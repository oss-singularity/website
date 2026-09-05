(() => {
  "use strict";
  const artwork = document.querySelector(".hub-observatory .constellation");
  const core = artwork?.querySelector(".constellation-core img");
  if (!core || typeof core.animate !== "function" || typeof IntersectionObserver !== "function" || typeof matchMedia !== "function") return;
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  if (typeof reducedMotion.addEventListener !== "function") return;
  let inView = false;
  let pageActive = true;
  let animation = null;
  let position = "translate(0, 0) rotate(0deg)";
  let direction = Math.random() * Math.PI * 2;
  const shouldMove = () => inView && pageActive && !document.hidden && !reducedMotion.matches;
  const wander = () => {
    const previous = animation;
    direction += Math.PI * (.5 + Math.random());
    const radius = .7 + Math.random() * .3;
    const x = (Math.cos(direction) * radius).toFixed(4);
    const y = (Math.sin(direction) * radius).toFixed(4);
    const next = `translate(calc(var(--core-roam) * ${x}), calc(var(--core-roam) * ${y})) rotate(${(Number(x) * 1.2).toFixed(3)}deg)`;
    animation = core.animate([{transform: position}, {transform: next}], {
      duration: 10000 + Math.random() * 4000, easing: "ease-in-out", fill: "forwards"
    });
    animation.id = "constellation-wander";
    position = next;
    const segment = animation;
    segment.onfinish = () => { if (animation === segment && !reducedMotion.matches) wander(); };
    if (!shouldMove()) animation.pause();
    // The new segment starts at the old endpoint before the old fill is removed.
    previous?.cancel();
  };
  const updateMotion = () => {
    const running = shouldMove();
    artwork.dataset.coreMotion = running ? "running" : "paused";
    if (reducedMotion.matches) {
      animation?.cancel();
      animation = null;
      position = "translate(0, 0) rotate(0deg)";
    } else if (running) {
      if (animation) animation.play();
      else wander();
    } else animation?.pause();
  };
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.target === artwork) inView = entry.isIntersecting && entry.intersectionRatio > 0;
    }
    updateMotion();
  });
  observer.observe(artwork);
  updateMotion();
  reducedMotion.addEventListener("change", updateMotion);
  document.addEventListener("visibilitychange", updateMotion);
  addEventListener("pagehide", () => { pageActive = false; updateMotion(); });
  addEventListener("pageshow", () => {
    // Restored scroll positions need a fresh visibility observation before resuming.
    pageActive = true;
    inView = false;
    observer.unobserve(artwork);
    observer.observe(artwork);
    updateMotion();
  });
})();

