import { ApiError, response, invalid, readJson, textField, identifier, safeUrl } from './security.mjs';
import { authenticateIdentity } from './identity.mjs';

const sqlUuid = "lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-8' || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))";
const identityColumns = (name) => ['github_id', 'github_login', 'verified_at'].map(key => `${name}.${key} AS ${name}_${key}`).join(', ');
const iso = value => value == null ? null : new Date(value).toISOString();
const version = value => { if (!Number.isSafeInteger(value) || value < 1) invalid('expected_version must be a positive integer.', 'expected_version'); return value; };

const MEDIA_TYPES = ['text/plain', 'text/markdown', 'application/json', 'application/pdf', 'application/zip', 'application/octet-stream'];
const MAX_REVISIONS = 10;
const NOTICE = 'A manifest records a delivery and its declared integrity metadata; the service fetches and verifies no artifact bytes, establishes no quality, authorship or acceptance, and authorizes no payment.';

const deliverySelect = `SELECT d.*, ${identityColumns('author')} FROM deliveries d
  JOIN identities author ON author.id = d.author_identity_id`;

async function projectAndMilestone(db, projectId, milestoneId) {
  const row = await db.prepare(`SELECT m.id AS milestone_id, m.status AS milestone_status, m.scope_version,
      p.id AS project_id, p.status AS project_status, p.version AS project_version
    FROM milestones m JOIN projects p ON p.id = m.project_id
    WHERE m.id = ? AND m.project_id = ?`).bind(milestoneId, projectId).first();
  if (!row) throw new ApiError(404, 'not_found', 'Milestone not found in this project.');
  return row;
}

function deliveryView(row) {
  return {
    id: row.id, project_id: row.project_id, milestone_id: row.milestone_id, revision: row.revision,
    scope_version: row.scope_version, summary: row.summary,
    artifact: {
      url: row.artifact_url, media_type: row.artifact_media_type, size_bytes: row.artifact_size_bytes,
      integrity: { algorithm: row.integrity_algorithm, digest: row.integrity_digest },
      content_identifier: row.content_identifier ?? null,
    },
    evidence_url: row.evidence_url ?? null,
    retention: row.retention_retained_by ? {
      retained_by: row.retention_retained_by,
      retained_until: row.retention_retained_until ?? null,
      access: row.retention_access ?? 'public',
      on_unavailable: row.retention_on_unavailable ?? null,
    } : null,
    author: row.author_github_id ? {
      identity_id: row.author_identity_id, github_id: row.author_github_id, github_login: row.author_github_login,
      github_url: `https://github.com/${row.author_github_login}`, verification: 'github-account-control',
      verified_at: iso(row.author_verified_at) } : null,
    created_at: iso(row.created_at),
  };
}

