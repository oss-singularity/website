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
const digest = 'a'.repeat(64);

async function coordinatedMilestone(env, coordinator, contributor) {
  const project = await call(env, 'POST', '/api/v1/projects', {
    mission_id: 'build-the-commons', title: 'Receipts pilot',
    purpose: 'Deliver inspectable artifacts with an honest manifest.',
  }, coordinator);
  assert.equal(project.status, 201, JSON.stringify(project.body).slice(0, 200));
  const milestone = await call(env, 'POST', `/api/v1/projects/${project.body.id}/milestones`, {
    title: 'Digest verifier', purpose: 'Verify raw-file digests for independent reviewers.',
    expected_artifact: 'A delivery manifest with raw-file digest instructions.',
    acceptance: criteria, expected_version: project.body.version,
  }, coordinator);
  assert.equal(milestone.status, 201, JSON.stringify(milestone.body ?? '').slice(0, 200));
  const offer = await call(env, 'POST', `/api/v1/projects/${project.body.id}/commitments`,
    { milestone_id: milestone.body.id, terms: 'volunteer' }, contributor);
  assert.equal(offer.status, 201, JSON.stringify(offer.body).slice(0, 200));
  const confirmed = await call(env, 'POST', `/api/v1/projects/${project.body.id}/commitments/${offer.body.id}/actions`,
    { action: 'confirm' }, coordinator);
  assert.equal(confirmed.status, 200, JSON.stringify(confirmed.body ?? '').slice(0, 200));
  return { projectId: project.body.id, milestoneId: milestone.body.id };
}

test('a confirmed contributor delivers immutable revisions with a verifiable manifest', async t => {
  const env = await environment(t);
  const aria = await enroll(env, 'aria');
  const kofi = await enroll(env, 'kofi');
  const { projectId, milestoneId } = await coordinatedMilestone(env, aria, kofi);

  const first = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`, {
    summary: 'First revision of the synthetic artifact with its digest instructions.',
    artifact_url: 'https://oss-singularity.io/data/synthetic-delivery-artifact.json',
    artifact_media_type: 'text/plain', artifact_size_bytes: 512,
    integrity_digest: digest, content_identifier: 'bafkreihdwdcefgh4dqkjv67uzcmw7ojee6xedzdetojuzjevtenxquvyku',
    evidence_url: 'https://github.com/oss-singularity/website/pull/89',
    expected_version: 2,
  }, kofi);
  assert.equal(first.status, 201, JSON.stringify(first.body ?? '').slice(0, 300));
  assert.equal(first.body.revision, 1, 'revisions are server-numbered from one');
  assert.equal(first.body.artifact.integrity.algorithm, 'sha256');
  assert.equal(first.body.author.github_login, 'kofi');

  const manifest = await call(env, 'GET', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries/1`);
  assert.equal(manifest.status, 200, JSON.stringify(manifest.body ?? '').slice(0, 300));
  assert.equal(manifest.body.kind, 'oss-delivery-manifest');
  assert.equal(manifest.body.schema_version, 1);
  assert.equal(manifest.body.milestone.id, milestoneId);
  assert.equal(manifest.body.artifact.content_identifier.startsWith('baf'), true);
  assert.match(manifest.body.verification.content_identifier_note, /not the ordinary checksum/);
  assert.match(manifest.body.notice, /verifies no artifact bytes/);

  const second = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`, {
    summary: 'Second revision after an independent verification found an encoding gap.',
    artifact_url: 'https://oss-singularity.io/data/synthetic-delivery-artifact.json',
    artifact_media_type: 'text/plain', artifact_size_bytes: 513,
    integrity_digest: 'b'.repeat(64), expected_version: 3,
  }, kofi);
  assert.equal(second.status, 201, JSON.stringify(second.body ?? '').slice(0, 300));
  assert.equal(second.body.revision, 2);
  assert.equal(second.body.artifact.content_identifier, null);

  const stillOne = await call(env, 'GET', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries/1`);
  assert.equal(stillOne.body.artifact.integrity.digest, digest, 'revision one stays immutable');

  const list = await call(env, 'GET', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`);
  assert.equal(list.status, 200);
  assert.equal(list.body.items.length, 2);
  assert.equal(list.body.items[0].revision, 2, 'the list orders by newest revision');

  const discovery = await call(env, 'GET', '/api/v1');
  assert.equal(discovery.body.endpoints.project_deliveries, '/api/v1/projects/{id}/milestones/{milestone_id}/deliveries');
  assert.equal(discovery.body.endpoints.project_delivery_manifest, '/api/v1/projects/{id}/milestones/{milestone_id}/deliveries/{revision}');
});

test('delivery authority stays with the confirmed contributor of an open milestone', async t => {
  const env = await environment(t);
  const aria = await enroll(env, 'aria');
  const kofi = await enroll(env, 'kofi');
  const mallory = await enroll(env, 'mallory');
  const { projectId, milestoneId } = await coordinatedMilestone(env, aria, kofi);

  const stranger = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`, {
    summary: 'A delivery from an identity without a bound commitment here.',
    artifact_url: 'https://example.org/artifact.txt', artifact_media_type: 'text/plain',
    artifact_size_bytes: 10, integrity_digest: digest, expected_version: 3,
  }, mallory);
  assert.equal(stranger.status, 403, JSON.stringify(stranger.body ?? '').slice(0, 200));
  assert.equal(stranger.body.error.code, 'forbidden');

  const coordinator = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`, {
    summary: 'The coordinator reviews but does not deliver for the contributor.',
    artifact_url: 'https://example.org/artifact.txt', artifact_media_type: 'text/plain',
    artifact_size_bytes: 10, integrity_digest: digest, expected_version: 3,
  }, aria);
  assert.equal(coordinator.status, 403);

  const stale = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`, {
    summary: 'A delivery submitted against a stale project version.',
    artifact_url: 'https://example.org/artifact.txt', artifact_media_type: 'text/plain',
    artifact_size_bytes: 10, integrity_digest: digest, expected_version: 1,
  }, kofi);
  assert.equal(stale.status, 409, JSON.stringify(stale.body ?? '').slice(0, 200));
  assert.equal(stale.body.error.code, 'version_conflict');

  const done = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestoneId}/actions`,
    { action: 'complete', expected_version: 1 }, aria);
  assert.equal(done.status, 200, JSON.stringify(done.body ?? '').slice(0, 200));
  const closed = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`, {
    summary: 'A delivery onto a milestone that is already complete.',
    artifact_url: 'https://example.org/artifact.txt', artifact_media_type: 'text/plain',
    artifact_size_bytes: 10, integrity_digest: digest, expected_version: 9,
  }, kofi);
  assert.equal(closed.status, 409);
  assert.equal(closed.body.error.code, 'milestone_closed');
});

