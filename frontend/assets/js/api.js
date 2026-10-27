// ---------------------------------------------------------------------------
// api.js — fetch client for the frozen API contract in docs/API.md.
//
// Contract points implemented here:
//   * Base path /api/v1 (see config.js for the override mechanism).
//   * Error envelope { error: { code, message, request_id, details } } for all
//     non-2xx JSON responses; converted to ApiError instances.
//   * Pagination shape { items, total, limit, offset } for list endpoints.
//   * Cookie session: the backend session cookie is HttpOnly, so nothing is
//     stored in localStorage/sessionStorage. Requests send credentials.
//   * CSRF double-submit: non-GET requests carry `X-CSRF-Token` matching the
//     `csrf_token` cookie when that cookie exists. (If the backend only issues
//     `csrf_token` after authentication, login requests legitimately carry no
//     header; auth.login() bootstraps the cookie with a cheap GET first.)
//   * 429 responses expose Retry-After (seconds) on ApiError.retryAfter.
//   * X-Request-Id / error.request_id is preserved for bug reports.
//   * Idempotency (docs/API.md "Idempotent mutations"): every mutating request
//     (POST/PATCH/DELETE) carries a fresh `Idempotency-Key`
//     (crypto.randomUUID; a random fallback for older engines). The key is a
//     random token - it NEVER encodes or contains clinical data or anything
//     user-authored. A retry of the SAME logical request reuses its key (pass
//     `idempotencyKey` through the options); a NEW request always gets a fresh
//     key. GET/HEAD/OPTIONS never send the header.
// ---------------------------------------------------------------------------

import { API_BASE_URL } from './config.js';

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

/**
 * Fresh idempotency key: a random UUID (crypto.randomUUID where available,
 * 128 bits of randomness otherwise). Never derived from request contents.
 */
