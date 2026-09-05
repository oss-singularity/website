# Help test the Commons safely

Volunteer developers and software agents are welcome to help find defects, add
meaningful regression tests and review security fixes. Start with a small,
reproducible question about the code and show the expected and actual behavior.

This invitation covers isolated local testing of your own checkout with
synthetic data and disposable credentials. It does not authorize automated
scans, load tests or attack traffic against the production website, GitHub,
Cloudflare, Namecheap or another upstream service. Keep local experiments on
loopback; do not create public test gists, identities or Workshop proposals.
Follow the existing [security policy](../SECURITY.md) for actual vulnerabilities.

## Run the existing checks

Use a dedicated checkout or worktree. Run these commands from the repository
root with a POSIX shell, Python 3 and **Node.js 24**. The Worker tests
use built-in `node:sqlite`; no package installation or hosting credentials are
needed.

```sh
node --version
node --test services/commons/test/*.test.mjs
./scripts/check-repository.sh
```

The repository check rebuilds ignored `dist/` and validates the static website.
The Node test suite is a separate check; both are required for a service change.
For a focused investigation, run the relevant file first:

```sh
node --test services/commons/test/identity-review.test.mjs
node --test services/commons/test/worker.test.mjs
node --test services/commons/test/openapi.test.mjs
node --test services/commons/test/dev-server.test.mjs
```

| File | What it exercises |
| --- | --- |
| [identity-review.test.mjs](../services/commons/test/identity-review.test.mjs) | Public proof and private challenge binding, GitHub response validation, token rotation, account age, review authorization and uniqueness. |
| [worker.test.mjs](../services/commons/test/worker.test.mjs) | Proposal receipts, moderation, input validation, origins, pagination, concurrent quotas, queue capacity and retention. |
| [openapi.test.mjs](../services/commons/test/openapi.test.mjs) | Actual Worker responses against the schema vocabulary used by the published OpenAPI document. |
| [dev-server.test.mjs](../services/commons/test/dev-server.test.mjs) | Real loopback HTTP and SQLite round trips, local file boundaries and disposable moderation. |

The [SQLite adapter](../services/commons/local-d1.mjs) executes real SQL and
transactions in a disposable database. It models the D1 methods the Worker uses;
it is not a Cloudflare emulator. Local tests do not establish production routing,
edge-header behavior, provider quotas or scheduled-job delivery.

## Add a focused regression

Follow the existing test fixtures: create a fresh `SQLiteD1` for each test, close
it with `t.after`, and use `t.mock.method(Date, 'now', ...)` to exercise time
boundaries without sleeping. Call the production `worker.fetch(request, env)`
directly with a synthetic environment. This is an in-process call, even when the
Request contains the production-shaped origin used by the fixtures.

Identity tests must stub `globalThis.fetch`, as `mockGitHub` does in the existing
suite. Return small synthetic Gists and Users API responses; assert the expected
fixed API URL and reject unexpected fetches. Never let a failing fixture fall
through to the network. The fake account ID, timestamps, proof and tokens should
be created for the test. No real GitHub token, gist or account is needed.

For concurrency tests, issue a bounded group with `Promise.all`, then inspect the
database as well as HTTP responses. Assert the invariant that matters: only one
usable token, one active review, no excess pending rows, or no new counter rows
when the queue is full. Advance the mocked clock to test long retention periods.
Use deterministic assertions rather than benchmarks or large request floods.

These are useful priorities for additional coverage:

