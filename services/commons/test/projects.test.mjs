import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../worker.mjs';
import { SQLiteD1 } from '../local-d1.mjs';
import { digest } from '../security.mjs';

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

test('commitment metadata publishes only through a confirmed history', async t => {
  const env = await environment(t);
  const aria = await enroll(env, 'aria');
  const kofi = await enroll(env, 'kofi');
  const lex = await enroll(env, 'lex');
  // A fourth enrollment would exceed the challenge rate limit, so the
  // unrelated viewer identity is inserted directly like the fixtures are.
  const strangerToken = `test_identity_${crypto.randomUUID().replaceAll('-', '')}`;
  env.DB.sqlite.prepare('INSERT INTO identities VALUES (?, ?, ?, ?, ?, ?, ?)')
    .run(crypto.randomUUID(), 990, 'stranger', Date.now() - 40 * 86_400_000, Date.now(), Date.now(), await digest(strangerToken));
  const project = await call(env, 'POST', '/api/v1/projects', {
    mission_id: 'build-the-commons', title: 'Visibility pilot',
    purpose: 'Keep never-confirmed commitment metadata with its participants.',
  }, aria);
  const projectId = project.body.id;
  const milestone = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones`, {
    title: 'Bound slice', purpose: 'One milestone carrying several commitment states.',
    expected_artifact: 'An artifact used only to drive commitment states.',
    acceptance: criteria, expected_version: project.body.version,
  }, aria);
  assert.equal(milestone.status, 201, JSON.stringify(milestone.body ?? ''));

  // A confirmed commitment is public; declined and withdrawn ones never were
  // confirmed and stay with their two bound participants.
  const kept = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments`,
    { milestone_id: milestone.body.id, terms: 'volunteer' }, kofi);
  await call(env, 'POST', `/api/v1/projects/${projectId}/commitments/${kept.body.id}/actions`, { action: 'confirm' }, aria);
  const declined = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments`,
    { milestone_id: milestone.body.id, terms: 'volunteer' }, lex);
  await call(env, 'POST', `/api/v1/projects/${projectId}/commitments/${declined.body.id}/actions`, { action: 'decline' }, aria);
  const withdrawn = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments`,
    { milestone_id: milestone.body.id, terms: 'volunteer' }, lex);
  await call(env, 'POST', `/api/v1/projects/${projectId}/commitments/${withdrawn.body.id}/actions`, { action: 'withdraw' }, lex);

  const visibleIds = async token => new Set((await call(env, 'GET', `/api/v1/projects/${projectId}`, undefined, token)).body.commitments.map(c => c.id));
  assert.deepEqual([...await visibleIds()], [kept.body.id], 'anonymous reads see only the confirmed commitment');
  assert.deepEqual([...await visibleIds(strangerToken)], [kept.body.id],
    'an unrelated authenticated identity sees the same public set');
  assert.ok((await visibleIds(lex)).has(declined.body.id), 'the bound contributor still sees their own declined commitment');
  assert.ok((await visibleIds(aria)).has(withdrawn.body.id), 'the bound coordinator still sees the withdrawn commitment');
  const exportView = await call(env, 'GET', `/api/v1/projects/${projectId}/export`);
  assert.deepEqual(exportView.body.commitments.map(c => c.id), [kept.body.id],
    'the export carries the same public commitment set');

  // A cancelled commitment is conservatively private even when it was
  // confirmed before the project was cancelled: the row alone cannot prove
  // that history, and no heuristic reconstruction exists.
  const cancelledProject = await call(env, 'POST', '/api/v1/projects', {
    mission_id: 'build-the-commons', title: 'Cancelled visibility',
    purpose: 'A project whose confirmed commitments end cancelled, not public.',
  }, aria);
  const cancelledMilestone = await call(env, 'POST', `/api/v1/projects/${cancelledProject.body.id}/milestones`, {
    title: 'Doomed slice', purpose: 'A milestone whose commitment ends cancelled.',
    expected_artifact: 'An artifact the cancelled project never accepts.',
    acceptance: criteria, expected_version: cancelledProject.body.version,
  }, aria);
  const confirmed = await call(env, 'POST', `/api/v1/projects/${cancelledProject.body.id}/commitments`,
    { milestone_id: cancelledMilestone.body.id, terms: 'volunteer' }, kofi);
  await call(env, 'POST', `/api/v1/projects/${cancelledProject.body.id}/commitments/${confirmed.body.id}/actions`, { action: 'confirm' }, aria);
  const version = (await call(env, 'GET', `/api/v1/projects/${cancelledProject.body.id}`)).body.version;
  await call(env, 'POST', `/api/v1/projects/${cancelledProject.body.id}/actions`,
    { action: 'cancel', expected_version: version }, aria);
  assert.equal((await call(env, 'GET', `/api/v1/projects/${cancelledProject.body.id}`)).status, 404,
    'the cancelled project itself leaves the public reads');
  assert.equal((await call(env, 'GET', `/api/v1/projects/${cancelledProject.body.id}`, undefined, kofi)).status, 404,
    'the coordinator exception does not extend to the bound contributor');
  const asCoordinator = await call(env, 'GET', `/api/v1/projects/${cancelledProject.body.id}`, undefined, aria);
  assert.equal(asCoordinator.status, 200, JSON.stringify(asCoordinator.body ?? ''));
  assert.ok(asCoordinator.body.commitments.some(c => c.id === confirmed.body.id && c.status === 'cancelled'),
    'the bound coordinator keeps the cancelled commitment in the private view');
});

