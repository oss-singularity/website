import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const script = readFileSync(new URL('../site/assets/scripts/theme-v1.js', import.meta.url), 'utf8');
const key = 'oss-singularity-theme';

// Exercise the public controller with unavailable browser storage and real
// preference transitions. Layout and keyboard activation need browser checks.
function page({ saved = null, accessError = false, readError = false, writeError = false, ready = 'loading', metas = true } = {}) {
  const reads = [], writes = [], changes = [], documentEvents = new Map(), windowEvents = new Map();
  const root = { dataset: { theme: 'bright' } };
  const buttons = Array.from({ length: 2 }, () => ({
    hidden: true, attributes: {}, events: new Map(),
    icon: { textContent: '', attributes: { 'aria-hidden': 'true' } },
    label: { textContent: '' },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, listener) { this.events.set(name, listener); },
    querySelector(selector) {
      if (selector === '[data-theme-icon]') return this.icon;
      if (selector === '[data-theme-label]') return this.label;
      throw new Error(`Unexpected button access: ${selector}`);
    },
  }));
  for (const button of buttons) {
    for (const element of [button, button.icon, button.label]) {
      Object.defineProperty(element, 'innerHTML', { set() { throw new Error('Unexpected HTML injection'); } });
      Object.defineProperty(element, 'style', { get() { throw new Error('Unexpected inline style access'); } });
    }
  }
  const themeColor = { setAttribute(name, value) { this[name] = value; } };
  const colorScheme = { setAttribute(name, value) { this[name] = value; } };
  const storage = {
    getItem(name) {
      reads.push(name);
      if (readError) throw new Error('Storage read blocked');
      return saved;
    },
    setItem(name, value) {
      writes.push([name, value]);
      if (writeError) throw new Error('Storage write blocked');
      saved = value;
    },
  };
  const document = {
    documentElement: root, readyState: ready,
    querySelectorAll(selector) {
      if (selector === 'button[data-theme-toggle]') return buttons;
      if (selector === 'meta[name="theme-color"]') return metas ? [themeColor] : [];
      if (selector === 'meta[name="color-scheme"]') return metas ? [colorScheme] : [];
      throw new Error(`Unexpected document access: ${selector}`);
    },
    addEventListener(name, listener) { documentEvents.set(name, listener); },
    dispatchEvent(event) {
      assert.equal(event.type, 'oss-theme-change');
      assert.deepEqual(Object.keys(event.detail), ['theme']);
      changes.push(event.detail.theme);
    },
  };
  const window = {
    get localStorage() {
      if (accessError) throw new Error('Storage access blocked');
      return storage;
    },
    addEventListener(name, listener) { windowEvents.set(name, listener); },
    matchMedia() { throw new Error('The default must not follow the operating system'); },
  };
  // These page concerns are deliberately inaccessible to the palette controller.
  for (const name of ['location', 'history', 'scrollX', 'scrollY', 'sessionStorage']) {
    Object.defineProperty(window, name, { get() { throw new Error(`Unexpected access: ${name}`); } });
  }
  Object.defineProperty(document, 'activeElement', { get() { throw new Error('Unexpected focus access'); } });
  class CustomEvent { constructor(type, { detail }) { this.type = type; this.detail = detail; } }
  vm.runInNewContext(script, { document, window, CustomEvent }, { filename: 'theme-v1.js' });
  return {
    root, buttons, themeColor, colorScheme, reads, writes, changes,
    ready() { documentEvents.get('DOMContentLoaded')?.(); },
    click(index = 0) { buttons[index].events.get('click')(); },
    store(value) { saved = value; },
    blockStorage({ access = false, read = false } = {}) { accessError = access; readError = read; },
    pageshow(persisted = false) { windowEvents.get('pageshow')({ persisted }); },
    external(value, eventKey = key, area = storage) {
      windowEvents.get('storage')({ key: eventKey, newValue: value, storageArea: area });
    },
  };
}

function assertAction(p, icon, label, accessibleName) {
  for (const button of p.buttons) {
    assert.equal(button.icon.textContent, icon);
    assert.equal(button.icon.attributes['aria-hidden'], 'true');
    assert.equal(button.label.textContent, label);
    assert.equal(button.attributes['aria-label'], accessibleName);
    assert.equal('aria-pressed' in button.attributes, false, 'A destination action has no toggle state');
  }
}

test('first visit synchronously selects dark and never writes a preference', () => {
  const p = page();
  assert.equal(p.root.dataset.theme, 'dark');
  assert.equal(p.themeColor.content, '#07111f');
  assert.equal(p.colorScheme.content, 'dark');
  assert.deepEqual(p.reads, [key]);
  assert.deepEqual(p.writes, []);
  assert.ok(p.buttons.every((button) => button.hidden));
  p.ready();
  assert.ok(p.buttons.every((button) => !button.hidden));
  assertAction(p, '☀', 'Bright mode', 'Switch to bright mode');
  assert.deepEqual(p.reads, [key]);
  assert.deepEqual(p.writes, []);
});

test('a saved bright preference applies before buttons are activated', () => {
  const p = page({ saved: 'bright' });
  assert.equal(p.root.dataset.theme, 'bright');
  assert.equal(p.themeColor.content, '#f4f7fb');
  assert.equal(p.colorScheme.content, 'light');
  assert.ok(p.buttons.every((button) => button.hidden));
  p.ready();
  assertAction(p, '☾', 'Dark mode', 'Switch to dark mode');
  assert.deepEqual(p.reads, [key]);
  assert.deepEqual(p.writes, []);
});