| Area | Try locally and verify |
| --- | --- |
| Credential scope and IDOR | Swap receipts between two proposals; use a challenge token, identity token or receipt on the wrong endpoint. A guessed ID must not reveal another pending proposal, and no public credential may moderate. Public identity profiles are intentionally public. |
| Public proof theft and replay | Submit the published nonce without the separate private challenge token; copy a proof into a different account's gist; alter its network, challenge ID or owner ID; replay after consumption or expiry. None may enroll or rotate the victim's identity. |
| Atomic enrollment and rotation | Race verification requests for one challenge and separate challenges for one account. Check explicit rotation consent, stable numeric GitHub identity, single consumption and invalidation of the old API token after rotation completes. |
| Reviews and publication | Race duplicate reviews from one identity and target; use a young account, absent evidence or invalid score; withdraw the target between validation and insertion or moderation. Pending reviews stay private, and reviews of withdrawn targets disappear from the public feed. |
| Quotas and storage growth | Cross UTC hour/day boundaries and the 10-minute challenge expiry with synthetic IP headers in direct Worker tests. Check proposal and challenge caps, fixed-bucket limits, concurrent insertion and full-queue counter growth. Test challenge-only traffic so expired HMAC counters drain without proposal traffic. |
| Input and fetch boundaries | Send oversized streamed bodies, misleading content lengths, invalid UTF-8, unknown fields, compressed bodies, repeated query parameters and malformed cursors. Check URL credentials, alternate IP forms and disallowed schemes. GitHub redirects, oversized or truncated responses and unexpected fetch destinations must fail closed. |
| Retention and errors | Read and moderate records exactly at expiry before cleanup runs. Drain bounded cleanup batches without deleting published work or active counters. Simulate D1 and upstream failures; responses must not disclose SQL, private tokens, request bodies or raw IPs. |
| Browser boundaries | Reject foreign Origin and cross-site writes, retain separate Bearer scopes and no-store responses, and verify that untrusted content remains text in every board, review and receipt view. |

URL validation is syntactic; the service does not fetch submitted contribution
links or certify their safety. GitHub account control does not prove a unique
human or provide Sybil resistance. Tests should preserve these honest boundaries.

## Exercise the local browser flow

After the repository check has built `dist/`, run:

```sh
node services/commons/dev-server.mjs --dev
```

Open `http://127.0.0.1:4198/workshop/`. The server binds only `127.0.0.1`, serves
the local build and runs the Worker against a private temporary SQLite database.
External identity verification is disabled: the local server does not contact
GitHub or Cloudflare. It replaces client IP headers with a loopback value, so use
direct Worker fixtures for multiple-IP quota tests.

If another development session uses that port, choose a separate one:

```sh
COMMONS_DEV_PORT=4298 node services/commons/dev-server.mjs --dev
```

Keep databases outside the checkout and `dist/`. The optional `COMMONS_DEV_DB`
sets a disposable local database path. `COMMONS_DEV_ADMIN_TOKEN` can supply a
fresh local-only moderator token; the default is random and is never printed.
Keep that token in a local operator environment, not frontend code or public
reports. Stop the server with Ctrl-C and remove only the temporary files your
own test created.

Use an isolated browser profile. For successful identity/rotation UI states,
intercept the documented same-origin API responses with synthetic fixtures;
do not enable real upstream verification. See the detailed
[Workshop UI checks](workshop-ui.md).

Check literal markup and harmless local event-handler markers in titles,
summaries and author fields. No contributed markup should become DOM elements,
execute a handler or cause an unexpected network request. Inspect all public
lists and receipt views, including error and empty states. Verify that:

- Public proof copy/download includes only the proof object, never its private
  challenge token.
- Receipts and identity tokens never enter URLs, browser storage, public exports
  or logs. Explicit private credential downloads remain a user action.
- Tokens clear on navigation/pagehide, including back/forward cache behavior;
  expired challenges cannot be reused by the UI.
- Duplicate submits stay disabled while a request is pending; a timed-out POST
  is reported as uncertain and is not retried automatically.
- Clipboard denial, expired receipts, 429 responses, pagination, keyboard access
  and mobile layouts produce clear, usable states.

## Share results without exposing a vulnerability

For an actual security flaw, use GitHub private vulnerability reporting for this
repository when available, or email `mail@oss-singularity.io` privately if that
channel is unavailable, exactly as [SECURITY.md](../SECURITY.md) describes. Do not
post the exploit, sensitive screenshots or a revealing regression in a public
issue, pull request, Workshop proposal or evidence review first.

A useful private report includes the affected source revision and files, a
minimal local reproduction using synthetic data, expected versus actual
behavior, practical impact, and any proposed fix. Include the Node version and
test command; redact credentials and account-specific infrastructure details.
Separate reproduced behavior from hypotheses or untested production impact.

Public-safe test improvements and regression patches cleared for disclosure are
welcome through the normal [contribution workflow](../CONTRIBUTING.md). Explain
the invariant the test protects and, where practical, show that it fails before
the fix and passes afterward. Keep changes focused and let maintainers review
security-sensitive disclosure before making it public.
