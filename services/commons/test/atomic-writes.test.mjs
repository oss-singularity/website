import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../worker.mjs';
import { SQLiteD1 } from '../local-d1.mjs';

const NOW = Date.parse('2026-09-18T00:00:00Z');
let currentLogin = '';
let currentProof = null;
let currentGithubId = 300;

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
    mission_id: 'build-the-commons', title: 'Atomicity pilot',
    purpose: 'Prove that rejected compare-and-set writes persist nothing.',
  }, coordinator);
  const milestone = await call(env, 'POST', `/api/v1/projects/${project.body.id}/milestones`, {
    title: 'Guarded slice', purpose: 'One milestone walked through every guarded write.',
    expected_artifact: 'A delivery whose acceptance binds the exact revision.',
    acceptance: criteria, expected_version: project.body.version,
  }, coordinator);
  const offer = await call(env, 'POST', `/api/v1/projects/${project.body.id}/commitments`,
    { milestone_id: milestone.body.id, terms: 'volunteer' }, contributor);
  await call(env, 'POST', `/api/v1/projects/${project.body.id}/commitments/${offer.body.id}/actions`, { action: 'confirm' }, coordinator);
  return { projectId: project.body.id, milestoneId: milestone.body.id };
}

const deliver = (env, ids, contributor, revision, summary) => call(env, 'POST',
  `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/deliveries`, {
    summary, artifact_url: ARTIFACT, artifact_media_type: 'application/json',
    artifact_size_bytes: 591, integrity_digest: digest, expected_version: revision,
  }, contributor);

// A full snapshot of every fachliche table: a rejected write must leave all of
// them untouched, including events and version counters.
const TABLES = ['projects', 'milestones', 'milestone_dependencies', 'commitments', 'deliveries', 'milestone_reviews', 'project_events'];
const snapshot = env => Object.fromEntries(TABLES.map(table => {
  const rows = env.DB.sqlite.prepare(`SELECT * FROM ${table}`).all();
  rows.sort((a, b) => JSON.stringify(a) < JSON.stringify(b) ? -1 : 1);
  return [table, rows];
}));

const injectBeforeBatch = (env, matches, interleave) => {
  const batch = env.DB.batch.bind(env.DB);
  let armed = true;
  env.DB.batch = async statements => {
    if (!armed || !matches(statements)) return batch(statements);
    armed = false;
    env.DB.batch = batch;
    await interleave();
    return batch(statements);
  };
};

test('a stale milestone add is rejected without inserting the milestone or an event', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const ids = await reviewableProject(env, coordinator, contributor);
  const before = snapshot(env);
  const result = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones`, {
    title: 'Stale milestone', purpose: 'This write must leave nothing behind at all.',
    expected_artifact: 'One synthetic artifact that must never appear.',
    acceptance: criteria, expected_version: 1,
  }, coordinator);
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'version_conflict');
  assert.deepEqual(snapshot(env), before);
});

test('a stale project close is rejected without cancelling any work', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const ids = await reviewableProject(env, coordinator, contributor);
  const before = snapshot(env);
  const result = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/actions`, { action: 'close', expected_version: 1 }, coordinator);
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'version_conflict');
  assert.deepEqual(snapshot(env), before);
});

test('a stale acceptance is rejected without completing the milestone or the commitment', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const ids = await reviewableProject(env, coordinator, contributor);
  assert.equal((await deliver(env, ids, contributor, 2, 'Synthetic first revision for the stale acceptance proof.')).status, 201);
  const before = snapshot(env);
  const result = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`,
    { delivery_revision: 1, decision: 'accept', expected_version: 999 }, coordinator);
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'version_conflict');
  assert.deepEqual(snapshot(env), before, 'no completion, no review, no delivery_accepted event, no version bump');
});

test('a stale revision request is rejected without a version bump or an event', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const ids = await reviewableProject(env, coordinator, contributor);
  assert.equal((await deliver(env, ids, contributor, 2, 'Synthetic revision for the stale review request proof.')).status, 201);
  const before = snapshot(env);
  const result = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 1, decision: 'revision_requested',
    note: 'A stale review probe with a wrong expected version.', expected_version: 999,
  }, coordinator);
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'version_conflict');
  assert.deepEqual(snapshot(env), before);
});

test('a stale delivery is rejected without a version bump or a delivery_added event', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const ids = await reviewableProject(env, coordinator, contributor);
  const before = snapshot(env);
  const result = await deliver(env, ids, contributor, 999, 'A stale delivery that must bump nothing.');
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'version_conflict');
  assert.deepEqual(snapshot(env), before);
});

test('a stale milestone completion is rejected without bumping the project version', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const ids = await reviewableProject(env, coordinator, contributor);
  const before = snapshot(env);
  const result = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/actions`,
    { action: 'complete', expected_version: 999 }, coordinator);
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'version_conflict');
  assert.deepEqual(snapshot(env), before);
});

