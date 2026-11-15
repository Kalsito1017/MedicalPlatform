// ---------------------------------------------------------------------------
// appointments.js — clinic appointment calendar (manual + voice + AI proposal).
//
// API contract (docs/API.md):
//   GET    /appointments/config
//   GET    /appointments?start&end[&doctor_id]
//   GET    /appointments/{id}
//   POST   /appointments                    (explicit doctor confirmation)
//   PATCH  /appointments/{id}               (reschedule / notes)
//   POST   /appointments/{id}/cancel
//   GET    /appointments/availability[?doctor_id]
//   PUT    /appointments/availability[?doctor_id]
//   GET    /appointments/slots?date[&doctor_id]
//   POST   /appointments/interpret          (AI proposal only — never writes)
//   POST   /voice/transcriptions            (MediaRecorder -> whisper.cpp)
//
// All clinic wall-clock interpretation happens in Europe/Sofia. The server
// revalidates every create/reschedule under advisory locks; this page's checks
// are UX only. No audio or transcript is written to web storage.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as cache from './cache.js';
import * as commandMenu from './command-menu.js?v=20261109j';
import * as i18n from './i18n.js';
import * as overlayHistory from './overlay-history.js';
import * as prefetch from './prefetch.js';
import * as toast from './toast.js';
import * as ui from './ui.js';
import { buildPatientCreateForm, validatePatientInput } from './patient-create.js';
import { VISIT_TYPES, findVisitType } from './visit-types.js';

const CLINIC_TZ = 'Europe/Sofia';
const CANCELLED_CALENDAR_VISIBLE_MS = 5 * 60 * 1000;
const HISTORY_PAGE_SIZE = 25;
const HISTORY_SUGGESTION_LIMIT = 8;
const HISTORY_SUGGESTION_DEBOUNCE_MS = 250;
const MAX_EXPIRY_TIMER_MS = 60 * 60 * 1000;
const VOICE_MAX_SECONDS = 60;
const SUGGESTION_DEBOUNCE_MS = 400;
const CORRECTION_REQUEST_TIMEOUT_MS = 20000;
const CORRECTION_MAX_INLINE = 3;
const VOICE_EXAMPLES = {
  bg: 'Запиши 30-минутна контролна визита за Елена Георгиева, MRN-0001, следващия понеделник от 10:00. Причина: контрол на кръвното налягане.',
  en: 'Book a 30-minute follow-up visit for Elena Georgieva, MRN-0001, next Monday at 10:00. Reason: blood pressure review.',
};
const SEARCH_DEBOUNCE_MS = 250;
const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const DAY_NAMES_FULL = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];
const STATUS_LABEL = {
  scheduled: 'Scheduled',
  confirmed: 'Confirmed',
  completed: 'Completed',
  cancelled: 'Cancelled',
  no_show: 'No show',
};
const STATUS_BADGE = {
  scheduled: 'badge--info',
  confirmed: 'badge--ok',
  completed: 'badge--neutral',
  cancelled: 'badge--alert',
  no_show: 'badge--warn',
  past_unmarked: 'badge--warn',
};
const HISTORY_STATUS_LABEL = {
  ...STATUS_LABEL,
  past_unmarked: 'Past — Status not updated',
};

const state = {
  me: null,
  timezone: CLINIC_TZ,
  isAdmin: false,
  isNurse: false,
  canWrite: false,
  canManage: false,
  canExport: false,
  doctors: [],
  doctorId: null,
  mode: 'calendar',
  view: 'month',
  anchorKey: '',
  appointments: [],
  cancelledTimer: null,
  activeExpiryTimer: null,
  slots: null,
  availability: null,
  history: {
    items: [],
    total: 0,
    offset: 0,
    limit: HISTORY_PAGE_SIZE,
    loading: false,
    filters: { q: '', start: '', end: '', status: '', visit_type: '' },
    suggestionTimer: null,
    suggestionSeq: 0,
    suggestions: [],
    suggestionActive: -1,
  },
  loading: false,
  voice: {
    recorder: null,
    stream: null,
    chunks: [],
    blob: null,
    startedAt: 0,
    timer: null,
    stopTimer: null,
    session: 0,
    state: 'idle',
    cancelRequested: false,
    audioContext: null,
    analyser: null,
    meterData: null,
    meterFrame: null,
    level: 0,
    suggestionTimer: null,
    suggestionSeq: 0,
    suggestions: [],
    corrections: [],
    correctionStatus: 'idle',
    correctionSeq: 0,
    correctionController: null,
    correctionTimer: null,
    correctionText: '',
    correctionLanguage: '',
    correctionModel: '',
    correctionAnalysisAvailable: true,
    detectedLanguage: '',
    dismissedCorrections: new Set(),
    appliedCorrections: [],
    suggestionsExpanded: false,
  },
};

/* ------------------------------------------------- cancelled calendar cleanup */

function cancelledExpiry(appointment) {
  if (!appointment || appointment.status !== 'cancelled') return null;
  const updated = Date.parse(appointment.updated_at);
  return Number.isFinite(updated) ? updated + CANCELLED_CALENDAR_VISIBLE_MS : null;
}

function pruneExpiredCancelled(items, now = Date.now()) {
  return (items || []).filter((appointment) => {
    const expiry = cancelledExpiry(appointment);
    return expiry == null || expiry > now;
  });
}

function clearCancelledCleanup() {
  if (state.cancelledTimer) {
    clearTimeout(state.cancelledTimer);
    state.cancelledTimer = null;
  }
}

function scheduleCancelledCleanup() {
  clearCancelledCleanup();
  const now = Date.now();
  const expiries = state.appointments
    .map(cancelledExpiry)
    .filter((expiry) => expiry != null && expiry > now);
  if (expiries.length === 0) return;
  const delay = Math.max(250, Math.min(...expiries) - now + 50);
  state.cancelledTimer = setTimeout(() => {
    state.cancelledTimer = null;
    state.appointments = pruneExpiredCancelled(state.appointments);
    renderCalendar();
    scheduleCancelledCleanup();
  }, delay);
  // Node render tests should not stay alive for the 5-minute timer; browsers
  // do not expose unref(), so this is a harmless no-op there.
  if (typeof state.cancelledTimer.unref === 'function') state.cancelledTimer.unref();
}

function clearActiveExpiryCleanup() {
  if (state.activeExpiryTimer) {
    clearTimeout(state.activeExpiryTimer);
    state.activeExpiryTimer = null;
  }
}

/**
 * Schedule a backend refetch when the earliest in-progress/future appointment
 * reaches its actual end_at. The server remains authoritative; this timer only
 * prompts a fresh list request so an already-open calendar updates promptly.
 */
function scheduleActiveExpiryCleanup() {
  clearActiveExpiryCleanup();
  if (state.mode !== 'calendar') return;
  const now = Date.now();
  const endTimes = state.appointments
    .filter((appointment) => ['scheduled', 'confirmed'].includes(appointment.status))
    .map((appointment) => Date.parse(appointment.end_at))
    .filter((end) => Number.isFinite(end) && end > now);
  if (endTimes.length === 0) return;
  const nextEnd = Math.min(...endTimes);
  const delay = Math.max(250, Math.min(MAX_EXPIRY_TIMER_MS, nextEnd - now + 250));
  state.activeExpiryTimer = setTimeout(() => {
    state.activeExpiryTimer = null;
    if (Date.now() >= nextEnd) loadData();
    else scheduleActiveExpiryCleanup();
  }, delay);
  if (typeof state.activeExpiryTimer.unref === 'function') {
    state.activeExpiryTimer.unref();
  }
}

/* --------------------------------------------------------------- date utils */

const partsFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: CLINIC_TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
});

function clinicParts(date = new Date()) {
  const values = {};
  for (const part of partsFormatter.formatToParts(date)) {
    if (part.type !== 'literal') values[part.type] = part.value;
  }
  return {
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    hour: Number(values.hour),
    minute: Number(values.minute),
    second: Number(values.second),
  };
}

function pad2(value) {
  return String(value).padStart(2, '0');
}

function dateKeyFromParts(year, month, day) {
  return `${year}-${pad2(month)}-${pad2(day)}`;
}

function clinicTodayKey() {
  const parts = clinicParts();
  return dateKeyFromParts(parts.year, parts.month, parts.day);
}

function clinicKeyFromIso(iso) {
  const parts = clinicParts(new Date(iso));
  return dateKeyFromParts(parts.year, parts.month, parts.day);
}

function parseDateKey(key) {
  const [year, month, day] = key.split('-').map(Number);
  return { year, month, day };
}

function addDaysKey(key, delta) {
  const { year, month, day } = parseDateKey(key);
  const date = new Date(Date.UTC(year, month - 1, day + delta));
  return dateKeyFromParts(
    date.getUTCFullYear(),
    date.getUTCMonth() + 1,
    date.getUTCDate()
  );
}

function weekdayIndex(key) {
  const { year, month, day } = parseDateKey(key);
  const jsDay = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  return (jsDay + 6) % 7; // 0 = Monday
}

function startOfWeekKey(key) {
  return addDaysKey(key, -weekdayIndex(key));
}

function startOfMonthKey(key) {
  const { year, month } = parseDateKey(key);
  return `${year}-${pad2(month)}-01`;
}

function shiftMonthKey(key, delta) {
  const { year, month } = parseDateKey(key);
  const date = new Date(Date.UTC(year, month - 1 + delta, 1));
  return `${date.getUTCFullYear()}-${pad2(date.getUTCMonth() + 1)}-01`;
}

function dateKeyObject(key) {
  const { year, month, day } = parseDateKey(key);
  return new Date(Date.UTC(year, month - 1, day, 12, 0, 0));
}

function dateLocale() {
  return i18n.getLanguage() === 'bg' ? 'bg-BG' : 'en-GB';
}

function formatDay(key, options = {}) {
  return new Intl.DateTimeFormat(dateLocale(), {
    timeZone: 'UTC',
    ...options,
  }).format(dateKeyObject(key));
}

function formatMonthYear(key) {
  return formatDay(startOfMonthKey(key), { month: 'long', year: 'numeric' });
}

