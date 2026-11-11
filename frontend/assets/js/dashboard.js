// ---------------------------------------------------------------------------
// dashboard.js — authenticated home: who you are, what you can reach, and the
// latest patient records. Everything rendered here comes from the
// /api/v1/auth/me payload and the paginated /api/v1/patients list; nothing is
// cached in web storage and no data is invented.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as cache from './cache.js';
import * as commandMenu from './command-menu.js?v=20261109j';
import * as prefetch from './prefetch.js';
import * as ui from './ui.js';

const RECENT_PATIENTS = 8;

function renderRoleBadges(user) {
  const roles = Array.isArray(user.roles) ? user.roles : [];
  if (roles.length === 0) {
    return ui.el('p', { class: 'text-muted', text: 'No role assigned' });
  }
  return ui.el('div', { class: 'badge-list' }, roles.map(ui.roleBadge));
}

function renderProfileCard(user) {
  const roles = Array.isArray(user.roles) ? user.roles : [];
  const permissions = Array.isArray(user.permissions) ? user.permissions : [];

  return ui.el('section', { class: 'card', 'aria-labelledby': 'profile-heading' },
    ui.el('div', { class: 'profile-card__head' },
      ui.avatarForUser(user, { size: 'lg' }),
      ui.el('div', {},
        ui.el('h2', { class: 'card__title', id: 'profile-heading', text: 'Your session' }),
        ui.el('p', { class: 'text-muted', text: user.display_name || 'Not recorded' }),
      ),
    ),
    ui.el('dl', { class: 'detail-grid' },
      ui.el('dt', { text: 'Name' }),
      ui.el('dd', { text: user.display_name || 'Not recorded' }),
      ui.el('dt', { text: 'Email' }),
      ui.el('dd', { text: user.email || 'Not recorded' }),
      ui.el('dt', { text: 'Role' }),
      ui.el('dd', {
        text: roles.length > 0 ? roles.map(ui.roleLabel).join(', ') : 'No role assigned',
      }),
      ui.el('dt', { text: 'Account status' }),
      ui.el('dd', { text: user.is_active === false ? 'Deactivated' : 'Active' }),
      ui.el('dt', { text: 'Permissions' }),
      ui.el('dd', {},
        permissions.length > 0
          ? ui.el('ul', { class: 'chip-list' },
              permissions.map((permission) =>
                ui.el('li', { class: 'chip', text: permission })
              )
            )
          : ui.el('span', { class: 'text-muted', text: 'None granted' })
      ),
    ),
    ui.el('p', { class: 'card__note', text: 'Permissions are enforced by the backend on every request; the interface only reflects them.' }),
  );
}

// Quick actions are real destinations only: each one leads to a working
// section of this deployment. Controls for features that do not exist (for
// example ordering labs or scheduling visits) are deliberately absent.
function renderQuickLinks(user) {
  const links = [];
  if (auth.hasPermission(user, 'patient:read')) {
    links.push({
      href: 'patients.html',
      icon: 'users',
      title: 'Find a patient',
      text: 'Search the patient list and open demographics, labs, and prescriptions.',
    });
  }
  if (auth.hasPermission(user, 'appointment:read')) {
    links.push({
      href: 'appointments.html',
      icon: 'calendar',
      title: 'Appointment calendar',
      text: 'Plan, reschedule, and cancel visits; voice scheduling is available when enabled.',
    });
  }
  if (auth.hasAnyPermission(user, ['staff:read', 'staff:manage', 'role:assign'])) {
    links.push({
      href: 'admin.html',
      icon: 'shield',
      title: 'Staff management',
      text: 'Staff accounts and role administration, with the audit log where permitted.',
    });
  }

  const list = ui.el('div', { class: 'quick-links' });
  for (const link of links) {
    list.append(
      ui.el('a', { class: 'quick-link card', href: link.href },
        ui.el('span', { class: 'quick-link__icon', 'aria-hidden': 'true', html: ui.ICONS[link.icon] }),
        ui.el('div', { class: 'quick-link__body' },
          ui.el('h3', { class: 'quick-link__title', text: link.title }),
          ui.el('p', { class: 'quick-link__text', text: link.text }),
        ),
        ui.el('span', { class: 'quick-link__cta', 'aria-hidden': 'true', html: ui.ICONS.chevronRight }),
      ),
    );
  }
  if (links.length === 0) {
    list.append(
      ui.el('p', { class: 'text-muted', text: 'No sections are enabled for your account yet. Contact an administrator.' }),
    );
  }

  return ui.el('section', { class: 'stack', 'aria-labelledby': 'quick-links-heading' },
    ui.el('h2', { class: 'card__title', id: 'quick-links-heading', text: 'Quick actions' }),
    list,
  );
}

function renderRecentPatients(user) {
  if (!auth.hasPermission(user, 'patient:read')) return null;

  const grid = ui.el('div', { class: 'patient-cards' });
  const section = ui.el('section', { class: 'stack', 'aria-labelledby': 'recent-heading' },
    ui.el('div', { class: 'toolbar' },
      ui.el('h2', { class: 'card__title', id: 'recent-heading', text: 'Recent patients' }),
      ui.el('a', { href: 'patients.html' }, 'View all patients'),
    ),
    grid,
  );

  ui.renderLoading(grid, { label: 'Loading recent patients', rows: 3, variant: 'cards' });
  const key = cache.keyFor('/patients', { limit: RECENT_PATIENTS, offset: 0 });
  cache.swr(key, () => api.get('/patients', { limit: RECENT_PATIENTS, offset: 0 }), {
    onData: (page) => {
      const items = Array.isArray(page.items) ? page.items : [];
      ui.clear(grid);
      if (items.length === 0) {
        ui.renderEmptyState(grid, {
          title: 'No patients yet',
          message: 'No patients are recorded for your account yet.',
          headingTag: 'h3',
        });
        return;
      }
      for (const patient of items) grid.append(ui.renderPatientCard(patient));
      // Hover/focus intent prefetch for the freshly rendered cards.
      prefetch.wirePatientIntent(grid, user);
    },
    onError: (error) => {
      ui.renderErrorState(grid, error, {
        describe: api.describeApiError,
        onRetry: () => window.location.reload(),
        headingTag: 'h3',
      });
    },
    onRevalidating: (on) => ui.setSyncIndicator(grid, on),
  });

  return section;
}

function render(user) {
  const heading = document.getElementById('welcome-heading');
  if (heading) {
    heading.textContent = `Welcome back, ${user.display_name || user.email || 'there'}`;
  }

  const body = ui.clear(document.getElementById('page-body'));
  body.append(
    renderRoleBadges(user),
    ui.el('div', { class: 'stack-lg' },
      renderQuickLinks(user),
      renderRecentPatients(user),
      renderProfileCard(user),
    ),
  );
  ui.markBlurReady(body);
}

ui.onReady(async () => {
  ui.setRetryHandler(() => window.location.reload());
  // Dashboard welcome hero: the heart loader (hero placeholder contract)
  // stands in for the session content while it loads (DESIGN.md item 8).
  ui.renderBlurPlaceholder(document.getElementById('page-body'), {
    label: 'Loading your session',
    variant: 'welcome',
  });
  // Pre-rendering (prefetch.js): on idle, warm the likely next page modules.
  prefetch.warmModules(['assets/js/patients.js', 'assets/js/patient.js']);
  try {
    const user = await auth.initAuthChrome({ active: 'dashboard' });
    if (!user) return;
    commandMenu.initCommandMenu({ user });
    render(user);
  } catch (error) {
    ui.renderErrorState(document.getElementById('page-body'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
  }
});
