// ---------------------------------------------------------------------------
// patient.html page module — one patient: demographics, summary
// (encounters, problems, latest labs, active meds), lab results, prescriptions,
// and the AI assistant panel.
// Endpoints (docs/API.md):
//   GET /api/v1/patients/{id}                -> demographics
//   GET /api/v1/patients/{id}/summary        -> patient summary
//   GET /api/v1/patients/{id}/labs           -> Page of lab results
//   GET /api/v1/patients/{id}/prescriptions  -> Page of prescriptions
//   POST /api/v1/ai/ask                      -> AI-generated answer (optional)
//   GET /api/v1/ai/conversations             -> own history, filtered by patient
// Every section has its own loading, empty, and error state; missing records
// are friendly empty states, not errors.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as aiHistory from './ai-history.js';
import * as auth from './auth.js';
import * as cache from './cache.js';
import * as commandMenu from './command-menu.js?v=20261109j';
import * as markdown from './markdown.js';
import * as prefetch from './prefetch.js';
import * as toast from './toast.js';
import * as ui from './ui.js';

const PAGE_SIZE = 20;

const state = {
  patientId: null,
  labsOffset: 0,
  prescriptionsOffset: 0,
  labsTotal: 0,
  prescriptionsTotal: 0,
};

// Hidden tab data is loaded on first activation, not during the initial page
// waterfall. The Summary tab already has its data; Labs/Prescriptions/AI
// history only cost a request when the clinician actually opens them.
const tabDataState = {
  labs: false,
  prescriptions: false,
  aiHistory: null, // Promise | null
};

// Graphify is only needed by the Labs tab. Loading it on demand keeps the
// default Summary view from paying for the 23 KB chart module up front.
let graphifyModule = null;
async function loadGraphify() {
  if (!graphifyModule) graphifyModule = await import('./graphify.js');
  return graphifyModule;
}

/* --------------------------------------------------------- normalization */
// The frozen contract fixes the envelope and pagination shapes but not every
// clinical field name; these helpers accept the documented names and common
// alternates so the page degrades gracefully instead of rendering "undefined".

