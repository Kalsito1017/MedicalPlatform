// ---------------------------------------------------------------------------
// print-record.js — printable patient medical record.
//
// Loaded on demand from patient.js (first print click). Fetches the complete
// record through the existing authenticated api.js client, renders a dedicated
// #print-record DOM tree with text nodes only, and then invokes the browser's
// native print dialog. Nothing here stores clinical data, logs it, or talks to
// an external service.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as i18n from './i18n.js';
import * as ui from './ui.js';

export const PRINT_PAGE_LIMIT = 100;
const MAX_PAGES = 500;

export class PrintLoadError extends Error {
  constructor(section, cause) {
    super(`Could not load ${section}.`);
    this.name = 'PrintLoadError';
    this.section = section;
    this.cause = cause;
  }
}

function text(value, fallback = '\u2014') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}

function patientName(patient) {
  if (!patient) return 'Patient';
  return (
    patient.display_name ||
    patient.name ||
    [patient.given_name, patient.family_name].filter(Boolean).join(' ') ||
    'Patient'
  );
}

function displayDateTime(value) {
  return ui.formatDateTime(value) || '\u2014';
}

function displayDate(value) {
  return ui.formatDate(value) || '\u2014';
}

function formatDose(prescription) {
  const amount = prescription && prescription.dose_amount;
  const unit = prescription && prescription.dose_unit;
  if (amount != null && amount !== '') return unit ? `${amount} ${unit}` : String(amount);
  return unit ? String(unit) : '\u2014';
}

/** Parse a plain decimal string into sign/digits/scale (no float rounding). */
function decimalParts(value) {
  const raw = String(value).trim();
  const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(raw);
  if (!match) return null;
  const fraction = match[3] || '';
  const digits = (match[2] + fraction).replace(/^0+/, '') || '0';
  return {
    sign: match[1] === '-' && digits !== '0' ? -1 : 1,
    digits,
    scale: fraction.length,
  };
}

/** Compare two decimal strings: -1, 0, 1, or null when not numeric. */
export function compareDecimal(left, right) {
  const a = decimalParts(left);
  const b = decimalParts(right);
  if (!a || !b) return null;
  if (a.digits === '0' && b.digits === '0') return 0;
  if (a.sign !== b.sign) return a.sign < b.sign ? -1 : 1;
  const scale = Math.max(a.scale, b.scale);
  const aDigits = a.digits + '0'.repeat(scale - a.scale);
  const bDigits = b.digits + '0'.repeat(scale - b.scale);
  const av = BigInt(aDigits);
  const bv = BigInt(bDigits);
  if (av === bv) return 0;
  const result = av < bv ? -1 : 1;
  return a.sign === -1 ? -result : result;
}

/** The same strict rule as the trend endpoint: outside the recorded bounds. */
export function deriveLabFlag(lab) {
  if (!lab || lab.value_num == null || lab.value_num === '') {
    return { key: 'none', label: '\u2014', rowClass: '', flagClass: '' };
  }
  if (lab.ref_low != null) {
    const low = compareDecimal(lab.value_num, lab.ref_low);
    if (low !== null && low < 0) {
      return {
        key: 'low',
        label: 'LOW \u2193',
        rowClass: 'print-row--abnormal',
        flagClass: 'print-flag--low',
      };
    }
  }
  if (lab.ref_high != null) {
    const high = compareDecimal(lab.value_num, lab.ref_high);
    if (high !== null && high > 0) {
      return {
        key: 'high',
        label: 'HIGH \u2191',
        rowClass: 'print-row--abnormal',
        flagClass: 'print-flag--high',
      };
    }
  }
  if (lab.ref_low != null || lab.ref_high != null) {
    return { key: 'normal', label: 'Normal', rowClass: '', flagClass: '' };
  }
  return { key: 'none', label: '\u2014', rowClass: '', flagClass: '' };
}

