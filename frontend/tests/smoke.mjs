#!/usr/bin/env node
/* ---------------------------------------------------------------------------
 * frontend/tests/smoke.mjs — zero-dependency checks for the buildless frontend.
 *
 * Run with:  node tests/smoke.mjs        (from the frontend/ directory)
 *            node frontend/tests/smoke.mjs  (from the repo root)
 * Companion check: node tests/render-smoke.mjs executes the page modules
 * against a DOM shim and a stubbed backend (see that file's header).
 *
 * What this covers without a browser or a backend:
 *   1. Static hygiene of the app assets (no console logging, no web storage of
 *      data, no external/third-party resources, required page chrome).
 *   2. Every ES module parses and is import-safe outside the browser.
 *   3. api.js unit behaviour: URL building, CSRF cookie parsing, error mapping.
 *   4. api.js request behaviour against a stubbed fetch (headers, credentials,
 *      error envelope, 429 Retry-After, network failure).
 *
 * What this cannot cover: real DOM rendering, accessibility, and live backend
 * integration. Those are verified manually against a running stack.
 * ------------------------------------------------------------------------- */

import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, '..');
const jsDir = path.join(frontendRoot, 'assets', 'js');

const results = [];

async function check(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
  } catch (error) {
    results.push({ name, ok: false, detail: error && error.message ? error.message : String(error) });
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`);
  }
}

const out = (text) => process.stdout.write(text);

/** Strip block comments and full-line // comments before hygiene matching. */
function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trim().startsWith('//'))
    .join('\n');
}

/* ---------------------------------------------------------- 1. static hygiene */

const htmlFiles = (await readdir(frontendRoot)).filter((f) => f.endsWith('.html')).sort();
const jsFiles = (await readdir(jsDir)).filter((f) => f.endsWith('.js')).sort();

await check('all expected pages exist', () => {
  for (const page of ['index.html', 'dashboard.html', 'patients.html', 'patient.html', 'ai.html', 'admin.html']) {
    assert(htmlFiles.includes(page), `missing ${page}`);
  }
});

for (const file of jsFiles) {
  const source = stripComments(await readFile(path.join(jsDir, file), 'utf8'));
  await check(`assets/js/${file}: no console logging`, () => {
    assert(!/console\s*\./.test(source), 'contains console.* usage');
  });
  await check(`assets/js/${file}: no web storage usage`, () => {
    assert(!/(local|session)Storage/.test(source), 'touches localStorage/sessionStorage');
  });
}

for (const file of htmlFiles) {
  const source = await readFile(path.join(frontendRoot, file), 'utf8');
  await check(`${file}: required chrome present`, () => {
    assert(source.includes('lang="en"'), 'missing lang attribute');
    assert(source.includes('data-open-about'), 'missing "About this demo" control');
    assert(source.includes('mailto:'), 'missing issue-reporting mailto');
    assert(source.includes('id="main"'), 'missing main landmark');
    assert(source.includes('name="viewport"'), 'missing viewport meta');
    assert(source.includes('window.__APP_CONFIG__'), 'missing config override hook');
    assert(source.includes('assets/css/styles.css'), 'missing stylesheet link');
  });
  await check(`${file}: no external resources`, () => {
    const refs = source.match(/(?:src|href)\s*=\s*"(https?:\/\/[^"]+)"/g) || [];
    assert(refs.length === 0, `external references found: ${refs.join(', ')}`);
    assert(!/@import\s+url\(/.test(source), 'external CSS import found');
  });
  await check(`${file}: no console logging`, () => {
    assert(!/console\s*\./.test(source), 'contains console.* usage');
  });
}

/* -------------------------------------------------- 1b. asset and DOM wiring */

await check('patient.html: AI assistant panel is labelled and bounded', async () => {
  const source = await readFile(path.join(frontendRoot, 'patient.html'), 'utf8');
  assert(source.includes('id="ai-question-form"'), 'missing AI question form');
  assert(source.includes('id="ai-char-count"'), 'missing character counter');
  assert(source.includes('id="ai-cancel"'), 'missing cancel control');
  assert(source.includes('not a diagnosis or treatment recommendation'), 'missing AI non-diagnosis label');
  assert(source.includes('2000'), 'missing question limit copy');
});

await check('admin.js: high-impact actions carry explicit consequence copy', async () => {
  const source = await readFile(path.join(jsDir, 'admin.js'), 'utf8');
  assert(source.includes('This grants'), 'missing grant consequence sentence');
  assert(source.includes('This removes their ability to'), 'missing revoke consequence sentence');
  assert(source.includes('Deactivating immediately ends this person'), 'missing session-revocation warning');
  assert(source.includes('This is shown once. Store it now.'), 'missing one-time password warning');
  assert(source.includes('targets your own account'), 'missing self-escalation warning');
});

await check('patient.js: AI output is labelled AI-generated and size bounded', async () => {
  const source = await readFile(path.join(jsDir, 'patient.js'), 'utf8');
  assert(source.includes('AI-generated'), 'missing AI-generated label');
  assert(source.includes('not a diagnosis or treatment recommendation'), 'missing non-diagnosis label');
  assert(source.includes('QUESTION_MAX = 2000'), 'missing 2000-character bound');
});

await check('ai.html: AI Assistant page is labelled and bounded', async () => {
  const source = await readFile(path.join(frontendRoot, 'ai.html'), 'utf8');
  assert(source.includes('id="ai-question-form"'), 'missing AI question form');
  assert(source.includes('id="ai-char-count"'), 'missing character counter');
  assert(source.includes('id="ai-cancel"'), 'missing cancel control');
  assert(source.includes('INFORMATIONAL ONLY'), 'missing informational-only pill');
  assert(source.includes('not a diagnosis or treatment recommendation'), 'missing AI non-diagnosis label');
  assert(source.includes('2000'), 'missing question limit copy');
});

await check('ai-assistant.js: general lookup sends no patient_id and labels AI output', async () => {
  const source = stripComments(await readFile(path.join(jsDir, 'ai-assistant.js'), 'utf8'));
  assert(source.includes('AI-generated'), 'missing AI-generated label');
  assert(source.includes('not a diagnosis or treatment recommendation'), 'missing non-diagnosis label');
  assert(source.includes('QUESTION_MAX = 2000'), 'missing 2000-character bound');
  assert(!/patient_id\s*:/.test(source), 'general lookup must never send patient_id');
});

const entryModules = {
  'index.html': 'login.js',
  'dashboard.html': 'dashboard.js',
  'patients.html': 'patients.js',
  'patient.html': 'patient.js',
  'ai.html': 'ai-assistant.js',
  'admin.html': 'admin.js',
};
const DYNAMIC_IDS = new Set(['app-dialog']);
const CHROME_IDS = ['global-status', 'site-nav', 'user-area', 'nav-toggle'];

for (const file of htmlFiles) {
  const source = await readFile(path.join(frontendRoot, file), 'utf8');
  await check(`${file}: referenced local assets exist`, async () => {
    const refs = [...source.matchAll(/(?:src|href)="([^"]+)"/g)]
      .map((m) => m[1])
      .filter((ref) => !ref.startsWith('#') && !ref.startsWith('mailto:') && !ref.startsWith('data:') && !ref.startsWith('http'));
    for (const ref of refs) {
      const target = path.join(frontendRoot, ref.split('?')[0]);
      const exists = await readFile(target).then(() => true, () => false);
      assert(exists, `missing asset: ${ref}`);
    }
  });

  const entry = entryModules[file];
  await check(`${file}: DOM ids used by ${entry} exist in the page`, async () => {
    const moduleSource = stripComments(await readFile(path.join(jsDir, entry), 'utf8'));
    const usedIds = [...moduleSource.matchAll(/getElementById\('([^']+)'\)/g)].map((m) => m[1]);
    for (const id of usedIds) {
      if (DYNAMIC_IDS.has(id)) continue;
      assert(source.includes(`id="${id}"`), `missing element #${id}`);
    }
  });

  if (file !== 'index.html') {
    await check(`${file}: shared chrome ids present`, () => {
      for (const id of CHROME_IDS) {
        assert(source.includes(`id="${id}"`), `missing chrome element #${id}`);
      }
    });
  }
}

