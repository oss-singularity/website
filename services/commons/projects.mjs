import { ApiError, response, invalid, readJson, textField, identifier, pagination } from './security.mjs';
import { authenticateIdentity } from './identity.mjs';

const PREFIX = '/api/v1/projects';
const sqlUuid = "lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-8' || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))";
const identityColumns = (name) => ['github_id', 'github_login', 'verified_at'].map(key => `${name}.${key} AS ${name}_${key}`).join(', ');
const projectSelect = `SELECT p.*, parent.status AS parent_status, parent.kind AS parent_kind,
  ${identityColumns('coordinator')} FROM projects p
  JOIN proposals parent ON parent.id = p.mission_id
  JOIN identities coordinator ON coordinator.id = p.coordinator_identity_id`;
const iso = value => value == null ? null : new Date(value).toISOString();
const version = value => { if (!Number.isSafeInteger(value) || value < 1) invalid('expected_version must be a positive integer.', 'expected_version'); return value; };
const profile = (row, id) => id && row.coordinator_github_id ? {
  identity_id: id, github_id: row.coordinator_github_id, github_login: row.coordinator_github_login,
  github_url: `https://github.com/${row.coordinator_github_login}`, verification: 'github-account-control',
  verified_at: iso(row.coordinator_verified_at) } : null;

const MAX_MILESTONES = 20;
const MAX_DEPENDENCIES = 10;
const MAX_DEPTH = 2;
const MAX_NONTERMINAL_COMMITMENTS = 3;
const MAX_OPEN_PROJECTS = 10;

export async function coordinatorOf(db, projectId, actorId) {
  if (!actorId) return false;
  return Boolean(await db.prepare('SELECT id FROM projects WHERE id = ? AND coordinator_identity_id = ?').bind(projectId, actorId).first());
}

async function projectRow(db, id, viewerId = null) {
  const extra = viewerId ? "AND (p.status != 'cancelled' OR p.coordinator_identity_id = ?)" : "AND p.status != 'cancelled'";
  const row = await db.prepare(`${projectSelect} WHERE p.id = ? ${extra}
    AND parent.status = 'published' AND parent.kind = 'mission'`).bind(...(viewerId ? [id, viewerId] : [id])).first();
  if (!row) throw new ApiError(404, 'not_found', 'Project not found.');
  return row;
}

async function milestonesOf(db, projectId) {
  const rows = (await db.prepare(`SELECT m.*, EXISTS (
      SELECT 1 FROM identities WHERE id = m.created_by_identity_id) AS identity_exists
    FROM milestones m WHERE m.project_id = ? ORDER BY m.created_at, m.id`).bind(projectId).all()).results;
  const dependencies = (await db.prepare(`SELECT milestone_id, depends_on_id FROM milestone_dependencies
    WHERE milestone_id IN (SELECT id FROM milestones WHERE project_id = ?)`).bind(projectId).all()).results;
  const done = new Set(rows.filter(row => row.status === 'done').map(row => row.id));
  const blocked = new Set();
  for (const row of rows) {
    const unmet = dependencies.filter(edge => edge.milestone_id === row.id && !done.has(edge.depends_on_id));
    if (unmet.length > 0) blocked.add(row.id);
  }
  return { rows: rows.filter(row => row.identity_exists), dependencies, blocked, byId: new Map(rows.map(row => [row.id, row])) };
}

function acceptanceOf(row) {
  try {
    const value = JSON.parse(row.acceptance);
    if (Array.isArray(value)) return value;
  } catch { /* unreachable: CHECK enforces valid JSON arrays */ }
  return [];
}

