-- Additive project coordination pilot (stage 02 first slice). No records are inserted.
-- Hierarchy: mission -> project -> milestone (parent_milestone_id adds one
-- subproject level later). Dependencies are explicit gates between milestones.
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
  coordinator_identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
  title TEXT NOT NULL CHECK (length(title) BETWEEN 3 AND 120),
  purpose TEXT NOT NULL CHECK (length(purpose) BETWEEN 20 AND 2000),
  status TEXT NOT NULL CHECK (status IN ('open','closed','cancelled')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS projects_mission ON projects(mission_id, status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS projects_coordinator ON projects(coordinator_identity_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS milestones (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  parent_milestone_id TEXT REFERENCES milestones(id) ON DELETE CASCADE,
  created_by_identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
  title TEXT NOT NULL CHECK (length(title) BETWEEN 3 AND 120),
  purpose TEXT NOT NULL CHECK (length(purpose) BETWEEN 20 AND 1000),
  expected_artifact TEXT NOT NULL CHECK (length(expected_artifact) BETWEEN 20 AND 500),
  acceptance TEXT NOT NULL CHECK (json_valid(acceptance) AND json_type(acceptance) = 'array'),
  status TEXT NOT NULL CHECK (status IN ('open','done','cancelled')),
  scope_version INTEGER NOT NULL DEFAULT 1 CHECK (scope_version > 0),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS milestones_project ON milestones(project_id, status, created_at, id);
CREATE INDEX IF NOT EXISTS milestones_parent ON milestones(parent_milestone_id);

CREATE TABLE IF NOT EXISTS milestone_dependencies (
  milestone_id TEXT NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
  depends_on_id TEXT NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (milestone_id, depends_on_id),
  CHECK (milestone_id != depends_on_id)
);
CREATE INDEX IF NOT EXISTS milestone_dependencies_reverse ON milestone_dependencies(depends_on_id);

CREATE TABLE IF NOT EXISTS commitments (
  id TEXT PRIMARY KEY,
  milestone_id TEXT NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  contributor_identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
  coordinator_identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
  terms TEXT NOT NULL CHECK (terms = 'volunteer'),
  scope_version INTEGER NOT NULL CHECK (scope_version > 0),
  status TEXT NOT NULL CHECK (status IN ('offered','confirmed','ended','withdrawn','declined','cancelled')),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS commitments_milestone ON commitments(milestone_id, status, created_at, id);
CREATE INDEX IF NOT EXISTS commitments_contributor ON commitments(contributor_identity_id, status, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS commitments_coordinator ON commitments(coordinator_identity_id, status, updated_at DESC, id DESC);

-- The journal describes project-level coordination actions. Commitment state
-- changes are visible through the commitments table and bounded project events.
CREATE TABLE IF NOT EXISTS project_events (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  version INTEGER NOT NULL CHECK (version > 0),
  action TEXT NOT NULL,
  actor_kind TEXT NOT NULL CHECK (actor_kind IN ('identity','system')),
  actor_identity_id TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS project_events_project ON project_events(project_id, version, created_at);

-- Mission withdrawal cancels dependent projects and nonterminal commitments
-- atomically. Republishing the mission never resurrects coordination state.
CREATE TRIGGER IF NOT EXISTS project_mission_withdrawn AFTER UPDATE OF status ON proposals
WHEN NEW.kind = 'mission' AND NEW.status != 'published' AND OLD.status != NEW.status
BEGIN
  INSERT INTO project_events (id, project_id, version, action, actor_kind, created_at)
  SELECT lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2)
    || '-8' || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6))),
    id, version + 1, 'parent_unavailable', 'system', NEW.updated_at
  FROM projects WHERE mission_id = NEW.id AND status = 'open';
  UPDATE projects SET status = 'cancelled', version = version + 1, updated_at = NEW.updated_at
  WHERE mission_id = NEW.id AND status = 'open';
  UPDATE commitments SET status = 'cancelled', updated_at = NEW.updated_at
  WHERE status IN ('offered','confirmed')
    AND project_id IN (SELECT id FROM projects WHERE mission_id = NEW.id);
  UPDATE milestones SET status = 'cancelled', version = version + 1, updated_at = NEW.updated_at
  WHERE status = 'open'
    AND project_id IN (SELECT id FROM projects WHERE mission_id = NEW.id);
END;
