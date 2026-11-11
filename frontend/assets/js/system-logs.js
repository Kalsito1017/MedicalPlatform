// ---------------------------------------------------------------------------
// system-logs.js — Admin-only live operational/audit terminal.
//
// Historical source: GET /api/v1/admin/logs (bounded, filtered, counted).
// Live source: GET /api/v1/admin/logs/stream (SSE with cursor replay/heartbeats).
// The page is read-only: no shell, SQL console, command input or server control.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as commandMenu from './command-menu.js?v=20261109j';
import * as toast from './toast.js';
import * as ui from './ui.js';

const MAX_CLIENT_EVENTS = 500;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

function makeTimeFormat(timeZone) {
  const options = {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    fractionalSecondDigits: 3,
    hour12: false,
  };
  try {
    return new Intl.DateTimeFormat('en-GB', { ...options, timeZone });
  } catch {
    return new Intl.DateTimeFormat('en-GB', { ...options, timeZone: 'UTC' });
  }
}

const SEVERITY_LABELS = {
  info: 'INFO',
  success: 'SUCCESS',
  warning: 'WARNING',
  error: 'ERROR',
  security: 'SECURITY',
};

const state = {
  user: null,
  events: [],
  seen: new Set(),
  counts: { total: 0, info: 0, success: 0, warning: 0, error: 0, security: 0 },
  directory: new Map(),
  renderedDay: null,
  filters: null,
  lastEventId: null,
  autoScroll: true,
  paused: false,
  newCount: 0,
  stream: null,
  reconnectTimer: null,
  reconnectAttempt: 0,
  lastConnected: null,
  manualClose: false,
  timezone: 'UTC',
  timeFormat: makeTimeFormat('UTC'),
};

function count(value) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

function setStatus(message) {
  const node = document.getElementById('logs-status');
  if (node) node.textContent = message || '';
}

function setConnection(kind, text) {
  const node = document.getElementById('logs-connection');
  if (!node) return;
  node.className = `logs-connection logs-connection--${kind}`;
  node.textContent = text;
}

function formatLogTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '--:--:--.---' : state.timeFormat.format(date);
}

function setTimezone(timeZone) {
  if (!timeZone || typeof timeZone !== 'string') return;
  state.timezone = timeZone;
  state.timeFormat = makeTimeFormat(timeZone);
  const label = document.getElementById('logs-timezone');
  if (label) label.textContent = timeZone;
}

function fullTimestamp(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const local = new Intl.DateTimeFormat('en-GB', {
    timeZone: state.timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZoneName: 'short',
  }).format(date);
  return `${local} · ${date.toISOString().replace('T', ' ').replace('Z', ' UTC')}`;
}

function updateLastEvent(event) {
  const node = document.getElementById('logs-last-event');
  if (node) node.textContent = `Last event: ${formatLogTime(event.timestamp)} ${state.timezone}`;
}

function renderCounts() {
  for (const key of ['total', 'info', 'success', 'warning', 'error', 'security']) {
    const node = document.getElementById(`logs-stat-${key}`);
    if (node) node.textContent = String(count(state.counts[key]));
  }
}

function incrementCounts(event) {
  state.counts.total = count(state.counts.total) + 1;
  if (Object.prototype.hasOwnProperty.call(state.counts, event.severity)) {
    state.counts[event.severity] = count(state.counts[event.severity]) + 1;
  }
  renderCounts();
}

function safeMetadataText(metadata) {
  if (!metadata || typeof metadata !== 'object') return '';
  const parts = [];
  for (const [key, value] of Object.entries(metadata)) {
    if (value === null || value === undefined) continue;
    parts.push(`${key}=${String(value)}`);
  }
  return parts.join(' ');
}

function actorInfo(event) {
  const user = event.actor_id ? state.directory.get(String(event.actor_id)) : null;
  if (user) {
    const roles = Array.isArray(user.roles) ? user.roles : [];
    const primary = roles.find((role) => ['clinical_admin', 'doctor', 'nurse'].includes(role)) || roles[0];
    return {
      name: user.display_name || 'Staff member',
      role: primary ? ui.roleLabel(primary) : '',
      user,
      system: false,
    };
  }
  if (event.actor_id) {
    return { name: 'Staff member', role: '', user: null, system: false };
  }
  return { name: 'MediCore System', role: 'System', user: null, system: true };
}

