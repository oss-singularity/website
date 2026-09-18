import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../site/fragments/singularity.html', import.meta.url), 'utf8');
const sources = ['projects-model-v1.js', 'projects-v1.js', 'projects-deliveries-v1.js'].map((file) => [file, readFileSync(new URL(`../site/assets/scripts/${file}`, import.meta.url), 'utf8')]);
const id = (n) => `${String(n).padStart(8, '0')}-1111-4111-8111-111111111111`;
const timestamp = '2026-09-16T12:00:00.000Z';
const actor = (n) => ({ identity_id: id(n), github_id: n, github_login: `fixture-${n}`, github_url: `https://github.com/fixture-${n}`, verification: 'github-account-control', verified_at: timestamp });
const milestone = (extra = {}) => ({ id: id(30), project_id: id(1), parent_milestone_id: null, title: 'Digest verifier', purpose: 'Make artifact verification usable for independent reviewers.', expected_artifact: 'A delivery manifest with raw-file digest instructions.', acceptance: ['Two independent verifications of the same synthetic bytes.'], status: 'open', scope_version: 1, version: 1, created_at: timestamp, updated_at: timestamp, depends_on: [], blocked: false, ...extra });
const commitment = (extra = {}) => ({ id: id(40), milestone_id: id(30), project_id: id(1), status: 'confirmed', terms: 'volunteer', scope_version: 1, created_at: timestamp, updated_at: timestamp, contributor: actor(5), coordinator: actor(2), ...extra });
const project = (extra = {}) => ({ id: id(1), mission_id: 'build-the-commons', title: 'Verification toolkit', purpose: 'Make artifact verification usable for independent reviewers everywhere.', status: 'open', version: 3, coordinator: actor(2), created_at: timestamp, updated_at: timestamp, milestones: [milestone()], commitments: [commitment()], ...extra });
const exportPacket = (extra = {}) => ({ schema_version: 1, kind: 'oss-project-export', exported_at: timestamp,
  project: { id: id(1), mission_id: 'build-the-commons', title: 'Verification toolkit', purpose: 'Make artifact verification usable for independent reviewers everywhere.', status: 'open', version: 3, scope_version: 1, coordinator: actor(2), created_at: timestamp, updated_at: timestamp },
  milestones: [milestone()], commitments: [{ id: id(40), milestone_id: id(30), contributor: actor(5), coordinator: actor(2), status: 'confirmed', terms: 'volunteer', scope_version: 1, created_at: timestamp, updated_at: timestamp }],
  notice: 'An export records coordination decisions and identities; it verifies no artifact and authorizes no payment.', ...extra });
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { for (let n = 0; n < 40; n += 1) await Promise.resolve(); };

