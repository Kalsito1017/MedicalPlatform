// ---------------------------------------------------------------------------
// analytics.html page module — admin-only clinical operations analytics.
//
// Endpoint (docs/API.md "Analytics"): GET /api/v1/analytics/summary with
// optional inclusive UTC `start`/`end` dates. One request feeds EVERY section;
// the shared filter never fans out per chart. The backend enforces
// analytics:read (Admin only) — this page's permission gate is UX only.
//
// Charts reuse graphify.js (pure CSS/inline SVG, no libraries, no canvas) and
// every chart carries role="img" plus an aria-label summary and a
// visually-hidden data table. Geometry is element attributes, never inline
// styles (CSP style-src 'self'). Appointment days/hours use the clinic
// timezone reported by the API; clinical/audit series use UTC days.
//
// Data flow is cache.js stale-while-revalidate keyed by path + date range. The
// page never invents a number: unpersisted latency/voice/API-error metrics are
// rendered from the API's explicit `available: false` objects with their
// reasons.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as cache from './cache.js';
import * as commandMenu from './command-menu.js?v=20261109j';
import * as graphify from './graphify.js';
import * as prefetch from './prefetch.js';
import * as ui from './ui.js';

const SUMMARY_PATH = '/analytics/summary';
const PRESETS = {
  7: { days: 7, label: 'Last 7 days' },
  30: { days: 30, label: 'Last 30 days' },
  90: { days: 90, label: 'Last 90 days' },
  year: { label: 'This year' },
};

const SECTIONS = [
  ['kpi-region', 'Loading key numbers'],
  ['patients-region', 'Loading patient activity'],
  ['appointments-region', 'Loading appointment analytics'],
  ['labs-region', 'Loading laboratory analytics'],
  ['prescriptions-region', 'Loading prescription analytics'],
  ['security-region', 'Loading access and security activity'],
  ['ai-region', 'Loading AI usage'],
  ['quality-region', 'Loading data quality checks'],
  ['health-region', 'Loading service configuration'],
  ['staff-region', 'Loading staff analytics'],
];

const state = {
  me: null,
  summary: null,
  range: null,
  loadToken: 0,
};

const numberFormat = new Intl.NumberFormat('en-GB');

function count(value) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

function formatCount(value) {
  return numberFormat.format(count(value));
}

function formatPercent(value) {
  if (value == null || !Number.isFinite(Number(value))) return 'Not available';
  return `${Number(value).toFixed(1)}%`;
}

function signedPercent(value) {
  if (value == null || !Number.isFinite(Number(value))) return null;
  const number = Number(value);
  const sign = number > 0 ? '+' : '';
  return `${sign}${number.toFixed(1)}%`;
}

/* ----------------------------------------------------------- date helpers */

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function todayIso() {
  return isoDate(new Date());
}

function parseIsoDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : value;
}

function shiftIso(value, days) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return isoDate(date);
}

function inclusiveDays(start, end) {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000) + 1;
}

function rangeForPreset(preset) {
  const end = todayIso();
  if (preset === 'year') {
    return { start: `${end.slice(0, 4)}-01-01`, end };
  }
  const days = PRESETS[preset]?.days || 30;
  return { start: shiftIso(end, -(days - 1)), end };
}

function periodLabel(range) {
  const start = ui.formatDate(range.start) || range.start;
  const end = ui.formatDate(range.end) || range.end;
  return `${start} – ${end} · ${formatCount(range.days)} days · UTC daily buckets`;
}

function comparisonNote(metric, data) {
  const entry = data && data.comparisons ? data.comparisons[metric] : null;
  if (!entry) return '';
  const days = formatCount(data.range?.days || 0);
  if (entry.previous === 0) {
    return entry.current === 0
      ? `No activity in this or the preceding ${days} days`
      : `New activity — no prior-period baseline (${days} days)`;
  }
  const change = signedPercent(entry.change_pct);
  if (!change) return `Compared with the preceding ${days} days`;
  if (Number(entry.change_pct) === 0) {
    return `No change vs the preceding ${days} days`;
  }
  return `${change} vs the preceding ${days} days`;
}

/* ------------------------------------------------------------- a11y table */

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

function visibleTable(caption, headers, rows) {
  return ui.el('div', { class: 'table-scroll', tabindex: '0', role: 'region', 'aria-label': caption },
    ui.el('table', { class: 'table' },
      ui.el('caption', { class: 'visually-hidden', text: caption }),
      ui.el('thead', {},
        ui.el('tr', {}, headers.map((header) => ui.el('th', { scope: 'col', text: header }))),
      ),
      ui.el('tbody', {}, rows.map((cells) =>
        ui.el('tr', {}, cells.map((cell) => (
          ui.el('td', {}, cell && cell.nodeType ? cell : String(cell))
        )))
      )),
    ),
  );
}

/* ---------------------------------------------------------------- layout */

function panel(title, note) {
  const node = ui.el('div', { class: 'analytics-panel card' },
    ui.el('h3', { class: 'card__title', text: title }),
    note ? ui.el('p', { class: 'text-muted analytics-panel__note', text: note }) : null,
  );
  return node;
}

function chartHost(panelNode) {
  const host = ui.el('div', { class: 'analytics-chart-host' });
  panelNode.append(host);
  return host;
}

function kpiTile(label, value, note) {
  return ui.el('article', { class: 'kpi-tile card' },
    ui.el('p', { class: 'kpi-tile__label', text: label }),
    ui.el('p', { class: 'kpi-tile__value tabular-nums', text: String(value) }),
    note ? ui.el('p', { class: 'kpi-tile__note', text: note }) : null,
  );
}

function kpiGrid(...tiles) {
  return ui.el('div', { class: 'kpi-grid' }, ...tiles.filter(Boolean));
}

