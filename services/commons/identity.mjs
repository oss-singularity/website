import { ApiError, response, invalid, readJson, identifier, digest, equalHash, bearer, randomToken } from './security.mjs';

const NETWORK = 'https://oss-singularity.io';
const DAY = 86_400_000;
const CHALLENGE_LIFETIME = 600_000;
const REVIEW_AGE = 30 * DAY;
const MAX_GITHUB_BYTES = 65_536;
const FILENAME = 'oss-singularity-identity.json';
const encoder = new TextEncoder();

export function githubLogin(value) {
  if (typeof value !== 'string' || !/^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$/i.test(value) || value.includes('--')) {
    invalid('github_login must be a valid GitHub account login (1–39 letters, digits or single hyphens).', 'github_login');
  }
  return value.toLowerCase();
}

export function gistId(value) {
  if (typeof value !== 'string' || value.length > 2048 || /[\s\\%]/u.test(value)) invalid('gist_url must be a public gist.github.com URL.', 'gist_url');
  let url;
  try { url = new URL(value); } catch { invalid('gist_url must be a public gist.github.com URL.', 'gist_url'); }
  const parts = url.pathname.split('/').slice(1);
  if (url.origin !== 'https://gist.github.com' || url.username || url.password || url.search || url.hash ||
      (parts.length !== 1 && parts.length !== 2) || !/^[a-f0-9]{1,64}$/i.test(parts.at(-1)) ||
      (parts.length === 2 && !/^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$/i.test(parts[0]))) {
    invalid('gist_url must identify one public GitHub gist without query parameters or fragments.', 'gist_url');
  }
  return parts.at(-1).toLowerCase();
}

function publicIdentity(row, now = Date.now()) {
  return {
    id: row.id, github_id: row.github_id, github_login: row.github_login,
    github_url: `https://github.com/${row.github_login}`,
    github_created_at: new Date(row.github_created_at).toISOString(),
    created_at: new Date(row.created_at).toISOString(), verified_at: new Date(row.verified_at).toISOString(),
    review_eligible: now >= row.github_created_at + REVIEW_AGE,
    review_eligible_at: new Date(row.github_created_at + REVIEW_AGE).toISOString(),
  };
}

export async function authenticateIdentity(request, env, now, review = false) {
  const token = bearer(request);
  const row = await env.DB.prepare('SELECT * FROM identities WHERE token_hash = ?').bind(await digest(token)).first();
  if (!row) throw new ApiError(401, 'unauthorized', 'A valid identity API token is required.');
  if (review && now < row.github_created_at + REVIEW_AGE) {
    throw new ApiError(403, 'review_age_required', 'Reviews require a verified GitHub account created at least 30 days ago.');
  }
  return row;
}

export async function getIdentity(env, id, now) {
  const row = await env.DB.prepare('SELECT * FROM identities WHERE id = ?').bind(id).first();
  if (!row) throw new ApiError(404, 'not_found', 'Identity not found.');
  return response(publicIdentity(row, now));
}

export async function cleanupChallenges(db, now, limit = 1000) {
  return db.batch([
    db.prepare('DELETE FROM identity_challenges WHERE id IN (SELECT id FROM identity_challenges WHERE expires_at <= ? ORDER BY expires_at LIMIT ?)').bind(now, limit),
    db.prepare('DELETE FROM rate_limits WHERE bucket IN (SELECT bucket FROM rate_limits WHERE expires_at <= ? ORDER BY expires_at LIMIT ?)').bind(now, limit),
  ]);
}

