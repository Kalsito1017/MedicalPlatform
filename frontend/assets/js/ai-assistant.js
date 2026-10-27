// ---------------------------------------------------------------------------
// ai-assistant.js — top-level AI Assistant page: general lookups over the
// synthetic demo records ("which patients are on metformin?").
// Endpoint (docs/API.md): POST /api/v1/ai/ask with { question } (no patient_id
// = general lookup mode; the patient page rail sends patient_id for the
// patient-scoped mode and is unchanged).
// The page is a chat surface: user questions on the right, AI-generated answers
// on the left with source chips and record citations, a composer with an
// inline send button (inside the textarea box) + cancel + 2000-character
// counter, and every state the rail has: loading, cancel, 503 "AI assistant
// unavailable", 429 with a retry countdown, and errors via
// api.describeApiError (network/offline included).
// Composer keys: Enter sends, Shift+Enter inserts a new line (hinted on the
// page and in the textarea's accessible description).
// Conversation history (docs/API.md "AI assistant" history): each successful
// ask is persisted server-side to the caller's private history and listed in
// the History section below the chat (newest first, expandable full answers
// with citations, "Clear history" behind a confirm dialog). History lives in
// the backend per user - never in web storage, never logged.
// Permission gating: the nav item and the composer render only for accounts
// with patient:read; the backend re-checks every request server-side.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as aiHistory from './ai-history.js';
import * as auth from './auth.js';
import * as ui from './ui.js';

const QUESTION_MAX = 2000;

// Plain-text shortcuts that fill the composer. Real queries, no automation.
const SUGGESTED_QUESTIONS = [
  'Which patients have abnormal potassium results?',
  'Which patients are on metformin?',
  'Which synthetic patients have the most recent lab results?',
  'Which patients went through me in the last 7 days?',
];

const aiRequest = { controller: null };
const state = { started: false };

function setAiStatus(message) {
  const status = document.getElementById('ai-status');
  if (status) status.textContent = message;
}

function syncQuestionCounter() {
  const input = document.getElementById('ai-question');
  const counter = document.getElementById('ai-char-count');
  const submit = document.getElementById('ai-submit');
  const errorNode = document.getElementById('ai-question-error');
  if (!input || !counter || !submit) return;

  const length = input.value.length;
  const over = length > QUESTION_MAX;
  counter.textContent = over
    ? `${length} / ${QUESTION_MAX} characters — over the limit`
    : `${length} / ${QUESTION_MAX} characters`;
  counter.classList.toggle('char-counter--over', over);
  if (over) {
    input.setAttribute('aria-invalid', 'true');
    if (errorNode) {
      errorNode.textContent = `Questions are limited to ${QUESTION_MAX} characters. Shorten your question to ask.`;
      errorNode.hidden = false;
    }
  } else {
    input.removeAttribute('aria-invalid');
    if (errorNode) {
      errorNode.textContent = '';
      errorNode.hidden = true;
    }
  }
  submit.disabled = length === 0 || over;
}

function renderSuggestedQuestions() {
  const host = document.getElementById('ai-suggestions');
  if (!host) return;
  ui.clear(host);
  for (const question of SUGGESTED_QUESTIONS) {
    host.append(ui.el('button', {
      class: 'chip chip--action',
      type: 'button',
      text: question,
      onClick: () => {
        const input = document.getElementById('ai-question');
        if (!input) return;
        input.value = question;
        syncQuestionCounter();
        if (typeof input.focus === 'function') input.focus();
      },
    }));
  }
}

/** Enter sends; Shift+Enter inserts a new line (documented in the hint). */
function onComposerKeydown(event) {
  if (!event || event.key !== 'Enter') return;
  if (event.shiftKey) return;
  if (event.isComposing) return;
  if (event.preventDefault) event.preventDefault();
  askAssistant();
}

