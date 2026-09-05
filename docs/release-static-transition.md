# Static transition rehearsal

**Planned design; not implemented.** This proposed next step in the
[release architecture](release-automation.md) would rehearse static file updates
and recovery entirely offline. It would not deploy a website, grant release
authority or enable production automation. Command names, flags and schemas
remain implementation decisions.

The first internal component is the [pure operation planner](release-static-plan.md).
It calculates and validates a plan in memory. The filesystem rehearsal, journal,
reconciliation and rollback described below are still planned work.

## Scope and inputs

The rehearsal would accept candidate and predecessor payloads with their
descriptors, independently expected identities, a predecessor baseline and
bounded synthetic fixture data. It would create and exclusively own a temporary
target for the fixed logical destination `oss-static`, a separate control
directory and a non-target sentinel. Inputs could not select an existing target,
backend, remote endpoint or executable operation. There would be no network,
credential access or execution of supplied code.

The candidate must pass the current complete product validation, including the
current file allowlist. The historical predecessor instead needs bounded generic
integrity checks: safe relative paths, no links or special files, complete unique
manifest coverage, actual file hashes, and a strictly typed descriptor matching
its commit, manifest hash, file count and byte count. Historical files need not
match today's allowlist. No historical checker would be downloaded or executed;
candidate requirements remain unchanged.

A separate baseline must bind the predecessor to the logical target, installed
generation, commit, descriptor byte hash, manifest hash and managed installation
inventory. These expected values cannot come solely from the submitted
predecessor. Fixtures would use independently fixed baseline records. A later
remote adapter would need an independently trusted observation or completed
release journal; a supplied hash or `trusted` flag is not proof of provenance.

Both payloads would be captured through bounded file-descriptor reads that reject
links and detect mutation. Writes would use those captured, validated bytes.
A private report would identify payloads, plan, fixture and observed outcomes,
explicitly stating that it covers a fixture and authorizes no deployment. It
would exclude credentials, overlay contents, absolute paths and raw errors.

## Operations and recovery

The core would produce a deterministic per-file plan with expected before/after
hashes and metadata. The intended phases are observation, staging, preparation,
application and verification; uncertain outcomes enter reconciliation. Rollback
would have separately journaled preparation, application and completion phases.

A target lock would cover forward operations, reconciliation and rollback.
Durable control state would record the active generation, attempt identity, plan
digest and phase. An unfinished attempt would block another attempt even after
process loss releases the operating-system lock. Stale plans and rollbacks must
never overwrite a newer generation.

Before the first target write, the adapter must verify the baseline and installed
generation, stage candidate bytes and verify backups of every affected existing
file. Before each mutation, it must durably record intent and check the expected
file type, hash and metadata. Afterwards, it must observe the resulting bytes and
durably record the outcome. A successful write call alone is insufficient.

Assets and data would precede pages, followed by `.htaccess` and the manifest.
Individual local file replacement may be atomic where metadata permits, but
this is not a whole-site atomic switch: readers can temporarily encounter mixed
generations. Remote filesystem behavior would require separate evidence.

An unknown outcome must never trigger a blind retry. Reconciliation would compare
the journal with actual state and classify an operation as not applied, applied
or conflicting. Unexpected bytes, metadata, missing backups or ambiguous
generation would block continuation. Crash recovery must work in a fresh process.

## Preservation and bounded rollback

Only planned candidate paths could be written. An unowned path collision must
fail rather than silently claim ownership. Files outside the plan, including
unmanaged `.well-known` entries and retired assets, would remain untouched. Root
permissions and ownership would remain unchanged. Aliased or overlapping roots,
links and special files would be rejected. Locks, journals and backups belong
outside the simulated webroot.

For `.htaccess`, the validated predecessor block must be nonempty and occur
exactly once in the installed file. Prefix and suffix must be bound separately
by length and hash and preserved in position. Only the managed block changes.
Missing, repeated or changed boundaries are conflicts. The artifact manifest
remains unchanged; the composite installed file needs its own hash, recorded
separately from the artifact block's hash.

Rollback would restore only verified preimages of files written by that attempt,
conditional on the active generation and matching postimages. It could remove
its own new files only while their bytes still match, and its own directories
only while empty. It must preserve newer non-target files and never restore an
entire backup tree. Changed managed files, overlays or backups block rollback.
There are no database, Worker or community-data operations.

## Acceptance and remaining boundaries

Required fixtures would cover:

- Real predecessor/candidate builds with legitimately added and removed assets;
  historical integrity succeeds, retired assets remain, and a forged predecessor
  pair fails against the fixed baseline.
- Hidden files, overlay variants, unowned collisions, changed bytes, path/FD
  substitution, links, special files and overlapping roots.
- Interruptions before and after staging, backup, intent, write, verification
  and generation recording; fresh-process reconciliation without blind retry.
- Concurrent attempts, stale plans, newer generations, interrupted rollback,
  changed backups and preservation of newly added non-target files.
- Bounded inputs, private exclusive reports, sanitized failures and protection
  checks that remain active under optimized Python.

Remote access remains operator-only until target and credential scope, shared
locking, durable journals, filesystem behavior and recovery are proven. Exact
release authority, required-check policy, fresh provenance, Commons compatibility,
TLS, origin/edge verification and cache invalidation remain separate work.