function chartGrid(...panels) {
  return ui.el('div', { class: 'analytics-chart-grid' }, ...panels.filter(Boolean));
}

/* --------------------------------------------------------------- key KPIs */

function renderKpis(container, data) {
  ui.clear(container);
  const counts = data.counts || {};
  const activity = data.activity || {};
  container.append(kpiGrid(
    kpiTile('Registered patients', formatCount(counts.patients), 'Lifetime total'),
    kpiTile('New patients', formatCount(activity.new_patients), comparisonNote('new_patients', data)),
    kpiTile('Encounters', formatCount(activity.encounters), comparisonNote('encounters', data)),
    kpiTile('Laboratory tests', formatCount(activity.lab_results), comparisonNote('lab_results', data)),
    kpiTile('Total prescriptions', formatCount(counts.prescriptions), 'Lifetime total'),
    kpiTile('Prescriptions written', formatCount(activity.prescriptions_written), comparisonNote('prescriptions_written', data)),
    kpiTile('Active prescriptions', formatCount(counts.active_prescriptions), 'Currently active status'),
    kpiTile('Appointments', formatCount(activity.appointments), comparisonNote('appointments', data)),
    kpiTile('AI requests', formatCount(activity.ai_requests), comparisonNote('ai_requests', data)),
  ));
}

/* ------------------------------------------------------ patients section */

function renderPatients(container, data) {
  ui.clear(container);
  const block = data.patients || {};
  const trend = Array.isArray(block.trend) ? block.trend : [];
  const types = Array.isArray(block.by_encounter_type) ? block.by_encounter_type : [];
  const total = count(block.encounters);

  container.append(kpiGrid(
    kpiTile('New patients', formatCount(block.new_patients), 'Created in the selected period'),
    kpiTile('Returning patients', formatCount(block.returning_patients), 'Encounter in period, account older'),
    kpiTile('Active patients', formatCount(block.active_patients), 'Distinct patients with an encounter'),
    kpiTile('Encounters', formatCount(total), 'In the selected period'),
    kpiTile('Average per day', Number(block.avg_encounters_per_day || 0).toFixed(1), 'Selected period ÷ UTC days'),
  ));

  const trendPanel = panel('Encounter volume', 'Daily encounters in UTC days.');
  if (total === 0) {
    const host = chartHost(trendPanel);
    ui.renderEmptyState(host, {
      title: 'No encounters in this period',
      message: 'Choose a wider date range or a period with recorded visits.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderLineChart(chartHost(trendPanel), {
      series: [{
        key: 'encounters',
        label: 'Encounters',
        points: trend.map((entry) => ({ x: entry.day, y: count(entry.encounters) })),
      }],
      ariaLabel: `Encounter volume for ${formatCount(total)} encounters in the selected period.`,
      tableCaption: 'Daily encounters (UTC)',
      valueFormatter: formatCount,
      dateFormatter: (value) => ui.formatDate(value) || String(value),
    });
  }

  const typesPanel = panel('Encounters by type', 'Top recorded encounter types in the selected period.');
  if (types.length === 0) {
    ui.renderEmptyState(chartHost(typesPanel), {
      title: 'No encounter types recorded',
      message: 'There are no encounter types to compare for this period.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderHorizontalBars(chartHost(typesPanel), {
      rows: types.map((entry) => ({
        label: String(entry.encounter_type || 'Unspecified'),
        value: count(entry.count),
      })),
      ariaLabel: 'Encounters by recorded type.',
      tableCaption: 'Encounters by type',
      valueFormatter: formatCount,
    });
  }

  container.append(
    chartGrid(trendPanel, typesPanel),
    ui.el('p', { class: 'card__note', text: 'Encounter daily buckets use UTC. No patient identifiers appear in these aggregates.' }),
  );
}

/* -------------------------------------------------- appointment section */

function svgEl(tag, props = {}, ...children) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null) continue;
    if (key === 'text') node.textContent = value;
    else if (key === 'class') node.setAttribute('class', value);
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child);
  }
  return node;
}

const APPOINTMENT_SEGMENTS = [
  { key: 'scheduled', label: 'Scheduled' },
  { key: 'confirmed', label: 'Confirmed' },
  { key: 'completed', label: 'Completed' },
  { key: 'cancelled', label: 'Cancelled' },
  { key: 'no_show', label: 'No-show' },
];

function renderStatusDonut(container, byStatus, total) {
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const svg = svgEl('svg', {
    class: 'donut graphify-chart analytics-status-donut',
    viewBox: '0 0 140 140',
    role: 'img',
    'aria-label': `${formatCount(total)} appointments by status: ${APPOINTMENT_SEGMENTS.map((segment) => `${segment.label} ${formatCount(byStatus[segment.key])}`).join(', ')}.`,
    focusable: 'false',
  },
    svgEl('circle', {
      class: 'donut__track', cx: '70', cy: '70', r: String(radius), fill: 'none', 'stroke-width': '18',
    }),
  );

  let offset = 0;
  for (const segment of APPOINTMENT_SEGMENTS) {
    const value = count(byStatus[segment.key]);
    const length = total > 0 ? (value / total) * circumference : 0;
    if (length > 0) {
      svg.append(svgEl('circle', {
        class: `donut__value donut__segment donut__segment--${segment.key}`,
        cx: '70',
        cy: '70',
        r: String(radius),
        fill: 'none',
        'stroke-width': '18',
        'stroke-dasharray': `${length} ${circumference - length}`,
        'stroke-dashoffset': String(-offset),
        transform: 'rotate(-90 70 70)',
      }));
    }
    offset += length;
  }
  svg.append(
    svgEl('text', { class: 'donut__label', x: '70', y: '68', 'text-anchor': 'middle', text: formatCount(total) }),
    svgEl('text', { class: 'donut__caption', x: '70', y: '90', 'text-anchor': 'middle', text: 'appointments' }),
  );

  const legend = ui.el('ul', { class: 'analytics-status-legend' }, APPOINTMENT_SEGMENTS.map((segment) =>
    ui.el('li', { class: 'analytics-status-legend__item' },
      ui.el('span', {
        class: `analytics-status-legend__swatch analytics-status-legend__swatch--${segment.key}`,
        'aria-hidden': 'true',
      }),
      ui.el('span', { text: segment.label }),
      ui.el('span', {
        class: 'analytics-status-legend__count tabular-nums',
        text: formatCount(byStatus[segment.key]),
      }),
    )
  ));

  container.append(
    ui.el('div', { class: 'donut-wrap' }, svg, legend),
    hiddenTable(
      'Appointments by status',
      ['Status', 'Appointments'],
      APPOINTMENT_SEGMENTS.map((segment) => [segment.label, count(byStatus[segment.key])]),
    ),
  );
}