export async function createChallenge(request, env, now) {
  if (typeof env.IP_HMAC_SECRET !== 'string' || env.IP_HMAC_SECRET.length < 32) throw new ApiError(503, 'service_unavailable', 'Identity enrollment is temporarily unavailable.');
  const ip = request.headers.get('cf-connecting-ip');
  if (!ip || ip.length > 64 || !/^[0-9a-f:.]+$/i.test(ip)) throw new ApiError(503, 'service_unavailable', 'Identity enrollment is temporarily unavailable.');
  const body = await readJson(request, ['github_login']);
  const login = githubLogin(body.github_login);
  await cleanupChallenges(env.DB, now, 100);
  const hour = Math.floor(now / 3_600_000);
  const key = await crypto.subtle.importKey('raw', encoder.encode(env.IP_HMAC_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const rawHash = await crypto.subtle.sign('HMAC', key, encoder.encode(`identity-hour:${hour}\n${ip}`));
  const bucket = [...new Uint8Array(rawHash)].map((value) => value.toString(16).padStart(2, '0')).join('');
  const id = crypto.randomUUID();
  const nonce = randomToken();
  const challengeToken = randomToken();
  const expires = now + CHALLENGE_LIFETIME;
  const result = await env.DB.batch([
    env.DB.prepare(`INSERT OR IGNORE INTO rate_limits (bucket, count, expires_at) SELECT ?, 0, ?
      WHERE (SELECT COUNT(*) FROM identity_challenges WHERE consumed_at IS NULL AND expires_at > ?) < 200`).bind(bucket, now + DAY, now),
    env.DB.prepare(`INSERT INTO identity_challenges (id, github_login, nonce_hash, token_hash, created_at, expires_at)
      SELECT ?, ?, ?, ?, ?, ? WHERE (SELECT count FROM rate_limits WHERE bucket = ?) < 3
      AND (SELECT COUNT(*) FROM identity_challenges WHERE consumed_at IS NULL AND expires_at > ?) < 200`)
      .bind(id, login, await digest(nonce), await digest(challengeToken), now, expires, bucket, now),
    env.DB.prepare('UPDATE rate_limits SET count = count + 1 WHERE bucket = ? AND EXISTS (SELECT 1 FROM identity_challenges WHERE id = ?)').bind(bucket, id),
    env.DB.prepare('SELECT count FROM rate_limits WHERE bucket = ?').bind(bucket),
  ]);
  if (result[1].meta.changes !== 1) {
    if (result[3].results[0]?.count >= 3) throw new ApiError(429, 'rate_limited', 'The identity challenge limit has been reached.', undefined, Math.ceil((3_600_000 - now % 3_600_000) / 1000));
    throw new ApiError(503, 'queue_full', 'The identity challenge queue is full. Try again later.');
  }
  return response({ id, proof: { network: NETWORK, challenge_id: id, nonce }, challenge_token: challengeToken, gist_filename: FILENAME, expires_at: new Date(expires).toISOString() }, 201);
}

async function githubJson(path) {
  // Callers construct only these two fixed GitHub API paths; never fetch raw_url,
  // redirects, avatars or any URL supplied by a remote response or proposal.
  if (!/^\/(?:gists\/[a-f0-9]{1,64}|users\/[a-z0-9][a-z0-9-]{0,38})$/.test(path)) throw new ApiError(503, 'service_unavailable', 'Identity verification is temporarily unavailable.');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const result = await fetch(`https://api.github.com${path}`, {
      redirect: 'manual', signal: controller.signal,
      headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'OSS-Singularity-Identity', 'X-GitHub-Api-Version': '2026-03-10' },
    });
    if (result.status === 404) invalid('The public GitHub proof or account could not be found.', 'gist_url');
    if (!result.ok || result.redirected) throw new ApiError(503, 'upstream_unavailable', 'GitHub verification is temporarily unavailable. Try again later.');
    if (Number(result.headers.get('content-length')) > MAX_GITHUB_BYTES) invalid('The GitHub proof response is too large; use a small gist containing only the proof.', 'gist_url');
    const reader = result.body?.getReader();
    if (!reader) throw new ApiError(503, 'upstream_unavailable', 'GitHub returned an incomplete response.');
    const chunks = [];
    let size = 0;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_GITHUB_BYTES) { await reader.cancel(); invalid('The GitHub proof response is too large.', 'gist_url'); }
        chunks.push(value);
      }
    } finally { reader.releaseLock(); }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
    catch { throw new ApiError(503, 'upstream_unavailable', 'GitHub returned an invalid response.'); }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(503, 'upstream_unavailable', 'GitHub verification is temporarily unavailable. Try again later.');
  } finally { clearTimeout(timer); }
}

