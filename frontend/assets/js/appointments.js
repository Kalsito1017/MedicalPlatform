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
import * as overlayHistory from './overlay-history.js';
import * as prefetch from './prefetch.js';
import * as toast from './toast.js';
import * as ui from './ui.js';
import { buildPatientCreateForm, validatePatientInput } from './patient-create.js';
import { VISIT_TYPES, findVisitType } from './visit-types.js';

const CLINIC_TZ = 'Europe/Sofia';
const CANCELLED_CALENDAR_VISIBLE_MS = 5 * 60 * 1000;
const VOICE_MAX_SECONDS = 60;
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
};

const state = {
  me: null,
  timezone: CLINIC_TZ,
  isAdmin: false,
  isNurse: false,
  canWrite: false,
  canManage: false,
  doctors: [],
  doctorId: null,
  view: 'month',
  anchorKey: '',
  appointments: [],
  cancelledTimer: null,
  slots: null,
  availability: null,
  loading: false,
  voice: {
    recorder: null,
    stream: null,
    chunks: [],
    blob: null,
    startedAt: 0,
    timer: null,
    session: 0,
    status: '',
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

function formatDay(key, options = {}) {
  return new Intl.DateTimeFormat('en-GB', {
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
  const startText = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    day: 'numeric',
    month: sameMonth ? undefined : 'short',
  }).format(start);
  const endText = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(end);
  return `${startText} – ${endText}`;
}

const timeFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: CLINIC_TZ,
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

const dateTimeFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: CLINIC_TZ,
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

const longDateFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: CLINIC_TZ,
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  year: 'numeric',
});

function formatClinicTime(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : timeFormatter.format(date);
}

function formatClinicDateTime(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : dateTimeFormatter.format(date);
}

function formatClinicLongDate(key) {
  return longDateFormatter.format(dateKeyObject(key));
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
    state.appointments = pruneExpiredCancelled(appointments.items);
    if (appointments.total > appointments.items.length) {
      setStatus(`Showing the first ${appointments.items.length} of ${appointments.total} appointments in this range.`);
    }
    renderCalendar();
    updateContext();
    scheduleCancelledCleanup();
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
    ['Duration', `${appointment.duration_minutes} minutes`],
    ['Type', appointment.appointment_type || 'Not recorded'],
    ['Status', statusBadge(appointment.status)],
    ['Notes', appointment.notes || 'None'],
  ];
  const list = ui.el('dl', { class: 'detail-grid' });
  for (const [label, value] of rows) {
    list.append(ui.el('dt', { text: label }));
    list.append(ui.el('dd', {}, value && value.nodeType ? value : String(value)));
  }
  detail.append(list);

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
        class: 'btn btn-secondary',
        type: 'button',
        onClick: () => closeThen(() => openAppointmentForm({ appointment })),
      }, 'Reschedule')
    );
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

function setVoiceControls() {
  const record = document.getElementById('voice-record');
  const transcribe = document.getElementById('voice-transcribe');
  const clear = document.getElementById('voice-clear');
  const interpret = document.getElementById('voice-interpret');
  const recording = Boolean(state.voice.recorder && state.voice.recorder.state === 'recording');
  if (record) {
    const label = document.getElementById('voice-record-label');
    if (label) label.textContent = recording ? 'Stop recording' : 'Start recording';
    record.classList.toggle('btn-danger', recording);
    record.classList.toggle('btn-accent', !recording);
  }
  if (transcribe) transcribe.hidden = !state.voice.blob || recording;
  if (clear) clear.hidden = !state.voice.blob && !document.getElementById('voice-transcript')?.value.trim();
  if (interpret) {
    const text = document.getElementById('voice-transcript')?.value.trim() || '';
    interpret.disabled = text.length < 3;
  }
}

function stopVoiceStream() {
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
  stopVoiceTimer();
  stopVoiceStream();
  state.voice.recorder = null;
  state.voice.chunks = [];
  state.voice.blob = null;
  state.voice.startedAt = 0;
  const timer = document.getElementById('voice-timer');
  if (timer) timer.textContent = 'Ready';
  const transcribe = document.getElementById('voice-transcribe');
  if (transcribe) transcribe.hidden = true;
  const clear = document.getElementById('voice-clear');
  if (clear) clear.hidden = true;
  const proposal = document.getElementById('voice-proposal');
  if (proposal) {
    proposal.hidden = true;
    ui.clear(proposal);
  }
  setVoiceControls();
}

