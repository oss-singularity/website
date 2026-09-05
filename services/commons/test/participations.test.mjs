import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import worker from '../worker.mjs';
import { SQLiteD1 } from '../local-d1.mjs';
import { digest, randomToken } from '../security.mjs';
import { cleanupParticipations } from '../participations.mjs';

const NOW = Date.parse('2026-09-05T12:34:00Z');
const DAY = 86_400_000;
const ORIGIN = 'https://oss-singularity.io';
const ADMIN = 'test_admin_secret_that_is_at_least_32_characters';
let githubId = 1000;

function setup(t) {
  let now = NOW;
  t.mock.method(Date, 'now', () => now);
  const DB = new SQLiteD1();
  t.after(() => DB.sqlite.close());
  return { DB, PUBLIC_ORIGIN: ORIGIN, ADMIN_TOKEN: ADMIN, IP_HMAC_SECRET: 'test_hmac_secret_that_is_at_least_32_characters', advance: n => { now += n; } };
}

async function identity(env) {
  const id = randomUUID();
  const token = randomToken();
  env.DB.sqlite.prepare(`INSERT INTO identities (id, github_id, github_login, github_created_at, created_at, verified_at, token_hash)
    VALUES (?, ?, 'builder', ?, ?, ?, ?)`).run(id, ++githubId, Date.now(), Date.now(), Date.now(), await digest(token));
  return { id, token };
}

function mission(env, id = randomUUID()) {
  env.DB.sqlite.prepare(`INSERT INTO proposals (id, kind, title, summary, status, provenance, created_at, updated_at, published_at)
    VALUES (?, 'mission', 'A test mission', 'A bounded test mission with a concrete result.', 'published', 'seed', ?, ?, ?)`).run(id, Date.now(), Date.now(), Date.now());
  return id;
}

function payload(extra = {}) {
  return { mission_id: 'build-the-commons', intent: 'offer', participant_type: 'agent', collaboration: 'volunteer', title: 'A bounded contribution', summary: 'Review an isolated local checkout and deliver a source-backed report.', ...extra };
}

async function send(env, path, method = 'GET', body, token, ip = '203.0.113.11', extraHeaders = {}) {
  const response = await worker.fetch(new Request(ORIGIN + '/api/v1' + path, {
    method, headers: { 'cf-connecting-ip': ip, ...(body === undefined ? {} : { 'content-type': 'application/json' }), ...(token ? { authorization: `Bearer ${token}` } : {}), ...extraHeaders },
    ...(body === undefined ? {} : { body: typeof body === 'string' ? body : JSON.stringify(body) }),
  }), env);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('access-control-allow-origin'), null);
  return { status: response.status, body: response.status === 204 ? null : await response.json(), response };
}

async function create(env, actor, extra = {}, ip) {
  return send(env, '/participations', 'POST', payload(extra), actor.token, ip);
}

const moderate = (env, id, status = 'published') => send(env, `/admin/participations/${id}`, 'PATCH', { status }, ADMIN);
const change = (env, actor, id, state) => send(env, `/participations/${id}`, 'PATCH', { state }, actor.token);

async function insert(env, actor, { id = randomUUID(), mission_id = mission(env), status = 'pending', state = 'active', intent = 'offer', created_at = Date.now(), expires_at = created_at + 30 * DAY } = {}) {
  const token = randomToken();
  env.DB.sqlite.prepare(`INSERT INTO participations (id, mission_id, identity_id, intent, participant_type, collaboration,
    title, summary, status, state, receipt_hash, created_at, updated_at, published_at, expires_at)
    VALUES (?, ?, ?, ?, 'human', 'volunteer', 'A test participation', 'A synthetic local participation for a bounded test.', ?, ?, ?, ?, ?, ?, ?)`)
    .run(id, mission_id, actor.id, intent, status, state, await digest(token), created_at, created_at, status === 'published' ? created_at : null, expires_at);
  return { id, token };
}

