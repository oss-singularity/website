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
    createElement: (tag) => new FakeNode(tag),
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
// One shared creation moment for every fixture project: separate Date.now()
// calls can straddle a millisecond under load, which would turn the shared
// project-start dot into two and make the joint-jump assertions racy.
const CREATED_AT = new Date(Date.now() - 3 * DAY).toISOString();
const detail = (id, { doneAt = [], completedAt = [] } = {}) => ({
  id, status: 'closed',
  created_at: CREATED_AT,
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
const deepText = (node) => [node.textContent, ...node.children.map(deepText)].filter(Boolean).join(' ').trim();
const descendants = (node, tag) => {
  const found = [];
  const walk = (parent) => { for (const child of parent.children) { if (child.tagName === tag.toUpperCase()) found.push(child); walk(child); } };
  walk(node);
  return found;
};
const eventsTable = (p) => {
  const details = p.content.children.find((child) => child.tagName === 'DETAILS');
  assert.ok(details, 'The journey carries a collapsible event table');
  return details;
};

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
  assert.equal(p.status.textContent, 'Every point is a public record — hover a dot or open the event table for its moment.');
  assert.deepEqual(p.requests.filter((url) => !url.includes('/projects?')).length, 2, 'One detail request per listed project');
});

test('every axis value and date label is real text, never an empty node', async () => {
  const a = 'proj-a';
  const data = fixture([a], { [a]: detail(a, { doneAt: [new Date(Date.now() - DAY).toISOString()] }) });
  const p = page({ list: data.list, details: data.details });
  await flush();
  const texts = descendants(p.chart.children[0], 'text');
  assert.equal(texts.length, 5, 'Three grid values and two date labels are set as SVG text');
  const labels = texts.map((node) => String(node.textContent));
  assert.deepEqual(labels.slice(0, 3), ['0', '1', '1'], 'The grid counts carry their values');
  assert.match(labels[3], /^[A-Z][a-z]{2} \d+$/, 'The start date is readable at the chart');
  assert.match(labels[4], /today$/, 'The end marker is readable at the chart');
});

test('event moments open through one collapsed table, with no trap and no forced stops', async () => {
  const earlier = new Date(Date.now() - 2 * DAY).toISOString();
  const shared = new Date(Date.now() - DAY).toISOString();
  const data = fixture(['proj-a', 'proj-b'], {
    'proj-a': detail('proj-a', { doneAt: [earlier], completedAt: [shared, shared] }),
    'proj-b': detail('proj-b', { doneAt: [shared] }),
  });
  const p = page({ list: data.list, details: data.details });
  await flush();
  const details = eventsTable(p);
  assert.match(deepText(details.children[0]), /Every point's moment/, 'The summary names what opening reveals');
  const rows = descendants(details, 'tbody')[0].children;
  assert.equal(rows.length, 4, 'One row per drawn point: the joint projects jump, two milestones, the joint commitments jump');
  for (const dot of withClass(p.chart.children[0], 'road-dot')) {
    assert.equal('tabindex' in dot.attributes, false, 'Dots stay unfocusable — the table is the keyboard path, not hundreds of stops');
  }
  const text = rows.map(deepText).join('\n');
  assert.match(text, /Projects coordinated\s*2/, 'The joint projects jump names its cumulative count');
  assert.match(text, /Commitments accepted\s*2/, 'The joint commitments jump names its cumulative count');
  assert.match(text, /Milestones completed\s*1/, 'Milestone moments carry their counts');
  const header = deepText(descendants(details, 'thead')[0]);
  assert.match(header, /Moment/);
  assert.match(header, /Record/);
  assert.match(header, /Cumulative count/);
  const moments = rows.map((row) => deepText(row.children[0]));
  const fmtMoment = (value) => new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(new Date(value));
  const created = data.details['proj-a'].created_at;
  assert.deepEqual(moments, [fmtMoment(created), fmtMoment(earlier), fmtMoment(shared), fmtMoment(shared)], 'Rows run in record order, oldest first');
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
  assert.equal(descendants(eventsTable(p), 'tbody')[0].children.length, 62, 'The event table lists every moment too — 61 commitments plus the project start');
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

test('render anchors stay in a local copy, never in the shared reader snapshot', async () => {
  const a = 'solo';
  const data = fixture([a], { [a]: detail(a, {}) });
  const p = page({ list: data.list, details: data.details });
  // Join before the read resolves, like an earlier panel on the page would,
  // and again after the draw, like any subscriber arriving later.
  let early = null;
  p.window.OssGrowthData.subscribe((snapshot) => { if (snapshot.ok) early = snapshot; });
  await flush();
  assert.equal(p.content.hidden, false, 'The empty-series draw still renders');
  let late = null;
  p.window.OssGrowthData.subscribe((snapshot) => { if (snapshot.ok) late = snapshot; });
  assert.ok(late, 'The late subscriber receives the cached snapshot');
  assert.equal(late, early, 'Both subscribers see the one shared snapshot object');
  assert.equal(late.series.milestones.length, 0, 'Milestones stay empty in the snapshot — the baseline anchor lives only in the draw');
  assert.equal(late.series.commitments.length, 0, 'Commitments stay empty in the snapshot — the baseline anchor lives only in the draw');
  assert.equal(late.series.projects.length, 1, 'The project series keeps exactly its record');
  for (const key of ['milestones', 'commitments', 'projects']) {
    for (const point of late.series[key]) assert.equal(point.anchor, undefined, `No render flag leaks into the shared ${key} series`);
  }
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
