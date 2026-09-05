# Verify a completed release candidate

The candidate consumer joins a completed [GitHub rehearsal](release-rehearsal.md)
to the exact downloaded candidate and receipt bytes. It gives contributors and
automated agents the same inspectable handoff without production access.
`deployment_authorized` always remains false.

## Start from trusted code and explicit identities

Run `scripts/release-candidate.py` from a separately trusted verifier installation,
with its companion artifact and product checkers. Those checkers must represent
the candidate's reviewed product contract. A changed allowlist or budget can
legitimately reject an older candidate; do not weaken checks to make it pass.
The command does not prove the trustworthiness of its own installation.

Select a completed canonical rehearsal run and its exact attempt. Record its
40-character lowercase commit SHA and the numeric IDs of both artifacts. Obtain
the original ZIP archives separately, without extracting or executing them. Keep
them in an owned staging directory outside executable or served paths.

The candidate name is `static-candidate-SHA-RUN-ATTEMPT`; the receipt name is
`static-rehearsal-receipt-SHA-RUN-ATTEMPT`. Names help cross-check identity but do
not replace IDs or hashes. A copied `dry-run-plan.json` alone is insufficient.
There is no automatic selection of the newest artifact or download command.

A caller-managed `GH_TOKEN` supplies read-only GitHub API access. A GitHub runner
needs only repository contents and Actions read permissions for this command.
Pass credentials through the caller's existing environment boundary, never a
CLI argument, archive, report or checked-in file.

With the following variables set to the selected identities and local paths:

```sh
python3 scripts/release-candidate.py verify \
  --expected-commit "$CANDIDATE_SHA" \
  --run-id "$CANDIDATE_RUN_ID" \
  --run-attempt "$CANDIDATE_ATTEMPT" \
  --candidate-id "$CANDIDATE_ARTIFACT_ID" \
  --candidate-archive "$CANDIDATE_ZIP" \
  --receipt-id "$RECEIPT_ARTIFACT_ID" \
  --receipt-archive "$RECEIPT_ZIP" \
  --out "$VERIFICATION_JSON"
```

IDs and attempt must be positive decimal integers without leading zeroes. Use a
new output pathname; the command refuses to overwrite an existing file.

## What a successful verification establishes

The consumer independently reads fixed resources in `oss-singularity/website`.
It checks the canonical repository IDs, active rehearsal workflow, current
protected `main`, and successful completion of both the selected run and its
exact attempt. The run path must be the exact workflow path, optionally suffixed with `@main`;
its workflow ID and separately read active workflow path must also agree.
The latest attempt must still match; an older successful attempt
cannot hide a later retry. The expected commit must still be current `main`.

Both artifact IDs, names, source identities and unexpired metadata must agree.
Each original ZIP's SHA256 must match its independently read GitHub digest before
its contents are processed. The consumer checks these remote conditions again
after processing the bytes.

Archive input uses bounded reads through actual no-follow file descriptors. A
bounded central-directory check precedes ZIP entry allocation; path, entry,
file-size and decompression limits reject links, special entries, duplicate
names and escapes. Local records must cover the archive without unindexed
members or contradictory headers. Complete bounded decompression verifies the
actual size, stream end and CRC, including empty directories. Candidate contents are restricted to `release.json` and
`payload/`; the receipt contains only `dry-run-plan.json`. Normal ZIP and ZIP64
end records are supported, with no multi-disk archives or archive comments.

Captured files enter a temporary private snapshot. The existing
[artifact contract](release-artifacts.md) rechecks the descriptor, complete
manifest, exact product allowlist, hidden files and product checks. Receipt fields
must agree with these bytes and the independently observed identities. No archive
file becomes a script, import, build input or browser page during verification.
The consumer does not run a build; `producer_reproducibility: matched` records
the successful producer's checked result, while `consumer_rebuild: not_performed`
states this command's narrower work.

## Read the result and handle failure

Success writes the same sanitized JSON to stdout and a new owner-only `0600`
file. It records `kind: static-candidate-verification`, source/workflow/run
identity, both artifact IDs and digests, descriptor and manifest hashes,
`rehearsal_candidate_verified: true`, completed checks and remaining gates.
It excludes credentials, actors, API response bodies and local paths.

Rejected input or an unavailable remote observation returns a nonzero exit and
a static JSON error code. There are no API write operations or automatic retries.
Treat any nonzero exit or interruption as failure, even if an output file exists.
Use a fresh output path after resolving the cause; never treat an older report as
success for a failed new attempt. Input archives are not rewritten, and temporary
snapshots are discarded.

The result describes captured bytes and observed GitHub state. It is not a
signature, a filesystem seal or a lock; local files and `main` can change later.
All required GitHub checks, protection-policy adequacy, fresh promotion identity,
serialization and predecessor, installed Commons compatibility, scoped publication
access, preserved overlays, rollback and live verification remain separate
[release gates](release-automation.md). This command does not enable deployment.

## Test locally

```sh
python3 scripts/test-release-candidate.py
```

The suite builds trusted source only into temporary directories, produces real
artifact/receipt fixtures and injects offline GitHub responses. It covers stale
or failed runs despite a positive receipt, mismatched attempts and artifacts,
ZIP/JSON tampering, filesystem replacement, output boundaries and optimized
Python execution. It needs no GitHub token or production connection. A separately
checked real canonical pair remains necessary evidence for GitHub transport.

GitHub documents [workflow run attempts](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run-attempt)
and [artifact metadata](https://docs.github.com/en/rest/actions/artifacts#get-an-artifact).
