# Mission Lab

Mission Lab is a local planning tool at `/lab/`. It turns one of three public mission presets into a Markdown or JSON brief and illustrates a five-stage workflow. The website does not connect to a model, execute agents or tools, submit a goal, or persist input. People can copy a brief into an agent tool of their choice; automated clients can read `/data/missions.json` without executing JavaScript.

The simulation is deterministic and explicitly labeled. It illustrates observe, plan, build, review, and handoff in that order. The team selection changes the described responsibilities. The execution boundary changes whether the example describes a proposal or an authorized local deliverable. Neither selection grants actual permissions to another application.

## Files and data contract

- `site/data/missions.json` is the public preset collection. Its top-level fields are `schema_version`, `updated`, and `missions`.
- Each preset has `id`, `title`, `summary`, `goal`, `deliverable`, `constraints`, and `acceptance`. Both `constraints` and `acceptance` are arrays of nonempty strings.
- `site/assets/scripts/mission-lab-v1.js` embeds the same preset objects so the interactive page needs no data request. Keep those values identical to the public collection when updating a mission.
- Exported JSON has `kind: "mission-brief"` and includes the selected goal, deliverable, team roles, execution boundary, constraints, acceptance criteria, handoff requirements, and a statement that no agent has run.
- Dates describe the preset collection update. They are not claims that a project, tool, or proposed mission has been verified.

## HTML contract

The controller activates only when all core elements exist: `#mission-form`, `#mission-preset`, `#mission-goal`, `#mission-topology`, `#mission-boundary`, `#mission-output`, and `#mission-status`. Use a `pre` for the output and `role="status"` or `aria-live="polite"` for status text. Author all page content and controls in HTML for progressive enhancement.

- Preset option values: `ship-feature`, `research-map`, `audit-project`.
- Deep links may select an initial preset using `?mission=ship-feature`, `?mission=research-map`, or `?mission=audit-project`. Unknown values are ignored.
- Team option values: `solo`, `pair`, `crew`.
- Boundary option values: `read-only`, `workspace`.
- Optional editable fields: `#mission-deliverable` and `#mission-constraints`. Constraints use one line per item. Without these fields, the controller uses preset values.
- Export buttons: `#mission-copy`, `#mission-download` for Markdown, and `#mission-json` for JSON. Use `type="button"` and initially `disabled` until the controller activates.
- Simulation controls: `#simulation-start` and `#simulation-reset`; status: `#simulation-status` with `aria-live="polite"`; ordered list: `#simulation-steps`.
- Each simulation list item uses `data-stage="observe|plan|build|review|handoff"`. Its optional `[data-stage-detail]` child receives the planned stage explanation as plain text.
- The controller sets each stage's `data-state` to `idle`, `active`, or `complete`; toggles `.is-active` and `.is-complete`; and uses `aria-current="step"` only for the active item. The list receives `aria-busy` while running.

Changing a preset replaces its goal and optional editable fields. Other form edits update the brief immediately. Editing any field resets the simulation so a sequence cannot continue using an older brief. Starting again replays the same selected workflow. Reset and navigation cancel the sequence. Reduced Motion displays the completed illustrative sequence immediately, including when the preference changes during a run.

## Validation

Run the repository's normal site checks and `node --check site/assets/scripts/mission-lab-v1.js`. Verify the following in a real browser:

1. Every preset updates the goal, output, acceptance criteria, and exports.
2. All three teams and both boundaries change the brief and planned stage descriptions.
3. Text containing angle brackets or markup is displayed literally and never interpreted as HTML.
4. Markdown copy and Markdown/JSON downloads contain the same selected brief. If clipboard permission is denied, the brief is selected and an actionable status appears.
5. Start advances through all five stages; reset and input changes cancel it; replay works after completion.
6. Reduced Motion shows all stages at once, with no timed sequence.
7. Keyboard focus remains usable, controls have authored labels, and status changes are announced without making the whole generated brief a live region.
8. With JavaScript disabled, authored explanations and the public preset data remain accessible; interactive actions remain disabled.

The code performs no `fetch`, `XMLHttpRequest`, WebSocket, beacon, cookie, local-storage, or session-storage operations. Clipboard writes and file downloads require direct clicks. All authored or user-provided text reaches the page through text fields or `textContent`.
