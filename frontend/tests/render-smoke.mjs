/* Render-path smoke: executes the real frontend page modules in Node against a
 * minimal DOM shim (/tmp/opencode/domshim.mjs) and a stubbed backend that
 * speaks the frozen docs/API.md contract. This verifies that render flows run
 * without throwing and produce expected text. It is NOT a real browser:
 * layout, CSS, focus, and screen-reader behaviour are unverified here. */

/* ---------------------------------------------------------------------------
 * frontend/tests/render-smoke.mjs — render-path smoke for the buildless
 * frontend. Runs the real page modules in Node against a minimal DOM shim
 * (./domshim.mjs) and a stubbed backend that speaks the frozen docs/API.md
 * contract shapes.
 *
 * Run with:  node tests/render-smoke.mjs   (from the frontend/ directory)
 *
 * What this verifies: page render flows execute without throwing and produce
 * the expected text/structure (list + pagination, patient sections, per-section
 * 403 handling, empty/offline states, login validation + uniform 401 message,
 * role-aware nav, anonymous redirect, admin staff table + create/role/activate
 * flows with confirm dialogs and 403/409 rendering, one-time generated
 * password display, AI assistant panel states: answer labelling, 503
 * unavailable, 429 rate limit, 2000-char limit, loading/cancel, permission
 * gating).
 * What this does NOT verify: real browser DOM, layout/CSS, focus behaviour,
 * screen-reader output, and live backend integration.
 * ------------------------------------------------------------------------- */

import { installDom, fixture, textOf, settle } from './domshim.mjs';

const JS = new URL('../assets/js/', import.meta.url).href;
// Shared module for date expectations: Intl output varies slightly across ICU
// versions (e.g. "Sept" vs "Sep"), so assertions compare against ui.formatDate.
const ui = await import(JS + 'ui.js');
let scenarioId = 0;
const results = [];

async function check(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
  } catch (error) {
    results.push({ name, ok: false, detail: error && error.message ? error.message : String(error) });
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertContains(text, needle, message) {
  assert(text.includes(needle), `${message} — expected to find ${JSON.stringify(needle)} in output`);
}

const DOCTOR = {
  id: 'u1',
  email: 'doctor@example.com',
  display_name: 'Dr Ada Example',
  roles: ['doctor'],
  permissions: ['patient:read', 'lab:read', 'prescription:read'],
  is_active: true,
};
const CLIN_ADMIN = {
  id: 'u2',
  email: 'admin@example.com',
  display_name: 'Cal Admin',
  roles: ['clinical_admin'],
  permissions: ['staff:read', 'staff:manage', 'role:assign', 'audit:read'],
  is_active: true,
};
const PATIENT = {
  id: 'p1',
  mrn: 'MRN-0001',
  display_name: 'Ann Test',
  given_name: 'Ann',
  family_name: 'Test',
  date_of_birth: '1980-05-04',
  sex: 'F',
  provenance: 'synthetic',
};
// Fixtures mirror backend/app/schemas/patient.py field names exactly.
const SUMMARY = {
  patient: PATIENT,
  provenance: 'synthetic',
  encounters: [{ id: 'e1', encounter_type: 'office_visit', occurred_at: '2026-09-14', summary: 'Routine follow-up (synthetic).' }],
  latest_labs: [{ test_name: 'Haemoglobin', value_num: '13.5', unit: 'g/dL', collected_at: '2026-09-14' }],
  active_prescriptions: [{ medication_name: 'Amoxicillin', dose_amount: '500', dose_unit: 'mg', route: 'Oral' }],
};
const LABS = {
  items: [
    {
      id: 'l1',
      test_code: 'HGB',
      test_name: 'Haemoglobin',
      value_num: '13.5',
      unit: 'g/dL',
      ref_low: '12.0',
      ref_high: '17.0',
      status: 'normal',
      collected_at: '2026-09-14',
    },
  ],
  total: 1,
  limit: 20,
  offset: 0,
};
const PRESCRIPTIONS = {
  items: [
    {
      id: 'rx1',
      medication_name: 'Amoxicillin',
      dose_amount: '500',
      dose_unit: 'mg',
      route: 'Oral',
      frequency: 'Three times daily',
      status: 'active',
      start_date: '2026-09-14',
      end_date: null,
    },
  ],
  total: 1,
  limit: 20,
  offset: 0,
};

/* -------------------------------------------- admin + AI panel fixtures */

const NURSE = {
  id: 'u7',
  email: 'nurse@example.com',
  display_name: 'Nan Nurse',
  roles: ['nurse'],
  permissions: ['patient:read', 'lab:read', 'prescription:read', 'staff:read'],
  is_active: true,
};

function staffUser(overrides) {
  return {
    id: 'u-1',
    email: 'person@example.com',
    display_name: 'Pat Person',
    roles: [],
    is_active: true,
    created_at: '2026-10-26T13:00:00Z',
    updated_at: '2026-10-26T13:00:00Z',
    ...overrides,
  };
}

const AUDIT_EVENT = {
  id: 'ae1',
  actor_user_id: 'u2',
  actor_email: 'admin@example.com',
  actor_display_name: 'Cal Admin',
  action: 'role.grant',
  resource_type: 'user_role',
  resource_id: 'u-3:doctor',
  outcome: 'success',
  request_id: 'req-77',
  created_at: '2026-10-26T13:05:00Z',
};

const AI_ANSWER = {
  answer: 'The latest synthetic haemoglobin is 13.5 g/dL.\n\nIt is within the recorded reference range.',
  used_tools: ['get_patient_lab_results'],
  citations: [{ type: 'lab_result', id: 'DEMO-LAB-0001' }],
  model: 'demo-model-1',
  ai_generated: true,
  disclaimer: 'AI-generated synthetic-demo assistance; not a diagnosis or treatment recommendation.',
};

const ADMIN_IDS = [
  'admin-loading',
  'create-staff-section',
  'create-staff-form',
  'create-email',
  'create-email-error',
  'create-name',
  'create-name-error',
  'create-password',
  'create-password-error',
  'create-role',
  'create-submit',
  'create-status',
  'staff-section',
  'staff-status',
  'staff-region',
  'audit-section',
  'audit-status',
  'audit-region',
];
const AI_IDS = [
  'ai-panel',
  'ai-question-form',
  'ai-question',
  'ai-char-count',
  'ai-question-error',
  'ai-submit',
  'ai-cancel',
  'ai-status',
  'ai-response-region',
];
const PATIENT_IDS = [
  'page-body',
  'patient-heading',
  'patient-avatar',
  'demographics-region',
  'summary-region',
  'labs-region',
  'prescriptions-region',
  'encounters-region',
  ...AI_IDS,
];

function buttonByText(root, text) {
  return root.querySelectorAll('button').find((button) => button.textContent.trim() === text) || null;
}

async function clickButton(root, text) {
  const button = buttonByText(root, text);
  if (!button) throw new Error(`button not found: ${text}`);
  await button.dispatch('click');
}

function firstRow(root) {
  const body = root.querySelector('tbody');
  return body ? body.querySelector('tr') : null;
}

function badgeTexts(root) {
  return root.querySelectorAll('.badge').map((badge) => badge.textContent);
}

const json = (status, body, headers = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: {
    get: (name) => {
      const key = Object.keys(headers).find((h) => h.toLowerCase() === name.toLowerCase());
      return key ? headers[key] : null;
    },
  },
  json: async () => body,
});

