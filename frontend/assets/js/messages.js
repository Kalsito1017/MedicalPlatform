// ---------------------------------------------------------------------------
// messages.html page module — internal staff-to-staff messages.
//
// Endpoints: GET /messages?box=inbox|sent&q=, GET /messages/counts,
// GET /messages/recipients, POST /messages, POST /messages/{id}/read.
// Only the caller's own inbox/sent items are returned; the backend re-checks
// message:read/message:write. Message text is rendered as text only.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as commandMenu from './command-menu.js?v=20261109j';
import * as toast from './toast.js';
import * as ui from './ui.js';

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 250;

const state = {
  user: null,
  box: 'inbox',
  messages: [],
  selectedId: null,
  recipients: null,
  counts: { inbox_total: 0, unread_total: 0, sent_total: 0 },
  q: '',
  loading: false,
  sending: false,
  searchTimer: null,
};

function setStatus(message) {
  const node = document.getElementById('messages-status');
  if (node) node.textContent = message || '';
}

function setTabs() {
  for (const [box, id] of [['inbox', 'messages-inbox-tab'], ['sent', 'messages-sent-tab']]) {
    const tab = document.getElementById(id);
    if (!tab) continue;
    const active = state.box === box;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  }
  const inboxCount = document.getElementById('messages-inbox-count');
  if (inboxCount) {
    inboxCount.textContent = state.counts.unread_total > 0
      ? String(state.counts.unread_total)
      : '';
  }
  const sentCount = document.getElementById('messages-sent-count');
  if (sentCount) {
    sentCount.textContent = state.counts.sent_total > 0
      ? String(state.counts.sent_total)
      : '';
  }
}

function updateCounts() {
  const count = document.getElementById('messages-count');
  const unread = document.getElementById('messages-unread');
  if (count) {
    count.textContent = `${state.counts.inbox_total} message${
      state.counts.inbox_total === 1 ? '' : 's'
    } in your inbox`;
  }
  if (unread) {
    unread.textContent = `${state.counts.unread_total} unread`;
  }
  setTabs();
}

function messageCounterpart(message) {
  return state.box === 'inbox'
    ? message.sender_display_name || 'Unknown sender'
    : message.recipient_display_name || 'Unknown recipient';
}

function fullMessageTime(value) {
  return ui.formatDateTime(value) || 'Unknown time';
}

function relativeMessageTime(value) {
  const timestamp = Date.parse(String(value || ''));
  if (!Number.isFinite(timestamp)) return 'Unknown time';
  const elapsed = Date.now() - timestamp;
  if (elapsed < 0) return fullMessageTime(value);
  if (elapsed < 60_000) return 'Just now';
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}m ago`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)}h ago`;
  return fullMessageTime(value);
}

function messagePreview(body) {
  return String(body || '').replace(/\s+/g, ' ').trim();
}

function messageEmptyState(region) {
  if (state.q) {
    ui.renderEmptyState(region, {
      title: 'No messages match your search.',
      message: 'Try a different sender, subject or phrase.',
      headingTag: 'h3',
    });
    return;
  }
  ui.renderEmptyState(region, {
    title: state.box === 'inbox' ? 'Your inbox is empty' : 'No sent messages',
    message: state.box === 'inbox'
      ? 'Messages from other staff will appear here.'
      : 'Messages you send will appear here.',
    headingTag: 'h3',
  });
}