function renderAppointments(container, data) {
  ui.clear(container);
  const block = data.appointments || {};
  const status = block.by_status || {};
  const trend = Array.isArray(block.trend) ? block.trend : [];
  const visitTypes = Array.isArray(block.by_visit_type) ? block.by_visit_type : [];
  const byWeekday = Array.isArray(block.by_weekday) ? block.by_weekday : [];
  const byHour = Array.isArray(block.by_hour) ? block.by_hour : [];
  const total = count(block.total);
  const utilization = block.utilization || {};
  const available = count(utilization.available_minutes);
  const booked = count(utilization.booked_minutes);
  const utilizationValue = available > 0 ? formatPercent(utilization.utilization_pct) : 'Not available';

  container.append(kpiGrid(
    kpiTile('Appointments', formatCount(total), 'Starting in the selected period'),
    kpiTile('Completed', formatCount(status.completed), 'Recorded status'),
    kpiTile('Cancelled', formatCount(status.cancelled), 'Recorded status'),
    kpiTile('No-show', formatCount(status.no_show), 'Recorded status'),
    kpiTile(
      'Calendar utilization',
      utilizationValue,
      available > 0
        ? `${formatCount(booked)} booked / ${formatCount(available)} available minutes`
        : 'No recorded working hours in this period',
    ),
  ));

  const trendPanel = panel('Appointment volume', 'Appointment days use the clinic timezone.');
  if (total === 0) {
    ui.renderEmptyState(chartHost(trendPanel), {
      title: 'No appointments in this period',
      message: 'Choose a wider date range or a period with scheduled appointments.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderLineChart(chartHost(trendPanel), {
      series: [
        {
          key: 'appointments',
          label: 'All appointments',
          points: trend.map((entry) => ({ x: entry.day, y: count(entry.total) })),
        },
        {
          key: 'completed',
          label: 'Completed',
          className: 'line-series--teal',
          points: trend.map((entry) => ({ x: entry.day, y: count(entry.completed) })),
        },
        {
          key: 'cancelled',
          label: 'Cancelled',
          className: 'line-series--muted',
          points: trend.map((entry) => ({ x: entry.day, y: count(entry.cancelled) })),
        },
        {
          key: 'no_show',
          label: 'No-show',
          className: 'line-series--danger',
          points: trend.map((entry) => ({ x: entry.day, y: count(entry.no_show) })),
        },
      ],
      ariaLabel: `${formatCount(total)} appointments in the selected period, with completed, cancelled and no-show trends.`,
      tableCaption: 'Appointment volume by clinic-local day',
      valueFormatter: formatCount,
      dateFormatter: (value) => ui.formatDate(value) || String(value),
    });
  }

  const statusPanel = panel('Appointments by status', 'A small closed status vocabulary; no clinical interpretation.');
  if (total === 0) {
    ui.renderEmptyState(chartHost(statusPanel), {
      title: 'No appointment statuses to show',
      message: 'No appointments start in this period.',
      headingTag: 'h4',
    });
  } else {
    renderStatusDonut(chartHost(statusPanel), status, total);
  }

  const typesPanel = panel('Appointments by visit type', 'Top recorded visit types in the selected period.');
  if (visitTypes.length === 0) {
    ui.renderEmptyState(chartHost(typesPanel), {
      title: 'No visit types recorded',
      message: 'Appointments in this period have no recorded visit type.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderHorizontalBars(chartHost(typesPanel), {
      rows: visitTypes.map((entry) => ({
        label: String(entry.visit_type || 'Unspecified'),
        value: count(entry.count),
      })),
      ariaLabel: 'Appointments by visit type.',
      tableCaption: 'Appointments by visit type',
      valueFormatter: formatCount,
    });
  }

  const weekdayPanel = panel('Appointments by weekday', 'Clinic-local weekdays, Monday to Sunday.');
  if (total === 0) {
    ui.renderEmptyState(chartHost(weekdayPanel), {
      title: 'No weekday distribution yet',
      message: 'No appointments start in this period.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderHorizontalBars(chartHost(weekdayPanel), {
      rows: byWeekday.map((entry) => ({
        label: String(entry.label || `Day ${entry.weekday}`),
        value: count(entry.count),
      })),
      ariaLabel: 'Appointments by clinic-local weekday.',
      tableCaption: 'Appointments by weekday',
      valueFormatter: formatCount,
    });
  }

  const hourPanel = panel('Busiest appointment hours', 'Top clinic-local hours by appointment count; the full 24-hour distribution is in the accessible table.');
  if (total === 0) {
    ui.renderEmptyState(chartHost(hourPanel), {
      title: 'No hourly distribution yet',
      message: 'No appointments start in this period.',
      headingTag: 'h4',
    });
  } else {
    const topHours = [...byHour]
      .sort((a, b) => count(b.count) - count(a.count) || count(a.hour) - count(b.hour))
      .slice(0, 8);
    const host = chartHost(hourPanel);
    graphify.renderHorizontalBars(host, {
      rows: topHours.map((entry) => ({
        label: `${String(entry.hour).padStart(2, '0')}:00`,
        value: count(entry.count),
      })),
      ariaLabel: 'Busiest clinic-local appointment hours.',
      tableCaption: 'Top appointment hours',
      valueFormatter: formatCount,
    });
    host.append(hiddenTable(
      'Appointment distribution by clinic-local hour',
      ['Hour', 'Appointments'],
      byHour.map((entry) => [`${String(entry.hour).padStart(2, '0')}:00`, count(entry.count)]),
    ));
  }

  container.append(
    chartGrid(trendPanel, statusPanel, typesPanel, weekdayPanel, hourPanel),
    ui.el('p', {
      class: 'card__note',
    },
      `Appointment days, weekdays and hours use ${block.timezone || data.range?.appointment_timezone || 'the clinic timezone'}. Booked minutes exclude cancelled appointments and cover doctors with recorded working hours. Available minutes come only from those recorded working hours minus time off; no default schedule is assumed. Volume is not a measure of medical performance. `,
      ui.el('a', { href: 'appointments.html' }, 'Open the appointment calendar'),
    ),
  );
}