function makeFetch({ me = DOCTOR, patients, patient, summary, labs, prescriptions, staff, audit, ai, routes = [], calls = [] }) {
  // A payload carrying a numeric `status` (and no `ok` flag) is an error
  // response: { status, code?, message?, retryAfter? }. A non-200 success
  // carries `ok: true` (e.g. { status: 204, ok: true }).
  const respond = (payload) => {
    if (payload instanceof Error) throw payload;
    if (payload && typeof payload === 'object' && typeof payload.status === 'number' && payload.ok !== true) {
      const code = payload.code || ({
        400: 'validation_error',
        401: 'unauthenticated',
        403: 'forbidden',
        404: 'not_found',
        409: 'conflict',
        429: 'rate_limited',
        500: 'internal_error',
        503: 'dependency_unavailable',
      }[payload.status] || 'forbidden');
      const headers = payload.retryAfter != null ? { 'Retry-After': String(payload.retryAfter) } : {};
      return json(
        payload.status,
        { error: { code, message: payload.message || '', request_id: payload.requestId || 'req-test' } },
        headers,
      );
    }
    if (payload && typeof payload === 'object' && payload.ok === true) {
      return json(payload.status, payload.body !== undefined ? payload.body : null);
    }
    return json(200, payload);
  };
  return async (url, init = {}) => {
    calls.push({ url, init });
    const [path] = url.split('?');
    const method = (init.method || 'GET').toUpperCase();

    for (const route of routes) {
      const routeMethod = (route.method || 'GET').toUpperCase();
      const matches = typeof route.match === 'string' ? path === route.match : route.match.test(path);
      if (routeMethod !== method || !matches) continue;
      const payload = typeof route.response === 'function'
        ? route.response({ path, method, init, body: init.body ? JSON.parse(init.body) : null })
        : route.response;
      return respond(payload);
    }

    if (path.endsWith('/auth/me')) {
      return respond(me);
    }
    if (path.endsWith('/auth/login')) return json(200, me);
    if (path.endsWith('/auth/logout')) return json(204, null);
    if (/\/patients\/[^/]+\/labs$/.test(path)) return respond(labs);
    if (/\/patients\/[^/]+\/prescriptions$/.test(path)) return respond(prescriptions);
    if (/\/patients\/[^/]+\/summary$/.test(path)) return respond(summary);
    if (/\/patients\/[^/]+$/.test(path)) return respond(patient);
    if (path.endsWith('/patients')) return respond(patients);
    if (path.endsWith('/staff') && method === 'GET') {
      if (staff === undefined) throw new Error(`unstubbed request: ${method} ${url}`);
      return respond(staff);
    }
    if (path.endsWith('/audit') && method === 'GET') {
      if (audit === undefined) throw new Error(`unstubbed request: ${method} ${url}`);
      return respond(audit);
    }
    if (path.endsWith('/ai/ask') && method === 'POST') {
      if (ai === undefined) throw new Error(`unstubbed request: ${method} ${url}`);
      return respond(ai);
    }
    throw new Error(`unstubbed request: ${method} ${url}`);
  };
}

async function freshPage(moduleName) {
  scenarioId += 1;
  return import(`${JS}${moduleName}?scenario=${scenarioId}`);
}

function bootDom(ids) {
  const document = installDom();
  fixture(document, ids);
  globalThis.location.pathname = '/page.html';
  globalThis.location.redirectedTo = null;
  globalThis.location.replace = (url) => {
    globalThis.location.redirectedTo = url;
  };
  globalThis.location.assign = (url) => {
    globalThis.location.redirectedTo = url;
  };
  return document;
}

const CHROME_IDS = ['global-status', 'nav-toggle', 'site-nav', 'user-area', 'main'];

/* ---------------------------------------------------- patients list happy path */

await check('patients list renders Page shape with search + pagination', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    patients: {
      items: [
        PATIENT,
        { id: 'p2', display_name: 'Bob Test', mrn: 'MRN-0002', birth_date: '1975-01-20', sex: 'M', provenance: 'synthetic' },
      ],
      total: 42,
      limit: 20,
      offset: 0,
    },
  });
  const document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();

  const region = textOf(document.getElementById('patients-region'));
  assertContains(region, 'Ann Test', 'patient name not rendered');
  assertContains(region, 'MRN-0002', 'MRN not rendered');
  assertContains(region, 'Showing 1\u20132 of 42 synthetic patients', 'pagination summary wrong');
  const link = document.getElementById('patients-region').querySelector('a');
  assertEqualLocal(link && link.getAttribute('href'), 'patient.html?id=p1', 'patient link missing');
  assertContains(textOf(document.getElementById('patients-status')), '42 synthetic patients found', 'status not announced');

  const nav = textOf(document.getElementById('site-nav'));
  assertContains(nav, 'Patients', 'nav missing Patients');
  assert(!nav.includes('Admin'), 'doctor must not see Admin nav');
});

