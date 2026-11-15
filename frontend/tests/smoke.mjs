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
 *   1. Static hygiene of the app assets (no console logging, no clinical/session
 *      web storage — only the theme preference — no external resources,
 *      required page chrome).
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
// The only sanctioned web-storage uses are the theme and language
// preferences; theme-init.js is a classic pre-paint script, not an ES module.
const THEME_STORAGE_FILES = new Set(['theme-init.js', 'theme.js']);
const LANGUAGE_STORAGE_FILES = new Set(['i18n.js']);
const STORAGE_FILES = new Set([...THEME_STORAGE_FILES, ...LANGUAGE_STORAGE_FILES]);
const CLASSIC_SCRIPTS = new Set(['theme-init.js']);

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

/* ------------------------------------------------------ de-marking hygiene */
// Decision (owner, 2026-10-30; DESIGN.md "De-marking"): the product surface
// carries no origin labelling and no disclaimer copy. The banned fragments are
// assembled from string pieces so this test file does not itself contain the
// banned literals (a raw sweep over the tree stays clean). The first fragment
// is word-bounded and excludes the "demographics" family so standard patient
// demographics terminology is deliberately NOT matched. Code comments are
// exempt (they are stripped before matching) but were reworded anyway;
// DESIGN.md is an internal document and still records the data provenance.
const DEMARKED_COPY = new RegExp(
  [
    `de${'mo'}(?!gra)`,
    `\\bде${'мо'}\\b`,
    `syn${'thetic'}`,
    `not for ${'clinical'}`,
    `not a ${'diagnosis'}`,
    `medical ${'device'}`,
    `informational ${'only'}`,
    `clinical ${'review'}`,
    `not ${'real'}`,
    `de${'monstrat'}`,
  ].join('|'),
  'i',
);

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
const LEGAL_PAGES = new Set(['privacy.html', 'terms.html']);

await check('all expected pages exist', () => {
  for (const page of ['index.html', 'dashboard.html', 'patients.html', 'patient.html', 'ai.html', 'appointments.html', 'admin.html', 'analytics.html', 'messages.html', 'system-logs.html', '404.html', 'privacy.html', 'terms.html']) {
    assert(htmlFiles.includes(page), `missing ${page}`);
  }
});

for (const file of jsFiles) {
  const source = stripComments(await readFile(path.join(jsDir, file), 'utf8'));
  await check(`assets/js/${file}: no console logging`, () => {
    assert(!/console\s*\./.test(source), 'contains console.* usage');
  });
  if (!STORAGE_FILES.has(file)) {
    await check(`assets/js/${file}: no web storage usage`, () => {
      assert(!/(local|session)Storage/.test(source), 'touches localStorage/sessionStorage');
    });
  }
}

await check('System Logs header has no group avatar', async () => {
  const source = await readFile(path.join(frontendRoot, 'system-logs.html'), 'utf8');
  assert(!source.includes('logs-chat__avatar'), 'System Logs header group avatar markup must be removed');
  const header = source.match(/<header class="logs-chat__head">([\s\S]*?)<\/header>/);
  assert(header, 'System Logs chat header missing');
  assert(!/<img\b/.test(header[1]), 'System Logs chat header must not contain decorative images');
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  assert(!css.includes('.logs-chat__avatar'), 'dead System Logs group-avatar CSS must be removed');
});

await check('custom error illustration assets and 404 page are present', async () => {
  for (const file of ['error-404.svg', 'error-connection.svg']) {
    const exists = await readFile(path.join(frontendRoot, 'assets', 'images', file)).then(
      () => true,
      () => false,
    );
    assert(exists, `missing error illustration ${file}`);
  }
  const page = await readFile(path.join(frontendRoot, '404.html'), 'utf8');
  for (const marker of [
    'id="main"',
    'Page not found',
    'assets/images/error-404.svg',
    'assets/js/theme-init.js',
    'data-open-about',
  ]) {
    assert(page.includes(marker), `404.html missing ${marker}`);
  }
  const uiSource = stripComments(await readFile(path.join(jsDir, 'ui.js'), 'utf8'));
  assert(uiSource.includes('error-404.svg'), 'ui.renderErrorState missing 404 illustration');
  assert(uiSource.includes('error-connection.svg'), 'ui.renderErrorState missing connection illustration');
  const backend = await readFile(path.join(frontendRoot, '..', 'backend', 'app', 'main.py'), 'utf8');
  assert(backend.includes('status_code=404'), 'navigation fallback must serve 404.html with status 404');
});

await check('demo staff portraits are local and role-aware', async () => {
  for (const file of ['doctor-1.jpg', 'doctor-2.jpg', 'doctor-3.jpg', 'doctor-4.jpg']) {
    const exists = await readFile(path.join(frontendRoot, 'assets', 'images', 'doctors', file)).then(
      () => true,
      () => false,
    );
    assert(exists, `missing doctor portrait ${file}`);
  }
  for (const file of ['admin-1.jpg', 'doctor-1.jpg', 'nurse-1.jpg']) {
    const exists = await readFile(path.join(frontendRoot, 'assets', 'images', 'staff', file)).then(
      () => true,
      () => false,
    );
    assert(exists, `missing seeded staff portrait ${file}`);
  }
  const uiSource = stripComments(await readFile(path.join(jsDir, 'ui.js'), 'utf8'));
  assert(uiSource.includes('avatarForUser'), 'missing role-aware avatar helper');
  assert(uiSource.includes('SEEDED_STAFF_PORTRAITS'), 'missing seeded staff portrait map');
  assert(uiSource.includes("roles.includes('doctor')"), 'doctor fallback portraits must be gated on the doctor role');
  assert(uiSource.includes('assets/images/doctors/doctor-1.jpg'), 'doctor portrait pool missing');
  assert(uiSource.includes('assets/images/staff/admin-1.jpg'), 'seeded admin portrait missing');
  assert(uiSource.includes('assets/images/staff/doctor-1.jpg'), 'seeded doctor portrait missing');
  assert(uiSource.includes('assets/images/staff/nurse-1.jpg'), 'seeded nurse portrait missing');
});

