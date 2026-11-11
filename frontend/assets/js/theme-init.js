// ---------------------------------------------------------------------------
// theme-init.js — classic, render-blocking pre-paint theme bootstrap.
//
// Loaded synchronously in <head> BEFORE the stylesheet. It reads the saved
// preference and sets data-theme/data-theme-mode on <html>, so the first paint
// already uses the chosen light/dark palette (no flash). It intentionally does
// not use modules, imports, or inline styles.
// ---------------------------------------------------------------------------
(function () {
  var KEY = 'medicore-theme';
  var mode = 'system';
  try {
    var saved = window.localStorage.getItem(KEY);
    if (saved === 'light' || saved === 'dark' || saved === 'system') mode = saved;
  } catch (error) {
    // Storage can be blocked (private mode/privacy settings): fall back to system.
  }

  var prefersDark = false;
  try {
    prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches === true;
  } catch (error) {
    prefersDark = false;
  }

  var resolved = mode === 'system' ? (prefersDark ? 'dark' : 'light') : mode;
  var root = document.documentElement;
  root.setAttribute('data-theme', resolved);
  root.setAttribute('data-theme-mode', mode);
})();