await check('patients search debounces and sends q/limit/offset', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({ calls, patients: { items: [PATIENT], total: 1, limit: 20, offset: 0 } });
  const document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();

  const input = document.getElementById('patient-search');
  input.value = 'ann';
  await input.dispatch('input');
  await settle(400);
  const last = calls[calls.length - 1].url;
  assertContains(last, '/api/v1/patients', 'wrong endpoint');
  assertContains(last, 'q=ann', 'query q missing');
  assertContains(last, 'limit=20', 'limit missing');
});

await check('patients empty state is friendly, not an error', async () => {
  globalThis.fetch = makeFetch({ patients: { items: [], total: 0, limit: 20, offset: 0 } });
  const document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();
  const region = textOf(document.getElementById('patients-region'));
  assertContains(region, 'No synthetic patients match your search', 'empty state copy missing');
});

await check('patients network failure renders offline banner with retry', async () => {
  globalThis.fetch = makeFetch({ patients: new TypeError('Failed to fetch') });
  const document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();
  const region = textOf(document.getElementById('patients-region'));
  assertContains(region, 'Backend unreachable', 'error state copy missing');
  const banner = textOf(document.getElementById('global-status'));
  assertContains(banner, 'Backend unreachable', 'global banner missing');
  assertContains(banner, 'Retry', 'retry action missing');
});

/* ------------------------------------------------------- patient detail page */

await check('patient detail renders demographics, summary, labs, prescriptions', async () => {
  globalThis.fetch = makeFetch({ patient: PATIENT, summary: SUMMARY, labs: LABS, prescriptions: PRESCRIPTIONS });
  const document = bootDom([...CHROME_IDS, 'page-body', 'patient-heading', 'patient-avatar', 'demographics-region', 'summary-region', 'labs-region', 'prescriptions-region', 'encounters-region']);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  assertContains(textOf(document.getElementById('patient-heading')), 'Ann Test', 'heading not set');
  const demo = textOf(document.getElementById('demographics-region'));
  assertContains(demo, 'MRN-0001', 'MRN missing');
  assertContains(demo, 'Synthetic demo data', 'provenance badge missing');
  assertContains(demo, ui.formatDate('1980-05-04'), 'birth date not formatted');

  const summary = textOf(document.getElementById('summary-region'));
  assertContains(summary, 'Amoxicillin (500 mg, Oral)', 'active medication missing');
  assertContains(summary, 'No active problems recorded.', 'problems empty state missing');

  const encounters = textOf(document.getElementById('encounters-region'));
  assertContains(encounters, 'Routine follow-up (synthetic).', 'encounter missing');
  assertContains(encounters, 'office_visit', 'encounter type badge missing');

  const labs = textOf(document.getElementById('labs-region'));
  assertContains(labs, 'Haemoglobin', 'lab name missing');
  assertContains(labs, '13.5', 'lab value missing');
  assertContains(labs, 'g/dL', 'lab unit missing');
  assertContains(labs, '12.0\u201317.0', 'reference range missing');
  assertContains(labs, ui.formatDate('2026-09-14'), 'collected date missing');

  const rx = textOf(document.getElementById('prescriptions-region'));
  assertContains(rx, 'Amoxicillin', 'medication missing');
  assertContains(rx, '500 mg', 'dose missing');
  assertContains(rx, 'Oral', 'route missing');
  assertContains(rx, 'Three times daily', 'frequency missing');
});

await check('patient detail isolates a 403 lab section from the rest', async () => {
  globalThis.fetch = makeFetch({
    patient: PATIENT,
    summary: SUMMARY,
    labs: { status: 403 },
    prescriptions: PRESCRIPTIONS,
  });
  const document = bootDom([...CHROME_IDS, 'page-body', 'patient-heading', 'patient-avatar', 'demographics-region', 'summary-region', 'labs-region', 'prescriptions-region', 'encounters-region']);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  assertContains(textOf(document.getElementById('labs-region')), 'You do not have permission', '403 copy missing');
  assertContains(textOf(document.getElementById('prescriptions-region')), 'Amoxicillin', 'prescriptions should still render');
});

await check('patient detail shows friendly state without an id', async () => {
  globalThis.fetch = makeFetch({ patient: PATIENT, summary: SUMMARY, labs: LABS, prescriptions: PRESCRIPTIONS });
  const document = bootDom([...CHROME_IDS, 'page-body', 'patient-heading', 'patient-avatar', 'demographics-region', 'summary-region', 'labs-region', 'prescriptions-region', 'encounters-region']);
  globalThis.location.search = '';
  await freshPage('patient.js');
  await settle();
  assertContains(textOf(document.getElementById('page-body')), 'No patient selected', 'missing-id copy');
});

/* --------------------------------------------------------- AI assistant panel */

