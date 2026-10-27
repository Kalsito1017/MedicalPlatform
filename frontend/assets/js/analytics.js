// ---------------------------------------------------------------------------
// analytics.html page module — admin-only operational analytics (wave U).
//
// Endpoint (docs/API.md "Analytics"): GET /api/v1/analytics/summary — ONE
// bounded call returning real aggregates over the synthetic demo dataset
// (analytics:read; the backend answers 403 for everyone else, so this page's
// permission gate is UX only and the friendly denial mirrors admin.js).
//
// Charts are PURE CSS + inline SVG (no chart libraries, no canvas, no images):
// every bar and the donut arc carry geometry as element attributes and take
// their colours from styles.css classes. Nothing writes element styles — the
// app's only sanctioned style write remains sheet.js's --sheet-dy (DESIGN.md).
// Each chart is exposed as role="img" with an aria-label summary PLUS a
// visually-hidden data table, so screen readers get the same numbers.
//
// Data flow is cache.js stale-while-revalidate: first paint shows shimmer
// skeletons (ui.renderLoading), "Refresh" re-fetches through the same SWR key
// (a cache hit renders instantly while the background revalidation runs).
// Numbers are rendered with .tabular-nums; nothing is invented — every value
// on screen comes from the payload, and empty states say so honestly.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as cache from './cache.js';
import * as commandMenu from './command-menu.js';
import * as prefetch from './prefetch.js';
import * as ui from './ui.js';

const SUMMARY_PATH = '/analytics/summary';

const state = {
  me: null,
  summary: null,
};

const numberFormat = new Intl.NumberFormat('en-GB');

function count(value) {
  return Number.isFinite(value) ? value : 0;
}

function formatCount(value) {
  return numberFormat.format(count(value));
}

/* ------------------------------------------------------------- a11y table */

// Screen-reader twin of a chart: the chart itself is role="img" with a spoken
// summary; this table (visually hidden, still in the accessibility tree)
// carries the exact numbers.
function hiddenTable(caption, headers, rows) {
  return ui.el('table', { class: 'visually-hidden' },
    ui.el('caption', { text: caption }),
    ui.el('thead', {},
      ui.el('tr', {}, headers.map((header) => ui.el('th', { scope: 'col', text: header }))),
    ),
    ui.el('tbody', {}, rows.map((cells) =>
      ui.el('tr', {}, cells.map((cell) => ui.el('td', { text: String(cell) })))
    )),
  );
}

/* ------------------------------------------------------------- KPI tiles */

function kpiTile(label, value, note) {
  return ui.el('article', { class: 'kpi-tile card' },
    ui.el('p', { class: 'kpi-tile__label', text: label }),
    ui.el('p', { class: 'kpi-tile__value tabular-nums', text: formatCount(value) }),
    note ? ui.el('p', { class: 'kpi-tile__note', text: note }) : null,
  );
}

function renderKpis(container, data) {
  ui.clear(container);
  const counts = data.counts || {};
  const last30 = data.last_30_days || {};
  const grid = ui.el('div', { class: 'kpi-grid' },
    kpiTile('Patients', counts.patients, 'Synthetic demo records'),
    kpiTile('Encounters', counts.encounters, `+${formatCount(last30.encounters)} in the last 30 days`),
    kpiTile('Lab results', counts.labs, `+${formatCount(last30.lab_results)} in the last 30 days`),
    kpiTile('Prescriptions', counts.prescriptions, `+${formatCount(last30.prescriptions_written)} written in the last 30 days`),
    kpiTile('Active prescriptions', counts.active_prescriptions, 'Currently active'),
    kpiTile('Active diagnoses', counts.diagnoses_active, 'Open problem-list entries'),
    kpiTile('Active staff', counts.staff_active, 'Non-deactivated accounts'),
    kpiTile('AI conversations', counts.ai_conversations, `+${formatCount(last30.ai_asks)} asks in the last 30 days`),
  );
  container.append(grid);
}

/* ------------------------------------------- audit activity (grouped bars) */

