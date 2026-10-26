// ---------------------------------------------------------------------------
// patient.html page module — one synthetic patient: demographics, summary
// (encounters, problems, latest labs, active meds), lab results, prescriptions.
// Endpoints (docs/API.md):
//   GET /api/v1/patients/{id}                -> demographics + provenance
//   GET /api/v1/patients/{id}/summary        -> synthetic summary
//   GET /api/v1/patients/{id}/labs           -> Page of lab results
//   GET /api/v1/patients/{id}/prescriptions  -> Page of prescriptions
// Every section has its own loading, empty, and error state; missing records
// are friendly empty states, not errors.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as ui from './ui.js';

const PAGE_SIZE = 20;

const state = {
  patientId: null,
  labsOffset: 0,
  prescriptionsOffset: 0,
  labsTotal: 0,
  prescriptionsTotal: 0,
};

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
    'Synthetic patient'
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

// Runs one section load: skeleton -> data -> renderer, or a friendly error
// panel with retry. Renderers own their empty states.
function loadSection(container, fetcher, render, { loadingLabel = 'Loading' } = {}) {
  const attempt = () => {
    ui.renderLoading(container, { label: loadingLabel, rows: 3 });
    Promise.resolve()
      .then(fetcher)
      .then((data) => {
        render(data);
      })
      .catch((error) => {
        ui.renderErrorState(container, error, {
          describe: api.describeApiError,
          onRetry: attempt,
          headingTag: 'h3',
        });
      });
  };
  attempt();
}

function renderDemographics(patient) {
  const region = document.getElementById('demographics-region');
  ui.clear(region);

  const provenance = firstValue(patient, ['provenance']) || 'synthetic';
  const birth = ui.formatDate(firstValue(patient, ['birth_date', 'date_of_birth', 'dob']));

  region.append(
    ui.el('dl', { class: 'detail-grid' },
      ui.el('dt', { text: 'Name' }),
      ui.el('dd', { text: patientName(patient) }),
      ui.el('dt', { text: 'Medical record number' }),
      ui.el('dd', {
        text: firstValue(patient, ['mrn', 'medical_record_number', 'record_number']) || 'Not recorded',
      }),
      ui.el('dt', { text: 'Date of birth' }),
      ui.el('dd', { text: birth || 'Not recorded' }),
      ui.el('dt', { text: 'Sex' }),
      ui.el('dd', { text: firstValue(patient, ['sex', 'gender']) || 'Not recorded' }),
      ui.el('dt', { text: 'Data provenance' }),
      ui.el('dd', {},
        ui.el('span', {
          class: provenance === 'synthetic' ? 'badge badge--synthetic' : 'badge badge--neutral',
          text: provenance === 'synthetic' ? 'Synthetic demo data' : provenance,
        }),
      ),
    ),
  );

  const heading = document.getElementById('patient-heading');
  if (heading) heading.textContent = patientName(patient);
}

function renderSummary(summary) {
  const region = document.getElementById('summary-region');
  ui.clear(region);

  const encounters = asList(firstValue(summary, ['encounters']) || summary.encounters);
  const problems = asList(summary.problems);
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

  region.append(
    ui.el('div', { class: 'summary-grid' },
      block('Encounters', encountersNode),
      block('Active problems', problemsNode),
      block('Latest labs', labsNode),
      block('Active medications', medsNode),
    ),
    ui.el('p', {
      class: 'card__note',
      text: 'Summary content is synthetic demo data and is not a clinical assessment.',
    }),
  );
}

function renderLabs(page) {
  const region = document.getElementById('labs-region');
  const labs = asList(page.items);
  state.labsTotal = Number.isFinite(page.total) ? page.total : labs.length;

  if (labs.length === 0) {
    ui.renderEmptyState(region, {
      title: 'No lab results',
      message: 'No lab results are recorded for this synthetic patient.',
      headingTag: 'h3',
    });
    return;
  }

  ui.clear(region);
  const table = ui.el('table', { class: 'table table-stack' },
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
      message: 'No prescriptions are recorded for this synthetic patient.',
      headingTag: 'h3',
    });
    return;
  }

  ui.clear(region);
  const table = ui.el('table', { class: 'table table-stack' },
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
  loadSection(
    document.getElementById('labs-region'),
    () => api.get(`/patients/${encodeURIComponent(state.patientId)}/labs`, {
      limit: PAGE_SIZE,
      offset: state.labsOffset,
    }),
    renderLabs,
    { loadingLabel: 'Loading lab results' },
  );
}

function loadPrescriptions() {
  loadSection(
    document.getElementById('prescriptions-region'),
    () => api.get(`/patients/${encodeURIComponent(state.patientId)}/prescriptions`, {
      limit: PAGE_SIZE,
      offset: state.prescriptionsOffset,
    }),
    renderPrescriptions,
    { loadingLabel: 'Loading prescriptions' },
  );
}

function loadSummary() {
  loadSection(
    document.getElementById('summary-region'),
    () => api.get(`/patients/${encodeURIComponent(state.patientId)}/summary`),
    renderSummary,
    { loadingLabel: 'Loading patient summary' },
  );
}

function loadDemographics() {
  loadSection(
    document.getElementById('demographics-region'),
    () => api.get(`/patients/${encodeURIComponent(state.patientId)}`),
    renderDemographics,
    { loadingLabel: 'Loading patient demographics' },
  );
}

function renderMissingId() {
  const body = ui.clear(document.getElementById('page-body'));
  body.append(
    ui.el('div', { class: 'state-panel state-panel--error', role: 'alert' },
      ui.el('h2', { class: 'state-panel__title', text: 'No patient selected' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: 'This page needs a patient link. Open a patient from the patient list to see its synthetic record.',
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

  try {
    const user = await auth.initAuthChrome({ active: 'patients' });
    if (!user) return;
  } catch (error) {
    ui.renderErrorState(document.getElementById('page-body'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
    return;
  }

  loadDemographics();
  loadSummary();
  loadLabs();
  loadPrescriptions();

  // ================================================================
  // EXTENSION POINT: AI ASSISTANT PANEL (PLAN.md Phase 8 UI)
  // The AI panel attaches here, anchored to the #ai-panel placeholder in
  // patient.html (directly after the prescriptions section). It will POST
  // /api/v1/ai/ask { patient_id, question } via api.post(), render the
  // { answer, used_tools, citations, model, ai_generated, disclaimer }
  // response with explicit AI-generated labelling, and handle 503
  // (dependency_unavailable) with a graceful unavailable state. Build no
  // interactive AI controls until that wave.
  // ================================================================
});
