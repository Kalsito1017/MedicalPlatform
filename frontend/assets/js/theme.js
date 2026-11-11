// ---------------------------------------------------------------------------
// theme.js — Light / Dark / System theme control.
//
// `data-theme="light|dark"` on <html> selects the token set; the classic
// theme-init.js sets it before first paint. This module keeps the choice in
// localStorage (`medicore-theme`), listens to the OS preference while in
// System mode, and mounts the accessible three-button switcher.
// ---------------------------------------------------------------------------

export const THEME_STORAGE_KEY = 'medicore-theme';
export const THEME_MODES = ['light', 'dark', 'system'];

const mediaQuery =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null;

function readStoredMode() {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return 'system';
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
    return THEME_MODES.includes(saved) ? saved : 'system';
  } catch (error) {
    return 'system';
  }
}

let mode = readStoredMode();

function resolvedTheme(value = mode) {
  if (value !== 'system') return value;
  return mediaQuery && mediaQuery.matches === true ? 'dark' : 'light';
}

function persist(value) {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(THEME_STORAGE_KEY, value);
    }
  } catch (error) {
    // Storage is optional; the current page still switches immediately.
  }
}

function updateButtons() {
  if (typeof document === 'undefined') return;
  for (const button of document.querySelectorAll('.theme-switcher__btn')) {
    const active = button.dataset.themeMode === mode;
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    button.classList.toggle('is-active', active);
  }
}

function apply() {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.setAttribute('data-theme', resolvedTheme());
  root.setAttribute('data-theme-mode', mode);
  updateButtons();
}

export function getTheme() {
  return { mode, theme: resolvedTheme() };
}

export function setTheme(next) {
  if (!THEME_MODES.includes(next)) return;
  mode = next;
  persist(mode);
  apply();
  if (typeof document !== 'undefined' && typeof document.dispatchEvent === 'function') {
    document.dispatchEvent(
      new CustomEvent('medicore:themechange', {
        detail: { mode, theme: resolvedTheme() },
      })
    );
  }
}

function onSystemChange() {
  if (mode === 'system') apply();
}

if (mediaQuery) {
  if (typeof mediaQuery.addEventListener === 'function') {
    mediaQuery.addEventListener('change', onSystemChange);
  } else if (typeof mediaQuery.addListener === 'function') {
    mediaQuery.addListener(onSystemChange);
  }
}

function iconNode(name) {
  const namespace = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(namespace, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');

  const add = (tag, attributes) => {
    const node = document.createElementNS(namespace, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    svg.append(node);
  };

  if (name === 'light') {
    add('circle', { cx: '12', cy: '12', r: '4' });
    add('path', { d: 'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4' });
  } else if (name === 'dark') {
    add('path', { d: 'M20.5 14.6A8.5 8.5 0 0 1 9.4 3.5 8.5 8.5 0 1 0 20.5 14.6Z' });
  } else {
    add('rect', { x: '3', y: '4', width: '18', height: '14', rx: '2' });
    add('path', { d: 'M8 21h8M12 18v3' });
  }
  return svg;
}

const LABELS = {
  light: { label: 'Light', title: 'Light theme' },
  dark: { label: 'Dark', title: 'Dark theme' },
  system: { label: 'System', title: 'System theme (follows OS)' },
};

export function mountSwitcher(container = null) {
  if (typeof document === 'undefined') return null;
  const host = document.createElement('div');
  host.className = 'theme-switcher';
  host.setAttribute('role', 'group');
  host.setAttribute('aria-label', 'Color theme');

  for (const value of THEME_MODES) {
    const config = LABELS[value];
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'theme-switcher__btn hit-slop';
    button.dataset.themeMode = value;
    button.setAttribute('aria-label', config.label);
    button.setAttribute('title', config.title);
    button.setAttribute('aria-pressed', value === mode ? 'true' : 'false');
    button.append(iconNode(value));
    button.addEventListener('click', () => setTheme(value));
    host.append(button);
  }

  if (container) container.append(host);
  return host;
}

export function initThemeSwitcher(root = document) {
  if (typeof document === 'undefined' || !root) return;
  const host = root.getElementById ? root.getElementById('theme-switcher') : null;
  if (host && !host.dataset.themeMounted) {
    host.dataset.themeMounted = 'true';
    mountSwitcher(host);
    updateButtons();
  }
}

apply();
