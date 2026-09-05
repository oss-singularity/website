# Appearance

Every page, including the 404, shares one explicit appearance switch. Dark is
the first-visit default even when the operating system prefers a light theme.
The native button names the next action: a sun with Bright mode in Dark, a moon
with Dark mode in Bright. Its accessible label names the same destination; it
does not expose a conflicting pressed state. It stays hidden when JavaScript is
unavailable. Switching changes colors in place, preserving form
drafts, focus, scroll position and navigation history.

The external `theme-v1.js` runs synchronously in the document head before styles
are loaded. It reads only `oss-singularity-theme` from localStorage and accepts
only `bright` or `dark`. No value is written on page load. An explicit toggle
saves only that preference; blocked storage leaves a functional page-local
choice. A matching storage event updates other tabs without writing back.
Removing or clearing the preference restores Dark. A page restored from the
back/forward cache refreshes its choice, preserving its current appearance when
storage access fails. No token, receipt, input or
identifier is stored or included in a URL, event or network request.

Shared CSS roles define contrast-bearing surfaces, text, controls and charts.
Existing Dark nuances are retained as local fallbacks where one Bright role
serves several equivalent surfaces. Brand artwork retains its original colors.
The homepage Canvas reads `document.documentElement.dataset.theme` once at
startup and listens on `document` for `oss-theme-change`. It changes its palette
without resetting particles or starting another animation loop. Reduced Motion
continues to hide the Canvas and remove animated transitions.

Run the repository gate and `node --test scripts/test-theme.mjs`. Browser review
must also cover the complete page set, narrow layouts, native keyboard
activation, chart and form states, persistence across navigation/reload/back,
unchanged drafts, and the decorative Canvas in both palettes. Check Reduced
Motion and forced colors through supported browser controls when available;
record source-only checks separately from actual browser emulation.
