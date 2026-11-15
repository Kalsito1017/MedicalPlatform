// ---------------------------------------------------------------------------
// messages.html page module — internal staff-to-staff messages.
//
// Endpoints: GET /messages?box=inbox|sent&q=, GET /messages/counts,
// GET /messages/recipients, POST /messages, POST /messages/attachments,
// GET /messages/{id}/attachment, POST /messages/{id}/read.
// Only the caller's own inbox/sent items are returned; the backend re-checks
// message:read/message:write and sender/recipient attachment ownership.
// Message text is rendered as text only; attachments are always downloads.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as commandMenu from './command-menu.js?v=20261109j';
import * as toast from './toast.js';
import * as ui from './ui.js';

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 250;
const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;
const ATTACHMENT_ACCEPT = '.pdf,.png,.jpg,.jpeg,.webp,.txt,.csv,.docx,.xlsx';
const ATTACHMENT_EXTENSIONS = new Set(
  ATTACHMENT_ACCEPT.split(',').map((value) => value.trim().toLowerCase()),
);
const ATTACHMENT_HINT = 'PDF, PNG, JPEG, WebP, TXT, CSV, DOCX or XLSX · up to 5 MB';

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
  composeMode: false,
  composer: null,
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

function messageCounterpartId(message) {
  return state.box === 'inbox' ? message.sender_id : message.recipient_id;
}

/** The counterpart directory record (roles/photo) for a message. */
function counterpartRecord(message) {
  const id = String(messageCounterpartId(message) || '');
  const name = messageCounterpart(message);
  const match = Array.isArray(state.recipients)
    ? state.recipients.find((person) => String(person.id) === id)
    : null;
  return match
    ? { ...match, display_name: name || match.display_name }
    : { id, display_name: name, roles: [] };
}