export async function submitDelivery(request, env, projectId, milestoneId, now) {
  const actor = await authenticateIdentity(request, env, now);
  const context = await projectAndMilestone(env.DB, projectId, milestoneId);
  if (context.project_status === 'cancelled') throw new ApiError(404, 'not_found', 'Milestone not found in this project.');
  if (context.project_status !== 'open' || context.milestone_status !== 'open') {
    throw new ApiError(409, 'milestone_closed', 'Only an open milestone of an open project accepts deliveries.');
  }
  // A delivery comes from the bound contributor: an identity holding a confirmed
  // commitment on exactly this milestone. The coordinator reviews; they do not
  // deliver for the contributor.
  const bound = await env.DB.prepare(`SELECT id FROM commitments
    WHERE milestone_id = ? AND contributor_identity_id = ? AND status = 'confirmed'`)
    .bind(milestoneId, actor.id).first();
  if (!bound) throw new ApiError(403, 'forbidden', 'Only the confirmed contributor of this milestone submits deliveries.');
  const body = await readJson(request, ['summary', 'artifact_url', 'artifact_media_type', 'artifact_size_bytes',
    'integrity_digest', 'content_identifier', 'evidence_url', 'retention', 'expected_version']);
  const retention = { retained_by: null, retained_until: null, access: null, on_unavailable: null };
  if (body.retention !== undefined && body.retention !== null) {
    if (typeof body.retention !== 'object' || Array.isArray(body.retention)) invalid('retention must be an object naming the declared retention rules.', 'retention');
    if (!['contributor', 'coordinator', 'third-party'].includes(body.retention.retained_by)) invalid('retention.retained_by must name who keeps the artifact retrievable: contributor, coordinator or third-party.', 'retention');
    retention.retained_by = body.retention.retained_by;
    if (body.retention.retained_until !== undefined && body.retention.retained_until !== null) {
      const until = body.retention.retained_until;
      const parts = typeof until === 'string' ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(until) : null;
      const calendar = parts ? new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3])) : null;
      const real = calendar && calendar.getFullYear() === Number(parts[1]) && calendar.getMonth() === Number(parts[2]) - 1 && calendar.getDate() === Number(parts[3]);
      if (!real) {
        invalid('retention.retained_until must be a valid ISO calendar date (YYYY-MM-DD) or null.', 'retention');
      }
      retention.retained_until = body.retention.retained_until;
    }
    if (body.retention.access !== undefined && body.retention.access !== null && body.retention.access !== 'public') {
      invalid('retention.access must be public in this pilot; non-public access plans are a later stage.', 'retention');
    }
    retention.access = 'public';
    if (body.retention.on_unavailable !== undefined && body.retention.on_unavailable !== null && body.retention.on_unavailable !== '') {
      retention.on_unavailable = textField(body.retention.on_unavailable, 'retention.on_unavailable', 10, 300);
    }
  }
  const summary = textField(body.summary, 'summary', 20, 2000);
  const artifactUrl = safeUrl(body.artifact_url);
  if (!MEDIA_TYPES.includes(body.artifact_media_type)) invalid('artifact_media_type must be one of the declared media types.', 'artifact_media_type');
  if (!Number.isSafeInteger(body.artifact_size_bytes) || body.artifact_size_bytes < 1 || body.artifact_size_bytes > 52428800) {
    invalid('artifact_size_bytes must be between 1 and 52428800 bytes.', 'artifact_size_bytes');
  }
  if (typeof body.integrity_digest !== 'string' || !/^[a-f0-9]{64}$/.test(body.integrity_digest)) {
    invalid('integrity_digest must be a lowercase hex sha256 digest of the delivered bytes.', 'integrity_digest');
  }
  if (body.content_identifier !== undefined && body.content_identifier !== null && body.content_identifier !== '') {
    if (typeof body.content_identifier !== 'string' || !/^[a-zA-Z0-9]{9,128}$/.test(body.content_identifier)) {
      invalid('content_identifier must be a compact content identifier string, or omitted.', 'content_identifier');
    }
  }
  const evidenceUrl = safeUrl(body.evidence_url);
  version(body.expected_version);
  const count = await env.DB.prepare(`SELECT COUNT(*) AS revisions FROM deliveries WHERE milestone_id = ?`).bind(milestoneId).first();
  if (count.revisions >= MAX_REVISIONS) {
    throw new ApiError(409, 'revision_limit', `A milestone keeps at most ${MAX_REVISIONS} delivery revisions.`);
  }
  const id = crypto.randomUUID();
  const results = await env.DB.batch([
    env.DB.prepare(`INSERT INTO deliveries (id, project_id, milestone_id, author_identity_id, revision, scope_version,
        summary, artifact_url, artifact_media_type, artifact_size_bytes, integrity_algorithm, integrity_digest,
        content_identifier, evidence_url, retention_retained_by, retention_retained_until, retention_access, retention_on_unavailable, created_at)
      SELECT ?, ?, ?, ?, (SELECT COALESCE(MAX(revision), 0) + 1 FROM deliveries WHERE milestone_id = ?), ?, ?, ?, ?, ?, 'sha256', ?, ?, ?, ?, ?, ?, ?, ?
      WHERE EXISTS (SELECT 1 FROM projects WHERE id = ? AND version = ? AND status = 'open')
        AND EXISTS (SELECT 1 FROM milestones WHERE id = ? AND project_id = ? AND status = 'open')
        AND EXISTS (SELECT 1 FROM commitments WHERE milestone_id = ? AND contributor_identity_id = ? AND status = 'confirmed')`)
      .bind(id, projectId, milestoneId, actor.id, milestoneId, context.scope_version, summary,
        artifactUrl, body.artifact_media_type, body.artifact_size_bytes, body.integrity_digest,
        body.content_identifier || null, evidenceUrl, retention.retained_by, retention.retained_until,
        retention.access, retention.on_unavailable, now,
        projectId, body.expected_version, milestoneId, projectId, milestoneId, actor.id),
    env.DB.prepare(`UPDATE projects SET version = version + 1, updated_at = ? WHERE id = ? AND status = 'open'`).bind(now, projectId),
    env.DB.prepare(`INSERT INTO project_events (id, project_id, version, action, actor_kind, actor_identity_id, created_at)
      SELECT ${sqlUuid}, id, version + 1, 'delivery_added', 'identity', ?, ? FROM projects WHERE id = ?`).bind(actor.id, now, projectId),
  ]);
  if (results[0].meta.changes !== 1) {
    throw new ApiError(409, 'version_conflict', 'The project changed since you read it. Reload and retry.');
  }
  const row = await env.DB.prepare(`${deliverySelect} WHERE d.id = ?`).bind(id).first();
  return response(deliveryView(row), 201);
}

