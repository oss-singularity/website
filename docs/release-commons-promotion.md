# Commons Worker promotion

This document defines the reviewed promotion procedure that turns the
[planner](release-commons-plan.md), the [transition fixture](release-commons-transition.md)
and the [live-validated Cloudflare adapter](../scripts/commons_cloudflare.py)
into a bounded release path for the Commons Worker. It separates what is
**specified**, what is **implemented**, and what stays an **operator
procedure** until its own verified implementation exists. The procedure is
design criteria, not a working automation: nothing here changes production
until each gate below has its own evidence.

The static publication path is the architectural template: independently
consumed candidates, durable intent, one serialized target, live acceptance
and retained rollback. Worker promotion differs in one decisive point — the
target is not a fixed filesystem but a versioned Worker whose bindings
(secrets, D1 database) must survive every transition unchanged.

## Ground rules

- **Code-only first.** A promotion that changes Worker code must demonstrate
  compatibility with the installed schema profile (see
  [artifacts](release-commons-artifacts.md)). Schema changes are a separate
  procedure with their own migration, backup and DDL inventory; they are out
  of scope here.
- **Bindings are inherited, never re-entered.** The live rehearsal on
  15 September 2026 confirmed the provider's model: staged versions reuse the
  installed bindings through `inherit` entries; secrets and database bindings
  cannot and must not be re-uploaded. A promotion whose staged version loses,
  renames or re-types any binding fails before activation.
- **One writer, durable intent.** A single promotion runs at a time. Before
  the first mutation, a durable intent record binds the candidate commit,
  descriptor digest, planned predecessor version and the operator or run
  identity. The record stays open until the promotion closes as promoted,
  rolled back or unresolved — the same closed-record discipline as the static
  deployment records.
- **Never repeat a mutation.** Stage, activate and restore are attempted
  exactly once per intent. Lost responses are resolved by observation
  (read the current deployments and versions by identity annotations), and an
  ambiguous outcome keeps the intent unresolved.
- **Every activation is paired with retained rollback.** The predecessor
  version is captured before activation and restored — not redeployed — on
  rollback, because versions are immutable.

## Procedure

1. **Consume the candidate.** Use the [completed-run consumer](release-commons-candidates.md)
   to bind one successful canonical rehearsal run: exact archives, rebuilt
   source and required-check provenance for the candidate commit. A missing or
   ambiguous rehearsal blocks promotion.
2. **Plan against the live predecessor.** Run the [version-bound planner](release-commons-plan.md)
   against the adapter's `observe()` snapshot: the active version must match
   the recorded release annotation (currently the `workers/tag` binding to the
   release commit), and the plan records the predecessor version, the code
   changes, and the expected unchanged set — bindings, routes, schedules and
   D1 schema fingerprint.
3. **Record the intent.** Write the durable intent (commit, descriptor digest,
   predecessor version, planned binding fingerprint) before staging.
4. **Stage.** Upload the candidate as a new version with `inherit` bindings,
   the annotated message/tag from the plan, and the compatibility date from
   the installed settings. Verify the staged version server-side: module set,
   bindings (including the D1 binding id and both secrets), handlers and
   compatibility date must equal the plan. A mismatch aborts the intent;
   nothing was activated.
5. **Activate once.** Deploy the staged version at 100%. Immediately verify
   live acceptance: the public `/api/v1` answers with the expected release
   identity, read endpoints return uncached published records, and the route
   and schedule inventory is unchanged.
6. **Close or roll back.** If live acceptance passes, close the intent as
   promoted. Otherwise restore the predecessor version once, re-run the same
   live acceptance against it, and close the intent as rolled back. Any
   unresolvable step closes the intent as unresolved and blocks the next
   promotion until an operator reconciles it.
7. **Preserve the evidence.** The promotion record keeps the staged version
   id, both deployment ids and the acceptance results. Staged versions are
   immutable; the provider's own version listing is the retention mechanism.

## Current state and next slices

