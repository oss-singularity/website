import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import worker from '../worker.mjs';
import { SQLiteD1 } from '../local-d1.mjs';
import { digest, randomToken } from '../security.mjs';
import { cleanupWorkItems } from '../work-items.mjs';

const ORIGIN = 'https://oss-singularity.io';
const NOW = Date.parse('2026-09-05T12:34:00Z');
const DAY = 86_400_000;
const ADMIN = 'test_only_admin_token_at_least_32_characters';
const auth = token => ({ authorization: `Bearer ${token}` });

async function setup(t) {
  let clock = NOW;
  t.mock.method(Date, 'now', () => clock);
  const DB = new SQLiteD1();
  t.after(() => DB.sqlite.close());
  const env = { DB, PUBLIC_ORIGIN: ORIGIN, ADMIN_TOKEN: ADMIN, IP_HMAC_SECRET: 'test_only_hmac_secret_at_least_32_characters' };
  let sequence = 1;
  async function account() {
    const id = randomUUID(), token = randomToken(), github = sequence++;
    DB.sqlite.prepare('INSERT INTO identities VALUES (?, ?, ?, ?, ?, ?, ?)').run(id, github, `fixture-${github}`, clock - 40 * DAY, clock, clock, await digest(token));
    return { id, token };
  }
  const requester = await account(), contributor = await account(), outsider = await account();
  const send = async (path, method = 'GET', body, actor = null, extraHeaders = {}) => {
    const result = await worker.fetch(new Request(`${ORIGIN}/api/v1${path}`, { method,
      headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.9', ...(actor ? auth(actor.token || actor) : {}), ...extraHeaders },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    }), env);
    assert.equal(result.headers.get('cache-control'), 'no-store');
    return { status: result.status, body: result.status === 204 ? null : await result.json(), headers: result.headers };
  };
  const mine = async (id, actor = requester) => send(`/work-items/mine/${id}`, 'GET', undefined, actor);
  const publicItem = id => send(`/work-items/${id}`);
  const createBody = extra => ({ client_request_id: randomUUID(), mission_id: 'build-the-commons', title: 'A bounded keyboard check',
    scope: 'Inspect the local keyboard journey with synthetic records only.', deliverable: 'A concise report with repeatable steps and clear limitations.',
    acceptance: ['The report names the exact controls and a repeatable local sequence.'], terms: 'volunteer', public_consent: true, ...extra });
  const create = async extra => send('/work-items', 'POST', createBody(extra), requester);
  const moderate = async (id, status = 'published') => {
    const row = DB.sqlite.prepare('SELECT version FROM work_items WHERE id = ?').get(id);
    return send(`/admin/work-items/${id}`, 'PATCH', { expected_version: row.version, status }, ADMIN);
  };
  const actBody = (item, action, extra = {}) => ({ client_request_id: randomUUID(), expected_version: item.version, action, ...extra });
  const act = async (id, actor, action, extra = {}) => {
    const row = DB.sqlite.prepare('SELECT version FROM work_items WHERE id = ?').get(id);
    return send(`/work-items/${id}/actions`, 'POST', actBody(row, action, extra), actor);
  };
  const active = async () => {
    const made = await create(); assert.equal(made.status, 202, JSON.stringify(made.body));
    const id = made.body.item.id;
    const published = await moderate(id); assert.equal(published.status, 200, JSON.stringify(published.body));
    const offered = await act(id, contributor, 'offer', { public_consent: true }); assert.equal(offered.status, 200, JSON.stringify(offered.body));
    const confirmed = await act(id, requester, 'confirm'); assert.equal(confirmed.status, 200, JSON.stringify(confirmed.body));
    return id;
  };
  const resultBody = (id, extra = {}) => ({ client_request_id: randomUUID(), expected_version: DB.sqlite.prepare('SELECT version FROM work_items WHERE id = ?').get(id).version,
    kind: 'field-note', title: 'Local keyboard findings', summary: 'The exact local path, observed behavior and remaining limits are documented here.',
    url: 'https://github.com/oss-singularity/website/pull/123', public_consent: true, ...extra });
  const submitResult = (id, extra) => send(`/work-items/${id}/results`, 'POST', resultBody(id, extra), contributor);
  const publishResult = (proposal, status = 'published') => send(`/admin/proposals/${proposal}`, 'PATCH', { status }, ADMIN);
  return { DB, env, send, requester, contributor, outsider, account, mine, publicItem, createBody, create, moderate, actBody, act, active, resultBody, submitResult, publishResult,
    advance: duration => { clock += duration; }, now: () => clock };
}

test('moderated private creation, explicit two-party confirmation, and attributed revision handoff', async t => {
  const h = await setup(t);
  const requestBody = h.createBody();
  assert.equal((await h.send('/work-items', 'POST', requestBody)).status, 401);
  const made = await h.send('/work-items', 'POST', requestBody, h.requester);
  assert.equal(made.status, 202, JSON.stringify(made.body));
  const id = made.body.item.id;
  assert.equal(made.body.item.version, 1);
  assert.equal(made.body.item.moderation, 'pending');
  assert.deepEqual(made.body.item.allowed_actions, ['cancel']);
  assert.equal((await h.publicItem(id)).status, 404);
  assert.equal((await h.mine(id, h.outsider)).status, 404);
  const replay = await h.send('/work-items', 'POST', requestBody, h.requester);
  assert.equal(replay.status, 200);
  assert.equal(replay.body.item.id, id);
  assert.equal(replay.body.operation.replayed, true);
  assert.equal((await h.send('/work-items', 'POST', { ...requestBody, title: 'Different task' }, h.requester)).body.error.code, 'idempotency_conflict');
  assert.equal((await h.moderate(id)).status, 200);
  assert.equal((await h.act(id, h.requester, 'offer', { public_consent: true })).status, 409);
  const offered = await h.act(id, h.contributor, 'offer', { public_consent: true });
  assert.equal(offered.status, 200, JSON.stringify(offered.body));
  assert.equal(offered.body.item.state, 'offered');
  const publicOffer = await h.publicItem(id);
  assert.equal(publicOffer.body.contributor, null);
  assert.ok(!JSON.stringify(publicOffer.body).includes(h.contributor.id));
  assert.equal((await h.mine(id)).body.offer.identity_id, h.contributor.id);
  assert.equal((await h.act(id, h.outsider, 'confirm')).status, 404);
  const confirmed = await h.act(id, h.requester, 'confirm');
  assert.equal(confirmed.status, 200);
  assert.equal(confirmed.body.item.contributor.identity_id, h.contributor.id);
  assert.equal(confirmed.body.item.state, 'active');
  assert.ok((await h.mine(id, h.contributor)).body.allowed_actions.includes('submit_result'));

  const resultBody = h.resultBody(id);
  const posted = await h.send(`/work-items/${id}/results`, 'POST', resultBody, h.contributor);
  assert.equal(posted.status, 202, JSON.stringify(posted.body));
  const result = posted.body.result_id, proposal = posted.body.receipt.id;
  assert.match(posted.body.receipt.receipt_token, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(posted.body.item.state, 'active');
  assert.equal(posted.body.item.own_results[0].author_identity_id, h.contributor.id);
  assert.equal(h.DB.sqlite.prepare('SELECT identity_id FROM proposals WHERE id = ?').get(proposal).identity_id, h.contributor.id);
  assert.equal((await h.mine(id)).body.own_results.length, 0);
  assert.ok(!JSON.stringify((await h.publicItem(id)).body).includes(proposal));
  const replayResult = await h.send(`/work-items/${id}/results`, 'POST', resultBody, h.contributor);
  assert.equal(replayResult.status, 200);
  assert.equal(replayResult.body.result_id, result);
  assert.equal(replayResult.body.receipt, undefined);
  assert.equal((await h.act(id, h.contributor, 'deliver', { result_id: result })).status, 409);
  const beforePublish = (await h.mine(id)).body.version;
  assert.equal((await h.publishResult(proposal)).status, 200);
  assert.equal((await h.mine(id)).body.version, beforePublish + 1);
  const delivered = await h.act(id, h.contributor, 'deliver', { result_id: result });
  assert.equal(delivered.status, 200, JSON.stringify(delivered.body));
  assert.equal(delivered.body.item.current_result_available, true);
  assert.equal(delivered.body.item.last_delivered_revision, 1);
  assert.equal((await h.act(id, h.contributor, 'acknowledge', { result_id: result })).status, 409);
  assert.equal((await h.act(id, h.requester, 'request_revision')).status, 200);
  assert.equal((await h.act(id, h.contributor, 'deliver', { result_id: result })).status, 409);
  const revised = await h.submitResult(id); assert.equal(revised.status, 202, JSON.stringify(revised.body));
  assert.equal(revised.body.item.own_results[1].revision, 2);
  assert.equal((await h.publishResult(revised.body.receipt.id)).status, 200);
  assert.equal((await h.act(id, h.contributor, 'deliver', { result_id: revised.body.result_id })).status, 200);
  assert.equal((await h.act(id, h.requester, 'acknowledge', { result_id: result })).status, 409);
  const done = await h.act(id, h.requester, 'acknowledge', { result_id: revised.body.result_id });
  assert.equal(done.status, 200, JSON.stringify(done.body));
  assert.equal(done.body.item.state, 'acknowledged');
  assert.equal(done.body.item.acknowledged_result_id, revised.body.result_id);
  assert.equal((await h.act(id, h.requester, 'cancel')).status, 409);
  const publicDone = (await h.publicItem(id)).body;
  assert.equal(publicDone.acknowledged_result_id, revised.body.result_id);
  assert.ok(!JSON.stringify(publicDone).includes(posted.body.receipt.receipt_token));
  assert.ok(!JSON.stringify(publicDone).includes('request_digest'));
});

test('first public offer needs no prior membership; declined candidates cannot see a replacement offer', async t => {
  const h = await setup(t), made = await h.create(), id = made.body.item.id;
  await h.moderate(id);
  assert.equal((await h.mine(id, h.contributor)).status, 404);
  const body = h.actBody((await h.publicItem(id)).body, 'offer', { public_consent: true });
  const offered = await h.send(`/work-items/${id}/actions`, 'POST', body, h.contributor);
  assert.equal(offered.status, 200);
  await h.act(id, h.requester, 'decline');
  await h.act(id, h.outsider, 'offer', { public_consent: true });
  const former = await h.mine(id, h.contributor);
  assert.equal(former.status, 200);
  assert.equal(former.body.offer, null);
  assert.equal(former.body.viewer.past_participant, true);
  assert.ok(!JSON.stringify(former.body).includes(h.outsider.id));
  const replay = await h.send(`/work-items/${id}/actions`, 'POST', body, h.contributor);
  assert.equal(replay.status, 200);
  assert.equal(replay.body.operation.replayed, true);
  assert.equal(replay.body.item.offer, null);
  await h.act(id, h.outsider, 'withdraw_offer');
  assert.equal((await h.publicItem(id)).body.state, 'open');
});

test('simultaneous offers and result requests admit one operation without orphan data or quota charges', async t => {
  const h = await setup(t), made = await h.create(), id = made.body.item.id;
  await h.moderate(id);
  const row = (await h.publicItem(id)).body;
  const offers = await Promise.all([h.contributor, h.outsider].map(actor => h.send(`/work-items/${id}/actions`, 'POST', h.actBody(row, 'offer', { public_consent: true }), actor)));
  assert.deepEqual(offers.map(r => r.status).sort(), [200, 409]);
  assert.equal(h.DB.sqlite.prepare("SELECT COUNT(*) n FROM work_item_events WHERE work_item_id = ? AND action = 'offer'").get(id).n, 1);
  const winner = offers[0].status === 200 ? h.contributor : h.outsider;
  await h.act(id, h.requester, 'confirm');
  const body = h.resultBody(id);
  const results = await Promise.all([0, 1].map(() => h.send(`/work-items/${id}/results`, 'POST', body, winner)));
  assert.deepEqual(results.map(r => r.status).sort(), [200, 202]);
  assert.equal(results[0].body.result_id, results[1].body.result_id);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_results WHERE work_item_id = ?').get(id).n, 1);
  assert.equal(h.DB.sqlite.prepare("SELECT COUNT(*) n FROM proposals WHERE kind = 'field-note'").get().n, 1);
  assert.equal(results.filter(r => r.body.receipt).length, 1);
});

test('result authorship cannot be inferred from foreign or anonymous proposals; revoked in-flight tokens cannot write', async t => {
  const h = await setup(t), id = await h.active();
  const before = h.DB.sqlite.prepare('SELECT COUNT(*) n FROM proposals').get().n;
  assert.equal((await h.send(`/work-items/${id}/results`, 'POST', h.resultBody(id, { proposal_id: randomUUID() }), h.contributor)).status, 400);
  assert.equal((await h.send(`/work-items/${id}/results`, 'POST', h.resultBody(id, { author_identity_id: h.outsider.id }), h.contributor)).status, 400);
  assert.equal((await h.send(`/work-items/${id}/results`, 'POST', h.resultBody(id), h.requester)).status, 404);
  const originalBatch = h.DB.batch.bind(h.DB);
  const nextHash = await digest(randomToken());
  let revoked = false;
  h.DB.batch = async statements => {
    if (!revoked && statements.some(s => s.sql.includes("'result_submitted'"))) {
      revoked = true;
      h.DB.sqlite.prepare('UPDATE identities SET token_hash = ? WHERE id = ?').run(nextHash, h.contributor.id);
    }
    return originalBatch(statements);
  };
  const failed = await h.submitResult(id);
  assert.equal(failed.status, 401, JSON.stringify(failed.body));
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM proposals').get().n, before);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_results').get().n, 0);
  assert.equal(h.DB.sqlite.prepare("SELECT COUNT(*) n FROM work_item_events WHERE action = 'result_submitted'").get().n, 0);
  assert.equal(h.DB.sqlite.prepare('SELECT result_revision_count FROM work_items WHERE id = ?').get(id).result_revision_count, 0);
});

test('proposal deletion preserves monotonic revisions and lost-response replay without creating a replacement', async t => {
  const h = await setup(t), id = await h.active(), body = h.resultBody(id);
  const first = await h.send(`/work-items/${id}/results`, 'POST', body, h.contributor);
  assert.equal(first.status, 202);
  await h.publishResult(first.body.receipt.id);
  await h.act(id, h.contributor, 'deliver', { result_id: first.body.result_id });
  const before = (await h.mine(id)).body.version;
  h.DB.sqlite.prepare('DELETE FROM proposals WHERE id = ?').run(first.body.receipt.id);
  const changed = (await h.mine(id, h.contributor)).body;
  assert.equal(changed.version, before + 1);
  assert.equal(changed.current_result_available, false);
  assert.equal(changed.last_delivered_revision, 1);
  assert.equal(changed.own_results.length, 0);
  assert.equal((await h.act(id, h.requester, 'acknowledge', { result_id: first.body.result_id })).status, 409);
  const replay = await h.send(`/work-items/${id}/results`, 'POST', body, h.contributor);
  assert.equal(replay.status, 200);
  assert.equal(replay.body.result_id, first.body.result_id);
  assert.equal(replay.body.receipt, undefined);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_results').get().n, 0);
  const next = await h.submitResult(id);
  assert.equal(next.status, 202, JSON.stringify(next.body));
  assert.equal(next.body.item.own_results[0].revision, 2);
});

test('result moderation updates availability and version without growing history; parent withdrawal never resurrects work', async t => {
  const h = await setup(t), id = await h.active(), result = await h.submitResult(id);
  const count = h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_events WHERE work_item_id = ?').get(id).n;
  let previous = (await h.mine(id)).body.version;
  for (const status of ['published', 'rejected', 'published', 'rejected']) {
    assert.equal((await h.publishResult(result.body.receipt.id, status)).status, 200);
    const next = (await h.mine(id)).body.version;
    assert.equal(next, previous + 1); previous = next;
  }
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_events WHERE work_item_id = ?').get(id).n, count);
  h.DB.sqlite.prepare("UPDATE proposals SET status = 'rejected', published_at = NULL, updated_at = ? WHERE id = 'build-the-commons'").run(h.now());
  assert.equal((await h.publicItem(id)).status, 404);
  const privateItem = (await h.mine(id)).body;
  assert.equal(privateItem.state, 'cancelled');
  assert.equal(privateItem.parent_available, false);
  h.DB.sqlite.prepare("UPDATE proposals SET status = 'published', published_at = ?, updated_at = ? WHERE id = 'build-the-commons'").run(h.now(), h.now());
  assert.equal((await h.publicItem(id)).status, 404);
  assert.equal((await h.mine(id)).body.state, 'cancelled');
});

test('offer expiry, identity removal and bounded retention remain effective before cleanup', async t => {
  const h = await setup(t), made = await h.create(), id = made.body.item.id;
  await h.moderate(id);
  await h.act(id, h.contributor, 'offer', { public_consent: true });
  const oldVersion = (await h.mine(id)).body.version;
  h.advance(2 * DAY);
  const snapshot = (await h.publicItem(id)).body;
  assert.equal(snapshot.state, 'open');
  assert.equal(snapshot.version, oldVersion + 1);
  const offered = await h.send(`/work-items/${id}/actions`, 'POST', h.actBody(snapshot, 'offer', { public_consent: true }), h.outsider);
  assert.equal(offered.status, 200, JSON.stringify(offered.body));
  h.DB.sqlite.prepare('DELETE FROM identities WHERE id = ?').run(h.outsider.id);
  assert.equal((await h.publicItem(id)).body.state, 'open');
  assert.ok(h.DB.sqlite.prepare('SELECT id FROM work_items WHERE id = ?').get(id));
  const cancelSnapshot = (await h.mine(id)).body;
  assert.equal((await h.send(`/work-items/${id}/actions`, 'POST', h.actBody(cancelSnapshot, 'cancel'), h.requester)).status, 200);
  assert.equal((await h.publicItem(id)).status, 404);
  h.advance(30 * DAY);
  assert.equal((await h.mine(id)).status, 404);
  await cleanupWorkItems(h.DB, h.now(), 1);
  assert.equal(h.DB.sqlite.prepare('SELECT id FROM work_items WHERE id = ?').get(id), undefined);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_events WHERE work_item_id = ?').get(id).n, 0);
});

test('regular operation/revision caps preserve cancellation and do not charge unsuccessful writes', async t => {
  const h = await setup(t), id = await h.active();
  h.DB.sqlite.prepare('UPDATE work_items SET operation_count = 128 WHERE id = ?').run(id);
  const counters = h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all();
  const failed = await h.submitResult(id);
  assert.equal(failed.status, 409, JSON.stringify(failed.body));
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_results').get().n, 0);
  assert.deepEqual(h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all(), counters);
  assert.equal((await h.act(id, h.contributor, 'cancel')).status, 200);
  assert.equal((await h.mine(id)).body.state, 'cancelled');
});

test('public filters, scopes, media and private export boundaries remain explicit', async t => {
  const h = await setup(t), id = await h.active();
  for (const path of ['/work-items?state=ongoing', '/work-items?state=active', '/work-items?state=all', '/work-items?mission_id=build-the-commons']) {
    const read = await h.send(path); assert.equal(read.status, 200, JSON.stringify(read.body));
    assert.equal(read.body.items[0].id, id);
    assert.equal(read.body.items[0].own_results, undefined);
    assert.equal(read.body.items[0].offer, undefined);
  }
  for (const path of ['/work-items?state=cancelled', '/work-items?limit=51', '/work-items?limit=1&limit=2', `/work-items/${id}?extra=1`]) assert.equal((await h.send(path)).status, 400, path);
  assert.equal((await h.send('/work-items?mission_id=not-published')).status, 404);
  assert.equal((await h.send('/admin/work-items', 'GET', undefined, h.requester)).status, 401);
  assert.equal((await h.send('/work-items/mine')).status, 401);
  assert.equal((await h.send(`/work-items/${id}/actions`, 'POST', h.actBody((await h.publicItem(id)).body, 'cancel'), h.requester, { Origin: 'https://foreign.example' })).status, 403);
});

test('cancellation winning a result race leaves no orphan proposal, event or quota row', async t => {
  const h = await setup(t), id = await h.active(), body = h.resultBody(id);
  const originalBatch = h.DB.batch.bind(h.DB);
  const counters = h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all();
  let cancelled = false;
  h.DB.batch = async statements => {
    if (!cancelled && statements.some(s => s.sql.includes("'result_submitted'"))) {
      cancelled = true;
      const response = await h.act(id, h.requester, 'cancel');
      assert.equal(response.status, 200);
    }
    return originalBatch(statements);
  };
  const failed = await h.send(`/work-items/${id}/results`, 'POST', body, h.contributor);
  assert.equal(failed.status, 409);
  assert.equal(failed.body.error.code, 'version_conflict');
  assert.equal(h.DB.sqlite.prepare("SELECT COUNT(*) n FROM proposals WHERE kind = 'field-note'").get().n, 0);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_results').get().n, 0);
  assert.equal(h.DB.sqlite.prepare("SELECT COUNT(*) n FROM work_item_events WHERE action = 'result_submitted'").get().n, 0);
  assert.deepEqual(h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all(), counters);
});

test('create and ordinary actions recheck a rotated token inside the transaction', async t => {
  const h = await setup(t);
  const originalBatch = h.DB.batch.bind(h.DB);
  let revoked = false;
  const nextToken = randomToken(), nextHash = await digest(nextToken);
  h.DB.batch = async statements => {
    if (!revoked && statements.some(s => s.sql.includes('INSERT INTO work_items'))) {
      revoked = true;
      h.DB.sqlite.prepare('UPDATE identities SET token_hash = ? WHERE id = ?').run(nextHash, h.requester.id);
    }
    return originalBatch(statements);
  };
  const failedCreate = await h.create();
  assert.equal(failedCreate.status, 401);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_items').get().n, 0);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM rate_limits').get().n, 0);
  h.requester.token = nextToken;
  const made = await h.create(), id = made.body.item.id;
  await h.moderate(id);
  const contributorHash = await digest(randomToken());
  revoked = false;
  h.DB.batch = async statements => {
    if (!revoked && statements.some(s => s.sql.includes('INSERT INTO work_item_events') && s.values.includes('offer'))) {
      revoked = true;
      h.DB.sqlite.prepare('UPDATE identities SET token_hash = ? WHERE id = ?').run(contributorHash, h.contributor.id);
    }
    return originalBatch(statements);
  };
  const failedOffer = await h.act(id, h.contributor, 'offer', { public_consent: true });
  assert.equal(failedOffer.status, 401);
  assert.equal((await h.publicItem(id)).body.state, 'open');
  assert.equal(h.DB.sqlite.prepare("SELECT COUNT(*) n FROM work_item_events WHERE action = 'offer'").get().n, 0);
});