await check('the repeated full-width page footer is removed', async () => {
  for (const file of htmlFiles) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(!source.includes('class="site-footer'), `${file} still renders the removed full-width footer`);
  }
  const landing = await readFile(path.join(frontendRoot, 'index.html'), 'utf8');
  assert(landing.includes('class="landing-footer"'), 'landing footer missing');
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  assert(!css.includes('.site-footer {'), 'dead site-footer CSS should be removed');
});

await check('theme storage is limited to the sanitized preference key', async () => {
  for (const file of THEME_STORAGE_FILES) {
    const source = await readFile(path.join(jsDir, file), 'utf8');
    assert(source.includes('medicore-theme') || source.includes('THEME_STORAGE_KEY'), `${file} missing theme key`);
    assert(!/patient|clinical|record|diagnos|prescription/i.test(source), `${file} must not store clinical data`);
  }
});

await check('language storage is limited to the preference key', async () => {
  for (const file of LANGUAGE_STORAGE_FILES) {
    const source = await readFile(path.join(jsDir, file), 'utf8');
    assert(source.includes('medicore-language'), `${file} missing language key`);
    assert(source.includes('setItem(LANGUAGE_STORAGE_KEY, value)'), `${file} must store only the language preference`);
  }
});

for (const file of htmlFiles) {
  const source = await readFile(path.join(frontendRoot, file), 'utf8');
  await check(`${file}: required chrome present`, () => {
    assert(source.includes('lang="en"'), 'missing lang attribute');
    assert(source.includes('data-open-about'), 'missing "About" control');
    assert(source.includes('privacy.html'), 'missing Privacy Policy link');
    assert(source.includes('terms.html'), 'missing Terms link');
    assert(!source.includes('mailto:'), 'unverified mailto contact link must not be published');
    assert(source.includes('id="main"'), 'missing main landmark');
    assert(source.includes('name="viewport"'), 'missing viewport meta');
    assert(source.includes('window.__APP_CONFIG__'), 'missing config override hook');
    assert(source.includes('assets/css/styles.css'), 'missing stylesheet link');
    const themeIndex = source.indexOf('assets/js/theme-init.js');
    const cssIndex = source.indexOf('assets/css/styles.css');
    assert(themeIndex >= 0, 'missing pre-paint theme bootstrap');
    assert(themeIndex < cssIndex, 'theme bootstrap must run before the stylesheet');
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
  assert(source.includes('2000'), 'missing question limit copy');
  assert(!source.includes('ai-card__disclaimer'), 'the AI panel must not carry a disclaimer block');
  assert(!source.includes('ai-card__pill'), 'the AI panel must not carry an informational-only pill');
});

await check('admin.js: high-impact actions carry explicit consequence copy', async () => {
  const source = await readFile(path.join(jsDir, 'admin.js'), 'utf8');
  assert(source.includes('This grants'), 'missing grant consequence sentence');
  assert(source.includes('This removes their ability to'), 'missing revoke consequence sentence');
  assert(source.includes('Deactivating immediately ends this person'), 'missing session-revocation warning');
  assert(source.includes('This is shown once. Store it now.'), 'missing one-time password warning');
  assert(source.includes('targets your own account'), 'missing self-escalation warning');
});

await check('patient.js: AI output is unbadged and size bounded', async () => {
  const source = await readFile(path.join(jsDir, 'patient.js'), 'utf8');
  assert(!source.includes('AI-generated'), 'AI-generated badge must not render');
  assert(source.includes('QUESTION_MAX = 2000'), 'missing 2000-character bound');
  assert(!DEMARKED_COPY.test(source), 'AI rendering must not carry de-marked copy');
});

await check('ai.html: AI Assistant page is labelled, bounded and two-pane', async () => {
  const source = await readFile(path.join(frontendRoot, 'ai.html'), 'utf8');
  for (const marker of [
    'id="ai-workspace"',
    'id="ai-history"',
    'id="ai-new-conversation"',
    'id="ai-history-search"',
    'id="ai-history-toggle"',
    'id="ai-context-label"',
    'id="ai-question-form"',
    'id="ai-char-count"',
    'id="ai-cancel"',
  ]) {
    assert(source.includes(marker), `missing ${marker}`);
  }
  assert(source.includes('2000'), 'missing question limit copy');
  assert(!source.includes('ai-history-card'), 'the old below-chat history card must be removed');
  assert(!source.includes('ai-card__disclaimer'), 'the AI card must not carry a disclaimer block');
  assert(!source.includes('ai-card__pill'), 'the AI card must not carry an informational-only pill');
});

await check('ai-assistant.js: general lookup sends no patient_id and keeps AI output unbadged', async () => {
  const source = stripComments(await readFile(path.join(jsDir, 'ai-assistant.js'), 'utf8'));
  assert(!source.includes('AI-generated'), 'AI-generated badge must not render');
  assert(source.includes('QUESTION_MAX = 2000'), 'missing 2000-character bound');
  assert(!/patient_id\s*:/.test(source), 'general lookup must never send patient_id');
  assert(!DEMARKED_COPY.test(source), 'AI rendering must not carry de-marked copy');
});

await check('messages.html: three-pane conversation layout with contact profile', async () => {
  const source = await readFile(path.join(frontendRoot, 'messages.html'), 'utf8');
  for (const marker of [
    'id="messages-workspace"',
    'id="messages-list-panel"',
    'id="messages-detail"',
    'id="messages-profile"',
    'id="messages-new"',
    'class="messages-panel__head"',
  ]) {
    assert(source.includes(marker), `missing ${marker}`);
  }
  const js = stripComments(await readFile(path.join(jsDir, 'messages.js'), 'utf8'));
  assert(js.includes('avatarForUser'), 'message list must use directory-backed staff avatars');
  assert(js.includes('renderProfile'), 'messages page must render the contact profile pane');
  assert(js.includes("api.postForm('/messages/attachments'"), 'message attachments must use multipart upload');
  assert(js.includes('message-composer'), 'messages page must use the inline composer');
  assert(js.includes("ui.icon('paperclip')"), 'attachment control missing');
  assert(!js.includes('openDialog'), 'messages must not use a modal composer');
  assert(!js.includes('can read this message'), 'legacy privacy notice must be removed');
});

await check('appointments.html: calendar, voice, and availability surfaces present', async () => {
  const source = await readFile(path.join(frontendRoot, 'appointments.html'), 'utf8');
  for (const marker of [
    'id="view-switcher"',
    'id="appointments-calendar"',
    'id="appointments-upcoming"',
    'id="voice-listener"',
    'id="voice-record"',
    'id="voice-waveform"',
    'id="voice-cancel"',
    'id="voice-transcript"',
    'id="voice-language"',
    'value="auto" selected',
    'id="voice-suggestions"',
    'id="voice-interpret"',
    'id="voice-example"',
    'id="voice-example-text"',
    'id="voice-example-use"',
    'What to say',
    'id="availability-open"',
    'id="appointment-new"',
    'id="appointments-mode-switcher"',
    'id="appointments-history"',
    'id="appointments-history-filters"',
    'id="appointments-history-region"',
    'id="appointments-history-export"',
  ]) {
    assert(source.includes(marker), `appointments.html missing ${marker}`);
  }
  assert(!/style\s*=/.test(source), 'appointments.html must not use inline styles');
});

await check('appointments.js: clinic timezone, MediaRecorder, and explicit confirmation', async () => {
  const source = stripComments(await readFile(path.join(jsDir, 'appointments.js'), 'utf8'));
  for (const marker of [
    "const CLINIC_TZ = 'Europe/Sofia'",
    'navigator.mediaDevices.getUserMedia',
    'window.MediaRecorder',
    'stopVoiceStream',
    "api.postForm('/voice/transcriptions'",
    'VOICE_EXAMPLES',
    'detectedLanguage',
    'correctionLanguage',
    'insertVoiceExample',
    'setVoiceState',
    'startVoiceMeter',
    'schedulePatientSuggestions',
    'Did you mean',
    "'/appointments/transcription-corrections'",
    'checkTranscriptionCorrections',
    'voice-correction',
    'Keep original',
    'Undo',
    "api.post('/appointments/interpret'",
    "api.post('/appointments'",
    'Create appointment',
    'scheduleActiveExpiryCleanup',
    "api.get('/appointments/history'",
    'appointments/history/export.csv',
    'appointment:export',
  ]) {
    assert(source.includes(marker), `appointments.js missing ${marker}`);
  }
  assert(!/localStorage|sessionStorage|console\s*\./.test(source), 'voice/calendar must not persist or log');
});

const entryModules = {
  'index.html': 'login.js',
  'dashboard.html': 'dashboard.js',
  'patients.html': 'patients.js',
  'patient.html': 'patient.js',
  'ai.html': 'ai-assistant.js',
  'appointments.html': 'appointments.js',
  '404.html': 'error-page.js',
  'admin.html': 'admin.js',
  'analytics.html': 'analytics.js',
  'messages.html': 'messages.js',
  'system-logs.html': 'system-logs.js',
  'privacy.html': 'legal.js',
  'terms.html': 'legal.js',
};
const DYNAMIC_IDS = new Set([
  'app-dialog',
  'new-patient-given',
  'new-patient-family',
  'new-patient-dob',
  'new-patient-sex',
  'new-patient-blood-type',
  'new-patient-error',
  'message-recipient',
  'message-subject',
  'message-body',
  'message-compose-error',
  'message-composer',
  'message-attach',
  'message-file',
  'message-send',
  'message-attachment-preview',
]);
const CHROME_IDS = ['global-status', 'site-nav', 'user-area', 'nav-toggle'];
const SHELL_PAGES = [
  'dashboard.html',
  'patients.html',
  'patient.html',
  'ai.html',
  'appointments.html',
  'admin.html',
  'analytics.html',
  'messages.html',
  'system-logs.html',
];

await check('page metadata is unique and public pages are indexable', async () => {
  const titles = new Map();
  for (const file of htmlFiles) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    const title = (source.match(/<title>([^<]+)<\/title>/) || [])[1];
    assert(title, `${file}: missing title`);
    assert(!titles.has(title), `${file}: duplicate title with ${titles.get(title)}`);
    titles.set(title, file);
  }
  for (const file of ['index.html', 'privacy.html', 'terms.html']) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(source.includes('name="robots" content="index, follow"'), `${file}: public page must be indexable`);
    assert(/<meta name="description" content="[^"]+">/.test(source), `${file}: missing description`);
    assert(source.includes('rel="canonical"'), `${file}: missing canonical`);
    for (const marker of [
      'property="og:title"',
      'property="og:description"',
      'property="og:image"',
      'name="twitter:card"',
      'name="twitter:image"',
    ]) {
      assert(source.includes(marker), `${file}: missing ${marker}`);
    }
    assert(source.includes('/assets/images/og-medicore.png'), `${file}: OG image path missing`);
    assert(source.includes('property="og:image:width" content="1200"'), `${file}: OG width missing`);
    assert(source.includes('property="og:image:height" content="630"'), `${file}: OG height missing`);
    assert(source.includes('property="og:type"'), `${file}: OG type missing`);
  }
  for (const file of [...SHELL_PAGES, '404.html']) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(source.includes('name="robots" content="noindex, nofollow"'), `${file}: authenticated/404 page must stay noindex`);
  }
});

