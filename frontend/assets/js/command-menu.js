// ---------------------------------------------------------------------------
// command-menu.js — ⌘K / Ctrl+K command palette (MediCore styling).
//
// One dialog, opened from anywhere in the app shell, filtering (fuzzy-ish)
// over three command sources:
//   1. Navigation — only destinations the signed-in user's permissions allow,
//      derived from GET /auth/me via auth.navItemsFor().
//   2. "Go to patient <name / MRN>" — from the bounded patients list
//      (first 50 records; a note points to the Patients page when there are
//      more). Loaded lazily on first open, only with patient:read.
//   3. Contextual actions supplied by the page module (e.g. "Summarize this
//      patient" on the patient page, the AI suggestion chips on the AI page).
// Plus a real "Keyboard shortcuts" entry that lists the app's actual
// keybindings (no invented shortcuts).
//
// Keyboard: ⌘K/Ctrl+K opens, type filters, ↑/↓ move, Enter runs, Esc closes.
// Focus is trapped by the native <dialog> while open and restored to the
// opener on close. The palette is never a fake control: every entry runs a
// real navigation or an existing page action.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as cache from './cache.js';
import * as overlayHistory from './overlay-history.js';
import * as prefetch from './prefetch.js';
import * as sheet from './sheet.js';
import * as ui from './ui.js';

const PATIENT_LIMIT = 50;

/** The app's REAL keybindings, listed by the "Keyboard shortcuts" command. */
function shortcutsDialog() {
  const shortcuts = [
    ui.el('li', {},
      ui.el('strong', { text: 'Command menu' }), ' \u2014 ',
      ui.kbdChip('\u2318K'), ' or ', ui.kbdChip('Ctrl', 'K'), ' opens it anywhere; type to filter, ',
      ui.kbdChip('\u2191'), ' ', ui.kbdChip('\u2193'), ' to move, ', ui.kbdChip('Enter'), ' to run, ',
      ui.kbdChip('Esc'), ' to close.',
    ),
    ui.el('li', {},
      ui.el('strong', { text: 'AI question box' }), ' \u2014 ',
      ui.kbdChip('Enter'), ' sends the question, ', ui.kbdChip('Shift', 'Enter'), ' inserts a new line.',
    ),
    ui.el('li', {},
      ui.el('strong', { text: 'Patient record tabs' }), ' \u2014 ',
      ui.kbdChip('\u2190'), ' ', ui.kbdChip('\u2192'), ' move between tabs, ',
      ui.kbdChip('Home'), ' and ', ui.kbdChip('End'), ' jump to the first or last tab.',
    ),
    ui.el('li', {},
      ui.el('strong', { text: 'Dialogs' }), ' \u2014 ', ui.kbdChip('Esc'), ' cancels and closes.',
    ),
  ];

  const modal = { close: null };
  const handle = ui.openDialog({
    title: 'Keyboard shortcuts',
    icon: 'help',
    bodyNodes: [
      ui.el('ul', { class: 'shortcut-list' }, shortcuts),
      ui.el('p', {
        class: 'field-hint',
        text: 'These are all of the keyboard shortcuts in this app. There are no others.',
      }),
    ],
    actionNodes: [
      ui.el('button', {
        class: 'btn btn-primary hit-slop',
        type: 'button',
        onClick: () => {
          if (modal.close) modal.close();
        },
      }, 'Close'),
    ],
  });
  modal.close = handle.close;
}

// Per-document wiring guard (module instances can outlive a document in tests).
const wiredDocuments = new WeakSet();

const state = {
  user: null,
  contextual: [],
  patients: [],
  patientsNote: '',
  filtered: [],
  activeIndex: 0,
  opener: null,
  ready: false,
  overlay: null,
};

