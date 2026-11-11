// ---------------------------------------------------------------------------
// patient-create.js — shared patient creation form/dialog.
//
// Used by patients.js (modal) and the appointment patient picker (inline).
// The backend owns the MRN/provenance and enforces patient:write; callers only
// gate the visible control. No clinical data is stored in the browser.
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as cache from './cache.js';
import * as ui from './ui.js';

const SEX_OPTIONS = [
  ['female', 'Female'],
  ['male', 'Male'],
  ['other', 'Other'],
  ['unknown', 'Unknown'],
];
const BLOOD_TYPE_OPTIONS = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-', 'unknown'];

export function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

/** Build the shared fields without a nested <form> (dialogs already own one). */
export function buildPatientCreateForm() {
  const element = ui.el('div', { class: 'form-grid' });
  const givenInput = ui.el('input', {
    id: 'new-patient-given',
    name: 'given_name',
    type: 'text',
    maxlength: 200,
    autocomplete: 'off',
    required: true,
  });
  const familyInput = ui.el('input', {
    id: 'new-patient-family',
    name: 'family_name',
    type: 'text',
    maxlength: 200,
    autocomplete: 'off',
    required: true,
  });
  const dobInput = ui.el('input', {
    id: 'new-patient-dob',
    name: 'date_of_birth',
    type: 'date',
    max: todayIso(),
  });
  const sexInput = ui.el('select', { id: 'new-patient-sex', name: 'sex' },
    SEX_OPTIONS.map(([value, label]) => ui.el('option', { value, text: label })),
  );
  sexInput.value = 'unknown';
  const bloodInput = ui.el('select', { id: 'new-patient-blood-type', name: 'blood_type' },
    ui.el('option', { value: '', text: 'Not recorded' }),
    BLOOD_TYPE_OPTIONS.map((value) => ui.el('option', { value, text: value })),
  );

  element.append(
    ui.el('div', { class: 'field' },
      ui.el('label', { for: 'new-patient-given', text: 'Given name' }),
      givenInput,
    ),
    ui.el('div', { class: 'field' },
      ui.el('label', { for: 'new-patient-family', text: 'Family name' }),
      familyInput,
    ),
    ui.el('div', { class: 'field' },
      ui.el('label', { for: 'new-patient-dob', text: 'Date of birth (optional)' }),
      dobInput,
    ),
    ui.el('div', { class: 'field' },
      ui.el('label', { for: 'new-patient-sex', text: 'Sex' }),
      sexInput,
    ),
    ui.el('div', { class: 'field' },
      ui.el('label', { for: 'new-patient-blood-type', text: 'Blood type (optional)' }),
      bloodInput,
    ),
  );

  const errorNode = ui.el('p', { class: 'field-error', id: 'new-patient-error', role: 'alert' });
  errorNode.hidden = true;

  function read() {
    return {
      given_name: givenInput.value.trim(),
      family_name: familyInput.value.trim(),
      date_of_birth: dobInput.value || null,
      sex: sexInput.value || 'unknown',
      blood_type: bloodInput.value || null,
    };
  }

  function setError(message) {
    errorNode.textContent = message || '';
    errorNode.hidden = !message;
  }

  function reset() {
    givenInput.value = '';
    familyInput.value = '';
    dobInput.value = '';
    sexInput.value = 'unknown';
    bloodInput.value = '';
    setError('');
  }

  return {
    element,
    errorNode,
    read,
    setError,
    reset,
    focus: () => givenInput.focus(),
  };
}

export function validatePatientInput(input) {
  if (!input.given_name || !input.family_name) {
    return 'Enter both a given name and a family name.';
  }
  if (input.date_of_birth && input.date_of_birth > todayIso()) {
    return 'Date of birth cannot be in the future.';
  }
  return null;
}

export function openCreatePatientDialog({ onCreated } = {}) {
  const fields = buildPatientCreateForm();
  const cancelButton = ui.el('button', {
    class: 'btn btn-secondary',
    type: 'button',
    onClick: () => close(),
  }, 'Cancel');
  const saveButton = ui.el('button', {
    class: 'btn btn-primary',
    type: 'button',
    onClick: () => submit(),
  }, 'Create patient');

  const submit = async () => {
    const input = fields.read();
    const message = validatePatientInput(input);
    if (message) {
      fields.setError(message);
      return;
    }
    fields.setError('');
    ui.setBusy(saveButton, true, 'Creating\u2026');
    try {
      const created = await api.post('/patients', input);
      cache.invalidate('/patients');
      close();
      if (typeof onCreated === 'function') onCreated(created);
      else window.location.assign(`patient.html?id=${encodeURIComponent(created.id)}`);
    } catch (error) {
      fields.setError(api.describeApiError(error).message);
      ui.setBusy(saveButton, false);
    }
  };

  fields.element.addEventListener('keydown', (event) => {
    if (event && event.key === 'Enter' && event.target && event.target.tagName !== 'TEXTAREA') {
      event.preventDefault();
      submit();
    }
  });

  const { close } = ui.openDialog({
    title: 'Add patient',
    icon: 'plus',
    bodyNodes: [fields.element, fields.errorNode],
    actionNodes: [cancelButton, saveButton],
  });
  fields.focus();
}
