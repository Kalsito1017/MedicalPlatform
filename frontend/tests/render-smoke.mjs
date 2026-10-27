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
 * gating), plus the wave UI patterns: shimmer skeletons replaced atomically by
 * content, stale-while-revalidate (cache hit renders instantly, background
 * refetch updates the DOM only on change), the ⌘K command menu (open, filter,
 * run, Esc + focus restore, bounded patient entries, real shortcut list),
 * optimistic staff mutations with rollback and error toasts, and undo toasts
 * whose Undo action runs the inverse API call, plus the wave UI-B patterns:
 * blur placeholders swapping to sharp content on the ready class, the
 * view-transition helper (usage + instant fallback), swipe-to-dismiss sheets
 * (below-threshold keeps the sheet, above-threshold dismisses WITHOUT
 * confirming), interruptible-animation class re-targeting under rapid
 * toggling, predictive back (LIFO overlay unwinding via popstate/history
 * simulation), idle module warm-up with modulepreload hints, hover-intent
 * patient prefetch (permission-gated, in-memory), and idempotency keys on
 * mutating requests, and the wave UI-C responsive contract: the command
 * palette's arrow-follow scroll ({block:'nearest'} on the NEW active option),
 * wrap-safe long synthetic names in patient cards and the patient header,
 * and the narrow-width structure contract (card-stacked tables, labelled
 * keyboard-focusable scroll regions — the shim has no layout, real layout at
 * 320–430px is browser-verified).
 * What this does NOT verify: real browser DOM, layout/CSS, focus behaviour,
 * screen-reader output, and live backend integration.
 * ------------------------------------------------------------------------- */

import { installDom, fixture, textOf, settle, dispatchWindowEvent, scrollIntoViewCalls } from './domshim.mjs';

const JS = new URL('../assets/js/', import.meta.url).href;
// Shared module for date expectations: Intl output varies slightly across ICU
// versions (e.g. "Sept" vs "Sep"), so assertions compare against ui.formatDate.
const ui = await import(JS + 'ui.js');
// The in-memory response cache survives across module instances; every scenario
// boots a fresh document (= a fresh page load), so it starts cold exactly like
// a real navigation. The SWR scenario loads twice within one scenario.
const cache = await import(JS + 'cache.js');
// Wave UI-B pattern modules (shared instances with the page modules).
const prefetch = await import(JS + 'prefetch.js');
const toast = await import(JS + 'toast.js');
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
  permissions: [
    'patient:read',
    'lab:read',
    'prescription:read',
    'staff:read',
    'staff:manage',
    'role:assign',
    'audit:read',
    'analytics:read',
  ],
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
  permissions: ['patient:read', 'lab:read', 'prescription:read'],
  is_active: true,
};

// Permission-driven UI gate fixture: no real role lacks patient:read in the
// three-role model (admin gained clinical reads), so the "no patient access"
// gates are exercised with an explicit permission set, not a role.
const NO_PATIENT_READ = {
  id: 'u8',
  email: 'ops@example.com',
  display_name: 'Oli Ops',
  roles: [],
  permissions: ['staff:read', 'staff:manage', 'role:assign', 'audit:read'],
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

const AI_GENERAL_ANSWER = {
  answer: 'One synthetic patient is on metformin.\n\nThe record consulted is listed below.',
  used_tools: ['search_authorized_patients', 'get_recent_prescriptions'],
  citations: [{ type: 'patient', id: 'p1' }, { type: 'prescription', id: 'rx1' }],
  model: 'demo-model-1',
  ai_generated: true,
  disclaimer: 'AI-generated synthetic-demo assistance; not a diagnosis or treatment recommendation.',
};

// A doctor-scannable Markdown answer as the prompt steers it: bold summary,
// a pipe table with a header separator row, bolded numeric results/flags,
// and a trailing "### Records consulted" section (Bulgarian answer text —
// the backend steers answers to Bulgarian by default).
const AI_MARKDOWN_ANSWER = {
  answer: [
    '**Обобщение:** 2-ма пациенти с отклонения в калия (K) — и двамата с повишени стойности.',
    '',
    '| Пациент | Тест | Стойност | Флаг |',
    '|---|---|---|---|',
    '| 5340c8c2-… | K | **6.1 mmol/L** | **високо ↑** |',
    '| 9f2c11aa-… | K | **5.9 mmol/L** | **високо ↑** |',
    '',
    '### Records consulted',
    '',
    '- 5340c8c2-…',
    '- 9f2c11aa-…',
  ].join('\n'),
  used_tools: ['find_patients_by_lab'],
  citations: [{ type: 'patient', id: 'p1' }],
  model: 'demo-model-1',
  ai_generated: true,
  disclaimer: 'AI-generated synthetic-demo assistance; not a diagnosis or treatment recommendation.',
};

/* Conversation history fixtures (docs/API.md "AI assistant" history). */
const minutesAgo = (n) => new Date(Date.now() - n * 60 * 1000).toISOString();
const CONVERSATIONS = {
  items: [
    {
      id: 'c1',
      patient_id: 'p1',
      question: 'What is the latest haemoglobin?',
      answer: 'The latest synthetic haemoglobin is 13.5 g/dL.\n\nIt is within the recorded reference range.',
      used_tools: ['get_patient_lab_results'],
      citations: [{ type: 'patient', id: 'p1' }, { type: 'lab_result', id: 'DEMO-LAB-0001' }],
      model: 'demo-model-1',
      created_at: minutesAgo(5),
    },
    {
      id: 'c2',
      patient_id: null,
      question: 'Which patients are on metformin?',
      answer: 'One synthetic patient is on metformin.',
      used_tools: ['find_patients_by_medication'],
      citations: [],
      model: 'demo-model-1',
      created_at: minutesAgo(90),
    },
  ],
  total: 2,
  limit: 20,
  offset: 0,
};
const EMPTY_CONVERSATIONS = { items: [], total: 0, limit: 20, offset: 0 };

/* -------------------------------------------- analytics fixtures (wave U) */

// Mirrors the documented GET /analytics/summary payload exactly
// (docs/API.md "Analytics"): ints everywhere, bounded lists, synthetic label.
const ANALYTICS_SUMMARY = {
  generated_at: '2026-10-27T09:30:00Z',
  counts: {
    patients: 45,
    encounters: 167,
    labs: 415,
    prescriptions: 104,
    active_prescriptions: 62,
    diagnoses_active: 90,
    staff_active: 3,
    ai_conversations: 12,
  },
  last_30_days: {
    encounters: 24,
    lab_results: 58,
    prescriptions_written: 9,
    ai_asks: 12,
    logins: 31,
    permission_denials: 4,
  },
  lab_status_mix: { final: 380, preliminary: 25, pending: 10 },
  abnormal_lab_rate: { abnormal: 121, total: 402, pct: 30.1 },
  top_abnormal_tests: [
    { test_code: 'GLU', test_name: 'Glucose, fasting', abnormal_count: 12 },
    { test_code: 'HBA1C', test_name: 'HbA1c', abnormal_count: 9 },
    { test_code: 'K', test_name: 'Potassium', abnormal_count: 7 },
    { test_code: 'HGB', test_name: 'Hemoglobin', abnormal_count: 5 },
    { test_code: 'LDL', test_name: 'LDL cholesterol', abnormal_count: 4 },
    { test_code: 'TSH', test_name: 'TSH', abnormal_count: 3 },
  ],
  prescriptions_by_status: { active: 62, completed: 22, stopped: 12, cancelled: 8 },
  staff_by_role: [
    { role: 'doctor', count: 2 },
    { role: 'nurse', count: 2 },
    { role: 'clinical_admin', count: 1 },
  ],
  audit_activity: [
    { day: '2026-10-14', logins: 3, denials: 0, record_reads: 10 },
    { day: '2026-10-15', logins: 2, denials: 1, record_reads: 8 },
    { day: '2026-10-16', logins: 0, denials: 0, record_reads: 0 },
    { day: '2026-10-17', logins: 4, denials: 0, record_reads: 12 },
    { day: '2026-10-18', logins: 1, denials: 0, record_reads: 6 },
    { day: '2026-10-19', logins: 0, denials: 0, record_reads: 2 },
    { day: '2026-10-20', logins: 5, denials: 2, record_reads: 14 },
    { day: '2026-10-21', logins: 3, denials: 0, record_reads: 9 },
    { day: '2026-10-22', logins: 2, denials: 0, record_reads: 7 },
    { day: '2026-10-23', logins: 0, denials: 0, record_reads: 0 },
    { day: '2026-10-24', logins: 4, denials: 1, record_reads: 11 },
    { day: '2026-10-25', logins: 1, denials: 0, record_reads: 5 },
    { day: '2026-10-26', logins: 3, denials: 0, record_reads: 8 },
    { day: '2026-10-27', logins: 3, denials: 0, record_reads: 6 },
  ],
  ai_usage: {
    total_asks: 12,
    general_asks: 5,
    patient_asks: 7,
    top_tools: [
      { tool: 'get_patient_summary', calls: 9 },
      { tool: 'get_patient_lab_results', calls: 7 },
      { tool: 'find_patients_by_medication', calls: 4 },
    ],
  },
  data_provenance: 'synthetic',
};

const ANALYTICS_IDS = [
  'page-body',
  'analytics-loading',
  'analytics-generated-at',
  'analytics-refresh',
  'analytics-status',
  'kpi-region',
  'audit-activity-region',
  'labs-region',
  'prescriptions-region',
  'staff-region',
  'ai-region',
];

const AI_PAGE_IDS = [
  'page-body',
  'ai-suggestions',
  'ai-response-region',
  'ai-question-form',
  'ai-question',
  'ai-char-count',
  'ai-question-error',
  'ai-submit',
  'ai-cancel',
  'ai-status',
  'ai-patient-link',
  'ai-empty-note',
  'ai-history',
  'ai-history-list',
  'ai-history-clear',
];

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
  'ai-quick-actions',
  'ai-quick-note',
  'ai-history',
  'ai-history-list',
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

function makeFetch({ me = DOCTOR, patients, patient, summary, labs, prescriptions, staff, audit, ai, conversations, analytics, routes = [], calls = [] }) {
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
    if (path.endsWith('/ai/conversations') && method === 'GET') {
      return respond(conversations === undefined ? EMPTY_CONVERSATIONS : conversations);
    }
    if (path.endsWith('/ai/conversations') && method === 'DELETE') {
      return json(204, null);
    }
    if (/\/ai\/conversations\/[^/]+$/.test(path) && method === 'DELETE') {
      return json(204, null);
    }
    if (path.endsWith('/analytics/summary') && method === 'GET') {
      if (analytics === undefined) throw new Error(`unstubbed request: ${method} ${url}`);
      return respond(analytics);
    }
    throw new Error(`unstubbed request: ${method} ${url}`);
  };
}

