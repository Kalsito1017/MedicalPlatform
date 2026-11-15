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
 * gating), plus the wave UI patterns: heart loaders replaced atomically by
 * content, stale-while-revalidate (cache hit renders instantly, background
 * refetch updates the DOM only on change), the ⌘K command menu (open, filter,
 * run, Esc + focus restore, bounded patient entries, real shortcut list),
 * optimistic staff mutations with rollback and error toasts, and undo toasts
 * whose Undo action runs the inverse API call, plus the wave UI-B patterns:
 * hero placeholders (heart loader) settling on the ready class, the
 * view-transition helper (usage + instant fallback), swipe-to-dismiss sheets
 * (below-threshold keeps the sheet, above-threshold dismisses WITHOUT
 * confirming), interruptible-animation class re-targeting under rapid
 * toggling, predictive back (LIFO overlay unwinding via popstate/history
 * simulation), idle module warm-up with modulepreload hints, hover-intent
 * patient prefetch (permission-gated, in-memory), and idempotency keys on
 * mutating requests, and the wave UI-C responsive contract: the command
 * palette's arrow-follow scroll ({block:'nearest'} on the NEW active option),
 * wrap-safe long names in patient cards and the patient header,
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
const PATIENT_WRITER = {
  ...DOCTOR,
  permissions: [...DOCTOR.permissions, 'patient:write'],
};
const MESSAGING_DOCTOR = {
  ...DOCTOR,
  permissions: [...DOCTOR.permissions, 'message:read', 'message:write'],
};
const SYSTEM_LOG_EVENT = {
  id: 'audit:11111111-1111-1111-1111-111111111111',
  timestamp: '2026-11-09T10:00:00.123456Z',
  severity: 'info',
  category: 'patients',
  event_type: 'patient.read',
  description: 'Patient record accessed',
  actor_id: 'u1',
  resource_type: 'patient',
  resource_reference: 'p1',
  request_id: 'req-log-1',
  outcome: 'success',
  metadata: {},
};
const SYSTEM_LOG_STAFF = {
  items: [DOCTOR],
  total: 1,
  limit: 100,
  offset: 0,
};
const SYSTEM_LOGS_PAGE = {
  items: [SYSTEM_LOG_EVENT],
  total: 1,
  limit: 50,
  offset: 0,
  counts: { total: 1, info: 1, success: 0, warning: 0, error: 0, security: 0 },
  start: '2026-11-09T09:45:00Z',
  end: '2026-11-09T10:00:00Z',
  timezone: 'UTC',
};
const SYSTEM_LOG_IDS = [
  'page-body',
  'logs-loading',
  'logs-status',
  'logs-connection',
  'logs-last-connected',
  'logs-last-event',
  'logs-stat-total',
  'logs-stat-info',
  'logs-stat-success',
  'logs-stat-warning',
  'logs-stat-error',
  'logs-stat-security',
  'logs-filter-form',
  'logs-severity',
  'logs-category',
  'logs-range',
  'logs-custom',
  'logs-start',
  'logs-end',
  'logs-actor',
  'logs-event-type',
  'logs-request-id',
  'logs-outcome',
  'logs-search',
  'logs-apply',
  'logs-reset',
  'logs-filter-error',
  'logs-timezone',
  'logs-terminal',
  'logs-empty',
  'logs-pause',
  'logs-autoscroll',
  'logs-copy-visible',
  'logs-export',
  'logs-clear',
  'logs-new-indicator',
];
const MESSAGE_ATTACHMENT = {
  id: 'att1',
  filename: 'rota.pdf',
  content_type: 'application/pdf',
  size_bytes: 2048,
  created_at: '2026-11-09T10:00:00Z',
};
const MESSAGE_INBOX = {
  id: 'm1',
  sender_id: 'u9',
  sender_display_name: 'Doc Sender',
  recipient_id: 'u2',
  recipient_display_name: 'Cal Admin',
  subject: 'Rota review',
  body: 'Please review the rota for next week.',
  read_at: null,
  attachment: MESSAGE_ATTACHMENT,
  created_at: '2026-11-09T10:00:00Z',
  updated_at: '2026-11-09T10:00:00Z',
};
const MESSAGE_RECIPIENTS = {
  items: [
    { id: 'u9', display_name: 'Doc Sender', roles: ['doctor'] },
    { id: 'u10', display_name: 'Nurse Person', roles: ['nurse'] },
    { id: 'u11', display_name: 'Admin Person', roles: ['clinical_admin'] },
  ],
};
const MESSAGES_IDS = [
  'page-body',
  'messages-loading',
  'messages-status',
  'messages-count',
  'messages-unread',
  'messages-inbox-tab',
  'messages-inbox-count',
  'messages-sent-tab',
  'messages-sent-count',
  'messages-new',
  'messages-search-form',
  'messages-search',
  'messages-workspace',
  'messages-list-panel',
  'messages-region',
  'messages-detail',
  'messages-profile',
];

