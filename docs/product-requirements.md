# Launch Pad v0 product requirements

Historical v0 brief, selected on 2026-08-30. Current product scope and the explicitly authorized shared-home evolution are defined in [Commons requirements](commons-requirements.md). The original visual identity remains the foundation; this historical single-page scope no longer limits the current product.

## Product outcome

The first OSS Singularity website should replace a generic parking page with a small, memorable home base that answers three questions within one viewport:

1. What is OSS Singularity?
2. What has it built?
3. Where can I inspect or join the work?

The desired impression is technically credible, unmistakably independent, warmly human, and deliberately restrained. The site is a launch pad into maintained open-source projects, not a substitute for their documentation or GitHub history.

## Audiences and journeys

### People looking for a useful Linux tool

- Arrive through a project mention, search result, or shared link.
- Recognize the engineering focus and supported environment quickly.
- Scan a small set of real projects with authentic product imagery.
- Open the relevant GitHub repository for installation, documentation, or releases.

### Open-source developers and potential contributors

- Understand the mission and quality bar from the hero and principles.
- See that the work is public, inspectable, privacy-minded, and actively maintained.
- Move directly to the organization, source, issues, and project documentation on GitHub.

### Supporters and future collaborators

- Meet the human voice behind the work without marketing theatre.
- Understand what continued support would sustain.
- Use the public organization or private contact route. The organization Sponsors destination must not be presented as active until activation is verified.

## Core narrative

1. **Signal:** “Open-source engineering beyond the event horizon” establishes the territory.
2. **Proof:** real tools, real interfaces, and specific outcomes replace abstract claims.
3. **Principles:** user control, privacy, dependable maintenance, and openness explain how the work is made.
4. **Invitation:** “Welcome, fellow traveler” turns a portfolio into an open door.

## Information architecture

The v0 is a single canonical page with stable anchors:

- Header: identity, Work, Principles, GitHub
- Hero: positioning, welcome line, primary and secondary calls to action
- Mission signal: concise statement of the engineering territory
- Featured work: PDrive Control Center, ChatGPT Usage for Cinnamon, Nemo Action Bar, plus a link to all repositories
- Principles: Open by default, Privacy with intent, Built for real desktops
- Invitation: GitHub organization and private email contact
- Footer: concise identity, navigation, security and privacy statements

Project documentation, releases, issue tracking, and installation instructions remain canonical in their GitHub repositories.

## Functional scope

- Responsive one-page experience plus a branded 404 page
- Keyboard-accessible skip link and navigation
- Semantic project links with descriptive accessible names
- Local social-preview, favicon, avatar and product assets
- `robots.txt`, sitemap, canonical metadata, Open Graph and X/Twitter metadata
- Explicit LiteSpeed/Apache headers, caching, compression hints, and error-page policy in a deployable `.htaccess`
- Deterministic allowlisted build into `dist/`
- No search, account, form, CMS, server session, database, analytics, cookie banner, or runtime API in v0

## Accessibility target

- WCAG 2.2 AA for the shipped page and 404 page
- Logical landmarks and heading order
- Visible `:focus-visible` treatment and a bypass link
- Minimum 44×44 CSS-pixel primary interactive targets where practical
- Text contrast of at least 4.5:1 and non-text contrast of at least 3:1
- No information conveyed by color alone
- Motion is decorative, modest, pointer-reactive, and removed for `prefers-reduced-motion: reduce`
- Layout remains usable at 320 CSS pixels, 200% zoom, and with long translated text even though v0 ships in English

## Performance budget

Measured against a clean production build before launch:

| Budget | Target |
| --- | --- |
| Executable JavaScript | under 8 KB, local and dependency-free |
| Initial HTML, uncompressed | under 35 KB |
| CSS, uncompressed | under 40 KB |
| Initial page transfer, excluding social preview | under 350 KB |
| Largest content image | under 180 KB in its delivered format |
| Lighthouse mobile | Performance, Accessibility, Best Practices and SEO each at least 95 |
| Core stability goals | LCP under 2.0 s on a fast 4G profile; CLS under 0.05 |

System fonts, responsive local images, explicit image dimensions, immutable asset caching, and zero client-side framework code support the budget. The optional reactive signal field makes no network requests, stores no state, stops in background tabs, caps display density, and is disabled when reduced motion is requested.

## SEO and social sharing

- Canonical origin is `https://oss-singularity.io/`.
- `www` must not be published as a canonical URL until its TLS coverage is valid; once covered, it redirects permanently to the apex.
- The title and description lead with the mission and open-source engineering outcome.
- The social preview is a local 1200×630 PNG derived from the canonical vector avatar.
- Search indexing is allowed for the canonical page and excluded for the 404 page.
- The sitemap contains only real canonical pages.

## Privacy and security decisions

- No analytics, tracking pixels, cookies, fingerprinting, third-party fonts, embeds, CDN runtime dependencies, or automatic GitHub requests.
- Outbound GitHub and email actions occur only after a user activates a link.
- The production response should set a restrictive Content Security Policy, Referrer Policy, Permissions Policy, anti-framing protection, MIME sniffing protection, and an intentional HSTS policy after both canonical hosts have valid TLS.
- Mail, DNS, `.well-known`, cPanel metadata, and unrelated hosting data are outside the website build and must be preserved during deployment.

## Technology decision

Use authored HTML, CSS and one small progressive-enhancement script with a POSIX-shell build that copies an explicit allowlist into `dist/` and verifies the required output. There are no package-manager dependencies.

This remains the simplest fit because the content is editorial, the primary interactions are links and anchors, and the verified default Stellar shell exposes no Node/npm runtime. A small local Canvas enhancement adds visual reactivity without a framework, runtime service, tracking or third-party request. PHP 8.2 is available but adds no value. The static output remains reproducible locally and in GitHub Actions, cheap to cache, easy to stage exactly, and easy to roll back.

Reconsider a static-site generator only when repeated content, localization, or a larger editorial surface makes hand-authored pages demonstrably harder to maintain. Reconsider a server runtime only for a concrete server-side requirement.

## Launch acceptance

- Clean build from a clean checkout
- Repository checks and static-site checks green locally and in CI
- Keyboard, reduced-motion, responsive, contrast, link, metadata, and 404 review complete
- Performance budget met
- Final local v0 shown to the owner
- Fresh production inventory compared with the verified backup immediately before deployment
- Explicit owner approval immediately before replacing the parking page
- Only declared `dist/` output staged; rollback tree preserved
- Live apex, assets, canonical behavior, headers, caching, compression, error page, certificate names, and server logs verified after deployment