function formatWeekRange(startKey) {
  const endKey = addDaysKey(startKey, 6);
  const start = dateKeyObject(startKey);
  const end = dateKeyObject(endKey);
  const sameMonth = start.getUTCMonth() === end.getUTCMonth();
  const startText = new Intl.DateTimeFormat(dateLocale(), {
    timeZone: 'UTC',
    day: 'numeric',
    month: sameMonth ? undefined : 'short',
  }).format(start);
  const endText = new Intl.DateTimeFormat(dateLocale(), {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(end);
  return `${startText} – ${endText}`;
}

const CLINIC_FORMATTERS = new Map();

function clinicFormatter(kind) {
  const key = `${kind}:${dateLocale()}`;
  let formatter = CLINIC_FORMATTERS.get(key);
  if (!formatter) {
    const options = kind === 'time'
      ? { timeZone: CLINIC_TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }
      : kind === 'long-date'
        ? {
            timeZone: CLINIC_TZ,
            weekday: 'long',
            day: 'numeric',
            month: 'long',
            year: 'numeric',
          }
        : {
            timeZone: CLINIC_TZ,
            weekday: 'short',
            day: 'numeric',
            month: 'short',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hourCycle: 'h23',
          };
    formatter = new Intl.DateTimeFormat(dateLocale(), options);
    CLINIC_FORMATTERS.set(key, formatter);
  }
  return formatter;
}

function formatClinicTime(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : clinicFormatter('time').format(date);
}

function formatClinicDateTime(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : clinicFormatter('datetime').format(date);
}

function formatClinicLongDate(key) {
  return clinicFormatter('long-date').format(dateKeyObject(key));
}

/**
 * Convert a clinic wall-clock date/time into UTC using the same DST semantics
 * as the backend: resolve the zone offset at the target instant, re-check the
 * round trip, and fail closed when the local time does not exist.
 */
function clinicWallToUtc(dateKey, timeValue) {
  const { year, month, day } = parseDateKey(dateKey);
  const [hour, minute] = String(timeValue || '00:00').split(':').map(Number);
  const target = Date.UTC(year, month - 1, day, hour, minute, 0);
  let stamp = target;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const parts = clinicParts(new Date(stamp));
    const asUtc = Date.UTC(
      parts.year,
      parts.month - 1,
      parts.day,
      parts.hour,
      parts.minute,
      parts.second
    );
    stamp += target - asUtc;
  }
  const parts = clinicParts(new Date(stamp));
  if (
    parts.year !== year ||
    parts.month !== month ||
    parts.day !== day ||
    parts.hour !== hour ||
    parts.minute !== minute
  ) {
    return null;
  }
  return new Date(stamp).toISOString();
}

function utcToClinicInput(iso) {
  const parts = clinicParts(new Date(iso));
  return `${dateKeyFromParts(parts.year, parts.month, parts.day)}T${pad2(parts.hour)}:${pad2(parts.minute)}`;
}

/* --------------------------------------------------------------- API helpers */

function doctorQuery() {
  return state.isAdmin && state.doctorId ? { doctor_id: state.doctorId } : undefined;
}

function scopedQuery(extra = {}) {
  const query = { ...extra };
  if (state.isAdmin && state.doctorId) query.doctor_id = state.doctorId;
  return query;
}

async function loadConfig() {
  const config = await api.get('/appointments/config');
  state.timezone = config.timezone || CLINIC_TZ;
}

async function loadDoctors() {
  if (!state.isAdmin) {
    if (state.isNurse) {
      // Nurses get the clinic-wide read-only schedule, not a doctor selector.
      state.doctors = [];
      state.doctorId = null;
      return;
    }
    state.doctors = [{ id: state.me.id, display_name: state.me.display_name || 'My calendar' }];
    state.doctorId = state.me.id;
    return;
  }
  const page = await api.get('/staff', { limit: 100 });
  state.doctors = (page.items || [])
    .filter((user) => user.is_active && Array.isArray(user.roles) && user.roles.includes('doctor'))
    .sort((a, b) => String(a.display_name).localeCompare(String(b.display_name)));
  if (!state.doctors.some((doctor) => doctor.id === state.doctorId)) {
    state.doctorId = state.doctors[0] ? state.doctors[0].id : null;
  }
}

function doctorLabel() {
  const doctor = state.doctors.find((item) => item.id === state.doctorId);
  return doctor ? doctor.display_name : 'No active doctor';
}

/* --------------------------------------------------------------- rendering */

function setStatus(message) {
  const node = document.getElementById('appointments-status');
  if (node) node.textContent = message || '';
}

function setLoading(loading) {
  state.loading = loading;
  const node = document.getElementById('appointments-loading');
  if (node) node.hidden = !loading;
}

function statusBadge(status) {
  return ui.el(
    'span',
    { class: `badge ${STATUS_BADGE[status] || 'badge--neutral'}` },
    STATUS_LABEL[status] || status
  );
}

function appointmentChip(appointment) {
  const cancelled = appointment.status === 'cancelled';
  const label = `${formatClinicTime(appointment.start_at)} ${appointment.patient_display_name}`;
  return ui.el(
    'button',
    {
      type: 'button',
      class: `appointment-chip${cancelled ? ' appointment-chip--cancelled' : ''}${
        appointment.status === 'confirmed' ? ' appointment-chip--confirmed' : ''
      }`,
      'aria-label': `${label}, ${STATUS_LABEL[appointment.status] || appointment.status}`,
      onClick: () => openAppointmentDetail(appointment),
    },
    ui.el('span', { class: 'appointment-chip__time', text: formatClinicTime(appointment.start_at) }),
    ui.el('span', { class: 'appointment-chip__name', text: appointment.patient_display_name }),
    state.isAdmin
      ? ui.el('span', { class: 'appointment-chip__meta', text: appointment.doctor_display_name })
      : null
  );
}

function groupByDay(items) {
  const grouped = new Map();
  for (const item of items) {
    const key = clinicKeyFromIso(item.start_at);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(item);
  }
  for (const list of grouped.values()) {
    list.sort((a, b) => String(a.start_at).localeCompare(String(b.start_at)));
  }
  return grouped;
}

function renderMonth(root) {
  const gridStart = startOfWeekKey(startOfMonthKey(state.anchorKey));
  const grouped = groupByDay(state.appointments);
  const today = clinicTodayKey();
  const scroll = ui.el('div', {
    class: 'calendar-scroll',
    role: 'region',
    tabindex: '0',
    'aria-label': 'Month calendar, scrollable',
  });
  const grid = ui.el('div', { class: 'calendar-grid', role: 'grid', 'aria-label': 'Month calendar' });
  for (const day of DAY_NAMES) {
    grid.append(ui.el('div', { class: 'calendar-weekday', role: 'columnheader', text: day }));
  }
  for (let offset = 0; offset < 42; offset += 1) {
    const key = addDaysKey(gridStart, offset);
    const inMonth = key.slice(0, 7) === state.anchorKey.slice(0, 7);
    const items = grouped.get(key) || [];
    const cell = ui.el('div', {
      class: `calendar-day${inMonth ? '' : ' calendar-day--outside'}${
        key === today ? ' calendar-day--today' : ''
      }`,
      role: 'gridcell',
    });
    cell.append(
      ui.el(
        'button',
        {
          type: 'button',
          class: 'calendar-day__number',
          'aria-label': `Open ${formatClinicLongDate(key)}`,
          onClick: () => {
            state.anchorKey = key;
            setView('day');
          },
        },
        formatDay(key, { day: 'numeric' })
      )
    );
    const list = ui.el('div', { class: 'calendar-day__items' });
    for (const appointment of items.slice(0, 3)) list.append(appointmentChip(appointment));
    if (items.length > 3) {
      list.append(
        ui.el(
          'button',
          {
            type: 'button',
            class: 'calendar-more',
            onClick: () => {
              state.anchorKey = key;
              setView('day');
            },
          },
          `+${items.length - 3} more`
        )
      );
    }
    cell.append(list);
    grid.append(cell);
  }
  scroll.append(grid);
  root.append(scroll);
}

function renderWeek(root) {
  const startKey = startOfWeekKey(state.anchorKey);
  const grouped = groupByDay(state.appointments);
  const today = clinicTodayKey();
  const scroll = ui.el('div', {
    class: 'calendar-scroll',
    role: 'region',
    tabindex: '0',
    'aria-label': 'Week calendar, scrollable',
  });
  const grid = ui.el('div', { class: 'week-grid' });
  for (let offset = 0; offset < 7; offset += 1) {
    const key = addDaysKey(startKey, offset);
    const column = ui.el('section', {
      class: `week-column${key === today ? ' week-column--today' : ''}`,
      'aria-label': formatClinicLongDate(key),
    });
    column.append(
      ui.el(
        'button',
        {
          type: 'button',
          class: 'week-column__head',
          onClick: () => {
            state.anchorKey = key;
            setView('day');
          },
        },
        ui.el('span', { class: 'week-column__day', text: DAY_NAMES[offset] }),
        ui.el('span', { class: 'week-column__date', text: formatDay(key, { day: 'numeric', month: 'short' }) })
      )
    );
    const list = ui.el('div', { class: 'week-column__items' });
    const items = grouped.get(key) || [];
    for (const appointment of items) list.append(appointmentChip(appointment));
    if (items.length === 0) list.append(ui.el('p', { class: 'calendar-empty', text: 'No appointments' }));
    column.append(list);
    grid.append(column);
  }
  scroll.append(grid);
  root.append(scroll);
}

function renderDay(root, availability) {
  const key = state.anchorKey;
  const items = state.appointments
    .filter((appointment) => clinicKeyFromIso(appointment.start_at) === key)
    .sort((a, b) => String(a.start_at).localeCompare(String(b.start_at)));
  const day = ui.el('div', { class: 'day-view' });
  day.append(
    ui.el('div', { class: 'day-view__head' },
      ui.el('h3', { class: 'day-view__title', text: formatClinicLongDate(key) }),
      ui.el('p', { class: 'text-muted', text: `${items.length} appointment${items.length === 1 ? '' : 's'}` })
    )
  );

  const timeline = ui.el('ol', { class: 'timeline' });
  for (const appointment of items) {
    const row = ui.el('li', { class: 'timeline__row' });
    row.append(
      ui.el('span', { class: 'timeline__time', text: formatClinicTime(appointment.start_at) }),
      ui.el(
        'button',
        {
          type: 'button',
          class: `timeline__card${appointment.status === 'cancelled' ? ' timeline__card--cancelled' : ''}`,
          onClick: () => openAppointmentDetail(appointment),
        },
        ui.el('span', { class: 'timeline__patient', text: appointment.patient_display_name }),
        ui.el('span', { class: 'timeline__meta', text: `${appointment.appointment_type || 'Appointment'} · ${appointment.duration_minutes} min` }),
        statusBadge(appointment.status)
      )
    );
    timeline.append(row);
  }
  if (items.length === 0) {
    timeline.append(ui.el('li', { class: 'timeline__empty', text: 'No appointments on this day.' }));
  }
  day.append(timeline);

  if (availability) {
    const hours = availability.working_hours || [];
    const dayHours = hours.filter((row) => Number(row.weekday) === weekdayIndex(key));
    const hoursPanel = ui.el('section', { class: 'day-view__panel' },
      ui.el('h4', { text: 'Working hours' }),
      ui.el('p', {
        class: 'text-muted',
        text: dayHours.length
          ? dayHours.map((row) => `${String(row.start_time).slice(0, 5)}–${String(row.end_time).slice(0, 5)}`).join(', ')
          : 'No working hours configured for this weekday.',
      })
    );
    day.append(hoursPanel);
  }

  if (state.canWrite && key >= clinicTodayKey()) {
    const slotsPanel = ui.el('section', { class: 'day-view__panel' });
    slotsPanel.append(ui.el('h4', { text: 'Available times' }));
    const slotGrid = ui.el('div', { class: 'slot-grid' });
    if (state.slots === null) {
      slotGrid.append(ui.el('p', { class: 'text-muted', text: 'Loading slots…' }));
    } else if (state.slots.length === 0) {
      slotGrid.append(ui.el('p', { class: 'text-muted', text: 'No slots for this day. Check working hours or choose another date.' }));
    } else {
      for (const slot of state.slots) {
        slotGrid.append(
          ui.el(
            'button',
            {
              type: 'button',
              class: `slot-button${slot.available ? '' : ' slot-button--disabled'}`,
              disabled: !slot.available,
              title: slot.reason,
              onClick: () =>
                openAppointmentForm({
                  slot: { date: key, start_at: slot.start_at, local_start: slot.local_start },
                }),
            },
            slot.local_start
          )
        );
      }
    }
    slotsPanel.append(slotGrid);
    day.append(slotsPanel);
  }

  root.append(day);
}

function renderUpcoming(root) {
  const start = clinicTodayKey();
  const end = addDaysKey(start, 29);
  const grouped = groupByDay(state.appointments);
  const keys = [...grouped.keys()].filter((key) => key >= start && key <= end).sort();
  if (keys.length === 0) {
    ui.renderEmptyState(root, {
      title: 'No upcoming appointments',
      message: 'The next 30 days are clear. Create an appointment or check another view.',
    });
    return;
  }
  const list = ui.el('div', { class: 'upcoming-list' });
  for (const key of keys) {
    const section = ui.el('section', { class: 'upcoming-day' },
      ui.el('h3', { class: 'upcoming-day__title', text: formatClinicLongDate(key) })
    );
    const rows = ui.el('div', { class: 'upcoming-day__rows' });
    for (const appointment of grouped.get(key)) {
      rows.append(
        ui.el(
          'button',
          {
            type: 'button',
            class: 'upcoming-row',
            onClick: () => openAppointmentDetail(appointment),
          },
          ui.el('span', { class: 'upcoming-row__time', text: formatClinicTime(appointment.start_at) }),
          ui.el('span', { class: 'upcoming-row__main' },
            ui.el('span', { class: 'upcoming-row__patient', text: appointment.patient_display_name }),
            ui.el('span', { class: 'upcoming-row__meta', text: `${appointment.appointment_type || 'Appointment'} · ${appointment.doctor_display_name}` })
          ),
          statusBadge(appointment.status)
        )
      );
    }
    section.append(rows);
    list.append(section);
  }
  root.append(list);
}

function renderCalendar() {
  const calendar = document.getElementById('appointments-calendar');
  const upcoming = document.getElementById('appointments-upcoming');
  if (!calendar || !upcoming) return;
  ui.clear(calendar);
  ui.clear(upcoming);
  const isUpcoming = state.view === 'upcoming';
  calendar.hidden = isUpcoming;
  upcoming.hidden = !isUpcoming;
  if (isUpcoming) renderUpcoming(upcoming);
  else if (state.view === 'month') renderMonth(calendar);
  else if (state.view === 'week') renderWeek(calendar);
  else renderDay(calendar, state.availability);
}

function updateRangeLabel() {
  const node = document.getElementById('appointments-range');
  if (!node) return;
  if (state.view === 'month') node.textContent = formatMonthYear(state.anchorKey);
  else if (state.view === 'week') node.textContent = formatWeekRange(startOfWeekKey(state.anchorKey));
  else if (state.view === 'day') node.textContent = formatClinicLongDate(state.anchorKey);
  else node.textContent = 'Next 30 days';
}

function updateContext() {
  const node = document.getElementById('appointments-context');
  if (!node) return;
  let scope = 'Your calendar';
  if (state.isAdmin) scope = doctorLabel();
  else if (state.isNurse) scope = 'Read-only clinic calendar';
  node.textContent = `Clinic timezone: ${state.timezone} · ${scope}`;
}

function renderDoctorPicker() {
  const picker = document.getElementById('doctor-picker');
  const select = document.getElementById('doctor-select');
  if (!picker || !select) return;
  picker.hidden = !state.isAdmin;
  ui.clear(select);
  if (!state.isAdmin) return;
  if (state.doctors.length === 0) {
    select.append(ui.el('option', { value: '', text: 'No active doctors' }));
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const doctor of state.doctors) {
    select.append(ui.el('option', { value: doctor.id, text: doctor.display_name }));
  }
  select.value = state.doctorId || '';
}

function setView(view) {
  state.view = view;
  if (view === 'upcoming') state.anchorKey = clinicTodayKey();
  const switcher = document.getElementById('view-switcher');
  if (switcher) {
    for (const button of switcher.querySelectorAll('[data-view]')) {
      const active = button.dataset.view === view;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    }
  }
  const prev = document.getElementById('calendar-prev');
  const next = document.getElementById('calendar-next');
  const today = document.getElementById('calendar-today');
  if (prev) prev.hidden = view === 'upcoming';
  if (next) next.hidden = view === 'upcoming';
  if (today) today.hidden = view === 'upcoming';
  loadData();
}

/* ------------------------------------------------------ appointment history */

function isPastUnmarked(appointment) {
  return (
    ['scheduled', 'confirmed'].includes(appointment.status) &&
    Date.parse(appointment.end_at) <= Date.now()
  );
}

function historyStatus(appointment) {
  return isPastUnmarked(appointment) ? 'past_unmarked' : appointment.status;
}

function historyStatusBadge(appointment) {
  const status = historyStatus(appointment);
  return ui.el(
    'span',
    { class: `badge ${STATUS_BADGE[status] || 'badge--neutral'}` },
    HISTORY_STATUS_LABEL[status] || status
  );
}

function setHistoryLoading(loading) {
  state.history.loading = loading;
  const node = document.getElementById('appointments-history-loading');
  if (node) node.hidden = !loading;
}

function setHistoryError(message) {
  const node = document.getElementById('appointments-history-error');
  if (!node) return;
  node.textContent = message || '';
  node.hidden = !message;
}

function setHistoryStatus(message) {
  const node = document.getElementById('appointments-history-status');
  if (node) node.textContent = message || '';
}

function historyDateRange() {
  const startInput = document.getElementById('appointments-history-start');
  const endInput = document.getElementById('appointments-history-end');
  const startKey = startInput ? startInput.value : '';
  const endKey = endInput ? endInput.value : '';
  if (!startKey && !endKey) return { start: null, end: null };
  if (!startKey || !endKey) {
    throw new Error('Choose both a From and To date for the history range.');
  }
  if (endKey < startKey) {
    throw new Error('The history range end must be on or after its start.');
  }
  const start = clinicWallToUtc(startKey, '00:00');
  const end = clinicWallToUtc(addDaysKey(endKey, 1), '00:00');
  if (!start || !end) {
    throw new Error('That history range falls on a daylight-saving gap. Choose another range.');
  }
  return { start, end };
}

function historyFiltersFromForm() {
  const q = document.getElementById('appointments-history-search')?.value.trim() || '';
  const status = document.getElementById('appointments-history-status-filter')?.value || '';
  const visitType = document.getElementById('appointments-history-type')?.value || '';
  const range = historyDateRange();
  state.history.filters = { q, status, visit_type: visitType, start: range.start, end: range.end };
  return state.history.filters;
}

function historySuggestionElements() {
  return {
    input: document.getElementById('appointments-history-search'),
    list: document.getElementById('appointments-history-suggestions'),
    status: document.getElementById('appointments-history-search-status'),
  };
}

function setHistorySuggestionStatus(message) {
  const { status } = historySuggestionElements();
  if (status) status.textContent = message || '';
}

function hideHistorySuggestions({ clearStatus = false } = {}) {
  state.history.suggestionSeq += 1;
  state.history.suggestions = [];
  state.history.suggestionActive = -1;
  if (state.history.suggestionTimer) {
    clearTimeout(state.history.suggestionTimer);
    state.history.suggestionTimer = null;
  }
  const { input, list } = historySuggestionElements();
  if (list) {
    list.hidden = true;
    ui.clear(list);
  }
  if (input) {
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }
  if (clearStatus) setHistorySuggestionStatus('');
}

function setHistorySuggestionActive(index) {
  const { input, list } = historySuggestionElements();
  const items = state.history.suggestions;
  if (!input || !list || items.length === 0) return;
  const next = Math.max(0, Math.min(index, items.length - 1));
  state.history.suggestionActive = next;
  const options = Array.from(list.querySelectorAll('[role="option"]'));
  options.forEach((option, optionIndex) => {
    const active = optionIndex === next;
    option.setAttribute('aria-selected', active ? 'true' : 'false');
    option.classList.toggle('is-active', active);
  });
  const activeOption = options[next];
  if (activeOption) {
    input.setAttribute('aria-activedescendant', activeOption.id);
    if (typeof activeOption.scrollIntoView === 'function') {
      activeOption.scrollIntoView({ block: 'nearest' });
    }
  }
}

function renderHistorySuggestions(items, query) {
  const { input, list } = historySuggestionElements();
  if (!input || !list) return;
  ui.clear(list);
  state.history.suggestions = Array.isArray(items) ? items : [];
  state.history.suggestionActive = -1;
  input.removeAttribute('aria-activedescendant');

  if (state.history.suggestions.length === 0) {
    list.append(
      ui.el('li', {
        class: 'patient-result patient-result--empty',
        role: 'presentation',
        text: `No patients match “${query}”.`,
      }),
    );
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    setHistorySuggestionStatus(`No patients match ${query}.`);
    return;
  }

  state.history.suggestions.forEach((patient, index) => {
    const option = ui.el('li', {
      class: 'patient-result',
      role: 'option',
      id: `appointments-history-suggestion-${index}`,
      'aria-selected': 'false',
      onClick: () => chooseHistorySuggestion(index),
    },
      ui.el('span', { class: 'patient-result__name', text: patient.display_name }),
      ui.el('span', { class: 'patient-result__mrn', text: patient.mrn || '' }),
    );
    // Select on pointerdown so an input blur cannot close the list first; the
    // click handler below remains for keyboard/screen-reader activation.
    option.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      chooseHistorySuggestion(index);
    });
    option.addEventListener('pointerenter', () => setHistorySuggestionActive(index));
    list.append(option);
  });
  list.hidden = false;
  input.setAttribute('aria-expanded', 'true');
  setHistorySuggestionStatus(
    `${state.history.suggestions.length} matching patient${state.history.suggestions.length === 1 ? '' : 's'}. Use the up and down arrow keys to choose.`,
  );
}

