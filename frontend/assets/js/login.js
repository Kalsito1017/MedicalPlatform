// ---------------------------------------------------------------------------
// index.html page module — landing and sign-in.
// POST /api/v1/auth/login { email, password } (docs/API.md). The session is an
// HttpOnly cookie: nothing is written to web storage. Wrong email and wrong
// password both map to the same 401 message ("Invalid email or password").
// ---------------------------------------------------------------------------

import * as api from './api.js';
import * as auth from './auth.js';
import * as prefetch from './prefetch.js';
import * as theme from './theme.js';
import * as ui from './ui.js';

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function setFieldError(input, message) {
  const errorNode = document.getElementById(`${input.id}-error`);
  if (message) {
    input.setAttribute('aria-invalid', 'true');
    if (errorNode) {
      errorNode.textContent = message;
      errorNode.hidden = false;
    }
  } else {
    input.removeAttribute('aria-invalid');
    if (errorNode) {
      errorNode.textContent = '';
      errorNode.hidden = true;
    }
  }
}

function showFormError(message) {
  const host = document.getElementById('login-status');
  ui.clear(host);
  host.append(
    ui.el('div', { class: 'alert alert--danger', role: 'alert' },
      ui.el('p', { text: message }),
    ),
  );
}

function clearFormError() {
  ui.clear(document.getElementById('login-status'));
}

function serverFieldErrors(error) {
  const details = error && typeof error === 'object' ? error.details : null;
  return details && typeof details === 'object' ? details : null;
}

function fieldMessage(details, fieldName) {
  if (!details) return null;
  const value = details[fieldName];
  if (typeof value === 'string' && value.trim() !== '') return value.trim();
  if (Array.isArray(value) && typeof value[0] === 'string') return value[0];
  return null;
}

function renderSignedIn(user) {
  document.getElementById('login-card').hidden = true;
  const card = document.getElementById('signed-in-card');
  card.hidden = false;

  const roles = Array.isArray(user.roles) ? user.roles : [];
  ui.clear(card);
  card.append(
    ui.el('h2', { class: 'card__title', text: 'You are signed in' }),
    ui.el('dl', { class: 'detail-grid' },
      ui.el('dt', { text: 'Name' }),
      ui.el('dd', { text: user.display_name || user.email || 'Signed in' }),
      ui.el('dt', { text: 'Email' }),
      ui.el('dd', { text: user.email || 'Not recorded' }),
      ui.el('dt', { text: 'Role' }),
      ui.el('dd', { text: roles.length > 0 ? roles.map(ui.roleLabel).join(', ') : 'No role assigned' }),
    ),
    ui.el('div', { class: 'form-actions form-actions--start' },
      ui.el('a', { class: 'btn btn-primary', href: 'dashboard.html' }, 'Go to dashboard'),
      ui.el('button', {
        class: 'btn btn-secondary',
        type: 'button',
        onClick: async () => {
          const confirmed = await ui.confirmDialog({
            title: 'Sign out',
            message: 'Sign out? Your session ends and protected pages will require signing in again.',
            confirmLabel: 'Sign out',
          });
          if (confirmed) await auth.logout();
        },
      }, 'Sign out'),
    ),
  );
}

function initPasswordToggle() {
  const toggle = document.getElementById('password-toggle');
  const passwordInput = document.getElementById('password');
  if (!toggle || !passwordInput) return;

  toggle.addEventListener('click', () => {
    const show = passwordInput.type === 'password';
    passwordInput.type = show ? 'text' : 'password';
    toggle.setAttribute('aria-pressed', show ? 'true' : 'false');
    toggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    if (typeof passwordInput.focus === 'function') passwordInput.focus();
  });
}

function initLoginForm() {
  const form = document.getElementById('login-form');
  const emailInput = document.getElementById('email');
  const passwordInput = document.getElementById('password');
  const submitButton = document.getElementById('login-submit');
  if (!form || !emailInput || !passwordInput || !submitButton) return;

  initPasswordToggle();

  for (const input of [emailInput, passwordInput]) {
    input.addEventListener('input', () => setFieldError(input, null));
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    clearFormError();

    const email = emailInput.value.trim();
    const password = passwordInput.value;
    let firstInvalid = null;

    if (email === '') {
      setFieldError(emailInput, 'Enter your email address.');
      firstInvalid = emailInput;
    } else if (!EMAIL_PATTERN.test(email)) {
      setFieldError(emailInput, 'Enter a valid email address.');
      firstInvalid = emailInput;
    } else {
      setFieldError(emailInput, null);
    }

    if (password === '') {
      setFieldError(passwordInput, 'Enter your password.');
      firstInvalid = firstInvalid || passwordInput;
    } else {
      setFieldError(passwordInput, null);
    }

    if (firstInvalid) {
      firstInvalid.focus();
      return;
    }

    ui.setBusy(submitButton, true, 'Signing in\u2026');
    try {
      await auth.login(email, password);
      // Never cache the user payload: the session cookie is the only store.
      window.location.assign('dashboard.html');
    } catch (error) {
      ui.setBusy(submitButton, false);
      if (error instanceof api.ApiError && error.status === 401) {
        // Uniform message: an unknown email and a wrong password look identical.
        showFormError('Invalid email or password.');
        passwordInput.focus();
        return;
      }
      if (error instanceof api.ApiError && error.status === 400) {
        const details = serverFieldErrors(error);
        const emailError = fieldMessage(details, 'email');
        const passwordError = fieldMessage(details, 'password');
        if (emailError) setFieldError(emailInput, emailError);
        if (passwordError) setFieldError(passwordInput, passwordError);
        if (emailError || passwordError) {
          (emailError ? emailInput : passwordInput).focus();
          return;
        }
      }
      if (error instanceof api.ApiError && error.status === 429) {
        const seconds = error.retryAfter ? Math.ceil(error.retryAfter) : null;
        showFormError(
          seconds
            ? `Too many sign-in attempts. Please wait about ${seconds} seconds and try again.`
            : 'Too many sign-in attempts. Please wait a moment and try again.',
        );
        return;
      }
      const info = api.describeApiError(error);
      showFormError(info.message);
    }
  });
}

ui.onReady(async () => {
  ui.wireFooterDialogs();
  ui.setRetryHandler(() => window.location.reload());
  theme.mountSwitcher(document.querySelector('.landing-header'));
  initLoginForm();
  // Pre-rendering (prefetch.js): a successful sign-in lands on the dashboard,
  // so warm its module on idle (link hint only — nothing is executed early).
  prefetch.warmModules(['assets/js/dashboard.js']);

  try {
    const user = await auth.currentSession();
    if (user) renderSignedIn(user);
  } catch (error) {
    // The sign-in form stays usable; surface the connectivity problem only.
    const info = api.describeApiError(error);
    if (info.retryable) {
      ui.showGlobalBanner({
        tone: 'warn',
        message: 'We cannot confirm your session right now \u2014 the backend did not respond.',
        actionLabel: 'Retry',
        onAction: () => window.location.reload(),
      });
    }
  }
});