export async function listDeliveries(request, env, projectId, milestoneId, now) {
  if (new URL(request.url).search) invalid('This endpoint does not accept query parameters.');
  await projectAndMilestone(env.DB, projectId, milestoneId);
  const rows = (await env.DB.prepare(`${deliverySelect} WHERE d.milestone_id = ? ORDER BY d.revision DESC`)
    .bind(milestoneId).all()).results;
  return response({ items: rows.map(deliveryView), next_cursor: null });
}

export async function deliveryManifest(request, env, projectId, milestoneId, revision, now) {
  if (new URL(request.url).search) invalid('This endpoint does not accept query parameters.');
  if (!/^[1-9][0-9]{0,1}$/.test(String(revision)) || Number(revision) < 1 || Number(revision) > MAX_REVISIONS) {
    throw new ApiError(404, 'not_found', 'No such delivery revision.');
  }
  const context = await projectAndMilestone(env.DB, projectId, milestoneId);
  const row = await env.DB.prepare(`${deliverySelect} WHERE d.milestone_id = ? AND d.revision = ?`)
    .bind(milestoneId, Number(revision)).first();
  if (!row) throw new ApiError(404, 'not_found', 'No such delivery revision.');
  const project = await env.DB.prepare('SELECT id, title FROM projects WHERE id = ?').bind(projectId).first();
  const milestone = await env.DB.prepare('SELECT id, title, scope_version FROM milestones WHERE id = ?').bind(milestoneId).first();
  const reviewRow = await env.DB.prepare(`${reviewSelect} WHERE r.milestone_id = ? AND r.delivery_revision = ? ORDER BY r.created_at DESC, r.id DESC LIMIT 1`)
    .bind(milestoneId, Number(revision)).first();
  const newestRow = await env.DB.prepare('SELECT MAX(revision) AS revision FROM deliveries WHERE milestone_id = ?').bind(milestoneId).first();
  const view = deliveryView(row);
  return response({
    schema_version: 1, kind: 'oss-delivery-manifest',
    project: { id: project.id, title: project.title, scope_version: 1 },
    milestone: { id: milestone.id, title: milestone.title, scope_version: milestone.scope_version },
    delivery_revision: row.revision, delivered_at: iso(row.created_at),
    author: view.author,
    artifact: view.artifact,
    evidence_url: row.evidence_url ?? null,
    retention: view.retention,
    superseded_by_revision: newestRow?.revision !== null && newestRow?.revision > row.revision ? newestRow.revision : null,
    summary: row.summary,
    verification: {
      algorithm: 'sha256',
      instruction: 'Retrieve the artifact bytes over a channel you trust, then compare sha256 of those bytes with the digest above.',
      content_identifier_note: 'A content identifier names content plus its encoding; it is not the ordinary checksum of the original file. The raw-file digest above is the byte-level check.',
    },
    stale_revision_note: `Revisions are immutable; a higher revision supersedes this one without altering it. The current project version is ${context.project_version}.`,
    review: reviewRow ? reviewView(reviewRow) : null,
    notice: NOTICE,
  });
}