test('new account participation is private and recoverable, with distinct identity/receipt/admin scopes', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const other = await identity(env);
  const result = await create(env, actor);
  assert.equal(result.status, 202);
  const { id, receipt_token: receipt } = result.body;
  assert.match(receipt, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(result.body.expires_at, new Date(NOW + 30 * DAY).toISOString());
  const stored = env.DB.sqlite.prepare('SELECT * FROM participations WHERE id = ?').get(id);
  assert.equal(stored.receipt_hash, await digest(receipt));
  assert.ok(!JSON.stringify(stored).includes(receipt));
  assert.deepEqual((await send(env, '/participations')).body.items, []);
  assert.equal((await send(env, '/participations/mine')).status, 401);
  const mine = await send(env, '/participations/mine', 'GET', undefined, actor.token);
  assert.equal(mine.body.items[0].id, id);
  assert.equal(mine.body.items[0].author.identity_id, actor.id);
  assert.deepEqual((await send(env, '/participations/mine', 'GET', undefined, other.token)).body.items, []);
  assert.equal((await send(env, `/participations/${id}`, 'GET', undefined, receipt)).status, 200);
  for (const wrong of [actor.token, other.token, ADMIN]) {
    assert.equal((await send(env, `/participations/${id}`, 'GET', undefined, wrong)).status, 404);
  }
  for (const wrong of [receipt, ADMIN]) {
    assert.equal((await send(env, '/participations/mine', 'GET', undefined, wrong)).status, 401);
    assert.equal((await send(env, `/participations/${id}`, 'PATCH', { state: 'withdrawn' }, wrong)).status, 401);
  }
  assert.equal((await send(env, '/admin/participations', 'GET', undefined, actor.token)).status, 401);
  assert.equal((await send(env, `/admin/participations/${id}`, 'PATCH', { status: 'published' }, receipt)).status, 401);
  const foreign = await change(env, other, id, 'withdrawn');
  const absent = await change(env, other, 'unknown-id', 'withdrawn');
  assert.equal(foreign.status, 404);
  assert.deepEqual(foreign.body, absent.body);
  assert.ok(!JSON.stringify(mine.body).includes('receipt'));
});

test('publication starts one final lifetime; close and withdraw have distinct idempotent meanings', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const result = await create(env, actor);
  const { id, receipt_token: receipt } = result.body;
  assert.equal((await change(env, actor, id, 'closed')).body.error.code, 'invalid_transition');
  env.advance(DAY);
  const approved = await moderate(env, id);
  assert.equal(approved.status, 200);
  assert.equal(approved.body.expires_at, new Date(NOW + 31 * DAY).toISOString());
  assert.equal((await moderate(env, id)).status, 409);
  const publicCard = (await send(env, '/participations?mission_id=build-the-commons&intent=offer')).body.items[0];
  assert.equal(publicCard.author.verification, 'github-account-control');
  assert.equal(publicCard.participant_type, 'agent');
  const closed = await change(env, actor, id, 'closed');
  assert.equal(closed.status, 200);
  assert.equal(closed.body.state, 'closed');
  env.advance(DAY);
  assert.deepEqual((await change(env, actor, id, 'closed')).body, closed.body);
  assert.equal((await send(env, '/participations')).body.items.length, 0);
  assert.equal((await send(env, '/participations?state=closed')).body.items[0].id, id);
  assert.equal((await moderate(env, id)).status, 409);
  const withdrawn = await change(env, actor, id, 'withdrawn');
  assert.equal(withdrawn.body.expires_at, approved.body.expires_at);
  assert.equal((await send(env, '/participations?state=all')).body.items.length, 0);
  env.advance(DAY);
  assert.deepEqual((await change(env, actor, id, 'withdrawn')).body, withdrawn.body);
  assert.equal((await send(env, `/participations/${id}`, 'GET', undefined, receipt)).body.state, 'withdrawn');
  assert.equal((await send(env, '/participations/mine', 'GET', undefined, actor.token)).body.items[0].state, 'withdrawn');
  assert.equal((await change(env, actor, id, 'closed')).status, 409);
  assert.equal((await moderate(env, id)).status, 409);
});

