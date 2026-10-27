// ---------------------------------------------------------------------------
// admin.html page module — staff administration for the synthetic demo.
// Endpoints (docs/API.md):
//   GET    /api/v1/staff                        -> Page {items,total,limit,offset}
//   POST   /api/v1/staff                        -> 201 {user, generated_password}
//   PATCH  /api/v1/staff/{user_id}              -> StaffUserOut (display_name, is_active)
//   POST   /api/v1/staff/{user_id}/roles        -> StaffUserOut {role}
//   DELETE /api/v1/staff/{user_id}/roles/{role} -> 204 (409 last-admin, 404 not held)
//   GET    /api/v1/audit                        -> Page of audit events (audit:read)
// StaffCreate forbids unknown fields, so creation sends {email, display_name,
// password?} only; the optional role is granted right afterwards through the
// dedicated role endpoint. Never rendered here: password hashes, tokens,
// sessions, or secret configuration — the API never returns them.
// High-impact actions (role grant, role revoke, deactivate) always confirm via
// ui.confirmDialog() with an explicit consequence sentence, then apply
// OPTIMISTICALLY: the row updates immediately and the API call runs behind it.
// On failure the row rolls back to the last server truth and an explanatory
// error toast appears (api.describeApiError). Reversible actions additionally
// get an undo toast (revoke role, deactivate account); see DESIGN.md.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as cache from './cache.js';
import * as commandMenu from './command-menu.js';
import * as prefetch from './prefetch.js';
import * as toast from './toast.js';
import * as ui from './ui.js';

const ADMIN_PERMISSIONS = ['staff:read', 'staff:manage', 'role:assign'];
const PAGE_SIZE = 20;
const AUDIT_PAGE_SIZE = 10;
const PASSWORD_MIN = 8;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ROLE_KEYS = ['doctor', 'nurse', 'clinical_admin'];

// Role consequences, stated in the confirmation dialog before every grant.
const ROLE_ABILITY = {
  doctor: 'view patient records and clinical data permitted by the demo policy (labs and prescriptions)',
  nurse: 'view permitted patient information and clinical data (least privilege, with no staff or audit administration)',
  clinical_admin: 'do everything in the demo: view clinical data, manage staff accounts and roles, read the audit log, and view analytics',
};

const state = {
  me: null,
  users: [],
  offset: 0,
  total: 0,
  auditOffset: 0,
  auditTotal: 0,
};

function canManage() {
  return auth.hasPermission(state.me, 'staff:manage');
}

function canAssign() {
  return auth.hasPermission(state.me, 'role:assign');
}

/* ------------------------------------------------------------- form fields */

function setFieldError(input, message) {
  const errorNode = document.getElementById(`${input.id}-error`);
  if (message) {
    input.setAttribute('aria-invalid', 'true');
    if (errorNode) {
      errorNode.textContent = message;
      errorNode.hidden = false;
    }
  } else {
    input.removeAttribute('aria-invalid');
    if (errorNode) {
      errorNode.textContent = '';
      errorNode.hidden = true;
    }
  }
}

/* --------------------------------------------------------------- outcomes */

// Mutation results from the staff table land in #staff-status (aria-live) and
// receive keyboard focus, so the outcome is announced and reachable.
function announceStatus(host, message) {
  const status = document.getElementById(host);
  if (!status) return;
  ui.clear(status);
  status.append(ui.el('p', { text: message }));
  if (host === 'staff-status') status.focus();
}

function announceError(host, error) {
  const status = document.getElementById(host);
  if (!status) return;
  const info = api.describeApiError(error);
  ui.clear(status);
  status.append(
    ui.el('div', { class: 'alert alert--danger', role: 'alert' },
      ui.el('p', { text: `${info.title}. ${info.message}` }),
      error && error.requestId
        ? ui.el('p', { class: 'state-panel__meta', text: `Request ID: ${error.requestId}` })
        : null,
    ),
  );
  if (host === 'staff-status') status.focus();
}

/* ------------------------------------------------------------ staff table */

function roleBadges(roles) {
  if (!Array.isArray(roles) || roles.length === 0) {
    return ui.el('span', { class: 'text-muted', text: 'No roles' });
  }
  return ui.el('div', { class: 'badge-list' }, roles.map(ui.roleBadge));
}

