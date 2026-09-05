(() => {
  "use strict";
  const el = (id) => document.getElementById(`work-${id}`);
  if (!el("public")) return;
  const tokenInput = document.getElementById("room-identity-token");
  const { uuid, missionId, text, labels, actionNames, safeUrl, summary, detailValid, publicPacket, validLength, shortDate, resultSections, contextNotice } = window.OssWorkItems;
  const node = (tag, value, className) => { const n = document.createElement(tag); if (value !== undefined) n.textContent = value; if (className) n.className = className; return n; };
  const p = (value, className) => node("p", value, className);
  const unexpected = () => new Error("The service returned an unexpected response. Refresh before acting.");
  const requests = new Map(), urls = new Set();
  let alive = true, generation = 0, privateGeneration = 0, mission = null, publicDetail = null, privateDetail = null;
  let publicItems = [], privateItems = [], publicCursor = null, privateCursor = null, pending = null, writeBusy = false, receipt = null, recoveryNeeded = false;
  const seq = { list: 0, detail: 0, mine: 0, own: 0, export: 0 };
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
  const status = (id, value) => { el(id).textContent = value; if (id === "action-status" && publicDetail) el("detail-status").textContent = value; };
  const identity = () => {
    const value = tokenInput.value.trim();
    if (/^[A-Za-z0-9_-]{43}$/.test(value)) return value;
    status("action-status", "Paste your complete 43-character Commons token above, or connect your identity in the Workshop."); tokenInput.focus(); return null;
  };
  const button = (label, fn, disabled = false) => { const b = node("button", label, "button button-secondary"); b.type = "button"; b.disabled = disabled; b.addEventListener("click", fn); return b; };
  const checkbox = (label, id) => { const l = node("label", undefined, "room-check"), input = node("input"); input.type = "checkbox"; input.id = id; input.required = true; l.append(input, document.createTextNode(label)); return { label: l, input }; };
  const sync = () => {
    el("create-fields").disabled = !alive || !mission || writeBusy || !!pending;
    el("mine-load").disabled = !alive || writeBusy; el("mine-more").disabled = !alive || writeBusy;
    el("retry-write").disabled = writeBusy || !pending; el("uncertain").hidden = !pending || writeBusy;
    el("refresh").disabled = !alive || !mission;
  };
  const revoke = () => { for (const url of urls) URL.revokeObjectURL(url); urls.clear(); };
  const download = (packet, name) => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(packet, null, 2)], { type: "application/json;charset=utf-8" })); urls.add(url);
    const a = node("a"); a.href = url; a.download = name; document.body.append(a); a.click(); a.remove();
    window.setTimeout(() => { URL.revokeObjectURL(url); urls.delete(url); }, 1000);
  };
  const clearReceipt = () => { receipt = null; el("receipt").textContent = ""; el("receipt-panel").hidden = true; revoke(); };
  const clearDraft = () => { for (const key of ["title", "scope", "deliverable", "acceptance"]) el(`create-${key}`).value = ""; el("create-consent").checked = false; };
  const clearPrivate = () => {
    privateGeneration += 1; seq.mine += 1; seq.own += 1; abort("private"); abort("write");
    privateDetail = null; privateItems = []; privateCursor = null; pending = null; writeBusy = false;
    el("mine").replaceChildren(); el("mine").setAttribute("aria-busy", "false"); el("mine-more").hidden = true;
    el("private-detail").replaceChildren(); el("private-detail").hidden = true; clearDraft(); clearReceipt();
    status("mine-status", "Choose Load my work for the identity now in this page."); sync();
  };
  const attribution = (role, actor) => p(`${role}: @${actor.github_login} · GitHub account control`, "room-attribution");
  const card = (item, privateView) => {
    const a = node("article", undefined, "room-entry"); a.append(p(`${labels[item.state]} · ${item.moderation}`, "room-entry-state"), node("h4", item.title));
    a.append(p(`Scope ${item.scope_version} · version ${item.version} · expires ${shortDate(item.expires_at)}`, "room-meta"));
    if (privateView) a.append(p(`Mission: ${item.mission_id}`, "room-meta"));
    a.append(button(privateView ? "Open my work & decisions" : "Read scope & decisions", () => openDetail(item.id, privateView)));
    return a;
  };
  const renderList = (privateView) => { const box = el(privateView ? "mine" : "list"); box.replaceChildren(...(privateView ? privateItems : publicItems).map((item) => card(item, privateView))); };
  const readList = async (privateView = false, more = false) => {
    if (!alive || (!privateView && !mission) || (privateView && writeBusy)) return;
    const token = privateView ? identity() : null; if (privateView && !token) return;
    const key = privateView ? "mine" : "list", n = ++seq[key], gen = generation, priv = privateView ? privateGeneration : undefined;
    const cursor = more ? (privateView ? privateCursor : publicCursor) : null;
    if (more && !cursor) return;
    if (!more) {
      if (privateView) { privateItems = []; privateCursor = null; privateDetail = null; seq.own += 1; el("private-detail").replaceChildren(); el("private-detail").hidden = true; }
      else { publicItems = []; publicCursor = null; publicDetail = null; seq.detail += 1; seq.export += 1; el("detail").replaceChildren(); el("detail").hidden = true; }
      renderList(privateView);
    }
    const query = new URLSearchParams({ limit: "20" });
    if (!privateView) { query.set("mission_id", mission); if (el("include-ended").checked) query.set("state", "all"); }
    if (cursor) query.set("cursor", cursor);
    status(`${key}-status`, "Loading work items…"); el(key).setAttribute("aria-busy", "true"); el(privateView ? "mine-more" : "more").hidden = true;
    try {
      const data = await request(`/api/v1/work-items${privateView ? "/mine" : ""}?${query}`, privateView ? { headers: { Authorization: `Bearer ${token}` } } : {}, privateView ? "private" : "public");
      if (!current(gen, priv) || n !== seq[key]) return;
      if (!data || !Array.isArray(data.items) || data.items.length > 50 || !data.items.every((item) => summary(item, privateView) && (privateView || item.mission_id === mission))
        || !(data.next_cursor === null || text(data.next_cursor, 256))) throw unexpected();
      const old = privateView ? privateItems : publicItems, map = new Map(old.map((item) => [item.id, item])); data.items.forEach((item) => map.set(item.id, item));
      if (privateView) { privateItems = [...map.values()]; privateCursor = data.next_cursor; recoveryNeeded = false; } else { publicItems = [...map.values()]; publicCursor = data.next_cursor; }
      renderList(privateView); status(`${key}-status`, map.size ? `${map.size} work item${map.size === 1 ? "" : "s"} loaded. Choose one to inspect its scope.` : privateView ? "No retained work found for this identity. You can propose a bounded work item or offer on an open one." : "No published work items here yet. Propose one clear scope and checkable result below.");
      el(privateView ? "mine-more" : "more").hidden = !data.next_cursor;
    } catch (error) { if (current(gen, priv) && n === seq[key]) { status(`${key}-status`, `${error.message} Choose ${privateView ? "Load my work" : "Refresh work"} to retry.`); el(privateView ? "mine-more" : "more").hidden = !cursor; } }
    finally { if (current(gen, priv) && n === seq[key]) el(key).setAttribute("aria-busy", "false"); }
  };
  const exportPublic = async (item) => {
    const gen = generation, n = ++seq.export; status("detail-status", "Refreshing the public record for export…");
    try {
      const value = await request(`/api/v1/work-items/${item.id}`);
      if (!current(gen) || n !== seq.export) return;
      if (!detailValid(value, false) || value.id !== item.id || value.mission_id !== item.mission_id) throw unexpected();
      download(publicPacket(value, window.location.origin), `oss-singularity-work-${value.id}.json`); status("detail-status", "Public JSON downloaded. It contains no private submissions, credentials, receipts or client operation IDs.");
    } catch (error) { if (current(gen) && n === seq.export) status("detail-status", `Export unavailable. ${error.message}`); }
  };
  const renderResult = (r) => {
    const a = node("article", undefined, "room-work-result"); a.append(p(`Revision ${r.revision} · ${r.status}`, "room-entry-state"), node("h5", r.title), p(r.summary, "room-description"), p(`Result ${r.id}`, "room-meta"));
    const link = node("a", "Inspect the public source ↗"); link.href = safeUrl(r.url); link.target = "_blank"; link.rel = "noopener noreferrer"; a.append(link); return a;
  };
  const renderDetail = (item, privateView = false) => {
    const box = el(privateView ? "private-detail" : "detail"); box.replaceChildren(); box.hidden = false;
    box.append(p(privateView ? "PRIVATE ACCOUNT VIEW" : "PUBLIC WORK ITEM", "micro-label"), node("h4", item.title), p(`${labels[item.state]} · ${item.moderation}`, "room-entry-state"), attribution("Requester", item.requester));
    if (item.contributor) box.append(attribution("Confirmed contributor", item.contributor));
    box.append(p(`Scope ${item.scope_version} · version ${item.version} · expires ${shortDate(item.expires_at)}`, "room-meta"), node("h5", "Fixed scope"), p(item.scope, "room-description"), node("h5", "Expected delivery"), p(item.deliverable, "room-description"), node("h5", "Acceptance criteria"));
    const criteria = node("ol"); item.acceptance.forEach((s) => criteria.append(node("li", s))); box.append(criteria);
    box.append(p("Voluntary terms. An offer awaits requester confirmation for up to 48 hours. Account control provides attribution, not a quality guarantee.", "source-note"));
    if (privateView && !item.parent_available) box.append(p("The parent mission is unavailable. New work and delivery decisions are blocked.", "room-work-notice"));
    if (privateView && item.offer) box.append(attribution("Offer awaiting confirmation", item.offer), p(`Offer expires ${new Date(item.offer_expires_at).toLocaleString()}. Confirmation binds this scope and contributor.`, "source-note"));
    for (const section of resultSections(item, privateView)) {
      box.append(node("h5", section.title)); if (section.notice) box.append(p(section.notice, "room-work-notice"));
      section.results.forEach((r) => box.append(renderResult(r)));
    }
    box.append(button(privateView ? "Refresh my work item" : "Refresh this work item", () => openDetail(item.id, privateView), writeBusy));
    if (!privateView) {
      box.append(button("Download public work JSON", () => exportPublic(item)));
      if (item.state === "open") {
        const consent = checkbox("I offer to do this exact scope on voluntary terms. My attribution may become public if the requester confirms me.", "work-offer-consent"); box.append(consent.label);
        box.append(button(actionNames.offer, () => { if (publicDetail !== item || !consent.input.checked) { status("action-status", "Read the scope and check the voluntary offer consent before submitting."); return; } mutate(`/api/v1/work-items/${item.id}/actions`, { expected_version: item.version, action: "offer", public_consent: true }); }, writeBusy || !!pending));
      }
      return;
    }
    const controls = node("div", undefined, "room-actions");
    for (const action of item.allowed_actions.filter((a) => !["offer", "submit_result", "deliver"].includes(a))) {
      if (action === "acknowledge" && (!item.current_result_available || !item.results.some((r) => r.id === item.current_result_id))) continue;
      controls.append(button(actionNames[action], () => {
        if (privateDetail !== item) return;
        const body = { expected_version: item.version, action }; if (action === "acknowledge") body.result_id = item.current_result_id;
        mutate(`/api/v1/work-items/${item.id}/actions`, body);
      }, writeBusy || !!pending));
    }
    box.append(controls);
    if (item.allowed_actions.includes("request_revision")) box.append(p("Revision feedback belongs in a separately moderated contribution or your already agreed external channel.", "source-note"));
    if (item.allowed_actions.includes("cancel")) box.append(p("Cancel ends this arrangement and removes the item from public view. It cannot be reopened; independent Workshop contributions remain.", "source-note"));
    if (item.allowed_actions.includes("deliver")) {
      const eligible = item.own_results.filter((r) => r.status === "published" && r.revision > item.last_delivered_revision);
      if (eligible.length) {
        const label = node("label", "Published result to deliver"), select = node("select"); select.id = "work-delivery-result";
        eligible.forEach((r) => { const o = node("option", `Revision ${r.revision}: ${r.title}`); o.value = r.id; select.append(o); }); label.append(select); box.append(label);
        box.append(button(actionNames.deliver, () => { if (privateDetail === item && eligible.some((r) => r.id === select.value)) mutate(`/api/v1/work-items/${item.id}/actions`, { expected_version: item.version, action: "deliver", result_id: select.value }); }, writeBusy || !!pending));
      } else box.append(p("A new result must be published before you can deliberately deliver it. Refresh this item after moderation.", "source-note"));
    }
    if (item.allowed_actions.includes("submit_result")) resultForm(box, item);
  };
  const openDetail = async (id, privateView) => {
    if (!alive || !uuid(id) || writeBusy) return;
    const token = privateView ? identity() : null; if (privateView && !token) return;
    const gen = generation, priv = privateView ? privateGeneration : undefined, key = privateView ? "own" : "detail", n = ++seq[key];
    const statusId = privateView ? "mine-status" : "detail-status", box = el(privateView ? "private-detail" : "detail");
    if (privateView) privateDetail = null; else { publicDetail = null; seq.export += 1; }
    box.replaceChildren(); box.hidden = true; status(statusId, "Loading the current work item…");
    try {
      const item = await request(`/api/v1/work-items/${privateView ? "mine/" : ""}${id}`, privateView ? { headers: { Authorization: `Bearer ${token}` } } : {}, privateView ? "private" : "public");
      if (!current(gen, priv) || n !== seq[key]) return;
      if (!detailValid(item, privateView) || item.id !== id || (!privateView && item.mission_id !== mission)) throw unexpected();
      if (privateView) privateDetail = item; else publicDetail = item;
      renderDetail(item, privateView); box.focus(); status(statusId, "Current scope loaded. Review the details before choosing an action.");
    } catch (error) { if (current(gen, priv) && n === seq[key]) status(statusId, `${error.message} Refresh the list and choose the item again.`); }
  };
  const mutate = async (path, body, retry = false) => {
    if (!alive || writeBusy || (!retry && pending)) return;
    const token = identity(); if (!token) return;
    const gen = generation, priv = privateGeneration;
    const op = retry ? pending : { path, body: { client_request_id: crypto.randomUUID(), ...body } };
    if (!op) return;
    if (new Blob([JSON.stringify(op.body)]).size > 8192) { status("action-status", "Shorten this submission to fit the 8 KB request limit."); return; }
    writeBusy = true; pending = null; seq.mine += 1; seq.own += 1; abort("private"); clearReceipt(); sync();
    if (privateDetail) renderDetail(privateDetail, true); if (publicDetail) renderDetail(publicDetail);
    status("action-status", "Sending your explicit decision…");
    try {
      const data = await request(op.path, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(op.body) }, "write");
      if (!current(gen, priv)) return;
      if (!detailValid(data?.item, true) || !uuid(data.operation?.id) || !Number.isSafeInteger(data.operation.applied_version)
        || typeof data.operation.replayed !== "boolean" || (op.body.mission_id ? data.item.mission_id !== op.body.mission_id : !op.path.includes(`/${data.item.id}/`))) throw unexpected();
      privateDetail = data.item; privateItems = privateItems.map((v) => v.id === data.item.id ? data.item : v); renderList(true);
      if (data.receipt && uuid(data.receipt.id) && data.receipt.status === "pending" && /^[A-Za-z0-9_-]{43}$/.test(data.receipt.receipt_token)
        && data.receipt.poll_url === `/api/v1/proposals/${data.receipt.id}`) {
        receipt = { id: data.receipt.id, status: "pending", poll_url: data.receipt.poll_url, receipt_token: data.receipt.receipt_token };
        el("receipt").textContent = JSON.stringify(receipt, null, 2); el("receipt-panel").hidden = false;
      }
      if (op.body.mission_id) clearDraft();
      status("action-status", data.operation.replayed ? "Exact operation recovered. Review its current state below." : op.path.endsWith("/results") ? "Result submitted for moderation. Refresh your item after publication, then explicitly deliver it." : "Decision saved. Review the current state below.");
      readList(false);
    } catch (error) {
      if (!current(gen, priv)) return;
      if (!error.rejected) { pending = op; status("action-status", "The write outcome is uncertain. Recover your work or explicitly retry this exact operation."); }
      else { privateDetail = null; el("private-detail").replaceChildren(); el("private-detail").hidden = true; status("action-status", `${error.message} Load your work to refresh permissions and version before another decision.`); }
    } finally {
      if (current(gen, priv)) { writeBusy = false; sync(); if (privateDetail) { renderDetail(privateDetail, true); el("private-detail").focus(); } if (publicDetail) renderDetail(publicDetail); }
    }
  };
  const resultForm = (box, item) => {
    const form = node("form", undefined, "room-compose room-result-form"); form.autocomplete = "off";
    form.append(node("h5", "Submit a new attributed result"), p("A fresh field note or project is created for this scope. Publication needs moderation; submitting it does not deliver the work. Keep private information out. Cancelling work does not withdraw the independent contribution.", "source-note"));
    const fields = {};
    for (const [key, label, type] of [["kind", "Contribution type", "select"], ["title", "Result title", "input"], ["summary", "What did you do and how can it be checked?", "textarea"], ["url", "Public HTTPS source", "input"]]) {
      const l = node("label", label), input = node(type); input.id = `work-result-${key}`; input.required = true;
      if (key === "kind") for (const [value, title] of [["field-note", "Field note"], ["project", "Project"]]) { const o = node("option", title); o.value = value; input.append(o); }
      else { input.maxLength = key === "summary" ? 4000 : key === "url" ? 2048 : 240; if (key === "url") input.type = "url"; }
      l.append(input); form.append(l); fields[key] = input;
    }
    const consent = checkbox("I may publish this result and my account attribution after moderation.", "work-result-consent"); form.append(consent.label);
    const submit = node("button", "Send result for publication review", "button button-primary"); submit.type = "submit"; submit.disabled = writeBusy || !!pending; form.append(submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault(); if (privateDetail !== item || !consent.input.checked) return;
      const values = Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.value.trim()]));
      if (!validLength(values.title, 3, 120) || !validLength(values.summary, 20, 2000) || !safeUrl(values.url)) { status("action-status", "Use a title of 3–120 characters, a summary of 20–2,000, and a safe public HTTPS source."); return; }
      mutate(`/api/v1/work-items/${item.id}/results`, { expected_version: item.version, ...values, public_consent: true });
    }); box.append(form);
  };
  el("create-form").addEventListener("submit", (event) => {
    event.preventDefault(); if (!mission || !el("create-consent").checked) return;
    const values = Object.fromEntries(["title", "scope", "deliverable"].map((key) => [key, el(`create-${key}`).value.trim()]));
    values.acceptance = el("create-acceptance").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    if (!validLength(values.title, 3, 120) || !validLength(values.scope, 20, 2000) || !validLength(values.deliverable, 20, 1000)
      || values.acceptance.length < 1 || values.acceptance.length > 8 || !values.acceptance.every((s) => validLength(s, 10, 300))) { status("action-status", "Check the field lengths and provide 1–8 clear acceptance criteria, one per line."); return; }
    mutate("/api/v1/work-items", { mission_id: mission, ...values, terms: "volunteer", public_consent: true });
  });
  const setMission = (id) => {
    recoveryNeeded = recoveryNeeded || writeBusy || !!pending;
    generation += 1; abort(); mission = missionId(id) ? id : null; publicDetail = null; publicItems = []; publicCursor = null;
    el("list").replaceChildren(); el("detail").replaceChildren(); el("detail").hidden = true; el("more").hidden = true; clearPrivate();
    status("create-context", mission ? `For mission: ${mission} · fixed scope, voluntary terms` : "Open a published mission to begin.");
    status("list-status", "Open a published mission to browse voluntary work."); status("detail-status", ""); status("action-status", contextNotice("Room", recoveryNeeded));
    sync(); if (alive && mission) readList();
  };
  tokenInput.addEventListener("input", () => { recoveryNeeded = recoveryNeeded || writeBusy || !!pending; clearPrivate(); if (publicDetail) renderDetail(publicDetail); status("action-status", contextNotice("Identity", recoveryNeeded)); });
  document.addEventListener("singularity:mission", (event) => setMission(event.detail?.id));
  el("refresh").addEventListener("click", () => readList()); el("include-ended").addEventListener("change", () => readList()); el("more").addEventListener("click", () => readList(false, true));
  el("mine-load").addEventListener("click", () => readList(true)); el("mine-more").addEventListener("click", () => readList(true, true));
  el("retry-write").addEventListener("click", () => mutate(null, null, true));
  el("receipt-download").addEventListener("click", () => { if (alive && receipt) download(receipt, `private-result-receipt-${receipt.id}.json`); });
  window.addEventListener("pagehide", () => { recoveryNeeded = recoveryNeeded || writeBusy || !!pending; alive = false; generation += 1; abort(); clearPrivate(); tokenInput.value = ""; publicDetail = null; el("detail").replaceChildren(); el("detail").hidden = true; sync(); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) { alive = true; setMission(document.getElementById("room-context")?.dataset.missionId); } });
  setMission(document.getElementById("room-context")?.dataset.missionId);
})();