test('result revision limit survives removed associations and blocks only new work, not cancellation', async t => {
  const h = await setup(t), id = await h.active();
  h.DB.sqlite.prepare('UPDATE work_items SET result_revision_count = 10 WHERE id = ?').run(id);
  const failed = await h.submitResult(id);
  assert.equal(failed.status, 409);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_results').get().n, 0);
  assert.equal((await h.mine(id, h.contributor)).body.allowed_actions.includes('submit_result'), false);
  assert.equal((await h.act(id, h.contributor, 'cancel')).status, 200);
});

test('completed contributors free slots while delivery/revision and pending offers still count', async t => {
  const h = await setup(t), seed = await h.active();
  const base = h.DB.sqlite.prepare('SELECT * FROM work_items WHERE id = ?').get(seed);
  // Build the bounded quota edge without manufacturing 40 unrelated API calls.
  const insert = h.DB.sqlite.prepare(`INSERT INTO work_items (id, mission_id, requester_identity_id, contributor_identity_id,
    title, scope, deliverable, acceptance, terms, moderation, state, created_at, updated_at, published_at, expires_at,
    client_request_id, request_digest, creation_operation_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'volunteer', 'published', ?, ?, ?, ?, ?, ?, ?, ?)`);
  for (let index = 0; index < 9; index++) insert.run(randomUUID(), base.mission_id, base.requester_identity_id, h.contributor.id,
    base.title, base.scope, base.deliverable, base.acceptance, index % 2 ? 'revision_requested' : 'delivered', NOW, NOW, NOW, NOW + 90 * DAY, randomUUID(), 'a'.repeat(64), randomUUID());
  // A separate requester creates another item so the creator cap is independent.
  const made = await h.send('/work-items', 'POST', h.createBody(), h.outsider);
  assert.equal(made.status, 202, JSON.stringify(made.body));
  const id = made.body.item.id;
  await h.moderate(id);
  assert.equal((await h.act(id, h.contributor, 'offer', { public_consent: true })).status, 409);
  h.DB.sqlite.prepare("UPDATE work_items SET state = 'acknowledged' WHERE id = ?").run(seed);
  assert.equal((await h.act(id, h.contributor, 'offer', { public_consent: true })).status, 200);
});

