// ---------------------------------------------------------------------------
// i18n.js — English/Bulgarian UI localization.
//
// The application is authored in English. This module keeps the selected
// language in localStorage (`medicore-language`), follows the browser language
// on first visit, translates static HTML and rendered UI text from the shared
// dictionary, and exposes `t()` for new code. It never translates clinical
// data: only exact UI phrases (or explicit patterns) are replaced.
//
// A MutationObserver translates text that page modules render after load, so
// the dictionary covers both static pages and dynamic tables/dialogs. Text
// nodes keep their English source, allowing an instant switch back to English.
// ---------------------------------------------------------------------------

import { BG } from './i18n-bg.js';

export const LANGUAGE_STORAGE_KEY = 'medicore-language';
export const LANGUAGES = ['en', 'bg'];
export const LANGUAGE_LABELS = { en: 'English', bg: 'Bulgarian' };

const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'CODE', 'PRE', 'TEXTAREA']);
const ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];

// Common dynamic phrases that cannot be exact dictionary keys because they
// contain counts or dates. Dates themselves are formatted in the active locale
// by ui.js/appointments.js before they reach these rules.
const PATTERNS = [
  [/^(\d+) patients? found\.$/, (m) => `Намерени пациенти: ${m[1]}.`],
  [/^(\d+) staff accounts? found\.$/, (m) => `Намерени профили на служители: ${m[1]}.`],
  [/^(\d+) audit events? found\.$/, (m) => `Намерени събития за одит: ${m[1]}.`],
  [/^(\d+) messages? found\.$/, (m) => `Намерени съобщения: ${m[1]}.`],
  [/^(\d+) unread$/, (m) => `${m[1]} ${m[1] === '1' ? 'непрочетено' : 'непрочетени'}`],
  [/^(\d+) appointment(?:s)?$/, (m) => `${m[1]} ${m[1] === '1' ? 'запис' : 'записа'}`],
  [/^(\d+) possible patients? — choose one$/, (m) => `${m[1]} ${m[1] === '1' ? 'възможен пациент' : 'възможни пациенти'} — изберете един`],
  [/^(\d+) result(?:s)? · complete available record$/, (m) => `${m[1]} ${m[1] === '1' ? 'резултат' : 'резултата'} · пълен наличен запис`],
  [/^(\d+) prescription(?:s)? · active first, then start date$/, (m) => `${m[1]} ${m[1] === '1' ? 'рецепта' : 'рецепти'} · първо активните, после началната дата`],
  [/^(\d+) recorded visit(?:s)?$/, (m) => `${m[1]} ${m[1] === '1' ? 'записано посещение' : 'записани посещения'}`],
  [/^(\d+) minutes?$/, (m) => `${m[1]} ${m[1] === '1' ? 'минута' : 'минути'}`],
  [/^(\d+) hours? ago$/, (m) => `преди ${m[1]} ${m[1] === '1' ? 'час' : 'часа'}`],
  [/^(\d+) days? ago$/, (m) => `преди ${m[1]} ${m[1] === '1' ? 'ден' : 'дни'}`],
  [/^(\d+) minutes? ago$/, (m) => `преди ${m[1]} ${m[1] === '1' ? 'минута' : 'минути'}`],
  [/^(\d+) years?$/, (m) => `${m[1]} ${m[1] === '1' ? 'година' : 'години'}`],
  [/^(\d+(?:\.\d+)?)s of audio$/, (m) => `${m[1]} сек. аудио`],
  [/^(\d+(?:\.\d+)?)s recorded$/, (m) => `${m[1]} сек. запис`],
  [/^Retry after (\d+)s\.$/, (m) => `Опитайте отново след ${m[1]} сек.`],
  [/^(\d+) \/ 2000 characters$/, (m) => `${m[1]} / 2000 знака`],
  [/^Showing (\d+)[–-](\d+) of (\d+)$/, (m) => `Показани ${m[1]}–${m[2]} от ${m[3]}`],
  [/^(\d+) matching events? in the selected period\.$/, (m) => `${m[1]} ${m[1] === '1' ? 'съвпадащо събитие' : 'съвпадащи събития'} в избрания период.`],
  [/^(\d+) clinical-record reads in the selected period\.$/, (m) => `${m[1]} ${m[1] === '1' ? 'прочитане на клиничен запис' : 'прочитания на клинични записи'} в избрания период.`],
  [/^(\d+) sign-in events in the selected period, split into successful and failed\.$/, (m) => `Събития за вход в избрания период: ${m[1]} (успешни и неуспешни).`],
  [/^(\d+) appointments in the selected period, with completed, cancelled and no-show trends\.$/, (m) => `Срещи в избрания период: ${m[1]} (завършени, отменени и неявили се).`],
  [/^(\d+) prescriptions written in the selected period\.$/, (m) => `Изписани рецепти в избрания период: ${m[1]}.`],
  [/^(\d+) permission denials in the selected period\.$/, (m) => `Откази за достъп в избрания период: ${m[1]}.`],
  [/^(\d+) evaluable results$/, (m) => `${m[1]} ${m[1] === '1' ? 'оценяем резултат' : 'оценяеми резултата'}`],
  [/^(\d+) abnormal$/, (m) => `${m[1]} ${m[1] === '1' ? 'резултат извън нормата' : 'резултата извън нормата'}`],
  [/^(\d+) booked \/ (\d+) available minutes$/, (m) => `Заети / свободни минути: ${m[1]} / ${m[2]}.`],
  [/^(\d+) new messages? — Resume$/, (m) => `${m[1]} ${m[1] === '1' ? 'ново съобщение' : 'нови съобщения'} — продължи`],
  [/^(\d+) messages? in your inbox$/, (m) => `${m[1]} ${m[1] === '1' ? 'съобщение' : 'съобщения'} във входящата ви поща.`],
  [/^(\d+) matching patients?\. Use the up and down arrow keys to choose\.$/, (m) => `${m[1]} ${m[1] === '1' ? 'съвпадащ пациент' : 'съвпадащи пациенти'}. Използвайте стрелките нагоре и надолу, за да изберете.`],
  [/^(.+) can sign in again\.$/, (m) => `${m[1]} може отново да влезе.`],
  [/^Welcome back, (.+)$/, (m) => `Добре дошли отново, ${m[1]}`],
  [/^Signed in as (.+)$/, (m) => `Влезли сте като ${m[1]}`],
  [/^(.+) is deactivated and their sessions have ended\.$/, (m) => `${m[1]} е деактивиран и сесиите му са прекратени.`],
  [/^(.+) hidden\.$/, (m) => `${m[1]} скрит.`],
  [/^(.+) shown\.$/, (m) => `${m[1]} показан.`],
  [/^(.+) trend values$/, (m) => `Стойности на тенденцията за ${m[1]}.`],
  [/^Scheduled for (.+)$/, (m) => `Насрочено за ${m[1]}`],
  [/^Status: (.+) → (.+)$/, (m) => `Статус: ${statusLabel(m[1])} → ${statusLabel(m[2])}`],
  [/^Time: (.+) → (.+)$/, (m) => `Време: ${m[1]} → ${m[2]}`],
  [/^End: (.+) → (.+)$/, (m) => `Край: ${m[1]} → ${m[2]}`],
  [/^(.+) · (\d+) min$/, (m) => `${m[1]} · ${m[2]} мин`],
];

