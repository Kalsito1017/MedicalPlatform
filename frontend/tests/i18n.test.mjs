/* Bulgarian localization regression checks.
 *
 * Verifies dictionary integrity, placeholder preservation, dynamic patterns,
 * immediate EN/BG switching, browser-language default, persistence, and that
 * clinical identifiers are never translated.
 *
 * Run with: node frontend/tests/i18n.test.mjs
 */
import { installDom, settle, textOf } from './domshim.mjs';
import { BG } from '../assets/js/i18n-bg.js';
import { readFileSync } from 'node:fs';

const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
  } catch (error) {
    results.push({
      name,
      ok: false,
      detail: error && error.message ? error.message : String(error),
    });
  }
}
function assert(condition, message) {
  if (!condition) throw new Error(message);
}
function equal(actual, expected, message) {
  if (actual !== expected) throw new Error(`${message} (got ${JSON.stringify(actual)})`);
}

const PLACEHOLDER = /\{([a-zA-Z0-9_]+)\}/g;
function placeholders(value) {
  return [...String(value).matchAll(PLACEHOLDER)].map((match) => match[1]).sort();
}

function decodeEntities(value) {
  return String(value)
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCodePoint(parseInt(code, 16)))
    .replace(/&mdash;/g, '\u2014')
    .replace(/&middot;/g, '\u00b7')
    .replace(/&ldquo;/g, '\u201c')
    .replace(/&rdquo;/g, '\u201d')
    .replace(/&rsquo;/g, '\u2019')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

/** Visible text segments of a legal page, with code/script/style removed. */
function legalTextSegments(file) {
  const html = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
  return html
    .replace(/<!--[\s\S]*?-->/g, '\n')
    .replace(/<script[\s\S]*?<\/script>/gi, '\n')
    .replace(/<style[\s\S]*?<\/style>/gi, '\n')
    .replace(/<code[\s\S]*?<\/code>/gi, '\n')
    .replace(/<pre[\s\S]*?<\/pre>/gi, '\n')
    .replace(/<textarea[\s\S]*?<\/textarea>/gi, '\n')
    .replace(/<[^>]*>/g, '\n')
    .split('\n')
    .map(decodeEntities)
    .map((value) => value.replace(/\s+/g, ' ').trim())
    .filter((value) => value && /[A-Za-z\u0410-\u044f]/.test(value));
}

const CORE_TERMS = {
  Dashboard: 'Табло',
  Patients: 'Пациенти',
  Patient: 'Пациент',
  Appointments: 'Срещи',
  Appointment: 'Среща',
  'Appointment type': 'Вид среща',
  Availability: 'Работно време',
  'Follow-up Visit': 'Контролно посещение',
  Abnormal: 'Извън нормата',
  Normal: 'В норма',
  High: 'Над нормата',
  Low: 'Под нормата',
  'Staff accounts': 'Профили на служители',
  'Active staff accounts': 'Активни профили на служители',
  'Create a staff account': 'Създаване на профил на служител',
  'System Logs': 'Логове',
  'Terms and Conditions': 'Условия и правила',
  'Privacy Policy': 'Политика за поверителност',
  'Back to dashboard': 'Обратно към таблото',
  'View all patients': 'Всички пациенти',
  Born: 'Роден(а)',
};

await check('dictionary has no missing or empty Bulgarian entries', () => {
  const entries = Object.entries(BG);
  assert(entries.length >= 1400, `dictionary unexpectedly small: ${entries.length}`);
  for (const [key, value] of entries) {
    assert(typeof key === 'string' && key.length > 0, 'empty dictionary key');
    assert(typeof value === 'string' && value.trim().length > 0, `empty value for ${key}`);
  }
});

await check('all interpolation placeholders are preserved', () => {
  for (const [key, value] of Object.entries(BG)) {
    const expected = placeholders(key);
    if (expected.length === 0) continue;
    const actual = placeholders(value);
    assert(
      JSON.stringify(actual) === JSON.stringify(expected),
      `placeholder mismatch for ${JSON.stringify(key)}: ${JSON.stringify(actual)}`,
    );
  }
});

await check('core terminology is consistently translated', () => {
  for (const [key, expected] of Object.entries(CORE_TERMS)) {
    equal(BG[key], expected, `core term ${key}`);
  }
  const joined = Object.values(BG).join(' ');
  for (const forbidden of ['бекенд', 'акаунт', 'абнорм', 'одиторски', 'медикамент']) {
    assert(!joined.toLowerCase().includes(forbidden), `unreviewed term remains: ${forbidden}`);
  }
});