function chooseHistorySuggestion(index) {
  const patient = state.history.suggestions[index];
  const { input } = historySuggestionElements();
  if (!patient || !input) return;
  input.value = patient.display_name;
  hideHistorySuggestions({ clearStatus: true });
  setHistoryError('');
  try {
    historyFiltersFromForm();
  } catch (error) {
    setHistoryError(error.message);
    return;
  }
  state.history.offset = 0;
  if (typeof input.focus === 'function') input.focus();
  loadHistory();
}

async function fetchHistorySuggestions(query, sequence) {
  try {
    const page = await api.get('/patients', { q: query, limit: HISTORY_SUGGESTION_LIMIT });
    if (sequence !== state.history.suggestionSeq) return;
    renderHistorySuggestions(page.items || [], query);
  } catch {
    if (sequence !== state.history.suggestionSeq) return;
    hideHistorySuggestions({ clearStatus: true });
    setHistorySuggestionStatus('Patient suggestions are unavailable. Press Apply filters to search.');
  }
}

function scheduleHistorySuggestions(delay = HISTORY_SUGGESTION_DEBOUNCE_MS) {
  const { input } = historySuggestionElements();
  if (!input) return;
  const query = input.value.trim();
  if (!query) return;
  if (state.history.suggestionTimer) {
    clearTimeout(state.history.suggestionTimer);
  }
  const sequence = (state.history.suggestionSeq += 1);
  setHistorySuggestionStatus('Searching for patients…');
  state.history.suggestionTimer = setTimeout(() => {
    state.history.suggestionTimer = null;
    fetchHistorySuggestions(query, sequence);
  }, delay);
}

function wireHistoryPatientSuggestions() {
  const { input, list } = historySuggestionElements();
  if (!input || !list) return;
  if (!state.me || !auth.hasPermission(state.me, 'patient:read')) return;

  input.addEventListener('input', () => {
    hideHistorySuggestions({ clearStatus: true });
    const query = input.value.trim();
    if (!query) return;
    scheduleHistorySuggestions();
  });

  input.addEventListener('search', () => {
    hideHistorySuggestions({ clearStatus: true });
  });

  input.addEventListener('keydown', (event) => {
    const expanded = Boolean(list && !list.hidden);
    const hasOptions = state.history.suggestions.length > 0;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!expanded) {
        scheduleHistorySuggestions(0);
        return;
      }
      if (!hasOptions) return;
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      const current = state.history.suggestionActive;
      const next = current < 0
        ? (direction > 0 ? 0 : state.history.suggestions.length - 1)
        : current + direction;
      const length = state.history.suggestions.length;
      setHistorySuggestionActive((next + length) % length);
      return;
    }
    if (event.key === 'Enter' && expanded && hasOptions && state.history.suggestionActive >= 0) {
      event.preventDefault();
      chooseHistorySuggestion(state.history.suggestionActive);
      return;
    }
    if (event.key === 'Escape' && expanded) {
      event.preventDefault();
      hideHistorySuggestions({ clearStatus: true });
    }
  });

  input.addEventListener('blur', () => {
    hideHistorySuggestions({ clearStatus: true });
  });
}

function renderHistoryPagination() {
  const host = document.getElementById('appointments-history-pagination');
  if (!host) return;
  ui.renderPagination(host, {
    total: state.history.total,
    limit: state.history.limit,
    offset: state.history.offset,
    count: state.history.items.length,
    itemLabel: 'appointments',
    onChange: (nextOffset) => {
      state.history.offset = nextOffset;
      loadHistory();
    },
  });
}

function renderHistory() {
  const region = document.getElementById('appointments-history-region');
  if (!region) return;
  ui.clear(region);
  if (state.history.items.length === 0) {
    ui.renderEmptyState(region, {
      title: state.history.filters.q || state.history.filters.status || state.history.filters.visit_type
        ? 'No historical appointments match these filters.'
        : 'No appointment history yet.',
      message: 'Completed, no-show, cancelled and past appointments will appear here.',
      headingTag: 'h3',
    });
    renderHistoryPagination();
    return;
  }

  const scroll = ui.el('div', {
    class: 'table-scroll',
    role: 'region',
    tabindex: '0',
    'aria-label': 'Appointment history, scrollable',
  });
  const table = ui.el('table', { class: 'table table-stack tabular-nums appointments-history__table' });
  const columns = [
    'Patient',
    'MRN',
    'Doctor',
    'Start',
    'End',
    'Visit type',
    'Duration',
    'Status',
    'Created',
    'Updated',
    'Details',
  ];
  table.append(
    ui.el('thead', {},
      ui.el('tr', {}, columns.map((label) => ui.el('th', { scope: 'col', text: label }))),
    ),
  );
  const body = ui.el('tbody');
  for (const appointment of state.history.items) {
    const status = historyStatus(appointment);
    body.append(
      ui.el('tr', {},
        ui.el('td', { 'data-label': 'Patient', text: appointment.patient_display_name }),
        ui.el('td', { 'data-label': 'MRN', class: 'mono', text: appointment.patient_mrn }),
        ui.el('td', { 'data-label': 'Doctor', text: appointment.doctor_display_name }),
        ui.el('td', { 'data-label': 'Start', text: formatClinicDateTime(appointment.start_at) }),
        ui.el('td', { 'data-label': 'End', text: formatClinicDateTime(appointment.end_at) }),
        ui.el('td', { 'data-label': 'Visit type', text: appointment.appointment_type || 'Not recorded' }),
        ui.el('td', { 'data-label': 'Duration', text: `${appointment.duration_minutes} min` }),
        ui.el('td', { 'data-label': 'Status' },
          ui.el('span', {
            class: `badge ${STATUS_BADGE[status] || 'badge--neutral'}`,
            text: HISTORY_STATUS_LABEL[status] || status,
          }),
        ),
        ui.el('td', { 'data-label': 'Created', text: formatClinicDateTime(appointment.created_at) }),
        ui.el('td', { 'data-label': 'Updated', text: formatClinicDateTime(appointment.updated_at) }),
        ui.el('td', { 'data-label': 'Details' },
          ui.el('button', {
            class: 'link-button',
            type: 'button',
            onClick: () => openAppointmentDetail(appointment),
          }, 'View'),
        ),
      ),
    );
  }
  table.append(body);
  scroll.append(table);
  region.append(scroll);
  renderHistoryPagination();
}

async function loadHistory() {
  if (state.history.loading) return;
  const region = document.getElementById('appointments-history-region');
  if (!region) return;
  setHistoryError('');
  setHistoryLoading(true);
  setHistoryStatus('Loading appointment history.');
  try {
    const filters = state.history.filters;
    const query = {
      ...scopedQuery(),
      limit: state.history.limit,
      offset: state.history.offset,
    };
    for (const key of ['q', 'status', 'visit_type', 'start', 'end']) {
      if (filters[key]) query[key] = filters[key];
    }
    const page = await api.get('/appointments/history', query);
    state.history.items = Array.isArray(page.items) ? page.items : [];
    state.history.total = Number(page.total) || 0;
    renderHistory();
    const first = state.history.total === 0 ? 0 : state.history.offset + 1;
    const last = state.history.offset + state.history.items.length;
    setHistoryStatus(
      state.history.total === 0
        ? 'No historical appointments match these filters.'
        : `Showing ${first}–${last} of ${state.history.total} appointments.`,
    );
  } catch (error) {
    ui.renderErrorState(region, error, {
      describe: api.describeApiError,
      onRetry: () => loadHistory(),
    });
    setHistoryStatus('Appointment history could not be loaded.');
  } finally {
    setHistoryLoading(false);
  }
}

function exportHistory() {
  setHistoryError('');
  let filters;
  try {
    filters = historyFiltersFromForm();
  } catch (error) {
    setHistoryError(error.message);
    return;
  }
  if (!filters.start || !filters.end) {
    setHistoryError('Choose both a From and To date before exporting.');
    return;
  }
  const query = { ...scopedQuery() };
  for (const key of ['q', 'status', 'visit_type', 'start', 'end']) {
    if (filters[key]) query[key] = filters[key];
  }
  window.location.assign(api.buildUrl('/appointments/history/export.csv', query));
}

function setMode(mode) {
  state.mode = mode;
  const switcher = document.getElementById('appointments-mode-switcher');
  if (switcher) {
    for (const button of switcher.querySelectorAll('[data-mode]')) {
      const active = button.dataset.mode === mode;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    }
  }
  const calendarCard = document.getElementById('appointments-calendar-card');
  const historySection = document.getElementById('appointments-history');
  const voice = document.getElementById('voice-region');
  if (calendarCard) calendarCard.hidden = mode === 'history';
  if (historySection) historySection.hidden = mode !== 'history';
  if (voice) voice.hidden = mode === 'history' || !state.canWrite;
  if (mode === 'history') {
    clearActiveExpiryCleanup();
    loadHistory();
  } else {
    hideHistorySuggestions({ clearStatus: true });
    scheduleActiveExpiryCleanup();
    loadData();
  }
}

async function refreshCurrentView() {
  if (state.mode === 'history') await loadHistory();
  else await loadData();
}

/* --------------------------------------------------------------- data load */

function visibleRange() {
  if (state.view === 'month') {
    const start = startOfWeekKey(startOfMonthKey(state.anchorKey));
    return { start, end: addDaysKey(start, 41) };
  }
  if (state.view === 'week') {
    const start = startOfWeekKey(state.anchorKey);
    return { start, end: addDaysKey(start, 6) };
  }
  if (state.view === 'day') return { start: state.anchorKey, end: state.anchorKey };
  const start = clinicTodayKey();
  return { start, end: addDaysKey(start, 29) };
}

async function fetchAppointments(range) {
  const start = clinicWallToUtc(range.start, '00:00');
  const end = clinicWallToUtc(addDaysKey(range.end, 1), '00:00');
  if (!start || !end) throw new Error('Unable to resolve clinic dates.');
  const items = [];
  let total = 0;
  let offset = 0;
  for (let page = 0; page < 3; page += 1) {
    const payload = await api.get('/appointments', {
      start,
      end,
      ...scopedQuery(),
      limit: 100,
      offset,
    });
    items.push(...(payload.items || []));
    total = payload.total || items.length;
    offset += 100;
    if (items.length >= total) break;
  }
  return { items, total };
}

async function fetchSlots(key) {
  if (key < clinicTodayKey()) return null;
  const payload = await api.get('/appointments/slots', scopedQuery({ date: key }));
  return payload.slots || [];
}

async function loadAvailability() {
  if (state.isAdmin && !state.doctorId) {
    state.availability = null;
    return;
  }
  if (!state.canWrite && !state.canManage) {
    // Read-only nurses do not need the doctor availability/slot machinery.
    state.availability = null;
    return;
  }
  state.availability = await api.get('/appointments/availability', scopedQuery());
}

async function loadData() {
  if (state.isAdmin && !state.doctorId) {
    clearCancelledCleanup();
    clearActiveExpiryCleanup();
    state.appointments = [];
    state.slots = null;
    renderCalendar();
    updateRangeLabel();
    updateContext();
    ui.renderEmptyState(document.getElementById('appointments-calendar'), {
      title: 'No active doctors',
      message: 'Create an active doctor account before scheduling appointments.',
    });
    setLoading(false);
    return;
  }
  setLoading(true);
  setStatus('');
  updateRangeLabel();
  try {
    const range = visibleRange();
    const [appointments] = await Promise.all([
      fetchAppointments(range),
      state.view === 'day' && state.canWrite
        ? fetchSlots(state.anchorKey).then((slots) => {
            state.slots = slots;
          })
        : Promise.resolve((state.slots = null)),
    ]);
    clearCancelledCleanup();
    clearActiveExpiryCleanup();
    state.appointments = pruneExpiredCancelled(appointments.items);
    if (appointments.total > appointments.items.length) {
      setStatus(`Showing the first ${appointments.items.length} of ${appointments.total} appointments in this range.`);
    }
    renderCalendar();
    updateContext();
    scheduleCancelledCleanup();
    scheduleActiveExpiryCleanup();
  } catch (error) {
    const target =
      state.view === 'upcoming'
        ? document.getElementById('appointments-upcoming')
        : document.getElementById('appointments-calendar');
    ui.renderErrorState(target, error, {
      describe: api.describeApiError,
      onRetry: () => loadData(),
    });
  } finally {
    setLoading(false);
  }
}

async function refreshAll() {
  if (state.mode === 'history') {
    await loadHistory();
    return;
  }
  try {
    await loadAvailability();
  } catch (error) {
    state.availability = null;
    ui.showGlobalBanner({
      tone: 'warn',
      message: `Availability could not be loaded: ${api.describeApiError(error).message}`,
      dismissible: true,
    });
  }
  await loadData();
}

/* --------------------------------------------------------------- dialogs */

function formErrorNode() {
  return ui.el('p', { class: 'field-error form-error', role: 'alert' });
}

function setFormError(node, message) {
  if (!node) return;
  node.textContent = message || '';
  node.hidden = !message;
}

function lifecycleChangeText(event) {
  const statusText = (value) => STATUS_LABEL[value] || value;
  const oldStatus = event.old_status;
  const newStatus = event.new_status;
  const oldStart = event.old_start_at;
  const newStart = event.new_start_at;
  const oldEnd = event.old_end_at;
  const newEnd = event.new_end_at;
  const statusChanged = Boolean(oldStatus && newStatus && oldStatus !== newStatus);
  const startChanged = Boolean(oldStart && newStart && oldStart !== newStart);
  const endChanged = Boolean(oldEnd && newEnd && oldEnd !== newEnd);

  if (event.event_type === 'created') {
    const parts = [];
    if (newStart) parts.push(`Scheduled for ${formatClinicDateTime(newStart)}`);
    if (newStatus && newStatus !== 'scheduled') {
      parts.push(`Status: ${statusText(newStatus)}`);
    }
    return parts.join(' · ');
  }

  if (event.event_type === 'calendar_removed') {
    return 'Removed from the calendar';
  }

  const parts = [];
  if (statusChanged) {
    parts.push(`Status: ${statusText(oldStatus)} → ${statusText(newStatus)}`);
  }
  if (startChanged) {
    parts.push(
      `Time: ${formatClinicDateTime(oldStart)} → ${formatClinicDateTime(newStart)}`,
    );
  } else if (endChanged) {
    parts.push(`End: ${formatClinicDateTime(oldEnd)} → ${formatClinicDateTime(newEnd)}`);
  }
  if (parts.length > 0) return parts.join(' · ');

  if (event.event_type === 'rescheduled') return 'Time updated';
  if (event.event_type === 'status_changed') return 'Status updated';
  if (event.event_type === 'updated') return 'Appointment details updated';
  return '';
}