function harness({ route, mission = 'build-the-commons' } = {}) {
  const elements = new Map(), documentEvents = new Map(), windowEvents = new Map(), requests = [], blobs = new Map(), downloads = [], timers = new Map();
  let sequence = 30;
  class Element {
    constructor(tag, idValue = '') { this.tagName = tag.toUpperCase(); this.id = idValue; this.children = []; this.events = new Map(); this.attributes = {}; this.dataset = {}; this.value = ''; this.checked = false; this.disabled = false; this.hidden = false; this._text = ''; }
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
    querySelectorAll(selector) { const cls = selector.startsWith('.') ? selector.slice(1) : null; return walk(this).filter((n) => cls ? (n.className || '').split(/\s+/).includes(cls) : false); }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
    append(...children) { this.children.push(...children); }
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
  const get = (name) => elements.get(name) || [...elements.values()].flatMap(walk).find((e) => e.id === name);
  const document = { getElementById: (name) => get(name), createElement: (tag) => new Element(tag), createTextNode: (value) => ({ textContent: value }), body: new Element('body'), addEventListener: (name, listener) => documentEvents.set(name, listener), dispatchEvent: (event) => { const listener = documentEvents.get(event.type); if (listener) listener(event); }, querySelectorAll: (selector) => selector === '#project-detail article[data-milestone-id]' ? walk(get('project-detail')).filter((n) => n.tagName === 'ARTICLE' && n.dataset?.milestoneId) : [] };
  const window = { location: new URL('https://oss-singularity.io/singularity/?mission=build-the-commons'), addEventListener: (name, listener) => windowEvents.set(name, listener), setTimeout: (fn, delay) => { const n = ++sequence; timers.set(n, { fn, delay }); return n; }, clearTimeout: (n) => timers.delete(n) };
  class TestURL extends URL {}
  TestURL.createObjectURL = (blob) => { const n = `blob:test-${++sequence}`; blobs.set(n, blob); return n; };
  TestURL.revokeObjectURL = (url) => blobs.delete(url);
  const fetch = async (path, options) => { requests.push({ path, options }); const response = await route?.(path, options) || { body: { items: [], next_cursor: null } }; return { ok: (response.status || 200) < 400, status: response.status || 200, json: async () => response.body }; };
  class TestCustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } }
  const context = vm.createContext({ window, document, fetch, URL: TestURL, URLSearchParams, AbortController, Blob, CustomEvent: TestCustomEvent, crypto: { randomUUID: () => id(++sequence) } });
  for (const [filename, source] of sources) vm.runInContext(source, context, { filename });
  const h = { get, walk, requests, blobs, downloads, timers, model: window.OssProjects, text: (name) => get(name).textContent,
    fire: (name, event = 'click') => get(name).events.get(event)?.({ preventDefault() {} }),
    choose: (value) => { elements.get('room-context').dataset.missionId = value || ''; documentEvents.get('singularity:mission')({ detail: value ? { id: value, title: 'ignored' } : null }); },
    token: (value = 'a'.repeat(43)) => { get('room-identity-token').value = value; get('room-identity-token').events.get('input')?.({}); },
    button: (label, box = 'project-detail') => walk(get(box)).find((n) => n.tagName === 'BUTTON' && n.textContent === label),
    pagehide: () => windowEvents.get('pagehide')({}), pageshow: () => windowEvents.get('pageshow')({ persisted: true }),
  };
  return h;
}
async function publicOpen(h) {
  await flush();
  await h.button('Read milestones & commitments', 'projects-list').click();
  await flush();
}
const routes = (value, overrides) => (path, options) => overrides?.(path, options)
  || (path.startsWith('/api/v1/projects?') ? { body: { items: [value], next_cursor: null } }
    : path.includes('/export') ? { body: exportPacket() } : { body: value });

test('public browsing sends no credentials, stays mission-scoped and hides offered commitments', async () => {
  const gated = milestone({ id: id(31), depends_on: [id(30)], blocked: true, title: 'CID explainer' });
  const child = milestone({ id: id(32), parent_milestone_id: id(30), title: 'Verifier subpart' });
  const value = project({ title: '</h4><script>private()</script>', milestones: [milestone(), gated, child], commitments: [commitment()] });
  const h = harness({ route: routes(value) });
  await publicOpen(h);
  assert.ok(h.requests.filter(({ path }) => path.includes('/api/v1/projects?')).every(({ path }) => path.includes('mission_id=build-the-commons')));
  assert.ok(h.requests.every(({ options }) => options.credentials === 'omit' && options.cache === 'no-store' && options.redirect === 'error' && options.headers.Authorization === undefined));
  const heading = h.get('project-detail').children.find((n) => n.tagName === 'H4');
  assert.equal(heading.textContent, '</h4><script>private()</script>'); assert.equal(heading.children.length, 0);
  assert.match(h.text('project-detail'), /blocked by a dependency gate/);
  assert.match(h.text('project-detail'), /subproject part/);
  assert.doesNotMatch(h.text('project-detail'), /Offer awaiting coordinator/);
  assert.match(h.text('project-detail'), /Bound commitment/);
});