await check('edge headers mark authenticated pages and APIs noindex', async () => {
  const headers = await readFile(path.join(frontendRoot, '_headers'), 'utf8');
  for (const file of [...SHELL_PAGES, '404.html']) {
    assert(headers.includes(`/${file}\n  X-Robots-Tag: noindex, nofollow`), `_headers missing noindex for ${file}`);
  }
  assert(headers.includes('/api/*\n  X-Robots-Tag: noindex, nofollow'), '_headers missing API noindex');
});

await check('favicon and web manifest configuration is complete', async () => {
  for (const file of htmlFiles) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    for (const marker of [
      'rel="icon" href="/favicon.ico"',
      'rel="icon" type="image/svg+xml"',
      'sizes="32x32"',
      'sizes="16x16"',
      'rel="apple-touch-icon"',
      'rel="manifest" href="/site.webmanifest"',
    ]) {
      assert(source.includes(marker), `${file}: missing favicon marker ${marker}`);
    }
  }
  for (const rel of [
    'favicon.ico',
    'apple-touch-icon.png',
    'site.webmanifest',
    'assets/images/favicon.svg',
    'assets/images/favicon-16x16.png',
    'assets/images/favicon-32x32.png',
    'assets/images/favicon-192x192.png',
    'assets/images/favicon-512x512.png',
    'assets/images/og-medicore.png',
  ]) {
    const exists = await readFile(path.join(frontendRoot, rel)).then(() => true, () => false);
    assert(exists, `missing public asset ${rel}`);
  }
});