function menuDialog() {
  let dialog = document.getElementById('command-menu');
  if (dialog) return dialog;

  const input = ui.el('input', {
    id: 'command-menu-input',
    class: 'command-menu__input',
    type: 'text',
    role: 'combobox',
    'aria-expanded': 'true',
    'aria-controls': 'command-menu-list',
    'aria-autocomplete': 'list',
    'aria-label': 'Type a command, a page, or a patient name',
    placeholder: 'Search commands and patients\u2026',
    autocomplete: 'off',
    spellcheck: 'false',
  });

  dialog = ui.el('dialog', { id: 'command-menu', class: 'command-menu', 'aria-label': 'Command menu' },
    ui.el('div', { class: 'command-menu__inner' },
      // Sheet drag handle on touch widths (swipe-down dismisses, cancel
      // semantics only — see sheet.js).
      ui.el('div', { class: 'sheet-grabber', 'aria-hidden': 'true' }),
      ui.el('div', { class: 'command-menu__search' },
        ui.icon('search', 'icon--sm'),
        input,
      ),
      ui.el('ul', {
        id: 'command-menu-list',
        class: 'command-menu__list',
        role: 'listbox',
        'aria-label': 'Commands',
      }),
      ui.el('div', { class: 'command-menu__footer' },
        ui.el('p', { class: 'command-menu__hints' },
          ui.el('span', { class: 'command-menu__hint' },
            ui.kbdChip('\u2191'), ui.kbdChip('\u2193'), ' navigate'),
          ui.el('span', { class: 'command-menu__hint' },
            ui.kbdChip('\u21b5'), ' open'),
          ui.el('span', { class: 'command-menu__hint' },
            ui.kbdChip('esc'), ' close'),
        ),
      ),
    ),
  );

  // Wire every listener once, at creation time.
  input.addEventListener('keydown', onMenuKeydown);
  input.addEventListener('input', () => applyFilter(input.value));
  dialog.addEventListener('keydown', (event) => {
    // Only the input keeps focus while open, but route keys from anywhere in
    // the palette through the same handler.
    if (event && event.target !== input) onMenuKeydown(event);
  });
  dialog.addEventListener('cancel', (event) => {
    if (event && event.preventDefault) event.preventDefault();
    closeCommandMenu();
  });
  dialog.addEventListener('close', () => restoreFocus());
  dialog.addEventListener('click', (event) => {
    // Clicking the scrim (the dialog element itself) closes the palette.
    if (event && event.target === dialog) closeCommandMenu();
  });

  document.body.append(dialog);
  sheet.attachSwipeToDismiss(dialog, {
    close: closeCommandMenu,
    handle: dialog.querySelector('.sheet-grabber'),
  });
  return dialog;
}

function menuInput() {
  return document.getElementById('command-menu-input');
}

function menuList() {
  return document.getElementById('command-menu-list');
}

/** Subsequence match with a small quality score; -1 when it does not match. */
export function fuzzyScore(query, text) {
  const needle = String(query || '').trim().toLowerCase();
  const haystack = String(text || '').toLowerCase();
  if (needle === '') return 0;
  if (haystack.includes(needle)) return 200 - haystack.indexOf(needle);
  let queryIndex = 0;
  let streak = 0;
  let score = 0;
  for (let i = 0; i < haystack.length && queryIndex < needle.length; i += 1) {
    if (haystack[i] === needle[queryIndex]) {
      queryIndex += 1;
      streak += 1;
      score += streak;
    } else {
      streak = 0;
    }
  }
  return queryIndex === needle.length ? score : -1;
}

function allCommands() {
  const commands = [];

  for (const item of auth.navItemsFor(state.user)) {
    commands.push({
      id: `nav-${item.key}`,
      // Destinations keep "Go to …" except when the nav source declares an
      // action-style command label (auth.js: "Open Analytics").
      title: item.commandLabel || `Go to ${item.label}`,
      // Navigation entries deliberately omit the destination filename: the
      // palette is for actions, not for exposing buildless page paths.
      keywords: `navigation page ${item.label}`,
      group: 'Navigation',
      run: () => window.location.assign(item.href),
    });
  }

  for (const patient of state.patients) {
    const name = ui.patientName(patient);
    const mrn = ui.patientMrn(patient);
    commands.push({
      id: `patient-${ui.patientId(patient)}`,
      title: `Go to patient ${name}`,
      hint: mrn ? `MRN ${mrn}` : 'Patient record',
      keywords: `patient record ${name} ${mrn || ''}`,
      group: 'Patients',
      patientId: ui.patientId(patient) != null ? String(ui.patientId(patient)) : null,
      run: () => window.location.assign(`patient.html?id=${encodeURIComponent(ui.patientId(patient))}`),
    });
  }

  for (const command of state.contextual) {
    commands.push({ ...command, group: command.group || 'Actions' });
  }

  commands.push({
    id: 'keyboard-shortcuts',
    title: 'Keyboard shortcuts',
    hint: 'List every keybinding in this app',
    keywords: 'help keys keyboard shortcuts',
    group: 'Help',
    run: shortcutsDialog,
  });

  return commands;
}

