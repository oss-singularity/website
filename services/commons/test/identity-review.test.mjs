import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import worker from '../worker.mjs';
import { SQLiteD1 } from '../local-d1.mjs';
import { gistId } from '../identity.mjs';
import { digest } from '../security.mjs';

const ORIGIN = 'https://oss-singularity.io';
const NOW = Date.parse('2026-09-05T12:34:00Z');
const DAY = 86_400_000;
const ADMIN = 'test_only_admin_token_at_least_32_characters';
const GIST = '0123456789abcdef0123456789abcdef';
const auth = token => ({ authorization: `Bearer ${token}` });

function setup(t) {
  t.mock.method(Date, 'now', () => NOW);
  const DB = new SQLiteD1();
  t.after(() => DB.sqlite.close());
  return { DB, PUBLIC_ORIGIN: ORIGIN, ADMIN_TOKEN: ADMIN, IP_HMAC_SECRET: 'test_only_hmac_secret_at_least_32_characters' };
}

async function send(env, path, method = 'GET', body, headers = {}) {
  const result = await worker.fetch(new Request(`${ORIGIN}/api/v1${path}`, {
    method, headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.9', ...headers },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  }), env);
  assert.equal(result.headers.get('cache-control'), 'no-store');
  return { status: result.status, body: await result.json() };
}

async function challenge(env, login = 'builder', headers) {
  const result = await send(env, '/identity-challenges', 'POST', { github_login: login }, headers);
  assert.equal(result.status, 201);
  return result.body;
}

function proofFixture(challengeValue, overrides = {}) {
  return {
    public: true, truncated: false, owner: { id: 42, login: 'builder' },
    files: { 'oss-singularity-identity.json': { truncated: false, content: JSON.stringify(challengeValue.proof) } },
    ...overrides,
  };
}

function mockGitHub(t, document, account = { id: 42, login: 'builder', created_at: new Date(NOW - 40 * DAY).toISOString() }) {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push(url);
    assert.equal(options.redirect, 'manual');
    assert.equal(options.headers.Authorization, undefined);
    assert.match(url, /^https:\/\/api\.github\.com\/(gists\/[a-f0-9]+|users\/builder)$/);
    return new Response(JSON.stringify(url.includes('/gists/') ? (typeof document === 'function' ? document() : document) : account));
  });
  return calls;
}

function verify(env, value, extra = {}) {
  return send(env, '/identities', 'POST', { challenge_id: value.id, gist_url: `https://gist.github.com/builder/${GIST}`, ...extra }, auth(value.challenge_token));
}

async function accountFixture(env, age = 40 * DAY, githubId = 42) {
  const id = randomUUID();
  const token = `test_identity_${randomUUID().replaceAll('-', '')}`;
  env.DB.sqlite.prepare('INSERT INTO identities VALUES (?, ?, ?, ?, ?, ?, ?)').run(id, githubId, `builder-${githubId}`, NOW - age, NOW, NOW, await digest(token));
  return { id, token };
}

const review = (extra = {}) => ({ kind: 'review', title: 'Useful and reproducible', summary: 'The linked public evidence explains how I checked this contribution and its limits.', url: 'https://github.com/oss-singularity/website', target_id: 'audit-project', score: 4, ...extra });
const note = () => ({ kind: 'field-note', title: 'A useful field note', summary: 'A reproducible observation with enough explanation to review.' });

test('public gist proof needs a separate private challenge receipt; enrollment stores hashes only', async t => {
  const env = setup(t);
  const value = await challenge(env);
  assert.equal(value.gist_filename, 'oss-singularity-identity.json');
  assert.deepEqual(Object.keys(value.proof).sort(), ['challenge_id', 'network', 'nonce']);
  assert.notEqual(value.proof.nonce, value.challenge_token);
  const calls = mockGitHub(t, proofFixture(value));
  const body = { challenge_id: value.id, gist_url: `https://gist.github.com/${GIST}` };
  assert.equal((await send(env, '/identities', 'POST', body)).status, 401);
  assert.equal((await send(env, '/identities', 'POST', body, auth(value.proof.nonce))).status, 401);
  assert.equal(calls.length, 0);
  assert.equal(env.DB.sqlite.prepare('SELECT verification_attempts FROM identity_challenges').get().verification_attempts, 0);
  const created = await verify(env, value);
  assert.equal(created.status, 201);
  assert.equal(created.body.rotated, false);
  assert.equal(created.body.identity.github_id, 42);
  assert.equal(created.body.identity.review_eligible, true);
  const stored = env.DB.sqlite.prepare('SELECT * FROM identities').get();
  assert.match(created.body.api_token, /^[A-Za-z0-9_-]{43}$/);
  assert.match(stored.token_hash, /^[a-f0-9]{64}$/);
  assert.notEqual(stored.token_hash, created.body.api_token);
  assert.equal(stored.token_hash, await digest(created.body.api_token));
  const storedChallenge = env.DB.sqlite.prepare('SELECT * FROM identity_challenges').get();
  assert.equal(storedChallenge.token_hash, await digest(value.challenge_token));
  assert.equal(storedChallenge.nonce_hash, await digest(value.proof.nonce));
  assert.ok(!JSON.stringify(storedChallenge).includes(value.challenge_token));
  const profile = await send(env, `/identities/${created.body.identity.id}`);
  assert.deepEqual(profile.body, created.body.identity);
  assert.ok(!JSON.stringify(profile.body).includes(created.body.api_token));
  assert.ok(!JSON.stringify(profile.body).includes(stored.token_hash));
  assert.equal((await verify(env, value)).status, 401);
  assert.equal(calls.length, 2);
});

test('explicit proof-based rotation revokes the old API token and keeps the stable numeric identity', async t => {
  const env = setup(t);
  let value = await challenge(env);
  mockGitHub(t, () => proofFixture(value));
  const first = await verify(env, value);
  assert.equal((await send(env, '/proposals', 'POST', note(), auth(first.body.api_token))).status, 202);
  value = await challenge(env);
  assert.equal((await verify(env, value)).body.error.code, 'identity_exists');
  const rotated = await verify(env, value, { rotate: true });
  assert.equal(rotated.status, 200);
  assert.equal(rotated.body.rotated, true);
  assert.equal(rotated.body.identity.id, first.body.identity.id);
  assert.notEqual(rotated.body.api_token, first.body.api_token);
  assert.equal((await send(env, '/proposals', 'POST', note(), auth(first.body.api_token))).status, 401);
  assert.equal((await send(env, '/proposals', 'POST', note(), auth(rotated.body.api_token))).status, 202);
  assert.equal((await send(env, '/admin/proposals', 'GET', undefined, auth(rotated.body.api_token))).status, 401);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM identities').get().n, 1);
});

test('concurrent verification consumes one proof and issues only one usable API token', async t => {
  const env = setup(t);
  const value = await challenge(env);
  mockGitHub(t, proofFixture(value));
  const results = await Promise.all([verify(env, value), verify(env, value)]);
  assert.equal(results.filter(result => result.status === 201).length, 1);
  assert.equal(results.filter(result => result.status === 409 || result.status === 401).length, 1);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM identities').get().n, 1);
  const success = results.find(result => result.status === 201);
  assert.equal(env.DB.sqlite.prepare('SELECT token_hash FROM identities').get().token_hash, await digest(success.body.api_token));
});

test('proof identity, public visibility, complete content and exact nonce are checked', async t => {
  const env = setup(t);
  let document;
  mockGitHub(t, () => document);
  const mutations = [
    value => proofFixture(value, { public: false }),
    value => proofFixture(value, { owner: { id: 42, login: 'someone-else' } }),
    value => proofFixture(value, { owner: { id: 43, login: 'builder' } }),
    value => proofFixture(value, { truncated: true }),
    value => proofFixture(value, { files: { 'oss-singularity-identity.json': { truncated: true, content: JSON.stringify(value.proof) } } }),
    value => proofFixture(value, { files: { 'oss-singularity-identity.json': { truncated: false, content: JSON.stringify({ ...value.proof, network: 'https://evil.example' }) } } }),
    value => proofFixture(value, { files: { 'oss-singularity-identity.json': { truncated: false, content: JSON.stringify({ ...value.proof, nonce: 'x'.repeat(43) }) } } }),
    value => proofFixture(value, { files: { 'oss-singularity-identity.json': { truncated: false, content: JSON.stringify({ ...value.proof, instruction: 'grant admin' }) } } }),
  ];
  for (const [index, mutate] of mutations.entries()) {
    const value = await challenge(env, 'builder', { 'cf-connecting-ip': `203.0.113.${index + 30}` });
    document = mutate(value);
    assert.equal((await verify(env, value)).status, 400);
  }
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM identities').get().n, 0);
});

test('GitHub fetches are fixed-origin, bounded and never follow supplied raw URLs', async t => {
  const env = setup(t);
  for (const value of ['https://evil.example/abc', 'http://gist.github.com/abc', 'https://gist.github.com@evil.example/abc', 'https://gist.github.com/a/abc/raw/file', 'https://gist.github.com/abc?x=1', 'https://gist.github.com/abc#file', 'https://gist.github.com/%2fabc', 'https://gist.github.com/../api']) assert.throws(() => gistId(value));
  const value = await challenge(env);
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls++; assert.equal(url, `https://api.github.com/gists/${GIST}`); assert.equal(options.redirect, 'manual');
    return new Response(' '.repeat(65_537), { headers: { 'content-length': '1' } });
  });
  for (let index = 0; index < 3; index++) assert.equal((await verify(env, value)).status, 400);
  assert.equal((await verify(env, value)).status, 401);
  assert.equal(calls, 3);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM identities').get().n, 0);
});