function openAppointmentDetail(appointment) {
  const detail = ui.el('div', { class: 'appointment-detail' });
  const rows = [
    ['Patient', ui.el('a', { href: `patient.html?id=${encodeURIComponent(appointment.patient_id)}`, text: appointment.patient_display_name })],
    ['MRN', appointment.patient_mrn],
    [
      'Doctor',
      ui.el('span', { class: 'doctor-identity' },
        ui.doctorAvatar(`${appointment.doctor_id || ''}|${appointment.doctor_display_name}`, { size: 'sm' }),
        ui.el('span', { text: appointment.doctor_display_name })
      ),
    ],
    ['When', formatClinicDateTime(appointment.start_at)],
    ['Ends', formatClinicDateTime(appointment.end_at)],
    ['Duration', `${appointment.duration_minutes} minutes`],
    ['Type', appointment.appointment_type || 'Not recorded'],
    ['Status', statusBadge(appointment.status)],
    ['Created', formatClinicDateTime(appointment.created_at)],
    ['Last updated', formatClinicDateTime(appointment.updated_at)],
    ['Calendar removed', appointment.calendar_removed_at
      ? formatClinicDateTime(appointment.calendar_removed_at)
      : 'No'],
    ['Notes', appointment.notes || 'None'],
  ];
  const list = ui.el('dl', { class: 'detail-grid' });
  for (const [label, value] of rows) {
    list.append(ui.el('dt', { text: label }));
    list.append(ui.el('dd', {}, value && value.nodeType ? value : String(value)));
  }
  const lifecycleHost = ui.el('section', { class: 'appointment-lifecycle' },
    ui.el('h3', { class: 'appointment-lifecycle__title', text: 'Lifecycle' }),
    ui.el('p', { class: 'text-muted', text: 'Loading lifecycle events…' }),
  );
  detail.append(list, lifecycleHost);
  api.get(`/appointments/${encodeURIComponent(appointment.id)}/history`)
    .then((events) => {
      ui.clear(lifecycleHost);
      lifecycleHost.append(
        ui.el('h3', { class: 'appointment-lifecycle__title', text: 'Lifecycle' }),
      );
      if (!Array.isArray(events) || events.length === 0) {
        lifecycleHost.append(
          ui.el('p', { class: 'text-muted', text: 'No lifecycle events recorded.' }),
        );
        return;
      }
      const labels = {
        created: 'Created',
        updated: 'Updated',
        rescheduled: 'Rescheduled',
        status_changed: 'Status changed',
        calendar_removed: 'Removed from calendar',
      };
      const eventList = ui.el('ol', { class: 'appointment-lifecycle__list' });
      for (const event of events) {
        const changeText = lifecycleChangeText(event);
        eventList.append(
          ui.el('li', {},
            ui.el('span', { class: 'appointment-lifecycle__event', text: labels[event.event_type] || event.event_type }),
            ui.el('span', { class: 'appointment-lifecycle__time', text: formatClinicDateTime(event.created_at) }),
            changeText
              ? ui.el('span', { class: 'appointment-lifecycle__change', text: changeText })
              : null,
            ui.el('span', {
              class: 'appointment-lifecycle__actor',
              text: event.actor_display_name ? `by ${event.actor_display_name}` : 'by system',
            }),
          ),
        );
      }
      lifecycleHost.append(eventList);
    })
    .catch((error) => {
      ui.clear(lifecycleHost);
      lifecycleHost.append(
        ui.el('h3', { class: 'appointment-lifecycle__title', text: 'Lifecycle' }),
        ui.el('p', { class: 'text-muted', text: api.describeApiError(error).message }),
      );
    });

  // The shared <dialog> can have its native close deferred into a view
  // transition. Wait for its own close event before reusing it for the next
  // modal, otherwise the pending close event clears the new form/confirmation.
  // Listening directly (rather than only awaiting ui.close()) also keeps this
  // flow correct if a browser still has an older cached ui.js module.
  function closeThen(action) {
    const dialog = document.getElementById('app-dialog');
    if (!dialog || !dialog.open) {
      action();
      return;
    }
    let finished = false;
    let timer = null;
    const run = () => {
      if (finished) return;
      finished = true;
      if (timer) clearTimeout(timer);
      if (typeof window !== 'undefined') window.removeEventListener('popstate', run);
      action();
    };
    const onClose = () => {
      dialog.removeEventListener('close', onClose);
      if (overlayHistory.supportsDeferredPush) {
        run();
        return;
      }
      // Older cached overlay-history.js: its releaseOverlay() requests an
      // asynchronous history.back(). Wait for that popstate so the next modal
      // does not race it and navigate to the previous page. The timeout covers
      // the no-history-entry case.
      if (typeof window !== 'undefined') window.addEventListener('popstate', run);
      timer = setTimeout(run, 300);
    };
    dialog.addEventListener('close', onClose);
    close();
  }

  const actions = [];
  const active = appointment.status === 'scheduled' || appointment.status === 'confirmed';
  if (state.canWrite && active) {
    actions.push(
      ui.el('button', {
        class: 'btn btn-primary',
        type: 'button',
        onClick: () => closeThen(() => markAppointmentStatus(appointment, 'completed')),
      }, 'Mark completed')
    );
    actions.push(
      ui.el('button', {
        class: 'btn btn-secondary',
        type: 'button',
        onClick: () => closeThen(() => markAppointmentStatus(appointment, 'no_show')),
      }, 'Mark no-show')
    );
    if (Date.parse(appointment.end_at) > Date.now()) {
      actions.push(
        ui.el('button', {
          class: 'btn btn-secondary',
          type: 'button',
          onClick: () => closeThen(() => openAppointmentForm({ appointment })),
        }, 'Reschedule')
      );
    }
    actions.push(
      ui.el('button', {
        class: 'btn btn-danger',
        type: 'button',
        onClick: () => closeThen(() => cancelAppointment(appointment)),
      }, 'Cancel appointment')
    );
  }
  if (state.isAdmin && appointment.status === 'cancelled') {
    actions.push(
      ui.el('button', {
        class: 'btn btn-secondary',
        type: 'button',
        onClick: () => closeThen(() => removeFromCalendar(appointment)),
      }, 'Remove from calendar')
    );
  }
  actions.push(ui.el('button', { class: 'btn btn-primary', type: 'button', onClick: () => close() }, 'Close'));

  const { close } = ui.openDialog({
    title: 'Appointment',
    bodyNodes: [detail],
    actionNodes: actions,
  });
}

async function markAppointmentStatus(appointment, status) {
  const label = status === 'completed' ? 'Completed' : 'No-show';
  const confirmed = await ui.confirmDialog({
    title: `Mark as ${label.toLowerCase()}?`,
    message: status === 'completed'
      ? 'This records the appointment outcome and moves it to history. The scheduled times and audit history are retained.'
      : 'This records that the patient did not attend. The appointment moves to history and the scheduled times and audit history are retained.',
    confirmLabel: `Mark as ${label.toLowerCase()}`,
    cancelLabel: 'Keep as is',
    danger: status === 'no_show',
    details: [
      { label: 'Patient', value: appointment.patient_display_name },
      { label: 'When', value: formatClinicDateTime(appointment.start_at) },
      { label: 'Doctor', value: appointment.doctor_display_name },
      { label: 'Current status', value: STATUS_LABEL[appointment.status] || appointment.status },
    ],
  });
  if (!confirmed) return;
  try {
    await api.patch(`/appointments/${appointment.id}`, { status });
    toast.showToast({
      message: `Appointment marked ${label.toLowerCase()}.`,
      tone: status === 'completed' ? 'success' : 'info',
    });
    await refreshCurrentView();
  } catch (error) {
    toast.showToast({ message: api.describeApiError(error).message, tone: 'error' });
  }
}

async function cancelAppointment(appointment) {
  const confirmed = await ui.confirmDialog({
    title: 'Cancel this appointment?',
    message: 'The time is released for other patients. The appointment is kept in the record as cancelled.',
    confirmLabel: 'Cancel appointment',
    cancelLabel: 'Keep appointment',
    danger: true,
    details: [
      { label: 'Patient', value: appointment.patient_display_name },
      { label: 'When', value: formatClinicDateTime(appointment.start_at) },
      { label: 'Doctor', value: appointment.doctor_display_name },
    ],
  });
  if (!confirmed) return;
  try {
    await api.post(`/appointments/${appointment.id}/cancel`, {});
    toast.showToast({
      message: 'Appointment cancelled. It will leave the calendar in 5 minutes.',
      tone: 'success',
    });
    await loadData();
  } catch (error) {
    toast.showToast({ message: api.describeApiError(error).message, tone: 'error' });
  }
}

async function removeFromCalendar(appointment) {
  const confirmed = await ui.confirmDialog({
    title: 'Remove from calendar?',
    message: 'The cancelled appointment will no longer appear in the calendar. The appointment record and audit history are kept.',
    confirmLabel: 'Remove from calendar',
    cancelLabel: 'Keep on calendar',
    danger: true,
    details: [
      { label: 'Patient', value: appointment.patient_display_name },
      { label: 'When', value: formatClinicDateTime(appointment.start_at) },
      { label: 'Doctor', value: appointment.doctor_display_name },
    ],
  });
  if (!confirmed) return;
  try {
    await api.post(`/appointments/${appointment.id}/remove-from-calendar`, {});
    toast.showToast({ message: 'Removed from the calendar.', tone: 'success' });
    await loadData();
  } catch (error) {
    toast.showToast({ message: api.describeApiError(error).message, tone: 'error' });
  }
}

/* --------------------------------------------------------- patient search */

function patientPicker(initialPatient, initialQuery = '') {
  const wrapper = ui.el('div', { class: 'field patient-picker' });
  const label = ui.el('label', { for: 'appointment-patient-search', text: 'Patient' });
  const input = ui.el('input', {
    id: 'appointment-patient-search',
    type: 'search',
    placeholder: 'Search by name or MRN',
    autocomplete: 'off',
    'aria-describedby': 'appointment-patient-help',
  });
  const help = ui.el('p', {
    class: 'field-hint',
    id: 'appointment-patient-help',
    text: 'Search is limited to patients your account may read.',
  });
  const results = ui.el('ul', { class: 'patient-results', hidden: true });
  const selected = ui.el('p', { class: 'patient-selected', hidden: true });
  const canCreatePatient = Boolean(
    state.me && auth.hasPermission(state.me, 'patient:write') && state.canWrite
  );
  const createFields = canCreatePatient ? buildPatientCreateForm() : null;
  const createPanel = createFields
    ? ui.el('div', { class: 'patient-create-inline', hidden: true },
        ui.el('h3', { class: 'card__title', text: 'Add a new patient' }),
        createFields.element,
        createFields.errorNode,
        ui.el('div', { class: 'patient-create-inline__actions' },
          ui.el('button', {
            class: 'btn btn-secondary btn-sm',
            type: 'button',
            onClick: () => {
              createPanel.hidden = true;
              createFields.reset();
            },
          }, 'Cancel'),
          ui.el('button', {
            class: 'btn btn-primary btn-sm',
            type: 'button',
            onClick: () => createPatientForAppointment(),
          }, 'Create patient'),
        ),
      )
    : null;
  let current = initialPatient || null;
  let timer = null;
  let sequence = 0;

  async function createPatientForAppointment() {
    if (!createFields) return;
    const input = createFields.read();
    const message = validatePatientInput(input);
    if (message) {
      createFields.setError(message);
      return;
    }
    createFields.setError('');
    const save = createPanel.querySelector('.btn-primary');
    ui.setBusy(save, true, 'Creating\u2026');
    try {
      const created = await api.post('/patients', input);
      cache.invalidate('/patients');
      ui.setBusy(save, false);
      createFields.reset();
      createPanel.hidden = true;
      choose({
        id: created.id,
        display_name: created.display_name,
        mrn: created.mrn,
      });
      toast.showToast({ message: 'Patient created and selected.', tone: 'success' });
    } catch (error) {
      createFields.setError(api.describeApiError(error).message);
      ui.setBusy(save, false);
    }
  }

  function renderSelected() {
    if (!current) {
      selected.hidden = true;
      return;
    }
    selected.hidden = false;
    ui.clear(selected);
    selected.append(
      ui.el('strong', { text: current.display_name }),
      ui.el('span', { class: 'text-muted', text: ` · ${current.mrn || 'MRN not recorded'}` })
    );
    input.value = `${current.display_name}${current.mrn ? ` · ${current.mrn}` : ''}`;
  }

  function choose(patient) {
    current = patient;
    results.hidden = true;
    if (createPanel) createPanel.hidden = true;
    renderSelected();
  }

  async function search() {
    const query = input.value.trim();
    if (!query) {
      results.hidden = true;
      return;
    }
    const currentSequence = (sequence += 1);
    try {
      const page = await api.get('/patients', { q: query, limit: 8 });
      if (currentSequence !== sequence) return;
      ui.clear(results);
      const items = page.items || [];
      if (items.length === 0) {
        const empty = ui.el('li', { class: 'patient-result patient-result--empty' });
        empty.append(ui.el('p', { text: 'No matching patients.' }));
        if (canCreatePatient && createPanel) {
          empty.append(
            ui.el('button', {
              class: 'btn btn-secondary btn-sm',
              type: 'button',
              onClick: () => {
                createPanel.hidden = false;
                createFields.focus();
              },
            }, 'Add new patient')
          );
        }
        results.append(empty);
      } else {
        for (const patient of items) {
          results.append(
            ui.el('li', {},
              ui.el('button', {
                type: 'button',
                class: 'patient-result',
                onClick: () => choose(patient),
              },
              ui.el('span', { class: 'patient-result__name', text: patient.display_name }),
              ui.el('span', { class: 'patient-result__mrn', text: patient.mrn || '' })
              )
            )
          );
        }
      }
      results.hidden = false;
    } catch (error) {
      results.hidden = true;
      setStatus(api.describeApiError(error).message);
    }
  }

  input.addEventListener('input', () => {
    current = null;
    selected.hidden = true;
    if (createPanel) createPanel.hidden = true;
    if (timer) clearTimeout(timer);
    timer = setTimeout(search, SEARCH_DEBOUNCE_MS);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (timer) clearTimeout(timer);
      search();
    }
  });

  wrapper.append(label, input, help, selected, results);
  if (createPanel) wrapper.append(createPanel);
  if (current) renderSelected();
  else if (initialQuery) {
    input.value = initialQuery;
    search();
  }
  return { node: wrapper, getPatient: () => current };
}

/* ------------------------------------------------------- appointment form */

async function loadSlotsForForm(dateKey) {
  if (!dateKey) return [];
  return fetchSlots(dateKey);
}