function firstValue(source, keys) {
  for (const key of keys) {
    const value = source ? source[key] : null;
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return null;
}

function patientName(patient) {
  return (
    firstValue(patient, ['display_name', 'name', 'full_name']) ||
    [patient.first_name, patient.last_name].filter(Boolean).join(' ') ||
    'Patient'
  );
}

function normalizeLab(lab) {
  const range = firstValue(lab, ['reference_range', 'reference', 'range']);
  let rangeText = null;
  if (typeof range === 'string') rangeText = range;
  else if (range && typeof range === 'object') {
    const low = firstValue(range, ['low', 'min', 'lower']);
    const high = firstValue(range, ['high', 'max', 'upper']);
    if (low != null && high != null) rangeText = `${low}\u2013${high}`;
    else rangeText = firstValue(range, ['text', 'display']);
  }
  if (rangeText == null) {
    // Backend schema shape: top-level ref_low / ref_high (docs/API.md payloads).
    const low = firstValue(lab, ['ref_low', 'low']);
    const high = firstValue(lab, ['ref_high', 'high']);
    if (low != null && high != null) rangeText = `${low}\u2013${high}`;
    else if (low != null || high != null) rangeText = String(low != null ? low : high);
  }
  return {
    name: firstValue(lab, ['test_name', 'name', 'test', 'test_code', 'code']) || 'Unnamed test',
    value: firstValue(lab, ['value_num', 'value_text', 'value', 'result', 'result_value']),
    unit: firstValue(lab, ['unit', 'units', 'uom']),
    range: rangeText,
    status: firstValue(lab, ['status', 'flag', 'interpretation']),
    collected: firstValue(lab, ['collected_at', 'collected_date', 'collected', 'resulted_at', 'resulted_date']),
  };
}

function normalizePrescription(rx) {
  const dose = firstValue(rx, ['dose_amount', 'dose', 'dose_value', 'strength']);
  const doseUnit = firstValue(rx, ['dose_unit', 'unit', 'strength_unit']);
  return {
    medication:
      firstValue(rx, ['medication_name', 'medication', 'name', 'drug_name']) || 'Unnamed medication',
    dose: dose != null ? `${dose}${doseUnit ? ` ${doseUnit}` : ''}` : null,
    route: firstValue(rx, ['route', 'route_name']),
    frequency: firstValue(rx, ['frequency', 'schedule', 'frequency_name']),
    status: firstValue(rx, ['status']),
    startDate: firstValue(rx, ['start_date', 'start', 'started_at', 'prescribed_at']),
    endDate: firstValue(rx, ['end_date', 'end', 'ended_at', 'stopped_at']),
  };
}

function statusBadge(status) {
  const text = typeof status === 'string' && status.trim() !== '' ? status : null;
  if (!text) return ui.el('span', { class: 'badge badge--neutral', text: 'Not recorded' });
  const normalized = text.toLowerCase();
  let variant = 'neutral';
  if (['normal', 'active', 'completed', 'ok', 'final'].includes(normalized)) variant = 'ok';
  else if (['high', 'low', 'abnormal', 'critical', 'stopped', 'cancelled', 'canceled'].includes(normalized)) variant = 'alert';
  else if (['pending', 'in_progress', 'draft', 'on_hold'].includes(normalized)) variant = 'warn';
  return ui.el('span', { class: `badge badge--${variant}`, text });
}

function asList(value) {
  return Array.isArray(value) ? value : [];
}

/** Label for an active-medication entry (string or prescription object). */
function medLabel(entry) {
  if (entry && typeof entry === 'object') {
    const name = firstValue(entry, ['medication_name', 'medication', 'name', 'drug_name']);
    if (name) {
      const rx = normalizePrescription(entry);
      return rx.dose ? `${name} (${rx.dose}${rx.route ? `, ${rx.route}` : ''})` : name;
    }
  }
  return listItemLabel(entry);
}

function listItemLabel(entry) {
  if (typeof entry === 'string') return entry;
  if (entry && typeof entry === 'object') {
    return (
      firstValue(entry, ['name', 'display', 'text', 'description', 'label']) ||
      firstValue(entry, ['code']) ||
      JSON.stringify(entry)
        .replace(/[{}"]/g, '')
        .replace(/,/g, ', ')
    );
  }
  return entry == null ? '' : String(entry);
}

/* ------------------------------------------------------------- sections */

// Runs one section load: heart loader (via the hero placeholder contract for
// hero regions) -> data -> renderer, or a friendly error panel with retry.
// Renderers own their empty states. GET responses are cached in memory
// (cache.js): revisiting a section renders the cached copy instantly and
// revalidates in the background, re-rendering only on change.
function loadSection(container, key, fetcher, render, { loadingLabel = 'Loading', onError = null, blurVariant = null } = {}) {
  const surfaceError = (error) => {
    ui.renderErrorState(container, error, {
      describe: api.describeApiError,
      onRetry: attempt,
      headingTag: 'h3',
    });
    if (typeof onError === 'function') onError(error);
  };
  const attempt = () => {
    if (!cache.peek(key)) {
      if (blurVariant) {
        ui.renderBlurPlaceholder(container, { label: loadingLabel, variant: blurVariant });
      } else {
        ui.renderLoading(container, { label: loadingLabel, rows: 3 });
      }
    }
    cache.swr(key, fetcher, {
      onData: (data) => {
        // Renderers may load a non-critical module on demand (e.g. Graphify on
        // the Labs tab). Await the promise before declaring the region ready.
        const rendered = render(data);
        if (rendered && typeof rendered.then === 'function') {
          rendered.then(
            () => {
              if (blurVariant) ui.markBlurReady(container);
            },
            surfaceError,
          );
          return;
        }
        if (blurVariant) ui.markBlurReady(container);
      },
      onError: surfaceError,
      onRevalidating: (on) => ui.setSyncIndicator(container, on),
    });
  };
  attempt();
}

/** Clipboard copy control with an accessible, honest fallback message. */
function copyValueButton(value, label, feedbackText) {
  const feedback = ui.el('p', {
    class: 'copy-feedback',
    role: 'status',
    'aria-live': 'polite',
    text: '',
  });
  const button = ui.el('button', {
    class: 'btn btn-secondary btn-sm hit-slop',
    type: 'button',
    onClick: async () => {
      const nav = typeof navigator !== 'undefined' ? navigator : null;
      if (nav && nav.clipboard && typeof nav.clipboard.writeText === 'function') {
        try {
          await nav.clipboard.writeText(value);
          feedback.textContent = feedbackText;
          return;
        } catch {
          // Clipboard denied or unavailable: fall through to manual copy.
        }
      }
      feedback.textContent =
        'Automatic copy is unavailable here. Select the value and copy it manually.';
    },
  }, ui.icon('copy', 'icon--sm'), label);
  return { button, feedback };
}

function renderDemographics(patient) {
  const region = document.getElementById('demographics-region');
  ui.clear(region);

  const name = patientName(patient);
  const birth = ui.formatDate(firstValue(patient, ['birth_date', 'date_of_birth', 'dob']));
  const sex = firstValue(patient, ['sex', 'gender']);
  const mrn =
    firstValue(patient, ['mrn', 'medical_record_number', 'record_number']) || 'Not recorded';

  const avatarNode = document.getElementById('patient-avatar');
  if (avatarNode) avatarNode.textContent = ui.initials(name);

  const metaLine = [birth ? `Born ${birth}` : 'Date of birth not recorded', sex || null]
    .filter(Boolean)
    .join(' \u2022 ');

  const copy = copyValueButton(mrn, 'Copy MRN', 'MRN copied to the clipboard.');
  region.append(
    ui.el('div', { class: 'patient-header__meta' },
      ui.el('span', { class: 'mono', text: `MRN: ${mrn}` }),
      copy.button,
    ),
    copy.feedback,
    ui.el('p', { class: 'patient-header__line', text: metaLine }),
  );

  const heading = document.getElementById('patient-heading');
  if (heading) {
    // Long compound names (e.g. "Александър-Константин
    // Димитров-Стоянов") wrap cleanly at phone widths and 200% zoom.
    heading.classList.add('text-break');
    heading.textContent = name;
  }

  const aiContext = document.getElementById('ai-patient-context');
  if (aiContext) {
    const mrnText = mrn !== 'Not recorded' ? ` \u00b7 MRN ${mrn}` : '';
    aiContext.textContent =
      `About: ${name}${mrnText}. Answers use only this patient\u2019s record.`;
  }
}

function renderSummary(summary) {
  const region = document.getElementById('summary-region');
  ui.clear(region);

  const encounters = asList(firstValue(summary, ['encounters']) || summary.encounters);
  const problems = asList(firstValue(summary, ['active_problems', 'problems']));
  const latestLabs = asList(
    firstValue(summary, ['latest_labs', 'latestLabs', 'labs']) || summary.latest_labs
  );
  const activeMeds = asList(
    firstValue(summary, ['active_prescriptions', 'active_medications', 'active_meds', 'medications']) ||
      summary.active_medications
  );

  const block = (title, contentNode) =>
    ui.el('section', { class: 'summary-block', 'aria-label': title },
      ui.el('h3', { class: 'summary-block__title', text: title }),
      contentNode,
    );

  const problemsNode = problems.length > 0
    ? ui.el('ul', { class: 'chip-list' }, problems.map((entry) => ui.el('li', { class: 'chip', text: listItemLabel(entry) })))
    : ui.el('p', { class: 'text-muted', text: 'No active problems recorded.' });

  const labsNode = latestLabs.length > 0
    ? ui.el('ul', { class: 'compact-list' }, latestLabs.map((entry) => {
        const lab = normalizeLab(entry);
        return ui.el('li', {
          text: `${lab.name}: ${lab.value != null ? lab.value : 'value not recorded'}${lab.unit ? ` ${lab.unit}` : ''}${lab.collected ? ` (${ui.displayDate(lab.collected)})` : ''}`,
        });
      }))
    : ui.el('p', { class: 'text-muted', text: 'No lab results recorded.' });

  const medsNode = activeMeds.length > 0
    ? ui.el('ul', { class: 'compact-list' }, activeMeds.map((entry) => ui.el('li', { text: medLabel(entry) })))
    : ui.el('p', { class: 'text-muted', text: 'No active medications recorded.' });

  region.append(
    ui.el('div', { class: 'summary-grid' },
      block('Active problems', problemsNode),
      block('Latest labs', labsNode),
      block('Active medications', medsNode),
    ),
  );

  renderEncounters(encounters);
}

function renderEncounters(encounters) {
  const region = document.getElementById('encounters-region');
  if (!region) return;
  ui.clear(region);

  const encountersNode = encounters.length > 0
    ? ui.el('ol', { class: 'timeline' }, encounters.map((encounter) => {
        const date = ui.formatDate(firstValue(encounter, ['date', 'encounter_date', 'occurred_at', 'datetime', 'start']));
        const type = firstValue(encounter, ['type', 'encounter_type', 'kind']);
        const text = firstValue(encounter, ['summary', 'reason', 'description', 'note']);
        return ui.el('li', { class: 'timeline__item' },
          ui.el('p', { class: 'timeline__meta' },
            ui.el('span', { text: date || 'Date not recorded' }),
            type ? ui.el('span', { class: 'badge badge--neutral', text: String(type) }) : null,
          ),
          text ? ui.el('p', { class: 'timeline__text', text }) : null,
        );
      }))
    : ui.el('p', { class: 'text-muted', text: 'No encounters recorded.' });

  region.append(encountersNode);
}

async function renderLabTrends(payload) {
  const region = document.getElementById('lab-trends-region');
  if (!region) return;
  ui.clear(region);
  const series = Array.isArray(payload && payload.series) ? payload.series : [];
  const usable = series.filter((entry) => Array.isArray(entry.points) && entry.points.length > 0);
  if (usable.length === 0) {
    ui.renderEmptyState(region, {
      title: 'No lab trends',
      message: 'No numeric lab results are available for a trend chart.',
      headingTag: 'h3',
    });
    return;
  }

  region.append(
    ui.el('div', { class: 'lab-summary-head' },
      ui.el('h3', { class: 'lab-summary-title', text: 'Lab Results Summary' }),
      ui.el('p', {
        class: 'field-hint',
        text: 'One chart per test. Shaded bands show the recorded reference range; values are plotted on each test\u2019s own scale.',
      }),
    ),
  );
  const gridHost = ui.el('div', { class: 'lab-chart-grid-host' });
  region.append(gridHost);

  const graphify = await loadGraphify();
  graphify.renderLabTrendPanels(gridHost, {
    series: usable.map((entry) => ({
      testName: entry.test_name || entry.test_code,
      testCode: entry.test_code,
      unit: entry.unit,
      refLow: entry.ref_low,
      refHigh: entry.ref_high,
      abnormalCount: entry.abnormal_count,
      points: entry.points.map((point) => ({
        x: point.collected_at,
        y: point.value_num,
        flag: point.flag,
      })),
    })),
  });

  if (payload.truncated) {
    region.append(ui.el('p', {
      class: 'field-hint',
      text: 'Showing the most recent points for each test; older points are outside the chart window.',
    }));
  }
}

function renderLabs(page) {
  const region = document.getElementById('labs-region');
  const labs = asList(page.items);
  state.labsTotal = Number.isFinite(page.total) ? page.total : labs.length;

  if (labs.length === 0) {
    ui.renderEmptyState(region, {
      title: 'No lab results',
      message: 'No lab results are recorded for this patient.',
      headingTag: 'h3',
    });
    return;
  }

  ui.clear(region);
  const table = ui.el('table', { class: 'table table-stack tabular-nums' },
    ui.el('thead', {},
      ui.el('tr', {},
        ui.el('th', { scope: 'col', text: 'Test' }),
        ui.el('th', { scope: 'col', text: 'Value' }),
        ui.el('th', { scope: 'col', text: 'Unit' }),
        ui.el('th', { scope: 'col', text: 'Reference range' }),
        ui.el('th', { scope: 'col', text: 'Status' }),
        ui.el('th', { scope: 'col', text: 'Collected' }),
      ),
    ),
    ui.el('tbody', {}, labs.map((entry) => {
      const lab = normalizeLab(entry);
      return ui.el('tr', {},
        ui.el('td', { 'data-label': 'Test', text: lab.name }),
        ui.el('td', { 'data-label': 'Value', text: lab.value != null ? String(lab.value) : 'Not recorded' }),
        ui.el('td', { 'data-label': 'Unit', text: lab.unit || 'Not recorded' }),
        ui.el('td', { 'data-label': 'Reference range', text: lab.range || 'Not recorded' }),
        ui.el('td', { 'data-label': 'Status' }, statusBadge(lab.status)),
        ui.el('td', { 'data-label': 'Collected', text: ui.displayDate(lab.collected) }),
      );
    })),
  );

  region.append(
    ui.el('div', {
      class: 'table-scroll',
      role: 'region',
      'aria-label': 'Lab results',
      tabindex: '0',
    }, table),
  );
  const paginationHost = ui.el('div', {});
  region.append(paginationHost);
  ui.renderPagination(paginationHost, {
    total: state.labsTotal,
    limit: PAGE_SIZE,
    offset: state.labsOffset,
    count: labs.length,
    itemLabel: 'lab results',
    onChange: (nextOffset) => {
      state.labsOffset = nextOffset;
      loadLabs();
    },
  });
}

function renderPrescriptions(page) {
  const region = document.getElementById('prescriptions-region');
  const prescriptions = asList(page.items);
  state.prescriptionsTotal = Number.isFinite(page.total)
    ? page.total
    : prescriptions.length;

  if (prescriptions.length === 0) {
    ui.renderEmptyState(region, {
      title: 'No prescriptions',
      message: 'No prescriptions are recorded for this patient.',
      headingTag: 'h3',
    });
    return;
  }

  ui.clear(region);
  const table = ui.el('table', { class: 'table table-stack tabular-nums' },
    ui.el('thead', {},
      ui.el('tr', {},
        ui.el('th', { scope: 'col', text: 'Medication' }),
        ui.el('th', { scope: 'col', text: 'Dose' }),
        ui.el('th', { scope: 'col', text: 'Route' }),
        ui.el('th', { scope: 'col', text: 'Frequency' }),
        ui.el('th', { scope: 'col', text: 'Status' }),
        ui.el('th', { scope: 'col', text: 'Start' }),
        ui.el('th', { scope: 'col', text: 'End' }),
      ),
    ),
    ui.el('tbody', {}, prescriptions.map((entry) => {
      const rx = normalizePrescription(entry);
      return ui.el('tr', {},
        ui.el('td', { 'data-label': 'Medication', text: rx.medication }),
        ui.el('td', { 'data-label': 'Dose', text: rx.dose || 'Not recorded' }),
        ui.el('td', { 'data-label': 'Route', text: rx.route || 'Not recorded' }),
        ui.el('td', { 'data-label': 'Frequency', text: rx.frequency || 'Not recorded' }),
        ui.el('td', { 'data-label': 'Status' }, statusBadge(rx.status)),
        ui.el('td', { 'data-label': 'Start', text: ui.displayDate(rx.startDate) }),
        ui.el('td', { 'data-label': 'End', text: rx.endDate ? ui.displayDate(rx.endDate) : 'Ongoing / not recorded' }),
      );
    })),
  );

  region.append(
    ui.el('div', {
      class: 'table-scroll',
      role: 'region',
      'aria-label': 'Prescriptions',
      tabindex: '0',
    }, table),
  );
  const paginationHost = ui.el('div', {});
  region.append(paginationHost);
  ui.renderPagination(paginationHost, {
    total: state.prescriptionsTotal,
    limit: PAGE_SIZE,
    offset: state.prescriptionsOffset,
    count: prescriptions.length,
    itemLabel: 'prescriptions',
    onChange: (nextOffset) => {
      state.prescriptionsOffset = nextOffset;
      loadPrescriptions();
    },
  });
}

/* ------------------------------------------------------------ data loads */

function loadLabs() {
  const query = { limit: PAGE_SIZE, offset: state.labsOffset };
  loadSection(
    document.getElementById('labs-region'),
    cache.keyFor(`/patients/${encodeURIComponent(state.patientId)}/labs`, query),
    () => api.get(`/patients/${encodeURIComponent(state.patientId)}/labs`, query),
    renderLabs,
    { loadingLabel: 'Loading lab results' },
  );
}

function loadLabTrends() {
  const region = document.getElementById('lab-trends-region');
  if (!region) return;
  const path = `/patients/${encodeURIComponent(state.patientId)}/labs/trends`;
  loadSection(
    region,
    cache.keyFor(path),
    () => api.get(path),
    renderLabTrends,
    { loadingLabel: 'Loading lab trends' },
  );
}

function loadPrescriptions() {
  const query = { limit: PAGE_SIZE, offset: state.prescriptionsOffset };
  loadSection(
    document.getElementById('prescriptions-region'),
    cache.keyFor(`/patients/${encodeURIComponent(state.patientId)}/prescriptions`, query),
    () => api.get(`/patients/${encodeURIComponent(state.patientId)}/prescriptions`, query),
    renderPrescriptions,
    { loadingLabel: 'Loading prescriptions' },
  );
}

function loadSummary() {
  const encountersRegion = document.getElementById('encounters-region');
  if (encountersRegion && !cache.peek(summaryCacheKey())) {
    ui.renderLoading(encountersRegion, { label: 'Loading encounters', rows: 2 });
  }
  loadSection(
    document.getElementById('summary-region'),
    summaryCacheKey(),
    () => api.get(`/patients/${encodeURIComponent(state.patientId)}/summary`),
    renderSummary,
    {
      loadingLabel: 'Loading patient summary',
      onError: () => {
        if (!encountersRegion) return;
        ui.clear(encountersRegion);
        encountersRegion.append(
          ui.el('div', { class: 'state-panel state-panel--empty' },
            ui.el('h3', { class: 'state-panel__title', text: 'Encounters unavailable' }),
            ui.el('p', {
              class: 'state-panel__text',
              text: 'The patient summary could not be loaded, so encounters are unavailable. Use the retry action in the summary panel.',
            }),
          ),
        );
      },
    },
  );
}

function summaryCacheKey() {
  return cache.keyFor(`/patients/${encodeURIComponent(state.patientId)}/summary`);
}

function loadDemographics() {
  const path = `/patients/${encodeURIComponent(state.patientId)}`;
  loadSection(
    document.getElementById('demographics-region'),
    cache.keyFor(path),
    () => api.get(path),
    renderDemographics,
    // Hero region: the heart loader stands in for the header card, then the
    // real demographics sharpen in (hero loading regions, DESIGN.md item 8).
    { loadingLabel: 'Loading patient demographics', blurVariant: 'hero' },
  );
}

function renderMissingId() {
  const body = ui.clear(document.getElementById('page-body'));
  body.append(
    ui.el('div', { class: 'state-panel state-panel--error', role: 'alert' },
      ui.el('h2', { class: 'state-panel__title', text: 'No patient selected' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: 'This page needs a patient link. Open a patient from the patient list to see its record.',
      }),
      ui.el('div', { class: 'state-panel__actions' },
        ui.el('a', { class: 'btn btn-primary', href: 'patients.html' }, 'Go to patient list'),
      ),
    ),
  );
}

ui.onReady(async () => {
  ui.setRetryHandler(() => window.location.reload());

  const params = new URLSearchParams(window.location.search);
  const patientId = (params.get('id') || '').trim();
  if (!patientId) {
    ui.initChrome({ user: null, active: 'patients', navItems: [] });
    renderMissingId();
    return;
  }
  state.patientId = patientId;

  let user;
  try {
    user = await auth.initAuthChrome({ active: 'patients' });
    if (!user) return;
  } catch (error) {
    ui.renderErrorState(document.getElementById('page-body'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
    return;
  }

  // Contextual command-menu actions for this record (AI surfaces follow the
  // same permission gate as the AI Patient tab itself).
  const aiCommands = auth.hasPermission(user, 'patient:read')
    ? [
      {
        id: 'summarize-patient',
        title: 'Summarize this patient',
        hint: 'Ask the assistant for problems, labs, medications, and plan',
        keywords: 'ai assistant summary summarize patient',
        group: 'Actions',
        run: () => {
          openAiPatientTab();
          fillComposer(SUMMARY_QUESTION);
          askAssistant();
        },
      },
      {
        id: 'ask-ai-patient',
        title: 'Ask AI about this patient',
        hint: 'Open AI Patient and focus the question box',
        keywords: 'ai patient ask question patient',
        group: 'Actions',
        run: () => openAiPatientTab({ focusComposer: true }),
      },
      ...PATIENT_SUGGESTIONS.map((question) => ({
        id: `ask-${question}`,
        title: question,
        hint: 'Fill the question box (you review before sending)',
        keywords: 'ai patient question suggestion',
        group: 'Actions',
        run: () => {
          openAiPatientTab();
          fillComposer(question);
        },
      })),
    ]
    : [];
  commandMenu.initCommandMenu({ user, commands: aiCommands });

  // Pre-rendering (prefetch.js): on idle, warm the modules a navigation from
  // this page will likely need (link hints only — nothing is executed early).
  prefetch.warmModules(['assets/js/ai-assistant.js', 'assets/js/patients.js']);

  loadDemographics();
  loadSummary();

  // ================================================================
  // AI PATIENT PANEL (PLAN.md Phase 8 UI) — landed here in the AI Patient
  // tab, anchored to #ai-panel in patient.html. POST /api/v1/ai/ask
  // { patient_id, question } via api.post(); the
  // { answer, used_tools, citations, model, ai_generated } response is
  // rendered as plain text only, explicitly labelled AI-generated. 503
  // (dependency_unavailable) is a normal state and renders an honest
  // unavailable panel; 429 renders a retry countdown; other errors go
  // through api.describeApiError. Composer keys: Enter sends, Shift+Enter
  // inserts a new line; compose is disabled while a request is in flight.
  // Quick actions ("Summarize this patient" + suggestion chips) ask real
  // questions of the existing tools. Exchanges are persisted server-side per
  // user and listed as this patient's history (docs/API.md "AI assistant"
  // history) — never in web storage, never logged. The history request is
  // lazy: it starts when the AI Patient tab is first opened (or before the
  // first ask), so the Summary tab does not pay for a hidden panel.
  // ================================================================
  initAiPanel(user);
  initPrintAction(user);
  initRemoveAction(user);
  initTabs({ onActivate: ensureTabData });
});

/* --------------------------------------------------------- record tabs */
// Icon tabs (Summary | Labs | Prescriptions | Encounters | AI Patient)
// switching the section panels. Full ARIA tabs pattern: roving tabindex,
// arrow-key navigation, Home/End support. Every panel keeps loading
// independently.

/** Load a hidden tab's data the first time that tab becomes visible. */
function ensureTabData(tab) {
  const id = tab && tab.id;
  if (id === 'tab-labs') {
    if (!tabDataState.labs) {
      tabDataState.labs = true;
      loadLabs();
      loadLabTrends();
    }
    return;
  }
  if (id === 'tab-prescriptions') {
    if (!tabDataState.prescriptions) {
      tabDataState.prescriptions = true;
      loadPrescriptions();
    }
    return;
  }
  if (id === 'tab-ai-patient') ensureAiHistory();
}

function initTabs({ onActivate } = {}) {
  const tablist = document.querySelector('.tabs');
  if (!tablist) return;
  const tabs = Array.from(tablist.querySelectorAll('.tab'));
  if (tabs.length === 0) return;

  const activate = (tab, { focus = false } = {}) => {
    // Panel swaps run inside a view transition (ui.js wrapper: feature-detected
    // with an instant fallback and reduced-motion opt-out).
    ui.withViewTransition(() => {
      for (const other of tabs) {
        const selected = other === tab;
        other.classList.toggle('is-active', selected);
        other.setAttribute('aria-selected', selected ? 'true' : 'false');
        other.setAttribute('tabindex', selected ? '0' : '-1');
        const panel = document.getElementById(other.getAttribute('aria-controls'));
        if (panel) panel.hidden = !selected;
      }
    });
    if (typeof onActivate === 'function') onActivate(tab);
    if (focus && typeof tab.focus === 'function') tab.focus();
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activate(tab));
    tab.addEventListener('keydown', (event) => {
      const key = event && event.key;
      if (!key) return;
      let next = null;
      if (key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
      else if (key === 'Home') next = 0;
      else if (key === 'End') next = tabs.length - 1;
      if (next === null) return;
      if (event.preventDefault) event.preventDefault();
      activate(tabs[next], { focus: true });
    });
  });

  const initial = tabs.find((tab) => tab.classList.contains('is-active')) || tabs[0];
  activate(initial);
}

/** Bring the AI Patient tab forward before focusing the composer or asking. */
function openAiPatientTab({ focusComposer = false } = {}) {
  const tab = document.getElementById('tab-ai-patient');
  if (tab && tab.getAttribute('aria-selected') !== 'true') {
    if (typeof tab.click === 'function') tab.click();
  }
  if (focusComposer) {
    const input = document.getElementById('ai-question');
    if (input && typeof input.focus === 'function') input.focus();
  }
}

/** Admin-only soft delete; the button is hidden for every other role. */
function initRemoveAction(user) {
  const button = document.getElementById('remove-patient');
  if (!button) return;
  const canDelete = auth.hasPermission(user, 'patient:delete') && Boolean(state.patientId);
  button.hidden = !canDelete;
  if (!canDelete) return;

  button.addEventListener('click', async () => {
    const heading = document.getElementById('patient-heading');
    const identifier = document.querySelector('#demographics-region .mono');
    const name = heading && heading.textContent ? heading.textContent : 'this patient';
    const mrn = identifier && identifier.textContent ? identifier.textContent : 'Identifier not recorded';
    const confirmed = await ui.confirmDialog({
      title: 'Remove this patient?',
      message: 'The patient will disappear from search, records and appointments. The underlying row, clinical history and audit trail are retained internally.',
      confirmLabel: 'Remove patient',
      cancelLabel: 'Keep patient',
      danger: true,
      details: [
        { label: 'Patient', value: name },
        { label: 'Identifier', value: mrn },
      ],
    });
    if (!confirmed) return;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
      await api.del(`/patients/${encodeURIComponent(state.patientId)}`);
      cache.invalidate('/patients');
      cache.invalidate(`/patients/${state.patientId}`);
      toast.showToast({ message: 'Patient removed from the application.', tone: 'success' });
      window.location.assign('patients.html');
    } catch (error) {
      toast.showToast({ message: api.describeApiError(error).message, tone: 'error' });
      button.disabled = false;
      button.removeAttribute('aria-busy');
    }
  });
}

/** Wire the on-demand print workflow (button is hidden without permission). */
function initPrintAction(user) {
  const button = document.getElementById('print-patient-record');
  const statusNode = document.getElementById('print-status');
  const recordNode = document.getElementById('print-record');
  if (!button) return;
  if (!auth.hasPermission(user, 'patient:read') || !state.patientId) {
    button.hidden = true;
    return;
  }

  let modulePromise = null;
  let running = false;
  button.addEventListener('click', async () => {
    if (running) return;
    running = true;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
      if (!modulePromise) modulePromise = import('./print-record.js');
      const printRecord = await modulePromise;
      await printRecord.prepareAndPrint({
        patientId: state.patientId,
        button,
        statusNode,
        recordNode,
      });
    } catch {
      modulePromise = null;
      if (statusNode) {
        ui.clear(statusNode);
        statusNode.hidden = false;
        statusNode.classList.add('print-status--error');
        statusNode.setAttribute('role', 'alert');
        statusNode.append(ui.el('span', {
          text: 'Could not prepare the patient record. The report was not printed.',
        }));
      }
    } finally {
      running = false;
      button.disabled = false;
      button.removeAttribute('aria-busy');
    }
  });
}

/* ------------------------------------------------------ AI patient panel */

const QUESTION_MAX = 2000;
const aiRequest = { controller: null };

// Doctor-fast actions: real questions for the existing MCP tools (the
// canonical summary maps to get_patient_summary; the chips map to the lab,
// prescription, and encounter reads). Nothing decorative, nothing automated.
const SUMMARY_QUESTION =
  'Summarize this patient: active problems, latest lab results, current medications, and the proposed plan.';
const PATIENT_SUGGESTIONS = [
  'What are the latest abnormal results?',
  'Current medications and proposed plan?',
  'What changed since the last visit?',
];

function setAiStatus(message) {
  const status = document.getElementById('ai-status');
  if (status) status.textContent = message;
}

function syncQuestionCounter() {
  const input = document.getElementById('ai-question');
  const counter = document.getElementById('ai-char-count');
  const submit = document.getElementById('ai-submit');
  const errorNode = document.getElementById('ai-question-error');
  if (!input || !counter || !submit) return;

  const length = input.value.length;
  const over = length > QUESTION_MAX;
  counter.textContent = over
    ? `${length} / ${QUESTION_MAX} characters — over the limit`
    : `${length} / ${QUESTION_MAX} characters`;
  counter.classList.toggle('char-counter--over', over);
  if (over) {
    input.setAttribute('aria-invalid', 'true');
    if (errorNode) {
      errorNode.textContent = `Questions are limited to ${QUESTION_MAX} characters. Shorten your question to ask.`;
      errorNode.hidden = false;
    }
  } else {
    input.removeAttribute('aria-invalid');
    if (errorNode) {
      errorNode.textContent = '';
      errorNode.hidden = true;
    }
  }
  submit.disabled = length === 0 || over;
}

function initAiPanel(user) {
  const form = document.getElementById('ai-question-form');
  const input = document.getElementById('ai-question');
  const cancel = document.getElementById('ai-cancel');
  if (!form || !input || !cancel) return;

  // The panel is only interactive for accounts that may view patient data;
  // the backend still re-checks every request server-side.
  if (!auth.hasPermission(user, 'patient:read')) {
    form.hidden = true;
    const region = document.getElementById('ai-response-region');
    if (region) {
      ui.clear(region);
      region.append(ui.el('p', {
        class: 'text-muted',
        text: 'AI Patient is only available to accounts that can view this patient. Access is enforced by the backend.',
      }));
    }
    const quickActions = document.getElementById('ai-quick-actions');
    if (quickActions) quickActions.hidden = true;
    const historySection = document.getElementById('ai-history');
    if (historySection) historySection.hidden = true;
    setAiStatus('AI Patient is not available for this account.');
    return;
  }

  input.addEventListener('input', syncQuestionCounter);
  input.addEventListener('keydown', onComposerKeydown);
  const send = document.getElementById('ai-submit');
  if (send) {
    send.addEventListener('click', () => {
      askAssistant();
    });
  }
  cancel.addEventListener('click', () => {
    if (aiRequest.controller) aiRequest.controller.abort();
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    askAssistant();
  });
  syncQuestionCounter();
  renderQuickActions();
}

/** Enter sends; Shift+Enter inserts a new line (documented in the hint). */
function onComposerKeydown(event) {
  if (!event || event.key !== 'Enter') return;
  if (event.shiftKey) return;
  if (event.isComposing) return;
  if (event.preventDefault) event.preventDefault();
  askAssistant();
}

function fillComposer(question) {
  const input = document.getElementById('ai-question');
  if (!input) return;
  input.value = question;
  syncQuestionCounter();
  if (typeof input.focus === 'function') input.focus();
}

function renderQuickActions() {
  const host = document.getElementById('ai-quick-actions');
  if (!host) return;
  ui.clear(host);
  host.append(
    ui.el('button', {
      class: 'btn btn-primary ai-summarize',
      type: 'button',
      text: 'Summarize this patient',
      onClick: () => {
        fillComposer(SUMMARY_QUESTION);
        askAssistant();
      },
    }),
  );
  for (const question of PATIENT_SUGGESTIONS) {
    host.append(ui.el('button', {
      class: 'chip chip--action hit-slop',
      type: 'button',
      text: question,
      onClick: () => fillComposer(question),
    }));
  }
}

/** Start the patient-filtered history request at most once, on first need. */
function ensureAiHistory() {
  if (!tabDataState.aiHistory) {
    tabDataState.aiHistory = initAiHistory().catch(() => {});
  }
  return tabDataState.aiHistory;
}

async function initAiHistory() {
  const list = document.getElementById('ai-history-list');
  if (!list) return;
  const key = cache.keyFor('/ai/conversations', {
    limit: 20,
    offset: 0,
    patient_id: state.patientId,
  });
  const renderItems = (page) => {
    aiHistory.renderHistoryList(list, page && Array.isArray(page.items) ? page.items : [], {
      compact: true,
      emptyText: 'No questions about this patient yet.',
    });
  };
  if (!cache.peek(key)) {
    ui.renderLoading(list, {
      label: 'Loading questions about this patient',
      rows: 2,
      wrapperTag: 'li',
    });
  }
  await cache.swr(key, () => aiHistory.fetchHistory({ patientId: state.patientId }), {
    onData: renderItems,
    onError: (error) => aiHistory.renderHistoryFailure(list, error),
    onRevalidating: (on) => ui.setSyncIndicator(list, on),
  });
}

/** The just-finished exchange joins this patient's history without a reload. */
function recordHistoryEntry(question, payload) {
  cache.invalidate('/ai/conversations');
  aiHistory.appendHistoryEntry(document.getElementById('ai-history-list'), {
    question,
    answer: payload && typeof payload.answer === 'string' ? payload.answer : '',
    used_tools: Array.isArray(payload && payload.used_tools) ? payload.used_tools : [],
    citations: Array.isArray(payload && payload.citations) ? payload.citations : [],
    model: payload && typeof payload.model === 'string' ? payload.model : '',
    created_at: new Date().toISOString(),
    patient_id: state.patientId,
  }, { compact: true });
}

function citationLabel(citation) {
  if (typeof citation === 'string') {
    return `Record reference: ${citation}`;
  }
  if (citation && typeof citation === 'object') {
    const type = firstValue(citation, ['resource_type', 'type', 'kind']);
    const id = firstValue(citation, ['resource_id', 'record_id', 'id']);
    const text = firstValue(citation, ['label', 'title', 'text', 'description', 'name']);
    if (type && id) {
      const kind = String(type).replace(/_/g, ' ');
      return `${kind.charAt(0).toUpperCase()}${kind.slice(1)} ${id}`;
    }
    if (text && id) return `Record reference: ${text} (${id})`;
    if (text) return `Record reference: ${text}`;
    if (id) return `Record reference: ${id}`;
    return `Record reference: ${listItemLabel(citation)}`;
  }
  return 'Record reference';
}

function chatBubble(variant, ...children) {
  return ui.el('div', { class: `chat-bubble chat-bubble--${variant}` }, children.flat());
}

function chatMeta(text) {
  return ui.el('p', { class: 'chat-bubble__meta', text });
}

function renderAiAnswer(payload, question = '') {
  const region = document.getElementById('ai-response-region');
  if (!region) return;
  ui.clear(region);

  const answeredAt = ui.formatDateTime(new Date().toISOString()) || '';
  if (question) {
    region.append(
      chatBubble('user',
        ui.el('p', { text: question }),
        answeredAt ? chatMeta(answeredAt) : null,
      ),
    );
  }

  const answer = payload && typeof payload.answer === 'string' ? payload.answer.trim() : '';
  const usedTools = Array.isArray(payload && payload.used_tools) ? payload.used_tools : [];
  const citations = Array.isArray(payload && payload.citations) ? payload.citations : [];
  const model = payload && typeof payload.model === 'string' ? payload.model.trim() : '';

  const bubble = chatBubble('assistant',
    ui.el('div', { class: 'badge-list' },
      ui.el('span', { class: 'badge badge--ai', text: 'AI-generated' }),
    ),
  );
  bubble.prepend(ui.assistantTurnHead());

  // The answer body is untrusted model output: rendered through the safe
  // Markdown-subset renderer (markdown.js builds DOM nodes only, never HTML).
  const answerNode = answer ? markdown.answerBody(answer) : null;
  bubble.append(
    answerNode || ui.el('p', { class: 'text-muted', text: 'The assistant returned no answer text.' }),
  );

  if (usedTools.length > 0) {
    bubble.append(
      ui.el('div', { class: 'ai-sources' },
        ui.el('p', { class: 'field-hint', text: 'Sources consulted:' }),
        ui.el('ul', { class: 'chip-list' },
          usedTools.map((tool) => ui.el('li', { class: 'chip', text: String(tool) })),
        ),
      ),
    );
  }

  if (citations.length > 0) {
    bubble.append(
      ui.el('div', { class: 'ai-citations' },
        ui.el('p', { class: 'field-hint', text: 'Records consulted:' }),
        ui.el('ul', { class: 'compact-list' },
          citations.map((citation) => ui.el('li', { text: citationLabel(citation) })),
        ),
      ),
    );
  }

  bubble.append(
    chatMeta(
      `${model ? `Model: ${model}` : 'Model not reported by the backend.'}${answeredAt ? ` \u2022 ${answeredAt}` : ''}`,
    ),
  );

  region.append(bubble);
  setAiStatus('Answer received. AI-generated answer.');
}

function renderAiUnavailable(region) {
  ui.clear(region);
  region.append(
    ui.el('div', { class: 'state-panel state-panel--empty' },
      ui.el('h3', { class: 'state-panel__title', text: 'AI Patient unavailable' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: 'AI Patient is switched off or unreachable in this deployment. It runs without a language model unless one is configured, so patient-scoped answers can be unavailable. Everything else on this page keeps working, and you can try again later.',
      }),
    ),
  );
  setAiStatus('AI Patient unavailable.');
}

function renderAiRateLimited(region, error) {
  const seconds = error && error.retryAfter ? Math.ceil(error.retryAfter) : null;
  ui.clear(region);
  region.append(
    ui.el('div', { class: 'state-panel state-panel--empty' },
      ui.el('h3', { class: 'state-panel__title', text: 'Too many questions' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: seconds
          ? `You have asked too many questions in a short time. Please retry in ${seconds} seconds.`
          : 'You have asked too many questions in a short time. Please wait a moment and try again.',
      }),
    ),
  );
  setAiStatus(seconds ? `Too many questions. Retry in ${seconds} seconds.` : 'Too many questions.');
}

function renderAiError(error) {
  const region = document.getElementById('ai-response-region');
  if (!region) return;
  if (error instanceof api.ApiError && error.status === 503) {
    renderAiUnavailable(region);
    return;
  }
  if (error instanceof api.ApiError && error.status === 429) {
    renderAiRateLimited(region, error);
    return;
  }
  ui.renderErrorState(region, error, {
    describe: api.describeApiError,
    onRetry: () => askAssistant(),
    headingTag: 'h3',
  });
  setAiStatus('The assistant request did not succeed. See the message on this page.');
}

async function askAssistant() {
  const input = document.getElementById('ai-question');
  const region = document.getElementById('ai-response-region');
  const submit = document.getElementById('ai-submit');
  const cancel = document.getElementById('ai-cancel');
  const question = input ? input.value.trim() : '';

  if (!question || question.length > QUESTION_MAX) {
    syncQuestionCounter();
    return;
  }

  if (aiRequest.controller) aiRequest.controller.abort();
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  aiRequest.controller = controller;

  // Compose is disabled while a request is in flight (Cancel stays available);
  // the finally block restores it.
  if (submit) submit.disabled = true;
  if (input) input.disabled = true;
  if (cancel) cancel.hidden = false;
  setAiStatus('Consulting the assistant\u2026');
  if (region) {
    ui.clear(region);
    // Heart loader for the answer turn (DESIGN.md "Heart loader"): the state
    // panel keeps the status text; the anatomical heart pulses in the answer
    // region until the Markdown answer arrives.
    region.classList.add('blur-region', 'is-loading');
    region.classList.remove('is-ready');
    region.setAttribute('aria-busy', 'true');
    region.append(
      chatBubble('user',
        ui.el('p', { text: question }),
        chatMeta(ui.formatDateTime(new Date().toISOString()) || ''),
      ),
      ui.el('div', { class: 'state-panel', role: 'status' },
        ui.el('div', { class: 'state-panel__media' },
          ui.heartLoader({ size: 'lg', label: '' }),
        ),
        ui.el('p', { class: 'state-panel__title', text: 'Consulting the assistant\u2026' }),
        ui.el('p', {
          class: 'state-panel__text',
          text: 'The assistant is reviewing this record to answer your question.',
        }),
      ),
    );
  }

  try {
    await ensureAiHistory();
    const payload = await api.post(
      '/ai/ask',
      { patient_id: state.patientId, question },
      controller ? { signal: controller.signal } : undefined,
    );
    renderAiAnswer(payload, question);
    recordHistoryEntry(question, payload);
  } catch (error) {
    if (error && error.name === 'AbortError') {
      if (region) {
        ui.clear(region);
        region.append(
          ui.el('p', { class: 'text-muted', text: 'Request cancelled. Ask again whenever you are ready.' }),
        );
      }
      setAiStatus('Request cancelled.');
    } else {
      renderAiError(error);
    }
  } finally {
    aiRequest.controller = null;
    if (region) {
      region.removeAttribute('aria-busy');
      ui.markBlurReady(region);
    }
    if (cancel) cancel.hidden = true;
    if (input) input.disabled = false;
    syncQuestionCounter();
  }
}
