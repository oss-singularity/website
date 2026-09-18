-- Additive receipt completion slice (stage 03/04 follow-up, implementing the
-- two accepted design notes). No records are destroyed: the commitments
-- rebuild only widens the status CHECK to admit 'completed' for commitments
-- whose milestone was accepted, and the delivery columns add the declared
-- retention object. The service still fetches and verifies no artifact bytes.
ALTER TABLE deliveries ADD COLUMN retention_retained_by TEXT CHECK (retention_retained_by IS NULL OR retention_retained_by IN ('contributor','coordinator','third-party'));
ALTER TABLE deliveries ADD COLUMN retention_retained_until TEXT CHECK (retention_retained_until IS NULL OR length(retention_retained_until) = 10);
ALTER TABLE deliveries ADD COLUMN retention_access TEXT CHECK (retention_access IS NULL OR retention_access = 'public');
ALTER TABLE deliveries ADD COLUMN retention_on_unavailable TEXT CHECK (retention_on_unavailable IS NULL OR length(retention_on_unavailable) BETWEEN 10 AND 300);
CREATE TABLE commitments_v2 (
  id TEXT PRIMARY KEY,
  milestone_id TEXT NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  contributor_identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
  coordinator_identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
  terms TEXT NOT NULL CHECK (terms = 'volunteer'),
  scope_version INTEGER NOT NULL CHECK (scope_version > 0),
  status TEXT NOT NULL CHECK (status IN ('offered','confirmed','completed','ended','withdrawn','declined','cancelled')),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
INSERT INTO commitments_v2 (id, milestone_id, project_id, contributor_identity_id, coordinator_identity_id, terms, scope_version, status, created_at, updated_at)
  SELECT id, milestone_id, project_id, contributor_identity_id, coordinator_identity_id, terms, scope_version, status, created_at, updated_at FROM commitments;
DROP TRIGGER IF EXISTS project_mission_withdrawn;
DROP TABLE commitments;
ALTER TABLE commitments_v2 RENAME TO commitments;
CREATE INDEX IF NOT EXISTS commitments_milestone ON commitments(milestone_id, status, created_at, id);
CREATE INDEX IF NOT EXISTS commitments_contributor ON commitments(contributor_identity_id, status, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS commitments_coordinator ON commitments(coordinator_identity_id, status, updated_at DESC, id DESC);

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
    AND milestone_id IN (SELECT id FROM milestones WHERE status != 'done')
    AND project_id IN (SELECT id FROM projects WHERE mission_id = NEW.id);
  UPDATE milestones SET status = 'cancelled', version = version + 1, updated_at = NEW.updated_at
  WHERE status = 'open'
    AND project_id IN (SELECT id FROM projects WHERE mission_id = NEW.id);
END;
