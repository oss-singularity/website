# Static release rehearsal

The [rehearsal workflow](../.github/workflows/static-release-rehearsal.yml)
exercises the static artifact path on a push to the protected canonical `main`.
It has read-only repository/API permissions and no production credentials,
environment, deployment step or database operation.

## What a successful run shows

1. The GitHub repository name and numeric ID, event, branch, workflow and checked
   out commit agree. The workflow observes that protected `main` still points to
   that commit before building.
2. Two fresh builds pass the [artifact contract](release-artifacts.md) and match
   byte for byte, including the hash manifest.
3. Only `payload/` and `release.json` are uploaded from a dedicated staging
   directory. Hidden files are included, preserving `.htaccess` and `.well-known`.
4. The exact returned artifact ID is downloaded with digest mismatch treated as
   an error. The downloaded payload and descriptor are reverified against the
   original candidate.
5. The artifact's GitHub metadata matches its ID, digest, originating run,
   repository and commit. A second observation checks current protected `main`.
   Only then is a separate, sanitized `dry-run-plan.json` published.

Candidate and receipt names contain the commit, run ID and attempt. Uploads
cannot overwrite an earlier artifact, and expire after seven days. A failed or
stale check may leave an uploaded candidate. Even a published receipt does not
establish the eventual success of the whole workflow. The repository's Actions
tab links the run and its artifacts.

The receipt includes observed source/run identity, artifact ID and digest,
descriptor and manifest hashes, and the completed round-trip check. It excludes
event bodies, actor details, credentials and runner paths. Keeping it separate
avoids pretending an artifact knew its own upload identity before upload.

## What it does not establish

`deployment_authorized` remains false. This is a rehearsal, not a release or a
signature. A protected-branch flag does not prove that every protection rule is
adequate. A current-main observation is not a lock; main can move afterward.

The run cannot certify its own eventual success from inside itself, and this
workflow does not wait for the separate service and CodeQL checks. Before later
consumption, a release controller must independently verify the exact successful
run, all required checks, current source/artifact identities and payload bytes.
The production lock, predecessor, installed Commons compatibility, provider
scope, preserved overlays, backup/rollback and live verification are still
separate [automation gates](release-automation.md).

## Test the contract locally

Run `python3 scripts/test-release-rehearsal.py` for isolated context, transport
and receipt fixtures. It needs no GitHub token or production access. The
existing artifact suite covers malformed payloads and filesystem boundaries.
The first canonical workflow run also needs a real upload/download verification;
fixture success alone is not evidence that GitHub transported the candidate.

The workflow uses GitHub's documented [default variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables),
the pinned [upload action](https://github.com/actions/upload-artifact/tree/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a)
and [download action](https://github.com/actions/download-artifact/tree/3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c).
The [artifact metadata API](https://docs.github.com/en/rest/actions/artifacts#get-an-artifact)
provides the remote identity check. Future consumers must verify that identity
again rather than trusting a copied receipt.
