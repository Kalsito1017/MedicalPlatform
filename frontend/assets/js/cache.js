// ---------------------------------------------------------------------------
// cache.js — tiny in-memory stale-while-revalidate cache for GET responses.
//
// Rules (project constraints):
//   * Memory only: the cache lives in this module's Map for the lifetime of the
//     page view (one tab, one document). It is NEVER persisted to web storage
//     of any kind — clinical data must not outlive the page. A full page load
//     therefore starts with a cold cache; within a page view (paging back and
//     forth, re-running a search, revisiting a section) responses render
//     instantly and are revalidated in the background.
//   * The cache is cleared on logout (auth.js) and can be cleared at any time
//     with clearCache(); mutations invalidate their resource with invalidate().
//   * Keys are request URLs (path + query) built by api.buildUrl, so the same
//     key always maps to the same request.
//   * TTL (30s): an entry younger than the TTL is served immediately AND
//     revalidated in the background; a stale entry is treated as absent so the
//     normal loading placeholder shows again. First visit (no entry) is unchanged:
//     heart loader -> data, or a friendly error state on failure.
//   * Background revalidation never replaces working content with an error:
//     when the refetch fails the cached copy simply stays on screen. The DOM is
//     only re-rendered when the fresh payload actually differs (no flicker).
// ---------------------------------------------------------------------------

import { buildUrl } from './api.js';

const TTL_MS = 30000;

const entries = new Map();

/** Cache key for a GET request: the exact request URL (path + query). */
export function keyFor(path, query) {
  return buildUrl(path, query);
}

/** Drop every cached response (logout, user switch, tests). */
export function clearCache() {
  entries.clear();
}

/** Drop cached responses whose key contains `fragment` (after a mutation). */
export function invalidate(fragment) {
  for (const key of [...entries.keys()]) {
    if (key.includes(fragment)) entries.delete(key);
  }
}

function signature(data) {
  try {
    return JSON.stringify(data);
  } catch {
    return String(data);
  }
}

/**
 * Peek at a cached response without touching the network.
 * Returns { data, stale } when an entry exists, null otherwise.
 */
export function peek(key) {
  const entry = entries.get(key);
  if (!entry) return null;
  return { data: entry.data, stale: Date.now() - entry.at >= TTL_MS };
}

function remember(key, data) {
  entries.set(key, { data, at: Date.now(), signature: signature(data) });
}

/**
 * Stale-while-revalidate fetch.
 *
 *   swr(key, fetcher, { onData, onError, onRevalidating })
 *
 * - Fresh entry: onData(cachedData, { cached: true }) runs synchronously, then
 *   fetcher() revalidates in the background and onData(freshData,
 *   { revalidated: true }) runs only when the payload changed.
 * - No (fresh) entry: fetcher() runs, the result is cached and handed to
 *   onData(data, { cached: false }); failures go to onError(error).
 * - onRevalidating(true|false) brackets the background refetch so callers can
 *   show a tiny "Updating…" indicator.
 *
 * The returned promise resolves when the initial work (including background
 * revalidation) has settled; it never rejects — errors are routed to onError.
 */
export async function swr(key, fetcher, { onData, onError, onRevalidating } = {}) {
  const entry = entries.get(key);
  const fresh = entry && Date.now() - entry.at < TTL_MS;

  if (fresh) {
    if (typeof onData === 'function') onData(entry.data, { cached: true });
    if (typeof onRevalidating === 'function') onRevalidating(true);
    try {
      const data = await fetcher();
      if (signature(data) !== entry.signature) {
        remember(key, data);
        if (typeof onData === 'function') onData(data, { cached: false, revalidated: true });
      } else {
        entry.at = Date.now();
      }
    } catch {
      // Background revalidation failed: keep serving the cached copy. The
      // error state is reserved for loads with nothing to show.
    }
    if (typeof onRevalidating === 'function') onRevalidating(false);
    return;
  }

  entries.delete(key);
  try {
    const data = await fetcher();
    remember(key, data);
    if (typeof onData === 'function') onData(data, { cached: false });
  } catch (error) {
    if (typeof onError === 'function') onError(error);
  }
}
