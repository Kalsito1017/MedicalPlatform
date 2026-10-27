// ---------------------------------------------------------------------------
// ai-history.js — shared conversation-history surface for both AI entry points
// (the AI Assistant page and the patient rail).
// API (docs/API.md "AI assistant"): GET /api/v1/ai/conversations returns ONLY
// the caller's own exchanges (newest first; optional patient filter via the
// patient-scoped entry point) and DELETE /api/v1/ai/conversations clears the
// caller's own history. History is stored SERVER-SIDE per user - never in web
// storage, never logged, never shared between accounts.
// Each entry renders as a collapsible summary (question, relative timestamp,
// mode badge patient/general, which tools ran) that expands into the full
// answer turn with its record citations, styled like a live chat turn. The
// summary is a real button (aria-expanded + aria-controls); expanding moves
// focus into the detail so keyboard and screen-reader users land on the answer.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as markdown from './markdown.js';
import * as overlayHistory from './overlay-history.js';
import * as ui from './ui.js';

/** One history page for the caller (patientId narrows to one patient). */
export async function fetchHistory({ patientId = null, limit = 20, offset = 0 } = {}) {
  const query = { limit, offset };
  if (patientId) query.patient_id = patientId;
  return api.get('/ai/conversations', query);
}

/** Clear the caller's ENTIRE own history (all patients and general lookups). */
export async function clearHistory() {
  return api.del('/ai/conversations');
}

let entrySequence = 0;

// History-entry overlay handles (predictive back): one per expanded entry so
// Back collapses the topmost expanded entry first, in LIFO order.
const overlays = new WeakMap();

function setExpanded(summary, detail, expanded) {
  summary.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  detail.hidden = !expanded;
  if (expanded && typeof detail.focus === 'function') detail.focus();
}

function toggleEntry(summary, detail) {
  const open = summary.getAttribute('aria-expanded') === 'true';
  if (open) {
    // UI collapse: consume the entry's history entry (predictive back).
    const entry = overlays.get(detail);
    if (entry) {
      overlays.delete(detail);
      overlayHistory.releaseOverlay(entry);
    }
    ui.withViewTransition(() => setExpanded(summary, detail, false));
    return;
  }
  // Expand with a view transition (instant fallback / reduced motion), then
  // register the overlay: Back collapses it before leaving the page.
  ui.withViewTransition(() => setExpanded(summary, detail, true));
  overlays.set(
    detail,
    overlayHistory.pushOverlay(() => {
      overlays.delete(detail);
      setExpanded(summary, detail, false);
    }),
  );
}

function answerParagraphs(answer) {
  // Untrusted model output: the safe Markdown-subset renderer builds DOM
  // nodes only (never HTML); an empty answer keeps the calm fallback note.
  return markdown.answerBody(answer)
    || ui.el('p', { class: 'text-muted', text: 'The assistant returned no answer text.' });
}

function citationsBlock(citations) {
  return ui.el('div', { class: 'ai-citations' },
    ui.el('p', { class: 'field-hint', text: 'Records consulted:' }),
    ui.el('ul', { class: 'compact-list' },
      citations.map((citation) => {
        const label = `Synthetic ${String(citation.type || 'record').replace(/_/g, ' ')} ${citation.id}`;
        return ui.el('li', {},
          citation.type === 'patient'
            ? ui.el('a', { href: `patient.html?id=${encodeURIComponent(citation.id)}`, text: label })
            : ui.el('span', { text: label }),
        );
      }),
    ),
  );
}

/**
 * One collapsible history entry. `entry` speaks the docs/API.md conversation
 * item shape: { question, answer, used_tools, citations, model, created_at,
 * patient_id? }.
 */