function renderRowActions(user) {
  const wrap = ui.el('div', { class: 'row-actions' });
  const roles = Array.isArray(user.roles) ? user.roles : [];

  if (canAssign()) {
    const grantable = ROLE_KEYS.filter((role) => !roles.includes(role));
    const select = ui.el('select', { 'aria-label': `Role to grant to ${user.display_name}` },
      ui.el('option', { value: '' }, 'Choose a role'),
      grantable.map((role) => ui.el('option', { value: role }, ui.roleLabel(role))),
    );
    let grantButton = null;
    grantButton = ui.el('button', {
      class: 'btn btn-secondary btn-sm hit-slop',
      type: 'button',
      onClick: () => {
        grantRole(user, select.value, grantButton);
      },
    }, 'Grant role');
    grantButton.disabled = true;
    select.addEventListener('change', () => {
      grantButton.disabled = select.value === '';
    });
    wrap.append(ui.el('div', { class: 'row-actions__group' }, select, grantButton));

    for (const role of roles) {
      let revokeButton = null;
      revokeButton = ui.el('button', {
        class: 'btn btn-ghost btn-sm hit-slop',
        type: 'button',
        'aria-label': `Revoke ${ui.roleLabel(role)} from ${user.display_name}`,
        onClick: () => {
          revokeRole(user, role, revokeButton);
        },
      }, `Revoke ${ui.roleLabel(role)}`);
      wrap.append(revokeButton);
    }
    if (grantable.length === 0) {
      wrap.append(ui.el('p', { class: 'field-hint', text: 'This account already holds every role.' }));
    }
  }

  if (canManage()) {
    let toggleButton = null;
    toggleButton = ui.el('button', {
      class: user.is_active ? 'btn btn-danger btn-sm hit-slop' : 'btn btn-secondary btn-sm hit-slop',
      type: 'button',
      onClick: () => {
        toggleActive(user, toggleButton);
      },
    }, user.is_active ? 'Deactivate' : 'Activate');
    wrap.append(toggleButton);
  }

  return wrap;
}

function renderStaffRow(user) {
  const cells = [
    ui.el('td', { 'data-label': 'Staff member' },
      ui.el('div', { class: 'cell-stack' },
        ui.avatar(user.display_name || user.email || '?', { size: 'sm' }),
        ui.el('div', { class: 'cell-stack__text' },
          ui.el('span', { class: 'cell-stack__primary', text: user.display_name || 'Not recorded' }),
          ui.el('span', { class: 'cell-stack__secondary', text: user.email || 'Not recorded' }),
        ),
      ),
    ),
    ui.el('td', { 'data-label': 'Roles' }, roleBadges(user.roles)),
    ui.el('td', { 'data-label': 'Status' },
      user.is_active
        ? ui.el('span', { class: 'badge badge--ok', text: 'Active' })
        : ui.el('span', { class: 'badge badge--neutral', text: 'Inactive' }),
    ),
    ui.el('td', { 'data-label': 'Created', text: ui.displayDate(user.created_at) }),
  ];
  if (canManage() || canAssign()) {
    cells.push(ui.el('td', { 'data-label': 'Actions' }, renderRowActions(user)));
  }
  return ui.el('tr', {}, cells);
}

function renderStaffTable() {
  const region = document.getElementById('staff-region');
  ui.clear(region);

  if (state.users.length === 0) {
    ui.renderEmptyState(region, {
      title: 'No staff accounts',
      message: 'This demo database has no staff accounts yet. Create one above.',
      headingTag: 'h3',
    });
    return;
  }

  const columns = ['Staff member', 'Roles', 'Status', 'Created'];
  if (canManage() || canAssign()) columns.push('Actions');

  const table = ui.el('table', { class: 'table table-stack tabular-nums' },
    ui.el('thead', {},
      ui.el('tr', {}, columns.map((label) => ui.el('th', { scope: 'col', text: label }))),
    ),
    ui.el('tbody', {}, state.users.map(renderStaffRow)),
  );

  region.append(
    ui.el('div', {
      class: 'table-scroll',
      role: 'region',
      'aria-label': 'Staff accounts',
      tabindex: '0',
    }, table),
  );
  const paginationHost = ui.el('div', {});
  region.append(paginationHost);
  ui.renderPagination(paginationHost, {
    total: state.total,
    limit: PAGE_SIZE,
    offset: state.offset,
    count: state.users.length,
    itemLabel: 'staff accounts',
    onChange: (nextOffset) => {
      state.offset = nextOffset;
      loadStaff();
    },
  });
}