const AUDIT_SERIES = [
  { key: 'logins', label: 'Logins', barClass: 'chart-bar--logins', swatch: 'logins' },
  { key: 'denials', label: 'Permission denials', barClass: 'chart-bar--denials', swatch: 'denials' },
  { key: 'record_reads', label: 'Record reads', barClass: 'chart-bar--reads', swatch: 'reads' },
];

function renderAuditActivity(container, days) {
  ui.clear(container);
  const series = AUDIT_SERIES;

  const totals = {};
  for (const entry of series) {
    totals[entry.key] = days.reduce((sum, day) => sum + count(day[entry.key]), 0);
  }
  const summary =
    `Audit activity for the last 14 days: ${formatCount(totals.logins)} logins, ` +
    `${formatCount(totals.denials)} permission denials, and ` +
    `${formatCount(totals.record_reads)} record reads in total. ` +
    'The daily values are in the data table below the chart.';

  // Pure SVG grouped bars: geometry via attributes only (see styles.css).
  const width = 620;
  const height = 230;
  const baseline = height - 34;
  const groupWidth = width / Math.max(1, days.length);
  const barWidth = Math.max(3, Math.floor((groupWidth - 8) / series.length));
  const maxValue = Math.max(
    1,
    ...days.map((day) => Math.max(...series.map((entry) => count(day[entry.key])))),
  );

  const marks = [ui.el('line', {
    class: 'chart-axis',
    x1: '0',
    y1: String(baseline),
    x2: String(width),
    y2: String(baseline),
    'stroke-width': '1',
  })];
  days.forEach((day, index) => {
    series.forEach((entry, seriesIndex) => {
      const value = count(day[entry.key]);
      const barHeight = value > 0 ? Math.max(2, Math.round((value / maxValue) * (baseline - 12))) : 0;
      marks.push(ui.el('rect', {
        class: `chart-bar ${entry.barClass}`,
        x: String(Math.round(index * groupWidth + 4 + seriesIndex * barWidth)),
        y: String(baseline - barHeight),
        width: String(barWidth),
        height: String(barHeight),
        rx: '2',
      }));
    });
    if (index % 2 === 0) {
      marks.push(ui.el('text', {
        class: 'chart-day',
        x: String(Math.round(index * groupWidth + groupWidth / 2)),
        y: String(baseline + 18),
        'text-anchor': 'middle',
        text: String(day.day || '').slice(5),
      }));
    }
  });

  container.append(
    ui.el('ul', { class: 'chart-legend' }, series.map((entry) =>
      ui.el('li', { class: 'chart-legend__item' },
        ui.el('span', { class: `chart-legend__swatch chart-legend__swatch--${entry.swatch}`, 'aria-hidden': 'true' }),
        ui.el('span', { text: entry.label }),
      )
    )),
    ui.el('svg', {
      class: 'chart',
      viewBox: `0 0 ${width} ${height}`,
      role: 'img',
      'aria-label': summary,
      focusable: 'false',
    }, marks),
    hiddenTable(
      'Daily audit activity for the last 14 days (UTC)',
      ['Day', ...series.map((entry) => entry.label)],
      days.map((day) => [day.day, ...series.map((entry) => count(day[entry.key]))]),
    ),
  );
}

/* --------------------------------------------------- labs (donut + lists) */