await check('AI panel renders the answer with AI-generated labelling, tools, and citations', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    ai: AI_ANSWER,
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'What is the latest haemoglobin?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  const askCall = calls.find((call) => call.url.includes('/ai/ask'));
  assert(askCall, 'AI endpoint not called');
  const body = JSON.parse(askCall.init.body);
  assertEqualLocal(body.patient_id, 'p1', 'patient_id missing from the AI request');
  assertEqualLocal(body.question, 'What is the latest haemoglobin?', 'question missing from the AI request');

  const region = textOf(document.getElementById('ai-response-region'));
  assertContains(region, 'The latest synthetic haemoglobin is 13.5 g/dL.', 'answer text missing');
  assertContains(region, 'It is within the recorded reference range.', 'second answer paragraph missing');
  assertContains(region, 'AI-generated', 'AI-generated label missing');
  assertContains(region, 'not a diagnosis or treatment recommendation', 'diagnosis disclaimer missing');
  assertContains(region, AI_ANSWER.disclaimer, 'backend disclaimer missing');
  assertContains(region, 'Sources consulted', 'sources label missing');
  assertContains(region, 'get_patient_lab_results', 'tool chip missing');
  assertContains(region, 'Synthetic lab result DEMO-LAB-0001', 'citation label missing');
  assertContains(region, 'Model: demo-model-1', 'model fine print missing');
});

