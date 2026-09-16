import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../worker.mjs';
import { SQLiteD1 } from '../local-d1.mjs';

const NOW = Date.parse('2026-09-16T12:00:00Z');

let currentLogin = '';
let currentProof = null;
let currentGithubId = 100;

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
  assert.equal(enrollment.identity !== undefined, true, JSON.stringify(enrollment).slice(0, 200));
  return enrollment.api_token;
}

const criteria = ['Two independent verifications of the same synthetic bytes.'];

test('the coordination slice joins a project, dependent milestones and one bound commitment', async t => {
  const env = await environment(t);
  const aria = await enroll(env, 'aria');
  const kofi = await enroll(env, 'kofi');

  const created = await call(env, 'POST', '/api/v1/projects', {
    mission_id: 'build-the-commons', title: 'Verification toolkit',
    purpose: 'Make artifact verification usable for independent reviewers.',
  }, aria);
  assert.equal(created.status, 201, JSON.stringify(created.body));
  assert.equal(created.body.status, 'open');
  assert.equal(created.body.scope_version, 1);
  const projectId = created.body.id;

  const verifier = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones`, {
    title: 'Digest verifier', purpose: 'Verify raw-file digests for independent reviewers.',
    expected_artifact: 'A delivery manifest with raw-file digest instructions.',
    acceptance: criteria, expected_version: created.body.version,
  }, aria);
  assert.equal(verifier.status, 201, JSON.stringify(verifier.body ?? ''));
  assert.equal(verifier.body.blocked, false);
  const explainer = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones`, {
    title: 'CID explainer', purpose: 'Explain content identifiers versus raw digests.',
    expected_artifact: 'A short explained walkthrough of CIDs.',
    acceptance: criteria, depends_on: [verifier.body.id], expected_version: created.body.version + 1,
  }, aria);
  assert.equal(explainer.status, 201, JSON.stringify(explainer.body ?? ''));
  assert.equal(explainer.body.blocked, true, 'the dependency gate blocks the dependent milestone');

  const blocked = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments`,
    { milestone_id: explainer.body.id, terms: 'volunteer' }, kofi);
  assert.equal(blocked.status, 409, JSON.stringify(blocked.body ?? ''));
  assert.equal(blocked.body.error.code, 'dependency_blocked');

  const offer = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments`,
    { milestone_id: verifier.body.id, terms: 'volunteer' }, kofi);
  assert.equal(offer.status, 201, JSON.stringify(offer.body));
  assert.equal(offer.body.status, 'offered');

  // An offered commitment is visible to its two participants only.
  const asAria = await call(env, `GET`, `/api/v1/projects/${projectId}`, undefined, aria);
  assert.equal(asAria.body.commitments.length, 1);
  assert.equal(asAria.body.commitments[0].contributor.github_login, 'kofi');
  const asPublic = await call(env, 'GET', `/api/v1/projects/${projectId}`);
  assert.equal(asPublic.body.commitments.length, 0);

  const confirmed = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments/${offer.body.id}/actions`,
    { action: 'confirm' }, aria);
  assert.equal(confirmed.status, 200, JSON.stringify(confirmed.body ?? ''));
  assert.equal(confirmed.body.status, 'confirmed');
  assert.equal(confirmed.body.scope_version, verifier.body.scope_version, 'the commitment binds the milestone scope version');

  // Confirming a milestone completes its gate: the dependent milestone unblocks.
  const done = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${verifier.body.id}/actions`,
    { action: 'complete', expected_version: verifier.body.version }, aria);
  assert.equal(done.status, 200, JSON.stringify(done.body ?? ''));
  assert.equal(done.body.status, 'done');
  const unblocked = await call(env, 'GET', `/api/v1/projects/${projectId}`);
  assert.equal(unblocked.body.milestones.find(m => m.id === explainer.body.id).blocked, false);

  const ended = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments/${offer.body.id}/actions`,
    { action: 'end' }, kofi);
  assert.equal(ended.status, 200, JSON.stringify(ended.body ?? ''));
  assert.equal(ended.body.status, 'ended');

  const exportView = await call(env, 'GET', `/api/v1/projects/${projectId}/export`);
  assert.equal(exportView.status, 200, JSON.stringify(exportView.body ?? ''));
  assert.equal(exportView.body.kind, 'oss-project-export');
  assert.equal(exportView.body.schema_version, 1);
  assert.equal(exportView.body.project.id, projectId);
  assert.equal(exportView.body.milestones.length, 2);
  assert.equal(exportView.body.commitments.length, 1);
  assert.equal(exportView.body.commitments[0].status, 'ended');
  assert.deepEqual(exportView.body.milestones.find(m => m.id === explainer.body.id).depends_on, [verifier.body.id]);
});

test('milestone authority, version conflicts and dependency structure are enforced', async t => {
  const env = await environment(t);
  const aria = await enroll(env, 'aria');
  const kofi = await enroll(env, 'kofi');
  const project = await call(env, 'POST', '/api/v1/projects', {
    mission_id: 'build-the-commons', title: 'Second toolkit',
    purpose: 'Another bounded coordination project under the shared mission.',
  }, aria);
  const projectId = project.body.id;

  const stranger = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones`, {
    title: 'Uninvited milestone', purpose: 'A milestone nobody authorized this account to add.',
    expected_artifact: 'An artifact nobody requested from this account.',
    acceptance: criteria, expected_version: project.body.version,
  }, kofi);
  assert.equal(stranger.status, 403, JSON.stringify(stranger.body ?? ''));
  assert.equal(stranger.body.error.code, 'forbidden');

  const stale = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones`, {
    title: 'Stale milestone', purpose: 'A milestone added against a stale project version.',
    expected_artifact: 'An artifact described against the stale project version.',
    acceptance: criteria, expected_version: 1,
  }, aria);
  assert.equal(stale.status, 201, 'the first add matches version 1');
  const conflicting = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones`, {
    title: 'Conflicting milestone', purpose: 'A milestone added against the already stale version.',
    expected_artifact: 'An artifact described against the stale project version.',
    acceptance: criteria, expected_version: 1,
  }, aria);
  assert.equal(conflicting.status, 409, JSON.stringify(conflicting.body ?? ''));
  assert.equal(conflicting.body.error.code, 'version_conflict');

  // Dependencies reference pre-existing milestones only: edges point backward,
  // so the graph is acyclic by construction, and unknown or foreign
  // dependencies refuse.
  const foreign = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones`, {
    title: 'Foreign dependency', purpose: 'A milestone that depends on an unknown identifier.',
    expected_artifact: 'An artifact described against the unknown dependency.',
    acceptance: criteria, depends_on: ['ffffffff-ffff-4fff-8fff-ffffffffffff'], expected_version: 3,
  }, aria);
  assert.equal(foreign.status, 400, JSON.stringify(foreign.body ?? ''));

  const badAcceptance = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones`, {
    title: 'Unbounded milestone', purpose: 'A milestone without acceptance criteria.',
    expected_artifact: 'An artifact without criteria cannot be accepted.',
    acceptance: [], expected_version: 3,
  }, aria);
  assert.equal(badAcceptance.status, 400, JSON.stringify(badAcceptance.body ?? ''));
});