function loadStaff() {
  const region = document.getElementById('staff-region');
  const key = cache.keyFor('/staff', { limit: PAGE_SIZE, offset: state.offset });
  if (!cache.peek(key)) {
    ui.renderLoading(region, { label: 'Loading staff accounts', rows: 5 });
  }
  cache.swr(key, () => api.get('/staff', { limit: PAGE_SIZE, offset: state.offset }), {
    onData: (page) => {
      const items = Array.isArray(page.items) ? page.items : [];
      state.users = items;
      state.total = Number.isFinite(page.total) ? page.total : items.length;
      renderStaffTable();
      const status = document.getElementById('staff-status');
      if (status) {
        status.textContent = state.total === 1
          ? '1 staff account found.'
          : `${state.total} staff accounts found.`;
      }
    },
    onError: (error) => {
      ui.renderErrorState(region, error, {
        describe: api.describeApiError,
        onRetry: loadStaff,
      });
    },
    onRevalidating: (on) => ui.setSyncIndicator(region, on),
  });
}

// Re-renders the table from state.users after a successful mutation so the
// affected row reflects the fresh StaffUserOut returned by the API.
function replaceUser(updated) {
  const index = state.users.findIndex((user) => user.id === updated.id);
  if (index >= 0) state.users[index] = updated;
  renderStaffTable();
}

/** Current row state for a user id (used by optimistic rollback/undo). */
function userById(userId) {
  return state.users.find((user) => user.id === userId) || null;
}

// Optimistic apply: swap the row instantly, then reconcile with the server.
// On failure the row returns to the last server truth and the caller explains.
function applyOptimistic(updated) {
  replaceUser(updated);
  cache.invalidate('/staff');
}

function rollbackOptimistic(previous) {
  cache.invalidate('/staff');
  replaceUser(previous);
}

/* ---------------------------------------------------------- role changes */

async function grantRole(user, role, button) {
  if (!role) return;
  const isSelf = Boolean(state.me && user.id === state.me.id);
  const message =
    `Grant ${ui.roleLabel(role)} to ${user.display_name}? ` +
    `This grants ${user.display_name} the ability to ${ROLE_ABILITY[role]}.` +
    (isSelf
      ? ' This change targets your own account: the backend refuses self-escalation (403) whenever the role adds permissions you do not already hold.'
      : '');

  const confirmed = await ui.confirmDialog({
    title: 'Grant role',
    message,
    confirmLabel: 'Grant role',
    details: [
      { label: 'Name', value: user.display_name || 'Not recorded' },
      { label: 'Email', value: user.email || 'Not recorded' },
      { label: 'Role', value: ui.roleLabel(role) },
    ],
  });
  if (!confirmed) return;

  // Optimistic: the row shows the new role immediately.
  const previous = user;
  const roles = Array.isArray(user.roles) ? user.roles : [];
  applyOptimistic({ ...user, roles: roles.includes(role) ? roles : [...roles, role] });

  ui.setBusy(button, true, 'Granting…');
  try {
    const updated = await api.post(
      `/staff/${encodeURIComponent(user.id)}/roles`,
      { role },
    );
    cache.invalidate('/staff');
    if (updated && updated.id) replaceUser(updated);
    else loadStaff();
    announceStatus('staff-status', `Role granted: ${ui.roleLabel(role)} is now held by ${user.display_name}.`);
  } catch (error) {
    rollbackOptimistic(previous);
    announceError('staff-status', error);
    toast.showErrorToast(error);
  }
}

async function revokeRole(user, role, button) {
  const confirmed = await ui.confirmDialog({
    title: 'Revoke role',
    message:
      `Revoke ${ui.roleLabel(role)} from ${user.display_name}? ` +
      `This removes their ability to ${ROLE_ABILITY[role]}.`,
    confirmLabel: 'Revoke role',
    details: [
      { label: 'Name', value: user.display_name || 'Not recorded' },
      { label: 'Email', value: user.email || 'Not recorded' },
      { label: 'Role', value: ui.roleLabel(role) },
    ],
  });
  if (!confirmed) return;

  // Optimistic: the role disappears from the row immediately.
  const previous = user;
  const roles = Array.isArray(user.roles) ? user.roles : [];
  applyOptimistic({ ...user, roles: roles.filter((held) => held !== role) });

  ui.setBusy(button, true, 'Revoking…');
  try {
    await api.del(
      `/staff/${encodeURIComponent(user.id)}/roles/${encodeURIComponent(role)}`,
    );
    cache.invalidate('/staff');
    announceStatus('staff-status', `Role revoked: ${ui.roleLabel(role)} was removed from ${user.display_name}.`);
    toast.showToast({
      message: `Role removed: ${ui.roleLabel(role)} was revoked from ${user.display_name}.`,
      actionLabel: 'Undo',
      onAction: () => restoreRole(user, role),
    });
  } catch (error) {
    rollbackOptimistic(previous);
    announceError('staff-status', error);
    toast.showErrorToast(error);
  }
}