export async function verifyIdentity(request, env, now) {
  const body = await readJson(request, ['challenge_id', 'gist_url', 'rotate']);
  const id = identifier(body.challenge_id, 'challenge_id');
  const gist = gistId(body.gist_url);
  if (body.rotate !== undefined && typeof body.rotate !== 'boolean') invalid('rotate must be a boolean.', 'rotate');
  if (env.IDENTITY_VERIFICATION_DISABLED === 'true') throw new ApiError(503, 'service_unavailable', 'External identity verification is disabled in this local environment.');
  const challengeHash = await digest(bearer(request));
  const challenge = await env.DB.prepare(`UPDATE identity_challenges SET verification_attempts = verification_attempts + 1
    WHERE id = ? AND token_hash = ? AND consumed_at IS NULL AND expires_at > ? AND verification_attempts < 3
    RETURNING id, github_login, nonce_hash, expires_at`).bind(id, challengeHash, now).first();
  if (!challenge) throw new ApiError(401, 'unauthorized', 'A valid unexpired challenge token with remaining verification attempts is required.');
  const document = await githubJson(`/gists/${gist}`);
  const owner = document?.owner;
  const file = document?.files?.[FILENAME];
  if (document?.public !== true || document.truncated === true || !owner || !Number.isSafeInteger(owner.id) || owner.id <= 0 ||
      githubLogin(owner.login) !== challenge.github_login || !file || file.truncated !== false || typeof file.content !== 'string' || encoder.encode(file.content).byteLength > 8192) {
    invalid('The gist must be public, owned by the challenged GitHub account, and contain the complete proof file.', 'gist_url');
  }
  let proof;
  try { proof = JSON.parse(file.content); } catch { invalid('The gist proof file must contain the exact challenge JSON object.', 'gist_url'); }
  if (!proof || Array.isArray(proof) || Object.keys(proof).sort().join(',') !== 'challenge_id,network,nonce' ||
      proof.network !== NETWORK || proof.challenge_id !== challenge.id || typeof proof.nonce !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(proof.nonce) ||
      !equalHash(await digest(proof.nonce), challenge.nonce_hash)) invalid('The gist proof does not match this challenge.', 'gist_url');
  const account = await githubJson(`/users/${challenge.github_login}`);
  const created = Date.parse(account?.created_at);
  if (!account || account.id !== owner.id || githubLogin(account.login) !== challenge.github_login || typeof account.created_at !== 'string' || !Number.isFinite(created) || created > now || created < 0) {
    invalid('GitHub account identity and creation time could not be verified.', 'github_login');
  }
  const existing = await env.DB.prepare('SELECT id FROM identities WHERE github_id = ?').bind(account.id).first();
  if (existing && body.rotate !== true) throw new ApiError(409, 'identity_exists', 'This GitHub account already has an identity. Explicitly request rotate: true with fresh proof to replace its API token.');
  const token = randomToken();
  const tokenHash = await digest(token);
  const identityId = crypto.randomUUID();
  const completedAt = Date.now();
  const result = await env.DB.batch([
    env.DB.prepare(`INSERT INTO identities (id, github_id, github_login, github_created_at, created_at, verified_at, token_hash)
      SELECT ?, ?, ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM identity_challenges WHERE id = ? AND consumed_at IS NULL AND expires_at > ?)
      ON CONFLICT(github_id) DO UPDATE SET github_login = excluded.github_login, github_created_at = excluded.github_created_at,
        verified_at = excluded.verified_at, token_hash = excluded.token_hash WHERE ? = 1`)
      .bind(identityId, account.id, challenge.github_login, created, completedAt, completedAt, tokenHash, id, completedAt, body.rotate === true ? 1 : 0),
    env.DB.prepare(`UPDATE identity_challenges SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL AND expires_at > ?
      AND EXISTS (SELECT 1 FROM identities WHERE github_id = ? AND token_hash = ?)`)
      .bind(completedAt, id, completedAt, account.id, tokenHash),
    env.DB.prepare('SELECT * FROM identities WHERE github_id = ? AND token_hash = ?').bind(account.id, tokenHash),
  ]);
  if (result[0].meta.changes !== 1 || result[1].meta.changes !== 1 || !result[2].results[0]) {
    if (await env.DB.prepare('SELECT id FROM identities WHERE github_id = ?').bind(account.id).first()) throw new ApiError(409, 'identity_exists', 'Identity enrollment changed concurrently or this proof was consumed. Request fresh proof before an explicit token rotation.');
    invalid('The challenge expired or was consumed during verification. Request a new challenge.', 'challenge_id');
  }
  const rotated = result[2].results[0].id !== identityId;
  return response({ identity: publicIdentity(result[2].results[0], completedAt), api_token: token, rotated }, rotated ? 200 : 201);
}
