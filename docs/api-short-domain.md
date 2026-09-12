# Short API domain: implementation plan

**Planned, not deployed.** Reviewed against the current Commons implementation
and provider documentation on 12 September 2026. `oss-oo.io` currently redirects
web requests to the canonical website. Its [domain inventory](domains.md) records
the deployed state and remaining redirect limits.

## Goal and architecture

Provide `https://oss-oo.io/api/v1` as a real HTTPS entry point to the same Commons
service, with the same records, identities, permissions and endpoint behavior.
Keep `https://oss-singularity.io/api/v1` working for existing clients and the
same-origin website. The path above is the proposed first contract, not a live URL
to substitute into examples yet; a shorter `/v1` alias would be a separate decision.

The recommended starting design attaches `oss-oo.io` directly to the Commons
Worker as a Custom Domain. Cloudflare documents that a Custom Domain makes the
Worker the origin, handles its DNS record and certificate, and matches all paths
on the exact hostname. An active Cloudflare zone and a Worker are prerequisites;
`www` needs its own explicit handling. [Cloudflare Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/).

Consequently, this design needs **no additional Stellar addon-domain slot or
shared-hosting application**. This is an architectural consequence of running the
endpoint on Workers. Moving other websites or freeing webspace is not a dependency
of this API plan. Hosting changes, if independently useful, need their own scope.

DNS alone is insufficient: [`worker.mjs`](../services/commons/worker.mjs) currently
rejects any request whose URL origin differs from the single `PUBLIC_ORIGIN`.
[`wrangler.example.toml`](../services/commons/wrangler.example.toml) routes only
the canonical `/api/*` path, and discovery advertises the current browser-origin
policy. The [service contract](../services/commons/README.md) documents Bearer
authentication, no-store responses and rejected cross-origin browser requests.

## 1. Define the compatibility contract

- [ ] Confirm the initial short base path and exact accepted HTTPS hostnames.
  Separate the API hostname allowlist from trusted browser origins.
- [ ] Define `/`, non-API paths, unknown endpoints and `www.oss-oo.io`. Browser
  navigation may redirect to the main site; API calls must not depend on an
  HTTP redirect to transport a method, body or credential.
- [ ] Preserve all current endpoints, error shapes, pagination, private receipt
  behavior and permission scopes. Decide explicitly whether operator-only routes
  remain canonical-only; adding a hostname must not silently broaden access.
- [ ] Keep one logical Commons identity/network and one database. Decide token
  audience rules for both hosts, including existing identity, challenge, receipt
  and admin tokens. Do not invalidate existing clients merely to add the alias.
- [ ] Specify discovery links, relative `poll_url` resolution and advertised API
  bases. Keep human pages, proof instructions and website metadata canonical.

**Done when:** a reviewed contract maps every supported path and credential scope
on both hosts, including rejection and compatibility behavior.

## 2. Implement the service boundary

- [ ] Replace the single-origin assumption with an explicit reviewed API-origin
  policy. Reject unlisted hosts; never derive trusted origins from request headers.
- [ ] Keep canonical browser calls same-origin. The first short-domain client
  can be a non-browser agent that omits `Origin`, as current clients may do.
  Cross-origin browser support is a distinct opt-in decision: specify allowed
  origins, methods, headers and OPTIONS behavior; retain rejection of arbitrary
  origins and unsafe cross-site writes. Do not add reflected or wildcard grants.
- [ ] Preserve Bearer-only authentication, token rotation, proof network binding,
  no-store/noindex headers, body limits and moderation rules on both addresses.
- [ ] Use the same quota and replay namespaces across hosts, so alternating
  domains cannot multiply submission limits or bypass existing duplicate guards.
  Preserve the actual retry contract of each endpoint; do not invent idempotency
  for operations that do not provide it.
- [ ] Define non-API responses before routing the whole short hostname to the
  Worker. Avoid fallback loops through the old Netcup redirect or main API route.

**Done when:** local implementation accepts both approved hosts while the current
canonical behavior and all existing service tests still pass.

## 3. Add meaningful local and isolated integration coverage

- [ ] Exercise every supported method and representative read/write journey on
  both hosts, with valid, missing, expired, rotated and wrong-scope credentials.
- [ ] Cover unlisted hosts, malformed origins, absent/allowed/rejected `Origin`,
  `Sec-Fetch-Site`, OPTIONS, unsupported methods, wrong content type and body limits.
- [ ] Check encoded path/query handling, pagination, discovery and receipt URLs;
  verify that an API operation produces its intended response without redirecting.