async function freshPage(moduleName) {
  scenarioId += 1;
  return import(`${JS}${moduleName}?scenario=${scenarioId}`);
}

function bootDom(ids) {
  cache.clearCache();
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
    me: NO_PATIENT_READ,
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

/* ------------------------------------------------- AI Assistant page (general) */

await check('AI page sends a general question and renders bubbles, chips, and linked citations', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({ calls, me: DOCTOR, ai: AI_GENERAL_ANSWER });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  assertContains(textOf(document.getElementById('site-nav')), 'AI Assistant', 'nav item missing for patient:read');

  // Suggested questions are plain shortcuts that fill the composer.
  await clickButton(document.getElementById('ai-suggestions'), 'Which patients are on metformin?');
  const input = document.getElementById('ai-question');
  assertEqualLocal(input.value, 'Which patients are on metformin?', 'suggestion did not fill the composer');

  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  const askCall = calls.find((call) => call.url.includes('/ai/ask'));
  assert(askCall, 'AI endpoint not called');
  const body = JSON.parse(askCall.init.body);
  assert(!('patient_id' in body), 'general lookup must not send patient_id');
  assertEqualLocal(body.question, 'Which patients are on metformin?', 'question missing from the request');

  const region = textOf(document.getElementById('ai-response-region'));
  assertContains(region, 'Which patients are on metformin?', 'user question bubble missing');
  assertContains(region, 'One synthetic patient is on metformin.', 'answer text missing');
  assertContains(region, 'AI-generated', 'AI-generated label missing');
  assertContains(region, 'not a diagnosis or treatment recommendation', 'diagnosis disclaimer missing');
  assertContains(region, 'Sources consulted', 'sources label missing');
  assertContains(region, 'search_authorized_patients', 'tool chip missing');
  assertContains(region, 'Records consulted', 'citations label missing');
  assertContains(region, 'Synthetic patient p1', 'patient citation label missing');
  assertContains(region, 'Synthetic prescription rx1', 'prescription citation label missing');
  assertContains(region, 'Model: demo-model-1', 'model fine print missing');

  const hrefs = document.getElementById('ai-response-region').querySelectorAll('a').map((a) => a.getAttribute('href'));
  assert(hrefs.includes('patient.html?id=p1'), 'patient citation link missing');
});

await check('AI page keeps record citations plain when the patient is ambiguous', async () => {
  globalThis.fetch = makeFetch({
    me: DOCTOR,
    ai: {
      ...AI_GENERAL_ANSWER,
      citations: [
        { type: 'patient', id: 'p1' },
        { type: 'patient', id: 'p2' },
        { type: 'lab_result', id: 'DEMO-LAB-0001' },
      ],
    },
  });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'Which patients have abnormal potassium results?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  const region = document.getElementById('ai-response-region');
  const hrefs = region.querySelectorAll('a').map((a) => a.getAttribute('href'));
  assert(hrefs.includes('patient.html?id=p1'), 'patient citations must link');
  assert(hrefs.includes('patient.html?id=p2'), 'patient citations must link');
  assert(!hrefs.some((href) => href && href.startsWith('patient.html') && !href.includes('p1') && !href.includes('p2')),
    'record citations must not invent a patient link');
  assertContains(textOf(region), 'Synthetic lab result DEMO-LAB-0001', 'record reference must still render');
});