test('real empty, error, retry and cursor pagination keep the project list honest', async () => {
  let step = 0;
  const h = harness({ route: (path) => {
    if (step === 0) return { body: { items: [], next_cursor: null } };
    if (step === 1) return { status: 503, body: { error: { code: 'service_unavailable', message: 'Service unavailable' } } };
    return { body: { items: [project({ id: path.includes('cursor=') ? id(9) : id(1) })], next_cursor: path.includes('cursor=') ? null : `${Date.parse(timestamp)}:${id(1)}` } };
  } });
  await flush(); assert.match(h.text('projects-list-status'), /No coordinated projects/);
  step = 1; h.fire('projects-refresh'); await flush(); assert.match(h.text('projects-list-status'), /Refresh projects to retry/);
  assert.equal(h.get('projects-list').children.length, 0);
  step = 2; h.fire('projects-refresh'); await flush(); assert.equal(h.get('projects-more').hidden, false);
  h.fire('projects-more'); await flush(); assert.equal(h.get('projects-list').querySelectorAll('.room-entry').length, 2); assert.equal(h.get('projects-more').hidden, true);
});

test('the project list filters by status without refetching and keeps entries intact', async () => {
  const openProject = project({ id: id(1), status: 'open', title: 'Open one' });
  const closedProject = project({ id: id(2), status: 'closed', title: 'Closed one' });
  const h = harness({ route: (path) => path.startsWith('/api/v1/projects?') ? { body: { items: [openProject, closedProject], next_cursor: null } } : { body: openProject } });
  await flush();
  const chips = () => h.get('projects-list').querySelectorAll('.chip');
  assert.equal(chips().length, 3);
  assert.equal(h.get('projects-list').querySelectorAll('.room-entry').length, 2);
  const closedChip = chips().find((n) => n.textContent === 'Closed (1)');
  assert.equal(closedChip.attributes['aria-pressed'], 'false');
  closedChip.click(); await flush();
  assert.equal(h.get('projects-list').querySelectorAll('.room-entry').length, 1);
  assert.match(h.text('projects-list'), /Closed one/); assert.doesNotMatch(h.text('projects-list'), /Open one/);
  assert.equal(chips().find((n) => n.textContent === 'Closed (1)').attributes['aria-pressed'], 'true');
  chips().find((n) => n.textContent === 'All (2)').click(); await flush();
  assert.equal(h.get('projects-list').querySelectorAll('.room-entry').length, 2);
});

test('delivered milestones collapse to their headline but keep the record inspectable', async () => {
  const done = milestone({ id: id(31), status: 'done', title: 'Shipped slice', purpose: 'The delivered purpose line.' });
  const h = harness({ route: routes(project({ milestones: [done] })) });
  await publicOpen(h);
  const detail = h.get('project-detail');
  const details = h.walk(detail).filter((n) => n.tagName === 'DETAILS');
  assert.equal(details.length, 1);
  assert.match(details[0].textContent, /Show the delivered record/);
  assert.match(details[0].textContent, /The delivered purpose line./);
  assert.match(h.text('project-detail'), /Acceptance criteria/); // record stays in the DOM
});

test('reordered mission and detail responses cannot reintroduce old projects', async () => {
  const late = deferred();
  const h = harness({ route: (path) => path.startsWith('/api/v1/projects?') && path.includes('build-the-commons') ? late.promise : { body: { items: [project({ mission_id: 'other-mission' })], next_cursor: null } } });
  h.choose('other-mission'); await flush(); late.resolve({ body: { items: [project()], next_cursor: null } }); await flush();
  assert.ok(h.requests[0].options.signal.aborted); assert.equal(h.get('projects-list').querySelectorAll('.room-entry').length, 1);
  const delayed = deferred(); let wait = false;
  const d = harness({ route: routes(project(), (path) => wait && !path.includes('?') ? delayed.promise : undefined) });
  await flush(); wait = true;
  d.button('Read milestones & commitments', 'projects-list').click(); await flush(); d.choose(null); delayed.resolve({ body: project() }); await flush();
  assert.equal(d.get('project-detail').hidden, true); assert.equal(d.text('project-detail'), '');
});

