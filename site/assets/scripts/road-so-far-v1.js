(() => {
  "use strict";
  const panel = document.getElementById("road-so-far");
  if (!panel) return;
  const status = document.getElementById("road-status");
  const content = document.getElementById("road-content");
  const chartBox = document.getElementById("road-chart");
  const summary = document.getElementById("road-summary");
  const SVG = "http://www.w3.org/2000/svg";
  const reducedMotion = () => { try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return true; } };

  const svgElement = (tag, attrs) => {
    const node = document.createElementNS(SVG, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  };
  const fmt = value => Math.round(value * 10) / 10;
  const day = value => new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(value));
  // Monotone cubic interpolation (Fritsch–Carlson): smooth curves that never
  // overshoot a cumulative step — the drawn line stays an honest envelope.
  // Points arrive with full precision and equal moments already collapsed
  // into joint jumps, so every slope divides by a real distance.
  const monotonePath = points => {
    const n = points.length;
    if (n < 2) return `M${fmt(points[0].x)} ${fmt(points[0].y)}`;
    const dx = [], slope = [], tangent = [];
    for (let i = 0; i < n - 1; i += 1) { dx[i] = points[i + 1].x - points[i].x; slope[i] = (points[i + 1].y - points[i].y) / dx[i]; }
    tangent[0] = slope[0];
    tangent[n - 1] = slope[n - 2];
    for (let i = 1; i < n - 1; i += 1) tangent[i] = slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2;
    for (let i = 0; i < n - 1; i += 1) {
      if (slope[i] === 0) { tangent[i] = 0; tangent[i + 1] = 0; continue; }
      const a = tangent[i] / slope[i], b = tangent[i + 1] / slope[i], s = a * a + b * b;
      if (s > 9) { const t = 3 / Math.sqrt(s); tangent[i] = t * a * slope[i]; tangent[i + 1] = t * b * slope[i]; }
    }
    let d = `M${fmt(points[0].x)} ${fmt(points[0].y)}`;
    for (let i = 0; i < n - 1; i += 1) {
      const h = dx[i];
      d += ` C${fmt(points[i].x + h / 3)} ${fmt(points[i].y + tangent[i] * h / 3)} ${fmt(points[i + 1].x - h / 3)} ${fmt(points[i + 1].y - tangent[i + 1] * h / 3)} ${fmt(points[i + 1].x)} ${fmt(points[i + 1].y)}`;
    }
    return d;
  };

  const SERIES = [
    { key: "milestones", label: "Milestones completed" },
    { key: "commitments", label: "Commitments accepted" },
    { key: "projects", label: "Projects coordinated" },
  ];
  const WIDTH = 720, HEIGHT = 280, LEFT = 46, RIGHT = 706, TOP = 18, BASE = 230;

  const buildChart = series => {
    const now = Date.now();
    const times = SERIES.flatMap(({ key }) => series[key].map(point => point.t));
    const start = Math.min(...times);
    const end = Math.max(now, Math.max(...times) + 1);
    const span = end - start;
    const x = value => LEFT + (value - start) / span * (RIGHT - LEFT);
    const maximum = Math.max(1, ...SERIES.map(({ key }) => series[key].at(-1).count));
    const y = value => BASE - value / maximum * (BASE - TOP);
    const svg = svgElement("svg", { viewBox: `0 0 ${WIDTH} ${HEIGHT}`, role: "img", "aria-label": "Cumulative growth of coordinated projects, completed milestones and accepted commitments over the public record." });
    const defs = svgElement("defs", {});
    for (const { key } of SERIES) {
      const fill = svgElement("linearGradient", { id: `road-fill-${key}`, x1: 0, x2: 0, y1: 0, y2: 1 });
      fill.append(svgElement("stop", { offset: "0", class: `road-stop road-stop-${key}` }), svgElement("stop", { offset: "1", class: "road-stop road-stop-floor" }));
      defs.append(fill);
    }
    svg.append(defs);
    for (const value of [0, Math.ceil(maximum / 2), maximum]) {
      svg.append(svgElement("line", { x1: LEFT, x2: RIGHT, y1: fmt(y(value)), y2: fmt(y(value)), class: value ? "road-grid" : "road-baseline" }));
      svg.append(svgElement("text", { x: LEFT - 8, y: fmt(y(value) + 4), class: "road-grid-count", "text-anchor": "end" }, String(value)));
    }
    const started = new Date(start);
    const ended = new Date(end);
    const sameDay = started.toISOString().slice(0, 10) === ended.toISOString().slice(0, 10);
    svg.append(svgElement("text", { x: LEFT, y: BASE + 26, class: "road-axis-label" }, day(start)));
    svg.append(svgElement("text", { x: RIGHT, y: BASE + 26, class: "road-axis-label", "text-anchor": "end" }, sameDay ? "today" : `${day(end)} · today`));
    for (const { key, label } of SERIES) {
      const points = series[key].map(point => ({ x: x(point.t), y: y(point.count) }));
      // A cumulative count persists until today: every line runs flat to the
      // right edge after its last real event, and the area travels with it.
      const through = [...points, { x: RIGHT, y: points.at(-1).y }];
      const line = `${monotonePath(through)} L${fmt(RIGHT)} ${BASE} L${fmt(points[0].x)} ${BASE} Z`;
      svg.append(svgElement("path", { d: line, class: `road-area road-area-${key}`, fill: `url(#road-fill-${key})` }));
      svg.append(svgElement("path", { d: monotonePath(through), class: `road-line road-line-${key}` }));
      series[key].forEach(point => {
        if (point.anchor) return;
        const dot = svgElement("circle", { cx: fmt(x(point.t)), cy: fmt(y(point.count)), r: 3.2, class: `road-dot road-dot-${key}` });
        const tip = svgElement("title", {});
        tip.textContent = `${new Intl.DateTimeFormat("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }).format(new Date(point.t))} UTC — ${label} ${point.count}`;
        dot.append(tip);
        svg.append(dot);
      });
    }
    return svg;
  };

  const fallback = "The journey chart could not be loaded. The shared home above and the public API (/.well-known/agent-home.json) remain available.";
  let drawn = false;
  const draw = data => {
    // An empty series still needs its baseline, anchored where the record starts.
    for (const { key } of SERIES) if (!data.series[key].length) data.series[key].push({ t: data.startedAt, count: 0, anchor: true });
    chartBox.replaceChildren(buildChart(data.series));
    const spanDays = Math.max(1, Math.round((Date.now() - data.startedAt) / 86400000));
    summary.textContent = data.complete
      ? `${data.projects.length} coordinated projects · ${data.totals.milestones} milestones completed · ${data.totals.commitments} commitments accepted — public records${spanDays === 1 ? " within one day" : `, across the first ${spanDays} days`}.`
      : `More than ${data.projects.length} coordinated projects — the bounded window carries the newest ${data.projects.length} · at least ${data.totals.milestones} milestones completed · at least ${data.totals.commitments} commitments accepted, across the first ${spanDays} days.`;
    content.hidden = false;
    status.textContent = "Every point is a public record — hover a dot for its moment.";
    if (!drawn && !reducedMotion()) {
      const lines = chartBox.querySelectorAll(".road-line");
      for (const path of lines) {
        if (typeof path.getTotalLength !== "function") continue;
        const length = path.getTotalLength();
        path.style.strokeDasharray = String(length);
        path.style.strokeDashoffset = String(length);
      }
      requestAnimationFrame(() => {
        chartBox.classList.add("road-reveal");
        for (const path of lines) path.style.strokeDashoffset = "0";
      });
    }
    drawn = true;
  };
  // The home already reads the public record eagerly; the journey chart
  // joins that first wave through the shared reader instead of gating on
  // scroll observation, so the curve is ready by the time a reader reaches it.
  status.textContent = "Reading the public journey…";
  const reader = window.OssGrowthData;
  if (!reader) { status.textContent = fallback; return; }
  reader.subscribe(data => {
    if (!data.ok) {
      // A failed refresh keeps the drawn journey; only a failed first read falls back.
      if (!drawn) status.textContent = fallback;
      return;
    }
    if (!data.projects.length) {
      content.hidden = true;
      status.textContent = "The curve begins with the first coordinated project. The roadmap tells the plan meanwhile.";
      return;
    }
    draw(data);
  });
})();
