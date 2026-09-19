import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const sources = ['commons-growth-data-v1.js', 'road-so-far-v1.js']
  .map((file) => [file, readFileSync(new URL(`../site/assets/scripts/${file}`, import.meta.url), 'utf8')]);

const HOUR = 3600000, DAY = 86400000;

class FakeNode {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.style = {};
    this.classes = new Set();
    this.classList = { add: (name) => this.classes.add(name) };
    this.textContent;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  querySelectorAll(selector) {
    assert.match(selector, /^\.[-a-z]+$/, 'The chart queries class-only selectors');
    const wanted = selector.slice(1);
    const found = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (classesOf(child).has(wanted)) found.push(child);
        walk(child);
      }
    };
    walk(this);
    return found;
  }
}

// Exercise the journey chart with fixture API payloads. The shared growth
// reader joins the sandbox, so the chart consumes the same snapshot the
// mission curve would. The sandbox has no IntersectionObserver, so the
// journey loads immediately; reduced motion is the default so the drawn
// chart is in its final state for assertions.
function page({ list, details, failList = false, failDetailId = null, reduced = true, routes = null } = {}) {
  const nodes = new Map();
  for (const id of ['road-so-far', 'road-status', 'road-content', 'road-chart', 'road-summary']) nodes.set(id, new FakeNode('div'));
  nodes.get('road-content').hidden = true;
  const document = {
    getElementById: (id) => { assert.ok(nodes.has(id), `Unexpected element access: ${id}`); return nodes.get(id); },
    createElementNS: (ns, tag) => { assert.equal(ns, 'http://www.w3.org/2000/svg'); return new FakeNode(tag); },
  };
  const requests = [];
  const fetch = async (url) => {
    requests.push(url);
    if (routes) return routes(url, requests);
    if (failList && url.includes('/projects?')) return { ok: false, json: async () => ({}) };
    if (url.includes('/projects?')) return { ok: true, json: async () => list };
    const id = decodeURIComponent(url.split('/projects/')[1]);
    if (failDetailId === id) return { ok: false, json: async () => ({}) };
    const detail = details[id];
    if (!detail) throw new Error(`Unexpected fetch: ${url}`);
    return { ok: true, json: async () => detail };
  };
  const window = { matchMedia: () => ({ matches: reduced }) };
  const sandbox = { document, window, fetch, requests, nodes, URLSearchParams, AbortController,
    setTimeout, clearTimeout, addEventListener: () => {}, requestAnimationFrame: (fn) => fn() };
  for (const [filename, source] of sources) vm.runInNewContext(source, sandbox, { filename });
  return {
    nodes, requests, window,
    status: nodes.get('road-status'), content: nodes.get('road-content'),
    chart: nodes.get('road-chart'), summary: nodes.get('road-summary'),
  };
}

const flush = async () => { for (let count = 0; count < 120; count += 1) await Promise.resolve(); };

const classesOf = (node) => new Set([
  ...node.classes,
  ...String(node.attributes.class || '').split(/\s+/).filter(Boolean),
]);

const project = (id, createdHoursAgo, { milestones = [], commitments = [] } = {}) => ({
  id, mission_id: 'build-the-commons', title: `Journey ${id}`, purpose: 'A bounded inspectable step.',
  status: 'closed', version: 1, created_at: new Date(Date.now() - createdHoursAgo * HOUR).toISOString(),
  updated_at: new Date(Date.now() - createdHoursAgo * HOUR).toISOString(), milestones, commitments,
});
const detail = (id, { doneAt = [], completedAt = [] } = {}) => ({
  id, status: 'closed',
  created_at: new Date(Date.now() - 3 * DAY).toISOString(),
  updated_at: new Date().toISOString(),
  milestones: doneAt.map((at, index) => ({ id: `m-${id}-${index}`, status: 'done', updated_at: at })),
  commitments: completedAt.map((at, index) => ({ id: `c-${id}-${index}`, status: 'completed', updated_at: at })),
});
const fixture = (ids, details) => ({
  list: { items: ids.map((id) => ({ id, mission_id: 'build-the-commons', status: 'closed', created_at: details[id].created_at })), next_cursor: null },
  details,
});

const countTags = (node, tag) => node.children.filter((child) => child.tagName === tag.toUpperCase()).length;
const withClass = (node, cls) => node.children.filter((child) => classesOf(child).has(cls));