export function receiptsDiscovery() {
  return {
    project_deliveries: `/api/v1/projects/{id}/milestones/{milestone_id}/deliveries`,
    project_delivery_manifest: `/api/v1/projects/{id}/milestones/{milestone_id}/deliveries/{revision}`,
  };
}

const reviewSelect = `SELECT r.*, ${identityColumns('reviewer')} FROM milestone_reviews r
  JOIN identities reviewer ON reviewer.id = r.reviewer_identity_id`;

function reviewView(row) {
  return {
    id: row.id, project_id: row.project_id, milestone_id: row.milestone_id,
    delivery_revision: row.delivery_revision, decision: row.decision, note: row.note ?? null,
    reviewer: row.reviewer_github_id ? {
      identity_id: row.reviewer_identity_id, github_id: row.reviewer_github_id, github_login: row.reviewer_github_login,
      github_url: `https://github.com/${row.reviewer_github_login}`, verification: 'github-account-control',
      verified_at: iso(row.reviewer_verified_at) } : null,
    created_at: iso(row.created_at),
  };
}

async function latestRevision(db, milestoneId) {
  const row = await db.prepare('SELECT MAX(revision) AS revision FROM deliveries WHERE milestone_id = ?').bind(milestoneId).first();
  return row?.revision ?? 0;
}