function shortRef(value, max = 20) {
  const text = String(value == null ? '' : value);
  return text.length > max ? `${text.slice(0, 8)}…${text.slice(-4)}` : text;
}

function actorAvatar(event) {
  const info = actorInfo(event);
  if (info.system) {
    return ui.el('span', { class: 'chat-avatar chat-avatar--system', 'aria-hidden': 'true' },
      ui.icon('terminal'),
    );
  }
  if (info.user) return ui.avatarForUser(info.user, { size: 'sm' });
  return ui.avatar(info.name, { size: 'sm' });
}

function eventText(event) {
  const bits = [
    `[${formatLogTime(event.timestamp)}]`,
    `[${SEVERITY_LABELS[event.severity] || String(event.severity).toUpperCase()}]`,
    String(event.event_type),
    String(event.description || ''),
  ];
  if (event.actor_id) {
    const info = actorInfo(event);
    bits.push(`actor=${info.name} (${event.actor_id})`);
  }
  if (event.request_id) bits.push(`request_id=${event.request_id}`);
  if (event.resource_type) bits.push(`resource_type=${event.resource_type}`);
  if (event.resource_reference) bits.push(`resource=${event.resource_reference}`);
  if (event.outcome) bits.push(`outcome=${event.outcome}`);
  const metadata = safeMetadataText(event.metadata);
  if (metadata) bits.push(metadata);
  return bits.join(' ');
}

function chatChips(event) {
  const chips = [
    ui.el('span', { class: 'chat-chip chat-chip--category', text: `#${event.category}` }),
  ];
  if (event.resource_type) {
    chips.push(ui.el('span', { class: 'chat-chip', text: event.resource_type }));
  }
  if (event.outcome) {
    chips.push(ui.el('span', { class: `chat-chip chat-chip--${event.outcome}`, text: event.outcome }));
  }
  if (event.request_id) {
    chips.push(ui.el('span', {
      class: 'chat-chip chat-chip--request',
      title: `Request ${event.request_id}`,
      text: `req ${shortRef(event.request_id)}`,
    }));
  }
  const metadata = safeMetadataText(event.metadata);
  if (metadata) {
    chips.push(ui.el('span', {
      class: 'chat-chip chat-chip--meta',
      title: metadata,
      text: shortRef(metadata, 26),
    }));
  }
  return chips;
}

function chatMessageNode(event) {
  const info = actorInfo(event);
  if (info.system) {
    const iconName = event.severity === 'security'
      ? 'shield'
      : event.severity === 'error'
        ? 'alert'
        : event.severity === 'warning'
          ? 'alert'
          : 'info';
    return ui.el('div', { class: 'chat-notice-wrap' },
      ui.el('button', {
        type: 'button',
        class: `chat-notice chat-notice--${event.severity}`,
        'aria-label': eventText(event),
        onClick: () => openEventDetails(event),
      },
        ui.el('span', { class: 'chat-notice__icon', 'aria-hidden': 'true' }, ui.icon(iconName)),
        ui.el('span', { class: 'chat-notice__body' },
          ui.el('span', { class: 'chat-notice__event', text: String(event.event_type) }),
          ui.el('span', { class: 'chat-notice__text', text: String(event.description || '') }),
          ui.el('span', { class: 'chat-notice__time', text: `${formatLogTime(event.timestamp)} ${state.timezone}` }),
        ),
      ),
    );
  }

  const bubble = ui.el('button', {
    type: 'button',
    class: `chat-bubble chat-bubble--${event.severity}`,
    'aria-label': eventText(event),
    onClick: () => openEventDetails(event),
  },
    ui.el('span', { class: 'chat-bubble__top' },
      ui.el('span', { class: 'chat-bubble__event', text: String(event.event_type) }),
      ui.el('span', {
        class: `chat-badge chat-badge--${event.severity}`,
        text: SEVERITY_LABELS[event.severity] || String(event.severity).toUpperCase(),
      }),
    ),
    ui.el('span', { class: 'chat-bubble__text', text: String(event.description || '') }),
    ui.el('span', { class: 'chat-bubble__meta' }, chatChips(event)),
    ui.el('span', { class: 'chat-bubble__foot' },
      ui.el('span', { class: 'chat-bubble__time', text: formatLogTime(event.timestamp) }),
      ui.el('span', { class: 'chat-bubble__tz', text: state.timezone }),
    ),
  );

  return ui.el('article', { class: `chat-message chat-message--${event.severity}`, 'data-event-id': event.id },
    ui.el('div', { class: 'chat-message__avatar' }, actorAvatar(event)),
    ui.el('div', { class: 'chat-message__content' },
      ui.el('div', { class: 'chat-message__sender' },
        ui.el('span', { class: 'chat-message__name', text: info.name }),
        info.role ? ui.el('span', { class: 'chat-message__role', text: info.role }) : null,
      ),
      bubble,
    ),
  );
}