function openAppointmentForm({ appointment = null, proposal = null, slot = null } = {}) {
  if (!state.canWrite) return;
  if (state.isAdmin && !state.doctorId) {
    toast.showToast({ message: 'Select a doctor first.', tone: 'error' });
    return;
  }

  const editing = Boolean(appointment);
  const initialPatient = appointment
    ? {
        id: appointment.patient_id,
        display_name: appointment.patient_display_name,
        mrn: appointment.patient_mrn,
      }
    : proposal?.patient || null;
  const initialDate = appointment
    ? clinicKeyFromIso(appointment.start_at)
    : proposal?.date || slot?.date || clinicTodayKey();
  let selectedStartAt = appointment?.start_at || proposal?.start_at || slot?.start_at || null;
  let slotButtons = [];

  const body = ui.el('div', { class: 'appointment-form' });
  const picker = patientPicker(initialPatient, proposal?.patient_query || '');
  body.append(picker.node);

  const dateField = ui.el('div', { class: 'field' });
  const dateInput = ui.el('input', {
    id: 'appointment-date',
    type: 'date',
    value: initialDate,
    min: clinicTodayKey(),
  });
  dateField.append(ui.el('label', { for: 'appointment-date', text: 'Date (Europe/Sofia)' }), dateInput);
  body.append(dateField);

  const slotField = ui.el('div', { class: 'field' });
  const slotHelp = ui.el('p', {
    class: 'field-hint',
    text: 'Choose an available time. Times are clinic-local.',
  });
  const slotGrid = ui.el('div', { class: 'slot-grid', role: 'group', 'aria-label': 'Available times' });
  const slotStatus = ui.el('p', { class: 'text-muted', role: 'status' });
  slotField.append(ui.el('span', { class: 'field-label', text: 'Time' }), slotHelp, slotStatus, slotGrid);
  body.append(slotField);

  const durationField = ui.el('div', { class: 'field' });
  const defaultDuration =
    appointment?.duration_minutes ||
    proposal?.duration_minutes ||
    state.availability?.default_duration_minutes ||
    30;
  const durationInput = ui.el('input', {
    id: 'appointment-duration',
    type: 'number',
    min: 5,
    max: 480,
    step: 5,
    value: String(defaultDuration),
  });
  durationField.append(
    ui.el('label', { for: 'appointment-duration', text: 'Duration (minutes)' }),
    durationInput
  );
  body.append(durationField);

  const initialTypeText = appointment?.appointment_type || proposal?.appointment_type || '';
  const matchedType = findVisitType(initialTypeText);
  const initialTypeId = matchedType ? matchedType.id : initialTypeText ? 'other' : 'general';
  const initialCustomType = matchedType ? '' : initialTypeText;

  const typeField = ui.el('div', { class: 'field' });
  const typeSelect = ui.el('select', { id: 'appointment-type' });
  for (const type of VISIT_TYPES) {
    typeSelect.append(ui.el('option', { value: type.id, text: type.label }));
  }
  typeSelect.value = initialTypeId;
  const typeHint = ui.el('p', { class: 'field-hint' });
  const customTypeField = ui.el('div', { class: 'field', hidden: true });
  const customTypeInput = ui.el('input', {
    id: 'appointment-type-custom',
    type: 'text',
    maxlength: 100,
    value: initialCustomType,
    placeholder: 'Enter the custom visit type',
  });
  customTypeField.append(
    ui.el('label', { for: 'appointment-type-custom', text: 'Custom visit type' }),
    customTypeInput
  );
  typeField.append(
    ui.el('label', { for: 'appointment-type', text: 'Type of visit' }),
    typeSelect,
    typeHint,
    customTypeField
  );
  body.append(typeField);

  function syncTypeFields({ applyDuration = false } = {}) {
    const selectedType = VISIT_TYPES.find((type) => type.id === typeSelect.value) || null;
    customTypeField.hidden = !selectedType || selectedType.id !== 'other';
    typeHint.textContent = selectedType
      ? `Suggested duration: ${selectedType.duration} minutes.`
      : '';
    if (applyDuration && selectedType && selectedType.id !== 'other') {
      durationInput.value = String(selectedType.duration);
    }
  }

  typeSelect.addEventListener('change', () => syncTypeFields({ applyDuration: true }));
  syncTypeFields();

  const notesField = ui.el('div', { class: 'field' });
  const notesInput = ui.el('textarea', {
    id: 'appointment-notes',
    rows: 3,
    maxlength: 2000,
  });
  notesInput.value = appointment?.notes || proposal?.notes || '';
  notesField.append(ui.el('label', { for: 'appointment-notes', text: 'Notes' }), notesInput);
  body.append(notesField);

  const errorNode = formErrorNode();
  errorNode.hidden = true;
  body.append(errorNode);

  function renderSlots(slots) {
    ui.clear(slotGrid);
    slotButtons = [];
    const dateValue = dateInput.value;
    const currentStart = appointment?.start_at;
    const candidates = [...(slots || [])];
    if (
      selectedStartAt &&
      !candidates.some((item) => item.start_at === selectedStartAt) &&
      clinicKeyFromIso(selectedStartAt) === dateValue
    ) {
      candidates.push({
        start_at: selectedStartAt,
        local_start: formatClinicTime(selectedStartAt),
        local_end: formatClinicTime(selectedStartAt),
        available: true,
        reason: 'available',
        custom: true,
      });
    }
    if (candidates.length === 0) {
      slotStatus.textContent = 'No slots for this date. Configure availability or choose another date.';
      return;
    }
    slotStatus.textContent = '';
    candidates.sort((a, b) => String(a.local_start).localeCompare(String(b.local_start)));
    for (const candidate of candidates) {
      const isCurrent = candidate.start_at === currentStart;
      const enabled = candidate.available || isCurrent || candidate.custom;
      const button = ui.el(
        'button',
        {
          type: 'button',
          class: `slot-button${selectedStartAt === candidate.start_at ? ' is-selected' : ''}${
            enabled ? '' : ' slot-button--disabled'
          }`,
          disabled: !enabled,
          'aria-pressed': selectedStartAt === candidate.start_at ? 'true' : 'false',
          title: candidate.reason || '',
          onClick: () => {
            selectedStartAt = candidate.start_at;
            for (const node of slotButtons) {
              const active = node.dataset.start === selectedStartAt;
              node.classList.toggle('is-selected', active);
              node.setAttribute('aria-pressed', active ? 'true' : 'false');
            }
          },
        },
        candidate.local_start
      );
      button.dataset.start = candidate.start_at;
      slotButtons.push(button);
      slotGrid.append(button);
    }
  }

  async function refreshSlots() {
    slotStatus.textContent = 'Loading slots…';
    try {
      const slots = await loadSlotsForForm(dateInput.value);
      if (slots === null) {
        slotStatus.textContent = 'Past dates cannot be booked.';
        ui.clear(slotGrid);
        slotButtons = [];
        return;
      }
      renderSlots(slots);
    } catch (error) {
      slotStatus.textContent = api.describeApiError(error).message;
      ui.clear(slotGrid);
      slotButtons = [];
    }
  }

  dateInput.addEventListener('change', () => {
    selectedStartAt = null;
    refreshSlots();
  });

  const cancelButton = ui.el('button', {
    class: 'btn btn-secondary',
    type: 'button',
    onClick: () => close(),
  }, 'Cancel');
  const saveButton = ui.el('button', {
    class: 'btn btn-primary',
    type: 'button',
    onClick: async () => {
      setFormError(errorNode, '');
      const patient = picker.getPatient();
      if (!patient) {
        setFormError(errorNode, 'Select a patient before saving.');
        return;
      }
      if (!selectedStartAt) {
        setFormError(errorNode, 'Select an available time before saving.');
        return;
      }
      const duration = Number(durationInput.value);
      if (!Number.isFinite(duration) || duration < 5 || duration > 480) {
        setFormError(errorNode, 'Duration must be between 5 and 480 minutes.');
        return;
      }
      const selectedType = VISIT_TYPES.find((type) => type.id === typeSelect.value) || null;
      let appointmentType = selectedType ? selectedType.label : null;
      if (selectedType && selectedType.id === 'other') {
        const customType = customTypeInput.value.trim();
        if (!customType) {
          setFormError(errorNode, 'Enter a custom visit type.');
          return;
        }
        appointmentType = customType;
      }
      const payload = {
        patient_id: patient.id,
        start_at: selectedStartAt,
        duration_minutes: duration,
        appointment_type: appointmentType,
        notes: notesInput.value.trim() || null,
      };
      if (!editing && state.isAdmin) payload.doctor_id = state.doctorId;
      ui.setBusy(saveButton, true, editing ? 'Saving…' : 'Creating…');
      try {
        if (editing) {
          await api.patch(`/appointments/${appointment.id}`, payload);
          toast.showToast({ message: 'Appointment updated.', tone: 'success' });
        } else {
          await api.post('/appointments', payload);
          toast.showToast({ message: 'Appointment created.', tone: 'success' });
        }
        close();
        await loadData();
      } catch (error) {
        const described = api.describeApiError(error);
        setFormError(errorNode, described.message);
        ui.setBusy(saveButton, false);
        if (error.status === 409) refreshSlots();
      }
    },
  }, editing ? 'Save changes' : 'Create appointment');

  const { close } = ui.openDialog({
    title: editing ? 'Reschedule appointment' : 'New appointment',
    bodyNodes: [body],
    actionNodes: [cancelButton, saveButton],
    icon: 'calendar',
  });
  refreshSlots();
}

/* ------------------------------------------------------- availability form */

function timeValue(value) {
  return String(value || '09:00').slice(0, 5);
}

function intervalRow(startValue = '09:00', endValue = '17:00') {
  const row = ui.el('div', { class: 'interval-row' });
  const start = ui.el('input', { type: 'time', value: timeValue(startValue), 'data-role': 'start', 'aria-label': 'Start time' });
  const end = ui.el('input', { type: 'time', value: timeValue(endValue), 'data-role': 'end', 'aria-label': 'End time' });
  const remove = ui.el('button', {
    type: 'button',
    class: 'btn btn-ghost btn-sm',
    onClick: () => row.remove(),
  }, 'Remove');
  row.append(ui.el('span', { class: 'interval-row__label', text: 'From' }), start,
    ui.el('span', { class: 'interval-row__label', text: 'to' }), end, remove);
  return row;
}

function timeOffRow(startValue = '', endValue = '', reason = '') {
  const row = ui.el('div', { class: 'time-off-row' });
  const start = ui.el('input', { type: 'datetime-local', value: startValue, 'data-role': 'start', 'aria-label': 'Time off starts' });
  const end = ui.el('input', { type: 'datetime-local', value: endValue, 'data-role': 'end', 'aria-label': 'Time off ends' });
  const reasonInput = ui.el('input', { type: 'text', maxlength: 200, value: reason, 'data-role': 'reason', placeholder: 'Reason (optional)', 'aria-label': 'Time off reason' });
  const remove = ui.el('button', {
    type: 'button',
    class: 'btn btn-ghost btn-sm',
    onClick: () => row.remove(),
  }, 'Remove');
  row.append(start, end, reasonInput, remove);
  return row;
}

async function openAvailability() {
  if (!state.canManage) return;
  if (state.isAdmin && !state.doctorId) {
    toast.showToast({ message: 'Select a doctor first.', tone: 'error' });
    return;
  }
  try {
    await loadAvailability();
  } catch (error) {
    toast.showToast({ message: api.describeApiError(error).message, tone: 'error' });
    return;
  }
  const availability = state.availability || {
    default_duration_minutes: 30,
    slot_increment_minutes: 30,
    working_hours: [],
    time_off: [],
  };
  const body = ui.el('div', { class: 'availability-form' });
  const intro = ui.el('p', {
    class: 'text-muted',
    text: `Working hours are clinic-local (${state.timezone}); time off is stored as an exact UTC interval.`,
  });
  body.append(intro);

  const defaults = ui.el('div', { class: 'form-grid' });
  const durationInput = ui.el('input', {
    id: 'availability-duration',
    type: 'number',
    min: 5,
    max: 240,
    value: String(availability.default_duration_minutes || 30),
  });
  const incrementInput = ui.el('input', {
    id: 'availability-increment',
    type: 'number',
    min: 5,
    max: 60,
    value: String(availability.slot_increment_minutes || 30),
  });
  defaults.append(
    ui.el('div', { class: 'field' }, ui.el('label', { for: 'availability-duration', text: 'Default duration (minutes)' }), durationInput),
    ui.el('div', { class: 'field' }, ui.el('label', { for: 'availability-increment', text: 'Slot increment (minutes)' }), incrementInput)
  );
  body.append(defaults);

  const dayRows = [];
  const dayContainer = ui.el('div', { class: 'availability-days' });
  const byDay = new Map();
  for (const row of availability.working_hours || []) {
    const day = Number(row.weekday);
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day).push(row);
  }
  function addIntervalRow(dayRow, start, end) {
    const list = dayRow.querySelector('.interval-list');
    list.append(intervalRow(start, end));
  }
  for (let day = 0; day < 7; day += 1) {
    const dayRow = ui.el('div', { class: 'availability-day' });
    dayRow.dataset.day = String(day);
    dayRow.append(ui.el('h3', { class: 'availability-day__name', text: DAY_NAMES_FULL[day] }));
    const list = ui.el('div', { class: 'interval-list' });
    for (const interval of byDay.get(day) || []) {
      list.append(intervalRow(interval.start_time, interval.end_time));
    }
    const add = ui.el('button', {
      type: 'button',
      class: 'btn btn-secondary btn-sm',
      onClick: () => addIntervalRow(dayRow, '09:00', '17:00'),
    }, 'Add interval');
    dayRow.append(list, add);
    dayRows.push(dayRow);
    dayContainer.append(dayRow);
  }
  body.append(dayContainer);
  body.append(
    ui.el('div', { class: 'form-actions' },
      ui.el('button', {
        type: 'button',
        class: 'btn btn-secondary btn-sm',
        onClick: () => {
          for (const dayRow of dayRows) {
            const day = Number(dayRow.dataset.day);
            const list = dayRow.querySelector('.interval-list');
            ui.clear(list);
            if (day < 5) list.append(intervalRow('09:00', '17:00'));
          }
        },
      }, 'Use standard weekdays (09:00–17:00)')
    )
  );

  const timeOffSection = ui.el('div', { class: 'availability-days' });
  timeOffSection.append(ui.el('h3', { text: 'Time off' }));
  const timeOffList = ui.el('div', { class: 'time-off-list' });
  for (const off of availability.time_off || []) {
    timeOffList.append(timeOffRow(utcToClinicInput(off.start_at), utcToClinicInput(off.end_at), off.reason || ''));
  }
  timeOffSection.append(
    timeOffList,
    ui.el('button', {
      type: 'button',
      class: 'btn btn-secondary btn-sm',
      onClick: () => timeOffList.append(timeOffRow()),
    }, 'Add time off')
  );
  body.append(timeOffSection);

  const errorNode = formErrorNode();
  errorNode.hidden = true;
  body.append(errorNode);

  const cancelButton = ui.el('button', { class: 'btn btn-secondary', type: 'button', onClick: () => close() }, 'Cancel');
  const saveButton = ui.el('button', {
    class: 'btn btn-primary',
    type: 'button',
    onClick: async () => {
      setFormError(errorNode, '');
      const workingHours = [];
      for (const dayRow of dayRows) {
        const day = Number(dayRow.dataset.day);
        for (const row of dayRow.querySelectorAll('.interval-row')) {
          const start = row.querySelector('[data-role="start"]').value;
          const end = row.querySelector('[data-role="end"]').value;
          if (!start || !end) {
            setFormError(errorNode, 'Every working interval needs a start and end time.');
            return;
          }
          if (start >= end) {
            setFormError(errorNode, 'Working intervals must end after they start.');
            return;
          }
          workingHours.push({ weekday: day, start_time: `${start}:00`, end_time: `${end}:00` });
        }
      }
      const timeOff = [];
      for (const row of timeOffList.querySelectorAll('.time-off-row')) {
        const startInput = row.querySelector('[data-role="start"]').value;
        const endInput = row.querySelector('[data-role="end"]').value;
        if (!startInput && !endInput) continue;
        if (!startInput || !endInput) {
          setFormError(errorNode, 'Time off needs both a start and an end.');
          return;
        }
        const [startDate, startTime] = startInput.split('T');
        const [endDate, endTime] = endInput.split('T');
        const startAt = clinicWallToUtc(startDate, startTime);
        const endAt = clinicWallToUtc(endDate, endTime);
        if (!startAt || !endAt) {
          setFormError(errorNode, 'That time off falls on a daylight-saving gap. Choose another time.');
          return;
        }
        if (endAt <= startAt) {
          setFormError(errorNode, 'Time off must end after it starts.');
          return;
        }
        timeOff.push({
          start_at: startAt,
          end_at: endAt,
          reason: row.querySelector('[data-role="reason"]').value.trim() || null,
        });
      }
      ui.setBusy(saveButton, true, 'Saving…');
      try {
        await api.put('/appointments/availability', {
          default_duration_minutes: Number(durationInput.value),
          slot_increment_minutes: Number(incrementInput.value),
          working_hours: workingHours,
          time_off: timeOff,
        }, { query: scopedQuery() });
        toast.showToast({ message: 'Availability updated.', tone: 'success' });
        close();
        await loadAvailability();
        await loadData();
      } catch (error) {
        setFormError(errorNode, api.describeApiError(error).message);
        ui.setBusy(saveButton, false);
      }
    },
  }, 'Save availability');

  const { close } = ui.openDialog({
    title: `Availability · ${doctorLabel()}`,
    bodyNodes: [body],
    actionNodes: [cancelButton, saveButton],
    icon: 'calendar',
  });
}

/* --------------------------------------------------------------- voice UI */

function voiceStatus(message, tone = 'info') {
  const node = document.getElementById('voice-status');
  if (!node) return;
  node.classList.remove(
    'voice-status--info',
    'voice-status--success',
    'voice-status--warn',
    'voice-status--error'
  );
  node.textContent = message || '';
  node.hidden = !message;
  if (message && tone !== 'info') node.classList.add(`voice-status--${tone}`);
}