await check('legal pages have complete Bulgarian coverage', () => {
  const skip = new Set([
    'Това е български превод за удобство. При несъответствие меродавен е английският текст.',
  ]);
  for (const file of ['privacy.html', 'terms.html']) {
    const missing = legalTextSegments(file).filter(
      (text) => !skip.has(text) && !(text in BG),
    );
    assert(
      missing.length === 0,
      `${file}: missing translations: ${missing.slice(0, 5).join(' | ')}`,
    );
  }
});

await check('EN/BG switching translates text and restores English', async () => {
  installDom();
  const i18n = await import('../assets/js/i18n.js');
  const host = document.createElement('div');
  const label = document.createTextNode('Dashboard');
  host.append(label);
  document.body.append(host);
  i18n.setLanguage('bg');
  equal(textOf(host), 'Табло', 'Bulgarian translation');
  i18n.setLanguage('en');
  equal(textOf(host), 'Dashboard', 'English restoration');
});

await check('dynamic counts, durations and status transitions are localized', async () => {
  installDom();
  const i18n = await import('../assets/js/i18n.js');
  i18n.setLanguage('bg');
  equal(i18n.t('{seconds}s of audio', { seconds: 5.4 }), '5.4 сек. аудио', 'duration interpolation');
  equal(i18n.t('Transcription ready ({detail}). Review and edit it below, then propose an appointment.', {
    detail: 'Български, 5.4 сек. аудио',
  }), 'Транскрипцията е готова (Български, 5.4 сек. аудио). Прегледайте и редактирайте текста по-долу, след което предложете запис.', 'voice status interpolation');

  const host = document.createElement('div');
  host.append(
    document.createTextNode('7 patients found.'),
    document.createTextNode('5 minutes ago'),
    document.createTextNode('Status: Scheduled → Completed'),
  );
  document.body.append(host);
  i18n.applyLanguage(host);
  const text = textOf(host);
  assert(text.includes('Намерени пациенти: 7.'), `count pattern missing: ${text}`);
  assert(text.includes('преди 5 минути'), `relative time pattern missing: ${text}`);
  assert(text.includes('Статус: Планиран → Завършен'), `status pattern missing: ${text}`);
  i18n.setLanguage('en');
});

await check('Bulgarian pluralization is correct for 0/1/2/5/11/21/25/101', async () => {
  installDom();
  const i18n = await import('../assets/js/i18n.js');
  i18n.setLanguage('bg');
  const cases = [
    ['1 patient found.', 'Намерен е 1 пациент.'],
    ['0 patients found.', 'Намерени пациенти: 0.'],
    ['2 patients found.', 'Намерени пациенти: 2.'],
    ['5 patients found.', 'Намерени пациенти: 5.'],
    ['11 patients found.', 'Намерени пациенти: 11.'],
    ['21 patients found.', 'Намерени пациенти: 21.'],
    ['25 patients found.', 'Намерени пациенти: 25.'],
    ['101 patients found.', 'Намерени пациенти: 101.'],
    ['0 minutes', '0 минути'],
    ['1 minute', '1 минута'],
    ['2 minutes', '2 минути'],
    ['5 minutes', '5 минути'],
    ['11 minutes', '11 минути'],
    ['21 minutes', '21 минути'],
    ['25 minutes', '25 минути'],
    ['101 minutes', '101 минути'],
    ['0 years', '0 години'],
    ['1 year', '1 година'],
    ['2 years', '2 години'],
    ['21 years', '21 години'],
    ['101 years', '101 години'],
    ['0 unread', '0 непрочетени'],
    ['1 unread', '1 непрочетено'],
    ['2 unread', '2 непрочетени'],
    ['21 unread', '21 непрочетени'],
    ['101 unread', '101 непрочетени'],
    ['0 possible patients — choose one', '0 възможни пациенти — изберете един'],
    ['1 possible patient — choose one', '1 възможен пациент — изберете един'],
    ['2 possible patients — choose one', '2 възможни пациенти — изберете един'],
    ['1 result · complete available record', '1 резултат · пълен наличен запис'],
    ['2 results · complete available record', '2 резултата · пълен наличен запис'],
  ];
  for (const [source, expected] of cases) {
    const host = document.createElement('div');
    host.append(document.createTextNode(source));
    document.body.append(host);
    i18n.applyLanguage(host);
    equal(textOf(host), expected, `pluralization: ${source}`);
  }
  i18n.setLanguage('en');
});

await check('rapid EN/BG switching stays stable and ends in the requested language', async () => {
  installDom();
  const i18n = await import('../assets/js/i18n.js');
  const host = document.createElement('div');
  host.append(document.createTextNode('Dashboard'));
  document.body.append(host);
  for (let index = 0; index < 500; index += 1) {
    i18n.setLanguage(index % 2 === 0 ? 'bg' : 'en');
  }
  equal(i18n.getLanguage(), 'en', 'final language after rapid switching');
  equal(textOf(host), 'Dashboard', 'final text after rapid switching');
});