export function formatReferenceRange(lab) {
  const low = lab && lab.ref_low != null ? String(lab.ref_low) : null;
  const high = lab && lab.ref_high != null ? String(lab.ref_high) : null;
  if (low != null && high != null) return `${low}\u2013${high}`;
  if (low != null) return `\u2265 ${low}`;
  if (high != null) return `\u2264 ${high}`;
  return '\u2014';
}

function labResultText(lab) {
  if (lab.value_num != null && lab.value_num !== '') return String(lab.value_num);
  if (lab.value_text != null && lab.value_text !== '') return String(lab.value_text);
  return '\u2014';
}

/**
 * Fetch every page of a bounded list endpoint. A short/empty page before
 * `total` is reached is an error, never silent truncation.
 */
export async function fetchAllPages(fetchPage, { limit = PRINT_PAGE_LIMIT } = {}) {
  const items = [];
  let offset = 0;
  let total = null;
  let pages = 0;
  while (true) {
    const page = await fetchPage({ limit, offset });
    if (!page || !Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < 0) {
      throw new Error('Malformed paginated response.');
    }
    if (total === null) total = page.total;
    else if (page.total !== total) throw new Error('Record count changed while loading.');
    if (page.items.length > limit) throw new Error('Page exceeds the requested limit.');
    if (offset + page.items.length > total) throw new Error('Page exceeds the record count.');
    items.push(...page.items);
    pages += 1;
    offset += page.items.length;
    if (offset >= total) break;
    if (page.items.length === 0) throw new Error('Incomplete paginated response.');
    if (pages >= MAX_PAGES) throw new Error('Too many pages.');
  }
  return items;
}

async function loadSection(section, task) {
  try {
    return await task();
  } catch (error) {
    throw new PrintLoadError(section, error);
  }
}

/** Fetch the complete record. Every section is wrapped so errors name it. */
export async function loadPrintData(patientId) {
  const base = `/patients/${encodeURIComponent(patientId)}`;
  const [patient, summary, diagnoses, labs, prescriptions, encounters] = await Promise.all([
    loadSection('patient information', () => api.get(base)),
    loadSection('patient summary', () => api.get(`${base}/summary`)),
    loadSection('diagnoses', () =>
      fetchAllPages(({ limit, offset }) => api.get(`${base}/diagnoses`, { limit, offset })),
    ),
    loadSection('laboratory results', () =>
      fetchAllPages(({ limit, offset }) => api.get(`${base}/labs`, { limit, offset })),
    ),
    loadSection('prescriptions', () =>
      fetchAllPages(({ limit, offset }) => api.get(`${base}/prescriptions`, { limit, offset })),
    ),
    loadSection('encounter history', () =>
      fetchAllPages(({ limit, offset }) => api.get(`${base}/encounters`, { limit, offset })),
    ),
  ]);
  return { patient, summary, diagnoses, labs, prescriptions, encounters };
}

function fact(label, value) {
  return ui.el('div', { class: 'print-fact' },
    ui.el('dt', { text: label }),
    ui.el('dd', { text: text(value, 'Not recorded') }),
  );
}

function empty(message) {
  return ui.el('p', { class: 'print-empty', text: message });
}

function section(title, children, { className = '' } = {}) {
  return ui.el('section', { class: `print-section ${className}`.trim() },
    ui.el('h2', { text: title }),
    children,
  );
}

function list(items) {
  return ui.el('ul', { class: 'print-list' },
    items.map((item) => ui.el('li', { text: item })),
  );
}

function buildRunningHeader(patient, mrn) {
  return ui.el('header', { class: 'print-running-head' },
    ui.el('p', { class: 'print-running-brand' },
      'MediCore',
      ui.el('span', { text: 'Clinical Platform' }),
    ),
    ui.el('div', {},
      ui.el('p', { class: 'print-running-title', text: 'Patient Medical Record' }),
      ui.el('p', { class: 'print-running-identity' },
        ui.el('strong', { text: patientName(patient) }),
        ` \u00b7 MRN ${mrn}`,
      ),
    ),
  );
}

