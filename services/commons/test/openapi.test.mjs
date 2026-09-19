import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../worker.mjs';
import { SQLiteD1 } from '../local-d1.mjs';

import { matches, specification } from './schema-assertions.mjs';

test('actual SQLite and stubbed GitHub responses match the published public OpenAPI schemas', async t => {
  const now = Date.parse('2026-09-05T12:34:00Z');
  t.mock.method(Date, 'now', () => now);
  const DB = new SQLiteD1();
  t.after(() => DB.sqlite.close());
  const env = { DB, PUBLIC_ORIGIN: 'https://oss-singularity.io', IP_HMAC_SECRET: 'test_only_hmac_secret_longer_than_32', ADMIN_TOKEN: 'test_only_admin_secret_longer_than_32', RELEASE_SHA: 'a'.repeat(40) };
  async function request(path, name, method = 'GET', body, token) {
    const result = await worker.fetch(new Request(env.PUBLIC_ORIGIN + path, { method, headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.87', ...(token ? { authorization: `Bearer ${token}` } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}) }), env);
    const data = await result.json();
    matches(data, specification.components.schemas[name]);
    return data;
  }
  await request('/api/v1', 'Discovery');
  await request('/api/v1/activity', 'Activity');
  await request('/api/v1/missions?limit=2', 'MissionsPage');
  await request('/api/v1/missions/build-the-commons', 'PublishedMission');
  await request('/api/v1/contributions', 'ContributionsPage');
  await request('/api/v1/reviews', 'ReviewsPage');
  const challenge = await request('/api/v1/identity-challenges', 'IdentityChallenge', 'POST', { github_login: 'builder' });
  t.mock.method(globalThis, 'fetch', async url => new Response(JSON.stringify(url.includes('/gists/') ? { public: true, truncated: false, owner: { id: 99, login: 'builder' }, files: { 'oss-singularity-identity.json': { truncated: false, content: JSON.stringify(challenge.proof) } } } : { id: 99, login: 'builder', created_at: '2020-01-01T00:00:00Z' })));
  const enrollment = await request('/api/v1/identities', 'IdentityEnrollment', 'POST', { challenge_id: challenge.id, gist_url: 'https://gist.github.com/abcdef0123456789' }, challenge.challenge_token);
  await request(`/api/v1/identities/${enrollment.identity.id}`, 'Identity');
  const receipt = await request('/api/v1/proposals', 'ProposalReceipt', 'POST', { kind: 'review', target_id: 'audit-project', score: 4, title: 'A response contract review', summary: 'An isolated, source-backed review of the published contract.', url: 'https://github.com/oss-singularity/website' }, enrollment.api_token);
  await request(receipt.poll_url, 'Proposal', 'GET', undefined, receipt.receipt_token);
  await request(receipt.poll_url, 'Error');
  await request(`/api/v1/admin/proposals/${receipt.id}`, 'Proposal', 'PATCH', { status: 'published' }, env.ADMIN_TOKEN);
  await request('/api/v1/reviews?target_id=audit-project', 'ReviewsPage');
  const participation = await request('/api/v1/participations', 'ParticipationReceipt', 'POST', {
    mission_id: 'build-the-commons', intent: 'offer', participant_type: 'agent', collaboration: 'volunteer',
    title: 'A bounded contract review', summary: 'Check the isolated public contract and deliver a source-backed review.',
  }, enrollment.api_token);
  await request(participation.poll_url, 'Participation', 'GET', undefined, participation.receipt_token);
  await request('/api/v1/participations/mine', 'OwnParticipationsPage', 'GET', undefined, enrollment.api_token);
  await request(`/api/v1/admin/participations/${participation.id}`, 'Participation', 'PATCH', { status: 'published' }, env.ADMIN_TOKEN);
  await request('/api/v1/participations?mission_id=build-the-commons', 'ParticipationsPage');
  await request(participation.poll_url, 'Participation', 'PATCH', { state: 'closed' }, enrollment.api_token);
  await request('/api/v1/participations?state=closed', 'ParticipationsPage');
  await request('/api/v1/activity', 'Activity');
  await request(participation.poll_url, 'Participation', 'PATCH', { state: 'withdrawn' }, enrollment.api_token);
  await request(participation.poll_url, 'Error', 'PATCH', { state: 'closed' }, enrollment.api_token);
});

test('actual project responses match the published project response schemas', async t => {
  const now = Date.parse('2026-09-05T12:34:00Z');
  t.mock.method(Date, 'now', () => now);
  const DB = new SQLiteD1();
  t.after(() => DB.sqlite.close());
  const env = { DB, PUBLIC_ORIGIN: 'https://oss-singularity.io', IP_HMAC_SECRET: 'test_only_hmac_secret_longer_than_32', ADMIN_TOKEN: 'test_only_admin_secret_longer_than_32', RELEASE_SHA: 'a'.repeat(40) };
  let currentLogin = '';
  let currentProof = null;
  let currentGithubId = 300;
  t.mock.method(globalThis, 'fetch', async url => new Response(JSON.stringify(
    url.includes('/gists/')
      ? { public: true, truncated: false, owner: { id: currentGithubId, login: currentLogin }, files: { 'oss-singularity-identity.json': { truncated: false, content: JSON.stringify(currentProof) } } }
      : { id: currentGithubId, login: currentLogin, created_at: '2020-01-01T00:00:00Z' })));
  async function enroll(login) {
    currentLogin = login;
    currentGithubId += 1;
    const challenge = await worker.fetch(new Request(`${env.PUBLIC_ORIGIN}/api/v1/identity-challenges`, {
      method: 'POST', headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.9' },
      body: JSON.stringify({ github_login: login }),
    }), env);
    const challengeBody = await challenge.json();
    currentProof = challengeBody.proof;
    const enrollment = await worker.fetch(new Request(`${env.PUBLIC_ORIGIN}/api/v1/identities`, {
      method: 'POST', headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.9', authorization: `Bearer ${challengeBody.challenge_token}` },
      body: JSON.stringify({ challenge_id: challengeBody.id, gist_url: 'https://gist.github.com/abcdef0123456789' }),
    }), env);
    const body = await enrollment.json();
    assert.equal(enrollment.status, 201, JSON.stringify(body).slice(0, 200));
    return body.api_token;
  }
  async function request(path, name, method = 'GET', body, token) {
    const result = await worker.fetch(new Request(env.PUBLIC_ORIGIN + path, { method, headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.9', ...(token ? { authorization: `Bearer ${token}` } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}) }), env);
    const data = await result.json();
    assert.equal(result.status < 400, true, `${method} ${path}: ${JSON.stringify(data).slice(0, 200)}`);
    matches(data, specification.components.schemas[name]);
    return data;
  }
  const coordinator = await enroll('contract-coordinator');
  const contributor = await enroll('contract-contributor');

  const created = await request('/api/v1/projects', 'ProjectCreated', 'POST', {
    mission_id: 'build-the-commons', title: 'Response contract pilot',
    purpose: 'Walk the published project response schemas through real answers.',
  }, coordinator);
  const projectId = created.id;
  await request('/api/v1/projects', 'ProjectsPage');

  const milestone = await request(`/api/v1/projects/${projectId}/milestones`, 'MilestoneView', 'POST', {
    title: 'Contract slice', purpose: 'One milestone completing the full acceptance loop.',
    expected_artifact: 'A delivery whose acceptance completes a commitment.',
    acceptance: ['One criterion long enough for the contract validator.'], expected_version: created.version,
  }, coordinator);
  const offer = await request(`/api/v1/projects/${projectId}/commitments`, 'CommitmentView', 'POST',
    { milestone_id: milestone.id, terms: 'volunteer' }, contributor);
  await request(`/api/v1/projects/${projectId}/commitments/${offer.id}/actions`, 'CommitmentView', 'POST', { action: 'confirm' }, coordinator);
  const version = (await request(`/api/v1/projects/${projectId}`, 'ProjectDetail')).version;
  await request(`/api/v1/projects/${projectId}/milestones/${milestone.id}/deliveries`, 'DeliveryView', 'POST', {
    summary: 'A synthetic delivery completing the response contract walkthrough.',
    artifact_url: 'https://oss-singularity.io/data/synthetic-delivery-artifact.json',
    artifact_media_type: 'application/json', artifact_size_bytes: 591,
    integrity_digest: 'b'.repeat(64), expected_version: version,
  }, contributor);
  await request(`/api/v1/projects/${projectId}/milestones/${milestone.id}/reviews`, 'MilestoneReview', 'POST', {
    delivery_revision: 1, decision: 'accept',
    note: 'The synthetic walkthrough completes with an accepted revision.',
    expected_version: milestone.version,
  }, coordinator);

  // Public and coordinator views both carry a completed commitment; only the
  // coordinator adds the viewer marker and creator attributions.
  const detail = await request(`/api/v1/projects/${projectId}`, 'ProjectDetail');
  assert.deepEqual(detail.commitments.map(c => c.status), ['completed']);
  const coordinatorDetail = await request(`/api/v1/projects/${projectId}`, 'ProjectDetail', 'GET', undefined, coordinator);
  assert.equal(coordinatorDetail.viewer.coordinator, true);
  await request(`/api/v1/projects/${projectId}/export`, 'ProjectExport');

  // The merged schemas stay closed: extra fields and missing fields reject.
  assert.throws(() => matches({ ...detail, scope_version: 1 }, specification.components.schemas.ProjectDetail), /extra /);
  assert.throws(() => matches({ ...created, surprise: true }, specification.components.schemas.ProjectCreated), /extra /);
  const { milestones, ...withoutMilestones } = coordinatorDetail;
  assert.throws(() => matches(withoutMilestones, specification.components.schemas.ProjectDetail), /missing /);
});