await check('AI panel shows a calm unavailable state on 503, not a crash', async () => {
  globalThis.fetch = makeFetch({
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    ai: { status: 503, code: 'dependency_unavailable', message: 'The LLM is not configured.' },
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'What is the latest haemoglobin?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  const region = document.getElementById('ai-response-region');
  const text = textOf(region);
  assertContains(text, 'AI assistant unavailable', 'unavailable title missing');
  assertContains(text, 'runs without a language model', 'unavailable explanation missing');
  assertEqualLocal(region.querySelector('.state-panel--error'), null, '503 must not render as an error crash');
  assertEqualLocal(document.getElementById('ai-submit').disabled, false, 'submit must stay usable after 503');
});

await check('AI panel rate limits with a retry countdown on 429', async () => {
  globalThis.fetch = makeFetch({
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    ai: { status: 429, code: 'rate_limited', message: '', retryAfter: 45 },
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'What is the latest haemoglobin?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  const text = textOf(document.getElementById('ai-response-region'));
  assertContains(text, 'Too many questions', 'rate-limit title missing');
  assertContains(text, '45 seconds', 'retry countdown missing');
  assertEqualLocal(document.getElementById('ai-response-region').querySelector('.state-panel--error'), null, '429 must not render as a crash');
});

await check('AI panel enforces the 2000-character question limit', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    ai: AI_ANSWER,
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const input = document.getElementById('ai-question');
  const submit = document.getElementById('ai-submit');
  input.value = 'x'.repeat(2001);
  await input.dispatch('input');
  assertContains(textOf(document.getElementById('ai-char-count')), '2001 / 2000 characters', 'counter must show the length');
  assertEqualLocal(submit.disabled, true, 'submit must disable over the limit');
  assertEqualLocal(input.getAttribute('aria-invalid'), 'true', 'textarea must be marked invalid over the limit');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();
  assertEqualLocal(calls.filter((call) => call.url.includes('/ai/ask')).length, 0, 'no request may be sent over the limit');

  input.value = 'x'.repeat(2000);
  await input.dispatch('input');
  assertEqualLocal(submit.disabled, false, 'submit must enable at exactly 2000 characters');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();
  const askCall = calls.find((call) => call.url.includes('/ai/ask'));
  assert(askCall, 'request missing at exactly 2000 characters');
  assertEqualLocal(JSON.parse(askCall.init.body).question.length, 2000, 'question length wrong');
});

await check('AI panel shows a loading state and supports cancelling the request', async () => {
  const baseFetch = makeFetch({ patient: PATIENT, summary: SUMMARY, labs: LABS, prescriptions: PRESCRIPTIONS });
  globalThis.fetch = async (url, init = {}) => {
    if (url.includes('/ai/ask')) {
      // Pending request that rejects with AbortError when the signal aborts,
      // exactly like a real fetch() in the browser.
      return new Promise((resolve, reject) => {
        if (init.signal) {
          init.signal.addEventListener('abort', () => {
            const error = new Error('The request was aborted.');
            error.name = 'AbortError';
            reject(error);
          });
        }
      });
    }
    return baseFetch(url, init);
  };
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'What is the latest haemoglobin?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  assertContains(textOf(document.getElementById('ai-response-region')), 'Consulting the assistant', 'loading state missing');
  assertEqualLocal(document.getElementById('ai-cancel').hidden, false, 'cancel button must show while loading');

  await document.getElementById('ai-cancel').dispatch('click');
  await settle();
  assertContains(textOf(document.getElementById('ai-response-region')), 'Request cancelled', 'cancel outcome missing');
  assertContains(textOf(document.getElementById('ai-status')), 'Request cancelled', 'cancel not announced');
  assertEqualLocal(document.getElementById('ai-submit').disabled, false, 'submit must re-enable after cancel');
});

await check('AI panel is not interactive without permission to view patients', async () => {
  globalThis.fetch = makeFetch({
    me: CLIN_ADMIN,
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  assertEqualLocal(document.getElementById('ai-question-form').hidden, true, 'question form must hide without patient:read');
  assertContains(
    textOf(document.getElementById('ai-response-region')),
    'only available to accounts that can view this patient',
    'permission note missing',
  );
});

/* --------------------------------------------------------------- admin page */

await check('admin page denies a doctor honestly and shows no fake controls', async () => {
  globalThis.fetch = makeFetch({ me: DOCTOR });
  const document = bootDom([...CHROME_IDS, 'page-body']);
  await freshPage('admin.js');
  await settle();
  const body = textOf(document.getElementById('page-body'));
  assertContains(body, 'You do not have permission', 'denial copy missing');
});

await check('admin staff table renders the Page shape with roles, status, and pagination', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    me: CLIN_ADMIN,
    staff: {
      items: [
        staffUser({ id: 'u-1', display_name: 'Ann Doctor', email: 'ann@example.com', roles: ['doctor'] }),
        staffUser({
          id: 'u-2b',
          display_name: 'Tom Tech',
          email: 'tom@example.com',
          roles: ['clinical_admin', 'tech_admin'],
          is_active: false,
          created_at: '2026-10-20T09:00:00Z',
        }),
      ],
      total: 25,
      limit: 20,
      offset: 0,
    },
    audit: { items: [AUDIT_EVENT], total: 1, limit: 10, offset: 0 },
  });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  const region = textOf(document.getElementById('staff-region'));
  assertContains(region, 'Ann Doctor', 'display name missing');
  assertContains(region, 'ann@example.com', 'email missing');
  assertContains(region, 'Technical administrator', 'role label missing');
  assertContains(region, ui.displayDate('2026-10-20T09:00:00Z'), 'created date missing');
  assertContains(region, 'Showing 1\u20132 of 25 staff accounts', 'pagination summary wrong');
  const badges = badgeTexts(firstRow(document.getElementById('staff-region')));
  assert(badges.includes('Doctor') && badges.includes('Active'), `role/status badges wrong: ${badges}`);
  const secondBadges = badgeTexts(document.getElementById('staff-region').querySelector('tbody').querySelectorAll('tr')[1]);
  assert(secondBadges.includes('Inactive'), `inactive badge missing: ${secondBadges}`);

  const audit = textOf(document.getElementById('audit-region'));
  assertContains(audit, 'role.grant', 'audit action missing');
  assertContains(audit, 'Cal Admin', 'audit actor missing');
  assertContains(audit, 'user_role u-3:doctor', 'audit resource missing');
  assertContains(audit, 'req-77', 'audit request id missing');

  const next = buttonByText(document.getElementById('staff-region'), 'Next');
  assert(next, 'Next button missing');
  await next.dispatch('click');
  await settle();
  const staffCalls = calls.filter((call) => call.url.startsWith('/api/v1/staff'));
  assertContains(staffCalls[staffCalls.length - 1].url, 'offset=20', 'pagination offset missing');
  assertContains(textOf(document.getElementById('site-nav')), 'Admin', 'admin nav missing for clinical_admin');
});

await check('admin role grant confirms the consequence and refreshes the row', async () => {
  const calls = [];
  const base = staffUser({ id: 'u-3', display_name: 'Nina Nurse', email: 'nina@example.com', roles: ['nurse'] });
  globalThis.fetch = makeFetch({
    calls,
    me: CLIN_ADMIN,
    staff: { items: [base], total: 1, limit: 20, offset: 0 },
    routes: [{
      method: 'POST',
      match: /\/staff\/u-3\/roles$/,
      response: { ...base, roles: ['nurse', 'doctor'] },
    }],
  });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  const region = document.getElementById('staff-region');
  const row = firstRow(region);
  const select = row.querySelector('select');
  assert(select, 'role select missing');
  const grantButton = buttonByText(row, 'Grant role');
  assert(grantButton, 'grant button missing');
  assertEqualLocal(grantButton.disabled, true, 'grant button must start disabled');

  select.value = 'doctor';
  await select.dispatch('change');
  assertEqualLocal(grantButton.disabled, false, 'grant button must enable after a selection');

  await grantButton.dispatch('click');
  await settle();
  const dialog = textOf(document.getElementById('app-dialog-body'));
  assertContains(dialog, 'This grants Nina Nurse the ability to view patient records', 'consequence sentence missing');
  await clickButton(document.getElementById('app-dialog-actions'), 'Grant role');
  await settle();

  const roleCall = calls.find((call) => call.url.includes('/roles'));
  assert(roleCall, 'role endpoint not called');
  assertEqualLocal(roleCall.init.body, '{"role":"doctor"}', 'role request body');

  const updatedRow = firstRow(document.getElementById('staff-region'));
  const badges = badgeTexts(updatedRow);
  assert(badges.includes('Nurse') && badges.includes('Doctor'), `row not refreshed: ${badges}`);
  assertContains(textOf(document.getElementById('staff-status')), 'Role granted', 'success not announced');
  assertEqualLocal(document.activeElement && document.activeElement.id, 'staff-status', 'focus must move to the status region');
});

await check('admin role revoke renders the 409 last-admin conflict cleanly', async () => {
  const base = staffUser({ id: 'u-9', display_name: 'Cal Admin', email: 'admin@example.com', roles: ['clinical_admin'] });
  globalThis.fetch = makeFetch({
    me: CLIN_ADMIN,
    staff: { items: [base], total: 1, limit: 20, offset: 0 },
    routes: [{
      method: 'DELETE',
      match: /\/staff\/u-9\/roles\/[^/]+$/,
      response: { status: 409, code: 'conflict', message: 'At least one active administrator must remain.' },
    }],
  });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  const row = firstRow(document.getElementById('staff-region'));
  await clickButton(row, 'Revoke Clinical administrator');
  await settle();
  const dialog = textOf(document.getElementById('app-dialog-body'));
  assertContains(dialog, 'This removes their ability to', 'revoke consequence missing');
  await clickButton(document.getElementById('app-dialog-actions'), 'Revoke role');
  await settle();

  const status = textOf(document.getElementById('staff-status'));
  assertContains(status, 'Action not allowed right now', '409 title missing');
  assertContains(status, 'At least one active administrator must remain.', '409 server message missing');
  assert(badgeTexts(row).includes('Clinical administrator'), 'role badge must remain after a refused revoke');
});

await check('admin self-escalation 403 from the backend is rendered cleanly', async () => {
  const base = staffUser({ id: CLIN_ADMIN.id, display_name: CLIN_ADMIN.display_name, email: CLIN_ADMIN.email, roles: ['clinical_admin'] });
  globalThis.fetch = makeFetch({
    me: CLIN_ADMIN,
    staff: { items: [base], total: 1, limit: 20, offset: 0 },
    routes: [{
      method: 'POST',
      match: /\/staff\/u2\/roles$/,
      response: { status: 403, code: 'forbidden', message: 'Self role assignment is not permitted' },
    }],
  });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  const row = firstRow(document.getElementById('staff-region'));
  const select = row.querySelector('select');
  select.value = 'tech_admin';
  await select.dispatch('change');
  await buttonByText(row, 'Grant role').dispatch('click');
  await settle();
  const dialog = textOf(document.getElementById('app-dialog-body'));
  assertContains(dialog, 'targets your own account', 'self-escalation warning missing');
  await clickButton(document.getElementById('app-dialog-actions'), 'Grant role');
  await settle();

  const status = textOf(document.getElementById('staff-status'));
  assertContains(status, 'You do not have permission', '403 title missing');
  assertContains(status, 'Self role assignment is not permitted', '403 server message missing');
});

await check('admin deactivate confirms session revocation and is reversible', async () => {
  const patches = [];
  const base = staffUser({ id: 'u-5', display_name: 'Eva Example', email: 'eva@example.com', roles: ['nurse'] });
  globalThis.fetch = makeFetch({
    me: CLIN_ADMIN,
    staff: { items: [base], total: 1, limit: 20, offset: 0 },
    routes: [{
      method: 'PATCH',
      match: /\/staff\/u-5$/,
      response: ({ body }) => {
        patches.push(body);
        return { ...base, is_active: body.is_active };
      },
    }],
  });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  const rowNow = () => firstRow(document.getElementById('staff-region'));
  await clickButton(rowNow(), 'Deactivate');
  await settle();
  const dialog = textOf(document.getElementById('app-dialog-body'));
  assertContains(dialog, 'Deactivating immediately ends this person\u0027s sessions.', 'session-revocation warning missing');
  await clickButton(document.getElementById('app-dialog-actions'), 'Deactivate');
  await settle();

  assertEqualLocal(patches.length, 1, 'PATCH not sent');
  assertEqualLocal(patches[0].is_active, false, 'deactivate body wrong');
  assert(badgeTexts(rowNow()).includes('Inactive'), 'inactive badge missing after deactivation');

  await clickButton(rowNow(), 'Activate');
  await settle();
  await clickButton(document.getElementById('app-dialog-actions'), 'Activate');
  await settle();

  assertEqualLocal(patches.length, 2, 'second PATCH not sent');
  assertEqualLocal(patches[1].is_active, true, 'activate body wrong');
  assert(badgeTexts(rowNow()).includes('Active'), 'active badge missing after reactivation');
  assertContains(textOf(document.getElementById('staff-status')), 'can sign in again', 'activation not announced');
});

await check('admin create staff shows the generated password once with a warning', async () => {
  const calls = [];
  const created = [];
  globalThis.fetch = makeFetch({
    calls,
    me: CLIN_ADMIN,
    staff: { items: [], total: 0, limit: 20, offset: 0 },
    routes: [{
      method: 'POST',
      match: /\/staff$/,
      response: ({ body }) => {
        created.push(body);
        return {
          user: staffUser({ id: 'u-new', email: body.email, display_name: body.display_name, roles: [] }),
          generated_password: 'Gen-9x-Pass-1',
        };
      },
    }],
  });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  document.getElementById('create-email').value = 'new@example.com';
  document.getElementById('create-name').value = 'New Person';
  await document.getElementById('create-staff-form').dispatch('submit');
  await settle();

  assertEqualLocal(created.length, 1, 'create request missing');
  assertEqualLocal(created[0].email, 'new@example.com', 'email body wrong');
  assertEqualLocal(created[0].display_name, 'New Person', 'display name body wrong');
  assert(!('password' in created[0]), 'password key must be omitted when the field is blank');
  assert(!('roles' in created[0]), 'roles must never be sent to POST /staff (schema forbids it)');

  const modalBody = document.getElementById('app-dialog-body');
  const modalText = textOf(modalBody);
  assertContains(modalText, 'Gen-9x-Pass-1', 'generated password not displayed');
  assertContains(modalText, 'This is shown once. Store it now.', 'one-time warning missing');
  const copyButton = buttonByText(modalBody, 'Copy password');
  assert(copyButton, 'copy button missing');
  assertContains(textOf(document.getElementById('create-status')), 'Account created', 'creation not announced');

  const copied = [];
  Object.defineProperty(globalThis, 'navigator', {
    value: { clipboard: { writeText: async (text) => { copied.push(text); } } },
    configurable: true,
  });
  await copyButton.dispatch('click');
  await settle();
  assertEqualLocal(copied[0], 'Gen-9x-Pass-1', 'copy button did not copy the password');
  assertContains(textOf(modalBody), 'copied to the clipboard', 'copy feedback missing');

  await clickButton(document.getElementById('app-dialog-actions'), 'Close');
  await settle();
  assert(!textOf(document.body).includes('Gen-9x-Pass-1'), 'password must not linger in the DOM after close');
  const reloads = calls.filter((call) => call.url.startsWith('/api/v1/staff') && (call.init.method || 'GET') === 'GET');
  assert(reloads.length >= 2, 'staff list must reload after creation');
});

await check('admin create staff with a role confirms the grant and assigns it after creation', async () => {
  const created = [];
  const roleCalls = [];
  globalThis.fetch = makeFetch({
    me: CLIN_ADMIN,
    staff: { items: [], total: 0, limit: 20, offset: 0 },
    routes: [
      {
        method: 'POST',
        match: /\/staff$/,
        response: ({ body }) => {
          created.push(body);
          return {
            user: staffUser({ id: 'u-new', email: body.email, display_name: body.display_name }),
            generated_password: null,
          };
        },
      },
      {
        method: 'POST',
        match: /\/staff\/u-new\/roles$/,
        response: ({ body }) => {
          roleCalls.push(body);
          return staffUser({ id: 'u-new', display_name: 'New Person', roles: [body.role] });
        },
      },
    ],
  });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  document.getElementById('create-email').value = 'new@example.com';
  document.getElementById('create-name').value = 'New Person';
  document.getElementById('create-password').value = 'short';
  document.getElementById('create-role').value = 'nurse';
  await document.getElementById('create-staff-form').dispatch('submit');
  await settle();
  assertContains(textOf(document.getElementById('create-password-error')), 'at least 8 characters', 'password length validation missing');
  assertEqualLocal(created.length, 0, 'no request may be sent while the form is invalid');

  document.getElementById('create-password').value = 'long-enough-password';
  await document.getElementById('create-staff-form').dispatch('submit');
  await settle();
  const dialog = textOf(document.getElementById('app-dialog-body'));
  assertContains(dialog, 'This grants New Person the ability to view permitted patient information', 'create+grant consequence missing');
  await clickButton(document.getElementById('app-dialog-actions'), 'Create and grant role');
  await settle();

  assertEqualLocal(created.length, 1, 'create request missing');
  assertEqualLocal(created[0].password, 'long-enough-password', 'password body wrong');
  assertEqualLocal(roleCalls.length, 1, 'role grant request missing');
  assertEqualLocal(roleCalls[0].role, 'nurse', 'role body wrong');
  assertContains(textOf(document.getElementById('create-status')), 'Nurse granted', 'role outcome not announced');
  assert(!textOf(document.getElementById('app-dialog-body')).includes('Generated password'), 'no one-time dialog expected for a supplied password');
});

await check('admin renders a read-only staff table for a nurse without unmaintainable controls', async () => {
  globalThis.fetch = makeFetch({
    me: NURSE,
    staff: {
      items: [staffUser({ id: 'u-1', display_name: 'Ann Doctor', roles: ['doctor'] })],
      total: 1,
      limit: 20,
      offset: 0,
    },
  });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  assertContains(textOf(document.getElementById('staff-region')), 'Ann Doctor', 'staff row missing');
  assertEqualLocal(document.getElementById('create-staff-section').hidden, true, 'create form must hide without staff:manage');
  assertEqualLocal(document.getElementById('audit-section').hidden, true, 'audit section must hide without audit:read');
  const region = textOf(document.getElementById('staff-region'));
  assert(!region.includes('Grant role'), 'grant control must not render without role:assign');
  assert(!region.includes('Deactivate'), 'deactivate control must not render without staff:manage');
});

/* ------------------------------------------- dashboard + patient detail UI */

await check('dashboard renders welcome, role badges, quick actions, and recent patient cards', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    patients: {
      items: [
        PATIENT,
        { id: 'p2', display_name: 'Bob Test', mrn: 'MRN-0002', date_of_birth: '1975-01-20', sex: 'M', provenance: 'synthetic' },
      ],
      total: 2,
      limit: 8,
      offset: 0,
    },
  });
  const document = bootDom([...CHROME_IDS, 'page-body', 'welcome-heading']);
  await freshPage('dashboard.js');
  await settle();

  assertContains(textOf(document.getElementById('welcome-heading')), 'Welcome back, Dr Ada Example', 'welcome heading missing');
  const body = textOf(document.getElementById('page-body'));
  assertContains(body, 'Quick actions', 'quick actions heading missing');
  assertContains(body, 'Recent patients', 'recent patients heading missing');
  assertContains(body, 'MRN-0001', 'recent patient card missing');
  assertContains(body, 'MRN-0002', 'second patient card missing');
  assertContains(body, 'Synthetic demo data', 'provenance badge missing');
  assertContains(body, 'Doctor', 'role badge missing');
  assertContains(body, 'Synthetic demo data \u2014 not for clinical use', 'demo disclosure card missing');

  const hrefs = document.getElementById('page-body').querySelectorAll('a').map((a) => a.getAttribute('href'));
  assert(hrefs.includes('patient.html?id=p1'), 'patient card link missing');
  assert(hrefs.includes('patients.html'), 'quick action link to patients missing');
  const patientCalls = calls.filter((call) => call.url.includes('/api/v1/patients'));
  assertEqualLocal(patientCalls.length, 1, 'exactly one recent-patients fetch expected');
  assertContains(patientCalls[0].url, 'limit=8', 'recent patients fetch must be bounded');
});

