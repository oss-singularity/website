(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  if (!$("projects-list")) return;
  const tokenInput = $("room-identity-token");
  const { uuid, missionId, text, date, shortDate, projectLabels, milestoneLabels, commitmentLabels, commitmentActions, summary, milestone, commitment, detail, exported } = window.OssProjects;
  const node = (tag, value, className) => { const n = document.createElement(tag); if (value !== undefined) n.textContent = value; if (className) n.className = className; return n; };
  const p = (value, className) => node("p", value, className);
  const unexpected = () => new Error("The service returned an unexpected response. Refresh before acting.");
  const requests = new Map(), urls = new Set();
  let alive = true, generation = 0, privateGeneration = 0, mission = null, loaded = false;
  let items = [], cursor = null, publicDetail = null, privateDetail = null;
  let pending = null, writeBusy = false, offerMilestone = null;
  const seq = { list: 0, detail: 0, auth: 0, export: 0 };
  const abort = (group) => { for (const [c, kind] of requests) if (!group || kind === group) c.abort(); };
  const request = async (path, options = {}, group = "public") => {
    const c = new AbortController(); requests.set(c, group);
    const timer = window.setTimeout(() => c.abort(), 20000);
    try {
      const response = await fetch(path, { ...options, credentials: "omit", cache: "no-store", redirect: "error", signal: c.signal, headers: { Accept: "application/json", ...options.headers } });
      const data = await response.json();
      if (!response.ok) {
        const known = typeof data?.error?.code === "string" && typeof data?.error?.message === "string";
        const error = new Error(known ? data.error.message : `The service returned HTTP ${response.status}.`);
        error.rejected = known && response.status < 500; error.status = response.status;
        if (response.status === 429 && Number(data.retry_after_seconds) > 0) error.message += ` Try again in ${Math.ceil(data.retry_after_seconds)} seconds.`;
        throw error;
      }
      return data;
    } finally { requests.delete(c); window.clearTimeout(timer); }
  };
  const current = (gen, priv) => alive && gen === generation && (priv === undefined || priv === privateGeneration);
  const status = (id, value) => $(id).textContent = value;
  const identity = () => {
    const value = tokenInput.value.trim();
    if (/^[A-Za-z0-9_-]{43}$/.test(value)) return value;
    status("projects-action-status", "Paste your complete 43-character Commons token above, or connect your identity in the Workshop."); tokenInput.focus(); return null;
  };
  const button = (label, fn, disabled = false) => { const b = node("button", label, "button button-secondary"); b.type = "button"; b.disabled = disabled; b.addEventListener("click", fn); return b; };
  const sync = () => { $("projects-refresh").disabled = !alive || !mission || writeBusy; $("projects-more").disabled = !alive || writeBusy; };
  const revoke = () => { for (const url of urls) URL.revokeObjectURL(url); urls.clear(); };
  const download = (value, name) => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json;charset=utf-8" })); urls.add(url);
    const a = node("a"); a.href = url; a.download = name; document.body.append(a); a.click(); a.remove();
    window.setTimeout(() => { URL.revokeObjectURL(url); urls.delete(url); }, 1000);
  };
  const attribution = (role, actor) => {
    const line = p(`${role}: @${actor.github_login} · GitHub account control`, "room-attribution");
    return line;
  };
  const milestoneTitle = (mId) => (privateDetail ?? publicDetail)?.milestones.find((m) => m.id === mId)?.title ?? mId;
  const clearPrivate = () => {
    privateGeneration += 1; seq.auth += 1; abort("private"); abort("write");
    privateDetail = null; pending = null; writeBusy = false; offerMilestone = null;
    status("projects-action-status", "Private view cleared. Choose an action to authenticate it.");
    sync(); renderDetail();
  };
  let listFilter = "all";
  const projectFilter = () => {
    const bar = node("div", undefined, "project-filter");
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "Filter projects by status");
    const counts = items.reduce((acc, item) => { acc[item.status] = (acc[item.status] ?? 0) + 1; return acc; }, {});
    const all = items.length;
    const chips = [["all", `All (${all})`], ["open", `Open (${counts.open ?? 0})`], ["closed", `Closed (${counts.closed ?? 0})`]];
    chips.forEach(([key, label]) => {
      const chip = node("button", label, `chip${listFilter === key ? " is-active" : ""}`);
      chip.type = "button";
      chip.setAttribute("aria-pressed", listFilter === key ? "true" : "false");
      if (!(key in counts) && key !== "all") chip.disabled = true;
      chip.addEventListener("click", () => { listFilter = key; renderList(); });
      bar.append(chip);
    });
    return bar;
  };
  const renderList = () => {
    const box = $("projects-list"); box.replaceChildren();
    if (items.length) {
      box.append(projectFilter());
      const shown = items.filter((item) => listFilter === "all" || item.status === listFilter);
      const card = (item) => {
        const article = node("article", undefined, "room-entry");
        article.append(p(`${projectLabels[item.status]} · mission ${item.mission_id}`, "room-entry-state"), node("h4", item.title));
        article.append(p(`Version ${item.version} · updated ${shortDate(item.updated_at)}`, "room-meta"), attribution("Coordinator", item.coordinator));
        article.append(button("Read milestones & commitments", () => openDetail(item.id)));
        return article;
      };
      // keep the room short at any project count: open work stays visible,
      // the closed history folds into one inspectable group
      const active = shown.filter((item) => item.status !== "closed");
      const archived = shown.filter((item) => item.status === "closed");
      if (listFilter !== "closed") {
        box.append(...active.map(card));
        if (archived.length) {
          const history = node("details", undefined, "project-history");
          history.append(node("summary", `Closed history (${archived.length}) — show finished projects`));
          history.append(...archived.map(card));
          box.append(history);
        }
      } else box.append(...shown.map(card));
    }
    else if (mission && loaded) {
      const empty = node("div", undefined, "room-empty");
      empty.append(p("No coordinated projects are published for this mission yet."));
      empty.append(p("A coordinator can structure the first project through the public API; every milestone then names its expected artifact and acceptance criteria."));
      box.append(empty);
    }
    $("projects-more").hidden = !cursor;
  };
  const readList = async (more = false) => {
    if (!alive || !mission) return;
    const n = ++seq.list, gen = generation;
    const next = more ? cursor : null;
    if (more && !next) return;
    if (!more) { items = []; cursor = null; loaded = false; renderList(); }
    const query = new URLSearchParams({ mission_id: mission, limit: "20" });
    if (next) query.set("cursor", next);
    status("projects-list-status", "Reading coordinated projects…"); $("projects-list").setAttribute("aria-busy", "true"); $("projects-more").hidden = true;
    try {
      const data = await request(`/api/v1/projects?${query}`, {}, "public");
      if (!current(gen) || n !== seq.list) return;
      if (!data || !Array.isArray(data.items) || data.items.length > 100 || !data.items.every((item) => summary(item) && item.mission_id === mission)
        || !(data.next_cursor === null || (typeof data.next_cursor === "string" && data.next_cursor.length <= 256))) throw unexpected();
      const map = new Map([...items, ...data.items].map((item) => [item.id, item]));
      items = [...map.values()]; cursor = data.next_cursor; loaded = true;
      renderList(); status("projects-list-status", items.length ? `${items.length} project${items.length === 1 ? "" : "s"} loaded. Choose one to inspect its milestones.` : "No coordinated projects here yet. This mission can grow its first one through the public API.");
      $("projects-more").hidden = !cursor;
    } catch (error) { if (current(gen) && n === seq.list) { status("projects-list-status", `${error.message} Choose Refresh projects to retry.`); $("projects-more").hidden = !next; } }
    finally { if (current(gen) && n === seq.list) $("projects-list").setAttribute("aria-busy", "false"); }
  };
  const renderMilestone = (m, children, offerable) => {
    const done = m.status === "done";
    const article = node("article", undefined, `room-entry project-milestone${m.blocked && m.status === "open" ? " is-gated" : ""}${done ? " is-done" : ""}`);
    article.dataset.milestoneId = m.id;
    article.append(p(`${milestoneLabels[m.status]}${m.blocked && m.status === "open" ? " · blocked by a dependency gate" : ""}${m.parent_milestone_id ? " · subproject part" : ""}`, "room-entry-state"), node("h4", m.title));
    const body = node("div", undefined, "project-milestone-body");
    body.append(p(m.purpose, "room-description"), node("h5", "Expected artifact"), p(m.expected_artifact, "room-description"), node("h5", "Acceptance criteria"));
    const criteria = node("ol"); m.acceptance.forEach((entry) => criteria.append(node("li", entry))); body.append(criteria);
    if (done) {
      // finished milestones collapse to their headline; the record stays inspectable
      const reveal = node("details", undefined, "project-milestone-reveal");
      reveal.append(node("summary", "Show the delivered record"));
      reveal.append(body);
      article.append(reveal);
    } else article.append(body);
    if (m.depends_on.length) article.append(p(`Depends on ${m.depends_on.length} earlier milestone${m.depends_on.length === 1 ? "" : "s"} · offers stay closed until each is done`, "room-meta"));
    article.append(p(`Scope ${m.scope_version} · version ${m.version} · updated ${shortDate(m.updated_at)}`, "room-meta"));
    if (offerable && m.status === "open" && !m.blocked && !privateDetail) article.append(button("Offer to take this milestone", () => { offerMilestone = m.id; renderDetail(); }, writeBusy || !!pending));
    const branch = children.get(m.id);
    if (branch) { const list = node("ul", undefined, "project-tree-branch"); branch.forEach((child) => { const li = node("li"); li.append(renderMilestone(child, children, offerable)); list.append(li); }); article.append(list); }
    return article;
  };
  const renderDetail = () => {
    const view = privateDetail ?? publicDetail;
    const box = $("project-detail");
    if (!view) { box.replaceChildren(); box.hidden = true; return; }
    const isPrivate = privateDetail != null;
    box.replaceChildren(); box.hidden = false;
    box.append(p(isPrivate ? (view.viewer?.coordinator ? "COORDINATOR VIEW" : "PARTICIPANT VIEW") : "PUBLIC PROJECT", "micro-label"), node("h4", view.title), p(`${projectLabels[view.status]} · mission ${view.mission_id}`, "room-entry-state"), attribution("Coordinator", view.coordinator));
    box.append(p(view.purpose, "room-description"), p(`Version ${view.version} · created ${shortDate(view.created_at)} · updated ${shortDate(view.updated_at)}`, "room-meta"));
    const roots = view.milestones.filter((m) => !m.parent_milestone_id);
    const children = new Map();
    view.milestones.forEach((m) => { if (m.parent_milestone_id) children.set(m.parent_milestone_id, [...(children.get(m.parent_milestone_id) ?? []), m]); });
    box.append(node("h5", "Milestones & dependency gates"));
    const tree = node("ul", undefined, "project-tree");
    roots.forEach((m) => { const li = node("li"); li.append(renderMilestone(m, children, !isPrivate)); tree.append(li); });
    if (roots.length) box.append(tree); else box.append(p("No milestones yet. The coordinator adds them through the public API.", "source-note"));
    box.append(node("h5", "Commitments"));
    const commitments = view.commitments.filter((c) => isPrivate || c.status !== "offered");
    if (commitments.length) commitments.forEach((c) => {
      const article = node("article", undefined, `room-entry project-commitment${c.status === "confirmed" ? " is-bound" : ""}`);
      article.append(p(`${commitmentLabels[c.status]} · ${milestoneTitle(c.milestone_id)}`, "room-entry-state"), attribution("Contributor", c.contributor), attribution("Coordinator side", c.coordinator));
      article.append(p(`Voluntary terms · bound at milestone scope ${c.scope_version} · updated ${shortDate(c.updated_at)}`, "room-meta"));
      if (isPrivate && c.status === "offered") {
        const controls = node("div", undefined, "room-actions");
        const asCoordinator = view.viewer?.coordinator === true;
        for (const action of asCoordinator ? ["confirm", "decline"] : ["withdraw"]) controls.append(button(commitmentActions[action], () => mutate(`/api/v1/projects/${view.id}/commitments/${c.id}/actions`, { action }, c.id, "offered"), writeBusy || !!pending));
        article.append(controls);
      }
      if (isPrivate && c.status === "confirmed" && view.viewer?.coordinator === true) {
        const controls = node("div", undefined, "room-actions");
        controls.append(button(commitmentActions.end, () => mutate(`/api/v1/projects/${view.id}/commitments/${c.id}/actions`, { action: "end" }, c.id, "confirmed"), writeBusy || !!pending));
        article.append(p("Ending releases both sides by agreement. The contributor finds their own end action in the published API contract.", "source-note"));
        article.append(controls);
      }
      box.append(article);
    });
    else box.append(p(isPrivate ? "No commitments are visible to this view yet." : "No bound commitments yet. An offer becomes visible publicly only after the coordinator confirms it.", "source-note"));
    if (!isPrivate) {
      const target = view.milestones.find((m) => m.id === offerMilestone);
      if (target && target.status === "open" && !target.blocked) {
        box.append(node("h5", `Offer to take: ${target.title}`));
        const label = node("label", undefined, "room-check"), consent = node("input"); consent.type = "checkbox"; consent.id = "project-offer-consent"; consent.required = true;
        label.append(consent, document.createTextNode(" I offer to take this exact milestone scope on voluntary terms. My attribution becomes visible to the coordinator and, after confirmation, publicly."));
        box.append(label);
        box.append(button("Send voluntary offer", () => {
          if (!consent.checked) { status("projects-action-status", "Read the milestone scope and confirm the voluntary offer consent first."); return; }
          mutate(`/api/v1/projects/${view.id}/commitments`, { milestone_id: target.id, terms: "volunteer" }, target.id, "offer");
        }, writeBusy || !!pending));
        box.append(button("Cancel offer draft", () => { offerMilestone = null; renderDetail(); }));
      } else if (offerMilestone) offerMilestone = null;
      box.append(button("Load participant view", () => authedDetail(), writeBusy || !!pending));
    } else box.append(button("Back to public view", () => { privateDetail = null; seq.auth += 1; renderDetail(); }, writeBusy || !!pending));
    box.append(button("Refresh this project", () => (privateDetail ? authedDetail(view.id, true) : openDetail(view.id, true)), writeBusy));
    box.append(button("Download project export", () => exportProject(view)));
    if (pending) box.append(p("The last write has an uncertain outcome. Retry re-checks the server state and re-sends only if that action is still possible.", "room-work-notice"),
      button("Retry the same action against the current state", () => resolvePending(), writeBusy));
    document.dispatchEvent(new CustomEvent("projects:detail", { detail: { projectId: view.id, milestones: view.milestones.map((m) => m.id), live: !writeBusy && !pending } }));
  };
  const openDetail = async (id, keepPrivate = false) => {
    if (!alive || !uuid(id) || writeBusy) return;
    const gen = generation, n = ++seq.detail;
    if (!keepPrivate) { publicDetail = null; privateDetail = null; offerMilestone = null; seq.auth += 1; }
    $("project-detail").replaceChildren(); $("project-detail").hidden = true;
    status("project-detail-status", "Loading the current project…");
    try {
      const value = await request(`/api/v1/projects/${id}`, {}, "public");
      if (!current(gen) || n !== seq.detail) return;
      if (!detail(value, false) || value.id !== id || value.mission_id !== mission) throw unexpected();
      publicDetail = value; renderDetail(); $("project-detail").focus();
      status("project-detail-status", "Current project loaded. Review the milestones before choosing an action.");
    } catch (error) { if (current(gen) && n === seq.detail) status("project-detail-status", `${error.message} Refresh the list and choose the project again.`); }
  };
  const authedDetail = async (id = privateDetail?.id ?? publicDetail?.id, silent = false) => {
    if (!alive || !uuid(id) || writeBusy) return;
    const token = identity(); if (!token) return;
    const gen = generation, priv = privateGeneration, n = ++seq.auth;
    if (!silent) status("project-detail-status", "Loading your participant view…");
    try {
      const value = await request(`/api/v1/projects/${id}`, { headers: { Authorization: `Bearer ${token}` } }, "private");
      if (!current(gen, priv) || n !== seq.auth) return;
      if (!detail(value, true) || value.id !== id || value.mission_id !== mission) throw unexpected();
      privateDetail = value; publicDetail = value; renderDetail();
      status("project-detail-status", value.viewer?.coordinator ? "Coordinator view loaded. Decisions stay explicit and reviewed." : "Participant view loaded. Your offered commitments appear here.");
    } catch (error) { if (current(gen, priv) && n === seq.auth) status("project-detail-status", `${error.message} The public view above remains available.`); }
  };
  const exportProject = async (view) => {
    const gen = generation, n = ++seq.export;
    status("project-detail-status", "Refreshing the public export…");
    try {
      const value = await request(`/api/v1/projects/${view.id}/export`, {}, "public");
      if (!current(gen) || n !== seq.export) return;
      if (!exported(value) || value.project.id !== view.id) throw unexpected();
      download(value, `oss-singularity-project-${value.project.id}.json`);
      status("project-detail-status", "Project export downloaded. It records coordination decisions; it verifies no artifact and authorizes no payment.");
    } catch (error) { if (current(gen) && n === seq.export) status("project-detail-status", `Export unavailable. ${error.message}`); }
  };
  const preStateHolds = (fresh, op) => {
    if (op.kind === "offer") { const m = fresh.milestones.find((x) => x.id === op.milestoneId); return m != null && m.status === "open" && !m.blocked; }
    const c = fresh.commitments.find((x) => x.id === op.commitmentId);
    return c != null && c.status === op.preState;
  };
  const send = async (op, token) => {
    const body = op.kind === "offer" ? { milestone_id: op.milestoneId, terms: "volunteer" } : { action: op.action };
    const data = await request(op.path, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }, "write");
    if (!commitment(data)) throw unexpected();
    return data;
  };
  const mutate = async (path, body, refId, kind, retry = false) => {
    if (!alive || writeBusy || (!retry && pending)) return;
    const token = identity(); if (!token) return;
    const gen = generation, priv = privateGeneration;
    const projectId = (publicDetail ?? privateDetail)?.id;
    const op = retry ? pending : { kind, path, action: body.action, milestoneId: body.milestone_id ?? null, commitmentId: refId ?? null, projectId, preState: kind };
    if (!op || !uuid(op.projectId)) return;
    writeBusy = true; pending = null; seq.auth += 1; abort("private"); sync(); renderDetail();
    status("projects-action-status", "Sending your explicit decision…");
    try {
      const data = await send(op, token);
      if (!current(gen, priv)) return;
      pending = null;
      status("projects-action-status", op.kind === "offer" ? "Offer sent. It is visible to the coordinator; nothing is bound before their confirmation." : "Decision saved. The current state is shown below.");
      offerMilestone = null;
      writeBusy = false;
      await authedDetail(op.projectId, true);
      if (!current(gen, priv)) return;
      readList();
    } catch (error) {
      if (!current(gen, priv)) return;
      if (!error.rejected) { pending = op; status("projects-action-status", "The write outcome is uncertain. Check the current state and explicitly retry against it."); }
      else {
        pending = null;
        const duplicate = error.status === 409 && op.kind === "offer";
        status("projects-action-status", duplicate ? "The service reports this identity already holds a commitment on this milestone — the earlier offer most likely arrived. Load the participant view." : `${error.message} Reload the project and review the current state before another decision.`);
      }
    } finally {
      if (current(gen, priv)) { writeBusy = false; sync(); renderDetail(); }
    }
  };
  const resolvePending = async () => {
    if (!alive || !pending || writeBusy) return;
    const token = identity(); if (!token) return;
    const op = pending, gen = generation, priv = privateGeneration;
    writeBusy = true; sync(); renderDetail();
    status("projects-action-status", "Checking the current server state before retrying…");
    try {
      const fresh = await request(`/api/v1/projects/${op.projectId}`, { headers: { Authorization: `Bearer ${token}` } }, "private");
      if (!current(gen, priv)) return;
      if (!detail(fresh, true) || fresh.id !== op.projectId) throw unexpected();
      privateDetail = fresh; publicDetail = fresh;
      if (!preStateHolds(fresh, op)) { pending = null; status("projects-action-status", "The server state already moved past that action. Review the current state below."); }
      else { await send(op, token); if (!current(gen, priv)) return; pending = null; status("projects-action-status", "Retry applied. Review the current state below."); }
      writeBusy = false;
      renderDetail(); await authedDetail(op.projectId, true);
    } catch (error) {
      if (!current(gen, priv)) return;
      if (!error.rejected) status("projects-action-status", "The state check itself failed. Nothing was sent; try again when the service answers.");
      else { pending = null; status("projects-action-status", `${error.message} Nothing further was sent.`); }
    } finally { if (current(gen, priv)) { writeBusy = false; sync(); renderDetail(); } }
  };
  const setMission = (id) => {
    generation += 1; abort(); mission = missionId(id) ? id : null;
    publicDetail = null; privateDetail = null; items = []; cursor = null; loaded = false; offerMilestone = null; pending = null; writeBusy = false;
    $("projects-list").replaceChildren(); $("projects-list").setAttribute("aria-busy", "false");
    $("project-detail").replaceChildren(); $("project-detail").hidden = true; $("projects-more").hidden = true;
    status("projects-list-status", mission ? "Reading coordinated projects…" : "Open a published mission to browse its coordinated projects.");
    status("project-detail-status", ""); sync();
    if (alive && mission) readList();
  };
  tokenInput.addEventListener("input", () => clearPrivate());
  document.addEventListener("singularity:mission", (event) => setMission(event.detail?.id));
  $("projects-refresh").addEventListener("click", () => readList());
  $("projects-more").addEventListener("click", () => readList(true));
  window.addEventListener("pagehide", () => { alive = false; generation += 1; abort(); revoke(); tokenInput.value = ""; });
  window.addEventListener("pageshow", (event) => { if (event.persisted) { alive = true; setMission(document.getElementById("room-context")?.dataset.missionId); } });
  setMission(document.getElementById("room-context")?.dataset.missionId);
})();