const STATUS_BG = {
  Scheduled: 'Планиран',
  Confirmed: 'Потвърден',
  Completed: 'Завършен',
  Cancelled: 'Отменен',
  'No show': 'Не се яви',
};

function statusLabel(value) {
  return STATUS_BG[value] || value;
}

function browserLanguage() {
  try {
    const raw =
      (typeof navigator !== 'undefined' && navigator.languages && navigator.languages[0]) ||
      (typeof navigator !== 'undefined' && navigator.language) ||
      'en';
    return String(raw).toLowerCase().startsWith('bg') ? 'bg' : 'en';
  } catch (error) {
    return 'en';
  }
}

function readStoredLanguage() {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return null;
    const saved = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return LANGUAGES.includes(saved) ? saved : null;
  } catch (error) {
    return null;
  }
}

function persistLanguage(value) {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, value);
    }
  } catch (error) {
    // Storage is optional; the current page still switches immediately.
  }
}

let language = readStoredLanguage() || browserLanguage();
let started = false;
let applying = false;

export function getLanguage() {
  return language;
}

/** Explicit translation for code paths that build strings before insertion. */
export function t(key, params) {
  const source = String(key == null ? '' : key);
  const template = language === 'bg' ? (BG[source] || source) : source;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, name) => (
    params[name] === undefined || params[name] === null ? '' : String(params[name])
  ));
}

