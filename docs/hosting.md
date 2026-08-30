# Hosting baseline

Verified on 2026-08-30. This document contains no credentials, account username, private paths, API tokens, keys, cookies, or cPanel session URLs.

## Current public state

| Area | Verified state |
| --- | --- |
| Canonical URL | `https://oss-singularity.io/` |
| Current page | OSS Singularity Launch Pad v0, HTTP 200 |
| Hosting | Namecheap Stellar shared hosting |
| Web server | LiteSpeed |
| DNS authority | `dns1.namecheaphosting.com`, `dns2.namecheaphosting.com` |
| Apex address | Stellar shared-hosting address |
| `www` | CNAME to the apex domain |
| Mail | Microsoft 365 MX and SPF; separate from website deployment |
| TLS | Valid certificate for the apex domain; `www` follow-up open |

The canonical origin is always `https://oss-singularity.io/`. Canonical, Open Graph, social-preview, sitemap, robots, and internal links all use the apex URL; `www` is never published as a content URL. Its only intended function is to redirect a manually entered `www` URL permanently to the equivalent apex path.

The installed certificate does not advertise `www.oss-singularity.io` in its SAN list, so an HTTPS request to `www` currently fails hostname validation before the application redirect can run. A 2026-08-30 reissue attempt failed with Namecheap error `1010` while its dashboard reported an SSL.com certificate-issuance incident. The owner explicitly accepted an apex-only launch while the provider is blocked. Reissue with both apex and `www` coverage when issuance recovers, verify both SANs, then verify `https://www.oss-singularity.io/<path>` returns a permanent redirect to `https://oss-singularity.io/<path>`. Do not remove the `www` CNAME and do not switch nameservers merely to use a registrar URL-forwarding service.

The live response emits the intended HSTS, Content Security Policy, browser security, cache, and compression headers. Microsoft 365 mail routing was separately configured and validated by the owner. DMARC is an email-policy concern rather than a website-development gate; this repository makes no mail-policy decision. Preserve all working Microsoft 365 records including any DKIM or DMARC records.

The authoritative cPanel zone contained 22 records during the final authenticated read-only audit. Preserve the complete zone rather than reconstructing only the publicly observed records.

## Verified access baseline

- A dedicated encrypted Ed25519 key, isolated SSH alias, and pinned ED25519 host key are in use.
- Key-only SSH succeeds with password and keyboard-interactive authentication disabled.
- A separately named, non-expiring cPanel API token was created by explicit owner choice and is stored only in the local Secret Service keyring.
- HTTPS UAPI succeeds from the keyring without exposing the token.
- The primary domain and its document-root mapping were verified against the authenticated account.
- A timestamped local backup of the complete current document root exists outside the repository. Its archive hash, manifest hash, safe paths, and all 45 source files were verified before the temporary server-side copy was removed.
- The cPanel account currently has no Git Version Control repositories and no deployment connection to the document root.

Namecheap's jailed shell exposes `/usr/local/cpanel/bin/uapi`, but the command cannot execute because `/usr/local/cpanel/cpanel` is outside the provider-controlled jail. The account cannot safely repair that server-level boundary. The operational fix is the verified local HTTPS-UAPI helper; it avoids storing the cPanel token on the hosting server.

## GitHub controls and deployment access

The source repository remains private by explicit owner choice. The available private-repository controls are accepted for this project: squash-only merges, branch cleanup, selected Actions, read-only workflow permissions, Dependabot alerts, and automated security updates.

The OSS Singularity organization currently disables deploy keys across its repositories. A repo-specific cPanel deploy key was therefore not retained, no broader personal access token or account key was installed on the shared host, and no cPanel mirror was created. The preferred low-complexity path is an initial reviewed release over the already verified local SSH connection, followed by a GitHub Actions push deployment after successful changes to `main`. The workflow must transfer only the declared build output, serialize production deployments, verify the live result, and retain a rollback target. A repository-scoped GitHub App is reserved for a future requirement that genuinely needs server-initiated pulls; do not add its token-rotation and private-key machinery without that need.

## Available platform capabilities

The exported cPanel tools confirm SSH/SFTP, API tokens, Git Version Control, Zone Editor, cron, backups, TLS management, ModSecurity, Imunify360, MariaDB, PHP, and managed Node.js/Python/Ruby application runtimes.

Shared hosting uses jailed shell access and Namecheap's nonstandard SSH port. A fresh 2026-08-30 read-only check found no Node or npm in the default shell; Python 3.6 and Ruby 2.5 are legacy versions, while PHP 8.2 is available. The selected v0 has no server-runtime requirement: it is built and tested locally or in CI and deployed as static output.

## Recommended source and deployment model

1. GitHub `oss-singularity/website` is canonical.
2. CI validates a reproducible, dependency-free build and its exact file allowlist.
3. The first launch staged only the checked `dist/` output over the verified local SSH path after explicit owner approval.
4. The previous production tree remains available as a verified remote rollback archive; `.well-known`, `cgi-bin`, and unrelated hosting data were preserved outside replacement scope.
5. Production verification checks content identity, HTTPS, redirects, representative pages/assets, security headers, caching, compression, the 404 response, and server error logs.
6. After the first launch is proven, a serialized GitHub Actions SSH push may automate the same checked-output transfer and verification contract.

Do not deploy with a wildcard that can copy `.git`, source-only files, secrets, prototypes, or build caches. A cPanel-managed mirror is not part of the selected push model.

## Credential boundary

The access bootstrap is complete, but cPanel API tokens remain full-access within the account's enabled features. The token value, SSH private key, passphrase, cPanel sessions, cookies, account username, and private hosting paths must never enter this repository, CI variables, documentation, screenshots, or chat output. Any new credential, token revocation, or account mutation requires fresh explicit authorization.

## DNS and mail boundary

Because the domain uses Namecheap Web Hosting DNS, DNS records are managed by cPanel rather than Namecheap Advanced DNS/API. Any DNS change must begin with a complete zone snapshot and preserve Microsoft 365 MX, SPF, verification, autodiscover, DKIM, and DMARC records.

## Hosting decision

Keep Stellar/cPanel as the initial production target. It is already live, exposes the native CLI/API/Git capabilities required for a pleasant workflow, and leaves room for both static and managed application deployments. Reconsider GitHub Pages only if the final site is purely static and its simpler CDN/Actions model materially outweighs losing the existing cPanel runtime and deployment surface.

## Production verification and follow-up

- Launch Pad v0 is live from the checked source commit and the deployed build files match its SHA-256 manifest.
- HTTPS apex, homepage, direct index, representative assets, the social image, custom 404, security headers, immutable asset caching, Brotli, and gzip were verified live.
- A TelegramBot user-agent receives HTTP 200, the exact production HTML, apex-only Open Graph/Twitter metadata, and the 1200×630 social-preview URL.
- The previous parking tree is retained in a hash-verified remote rollback archive; the complete local backup remains separately verified.
- `.well-known`, `cgi-bin`, mail DNS, TLS configuration, and unrelated hosting data remain preserved.
- **Next hosting task:** retry the Namecheap SSL reissue after incident/error `1010` clears, require apex plus `www` SANs, and verify the redirect-only `www` path without changing the canonical apex URL.
