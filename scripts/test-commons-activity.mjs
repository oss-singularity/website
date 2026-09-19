import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const sources = ['commons-growth-data-v1.js', 'commons-activity-v1.js']
  .map((file) => [file, readFileSync(new URL(`../site/assets/scripts/${file}`, import.meta.url), 'utf8')]);

const DAY = 86400000;

class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.events = new Map(); this.attributes = {}; this.className = ''; this.hidden = false; this.disabled = false; this._text = ''; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(type, listener) { this.events.set(type, listener); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(''); }
}

// Exercise the shared activity panel — the seven-day window and the
// mission's curve — with fixture API payloads, mirroring the browser audit
// cases: simultaneous completions, refresh, transient detail failures and
// the bounded record window.
function panel(options = {}) {
  const nodes = new Map();
  for (const id of ['commons-activity', 'activity-status', 'activity-content', 'activity-refresh', 'activity-totals',
    'activity-editorial', 'activity-coordination', 'activity-chart', 'activity-days', 'activity-charts',
    'activity-details', 'activity-summary', 'activity-window', 'activity-growth', 'activity-growth-chart',
    'activity-growth-window', 'activity-growth-summary']) nodes.set(id, new Element('div'));
  nodes.get('activity-content').hidden = true;
  nodes.get('activity-charts').hidden = true;
  nodes.get('activity-growth').hidden = true;
  const document = {
    getElementById: (id) => { assert.ok(nodes.has(id), `Unexpected element access: ${id}`); return nodes.get(id); },
    createElement: (tag) => new Element(tag),
    createElementNS: (ns, tag) => { assert.equal(ns, 'http://www.w3.org/2000/svg'); return new Element(tag); },
  };
  const requests = [];
  // Options are read at request time so tests can flip server behaviour
  // between loads through getters.
  const fetch = async (path) => {
    requests.push(path);
    const { projects = [], activity = null, failActivity = false, failDetail = false, pageSize = null } = options;
    if (path === '/api/v1/activity') {
      if (failActivity) return { ok: false, json: async () => ({}) };
      return { ok: true, json: async () => activity ?? activityJson(projects) };
    }
    if (path.startsWith('/api/v1/projects?')) {
      if (failDetail) return { ok: false, json: async () => ({}) };
      const cursor = new URL(path, 'https://fixture.local').searchParams.get('cursor');
      const offset = cursor ? projects.findIndex((p) => p.id === cursor.slice(cursor.indexOf(':') + 1)) + 1 : 0;
      const size = pageSize ?? projects.length;
      const slice = projects.slice(offset, offset + size);
      const last = slice.at(-1);
      const more = offset + size < projects.length;
      return { ok: true, json: async () => ({ items: slice.map(({ milestones, commitments, ...item }) => item), next_cursor: more && last ? `${Date.parse(last.created_at)}:${last.id}` : null }) };
    }
    if (failDetail) return { ok: false, json: async () => ({}) };
    const id = decodeURIComponent(path.split('/projects/')[1]);
    const found = projects.find((p) => p.id === id);
    return { ok: !!found, json: async () => found ?? {} };
  };
  const window = {};
  const sandbox = { document, window, fetch, requests, URLSearchParams, AbortController,
    setTimeout, clearTimeout, addEventListener: () => {} };
  for (const [filename, source] of sources) vm.runInNewContext(source, sandbox, { filename });
  return {
    nodes, requests, window,
    get: (id) => nodes.get(id),
    text: (id) => nodes.get(id).textContent,
    clickRefresh: () => nodes.get('activity-refresh').events.get('click')(),
  };
}

const flush = async () => { for (let count = 0; count < 120; count += 1) await Promise.resolve(); };

const now = () => Date.now();
const iso = (t) => new Date(t).toISOString();
const project = (index, { createdOffset = 3 * DAY, milestones = [], commitments = [] } = {}) => ({
  id: `fixture-project-${index}`, mission_id: 'build-the-commons', status: 'closed',
  created_at: iso(now() - createdOffset), milestones, commitments,
});
const activityJson = (projects) => {
  const today = Math.floor(now() / DAY) * DAY;
  return {
    generated_at: iso(now()), window: { days: 7, timezone: 'UTC' },
    totals: { missions: 1, contributions: 2, offers: 0, needs: 0 }, editorial_missions: 1,
    days: Array.from({ length: 7 }, (_, i) => ({ date: iso(today - (6 - i) * DAY).slice(0, 10), contributions: i === 6 ? 2 : 0, participations: 0 })),
    coordination: {
      projects_total: projects.length, projects_open: 0, projects_closed: projects.length, milestones_open: 0,
      milestones_done: projects.reduce((n, p) => n + p.milestones.filter((m) => m.status === 'done').length, 0),
      commitments_confirmed: 0,
      commitments_completed: projects.reduce((n, p) => n + p.commitments.filter((c) => c.status === 'completed').length, 0),
      deliveries_total: 0,
    },
  };
};
const growthPaths = (p) => p.get('activity-growth-chart').children
  .filter((child) => child.tagName === 'PATH')
  .map((child) => child.attributes.d);

