import { ApiError, response, invalid, readJson, textField, identifier, digest, randomToken, safeUrl, pagination, rateKeys, hex } from './security.mjs';
import { authenticateIdentity } from './identity.mjs';

const DAY = 86_400_000;
const PREFIX = '/api/v1/work-items';
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const states = ['open', 'offered', 'active', 'delivered', 'revision_requested', 'acknowledged'];
const actions = ['offer', 'confirm', 'decline', 'withdraw_offer', 'deliver', 'request_revision', 'acknowledge', 'cancel'];
const ongoing = "state NOT IN ('cancelled','acknowledged')";
const identityColumns = (name) => ['github_id', 'github_login', 'verified_at'].map(key => `${name}.${key} AS ${name}_${key}`).join(', ');
const select = `SELECT w.*, parent.status AS parent_status, parent.kind AS parent_kind,
  EXISTS (SELECT 1 FROM work_item_results cr JOIN proposals cp ON cp.id = cr.proposal_id
    WHERE cr.id = w.current_result_id AND cr.work_item_id = w.id AND cr.scope_version = w.scope_version
    AND cr.author_identity_id = w.contributor_identity_id AND cp.identity_id = cr.author_identity_id
    AND cp.mission_id = w.mission_id AND cp.status = 'published' AND cp.kind IN ('field-note','project')
    AND cp.provenance = 'community') AS result_available,
  ${identityColumns('requester')}, ${identityColumns('contributor')}, ${identityColumns('candidate')}
  FROM work_items w JOIN proposals parent ON parent.id = w.mission_id
  JOIN identities requester ON requester.id = w.requester_identity_id
  LEFT JOIN identities contributor ON contributor.id = w.contributor_identity_id
  LEFT JOIN identities candidate ON candidate.id = w.offered_identity_id`;
const alive = `w.expires_at > ? AND (w.moderation != 'pending' OR w.state = 'cancelled' OR w.created_at > ?)
  AND ((w.state != 'cancelled' AND w.moderation != 'rejected') OR w.ended_at > ?)`;
const aliveValues = now => [now, now - 30 * DAY, now - 30 * DAY];
const parentExists = "EXISTS (SELECT 1 FROM proposals p WHERE p.id = w.mission_id AND p.kind = 'mission' AND p.status = 'published')";
const currentToken = 'EXISTS (SELECT 1 FROM identities WHERE id = ? AND token_hash = ?)';
const sqlUuid = "lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-8' || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))";
const iso = value => value == null ? null : new Date(value).toISOString();
const conflict = (code, message) => { throw new ApiError(409, code, message); };
const uuid = (value, field) => { if (typeof value !== 'string' || !UUID.test(value)) invalid(`${field} must be a lowercase UUID v4.`, field); return value; };
const version = value => { if (!Number.isSafeInteger(value) || value < 1) invalid('expected_version must be a positive integer.', 'expected_version'); return value; };
const consent = value => { if (value !== true) invalid('Explicit public_consent: true is required.', 'public_consent'); };
const acceptedDigest = value => digest(JSON.stringify(value));

