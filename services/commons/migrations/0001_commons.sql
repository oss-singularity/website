CREATE TABLE IF NOT EXISTS identities (
  id TEXT PRIMARY KEY,
  github_id INTEGER NOT NULL UNIQUE CHECK (github_id > 0),
  github_login TEXT NOT NULL CHECK (length(github_login) BETWEEN 1 AND 39),
  github_created_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  verified_at INTEGER NOT NULL,
  token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64)
);

CREATE TABLE IF NOT EXISTS identity_challenges (
  id TEXT PRIMARY KEY,
  github_login TEXT NOT NULL,
  nonce_hash TEXT NOT NULL CHECK (length(nonce_hash) = 64),
  token_hash TEXT NOT NULL CHECK (length(token_hash) = 64),
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER,
  verification_attempts INTEGER NOT NULL DEFAULT 0 CHECK (verification_attempts BETWEEN 0 AND 3)
);
CREATE INDEX IF NOT EXISTS challenges_expiry ON identity_challenges(consumed_at, expires_at);

CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('mission', 'field-note', 'project', 'review')),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 3 AND 120),
  summary TEXT NOT NULL CHECK (length(summary) BETWEEN 20 AND 2000),
  url TEXT,
  mission_id TEXT,
  target_id TEXT,
  score INTEGER,
  identity_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'published', 'rejected')),
  provenance TEXT NOT NULL CHECK (provenance IN ('seed', 'community')),
  receipt_hash TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  published_at INTEGER,
  CHECK ((status = 'published' AND published_at IS NOT NULL) OR (status != 'published' AND published_at IS NULL)),
  CHECK ((kind = 'review' AND target_id IS NOT NULL AND length(target_id) BETWEEN 1 AND 80
      AND score IS NOT NULL AND typeof(score) = 'integer' AND score BETWEEN 1 AND 5
      AND url IS NOT NULL AND length(url) > 0 AND mission_id IS NULL AND identity_id IS NOT NULL)
    OR (kind != 'review' AND target_id IS NULL AND score IS NULL)),
  CHECK ((provenance = 'seed' AND receipt_hash IS NULL) OR (provenance = 'community' AND receipt_hash IS NOT NULL AND length(receipt_hash) = 64))
);

CREATE INDEX IF NOT EXISTS proposals_public ON proposals(status, kind, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS proposals_mission ON proposals(mission_id, status, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS proposals_target ON proposals(target_id, status, published_at DESC, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS proposals_one_active_review ON proposals(identity_id, target_id)
  WHERE kind = 'review' AND status IN ('pending', 'published');
CREATE INDEX IF NOT EXISTS proposals_created ON proposals(status, created_at);
CREATE INDEX IF NOT EXISTS proposals_updated ON proposals(status, updated_at);

CREATE TABLE IF NOT EXISTS rate_limits (
  bucket TEXT PRIMARY KEY CHECK (length(bucket) = 64),
  count INTEGER NOT NULL CHECK (count >= 0),
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_limits_expiry ON rate_limits(expires_at);

-- Editorial templates and founding mission; timestamps record migration execution.
-- These are labelled seeds, never represented as community activity.
INSERT OR IGNORE INTO proposals (id, kind, title, summary, url, status, provenance, created_at, updated_at, published_at) VALUES
('ship-feature', 'mission', 'Ship a useful feature', 'Turn a concrete user need into a small, reviewable change with evidence.', 'https://oss-singularity.io/lab/?mission=ship-feature', 'published', 'seed', (CAST(strftime('%s', 'now') AS INTEGER) * 1000), (CAST(strftime('%s', 'now') AS INTEGER) * 1000), (CAST(strftime('%s', 'now') AS INTEGER) * 1000)),
('research-map', 'mission', 'Map an unfamiliar topic', 'Build a source-backed research map with useful distinctions and honest unknowns.', 'https://oss-singularity.io/lab/?mission=research-map', 'published', 'seed', (CAST(strftime('%s', 'now') AS INTEGER) * 1000), (CAST(strftime('%s', 'now') AS INTEGER) * 1000), (CAST(strftime('%s', 'now') AS INTEGER) * 1000)),
('audit-project', 'mission', 'Audit a project', 'Find actionable reliability and contributor-experience problems before changing anything.', 'https://oss-singularity.io/lab/?mission=audit-project', 'published', 'seed', (CAST(strftime('%s', 'now') AS INTEGER) * 1000), (CAST(strftime('%s', 'now') AS INTEGER) * 1000), (CAST(strftime('%s', 'now') AS INTEGER) * 1000));

INSERT OR IGNORE INTO proposals (id, kind, title, summary, url, status, provenance, created_at, updated_at, published_at) VALUES
('build-the-commons', 'mission', 'Build the open commons', 'Help build an open home where people and software agents discover useful work, contribute evidence and create things others can inspect and use.', 'https://oss-singularity.io/mission/', 'published', 'seed', (CAST(strftime('%s', 'now') AS INTEGER) * 1000), (CAST(strftime('%s', 'now') AS INTEGER) * 1000), (CAST(strftime('%s', 'now') AS INTEGER) * 1000));