function getText(node) {
  return node.nodeValue != null ? node.nodeValue : (node.data != null ? node.data : '');
}

function setText(node, value) {
  if (getText(node) === value) return;
  node.nodeValue = value;
  if (node.data != null) node.data = value;
}

function excluded(node) {
  let current = node.nodeType === 1 ? node : node.parent;
  while (current && current.nodeType === 1) {
    if (current.hasAttribute && current.hasAttribute('data-no-i18n')) return true;
    if (SKIP_TAGS.has(current.tagName)) return true;
    current = current.parent;
  }
  return false;
}

function attributesExcluded(element) {
  let current = element;
  while (current && current.nodeType === 1) {
    if (current.hasAttribute && current.hasAttribute('data-no-i18n')) return true;
    current = current.parent;
  }
  return false;
}

function translatedValue(source) {
  const trimmed = source.trim();
  if (!trimmed) return null;
  const normalized = trimmed.replace(/\s+/g, ' ');
  if (Object.prototype.hasOwnProperty.call(BG, normalized)) return BG[normalized];
  if (language !== 'bg') return null;
  for (const [pattern, build] of PATTERNS) {
    const match = normalized.match(pattern);
    if (match) return build(match);
  }
  return null;
}

function translateTextNode(node) {
  if (!node || node.nodeType !== 3) return;
  const current = getText(node);
  if (!current.trim()) return;
  if (excluded(node)) return;
  if (current === node.__i18nTranslated && language === 'bg') return;
  if (current !== node.__i18nTranslated && current !== node.__i18nSource) {
    node.__i18nSource = current;
  }
  const source = node.__i18nSource;
  const translated = language === 'bg' ? translatedValue(source) : null;
  if (translated !== null && translated !== undefined) {
    const leading = source.match(/^\s*/)[0];
    const trailing = source.match(/\s*$/)[0];
    const next = `${leading}${translated}${trailing}`;
    setText(node, next);
    node.__i18nTranslated = next;
  } else if (language === 'en' && node.__i18nTranslated !== undefined) {
    setText(node, source);
    node.__i18nTranslated = source;
  }
}

function translateElementAttributes(element) {
  if (!element || element.nodeType !== 1 || attributesExcluded(element)) return;
  if (!element.__i18nAttrs) element.__i18nAttrs = {};
  if (!element.__i18nTranslatedAttrs) element.__i18nTranslatedAttrs = {};
  for (const attr of ATTRS) {
    const current = element.getAttribute(attr);
    if (current === null || current === '') continue;
    if (current === element.__i18nTranslatedAttrs[attr] && language === 'bg') continue;
    if (current !== element.__i18nTranslatedAttrs[attr] && current !== element.__i18nAttrs[attr]) {
      element.__i18nAttrs[attr] = current;
    }
    const source = element.__i18nAttrs[attr];
    const translated = language === 'bg' ? translatedValue(source) : null;
    if (translated) {
      element.setAttribute(attr, translated);
      element.__i18nTranslatedAttrs[attr] = translated;
    } else if (language === 'en' && current !== source) {
      element.setAttribute(attr, source);
      element.__i18nTranslatedAttrs[attr] = source;
    }
  }
}

function walkNode(node) {
  if (!node) return;
  if (node.nodeType === 3) {
    translateTextNode(node);
    return;
  }
  if (node.nodeType !== 1) return;
  translateElementAttributes(node);
  const children = node.childNodes || node.children || [];
  for (const child of children) walkNode(child);
}

