/* Unit/integration checks for the printable patient record. Run with:
 *   node frontend/tests/print-record.test.mjs
 */
import { installDom, textOf } from './domshim.mjs';

const printRecord = await import('../assets/js/print-record.js');

const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
  } catch (error) {
    results.push({
      name,
      ok: false,
      detail: error && error.message ? error.message : String(error),
    });
  }
}
function assert(condition, message) {
  if (!condition) throw new Error(message);
}
function equal(actual, expected, message) {
  if (actual !== expected) throw new Error(`${message} (got ${JSON.stringify(actual)})`);
}

const PATIENT = {
  id: 'p1',
  display_name: 'Ann Test',
  mrn: 'MRN-0001',
  date_of_birth: '1980-05-04',
  age_years: 46,
  sex: 'female',
  blood_type: 'A+',
};
const SUMMARY = {
  patient: PATIENT,
  encounters: [],
  diagnoses: [
    { code: 'E11.9', name: 'Type 2 diabetes mellitus', status: 'active', onset_date: '2024-05-01' },
  ],
  active_problems: [],
  vitals: {
    weight_kg: '70.50',
    height_cm: '165.00',
    measured_at: '2026-10-01T09:30:00Z',
  },
  symptoms: 'fatigue and cold intolerance',
  assessment: 'hypothyroidism',
  proposed_plan: 'recheck TSH in 6 weeks',
  latest_labs: [],
  active_prescriptions: [],
};
const ENCOUNTER = {
  id: 'enc1',
  patient_id: 'p1',
  occurred_at: '2026-10-01T09:30:00Z',
  encounter_type: 'annual exam',
  summary: 'Routine follow-up.',
  weight_kg: '70.50',
  height_cm: '165.00',
};
const LABS = [
  {
    id: 'lab1',
    patient_id: 'p1',
    test_code: 'GLU',
    test_name: 'Glucose',
    value_num: '6.100',
    unit: 'mg/dL',
    ref_low: '4.000',
    ref_high: '6.000',
    status: 'final',
    collected_at: '2026-10-03T07:30:00Z',
    resulted_at: '2026-10-03T09:15:00Z',
  },
  {
    id: 'lab2',
    patient_id: 'p1',
    test_code: 'K',
    test_name: 'Potassium',
    value_num: '3.200',
    unit: 'mmol/L',
    ref_low: '3.500',
    ref_high: '5.100',
    status: 'final',
    collected_at: '2026-10-02T08:00:00Z',
  },
  {
    id: 'lab3',
    patient_id: 'p1',
    test_code: 'TSH',
    test_name: 'TSH',
    value_num: '2.000',
    unit: 'mIU/L',
    ref_low: '0.400',
    ref_high: '4.000',
    status: 'final',
    collected_at: '2026-09-15T09:00:00Z',
  },
];
const PRESCRIPTIONS = [
  {
    id: 'rx1',
    patient_id: 'p1',
    medication_name: 'Metformin',
    dose_amount: '500.000',
    dose_unit: 'mg',
    route: 'oral',
    frequency: 'twice daily',
    status: 'active',
    start_date: '2026-02-01',
    end_date: null,
    prescriber_id: null,
    items: [{ sequence_no: 1, medication: 'Metformin', dose_amount: '500.000', dose_unit: 'mg' }],
  },
  {
    id: 'rx2',
    patient_id: 'p1',
    medication_name: 'Amoxicillin',
    dose_amount: '500.000',
    dose_unit: 'mg',
    route: 'oral',
    frequency: 'three times daily',
    status: 'completed',
    start_date: '2026-01-01',
    end_date: '2026-01-07',
    prescriber_id: null,
    items: [],
  },
];

function response(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: async () => data,
  };
}

function makeFetch({ labs = LABS, prescriptions = PRESCRIPTIONS, encounters = [ENCOUNTER], labsStatus = 200, labsPageSize = 0 } = {}) {
  const calls = [];
  const fetchStub = async (url) => {
    calls.push(String(url));
    const parsed = new URL(String(url), 'http://testserver');
    const path = parsed.pathname;
    const offset = Number(parsed.searchParams.get('offset') || 0);
    const requestedLimit = Number(parsed.searchParams.get('limit') || 20);
    const page = (items, total = items.length, limit = requestedLimit) => response({
      items,
      total,
      limit,
      offset,
    });
    if (path.endsWith('/patients/p1')) return response(PATIENT);
    if (path.endsWith('/patients/p1/summary')) return response(SUMMARY);
    if (path.endsWith('/patients/p1/labs')) {
      if (labsStatus !== 200) {
        return response({ error: { code: 'internal_error', message: 'load failed' } }, labsStatus);
      }
      const size = labsPageSize || requestedLimit;
      return page(labs.slice(offset, offset + size), labs.length, size);
    }
    if (path.endsWith('/patients/p1/prescriptions')) {
      return page(prescriptions.slice(offset, offset + requestedLimit));
    }
    if (path.endsWith('/patients/p1/encounters')) {
      return page(encounters.slice(offset, offset + requestedLimit));
    }
    return response({ error: { code: 'not_found', message: 'missing' } }, 404);
  };
  globalThis.fetch = fetchStub;
  return calls;
}