function buildDocumentHeader(generatedAt) {
  return ui.el('header', { class: 'print-doc-header' },
    ui.el('h1', { text: 'Patient Medical Record' }),
    ui.el('p', { text: `MediCore Clinical Platform \u00b7 Generated ${generatedAt}` }),
  );
}

function buildPatientSection(patient) {
  return section('Patient Information',
    ui.el('dl', { class: 'print-facts' },
      fact('Full name', patientName(patient)),
      fact('MRN', patient.mrn),
      fact('Date of birth', patient.date_of_birth ? displayDate(patient.date_of_birth) : null),
      fact('Age', patient.age_years != null ? `${patient.age_years} years` : null),
      fact('Sex', patient.sex),
      fact('Blood type', patient.blood_type),
    ),
  );
}

function buildSummarySection(summary) {
  const vitals = summary.vitals;
  const vitalRows = [];
  if (vitals) {
    if (vitals.weight_kg != null) vitalRows.push(`Weight: ${text(vitals.weight_kg)} kg`);
    if (vitals.height_cm != null) vitalRows.push(`Height: ${text(vitals.height_cm)} cm`);
    if (vitals.measured_at) vitalRows.push(`Measured: ${displayDateTime(vitals.measured_at)}`);
    if (vitals.previous_weight_kg != null) {
      vitalRows.push(`Previous weight: ${text(vitals.previous_weight_kg)} kg`);
    }
    if (vitals.previous_measured_at) {
      vitalRows.push(`Previous measurement: ${displayDateTime(vitals.previous_measured_at)}`);
    }
  }

  const clinicalParts = [];
  if (summary.symptoms) {
    clinicalParts.push(ui.el('p', { class: 'print-narrative' },
      ui.el('strong', { text: 'Symptoms: ' }), String(summary.symptoms)));
  }
  if (summary.assessment) {
    clinicalParts.push(ui.el('p', { class: 'print-narrative' },
      ui.el('strong', { text: 'Assessment: ' }), String(summary.assessment)));
  }
  if (summary.proposed_plan) {
    clinicalParts.push(ui.el('p', { class: 'print-narrative' },
      ui.el('strong', { text: 'Proposed plan: ' }), String(summary.proposed_plan)));
  }
  if (clinicalParts.length === 0) clinicalParts.push(empty('No clinical summary recorded.'));

  return section('Patient Summary', [
    ui.el('h3', { class: 'print-subhead', text: 'Clinical summary' }),
    ...clinicalParts,
    ui.el('h3', { class: 'print-subhead', text: 'Vitals' }),
    vitalRows.length > 0 ? list(vitalRows) : empty('No vitals recorded.'),
  ]);
}

function diagnosisDescription(diagnosis) {
  const bg = diagnosis && diagnosis.code_description ? String(diagnosis.code_description) : null;
  const en = diagnosis && diagnosis.code_description_en
    ? String(diagnosis.code_description_en)
    : null;
  if (i18n.getLanguage() === 'en') return en || bg;
  return bg || en;
}

function diagnosisStatusLabel(status) {
  const labels = {
    active: 'Active',
    resolved: 'Resolved',
    ruled_out: 'Ruled out',
  };
  return labels[status] || status || 'Unknown';
}