function translateDocumentTitle() {
  if (typeof document === 'undefined' || typeof document.title !== 'string') return;
  const current = document.title;
  if (!current) return;
  if (current !== document.__i18nTitleSource && current !== document.__i18nTitleTranslated) {
    document.__i18nTitleSource = current;
  }
  const source = document.__i18nTitleSource || current;
  const translated = language === 'bg' ? translatedValue(source) : null;
  const next = translated || source;
  if (document.title !== next) document.title = next;
  document.__i18nTitleTranslated = next;
}

export function applyLanguage(root = null) {
  if (typeof document === 'undefined' || !document.body) return;
  applying = true;
  try {
    if (!root || root === document.body) translateDocumentTitle();
    walkNode(root || document.body);
  } finally {
    applying = false;
  }
}

let applyScheduled = false;

/** Coalesce rapid switches into one DOM pass on the next animation frame. */
function scheduleApply() {
  if (typeof requestAnimationFrame !== 'function') {
    applyLanguage();
    return;
  }
  if (applyScheduled) return;
  applyScheduled = true;
  requestAnimationFrame(() => {
    applyScheduled = false;
    applyLanguage();
  });
}

function setAttrIfChanged(element, name, value) {
  if (element.getAttribute(name) !== value) element.setAttribute(name, value);
}

function updateSwitcher() {
  if (typeof document === 'undefined' || typeof document.querySelectorAll !== 'function') return;
  const host = document.querySelector('.language-switcher');
  if (host) setAttrIfChanged(host, 'aria-label', language === 'bg' ? 'Език' : 'Language');
  for (const button of document.querySelectorAll('.language-switcher__btn')) {
    const active = button.dataset.language === language;
    setAttrIfChanged(button, 'aria-pressed', active ? 'true' : 'false');
    button.classList.toggle('is-active', active);
    if (button.dataset.language === 'bg') {
      setAttrIfChanged(button, 'aria-label', language === 'bg' ? 'Български' : 'Bulgarian');
      setAttrIfChanged(button, 'title', language === 'bg' ? 'Български' : 'Bulgarian');
    } else {
      setAttrIfChanged(button, 'aria-label', language === 'bg' ? 'Английски' : 'English');
      setAttrIfChanged(button, 'title', language === 'bg' ? 'Английски' : 'English');
    }
  }
}

export function setLanguage(next) {
  if (!LANGUAGES.includes(next) || next === language) return;
  language = next;
  persistLanguage(language);
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('lang', language);
  }
  scheduleApply();
  updateSwitcher();
  if (typeof document !== 'undefined' && typeof document.dispatchEvent === 'function') {
    document.dispatchEvent(
      new CustomEvent('medicore:languagechange', { detail: { language } })
    );
  }
}

export function mountSwitcher(container = null) {
  if (typeof document === 'undefined') return null;
  const host = document.createElement('div');
  host.className = 'language-switcher';
  host.setAttribute('role', 'group');
  host.setAttribute('aria-label', language === 'bg' ? 'Език' : 'Language');
  for (const code of LANGUAGES) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'language-switcher__btn hit-slop';
    button.dataset.language = code;
    button.textContent = code.toUpperCase();
    button.setAttribute('aria-label', code === 'bg' ? 'Български' : 'English');
    button.setAttribute('title', code === 'bg' ? 'Български' : 'English');
    button.setAttribute('aria-pressed', code === language ? 'true' : 'false');
    button.classList.toggle('is-active', code === language);
    button.addEventListener('click', () => setLanguage(code));
    host.append(button);
  }
  if (container) container.append(host);
  return host;
}

function startObserver() {
  if (typeof MutationObserver !== 'function' || !document.body) return;
  const observer = new MutationObserver((mutations) => {
    if (applying) return;
    applying = true;
    try {
      for (const mutation of mutations) {
        if (mutation.type === 'childList') {
          for (const node of mutation.addedNodes) walkNode(node);
        } else if (mutation.type === 'characterData') {
          translateTextNode(mutation.target);
        } else if (mutation.type === 'attributes') {
          translateElementAttributes(mutation.target);
        }
      }
    } finally {
      applying = false;
    }
  });
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ATTRS,
  });
}

export function start() {
  if (started || typeof document === 'undefined' || !document.body) return;
  started = true;
  document.documentElement.setAttribute('lang', language);
  applyLanguage();
  startObserver();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading' && typeof document.addEventListener === 'function') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
}
