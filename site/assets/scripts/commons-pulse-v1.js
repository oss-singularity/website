(() => {
  "use strict";
  const board = document.querySelector("#commons-pulse");
  const status = document.querySelector("#pulse-status");
  if (!board || !status) return;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  status.textContent = "Reading the shared Workshop…";
  Promise.all(["missions", "contributions"].map(async path => {
    const response = await fetch(`/api/v1/${path}?limit=3`, {signal: controller.signal, credentials: "omit", cache: "no-store", headers: {Accept: "application/json"}});
    if (!response.ok) throw new Error("Unavailable");
    const data = await response.json();
    if (!Array.isArray(data.items)) throw new Error("Invalid response");
    return data.items;
  })).then(groups => {
    const items = groups.flat().sort((a, b) => String(b.published_at || b.created_at).localeCompare(String(a.published_at || a.created_at))).slice(0, 3);
    board.replaceChildren();
    items.forEach(item => {
      const card = document.createElement("a");
      card.className = "pulse-card";
      const id = typeof item.id === "string" && /^[a-z0-9][a-z0-9-]{0,79}$/.test(item.id) ? item.id : null;
      card.href = item.kind === "mission" && id ? `/singularity/?mission=${encodeURIComponent(id)}` : id ? `/workshop/?signal=${encodeURIComponent(id)}` : "/workshop/";
      const kind = document.createElement("span");
      kind.className = "micro-label";
      kind.textContent = item.kind === "mission" ? item.provenance === "seed" ? item.id === "build-the-commons" ? "Founding mission" : "Editorial mission template" : "Community mission" : item.kind === "field-note" ? "Field note" : "Project signal";
      const title = document.createElement("h3");
      title.textContent = item.title;
      const summary = document.createElement("p");
      summary.textContent = item.summary;
      const action = document.createElement("span");
      action.className = "journey-link";
      action.textContent = "Explore this signal →";
      card.append(kind, title, summary, action);
      board.append(card);
    });
    status.textContent = items.length ? "Published signals · Read directly from the shared service" : "The board is open. Bring the first community signal.";
  }).catch(() => { status.textContent = "The live board is temporarily unavailable. You can still explore the Atlas and Mission Lab."; }).finally(() => clearTimeout(timer));
  addEventListener("pagehide", () => controller.abort(), {once: true});
})();
