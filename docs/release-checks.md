# Verify required release checks

The required-check verifier checks the four canonical CI results and their
provenance for one exact current `main` commit. It complements the separate
[candidate consumer](release-candidates.md). Neither result grants deployment
authority or enables automatic publication.

## Run from trusted code

Use a separately trusted checkout containing `scripts/release-checks.py`, its
companion verification modules and the reviewed baseline workflow. The verifier
compares that local workflow's captured bytes with the source at the expected
remote commit. Downloaded artifacts cannot supply its code or policy.

```sh
python3 scripts/release-checks.py verify \
  --expected-commit "$RELEASE_SHA" \
  --out "$CHECK_VERIFICATION_JSON"
```

Use a full lowercase commit SHA and a new output path. Supply an existing
caller-managed `GH_TOKEN` through the environment, never arguments or files in
the repository. The command uses fixed GitHub API GET routes and makes no remote
changes. Missing permissions or unavailable observations cause failure.

The read-only policy queries need more than ordinary workflow-result access:
GitHub requires fine-grained **Administration: read** for both
[branch protection](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection)
and [CodeQL default setup](https://docs.github.com/en/rest/code-scanning/code-scanning#get-a-code-scanning-default-setup-configuration).
The caller also needs access to repository contents, Actions runs/jobs and check
results. Do not assume the rehearsal's existing contents/Actions-only token can
run this command. This implementation configures no tokens, secrets or extra CI
permissions; its CI tests use offline fixtures.

## What is checked

The supported policy fixes the canonical repository, the four required context
names, their GitHub Actions app identity and the reviewed protection settings.
It requires strict checks, enforced administrator protections, linear history
and resolved conversations. The current policy requires **zero approving
reviews**; success does not establish human approval. Changes to the supported
policy or additional effective rules require review rather than being silently
accepted.

`repository-baseline` belongs to the repository's baseline workflow and a `push`
run. The three CodeQL `Analyze` contexts belong to GitHub's managed default-setup
workflow and use the `dynamic` event. The verifier checks that managed setup,
including the versioned API's combined `javascript-typescript` language.

For each workflow, one canonical run must match the expected SHA and current
attempt. The run, suite, jobs, check-run IDs, names and app identities must agree.
Each current required job and its steps must have completed successfully; the
baseline must contain the steps named in the separately trusted workflow.

Check-runs for the SHA are read with all results included. Historical attempts
are identified through the workflow's actual job records, never ID ordering or
names alone. They cannot supply a missing current result. Competing results,
multiple independent runs, partial reruns and incomplete or excessive lists
cause failure. This first version accepts complete lists of at most 100 entries;
it does not silently truncate or select the newest apparent success.

Main, policy, workflow configuration and relevant run/check observations are
read again before writing success. Any relevant change blocks the result.
These observations are not an atomic GitHub lock, and main can move later.

## Result and limits

Success writes a new owner-only JSON file and the same sanitized report to
stdout. It records the commit, policy and baseline-source hashes, required
check/job/run identities, `required_checks_verified: true` and
`deployment_authorized: false`. Credentials, actor details, API bodies and
private paths do not enter the report.

Failure returns a nonzero exit and a static JSON error code. Treat any failure
or interruption as unsuccessful, even if an output file exists. Resolve the
cause and use a new output path; an older report is not evidence for a failed
new attempt. The command does not retry remote calls or change protection rules.

CodeQL success establishes scan completion under the checked configuration,
not a zero-findings policy. Candidate verification, fresh promotion identity,
serialization, installed Commons compatibility, scoped publication access,
preserved files, rollback and live verification remain separate
[release gates](release-automation.md).

## Test locally

Run `python3 scripts/test-release-checks.py`. Fixtures cover policy changes,
incorrect provenance, competing or incomplete runs, historical attempts,
changing observations, bounded input and output handling. They require no
GitHub token or production access. A real canonical run is checked separately
before treating the new verifier as operationally proven.
