/* Local Observatory motion comparison. No requests, storage or auto-mount. */
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const mounted = new WeakMap();
  const variants = new Set(["thin-energy", "wide-wormhole"]);
  const node = (tag, attrs = {}) => {
    const element = document.createElementNS(NS, tag);
    for (const [name, value] of Object.entries(attrs)) element.setAttribute(name, value);
    return element;
  };
  const mix = (a, b, t) => a + (b - a) * t;
  const point = (x, y) => ({ x, y });
  const add = (a, b, weight = 1) => point(a.x + b.x * weight, a.y + b.y * weight);
  const hash = (seed, index) => {
    let value = Math.imul(seed ^ index, 0x45d9f3b);
    value = Math.imul(value ^ value >>> 16, 0x45d9f3b);
    return ((value ^ value >>> 16) >>> 0) / 4294967295 * 2 - 1;
  };
  // Quintic interpolation has zero first/second derivatives at target changes.
  const noise = (seed, seconds, period) => {
    const position = seconds / period, step = Math.floor(position), f = position - step;
    const eased = f * f * f * (f * (f * 6 - 15) + 10);
    return mix(hash(seed, step), hash(seed, step + 1), eased);
  };
  const cubic = (curve, t) => {
    const a = 1 - t;
    return point(
      a * a * a * curve[0].x + 3 * a * a * t * curve[1].x + 3 * a * t * t * curve[2].x + t * t * t * curve[3].x,
      a * a * a * curve[0].y + 3 * a * a * t * curve[1].y + 3 * a * t * t * curve[2].y + t * t * t * curve[3].y,
    );
  };
  const pathData = curves => "M" + curves[0][0].x.toFixed(2) + " " + curves[0][0].y.toFixed(2)
    + curves.map(curve => " C" + curve.slice(1).map(p => p.x.toFixed(2) + " " + p.y.toFixed(2)).join(" ")).join("");
  const distanceTable = curves => {
    const table = [{ p: curves[0][0], distance: 0 }];
    for (const curve of curves) for (let i = 1; i <= 32; i += 1) {
      const p = cubic(curve, i / 32), previous = table[table.length - 1];
      table.push({ p, distance: previous.distance + Math.hypot(p.x - previous.p.x, p.y - previous.p.y) });
    }
    return table;
  };
  const pointAlong = (table, phase) => {
    const distance = phase * table[table.length - 1].distance;
    let next = 1;
    while (next < table.length - 1 && table[next].distance < distance) next += 1;
    const a = table[next - 1], b = table[next];
    const t = (distance - a.distance) / Math.max(.001, b.distance - a.distance);
    return point(mix(a.p.x, b.p.x, t), mix(a.p.y, b.p.y, t));
  };
  function mount(host, options = {}) {
    if (!(host instanceof HTMLElement)) throw new TypeError("A constellation element is required.");
    const core = host.querySelector(".constellation-core img");
    const orbitNodes = [...host.querySelectorAll(".constellation-orbit")].slice(0, 2);
    if (!core || orbitNodes.length !== 2) throw new TypeError("The Observatory core and two orbits are required.");
    mounted.get(host)?.destroy();
    let variant = variants.has(options.variant) ? options.variant : "thin-energy";
    const seed = Number.isFinite(options.seed) ? options.seed | 0 : 60;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    const svg = node("svg", { class: "obs-stream-layer", viewBox: "0 0 600 600", "aria-hidden": "true", focusable: "false" });
    const groups = [0, 1].map(index => {
      const group = node("g", { class: "obs-stream-group obs-stream-" + index });
      const haze = node("path", { class: "obs-stream-haze", fill: "none" });
      const lanes = Array.from({ length: 5 }, () => node("path", { class: "obs-stream-lane", fill: "none" }));
      const markers = Array.from({ length: 5 }, () => node("circle", { class: "obs-stream-packet", r: "2" }));
      const endpoints = [0, 1].map(() => node("circle", { class: "obs-stream-anchor", r: "2.4" }));
      group.append(haze, ...lanes, ...markers, ...endpoints);
      svg.appendChild(group);
      return { haze, lanes, markers, endpoints };
    });
    host.prepend(svg);
    host.classList.add("obs-streams-host");
    svg.dataset.variant = variant;
    let geometry = [], frame = 0, elapsed = 0, last = 0, inView = false;
    let manualPaused = false, pageActive = true, destroyed = false, draws = 0;
    let lastEndpoints = [], lastCore = null;
    const readGeometry = () => {
      const width = host.clientWidth, height = host.clientHeight;
      if (!width || !height) return;
      geometry = orbitNodes.map(orbit => {
        const style = getComputedStyle(orbit);
        const matrix = style.transform === "none" ? new DOMMatrixReadOnly() : new DOMMatrixReadOnly(style.transform);
        const center = point(orbit.offsetLeft + orbit.offsetWidth / 2, orbit.offsetTop + orbit.offsetHeight / 2);
        const radius = point((orbit.offsetWidth - 1) / 2, (orbit.offsetHeight - 1) / 2);
        return angle => {
          const x = Math.cos(angle) * radius.x, y = Math.sin(angle) * radius.y;
          return point((center.x + matrix.a * x + matrix.c * y + matrix.e) * 600 / width,
            (center.y + matrix.b * x + matrix.d * y + matrix.f) * 600 / height);
        };
      });
    };
    const running = () => !destroyed && inView && pageActive && !document.hidden && !manualPaused && !reduced.matches;
    const render = () => {
      if (!geometry.length || destroyed) return;
      // Read before SVG writes; this follows the separate core motion controller.
      const hostBox = host.getBoundingClientRect(), coreBox = core.getBoundingClientRect();
      if (!hostBox.width || !hostBox.height) return;
      const center = point((coreBox.x + coreBox.width / 2 - hostBox.x) * 600 / hostBox.width,
        (coreBox.y + coreBox.height / 2 - hostBox.y) * 600 / hostBox.height);
      const wide = variant === "wide-wormhole", time = reduced.matches ? 0 : elapsed;
      const endpoints = [];
      groups.forEach((group, index) => {
        const direction = index === 0 ? 1 : -1;
        const drift = noise(seed + 101 + index * 317, time, 10.5 + index) * .68;
        const theta = (index === 0 ? 3.2 : 2.35) + drift;
        const spread = noise(seed + 607 + index * 193, time, 13.2) * .32;
        const start = geometry[index](theta), end = geometry[index](theta + Math.PI + spread);
        endpoints.push([start, end]);
        const length = Math.hypot(end.x - start.x, end.y - start.y);
        const axis = point((end.x - start.x) / length, (end.y - start.y) / length);
        const normal = point(-axis.y, axis.x);
        const wave = noise(seed + 907 + index * 313, time, 7.6) * 12;
        const throat = add(center, normal, direction * (wide ? 7 : 12));
        const bend = direction * ((wide ? 67 : 36) + wave);
        let centralCurves;
        group.lanes.forEach((lane, laneIndex) => {
          const fan = wide ? (laneIndex - 2) * 7 : 0;
          const shoulderIn = add(point(mix(start.x, throat.x, .5), mix(start.y, throat.y, .5)), normal, bend + fan);
          const shoulderOut = add(point(mix(throat.x, end.x, .5), mix(throat.y, end.y, .5)), normal, -bend - fan);
          const curves = [
            [start, shoulderIn, add(throat, axis, -55), throat],
            [throat, add(throat, axis, 55), shoulderOut, end],
          ];
          lane.setAttribute("d", pathData(curves));
          lane.style.display = wide || laneIndex === 2 ? "" : "none";
          if (laneIndex === 2) centralCurves = curves;
        });
        group.haze.setAttribute("d", pathData(centralCurves));
        const table = distanceTable(centralCurves);
        group.markers.forEach((marker, markerIndex) => {
          const active = wide || markerIndex < 3;
          marker.style.display = active ? "" : "none";
          const phase = (markerIndex / (wide ? 5 : 3) + time / (index ? 8.4 : 7.2) + index * .19) % 1;
          const p = pointAlong(table, direction === 1 ? phase : 1 - phase);
          marker.setAttribute("cx", p.x.toFixed(2)); marker.setAttribute("cy", p.y.toFixed(2));
          marker.setAttribute("opacity", reduced.matches ? ".45" : String(.25 + .75 * Math.sin(phase * Math.PI)));
        });
        group.endpoints.forEach((anchor, n) => {
          anchor.setAttribute("cx", [start, end][n].x.toFixed(2)); anchor.setAttribute("cy", [start, end][n].y.toFixed(2));
        });
      });
      draws += 1; lastEndpoints = endpoints; lastCore = center;
    };
    const tick = timestamp => {
      frame = 0;
      if (!running()) { last = 0; sync(); return; }
      if (last) elapsed += Math.min((timestamp - last) / 1000, .05);
      last = timestamp;
      render();
      frame = requestAnimationFrame(tick);
    };
    const sync = () => {
      if (destroyed) return;
      if (frame) cancelAnimationFrame(frame);
      frame = 0; last = 0;
      svg.dataset.motion = reduced.matches ? "reduced" : running() ? "running" : "paused";
      if (reduced.matches) render();
      if (running()) frame = requestAnimationFrame(tick);
    };
    const resize = new ResizeObserver(() => { readGeometry(); render(); sync(); });
    const intersection = new IntersectionObserver(entries => {
      const entry = entries.find(item => item.target === host);
      if (entry) inView = entry.isIntersecting && entry.intersectionRatio > 0;
      sync();
    });
    const hide = () => { pageActive = false; sync(); };
    const show = () => { pageActive = true; inView = false; intersection.unobserve(host); intersection.observe(host); sync(); };
    document.addEventListener("visibilitychange", sync);
    window.addEventListener("pagehide", hide);
    window.addEventListener("pageshow", show);
    reduced.addEventListener("change", sync);
    resize.observe(host); intersection.observe(host); readGeometry(); render();
    const controller = {
      setVariant(next) { if (!variants.has(next)) throw new RangeError("Unknown stream variant."); variant = next; svg.dataset.variant = next; render(); },
      pause() { manualPaused = true; sync(); },
      resume() { manualPaused = false; sync(); },
      inspect() { return { variant, seed, elapsed, draws, motion: svg.dataset.motion, endpoints: lastEndpoints, core: lastCore }; },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        cancelAnimationFrame(frame); resize.disconnect(); intersection.disconnect();
        document.removeEventListener("visibilitychange", sync);
        window.removeEventListener("pagehide", hide); window.removeEventListener("pageshow", show);
        reduced.removeEventListener("change", sync);
        svg.remove(); host.classList.remove("obs-streams-host"); mounted.delete(host);
      },
    };
    mounted.set(host, controller);
    return controller;
  }
  window.ObservatoryStreams = Object.freeze({ mount });
})();
