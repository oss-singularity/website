# Operations FAQ

Practical answers to questions that come up when contributing, watching the
release workflows or operating the documented procedures. Each answer links the
contract that governs the behavior. Nothing here changes a contract; when a
document and this page disagree, the document wins.

## Release workflows

### A publication run failed, but the site looks fine. What happened?

Check the deployment record first, not the run conclusion:

```
gh api "repos/oss-singularity/website/deployments?environment=production-static&task=deploy%3Aoss-static&per_page=1"
gh api "repos/oss-singularity/website/deployments/<id>/statuses?per_page=1"
```

- `success` ("Static bytes, origin, edge, API and rollback material verified."):
  the publication completed; the run's red conclusion came from a failed
  *confirmation read* after the status post was accepted. Since the bounded
  read retry this is rare, and the record stands.
- `failure` ("Publication failed; original static bytes and HTTP rollback
  verified."): the candidate was applied, failed live acceptance and the
  verified rollback restored the predecessor. The site intentionally stays on
  the previous release. Dispatch `plan`, review the report, then dispatch
  `publish` again when the cause looks transient.
- `error` ("Outcome requires reconciliation...") or no status: do not delete
  the record. Dispatch mode `recover`, which reconciles the retained attempt
  under its original identity and closes the record only after verified live
  bytes. See the [publication guide](release-publication.md).

A run conclusion and a deployment record are different artifacts. The record is
the durable truth.

### Which changes actually reach the site when a PR merges?

Everything that changes bytes under `site/` — pages, styles, data, the
`.htaccess` contract. Changes to release scripts, tests and `docs/` trigger the
same publication but produce identical static bytes; the run verifies and
republishes the same content. A failed publication of a docs-only PR therefore
never leaves the site stale.

### The `http_bytes_mismatch` failure keeps appearing. Is something broken?

It means live acceptance read bytes that did not match the candidate after the
cache purge — typically an origin-level cache serving stale content
transiently. The client retries, and on failure the verified rollback restores
the predecessor. If it recurs across unrelated commits, treat it as an origin
platform issue to raise with the host, not as a content problem; the
[hosting contract](hosting.md) owns that boundary.

### What does the `recover` dispatch mode do differently from `publish`?

`recover` closes one blocked deployment record: it re-reads the recorded
intent, reconciles the retained attempt under its original identity, completes
interrupted maintenance, and closes the record only after the resulting live
bytes pass the same acceptance as a publication. It never creates a deployment,
never repeats a mutation, and works while `STATIC_PUBLISH_ENABLED` is off. See
the [publication guide](release-publication.md#pilot-reports-and-exceptional-recovery).

## Commons worker and promotion

### Why do the Commons service tests fail locally with `No such built-in module: node:sqlite`?

The service uses Node's built-in SQLite module, which needs Node.js 22.5+ (the
repository targets 24). Local hosts with older Node can run the suite in a
container:

```sh
podman run --rm -v "$PWD":/repo:Z -w /repo docker.io/library/node:24-slim \
  sh -c 'node --test services/commons/test/*.test.mjs'
```

### How do I check what the Commons worker currently serves?

The adapter's `observe()` reports the active version, deployments, routes,
schedules and the D1 schema fingerprint; `version_detail()` returns one
version's server-side bindings, modules and runtime settings. Both are read
only. The [promotion procedure](release-commons-promotion.md) builds on exactly
these reads.

### Can I run a promotion or publication against the real provider to "just try it"?

No. Offline fixtures and synthetic providers prove the logic; live evidence
comes from canonical rehearsals and documented rehearsals with explicit
authorization. Credentials and account identifiers stay out of the repository
and out of CI beyond the scoped production environments. See
[release automation](release-automation.md) and the
[promotion procedure](release-commons-promotion.md).

## Contributing

### My Python test passes locally but fails in CI with `FileNotFoundError` for a module it imports.

Tests are invoked from the repository root in CI, not from `scripts/`. Resolve
sibling modules relative to the test file — `Path(__file__).resolve().parent` —
instead of relying on the current working directory.

### I changed a page/design and the check complains about budgets or sitemaps.

New pages must be added to `site/sitemap.xml`, registered in
`scripts/build-hub.py`, copied by `scripts/build-site.sh` and listed in the
allowlists of `scripts/check-site.py` (assets and per-page scripts). Inline
`style=""` attributes and inline scripts are rejected. Per-page budgets: 35 KB
HTML (45 KB for the Atlas), 65 KB CSS per page, 350 KB initial transfer.

### I edited a design concept screen — anything else to do?

Yes: re-capture its preview image in `design/roadmap-vision/screens/` so the
README stays truthful, and mirror the change in the public `/vision/` summary
page if the screen's shape changed. The
[vision README](../design/roadmap-vision/README.md) holds the checklist.

## This repository

### Is there an `AGENTS.md`?

Not in the repository. A local, untracked instructions file may exist in some
checkouts; it is private tooling preference, not a repository contract. The
governing documents are [CONTRIBUTING](../CONTRIBUTING.md), the
[documentation map](README.md) and the contracts linked there.

### Something here is wrong or outdated.

Fix it in the same PR as your change, or open an issue referencing the exact
document and section. The [documentation map](README.md) explains which file
owns which contract.