function milestoneView(row, dependencies, blocked, viewer) {
  return {
    id: row.id, project_id: row.project_id, parent_milestone_id: row.parent_milestone_id,
    title: row.title, purpose: row.purpose, expected_artifact: row.expected_artifact,
    acceptance: acceptanceOf(row), status: row.status, scope_version: row.scope_version,
    version: row.version, created_at: iso(row.created_at), updated_at: iso(row.updated_at),
    depends_on: dependencies.filter(edge => edge.milestone_id === row.id).map(edge => edge.depends_on_id),
    blocked: blocked.has(row.id),
    ...(viewer ? { created_by_identity_id: row.created_by_identity_id } : {}),
  };
}

function commitmentView(row, viewer, includeCandidate) {
  const base = {
    id: row.id, milestone_id: row.milestone_id, project_id: row.project_id,
    status: row.status, terms: row.terms, scope_version: row.scope_version,
    created_at: iso(row.created_at), updated_at: iso(row.updated_at),
  };
  if (viewer || includeCandidate) {
    base.contributor = row.contributor_profile;
    base.coordinator = row.coordinator_profile;
  }
  return base;
}

async function commitmentsFor(db, projectId, viewer, now) {
  const rows = (await db.prepare(`SELECT c.*, ${identityColumns('contributor')}, ${identityColumns('coord')},
      EXISTS (SELECT 1 FROM identities WHERE id = c.contributor_identity_id) AS identity_exists
    FROM commitments c
    JOIN identities contributor ON contributor.id = c.contributor_identity_id
    JOIN identities coord ON coord.id = c.coordinator_identity_id
    WHERE c.project_id = ? ORDER BY c.created_at, c.id`).bind(projectId).all()).results;
  return rows.filter(row => row.identity_exists).map(row => {
    const participant = viewer && [row.contributor_identity_id, row.coordinator_identity_id].includes(viewer.id);
    // An offered commitment is visible only to its two bound participants;
    // the public view sees a commitment once it is confirmed.
    if (row.status === 'offered' && !participant) return null;
    const profiled = { ...row,
      contributor_profile: { identity_id: row.contributor_identity_id, github_id: row.contributor_github_id,
        github_login: row.contributor_github_login, github_url: `https://github.com/${row.contributor_github_login}`,
        verification: 'github-account-control', verified_at: iso(row.contributor_verified_at) },
      coordinator_profile: { identity_id: row.coordinator_identity_id, github_id: row.coord_github_id,
        github_login: row.coord_github_login, github_url: `https://github.com/${row.coord_github_login}`,
        verification: 'github-account-control', verified_at: iso(row.coord_verified_at) } };
    return commitmentView(profiled, viewer, true);
  }).filter(Boolean);
}

export async function createProject(request, env, now) {
  const actor = await authenticateIdentity(request, env, now);
  const body = await readJson(request, ['mission_id', 'title', 'purpose']);
  const missionId = identifier(body.mission_id, 'mission_id');
  const title = textField(body.title, 'title', 3, 120);
  const purpose = textField(body.purpose, 'purpose', 20, 2000);
  const open = await env.DB.prepare(`SELECT COUNT(*) AS count FROM projects WHERE coordinator_identity_id = ? AND status = 'open'`).bind(actor.id).first();
  if (open.count >= MAX_OPEN_PROJECTS) {
    throw new ApiError(409, 'project_limit', `A coordinator keeps at most ${MAX_OPEN_PROJECTS} open projects.`);
  }
  const id = crypto.randomUUID();
  const results = await env.DB.batch([
    env.DB.prepare(`INSERT INTO projects (id, mission_id, coordinator_identity_id, title, purpose, status, version, created_at, updated_at)
      SELECT ?, ?, ?, ?, ?, 'open', 1, ?, ?
      WHERE EXISTS (SELECT 1 FROM proposals WHERE id = ? AND kind = 'mission' AND status = 'published')`).bind(
      id, missionId, actor.id, title, purpose, now, now, missionId),
    env.DB.prepare(`INSERT INTO project_events (id, project_id, version, action, actor_kind, actor_identity_id, created_at)
      SELECT ${sqlUuid}, id, 2, 'created', 'identity', ?, ? FROM projects WHERE id = ?`).bind(actor.id, now, id),
  ]);
  if (results[0].meta.changes !== 1) {
    invalid('mission_id must identify a published mission.', 'mission_id');
  }
  const row = await projectRow(env.DB, id);
  return response({ id: row.id, mission_id: row.mission_id, title: row.title, purpose: row.purpose,
    status: row.status, scope_version: 1, version: row.version, coordinator: profile(row, row.coordinator_identity_id),
    created_at: iso(row.created_at), updated_at: iso(row.updated_at) }, 201);
}