test('an acceptance racing a newer revision fails on the commit-time revision guard', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const ids = await reviewableProject(env, coordinator, contributor);
  assert.equal((await deliver(env, ids, contributor, 2, 'Synthetic first revision for the race proof.')).status, 201);
  const version = env.DB.sqlite.prepare('SELECT version FROM projects WHERE id = ?').get(ids.projectId).version;
  injectBeforeBatch(env,
    statements => statements[0].sql.includes('INSERT INTO milestone_reviews'),
    async () => {
      const newer = await deliver(env, ids, contributor, version, 'Synthetic second revision arriving just before acceptance commits.');
      assert.equal(newer.status, 201, JSON.stringify(newer.body));
    });
  const result = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`,
    { delivery_revision: 1, decision: 'accept', expected_version: 1 }, coordinator);
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'stale_revision');
  const after = snapshot(env);
  assert.equal(after.milestone_reviews.length, 0, 'no acceptance review was recorded');
  assert.deepEqual(after.milestones.map(row => row.status), ['open']);
  assert.equal(after.commitments.find(row => row.milestone_id === ids.milestoneId).status, 'confirmed');
  assert.equal(after.deliveries.length, 2, 'the racing revision itself stays intact');
  assert.equal(after.project_events.filter(row => row.action === 'delivery_accepted').length, 0);
  assert.equal(after.project_events.filter(row => row.action === 'delivery_added').length, 2);

  // The same reviewer can still accept the revision that is current now.
  const accepted = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`,
    { delivery_revision: 2, decision: 'accept', expected_version: 1 }, coordinator);
  assert.equal(accepted.status, 201, JSON.stringify(accepted.body));
  assert.equal(accepted.body.delivery_revision, 2);
  const manifest = await call(env, 'GET', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/deliveries/1`);
  assert.equal(manifest.body.review, null, 'the superseded revision carries no acceptance');
  const current = await call(env, 'GET', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/deliveries/2`);
  assert.equal(current.body.review.decision, 'accept');
  assert.equal(current.body.superseded_by_revision, null);
});

test('a raced duplicate commitment offer records no second offer event', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const racer = await enroll(env, 'atomic-racer');
  const ids = await reviewableProject(env, coordinator, contributor);
  const version = env.DB.sqlite.prepare('SELECT version FROM projects WHERE id = ?').get(ids.projectId).version;
  injectBeforeBatch(env,
    statements => statements[0].sql.includes('INSERT INTO commitments'),
    async () => {
      const racing = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/commitments`,
        { milestone_id: ids.milestoneId, terms: 'volunteer' }, racer);
      assert.equal(racing.status, 201, JSON.stringify(racing.body));
    });
  const result = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/commitments`,
    { milestone_id: ids.milestoneId, terms: 'volunteer' }, racer);
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'duplicate_commitment');
  const after = snapshot(env);
  assert.equal(after.commitments.length, 2, 'the confirmed setup commitment plus exactly one raced offer');
  assert.equal(after.project_events.filter(row => row.action === 'commitment_offered').length, 2, 'setup offer plus the raced winner, no leak for the loser');
  assert.equal(version, env.DB.sqlite.prepare('SELECT version FROM projects WHERE id = ?').get(ids.projectId).version);
});