test('pending withdrawal and rejection never imply acceptance and release the active slot', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const first = await create(env, actor);
  const withdrawn = await change(env, actor, first.body.id, 'withdrawn');
  assert.equal(withdrawn.body.status, 'pending');
  assert.equal(withdrawn.body.published_at, null);
  assert.equal((await moderate(env, first.body.id)).status, 409);
  const second = await create(env, actor);
  assert.equal(second.status, 202);
  assert.equal((await moderate(env, second.body.id, 'rejected')).body.status, 'rejected');
  assert.equal((await moderate(env, second.body.id)).status, 409);
  assert.equal((await create(env, actor)).status, 202);
  assert.equal((await send(env, '/participations/mine', 'GET', undefined, actor.token)).body.items.length, 3);
});

test('strict payload, URL, query and origin boundaries do not consume submission quota', async t => {
  const env = setup(t);
  const actor = await identity(env);
  for (const extra of [{ intent: 'assign' }, { participant_type: 'verified-agent' }, { collaboration: 'paid' }, { identity_id: actor.id }, { status: 'published' }, { state: 'closed' }, { title: 'ab' }, { summary: 'tiny' }, { url: 'http://github.com/x' }, { url: 'https://127.0.0.1/' }, { mission_id: '../x' }]) {
    assert.equal((await create(env, actor, extra)).status, 400, JSON.stringify(extra));
  }
  assert.equal((await create(env, actor, { mission_id: 'missing' })).status, 404);
  assert.equal((await send(env, '/participations', 'POST', payload(), actor.token, undefined, { origin: 'https://other.example' })).status, 403);
  assert.equal((await send(env, '/participations', 'POST', payload(), actor.token, undefined, { 'sec-fetch-site': 'cross-site' })).status, 403);
  assert.equal((await create(env, actor, { summary: '🖤'.repeat(2100) })).status, 413);
  assert.equal((await send(env, '/participations', 'POST', payload(), actor.token, undefined, { 'content-type': 'text/plain' })).status, 415);
  for (const query of ['state=withdrawn', 'state=expired', 'limit=0', 'limit=101', 'limit=1&limit=2', 'cursor=broken', 'identity_id=someone', 'intent=assign']) {
    assert.equal((await send(env, '/participations?' + query)).status, 400, query);
  }
  assert.equal((await send(env, '/participations/mine?identity_id=other', 'GET', undefined, actor.token)).status, 400);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM rate_limits').get().count, 0);
  const preflight = await send(env, '/participations', 'OPTIONS', undefined, undefined, undefined, { origin: ORIGIN });
  assert.equal(preflight.status, 204);
  assert.equal(preflight.response.headers.get('allow'), 'GET, POST, OPTIONS');
  assert.equal((await send(env, '/participations/unknown', 'DELETE', undefined, actor.token)).status, 405);
});

test('concurrent identical participation requests create one card and one set of counters', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const results = await Promise.all(Array.from({ length: 12 }, () => create(env, actor)));
  assert.equal(results.filter(result => result.status === 202).length, 1);
  assert.equal(results.filter(result => result.body.error?.code === 'duplicate_participation').length, 11);
  const counters = env.DB.sqlite.prepare('SELECT count FROM rate_limits').all();
  assert.equal(counters.length, 4);
  assert.ok(counters.every(row => row.count === 1));
});

test('hourly participation quotas bind both identity and network without allocating rejected counters', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const missions = Array.from({ length: 14 }, () => mission(env));
  const results = await Promise.all(missions.slice(0, 12).map(mission_id => create(env, actor, { mission_id })));
  assert.equal(results.filter(result => result.status === 202).length, 5);
  assert.equal(results.filter(result => result.status === 429).length, 7);
  assert.equal((await create(env, actor, { mission_id: missions[12] }, '203.0.113.99')).status, 429);
  const other = await identity(env);
  const denied = await create(env, other, { mission_id: missions[13] });
  assert.equal(denied.status, 429);
  assert.ok(Number(denied.response.headers.get('retry-after')) > 0);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM rate_limits').get().count, 4);
  assert.equal((await create(env, other, { mission_id: missions[13] }, '203.0.113.100')).status, 202);
});