test('the journey draws three cumulative series from public records', async () => {
  const a = 'proj-a', b = 'proj-b';
  const data = fixture([a, b], {
    [a]: detail(a, { doneAt: [new Date(Date.now() - 2 * DAY).toISOString(), new Date(Date.now() - DAY).toISOString()], completedAt: [new Date(Date.now() - 2 * DAY).toISOString()] }),
    [b]: detail(b, { doneAt: [new Date(Date.now() - HOUR).toISOString()], completedAt: [new Date(Date.now() - 2 * HOUR).toISOString()] }),
  });
  const p = page({ list: data.list, details: data.details });
  await flush();
  assert.equal(p.content.hidden, false);
  assert.equal(p.chart.children.length, 1, 'The chart renders exactly one svg root');
  const svg = p.chart.children[0];
  assert.equal(svg.tagName, 'SVG');
  assert.equal(withClass(svg, 'road-area').length, 3, 'One area per series');
  const lines = withClass(svg, 'road-line');
  assert.equal(lines.length, 3, 'One line per series');
  for (const line of lines) {
    assert.match(line.attributes.d, /^M-?[\d.]+ -?[\d.]+ C/, 'Lines are smooth monotone curves');
    assert.doesNotMatch(line.attributes.d, /NaN|Infinity/, 'No interpolation breakdown');
  }
  const dots = withClass(svg, 'road-dot');
  assert.equal(dots.length, 6, 'One dot per distinct moment across all three series — the two projects share one created moment, milestones keep three, commitments two');
  for (const dot of dots) assert.equal(dot.children.length, 1, 'Each dot carries a tooltip');
  assert.match(p.summary.textContent, /2 coordinated projects · 3 milestones completed · 2 commitments accepted — public records, across the first \d+ days\./);
  assert.equal(p.status.textContent, 'Every point is a public record — hover a dot for its moment.');
  assert.deepEqual(p.requests.filter((url) => !url.includes('/projects?')).length, 2, 'One detail request per listed project');
});

test('simultaneous completions stay one honest joint jump, never a NaN path', async () => {
  const shared = new Date(Date.now() - DAY).toISOString();
  const data = fixture(['proj-a'], {
    'proj-a': detail('proj-a', { doneAt: [shared], completedAt: [shared, shared] }),
  });
  const p = page({ list: data.list, details: data.details });
  await flush();
  assert.equal(p.content.hidden, false);
  const svg = p.chart.children[0];
  for (const line of [...withClass(svg, 'road-line'), ...withClass(svg, 'road-area')]) {
    assert.doesNotMatch(line.attributes.d, /NaN|Infinity/, 'Equal moments produce finite paths');
    assert.match(line.attributes.d, /^M-?[\d.]+ -?[\d.]+( C|$)/, 'The path still serializes');
  }
  assert.equal(withClass(svg, 'road-dot-commitments').length, 1, 'Two same-moment completions share one dot');
  const tip = withClass(svg, 'road-dot-commitments')[0].children[0].textContent;
  assert.match(tip, /Commitments accepted 2/, 'The shared dot carries the joint cumulative count');
  assert.match(p.summary.textContent, /1 coordinated projects? · 1 milestones? completed · 2 commitments? accepted/);
});

test('a project with 61 terminal commitments still draws the full record', async () => {
  const stamps = Array.from({ length: 61 }, (_, index) => new Date(Date.now() - 2 * DAY + index * 60000).toISOString());
  const data = fixture(['proj-a'], { 'proj-a': detail('proj-a', { completedAt: stamps }) });
  const p = page({ list: data.list, details: data.details });
  await flush();
  assert.equal(p.content.hidden, false, 'The invented 30/60 history caps are gone');
  assert.match(p.summary.textContent, /61 commitments accepted/);
  assert.equal(withClass(p.chart.children[0], 'road-dot-commitments').length, 61);
});

test('cursor pages are followed so 51 projects draw as 51, not a silent excerpt', async () => {
  const projects = Array.from({ length: 51 }, (_, index) => project(`p-${index + 1}`, (index + 2) * HOUR));
  const details = Object.fromEntries(projects.map(({ id }) => [id, detail(id)]));
  const p = page({ routes: (url, requests) => {
    if (!url.includes('/projects?')) {
      const id = decodeURIComponent(url.split('/projects/')[1]);
      return { ok: true, json: async () => details[id] };
    }
    const cursor = new URL(url, 'https://fixture.local').searchParams.get('cursor');
    const offset = cursor ? 50 : 0;
    const slice = projects.slice(offset, offset + 50);
    const last = slice.at(-1);
    return { ok: true, json: async () => ({ items: slice.map(({ milestones, commitments, ...item }) => item), next_cursor: offset === 0 ? `${Date.parse(last.created_at)}:${last.id}` : null }) };
  } });
  await flush();
  assert.equal(p.content.hidden, false);
  assert.match(p.summary.textContent, /51 coordinated projects/);
  const cursored = p.requests.filter((url) => url.includes('cursor='));
  assert.equal(cursored.length, 1, 'The waiting next_cursor page is read exactly once');
});

