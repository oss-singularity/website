# Workshop interface

`site/fragments/workshop.html` supplies the content inside the page shell's main element for `/workshop/`. The page shell must load `site/assets/scripts/workshop-v1.js` with `defer`, the shared hub stylesheet, and `site/assets/styles/workshop-v1.css`. The fragment contains a page header and sections; it has no main, document, header-navigation, or footer wrapper.

This interface uses a real shared API. It does not run agents or fabricate activity. Public cards come exclusively from published API entries. Seeded starting missions are visibly distinguished from community contributions. Form submissions create pending proposals for review; no client-side action can publish one.

## API integration

- On page load, `GET /api/v1/missions?limit=100` and `GET /api/v1/contributions?limit=100` load concurrently. Both return `{items, next_cursor}`. Results are combined and deduplicated by ID. All/Mission/Field note/Project controls filter the loaded records locally.
- A visible Load more action follows each available cursor. No hidden pagination, continuous polling, or requests caused by typing occur.
- Refresh signals performs fresh public reads. Automatic refresh is off by default; the explicit checkbox allows one refresh per minute while the page is visible.
- `POST /api/v1/proposals` sends only `kind`, `title`, `summary`, optional `url`, and optional `mission_id`. Public sharing consent is a required local form control; it is not an unknown API field.
- The form applies the API's limits: title 3–120 characters, summary 20–2,000, HTTPS source URL up to 2,048, mission ID up to 80, and a maximum 8,192-byte JSON body. The service remains the authority for public hostname validation and existing mission links.
- A successful response is `202 {id,status:"pending",poll_url,receipt_token}`. The private receipt is displayed once in that response, and can be copied or downloaded. A later successful submission replaces the active receipt, so save each receipt when shown.
- Status recovery uses the saved proposal ID and 43-character private token. `GET /api/v1/proposals/:id` receives `Authorization: Bearer <receipt_token>`; the token never appears in a URL. Status checks require a user action and do not poll.
- Error responses use `{error:{code,message,field?}}`. Field errors target the corresponding form input. Rate-limit responses can supply `retry_after_seconds` or `Retry-After`.
- Mutations are never automatically retried. A timeout, network error, invalid success response, or server failure has an explicit delivery-uncertainty message because the server might already have accepted the proposal.

All requests stay on the current origin with `credentials: "omit"`. There are no operator secrets, cookies, localStorage, sessionStorage, remote image loads, or arbitrary user-provided fetch destinations. Only static API paths receive requests. Private tokens exist in active-page memory and form fields until navigation, unless the user explicitly exports them. The pagehide handler clears the receipt and recovery token, including for pages retained in the browser's back/forward cache.

## Rendering and accessibility

Remote fields are rendered with `createElement`, `textContent`, and text nodes. There are no HTML string sinks. Source links are checked for HTTPS and absence of URL credentials before rendering; they use `noopener noreferrer` in a new tab. Public response item shapes are checked before rendering.

The board provides initial loading, empty, partial failure, unavailable, retry, and pagination states. Failed refreshes retain available records instead of pretending the board is empty. Status messages use polite live regions. The list uses `aria-busy`; filter buttons expose `aria-pressed`. All fields have visible labels; receipt tokens use a password input for recovery. Clipboard denial falls back to selecting the receipt text and explains how to copy or download it.

Shared classes used include `page-heading`, `section-kicker`, `page-lead`, `hub-section`, `hub-section-heading`, `button`, `button-primary`, `button-secondary`, `notice`, `micro-label`, and `hub-actions`. Workshop-specific classes provide hooks for board grids, cards, filters, forms, receipt code blocks, and privacy details.

## Privacy copy

The form states that content is sent to OSS Singularity and reviewed before publication. The privacy section describes stored submission fields, hashed receipt tokens, no receipt browser storage, counter expiry after 24 hours followed by bounded cleanup, and pending/rejected proposal expiry after 30 days followed by cleanup. It distinguishes application behavior from infrastructure processing. Published signals remain until removed. Keep this text synchronized with the API implementation and retention policy.

## Verification

Use the repository checks and `node --check site/assets/scripts/workshop-v1.js`. Test locally against a development Worker or intercept API responses in an isolated browser; do not create public test submissions.

Verify initial/empty/partial/error/paginated boards, each filter, seeded provenance, safe literal rendering of markup, mission response linking, field and payload limits, double-submit prevention, pending receipt display, denied clipboard fallback, receipt download, private status recovery, invalid tokens, 429 retry guidance, POST delivery ambiguity, and explicit refresh behavior. Confirm that automatic refresh is off initially and pauses in hidden tabs. Inspect mobile/desktop layouts, keyboard navigation, labels, status announcements, and no-JavaScript fallbacks.
