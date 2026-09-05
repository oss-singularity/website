(() => {
  "use strict";
  const byId = (id) => document.getElementById(`room-${id}`);
  const form = byId("participation-form");
  if (!form) return;
  const tokenInput = byId("identity-token");
  const fieldNames = { intent: "intent", participant_type: "participant-type", collaboration: "collaboration", title: "participation-title", summary: "participation-summary", url: "participation-url" };
  const fields = Object.fromEntries(Object.entries(fieldNames).map(([name, id]) => [name, byId(id)]));
  const ids = (value) => typeof value === "string" && /^[a-z0-9][a-z0-9-]{0,79}$/.test(value);
  const tokens = (value) => typeof value === "string" && /^[A-Za-z0-9_-]{43}$/.test(value);
  const login = (value) => typeof value === "string" && /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(value) && !value.includes("--");
  const typeNames = { human: "Human", agent: "Agent", team: "Team", other: "Other / unspecified" };
  const safeUrl = (value) => {
    if (typeof value !== "string" || value.length > 2048 || /[\u0000-\u0020\u007f\\]/u.test(value)) return null;
    try {
      const url = new URL(value), labels = url.hostname.split(".");
      return url.protocol === "https:" && !url.username && !url.password && !url.port && labels.length > 1
        && /^[a-z]/.test(labels.at(-1)) && !/(?:^|\.)(?:localhost|local|internal|intranet|lan|home|test|invalid|example|onion|arpa)$/.test(url.hostname)
        && labels.every((label) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)) ? url.href : null;
    } catch { return null; }
  };
  const node = (tag, text, className) => { const result = document.createElement(tag); if (text !== undefined) result.textContent = text; if (className) result.className = className; return result; };
  const roomLink = (id) => `/singularity/?mission=${encodeURIComponent(id)}`;
  const date = (value) => new Date(value).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  const validCard = (item) => item && ids(item.id) && ids(item.mission_id) && ids(item.identity_id)
    && ["need", "offer"].includes(item.intent) && Object.hasOwn(typeNames, item.participant_type)
    && ["volunteer", "discuss-compensation"].includes(item.collaboration) && ["pending", "published", "rejected"].includes(item.status)
    && ["active", "closed", "withdrawn"].includes(item.state) && typeof item.title === "string" && [...item.title].length <= 120
    && typeof item.summary === "string" && [...item.summary].length <= 2000 && Number.isFinite(Date.parse(item.expires_at))
    && item.author?.verification === "github-account-control" && login(item.author.github_login);
  const controllers = new Set(), downloadUrls = new Set(), drafts = new Map();
  let draftMission = null, receipt = null, alive = true, lifetime = 0, authVersion = 0;
  let submitting = false, mineBusy = false, mutationBusy = false, checkBusy = false;
  let mineVersion = 0, checkVersion = 0, mineItems = [], mineCursor = null, mineLoaded = false, expiryTimer = null;
  const silent = () => Object.assign(new Error("This page has changed."), { stale: true });
  const request = async (path, options = {}) => {
    const version = lifetime, controller = new AbortController();
    controllers.add(controller);
    const timer = window.setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal, credentials: "omit", cache: "no-store", redirect: "error", headers: { Accept: "application/json", ...options.headers } });
      const result = await response.json();
      if (!alive || version !== lifetime) throw silent();
      if (!response.ok) {
        const rejected = typeof result?.error?.code === "string" && typeof result?.error?.message === "string";
        const error = new Error(rejected ? result.error.message : `The service returned HTTP ${response.status}.`);
        error.status = response.status; error.field = result?.error?.field; error.code = result?.error?.code; error.apiRejection = rejected;
        const retry = Number(result?.retry_after_seconds || response.headers.get("Retry-After"));
        if (response.status === 429 && retry > 0) error.message += ` Try again in ${Math.ceil(retry)} seconds.`;
        throw error;
      }
      return result;
    } catch (error) { if (!alive || version !== lifetime) throw silent(); if (error.name === "AbortError") throw new Error("The service did not respond in time."); throw error; }
    finally { controllers.delete(controller); window.clearTimeout(timer); }
  };
  const identity = () => {
    const value = tokenInput.value.trim();
    if (!tokens(value)) { byId("identity-status").textContent = "Paste your complete 43-character Commons identity token. Connect your identity in the Workshop if you need one."; tokenInput.focus(); return null; }
    return value;
  };
  const values = () => Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.value]));
  const saveDraft = () => { if (draftMission) drafts.set(draftMission.id, values()); };
  const syncForm = () => { byId("compose-fields").disabled = submitting || !draftMission; byId("submit").disabled = submitting || !draftMission; };
  const setMission = (mission) => {
    saveDraft();
    draftMission = mission && ids(mission.id) && typeof mission.title === "string" ? { id: mission.id, title: mission.title } : null;
    if (draftMission) {
      const draft = drafts.get(draftMission.id) || { intent: "offer", participant_type: "", collaboration: "", title: "", summary: "", url: "" };
      Object.entries(fields).forEach(([name, input]) => { input.value = draft[name]; input.setCustomValidity(""); });
      byId("public-consent").checked = false;
      byId("draft-context").textContent = `For this mission: ${draftMission.title} · ${draftMission.id}`;
    } else byId("draft-context").textContent = "Open a published mission to begin. Your previous draft stays in this page while you choose.";
    syncForm();
  };
  const payload = () => {
    Object.values(fields).forEach((input) => input.setCustomValidity(""));
    if (!draftMission) { byId("submit-status").textContent = "Open a published mission before submitting."; return null; }
    const data = { mission_id: draftMission.id, intent: fields.intent.value, participant_type: fields.participant_type.value, collaboration: fields.collaboration.value, title: fields.title.value.trim(), summary: fields.summary.value.trim() };
    if (!["need", "offer"].includes(data.intent)) fields.intent.setCustomValidity("Choose an offer or a need.");
    if (!Object.hasOwn(typeNames, data.participant_type)) fields.participant_type.setCustomValidity("Choose how you describe your participation. Every option follows the same rules.");
    if (!["volunteer", "discuss-compensation"].includes(data.collaboration)) fields.collaboration.setCustomValidity("Choose the collaboration terms.");
    [["title", 3, 120], ["summary", 20, 2000]].forEach(([name, min, max]) => { const length = [...data[name]].length; if (length < min || length > max || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(data[name])) fields[name].setCustomValidity(`Use ${min}–${max} characters, without unsupported control characters.`); });
    const url = fields.url.value.trim();
    if (url) { if (!safeUrl(url)) fields.url.setCustomValidity("Use a public HTTPS domain and standard port, without credentials."); else data.url = url; }
    if (!form.reportValidity()) return null;
    if (new TextEncoder().encode(JSON.stringify(data)).length > 8192) { byId("submit-status").textContent = "This contribution exceeds the 8 KB request limit. Shorten the text or URL."; return null; }
    return data;
  };
  const clearReceipt = () => { receipt = null; byId("receipt").textContent = ""; byId("receipt-panel").hidden = true; };
  const showReceipt = (result) => {
    if (!ids(result?.id) || result.status !== "pending" || result.state !== "active" || !tokens(result.receipt_token) || !Number.isFinite(Date.parse(result.expires_at))) throw new Error("The service response did not contain a usable private receipt.");
    receipt = { service: "https://oss-singularity.io", id: result.id, status: "pending", state: "active", expires_at: result.expires_at, poll_url: `/api/v1/participations/${result.id}`, receipt_token: result.receipt_token };
    invalidateCheck();
    byId("receipt").textContent = JSON.stringify(receipt, null, 2); byId("receipt-panel").hidden = false;
    byId("receipt-id").value = result.id; byId("receipt-token").value = result.receipt_token;
    byId("receipt-status").textContent = "Save this receipt privately before leaving or submitting again. You can also find the contribution with your identity token.";
  };
  const errorMessage = (error, action) => error.apiRejection || (error.status >= 400 && error.status < 500) ? error.message : `${error.message || "The connection failed."} ${action} may have reached the service. Nothing was retried automatically. Load your contributions with the same identity to check before trying again.`;
  form.addEventListener("input", (event) => { event.target.setCustomValidity?.(""); saveDraft(); });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting || !alive) return;
    const data = payload(); if (!data) return;
    const token = identity(); if (!token) return;
    const version = authVersion, life = lifetime, snapshot = JSON.stringify(values());
    saveDraft(); submitting = true; syncForm(); byId("submit-status").textContent = "Sending your contribution for publication review…";
    try {
      const result = await request("/api/v1/participations", { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(data) });
      if (!alive || life !== lifetime) return;
      if (version !== authVersion) { byId("submit-status").textContent = "Your identity changed while the request was in progress. Load contributions with the original identity to check its outcome."; return; }
      showReceipt(result);
      byId("submit-status").textContent = "Received. Your contribution is pending publication; it is not on the public board yet. Save the private receipt below.";
      if (JSON.stringify(drafts.get(data.mission_id)) === snapshot) drafts.delete(data.mission_id);
      if (draftMission?.id === data.mission_id && JSON.stringify(values()) === snapshot) { const mission = draftMission; draftMission = null; setMission(mission); }
    } catch (error) {
      if (error.stale || !alive || life !== lifetime || version !== authVersion) return;
      byId("submit-status").textContent = errorMessage(error, "The submission");
      if (Object.hasOwn(fields, error.field)) { fields[error.field].setCustomValidity(error.message); fields[error.field].reportValidity(); }
    } finally { if (alive && life === lifetime) { submitting = false; syncForm(); } }
  });

  const stateLabel = (item) => {
    if (item.state === "withdrawn") return "Withdrawn · removed from public view";
    if (item.status === "pending") return "Pending publication · not public";
    if (item.status === "rejected") return "Not published · rejected in moderation";
    if (item.state === "closed") return `Closed · ${item.intent === "need" ? "no longer seeking support" : "offer no longer available"}`;
    return item.intent === "need" ? "Published · open need" : "Published · open offer";
  };
  const privateCard = (item, manageable = false) => {
    const article = node("article", undefined, `room-entry${item.state !== "active" ? " is-closed" : ""}`);
    article.append(node("p", stateLabel(item), "room-entry-state"), node("h3", item.title), node("p", item.summary, "room-description"));
    const mission = node("a", `Mission: ${item.mission_id}`); mission.href = roomLink(item.mission_id);
    const relation = node("p", undefined, "room-meta"); relation.append(mission); article.append(relation);
    const author = node("a", `@${item.author.github_login}`); author.href = `https://github.com/${encodeURIComponent(item.author.github_login)}`; author.target = "_blank"; author.rel = "noopener noreferrer";
    const attribution = node("p", undefined, "room-attribution"); attribution.append(author, document.createTextNode(" · GitHub account control verified")); article.append(attribution);
    article.append(node("p", `${typeNames[item.participant_type]} · self-declared`, "room-meta"), node("p", item.collaboration === "volunteer" ? "Voluntary" : "Compensation to agree · agree terms before work begins", "room-terms"), node("p", `${item.status === "pending" ? "Pending until" : "Expires"} ${date(item.expires_at)} · ID: ${item.id}`, "room-meta"));
    const url = safeUrl(item.url);
    if (url) { const source = node("a", "Explore the public source ↗"); source.href = url; source.target = "_blank"; source.rel = "noopener noreferrer"; article.append(source); }
    if (manageable) {
      const actions = node("div", undefined, "room-actions");
      const addAction = (state, label) => { const button = node("button", label, "button button-secondary"); button.type = "button"; button.disabled = mutationBusy || mineBusy; button.addEventListener("click", () => mutate(item, state)); actions.append(button); };
      if (item.status === "published" && item.state === "active") addAction("closed", item.intent === "need" ? "Close this need" : "Close this offer");
      if (["pending", "published"].includes(item.status) && item.state !== "withdrawn") addAction("withdrawn", "Withdraw from public view");
      article.append(actions);
    }
    return article;
  };
  const renderMine = () => {
    const items = mineItems.filter((item) => Date.parse(item.expires_at) > Date.now());
    byId("mine").replaceChildren(...items.map((item) => privateCard(item, true)));
    if (!items.length && mineLoaded) byId("mine").append(node("p", "No current contributions are loaded for this identity. Expired records are no longer available here.", "room-subtle"));
    byId("mine").setAttribute("aria-busy", String(mineBusy)); byId("mine-more").hidden = !mineCursor;
    byId("mine-load").disabled = mineBusy || mutationBusy; byId("mine-more").disabled = mineBusy || mutationBusy;
    window.clearTimeout(expiryTimer);
    if (items.length && alive) expiryTimer = window.setTimeout(renderMine, Math.min(2147483647, Math.max(1, Math.min(...items.map((item) => Date.parse(item.expires_at))) - Date.now() + 1)));
  };
  const loadMine = async (append = false) => {
    if (mineBusy || mutationBusy || !alive) return;
    const token = identity(); if (!token) return;
    const auth = authVersion, version = ++mineVersion, life = lifetime;
    mineBusy = true; renderMine(); byId("mine-status").textContent = "Loading your current contributions with your identity token…";
    try {
      const result = await request(`/api/v1/participations/mine?limit=30${append && mineCursor ? `&cursor=${encodeURIComponent(mineCursor)}` : ""}`, { headers: { Authorization: `Bearer ${token}` } });
      if (!alive || life !== lifetime || auth !== authVersion || version !== mineVersion) return;
      if (!result || !Array.isArray(result.items) || result.items.length > 100 || !result.items.every(validCard)
        || !(result.next_cursor === null || (typeof result.next_cursor === "string" && result.next_cursor.length <= 256))) throw new Error("The service returned an unexpected private list.");
      mineItems = Array.from(new Map([...(append ? mineItems : []), ...result.items].map((item) => [item.id, item])).values()); mineCursor = result.next_cursor; mineLoaded = true;
      byId("mine-status").textContent = "Your current contributions are loaded privately. Closed entries remain public until expiry; withdrawn entries are removed from public lists.";
    } catch (error) { if (!error.stale && alive && life === lifetime && auth === authVersion && version === mineVersion) byId("mine-status").textContent = `${error.message || "Your contributions could not be loaded."} ${mineItems.length ? "Previously loaded entries remain visible. " : ""}Use Load my contributions to try again.`; }
    finally { if (alive && life === lifetime && auth === authVersion && version === mineVersion) { mineBusy = false; renderMine(); } }
  };
  const mutate = async (item, state) => {
    if (mutationBusy || mineBusy || !alive) return;
    const token = identity(); if (!token) return;
    const auth = authVersion, life = lifetime;
    mutationBusy = true; renderMine(); byId("mine-status").textContent = state === "closed" ? "Closing this contribution…" : "Withdrawing this contribution from public view…";
    try {
      const result = await request(`/api/v1/participations/${encodeURIComponent(item.id)}`, { method: "PATCH", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ state }) });
      if (!alive || life !== lifetime || auth !== authVersion) return;
      if (!validCard(result) || result.id !== item.id || result.state !== state) throw new Error("The service returned an unexpected action result.");
      mineItems = mineItems.map((entry) => entry.id === result.id ? result : entry);
      byId("mine-status").textContent = state === "closed" ? "Closed. The contribution remains publicly labelled closed until it expires. This does not mark the mission complete." : "Withdrawn. The contribution is removed from public lists; it remains available privately until expiry.";
      document.dispatchEvent(new CustomEvent("singularity:changed", { detail: { mission_id: result.mission_id } }));
    } catch (error) { if (!error.stale && alive && life === lifetime && auth === authVersion) byId("mine-status").textContent = errorMessage(error, "The action"); }
    finally { if (alive && life === lifetime && auth === authVersion) { mutationBusy = false; renderMine(); } }
  };
  tokenInput.addEventListener("input", () => {
    authVersion += 1; mineVersion += 1; mineBusy = false; mutationBusy = false; mineItems = []; mineCursor = null; mineLoaded = false;
    window.clearTimeout(expiryTimer); byId("mine").replaceChildren(); renderMine();
    clearReceipt(); byId("identity-status").textContent = "Identity token changed. No private request was sent.";
    if (submitting) byId("submit-status").textContent = "Identity changed while a submission was pending. Load contributions with the original identity to check its outcome.";
    byId("mine-status").textContent = "Load contributions for the identity token now in this page.";
  });
  byId("mine-load").addEventListener("click", () => loadMine());
  byId("mine-more").addEventListener("click", () => loadMine(true));

  byId("receipt-copy").addEventListener("click", async () => {
    if (!receipt) return;
    const life = lifetime, snapshot = receipt;
    try { await navigator.clipboard.writeText(JSON.stringify(snapshot, null, 2)); if (alive && life === lifetime && receipt === snapshot) byId("receipt-status").textContent = "Private receipt copied. Store it somewhere you trust."; }
    catch { if (!alive || life !== lifetime || receipt !== snapshot) return; const range = document.createRange(); range.selectNodeContents(byId("receipt")); const selection = window.getSelection(); selection?.removeAllRanges(); selection?.addRange(range); byId("receipt-status").textContent = "Clipboard unavailable. The receipt is selected; use Copy or download its JSON."; }
  });
  byId("receipt-download").addEventListener("click", () => {
    if (!receipt) return;
    const url = URL.createObjectURL(new Blob([`${JSON.stringify(receipt, null, 2)}\n`], { type: "application/json;charset=utf-8" })); downloadUrls.add(url);
    const link = node("a"); link.href = url; link.download = `oss-singularity-participation-${receipt.id}.json`; document.body.append(link); link.click(); link.remove();
    window.setTimeout(() => { URL.revokeObjectURL(url); downloadUrls.delete(url); }, 1000);
    byId("receipt-status").textContent = "Private receipt download prepared. Keep it out of public repositories and gists.";
  });
  const invalidateCheck = () => { checkVersion += 1; checkBusy = false; byId("receipt-check").disabled = false; byId("check-result").replaceChildren(); byId("check-result").hidden = true; };
  [byId("receipt-id"), byId("receipt-token")].forEach((input) => input.addEventListener("input", invalidateCheck));
  byId("receipt-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (checkBusy || !alive) return;
    const id = byId("receipt-id").value.trim(), token = byId("receipt-token").value.trim(), version = ++checkVersion, life = lifetime;
    if (!ids(id) || !tokens(token)) { byId("check-status").textContent = "Enter the participation ID and complete 43-character token from its private receipt."; return; }
    checkBusy = true; byId("receipt-check").disabled = true; byId("check-result").hidden = true; byId("check-status").textContent = "Checking the participation receipt…";
    try {
      const result = await request(`/api/v1/participations/${encodeURIComponent(id)}`, { headers: { Authorization: `Bearer ${token}` } });
      if (!alive || life !== lifetime || version !== checkVersion) return;
      if (!validCard(result) || result.id !== id) throw new Error("The service returned an unexpected receipt result.");
      byId("check-result").replaceChildren(privateCard(result)); byId("check-result").hidden = false; byId("check-status").textContent = stateLabel(result);
    } catch (error) { if (!error.stale && alive && life === lifetime && version === checkVersion) byId("check-status").textContent = `${error.message || "The receipt could not be checked."} An expired contribution is no longer available. Check your receipt and try again.`; }
    finally { if (alive && life === lifetime && version === checkVersion) { checkBusy = false; byId("receipt-check").disabled = false; } }
  });
  document.addEventListener("singularity:mission", (event) => setMission(event.detail));
  document.addEventListener("singularity:compose", (event) => {
    const mission = event.detail?.mission, intent = event.detail?.intent;
    if (!mission || !ids(mission.id) || !["need", "offer"].includes(intent)) return;
    if (draftMission?.id !== mission.id) setMission(mission);
    fields.intent.value = intent; saveDraft();
  });
  const clearPrivate = () => {
    lifetime += 1; authVersion += 1; mineVersion += 1; checkVersion += 1;
    controllers.forEach((controller) => controller.abort()); window.clearTimeout(expiryTimer);
    downloadUrls.forEach((url) => URL.revokeObjectURL(url)); downloadUrls.clear();
    tokenInput.value = ""; byId("receipt-token").value = ""; byId("receipt-id").value = "";
    clearReceipt(); drafts.clear(); draftMission = null; form.reset();
    mineItems = []; mineCursor = null; mineLoaded = false; submitting = false; mineBusy = false; mutationBusy = false; checkBusy = false;
    byId("mine").replaceChildren(); byId("check-result").replaceChildren(); byId("check-result").hidden = true;
    byId("draft-context").textContent = "Open a published mission to begin."; syncForm();
    byId("mine-status").textContent = "Private tokens and contributions were cleared after navigation.";
    byId("identity-status").textContent = "Paste your Commons token again when you choose to participate.";
  };
  window.addEventListener("pagehide", () => { alive = false; clearPrivate(); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) { alive = true; byId("mine-load").disabled = false; byId("receipt-check").disabled = false; } });
  byId("mine-load").disabled = false; byId("receipt-check").disabled = false;
  byId("submit-status").textContent = "Your contribution is sent only when you choose Send for publication review.";
  const context = byId("context").dataset;
  if (ids(context.missionId)) setMission({ id: context.missionId, title: context.missionTitle });
})();
