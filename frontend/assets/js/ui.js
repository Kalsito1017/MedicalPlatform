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

/**
 * ISO-8601 (UTC) -> compact relative label ("just now", "5 minutes ago",
 * "yesterday", "3 days ago", then the absolute date). Returns '' when
 * unparseable. Used for conversation-history timestamps.
 */
export function formatRelativeTime(iso) {
  const date = parseDate(iso);
  if (!date) return '';
  const deltaSeconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (deltaSeconds < 0) return formatDateTime(iso) || '';
  if (deltaSeconds < 45) return 'just now';
  const minutes = Math.round(deltaSeconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.round(deltaSeconds / 3600);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.floor(deltaSeconds / 86400);
  if (days === 1) return 'yesterday';
  if (days < 7) return `${days} days ago`;
  return formatDate(iso) || '';
}

/* ------------------------------------------------------------- role labels */

const ROLE_LABELS = {
  doctor: 'Doctor',
  nurse: 'Nurse',
  clinical_admin: 'Admin',
};

export function roleLabel(role) {
  if (typeof role !== 'string' || role.trim() === '') return 'Unknown role';
  return ROLE_LABELS[role] || role.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/** Colored role pill, matching the staff table's per-role hue tags. */
export function roleBadge(role) {
  return el('span', {
    class: `badge badge--role badge--role-${role}`,
    text: roleLabel(role),
  });
}

/* ------------------------------------------------------- icons + avatars */
// Inline SVG line icons (24x24, stroke = currentColor). App-authored markup
// only: no icon fonts, no external sprite sheets, no CDN requests.

const SVG_OPEN =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">';

export const ICONS = {
  home: `${SVG_OPEN}<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5"/></svg>`,
  users: `${SVG_OPEN}<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19.5c.6-3 2.8-4.7 5.5-4.7s4.9 1.7 5.5 4.7"/><circle cx="17" cy="9" r="2.5"/><path d="M16 14.6c2.3.2 4 1.8 4.5 4.4"/></svg>`,
  shield: `${SVG_OPEN}<path d="M12 3 5 5.8v5.3c0 4.6 2.9 8 7 9.2 4.1-1.2 7-4.6 7-9.2V5.8z"/><path d="m9.2 11.8 2 2 3.6-3.8"/></svg>`,
  search: `${SVG_OPEN}<circle cx="11" cy="11" r="6.5"/><path d="m20 20-3.6-3.6"/></svg>`,
  sparkles: `${SVG_OPEN}<path d="M11 3.5 12.6 7.9 17 9.5l-4.4 1.6L11 15.5 9.4 11.1 5 9.5l4.4-1.6z"/><path d="m18.4 15 .7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/></svg>`,
  flask: `${SVG_OPEN}<path d="M9.5 3h5"/><path d="M10.5 3v5.2L5.8 17a2 2 0 0 0 1.7 3h9a2 2 0 0 0 1.7-3l-4.7-8.8V3"/><path d="M8 14h8"/></svg>`,
  copy: `${SVG_OPEN}<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>`,
  check: `${SVG_OPEN}<circle cx="12" cy="12" r="8.5"/><path d="m8.5 12 2.5 2.5 4.5-5"/></svg>`,
  alert: `${SVG_OPEN}<path d="M12 4.5 21 19.5H3z"/><path d="M12 10v4"/><path d="M12 16.8v.2"/></svg>`,
  chevronRight: `${SVG_OPEN}<path d="m9 6 6 6-6 6"/></svg>`,
  chevronLeft: `${SVG_OPEN}<path d="m15 6-6 6 6 6"/></svg>`,
  chevronDown: `${SVG_OPEN}<path d="m6 9 6 6 6-6"/></svg>`,
  arrowLeft: `${SVG_OPEN}<path d="M19 12H5"/><path d="m11 6-6 6 6 6"/></svg>`,
  x: `${SVG_OPEN}<path d="M6 6 18 18"/><path d="M18 6 6 18"/></svg>`,
  plus: `${SVG_OPEN}<path d="M12 5v14"/><path d="M5 12h14"/></svg>`,
  send: `${SVG_OPEN}<path d="m21 3-9.5 9.5"/><path d="M21 3 14.5 21l-3-7.5-7.5-3z"/></svg>`,
  info: `${SVG_OPEN}<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5"/><path d="M12 8.2v.2"/></svg>`,
  help: `${SVG_OPEN}<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.9.6c0 1.7-2.5 2-2.5 3.5"/><path d="M12 16.6v.2"/></svg>`,
  logout: `${SVG_OPEN}<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="m10 8-4 4 4 4"/><path d="M6 12h9"/></svg>`,
  clipboard: `${SVG_OPEN}<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4.5V3.5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1"/><path d="M9 10h6"/><path d="M9 14h6"/></svg>`,
  capsule: `${SVG_OPEN}<path d="M10.5 4.5 4.5 10.5a4.2 4.2 0 0 0 6 6l6-6a4.2 4.2 0 0 0-6-6z"/><path d="m7.5 7.5 6 6"/></svg>`,
  calendar: `${SVG_OPEN}<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M8 3v4"/><path d="M16 3v4"/><path d="M4 10h16"/></svg>`,
  mail: `${SVG_OPEN}<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 7 8.5 6 8.5-6"/></svg>`,
};

/** Inline SVG icon element. Decorative: names are always adjacent text. */
export function icon(name, className = '') {
  return el('span', {
    class: className ? `icon ${className}` : 'icon',
    'aria-hidden': 'true',
    html: ICONS[name] || ICONS.info,
  });
}

/** Initials for an avatar circle (first letters of first and last words). */
export function initials(name) {
  const words = String(name || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return '?';
  const first = words[0][0] || '';
  const last = words.length > 1 ? words[words.length - 1][0] || '' : '';
  return (first + last).toUpperCase() || '?';
}

/** Deterministic avatar hue class (stable per name). */
export function avatarTone(name) {
  const text = String(name || '');
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) {
    hash = (hash * 31 + text.charCodeAt(i)) % 997;
  }
  return `avatar--${(hash % 6) + 1}`;
}

/** Colored initials circle (decorative; the display name is always nearby). */
export function avatar(name, { size = '', label = '' } = {}) {
  return el('span', {
    class: `avatar ${avatarTone(name)}${size ? ` avatar--${size}` : ''}`.trim(),
    'aria-hidden': label ? null : 'true',
    'aria-label': label || null,
    text: initials(name),
  });
}

/* ---------------------------------------------------------- keyboard chips */

/**
 * A <kbd> chip for a real keyboard shortcut (e.g. "⌘K", "Enter"). Used in
 * search/command affordances, the AI composer hints, and the command menu
 * footer. `keys` accepts one label or an array rendered as "Shift+Enter".
 */
export function kbdChip(...keys) {
  return el('kbd', { class: 'kbd', text: keys.flat().filter(Boolean).join('+') });
}

/** A shortcut hint line: kbdChip('Enter') + ' to send' entries as children. */
export function shortcutHint(...children) {
  return el('p', { class: 'ai-keyboard-hint' }, children.flat());
}

/* ---------------------------------------------------------- patient cards */

function firstValueOf(source, keys) {
  for (const key of keys) {
    const value = source ? source[key] : null;
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return null;
}

export function patientName(patient) {
  return (
    firstValueOf(patient, ['display_name', 'name', 'full_name']) ||
    [patient.first_name, patient.last_name].filter(Boolean).join(' ') ||
    'Synthetic patient'
  );
}

export function patientMrn(patient) {
  return (
    firstValueOf(patient, ['mrn', 'medical_record_number', 'record_number']) ||
    null
  );
}

export function patientBirthDate(patient) {
  return firstValueOf(patient, ['birth_date', 'date_of_birth', 'dob']);
}

export function patientSex(patient) {
  return firstValueOf(patient, ['sex', 'gender']);
}

export function patientId(patient) {
  return patient ? patient.id || patient.patient_id : null;
}

/**
 * Dashboard/search patient card: initials avatar, bold name, MRN, date of
 * birth + sex, provenance badge, chevron. Real fields only — no invented
 * vitals, insurers, or visit dates. The whole card is the patient link.
 */
export function renderPatientCard(patient) {
  const id = patientId(patient);
  const name = patientName(patient);
  const birth = formatDate(patientBirthDate(patient));
  const sex = patientSex(patient);
  const meta = [birth ? `Born ${birth}` : null, sex || null]
    .filter(Boolean)
    .join(' \u2022 ');

  const body = [
    el('p', { class: 'patient-card__name', text: name }),
    el('p', { class: 'patient-card__mrn mono', text: `MRN: ${patientMrn(patient) || 'Not recorded'}` }),
    meta ? el('p', { class: 'patient-card__meta', text: meta }) : null,
    el('div', { class: 'badge-list' },
      el('span', { class: 'badge badge--synthetic', text: 'Synthetic demo data' }),
    ),
  ];

  const inner = [
    avatar(name, { size: 'lg' }),
    el('div', { class: 'patient-card__body' }, body),
    el('span', { class: 'patient-card__chevron', 'aria-hidden': 'true', html: ICONS.chevronRight }),
  ];

  if (!id) {
    return el('div', { class: 'patient-card card' }, inner);
  }
  return el('a', {
    class: 'patient-card card',
    href: `patient.html?id=${encodeURIComponent(id)}`,
  }, inner);
}

/* --------------------------------------------------------------- loading */

/**
 * Shimmer skeleton placeholder shaped like the content it replaces, so the
 * region's height and rhythm are close to the real data (no layout jump when
 * the content arrives). Variants:
 *   'rows'  (default) — three-bar rows: tables, lists, text sections;
 *   'cards' — avatar circle + name/meta bars: patient card grids.
 * The shimmer is a moving gradient over a solid grey block; under
 * prefers-reduced-motion the CSS drops the shimmer and the static grey blocks
 * remain. The skeleton is always replaced atomically by the real renderer.
 */
export function renderLoading(container, { label = 'Loading', rows = 5, variant = 'rows', wrapperTag = 'div' } = {}) {
  clear(container);
  const wrapper = el(wrapperTag, { class: 'skeleton-block', role: 'status', 'aria-live': 'polite' },
    el('p', { class: 'visually-hidden', text: `${label}\u2026` }),
  );
  for (let i = 0; i < rows; i += 1) {
    if (variant === 'cards') {
      wrapper.append(el('div', { class: 'skeleton-card' },
        el('div', { class: 'skeleton-bar skeleton-bar--circle', 'aria-hidden': 'true' }),
        el('div', { class: 'skeleton-card__body' },
          el('div', { class: 'skeleton-bar skeleton-bar--lg', 'aria-hidden': 'true' }),
          el('div', { class: 'skeleton-bar skeleton-bar--md', 'aria-hidden': 'true' }),
        ),
      ));
    } else {
      wrapper.append(el('div', { class: 'skeleton-row' },
        el('div', { class: 'skeleton-bar skeleton-bar--lg', 'aria-hidden': 'true' }),
        el('div', { class: 'skeleton-bar skeleton-bar--md', 'aria-hidden': 'true' }),
        el('div', { class: 'skeleton-bar skeleton-bar--sm', 'aria-hidden': 'true' }),
      ));
    }
  }
  container.append(wrapper);
}

/**
 * Tiny "Updating…" indicator for stale-while-revalidate background refreshes.
 * The cached content stays on screen; the chip only signals that a refresh is
 * running. Safe to call with on=true/false repeatedly.
 */
export function setSyncIndicator(container, on) {
  if (!container) return;
  const existing = container.querySelector('.sync-chip');
  if (on) {
    if (!existing) {
      const tag = String(container.tagName || '').toUpperCase() === 'UL' ? 'li' : 'p';
      container.append(el(tag, { class: 'sync-chip', role: 'status', text: 'Updating\u2026' }));
    }
  } else if (existing && typeof container.removeChild === 'function') {
    container.removeChild(existing);
  }
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
      class: 'btn btn-secondary btn-sm btn-icon hit-slop',
      type: 'button',
      disabled: currentPage <= 1 ? true : null,
      'aria-label': 'Previous page',
      onClick: () => goTo(currentPage - 1),
    }, icon('chevronLeft', 'icon--sm'), el('span', { class: 'visually-hidden', text: 'Previous' })),
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
      class: 'btn btn-secondary btn-sm btn-icon hit-slop',
      type: 'button',
      disabled: currentPage >= pageCount ? true : null,
      'aria-label': 'Next page',
      onClick: () => goTo(currentPage + 1),
    }, icon('chevronRight', 'icon--sm'), el('span', { class: 'visually-hidden', text: 'Next' })),
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
      class: 'btn btn-ghost btn-sm hit-slop',
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
      el('div', { class: 'dialog__head' },
        el('span', {
          class: 'dialog__icon',
          id: 'app-dialog-icon',
          'aria-hidden': 'true',
          html: ICONS.shield,
        }),
        el('h2', { class: 'dialog__title', id: 'app-dialog-title' }),
      ),
      el('div', { class: 'dialog__body', id: 'app-dialog-body' }),
      el('div', { class: 'dialog__actions', id: 'app-dialog-actions' }),
    ),
  );
  document.body.append(dialog);
  return dialog;
}

