// ---------------------------------------------------------------------------
// patients.html page module — synthetic patient list, search, pagination.
// Endpoint: GET /api/v1/patients?q=&limit=&offset= -> { items, total, limit,
// offset } (docs/API.md). Empty and missing results are friendly states, never
// raw errors.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
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

function patientName(patient) {
  if (patient.display_name) return patient.display_name;
  if (patient.name) return patient.name;
  if (patient.full_name) return patient.full_name;
  const joined = [patient.first_name, patient.last_name].filter(Boolean).join(' ');
  return joined || 'Synthetic patient';
}

function patientMrn(patient) {
  return patient.mrn || patient.medical_record_number || patient.record_number || null;
}

function patientBirthDate(patient) {
  return patient.birth_date || patient.date_of_birth || patient.dob || null;
}

function patientSex(patient) {
  return patient.sex || patient.gender || null;
}

function renderTable(items) {
  const table = ui.el('table', { class: 'table table-stack' },
    ui.el('thead', {},
      ui.el('tr', {},
        ui.el('th', { scope: 'col', text: 'Name' }),
        ui.el('th', { scope: 'col', text: 'MRN' }),
        ui.el('th', { scope: 'col', text: 'Date of birth' }),
        ui.el('th', { scope: 'col', text: 'Sex' }),
      ),
    ),
    ui.el('tbody', {},
      items.map((patient) => {
        const id = patient.id || patient.patient_id;
        const birth = ui.formatDate(patientBirthDate(patient));
        return ui.el('tr', {},
          ui.el('td', { 'data-label': 'Name' },
            id
              ? ui.el('a', { href: `patient.html?id=${encodeURIComponent(id)}`, text: patientName(patient) })
              : ui.el('span', { text: patientName(patient) }),
          ),
          ui.el('td', { 'data-label': 'MRN', text: patientMrn(patient) || 'Not recorded' }),
          ui.el('td', { 'data-label': 'Date of birth', text: birth || 'Not recorded' }),
          ui.el('td', { 'data-label': 'Sex', text: patientSex(patient) || 'Not recorded' }),
        );
      }),
    ),
  );

  return ui.el('div', {
    class: 'table-scroll',
    role: 'region',
    'aria-label': 'Synthetic patient list',
    tabindex: '0',
  }, table);
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
    region.append(renderTable(items));
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
  if (status) status.textContent = 'Loading synthetic patients.';

  ui.renderLoading(region, { label: 'Loading synthetic patients', rows: 6 });

  api
    .get('/patients', { q: state.query, limit: PAGE_SIZE, offset: state.offset })
    .then((page) => {
      ui.hideGlobalBanner();
      renderResults(page);
    })
    .catch((error) => {
      if (status) status.textContent = 'Loading failed.';
      ui.renderErrorState(region, error, {
        describe: api.describeApiError,
        onRetry: load,
      });
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