function dayKey(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'invalid';
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: state.timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function dayLabel(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Unknown date';
  const key = dayKey(value);
  const now = new Date();
  if (key === dayKey(now.toISOString())) return 'Today';
  if (key === dayKey(new Date(now.getTime() - 86400000).toISOString())) return 'Yesterday';
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: state.timezone,
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(date);
}

function appendDaySeparator(feed, event) {
  const key = dayKey(event.timestamp);
  if (state.renderedDay === key) return;
  state.renderedDay = key;
  feed.append(ui.el('div', { class: 'chat-day' },
    ui.el('span', { class: 'chat-day__label', text: dayLabel(event.timestamp) }),
  ));
}

function emptyChatNode() {
  return ui.el('div', { class: 'chat-empty', id: 'logs-empty' },
    ui.el('span', { class: 'chat-empty__icon', 'aria-hidden': 'true' }, ui.icon('mail')),
    ui.el('p', { class: 'chat-empty__title', text: 'No activity yet' }),
    ui.el('p', { class: 'chat-empty__text', text: 'Waiting for incoming events…' }),
  );
}

function openEventDetails(event) {
  const info = actorInfo(event);
  const rows = [
    ['From', info.name],
    ['Role', info.role || 'Not recorded'],
    ['Full timestamp', fullTimestamp(event.timestamp)],
    ['Severity', SEVERITY_LABELS[event.severity] || event.severity],
    ['Category', event.category],
    ['Event', event.event_type],
    ['Description', event.description],
    ['Actor ID', event.actor_id || 'Not applicable'],
    ['Resource type', event.resource_type || 'Not applicable'],
    ['Resource reference', event.resource_reference || 'Not applicable'],
    ['Request ID', event.request_id || 'Not applicable'],
    ['Outcome', event.outcome || 'Not applicable'],
  ];
  const metadata = event.metadata && typeof event.metadata === 'object' ? event.metadata : {};
  for (const [key, value] of Object.entries(metadata)) {
    rows.push([`Metadata · ${key}`, value == null ? '' : String(value)]);
  }
  const list = ui.el('dl', { class: 'logs-detail' },
    rows.flatMap(([label, value]) => [
      ui.el('dt', { text: label }),
      ui.el('dd', { text: value == null ? '' : String(value) }),
    ]),
  );
  const copy = ui.el('button', {
    class: 'btn btn-secondary',
    type: 'button',
    onClick: () => copyText(eventText(event)),
  }, 'Copy message');
  const close = ui.el('button', {
    class: 'btn btn-primary',
    type: 'button',
    onClick: () => handler.close(),
  }, 'Close');
  const handler = ui.openDialog({
    title: 'Message details',
    icon: 'mail',
    bodyNodes: [list],
    actionNodes: [copy, close],
  });
}

function showNewIndicator() {
  const node = document.getElementById('logs-new-indicator');
  if (!node) return;
  node.hidden = state.newCount === 0;
  node.textContent = state.newCount > 0 ? `${state.newCount} new message${state.newCount === 1 ? '' : 's'} — Resume` : '';
}

function scrollToBottom() {
  const terminal = document.getElementById('logs-terminal');
  if (terminal) terminal.scrollTop = terminal.scrollHeight;
}

function renderEvents() {
  const feed = document.getElementById('logs-terminal');
  if (!feed) return;
  state.renderedDay = null;
  ui.clear(feed);
  if (state.events.length === 0) {
    feed.append(emptyChatNode());
  } else {
    for (const event of state.events) {
      appendDaySeparator(feed, event);
      feed.append(chatMessageNode(event));
    }
  }
  if (state.autoScroll && !state.paused) scrollToBottom();
}

function compareEvents(left, right) {
  if (left.timestamp !== right.timestamp) return left.timestamp < right.timestamp ? -1 : 1;
  return String(left.id).localeCompare(String(right.id));
}

function insertionIndex(event) {
  let low = 0;
  let high = state.events.length;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (compareEvents(state.events[mid], event) <= 0) low = mid + 1;
    else high = mid;
  }
  return low;
}