test('creator capacity and hourly quota reject without creating state', async t => {
  const h = await setup(t);
  const first = await h.create(); assert.equal(first.status, 202);
  const base = h.DB.sqlite.prepare('SELECT * FROM work_items WHERE id = ?').get(first.body.item.id);
  const insert = h.DB.sqlite.prepare(`INSERT INTO work_items (id, mission_id, requester_identity_id, title, scope, deliverable, acceptance,
    terms, moderation, state, created_at, updated_at, expires_at, client_request_id, request_digest, creation_operation_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'volunteer', 'pending', 'open', ?, ?, ?, ?, ?, ?)`);
  for (let index = 0; index < 9; index++) insert.run(randomUUID(), base.mission_id, h.requester.id, base.title, base.scope, base.deliverable,
    base.acceptance, NOW, NOW, NOW + 90 * DAY, randomUUID(), 'a'.repeat(64), randomUUID());
  const counters = h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all();
  assert.equal((await h.create()).body.error.code, 'capacity_reached');
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_items').get().n, 10);
  assert.deepEqual(h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all(), counters);
  h.DB.sqlite.prepare('UPDATE rate_limits SET count = 5').run();
  const rate = await h.create();
  assert.equal(rate.status, 429);
  assert.ok(Number(rate.headers.get('retry-after')) > 0);
});

