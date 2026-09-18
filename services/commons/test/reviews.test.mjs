import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../worker.mjs';
import { SQLiteD1 } from '../local-d1.mjs';

const NOW = Date.parse('2026-09-17T00:00:00Z');
let currentLogin = '';
let currentProof = null;
let currentGithubId = 200;

async function environment(t) {
  const DB = new SQLiteD1();
  t.after(() => DB.sqlite.close());
  const env = { DB, PUBLIC_ORIGIN: 'https://oss-singularity.io', IP_HMAC_SECRET: 'test_only_hmac_secret_longer_than_32', ADMIN_TOKEN: 'test_only_admin_secret_longer_than_32' };
  t.mock.method(Date, 'now', () => NOW);
  t.mock.method(globalThis, 'fetch', async url => new Response(JSON.stringify(
    url.includes('/gists/')
      ? { public: true, truncated: false, owner: { id: currentGithubId, login: currentLogin }, files: { 'oss-singularity-identity.json': { truncated: false, content: JSON.stringify(currentProof) } } }
      : { id: currentGithubId, login: currentLogin, created_at: '2020-01-01T00:00:00Z' })));
  return env;
}

async function call(env, method, path, body, token) {
  const result = await worker.fetch(new Request(env.PUBLIC_ORIGIN + path, {
    method,
    headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.87',
      ...(token ? { authorization: `Bearer ${token}` } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  }), env);
  return { status: result.status, body: await result.json() };
}

async function enroll(env, login) {
  currentLogin = login;
  currentGithubId += 1;
  const challenge = (await call(env, 'POST', '/api/v1/identity-challenges', { github_login: login })).body;
  currentProof = challenge.proof;
  const enrollment = (await call(env, 'POST', '/api/v1/identities',
    { challenge_id: challenge.id, gist_url: 'https://gist.github.com/abcdef0123456789' }, challenge.challenge_token)).body;
  assert.equal(enrollment.identity !== undefined, true);
  return enrollment.api_token;
}

const criteria = ['The manifest names its acceptance decision trail.'];
const digest = 'c'.repeat(64);
const ARTIFACT = 'https://oss-singularity.io/data/synthetic-delivery-artifact.json';

async function reviewableProject(env, coordinator, contributor) {
  const project = await call(env, 'POST', '/api/v1/projects', {
    mission_id: 'build-the-commons', title: 'Acceptance pilot',
    purpose: 'Close the loop from delivery over revision to explicit acceptance.',
  }, coordinator);
  const milestone = await call(env, 'POST', `/api/v1/projects/${project.body.id}/milestones`, {
    title: 'Reviewed slice', purpose: 'One milestone walked through the full review loop.',
    expected_artifact: 'A delivery whose acceptance binds the exact revision.',
    acceptance: criteria, expected_version: project.body.version,
  }, coordinator);
  const offer = await call(env, 'POST', `/api/v1/projects/${project.body.id}/commitments`,
    { milestone_id: milestone.body.id, terms: 'volunteer' }, contributor);
  await call(env, 'POST', `/api/v1/projects/${project.body.id}/commitments/${offer.body.id}/actions`, { action: 'confirm' }, coordinator);
  return { projectId: project.body.id, milestoneId: milestone.body.id, milestoneVersion: milestone.body.version };
}

const deliver = (env, ids, contributor, revision, summary) => call(env, 'POST',
  `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/deliveries`, {
    summary, artifact_url: ARTIFACT, artifact_media_type: 'application/json',
    artifact_size_bytes: 591, integrity_digest: digest, expected_version: revision,
  }, contributor);

test('delivery, revision request, new delivery and acceptance close the loop with a portable trail', async t => {
  const env = await environment(t);
  const aria = await enroll(env, 'aria');
  const kofi = await enroll(env, 'kofi');
  const ids = await reviewableProject(env, aria, kofi);

  await deliver(env, ids, kofi, 2, 'First revision of the reviewed artifact bytes.');
  const requested = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 1, decision: 'revision_requested',
    note: 'The manifest must also state who retains the artifact and for how long.',
    expected_version: 1,
  }, aria);
  assert.equal(requested.status, 201, JSON.stringify(requested.body).slice(0, 250));
  assert.equal(requested.body.decision, 'revision_requested');
  assert.equal(requested.body.reviewer.github_login, 'aria');

  await deliver(env, ids, kofi, 4, 'Second revision with the retention statement attached.');
  const accepted = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 2, decision: 'accept',
    note: 'Revision two carries the retention statement; acceptance binds exactly this revision.',
    expected_version: 1,
  }, aria);
  assert.equal(accepted.status, 201, JSON.stringify(accepted.body).slice(0, 250));

  const detail = await call(env, 'GET', `/api/v1/projects/${ids.projectId}`);
  assert.equal(detail.body.milestones.find(m => m.id === ids.milestoneId).status, 'done', 'acceptance completes the milestone');
  assert.equal(detail.body.commitments.find(c => c.milestone_id === ids.milestoneId).status, 'completed', 'a confirmed commitment completes with its accepted milestone');

  const trail = await call(env, 'GET', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`);
  assert.equal(trail.status, 200);
  assert.equal(trail.body.items.length, 2);
  assert.deepEqual(trail.body.items.map(item => [item.delivery_revision, item.decision]).sort(), [[1, 'revision_requested'], [2, 'accept']]);

  const manifest = await call(env, 'GET', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/deliveries/2`);
  assert.equal(manifest.body.review.decision, 'accept', 'the manifest carries its own acceptance trail');
  const first = await call(env, 'GET', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/deliveries/1`);
  assert.equal(first.body.review.decision, 'revision_requested');
});

test('stale, duplicate, unauthorized and closed-state review decisions have defined outcomes', async t => {
  const env = await environment(t);
  const aria = await enroll(env, 'aria');
  const kofi = await enroll(env, 'kofi');
  const mallory = await enroll(env, 'mallory');
  const ids = await reviewableProject(env, aria, kofi);

  const unauthorized = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 1, decision: 'accept', expected_version: 3,
  }, kofi);
  assert.equal(unauthorized.status, 403, JSON.stringify(unauthorized.body).slice(0, 200));

  const missing = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 7, decision: 'accept', expected_version: 3,
  }, aria);
  assert.equal(missing.status, 404);

  await deliver(env, ids, kofi, 2, 'First revision the reviewer walks through the guards.');
  await deliver(env, ids, kofi, 3, 'Second revision making the first one stale.');

  const stale = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 1, decision: 'accept', note: 'Accepting a revision that is no longer current.', expected_version: 1,
  }, aria);
  assert.equal(stale.status, 409);
  assert.equal(stale.body.error.code, 'stale_revision');

  const noteless = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 2, decision: 'revision_requested', expected_version: 1,
  }, aria);
  assert.equal(noteless.status, 400);

  const accepted = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 2, decision: 'accept', expected_version: 1,
  }, aria);
  assert.equal(accepted.status, 201);
  const duplicate = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 2, decision: 'accept', expected_version: 2,
  }, aria);
  assert.equal(duplicate.status, 409);
  assert.equal(duplicate.body.error.code, 'duplicate_decision');

  const closed = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 2, decision: 'revision_requested', note: 'A review against an already accepted milestone.', expected_version: 2,
  }, aria);
  assert.equal(closed.status, 409);
  assert.equal(closed.body.error.code, 'milestone_closed');

  const stranger = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 2, decision: 'accept', expected_version: 2,
  }, mallory);
  assert.equal(stranger.status, 403);

  const discovery = await call(env, 'GET', '/api/v1');
  assert.equal(discovery.body.endpoints.project_milestone_reviews, '/api/v1/projects/{id}/milestones/{milestone_id}/reviews');
});
