-- Additive community participation; no existing proposal or identity is changed.
CREATE TABLE IF NOT EXISTS participations (
  id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
  identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
  intent TEXT NOT NULL CHECK (intent IN ('offer', 'need')),
  participant_type TEXT NOT NULL CHECK (participant_type IN ('human', 'agent', 'team', 'other')),
  collaboration TEXT NOT NULL CHECK (collaboration IN ('volunteer', 'discuss-compensation')),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 3 AND 120),
  summary TEXT NOT NULL CHECK (length(summary) BETWEEN 20 AND 2000),
  url TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'published', 'rejected')),
  state TEXT NOT NULL CHECK (state IN ('active', 'closed', 'withdrawn', 'expired')),
  receipt_hash TEXT NOT NULL CHECK (length(receipt_hash) = 64),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  published_at INTEGER,
  expires_at INTEGER NOT NULL CHECK (expires_at > created_at),
  CHECK ((status = 'published' AND published_at IS NOT NULL)
    OR (status = 'pending' AND published_at IS NULL) OR status = 'rejected'),
  CHECK (state != 'closed' OR published_at IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS participation_one_active
  ON participations(identity_id, mission_id, intent)
  WHERE status IN ('pending', 'published') AND state = 'active';
CREATE INDEX IF NOT EXISTS participation_public
  ON participations(status, state, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS participation_mission
  ON participations(mission_id, status, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS participation_owner
  ON participations(identity_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS participation_queue
  ON participations(status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS participation_expiry ON participations(expires_at);