function roleText(record) {
  const roles = Array.isArray(record.roles)
    ? record.roles.map(ui.roleLabel).filter(Boolean)
    : [];
  return roles.length > 0 ? roles.join(' · ') : 'Internal staff';
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

function formatFileSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return 'Unknown size';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function attachmentExtension(name) {
  const match = /\.([^.]+)$/.exec(String(name || ''));
  return match ? `.${match[1].toLowerCase()}` : '';
}

function replyComposerFor(message) {
  return {
    mode: 'reply',
    messageId: message.id,
    recipientId: String(messageCounterpartId(message) || ''),
    recipientName: messageCounterpart(message),
    subject: message.subject ? `Re: ${message.subject}` : '',
    body: '',
    file: null,
    error: '',
  };
}

function newComposer() {
  return {
    mode: 'new',
    messageId: null,
    recipientId: '',
    recipientName: '',
    subject: '',
    body: '',
    file: null,
    error: '',
  };
}

function composerAttachmentCard(attachment, messageId) {
  if (!attachment) return null;
  const href = api.buildUrl(
    `/messages/${encodeURIComponent(messageId)}/attachment`,
  );
  return ui.el('div', { class: 'message-attachment' },
    ui.el('span', { class: 'message-attachment__icon', 'aria-hidden': 'true' },
      ui.icon('file'),
    ),
    ui.el('span', { class: 'message-attachment__info' },
      ui.el('span', { class: 'message-attachment__name', text: attachment.filename }),
      ui.el('span', {
        class: 'message-attachment__size',
        text: formatFileSize(attachment.size_bytes),
      }),
    ),
    ui.el('a', {
      class: 'message-attachment__download',
      href,
      download: attachment.filename,
      title: `Download ${attachment.filename}`,
      'aria-label': `Download ${attachment.filename}`,
    }, ui.icon('download', 'icon--sm')),
  );
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
    const record = counterpartRecord(message);
    const subject = message.subject?.trim() || '(No subject)';
    const time = message.created_at;
    const preview = messagePreview(message.body);
    const previewText = message.attachment
      ? `${preview ? `${preview} · ` : ''}${message.attachment.filename}`
      : preview;
    const row = ui.el('button', {
      type: 'button',
      class: `message-row${unread ? ' message-row--unread' : ''}${
        state.selectedId === message.id ? ' is-active' : ''
      }`,
      'aria-current': state.selectedId === message.id ? 'true' : null,
      'aria-label': `${unread ? 'Unread message. ' : ''}${counterpart}: ${subject}`,
      onClick: () => selectMessage(message),
    },
      ui.el('span', { class: 'message-row__avatar' }, ui.avatarForUser(record, { size: 'lg' })),
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
        ui.el('span', { class: 'message-row__preview', text: previewText }),
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

function messageIsOutgoing(message) {
  return String(message.sender_id) === String(state.user && state.user.id);
}

function newComposeHeader() {
  return ui.el('header', { class: 'message-detail__header message-detail__header--compose' },
    ui.el('div', { class: 'message-detail__identity' },
      ui.el('p', { class: 'message-detail__sender', text: 'New message' }),
      ui.el('p', {
        class: 'message-detail__recipient',
        text: 'Send a private message to another staff member.',
      }),
    ),
    ui.el('div', { class: 'message-detail__head-actions' },
      ui.el('button', {
        class: 'link-button',
        type: 'button',
        onClick: () => cancelCompose(),
      }, 'Cancel'),
    ),
  );
}

function composerNode() {
  const draft = state.composer;
  if (!draft) return null;

  const errorNode = ui.el('p', {
    class: 'field-error message-composer__error',
    id: 'message-compose-error',
    role: 'alert',
  });
  errorNode.hidden = !draft.error;
  errorNode.textContent = draft.error || '';
  const showError = (message) => {
    draft.error = message;
    errorNode.textContent = message || '';
    errorNode.hidden = !message;
  };

  const recipients = Array.isArray(state.recipients) ? state.recipients : [];
  const recipientSelect = draft.mode === 'new'
    ? ui.el('select', {
        id: 'message-recipient',
        class: 'message-composer__recipient',
        'aria-label': 'Recipient',
      },
      ui.el('option', { value: '', text: 'Choose a recipient' }),
      recipients.map((person) => ui.el('option', {
        value: person.id,
        text: recipientLabel(person),
      })),
    )
    : null;
  if (recipientSelect) {
    recipientSelect.value = draft.recipientId || '';
    recipientSelect.addEventListener('change', () => {
      draft.recipientId = recipientSelect.value || '';
      const selected = recipients.find(
        (person) => String(person.id) === draft.recipientId,
      );
      draft.recipientName = selected ? selected.display_name : '';
    });
  }

  const subjectInput = ui.el('input', {
    id: 'message-subject',
    class: 'message-composer__subject',
    type: 'text',
    maxlength: 200,
    placeholder: 'Subject (optional)',
    value: draft.subject,
    autocomplete: 'off',
  });
  subjectInput.addEventListener('input', () => {
    draft.subject = subjectInput.value;
  });

  const bodyInput = ui.el('textarea', {
    id: 'message-body',
    class: 'message-composer__body',
    rows: 1,
    maxlength: 5000,
    placeholder: draft.mode === 'new'
      ? 'Write a message…'
      : `Reply to ${draft.recipientName}…`,
  });
  bodyInput.value = draft.body;
  bodyInput.addEventListener('input', () => {
    draft.body = bodyInput.value;
  });

  const fileInput = ui.el('input', {
    id: 'message-file',
    type: 'file',
    accept: ATTACHMENT_ACCEPT,
    hidden: true,
  });
  const attachmentHost = ui.el('div', {
    class: 'message-composer__attachment',
    id: 'message-attachment-preview',
  });
  const renderSelectedFile = () => {
    ui.clear(attachmentHost);
    if (!draft.file) return;
    attachmentHost.append(
      ui.el('div', { class: 'message-attachment-chip' },
        ui.el('span', { class: 'message-attachment-chip__icon', 'aria-hidden': 'true' },
          ui.icon('file', 'icon--sm'),
        ),
        ui.el('span', { class: 'message-attachment-chip__info' },
          ui.el('span', { class: 'message-attachment-chip__name', text: draft.file.name }),
          ui.el('span', {
            class: 'message-attachment-chip__size',
            text: formatFileSize(draft.file.size),
          }),
        ),
        ui.el('button', {
          class: 'message-composer__icon message-composer__icon--remove',
          type: 'button',
          'aria-label': 'Remove attachment',
          title: 'Remove attachment',
          onClick: () => {
            draft.file = null;
            fileInput.value = '';
            renderSelectedFile();
            bodyInput.focus();
          },
        }, ui.icon('x', 'icon--sm')),
      ),
    );
  };
  fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    if (!ATTACHMENT_EXTENSIONS.has(attachmentExtension(file.name))) {
      showError(`Unsupported attachment type. Use ${ATTACHMENT_HINT}.`);
      fileInput.value = '';
      return;
    }
    if (Number(file.size) > MAX_ATTACHMENT_BYTES) {
      showError('The attachment exceeds the 5 MB limit.');
      fileInput.value = '';
      return;
    }
    draft.file = file;
    showError('');
    renderSelectedFile();
  });

  const attachButton = ui.el('button', {
    class: 'message-composer__icon',
    type: 'button',
    id: 'message-attach',
    'aria-label': 'Attach a file',
    title: 'Attach a file',
    onClick: () => {
      if (typeof fileInput.click === 'function') fileInput.click();
    },
  }, ui.icon('paperclip'));

  const sendButton = ui.el('button', {
    class: 'message-composer__send',
    type: 'submit',
    id: 'message-send',
    'aria-label': 'Send message',
    title: 'Send message',
  }, ui.icon('send', 'icon--sm'));

  const submit = async (event) => {
    event.preventDefault();
    if (state.sending) return;
    showError('');
    if (recipientSelect) {
      draft.recipientId = recipientSelect.value || '';
      const selected = recipients.find(
        (person) => String(person.id) === draft.recipientId,
      );
      draft.recipientName = selected ? selected.display_name : '';
    }
    if (!draft.recipientId) {
      showError('Choose a recipient.');
      if (recipientSelect) recipientSelect.focus();
      return;
    }
    const bodyValue = bodyInput.value.trim();
    draft.body = bodyInput.value;
    draft.subject = subjectInput.value;
    if (!bodyValue && !draft.file) {
      showError('Write a message or attach a file.');
      bodyInput.focus();
      return;
    }
    if (bodyValue.length > 5000) {
      showError('The message must be at most 5000 characters.');
      bodyInput.focus();
      return;
    }

    state.sending = true;
    sendButton.disabled = true;
    sendButton.setAttribute('aria-busy', 'true');
    try {
      let response;
      if (draft.file) {
        const formData = new FormData();
        formData.append('recipient_id', draft.recipientId);
        const subjectValue = draft.subject.trim();
        if (subjectValue) formData.append('subject', subjectValue);
        formData.append('body', draft.body);
        formData.append('file', draft.file, draft.file.name);
        response = await api.postForm('/messages/attachments', formData);
      } else {
        response = await api.post('/messages', {
          recipient_id: draft.recipientId,
          subject: draft.subject.trim() || null,
          body: draft.body,
        });
      }
      const sentId = response && response.id ? String(response.id) : null;
      state.composer = null;
      state.composeMode = false;
      toast.showToast({ message: 'Message sent.', tone: 'success' });
      state.box = 'sent';
      state.q = '';
      const search = document.getElementById('messages-search');
      if (search) search.value = '';
      state.selectedId = sentId;
      setTabs();
      await Promise.all([loadMessages(), loadCounts()]);
    } catch (error) {
      showError(api.describeApiError(error).message);
    } finally {
      state.sending = false;
      sendButton.disabled = false;
      sendButton.removeAttribute('aria-busy');
    }
  };

  const form = ui.el('form', {
    class: 'message-composer',
    id: 'message-composer',
    onSubmit: submit,
  },
    ui.el('div', { class: 'message-composer__meta' },
      recipientSelect || ui.el('span', {
        class: 'message-composer__to',
        text: `To ${draft.recipientName}`,
      }),
      subjectInput,
    ),
    attachmentHost,
    ui.el('div', { class: 'message-composer__row' },
      attachButton,
      fileInput,
      bodyInput,
      sendButton,
    ),
    ui.el('p', { class: 'message-composer__hint', text: ATTACHMENT_HINT }),
    errorNode,
  );
  renderSelectedFile();
  return form;
}

function renderDetail(message) {
  const region = document.getElementById('messages-detail');
  if (!region) return;
  ui.clear(region);
  if (!message) {
    if (state.composeMode && state.composer) {
      region.append(newComposeHeader(), composerNode());
    } else {
      region.append(emptyDetailNode());
    }
    return;
  }

  if (
    !state.composer ||
    state.composer.mode !== 'reply' ||
    state.composer.messageId !== message.id
  ) {
    state.composer = replyComposerFor(message);
  }
  state.composeMode = false;

  const record = counterpartRecord(message);
  const name = record.display_name || messageCounterpart(message);
  const role = roleText(record);
  const sender = message.sender_display_name || 'Unknown sender';
  const recipient = message.recipient_display_name || 'Unknown recipient';
  const subject = message.subject?.trim() || '(No subject)';
  const timestamp = message.created_at;
  const outgoing = messageIsOutgoing(message);
  const readState = message.read_at ? 'Read' : 'Unread';

  const back = ui.el('button', {
    class: 'link-button message-detail__back',
    type: 'button',
    onClick: () => closeDetail(),
  }, '← Back to inbox');

  const reply = ui.el('button', {
    class: 'btn btn-primary btn-sm',
    type: 'button',
    onClick: () => focusComposer(message),
  }, ui.icon('mail', 'icon--sm'), 'Reply');

  const newMessage = ui.el('button', {
    class: 'btn btn-secondary btn-sm',
    type: 'button',
    onClick: () => openNewComposer(),
  }, ui.icon('plus', 'icon--sm'), 'New message');

  const header = ui.el('header', { class: 'message-detail__header' },
    ui.el('div', { class: 'message-detail__avatar' }, ui.avatarForUser(record, { size: 'lg' })),
    ui.el('div', { class: 'message-detail__identity' },
      ui.el('p', { class: 'message-detail__sender', text: name }),
      ui.el('p', { class: 'message-detail__recipient', text: role }),
      ui.el('p', {
        class: 'message-detail__route',
        text: outgoing
          ? `From you · To ${recipient}`
          : `From ${sender} · To ${recipient}`,
      }),
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
    ui.el('div', { class: 'message-detail__head-actions' }, reply, newMessage),
  );

  const thread = ui.el('div', { class: 'message-detail__thread' },
    ui.el('div', { class: 'message-day' },
      ui.el('span', { class: 'message-day__label', text: ui.displayDate(timestamp) }),
    ),
  );
  const bubble = ui.el('div', {
    class: `message-bubble ${outgoing ? 'message-bubble--outgoing' : 'message-bubble--incoming'}`,
  },
    ui.el('p', { class: 'message-bubble__subject', text: subject }),
    message.body ? ui.el('p', { class: 'message-bubble__body', text: message.body }) : null,
    message.attachment
      ? composerAttachmentCard(message.attachment, message.id)
      : null,
    ui.el('p', {
      class: 'message-bubble__meta',
      text: `${fullMessageTime(timestamp)} · ${readState}`,
    }),
  );
  const avatar = outgoing
    ? ui.avatarForUser(state.user || {}, { size: 'sm' })
    : ui.avatarForUser(record, { size: 'sm' });
  const bubbleRow = ui.el('div', {
    class: `message-bubble-row ${outgoing ? 'is-outgoing' : 'is-incoming'}`,
  });
  if (outgoing) bubbleRow.append(bubble, avatar);
  else bubbleRow.append(avatar, bubble);
  thread.append(bubbleRow);

  region.append(back, header, thread, composerNode());
}

function emptyProfileNode() {
  return ui.el('div', { class: 'messages-profile__empty' },
    ui.el('span', { class: 'messages-detail__empty-icon', 'aria-hidden': 'true' }, ui.icon('users')),
    ui.el('p', { class: 'messages-detail__empty-title', text: 'Contact details' }),
    ui.el('p', {
      class: 'messages-detail__empty-text',
      text: 'Select a message to see the staff member and message details.',
    }),
  );
}

function renderProfile(message) {
  const region = document.getElementById('messages-profile');
  if (!region) return;
  ui.clear(region);
  if (!message) {
    region.append(emptyProfileNode());
    return;
  }

  const record = counterpartRecord(message);
  const name = record.display_name || messageCounterpart(message);
  const subject = message.subject?.trim() || '(No subject)';
  const outgoing = messageIsOutgoing(message);
  const reply = ui.el('button', {
    class: 'messages-profile__action',
    type: 'button',
    onClick: () => focusComposer(message),
  }, ui.icon('mail'), 'Reply');
  const newMessage = ui.el('button', {
    class: 'messages-profile__action',
    type: 'button',
    onClick: () => openNewComposer(),
  }, ui.icon('plus'), 'New message');
  const details = ui.el('dl', { class: 'messages-profile__details' },
    ui.el('dt', { text: 'Subject' }),
    ui.el('dd', { text: subject }),
    ui.el('dt', { text: 'Direction' }),
    ui.el('dd', { text: outgoing ? 'Sent' : 'Received' }),
    ui.el('dt', { text: 'Time' }),
    ui.el('dd', { text: fullMessageTime(message.created_at) }),
    ui.el('dt', { text: 'Read state' }),
    ui.el('dd', { text: message.read_at ? 'Read' : 'Unread' }),
    ...(message.attachment
      ? [
          ui.el('dt', { text: 'Attachment' }),
          ui.el('dd', { text: message.attachment.filename }),
        ]
      : []),
  );

  region.append(
    ui.el('div', { class: 'messages-profile__avatar' }, ui.avatarForUser(record, { size: 'lg' })),
    ui.el('h2', { class: 'messages-profile__name', text: name }),
    ui.el('p', { class: 'messages-profile__role', text: roleText(record) }),
    ui.el('div', { class: 'messages-profile__actions' }, reply, newMessage),
    ui.el('div', { class: 'messages-profile__section' },
      ui.el('h3', { class: 'messages-profile__section-title', text: 'Message details' }),
      details,
    ),
  );
}

function openDetailOnMobile() {
  document.getElementById('messages-workspace')?.classList.add('is-reading');
}

function closeDetail() {
  state.selectedId = null;
  state.composeMode = false;
  state.composer = null;
  document.getElementById('messages-workspace')?.classList.remove('is-reading');
  renderDetail(null);
  renderProfile(null);
  renderMessageRows();
}

async function selectMessage(message) {
  state.selectedId = message.id;
  openDetailOnMobile();
  renderDetail(message);
  renderProfile(message);
  renderMessageRows();
  if (state.box !== 'inbox' || message.read_at) return;
  try {
    await api.post(`/messages/${encodeURIComponent(message.id)}/read`, {});
    message.read_at = new Date().toISOString();
    renderDetail(message);
    renderProfile(message);
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
      if (!state.composeMode) {
        document.getElementById('messages-workspace')?.classList.remove('is-reading');
      }
    }
    renderMessageRows();
    renderDetail(selected);
    renderProfile(selected);
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
  state.composeMode = false;
  state.composer = null;
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
    state.composeMode = false;
    state.composer = null;
    loadMessages();
  });
  input.addEventListener('input', () => {
    if (state.searchTimer) clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => {
      state.searchTimer = null;
      state.q = input.value.trim();
      state.selectedId = null;
      state.composeMode = false;
      state.composer = null;
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

/** Load the staff directory so list/detail avatars and roles can use it. */
function loadDirectory() {
  ensureRecipients()
    .then(() => {
      const selected = state.messages.find((message) => message.id === state.selectedId) || null;
      renderMessageRows();
      renderDetail(selected);
      renderProfile(selected);
    })
    .catch(() => {
      // Directory/photo enrichment is optional; initials remain the fallback.
    });
}

function recipientLabel(person) {
  const roles = Array.isArray(person.roles) ? person.roles.map(ui.roleLabel).filter(Boolean) : [];
  return roles.length > 0 ? `${person.display_name} · ${roles.join(', ')}` : person.display_name;
}

function focusComposer(message) {
  if (!state.user || !auth.hasPermission(state.user, 'message:write')) return;
  if (
    !state.composer ||
    state.composer.mode !== 'reply' ||
    state.composer.messageId !== message.id
  ) {
    state.composer = replyComposerFor(message);
  }
  state.composeMode = false;
  openDetailOnMobile();
  renderDetail(message);
  renderProfile(message);
  renderMessageRows();
  document.getElementById('message-body')?.focus();
}

async function openNewComposer() {
  if (!state.user || !auth.hasPermission(state.user, 'message:write')) return;
  try {
    const recipients = await ensureRecipients();
    if (recipients.length === 0) {
      toast.showToast({ message: 'No available staff recipients.', tone: 'error' });
      return;
    }
    state.composeMode = true;
    state.selectedId = null;
    state.composer = newComposer();
    openDetailOnMobile();
    renderDetail(null);
    renderProfile(null);
    renderMessageRows();
    document.getElementById('message-recipient')?.focus();
  } catch (error) {
    toast.showToast({ message: api.describeApiError(error).message, tone: 'error' });
  }
}

function cancelCompose() {
  state.composeMode = false;
  state.composer = null;
  const selected = state.messages.find(
    (message) => message.id === state.selectedId,
  ) || null;
  if (!selected) {
    document.getElementById('messages-workspace')?.classList.remove('is-reading');
  } else {
    state.composer = replyComposerFor(selected);
  }
  renderDetail(selected);
  renderProfile(selected);
  renderMessageRows();
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
          run: () => openNewComposer(),
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
    newButton.addEventListener('click', () => openNewComposer());
  }
  document.getElementById('messages-inbox-tab')?.addEventListener('click', () => setBox('inbox'));
  document.getElementById('messages-sent-tab')?.addEventListener('click', () => setBox('sent'));
  wireSearch();
  renderDetail(null);
  renderProfile(null);
  loadDirectory();
  setTabs();
  loadCounts();
  loadMessages();
});
