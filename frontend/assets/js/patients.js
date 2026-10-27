// ---------------------------------------------------------------------------
// patients.html page module — synthetic patient list, search, pagination.
// Endpoint: GET /api/v1/patients?q=&limit=&offset= -> { items, total, limit,
// offset } (docs/API.md). Empty and missing results are friendly states, never
// raw errors. Results render as patient cards carrying only real fields:
// display name, MRN, date of birth, sex, provenance.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as cache from './cache.js';
import * as commandMenu from './command-menu.js';
import * as ui from './ui.js';

const PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 300;

const state = {
  query: '',
  offset: 0,
  total: 0,
};

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
    'aria-label': 'Synthetic patient list',
    tabindex: '0',
  }, items.map((patient) => ui.renderPatientCard(patient)));
}

function renderResults(page) {
  const region = document.getElementById('patients-region');
  const items = Array.isArray(page.items) ? page.items : [];
  state.total = Number.isFinite(page.total) ? page.total : items.length;

  if (items.length === 0) {
    ui.renderEmptyState(region, {
      title: 'No synthetic patients match your search',
      message: state.query
        ? `Nothing in the fictional dataset matches \u201c${state.query}\u201d. Try a shorter term, or clear the search to see everyone.`
        : 'The fictional dataset is empty for your account. Seed the demo data and reload.',
    });
  } else {
    ui.clear(region);
    region.append(renderCards(items));
    const paginationHost = ui.el('div', {});
    region.append(paginationHost);
    ui.renderPagination(paginationHost, {
      total: state.total,
      limit: PAGE_SIZE,
      offset: state.offset,
      count: items.length,
      itemLabel: 'synthetic patients',
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
      ? '1 synthetic patient found.'
      : `${state.total} synthetic patients found.`;
  }
}

function load() {
  const region = document.getElementById('patients-region');
  const status = document.getElementById('patients-status');
  const key = cache.keyFor('/patients', { q: state.query, limit: PAGE_SIZE, offset: state.offset });

  // Stale-while-revalidate: a cached page renders instantly and refreshes in
  // the background; a cold cache keeps the original skeleton -> data flow.
  const cached = cache.peek(key);
  if (!cached) {
    if (status) status.textContent = 'Loading synthetic patients.';
    ui.renderLoading(region, { label: 'Loading synthetic patients', rows: 6, variant: 'cards' });
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
    commandMenu.initCommandMenu({
      user,
      commands: [{
        id: 'search-patients',
        title: 'Search synthetic patients',
        hint: 'Focus the patient search box on this page',
        keywords: 'find filter search patients mrn',
        group: 'Actions',
        run: () => {
          const input = document.getElementById('patient-search');
          if (input && typeof input.focus === 'function') input.focus();
        },
      }],
    });
  } catch (error) {
    ui.renderErrorState(document.getElementById('patients-region'), error, {
      describe: api.describeApiError,
      onRetry: () => window.location.reload(),
    });
    return;
  }
  initSearchForm();
  load();
});
