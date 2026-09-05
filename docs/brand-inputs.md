# Brand inputs

Verified on 2026-08-30. This inventory records existing public identity and messaging sources without turning them into a final website design system.

## Existing identity

| Input | Current source |
| --- | --- |
| Name | OSS Singularity |
| Positioning | Open-source engineering beyond the event horizon — tools, automation & experiments built for what comes next. ❤️‍🔥 |
| Welcome line | 🫰🌈 Welcome, fellow traveler! 💎 |
| Visual anchor | Current OSS Singularity GitHub organization avatar |
| Public organization profile | [github.com/oss-singularity](https://github.com/oss-singularity) |
| Contact | `mail@oss-singularity.io` |

The organization profile, avatar, pinned projects, project screenshots, and existing repository presentation are source material for the website's visual language.

The highest-quality avatar source was located in the canonical `oss-singularity/oss-singularity` repository at `docs/assets/OSS-Singularity_GitHub_Avatar/OSS-Singularity_GitHub_Avatar_Source.svg`. It is a 2048×2048 vector with SHA-256 `8b121db6fb5aa138509ef9876d221270f83c1b21dbd2218465a1811a4901e417`; the public GitHub avatar is only a 460×460 derivative. The website carries a semantically annotated copy of that vector and derives its social preview from the same geometry.

## Sponsors messaging

An OSS Singularity GitHub Sponsors listing exists in preview and provides a substantial messaging foundation around thoughtful open-source software, privacy-minded tools, user control, dependable maintenance, and software that remains open and freely available.

The organization Sponsors listing is not active yet. Until activation is verified, `.github/FUNDING.yml` intentionally keeps its current working recipient and the website must not present the organization listing as a live funding destination.

## Decisions for Launch Pad v0

- Primary audiences, journeys, content hierarchy, calls to action and the relationship to GitHub are specified in `docs/product-requirements.md`.
- “Signal Observatory” is the selected visual direction; the comparison and source prototypes are in `docs/design-directions.md` and `design/prototypes/`.
- The canonical mark keeps its cyan/magenta event-horizon geometry. The website uses a deep observatory palette, system typography, restrained motion, and authentic project screenshots.
- The launch target is WCAG 2.2 AA with explicit performance and privacy budgets. The only executable client-side JavaScript is a small, dependency-free decorative Canvas enhancement; it performs no analytics, tracking, storage, or third-party runtime requests and is removed for reduced-motion users.
- GitHub remains canonical for repositories, releases, documentation and issues. The organization Sponsors destination remains absent until activation is verified.

These decisions belong to the Requirements, Design Direction, and Tech Stack phase. Existing sources constrain authenticity, not creativity.

## Shared home language — 2026-09-05

- **Engineering beyond the event horizon.** remains the opening engineering
  statement. It connects the original identity to the work still ahead.
- **Many minds. One open horizon.** is the shared-home invitation used by the
  social preview. Its magenta-to-cyan emphasis includes both **open horizon**
  words; the original mark and lettering remain intact.
- **Engineering for the open horizon.** is a complementary line for places
  where discovery becomes shared work, such as the Workshop and missions. It
  is available to use where it fits, without repeating it throughout the site.
- The home welcomes every entity. Participation and shared benefit include
  the contributors themselves; openness does not require unpaid work or a
  particular provider, license or business model.