function renderLabs(container, data) {
  ui.clear(container);
  const rate = data.abnormal_lab_rate || { abnormal: 0, total: 0, pct: 0 };
  const mix = data.lab_status_mix || {};
  const top = Array.isArray(data.top_abnormal_tests) ? data.top_abnormal_tests : [];

  const abnormal = count(rate.abnormal);
  const total = count(rate.total);
  const pct = Number(rate.pct) || 0;

  // Donut: the abnormal arc is a stroke-dasharray ATTRIBUTE on a circle.
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const arc = total > 0 ? (abnormal / total) * circumference : 0;
  const donut = ui.el('svg', {
    class: 'donut',
    viewBox: '0 0 140 140',
    role: 'img',
    'aria-label': `Abnormal lab results: ${formatCount(abnormal)} of ${formatCount(total)} evaluable results (${pct}%).`,
    focusable: 'false',
  },
    ui.el('circle', {
      class: 'donut__track', cx: '70', cy: '70', r: String(radius), fill: 'none', 'stroke-width': '18',
    }),
    ui.el('circle', {
      class: 'donut__value',
      cx: '70',
      cy: '70',
      r: String(radius),
      fill: 'none',
      'stroke-width': '18',
      'stroke-dasharray': `${arc} ${circumference}`,
      transform: 'rotate(-90 70 70)',
    }),
    ui.el('text', { class: 'donut__label', x: '70', y: '68', 'text-anchor': 'middle', text: `${pct}%` }),
    ui.el('text', { class: 'donut__caption', x: '70', y: '90', 'text-anchor': 'middle', text: 'abnormal' }),
  );

  const statusRows = [
    { label: 'Final', value: count(mix.final), fill: '' },
    { label: 'Preliminary', value: count(mix.preliminary), fill: 'bar-track__fill--teal' },
    { label: 'Pending', value: count(mix.pending), fill: 'bar-track__fill--muted' },
  ];
  const statusMax = Math.max(1, ...statusRows.map((row) => row.value));

  container.append(
    ui.el('div', { class: 'donut-wrap' },
      donut,
      ui.el('div', { class: 'donut-side' },
        total === 0
          ? ui.el('p', {
              class: 'text-muted',
              text: 'No numeric lab results with reference ranges yet — nothing can be classified.',
            })
          : ui.el('p', {
              class: 'text-muted',
              text: `${formatCount(abnormal)} of ${formatCount(total)} evaluable results fall outside their recorded reference range (text-only results are never classified).`,
            }),
        ui.el('h3', { class: 'card__title', text: 'Result status mix' }),
        ui.el('ul', { class: 'bar-list' }, statusRows.map((row) => barRow(row.label, row.value, statusMax, row.fill))),
        hiddenTable(
          'Lab result document status mix',
          ['Status', 'Results'],
          statusRows.map((row) => [row.label, row.value]),
        ),
      ),
    ),
    hiddenTable(
      'Abnormal lab results',
      ['Measure', 'Value'],
      [
        ['Abnormal results', abnormal],
        ['Evaluable results', total],
        ['Abnormal share (percent, rounded)', pct],
      ],
    ),
  );

  const topWrap = ui.el('div', {});
  const heading = ui.el('h3', { class: 'card__title', text: 'Most frequent abnormal tests' });
  topWrap.append(heading);
  if (top.length === 0) {
    topWrap.append(ui.el('p', {
      class: 'text-muted',
      text: 'No abnormal lab results recorded yet.',
    }));
  } else {
    const topMax = Math.max(...top.map((entry) => count(entry.abnormal_count)));
    topWrap.append(
      ui.el('ul', { class: 'bar-list' }, top.map((entry) =>
        barRow(
          `${entry.test_name} (${entry.test_code})`,
          count(entry.abnormal_count),
          topMax,
          'bar-track__fill--teal',
        )
      )),
      hiddenTable(
        'Most frequent abnormal lab tests',
        ['Test', 'Code', 'Abnormal results'],
        top.map((entry) => [entry.test_name, entry.test_code, count(entry.abnormal_count)]),
      ),
    );
  }
  container.append(topWrap);
}

/* ------------------------------------------------ horizontal bar lists */

function barRow(label, value, maxValue, fillClass) {
  const width = maxValue > 0 ? Math.max(2, Math.round((value / maxValue) * 100)) : 0;
  return ui.el('li', { class: 'bar-row' },
    ui.el('p', { class: 'bar-row__label' }, label && label.nodeType ? label : String(label)),
    ui.el('p', { class: 'bar-row__value tabular-nums', text: formatCount(value) }),
    ui.el('svg', {
      class: 'bar-track',
      viewBox: '0 0 100 8',
      preserveAspectRatio: 'none',
      role: 'presentation',
      focusable: 'false',
    },
      ui.el('rect', { class: 'bar-track__bg', x: '0', y: '0', width: '100', height: '8', rx: '4' }),
      ui.el('rect', {
        class: `bar-track__fill ${fillClass || ''}`.trim(),
        x: '0',
        y: '0',
        width: String(width),
        height: '8',
        rx: '4',
      }),
    ),
  );
}