test('closing a project cancels open milestones and nonterminal commitments', async t => {
  const env = await environment(t);
  const aria = await enroll(env, 'aria');
  const kofi = await enroll(env, 'kofi');
  const project = await call(env, 'POST', '/api/v1/projects', {
    mission_id: 'build-the-commons', title: 'Cancelled toolkit',
    purpose: 'A project the coordinator cancels before completion.',
  }, aria);
  const projectId = project.body.id;
  const milestone = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones`, {
    title: 'Doomed milestone', purpose: 'A milestone the project cancellation ends.',
    expected_artifact: 'An artifact the cancelled project no longer expects.',
    acceptance: criteria, expected_version: project.body.version,
  }, aria);
  const offer = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments`,
    { milestone_id: milestone.body.id, terms: 'volunteer' }, kofi);
  assert.equal(offer.status, 201, JSON.stringify(offer.body));

  const cancelled = await call(env, 'POST', `/api/v1/projects/${projectId}/actions`,
    { action: 'cancel', expected_version: project.body.version + 1 }, aria);
  assert.equal(cancelled.status, 200, JSON.stringify(cancelled.body ?? ''));
  assert.equal(cancelled.body.status, 'cancelled');

  // The coordinator keeps a private view of the cancelled project; the
  // public views do not.
  const asAria = await call(env, 'GET', `/api/v1/projects/${projectId}`, undefined, aria);
  assert.equal(asAria.status, 200, JSON.stringify(asAria.body ?? ''));
  assert.ok(asAria.body.milestones.every(m => m.status === 'cancelled'));
  assert.ok(asAria.body.commitments.every(c => c.status === 'cancelled'));

  const withdrawn = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments/${offer.body.id}/actions`,
    { action: 'end' }, kofi);
  assert.equal(withdrawn.status, 409, JSON.stringify(withdrawn.body ?? ''));

  const again = await call(env, 'GET', `/api/v1/projects/${projectId}`);
  assert.equal(again.status, 404, 'cancelled projects leave the public views');
});

test('unknown projects and missing credentials behave like the rest of the API', async t => {
  const env = await environment(t);
  const missing = await call(env, 'GET', '/api/v1/projects/ffffffff-ffff-4fff-8fff-ffffffffffff');
  assert.equal(missing.status, 404, JSON.stringify(missing.body ?? ''));
  const unauthorized = await call(env, 'POST', '/api/v1/projects', {
    mission_id: 'build-the-commons', title: 'Anonymous project',
    purpose: 'A project cannot be created without an authenticated coordinator.',
  });
  assert.equal(unauthorized.status, 401, JSON.stringify(unauthorized.body ?? ''));
});