test('daily limits survive hourly changes, withdrawals and changes of address or identity', async t => {
  const env = setup(t);
  const actor = await identity(env);
  // Fixed UTC day: start just after midnight so ten hours remain in one day.
  env.advance(-12 * 3_600_000);
  for (let hour = 0; hour < 10; hour++) {
    for (let count = 0; count < 5; count++) {
      const result = await create(env, actor);
      assert.equal(result.status, 202);
      assert.equal((await change(env, actor, result.body.id, 'withdrawn')).status, 200);
    }
    env.advance(3_600_000);
  }
  assert.equal((await create(env, actor)).status, 429);
  assert.equal((await create(env, actor, {}, '203.0.113.99')).status, 429);
  const other = await identity(env);
  assert.equal((await create(env, other)).status, 429);
  assert.equal((await create(env, other, {}, '203.0.113.99')).status, 202);
});

test('concurrent identity active cap and global pending cap cannot overshoot', async t => {
  const env = setup(t);
  const actor = await identity(env);
  for (let index = 0; index < 9; index++) await insert(env, actor);
  const attempts = await Promise.all(Array.from({ length: 12 }, () => create(env, actor, { mission_id: mission(env) })));
  assert.equal(attempts.filter(row => row.status === 202).length, 1);
  assert.equal(attempts.filter(row => row.body.error?.code === 'active_limit').length, 11);
  const filler = await identity(env);
  for (let index = 0; index < 188; index++) await insert(env, filler);
  const actors = await Promise.all(Array.from({ length: 12 }, () => identity(env)));
  const capped = await Promise.all(actors.map((actor, index) => create(env, actor, {}, `203.0.113.${index + 30}`)));
  assert.equal(capped.filter(row => row.status === 202).length, 2);
  assert.equal(capped.filter(row => row.body.error?.code === 'queue_full').length, 10);
  assert.equal(env.DB.sqlite.prepare("SELECT COUNT(*) AS count FROM participations WHERE status = 'pending' AND state = 'active'").get().count, 200);
  // The first accepted active-cap card plus two accepted queue-cap cards.
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM rate_limits').get().count, 12);
});

test('target publication and token revocation are rechecked inside the insertion transaction', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const batch = env.DB.batch.bind(env.DB);
  let revoke = false;
  const newHash = await digest(randomToken());
  env.DB.batch = async statements => {
    if (statements.some(statement => statement.sql.includes('INSERT INTO participations'))) {
      if (revoke) env.DB.sqlite.prepare('UPDATE identities SET token_hash = ? WHERE id = ?').run(newHash, actor.id);
      else env.DB.sqlite.prepare("UPDATE proposals SET status = 'rejected', published_at = NULL WHERE id = 'build-the-commons'").run();
    }
    return batch(statements);
  };
  assert.equal((await create(env, actor)).status, 404);
  env.DB.sqlite.prepare("UPDATE proposals SET status = 'published', published_at = ? WHERE id = 'build-the-commons'").run(NOW);
  revoke = true;
  assert.equal((await create(env, actor)).status, 401);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM participations').get().count, 0);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM rate_limits').get().count, 0);
});

test('mission withdrawal prevents moderation and hides published participation; identity removal deletes cards', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const first = await create(env, actor);
  const second = await create(env, actor, { intent: 'need' });
  assert.equal((await moderate(env, first.body.id)).status, 200);
  env.DB.sqlite.prepare("UPDATE proposals SET status = 'rejected', published_at = NULL WHERE id = 'build-the-commons'").run();
  assert.equal((await moderate(env, second.body.id)).status, 409);
  assert.deepEqual((await send(env, '/participations?state=all')).body.items, []);
  assert.equal((await send(env, '/participations?mission_id=build-the-commons')).status, 404);
  assert.equal((await send(env, `/participations/${first.body.id}`, 'GET', undefined, first.body.receipt_token)).status, 200);
  env.DB.sqlite.prepare('DELETE FROM identities WHERE id = ?').run(actor.id);
  assert.equal((await send(env, `/participations/${first.body.id}`, 'GET', undefined, first.body.receipt_token)).status, 404);
  assert.equal((await send(env, '/participations/mine', 'GET', undefined, actor.token)).status, 401);
});

