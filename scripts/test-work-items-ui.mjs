import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../site/fragments/singularity.html', import.meta.url), 'utf8');
const sources = ['work-items-model-v1.js', 'work-items-v1.js'].map((file) => [file, readFileSync(new URL(`../site/assets/scripts/${file}`, import.meta.url), 'utf8')]);
const id = (n) => `${String(n).padStart(8, '0')}-1111-4111-8111-111111111111`;
const timestamp = '2026-09-05T12:00:00.000Z';
const actor = (n) => ({ identity_id: id(n), github_id: n, github_login: `fixture-${n}`, github_url: `https://github.com/fixture-${n}`, verification: 'github-account-control', verified_at: timestamp });
const result = (extra = {}) => ({ id: id(8), proposal_id: id(9), revision: 1, scope_version: 1, author_identity_id: id(3), status: 'published', kind: 'field-note', title: 'Keyboard report', summary: 'A report with repeatable steps and the observed behavior.', url: 'https://github.com/oss-singularity/website/pull/123', created_at: timestamp, published_at: timestamp, ...extra });
const item = (extra = {}) => ({ id: id(1), mission_id: 'build-the-commons', title: 'Check the keyboard journey', scope: 'Inspect the keyboard journey within the selected local mission.', deliverable: 'A report with the exact steps and limits of the findings.', acceptance: ['The report provides a repeatable sequence.'], terms: 'volunteer', scope_version: 1, version: 4, last_delivered_revision: 0, moderation: 'published', state: 'open', requester: actor(2), contributor: null, created_at: timestamp, updated_at: timestamp, published_at: timestamp, expires_at: '2026-12-04T12:00:00.000Z', offer_expires_at: null, ended_at: null, current_result_id: null, current_result_available: false, acknowledged_result_id: null, acknowledged_at: null, results: [], events: [], ...extra });
const own = (extra = {}) => item({ viewer: { requester: false, candidate: false, contributor: true, past_participant: false }, parent_available: true, offer: null, allowed_actions: [], own_results: [], ...extra });
const envelope = (value, extra = {}) => ({ item: value, operation: { id: id(20), applied_version: value.version, replayed: false }, ...extra });
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { for (let n = 0; n < 40; n += 1) await Promise.resolve(); };