test('a truncated window says so instead of passing an excerpt off as the whole record', async () => {
  const projects = Array.from({ length: 320 }, (_, index) => project(`p-${index + 1}`, (index + 2) * HOUR));
  const details = Object.fromEntries(projects.map(({ id }) => [id, detail(id)]));
  const p = page({ routes: (url) => {
    if (!url.includes('/projects?')) {
      const id = decodeURIComponent(url.split('/projects/')[1]);
      return { ok: true, json: async () => details[id] };
    }
    const cursor = new URL(url, 'https://fixture.local').searchParams.get('cursor');
    const offset = cursor ? Number(cursor.split(':')[1].slice(2)) : 0;
    const slice = projects.slice(offset, offset + 100);
    const last = slice.at(-1);
    return { ok: true, json: async () => ({ items: slice.map(({ milestones, commitments, ...item }) => item), next_cursor: `${Date.parse(last.created_at)}:${last.id}` }) };
  } });
  await flush();
  assert.equal(p.content.hidden, false);
  assert.match(p.summary.textContent, /More than 300 coordinated projects/);
  assert.match(p.summary.textContent, /at least \d+ milestones completed · at least \d+ commitments accepted/);
  assert.doesNotMatch(p.summary.textContent, /— public records/, 'The truncated window never claims the complete record');
});

test('a refresh renews the journey and heals a failed first read', async () => {
  let healthy = false;
  const first = fixture(['proj-a'], { 'proj-a': detail('proj-a') });
  const grown = fixture(['proj-a', 'proj-b'], { 'proj-a': detail('proj-a'), 'proj-b': detail('proj-b') });
  let current = first;
  const p = page({ routes: (url) => {
    if (!url.includes('/projects?')) {
      const id = decodeURIComponent(url.split('/projects/')[1]);
      return { ok: true, json: async () => current.details[id] };
    }
    if (!healthy) return { ok: false, json: async () => ({}) };
    return { ok: true, json: async () => current.list };
  } });
  await flush();
  assert.equal(p.content.hidden, true, 'The failed first read keeps the chart hidden');
  assert.match(p.status.textContent, /could not be loaded/);
  healthy = true;
  current = grown;
  p.window.OssGrowthData.refresh();
  await flush();
  assert.equal(p.content.hidden, false, 'Refresh heals the transient failure');
  assert.match(p.summary.textContent, /2 coordinated projects/);
  assert.equal(p.requests.filter((url) => url.includes('/projects?')).length, 2, 'The chain re-read the record');
});

test('series without events stay on the baseline without fake dots', async () => {
  const a = 'solo';
  const data = fixture([a], { [a]: detail(a, {}) });
  const p = page({ list: data.list, details: data.details });
  await flush();
  const svg = p.chart.children[0];
  assert.equal(withClass(svg, 'road-dot').length, 1, 'Only the started project marks a point');
  const milestonesLine = withClass(svg, 'road-line').find((line) => classesOf(line).has('road-line-milestones'));
  assert.match(milestonesLine.attributes.d, /M-?[\d.]+ 230/, 'Empty series hug the baseline');
  assert.match(p.summary.textContent, /1 coordinated projects? · 0 milestones completed · 0 commitments accepted/);
});

test('hostile or unexpected payloads fail closed to the honest fallback', async () => {
  const good = fixture(['proj-a'], { 'proj-a': detail('proj-a', { doneAt: [new Date().toISOString()] }) });
  const cases = [
    ['list transport failure', { failList: true }],
    ['detail transport failure', { ...good, failDetailId: 'proj-a' }],
    ['list with a hostile status', (() => {
      const data = fixture(['proj-a'], { 'proj-a': detail('proj-a') });
      data.list.items[0].status = 'published';
      return { list: data.list, details: data.details };
    })()],
    ['detail with a hostile milestone status', (() => {
      const data = fixture(['proj-a'], { 'proj-a': detail('proj-a', { doneAt: [new Date().toISOString()] }) });
      data.details['proj-a'].milestones[0].status = 'published';
      return { list: data.list, details: data.details };
    })()],
    ['detail id mismatch', (() => {
      const data = fixture(['proj-a'], { 'proj-a': detail('proj-a') });
      data.details['proj-a'].id = 'proj-b';
      return { list: data.list, details: data.details };
    })()],
    ['oversized page beyond the API limit', (() => {
      const data = fixture(['proj-a'], { 'proj-a': detail('proj-a') });
      data.list.items = Array.from({ length: 101 }, () => data.list.items[0]);
      return { list: data.list, details: data.details };
    })()],
  ];
  for (const [name, setup] of cases) {
    const p = page(setup);
    await flush();
    assert.equal(p.content.hidden, true, `${name}: the chart stays hidden`);
    assert.match(p.status.textContent, /could not be loaded/, `${name}: the fallback is honest`);
  }
});

test('an empty commons shows no chart and points to the roadmap', async () => {
  const p = page({ list: { items: [], next_cursor: null }, details: {} });
  await flush();
  assert.equal(p.content.hidden, true);
  assert.match(p.status.textContent, /The curve begins with the first coordinated project/);
  assert.doesNotMatch(p.status.textContent, /could not be loaded/, 'An empty commons is not an error');
});
