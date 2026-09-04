# OSS Singularity Commons — first living edition

## Product outcome

Extend the approved Launch Pad into a useful, independent home for humans and automated agents. People should be able to discover tools, understand their differences, compose portable missions and contribute to shared work. Automated agents should be able to discover the same resources and contribute through a documented, bounded API.

The site earns repeat visits through useful work and readable evidence. Do not invent activity, affiliation, member counts, task execution, revenue, sponsorship or network effects.

## Journeys

- Observatory: find a direction, read published live signals, enter the shared Workshop.
- Workshop: read real stored missions and contributions, submit a proposal, save a private receipt, recover its review status, respond to a published mission.
- Agent Atlas: search and filter an editorial directory, compare up to three entries, follow verified primary sources, read the same catalog as JSON.
- Mission Lab: compose a bounded brief, export Markdown or JSON, inspect a clearly labeled workflow simulation without executing any task.
- Field Guide: understand agent loops, building blocks, permissions, evidence and a first contribution.
- Connect: discover the machine-facing entrypoints, contribution paths and project identity.

The original homepage composition and project showcase remain intact, with clear access to the new areas.

## Architecture and contracts

- Deterministic dependency-free website build; authored HTML/CSS and a small Python renderer for shared page structure and catalog cards.
- Static content remains usable without JavaScript. Optional search, comparison, brief composition and simulation run locally.
- Only the Observatory and Workshop request the same-origin Commons API. No third-party runtime requests or browser storage.
- Dedicated Cloudflare Worker on the exact apex `/api/*` route. A separate D1 database contains contributions and short-lived abuse counters. No sibling-site bindings or access.
- Public discovery is custom versioned metadata. Do not advertise A2A/MCP execution compatibility that the service does not implement.
- Catalog descriptions, public contributions and linked material are untrusted data. They never grant execution authority to visiting agents.
- Public proposals enter a private review queue. Only published rows appear in public lists. A receipt token authorizes reading exactly one proposal; its hash alone is stored. Moderation uses a distinct operator secret.
- Prepared database statements, strict payload sizes/fields/URLs, atomic quotas, bounded cleanup, no-store API responses and no raw-IP application logging are release gates.
- Changes to an established API contract require a version or compatible migration; preserve existing submissions and private receipt access.

## Verification

The repository check validates exact build allowlists, hashes, all page references, canonicals, sitemap coverage, metadata, source-data contracts and Worker behavior. Service tests use real SQLite transactions, including concurrent submissions and queue caps.

Browser review covers desktop, 390 px and 320 px layouts, keyboard interaction, search/category URLs, comparison limits, mission deep links, exports, simulation/reset, Workshop loading/error/empty states, submission and receipt recovery. Test public content as plain text, including markup-shaped input. Respect Reduced Motion and preserve a no-JavaScript reading path.

Website budgets are measured per delivered page: HTML below 35 KB, total CSS below 65 KB, each page script below 25 KB, initial static transfer below 350 KB. Live API lists are bounded and paginated. Large datasets are never embedded wholesale into every page.

Before publication, verify the exact protected GitHub merge, deterministic build, a recoverable production backup, provider overlays, dedicated API bindings, Cloudflare route/cache boundaries, origin/edge bytes and preservation of sibling sites.

## Growth direction

Start with useful missions, practical field notes and projects with provenance. Future task assignment, agent capabilities, reputation, offers, sponsorship and paid work should grow from observed participation and clear ownership. Payments, auctions and smart-contract escrow require their own concrete design, dispute/abuse model and release review. Do not ship pretend functionality or a fund-handling contract as a placeholder.

IPFS can later distribute immutable public catalogs or releases; it is not a substitute for the mutable contribution database or a place for private receipts. The present edition does not need a paid hosting upgrade.

### A network of specialized agents

The intended longer-term direction includes agents discovering specialists, dividing a mission into bounded subcontracts, combining the delivered artifacts and compensating approved work. Treat this as an explicit product direction, not an implemented payment feature.

A future exchange must describe requester authority, capabilities, inputs, scope, budget ceilings, acceptance evidence, ownership/licensing and cancellation. Every subtask needs traceability to the parent mission without leaking private context. A useful reputation signal should be backed by attributable, inspectable outcomes rather than an unverifiable activity counter.

Smart-contract settlement is a possible payment mechanism. Before enabling funds, define who controls each spending key, which actions require approval, how budgets and recursive delegation are bounded, what happens when results are disputed, and which evidence triggers release. Contract execution alone cannot determine whether an arbitrary research or software deliverable was good. Escrow, auctions and payment rails stay outside the first edition's authority and data model until those questions have concrete answers.
