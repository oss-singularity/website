# Release automation

**Architecture agenda; deployment automation is not implemented yet.** The goal
is that an approved contribution can reach production through a reviewed PR,
successful checks and a reproducible release, without requiring Codex or one
maintainer's workstation. Routine promotion should become automatic once the
gates below have demonstrated reliable behavior.

The offline [static artifact contract](release-artifacts.md) validates payload
bytes and a constrained descriptor, including an independent rebuild comparison.
The [static release rehearsal](release-rehearsal.md) exercises canonical-main
builds and GitHub artifact upload/download, with a separate observed-identity
receipt. The independent [candidate consumer](release-candidates.md) checks a
completed canonical run, both artifact identities and the captured bytes. The
[required-check verifier](release-checks.md) separately checks the current branch
policy and exact workflow, run, job and check provenance. It uses caller-managed
read access; the rehearsal workflow does not receive additional permissions.
Provider adapters and production promotion remain separate work.

The target is exclusively OSS Singularity: its static destination, Commons
Worker, dedicated D1 database and necessary cache invalidation. The existing
[hosting contract](hosting.md) and
[Commons deployment handoff](../services/commons/README.md#deployment-handoff)
remain the release requirements until their portable replacement is verified.

## Contribution, credentials and release authority

PR checks run without production credentials. Deployment executes trusted code
from the protected canonical branch, after all required checks pass for the
exact commit. Privileged jobs must never check out untrusted PR code or execute
artifacts from an untrusted run. Pin actions to reviewed commit SHAs, minimize
`GITHUB_TOKEN` permissions, and require review of deployment code and workflow
changes. Use isolated runners; the operator workstation is outside this design.
[GitHub's secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use)
explains these boundaries.

Use protected environment secrets for production access, separately scoped for
static and Commons operations. Configure and verify environments before enabling
workflows: naming an environment does not establish its protection rules.
Environment approval can gate initial adoption; routine automatic releases must
still satisfy their configured protections.
[GitHub environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
control when jobs receive environment secrets.

Repository secrets serve one repository. Organization secrets need a genuine
shared purpose and an explicit repository allowlist; they do not substitute for
an environment gate. Prove that each provider credential or constrained
deployment endpoint cannot modify non-target resources. OIDC is an option only
after the actual provider's support and trust policy are established. Never
place operator credentials, private journals or database exports in public
artifacts or logs. See
[GitHub's secret scopes](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

## Implementation stages and acceptance criteria

The planned [static transition rehearsal](release-static-transition.md) records
the offline adapter's preservation, historical-baseline and recovery invariants.
It is a design contract, not an implemented publication command.

1. **Portable, constrained adapter.** Extract a release interface without
   workstation paths or private historical state. Fix the permitted destinations
   and operations. Offline fixtures must prove exact-byte promotion, preservation
   of non-target resources, interrupted-operation handling and rollback without
   overwriting newer community data. Copying today's private operator into CI
   does not meet this stage.
2. **Trusted post-merge dry run.** Build without deployment credentials. Produce
   a manifest binding the canonical repository, exact protected commit,
   successful run, artifact identity, file hashes and compatibility requirements.
   Validate that artifact and publish a sanitized release plan. Acceptance
   includes rejecting fork artifacts, changed payloads and stale release plans.
3. **Static promotion.** Promote those exact validated bytes through the
   constrained static adapter. Verify the destination, TLS, cache behavior and
   deployed hashes; retain a verified rollback target. Interrupted transfers,
   concurrent runs and recovery must pass before enabling routine automatic
   promotion. Static-only releases receive no database-write authority.
4. **Separate Worker and schema promotion.** Add Worker releases only after
   scoped permissions and durable recovery are proven. Code-only changes must
   demonstrate compatibility with the installed schema. Schema changes require
   their own rehearsal, private backup, exact DDL inventory and preservation
   evidence before dependent Worker and static promotion. File-path filters alone
   are not a compatibility check.

## Serialization and uncertain outcomes

Use one production concurrency group across the release stages, without
canceling an in-progress mutation. GitHub's repository-level
[concurrency control](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
does not establish commit order or lock manual operators. Every entry point
must respect the same durable target lock and journal; revalidate the intended
predecessor immediately before mutation.

Record attempt identity and phase durably before external writes. Runner loss or
an unknown migration, upload or rollback outcome blocks further promotion until
explicit reconciliation establishes the actual state. Never blindly retry a
write or restore an old database over newer work. Routine releases are the
automation target; exceptional recovery remains an operator procedure until it
has its own verified implementation.