export function newIdempotencyKey() {
  const cryptoObj = typeof globalThis !== 'undefined' ? globalThis.crypto : null;
  if (cryptoObj && typeof cryptoObj.randomUUID === 'function') return cryptoObj.randomUUID();
  const bytes = new Uint8Array(16);
  if (cryptoObj && typeof cryptoObj.getRandomValues === 'function') {
    cryptoObj.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** Application error with the contract's error-envelope fields. */
export class ApiError extends Error {
  constructor({
    status = 0,
    code = 'unknown_error',
    message = '',
    requestId = null,
    details = null,
    retryAfter = null,
  } = {}) {
    super(message || code);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
    this.retryAfter = retryAfter;
  }
}

/** Build an absolute/relative request URL with bounded query encoding. */
export function buildUrl(path, query) {
  const base = API_BASE_URL.replace(/\/+$/, '');
  const suffix = path.startsWith('/') ? path : `/${path}`;
  const url = `${base}${suffix}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

/** Parse a `document.cookie`-style string for the CSRF cookie value. */
export function csrfTokenFromCookie(cookieString) {
  if (typeof cookieString !== 'string' || cookieString.length === 0) return null;
  for (const part of cookieString.split(';')) {
    const separator = part.indexOf('=');
    if (separator < 0) continue;
    const name = part.slice(0, separator).trim();
    if (name !== 'csrf_token') continue;
    const raw = part.slice(separator + 1).trim();
    try {
      return decodeURIComponent(raw);
    } catch {
      return raw;
    }
  }
  return null;
}

function readCsrfCookie() {
  if (typeof document === 'undefined') return null;
  return csrfTokenFromCookie(document.cookie);
}

const STATUS_FALLBACK_CODES = {
  400: 'validation_error',
  401: 'unauthenticated',
  403: 'forbidden',
  404: 'not_found',
  409: 'conflict',
  429: 'rate_limited',
  500: 'internal_error',
  503: 'dependency_unavailable',
};

async function parseBody(response) {
  // Parse JSON whenever possible (tolerant of missing content-type headers,
  // e.g. from proxies); anything unparseable becomes null.
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/**
 * Core request helper. Resolves with the parsed JSON body (null for 204),
 * rejects with ApiError for every failure mode, including network failure
 * (status 0, code 'network_error').
 */
export async function request(path, { method = 'GET', body, query, signal, idempotencyKey } = {}) {
  const upperMethod = method.toUpperCase();
  const headers = { Accept: 'application/json' };

  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (!SAFE_METHODS.has(upperMethod)) {
    const csrf = readCsrfCookie();
    if (csrf) headers['X-CSRF-Token'] = csrf;
    // Fresh key per mutating request; a retry of the same logical request
    // reuses the key passed via `idempotencyKey` (see header comment).
    headers['Idempotency-Key'] = idempotencyKey || newIdempotencyKey();
  }

  let response;
  try {
    response = await fetch(buildUrl(path, query), {
      method: upperMethod,
      headers,
      credentials: 'include',
      cache: 'no-store',
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (networkFailure) {
    if (networkFailure && networkFailure.name === 'AbortError') throw networkFailure;
    throw new ApiError({
      status: 0,
      code: 'network_error',
      message: 'The demo backend could not be reached.',
    });
  }

  const data = await parseBody(response);
  const requestId =
    (data && data.error && data.error.request_id) ||
    response.headers.get('X-Request-Id');

  if (!response.ok) {
    const envelope = (data && data.error) || {};
    const retryAfterHeader = response.headers.get('Retry-After');
    const retryAfter =
      envelope.retry_after != null
        ? Number(envelope.retry_after)
        : retryAfterHeader != null && retryAfterHeader !== ''
          ? Number(retryAfterHeader)
          : null;

    throw new ApiError({
      status: response.status,
      code: envelope.code || STATUS_FALLBACK_CODES[response.status] || 'unknown_error',
      message: typeof envelope.message === 'string' ? envelope.message : '',
      requestId: requestId || null,
      details: envelope.details || null,
      retryAfter: Number.isFinite(retryAfter) ? retryAfter : null,
    });
  }

  return data;
}

/**
 * GET helpers. The second argument is the query object (contract pagination:
 * ?limit=&offset=&q=), the third an escape hatch for { signal }.
 */
export function get(path, query, options) {
  return request(path, { ...options, method: 'GET', query });
}

export function post(path, body, options) {
  return request(path, { ...options, method: 'POST', body });
}

export function patch(path, body, options) {
  return request(path, { ...options, method: 'PATCH', body });
}

export function del(path, query, options) {
  return request(path, { ...options, method: 'DELETE', query });
}

/**
 * Pure presentation mapping for ApiError. Never surfaces raw error objects,
 * stack traces, or internal details.
 */
export function describeApiError(error) {
  const isApiError = error instanceof ApiError;
  const status = isApiError ? error.status : 0;
  const serverMessage =
    isApiError && typeof error.message === 'string' ? error.message.trim() : '';

  switch (status) {
    case 0:
      return {
        title: 'Backend unreachable',
        message:
          'We cannot reach the demo backend right now. It may be starting up, offline, or blocked by a network problem. Check your connection and try again.',
        retryable: true,
      };
    case 400:
      return {
        title: 'Please check your input',
        message: serverMessage || 'Some of the submitted values were not accepted.',
        retryable: false,
      };
    case 401:
      return {
        title: 'Session expired',
        message: 'Your session has expired or you are signed out. Please sign in again.',
        retryable: true,
      };
    case 403:
      return {
        title: 'You do not have permission',
        message:
          serverMessage ||
          'Your account does not have permission for this. Access rules are enforced by the backend.',
        retryable: false,
      };
    case 404:
      return {
        title: 'Not found',
        message:
          'We could not find this record. It may not exist, or it may be hidden for your account.',
        retryable: false,
      };
    case 409:
      return {
        title: 'Action not allowed right now',
        message:
          serverMessage ||
          'This action conflicts with the current state of the account or role setup.',
        retryable: false,
      };
    case 429: {
      const seconds = isApiError && error.retryAfter ? Math.ceil(error.retryAfter) : null;
      return {
        title: 'Too many requests',
        message: seconds
          ? `This endpoint is rate limited. Please wait about ${seconds} seconds and try again.`
          : 'This endpoint is rate limited. Please wait a moment and try again.',
        retryable: true,
      };
    }
    case 500:
      return {
        title: 'Something went wrong',
        message:
          'The demo backend hit an unexpected error. Nothing was changed. Please try again.',
        retryable: true,
      };
    case 503:
      return {
        title: 'Service temporarily unavailable',
        message:
          serverMessage ||
          'A backend service is temporarily unavailable. The rest of the demo keeps working; please try again shortly.',
        retryable: true,
      };
    default:
      return {
        title: 'Something went wrong',
        message: serverMessage || 'An unexpected problem occurred. Please try again.',
        retryable: true,
      };
  }
}
