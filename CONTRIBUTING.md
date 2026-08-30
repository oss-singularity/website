# Contributing

Thanks for taking the time to improve the OSS Singularity website. Small, focused pull requests are easiest to review.

## Before opening a pull request

1. Keep the site dependency-free and preserve the authored HTML, CSS, and progressive-enhancement approach.
2. Do not add analytics, cookies, storage, third-party runtime assets, automatic network requests, credentials, private infrastructure details, or account-specific screenshots.
3. Preserve keyboard access, reduced-motion behavior, responsive layouts, semantic structure, and the budgets in `docs/product-requirements.md`.
4. Build and validate the complete production tree:

   ```sh
   ./scripts/check-repository.sh
   ```

5. Describe behavior and visual verification in the pull request. Include public-safe screenshots when a visible change benefits from them.

Generated `dist/` output is intentionally ignored. Change the authored files in `site/`, then let the repository check rebuild and validate the production tree.

## Security

Do not disclose vulnerabilities, credentials, private paths, hosting account details, or complete infrastructure exports in an issue or pull request. Follow [SECURITY.md](SECURITY.md) for private reporting.

## Production boundary

Merging source does not authorize a production deployment. Deployment credentials stay outside the repository, and production changes follow the separately documented review, backup, allowlist, and rollback gates.
