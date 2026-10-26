// ---------------------------------------------------------------------------
// ui.js — shared design-system and DOM helpers. No imports, no side effects at
// module load, so it stays testable outside the browser.
// Never logs data: nothing in this module (or the app) writes to the console.
// ---------------------------------------------------------------------------

/* ------------------------------------------------------------------ utils */

export function onReady(fn) {
  if (typeof document === 'undefined') return;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', fn, { once: true });
  } else {
    fn();
  }
}

/**
 * Minimal element builder.
 * el('button', { class: 'btn', onClick }, 'Label', el('span', {}, 'x'))
 */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value; // trusted, app-authored markup only
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'dataset') {
      for (const [dataKey, dataValue] of Object.entries(value)) {
        node.dataset[dataKey] = dataValue;
      }
    } else {
      node.setAttribute(key, value === true ? '' : String(value));
    }
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

const DATE_FORMAT = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
});

const DATE_TIME_FORMAT = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'UTC',
});

function parseDate(iso) {
  if (typeof iso !== 'string' || iso.trim() === '') return null;
  const value = iso.trim();
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? new Date(`${value}T00:00:00Z`)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** ISO-8601 (UTC) -> "26 Oct 2026". Returns null when unparseable. */
export function formatDate(iso) {
  const date = parseDate(iso);
  return date ? DATE_FORMAT.format(date) : null;
}

/** ISO-8601 (UTC) -> "26 Oct 2026, 13:00". Returns null when unparseable. */
export function formatDateTime(iso) {
  const date = parseDate(iso);
  return date ? DATE_TIME_FORMAT.format(date) : null;
}

/** Fallback display for any date-like field. */
export function displayDate(iso) {
  return formatDate(iso) || 'Not recorded';
}

/* ------------------------------------------------------------- role labels */

const ROLE_LABELS = {
  doctor: 'Doctor',
  nurse: 'Nurse',
  clinical_admin: 'Clinical administrator',
  tech_admin: 'Technical administrator',
};

export function roleLabel(role) {
  if (typeof role !== 'string' || role.trim() === '') return 'Unknown role';
  return ROLE_LABELS[role] || role.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/* --------------------------------------------------------------- loading */

export function renderLoading(container, { label = 'Loading', rows = 5 } = {}) {
  clear(container);
  const wrapper = el('div', { class: 'skeleton-block', role: 'status', 'aria-live': 'polite' },
    el('p', { class: 'visually-hidden', text: `${label}…` }),
  );
  for (let i = 0; i < rows; i += 1) {
    wrapper.append(el('div', { class: 'skeleton-row' },
      el('div', { class: 'skeleton-bar skeleton-bar--lg', 'aria-hidden': 'true' }),
      el('div', { class: 'skeleton-bar skeleton-bar--md', 'aria-hidden': 'true' }),
      el('div', { class: 'skeleton-bar skeleton-bar--sm', 'aria-hidden': 'true' }),
    ));
  }
  container.append(wrapper);
}

/* ------------------------------------------------------------ empty/error */

export function renderEmptyState(container, { title = 'Nothing here yet', message = '', headingTag = 'h2' } = {}) {
  clear(container);
  container.append(
    el('div', { class: 'state-panel state-panel--empty' },
      el(headingTag, { class: 'state-panel__title', text: title }),
      message ? el('p', { class: 'state-panel__text', text: message }) : null,
    ),
  );
}

/**
 * Friendly, actionable error panel. Never renders raw error objects or stack
 * traces; shows the contract's generic message plus a request id for reports.
 * 401 states link back to sign-in.
 */
export function renderErrorState(container, error, { title, onRetry, describe, headingTag = 'h2' } = {}) {
  const info = describe
    ? describe(error)
    : { title: 'Something went wrong', message: 'Please try again.', retryable: true };

  clear(container);
  const panel = el('div', { class: 'state-panel state-panel--error', role: 'alert' },
    el(headingTag, { class: 'state-panel__title', text: title || info.title }),
    el('p', { class: 'state-panel__text', text: info.message }),
  );

  const actions = el('div', { class: 'state-panel__actions' });
  const status = error && typeof error === 'object' ? error.status : null;

  if (status === 401) {
    actions.append(el('a', { class: 'btn btn-primary', href: 'index.html' }, 'Go to sign in'));
  }
  if (onRetry && info.retryable) {
    actions.append(el('button', { class: 'btn btn-secondary', type: 'button', onClick: onRetry }, 'Try again'));
  }
  if (actions.childNodes.length > 0) panel.append(actions);

  const requestId = error && typeof error === 'object' ? error.requestId : null;
  if (requestId) {
    panel.append(
      el('p', { class: 'state-panel__meta', text: `Request ID: ${requestId}` }),
    );
  }
  container.append(panel);

  // Network failures also get the persistent global banner with a retry action.
  if (status === 0) {
    showGlobalBanner({
      tone: 'danger',
      message: 'Backend unreachable — the demo backend did not respond.',
      actionLabel: 'Retry',
      onAction: onRetry || null,
    });
  }
}

/* ------------------------------------------------------------- busy state */

export function setBusy(button, busy, busyLabel = 'Working…') {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = busyLabel;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.disabled = false;
    button.removeAttribute('aria-busy');
  }
}

/* ------------------------------------------------------------- pagination */

function pageWindow(currentPage, pageCount) {
  const pages = [];
  const start = Math.max(1, Math.min(currentPage - 2, pageCount - 4));
  const end = Math.min(pageCount, start + 4);
  for (let page = Math.max(1, start); page <= end; page += 1) pages.push(page);
  return pages;
}

/**
 * Renders "Showing X–Y of Z" plus numbered prev/next pagination for the
 * contract's Page shape { total, limit, offset }. onChange receives a new offset.
 */
export function renderPagination(container, { total, limit, offset, onChange, itemLabel = 'records', count = null }) {
  clear(container);
  const safeLimit = Math.max(1, limit || 20);
  const safeOffset = Math.max(0, offset || 0);
  if (!Number.isFinite(total) || total <= 0 || typeof onChange !== 'function') return;

  const pageCount = Math.ceil(total / safeLimit);
  const currentPage = Math.floor(safeOffset / safeLimit) + 1;
  const first = safeOffset + 1;
  const shown = Number.isFinite(count) ? count : safeLimit;
  const last = Math.min(safeOffset + shown, total);

  const summary = el('p', {
    class: 'pagination__summary',
    text: `Showing ${first}\u2013${last} of ${total} ${itemLabel}`,
  });

  const list = el('ul', { class: 'pagination__list' });
  const goTo = (page) => onChange(Math.max(0, Math.min(page - 1, pageCount - 1)) * safeLimit);

  const prev = el('li', {},
    el('button', {
      class: 'btn btn-secondary btn-sm',
      type: 'button',
      disabled: currentPage <= 1 ? true : null,
      'aria-label': 'Previous page',
      onClick: () => goTo(currentPage - 1),
    }, 'Previous'),
  );
  list.append(prev);

  const pages = pageWindow(currentPage, pageCount);
  if (pages[0] > 1) {
    list.append(el('li', { class: 'pagination__ellipsis', 'aria-hidden': 'true', text: '\u2026' }));
  }
  for (const page of pages) {
    const isCurrent = page === currentPage;
    list.append(el('li', {},
      el('button', {
        class: isCurrent ? 'btn btn-page btn-sm is-current' : 'btn btn-page btn-sm',
        type: 'button',
        'aria-label': `Page ${page}`,
        'aria-current': isCurrent ? 'page' : null,
        onClick: () => goTo(page),
      }, String(page)),
    ));
  }
  if (pages[pages.length - 1] < pageCount) {
    list.append(el('li', { class: 'pagination__ellipsis', 'aria-hidden': 'true', text: '\u2026' }));
  }

  list.append(el('li', {},
    el('button', {
      class: 'btn btn-secondary btn-sm',
      type: 'button',
      disabled: currentPage >= pageCount ? true : null,
      'aria-label': 'Next page',
      onClick: () => goTo(currentPage + 1),
    }, 'Next'),
  ));

  container.append(
    el('div', { class: 'pagination' },
      summary,
      el('nav', { class: 'pagination__nav', 'aria-label': 'Pagination' }, list),
    ),
  );
}

/* ------------------------------------------------------------- global banner */

let retryHandler = null;

export function setRetryHandler(fn) {
  retryHandler = typeof fn === 'function' ? fn : null;
}

export function hideGlobalBanner() {
  if (typeof document === 'undefined') return;
  const host = document.getElementById('global-status');
  if (host) clear(host);
}

export function showGlobalBanner({ tone = 'danger', message, actionLabel, onAction, dismissible = true }) {
  if (typeof document === 'undefined') return;
  let host = document.getElementById('global-status');
  if (!host) {
    host = el('div', { id: 'global-status', class: 'global-status', role: 'status', 'aria-live': 'polite' });
    document.body.prepend(host);
  }
  clear(host);

  const banner = el('div', { class: `banner banner--${tone}` },
    el('p', { class: 'banner__text', text: message }),
    el('div', { class: 'banner__actions' }),
  );
  const actions = banner.querySelector('.banner__actions');

  if (actionLabel) {
    actions.append(el('button', {
      class: 'btn btn-secondary btn-sm',
      type: 'button',
      onClick: () => {
        hideGlobalBanner();
        const handler = onAction || retryHandler;
        if (handler) handler();
      },
    }, actionLabel));
  }
  if (dismissible) {
    actions.append(el('button', {
      class: 'btn btn-ghost btn-sm',
      type: 'button',
      'aria-label': 'Dismiss message',
      onClick: hideGlobalBanner,
    }, 'Dismiss'));
  }
  host.append(banner);
}

/* ------------------------------------------------------------- dialogs */

function ensureDialogHost() {
  let dialog = document.getElementById('app-dialog');
  if (dialog) return dialog;
  dialog = el('dialog', { id: 'app-dialog', class: 'dialog' },
    el('form', { class: 'dialog__inner', method: 'dialog' },
      el('h2', { class: 'dialog__title', id: 'app-dialog-title' }),
      el('div', { class: 'dialog__body', id: 'app-dialog-body' }),
      el('div', { class: 'dialog__actions', id: 'app-dialog-actions' }),
    ),
  );
  document.body.append(dialog);
  return dialog;
}

function closeDialog(dialog, value) {
  if (typeof dialog.close === 'function' && dialog.open) dialog.close(value);
  else dialog.removeAttribute('open');
}

/**
 * Confirmation dialog for high-impact actions (logout now, admin role changes
 * and deactivation later). Resolves true when confirmed, false otherwise.
 */
export function confirmDialog({
  title = 'Are you sure?',
  message = '',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
} = {}) {
  if (typeof document === 'undefined') return Promise.resolve(false);

  const dialog = ensureDialogHost();
  const titleNode = dialog.querySelector('#app-dialog-title');
  const body = clear(dialog.querySelector('#app-dialog-body'));
  const actions = clear(dialog.querySelector('#app-dialog-actions'));

  titleNode.textContent = title;
  body.append(el('p', { text: message }));

  return new Promise((resolve) => {
    let settled = false;
    const settle = (value) => {
      if (settled) return;
      settled = true;
      dialog.removeEventListener('close', onClose);
      closeDialog(dialog);
      resolve(value);
    };
    const onClose = () => settle(false);

    actions.append(
      el('button', {
        class: 'btn btn-secondary',
        type: 'button',
        autofocus: true,
        onClick: () => settle(false),
      }, cancelLabel),
      el('button', {
        class: danger ? 'btn btn-danger' : 'btn btn-primary',
        type: 'button',
        onClick: () => settle(true),
      }, confirmLabel),
    );

    dialog.addEventListener('close', onClose);
    if (typeof dialog.showModal === 'function') {
      dialog.showModal();
      const cancel = actions.querySelector('button');
      if (cancel) cancel.focus();
    } else if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
      settle(window.confirm(`${title}\n\n${message}`));
    } else {
      settle(false);
    }
  });
}

