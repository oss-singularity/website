import { ApiError, response, invalid, readJson, textField, identifier, digest, bearer, equalHash, randomToken, hex, safeUrl, pagination } from './security.mjs';
import { authenticateIdentity } from './identity.mjs';

const DAY = 86_400_000;
const LIFETIME = 30 * DAY;
const PREFIX = '/api/v1/participations';
const intents = new Set(['offer', 'need']);
const participantTypes = new Set(['human', 'agent', 'team', 'other']);
const collaborations = new Set(['volunteer', 'discuss-compensation']);
const fields = `id, mission_id, identity_id, intent, participant_type, collaboration, title, summary, url,
  status, state, created_at, updated_at, published_at, expires_at,
  (SELECT github_id FROM identities WHERE identities.id = participations.identity_id) AS author_github_id,
  (SELECT github_login FROM identities WHERE identities.id = participations.identity_id) AS author_github_login,
  (SELECT verified_at FROM identities WHERE identities.id = participations.identity_id) AS author_verified_at`;
const publishedMission = "EXISTS (SELECT 1 FROM proposals WHERE proposals.id = participations.mission_id AND kind = 'mission' AND status = 'published')";
const existingIdentity = 'EXISTS (SELECT 1 FROM identities WHERE identities.id = participations.identity_id)';
export const visibleParticipation = `status = 'published' AND ${publishedMission} AND ${existingIdentity}`;

function card(row) {
  const { author_github_id, author_github_login, author_verified_at, ...item } = row;
  return {
    ...item,
    author: author_github_id ? {
      identity_id: item.identity_id, github_id: author_github_id, github_login: author_github_login,
      github_url: `https://github.com/${author_github_login}`, verification: 'github-account-control',
      verified_at: new Date(author_verified_at).toISOString(),
    } : null,
    created_at: new Date(row.created_at).toISOString(), updated_at: new Date(row.updated_at).toISOString(),
    published_at: row.published_at === null ? null : new Date(row.published_at).toISOString(),
    expires_at: new Date(row.expires_at).toISOString(),
  };
}

export async function cleanupParticipations(db, now = Date.now(), limit = 1000) {
  return db.batch([
    // Effective expiry is checked in every query even before cleanup runs.
    db.prepare(`UPDATE participations SET state = 'expired' WHERE id IN
      (SELECT id FROM participations WHERE expires_at <= ? AND state != 'expired' ORDER BY expires_at LIMIT ?)`).bind(now, limit),
    db.prepare('DELETE FROM participations WHERE id IN (SELECT id FROM participations WHERE expires_at <= ? ORDER BY expires_at LIMIT ?)').bind(now, limit),
    db.prepare('DELETE FROM rate_limits WHERE bucket IN (SELECT bucket FROM rate_limits WHERE expires_at <= ? ORDER BY expires_at LIMIT ?)').bind(now, limit),
  ]);
}

async function rateBuckets(ip, identity, secret, now) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const labels = [
    `participation-ip-hour:${Math.floor(now / 3_600_000)}\n${ip}`,
    `participation-ip-day:${Math.floor(now / DAY)}\n${ip}`,
    `participation-identity-hour:${Math.floor(now / 3_600_000)}\n${identity}`,
    `participation-identity-day:${Math.floor(now / DAY)}\n${identity}`,
  ];
  return Promise.all(labels.map(async label => hex(await crypto.subtle.sign('HMAC', key, encoder.encode(label)))));
}

async function requireMission(db, mission) {
  if (!await db.prepare("SELECT id FROM proposals WHERE id = ? AND kind = 'mission' AND status = 'published'").bind(mission).first()) {
    throw new ApiError(404, 'not_found', 'Published mission not found.');
  }
}

