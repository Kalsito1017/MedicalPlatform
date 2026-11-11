// ---------------------------------------------------------------------------
// sheet.js — swipe-to-dismiss for overlay dialogs presented as bottom sheets.
//
// On touch widths (≤620px, see styles.css) the overlay surfaces (confirmDialog,
// openDialog — including the generated-password modal — and the command menu)
// present as bottom sheets. Dragging the sheet's grab handle down follows the
// finger; releasing past a threshold (≥120px) dismisses the sheet with the
// same semantics as Esc/cancel — the dialog CLOSES without ever confirming.
//
// Input model: Pointer Events where available (mouse/pen/touch unified), with
// a touch-events fallback. The drag only starts on the dedicated grab handle
// (`.sheet-grabber`), so scrolling the command-menu list or typing in the
// palette input is never hijacked. Vertical drags only: a horizontal-first
// gesture cancels the drag. Upward movement past the resting position is
// rubber-banded with diminishing resistance, then snaps back on release.
//
// STYLE-WRITE EXCEPTION (lead pre-authorization, wave UI-B — documented in
// DESIGN.md and allowlisted in tests/smoke.mjs):
//   sheet.style.setProperty('--sheet-dy', `${dy}px`)
// Finger-following is impossible without a per-frame dynamic offset, and the
// ban on el.style/setProperty writes has no attribute-based encoding for a
// running pixel value. This is the ONLY style write in the entire app and it
// touches ONLY the sheet element's `--sheet-dy` custom property (consumed by
// styles.css as `transform: translateY(var(--sheet-dy, 0px))`). Everything
// else — including the drag state — is plain classes. Under
// prefers-reduced-motion the CSS drops the transform transition (the offset
// still tracks the finger; the settle/dismiss animation is instant).
// ---------------------------------------------------------------------------

const DISMISS_THRESHOLD_PX = 120;
const UPWARD_RUBBER_BAND_PX = 28;

/** Diminishing return when the sheet is pulled above its resting position. */
function rubberBand(distance) {
  return Math.min(UPWARD_RUBBER_BAND_PX, Math.pow(distance, 0.72));
}

function supportsPointerEvents() {
  return typeof window !== 'undefined' && typeof window.PointerEvent === 'function';
}

/**
 * Attach swipe-to-dismiss to a sheet element.
 *   attachSwipeToDismiss(sheet, { close, handle })
 * `close` runs the Esc/cancel close path (NEVER the confirm path). `handle`
 * is the drag surface (defaults to the sheet itself). Returns a detach
 * function. Safe to call once per sheet; repeated calls are ignored.
 */
const attached = new WeakSet();

export function attachSwipeToDismiss(sheet, { close, handle = null, threshold = DISMISS_THRESHOLD_PX } = {}) {
  if (!sheet || typeof close !== 'function') return () => {};
  if (attached.has(sheet)) return () => {};
  attached.add(sheet);

  const dragHandle = handle || sheet;
  const pointerLike = supportsPointerEvents();

  let dragging = false;
  let axis = null;
  let startY = 0;
  let startX = 0;
  let dy = 0;

  // The single sanctioned style write in the app (see the header above).
  const setSheetOffset = (value) => {
    if (sheet.style && typeof sheet.style.setProperty === 'function') {
      sheet.style.setProperty('--sheet-dy', `${Math.round(value)}px`);
    }
  };

  const begin = (clientX, clientY) => {
    dragging = true;
    axis = null;
    startX = clientX;
    startY = clientY;
    dy = 0;
    sheet.classList.add('is-dragging');
  };

  const move = (clientX, clientY) => {
    if (!dragging) return;
    const deltaX = clientX - startX;
    const deltaY = clientY - startY;
    if (axis === null) {
      if (Math.abs(deltaX) > Math.abs(deltaY)) {
        cancel();
        return;
      }
      if (Math.abs(deltaY) < 4) return;
      axis = 'vertical';
    }
    // Upward movement past the resting position gets a rubber-band
    // resistance; downward movement follows the finger 1:1.
    dy = deltaY < 0 ? -rubberBand(-deltaY) : deltaY;
    setSheetOffset(dy);
  };

  const cancel = () => {
    if (!dragging) return;
    dragging = false;
    axis = null;
    dy = 0;
    sheet.classList.remove('is-dragging');
    setSheetOffset(0);
  };

  const finish = () => {
    if (!dragging) return;
    dragging = false;
    axis = null;
    sheet.classList.remove('is-dragging');
    const dismissed = dy >= threshold;
    dy = 0;
    setSheetOffset(0);
    if (dismissed) {
      // Exit transition (CSS class) then close — cancel semantics only: the
      // sheet is dismissed exactly like Esc, it NEVER confirms an action.
      sheet.classList.add('is-closing');
      close();
    }
  };

  const onPointerDown = (event) => {
    if (!event || (event.pointerType === 'mouse' && event.button !== 0)) return;
    begin(event.clientX || 0, event.clientY || 0);
  };
  const onPointerMove = (event) => {
    if (!dragging) return;
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    move(event.clientX || 0, event.clientY || 0);
  };
  const onPointerUp = () => finish();
  const onPointerCancel = () => cancel();

  const onTouchStart = (event) => {
    const touch = event && event.touches && event.touches[0];
    if (!touch) return;
    begin(touch.clientX || 0, touch.clientY || 0);
  };
  const onTouchMove = (event) => {
    if (!dragging) return;
    const touch = event && event.touches && event.touches[0];
    if (!touch) return;
    if (typeof event.preventDefault === 'function') event.preventDefault();
    move(touch.clientX || 0, touch.clientY || 0);
  };
  const onTouchEnd = () => finish();
  const onTouchCancel = () => cancel();

  if (pointerLike) {
    dragHandle.addEventListener('pointerdown', onPointerDown);
    dragHandle.addEventListener('pointermove', onPointerMove);
    dragHandle.addEventListener('pointerup', onPointerUp);
    dragHandle.addEventListener('pointercancel', onPointerCancel);
  } else {
    dragHandle.addEventListener('touchstart', onTouchStart, { passive: true });
    dragHandle.addEventListener('touchmove', onTouchMove, { passive: false });
    dragHandle.addEventListener('touchend', onTouchEnd);
    dragHandle.addEventListener('touchcancel', onTouchCancel);
  }

  return () => {
    attached.delete(sheet);
    if (pointerLike) {
      dragHandle.removeEventListener('pointerdown', onPointerDown);
      dragHandle.removeEventListener('pointermove', onPointerMove);
      dragHandle.removeEventListener('pointerup', onPointerUp);
      dragHandle.removeEventListener('pointercancel', onPointerCancel);
    } else {
      dragHandle.removeEventListener('touchstart', onTouchStart);
      dragHandle.removeEventListener('touchmove', onTouchMove);
      dragHandle.removeEventListener('touchend', onTouchEnd);
      dragHandle.removeEventListener('touchcancel', onTouchCancel);
    }
  };
}

/** Drop the exit state when a sheet re-opens (called by the hosts). */
export function resetSheetState(sheet) {
  if (!sheet || !sheet.classList) return;
  sheet.classList.remove('is-closing', 'is-dragging');
}
