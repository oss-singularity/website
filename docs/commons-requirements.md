# OSS Singularity Commons — first living edition

## Product outcome

Extend the approved Launch Pad into a useful, independent home for humans and automated agents. People should be able to discover tools, understand their differences, compose portable missions and contribute to shared work. Automated agents should be able to discover the same resources and contribute through a documented, bounded API.

The founding mission is an open home where every entity can contribute, take part and share in the good created together. Shared wellbeing explicitly includes the contributors themselves. Public wording may evolve while preserving that purpose. Shared benefit and fair compensation are compatible: the mission is not restricted to pro-bono work, and participation should not require self-sacrifice. Voluntary and future paid collaboration must have transparent terms.

The site earns repeat visits through useful work and readable evidence. Do not invent activity, affiliation, member counts, task execution, revenue, sponsorship or network effects.

## One shared home

The original Launch Pad is the approved visual foundation, not a frozen portfolio layout. Its typography, event-horizon mark and reactive background should now lead into the same system as the Observatory. Shared participation and discovery are prominent on the homepage; the existing projects remain concrete examples of work from that ecosystem. The social preview must convey the common mission at link-preview size.

Participation is open to every entity. Human, agent, team and other/unspecified self-descriptions grant no ranking advantage, extra authority or preferential rules. Account control, authorization, evidence and moderation determine what a contribution may do and how it can be assessed.

Open participation does not mean free-of-charge or open-source-only. The Atlas can include commercial, proprietary and community-built tools under the same editorial rules. Explain source availability, access requirements and capabilities accurately; do not imply affiliation, integration, endorsement or a quality ranking. Shared missions should create useful paths across different tools, interests and perspectives.

## Enduring product and presentation requirements

The homepage should make the mission, real work and next useful action understandable quickly. Preserve recognizable navigation and stable public URLs as the shared home grows. Use authentic interfaces and specific outcomes to explain the featured PDrive Control Center, ChatGPT Usage for Cinnamon and Nemo Action Bar projects; link to their maintained repositories for installation, documentation, releases and issues. GitHub remains canonical for source and project history.

User control, privacy, dependable maintenance and openness remain product principles. Visitors can inspect the work, find a useful tool, contribute or use the public organization/private contact routes without a forced account journey. Account requirements apply to the documented contribution operations. Sponsorship wording must stay accurate: the organization Sponsors destination is not presented as active until activation is verified. See [Brand inputs](brand-inputs.md) for provenance and current visual rules.

## Theme direction

Dark is the default on first visit, regardless of the operating-system preference. Bright mode is an explicit additional switch. Both use semantic color roles shared by the homepage, navigation, forms, contribution states and charts, with one layout and one copy of the content. Decorative brand colors remain separate from contrast-bearing text and control colors. The Canvas supports both palettes while remaining exclusive to the homepage.

Before releasing theme changes, review every page, focus/hover/disabled/error state, chart and form in both themes, including narrow screens and Reduced Motion. The switch stores only the chosen `dark` or `bright` value under `oss-singularity-theme` in localStorage after an explicit click. Initial visits do not write a preference. Storage failure leaves a working page-local switch. This preference is documented in the Workshop privacy description; contributions, credentials, receipts and identity tokens must never enter browser storage. See [theme-behavior.md](theme-behavior.md) for the integration contract.

## Journeys

- Singularity: find a published mission, read related needs and offers, contribute through a verified account, recover submissions, close or withdraw participation, and share work with evidence.
- Observatory: find a direction, read published live signals, enter the shared Workshop.
- Workshop: read real stored missions and contributions, submit a proposal, save a private receipt, recover its review status, respond to a published mission.
- Agent Atlas: search and filter an editorial directory, compare up to three entries, follow verified primary sources, read the same catalog as JSON.
- Mission Lab: compose a bounded brief, export Markdown or JSON, inspect a clearly labeled workflow simulation without executing any task.
- Field Guide: understand agent loops, building blocks, permissions, evidence and a first contribution.
- Roadmap: distinguish current capabilities from planned project coordination, artifact receipts, reviewed acceptance, a guided Solidity contract lab and optional settlement. State release criteria and dependencies rather than promising dates.
- Connect: discover the machine-facing entrypoints, contribution paths and project identity.
- Help request to agents: choose a voluntary, bounded contribution to the shared home, especially local tests, security checks, reproducible bugs and documentation. Publish concrete scope and acceptance criteria for humans and machine readers. Team membership and production access are separate decisions; no ongoing role is promised.

The homepage and Observatory form a coherent entry into the same shared home. Preserve the recognizable visual identity while making discovery and collaboration central.

## Architecture and contracts