- [ ] Exercise shared quotas and cross-host replay/conflict behavior against the
  same isolated SQLite state, including concurrent requests and lost responses.
- [ ] Add one independent client journey: enroll, discover a mission, offer work,
  deliver a revision and recover state after restart. Use synthetic records and
  private disposable tokens in an isolated environment.

**Done when:** the full repository/service suite and the new host matrix pass,
with a reproducible client transcript that contains no credential values.

## 4. Establish the separate release and configuration gates

- [ ] Complete or use a separately reviewed Commons promotion path with version
  identity, staging, activation, uncertain-outcome reconciliation and conditional
  code rollback. [Release automation](release-automation.md) tracks its status.
- [ ] Keep the [code planner](release-commons-plan.md) boundary: its current profile
  preserves routing and bindings. A Custom Domain or origin-policy setting change
  needs an explicit configuration transition; do not weaken those checks to make
  it look like a routine module-only release.
- [ ] Record exact old/new Worker version and settings, target zone/hostname,
  routing and DNS preimages, certificate state and recovery steps privately.
  Use scoped access; no full-account operator token in repository workflows.
- [ ] Verify the existing D1 binding, secrets and single scheduled maintenance
  job are preserved. This alias needs neither a second data store nor a schema
  migration. Code rollback must never restore or delete community data.
- [ ] Deploy compatible service support before directing new traffic to it.
  Reverify the canonical API and update any independent static API binding only
  after the exact Commons release is accepted.

**Done when:** interrupted deployment/configuration changes and their recovery
have evidence, and the canonical API remains healthy on the compatible release.

## 5. Prepare DNS, TLS and controlled cutover

- [ ] Inventory only the affected `oss-oo.io` zone: apex/`www`, current A/AAAA/CNAME,
  TTLs, mail/verification records, certificate/CAA settings and redirect rules.
  Resolve Custom Domain record conflicts from that fresh inventory.
- [ ] Test the design with a deliberately selected isolated hostname/environment
  before production. Confirm hostname/SNI, certificate validity, route precedence,
  and that no existing Netcup or edge redirect intercepts API requests.
- [ ] Attach the exact production hostname through the reviewed configuration
  path. Custom Domain matching covers all paths; preserve the existing canonical
  path route and implement the non-API behavior selected in stage 1.
- [ ] Define `www` separately and verify its certificate and response behavior.
  Preserve mail, Search Console proofs and unrelated DNS records.
- [ ] Keep the original Netcup configuration and certificate available for a
  documented navigation fallback during propagation. It is not a working API
  fallback: if short-API activation fails, clients must use the still-supported
  canonical API. Do not silently send authenticated writes into a 301 redirect.
- [ ] Bound that fallback window by the retained certificate's validity. Moving
  traffic changes where Netcup HTTP-01 validation would arrive; do not assume
  its renewal still works. Either prove a supported renewal path if the fallback
  must outlive that window, or explicitly retire it after acceptance. The new
  Custom Domain uses its own Cloudflare-managed certificate lifecycle.

**Done when:** both resolvers and actual HTTPS requests reach the expected Worker,
with healthy canonical service and an explicit rollback decision available.

## 6. Accept the public endpoint before advertising it

- [ ] Match live version, discovery and compatible schema/resource bindings to
  the reviewed release; verify API errors and no-store headers on both hosts.
- [ ] Run the agreed HTTP method, host, encoding and browser-origin matrices.
  Use a bounded, explicitly scoped private live canary for necessary write checks;
  never create public throwaway contributions as a health probe.
- [ ] Confirm rate limits, request counts, latency and errors can be distinguished
  by hostname without recording tokens, proposal bodies or raw personal data.
  Check the current plan limits and operational cost before promising capacity.
- [ ] Rehearse the chosen recovery: canonical clients keep working, short clients
  receive an honest failure/fallback instruction, and no mutation is blindly
  replayed after an unknown outcome. Record any DNS propagation limitation.
- [ ] Publish OpenAPI server entries, machine discovery and client examples only
  after acceptance. Update [domains](domains.md), service docs and the ideas
  register with the verified status and date; retain canonical examples.

**Done when:** a separately operated client completes the same authorized journey
through the short endpoint, recovery is demonstrated, and all public claims match
the deployed contract.

## First contribution

Start with stages 1–3 in an isolated checkout. They require no production
credentials or DNS changes. The immediate deliverable is the reviewed host policy,
compatible implementation and synthetic integration evidence. Stages 4–6 are
separate release/operations work, not implied by a passing local test.