await check('every rendered image has an alt attribute', async () => {
  for (const file of htmlFiles) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    for (const image of source.matchAll(/<img\b[^>]*>/g)) {
      assert(/\balt=/.test(image[0]), `${file}: image missing alt attribute`);
    }
  }
  const uiSource = stripComments(await readFile(path.join(jsDir, 'ui.js'), 'utf8'));
  assert(uiSource.includes("alt: ''"), 'dynamic avatar images must set an empty alt');
});

await check('landing has above-the-fold and sticky mobile CTAs only', async () => {
  const index = await readFile(path.join(frontendRoot, 'index.html'), 'utf8');
  assert(index.includes('landing-hero__cta'), 'landing hero CTA missing');
  assert(index.includes('landing-mobile-cta'), 'landing sticky mobile CTA missing');
  for (const file of htmlFiles) {
    if (file === 'index.html') continue;
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(!source.includes('landing-mobile-cta'), `${file}: sticky marketing CTA must not appear in clinical pages`);
  }
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  assert(css.includes('.landing-mobile-cta'), 'sticky CTA styles missing');
  assert(/\.landing-mobile-cta[\s\S]*env\(safe-area-inset-bottom\)/.test(css), 'sticky CTA must respect safe-area insets');
  assert(/body\.landing-page[\s\S]*padding-bottom:\s*calc\(5rem \+ env\(safe-area-inset-bottom\)\)/.test(css), 'landing body must reserve space for the sticky CTA');
});

await check('legal pages state the synthetic scope and carry review placeholders', async () => {
  for (const file of LEGAL_PAGES) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(source.includes('legal-placeholder'), `${file}: legal placeholders missing`);
    assert(source.includes('legal counsel'), `${file}: legal review notice missing`);
    assert(!source.includes('mailto:'), `${file}: must not publish an unverified contact link`);
    assert(/synthetic/i.test(source), `${file}: synthetic-data scope missing`);
  }
  const privacy = await readFile(path.join(frontendRoot, 'privacy.html'), 'utf8');
  const terms = await readFile(path.join(frontendRoot, 'terms.html'), 'utf8');
  assert(privacy.includes('PRIVACY CONTACT'), 'privacy contact placeholder missing');
  assert(terms.includes('GOVERNING LAW'), 'governing-law placeholder missing');
  assert(/not approved for clinical diagnosis/i.test(privacy), 'privacy clinical-scope notice missing');
});

await check('no third-party analytics or tracking scripts are installed', async () => {
  const tracking = /\bgtag\s*\(|googletagmanager|google-analytics|plausible|umami|matomo|posthog|mixpanel|hotjar|fullstory|sessioncam|segment\.(?:com|io)/i;
  for (const file of htmlFiles) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(!tracking.test(source), `${file}: third-party tracking script found`);
    assert(!/id="cookie-banner"|class="cookie-banner"/i.test(source), `${file}: cookie banner must not be added for strictly necessary cookies`);
  }
  for (const file of jsFiles) {
    const source = stripComments(await readFile(path.join(jsDir, file), 'utf8'));
    assert(!tracking.test(source), `assets/js/${file}: third-party tracking script found`);
  }
});

await check('no unverified contact address is published', async () => {
  for (const file of htmlFiles) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(!source.includes('support@example.com'), `${file}: placeholder support email found`);
    assert(!source.includes('mailto:'), `${file}: unverified mailto contact found`);
  }
  for (const file of jsFiles) {
    const source = stripComments(await readFile(path.join(jsDir, file), 'utf8'));
    assert(!source.includes('support@example.com'), `assets/js/${file}: placeholder support email found`);
    assert(!source.includes('mailto:'), `assets/js/${file}: unverified mailto contact found`);
  }
});

