-- Additive artifact receipts pilot (stage 03 first slice). No records are inserted.
-- Deliveries are immutable, server-numbered revisions attached to one milestone;
-- the manifest references offchain artifacts and never authorizes the service to
-- fetch them. Integrity metadata distinguishes a raw sha256 file digest from an
-- optional content identifier.
CREATE TABLE IF NOT EXISTS deliveries (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  milestone_id TEXT NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
  author_identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
  revision INTEGER NOT NULL CHECK (revision BETWEEN 1 AND 10),
  scope_version INTEGER NOT NULL CHECK (scope_version > 0),
  summary TEXT NOT NULL CHECK (length(summary) BETWEEN 20 AND 2000),
  artifact_url TEXT NOT NULL CHECK (length(artifact_url) BETWEEN 12 AND 2048),
  artifact_media_type TEXT NOT NULL CHECK (artifact_media_type IN ('text/plain','text/markdown','application/json','application/pdf','application/zip','application/octet-stream')),
  artifact_size_bytes INTEGER NOT NULL CHECK (artifact_size_bytes BETWEEN 1 AND 52428800),
  integrity_algorithm TEXT NOT NULL CHECK (integrity_algorithm = 'sha256'),
  integrity_digest TEXT NOT NULL CHECK (length(integrity_digest) = 64),
  content_identifier TEXT CHECK (content_identifier IS NULL OR length(content_identifier) BETWEEN 9 AND 128),
  evidence_url TEXT CHECK (evidence_url IS NULL OR length(evidence_url) BETWEEN 12 AND 2048),
  created_at INTEGER NOT NULL,
  UNIQUE (milestone_id, revision)
);
CREATE INDEX IF NOT EXISTS deliveries_milestone ON deliveries(milestone_id, revision DESC);
CREATE INDEX IF NOT EXISTS deliveries_author ON deliveries(author_identity_id, created_at DESC, id DESC);
