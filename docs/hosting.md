# Hosting baseline

Current topology, TLS and redirect behavior verified on 2026-09-05. The Cloudflare cache-safety addendum below was verified on 2026-09-04. This document contains no credentials, account username, private paths, API tokens, keys, cookies, or cPanel session URLs.

## Cloudflare edge-cache safety (2026-09-04)

The `oss-singularity.io` Cloudflare zone has the enabled response-stage Cache Rule `Upstream no-cache response guard` (`ref: upstream_no_cache_response_guard`, Cloudflare ruleset ID `22b1cb235f094e469a16ec51bcfe7c34`, rule ID `1a5b282700224ebdb07ec090c301535a`). It matches:

```text
any(http.response.headers["cf-edge-cache"][*] == "no-cache")
```

For matching origin responses it sets Cloudflare-only `no-store`. This prevents a Namecheap/Imunify360 verification response carrying `CF-Edge-Cache: no-cache` from being stored at the edge and served as the website to later visitors. It does not disable or configure Imunify360 at Namecheap; it only protects Cloudflare's cache. Ordinary website responses remain eligible for the normal cache policy. After creation, only this zone's cache was purged; live verification showed the normal page and normal `MISS` to `HIT` caching behavior. Revalidate this guard if Namecheap changes the header name or semantics.

## Current public state

| Area | Verified state |
| --- | --- |
| Canonical URL | `https://oss-singularity.io/` |
| Current site | OSS Singularity shared home and Commons pages |
| Static origin | Namecheap Stellar shared hosting; separate addon-domain document root |
| Web server | LiteSpeed |
| DNS authority and edge | Cloudflare Free |
| `www.oss-singularity.io` | HTTPS-capable permanent redirect to the canonical apex |
| Mail | Microsoft 365 MX and SPF; separate from website deployment |
| TLS | Cloudflare Full (strict); valid edge and origin coverage for apex and `www` |
| Commons API | Separate Cloudflare Worker and D1 database on `oss-singularity.io/api/*` |
| Additional redirect hosts | `oss-singularity.com`, `oss-singularity.de` and `www.oss-singularity.de` on Netcup |

The canonical origin is always `https://oss-singularity.io/`. Canonical, Open Graph, social-preview, sitemap, robots, and internal links all use the apex URL; `www` is never published as a content URL. Its only intended function is to redirect a manually entered `www` URL permanently to the equivalent apex path.

The earlier `www` certificate-issuance blocker is resolved. Both the Cloudflare edge and the Namecheap origin have valid TLS coverage for the apex and `www`. HTTP and HTTPS requests to `www.oss-singularity.io` redirect permanently to the equivalent HTTPS apex path and query.

The separate Netcup hosts `oss-singularity.com`, `oss-singularity.de` and `www.oss-singularity.de` return HTTP 301 redirects to the same canonical HTTPS destination, preserving the path and query on both HTTP and HTTPS requests. The `.de` hosts were verified over IPv4 and IPv6, with Let's Encrypt automatic renewal enabled. Their certificate-validation paths remain local for renewal, and PHP, CGI, FastCGI and SSI are disabled. Static and API releases do not deploy to these redirect hosts.

The live response emits the intended HSTS, Content Security Policy, browser security, cache, and compression headers. Microsoft 365 mail routing was separately configured and validated by the owner. DMARC is an email-policy concern rather than a website-development gate; this repository makes no mail-policy decision. Preserve all working Microsoft 365 records including any DKIM or DMARC records.

Cloudflare is authoritative for `oss-singularity.io`. Preserve the complete current zone and the separate provider configuration rather than reconstructing them from a partial public DNS lookup or an old record count.

## Verified access baseline

- A dedicated encrypted Ed25519 key, isolated SSH alias, and pinned ED25519 host key are in use.
- Key-only SSH succeeds with password and keyboard-interactive authentication disabled.
- A separately named, non-expiring cPanel API token was created by explicit owner choice and is stored only in the local Secret Service keyring.
- HTTPS UAPI succeeds from the keyring without exposing the token.
- The isolated addon-domain document-root mapping was verified against the authenticated account; sibling document roots remain outside the deployment scope.
- Timestamped backups and release manifests are kept outside the repository. Each release verifies its own backup hashes, safe paths and rollback target rather than relying on a fixed historical file count.
- This site does not deploy through a cPanel Git mirror.

