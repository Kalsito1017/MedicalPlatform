/* ---------------------------------------------------------------------------
 * frontend/tests/domshim.mjs — minimal DOM shim used by render-smoke.mjs.
 * Enough surface for the frontend page modules to run in Node against a
 * stubbed fetch. Not a real browser: it verifies that render paths execute
 * without throwing and produce expected text, nothing more.
 * ------------------------------------------------------------------------- */

class ShimText {
  constructor(value) {
    this.nodeType = 3;
    this.data = String(value);
    this.parent = null;
  }
  get textContent() {
    return this.data;
  }
}

class ShimNode {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parent = null;
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this.disabled = false;
    this.hidden = false;
    this.value = '';
    this.open = false;
    this.returnValue = '';
    this._innerHTML = '';
    this._classSet = new Set();
    // Minimal CSSStyleDeclaration stand-in that RECORDS custom-property writes
    // (the app writes exactly one: --sheet-dy on sheet elements, sheet.js).
    this.style = {
      values: {},
      setProperty(name, value) {
        this.values[name] = String(value);
      },
      getPropertyValue(name) {
        return this.values[name] === undefined ? '' : this.values[name];
      },
    };
    const self = this;
    this.classList = {
      add: (...c) => c.forEach((x) => self._classSet.add(x)),
      remove: (...c) => c.forEach((x) => self._classSet.delete(x)),
      toggle: (c, force) => {
        const on = force === undefined ? !self._classSet.has(c) : force;
        if (on) self._classSet.add(c);
        else self._classSet.delete(c);
        return on;
      },
      contains: (c) => self._classSet.has(c),
    };
  }

  get id() {
    return this.attributes.id || '';
  }
  set id(v) {
    this.attributes.id = String(v);
  }
  get className() {
    return [...this._classSet].join(' ');
  }
  set className(v) {
    this._classSet = new Set(String(v).split(/\s+/).filter(Boolean));
    this.attributes.class = String(v);
  }
  get innerHTML() {
    return this._innerHTML;
  }
  set innerHTML(v) {
    this._innerHTML = String(v);
    this.children = [];
    if (String(v).trim() !== '') this.children.push(new ShimText(''));
  }
  get textContent() {
    return this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) {
    this.children = [];
    this.append(new ShimText(v));
  }
  get firstChild() {
    return this.children[0] || null;
  }
  get childNodes() {
    return this.children;
  }

  append(...nodes) {
    for (const node of nodes) {
      const n = typeof node === 'string' ? new ShimText(node) : node;
      n.parent = this;
      this.children.push(n);
    }
  }
  appendChild(node) {
    this.append(node);
    return node;
  }
  prepend(...nodes) {
    const mapped = nodes.map((n) => (typeof n === 'string' ? new ShimText(n) : n));
    mapped.forEach((n) => (n.parent = this));
    this.children.unshift(...mapped);
  }
  removeChild(node) {
    const index = this.children.indexOf(node);
    if (index >= 0) this.children.splice(index, 1);
    return node;
  }

  setAttribute(key, value) {
    const v = value === true ? '' : String(value);
    this.attributes[key] = v;
    if (key === 'id') this.attributes.id = v;
    if (key === 'class') this.className = v;
    if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (m, c) => c.toUpperCase())] = v;
  }
  getAttribute(key) {
    return key in this.attributes ? this.attributes[key] : null;
  }
  removeAttribute(key) {
    delete this.attributes[key];
  }
  hasAttribute(key) {
    return key in this.attributes;
  }

  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }
  removeEventListener(type, handler) {
    const list = this.listeners[type] || [];
    const index = list.indexOf(handler);
    if (index >= 0) list.splice(index, 1);
  }
  async dispatch(type, event = {}) {
    event.preventDefault = event.preventDefault || (() => {});
    for (const handler of [...(this.listeners[type] || [])]) await handler(event);
  }
  focus() {
    ShimDocument.activeElement = this;
  }

  /* <dialog> semantics: modal open/close plus the close event. */
  showModal() {
    this.open = true;
    this.setAttribute('open', '');
  }
  close(value) {
    if (!this.open) return;
    this.open = false;
    this.removeAttribute('open');
    if (value !== undefined) this.returnValue = String(value);
    this.dispatch('close');
  }

  matches(selector) {
    if (selector.startsWith('.')) return this._classSet.has(selector.slice(1));
    if (selector.startsWith('#')) return this.id === selector.slice(1);
    if (selector.startsWith('[')) {
      const name = selector.slice(1, -1).split('=')[0];
      return name in this.attributes;
    }
    return this.tagName === selector.toUpperCase();
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  querySelectorAll(selector) {
    const results = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (child.nodeType === 1) {
          if (child.matches(selector)) results.push(child);
          walk(child);
        }
      }
    };
    walk(this);
    return results;
  }
}

