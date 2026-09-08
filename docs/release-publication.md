# Static publication

The [publication workflow](../.github/workflows/static-publication.yml) joins the
existing candidate, required-check, filesystem and live-verification contracts.
**Activation is not complete.** A configured environment, restricted production
endpoint, scoped credentials and a successful canonical pilot are required
before enabling automatic runs. The Worker and database remain separate release
stages; this workflow cannot update either.

## What a contributor can expect

Once enabled, successful repository checks on the current protected `main`
trigger a static publication. The client independently verifies all required
checks, including CodeQL, and consumes the single successful rehearsal for that
same commit. A failed check, ambiguous run, changed artifact or newer `main`
prevents publication. A source rebuild must exactly match the captured artifact.
Independent CodeQL and rehearsal queues may finish after the triggering check;
the client waits up to six minutes for those runs, then performs the full
provenance checks. Failure, ambiguity or a newer `main` stops that wait.

The entire `services/commons` Git tree must match the independently configured
deployed API commit, and the live `/api/v1` must report that commit. This is a
conservative static-only gate, not a claim of general backwards compatibility.
Backend changes, including migrations, require the separate Commons release
procedure and an updated independently verified API binding before dependent
static publication. Changes to the pinned `.htaccess` contract require an
operator update to the installed static policy.

## Durable intent and acceptance

The workflow serializes production jobs with cancellation disabled. The remote
endpoint separately locks its fixed target and checks its generation and
predecessor. Both controls matter: workflow concurrency does not lock a manual
operator or establish commit order.

Before preparation, the client creates a GitHub deployment with task
`deploy:oss-static`. Its sanitized payload records the candidate, original
attempt identity, predecessor hashes/generation, runtime digest and source-run
identities. It contains no credentials, private paths, file contents or private
server journal. The complete journal and rollback bytes remain on the endpoint.
GitHub's ordinary environment-job records are distinct from these explicit
publication records.

The previous publication record must be closed with either verified success or
verified rollback. An absent status, unresolved outcome or unrelated record
blocks the next publication. Creating an intent or reporting filesystem success
is not a successful release.