test('an offer needs explicit consent, sends exact voluntary terms and then loads the participant view', async () => {
  const offered = commitment({ id: id(41), status: 'offered', contributor: actor(5) });
  const h = harness({ route: routes(project(), (path, options) => {
    if (options?.method === 'POST') return { body: offered };
    if (options?.headers?.Authorization && !path.includes('?')) return { body: project({ commitments: [offered, commitment()] }) };
    return undefined;
  }) });
  await publicOpen(h); h.token();
  h.button('Offer to take this milestone').click(); await flush();
  h.button('Send voluntary offer').click(); await flush();
  assert.equal(h.requests.filter((r) => r.options.method).length, 0);
  assert.match(h.text('projects-action-status'), /consent/i);
  h.get('project-offer-consent').checked = true;
  h.button('Send voluntary offer').click(); await flush();
  const post = h.requests.find((r) => r.options.method);
  assert.equal(post.path, `/api/v1/projects/${id(1)}/commitments`);
  assert.deepEqual(JSON.parse(post.options.body), { milestone_id: id(30), terms: 'volunteer' });
  assert.equal(post.options.headers.Authorization, `Bearer ${'a'.repeat(43)}`);
  assert.match(h.text('projects-action-status'), /Offer sent/);
  assert.match(h.text('project-detail'), /PARTICIPANT VIEW/);
  assert.match(h.text('project-detail'), /Offer awaiting coordinator/);
});

test('coordinator and contributor act only through the permitted explicit transitions', async () => {
  const offered = commitment({ id: id(41), status: 'offered', contributor: actor(5) });
  const cases = [
    [{ coordinator: true }, [['Confirm this contributor', 'confirm', id(41)], ['End this commitment', 'end', id(40)]]],
    [undefined, [['Withdraw my offer', 'withdraw', id(41)]]],
  ];
  for (const [viewer, actions] of cases) {
    const publicValue = project({ commitments: [commitment()] });
    const value = project({ viewer, commitments: [offered, commitment()] });
    const h = harness({ route: (path, options) => {
      if (options?.method) return { body: commitment({ id: id(41), status: 'confirmed' }) };
      if (options?.headers?.Authorization && !path.includes('?')) return { body: value };
      if (path.startsWith('/api/v1/projects?')) return { body: { items: [publicValue], next_cursor: null } };
      return { body: publicValue };
    } });
    await publicOpen(h); h.token(); await h.button('Load participant view').click(); await flush();
    let posts = 0;
    for (const [label, action, commitmentId] of actions) {
      const button = h.button(label); assert.ok(button, `${label} missing`);
      await button.click(); await flush();
      posts += 1;
      assert.equal(h.requests.filter((r) => r.options.method).length, posts);
      const post = h.requests.filter((r) => r.options.method).at(-1);
      assert.equal(post.path, `/api/v1/projects/${id(1)}/commitments/${commitmentId}/actions`);
      assert.deepEqual(JSON.parse(post.options.body), { action });
    }
  }
});

test('uncertain writes never auto-retry; explicit retry re-checks state and re-sends only a matching action', async () => {
  let posts = 0;
  const h = harness({ route: routes(project(), (path, options) => {
    if (options?.method) { posts += 1; if (posts === 1) throw new Error('Lost response'); return { body: commitment({ id: id(41), status: 'offered' }) }; }
    if (options?.headers?.Authorization) return { body: project({ milestones: [milestone()] }) };
    return undefined;
  }) });
  await publicOpen(h); h.token();
  h.button('Offer to take this milestone').click(); await flush();
  h.get('project-offer-consent').checked = true;
  h.button('Send voluntary offer').click(); await flush();
  assert.equal(posts, 1); assert.match(h.text('projects-action-status'), /uncertain/);
  h.button('Send voluntary offer')?.click(); await flush(); assert.equal(posts, 1);
  const retry = h.button('Retry the same action against the current state');
  assert.ok(retry); await retry.click(); await flush();
  assert.equal(posts, 2); // the state re-check is a read; the second POST is the deliberate retry
  assert.match(h.text('projects-action-status'), /Retry applied/);
  const bodies = h.requests.filter((r) => r.options.method).map((r) => JSON.parse(r.options.body));
  assert.deepEqual(bodies[0], bodies[1]);
});

