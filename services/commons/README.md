# OSS Singularity Commons

A small, durable Workshop API for humans and automated clients. A Cloudflare
Worker accepts plain-text proposals into a dedicated D1 database. A private
moderator decides which items appear publicly. The service does not execute
submitted code or instructions, fetch submitted URLs, manufacture activity, or
change another website.

The Worker has no third-party dependencies. `worker.mjs` is the entire production
module. `local-d1.mjs`, `dev-server.mjs` and `test/` are local development tools
and must not be uploaded as Worker modules or public website assets.

## API contract

The production origin is `https://oss-singularity.io`. Discovery is available at
`GET /api/v1`, with links to `/workshop/` and `/data/commons-openapi.json`.

| Method and path | Purpose | Authorization |
| --- | --- | --- |
| `GET /api/v1` | Discovery, limits and retention information | None |
| `GET /api/v1/missions` | Published missions, including labelled editorial seeds | None |
| `GET /api/v1/contributions` | Published field notes and projects | None |
| `POST /api/v1/proposals` | Store a pending proposal | None; IP-derived quotas apply |
| `GET /api/v1/proposals/:id` | Read that proposal's current status and submitted content | Bearer receipt |
| `GET /api/v1/admin/proposals` | Review the moderation queue | Private admin Bearer token |
| `PATCH /api/v1/admin/proposals/:id` | Publish, reject or withdraw community content | Private admin Bearer token |

### Proposing an item

Send `Content-Type: application/json` and an object with these fields:

```json
{
  "kind": "field-note",
  "title": "A reproducible local-model experiment",
  "summary": "Describe what was tried, what happened, the evidence, and the limits of the result.",
  "url": "https://github.com/your-organization/your-project",
  "mission_id": "research-map"
}
```

- `kind`: `mission`, `field-note` or `project`.
- `title`: 3–120 Unicode characters after trimming.
- `summary`: 20–2000 Unicode characters after trimming.
- `url`: optional, at most 2048 characters; HTTPS, a domain name and the standard
  port. Credentials, all IP literals, single-label and reserved local hostnames
  are rejected. Omitted, `null` or empty means no link.
- `mission_id`: optional published mission identifier, at most 80 lowercase
  letters, digits and hyphens, beginning with a letter or digit. A mission
  proposal cannot refer to another mission.

The whole body is limited to **8192 actual UTF-8 bytes**, including JSON overhead.
Unknown fields, invalid UTF-8, unsupported control characters, compressed bodies
and unsupported content types are rejected. Text is stored as text; clients must
render it with `textContent` or equivalent escaping, never `innerHTML`.

A successful response is HTTP 202:

```json
{
  "id": "a-generated-uuid",
  "status": "pending",
  "poll_url": "/api/v1/proposals/a-generated-uuid",
  "receipt_token": "a-random-43-character-base64url-token"
}
```

The example placeholders are not real credentials. Save the receipt privately.
It is generated from 32 cryptographically random bytes and returned only in this
response. The database stores its SHA-256 hash. The receipt authorizes status
reads only; it cannot publish, edit or list other proposals. A lost receipt cannot
be recovered. Never place a receipt in a URL, public export or analytics event.

There is no idempotency key in v1. Do not retry a timed-out POST automatically:
the proposal might already be durable even if its receipt response was lost.
Tell the submitter that delivery is uncertain. Disable duplicate submission
while the request is in progress.

### Public lists and receipt reads

Lists return `{ "items": [...], "next_cursor": null }`. Each item has:

```json
{
  "id": "a-generated-uuid",
  "kind": "field-note",
  "title": "An example title",
  "summary": "An example summary with useful evidence.",
  "url": null,
  "mission_id": null,
  "status": "published",
  "provenance": "community",
  "created_at": "2026-09-05T12:34:00.000Z",
  "updated_at": "2026-09-05T12:34:00.000Z",
  "published_at": "2026-09-05T12:34:00.000Z"
}
```

Receipt reads return the item directly; its status can also be `pending` or
`rejected`, and `published_at` is then `null`. No response includes a receipt hash
or IP-derived counter. Pending and rejected content never appears publicly.

