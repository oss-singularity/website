(() => {
  "use strict";
  const byId = (id) => document.getElementById(`room-${id}`);
  if (!byId("workspace")) return;
  const idPattern = /^[a-z0-9][a-z0-9-]{0,79}$/;
  const tokenFreeId = (value) => typeof value === "string" && idPattern.test(value);
  const nodes = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const safeUrl = (value) => {
    if (typeof value !== "string" || value.length > 2048 || /[\u0000-\u0020\u007f\\]/u.test(value)) return null;
    try {
      const url = new URL(value);
      const labels = url.hostname.split(".");
      return url.protocol === "https:" && !url.username && !url.password && !url.port && labels.length > 1
        && /^[a-z]/.test(labels.at(-1)) && !/(?:^|\.)(?:localhost|local|internal|intranet|lan|home|test|invalid|example|onion|arpa)$/.test(url.hostname)
        && labels.every((label) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)) ? url.href : null;
    } catch { return null; }
  };
  const date = (value) => Number.isFinite(Date.parse(value));
  const formatDate = (value) => new Date(value).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  const roomLink = (id) => `/singularity/?mission=${encodeURIComponent(id)}`;
  const external = (label, href) => {
    const link = nodes("a", label);
    link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer";
    return link;
  };
  const validMission = (item) => item && tokenFreeId(item.id) && item.kind === "mission" && item.status === "published"
    && typeof item.title === "string" && [...item.title].length <= 120 && typeof item.summary === "string" && [...item.summary].length <= 2000;
  const validAuthor = (author) => author?.verification === "github-account-control" && typeof author.github_login === "string"
    && /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(author.github_login) && !author.github_login.includes("--");
  const validParticipation = (item) => item && tokenFreeId(item.id) && tokenFreeId(item.mission_id)
    && ["offer", "need"].includes(item.intent) && ["human", "agent", "team", "other"].includes(item.participant_type)
    && ["volunteer", "discuss-compensation"].includes(item.collaboration) && item.status === "published"
    && ["active", "closed"].includes(item.state) && typeof item.title === "string" && [...item.title].length <= 120
    && typeof item.summary === "string" && [...item.summary].length <= 2000 && validAuthor(item.author)
    && date(item.expires_at) && date(item.published_at);
  const validEvidence = (item) => item && tokenFreeId(item.id) && tokenFreeId(item.mission_id)
    && ["field-note", "project"].includes(item.kind) && item.status === "published"
    && typeof item.title === "string" && [...item.title].length <= 120 && typeof item.summary === "string" && [...item.summary].length <= 2000;
  const pageItems = (payload, validate) => {
    if (!payload || !Array.isArray(payload.items) || payload.items.length > 100 || !payload.items.every(validate)
      || !(payload.next_cursor === null || (typeof payload.next_cursor === "string" && payload.next_cursor.length <= 256))) throw new Error("The service returned an unexpected response.");
    return payload;
  };
  const requests = new Set();
  const missions = new Map();
  const feeds = Object.fromEntries(["needs", "offers", "evidence"].map((name) => [name, { items: [], cursor: null, busy: false, error: false, loaded: false, serial: 0 }]));
  let current = null;
  let roomVersion = 0;
  let directoryVersion = 0;
  let directoryCursor = null;
  let directoryBusy = false;
  let alive = true;
  let lifetime = 0;
  let expiryTimer = null;

  const request = async (path, roomRequest = false) => {
    const controller = new AbortController();
    const entry = { controller, roomRequest };
    const started = lifetime;
    requests.add(entry);
    const timeout = window.setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, { signal: controller.signal, credentials: "omit", cache: "no-store", redirect: "error", headers: { Accept: "application/json" } });
      const payload = await response.json();
      if (!alive || started !== lifetime) throw new Error("Inactive page");
      if (!response.ok) {
        const error = new Error(typeof payload?.error?.message === "string" ? payload.error.message : "The shared service is unavailable.");
        error.status = response.status;
        throw error;
      }
      return payload;
    } finally { requests.delete(entry); window.clearTimeout(timeout); }
  };
  const emitMission = () => {
    byId("context").dataset.missionId = current?.id || "";
    byId("context").dataset.missionTitle = current?.title || "";
    document.dispatchEvent(new CustomEvent("singularity:mission", { detail: current ? { id: current.id, title: current.title } : null }));
  };
  const compose = (intent) => {
    if (!current) return;
    document.dispatchEvent(new CustomEvent("singularity:compose", { detail: { mission: { id: current.id, title: current.title }, intent } }));
    document.getElementById("participate").scrollIntoView({ block: "start" });
    byId("participation-title").focus({ preventScroll: true });
  };
  const attribution = (author) => {
    const line = nodes("p", "", "room-attribution");
    if (validAuthor(author)) {
      line.append(external(`@${author.github_login}`, `https://github.com/${encodeURIComponent(author.github_login)}`), document.createTextNode(" · GitHub account control verified"));
    } else line.textContent = "No account attribution provided.";
    return line;
  };
  const participation = (item) => {
    const article = nodes("article", undefined, `room-entry${item.state === "closed" ? " is-closed" : ""}`);
    article.id = `participation-${item.id}`;
    article.append(nodes("p", item.state === "closed" ? `Closed · ${item.intent === "need" ? "no longer seeking support" : "offer no longer available"}` : item.intent === "need" ? "Open need" : "Open offer", "room-entry-state"));
    article.append(nodes("h4", item.title), nodes("p", item.summary, "room-description"), attribution(item.author));
    const type = { human: "Human", agent: "Agent", team: "Team", other: "Other / unspecified" }[item.participant_type];
    article.append(nodes("p", `${type} · self-declared`, "room-meta"));
    article.append(nodes("p", item.collaboration === "volunteer" ? "Voluntary" : "Compensation to agree · agree terms before work begins", "room-terms"));
    article.append(nodes("p", `Published ${formatDate(item.published_at)} · Expires ${formatDate(item.expires_at)}`, "room-meta"));
    const url = safeUrl(item.url);
    if (url) { const actions = nodes("div", undefined, "room-actions"); actions.append(external("Explore the source ↗", url)); article.append(actions); }
    return article;
  };
  const evidence = (item) => {
    const article = nodes("article", undefined, "room-entry");
    article.append(nodes("p", item.kind === "field-note" ? "Field note" : "Project", "room-entry-state"), nodes("h4", item.title), nodes("p", item.summary, "room-description"), attribution(item.author));
    const actions = nodes("div", undefined, "room-actions");
    const source = safeUrl(item.url);
    if (source) actions.append(external("Explore the source ↗", source));
    const link = nodes("a", "View in the Workshop →");
    link.href = `/workshop/?signal=${encodeURIComponent(item.id)}#signal-${encodeURIComponent(item.id)}`;
    actions.append(link); article.append(actions);
    return article;
  };
  const renderFeed = (name) => {
    const feed = feeds[name];
    const container = byId(name);
    const items = feed.items.filter((item) => name === "evidence" || Date.parse(item.expires_at) > Date.now());
    container.replaceChildren();
    if (items.length) container.append(...items.map(name === "evidence" ? evidence : participation));
    else if (feed.loaded && !feed.error) {
      const empty = nodes("div", undefined, "room-empty");
      const title = name === "needs" ? "What would help this mission move forward?" : name === "offers" ? "There is room for what you can bring." : "Leave something another person can build on.";
      empty.append(nodes("p", title));
      empty.append(nodes("p", name === "evidence" ? "No published field notes or projects are linked to this mission yet." : `No ${byId("include-closed").checked ? "current published" : "open published"} ${name} are loaded for this mission.`));
      if (name !== "evidence") { const button = nodes("button", name === "needs" ? "Share a need →" : "Offer your support →", "text-button"); button.type = "button"; button.addEventListener("click", () => compose(name === "needs" ? "need" : "offer")); empty.append(button); }
      container.append(empty);
    }
    byId(`${name}-more`).hidden = !feed.cursor;
    byId(`${name}-more`).disabled = feed.busy;
    byId(`${name}-retry`).hidden = !feed.error;
    byId(`${name}-retry`).disabled = feed.busy;
    container.setAttribute("aria-busy", String(feed.busy));
  };
  const scheduleExpiry = () => {
    window.clearTimeout(expiryTimer);
    const expiries = ["needs", "offers"].flatMap((name) => feeds[name].items.map((item) => Date.parse(item.expires_at))).filter((value) => value > Date.now());
    if (expiries.length && alive) expiryTimer = window.setTimeout(() => { renderFeed("needs"); renderFeed("offers"); scheduleExpiry(); }, Math.min(2147483647, Math.max(1, Math.min(...expiries) - Date.now() + 1)));
  };
  const loadFeed = async (name, append = false) => {
    if (!current || !alive || feeds[name].busy) return;
    const feed = feeds[name], version = roomVersion, mission = current.id, serial = ++feed.serial;
    feed.busy = true;
    byId(`${name}-status`).textContent = append ? "Loading more published contributions…" : "Reading published contributions…";
    renderFeed(name);
    const query = `mission_id=${encodeURIComponent(mission)}&limit=12${append && feed.cursor ? `&cursor=${encodeURIComponent(feed.cursor)}` : ""}`;
    const path = name === "evidence" ? `/api/v1/contributions?${query}` : `/api/v1/participations?${query}&intent=${name === "needs" ? "need" : "offer"}&state=${byId("include-closed").checked ? "all" : "active"}`;
    try {
      const result = pageItems(await request(path, true), (item) => (name === "evidence" ? validEvidence(item) : validParticipation(item)) && item.mission_id === mission && (name === "evidence" || item.intent === (name === "needs" ? "need" : "offer")));
      if (!alive || version !== roomVersion || serial !== feed.serial) return;
      feed.items = Array.from(new Map([...(append ? feed.items : []), ...result.items].map((item) => [item.id, item])).values());
      feed.cursor = result.next_cursor; feed.loaded = true; feed.error = false;
      byId(`${name}-status`).textContent = `Loaded ${new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}. Only published contributions appear here.`;
    } catch {
      if (!alive || version !== roomVersion || serial !== feed.serial) return;
      feed.error = true;
      feed.retryAppend = append;
      byId(`${name}-status`).textContent = `${name === "evidence" ? "Work & evidence" : name === "needs" ? "Needs" : "Offers"} could not be loaded. ${feed.items.length ? "Previously loaded contributions remain visible. " : ""}Use Retry to try again.`;
    } finally {
      if (alive && version === roomVersion && serial === feed.serial) { feed.busy = false; renderFeed(name); scheduleExpiry(); }
    }
  };
  const renderDirectory = () => {
    const select = byId("mission-select"), selected = current?.id || select.value;
    select.replaceChildren(nodes("option", "Choose a mission")); select.firstChild.value = "";
    missions.forEach((mission) => { const option = nodes("option", `${mission.title}${mission.provenance === "seed" && mission.id !== "build-the-commons" ? " · editorial template" : ""}`); option.value = mission.id; select.append(option); });
    select.value = selected;
    select.disabled = missions.size === 0;
    byId("open-mission").disabled = missions.size === 0;
    byId("missions-more").hidden = !directoryCursor;
  };
  const loadDirectory = async (append = false) => {
    if (directoryBusy || !alive) return;
    const version = ++directoryVersion;
    directoryBusy = true;
    byId("missions-retry").disabled = true; byId("missions-more").disabled = true;
    byId("directory-status").textContent = "Reading published missions…";
    try {
      const result = pageItems(await request(`/api/v1/missions?limit=30${append && directoryCursor ? `&cursor=${encodeURIComponent(directoryCursor)}` : ""}`), validMission);
      if (!alive || version !== directoryVersion) return;
      if (!append) missions.clear();
      result.items.forEach((item) => missions.set(item.id, item));
      if (current) missions.set(current.id, current);
      directoryCursor = result.next_cursor;
      renderDirectory();
      byId("directory-status").textContent = missions.size ? "Choose a mission to open its shared room." : "No published missions are available yet. The Workshop is open for suggestions.";
    } catch { if (alive && version === directoryVersion) byId("directory-status").textContent = "The mission list could not be loaded. You can retry or open a direct room link."; }
    finally { if (alive && version === directoryVersion) { directoryBusy = false; byId("missions-retry").disabled = false; byId("missions-more").disabled = false; } }
  };
  const loadRoom = async (id) => {
    const version = ++roomVersion;
    requests.forEach((entry) => { if (entry.roomRequest) entry.controller.abort(); });
    window.clearTimeout(expiryTimer);
    current = null; emitMission();
    byId("live-content").hidden = true;
    document.querySelectorAll("[data-room-intent]").forEach((button) => { button.disabled = true; });
    byId("workspace").setAttribute("aria-busy", "true");
    byId("refresh").disabled = true;
    byId("title").textContent = "Opening the shared mission…";
    byId("summary").textContent = ""; byId("context").textContent = ""; byId("provenance").textContent = "SHARED PURPOSE";
    byId("source").hidden = true; byId("permalink").hidden = true;
    byId("status").textContent = "Reading this mission from the shared service…";
    Object.keys(feeds).forEach((name) => { Object.assign(feeds[name], { items: [], cursor: null, busy: false, error: false, loaded: false }); feeds[name].serial += 1; byId(name).replaceChildren(); });
    try {
      if (!tokenFreeId(id)) throw new Error("This room link does not contain a valid mission ID.");
      const mission = await request(`/api/v1/missions/${encodeURIComponent(id)}`, true);
      if (!alive || version !== roomVersion) return;
      if (!validMission(mission) || mission.id !== id) throw new Error("The service returned an unexpected mission.");
      current = mission; missions.set(id, mission); renderDirectory();
      byId("title").textContent = mission.title; byId("summary").textContent = mission.summary;
      byId("provenance").textContent = id === "build-the-commons" && mission.provenance === "seed" ? "OUR FOUNDING MISSION" : mission.provenance === "seed" ? "EDITORIAL MISSION TEMPLATE" : "COMMUNITY MISSION";
      byId("context").textContent = `Mission / ${id}`;
      byId("permalink").href = roomLink(id); byId("permalink").hidden = false;
      const source = safeUrl(mission.url);
      if (source) { byId("source").href = source; byId("source").target = "_blank"; byId("source").rel = "noopener noreferrer"; byId("source").hidden = false; }
      byId("share-evidence").href = `/workshop/?mission=${encodeURIComponent(id)}#contribute`;
      byId("live-content").hidden = false;
      byId("status").textContent = "A shared room for this mission. Needs and offers are published after moderation.";
      document.querySelectorAll("[data-room-intent]").forEach((button) => { button.disabled = false; });
      emitMission();
      await Promise.all(Object.keys(feeds).map((name) => loadFeed(name)));
    } catch (error) {
      if (!alive || version !== roomVersion) return;
      byId("title").textContent = "This mission room is unavailable.";
      byId("status").textContent = error.status === 404 ? "This mission is not published or is no longer available. Choose another mission or retry this link." : `${error.message || "The service could not be reached."} Choose a mission or use Refresh room to try again.`;
    } finally { if (alive && version === roomVersion) { byId("workspace").setAttribute("aria-busy", "false"); byId("refresh").disabled = false; } }
  };
  const requested = () => new URLSearchParams(window.location.search).get("mission") ?? "build-the-commons";
  byId("mission-form").addEventListener("submit", (event) => {
    event.preventDefault(); const id = byId("mission-select").value;
    if (!tokenFreeId(id)) return;
    window.history.pushState(null, "", roomLink(id)); loadRoom(id);
  });
  byId("missions-retry").addEventListener("click", () => loadDirectory());
  byId("missions-more").addEventListener("click", () => loadDirectory(true));
  byId("refresh").addEventListener("click", () => loadRoom(requested()));
  byId("include-closed").addEventListener("change", () => { if (current) loadRoom(current.id); });
  Object.keys(feeds).forEach((name) => { byId(`${name}-more`).addEventListener("click", () => loadFeed(name, true)); byId(`${name}-retry`).addEventListener("click", () => loadFeed(name, feeds[name].retryAppend || false)); });
  document.querySelectorAll("[data-room-intent]").forEach((button) => button.addEventListener("click", () => compose(button.dataset.roomIntent)));
  document.addEventListener("singularity:changed", (event) => { if (current?.id === event.detail?.mission_id) loadRoom(current.id); });
  window.addEventListener("popstate", () => loadRoom(requested()));
  window.addEventListener("pagehide", () => { alive = false; lifetime += 1; roomVersion += 1; directoryVersion += 1; window.clearTimeout(expiryTimer); requests.forEach(({ controller }) => controller.abort()); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) { alive = true; directoryBusy = false; loadDirectory(); loadRoom(requested()); } });
  byId("missions-retry").disabled = false;
  loadDirectory(); loadRoom(requested());
})();