function buildDiagnosisSection(diagnoses) {
  if (!Array.isArray(diagnoses) || diagnoses.length === 0) {
    return section('Diagnoses and ICD-10 Codes', empty('No diagnoses recorded.'));
  }
  const rows = diagnoses.map((diagnosis) => {
    const code = diagnosis && diagnosis.code ? String(diagnosis.code) : '';
    const codeStatus = diagnosis && diagnosis.code_status ? diagnosis.code_status : 'missing';
    const description = code ? diagnosisDescription(diagnosis) : null;
    const review = Boolean(code && codeStatus !== 'confirmed');
    const onset = diagnosis && diagnosis.onset_date ? displayDate(diagnosis.onset_date) : null;
    const nameCell = ui.el('td', {},
      ui.el('div', { text: text(diagnosis && diagnosis.name, 'Unnamed diagnosis') }),
      onset
        ? ui.el('div', { class: 'print-meta', text: `${i18n.t('Onset')}: ${onset}` })
        : null,
    );
    const codeCell = ui.el(
      'td',
      { class: `print-code${code ? '' : ' print-code--missing'}`.trim() },
      ui.el('strong', {
        text: code || i18n.t('ICD-10 code: Not assigned/confirmed'),
      }),
      review
        ? ui.el('div', { class: 'print-meta', text: i18n.t('Review required') })
        : null,
    );
    const descriptionCell = ui.el('td', {
      text: description
        || (code ? i18n.t('No description in the configured catalogue.') : '\u2014'),
    });
    const statusCell = ui.el('td', {
      text: i18n.t(diagnosisStatusLabel(diagnosis && diagnosis.status)),
    });
    return ui.el('tr', {}, nameCell, codeCell, descriptionCell, statusCell);
  });
  return section('Diagnoses and ICD-10 Codes',
    ui.el('table', { class: 'print-table' },
      ui.el('caption', {
        text: `${diagnoses.length} ${i18n.t('diagnoses')} \u00b7 ${i18n.t('all recorded')}`,
      }),
      ui.el('thead', {},
        ui.el('tr', {},
          ['Diagnosis', 'ICD-10 Code', 'ICD-10 Description', 'Status']
            .map((header) => ui.el('th', { scope: 'col', text: header })),
        ),
      ),
      ui.el('tbody', {}, rows),
    ),
    { className: 'print-section--diagnoses' },
  );
}

function sortEncounters(encounters) {
  return [...encounters].sort((a, b) => {
    const aTime = Date.parse(String(a.occurred_at || ''));
    const bTime = Date.parse(String(b.occurred_at || ''));
    if (Number.isFinite(aTime) && Number.isFinite(bTime) && aTime !== bTime) return bTime - aTime;
    return String(a.id || '').localeCompare(String(b.id || ''));
  });
}

function buildEncounterSection(encounters) {
  if (!encounters.length) return section('Encounter History', empty('No encounters recorded.'));
  return section('Encounter History',
    encounters.map((encounter) => {
      const vitals = [];
      if (encounter.weight_kg != null) vitals.push(`Weight ${text(encounter.weight_kg)} kg`);
      if (encounter.height_cm != null) vitals.push(`Height ${text(encounter.height_cm)} cm`);
      return ui.el('article', { class: 'print-encounter' },
        ui.el('div', { class: 'print-encounter__head' },
          ui.el('p', { class: 'print-encounter__type', text: text(encounter.encounter_type, 'Encounter') }),
          ui.el('p', { class: 'print-encounter__date', text: displayDateTime(encounter.occurred_at) }),
        ),
        ui.el('p', {
          class: 'print-encounter__summary',
          text: text(encounter.summary, 'No encounter summary recorded.'),
        }),
        vitals.length > 0
          ? ui.el('p', { class: 'print-vitals', text: vitals.join(' \u00b7 ') })
          : null,
      );
    }),
  );
}

function sortLabs(labs) {
  return [...labs].sort((a, b) => {
    const aTime = Date.parse(String(a.collected_at || ''));
    const bTime = Date.parse(String(b.collected_at || ''));
    if (Number.isFinite(aTime) && Number.isFinite(bTime) && aTime !== bTime) return bTime - aTime;
    return String(a.id || '').localeCompare(String(b.id || ''));
  });
}

