// ---------------------------------------------------------------------------
// Deployment configuration
//
// This site is buildless static HTML/CSS/JS. The API base URL is resolved in
// this order:
//
//   1. window.__APP_CONFIG__.API_BASE_URL
//      A tiny inline script in the <head> of every page creates
//      window.__APP_CONFIG__. To point a deployment at a hosted backend
//      without touching application code, edit that inline script in the
//      pages (or inject it at deploy time) to:
//
//          window.__APP_CONFIG__ = { API_BASE_URL: "https://<backend-host>/api/v1" };
//
//   2. DEFAULT_API_BASE_URL below.
//
// The value is the backend origin plus the frozen API base path `/api/v1`
// (see docs/API.md). The default is the same-origin path `/api/v1`, which
// works when the FastAPI backend serves or proxies the API under that path.
// No secrets belong here: the session is an HttpOnly cookie issued by the
// backend; this file must never hold credentials or API keys.
// ---------------------------------------------------------------------------

const DEFAULT_API_BASE_URL = '/api/v1';

const runtimeConfig =
  (typeof window !== 'undefined' && window.__APP_CONFIG__) || {};

const override =
  typeof runtimeConfig.API_BASE_URL === 'string'
    ? runtimeConfig.API_BASE_URL.trim()
    : '';

export const API_BASE_URL = (override || DEFAULT_API_BASE_URL).replace(
  /\/+$/,
  ''
);
