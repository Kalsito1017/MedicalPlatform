// ---------------------------------------------------------------------------
// admin.html page module.
//
// Current state: honest shell. The staff/role management data table is a
// LATER WAVE (PLAN.md Phase 10) feature: the admin API (GET/POST /api/v1/staff,
// PATCH /api/v1/staff/{user_id}, role assign/remove) is contracted in
// docs/API.md but the interactive UI is deliberately not faked here. There are
// no decorative controls on this page — every element rendered today works.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as ui from './ui.js';

const ADMIN_PERMISSIONS = ['staff:read', 'staff:manage', 'role:assign'];

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

function renderPlaceholder() {
  const body = ui.clear(document.getElementById('page-body'));
  body.append(
    // ================================================================
    // EXTENSION POINT: ADMIN STAFF TABLE (Phase 10)
    // Replace the placeholder panel below with the staff management data
    // table (GET /api/v1/staff with the Page shape {items,total,limit,offset},
    // plus create/activate/deactivate/role-assign flows). Use
    // ui.confirmDialog() for role changes and deactivation, ui.renderPagination()
    // for paging, and api.patch()/api.post()/api.del() for mutations (they all
    // send X-CSRF-Token on non-GET requests). Do not remove renderPermissionDenied().
    // ================================================================
    ui.el('section', { class: 'card card--notice', 'aria-labelledby': 'admin-placeholder-heading' },
      ui.el('h2', {
        class: 'card__title',
        id: 'admin-placeholder-heading',
        text: 'Staff management loads here',
      }),
      ui.el('p', {
        text: 'This page is a working shell. The staff and role management interface (staff list, account activation, role assignment with confirmation dialogs) lands in a later phase and will render in this section.',
      }),
      ui.el('p', {
        class: 'card__note',
        text: 'Your account does have admin permissions, but no admin data table exists in this build yet. Nothing on this page is a placeholder control: there are deliberately no inactive buttons.',
      }),
    ),
  );
}

ui.onReady(async () => {
  ui.setRetryHandler(() => window.location.reload());
  try {
    const user = await auth.initAuthChrome({ active: 'admin' });
    if (!user) return;

    if (auth.hasAnyPermission(user, ADMIN_PERMISSIONS)) {
      renderPlaceholder();
    } else {
      renderPermissionDenied();
    }
  } catch (error) {
    ui.renderErrorState(document.getElementById('page-body'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
  }
});
