// ---------------------------------------------------------------------------
// patients.html page module — patient list, search, pagination.
// Endpoint: GET /api/v1/patients?q=&limit=&offset= -> { items, total, limit,
// offset } (docs/API.md). Empty and missing results are friendly states, never
// raw errors. Results render as patient cards carrying only real fields:
// display name, MRN, date of birth, sex.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as cache from './cache.js';
import * as commandMenu from './command-menu.js?v=20261109j';
import * as prefetch from './prefetch.js';
import { openCreatePatientDialog } from './patient-create.js';
import * as ui from './ui.js';

const PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 300;

const state = {
  query: '',
  offset: 0,
  total: 0,
};

// Signed-in user (for permission-gated intent prefetch of patient cards).
let currentUser = null;

function readStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const q = params.get('q');
  const offset = Number.parseInt(params.get('offset') || '', 10);
  state.query = typeof q === 'string' ? q.trim() : '';
  state.offset = Number.isFinite(offset) && offset > 0 ? offset : 0;
}

function syncUrl() {
  const params = new URLSearchParams();
  if (state.query) params.set('q', state.query);
  if (state.offset > 0) params.set('offset', String(state.offset));
  const qs = params.toString();
  const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  window.history.replaceState(null, '', url);
}

function renderCards(items) {
  return ui.el('div', {
    class: 'patient-cards',
    role: 'region',
    'aria-label': 'Patient list',
    tabindex: '0',
  }, items.map((patient) => ui.renderPatientCard(patient)));
}

function renderResults(page) {
  const region = document.getElementById('patients-region');
  const items = Array.isArray(page.items) ? page.items : [];
  state.total = Number.isFinite(page.total) ? page.total : items.length;

  if (items.length === 0) {
    ui.renderEmptyState(region, {
      title: 'No patients match your search',
      message: state.query
        ? `Nothing matches \u201c${state.query}\u201d. Try a shorter term, or clear the search to see everyone.`
        : 'No patients are recorded for your account yet.',
    });
  } else {
    ui.clear(region);
    region.append(renderCards(items));
    // Hover/focus intent prefetch for the freshly rendered cards.
    prefetch.wirePatientIntent(region, currentUser);
    const paginationHost = ui.el('div', {});
    region.append(paginationHost);
    ui.renderPagination(paginationHost, {
      total: state.total,
      limit: PAGE_SIZE,
      offset: state.offset,
      count: items.length,
      itemLabel: 'patients',
      onChange: (nextOffset) => {
        state.offset = nextOffset;
        syncUrl();
        load();
      },
    });
  }

  const status = document.getElementById('patients-status');
  if (status) {
    status.textContent = state.total === 1
      ? '1 patient found.'
      : `${state.total} patients found.`;
  }
}

function load() {
  const region = document.getElementById('patients-region');
  const status = document.getElementById('patients-status');
  const key = cache.keyFor('/patients', { q: state.query, limit: PAGE_SIZE, offset: state.offset });

  // Stale-while-revalidate: a cached page renders instantly and refreshes in
  // the background; a cold cache keeps the original loader -> data flow.
  const cached = cache.peek(key);
  if (!cached) {
    if (status) status.textContent = 'Loading patients.';
    ui.renderLoading(region, { label: 'Loading patients', rows: 6, variant: 'cards' });
  }

  cache.swr(key, () => api.get('/patients', { q: state.query, limit: PAGE_SIZE, offset: state.offset }), {
    onData: (page) => {
      ui.hideGlobalBanner();
      renderResults(page);
    },
    onError: (error) => {
      if (status) status.textContent = 'Loading failed.';
      ui.renderErrorState(region, error, {
        describe: api.describeApiError,
        onRetry: load,
      });
    },
    onRevalidating: (on) => ui.setSyncIndicator(region, on),
  });
}

function initSearchForm() {
  const form = document.getElementById('patient-search-form');
  const input = document.getElementById('patient-search');
  const clearButton = document.getElementById('patient-search-clear');
  if (!form || !input) return;

  input.value = state.query;
  if (clearButton) clearButton.hidden = state.query === '';

  let debounceTimer = null;
  const runSearch = () => {
    state.query = input.value.trim();
    state.offset = 0;
    if (clearButton) clearButton.hidden = state.query === '';
    syncUrl();
    load();
  };

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (debounceTimer) window.clearTimeout(debounceTimer);
    runSearch();
  });

  input.addEventListener('input', () => {
    if (clearButton) clearButton.hidden = input.value.trim() === '';
    if (debounceTimer) window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(runSearch, SEARCH_DEBOUNCE_MS);
  });

  if (clearButton) {
    clearButton.addEventListener('click', () => {
      input.value = '';
      clearButton.hidden = true;
      input.focus();
      runSearch();
    });
  }
}

ui.onReady(async () => {
  ui.setRetryHandler(load);
  readStateFromUrl();
  try {
    const user = await auth.initAuthChrome({ active: 'patients' });
    if (!user) return;
    currentUser = user;
    const createButton = document.getElementById('patient-new');
    const canCreate = auth.hasPermission(user, 'patient:write');
    if (createButton) {
      createButton.hidden = !canCreate;
      if (canCreate) createButton.addEventListener('click', () => openCreatePatientDialog());
    }
    const commands = [{
      id: 'search-patients',
      title: 'Search patients',
      hint: 'Focus the patient search box on this page',
      keywords: 'find filter search patients mrn',
      group: 'Actions',
      run: () => {
        const input = document.getElementById('patient-search');
        if (input && typeof input.focus === 'function') input.focus();
      },
    }];
    if (canCreate) {
      commands.push({
        id: 'add-patient',
        title: 'Add patient',
        hint: 'Create a new patient record',
        keywords: 'new create register patient',
        group: 'Actions',
        run: openCreatePatientDialog,
      });
    }
    commandMenu.initCommandMenu({ user, commands });
  } catch (error) {
    ui.renderErrorState(document.getElementById('patients-region'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
    return;
  }
  initSearchForm();
  // Pre-rendering (prefetch.js): on idle, warm the modules a navigation from
  // this page will likely need (link hints only — nothing is executed early).
  prefetch.warmModules(['assets/js/patient.js']);
  load();
});