// Stage 04 first slice: the v1 reviewer is the project coordinator. The
// structural guarantees carry the role rules: the coordinator can never be
// the delivering contributor (self-commitment is refused upstream), so a
// reviewer never reviews their own delivery, and acceptance binds exactly
// one immutable revision — a stale acceptance is refused, not superseded.
export async function submitReview(request, env, projectId, milestoneId, now) {
  const actor = await authenticateIdentity(request, env, now);
  const context = await projectAndMilestone(env.DB, projectId, milestoneId);
  if (context.project_status === 'cancelled') throw new ApiError(404, 'not_found', 'Milestone not found in this project.');
  const project = await env.DB.prepare('SELECT coordinator_identity_id FROM projects WHERE id = ?').bind(projectId).first();
  if (!project || project.coordinator_identity_id !== actor.id) {
    throw new ApiError(403, 'forbidden', 'Only the project coordinator reviews deliveries in this pilot.');
  }
  const body = await readJson(request, ['delivery_revision', 'decision', 'note', 'expected_version']);
  if (!Number.isSafeInteger(body.delivery_revision) || body.delivery_revision < 1 || body.delivery_revision > MAX_REVISIONS) {
    invalid('delivery_revision must be between 1 and 10.', 'delivery_revision');
  }
  if (!['accept', 'revision_requested'].includes(body.decision)) invalid('decision must be accept or revision_requested.', 'decision');
  const note = body.note === undefined || body.note === null || body.note === '' ? null : textField(body.note, 'note', 10, 2000);
  if (body.decision === 'revision_requested' && !note) invalid('A revision request must name what evidence or change it needs (10-2000 characters).', 'note');
  version(body.expected_version);
  const delivery = await env.DB.prepare('SELECT revision FROM deliveries WHERE milestone_id = ? AND revision = ?')
    .bind(milestoneId, body.delivery_revision).first();
  if (!delivery) throw new ApiError(404, 'not_found', 'No such delivery revision.');
  const newest = await latestRevision(env.DB, milestoneId);
  if (body.decision === 'accept' && body.delivery_revision !== newest) {
    throw new ApiError(409, 'stale_revision', 'Only the newest delivery revision can be accepted; matched bytes of a superseded revision are not a current delivery.');
  }
  const id = crypto.randomUUID();
  const statements = [
    env.DB.prepare(`INSERT INTO milestone_reviews (id, project_id, milestone_id, delivery_revision, decision, note, reviewer_identity_id, created_at)
      SELECT ?, ?, ?, ?, ?, ?, ?, ?
      WHERE EXISTS (SELECT 1 FROM milestones WHERE id = ? AND project_id = ? AND status = 'open' AND version = ?)
        AND NOT EXISTS (SELECT 1 FROM milestone_reviews WHERE milestone_id = ? AND delivery_revision = ? AND decision = ?)`)
      .bind(id, projectId, milestoneId, body.delivery_revision, body.decision, note, actor.id, now,
        milestoneId, projectId, body.expected_version, milestoneId, body.delivery_revision, body.decision),
  ];
  if (body.decision === 'accept') {
    statements.push(
      env.DB.prepare(`UPDATE milestones SET status = 'done', version = version + 1, updated_at = ?
        WHERE id = ? AND project_id = ? AND status = 'open'`).bind(now, milestoneId, projectId),
      env.DB.prepare(`UPDATE commitments SET status = 'completed', updated_at = ?
        WHERE milestone_id = ? AND status = 'confirmed'`).bind(now, milestoneId),
      env.DB.prepare(`UPDATE commitments SET status = 'declined', updated_at = ?
        WHERE milestone_id = ? AND status = 'offered'`).bind(now, milestoneId),
      env.DB.prepare(`UPDATE projects SET version = version + 1, updated_at = ? WHERE id = ? AND status = 'open'`).bind(now, projectId),
      env.DB.prepare(`INSERT INTO project_events (id, project_id, version, action, actor_kind, actor_identity_id, created_at)
        SELECT ${sqlUuid}, id, version + 1, 'delivery_accepted', 'identity', ?, ? FROM projects WHERE id = ?`).bind(actor.id, now, projectId));
  } else {
    statements.push(
      env.DB.prepare(`UPDATE projects SET version = version + 1, updated_at = ? WHERE id = ? AND status = 'open'`).bind(now, projectId),
      env.DB.prepare(`INSERT INTO project_events (id, project_id, version, action, actor_kind, actor_identity_id, created_at)
        SELECT ${sqlUuid}, id, version + 1, 'revision_requested', 'identity', ?, ? FROM projects WHERE id = ?`).bind(actor.id, now, projectId));
  }
  const results = await env.DB.batch(statements);
  if (results[0].meta.changes !== 1) {
    const duplicate = await env.DB.prepare(`SELECT id FROM milestone_reviews WHERE milestone_id = ? AND delivery_revision = ? AND decision = ?`)
      .bind(milestoneId, body.delivery_revision, body.decision).first();
    if (duplicate) throw new ApiError(409, 'duplicate_decision', 'This exact decision is already recorded; the trail is immutable.');
    if (context.milestone_status !== 'open') throw new ApiError(409, 'milestone_closed', 'Only an open milestone accepts review decisions.');
    throw new ApiError(409, 'version_conflict', 'The milestone changed since you read it. Reload and retry.');
  }
  if (body.decision === 'accept' && results[1].meta.changes !== 1) {
    throw new ApiError(409, 'milestone_closed', 'The milestone changed concurrently. Reload and retry.');
  }
  const row = await env.DB.prepare(`${reviewSelect} WHERE r.id = ?`).bind(id).first();
  return response(reviewView(row), 201);
}

export async function listReviews(request, env, projectId, milestoneId, now) {
  if (new URL(request.url).search) invalid('This endpoint does not accept query parameters.');
  await projectAndMilestone(env.DB, projectId, milestoneId);
  const rows = (await env.DB.prepare(`${reviewSelect} WHERE r.milestone_id = ? ORDER BY r.created_at DESC, r.id DESC`)
    .bind(milestoneId).all()).results;
  return response({ items: rows.map(reviewView), next_cursor: null });
}

export function reviewsDiscovery() {
  return { project_milestone_reviews: `/api/v1/projects/{id}/milestones/{milestone_id}/reviews` };
}
