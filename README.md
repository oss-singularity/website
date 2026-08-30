# OSS Singularity Website

Source repository for [oss-singularity.io](https://oss-singularity.io/).

The site itself is in the infrastructure and design preparation phase. Framework selection will follow the content, interaction, accessibility, performance, and hosting requirements instead of constraining them up front.

## Infrastructure

Production currently uses Namecheap Stellar shared hosting with cPanel, LiteSpeed, HTTPS, and Microsoft 365 mail routing. GitHub is the canonical source of truth; the intended delivery path is a tested Git deployment over SSH/cPanel UAPI rather than manual file-manager or FTP uploads.

See [docs/hosting.md](docs/hosting.md) for the verified baseline, safety boundaries, and acceptance gates.

## Status

- Repository and hosting baseline: in preparation
- Website architecture and visual system: not selected yet
- Production deployment: not connected yet

Security-sensitive findings should be reported privately as described in [SECURITY.md](SECURITY.md).