function initAiPage(user) {
  const form = document.getElementById('ai-question-form');
  const input = document.getElementById('ai-question');
  const cancel = document.getElementById('ai-cancel');
  const region = document.getElementById('ai-response-region');
  if (!form || !input || !cancel || !region) return;

  // UX gating only: the backend enforces patient:read server-side on every ask.
  if (!auth.hasPermission(user, 'patient:read')) {
    form.hidden = true;
    const suggestions = document.getElementById('ai-suggestions');
    if (suggestions) suggestions.hidden = true;
    const historySection = document.getElementById('ai-history');
    if (historySection) historySection.hidden = true;
    ui.clear(region);
    region.append(ui.el('div', { class: 'state-panel state-panel--empty' },
      ui.el('h3', { class: 'state-panel__title', text: 'AI assistant not available' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: 'The AI assistant is only available to accounts that can view patient data. Access is enforced by the backend.',
      }),
    ));
    setAiStatus('The AI assistant is not available for this account.');
    return;
  }

  renderSuggestedQuestions();
  input.addEventListener('input', syncQuestionCounter);
  input.addEventListener('keydown', onComposerKeydown);
  const send = document.getElementById('ai-submit');
  if (send) {
    send.addEventListener('click', () => {
      askAssistant();
    });
  }
  cancel.addEventListener('click', () => {
    if (aiRequest.controller) aiRequest.controller.abort();
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    askAssistant();
  });
  syncQuestionCounter();
  initHistory();
}

/* ------------------------------------------------------------ rendering */

function chatBubble(variant, ...children) {
  return ui.el('div', { class: `chat-bubble chat-bubble--${variant}` }, children.flat());
}

function chatMeta(text) {
  return ui.el('p', { class: 'chat-bubble__meta', text });
}

function citationLabel(citation) {
  if (typeof citation === 'string') return `Synthetic record reference: ${citation}`;
  if (citation && typeof citation === 'object') {
    const type = citation.type || citation.resource_type || citation.kind;
    const id = citation.id || citation.resource_id || citation.record_id;
    if (type && id) return `Synthetic ${String(type).replace(/_/g, ' ')} ${id}`;
    if (id) return `Synthetic record reference: ${id}`;
    return 'Synthetic record reference';
  }
  return 'Synthetic record reference';
}

function citationHref(citation, knownPatientId) {
  if (!citation || typeof citation !== 'object') return null;
  const type = citation.type || citation.resource_type || citation.kind;
  const id = citation.id || citation.resource_id || citation.record_id;
  if (!id) return null;
  if (type === 'patient') return `patient.html?id=${encodeURIComponent(id)}`;
  // Record citations (encounter/lab/prescription) link to the patient record
  // only when the answer cites exactly one patient, so the attribution is
  // unambiguous. Otherwise the reference stays plain text — never invented.
  if (knownPatientId && ['encounter', 'lab_result', 'prescription'].includes(type)) {
    return `patient.html?id=${encodeURIComponent(knownPatientId)}`;
  }
  return null;
}

function citationsBlock(citations) {
  const patientIds = [...new Set(
    citations
      .filter((citation) => citation && typeof citation === 'object' && (citation.type || citation.resource_type) === 'patient')
      .map((citation) => String(citation.id || citation.resource_id || '')),
  )].filter(Boolean);
  const knownPatientId = patientIds.length === 1 ? patientIds[0] : null;

  return ui.el('div', { class: 'ai-citations' },
    ui.el('p', { class: 'field-hint', text: 'Records consulted:' }),
    ui.el('ul', { class: 'compact-list' },
      citations.map((citation) => {
        const href = citationHref(citation, knownPatientId);
        return ui.el('li', {},
          href
            ? ui.el('a', { href, text: citationLabel(citation) })
            : ui.el('span', { text: citationLabel(citation) }),
        );
      }),
    ),
  );
}