After applying the exact candidate, the client immediately purges only the OSS
zone. It then verifies HTML and stylesheet `MISS` → `HIT` behavior, complete
origin/edge payload bytes, MIME/security/cache headers, page aliases, permanent
canonical redirects, custom 404, Telegram homepage bytes, and certificate/hostname
validation for apex and `www`. Public and direct-origin requests use normal CA
validation; origin requests also check the connected address. The API identity
must remain unchanged; repeated mission, contribution and review reads must
return uncached public pages. Cloudflare configuration and the complete current DNS
inventory are compared before and after the transition.
Only the elapsed Development Mode timers are normalized, after verifying that
Development Mode is off; actual settings and modification timestamps remain part
of the comparison. Cloudflare documents this timer as seconds since expiration
in the [zone API](https://developers.cloudflare.com/api/resources/zones/methods/get/).

The managed edge `security.txt` has a separate explicit header contract. Its
fresh zone configuration must render to the exact source bytes, and its HTTPS
response must contain those bytes with Cloudflare provenance and `text/plain`.
This exception does not change other files' header requirements.

On failed acceptance, rollback restores only the attempt's verified preimages.
The cache is purged again and the previous captured payload receives full HTTP
verification. Changed operator files are preserved and leave the attempt
unresolved. Lost responses are observed using the retained identity and ticket;
an unrelated newly observed attempt is never adopted. A lost apply response can
resume once after reconciliation and fresh gates. A lost rollback response is
observed without blindly repeating rollback.

Only after live acceptance and bounded maintenance does the explicit deployment
record become successful. Maintenance preserves the current rollback material.
A bookkeeping or maintenance failure keeps the record unresolved without
undoing an already healthy verified site.

## Configure access before activation

Use a `production-static` environment restricted to the exact `main` branch.
Verify its actual deployment policy before inserting credentials. The repository
variable `STATIC_PUBLISH_ENABLED` controls automatic execution and the client's
publish mode; its absence or any value other than `true` keeps publishing off.
The flag must be repository-scoped because it is evaluated before the job starts.

| Environment setting | Purpose |
| --- | --- |
| Variable `RELEASE_READER_CLIENT_ID` and secret `RELEASE_READER_PRIVATE_KEY` | Private GitHub App with Administration read and mandatory Metadata read. The organization may reuse the reader across its repositories; this action explicitly requests a temporary token restricted to `website` and revokes it after the job. |
| Secret `CF_RELEASE_TOKEN` | Only this zone: cache purge plus the read permissions needed for zone, settings, DNS and cache-rule verification. No DNS, Worker, database or configuration write permission. |
| Secrets `STATIC_ORIGIN_IP`, `STATIC_SSH_USER`, `STATIC_SSH_PORT` | Independently verified provider connection binding. Values stay outside source and reports. |
| Secrets `STATIC_SSH_HOST_KEY`, `STATIC_SSH_KEY` | Pinned Ed25519 host key and separate restricted SSH identity. The key accepts only the installed fixed command, with no operator identity fallback. |
| Variable `STATIC_RUNTIME_SHA256` | Independently verified installed nine-file runtime digest. |
| Variable `STATIC_API_RELEASE_SHA` | Independently verified installed Commons commit. |

The built-in token retains Contents, Actions and Checks read plus Deployments
write for the sanitized intent/outcome records. Only the two administration
reads receive the additional policy token. The actual built-in-token audit
returned 403 for branch protection and CodeQL setup; the extra reader addresses
that observed requirement. It does not receive repository write permissions.
See [GitHub App permission selection](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app).

No cPanel token or ordinary operator SSH identity belongs in this environment.
The client restores the private key's final newline when creating its temporary
identity file. Secret uploads through the GitHub CLI can trim that newline;
OpenSSH must still be able to read the resulting key file.
Independently install and verify the [fixed endpoint](release-static-remote.md),
provider mapping, root identities, static handler policy and initial predecessor
before giving the job its restricted key. The initial pilot additionally needs
the full private hosting/rollback/preservation evidence in [Hosting](hosting.md).
Those bootstrap bindings cannot be initialized or changed by a publication
request.

## Pilot, reports and exceptional recovery

Dispatch `plan` on canonical `main` first. It runs the real candidate download,
required-check, rebuild, baseline HTTP, API and edge-configuration checks. It
does not prepare/apply a candidate, create a publication deployment or purge a
cache. The endpoint's status operation may complete its documented private
allocation recovery; it is not the separate strictly read-only observer.

After reviewing that result and the scoped access proof, enable publishing and
dispatch `publish` for the same freshly checked `main`. Retain the exact live
proof and exercise rollback before treating unattended promotion as established.
The workflow's summary and artifact contain only the sanitized outcome. Private
keys, archives, tickets and local request material are confined to temporary
private runner storage and are not uploaded.

SSH failures report only a fixed category for host identity, authentication,
connection failure or an unconfirmed outcome. Bounded raw diagnostics remain in
temporary private storage and are removed after the call. A category never
proves that a mutation did not happen; the original attempt still needs the same
reconciliation before further writes.

A lost runner leaves the original identity in the deployment intent and the
filesystem journal on the endpoint. A new workflow run refuses an unresolved
intent. An operator must inspect that original attempt, reconcile the filesystem
and complete live verification before closing its record. Do not delete an
unresolved record or reset the server journal merely to unblock another run.
The ordinary automatic path and exceptional operator recovery have different
acceptance requirements.

## Verify changes

Run `python3 scripts/test-static-publication.py` and its `python3 -O` counterpart,
then the [complete repository checks](../CONTRIBUTING.md#before-opening-a-pull-request).
The publication tests use installed isolated endpoint processes and synthetic
external-service responses. They cover durable intent ordering, lost replies,
process-exit reconciliation, rollback and preservation of intervening writes.
Real GitHub artifact transport, scoped credentials and origin/edge behavior need
separate canonical/provider evidence; offline fixtures do not establish them.