export async function listProjects(request, env, now) {
  const params = new URL(request.url).searchParams;
  const { limit, cursor } = pagination(params, ['mission_id', 'limit', 'cursor']);
  const where = ["p.status != 'cancelled'", "parent.status = 'published'", "parent.kind = 'mission'"];
  const values = [];
  if (params.has('mission_id')) {
    where.push('p.mission_id = ?'); values.push(identifier(params.get('mission_id'), 'mission_id'));
  }
  if (cursor) {
    where.push('(p.created_at < ? OR (p.created_at = ? AND p.id < ?))');
    values.push(cursor[0], cursor[0], cursor[1]);
  }
  const result = await env.DB.prepare(`${projectSelect} WHERE ${where.join(' AND ')}
    ORDER BY p.created_at DESC, p.id DESC LIMIT ?`).bind(...values, limit + 1).all();
  const rows = (result.results ?? result).slice(0, limit);
  const last = rows.at(-1);
  return response({
    items: rows.map(row => ({ id: row.id, mission_id: row.mission_id, title: row.title, purpose: row.purpose,
      status: row.status, version: row.version, coordinator: profile(row, row.coordinator_identity_id),
      created_at: iso(row.created_at), updated_at: iso(row.updated_at) })),
    next_cursor: (result.results ?? result).length > limit ? `${last.created_at}:${last.id}` : null,
  });
}

export async function readProject(request, env, id, now, mode = 'public') {
  const viewer = mode === 'mine' || request.headers.has('authorization') ? await authenticateIdentity(request, env, now) : null;
  const row = await projectRow(env.DB, id, viewer?.id ?? null);
  const isCoordinator = viewer && viewer.id === row.coordinator_identity_id;
  const milestones = await milestonesOf(env.DB, id);
  const commitments = await commitmentsFor(env.DB, id, viewer, now);
  const project = {
    id: row.id, mission_id: row.mission_id, title: row.title, purpose: row.purpose,
    status: row.status, version: row.version, coordinator: profile(row, row.coordinator_identity_id),
    created_at: iso(row.created_at), updated_at: iso(row.updated_at),
    milestones: milestones.rows.map(milestone => milestoneView(milestone, milestones.dependencies, milestones.blocked, isCoordinator || (viewer && milestones.rows.some(m => m.created_by_identity_id === viewer.id)))),
    commitments: commitments.filter(view => view.status !== 'offered' || (viewer && [view.contributor?.identity_id, view.coordinator?.identity_id].includes(viewer.id))),
  };
  if (isCoordinator) project.viewer = { coordinator: true };
  return response(project);
}

