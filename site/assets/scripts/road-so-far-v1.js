(() => {
  "use strict";
  const panel = document.getElementById("road-so-far");
  if (!panel) return;
  const status = document.getElementById("road-status");
  const content = document.getElementById("road-content");
  const chartBox = document.getElementById("road-chart");
  const summary = document.getElementById("road-summary");
  const SVG = "http://www.w3.org/2000/svg";
  const idPattern = /^[a-z0-9][a-z0-9-]{0,79}$/;
  const text = (value, max) => typeof value === "string" && [...value].length <= max;
  const time = value => typeof value === "string" && value.length <= 32 && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const reducedMotion = () => { try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return true; } };

  const validList = data => {
    if (!data || !Array.isArray(data.items) || data.items.length > 50
      || !(data.next_cursor === null || text(data.next_cursor, 256))) return false;
    return data.items.every(item => item && idPattern.test(item.id) && text(item.mission_id, 80)
      && ["open", "closed", "cancelled"].includes(item.status) && time(item.created_at) !== null);
  };
  const validDetail = data => {
    if (!data || !idPattern.test(data.id) || !["open", "closed", "cancelled"].includes(data.status)
      || time(data.created_at) === null || !Array.isArray(data.milestones) || data.milestones.length > 20
      || !Array.isArray(data.commitments) || data.commitments.length > 30) return false;
    const stamp = value => time(value) !== null;
    return data.milestones.every(m => m && ["open", "done", "cancelled"].includes(m.status) && stamp(m.updated_at))
      && data.commitments.every(c => c && ["offered", "confirmed", "completed", "ended", "withdrawn", "declined", "cancelled"].includes(c.status) && stamp(c.updated_at));
  };

  const svgElement = (tag, attrs) => {
    const node = document.createElementNS(SVG, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  };
  const fmt = value => Math.round(value * 10) / 10;
  const day = value => new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(value));
  // Monotone cubic interpolation (Fritsch–Carlson): smooth curves that never
  // overshoot a cumulative step — the drawn line stays an honest envelope.
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

  const assemble = projects => {
    const series = { milestones: [], commitments: [], projects: [] };
    for (const project of projects) {
      series.projects.push({ t: time(project.created_at) });
      for (const milestone of project.milestones) if (milestone.status === "done") series.milestones.push({ t: time(milestone.updated_at) });
      for (const commitment of project.commitments) if (commitment.status === "completed") series.commitments.push({ t: time(commitment.updated_at) });
    }
    const totals = {};
    const firstStart = Math.min(...projects.map(project => time(project.created_at)));
    for (const { key } of SERIES) {
      totals[key] = series[key].length;
      series[key].sort((a, b) => a.t - b.t);
      if (!series[key].length) series[key].push({ t: firstStart, count: 0, anchor: true });
      let last = -Infinity;
      series[key].forEach((point, index) => {
        if (point.t <= last) point.t = last + 1;
        last = point.t;
        if (!point.anchor) point.count = index + 1;
      });
    }
    return { series, totals };
  };

  let started = false;
  const load = async () => {
    if (started) return;
    started = true;
    status.textContent = "Reading the public journey…";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const listResponse = await fetch("/api/v1/projects?limit=50", { signal: controller.signal, credentials: "omit", cache: "no-store", headers: { Accept: "application/json" } });
      if (!listResponse.ok) throw new Error("Unavailable");
      const list = await listResponse.json();
      if (!validList(list)) throw new Error("Invalid list");
      if (!list.items.length) {
        status.textContent = "The curve begins with the first coordinated project. The roadmap tells the plan meanwhile.";
        return;
      }
      const details = await Promise.all(list.items.map(async project => {
        const response = await fetch(`/api/v1/projects/${encodeURIComponent(project.id)}`, { signal: controller.signal, credentials: "omit", cache: "no-store", headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error("Unavailable");
        const detail = await response.json();
        if (!validDetail(detail) || detail.id !== project.id) throw new Error("Invalid project");
        return detail;
      }));
      const { series, totals } = assemble(details);
      chartBox.replaceChildren(buildChart(series));
      const spanDays = Math.max(1, Math.round((Date.now() - Math.min(...details.map(project => time(project.created_at)))) / 86400000));
      summary.textContent = `${details.length} coordinated projects · ${totals.milestones} milestones completed · ${totals.commitments} commitments accepted — public records${spanDays === 1 ? " within one day" : `, across the first ${spanDays} days`}.`;
      content.hidden = false;
      status.textContent = "Every point is a public record — hover a dot for its moment.";
      if (!reducedMotion()) {
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
    } catch {
      status.textContent = "The journey chart could not be loaded. The shared home above and the public API (/.well-known/agent-home.json) remain available.";
    } finally { clearTimeout(timeout); }
  };
  // The home already reads the public activity eagerly; the journey chart
  // joins that first wave instead of gating on scroll observation, so the
  // curve is ready by the time a reader reaches it.
  load();
})();
