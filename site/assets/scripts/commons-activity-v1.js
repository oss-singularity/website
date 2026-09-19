(() => {
  "use strict";
  const panel = document.getElementById("commons-activity");
  if (!panel) return;
  const status = document.getElementById("activity-status");
  const content = document.getElementById("activity-content");
  const refresh = document.getElementById("activity-refresh");
  const controllers = new Set();
  let loading = false;
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const element = (tag, text) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const svgElement = (tag, attrs, text) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const dayLabel = date => new Intl.DateTimeFormat("en", {weekday: "short", timeZone: "UTC"}).format(new Date(`${date}T00:00:00Z`));
  const coordinationKeys = ["projects_total", "projects_open", "projects_closed", "milestones_open",
    "milestones_done", "commitments_confirmed", "commitments_completed", "deliveries_total"];
  // The coordination block arrived after this panel; an older snapshot stays valid without it.
  const coordinationCounts = value => !value || coordinationKeys.every(key => count(value[key]));
  const valid = data => {
    if (!data || data.window?.days !== 7 || data.window?.timezone !== "UTC" || !Number.isFinite(Date.parse(data.generated_at)) ||
        !data.totals || !["missions", "contributions", "offers", "needs"].every(key => count(data.totals[key])) ||
        !count(data.editorial_missions) || data.editorial_missions > data.totals.missions || !Array.isArray(data.days) || data.days.length !== 7 ||
        !coordinationCounts(data.coordination)) return false;
    const today = Math.floor(Date.parse(data.generated_at) / 86400000) * 86400000;
    return data.days.every((day, index) => day && day.date === new Date(today - (6 - index) * 86400000).toISOString().slice(0, 10) &&
      count(day.contributions) && count(day.participations) && count(day.contributions + day.participations));
  };
  const render = data => {
    const totals = document.getElementById("activity-totals");
    totals.replaceChildren();
    [["missions", "Published missions"], ["contributions", "Work & evidence"], ["offers", "Open offers"], ["needs", "Open needs"]].forEach(([key, label]) => {
      const group = element("div");
      group.append(element("dt", label), element("dd", data.totals[key].toLocaleString("en")));
      totals.append(group);
    });
    if (data.coordination) {
      [["projects_total", "Coordinated projects"], ["milestones_done", "Milestones done"],
       ["commitments_completed", "Completed commitments"], ["deliveries_total", "Delivered revisions"]]
        .forEach(([key, label]) => {
          const group = element("div");
          group.className = "activity-coordination";
          group.append(element("dt", label), element("dd", data.coordination[key].toLocaleString("en")));
          totals.append(group);
        });
    }
    document.getElementById("activity-editorial").textContent = `${data.editorial_missions} of these missions are editorial starting points. Needs and offers are invitations, not assigned work.`;
    const coordination = document.getElementById("activity-coordination");
    if (data.coordination) {
      coordination.textContent = `${data.coordination.projects_open.toLocaleString("en")} open · ${data.coordination.projects_closed.toLocaleString("en")} closed coordinated projects. Delivered revisions count records, not verified artifacts.`;
    } else {
      coordination.textContent = "Coordinated project counters are not part of this API snapshot yet.";
    }
    const chart = document.getElementById("activity-chart");
    const table = document.getElementById("activity-days");
    chart.replaceChildren();
    table.replaceChildren();
    const values = data.days.map(day => day.contributions + day.participations);
    const maximum = Math.max(1, ...values);
    // Two smooth flow curves (same monotone envelope as the home journey
    // chart): they never overshoot a daily step, so the drawing stays honest.
    const W = 560, BASE = 118, TOP = 22, LEFT = 14, RIGHT = 546;
    const x = index => LEFT + index * (RIGHT - LEFT) / (data.days.length - 1);
    const y = value => BASE - value / maximum * (BASE - TOP);
    const fmt = value => Math.round(value * 10) / 10;
    const monotone = points => {
      const n = points.length;
      let d = `M${fmt(points[0].x)} ${fmt(points[0].y)}`;
      if (n < 2) return d;
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
      for (let i = 0; i < n - 1; i += 1) {
        const h = dx[i];
        d += ` C${fmt(points[i].x + h / 3)} ${fmt(points[i].y + tangent[i] * h / 3)} ${fmt(points[i + 1].x - h / 3)} ${fmt(points[i + 1].y - tangent[i + 1] * h / 3)} ${fmt(points[i + 1].x)} ${fmt(points[i + 1].y)}`;
      }
      return d;
    };
    chart.append(svgElement("line", {x1: LEFT, x2: RIGHT, y1: BASE, y2: BASE, class: "activity-baseline"}));
    const series = [
      { key: "contributions", pick: day => day.contributions },
      { key: "participations", pick: day => day.participations },
    ];
    for (const { key, pick } of series) {
      const points = data.days.map((day, index) => ({ x: x(index), y: y(pick(day)) }));
      chart.append(svgElement("path", { d: `${monotone(points)} L${fmt(points.at(-1).x)} ${BASE} L${fmt(points[0].x)} ${BASE} Z`, class: `activity-area activity-area-${key}` }));
      chart.append(svgElement("path", { d: monotone(points), class: `activity-line activity-line-${key}` }));
    }
    data.days.forEach((day, index) => {
      const group = svgElement("g", {});
      group.append(svgElement("title", {}, `${day.date} UTC: ${day.contributions} work contributions, ${day.participations} needs or offers`));
      for (const { key, pick } of series) {
        const value = pick(day);
        if (!value) continue;
        const dot = svgElement("circle", { cx: fmt(x(index)), cy: fmt(y(value)), r: 3.4, class: `activity-dot activity-dot-${key}` });
        const tip = svgElement("title", {});
        tip.textContent = `${dayLabel(day.date)} · ${value} ${key === "contributions" ? "work & evidence" : "needs & offers"}`;
        dot.append(tip);
        group.append(dot);
      }
      if (values[index]) group.append(svgElement("text", { x: fmt(x(index)), y: fmt(y(values[index]) - 11), class: "activity-count" }, values[index]));
      group.append(svgElement("text", { x: fmt(x(index)), y: BASE + 26, class: "activity-day" }, dayLabel(day.date)));
      chart.append(group);
      const row = element("tr");
      const date = element("th", day.date);
      date.scope = "row";
      row.append(date, element("td", day.contributions), element("td", day.participations));
      table.append(row);
    });
    const total = values.reduce((sum, value) => sum + value, 0);
    if (!total) chart.append(svgElement("text", { x: 280, y: 66, class: "activity-empty" }, "Quiet this week — the next entry could be yours."));
    const summaryLine = document.getElementById("activity-summary");
    summaryLine.replaceChildren();
    if (total) {
      summaryLine.textContent = `${total.toLocaleString("en")} currently public community entries were published in this seven-day window.`;
    } else {
      summaryLine.append(element("span", "No community entries are currently public in this seven-day window. "));
      const link = element("a", "Find a shared mission →");
      link.href = "/singularity/";
      summaryLine.append(link);
    }
    document.getElementById("activity-window").textContent = `${data.days[0].date} – ${data.days[6].date} · UTC`;
    content.hidden = false;
  };
  const load = async () => {
    if (loading) return;
    loading = true;
    refresh.disabled = true;
    status.textContent = "Reading the public Commons…";
    const controller = new AbortController();
    controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch("/api/v1/activity", {signal: controller.signal, credentials: "omit", cache: "no-store", headers: {Accept: "application/json"}});
      if (!response.ok) throw new Error("Unavailable");
      const data = await response.json();
      if (!valid(data)) throw new Error("Invalid activity");
      render(data);
      status.textContent = `Public snapshot · ${new Intl.DateTimeFormat("en", {hour: "2-digit", minute: "2-digit", timeZone: "UTC"}).format(new Date(data.generated_at))} UTC`;
    } catch {
      status.textContent = content.hidden
        ? "The public overview could not be loaded. Try refreshing; the rest of the home remains available."
        : "Refresh failed. The earlier snapshot is still shown; its counts may have changed.";
    } finally {
      clearTimeout(timeout);
      controllers.delete(controller);
      loading = false;
      refresh.disabled = false;
    }
  };
  refresh.addEventListener("click", load);
  addEventListener("pagehide", () => controllers.forEach(controller => controller.abort()), {once: true});
  load();

  // The mission's curve: cumulative coordination growth drawn from public
  // records. Unlike the seven-day window it is never empty while the commons
  // grows, so the publications column always carries a living graph.
  const growthPanel = document.getElementById("activity-growth");
  const growthSvg = document.getElementById("activity-growth-chart");
  const growthWindow = document.getElementById("activity-growth-window");
  const growthSummary = document.getElementById("activity-growth-summary");
  const idPattern = /^[a-z0-9][a-z0-9-]{0,79}$/;
  const stamp = value => typeof value === "string" && value.length <= 32 && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
  const moment = value => new Intl.DateTimeFormat("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }).format(new Date(value));
  const fmt = value => Math.round(value * 10) / 10;
  const growthSeries = [
    { key: "milestones", label: "Milestones completed" },
    { key: "commitments", label: "Commitments accepted" },
    { key: "projects", label: "Projects coordinated" },
  ];
  const growthValidList = data => data && Array.isArray(data.items) && data.items.length > 0 && data.items.length <= 50
    && (data.next_cursor === null || (typeof data.next_cursor === "string" && data.next_cursor.length <= 256))
    && data.items.every(item => item && idPattern.test(item.id) && typeof item.mission_id === "string" && item.mission_id.length <= 80
      && ["open", "closed", "cancelled"].includes(item.status) && stamp(item.created_at) !== null);
  const growthValidDetail = data => data && idPattern.test(data.id) && ["open", "closed", "cancelled"].includes(data.status)
    && stamp(data.created_at) !== null && Array.isArray(data.milestones) && data.milestones.length <= 20
    && Array.isArray(data.commitments) && data.commitments.length <= 30
    && data.milestones.every(item => item && ["open", "done", "cancelled"].includes(item.status) && stamp(item.updated_at) !== null)
    && data.commitments.every(item => item && ["offered", "confirmed", "completed", "ended", "withdrawn", "declined", "cancelled"].includes(item.status) && stamp(item.updated_at) !== null);
  // The same monotone envelope the journey chart draws: smooth curves that
  // never overshoot a cumulative step.
  const growthMonotone = points => {
    const n = points.length;
    let d = `M${fmt(points[0].x)} ${fmt(points[0].y)}`;
    if (n < 2) return d;
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
    for (let i = 0; i < n - 1; i += 1) {
      const h = dx[i];
      d += ` C${fmt(points[i].x + h / 3)} ${fmt(points[i].y + tangent[i] * h / 3)} ${fmt(points[i + 1].x - h / 3)} ${fmt(points[i + 1].y - tangent[i + 1] * h / 3)} ${fmt(points[i + 1].x)} ${fmt(points[i + 1].y)}`;
    }
    return d;
  };
  const loadGrowth = async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const listResponse = await fetch("/api/v1/projects?limit=50", { signal: controller.signal, credentials: "omit", cache: "no-store", headers: { Accept: "application/json" } });
      if (!listResponse.ok) throw new Error("Unavailable");
      const list = await listResponse.json();
      if (!growthValidList(list)) throw new Error("Invalid projects");
      const details = await Promise.all(list.items.map(async project => {
        const response = await fetch(`/api/v1/projects/${encodeURIComponent(project.id)}`, { signal: controller.signal, credentials: "omit", cache: "no-store", headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error("Unavailable");
        const detail = await response.json();
        if (!growthValidDetail(detail) || detail.id !== project.id) throw new Error("Invalid project");
        return detail;
      }));
      const series = {};
      for (const { key } of growthSeries) series[key] = [];
      for (const project of details) {
        series.projects.push(stamp(project.created_at));
        for (const milestone of project.milestones) if (milestone.status === "done") series.milestones.push(stamp(milestone.updated_at));
        for (const commitment of project.commitments) if (commitment.status === "completed") series.commitments.push(stamp(commitment.updated_at));
      }
      const totals = {};
      for (const { key } of growthSeries) {
        totals[key] = series[key].length;
        series[key].sort((a, b) => a - b);
        let last = -Infinity;
        series[key] = series[key].map((value, index) => {
          if (value <= last) value = last + 1;
          last = value;
          return { t: value, count: index + 1 };
        });
      }
      const every = growthSeries.flatMap(({ key }) => series[key]).map(point => point.t);
      const start = Math.min(...every);
      const end = Math.max(Date.now(), Math.max(...every) + 1);
      const x = value => 30 + (value - start) / (end - start) * (548 - 30);
      const maximum = Math.max(1, ...growthSeries.map(({ key }) => series[key].length));
      const y = value => 138 - value / maximum * (138 - 14);
      growthSvg.replaceChildren();
      growthSvg.append(svgElement("line", { x1: 30, x2: 548, y1: 138, y2: 138, class: "activity-baseline" }));
      growthSvg.append(svgElement("line", { x1: 30, x2: 548, y1: fmt(y(maximum)), y2: fmt(y(maximum)), class: "activity-grid" }));
      growthSvg.append(svgElement("text", { x: 24, y: fmt(y(maximum) + 4), class: "activity-grid-count", "text-anchor": "end" }, String(maximum)));
      growthSvg.append(svgElement("text", { x: 24, y: 142, class: "activity-grid-count", "text-anchor": "end" }, "0"));
      for (const { key, label } of growthSeries) {
        if (!series[key].length) continue;
        const points = series[key].map(point => ({ x: fmt(x(point.t)), y: fmt(y(point.count)) }));
        // a cumulative count persists until today
        const through = [...points, { x: 548, y: points.at(-1).y }];
        growthSvg.append(svgElement("path", { d: `${growthMonotone(through)} L548 138 L${fmt(points[0].x)} 138 Z`, class: `activity-area activity-area-${key}` }));
        growthSvg.append(svgElement("path", { d: growthMonotone(through), class: `activity-line activity-line-${key}` }));
        series[key].forEach(point => {
          const dot = svgElement("circle", { cx: fmt(x(point.t)), cy: fmt(y(point.count)), r: 3, class: `activity-dot activity-dot-${key}` });
          const tip = svgElement("title", {});
          tip.textContent = `${moment(point.t)} UTC — ${label} ${point.count}`;
          dot.append(tip);
          growthSvg.append(dot);
        });
      }
      const startedDay = new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(start));
      growthSvg.append(svgElement("text", { x: 30, y: 160, class: "activity-axis" }, startedDay));
      growthSvg.append(svgElement("text", { x: 548, y: 160, class: "activity-axis", "text-anchor": "end" }, "today"));
      growthWindow.textContent = `${startedDay} – today · UTC`;
      growthSummary.textContent = `${totals.milestones} milestones completed · ${totals.commitments} commitments accepted · ${totals.projects} projects coordinated — every point a public record.`;
      growthPanel.hidden = false;
    } catch {
      growthPanel.hidden = true; // the shared home stays usable without the curve
    } finally { clearTimeout(timeout); }
  };
  loadGrowth();
})();