export async function addMilestone(request, env, projectId, now) {
  const actor = await authenticateIdentity(request, env, now);
  const row = await projectRow(env.DB, projectId);
  if (row.coordinator_identity_id !== actor.id) {
    throw new ApiError(403, 'forbidden', 'Only the project coordinator adds milestones.');
  }
  if (row.status !== 'open') throw new ApiError(409, 'project_closed', 'Only an open project accepts milestones.');
  const body = await readJson(request, ['title', 'purpose', 'expected_artifact', 'acceptance', 'parent_milestone_id', 'depends_on', 'expected_version']);
  const title = textField(body.title, 'title', 3, 120);
  const purpose = textField(body.purpose, 'purpose', 20, 1000);
  const expectedArtifact = textField(body.expected_artifact, 'expected_artifact', 20, 500);
  if (!Array.isArray(body.acceptance) || body.acceptance.length < 1 || body.acceptance.length > 8
      || !body.acceptance.every(item => typeof item === 'string' && item.length >= 10 && item.length <= 300)) {
    invalid('acceptance must be an array of one to eight criteria (10-300 characters each).', 'acceptance');
  }
  version(body.expected_version);
  const count = await env.DB.prepare("SELECT COUNT(*) AS count FROM milestones WHERE project_id = ? AND status != 'cancelled'").bind(projectId).first();
  if (count.count >= MAX_MILESTONES) {
    throw new ApiError(409, 'milestone_limit', `A project keeps at most ${MAX_MILESTONES} milestones.`);
  }
  let parent = null;
  if (body.parent_milestone_id !== null && body.parent_milestone_id !== undefined && body.parent_milestone_id !== '') {
    parent = await env.DB.prepare("SELECT id, parent_milestone_id FROM milestones WHERE id = ? AND project_id = ? AND status != 'cancelled'")
      .bind(identifier(body.parent_milestone_id, 'parent_milestone_id'), projectId).first();
    if (!parent) invalid('parent_milestone_id must identify a milestone of this project.', 'parent_milestone_id');
    if (parent.parent_milestone_id) invalid('Milestones nest at most one subproject level.', 'parent_milestone_id');
  }
  const requested = Array.isArray(body.depends_on) ? body.depends_on : [];
  if (requested.length > MAX_DEPENDENCIES) {
    throw new ApiError(409, 'dependency_limit', `A milestone depends on at most ${MAX_DEPENDENCIES} milestones.`);
  }
  const dependencies = [];
  for (const item of requested) {
    const dependency = await env.DB.prepare(`SELECT id, parent_milestone_id, status FROM milestones
      WHERE id = ? AND project_id = ? AND status != 'cancelled'`).bind(identifier(item, 'depends_on'), projectId).first();
    if (!dependency) invalid('depends_on must identify milestones of this project.', 'depends_on');
    if (parent && (dependency.id === parent.id || dependency.parent_milestone_id === parent.id)) {
      invalid('A milestone cannot depend on its own subproject ancestors.', 'depends_on');
    }
    dependencies.push(dependency.id);
  }
  if (new Set(dependencies).size !== dependencies.length) {
    invalid('depends_on must not repeat a milestone.', 'depends_on');
  }
  const id = crypto.randomUUID();
  const statements = [
    env.DB.prepare(`INSERT INTO milestones (id, project_id, parent_milestone_id, created_by_identity_id, title, purpose,
        expected_artifact, acceptance, status, scope_version, version, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', 1, 1, ?, ?)`).bind(
      id, projectId, parent?.id ?? null, actor.id, title, purpose, expectedArtifact,
      JSON.stringify(body.acceptance), now, now),
    env.DB.prepare("UPDATE projects SET version = version + 1, updated_at = ? WHERE id = ? AND version = ? AND status = 'open'")
      .bind(now, projectId, body.expected_version),
    env.DB.prepare(`INSERT INTO project_events (id, project_id, version, action, actor_kind, actor_identity_id, created_at)
      SELECT ${sqlUuid}, id, version + 1, 'milestone_added', 'identity', ?, ? FROM projects WHERE id = ?`).bind(actor.id, now, projectId),
  ];
  for (const dependency of dependencies) {
    statements.push(env.DB.prepare('INSERT INTO milestone_dependencies (milestone_id, depends_on_id, created_at) VALUES (?, ?, ?)')
      .bind(id, dependency, now));
  }
  const results = await env.DB.batch(statements);
  if (results[1].meta.changes !== 1) {
    conflict409('version_conflict', 'The project changed since you read it. Reload and retry.');
  }
  const milestones = await milestonesOf(env.DB, projectId);
  const created = milestones.byId.get(id);
  return response(milestoneView(created, milestones.dependencies, milestones.blocked, true), 201);
}