const CLIN_ADMIN = {
  id: 'u2',
  email: 'admin@example.com',
  display_name: 'Cal Admin',
  roles: ['clinical_admin'],
  permissions: [
    'patient:read',
    'patient:write',
    'patient:delete',
    'lab:read',
    'prescription:read',
    'staff:read',
    'staff:manage',
    'role:assign',
    'audit:read',
    'analytics:read',
    'message:read',
    'message:write',
    'logs:read',
    'appointment:read',
    'appointment:write',
    'appointment:manage_availability',
    'appointment:export',
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
};
// Fixtures mirror backend/app/schemas/patient.py field names exactly.
const SUMMARY = {
  patient: PATIENT,
  encounters: [{ id: 'e1', encounter_type: 'office_visit', occurred_at: '2026-09-14', summary: 'Routine follow-up.' }],
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
const LAB_TRENDS = {
  patient_id: 'p1',
  window_days: 365,
  series: [
    {
      test_code: 'HGB',
      test_name: 'Haemoglobin',
      unit: 'g/dL',
      ref_low: '12.0',
      ref_high: '17.0',
      points: [
        { collected_at: '2026-08-01', value_num: '11.8', flag: 'low' },
        { collected_at: '2026-09-14', value_num: '13.5', flag: 'normal' },
      ],
      abnormal_count: 1,
    },
    {
      test_code: 'GLU',
      test_name: 'Fasting glucose',
      unit: 'mg/dL',
      ref_low: '70.0',
      ref_high: '99.0',
      points: [
        { collected_at: '2026-07-01', value_num: '104.0', flag: 'high' },
        { collected_at: '2026-09-14', value_num: '92.0', flag: 'normal' },
      ],
      abnormal_count: 1,
    },
  ],
  truncated: false,
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
const ENCOUNTERS = {
  items: [
    {
      id: 'e1',
      patient_id: 'p1',
      occurred_at: '2026-09-14T09:30:00Z',
      encounter_type: 'office_visit',
      summary: 'Routine follow-up.',
      weight_kg: '70.50',
      height_cm: '165.00',
    },
  ],
  total: 1,
  limit: 100,
  offset: 0,
};

/* -------------------------------------------- admin + AI panel fixtures */

const NURSE = {
  id: 'u7',
  email: 'nurse@example.com',
  display_name: 'Nan Nurse',
  roles: ['nurse'],
  permissions: [
    'patient:read',
    'message:read',
    'message:write',
    'lab:read',
    'prescription:read',
    'appointment:read',
  ],
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
  answer: 'The latest haemoglobin is 13.5 g/dL.\n\nIt is within the recorded reference range.',
  used_tools: ['get_patient_lab_results'],
  citations: [{ type: 'lab_result', id: 'LAB-0001' }],
  model: 'model-1',
  ai_generated: true,
};

const AI_GENERAL_ANSWER = {
  answer: 'One patient is on metformin.\n\nThe record consulted is listed below.',
  used_tools: ['search_authorized_patients', 'get_recent_prescriptions'],
  citations: [{ type: 'patient', id: 'p1' }, { type: 'prescription', id: 'rx1' }],
  model: 'model-1',
  ai_generated: true,
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
  model: 'model-1',
  ai_generated: true,
};

/* Conversation history fixtures (docs/API.md "AI assistant" history). */
const minutesAgo = (n) => new Date(Date.now() - n * 60 * 1000).toISOString();
const CONVERSATIONS = {
  items: [
    {
      id: 'c1',
      patient_id: 'p1',
      question: 'What is the latest haemoglobin?',
      answer: 'The latest haemoglobin is 13.5 g/dL.\n\nIt is within the recorded reference range.',
      used_tools: ['get_patient_lab_results'],
      citations: [{ type: 'patient', id: 'p1' }, { type: 'lab_result', id: 'LAB-0001' }],
      model: 'model-1',
      created_at: minutesAgo(5),
    },
    {
      id: 'c2',
      patient_id: null,
      question: 'Which patients are on metformin?',
      answer: 'One patient is on metformin.',
      used_tools: ['find_patients_by_medication'],
      citations: [],
      model: 'model-1',
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
// (docs/API.md "Analytics"): ints everywhere, bounded lists.
const ANALYTICS_DAYS = Array.from(
  { length: 14 },
  (_, index) => `2026-10-${String(index + 1).padStart(2, '0')}`,
);

const ANALYTICS_SECURITY_TREND = ANALYTICS_DAYS.map((day, index) => ({
  day,
  successful_signins: index % 5 === 0 ? 4 : 2,
  failed_signins: index % 6 === 0 ? 1 : 0,
  permission_denials: index % 7 === 0 ? 1 : 0,
  patient_reads: 2 + (index % 3),
  lab_reads: index % 2 === 0 ? 2 : 1,
  prescription_reads: index % 3 === 0 ? 2 : 1,
  ai_requests: index % 4 === 0 ? 2 : 1,
}));

const ANALYTICS_SECURITY_TOTALS = ANALYTICS_SECURITY_TREND.reduce(
  (totals, day) => {
    for (const key of Object.keys(totals)) totals[key] += day[key];
    return totals;
  },
  {
    successful_signins: 0,
    failed_signins: 0,
    permission_denials: 0,
    patient_reads: 0,
    lab_reads: 0,
    prescription_reads: 0,
    ai_requests: 0,
  },
);

const ANALYTICS_APPOINTMENT_TREND = ANALYTICS_DAYS.map((day, index) => ({
  day,
  total: index < 12 ? 1 : 0,
  completed: index < 4 ? 1 : 0,
  cancelled: index === 4 ? 1 : 0,
  no_show: index === 5 ? 1 : 0,
}));

const ANALYTICS_WEEKDAYS = [
  ['Monday', 2],
  ['Tuesday', 3],
  ['Wednesday', 2],
  ['Thursday', 2],
  ['Friday', 2],
  ['Saturday', 1],
  ['Sunday', 0],
].map(([label, count], weekday) => ({ weekday, label, count }));

const ANALYTICS_HOURS = Array.from({ length: 24 }, (_, hour) => ({
  hour,
  count: hour >= 8 && hour <= 16 ? (hour % 3) + 1 : 0,
}));

// Mirrors the documented GET /analytics/summary payload exactly
// (docs/API.md "Analytics"): ints, bounded lists, zero-filled series.
const ANALYTICS_SUMMARY = {
  generated_at: '2026-10-14T18:15:00Z',
  range: {
    start: '2026-10-01',
    end: '2026-10-14',
    days: 14,
    previous_start: '2026-09-17',
    previous_end: '2026-09-30',
    timezone: 'UTC',
    appointment_timezone: 'Europe/Sofia',
  },
  counts: {
    patients: 45,
    encounters: 167,
    labs: 415,
    prescriptions: 104,
    active_prescriptions: 62,
    diagnoses_active: 90,
    staff_active: 3,
    staff_inactive: 1,
    ai_conversations: 12,
  },
  activity: {
    new_patients: 8,
    encounters: 24,
    lab_results: 58,
    prescriptions_written: 9,
    appointments: 12,
    ai_requests: 12,
    successful_signins: ANALYTICS_SECURITY_TOTALS.successful_signins,
    failed_signins: ANALYTICS_SECURITY_TOTALS.failed_signins,
    permission_denials: ANALYTICS_SECURITY_TOTALS.permission_denials,
  },
  previous_activity: {
    new_patients: 5,
    encounters: 20,
    lab_results: 50,
    prescriptions_written: 11,
    appointments: 10,
    ai_requests: 9,
    successful_signins: 26,
    failed_signins: 2,
    permission_denials: 2,
  },
  comparisons: {
    new_patients: { current: 8, previous: 5, change_pct: 60.0 },
    encounters: { current: 24, previous: 20, change_pct: 20.0 },
    lab_results: { current: 58, previous: 50, change_pct: 16.0 },
    prescriptions_written: { current: 9, previous: 11, change_pct: -18.2 },
    appointments: { current: 12, previous: 10, change_pct: 20.0 },
    ai_requests: { current: 12, previous: 9, change_pct: 33.3 },
  },
  patients: {
    new_patients: 8,
    returning_patients: 14,
    active_patients: 22,
    encounters: 24,
    avg_encounters_per_day: 1.7,
    by_encounter_type: [
      { encounter_type: 'office_visit', count: 14 },
      { encounter_type: 'follow_up', count: 6 },
      { encounter_type: 'telehealth', count: 4 },
    ],
    trend: ANALYTICS_DAYS.map((day, index) => ({ day, encounters: index % 4 })),
    timezone: 'UTC',
  },
  appointments: {
    total: 12,
    by_status: { scheduled: 4, confirmed: 2, completed: 4, cancelled: 1, no_show: 1 },
    by_visit_type: [
      { visit_type: 'Consultation', count: 5 },
      { visit_type: 'Follow-up', count: 3 },
      { visit_type: 'Analytics Probe', count: 2 },
    ],
    by_weekday: ANALYTICS_WEEKDAYS,
    by_hour: ANALYTICS_HOURS,
    trend: ANALYTICS_APPOINTMENT_TREND,
    utilization: {
      booked_minutes: 360,
      available_minutes: 720,
      utilization_pct: 50.0,
      doctors_with_schedule: 2,
    },
    timezone: 'Europe/Sofia',
  },
  labs: {
    total: 58,
    status_mix: { final: 50, preliminary: 6, pending: 2 },
    abnormal_rate: { abnormal: 17, total: 50, pct: 34.0 },
    classification: {
      normal: 33,
      high: 11,
      low: 6,
      unclassified: 8,
      missing_reference_ranges: 6,
      reference_coverage_pct: 86.2,
    },
    top_abnormal_tests: [
      { test_code: 'GLU', test_name: 'Glucose, fasting', abnormal_count: 5 },
      { test_code: 'HBA1C', test_name: 'HbA1c', abnormal_count: 4 },
      { test_code: 'K', test_name: 'Potassium', abnormal_count: 3 },
      { test_code: 'HGB', test_name: 'Hemoglobin', abnormal_count: 2 },
    ],
    trend: ANALYTICS_DAYS.map((day, index) => ({
      day,
      total: 4 + (index % 3),
      evaluable: 3 + (index % 3),
      abnormal: index % 4,
    })),
    timezone: 'UTC',
  },
  prescriptions: {
    total: 9,
    by_status: { active: 5, completed: 2, stopped: 1, cancelled: 1 },
    lifetime_by_status: { active: 62, completed: 22, stopped: 12, cancelled: 8 },
    trend: ANALYTICS_DAYS.map((day, index) => ({ day, prescriptions: index % 3 })),
    timezone: 'UTC',
    date_basis: 'start_date',
  },
  security: {
    totals: ANALYTICS_SECURITY_TOTALS,
    trend: ANALYTICS_SECURITY_TREND,
    timezone: 'UTC',
  },
  ai_usage: {
    total_requests: 12,
    successful: 10,
    failed: 1,
    denied: 1,
    success_rate: 90.9,
    top_tools: [
      { tool: 'get_patient_summary', calls: 9, failures: 1 },
      { tool: 'get_patient_lab_results', calls: 7, failures: 0 },
      { tool: 'find_patients_by_medication', calls: 4, failures: 0 },
    ],
    latency: {
      available: false,
      average_ms: null,
      reason: 'Per-request AI latency is not persisted.',
    },
    voice: {
      available: false,
      reason: 'Voice transcription requests are intentionally ephemeral.',
    },
    timezone: 'UTC',
  },
  data_quality: {
    checks: [
      {
        key: 'lab_missing_reference_ranges',
        label: 'Lab results missing reference ranges',
        count: 6,
        denominator: 58,
        scope: 'period',
        kind: 'classification',
        note: 'A missing range cannot be classified.',
      },
      {
        key: 'lab_unclassified',
        label: 'Unclassified lab results',
        count: 8,
        denominator: 58,
        scope: 'period',
        kind: 'classification',
        note: 'Text-only or missing usable bound.',
      },
      {
        key: 'patient_missing_date_of_birth',
        label: 'Patients without a recorded date of birth',
        count: 2,
        denominator: 45,
        scope: 'current',
        kind: 'optional_metadata',
        note: 'Optional demographic field; not a data error.',
      },
      {
        key: 'patient_missing_blood_type',
        label: 'Patients without a recorded blood type',
        count: 7,
        denominator: 45,
        scope: 'current',
        kind: 'optional_metadata',
        note: 'Optional clinical field.',
      },
      {
        key: 'encounter_missing_practitioner',
        label: 'Encounters without a practitioner',
        count: 1,
        denominator: 24,
        scope: 'period',
        kind: 'optional_metadata',
        note: 'Practitioner attribution is nullable.',
      },
      {
        key: 'appointment_missing_visit_type',
        label: 'Appointments without a visit type',
        count: 2,
        denominator: 12,
        scope: 'period',
        kind: 'optional_metadata',
        note: 'Visit type is optional scheduling metadata.',
      },
    ],
  },
  system_health: {
    backend: 'operational',
    database: 'connected',
    api_error_rate: {
      available: false,
      rate_pct: null,
      reason: 'API error rates are not persisted in the audit stream.',
    },
    llm: {
      configured: true,
      availability: 'not_monitored',
      note: 'No persisted provider-health probe; AI ask outcomes are reported above.',
    },
    voice: {
      configured: true,
      availability: 'not_monitored',
      note: 'Transcription requests are intentionally not audited or persisted.',
    },
  },
  staff: {
    active_total: 3,
    inactive_total: 1,
    by_role: [
      { role: 'doctor', count: 2 },
      { role: 'nurse', count: 2 },
      { role: 'clinical_admin', count: 1 },
    ],
  },
};

const ANALYTICS_IDS = [
  'page-body',
  'analytics-loading',
  'analytics-generated-at',
  'analytics-period-label',
  'analytics-refresh',
  'analytics-status',
  'analytics-error-region',
  'analytics-filter-form',
  'analytics-range',
  'analytics-start',
  'analytics-end',
  'analytics-custom',
  'analytics-reset',
  'analytics-range-error',
  'analytics-filter-summary',
  'kpi-region',
  'patients-region',
  'appointments-region',
  'labs-region',
  'prescriptions-region',
  'security-region',
  'ai-region',
  'quality-region',
  'health-region',
  'staff-region',
];

const AI_PAGE_IDS = [
  'page-body',
  'ai-workspace',
  'ai-rail-backdrop',
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
  'ai-context-label',
  'ai-history',
  'ai-history-list',
  'ai-history-clear',
  'ai-history-search',
  'ai-history-status',
  'ai-history-toggle',
  'ai-history-close',
  'ai-new-conversation',
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
  'ai-patient-context',
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
  'lab-trends-region',
  'labs-region',
  'prescriptions-region',
  'encounters-region',
  'outcomes-region',
  'print-status',
  'print-record',
  'remove-patient',
  ...AI_IDS,
];

const PRINT_BUTTON_FIXTURE = {
  id: 'print-patient-record',
  tag: 'button',
  attrs: { type: 'button', 'aria-describedby': 'print-status' },
};

// Shared patient-tab DOM contract for tests that need to open a hidden tab.
// Hidden tabs load lazily (Labs/Prescriptions/AI history), so tests must
// activate the matching tab before asserting its rendered data.
const PATIENT_TAB_FIXTURE = [
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
      {
        id: 'tab-prescriptions',
        tag: 'button',
        class: 'tab',
        attrs: { role: 'tab', 'aria-controls': 'panel-prescriptions', 'aria-selected': 'false', tabindex: '-1' },
        text: 'Prescriptions',
      },
      {
        id: 'tab-encounters',
        tag: 'button',
        class: 'tab',
        attrs: { role: 'tab', 'aria-controls': 'panel-encounters', 'aria-selected': 'false', tabindex: '-1' },
        text: 'Encounters',
      },
      {
        id: 'tab-outcomes',
        tag: 'button',
        class: 'tab',
        attrs: { role: 'tab', 'aria-controls': 'panel-outcomes', 'aria-selected': 'false', tabindex: '-1' },
        text: 'Outcomes',
      },
      {
        id: 'tab-ai-patient',
        tag: 'button',
        class: 'tab',
        attrs: { role: 'tab', 'aria-controls': 'panel-ai-patient', 'aria-selected': 'false', tabindex: '-1' },
        text: 'AI Patient',
      },
    ],
  },
  { id: 'panel-summary', class: 'tab-panel', attrs: { role: 'tabpanel' } },
  { id: 'panel-labs', class: 'tab-panel', attrs: { role: 'tabpanel', hidden: '' } },
  { id: 'panel-prescriptions', class: 'tab-panel', attrs: { role: 'tabpanel', hidden: '' } },
  { id: 'panel-encounters', class: 'tab-panel', attrs: { role: 'tabpanel', hidden: '' } },
  { id: 'panel-outcomes', class: 'tab-panel', attrs: { role: 'tabpanel', hidden: '' } },
  { id: 'panel-ai-patient', class: 'tab-panel', attrs: { role: 'tabpanel', hidden: '' } },
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

function makeFetch({ me = DOCTOR, patients, patient, summary, labs, trend = LAB_TRENDS, prescriptions, encounters = ENCOUNTERS, staff, audit, ai, conversations, analytics, messages, messageRecipients, messageCounts, routes = [], calls = [] }) {
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
      const isJsonBody = typeof init.body === 'string';
      const payload = typeof route.response === 'function'
        ? route.response({
            path,
            method,
            init,
            body: isJsonBody && init.body ? JSON.parse(init.body) : null,
            formData: !isJsonBody && init.body ? init.body : null,
          })
        : route.response;
      return respond(payload);
    }

    if (path.endsWith('/auth/me')) {
      return respond(me);
    }
    if (path.endsWith('/auth/login')) return json(200, me);
    if (path.endsWith('/auth/logout')) return json(204, null);
    if (/\/patients\/[^/]+\/labs\/trends$/.test(path)) return respond(trend);
    if (/\/patients\/[^/]+\/labs$/.test(path)) return respond(labs);
    if (/\/patients\/[^/]+\/prescriptions$/.test(path)) return respond(prescriptions);
    if (/\/patients\/[^/]+\/encounters$/.test(path)) return respond(encounters);
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
    if (path.endsWith('/messages/counts') && method === 'GET') {
      const counts = messageCounts || {
        inbox_total: Number.isFinite(messages && messages.total) ? messages.total : 0,
        unread_total: messages && Array.isArray(messages.items)
          ? messages.items.filter((item) => !item.read_at).length
          : 0,
        sent_total: 0,
      };
      return respond(counts);
    }
    if (path.endsWith('/messages/recipients') && method === 'GET') {
      if (messageRecipients === undefined) throw new Error(`unstubbed request: ${method} ${url}`);
      return respond(messageRecipients);
    }
    if (path.endsWith('/messages') && method === 'GET') {
      if (messages === undefined) throw new Error(`unstubbed request: ${method} ${url}`);
      return respond(messages);
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
        { id: 'p2', display_name: 'Bob Test', mrn: 'MRN-0002', birth_date: '1975-01-20', sex: 'M' },
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
  assertContains(region, 'Showing 1\u20132 of 42 patients', 'pagination summary wrong');
  const link = document.getElementById('patients-region').querySelector('a');
  assertEqualLocal(link && link.getAttribute('href'), 'patient.html?id=p1', 'patient link missing');
  assertContains(textOf(document.getElementById('patients-status')), '42 patients found', 'status not announced');

  const nav = textOf(document.getElementById('site-nav'));
  assertContains(nav, 'Patients', 'nav missing Patients');
  assert(!nav.includes('Staff Management'), 'doctor must not see Staff Management nav');
});

await check('doctor creates a patient and nurse does not get the control', async () => {
  const calls = [];
  const created = [];
  const newPatient = {
    ...PATIENT,
    id: 'p-new',
    mrn: 'MRN-NEW',
    display_name: 'New Patient',
    given_name: 'New',
    family_name: 'Patient',
    date_of_birth: '1992-03-04',
    sex: 'female',
    blood_type: 'O+',
  };
  globalThis.fetch = makeFetch({
    me: PATIENT_WRITER,
    calls,
    patients: { items: [PATIENT], total: 1, limit: 20, offset: 0 },
    routes: [{
      method: 'POST',
      match: '/api/v1/patients',
      response: ({ body }) => {
        created.push(body);
        return { status: 201, ok: true, body: newPatient };
      },
    }],
  });
  const pageIds = [...CHROME_IDS, 'patient-new', 'patient-search-form', 'patient-search', 'patient-search-clear', 'patients-status', 'patients-region'];
  const document = bootDom(pageIds);
  await freshPage('patients.js');
  await settle();

  const newButton = document.getElementById('patient-new');
  assertEqualLocal(newButton.hidden, false, 'doctor with patient:write must see Add patient');
  await newButton.dispatch('click');
  await settle();
  assertContains(textOf(document.getElementById('app-dialog-title')), 'Add patient', 'dialog title missing');

  await clickButton(document.getElementById('app-dialog-actions'), 'Create patient');
  assertContains(textOf(document.getElementById('new-patient-error')), 'both a given name', 'required-name validation missing');
  assertEqualLocal(created.length, 0, 'invalid form must not POST');

  document.getElementById('new-patient-given').value = 'New';
  document.getElementById('new-patient-family').value = 'Patient';
  document.getElementById('new-patient-dob').value = '1992-03-04';
  document.getElementById('new-patient-sex').value = 'female';
  document.getElementById('new-patient-blood-type').value = 'O+';
  await clickButton(document.getElementById('app-dialog-actions'), 'Create patient');
  await settle();
  assertEqualLocal(created.length, 1, 'valid form must POST once');
  assertEqualLocal(created[0].given_name, 'New', 'given name missing from request');
  assertEqualLocal(created[0].family_name, 'Patient', 'family name missing from request');
  assertEqualLocal(created[0].date_of_birth, '1992-03-04', 'date of birth missing from request');
  assertEqualLocal(created[0].sex, 'female', 'sex missing from request');
  assertEqualLocal(created[0].blood_type, 'O+', 'blood type missing from request');
  assertEqualLocal(globalThis.location.redirectedTo, 'patient.html?id=p-new', 'must open the new record');

  globalThis.fetch = makeFetch({
    me: NURSE,
    calls: [],
    patients: { items: [PATIENT], total: 1, limit: 20, offset: 0 },
  });
  const nurseDocument = bootDom(pageIds);
  await freshPage('patients.js');
  await settle();
  assertEqualLocal(nurseDocument.getElementById('patient-new').hidden, true, 'nurse must not see Add patient');
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
  assertContains(region, 'No patients match your search', 'empty state copy missing');
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
  const document = bootDom([...CHROME_IDS, 'page-body', 'patient-heading', 'patient-avatar', 'demographics-region', 'summary-region', 'lab-trends-region', 'labs-region', 'prescriptions-region', 'encounters-region', ...PATIENT_TAB_FIXTURE]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  assertContains(textOf(document.getElementById('patient-heading')), 'Ann Test', 'heading not set');
  await document.getElementById('tab-labs').dispatch('click');
  await settle();
  const trendsHost = document.getElementById('lab-trends-region');
  const trends = textOf(trendsHost);
  assertContains(trends, 'Lab Results Summary', 'lab summary heading missing');
  assertContains(trends, 'Haemoglobin (HGB) trend values', 'lab trend hidden table missing');
  assertContains(trends, 'Fasting glucose (GLU) trend values', 'second lab trend hidden table missing');
  assertContains(trends, 'Target: 12\u201317 g/dL', 'reference target missing');
  assertContains(trends, 'Target: 70\u201399 mg/dL', 'second reference target missing');
  assertContains(trends, '13.5', 'lab value label missing');
  assertContains(trends, '92', 'second lab value label missing');
  assertEqualLocal(trendsHost.querySelectorAll('.lab-chart').length, 2, 'expected one chart card per test');
  assertEqualLocal(trendsHost.querySelectorAll('.chart-range-band').length, 2, 'expected a reference band per chart');
  assert(trendsHost.querySelectorAll('.lab-chart__value').length >= 4, 'value labels must render beside points');
  const header = textOf(document.getElementById('demographics-region'));
  assertContains(header, 'MRN-0001', 'MRN missing');
  assertEqualLocal(
    document.getElementById('demographics-region').querySelectorAll('.badge').length,
    0,
    'no data-origin badge may render in the patient header',
  );
  assertContains(header, ui.formatDate('1980-05-04'), 'birth date not formatted');

  const summary = textOf(document.getElementById('summary-region'));
  assertContains(summary, 'Amoxicillin (500 mg, Oral)', 'active medication missing');
  assertContains(summary, 'No active problems recorded.', 'problems empty state missing');

  const encounters = textOf(document.getElementById('encounters-region'));
  assertContains(encounters, 'Routine follow-up.', 'encounter missing');
  assertContains(encounters, 'office_visit', 'encounter type badge missing');

  const labs = textOf(document.getElementById('labs-region'));
  assertContains(labs, 'Haemoglobin', 'lab name missing');
  assertContains(labs, '13.5', 'lab value missing');
  assertContains(labs, 'g/dL', 'lab unit missing');
  assertContains(labs, '12.0\u201317.0', 'reference range missing');
  assertContains(labs, ui.formatDate('2026-09-14'), 'collected date missing');

  await document.getElementById('tab-prescriptions').dispatch('click');
  await settle();
  const rx = textOf(document.getElementById('prescriptions-region'));
  assertContains(rx, 'Amoxicillin', 'medication missing');
  assertContains(rx, '500 mg', 'dose missing');
  assertContains(rx, 'Oral', 'route missing');
  assertContains(rx, 'Three times daily', 'frequency missing');
});

await check('patient summary shows last visit, vitals and treatment response; Outcomes tab charts them', async () => {
  const enrichedSummary = {
    ...SUMMARY,
    last_visit: {
      id: 'e1',
      occurred_at: '2026-09-14T09:30:00Z',
      encounter_type: 'office_visit',
      summary: 'Routine follow-up.',
      weight_kg: '70.50',
      height_cm: '165.00',
      systolic_bp: 128,
      diastolic_bp: 78,
      heart_rate: 72,
    },
    visit_count: 6,
    vitals: {
      weight_kg: '70.50',
      height_cm: '165.00',
      measured_at: '2026-09-14T09:30:00Z',
      systolic_bp: 128,
      diastolic_bp: 78,
      heart_rate: 72,
      oxygen_saturation: 98,
    },
    medication_responses: [{
      prescription_id: 'rx1',
      medication: 'Metformin',
      status: 'active',
      started_on: '2026-02-10',
      metric_code: 'HBA1C',
      metric_name: 'HbA1c',
      unit: '%',
      baseline: { measured_at: '2026-01-18T08:00:00Z', value: '9.4' },
      follow_up: { measured_at: '2026-06-02T08:00:00Z', value: '7.2' },
      current: { measured_at: '2026-10-14T08:00:00Z', value: '7.0' },
      delta: '-2.4',
      trend: 'improved',
      points: [
        { measured_at: '2026-01-18T08:00:00Z', value: '9.4' },
        { measured_at: '2026-06-02T08:00:00Z', value: '7.2' },
        { measured_at: '2026-10-14T08:00:00Z', value: '7.0' },
      ],
    }],
  };
  const outcomeEncounters = {
    items: [
      { ...ENCOUNTERS.items[0], systolic_bp: 128, diastolic_bp: 78, heart_rate: 72 },
      { id: 'e2', patient_id: 'p1', occurred_at: '2026-06-02T09:00:00Z', encounter_type: 'chronic disease review', summary: 'Review.', weight_kg: '73.00', systolic_bp: 146, diastolic_bp: 92, heart_rate: 80 },
      { id: 'e3', patient_id: 'p1', occurred_at: '2026-02-10T09:00:00Z', encounter_type: 'chronic disease review', summary: 'Baseline.', weight_kg: '75.00', systolic_bp: 158, diastolic_bp: 98, heart_rate: 84 },
    ],
    total: 3,
    limit: 100,
    offset: 0,
  };
  globalThis.fetch = makeFetch({
    patient: PATIENT,
    summary: enrichedSummary,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    encounters: outcomeEncounters,
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS, ...PATIENT_TAB_FIXTURE]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const summary = textOf(document.getElementById('summary-region'));
  assertContains(summary, 'Last visit', 'last visit block missing');
  assertContains(summary, '128/78', 'BP summary missing');
  assertContains(summary, 'Metformin', 'treatment response missing');
  assertContains(summary, 'HbA1c: 9.4', 'baseline value missing');
  assertContains(summary, 'Improved', 'trend label missing');

  await document.getElementById('tab-outcomes').dispatch('click');
  await settle();
  const outcomes = textOf(document.getElementById('outcomes-region'));
  assertContains(outcomes, 'Blood pressure', 'BP chart missing');
  assertContains(outcomes, 'Weight', 'weight chart missing');
  assertContains(outcomes, 'Medication response', 'response section missing');
  assertContains(outcomes, 'Recent visits', 'visit history missing');
  assertContains(outcomes, '158/98', 'BP visit reading missing');
  assert(document.getElementById('outcomes-region').querySelectorAll('.chart').length >= 2, 'vitals charts missing');
});

await check('patient detail isolates a 403 lab section from the rest', async () => {
  globalThis.fetch = makeFetch({
    patient: PATIENT,
    summary: SUMMARY,
    labs: { status: 403 },
    prescriptions: PRESCRIPTIONS,
  });
  const document = bootDom([...CHROME_IDS, 'page-body', 'patient-heading', 'patient-avatar', 'demographics-region', 'summary-region', 'labs-region', 'prescriptions-region', 'encounters-region', ...PATIENT_TAB_FIXTURE]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  await document.getElementById('tab-labs').dispatch('click');
  await settle();
  assertContains(textOf(document.getElementById('labs-region')), 'You do not have permission', '403 copy missing');
  await document.getElementById('tab-prescriptions').dispatch('click');
  await settle();
  assertContains(textOf(document.getElementById('prescriptions-region')), 'Amoxicillin', 'prescriptions should still render');
});

await check('admin removes a patient while doctor and nurse do not get the control', async () => {
  const deletions = [];
  const routes = [{
    method: 'DELETE',
    match: '/api/v1/patients/p1',
    response: () => {
      deletions.push('p1');
      return { status: 204, ok: true };
    },
  }];
  globalThis.fetch = makeFetch({
    me: CLIN_ADMIN,
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    routes,
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS, ...PATIENT_TAB_FIXTURE]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const remove = document.getElementById('remove-patient');
  assertEqualLocal(remove.hidden, false, 'admin must see Remove patient');
  remove.dispatch('click', {});
  await settle(30);
  assertContains(textOf(document.getElementById('app-dialog-body')), 'will disappear from search', 'remove confirmation missing');
  await clickButton(document.getElementById('app-dialog-actions'), 'Remove patient');
  await settle(60);
  assertEqualLocal(deletions.length, 1, 'DELETE patient request missing');
  assertEqualLocal(globalThis.location.redirectedTo, 'patients.html', 'admin must return to the patient list');

  for (const user of [DOCTOR, NURSE]) {
    globalThis.fetch = makeFetch({
      me: user,
      patient: PATIENT,
      summary: SUMMARY,
      labs: LABS,
      prescriptions: PRESCRIPTIONS,
    });
    const other = bootDom([...CHROME_IDS, ...PATIENT_IDS, ...PATIENT_TAB_FIXTURE]);
    globalThis.location.search = '?id=p1';
    await freshPage('patient.js');
    await settle();
    assertEqualLocal(other.getElementById('remove-patient').hidden, true, `${user.roles[0]} must not see Remove patient`);
  }
});

await check('patient hidden tabs defer their data requests until opened', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    conversations: CONVERSATIONS,
  });
  const document = bootDom([
    ...CHROME_IDS,
    ...PATIENT_IDS,
    ...PATIENT_TAB_FIXTURE,
  ]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const called = (fragment) => calls.some((call) => call.url.includes(fragment));
  assert(!called('/labs'), 'labs must not be fetched before the Labs tab opens');
  assert(!called('/prescriptions'), 'prescriptions must not be fetched before the Prescriptions tab opens');
  assert(!called('/ai/conversations'), 'AI history must not be fetched before the AI Patient tab opens');
  assert(!called('/encounters'), 'outcomes must not be fetched before the Outcomes tab opens');

  await document.getElementById('tab-labs').dispatch('click');
  await settle();
  assert(called('/labs'), 'Labs tab must fetch the lab table');
  assert(called('/labs/trends'), 'Labs tab must fetch the trend chart');

  await document.getElementById('tab-prescriptions').dispatch('click');
  await settle();
  assert(called('/prescriptions'), 'Prescriptions tab must fetch on first open');

  await document.getElementById('tab-outcomes').dispatch('click');
  await settle();
  assert(called('/summary'), 'Outcomes tab must reuse the summary response data');
  assert(called('/encounters'), 'Outcomes tab must fetch encounter vitals on first open');

  await document.getElementById('tab-ai-patient').dispatch('click');
  await settle();
  assert(called('/ai/conversations'), 'AI Patient tab must fetch patient-filtered history on first open');
});

await check('print action prepares a complete record without opening any tab', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    calls,
    patient: PATIENT,
    summary: SUMMARY,
    labs: {
      ...LABS,
      items: [
        ...LABS.items,
        {
          id: 'l2',
          test_code: 'GLU',
          test_name: 'Fasting glucose',
          value_num: '104.0',
          unit: 'mg/dL',
          ref_low: '70.0',
          ref_high: '99.0',
          status: 'final',
          collected_at: '2026-08-01T07:30:00Z',
        },
      ],
      total: 2,
    },
    prescriptions: PRESCRIPTIONS,
    encounters: ENCOUNTERS,
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS, ...PATIENT_TAB_FIXTURE, PRINT_BUTTON_FIXTURE]);
  globalThis.location.search = '?id=p1';
  const previousPrint = globalThis.print;
  let printed = 0;
  globalThis.print = () => { printed += 1; };

  await freshPage('patient.js');
  await settle();

  const button = document.getElementById('print-patient-record');
  assert(button, 'print button missing');
  assertEqualLocal(button.tagName, 'BUTTON', 'print action must be a real button');
  assertEqualLocal(button.getAttribute('type'), 'button', 'print action must not submit');
  assertEqualLocal(button.getAttribute('aria-describedby'), 'print-status', 'print action must describe its status');
  assert(!button.hidden, 'print button must be visible with patient:read');
  assert(!calls.some((call) => call.url.includes('/encounters')), 'print data must not load before the click');

  await button.dispatch('click');
  for (let attempt = 0; attempt < 50 && printed === 0; attempt += 1) {
    await settle(10);
  }
  assertEqualLocal(printed, 1, 'native print must be called once');

  const report = textOf(document.getElementById('print-record'));
  for (const expected of [
    'Patient Medical Record',
    'Ann Test',
    'MRN-0001',
    'Patient Information',
    'Patient Summary',
    'Encounter History',
    'Laboratory Results',
    'Prescriptions',
    'Routine follow-up.',
    'Haemoglobin',
    'HIGH \u2191',
    'Amoxicillin',
  ]) {
    assertContains(report, expected, `print report missing ${expected}`);
  }
  assertContains(textOf(document.getElementById('print-status')), 'Print dialog opened', 'print status missing');

  if (previousPrint === undefined) delete globalThis.print;
  else globalThis.print = previousPrint;
});

await check('print failure names the failed section and never opens print', async () => {
  globalThis.fetch = makeFetch({
    patient: PATIENT,
    summary: SUMMARY,
    labs: { status: 500 },
    prescriptions: PRESCRIPTIONS,
    encounters: ENCOUNTERS,
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS, ...PATIENT_TAB_FIXTURE, PRINT_BUTTON_FIXTURE]);
  globalThis.location.search = '?id=p1';
  const previousPrint = globalThis.print;
  let printed = 0;
  globalThis.print = () => { printed += 1; };

  await freshPage('patient.js');
  await settle();
  await document.getElementById('print-patient-record').dispatch('click');
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (textOf(document.getElementById('print-status')).includes('laboratory results')) break;
    await settle(10);
  }

  assertEqualLocal(printed, 0, 'print must not open when a section fails');
  const status = textOf(document.getElementById('print-status'));
  assertContains(status, 'laboratory results', 'failed section must be named');
  assertContains(status, 'not printed', 'failure must state the report was not printed');
  assertContains(status, 'Retry', 'failure must offer retry');

  if (previousPrint === undefined) delete globalThis.print;
  else globalThis.print = previousPrint;
});

await check('print action is hidden without patient:read', async () => {
  globalThis.fetch = makeFetch({
    me: NO_PATIENT_READ,
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    encounters: ENCOUNTERS,
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS, ...PATIENT_TAB_FIXTURE, PRINT_BUTTON_FIXTURE]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();
  assertEqualLocal(document.getElementById('print-patient-record').hidden, true, 'print must be hidden without patient:read');
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

await check('AI panel renders the answer with tools and citations', async () => {
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
  assertContains(region, 'The latest haemoglobin is 13.5 g/dL.', 'answer text missing');
  assertContains(region, 'It is within the recorded reference range.', 'second answer paragraph missing');
  assert(!region.includes('AI-generated'), 'AI-generated badge must not render');
  assertEqualLocal(
    document.getElementById('ai-response-region').querySelectorAll('.badge').length,
    0,
    'the answer must not carry an AI-generated badge',
  );
  assertEqualLocal(
    document.getElementById('ai-response-region').querySelectorAll('.alert').length,
    0,
    'no disclaimer note may render in the answer turn',
  );
  assertContains(region, 'Sources consulted', 'sources label missing');
  assertContains(region, 'get_patient_lab_results', 'tool chip missing');
  assertContains(region, 'Lab result LAB-0001', 'citation label missing');
  assert(!region.includes('Model:'), 'model name must not render');
  assert(!region.includes('model-1'), 'model identifier must not render');
  assertEqualLocal(
    document.getElementById('ai-response-region').querySelector('.heart-loader__img'),
    null,
    'the rendered answer must replace the heart loader',
  );
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
  assertContains(text, 'AI Patient unavailable', 'unavailable title missing');
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

  const pendingAnswer = document.getElementById('ai-response-region');
  assertContains(textOf(pendingAnswer), 'Consulting the assistant', 'loading state missing');
  assert(pendingAnswer.querySelector('.heart-loader__img'), 'the pending answer must show the heart loader');
  assert(pendingAnswer.querySelector('.heart-loader--lg'), 'the answer region takes the lg heart');
  assertEqualLocal(pendingAnswer.querySelector('.ai-avatar'), null, 'loading state must not show the assistant plus avatar');
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
  assertContains(region, 'One patient is on metformin.', 'answer text missing');
  assert(!region.includes('AI-generated'), 'AI-generated badge must not render');
  assertEqualLocal(
    document.getElementById('ai-response-region').querySelectorAll('.alert').length,
    0,
    'no disclaimer note may render in the answer turn',
  );
  assertContains(region, 'Sources consulted', 'sources label missing');
  assertContains(region, 'search_authorized_patients', 'tool chip missing');
  assertContains(region, 'Records consulted', 'citations label missing');
  assertContains(region, 'Patient p1', 'patient citation label missing');
  assertContains(region, 'Prescription rx1', 'prescription citation label missing');
  assert(!region.includes('Model:'), 'model name must not render');
  assert(!region.includes('model-1'), 'model identifier must not render');

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
        { type: 'lab_result', id: 'LAB-0001' },
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
  assertContains(textOf(region), 'Lab result LAB-0001', 'record reference must still render');
});

await check('legacy payload keys (provenance, disclaimer) are ignored gracefully', async () => {
  // The de-marked contract drops `provenance` from clinical payloads and
  // `disclaimer` from the ask response. A stale backend may still send them
  // (or any other extra keys): rendering must neither crash nor surface them.
  globalThis.fetch = makeFetch({
    patient: { ...PATIENT, provenance: 'legacy-tag', extra_field: 'ignored' },
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    ai: { ...AI_ANSWER, disclaimer: 'OBSOLETE-COPY-MARKER', unused_key: 1 },
  });
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const header = textOf(document.getElementById('demographics-region'));
  assert(!header.includes('legacy-tag'), 'a legacy provenance value must never render');
  assertContains(header, 'MRN-0001', 'the patient header must still render');

  const input = document.getElementById('ai-question');
  input.value = 'What is the latest haemoglobin?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  const region = textOf(document.getElementById('ai-response-region'));
  assert(!region.includes('OBSOLETE-COPY-MARKER'), 'a legacy disclaimer key must never render');
  assertContains(region, 'The latest haemoglobin is 13.5 g/dL.', 'the answer must still render');
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
  input.value = 'Which patients have the most recent lab results?';
  await input.dispatch('input');
  await document.getElementById('ai-question-form').dispatch('submit');
  await settle();

  const pendingAnswer = document.getElementById('ai-response-region');
  assertContains(textOf(pendingAnswer), 'Consulting the assistant', 'loading state missing');
  assert(pendingAnswer.querySelector('.heart-loader__img'), 'the pending answer must show the heart loader');
  assert(pendingAnswer.querySelector('.heart-loader--lg'), 'the answer region takes the lg heart');
  assertEqualLocal(pendingAnswer.querySelector('.ai-avatar'), null, 'loading state must not show the assistant plus avatar');
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
  assertEqualLocal(document.getElementById('ai-history').hidden, true, 'conversation rail must hide without patient:read');
  assertEqualLocal(document.getElementById('ai-history-toggle').hidden, true, 'mobile rail toggle must hide without patient:read');
  assert(document.getElementById('ai-workspace').classList.contains('ai-workspace--no-rail'), 'denied page must use a single column');
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
  assertContains(textOf(document.getElementById('ai-response-region')), 'One patient is on metformin.', 'answer missing');
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

await check('AI page conversation rail selects saved answers, filters, and clears behind a confirm dialog', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({ calls, me: DOCTOR, conversations: CONVERSATIONS });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const list = document.getElementById('ai-history-list');
  const railItems = list.querySelectorAll('.ai-rail__item');
  assertEqualLocal(railItems.length, 2, 'both conversations must render in the rail');
  const railText = textOf(list);
  assertContains(railText, 'What is the latest haemoglobin?', 'newest question missing');
  assertContains(railText, 'Which patients are on metformin?', 'older question missing');
  assertContains(railText, 'Patient', 'patient mode badge missing');
  assertContains(railText, 'General', 'general mode badge missing');
  assertContains(railText, 'minutes ago', 'relative timestamp missing');
  assertContains(railText, 'get_patient_lab_results', 'tool name missing from the rail');
  assertEqualLocal(list.querySelectorAll('.ai-history__detail').length, 0, 'the rail must not render inline details');

  const firstButton = list.querySelectorAll('.ai-rail__conversation')[0];
  await firstButton.dispatch('click');
  const region = document.getElementById('ai-response-region');
  assertContains(textOf(region), 'What is the latest haemoglobin?', 'selected question missing from the main pane');
  assertContains(textOf(region), 'The latest haemoglobin is 13.5 g/dL.', 'selected answer missing');
  assertContains(textOf(region), 'Records consulted', 'selected citations label missing');
  assertContains(textOf(region), 'Lab result LAB-0001', 'selected citation missing');
  assert(!textOf(region).includes('AI-generated'), 'selected turn must not carry an AI-generated label');
  assert(!textOf(region).includes('Model:'), 'selected turn must not carry a model line');
  assert(region.querySelectorAll('a').map((a) => a.getAttribute('href')).includes('patient.html?id=p1'),
    'patient citations must link to the record');
  const activeButton = list.querySelectorAll('.ai-rail__conversation')[0];
  assertEqualLocal(activeButton.getAttribute('aria-current'), 'true', 'selected conversation must be marked current');
  assert(list.querySelector('.ai-rail__item.is-active'), 'selected conversation must have an active style hook');

  // Client-side rail search filters the loaded, bounded history page.
  const search = document.getElementById('ai-history-search');
  search.value = 'haemoglobin';
  await search.dispatch('input');
  assertEqualLocal(list.querySelectorAll('.ai-rail__item').length, 1, 'rail search must filter to the matching conversation');
  assertContains(textOf(list), 'What is the latest haemoglobin?', 'matching conversation missing after search');
  assert(!textOf(list).includes('Which patients are on metformin?'), 'non-matching conversation must be filtered out');
  search.value = '';
  await search.dispatch('input');
  assertEqualLocal(list.querySelectorAll('.ai-rail__item').length, 2, 'clearing search must restore both conversations');

  // New conversation resets the main pane without deleting history.
  await document.getElementById('ai-new-conversation').dispatch('click');
  assertContains(textOf(region), 'No question asked yet', 'new conversation must show the empty composer state');
  assertEqualLocal(document.getElementById('ai-question').value, '', 'new conversation must clear the draft');
  assertEqualLocal(list.querySelectorAll('.ai-rail__item.is-active').length, 0, 'new conversation must clear the selected rail item');

  // Select a saved conversation again, then clear all history.
  await list.querySelectorAll('.ai-rail__conversation')[0].dispatch('click');
  await document.getElementById('ai-history-clear').dispatch('click');
  const dialog = document.getElementById('app-dialog');
  assert(dialog, 'the confirm dialog must open');
  await clickButton(dialog, 'Clear history');
  await settle();
  assert(calls.some((call) => call.url.endsWith('/ai/conversations') && (call.init.method || 'GET') === 'DELETE'),
    'clearing must DELETE the caller history');
  assertContains(textOf(list), 'No questions yet.', 'empty history note missing after clearing');
  assertContains(textOf(region), 'No question asked yet', 'clearing a viewed saved conversation must reset the main pane');
  assertContains(textOf(document.getElementById('ai-status')), 'cleared', 'clearing must be announced');
  assertEqualLocal(search.value, '', 'clearing history must reset the rail search');
});

await check('AI page conversation rail opens and closes as a mobile drawer', async () => {
  globalThis.fetch = makeFetch({ me: DOCTOR, conversations: CONVERSATIONS });
  const document = bootDom([...CHROME_IDS, ...AI_PAGE_IDS]);
  await freshPage('ai-assistant.js');
  await settle();

  const workspace = document.getElementById('ai-workspace');
  const toggle = document.getElementById('ai-history-toggle');
  const backdrop = document.getElementById('ai-rail-backdrop');
  const close = document.getElementById('ai-history-close');
  assertEqualLocal(workspace.classList.contains('is-rail-open'), false, 'rail must start closed');
  assertEqualLocal(backdrop.hidden, true, 'rail backdrop must start hidden');
  assertEqualLocal(toggle.getAttribute('aria-expanded'), 'false', 'toggle must start collapsed');

  await toggle.dispatch('click');
  assertEqualLocal(workspace.classList.contains('is-rail-open'), true, 'toggle must open the rail');
  assertEqualLocal(backdrop.hidden, false, 'opening the rail must show the backdrop');
  assertEqualLocal(toggle.getAttribute('aria-expanded'), 'true', 'toggle must report expanded');

  await close.dispatch('click');
  assertEqualLocal(workspace.classList.contains('is-rail-open'), false, 'close button must close the rail');
  assertEqualLocal(backdrop.hidden, true, 'closing the rail must hide the backdrop');

  await toggle.dispatch('click');
  await backdrop.dispatch('click');
  assertEqualLocal(workspace.classList.contains('is-rail-open'), false, 'backdrop click must close the rail');
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
  const document = bootDom([...CHROME_IDS, ...PATIENT_IDS, ...PATIENT_TAB_FIXTURE, PRINT_BUTTON_FIXTURE]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  // Opening the AI Patient tab starts the lazy patient-filtered history request.
  await document.getElementById('tab-ai-patient').dispatch('click');
  await settle();
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

await check('AI Patient tab + history render Markdown answers through the safe renderer', async () => {
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
          display_name: 'Администратор Системен',
          email: 'admin@example.com',
          roles: ['clinical_admin'],
          is_active: false,
          created_at: '2026-10-20T09:00:00Z',
        }),
        staffUser({
          id: 'u-2c',
          display_name: 'Мария Николова',
          email: 'nurse@example.com',
          roles: ['nurse'],
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
  assertContains(region, 'Showing 1\u20133 of 25 staff accounts', 'pagination summary wrong');
  const staffRows = document.getElementById('staff-region').querySelector('tbody').querySelectorAll('tr');
  const doctorAvatar = staffRows[0].querySelector('.avatar--photo');
  assert(doctorAvatar, 'doctor row must render a demo profile picture');
  const doctorPhoto = doctorAvatar.querySelector('img');
  assert(doctorPhoto, 'doctor avatar must contain a local image');
  assertContains(doctorPhoto.getAttribute('src'), 'assets/images/doctors/doctor-', 'doctor photo must be local');
  const adminAvatar = staffRows[1].querySelector('.avatar--photo');
  assert(adminAvatar, 'seeded admin row must render the dedicated demo portrait');
  assertContains(adminAvatar.querySelector('img').getAttribute('src'), 'assets/images/staff/admin-1.jpg', 'admin portrait must be the seeded staff image');
  const nurseAvatar = staffRows[2].querySelector('.avatar--photo');
  assert(nurseAvatar, 'seeded nurse row must render the dedicated demo portrait');
  assertContains(nurseAvatar.querySelector('img').getAttribute('src'), 'assets/images/staff/nurse-1.jpg', 'nurse portrait must be the seeded staff image');
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
  assertContains(textOf(document.getElementById('site-nav')), 'Staff Management', 'Staff Management nav missing for clinical_admin');
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
  const interpretBodies = [];
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
  assert(!textOf(document.getElementById('site-nav')).includes('Staff Management'), 'Staff Management nav item must not render for a nurse');
  const nurseTopAvatar = document.getElementById('user-area').querySelector('.avatar--photo');
  assert(nurseTopAvatar, 'seeded nurse top-bar avatar must use the dedicated demo portrait');
  assertContains(nurseTopAvatar.querySelector('img').getAttribute('src'), 'assets/images/staff/nurse-1.jpg', 'nurse top-bar photo must be the seeded staff image');
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
        { id: 'p2', display_name: 'Bob Test', mrn: 'MRN-0002', date_of_birth: '1975-01-20', sex: 'M' },
      ],
      total: 2,
      limit: 8,
      offset: 0,
    },
  });
  const document = bootDom([...CHROME_IDS, 'page-body', 'welcome-heading']);
  await freshPage('dashboard.js');
  await settle();

  const themeSwitcher = document.getElementById('user-area').querySelector('.theme-switcher');
  assert(themeSwitcher, 'theme switcher missing from the authenticated top bar');
  assertEqualLocal(themeSwitcher.querySelectorAll('.theme-switcher__btn').length, 3, 'theme switcher must expose three modes');
  const topAvatar = document.getElementById('user-area').querySelector('.avatar--photo');
  assert(topAvatar, 'doctor top-bar avatar must use a demo profile picture');
  const topPhoto = topAvatar.querySelector('img');
  assert(topPhoto, 'doctor top-bar avatar must contain a local image');
  assertContains(topPhoto.getAttribute('src'), 'assets/images/staff/doctor-', 'seeded doctor top-bar photo must be the dedicated demo image');
  const profileHead = document.getElementById('page-body').querySelector('.profile-card__head');
  const profileAvatar = profileHead && profileHead.querySelector('.avatar--photo');
  assert(profileAvatar, 'doctor dashboard profile card must show the demo picture');
  const profilePhoto = profileAvatar && profileAvatar.querySelector('img');
  assert(profilePhoto, 'doctor dashboard profile picture must contain an image');
  assertContains(textOf(document.getElementById('welcome-heading')), 'Welcome back, Dr Ada Example', 'welcome heading missing');
  const body = textOf(document.getElementById('page-body'));
  assertContains(body, 'Quick actions', 'quick actions heading missing');
  assertContains(body, 'Recent patients', 'recent patients heading missing');
  assertContains(body, 'MRN-0001', 'recent patient card missing');
  assertContains(body, 'MRN-0002', 'second patient card missing');
  assertContains(body, 'Doctor', 'role badge missing');
  assertEqualLocal(
    document.getElementById('page-body').querySelectorAll('.card--notice').length,
    0,
    'no disclosure card may render on the dashboard',
  );

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

  const header = textOf(document.getElementById('demographics-region'));
  assertEqualLocal(
    document.getElementById('demographics-region').querySelectorAll('.badge').length,
    0,
    'no data-origin badge may render in the patient header',
  );
  assertContains(header, 'MRN: MRN-0001', 'MRN missing');
  assertContains(header, ui.formatDate('1980-05-04'), 'birth date not shown in the header meta line');
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
        {
          id: 'tab-ai-patient',
          tag: 'button',
          class: 'tab',
          attrs: { role: 'tab', 'aria-controls': 'panel-ai-patient', 'aria-selected': 'false', tabindex: '-1' },
          text: 'AI Patient',
        },
      ],
    },
    { id: 'panel-summary', class: 'tab-panel', attrs: { role: 'tabpanel' } },
    { id: 'panel-labs', class: 'tab-panel', attrs: { role: 'tabpanel', hidden: '' } },
    { id: 'panel-ai-patient', class: 'tab-panel', attrs: { role: 'tabpanel', hidden: '' } },
  ]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  const summaryPanel = document.getElementById('panel-summary');
  const labsPanel = document.getElementById('panel-labs');
  const aiPanel = document.getElementById('panel-ai-patient');
  assertEqualLocal(summaryPanel.hidden, false, 'summary panel must start visible');
  assertEqualLocal(labsPanel.hidden, true, 'labs panel must start hidden');
  assertEqualLocal(aiPanel.hidden, true, 'AI Patient panel must start hidden');
  assertContains(
    textOf(document.getElementById('ai-patient-context')),
    'About: Ann Test \u00b7 MRN MRN-0001',
    'AI Patient must show the patient context',
  );

  await document.getElementById('tab-labs').dispatch('click');
  assertEqualLocal(labsPanel.hidden, false, 'labs panel must show after clicking its tab');
  assertEqualLocal(summaryPanel.hidden, true, 'summary panel must hide after switching');
  assertEqualLocal(document.getElementById('tab-labs').getAttribute('aria-selected'), 'true', 'aria-selected not set on the new tab');
  assertEqualLocal(document.getElementById('tab-summary').getAttribute('aria-selected'), 'false', 'aria-selected not cleared on the old tab');

  await document.getElementById('tab-ai-patient').dispatch('click');
  assertEqualLocal(aiPanel.hidden, false, 'AI Patient panel must show after clicking its tab');
  assertEqualLocal(labsPanel.hidden, true, 'labs panel must hide after switching to AI Patient');
  assertEqualLocal(document.getElementById('tab-ai-patient').getAttribute('aria-selected'), 'true', 'AI Patient tab must be selected');

  await document.getElementById('tab-ai-patient').dispatch('keydown', { key: 'ArrowLeft' });
  assertEqualLocal(labsPanel.hidden, false, 'arrow key must move selection back to the labs tab');
  assertEqualLocal(aiPanel.hidden, true, 'AI Patient panel must hide after arrow-key navigation');
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

await check('landing header receives the three-mode theme switcher', async () => {
  globalThis.fetch = async (url) =>
    (url.endsWith('/auth/me')
      ? json(401, { error: { code: 'unauthenticated', message: '' } })
      : json(401, { error: { code: 'unauthenticated', message: 'no' } }));
  const document = bootDom([
    'global-status',
    'login-form',
    'email',
    'password',
    'login-submit',
    'login-status',
    'email-error',
    'password-error',
    'login-card',
    'signed-in-card',
    { id: 'landing-header', tag: 'header', class: 'landing-header' },
  ]);
  await freshPage('login.js');
  await settle();
  const switcher = document.getElementById('landing-header').querySelector('.theme-switcher');
  assert(switcher, 'landing theme switcher missing');
  assertEqualLocal(switcher.querySelectorAll('.theme-switcher__btn').length, 3, 'landing switcher must expose three modes');
  for (const button of switcher.querySelectorAll('.theme-switcher__btn')) {
    assert(button.getAttribute('aria-label'), 'landing theme button needs an accessible label');
    assert(button.getAttribute('title'), 'landing theme button needs a tooltip');
  }
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

await check('login password toggle switches visibility accessibly', async () => {
  globalThis.fetch = async (url) =>
    (url.endsWith('/auth/me')
      ? json(401, { error: { code: 'unauthenticated', message: '' } })
      : json(401, { error: { code: 'unauthenticated', message: 'no' } }));
  const document = bootDom([
    'global-status', 'login-form', 'email', 'password', 'login-submit',
    'login-status', 'email-error', 'password-error', 'login-card',
    'signed-in-card', 'password-toggle',
  ]);
  const password = document.getElementById('password');
  const toggle = document.getElementById('password-toggle');
  password.type = 'password';
  await freshPage('login.js');
  await settle();

  await toggle.dispatch('click');
  assertEqualLocal(password.type, 'text', 'toggle must reveal the password');
  assertEqualLocal(toggle.getAttribute('aria-pressed'), 'true', 'toggle must expose its pressed state');
  assertEqualLocal(toggle.getAttribute('aria-label'), 'Hide password', 'toggle label must describe hiding');

  await toggle.dispatch('click');
  assertEqualLocal(password.type, 'password', 'toggle must hide the password again');
  assertEqualLocal(toggle.getAttribute('aria-pressed'), 'false', 'toggle must clear its pressed state');
  assertEqualLocal(toggle.getAttribute('aria-label'), 'Show password', 'toggle label must describe showing');
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

/* ------------------------------------------------------- heart loaders */

await check('heart loaders show on first load and are replaced by real content', async () => {
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
  assert(region.querySelector('.loading-block'), 'first load must show the loading placeholder');
  assert(region.querySelector('.heart-loader__img'), 'the placeholder must show the heart loader');
  assertContains(textOf(region), 'Loading patients', 'the placeholder must announce its label');

  releasePatients();
  await settle();
  assertContains(textOf(region), 'Ann Test', 'content must replace the placeholder');
  assertEqualLocal(region.querySelector('.loading-block'), null, 'the placeholder must be gone once data arrives');
  assertEqualLocal(region.querySelector('.heart-loader__img'), null, 'the heart must be gone once data arrives');
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
            { id: 'p2', display_name: 'Bob Test', mrn: 'MRN-0002' },
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
  // revalidate in the background (no loader, no flicker).
  const input = document.getElementById('patient-search');
  input.value = '';
  await input.dispatch('input');
  await settle(400); // search debounce

  assertEqualLocal(patientCallCount, 2, 'the second load must still revalidate against the API');
  assertContains(textOf(region), 'Ann Test', 'cached data must render instantly on the second load');
  assert(!textOf(region).includes('Bob Test'), 'nothing may change until the revalidation lands');
  assertEqualLocal(region.querySelector('.loading-block'), null, 'a cache hit must not show the heart loader');
  assertContains(textOf(document.getElementById('patients-status')), '1 patient found', 'cached status must render instantly');
  assert(region.querySelector('.sync-chip'), 'a tiny updating indicator must mark the background refresh');

  releaseSecond();
  await settle();
  assertContains(textOf(region), 'Bob Test', 'the revalidated payload must update the DOM');
  assertEqualLocal(region.querySelector('.sync-chip'), null, 'the updating indicator must clear');
  assertContains(textOf(document.getElementById('patients-status')), '2 patients found', 'status must follow the refresh');
});

/* ---------------------------------------------------------- system logs */

await check('system logs render history, stats, filters, clear, and deny non-admins', async () => {
  const calls = [];
  const routes = [{
    method: 'GET',
    match: /\/admin\/logs$/,
    response: SYSTEM_LOGS_PAGE,
  }];
  globalThis.fetch = makeFetch({ me: CLIN_ADMIN, calls, routes, staff: SYSTEM_LOG_STAFF });
  const document = bootDom([...CHROME_IDS, ...SYSTEM_LOG_IDS]);
  await freshPage('system-logs.js');
  await settle();

  const nav = textOf(document.getElementById('site-nav'));
  assertContains(nav, 'System Logs', 'admin System Logs nav missing');
  assert(nav.indexOf('Analytics') < nav.indexOf('System Logs'), 'System Logs must follow Analytics');
  const terminal = document.getElementById('logs-terminal');
  assertContains(textOf(terminal), 'patient.read', 'historical event missing');
  assertContains(textOf(terminal), 'Patient record accessed', 'event description missing');
  assertContains(textOf(terminal), 'Dr Ada Example', 'event sender missing');
  const senderImages = terminal.querySelectorAll('img');
  assert(senderImages.length > 0, 'doctor profile portrait missing');
  assert(senderImages[0].getAttribute('src').includes('staff/doctor-'), 'portrait must use the seeded doctor demo image');
  assertEqualLocal(textOf(document.getElementById('logs-stat-total')), '1', 'total counter wrong');
  assertEqualLocal(textOf(document.getElementById('logs-connection')), 'Disconnected', 'status must not claim live');
  assertContains(textOf(document.getElementById('logs-last-event')), '10:00:00.123', 'last-event timestamp missing');

  document.getElementById('logs-severity').value = 'error';
  await document.getElementById('logs-filter-form').dispatch('submit');
  await settle();
  assert(
    calls.some((call) => call.url.includes('/admin/logs') && call.url.includes('severity=error')),
    'severity filter was not sent',
  );

  const beforeClear = calls.length;
  const terminalBeforeCancel = textOf(document.getElementById('logs-terminal'));
  await document.getElementById('logs-clear').dispatch('click');
  assertContains(textOf(document.getElementById('app-dialog-title')), 'Are you sure?', 'clear view confirmation title missing');
  assertContains(textOf(document.getElementById('app-dialog-body')), 'Persisted audit and operational events will not be deleted', 'clear view safety copy missing');
  await clickButton(document.getElementById('app-dialog-actions'), 'Cancel');
  await settle();
  assertEqualLocal(textOf(document.getElementById('logs-terminal')), terminalBeforeCancel, 'cancelling clear view must leave the events visible');
  assertEqualLocal(calls.length, beforeClear, 'cancelling clear view must not call the backend');

  await document.getElementById('logs-clear').dispatch('click');
  await clickButton(document.getElementById('app-dialog-actions'), 'Clear view');
  await settle();
  assertContains(textOf(document.getElementById('logs-terminal')), 'Waiting for incoming events', 'clear view must empty the terminal after confirmation');
  assert(document.getElementById('logs-empty'), 'clear view must leave a replaceable empty marker');
  assertContains(textOf(document.getElementById('logs-status')), 'Persisted backend events were not deleted', 'clear view must announce that backend events remain');
  assertEqualLocal(calls.length, beforeClear, 'clear view must not call the backend or delete events');

  for (const user of [DOCTOR, NURSE]) {
    const deniedCalls = [];
    globalThis.fetch = makeFetch({ me: user, calls: deniedCalls });
    const denied = bootDom([...CHROME_IDS, ...SYSTEM_LOG_IDS]);
    await freshPage('system-logs.js');
    await settle();
    assertContains(textOf(denied.getElementById('page-body')), 'You do not have permission', `${user.roles[0]} denial missing`);
    assert(!textOf(denied.getElementById('site-nav')).includes('System Logs'), `${user.roles[0]} must not see System Logs`);
    assert(!deniedCalls.some((call) => call.url.includes('/admin/logs')), `${user.roles[0]} must not fetch logs`);
  }
});

/* ------------------------------------------------------------- messages */

await check('messages page renders own inbox, marks read, and uses inline composer + attachments', async () => {
  const calls = [];
  const readCalls = [];
  const sentMessages = [];
  const sentAttachments = [];
  const routes = [
    {
      method: 'POST',
      match: '/api/v1/messages/m1/read',
      response: () => {
        readCalls.push('m1');
        return { status: 204, ok: true };
      },
    },
    {
      method: 'POST',
      match: '/api/v1/messages/attachments',
      response: ({ formData }) => {
        sentAttachments.push(formData);
        return {
          status: 201,
          ok: true,
          body: {
            ...MESSAGE_INBOX,
            id: 'm3',
            subject: formData.get('subject'),
            body: formData.get('body'),
            attachment: { ...MESSAGE_ATTACHMENT, filename: formData.get('file').name },
          },
        };
      },
    },
    {
      method: 'POST',
      match: '/api/v1/messages',
      response: ({ body }) => {
        sentMessages.push(body);
        return {
          status: 201,
          ok: true,
          body: { ...MESSAGE_INBOX, id: 'm2', attachment: null, ...body },
        };
      },
    },
  ];
  globalThis.fetch = makeFetch({
    me: CLIN_ADMIN,
    calls,
    routes,
    messages: { items: [MESSAGE_INBOX], total: 1, limit: 50, offset: 0 },
    messageRecipients: MESSAGE_RECIPIENTS,
  });
  const document = bootDom([...CHROME_IDS, ...MESSAGES_IDS]);
  await freshPage('messages.js');
  await settle();

  const nav = textOf(document.getElementById('site-nav'));
  assertContains(nav, 'Messages', 'admin nav item missing');
  assert(nav.indexOf('Messages') < nav.indexOf('Staff Management'), 'Messages must sit before Staff Management');
  const region = document.getElementById('messages-region');
  assertContains(textOf(region), 'Doc Sender', 'inbox sender missing');
  assertContains(textOf(region), 'Rota review', 'inbox subject missing');
  assertContains(textOf(region), 'rota.pdf', 'inbox attachment preview missing');
  assertContains(textOf(region), 'Unread', 'unread marker missing');
  const listAvatarHost = region.querySelector('.message-row__avatar');
  const listAvatar = listAvatarHost && listAvatarHost.querySelector('.avatar');
  assert(
    listAvatar && listAvatar.classList.contains('avatar--photo'),
    'conversation list must render a staff profile image',
  );
  assertContains(textOf(document.getElementById('messages-count')), '1 message in your inbox', 'inbox count missing');
  assertContains(textOf(document.getElementById('messages-unread')), '1 unread', 'unread count missing');
  assertContains(textOf(document.getElementById('messages-inbox-count')), '1', 'inbox tab count missing');

  document.getElementById('messages-search').value = 'rota';
  await document.getElementById('messages-search-form').dispatch('submit');
  await settle();
  assert(
    calls.some((call) => call.url.includes('/messages') && call.url.includes('q=rota')),
    'server-side message search missing',
  );

  await document.getElementById('messages-sent-tab').dispatch('click');
  await settle();
  assert(
    calls.some((call) => call.url.includes('/messages') && call.url.includes('box=sent')),
    'sent folder request missing',
  );
  await document.getElementById('messages-inbox-tab').dispatch('click');
  await settle();

  const row = region.querySelector('.message-row');
  await row.dispatch('click');
  await settle();
  assertEqualLocal(readCalls.length, 1, 'mark-read request missing');
  assertContains(textOf(document.getElementById('messages-detail')), 'Please review the rota', 'detail body missing');
  assertContains(textOf(document.getElementById('messages-detail')), 'Read', 'detail read state missing');
  assertContains(textOf(document.getElementById('messages-detail')), 'Doc Sender', 'detail sender missing');
  assertContains(textOf(document.getElementById('messages-detail')), 'Cal Admin', 'detail recipient missing');
  assertContains(textOf(document.getElementById('messages-detail')), 'rota.pdf', 'detail attachment missing');
  const download = document.getElementById('messages-detail').querySelector('.message-attachment__download');
  assert(download, 'attachment download action missing');
  assertContains(download.getAttribute('href'), '/messages/m1/attachment', 'attachment download URL missing');
  assertEqualLocal(document.getElementById('message-composer') !== null, true, 'inline composer missing');
  assert(!document.getElementById('app-dialog'), 'messages must not open a modal composer');

  const profile = document.getElementById('messages-profile');
  assertContains(textOf(profile), 'Doc Sender', 'profile contact name missing');
  assertContains(textOf(profile), 'Doctor', 'profile role missing');
  assertContains(textOf(profile), 'Rota review', 'profile subject missing');
  assertContains(textOf(profile), 'rota.pdf', 'profile attachment missing');
  assert(!textOf(profile).includes('Only you and'), 'legacy privacy notice must be removed');
  const profileAvatarHost = profile.querySelector('.messages-profile__avatar');
  const profileAvatar = profileAvatarHost && profileAvatarHost.querySelector('.avatar');
  assert(
    profileAvatar && profileAvatar.classList.contains('avatar--photo'),
    'profile pane must render the contact image',
  );
  assert(document.getElementById('messages-workspace').classList.contains('is-reading'), 'selected message must open the reading view on mobile');

  await document.getElementById('messages-new').dispatch('click');
  await settle();
  assert(document.getElementById('message-recipient'), 'inline new-message recipient field missing');
  assert(!document.getElementById('app-dialog'), 'New message must not open a dialog');
  document.getElementById('message-recipient').value = 'u9';
  document.getElementById('message-subject').value = 'Reply';
  document.getElementById('message-body').value = 'Thanks for the update.';
  await document.getElementById('message-composer').dispatch('submit');
  await settle();
  assertEqualLocal(sentMessages.length, 1, 'message send request missing');
  assertEqualLocal(sentMessages[0].recipient_id, 'u9', 'recipient missing from message');
  assertEqualLocal(sentMessages[0].subject, 'Reply', 'subject missing from message');
  assertEqualLocal(sentMessages[0].body, 'Thanks for the update.', 'body missing from message');

  // Attachments are sent as multipart from the inline composer.
  await document.getElementById('messages-inbox-tab').dispatch('click');
  await settle();
  await region.querySelector('.message-row').dispatch('click');
  await settle();
  const fileInput = document.getElementById('message-file');
  const file = new File(['hello attachment'], 'note.txt', { type: 'text/plain' });
  fileInput.files = [file];
  await fileInput.dispatch('change');
  assertContains(textOf(document.getElementById('message-attachment-preview')), 'note.txt', 'selected attachment chip missing');
  document.getElementById('message-body').value = 'Attached notes.';
  await document.getElementById('message-composer').dispatch('submit');
  await settle();
  assertEqualLocal(sentAttachments.length, 1, 'attachment send request missing');
  assertEqualLocal(sentAttachments[0].get('recipient_id'), 'u9', 'attachment recipient missing');
  assertEqualLocal(sentAttachments[0].get('file').name, 'note.txt', 'attachment file missing');
  assertEqualLocal(sentAttachments[0].get('body'), 'Attached notes.', 'attachment body missing');
  assertContains(String(sentAttachments[0].get('subject')), 'Re: Rota review', 'attachment reply subject missing');

  // Doctors get Messages too, but not the Staff Management nav item.
  globalThis.fetch = makeFetch({
    me: MESSAGING_DOCTOR,
    messages: { items: [], total: 0, limit: 50, offset: 0 },
    messageRecipients: MESSAGE_RECIPIENTS,
  });
  const doctorDocument = bootDom([...CHROME_IDS, ...MESSAGES_IDS]);
  await freshPage('messages.js');
  await settle();
  const doctorNav = textOf(doctorDocument.getElementById('site-nav'));
  assertContains(doctorNav, 'Messages', 'doctor nav item missing');
  assert(!doctorNav.includes('Staff Management'), 'doctor must not see Staff Management nav');
  assertContains(textOf(doctorDocument.getElementById('messages-region')), 'Your inbox is empty', 'empty inbox state missing');

  // Nurses now share the same Messages surface and recipient directory.
  const nurseCalls = [];
  globalThis.fetch = makeFetch({
    me: NURSE,
    calls: nurseCalls,
    messages: { items: [], total: 0, limit: 50, offset: 0 },
    messageRecipients: MESSAGE_RECIPIENTS,
  });
  const nurseDocument = bootDom([...CHROME_IDS, ...MESSAGES_IDS]);
  await freshPage('messages.js');
  await settle();
  const nurseNav = textOf(nurseDocument.getElementById('site-nav'));
  assertContains(nurseNav, 'Messages', 'nurse nav item missing');
  assert(!nurseNav.includes('Staff Management'), 'nurse must not see Staff Management nav');
  assertContains(textOf(nurseDocument.getElementById('messages-region')), 'Your inbox is empty', 'nurse empty inbox state missing');
  assert(nurseCalls.some((call) => call.url.includes('/messages')), 'nurse must fetch messages');

  await nurseDocument.getElementById('messages-new').dispatch('click');
  await settle();
  const recipientSelect = nurseDocument.getElementById('message-recipient');
  const recipientLabels = recipientSelect.querySelectorAll('option').map((option) => option.textContent);
  assert(recipientLabels.includes('Doc Sender · Doctor'), 'nurse must be able to select a Doctor');
  assert(recipientLabels.includes('Nurse Person · Nurse'), 'nurse must be able to select a Nurse');
  assert(recipientLabels.includes('Admin Person · Admin'), 'nurse must be able to select an Admin');
  const nurseCancel = buttonByText(nurseDocument.getElementById('messages-detail'), 'Cancel');
  assert(nurseCancel, 'nurse inline composer cancel missing');
  await nurseCancel.dispatch('click');
  await settle();
  assert(!nurseDocument.getElementById('message-composer'), 'cancel must close the inline composer');
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

  // Navigation rows are actions, not page-filename listings. Patient rows may
  // still carry their MRN hint, but no option hint may be a .html path.
  const hints = document.getElementById('command-menu-list').querySelectorAll('.command-menu__option-hint');
  for (const hint of hints) {
    assert(!/\.html$/.test(textOf(hint)), `navigation hint must not expose a page path: ${textOf(hint)}`);
  }

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
  assertContains(listText, 'of 130 patients', 'the bounded patient note is missing');
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

/* ---------------------------------------------------- hero placeholders */

await check('hero placeholders (heart loader) swap to content via the ready class', async () => {
  const document = bootDom([]);
  const region = document.createElement('div');
  document.body.append(region);

  ui.renderBlurPlaceholder(region, { label: 'Loading patient demographics', variant: 'hero' });
  assert(region.classList.contains('blur-region'), 'the region must carry the blur-region class');
  assert(region.classList.contains('is-loading'), 'the region must be marked loading');
  assert(region.querySelector('.blur-placeholder'), 'the hero placeholder must render');
  assertContains(textOf(region), 'Loading patient demographics', 'the placeholder must announce its label');
  assert(region.querySelector('.heart-loader__img'), 'the placeholder must show the heart loader');
  assert(region.querySelector('.heart-loader--md'), 'the hero placeholder takes the md heart');

  ui.clear(region);
  region.append(ui.el('p', { text: 'Real demographics' }));
  ui.markBlurReady(region);
  assertEqualLocal(region.querySelector('.blur-placeholder'), null, 'the placeholder must be gone');
  assertEqualLocal(region.querySelector('.heart-loader__img'), null, 'the heart must be gone');
  assert(region.classList.contains('is-ready'), 'the ready class must be toggled on');
  assert(!region.classList.contains('is-loading'), 'the loading class must clear');

  // Re-targeting: further swaps stay consistent (class toggling is idempotent),
  // and the answer variant takes the lg heart of the AI answer region.
  ui.renderBlurPlaceholder(region, { variant: 'answer' });
  assert(region.querySelector('.heart-loader--lg'), 'the answer placeholder takes the lg heart');
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
  assert(region.querySelector('.blur-placeholder'), 'the hero header must show the placeholder');
  assert(region.querySelector('.heart-loader__img'), 'the hero placeholder must show the heart loader');
  assertEqualLocal(region.querySelector('.loading-block'), null, 'hero regions use the hero placeholder, not the list loader');

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
    message: 'Sign out?',
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

  // Upward drag past the resting position: rubber-band resistance, then snap back.
  await grabber.dispatch('pointerdown', { clientX: 100, clientY: 220, pointerType: 'touch' });
  await grabber.dispatch('pointermove', { clientX: 100, clientY: 160, pointerType: 'touch' });
  const upwardOffset = Number.parseFloat(dialog.style.getPropertyValue('--sheet-dy'));
  assert(upwardOffset < 0 && upwardOffset > -60, `upward drag must rubber-band, got ${upwardOffset}`);
  await grabber.dispatch('pointerup', {});
  await settle();
  assertEqualLocal(resolved, undefined, 'an upward drag must never dismiss the dialog');
  assertEqualLocal(dialog.style.getPropertyValue('--sheet-dy'), '0px', 'rubber-banded sheet must snap back');

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

await check('overlay history defers a replacement push until the pending self-back settles', async () => {
  bootDom([]);
  const overlayHistory = await import(JS + 'overlay-history.js');
  overlayHistory.resetOverlayStack();
  const originalBack = globalThis.history.back.bind(globalThis.history);
  let pendingBack = false;
  globalThis.history.back = () => {
    pendingBack = true;
  };

  const baseline = globalThis.history.length;
  const first = overlayHistory.pushOverlay(() => {});
  assertEqualLocal(globalThis.history.length, baseline + 1, 'the first overlay must push one entry');

  overlayHistory.releaseOverlay(first);
  assert(pendingBack, 'a UI close must request history.back()');

  const replacement = overlayHistory.pushOverlay(() => {});
  assertEqualLocal(
    globalThis.history.length,
    baseline + 1,
    'the replacement push must wait while the self-back is in flight',
  );

  dispatchWindowEvent('popstate', { state: null });
  assertEqualLocal(
    globalThis.history.length,
    baseline + 2,
    'the replacement push must run after the self-back popstate',
  );

  globalThis.history.back = originalBack;
  overlayHistory.resetOverlayStack();
});

await check('expanded history entries unwind through Back in LIFO order', async () => {
  globalThis.fetch = makeFetch({
    me: DOCTOR,
    patient: PATIENT,
    summary: SUMMARY,
    labs: LABS,
    prescriptions: PRESCRIPTIONS,
    conversations: CONVERSATIONS,
  });
  const document = bootDom([
    ...CHROME_IDS,
    ...PATIENT_IDS,
    ...PATIENT_TAB_FIXTURE,
    PRINT_BUTTON_FIXTURE,
  ]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();
  await document.getElementById('tab-ai-patient').dispatch('click');
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
   pure CSS/inline-SVG charts: one request supplies every section, the shared
   date filter requests the selected UTC range, SVG/donut counts match the data,
   every chart carries role="img" + an aria-label and a visually-hidden data
   table, empty states are honest, the first load shows skeletons, Refresh rides
   the SWR cache, and the page/nav/palette all gate on analytics:read. */

await check('analytics page renders range-aware KPIs and every aggregate section', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({ me: CLIN_ADMIN, calls, analytics: ANALYTICS_SUMMARY });
  const document = bootDom([...CHROME_IDS, ...ANALYTICS_IDS]);
  await freshPage('analytics.js');
  await settle();

  const analyticsCalls = calls.filter((call) => call.url.includes('/analytics/summary'));
  assertEqualLocal(analyticsCalls.length, 1, 'one request must feed every analytics section');
  assertContains(analyticsCalls[0].url, 'start=', 'the selected range start must be sent');
  assertContains(analyticsCalls[0].url, 'end=', 'the selected range end must be sent');

  const kpi = document.getElementById('kpi-region');
  const kpiText = textOf(kpi);
  assertContains(kpiText, 'Registered patients', 'KPI labels missing');
  assertContains(kpiText, '45', 'lifetime patient count missing');
  assertContains(kpiText, 'New patients', 'new-patient KPI missing');
  assertContains(kpiText, '8', 'new-patient count missing');
  assertContains(kpiText, '+60.0% vs the preceding 14 days', 'comparison note missing');
  assertContains(kpiText, 'Laboratory tests', 'lab KPI missing');
  assertContains(kpiText, 'Total prescriptions', 'lifetime prescription KPI missing');
  assertContains(kpiText, 'Prescriptions written', 'prescription KPI missing');
  assertContains(kpiText, 'Active prescriptions', 'active prescription KPI missing');
  assertContains(kpiText, 'Appointments', 'appointment KPI missing');
  assertContains(kpiText, 'AI requests', 'AI KPI missing');
  assertEqualLocal(kpi.querySelectorAll('.kpi-tile').length, 9, 'nine KPI tiles expected');
  assertContains(textOf(document.getElementById('analytics-generated-at')), '14 Oct 2026', 'generated-at line missing');
  assertContains(textOf(document.getElementById('analytics-period-label')), '1 Oct 2026', 'period start missing');
  assertContains(textOf(document.getElementById('analytics-period-label')), '14 Oct 2026', 'period end missing');
  assertContains(textOf(document.getElementById('analytics-period-label')), '14 days', 'period length missing');

  const patients = document.getElementById('patients-region');
  assertContains(textOf(patients), 'Encounter volume', 'encounter trend panel missing');
  assertContains(textOf(patients), 'office_visit', 'encounter type bars missing');
  assert(patients.querySelector('svg.chart'), 'encounter line chart missing');
  assertEqualLocal(patients.querySelectorAll('.chart-legend__item').length, 1, 'one encounter series expected');

  const appointments = document.getElementById('appointments-region');
  assertContains(textOf(appointments), 'Calendar utilization', 'utilization KPI missing');
  assertContains(textOf(appointments), '50.0%', 'utilization percent missing');
  assertContains(textOf(appointments), '360 booked / 720 available minutes', 'utilization denominator missing');
  assertContains(textOf(appointments), 'Appointments by status', 'status panel missing');
  assertContains(textOf(appointments), 'Analytics Probe', 'visit type bars missing');
  assertContains(textOf(appointments), 'Monday', 'weekday distribution missing');
  assertContains(textOf(appointments), '08:00', 'hour distribution missing');
  assertEqualLocal(appointments.querySelectorAll('.analytics-status-legend__item').length, 5, 'five status categories expected');
  const statusDonut = appointments.querySelector('.analytics-status-donut');
  assert(statusDonut, 'appointment status donut missing');
  assertEqualLocal(statusDonut.getAttribute('role'), 'img', 'status donut must be exposed as an image');
  assertContains(statusDonut.getAttribute('aria-label') || '', 'Scheduled', 'status donut aria-label missing categories');
  const segments = appointments.querySelectorAll('.donut__segment');
  assert(segments.length >= 1, 'status donut segments missing');
  assertContains(segments[0].getAttribute('stroke-dasharray') || '', ' ', 'status segments must use dasharray attributes');

  const labs = document.getElementById('labs-region');
  assertContains(textOf(labs), '34.0%', 'abnormal rate missing');
  assertContains(textOf(labs), '17 of 50', 'abnormal denominator missing');
  assertContains(textOf(labs), 'Unclassified', 'classification missing');
  assertContains(textOf(labs), 'Glucose, fasting (GLU)', 'top abnormal tests missing');
  assertContains(textOf(labs), 'Document status mix', 'lab status mix missing');
  assert(labs.querySelector('svg.donut'), 'abnormal-rate donut missing');

  const prescriptions = document.getElementById('prescriptions-region');
  assertContains(textOf(prescriptions), 'Stopped / discontinued', 'discontinued label missing');
  assertContains(textOf(prescriptions), 'Lifetime prescriptions by status', 'lifetime status panel missing');
  assertContains(textOf(prescriptions), 'Completed', 'completed status missing');

  const security = document.getElementById('security-region');
  assertContains(textOf(security), 'Successful sign-ins', 'sign-in KPI missing');
  assertContains(textOf(security), 'Permission denials', 'denial KPI missing');
  assertContains(textOf(security), 'Clinical-record reads over time', 'record-read chart missing');
  assertContains(textOf(security), 'Permission denials over time', 'denial chart missing');

  const ai = document.getElementById('ai-region');
  assertContains(textOf(ai), 'MCP tool calls', 'tool panel missing');
  assertContains(textOf(ai), 'get_patient_summary', 'tool name missing');
  assertContains(textOf(ai), 'Failures', 'tool failures column missing');
  assertContains(textOf(ai), 'Not available', 'unpersisted metrics must be marked unavailable');
  assertContains(textOf(ai), 'Provider health', 'provider health panel missing');

  const quality = document.getElementById('quality-region');
  assertContains(textOf(quality), 'Lab results missing reference ranges', 'quality check missing');
  assertContains(textOf(quality), 'Optional metadata', 'quality kind missing');

  const health = document.getElementById('health-region');
  assertContains(textOf(health), 'Backend', 'backend health missing');
  assertContains(textOf(health), 'Operational', 'backend state missing');
  assertContains(textOf(health), 'Connected', 'database state missing');
  assertContains(textOf(health), 'API error rate', 'API error rate missing');

  const staff = document.getElementById('staff-region');
  assertContains(textOf(staff), 'Active staff accounts', 'staff KPI missing');
  assertContains(textOf(staff), 'Doctor', 'doctor role missing');
  assertContains(textOf(staff), 'Admin', 'admin role missing');

  for (const regionId of [
    'patients-region',
    'appointments-region',
    'labs-region',
    'prescriptions-region',
    'security-region',
    'ai-region',
    'staff-region',
  ]) {
    const region = document.getElementById(regionId);
    assert(
      region.querySelectorAll('.visually-hidden').length >= 1,
      `${regionId} must carry a visually-hidden data table`,
    );
  }
});

await check('analytics date filter requests the selected UTC range exactly once', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({ me: CLIN_ADMIN, calls, analytics: ANALYTICS_SUMMARY });
  const document = bootDom([...CHROME_IDS, ...ANALYTICS_IDS]);
  await freshPage('analytics.js');
  await settle();

  const range = document.getElementById('analytics-range');
  range.value = '7';
  await range.dispatch('change');
  assertContains(textOf(document.getElementById('analytics-period-label')), '7 days', 'period label did not update immediately');
  await settle();
  let analyticsCalls = calls.filter((call) => call.url.includes('/analytics/summary'));
  assertEqualLocal(analyticsCalls.length, 2, 'a preset change must add exactly one request');
  const sevenDayUrl = new URL(analyticsCalls[1].url, 'http://testserver');
  const sevenStart = Date.parse(`${sevenDayUrl.searchParams.get('start')}T00:00:00Z`);
  const sevenEnd = Date.parse(`${sevenDayUrl.searchParams.get('end')}T00:00:00Z`);
  assertEqualLocal((sevenEnd - sevenStart) / 86400000 + 1, 7, 'seven-day range mismatch');

  range.value = 'custom';
  await range.dispatch('change');
  assertEqualLocal(document.getElementById('analytics-custom').hidden, false, 'custom inputs must be revealed');
  document.getElementById('analytics-start').value = '2026-09-01';
  document.getElementById('analytics-end').value = '2026-09-10';
  await document.getElementById('analytics-filter-form').dispatch('submit');
  await settle();
  analyticsCalls = calls.filter((call) => call.url.includes('/analytics/summary'));
  assertEqualLocal(analyticsCalls.length, 3, 'custom apply must add exactly one request');
  assertContains(analyticsCalls[2].url, 'start=2026-09-01', 'custom start missing');
  assertContains(analyticsCalls[2].url, 'end=2026-09-10', 'custom end missing');

  const beforeInvalid = calls.filter((call) => call.url.includes('/analytics/summary')).length;
  document.getElementById('analytics-start').value = '2026-09-20';
  document.getElementById('analytics-end').value = '2026-09-10';
  await document.getElementById('analytics-filter-form').dispatch('submit');
  await settle();
  assertContains(textOf(document.getElementById('analytics-range-error')), 'on or after', 'invalid range error missing');
  assertEqualLocal(
    calls.filter((call) => call.url.includes('/analytics/summary')).length,
    beforeInvalid,
    'an invalid range must not fetch',
  );

  await document.getElementById('analytics-reset').dispatch('click');
  await settle();
  assertEqualLocal(range.value, '30', 'reset must restore the default preset');
  analyticsCalls = calls.filter((call) => call.url.includes('/analytics/summary'));
  const resetUrl = new URL(analyticsCalls[analyticsCalls.length - 1].url, 'http://testserver');
  const resetStart = Date.parse(`${resetUrl.searchParams.get('start')}T00:00:00Z`);
  const resetEnd = Date.parse(`${resetUrl.searchParams.get('end')}T00:00:00Z`);
  assertEqualLocal((resetEnd - resetStart) / 86400000 + 1, 30, 'reset must request the default 30-day range');
});

await check('analytics empty states are honest when a period has no activity', async () => {
  const zeroCounts = {
    successful_signins: 0,
    failed_signins: 0,
    permission_denials: 0,
    patient_reads: 0,
    lab_reads: 0,
    prescription_reads: 0,
    ai_requests: 0,
  };
  const emptyTrend = ANALYTICS_DAYS.map((day) => ({ day }));
  const emptyish = {
    ...ANALYTICS_SUMMARY,
    activity: {
      new_patients: 0,
      encounters: 0,
      lab_results: 0,
      prescriptions_written: 0,
      appointments: 0,
      ai_requests: 0,
      successful_signins: 0,
      failed_signins: 0,
      permission_denials: 0,
    },
    previous_activity: {
      new_patients: 0,
      encounters: 0,
      lab_results: 0,
      prescriptions_written: 0,
      appointments: 0,
      ai_requests: 0,
      successful_signins: 0,
      failed_signins: 0,
      permission_denials: 0,
    },
    comparisons: Object.fromEntries(
      Object.keys(ANALYTICS_SUMMARY.comparisons).map((key) => [key, { current: 0, previous: 0, change_pct: null }]),
    ),
    patients: {
      ...ANALYTICS_SUMMARY.patients,
      new_patients: 0,
      returning_patients: 0,
      active_patients: 0,
      encounters: 0,
      avg_encounters_per_day: 0,
      by_encounter_type: [],
      trend: emptyTrend.map((entry) => ({ ...entry, encounters: 0 })),
    },
    appointments: {
      ...ANALYTICS_SUMMARY.appointments,
      total: 0,
      by_status: { scheduled: 0, confirmed: 0, completed: 0, cancelled: 0, no_show: 0 },
      by_visit_type: [],
      by_weekday: ANALYTICS_SUMMARY.appointments.by_weekday.map((entry) => ({ ...entry, count: 0 })),
      by_hour: ANALYTICS_SUMMARY.appointments.by_hour.map((entry) => ({ ...entry, count: 0 })),
      trend: emptyTrend.map((entry) => ({ ...entry, total: 0, completed: 0, cancelled: 0, no_show: 0 })),
      utilization: { booked_minutes: 0, available_minutes: 0, utilization_pct: null, doctors_with_schedule: 0 },
    },
    labs: {
      ...ANALYTICS_SUMMARY.labs,
      total: 0,
      status_mix: { final: 0, preliminary: 0, pending: 0 },
      abnormal_rate: { abnormal: 0, total: 0, pct: 0 },
      classification: {
        normal: 0,
        high: 0,
        low: 0,
        unclassified: 0,
        missing_reference_ranges: 0,
        reference_coverage_pct: 0,
      },
      top_abnormal_tests: [],
      trend: emptyTrend.map((entry) => ({ ...entry, total: 0, evaluable: 0, abnormal: 0 })),
    },
    prescriptions: {
      ...ANALYTICS_SUMMARY.prescriptions,
      total: 0,
      by_status: { active: 0, completed: 0, stopped: 0, cancelled: 0 },
      lifetime_by_status: { active: 0, completed: 0, stopped: 0, cancelled: 0 },
      trend: emptyTrend.map((entry) => ({ ...entry, prescriptions: 0 })),
    },
    security: { totals: zeroCounts, trend: emptyTrend.map((entry) => ({ ...entry, ...zeroCounts })), timezone: 'UTC' },
    ai_usage: {
      ...ANALYTICS_SUMMARY.ai_usage,
      total_requests: 0,
      successful: 0,
      failed: 0,
      denied: 0,
      success_rate: null,
      top_tools: [],
    },
    data_quality: { checks: [] },
    staff: { active_total: 0, inactive_total: 0, by_role: [] },
  };
  globalThis.fetch = makeFetch({ me: CLIN_ADMIN, analytics: emptyish });
  const document = bootDom([...CHROME_IDS, ...ANALYTICS_IDS]);
  await freshPage('analytics.js');
  await settle();

  assertContains(textOf(document.getElementById('kpi-region')), 'No activity in this or the preceding 14 days', 'zero comparison must be explained');
  assertContains(textOf(document.getElementById('patients-region')), 'No encounters in this period', 'empty encounter state missing');
  assertContains(textOf(document.getElementById('appointments-region')), 'No appointments in this period', 'empty appointment state missing');
  assertContains(textOf(document.getElementById('labs-region')), 'No evaluable laboratory results', 'zero lab denominator must be explained');
  assertContains(textOf(document.getElementById('prescriptions-region')), 'No prescriptions written in this period', 'empty prescription state missing');
  assertContains(textOf(document.getElementById('security-region')), 'No sign-in events in this period', 'empty sign-in state missing');
  assertContains(textOf(document.getElementById('ai-region')), 'No MCP tool calls in this period', 'empty tool state missing');
  assertContains(textOf(document.getElementById('quality-region')), 'No data-quality checks available', 'empty quality state missing');
  assertContains(textOf(document.getElementById('staff-region')), 'No active staff account holds a role', 'empty staff state missing');
});

await check('analytics server-error state is honest and offers a retry', async () => {
  globalThis.fetch = makeFetch({
    me: CLIN_ADMIN,
    analytics: {
      status: 503,
      code: 'dependency_unavailable',
      message: 'Analytics is temporarily unavailable.',
    },
  });
  const document = bootDom([...CHROME_IDS, ...ANALYTICS_IDS]);
  await freshPage('analytics.js');
  await settle();

  const errorRegion = document.getElementById('analytics-error-region');
  assertContains(textOf(errorRegion), 'Analytics is temporarily unavailable.', 'server error message missing');
  assert(errorRegion.querySelector('.state-panel--error'), 'error panel missing');
  assertContains(textOf(errorRegion), 'Try again', 'retry action missing');
  assertEqualLocal(document.getElementById('kpi-region').querySelectorAll('.kpi-tile').length, 0, 'no fake KPI data on error');
});

await check('analytics first load shows the heart loader and replaces it with the summary', async () => {
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
  assert(kpi.querySelector('.loading-block'), 'first load must show the loading placeholder');
  assert(kpi.querySelector('.heart-loader__img'), 'the placeholder must show the heart loader');
  assertContains(textOf(kpi), 'Loading key numbers', 'the placeholder must announce its label');

  releaseSummary();
  await settle();
  assertContains(textOf(kpi), '45', 'content must replace the placeholder');
  assertEqualLocal(kpi.querySelector('.loading-block'), null, 'the placeholder must be gone');
  assertEqualLocal(kpi.querySelector('.heart-loader__img'), null, 'the heart must be gone');
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
  assertEqualLocal(document.querySelector('.loading-block'), null, 'a cache hit must not show the heart loader');
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
    'Елена Георгиева',
    'Иван Петров',
    'Александър-Константин Димитров-Стоянов',
    'Мария Иванова',
    'Антоанета Иванова-Петрова',
  ];
  globalThis.fetch = makeFetch({
    me: DOCTOR,
    patients: {
      items: names.map((display_name, index) => ({
        id: `pg${index + 1}`,
        mrn: `REC-00${index + 1}`,
        display_name,
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

await check('long compound names render wrap-safe in cards/header; pagination keeps thumb targets', async () => {
  // The real seed's longest compound name (long MRN variant included).
  const LONG_NAME = 'Александър-Константин Димитров-Стоянов';
  const LONG_PATIENT = {
    id: 'p13',
    mrn: 'REC-0013-EXTRA-LONG-RECORD-NUMBER-000013',
    display_name: LONG_NAME,
    given_name: 'Александър-Константин',
    family_name: 'Димитров-Стоянов',
    date_of_birth: '1949-04-22',
    sex: 'M',
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
  assertContains(mrnNode.textContent, 'REC-0013-EXTRA-LONG-RECORD-NUMBER-000013', 'the full MRN must render');

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
  assertContains(textOf(document.getElementById('demographics-region')), 'REC-0013-EXTRA-LONG-RECORD-NUMBER-000013', 'the full MRN must render in the header');
});

await check('narrow-width structure: card-stacked tables and labelled keyboard-focusable scroll regions', async () => {
  // The shim has no layout: this asserts the responsive class/attribute
  // CONTRACT that the phone CSS keys off (table-stack at ≤620px, scroll
  // regions focusable for keyboards/TV remotes). Real 320–430px layout is
  // browser-verified (see the checklist in DESIGN.md).
  globalThis.fetch = makeFetch({ patient: PATIENT, summary: SUMMARY, labs: LABS, prescriptions: PRESCRIPTIONS });
  const document = bootDom([...CHROME_IDS, 'page-body', 'patient-heading', 'patient-avatar', 'demographics-region', 'summary-region', 'labs-region', 'prescriptions-region', 'encounters-region', ...PATIENT_TAB_FIXTURE]);
  globalThis.location.search = '?id=p1';
  await freshPage('patient.js');
  await settle();

  await document.getElementById('tab-labs').dispatch('click');
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

/* ============================================================ themes ==== */

await check('error states use the themed 404 and connection illustrations', async () => {
  const document = bootDom([...CHROME_IDS, 'error-host']);
  const host = document.getElementById('error-host');
  const describe = () => ({ title: 'Error', message: 'Something went wrong.', retryable: false });

  ui.renderErrorState(host, { status: 404 }, { describe });
  let art = host.querySelector('.state-panel__art');
  assert(art, '404 error state missing illustration');
  assertContains(art.getAttribute('src'), 'error-404.svg', 'wrong 404 illustration');

  ui.renderErrorState(host, { status: 0 }, { describe });
  art = host.querySelector('.state-panel__art');
  assert(art, 'connection error state missing illustration');
  assertContains(art.getAttribute('src'), 'error-connection.svg', 'wrong connection illustration');

  ui.renderErrorState(host, { status: 403 }, { describe });
  assert(!host.querySelector('.state-panel__art'), '403 state should not use the outage illustration');
});

await check('theme switcher persists Light/Dark/System and follows live OS changes', async () => {
  const store = new Map();
  globalThis.localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  };
  let prefersDark = false;
  const listeners = [];
  const document = bootDom([...CHROME_IDS]);
  globalThis.matchMedia = (query) => ({
    get matches() {
      return query === '(prefers-color-scheme: dark)' ? prefersDark : false;
    },
    media: String(query),
    addEventListener: (type, listener) => {
      if (type === 'change') listeners.push(listener);
    },
    removeEventListener: () => {},
  });
  const theme = await import(`${JS}theme.js?theme-test=${Date.now()}`);
  const root = document.documentElement;
  assertEqualLocal(listeners.length, 1, 'system theme listener was not registered');

  assertEqualLocal(root.getAttribute('data-theme'), 'light', 'system default should resolve to light');
  assertEqualLocal(root.getAttribute('data-theme-mode'), 'system', 'system mode should be the default');

  theme.setTheme('light');
  assertEqualLocal(root.getAttribute('data-theme'), 'light', 'Light choice not applied');
  assertEqualLocal(store.get('medicore-theme'), 'light', 'Light choice not persisted');

  theme.setTheme('dark');
  assertEqualLocal(root.getAttribute('data-theme'), 'dark', 'Dark choice not applied');
  assertEqualLocal(store.get('medicore-theme'), 'dark', 'Dark choice not persisted');

  theme.setTheme('system');
  assertEqualLocal(root.getAttribute('data-theme-mode'), 'system', 'System choice not applied');
  assertEqualLocal(root.getAttribute('data-theme'), 'light', 'System should reflect the current light OS');
  assertEqualLocal(store.get('medicore-theme'), 'system', 'System choice not persisted');

  prefersDark = true;
  for (const listener of listeners) listener({ matches: true });
  assertEqualLocal(root.getAttribute('data-theme'), 'dark', 'System mode did not follow a live OS change');
  prefersDark = false;
  for (const listener of listeners) listener({ matches: false });
  assertEqualLocal(root.getAttribute('data-theme'), 'light', 'System mode did not follow the OS back to light');

  const switcher = theme.mountSwitcher();
  document.body.append(switcher);
  const buttons = switcher.querySelectorAll('.theme-switcher__btn');
  assertEqualLocal(buttons.length, 3, 'theme switcher must expose three modes');
  assertEqualLocal(buttons.map((button) => button.dataset.themeMode).join(','), 'light,dark,system', 'theme modes/order wrong');
  assert(buttons.every((button) => button.getAttribute('aria-label')), 'each theme button needs an accessible label');
  assert(buttons.every((button) => button.getAttribute('title')), 'each theme button needs a tooltip');
  const darkButton = buttons.find((button) => button.dataset.themeMode === 'dark');
  await darkButton.dispatch('click');
  assertEqualLocal(root.getAttribute('data-theme'), 'dark', 'switcher click did not apply Dark');
  assertEqualLocal(darkButton.getAttribute('aria-pressed'), 'true', 'active theme must be pressed');

  // A fresh module instance reads the persisted preference on a reload.
  store.set('medicore-theme', 'dark');
  const reloaded = await import(`${JS}theme.js?theme-reload=${Date.now()}`);
  assertEqualLocal(root.getAttribute('data-theme'), 'dark', 'persisted dark preference not restored on reload');
  assertEqualLocal(reloaded.getTheme().mode, 'dark', 'reloaded theme mode mismatch');
});

/* ============================================ appointments (calendar + voice) */

const APPT_DOCTOR = {
  ...DOCTOR,
  permissions: [
    ...DOCTOR.permissions,
    'appointment:read',
    'appointment:write',
    'appointment:manage_availability',
    'appointment:export',
  ],
};
const APPT_PATIENT_WRITER = {
  ...APPT_DOCTOR,
  permissions: [...APPT_DOCTOR.permissions, 'patient:write'],
};
const APPT_ADMIN = {
  ...CLIN_ADMIN,
  permissions: [
    ...CLIN_ADMIN.permissions,
    'appointment:read',
    'appointment:write',
    'appointment:manage_availability',
  ],
};
const APPT_NURSE = {
  ...NURSE,
  permissions: [...NURSE.permissions, 'appointment:read'],
};
const APPT_IDS = [
  'appointments-context',
  {
    id: 'appointments-mode-switcher',
    children: [
      { tag: 'button', attrs: { 'data-mode': 'calendar' }, text: 'Calendar' },
      { tag: 'button', attrs: { 'data-mode': 'history' }, text: 'History' },
    ],
  },
  'doctor-picker',
  'doctor-select',
  'availability-open',
  'appointment-new',
  'voice-region',
  'voice-help',
  'voice-language',
  'voice-example',
  'voice-example-text',
  { id: 'voice-example-use', tag: 'button', text: 'Insert example request' },
  'voice-listener',
  'voice-record',
  'voice-waveform',
  'voice-cancel',
  'voice-clear',
  'voice-timer',
  'voice-transcript',
  'voice-suggestions',
  'voice-hint',
  'voice-interpret',
  'voice-status',
  'voice-proposal',
  {
    id: 'view-switcher',
    children: [
      { tag: 'button', attrs: { 'data-view': 'month' }, text: 'Month' },
      { tag: 'button', attrs: { 'data-view': 'week' }, text: 'Week' },
      { tag: 'button', attrs: { 'data-view': 'day' }, text: 'Day' },
      { tag: 'button', attrs: { 'data-view': 'upcoming' }, text: 'Upcoming' },
    ],
  },
  'calendar-prev',
  'calendar-today',
  'calendar-next',
  'appointments-refresh',
  'appointments-range',
  'appointments-status',
  'appointments-loading',
  'appointments-calendar',
  'appointments-upcoming',
  'appointments-calendar-card',
  'appointments-history',
  'appointments-history-heading',
  'appointments-history-copy',
  'appointments-history-export',
  'appointments-history-filters',
  'appointments-history-search',
  'appointments-history-suggestions',
  'appointments-history-search-status',
  'appointments-history-start',
  'appointments-history-end',
  'appointments-history-status',
  'appointments-history-status-filter',
  'appointments-history-type',
  'appointments-history-apply',
  'appointments-history-reset',
  'appointments-history-error',
  'appointments-history-loading',
  'appointments-history-region',
  'appointments-history-pagination',
];
const CLINIC_TODAY = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Europe/Sofia',
}).format(new Date());
const APPT_START = new Date();
const APPT_END = new Date(APPT_START.getTime() + 30 * 60 * 1000);
const APPOINTMENT = {
  id: 'a1',
  patient_id: 'p1',
  doctor_id: 'u1',
  start_at: APPT_START.toISOString(),
  end_at: APPT_END.toISOString(),
  duration_minutes: 30,
  appointment_type: 'Consultation',
  status: 'scheduled',
  notes: null,
  patient_display_name: 'Ann Test',
  patient_mrn: 'MRN-0001',
  doctor_display_name: 'Dr Ada Example',
  created_at: APPT_START.toISOString(),
  updated_at: APPT_START.toISOString(),
};
const HISTORY_START = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000);
const HISTORY_END = new Date(HISTORY_START.getTime() + 30 * 60 * 1000);
const APPT_HISTORY_UNMARKED = {
  ...APPOINTMENT,
  id: 'h1',
  start_at: HISTORY_START.toISOString(),
  end_at: HISTORY_END.toISOString(),
  status: 'confirmed',
  updated_at: HISTORY_START.toISOString(),
};
const APPT_HISTORY_COMPLETED = {
  ...APPOINTMENT,
  id: 'h2',
  start_at: new Date(HISTORY_START.getTime() - 24 * 60 * 60 * 1000).toISOString(),
  end_at: new Date(HISTORY_END.getTime() - 24 * 60 * 60 * 1000).toISOString(),
  status: 'completed',
  updated_at: HISTORY_START.toISOString(),
};
const APPT_HISTORY = {
  items: [APPT_HISTORY_UNMARKED, APPT_HISTORY_COMPLETED],
  total: 2,
  limit: 25,
  offset: 0,
};
const APPT_LIFECYCLE = [
  {
    id: 'ev1',
    appointment_id: 'a1',
    event_type: 'created',
    actor_user_id: 'u1',
    actor_display_name: 'Dr Ada Example',
    old_start_at: null,
    old_end_at: null,
    new_start_at: APPT_START.toISOString(),
    new_end_at: APPT_END.toISOString(),
    old_status: null,
    new_status: 'scheduled',
    request_id: 'req-appt-1',
    created_at: APPT_START.toISOString(),
  },
];
const APPT_AVAILABILITY = {
  doctor_id: 'u1',
  timezone: 'Europe/Sofia',
  default_duration_minutes: 30,
  slot_increment_minutes: 30,
  working_hours: [0, 1, 2, 3, 4].map((weekday) => ({
    weekday,
    start_time: '09:00:00',
    end_time: '12:00:00',
  })),
  time_off: [],
};
const APPT_SLOTS = {
  doctor_id: 'u1',
  date: CLINIC_TODAY,
  timezone: 'Europe/Sofia',
  slots: [
    { start_at: APPT_START.toISOString(), end_at: APPT_END.toISOString(), local_start: '09:00', local_end: '09:30', available: true, reason: 'available' },
  ],
};
const APPT_PROPOSAL = {
  patient: { id: 'p1', display_name: 'Ann Test', mrn: 'MRN-0001' },
  patient_candidates: [{ id: 'p1', display_name: 'Ann Test', mrn: 'MRN-0001' }],
  patient_query: 'Ann Test',
  date: CLINIC_TODAY,
  start_time: '09:00',
  start_at: APPT_START.toISOString(),
  end_at: APPT_END.toISOString(),
  duration_minutes: 30,
  appointment_type: 'Consultation',
  notes: 'Follow-up',
  timezone: 'Europe/Sofia',
  missing_fields: [],
  warnings: [],
  can_confirm: true,
  model: 'fake-appointment-model',
  ai_generated: true,
};

function appointmentRoutes(calls, { createdAt = null, interpretBodies = null } = {}) {
  return [
    {
      method: 'GET',
      match: '/api/v1/appointments/config',
      response: {
        timezone: 'Europe/Sofia',
        max_duration_minutes: 480,
        max_range_days: 62,
        history_max_range_days: 366,
      },
    },
    {
      method: 'GET',
      match: '/api/v1/appointments/availability',
      response: APPT_AVAILABILITY,
    },
    {
      method: 'GET',
      match: '/api/v1/appointments/slots',
      response: APPT_SLOTS,
    },
    {
      method: 'GET',
      match: '/api/v1/appointments/history',
      response: APPT_HISTORY,
    },
    {
      method: 'GET',
      match: /\/api\/v1\/appointments\/[^/]+\/history$/,
      response: APPT_LIFECYCLE,
    },
    {
      method: 'GET',
      match: '/api/v1/appointments',
      response: { items: [APPOINTMENT], total: 1, limit: 100, offset: 0 },
    },
    {
      method: 'POST',
      match: '/api/v1/appointments/patient-suggestions',
      response: () => ({ items: [] }),
    },
    {
      method: 'POST',
      match: '/api/v1/appointments/interpret',
      response: ({ body }) => {
        if (interpretBodies) interpretBodies.push(body);
        return APPT_PROPOSAL;
      },
    },
    {
      method: 'POST',
      match: '/api/v1/appointments',
      response: ({ body }) => {
        if (createdAt) createdAt.push(body);
        return { status: 201, ok: true, body: APPOINTMENT };
      },
    },
  ];
}

await check('appointments page renders the month calendar and role-aware nav', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    me: APPT_DOCTOR,
    calls,
    routes: appointmentRoutes(calls),
  });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  assertContains(textOf(document.getElementById('site-nav')), 'Appointments', 'nav item missing');
  assertContains(textOf(document.getElementById('appointments-context')), 'Europe/Sofia', 'clinic timezone missing');
  assertContains(textOf(document.getElementById('appointments-calendar')), 'Ann Test', 'appointment missing from month view');
  assertContains(textOf(document.getElementById('appointments-range')), 'Nov', 'month label missing');
  assert(document.getElementById('appointments-calendar').querySelector('.calendar-grid'), 'month grid missing');
  assert(!document.getElementById('appointment-new').hidden, 'new appointment must be visible for a writer');
  assert(!document.getElementById('voice-region').hidden, 'voice panel must be visible for a writer');
  assert(document.getElementById('doctor-picker').hidden, 'doctor picker must be hidden for a doctor');

  const chip = document.getElementById('appointments-calendar').querySelector('.appointment-chip');
  assert(chip, 'appointment chip missing');
  await chip.dispatch('click');
  await settle();
  const doctorIdentity = document.getElementById('app-dialog-body').querySelector('.doctor-identity');
  assert(doctorIdentity, 'appointment detail doctor identity missing');
  const detailAvatar = doctorIdentity.querySelector('.avatar--photo');
  assert(detailAvatar && detailAvatar.querySelector('img'), 'appointment detail doctor must show the demo portrait');
  await clickButton(document.getElementById('app-dialog-actions'), 'Close');
});

await check('appointment detail Reschedule and Cancel work for a doctor under deferred dialog close', async () => {
  const calls = [];
  const patches = [];
  const cancels = [];
  const nextStart = new Date(APPT_START.getTime() + 30 * 60 * 1000).toISOString();
  const routes = [
    {
      method: 'GET',
      match: '/api/v1/appointments/slots',
      response: {
        ...APPT_SLOTS,
        slots: [
          { start_at: APPT_START.toISOString(), end_at: APPT_END.toISOString(), local_start: '09:00', local_end: '09:30', available: true, reason: 'available' },
          { start_at: nextStart, end_at: new Date(Date.parse(nextStart) + 30 * 60 * 1000).toISOString(), local_start: '09:30', local_end: '10:00', available: true, reason: 'available' },
        ],
      },
    },
    {
      method: 'PATCH',
      match: '/api/v1/appointments/a1',
      response: ({ body }) => {
        patches.push(body);
        return { ...APPOINTMENT, ...body, start_at: body.start_at, duration_minutes: body.duration_minutes };
      },
    },
    {
      method: 'POST',
      match: '/api/v1/appointments/a1/cancel',
      response: () => {
        cancels.push(true);
        return { ...APPOINTMENT, status: 'cancelled' };
      },
    },
    ...appointmentRoutes(calls),
  ];
  globalThis.fetch = makeFetch({ me: APPT_DOCTOR, calls, routes });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  // Real browsers defer the native close callback inside a view transition.
  // Reusing the shared dialog must wait for the close event, not race it.
  document.startViewTransition = (update) => {
    const done = Promise.resolve().then(() => {
      return update();
    });
    return { updateCallbackDone: done, ready: done, finished: done, skipTransition() {} };
  };

  const chip = document.getElementById('appointments-calendar').querySelector('.appointment-chip');
  assert(chip, 'appointment chip missing');
  await chip.dispatch('click');
  await settle();
  await clickButton(document.getElementById('app-dialog-actions'), 'Reschedule');
  await settle(30);
  assertEqualLocal(document.getElementById('app-dialog-title').textContent, 'Reschedule appointment', 'reschedule form did not open after close');
  assert(document.getElementById('app-dialog').open, 'reschedule dialog must stay open');

  const slotButtons = document.getElementById('app-dialog-body').querySelectorAll('.slot-button');
  assert(slotButtons.length >= 2, 'reschedule form must show selectable slots');
  await slotButtons[1].dispatch('click');
  await clickButton(document.getElementById('app-dialog-actions'), 'Save changes');
  await settle(60);
  assertEqualLocal(patches.length, 1, 'reschedule PATCH missing');
  assertEqualLocal(patches[0].start_at, nextStart, 'reschedule must send the newly selected time');
  assertEqualLocal(document.getElementById('app-dialog').open, false, 'reschedule dialog must close after save');

  const chipAgain = document.getElementById('appointments-calendar').querySelector('.appointment-chip');
  assert(chipAgain, 'appointment chip missing after reschedule');
  await chipAgain.dispatch('click');
  await settle();
  // The detail button opens a confirmation and its async handler stays pending
  // until the user answers it; fire the click without awaiting the handler.
  buttonByText(document.getElementById('app-dialog-actions'), 'Cancel appointment').dispatch('click', {});
  await settle(30);
  assertContains(textOf(document.getElementById('app-dialog-body')), 'The time is released for other patients', 'cancel confirmation missing');
  assert(document.getElementById('app-dialog').open, 'cancel confirmation must stay open');
  await clickButton(document.getElementById('app-dialog-actions'), 'Cancel appointment');
  await settle(60);
  assertEqualLocal(cancels.length, 1, 'cancel POST missing');
  assertEqualLocal(document.getElementById('app-dialog').open, false, 'cancel dialog must close after confirmation');
});

await check('admin removes cancelled appointments and expired cancelled rows are pruned', async () => {
  const calls = [];
  const removed = [];
  const staff = {
    items: [{ id: 'u1', email: 'doctor@example.com', display_name: 'Dr Ada Example', roles: ['doctor'], is_active: true }],
  };
  const oldUpdated = new Date(Date.now() - 6 * 60 * 1000).toISOString();
  const freshCancelled = {
    ...APPOINTMENT,
    status: 'cancelled',
    updated_at: new Date().toISOString(),
  };
  const oldCancelled = {
    ...APPOINTMENT,
    id: 'a2',
    patient_display_name: 'Old Cancelled',
    status: 'cancelled',
    updated_at: oldUpdated,
  };
  let items = [freshCancelled, oldCancelled];
  const routes = [
    {
      method: 'GET',
      match: '/api/v1/appointments',
      response: () => ({ items, total: items.length, limit: 100, offset: 0 }),
    },
    {
      method: 'POST',
      match: '/api/v1/appointments/a1/remove-from-calendar',
      response: () => {
        removed.push('a1');
        items = items.filter((item) => item.id !== 'a1');
        return { status: 204, ok: true };
      },
    },
    ...appointmentRoutes(calls),
  ];
  globalThis.fetch = makeFetch({ me: APPT_ADMIN, calls, staff, routes });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  const calendar = document.getElementById('appointments-calendar');
  assertContains(textOf(calendar), 'Ann Test', 'fresh cancelled appointment missing');
  assert(!textOf(calendar).includes('Old Cancelled'), 'expired cancelled appointment must be pruned');

  const chip = calendar.querySelector('.appointment-chip');
  await chip.dispatch('click');
  await settle();
  assertContains(
    textOf(document.getElementById('app-dialog-actions')),
    'Remove from calendar',
    'admin remove action missing',
  );
  buttonByText(document.getElementById('app-dialog-actions'), 'Remove from calendar').dispatch('click', {});
  await settle(30);
  assertContains(
    textOf(document.getElementById('app-dialog-body')),
    'The cancelled appointment will no longer appear',
    'remove confirmation missing',
  );
  await clickButton(document.getElementById('app-dialog-actions'), 'Remove from calendar');
  await settle(60);
  assertEqualLocal(removed.length, 1, 'remove API was not called');
  assert(!textOf(document.getElementById('appointments-calendar')).includes('Ann Test'), 'removed appointment still rendered');

  // Doctors may cancel, but only an Admin gets the immediate removal action.
  globalThis.fetch = makeFetch({
    me: APPT_DOCTOR,
    calls: [],
    routes: [
      {
        method: 'GET',
        match: '/api/v1/appointments',
        response: { items: [freshCancelled], total: 1, limit: 100, offset: 0 },
      },
      ...appointmentRoutes([]),
    ],
  });
  const doctorDocument = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();
  const doctorChip = doctorDocument
    .getElementById('appointments-calendar')
    .querySelector('.appointment-chip');
  await doctorChip.dispatch('click');
  await settle();
  assert(
    !textOf(doctorDocument.getElementById('app-dialog-actions')).includes('Remove from calendar'),
    'doctor must not get the admin removal action',
  );
});

await check('appointment history separates past records, filters, paginates, and exports', async () => {
  const calls = [];
  const staff = {
    items: [{ id: 'u1', email: 'doctor@example.com', display_name: 'Dr Ada Example', roles: ['doctor'], is_active: true }],
  };
  globalThis.fetch = makeFetch({
    me: APPT_ADMIN,
    calls,
    staff,
    routes: appointmentRoutes(calls),
  });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  await clickButton(document.getElementById('appointments-mode-switcher'), 'History');
  await settle();
  assertEqualLocal(document.getElementById('appointments-calendar-card').hidden, true, 'calendar must hide in history mode');
  assertEqualLocal(document.getElementById('appointments-history').hidden, false, 'history must show in history mode');
  const regionText = textOf(document.getElementById('appointments-history-region'));
  assertContains(regionText, 'Ann Test', 'history patient missing');
  assertContains(regionText, 'MRN-0001', 'history MRN missing');
  assertContains(regionText, 'Past — Status not updated', 'unmarked past status missing');
  assertContains(regionText, 'Completed', 'completed status missing');
  assertContains(textOf(document.getElementById('appointments-history-pagination')), 'Showing 1–2 of 2', 'history pagination summary missing');

  const historyCall = calls.find((call) => call.url.includes('/appointments/history') && !call.url.includes('/export'));
  assert(historyCall, 'history endpoint not called');

  const startInput = document.getElementById('appointments-history-start');
  const endInput = document.getElementById('appointments-history-end');
  const startDate = new Date(Date.now() - 20 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const endDate = new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
  startInput.value = startDate;
  endInput.value = endDate;
  document.getElementById('appointments-history-search').value = 'Ann';
  document.getElementById('appointments-history-status-filter').value = 'past_unmarked';
  document.getElementById('appointments-history-type').value = 'General Consultation';
  await document.getElementById('appointments-history-filters').dispatch('submit');
  await settle();
  const filtered = calls.filter((call) => call.url.includes('/appointments/history') && !call.url.includes('/export')).at(-1);
  assertContains(filtered.url, 'q=Ann', 'history patient search not sent');
  assertContains(filtered.url, 'status=past_unmarked', 'history status filter not sent');
  assertContains(filtered.url, 'visit_type=General+Consultation', 'history visit type filter not sent');
  assertContains(filtered.url, 'start=', 'history start date not sent');
  assertContains(filtered.url, 'end=', 'history end date not sent');

  await document.getElementById('appointments-history-export').dispatch('click');
  assertContains(
    globalThis.location.redirectedTo || '',
    '/appointments/history/export.csv',
    'history export URL missing',
  );
  assertContains(globalThis.location.redirectedTo || '', 'start=', 'export start missing');
  assertContains(globalThis.location.redirectedTo || '', 'end=', 'export end missing');
  assertContains(globalThis.location.redirectedTo || '', 'q=Ann', 'export search missing');
});

await check('appointment history patient search shows live name suggestions and selects them', async () => {
  const calls = [];
  const patients = {
    items: [{ id: 'p1', display_name: 'Elena Georgieva', mrn: 'MRN-0001' }],
    total: 1,
    limit: 8,
    offset: 0,
  };
  globalThis.fetch = makeFetch({
    me: APPT_DOCTOR,
    calls,
    patients,
    routes: appointmentRoutes(calls),
  });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();
  await clickButton(document.getElementById('appointments-mode-switcher'), 'History');
  await settle();

  const input = document.getElementById('appointments-history-search');
  const list = document.getElementById('appointments-history-suggestions');
  input.value = 'Ел';
  await input.dispatch('input');
  await settle(350);

  assertEqualLocal(list.hidden, false, 'patient suggestion list must open while typing');
  assertContains(textOf(list), 'Elena Georgieva', 'patient name suggestion missing');
  assertContains(textOf(list), 'MRN-0001', 'patient MRN suggestion missing');
  assertEqualLocal(input.getAttribute('aria-expanded'), 'true', 'combobox must report expanded');
  assert(
    calls.some((call) => call.url.includes('/patients?') && call.url.includes('limit=8')),
    'patient suggestion query was not sent',
  );

  const option = list.querySelector('[role="option"]');
  assert(option, 'patient suggestion option missing');
  await option.dispatch('pointerdown', {});
  await settle();

  assertEqualLocal(input.value, 'Elena Georgieva', 'selecting a suggestion must fill the search field');
  assertEqualLocal(list.hidden, true, 'suggestion list must close after selection');
  const selectedCall = calls
    .filter((call) => call.url.includes('/appointments/history') && !call.url.includes('/export'))
    .at(-1);
  assertContains(selectedCall.url, 'q=Elena+Georgieva', 'selected patient must filter appointment history');

  // Keyboard path: arrow down highlights the first option; Enter selects it.
  input.value = 'Ел';
  await input.dispatch('input');
  await settle(350);
  await input.dispatch('keydown', { key: 'ArrowDown' });
  assertEqualLocal(
    input.getAttribute('aria-activedescendant'),
    'appointments-history-suggestion-0',
    'ArrowDown must activate the first suggestion',
  );
  const activeOption = list.querySelector('[role="option"]');
  assertEqualLocal(activeOption.getAttribute('aria-selected'), 'true', 'active option must be marked selected');
  await input.dispatch('keydown', { key: 'Enter' });
  await settle();
  assertEqualLocal(input.value, 'Elena Georgieva', 'Enter must select the active suggestion');
  assertEqualLocal(list.hidden, true, 'suggestion list must close after keyboard selection');
});

await check('appointment history detail shows lifecycle and doctor status actions', async () => {
  const patches = [];
  const routes = [
    {
      method: 'PATCH',
      match: /\/api\/v1\/appointments\/h1$/,
      response: ({ body }) => {
        patches.push(body);
        return { ...APPT_HISTORY_UNMARKED, status: body.status };
      },
    },
    ...appointmentRoutes([]),
  ];
  globalThis.fetch = makeFetch({ me: APPT_DOCTOR, routes });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  await clickButton(document.getElementById('appointments-mode-switcher'), 'History');
  await settle();
  const viewButton = buttonByText(document.getElementById('appointments-history-region'), 'View');
  assert(viewButton, 'history detail action missing');
  await viewButton.dispatch('click');
  await settle(30);
  assertContains(textOf(document.getElementById('app-dialog-body')), 'Lifecycle', 'lifecycle section missing');
  assertContains(textOf(document.getElementById('app-dialog-body')), 'Created', 'lifecycle event missing');
  assertContains(textOf(document.getElementById('app-dialog-body')), 'Scheduled for', 'created lifecycle summary missing');
  assert(
    !textOf(document.getElementById('app-dialog-body')).includes('— →'),
    'lifecycle must not render empty from-values',
  );
  assertContains(textOf(document.getElementById('app-dialog-body')), 'Dr Ada Example', 'lifecycle actor missing');

  buttonByText(document.getElementById('app-dialog-actions'), 'Mark completed').dispatch('click', {});
  await settle(30);
  assertContains(textOf(document.getElementById('app-dialog-body')), 'records the appointment outcome', 'completed confirmation missing');
  await clickButton(document.getElementById('app-dialog-actions'), 'Mark as completed');
  await settle(60);
  assertEqualLocal(patches.length, 1, 'completed PATCH missing');
  assertEqualLocal(patches[0].status, 'completed', 'completed status missing');

  // Nurses remain strictly read-only.
  globalThis.fetch = makeFetch({ me: APPT_NURSE, routes: appointmentRoutes([]) });
  const nurseDocument = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();
  await clickButton(nurseDocument.getElementById('appointments-mode-switcher'), 'History');
  await settle();
  assertEqualLocal(nurseDocument.getElementById('appointments-history-export').hidden, true, 'nurse must not get history export');
  const nurseView = buttonByText(nurseDocument.getElementById('appointments-history-region'), 'View');
  await nurseView.dispatch('click');
  await settle(30);
  const nurseActions = textOf(nurseDocument.getElementById('app-dialog-actions'));
  assert(!nurseActions.includes('Mark completed'), 'nurse must not get Mark completed');
  assert(!nurseActions.includes('Mark no-show'), 'nurse must not get Mark no-show');
  assert(!nurseActions.includes('Reschedule'), 'nurse must not get Reschedule');
  assert(!nurseActions.includes('Cancel appointment'), 'nurse must not get Cancel');
});

await check('active calendar refetches when the next appointment reaches end_at', async () => {
  const calls = [];
  let calendarCalls = 0;
  const expiring = {
    ...APPOINTMENT,
    id: 'expiring',
    start_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    end_at: new Date(Date.now() + 120).toISOString(),
  };
  globalThis.fetch = makeFetch({
    me: APPT_DOCTOR,
    calls,
    routes: [
      {
        method: 'GET',
        match: '/api/v1/appointments',
        response: () => {
          calendarCalls += 1;
          return calendarCalls === 1
            ? { items: [expiring], total: 1, limit: 100, offset: 0 }
            : { items: [], total: 0, limit: 100, offset: 0 };
        },
      },
      ...appointmentRoutes(calls),
    ],
  });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();
  assertContains(textOf(document.getElementById('appointments-calendar')), 'Ann Test', 'expiring appointment must start visible');
  await settle(700);
  assert(calendarCalls >= 2, 'calendar must refetch after the appointment end time');
  assert(!textOf(document.getElementById('appointments-calendar')).includes('Ann Test'), 'expired appointment must leave the active calendar');
});

await check('appointment form selects a slot and stores a custom Other visit type', async () => {
  const calls = [];
  const created = [];
  globalThis.fetch = makeFetch({
    me: APPT_DOCTOR,
    calls,
    patients: { items: [PATIENT], total: 1, limit: 8, offset: 0 },
    routes: appointmentRoutes(calls, { createdAt: created }),
  });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  await document.getElementById('appointment-new').dispatch('click');
  await settle();
  const search = document.getElementById('appointment-patient-search');
  search.value = 'Ann';
  await search.dispatch('input');
  await settle(400);
  const patientResult = document
    .getElementById('app-dialog-body')
    .querySelectorAll('.patient-result')[0];
  assert(patientResult, 'patient search result missing');
  await patientResult.dispatch('click');

  const slotButton = document.getElementById('app-dialog-body').querySelector('.slot-button');
  assert(slotButton, 'slot missing');
  assert(!slotButton.disabled, 'available slot must be clickable');
  await slotButton.dispatch('click');

  const typeSelect = document.getElementById('appointment-type');
  typeSelect.value = 'other';
  await typeSelect.dispatch('change');
  const customType = document.getElementById('appointment-type-custom');
  assert(customType && !customType.hidden, 'custom visit type field missing for Other');
  customType.value = 'Home visit';

  await clickButton(document.getElementById('app-dialog-actions'), 'Create appointment');
  await settle(60);
  assertEqualLocal(created.length, 1, 'custom-type appointment was not created');
  assertEqualLocal(created[0].appointment_type, 'Home visit', 'custom visit type not sent');
  assertEqualLocal(created[0].duration_minutes, 30, 'duration wrong');
});

await check('appointment picker creates and selects a new patient when none match', async () => {
  const calls = [];
  const createdPatients = [];
  const createdAppointments = [];
  const newPatient = {
    ...PATIENT,
    id: 'p-new',
    mrn: 'MRN-NEW',
    display_name: 'New Patient',
    given_name: 'New',
    family_name: 'Patient',
    date_of_birth: '1992-03-04',
    sex: 'female',
    blood_type: 'O+',
  };
  const routes = [
    {
      method: 'GET',
      match: '/api/v1/patients',
      response: { items: [], total: 0, limit: 8, offset: 0 },
    },
    {
      method: 'POST',
      match: '/api/v1/patients',
      response: ({ body }) => {
        createdPatients.push(body);
        return { status: 201, ok: true, body: newPatient };
      },
    },
    ...appointmentRoutes(calls, { createdAt: createdAppointments }),
  ];
  globalThis.fetch = makeFetch({ me: APPT_PATIENT_WRITER, calls, routes });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  await document.getElementById('appointment-new').dispatch('click');
  await settle();
  const search = document.getElementById('appointment-patient-search');
  search.value = 'Nobody';
  await search.dispatch('input');
  await settle(400);

  const body = document.getElementById('app-dialog-body');
  assertContains(textOf(body), 'No matching patients', 'empty patient search state missing');
  assertContains(textOf(body), 'Add new patient', 'create-patient fallback missing');
  await clickButton(body, 'Add new patient');
  await settle();
  assertContains(textOf(body), 'Add a new patient', 'inline patient form missing');

  document.getElementById('new-patient-given').value = 'New';
  document.getElementById('new-patient-family').value = 'Patient';
  document.getElementById('new-patient-dob').value = '1992-03-04';
  document.getElementById('new-patient-sex').value = 'female';
  document.getElementById('new-patient-blood-type').value = 'O+';
  await clickButton(body, 'Create patient');
  await settle(60);
  assertEqualLocal(createdPatients.length, 1, 'patient create request missing');
  assertEqualLocal(createdPatients[0].given_name, 'New', 'patient given name missing');
  assertContains(document.getElementById('appointment-patient-search').value, 'New Patient', 'new patient not selected');

  const slotButton = body.querySelector('.slot-button');
  assert(slotButton, 'appointment slot missing after patient creation');
  await slotButton.dispatch('click');
  await clickButton(document.getElementById('app-dialog-actions'), 'Create appointment');
  await settle(60);
  assertEqualLocal(createdAppointments.length, 1, 'appointment create request missing');
  assertEqualLocal(createdAppointments[0].patient_id, 'p-new', 'new patient not used for appointment');
});

await check('appointments page is a read-only clinic view for a nurse', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({
    me: APPT_NURSE,
    calls,
    routes: appointmentRoutes(calls),
  });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  assertContains(textOf(document.getElementById('site-nav')), 'Appointments', 'nurse nav item missing');
  assertContains(
    textOf(document.getElementById('appointments-context')),
    'Read-only clinic calendar',
    'read-only context missing',
  );
  assertContains(textOf(document.getElementById('appointments-calendar')), 'Ann Test', 'clinic appointment missing');
  assert(document.getElementById('appointment-new').hidden, 'nurse must not get a new-appointment button');
  assert(document.getElementById('voice-region').hidden, 'nurse must not get voice scheduling');
  assert(document.getElementById('availability-open').hidden, 'nurse must not get availability controls');
  assert(document.getElementById('doctor-picker').hidden, 'nurse must not get the admin doctor picker');
  assert(
    !calls.some((call) => call.url.includes('/appointments/availability')),
    'read-only nurse must not request availability',
  );
  assert(
    !calls.some((call) => call.url.includes('/appointments/slots')),
    'read-only nurse must not request slots',
  );

  const dayButton = document
    .getElementById('view-switcher')
    .querySelectorAll('[data-view]')
    .find((node) => node.dataset.view === 'day');
  await dayButton.dispatch('click');
  await settle();
  assertContains(textOf(document.getElementById('appointments-calendar')), 'Ann Test', 'nurse day schedule missing');
  assert(
    !textOf(document.getElementById('appointments-calendar')).includes('Available times'),
    'nurse day view must not expose booking slots',
  );
  assert(
    !calls.some((call) => call.url.includes('/appointments/slots')),
    'nurse day view must not request slots',
  );
});

await check('appointments admin day view, availability dialog, and voice proposal confirmation', async () => {
  const calls = [];
  const created = [];
  const interpretBodies = [];
  globalThis.fetch = makeFetch({
    me: APPT_ADMIN,
    calls,
    staff: {
      items: [{ id: 'u1', email: 'doctor@example.com', display_name: 'Dr Ada Example', roles: ['doctor'], is_active: true }],
      total: 1,
      limit: 100,
      offset: 0,
    },
    routes: appointmentRoutes(calls, { createdAt: created, interpretBodies }),
  });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  assert(!document.getElementById('doctor-picker').hidden, 'admin must see the doctor picker');
  assertEqualLocal(document.getElementById('doctor-select').value, 'u1', 'admin doctor selection not applied');

  const dayButton = document
    .getElementById('view-switcher')
    .querySelectorAll('[data-view]')
    .find((node) => node.dataset.view === 'day');
  await dayButton.dispatch('click');
  await settle();
  assertContains(textOf(document.getElementById('appointments-calendar')), 'Working hours', 'day view working hours missing');
  assertContains(textOf(document.getElementById('appointments-calendar')), 'Available times', 'day view slots missing');

  await document.getElementById('availability-open').dispatch('click');
  await settle();
  assertContains(textOf(document.getElementById('app-dialog-body')), 'Default duration', 'availability dialog missing');
  assertContains(textOf(document.getElementById('app-dialog-body')), 'Use standard weekdays', 'standard week helper missing');
  await clickButton(document.getElementById('app-dialog-actions'), 'Cancel');
  await settle();

  await document.getElementById('appointment-new').dispatch('click');
  await settle();
  assertContains(textOf(document.getElementById('app-dialog-body')), 'Patient', 'new appointment form missing');
  assertContains(textOf(document.getElementById('app-dialog-actions')), 'Create appointment', 'explicit create action missing');

  const typeSelect = document.getElementById('appointment-type');
  assert(typeSelect, 'visit type select missing');
  assertEqualLocal(typeSelect.querySelectorAll('option').length, 19, 'visit catalogue incomplete');
  assertEqualLocal(typeSelect.value, 'general', 'default visit type should be General Consultation');
  typeSelect.value = 'new_patient';
  await typeSelect.dispatch('change');
  assertEqualLocal(document.getElementById('appointment-duration').value, '45', 'duration should follow New Patient Consultation');
  typeSelect.value = 'other';
  await typeSelect.dispatch('change');
  const customType = document.getElementById('appointment-type-custom');
  assert(customType && !customType.hidden, 'custom visit type input must appear for Other');

  const slotButton = document.getElementById('app-dialog-body').querySelector('.slot-button');
  assert(slotButton, 'available slot button missing');
  assert(!slotButton.disabled, 'available slot must not be disabled');
  assert(!slotButton.hasAttribute('disabled'), 'disabled=false must not leave a disabled attribute');
  await slotButton.dispatch('click');
  assert(slotButton.classList.contains('is-selected'), 'clicking a slot must select it');
  assertEqualLocal(slotButton.getAttribute('aria-pressed'), 'true', 'selected slot must expose aria-pressed');

  await clickButton(document.getElementById('app-dialog-actions'), 'Cancel');
  await settle();

  const transcript = document.getElementById('voice-transcript');
  assert(
    document.getElementById('voice-example-use'),
    'voice example insert button missing',
  );
  assertContains(textOf(document.getElementById('voice-example-text')), 'Example:', 'voice example text missing');
  const voiceLanguage = document.getElementById('voice-language');
  voiceLanguage.value = 'en';
  await voiceLanguage.dispatch('change');
  assertContains(textOf(document.getElementById('voice-example-text')), 'Book a 30-minute follow-up visit', 'English example not shown');
  await document.getElementById('voice-example-use').dispatch('click');
  assertContains(transcript.value, 'Elena Georgieva', 'example request not inserted');
  assertEqualLocal(document.getElementById('voice-interpret').disabled, false, 'example must enable the propose action');

  transcript.value = 'Book Ann Test tomorrow at 09:00.';
  await transcript.dispatch('input');
  await document.getElementById('voice-interpret').dispatch('click');
  await settle(50);
  assertContains(textOf(document.getElementById('voice-proposal')), 'AI-generated', 'proposal is not labelled AI-generated');
  assertContains(textOf(document.getElementById('voice-proposal')), 'Review & create', 'proposal confirmation action missing');
  const interpretCall = calls.find((call) => call.url.includes('/appointments/interpret'));
  assert(interpretCall, 'interpret request missing');
  assertContains(interpretCall.url, 'doctor_id=u1', 'admin interpret must be scoped to the selected doctor');
  assertEqualLocal(interpretBodies[0].text, 'Book Ann Test tomorrow at 09:00.', 'transcript text not sent');

  await clickButton(document.getElementById('voice-proposal'), 'Review & create');
  await settle();
  assertContains(textOf(document.getElementById('app-dialog-body')), 'Ann Test', 'proposal patient not prefilled');
  assertContains(textOf(document.getElementById('app-dialog-actions')), 'Create appointment', 'confirmation form missing');

  await clickButton(document.getElementById('app-dialog-actions'), 'Create appointment');
  await settle(60);
  assertEqualLocal(created.length, 1, `explicit confirmation did not create exactly one appointment (dialog: ${textOf(document.getElementById('app-dialog-body'))})`);
  assertEqualLocal(created[0].patient_id, 'p1', 'created appointment patient wrong');
  assertEqualLocal(created[0].doctor_id, 'u1', 'admin creation must target the selected doctor');
});

await check('voice listener runs idle, listening, processing, success, error, cancel and permission states', async () => {
  const calls = [];
  const voiceCalls = [];
  const suggestionCalls = [];
  const correctionCalls = [];
  const stoppedTracks = { count: 0 };
  let permissionDenied = false;
  const stream = {
    getTracks() {
      return [{ stop() { stoppedTracks.count += 1; } }];
    },
  };
  const fakeNavigator = {
    mediaDevices: {
      async getUserMedia() {
        if (permissionDenied) {
          const error = new Error('Permission denied');
          error.name = 'NotAllowedError';
          throw error;
        }
        return stream;
      },
    },
  };

  class FakeMediaRecorder {
    static isTypeSupported() { return true; }
    constructor(_stream, options = {}) {
      this.mimeType = options.mimeType || 'audio/webm';
      this.state = 'inactive';
      this.listeners = {};
    }
    addEventListener(type, handler) {
      (this.listeners[type] = this.listeners[type] || []).push(handler);
    }
    emit(type, event = {}) {
      for (const handler of this.listeners[type] || []) handler(event);
    }
    start() { this.state = 'recording'; }
    stop() {
      if (this.state !== 'recording') return;
      this.state = 'inactive';
      this.emit('dataavailable', {
        data: new Blob(['voice-listener-test-'.repeat(80)], { type: this.mimeType }),
      });
      setTimeout(() => this.emit('stop'), 0);
    }
  }

  class FakeAudioContext {
    constructor() { this.closed = false; }
    resume() { return Promise.resolve(); }
    close() { this.closed = true; return Promise.resolve(); }
    createMediaStreamSource() { return { connect() {} }; }
    createAnalyser() {
      return {
        fftSize: 1024,
        smoothingTimeConstant: 0,
        connect() {},
        getByteTimeDomainData(data) { data.fill(170); },
      };
    }
  }

  const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const originalRecorder = globalThis.MediaRecorder;
  const originalAudioContext = globalThis.AudioContext;
  const originalRaf = globalThis.requestAnimationFrame;
  const originalCancelRaf = globalThis.cancelAnimationFrame;
  let rafCount = 0;
  try {
    Object.defineProperty(globalThis, 'navigator', {
      value: fakeNavigator,
      configurable: true,
      writable: true,
    });
    globalThis.MediaRecorder = FakeMediaRecorder;
    globalThis.AudioContext = FakeAudioContext;
    globalThis.requestAnimationFrame = (handler) => {
      const id = ++rafCount;
      if (rafCount <= 4) setTimeout(handler, 0);
      return id;
    };
    globalThis.cancelAnimationFrame = () => {};

    const routes = [
      {
        method: 'POST',
        match: '/api/v1/voice/transcriptions',
        response: ({ formData }) => {
          voiceCalls.push(formData);
          return {
            text: 'Book a follow-up for Elena Georgievah. M-R-N-0-0-0-1.',
            language: 'en',
            model: 'base',
            duration_seconds: 2.4,
          };
        },
      },
      {
        method: 'POST',
        match: '/api/v1/appointments/patient-suggestions',
        response: ({ body }) => {
          suggestionCalls.push(body);
          return {
            items: [
              {
                display_name: 'Елена Георгиева',
                mrn: 'MRN-0001',
                matched_name_text: 'Elena Georgievah',
                matched_mrn_text: 'M-R-N-0-0-0-1',
              },
            ],
          };
        },
      },
      {
        method: 'POST',
        match: '/api/v1/appointments/transcription-corrections',
        response: ({ body }) => {
          correctionCalls.push(body);
          return {
            language: body.language,
            has_suggestions: false,
            suggestions: [],
            analysis_available: true,
            model: 'fake-correction-model',
          };
        },
      },
      ...appointmentRoutes([]),
    ];
    globalThis.fetch = makeFetch({ me: APPT_DOCTOR, calls, routes });
    const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
    await freshPage('appointments.js');
    await settle();

    const listener = document.getElementById('voice-listener');
    const orb = document.getElementById('voice-record');
    const status = document.getElementById('voice-status');
    const waveform = document.getElementById('voice-waveform');
    assertEqualLocal(listener.dataset.state, 'idle', 'voice listener must start idle');
    assertContains(textOf(status), 'Tap to speak', 'idle instruction missing');
    assertEqualLocal(orb.disabled, false, 'idle orb must be enabled');
    assertEqualLocal(orb.getAttribute('aria-label'), 'Start voice recording', 'idle accessible label missing');

    await orb.dispatch('click');
    await settle(40);
    assertEqualLocal(listener.dataset.state, 'listening', 'listening state not entered');
    assertContains(textOf(status), 'Listening', 'listening label missing');
    assertEqualLocal(document.getElementById('voice-cancel').hidden, false, 'cancel action must be available while listening');
    assertContains(textOf(document.getElementById('voice-timer')), '0:0', 'recording timer missing');
    assertEqualLocal(waveform.dataset.level, '4', 'waveform must respond to microphone input');

    await orb.dispatch('click');
    await settle(80);
    assertEqualLocal(voiceCalls.length, 1, 'stopping must submit exactly one transcription request');
    assertEqualLocal(voiceCalls[0].get('file').type.startsWith('audio/'), true, 'audio upload type missing');
    assertEqualLocal(voiceCalls[0].get('language'), 'auto', 'auto-detect language not sent');
    assertEqualLocal(document.getElementById('voice-transcript').value, 'Book a follow-up for Elena Georgievah. M-R-N-0-0-0-1.', 'transcript not returned to workflow');
    assertEqualLocal(listener.dataset.state, 'success', 'success state missing');
    assertContains(textOf(status), 'Transcription ready', 'success label missing');
    assertContains(textOf(status), 'English', 'detected language missing');
    assertEqualLocal(stoppedTracks.count >= 1, true, 'microphone tracks must be released after recording');
    assertEqualLocal(suggestionCalls.length, 1, 'patient suggestion request missing');
    assertEqualLocal(suggestionCalls[0].text, 'Book a follow-up for Elena Georgievah. M-R-N-0-0-0-1.', 'suggestion text not sent');
    assertEqualLocal(correctionCalls.length, 1, 'transcription correction request missing');
    assertEqualLocal(correctionCalls[0].language, 'en', 'correction language not sent');
    assertEqualLocal(correctionCalls[0].text, 'Book a follow-up for Elena Georgievah. M-R-N-0-0-0-1.', 'correction text not sent');
    const suggestionsHost = document.getElementById('voice-suggestions');
    assertEqualLocal(suggestionsHost.hidden, false, 'Did-you-mean suggestions must be visible');
    assertContains(textOf(suggestionsHost), 'Did you mean', 'suggestion heading missing');
    assertContains(textOf(suggestionsHost), 'Елена Георгиева', 'correct patient name missing');
    assertContains(textOf(suggestionsHost), 'MRN-0001', 'correct MRN missing');
    await suggestionsHost.querySelector('.voice-suggestion').dispatch('click');
    assertEqualLocal(document.getElementById('voice-transcript').value, 'Book a follow-up for Елена Георгиева. MRN-0001.', 'suggestion did not correct the transcript');
    assertEqualLocal(suggestionsHost.querySelector('.voice-suggestion'), null, 'applied patient suggestion must be removed');
    assert(suggestionsHost.querySelector('.voice-corrections__applied'), 'applied correction undo row missing');

    permissionDenied = true;
    await orb.dispatch('click');
    await settle(40);
    assertEqualLocal(listener.dataset.state, 'error', 'permission error state missing');
    assertContains(textOf(status), 'permission was denied', 'permission error message missing');
    assertEqualLocal(orb.disabled, false, 'orb must allow retry after permission error');
    permissionDenied = false;

    await orb.dispatch('click');
    await settle(40);
    assertEqualLocal(listener.dataset.state, 'listening', 'retry did not start listening');
    await document.getElementById('voice-cancel').dispatch('click');
    await settle(40);
    assertEqualLocal(listener.dataset.state, 'idle', 'cancel must return to idle');
    assertContains(textOf(status), 'Recording cancelled', 'cancel message missing');
    assertEqualLocal(document.getElementById('voice-cancel').hidden, true, 'cancel action must hide after cancel');

    delete fakeNavigator.mediaDevices.getUserMedia;
    await orb.dispatch('click');
    await settle(20);
    assertEqualLocal(listener.dataset.state, 'error', 'unsupported microphone state missing');
    assertContains(textOf(status), 'not supported', 'unsupported microphone message missing');
  } finally {
    if (originalNavigator) Object.defineProperty(globalThis, 'navigator', originalNavigator);
    if (originalRecorder === undefined) delete globalThis.MediaRecorder;
    else globalThis.MediaRecorder = originalRecorder;
    if (originalAudioContext === undefined) delete globalThis.AudioContext;
    else globalThis.AudioContext = originalAudioContext;
    if (originalRaf === undefined) delete globalThis.requestAnimationFrame;
    else globalThis.requestAnimationFrame = originalRaf;
    if (originalCancelRaf === undefined) delete globalThis.cancelAnimationFrame;
    else globalThis.cancelAnimationFrame = originalCancelRaf;
  }
});

await check('transcription corrections can be checked, accepted, rejected, undone, and invalidated', async () => {
  const calls = [];
  const correctionCalls = [];
  const routes = [
    {
      method: 'POST',
      match: '/api/v1/appointments/patient-suggestions',
      response: () => ({ items: [] }),
    },
    {
      method: 'POST',
      match: '/api/v1/appointments/transcription-corrections',
      response: ({ body }) => {
        correctionCalls.push(body);
        return {
          language: body.language,
          has_suggestions: true,
          analysis_available: true,
          model: 'fake-correction-model',
          suggestions: [
            {
              original_text: 'follow-up',
              suggested_text: 'Follow-up Visit',
              reason: 'Possible appointment-type mismatch',
              confidence: 'medium',
              category: 'appointment_type',
            },
            {
              original_text: 'tomorrow',
              suggested_text: 'next Monday',
              reason: 'Possible weekday mismatch',
              confidence: 'low',
              category: 'weekday',
            },
          ],
        };
      },
    },
    ...appointmentRoutes(calls),
  ];
  globalThis.fetch = makeFetch({ me: APPT_DOCTOR, calls, routes });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  const transcript = document.getElementById('voice-transcript');
  const host = document.getElementById('voice-suggestions');
  transcript.value = 'Book a follow-up for Elena Georgievah tomorrow at 10:00.';
  await transcript.dispatch('input');
  await settle(30);
  assertContains(textOf(host), 'Check transcription', 'manual transcription check action missing');

  await clickButton(host, 'Check transcription');
  await settle(40);
  assertEqualLocal(correctionCalls.length, 1, 'correction endpoint not called');
  assertEqualLocal(correctionCalls[0].language, 'en', 'correction language not sent');
  assertContains(textOf(host), 'Did you mean', 'correction heading missing');
  assertContains(textOf(host), 'follow-up', 'original correction phrase missing');
  assertContains(textOf(host), 'Follow-up Visit', 'suggested correction phrase missing');
  assertContains(textOf(host), 'Keep original', 'keep-original action missing');

  const cards = host.querySelectorAll('.voice-correction');
  assertEqualLocal(cards.length, 2, 'two correction cards expected');
  const weekdayCard = cards.find((card) => textOf(card).includes('next Monday'));
  assert(weekdayCard, 'weekday correction card missing');
  await clickButton(weekdayCard, 'Keep original');
  await settle();
  assert(!textOf(host).includes('next Monday'), 'dismissed correction must disappear');
  assertContains(textOf(host), 'Follow-up Visit', 'remaining correction must stay visible');

  const typeCard = host.querySelectorAll('.voice-correction').find((card) => textOf(card).includes('Follow-up Visit'));
  assert(typeCard, 'appointment-type correction card missing');
  await clickButton(typeCard, 'Use suggestion');
  await settle();
  assertContains(transcript.value, 'Follow-up Visit', 'accepted correction not applied to transcript');
  assertContains(textOf(host), 'Applied', 'applied-correction row missing');
  assertContains(textOf(host), 'Applied · Appointment type', 'applied-correction category missing');
  assertContains(textOf(host), 'Transcript changed. Check transcription again.', 'post-apply recheck state missing');
  assert(buttonByText(host, 'Check transcription'), 'post-apply recheck action missing');
  assert(
    !textOf(host).includes('No obvious transcription issues detected.'),
    'an applied correction must not report that no issues were detected',
  );
  const undo = buttonByText(host, 'Undo');
  assert(undo, 'undo action missing');
  await undo.dispatch('click');
  await settle();
  assertEqualLocal(
    transcript.value,
    'Book a follow-up for Elena Georgievah tomorrow at 10:00.',
    'undo did not restore the transcript',
  );

  transcript.value = 'Book a Follow-up Visit for Elena Georgievah tomorrow at 10:00.';
  await transcript.dispatch('input');
  await settle(30);
  assert(!textOf(host).includes('Use suggestion'), 'stale correction cards must be removed after manual edit');
  assertContains(textOf(host), 'Check transcription', 'recheck action missing after manual edit');
});

await check('transcription correction failure is non-blocking and retryable', async () => {
  const calls = [];
  const routes = [
    {
      method: 'POST',
      match: '/api/v1/appointments/patient-suggestions',
      response: () => ({ items: [] }),
    },
    {
      method: 'POST',
      match: '/api/v1/appointments/transcription-corrections',
      response: () => ({ status: 503, message: 'Correction analysis unavailable.' }),
    },
    ...appointmentRoutes(calls),
  ];
  globalThis.fetch = makeFetch({ me: APPT_DOCTOR, calls, routes });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  const transcript = document.getElementById('voice-transcript');
  const host = document.getElementById('voice-suggestions');
  transcript.value = 'Book a follow-up for Elena Georgievah tomorrow at 10:00.';
  await transcript.dispatch('input');
  await settle(30);
  await clickButton(host, 'Check transcription');
  await settle(40);

  assertContains(textOf(host), 'Unable to check transcription. You can still edit it manually.', 'correction failure state missing');
  assertContains(textOf(host), 'Retry check', 'retry action missing');
  assertEqualLocal(document.getElementById('voice-interpret').disabled, false, 'correction failure must not disable appointment proposal');
});

await check('transcription correction empty state is honest', async () => {
  const calls = [];
  const routes = [
    {
      method: 'POST',
      match: '/api/v1/appointments/patient-suggestions',
      response: () => ({ items: [] }),
    },
    {
      method: 'POST',
      match: '/api/v1/appointments/transcription-corrections',
      response: ({ body }) => ({
        language: body.language,
        has_suggestions: false,
        suggestions: [],
        analysis_available: true,
        model: 'fake-correction-model',
      }),
    },
    ...appointmentRoutes(calls),
  ];
  globalThis.fetch = makeFetch({ me: APPT_DOCTOR, calls, routes });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  const transcript = document.getElementById('voice-transcript');
  const host = document.getElementById('voice-suggestions');
  transcript.value = 'Book a follow-up for Elena Georgievah tomorrow at 10:00.';
  await transcript.dispatch('input');
  await settle(30);
  await clickButton(host, 'Check transcription');
  await settle(40);

  assertContains(textOf(host), 'No obvious transcription issues detected.', 'empty correction state missing');
  assertEqualLocal(document.getElementById('voice-interpret').disabled, false, 'empty correction state must not block proposal');
});

await check('EN/BG switcher translates the shell and restores English', async () => {
  const calls = [];
  globalThis.fetch = makeFetch({ me: APPT_DOCTOR, calls, routes: appointmentRoutes([]) });
  const document = bootDom([...CHROME_IDS, ...APPT_IDS]);
  await freshPage('appointments.js');
  await settle();

  const host = document.querySelector('.language-switcher');
  assert(host, 'language switcher missing from the top bar');
  const buttons = host.querySelectorAll('.language-switcher__btn');
  assertEqualLocal(buttons.length, 2, 'expected EN and BG buttons');
  const bg = buttons.find((button) => button.dataset.language === 'bg');
  const en = buttons.find((button) => button.dataset.language === 'en');
  assert(bg && en, 'EN/BG buttons missing');
  assertEqualLocal(en.getAttribute('aria-pressed'), 'true', 'English must be the test default');

  await bg.dispatch('click');
  await settle();
  assertEqualLocal(document.documentElement.getAttribute('lang'), 'bg', 'BG must set the document language');
  assertContains(textOf(document.getElementById('site-nav')), 'Табло', 'navigation was not translated');
  assertEqualLocal(bg.getAttribute('aria-pressed'), 'true', 'BG button must be active');

  await en.dispatch('click');
  await settle();
  assertEqualLocal(document.documentElement.getAttribute('lang'), 'en', 'EN must restore the document language');
  assertContains(textOf(document.getElementById('site-nav')), 'Dashboard', 'English navigation was not restored');
});

/* ------------------------------------------------------------------ report */

const failed = results.filter((r) => !r.ok);
for (const result of results) {
  process.stdout.write(`${result.ok ? 'PASS' : 'FAIL'} ${result.name}${result.ok ? '' : `: ${result.detail}`}\n`);
}
process.stdout.write(`\n${results.length - failed.length}/${results.length} render-path scenarios passed\n`);
if (failed.length > 0) process.exitCode = 1;