/* ------------------------------------------------- 1c. design-system hygiene */

await check('styles.css defines the design tokens and motion safety', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  for (const token of ['--color-primary', '--color-canvas', '--font-sans', '--space-4', '--radius-lg', '--shadow-1']) {
    assert(css.includes(`${token}:`), `missing design token ${token}`);
  }
  assert(css.includes('prefers-reduced-motion'), 'missing prefers-reduced-motion handling');
  assert(css.includes('prefers-color-scheme: dark'), 'missing dark-scheme token set');
  const code = css.replace(/\/\*[\s\S]*?\*\//g, '');
  assert(!/https?:\/\//.test(code), 'external URL referenced from the stylesheet');
  assert(!/@import\s+url\(/.test(code), 'external CSS import found');
});

await check('no inline style attributes anywhere (CSP style-src self)', async () => {
  for (const file of htmlFiles) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    const found = source.match(/\sstyle\s*=\s*["'][^"']*["']/g) || [];
    assert(found.length === 0, `${file} has inline style attributes`);
  }
  for (const file of jsFiles) {
    const source = stripComments(await readFile(path.join(jsDir, file), 'utf8'));
    assert(!/\bstyle\s*[:=]\s*['"`]/.test(source), `assets/js/${file} sets inline styles`);
    assert(!/setAttribute\(\s*['"]style['"]/.test(source), `assets/js/${file} sets a style attribute`);
    assert(!/\.style\.\w+\s*=/.test(source), `assets/js/${file} mutates element styles directly`);
  }
});

const SHELL_PAGES = ['dashboard.html', 'patients.html', 'patient.html', 'ai.html', 'admin.html'];
for (const file of SHELL_PAGES) {
  const source = await readFile(path.join(frontendRoot, file), 'utf8');
  await check(`${file}: app shell design structure present`, () => {
    assert(source.includes('class="app-shell"'), 'missing app shell');
    assert(source.includes('class="sidebar"'), 'missing sidebar');
    assert(source.includes('class="topbar"'), 'missing topbar');
    assert(source.includes('class="help-card"'), 'missing sidebar help card');
    assert(source.includes('class="brand__mark"'), 'missing brand mark');
    assert(source.includes('MediCore'), 'missing product brand');
  });
}

await check('patient.html: redesigned patient detail structures present', async () => {
  const source = await readFile(path.join(frontendRoot, 'patient.html'), 'utf8');
  for (const marker of [
    'class="card patient-header"',
    'id="patient-avatar"',
    'role="tablist"',
    'class="ai-card"',
    'INFORMATIONAL ONLY',
    'class="ai-card__disclaimer"',
  ]) {
    assert(source.includes(marker), `missing ${marker}`);
  }
});

await check('index.html: brand panel and info-row disclosures present', async () => {
  const source = await readFile(path.join(frontendRoot, 'index.html'), 'utf8');
  for (const marker of ['class="auth-hero"', 'class="info-rows"', 'What this demo is', 'What this demo is not', 'MediCore']) {
    assert(source.includes(marker), `missing ${marker}`);
  }
});

/* ------------------------------------------------------ 2. module import safety */

const api = await import(path.join(jsDir, 'api.js'));
const ui = await import(path.join(jsDir, 'ui.js'));

await check('ui.formatDate renders ISO-8601 UTC dates', () => {
  assertEqual(ui.formatDate('2026-10-26T13:00:00Z'), '26 Oct 2026', 'timestamp format');
  assertEqual(ui.formatDate('2026-10-26'), '26 Oct 2026', 'date-only format');
  assertEqual(ui.formatDate('not-a-date'), null, 'invalid input should be null');
});

await check('ui.roleLabel maps contract role keys to friendly labels', () => {
  assertEqual(ui.roleLabel('doctor'), 'Doctor', 'doctor label');
  assertEqual(ui.roleLabel('clinical_admin'), 'Clinical administrator', 'clinical_admin label');
  assertEqual(ui.roleLabel('tech_admin'), 'Technical administrator', 'tech_admin label');
  assertEqual(ui.roleLabel('nurse'), 'Nurse', 'nurse label');
});

await check('ui.displayDate never renders undefined', () => {
  assertEqual(ui.displayDate(undefined), 'Not recorded', 'undefined fallback');
  assertEqual(ui.displayDate(null), 'Not recorded', 'null fallback');
});

for (const file of jsFiles) {
  await check(`assets/js/${file}: parses and imports in Node`, async () => {
    await import(path.join(jsDir, file));
  });
}

/* ------------------------------------------------------------- 3. api.js units */

await check('buildUrl joins base path and query', () => {
  const url = api.buildUrl('/patients', { q: 'ann lee', limit: 20, offset: 0, ignored: '' });
  assertEqual(url, '/api/v1/patients?q=ann+lee&limit=20&offset=0', 'unexpected URL');
});

await check('buildUrl handles paths without leading slash and empty query', () => {
  assertEqual(api.buildUrl('auth/me', {}), '/api/v1/auth/me', 'unexpected URL');
  assertEqual(api.buildUrl('/health/live'), '/api/v1/health/live', 'unexpected URL');
});

await check('csrfTokenFromCookie reads csrf_token and ignores other cookies', () => {
  const cookie = 'cap_session=opaque; csrf_token=tok-42; other=1';
  assertEqual(api.csrfTokenFromCookie(cookie), 'tok-42', 'csrf cookie not parsed');
  assertEqual(api.csrfTokenFromCookie('a=b'), null, 'missing cookie should be null');
  assertEqual(api.csrfTokenFromCookie(''), null, 'empty cookie should be null');
});

await check('describeApiError maps every contract status to friendly copy', () => {
  const cases = [
    [0, 'Backend unreachable'],
    [400, 'Please check your input'],
    [401, 'Session expired'],
    [403, 'You do not have permission'],
    [404, 'Not found'],
    [409, 'Action not allowed right now'],
    [429, 'Too many requests'],
    [500, 'Something went wrong'],
    [503, 'Service temporarily unavailable'],
  ];
  for (const [status, title] of cases) {
    const error = new api.ApiError({ status, code: 'x', message: '' });
    const info = api.describeApiError(error);
    assertEqual(info.title, title, `status ${status} title`);
    assert(info.message.length > 0, `status ${status} has no message`);
  }
});

await check('describeApiError 429 includes retry seconds', () => {
  const error = new api.ApiError({ status: 429, code: 'rate_limited', retryAfter: 7 });
  assert(api.describeApiError(error).message.includes('7 seconds'), 'retry seconds missing');
});

/* ------------------------------------------------------- 4. api.js request stub */

function jsonResponse(status, body, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (name) => {
        const key = Object.keys(headers).find((h) => h.toLowerCase() === name.toLowerCase());
        return key ? headers[key] : null;
      },
    },
    json: async () => body,
  };
}

await check('request sends credentials, JSON, and X-CSRF-Token on non-GET', async () => {
  const calls = [];
  globalThis.document = { cookie: 'csrf_token=token-9' };
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return jsonResponse(200, { id: 'u1' });
  };
  await api.post('/auth/login', { email: 'a@b.co', password: 'x' });
  const call = calls[0];
  assertEqual(call.url, '/api/v1/auth/login', 'request URL');
  assertEqual(call.init.method, 'POST', 'request method');
  assertEqual(call.init.credentials, 'include', 'credentials mode');
  assertEqual(call.init.headers['X-CSRF-Token'], 'token-9', 'CSRF header');
  assertEqual(call.init.headers['Content-Type'], 'application/json', 'content type');
  assertEqual(call.init.body, '{"email":"a@b.co","password":"x"}', 'request body');
});

await check('request omits CSRF header on GET and when cookie is absent', async () => {
  const calls = [];
  globalThis.document = { cookie: 'cap_session=only' };
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return jsonResponse(200, { items: [], total: 0, limit: 20, offset: 0 });
  };
  await api.get('/patients', { q: 'x', limit: 20, offset: 0 });
  assert(!('X-CSRF-Token' in calls[0].init.headers), 'GET must not send CSRF header');
  assertEqual(calls[0].url, '/api/v1/patients?q=x&limit=20&offset=0', 'query URL');
});

await check('request maps the error envelope to ApiError', async () => {
  globalThis.fetch = async () =>
    jsonResponse(403, {
      error: {
        code: 'forbidden',
        message: 'You do not have permission to perform this action.',
        request_id: 'req-1',
        details: {},
      },
    });
  try {
    await api.get('/staff');
    throw new Error('expected ApiError');
  } catch (error) {
    assert(error instanceof api.ApiError, 'wrong error type');
    assertEqual(error.status, 403, 'status');
    assertEqual(error.code, 'forbidden', 'code');
    assertEqual(error.requestId, 'req-1', 'request id');
  }
});

await check('request surfaces Retry-After on 429', async () => {
  globalThis.fetch = async () =>
    jsonResponse(429, { error: { code: 'rate_limited', message: '' } }, { 'Retry-After': '12' });
  try {
    await api.post('/auth/login', {});
    throw new Error('expected ApiError');
  } catch (error) {
    assertEqual(error.status, 429, 'status');
    assertEqual(error.retryAfter, 12, 'retryAfter');
  }
});

await check('request turns network failure into a friendly ApiError', async () => {
  globalThis.fetch = async () => {
    throw new TypeError('Failed to fetch');
  };
  try {
    await api.get('/patients');
    throw new Error('expected ApiError');
  } catch (error) {
    assertEqual(error.status, 0, 'status');
    assertEqual(error.code, 'network_error', 'code');
  }
});

await check('request returns null for 204 responses', async () => {
  const empty = {
    ok: true,
    status: 204,
    headers: { get: () => null },
    json: async () => {
      throw new Error('no body');
    },
  };
  globalThis.fetch = async () => empty;
  const result = await api.request('/auth/logout', { method: 'POST', body: {} });
  assertEqual(result, null, 'expected null body');
});

/* -------------------------------------------------------------- report */

const failed = results.filter((r) => !r.ok);
for (const result of results) {
  if (!result.ok) out(`FAIL ${result.name}: ${result.detail}\n`);
}
out(`\n${results.length - failed.length}/${results.length} frontend checks passed\n`);
if (failed.length > 0) {
  out(`${failed.length} check(s) failed\n`);
  process.exitCode = 1;
}
