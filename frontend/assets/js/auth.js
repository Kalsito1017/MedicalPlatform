// ---------------------------------------------------------------------------
// auth.js — session, login/logout, and role-aware navigation.
// The session itself lives in an HttpOnly cookie (docs/API.md); this module
// stores no tokens or user data in web storage.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as ui from './ui.js';

/** GET /api/v1/auth/me — current user payload or null when anonymous. */
export async function currentSession() {
  try {
    return await api.get('/auth/me');
  } catch (error) {
    if (error instanceof api.ApiError && error.status === 401) return null;
    throw error;
  }
}

export function hasPermission(user, permission) {
  return Boolean(
    user &&
      Array.isArray(user.permissions) &&
      user.permissions.includes(permission)
  );
}

export function hasAnyPermission(user, permissions) {
  return permissions.some((permission) => hasPermission(user, permission));
}

/**
 * Navigation is derived from the /auth/me permission list. Hiding items is UX
 * only: the backend enforces every permission server-side, and pages render a
 * friendly denial when a user opens one directly.
 */
export function navItemsFor(user) {
  const items = [{ key: 'dashboard', label: 'Dashboard', href: 'dashboard.html' }];
  if (hasPermission(user, 'patient:read')) {
    items.push({ key: 'patients', label: 'Patients', href: 'patients.html' });
  }
  if (hasAnyPermission(user, ['staff:read', 'staff:manage', 'role:assign'])) {
    items.push({ key: 'admin', label: 'Admin', href: 'admin.html' });
  }
  return items;
}

/** POST /api/v1/auth/login. Throws ApiError; the caller renders the message. */
export async function login(email, password) {
  // Bootstrap the CSRF cookie if the backend issues one on unauthenticated
  // GETs (harmless if it does not; the header is simply omitted then).
  try {
    await api.get('/auth/me');
  } catch {
    // 401 (anonymous) and network failures are both acceptable here.
  }
  return api.post('/auth/login', { email, password });
}

/** POST /api/v1/auth/logout. A 401 means the session is already gone. */
export async function logout() {
  try {
    await api.post('/auth/logout', {});
  } catch (error) {
    if (!(error instanceof api.ApiError && error.status === 401)) throw error;
  }
  window.location.assign('index.html');
}

/**
 * Shared entry point for authenticated pages: validates the session, builds
 * the app chrome (nav + user area + sign out), and returns the user payload.
 * Returns null after redirecting to index.html when the session is missing.
 * Throws when the backend is unreachable so the page can render an error state.
 */
export async function initAuthChrome({ active } = {}) {
  const user = await currentSession();

  if (!user) {
    window.location.replace('index.html');
    return null;
  }

  ui.initChrome({
    user,
    active,
    navItems: navItemsFor(user),
    onLogout: logout,
  });
  return user;
}
