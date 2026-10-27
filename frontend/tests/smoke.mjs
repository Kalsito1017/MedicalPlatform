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
  'analytics.html': 'analytics.js',
};
const DYNAMIC_IDS = new Set(['app-dialog']);
const CHROME_IDS = ['global-status', 'site-nav', 'user-area', 'nav-toggle'];
const SHELL_PAGES = ['dashboard.html', 'patients.html', 'patient.html', 'ai.html', 'admin.html', 'analytics.html'];

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
  // ALLOWLIST (one entry, documented in DESIGN.md "Swipe-to-dismiss sheets" and
  // verified exactly below): sheet.js may write the `--sheet-dy` CSS custom
  // property on the SHEET ELEMENT ONLY — the lead's pre-authorized exception
  // for finger-following swipe-to-dismiss (a running pixel offset cannot be
  // encoded as an HTML attribute). Nothing else in the app may write styles;
  // every other module keeps the full ban.
  const STYLE_WRITE_ALLOWLIST = new Set(['sheet.js']);
  for (const file of jsFiles) {
    const source = stripComments(await readFile(path.join(jsDir, file), 'utf8'));
    assert(!/\bstyle\s*[:=]\s*['"`]/.test(source), `assets/js/${file} sets inline styles`);
    assert(!/setAttribute\(\s*['"]style['"]/.test(source), `assets/js/${file} sets a style attribute`);
    assert(!/cssText/.test(source), `assets/js/${file} mutates element styles directly`);
    if (STYLE_WRITE_ALLOWLIST.has(file)) continue;
    assert(!/\.style\b/.test(source), `assets/js/${file} touches element styles directly`);
    assert(!/setProperty\s*\(/.test(source), `assets/js/${file} mutates CSS custom properties`);
  }
});

await check('answer rendering never parses HTML (innerHTML family banned)', async () => {
  // The AI answer body is untrusted model output (an XSS boundary): the
  // markdown.js renderer and the three answer renderers must build DOM nodes
  // only. ui.js keeps its documented innerHTML uses for APP-AUTHORED markup
  // (icons, static dialog copy) and is deliberately not in this list.
  for (const file of ['markdown.js', 'ai-assistant.js', 'patient.js', 'ai-history.js']) {
    const source = stripComments(await readFile(path.join(jsDir, file), 'utf8'));
    for (const banned of ['innerHTML', 'insertAdjacentHTML', 'outerHTML', 'DOMParser', 'parseFromString']) {
      assert(!source.includes(banned), `assets/js/${file} must never use ${banned} on answer content`);
    }
  }
});

await check('sheet.js writes only the sanctioned --sheet-dy custom property', async () => {
  const source = stripComments(await readFile(path.join(jsDir, 'sheet.js'), 'utf8'));
  const writes = source.match(/setProperty\s*\(/g) || [];
  assertEqual(writes.length, 1, 'sheet.js must contain exactly one style write');
  assert(
    /sheet\.style\.setProperty\('--sheet-dy',\s*`\$\{Math\.round\(value\)\}px`\)/.test(source),
    'the only style write must be sheet.style.setProperty(--sheet-dy, …) on the sheet element',
  );
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  assert(css.includes('var(--sheet-dy, 0px)'), 'styles.css must consume --sheet-dy');
});

/* --------------------------------------------- 1d. wave UI pattern hygiene */

await check('styles.css defines the wave UI pattern utilities', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  for (const marker of [
    '.kbd',
    '.hit-slop',
    '.tabular-nums',
    'text-wrap: balance',
    'corner-shape: squircle',
    'env(safe-area-inset',
    'skeleton-shimmer',
    '.command-menu',
    '.toast-region',
    '.sync-chip',
  ]) {
    assert(css.includes(marker), `styles.css missing ${marker}`);
  }
  assert(css.includes('skeleton-bar--circle'), 'card-shaped skeleton variant missing');
});

/* --------------------------------------------- 1e. wave UI-B pattern hygiene */

await check('styles.css defines the wave UI-B pattern utilities', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  for (const marker of [
    '.blur-placeholder',
    '.blur-region.is-ready',
    '@keyframes blur-in',
    'view-transition-name',
    '::view-transition-old(vt-dialog-surface)',
    'prefers-reduced-motion: reduce',
    '.sheet-grabber',
    'var(--sheet-dy, 0px)',
    '@keyframes toast-in',
    '@keyframes overlay-in',
    '@keyframes reveal-in',
  ]) {
    assert(css.includes(marker), `styles.css missing ${marker}`);
  }
  const code = css.replace(/\/\*[\s\S]*?\*\//g, '');
  assert(!/https?:\/\//.test(code), 'external URL referenced from the stylesheet');
});

await check('pages declare modulepreload hints for the shared module graph', async () => {
  for (const file of htmlFiles) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(source.includes('rel="modulepreload"'), `${file} missing modulepreload hints`);
    assert(source.includes('assets/js/ui.js'), `${file} must warm the ui.js module`);
    assert(source.includes('assets/js/api.js'), `${file} must warm the api.js module`);
  }
});

await check('prefetch.js is permission-gated, in-memory, TTL-respecting, idle-scheduled', async () => {
  const source = stripComments(await readFile(path.join(jsDir, 'prefetch.js'), 'utf8'));
  assert(source.includes("'patient:read'"), 'prefetch must gate on patient:read');
  assert(source.includes('cache.peek'), 'prefetch must respect the cache TTL');
  assert(source.includes('modulepreload'), 'module warm-up must use link hints (no early execution)');
  assert(source.includes('requestIdleCallback'), 'warm-up must run on idle with a fallback');
  assert(!/(local|session)Storage/.test(source), 'prefetch must stay in-memory only');
  assert(source.includes('setTimeout'), 'intent prefetch must be debounced');
});

await check('overlay-history.js unwinds overlays LIFO and never breaks a UI close', async () => {
  const source = stripComments(await readFile(path.join(jsDir, 'overlay-history.js'), 'utf8'));
  assert(source.includes('pushState'), 'overlays must push history entries');
  assert(source.includes("addEventListener('popstate'"), 'Back must be handled via popstate');
  assert(source.includes('stack.pop()'), 'overlays must unwind LIFO');
  assert(source.includes('selfBacksPending'), 'self-inflicted popstates must never double-close');
  assert(source.includes('entry.pushed'), 'history.back() must only run for entries this module pushed');
});

await check('DESIGN.md documents the wave UI-B patterns and their decisions', async () => {
  const doc = await readFile(path.join(frontendRoot, 'DESIGN.md'), 'utf8');
  for (const marker of [
    'Pre-rendering',
    'Blur placeholders',
    'View transitions',
    'Swipe-to-dismiss',
    'Interruptible',
    'Predictive back',
    'Idempotency',
    '--sheet-dy',
  ]) {
    assert(doc.includes(marker), `DESIGN.md missing documentation for ${marker}`);
  }
});

await check('forms carry correct autofill semantics', async () => {
  const index = await readFile(path.join(frontendRoot, 'index.html'), 'utf8');
  assert(/autocomplete="email"/.test(index), 'login email must carry autocomplete="email"');
  assert(/autocomplete="current-password"/.test(index), 'login password must carry autocomplete="current-password"');
  const admin = await readFile(path.join(frontendRoot, 'admin.html'), 'utf8');
  assert(/id="create-email"[^>]*autocomplete="email"/.test(admin), 'staff email must carry autocomplete="email"');
  assert(/id="create-name"[^>]*autocomplete="name"/.test(admin), 'staff display name must carry autocomplete="name"');
  assert(/id="create-password"[^>]*autocomplete="new-password"/.test(admin), 'staff password must carry autocomplete="new-password"');
  // The app has no OTP surface: a one-time-code token would be a lie.
  for (const file of htmlFiles) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(!source.includes('one-time-code'), `${file} claims an OTP autofill surface that does not exist`);
  }
});

await check('kbd shortcut chips render on every app-shell page', async () => {
  for (const file of SHELL_PAGES) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(source.includes('class="kbd"'), `${file} missing kbd shortcut chips`);
    assert(source.includes('id="command-menu-button"'), `${file} missing the command-menu affordance`);
  }
});

await check('every shell page module wires the command menu', async () => {
  for (const file of SHELL_PAGES) {
    const entry = entryModules[file];
    const source = stripComments(await readFile(path.join(jsDir, entry), 'utf8'));
    assert(source.includes('initCommandMenu'), `${entry} must open the command menu (⌘K)`);
  }
});

await check('command-menu.js only advertises real keybindings', async () => {
  const source = await readFile(path.join(jsDir, 'command-menu.js'), 'utf8');
  for (const marker of ['Keyboard shortcuts', 'Shift', 'Enter', 'Escape', 'ArrowDown']) {
    assert(source.includes(marker), `command-menu.js missing ${marker} in its shortcut handling`);
  }
});

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

/* ------------------------------------------------ 1f. wave U (analytics) */

await check('analytics.html keeps the CSP bootstrap block byte-identical', async () => {
  // The inline window.__APP_CONFIG__ block is hashed into the CSP
  // (frontend/_headers + scripts/security_check.py): a single changed byte
  // breaks the hash, so the analytics page must copy it VERBATIM.
  const bootstrap = (source) => {
    const match = source.match(/<script>\s*\/\* Deployment override[\s\S]*?<\/script>/);
    assert(match, 'missing inline config bootstrap block');
    return match[0];
  };
  const analytics = await readFile(path.join(frontendRoot, 'analytics.html'), 'utf8');
  const admin = await readFile(path.join(frontendRoot, 'admin.html'), 'utf8');
  assertEqual(bootstrap(analytics), bootstrap(admin), 'bootstrap block drifted from admin.html');
  assert(bootstrap(analytics).includes('window.__APP_CONFIG__ = window.__APP_CONFIG__ || {};'), 'bootstrap hook missing');
});

await check('analytics page + module use pure CSS/SVG charts with no external resources', async () => {
  const analytics = await readFile(path.join(frontendRoot, 'analytics.html'), 'utf8');
  const moduleSource = stripComments(await readFile(path.join(jsDir, 'analytics.js'), 'utf8'));
  assert(!analytics.includes('<canvas'), 'canvas is not allowed (pure CSS/SVG charts)');
  assert(!moduleSource.includes('<canvas'), 'canvas is not allowed (pure CSS/SVG charts)');
  for (const banned of ['chart.js', 'chartjs', 'd3.', 'echarts', 'highcharts', 'https://']) {
    assert(!moduleSource.includes(banned), `analytics.js must not reference ${banned}`);
  }
  assert(moduleSource.includes("role: 'img'"), 'charts must carry role="img"');
  assert(moduleSource.includes("'aria-label'"), 'charts must carry aria-label summaries');
  assert(moduleSource.includes('visually-hidden'), 'charts must ship hidden data tables');
  assert(moduleSource.includes("'stroke-dasharray'"), 'the donut arc must be an attribute, not a style');
});

await check('analytics nav and palette command are permission-gated in the shared modules', async () => {
  const auth = stripComments(await readFile(path.join(jsDir, 'auth.js'), 'utf8'));
  const menu = stripComments(await readFile(path.join(jsDir, 'command-menu.js'), 'utf8'));
  assert(auth.includes("'analytics:read'"), 'auth.js must gate the nav item on analytics:read');
  assert(auth.includes('commandLabel'), 'auth.js must declare the palette label for Analytics');
  assert(auth.includes('Open Analytics'), 'the palette label must be "Open Analytics"');
  assert(menu.includes('item.commandLabel'), 'command-menu.js must surface the nav-declared command label');
  const analytics = await readFile(path.join(frontendRoot, 'analytics.html'), 'utf8');
  assert(analytics.includes('Analytics'), 'analytics.html must be titled Analytics');
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
  assertEqual(ui.roleLabel('clinical_admin'), 'Admin', 'clinical_admin label');
  assertEqual(ui.roleLabel('nurse'), 'Nurse', 'nurse label');
  assert(
    ui.roleLabel('tech_admin') !== 'Technical administrator',
    'the removed tech_admin label must not exist',
  );
});

await check('ui.displayDate never renders undefined', () => {
  assertEqual(ui.displayDate(undefined), 'Not recorded', 'undefined fallback');
  assertEqual(ui.displayDate(null), 'Not recorded', 'null fallback');
});

await check('command shortcut chips are platform-neutral', async () => {
  assertEqual(ui.commandShortcutLabel('MacIntel'), '\u2318K', 'macOS label');
  assertEqual(ui.commandShortcutLabel('iPhone'), '\u2318K', 'iOS label');
  assertEqual(ui.commandShortcutLabel('Win32'), 'Ctrl K', 'Windows label');
  assertEqual(ui.commandShortcutLabel('Linux x86_64'), 'Ctrl K', 'Linux label');
  assertEqual(ui.commandShortcutLabel(''), 'Ctrl/\u2318 K', 'unknown platform must stay neutral');
  for (const file of SHELL_PAGES) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(source.includes('data-command-shortcut'), `${file} missing the adaptive command chip`);
    assert(
      !/<kbd class="kbd">&#8984;K<\/kbd>/.test(source),
      `${file} hardcodes the macOS-only glyph in a chip`,
    );
  }
});

await check('ui.withViewTransition falls back gracefully and wraps updates', () => {
  const previousDocument = globalThis.document;
  // Fallback (no startViewTransition): the update runs synchronously and the
  // returned object mimics the ViewTransition shape.
  globalThis.document = {};
  let ran = 0;
  const fallback = ui.withViewTransition(() => {
    ran += 1;
    return 'done';
  });
  assertEqual(ran, 1, 'the fallback must run the update immediately');
  assert(fallback.finished instanceof Promise, 'fallback must expose finished');
  assertEqual(typeof fallback.skipTransition, 'function', 'fallback must expose skipTransition');

  // Supported: the update is handed to document.startViewTransition exactly once.
  let viaStub = 0;
  globalThis.document = {
    startViewTransition(update) {
      viaStub += 1;
      const value = update();
      return { finished: Promise.resolve(value), skipTransition() {} };
    },
  };
  ui.withViewTransition(() => {
    ran += 1;
  });
  assertEqual(viaStub, 1, 'startViewTransition must be used when available');
  assertEqual(ran, 2, 'the update must run exactly once through the stub');
  globalThis.document = previousDocument;
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

/* ------------------------------------------ 5. idempotency key behaviour */

await check('mutating requests carry a fresh Idempotency-Key; GETs never do', async () => {
  const calls = [];
  globalThis.document = { cookie: 'csrf_token=token-9' };
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return jsonResponse(200, { ok: true });
  };
  await api.get('/patients');
  await api.post('/auth/login', { email: 'a@b.co', password: 'x' });
  await api.patch('/staff/u1', { is_active: false });
  await api.del('/staff/u1/roles/nurse');
  assert(!('Idempotency-Key' in calls[0].init.headers), 'GET must not send Idempotency-Key');
  const keys = [calls[1], calls[2], calls[3]].map((call) => call.init.headers['Idempotency-Key']);
  for (const key of keys) {
    assert(typeof key === 'string' && key.length > 0, 'mutating request missing Idempotency-Key');
  }
  assertEqual(new Set(keys).size, 3, 'every NEW mutating request must get a fresh key');
});

await check('retrying a request reuses its Idempotency-Key', async () => {
  const calls = [];
  globalThis.document = { cookie: '' };
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return jsonResponse(200, { ok: true });
  };
  await api.post('/ai/ask', { question: 'x' }, { idempotencyKey: 'retry-key-1' });
  await api.post('/ai/ask', { question: 'x' }, { idempotencyKey: 'retry-key-1' });
  assertEqual(calls[0].init.headers['Idempotency-Key'], 'retry-key-1', 'retry must reuse the key');
  assertEqual(calls[1].init.headers['Idempotency-Key'], 'retry-key-1', 'retry must reuse the key');
});

await check('newIdempotencyKey is random and never encodes request data', () => {
  const first = api.newIdempotencyKey();
  const second = api.newIdempotencyKey();
  assert(typeof first === 'string' && first.length >= 16, 'key must be a random token');
  assert(first !== second, 'keys must be fresh');
  assert(!first.includes('patient'), 'a key must never encode request contents');
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