await check('clinical identifiers and names are never translated', async () => {
  installDom();
  const i18n = await import('../assets/js/i18n.js');
  const host = document.createElement('div');
  host.append(
    document.createTextNode('Елена Георгиева'),
    document.createTextNode('MRN-0001'),
    document.createTextNode('Glucose 5.5 mg/dL'),
  );
  document.body.append(host);
  i18n.setLanguage('bg');
  const text = textOf(host);
  assert(text.includes('Елена Георгиева'), 'patient name changed');
  assert(text.includes('MRN-0001'), 'MRN changed');
  assert(text.includes('Glucose 5.5 mg/dL'), 'clinical value changed');
  i18n.setLanguage('en');
});

await check('dashboard patient cards localize birth and sex labels', async () => {
  installDom();
  const i18n = await import('../assets/js/i18n.js');
  const ui = await import('../assets/js/ui.js');
  i18n.setLanguage('bg');
  const card = ui.renderPatientCard({
    id: 'p1',
    display_name: 'Test Patient',
    mrn: 'MRN-0001',
    birth_date: '1990-01-02',
    sex: 'female',
  });
  document.body.append(card);
  i18n.applyLanguage(card);
  const text = textOf(card);
  assert(text.includes('Роден(а)'), `birth label not translated: ${text}`);
  assert(text.includes('Женски'), `sex label not translated: ${text}`);
  assert(!text.includes('female'), `lowercase sex leaked: ${text}`);
  i18n.setLanguage('en');
  assert(textOf(card).includes('Born'), 'birth label not restored');
  assert(textOf(card).includes('Female'), 'sex label not restored');
});

await check('attributes and browser default/persistence are handled', async () => {
  installDom();
  const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const storageDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const store = new Map();
  Object.defineProperty(globalThis, 'navigator', {
    value: { language: 'bg-BG', languages: ['bg-BG'] },
    configurable: true,
  });
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, String(value)),
    },
    configurable: true,
  });
  try {
    const fresh = await import(`../assets/js/i18n.js?browser=${Date.now()}`);
    equal(fresh.getLanguage(), 'bg', 'browser language default');
    fresh.setLanguage('en');
    equal(store.get('medicore-language'), 'en', 'language persistence');

    const input = document.createElement('input');
    input.setAttribute('placeholder', 'Search by name or MRN');
    document.body.append(input);
    fresh.setLanguage('bg');
    equal(input.getAttribute('placeholder'), 'Търсене по име или MRN', 'placeholder translation');
  } finally {
    if (navigatorDescriptor) Object.defineProperty(globalThis, 'navigator', navigatorDescriptor);
    else delete globalThis.navigator;
    if (storageDescriptor) Object.defineProperty(globalThis, 'localStorage', storageDescriptor);
    else delete globalThis.localStorage;
  }
});

await check('legal page shows Back to dashboard when a session exists', async () => {
  installDom();
  const header = document.createElement('header');
  header.className = 'legal-header';
  const actions = document.createElement('div');
  actions.className = 'legal-header__actions';
  const headerLink = document.createElement('a');
  headerLink.setAttribute('href', 'index.html');
  headerLink.setAttribute('data-legal-session-link', '');
  headerLink.textContent = 'Sign in';
  actions.append(headerLink);
  header.append(actions);
  const footerLink = document.createElement('a');
  footerLink.setAttribute('href', 'index.html');
  footerLink.setAttribute('data-legal-session-link', '');
  footerLink.textContent = 'Sign in';
  document.body.append(header, footerLink);

  globalThis.fetch = async (url) => {
    if (String(url).includes('/auth/me')) {
      return new Response(JSON.stringify({
        id: 'u1',
        email: 'doctor@example.com',
        display_name: 'Dr Test',
        roles: ['doctor'],
        permissions: ['patient:read'],
        is_active: true,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  await import(`../assets/js/legal.js?legal=${Date.now()}`);
  await settle(25);
  equal(headerLink.getAttribute('href'), 'dashboard.html', 'header link target');
  equal(footerLink.getAttribute('href'), 'dashboard.html', 'footer link target');
  equal(textOf(headerLink), 'Back to dashboard', 'header link label');
  equal(textOf(footerLink), 'Back to dashboard', 'footer link label');
});

for (const result of results) {
  process.stdout.write(`${result.ok ? 'ok' : 'FAIL'} ${result.name}${result.detail ? ` \u2014 ${result.detail}` : ''}\n`);
}
const failed = results.filter((result) => !result.ok).length;
process.stdout.write(`i18n: ${results.length - failed}/${results.length} passed\n`);
if (failed) process.exit(1);