function buildLabSection(labs) {
  if (!labs.length) return section('Laboratory Results', empty('No laboratory results recorded.'));
  const rows = sortLabs(labs).map((lab) => {
    const flag = deriveLabFlag(lab);
    const collectedCell = ui.el('td', { class: 'print-date' },
      ui.el('div', { text: displayDateTime(lab.collected_at) }),
      lab.resulted_at
        ? ui.el('div', { class: 'print-meta', text: `${i18n.t('Resulted')} ${displayDateTime(lab.resulted_at)}` })
        : null,
    );
    return ui.el('tr', { class: flag.rowClass },
      ui.el('td', { text: text(lab.test_name || lab.test_code, 'Unnamed test') }),
      ui.el('td', { class: 'print-num', text: labResultText(lab) }),
      ui.el('td', { text: text(lab.unit, '\u2014') }),
      ui.el('td', { class: 'print-num', text: formatReferenceRange(lab) }),
      ui.el('td', { class: `print-flag ${flag.flagClass}`.trim(), text: flag.label }),
      ui.el('td', { text: text(lab.status, '\u2014') }),
      collectedCell,
    );
  });
  return section('Laboratory Results',
    ui.el('table', { class: 'print-table' },
      ui.el('caption', { text: `${labs.length} result${labs.length === 1 ? '' : 's'} \u00b7 complete available record` }),
      ui.el('thead', {},
        ui.el('tr', {},
          ['Test', 'Result', 'Unit', 'Reference', 'Flag', 'Status', 'Collected'].map((header) =>
            ui.el('th', { scope: 'col', text: header })),
        ),
      ),
      ui.el('tbody', {}, rows),
    ),
    { className: 'print-section--labs' },
  );
}

function prescriptionItems(prescription) {
  return Array.isArray(prescription.items) ? prescription.items : [];
}

function buildPrescriptionSection(prescriptions) {
  if (!prescriptions.length) return section('Prescriptions', empty('No prescriptions recorded.'));
  const ordered = [...prescriptions].sort((a, b) => {
    const aActive = a.status === 'active' ? 0 : 1;
    const bActive = b.status === 'active' ? 0 : 1;
    if (aActive !== bActive) return aActive - bActive;
    return String(b.start_date || '').localeCompare(String(a.start_date || ''));
  });
  const rows = ordered.map((prescription) => {
    const items = prescriptionItems(prescription);
    const medication = ui.el('div', {},
      ui.el('span', { text: text(prescription.medication_name, 'Unnamed medication') }),
      items.length > 0
        ? ui.el('div', {
            class: 'print-meta',
            text: `Items: ${items.map((item) => [
              item.medication,
              item.dose_amount != null ? `${item.dose_amount}${item.dose_unit ? ` ${item.dose_unit}` : ''}` : '',
            ].filter(Boolean).join(' ')).join('; ')}`,
          })
        : null,
      prescription.prescriber_id
        ? ui.el('div', {
            class: 'print-meta',
            text: `Prescriber reference: ${prescription.prescriber_id}`,
          })
        : null,
    );
    const active = prescription.status === 'active';
    return ui.el('tr', { class: active ? '' : 'print-prescription--inactive' },
      ui.el('td', {}, medication),
      ui.el('td', { class: 'print-num', text: formatDose(prescription) }),
      ui.el('td', { text: text(prescription.route, '\u2014') }),
      ui.el('td', { text: text(prescription.frequency, '\u2014') }),
      ui.el('td', {
        class: `print-status-text ${active ? '' : 'print-status-text--inactive'}`.trim(),
        text: text(prescription.status, '\u2014'),
      }),
      ui.el('td', { class: 'print-date', text: displayDate(prescription.start_date) }),
      ui.el('td', { class: 'print-date', text: prescription.end_date ? displayDate(prescription.end_date) : '\u2014' }),
    );
  });
  return section('Prescriptions',
    ui.el('table', { class: 'print-table' },
      ui.el('caption', {
        text: `${prescriptions.length} prescription${prescriptions.length === 1 ? '' : 's'} \u00b7 active first, then start date`,
      }),
      ui.el('thead', {},
        ui.el('tr', {},
          ['Medication', 'Dose', 'Route', 'Frequency', 'Status', 'Start', 'End']
            .map((header) => ui.el('th', { scope: 'col', text: header })),
        ),
      ),
      ui.el('tbody', {}, rows),
    ),
  );
}