for (const file of htmlFiles) {
  const source = await readFile(path.join(frontendRoot, file), 'utf8');
  await check(`${file}: referenced local assets exist`, async () => {
    const refs = [...source.matchAll(/(?:src|href)="([^"]+)"/g)]
      .map((m) => m[1])
      .filter((ref) => ref !== '/' && !ref.startsWith('#') && !ref.startsWith('mailto:') && !ref.startsWith('data:') && !ref.startsWith('http'));
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

  if (file !== 'index.html' && file !== '404.html' && !LEGAL_PAGES.has(file)) {
    await check(`${file}: shared chrome ids present`, () => {
      for (const id of CHROME_IDS) {
        assert(source.includes(`id="${id}"`), `missing chrome element #${id}`);
      }
    });
  }
}

/* ------------------------------------------------- 1c. design-system hygiene */

function cssBlock(css, selector) {
  const index = css.indexOf(selector);
  if (index < 0) throw new Error(`missing CSS selector ${selector}`);
  const start = css.indexOf('{', index) + 1;
  const end = css.indexOf('}', start);
  return css.slice(start, end);
}

function colorTokens(block) {
  const tokens = {};
  for (const match of block.matchAll(/--color-([a-z-]+):\s*(#[0-9a-fA-F]{6});/g)) {
    tokens[match[1]] = match[2];
  }
  return tokens;
}

function relativeLuminance(hex) {
  const channels = [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255);
  const linear = channels.map((value) =>
    value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(foreground, background) {
  const a = relativeLuminance(foreground);
  const b = relativeLuminance(background);
  const lighter = Math.max(a, b);
  const darker = Math.min(a, b);
  return (lighter + 0.05) / (darker + 0.05);
}

function assertContrast(tokens, foreground, background, minimum, label) {
  const ratio = contrastRatio(tokens[foreground], tokens[background]);
  assert(
    ratio >= minimum,
    `${label}: ${foreground} vs ${background} contrast ${ratio.toFixed(2)} < ${minimum}`
  );
}

await check('light and dark color tokens meet WCAG AA contrast', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  const light = colorTokens(cssBlock(css, ':root {'));
  const dark = colorTokens(cssBlock(css, ":root[data-theme='dark'] {"));
  for (const [name, tokens] of [
    ['light', light],
    ['dark', dark],
  ]) {
    assertContrast(tokens, 'text', 'canvas', 7, `${name} primary text`);
    assertContrast(tokens, 'text-body', 'canvas', 4.5, `${name} body text`);
    assertContrast(tokens, 'text-muted', 'canvas', 4.5, `${name} secondary text`);
    assertContrast(tokens, 'text-muted', 'surface', 4.5, `${name} secondary text on cards`);
    assertContrast(tokens, 'danger-text', 'danger-soft', 4.5, `${name} error text`);
    assertContrast(tokens, 'warn-text', 'warn-soft', 4.5, `${name} warning text`);
    assertContrast(tokens, 'success-text', 'success-soft', 4.5, `${name} success text`);
    assertContrast(tokens, 'text', 'surface', 7, `${name} primary text on cards`);
    assertContrast(tokens, 'action-contrast', 'action', 4.5, `${name} primary action text`);
  }
  assert(
    css.includes('.field input::placeholder') && css.includes('color: var(--color-text-muted)'),
    'placeholders must use the accessible muted token'
  );
});

await check('styles.css defines the design tokens and motion safety', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  for (const token of ['--color-primary', '--color-canvas', '--font-sans', '--space-4', '--radius-lg', '--shadow-1']) {
    assert(css.includes(`${token}:`), `missing design token ${token}`);
  }
  assert(css.includes('prefers-reduced-motion'), 'missing prefers-reduced-motion handling');
  assert(css.includes('prefers-color-scheme: dark'), 'missing dark-scheme token set');
  assert(css.includes(":root[data-theme='dark']"), 'missing explicit dark token selector');
  assert(css.includes(":root[data-theme='light']"), 'missing explicit light token selector');
  assert(css.includes(':root:not([data-theme])'), 'missing no-JS system fallback');
  const code = css.replace(/\/\*[\s\S]*?\*\//g, '');
  assert(!/https?:\/\//.test(code), 'external URL referenced from the stylesheet');
  assert(!/@import\s+url\(/.test(code), 'external CSS import found');
});

await check('visual identity: contour + thermal background utilities are wired', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  for (const token of [
    '--bg-contour-image',
    '--bg-thermal-image',
    '--voice-contour-image',
    '--surface-sheen',
    '--active-nav-gradient',
    '--focus-ring',
    '--color-cyan',
    '--color-blue',
    '--color-violet',
  ]) {
    assert(css.includes(`${token}:`), `missing visual identity token ${token}`);
  }
  assert(
    css.includes('background-image: var(--bg-thermal-image), var(--bg-contour-image)'),
    'body must layer thermal gradients over contour lines',
  );
  assert(
    css.includes('var(--voice-contour-image)'),
    'voice listener must use the contour layer',
  );
  for (const file of ['contour-lines-light.svg', 'contour-lines-dark.svg']) {
    const exists = await readFile(path.join(frontendRoot, 'assets', 'images', file)).then(
      () => true,
      () => false,
    );
    assert(exists, `missing local contour asset ${file}`);
  }
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
    '@keyframes heart-beat',
    '.command-menu',
    '.toast-region',
    '.sync-chip',
  ]) {
    assert(css.includes(marker), `styles.css missing ${marker}`);
  }
  assert(css.includes('.heart-loader--lg'), 'heart loader size variants missing');
});

await check('heart loader is the app-wide loader; skeletons and the emoji ticker are retired', async () => {
  // The anatomical heart PNG (local asset) is THE loading indicator since
  // 2026-10-27: it must be wired to the component and served by the pages
  // that carry a static pre-JS loading fallback.
  const image = await readFile(path.join(frontendRoot, 'assets', 'images', 'heart-loader.png')).then(
    () => true,
    () => false,
  );
  assert(image, 'missing assets/images/heart-loader.png');
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  const uiSource = stripComments(await readFile(path.join(jsDir, 'ui.js'), 'utf8'));
  assert(css.includes('.heart-loader__img'), 'styles.css must style the heart loader');
  assert(css.includes('@keyframes heart-beat'), 'the lub-dub heartbeat keyframes missing');
  assert(css.includes('.heart-loader--sm') && css.includes('.heart-loader--lg'), 'heart loader sizes missing');
  assert(uiSource.includes('assets/images/heart-loader.png'), 'ui.js must reference the heart PNG');
  for (const file of ['dashboard.html', 'patients.html', 'patient.html', 'appointments.html', 'admin.html', 'analytics.html']) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(source.includes('assets/images/heart-loader.png'), `${file} static loading fallback must show the heart`);
  }

  // Retirement proof (dead-code rule): the superseded visuals must be GONE —
  // the emoji thinking ticker (previous owner request) and the shimmer
  // skeletons are both replaced by the heart loader.
  const cssCode = css.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const banned of ['emoji-pulse', 'ai-emoji-loader', 'skeleton-']) {
    assert(!cssCode.includes(banned), `styles.css must not reference the retired ${banned}`);
  }
  for (const file of jsFiles) {
    const source = stripComments(await readFile(path.join(jsDir, file), 'utf8'));
    for (const banned of ['emoji-pulse', 'ai-emoji-loader', 'emojiLoadingNode', 'skeleton']) {
      assert(!source.includes(banned), `assets/js/${file} must not reference the retired ${banned}`);
    }
  }
  const doc = await readFile(path.join(frontendRoot, 'DESIGN.md'), 'utf8');
  assert(doc.includes('Heart loader'), 'DESIGN.md must document the heart loader');
  assert(doc.includes('2026-10-27'), 'DESIGN.md must note the superseded emoji ticker date');
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

await check('styles.css defines the restrained motion system', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  for (const marker of [
    '--motion-stagger: 30ms',
    '--motion-exit: 160ms',
    '@keyframes row-in',
    '@keyframes settle-in',
    '@keyframes success-pulse',
    '@keyframes command-menu-in',
    '@keyframes sheet-in',
    '::view-transition-old(vt-record-panels)',
    '::view-transition-new(vt-command-menu)',
    'mask-image: linear-gradient',
    'transform: scale(1.12)',
    'overscroll-behavior: contain',
  ]) {
    assert(css.includes(marker), `styles.css missing ${marker}`);
  }
});

await check('pages declare modulepreload hints for the shared module graph', async () => {
  for (const file of htmlFiles) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(source.includes('rel="modulepreload"'), `${file} missing modulepreload hints`);
    assert(source.includes('assets/js/ui.js'), `${file} must warm the ui.js module`);
    if (file === '404.html' || LEGAL_PAGES.has(file)) {
      assert(source.includes('assets/js/theme.js'), `${file} must warm the theme module`);
      continue;
    }
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
    'id="tab-ai-patient"',
    'id="panel-ai-patient"',
    'id="ai-patient-context"',
    'class="ai-card"',
    'AI Patient',
  ]) {
    assert(source.includes(marker), `missing ${marker}`);
  }
});

await check('patient.html: print / save-as-PDF action and print container present', async () => {
  const source = await readFile(path.join(frontendRoot, 'patient.html'), 'utf8');
  for (const marker of [
    'id="print-patient-record"',
    'Print / Save as PDF',
    'id="print-status"',
    'id="print-record"',
    'assets/css/print.css',
    'media="print"',
    'type="button"',
    'aria-describedby="print-status"',
  ]) {
    assert(source.includes(marker), `missing ${marker}`);
  }
  const styles = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  assert(
    /\.print-record\s*\{[^}]*display:\s*none/s.test(styles),
    'styles.css must hide the print record on screen',
  );
});

await check('print.css defines an A4 report and hides the application shell', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'print.css'), 'utf8');
  for (const marker of [
    '@page',
    'size: A4 portrait',
    '@media print',
    '.print-record',
    '.app-shell',
    'display: none !important',
    'display: table-header-group',
    '.print-repeat',
    '.print-running-head',
    'break-inside: avoid',
    '.print-row--abnormal',
    '@bottom-center',
    'counter(page)',
    'counter(pages)',
  ]) {
    assert(css.includes(marker), `print.css missing ${marker}`);
  }
  const code = css.replace(/\/\*[\s\S]*?\*\//g, '');
  assert(!/https?:\/\//.test(code), 'print.css must not reference external resources');
  assert(
    !/position:\s*fixed/.test(code),
    'print.css must not position report elements with fixed positioning',
  );
});