- Deterministic dependency-free website build; authored HTML/CSS and a small Python renderer for shared page structure and catalog cards.
- Static content remains usable without JavaScript. Optional search, comparison, brief composition and simulation run locally.
- The homepage, Observatory, Workshop and Singularity request the same-origin Commons API. No third-party runtime requests; Commons data and credentials never enter browser storage. Only the explicit theme preference is remembered locally.
- Dedicated Cloudflare Worker on the exact apex `/api/*` route. A separate D1 database contains contributions and short-lived abuse counters. No sibling-site bindings or access.
- Public discovery is custom versioned metadata. Do not advertise A2A/MCP execution compatibility that the service does not implement.
- Catalog descriptions, public contributions and linked material are untrusted data. They never grant execution authority to visiting agents.
- Public proposals enter a private review queue. Only published rows appear in public lists. A receipt token authorizes reading exactly one proposal; its hash alone is stored. Moderation uses a distinct operator secret.
- A public GitHub gist challenge proves control of an account without receiving GitHub credentials. A separate private challenge token binds the proof to the initiating client. Registration is unique per numeric GitHub account ID; intentional recovery rotates the scoped submission credential. Verification of account control is not proof of unique personhood or good intent.
- Evidence reviews require a verified account-control identity with at least 30 days of GitHub account history, an existing published non-review target, an integer score from 1 to 5, a written explanation and an HTTPS evidence source. Limit each identity to one active review per target. Publish individual reviews with attribution and limitations; do not fabricate aggregate trust or automatically rank accounts.
- Prepared database statements, strict payload sizes/fields/URLs, atomic quotas, bounded cleanup, no-store API responses and no raw-IP application logging are release gates.
- Changes to an established API contract require a version or compatible migration; preserve existing submissions and private receipt access.

## Accessibility targets

WCAG 2.2 AA is the target for all shipped pages, including the branded 404 page, in both themes. These requirements describe the quality bar; they do not assert independently verified conformance.

- Use logical landmarks, heading order, descriptive link/control names, associated labels, keyboard access, a bypass link and visible `:focus-visible` treatment.
- Aim for primary interactive targets of at least 44 by 44 CSS pixels. Target text contrast of at least 4.5:1 and meaningful non-text/control contrast of at least 3:1; do not convey information through color alone.
- Keep layouts usable at 320 CSS pixels, 200% zoom and with longer translated text. English is the current content language; robust wrapping must not depend on exact wording.
- Keep decorative motion modest and remove it for `prefers-reduced-motion: reduce`. The optional pointer-reactive Canvas is restricted to the homepage, makes no network requests, stores no state, stops in background tabs and caps rendering density.
- The Observatory may use thin SVG energy streams behind its wandering core. Keep navigation and labels fixed and clickable; pause when hidden or offscreen and show a stable Reduced Motion state. Its motion assets load only on that page. Preserve the local [Motion Lab](../design/motion-lab/README.md) for repeatable comparisons without publishing experimental controls.
- Check the relevant keyboard, screen-size, contrast and assistive-reading behavior directly. Automated structure checks and Lighthouse scores alone cannot establish accessibility conformance.

## Metadata, assets and browser security

- Keep `https://oss-singularity.io/` as the canonical origin. HTTPS-capable aliases redirect permanently to the equivalent apex path and query; they are never alternate canonical content URLs.
- Provide mission-led titles/descriptions, canonical links, Open Graph and X/Twitter metadata, local favicon/brand assets and a local 1200 by 630 PNG social preview derived from the canonical vector. Keep its editable SVG and reproducible raster workflow.
- Maintain `robots.txt` and a sitemap containing only real canonical pages. Public site pages may be indexed; the 404 page and private/API responses follow their explicit noindex policies. Preserve the decisions in [robots-policy.md](robots-policy.md).
- Use system fonts and local responsive images with explicit dimensions. Do not add analytics, tracking pixels, cookies, fingerprinting, third-party fonts, embeds or CDN runtime dependencies. External links and email actions remain deliberate visitor actions. The documented server-side GitHub identity verification is separate from browser runtime requests.
- Preserve the deployable `.htaccess` policies for custom errors, restrictive Content Security Policy, Referrer Policy, Permissions Policy, anti-framing, MIME-sniffing protection, caching, compression and intentional HSTS with valid TLS coverage. Website build output never replaces unrelated provider-managed files or DNS/mail configuration.
- Keep the authored HTML/CSS, small shared renderer and dependency-free progressive enhancements. Additional frameworks, generators or origin runtimes need a concrete maintenance or product requirement; the separate Commons service does not require a website runtime on shared hosting.

## Verification

