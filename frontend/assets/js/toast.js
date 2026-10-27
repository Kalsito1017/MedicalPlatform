// ---------------------------------------------------------------------------
// toast.js — transient status toasts with an optional working Undo action.
//
// Used for reversible, high-impact admin mutations (deactivate account, revoke
// role): the mutation happens immediately (optimistic UI, see admin.js) and the
// toast offers "Undo", which runs the inverse API call and reflects the result
// ("Restored", or an explanatory error toast built from api.describeApiError).
//
// Accessibility: the region is a polite aria-live status; every toast carries a
// real focusable action button and a focusable close button; toasts auto-dismiss
// after a few seconds but can always be dismissed manually. Focus is never
// stolen: page modules announce mutation outcomes in their own status regions.
// No data is logged and nothing is written to web storage.
// ---------------------------------------------------------------------------

import { describeApiError } from './api.js';
import { clear, el } from './ui.js';

const TOAST_DURATION_MS = 6000;

function toastRegion() {
  let region = document.getElementById('toast-region');
  if (region) return region;
  region = el('div', {
    id: 'toast-region',
    class: 'toast-region',
    role: 'status',
    'aria-live': 'polite',
    'aria-label': 'Notifications',
  });
  document.body.append(region);
  return region;
}

function detach(region, node) {
  if (!region || !node) return;
  // Real DOM uses parentNode; the test DOM shim uses parent.
  const parent = node.parentNode || node.parent;
  if (parent === region && typeof region.removeChild === 'function') {
    region.removeChild(node);
  }
}

function scheduleDismiss(region, node, duration) {
  if (!(duration > 0)) return;
  const timer = setTimeout(() => detach(region, node), duration);
  // In Node (tests) a pending timer would keep the process alive.
  if (timer && typeof timer.unref === 'function') timer.unref();
}

/**
 * Show a toast. Options:
 *   message      — required, plain professional copy.
 *   tone         — 'info' (default) | 'error'.
 *   actionLabel  — optional action button label (e.g. 'Undo').
 *   onAction     — async handler for the action button; the toast dismisses
 *                  when it resolves and stays (with an error toast) when it
 *                  rejects.
 *   duration     — auto-dismiss delay in ms (default 6000; 0 keeps it until
 *                  dismissed).
 * Returns { dismiss }.
 */
export function showToast({
  message,
  tone = 'info',
  actionLabel = null,
  onAction = null,
  duration = TOAST_DURATION_MS,
} = {}) {
  const region = toastRegion();

  const node = el('div', { class: `toast toast--${tone}` },
    el('p', { class: 'toast__text', text: String(message == null ? '' : message) }),
    el('div', { class: 'toast__actions' }),
  );
  const actions = node.querySelector('.toast__actions');

  const dismiss = () => detach(region, node);

  if (actionLabel && typeof onAction === 'function') {
    const actionButton = el('button', {
      class: 'btn btn-secondary btn-sm hit-slop',
      type: 'button',
      onClick: async () => {
        actionButton.disabled = true;
        try {
          await onAction();
          dismiss();
        } catch (error) {
          dismiss();
          showErrorToast(error);
        }
      },
    }, actionLabel);
    actions.append(actionButton);
  }

  actions.append(el('button', {
    class: 'btn btn-ghost btn-sm hit-slop',
    type: 'button',
    'aria-label': 'Dismiss notification',
    onClick: dismiss,
  }, 'Close'));

  region.append(node);
  scheduleDismiss(region, node, duration);
  return { dismiss };
}

/** Explanatory error toast built from the shared API error mapping. */
export function showErrorToast(error) {
  const info = describeApiError(error);
  return showToast({
    message: `${info.title}. ${info.message}`,
    tone: 'error',
  });
}

/** Remove every visible toast (used when a page needs a clean slate). */
export function clearToasts() {
  const region = document.getElementById('toast-region');
  if (region) clear(region);
}