test('concurrent owner withdrawal and admin publication cannot reopen a card', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const first = await create(env, actor);
  const results = await Promise.all([change(env, actor, first.body.id, 'withdrawn'), moderate(env, first.body.id)]);
  assert.equal(results[0].status, 200);
  assert.ok([200, 409].includes(results[1].status));
  const row = env.DB.sqlite.prepare('SELECT state FROM participations WHERE id = ?').get(first.body.id);
  assert.equal(row.state, 'withdrawn');
  assert.equal((await moderate(env, first.body.id)).status, 409);
  assert.deepEqual((await send(env, '/participations?state=all')).body.items, []);
});

test('expiration is immediate across every view and action and releases uniqueness despite cleanup backlog', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const expired = await insert(env, actor, { mission_id: 'build-the-commons', status: 'published', created_at: NOW - 31 * DAY, expires_at: NOW });
  assert.deepEqual((await send(env, '/participations?state=all')).body.items, []);
  assert.deepEqual((await send(env, '/participations/mine', 'GET', undefined, actor.token)).body.items, []);
  assert.equal((await send(env, `/participations/${expired.id}`, 'GET', undefined, expired.token)).status, 404);
  assert.equal((await change(env, actor, expired.id, 'closed')).status, 404);
  assert.equal((await moderate(env, expired.id)).status, 404);
  const filler = await identity(env);
  for (let index = 0; index < 105; index++) await insert(env, filler, { created_at: NOW - 32 * DAY, expires_at: NOW - DAY });
  const fresh = await create(env, actor);
  assert.equal(fresh.status, 202);
  assert.equal(env.DB.sqlite.prepare('SELECT state FROM participations WHERE id = ?').get(expired.id).state, 'expired');
  await cleanupParticipations(env.DB, NOW, 2);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM participations WHERE expires_at <= ?').get(NOW).count, 4);
  await worker.scheduled({}, env);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM participations').get().count, 1);
});

test('mission lookup, mission-filtered results and stable card pagination expose published data only', async t => {
  const env = setup(t);
  const actor = await identity(env);
  assert.equal((await send(env, '/missions/build-the-commons')).body.kind, 'mission');
  assert.equal((await send(env, '/missions/missing')).status, 404);
  assert.equal((await send(env, '/missions/build-the-commons?extra=1')).status, 400);
  const ids = [];
  for (let index = 0; index < 3; index++) {
    const result = await insert(env, actor, { status: 'published' });
    ids.push(result.id);
  }
  await insert(env, actor);
  const page = await send(env, '/participations?limit=2&state=all');
  assert.equal(page.body.items.length, 2);
  const next = await send(env, '/participations?limit=2&state=all&cursor=' + page.body.next_cursor);
  assert.equal(next.body.next_cursor, null);
  assert.deepEqual([...page.body.items, ...next.body.items].map(row => row.id), ids.sort().reverse());
  assert.deepEqual((await send(env, '/contributions?mission_id=build-the-commons')).body.items, []);
  assert.equal((await send(env, '/participations/mine?limit=2', 'GET', undefined, actor.token)).body.items.length, 2);
});

test('participation text is immutable data and cannot alter SQL or authorize work', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const summary = "<script>alert(1)</script> '); DROP TABLE proposals; -- This is untrusted reference data.";
  const result = await create(env, actor, { summary, collaboration: 'discuss-compensation', participant_type: 'team' });
  assert.equal(result.status, 202);
  const card = await moderate(env, result.body.id);
  assert.equal(card.body.summary, summary);
  assert.equal(card.body.collaboration, 'discuss-compensation');
  assert.equal((await send(env, `/participations/${result.body.id}`, 'PATCH', { summary }, actor.token)).status, 400);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM proposals').get().count, 4);
  assert.equal((await send(env, '')).body.policy.automatic_execution, false);
});

test('all self-described participant types have the same participation rules', async t => {
  const env = setup(t);
  const actor = await identity(env);
  for (const participant_type of ['human', 'agent', 'team', 'other']) {
    const result = await create(env, actor, { participant_type });
    assert.equal(result.status, 202);
    const approved = await moderate(env, result.body.id);
    assert.equal(approved.status, 200);
    assert.equal(approved.body.participant_type, participant_type);
    assert.equal(approved.body.author.verification, 'github-account-control');
    await change(env, actor, result.body.id, 'withdrawn');
  }
});