/** Render the full report into the dedicated print container. */
export function renderPrintRecord(container, data, generatedAt) {
  if (!container) throw new Error('Print container is missing.');
  ui.clear(container);
  const patient = data.patient || {};
  const mrn = text(patient.mrn, 'Not recorded');
  container.append(
    ui.el('table', { class: 'print-repeat' },
      ui.el('thead', {},
        ui.el('tr', {},
          ui.el('td', {}, buildRunningHeader(patient, mrn)),
        ),
      ),
      ui.el('tbody', {},
        ui.el('tr', {},
          ui.el('td', {},
            ui.el('main', { class: 'print-doc' },
              buildDocumentHeader(generatedAt),
              buildPatientSection(patient),
              buildSummarySection(data.summary || {}),
              buildDiagnosisSection(data.diagnoses || []),
              buildEncounterSection(sortEncounters(data.encounters || [])),
              buildLabSection(data.labs || []),
              buildPrescriptionSection(data.prescriptions || []),
            ),
          ),
        ),
      ),
    ),
  );
  i18n.applyLanguage(container);
}

function setStatus(node, message, { tone = 'info', retry = null } = {}) {
  if (!node) return;
  ui.clear(node);
  node.hidden = !message;
  node.classList.toggle('print-status--error', tone === 'error');
  node.setAttribute('role', tone === 'error' ? 'alert' : 'status');
  if (!message) return;
  node.append(ui.el('span', { text: message }));
  if (retry) {
    node.append(ui.el('button', {
      class: 'btn btn-secondary btn-sm print-status__retry',
      type: 'button',
      text: 'Retry',
      onClick: retry,
    }));
  }
}

function waitForPaint() {
  if (typeof requestAnimationFrame !== 'function') return Promise.resolve();
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
}

/**
 * Fetch, render and print the complete record. On any load failure the print
 * dialog is not opened and the status region names the failed section.
 */
export async function prepareAndPrint({
  patientId,
  button = null,
  statusNode = null,
  recordNode = null,
  printWindow = null,
} = {}) {
  if (!patientId) throw new Error('Patient id is required.');
  if (button) {
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
  }
  setStatus(statusNode, 'Preparing patient record\u2026');
  try {
    const data = await loadPrintData(patientId);
    const generatedAt = displayDateTime(new Date().toISOString());
    renderPrintRecord(recordNode, data, generatedAt);
    setStatus(statusNode, 'Record prepared. Opening print dialog\u2026');
    await waitForPaint();
    if (typeof document !== 'undefined' && document.fonts && document.fonts.ready) {
      try {
        await document.fonts.ready;
      } catch {
        // Font loading is best-effort; system fonts still print.
      }
    }
    const win = printWindow || (typeof window !== 'undefined' ? window : null);
    if (!win || typeof win.print !== 'function') {
      throw new Error('Printing is not available in this browser.');
    }
    win.print();
    setStatus(statusNode, 'Print dialog opened.');
  } catch (error) {
    const section = error instanceof PrintLoadError ? error.section : 'patient record';
    setStatus(
      statusNode,
      `Could not load ${section}. The report was not printed.`,
      {
        tone: 'error',
        retry: () => prepareAndPrint({
          patientId,
          button,
          statusNode,
          recordNode,
          printWindow,
        }),
      },
    );
  } finally {
    if (button) {
      button.disabled = false;
      button.removeAttribute('aria-busy');
    }
  }
}