test('invalid saved preferences fall back to dark without repairing storage', () => {
  for (const saved of ['dark', '', 'light', 'system', 'BRIGHT', ' bright ', '{"theme":"bright"}']) {
    const p = page({ saved, ready: 'complete' });
    assert.equal(p.root.dataset.theme, 'dark', `Preference: ${saved}`);
    assertAction(p, '☀', 'Bright mode', 'Switch to bright mode');
    assert.deepEqual(p.writes, []);
  }
});

test('explicit toggles update every button, emit palette changes, and save only the theme', () => {
  const p = page({ ready: 'complete' });
  p.changes.length = 0;
  p.click();
  assert.equal(p.root.dataset.theme, 'bright');
  assertAction(p, '☾', 'Dark mode', 'Switch to dark mode');
  assert.equal(p.colorScheme.content, 'light');
  p.click(1);
  assert.equal(p.root.dataset.theme, 'dark');
  assertAction(p, '☀', 'Bright mode', 'Switch to bright mode');
  assert.deepEqual(p.changes, ['bright', 'dark']);
  assert.deepEqual(p.writes, [[key, 'bright'], [key, 'dark']]);
  assert.deepEqual(p.reads, [key]);
});

test('blocked storage access, reads or writes leave a working page-local toggle', () => {
  for (const failure of ['accessError', 'readError', 'writeError']) {
    const p = page({ [failure]: true, ready: 'complete', metas: false });
    assert.equal(p.root.dataset.theme, 'dark');
    p.click();
    assert.equal(p.root.dataset.theme, 'bright');
    assertAction(p, '☾', 'Dark mode', 'Switch to dark mode');
    p.external('dark');
    assert.equal(p.root.dataset.theme, failure === 'accessError' ? 'bright' : 'dark');
    p.click();
    assert.equal(p.root.dataset.theme, failure === 'accessError' ? 'dark' : 'bright');
  }
});

test('cross-tab updates affect only this local preference and never write back', () => {
  const p = page({ ready: 'complete' });
  p.changes.length = 0;
  p.external('bright', 'unrelated-private-key');
  p.external('bright', key, {});
  assert.equal(p.root.dataset.theme, 'dark');
  assertAction(p, '☀', 'Bright mode', 'Switch to bright mode');
  assert.deepEqual(p.changes, []);
  p.external('bright');
  assert.equal(p.root.dataset.theme, 'bright');
  assertAction(p, '☾', 'Dark mode', 'Switch to dark mode');
  p.external('bright');
  assert.deepEqual(p.changes, ['bright']);
  p.external('invalid');
  assert.equal(p.root.dataset.theme, 'dark');
  assertAction(p, '☀', 'Bright mode', 'Switch to bright mode');
  p.external('bright');
  p.external(null);
  assert.equal(p.root.dataset.theme, 'dark');
  assertAction(p, '☀', 'Bright mode', 'Switch to bright mode');
  p.external('bright');
  p.external(null, null);
  assert.equal(p.root.dataset.theme, 'dark');
  assertAction(p, '☀', 'Bright mode', 'Switch to bright mode');
  assert.deepEqual(p.writes, []);
  assert.deepEqual(p.reads, [key]);
});

test('back/forward restoration catches missed preferences without rewiring buttons or writing storage', () => {
  const p = page({ ready: 'complete' });
  const listeners = p.buttons.map((button) => button.events.get('click'));
  p.changes.length = 0;
  p.store('bright');
  p.pageshow();
  assert.equal(p.root.dataset.theme, 'dark', 'Ordinary pageshow does not reload preferences');
  assertAction(p, '☀', 'Bright mode', 'Switch to bright mode');
  assert.deepEqual(p.reads, [key]);
  assert.deepEqual(p.changes, []);
  p.pageshow(true);
  assert.equal(p.root.dataset.theme, 'bright');
  assert.equal(p.themeColor.content, '#f4f7fb');
  assert.equal(p.colorScheme.content, 'light');
  assertAction(p, '☾', 'Dark mode', 'Switch to dark mode');
  assert.deepEqual(p.reads, [key, key]);
  assert.deepEqual(p.changes, ['bright']);
  for (const value of [null, 'invalid']) {
    p.store(value);
    p.pageshow(true);
    assert.equal(p.root.dataset.theme, 'dark');
    assert.equal(p.themeColor.content, '#07111f');
    assert.equal(p.colorScheme.content, 'dark');
    assertAction(p, '☀', 'Bright mode', 'Switch to bright mode');
    p.store('bright');
    p.pageshow(true);
  }
  for (const failure of [{ access: true }, { read: true }]) {
    p.store('dark');
    p.blockStorage(failure);
    const changesBefore = p.changes.length;
    p.pageshow(true);
    assert.equal(p.root.dataset.theme, 'bright', 'Blocked access preserves the restored page theme');
    assert.equal(p.themeColor.content, '#f4f7fb');
    assert.equal(p.colorScheme.content, 'light');
    assertAction(p, '☾', 'Dark mode', 'Switch to dark mode');
    assert.equal(p.changes.length, changesBefore);
    p.blockStorage();
  }
  for (const [index, button] of p.buttons.entries()) {
    assert.equal(button.events.get('click'), listeners[index]);
    assert.equal(button.hidden, false);
  }
  assert.deepEqual(p.writes, []);
  p.click();
  assert.equal(p.root.dataset.theme, 'dark');
  assertAction(p, '☀', 'Bright mode', 'Switch to bright mode');
  assert.deepEqual(p.writes, [[key, 'dark']], 'The existing listener still handles one explicit toggle');
});
