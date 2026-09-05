# Reading position in section menus

The Field Guide, Help and Roadmap share the `.guide-toc` navigation. A small local
enhancement marks the section at the upper reading line with
`aria-current="location"`. The marker clears outside the indexed sections.
The last visible section remains reachable when the document is too short to
scroll its heading to that line.

The accent fades without moving text. Links keep native fragment navigation,
keyboard focus and browser history; scrolling does not write the URL or steal
focus. Without JavaScript every link still works. Targets are at least 44 px
high, reduced motion disables transitions, and forced colors retains a border.
Viewport changes, expanded details and restored pages refresh the marker.

Verify in a real browser: all three pages, direct fragments, ordinary scrolling
in both directions, a short final section, keyboard navigation, narrow layout
and reduced motion. The enhancement reads only section geometry; it makes no
network requests and stores no visitor state.
