# Portable static release artifacts

This is the first offline building block of [release automation](release-automation.md).
It validates a static payload and binds its existing hash manifest to a declared
commit. It performs no deployment, database operation or credential lookup.

## Create and inspect a candidate

Use a clean, trusted checkout and its build/check scripts. Keep the descriptor outside
the payload, so it cannot become a public website file. The tested environment is
Linux with Python 3.12, a POSIX shell and GNU build utilities. Directory-descriptor
and no-follow filesystem support are required. Use a real temporary directory:
symlinks in any ancestor of the payload or descriptor path are rejected, including
a symlinked `TMPDIR`.

From the repository root:

```sh
release_plan_dir=$(mktemp -d)
./scripts/build-site.sh "$release_plan_dir/payload"
python3 scripts/release-artifact.py create \
  --artifact-dir "$release_plan_dir/payload" \
  --commit "$(git rev-parse HEAD)" \
  --out "$release_plan_dir/release.json"
python3 scripts/release-artifact.py verify \
  --artifact-dir "$release_plan_dir/payload" \
  --descriptor "$release_plan_dir/release.json" \
  --expected-commit "$(git rev-parse HEAD)"
```

An independent second build can be compared with `verify --rebuild-dir DIR`.
The verifier reads the candidate as supplied; it does not rebuild or repair it.
The build sorts manifest paths with the fixed `C` locale, so a contributor's
language setting does not change the manifest bytes.
Run `python3 scripts/test-release-artifact.py` for the offline regression suite.

## What the descriptor means

The [descriptor schema](../scripts/release-artifact.schema.json) fixes the
repository to `oss-singularity/website` and the payload kind to `static-site`.
It contains a format version, a full lowercase commit SHA, the existing
`dist-manifest.sha256` file's digest, file count and total byte count. It contains
no provider settings, absolute paths, timestamps, credentials or copied file
contents. The existing manifest remains the only list of per-file hashes.

Creation and verification use the production file allowlist, content-addressed
social-image convention, manifest checks, site rules and machine-data contracts.
Malformed paths, duplicate entries, symlinks, hardlinks, special files and
unexpected content fail verification. Reading is bounded. A supplied descriptor
must match the expected commit and the actual payload; unknown or duplicate JSON
fields are rejected.

## What still needs external proof

Matching bytes and matching declared metadata are not proof of provenance. An
untrusted party could replace both payload and descriptor. The release pipeline
must independently bind them to the exact protected commit, successful trusted
run and immutable artifact identity. It must also reject a stale predecessor
and hold the shared production lock before changing the target.

The offline report therefore keeps `deployment_authorized` false. It does not
prove Worker/schema compatibility, provider access boundaries, preserved
overlays, backup availability or live behavior. Those remain separate release
gates. A future adapter must transfer the exact bytes it reverified; a successful
check does not make a mutable directory immutable.

Production automation is still under construction. No production environment,
secret or deployment trigger is enabled by this artifact contract.
