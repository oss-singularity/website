# Static transition rehearsal

**Implemented internal Python fixture backend; no deployment command.**
This stage of the [release architecture](release-automation.md) exercises real
file writes, process interruption and conditional rollback entirely offline.
It grants no release authority and enables no production automation.

The [pure planner](release-static-plan.md) supplies the operation order and
preservation rules. `scripts/static_fixture.py` creates the private filesystem
and anchors its descriptors; `scripts/static_transition.py` keeps the fixture
preparation API. The shared `scripts/static_engine.py` owns journaling,
application and recovery, using `scripts/static_posix.py` for bounded filesystem
operations. `scripts/test-static-transition.py`
contains executable examples, product integration and failure cases.

## Fixture and trust boundary

`create_fixture(installation, baseline)` exclusively creates a temporary
container with sibling `target`, `control` and `peer` directories for the fixed
logical destination `oss-static`. There is no argument for an existing target,
provider, endpoint or executable operation. The target begins with synthetic
installation bytes; a fixed peer sentinel detects unintended changes.

The fixture builder supplies an **independently trusted baseline**. It binds the
predecessor commit, exact descriptor bytes, manifest, generation, managed files
and parent-directory metadata, and separate `.htaccess` prefix/suffix hashes and
lengths. A rejected predecessor must never be used to regenerate its own expected
baseline. The tests establish their initial installation before submitting
altered or forged artifact pairs. Later baselines come from successfully verified
transitions recorded by the same fixture journal.

The container, control directory and journal are private to the current OS user.
Handles and attempt tickets are trusted harness state passed between its own
processes. Do not deserialize them from an artifact or expose the internal
captured-input test seam to untrusted callers. This is not an in-process Python
sandbox, a defense against root or hostile same-user writers, or proof of remote
filesystem behavior. Locks coordinate cooperating fixture processes; POSIX
replacement is not a compare-and-swap against an uncooperative writer.

## Preparation and use

Within `session(handle)`, `prepare(...)` accepts candidate/predecessor artifact
directories, descriptor paths and an independently expected candidate commit.
The candidate passes today's complete product checks and allowlist through the
trusted artifact checker shipped in this repository. The predecessor passes
bounded generic historical integrity checks and its independent baseline;
historical code is never executed. Both captures use bounded no-follow reads.
Inputs must not overlap each other or the fixture container. Writes use captured
bytes, never subsequently reread candidate paths.

After preparation, retain `session.ticket()`. Pass that exact `Attempt` to
`apply(ticket)`, `reconcile(ticket)` or `rollback(ticket)`, including after opening
a new session in another process. A different current attempt rejects the old
ticket before acting. Never substitute a newly observed ticket merely to make a
stale recovery request pass.

Preparation writes candidate staging files and verified preimage backups outside
the target. It preserves existing modes, UID and GID; new fixture files and
directories use explicit creation metadata for the current OS user. It does not
model arbitrary ownership changes, ACLs or extended attributes. Payload and
inventory bounds come from the artifact/planner contracts; journals are limited
to 2 MiB and each fixture permits at most eight attempts. Generation capacity is
checked before preparation, reserving room for application and rollback.

## Journal and interruption

The normal phases are `observed`, `staged`, `prepared`, `applying`, `applied` and
`verified`. Control state includes generation, baseline, attempt, plan digest,
progress and any pending operation. An unfinished attempt blocks new attempts,
even after process death releases the operating-system lock.

Before each target mutation, the engine durably records intent. It checks the
expected bytes, metadata, path identities, parent directories and staged source,
then publishes the prepared file or directory. Afterwards it observes actual
state before recording completion. A successful write return is insufficient.
Files and containing directories are fsynced. State replacement records the
verified generation and its baseline together; a journal checksum detects
corruption, not forgery by a writer who controls the private journal.

Required new directories precede files. Assets and data precede HTML, followed
by `.htaccess` and the unchanged artifact manifest. Individual replacements are
atomic on the tested local filesystem. This is not a whole-site atomic switch:
readers could see mixed generations during application.

`reconcile(ticket)` never writes target files. It compares a pending operation
with its before and after states, recording `not_applied` or `applied`. A third
state, changed metadata, missing backup, substituted path or ambiguous generation
blocks continuation with `reconciliation_required`. Consistent state permits an
explicit subsequent apply or rollback. A preparation interrupted before target
writes is closed as `aborted` after verifying the untouched target; a new attempt
must validate its inputs again.

Tests terminate disposable workers at staging, backup, intent, write,
observation, verification and generation boundaries. The trusted parent retains
the fixture and ticket while a fresh worker reads the durable state. These are
process-crash tests, not a claim about power-loss recovery on arbitrary storage.

## Preservation and rollback

Only planned candidate paths are written. Unowned collisions fail; retired
assets and unmanaged files, including `.well-known` contents, remain. Root
metadata stays unchanged. Links, hardlinks, special files, root aliases and
overlapping roots are rejected. Control state and backups stay outside the
simulated webroot.

The nonempty historical `.htaccess` block must occur exactly once in its installed
file. Its bound prefix and suffix stay in position around the candidate block.
The resulting block must also be unambiguous. The composite installed hash is
recorded separately from the artifact block's hash; artifact bytes and their
manifest remain unchanged.

Rollback has separate preparation and application phases with the same
intent/observation/reconciliation protocol. It requires the original attempt
ticket, matching generation, verified backups and unchanged postimages. It
restores only files actually written by that attempt. New files are removed
conditionally; created directories are removed only while empty. New non-target
files and their containing directories survive. Rollback advances the generation
monotonically and binds the restored baseline; it never rewinds the counter or
restores an entire tree. No database, Worker or community-data operation exists.

## Reports and remaining release gates

`report()` returns a bounded summary of phase, generation, candidate, plan digest
and operation counts with `fixture_only: true` and
`deployment_authorized: false`. `write_report(path)` creates an exclusive 0600
file outside the target. Reports exclude absolute paths, ownership IDs, overlay
contents and raw filesystem errors. Detailed journals and handles remain private.

Run the suite with ordinary and optimized Python as documented in
[CONTRIBUTING.md](../CONTRIBUTING.md). The product integration test builds the
current site and uses a manifest-valid historical fixture with an added and a
retired asset. Other tests cover stale tickets, concurrent attempts, byte and
metadata conflicts, broken backups, incomplete transfers, false successful
writes, preservation and fresh-process recovery.

A [fixed-command observer](release-static-observer.md) now provides a separate
read-only interface for an independently configured existing target. It does not
open a production target through the fixture API. The separate
[restricted remote writer](release-static-remote.md) uses the same engine with
its own installed target binding and static server policy. Production
credentials, provider acceptance, operational retention and a complete publication
workflow remain separate work. Exact release authority, required
checks, fresh provenance, Commons compatibility, TLS, origin/edge verification
and cache invalidation remain separate gates. An offline fixture result cannot
authorize production access.