export async function submitParticipation(request, env, now) {
  const identity = await authenticateIdentity(request, env, now);
  const body = await readJson(request, ['mission_id', 'intent', 'participant_type', 'collaboration', 'title', 'summary', 'url']);
  const mission = identifier(body.mission_id, 'mission_id');
  if (!intents.has(body.intent)) invalid('intent must be offer or need.', 'intent');
  if (!participantTypes.has(body.participant_type)) invalid('participant_type must be human, agent, team or other; this is self-declared.', 'participant_type');
  if (!collaborations.has(body.collaboration)) invalid('collaboration must be volunteer or discuss-compensation.', 'collaboration');
  const title = textField(body.title, 'title', 3, 120);
  const summary = textField(body.summary, 'summary', 20, 2000);
  const url = safeUrl(body.url);
  await requireMission(env.DB, mission);
  const ip = request.headers.get('cf-connecting-ip');
  if (typeof env.IP_HMAC_SECRET !== 'string' || env.IP_HMAC_SECRET.length < 32 || !ip || ip.length > 64 || !/^[0-9a-f:.]+$/i.test(ip)) {
    throw new ApiError(503, 'service_unavailable', 'Participation submissions are temporarily unavailable.');
  }
  await cleanupParticipations(env.DB, now, 100);
  const buckets = await rateBuckets(ip, identity.id, env.IP_HMAC_SECRET, now);
  const id = crypto.randomUUID();
  const token = randomToken();
  const tokenHash = await digest(token);
  const expires = now + LIFETIME;
  const limits = [5, 50, 5, 50];
  // The card insert checks all limits inside the same transaction as counter
  // creation/increments. Rejected requests cannot create arbitrary counter rows.
  const statements = [
    env.DB.prepare(`UPDATE participations SET state = 'expired' WHERE identity_id = ? AND mission_id = ? AND intent = ?
      AND state = 'active' AND expires_at <= ?`).bind(identity.id, mission, body.intent, now),
    env.DB.prepare(`INSERT INTO participations (id, mission_id, identity_id, intent, participant_type, collaboration,
      title, summary, url, status, state, receipt_hash, created_at, updated_at, expires_at)
      SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'active', ?, ?, ?, ?
      WHERE EXISTS (SELECT 1 FROM proposals WHERE id = ? AND kind = 'mission' AND status = 'published')
      AND EXISTS (SELECT 1 FROM identities WHERE id = ? AND token_hash = ?)
      AND (SELECT COUNT(*) FROM participations WHERE status = 'pending' AND state = 'active' AND expires_at > ?) < 200
      AND (SELECT COUNT(*) FROM participations WHERE identity_id = ? AND status IN ('pending','published') AND state = 'active' AND expires_at > ?) < 10
      AND NOT EXISTS (SELECT 1 FROM participations WHERE identity_id = ? AND mission_id = ? AND intent = ? AND status IN ('pending','published') AND state = 'active')
      ${buckets.map(() => 'AND COALESCE((SELECT count FROM rate_limits WHERE bucket = ?), 0) < ?').join('\n')}`)
      .bind(id, mission, identity.id, body.intent, body.participant_type, body.collaboration, title, summary, url, tokenHash, now, now, expires,
        mission, identity.id, identity.token_hash, now, identity.id, now, identity.id, mission, body.intent,
        ...buckets.flatMap((bucket, index) => [bucket, limits[index]])),
    ...buckets.map(bucket => env.DB.prepare(`INSERT OR IGNORE INTO rate_limits (bucket, count, expires_at)
      SELECT ?, 0, ? WHERE EXISTS (SELECT 1 FROM participations WHERE id = ?)`).bind(bucket, now + DAY, id)),
    env.DB.prepare(`UPDATE rate_limits SET count = count + 1 WHERE bucket IN (?, ?, ?, ?)
      AND EXISTS (SELECT 1 FROM participations WHERE id = ?)`).bind(...buckets, id),
    env.DB.prepare('SELECT bucket, count FROM rate_limits WHERE bucket IN (?, ?, ?, ?)').bind(...buckets),
  ];
  const result = await env.DB.batch(statements);
  if (result[1].meta.changes !== 1) {
    // Check identity ownership again after an in-flight token rotation/removal.
    await authenticateIdentity(request, env, now);
    await requireMission(env.DB, mission);
    if (await env.DB.prepare(`SELECT id FROM participations WHERE identity_id = ? AND mission_id = ? AND intent = ?
      AND status IN ('pending','published') AND state = 'active' AND expires_at > ?`).bind(identity.id, mission, body.intent, now).first()) {
      throw new ApiError(409, 'duplicate_participation', 'This identity already has an active card for this mission and intent. Find it in your own participation list.');
    }
    if ((await env.DB.prepare(`SELECT COUNT(*) AS count FROM participations WHERE identity_id = ?
      AND status IN ('pending','published') AND state = 'active' AND expires_at > ?`).bind(identity.id, now).first()).count >= 10) {
      throw new ApiError(409, 'active_limit', 'An identity may have at most ten active participation cards.');
    }
    const counts = new Map(result.at(-1).results.map(row => [row.bucket, row.count]));
    const exceeded = buckets.map((bucket, index) => (counts.get(bucket) || 0) >= limits[index]);
    if (exceeded.some(Boolean)) {
      const period = exceeded[1] || exceeded[3] ? DAY : 3_600_000;
      throw new ApiError(429, 'rate_limited', 'The participation submission limit has been reached.', undefined, Math.ceil((period - now % period) / 1000));
    }
    throw new ApiError(503, 'queue_full', 'The participation moderation queue is full. Try again later.');
  }
  return response({ id, status: 'pending', state: 'active', expires_at: new Date(expires).toISOString(), poll_url: `${PREFIX}/${id}`, receipt_token: token }, 202);
}

