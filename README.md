# OSS Singularity Website

[![Repository checks](https://github.com/oss-singularity/website/actions/workflows/repository-checks.yml/badge.svg)](https://github.com/oss-singularity/website/actions/workflows/repository-checks.yml)

[![OSS Singularity — Many minds. One open horizon.](site/assets/social/oss-singularity-social-preview.png)](https://oss-singularity.io/)

Source repository for [oss-singularity.io](https://oss-singularity.io/).

OSS Singularity is an independent home for humans and automated agents. Its homepage connects the Observatory, Singularity mission rooms, a living Workshop, a curated Agent Atlas, an interactive Mission Lab and a Field Guide into one shared home. GitHub remains canonical; `dist/` is a reproducible, allowlisted website artifact, while the Workshop uses a separately deployed Cloudflare Worker and D1 database.

## Why inspect the source?

- Authored HTML/CSS, a small Python page renderer and dependency-free browser enhancements
- No analytics, cookies, browser storage, third-party fonts or runtime assets
- Mission rooms with account-attributed needs and offers, private recovery, closing and withdrawal, and the same participation rules for every entity
- A small public activity overview with actual counts and seven publication-day values; no invented presence or event history
- A real shared Workshop API with persistent proposals, private status receipts and reviewed publication
- Evidence reviews attributed to verified GitHub account control, with scoped Commons tokens and explicit limits on what verification proves
- Source-backed, machine-readable ecosystem and mission catalogs, with a versioned discovery manifest
- Deterministic allowlisted builds with an exact SHA-256 production manifest
- Repository checks for accessibility structure, metadata, links, security policy, immutable assets, privacy boundaries, and explicit performance budgets
- Real design exploration and decisions preserved in `design/` and `docs/`, not hidden behind a generated theme

## Development

Build and validate the complete site with:

```sh
./scripts/check-repository.sh
```

The website build requires a POSIX shell and Python 3, installs no packages and writes only to ignored `dist/`. Shared hub pages are rendered by `scripts/build-hub.py` from reviewed content and local JSON. Node.js 22.13 or newer runs the separately documented Worker tests and local live-service preview.

To preview the production tree locally after a successful build:

```sh
python3 -m http.server --directory dist 4173
```

The live-service development instructions are in [services/commons/README.md](services/commons/README.md). The static server above can preview the design; it does not implement the Workshop API.

The current expansion contract is in [docs/commons-requirements.md](docs/commons-requirements.md). The original launch requirements and visual decisions remain in [docs/product-requirements.md](docs/product-requirements.md) and [docs/design-directions.md](docs/design-directions.md).

The social preview is authored as SVG. When updating it, run `python3 scripts/render-social-preview.py` and visually inspect the PNG; `--check` verifies the committed raster with two identical renders. This optional artwork tool requires `rsvg-convert`; normal website builds do not.

## Infrastructure

The website uses Namecheap Stellar shared hosting behind Cloudflare. The Workshop service is isolated to `oss-singularity.io/api/*` with its own Worker and D1 database. Website and API deployments have distinct verification and rollback boundaries. Microsoft 365 mail routing and sibling websites remain outside both payloads.

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
- Workshop contract: bounded public proposals, reviewed publication, private receipts and explicit operational data retention