function renderAiAnswer(container, payload) {
  const answer = payload && typeof payload.answer === 'string' ? payload.answer.trim() : '';
  const usedTools = Array.isArray(payload && payload.used_tools) ? payload.used_tools : [];
  const citations = Array.isArray(payload && payload.citations) ? payload.citations : [];
  const disclaimer = payload && typeof payload.disclaimer === 'string' ? payload.disclaimer.trim() : '';
  const model = payload && typeof payload.model === 'string' ? payload.model.trim() : '';
  const answeredAt = ui.formatDateTime(new Date().toISOString()) || '';

  const bubble = chatBubble('assistant',
    ui.el('div', { class: 'badge-list' },
      ui.el('span', { class: 'badge badge--synthetic', text: 'AI-generated' }),
      ui.el('span', { class: 'badge badge--neutral', text: 'Synthetic demo data' }),
    ),
  );

  const paragraphs = answer.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean);
  bubble.append(
    paragraphs.length > 0
      ? ui.el('div', { class: 'ai-answer' }, paragraphs.map((text) => ui.el('p', { text })))
      : ui.el('p', { class: 'text-muted', text: 'The assistant returned no answer text.' }),
  );

  bubble.append(
    ui.el('div', { class: 'alert alert--info', role: 'note' },
      ui.el('p', {
        text: 'AI-generated synthetic-demo assistance about synthetic demo data \u2014 not a diagnosis or treatment recommendation.',
      }),
      disclaimer ? ui.el('p', { text: disclaimer }) : null,
    ),
  );

  if (usedTools.length > 0) {
    bubble.append(
      ui.el('div', { class: 'ai-sources' },
        ui.el('p', { class: 'field-hint', text: 'Sources consulted:' }),
        ui.el('ul', { class: 'chip-list' },
          usedTools.map((tool) => ui.el('li', { class: 'chip', text: String(tool) })),
        ),
      ),
    );
  }

  if (citations.length > 0) {
    bubble.append(citationsBlock(citations));
  }

  bubble.append(
    chatMeta(
      `${model ? `Model: ${model}` : 'Model not reported by the backend.'}${answeredAt ? ` \u2022 ${answeredAt}` : ''}`,
    ),
  );

  container.append(bubble);
  setAiStatus('Answer received. AI-generated synthetic-demo assistance \u2014 not a diagnosis or treatment recommendation.');
}

function renderAiUnavailable(container) {
  container.append(
    ui.el('div', { class: 'state-panel state-panel--empty' },
      ui.el('h3', { class: 'state-panel__title', text: 'AI assistant unavailable' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: 'The AI assistant is switched off or unreachable in this deployment. This demo runs without a language model unless one is configured, so assistant answers can be unavailable. Everything else on this page keeps working, and you can try again later.',
      }),
    ),
  );
  setAiStatus('AI assistant unavailable.');
}

function renderAiRateLimited(container, error) {
  const seconds = error && error.retryAfter ? Math.ceil(error.retryAfter) : null;
  container.append(
    ui.el('div', { class: 'state-panel state-panel--empty' },
      ui.el('h3', { class: 'state-panel__title', text: 'Too many questions' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: seconds
          ? `You have asked too many questions in a short time. Please retry in ${seconds} seconds.`
          : 'You have asked too many questions in a short time. Please wait a moment and try again.',
      }),
    ),
  );
  setAiStatus(seconds ? `Too many questions. Retry in ${seconds} seconds.` : 'Too many questions.');
}

function renderAiError(container, error) {
  if (error instanceof api.ApiError && error.status === 503) {
    renderAiUnavailable(container);
    return;
  }
  if (error instanceof api.ApiError && error.status === 429) {
    renderAiRateLimited(container, error);
    return;
  }
  ui.renderErrorState(container, error, {
    describe: api.describeApiError,
    onRetry: () => askAssistant(),
    headingTag: 'h3',
  });
  setAiStatus('The assistant request did not succeed. See the message on this page.');
}

/* -------------------------------------------------------------- history */

async function initHistory() {
  const clearButton = document.getElementById('ai-history-clear');
  if (clearButton) {
    clearButton.addEventListener('click', () => {
      clearHistoryFlow();
    });
  }
  const list = document.getElementById('ai-history-list');
  if (!list) return;
  try {
    const page = await aiHistory.fetchHistory();
    aiHistory.renderHistoryList(list, page && Array.isArray(page.items) ? page.items : []);
  } catch (error) {
    aiHistory.renderHistoryFailure(list, error);
  }
}