test('owner PATCH rechecks token rotation after preliminary authentication', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const result = await create(env, actor);
  const newToken = randomToken();
  const newHash = await digest(newToken);
  const prepare = env.DB.prepare.bind(env.DB);
  let rotated = false;
  env.DB.prepare = sql => {
    if (!rotated && sql.includes('UPDATE participations SET state = ?, updated_at')) {
      env.DB.sqlite.prepare('UPDATE identities SET token_hash = ? WHERE id = ?').run(newHash, actor.id);
      rotated = true;
    }
    return prepare(sql);
  };
  assert.equal((await change(env, actor, result.body.id, 'withdrawn')).status, 401);
  assert.equal(env.DB.sqlite.prepare('SELECT state FROM participations WHERE id = ?').get(result.body.id).state, 'active');
  assert.equal((await change(env, { ...actor, token: newToken }, result.body.id, 'withdrawn')).status, 200);
});

test('failure before counter increment rolls back both participation and allocated buckets', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const execute = env.DB.execute.bind(env.DB);
  env.DB.execute = statement => {
    if (statement.sql.includes('UPDATE rate_limits SET count = count + 1')) throw new Error('Synthetic database failure');
    return execute(statement);
  };
  const result = await create(env, actor);
  assert.equal(result.status, 503);
  assert.equal(result.body.error.code, 'service_unavailable');
  assert.ok(!JSON.stringify(result.body).includes('Synthetic'));
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM participations').get().count, 0);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS count FROM rate_limits').get().count, 0);
});

test('activity exposes bounded public counts and zero-filled UTC dates without identities', async t => {
  const env = setup(t);
  const result = await send(env, '/activity');
  assert.equal(result.status, 200);
  assert.deepEqual(result.body.totals, { missions: 4, contributions: 0, offers: 0, needs: 0 });
  assert.equal(result.body.editorial_missions, 4);
  assert.deepEqual(result.body.window, { days: 7, timezone: 'UTC' });
  assert.equal(result.body.generated_at, new Date(NOW).toISOString());
  assert.equal(result.body.days.length, 7);
  assert.equal(result.body.days[0].date, '2026-08-30');
  assert.equal(result.body.days.at(-1).date, '2026-09-05');
  assert.ok(result.body.days.every(day => day.contributions === 0 && day.participations === 0));
  assert.equal((await send(env, '/activity?days=100')).status, 400);
  assert.equal((await send(env, '/activity', 'POST', {})).status, 405);
});

