// ---------------------------------------------------------------------------
// messages.html page module — internal staff-to-staff messages.
//
// Endpoints: GET /messages?box=inbox|sent, GET /messages/recipients,
// POST /messages, POST /messages/{id}/read. Only the caller's own inbox/sent
// items are returned; the backend re-checks message:read/message:write.
// Message bodies are rendered as text only (never HTML/Markdown).
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as commandMenu from './command-menu.js?v=20261109j';
import * as toast from './toast.js';
import * as ui from './ui.js';

const PAGE_SIZE = 50;

const state = {
  user: null,
  box: 'inbox',
  messages: [],
  selectedId: null,
  recipients: null,
  loading: false,
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
}

function messageRows() {
  const region = document.getElementById('messages-region');
  ui.clear(region);
  if (state.messages.length === 0) {
    ui.renderEmptyState(region, {
      title: state.box === 'inbox' ? 'Your inbox is empty' : 'No sent messages',
      message: state.box === 'inbox'
        ? 'Messages from other staff will appear here.'
        : 'Messages you send will appear here.',
      headingTag: 'h3',
    });
    return;
  }
  const list = ui.el('div', { class: 'message-list' });
  for (const message of state.messages) {
    const unread = state.box === 'inbox' && !message.read_at;
    const counterpart = state.box === 'inbox'
      ? message.sender_display_name
      : message.recipient_display_name;
    const row = ui.el('button', {
      type: 'button',
      class: `message-row${unread ? ' message-row--unread' : ''}${
        state.selectedId === message.id ? ' is-active' : ''
      }`,
      'aria-current': state.selectedId === message.id ? 'true' : null,
      onClick: () => selectMessage(message),
    },
      ui.el('span', { class: 'message-row__top' },
        ui.el('span', { class: 'message-row__person', text: counterpart }),
        ui.el('span', {
          class: 'message-row__time',
          text: ui.formatDateTime(message.created_at) || 'Unknown time',
        }),
      ),
      ui.el('span', {
        class: 'message-row__subject',
        text: message.subject || '(No subject)',
      }),
      ui.el('span', { class: 'message-row__preview', text: message.body }),
      unread
        ? ui.el('span', { class: 'message-row__unread', text: 'Unread' })
        : null,
    );
    list.append(row);
  }
  region.append(list);
}

function renderDetail(message) {
  const region = document.getElementById('messages-detail');
  ui.clear(region);
  if (!message) {
    region.append(ui.el('p', { class: 'text-muted', text: 'Select a message to read it.' }));
    return;
  }
  const readState = message.read_at ? 'Read' : 'Unread';
  const actions = ui.el('div', { class: 'message-detail__actions' },
    ui.el('button', {
      class: 'btn btn-secondary btn-sm',
      type: 'button',
      onClick: () => openCompose({
        recipientId: state.box === 'inbox' ? message.sender_id : message.recipient_id,
        recipientName: state.box === 'inbox'
          ? message.sender_display_name
          : message.recipient_display_name,
        subject: message.subject ? `Re: ${message.subject}` : '',
      }),
    }, 'Reply'),
  );
  region.append(
    ui.el('div', { class: 'message-detail__head' },
      ui.el('h2', { class: 'message-detail__subject', text: message.subject || '(No subject)' }),
      ui.el('span', {
        class: `badge ${message.read_at ? 'badge--neutral' : 'badge--info'}`,
        text: readState,
      }),
    ),
    ui.el('dl', { class: 'message-detail__meta' },
      ui.el('dt', { text: 'From' }),
      ui.el('dd', { text: message.sender_display_name }),
      ui.el('dt', { text: 'To' }),
      ui.el('dd', { text: message.recipient_display_name }),
      ui.el('dt', { text: 'Sent' }),
      ui.el('dd', { text: ui.formatDateTime(message.created_at) || 'Unknown time' }),
    ),
    ui.el('p', { class: 'message-detail__body', text: message.body }),
    actions,
  );
}

async function selectMessage(message) {
  state.selectedId = message.id;
  renderDetail(message);
  messageRows();
  if (state.box !== 'inbox' || message.read_at) return;
  try {
    await api.post(`/messages/${encodeURIComponent(message.id)}/read`, {});
    message.read_at = new Date().toISOString();
    renderDetail(message);
    messageRows();
  } catch (error) {
    toast.showToast({ message: api.describeApiError(error).message, tone: 'error' });
  }
}

async function loadMessages() {
  if (state.loading) return;
  state.loading = true;
  setStatus('Loading messages.');
  setTabs();
  const region = document.getElementById('messages-region');
  ui.renderLoading(region, { label: state.box === 'inbox' ? 'Loading inbox' : 'Loading sent messages' });
  try {
    const page = await api.get('/messages', { box: state.box, limit: PAGE_SIZE, offset: 0 });
    state.messages = Array.isArray(page.items) ? page.items : [];
    const selected = state.messages.find((message) => message.id === state.selectedId) || null;
    if (!selected) state.selectedId = null;
    messageRows();
    renderDetail(selected);
    setStatus(
      state.messages.length === 0
        ? (state.box === 'inbox' ? 'Inbox is empty.' : 'No sent messages.')
        : `${state.messages.length} message${state.messages.length === 1 ? '' : 's'} shown.`,
    );
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
  setTabs();
  loadMessages();
}

async function ensureRecipients() {
  if (Array.isArray(state.recipients)) return state.recipients;
  const response = await api.get('/messages/recipients');
  state.recipients = Array.isArray(response.items) ? response.items : [];
  return state.recipients;
}

function openCompose({ recipientId = '', recipientName = '', subject = '' } = {}) {
  if (!state.user || !auth.hasPermission(state.user, 'message:write')) return;
  ensureRecipients().then((recipients) => {
    if (recipients.length === 0) {
      toast.showToast({ message: 'No available staff recipients.', tone: 'error' });
      return;
    }
    const form = ui.el('div', { class: 'form-grid' });
    const recipient = ui.el('select', { id: 'message-recipient' },
      ui.el('option', { value: '', text: 'Choose a recipient' }),
      recipients.map((person) => ui.el('option', {
        value: person.id,
        text: person.display_name,
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
      rows: 7,
      maxlength: 5000,
      placeholder: 'Write a message…',
    });
    const errorNode = ui.el('p', { class: 'field-error', id: 'message-compose-error', role: 'alert' });
    errorNode.hidden = true;
    form.append(
      ui.el('div', { class: 'field' },
        ui.el('label', { for: 'message-recipient', text: 'Recipient' }),
        recipient,
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
      errorNode.hidden = true;
      if (!recipient.value) {
        errorNode.textContent = 'Choose a recipient.';
        errorNode.hidden = false;
        return;
      }
      if (!bodyInput.value.trim()) {
        errorNode.textContent = 'Enter a message.';
        errorNode.hidden = false;
        return;
      }
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
        state.selectedId = null;
        setTabs();
        await loadMessages();
      } catch (error) {
        errorNode.textContent = api.describeApiError(error).message;
        errorNode.hidden = false;
        ui.setBusy(send, false);
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
  setTabs();
  loadMessages();
});
