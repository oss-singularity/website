# Workshop interface

`site/fragments/workshop.html` supplies the content inside the page shell's main element for `/workshop/`. The page shell must load `site/assets/scripts/workshop-v1.js` and `site/assets/scripts/workshop-identity-v1.js` with `defer`, the shared hub stylesheet, and `site/assets/styles/workshop-v1.css`. The fragment contains a page header and sections; it has no main, document, header-navigation, or footer wrapper.

This interface uses a real shared API. It does not run agents or fabricate activity. Public cards come exclusively from published API entries. Seeded starting missions are visibly distinguished from community contributions. Form submissions create pending proposals for review; no client-side action can publish one.

## API integration

- On page load, `GET /api/v1/missions?limit=100`, `GET /api/v1/contributions?limit=100`, and `GET /api/v1/reviews?limit=100` load concurrently. Each returns `{items, next_cursor}`. Results are combined and deduplicated by ID. All/Mission/Field note/Project/Review controls filter the loaded records locally.
- A visible Load more action follows each available cursor. No hidden pagination, continuous polling, or requests caused by typing occur.
- Refresh signals performs fresh public reads. Automatic refresh is off by default; the explicit checkbox allows one refresh per minute while the page is visible.
- `POST /api/v1/proposals` sends `kind`, `title`, `summary`, optional `url`, and optional `mission_id` for regular signals. An evidence review instead requires `url`, `target_id`, and an integer `score` from 1 to 5 and omits `mission_id`. Public sharing consent is a required local form control; it is not an unknown API field.
- Reviews require a scoped Commons identity token in the Bearer header. Other contributions can be anonymous or use the token for public attribution. The server verifies target publication, account eligibility, and duplicate-review restrictions. Tokens never enter request bodies or URLs.
- The form applies the API's limits: title 3–120 characters, summary 20–2,000, HTTPS source URL up to 2,048, mission ID up to 80, and a maximum 8,192-byte JSON body. The service remains the authority for public hostname validation and existing mission links.
- A successful response is `202 {id,status:"pending",poll_url,receipt_token}`. The private receipt is displayed once in that response, and can be copied or downloaded. A later successful submission replaces the active receipt, so save each receipt when shown.
- Status recovery uses the saved proposal ID and 43-character private token. `GET /api/v1/proposals/:id` receives `Authorization: Bearer <receipt_token>`; the token never appears in a URL. Status checks require a user action and do not poll.
- Error responses use `{error:{code,message,field?}}`. Field errors target the corresponding form input. Rate-limit responses can supply `retry_after_seconds` or `Retry-After`.
- Mutations are never automatically retried. A timeout, network error, invalid success response, or server failure has an explicit delivery-uncertainty message because the server might already have accepted the proposal.

All requests stay on the current origin with `credentials: "omit"`. There are no operator secrets, cookies, localStorage, sessionStorage, remote image loads, or arbitrary user-provided fetch destinations. Only static API paths receive requests. Private tokens exist in active-page memory and form fields until navigation, unless the user explicitly exports them. The pagehide handler clears the receipt and recovery token, including for pages retained in the browser's back/forward cache.

## Account-control proof and evidence reviews

The identity wizard is collapsed until opened. `POST /api/v1/identity-challenges` receives only `github_login` and returns a public proof with a 10-minute expiry plus a private `challenge_token`. The visitor copies or downloads only `response.proof`, manually creates a public GitHub gist named `oss-singularity-identity.json`, and submits its URL through `POST /api/v1/identities`. That verification request authenticates with the private challenge token in a Bearer header. The challenge token never appears in the displayed proof, copied JSON, downloaded proof, or request body; it is cleared on expiry, successful enrollment, or navigation. The browser never requests a GitHub password, personal access token, or account-creation operation. Gist verification is performed by the shared service.

Successful verification returns a public identity and one private `api_token`. The wizard offers copy/download of its private receipt and fills the contribution form's password field for the current page. Account-control proof is distinct from unique-person verification, expertise, or trustworthiness. Reviews require a GitHub account at least 30 days old. A younger connected account can use regular contributions while waiting for eligibility. Existing identities require a fresh proof and the explicit replacement checkbox to rotate a token; the old token is invalidated. The wizard never retries a write automatically, and uncertain delivery explains the fresh-proof recovery path.

Published evidence reviews display the target title when loaded (otherwise its ID), an individual 1–5 usefulness assessment, the evidence link, and verified GitHub account-control attribution. There is no aggregate rating, star ranking, or inferred reputation. Clicking a review target reveals the corresponding loaded card; a `?signal=<id>` deep link can also focus it. Older targets require the visible Load more action. Withdrawn or unpublished targets are excluded by the service's review-feed policy.

The private proposal receipt grants status access to one submission. The private identity token attributes submissions to a GitHub account. They have different purposes and are labeled separately. Both modules clear raw tokens on pagehide; the identity module also clears its proof and receipt state.

## Rendering and accessibility

Remote fields are rendered with `createElement`, `textContent`, and text nodes. There are no HTML string sinks. Source links are checked for HTTPS and absence of URL credentials before rendering; they use `noopener noreferrer` in a new tab. Public response item shapes are checked before rendering.

The board provides initial loading, empty, partial failure, unavailable, retry, and pagination states. Failed refreshes retain available records instead of pretending the board is empty. Status messages use polite live regions. The list uses `aria-busy`; filter buttons expose `aria-pressed`. All fields have visible labels; receipt tokens use a password input for recovery. Clipboard denial falls back to selecting the receipt text and explains how to copy or download it.

Shared classes used include `page-heading`, `section-kicker`, `page-lead`, `hub-section`, `hub-section-heading`, `button`, `button-primary`, `button-secondary`, `notice`, `micro-label`, and `hub-actions`. Workshop-specific classes provide hooks for board grids, cards, filters, forms, receipt code blocks, and privacy details.

## Privacy copy

The form states that content is sent to OSS Singularity and reviewed before publication. The privacy section describes stored submission/review fields, hashed receipt and identity tokens, public GitHub attribution, persistent identity profiles until operator removal, challenge expiry, no token browser storage, counter expiry after 24 hours followed by bounded cleanup, and pending/rejected proposal expiry after 30 days followed by cleanup. It distinguishes application behavior from infrastructure processing and public GitHub gist hosting. Published signals remain until removed. Keep this text synchronized with the API implementation and retention policy.

## Verification

Use the repository checks and `node --check site/assets/scripts/workshop-v1.js`. Test locally against a development Worker or intercept API responses in an isolated browser; do not create public test submissions.

Verify initial/empty/partial/error/paginated boards, each filter, seeded provenance, literal remote markup, mission response linking, review target/evidence/score validation, review authentication, GitHub attribution, field/payload limits, duplicate-submit prevention, receipt export/recovery, clipboard denial, 429 guidance, and delivery ambiguity. Exercise valid/invalid GitHub logins, public proof export, expired challenge, gist URL validation, successful enrollment, existing-identity rotation consent, younger-account messaging, and pagehide token cleanup. Confirm automatic refresh is off initially and pauses in hidden tabs. Inspect mobile/desktop layouts, keyboard navigation, labels, status announcements, and no-JavaScript fallbacks.