export async function projectAction(request, env, projectId, now) {
  const actor = await authenticateIdentity(request, env, now);
  const row = await projectRow(env.DB, projectId);
  if (row.coordinator_identity_id !== actor.id) {
    throw new ApiError(403, 'forbidden', 'Only the project coordinator closes or cancels a project.');
  }
  const body = await readJson(request, ['action', 'expected_version']);
  if (!['close', 'cancel'].includes(body.action)) invalid('action must be close or cancel.', 'action');
  version(body.expected_version);
  const status = body.action === 'close' ? 'closed' : 'cancelled';
  const results = await env.DB.batch([
    env.DB.prepare(`UPDATE projects SET status = ?, version = version + 1, updated_at = ?
      WHERE id = ? AND version = ? AND status = 'open'
      RETURNING id, mission_id, title, status, version, updated_at`).bind(status, now, projectId, body.expected_version),
    env.DB.prepare(`UPDATE milestones SET status = 'cancelled', version = version + 1, updated_at = ?
      WHERE project_id = ? AND status = 'open'`).bind(now, projectId),
    env.DB.prepare(`UPDATE commitments SET status = 'cancelled', updated_at = ?
      WHERE project_id = ? AND status IN ('offered','confirmed')
        AND milestone_id IN (SELECT id FROM milestones WHERE project_id = ? AND status != 'done')`)
      .bind(now, projectId, projectId),
    env.DB.prepare(`INSERT INTO project_events (id, project_id, version, action, actor_kind, actor_identity_id, created_at)
      SELECT ${sqlUuid}, id, version + 1, ?, 'identity', ?, ? FROM projects WHERE id = ?`).bind(body.action, actor.id, now, projectId),
  ]);
  if (results[0].meta.changes !== 1) {
    conflict409('version_conflict', 'The project changed since you read it. Reload and retry.');
  }
  const closed = results[0].results[0];
  return response({ id: closed.id, mission_id: closed.mission_id, title: closed.title,
    status: closed.status, version: closed.version, updated_at: iso(closed.updated_at) });
}

function conflict409(code, message) { throw new ApiError(409, code, message); }

export async function milestoneAction(request, env, projectId, milestoneId, now) {
  const actor = await authenticateIdentity(request, env, now);
  const row = await projectRow(env.DB, projectId);
  if (row.coordinator_identity_id !== actor.id) {
    throw new ApiError(403, 'forbidden', 'Only the project coordinator completes milestones.');
  }
  const body = await readJson(request, ['action', 'expected_version']);
  if (body.action !== 'complete') invalid('action must be complete.', 'action');
  version(body.expected_version);
  const milestone = await env.DB.prepare(`SELECT id, status FROM milestones WHERE id = ? AND project_id = ?`)
    .bind(milestoneId, projectId).first();
  if (!milestone || milestone.status !== 'open') {
    throw new ApiError(404, 'not_found', 'No open milestone was found.');
  }
  const results = await env.DB.batch([
    env.DB.prepare(`UPDATE milestones SET status = 'done', version = version + 1, updated_at = ?
      WHERE id = ? AND project_id = ? AND status = 'open' AND version = ?`).bind(now, milestoneId, projectId, body.expected_version),
    env.DB.prepare(`UPDATE projects SET version = version + 1, updated_at = ? WHERE id = ? AND status = 'open'`).bind(now, projectId),
  ]);
  if (results[0].meta.changes !== 1) {
    conflict409('version_conflict', 'The milestone changed since you read it. Reload and retry.');
  }
  const milestones = await milestonesOf(env.DB, projectId);
  return response(milestoneView(milestones.byId.get(milestoneId), milestones.dependencies, milestones.blocked, true));
}