async function clearHistoryFlow() {
  const list = document.getElementById('ai-history-list');
  const confirmed = await ui.confirmDialog({
    title: 'Clear your question history?',
    message: 'This permanently removes every saved question and answer from your account \u2014 all patients and general lookups. It cannot be undone.',
    confirmLabel: 'Clear history',
    cancelLabel: 'Keep history',
    danger: true,
  });
  if (!confirmed) return;
  try {
    await aiHistory.clearHistory();
    aiHistory.renderHistoryList(list, []);
    setAiStatus('Your question history was cleared.');
  } catch (error) {
    aiHistory.renderHistoryFailure(list, error);
    setAiStatus('The question history could not be cleared. See the message on this page.');
  }
}

/** The just-finished exchange joins the history list without a reload. */
function recordHistoryEntry(question, payload) {
  aiHistory.appendHistoryEntry(document.getElementById('ai-history-list'), {
    question,
    answer: payload && typeof payload.answer === 'string' ? payload.answer : '',
    used_tools: Array.isArray(payload && payload.used_tools) ? payload.used_tools : [],
    citations: Array.isArray(payload && payload.citations) ? payload.citations : [],
    model: payload && typeof payload.model === 'string' ? payload.model : '',
    created_at: new Date().toISOString(),
  });
}

/* --------------------------------------------------------------- asking */

async function askAssistant() {
  const input = document.getElementById('ai-question');
  const region = document.getElementById('ai-response-region');
  const submit = document.getElementById('ai-submit');
  const cancel = document.getElementById('ai-cancel');
  const question = input ? input.value.trim() : '';

  if (!question || question.length > QUESTION_MAX) {
    syncQuestionCounter();
    return;
  }

  if (!state.started) {
    ui.clear(region);
    state.started = true;
  }

  if (aiRequest.controller) aiRequest.controller.abort();
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  aiRequest.controller = controller;

  // Compose is disabled while a request is in flight (send stays reachable
  // only via Cancel); the finally block restores it.
  if (submit) submit.disabled = true;
  if (input) input.disabled = true;
  if (cancel) cancel.hidden = false;
  setAiStatus('Consulting the assistant\u2026');

  region.append(
    chatBubble('user',
      ui.el('p', { text: question }),
      chatMeta(ui.formatDateTime(new Date().toISOString()) || ''),
    ),
  );
  const turn = ui.el('div', { class: 'ai-turn', role: 'status' });
  region.append(turn);
  turn.append(
    ui.el('div', { class: 'state-panel' },
      ui.el('p', { class: 'state-panel__title', text: 'Consulting the assistant\u2026' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: 'The assistant is searching the synthetic records your account may read.',
      }),
    ),
  );
  region.setAttribute('aria-busy', 'true');

  try {
    // General lookup mode: no patient reference in the body (docs/API.md).
    const payload = await api.post(
      '/ai/ask',
      { question },
      controller ? { signal: controller.signal } : undefined,
    );
    ui.clear(turn);
    renderAiAnswer(turn, payload);
    recordHistoryEntry(question, payload);
  } catch (error) {
    ui.clear(turn);
    if (error && error.name === 'AbortError') {
      turn.append(
        ui.el('p', { class: 'text-muted', text: 'Request cancelled. Ask again whenever you are ready.' }),
      );
      setAiStatus('Request cancelled.');
    } else {
      renderAiError(turn, error);
    }
  } finally {
    // Only the in-flight request resets the shared controls: an aborted
    // predecessor must not clear the state of the request that replaced it.
    if (aiRequest.controller === controller) {
      aiRequest.controller = null;
      region.removeAttribute('aria-busy');
      if (cancel) cancel.hidden = true;
      if (input) input.disabled = false;
      syncQuestionCounter();
    }
    turn.removeAttribute('role');
  }
}

ui.onReady(async () => {
  ui.setRetryHandler(() => window.location.reload());

  let user;
  try {
    user = await auth.initAuthChrome({ active: 'ai' });
    if (!user) return;
  } catch (error) {
    ui.renderErrorState(document.getElementById('page-body'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
    return;
  }
  initAiPage(user);
});
