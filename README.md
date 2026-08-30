# OSS Singularity Website

Source repository for [oss-singularity.io](https://oss-singularity.io/).

Launch Pad v0 is a dependency-free static site shaped around the “Signal Observatory” visual direction: a precise cosmic shell, authentic project interfaces, and an intentionally human open-source voice. GitHub remains canonical; `dist/` is a reproducible, allowlisted production artifact.

## Development

Build and validate the complete site with:

```sh
./scripts/check-repository.sh
```

The build uses authored HTML and CSS plus local optimized assets. It requires a POSIX shell and Python 3 for validation, installs no packages, executes no client-side JavaScript, and writes only to ignored `dist/`.

To preview the production tree locally after a successful build:

```sh
python3 -m http.server --directory dist 4173
```

Product requirements and the launch gates are recorded in [docs/product-requirements.md](docs/product-requirements.md). The three visual prototypes, comparison, and selected direction are recorded in [docs/design-directions.md](docs/design-directions.md).

## Infrastructure

Production currently uses Namecheap Stellar shared hosting with cPanel, LiteSpeed, HTTPS, and Microsoft 365 mail routing. The first launch uses the verified local SSH path and transfers only the checked `dist/` tree after explicit approval. A serialized GitHub Actions SSH push is the intended later automation path.

See [docs/hosting.md](docs/hosting.md) for the verified baseline, safety boundaries, and acceptance gates.

See [docs/brand-inputs.md](docs/brand-inputs.md) for the verified identity and messaging sources that will inform the design process.

## Status

- Hosting access baseline: verified
- Repository security baseline: verified
- Existing brand inputs: inventoried; canonical vector avatar source located and preserved
- Requirements, architecture, visual direction, and static technology stack: selected and documented
- Launch Pad v0: implemented; local acceptance and production approval pending
- Production deployment: not connected yet

Security-sensitive findings should be reported privately as described in [SECURITY.md](SECURITY.md).