/* ------------------------------------------------------------ labs block */

function renderLabs(container, data) {
  ui.clear(container);
  const block = data.labs || {};
  const classification = block.classification || {};
  const rate = block.abnormal_rate || {};
  const statusMix = block.status_mix || {};
  const top = Array.isArray(block.top_abnormal_tests) ? block.top_abnormal_tests : [];
  const trend = Array.isArray(block.trend) ? block.trend : [];
  const total = count(block.total);
  const evaluable = count(rate.total);
  const abnormal = count(rate.abnormal);

  container.append(kpiGrid(
    kpiTile('Laboratory tests', formatCount(total), 'Collected in the selected period'),
    kpiTile('Abnormal', formatCount(abnormal), `${formatCount(evaluable)} evaluable results`),
    kpiTile('Normal', formatCount(classification.normal), 'Evaluable and within range'),
    kpiTile('High', formatCount(classification.high), 'Above the recorded high bound'),
    kpiTile('Low', formatCount(classification.low), 'Below the recorded low bound'),
    kpiTile('Unclassified', formatCount(classification.unclassified), 'Text-only or missing/invalid range'),
    kpiTile('Range coverage', formatPercent(classification.reference_coverage_pct), 'Evaluable ÷ all tests'),
  ));

  const ratePanel = panel('Abnormal results', 'Only numeric results with a recorded reference bound are evaluable.');
  if (evaluable === 0) {
    ui.renderEmptyState(chartHost(ratePanel), {
      title: 'No evaluable laboratory results',
      message: 'This period has no numeric results with a usable reference range, so an abnormal percentage is not defined.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderDonutChart(chartHost(ratePanel), {
      label: 'Abnormal results',
      value: abnormal,
      total: evaluable,
      percent: Number(rate.pct || 0),
      centerLabel: formatPercent(rate.pct),
      centerCaption: 'abnormal',
      ariaLabel: `Abnormal laboratory results: ${formatCount(abnormal)} of ${formatCount(evaluable)} evaluable results (${formatPercent(rate.pct)}).`,
      tableCaption: 'Abnormal laboratory results',
      rows: [
        ['Abnormal results', abnormal],
        ['Evaluable results (denominator)', evaluable],
        ['Abnormal share (rounded)', formatPercent(rate.pct)],
        ['Unclassified results', count(classification.unclassified)],
        ['Total laboratory tests', total],
      ],
    });
  }

  const classificationPanel = panel('Classification mix', 'Normal + high + low + unclassified equals all tests in the period.');
  if (total === 0) {
    ui.renderEmptyState(chartHost(classificationPanel), {
      title: 'No laboratory results in this period',
      message: 'Choose a wider date range or a period with recorded laboratory tests.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderHorizontalBars(chartHost(classificationPanel), {
      rows: [
        { label: 'Normal', value: count(classification.normal), className: 'bar-track__fill--teal' },
        { label: 'High', value: count(classification.high), className: 'bar-track__fill--warn' },
        { label: 'Low', value: count(classification.low), className: 'bar-track__fill--danger' },
        { label: 'Unclassified', value: count(classification.unclassified), className: 'bar-track__fill--muted' },
      ],
      ariaLabel: 'Laboratory results by classification.',
      tableCaption: 'Laboratory results by classification',
      valueFormatter: formatCount,
    });
  }

  const topPanel = panel('Most frequent abnormal tests', 'Grouped by recorded test code and name.');
  if (top.length === 0) {
    ui.renderEmptyState(chartHost(topPanel), {
      title: 'No abnormal laboratory results',
      message: 'No evaluated result falls outside its recorded reference range in this period.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderHorizontalBars(chartHost(topPanel), {
      rows: top.map((entry) => ({
        label: `${entry.test_name} (${entry.test_code})`,
        value: count(entry.abnormal_count),
        className: 'bar-track__fill--teal',
      })),
      ariaLabel: 'Most frequent abnormal laboratory tests.',
      tableCaption: 'Most frequent abnormal laboratory tests',
      valueFormatter: formatCount,
    });
  }

  const trendPanel = panel('Laboratory volume and abnormalities', 'Daily results in UTC days; abnormal is a subset of evaluable.');
  if (total === 0) {
    ui.renderEmptyState(chartHost(trendPanel), {
      title: 'No laboratory trend to show',
      message: 'No laboratory results were collected in this period.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderLineChart(chartHost(trendPanel), {
      series: [
        {
          key: 'total',
          label: 'All results',
          points: trend.map((entry) => ({ x: entry.day, y: count(entry.total) })),
        },
        {
          key: 'evaluable',
          label: 'Evaluable',
          className: 'line-series--teal',
          points: trend.map((entry) => ({ x: entry.day, y: count(entry.evaluable) })),
        },
        {
          key: 'abnormal',
          label: 'Abnormal',
          className: 'line-series--danger',
          points: trend.map((entry) => ({ x: entry.day, y: count(entry.abnormal) })),
        },
      ],
      ariaLabel: 'Daily laboratory volume with evaluable and abnormal counts.',
      tableCaption: 'Daily laboratory results (UTC)',
      valueFormatter: formatCount,
      dateFormatter: (value) => ui.formatDate(value) || String(value),
    });
  }

  const statusPanel = panel('Document status mix', 'The recorded laboratory document state, not an abnormality flag.');
  if (total === 0) {
    ui.renderEmptyState(chartHost(statusPanel), {
      title: 'No document statuses in this period',
      message: 'No laboratory results were collected in this period.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderHorizontalBars(chartHost(statusPanel), {
      rows: [
        { label: 'Final', value: count(statusMix.final) },
        { label: 'Preliminary', value: count(statusMix.preliminary), className: 'bar-track__fill--teal' },
        { label: 'Pending', value: count(statusMix.pending), className: 'bar-track__fill--muted' },
      ],
      ariaLabel: 'Laboratory document status mix.',
      tableCaption: 'Laboratory document status mix',
      valueFormatter: formatCount,
    });
  }

  container.append(
    chartGrid(ratePanel, classificationPanel, topPanel, trendPanel, statusPanel),
    ui.el('p', {
      class: 'card__note',
      text: `Abnormal share denominator: ${formatCount(abnormal)} of ${formatCount(evaluable)} evaluable results (${formatPercent(rate.pct)}). Unclassified (${formatCount(classification.unclassified)} of ${formatCount(total)}) includes text-only results and results without a usable reference range; missing ranges are never counted as normal. These are statistical classifications, not diagnoses.`,
    }),
  );
}