test('a raced commitment confirm records neither the event nor the transition', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const racer = await enroll(env, 'atomic-racer');
  const ids = await reviewableProject(env, coordinator, contributor);
  const offer = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/commitments`,
    { milestone_id: ids.milestoneId, terms: 'volunteer' }, racer);
  assert.equal(offer.status, 201, JSON.stringify(offer.body));
  let afterWithdraw;
  injectBeforeBatch(env,
    statements => statements.some(statement => statement.sql.includes('INSERT INTO project_events'))
      && statements.some(statement => statement.sql.startsWith('UPDATE commitments')),
    async () => {
      const withdrawn = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/commitments/${offer.body.id}/actions`,
        { action: 'withdraw' }, racer);
      assert.equal(withdrawn.status, 200, JSON.stringify(withdrawn.body));
      afterWithdraw = snapshot(env);
    });
  const result = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/commitments/${offer.body.id}/actions`,
    { action: 'confirm' }, coordinator);
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'state_conflict');
  assert.deepEqual(snapshot(env), afterWithdraw, 'the withdraw won; the confirm left nothing behind');
});

test('a milestone add racing a project close fails cleanly with no orphaned rows', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const ids = await reviewableProject(env, coordinator, contributor);
  const version = env.DB.sqlite.prepare('SELECT version FROM projects WHERE id = ?').get(ids.projectId).version;
  injectBeforeBatch(env,
    statements => statements[0].sql.includes('INSERT INTO milestones'),
    async () => {
      const closed = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/actions`, { action: 'close', expected_version: version }, coordinator);
      assert.equal(closed.status, 200, JSON.stringify(closed.body));
    });
  const result = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones`, {
    title: 'Racing milestone', purpose: 'The close wins and this write must vanish.',
    expected_artifact: 'One synthetic artifact that must never appear.',
    acceptance: criteria, expected_version: version,
  }, coordinator);
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'project_closed');
  const after = snapshot(env);
  assert.equal(after.milestones.length, 1, 'only the setup milestone exists');
  assert.equal(after.project_events.filter(row => row.action === 'milestone_added').length, 1);
  assert.deepEqual(after.projects.map(row => row.status), ['closed']);
});

test('the milestone cap holds inside the write when a racing request fills it first', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const project = await call(env, 'POST', '/api/v1/projects', {
    mission_id: 'build-the-commons', title: 'Cap pilot',
    purpose: 'Fill the milestone cap while a raced add is between check and write.',
  }, coordinator);
  const projectId = project.body.id;
  const owner = env.DB.sqlite.prepare('SELECT coordinator_identity_id AS id FROM projects WHERE id = ?').get(projectId).id;
  const filler = env.DB.sqlite.prepare(`INSERT INTO milestones (id, project_id, parent_milestone_id, created_by_identity_id,
      title, purpose, expected_artifact, acceptance, status, scope_version, version, created_at, updated_at)
    VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'open', 1, 1, ?, ?)`);
  for (let index = 1; index <= 19; index += 1) {
    filler.run(crypto.randomUUID(), projectId, owner, `Cap filler ${index}`,
      'Occupies one of the twenty guarded milestone slots.',
      'A synthetic cap filler artifact.', '["Occupies a guarded slot."]', NOW, NOW);
  }
  injectBeforeBatch(env,
    statements => statements[0].sql.includes('INSERT INTO milestones'),
    () => {
      filler.run(crypto.randomUUID(), projectId, owner, 'Cap racer',
        'Fills the last slot while the raced add waits.',
        'A synthetic cap racer artifact.', '["Fills the last slot."]', NOW, NOW);
    });
  const result = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones`, {
    title: 'Over cap', purpose: 'This write must clear the write-guarded cap.',
    expected_artifact: 'One synthetic artifact over the cap.',
    acceptance: criteria, expected_version: project.body.version,
  }, coordinator);
  assert.equal(result.status, 409);
  assert.equal(result.body.error.code, 'milestone_limit');
  const after = snapshot(env);
  assert.equal(after.milestones.length, 20, 'nineteen fillers plus the racer, never the loser');
  assert.deepEqual(after.projects.map(row => row.version), [1], 'no version bump leaked');
  assert.deepEqual(after.project_events.map(row => row.action), ['created'], 'no milestone_added event leaked');
});