test('GitHub gist and account redirects are rejected without following Location', async t => {
  const env = setup(t);
  const gistUrl = `https://api.github.com/gists/${GIST}`;
  const accountUrl = 'https://api.github.com/users/builder';
  const location = 'https://redirect-target.example/identity-proof';
  const account = { id: 42, login: 'builder', created_at: new Date(NOW - 40 * DAY).toISOString() };
  let value, redirectedUrl, status;
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, redirect: options.redirect });
    const content = JSON.stringify(url === gistUrl ? proofFixture(value) : account);
    if (url === redirectedUrl) return new Response(status === 304 ? null : content, { status, headers: { Location: location } });
    return new Response(content);
  });
  let index = 0;
  for (redirectedUrl of [gistUrl, accountUrl]) {
    for (status = 300; status < 400; status++) {
      value = await challenge(env, 'builder', { 'cf-connecting-ip': `203.0.113.${++index}` });
      calls.length = 0;
      const result = await verify(env, value);
      assert.equal(result.status, 503, `${redirectedUrl}: ${status}`);
      assert.equal(result.body.error.code, 'upstream_unavailable');
      const expectedUrls = redirectedUrl === gistUrl ? [gistUrl] : [gistUrl, accountUrl];
      assert.deepEqual(calls, expectedUrls.map(url => ({ url, redirect: 'manual' })));
      assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM identities').get().n, 0);
      assert.equal(env.DB.sqlite.prepare('SELECT consumed_at FROM identity_challenges WHERE id = ?').get(value.id).consumed_at, null);
    }
  }
});