await check('AI page shows a calm unavailable state on 503, not a crash', async () => {
  globalThis.fetch = makeFetch({
    me: DOCTOR,
    ai: { status: 503, code: 'dependency_unavailable', message: 'The LLM is not configured.' },
  });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'Who is on metformin?';
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

await check('AI page rate limits with a retry countdown on 429', async () => {
  globalThis.fetch = makeFetch({
    me: DOCTOR,
    ai: { status: 429, code: 'rate_limited', message: '', retryAfter: 45 },
  });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'Who is on metformin?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  const region = document.getElementById('ai-response-region');
  const text = textOf(region);
  assertContains(text, 'Too many questions', 'rate-limit title missing');
  assertContains(text, '45 seconds', 'retry countdown missing');
  assertEqualLocal(region.querySelector('.state-panel--error'), null, '429 must not render as a crash');
});

await check('AI page shows a loading state and supports cancelling the request', async () => {
  const baseFetch = makeFetch({ me: DOCTOR });
  globalThis.fetch = async (url, init = {}) => {
    if (url.includes('/ai/ask')) {
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
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'Which synthetic patients have the most recent lab results?';
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

await check('AI page network failure renders the offline banner with retry', async () => {
  globalThis.fetch = makeFetch({ me: DOCTOR, ai: new TypeError('Failed to fetch') });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'Who is on metformin?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  assertContains(textOf(document.getElementById('ai-response-region')), 'Backend unreachable', 'error state copy missing');
  const banner = textOf(document.getElementById('global-status'));
  assertContains(banner, 'Backend unreachable', 'global banner missing');
  assertContains(banner, 'Retry', 'retry action missing');
});

await check('AI page is gated without patient:read: nav hidden and denial shown', async () => {
  globalThis.fetch = makeFetch({ me: NO_PATIENT_READ });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const nav = textOf(document.getElementById('site-nav'));
  assert(!nav.includes('AI Assistant'), 'nav item must not render without patient:read');
  assertEqualLocal(document.getElementById('ai-question-form').hidden, true, 'composer must hide without patient:read');
  assertEqualLocal(document.getElementById('ai-suggestions').hidden, true, 'suggestions must hide without patient:read');
  assertContains(
    textOf(document.getElementById('ai-response-region')),
    'only available to accounts that can view patient data',
    'permission denial note missing',
  );
});

/* ------------------------------- AI composer keys + conversation history */

await check('AI page composer sends on Enter and inserts a new line on Shift+Enter', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({ calls, me: DOCTOR, ai: AI_GENERAL_ANSWER });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'Which patients are on metformin?';
  await input.dispatch('input');

  // Shift+Enter: the default browser behaviour (a new line) must survive.
  const shiftEvent = {
    key: 'Enter',
    shiftKey: true,
    prevented: false,
    preventDefault() { this.prevented = true; },
  };
  await input.dispatch('keydown', shiftEvent);
  await settle();
  assertEqualLocal(shiftEvent.prevented, false, 'Shift+Enter must not be intercepted');
  assertEqualLocal(calls.filter((call) => call.url.includes('/ai/ask')).length, 0, 'Shift+Enter must not send');

  // Enter: intercepted and sends exactly once.
  const enterEvent = {
    key: 'Enter',
    shiftKey: false,
    prevented: false,
    preventDefault() { this.prevented = true; },
  };
  await input.dispatch('keydown', enterEvent);
  await settle();
  assertEqualLocal(enterEvent.prevented, true, 'Enter must be intercepted before a newline is typed');
  const askCalls = calls.filter((call) => call.url.includes('/ai/ask'));
  assertEqualLocal(askCalls.length, 1, 'Enter must send exactly one question');
  assertEqualLocal(JSON.parse(askCalls[0].init.body).question, 'Which patients are on metformin?', 'sent question');
  assertContains(textOf(document.getElementById('ai-response-region')), 'One synthetic patient is on metformin.', 'answer missing');
});

await check('AI panel send button sends and compose is disabled while in flight', async () => {
  const calls = [];
  const baseFetch = makeFetch({
    calls,
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
  });
  let resolveAsk = null;
  globalThis.fetch = async (url, init = {}) => {
    if (url.includes('/ai/ask')) {
      calls.push({ url, init });
      return new Promise((resolve) => {
        resolveAsk = resolve;
      });
    }
    return baseFetch(url, init);
  };
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const input = document.getElementById('ai-question');
  const send = document.getElementById('ai-submit');
  input.value = 'What is the latest haemoglobin?';
  await input.dispatch('input');
  await send.dispatch('click');
  await settle();

  assertEqualLocal(calls.filter((call) => call.url.includes('/ai/ask')).length, 1, 'the send button must ask once');
  assertEqualLocal(input.disabled, true, 'compose must be disabled while the request is in flight');
  assertEqualLocal(send.disabled, true, 'the send button must disable while the request is in flight');

  resolveAsk(json(200, AI_ANSWER));
  await settle();
  assertContains(textOf(document.getElementById('ai-response-region')), '13.5 g/dL', 'answer missing after the send button');
  assertEqualLocal(input.disabled, false, 'compose must re-enable when the request finishes');
  assertEqualLocal(send.disabled, false, 'the send button must re-enable when the request finishes');
});

await check('AI page history renders, expands the full answer, and clears behind a confirm dialog', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({ calls, me: DOCTOR, conversations: CONVERSATIONS });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const list = document.getElementById('ai-history-list');
  const entries = list.querySelectorAll('.ai-history__item');
  assertEqualLocal(entries.length, 2, 'both history entries must render, newest first');
  const summaryText = textOf(list);
  assertContains(summaryText, 'What is the latest haemoglobin?', 'newest question missing');
  assertContains(summaryText, 'Which patients are on metformin?', 'older question missing');
  assertContains(summaryText, 'Patient', 'patient mode badge missing');
  assertContains(summaryText, 'General', 'general mode badge missing');
  assertContains(summaryText, 'minutes ago', 'relative timestamp missing');
  assertContains(summaryText, 'get_patient_lab_results', 'tool name missing from the summary');

  // Expand: aria-expanded, the full styled answer turn, citations, focus.
  const firstSummary = list.querySelectorAll('.ai-history__summary')[0];
  await firstSummary.dispatch('click');
  const detail = list.querySelector('.ai-history__detail');
  assertEqualLocal(firstSummary.getAttribute('aria-expanded'), 'true', 'summary must report expanded');
  assertEqualLocal(detail.hidden, false, 'the answer detail must be visible when expanded');
  const detailText = textOf(detail);
  assertContains(detailText, 'The latest synthetic haemoglobin is 13.5 g/dL.', 'expanded answer missing');
  assertContains(detailText, 'AI-generated', 'expanded turn must keep the AI-generated label');
  assertContains(detailText, 'not a diagnosis or treatment recommendation', 'expanded turn must keep the disclaimer');
  assertContains(detailText, 'Records consulted', 'expanded citations label missing');
  assertContains(detailText, 'Synthetic lab result DEMO-LAB-0001', 'expanded citation missing');
  assert(document.activeElement === detail, 'expanding must move focus into the answer');
  assert(detail.querySelectorAll('a').map((a) => a.getAttribute('href')).includes('patient.html?id=p1'),
    'patient citations must link to the record');

  // Collapse again.
  await firstSummary.dispatch('click');
  assertEqualLocal(firstSummary.getAttribute('aria-expanded'), 'false', 'summary must report collapsed');
  assertEqualLocal(detail.hidden, true, 'the answer detail must hide when collapsed');

  // Clear history: confirmed behind the dialog, DELETEs, then an empty state.
  await document.getElementById('ai-history-clear').dispatch('click');
  const dialog = document.getElementById('app-dialog');
  assert(dialog, 'the confirm dialog must open');
  await clickButton(dialog, 'Clear history');
  await settle();
  assert(calls.some((call) => call.url.endsWith('/ai/conversations') && (call.init.method || 'GET') === 'DELETE'),
    'clearing must DELETE the caller history');
  assertContains(textOf(list), 'No questions yet.', 'empty history note missing after clearing');
  assertContains(textOf(document.getElementById('ai-status')), 'cleared', 'clearing must be announced');
});

await check('AI panel history is filtered to the patient and grows without a reload', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    conversations: CONVERSATIONS,
    ai: AI_ANSWER,
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  // The rail history request carries the patient filter (never a full dump).
  const historyCall = calls.find((call) => call.url.includes('/ai/conversations'));
  assert(historyCall, 'history endpoint not called');
  assert(historyCall.url.includes('patient_id=p1'), `history must filter by patient: ${historyCall.url}`);

  const list = document.getElementById('ai-history-list');
  assertContains(textOf(list), 'What is the latest haemoglobin?', 'past patient question missing');

  const input = document.getElementById('ai-question');
  input.value = 'What changed since the last visit?';
  await input.dispatch('input');
  await document.getElementById('ai-submit').dispatch('click');
  await settle();

  // The new exchange joins the list without any reload or refetch.
  const firstEntry = list.querySelectorAll('.ai-history__item')[0];
  assertContains(textOf(firstEntry), 'What changed since the last visit?', 'new exchange missing from history');
  assertContains(textOf(firstEntry), '13.5 g/dL', 'new answer missing from history');
});

await check('AI panel quick action "Summarize this patient" fills the composer and sends', async () => {
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

  const quick = document.getElementById('ai-quick-actions');
  assertContains(textOf(quick), 'Summarize this patient', 'quick action missing');
  assertContains(textOf(quick), 'What are the latest abnormal results?', 'lab suggestion chip missing');
  assertContains(textOf(quick), 'Current medications and proposed plan?', 'medication suggestion chip missing');
  assertContains(textOf(quick), 'What changed since the last visit?', 'change suggestion chip missing');

  await clickButton(quick, 'Summarize this patient');
  await settle();

  const askCall = calls.find((call) => call.url.includes('/ai/ask'));
  assert(askCall, 'the quick action must ask the assistant');
  const body = JSON.parse(askCall.init.body);
  assertEqualLocal(body.patient_id, 'p1', 'quick action must stay patient-scoped');
  assertContains(body.question, 'Summarize this patient', 'quick action must send the canonical summary question');
  assertContains(
    document.getElementById('ai-question').value,
    'Summarize this patient',
    'quick action must also fill the composer',
  );
  assertContains(textOf(document.getElementById('ai-response-region')), '13.5 g/dL', 'quick action answer missing');

  // Suggestion chips fill the composer without sending (review before send).
  await clickButton(quick, 'What are the latest abnormal results?');
  assertEqualLocal(
    document.getElementById('ai-question').value,
    'What are the latest abnormal results?',
    'suggestion chip must fill the composer',
  );
});

/* ------------------------------------- Markdown answer rendering (renderer) */

await check('AI page renders a Markdown answer as real table/strong/details DOM', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({ calls, me: DOCTOR, ai: AI_MARKDOWN_ANSWER });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'Кои пациенти имат отклонения в калия?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  const answerBody = document.getElementById('ai-response-region').querySelector('.ai-answer');
  assert(answerBody, 'the answer body must render');

  const table = answerBody.querySelector('table');
  assert(table, 'the pipe table must render as a real <table>');
  assertEqualLocal(table.querySelectorAll('thead').length, 1, 'the table needs one head section');
  assertEqualLocal(table.querySelectorAll('tbody').length, 1, 'the table needs one body section');
  assertEqualLocal(
    JSON.stringify(table.querySelectorAll('th').map((th) => th.textContent)),
    JSON.stringify(['Пациент', 'Тест', 'Стойност', 'Флаг']),
    'header cells must render',
  );
  const numericHeads = table.querySelectorAll('th').filter((th) => th.classList.contains('ai-md-num'));
  assertEqualLocal(
    JSON.stringify(numericHeads.map((th) => th.textContent)),
    JSON.stringify(['Стойност']),
    'only the numeric column right-aligns',
  );
  const rows = table.querySelector('tbody').querySelectorAll('tr');
  assertEqualLocal(rows.length, 2, 'both patient rows must render');
  assertEqualLocal(rows[0].querySelectorAll('td')[2].querySelector('strong').textContent, '6.1 mmol/L', 'the numeric result must render bold');

  const bolds = answerBody.querySelectorAll('strong').map((node) => node.textContent);
  assert(bolds.includes('високо ↑'), 'the abnormal flag must render bold');

  const details = answerBody.querySelector('details');
  assert(details, 'the records-consulted tail must fold into a native disclosure');
  assert(details.querySelector('summary'), 'the disclosure needs a summary');
  assertContains(textOf(details), '5340c8c2', 'the records list must stay inside the disclosure');
  assertEqualLocal(answerBody.querySelectorAll('h4').length, 0, 'the folded heading must not also render');
  assertContains(textOf(document.getElementById('ai-response-region')), '2-ма пациенти', 'the bold summary must render');
});

await check('patient rail + history render Markdown answers through the safe renderer', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    ai: AI_MARKDOWN_ANSWER,
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'Има ли отклонения в калия?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  const answerBody = document.getElementById('ai-response-region').querySelector('.ai-answer');
  assert(answerBody, 'the answer body must render');
  assert(answerBody.querySelector('table'), 'the patient answer must render a real table');
  assert(answerBody.querySelector('details'), 'the patient answer must render the records disclosure');

  // The same Markdown answer in the conversation history (ai-history.js).
  const historyList = document.getElementById('ai-history-list');
  const historyAnswer = historyList.querySelector('.ai-answer');
  assert(historyAnswer, 'the history entry must render the answer body');
  assert(historyAnswer.querySelector('table'), 'the history entry must render the table');
  assert(historyAnswer.querySelector('details'), 'the history entry must render the disclosure');
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
          roles: ['clinical_admin'],
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
  assertContains(region, 'Admin', 'role label missing');
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
      response: { status: 409, code: 'conflict', message: 'At least one active account with the Admin role must remain. Assign the Admin role to another active account before removing this one.' },
    }],
  });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  const row = firstRow(document.getElementById('staff-region'));
  await clickButton(row, 'Revoke Admin');
  await settle();
  const dialog = textOf(document.getElementById('app-dialog-body'));
  assertContains(dialog, 'This removes their ability to', 'revoke consequence missing');
  await clickButton(document.getElementById('app-dialog-actions'), 'Revoke role');
  await settle();

  const status = textOf(document.getElementById('staff-status'));
  assertContains(status, 'Action not allowed right now', '409 title missing');
  assertContains(status, 'At least one active account with the Admin role must remain.', '409 server message missing');
  assert(badgeTexts(row).includes('Admin'), 'role badge must remain after a refused revoke');
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
  select.value = 'doctor';
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

await check('admin page denies a nurse honestly and shows no fake controls', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({ calls, me: NURSE });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS, 'page-body']);
  await freshPage('admin.js');
  await settle();

  const body = textOf(document.getElementById('page-body'));
  assertContains(body, 'You do not have permission', 'denial copy missing');
  assertContains(textOf(document.getElementById('site-nav')), 'Dashboard', 'nav must still render');
  assert(!textOf(document.getElementById('site-nav')).includes('Admin'), 'admin nav item must not render for a nurse');
  assert(!textOf(document.body).includes('Grant role'), 'no staff controls may render for a nurse');
  assertEqualLocal(
    calls.filter((call) => call.url.startsWith('/api/v1/staff') || call.url.startsWith('/api/v1/audit')).length,
    0,
    'no staff/audit fetch may happen without the permissions',
  );
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
    me: NO_PATIENT_READ,
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

