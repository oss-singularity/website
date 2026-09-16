-- Additive acceptance pilot (stage 04 first slice). No records are inserted.
-- Reviews are immutable decisions binding one exact delivery revision; the
-- v1 reviewer is the project coordinator (a QA role is a later stage). An
-- accepted revision closes its milestone; a revision request records what is
-- missing without changing any status.
CREATE TABLE IF NOT EXISTS milestone_reviews (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  milestone_id TEXT NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
  delivery_revision INTEGER NOT NULL CHECK (delivery_revision BETWEEN 1 AND 10),
  decision TEXT NOT NULL CHECK (decision IN ('accept','revision_requested')),
  note TEXT CHECK (note IS NULL OR length(note) BETWEEN 10 AND 2000),
  reviewer_identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  UNIQUE (milestone_id, delivery_revision, decision)
);
CREATE INDEX IF NOT EXISTS milestone_reviews_milestone ON milestone_reviews(milestone_id, created_at DESC, id DESC);