function selectedVoiceLanguage() {
  const value = document.getElementById('voice-language')?.value;
  return value === 'en' || value === 'bg' ? value : 'auto';
}

function correctionLanguage() {
  const manual = selectedVoiceLanguage();
  if (manual === 'en' || manual === 'bg') return manual;
  const detected = state.voice.detectedLanguage;
  if (detected === 'en' || detected === 'bg') return detected;
  const transcript = document.getElementById('voice-transcript');
  return /[А-Яа-яЁё]/.test(transcript?.value || '') ? 'bg' : 'en';
}

function updateVoiceExample() {
  const node = document.getElementById('voice-example-text');
  if (!node) return;
  const selected = selectedVoiceLanguage();
  const language = selected === 'auto'
    ? (state.voice.detectedLanguage === 'en' ? 'en' : 'bg')
    : selected;
  node.textContent = `Example: ${VOICE_EXAMPLES[language]}`;
}

function setVoiceState(kind, message, tone = 'info') {
  state.voice.state = kind;
  const listener = document.getElementById('voice-listener');
  if (listener) listener.dataset.state = kind;
  const record = document.getElementById('voice-record');
  const busy = kind === 'requesting' || kind === 'processing';
  if (record) {
    record.disabled = busy;
    if (busy) record.setAttribute('aria-busy', 'true');
    else record.removeAttribute('aria-busy');
    const labels = {
      idle: 'Start voice recording',
      requesting: 'Requesting microphone access',
      listening: 'Stop recording and transcribe',
      processing: 'Processing voice recording',
      success: 'Start another voice recording',
      error: 'Retry voice recording',
    };
    record.setAttribute('aria-label', labels[kind] || labels.idle);
  }
  const cancel = document.getElementById('voice-cancel');
  if (cancel) cancel.hidden = kind !== 'listening';
  if (kind !== 'listening') updateVoiceLevel(0);
  voiceStatus(message, tone);
  setVoiceControls();
}

function setVoiceControls() {
  const clear = document.getElementById('voice-clear');
  const interpret = document.getElementById('voice-interpret');
  const text = document.getElementById('voice-transcript')?.value.trim() || '';
  const busy = state.voice.state === 'requesting'
    || state.voice.state === 'listening'
    || state.voice.state === 'processing';
  if (clear) clear.hidden = busy || (!state.voice.blob && !text);
  if (interpret) interpret.disabled = busy || text.length < 3;
}

function formatVoiceTime(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

function updateVoiceLevel(level) {
  const normalized = Math.max(0, Math.min(4, Number(level) || 0));
  state.voice.level = normalized;
  const waveform = document.getElementById('voice-waveform');
  if (waveform) waveform.dataset.level = String(normalized);
}

function stopVoiceMeter() {
  if (state.voice.meterFrame != null) {
    if (typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(state.voice.meterFrame);
    }
    state.voice.meterFrame = null;
  }
  const context = state.voice.audioContext;
  state.voice.audioContext = null;
  state.voice.analyser = null;
  state.voice.meterData = null;
  if (context && typeof context.close === 'function') {
    try {
      const closing = context.close();
      if (closing && typeof closing.catch === 'function') closing.catch(() => {});
    } catch {
      // The audio context may already be closed.
    }
  }
  updateVoiceLevel(0);
}

function startVoiceMeter(stream) {
  stopVoiceMeter();
  const AudioCtor = typeof window !== 'undefined'
    ? (window.AudioContext || window.webkitAudioContext)
    : null;
  if (!AudioCtor || typeof requestAnimationFrame !== 'function') {
    updateVoiceLevel(2);
    return;
  }
  try {
    const context = new AudioCtor();
    if (typeof context.resume === 'function') {
      const resumed = context.resume();
      if (resumed && typeof resumed.catch === 'function') resumed.catch(() => {});
    }
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.8;
    source.connect(analyser);
    state.voice.audioContext = context;
    state.voice.analyser = analyser;
    state.voice.meterData = new Uint8Array(analyser.fftSize);
    const session = state.voice.session;
    const sample = () => {
      if (state.voice.session !== session) return;
      const data = state.voice.meterData;
      if (!data) return;
      try {
        analyser.getByteTimeDomainData(data);
      } catch {
        updateVoiceLevel(2);
        return;
      }
      let sum = 0;
      for (let index = 0; index < data.length; index += 1) {
        const value = (data[index] - 128) / 128;
        sum += value * value;
      }
      const rms = Math.sqrt(sum / data.length);
      const level = rms < 0.01 ? 0 : rms < 0.035 ? 1 : rms < 0.08 ? 2 : rms < 0.16 ? 3 : 4;
      updateVoiceLevel(level);
      state.voice.meterFrame = requestAnimationFrame(sample);
    };
    state.voice.meterFrame = requestAnimationFrame(sample);
  } catch {
    stopVoiceMeter();
    updateVoiceLevel(2);
  }
}

function stopVoiceStream() {
  stopVoiceMeter();
  if (state.voice.stream) {
    for (const track of state.voice.stream.getTracks()) track.stop();
    state.voice.stream = null;
  }
}

function stopVoiceTimer() {
  if (state.voice.timer) {
    clearInterval(state.voice.timer);
    state.voice.timer = null;
  }
}

function startVoiceTimer() {
  stopVoiceTimer();
  const tick = () => {
    const elapsed = Math.max(0, Math.floor((Date.now() - state.voice.startedAt) / 1000));
    const timer = document.getElementById('voice-timer');
    if (timer) {
      timer.textContent = `${formatVoiceTime(elapsed)} / ${formatVoiceTime(VOICE_MAX_SECONDS)}`;
    }
    if (elapsed >= VOICE_MAX_SECONDS) stopRecording();
  };
  tick();
  state.voice.timer = setInterval(tick, 250);
  if (state.voice.timer && typeof state.voice.timer.unref === 'function') {
    state.voice.timer.unref();
  }
}

function chooseMimeType() {
  if (typeof window === 'undefined' || typeof window.MediaRecorder === 'undefined') return '';
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
  ];
  for (const candidate of candidates) {
    if (typeof window.MediaRecorder.isTypeSupported !== 'function' || window.MediaRecorder.isTypeSupported(candidate)) {
      return candidate;
    }
  }
  return '';
}

function resetVoice() {
  state.voice.session += 1;
  if (state.voice.stopTimer) {
    clearTimeout(state.voice.stopTimer);
    state.voice.stopTimer = null;
  }
  if (state.voice.recorder && state.voice.recorder.state === 'recording') {
    try {
      state.voice.recorder.stop();
    } catch {
      // The recorder may already be stopped; the session id ignores the event.
    }
  }
  stopVoiceTimer();
  stopVoiceStream();
  state.voice.recorder = null;
  state.voice.chunks = [];
  state.voice.blob = null;
  state.voice.startedAt = 0;
  state.voice.cancelRequested = false;
  state.voice.detectedLanguage = '';
  updateVoiceLevel(0);
  const timer = document.getElementById('voice-timer');
  if (timer) timer.textContent = '';
  const proposal = document.getElementById('voice-proposal');
  if (proposal) {
    proposal.hidden = true;
    ui.clear(proposal);
  }
  resetTranscriptionCorrections();
  hidePatientSuggestions();
  renderVoiceSuggestionPanel();
  updateVoiceExample();
  setVoiceState('idle', 'Tap to speak');
}

async function startRecording() {
  if (state.voice.state === 'listening') {
    stopRecording();
    return;
  }
  if (state.voice.state === 'requesting' || state.voice.state === 'processing') return;
  if (
    typeof navigator === 'undefined' ||
    !navigator.mediaDevices ||
    typeof navigator.mediaDevices.getUserMedia !== 'function' ||
    typeof window === 'undefined' ||
    typeof window.MediaRecorder === 'undefined'
  ) {
    setVoiceState(
      'error',
      'Recording is not supported in this browser. Type the request below.',
      'error'
    );
    return;
  }

  resetVoice();
  const session = state.voice.session;
  setVoiceState('requesting', 'Requesting microphone access…');
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    if (state.voice.session !== session) {
      for (const track of stream.getTracks()) track.stop();
      return;
    }
    const mimeType = chooseMimeType();
    const recorder = mimeType
      ? new window.MediaRecorder(stream, { mimeType })
      : new window.MediaRecorder(stream);
    state.voice.stream = stream;
    state.voice.recorder = recorder;
    state.voice.chunks = [];
    state.voice.startedAt = Date.now();
    state.voice.cancelRequested = false;

    recorder.addEventListener('dataavailable', (event) => {
      if (state.voice.session === session && event.data && event.data.size > 0) {
        state.voice.chunks.push(event.data);
      }
    });
    recorder.addEventListener('error', () => {
      if (state.voice.session !== session) return;
      stopVoiceTimer();
      stopVoiceStream();
      state.voice.recorder = null;
      state.voice.blob = null;
      setVoiceState(
        'error',
        'Recording failed. Tap the circle to retry, or type the request below.',
        'error'
      );
    });
    recorder.addEventListener('stop', () => {
      if (state.voice.session !== session) return;
      if (state.voice.stopTimer) {
        clearTimeout(state.voice.stopTimer);
        state.voice.stopTimer = null;
      }
      stopVoiceTimer();
      stopVoiceStream();
      const elapsed = Math.max(1, Math.round((Date.now() - state.voice.startedAt) / 1000));
      const timer = document.getElementById('voice-timer');
      if (timer) timer.textContent = '';
      const blob = state.voice.chunks.length > 0
        ? new Blob(state.voice.chunks, { type: recorder.mimeType || mimeType || 'audio/webm' })
        : null;
      state.voice.recorder = null;
      if (state.voice.cancelRequested) {
        state.voice.cancelRequested = false;
        state.voice.blob = null;
        setVoiceState('idle', 'Recording cancelled. Tap to speak.');
        return;
      }
      if (!blob || blob.size < 512) {
        state.voice.blob = null;
        setVoiceState(
          'error',
          'No audio was captured. Check the microphone and try again, or type the request.',
          'warn'
        );
        return;
      }
      state.voice.blob = blob;
      void transcribeVoice(elapsed);
    });

    recorder.start(1000);
    startVoiceTimer();
    startVoiceMeter(stream);
    setVoiceState('listening', 'Listening…');
  } catch (error) {
    if (state.voice.session !== session) return;
    stopVoiceStream();
    state.voice.recorder = null;
    const name = error && error.name ? error.name : '';
    let message = 'The microphone could not be started. Tap the circle to retry, or type below.';
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      message = 'Microphone permission was denied. Allow access in the browser, or type below.';
    } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
      message = 'No microphone was detected. Connect a microphone, or type below.';
    } else if (name === 'NotReadableError' || name === 'TrackStartError') {
      message = 'The microphone is busy or unavailable. Close other apps, then retry.';
    }
    setVoiceState('error', message, 'error');
  }
}

function stopRecording() {
  if (state.voice.recorder && state.voice.recorder.state === 'recording') {
    stopVoiceTimer();
    try {
      state.voice.recorder.stop();
    } catch {
      // Fall through to the safety timeout below.
    }
    // Keep the stream alive until the final `dataavailable`/`stop` event so
    // browsers can flush the last audio chunk. The timeout is a safety net for
    // a recorder that never emits `stop`.
    if (state.voice.stopTimer) clearTimeout(state.voice.stopTimer);
    state.voice.stopTimer = setTimeout(() => {
      state.voice.stopTimer = null;
      if (state.voice.state !== 'listening') return;
      state.voice.session += 1;
      state.voice.recorder = null;
      state.voice.blob = null;
      stopVoiceStream();
      stopVoiceTimer();
      setVoiceState(
        'error',
        'Recording could not be finalized. Tap the circle to retry, or type below.',
        'error'
      );
    }, 1500);
    if (typeof state.voice.stopTimer.unref === 'function') {
      state.voice.stopTimer.unref();
    }
    return;
  }
  stopVoiceStream();
  stopVoiceTimer();
}

function cancelRecording() {
  if (state.voice.state !== 'listening') return;
  resetVoice();
  setVoiceState('idle', 'Recording cancelled. Tap to speak.');
}

async function transcribeVoice(recordedSeconds = 0) {
  if (!state.voice.blob || state.voice.state === 'processing') return;
  setVoiceState('processing', 'Processing your voice…');
  const language = selectedVoiceLanguage();
  const blob = state.voice.blob;
  state.voice.blob = null;
  state.voice.chunks = [];
  try {
    const form = new FormData();
    form.append('file', blob, 'appointment-recording.webm');
    form.append('language', language);
    const result = await api.postForm('/voice/transcriptions', form);
    const text = String(result.text || '').trim();
    const detected = typeof result.language === 'string'
      ? result.language.trim().toLowerCase()
      : '';
    if (detected) state.voice.detectedLanguage = detected;
    const transcript = document.getElementById('voice-transcript');
    if (transcript) {
      transcript.value = text;
    }
    if (text) {
      resetTranscriptionCorrections();
      transcript?.focus();
      updateVoiceExample();
      const duration = Number(result.duration_seconds);
      const durationText = Number.isFinite(duration)
        ? i18n.t('{seconds}s of audio', { seconds: duration })
        : (recordedSeconds
          ? i18n.t('{seconds}s recorded', { seconds: recordedSeconds })
          : '');
      const languageText = detected === 'bg'
        ? i18n.t('Bulgarian')
        : detected === 'en'
          ? i18n.t('English')
          : (detected ? i18n.t('detected: {code}', { code: detected }) : '');
      const detail = [languageText, durationText].filter(Boolean).join(', ');
      setVoiceState(
        'success',
        detail
          ? i18n.t('Transcription ready ({detail}). Review and edit it below, then propose an appointment.', { detail })
          : i18n.t('Transcription ready. Review and edit it below, then propose an appointment.'),
        'success'
      );
      schedulePatientSuggestions(0);
      void checkTranscriptionCorrections();
    } else {
      resetTranscriptionCorrections();
      hidePatientSuggestions();
      setVoiceState(
        'error',
        'No speech was detected. Tap the circle to retry, or type the request below.',
        'warn'
      );
    }
  } catch (error) {
    const described = api.describeApiError(error);
    const retry = error && error.retryAfter ? ` Retry after ${error.retryAfter}s.` : '';
    setVoiceState(
      'error',
      `${described.message}${retry} Tap the circle to retry, or type below.`,
      'error'
    );
  } finally {
    setVoiceControls();
  }
}

function insertVoiceExample() {
  if (state.voice.state === 'requesting'
    || state.voice.state === 'listening'
    || state.voice.state === 'processing') {
    return;
  }
  const transcript = document.getElementById('voice-transcript');
  if (!transcript) return;
  const selected = selectedVoiceLanguage();
  const exampleLanguage = selected === 'auto'
    ? (state.voice.detectedLanguage === 'en' ? 'en' : 'bg')
    : selected;
  transcript.value = VOICE_EXAMPLES[exampleLanguage];
  resetTranscriptionCorrections();
  transcript.focus();
  setVoiceState(
    'idle',
    'Example inserted. Edit it if needed, then propose an appointment.',
    'success'
  );
  schedulePatientSuggestions(0);
  void checkTranscriptionCorrections();
}

function correctionKey(item, source) {
  const original = source === 'patient'
    ? (item.matched_name_text || item.matched_mrn_text || '')
    : (item.original_text || '');
  const suggested = source === 'patient'
    ? `${item.display_name || ''}|${item.mrn || ''}`
    : (item.suggested_text || '');
  return `${source}|${item.category || ''}|${original}|${suggested}`;
}

function correctionCategoryLabel(category) {
  const labels = {
    appointment_type: 'Appointment type',
    date: 'Date',
    weekday: 'Weekday',
    time: 'Time',
    duration: 'Duration',
    medical_term: 'Medical term',
    number: 'Number',
    other: 'Transcription',
    patient_name: 'Patient name',
  };
  return labels[category] || 'Transcription';
}