test('declared integrity metadata is validated without the server fetching anything', async t => {
  const env = await environment(t);
  const aria = await enroll(env, 'aria');
  const kofi = await enroll(env, 'kofi');
  const { projectId, milestoneId } = await coordinatedMilestone(env, aria, kofi);
  const attempts = [
    { summary: 'A delivery whose digest is not a lowercase hex sha256 value.', integrity_digest: 'XYZ' },
    { summary: 'A delivery whose artifact is not served over trusted HTTPS.', artifact_url: 'http://example.org/artifact.txt' },
    { summary: 'A delivery whose artifact hides on a reserved internal host.', artifact_url: 'https://localhost/artifact.txt' },
    { summary: 'A delivery with an undeclared media type for the artifact.', artifact_media_type: 'application/x-msdownload' },
    { summary: 'A delivery whose artifact size is not a positive bounded integer.', artifact_size_bytes: 0 },
    { summary: 'A delivery whose content identifier is not compact.', content_identifier: 'not a cid!!' },
    { summary: 'A', summary_too: true },
  ];
  for (const override of attempts) {
    const body = {
      summary: 'A delivery whose declared metadata must be rejected honestly.',
      artifact_url: 'https://example.org/artifact.txt', artifact_media_type: 'text/plain',
      artifact_size_bytes: 10, integrity_digest: digest, expected_version: 3, ...override,
    };
    delete body.summary_too;
    if (override.summary_too) body.summary = override.summary;
    const attempt = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`, body, kofi);
    assert.equal(attempt.status, 400, JSON.stringify({ override, body: attempt.body }).slice(0, 240));
  }
  const unsupported = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`, {
    summary: 'A delivery carrying a field the contract does not declare.',
    artifact_url: 'https://example.org/artifact.txt', artifact_media_type: 'text/plain',
    artifact_size_bytes: 10, integrity_digest: digest, expected_version: 3, approved_by: 'me',
  }, kofi);
  assert.equal(unsupported.status, 400);
});

test('unknown revisions fail closed and the revision cap holds', async t => {
  const env = await environment(t);
  const aria = await enroll(env, 'aria');
  const kofi = await enroll(env, 'kofi');
  const { projectId, milestoneId } = await coordinatedMilestone(env, aria, kofi);
  const missing = await call(env, 'GET', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries/7`);
  assert.equal(missing.status, 404);
  const invalid = await call(env, 'GET', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries/99`);
  assert.equal(invalid.status, 404);

  let version = 2;
  for (let revision = 1; revision <= 10; revision += 1) {
    const delivery = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`, {
      summary: `Synthetic revision ${revision} of the delivered artifact bytes.`,
      artifact_url: 'https://example.org/artifact.txt', artifact_media_type: 'text/plain',
      artifact_size_bytes: 10 + revision, integrity_digest: digest, expected_version: version,
    }, kofi);
    assert.equal(delivery.status, 201, JSON.stringify(delivery.body ?? '').slice(0, 200));
    version += 1;
  }
  const over = await call(env, 'POST', `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`, {
    summary: 'An eleventh revision that the bounded pilot refuses to store.',
    artifact_url: 'https://example.org/artifact.txt', artifact_media_type: 'text/plain',
    artifact_size_bytes: 99, integrity_digest: digest, expected_version: version,
  }, kofi);
  assert.equal(over.status, 409, JSON.stringify(over.body ?? '').slice(0, 200));
  assert.equal(over.body.error.code, 'revision_limit');
});