await check('print-record.js stays safe and on-demand', async () => {
  const source = stripComments(await readFile(path.join(jsDir, 'print-record.js'), 'utf8'));
  for (const marker of [
    'fetchAllPages',
    'prepareAndPrint',
    'win.print',
    'printWindow',
    'PrintLoadError',
  ]) {
    assert(source.includes(marker), `print-record.js missing ${marker}`);
  }
  for (const banned of ['innerHTML', 'insertAdjacentHTML', 'localStorage', 'sessionStorage', 'style.setProperty']) {
    assert(!source.includes(banned), `print-record.js must not use ${banned}`);
  }
  assert(!/https?:\/\//.test(source), 'print-record.js must not reference external resources');
});

await check('index.html: brand panel and sign-in form present', async () => {
  const source = await readFile(path.join(frontendRoot, 'index.html'), 'utf8');
  for (const marker of [
    'class="auth-hero',
    'id="login-form"',
    'MediCore',
    'id="password-toggle"',
    'class="landing-shell"',
    'Role-based access with server-side audit logging.',
  ]) {
    assert(source.includes(marker), `missing ${marker}`);
  }
  assert(!source.toLowerCase().includes('hipaa'), 'the landing page must not claim HIPAA compliance');
});

await check('hidden and disabled boolean attributes cannot be overridden accidentally', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  assert(
    /\[hidden\]\s*\{[^}]*display:\s*none\s*!important/.test(css),
    'the hidden attribute must beat component display rules (e.g. the loading block)',
  );
  const uiSource = stripComments(await readFile(path.join(jsDir, 'ui.js'), 'utf8'));
  assert(uiSource.includes('BOOLEAN_ATTRIBUTES'), 'ui.el must special-case boolean attributes');
  assert(uiSource.includes("key === 'readOnly'"), 'ui.el must map readOnly to the readonly attribute');
});

