// ---------------------------------------------------------------------------
// overlay-history.js — "predictive back" history participation for overlays.
//
// Every overlay surface (command menu, dialogs/sheets, expanded AI history
// entries, the mobile nav drawer) pushes one history entry when it opens, so
// the browser Back button FIRST dismisses the topmost overlay (LIFO) with its
// exit transition instead of leaving the page. Overlay closes driven by the
// UI (Esc, scrim click, buttons, swipe-dismiss) consume their own history
// entry via history.back(), so the stack never leaks entries.
//
// What "predictive" means here (documented precisely, see DESIGN.md):
//   * TRUE: Back dismisses the topmost overlay first; the dismissal runs the
//     overlay's exit transition (view transition where supported); stacked
//     overlays unwind in LIFO order; popstate drives the state.
//   * ADAPTED: browsers do not expose gesture progress to vanilla JS (there is
//     no "predictive back" API on the web), so the exit transition cannot be
//     scrubbed with the gesture. The gesture-linked preview that DOES exist is
//     the swipe-to-dismiss sheet (sheet.js): the sheet follows the finger and
//     only dismisses past a threshold.
//
// Safety: history.back() is only called for entries this module actually
// pushed (pushState can fail in sandboxed frames — then no back() ever runs,
// so Back can never navigate away unexpectedly). URL state (patient.html?id=)
// is never touched: pushState repeats the current URL. Nothing here stores
// data; the history state carries only small opaque sequence numbers.
// ---------------------------------------------------------------------------

/** Feature marker: appointment flows can rely on deferred replacement pushes. */
export const supportsDeferredPush = true;

const stack = [];
let sequence = 0;
// Popstates caused by releaseOverlay() consuming its own history entry; each
// one swallows exactly one self-inflicted popstate so it never closes a second
// overlay (only a genuine Back unwinds the stack).
let selfBacksPending = 0;
// Overlays opened while a self-back is still in flight must not push their
// history entry yet: browsers perform history.back() asynchronously, so a
// synchronous pushState can be skipped/reordered and the pending back can
// navigate to the previous page (e.g. appointment detail -> cancel confirm).
let deferredPushes = [];
// Identity of the bound window.addEventListener (stable in a browser; the test
// DOM shim installs a fresh registry per scenario and needs a rebind).
let boundAddEventListener = null;

function onPopstate() {
  if (selfBacksPending > 0) {
    selfBacksPending -= 1;
    if (selfBacksPending === 0) flushDeferredPushes();
    return;
  }
  // Back pressed: unwind the TOPMOST live overlay (LIFO). Entries already
  // closed by the UI were spliced out of the stack, so a stray popstate (e.g.
  // the history.back() those closes issued) is a no-op.
  const entry = stack.pop();
  if (!entry || entry.done) return;
  entry.done = true;
  entry.close();
}

function bind() {
  if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
  if (boundAddEventListener === window.addEventListener) return;
  window.addEventListener('popstate', onPopstate);
  boundAddEventListener = window.addEventListener;
}

function writePush(entry) {
  if (entry.done || entry.pushed) return;
  try {
    if (typeof history !== 'undefined' && typeof history.pushState === 'function') {
      const current = history.state && typeof history.state === 'object' ? history.state : {};
      history.pushState({ ...current, __overlay: entry.sequence }, '');
      entry.pushed = true;
    }
  } catch {
    // Sandboxed frame: no history participation, close() still works.
    entry.pushed = false;
  }
}

function flushDeferredPushes() {
  if (selfBacksPending > 0) return;
  const pending = deferredPushes;
  deferredPushes = [];
  for (const entry of pending) writePush(entry);
}

/**
 * Register an overlay's close callback on the history stack.
 * Returns an entry handle to hand back to releaseOverlay() when the overlay
 * closes through the UI. `close` must close the surface WITHOUT touching
 * history (this module owns the history bookkeeping).
 *
 * If a previous overlay's UI close is still consuming its history entry, the
 * new entry's push is deferred until that self-back's popstate settles. This
 * makes "close one dialog, immediately open another" safe (appointment
 * detail -> reschedule/cancel confirmation) without navigating away.
 */
export function pushOverlay(close) {
  bind();
  sequence += 1;
  const entry = {
    sequence,
    close: typeof close === 'function' ? close : () => {},
    done: false,
    pushed: false,
  };
  stack.push(entry);
  if (selfBacksPending > 0) deferredPushes.push(entry);
  else writePush(entry);
  return entry;
}

/**
 * Release an overlay that closed through the UI: drop it from the stack and
 * consume its history entry. Idempotent — an entry closed by popstate is
 * already marked done and never triggers a second history.back().
 */
export function releaseOverlay(entry) {
  if (!entry || entry.done) return;
  entry.done = true;
  const index = stack.lastIndexOf(entry);
  if (index >= 0) stack.splice(index, 1);
  if (!entry.pushed) {
    // Closed before its deferred push ran: drop it from the queue so it can
    // never create a history entry after the fact.
    const deferredIndex = deferredPushes.indexOf(entry);
    if (deferredIndex >= 0) deferredPushes.splice(deferredIndex, 1);
    return;
  }
  try {
    if (typeof history !== 'undefined' && typeof history.back === 'function') {
      selfBacksPending += 1;
      history.back();
    }
  } catch {
    // Never break a UI close on history errors.
    selfBacksPending = Math.max(0, selfBacksPending - 1);
  }
}

/** Depth of the live overlay stack (tests, diagnostics). */
export function overlayDepth() {
  return stack.filter((entry) => !entry.done).length;
}

/** Drop all bookkeeping (tests only; a fresh page view starts empty). */
export function resetOverlayStack() {
  stack.length = 0;
  sequence = 0;
  selfBacksPending = 0;
  deferredPushes = [];
}
