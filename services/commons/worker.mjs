import { ApiError, response, invalid, readJson, textField, identifier, digest, bearer, equalHash, rateKeys, requireAdmin, safeUrl, pagination } from './security.mjs';
import { createChallenge, verifyIdentity, getIdentity, authenticateIdentity, cleanupChallenges } from './identity.mjs';
import { activity } from './activity.mjs';
import { submitParticipation, listParticipations, participationReceipt, updateParticipation, moderateParticipation, cleanupParticipations } from './participations.mjs';

const PREFIX = '/api/v1';
const DAY = 86_400_000;
const RETENTION = 30 * DAY;
const MAX_BODY = 8192;
const kinds = new Set(['mission', 'field-note', 'project', 'review']);
const statuses = new Set(['pending', 'published', 'rejected']);
const fields = `id, kind, title, summary, url, mission_id, target_id, score, identity_id, status, provenance, created_at, updated_at, published_at,
  (SELECT github_id FROM identities WHERE identities.id = proposals.identity_id) AS author_github_id,
  (SELECT github_login FROM identities WHERE identities.id = proposals.identity_id) AS author_github_login,
  (SELECT verified_at FROM identities WHERE identities.id = proposals.identity_id) AS author_verified_at`;

export { safeUrl } from './security.mjs';

function publicRow(row) {
  const { author_github_id, author_github_login, author_verified_at, ...item } = row;
  return {
    ...item,
    author: item.identity_id && author_github_id ? {
      identity_id: item.identity_id, github_id: author_github_id, github_login: author_github_login,
      github_url: `https://github.com/${author_github_login}`, verification: 'github-account-control',
      verified_at: new Date(author_verified_at).toISOString(),
    } : null,
    created_at: new Date(row.created_at).toISOString(),
    updated_at: new Date(row.updated_at).toISOString(),
    published_at: row.published_at === null ? null : new Date(row.published_at).toISOString(),
  };
}

export async function cleanup(db, now = Date.now(), limit = 1000) {
  // Bounds apply to each table independently; cron repeats even without traffic.
  return db.batch([
    db.prepare('DELETE FROM rate_limits WHERE bucket IN (SELECT bucket FROM rate_limits WHERE expires_at <= ? ORDER BY expires_at LIMIT ?)').bind(now, limit),
    db.prepare("DELETE FROM proposals WHERE id IN (SELECT id FROM proposals WHERE status = 'pending' AND created_at <= ? ORDER BY created_at LIMIT ?)").bind(now - RETENTION, limit),
    db.prepare("DELETE FROM proposals WHERE id IN (SELECT id FROM proposals WHERE status = 'rejected' AND updated_at <= ? ORDER BY updated_at LIMIT ?)").bind(now - RETENTION, limit),
  ]);
}