function renderMessageRows() {
  const region = document.getElementById('messages-region');
  if (!region) return;
  ui.clear(region);
  if (state.messages.length === 0) {
    messageEmptyState(region);
    return;
  }

  const list = ui.el('div', { class: 'message-list' });
  for (const message of state.messages) {
    const unread = state.box === 'inbox' && !message.read_at;
    const counterpart = messageCounterpart(message);
    const subject = message.subject?.trim() || '(No subject)';
    const time = message.created_at;
    const row = ui.el('button', {
      type: 'button',
      class: `message-row${unread ? ' message-row--unread' : ''}${
        state.selectedId === message.id ? ' is-active' : ''
      }`,
      'aria-current': state.selectedId === message.id ? 'true' : null,
      'aria-label': `${unread ? 'Unread message. ' : ''}${counterpart}: ${subject}`,
      onClick: () => selectMessage(message),
    },
      ui.el('span', { class: 'message-row__avatar' }, ui.avatar(counterpart, { size: 'sm' })),
      ui.el('span', { class: 'message-row__content' },
        ui.el('span', { class: 'message-row__top' },
          ui.el('span', { class: 'message-row__person', text: counterpart }),
          ui.el('time', {
            class: 'message-row__time',
            datetime: time || null,
            title: fullMessageTime(time),
            text: relativeMessageTime(time),
          }),
        ),
        ui.el('span', { class: 'message-row__subject', text: subject }),
        ui.el('span', { class: 'message-row__preview', text: messagePreview(message.body) }),
      ),
      unread
        ? ui.el('span', { class: 'message-row__unread', 'aria-hidden': 'true' })
        : null,
      unread ? ui.el('span', { class: 'visually-hidden', text: 'Unread' }) : null,
    );
    list.append(row);
  }
  region.append(list);
}

function emptyDetailNode() {
  return ui.el('div', { class: 'messages-detail__empty' },
    ui.el('span', { class: 'messages-detail__empty-icon', 'aria-hidden': 'true' }, ui.icon('mail')),
    ui.el('h2', { class: 'messages-detail__empty-title', text: 'Select a message to read it' }),
    ui.el('p', { class: 'messages-detail__empty-text', text: 'Choose a message from the list on the left.' }),
  );
}

function renderDetail(message) {
  const region = document.getElementById('messages-detail');
  if (!region) return;
  ui.clear(region);
  if (!message) {
    region.append(emptyDetailNode());
    return;
  }

  const sender = message.sender_display_name || 'Unknown sender';
  const recipient = message.recipient_display_name || 'Unknown recipient';
  const subject = message.subject?.trim() || '(No subject)';
  const isSent = state.box === 'sent';
  const timestamp = message.created_at;
  const readState = message.read_at ? 'Read' : 'Unread';

  const back = ui.el('button', {
    class: 'link-button message-detail__back',
    type: 'button',
    onClick: () => closeDetail(),
  }, '← Back to inbox');

  const reply = ui.el('button', {
    class: 'btn btn-primary btn-sm',
    type: 'button',
    onClick: () => openCompose({
      recipientId: isSent ? message.recipient_id : message.sender_id,
      recipientName: isSent ? recipient : sender,
      subject: message.subject ? `Re: ${message.subject}` : '',
    }),
  }, 'Reply');

  const newMessage = ui.el('button', {
    class: 'btn btn-secondary btn-sm',
    type: 'button',
    onClick: () => openCompose(),
  }, 'New message');

  region.append(
    back,
    ui.el('header', { class: 'message-detail__header' },
      ui.el('div', { class: 'message-detail__avatar' }, ui.avatar(sender, { size: 'lg' })),
      ui.el('div', { class: 'message-detail__identity' },
        ui.el('p', { class: 'message-detail__sender', text: sender }),
        ui.el('p', { class: 'message-detail__recipient', text: `To: ${recipient}` }),
      ),
      ui.el('div', { class: 'message-detail__stamp' },
        ui.el('time', {
          datetime: timestamp || null,
          title: fullMessageTime(timestamp),
          text: fullMessageTime(timestamp),
        }),
        ui.el('span', {
          class: `badge ${message.read_at ? 'badge--neutral' : 'badge--info'}`,
          text: readState,
        }),
      ),
    ),
    ui.el('h2', { class: 'message-detail__subject', text: subject }),
    ui.el('div', { class: 'message-detail__body', text: message.body }),
    ui.el('footer', { class: 'message-detail__actions' }, reply, newMessage),
  );
}

function openDetailOnMobile() {
  document.getElementById('messages-workspace')?.classList.add('is-reading');
}

function closeDetail() {
  state.selectedId = null;
  document.getElementById('messages-workspace')?.classList.remove('is-reading');
  renderDetail(null);
  renderMessageRows();
}

async function selectMessage(message) {
  state.selectedId = message.id;
  openDetailOnMobile();
  renderDetail(message);
  renderMessageRows();
  if (state.box !== 'inbox' || message.read_at) return;
  try {
    await api.post(`/messages/${encodeURIComponent(message.id)}/read`, {});
    message.read_at = new Date().toISOString();
    renderDetail(message);
    renderMessageRows();
    await loadCounts();
  } catch (error) {
    toast.showToast({ message: api.describeApiError(error).message, tone: 'error' });
  }
}