test('per-mission, global pending and retained capacities are separate hard boundaries', async t => {
  const h = await setup(t);
  const template = h.createBody();
  h.DB.sqlite.prepare(`INSERT INTO proposals (id, kind, title, summary, status, provenance, created_at, updated_at, published_at)
    VALUES ('fixture-spare-mission', 'mission', 'Spare fixture mission', 'A local synthetic parent for capacity checks.', 'published', 'seed', ?, ?, ?)`).run(NOW, NOW, NOW);
  const insert = h.DB.sqlite.prepare(`INSERT INTO work_items (id, mission_id, requester_identity_id, title, scope, deliverable, acceptance,
    terms, moderation, state, created_at, updated_at, published_at, expires_at, client_request_id, request_digest, creation_operation_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'volunteer', ?, 'open', ?, ?, ?, ?, ?, ?, ?)`);
  const fill = (count, mission, moderation = 'published') => {
    for (let index = 0; index < count; index++) insert.run(randomUUID(), mission, h.outsider.id, template.title, template.scope, template.deliverable,
      JSON.stringify(template.acceptance), moderation, NOW, NOW, moderation === 'published' ? NOW : null, NOW + 90 * DAY, randomUUID(), 'a'.repeat(64), randomUUID());
  };
  fill(100, 'build-the-commons');
  assert.equal((await h.create()).body.error.code, 'capacity_reached');
  h.DB.sqlite.prepare('DELETE FROM work_items').run();
  fill(100, 'research-map', 'pending'); fill(100, 'ship-feature', 'pending');
  assert.equal((await h.create()).body.error.code, 'capacity_reached');
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_items').get().n, 200);
  h.DB.sqlite.prepare('DELETE FROM work_items').run();
  fill(1000, 'research-map');
  assert.equal((await h.create({ mission_id: 'fixture-spare-mission' })).body.error.code, 'capacity_reached');
  h.DB.sqlite.prepare('DELETE FROM work_items WHERE id = (SELECT id FROM work_items LIMIT 1)').run();
  assert.equal((await h.create({ mission_id: 'fixture-spare-mission' })).status, 202);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_items').get().n, 1000);
});

