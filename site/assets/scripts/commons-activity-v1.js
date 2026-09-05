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
  const valid = data => {
    if (!data || data.window?.days !== 7 || data.window?.timezone !== "UTC" || !Number.isFinite(Date.parse(data.generated_at)) ||
        !data.totals || !["missions", "contributions", "offers", "needs"].every(key => count(data.totals[key])) ||
        !count(data.editorial_missions) || data.editorial_missions > data.totals.missions || !Array.isArray(data.days) || data.days.length !== 7) return false;
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
    document.getElementById("activity-editorial").textContent = `${data.editorial_missions} of these missions are editorial starting points. Needs and offers are invitations, not assigned work.`;
    const chart = document.getElementById("activity-chart");
    const table = document.getElementById("activity-days");
    chart.replaceChildren();
    table.replaceChildren();
    const values = data.days.map(day => day.contributions + day.participations);
    const maximum = Math.max(1, ...values);
    chart.append(svgElement("line", {x1: 8, x2: 552, y1: 125, y2: 125, class: "activity-baseline"}));
    data.days.forEach((day, index) => {
      const x = index * 80 + 23;
      const workHeight = day.contributions / maximum * 84;
      const participationHeight = day.participations / maximum * 84;
      const group = svgElement("g", {});
      group.append(svgElement("title", {}, `${day.date} UTC: ${day.contributions} work contributions, ${day.participations} needs or offers`));
      if (workHeight) group.append(svgElement("rect", {x, y: 125 - workHeight, width: 34, height: workHeight, class: "activity-work"}));
      if (participationHeight) group.append(svgElement("rect", {x, y: 125 - workHeight - participationHeight, width: 34, height: participationHeight, class: "activity-participation"}));
      group.append(svgElement("text", {x: x + 17, y: 115 - workHeight - participationHeight, class: "activity-count"}, values[index]));
      group.append(svgElement("text", {x: x + 17, y: 150, class: "activity-day"}, dayLabel(day.date)));
      chart.append(group);
      const row = element("tr");
      const date = element("th", day.date);
      date.scope = "row";
      row.append(date, element("td", day.contributions), element("td", day.participations));
      table.append(row);
    });
    const total = values.reduce((sum, value) => sum + value, 0);
    document.getElementById("activity-summary").textContent = total
      ? `${total.toLocaleString("en")} currently public community entries were published in this seven-day window.`
      : "No community entries are currently public in this seven-day window. A shared mission is a good place to begin.";
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
    const timeout = setTimeout(() => controller.abort(), 15000);
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
})();