await check('landing page uses the local blurred care-team background', async () => {
  const html = await readFile(path.join(frontendRoot, 'index.html'), 'utf8');
  assert(html.includes('class="landing-ambient__photo"'), 'missing decorative photo layer');
  assert(/landing-ambient[\s\S]*aria-hidden="true"/.test(html), 'photo layer must stay decorative');
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  assert(css.includes("url('../images/doctor-background.png')"), 'CSS must keep the PNG fallback');
  assert(css.includes("url('../images/doctor-background.webp')"), 'CSS must reference the optimized WebP');
  assert(css.includes('image-set('), 'CSS must use image-set for the WebP/PNG fallback');
  assert(/\.landing-ambient__photo[\s\S]*filter:\s*blur\(/.test(css), 'photo layer must be blurred');
  for (const file of ['doctor-background.png', 'doctor-background.webp']) {
    const image = await readFile(path.join(frontendRoot, 'assets', 'images', file)).then(
      () => true,
      () => false,
    );
    assert(image, `missing local doctor background image: ${file}`);
  }
});

await check('product surface carries no de-marked labelling or disclaimer copy (legal pages excepted)', async () => {
  // Static de-marking hygiene (see DEMARKED_COPY above): every clinical and
  // marketing string surface — HTML source (text, attributes, meta) and JS/CSS
  // sources with comments stripped — must stay free of the banned fragments.
  // The public privacy/terms pages are the documented legal exception.
  const cssFiles = ['css/styles.css'].map((f) => path.join('assets', f));
  for (const rel of [...htmlFiles, ...jsFiles.map((f) => path.join('assets', 'js', f)), ...cssFiles]) {
    // Public legal pages are the deliberate exception: privacy/terms must
    // state the synthetic-data scope and carry clearly marked legal
    // placeholders. They are reviewed by counsel before production use.
    if (LEGAL_PAGES.has(rel)) continue;
    // The generated translation dictionary intentionally mirrors legal/source
    // copy (including the synthetic-demo wording) and is not a product surface.
    if (rel === 'assets/js/i18n-bg.js') continue;
    const raw = await readFile(path.join(frontendRoot, rel), 'utf8');
    const source = rel.endsWith('.html') ? raw : stripComments(raw);
    const match = source.match(DEMARKED_COPY);
    assert(!match, `${rel} contains de-marked copy: ${JSON.stringify(match && match[0])}`);
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
  assert(!moduleSource.includes('.style.'), 'analytics.js must not write inline styles');
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  const analyticsCss = css.slice(css.indexOf('analytics dashboard extension'));
  assert(analyticsCss.includes('.analytics-chart-grid'), 'analytics chart grid CSS missing');
  assert(analyticsCss.includes('@media (max-width: 720px)'), 'analytics needs a narrow-screen breakpoint');
  assert(analyticsCss.includes('grid-template-columns: 1fr'), 'analytics charts must collapse to one column');
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

/* --------------------------------------------- 1g. responsive (wave UI-C) */

await check('styles.css scales display type fluidly and steps tokens up for TV/large displays', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  assert(css.includes('clamp('), 'display type must scale fluidly with clamp()');
  const tv = css.match(/@media \(min-width: 1600px\)[\s\S]*?\n\}/);
  assert(tv, 'missing the ≥1600px TV/large-display token step');
  assert(
    /^\s*@media \(min-width: 1600px\) \{\s*:root \{[^}]*\}\s*\}$/.test(tv[0]),
    'the TV step must contain ONLY a :root token override (no per-element magic numbers)',
  );
  for (const token of ['--text-base:', '--text-2xl:', '--space-4:', '--container-max:', '--sidebar-width:']) {
    assert(tv[0].includes(token), `TV step must override ${token}`);
  }
  const uhd = css.match(/@media \(min-width: 2560px\)[\s\S]*?\n\}/);
  assert(uhd, 'missing the ≥2560px 4K token step');
  assert(
    /^\s*@media \(min-width: 2560px\) \{\s*:root \{[^}]*\}\s*\}$/.test(uhd[0]),
    'the 4K step must contain ONLY a :root token override',
  );
  for (const token of ['--text-base:', '--container-max:']) {
    assert(uhd[0].includes(token), `4K step must override ${token}`);
  }
  // The content column grows with the steps: 75rem base -> wider at TV ->
  // wider again at 4K, so cards/tables use more of a large display.
  const maxRem = (block) => Number((block.match(/--container-max:\s*([\d.]+)rem/) || [])[1]);
  assert(maxRem(tv[0]) > 75, 'the TV step must raise the content max-width past the 75rem base');
  assert(maxRem(uhd[0]) > maxRem(tv[0]), 'the 4K step must raise the content max-width again');
});

