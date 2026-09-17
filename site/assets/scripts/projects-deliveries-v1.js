(() => {
  "use strict";
  if (!window.OssProjects || !document.getElementById("projects-list")) return;
  const { uuid, delivery, review } = window.OssProjects;
  const node = (tag, value, className) => { const n = document.createElement(tag); if (value !== undefined) n.textContent = value; if (className) n.className = className; return n; };
  const p = (value, className) => node("p", value, className);
  const unexpected = "The service returned an unexpected response. Refresh before acting.";
  let generation = 0;
  const request = async (path) => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, { credentials: "omit", cache: "no-store", redirect: "error", headers: { Accept: "application/json" } });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data?.error?.message === "string" ? data.error.message : `The service returned HTTP ${response.status}.`);
      return data;
    } finally { window.clearTimeout(timer); }
  };
  const reviewBadge = (reviews, revision) => {
    const match = reviews.find((r) => r.delivery_revision === revision);
    return match ? (match.decision === "accept" ? "Accepted" : "Revision requested") : null;
  };
  const renderInto = (host, projectId, milestoneId) => {
    const box = node("div", undefined, "project-dlv");
    const list = node("ul", undefined, "project-tree");
    const status = p("", "room-status");
    box.append(list, status);
    host.append(box);
    const run = async () => {
      list.replaceChildren();
      status.textContent = "Reading deliveries and review decisions…";
      const gen = ++generation;
      try {
        const [deliveriesData, reviewsData] = await Promise.all([
          request(`/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries`),
          request(`/api/v1/projects/${projectId}/milestones/${milestoneId}/reviews`),
        ]);
        if (gen !== generation) return;
        if (!deliveriesData || !Array.isArray(deliveriesData.items) || deliveriesData.items.length > 10 || !deliveriesData.items.every(delivery)
          || !reviewsData || !Array.isArray(reviewsData.items) || reviewsData.items.length > 20 || !reviewsData.items.every(review)) throw new Error(unexpected);
        status.textContent = "";
        if (!deliveriesData.items.length) {
          const li = node("li");
          li.append(p("No delivery revisions yet. The confirmed contributor delivers immutable revisions through the public API.", "source-note"));
          list.append(li);
        }
        for (const item of deliveriesData.items) {
          const li = node("li");
          const entry = node("article", undefined, "room-entry");
          const badge = reviewBadge(reviewsData.items, item.revision);
          entry.append(p(`Delivery revision ${item.revision} · delivered ${new Date(item.created_at).toLocaleDateString()}${badge ? ` · ${badge}` : ""}`, badge === "Accepted" ? "room-entry-state" : badge ? "room-work-notice" : "room-meta"), node("h4", item.summary), p(`Artifact: ${item.artifact.url}`, "room-meta"), p(`sha256 ${item.artifact.integrity.digest.slice(0, 16)}… over ${item.artifact.size_bytes} bytes${item.artifact.content_identifier ? ` · CID ${item.artifact.content_identifier.slice(0, 12)}…` : ""}`, "room-meta"), p(`Delivered by @${item.author.github_login} · GitHub account control`, "room-attribution"));
          const actions = node("div", undefined, "room-actions");
          const manifest = node("a", "Open the versioned manifest ↗");
          manifest.href = `/api/v1/projects/${projectId}/milestones/${milestoneId}/deliveries/${item.revision}`;
          manifest.target = "_blank"; manifest.rel = "noopener noreferrer";
          actions.append(manifest);
          entry.append(actions);
          li.append(entry);
          list.append(li);
        }
        for (const item of reviewsData.items) {
          const li = node("li");
          const entry = node("article", undefined, "room-entry");
          entry.append(p(`${item.decision === "accept" ? "Accepted" : "Revision requested"} · revision ${item.delivery_revision} · by @${item.reviewer.github_login}`, item.decision === "accept" ? "room-entry-state" : "room-work-notice"));
          if (item.note) entry.append(p(item.note, "room-description"));
          li.append(entry);
          list.append(li);
        }
        if (!deliveriesData.items.length && !reviewsData.items.length) status.textContent = "";
      } catch (error) {
        if (gen === generation) status.textContent = `${error.message} Choose Deliveries & reviews again to retry.`;
      }
    };
    run();
  };
  document.addEventListener("projects:detail", (event) => {
    const { projectId, milestones } = event.detail || {};
    if (!uuid(projectId) || !Array.isArray(milestones)) return;
    for (const article of document.querySelectorAll("#project-detail article[data-milestone-id]")) {
      const milestoneId = article.dataset.milestoneId;
      if (!uuid(milestoneId) || !milestones.includes(milestoneId)) continue;
      if (article.querySelector(".project-dlv-toggle")) continue;
      const toggle = node("button", "Deliveries & reviews", "text-button");
      toggle.type = "button";
      toggle.className = "text-button project-dlv-toggle";
      article.append(toggle);
      toggle.addEventListener("click", () => {
        const existing = article.querySelector(".project-dlv");
        if (existing) { existing.remove(); toggle.textContent = "Deliveries & reviews"; return; }
        toggle.textContent = "Hide deliveries & reviews";
        renderInto(article, projectId, milestoneId);
      }, { once: false });
    }
  });
})();