function correctionConfidenceLabel(confidence) {
  if (confidence === 'high') return 'High confidence';
  if (confidence === 'low') return 'Low confidence';
  return 'Medium confidence';
}

function patientSuggestionOriginal(item) {
  return [item.matched_name_text, item.matched_mrn_text].filter(Boolean).join(' · ');
}

function patientSuggestionSuggested(item) {
  return `${item.display_name || 'Patient'}${item.mrn ? ` (${item.mrn})` : ''}`;
}

function correctionVisible(item, source) {
  if (state.voice.dismissedCorrections.has(correctionKey(item, source))) return false;
  const transcript = document.getElementById('voice-transcript');
  const text = transcript ? transcript.value : '';
  if (source === 'patient') {
    const original = item.matched_name_text || item.matched_mrn_text;
    return Boolean(original && text.includes(original));
  }
  return Boolean(item.original_text && text.includes(item.original_text));
}

function visiblePatientSuggestions() {
  return state.voice.suggestions.filter((item) => correctionVisible(item, 'patient'));
}

function visibleCorrections() {
  return state.voice.corrections.filter((item) => correctionVisible(item, 'ai'));
}

function renderAppliedCorrection() {
  const applied = state.voice.appliedCorrections.at(-1);
  if (!applied) return null;
  return ui.el('div', { class: 'voice-corrections__applied' },
    ui.el('span', { class: 'voice-corrections__applied-text' },
      ui.el('span', {
        class: 'voice-corrections__applied-label',
        text: `Applied · ${correctionCategoryLabel(applied.category)}`,
      }),
      ui.el('del', { text: applied.original }),
      ui.el('span', { class: 'voice-corrections__arrow', text: '→' }),
      ui.el('ins', { text: applied.suggested }),
    ),
    ui.el('button', {
      class: 'link-button',
      type: 'button',
      onClick: () => undoLastCorrection(),
    }, 'Undo'),
  );
}

function renderCorrectionCard(item) {
  return ui.el('article', { class: 'voice-correction', 'data-category': item.category },
    ui.el('div', { class: 'voice-correction__meta' },
      ui.el('span', { class: 'badge badge--neutral', text: correctionCategoryLabel(item.category) }),
      ui.el('span', { class: 'voice-correction__confidence', text: correctionConfidenceLabel(item.confidence) }),
    ),
    ui.el('div', { class: 'voice-correction__diff' },
      ui.el('p', { class: 'voice-correction__line' },
        ui.el('span', { class: 'voice-correction__label', text: 'Original' }),
        ui.el('del', { text: item.original_text }),
      ),
      ui.el('p', { class: 'voice-correction__line' },
        ui.el('span', { class: 'voice-correction__label', text: 'Suggested' }),
        ui.el('ins', { text: item.suggested_text }),
      ),
    ),
    item.reason ? ui.el('p', { class: 'voice-correction__reason', text: item.reason }) : null,
    ui.el('div', { class: 'voice-correction__actions' },
      ui.el('button', {
        class: 'btn btn-primary btn-sm',
        type: 'button',
        onClick: () => applyCorrection(item),
      }, 'Use suggestion'),
      ui.el('button', {
        class: 'btn btn-ghost btn-sm',
        type: 'button',
        onClick: () => dismissCorrection(item, 'ai'),
      }, 'Keep original'),
    ),
  );
}

function renderPatientSuggestionRow(item) {
  const matched = item.matched_name_text || item.matched_mrn_text || '';
  return ui.el('div', { class: 'voice-suggestion-row' },
    ui.el('button', {
      class: 'voice-suggestion',
      type: 'button',
      'aria-label': `Use ${item.display_name}, ${item.mrn}`,
      onClick: () => applyPatientSuggestion(item),
    },
      ui.el('span', { class: 'voice-suggestion__text' },
        ui.el('span', { class: 'voice-suggestion__name', text: item.display_name }),
        ui.el('span', { class: 'voice-suggestion__mrn', text: item.mrn }),
        matched
          ? ui.el('span', {
              class: 'voice-suggestion__matched',
              text: `Replace “${matched}”`,
            })
          : null,
      ),
      ui.el('span', { class: 'voice-suggestion__action', text: 'Use' }),
    ),
    ui.el('button', {
      class: 'btn btn-ghost btn-sm',
      type: 'button',
      onClick: () => dismissCorrection(item, 'patient'),
    }, 'Keep original'),
  );
}

function renderVoiceSuggestionPanel() {
  const host = document.getElementById('voice-suggestions');
  if (!host) return;
  ui.clear(host);
  const transcript = document.getElementById('voice-transcript');
  const text = transcript ? transcript.value.trim() : '';
  if (!text) {
    host.hidden = true;
    return;
  }
  const patient = visiblePatientSuggestions();
  const corrections = visibleCorrections();
  const applied = state.voice.appliedCorrections.length > 0;
  const status = state.voice.correctionStatus;
  const hasSuggestions = patient.length > 0 || corrections.length > 0;
  const showCheck = text.length >= 3 && status !== 'checking'
    && ((!hasSuggestions && (status === 'idle' || status === 'stale' || status === 'error'))
      || (!state.voice.correctionAnalysisAvailable && status === 'ready'));
  if (!hasSuggestions && !applied && status === 'idle' && !showCheck) {
    host.hidden = true;
    return;
  }
  host.hidden = false;
  const panel = ui.el('div', { class: 'voice-corrections' });
  panel.append(
    ui.el('div', { class: 'voice-corrections__head' },
      ui.el('p', { class: 'voice-suggestions__title', text: hasSuggestions ? 'Did you mean?' : 'Transcription check' }),
      ui.el('span', { class: 'badge badge--neutral', text: 'Doctor review' }),
    ),
  );
  const appliedRow = renderAppliedCorrection();
  if (appliedRow) panel.append(appliedRow);

  if (status === 'checking') {
    panel.append(
      ui.el('div', { class: 'voice-corrections__state', role: 'status' },
        ui.el('span', { class: 'voice-corrections__spinner', 'aria-hidden': 'true' }),
        ui.el('span', { text: 'Checking transcription…' }),
      ),
    );
  } else if (status === 'error') {
    panel.append(
      ui.el('p', {
        class: 'voice-corrections__state voice-corrections__state--error',
        text: 'Unable to check transcription. You can still edit it manually.',
      }),
    );
  } else if (status === 'stale') {
    panel.append(
      ui.el('p', {
        class: 'voice-corrections__state',
        text: 'Transcript changed. Check transcription again.',
      }),
    );
  } else if (status === 'empty' && !hasSuggestions) {
    panel.append(
      ui.el('p', {
        class: 'voice-corrections__state',
        text: 'No obvious transcription issues detected.',
      }),
    );
  }

  if (patient.length > 0) {
    panel.append(ui.el('p', { class: 'voice-corrections__subhead', text: 'Patient name' }));
    const list = ui.el('div', { class: 'voice-suggestions__list' });
    for (const item of patient) list.append(renderPatientSuggestionRow(item));
    panel.append(list);
  }

  if (corrections.length > 0) {
    panel.append(ui.el('p', { class: 'voice-corrections__subhead', text: 'Possible transcription corrections' }));
    const list = ui.el('div', { class: 'voice-corrections__list' });
    const shown = state.voice.suggestionsExpanded
      ? corrections
      : corrections.slice(0, Math.max(0, CORRECTION_MAX_INLINE - patient.length));
    for (const item of shown) list.append(renderCorrectionCard(item));
    panel.append(list);
  }

  const totalSuggestions = patient.length + corrections.length;
  if (totalSuggestions > CORRECTION_MAX_INLINE) {
    panel.append(
      ui.el('button', {
        class: 'link-button voice-corrections__review',
        type: 'button',
        onClick: () => {
          state.voice.suggestionsExpanded = !state.voice.suggestionsExpanded;
          renderVoiceSuggestionPanel();
        },
      }, state.voice.suggestionsExpanded
        ? 'Show fewer suggestions'
        : `Review all suggestions (${totalSuggestions})`),
    );
  }

  if (status === 'ready' && !state.voice.correctionAnalysisAvailable && corrections.length > 0) {
    panel.append(
      ui.el('p', {
        class: 'voice-corrections__note',
        text: 'AI check unavailable; showing vocabulary checks only.',
      }),
    );
  }

  if (showCheck) {
    panel.append(
      ui.el('button', {
        class: 'btn btn-secondary btn-sm voice-corrections__check',
        type: 'button',
        onClick: () => checkTranscriptionCorrections(),
      }, status === 'error' ? 'Retry check' : 'Check transcription'),
    );
  }

  if (hasSuggestions) {
    panel.append(
      ui.el('p', {
        class: 'voice-suggestions__hint',
        text: 'Review each suggestion. Nothing is submitted automatically.',
      }),
    );
  }
  host.append(panel);
}

function hidePatientSuggestions() {
  state.voice.suggestionSeq += 1;
  state.voice.suggestions = [];
  if (state.voice.suggestionTimer) {
    clearTimeout(state.voice.suggestionTimer);
    state.voice.suggestionTimer = null;
  }
  renderVoiceSuggestionPanel();
}

function renderPatientSuggestions(items) {
  state.voice.suggestions = Array.isArray(items) ? items : [];
  renderVoiceSuggestionPanel();
}

function dismissCorrection(item, source) {
  state.voice.dismissedCorrections.add(correctionKey(item, source));
  if (source === 'patient') {
    const key = correctionKey(item, 'patient');
    state.voice.suggestions = state.voice.suggestions.filter(
      (candidate) => correctionKey(candidate, 'patient') !== key,
    );
  } else {
    const key = correctionKey(item, 'ai');
    state.voice.corrections = state.voice.corrections.filter(
      (candidate) => correctionKey(candidate, 'ai') !== key,
    );
  }
  if (!visiblePatientSuggestions().length && !visibleCorrections().length && state.voice.correctionStatus === 'ready') {
    state.voice.correctionStatus = 'idle';
  }
  const transcript = document.getElementById('voice-transcript');
  if (transcript && typeof transcript.focus === 'function') transcript.focus();
  renderVoiceSuggestionPanel();
}

function undoLastCorrection() {
  const applied = state.voice.appliedCorrections.at(-1);
  const transcript = document.getElementById('voice-transcript');
  if (!applied || !transcript) return;
  const current = transcript.value;
  let restored = null;
  if (current === applied.afterText) {
    restored = applied.beforeText;
  } else if (applied.suggested && current.includes(applied.suggested)) {
    const index = current.lastIndexOf(applied.suggested);
    restored = `${current.slice(0, index)}${applied.original}${current.slice(index + applied.suggested.length)}`;
  }
  state.voice.appliedCorrections.pop();
  if (restored === null) {
    renderVoiceSuggestionPanel();
    toast.showToast({
      message: 'The transcript changed after this correction; undo it manually.',
      tone: 'info',
    });
    return;
  }
  transcript.value = restored;
  state.voice.corrections = [];
  state.voice.correctionStatus = 'stale';
  transcript.focus();
  setVoiceControls();
  renderVoiceSuggestionPanel();
  toast.showToast({ message: 'Correction undone.', tone: 'success' });
}

function applyCorrection(item) {
  const transcript = document.getElementById('voice-transcript');
  if (!transcript || !item || typeof item.original_text !== 'string') return;
  const beforeText = transcript.value;
  const index = beforeText.indexOf(item.original_text);
  if (index < 0) {
    dismissCorrection(item, 'ai');
    toast.showToast({
      message: 'The transcript changed. Review the suggestion again.',
      tone: 'info',
    });
    return;
  }
  const afterText = `${beforeText.slice(0, index)}${item.suggested_text}${beforeText.slice(index + item.original_text.length)}`;
  transcript.value = afterText;
  const key = correctionKey(item, 'ai');
  state.voice.corrections = state.voice.corrections.filter(
    (candidate) => correctionKey(candidate, 'ai') !== key && afterText.includes(candidate.original_text),
  );
  state.voice.suggestions = state.voice.suggestions.filter(
    (candidate) => afterText.includes(candidate.matched_name_text || candidate.matched_mrn_text || ''),
  );
  state.voice.appliedCorrections.push({
    beforeText,
    afterText,
    original: item.original_text,
    suggested: item.suggested_text,
    category: item.category,
    source: 'ai',
  });
  state.voice.correctionStatus = visibleCorrections().length || visiblePatientSuggestions().length
    ? 'ready'
    : 'stale';
  transcript.focus();
  setVoiceControls();
  renderVoiceSuggestionPanel();
}

function applyPatientSuggestion(item) {
  const transcript = document.getElementById('voice-transcript');
  if (!transcript) return;
  const beforeText = transcript.value;
  let text = beforeText;
  const nameFound = Boolean(item.matched_name_text)
    && text.includes(item.matched_name_text);
  const mrnFound = Boolean(item.matched_mrn_text)
    && text.includes(item.matched_mrn_text);
  if (!nameFound && !mrnFound) {
    dismissCorrection(item, 'patient');
    toast.showToast({
      message: 'The transcript changed. Review the suggestion again.',
      tone: 'info',
    });
    return;
  }

  const replaceOnce = (needle, replacement) => {
    const index = text.indexOf(needle);
    if (index < 0) return false;
    text = `${text.slice(0, index)}${replacement}${text.slice(index + needle.length)}`;
    return true;
  };

  let nameReplaced = false;
  let mrnReplaced = false;
  if (nameFound) {
    nameReplaced = replaceOnce(item.matched_name_text, item.display_name);
  }
  if (mrnFound) {
    mrnReplaced = replaceOnce(item.matched_mrn_text, item.mrn);
  }

  const namePresent = nameReplaced || text.includes(item.display_name);
  const mrnPresent = mrnReplaced || text.toLowerCase().includes(item.mrn.toLowerCase());
  if (namePresent && !mrnPresent) {
    const index = text.indexOf(item.display_name);
    text = `${text.slice(0, index)}${item.display_name} (${item.mrn})${text.slice(index + item.display_name.length)}`;
  } else if (!namePresent && mrnPresent) {
    const index = text.toLowerCase().indexOf(item.mrn.toLowerCase());
    text = `${text.slice(0, index)}${item.display_name} (${item.mrn})${text.slice(index + item.mrn.length)}`;
  } else if (!namePresent && !mrnPresent) {
    text = `${text.trim()} ${item.display_name} (${item.mrn})`;
  }

  transcript.value = text;
  const key = correctionKey(item, 'patient');
  state.voice.suggestions = state.voice.suggestions.filter(
    (candidate) => correctionKey(candidate, 'patient') !== key,
  );
  state.voice.corrections = state.voice.corrections.filter(
    (candidate) => text.includes(candidate.original_text),
  );
  state.voice.appliedCorrections.push({
    beforeText,
    afterText: text,
    original: patientSuggestionOriginal(item),
    suggested: patientSuggestionSuggested(item),
    category: 'patient_name',
    source: 'patient',
  });
  state.voice.correctionStatus = visibleCorrections().length || visiblePatientSuggestions().length
    ? 'ready'
    : 'stale';
  transcript.focus();
  setVoiceControls();
  renderVoiceSuggestionPanel();
  toast.showToast({
    message: `Patient updated to ${item.display_name} (${item.mrn}).`,
    tone: 'success',
  });
}

function resetTranscriptionCorrections({ keepApplied = false } = {}) {
  state.voice.correctionSeq += 1;
  if (state.voice.correctionController) {
    try {
      state.voice.correctionController.abort();
    } catch {
      // The request may already be complete.
    }
    state.voice.correctionController = null;
  }
  if (state.voice.correctionTimer) {
    clearTimeout(state.voice.correctionTimer);
    state.voice.correctionTimer = null;
  }
  state.voice.corrections = [];
  state.voice.correctionStatus = 'idle';
  state.voice.correctionText = '';
  state.voice.correctionLanguage = '';
  state.voice.correctionModel = '';
  state.voice.correctionAnalysisAvailable = true;
  state.voice.dismissedCorrections = new Set();
  state.voice.suggestionsExpanded = false;
  if (!keepApplied) state.voice.appliedCorrections = [];
}