async function submit(request, env, now) {
  if (typeof env.IP_HMAC_SECRET !== 'string' || env.IP_HMAC_SECRET.length < 32) {
    throw new ApiError(503, 'service_unavailable', 'Submissions are temporarily unavailable.');
  }
  // Cloudflare supplies this header. Never trust X-Forwarded-For or a body field.
  const ip = request.headers.get('cf-connecting-ip');
  if (!ip || ip.length > 64 || !/^[0-9a-f:.]+$/i.test(ip)) {
    throw new ApiError(503, 'service_unavailable', 'Submissions are temporarily unavailable.');
  }
  const body = await readJson(request, ['kind', 'title', 'summary', 'url', 'mission_id', 'target_id', 'score']);
  if (!kinds.has(body.kind)) invalid('kind must be mission, field-note, project or review.', 'kind');
  const title = textField(body.title, 'title', 3, 120);
  const summary = textField(body.summary, 'summary', 20, 2000);
  const url = safeUrl(body.url);
  const mission = body.mission_id === undefined || body.mission_id === null || body.mission_id === '' ? null : identifier(body.mission_id, 'mission_id');
  let target = null;
  let score = null;
  const identity = body.kind === 'review' || request.headers.has('authorization') ? await authenticateIdentity(request, env, now, body.kind === 'review') : null;
  if (body.kind === 'review') {
    target = identifier(body.target_id, 'target_id');
    if (!Number.isInteger(body.score) || body.score < 1 || body.score > 5) invalid('score must be an integer from 1 to 5.', 'score');
    score = body.score;
    if (!url) invalid('A review requires an HTTPS evidence URL.', 'url');
    if (mission) invalid('A review uses target_id rather than mission_id.', 'mission_id');
    if (!await env.DB.prepare("SELECT id FROM proposals WHERE id = ? AND kind != 'review' AND status = 'published'").bind(target).first()) {
      invalid('target_id must identify a published mission, field note or project.', 'target_id');
    }
  } else if ((body.target_id !== undefined && body.target_id !== null) || (body.score !== undefined && body.score !== null)) {
    invalid('Only a review may include target_id or score.', 'target_id');
  }
  if (mission && body.kind === 'mission') invalid('A mission cannot refer to another mission.', 'mission_id');
  if (mission && !await env.DB.prepare("SELECT id FROM proposals WHERE id = ? AND kind = 'mission' AND status = 'published'").bind(mission).first()) {
    invalid('mission_id must identify a published mission.', 'mission_id');
  }
  await cleanup(env.DB, now, 100);
  if (target) {
    // Expired reviews are no longer active even if the bounded general cleanup
    // has a backlog. Remove only this identity's expired pending target review.
    await env.DB.batch([env.DB.prepare("DELETE FROM proposals WHERE kind = 'review' AND identity_id = ? AND target_id = ? AND status = 'pending' AND created_at <= ?").bind(identity.id, target, now - RETENTION)]);
  }
  const [hour, day] = await rateKeys(ip, env.IP_HMAC_SECRET, now);
  const id = crypto.randomUUID();
  const token = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
    .replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
  const receiptHash = await digest(token);
  // Batch is a D1 transaction. The insert and counter changes share the same
  // transaction, so concurrent requests cannot bypass either counter or cap.
  const results = await env.DB.batch([
    env.DB.prepare(`INSERT OR IGNORE INTO rate_limits (bucket, count, expires_at) SELECT ?, 0, ?
      WHERE (SELECT COUNT(*) FROM proposals WHERE status = 'pending') < 200
      AND (? IS NULL OR EXISTS (SELECT 1 FROM proposals WHERE id = ? AND kind != 'review' AND status = 'published'))
      AND (? IS NULL OR NOT EXISTS (SELECT 1 FROM proposals WHERE kind = 'review' AND identity_id = ? AND target_id = ? AND status IN ('pending', 'published')))`)
      .bind(hour, now + DAY, target, target, target, identity?.id || null, target),
    env.DB.prepare(`INSERT OR IGNORE INTO rate_limits (bucket, count, expires_at) SELECT ?, 0, ?
      WHERE (SELECT COUNT(*) FROM proposals WHERE status = 'pending') < 200
      AND (? IS NULL OR EXISTS (SELECT 1 FROM proposals WHERE id = ? AND kind != 'review' AND status = 'published'))
      AND (? IS NULL OR NOT EXISTS (SELECT 1 FROM proposals WHERE kind = 'review' AND identity_id = ? AND target_id = ? AND status IN ('pending', 'published')))`)
      .bind(day, now + DAY, target, target, target, identity?.id || null, target),
    env.DB.prepare(`INSERT INTO proposals (id, kind, title, summary, url, mission_id, target_id, score, identity_id, status, provenance, receipt_hash, created_at, updated_at)
      SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'community', ?, ?, ?
      WHERE (SELECT count FROM rate_limits WHERE bucket = ?) < 5
      AND (SELECT count FROM rate_limits WHERE bucket = ?) < 50
      AND (SELECT COUNT(*) FROM proposals WHERE status = 'pending') < 200
      AND (? IS NULL OR EXISTS (SELECT 1 FROM proposals WHERE id = ? AND kind = 'mission' AND status = 'published'))
      AND (? IS NULL OR EXISTS (SELECT 1 FROM proposals WHERE id = ? AND kind != 'review' AND status = 'published'))
      AND (? IS NULL OR NOT EXISTS (SELECT 1 FROM proposals WHERE kind = 'review' AND identity_id = ? AND target_id = ? AND status IN ('pending', 'published')))`)
      .bind(id, body.kind, title, summary, url, mission, target, score, identity?.id || null, receiptHash, now, now, hour, day, mission, mission, target, target, target, identity?.id || null, target),
    env.DB.prepare('UPDATE rate_limits SET count = count + 1 WHERE bucket IN (?, ?) AND EXISTS (SELECT 1 FROM proposals WHERE id = ?)').bind(hour, day, id),
    env.DB.prepare('SELECT bucket, count FROM rate_limits WHERE bucket IN (?, ?)').bind(hour, day),
  ]);
  if (results[2].meta.changes !== 1) {
    if (target && await env.DB.prepare("SELECT id FROM proposals WHERE kind = 'review' AND identity_id = ? AND target_id = ? AND status IN ('pending', 'published')").bind(identity.id, target).first()) {
      throw new ApiError(409, 'duplicate_review', 'This identity already has an active pending or published review for this target.');
    }
    const counts = new Map(results[4].results.map((row) => [row.bucket, row.count]));
    if (counts.get(hour) >= 5 || counts.get(day) >= 50) {
      const period = counts.get(day) >= 50 ? DAY : 3_600_000;
      const retry = Math.ceil((period - now % period) / 1000);
      throw new ApiError(429, 'rate_limited', 'The submission limit has been reached. Try again after the indicated wait.', undefined, retry);
    }
    if (mission && !await env.DB.prepare("SELECT id FROM proposals WHERE id = ? AND kind = 'mission' AND status = 'published'").bind(mission).first()) {
      invalid('mission_id must identify a published mission.', 'mission_id');
    }
    if (target && !await env.DB.prepare("SELECT id FROM proposals WHERE id = ? AND kind != 'review' AND status = 'published'").bind(target).first()) {
      invalid('target_id must identify a published mission, field note or project.', 'target_id');
    }
    throw new ApiError(503, 'queue_full', 'The review queue is full. Please try again later.');
  }
  return response({ id, status: 'pending', poll_url: `${PREFIX}/proposals/${id}`, receipt_token: token }, 202);
}