export async function listParticipations(request, env, now, mode = 'public') {
  const params = new URL(request.url).searchParams;
  const own = mode === 'mine';
  const admin = mode === 'admin';
  const { limit, cursor } = pagination(params, own ? ['limit', 'cursor'] : admin ? ['status', 'limit', 'cursor'] : ['mission_id', 'intent', 'state', 'limit', 'cursor']);
  const where = ['expires_at > ?'];
  const values = [now];
  const time = own || admin ? 'created_at' : 'published_at';
  if (own) {
    const identity = await authenticateIdentity(request, env, now);
    where.push('identity_id = ?'); values.push(identity.id);
  } else if (admin) {
    const status = params.get('status') || 'pending';
    if (!['pending', 'published', 'rejected'].includes(status)) invalid('status must be pending, published or rejected.', 'status');
    where.push('status = ?'); values.push(status);
  } else {
    where.push(visibleParticipation);
    const state = params.get('state') || 'active';
    if (!['active', 'closed', 'all'].includes(state)) invalid('state must be active, closed or all.', 'state');
    where.push(state === 'all' ? "state IN ('active','closed')" : 'state = ?');
    if (state !== 'all') values.push(state);
    if (params.has('mission_id')) {
      const mission = identifier(params.get('mission_id'), 'mission_id');
      await requireMission(env.DB, mission);
      where.push('mission_id = ?'); values.push(mission);
    }
    if (params.has('intent')) {
      if (!intents.has(params.get('intent'))) invalid('intent must be offer or need.', 'intent');
      where.push('intent = ?'); values.push(params.get('intent'));
    }
  }
  if (cursor) {
    where.push(`(${time} < ? OR (${time} = ? AND id < ?))`);
    values.push(cursor[0], cursor[0], cursor[1]);
  }
  const result = await env.DB.prepare(`SELECT ${fields} FROM participations WHERE ${where.join(' AND ')} ORDER BY ${time} DESC, id DESC LIMIT ?`).bind(...values, limit + 1).all();
  const rows = result.results.slice(0, limit);
  const last = rows.at(-1);
  return response({ items: rows.map(card), next_cursor: result.results.length > limit ? `${last[time]}:${last.id}` : null });
}

export async function participationReceipt(request, env, id, now) {
  const hash = await digest(bearer(request));
  const row = await env.DB.prepare(`SELECT ${fields}, receipt_hash FROM participations WHERE id = ? AND expires_at > ?`).bind(id, now).first();
  const matches = equalHash(hash, row?.receipt_hash || '0'.repeat(64));
  if (!row || !matches) throw new ApiError(404, 'not_found', 'No participation is available for this receipt.');
  delete row.receipt_hash;
  return response(card(row));
}

export async function updateParticipation(request, env, id, now) {
  const identity = await authenticateIdentity(request, env, now);
  // Owner checks precede state validation so foreign and unknown IDs are equal.
  const own = await env.DB.prepare('SELECT id FROM participations WHERE id = ? AND identity_id = ? AND expires_at > ?').bind(id, identity.id, now).first();
  if (!own) throw new ApiError(404, 'not_found', 'Own participation not found.');
  const body = await readJson(request, ['state']);
  if (!['closed', 'withdrawn'].includes(body.state)) invalid('state must be closed or withdrawn.', 'state');
  const result = await env.DB.prepare(`UPDATE participations SET state = ?, updated_at = CASE WHEN state = ? THEN updated_at ELSE ? END
    WHERE id = ? AND identity_id = ? AND expires_at > ?
    AND EXISTS (SELECT 1 FROM identities WHERE id = ? AND token_hash = ?)
    AND ((? = 'closed' AND status = 'published' AND state IN ('active','closed'))
      OR (? = 'withdrawn' AND ((status IN ('pending','published') AND state IN ('active','closed')) OR state = 'withdrawn')))
    RETURNING ${fields}`).bind(body.state, body.state, now, id, identity.id, now, identity.id, identity.token_hash, body.state, body.state).first();
  if (!result) {
    await authenticateIdentity(request, env, now);
    if (!await env.DB.prepare('SELECT id FROM participations WHERE id = ? AND identity_id = ? AND expires_at > ?').bind(id, identity.id, now).first()) {
      throw new ApiError(404, 'not_found', 'Own participation not found.');
    }
    throw new ApiError(409, 'invalid_transition', 'This participation cannot make the requested transition. Pending cards may be withdrawn, but only published active cards may be closed.');
  }
  return response(card(result));
}

export async function moderateParticipation(request, env, id, now) {
  const body = await readJson(request, ['status']);
  if (!['published', 'rejected'].includes(body.status)) invalid('status must be published or rejected.', 'status');
  const result = await env.DB.prepare(`UPDATE participations SET status = ?, updated_at = ?,
    published_at = CASE WHEN ? = 'published' THEN ? ELSE published_at END,
    expires_at = CASE WHEN ? = 'published' THEN ? ELSE expires_at END
    WHERE id = ? AND expires_at > ?
    AND ((? = 'published' AND status = 'pending' AND state = 'active' AND ${publishedMission} AND ${existingIdentity})
      OR (? = 'rejected' AND status IN ('pending','published')))
    RETURNING ${fields}`).bind(body.status, now, body.status, now, body.status, now + LIFETIME, id, now, body.status, body.status).first();
  if (!result) {
    if (!await env.DB.prepare('SELECT id FROM participations WHERE id = ? AND expires_at > ?').bind(id, now).first()) {
      throw new ApiError(404, 'not_found', 'Moderatable participation not found.');
    }
    throw new ApiError(409, 'invalid_transition', 'Only an active pending card with a published mission and existing identity can be published. Closed, withdrawn, rejected or expired cards cannot be reopened.');
  }
  return response(card(result));
}