test('pending result cleanup invalidates versions without resetting revision count; published work survives item expiry', async t => {
  const h = await setup(t), id = await h.active(), first = await h.submitResult(id);
  const firstVersion = (await h.mine(id)).body.version;
  h.advance(30 * DAY);
  const expiredDraft = (await h.mine(id, h.contributor)).body;
  assert.equal(expiredDraft.own_results.length, 0);
  assert.ok(expiredDraft.allowed_actions.includes('submit_result'));
  await worker.scheduled({}, h.env);
  assert.equal(h.DB.sqlite.prepare('SELECT id FROM proposals WHERE id = ?').get(first.body.receipt.id), undefined);
  assert.equal((await h.mine(id)).body.version, firstVersion + 1);
  const second = await h.submitResult(id);
  assert.equal(second.status, 202, JSON.stringify(second.body));
  assert.equal(second.body.item.own_results[0].revision, 2);
  await h.publishResult(second.body.receipt.id);
  await h.act(id, h.contributor, 'deliver', { result_id: second.body.result_id });
  await h.act(id, h.requester, 'acknowledge', { result_id: second.body.result_id });
  h.advance(60 * DAY);
  assert.equal((await h.publicItem(id)).status, 404);
  assert.equal((await h.mine(id)).status, 404);
  await cleanupWorkItems(h.DB, h.now(), 1);
  assert.equal(h.DB.sqlite.prepare('SELECT id FROM work_items WHERE id = ?').get(id), undefined);
  assert.equal(h.DB.sqlite.prepare('SELECT status FROM proposals WHERE id = ?').get(second.body.receipt.id).status, 'published');
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_results').get().n, 0);
});

