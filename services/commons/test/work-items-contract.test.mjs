import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import worker from '../worker.mjs';
import { SQLiteD1 } from '../local-d1.mjs';
import { digest, randomToken } from '../security.mjs';
import { matches, specification } from './schema-assertions.mjs';

test('work-item HTTP journey conforms to the published request and response contracts', async t => {
  const now = Date.parse('2026-09-05T19:00:00Z');
  t.mock.method(Date, 'now', () => now);
  const DB = new SQLiteD1();
  t.after(() => DB.sqlite.close());
  const env = { DB, PUBLIC_ORIGIN: 'https://oss-singularity.io',
    ADMIN_TOKEN: 'test_only_admin_token_at_least_32_characters',
    IP_HMAC_SECRET: 'test_only_hmac_secret_at_least_32_characters' };
  const schemas = specification.components.schemas;
  const actors = [];
  for (let n = 1; n <= 2; n++) {
    const token = randomToken(), id = randomUUID();
    DB.sqlite.prepare('INSERT INTO identities VALUES (?, ?, ?, ?, ?, ?, ?)')
      .run(id, n, `contract-fixture-${n}`, now - 40 * 86_400_000, now, now, await digest(token));
    actors.push(token);
  }
  const [requester, contributor] = actors;
  async function send(path, method, token, body, responseSchema, status = 200, requestSchema) {
    if (requestSchema) matches(body, schemas[requestSchema]);
    const response = await worker.fetch(new Request(env.PUBLIC_ORIGIN + '/api/v1' + path, {
      method, headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.51',
        ...(token ? { authorization: `Bearer ${token}` } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}),
    }), env);
    const data = await response.json();
    assert.equal(response.status, status, JSON.stringify(data));
    assert.equal(response.headers.get('cache-control'), 'no-store');
    if (responseSchema) matches(data, schemas[responseSchema]);
    return data;
  }
  const get = (path, schema, token, status) => send(path, 'GET', token, undefined, schema, status);
  await get('', 'Discovery');
  await get('/work-items', 'WorkItemsPage');
  await get('/work-items/mine', 'Error', undefined, 401);
  const createBody = { client_request_id: randomUUID(), mission_id: 'build-the-commons',
    title: 'Check the shared HTTP contract', scope: 'Use an isolated fixture to inspect the complete work-item journey.',
    deliverable: 'A repeatable report naming the requests and their expected public responses.',
    acceptance: ['Each request and response matches the documented contract.'], terms: 'volunteer', public_consent: true };
  const made = await send('/work-items', 'POST', requester, createBody, 'WorkItemMutation', 202, 'WorkItemRequest');
  const id = made.item.id;
  await send('/work-items', 'POST', requester, createBody, 'WorkItemMutation', 200, 'WorkItemRequest');
  await get(`/work-items/${id}`, 'Error', undefined, 404);
  await get('/work-items/mine', 'OwnWorkItemsPage', requester);
  await send(`/admin/work-items/${id}`, 'PATCH', env.ADMIN_TOKEN,
    { expected_version: made.item.version, status: 'published' }, 'OwnWorkItem');
  await get('/work-items?mission_id=build-the-commons&state=ongoing&limit=1', 'WorkItemsPage');
  let item = await get(`/work-items/${id}`, 'WorkItem');
  async function act(token, action, fields = {}) {
    const body = { client_request_id: randomUUID(), expected_version: item.version, action, ...fields };
    const result = await send(`/work-items/${id}/actions`, 'POST', token, body,
      'WorkItemMutation', 200, 'WorkItemActionRequest');
    item = result.item;
    await get(`/work-items/${id}`, 'WorkItem');
    await get(`/work-items/mine/${id}`, 'OwnWorkItem', token);
    return { body, result };
  }
  await act(contributor, 'offer', { public_consent: true });
  await get('/work-items/mine', 'OwnWorkItemsPage', contributor);
  await act(requester, 'confirm');
  async function resultRevision() {
    const body = { client_request_id: randomUUID(), expected_version: item.version,
      kind: 'field-note', title: 'A repeatable contract report',
      summary: 'The isolated journey records matching responses and explicit participant decisions.',
      url: 'https://github.com/oss-singularity/website', public_consent: true };
    const result = await send(`/work-items/${id}/results`, 'POST', contributor, body,
      'WorkItemResultMutation', 202, 'WorkItemResultRequest');
    const replay = await send(`/work-items/${id}/results`, 'POST', contributor, body,
      'WorkItemResultMutation', 200, 'WorkItemResultRequest');
    assert.equal(replay.receipt, undefined);
    assert.equal(replay.result_id, result.result_id);
    await get(result.receipt.poll_url.replace('/api/v1', ''), 'Proposal', result.receipt.receipt_token);
    await send(`/admin/proposals/${result.receipt.id}`, 'PATCH', env.ADMIN_TOKEN,
      { status: 'published' }, 'Proposal');
    item = await get(`/work-items/mine/${id}`, 'OwnWorkItem', contributor);
    await act(contributor, 'deliver', { result_id: result.result_id });
    return result;
  }
  await resultRevision();
  await act(requester, 'request_revision');
  const revision = await resultRevision();
  const acknowledgement = await act(requester, 'acknowledge', { result_id: revision.result_id });
  assert.equal(item.last_delivered_revision, 2);
  assert.equal(item.state, 'acknowledged');
  await send(`/work-items/${id}/actions`, 'POST', requester, acknowledgement.body,
    'WorkItemMutation', 200, 'WorkItemActionRequest');
  await get('/work-items?state=acknowledged', 'WorkItemsPage');
  await send(`/admin/proposals/${revision.receipt.id}`, 'PATCH', env.ADMIN_TOKEN,
    { status: 'rejected' }, 'Proposal');
  const unavailable = await get(`/work-items/${id}`, 'WorkItem');
  assert.equal(unavailable.current_result_available, false);
  assert.equal(unavailable.acknowledged_result_id, revision.result_id);
  await get(`/work-items/mine/${id}`, 'OwnWorkItem', contributor);
});

test('work-action schema distinguishes offer consent, result decisions, and ordinary actions', () => {
  const schema = specification.components.schemas.WorkItemActionRequest;
  const base = { client_request_id: randomUUID(), expected_version: 1 };
  matches({ ...base, action: 'offer', public_consent: true }, schema);
  matches({ ...base, action: 'deliver', result_id: randomUUID() }, schema);
  matches({ ...base, action: 'cancel' }, schema);
  for (const invalid of [
    { ...base, action: 'offer' }, { ...base, action: 'offer', public_consent: false },
    { ...base, action: 'acknowledge' }, { ...base, action: 'cancel', result_id: randomUUID() },
    { ...base, action: 'confirm', requester_identity_id: randomUUID() },
  ]) assert.throws(() => matches(invalid, schema));
});
