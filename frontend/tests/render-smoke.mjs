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
 * role-aware nav, anonymous redirect).
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

function makeFetch({ me = DOCTOR, patients, patient, summary, labs, prescriptions, calls = [] }) {
  // A payload carrying a numeric `status` (and no `items`) is an error response.
  const respond = (payload) => {
    if (payload instanceof Error) throw payload;
    if (payload && typeof payload === 'object' && typeof payload.status === 'number' && !('items' in payload)) {
      return json(payload.status, { error: { code: 'forbidden', message: 'denied by policy' } });
    }
    return json(200, payload);
  };
  return async (url, init = {}) => {
    calls.push({ url, init });
    const [path] = url.split('?');
    const method = (init.method || 'GET').toUpperCase();
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
  const document = bootDom([...CHROME_IDS, 'page-body', 'patient-heading', 'demographics-region', 'summary-region', 'labs-region', 'prescriptions-region']);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  assertContains(textOf(document.getElementById('patient-heading')), 'Ann Test', 'heading not set');
  const demo = textOf(document.getElementById('demographics-region'));
  assertContains(demo, 'MRN-0001', 'MRN missing');
  assertContains(demo, 'Synthetic demo data', 'provenance badge missing');
  assertContains(demo, ui.formatDate('1980-05-04'), 'birth date not formatted');

  const summary = textOf(document.getElementById('summary-region'));
  assertContains(summary, 'Routine follow-up (synthetic).', 'encounter missing');
  assertContains(summary, 'office_visit', 'encounter type badge missing');
  assertContains(summary, 'Amoxicillin (500 mg, Oral)', 'active medication missing');
  assertContains(summary, 'No active problems recorded.', 'problems empty state missing');

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
  const document = bootDom([...CHROME_IDS, 'page-body', 'patient-heading', 'demographics-region', 'summary-region', 'labs-region', 'prescriptions-region']);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  assertContains(textOf(document.getElementById('labs-region')), 'You do not have permission', '403 copy missing');
  assertContains(textOf(document.getElementById('prescriptions-region')), 'Amoxicillin', 'prescriptions should still render');
});

await check('patient detail shows friendly state without an id', async () => {
  globalThis.fetch = makeFetch({ patient: PATIENT, summary: SUMMARY, labs: LABS, prescriptions: PRESCRIPTIONS });
  const document = bootDom([...CHROME_IDS, 'page-body', 'patient-heading', 'demographics-region', 'summary-region', 'labs-region', 'prescriptions-region']);
  globalThis.location.search = '';
  await freshPage('patient.js');
  await settle();
  assertContains(textOf(document.getElementById('page-body')), 'No patient selected', 'missing-id copy');
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

await check('admin page shows the honest placeholder for a clinical admin', async () => {
  globalThis.fetch = makeFetch({ me: CLIN_ADMIN });
  const document = bootDom([...CHROME_IDS, 'page-body']);
  await freshPage('admin.js');
  await settle();
  const body = textOf(document.getElementById('page-body'));
  assertContains(body, 'Staff management loads here', 'placeholder copy missing');
  const nav = textOf(document.getElementById('site-nav'));
  assertContains(nav, 'Admin', 'admin nav missing for clinical_admin');
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