test('the activity coordination block mirrors public coordination state exactly', async t => {
  const env = await environment(t);
  const aria = await enroll(env, 'aria');
  const kofi = await enroll(env, 'kofi');
  const digest = 'c'.repeat(64);

  const empty = await call(env, 'GET', '/api/v1/activity');
  assert.equal(empty.status, 200);
  assert.deepEqual(empty.body.coordination, {
    projects_total: 0, projects_open: 0, projects_closed: 0, milestones_open: 0, milestones_done: 0,
    commitments_confirmed: 0, commitments_completed: 0, deliveries_total: 0,
  });

  const project = await call(env, 'POST', '/api/v1/projects', {
    mission_id: 'build-the-commons', title: 'Counter walkthrough',
    purpose: 'Walk every public coordination counter through one honest loop.',
  }, aria);
  assert.equal(project.status, 201, JSON.stringify(project.body).slice(0, 200));
  const projectId = project.body.id;
  const milestone = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones`, {
    title: 'Counter milestone', purpose: 'Carry the counters through a full review loop.',
    expected_artifact: 'A walkthrough artifact accepted after one revision cycle.',
    acceptance: criteria, expected_version: project.body.version,
  }, aria);
  assert.equal(milestone.status, 201, JSON.stringify(milestone.body ?? '').slice(0, 200));

  const offer = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments`,
    { milestone_id: milestone.body.id, terms: 'volunteer' }, kofi);
  assert.equal(offer.status, 201, JSON.stringify(offer.body).slice(0, 200));
  const confirmed = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments/${offer.body.id}/actions`,
    { action: 'confirm' }, aria);
  assert.equal(confirmed.status, 200, JSON.stringify(confirmed.body ?? '').slice(0, 200));

  // A second, still-private offer must not move any public counter.
  const lex = await enroll(env, 'lex');
  const privateOffer = await call(env, 'POST', `/api/v1/projects/${projectId}/commitments`,
    { milestone_id: milestone.body.id, terms: 'volunteer' }, lex);
  assert.equal(privateOffer.status, 201, JSON.stringify(privateOffer.body).slice(0, 200));

  let snapshot = await call(env, 'GET', '/api/v1/activity');
  assert.deepEqual(snapshot.body.coordination, {
    projects_total: 1, projects_open: 1, projects_closed: 0, milestones_open: 1, milestones_done: 0,
    commitments_confirmed: 1, commitments_completed: 0, deliveries_total: 0,
  });

  // A revision cycle: first delivery is returned, the second is accepted.
  const first = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestone.body.id}/deliveries`, {
    summary: 'First revision before the retention statement was attached.',
    artifact_url: 'https://oss-singularity.io/data/synthetic-delivery-artifact.json',
    artifact_media_type: 'text/plain', artifact_size_bytes: 512,
    integrity_digest: digest, expected_version: project.body.version + 1,
  }, kofi);
  assert.equal(first.status, 201, JSON.stringify(first.body ?? '').slice(0, 200));
  const requested = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestone.body.id}/reviews`, {
    delivery_revision: 1, decision: 'revision_requested',
    note: 'Attach the declared retention statement before acceptance.',
    expected_version: 1,
  }, aria);
  assert.equal(requested.status, 201, JSON.stringify(requested.body ?? '').slice(0, 200));
  const second = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestone.body.id}/deliveries`, {
    summary: 'Second revision carrying the declared retention statement.',
    artifact_url: 'https://oss-singularity.io/data/synthetic-delivery-artifact.json',
    artifact_media_type: 'text/plain', artifact_size_bytes: 512,
    integrity_digest: digest, expected_version: project.body.version + 3,
  }, kofi);
  assert.equal(second.status, 201, JSON.stringify(second.body ?? '').slice(0, 200));
  const accepted = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestone.body.id}/reviews`, {
    delivery_revision: 2, decision: 'accept',
    note: 'Revision two carries the retention statement; acceptance binds it.',
    expected_version: 1,
  }, aria);
  assert.equal(accepted.status, 201, JSON.stringify(accepted.body ?? '').slice(0, 200));

  snapshot = await call(env, 'GET', '/api/v1/activity');
  assert.deepEqual(snapshot.body.coordination, {
    projects_total: 1, projects_open: 1, projects_closed: 0, milestones_open: 0, milestones_done: 1,
    commitments_confirmed: 0, commitments_completed: 1, deliveries_total: 2,
  });

  // A closed project stays public and counts as closed.
  const secondProject = await call(env, 'POST', '/api/v1/projects', {
    mission_id: 'build-the-commons', title: 'Closed counter pilot',
    purpose: 'A project the coordinator closes without a milestone.',
  }, aria);
  assert.equal(secondProject.status, 201, JSON.stringify(secondProject.body).slice(0, 200));
  const closed = await call(env, 'POST', `/api/v1/projects/${secondProject.body.id}/actions`,
    { action: 'close', expected_version: secondProject.body.version }, aria);
  assert.equal(closed.status, 200, JSON.stringify(closed.body ?? '').slice(0, 200));
  snapshot = await call(env, 'GET', '/api/v1/activity');
  assert.deepEqual(snapshot.body.coordination, {
    projects_total: 2, projects_open: 1, projects_closed: 1, milestones_open: 0, milestones_done: 1,
    commitments_confirmed: 0, commitments_completed: 1, deliveries_total: 2,
  });

  // A project whose mission is withdrawn leaves the public counters entirely.
  const retiredMission = crypto.randomUUID();
  env.DB.sqlite.prepare(`INSERT INTO proposals (id, kind, title, summary, status, provenance, receipt_hash, created_at, updated_at, published_at)
    VALUES (?, 'mission', 'Retired mission', 'A mission the moderators retire with its project.', 'published', 'seed', NULL, ?, ?, ?)`)
    .run(retiredMission, NOW, NOW, NOW);
  const doomed = await call(env, 'POST', '/api/v1/projects', {
    mission_id: retiredMission, title: 'Withdrawn counter pilot',
    purpose: 'A project whose mission disappears from public view.',
  }, aria);
  assert.equal(doomed.status, 201, JSON.stringify(doomed.body).slice(0, 200));
  env.DB.sqlite.prepare("UPDATE proposals SET status = 'rejected', published_at = NULL WHERE id = ?").run(retiredMission);
  snapshot = await call(env, 'GET', '/api/v1/activity');
  assert.deepEqual(snapshot.body.coordination, {
    projects_total: 2, projects_open: 1, projects_closed: 1, milestones_open: 0, milestones_done: 1,
    commitments_confirmed: 0, commitments_completed: 1, deliveries_total: 2,
  });
  assert.ok(!JSON.stringify(snapshot.body).includes(doomed.body.id));
});