/* ----------------------------------------------------- prescriptions block */

function renderPrescriptions(container, data) {
  ui.clear(container);
  const block = data.prescriptions || {};
  const period = block.by_status || {};
  const lifetime = block.lifetime_by_status || {};
  const trend = Array.isArray(block.trend) ? block.trend : [];
  const total = count(block.total);
  const lifetimeTotal = Object.values(lifetime).reduce((sum, value) => sum + count(value), 0);

  container.append(kpiGrid(
    kpiTile('Prescriptions written', formatCount(total), 'Recorded course start in the period'),
    kpiTile('Active', formatCount(period.active), 'Selected period'),
    kpiTile('Completed', formatCount(period.completed), 'Selected period'),
    kpiTile('Stopped / discontinued', formatCount(period.stopped), 'Recorded lifecycle value'),
    kpiTile('Cancelled', formatCount(period.cancelled), 'Selected period'),
  ));

  const trendPanel = panel('Prescriptions written over time', 'Course start dates in UTC days.');
  if (total === 0) {
    ui.renderEmptyState(chartHost(trendPanel), {
      title: 'No prescriptions written in this period',
      message: 'Choose a wider date range or a period with recorded prescriptions.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderLineChart(chartHost(trendPanel), {
      series: [{
        key: 'prescriptions',
        label: 'Prescriptions written',
        points: trend.map((entry) => ({ x: entry.day, y: count(entry.prescriptions) })),
      }],
      ariaLabel: `${formatCount(total)} prescriptions written in the selected period.`,
      tableCaption: 'Prescriptions written by UTC day',
      valueFormatter: formatCount,
      dateFormatter: (value) => ui.formatDate(value) || String(value),
    });
  }

  const statusPanel = panel('Lifetime prescriptions by status', 'Current recorded status across all non-deleted prescriptions.');
  if (lifetimeTotal === 0) {
    ui.renderEmptyState(chartHost(statusPanel), {
      title: 'No prescriptions recorded yet',
      message: 'There are no lifetime prescriptions to summarise.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderHorizontalBars(chartHost(statusPanel), {
      rows: [
        { label: 'Active', value: count(lifetime.active) },
        { label: 'Completed', value: count(lifetime.completed), className: 'bar-track__fill--teal' },
        { label: 'Stopped', value: count(lifetime.stopped), className: 'bar-track__fill--muted' },
        { label: 'Cancelled', value: count(lifetime.cancelled), className: 'bar-track__fill--muted' },
      ],
      ariaLabel: 'Lifetime prescriptions by recorded status.',
      tableCaption: 'Lifetime prescriptions by status',
      valueFormatter: formatCount,
    });
  }

  container.append(
    chartGrid(trendPanel, statusPanel),
    ui.el('p', {
      class: 'card__note',
      text: `Stopped is the recorded lifecycle value for discontinued. Lifetime total: ${formatCount(lifetimeTotal)} prescriptions. These counts describe recorded status only and do not judge prescribing quality.`,
    }),
  );
}

/* ---------------------------------------------------- access & security */

function securitySeries(days, entries) {
  return entries.map((entry) => ({
    key: entry.key,
    label: entry.label,
    className: entry.className,
    swatchClass: entry.swatchClass,
    values: days.map((day) => count(day[entry.key])),
  }));
}