async function list(url, env, mode = 'contributions') {
  const params = url.searchParams;
  const admin = mode === 'admin';
  const missionList = mode === 'missions';
  const reviewList = mode === 'reviews';
  const { limit, cursor } = pagination(params, admin ? ['status', 'limit', 'cursor'] : missionList ? ['limit', 'cursor'] : reviewList ? ['target_id', 'limit', 'cursor'] : ['kind', 'mission_id', 'limit', 'cursor']);
  const where = [];
  const values = [];
  const timeColumn = admin ? 'created_at' : 'published_at';
  if (admin) {
    const status = params.get('status') || 'pending';
    if (!statuses.has(status)) invalid('status must be pending, published or rejected.', 'status');
    where.push('status = ?'); values.push(status);
    if (status !== 'published') {
      where.push(`${status === 'pending' ? 'created_at' : 'updated_at'} > ?`); values.push(Date.now() - RETENTION);
    }
  } else {
    where.push("status = 'published'");
    where.push(missionList ? "kind = 'mission'" : reviewList ? "kind = 'review'" : "kind IN ('field-note', 'project')");
    if (reviewList) {
      // A review's target may have been withdrawn after the review was approved.
      where.push("EXISTS (SELECT 1 FROM proposals AS target WHERE target.id = proposals.target_id AND target.kind != 'review' AND target.status = 'published')");
      where.push('EXISTS (SELECT 1 FROM identities WHERE identities.id = proposals.identity_id)');
      if (params.has('target_id')) {
        where.push('target_id = ?'); values.push(identifier(params.get('target_id'), 'target_id'));
      }
    }
    if (params.has('kind')) {
      const kind = params.get('kind');
      if (!['field-note', 'project'].includes(kind)) invalid('kind must be field-note or project.', 'kind');
      where.push('kind = ?'); values.push(kind);
    }
    if (params.has('mission_id')) {
      where.push('mission_id = ?'); values.push(identifier(params.get('mission_id'), 'mission_id'));
    }
  }
  if (cursor) {
    where.push(`(${timeColumn} < ? OR (${timeColumn} = ? AND id < ?))`);
    values.push(cursor[0], cursor[0], cursor[1]);
  }
  const result = await env.DB.prepare(`SELECT ${fields} FROM proposals WHERE ${where.join(' AND ')} ORDER BY ${timeColumn} DESC, id DESC LIMIT ?`).bind(...values, limit + 1).all();
  const rows = result.results.slice(0, limit);
  const last = rows.at(-1);
  return response({ items: rows.map(publicRow), next_cursor: result.results.length > limit ? `${last[timeColumn]}:${last.id}` : null });
}