Namecheap's jailed shell exposes `/usr/local/cpanel/bin/uapi`, but the command cannot execute because `/usr/local/cpanel/cpanel` is outside the provider-controlled jail. The account cannot safely repair that server-level boundary. The operational fix is the verified local HTTPS-UAPI helper; it avoids storing the cPanel token on the hosting server.

## GitHub controls and deployment access

The source repository is published for transparent inspection and contribution. Repository policy keeps squash-only merges, branch cleanup, selected Actions, read-only workflow permissions, automated dependency updates, private vulnerability reporting, secret scanning with push protection, CodeQL, and a protected linear `main` branch with required repository checks. Public source does not widen the production credential or deployment boundary.

The OSS Singularity organization disables deploy keys across its repositories. A repo-specific cPanel deploy key was therefore not retained, no broader personal access token or account key was installed on the shared host, and no cPanel mirror was created. Authorized static releases use the verified operator SSH path after the protected merge and required checks. GitHub Actions validates source changes; automatic production deployment is not currently configured. Any future automation must transfer only the declared build output, serialize production deployments, verify the live result, and retain a rollback target. A repository-scoped GitHub App is reserved for a future requirement that genuinely needs server-initiated pulls; do not add its token-rotation and private-key machinery without that need.

## Available platform capabilities

The exported cPanel tools confirm SSH/SFTP, API tokens, Git Version Control, Zone Editor, cron, backups, TLS management, ModSecurity, Imunify360, MariaDB, PHP, and managed Node.js/Python/Ruby application runtimes.

Shared hosting uses jailed shell access and Namecheap's nonstandard SSH port. The static frontend has no origin-runtime requirement: it is built and tested locally or in CI and deployed as static output. The dynamic Commons API runs separately on Cloudflare Workers and D1, not on a shared-hosting application runtime.

## Recommended source and deployment model

1. GitHub `oss-singularity/website` is canonical.
2. CI validates a reproducible, dependency-free build and its exact file allowlist.
3. Authorized static releases stage only the approved `dist/` output over the verified SSH path to the isolated addon-domain document root.
4. Each release retains a verified rollback target and preserves provider-managed overlays, `.well-known` content outside the build allowlist, `cgi-bin`, sibling websites and unrelated hosting data.
5. Production verification compares the approved artifact at origin and edge and checks TLS, redirects, pages/assets, security headers, caching, compression, the 404 response and provider-state preservation.
6. Worker and D1 releases have separate authorization, verification and rollback boundaries; a static release does not migrate or replace the Commons database.

Do not deploy with a wildcard that can copy `.git`, source-only files, secrets, prototypes, or build caches. A cPanel-managed mirror is not part of the selected push model.

## Credential boundary

The access bootstrap is complete, but cPanel API tokens remain full-access within the account's enabled features. The token value, SSH private key, passphrase, cPanel sessions, cookies, account username, and private hosting paths must never enter this repository, CI variables, documentation, screenshots, or chat output. Any new credential, token revocation, or account mutation requires fresh explicit authorization.

## DNS and mail boundary

The authoritative `oss-singularity.io` DNS zone is managed at Cloudflare. Netcup manages the separate `.com` and `.de` redirect-host configuration. A website release does not imply permission to alter DNS or mail. Any authorized DNS change must begin with a complete snapshot of the affected zone and preserve Microsoft 365 MX, SPF, verification, autodiscover, DKIM and DMARC records, together with all unrelated records.

## Hosting decision

Keep Stellar/cPanel as the static origin behind Cloudflare, with Workers and D1 providing the separate Commons service. Reconsider the infrastructure when measured product needs justify a change, preserving the independent static, API, database, DNS and mail boundaries.

## Production verification and follow-up

- The shared home and Commons pages are live from the checked source commit; deployed origin and edge files match the approved build, with intentional provider-managed responses checked separately.
- HTTPS apex and `www`, homepage, direct index, assets, the social image, custom 404, security headers, caching, Brotli and gzip were verified live.
- A TelegramBot user-agent receives HTTP 200, the exact production HTML, apex-only Open Graph/Twitter metadata, and the 1200×630 social-preview URL.
- Each static release retains a hash-verified rollback target and separate deployment evidence.
- `.well-known`, `cgi-bin`, mail DNS, TLS configuration, and unrelated hosting data remain preserved.
- The `.com` apex and `.de` apex plus `www` redirects are verified over HTTP and HTTPS. Keep certificate renewal and redirect-path checks in future hosting verification; the earlier `www.oss-singularity.io` TLS blocker is closed.
