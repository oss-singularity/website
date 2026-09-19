import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../site/assets/scripts/commons-growth-data-v1.js', import.meta.url), 'utf8');

const DAY = 86400000;

// Exercise the shared read client with fixture API payloads. The sandbox
// records every request so pagination, deduplication and failure behaviour
// stay observable.
function reader({ route } = {}) {
  const requests = [];
  const fetch = async (path, options) => {
    requests.push({ path, options });
    const response = await route(path, options);
    return { ok: (response.status ?? 200) < 400, status: response.status ?? 200, json: async () => response.body };
  };
  const window = {};
  const sandbox = { window, fetch, requests, URLSearchParams, AbortController, setTimeout, clearTimeout,
    addEventListener: () => {} };
  vm.runInNewContext(source, sandbox, { filename: 'commons-growth-data-v1.js' });
  return { requests, api: window.OssGrowthData };
}

const flush = async () => { for (let n = 0; n < 60; n += 1) await Promise.resolve(); };

const project = (index, { createdOffset = 3 * DAY, milestones = [], commitments = [] } = {}) => ({
  id: `fixture-project-${index}`, mission_id: 'build-the-commons', status: 'closed',
  created_at: new Date(Date.now() - createdOffset).toISOString(),
  milestones, commitments,
});
const listPage = (items, nextCursor = null) => ({ items, next_cursor: nextCursor });
const summaryOf = (project) => ({ id: project.id, mission_id: project.mission_id, status: 'closed', created_at: project.created_at });

const routesWith = (projects, { pageSize = null, failDetail = false } = {}) => (path) => {
  if (path.startsWith('/api/v1/projects?')) {
    const cursor = new URL(path, 'https://fixture.local').searchParams.get('cursor');
    const offset = cursor ? projects.findIndex((p) => p.id === cursor.slice(cursor.indexOf(':') + 1)) + 1 : 0;
    const size = pageSize ?? projects.length;
    const slice = projects.slice(offset, offset + size);
    const last = slice.at(-1);
    const more = offset + size < projects.length;
    return { body: listPage(slice.map(summaryOf), more && last ? `${Date.parse(last.created_at)}:${last.id}` : null) };
  }
  const id = decodeURIComponent(path.split('/projects/')[1]);
  if (failDetail) return { status: 503, body: {} };
  const found = projects.find((p) => p.id === id);
  return found ? { body: found } : { status: 404, body: {} };
};

test('one read chain serves every subscriber and follows cursors to completeness', async () => {
  const projects = Array.from({ length: 101 }, (_, i) => project(i + 1, { createdOffset: (i + 2) * DAY }));
  const r = reader({ route: routesWith(projects, { pageSize: 50 }) });
  const states = [];
  r.api.subscribe((state) => states.push(state));
  r.api.subscribe(() => {});
  await flush();
  assert.equal(states.length, 1, 'Both subscribers receive the shared snapshot');
  const snapshot = states[0];
  assert.equal(snapshot.ok, true);
  assert.equal(snapshot.complete, true, '101 projects across three pages are complete');
  assert.equal(snapshot.projects.length, 101);
  assert.equal(snapshot.totals.projects, 101);
  assert.equal(snapshot.startedAt, Date.parse(projects.at(-1).created_at), 'The record starts at the oldest project');
  const listReads = r.requests.filter(({ path }) => path.startsWith('/api/v1/projects?'));
  assert.deepEqual(listReads.map(({ path }) => new URL(path, 'https://x').searchParams.get('cursor')),
    [null, `${Date.parse(projects[49].created_at)}:fixture-project-50`, `${Date.parse(projects[99].created_at)}:fixture-project-100`],
    'Cursor pages are followed explicitly');
  assert.equal(listReads.every(({ path }) => new URL(path, 'https://x').searchParams.get('limit') === '100'), true,
    'The bounded page size requests the API maximum');
  assert.equal(r.requests.filter(({ path }) => !path.includes('?')).length, 101, 'One detail request per project, not one per subscriber');
});