function parentAvailable(row) { return row.parent_status === 'published' && row.parent_kind === 'mission'; }
function offerExpired(row, now) { return row.state === 'offered' && (!row.offered_identity_id || !row.candidate_github_id || row.offer_expires_at <= now); }
function effective(row, now) {
  if (!offerExpired(row, now)) return row;
  return { ...row, state: 'open', offered_identity_id: null, offer_expires_at: null, version: row.version + 1 };
}
function profile(row, prefix, id) {
  if (!id || !row[`${prefix}_github_id`]) return null;
  return { identity_id: id, github_id: row[`${prefix}_github_id`], github_login: row[`${prefix}_github_login`],
    github_url: `https://github.com/${row[`${prefix}_github_login`]}`, verification: 'github-account-control', verified_at: iso(row[`${prefix}_verified_at`]) };
}
async function rowById(db, id, now) { return db.prepare(`${select} WHERE w.id = ? AND ${alive}`).bind(id, ...aliveValues(now)).first(); }
async function membership(db, row, actor) {
  if (!actor) return false;
  if ([row.requester_identity_id, row.offered_identity_id, row.contributor_identity_id].includes(actor.id)) return true;
  return Boolean(await db.prepare('SELECT id FROM work_item_events WHERE work_item_id = ? AND actor_identity_id = ? LIMIT 1').bind(row.id, actor.id).first());
}
function publicItem(row) { return row.moderation === 'published' && row.state !== 'cancelled' && parentAvailable(row); }
function roles(row, actor) {
  return { requester: actor?.id === row.requester_identity_id, candidate: Boolean(actor && actor.id === row.offered_identity_id),
    contributor: Boolean(actor && actor.id === row.contributor_identity_id), past_participant: Boolean(actor && ![row.requester_identity_id, row.offered_identity_id, row.contributor_identity_id].includes(actor.id)) };
}
async function resultsFor(db, row, now) {
  const result = await db.prepare(`SELECT r.*, p.kind, p.title, p.summary, p.url, p.status, p.identity_id AS proposal_author,
    p.mission_id AS proposal_mission, p.provenance, p.published_at, p.created_at AS proposal_created_at, p.updated_at AS proposal_updated_at,
    EXISTS (SELECT 1 FROM identities WHERE id = r.author_identity_id) AS identity_exists
    FROM work_item_results r JOIN proposals p ON p.id = r.proposal_id WHERE r.work_item_id = ? ORDER BY r.revision`).bind(row.id).all();
  return result.results.filter(r => r.identity_exists && r.proposal_author === r.author_identity_id && r.proposal_mission === row.mission_id && r.scope_version === row.scope_version &&
    ['field-note', 'project'].includes(r.kind) && r.provenance === 'community' &&
    !(r.status === 'pending' && r.proposal_created_at <= now - 30 * DAY) && !(r.status === 'rejected' && r.proposal_updated_at <= now - 30 * DAY));
}
function resultView(row) {
  return { id: row.id, proposal_id: row.proposal_id, revision: row.revision, scope_version: row.scope_version,
    author_identity_id: row.author_identity_id, status: row.status, kind: row.kind, title: row.title, summary: row.summary, url: row.url,
    created_at: iso(row.created_at), published_at: iso(row.published_at) };
}
function actionHints(row, actor, results) {
  const view = roles(row, actor);
  const hints = [];
  if ((view.requester || view.contributor) && !['cancelled', 'acknowledged'].includes(row.state)) hints.push('cancel');
  if (!publicItem(row)) return hints;
  if (row.state === 'offered' && view.requester) hints.push('confirm', 'decline');
  if (row.state === 'offered' && view.candidate) hints.push('withdraw_offer');
  const current = results.find(r => r.id === row.current_result_id && r.status === 'published');
  const canSubmit = ['active', 'revision_requested'].includes(row.state) || (row.state === 'delivered' && !current);
  if (view.contributor && canSubmit && row.operation_count < 128) {
    if (row.result_revision_count < 10 && !results.some(r => r.author_identity_id === actor.id && r.status === 'pending')) hints.push('submit_result');
    if (results.some(r => r.author_identity_id === actor.id && r.status === 'published' && r.revision > row.last_delivered_revision)) hints.push('deliver');
  }
  if (row.state === 'delivered' && view.requester && row.operation_count < 128) {
    hints.push('request_revision');
    if (current && current.author_identity_id === row.contributor_identity_id) hints.push('acknowledge');
  }
  return hints.filter(action => ['cancel', 'withdraw_offer'].includes(action) || row.operation_count < 128);
}
async function itemView(db, raw, now, actor = null, { detail = false, admin = false } = {}) {
  const row = effective(raw, now);
  const results = detail ? await resultsFor(db, row, now) : [];
  const publishedResults = results.filter(r => r.status === 'published');
  const available = detail ? publishedResults.some(r => r.id === row.current_result_id && r.author_identity_id === row.contributor_identity_id) : Boolean(row.result_available);
  const item = {
    id: row.id, mission_id: row.mission_id, title: row.title, scope: row.scope, deliverable: row.deliverable, acceptance: JSON.parse(row.acceptance),
    terms: row.terms, scope_version: row.scope_version, version: row.version, moderation: row.moderation, state: row.state,
    requester: profile(row, 'requester', row.requester_identity_id), contributor: profile(row, 'contributor', row.contributor_identity_id),
    created_at: iso(row.created_at), updated_at: iso(row.updated_at), published_at: iso(row.published_at), expires_at: iso(row.expires_at),
    offer_expires_at: iso(row.offer_expires_at), ended_at: iso(row.ended_at), current_result_id: row.current_result_id,
    current_result_available: available, last_delivered_revision: row.last_delivered_revision,
    acknowledged_result_id: row.acknowledged_result_id, acknowledged_at: iso(row.acknowledged_at),
  };
  if (actor || admin) { item.parent_available = parentAvailable(row); item.viewer = admin ? { requester: false, candidate: false, contributor: false, past_participant: false } : roles(row, actor); }
  if (!detail) return item;
  item.results = publicItem(row) || actor || admin ? publishedResults.map(resultView) : [];
  const events = await db.prepare('SELECT id, version, action, actor_kind, actor_identity_id, result_id, created_at FROM work_item_events WHERE work_item_id = ? ORDER BY version, id').bind(row.id).all();
  const visibleActions = new Set(['confirm', 'deliver', 'request_revision', 'acknowledge', 'published']);
  item.events = events.results.filter(e => admin || e.actor_identity_id === actor?.id || visibleActions.has(e.action)).map(e => ({
    id: e.id, version: e.version, action: e.action, actor_kind: e.actor_kind,
    actor_identity_id: e.actor_kind === 'identity' ? e.actor_identity_id : null,
    result_id: e.result_id && (admin || results.some(r => r.id === e.result_id && (r.status === 'published' || r.author_identity_id === actor?.id))) ? e.result_id : null,
    created_at: iso(e.created_at),
  }));
  if (actor || admin) {
    item.offer = admin || item.viewer.requester || item.viewer.candidate ? profile(row, 'candidate', row.offered_identity_id) : null;
    item.allowed_actions = admin ? [] : actionHints(row, actor, results);
    item.own_results = results.filter(r => admin || r.author_identity_id === actor?.id).map(resultView);
  }
  return item;
}