(() => {
  "use strict";
  const host = document.querySelector(".hub-observatory .constellation[data-energy-streams]");
  const core = host?.querySelector(".constellation-core img");
  const orbitNodes = host ? [...host.querySelectorAll(".constellation-orbit")].slice(0, 2) : [];
  if (!core || orbitNodes.length !== 2 || typeof ResizeObserver !== "function"
    || typeof IntersectionObserver !== "function" || typeof DOMMatrixReadOnly !== "function"
    || typeof matchMedia !== "function" || host.querySelector(".constellation-streams")) return;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  if (typeof reduced.addEventListener !== "function") return;
  const seed = 60;
  const node = (tag, attrs = {}) => {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attrs)) element.setAttribute(name, value);
    return element;
  };
  const mix = (a, b, t) => a + (b - a) * t;
  const point = (x, y) => ({ x, y });
  const add = (a, b, weight = 1) => point(a.x + b.x * weight, a.y + b.y * weight);
  const hash = (key, index) => {
    let value = Math.imul(key ^ index, 0x45d9f3b);
    value = Math.imul(value ^ value >>> 16, 0x45d9f3b);
    return ((value ^ value >>> 16) >>> 0) / 4294967295 * 2 - 1;
  };
  // Smooth seeded targets change direction without a velocity discontinuity.
  const noise = (key, seconds, period) => {
    const position = seconds / period, step = Math.floor(position), f = position - step;
    const eased = f * f * f * (f * (f * 6 - 15) + 10);
    return mix(hash(key, step), hash(key, step + 1), eased);
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
  const svg = node("svg", { class: "constellation-streams", viewBox: "0 0 600 600", "aria-hidden": "true", focusable: "false" });
  const groups = [0, 1].map(index => {
    const group = node("g", { class: index ? "constellation-stream stream-pink" : "constellation-stream" });
    const haze = node("path", { class: "stream-haze", fill: "none" });
    const line = node("path", { class: "stream-line", fill: "none" });
    const markers = Array.from({ length: 3 }, () => node("circle", { class: "stream-packet", r: "2" }));
    const endpoints = [0, 1].map(() => node("circle", { class: "stream-anchor", r: "2.4" }));
    group.append(haze, line, ...markers, ...endpoints);
    svg.appendChild(group);
    return { haze, line, markers, endpoints };
  });
  host.prepend(svg);
  host.classList.add("has-constellation-streams");
  let geometry = [], frame = 0, elapsed = 0, last = 0, inView = false, pageActive = true;
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
  const running = () => inView && pageActive && !document.hidden && !reduced.matches;
  const render = () => {
    if (!geometry.length) return;
    // Follow the visible image center while keeping the caption and links fixed.
    const hostBox = host.getBoundingClientRect(), coreBox = core.getBoundingClientRect();
    if (!hostBox.width || !hostBox.height) return;
    const center = point((coreBox.x + coreBox.width / 2 - hostBox.x) * 600 / hostBox.width,
      (coreBox.y + coreBox.height / 2 - hostBox.y) * 600 / hostBox.height);
    const time = reduced.matches ? 0 : elapsed;
    groups.forEach((group, index) => {
      const direction = index === 0 ? 1 : -1;
      const drift = noise(seed + 101 + index * 317, time, 10.5 + index) * .68;
      const theta = (index === 0 ? 3.2 : 2.35) + drift;
      const spread = noise(seed + 607 + index * 193, time, 13.2) * .32;
      const start = geometry[index](theta), end = geometry[index](theta + Math.PI + spread);
      const length = Math.hypot(end.x - start.x, end.y - start.y);
      const axis = point((end.x - start.x) / length, (end.y - start.y) / length);
      const normal = point(-axis.y, axis.x);
      const wave = noise(seed + 907 + index * 313, time, 7.6) * 12;
      const throat = add(center, normal, direction * 12);
      const bend = direction * (36 + wave);
      const shoulderIn = add(point(mix(start.x, throat.x, .5), mix(start.y, throat.y, .5)), normal, bend);
      const shoulderOut = add(point(mix(throat.x, end.x, .5), mix(throat.y, end.y, .5)), normal, -bend);
      const curves = [
        [start, shoulderIn, add(throat, axis, -55), throat],
        [throat, add(throat, axis, 55), shoulderOut, end],
      ];
      const data = pathData(curves);
      group.line.setAttribute("d", data);
      group.haze.setAttribute("d", data);
      const table = distanceTable(curves);
      group.markers.forEach((marker, markerIndex) => {
        const phase = (markerIndex / 3 + time / (index ? 8.4 : 7.2) + index * .19) % 1;
        const p = pointAlong(table, direction === 1 ? phase : 1 - phase);
        marker.setAttribute("cx", p.x.toFixed(2)); marker.setAttribute("cy", p.y.toFixed(2));
        marker.setAttribute("opacity", reduced.matches ? ".45" : String(.25 + .75 * Math.sin(phase * Math.PI)));
      });
      group.endpoints.forEach((anchor, n) => {
        anchor.setAttribute("cx", [start, end][n].x.toFixed(2)); anchor.setAttribute("cy", [start, end][n].y.toFixed(2));
      });
    });
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
    if (frame) cancelAnimationFrame(frame);
    frame = 0; last = 0;
    if (reduced.matches) render();
    if (running()) frame = requestAnimationFrame(tick);
  };
  const resize = new ResizeObserver(() => { readGeometry(); render(); sync(); });
  const intersection = new IntersectionObserver(entries => {
    const entry = entries.find(item => item.target === host);
    if (entry) inView = entry.isIntersecting && entry.intersectionRatio > 0;
    sync();
  });
  document.addEventListener("visibilitychange", sync);
  window.addEventListener("pagehide", () => { pageActive = false; sync(); });
  window.addEventListener("pageshow", () => {
    pageActive = true;
    inView = false;
    intersection.unobserve(host);
    intersection.observe(host);
    sync();
  });
  reduced.addEventListener("change", sync);
  resize.observe(host);
  intersection.observe(host);
  readGeometry();
  render();
})();