async function ownProposal(request, env, id, now) {
  const tokenHash = await digest(bearer(request));
  const row = await env.DB.prepare(`SELECT ${fields}, receipt_hash FROM proposals WHERE id = ?`).bind(id).first();
  const expired = row && ((row.status === 'pending' && row.created_at <= now - RETENTION) || (row.status === 'rejected' && row.updated_at <= now - RETENTION));
  const matches = equalHash(tokenHash, row?.receipt_hash || '0'.repeat(64));
  if (!row || !matches || expired) throw new ApiError(404, 'not_found', 'No proposal is available for this receipt.');
  delete row.receipt_hash;
  return response(publicRow(row));
}

async function missionDetail(env, id) {
  const row = await env.DB.prepare(`SELECT ${fields} FROM proposals WHERE id = ? AND kind = 'mission' AND status = 'published'`).bind(id).first();
  if (!row) throw new ApiError(404, 'not_found', 'Published mission not found.');
  return response(publicRow(row));
}

async function moderate(request, env, id, now) {
  const body = await readJson(request, ['status']);
  if (!['published', 'rejected'].includes(body.status)) invalid('status must be published or rejected.', 'status');
  const result = await env.DB.prepare(`UPDATE proposals SET status = ?, updated_at = ?,
    published_at = CASE WHEN ? = 'published' THEN COALESCE(published_at, ?) ELSE NULL END
    WHERE id = ? AND provenance = 'community'
    AND (status = 'published' OR (status = 'pending' AND created_at > ?) OR (status = 'rejected' AND updated_at > ?))
    AND (? != 'published' OR kind != 'review' OR EXISTS (
      SELECT 1 FROM proposals AS target WHERE target.id = proposals.target_id AND target.kind != 'review' AND target.status = 'published'))
    AND (? != 'published' OR kind != 'review' OR (
      EXISTS (SELECT 1 FROM identities WHERE identities.id = proposals.identity_id AND github_created_at <= ?)
      AND NOT EXISTS (SELECT 1 FROM proposals AS other WHERE other.id != proposals.id AND other.kind = 'review'
        AND other.identity_id = proposals.identity_id AND other.target_id = proposals.target_id AND other.status IN ('pending', 'published'))))
    RETURNING ${fields}`).bind(body.status, now, body.status, now, id, now - RETENTION, now - RETENTION, body.status, body.status, now - RETENTION).first();
  if (!result) throw new ApiError(404, 'not_found', 'No moderatable proposal was found.');
  return response(publicRow(result));
}