export class ShimDocument {
  static activeElement = null;
  constructor() {
    this.readyState = 'complete';
    this.body = new ShimNode('body');
    this.documentElement = new ShimNode('html');
    this.listeners = {};
  }
  get activeElement() {
    return ShimDocument.activeElement;
  }
  createElement(tag) {
    return new ShimNode(tag);
  }
  createTextNode(value) {
    return new ShimText(value);
  }
  getElementById(id) {
    return this.body.querySelector(`#${id}`);
  }
  querySelector(selector) {
    return this.body.querySelector(selector);
  }
  querySelectorAll(selector) {
    return this.body.querySelectorAll(selector);
  }
  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }
  removeEventListener(type, handler) {
    const list = this.listeners[type] || [];
    const index = list.indexOf(handler);
    if (index >= 0) list.splice(index, 1);
  }
  /* Dispatch to document-level listeners (e.g. the ⌘K shortcut handler). */
  async dispatch(type, event = {}) {
    event.preventDefault = event.preventDefault || (() => {});
    for (const handler of [...(this.listeners[type] || [])]) await handler(event);
  }
  /* View Transitions stub: runs the DOM swap synchronously (exactly what the
     real API expects from its update callback) and mimics the ViewTransition
     return shape. Tests remove/replace it to exercise the ui.js fallback. */
  startViewTransition(update) {
    const result = typeof update === 'function' ? update() : undefined;
    return {
      updateCallbackDone: Promise.resolve(result),
      ready: Promise.resolve(),
      finished: Promise.resolve(result),
      skipTransition() {},
    };
  }
}

/* Window-level event registry (window === globalThis in the shim), so
   overlay-history.js can bind `popstate` and tests can simulate Back. */
class ShimWindowEvents {
  constructor() {
    this.listeners = {};
  }
  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }
  removeEventListener(type, handler) {
    const list = this.listeners[type] || [];
    const index = list.indexOf(handler);
    if (index >= 0) list.splice(index, 1);
  }
  dispatch(type, event = {}) {
    event.preventDefault = event.preventDefault || (() => {});
    for (const handler of [...(this.listeners[type] || [])]) handler(event);
  }
}

/** Dispatch a window-level event (e.g. dispatchWindowEvent('popstate')). */
export function dispatchWindowEvent(type, event = {}) {
  const registry = globalThis.__windowEvents;
  if (registry) registry.dispatch(type, event);
}

export function installDom() {
  const document = new ShimDocument();
  ShimDocument.activeElement = null;
  globalThis.document = document;
  globalThis.window = globalThis;
  globalThis.location = {
    pathname: '/page.html',
    search: '',
    href: 'http://localhost/page.html',
    replace() {},
    assign() {},
    reload() {},
  };
  // Session history with working pushState/back + popstate dispatch: Back is
  // synchronous here (the real one is async), which keeps the LIFO overlay
  // unwinding tests deterministic.
  const historyEntries = [{ state: null }];
  globalThis.history = {
    state: null,
    replaceState(state) {
      historyEntries[historyEntries.length - 1] = { state };
      this.state = state;
    },
    pushState(state) {
      historyEntries.push({ state });
      this.state = state;
    },
    back() {
      if (historyEntries.length <= 1) return;
      historyEntries.pop();
      this.state = historyEntries[historyEntries.length - 1].state;
      dispatchWindowEvent('popstate', { state: this.state });
    },
    get length() {
      return historyEntries.length;
    },
  };
  const windowEvents = new ShimWindowEvents();
  globalThis.__windowEvents = windowEvents;
  globalThis.addEventListener = windowEvents.addEventListener.bind(windowEvents);
  globalThis.removeEventListener = windowEvents.removeEventListener.bind(windowEvents);
  // Pointer events exist (sheet.js uses the pointer path); tests that need the
  // touch fallback can delete globalThis.PointerEvent before installDom.
  globalThis.PointerEvent = class PointerEvent {};
  // Reduced-motion opt-out is off by default; tests replace matchMedia to
  // simulate prefers-reduced-motion: reduce.
  globalThis.matchMedia = (query) => ({
    matches: false,
    media: String(query),
    addEventListener() {},
    removeEventListener() {},
  });
  return document;
}

/** Register a page fixture: elements by id (+ optional attribute buttons).
 * Entries are id strings or specs: { id, tag, class, text, attrs, children }. */
export function fixture(document, ids, { aboutButtons = 1 } = {}) {
  const build = (entry) => {
    const spec = typeof entry === 'string' ? { id: entry } : entry || {};
    const node = new ShimNode(spec.tag || 'div');
    if (spec.id) node.setAttribute('id', spec.id);
    if (spec.class) node.setAttribute('class', spec.class);
    for (const [key, value] of Object.entries(spec.attrs || {})) {
      node.setAttribute(key, value);
    }
    if (spec.text != null) node.textContent = spec.text;
    for (const child of spec.children || []) node.append(build(child));
    return node;
  };
  for (const entry of ids) document.body.append(build(entry));
  for (let i = 0; i < aboutButtons; i += 1) {
    const button = new ShimNode('button');
    button.setAttribute('data-open-about', '');
    document.body.append(button);
  }
  return document;
}

export function textOf(node) {
  return node ? node.textContent : '';
}

export function settle(ms = 15) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export { ShimNode, ShimText };