test('SQL failure after result admission rolls back the entire proposal/result/event/quota transaction', async t => {
  const h = await setup(t), id = await h.active();
  const originalBatch = h.DB.batch.bind(h.DB);
  const before = h.DB.sqlite.prepare('SELECT version, operation_count, result_revision_count FROM work_items WHERE id = ?').get(id);
  const counters = h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all();
  h.DB.batch = async statements => {
    if (statements.some(s => s.sql.includes("'result_submitted'"))) {
      const broken = h.DB.prepare('INSERT INTO table_that_does_not_exist VALUES (1)');
      return originalBatch([...statements, broken]);
    }
    return originalBatch(statements);
  };
  const failed = await h.submitResult(id);
  assert.equal(failed.status, 503);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_results').get().n, 0);
  assert.equal(h.DB.sqlite.prepare("SELECT COUNT(*) n FROM proposals WHERE kind = 'field-note'").get().n, 0);
  assert.equal(h.DB.sqlite.prepare("SELECT COUNT(*) n FROM work_item_events WHERE action = 'result_submitted'").get().n, 0);
  assert.deepEqual(h.DB.sqlite.prepare('SELECT version, operation_count, result_revision_count FROM work_items WHERE id = ?').get(id), before);
  assert.deepEqual(h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all(), counters);
});