/** Undo of a role revoke: re-grant optimistically and reflect the result. */
async function restoreRole(user, role) {
  const current = userById(user.id) || user;
  const previous = current;
  const roles = Array.isArray(current.roles) ? current.roles : [];
  if (!roles.includes(role)) {
    applyOptimistic({ ...current, roles: [...roles, role] });
  }
  try {
    const updated = await api.post(
      `/staff/${encodeURIComponent(user.id)}/roles`,
      { role },
    );
    cache.invalidate('/staff');
    if (updated && updated.id) replaceUser(updated);
    announceStatus('staff-status', `Restored: ${ui.roleLabel(role)} is held by ${user.display_name} again.`);
    toast.showToast({ message: 'Restored.' });
  } catch (error) {
    rollbackOptimistic(previous);
    announceError('staff-status', error);
    toast.showErrorToast(error);
  }
}

/* ------------------------------------------------------ activate/deactivate */

async function toggleActive(user, button) {
  const activating = !user.is_active;
  const confirmed = await ui.confirmDialog({
    title: activating ? 'Activate account' : 'Deactivate account',
    message: activating
      ? `Reactivate ${user.display_name}? They will be able to sign in again with their existing password.`
      : `Deactivate ${user.display_name}? Deactivating immediately ends this person's sessions. ` +
        'They will be signed out at once and cannot sign in again until this account is reactivated.',
    confirmLabel: activating ? 'Activate' : 'Deactivate',
    danger: !activating,
    details: [
      { label: 'Name', value: user.display_name || 'Not recorded' },
      { label: 'Email', value: user.email || 'Not recorded' },
      { label: 'Status now', value: user.is_active ? 'Active' : 'Inactive' },
    ],
  });
  if (!confirmed) return;

  // Optimistic: the status badge flips immediately.
  const previous = user;
  applyOptimistic({ ...user, is_active: activating });

  ui.setBusy(button, true, activating ? 'Activating…' : 'Deactivating…');
  try {
    const updated = await api.patch(
      `/staff/${encodeURIComponent(user.id)}`,
      { is_active: activating },
    );
    cache.invalidate('/staff');
    if (updated && updated.id) replaceUser(updated);
    else loadStaff();
    announceStatus(
      'staff-status',
      activating
        ? `${user.display_name} can sign in again.`
        : `${user.display_name} is deactivated and their sessions have ended.`,
    );
    if (!activating) {
      toast.showToast({
        message: `Account deactivated: ${user.display_name} cannot sign in until this account is reactivated.`,
        actionLabel: 'Undo',
        onAction: () => restoreActive(user),
      });
    }
  } catch (error) {
    rollbackOptimistic(previous);
    announceError('staff-status', error);
    toast.showErrorToast(error);
  }
}

/** Undo of a deactivation: reactivate optimistically and reflect the result. */
async function restoreActive(user) {
  const current = userById(user.id) || user;
  const previous = current;
  applyOptimistic({ ...current, is_active: true });
  try {
    const updated = await api.patch(
      `/staff/${encodeURIComponent(user.id)}`,
      { is_active: true },
    );
    cache.invalidate('/staff');
    if (updated && updated.id) replaceUser(updated);
    announceStatus('staff-status', `${user.display_name} can sign in again.`);
    toast.showToast({ message: 'Restored.' });
  } catch (error) {
    rollbackOptimistic(previous);
    announceError('staff-status', error);
    toast.showErrorToast(error);
  }
}

/* ----------------------------------------------------------- create staff */