async function materializeOffers(db, now, id = null, limit = 1000) {
  const batchKey = `system:${crypto.randomUUID()}`;
  // Two set-based statements regardless of the bounded row count. The private
  // batch marker admits only the rows selected by this transaction's first step.
  await db.batch([
    db.prepare(`INSERT INTO work_item_events (id, work_item_id, version, action, actor_kind, actor_key, created_at)
      SELECT ${sqlUuid}, id, version + 1, 'offer_expired', 'system', ?, ? FROM work_items WHERE state = 'offered'
      AND (offered_identity_id IS NULL OR offer_expires_at <= ?) ${id ? 'AND id = ?' : ''} ORDER BY offer_expires_at, id LIMIT ?`)
      .bind(batchKey, now, now, ...(id ? [id] : []), limit),
    db.prepare(`UPDATE work_items SET state = 'open', offered_identity_id = NULL, offer_expires_at = NULL, version = version + 1, updated_at = ?
      WHERE EXISTS (SELECT 1 FROM work_item_events e WHERE e.work_item_id = work_items.id AND e.actor_key = ? AND e.version = work_items.version + 1)`)
      .bind(now, batchKey),
  ]);
}

export async function cleanupWorkItems(db, now = Date.now(), limit = 1000) {
  await materializeOffers(db, now, null, limit);
  return db.batch([
    db.prepare(`DELETE FROM work_items WHERE id IN (SELECT id FROM work_items WHERE expires_at <= ?
      OR (moderation = 'pending' AND state != 'cancelled' AND created_at <= ?) OR ((moderation = 'rejected' OR state = 'cancelled') AND ended_at <= ?)
      ORDER BY expires_at LIMIT ?)`).bind(now, now - 30 * DAY, now - 30 * DAY, limit),
    db.prepare('DELETE FROM rate_limits WHERE bucket IN (SELECT bucket FROM rate_limits WHERE expires_at <= ? ORDER BY expires_at LIMIT ?)').bind(now, limit),
  ]);
}

export async function listWorkItems(request, env, now, mode = 'public') {
  const actor = mode === 'mine' ? await authenticateIdentity(request, env, now) : null;
  const params = new URL(request.url).searchParams;
  const { limit: requestedLimit, cursor } = pagination(params, mode === 'mine' ? ['limit', 'cursor'] : mode === 'admin' ? ['status', 'limit', 'cursor'] : ['mission_id', 'state', 'limit', 'cursor']);
  const limit = params.has('limit') ? requestedLimit : 20;
  if (limit > 50) invalid('limit must be between 1 and 50.', 'limit');
  const where = [alive], values = aliveValues(now);
  const time = mode === 'public' ? 'published_at' : 'created_at';
  if (mode === 'public') {
    where.push("w.moderation = 'published' AND w.state != 'cancelled' AND parent.status = 'published' AND parent.kind = 'mission'");
    const state = params.get('state') || 'ongoing';
    if (![...states, 'ongoing', 'all'].includes(state)) invalid('Unsupported work-item state.', 'state');
    const effectiveState = "CASE WHEN w.state = 'offered' AND (w.offered_identity_id IS NULL OR w.offer_expires_at <= ?) THEN 'open' ELSE w.state END";
    if (state === 'ongoing') where.push("w.state != 'acknowledged'");
    else if (state !== 'all') { where.push(`${effectiveState} = ?`); values.push(now, state); }
    if (params.has('mission_id')) {
      const mission = identifier(params.get('mission_id'), 'mission_id');
      if (!await env.DB.prepare("SELECT id FROM proposals WHERE id = ? AND kind = 'mission' AND status = 'published'").bind(mission).first()) throw new ApiError(404, 'not_found', 'Published mission not found.');
      where.push('w.mission_id = ?'); values.push(mission);
    }
  } else if (mode === 'mine') {
    where.push('(w.requester_identity_id = ? OR w.offered_identity_id = ? OR w.contributor_identity_id = ? OR EXISTS (SELECT 1 FROM work_item_events e WHERE e.work_item_id = w.id AND e.actor_identity_id = ?))');
    values.push(actor.id, actor.id, actor.id, actor.id);
  } else {
    const status = params.get('status') || 'pending';
    if (!['pending', 'published', 'rejected'].includes(status)) invalid('Unsupported moderation status.', 'status');
    where.push('w.moderation = ?'); values.push(status);
  }
  if (cursor) { where.push(`(w.${time} < ? OR (w.${time} = ? AND w.id < ?))`); values.push(cursor[0], cursor[0], cursor[1]); }
  const found = await env.DB.prepare(`${select} WHERE ${where.join(' AND ')} ORDER BY w.${time} DESC, w.id DESC LIMIT ?`).bind(...values, limit + 1).all();
  const rows = found.results.slice(0, limit), last = rows.at(-1);
  const items = [];
  for (const row of rows) items.push(await itemView(env.DB, row, now, actor, { admin: mode === 'admin' }));
  return response({ items, next_cursor: found.results.length > limit ? `${last[time]}:${last.id}` : null });
}

export async function readWorkItem(request, env, id, now, privateRead = false) {
  const actor = privateRead ? await authenticateIdentity(request, env, now) : null;
  const row = await rowById(env.DB, id, now);
  if (!row || (privateRead ? !await membership(env.DB, row, actor) : !publicItem(row))) throw new ApiError(404, 'not_found', 'Work item not found.');
  return response(await itemView(env.DB, row, now, actor, { detail: true }));
}