async function loadCounts() {
  try {
    const counts = await api.get('/messages/counts');
    state.counts = {
      inbox_total: Number(counts.inbox_total) || 0,
      unread_total: Number(counts.unread_total) || 0,
      sent_total: Number(counts.sent_total) || 0,
    };
    updateCounts();
  } catch {
    const count = document.getElementById('messages-count');
    const unread = document.getElementById('messages-unread');
    if (count) count.textContent = 'Message count unavailable';
    if (unread) unread.textContent = '';
  }
}

async function loadMessages() {
  if (state.loading) return;
  state.loading = true;
  setStatus('Loading messages.');
  setTabs();
  const region = document.getElementById('messages-region');
  ui.renderLoading(region, {
    label: state.box === 'inbox' ? 'Loading inbox' : 'Loading sent messages',
  });
  try {
    const query = { box: state.box, limit: PAGE_SIZE, offset: 0 };
    if (state.q) query.q = state.q;
    const page = await api.get('/messages', query);
    state.messages = Array.isArray(page.items) ? page.items : [];
    const selected = state.messages.find((message) => message.id === state.selectedId) || null;
    if (!selected) {
      state.selectedId = null;
      document.getElementById('messages-workspace')?.classList.remove('is-reading');
    }
    renderMessageRows();
    renderDetail(selected);
    const total = Number.isFinite(page.total) ? Number(page.total) : state.messages.length;
    if (total === 0) {
      setStatus(state.q ? 'No messages match your search.' : (
        state.box === 'inbox' ? 'Inbox is empty.' : 'No sent messages.'
      ));
    } else {
      setStatus(`${total} message${total === 1 ? '' : 's'} found.`);
    }
  } catch (error) {
    ui.renderErrorState(region, error, {
      describe: api.describeApiError,
      onRetry: loadMessages,
    });
    setStatus('Messages could not be loaded.');
  } finally {
    state.loading = false;
  }
}

function setBox(box) {
  if (state.box === box) return;
  state.box = box;
  state.selectedId = null;
  document.getElementById('messages-workspace')?.classList.remove('is-reading');
  setTabs();
  loadMessages();
}

function wireSearch() {
  const form = document.getElementById('messages-search-form');
  const input = document.getElementById('messages-search');
  if (!form || !input) return;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    state.q = input.value.trim();
    state.selectedId = null;
    loadMessages();
  });
  input.addEventListener('input', () => {
    if (state.searchTimer) clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => {
      state.searchTimer = null;
      state.q = input.value.trim();
      state.selectedId = null;
      loadMessages();
    }, SEARCH_DEBOUNCE_MS);
  });
}

async function ensureRecipients() {
  if (Array.isArray(state.recipients)) return state.recipients;
  const response = await api.get('/messages/recipients');
  state.recipients = Array.isArray(response.items) ? response.items : [];
  return state.recipients;
}

function recipientLabel(person) {
  const roles = Array.isArray(person.roles) ? person.roles.map(ui.roleLabel).filter(Boolean) : [];
  return roles.length > 0 ? `${person.display_name} · ${roles.join(', ')}` : person.display_name;
}