test('the seven-day window and the mission curve share one record read', async () => {
  const done = iso(now() - DAY), completed = iso(now() - DAY + 60000);
  const projects = [project(1, {
    milestones: [{ id: 'm-1', status: 'done', updated_at: done }],
    commitments: [{ id: 'c-1', status: 'completed', updated_at: completed }, { id: 'c-2', status: 'completed', updated_at: completed }],
  })];
  const p = panel({ projects });
  await flush();
  assert.equal(p.get('activity-content').hidden, false);
  assert.match(p.text('activity-status'), /^Public snapshot · \d{2}:\d{2} (AM|PM) UTC$/);
  assert.match(p.text('activity-totals'), /Coordinated projects/);
  assert.equal(p.get('activity-growth').hidden, false, 'The mission curve is drawn');
  assert.match(p.text('activity-growth-summary'), /1 milestones completed · 2 commitments accepted · 1 projects coordinated — every point a public record in this snapshot\./);
  assert.match(p.text('activity-growth-window'), /– today · UTC · snapshot \d{2}:\d{2} (AM|PM) UTC$/);
  assert.equal(p.requests.filter((path) => path === '/api/v1/activity').length, 1, 'One activity read');
  assert.equal(p.requests.filter((path) => path.startsWith('/api/v1/projects?')).length, 1, 'One list read');
  assert.equal(p.requests.filter((path) => path.startsWith('/api/v1/projects/')).length, 1, 'One detail read — not one chain per curve');
});

test('simultaneous completions draw finite paths with one joint jump', async () => {
  const shared = iso(now() - DAY);
  const projects = [project(1, {
    milestones: [{ id: 'm-1', status: 'done', updated_at: shared }],
    commitments: [{ id: 'c-1', status: 'completed', updated_at: shared }, { id: 'c-2', status: 'completed', updated_at: shared }],
  })];
  const p = panel({ projects });
  await flush();
  assert.equal(p.get('activity-growth').hidden, false, 'The curve stays visible');
  for (const d of growthPaths(p)) {
    assert.doesNotMatch(d, /NaN|Infinity/, 'No invalid path data reaches the SVG');
    assert.match(d, /^M-?[\d.]+ -?[\d.]+/, 'The path still serializes');
  }
  const dots = p.get('activity-growth-chart').children.filter((child) => child.tagName === 'CIRCLE' && /activity-dot-commitments/.test(child.attributes.class));
  assert.equal(dots.length, 1, 'Two same-moment completions share one dot');
  assert.match(dots[0].children[0].textContent, /Commitments accepted 2/, 'The shared dot carries the joint count');
  assert.match(p.text('activity-growth-summary'), /2 commitments accepted/);
});

test('refresh renews the whole overview: tiles, timestamp and mission curve together', async () => {
  const done = iso(now() - DAY);
  const withCurve = (count) => Array.from({ length: count }, (_, i) => project(i + 1, {
    milestones: [{ id: `m-${i}`, status: 'done', updated_at: done }],
    commitments: [],
  }));
  let projects = withCurve(1);
  const p = panel({ get projects() { return projects; } });
  await flush();
  assert.match(p.text('activity-growth-summary'), /1 projects coordinated/);
  projects = withCurve(2);
  p.clickRefresh();
  await flush();
  assert.match(p.text('activity-status'), /^Public snapshot · \d{2}:\d{2} (AM|PM) UTC$/, 'The snapshot time renews');
  assert.match(p.text('activity-growth-summary'), /2 milestones completed · 0 commitments accepted · 2 projects coordinated/, 'The curve follows the refreshed record');
  assert.equal(p.requests.filter((path) => path === '/api/v1/activity').length, 2, 'The activity JSON re-reads');
  assert.equal(p.requests.filter((path) => path.startsWith('/api/v1/projects?')).length, 2, 'The shared record re-reads');
  assert.equal(p.requests.filter((path) => path.startsWith('/api/v1/projects/')).length, 3, 'Details for both projects, once each');
});

test('a transient detail failure hides only the curve and heals through refresh', async () => {
  let failDetail = true;
  const projects = [project(1, { milestones: [{ id: 'm-1', status: 'done', updated_at: iso(now() - DAY) }], commitments: [] })];
  const p = panel({ get projects() { return projects; }, get failDetail() { return failDetail; } });
  await flush();
  assert.equal(p.get('activity-content').hidden, false, 'The tiles still render');
  assert.equal(p.get('activity-growth').hidden, true, 'The curve waits for healthy data');
  failDetail = false;
  p.clickRefresh();
  await flush();
  assert.equal(p.get('activity-growth').hidden, false, 'Refresh heals the transient failure');
  assert.match(p.text('activity-growth-summary'), /1 milestones completed/);
});

test('a failed refresh keeps the drawn curve instead of blanking it', async () => {
  const projects = [project(1, { milestones: [{ id: 'm-1', status: 'done', updated_at: iso(now() - DAY) }], commitments: [] })];
  let broken = false;
  const p = panel({
    projects,
    get failActivity() { return broken; },
    get failDetail() { return broken; },
  });
  await flush();
  assert.equal(p.get('activity-growth').hidden, false);
  const drawn = p.text('activity-growth-summary');
  broken = true;
  p.clickRefresh();
  await flush();
  assert.equal(p.get('activity-growth').hidden, false, 'The earlier curve stays on a failed refresh');
  assert.equal(p.text('activity-growth-summary'), drawn);
  assert.match(p.text('activity-status'), /Refresh failed\. The earlier snapshot is still shown/);
});

test('the bounded record window marks its excerpt honestly', async () => {
  const projects = Array.from({ length: 320 }, (_, i) => project(i + 1, { createdOffset: (i + 2) * DAY }));
  const p = panel({ projects, pageSize: 100 });
  await flush();
  assert.equal(p.get('activity-growth').hidden, false);
  assert.match(p.text('activity-growth-window'), /newest 300 of more projects/, 'The window names the excerpt');
  assert.match(p.text('activity-growth-summary'), /^At least /);
  assert.match(p.text('activity-growth-summary'), /the bounded window carries the newest 300 projects; older records exist beyond it\./);
  assert.doesNotMatch(p.text('activity-growth-summary'), /every point a public record in this snapshot/, 'Never the complete-record claim for an excerpt');
});