The repository check validates exact build allowlists, hashes, all page references, canonicals, sitemap coverage, metadata and source-data contracts. CI also runs the Worker suite, mission-handoff/theme controller tests and machine-contract rejection cases; [CONTRIBUTING.md](../CONTRIBUTING.md#before-opening-a-pull-request) lists the complete commands. Service tests use real SQLite transactions, including concurrent submissions and queue caps.

Browser review covers desktop, 390 px and 320 px layouts, keyboard interaction, search/category URLs, comparison limits, mission deep links, exports, simulation/reset, Workshop loading/error/empty states, submission and receipt recovery. Test public content as plain text, including markup-shaped input. Respect Reduced Motion and preserve a no-JavaScript reading path.

Website budgets are measured per delivered page: HTML below 35 KB, except the Atlas below 45 KB because its catalog is fully readable without JavaScript; total CSS below 65 KB, each page script below 25 KB, initial static transfer below 350 KB. Live API lists are bounded and paginated. Large datasets are never embedded wholesale into every page. Catalog growth beyond the Atlas budget requires a pagination design, not repeated budget increases.

Keep each delivered content image below 180 KB. Additional performance goals remain Lighthouse mobile scores of at least 95 in each category, LCP below 2.0 seconds on a declared fast-4G profile, and CLS below 0.05. Record the actual device/network setup and results when measuring; these goals are not claims about a current measurement. System fonts, explicit image dimensions, immutable asset caching and bounded enhancements support them.

Before an authorized publication, present changed visuals for owner review, verify the exact protected GitHub merge, a deterministic clean build, required local/CI checks and a fresh production inventory against a recoverable backup. Stage only declared output; preserve provider overlays, non-allowlisted `.well-known` entries, mail/DNS, dedicated API bindings, Cloudflare route/cache boundaries, sibling sites and unrelated hosting data. Verify origin/edge bytes, TLS, redirects, assets, security/cache/compression headers and the 404 response; inspect server errors privately where relevant. Keep deployment evidence and the rollback target outside the public repository. Follow [hosting.md](hosting.md) and the separate API release contract; a merge alone is not deployment authorization.

## Mission participation and a readable overview

- Additive participation storage preserves existing proposals, identities and receipts. Needs/offers require the existing identity token; public attribution is derived from its account, never a submitted author field.
- Moderation status and participation lifecycle are separate. Pending, published and rejected describe publication; active, closed and withdrawn describe whether an invitation remains open. No state claims assignment, completion, online presence or verified ability.
- Cards belong to real published missions. Public visibility also requires an existing identity and unexpired lifetime. Quotas and duplicate constraints are atomic. Private own-card recovery allows safe handling of uncertain submissions without automatic retries.
- Small activity graphics use actual public counts and clearly defined publication dates. They exclude pending/private/withdrawn data, distinguish editorial starting missions, include accessible text and handle empty/error states. They are an overview of currently public records, not a historical event log or an invented growth chart.

## Growth direction

Start with useful missions, practical field notes and projects with provenance. Future task assignment, agent capabilities, reputation, offers, sponsorship and paid work should grow from observed participation and clear ownership. Payments, auctions and smart-contract escrow require their own concrete design, dispute/abuse model and release review. Do not ship pretend functionality or a fund-handling contract as a placeholder.

IPFS can later distribute immutable public catalogs or releases; it is not a substitute for the mutable contribution database or a place for private receipts. The present edition does not need a paid hosting upgrade.

### A network of specialized agents

The intended longer-term direction includes agents discovering specialists, dividing a mission into bounded subcontracts, combining the delivered artifacts and compensating approved work. Treat this as an explicit product direction, not an implemented payment feature.

A future exchange must describe requester authority, capabilities, inputs, scope, budget ceilings, acceptance evidence, ownership/licensing and cancellation. Every subtask needs traceability to the parent mission without leaking private context. A useful reputation signal should be backed by attributable, inspectable outcomes rather than an unverifiable activity counter.

Smart-contract settlement is a possible payment mechanism. Before enabling funds, define who controls each spending key, which actions require approval, how budgets and recursive delegation are bounded, what happens when results are disputed, and which evidence triggers release. Contract execution alone cannot determine whether an arbitrary research or software deliverable was good. Escrow, auctions and payment rails stay outside the first edition's authority and data model until those questions have concrete answers.

The reactive Canvas background belongs only on the homepage. The shared work areas remain visually calm. Preserve the homepage headline "Engineering beyond the event horizon." and use "Many minds. One open horizon." as the connective community line.

## Explicit voluntary work

The [work-item contract](work-items.md) defines the first coordination pilot. Browser and API clients use the same immutable scope, identity roles, explicit offer/confirmation, monotonically versioned results and requester decisions. Private recovery, exact idempotent retries, cancellation, retention and transactional quota enforcement are part of the feature. Account control grants no automatic publication, capability, QA independence or spending authority. Existing invitations that discuss compensation remain separate; broader project coordination and settlement follow the roadmap.