function trimChatDom(feed) {
  const children = Array.from(feed.children);
  const index = children.findIndex((node) => node.classList && (
    node.classList.contains('chat-message') || node.classList.contains('chat-notice-wrap')
  ));
  if (index >= 0) feed.removeChild(children[index]);
  const remaining = Array.from(feed.children);
  if (
    remaining[0]?.classList?.contains('chat-day')
    && remaining[1]?.classList?.contains('chat-day')
  ) {
    feed.removeChild(remaining[0]);
  }
}

function addEvent(event, { historical = false } = {}) {
  if (!event || !event.id || state.seen.has(event.id)) return;
  state.seen.add(event.id);
  const index = insertionIndex(event);
  state.events.splice(index, 0, event);
  const feed = document.getElementById('logs-terminal');
  if (!feed) return;
  const empty = document.getElementById('logs-empty');
  if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
  // Live events almost always belong at the end; an out-of-order delivery is
  // rendered from the sorted model so the conversation stays chronological.
  if (index === state.events.length - 1) {
    appendDaySeparator(feed, event);
    feed.append(chatMessageNode(event));
  } else {
    renderEvents();
  }
  if (state.events.length > MAX_CLIENT_EVENTS) {
    const removed = state.events.shift();
    if (removed) state.seen.delete(removed.id);
    trimChatDom(feed);
  }
  if (!historical) {
    incrementCounts(event);
    updateLastEvent(state.events[state.events.length - 1] || event);
    if (state.autoScroll && !state.paused) {
      scrollToBottom();
    } else {
      state.newCount += 1;
      showNewIndicator();
    }
  }
}

function closeStream() {
  state.manualClose = true;
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
  if (state.stream) {
    state.stream.close();
    state.stream = null;
  }
}

function scheduleReconnect(query) {
  if (state.reconnectTimer) return;
  state.reconnectAttempt += 1;
  const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** (state.reconnectAttempt - 1));
  setConnection('reconnecting', `Reconnecting (${Math.ceil(delay / 1000)}s)`);
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    openStream(query);
  }, delay);
}

function openStream(query) {
  if (typeof EventSource !== 'function') {
    setConnection('disconnected', 'Disconnected');
    setStatus('Live streaming is not supported by this browser. Historical logs remain available.');
    return;
  }
  closeStream();
  state.manualClose = false;
  setConnection('reconnecting', 'Connecting…');
  const streamQuery = { ...query };
  if (state.lastEventId) streamQuery.last_event_id = state.lastEventId;
  const stream = new EventSource(api.buildUrl('/admin/logs/stream', streamQuery));
  state.stream = stream;
  stream.onopen = () => {
    if (state.stream !== stream) return;
    state.reconnectAttempt = 0;
    state.lastConnected = new Date();
    setConnection('connected', 'Connected');
    const node = document.getElementById('logs-last-connected');
    if (node) node.textContent = `Last connected: ${formatLogTime(state.lastConnected.toISOString())} ${state.timezone}`;
    setStatus('Connected to the live system log stream.');
  };
  stream.addEventListener('system', (message) => {
    if (state.stream !== stream) return;
    try {
      const event = JSON.parse(message.data);
      state.lastEventId = message.lastEventId || event.id || state.lastEventId;
      addEvent(event);
    } catch {
      setStatus('A malformed live event was ignored.');
    }
  });
  stream.addEventListener('auth_expired', () => {
    if (state.stream !== stream) return;
    closeStream();
    setConnection('disconnected', 'Disconnected');
    setStatus('The session expired. Reload the page to reconnect.');
  });
  stream.onerror = () => {
    if (state.stream !== stream || state.manualClose) return;
    stream.close();
    state.stream = null;
    setConnection('reconnecting', 'Reconnecting');
    setStatus('The live stream was interrupted. Reconnecting with bounded backoff.');
    scheduleReconnect(query);
  };
}