function renderPrescriptions(container, data) {
  ui.clear(container);
  const byStatus = data.prescriptions_by_status || {};
  const rows = [
    { label: 'Active', value: count(byStatus.active), fill: '' },
    { label: 'Completed', value: count(byStatus.completed), fill: 'bar-track__fill--teal' },
    { label: 'Stopped', value: count(byStatus.stopped), fill: 'bar-track__fill--muted' },
    { label: 'Cancelled', value: count(byStatus.cancelled), fill: 'bar-track__fill--muted' },
  ];
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  const max = Math.max(1, ...rows.map((row) => row.value));

  if (total === 0) {
    ui.renderEmptyState(container, {
      title: 'No prescriptions recorded yet',
      message: 'The synthetic dataset holds no prescriptions to summarise.',
      headingTag: 'h3',
    });
    return;
  }

  container.append(
    ui.el('p', {
      class: 'text-muted',
      text: `${formatCount(total)} synthetic prescriptions in total, by recorded status.`,
    }),
    ui.el('ul', { class: 'bar-list' }, rows.map((row) => barRow(row.label, row.value, max, row.fill))),
    hiddenTable(
      'Prescriptions by status',
      ['Status', 'Prescriptions'],
      rows.map((row) => [row.label, row.value]),
    ),
  );
}

function renderStaff(container, data) {
  ui.clear(container);
  const rows = Array.isArray(data.staff_by_role) ? data.staff_by_role : [];
  const total = rows.reduce((sum, row) => sum + count(row.count), 0);
  const max = Math.max(1, ...rows.map((row) => count(row.count)));

  if (total === 0) {
    ui.renderEmptyState(container, {
      title: 'No active staff roles yet',
      message: 'No active staff account holds a role in this demo database.',
      headingTag: 'h3',
    });
    return;
  }

  container.append(
    ui.el('p', {
      class: 'text-muted',
      text: 'Active accounts per role — an account holding two roles is counted under both.',
    }),
    ui.el('ul', { class: 'bar-list' }, rows.map((row) =>
      barRow(ui.roleBadge(row.role), count(row.count), max, '')
    )),
    hiddenTable(
      'Active staff by role',
      ['Role', 'Accounts'],
      rows.map((row) => [ui.roleLabel(row.role), count(row.count)]),
    ),
  );
}

/* --------------------------------------------------------------- AI usage */

function renderAiUsage(container, data) {
  ui.clear(container);
  const usage = data.ai_usage || {};
  const total = count(usage.total_asks);
  const tools = Array.isArray(usage.top_tools) ? usage.top_tools : [];

  if (total === 0) {
    ui.renderEmptyState(container, {
      title: 'No AI usage recorded yet',
      message: 'No questions have been asked of the AI assistant in this demo yet.',
      headingTag: 'h3',
    });
    return;
  }

  container.append(
    ui.el('div', { class: 'ai-split' },
      kpiTile('AI asks (all time)', total, 'Successful asks, audited'),
      kpiTile('General lookups', count(usage.general_asks), 'Across visible records'),
      kpiTile('Patient questions', count(usage.patient_asks), 'About one synthetic patient'),
    ),
    hiddenTable(
      'AI assistant asks',
      ['Measure', 'Count'],
      [
        ['Total asks', total],
        ['General lookups', count(usage.general_asks)],
        ['Patient questions', count(usage.patient_asks)],
      ],
    ),
  );

  const toolsWrap = ui.el('div', {});
  toolsWrap.append(ui.el('h3', { class: 'card__title', text: 'Most used tools' }));
  if (tools.length === 0) {
    toolsWrap.append(ui.el('p', {
      class: 'text-muted',
      text: 'No record-lookup tool calls yet — the assistant answered without reading records.',
    }));
  } else {
    toolsWrap.append(
      ui.el('ul', { class: 'chip-list' }, tools.map((tool) =>
        ui.el('li', { class: 'chip' },
          ui.el('span', { class: 'mono', text: String(tool.tool || 'unknown') }),
          ui.el('span', { class: 'chip__count tabular-nums', text: `\u00d7 ${formatCount(tool.calls)}` }),
        )
      )),
      hiddenTable(
        'Most used AI tools',
        ['Tool', 'Calls'],
        tools.map((tool) => [String(tool.tool || 'unknown'), count(tool.calls)]),
      ),
    );
  }
  container.append(toolsWrap);
}

