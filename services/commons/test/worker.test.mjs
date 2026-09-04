import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac, randomUUID } from 'node:crypto';
import worker, { cleanup, safeUrl } from '../worker.mjs';
import { SQLiteD1 } from '../local-d1.mjs';

const ORIGIN = 'https://oss-singularity.io';
const NOW = Date.parse('2026-09-05T12:34:00Z');
const DAY = 86_400_000;
const ADMIN = 'test_admin_secret_that_is_at_least_32_characters';
const SECRET = 'test_hmac_secret_that_is_at_least_32_characters';
const IP = '203.0.113.7';

function setup(t) {
  t.mock.method(Date, 'now', () => NOW);
  const DB = new SQLiteD1();
  t.after(() => DB.sqlite.close());
  return { DB, ADMIN_TOKEN: ADMIN, IP_HMAC_SECRET: SECRET, PUBLIC_ORIGIN: ORIGIN };
}

const payload = (extra = {}) => ({ kind: 'field-note', title: 'A useful field note', summary: 'A reproducible community field note with source evidence.', ...extra });

function request(path, method = 'GET', body, headers = {}) {
  return new Request(`${ORIGIN}/api/v1${path}`, {
    method,
    headers: { ...(body === undefined ? {} : { 'content-type': 'application/json' }), 'cf-connecting-ip': IP, ...headers },
    ...(body === undefined ? {} : { body: typeof body === 'string' ? body : JSON.stringify(body) }),
  });
}

async function send(env, path, method = 'GET', body, headers) {
  const result = await worker.fetch(request(path, method, body, headers), env);
  assert.equal(result.headers.get('cache-control'), 'no-store');
  assert.match(result.headers.get('x-robots-tag'), /noindex/);
  assert.equal(result.headers.get('access-control-allow-origin'), null);
  return { response: result, status: result.status, body: result.status === 204 ? null : await result.json() };
}

const auth = (token) => ({ authorization: `Bearer ${token}` });
const hash = (value) => createHash('sha256').update(value).digest('hex');
const bucket = (period, ip = IP, now = NOW) => createHmac('sha256', SECRET).update(`${period}:${Math.floor(now / (period === 'hour' ? 3_600_000 : DAY))}\n${ip}`).digest('hex');

function insert(db, { id = randomUUID(), status = 'pending', kind = 'field-note', created = NOW, updated = created, published = status === 'published' ? updated : null, token = 'x'.repeat(43) } = {}) {
  db.sqlite.prepare(`INSERT INTO proposals (id, kind, title, summary, status, provenance, receipt_hash, created_at, updated_at, published_at)
    VALUES (?, ?, 'Test proposal', 'A test proposal with sufficient explanatory content.', ?, 'community', ?, ?, ?, ?)`)
    .run(id, kind, status, hash(token), created, updated, published);
  return { id, token };
}

test('discovery, editorial seeds and empty community feed are truthful', async (t) => {
  const env = setup(t);
  const discovery = await send(env, '');
  assert.equal(discovery.status, 200);
  assert.equal(discovery.body.policy.automatic_execution, false);
  assert.equal(discovery.body.limits.pending_capacity, 200);
  const missions = await send(env, '/missions');
  assert.equal(missions.body.items.length, 4);
  assert.deepEqual(new Set(missions.body.items.map((item) => item.id)), new Set(['ship-feature', 'research-map', 'audit-project', 'build-the-commons']));
  for (const item of missions.body.items) {
    assert.equal(item.status, 'published');
    assert.equal(item.provenance, 'seed');
    assert.ok(!('receipt_hash' in item));
  }
  assert.deepEqual((await send(env, '/contributions')).body, { items: [], next_cursor: null });
});

