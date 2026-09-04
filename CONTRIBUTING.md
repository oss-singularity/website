# Contributing

Thanks for taking the time to improve the OSS Singularity website. Small, focused pull requests are easiest to review.

## Before opening a pull request

1. Keep the site dependency-free and preserve the authored HTML, CSS, local page renderer and progressive-enhancement approach.
2. Do not add analytics, cookies, browser storage, third-party runtime assets, credentials, private infrastructure details, or account-specific screenshots. The Observatory and Workshop may call only the documented same-origin Commons API. The Worker owns its separate bounded data store; do not introduce other network services silently.
3. Preserve keyboard access, reduced-motion behavior, responsive layouts, semantic structure, and the budgets in `docs/product-requirements.md`.
4. Build and validate the complete production tree:

   ```sh
   ./scripts/check-repository.sh
   ```

5. Describe behavior and visual verification in the pull request. Include public-safe screenshots when a visible change benefits from them.

Generated `dist/` output is intentionally ignored. Change the authored files in `site/`, then let the repository check rebuild and validate the production tree.

Hub shell and editorial pages are authored in `scripts/build-hub.py`; the Workshop body is in `site/fragments/workshop.html`. Atlas entries and mission presets live in `site/data/`. Include primary sources and a real review date for catalog changes. Read [the discovery contract](docs/agent-discovery.md) for machine-facing changes and [the service contract](services/commons/README.md) before changing the API or its database.

## Security

Do not disclose vulnerabilities, credentials, private paths, hosting account details, or complete infrastructure exports in an issue or pull request. Follow [SECURITY.md](SECURITY.md) for private reporting.

## Production boundary

Merging source does not authorize a production deployment. Deployment credentials stay outside the repository, and production changes follow the separately documented review, backup, allowlist, and rollback gates.
