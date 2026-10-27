// ---------------------------------------------------------------------------
// prefetch.js — pre-rendering: module warm-up + hover/focus intent prefetch.
//
// Two mechanisms (DESIGN.md "Pre-rendering and intent prefetch"):
//
//   1. Module warm-up. Shell pages already declare static
//      <link rel="modulepreload"> for their shared graph (ui/api/auth/cache/
//      command-menu); on idle we additionally warm the NEXT-likely page
//      modules the current page links to (e.g. patient.js from the patients
//      list) by injecting the same <link rel="modulepreload"> hints. That
//      fetches+parses the module WITHOUT executing it (a dynamic import would
//      RUN the page module, so link hints are the correct primitive here).
//      requestIdleCallback with a setTimeout fallback.
//
//   2. Hover-intent / focus-intent prefetch of patient-record data. Hovering
//      or tabbing onto a patient card or a "Go to patient" command-menu entry
//      (debounced ~150ms) warms the patient-detail GETs through cache.js so
//      the detail page paints instantly whenever the cache is still warm.
//
// PRIVACY STANCE (documented in DESIGN.md):
//   * Permission-gated: nothing is prefetched unless the signed-in user holds
//     patient:read (checked against the /auth/me payload — never beyond the
//     authenticated user's permissions; the backend still re-checks every
//     request). Anonymous users get zero prefetches.
//   * In-memory only: warmed responses live in cache.js's per-document Map.
//     Nothing is written to web storage, nothing is logged, no speculative
//     request is ever sent without an authenticated user context.
//   * Cache-TTL respecting: an entry that is still fresh is not re-fetched.
//   * Scope limit (honest): the app is multi-page, so a full cross-page
//     navigation starts a new document and the in-memory cache cannot carry
//     over — the warm-up pays off for within-page revisits (tabs, back/forward
//     in a page view, re-opened sections) and module parse time. Persisting
//     prefetched clinical payloads across documents would require web storage
//     (banned for clinical data) or a service worker (out of scope).
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as cache from './cache.js';

const INTENT_DELAY_MS = 150;
const warmedModules = new Set();
const warmingKeys = new Set();

/** Same UX gate as auth.hasPermission (kept inline: no import cycle with auth.js). */
function canReadPatients(user) {
  return Boolean(
    user &&
      Array.isArray(user.permissions) &&
      user.permissions.includes('patient:read')
  );
}

/** Inject one <link rel="modulepreload"> hint (once per href). */
export function modulePreload(href) {
  if (typeof document === 'undefined' || typeof document.createElement !== 'function') return null;
  const target = String(href || '');
  if (target === '' || warmedModules.has(target)) return null;
  warmedModules.add(target);
  const link = document.createElement('link');
  link.setAttribute('rel', 'modulepreload');
  link.setAttribute('href', target);
  const parent = document.head || document.body;
  if (parent && typeof parent.append === 'function') parent.append(link);
  return link;
}

/** Run `fn` when the browser is idle (requestIdleCallback, setTimeout fallback). */
export function onIdle(fn, { timeout = 2000, schedule = null } = {}) {
  if (typeof fn !== 'function') return;
  const runner = typeof schedule === 'function' ? schedule : null;
  if (runner) {
    runner(fn);
    return;
  }
  if (typeof requestIdleCallback === 'function') {
    requestIdleCallback(() => fn(), { timeout });
    return;
  }
  const timer = setTimeout(fn, 0);
  if (timer && typeof timer.unref === 'function') timer.unref();
}

/** Warm the modules a navigation from this page will likely need. */
export function warmModules(hrefs, options = {}) {
  const list = [hrefs].flat().filter(Boolean);
  if (list.length === 0) return;
  onIdle(() => {
    for (const href of list) modulePreload(href);
  }, options);
}

/**
 * Warm the patient-detail API data through cache.js (in-memory only).
 * No-op without patient:read, without an id, or while a fresh cache entry
 * exists (cache TTL respected).
 */
export function warmPatientRecord(patientId, user) {
  if (typeof patientId !== 'string' || patientId.trim() === '') return;
  if (!canReadPatients(user)) return;
  const id = encodeURIComponent(patientId.trim());
  for (const path of [`/patients/${id}`, `/patients/${id}/summary`]) {
    const key = cache.keyFor(path);
    const entry = cache.peek(key);
    if (entry && !entry.stale) continue; // fresh within TTL: nothing to do
    if (warmingKeys.has(key)) continue;
    warmingKeys.add(key);
    cache.swr(key, () => api.get(path)).finally(() => {
      warmingKeys.delete(key);
    });
  }
}

function patientIdOf(node) {
  if (!node) return null;
  if (typeof node.closest === 'function') {
    const holder = node.closest('[data-prefetch-patient]');
    return holder ? holder.getAttribute('data-prefetch-patient') : null;
  }
  if (typeof node.getAttribute === 'function') {
    return node.getAttribute('data-prefetch-patient');
  }
  return null;
}

const wired = new WeakSet();

/**
 * Hover-intent / focus-intent wiring: elements carrying
 * `data-prefetch-patient="<id>"` warm that patient's detail data after the
 * user has hovered (pointerenter) or tabbed onto them (focusin) for
 * ~150ms — a stray mouse pass never fires a request. Idempotent per element.
 */
export function wirePatientIntent(root, user, { delay = INTENT_DELAY_MS } = {}) {
  if (!root || typeof root.querySelectorAll !== 'function') return;
  if (!canReadPatients(user)) return;
  for (const node of root.querySelectorAll('[data-prefetch-patient]')) {
    if (wired.has(node)) continue;
    wired.add(node);
    let timer = null;
    const warm = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      timer = setTimeout(() => {
        timer = null;
        warmPatientRecord(patientIdOf(node), user);
      }, delay);
      if (timer && typeof timer.unref === 'function') timer.unref();
    };
    node.addEventListener('pointerenter', warm);
    node.addEventListener('focusin', warm);
  }
}