function renderSecurity(container, data) {
  ui.clear(container);
  const block = data.security || {};
  const totals = block.totals || {};
  const trend = Array.isArray(block.trend) ? block.trend : [];
  const days = trend.map((entry) => String(entry.day || ''));
  const signins = count(totals.successful_signins) + count(totals.failed_signins);
  const reads = count(totals.patient_reads) + count(totals.lab_reads) + count(totals.prescription_reads);

  container.append(kpiGrid(
    kpiTile('Successful sign-ins', formatCount(totals.successful_signins), 'Selected period'),
    kpiTile('Failed sign-ins', formatCount(totals.failed_signins), 'Selected period'),
    kpiTile('Permission denials', formatCount(totals.permission_denials), 'Selected period'),
    kpiTile('Patient record reads', formatCount(totals.patient_reads), 'Selected period'),
    kpiTile('Laboratory reads', formatCount(totals.lab_reads), 'Selected period'),
    kpiTile('Prescription reads', formatCount(totals.prescription_reads), 'Selected period'),
    kpiTile('AI requests', formatCount(totals.ai_requests), 'Selected period'),
  ));

  const signinPanel = panel('Sign-ins over time', 'Successful versus failed sign-ins in UTC days.');
  if (signins === 0) {
    ui.renderEmptyState(chartHost(signinPanel), {
      title: 'No sign-in events in this period',
      message: 'There are no successful or failed sign-ins to chart.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderGroupedBarChart(chartHost(signinPanel), {
      categories: days,
      series: securitySeries(trend, [
        { key: 'successful_signins', label: 'Successful', className: 'chart-bar--success', swatchClass: 'chart-legend__swatch--success' },
        { key: 'failed_signins', label: 'Failed', className: 'chart-bar--failure', swatchClass: 'chart-legend__swatch--failure' },
      ]),
      ariaLabel: `${formatCount(signins)} sign-in events in the selected period, split into successful and failed.`,
      tableCaption: 'Daily sign-ins (UTC)',
      valueFormatter: formatCount,
    });
  }

  const readPanel = panel('Clinical-record reads over time', 'Patient, laboratory and prescription read actions in UTC days.');
  if (reads === 0) {
    ui.renderEmptyState(chartHost(readPanel), {
      title: 'No clinical-record reads in this period',
      message: 'There are no patient, laboratory or prescription read events to chart.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderGroupedBarChart(chartHost(readPanel), {
      categories: days,
      series: securitySeries(trend, [
        { key: 'patient_reads', label: 'Patient reads', className: 'chart-bar--primary', swatchClass: 'chart-legend__swatch--primary' },
        { key: 'lab_reads', label: 'Laboratory reads', className: 'chart-bar--completed', swatchClass: 'chart-legend__swatch--success' },
        { key: 'prescription_reads', label: 'Prescription reads', className: 'chart-bar--reads', swatchClass: 'chart-legend__swatch--reads' },
      ]),
      ariaLabel: `${formatCount(reads)} clinical-record reads in the selected period.`,
      tableCaption: 'Daily clinical-record reads (UTC)',
      valueFormatter: formatCount,
    });
  }

  const denialPanel = panel('Permission denials over time', 'Authorization denials recorded in the audit stream (UTC days).');
  if (count(totals.permission_denials) === 0) {
    ui.renderEmptyState(chartHost(denialPanel), {
      title: 'No permission denials in this period',
      message: 'No authorization denial events were recorded.',
      headingTag: 'h4',
    });
  } else {
    graphify.renderLineChart(chartHost(denialPanel), {
      series: [{
        key: 'permission_denials',
        label: 'Permission denials',
        className: 'line-series--danger',
        points: trend.map((entry) => ({ x: entry.day, y: count(entry.permission_denials) })),
      }],
      ariaLabel: `${formatCount(totals.permission_denials)} permission denials in the selected period.`,
      tableCaption: 'Daily permission denials (UTC)',
      valueFormatter: formatCount,
      dateFormatter: (value) => ui.formatDate(value) || String(value),
    });
  }

  container.append(
    chartGrid(signinPanel, readPanel, denialPanel),
    ui.el('p', {
      class: 'card__note',
      text: 'UTC days. These are aggregate audit counts only; passwords, tokens, session identifiers, raw audit metadata, clinical payloads, patient identifiers, AI questions/answers and transcripts are never shown here. Detailed inspection stays in the authorized audit interface.',
    }),
  );
}

/* ------------------------------------------------------------- AI block */

function renderAi(container, data) {
  ui.clear(container);
  const usage = data.ai_usage || {};
  const tools = Array.isArray(usage.top_tools) ? usage.top_tools : [];
  const total = count(usage.total_requests);
  const successRate = usage.success_rate == null ? null : Number(usage.success_rate);

  container.append(kpiGrid(
    kpiTile('AI requests', formatCount(total), 'Success + failed + denied'),
    kpiTile('Successful', formatCount(usage.successful), 'Selected period'),
    kpiTile('Failed', formatCount(usage.failed), 'Selected period'),
    kpiTile(
      'Success rate',
      successRate == null ? 'Not available' : formatPercent(successRate),
      successRate == null ? 'No completed requests to rate' : 'Successful ÷ (successful + failed)',
    ),
    kpiTile('Denied', formatCount(usage.denied), 'Excluded from the success rate'),
  ));

  const toolsPanel = panel('MCP tool calls', 'Real read-only tool calls audited as mcp.<tool>; no arguments or clinical payloads are shown.');
  if (tools.length === 0) {
    ui.renderEmptyState(chartHost(toolsPanel), {
      title: 'No MCP tool calls in this period',
      message: 'The assistant did not call a record-lookup tool, or no AI requests were made.',
      headingTag: 'h4',
    });
  } else {
    toolsPanel.append(visibleTable(
      'MCP tool calls and failures',
      ['Tool', 'Calls', 'Failures'],
      tools.map((tool) => [String(tool.tool || 'unknown'), formatCount(tool.calls), formatCount(tool.failures)]),
    ));
  }

  const latencyPanel = panel('AI response latency', null);
  latencyPanel.append(
    ui.el('p', { class: 'analytics-unavailable__value', text: 'Not available' }),
    ui.el('p', { class: 'text-muted', text: usage.latency?.reason || 'Per-request AI latency is not persisted.' }),
  );

  const voicePanel = panel('Voice transcription', null);
  voicePanel.append(
    ui.el('p', { class: 'analytics-unavailable__value', text: 'Not available' }),
    ui.el('p', { class: 'text-muted', text: usage.voice?.reason || 'Voice transcription requests are not persisted.' }),
  );

  const providerPanel = panel('Provider health', 'No generated health probe is created for this dashboard.');
  const health = data.system_health || {};
  providerPanel.append(visibleTable(
    'AI and voice provider configuration',
    ['Service', 'Configured', 'Availability'],
    [
      ['LLM provider', health.llm?.configured ? 'Yes' : 'No', 'Not monitored'],
      ['Voice transcription service', health.voice?.configured ? 'Yes' : 'No', 'Not monitored'],
    ],
  ));
  providerPanel.append(ui.el('p', {
    class: 'card__note',
    text: `${health.llm?.note || 'No LLM health probe is persisted.'} ${health.voice?.note || 'Transcription requests are not audited or persisted.'}`,
  }));

  container.append(
    chartGrid(toolsPanel, latencyPanel, voicePanel, providerPanel),
    ui.el('p', {
      class: 'card__note',
      text: 'Questions, answers, audio files, transcripts, tool arguments and generated content are never displayed or exported by analytics.',
    }),
  );
}