test('expired pending proposals still occupy the physical hard queue until ordinary cleanup', async t => {
  const h = await setup(t), id = await h.active();
  const insert = h.DB.sqlite.prepare(`INSERT INTO proposals (id, kind, title, summary, status, provenance, receipt_hash, created_at, updated_at)
    VALUES (?, 'field-note', 'Expired queue entry', 'A synthetic expired pending proposal occupies one physical slot.', 'pending', 'community', ?, ?, ?)`);
  for (let i = 0; i < 200; i++) insert.run(randomUUID(), 'a'.repeat(64), h.now() - 31 * DAY, h.now() - 31 * DAY);
  const before = h.DB.sqlite.prepare('SELECT version, operation_count, result_revision_count FROM work_items WHERE id = ?').get(id);
  const counters = h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all();
  const result = await h.submitResult(id);
  assert.equal(result.status, 409, JSON.stringify(result.body));
  assert.equal(h.DB.sqlite.prepare("SELECT COUNT(*) n FROM proposals WHERE status = 'pending'").get().n, 200);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_results').get().n, 0);
  assert.equal(h.DB.sqlite.prepare("SELECT COUNT(*) n FROM work_item_events WHERE action = 'result_submitted'").get().n, 0);
  assert.deepEqual(h.DB.sqlite.prepare('SELECT version, operation_count, result_revision_count FROM work_items WHERE id = ?').get(id), before);
  assert.deepEqual(h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all(), counters);
  await worker.scheduled({}, h.env);
  assert.equal((await h.submitResult(id)).status, 202);
});