function localInputValue(date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function showFilterError(message) {
  const node = document.getElementById('logs-filter-error');
  if (!node) return;
  node.textContent = message || '';
  node.hidden = !message;
}

function buildQuery() {
  const range = document.getElementById('logs-range')?.value || '15m';
  const end = new Date();
  let start;
  if (range === 'custom') {
    const startValue = document.getElementById('logs-start')?.value;
    const endValue = document.getElementById('logs-end')?.value;
    if (!startValue || !endValue) {
      showFilterError('Choose both a custom start and end time.');
      return null;
    }
    start = new Date(startValue);
    end.setTime(new Date(endValue).getTime());
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) {
      showFilterError('The custom log range must have an end after start.');
      return null;
    }
    if (end - start > 7 * 24 * 60 * 60 * 1000) {
      showFilterError('The log range may not exceed 7 days.');
      return null;
    }
  } else {
    const minutes = range === '15m' ? 15 : range === '1h' ? 60 : range === '24h' ? 1440 : 10080;
    start = new Date(end.getTime() - minutes * 60000);
  }
  const actor = document.getElementById('logs-actor')?.value.trim() || '';
  if (actor && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(actor)) {
    showFilterError('Actor must be a full user UUID.');
    return null;
  }
  showFilterError('');
  const query = {
    start: start.toISOString(),
    end: end.toISOString(),
  };
  for (const [key, id] of [
    ['severity', 'logs-severity'],
    ['category', 'logs-category'],
    ['event_type', 'logs-event-type'],
    ['actor_id', 'logs-actor'],
    ['request_id', 'logs-request-id'],
    ['outcome', 'logs-outcome'],
    ['q', 'logs-search'],
  ]) {
    const value = document.getElementById(id)?.value.trim();
    if (value) query[key] = value;
  }
  return query;
}

async function loadStaffDirectory() {
  try {
    const page = await api.get('/staff', { limit: 100, offset: 0 });
    const directory = new Map();
    for (const user of page.items || []) {
      if (user && user.id) directory.set(String(user.id), user);
    }
    state.directory = directory;
  } catch {
    // Identity enrichment is optional; the log remains usable with initials.
    state.directory = new Map();
  }
}

async function loadHistory(query) {
  const terminal = document.getElementById('logs-terminal');
  if (!terminal) return;
  setStatus('Loading historical events.');
  try {
    const page = await api.get('/admin/logs', { ...query, limit: 100, offset: 0 });
    if (page.timezone) setTimezone(page.timezone);
    state.events = [];
    state.seen = new Set();
    state.counts = page.counts || state.counts;
    ui.clear(terminal);
    state.renderedDay = null;
    const items = Array.isArray(page.items) ? [...page.items].reverse() : [];
    for (const event of items) addEvent(event, { historical: true });
    if (items.length === 0) renderEvents();
    state.lastEventId = Array.isArray(page.items) && page.items[0] ? page.items[0].id : state.lastEventId;
    renderCounts();
    if (items.length > 0) {
      updateLastEvent(items[items.length - 1]);
      if (state.autoScroll && !state.paused) scrollToBottom();
    }
    setStatus(`${count(page.total)} matching event${count(page.total) === 1 ? '' : 's'} in the selected period.`);
    openStream(query);
  } catch (error) {
    setConnection('disconnected', 'Disconnected');
    ui.renderErrorState(terminal, error, {
      describe: api.describeApiError,
      onRetry: () => applyFilters(),
    });
    setStatus('Historical logs could not be loaded.');
  }
}

function applyFilters() {
  const query = buildQuery();
  if (!query) return;
  state.filters = query;
  closeStream();
  state.events = [];
  state.seen = new Set();
  state.lastEventId = null;
  state.newCount = 0;
  showNewIndicator();
  loadHistory(query);
}

async function copyText(text) {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(text);
    } else {
      throw new Error('clipboard unavailable');
    }
    toast.showToast({ message: 'Copied to the clipboard.', tone: 'success' });
  } catch {
    toast.showToast({ message: 'Copy is unavailable in this browser.', tone: 'error' });
  }
}