export function historyEntryNode(entry, { compact = false } = {}) {
  const question = typeof entry.question === 'string' ? entry.question.trim() : '';
  const answer = typeof entry.answer === 'string' ? entry.answer.trim() : '';
  const tools = Array.isArray(entry.used_tools) ? entry.used_tools.map(String) : [];
  const citations = Array.isArray(entry.citations) ? entry.citations.filter(Boolean) : [];
  const model = typeof entry.model === 'string' ? entry.model.trim() : '';
  const patientScoped = entry.patient_id != null && entry.patient_id !== '';
  const when = ui.formatRelativeTime(entry.created_at) || ui.formatDateTime(entry.created_at) || '';
  const whenExact = ui.formatDateTime(entry.created_at) || '';

  entrySequence += 1;
  const detailId = `ai-history-detail-${entrySequence}`;

  const detail = ui.el('div', {
    class: 'ai-history__detail',
    id: detailId,
    tabindex: '-1',
    hidden: true,
  },
    ui.el('div', { class: 'chat-bubble chat-bubble--user' },
      ui.el('p', { text: question || '(no question text)' }),
      whenExact ? ui.el('p', { class: 'chat-bubble__meta', text: whenExact }) : null,
    ),
    ui.el('div', { class: 'chat-bubble chat-bubble--assistant' },
      ui.assistantTurnHead(),
      ui.el('div', { class: 'badge-list' },
        ui.el('span', { class: 'badge badge--synthetic', text: 'AI-generated' }),
        ui.el('span', { class: 'badge badge--neutral', text: 'Synthetic demo data' }),
      ),
      answerParagraphs(answer),
      ui.el('div', { class: 'alert alert--info', role: 'note' },
        ui.el('p', {
          text: 'AI-generated synthetic-demo assistance about synthetic demo data \u2014 not a diagnosis or treatment recommendation.',
        }),
      ),
      tools.length > 0
        ? ui.el('div', { class: 'ai-sources' },
          ui.el('p', { class: 'field-hint', text: 'Sources consulted:' }),
          ui.el('ul', { class: 'chip-list' }, tools.map((tool) => ui.el('li', { class: 'chip', text: tool }))),
        )
        : null,
      citations.length > 0 ? citationsBlock(citations) : null,
      ui.el('p', {
        class: 'chat-bubble__meta',
        text: `${model ? `Model: ${model}` : 'Model not reported by the backend.'}${whenExact ? ` \u2022 ${whenExact}` : ''}`,
      }),
    ),
  );

  const summary = ui.el('button', {
    class: 'ai-history__summary',
    type: 'button',
    'aria-expanded': 'false',
    'aria-controls': detailId,
    onClick: () => toggleEntry(summary, detail),
  },
    ui.el('span', { class: 'ai-history__question', text: question || '(no question text)' }),
    ui.el('span', { class: 'ai-history__meta' },
      ui.el('span', {
        class: patientScoped ? 'badge badge--info' : 'badge badge--neutral',
        text: patientScoped ? 'Patient' : 'General',
      }),
      ui.el('span', { class: 'ai-history__time', text: when || 'Time not recorded' }),
      ui.el('span', { class: 'ai-history__tools' },
        tools.length > 0
          ? tools.map((tool) => ui.el('span', { class: 'chip', text: tool }))
          : ui.el('span', { class: 'text-muted', text: 'No record tools called' }),
      ),
    ),
  );

  return ui.el('li', { class: compact ? 'ai-history__item ai-history__item--compact' : 'ai-history__item' },
    summary,
    detail,
  );
}

/** Render a full history list (newest first); an empty list gets a calm note. */
export function renderHistoryList(listNode, entries, { compact = false, emptyText = 'No questions yet.' } = {}) {
  if (!listNode) return;
  ui.clear(listNode);
  const items = Array.isArray(entries) ? entries.filter(Boolean) : [];
  if (items.length === 0) {
    listNode.append(ui.el('p', { class: 'text-muted ai-history__empty', text: emptyText }));
    return;
  }
  for (const entry of items) listNode.append(historyEntryNode(entry, { compact }));
}

/** Prepend one just-completed exchange so it appears without a reload. */
export function appendHistoryEntry(listNode, entry, { compact = false } = {}) {
  if (!listNode || !entry) return;
  const empty = listNode.querySelector('.ai-history__empty');
  if (empty) listNode.removeChild(empty);
  listNode.prepend(historyEntryNode(entry, { compact }));
}

/** History load/clear failures: a small honest state, never a crash. */
export function renderHistoryFailure(listNode, error) {
  if (!listNode) return;
  ui.renderErrorState(listNode, error, {
    describe: api.describeApiError,
    headingTag: 'h3',
  });
}