function invalidateTranscriptionCorrections({ keepApplied = false } = {}) {
  const transcript = document.getElementById('voice-transcript');
  const text = transcript ? transcript.value.trim() : '';
  state.voice.correctionSeq += 1;
  if (state.voice.correctionController) {
    try {
      state.voice.correctionController.abort();
    } catch {
      // The request may already be complete.
    }
    state.voice.correctionController = null;
  }
  if (state.voice.correctionTimer) {
    clearTimeout(state.voice.correctionTimer);
    state.voice.correctionTimer = null;
  }
  state.voice.corrections = [];
  state.voice.correctionText = '';
  state.voice.correctionLanguage = '';
  state.voice.correctionModel = '';
  state.voice.correctionAnalysisAvailable = true;
  state.voice.dismissedCorrections = new Set();
  state.voice.suggestionsExpanded = false;
  if (!keepApplied) state.voice.appliedCorrections = [];
  state.voice.correctionStatus = text.length >= 3 ? 'stale' : 'idle';
  renderVoiceSuggestionPanel();
}

async function checkTranscriptionCorrections() {
  const transcript = document.getElementById('voice-transcript');
  const text = transcript ? transcript.value.trim() : '';
  if (!transcript || text.length < 3) {
    resetTranscriptionCorrections();
    renderVoiceSuggestionPanel();
    return;
  }
  if (state.voice.correctionStatus === 'checking') return;

  if (state.voice.correctionController) {
    try {
      state.voice.correctionController.abort();
    } catch {
      // The request may already be complete.
    }
  }
  if (state.voice.correctionTimer) clearTimeout(state.voice.correctionTimer);
  const sequence = (state.voice.correctionSeq += 1);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CORRECTION_REQUEST_TIMEOUT_MS);
  if (timer && typeof timer.unref === 'function') timer.unref();
  state.voice.correctionController = controller;
  state.voice.correctionTimer = timer;
  state.voice.correctionText = text;
  state.voice.correctionLanguage = correctionLanguage();
  state.voice.correctionModel = '';
  state.voice.correctionAnalysisAvailable = true;
  state.voice.correctionStatus = 'checking';
  state.voice.corrections = [];
  state.voice.suggestionsExpanded = false;
  renderVoiceSuggestionPanel();

  try {
    const response = await api.post(
      '/appointments/transcription-corrections',
      { text, language: state.voice.correctionLanguage },
      { signal: controller.signal },
    );
    if (sequence !== state.voice.correctionSeq) return;
    const items = Array.isArray(response.suggestions) ? response.suggestions : [];
    state.voice.corrections = items.filter((item) => (
      item
      && typeof item.original_text === 'string'
      && typeof item.suggested_text === 'string'
      && item.original_text !== item.suggested_text
      && text.includes(item.original_text)
    ));
    state.voice.correctionAnalysisAvailable = response.analysis_available !== false;
    state.voice.correctionModel = typeof response.model === 'string' ? response.model : '';
    state.voice.correctionStatus = state.voice.corrections.length
      ? 'ready'
      : (state.voice.correctionAnalysisAvailable ? 'empty' : 'error');
  } catch {
    if (sequence !== state.voice.correctionSeq) return;
    state.voice.corrections = [];
    state.voice.correctionAnalysisAvailable = false;
    state.voice.correctionStatus = 'error';
  } finally {
    if (sequence === state.voice.correctionSeq) {
      if (state.voice.correctionTimer) {
        clearTimeout(state.voice.correctionTimer);
        state.voice.correctionTimer = null;
      }
      state.voice.correctionController = null;
      renderVoiceSuggestionPanel();
    }
  }
}

function schedulePatientSuggestions(delay = SUGGESTION_DEBOUNCE_MS) {
  hidePatientSuggestions();
  const transcript = document.getElementById('voice-transcript');
  const text = transcript ? transcript.value.trim() : '';
  if (
    text.length < 3
    || !state.me
    || !auth.hasPermission(state.me, 'patient:read')
  ) {
    return;
  }
  const seq = state.voice.suggestionSeq;
  state.voice.suggestionTimer = setTimeout(async () => {
    state.voice.suggestionTimer = null;
    try {
      const response = await api.post(
        '/appointments/patient-suggestions',
        { text },
      );
      if (seq !== state.voice.suggestionSeq) return;
      const items = Array.isArray(response.items) ? response.items : [];
      renderPatientSuggestions(items);
    } catch {
      if (seq !== state.voice.suggestionSeq) return;
      // Suggestions are a helpful hint, never a blocker for the transcript.
      renderPatientSuggestions([]);
    }
  }, Math.max(0, delay));
}

function proposalPatientLine(proposal) {
  if (proposal.patient) {
    return `${proposal.patient.display_name} · ${proposal.patient.mrn}`;
  }
  if (proposal.patient_candidates && proposal.patient_candidates.length > 0) {
    return `${proposal.patient_candidates.length} possible patients — choose one`;
  }
  return 'Not resolved';
}

function renderProposal(proposal) {
  const host = document.getElementById('voice-proposal');
  if (!host) return;
  ui.clear(host);
  host.hidden = false;
  const card = ui.el('div', { class: 'proposal-card' });
  card.append(
    ui.el('div', { class: 'proposal-card__head' },
      ui.el('h3', { text: 'AI proposal' }),
      ui.el('span', { class: 'badge badge--ai', text: 'AI-generated' })
    ),
    ui.el('p', { class: 'text-muted', text: 'Review every field. Nothing is created until you press “Create appointment”.' })
  );
  const rows = [
    ['Patient', proposalPatientLine(proposal)],
    ['Date', proposal.date || 'Not stated'],
    ['Time', proposal.start_time || 'Not stated'],
    ['Duration', `${proposal.duration_minutes} minutes`],
    ['Type', proposal.appointment_type || 'Not stated'],
    ['Notes', proposal.notes || 'None'],
    ['Doctor', state.isAdmin ? doctorLabel() : state.me.display_name],
  ];
  const dl = ui.el('dl', { class: 'detail-grid' });
  for (const [label, value] of rows) {
    dl.append(ui.el('dt', { text: label }), ui.el('dd', { text: String(value) }));
  }
  card.append(dl);
  if (proposal.missing_fields && proposal.missing_fields.length > 0) {
    card.append(ui.el('p', { class: 'proposal-card__warning', text: `Missing: ${proposal.missing_fields.join(', ')}. Fill these in the review form.` }));
  }
  if (proposal.warnings && proposal.warnings.length > 0) {
    const list = ui.el('ul', { class: 'proposal-card__warnings' });
    for (const warning of proposal.warnings) list.append(ui.el('li', { text: warning }));
    card.append(list);
  }
  if (proposal.patient_candidates && proposal.patient_candidates.length > 0 && !proposal.patient) {
    const candidates = ui.el('div', { class: 'proposal-candidates' });
    for (const candidate of proposal.patient_candidates) {
      candidates.append(
        ui.el('button', {
          type: 'button',
          class: 'btn btn-secondary btn-sm',
          onClick: () => openAppointmentForm({
            proposal: { ...proposal, patient: candidate },
          }),
        }, `${candidate.display_name} · ${candidate.mrn}`)
      );
    }
    card.append(candidates);
  }
  const actions = ui.el('div', { class: 'form-actions' });
  actions.append(
    ui.el('button', {
      type: 'button',
      class: 'btn btn-primary',
      onClick: () => openAppointmentForm({ proposal }),
    }, proposal.can_confirm ? 'Review & create' : 'Review details'),
    ui.el('button', {
      type: 'button',
      class: 'btn btn-ghost',
      onClick: () => {
        host.hidden = true;
        ui.clear(host);
      },
    }, 'Dismiss')
  );
  card.append(actions);
  host.append(card);
}

async function interpretVoice() {
  const transcript = document.getElementById('voice-transcript');
  const text = transcript ? transcript.value.trim() : '';
  if (text.length < 3) {
    voiceStatus('Enter or record at least a few words first.', 'warn');
    return;
  }
  const button = document.getElementById('voice-interpret');
  if (button) ui.setBusy(button, true, 'Interpreting…');
  voiceStatus('The AI is preparing a proposal…');
  try {
    const query = state.isAdmin && state.doctorId ? { doctor_id: state.doctorId } : undefined;
    const proposal = await api.post('/appointments/interpret', { text }, { query });
    voiceStatus('Proposal ready for review.', 'success');
    renderProposal(proposal);
  } catch (error) {
    const described = api.describeApiError(error);
    const retry = error && error.retryAfter ? ` Retry after ${error.retryAfter}s.` : '';
    voiceStatus(`${described.message}${retry} You can still create the appointment manually.`, 'error');
  } finally {
    if (button) ui.setBusy(button, false);
    setVoiceControls();
  }
}

function initVoice() {
  const record = document.getElementById('voice-record');
  const clear = document.getElementById('voice-clear');
  const cancel = document.getElementById('voice-cancel');
  const interpret = document.getElementById('voice-interpret');
  const transcript = document.getElementById('voice-transcript');
  const language = document.getElementById('voice-language');
  if (record) record.addEventListener('click', startRecording);
  if (cancel) cancel.addEventListener('click', cancelRecording);
  document.getElementById('voice-example-use')?.addEventListener('click', insertVoiceExample);
  if (language) {
    language.addEventListener('change', () => {
      updateVoiceExample();
      invalidateTranscriptionCorrections({ keepApplied: true });
    });
  }
  updateVoiceExample();
  if (clear) {
    clear.addEventListener('click', () => {
      if (transcript) transcript.value = '';
      resetVoice();
      hidePatientSuggestions();
      renderVoiceSuggestionPanel();
      setVoiceState('idle', 'Tap to speak');
    });
  }
  if (interpret) interpret.addEventListener('click', interpretVoice);
  if (transcript) {
    transcript.addEventListener('input', () => {
      setVoiceControls();
      invalidateTranscriptionCorrections();
      schedulePatientSuggestions();
    });
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('pagehide', () => {
      resetVoice();
      clearCancelledCleanup();
    });
  }
  setVoiceState('idle', 'Tap to speak');
}

/* ----------------------------------------------------------------- chrome */

function renderPermissionDenied() {
  const body = ui.clear(document.getElementById('main'));
  body.append(
    ui.el('div', { class: 'state-panel state-panel--error', role: 'alert' },
      ui.el('h1', { class: 'state-panel__title', text: 'You do not have permission' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: 'Appointments are limited to accounts with the appointment:read permission. The backend enforces this on every request.',
      }),
      ui.el('div', { class: 'state-panel__actions' },
        ui.el('a', { class: 'btn btn-primary', href: 'dashboard.html' }, 'Back to dashboard')
      )
    )
  );
}

function wireToolbar() {
  const switcher = document.getElementById('view-switcher');
  if (switcher) {
    for (const button of switcher.querySelectorAll('[data-view]')) {
      button.addEventListener('click', () => {
        state.anchorKey = state.anchorKey || clinicTodayKey();
        setView(button.dataset.view);
      });
    }
  }
  document.getElementById('calendar-prev')?.addEventListener('click', () => {
    if (state.view === 'month') state.anchorKey = shiftMonthKey(state.anchorKey, -1);
    else if (state.view === 'day') state.anchorKey = addDaysKey(state.anchorKey, -1);
    else state.anchorKey = addDaysKey(state.anchorKey, -7);
    loadData();
  });
  document.getElementById('calendar-next')?.addEventListener('click', () => {
    if (state.view === 'month') state.anchorKey = shiftMonthKey(state.anchorKey, 1);
    else if (state.view === 'day') state.anchorKey = addDaysKey(state.anchorKey, 1);
    else state.anchorKey = addDaysKey(state.anchorKey, 7);
    loadData();
  });
  document.getElementById('calendar-today')?.addEventListener('click', () => {
    state.anchorKey = clinicTodayKey();
    loadData();
  });
  const modeSwitcher = document.getElementById('appointments-mode-switcher');
  if (modeSwitcher) {
    for (const button of modeSwitcher.querySelectorAll('[data-mode]')) {
      button.addEventListener('click', () => setMode(button.dataset.mode));
    }
  }
  document.getElementById('appointments-refresh')?.addEventListener('click', () => refreshCurrentView());
  document.getElementById('appointment-new')?.addEventListener('click', () => openAppointmentForm());
  document.getElementById('availability-open')?.addEventListener('click', openAvailability);
  const doctorSelect = document.getElementById('doctor-select');
  if (doctorSelect) {
    doctorSelect.addEventListener('change', async () => {
      state.doctorId = doctorSelect.value || null;
      state.history.offset = 0;
      await refreshCurrentView();
    });
  }

  const typeSelect = document.getElementById('appointments-history-type');
  if (typeSelect) {
    for (const type of VISIT_TYPES) {
      typeSelect.append(ui.el('option', { value: type.label, text: type.label }));
    }
  }
  const historyForm = document.getElementById('appointments-history-filters');
  if (historyForm) {
    historyForm.addEventListener('submit', (event) => {
      event.preventDefault();
      hideHistorySuggestions({ clearStatus: true });
      setHistoryError('');
      try {
        historyFiltersFromForm();
      } catch (error) {
        setHistoryError(error.message);
        return;
      }
      state.history.offset = 0;
      loadHistory();
    });
  }
  document.getElementById('appointments-history-reset')?.addEventListener('click', () => {
    hideHistorySuggestions({ clearStatus: true });
    for (const id of ['appointments-history-search', 'appointments-history-start', 'appointments-history-end']) {
      const node = document.getElementById(id);
      if (node) node.value = '';
    }
    for (const id of ['appointments-history-status-filter', 'appointments-history-type']) {
      const node = document.getElementById(id);
      if (node) node.value = '';
    }
    state.history.filters = { q: '', start: '', end: '', status: '', visit_type: '' };
    state.history.offset = 0;
    setHistoryError('');
    loadHistory();
  });
  document.getElementById('appointments-history-export')?.addEventListener('click', exportHistory);
}

ui.onReady(async () => {
  ui.setRetryHandler(() => window.location.reload());
  try {
    const user = await auth.initAuthChrome({ active: 'appointments' });
    if (!user) return;
    commandMenu.initCommandMenu({ user });
    prefetch.warmModules(['assets/js/patients.js', 'assets/js/patient.js']);

    if (!auth.hasPermission(user, 'appointment:read')) {
      renderPermissionDenied();
      return;
    }
    state.me = user;
    state.isAdmin = Array.isArray(user.roles) && user.roles.includes('clinical_admin');
    state.isNurse = Array.isArray(user.roles) && user.roles.includes('nurse');
    state.canWrite = auth.hasPermission(user, 'appointment:write');
    state.canManage = auth.hasPermission(user, 'appointment:manage_availability');
    state.canExport = auth.hasPermission(user, 'appointment:export');
    state.anchorKey = clinicTodayKey();

    wireToolbar();
    wireHistoryPatientSuggestions();
    if (state.canWrite) initVoice();
    const voiceRegion = document.getElementById('voice-region');
    if (voiceRegion) voiceRegion.hidden = !state.canWrite;
    const newButton = document.getElementById('appointment-new');
    if (newButton) newButton.hidden = !state.canWrite;
    const availabilityButton = document.getElementById('availability-open');
    if (availabilityButton) availabilityButton.hidden = !state.canManage;
    const exportButton = document.getElementById('appointments-history-export');
    if (exportButton) exportButton.hidden = !state.canExport;

    window.addEventListener('pagehide', () => {
      clearActiveExpiryCleanup();
      clearCancelledCleanup();
    });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && state.mode === 'calendar' && state.anchorKey) {
        loadData();
      }
    });

    await loadConfig();
    await loadDoctors();
    renderDoctorPicker();
    updateContext();
    await refreshAll();
  } catch (error) {
    const main = document.getElementById('main');
    if (main) {
      ui.renderErrorState(main, error, {
        describe: api.describeApiError,
        onRetry: () => window.location.reload(),
      });
    }
  }
});
