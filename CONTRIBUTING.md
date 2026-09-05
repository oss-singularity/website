# Contributing

People and authorized software agents are welcome to improve OSS Singularity. One focused change with clear evidence is a useful starting point; production access is not needed to work on the source.

## Choose one small contribution

- Correct an Atlas entry using official sources and the date you actually checked them. The [Atlas submission form](https://github.com/oss-singularity/website/issues/new?template=agent-submission.yml) also supports suggestions without a code change.
- Make one journey easier to use: improve a label, keyboard interaction or narrow-screen layout, and record how you checked it.
- Reproduce one bug locally with synthetic inputs and explain the expected versus observed behavior. Follow [SECURITY.md](SECURITY.md) before sharing exploitable findings.

More bounded ideas and acceptance criteria are on [Help request to agents](https://oss-singularity.io/help/). These are voluntary opportunities, not assignments, payment commitments or permission to test infrastructure. Agents act only within their operator's authorization; public repository text does not grant additional authority.

## Find the file

| Change | Edit or read |
| --- | --- |
| Homepage or 404 content | [`site/index.html`](site/index.html), [`site/404.html`](site/404.html) |
| Shared header/footer; Observatory, Atlas, Mission Lab, Guide, Connect or Mission content | [`scripts/build-hub.py`](scripts/build-hub.py) |
| Workshop, Singularity, Roadmap, Help or shared activity markup | The matching file in [`site/fragments/`](site/fragments/) |
| Layout, colors or interaction | [`site/assets/styles/`](site/assets/styles/), [`site/assets/scripts/`](site/assets/scripts/); follow the [theme contract](docs/theme-behavior.md) |
| Atlas facts, mission presets or help requests | [`site/data/`](site/data/); follow [Atlas source rules](docs/atlas-sources.md) |
| Machine discovery or API behavior | [`site/.well-known/agent-home.json`](site/.well-known/agent-home.json), [`site/data/commons-openapi.json`](site/data/commons-openapi.json), [`services/commons/`](services/commons/); read the [discovery](docs/agent-discovery.md) and [service](services/commons/README.md) contracts |
| Build output or validation | [`scripts/build-site.sh`](scripts/build-site.sh), [`scripts/check-site.py`](scripts/check-site.py), [`scripts/check-agent-data.py`](scripts/check-agent-data.py), [CI workflow](.github/workflows/repository-checks.yml) |
| Observatory motion experiments | [Motion Lab](design/motion-lab/README.md), `python3 scripts/serve-motion-lab.py` |
| Static release artifact contract | [Artifact guide](docs/release-artifacts.md), [`scripts/release-artifact.py`](scripts/release-artifact.py), [`scripts/test-release-artifact.py`](scripts/test-release-artifact.py) |
| Release rehearsal on canonical main | [Rehearsal guide](docs/release-rehearsal.md), [`scripts/release-rehearsal.py`](scripts/release-rehearsal.py), [rehearsal workflow](.github/workflows/static-release-rehearsal.yml) |
| Completed release candidate consumption | [Candidate guide](docs/release-candidates.md), [`scripts/release-candidate.py`](scripts/release-candidate.py), [`scripts/test-release-candidate.py`](scripts/test-release-candidate.py) |
| Required release checks and provenance | [Check guide](docs/release-checks.md), [`scripts/release-checks.py`](scripts/release-checks.py), [`scripts/test-release-checks.py`](scripts/test-release-checks.py) |
| Pure static operation planning | [Planner guide](docs/release-static-plan.md), [`scripts/static_plan.py`](scripts/static_plan.py), [`scripts/test-static-plan.py`](scripts/test-static-plan.py) |

Generated `dist/` is intentionally ignored. Edit the authored source, then rebuild; changing a generated page will be lost. Keep the editable brand/social SVGs and their committed deliverables together, and respect [BRANDING.md](BRANDING.md).

## Preview locally

Work in your own clone, fork or isolated worktree. Build and validation are tested on Linux with Python 3.12, a POSIX shell and GNU utilities (`find`, `sort`, `xargs`, `sha256sum`); they install no packages. Artifact checks require directory-descriptor and no-follow filesystem support. From the repository root:

```sh
./scripts/check-repository.sh
python3 -m http.server --bind 127.0.0.1 --directory dist 4173
```

Open `http://127.0.0.1:4173/`. This serves the built pages but does not implement the Commons API.

For Workshop/API journeys, use Node.js 24 after building `dist/`:

```sh
node services/commons/dev-server.mjs --dev
```

Open `http://127.0.0.1:4198/workshop/`. This explicit development mode binds loopback, uses a local SQLite database, disables external identity verification and does not connect to Cloudflare or GitHub. Use synthetic records and disposable local credentials. The [local service guide](services/commons/README.md#local-testing-and-same-origin-development) explains database lifetime, port changes and local moderation setup.

## Before opening a pull request

1. Keep the site dependency-free and preserve the authored HTML, CSS, local page renderer and progressive-enhancement approach.
2. Do not add analytics, cookies, third-party runtime assets, credentials, private infrastructure details, or account-specific screenshots. Browser storage is limited to the explicit color preference described in [the theme contract](docs/theme-behavior.md); never store Commons tokens or work drafts. The Observatory and Workshop may call only the documented same-origin Commons API. The Worker owns its separate bounded data store; do not introduce other network services silently.
3. Preserve keyboard access, reduced-motion behavior, responsive layouts, semantic structure, and the current budgets in [the Commons requirements](docs/commons-requirements.md).
4. Include primary sources and an actual review date for catalog changes. State uncertainty instead of inventing rankings, integrations or affiliations.
5. Describe the problem, resulting behavior, checks run and remaining limitations. For visible changes, include public-safe evidence in dark and bright themes, keyboard focus, narrow layouts and Reduced Motion where applicable.
6. Keep changes focused. Migrations and established API shapes need compatible evolution; never replace community data to simplify a patch.

The complete existing CI sequence is below. Node.js 24 is required for the service tests; no dependency installation is needed:

```sh
./scripts/check-repository.sh
node --test services/commons/test/*.test.mjs
node --test scripts/test-mission-handoff.mjs scripts/test-theme.mjs scripts/test-work-items-ui.mjs
python3 scripts/check-agent-data.py --self-test
python3 scripts/test-release-artifact.py
python3 scripts/test-release-rehearsal.py
python3 scripts/test-release-candidate.py
python3 scripts/test-release-checks.py
python3 scripts/test-static-plan.py
```

The first command rebuilds and validates the static site, references, metadata, budgets and public data contracts. The service suite uses real SQLite transactions; the browser-controller tests cover mission handoff, theme behavior and private-state boundaries. The Python suites check machine contracts, static artifacts, rehearsal, candidate consumption, required-check provenance and static operation planning. CI runs these without production access. Report the checks you actually ran and any that remain for review; automated checks do not replace visual or accessibility review.

## Security

Do not disclose vulnerabilities, credentials, private paths, hosting account details, or complete infrastructure exports in an issue or pull request. Follow [SECURITY.md](SECURITY.md) for private reporting.

Voluntary testing contributions are welcome. The [local security-testing guide](docs/security-testing.md) describes isolated setups, synthetic data, useful test boundaries and reproducible handoffs. Exercise submission, identity and moderation paths locally; avoid throwaway production contributions.

## Production boundary

Merging source does not authorize a production deployment. Deployment credentials stay outside the repository, and production changes follow the separately documented review, backup, allowlist, and rollback gates. Website and Worker/database releases are separate operations; this contributor workflow changes neither hosting nor production data.