| Step | State |
| --- | --- |
| Candidate consumption, planning, transition fixture | Implemented offline ([artifacts](release-commons-artifacts.md), [rehearsal](release-commons-rehearsal.md), [candidates](release-commons-candidates.md), [plan](release-commons-plan.md), [transition](release-commons-transition.md)). |
| Real adapter stage/activate/restore with inherited bindings | Implemented and live-validated on 15 September 2026: a byte-identical rehearsal staged, activated and restored the predecessor while every binding and the live API stayed unchanged. |
| Durable intent record for Worker promotions | Implemented: `scripts/commons_promotion.py` records intents under the distinct `promote:oss-commons` task so they never block static records; open or unresolved intents block the next promotion. |
| Promotion engine (stage, server-side verification, single activation, rollback) | Implemented with offline tests: lost stage and activation responses are resolved by observation under the call's own annotations, changed staged bindings abort before activation, and a failed live acceptance restores the predecessor exactly once. |
| Fixed operator command (steps 3–7) | Implemented: `scripts/commons-promotion.py` derives the plan from live provider state (installed bindings become inherit entries; predecessor, compatibility date and current release identity are read from the active version), takes the candidate packet and commit as inputs, uses the same live API acceptance as publication, and reports one sanitized JSON outcome. Account identifiers and the provider token come from the environment and stay outside the repository. |
| Candidate consumption and planning wiring | Not implemented as command wiring; the command deliberately performs no candidate download or planning itself — steps 1–2 run through the implemented contracts beforehand. |
| CI automation | Not started. Worker promotion must not run from PR code; it follows the same protected-canonical discipline as static publication. |
| CI automation | Not started. Worker promotion must not run from PR code; it follows the same protected-canonical discipline as static publication. |
| Schema migration | Separate procedure; remains gated by its own backup, DDL inventory and preservation evidence. |

The first implementation slices — the durable intent record, the promotion
engine and the fixed operator command for steps 3–7 — are implemented with
offline tests. What remains is wiring steps 1–2 and, afterwards, automation.

## Wiring specification for steps 1–2

The command currently expects the already-verified results of steps 1–2. The
following wiring makes it self-contained; it is specified here so the
implementation can be reviewed against a written contract.

- **Inputs to accept:** the canonical Commons rehearsal run id, its attempt,
  and the candidate commit. Everything else is derived.
- **Candidate consumption:** locate the two rehearsal artifacts by their
  committed name pattern on that exact run, download them, and run the
  implemented [candidate consumer](release-commons-candidates.md)
  (`commons_candidate.verify`) with the repository's own read transport. Its
  report supplies the verified candidate commit, packet digest and module set.
- **Plan construction:** run the implemented [planner](release-commons-plan.md)
  (`commons_plan.build_plan`) with the verified candidate packet, the
  predecessor packet reconstructed from the live predecessor version's
  content, a baseline built from the fresh `observe()` snapshot (generation,
  version id, deployment id, packet and observation digests), and the installed
  target policy. The planner's output is authoritative: `predecessor.version_id`
  is the rollback target, `desired_version.bindings` must equal the inherited
  live bindings, and `plan_sha256` is recorded in the promotion intent.
- **Engine feeding:** map the planner output onto the engine plan —
  predecessor version, inherit bindings over the observed installed bindings,
  compatibility date and current release sha from the active version, and the
  packet bytes exactly as verified. The engine's own staged-version
  verification then re-checks the same invariants server-side.
- **Refusals:** a changed active version or deployment id between planning and
  promotion (the planner's baseline checks) aborts before staging, mirroring
  the static path's stale-main discipline.

Three contracts close the remaining open points of that wiring:

- **Artifact names.** The canonical rehearsal publishes the verified packet as
  `commons-candidate-{sha}-{run_id}-{run_attempt}` and the separate receipt as
  `commons-rehearsal-receipt-{sha}-{run_id}-{run_attempt}`; the wired command
  locates both on the selected run by these exact names and downloads nothing
  else.
- **Generation mapping.** The planner's baseline carries a `generation` from
  the transition fixture's journal model. The live provider has no journal,
  but it has a provider-native monotonic counter: the version `number` the API
  assigns to every uploaded version. The wiring defines generation as the
  active version's `number`, recorded in the baseline and therefore bound into
  the observation digest — a concurrent upload by anyone else changes the next
  observation and is refused exactly like a changed predecessor.
- **Predecessor packet.** The predecessor packet is reconstructed from the
  live predecessor version's own content (the provider's multipart form), not
  from the candidate: `unpack` binds it to the recorded predecessor commit, and
  a mismatch between live bytes and that commit refuses the promotion.

## Workflow design (after wiring)

Automation follows the static publication discipline and Astra's original
design intent:

- `commons-promotion.yml`, **dispatch only** (no automatic trigger), protected
  canonical `main` guards identical to the publication workflow.
- Environment `production-commons` with a separately scoped provider token and
  the live-acceptance origin binding; no static secrets.
- Inputs: run id, attempt, commit. The job runs the wired command and uploads
  the sanitized outcome exactly like static publication; no artifact, log or
  summary may contain tokens or account identifiers.
- One production concurrency group shared with static publication, without
  canceling an in-progress run.