await check('dashboard hides patient content without patient:read and keeps admin access', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    me: CLIN_ADMIN,
    patients: { items: [PATIENT], total: 1, limit: 8, offset: 0 },
  });
  const document = bootDom([...CHROME_IDS, 'page-body', 'welcome-heading']);
  await freshPage('dashboard.js');
  await settle();

  const body = textOf(document.getElementById('page-body'));
  assert(!body.includes('Recent patients'), 'recent patients must not render without patient:read');
  assert(!body.includes('MRN-0001'), 'no patient data may render without patient:read');
  assertContains(body, 'Staff management', 'admin quick action missing');
  assertEqualLocal(
    calls.filter((call) => call.url.includes('/api/v1/patients')).length,
    0,
    'no patient fetch may happen without patient:read',
  );
});

await check('patient header renders the avatar initials and copies the MRN', async () => {
  globalThis.fetch = makeFetch({ patient: PATIENT, summary: SUMMARY, labs: LABS, prescriptions: PRESCRIPTIONS });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const demo = textOf(document.getElementById('demographics-region'));
  assertContains(demo, 'Synthetic demo data', 'provenance badge missing');
  assertContains(demo, 'MRN: MRN-0001', 'MRN missing');
  assertContains(demo, ui.formatDate('1980-05-04'), 'birth date not shown in the header meta line');
  assertEqualLocal(textOf(document.getElementById('patient-avatar')), 'AT', 'avatar initials wrong');
  assertContains(textOf(document.getElementById('patient-heading')), 'Ann Test', 'heading not set');

  const copied = [];
  Object.defineProperty(globalThis, 'navigator', {
    value: { clipboard: { writeText: async (text) => { copied.push(text); } } },
    configurable: true,
  });
  await clickButton(document.getElementById('demographics-region'), 'Copy MRN');
  await settle();
  assertEqualLocal(copied[0], 'MRN-0001', 'copy button did not copy the MRN');
  assertContains(textOf(document.getElementById('demographics-region')), 'copied to the clipboard', 'copy feedback missing');
});