/* ------------------------------------------------------------ page render */

function renderSkeletons() {
  const shapes = [
    ['kpi-region', 'Loading key numbers', 4],
    ['audit-activity-region', 'Loading audit activity', 3],
    ['labs-region', 'Loading lab analytics', 3],
    ['prescriptions-region', 'Loading prescription analytics', 3],
    ['staff-region', 'Loading staff analytics', 3],
    ['ai-region', 'Loading AI usage', 3],
  ];
  for (const [id, label, rows] of shapes) {
    const region = document.getElementById(id);
    if (region) ui.renderLoading(region, { label, rows });
  }
}

function renderAll(data) {
  state.summary = data;
  renderKpis(document.getElementById('kpi-region'), data);
  renderAuditActivity(document.getElementById('audit-activity-region'), data.audit_activity || []);
  renderLabs(document.getElementById('labs-region'), data);
  renderPrescriptions(document.getElementById('prescriptions-region'), data);
  renderStaff(document.getElementById('staff-region'), data);
  renderAiUsage(document.getElementById('ai-region'), data);

  const generated = document.getElementById('analytics-generated-at');
  if (generated) {
    generated.textContent = `Generated ${ui.formatDateTime(data.generated_at) || 'at an unknown time'} (UTC)`;
  }
  const status = document.getElementById('analytics-status');
  if (status) {
    status.textContent = `Analytics refreshed — generated ${ui.formatDateTime(data.generated_at) || 'at an unknown time'}.`;
  }
}

function loadSummary() {
  const key = cache.keyFor(SUMMARY_PATH);
  if (!cache.peek(key)) {
    // First paint (or stale entry): shimmer skeletons until the data lands.
    renderSkeletons();
  }
  cache.swr(key, () => api.get(SUMMARY_PATH), {
    onData: (data) => renderAll(data),
    onError: (error) => {
      ui.renderErrorState(document.getElementById('page-body'), error, {
        describe: api.describeApiError,
        onRetry: loadSummary,
      });
    },
    onRevalidating: (on) => ui.setSyncIndicator(document.getElementById('analytics-status'), on),
  });
}

function renderPermissionDenied() {
  const body = ui.clear(document.getElementById('page-body'));
  body.append(
    ui.el('div', { class: 'state-panel state-panel--error', role: 'alert' },
      ui.el('h2', { class: 'state-panel__title', text: 'You do not have permission' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: 'Analytics is limited to accounts with the analytics:read permission (Admin). If you open the analytics API directly it returns 403 for this account, because access is enforced server-side.',
      }),
      ui.el('div', { class: 'state-panel__actions' },
        ui.el('a', { class: 'btn btn-primary', href: 'dashboard.html' }, 'Back to dashboard'),
      ),
    ),
  );
}

ui.onReady(async () => {
  ui.setRetryHandler(() => window.location.reload());
  // Pre-rendering (prefetch.js): warm the likely next page module on idle.
  prefetch.warmModules(['assets/js/admin.js']);
  try {
    const user = await auth.initAuthChrome({ active: 'analytics' });
    if (!user) return;
    commandMenu.initCommandMenu({ user });

    if (!auth.hasPermission(user, 'analytics:read')) {
      renderPermissionDenied();
      return;
    }
    state.me = user;

    const loading = document.getElementById('analytics-loading');
    if (loading) loading.hidden = true;

    const refresh = document.getElementById('analytics-refresh');
    if (refresh) refresh.addEventListener('click', () => loadSummary());

    loadSummary();
  } catch (error) {
    ui.renderErrorState(document.getElementById('page-body'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
  }
});
