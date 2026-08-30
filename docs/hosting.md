# Hosting baseline

Verified on 2026-08-30. This document contains no credentials, account username, private paths, API tokens, keys, cookies, or cPanel session URLs.

## Current public state

| Area | Verified state |
| --- | --- |
| Canonical URL | `https://oss-singularity.io/` |
| Current page | Namecheap parking page, HTTP 200 |
| Hosting | Namecheap Stellar shared hosting |
| Web server | LiteSpeed |
| DNS authority | `dns1.namecheaphosting.com`, `dns2.namecheaphosting.com` |
| Apex address | Stellar shared-hosting address |
| `www` | CNAME to the apex domain |
| Mail | Microsoft 365 MX and SPF; separate from website deployment |
| TLS | Valid certificate for the apex domain |

The current certificate does not advertise `www.oss-singularity.io` in the observed SAN list, and a verified HTTPS request to `www` fails hostname validation. Treat a canonical `www` redirect and certificate coverage as a production acceptance gate before publishing links that use `www`.

The parking-page response does not currently emit common browser security headers such as HSTS or a Content Security Policy. Microsoft 365 DKIM selectors resolve publicly, while no DMARC TXT record was observed. Recheck all of these against the authoritative DNS and final application response before launch; website deployment must not rewrite the working mail records.

## Available platform capabilities

The exported cPanel tools confirm SSH/SFTP, API tokens, Git Version Control, Zone Editor, cron, backups, TLS management, ModSecurity, Imunify360, MariaDB, PHP, and managed Node.js/Python/Ruby application runtimes.

Shared hosting uses jailed shell access and Namecheap's nonstandard SSH port. It is suitable for a static build and may support a Passenger-hosted application, but framework selection must wait until runtime versions, resource ceilings, process behavior, and document-root constraints are verified live.

## Recommended source and deployment model

1. GitHub `oss-singularity/website` is canonical.
2. CI validates a reproducible build and creates the deployable output.
3. A separate cPanel-managed repository receives a reviewed commit.
4. A checked-in `.cpanel.yml` deploys only the declared build output into the exact document root.
5. Production verification checks commit/content identity, HTTPS, redirects, representative pages/assets, security headers, and server error logs.
6. Rollback restores the previous staged output or known commit.

Do not place the cPanel repository itself in the live document root and do not deploy with a wildcard that can copy `.git`, source-only files, secrets, or build caches.

## Credential bootstrap

The intended local connection uses:

- one dedicated SSH key and host alias for Stellar SSH/SFTP/Git;
- one separately named, expiring cPanel API token stored outside the repository;
- cPanel UAPI over verified HTTPS for inventory and narrowly targeted operations.

cPanel API tokens are full-access within the account's enabled features. Creating the key/token pair and the first provider backup therefore requires an explicit, observable bootstrap step before production automation is enabled.

## DNS and mail boundary

Because the domain uses Namecheap Web Hosting DNS, DNS records are managed by cPanel rather than Namecheap Advanced DNS/API. Any DNS change must begin with a complete zone snapshot and preserve Microsoft 365 MX, SPF, verification, autodiscover, DKIM, and DMARC records.

## Hosting decision

Keep Stellar/cPanel as the initial production target. It is already live, exposes the native CLI/API/Git capabilities required for a pleasant workflow, and leaves room for both static and managed application deployments. Reconsider GitHub Pages only if the final site is purely static and its simpler CDN/Actions model materially outweighs losing the existing cPanel runtime and deployment surface.

## Pre-launch gates

- Dedicated SSH access works with a pinned host key and no shared private key.
- A current hosting backup exists and its scope is known.
- cPanel UAPI read-only inventory succeeds without exposing the token.
- The exact document root and current contents are backed up and hashed.
- The build is reproducible in a clean environment.
- Apex and `www` have an intentional redirect/canonical policy and valid TLS coverage.
- Security headers, caching, compression, error pages, and `robots.txt` are intentional.
- The mail owner has made an explicit DMARC policy decision.
- Mail DNS remains unchanged and mail flow is unaffected.
- Deployment and rollback are both tested before the parking page is replaced.