await check('patient record tabs switch panels with the ARIA tabs pattern', async () => {
  globalThis.fetch = makeFetch({ patient: PATIENT, summary: SUMMARY, labs: LABS, prescriptions: PRESCRIPTIONS });
  const document = bootDom([
    ...CHROME_IDS,
    ...PATIENT_IDS,
    {
      id: 'patient-tabs',
      class: 'tabs',
      attrs: { role: 'tablist' },
      children: [
        {
          id: 'tab-summary',
          tag: 'button',
          class: 'tab is-active',
          attrs: { role: 'tab', 'aria-controls': 'panel-summary', 'aria-selected': 'true' },
          text: 'Summary',
        },
        {
          id: 'tab-labs',
          tag: 'button',
          class: 'tab',
          attrs: { role: 'tab', 'aria-controls': 'panel-labs', 'aria-selected': 'false', tabindex: '-1' },
          text: 'Labs',
        },
      ],
    },
    { id: 'panel-summary', class: 'tab-panel', attrs: { role: 'tabpanel' } },
    { id: 'panel-labs', class: 'tab-panel', attrs: { role: 'tabpanel', hidden: '' } },
  ]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const summaryPanel = document.getElementById('panel-summary');
  const labsPanel = document.getElementById('panel-labs');
  assertEqualLocal(summaryPanel.hidden, false, 'summary panel must start visible');
  assertEqualLocal(labsPanel.hidden, true, 'labs panel must start hidden');

  await document.getElementById('tab-labs').dispatch('click');
  assertEqualLocal(labsPanel.hidden, false, 'labs panel must show after clicking its tab');
  assertEqualLocal(summaryPanel.hidden, true, 'summary panel must hide after switching');
  assertEqualLocal(document.getElementById('tab-labs').getAttribute('aria-selected'), 'true', 'aria-selected not set on the new tab');
  assertEqualLocal(document.getElementById('tab-summary').getAttribute('aria-selected'), 'false', 'aria-selected not cleared on the old tab');

  await document.getElementById('tab-labs').dispatch('keydown', { key: 'ArrowLeft' });
  assertEqualLocal(summaryPanel.hidden, false, 'arrow key must move selection back to the summary tab');
  assertEqualLocal(labsPanel.hidden, true, 'labs panel must hide after arrow-key navigation');
});

/* ------------------------------------------------------------- login + session */

await check('login maps 401 to the uniform message and preserves input', async () => {
  globalThis.fetch = async (url) => {
    if (url.endsWith('/auth/me')) return json(401, { error: { code: 'unauthenticated', message: '' } });
    return json(401, { error: { code: 'unauthenticated', message: 'no' } });
  };
  const document = bootDom(['global-status', 'login-form', 'email', 'password', 'login-submit', 'login-status', 'email-error', 'password-error', 'login-card', 'signed-in-card']);
  const email = document.getElementById('email');
  const password = document.getElementById('password');
  email.value = 'doctor@example.com';
  password.value = 'wrong-password';
  await freshPage('login.js');
  await settle();
  await document.getElementById('login-form').dispatch('submit');
  await settle();

  assertContains(textOf(document.getElementById('login-status')), 'Invalid email or password.', 'uniform 401 copy missing');
  assertEqualLocal(email.value, 'doctor@example.com', 'email input was not preserved');
  assertEqualLocal(password.value, 'wrong-password', 'password input was not preserved');
});

await check('login validates fields inline before calling the API', async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return json(401, { error: { code: 'unauthenticated', message: '' } });
  };
  const document = bootDom(['global-status', 'login-form', 'email', 'password', 'login-submit', 'login-status', 'email-error', 'password-error', 'login-card', 'signed-in-card']);
  await freshPage('login.js');
  await settle();
  await document.getElementById('login-form').dispatch('submit');
  await settle();

  assertContains(textOf(document.getElementById('email-error')), 'Enter your email address.', 'email validation copy');
  assertContains(textOf(document.getElementById('password-error')), 'Enter your password.', 'password validation copy');
  assertEqualLocal(document.getElementById('email').getAttribute('aria-invalid'), 'true', 'aria-invalid not set');
  const loginCalls = calls.filter((call) => call.url.endsWith('/auth/login'));
  assertEqualLocal(loginCalls.length, 0, 'login API must not be called when validation fails');
});