/* ============================================ wave UI patterns (render) ==== */

/* ------------------------------------------------------- skeleton loaders */

await check('skeleton loaders show on first load and are replaced by real content', async () => {
  let releasePatients = null;
  globalThis.fetch = async (url, init = {}) => {
    if (url.includes('/auth/me')) return json(200, DOCTOR);
    if (url.includes('/patients')) {
      return new Promise((resolve) => {
        releasePatients = () => resolve(json(200, { items: [PATIENT], total: 1, limit: 20, offset: 0 }));
      });
    }
    throw new Error(`unstubbed request: ${url}`);
  };
  const document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();

  const region = document.getElementById('patients-region');
  assert(region.querySelector('.skeleton-block'), 'first load must show the shimmer skeleton');
  assert(region.querySelector('.skeleton-card'), 'the patient skeleton must approximate the card shape');
  assertContains(textOf(region), 'Loading synthetic patients', 'the skeleton must announce its label');

  releasePatients();
  await settle();
  assertContains(textOf(region), 'Ann Test', 'content must replace the skeleton');
  assertEqualLocal(region.querySelector('.skeleton-block'), null, 'the skeleton must be gone once data arrives');
});

/* --------------------------------------------- stale-while-revalidate (SWR) */

await check('stale-while-revalidate renders cached data instantly then refreshes in the background', async () => {
  let patientCallCount = 0;
  let releaseSecond = null;
  globalThis.fetch = async (url, init = {}) => {
    if (url.includes('/auth/me')) return json(200, DOCTOR);
    if (url.includes('/patients')) {
      patientCallCount += 1;
      if (patientCallCount === 1) {
        return json(200, { items: [PATIENT], total: 1, limit: 20, offset: 0 });
      }
      return new Promise((resolve) => {
        releaseSecond = () => resolve(json(200, {
          items: [
            PATIENT,
            { id: 'p2', display_name: 'Bob Test', mrn: 'MRN-0002', provenance: 'synthetic' },
          ],
          total: 2,
          limit: 20,
          offset: 0,
        }));
      });
    }
    throw new Error(`unstubbed request: ${url}`);
  };
  const document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();

  const region = document.getElementById('patients-region');
  assertContains(textOf(region), 'Ann Test', 'first load must render the data');

  // A second load of the same URL must serve the cache immediately and
  // revalidate in the background (no skeleton, no flicker).
  const input = document.getElementById('patient-search');
  input.value = '';
  await input.dispatch('input');
  await settle(400); // search debounce

  assertEqualLocal(patientCallCount, 2, 'the second load must still revalidate against the API');
  assertContains(textOf(region), 'Ann Test', 'cached data must render instantly on the second load');
  assert(!textOf(region).includes('Bob Test'), 'nothing may change until the revalidation lands');
  assertEqualLocal(region.querySelector('.skeleton-block'), null, 'a cache hit must not show a skeleton');
  assertContains(textOf(document.getElementById('patients-status')), '1 synthetic patient found', 'cached status must render instantly');
  assert(region.querySelector('.sync-chip'), 'a tiny updating indicator must mark the background refresh');

  releaseSecond();
  await settle();
  assertContains(textOf(region), 'Bob Test', 'the revalidated payload must update the DOM');
  assertEqualLocal(region.querySelector('.sync-chip'), null, 'the updating indicator must clear');
  assertContains(textOf(document.getElementById('patients-status')), '2 synthetic patients found', 'status must follow the refresh');
});

/* -------------------------------------------------------- command menu (⌘K) */

await check('command menu opens with the keyboard shortcut, filters, and runs a command', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    me: DOCTOR,
    patients: { items: [PATIENT], total: 1, limit: 50, offset: 0 },
  });
  const document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();

  const opener = document.getElementById('patient-search');
  opener.focus();

  await document.dispatch('keydown', { key: 'k', metaKey: true, preventDefault() {} });
  const dialog = document.getElementById('command-menu');
  assert(dialog && dialog.open, 'Cmd+K must open the command palette');
  assertEqualLocal(document.activeElement, document.getElementById('command-menu-input'), 'the palette input must take focus');

  const input = document.getElementById('command-menu-input');
  input.value = 'pat';
  await input.dispatch('input');
  const filtered = textOf(document.getElementById('command-menu-list'));
  assertContains(filtered, 'Go to Patients', 'the navigation command must match');
  assertContains(filtered, 'Go to patient Ann Test', 'the patient command must match');

  input.value = 'zzzzz';
  await input.dispatch('input');
  assertContains(textOf(document.getElementById('command-menu-list')), 'No matches', 'the empty state is missing');

  input.value = 'go to patients';
  await input.dispatch('input');
  const enter = {
    key: 'Enter',
    prevented: false,
    preventDefault() { this.prevented = true; },
  };
  await input.dispatch('keydown', enter);
  assert(enter.prevented, 'Enter must be intercepted by the palette');
  assertEqualLocal(globalThis.location.redirectedTo, 'patients.html', 'running the command must navigate');
  assertEqualLocal(dialog.open, false, 'the palette must close after running a command');
});

await check('command menu lists bounded patient entries, closes on Esc, and shows real shortcuts', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    me: DOCTOR,
    patients: { items: [PATIENT], total: 130, limit: 50, offset: 0 },
  });
  const document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();

  const opener = document.getElementById('patient-search');
  opener.focus();
  await document.dispatch('keydown', { key: 'k', ctrlKey: true, preventDefault() {} });
  await settle(); // the bounded patient fetch lands lazily on open

  const listText = textOf(document.getElementById('command-menu-list'));
  assertContains(listText, 'Go to patient Ann Test', 'patient entries are missing');
  assertContains(listText, 'of 130 synthetic patients', 'the bounded patient note is missing');
  const paletteFetch = calls.find((call) => call.url.includes('/api/v1/patients') && call.url.includes('limit=50'));
  assert(paletteFetch, 'the palette must fetch a bounded patient list');

  // Esc closes the palette and restores focus to the opener.
  const input = document.getElementById('command-menu-input');
  const esc = {
    key: 'Escape',
    prevented: false,
    preventDefault() { this.prevented = true; },
  };
  await input.dispatch('keydown', esc);
  assert(esc.prevented, 'Esc must be intercepted by the palette');
  assertEqualLocal(document.getElementById('command-menu').open, false, 'Esc must close the palette');
  assertEqualLocal(document.activeElement, opener, 'focus must return to the opener');

  // The "Keyboard shortcuts" command lists only real keybindings.
  await document.dispatch('keydown', { key: 'k', metaKey: true, preventDefault() {} });
  const inputAgain = document.getElementById('command-menu-input');
  inputAgain.value = 'keyboard';
  await inputAgain.dispatch('input');
  await inputAgain.dispatch('keydown', { key: 'Enter', preventDefault() {} });
  await settle();
  const shortcuts = textOf(document.getElementById('app-dialog-body'));
  assertContains(shortcuts, 'Command menu', 'shortcut list must cover the palette');
  assertContains(shortcuts, 'Shift', 'shortcut list must cover Shift+Enter');
  assertContains(shortcuts, 'Enter', 'shortcut list must cover Enter');
  assertContains(shortcuts, 'Esc', 'shortcut list must cover Esc');
});

/* -------------------------------------------- optimistic updates + undo toast */

await check('optimistic grant shows the new role before the server answers', async () => {
  const calls = [];
  const base = staffUser({ id: 'u-3', display_name: 'Nina Nurse', email: 'nina@example.com', roles: ['nurse'] });
  const baseFetch = makeFetch({
    calls,
    me: CLIN_ADMIN,
    staff: { items: [base], total: 1, limit: 20, offset: 0 },
    audit: { items: [AUDIT_EVENT], total: 1, limit: 10, offset: 0 },
  });
  let releaseRole = null;
  globalThis.fetch = async (url, init = {}) => {
    if (url.includes('/roles') && (init.method || 'GET') === 'POST') {
      calls.push({ url, init });
      return new Promise((resolve) => {
        releaseRole = () => resolve(json(200, { ...base, roles: ['nurse', 'doctor'] }));
      });
    }
    return baseFetch(url, init);
  };
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  const row = firstRow(document.getElementById('staff-region'));
  const select = row.querySelector('select');
  select.value = 'doctor';
  await select.dispatch('change');
  await buttonByText(row, 'Grant role').dispatch('click');
  await settle();
  await clickButton(document.getElementById('app-dialog-actions'), 'Grant role');
  await settle();

  assert(releaseRole, 'the role request must be in flight');
  assert(
    badgeTexts(firstRow(document.getElementById('staff-region'))).includes('Doctor'),
    'the row must show the new role while the request is still pending',
  );

  releaseRole();
  await settle();
  assertContains(textOf(document.getElementById('staff-status')), 'Role granted', 'the outcome must be announced');
});

await check('failed optimistic revoke rolls the row back and explains it in a toast', async () => {
  const base = staffUser({ id: 'u-9', display_name: 'Cal Admin', email: 'admin@example.com', roles: ['clinical_admin'] });
  globalThis.fetch = makeFetch({
    me: CLIN_ADMIN,
    staff: { items: [base], total: 1, limit: 20, offset: 0 },
    audit: { items: [AUDIT_EVENT], total: 1, limit: 10, offset: 0 },
    routes: [{
      method: 'DELETE',
      match: /\/staff\/u-9\/roles\/[^/]+$/,
      response: { status: 409, code: 'conflict', message: 'At least one active account with the Admin role must remain.' },
    }],
  });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  await clickButton(firstRow(document.getElementById('staff-region')), 'Revoke Admin');
  await settle();
  await clickButton(document.getElementById('app-dialog-actions'), 'Revoke role');
  await settle();

  assert(
    badgeTexts(firstRow(document.getElementById('staff-region'))).includes('Admin'),
    'the refused revoke must roll the row back to the server truth',
  );
  const toasts = textOf(document.getElementById('toast-region'));
  assertContains(toasts, 'Action not allowed right now', 'the error toast must explain the failure');
  assertContains(textOf(document.getElementById('staff-status')), 'Action not allowed right now', 'the status region must announce it too');
});