test('replayed terminal actions stay idempotent: one close, one close event', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const ids = await reviewableProject(env, coordinator, contributor);
  const closed = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/actions`, { action: 'close', expected_version: 2 }, coordinator);
  assert.equal(closed.status, 200);
  const before = snapshot(env);
  const replay = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/actions`, { action: 'close', expected_version: 2 }, coordinator);
  assert.equal(replay.status, 409);
  assert.equal(replay.body.error.code, 'version_conflict');
  assert.deepEqual(snapshot(env), before, 'the replay appended no second close event and touched nothing');
});

test('a repeated exact acceptance is refused without a second completion', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const ids = await reviewableProject(env, coordinator, contributor);
  assert.equal((await deliver(env, ids, contributor, 2, 'Synthetic revision for the duplicate acceptance proof.')).status, 201);
  const accepted = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`,
    { delivery_revision: 1, decision: 'accept', expected_version: 1 }, coordinator);
  assert.equal(accepted.status, 201);
  const before = snapshot(env);
  const replay = await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`,
    { delivery_revision: 1, decision: 'accept', expected_version: 1 }, coordinator);
  assert.equal(replay.status, 409);
  assert.equal(replay.body.error.code, 'duplicate_decision');
  assert.deepEqual(snapshot(env), before);
});

test('the guarded success path keeps the documented event versions byte for byte', async t => {
  const env = await environment(t);
  const coordinator = await enroll(env, 'atomic-coordinator');
  const contributor = await enroll(env, 'atomic-contributor');
  const ids = await reviewableProject(env, coordinator, contributor);
  const version = () => env.DB.sqlite.prepare('SELECT version FROM projects WHERE id = ?').get(ids.projectId).version;
  const milestoneVersion = () => env.DB.sqlite.prepare('SELECT version FROM milestones WHERE id = ?').get(ids.milestoneId).version;
  assert.equal((await deliver(env, ids, contributor, version(), 'First revision of the happy path.')).status, 201);
  assert.equal((await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 1, decision: 'revision_requested', note: 'State the retention window for the bytes.',
    expected_version: milestoneVersion(),
  }, coordinator)).status, 201);
  assert.equal((await deliver(env, ids, contributor, version(), 'Second revision answering the request.')).status, 201);
  assert.equal((await call(env, 'POST', `/api/v1/projects/${ids.projectId}/milestones/${ids.milestoneId}/reviews`, {
    delivery_revision: 2, decision: 'accept', expected_version: milestoneVersion(),
  }, coordinator)).status, 201);
  assert.equal((await call(env, 'POST', `/api/v1/projects/${ids.projectId}/actions`, { action: 'close', expected_version: version() }, coordinator)).status, 200);
  const events = env.DB.sqlite.prepare('SELECT action, version FROM project_events WHERE project_id = ? ORDER BY rowid')
    .all(ids.projectId).map(row => ({ action: row.action, version: row.version }));
  assert.deepEqual(events, [
    { action: 'created', version: 2 },
    { action: 'milestone_added', version: 3 },
    { action: 'commitment_offered', version: 3 },
    { action: 'commitment_confirm', version: 3 },
    { action: 'delivery_added', version: 4 },
    { action: 'revision_requested', version: 5 },
    { action: 'delivery_added', version: 6 },
    { action: 'delivery_accepted', version: 7 },
    { action: 'close', version: 8 },
  ]);
  const project = env.DB.sqlite.prepare('SELECT status, version FROM projects WHERE id = ?').get(ids.projectId);
  assert.equal(project.status, 'closed');
  assert.equal(project.version, 7);
  const milestone = env.DB.sqlite.prepare('SELECT status, version FROM milestones WHERE id = ?').get(ids.milestoneId);
  assert.equal(milestone.status, 'done');
  assert.equal(milestone.version, 2, 'a revision request leaves the milestone version alone; only acceptance bumps it');
  const commitments = env.DB.sqlite.prepare('SELECT status FROM commitments WHERE milestone_id = ?')
    .all(ids.milestoneId).map(row => row.status);
  assert.deepEqual(commitments, ['completed']);
});