async function bucketsFor(request, env, actor, now, kind) {
  const ip = request.headers.get('cf-connecting-ip');
  if (typeof env.IP_HMAC_SECRET !== 'string' || env.IP_HMAC_SECRET.length < 32 || !ip || ip.length > 64 || !/^[0-9a-f:.]+$/i.test(ip)) throw new ApiError(503, 'service_unavailable', 'Work-item submissions are temporarily unavailable.');
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', encoder.encode(env.IP_HMAC_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const labels = [`work-${kind}-ip-hour:${Math.floor(now / 3_600_000)}\n${ip}`, `work-${kind}-ip-day:${Math.floor(now / DAY)}\n${ip}`,
    `work-${kind}-identity-hour:${Math.floor(now / 3_600_000)}\n${actor.id}`, `work-${kind}-identity-day:${Math.floor(now / DAY)}\n${actor.id}`];
  const limits = kind === 'create' ? [5, 50, 5, 50] : [30, 100, 30, 100];
  const keys = await Promise.all(labels.map(async label => hex(await crypto.subtle.sign('HMAC', key, encoder.encode(label)))));
  return keys.map((key, i) => ({ key, limit: limits[i], period: i % 2 ? DAY : 3_600_000 }));
}
const quotaCondition = buckets => buckets.map(() => '(COALESCE((SELECT count FROM rate_limits WHERE bucket = ?), 0) < ?)').join(' AND ') || '1';
const quotaValues = buckets => buckets.flatMap(b => [b.key, b.limit]);
function quotaWrites(db, buckets, now, admission, admissionValues) {
  return buckets.map(b => db.prepare(`INSERT INTO rate_limits (bucket, count, expires_at) SELECT ?, 1, ? WHERE ${admission}
    ON CONFLICT(bucket) DO UPDATE SET count = count + 1`).bind(b.key, now + DAY, ...admissionValues));
}
async function quotaFailure(db, buckets, now) {
  for (const b of buckets) {
    const row = await db.prepare('SELECT count FROM rate_limits WHERE bucket = ?').bind(b.key).first();
    if ((row?.count || 0) >= b.limit) throw new ApiError(429, 'rate_limited', 'The work-item submission limit has been reached.', undefined, Math.ceil((b.period - now % b.period) / 1000));
  }
}
async function eventReplay(db, id, actor, requestId, requestDigest) {
  const event = await db.prepare('SELECT * FROM work_item_events WHERE work_item_id = ? AND actor_key = ? AND client_request_id = ?').bind(id, actor.id, requestId).first();
  if (event && event.request_digest !== requestDigest) conflict('idempotency_conflict', 'This client_request_id was already used with different input.');
  return event;
}
async function mutationResponse(env, id, actor, now, event, replayed, status = 200, extra = {}) {
  const row = await rowById(env.DB, id, now);
  if (!row || !await membership(env.DB, row, actor)) throw new ApiError(404, 'not_found', 'Work item not found.');
  return response({ item: await itemView(env.DB, row, now, actor, { detail: true }), operation: { id: event.id, applied_version: event.version, replayed }, ...extra }, status);
}

export async function createWorkItem(request, env, now) {
  const actor = await authenticateIdentity(request, env, now);
  const body = await readJson(request, ['client_request_id', 'mission_id', 'title', 'scope', 'deliverable', 'acceptance', 'terms', 'public_consent']);
  const requestId = uuid(body.client_request_id, 'client_request_id');
  consent(body.public_consent);
  if (body.terms !== 'volunteer') invalid('This pilot supports voluntary work; paid cooperation remains a separate future capability.', 'terms');
  if (!Array.isArray(body.acceptance) || body.acceptance.length < 1 || body.acceptance.length > 8) invalid('acceptance requires 1–8 criteria.', 'acceptance');
  const data = { mission_id: identifier(body.mission_id, 'mission_id'), title: textField(body.title, 'title', 3, 120),
    scope: textField(body.scope, 'scope', 20, 2000), deliverable: textField(body.deliverable, 'deliverable', 20, 1000),
    acceptance: body.acceptance.map(value => textField(value, 'acceptance', 10, 300)), terms: body.terms, public_consent: true };
  const requestDigest = await acceptedDigest(data);
  const existing = await env.DB.prepare('SELECT * FROM work_items WHERE requester_identity_id = ? AND client_request_id = ?').bind(actor.id, requestId).first();
  if (existing) {
    if (existing.request_digest !== requestDigest) conflict('idempotency_conflict', 'This client_request_id was already used with different input.');
    return mutationResponse(env, existing.id, actor, now, { id: existing.creation_operation_id, version: 1 }, true);
  }
  await cleanupWorkItems(env.DB, now, 100);
  const buckets = await bucketsFor(request, env, actor, now, 'create');
  const id = crypto.randomUUID(), eventId = crypto.randomUUID();
  const admission = 'EXISTS (SELECT 1 FROM work_items WHERE id = ? AND creation_operation_id = ?)';
  const result = await env.DB.batch([
    env.DB.prepare(`INSERT INTO work_items (id, mission_id, requester_identity_id, title, scope, deliverable, acceptance, terms, operation_count,
      moderation, state, created_at, updated_at, expires_at, client_request_id, request_digest, creation_operation_id)
      SELECT ?, ?, ?, ?, ?, ?, ?, 'volunteer', 1, 'pending', 'open', ?, ?, ?, ?, ?, ?
      WHERE ${currentToken} AND ${quotaCondition(buckets)}
      AND EXISTS (SELECT 1 FROM proposals WHERE id = ? AND kind = 'mission' AND status = 'published')
      AND NOT EXISTS (SELECT 1 FROM work_items WHERE requester_identity_id = ? AND client_request_id = ?)
      AND (SELECT COUNT(*) FROM work_items) < 1000
      AND (SELECT COUNT(*) FROM work_items WHERE mission_id = ?) < 100
      AND (SELECT COUNT(*) FROM work_items WHERE moderation = 'pending') < 200
      AND (SELECT COUNT(*) FROM work_items WHERE requester_identity_id = ? AND ${ongoing} AND moderation != 'rejected') < 10`)
      .bind(id, data.mission_id, actor.id, data.title, data.scope, data.deliverable, JSON.stringify(data.acceptance), now, now, now + 90 * DAY, requestId, requestDigest, eventId,
        actor.id, actor.token_hash, ...quotaValues(buckets), data.mission_id, actor.id, requestId, data.mission_id, actor.id),
    env.DB.prepare(`INSERT INTO work_item_events (id, work_item_id, version, action, actor_kind, actor_identity_id, actor_key, client_request_id, request_digest, created_at)
      SELECT ?, ?, 1, 'created', 'identity', ?, ?, ?, ?, ? WHERE ${admission}`).bind(eventId, id, actor.id, actor.id, requestId, requestDigest, now, id, eventId),
    ...quotaWrites(env.DB, buckets, now, admission, [id, eventId]),
  ]);
  if (result[0].meta.changes !== 1) {
    await authenticateIdentity(request, env, now);
    const raced = await env.DB.prepare('SELECT * FROM work_items WHERE requester_identity_id = ? AND client_request_id = ?').bind(actor.id, requestId).first();
    if (raced) {
      if (raced.request_digest !== requestDigest) conflict('idempotency_conflict', 'This client_request_id was already used with different input.');
      return mutationResponse(env, raced.id, actor, now, { id: raced.creation_operation_id, version: 1 }, true);
    }
    await quotaFailure(env.DB, buckets, now);
    if (!await env.DB.prepare("SELECT id FROM proposals WHERE id = ? AND kind = 'mission' AND status = 'published'").bind(data.mission_id).first()) throw new ApiError(404, 'not_found', 'Published mission not found.');
    throw new ApiError(503, 'capacity_reached', 'The voluntary work pilot has reached its current capacity.');
  }
  return mutationResponse(env, id, actor, now, { id: eventId, version: 1 }, false, 202);
}