/** Read-only informational dialog ("About this demo"). */
export function infoDialog({ title = 'About this demo', bodyHTML = '' } = {}) {
  if (typeof document === 'undefined') return;
  const dialog = ensureDialogHost();
  dialog.querySelector('#app-dialog-title').textContent = title;

  const body = clear(dialog.querySelector('#app-dialog-body'));
  const fragment = el('div', {});
  fragment.innerHTML = bodyHTML; // app-authored static copy only
  body.append(fragment);

  const actions = clear(dialog.querySelector('#app-dialog-actions'));
  actions.append(el('button', {
    class: 'btn btn-primary',
    type: 'button',
    autofocus: true,
    onClick: () => closeDialog(dialog),
  }, 'Close'));

  if (typeof dialog.showModal === 'function') dialog.showModal();
  else if (typeof window !== 'undefined' && typeof window.alert === 'function') {
    window.alert(title);
  }
}

const ABOUT_DEMO_HTML = `
  <p>This is a public demonstration of a clinical AI platform. It runs entirely on
  <strong>synthetic, fictional patient data</strong> generated for the demo.</p>
  <ul>
    <li>Not clinically validated and not a medical device.</li>
    <li>No real patient data is used, stored, or displayed anywhere.</li>
    <li>Nothing here is a diagnosis, treatment recommendation, or clinical decision support.</li>
    <li>AI-generated answers (when enabled) are labelled as such and may be incomplete.</li>
    <li>Demo accounts are shared and public; never enter real credentials or real health information.</li>
  </ul>
  <p>Use the "Report an issue" link in the footer to flag problems. Please do not include
  any personal or health information in a report.</p>
`;