function harness({ route, mission = 'build-the-commons' } = {}) {
  const elements = new Map(), documentEvents = new Map(), windowEvents = new Map(), requests = [], blobs = new Map(), downloads = [], timers = new Map();
  let sequence = 30;
  class Element {
    constructor(tag, idValue = '') { this.tagName = tag.toUpperCase(); this.id = idValue; this.children = []; this.events = new Map(); this.attributes = {}; this.dataset = {}; this.value = ''; this.checked = false; this.disabled = false; this.hidden = false; this._text = ''; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
    append(...children) { this.children.push(...children); if (this.tagName === 'SELECT' && !this.value) this.value = children[0]?.value || ''; }
    replaceChildren(...children) { this.children = children; this._text = ''; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(type, listener) { this.events.set(type, listener); }
    focus() { this.focused = true; }
    remove() {}
    click() { if (this.download) downloads.push({ href: this.href, filename: this.download }); else return this.events.get('click')?.({ preventDefault() {} }); }
  }
  for (const match of html.matchAll(/<([a-z][a-z0-9-]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)) { const e = new Element(match[1], match[2]); e.hidden = /\bhidden\b/.test(match[0]); e.disabled = /\bdisabled\b/.test(match[0]); elements.set(e.id, e); }
  elements.get('room-context').dataset.missionId = mission;
  const walk = (n) => [n, ...(n.children || []).flatMap(walk)];
  const get = (name) => { const exact = name.startsWith('room-') ? name : `work-${name}`; return elements.get(exact) || [...elements.values()].flatMap(walk).find((e) => e.id === exact); };
  const document = { getElementById: (name) => get(name.startsWith('work-') ? name.slice(5) : name), createElement: (tag) => new Element(tag), createTextNode: (value) => ({ textContent: value }), body: new Element('body'), addEventListener: (name, listener) => documentEvents.set(name, listener) };
  const window = { location: new URL('https://oss-singularity.io/singularity/?token=PRIVATE-QUERY#private'), addEventListener: (name, listener) => windowEvents.set(name, listener), setTimeout: (fn, delay) => { const n = ++sequence; timers.set(n, { fn, delay }); return n; }, clearTimeout: (n) => timers.delete(n) };
  class TestURL extends URL {}
  TestURL.createObjectURL = (blob) => { const n = `blob:test-${++sequence}`; blobs.set(n, blob); return n; };
  TestURL.revokeObjectURL = (url) => blobs.delete(url);
  const fetch = async (path, options) => { requests.push({ path, options }); const response = await route?.(path, options) || { body: { items: [], next_cursor: null } }; return { ok: (response.status || 200) < 400, status: response.status || 200, json: async () => response.body }; };
  const context = vm.createContext({ window, document, fetch, URL: TestURL, URLSearchParams, AbortController, Blob, crypto: { randomUUID: () => id(++sequence) } });
  for (const [filename, source] of sources) vm.runInContext(source, context, { filename });
  const h = { get, requests, blobs, downloads, timers, model: window.OssWorkItems, text: (name) => get(name).textContent,
    fire: (name, event = 'click') => get(name).events.get(event)?.({ preventDefault() {} }),
    choose: (value) => { elements.get('room-context').dataset.missionId = value || ''; documentEvents.get('singularity:mission')({ detail: value ? { id: value, title: 'ignored' } : null }); },
    token: (value = 'a'.repeat(43)) => { get('room-identity-token').value = value; get('room-identity-token').events.get('input')?.({}); },
    button: (label, box = 'private-detail') => walk(get(box)).find((n) => n.tagName === 'BUTTON' && n.textContent === label),
    pagehide: () => windowEvents.get('pagehide')({}), pageshow: () => windowEvents.get('pageshow')({ persisted: true }),
  };
  return h;
}
async function publicOpen(h) { await flush(); await h.button('Read scope & decisions', 'list').click(); await flush(); }
async function privateOpen(h) { h.token(); h.fire('mine-load'); await flush(); await h.button('Open my work & decisions', 'mine').click(); await flush(); }
const routes = (value, overrides) => (path, options) => overrides?.(path, options) || (path.includes('/mine/') ? { body: value } : path.includes('/mine?') ? { body: { items: [value], next_cursor: null } } : path.includes('?') ? { body: { items: [item()], next_cursor: null } } : { body: item() });

test('public browsing and identity edits send no credentials; private reads require explicit action', async () => {
  const h = harness({ route: routes(own()) }); await publicOpen(h); h.token(); await flush();
  assert.equal(h.requests.length, 2); assert.ok(h.requests.every(({ options }) => !options.headers.Authorization));
  h.fire('mine-load'); await flush(); assert.equal(h.requests.at(-1).options.headers.Authorization, `Bearer ${'a'.repeat(43)}`);
  for (const { path, options } of h.requests) { assert.ok(!path.includes('token')); assert.equal(options.cache, 'no-store'); assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error'); }
});

test('real empty, error, retry and cursor pagination keep each list scoped', async () => {
  let step = 0;
  const h = harness({ route: (path) => {
    if (step === 0) return { body: { items: [], next_cursor: null } };
    if (step === 1) return { status: 503, body: { error: { code: 'service_unavailable', message: 'Service unavailable' } } };
    return { body: { items: [item({ id: path.includes('cursor=') ? id(4) : id(1) })], next_cursor: path.includes('cursor=') ? null : `${Date.parse(timestamp)}:${id(1)}` } };
  } });
  await flush(); assert.match(h.text('list-status'), /No published/);
  step = 1; h.fire('refresh'); await flush(); assert.match(h.text('list-status'), /Refresh work to retry/);
  step = 2; h.fire('refresh'); await flush(); assert.equal(h.get('more').hidden, false);
  h.fire('more'); await flush(); assert.equal(h.get('list').children.length, 2); assert.equal(h.get('more').hidden, true);
  assert.ok(h.requests.every(({ path }) => path.includes('mission_id=build-the-commons')));
});

test('reordered mission and detail responses cannot reintroduce old data', async () => {
  const late = deferred();
  const h = harness({ route: (path) => path.includes('mission_id=build-the-commons') ? late.promise : { body: { items: [item({ mission_id: 'other-mission' })], next_cursor: null } } });
  h.choose('other-mission'); await flush(); late.resolve({ body: { items: [item()], next_cursor: null } }); await flush();
  assert.ok(h.requests[0].options.signal.aborted); assert.equal(h.get('list').children.length, 1);
  const delayed = deferred(); let wait = false;
  const d = harness({ route: routes(own(), (path) => wait && !path.includes('?') ? delayed.promise : undefined) }); await flush(); wait = true;
  d.button('Read scope & decisions', 'list').click(); await flush(); d.choose(null); delayed.resolve({ body: item() }); await flush();
  assert.equal(d.get('detail').hidden, true); assert.equal(d.text('detail'), '');
});

test('a changed identity discards pending private reads and every private draft', async () => {
  const late = deferred(); const h = harness({ route: routes(own(), (path) => path.includes('/mine?') ? late.promise : undefined) });
  h.token(); h.get('create-title').value = 'Private unsaved draft'; h.fire('mine-load'); await flush(); h.token('b'.repeat(43));
  late.resolve({ body: { items: [own({ title: 'PRIVATE OLD ACCOUNT' })], next_cursor: null } }); await flush();
  assert.equal(h.text('mine'), ''); assert.equal(h.get('create-title').value, ''); assert.ok(!h.text('mine-status').includes('PRIVATE'));
});

test('nonmember can explicitly offer with consent; body has exact CAS and no injected role', async () => {
  const h = harness({ route: routes(own(), (path, options) => options.method === 'POST' ? { body: envelope(own({ state: 'offered', version: 5 })) } : undefined) });
  await publicOpen(h); h.token(); h.button('Offer to do this', 'detail').click(); await flush(); assert.equal(h.requests.filter((r) => r.options.method).length, 0);
  h.get('offer-consent').checked = true; await h.button('Offer to do this', 'detail').click(); await flush();
  const post = h.requests.find((r) => r.options.method); assert.equal(post.path, `/api/v1/work-items/${id(1)}/actions`);
  const body = JSON.parse(post.options.body); assert.deepEqual(Object.keys(body).sort(), ['action', 'client_request_id', 'expected_version', 'public_consent']);
  assert.equal(body.action, 'offer'); assert.equal(body.expected_version, 4); assert.equal(body.public_consent, true);
});

test('create validates consent/criteria, fixes voluntary scope, and recovers the actual pending item', async () => {
  const h = harness({ route: routes(own(), (_path, options) => options.method ? { body: envelope(own({ moderation: 'pending', version: 1 })) } : undefined) }); await flush(); h.token();
  for (const [key, value] of Object.entries({ title: 'Check one path', scope: 'Inspect the complete keyboard journey.', deliverable: 'A reproducible report of the exact sequence.', acceptance: 'The sequence names the tested controls.' })) h.get(`create-${key}`).value = value;
  h.fire('create-form', 'submit'); await flush(); assert.equal(h.requests.filter((r) => r.options.method).length, 0);
  h.get('create-consent').checked = true; h.fire('create-form', 'submit'); await flush();
  const post = h.requests.find((r) => r.options.method), body = JSON.parse(post.options.body);
  assert.equal(post.path, '/api/v1/work-items'); assert.equal(body.terms, 'volunteer'); assert.equal(body.mission_id, 'build-the-commons'); assert.deepEqual(body.acceptance, ['The sequence names the tested controls.']);
  assert.equal(h.get('create-title').value, ''); assert.match(h.text('private-detail'), /pending/);
});

test('requester decisions use server permissions and acknowledge the exact current available result', async () => {
  for (const [label, action] of [['Confirm this contributor', 'confirm'], ['Decline offer', 'decline'], ['Request another revision', 'request_revision'], ['Cancel this work item', 'cancel'], ['Acknowledge this exact delivery', 'acknowledge']]) {
    const value = own({ state: 'delivered', allowed_actions: [action], current_result_id: id(8), current_result_available: true, results: [result()], contributor: actor(3) });
    const h = harness({ route: routes(value, (_path, options) => options.method ? { body: envelope(value) } : undefined) }); await privateOpen(h); await h.button(label).click(); await flush();
    const body = JSON.parse(h.requests.find((r) => r.options.method).options.body); assert.equal(body.action, action); assert.equal(body.expected_version, 4);
    assert.equal(body.result_id, action === 'acknowledge' ? id(8) : undefined);
  }
  const h = harness({ route: routes(own({ allowed_actions: ['acknowledge'], current_result_id: id(8), current_result_available: false })) }); await privateOpen(h);
  assert.equal(h.button('Acknowledge this exact delivery'), undefined); assert.match(h.text('private-detail'), /unavailable/);
});

test('a valid-length Unicode draft over the wire limit is kept without sending a write', async () => {
  const h = harness(); await flush(); h.token();
  h.get('create-title').value = 'A bounded task'; h.get('create-scope').value = '💎'.repeat(2000); h.get('create-deliverable').value = '💎'.repeat(1000); h.get('create-acceptance').value = 'One repeatable check.'; h.get('create-consent').checked = true;
  h.fire('create-form', 'submit'); await flush();
  assert.equal(h.requests.filter((r) => r.options.method).length, 0); assert.match(h.text('action-status'), /8 KB/); assert.equal([...h.get('create-scope').value].length, 2000);
});

test('delivery only offers a higher published revision, never a pending or prior result', async () => {
  const fresh = result({ id: id(10), revision: 3 }), value = own({ state: 'revision_requested', allowed_actions: ['deliver'], last_delivered_revision: 1, own_results: [result(), result({ id: id(11), status: 'pending', revision: 2, published_at: null }), fresh] });
  const h = harness({ route: routes(value, (_path, options) => options.method ? { body: envelope(value) } : undefined) }); await privateOpen(h);
  assert.equal(h.get('delivery-result').children.length, 1); assert.equal(h.get('delivery-result').value, fresh.id);
  await h.button('Deliver selected published result').click(); await flush();
  assert.equal(JSON.parse(h.requests.find((r) => r.options.method).options.body).result_id, fresh.id);
});

test('current delivery identifies revision one and its source even when revision two was published later', async () => {
  const first = result(), newer = result({ id: id(10), revision: 2, title: 'A later publication', url: 'https://github.com/oss-singularity/website/pull/124' });
  const value = own({ state: 'delivered', allowed_actions: ['acknowledge'], last_delivered_revision: 1, current_result_id: first.id, current_result_available: true, results: [newer, first] });
  const h = harness({ route: routes(value, (_path, options) => options.method ? { body: envelope(own({ ...value, state: 'acknowledged', acknowledged_result_id: first.id, acknowledged_at: timestamp, allowed_actions: [] })) } : undefined) });
  await privateOpen(h);
  const children = h.get('private-detail').children;
  assert.ok(children.some((n) => n.tagName === 'H5' && n.textContent === 'Current delivery · revision 1'));
  assert.match(h.text('private-detail'), new RegExp(`Acknowledgement target: revision 1, result ${first.id}`));
  const articles = children.filter((n) => n.tagName === 'ARTICLE');
  assert.match(articles[0].textContent, /Keyboard report/); assert.equal(articles[0].children.find((n) => n.tagName === 'A').href, first.url);
  const otherHeading = children.findIndex((n) => n.tagName === 'H5' && n.textContent === 'Other results & your own submissions');
  assert.ok(otherHeading > children.indexOf(articles[0])); assert.ok(children.indexOf(articles[1]) > otherHeading);
  await h.button('Acknowledge this exact delivery').click(); await flush();
  assert.equal(JSON.parse(h.requests.find((r) => r.options.method).options.body).result_id, first.id);
  assert.match(h.text('private-detail'), new RegExp(`Requester acknowledged revision 1, result ${first.id}`));
});

test('a retained history beyond 150 events stays readable and an exact action replay succeeds', async () => {
  const events = Array.from({ length: 153 }, (_, i) => ({ id: id(100 + i), version: i + 1, action: i % 2 ? 'offer' : 'withdraw_offer', actor_kind: 'identity', actor_identity_id: id(3), result_id: null, created_at: timestamp }));
  const value = own({ state: 'offered', version: 154, allowed_actions: ['withdraw_offer'], events });
  const h = harness({ route: routes(value, (_path, options) => options.method ? { body: envelope(value, { operation: { id: id(20), applied_version: 153, replayed: true } }) } : undefined) });
  await privateOpen(h); assert.equal(h.get('private-detail').hidden, false); assert.ok(h.button('Withdraw my offer'));
  await h.button('Withdraw my offer').click(); await flush();
  assert.match(h.text('action-status'), /Exact operation recovered/); assert.equal(h.get('private-detail').hidden, false); assert.equal(h.get('uncertain').hidden, true);
});

test('result submission derives no mission/author/old proposal and exposes a once-only receipt', async () => {
  const value = own({ state: 'active', allowed_actions: ['submit_result'] });
  const h = harness({ route: routes(value, (_path, options) => options.method ? { body: envelope(own({ state: 'active', own_results: [result({ status: 'pending', published_at: null })] }), { receipt: { id: id(9), status: 'pending', poll_url: `/api/v1/proposals/${id(9)}`, receipt_token: 'r'.repeat(43) } }) } : undefined) }); await privateOpen(h);
  h.get('result-title').value = 'A concrete check'; h.get('result-summary').value = 'The observed result and full repeatable keyboard sequence.'; h.get('result-url').value = 'https://github.com/oss-singularity/website/pull/123'; h.get('result-consent').checked = true;
  const submit = h.button('Send result for publication review');
  const form = h.get('private-detail').children.find((n) => n.tagName === 'FORM'); assert.ok(submit); await form.events.get('submit')({ preventDefault() {} }); await flush();
  const post = h.requests.find((r) => r.options.method), body = JSON.parse(post.options.body); assert.ok(post.path.endsWith('/results'));
  assert.deepEqual(Object.keys(body).sort(), ['client_request_id', 'expected_version', 'kind', 'public_consent', 'summary', 'title', 'url']); assert.equal(h.get('receipt-panel').hidden, false);
  h.fire('receipt-download'); assert.equal(h.downloads.length, 1); h.token(); assert.equal(h.text('receipt'), ''); assert.equal(h.blobs.size, 0);
});

test('uncertain writes never auto-retry and the explicit retry preserves operation ID and exact body', async () => {
  let posts = 0;
  const h = harness({ route: routes(own(), (_path, options) => { if (options.method) { posts += 1; if (posts === 1) throw new Error('Lost response'); return { body: envelope(own({ state: 'offered' }), { operation: { id: id(20), applied_version: 5, replayed: true } }) }; } return undefined; }) });
  await publicOpen(h); h.token(); h.get('offer-consent').checked = true; await h.button('Offer to do this', 'detail').click(); await flush();
  assert.equal(posts, 1); assert.equal(h.get('uncertain').hidden, false); h.fire('mine-load'); await flush(); assert.equal(posts, 1);
  await h.fire('retry-write'); await flush(); assert.equal(posts, 2); const writes = h.requests.filter((r) => r.options.method); assert.equal(writes[0].options.body, writes[1].options.body); assert.match(h.text('action-status'), /recovered/);
});

test('a token change or pagehide during a write rejects late private success and clears secrets', async () => {
  for (const change of ['token', 'pagehide']) {
    const late = deferred(); const h = harness({ route: routes(own(), (_path, options) => options.method ? late.promise : undefined) }); await publicOpen(h); h.token(); h.get('offer-consent').checked = true;
    h.button('Offer to do this', 'detail').click(); await flush(); h[change](); late.resolve({ body: envelope(own({ title: 'PRIVATE STALE RESPONSE' })) }); await flush();
    assert.equal(h.text('private-detail'), ''); assert.ok(!h.text('action-status').includes('Decision saved')); if (change === 'pagehide') assert.equal(h.get('room-identity-token').value, '');
  }
});

test('mission changes preserve honest recovery guidance for an in-flight or uncertain write and discard late success', async () => {
  for (const uncertain of [false, true]) {
    const late = deferred(); const h = harness({ route: routes(own(), (_path, options) => options.method ? late.promise : undefined) });
    await publicOpen(h); h.token(); h.get('offer-consent').checked = true; h.button('Offer to do this', 'detail').click(); await flush();
    if (uncertain) { late.reject(new Error('Lost response')); await flush(); assert.equal(h.get('uncertain').hidden, false); }
    h.choose(null); h.choose('other-mission');
    assert.match(h.text('action-status'), /may have been saved/); assert.match(h.text('action-status'), /Load my work with the original identity/); assert.ok(!h.text('action-status').includes('no authenticated request'));
    if (!uncertain) { late.resolve({ body: envelope(own({ title: 'PRIVATE STALE RESPONSE' })) }); await flush(); }
    assert.equal(h.text('private-detail'), ''); assert.equal(h.get('uncertain').hidden, true); assert.match(h.text('action-status'), /unresolved operation/);
    assert.equal(h.requests.filter((r) => r.options.method).length, 1);
  }
});

test('409 clears obsolete action controls and requires a deliberate fresh read', async () => {
  const value = own({ allowed_actions: ['confirm'] });
  const h = harness({ route: routes(value, (_path, options) => options.method ? { status: 409, body: { error: { code: 'version_conflict', message: 'Version changed' } } } : undefined) }); await privateOpen(h); const before = h.requests.length;
  await h.button('Confirm this contributor').click(); await flush(); assert.equal(h.requests.length, before + 1); assert.equal(h.get('private-detail').hidden, true); assert.match(h.text('action-status'), /Load your work/); assert.equal(h.get('uncertain').hidden, true);
});

test('BFCache restoration preserves unresolved-write recovery guidance without private data or automatic retry', async () => {
  const late = deferred(); const h = harness({ route: routes(own(), (_path, options) => options.method ? late.promise : undefined) });
  await publicOpen(h); h.token(); h.get('offer-consent').checked = true; h.button('Offer to do this', 'detail').click(); await flush();
  h.pagehide(); h.pageshow(); await flush();
  assert.equal(h.get('room-identity-token').value, ''); assert.equal(h.text('private-detail'), ''); assert.equal(h.get('uncertain').hidden, true);
  assert.match(h.text('action-status'), /may have been saved/); assert.match(h.text('action-status'), /Load my work with the original identity/); assert.ok(!h.text('action-status').includes('no authenticated request'));
  late.resolve({ body: envelope(own({ title: 'PRIVATE LATE SUCCESS' })) }); await flush();
  assert.equal(h.text('private-detail'), ''); assert.match(h.text('action-status'), /unresolved operation/); assert.equal(h.requests.filter((r) => r.options.method).length, 1);
  assert.equal(h.requests.at(-1).options.headers.Authorization, undefined);
});

test('public export fetches fresh without auth and strips private keys, candidate events, URL context and pending results', async () => {
  const secret = 'PRIVATE-SHOULD-NEVER-EXPORT'; const publicValue = item({ results: [result()], events: [{ id: id(12), version: 3, action: 'offer', actor_identity_id: id(5), created_at: timestamp, secret }, { id: id(13), version: 4, action: 'confirm', actor_identity_id: id(2), created_at: timestamp, result_id: null, secret }], own_results: [result({ summary: secret, status: 'pending' })], offer: { secret }, receipt_token: secret, client_request_id: secret, viewer: { secret } });
  const h = harness({ route: routes(own(), (path) => !path.includes('?') ? { body: publicValue } : undefined) }); await publicOpen(h); h.token(); const before = h.requests.length;
  await h.button('Download public work JSON', 'detail').click(); await flush(); assert.equal(h.requests.length, before + 1); assert.equal(h.requests.at(-1).options.headers.Authorization, undefined);
  const raw = await h.blobs.get(h.downloads[0].href).text(), packet = JSON.parse(raw);
  assert.ok(!raw.includes(secret) && !raw.includes('PRIVATE-QUERY') && !raw.includes('client_request_id') && !raw.includes('own_results') && !raw.includes('receipt_token'));
  assert.equal(packet.work_item.events.length, 1); assert.equal(packet.work_item.events[0].action, 'confirm'); assert.equal(packet.work_item.results.length, 1); assert.equal(packet.references.room, 'https://oss-singularity.io/singularity/?mission=build-the-commons');
  h.pagehide(); assert.equal(h.blobs.size, 0);
});

test('withdrawn public export fails closed, hostile text stays literal, BFCache restarts only public reads', async () => {
  let withdrawn = false; const hostile = '</h4><script>private()</script>';
  const h = harness({ route: routes(own(), (path) => !path.includes('?') ? withdrawn ? { status: 404, body: { error: { code: 'not_found', message: 'Unavailable' } } } : { body: item({ title: hostile }) } : undefined) }); await publicOpen(h);
  const heading = h.get('detail').children.find((n) => n.tagName === 'H4'); assert.equal(heading.textContent, hostile); assert.equal(heading.children.length, 0);
  withdrawn = true; await h.button('Download public work JSON', 'detail').click(); await flush(); assert.equal(h.downloads.length, 0); assert.match(h.text('detail-status'), /Export unavailable/);
  h.token(); h.pagehide(); const count = h.requests.length; h.pageshow(); await flush(); assert.equal(h.requests.length, count + 1); assert.equal(h.requests.at(-1).options.headers.Authorization, undefined); assert.equal(h.get('create-consent').checked, false);
});