test('a retry whose pre-state already moved on sends nothing and resolves honestly', async () => {
  const h = harness({ route: routes(project(), (path, options) => {
    if (options?.method) throw new Error('Lost response');
    if (options?.headers?.Authorization) return { body: project({ milestones: [milestone({ status: 'done' })] }) };
    return undefined;
  }) });
  await publicOpen(h); h.token();
  h.button('Offer to take this milestone').click(); await flush();
  h.get('project-offer-consent').checked = true;
  h.button('Send voluntary offer').click(); await flush();
  const writes = h.requests.filter((r) => r.options.method).length;
  await h.button('Retry the same action against the current state').click(); await flush();
  assert.equal(h.requests.filter((r) => r.options.method).length, writes);
  assert.match(h.text('projects-action-status'), /already moved past/);
});

test('a rejected 409 duplicate offer explains the likely earlier arrival without pending state', async () => {
  const h = harness({ route: routes(project(), (_path, options) => options?.method
    ? { status: 409, body: { error: { code: 'duplicate_commitment', message: 'This identity already has a nonterminal commitment on this milestone.' } } } : undefined) });
  await publicOpen(h); h.token();
  h.button('Offer to take this milestone').click(); await flush();
  h.get('project-offer-consent').checked = true;
  h.button('Send voluntary offer').click(); await flush();
  assert.match(h.text('projects-action-status'), /already holds a commitment/);
  assert.equal(h.button('Retry the same action against the current state'), undefined);
});

test('a token change drops the private view; pagehide wipes the token and discards late private success', async () => {
  const late = deferred();
  const h = harness({ route: routes(project(), (path, options) => options?.headers?.Authorization && !path.includes('?') ? late.promise : undefined) });
  await publicOpen(h); h.token(); h.button('Load participant view').click(); await flush();
  h.token('b'.repeat(43));
  assert.doesNotMatch(h.text('project-detail'), /COORDINATOR VIEW|PARTICIPANT VIEW/);
  late.resolve({ body: project({ title: 'PRIVATE STALE RESPONSE', viewer: { coordinator: true } }) }); await flush();
  assert.ok(!h.text('project-detail').includes('PRIVATE STALE RESPONSE'));
  h.pagehide();
  assert.equal(h.get('room-identity-token').value, '');
});

test('the export re-fetches unauthenticated, validates the packet and fails closed on a tampered notice', async () => {
  const h = harness({ route: routes(project(), (path) => path.includes('/export')
    ? { body: exportPacket({ notice: 'Everything here is verified and payments are authorized.' }) } : undefined) });
  await publicOpen(h);
  await h.button('Download project export').click(); await flush();
  assert.equal(h.downloads.length, 0); assert.match(h.text('project-detail-status'), /Export unavailable/);
  const ok = harness({ route: routes(project()) });
  await publicOpen(ok);
  await ok.button('Download project export').click(); await flush();
  assert.equal(ok.downloads.length, 1);
  const raw = await ok.blobs.get(ok.downloads[0].href).text();
  const packet = JSON.parse(raw);
  assert.equal(packet.kind, 'oss-project-export'); assert.equal(packet.project.id, id(1));
  assert.ok(ok.requests.at(-1).options.headers.Authorization === undefined);
});

test('the model refuses offered commitments in public detail, viewer markers in public views and wrong export notices', () => {
  const { detail, exported, summary } = windowModel();
  assert.ok(detail(project(), false));
  assert.ok(!detail(project({ commitments: [commitment({ status: 'offered' })] }), false));
  assert.ok(!detail(project({ viewer: { coordinator: true } }), false));
  assert.ok(!detail(project({ version: 0 })));
  assert.ok(!summary(project({ mission_id: 'Not A Slug' })));
  assert.ok(exported(exportPacket()));
  assert.ok(!exported(exportPacket({ notice: 'different' })));
  assert.ok(!exported(exportPacket({ commitments: [{ ...exportPacket().commitments[0], status: 'offered' }] })));
});