export function openAboutDemo() {
  infoDialog({ title: 'About this demo', bodyHTML: ABOUT_DEMO_HTML });
}

/** Wires every "About this demo" control in the page footer. */
export function wireFooterDialogs(root) {
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!scope) return;
  for (const button of scope.querySelectorAll('[data-open-about]')) {
    button.addEventListener('click', openAboutDemo);
  }
}

/* -------------------------------------------------------------- app chrome */

function renderNav(navNode, navItems, activeKey) {
  clear(navNode);
  const list = el('ul', { class: 'site-nav__list' });
  for (const item of navItems) {
    const isActive = item.key === activeKey;
    list.append(el('li', {},
      el('a', {
        class: isActive ? 'site-nav__link is-active' : 'site-nav__link',
        href: item.href,
        'aria-current': isActive ? 'page' : null,
      }, item.label),
    ));
  }
  navNode.append(list);
}

function renderUserArea(host, user, onLogout) {
  clear(host);
  if (!user) {
    host.append(
      el('div', { class: 'user-chip' },
        el('span', { class: 'user-chip__name', text: 'Not signed in' }),
      ),
      el('a', { class: 'btn btn-primary btn-sm', href: 'index.html' }, 'Sign in'),
    );
    return;
  }

  const roles = Array.isArray(user.roles) ? user.roles : [];
  const roleText = roles.length > 0 ? roles.map(roleLabel).join(', ') : 'No role assigned';

  host.append(
    el('div', { class: 'user-chip' },
      el('span', { class: 'user-chip__name', text: user.display_name || user.email || 'Signed in' }),
      el('span', { class: 'user-chip__role', text: roleText }),
    ),
    el('button', {
      class: 'btn btn-secondary btn-sm',
      type: 'button',
      onClick: async () => {
        const confirmed = await confirmDialog({
          title: 'Sign out',
          message: 'Sign out of the demo? Your session ends and protected pages will require signing in again.',
          confirmLabel: 'Sign out',
          danger: false,
        });
        if (confirmed && onLogout) await onLogout();
      },
    }, 'Sign out'),
  );
}

function wireNavToggle() {
  const toggle = document.getElementById('nav-toggle');
  const nav = document.getElementById('site-nav');
  if (!toggle || !nav) return;
  toggle.addEventListener('click', () => {
    const expanded = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', expanded ? 'false' : 'true');
    nav.classList.toggle('is-open', !expanded);
  });
}

/**
 * Builds header nav + user area and wires the mobile nav toggle and footer
 * dialogs. `navItems` is [{ key, label, href }]; pass user = null when the
 * session could not be loaded.
 */
export function initChrome({ user = null, active = null, navItems = [], onLogout = null } = {}) {
  if (typeof document === 'undefined') return;
  wireNavToggle();
  wireFooterDialogs();

  const navNode = document.getElementById('site-nav');
  if (navNode) renderNav(navNode, navItems, active);

  const userArea = document.getElementById('user-area');
  if (userArea) renderUserArea(userArea, user, onLogout);
}