async function requireActorItem(request, env, id, now, { publicOffer = false } = {}) {
  const actor = await authenticateIdentity(request, env, now);
  const row = await rowById(env.DB, id, now);
  if (!row || (!await membership(env.DB, row, actor) && !(publicOffer && publicItem(row)))) throw new ApiError(404, 'not_found', 'Work item not found.');
  return { actor, row };
}
async function recheckFailure(request, env, id, actor, now, requestId, requestDigest, expected, buckets) {
  await authenticateIdentity(request, env, now);
  const row = await rowById(env.DB, id, now);
  if (!row) throw new ApiError(404, 'not_found', 'Work item not found.');
  const replay = await eventReplay(env.DB, id, actor, requestId, requestDigest);
  if (replay) return replay;
  if (row.version !== expected) conflict('version_conflict', 'The work item changed. Refresh it before deciding on another action.');
  await quotaFailure(env.DB, buckets, now);
  conflict('invalid_transition', 'This action is not available for this work item, actor or current capacity.');
}

export async function actOnWorkItem(request, env, id, now) {
  const { actor } = await requireActorItem(request, env, id, now, { publicOffer: true });
  const body = await readJson(request, ['client_request_id', 'expected_version', 'action', 'result_id', 'public_consent']);
  if (!actions.includes(body.action)) invalid('Unsupported work-item action.', 'action');
  const requestId = uuid(body.client_request_id, 'client_request_id'), expected = version(body.expected_version);
  const action = body.action;
  if (action === 'offer') consent(body.public_consent);
  else if (Object.hasOwn(body, 'public_consent')) invalid('public_consent belongs only to offer.', 'public_consent');
  const resultId = ['deliver', 'acknowledge'].includes(action) ? uuid(body.result_id, 'result_id') : null;
  if (!resultId && Object.hasOwn(body, 'result_id')) invalid('result_id belongs only to deliver or acknowledge.', 'result_id');
  let row = await rowById(env.DB, id, now);
  if (!row) throw new ApiError(404, 'not_found', 'Work item not found.');
  if (action !== 'offer' && !await membership(env.DB, row, actor)) throw new ApiError(404, 'not_found', 'Work item not found.');
  const requestDigest = await acceptedDigest({ action, expected_version: expected, ...(resultId ? { result_id: resultId } : {}), ...(action === 'offer' ? { public_consent: true } : {}) });
  const replay = await eventReplay(env.DB, id, actor, requestId, requestDigest);
  if (replay) return mutationResponse(env, id, actor, now, replay, true);
  await materializeOffers(env.DB, now, id, 1);
  row = await rowById(env.DB, id, now);
  if (!row) throw new ApiError(404, 'not_found', 'Work item not found.');
  if (row.version !== expected) conflict('version_conflict', 'The work item changed. Refresh it before deciding on another action.');
  const exit = ['cancel', 'withdraw_offer'].includes(action);
  const buckets = exit ? [] : await bucketsFor(request, env, actor, now, 'action');
  let condition = '', conditionValues = [], assignments = '', assignmentValues = [];
  const published = `w.moderation = 'published' AND ${parentExists}`;
  switch (action) {
    case 'offer':
      condition = `${published} AND w.state = 'open' AND w.requester_identity_id != ?
        AND (SELECT COUNT(*) FROM work_items other WHERE other.moderation = 'published' AND other.expires_at > ? AND
          ((other.offered_identity_id = ? AND other.state = 'offered' AND other.offer_expires_at > ?)
          OR (other.contributor_identity_id = ? AND other.state IN ('active','delivered','revision_requested')))) < 10`;
      conditionValues = [actor.id, now, actor.id, now, actor.id];
      assignments = "state = 'offered', offered_identity_id = ?, offer_expires_at = ?";
      assignmentValues = [actor.id, now + 2 * DAY]; break;
    case 'confirm':
      condition = `${published} AND w.state = 'offered' AND w.requester_identity_id = ? AND w.offer_expires_at > ? AND w.offered_identity_id IS NOT NULL
        AND EXISTS (SELECT 1 FROM identities WHERE id = w.offered_identity_id)`;
      conditionValues = [actor.id, now];
      assignments = "state = 'active', contributor_identity_id = offered_identity_id, offered_identity_id = NULL, offer_expires_at = NULL"; break;
    case 'decline':
    case 'withdraw_offer':
      condition = `${published} AND w.state = 'offered' AND w.${action === 'decline' ? 'requester' : 'offered'}_identity_id = ?`;
      conditionValues = [actor.id];
      assignments = "state = 'open', offered_identity_id = NULL, offer_expires_at = NULL"; break;
    case 'cancel':
      condition = `w.state NOT IN ('cancelled','acknowledged') AND (w.requester_identity_id = ? OR w.contributor_identity_id = ?)`;
      conditionValues = [actor.id, actor.id];
      assignments = "state = 'cancelled', offered_identity_id = NULL, offer_expires_at = NULL, ended_at = ?"; assignmentValues = [now]; break;
    case 'deliver':
      condition = `${published} AND w.contributor_identity_id = ? AND (w.state IN ('active','revision_requested') OR (w.state = 'delivered'
        AND NOT EXISTS (SELECT 1 FROM work_item_results old JOIN proposals oldp ON oldp.id = old.proposal_id WHERE old.id = w.current_result_id AND oldp.status = 'published')))
        AND EXISTS (SELECT 1 FROM work_item_results r JOIN proposals p ON p.id = r.proposal_id WHERE r.id = ? AND r.work_item_id = w.id
          AND r.revision > w.last_delivered_revision AND r.scope_version = w.scope_version AND r.author_identity_id = w.contributor_identity_id
          AND p.identity_id = w.contributor_identity_id AND p.mission_id = w.mission_id AND p.kind IN ('field-note','project')
          AND p.provenance = 'community' AND p.status = 'published' AND p.url IS NOT NULL)`;
      conditionValues = [actor.id, resultId];
      assignments = "state = 'delivered', current_result_id = ?, last_delivered_revision = (SELECT revision FROM work_item_results WHERE id = ?)";
      assignmentValues = [resultId, resultId]; break;
    case 'request_revision':
      condition = `${published} AND w.state = 'delivered' AND w.requester_identity_id = ?`;
      conditionValues = [actor.id]; assignments = "state = 'revision_requested'"; break;
    case 'acknowledge':
      condition = `${published} AND w.state = 'delivered' AND w.requester_identity_id = ? AND w.current_result_id = ?
        AND EXISTS (SELECT 1 FROM work_item_results r JOIN proposals p ON p.id = r.proposal_id WHERE r.id = w.current_result_id
          AND r.work_item_id = w.id AND r.scope_version = w.scope_version AND r.revision = w.last_delivered_revision
          AND r.author_identity_id = w.contributor_identity_id AND p.identity_id = w.contributor_identity_id
          AND p.mission_id = w.mission_id AND p.kind IN ('field-note','project') AND p.provenance = 'community' AND p.status = 'published')`;
      conditionValues = [actor.id, resultId];
      assignments = "state = 'acknowledged', acknowledged_result_id = current_result_id, acknowledged_at = ?, ended_at = ?"; assignmentValues = [now, now]; break;
  }
  const eventId = crypto.randomUUID();
  const admitted = 'EXISTS (SELECT 1 FROM work_item_events WHERE id = ?)';
  const batch = await env.DB.batch([
    env.DB.prepare(`INSERT INTO work_item_events (id, work_item_id, version, action, actor_kind, actor_identity_id, actor_key, result_id, client_request_id, request_digest, created_at)
      SELECT ?, w.id, w.version + 1, ?, 'identity', ?, ?, ?, ?, ?, ? FROM work_items w
      WHERE w.id = ? AND w.version = ? AND ${alive} AND ${currentToken} AND ${condition}
      AND ${exit ? '1' : 'w.operation_count < 128'} AND ${quotaCondition(buckets)}
      AND NOT EXISTS (SELECT 1 FROM work_item_events WHERE work_item_id = w.id AND actor_key = ? AND client_request_id = ?)`)
      .bind(eventId, action, actor.id, actor.id, resultId, requestId, requestDigest, now, id, expected, ...aliveValues(now), actor.id, actor.token_hash, ...conditionValues, ...quotaValues(buckets), actor.id, requestId),
    env.DB.prepare(`UPDATE work_items SET ${assignments}, version = version + 1, updated_at = ?, operation_count = operation_count + ?
      WHERE id = ? AND version = ? AND ${admitted}`).bind(...assignmentValues, now, exit ? 0 : 1, id, expected, eventId),
    ...quotaWrites(env.DB, buckets, now, admitted, [eventId]),
  ]);
  if (batch[0].meta.changes !== 1) {
    const raced = await recheckFailure(request, env, id, actor, now, requestId, requestDigest, expected, buckets);
    return mutationResponse(env, id, actor, now, raced, true);
  }
  return mutationResponse(env, id, actor, now, { id: eventId, version: expected + 1 }, false);
}

