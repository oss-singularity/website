-- Additive voluntary coordination pilot. No records or seed activity are inserted.
CREATE TABLE IF NOT EXISTS work_items (
  id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
  requester_identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
  offered_identity_id TEXT REFERENCES identities(id) ON DELETE SET NULL,
  contributor_identity_id TEXT REFERENCES identities(id) ON DELETE CASCADE,
  title TEXT NOT NULL CHECK (length(title) BETWEEN 3 AND 120),
  scope TEXT NOT NULL CHECK (length(scope) BETWEEN 20 AND 2000),
  deliverable TEXT NOT NULL CHECK (length(deliverable) BETWEEN 20 AND 1000),
  acceptance TEXT NOT NULL CHECK (json_valid(acceptance) AND json_type(acceptance) = 'array'),
  terms TEXT NOT NULL CHECK (terms = 'volunteer'),
  scope_version INTEGER NOT NULL DEFAULT 1 CHECK (scope_version = 1),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  result_revision_count INTEGER NOT NULL DEFAULT 0 CHECK (result_revision_count BETWEEN 0 AND 10),
  last_delivered_revision INTEGER NOT NULL DEFAULT 0 CHECK (last_delivered_revision BETWEEN 0 AND 10),
  operation_count INTEGER NOT NULL DEFAULT 0 CHECK (operation_count BETWEEN 0 AND 128),
  moderation TEXT NOT NULL CHECK (moderation IN ('pending','published','rejected')),
  state TEXT NOT NULL CHECK (state IN ('open','offered','active','delivered','revision_requested','acknowledged','cancelled')),
  current_result_id TEXT,
  acknowledged_result_id TEXT,
  acknowledged_at INTEGER,
  offer_expires_at INTEGER,
  ended_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  published_at INTEGER,
  expires_at INTEGER NOT NULL CHECK (expires_at > created_at),
  client_request_id TEXT NOT NULL,
  request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
  creation_operation_id TEXT NOT NULL,
  UNIQUE(requester_identity_id, client_request_id)
);
CREATE INDEX IF NOT EXISTS work_items_public ON work_items(moderation, state, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS work_items_mission ON work_items(mission_id, moderation, state, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS work_items_requester ON work_items(requester_identity_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS work_items_candidate ON work_items(offered_identity_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS work_items_contributor ON work_items(contributor_identity_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS work_items_expiry ON work_items(expires_at);
CREATE INDEX IF NOT EXISTS work_items_queue ON work_items(moderation, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS work_item_results (
  id TEXT PRIMARY KEY,
  work_item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
  proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(id) ON DELETE CASCADE,
  author_identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
  scope_version INTEGER NOT NULL CHECK (scope_version = 1),
  revision INTEGER NOT NULL CHECK (revision BETWEEN 1 AND 10),
  created_at INTEGER NOT NULL,
  client_request_id TEXT NOT NULL,
  request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
  UNIQUE(work_item_id, revision),
  UNIQUE(work_item_id, author_identity_id, client_request_id)
);
CREATE INDEX IF NOT EXISTS work_item_results_owner ON work_item_results(author_identity_id, work_item_id);

CREATE TABLE IF NOT EXISTS work_item_events (
  id TEXT PRIMARY KEY,
  work_item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
  version INTEGER NOT NULL CHECK (version > 0),
  action TEXT NOT NULL,
  actor_kind TEXT NOT NULL CHECK (actor_kind IN ('identity','moderator','system')),
  actor_identity_id TEXT,
  actor_key TEXT NOT NULL,
  result_id TEXT,
  client_request_id TEXT,
  request_digest TEXT,
  created_at INTEGER NOT NULL,
  UNIQUE(work_item_id, actor_key, client_request_id)
);
CREATE INDEX IF NOT EXISTS work_item_events_actor ON work_item_events(actor_identity_id, work_item_id);
CREATE INDEX IF NOT EXISTS work_item_events_item ON work_item_events(work_item_id, version);

-- Existing proposal moderation remains the publication authority. Availability
-- changes invalidate optimistic versions without creating an unbounded journal.
CREATE TRIGGER IF NOT EXISTS work_result_status_changed AFTER UPDATE OF status ON proposals
WHEN OLD.status != NEW.status
BEGIN
  UPDATE work_items SET version = version + 1, updated_at = NEW.updated_at
  WHERE id IN (SELECT work_item_id FROM work_item_results WHERE proposal_id = NEW.id);
END;
CREATE TRIGGER IF NOT EXISTS work_result_removed BEFORE DELETE ON proposals
BEGIN
  UPDATE work_items SET version = version + 1,
    updated_at = MAX(updated_at, (CAST(strftime('%s', 'now') AS INTEGER) * 1000))
  WHERE id IN (SELECT work_item_id FROM work_item_results WHERE proposal_id = OLD.id);
END;

-- Parent withdrawal cancels dependent nonterminal work atomically. Republishing
-- the mission never resurrects an assignment or a cancelled item.
CREATE TRIGGER IF NOT EXISTS work_mission_withdrawn AFTER UPDATE OF status ON proposals
WHEN NEW.kind = 'mission' AND NEW.status != 'published' AND OLD.status != NEW.status
BEGIN
  INSERT INTO work_item_events (id, work_item_id, version, action, actor_kind, actor_key, created_at)
  SELECT lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2)
    || '-8' || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6))),
    id, version + 1, 'parent_unavailable', 'system', 'system', NEW.updated_at
  FROM work_items WHERE mission_id = NEW.id AND state NOT IN ('cancelled','acknowledged');
  UPDATE work_items SET state = 'cancelled', offered_identity_id = NULL, offer_expires_at = NULL,
    version = version + 1, updated_at = NEW.updated_at, ended_at = NEW.updated_at
  WHERE mission_id = NEW.id AND state NOT IN ('cancelled','acknowledged');
END;