function openCompose({ recipientId = '', recipientName = '', subject = '' } = {}) {
  if (!state.user || !auth.hasPermission(state.user, 'message:write')) return;
  ensureRecipients().then((recipients) => {
    if (recipients.length === 0) {
      toast.showToast({ message: 'No available staff recipients.', tone: 'error' });
      return;
    }
    const recipient = ui.el('select', { id: 'message-recipient' },
      ui.el('option', { value: '', text: 'Choose a recipient' }),
      recipients.map((person) => ui.el('option', {
        value: person.id,
        text: recipientLabel(person),
      })),
    );
    recipient.value = recipientId || '';
    const subjectInput = ui.el('input', {
      id: 'message-subject',
      type: 'text',
      maxlength: 200,
      value: subject,
      autocomplete: 'off',
    });
    const bodyInput = ui.el('textarea', {
      id: 'message-body',
      rows: 8,
      maxlength: 5000,
      placeholder: 'Write a message…',
    });
    const errorNode = ui.el('p', { class: 'field-error', id: 'message-compose-error', role: 'alert' });
    errorNode.hidden = true;
    const form = ui.el('div', { class: 'form-grid message-compose' },
      ui.el('div', { class: 'field' },
        ui.el('label', { for: 'message-recipient', text: 'Recipient' }),
        recipient,
        recipientName ? ui.el('p', { class: 'field-hint', text: `Replying to ${recipientName}` }) : null,
      ),
      ui.el('div', { class: 'field' },
        ui.el('label', { for: 'message-subject', text: 'Subject (optional)' }),
        subjectInput,
      ),
      ui.el('div', { class: 'field' },
        ui.el('label', { for: 'message-body', text: 'Message' }),
        bodyInput,
      ),
    );
    const cancel = ui.el('button', { class: 'btn btn-secondary', type: 'button', onClick: () => close() }, 'Cancel');
    const send = ui.el('button', { class: 'btn btn-primary', type: 'button', onClick: () => submit() }, 'Send message');
    const submit = async () => {
      if (state.sending) return;
      errorNode.hidden = true;
      if (!recipient.value) {
        errorNode.textContent = 'Choose a recipient.';
        errorNode.hidden = false;
        recipient.focus();
        return;
      }
      if (!bodyInput.value.trim()) {
        errorNode.textContent = 'Enter a message.';
        errorNode.hidden = false;
        bodyInput.focus();
        return;
      }
      state.sending = true;
      ui.setBusy(send, true, 'Sending…');
      try {
        await api.post('/messages', {
          recipient_id: recipient.value,
          subject: subjectInput.value.trim() || null,
          body: bodyInput.value,
        });
        close();
        toast.showToast({ message: 'Message sent.', tone: 'success' });
        state.box = 'sent';
        state.q = '';
        const search = document.getElementById('messages-search');
        if (search) search.value = '';
        state.selectedId = null;
        setTabs();
        await Promise.all([loadMessages(), loadCounts()]);
      } catch (error) {
        errorNode.textContent = api.describeApiError(error).message;
        errorNode.hidden = false;
        ui.setBusy(send, false);
      } finally {
        state.sending = false;
      }
    };
    const { close } = ui.openDialog({
      title: 'New message',
      icon: 'mail',
      bodyNodes: [form, errorNode],
      actionNodes: [cancel, send],
    });
    recipient.focus();
  }).catch((error) => {
    toast.showToast({ message: api.describeApiError(error).message, tone: 'error' });
  });
}

function renderPermissionDenied() {
  const body = ui.clear(document.getElementById('page-body'));
  body.append(
    ui.el('div', { class: 'state-panel state-panel--error', role: 'alert' },
      ui.el('h2', { class: 'state-panel__title', text: 'You do not have permission' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: 'Messages are limited to accounts with the message:read permission. The backend enforces this on every request.',
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
    user = await auth.initAuthChrome({ active: 'messages' });
    if (!user) return;
  } catch (error) {
    ui.renderErrorState(document.getElementById('page-body'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
    return;
  }
  state.user = user;
  commandMenu.initCommandMenu({
    user,
    commands: auth.hasPermission(user, 'message:write')
      ? [{
          id: 'compose-message',
          title: 'New message',
          hint: 'Compose an internal staff message',
          keywords: 'message compose send staff',
          group: 'Actions',
          run: () => openCompose(),
        }]
      : [],
  });
  if (!auth.hasPermission(user, 'message:read')) {
    renderPermissionDenied();
    return;
  }
  const loading = document.getElementById('messages-loading');
  if (loading) loading.hidden = true;
  const newButton = document.getElementById('messages-new');
  if (newButton) {
    newButton.hidden = !auth.hasPermission(user, 'message:write');
    newButton.addEventListener('click', () => openCompose());
  }
  document.getElementById('messages-inbox-tab')?.addEventListener('click', () => setBox('inbox'));
  document.getElementById('messages-sent-tab')?.addEventListener('click', () => setBox('sent'));
  wireSearch();
  setTabs();
  loadCounts();
  loadMessages();
});