export async function submitWorkResult(request, env, id, now) {
  const { actor } = await requireActorItem(request, env, id, now);
  const body = await readJson(request, ['client_request_id', 'expected_version', 'kind', 'title', 'summary', 'url', 'public_consent']);
  const requestId = uuid(body.client_request_id, 'client_request_id'), expected = version(body.expected_version);
  consent(body.public_consent);
  if (!['field-note', 'project'].includes(body.kind)) invalid('A result is a field-note or project.', 'kind');
  const data = { expected_version: expected, kind: body.kind, title: textField(body.title, 'title', 3, 120), summary: textField(body.summary, 'summary', 20, 2000), url: safeUrl(body.url), public_consent: true };
  if (!data.url) invalid('A result requires a public HTTPS source URL.', 'url');
  // Include route operation type so the same request ID cannot alias an action.
  const requestDigest = await acceptedDigest({ operation: 'result', ...data });
  const replay = await eventReplay(env.DB, id, actor, requestId, requestDigest);
  if (replay) return mutationResponse(env, id, actor, now, replay, true, 200, { result_id: replay.result_id });
  await materializeOffers(env.DB, now, id, 1);
  const row = await rowById(env.DB, id, now);
  if (!row) throw new ApiError(404, 'not_found', 'Work item not found.');
  if (row.version !== expected) conflict('version_conflict', 'The work item changed. Refresh before submitting a result.');
  if (row.contributor_identity_id !== actor.id) throw new ApiError(404, 'not_found', 'Own confirmed work item not found.');
  const actionBuckets = await bucketsFor(request, env, actor, now, 'action');
  const proposalKeys = await rateKeys(request.headers.get('cf-connecting-ip'), env.IP_HMAC_SECRET, now);
  const buckets = [...actionBuckets, ...proposalKeys.map((key, i) => ({ key, limit: i ? 50 : 5, period: i ? DAY : 3_600_000 }))];
  const eventId = crypto.randomUUID(), resultId = crypto.randomUUID(), proposalId = crypto.randomUUID(), token = randomToken(), receiptHash = await digest(token);
  const admitted = 'EXISTS (SELECT 1 FROM work_item_events WHERE id = ?)';
  const batch = await env.DB.batch([
    env.DB.prepare(`INSERT INTO work_item_events (id, work_item_id, version, action, actor_kind, actor_identity_id, actor_key, result_id, client_request_id, request_digest, created_at)
      SELECT ?, w.id, w.version + 1, 'result_submitted', 'identity', ?, ?, ?, ?, ?, ? FROM work_items w
      WHERE w.id = ? AND w.version = ? AND ${alive} AND ${currentToken} AND w.contributor_identity_id = ?
      AND w.moderation = 'published' AND ${parentExists} AND w.operation_count < 128 AND w.result_revision_count < 10
      AND (w.state IN ('active','revision_requested') OR (w.state = 'delivered' AND NOT EXISTS (
        SELECT 1 FROM work_item_results old JOIN proposals oldp ON oldp.id = old.proposal_id WHERE old.id = w.current_result_id AND oldp.status = 'published')))
      AND NOT EXISTS (SELECT 1 FROM work_item_results r JOIN proposals p ON p.id = r.proposal_id
        WHERE r.work_item_id = w.id AND r.author_identity_id = ? AND p.status = 'pending' AND p.created_at > ?)
      AND (SELECT COUNT(*) FROM proposals WHERE status = 'pending') < 200
      AND ${quotaCondition(buckets)}
      AND NOT EXISTS (SELECT 1 FROM work_item_events WHERE work_item_id = w.id AND actor_key = ? AND client_request_id = ?)`)
      .bind(eventId, actor.id, actor.id, resultId, requestId, requestDigest, now, id, expected, ...aliveValues(now), actor.id, actor.token_hash, actor.id,
        actor.id, now - 30 * DAY, ...quotaValues(buckets), actor.id, requestId),
    env.DB.prepare(`INSERT INTO proposals (id, kind, title, summary, url, mission_id, identity_id, status, provenance, receipt_hash, created_at, updated_at)
      SELECT ?, ?, ?, ?, ?, mission_id, ?, 'pending', 'community', ?, ?, ? FROM work_items WHERE id = ? AND version = ? AND ${admitted}`)
      .bind(proposalId, data.kind, data.title, data.summary, data.url, actor.id, receiptHash, now, now, id, expected, eventId),
    env.DB.prepare(`INSERT INTO work_item_results (id, work_item_id, proposal_id, author_identity_id, scope_version, revision, created_at, client_request_id, request_digest)
      SELECT ?, w.id, ?, ?, w.scope_version, w.result_revision_count + 1, ?, ?, ? FROM work_items w WHERE w.id = ? AND w.version = ?
      AND ${admitted} AND EXISTS (SELECT 1 FROM proposals WHERE id = ? AND identity_id = ? AND receipt_hash = ?)`)
      .bind(resultId, proposalId, actor.id, now, requestId, requestDigest, id, expected, eventId, proposalId, actor.id, receiptHash),
    env.DB.prepare(`UPDATE work_items SET result_revision_count = result_revision_count + 1, operation_count = operation_count + 1,
      version = version + 1, updated_at = ? WHERE id = ? AND version = ? AND ${admitted}
      AND EXISTS (SELECT 1 FROM work_item_results WHERE id = ? AND proposal_id = ?)`)
      .bind(now, id, expected, eventId, resultId, proposalId),
    ...quotaWrites(env.DB, buckets, now, `EXISTS (SELECT 1 FROM work_item_results WHERE id = ? AND proposal_id = ?)`, [resultId, proposalId]),
  ]);
  if (batch[0].meta.changes !== 1) {
    const replayAfter = await eventReplay(env.DB, id, actor, requestId, requestDigest);
    if (replayAfter) {
      await authenticateIdentity(request, env, now);
      return mutationResponse(env, id, actor, now, replayAfter, true, 200, { result_id: replayAfter.result_id });
    }
    await authenticateIdentity(request, env, now);
    const current = await rowById(env.DB, id, now);
    if (current && current.version === expected && await env.DB.prepare(`SELECT r.id FROM work_item_results r JOIN proposals p ON p.id = r.proposal_id
      WHERE r.work_item_id = ? AND r.author_identity_id = ? AND p.status = 'pending' AND p.created_at > ?`).bind(id, actor.id, now - 30 * DAY).first()) {
      conflict('duplicate_pending_result', 'This work item already has a pending result from you.');
    }
    const raced = await recheckFailure(request, env, id, actor, now, requestId, requestDigest, expected, buckets);
    return mutationResponse(env, id, actor, now, raced, true, 200, { result_id: raced.result_id });
  }
  return mutationResponse(env, id, actor, now, { id: eventId, version: expected + 1 }, false, 202, {
    result_id: resultId, receipt: { id: proposalId, status: 'pending', poll_url: `/api/v1/proposals/${proposalId}`, receipt_token: token },
  });
}

