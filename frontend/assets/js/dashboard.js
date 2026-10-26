// ---------------------------------------------------------------------------
// dashboard.js — authenticated home: who you are, what you can reach, and the
// standing synthetic-data notice. Everything rendered here comes from the
// /api/v1/auth/me payload; nothing is cached in web storage.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as ui from './ui.js';

function renderProfileCard(user) {
  const roles = Array.isArray(user.roles) ? user.roles : [];
  const permissions = Array.isArray(user.permissions) ? user.permissions : [];

  return ui.el('section', { class: 'card', 'aria-labelledby': 'profile-heading' },
    ui.el('h2', { class: 'card__title', id: 'profile-heading', text: 'Your session' }),
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

function renderQuickLinks(user) {
  const links = [];
  if (auth.hasPermission(user, 'patient:read')) {
    links.push({
      href: 'patients.html',
      title: 'Find a synthetic patient',
      text: 'Search the fictional patient list and open demographics, labs, and prescriptions.',
    });
  }
  if (auth.hasAnyPermission(user, ['staff:read', 'staff:manage', 'role:assign'])) {
    links.push({
      href: 'admin.html',
      title: 'Staff management',
      text: 'Staff accounts and role assignment. The admin data table arrives in a later phase.',
    });
  }

  const list = ui.el('div', { class: 'quick-links' });
  for (const link of links) {
    list.append(
      ui.el('a', { class: 'quick-link card', href: link.href },
        ui.el('h3', { class: 'quick-link__title', text: link.title }),
        ui.el('p', { class: 'quick-link__text', text: link.text }),
        ui.el('span', { class: 'quick-link__cta', text: 'Open' }),
      ),
    );
  }
  if (links.length === 0) {
    list.append(
      ui.el('p', { class: 'text-muted', text: 'No sections are enabled for your account yet. Contact a demo administrator.' }),
    );
  }

  return ui.el('section', { class: 'stack', 'aria-labelledby': 'quick-links-heading' },
    ui.el('h2', { class: 'card__title', id: 'quick-links-heading', text: 'Quick links' }),
    list,
  );
}

function renderNoticeCard() {
  return ui.el('section', { class: 'card card--notice', 'aria-labelledby': 'notice-heading' },
    ui.el('h2', { class: 'card__title', id: 'notice-heading', text: 'Synthetic demo data \u2014 not for clinical use' }),
    ui.el('p', {
      text: 'Every record in this platform is fictional and generated for demonstration. This is not clinically validated, not a medical device, and must never be used for real patient care.',
    }),
    ui.el('button', {
      class: 'btn btn-secondary',
      type: 'button',
      onClick: ui.openAboutDemo,
    }, 'About this demo'),
  );
}

function render(user) {
  const body = ui.clear(document.getElementById('page-body'));
  body.append(
    ui.el('div', { class: 'stack-lg' },
      renderProfileCard(user),
      renderQuickLinks(user),
      renderNoticeCard(),
    ),
  );
}

ui.onReady(async () => {
  ui.setRetryHandler(() => window.location.reload());
  try {
    const user = await auth.initAuthChrome({ active: 'dashboard' });
    if (!user) return;
    render(user);
  } catch (error) {
    ui.renderErrorState(document.getElementById('page-body'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
  }
});