/** Sets the dialog head icon tone (default informational shield). */
function setDialogIcon(dialog, { danger = false, glyph = 'shield' } = {}) {
  const iconNode = dialog.querySelector('#app-dialog-icon');
  if (!iconNode) return;
  iconNode.innerHTML = ICONS[glyph] || ICONS.shield;
  iconNode.classList.toggle('dialog__icon--danger', Boolean(danger));
}

function closeDialog(dialog, value) {
  if (typeof dialog.close === 'function' && dialog.open) dialog.close(value);
  else dialog.removeAttribute('open');
}

/**
 * Confirmation dialog for high-impact actions (logout now, admin role changes
 * and deactivation later). `details` optionally renders [{ label, value }]
 * definition rows in the confirmation modal's summary style. Resolves true
 * when confirmed, false otherwise.
 */
export function confirmDialog({
  title = 'Are you sure?',
  message = '',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
  details = [],
} = {}) {
  if (typeof document === 'undefined') return Promise.resolve(false);

  const dialog = ensureDialogHost();
  const titleNode = dialog.querySelector('#app-dialog-title');
  const body = clear(dialog.querySelector('#app-dialog-body'));
  const actions = clear(dialog.querySelector('#app-dialog-actions'));

  setDialogIcon(dialog, { danger, glyph: danger ? 'alert' : 'shield' });
  titleNode.textContent = title;
  body.append(el('p', { text: message }));

  const rows = Array.isArray(details) ? details.filter(Boolean) : [];
  if (rows.length > 0) {
    const list = el('dl', { class: 'confirm-details' });
    for (const row of rows) {
      list.append(
        el('dt', { text: String(row.label == null ? '' : row.label) }),
        el('dd', {}, row.value && row.value.nodeType ? row.value : String(row.value == null ? '' : row.value)),
      );
    }
    body.append(list);
  }

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
        class: 'btn btn-secondary hit-slop',
        type: 'button',
        autofocus: true,
        onClick: () => settle(false),
      }, cancelLabel),
      el('button', {
        class: danger ? 'btn btn-danger hit-slop' : 'btn btn-primary hit-slop',
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

/**
 * Modal dialog with app-authored body nodes and custom action buttons.
 * The native <dialog> traps focus while open; focus moves to the first action
 * on open and returns to the opener on close. Body and actions are cleared on
 * close (including Escape) so transient content such as a one-time password
 * never lingers in the DOM. onClose receives the close value.
 */
export function openDialog({ title = '', bodyNodes = [], actionNodes = [], onClose = null, icon: iconGlyph = 'info', danger = false } = {}) {
  if (typeof document === 'undefined') return { dialog: null, close: () => {} };

  const dialog = ensureDialogHost();
  dialog.querySelector('#app-dialog-title').textContent = title;
  setDialogIcon(dialog, { danger, glyph: iconGlyph });

  const body = clear(dialog.querySelector('#app-dialog-body'));
  for (const node of [bodyNodes].flat()) {
    if (node == null) continue;
    body.append(node.nodeType ? node : document.createTextNode(String(node)));
  }

  const actions = clear(dialog.querySelector('#app-dialog-actions'));
  for (const node of [actionNodes].flat()) {
    if (node == null) continue;
    actions.append(node.nodeType ? node : document.createTextNode(String(node)));
  }

  const opener =
    document.activeElement && typeof document.activeElement.focus === 'function'
      ? document.activeElement
      : null;

  let finished = false;
  const finish = (value) => {
    if (finished) return;
    finished = true;
    dialog.removeEventListener('close', onCloseEvent);
    clear(body);
    clear(actions);
    closeDialog(dialog, value);
    if (opener) opener.focus();
    if (typeof onClose === 'function') onClose(value);
  };
  const onCloseEvent = () => finish(dialog.returnValue);
  dialog.addEventListener('close', onCloseEvent);

  if (typeof dialog.showModal === 'function') {
    dialog.showModal();
    const first = actions.querySelector('button');
    if (first) first.focus();
  }
  return { dialog, close: finish };
}

/** Read-only informational dialog ("About this demo"). */
export function infoDialog({ title = 'About this demo', bodyHTML = '' } = {}) {
  if (typeof document === 'undefined') return;
  const dialog = ensureDialogHost();
  dialog.querySelector('#app-dialog-title').textContent = title;
  setDialogIcon(dialog, { danger: false, glyph: 'info' });

  const body = clear(dialog.querySelector('#app-dialog-body'));
  const fragment = el('div', {});
  fragment.innerHTML = bodyHTML; // app-authored static copy only
  body.append(fragment);

  const actions = clear(dialog.querySelector('#app-dialog-actions'));
  actions.append(el('button', {
    class: 'btn btn-primary hit-slop',
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

const NAV_ICONS = {
  dashboard: 'home',
  patients: 'users',
  ai: 'sparkles',
  admin: 'shield',
};

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
      }, icon(NAV_ICONS[item.key] || 'chevronRight'), item.label),
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
    el('div', { class: 'role-chip' },
      icon('flask', 'icon--sm'),
      el('span', { text: roleText }),
    ),
    el('div', { class: 'user-chip' },
      avatar(user.display_name || user.email || '?', { size: 'sm' }),
      el('div', { class: 'user-chip__text' },
        el('span', { class: 'user-chip__name', text: user.display_name || user.email || 'Signed in' }),
        el('span', { class: 'user-chip__role', text: roleText }),
      ),
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
    }, icon('logout', 'icon--sm'), 'Sign out'),
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
