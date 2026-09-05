# Observatory Motion Lab

Compare the current Observatory hero locally, using the same core journey and seed **60** for three variants:

- **Core only:** the shared production core motion, without streams.
- **Thin energy:** the selected production design, retained here as a comparison implementation.
- **Wide wormhole:** the wider experimental alternative; it is not a production option.

From the repository root, run:

```sh
python3 scripts/serve-motion-lab.py
```

Open the printed loopback address (default `http://127.0.0.1:4204/`). To choose another port, add `--port 4205`. An occupied port is refused; the helper never stops an existing server. Python 3 and the repository's normal build prerequisites are sufficient; no JavaScript packages are needed.

Use the variant buttons, Dark/Bright toggle, and full-width, 320 px, 390 px or 760 px viewport. **Restart · seed 60** resets the same path and timing. Changing the viewport or theme also restarts the comparison. Compare at the same size and elapsed time; frame timing can vary with browser load. The fixed dark toolbar surrounds the chosen preview theme.

The helper builds the current checkout into an owned temporary directory. It extracts only the Observatory hero and copies the required styles, shared motion script and brand mark from that fresh build. It does not use or overwrite the repository's `dist/`. Stop with **Ctrl+C** to remove its temporary build and server files. Restart the helper after source changes to rebuild the preview. Hero links open the canonical OSS Singularity site in a new tab.

The lab removes the hero's `data-energy-streams` capability attribute. The unmodified production motion script still supplies the core; `streams-lab.js` supplies the local thin/wide variants without mounting duplicate production streams. `preview-bootstrap.js` sets the theme and deterministic random source before that shared script runs. Only the iframe's random source is changed. These files are development inputs and are not copied into the production site.

When changing motion, compare all three variants in both themes and at wide and narrow widths. Check fixed link positions and pointer targets, keyboard focus, orbit attachment points, resizing, repeated variant switches, hidden/offscreen pause and resume, and the stable Reduced Motion state. Keep the iframe height tied to hero content, not the viewport-sized body, to avoid a resize feedback loop. A selected design still needs the repository's normal checks and visual review before promotion.