test('cancelling a pending item starts terminal retention without bypassing its absolute lifetime', async t => {
  const h = await setup(t), made = await h.create(), id = made.body.item.id;
  h.advance(29 * DAY);
  assert.equal((await h.act(id, h.requester, 'cancel')).status, 200);
  h.advance(2 * DAY);
  assert.equal((await h.mine(id)).body.state, 'cancelled');
  await cleanupWorkItems(h.DB, h.now());
  assert.ok(h.DB.sqlite.prepare('SELECT id FROM work_items WHERE id = ?').get(id));
  h.advance(28 * DAY);
  assert.equal((await h.mine(id)).status, 404);
  await cleanupWorkItems(h.DB, h.now());
  assert.equal(h.DB.sqlite.prepare('SELECT id FROM work_items WHERE id = ?').get(id), undefined);

  const second = await h.create(), next = second.body.item.id;
  await h.moderate(next);
  h.advance(89 * DAY);
  assert.equal((await h.act(next, h.requester, 'cancel')).status, 200);
  h.advance(DAY);
  assert.equal((await h.mine(next)).status, 404);
  await cleanupWorkItems(h.DB, h.now());
  assert.equal(h.DB.sqlite.prepare('SELECT id FROM work_items WHERE id = ?').get(next), undefined);
});

for (const removed of ['item', 'parent']) test(`a ${removed} removed after action preflight returns 404 without action writes`, async t => {
  const h = await setup(t), id = await h.active();
  const body = h.actBody((await h.mine(id)).body, 'cancel');
  const before = h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all();
  const prepare = h.DB.prepare.bind(h.DB);
  let reads = 0;
  h.DB.prepare = sql => {
    const statement = prepare(sql);
    if (sql.startsWith('SELECT w.*')) {
      const first = statement.first;
      statement.first = async function () {
        if (++reads === 2) {
          if (removed === 'item') h.DB.sqlite.prepare('DELETE FROM work_items WHERE id = ?').run(id);
          else h.DB.sqlite.prepare('DELETE FROM proposals WHERE id = ?').run('build-the-commons');
        }
        return first.call(this);
      };
    }
    return statement;
  };
  const result = await h.send(`/work-items/${id}/actions`, 'POST', body, h.requester);
  assert.equal(result.status, 404, JSON.stringify(result.body));
  assert.equal(reads, 2);
  assert.equal(h.DB.sqlite.prepare('SELECT COUNT(*) n FROM work_item_events WHERE work_item_id = ?').get(id).n, 0);
  assert.deepEqual(h.DB.sqlite.prepare('SELECT bucket, count FROM rate_limits ORDER BY bucket').all(), before);
});