async function startRecording() {
  if (state.voice.recorder && state.voice.recorder.state === 'recording') {
    stopRecording();
    return;
  }
  if (
    typeof navigator === 'undefined' ||
    !navigator.mediaDevices ||
    typeof navigator.mediaDevices.getUserMedia !== 'function' ||
    typeof window === 'undefined' ||
    typeof window.MediaRecorder === 'undefined'
  ) {
    voiceStatus('Recording is not supported in this browser. Type the request instead.', 'error');
    return;
  }
  resetVoice();
  voiceStatus('Requesting microphone access…');
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    const mimeType = chooseMimeType();
    const recorder = mimeType ? new window.MediaRecorder(stream, { mimeType }) : new window.MediaRecorder(stream);
    const session = state.voice.session;
    state.voice.stream = stream;
    state.voice.recorder = recorder;
    state.voice.chunks = [];
    state.voice.startedAt = Date.now();
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
      voiceStatus('Recording failed. Try again or type the request below.', 'error');
      setVoiceControls();
    });
    recorder.addEventListener('stop', () => {
      if (state.voice.session !== session) return;
      stopVoiceTimer();
      stopVoiceStream();
      const elapsed = Math.max(1, Math.round((Date.now() - state.voice.startedAt) / 1000));
      if (state.voice.chunks.length === 0) {
        voiceStatus('No audio was captured. Try again or type the request.', 'warn');
        state.voice.blob = null;
      } else {
        state.voice.blob = new Blob(state.voice.chunks, { type: recorder.mimeType || mimeType || 'audio/webm' });
        voiceStatus(`Recording ready (${elapsed}s). Transcribe it or type below.`, 'success');
      }
      state.voice.recorder = null;
      const timer = document.getElementById('voice-timer');
      if (timer) timer.textContent = state.voice.blob ? `${elapsed}s captured` : 'Ready';
      setVoiceControls();
    });
    recorder.start(1000);
    const timer = document.getElementById('voice-timer');
    state.voice.timer = setInterval(() => {
      const elapsed = Math.floor((Date.now() - state.voice.startedAt) / 1000);
      if (timer) timer.textContent = `${elapsed}s / ${VOICE_MAX_SECONDS}s`;
      if (elapsed >= VOICE_MAX_SECONDS) stopRecording();
    }, 250);
    voiceStatus('Recording… speak clearly, then stop.');
    setVoiceControls();
  } catch (error) {
    stopVoiceStream();
    state.voice.recorder = null;
    const name = error && error.name ? error.name : '';
    voiceStatus(
      name === 'NotAllowedError' || name === 'SecurityError'
        ? 'Microphone permission was denied. Type the request instead.'
        : 'The microphone could not be started. Type the request instead.',
      'error'
    );
    setVoiceControls();
  }
}

function stopRecording() {
  if (state.voice.recorder && state.voice.recorder.state === 'recording') {
    state.voice.recorder.stop();
  }
  stopVoiceStream();
  stopVoiceTimer();
}

async function transcribeVoice() {
  if (!state.voice.blob) {
    voiceStatus('Record audio first, or type the request below.', 'warn');
    return;
  }
  const button = document.getElementById('voice-transcribe');
  const language = document.getElementById('voice-language')?.value || 'bg';
  if (button) ui.setBusy(button, true, 'Transcribing…');
  voiceStatus('Uploading and transcribing with the private speech service…');
  try {
    const form = new FormData();
    form.append('file', state.voice.blob, 'appointment-recording.webm');
    form.append('language', language);
    const result = await api.postForm('/voice/transcriptions', form);
    const transcript = document.getElementById('voice-transcript');
    if (transcript) {
      transcript.value = result.text || '';
      transcript.focus();
    }
    if (result.text) {
      voiceStatus(
        `Transcript ready (${result.duration_seconds}s of audio). Edit it if needed, then propose an appointment.`,
        'success'
      );
    } else {
      voiceStatus('No speech was detected. Type the request below.', 'warn');
    }
  } catch (error) {
    const described = api.describeApiError(error);
    const retry = error && error.retryAfter ? ` Retry after ${error.retryAfter}s.` : '';
    voiceStatus(`${described.message}${retry}`, 'error');
  } finally {
    if (button) ui.setBusy(button, false);
    setVoiceControls();
  }
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
  const transcribe = document.getElementById('voice-transcribe');
  const clear = document.getElementById('voice-clear');
  const interpret = document.getElementById('voice-interpret');
  const transcript = document.getElementById('voice-transcript');
  if (record) record.addEventListener('click', startRecording);
  if (transcribe) transcribe.addEventListener('click', transcribeVoice);
  if (clear) {
    clear.addEventListener('click', () => {
      stopRecording();
      resetVoice();
      if (transcript) transcript.value = '';
      voiceStatus('');
      setVoiceControls();
    });
  }
  if (interpret) interpret.addEventListener('click', interpretVoice);
  if (transcript) {
    transcript.addEventListener('input', setVoiceControls);
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('pagehide', () => {
      stopRecording();
      resetVoice();
      clearCancelledCleanup();
    });
  }
  setVoiceControls();
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
  document.getElementById('appointments-refresh')?.addEventListener('click', () => refreshAll());
  document.getElementById('appointment-new')?.addEventListener('click', () => openAppointmentForm());
  document.getElementById('availability-open')?.addEventListener('click', openAvailability);
  const doctorSelect = document.getElementById('doctor-select');
  if (doctorSelect) {
    doctorSelect.addEventListener('change', async () => {
      state.doctorId = doctorSelect.value || null;
      await refreshAll();
    });
  }
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
    state.anchorKey = clinicTodayKey();

    wireToolbar();
    if (state.canWrite) initVoice();
    const voiceRegion = document.getElementById('voice-region');
    if (voiceRegion) voiceRegion.hidden = !state.canWrite;
    const newButton = document.getElementById('appointment-new');
    if (newButton) newButton.hidden = !state.canWrite;
    const availabilityButton = document.getElementById('availability-open');
    if (availabilityButton) availabilityButton.hidden = !state.canManage;

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