test('activity counts currently public records with exact UTC boundaries and excludes reviews and seeds from series', async t => {
  const env = setup(t);
  const actor = await identity(env);
  const start = Math.floor(NOW / DAY) * DAY - 6 * DAY;
  const end = start + 7 * DAY;
  const hashedReceipt = await digest(randomToken());
  function contribution(published, { kind = 'field-note', status = 'published', provenance = 'community' } = {}) {
    env.DB.sqlite.prepare(`INSERT INTO proposals (id, kind, title, summary, status, provenance, receipt_hash, created_at, updated_at, published_at)
      VALUES (?, ?, 'An activity fixture', 'A synthetic contribution for a local activity test.', ?, ?, ?, ?, ?, ?)`)
      .run(randomUUID(), kind, status, provenance, provenance === 'seed' ? null : hashedReceipt, published, published, status === 'published' ? published : null);
  }
  for (const published of [start - 1, start, end - 1, end]) contribution(published);
  contribution(start, { status: 'pending' });
  contribution(start, { status: 'rejected' });
  contribution(start, { kind: 'project', provenance: 'seed' });
  contribution(start, { kind: 'mission' });
  env.DB.sqlite.prepare(`INSERT INTO proposals (id, kind, title, summary, url, target_id, score, identity_id, status, provenance, receipt_hash, created_at, updated_at, published_at)
    VALUES (?, 'review', 'Excluded review', 'A synthetic review excluded from contribution activity.', 'https://github.com/oss-singularity/website', 'audit-project', 4, ?, 'published', 'community', ?, ?, ?, ?)`)
    .run(randomUUID(), actor.id, hashedReceipt, start, start, start);
  const active = await insert(env, actor, { status: 'published', created_at: start });
  await insert(env, actor, { status: 'published', state: 'closed', created_at: start });
  await insert(env, actor, { status: 'published', intent: 'need', created_at: NOW });
  await insert(env, actor, { status: 'published', state: 'withdrawn', created_at: start });
  await insert(env, actor, { status: 'published', created_at: start, expires_at: NOW });
  await insert(env, actor, { created_at: start });
  const hiddenMission = mission(env);
  await insert(env, actor, { status: 'published', mission_id: hiddenMission, created_at: start });
  env.DB.sqlite.prepare("UPDATE proposals SET status = 'rejected', published_at = NULL WHERE id = ?").run(hiddenMission);
  // Even a legacy/imported orphan must fail the public visibility predicate.
  const orphanActor = await identity(env);
  await insert(env, orphanActor, { status: 'published', created_at: start });
  env.DB.sqlite.exec('PRAGMA foreign_keys = OFF');
  env.DB.sqlite.prepare('DELETE FROM identities WHERE id = ?').run(orphanActor.id);
  env.DB.sqlite.exec('PRAGMA foreign_keys = ON');
  const result = await send(env, '/activity');
  assert.equal(result.body.totals.contributions, 4);
  assert.equal(result.body.editorial_missions, 11); // Four real seeds plus seven public fixture missions.
  assert.equal(result.body.totals.missions - result.body.editorial_missions, 1);
  assert.equal(result.body.totals.offers, 1);
  assert.equal(result.body.totals.needs, 1);
  assert.equal(result.body.days[0].contributions, 1);
  assert.equal(result.body.days[6].contributions, 1);
  assert.equal(result.body.days[0].participations, 2); // Active and closed are both still public.
  assert.equal(result.body.days[6].participations, 1);
  assert.ok(!JSON.stringify(result.body).includes(actor.id));
  assert.ok(!JSON.stringify(result.body).includes(active.id));
  await change(env, actor, active.id, 'withdrawn');
  const withdrawn = await send(env, '/activity');
  assert.equal(withdrawn.body.days[0].participations, 1); // Snapshot, not historical event count.
  assert.equal(withdrawn.body.totals.offers, 0);
});

test('additive migration preserves an existing database and restart does not replay seeds', async t => {
  const directory = mkdtempSync(join(tmpdir(), 'commons-migration-test-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const path = join(directory, 'existing.sqlite');
  const original = new DatabaseSync(path);
  original.exec(readFileSync(new URL('../migrations/0001_commons.sql', import.meta.url), 'utf8'));
  original.prepare("UPDATE proposals SET title = 'Preserved editorial title' WHERE id = 'build-the-commons'").run();
  const before = original.prepare('SELECT * FROM proposals ORDER BY id').all();
  original.close();
  const upgraded = new SQLiteD1(path);
  assert.deepEqual(upgraded.sqlite.prepare('SELECT * FROM proposals ORDER BY id').all(), before);
  assert.equal(upgraded.sqlite.prepare('SELECT COUNT(*) AS count FROM participations').get().count, 0);
  for (const table of ['work_items', 'work_item_results', 'work_item_events']) {
    assert.equal(upgraded.sqlite.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get().count, 0);
  }
  assert.deepEqual(upgraded.sqlite.prepare('SELECT name FROM local_migrations ORDER BY name').all().map(row => row.name), ['0001_commons.sql', '0002_participations.sql', '0003_work_items.sql']);
  upgraded.sqlite.prepare("DELETE FROM proposals WHERE id = 'ship-feature'").run();
  upgraded.sqlite.close();
  const reopened = new SQLiteD1(path);
  assert.equal(reopened.sqlite.prepare('SELECT COUNT(*) AS count FROM proposals').get().count, 3);
  assert.equal(reopened.sqlite.prepare('PRAGMA integrity_check').get().integrity_check, 'ok');
  assert.equal(reopened.sqlite.prepare('PRAGMA foreign_keys').get().foreign_keys, 1);
  assert.deepEqual(reopened.sqlite.prepare('PRAGMA foreign_key_check').all(), []);
  reopened.sqlite.close();
});