export async function offerCommitment(request, env, projectId, now) {
  const actor = await authenticateIdentity(request, env, now);
  const row = await projectRow(env.DB, projectId);
  if (row.status !== 'open') throw new ApiError(409, 'project_closed', 'Only an open project accepts commitments.');
  const body = await readJson(request, ['milestone_id', 'terms']);
  const milestoneId = identifier(body.milestone_id, 'milestone_id');
  if (body.terms !== 'volunteer') invalid('terms must be volunteer; paid coordination is a separate roadmap stage.', 'terms');
  const milestone = await env.DB.prepare(`SELECT id, status, scope_version FROM milestones WHERE id = ? AND project_id = ?`)
    .bind(milestoneId, projectId).first();
  if (!milestone || milestone.status !== 'open') {
    throw new ApiError(404, 'not_found', 'No open milestone was found.');
  }
  const milestones = await milestonesOf(env.DB, projectId);
  if (milestones.blocked.has(milestoneId)) {
    throw new ApiError(409, 'dependency_blocked', 'A dependency gate is still open; this milestone cannot start yet.');
  }
  if (row.coordinator_identity_id === actor.id) {
    throw new ApiError(409, 'self_commitment', 'The coordinator commits through a bound participant, not to themselves.');
  }
  const active = await env.DB.prepare(`SELECT COUNT(*) AS count FROM commitments
    WHERE milestone_id = ? AND contributor_identity_id = ? AND status IN ('offered','confirmed')`)
    .bind(milestoneId, actor.id).first();
  if (active.count > 0) {
    throw new ApiError(409, 'duplicate_commitment', 'This identity already has a nonterminal commitment on this milestone.');
  }
  const bound = await env.DB.prepare(`SELECT COUNT(*) AS count FROM commitments WHERE milestone_id = ? AND status IN ('offered','confirmed')`)
    .bind(milestoneId).first();
  if (bound.count >= MAX_NONTERMINAL_COMMITMENTS) {
    throw new ApiError(409, 'commitment_limit', `A milestone keeps at most ${MAX_NONTERMINAL_COMMITMENTS} nonterminal commitments.`);
  }
  const id = crypto.randomUUID();
  const results = await env.DB.batch([
    env.DB.prepare(`INSERT INTO commitments (id, milestone_id, project_id, contributor_identity_id, coordinator_identity_id,
        terms, scope_version, status, created_at, updated_at)
      SELECT ?, ?, ?, ?, ?, 'volunteer', ?, 'offered', ?, ?
      WHERE EXISTS (SELECT 1 FROM milestones WHERE id = ? AND status = 'open' AND project_id = ?)
        AND NOT EXISTS (SELECT 1 FROM commitments WHERE milestone_id = ? AND contributor_identity_id = ? AND status IN ('offered','confirmed'))`)
      .bind(id, milestoneId, projectId, actor.id, row.coordinator_identity_id, milestone.scope_version, now, now,
        milestoneId, projectId, milestoneId, actor.id),
    env.DB.prepare(`INSERT INTO project_events (id, project_id, version, action, actor_kind, actor_identity_id, created_at)
      SELECT ${sqlUuid}, id, version + 1, 'commitment_offered', 'identity', ?, ? FROM projects WHERE id = ?`).bind(actor.id, now, projectId),
  ]);
  if (results[0].meta.changes !== 1) {
    throw new ApiError(409, 'duplicate_commitment', 'This identity already has a nonterminal commitment on this milestone.');
  }
  const commitments = await commitmentsFor(env.DB, projectId, actor, now);
  return response(commitments.find(view => view.id === id), 201);
}

