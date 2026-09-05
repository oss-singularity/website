# Static operation planner

`scripts/static_plan.py` is the first internal component of the planned
[static transition rehearsal](release-static-transition.md). Its `build_plan`
function calculates a deterministic file plan entirely in memory. It does not
read an installation, write files, acquire locks, apply a plan or deploy a site.
The journal, recovery backend and command-line rehearsal remain unimplemented.

## Trusted inputs

The caller must first capture both payloads without following links or races,
validate the candidate with the current product checks, establish the expected
candidate commit, and independently obtain the installed predecessor baseline
and a fresh, complete inventory. Historical payloads need their own generic
integrity validation; their file list need not match today's product allowlist.
No historical code should be executed.

The planner rechecks the structure, byte limits, manifest coverage, descriptor
identity and baseline bindings of those captured values. It cannot establish
their provenance, freshness or actual filesystem type from supplied data.
Constructing a baseline from the submitted predecessor just to make a failed
comparison pass defeats the contract.

The keyword-only interface accepts:

| Argument | Value |
| --- | --- |
| `candidate`, `predecessor` | Plain relative-path-to-bytes dictionaries, including `.htaccess` and `dist-manifest.sha256` |
| `candidate_descriptor`, `predecessor_descriptor` | Exact JSON bytes using the existing [static artifact descriptor](release-artifacts.md), at most 4096 bytes |
| `expected_candidate_commit` | Independently expected full lowercase commit SHA |
| `baseline` | Fixed target, installed generation, predecessor commit, descriptor and manifest hashes, owned-inventory hash and separate overlay bindings |
| `inventory` | Complete typed file/directory records for `oss-static`, its generation and root metadata |
| `installed_htaccess` | Captured installed bytes, including retained prefix and suffix |
| `creation_metadata` | Explicit file and directory mode, UID and GID policy for absent paths |

Files carry their hash, size, mode, UID, GID and link count. Directories carry
mode, UID and GID. The baseline's owned-inventory hash covers root metadata,
predecessor files and all their parent directories. Canonical hash inputs use
sorted JSON keys, compact separators and ASCII escaping. The tests provide
synthetic examples of the exact shapes; this internal interface can evolve with
the future backend.

## Planned operations

Each candidate file becomes `create`, `replace` or `keep`, with its expected
before and after records. Assets and data precede HTML pages, then `.htaccess`,
then the manifest; paths sort lexically within each phase. Directory requirements
include preconditions for existing parents and explicit metadata for new ones.
Existing file and directory metadata is retained.

An unowned path collision fails. Retired assets and other non-target entries are
preserved. The predecessor `.htaccess` block must occur exactly once in the
installed bytes. Prefix and suffix are bound separately by length and hash and
remain in place around the candidate block. The resulting composite hash is
distinct from the artifact block's hash; the artifact manifest stays unchanged.
Ambiguous old or resulting block placement fails.

Payloads, inventories and projected installations remain within the bounded
offline contract. Malformed paths, links declared in inventory, special types,
missing parents, changed baseline metadata and excessive input are rejected.
Rejection uses static `ArtifactError` codes. Inputs are not mutated.

## Private plan and remaining work

The returned dictionary records the fixed target, expected generation, identities,
operations, directory preconditions, preserved paths and a deterministic
`plan_sha256`. It always states `plan_only: true` and
`deployment_authorized: false`.

**This is private planning data, not a public release receipt.** It contains
installation paths and numeric ownership metadata. It contains no payload or
overlay contents. A future public report must deliberately select safe fields.
The plan hash is an integrity binding, not a signature or grant of authority.

A future backend must independently capture and recheck the real installation,
serialize operations, durably journal intent and outcomes, reconcile interrupted
writes and enforce conditional rollback. It must consume the verified captured
bytes. Neither this plan nor individual atomic file replacement establishes an
atomic switch of the entire website.

Run `python3 scripts/test-static-plan.py` for the offline fixtures. They exercise
legitimate asset additions and retirement, independent baseline failures, path
collisions, overlays, metadata, bounds, deterministic ordering and unchanged
inputs without a token or production access.