function exportCsv() {
  const columns = ['id', 'timestamp', 'severity', 'category', 'event_type', 'description', 'actor_id', 'resource_type', 'resource_reference', 'request_id', 'outcome'];
  const escape = (value) => {
    const raw = String(value == null ? '' : value);
    // Neutralize spreadsheet formulas in exported text values.
    const safe = /^[=+\-@\t\r]/.test(raw) ? `'${raw}` : raw;
    return `"${safe.replace(/"/g, '""')}"`;
  };
  const lines = [columns.join(',')];
  for (const event of state.events) {
    lines.push(columns.map((key) => escape(key === 'metadata' ? JSON.stringify(event.metadata || {}) : event[key])).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
    toast.showToast({ message: 'Export is unavailable in this browser.', tone: 'error' });
    return;
  }
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `medicore-system-logs-${new Date().toISOString().slice(0, 10)}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
  toast.showToast({ message: 'Current filtered view exported.', tone: 'success' });
}

function wireControls() {
  const range = document.getElementById('logs-range');
  const custom = document.getElementById('logs-custom');
  if (range && custom) {
    const start = document.getElementById('logs-start');
    const end = document.getElementById('logs-end');
    if (start) start.value = localInputValue(new Date(Date.now() - 15 * 60000));
    if (end) end.value = localInputValue(new Date());
    range.addEventListener('change', () => {
      custom.hidden = range.value !== 'custom';
    });
  }
  document.getElementById('logs-filter-form')?.addEventListener('submit', (event) => {
    event.preventDefault();
    applyFilters();
  });
  document.getElementById('logs-reset')?.addEventListener('click', () => {
    for (const id of ['logs-severity', 'logs-category', 'logs-actor', 'logs-event-type', 'logs-request-id', 'logs-outcome', 'logs-search']) {
      const node = document.getElementById(id);
      if (node) node.value = '';
    }
    if (range) {
      range.value = '15m';
      custom.hidden = true;
    }
    applyFilters();
  });
  const pause = document.getElementById('logs-pause');
  function updatePause() {
    if (!pause) return;
    pause.textContent = state.paused ? 'Resume' : 'Pause';
    pause.setAttribute('aria-pressed', state.paused ? 'true' : 'false');
    if (!state.paused) {
      state.newCount = 0;
      showNewIndicator();
      scrollToBottom();
    }
  }
  pause?.addEventListener('click', () => {
    state.paused = !state.paused;
    updatePause();
  });
  const auto = document.getElementById('logs-autoscroll');
  function updateAuto() {
    if (!auto) return;
    auto.textContent = `Auto-scroll: ${state.autoScroll ? 'on' : 'off'}`;
    auto.setAttribute('aria-pressed', state.autoScroll ? 'true' : 'false');
  }
  auto?.addEventListener('click', () => {
    state.autoScroll = !state.autoScroll;
    updateAuto();
    if (state.autoScroll) {
      state.newCount = 0;
      showNewIndicator();
      scrollToBottom();
    }
  });
  const terminal = document.getElementById('logs-terminal');
  terminal?.addEventListener('scroll', () => {
    if (state.paused) return;
    const atBottom = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 12;
    state.autoScroll = atBottom;
    updateAuto();
  });
  document.getElementById('logs-new-indicator')?.addEventListener('click', () => {
    state.paused = false;
    state.autoScroll = true;
    state.newCount = 0;
    updatePause();
    updateAuto();
    showNewIndicator();
    scrollToBottom();
  });
  document.getElementById('logs-clear')?.addEventListener('click', () => {
    state.events = [];
    state.seen = new Set();
    state.newCount = 0;
    showNewIndicator();
    renderEvents();
    setStatus('View cleared. Persisted backend events were not deleted.');
  });
  document.getElementById('logs-copy-visible')?.addEventListener('click', () => {
    copyText(state.events.map(eventText).join('\n'));
  });
  document.getElementById('logs-export')?.addEventListener('click', exportCsv);
  updatePause();
  updateAuto();
}

function renderPermissionDenied() {
  const body = ui.clear(document.getElementById('page-body'));
  body.append(
    ui.el('div', { class: 'state-panel state-panel--error', role: 'alert' },
      ui.el('h2', { class: 'state-panel__title', text: 'You do not have permission' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: 'System Logs are limited to accounts with the logs:read permission. The backend enforces this on every request and stream connection.',
      }),
      ui.el('div', { class: 'state-panel__actions' },
        ui.el('a', { class: 'btn btn-primary', href: 'dashboard.html' }, 'Back to dashboard'),
      ),
    ),
  );
}

ui.onReady(async () => {
  ui.setRetryHandler(() => window.location.reload());
  let user;
  try {
    user = await auth.initAuthChrome({ active: 'logs' });
    if (!user) return;
  } catch (error) {
    ui.renderErrorState(document.getElementById('page-body'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
    return;
  }
  state.user = user;
  commandMenu.initCommandMenu({ user });
  if (!auth.hasPermission(user, 'logs:read')) {
    renderPermissionDenied();
    return;
  }
  wireControls();
  await loadStaffDirectory();
  const loading = document.getElementById('logs-loading');
  if (loading) loading.hidden = true;
  applyFilters();
  window.addEventListener('beforeunload', () => {
    closeStream();
  });
});