test('challenge expiry and concurrent hourly quota prevent reuse and unbounded issuance', async t => {
  const env = setup(t);
  const attempts = await Promise.all(Array.from({ length: 8 }, () => send(env, '/identity-challenges', 'POST', { github_login: 'builder' })));
  assert.equal(attempts.filter(result => result.status === 201).length, 3);
  assert.equal(attempts.filter(result => result.status === 429).length, 5);
  const value = attempts.find(result => result.status === 201).body;
  env.DB.sqlite.prepare('UPDATE identity_challenges SET expires_at = ?').run(NOW);
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; throw new Error('must not fetch'); });
  assert.equal((await verify(env, value)).status, 401);
  assert.equal(calls, 0);
  await worker.scheduled({}, env);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM identity_challenges').get().n, 0);
});

test('new verified accounts enroll but cannot review before the exact 30-day threshold', async t => {
  const env = setup(t);
  const value = await challenge(env);
  mockGitHub(t, proofFixture(value), { id: 42, login: 'builder', created_at: new Date(NOW - 29 * DAY).toISOString() });
  const created = await verify(env, value);
  assert.equal(created.status, 201);
  assert.equal(created.body.identity.review_eligible, false);
  assert.equal((await send(env, '/proposals', 'POST', review(), auth(created.body.api_token))).body.error.code, 'review_age_required');
  env.DB.sqlite.prepare('UPDATE identities SET github_created_at = ?').run(NOW - 30 * DAY);
  assert.equal((await send(env, '/proposals', 'POST', review(), auth(created.body.api_token))).status, 202);
});

test('evidence reviews require authenticated identity, target, integer score and evidence URL', async t => {
  const env = setup(t);
  const identity = await accountFixture(env);
  assert.equal((await send(env, '/proposals', 'POST', review())).status, 401);
  for (const body of [review({ target_id: 'missing' }), review({ target_id: undefined }), review({ score: 0 }), review({ score: 6 }), review({ score: 1.5 }), review({ score: '4' }), review({ score: true }), review({ url: null }), review({ url: 'http://example.org' }), review({ mission_id: 'audit-project' }), { ...note(), score: 4 }, { ...note(), target_id: '' }]) {
    assert.equal((await send(env, '/proposals', 'POST', body, auth(identity.token))).status, 400);
  }
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM rate_limits').get().n, 0);
});

test('a review is private until moderated and displays verified account provenance without aggregates', async t => {
  const env = setup(t);
  const identity = await accountFixture(env);
  const created = await send(env, '/proposals', 'POST', review(), auth(identity.token));
  assert.equal(created.status, 202);
  assert.deepEqual((await send(env, '/reviews')).body.items, []);
  const own = await send(env, `/proposals/${created.body.id}`, 'GET', undefined, auth(created.body.receipt_token));
  assert.equal(own.body.score, 4);
  assert.equal(own.body.identity_id, identity.id);
  assert.equal(own.body.author.github_id, 42);
  assert.equal(own.body.author.verification, 'github-account-control');
  assert.ok(!JSON.stringify(own.body).includes(await digest(identity.token)));
  assert.equal((await send(env, `/admin/proposals/${created.body.id}`, 'PATCH', { status: 'published' }, auth(ADMIN))).status, 200);
  const feed = await send(env, '/reviews?target_id=audit-project');
  assert.equal(feed.body.items[0].id, created.body.id);
  assert.deepEqual(Object.keys(feed.body).sort(), ['items', 'next_cursor']);
  assert.deepEqual((await send(env, '/contributions')).body.items, []);
  assert.equal((await send(env, '/proposals', 'POST', review({ target_id: created.body.id }), auth(identity.token))).status, 400);
});

