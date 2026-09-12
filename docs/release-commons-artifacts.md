# Commons code artifacts

The offline Commons artifact tools create a reproducible code package containing
the six production ES modules and a descriptor bound to a caller-supplied commit
and existing schema profile. This is the first component of the separate Worker
release stage in [Release automation](release-automation.md). It does not publish
a Worker, apply SQL to D1 or replace the current
[deployment handoff](../services/commons/README.md#deployment-handoff).

## Create and check a candidate

Run from a reviewed checkout with Python 3.12 or later on Linux:

```sh
candidate_dir=$(mktemp -d)
candidate_commit=$(git rev-parse HEAD)
python3 scripts/commons-artifact.py create \
  --source-dir services/commons \
  --commit "$candidate_commit" \
  --out "$candidate_dir/commons.json"
python3 scripts/commons-artifact.py verify \
  --candidate "$candidate_dir/commons.json" \
  --expected-commit "$candidate_commit" \
  --expected-schema-sha256 1d1800a100d598b2076c4932ad866e4e254595326eb7fb387522b51775f01278 \
  --rebuild-source services/commons
```

The schema fingerprint above belongs to profile 1: the existing three pinned
migrations. Verification receives the expected commit and schema independently;
copying those values from an untrusted packet would only check self-consistency.
A supplied commit is a claim until the separate canonical-source, successful-CI
and trusted-artifact-transport gates establish its provenance.

`create` writes a new owner-readable file outside the service source directory.
It never overwrites an earlier candidate. `verify` opens the captured packet
again and can compare it with a separate source capture. The JSON result contains
module sizes and SHA-256 digests, schema requirements, `artifact_verified`,
`rebuild_matched` and the remaining gates. `deployment_authorized` is always
`false`. Keep or remove the temporary candidate directory as appropriate.

## Package and source boundaries

The packet contains exactly two top-level fields: `descriptor` and `files`.
Each production module is transported as canonical Base64 with its byte count
and SHA-256 digest in the descriptor. JSON key ordering and serialization are
deterministic. Reordered input keys are accepted only when all validated values
and decoded bytes still match; duplicate keys are rejected.

The only packaged files are `worker.mjs`, `security.mjs`, `identity.mjs`,
`participations.mjs`, `activity.mjs` and `work-items.mjs`. Local servers, database
adapters, tests, configuration and migration SQL are excluded. The source capture
rejects an unknown root `.mjs` file or any unexpected migration file, so an
extension cannot silently omit a newly introduced module or migration.

Profile 1 fixes the module entry point, compatibility date `2026-09-04`, no
compatibility flags and the exact hashes of the three existing migration files.
It does not accept arbitrary bindings, credentials, provider resource IDs,
upload destinations or extra descriptor fields. A future module or migration
requires a deliberate profile update and the corresponding release evidence.

The limits are 512 KiB per module, 2 MiB of decoded code and a 3 MiB packet.
Modules must be nonempty UTF-8 without NUL bytes. Reads use the existing bounded,
no-follow directory-descriptor implementation and reject symlinks, hardlinks,
special files and source changes during capture. Errors crossing the CLI boundary
contain fixed codes, without payload contents or supplied paths.

## Schema identity and its limits

Only the three hash-pinned initialization files are evaluated, in a fresh
in-memory SQLite database. Digest validation finishes before SQL evaluation.
The packet carries migration hashes and a schema fingerprint; it carries no SQL
to execute. The tools never receive a production database path or credential.

`schema_hash` accepts bounded `sqlite_master` metadata: object type, name, table
name and SQL. It includes tables, indexes, triggers and views. It removes SQL
formatting and comments outside quoted values while preserving token boundaries,
quoted identifiers and literal contents. For example, changing a default from
`'a  b'` to `'a b'` changes the fingerprint. It never executes observed SQL or
reads stored application rows.

The fixed metadata query excludes SQLite's internal objects and D1's `_cf_KV`
object. On 2026-09-08, a separate read-only comparison matched all 35 existing
schema objects and the six deployed modules; no application rows were read and
no production writes were performed. That observation does not replace a fresh
check immediately before a future release.

A matching schema fingerprint establishes structural identity. It does not prove
that changed application queries or behavior are compatible with existing data.
The service tests, exact source checks and later live acceptance remain required.
Changed migrations fail this code-only profile; they need their own rehearsal,
private backup and preservation evidence. A Worker rollback must retain newer
community records and additive tables.

## Validation and remaining integration

Run the artifact tests in both normal and optimized Python:

```sh
python3 scripts/test-commons-artifact.py
python3 -O scripts/test-commons-artifact.py
```

They exercise real source capture and a CLI round trip, independent rebuild
mismatch, changed initialization before SQL execution, schema literal changes,
malformed or oversized packets, wrong commits, extra modules, altered runtime
fields, unsafe filesystem references and private error-output boundaries.
The [complete contributor checks](../CONTRIBUTING.md#before-opening-a-pull-request)
also exercise Commons behavior with real SQLite transactions.

The separate [Commons rehearsal](release-commons-rehearsal.md) now creates and
transports this packet through canonical GitHub Actions, then records its
observed artifact identity and source comparison in a receipt. It has no
production access and leaves completed-run and required-check consumption pending.

The remaining Worker stage needs independently authenticated candidate provenance, an
independently bound destination and schema, constrained provider access,
serialization, a durable deployment journal, uncertain-outcome reconciliation
and live acceptance with conditional recovery. Neither the existing static
publication workflow nor this packet grants Worker or database write access.