function showOneTimePassword(user, password) {
  const feedback = ui.el('p', {
    class: 'field-hint',
    role: 'status',
    'aria-live': 'polite',
    text: '',
  });

  const copyButton = ui.el('button', {
    class: 'btn btn-secondary btn-sm hit-slop',
    type: 'button',
    onClick: async () => {
      const nav = typeof navigator !== 'undefined' ? navigator : null;
      if (nav && nav.clipboard && typeof nav.clipboard.writeText === 'function') {
        try {
          await nav.clipboard.writeText(password);
          feedback.textContent = 'Password copied to the clipboard.';
          return;
        } catch {
          // Clipboard denied or unavailable: fall through to manual copy.
        }
      }
      feedback.textContent =
        'Automatic copy is unavailable here. Select the password text above and copy it manually.';
    },
  }, 'Copy password');

  const modal = { close: null };
  const closeButton = ui.el('button', {
    class: 'btn btn-primary hit-slop',
    type: 'button',
    onClick: () => {
      if (modal.close) modal.close();
    },
  }, 'Close');

  const handle = ui.openDialog({
    title: `One-time password for ${user.display_name}`,
    bodyNodes: [
      ui.el('div', { class: 'alert alert--info', role: 'alert' },
        ui.el('p', { text: 'This is shown once. Store it now.' }),
        ui.el('p', {
          text: 'The demo backend stores only a hash and will never show this password again. Do not use real credentials in this demo.',
        }),
      ),
      ui.el('div', { class: 'field' },
        ui.el('p', {
          class: 'field-hint',
          id: 'generated-password-label',
          text: `Generated password for ${user.email}`,
        }),
        ui.el('p', {
          class: 'password-display',
          id: 'generated-password-value',
          text: password,
        }),
        feedback,
      ),
      copyButton,
    ],
    actionNodes: [closeButton],
  });
  modal.close = handle.close;
}

function initCreateForm() {
  const form = document.getElementById('create-staff-form');
  const emailInput = document.getElementById('create-email');
  const nameInput = document.getElementById('create-name');
  const passwordInput = document.getElementById('create-password');
  const roleSelect = document.getElementById('create-role');
  const submitButton = document.getElementById('create-submit');
  if (!form || !emailInput || !nameInput || !passwordInput || !roleSelect || !submitButton) return;

  for (const input of [emailInput, nameInput, passwordInput]) {
    input.addEventListener('input', () => setFieldError(input, null));
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    handleSubmit();
  });

  const handleSubmit = async () => {
    const email = emailInput.value.trim();
    const displayName = nameInput.value.trim();
    const password = passwordInput.value;
    const role = roleSelect.value || null;
    let firstInvalid = null;

    if (email === '') {
      setFieldError(emailInput, 'Enter an email address.');
      firstInvalid = emailInput;
    } else if (!EMAIL_PATTERN.test(email)) {
      setFieldError(emailInput, 'Enter a valid email address.');
      firstInvalid = emailInput;
    } else {
      setFieldError(emailInput, null);
    }

    if (displayName === '') {
      setFieldError(nameInput, 'Enter a display name.');
      firstInvalid = firstInvalid || nameInput;
    } else {
      setFieldError(nameInput, null);
    }

    if (password !== '' && password.length < PASSWORD_MIN) {
      setFieldError(passwordInput, `Use at least ${PASSWORD_MIN} characters, or leave the field blank to generate a password.`);
      firstInvalid = firstInvalid || passwordInput;
    } else {
      setFieldError(passwordInput, null);
    }

    if (firstInvalid) {
      firstInvalid.focus();
      return;
    }

    if (role) {
      const confirmed = await ui.confirmDialog({
        title: 'Create account and grant role',
        message:
          `Create ${displayName} and grant ${ui.roleLabel(role)}? ` +
          `This grants ${displayName} the ability to ${ROLE_ABILITY[role]}.`,
        confirmLabel: 'Create and grant role',
        details: [
          { label: 'Name', value: displayName },
          { label: 'Email', value: email },
          { label: 'Role', value: ui.roleLabel(role) },
        ],
      });
      if (!confirmed) return;
    }

    ui.setBusy(submitButton, true, 'Creating…');
    try {
      const body = { email, display_name: displayName };
      if (password !== '') body.password = password;
      const created = await api.post('/staff', body);
      const user = created && created.user ? created.user : null;

      let roleFailure = null;
      if (role && user && user.id) {
        try {
          await api.post(`/staff/${encodeURIComponent(user.id)}/roles`, { role });
        } catch (error) {
          roleFailure = error;
        }
      }

      emailInput.value = '';
      nameInput.value = '';
      passwordInput.value = '';
      roleSelect.value = '';
      cache.invalidate('/staff');
      loadStaff();

      const name = user ? user.display_name : displayName;
      if (roleFailure) {
        const info = api.describeApiError(roleFailure);
        announceError(
          'create-status',
          roleFailure,
        );
        const status = document.getElementById('create-status');
        if (status) {
          status.append(ui.el('p', {
            text: `The account for ${name} was created, but ${ui.roleLabel(role)} could not be granted (${info.title}). Grant it from the table below.`,
          }));
        }
      } else {
        announceStatus(
          'create-status',
          `Account created for ${name}.${role ? ` ${ui.roleLabel(role)} granted.` : ''}`,
        );
      }

      if (created && typeof created.generated_password === 'string' && created.generated_password !== '') {
        showOneTimePassword(
          user || { display_name: displayName, email },
          created.generated_password,
        );
      }
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 400 && error.details) {
        const details = error.details;
        const messageFor = (field) => {
          const value = details[field];
          if (typeof value === 'string' && value.trim() !== '') return value.trim();
          if (Array.isArray(value) && typeof value[0] === 'string') return value[0];
          return null;
        };
        const emailError = messageFor('email');
        const nameError = messageFor('display_name') || messageFor('name');
        const passwordError = messageFor('password');
        if (emailError) setFieldError(emailInput, emailError);
        if (nameError) setFieldError(nameInput, nameError);
        if (passwordError) setFieldError(passwordInput, passwordError);
        if (emailError || nameError || passwordError) {
          (emailError ? emailInput : nameError ? nameInput : passwordInput).focus();
          return;
        }
      }
      announceError('create-status', error);
    } finally {
      ui.setBusy(submitButton, false);
    }
  };
}