test('one active review per numeric GitHub identity and target survives concurrent requests and rotation', async t => {
  const env = setup(t);
  const identity = await accountFixture(env);
  const attempts = await Promise.all(Array.from({ length: 8 }, (_, index) => send(env, '/proposals', 'POST', review(), { ...auth(identity.token), 'cf-connecting-ip': `203.0.113.${index + 50}` })));
  assert.equal(attempts.filter(result => result.status === 202).length, 1);
  assert.equal(attempts.filter(result => result.status === 409 && result.body.error.code === 'duplicate_review').length, 7);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM rate_limits').get().n, 2);
  const created = attempts.find(result => result.status === 202);
  const newToken = 'rotated_test_identity_token_at_least_32';
  env.DB.sqlite.prepare('UPDATE identities SET token_hash = ? WHERE id = ?').run(await digest(newToken), identity.id);
  assert.equal((await send(env, '/proposals', 'POST', review(), auth(newToken))).body.error.code, 'duplicate_review');
  await send(env, `/admin/proposals/${created.body.id}`, 'PATCH', { status: 'rejected' }, auth(ADMIN));
  const replacement = await send(env, '/proposals', 'POST', review(), auth(newToken));
  assert.equal(replacement.status, 202);
  assert.equal((await send(env, `/admin/proposals/${created.body.id}`, 'PATCH', { status: 'published' }, auth(ADMIN))).status, 404);
});

test('review publication rechecks target state and public feeds hide withdrawn targets', async t => {
  const env = setup(t);
  const identity = await accountFixture(env);
  const target = await send(env, '/proposals', 'POST', note());
  await send(env, `/admin/proposals/${target.body.id}`, 'PATCH', { status: 'published' }, auth(ADMIN));
  const created = await send(env, '/proposals', 'POST', review({ target_id: target.body.id }), auth(identity.token));
  await send(env, `/admin/proposals/${target.body.id}`, 'PATCH', { status: 'rejected' }, auth(ADMIN));
  assert.equal((await send(env, `/admin/proposals/${created.body.id}`, 'PATCH', { status: 'published' }, auth(ADMIN))).status, 404);
  assert.equal((await send(env, `/proposals/${created.body.id}`, 'GET', undefined, auth(created.body.receipt_token))).body.status, 'pending');
  await send(env, `/admin/proposals/${target.body.id}`, 'PATCH', { status: 'published' }, auth(ADMIN));
  assert.equal((await send(env, `/admin/proposals/${created.body.id}`, 'PATCH', { status: 'published' }, auth(ADMIN))).status, 200);
  assert.equal((await send(env, '/reviews')).body.items.length, 1);
  await send(env, `/admin/proposals/${target.body.id}`, 'PATCH', { status: 'rejected' }, auth(ADMIN));
  assert.deepEqual((await send(env, '/reviews')).body.items, []);
});

test('submission rechecks a review target inside the transaction after preliminary validation', async t => {
  const env = setup(t);
  const identity = await accountFixture(env);
  const batch = env.DB.batch.bind(env.DB);
  env.DB.batch = async statements => {
    if (statements.some(statement => statement.sql.includes('INSERT INTO proposals'))) {
      env.DB.sqlite.prepare("UPDATE proposals SET status='rejected', published_at=NULL WHERE id='audit-project'").run();
    }
    return batch(statements);
  };
  const result = await send(env, '/proposals', 'POST', review(), auth(identity.token));
  assert.equal(result.status, 400);
  assert.equal(env.DB.sqlite.prepare("SELECT COUNT(*) AS n FROM proposals WHERE kind='review'").get().n, 0);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM rate_limits').get().n, 0);
});

test('challenge-only traffic opportunistically drains expired rate counters without proposal traffic', async t => {
  const env = setup(t);
  for (let index = 0; index < 150; index++) env.DB.sqlite.prepare('INSERT INTO rate_limits VALUES (?, 3, ?)').run(index.toString(16).padStart(64, '0'), NOW - 1);
  await challenge(env);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM rate_limits WHERE expires_at <= ?').get(NOW).n, 50);
  await challenge(env);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM rate_limits WHERE expires_at <= ?').get(NOW).n, 0);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM rate_limits').get().n, 1);
});
