# OSS Singularity Website

[![Repository checks](https://github.com/oss-singularity/website/actions/workflows/repository-checks.yml/badge.svg)](https://github.com/oss-singularity/website/actions/workflows/repository-checks.yml)

Source repository for [oss-singularity.io](https://oss-singularity.io/).

Launch Pad is a dependency-free static site shaped around the “Signal Observatory” visual direction: a precise cosmic shell, an adaptive pointer-reactive signal field, authentic project interfaces, and an intentionally human open-source voice. GitHub remains canonical; `dist/` is a reproducible, allowlisted production artifact.

## Why inspect the source?

- Authored HTML and CSS with one small, dependency-free Canvas enhancement
- No framework, package install, analytics, cookies, storage, third-party runtime assets, or automatic network requests
- Deterministic allowlisted builds with an exact SHA-256 production manifest
- Repository checks for accessibility structure, metadata, links, security policy, immutable assets, privacy boundaries, and explicit performance budgets
- Real design exploration and decisions preserved in `design/` and `docs/`, not hidden behind a generated theme

## Development

Build and validate the complete site with:

```sh
./scripts/check-repository.sh
```

The build uses authored HTML and CSS, one small dependency-free Canvas enhancement, and local optimized assets. It requires a POSIX shell and Python 3 for validation, installs no packages, makes no runtime network requests, and writes only to ignored `dist/`.

To preview the production tree locally after a successful build:

```sh
python3 -m http.server --directory dist 4173
```

Product requirements and the launch gates are recorded in [docs/product-requirements.md](docs/product-requirements.md). The three visual prototypes, comparison, and selected direction are recorded in [docs/design-directions.md](docs/design-directions.md).

## Infrastructure

Production uses Namecheap Stellar shared hosting with cPanel, LiteSpeed, HTTPS, and Microsoft 365 mail routing. Launch Pad v0 was deployed through the verified local SSH path from the checked `dist/` tree. A serialized GitHub Actions SSH push is the intended later automation path.

See [docs/hosting.md](docs/hosting.md) for the verified baseline, safety boundaries, and acceptance gates.

See [docs/brand-inputs.md](docs/brand-inputs.md) for the verified identity and messaging sources that will inform the design process.

## Contributing

Thoughtful fixes and improvements are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the repository boundaries and local verification command. Please report security-sensitive findings privately as described in [SECURITY.md](SECURITY.md).

## License

Source code and technical documentation are available under the [MIT License](LICENSE). The OSS Singularity identity and visual assets are excluded as described in [BRANDING.md](BRANDING.md).

## Status

- Hosting access baseline: verified
- Repository security baseline: verified; public-repository protections tracked separately from source checks
- Existing brand inputs: inventoried; canonical vector avatar source located and preserved
- Requirements, architecture, visual direction, and static technology stack: selected and documented
- Launch Pad v0: live and production-verified at [oss-singularity.io](https://oss-singularity.io/)
- Canonical host: apex only; no published URL uses `www`
- Open hosting follow-up: reissue TLS with the `www` SAN after Namecheap/SSL.com issuance error `1010` clears, then verify the redirect-only alias