/* ------------------------------------ data quality + service configuration */

function renderQuality(container, data) {
  ui.clear(container);
  const checks = Array.isArray(data.data_quality?.checks) ? data.data_quality.checks : [];
  if (checks.length === 0) {
    ui.renderEmptyState(container, {
      title: 'No data-quality checks available',
      message: 'The backend returned no recorded checks for this selection.',
      headingTag: 'h3',
    });
    return;
  }
  container.append(
    ui.el('div', { class: 'analytics-panel card' },
      visibleTable(
        'Data quality checks',
        ['Check', 'Count', 'Denominator', 'Scope', 'Kind', 'Note'],
        checks.map((check) => [
          String(check.label || check.key || 'Check'),
          formatCount(check.count),
          formatCount(check.denominator),
          check.scope === 'current' ? 'Current' : 'Selected period',
          check.kind === 'classification' ? 'Classification' : 'Optional metadata',
          String(check.note || ''),
        ]),
      ),
      ui.el('p', {
        class: 'card__note',
        text: 'Optional metadata gaps are not errors. No clinical-quality score is calculated, and no patient-level record is exposed.',
      }),
    ),
  );
}

function renderHealth(container, data) {
  ui.clear(container);
  const health = data.system_health || {};
  const apiError = health.api_error_rate || {};
  const integrations = [
    ['Backend', health.backend === 'operational' ? 'Operational' : 'Unknown'],
    ['Database', health.database === 'connected' ? 'Connected' : 'Unknown'],
    ['API error rate', apiError.available ? formatPercent(apiError.rate_pct) : 'Not available'],
    ['LLM provider', health.llm?.configured ? 'Configured' : 'Not configured'],
    ['Voice transcription service', health.voice?.configured ? 'Configured' : 'Not configured'],
  ];
  container.append(
    ui.el('dl', { class: 'analytics-health' },
      integrations.flatMap(([label, value]) => [
        ui.el('dt', { text: label }),
        ui.el('dd', { text: value }),
      ]),
    ),
    ui.el('p', {
      class: 'text-muted',
      text: apiError.reason || 'API error rates are not persisted in the audit stream.',
    }),
    ui.el('p', {
      class: 'text-muted',
      text: 'Service availability is not continuously monitored; backend and database states reflect this successful response. LLM and voice availability require a separate health probe and are shown as not monitored rather than guessed.',
    }),
  );
}

/* ------------------------------------------------------------ staff block */

function renderStaff(container, data) {
  ui.clear(container);
  const block = data.staff || {};
  const roles = Array.isArray(block.by_role) ? block.by_role : [];
  const total = count(block.active_total);
  container.append(kpiGrid(
    kpiTile('Active staff accounts', formatCount(total), 'Non-deactivated accounts'),
    kpiTile('Inactive staff accounts', formatCount(block.inactive_total), 'Current inactive accounts'),
  ));
  if (roles.length === 0) {
    container.append(
      ui.el('div', { class: 'analytics-panel card' },
        ui.el('h3', { class: 'card__title', text: 'Active accounts by role' }),
        ui.el('p', { class: 'text-muted', text: 'No active staff account holds a role.' }),
      ),
    );
    return;
  }
  const rolePanel = ui.el('div', { class: 'analytics-panel card' },
    ui.el('h3', { class: 'card__title', text: 'Active accounts by role' }),
    ui.el('p', { class: 'text-muted analytics-panel__note', text: 'An account holding two roles is counted under both. No individual performance data is exposed.' }),
  );
  const roleHost = ui.el('div');
  rolePanel.append(roleHost);
  graphify.renderHorizontalBars(roleHost, {
    rows: roles.map((entry) => ({
      label: ui.roleLabel(entry.role),
      value: count(entry.count),
    })),
    ariaLabel: 'Active staff accounts by role.',
    tableCaption: 'Active staff accounts by role',
    valueFormatter: formatCount,
  });
  container.append(rolePanel);
}

/* ------------------------------------------------------------ page render */

function renderSkeletons() {
  for (const [id, label] of SECTIONS) {
    const region = document.getElementById(id);
    if (region) ui.renderLoading(region, { label, rows: 3 });
  }
}