await check('styles.css keeps 320px windows and 200% zoom overflow-free', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  for (const selector of ['.quick-links', '.patient-cards', '.summary-grid', '.kpi-grid', '.ai-split']) {
    const escaped = selector.replace(/\./g, '\\.');
    assert(
      new RegExp(`${escaped}\\s*\\{[^}]*minmax\\(min\\(`).test(css),
      `${selector} must clamp its auto-fit tracks with minmax(min(…)) so 320px/200%-zoom layouts never overflow`,
    );
  }
  assert(/\.text-break\s*\{[^}]*overflow-wrap: anywhere/.test(css), '.text-break must wrap long names/MRNs');
  for (const selector of [
    '.patient-card__name',
    '.patient-header__main h1',
    '.user-chip__name',
    '.cell-stack__primary',
    '.command-menu__option-title',
  ]) {
    const escaped = selector.replace(/\./g, '\\.').replace(/\s+/g, '\\s+');
    assert(
      new RegExp(`${escaped}[^{]*\\{[^}]*overflow-wrap: anywhere`).test(css),
      `${selector} must wrap long names (Bulgarian compound names, long MRNs) instead of overflowing`,
    );
  }
});

await check('focus rings are tokenised and thickened for TV viewing distance', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  assert(css.includes('--focus-width:'), 'focus-ring thickness must be a root token');
  assert(
    /:focus-visible\s*\{[^}]*outline: var\(--focus-width\)/.test(css),
    ':focus-visible must draw its outline from --focus-width',
  );
  const tv = css.match(/@media \(min-width: 1600px\)[\s\S]*?\n\}/);
  assert(tv[0].includes('--focus-width:'), 'the TV step must thicken focus rings for distance viewing');
});

await check('prefers-reduced-motion kills every animation and transition globally', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  const block = css.match(/@media \(prefers-reduced-motion: reduce\)[\s\S]*?\n\}/);
  assert(block, 'missing the reduced-motion media block');
  assert(block[0].includes('animation: none !important'), 'animations must be disabled globally');
  assert(block[0].includes('transition: none !important'), 'transitions must be disabled globally (sheets snap, toasts pop)');
  assert(block[0].includes('scroll-behavior: auto !important'), 'smooth scrolling must be disabled');
  assert(css.includes('::view-transition-'), 'view-transition pseudo-elements must be motion-gated too');
});

await check('command-menu.js keeps the active option visible while arrowing', async () => {
  const source = await readFile(path.join(jsDir, 'command-menu.js'), 'utf8');
  assert(
    source.includes("scrollIntoView({ block: 'nearest'"),
    'the active option must follow the selection with {block: nearest}',
  );
  assert(
    source.includes('command-menu-option-${state.activeIndex}'),
    'the follow scroll must target the NEW active option id',
  );
});

await check('pages engage safe-area insets and sheets clear the home indicator', async () => {
  for (const file of htmlFiles) {
    const source = await readFile(path.join(frontendRoot, file), 'utf8');
    assert(source.includes('viewport-fit=cover'), `${file} must lay out edge-to-edge so safe-area insets engage`);
  }
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  assert(
    /\.dialog,\s*\.command-menu \{[^}]*env\(safe-area-inset-bottom\)/.test(css),
    'bottom sheets must keep content clear of the home indicator',
  );
});

await check('table scroll regions do not trap vertical page scrolling', async () => {
  const css = await readFile(path.join(frontendRoot, 'assets', 'css', 'styles.css'), 'utf8');
  const rule = css.match(/\.table-scroll\s*\{[^}]*overscroll-behavior-x[^}]*\}/);
  assert(rule, 'missing the horizontal table-scroll overscroll rule');
  assert(rule[0].includes('overscroll-behavior-y: auto'), 'table rows must let vertical wheel/trackpad gestures reach the page');
  assert(
    !/\.command-menu__list,\s*\.ai-chat,\s*\.table-scroll\s*\{/.test(css),
    'table-scroll must not share the two-axis overscroll containment rule',
  );
});

await check('DESIGN.md documents the responsive wave (phone / TV / reduced)', async () => {
  const doc = await readFile(path.join(frontendRoot, 'DESIGN.md'), 'utf8');
  for (const marker of [
    'Responsive',
    'TV',
    'clamp',
    'minmax(min(',
    '--focus-width',
    'viewport-fit',
    'text-break',
  ]) {
    assert(doc.includes(marker), `DESIGN.md missing documentation for ${marker}`);
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
  if (CLASSIC_SCRIPTS.has(file)) continue;
  await check(`assets/js/${file}: parses and imports in Node`, async () => {
    await import(path.join(jsDir, file));
  });
}

await check('theme-init.js is valid classic script syntax', async () => {
  const source = await readFile(path.join(jsDir, 'theme-init.js'), 'utf8');
  assert(source.includes("setAttribute('data-theme'"), 'theme bootstrap must set data-theme');
  assert(source.includes("setAttribute('data-theme-mode'"), 'theme bootstrap must set data-theme-mode');
  assert(source.includes('medicore-theme'), 'theme bootstrap must use the persisted key');
  assert(source.includes('prefers-color-scheme: dark'), 'theme bootstrap must support system mode');
});

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

await check('postForm sends multipart FormData without forcing a JSON content type', async () => {
  const calls = [];
  globalThis.document = { cookie: 'csrf_token=token-9' };
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return jsonResponse(200, { text: 'ok' });
  };
  const form = new FormData();
  form.append('language', 'en');
  form.append('file', new Blob(['audio'], { type: 'audio/webm' }), 'clip.webm');
  await api.postForm('/voice/transcriptions', form);
  const call = calls[0];
  assertEqual(call.url, '/api/v1/voice/transcriptions', 'voice route URL');
  assertEqual(call.init.method, 'POST', 'voice method');
  assert(call.init.body === form, 'FormData must be passed through unchanged');
  assert(!('Content-Type' in call.init.headers), 'FormData must set its own boundary');
  assertEqual(call.init.headers['X-CSRF-Token'], 'token-9', 'voice CSRF header');
  assert(call.init.headers['Idempotency-Key'], 'voice mutation needs an idempotency key');
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