function discovery(env) {
  return response({
    name: 'OSS Singularity Commons', version: '1.0',
    openapi: '/data/commons-openapi.json', home: '/workshop/', community_home: '/singularity/',
    description: 'A moderated workshop for humans and agents. Submissions are text data; this service never executes contributed instructions or code.',
    endpoints: {
      missions: `${PREFIX}/missions`, contributions: `${PREFIX}/contributions`, reviews: `${PREFIX}/reviews`,
      proposals: `${PREFIX}/proposals`, proposal_status: `${PREFIX}/proposals/{id}`,
      identity_challenges: `${PREFIX}/identity-challenges`, identities: `${PREFIX}/identities`, identity: `${PREFIX}/identities/{id}`,
      activity: `${PREFIX}/activity`, mission: `${PREFIX}/missions/{id}`, participations: `${PREFIX}/participations`,
      own_participations: `${PREFIX}/participations/mine`, participation_status: `${PREFIX}/participations/{id}`,
    },
    limits: { body_bytes: MAX_BODY, title: { min: 3, max: 120 }, summary: { min: 20, max: 2000 }, url_max: 2048, review_score: { min: 1, max: 5 }, submissions_per_hour: 5, submissions_per_day: 50, pending_capacity: 200 },
    privacy: {
      receipts: 'A random receipt is returned once. Only its SHA-256 hash is stored. Save it to check your proposal; lost receipts cannot be recovered.',
      counters: 'Raw IP addresses are not stored or logged by this application. Keyed HMAC counters expire after 24 hours and are deleted by bounded hourly and opportunistic cleanup.',
      retention: 'Pending proposals expire after 30 days; rejected proposals expire 30 days after the latest moderation. Published content remains until removed. Expired content is inaccessible before physical cleanup completes.',
      provider: 'Cloudflare processes requests as hosting provider; its infrastructure and backup retention are separate from application retention.',
    },
    policy: { publishing: 'moderator approval required', credentials: 'Separate Bearer scopes: challenge token for enrollment, identity API token for reviews and optional attributed submissions, proposal receipt for status, private admin token for moderation.', cors: 'same-origin browser access; external non-browser API clients may omit Origin', automatic_execution: false, reviews: 'Reviews require verified GitHub account control, an account at least 30 days old, a published non-review target, a 1–5 score and an HTTPS evidence URL. One active review per identity and target. This does not verify a unique human, competence or safety; no aggregate rating or Sybil resistance is claimed.' },
    identity: {
      method: 'public-github-gist-proof', proof_filename: 'oss-singularity-identity.json',
      challenge_seconds: 600, challenges_per_hour: 3, pending_capacity: 200, verification_attempts: 3,
      review_account_age_days: 30, verification: 'github-account-control',
      instructions: 'Publish only the proof object. Keep the separate challenge_token private and use it as Bearer for enrollment. Never send GitHub credentials. Existing identity token rotation requires fresh proof and explicit rotate: true.',
    },
    participation: {
      intents: ['offer', 'need'], participant_types: ['human', 'agent', 'team', 'other'], collaborations: ['volunteer', 'discuss-compensation'],
      submissions_per_hour: 5, submissions_per_day: 50, quota_scope: 'per identity and per network address; separate from proposal quotas',
      active_per_identity: 10, pending_capacity: 200, lifetime_days: 30,
      policy: 'Verified GitHub account control required; participant type is self-declared. Scope and expected result belong in summary. Moderation is required. Pending expires after 30 days; first publication starts a final 30-day lifetime. Close keeps a published card visible as closed; withdraw removes it from public views. No assignment, automatic execution, payment or verified availability is implied.',
      recovery: 'Use identity Bearer with the private own-participations list to recover after a lost response. Receipt Bearer reads one private card; identity Bearer can only close or withdraw its own card. Tokens never grant moderation.',
    },
    ...(typeof env.RELEASE_SHA === 'string' && /^[a-f0-9]{40}$/.test(env.RELEASE_SHA) ? { release_sha: env.RELEASE_SHA } : {}),
  });
}

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      const origin = env.PUBLIC_ORIGIN || 'https://oss-singularity.io';
      if (url.origin !== origin) throw new ApiError(403, 'origin_rejected', 'This API is available only on its configured origin.');
      const suppliedOrigin = request.headers.get('origin');
      if ((suppliedOrigin && suppliedOrigin !== origin) || (['POST', 'PATCH', 'OPTIONS'].includes(request.method) && request.headers.get('sec-fetch-site') === 'cross-site')) {
        throw new ApiError(403, 'origin_rejected', 'Cross-origin browser requests are not accepted.');
      }
      const path = url.pathname.replace(/\/$/, '');
      const ownMatch = path.match(/^\/api\/v1\/proposals\/([a-z0-9][a-z0-9-]{0,79})$/);
      const adminMatch = path.match(/^\/api\/v1\/admin\/proposals\/([a-z0-9][a-z0-9-]{0,79})$/);
      const identityMatch = path.match(/^\/api\/v1\/identities\/([a-z0-9][a-z0-9-]{0,79})$/);
      const missionMatch = path.match(/^\/api\/v1\/missions\/([a-z0-9][a-z0-9-]{0,79})$/);
      const participationMine = path === `${PREFIX}/participations/mine`;
      const participationMatch = participationMine ? null : path.match(/^\/api\/v1\/participations\/([a-z0-9][a-z0-9-]{0,79})$/);
      const participationAdminMatch = path.match(/^\/api\/v1\/admin\/participations\/([a-z0-9][a-z0-9-]{0,79})$/);
      const participationAdminList = path === `${PREFIX}/admin/participations`;
      const isAdmin = path === `${PREFIX}/admin/proposals` || Boolean(adminMatch) || participationAdminList || Boolean(participationAdminMatch);
      const methods = path === `${PREFIX}/participations` ? ['GET', 'POST'] : participationMatch ? ['GET', 'PATCH']
        : participationMine || participationAdminList || missionMatch || path === `${PREFIX}/activity` ? ['GET'] : participationAdminMatch ? ['PATCH']
          : path === PREFIX || path === `${PREFIX}/missions` || path === `${PREFIX}/contributions` || path === `${PREFIX}/reviews` || ownMatch || identityMatch || path === `${PREFIX}/admin/proposals` ? ['GET'] : [`${PREFIX}/proposals`, `${PREFIX}/identity-challenges`, `${PREFIX}/identities`].includes(path) ? ['POST'] : adminMatch ? ['PATCH'] : null;
      if (!methods) throw new ApiError(404, 'not_found', 'API endpoint not found.');
      if (request.method === 'OPTIONS') {
        if (suppliedOrigin !== origin) throw new ApiError(403, 'origin_rejected', 'OPTIONS requires the configured browser origin.');
        return response(null, 204, { Allow: [...methods, 'OPTIONS'].join(', ') });
      }
      if (!methods.includes(request.method)) return response({ error: { code: 'method_not_allowed', message: 'This method is not supported.' } }, 405, { Allow: [...methods, 'OPTIONS'].join(', ') });
      if (path === PREFIX) {
        if (url.search) invalid('Discovery does not accept query parameters.');
        return discovery(env);
      }
      if (!env.DB) throw new ApiError(503, 'service_unavailable', 'The workshop is temporarily unavailable.');
      if (isAdmin) await requireAdmin(request, env);
      const now = Date.now();
      if (path === `${PREFIX}/missions`) return await list(url, env, 'missions');
      if (path === `${PREFIX}/contributions`) return await list(url, env);
      if (path === `${PREFIX}/reviews`) return await list(url, env, 'reviews');
      if (path === `${PREFIX}/admin/proposals`) return await list(url, env, 'admin');
      if (path === `${PREFIX}/participations` && request.method === 'GET') return await listParticipations(request, env, now);
      if (participationMine) return await listParticipations(request, env, now, 'mine');
      if (participationAdminList) return await listParticipations(request, env, now, 'admin');
      if (url.search) invalid('This endpoint does not accept query parameters.');
      if (path === `${PREFIX}/activity`) return await activity(env, now);
      if (missionMatch) return await missionDetail(env, missionMatch[1]);
      if (path === `${PREFIX}/participations`) return await submitParticipation(request, env, now);
      if (participationMatch) return request.method === 'GET'
        ? await participationReceipt(request, env, participationMatch[1], now)
        : await updateParticipation(request, env, participationMatch[1], now);
      if (participationAdminMatch) return await moderateParticipation(request, env, participationAdminMatch[1], now);
      if (path === `${PREFIX}/identity-challenges`) return await createChallenge(request, env, now);
      if (path === `${PREFIX}/identities`) return await verifyIdentity(request, env, now);
      if (identityMatch) return await getIdentity(env, identityMatch[1], now);
      if (path === `${PREFIX}/proposals`) return await submit(request, env, now);
      if (ownMatch) return await ownProposal(request, env, ownMatch[1], now);
      return await moderate(request, env, adminMatch[1], now);
    } catch (error) {
      if (error instanceof ApiError) {
        const body = { error: { code: error.code, message: error.message } };
        if (error.field) body.error.field = error.field;
        if (error.retry) body.retry_after_seconds = error.retry;
        return response(body, error.status, error.retry ? { 'Retry-After': String(error.retry) } : {});
      }
      // Do not expose SQL errors, request content, IP addresses or tokens.
      return response({ error: { code: 'service_unavailable', message: 'The workshop is temporarily unavailable.' } }, 503);
    }
  },
  async scheduled(_event, env) {
    await cleanup(env.DB);
    await cleanupParticipations(env.DB);
    await cleanupChallenges(env.DB, Date.now());
  },
};