function updatePeriodText(range) {
  const label = document.getElementById('analytics-period-label');
  if (label) {
    label.textContent = `Reporting period: ${periodLabel(range)} · appointment buckets: ${range.appointment_timezone || 'clinic timezone'}`;
  }
  const summary = document.getElementById('analytics-filter-summary');
  if (summary) {
    summary.textContent = `Showing ${ui.formatDate(range.start) || range.start} to ${ui.formatDate(range.end) || range.end} (${formatCount(range.days)} days). Compared with the preceding equally long period${range.previous_start ? ` (${ui.formatDate(range.previous_start) || range.previous_start} to ${ui.formatDate(range.previous_end) || range.previous_end})` : ''}.`;
  }
}

function renderAll(data) {
  state.summary = data;
  renderKpis(document.getElementById('kpi-region'), data);
  renderPatients(document.getElementById('patients-region'), data);
  renderAppointments(document.getElementById('appointments-region'), data);
  renderLabs(document.getElementById('labs-region'), data);
  renderPrescriptions(document.getElementById('prescriptions-region'), data);
  renderSecurity(document.getElementById('security-region'), data);
  renderAi(document.getElementById('ai-region'), data);
  renderQuality(document.getElementById('quality-region'), data);
  renderHealth(document.getElementById('health-region'), data);
  renderStaff(document.getElementById('staff-region'), data);

  updatePeriodText(data.range || state.range);
  const generated = document.getElementById('analytics-generated-at');
  if (generated) {
    generated.textContent = `Generated ${ui.formatDateTime(data.generated_at) || 'at an unknown time'} (UTC)`;
  }
  const status = document.getElementById('analytics-status');
  if (status) {
    status.textContent = `Analytics updated — ${formatCount(data.range?.days || 0)}-day reporting period.`;
  }
  const error = document.getElementById('analytics-error-region');
  if (error) {
    ui.clear(error);
    error.hidden = true;
  }
}

/* ----------------------------------------------------------- filter wiring */

function setCustomVisible(show) {
  const custom = document.getElementById('analytics-custom');
  if (custom) custom.hidden = !show;
}

function setRangeInputs(start, end) {
  const startInput = document.getElementById('analytics-start');
  const endInput = document.getElementById('analytics-end');
  if (startInput) startInput.value = start;
  if (endInput) endInput.value = end;
}

function showRangeError(message) {
  const error = document.getElementById('analytics-range-error');
  if (!error) return;
  error.textContent = message;
  error.hidden = !message;
}

function clearRangeError() {
  showRangeError('');
}

function applyRange(preset, start, end) {
  const days = inclusiveDays(start, end);
  if (days > 366) {
    showRangeError('The maximum reporting range is 366 days.');
    return;
  }
  clearRangeError();
  state.range = {
    preset,
    start,
    end,
    days,
    appointment_timezone: state.summary?.range?.appointment_timezone || null,
  };
  setRangeInputs(start, end);
  updatePeriodText({
    ...state.range,
    previous_start: null,
    previous_end: null,
  });
  loadSummary();
}

function applyPreset(preset) {
  const range = rangeForPreset(preset);
  applyRange(preset, range.start, range.end);
}

function applyCustom() {
  const start = parseIsoDate(document.getElementById('analytics-start')?.value);
  const end = parseIsoDate(document.getElementById('analytics-end')?.value);
  if (!start || !end) {
    showRangeError('Choose both a start and an end date.');
    return;
  }
  if (end < start) {
    showRangeError('The end date must be on or after the start date.');
    return;
  }
  applyRange('custom', start, end);
}

function wireFilter() {
  const form = document.getElementById('analytics-filter-form');
  const select = document.getElementById('analytics-range');
  const reset = document.getElementById('analytics-reset');
  if (!form || !select) return;

  select.addEventListener('change', () => {
    const value = select.value;
    setCustomVisible(value === 'custom');
    clearRangeError();
    if (value !== 'custom') applyPreset(value);
  });

  form.addEventListener('submit', (event) => {
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (select.value === 'custom') applyCustom();
    else applyPreset(select.value);
  });

  if (reset) {
    reset.addEventListener('click', () => {
      select.value = '30';
      setCustomVisible(false);
      clearRangeError();
      applyPreset('30');
    });
  }

  setCustomVisible(select.value === 'custom');
}

function loadSummary() {
  const range = state.range || { ...rangeForPreset('30'), preset: '30' };
  const query = { start: range.start, end: range.end };
  const key = cache.keyFor(SUMMARY_PATH, query);
  const token = ++state.loadToken;
  const cached = cache.peek(key);
  const errorRegion = document.getElementById('analytics-error-region');
  if (errorRegion) {
    ui.clear(errorRegion);
    errorRegion.hidden = true;
  }
  if (!cached || cached.stale) {
    renderSkeletons();
  }

  cache.swr(key, () => api.get(SUMMARY_PATH, query), {
    onData: (data) => {
      if (token !== state.loadToken) return;
      renderAll(data);
    },
    onError: (error) => {
      if (token !== state.loadToken) return;
      // No cached data exists here (SWR keeps working content on background
      // failure), so clear the skeleton regions and show one honest error
      // with a retry instead of leaving perpetual loaders on screen.
      for (const [id] of SECTIONS) {
        const region = document.getElementById(id);
        if (region) ui.clear(region);
      }
      const host = errorRegion || document.getElementById('page-body');
      ui.renderErrorState(host, error, {
        describe: api.describeApiError,
        onRetry: loadSummary,
      });
    },
    onRevalidating: (on) => {
      if (token === state.loadToken) {
        ui.setSyncIndicator(document.getElementById('analytics-status'), on);
      }
    },
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

    const initial = { ...rangeForPreset('30'), preset: '30' };
    state.range = { ...initial, days: inclusiveDays(initial.start, initial.end) };
    setRangeInputs(initial.start, initial.end);
    wireFilter();
    updatePeriodText(state.range);
    loadSummary();
  } catch (error) {
    ui.renderErrorState(document.getElementById('page-body'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
  }
});
