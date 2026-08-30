# OSS Singularity Website

Source repository for [oss-singularity.io](https://oss-singularity.io/).

The site itself is in the infrastructure and design preparation phase. Existing OSS Singularity identity and messaging provide real brand inputs, while the final visual system and website experience remain open. Framework selection will follow the content, interaction, accessibility, performance, and hosting requirements instead of constraining them up front.

## Infrastructure

Production currently uses Namecheap Stellar shared hosting with cPanel, LiteSpeed, HTTPS, and Microsoft 365 mail routing. GitHub is the canonical source of truth; the intended delivery path is a tested Git deployment over SSH/cPanel UAPI rather than manual file-manager or FTP uploads.

See [docs/hosting.md](docs/hosting.md) for the verified baseline, safety boundaries, and acceptance gates.

See [docs/brand-inputs.md](docs/brand-inputs.md) for the verified identity and messaging sources that will inform the design process.

## Status

- Hosting access baseline: verified
- Repository security baseline: verified
- Existing brand inputs: inventoried; website visual system not selected yet
- Requirements, website architecture, and technology stack: not selected yet
- Production deployment: not connected yet

Security-sensitive findings should be reported privately as described in [SECURITY.md](SECURITY.md).