const artifactDigest = 'a'.repeat(64);
const deliveryFixture = (extra = {}) => ({ id: id(50), project_id: id(1), milestone_id: id(30), revision: 1, scope_version: 1,
  summary: 'First revision of the delivered artifact with honest limits.', artifact: { url: 'https://oss-singularity.io/data/synthetic-delivery-artifact.json',
  media_type: 'application/json', size_bytes: 591, integrity: { algorithm: 'sha256', digest: artifactDigest }, content_identifier: null },
  evidence_url: null, retention: { retained_by: 'contributor', retained_until: '2027-09-17', access: 'public',
  on_unavailable: 'The contributor keeps a mirror and the coordinator can restore the artifact bytes on request.' },
  author: actor(5), created_at: timestamp, ...extra });
const reviewFixture = (extra = {}) => ({ id: id(51), project_id: id(1), milestone_id: id(30), delivery_revision: 1,
  decision: 'accept', note: null, reviewer: actor(2), created_at: timestamp, ...extra });

test('a milestone detail offers deliveries and reviews rendered as an inspectable trail', async () => {
  const hostile = '</h4><script>steal()</script>';
  const value = project({ milestones: [milestone({ title: hostile })], commitments: [] });
  const h = harness({ route: (path, options) => {
    if (path.includes('/deliveries/')) throw new Error('not requested');
    if (path.endsWith('/deliveries')) return { body: { items: [deliveryFixture({ summary: hostile })], next_cursor: null } };
    if (path.endsWith('/reviews')) return { body: { items: [reviewFixture({ decision: 'revision_requested', note: 'The manifest must also state who retains the artifact bytes and for how long they stay retrievable.' })], next_cursor: null } };
    if (path.startsWith('/api/v1/projects?')) return { body: { items: [value], next_cursor: null } };
    return { body: value };
  } });
  await publicOpen(h);
  const all = (n) => [n, ...n.children.flatMap(all)];
  const toggle = h.button('Deliveries & reviews', 'project-detail');
  assert.ok(toggle, 'toggle injected after detail render');
  await toggle.click(); await flush();
  const detailText = h.text('project-detail');
  assert.match(detailText, /Delivery revision 1/);
  assert.match(detailText, /Revision requested · revision 1/);
  const heading = all(h.get('project-detail')).find((n) => n.tagName === 'H4' && n.textContent === hostile);
  assert.equal(heading.children.length, 0, 'hostile text stays literal');
  const manifestLinks = all(h.get('project-detail')).filter((n) => n.tagName === 'A' && /\/deliveries\/1$/.test(n.href));
  assert.equal(manifestLinks.length, 1, 'versioned manifest link rendered');
});

test('invalid delivery responses fail closed without rendering a trail', async () => {
  const value = project({ milestones: [milestone()], commitments: [] });
  const h = harness({ route: (path) => {
    if (path.endsWith('/deliveries')) return { body: { items: [{ id: 'not-a-uuid' }], next_cursor: null } };
    if (path.endsWith('/reviews')) return { body: { items: [], next_cursor: null } };
    if (path.startsWith('/api/v1/projects?')) return { body: { items: [value], next_cursor: null } };
    return { body: value };
  } });
  await publicOpen(h);
  await h.button('Deliveries & reviews', 'project-detail').click(); await flush();
  assert.match(h.text('project-detail'), /unexpected response/);
  assert.doesNotMatch(h.text('project-detail'), /Delivery revision/);
});