/* --------------------------------------------------------------- audit */

function renderAudit(page) {
  const region = document.getElementById('audit-region');
  const items = Array.isArray(page.items) ? page.items : [];
  state.auditTotal = Number.isFinite(page.total) ? page.total : items.length;

  const status = document.getElementById('audit-status');
  if (status) {
    status.textContent = state.auditTotal === 1
      ? '1 audit event found.'
      : `${state.auditTotal} audit events found.`;
  }

  if (items.length === 0) {
    ui.renderEmptyState(region, {
      title: 'No audit events',
      message: 'No audit events have been recorded in this demo database yet.',
      headingTag: 'h3',
    });
    return;
  }

  ui.clear(region);
  const table = ui.el('table', { class: 'table table-stack tabular-nums' },
    ui.el('thead', {},
      ui.el('tr', {},
        ui.el('th', { scope: 'col', text: 'When' }),
        ui.el('th', { scope: 'col', text: 'Actor' }),
        ui.el('th', { scope: 'col', text: 'Action' }),
        ui.el('th', { scope: 'col', text: 'Resource' }),
        ui.el('th', { scope: 'col', text: 'Outcome' }),
        ui.el('th', { scope: 'col', text: 'Request ID' }),
      ),
    ),
    ui.el('tbody', {}, items.map((entry) => {
      const actor =
        entry.actor_display_name ||
        entry.actor_email ||
        (entry.actor_user_id ? 'Deleted account' : 'Anonymous / system');
      const resource = entry.resource_type
        ? `${entry.resource_type}${entry.resource_id ? ` ${entry.resource_id}` : ''}`
        : 'Not recorded';
      const outcome = typeof entry.outcome === 'string' ? entry.outcome : '';
      const outcomeVariant = outcome === 'success' ? 'ok' : outcome === 'denied' ? 'alert' : 'warn';
      return ui.el('tr', {},
        ui.el('td', { 'data-label': 'When', text: ui.formatDateTime(entry.created_at) || 'Not recorded' }),
        ui.el('td', { 'data-label': 'Actor', text: actor }),
        ui.el('td', { 'data-label': 'Action' }, ui.el('span', { class: 'mono', text: entry.action || 'unknown' })),
        ui.el('td', { 'data-label': 'Resource', text: resource }),
        ui.el('td', { 'data-label': 'Outcome' },
          ui.el('span', {
            class: `badge badge--${outcomeVariant}`,
            text: outcome || 'Not recorded',
          }),
        ),
        ui.el('td', { 'data-label': 'Request ID' },
          ui.el('span', { class: 'mono', text: entry.request_id || 'Not recorded' }),
        ),
      );
    })),
  );

  region.append(
    ui.el('div', {
      class: 'table-scroll',
      role: 'region',
      'aria-label': 'Audit events',
      tabindex: '0',
    }, table),
  );
  const paginationHost = ui.el('div', {});
  region.append(paginationHost);
  ui.renderPagination(paginationHost, {
    total: state.auditTotal,
    limit: AUDIT_PAGE_SIZE,
    offset: state.auditOffset,
    count: items.length,
    itemLabel: 'audit events',
    onChange: (nextOffset) => {
      state.auditOffset = nextOffset;
      loadAudit();
    },
  });
}