function renderList() {
  const list = menuList();
  if (!list) return;
  ui.clear(list);
  state.activeIndex = Math.max(0, Math.min(state.activeIndex, state.filtered.length - 1));

  if (state.filtered.length === 0) {
    list.append(ui.el('li', { class: 'command-menu__empty', text: 'No matches' }));
    return;
  }

  let previousGroup = null;
  state.filtered.forEach((command, index) => {
    if (command.group && command.group !== previousGroup) {
      previousGroup = command.group;
      list.append(ui.el('li', {
        class: 'command-menu__group',
        role: 'presentation',
        text: command.group,
      }));
    }
    const isActive = index === state.activeIndex;
    list.append(ui.el('li', {
      class: isActive ? 'command-menu__option is-active' : 'command-menu__option',
      id: `command-menu-option-${index}`,
      role: 'option',
      'aria-selected': isActive ? 'true' : 'false',
      // Hover/focus intent prefetch target: hovering a "Go to patient" entry
      // warms that patient's detail data (prefetch.js, patient:read only).
      'data-prefetch-patient': command.patientId || null,
      onClick: () => runCommand(command),
    },
      ui.el('span', { class: 'command-menu__option-title', text: command.title }),
      command.hint ? ui.el('span', { class: 'command-menu__option-hint', text: command.hint }) : null,
    ));
  });

  const input = menuInput();
  if (input) {
    input.setAttribute('aria-activedescendant', `command-menu-option-${state.activeIndex}`);
  }

  // Keep the active option visible while arrowing through long lists: follow
  // the selection without jumping when it is already in view. 'nearest' keeps
  // motion minimal and instant (no smooth scrolling to interrupt).
  const activeOption = list.querySelector(`#command-menu-option-${state.activeIndex}`);
  if (activeOption && typeof activeOption.scrollIntoView === 'function') {
    activeOption.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  prefetch.wirePatientIntent(list, state.user);

  if (state.patientsNote) {
    list.append(ui.el('li', { class: 'command-menu__note', role: 'presentation', text: state.patientsNote }));
  }
}

/** Keyboard intent: moving onto a "Go to patient" entry warms its record. */
function warmActiveCommand() {
  const command = state.filtered[state.activeIndex];
  if (command && command.patientId) {
    prefetch.warmPatientRecord(command.patientId, state.user);
  }
}

function applyFilter(query) {
  const scored = [];
  for (const command of allCommands()) {
    const haystack = `${command.title} ${command.hint || ''} ${command.keywords || ''}`;
    const score = fuzzyScore(query, haystack);
    if (score >= 0) scored.push({ command, score });
  }
  scored.sort((a, b) => b.score - a.score);
  state.filtered = scored.map((entry) => entry.command);
  state.activeIndex = 0;
  renderList();
}

function runCommand(command) {
  closeCommandMenu();
  if (command && typeof command.run === 'function') command.run();
}

function onMenuKeydown(event) {
  if (!event) return;
  const key = event.key;
  if (key === 'ArrowDown') {
    if (event.preventDefault) event.preventDefault();
    if (state.filtered.length > 0) {
      state.activeIndex = (state.activeIndex + 1) % state.filtered.length;
      renderList();
      warmActiveCommand();
    }
    return;
  }
  if (key === 'ArrowUp') {
    if (event.preventDefault) event.preventDefault();
    if (state.filtered.length > 0) {
      state.activeIndex = (state.activeIndex - 1 + state.filtered.length) % state.filtered.length;
      renderList();
      warmActiveCommand();
    }
    return;
  }
  if (key === 'Enter') {
    if (event.preventDefault) event.preventDefault();
    const command = state.filtered[state.activeIndex];
    if (command) runCommand(command);
    return;
  }
  if (key === 'Escape') {
    if (event.preventDefault) event.preventDefault();
    closeCommandMenu();
  }
}

/** Load the bounded patient list lazily (first open, patient:read only). */
async function loadPatientCommands() {
  if (!auth.hasPermission(state.user, 'patient:read')) return;
  const key = cache.keyFor('/patients', { limit: PATIENT_LIMIT, offset: 0 });
  await cache.swr(key, () => api.get('/patients', { limit: PATIENT_LIMIT, offset: 0 }), {
    onData: (page) => {
      const items = Array.isArray(page && page.items) ? page.items : [];
      const total = Number.isFinite(page && page.total) ? page.total : items.length;
      state.patients = items.slice(0, PATIENT_LIMIT);
      state.patientsNote = total > items.length
        ? `Showing the first ${items.length} of ${total} patients \u2014 open the Patients page to search the rest.`
        : '';
      const dialog = document.getElementById('command-menu');
      if (dialog && dialog.open) {
        const input = menuInput();
        applyFilter(input ? input.value : '');
      }
    },
  });
}

export function openCommandMenu() {
  const dialog = menuDialog();
  if (dialog.open) return;

  state.opener =
    document.activeElement && typeof document.activeElement.focus === 'function'
      ? document.activeElement
      : null;

  const input = menuInput();
  if (input) input.value = '';
  applyFilter('');

  sheet.resetSheetState(dialog);
  ui.withViewTransition(() => {
    if (typeof dialog.showModal === 'function') dialog.showModal();
  });
  if (input && typeof input.focus === 'function') input.focus();

  // Predictive back (overlay-history.js): Back dismisses the palette first,
  // with the palette's exit transition — it never leaves the page.
  state.overlay = overlayHistory.pushOverlay(() => {
    state.overlay = null;
    closeCommandMenuSurface();
  });

  loadPatientCommands();
}

function restoreFocus() {
  const opener = state.opener;
  state.opener = null;
  if (opener && typeof opener.focus === 'function') opener.focus();
}

function closeCommandMenuSurface() {
  const dialog = document.getElementById('command-menu');
  if (!dialog || !dialog.open) return;
  ui.withViewTransition(() => {
    if (typeof dialog.close === 'function') dialog.close();
    else {
      dialog.removeAttribute('open');
      restoreFocus();
    }
  });
}

export function closeCommandMenu() {
  // UI close path (Esc, scrim click, run command, swipe-dismiss): consume the
  // palette's history entry, then close the surface.
  const entry = state.overlay;
  state.overlay = null;
  if (entry) overlayHistory.releaseOverlay(entry);
  closeCommandMenuSurface();
}

/**
 * Wire the palette for a page: the global ⌘K/Ctrl+K shortcut, the optional
 * trigger button (#command-menu-button), and the page's contextual commands.
 * `commands` is [{ id?, title, hint?, keywords?, run }].
 */
export function initCommandMenu({ user = null, commands = [] } = {}) {
  if (typeof document === 'undefined') return;
  state.user = user;
  state.contextual = Array.isArray(commands) ? commands.filter(Boolean) : [];
  state.patients = [];
  state.patientsNote = '';
  state.filtered = [];
  state.activeIndex = 0;
  state.overlay = null;

  const trigger = document.getElementById('command-menu-button');
  if (trigger) {
    trigger.addEventListener('click', () => openCommandMenu());
  }

  if (wiredDocuments.has(document)) return;
  wiredDocuments.add(document);
  document.addEventListener('keydown', (event) => {
    if (!event) return;
    const isPaletteKey =
      (event.key === 'k' || event.key === 'K') && (event.metaKey || event.ctrlKey) && !event.altKey;
    if (!isPaletteKey) return;
    if (event.preventDefault) event.preventDefault();
    openCommandMenu();
  });
}