export async function moderateWorkItem(request, env, id, now) {
  const body = await readJson(request, ['expected_version', 'status']);
  const expected = version(body.expected_version);
  if (!['published', 'rejected'].includes(body.status)) invalid('status must be published or rejected.', 'status');
  await materializeOffers(env.DB, now, id, 1);
  const row = await rowById(env.DB, id, now);
  if (!row) throw new ApiError(404, 'not_found', 'Work item not found.');
  if (row.version !== expected) conflict('version_conflict', 'The work item changed. Refresh before moderation.');
  if (row.moderation === body.status) return response(await itemView(env.DB, row, now, null, { detail: true, admin: true }));
  const eventId = crypto.randomUUID(), publishing = body.status === 'published';
  const batch = await env.DB.batch([
    env.DB.prepare(`INSERT INTO work_item_events (id, work_item_id, version, action, actor_kind, actor_key, created_at)
      SELECT ?, w.id, w.version + 1, ?, 'moderator', 'moderator', ? FROM work_items w WHERE w.id = ? AND w.version = ? AND ${alive}
      AND ${publishing ? `w.moderation = 'pending' AND w.state != 'cancelled' AND ${parentExists}` : "w.moderation IN ('pending','published')"}`)
      .bind(eventId, body.status, now, id, expected, ...aliveValues(now)),
    env.DB.prepare(`UPDATE work_items SET moderation = ?, version = version + 1, updated_at = ?,
      published_at = CASE WHEN ? = 1 THEN ? ELSE published_at END,
      state = CASE WHEN ? = 0 AND state != 'acknowledged' THEN 'cancelled' ELSE state END,
      offered_identity_id = CASE WHEN ? = 0 THEN NULL ELSE offered_identity_id END,
      offer_expires_at = CASE WHEN ? = 0 THEN NULL ELSE offer_expires_at END,
      ended_at = CASE WHEN ? = 0 THEN ? ELSE ended_at END
      WHERE id = ? AND version = ? AND EXISTS (SELECT 1 FROM work_item_events WHERE id = ?)`)
      .bind(body.status, now, Number(publishing), now, Number(publishing), Number(publishing), Number(publishing), Number(publishing), now, id, expected, eventId),
  ]);
  if (batch[0].meta.changes !== 1) {
    const current = await rowById(env.DB, id, now);
    if (!current) throw new ApiError(404, 'not_found', 'Work item not found.');
    if (current.version !== expected) conflict('version_conflict', 'The work item changed. Refresh before moderation.');
    conflict('invalid_transition', 'Only pending available work can be published; rejected work cannot be reopened.');
  }
  return response(await itemView(env.DB, await rowById(env.DB, id, now), now, null, { detail: true, admin: true }));
}

export const workItemDiscovery = {
  version: '1.0', terms: ['volunteer'], scope_versions: [1], public_lifetime_days: 90, pending_lifetime_days: 30,
  terminal_private_lifetime_days: 30, offer_lifetime_hours: 48, results_per_item: 10, regular_operations_per_item: 128,
  retained_capacity: 1000, mission_capacity: 100, pending_capacity: 200, created_active_per_identity: 10, contributing_active_per_identity: 10,
  identity_required: true, automatic_assignment: false, automatic_execution: false, payment_processing: false,
};