await check('successful login redirects to the dashboard', async () => {
  globalThis.fetch = async (url) => {
    if (url.endsWith('/auth/me')) return json(401, { error: { code: 'unauthenticated', message: '' } });
    return json(200, DOCTOR);
  };
  const document = bootDom(['global-status', 'login-form', 'email', 'password', 'login-submit', 'login-status', 'email-error', 'password-error', 'login-card', 'signed-in-card']);
  document.getElementById('email').value = 'doctor@example.com';
  document.getElementById('password').value = 'correct-password';
  globalThis.location.redirectedTo = null;
  await freshPage('login.js');
  await settle();
  await document.getElementById('login-form').dispatch('submit');
  await settle();
  assertEqualLocal(globalThis.location.redirectedTo, 'dashboard.html', 'no redirect after login');
});

await check('anonymous access to an authenticated page redirects to sign-in', async () => {
  globalThis.fetch = async () => json(401, { error: { code: 'unauthenticated', message: '' } });
  bootDom([...CHROME_IDS, 'page-body']);
  globalThis.location.redirectedTo = null;
  await freshPage('dashboard.js');
  await settle();
  assertEqualLocal(globalThis.location.redirectedTo, 'index.html', 'no redirect on anonymous');
});

function assertEqualLocal(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`);
  }
}

/* ------------------------------------------------------------------ report */

const failed = results.filter((r) => !r.ok);
for (const result of results) {
  process.stdout.write(`${result.ok ? 'PASS' : 'FAIL'} ${result.name}${result.ok ? '' : `: ${result.detail}`}\n`);
}
process.stdout.write(`\n${results.length - failed.length}/${results.length} render-path scenarios passed\n`);
if (failed.length > 0) process.exitCode = 1;