function mount() {
  installDom();
  const record = document.createElement('div');
  const status = document.createElement('p');
  const button = document.createElement('button');
  document.body.append(record, status, button);
  return { record, status, button };
}

await check('decimal comparison and lab flags use recorded bounds only', () => {
  equal(printRecord.compareDecimal('5.100', '5.1'), 0, 'trailing zero comparison');
  equal(printRecord.compareDecimal('6.100', '6.000'), 1, 'above high');
  equal(printRecord.compareDecimal('3.200', '3.500'), -1, 'below low');
  equal(printRecord.compareDecimal('n/a', '4.0'), null, 'non-numeric value');

  const high = printRecord.deriveLabFlag(LABS[0]);
  equal(high.key, 'high', 'high flag');
  equal(high.label, 'HIGH \u2191', 'high text');
  assert(high.rowClass.includes('print-row--abnormal'), 'high row marker');

  const low = printRecord.deriveLabFlag(LABS[1]);
  equal(low.key, 'low', 'low flag');
  equal(low.label, 'LOW \u2193', 'low text');

  const normal = printRecord.deriveLabFlag(LABS[2]);
  equal(normal.key, 'normal', 'normal flag');

  const unknown = printRecord.deriveLabFlag({ value_num: '5.0' });
  equal(unknown.key, 'none', 'missing range is not invented');
});

await check('fetchAllPages retrieves every page and rejects incomplete data', async () => {
  const source = ['a', 'b', 'c', 'd', 'e'];
  const calls = [];
  const all = await printRecord.fetchAllPages(async ({ limit, offset }) => {
    calls.push({ limit, offset });
    return { items: source.slice(offset, offset + 2), total: source.length, limit: 2, offset };
  }, { limit: 2 });
  equal(all.length, 5, 'all rows fetched');
  equal(calls.length, 3, 'three pages requested');
  equal(calls[0].offset, 0, 'first offset');
  equal(calls[2].offset, 4, 'last offset');

  let failed = false;
  try {
    await printRecord.fetchAllPages(async ({ offset }) => ({
      items: offset === 0 ? ['a', 'b'] : [],
      total: 5,
      limit: 2,
      offset,
    }), { limit: 2 });
  } catch {
    failed = true;
  }
  assert(failed, 'incomplete pagination must fail closed');
});

await check('report renders all sections, flags, ranges and safe text', () => {
  const { record } = mount();
  printRecord.renderPrintRecord(record, {
    patient: PATIENT,
    summary: SUMMARY,
    encounters: [ENCOUNTER],
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
  }, '05 Nov 2026, 12:00');
  const report = textOf(record);
  for (const expected of [
    'Patient Medical Record',
    'Ann Test',
    'MRN-0001',
    '46 years',
    'female',
    'A+',
    'Patient Information',
    'Patient Summary',
    'Type 2 diabetes mellitus',
    'fatigue and cold intolerance',
    'Encounter History',
    'annual exam',
    'Laboratory Results',
    'Prescriptions',
    'Routine follow-up.',
    'Glucose',
    '6.100',
    '4.000',
    '6.000',
    'HIGH \u2191',
    'LOW \u2193',
    'Resulted',
    'Metformin',
    'Amoxicillin',
    'active',
    'completed',
  ]) {
    assert(report.includes(expected), `missing ${expected}`);
  }
  equal(record.querySelectorAll('.print-row--abnormal').length, 2, 'two abnormal lab rows');
  equal(record.querySelectorAll('thead').length, 3, 'running header and both data tables expose headers');
  equal(record.querySelectorAll('script').length, 0, 'no script element from clinical text');
  equal(record.querySelectorAll('img').length, 0, 'no image element from clinical text');
});

await check('missing data is displayed honestly, never fabricated', () => {
  const { record } = mount();
  printRecord.renderPrintRecord(record, {
    patient: { display_name: 'No Data Patient', mrn: 'MRN-9999' },
    summary: {},
    encounters: [],
    labs: [],
    prescriptions: [],
  }, '05 Nov 2026, 12:00');
  const report = textOf(record);
  for (const expected of [
    'No encounters recorded.',
    'No laboratory results recorded.',
    'No prescriptions recorded.',
    'Not recorded',
  ]) {
    assert(report.includes(expected), `missing empty state: ${expected}`);
  }
});