await check('deactivation shows an undo toast and Undo re-activates the account', async () => {
  const patches = [];
  const base = staffUser({ id: 'u-5', display_name: 'Eva Example', email: 'eva@example.com', roles: ['nurse'] });
  globalThis.fetch = makeFetch({
    me: CLIN_ADMIN,
    staff: { items: [base], total: 1, limit: 20, offset: 0 },
    audit: { items: [AUDIT_EVENT], total: 1, limit: 10, offset: 0 },
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

  await clickButton(firstRow(document.getElementById('staff-region')), 'Deactivate');
  await settle();
  await clickButton(document.getElementById('app-dialog-actions'), 'Deactivate');
  await settle();

  assertEqualLocal(patches.length, 1, 'the deactivation PATCH is missing');
  const toastHost = document.getElementById('toast-region');
  assertContains(textOf(toastHost), 'Account deactivated', 'the undo toast is missing');
  const undo = buttonByText(toastHost, 'Undo');
  assert(undo, 'the toast must carry a working Undo action');
  await undo.dispatch('click');
  await settle();

  assertEqualLocal(patches.length, 2, 'Undo must send the inverse PATCH');
  assertEqualLocal(patches[1].is_active, true, 'Undo must reactivate the account');
  assertContains(textOf(document.getElementById('toast-region')), 'Restored', 'restoration must be confirmed');
  assert(
    badgeTexts(firstRow(document.getElementById('staff-region'))).includes('Active'),
    'the row must show the restored state',
  );
});

await check('role revoke shows an undo toast and Undo re-grants the role', async () => {
  const roleCalls = [];
  const base = staffUser({ id: 'u-4', display_name: 'Ned Nurse', email: 'ned@example.com', roles: ['nurse', 'doctor'] });
  globalThis.fetch = makeFetch({
    me: CLIN_ADMIN,
    staff: { items: [base], total: 1, limit: 20, offset: 0 },
    audit: { items: [AUDIT_EVENT], total: 1, limit: 10, offset: 0 },
    routes: [
      {
        method: 'DELETE',
        match: /\/staff\/u-4\/roles\/[^/]+$/,
        response: () => ({ ...base, roles: ['nurse'] }),
      },
      {
        method: 'POST',
        match: /\/staff\/u-4\/roles$/,
        response: ({ body }) => {
          roleCalls.push(body);
          return { ...base, roles: ['nurse', 'doctor'] };
        },
      },
    ],
  });
  const document = bootDom([...CHROME_IDS, ...ADMIN_IDS]);
  await freshPage('admin.js');
  await settle();

  await clickButton(firstRow(document.getElementById('staff-region')), 'Revoke Doctor');
  await settle();
  await clickButton(document.getElementById('app-dialog-actions'), 'Revoke role');
  await settle();

  const toastHost = document.getElementById('toast-region');
  assertContains(textOf(toastHost), 'Role removed', 'the undo toast is missing');
  const undo = buttonByText(toastHost, 'Undo');
  assert(undo, 'the toast must carry a working Undo action');
  await undo.dispatch('click');
  await settle();

  assertEqualLocal(roleCalls.length, 1, 'Undo must re-grant through the API');
  assertEqualLocal(roleCalls[0].role, 'doctor', 'the re-grant must carry the revoked role');
  assertContains(textOf(document.getElementById('toast-region')), 'Restored', 'restoration must be confirmed');
  assert(
    badgeTexts(firstRow(document.getElementById('staff-region'))).includes('Doctor'),
    'the row must show the restored role',
  );
});

function assertEqualLocal(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`);
  }
}

/* ============================================ wave UI-B patterns (render) === */

/* ---------------------------------------------------- blur placeholders */

await check('blur placeholders swap to sharp content via the ready class', async () => {
  const document = bootDom([]);
  const region = document.createElement('div');
  document.body.append(region);

  ui.renderBlurPlaceholder(region, { label: 'Loading patient demographics', variant: 'hero' });
  assert(region.classList.contains('blur-region'), 'the region must carry the blur-region class');
  assert(region.classList.contains('is-loading'), 'the region must be marked loading');
  assert(region.querySelector('.blur-placeholder'), 'the blurred placeholder must render');
  assertContains(textOf(region), 'Loading patient demographics', 'the placeholder must announce its label');
  assert(region.querySelectorAll('.blur-block').length >= 3, 'placeholder blocks must approximate the layout');

  ui.clear(region);
  region.append(ui.el('p', { text: 'Real demographics' }));
  ui.markBlurReady(region);
  assertEqualLocal(region.querySelector('.blur-placeholder'), null, 'the placeholder must be gone');
  assert(region.classList.contains('is-ready'), 'the ready class must be toggled on');
  assert(!region.classList.contains('is-loading'), 'the loading class must clear');

  // Re-targeting: further swaps stay consistent (class toggling is idempotent).
  ui.renderBlurPlaceholder(region, { variant: 'answer' });
  ui.markBlurReady(region);
  assert(region.classList.contains('is-ready'), 'repeated swaps settle on the ready state');
});

await check('patient header card shows a blur placeholder that sharpens in on arrival', async () => {
  let releaseDemographics = null;
  const baseFetch = makeFetch({ patient: PATIENT, summary: SUMMARY, labs: LABS, prescriptions: PRESCRIPTIONS });
  globalThis.fetch = async (url, init = {}) => {
    const path = url.split('?')[0];
    if (path.endsWith('/patients/p1')) {
      // Pending demographics request (exactly like a real slow fetch).
      return new Promise((resolve) => {
        releaseDemographics = () => resolve(json(200, PATIENT));
      });
    }
    return baseFetch(url, init);
  };
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const region = document.getElementById('demographics-region');
  assert(region.querySelector('.blur-placeholder'), 'the hero header must show the blur placeholder');
  assertEqualLocal(region.querySelector('.skeleton-block'), null, 'hero regions use blur placeholders, not skeletons');

  releaseDemographics();
  await settle();
  assertContains(textOf(region), 'MRN-0001', 'the real demographics must replace the placeholder');
  assertEqualLocal(region.querySelector('.blur-placeholder'), null, 'the placeholder must be gone on arrival');
  assert(region.classList.contains('is-ready'), 'the ready class must trigger the blur-in');
});

/* ---------------------------------------------------- view transitions */

await check('panel swaps run through the view-transition helper with an instant fallback', async () => {
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

  let transitions = 0;
  const original = document.startViewTransition;
  document.startViewTransition = (update) => {
    transitions += 1;
    return original.call(document, update);
  };

  await freshPage('patient.js');
  await settle();

  await document.getElementById('tab-labs').dispatch('click');
  assertEqualLocal(document.getElementById('panel-labs').hidden, false, 'the labs panel must show');
  assertEqualLocal(document.getElementById('panel-summary').hidden, true, 'the summary panel must hide');
  assert(transitions >= 1, 'tab swaps must run through the view-transition helper');

  // Feature-detected fallback: without startViewTransition the swap is instant.
  document.startViewTransition = undefined;
  transitions = 0;
  await document.getElementById('tab-summary').dispatch('click');
  assertEqualLocal(document.getElementById('panel-summary').hidden, false, 'the fallback must still switch panels');
  assertEqualLocal(document.getElementById('panel-labs').hidden, true, 'the fallback must hide the other panel');
  assertEqualLocal(transitions, 0, 'no transition may run without the API');
});

/* ------------------------------------------------- swipe-to-dismiss sheets */

await check('swipe-to-dismiss keeps the sheet below the threshold and dismisses above it without confirming', async () => {
  bootDom([]);

  // First gesture: below the threshold — the sheet stays open and springs back.
  let resolved;
  const promise = ui.confirmDialog({
    title: 'Sign out',
    message: 'Sign out of the demo?',
    confirmLabel: 'Sign out',
  });
  promise.then((value) => {
    resolved = value;
  });
  const dialog = document.getElementById('app-dialog');
  assert(dialog && dialog.open, 'the confirm dialog must open as a sheet');
  const grabber = dialog.querySelector('.sheet-grabber');
  assert(grabber, 'the sheet must expose a grab handle');

  await grabber.dispatch('pointerdown', { clientX: 100, clientY: 100, pointerType: 'touch' });
  await grabber.dispatch('pointermove', { clientX: 100, clientY: 140, pointerType: 'touch' });
  assertEqualLocal(dialog.style.getPropertyValue('--sheet-dy'), '40px', 'the sheet must follow the finger');
  await grabber.dispatch('pointerup', {});
  await settle();
  assertEqualLocal(resolved, undefined, 'a small drag must not resolve the dialog');
  assertEqualLocal(dialog.open, true, 'a small drag must keep the sheet open');
  assertEqualLocal(dialog.style.getPropertyValue('--sheet-dy'), '0px', 'the sheet must spring back');

  // Second gesture: past the threshold — dismissed like cancel, NEVER confirmed.
  await grabber.dispatch('pointerdown', { clientX: 100, clientY: 100, pointerType: 'touch' });
  await grabber.dispatch('pointermove', { clientX: 100, clientY: 260, pointerType: 'touch' });
  await grabber.dispatch('pointerup', {});
  await settle();
  assertEqualLocal(resolved, false, 'swipe-dismiss must resolve like cancel, never confirm');
  assertEqualLocal(dialog.open, false, 'the sheet must close past the threshold');
});

/* ------------------------------------------------- interruptible animations */

await check('state-coupled animation classes re-target cleanly under rapid toggling', async () => {
  const document = bootDom([]);

  // Sync chip: on -> off -> on settles on exactly one chip.
  const host = document.createElement('div');
  document.body.append(host);
  ui.setSyncIndicator(host, true);
  ui.setSyncIndicator(host, false);
  ui.setSyncIndicator(host, true);
  assertEqualLocal(host.querySelectorAll('.sync-chip').length, 1, 'the sync chip must re-target to a single instance');

  // Blur region: repeated placeholder/ready swaps settle on the ready state.
  const region = document.createElement('div');
  document.body.append(region);
  for (let i = 0; i < 3; i += 1) {
    ui.renderBlurPlaceholder(region, { variant: 'hero' });
    ui.markBlurReady(region);
  }
  assert(region.classList.contains('is-ready'), 'blur swaps must settle on the ready state');
  assert(!region.classList.contains('is-loading'), 'blur swaps must clear the loading state');

  // Sheet: an interrupted drag followed by a dismiss settles exactly once.
  let resolvedCount = 0;
  ui.confirmDialog({ title: 'Interrupt me', message: 'Rapid gestures.', confirmLabel: 'Confirm' })
    .then(() => {
      resolvedCount += 1;
    });
  const dialog = document.getElementById('app-dialog');
  const grabber = dialog.querySelector('.sheet-grabber');
  await grabber.dispatch('pointerdown', { clientX: 0, clientY: 0, pointerType: 'touch' });
  await grabber.dispatch('pointermove', { clientX: 0, clientY: 60, pointerType: 'touch' });
  await grabber.dispatch('pointercancel', {});
  await grabber.dispatch('pointerdown', { clientX: 0, clientY: 0, pointerType: 'touch' });
  await grabber.dispatch('pointermove', { clientX: 0, clientY: 200, pointerType: 'touch' });
  await grabber.dispatch('pointerup', {});
  await settle();
  assertEqualLocal(resolvedCount, 1, 'interrupted gestures must settle exactly once');
  assertEqualLocal(dialog.open, false, 'the interrupted-then-dismissed sheet must close');

  // Toast: dismissed mid-entrance, then re-shown — the fresh toast survives.
  const shown = toast.showToast({ message: 'First notification' });
  shown.dismiss();
  toast.showToast({ message: 'Second notification' });
  await settle(300);
  const toasts = textOf(document.getElementById('toast-region'));
  assertContains(toasts, 'Second notification', 'the fresh toast must stay visible');
  assert(!toasts.includes('First notification'), 'the interrupted toast must leave after its exit transition');
});

/* ------------------------------------------------------- predictive back */

await check('predictive back dismisses the topmost overlay first and unwinds LIFO', async () => {
  globalThis.fetch = makeFetch({
    me: DOCTOR,
    patients: { items: [PATIENT], total: 1, limit: 50, offset: 0 },
  });
  const document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();

  const opener = document.getElementById('patient-search');
  opener.focus();
  await document.dispatch('keydown', { key: 'k', metaKey: true, preventDefault() {} });
  const menu = document.getElementById('command-menu');
  assert(menu.open, 'the palette must open');
  assertEqualLocal(globalThis.history.length, 2, 'opening an overlay must push one history entry');

  // Second overlay on top: the About dialog (footer control, real UI path).
  const aboutButton = document.querySelectorAll('[data-open-about]')[0];
  await aboutButton.dispatch('click');
  const dialog = document.getElementById('app-dialog');
  assert(dialog.open, 'the About dialog must stack on top');
  assertEqualLocal(globalThis.history.length, 3, 'each overlay owns one history entry');

  // Back #1: only the TOPMOST overlay closes (LIFO).
  globalThis.history.back();
  await settle();
  assertEqualLocal(dialog.open, false, 'Back must dismiss the topmost overlay first');
  assertEqualLocal(menu.open, true, 'the palette must stay open beneath it');

  // Back #2: the palette closes.
  globalThis.history.back();
  await settle();
  assertEqualLocal(menu.open, false, 'the second Back must dismiss the palette');
  assertEqualLocal(globalThis.history.length, 1, 'overlay history entries must unwind cleanly');
  assertEqualLocal(globalThis.location.href, 'http://localhost/page.html', 'URL state must be untouched');

  // UI close (Esc) consumes its own entry: no history leak.
  await document.dispatch('keydown', { key: 'k', metaKey: true, preventDefault() {} });
  assertEqualLocal(globalThis.history.length, 2, 'reopening must push one entry');
  await document.getElementById('command-menu-input').dispatch('keydown', {
    key: 'Escape',
    prevented: false,
    preventDefault() {
      this.prevented = true;
    },
  });
  await settle();
  assertEqualLocal(document.getElementById('command-menu').open, false, 'Esc must close the palette');
  assertEqualLocal(globalThis.history.length, 1, 'a UI close must consume its history entry');

  // The mobile nav drawer participates too.
  await document.getElementById('nav-toggle').dispatch('click');
  assertEqualLocal(globalThis.history.length, 2, 'the drawer must push one entry');
  globalThis.history.back();
  await settle();
  assertEqualLocal(document.getElementById('nav-toggle').getAttribute('aria-expanded'), 'false', 'Back must close the drawer');
});

await check('expanded history entries unwind through Back in LIFO order', async () => {
  globalThis.fetch = makeFetch({ me: DOCTOR, conversations: CONVERSATIONS });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const list = document.getElementById('ai-history-list');
  const summaries = list.querySelectorAll('.ai-history__summary');
  await summaries[0].dispatch('click');
  await summaries[1].dispatch('click');
  assertEqualLocal(summaries[0].getAttribute('aria-expanded'), 'true', 'first entry must expand');
  assertEqualLocal(summaries[1].getAttribute('aria-expanded'), 'true', 'second entry must expand');
  assertEqualLocal(globalThis.history.length, 3, 'each expanded entry owns one history entry');

  // Back collapses the most recently expanded entry first.
  globalThis.history.back();
  await settle();
  assertEqualLocal(summaries[1].getAttribute('aria-expanded'), 'false', 'Back must collapse the newest expansion first');
  assertEqualLocal(summaries[0].getAttribute('aria-expanded'), 'true', 'the older expansion must stay');

  globalThis.history.back();
  await settle();
  assertEqualLocal(summaries[0].getAttribute('aria-expanded'), 'false', 'the second Back must collapse the older one');
  assertEqualLocal(globalThis.history.length, 1, 'history entries must unwind cleanly');

  // A UI collapse consumes its own entry.
  await summaries[0].dispatch('click');
  assertEqualLocal(globalThis.history.length, 2, 'expanding must push one entry');
  await summaries[0].dispatch('click');
  assertEqualLocal(globalThis.history.length, 1, 'collapsing must consume its entry');
});

/* -------------------------------------- pre-rendering + intent prefetch */

await check('module warm-up registers modulepreload hints on idle', async () => {
  bootDom([]);
  const href = `assets/js/warm-${Date.now()}.js`;
  prefetch.warmModules([href], { schedule: (fn) => fn() });
  const links = document.querySelectorAll('link');
  const match = links.filter(
    (link) => link.getAttribute('rel') === 'modulepreload' && link.getAttribute('href') === href,
  );
  assertEqualLocal(match.length, 1, 'the idle warm-up must add a modulepreload hint');
  // Idempotent: warming the same href twice adds one hint.
  prefetch.warmModules([href], { schedule: (fn) => fn() });
  assertEqualLocal(
    document.querySelectorAll('link').filter((link) => link.getAttribute('href') === href).length,
    1,
    'module hints must be added once per href',
  );
});

await check('hover intent prefetch warms patient-detail data and stays permission-gated', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    me: DOCTOR,
    patients: { items: [PATIENT], total: 1, limit: 20, offset: 0 },
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
  });
  const document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();

  const card = document.getElementById('patients-region').querySelector('a');
  assert(card, 'the patient card must render');
  assertEqualLocal(card.getAttribute('data-prefetch-patient'), 'p1', 'cards must carry the prefetch hook');

  await card.dispatch('pointerenter', {});
  await settle(400); // past the ~150ms intent debounce
  const detailCalls = calls.filter((call) => call.url.includes('/api/v1/patients/p1'));
  assert(detailCalls.length >= 1, 'hover intent must warm the patient-detail data');
  assert(cache.peek(cache.keyFor('/patients/p1')) !== null, 'the warm-up must land in the in-memory cache');

  // Permission gate: without patient:read nothing may be prefetched.
  const before = calls.length;
  prefetch.warmPatientRecord('p2', NO_PATIENT_READ);
  await settle(300);
  assertEqualLocal(calls.length, before, 'no prefetch may run beyond the user permissions');
});

/* ------------------------------------------- idempotency key (frontend) */

await check('AI asks carry an Idempotency-Key with a fresh value per request', async () => {
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
  await document.getElementById('ai-submit').dispatch('click');
  await settle();

  input.value = 'And the potassium?';
  await input.dispatch('input');
  await document.getElementById('ai-submit').dispatch('click');
  await settle();

  const askCalls = calls.filter((call) => call.url.includes('/ai/ask'));
  assertEqualLocal(askCalls.length, 2, 'two asks expected');
  const keys = askCalls.map((call) => call.init.headers['Idempotency-Key']);
  for (const key of keys) {
    assert(typeof key === 'string' && key.length > 0, 'every mutating call must carry an Idempotency-Key');
  }
  assert(keys[0] !== keys[1], 'each NEW request must get a fresh key');
  assert(!JSON.stringify(keys).includes('haemoglobin'), 'the key must never encode clinical data');
});

/* ================================================ wave U — admin analytics ===
   The analytics page renders real aggregates from GET /analytics/summary as
   pure CSS/inline-SVG charts: SVG bar counts must match the data, the donut is
   a stroke-dasharray arc, every chart carries role="img" + an aria-label and a
   visually-hidden data table, empty states are honest, the first load shows
   skeletons, Refresh rides the SWR cache, and the page/nav/palette all gate on
   analytics:read. */

await check('analytics page renders KPI tiles and pure-SVG charts from the summary payload', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({ me: CLIN_ADMIN, calls, analytics: ANALYTICS_SUMMARY });
  const document = bootDom([...CHROME_IDS, ...ANALYTICS_IDS]);
  await freshPage('analytics.js');
  await settle();

  const kpi = document.getElementById('kpi-region');
  const kpiText = textOf(kpi);
  assertContains(kpiText, 'Patients', 'KPI labels missing');
  assertContains(kpiText, '45', 'patient count missing');
  assertContains(kpiText, '167', 'encounter count missing');
  assertContains(kpiText, '+24 in the last 30 days', 'the 30-day delta missing');
  assertEqualLocal(kpi.querySelectorAll('.kpi-tile').length, 8, 'eight KPI tiles expected');
  assertContains(textOf(document.getElementById('analytics-generated-at')), '27 Oct 2026', 'generated-at line missing');

  const audit = document.getElementById('audit-activity-region');
  assertEqualLocal(audit.querySelectorAll('rect').length, 14 * 3, 'one grouped bar per day and series');
  const chart = audit.querySelector('svg.chart');
  assert(chart, 'the audit chart must render');
  assertEqualLocal(chart.getAttribute('role'), 'img', 'the chart must be exposed as an image');
  assertContains(chart.getAttribute('aria-label') || '', 'last 14 days', 'the chart needs a spoken summary');
  assertContains(textOf(audit), 'Permission denials', 'the legend is missing');
  assertEqualLocal(audit.querySelectorAll('tr').length, 15, 'hidden data table must carry 14 days + header');

  const labs = document.getElementById('labs-region');
  const donut = labs.querySelector('svg.donut');
  assert(donut, 'the abnormal-rate donut must render');
  assertEqualLocal(donut.getAttribute('role'), 'img', 'the donut must be exposed as an image');
  assertContains(donut.getAttribute('aria-label') || '', '121 of 402', 'the donut summary must carry the numbers');
  assertEqualLocal(donut.querySelectorAll('circle').length, 2, 'track + value arc expected');
  const arc = donut.querySelectorAll('circle')[1];
  assertContains(arc.getAttribute('stroke-dasharray') || '', ' ', 'the arc length must be a dasharray attribute');
  assertContains(textOf(labs), '30.1%', 'the donut label must show the rate');
  assertContains(textOf(labs), 'Glucose, fasting (GLU)', 'top abnormal tests missing');
  assertContains(textOf(labs), 'Preliminary', 'status mix missing');

  const prescriptions = document.getElementById('prescriptions-region');
  assertContains(textOf(prescriptions), 'Completed', 'prescription statuses missing');
  assertEqualLocal(prescriptions.querySelectorAll('rect').length, 8, 'four bar rows with track + fill expected');

  const staff = document.getElementById('staff-region');
  assertContains(textOf(staff), 'Doctor', 'role pills missing');
  assertContains(textOf(staff), 'Admin', 'admin role pill missing');

  const ai = document.getElementById('ai-region');
  assertContains(textOf(ai), 'General lookups', 'AI split missing');
  assertContains(textOf(ai), 'Patient questions', 'AI split missing');
  assertContains(textOf(ai), 'get_patient_summary', 'tool chips missing');
  assertContains(textOf(ai), '\u00d7 9', 'tool call counts missing');

  // Every chart pairs its visual with a visually-hidden data table.
  for (const regionId of ['audit-activity-region', 'labs-region', 'prescriptions-region', 'staff-region', 'ai-region']) {
    const region = document.getElementById(regionId);
    assert(
      region.querySelectorAll('.visually-hidden').length >= 1,
      `${regionId} must carry a hidden data table`,
    );
  }
});

await check('analytics empty states are honest when a metric is empty', async () => {
  const emptyish = {
    ...ANALYTICS_SUMMARY,
    top_abnormal_tests: [],
    abnormal_lab_rate: { abnormal: 0, total: 0, pct: 0 },
    prescriptions_by_status: { active: 0, completed: 0, stopped: 0, cancelled: 0 },
    staff_by_role: [
      { role: 'doctor', count: 0 },
      { role: 'nurse', count: 0 },
      { role: 'clinical_admin', count: 0 },
    ],
    ai_usage: { total_asks: 0, general_asks: 0, patient_asks: 0, top_tools: [] },
  };
  globalThis.fetch = makeFetch({ me: CLIN_ADMIN, analytics: emptyish });
  const document = bootDom([...CHROME_IDS, ...ANALYTICS_IDS]);
  await freshPage('analytics.js');
  await settle();

  assertContains(
    textOf(document.getElementById('ai-region')),
    'No AI usage recorded yet',
    'zero AI usage must be said honestly',
  );
  const labs = textOf(document.getElementById('labs-region'));
  assertContains(labs, 'No abnormal lab results recorded yet', 'zero abnormal results must be said honestly');
  assertContains(
    labs,
    'No numeric lab results with reference ranges yet',
    'a zero denominator must be explained',
  );
  assertContains(
    textOf(document.getElementById('prescriptions-region')),
    'No prescriptions recorded yet',
    'zero prescriptions must be said honestly',
  );
  assertContains(
    textOf(document.getElementById('staff-region')),
    'No active staff roles yet',
    'zero staff roles must be said honestly',
  );
});

await check('analytics first load shows shimmer skeletons and replaces them with the summary', async () => {
  let releaseSummary = null;
  globalThis.fetch = async (url, init = {}) => {
    const path = url.split('?')[0];
    if (path.endsWith('/auth/me')) return json(200, CLIN_ADMIN);
    if (path.endsWith('/analytics/summary')) {
      return new Promise((resolve) => {
        releaseSummary = () => resolve(json(200, ANALYTICS_SUMMARY));
      });
    }
    throw new Error(`unstubbed request: ${url}`);
  };
  const document = bootDom([...CHROME_IDS, ...ANALYTICS_IDS]);
  await freshPage('analytics.js');
  await settle();

  const kpi = document.getElementById('kpi-region');
  assert(kpi.querySelector('.skeleton-block'), 'first load must show the shimmer skeleton');
  assertContains(textOf(kpi), 'Loading key numbers', 'the skeleton must announce its label');

  releaseSummary();
  await settle();
  assertContains(textOf(kpi), '45', 'content must replace the skeleton');
  assertEqualLocal(kpi.querySelector('.skeleton-block'), null, 'the skeleton must be gone');
});

await check('analytics Refresh re-fetches through the SWR cache-hit path', async () => {
  let summaryCalls = 0;
  let releaseSecond = null;
  globalThis.fetch = async (url, init = {}) => {
    const path = url.split('?')[0];
    if (path.endsWith('/auth/me')) return json(200, CLIN_ADMIN);
    if (path.endsWith('/analytics/summary')) {
      summaryCalls += 1;
      if (summaryCalls === 1) return json(200, ANALYTICS_SUMMARY);
      return new Promise((resolve) => {
        releaseSecond = () =>
          resolve(json(200, { ...ANALYTICS_SUMMARY, counts: { ...ANALYTICS_SUMMARY.counts, patients: 46 } }));
      });
    }
    throw new Error(`unstubbed request: ${url}`);
  };
  const document = bootDom([...CHROME_IDS, ...ANALYTICS_IDS]);
  await freshPage('analytics.js');
  await settle();
  assertContains(textOf(document.getElementById('kpi-region')), '45', 'first load must render the totals');

  await document.getElementById('analytics-refresh').dispatch('click');
  await settle();
  assertEqualLocal(summaryCalls, 2, 'Refresh must re-fetch through the SWR key');
  assertContains(textOf(document.getElementById('kpi-region')), '45', 'cached data must render instantly');
  assertEqualLocal(document.querySelector('.skeleton-block'), null, 'a cache hit must not show a skeleton');
  assert(document.querySelector('.sync-chip'), 'the background refresh must be marked');

  releaseSecond();
  await settle();
  assertContains(textOf(document.getElementById('kpi-region')), '46', 'the revalidated payload must update the DOM');
  assertEqualLocal(document.querySelector('.sync-chip'), null, 'the updating indicator must clear');
});

await check('analytics page renders the permission-denied panel for non-admins', async () => {
  for (const user of [DOCTOR, NURSE]) {
    const calls = [];
    globalThis.fetch = makeFetch({ me: user, calls, analytics: ANALYTICS_SUMMARY });
    const document = bootDom([...CHROME_IDS, ...ANALYTICS_IDS]);
    await freshPage('analytics.js');
    await settle();

    const body = textOf(document.getElementById('page-body'));
    assertContains(body, 'You do not have permission', `${user.roles[0]} must see the denial panel`);
    assertContains(body, 'analytics:read', 'the denial must name the missing permission');
    assertContains(body, 'Back to dashboard', 'the denial must offer a way out');
    assert(
      !calls.some((call) => call.url.includes('/analytics/summary')),
      `${user.roles[0]} must never fetch the summary`,
    );
    assert(
      !textOf(document.getElementById('site-nav')).includes('Analytics'),
      `${user.roles[0]} must not see the Analytics nav item`,
    );
  }
});

await check('nav item gating: Analytics appears only with analytics:read', async () => {
  const patientsPage = [...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region'];
  globalThis.fetch = makeFetch({ me: CLIN_ADMIN, patients: { items: [], total: 0, limit: 20, offset: 0 } });
  let document = bootDom(patientsPage);
  await freshPage('patients.js');
  await settle();
  assertContains(
    textOf(document.getElementById('site-nav')),
    'Analytics',
    'admin must see the Analytics nav item',
  );

  for (const user of [DOCTOR, NURSE]) {
    globalThis.fetch = makeFetch({ me: user, patients: { items: [], total: 0, limit: 20, offset: 0 } });
    document = bootDom(patientsPage);
    await freshPage('patients.js');
    await settle();
    assert(
      !textOf(document.getElementById('site-nav')).includes('Analytics'),
      `${user.roles[0]} must not see the Analytics nav item`,
    );
  }
});

await check('command palette offers "Open Analytics" only to permitted users', async () => {
  const patientsPage = [...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region'];

  globalThis.fetch = makeFetch({ me: CLIN_ADMIN, patients: { items: [], total: 0, limit: 50, offset: 0 } });
  let document = bootDom(patientsPage);
  await freshPage('patients.js');
  await settle();
  await document.dispatch('keydown', { key: 'k', metaKey: true, preventDefault() {} });
  await settle();
  assertContains(
    textOf(document.getElementById('command-menu-list')),
    'Open Analytics',
    'the admin palette must offer Open Analytics',
  );

  globalThis.fetch = makeFetch({ me: DOCTOR, patients: { items: [], total: 0, limit: 50, offset: 0 } });
  document = bootDom(patientsPage);
  await freshPage('patients.js');
  await settle();
  await document.dispatch('keydown', { key: 'k', metaKey: true, preventDefault() {} });
  await settle();
  assert(
    !textOf(document.getElementById('command-menu-list')).includes('Open Analytics'),
    'the doctor palette must not offer Open Analytics',
  );
});

/* ============================================ wave UI-C: responsive === */

/* ------------------------------------- command palette arrow-follow scroll */

await check('command menu arrows scroll the NEW active option into view with block:nearest', async () => {
  // Long filtered list: the active option must follow the arrows via
  // scrollIntoView({block:'nearest'}) (the fix in command-menu.js renderList).
  const names = [
    'Елена Георгиева (демо)',
    'Иван Петров (демо)',
    'Александър-Константин Димитров-Стоянов (демо)',
    'Мария Иванова (демо)',
    'Антоанета Иванова-Петрова (демо)',
  ];
  globalThis.fetch = makeFetch({
    me: DOCTOR,
    patients: {
      items: names.map((display_name, index) => ({
        id: `pg${index + 1}`,
        mrn: `DEMO-00${index + 1}`,
        display_name,
        provenance: 'synthetic',
      })),
      total: names.length,
      limit: 50,
      offset: 0,
    },
  });
  const document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();

  await document.dispatch('keydown', { key: 'k', metaKey: true, preventDefault() {} });
  await settle(); // the bounded patient list lands lazily on open

  const input = document.getElementById('command-menu-input');
  input.value = 'go to patient';
  await input.dispatch('input'); // a long filtered list (nav + all patients)

  const options = document.getElementById('command-menu-list').querySelectorAll('.command-menu__option');
  assert(options.length >= 5, `the filtered list must be long enough to need following (got ${options.length})`);

  const scrollCalls = scrollIntoViewCalls();
  scrollCalls.length = 0; // record from the arrow moves only

  await input.dispatch('keydown', { key: 'ArrowDown', preventDefault() {} });
  assertEqualLocal(scrollCalls.length, 1, 'ArrowDown must scroll the active option exactly once');
  assertEqualLocal(scrollCalls[0].node.getAttribute('id'), 'command-menu-option-1', 'first ArrowDown must follow the NEW active option');
  assertEqualLocal(scrollCalls[0].options && scrollCalls[0].options.block, 'nearest', 'the follow scroll must use block: nearest (no jump when already in view)');

  await input.dispatch('keydown', { key: 'ArrowDown', preventDefault() {} });
  assertEqualLocal(scrollCalls.length, 2, 'the second ArrowDown must scroll exactly once more');
  assertEqualLocal(scrollCalls[1].node.getAttribute('id'), 'command-menu-option-2', 'second ArrowDown must follow the NEW active option');
  assertEqualLocal(scrollCalls[1].options && scrollCalls[1].options.block, 'nearest', 'the follow scroll must stay {block: nearest}');

  assertEqualLocal(input.getAttribute('aria-activedescendant'), 'command-menu-option-2', 'aria-activedescendant must follow the arrows');

  // Mouse path: running an option by click closes the palette and must not
  // trigger any selection scrolling (the follow is an arrow-key affordance).
  scrollCalls.length = 0;
  const active = document.getElementById('command-menu-list').querySelector('#command-menu-option-2');
  await active.dispatch('click');
  assertEqualLocal(scrollCalls.length, 0, 'a mouse click must not trigger selection scrolling');
  assertEqualLocal(document.getElementById('command-menu').open, false, 'the palette must close after running the command');
});

/* ------------------------------------------- long names + narrow-width structure */

await check('long synthetic compound names render wrap-safe in cards/header; pagination keeps thumb targets', async () => {
  // The real seed's longest name (scripts/seed_demo_data.py: DEMO-0013).
  const LONG_NAME = 'Александър-Константин Димитров-Стоянов (демо)';
  const LONG_PATIENT = {
    id: 'p13',
    mrn: 'DEMO-0013-EXTRA-LONG-RECORD-NUMBER-000013',
    display_name: LONG_NAME,
    given_name: 'Александър-Константин',
    family_name: 'Димитров-Стоянов',
    date_of_birth: '1949-04-22',
    sex: 'M',
    provenance: 'synthetic',
  };

  // Patient card (patients list): full name, wrap class applied.
  globalThis.fetch = makeFetch({ patients: { items: [LONG_PATIENT], total: 42, limit: 20, offset: 0 } });
  let document = bootDom([...CHROME_IDS, 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region']);
  await freshPage('patients.js');
  await settle();

  const nameNode = document.getElementById('patients-region').querySelector('.patient-card__name');
  assert(nameNode, 'the patient card name node must render');
  assertContains(nameNode.textContent, LONG_NAME, 'the full long name must render, untruncated');
  assert(nameNode.classList.contains('patient-card__name'), 'the card name must keep its wrap class');
  const mrnNode = document.getElementById('patients-region').querySelector('.patient-card__mrn');
  assertContains(mrnNode.textContent, 'DEMO-0013-EXTRA-LONG-RECORD-NUMBER-000013', 'the full MRN must render');

  // Pagination page buttons carry hit-slop: >=44px effective thumb targets
  // (the numbered buttons are visually ~38x34px).
  const pageButtons = document.getElementById('patients-region').querySelectorAll('.btn-page');
  assert(pageButtons.length > 0, 'pagination page buttons must render');
  for (const button of pageButtons) {
    assert(button.classList.contains('hit-slop'), 'every pagination page button must carry hit-slop');
  }

  // Patient header: full name in the heading, wrap utility applied.
  globalThis.fetch = makeFetch({
    patient: LONG_PATIENT,
    summary: { ...SUMMARY, patient: LONG_PATIENT },
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
  });
  document = bootDom([...CHROME_IDS, 'page-body', 'patient-heading', 'patient-avatar', 'demographics-region', 'summary-region', 'labs-region', 'prescriptions-region', 'encounters-region']);
  globalThis.location.search = '?id=p13';
  await freshPage('patient.js');
  await settle();

  const heading = document.getElementById('patient-heading');
  assertContains(heading.textContent, LONG_NAME, 'the full long name must render in the patient header');
  assert(heading.classList.contains('text-break'), 'the patient heading must carry the wrap utility');
  assertContains(textOf(document.getElementById('demographics-region')), 'DEMO-0013-EXTRA-LONG-RECORD-NUMBER-000013', 'the full MRN must render in the header');
});

await check('narrow-width structure: card-stacked tables and labelled keyboard-focusable scroll regions', async () => {
  // The shim has no layout: this asserts the responsive class/attribute
  // CONTRACT that the phone CSS keys off (table-stack at ≤620px, scroll
  // regions focusable for keyboards/TV remotes). Real 320–430px layout is
  // browser-verified (see the checklist in DESIGN.md).
  globalThis.fetch = makeFetch({ patient: PATIENT, summary: SUMMARY, labs: LABS, prescriptions: PRESCRIPTIONS });
  const document = bootDom([...CHROME_IDS, 'page-body', 'patient-heading', 'patient-avatar', 'demographics-region', 'summary-region', 'labs-region', 'prescriptions-region', 'encounters-region']);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const scroller = document.getElementById('labs-region').querySelector('.table-scroll');
  assert(scroller, 'the labs table must live in a horizontal scroll container');
  assertEqualLocal(scroller.getAttribute('role'), 'region', 'the scroll container must be a labelled region');
  assertEqualLocal(scroller.getAttribute('tabindex'), '0', 'the scroll container must be keyboard/remote focusable');
  assertEqualLocal(scroller.getAttribute('aria-label'), 'Lab results', 'the scroll container must carry an accessible name');
  const table = scroller.querySelector('table');
  assert(table.classList.contains('table-stack'), 'data tables must carry the ≤620px card-stacking class');
});

await check('AI answer markdown tables get the same keyboard-focusable scroll-region parity', async () => {
  globalThis.fetch = makeFetch({ me: DOCTOR, ai: AI_MARKDOWN_ANSWER });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const input = document.getElementById('ai-question');
  input.value = 'Кои пациенти имат отклонения в калия?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  const answerScroller = document.getElementById('ai-response-region').querySelector('.ai-md-table-scroll');
  assert(answerScroller, 'the markdown answer table must render in its scroll container');
  assertEqualLocal(answerScroller.getAttribute('role'), 'region', 'the answer table scroller must be a labelled region');
  assertEqualLocal(answerScroller.getAttribute('tabindex'), '0', 'the answer table scroller must be keyboard/remote focusable');
  assertEqualLocal(answerScroller.getAttribute('aria-label'), 'Data table', 'the answer table scroller must carry a constant accessible name');
});

/* ------------------------------------------------------------------ report */

const failed = results.filter((r) => !r.ok);
for (const result of results) {
  process.stdout.write(`${result.ok ? 'PASS' : 'FAIL'} ${result.name}${result.ok ? '' : `: ${result.detail}`}\n`);
}
process.stdout.write(`\n${results.length - failed.length}/${results.length} render-path scenarios passed\n`);
if (failed.length > 0) process.exitCode = 1;