export async function commitmentAction(request, env, projectId, commitmentId, now) {
  const actor = await authenticateIdentity(request, env, now);
  const body = await readJson(request, ['action']);
  const actions = { confirm: 'coordinator', decline: 'coordinator', end: 'either', withdraw: 'contributor' };
  if (!(body.action in actions)) {
    invalid('action must be confirm, decline, withdraw or end.', 'action');
  }
  const row = await env.DB.prepare(`SELECT c.*, p.coordinator_identity_id, p.status AS project_status
    FROM commitments c JOIN projects p ON p.id = c.project_id WHERE c.id = ? AND c.project_id = ?`)
    .bind(commitmentId, projectId).first();
  if (!row) throw new ApiError(404, 'not_found', 'Commitment not found.');
  const isCoordinator = actor.id === row.coordinator_identity_id;
  const isContributor = actor.id === row.contributor_identity_id;
  const authority = { coordinator: isCoordinator, either: isCoordinator || isContributor, contributor: isContributor }[actions[body.action]];
  if (!authority) {
    throw new ApiError(403, 'forbidden', `Only the ${actions[body.action]} side performs this action.`);
  }
  const transitions = { confirm: ['offered', 'confirmed'], decline: ['offered', 'declined'],
    withdraw: ['offered', 'withdrawn'], end: ['confirmed', 'ended'] };
  const [from, to] = transitions[body.action];
  if (row.status !== from) {
    conflict409('state_conflict', `This action needs a commitment in state ${from}.`);
  }
  if (row.project_status !== 'open' && body.action !== 'end') {
    conflict409('project_closed', 'Only an open project accepts this action.');
  }
  const results = await env.DB.batch([
    env.DB.prepare('UPDATE commitments SET status = ?, updated_at = ? WHERE id = ? AND status = ?').bind(to, now, commitmentId, from),
    env.DB.prepare(`INSERT INTO project_events (id, project_id, version, action, actor_kind, actor_identity_id, created_at)
      SELECT ${sqlUuid}, id, version + 1, ?, 'identity', ?, ? FROM projects WHERE id = ?`)
      .bind(`commitment_${body.action}`, actor.id, now, projectId),
  ]);
  if (results[0].meta.changes !== 1) {
    conflict409('state_conflict', 'The commitment changed concurrently. Reload and retry.');
  }
  const commitments = await commitmentsFor(env.DB, projectId, actor, now);
  return response(commitments.find(view => view.id === commitmentId));
}

export async function projectExport(request, env, id, now) {
  const row = await projectRow(env.DB, id);
  const milestones = await milestonesOf(env.DB, id);
  const commitments = await commitmentsFor(env.DB, id, null, now);
  return response({
    schema_version: 1, kind: 'oss-project-export',
    exported_at: new Date(now).toISOString(),
    project: { id: row.id, mission_id: row.mission_id, title: row.title, purpose: row.purpose,
      status: row.status, scope_version: 1, version: row.version,
      coordinator: profile(row, row.coordinator_identity_id), created_at: iso(row.created_at), updated_at: iso(row.updated_at) },
    milestones: milestones.rows.map(milestone => milestoneView(milestone, milestones.dependencies, milestones.blocked, true)),
    commitments: commitments.filter(view => ['confirmed', 'ended', 'completed'].includes(view.status))
      .map(({ id: commitmentId, milestone_id, contributor, coordinator, status, terms, scope_version, created_at, updated_at }) =>
        ({ id: commitmentId, milestone_id, contributor, coordinator, status, terms, scope_version, created_at, updated_at })),
    notice: 'An export records coordination decisions and identities; it verifies no artifact and authorizes no payment.',
  });
}

export function projectDiscovery() {
  return {
    projects: `${PREFIX}`, project: `${PREFIX}/{id}`, project_export: `${PREFIX}/{id}/export`,
    project_milestones: `${PREFIX}/{id}/milestones`, milestone_actions: `${PREFIX}/{id}/milestones/{milestone_id}/actions`,
    project_commitments: `${PREFIX}/{id}/commitments`, commitment_actions: `${PREFIX}/{id}/commitments/{commitment_id}/actions`,
  };
}