test('proposal is durable and private, receipt stored hashed, moderation controls publication', async (t) => {
  const env = setup(t);
  const created = await send(env, '/proposals', 'POST', payload({ url: 'https://github.com/example/project', mission_id: 'ship-feature' }));
  assert.equal(created.status, 202);
  assert.match(created.body.receipt_token, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(created.body.poll_url, `/api/v1/proposals/${created.body.id}`);
  const stored = env.DB.sqlite.prepare('SELECT * FROM proposals WHERE id = ?').get(created.body.id);
  assert.equal(stored.receipt_hash, hash(created.body.receipt_token));
  assert.ok(!JSON.stringify(stored).includes(created.body.receipt_token));
  assert.deepEqual((await send(env, '/contributions')).body.items, []);
  assert.equal((await send(env, `/proposals/${created.body.id}`)).status, 401);
  assert.equal((await send(env, `/proposals/${created.body.id}`, 'GET', undefined, auth('wrong'.repeat(11)))).status, 404);
  const own = await send(env, `/proposals/${created.body.id}`, 'GET', undefined, auth(created.body.receipt_token));
  assert.equal(own.body.status, 'pending');
  assert.equal(own.body.mission_id, 'ship-feature');
  assert.ok(!JSON.stringify(own.body).includes('receipt'));
  assert.equal((await send(env, '/admin/proposals')).status, 401);
  const queue = await send(env, '/admin/proposals', 'GET', undefined, auth(ADMIN));
  assert.equal(queue.body.items[0].id, created.body.id);
  assert.equal((await send(env, `/admin/proposals/${created.body.id}`, 'PATCH', { status: 'published' }, auth(created.body.receipt_token))).status, 401);
  const published = await send(env, `/admin/proposals/${created.body.id}`, 'PATCH', { status: 'published' }, auth(ADMIN));
  assert.equal(published.status, 200);
  assert.equal(published.body.status, 'published');
  assert.equal(published.body.published_at, new Date(NOW).toISOString());
  const feed = await send(env, '/contributions?kind=field-note&mission_id=ship-feature');
  assert.equal(feed.body.items[0].id, created.body.id);
  assert.ok(!JSON.stringify(feed.body).includes(stored.receipt_hash));
  await send(env, `/admin/proposals/${created.body.id}`, 'PATCH', { status: 'rejected' }, auth(ADMIN));
  assert.deepEqual((await send(env, '/contributions')).body.items, []);
  assert.equal((await send(env, `/proposals/${created.body.id}`, 'GET', undefined, auth(created.body.receipt_token))).body.status, 'rejected');
});

test('mission and project submissions enter their correct public lists only after approval', async (t) => {
  const env = setup(t);
  for (const kind of ['mission', 'project']) {
    const proposal = await send(env, '/proposals', 'POST', payload({ kind }));
    assert.equal(proposal.status, 202);
    await send(env, `/admin/proposals/${proposal.body.id}`, 'PATCH', { status: 'published' }, auth(ADMIN));
  }
  assert.equal((await send(env, '/missions')).body.items.length, 5);
  const items = (await send(env, '/contributions')).body.items;
  assert.equal(items.length, 1);
  assert.equal(items[0].kind, 'project');
});

test('browser origin checks, preflight and method boundaries fail closed', async (t) => {
  const env = setup(t);
  for (const origin of ['null', 'https://attacker.example', 'https://oss-singularity.io.attacker.example', 'http://oss-singularity.io']) {
    assert.equal((await send(env, '/proposals', 'POST', payload(), { origin })).status, 403);
  }
  assert.equal((await send(env, '/proposals', 'POST', payload(), { 'sec-fetch-site': 'cross-site' })).status, 403);
  assert.equal((await send(env, '/proposals', 'OPTIONS')).status, 403);
  const preflight = await send(env, '/proposals', 'OPTIONS', undefined, { origin: ORIGIN });
  assert.equal(preflight.status, 204);
  assert.equal(preflight.response.headers.get('allow'), 'POST, OPTIONS');
  assert.equal((await send(env, '/proposals', 'POST', payload(), { origin: ORIGIN })).status, 202);
  assert.equal((await send(env, '/proposals', 'GET')).status, 405);
  assert.equal((await send(env, '/not-an-endpoint')).status, 404);
  const wrongHost = await worker.fetch(new Request('https://other.example/api/v1'), env);
  assert.equal(wrongHost.status, 403);
});

test('body size is checked against actual bytes, with or without content-length', async (t) => {
  const env = setup(t);
  for (const headers of [{}, { 'content-length': '1' }]) {
    assert.equal((await send(env, '/proposals', 'POST', payload({ summary: '🖤'.repeat(2100) }), headers)).status, 413);
  }
  assert.equal((await send(env, '/proposals', 'POST', payload(), { 'content-length': '9000' })).status, 413);
  assert.equal((await send(env, '/proposals', 'POST', payload(), { 'content-type': 'text/plain' })).status, 415);
  assert.equal((await send(env, '/proposals', 'POST', payload(), { 'content-encoding': 'gzip' })).status, 415);
  assert.equal((await send(env, '/proposals', 'POST', '{broken')).status, 400);
});

test('schema rejects unsupported fields and invalid lengths without consuming quota', async (t) => {
  const env = setup(t);
  for (const body of [[], null, 3, payload({ title: 'ab' }), payload({ title: 'x'.repeat(121) }), payload({ summary: 'short' }), payload({ summary: 'x'.repeat(2001) }), payload({ kind: 'script' }), payload({ admin: true }), payload({ title: 'bad\u0000title' }), payload({ mission_id: 'unknown' }), payload({ mission_id: '../secret' }), payload({ kind: 'mission', mission_id: 'ship-feature' })]) {
    const result = await send(env, '/proposals', 'POST', JSON.stringify(body));
    assert.equal(result.status, 400, JSON.stringify(body));
  }
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM rate_limits').get().count, 0);
});

test('URL policy rejects alternate IP forms, credentials, private hosts and dangerous schemes', () => {
  const rejected = [
    'javascript:alert(1)', 'http://github.com/project', 'data:text/html,hello',
    'https://localhost/x', 'https://sub.localhost/x', 'https://private.local/x',
    'https://127.0.0.1', 'https://10.0.0.1', 'https://169.254.169.254',
    'https://2130706433', 'https://0x7f000001', 'https://0177.0.0.1',
    'https://[::1]', 'https://[::ffff:127.0.0.1]', 'https://[2606:4700::1111]',
    'https://user:password@github.com', 'https://@github.com',
    'https://github.com:444/x', 'https://github.com\\@localhost/',
    'https://github.com./x', 'https://singlelabel/', 'https://foo.internal/',
  ];
  for (const url of rejected) assert.throws(() => safeUrl(url), undefined, url);
  assert.equal(safeUrl('https://github.com/org/repo?tab=readme#readme'), 'https://github.com/org/repo?tab=readme#readme');
  assert.equal(safeUrl('https://docs.python.org:443/3/'), 'https://docs.python.org/3/');
  assert.equal(safeUrl(''), null);
});

test('user content is returned as JSON text and never interpreted as HTML or SQL', async (t) => {
  const env = setup(t);
  const text = "<script>alert(1)</script> '); DROP TABLE proposals; --";
  const proposal = await send(env, '/proposals', 'POST', payload({ title: '<img src=x onerror=alert(1)>', summary: text }));
  assert.equal(proposal.status, 202);
  const own = await send(env, `/proposals/${proposal.body.id}`, 'GET', undefined, auth(proposal.body.receipt_token));
  assert.equal(own.body.summary, text);
  assert.match(own.response.headers.get('content-type'), /^application\/json/);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM proposals').get().count, 5);
});

test('concurrent submissions enforce the hourly cap with atomic counters', async (t) => {
  const env = setup(t);
  const attempts = await Promise.all(Array.from({ length: 12 }, () => send(env, '/proposals', 'POST', payload())));
  assert.equal(attempts.filter((result) => result.status === 202).length, 5);
  assert.equal(attempts.filter((result) => result.status === 429).length, 7);
  assert.equal(env.DB.sqlite.prepare("SELECT COUNT(*) AS count FROM proposals WHERE status = 'pending'").get().count, 5);
  const counters = env.DB.sqlite.prepare('SELECT * FROM rate_limits').all();
  assert.equal(counters.length, 2);
  assert.ok(counters.every((row) => row.count === 5 && row.expires_at === NOW + DAY));
  assert.ok(!JSON.stringify(counters).includes(IP));
  const denied = attempts.find((result) => result.status === 429);
  assert.equal(Number(denied.response.headers.get('retry-after')), 1560);
  assert.equal(denied.body.retry_after_seconds, 1560);
  assert.equal((await send(env, '/proposals', 'POST', payload(), { 'cf-connecting-ip': '203.0.113.8' })).status, 202);
});

test('daily cap remains effective after the hourly bucket changes', async (t) => {
  const env = setup(t);
  env.DB.sqlite.prepare('INSERT INTO rate_limits VALUES (?, 50, ?)').run(bucket('day'), NOW + DAY);
  const denied = await send(env, '/proposals', 'POST', payload());
  assert.equal(denied.status, 429);
  assert.equal(denied.body.retry_after_seconds, 41160);
  assert.equal(env.DB.sqlite.prepare("SELECT COUNT(*) AS count FROM proposals WHERE status = 'pending'").get().count, 0);
});

test('concurrent submissions cannot exceed the global pending cap', async (t) => {
  const env = setup(t);
  for (let index = 0; index < 198; index++) insert(env.DB);
  const attempts = await Promise.all(Array.from({ length: 12 }, (_, index) => send(env, '/proposals', 'POST', payload(), { 'cf-connecting-ip': `203.0.113.${index + 20}` })));
  assert.equal(attempts.filter((result) => result.status === 202).length, 2);
  assert.equal(attempts.filter((result) => result.status === 503 && result.body.error.code === 'queue_full').length, 10);
  assert.equal(env.DB.sqlite.prepare("SELECT COUNT(*) AS count FROM proposals WHERE status = 'pending'").get().count, 200);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM rate_limits').get().count, 4);
});

test('pagination is bounded, stable on timestamp ties and does not reveal pending entries', async (t) => {
  const env = setup(t);
  const expected = [];
  for (let index = 0; index < 7; index++) expected.push(insert(env.DB, { status: 'published' }).id);
  insert(env.DB);
  insert(env.DB, { status: 'rejected' });
  const received = [];
  let cursor = null;
  do {
    const page = await send(env, `/contributions?limit=2${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
    assert.equal(page.status, 200);
    assert.ok(page.body.items.length <= 2);
    received.push(...page.body.items.map((item) => item.id));
    cursor = page.body.next_cursor;
  } while (cursor);
  assert.deepEqual(received, expected.sort().reverse());
  for (const query of ['limit=0', 'limit=101', 'limit=1&limit=2', 'cursor=oops', 'kind=mission', 'status=pending', 'extra=1']) {
    assert.equal((await send(env, `/contributions?${query}`)).status, 400, query);
  }
});

test('expired content is inaccessible before cleanup, scheduled cleanup preserves published work', async (t) => {
  const env = setup(t);
  const old = NOW - 31 * DAY;
  const pending = insert(env.DB, { created: old });
  const rejected = insert(env.DB, { status: 'rejected', created: old });
  const published = insert(env.DB, { status: 'published', created: old });
  const recentRejection = insert(env.DB, { status: 'rejected', created: old, updated: NOW });
  env.DB.sqlite.prepare('INSERT INTO rate_limits VALUES (?, 1, ?)').run('1'.repeat(64), NOW - 1);
  env.DB.sqlite.prepare('INSERT INTO rate_limits VALUES (?, 1, ?)').run('2'.repeat(64), NOW + 1000);
  for (const expired of [pending, rejected]) {
    assert.equal((await send(env, `/proposals/${expired.id}`, 'GET', undefined, auth(expired.token))).status, 404);
    assert.equal((await send(env, `/admin/proposals/${expired.id}`, 'PATCH', { status: 'published' }, auth(ADMIN))).status, 404);
  }
  assert.deepEqual((await send(env, '/admin/proposals?status=pending', 'GET', undefined, auth(ADMIN))).body.items, []);
  await worker.scheduled({}, env);
  for (const expired of [pending, rejected]) assert.equal(env.DB.sqlite.prepare('SELECT id FROM proposals WHERE id = ?').get(expired.id), undefined);
  for (const preserved of [published, recentRejection]) assert.ok(env.DB.sqlite.prepare('SELECT id FROM proposals WHERE id = ?').get(preserved.id));
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM rate_limits').get().count, 1);
});

test('cleanup uses bounded batches and a subsequent pass drains remaining expired data', async (t) => {
  const env = setup(t);
  for (let index = 0; index < 3; index++) insert(env.DB, { created: NOW - 31 * DAY });
  await cleanup(env.DB, NOW, 2);
  assert.equal(env.DB.sqlite.prepare("SELECT COUNT(*) AS count FROM proposals WHERE status = 'pending'").get().count, 1);
  await cleanup(env.DB, NOW, 2);
  assert.equal(env.DB.sqlite.prepare("SELECT COUNT(*) AS count FROM proposals WHERE status = 'pending'").get().count, 0);
});

test('missing infrastructure and database errors expose no details or secrets', async (t) => {
  const env = setup(t);
  const cases = [{ ...env, DB: null }, { ...env, IP_HMAC_SECRET: '' }];
  for (const broken of cases) assert.equal((await send(broken, '/proposals', 'POST', payload())).status, 503);
  assert.equal((await send({ ...env, ADMIN_TOKEN: '' }, '/admin/proposals', 'GET', undefined, auth(ADMIN))).status, 503);
  assert.equal((await send(env, '/proposals', 'POST', payload(), { 'cf-connecting-ip': '' })).status, 503);
  const failed = await send({ ...env, DB: { prepare() { throw new Error(`secret SQL error ${ADMIN} ${IP}`); } } }, '/contributions');
  assert.equal(failed.status, 503);
  assert.ok(!JSON.stringify(failed.body).includes(ADMIN));
  assert.ok(!JSON.stringify(failed.body).includes(IP));
});

test('editorial seeds cannot be modified through community moderation', async (t) => {
  const env = setup(t);
  assert.equal((await send(env, '/admin/proposals/ship-feature', 'PATCH', { status: 'rejected' }, auth(ADMIN))).status, 404);
  assert.equal((await send(env, '/admin/proposals/ship-feature', 'PATCH', { status: 'pending' }, auth(ADMIN))).status, 400);
});
