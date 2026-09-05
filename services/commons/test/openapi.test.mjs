import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import worker from '../worker.mjs';
import { SQLiteD1 } from '../local-d1.mjs';

const specification = JSON.parse(readFileSync(new URL('../../../site/data/commons-openapi.json', import.meta.url), 'utf8'));

// Response-contract checks for the schema vocabulary used in this document.
// This deliberately does not claim to be a general OpenAPI validator.
function matches(value, schema, path = '$') {
  if (schema.$ref) matches(value, schema.$ref.slice(2).split('/').reduce((node, key) => node[key], specification), path);
  for (const child of schema.allOf || []) matches(value, child, path);
  if (schema.anyOf) assert.ok(schema.anyOf.some(child => { try { matches(value, child, path); return true; } catch { return false; } }), `${path}: anyOf`);
  if ('const' in schema) assert.deepEqual(value, schema.const, `${path}: const`);
  if (schema.enum) assert.ok(schema.enum.includes(value), `${path}: enum`);
  if (schema.type) {
    const actual = value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    assert.ok(types.includes(actual) || (types.includes('integer') && Number.isInteger(value)), `${path}: type`);
  }
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    for (const field of schema.required || []) assert.ok(field in value, `${path}: missing ${field}`);
    if (schema.additionalProperties === false) for (const field of Object.keys(value)) assert.ok(field in schema.properties, `${path}: extra ${field}`);
    for (const [field, child] of Object.entries(schema.properties || {})) if (field in value) matches(value[field], child, `${path}.${field}`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined) assert.ok(value.length >= schema.minItems, path);
    if (schema.maxItems !== undefined) assert.ok(value.length <= schema.maxItems, path);
    if (schema.items) value.forEach((item, index) => matches(item, schema.items, `${path}[${index}]`));
  }
  if (typeof value === 'string') {
    if (schema.pattern) assert.match(value, new RegExp(schema.pattern, 'u'), path);
    if (schema.minLength !== undefined) assert.ok([...value].length >= schema.minLength, path);
    if (schema.maxLength !== undefined) assert.ok([...value].length <= schema.maxLength, path);
    if (schema.format === 'date-time') assert.ok(Number.isFinite(Date.parse(value)), path);
    if (schema.format === 'uri') assert.ok(new URL(value).protocol, path);
  }
  if (typeof value === 'number') {
    if (schema.minimum !== undefined) assert.ok(value >= schema.minimum, path);
    if (schema.maximum !== undefined) assert.ok(value <= schema.maximum, path);
  }
}

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