test('the bounded window marks truncation instead of passing an excerpt off as complete', async () => {
  const projects = Array.from({ length: 340 }, (_, i) => project(i + 1, { createdOffset: (i + 2) * DAY }));
  const r = reader({ route: routesWith(projects, { pageSize: 100 }) });
  const states = [];
  r.api.subscribe((state) => states.push(state));
  await flush();
  const snapshot = states.at(-1);
  assert.equal(snapshot.ok, true);
  assert.equal(snapshot.complete, false, 'More pages waited beyond the window bound');
  assert.equal(snapshot.projects.length, 300);
  assert.equal(snapshot.totals.projects, 300, 'The excerpt counts only what it carries');
});

test('equal moments collapse into one joint jump with true cumulative counts', async () => {
  const shared = new Date(Date.now() - DAY).toISOString();
  const commitments = [0, 1, 2].map((i) => ({ id: `c-${i}`, status: 'completed', updated_at: shared }));
  const later = new Date(Date.now() - DAY + 40).toISOString();
  commitments.push({ id: 'c-3', status: 'completed', updated_at: later });
  const r = reader({ route: routesWith([project(1, { commitments })]) });
  const states = [];
  r.api.subscribe((state) => states.push(state));
  await flush();
  const { series, totals } = states.at(-1);
  assert.equal(totals.commitments, 4, 'Every event is counted');
  assert.deepEqual([...series.commitments].map((point) => ({ t: point.t, count: point.count })), [
    { t: Date.parse(shared), count: 3 },
    { t: Date.parse(later), count: 4 },
  ], 'Same-moment events leap together; the count never repeats an x');
});

test('a transient failure notifies honestly and heals on refresh without stale overwrites', async () => {
  let failDetail = true;
  const projects = [project(1), project(2)];
  const r = reader({ route: (path) => (path.startsWith('/api/v1/projects/') && failDetail ? { status: 503, body: {} } : routesWith(projects)(path)) });
  const states = [];
  r.api.subscribe((state) => states.push(state));
  await flush();
  assert.deepEqual(states.map((state) => state.ok), [false], 'The failed first read reports failure');
  failDetail = false;
  await r.api.refresh();
  await flush();
  assert.ok(states.at(-1).ok, 'Refresh heals the transient detail failure');
  assert.equal(states.at(-1).totals.projects, 2);
  await r.api.refresh();
  await flush();
  assert.equal(states.filter((state) => state.ok).length, 2, 'Each refresh renews the shared snapshot');
});

test('refresh joins an in-flight read instead of duplicating the request chain', async () => {
  let released = false;
  const gate = async () => { while (!released) await new Promise((resolve) => setTimeout(resolve, 0)); };
  const projects = [project(1)];
  const r = reader({ route: async (path) => { await gate(); return routesWith(projects)(path); } });
  const states = [];
  r.api.subscribe((state) => states.push(state));
  await Promise.resolve();
  await r.api.refresh(); // clicked while the first read is still in flight
  released = true;
  for (let tick = 0; tick < 5; tick += 1) await new Promise((resolve) => setTimeout(resolve, 0));
  await flush();
  assert.equal(r.requests.filter(({ path }) => path.startsWith('/api/v1/projects?')).length, 1,
    'No second chain races the first');
  assert.equal(states.at(-1).ok, true);
});

test('hostile or unexpected payloads fail closed', async () => {
  const good = project(1, { commitments: [{ id: 'c-0', status: 'completed', updated_at: new Date().toISOString() }] });
  const cases = [
    ['list transport failure', (path) => path.startsWith('/api/v1/projects?') ? { status: 503, body: {} } : { body: good }],
    ['detail transport failure', (path) => path.startsWith('/api/v1/projects?') ? { body: listPage([summaryOf(good)]) } : { status: 503, body: {} }],
    ['hostile list status', () => ({ body: { items: [{ ...summaryOf(good), status: 'published' }], next_cursor: null } })],
    ['hostile detail milestone status', () => ({ body: { ...good, milestones: [{ id: 'm', status: 'published', updated_at: new Date().toISOString() }] } })],
    ['detail id mismatch', () => ({ body: { ...good, id: 'fixture-project-other' } })],
    ['oversized page', () => ({ body: listPage(Array.from({ length: 101 }, () => summaryOf(good))) })],
    ['hostile cursor type', () => ({ body: { items: [], next_cursor: 17 } })],
  ];
  for (const [name, route] of cases) {
    const r = reader({ route });
    const states = [];
    r.api.subscribe((state) => states.push(state));
    await flush();
    assert.deepEqual(states.map((state) => state.ok), [false], `${name}: the read fails closed`);
  }
});