test('retention declarations render and older revisions are marked superseded', async () => {
  const value = project({ milestones: [milestone()], commitments: [] });
  const h = harness({ route: (path) => {
    if (path.endsWith('/deliveries')) return { body: { items: [deliveryFixture({ revision: 2, id: id(52) }), deliveryFixture({ retention: undefined })], next_cursor: null } };
    if (path.endsWith('/reviews')) return { body: { items: [], next_cursor: null } };
    if (path.startsWith('/api/v1/projects?')) return { body: { items: [value], next_cursor: null } };
    return { body: value };
  } });
  await publicOpen(h);
  await h.button('Deliveries & reviews', 'project-detail').click(); await flush();
  const all = (n) => [n, ...n.children.flatMap(all)];
  const entries = all(h.get('project-detail')).filter((n) => n.className === 'room-entry');
  assert.equal(entries.length, 2, 'two delivery entries rendered');
  assert.match(entries[0].textContent, /Delivery revision 2/);
  assert.match(entries[0].textContent, /Retained by contributor until 2027-09-17 · access public/);
  assert.match(entries[0].textContent, /If unavailable: The contributor keeps a mirror/);
  assert.doesNotMatch(entries[0].textContent, /Superseded by/);
  const currentLink = all(entries[0]).filter((n) => n.tagName === 'A')[0];
  assert.equal(currentLink.textContent, 'Open the versioned manifest ↗');
  assert.match(currentLink.href, /\/deliveries\/2$/);
  assert.match(entries[1].textContent, /Delivery revision 1/);
  assert.doesNotMatch(entries[1].textContent, /Retained by/);
  assert.match(entries[1].textContent, /Superseded by revision 2/);
  const historicalLink = all(entries[1]).filter((n) => n.tagName === 'A')[0];
  assert.equal(historicalLink.textContent, 'Open the versioned manifest (historical) ↗');
  assert.match(historicalLink.href, /\/deliveries\/1$/);
});

test('deliveries without retention keep rendering the plain trail', async () => {
  const value = project({ milestones: [milestone()], commitments: [] });
  const h = harness({ route: (path) => {
    if (path.endsWith('/deliveries')) return { body: { items: [deliveryFixture({ retention: undefined })], next_cursor: null } };
    if (path.endsWith('/reviews')) return { body: { items: [reviewFixture()], next_cursor: null } };
    if (path.startsWith('/api/v1/projects?')) return { body: { items: [value], next_cursor: null } };
    return { body: value };
  } });
  await publicOpen(h);
  await h.button('Deliveries & reviews', 'project-detail').click(); await flush();
  const detailText = h.text('project-detail');
  assert.match(detailText, /Delivery revision 1/);
  assert.match(detailText, /Open the versioned manifest ↗/);
  assert.doesNotMatch(detailText, /Retained by/);
  assert.doesNotMatch(detailText, /If unavailable:/);
  assert.doesNotMatch(detailText, /Superseded by/);
});

test('the model accepts optional retention and the completed commitment status', () => {
  const { delivery: deliveryModel, commitment: commitmentModel } = windowModel();
  assert.ok(deliveryModel(deliveryFixture()));
  assert.ok(deliveryModel(deliveryFixture({ retention: null })));
  assert.ok(deliveryModel(deliveryFixture({ retention: undefined })));
  assert.ok(deliveryModel(deliveryFixture({ retention: { retained_by: 'third-party', retained_until: null, access: 'public', on_unavailable: null } })));
  assert.ok(commitmentModel(commitment({ status: 'completed' })));
  assert.ok(!deliveryModel(deliveryFixture({ retention: { retained_by: 'nobody', retained_until: '2027-09-17', access: 'public', on_unavailable: null } })));
  assert.ok(!deliveryModel(deliveryFixture({ retention: { retained_by: 'third-party', retained_until: '17-09-2027', access: 'public', on_unavailable: null } })));
  assert.ok(!deliveryModel(deliveryFixture({ retention: { retained_by: 'third-party', retained_until: null, access: 'private', on_unavailable: null } })));
  assert.ok(!deliveryModel(deliveryFixture({ retention: { retained_by: 'third-party', retained_until: null, access: 'public', on_unavailable: 'too short' } })));
  assert.ok(!deliveryModel(deliveryFixture({ retention: { retained_by: 'third-party', retained_until: null, access: 'public', on_unavailable: 'x'.repeat(301) } })));
});

function windowModel() {
  const window = {};
  const context = vm.createContext({ window });
  const model = readFileSync(new URL('../site/assets/scripts/projects-model-v1.js', import.meta.url), 'utf8');
  vm.runInContext(model, context, { filename: 'projects-model-v1.js' });
  return window.OssProjects;
}