await check('prepareAndPrint fetches complete data and calls native print', async () => {
  const { record, status, button } = mount();
  const calls = makeFetch();
  let printed = 0;
  await printRecord.prepareAndPrint({
    patientId: 'p1',
    recordNode: record,
    statusNode: status,
    button,
    printWindow: { print: () => { printed += 1; } },
  });
  equal(printed, 1, 'window.print called once');
  equal(button.disabled, false, 'button restored');
  assert(calls.some((url) => url.includes('/patients/p1/encounters')), 'encounters fetched');
  assert(textOf(record).includes('Routine follow-up.'), 'encounter printed');
  assert(textOf(record).includes('Glucose'), 'lab printed');
  assert(textOf(record).includes('Metformin'), 'prescription printed');
  assert(textOf(status).includes('Print dialog opened.'), 'success status');
});

await check('prepareAndPrint retrieves every laboratory page', async () => {
  const { record, status, button } = mount();
  const manyLabs = Array.from({ length: 7 }, (_, index) => ({
    ...LABS[2],
    id: `lab-${index}`,
    test_name: `Lab ${index}`,
    collected_at: `2026-10-0${index + 1}T09:00:00Z`,
  }));
  const calls = makeFetch({ labs: manyLabs, labsPageSize: 2 });
  let printed = 0;
  await printRecord.prepareAndPrint({
    patientId: 'p1',
    recordNode: record,
    statusNode: status,
    button,
    printWindow: { print: () => { printed += 1; } },
  });
  equal(printed, 1, 'print called after all pages');
  const report = textOf(record);
  for (let index = 0; index < 7; index += 1) {
    assert(report.includes(`Lab ${index}`), `missing lab page row ${index}`);
  }
  const labCalls = calls.filter((url) => url.includes('/patients/p1/labs'));
  equal(labCalls.length, 4, 'four pages requested for seven rows at page size two');
  for (const offset of [0, 2, 4, 6]) {
    assert(labCalls.some((url) => url.includes(`offset=${offset}`)), `missing offset ${offset}`);
  }
});

await check('a section failure names the section and never opens print', async () => {
  const { record, status, button } = mount();
  makeFetch({ labsStatus: 500 });
  let printed = 0;
  await printRecord.prepareAndPrint({
    patientId: 'p1',
    recordNode: record,
    statusNode: status,
    button,
    printWindow: { print: () => { printed += 1; } },
  });
  equal(printed, 0, 'print must not open on a section failure');
  const message = textOf(status);
  assert(message.includes('laboratory results'), `failed section not named: ${message}`);
  assert(message.includes('not printed'), 'failure message must say the report was not printed');
  assert(status.classList.contains('print-status--error'), 'status uses the error class');
  assert(message.includes('Retry'), 'retry action missing');
  equal(button.disabled, false, 'button restored after failure');
});

await check('hostile clinical text is rendered as text, never as DOM', () => {
  const { record } = mount();
  const hostile = '<script>alert(1)</script><img src=x onerror=alert(1)>';
  printRecord.renderPrintRecord(record, {
    patient: { ...PATIENT, display_name: hostile },
    summary: { ...SUMMARY, symptoms: hostile },
    encounters: [{ ...ENCOUNTER, summary: hostile }],
    labs: [],
    prescriptions: [],
  }, '05 Nov 2026, 12:00');
  equal(record.querySelectorAll('script').length, 0, 'no script node');
  equal(record.querySelectorAll('img').length, 0, 'no image node');
  assert(textOf(record).includes(hostile), 'hostile text should remain readable text');
});

await check('print record follows the selected EN/BG language', async () => {
  const i18n = await import('../assets/js/i18n.js');
  const { record } = mount();
  i18n.setLanguage('bg');
  printRecord.renderPrintRecord(record, {
    patient: PATIENT,
    summary: SUMMARY,
    encounters: [ENCOUNTER],
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
  }, '05 Nov 2026, 12:00');
  const bulgarian = textOf(record);
  assert(bulgarian.includes('Медицинска карта на пациента'), 'Bulgarian print title missing');
  assert(bulgarian.includes('Информация за пациента'), 'Bulgarian patient section missing');
  assert(bulgarian.includes('Лабораторни резултати'), 'Bulgarian lab section missing');
  assert(bulgarian.includes('НАД НОРМАТА ↑'), 'Bulgarian high lab flag missing');
  assert(bulgarian.includes('ПОД НОРМАТА ↓'), 'Bulgarian low lab flag missing');
  assert(bulgarian.includes('Резултат'), 'Bulgarian resulted label missing');

  i18n.setLanguage('en');
  const { record: englishRecord } = mount();
  printRecord.renderPrintRecord(englishRecord, {
    patient: PATIENT,
    summary: SUMMARY,
    encounters: [ENCOUNTER],
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
  }, '05 Nov 2026, 12:00');
  assert(textOf(englishRecord).includes('Patient Medical Record'), 'English print title not restored');
});

for (const result of results) {
  process.stdout.write(`${result.ok ? 'ok' : 'FAIL'} ${result.name}${result.detail ? ` \u2014 ${result.detail}` : ''}\n`);
}
const failed = results.filter((result) => !result.ok).length;
process.stdout.write(`print-record: ${results.length - failed}/${results.length} passed\n`);
if (failed) process.exit(1);