function loadAudit() {
  const region = document.getElementById('audit-region');
  const key = cache.keyFor('/audit', { limit: AUDIT_PAGE_SIZE, offset: state.auditOffset });
  if (!cache.peek(key)) {
    ui.renderLoading(region, { label: 'Loading audit events', rows: 4 });
  }
  cache.swr(key, () => api.get('/audit', { limit: AUDIT_PAGE_SIZE, offset: state.auditOffset }), {
    onData: (page) => renderAudit(page),
    onError: (error) => {
      ui.renderErrorState(region, error, {
        describe: api.describeApiError,
        onRetry: loadAudit,
        headingTag: 'h3',
      });
    },
    onRevalidating: (on) => ui.setSyncIndicator(region, on),
  });
}

/* ---------------------------------------------------------------- page */

function renderPermissionDenied() {
  const body = ui.clear(document.getElementById('page-body'));
  body.append(
    ui.el('div', { class: 'state-panel state-panel--error', role: 'alert' },
      ui.el('h2', { class: 'state-panel__title', text: 'You do not have permission' }),
      ui.el('p', {
        class: 'state-panel__text',
        text: 'Staff administration is limited to accounts with staff or role administration permissions. If you open the admin API directly it returns 403 for this account, because access is enforced server-side.',
      }),
      ui.el('div', { class: 'state-panel__actions' },
        ui.el('a', { class: 'btn btn-primary', href: 'dashboard.html' }, 'Back to dashboard'),
      ),
    ),
  );
}

ui.onReady(async () => {
  ui.setRetryHandler(() => window.location.reload());
  // Pre-rendering (prefetch.js): on idle, warm the modules a navigation from
  // this page will likely need (link hints only — nothing is executed early).
  prefetch.warmModules(['assets/js/patients.js']);
  try {
    const user = await auth.initAuthChrome({ active: 'admin' });
    if (!user) return;
    commandMenu.initCommandMenu({ user });

    if (!auth.hasAnyPermission(user, ADMIN_PERMISSIONS)) {
      renderPermissionDenied();
      return;
    }
    state.me = user;

    const loading = document.getElementById('admin-loading');
    if (loading) loading.hidden = true;

    const createSection = document.getElementById('create-staff-section');
    const addStaffToggle = document.getElementById('add-staff-toggle');
    if (createSection) createSection.hidden = true;
    if (addStaffToggle) {
      addStaffToggle.hidden = !canManage();
      addStaffToggle.addEventListener('click', () => {
        if (!createSection) return;
        const expanded = addStaffToggle.getAttribute('aria-expanded') === 'true';
        addStaffToggle.setAttribute('aria-expanded', expanded ? 'false' : 'true');
        createSection.hidden = expanded;
        if (!expanded) {
          const firstField = document.getElementById('create-email');
          if (firstField && typeof firstField.focus === 'function') firstField.focus();
        }
      });
    }
    if (canManage()) initCreateForm();

    const auditSection = document.getElementById('audit-section');
    if (auditSection) auditSection.hidden = !auth.hasPermission(user, 'audit:read');

    if (auth.hasPermission(user, 'staff:read')) {
      loadStaff();
    } else {
      ui.renderEmptyState(document.getElementById('staff-region'), {
        title: 'Staff list unavailable',
        message: 'Your account cannot read the staff list (the API returns 403). Staff administration permissions are enforced server-side.',
        headingTag: 'h3',
      });
    }

    if (auth.hasPermission(user, 'audit:read')) loadAudit();
  } catch (error) {
    ui.renderErrorState(document.getElementById('page-body'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
  }
});