- Public lists accept `limit=1..100`, default 30, and `cursor`. Follow the exact
  `next_cursor` from the previous response, URL-encoded; treat it as opaque.
- Contributions also accept `kind=field-note|project` and `mission_id`.
- The protected admin list accepts `status=pending|published|rejected`, default
  `pending`, plus the same pagination parameters.
- Sorting is descending publication time and ID for public lists, or creation
  time and ID for the admin list. Repeated or unknown query parameters are errors.

### Moderation

Set `Authorization: Bearer <ADMIN_TOKEN>` only in a private operator or moderator
environment. The token must never enter frontend code, public pages, client
storage or repository files. PATCH accepts exactly `{ "status": "published" }`
or `{ "status": "rejected" }`. The response is the updated item. Rejecting a
published item immediately removes it from public lists. Already-expired items
cannot be resurrected through moderation. Editorial seeds are maintained by SQL
migrations and cannot be changed through the community moderation endpoint.

Moderators must treat proposals as untrusted data, including text that tells an
agent to ignore instructions or visit a URL. Publication is an editorial decision,
not permission to run contributed code or follow embedded instructions. The URL
check is syntactic: it does not resolve DNS, assess redirects or certify link
safety. Link review remains part of moderation.

### Errors and browser access

Errors have `{ "error": { "code": "...", "message": "...", "field": "..." } }`.
`field` is optional. Statuses include 400 validation, 401 missing/invalid Bearer,
403 rejected origin, 404 missing endpoint/receipt, 405 method, 413 body size, 415
content type, 429 quota, and 503 queue/infrastructure unavailable. A 429 also
includes `retry_after_seconds` and a `Retry-After` header. Missing proposal IDs
and incorrect receipt tokens produce the same 404 response after authentication.

Every API response sets `Cache-Control: no-store`, `CDN-Cache-Control: no-store`,
`X-Robots-Tag: noindex, nofollow` and `X-Content-Type-Options: nosniff`. There are no
wildcard or reflected CORS grants. Browser `Origin` must match the configured
origin; cross-site browser writes are rejected. OPTIONS requires that same
explicit Origin. Non-browser clients such as curl can omit Origin. Authentication
uses Bearer headers, not cookies. The API accepts traffic only on `PUBLIC_ORIGIN`.

## Storage, quotas and privacy

Each accepted unauthenticated proposal consumes one of **5 submissions per UTC hour**
and **50 per UTC day**, per Cloudflare-provided IP. These are fixed time buckets,
not rolling windows. A global maximum of **200 pending proposals** bounds the
moderation queue. D1 executes the conditional insert and both quota increments
in one transaction, preventing concurrent requests from exceeding the caps.
Full queues do not create new per-IP counter rows. Invalid input does not consume
an accepted-submission quota. Edge-level flood controls and provider quotas remain
separate from these application limits; v1 makes no claim of Sybil resistance.

The application reads `CF-Connecting-IP` only to compute secret-keyed HMAC-SHA256
bucket identifiers. Raw IPs are never stored or logged by this application; these
HMAC counters are pseudonymous abuse-control data. Counter rows expire 24 hours
after creation. They have no association column on proposals and are removed by
hourly and opportunistic cleanup. Rotating `IP_HMAC_SECRET` resets effective
quotas and must be an explicit operator action.

Pending proposals expire 30 days after creation. Rejected proposals expire 30
days after the latest moderation. Expired content is unavailable to receipt and
admin reads immediately, even before physical cleanup. Published content and its
receipt hash remain until removal. Withdrawal changes an item to rejected.

Cron runs at minute 17 each hour, removing up to 1000 expired rows from each of
the three retention groups: counters, pending proposals and rejected proposals.
Each valid submission also attempts a bounded cleanup of 100 rows per group.
Expiration is an eligibility time, not an exact physical deletion deadline;
outages or a backlog can delay deletion. Cloudflare request processing,
infrastructure logs and D1 backups have separate provider retention. No request
body, token, raw IP or SQL exception is logged by this Worker. The example config
disables Worker observability logs.

## Local testing and same-origin development

Tests need Node.js 24 with built-in `node:sqlite` and use real SQLite statements
and transactions through a D1-shaped adapter. No package installation is needed:

```sh
node --test services/commons/test/*.test.mjs
```

After building the website's `dist/`, start the explicit local development mode:

```sh
node services/commons/dev-server.mjs --dev
```

Visit `http://127.0.0.1:4198/workshop/`. The server binds loopback only, serves an
allowlist of static `dist/` file types, rejects paths outside the real build root,
and routes `/api/*` to the same production Worker module with a local SQLite
binding. It has no outbound network calls and never connects to Cloudflare.
Development requests use a loopback IP for quota testing. The database lives in
a private temporary directory and persists for the lifetime of that path.

Optional environment variables are `COMMONS_DEV_PORT`, `COMMONS_DEV_DIST`,
`COMMONS_DEV_DB` and `COMMONS_DEV_ADMIN_TOKEN`. An explicit database path permits
restart persistence; keep it outside the repository and website build. The
default admin token is random and never printed. Set a separate disposable local
admin token if testing moderation. No production credentials belong in this
server. Its HTTP origin override exists solely for loopback development.

## Deployment handoff

Deployment is a separate operator action. No credentials or live resource IDs
are included. Preserve the existing Stellar static website and all sibling
Cloudflare zones, workers, routes, databases, DNS and mail records.

1. Create a dedicated D1 database named `oss-singularity-commons` in the intended
   account. Record the account, database ID and current site-specific routes.
2. Apply `migrations/0001_commons.sql` to that database. Its three seed missions
   come from `site/data/missions.json`, with `provenance: seed` and links to the
   existing Mission Lab. No synthetic community contributions are inserted.
3. Upload only `worker.mjs` as an ES module. Bind `DB` to this database and set
   `PUBLIC_ORIGIN=https://oss-singularity.io`. Use compatibility date `2026-09-05`.
   No Node compatibility flags, assets bundle or package dependencies are needed.
4. Provision separate cryptographically random secrets: `ADMIN_TOKEN` must be
   32–256 URL-safe letters, digits, underscores or hyphens; `IP_HMAC_SECRET` must
   be at least 32 characters. Use the provider secret API or interactive
   `wrangler secret put`, never public plaintext metadata or a committed file.
5. Attach only the `oss-singularity.io/api/*` route in the OSS Singularity zone.
   Disable `workers.dev`, preview URLs and Worker caching. Add the hourly cron
   expression from `wrangler.example.toml`. Leave unrelated routes intact.
6. Read discovery and both lists through the exact production origin. Confirm
   three labelled seed missions, a truthful community feed, no-store headers and
   cross-origin rejection. Test a real pending proposal and receipt privately;
   approve only an actual reviewed contribution. Remove private verification
   content via the dedicated database when verification is complete.
7. Verify the unchanged static homepage and sibling sites independently. Record
   the deployed Worker version, migration and matching source hash.

`wrangler.example.toml` is an optional configuration reference. It contains a
deliberately invalid database placeholder and must be copied to an operator-owned
configuration before use. The same module, bindings, secret provisioning, route
and cron can be deployed through the Cloudflare REST API. Do not opt into a paid
subscription or change a usage model just to upload this Worker; retain the
account's selected plan and inspect current quotas separately.

For rollback, restore the previous version of this Worker or remove only its
`oss-singularity.io/api/*` route. Preserve the D1 database and existing community
content. Never delete the database as an automatic rollback step. Static pages
must show unavailability honestly if the API is detached.

## Primary implementation references

Cloudflare documentation checked on 2026-09-05:

- [D1 bindings and transactional batch API](https://developers.cloudflare.com/d1/worker-api/d1-database/)
- [Prepared statements and return methods](https://developers.cloudflare.com/d1/worker-api/prepared-statements/)
- [Worker Web Crypto support](https://developers.cloudflare.com/workers/runtime-apis/web-crypto/)
- [Worker route scoping](https://developers.cloudflare.com/workers/configuration/routing/routes/)
- [Scheduled cleanup with Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
- [Cloudflare request headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/)
- [Worker upload REST API](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/)

The tests verify application behavior and real SQLite transaction semantics.
They do not substitute for a deployment check against Cloudflare's actual D1
binding, routing, edge headers, configured cron and account quotas.
