(() => {
  const form = document.querySelector("#workshop-proposal-form");
  const board = document.querySelector("#workshop-board");
  if (!form || !board) {
    return;
  }

  const byId = (id) => document.getElementById(`workshop-${id}`);
  const boardStatus = byId("board-status");
  const refreshButton = byId("refresh");
  const moreButton = byId("more");
  const autoRefresh = byId("auto-refresh");
  const submitButton = byId("submit");
  const submitStatus = byId("submit-status");
  const statusForm = byId("status-form");
  const statusButton = byId("check-status");
  const filters = Array.from(document.querySelectorAll("[data-workshop-filter]"));
  const kindNames = { mission: "Mission", "field-note": "Field note", project: "Project", review: "Review" };
  const scoreNames = { 1: "Not useful", 2: "Limited usefulness", 3: "Somewhat useful", 4: "Useful", 5: "Strongly useful" };
  const feeds = { missions: { items: [], cursor: null }, contributions: { items: [], cursor: null }, reviews: { items: [], cursor: null } };
  const controllers = new Set();
  const downloadUrls = new Set();
  let filter = "all";
  let loading = false;
  let hasLoaded = false;
  let boardWarning = "";
  let refreshTimer = null;
  let receipt = null;
  let submitting = false;
  let requestedSignal = new URLSearchParams(window.location.search).get("signal");

  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) {
      node.textContent = text;
    }
    if (className) {
      node.className = className;
    }
    return node;
  };

  const validId = (value) => typeof value === "string" && /^[A-Za-z0-9_-]{1,80}$/.test(value);
  if (!validId(requestedSignal)) requestedSignal = null;
  const findSignal = (id) => Object.values(feeds).flatMap((feed) => feed.items).find((item) => item.id === id && item.kind !== "review");

  const publicUrl = (value) => {
    if (!value || typeof value !== "string") {
      return null;
    }
    try {
      const url = new URL(value);
      const hostname = url.hostname.toLowerCase();
      const publicHostname = hostname.includes(".") && !/[\[\]:]/.test(hostname)
        && !/\.\d+$/.test(hostname) && !/(?:^|\.)(?:localhost|local|internal)$/.test(hostname);
      return url.protocol === "https:" && !url.username && !url.password && publicHostname ? url.href : null;
    } catch {
      return null;
    }
  };

  const request = async (path, options = {}) => {
    const controller = new AbortController();
    controllers.add(controller);
    const timeout = window.setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, {
        ...options,
        credentials: "omit",
        cache: "no-store",
        signal: controller.signal,
        headers: { Accept: "application/json", ...options.headers },
      });
      let payload;
      try {
        payload = await response.json();
      } catch {
        throw new Error("Unreadable service response. Try again later.");
      }
      if (!response.ok) {
        const error = new Error(payload?.error?.message || `The shared service returned HTTP ${response.status}.`);
        error.field = payload?.error?.field;
        error.status = response.status;
        const retry = Number(payload?.retry_after_seconds || response.headers.get("Retry-After"));
        if (response.status === 429 && retry > 0) {
          error.message += ` Try again in ${Math.ceil(retry)} seconds.`;
        }
        throw error;
      }
      return payload;
    } catch (error) {
      if (error.name === "AbortError") {
        throw new Error("The service did not respond in time.");
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
      controllers.delete(controller);
    }
  };

  const validItem = (item) => item && validId(item.id)
    && Object.hasOwn(kindNames, item.kind)
    && typeof item.title === "string" && item.title.length <= 120
    && typeof item.summary === "string" && item.summary.length <= 2000
    && item.status === "published"
    && (item.kind !== "review" || (validId(item.target_id) && Number.isInteger(item.score)
      && item.score >= 1 && item.score <= 5 && publicUrl(item.url)));

  const responseItems = (payload) => {
    if (!payload || !Array.isArray(payload.items) || payload.items.length > 100
      || !(payload.next_cursor === null || typeof payload.next_cursor === "string")
      || !payload.items.every(validItem)) {
      throw new Error("The shared service returned an unexpected board format.");
    }
    return payload;
  };

  const respondToMission = (id) => {
    byId("mission-id").value = id;
    byId("mission-id").setCustomValidity("");
    byId("kind").value = "field-note";
    updateReviewFields();
    submitStatus.textContent = "Linked to this mission. Share a useful finding or next step.";
    document.getElementById("contribute").scrollIntoView({ block: "start" });
    byId("title-input").focus({ preventScroll: true });
  };

  const reviewSignal = (item) => {
    byId("kind").value = "review";
    byId("target-id").value = item.id;
    byId("target-id").setCustomValidity("");
    updateReviewFields();
    form.dispatchEvent(new Event("input"));
    submitStatus.textContent = "Explain this signal's usefulness with evidence.";
    document.getElementById("contribute").scrollIntoView({ block: "start" });
    byId("title-input").focus({ preventScroll: true });
  };

  const signalCard = (item) => {
    const card = element("article", undefined, "workshop-card");
    card.id = `signal-${item.id}`;
    card.dataset.kind = item.kind;
    card.append(element("p", `${kindNames[item.kind]} · ${item.provenance === "seed" ? "Curated starting point" : "Community contribution"}`, "micro-label"));
    card.append(element("h3", item.title));
    if (item.kind === "review") {
      card.append(element("p", `${item.score} / 5 · ${scoreNames[item.score]}`, "workshop-review-score"));
      const target = findSignal(item.target_id);
      const link = element("a", `Review of: ${target ? target.title : item.target_id}`, "workshop-review-target");
      link.href = `/workshop/?signal=${encodeURIComponent(item.target_id)}#signal-${encodeURIComponent(item.target_id)}`;
      link.addEventListener("click", (event) => {
        event.preventDefault();
        requestedSignal = item.target_id;
        setFilter("all");
        renderBoard();
      });
      card.append(link);
      const author = item.author;
      const disclosure = element("p", "Reviewed community feedback · ", "workshop-review-disclosure");
      if (author?.verification === "github-account-control" && /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(author.github_login || "")) {
        const account = element("a", `@${author.github_login}`);
        account.href = `https://github.com/${encodeURIComponent(author.github_login)}`;
        account.target = "_blank";
        account.rel = "noopener noreferrer";
        disclosure.append(account, document.createTextNode(" · GitHub account control verified. This does not verify a person or a claim."));
      } else {
        disclosure.append(document.createTextNode("Account verification details unavailable."));
      }
      card.append(disclosure);
    }
    card.append(element("p", item.summary, "workshop-summary"));
    if (item.mission_id) {
      card.append(element("p", `Related mission: ${item.mission_id}`, "micro-label"));
    }
    const actions = element("div", undefined, "workshop-card-actions");
    const url = publicUrl(item.url);
    if (url) {
      const link = element("a", item.kind === "review" ? "Read the evidence ↗" : "Explore source ↗", "workshop-source");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      actions.append(link);
    }
    if (item.kind === "mission") {
      const button = element("button", "Respond to this mission", "button button-secondary");
      button.type = "button";
      button.addEventListener("click", () => respondToMission(item.id));
      actions.append(button);
    }
    if (item.kind !== "review") {
      const button = element("button", "Review this signal", "button button-secondary");
      button.type = "button";
      button.addEventListener("click", () => reviewSignal(item));
      actions.append(button);
    }
    card.append(actions);
    const date = new Date(item.published_at || item.created_at);
    const footer = element("p", `ID: ${item.id}`, "workshop-card-meta micro-label");
    if (!Number.isNaN(date.getTime())) {
      const time = element("time", `Published ${date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}`);
      time.dateTime = date.toISOString();
      footer.prepend(time, document.createTextNode(" · "));
    }
    card.append(footer);
    return card;
  };

  const renderBoard = () => {
    const unique = new Map();
    Object.values(feeds).forEach((feed) => feed.items.forEach((item) => unique.set(item.id, item)));
    const allItems = Array.from(unique.values()).sort((a, b) => String(b.published_at || b.created_at).localeCompare(String(a.published_at || a.created_at)));
    const items = allItems.filter((item) => filter === "all" || item.kind === filter);
    board.replaceChildren();
    if (items.length) {
      board.append(...items.map(signalCard));
    } else if (hasLoaded) {
      const empty = element("article", undefined, "workshop-empty notice");
      empty.append(element("h3", filter === "all" ? "Room for the next useful idea." : `No ${filter === "field-note" ? "field notes" : `${kindNames[filter].toLowerCase()}s`} in this view yet.`));
      empty.append(element("p", "Published contributions appear here after review. Add a focused signal to help shape what comes next."));
      const link = element("a", "Add your signal", "button button-secondary");
      link.href = "#contribute";
      empty.append(link);
      board.append(empty);
    }
    const count = `${items.length} published ${items.length === 1 ? "signal" : "signals"} shown${filter === "all" ? "" : ` · ${kindNames[filter]}`}.`;
    boardStatus.textContent = boardWarning || (hasLoaded ? count : "Loading the shared signal board…");
    moreButton.hidden = !Object.values(feeds).some((feed) => feed.cursor);
    if (requestedSignal && hasLoaded) {
      const target = document.getElementById(`signal-${requestedSignal}`);
      const message = byId("focus-status");
      message.hidden = Boolean(target);
      if (target) {
        target.tabIndex = -1;
        target.focus({ preventScroll: true });
        target.scrollIntoView({ block: "center" });
        requestedSignal = null;
      } else {
        message.textContent = `Signal ${requestedSignal} is not in this view. ${moreButton.hidden ? "It may no longer be published." : "Use Load more signals to look further back in the board."}`;
      }
    }
  };

  const loadBoard = async (append = false) => {
    if (loading) {
      return;
    }
    loading = true;
    board.setAttribute("aria-busy", "true");
    refreshButton.disabled = true;
    moreButton.disabled = true;
    boardStatus.textContent = append ? "Loading more published signals…" : "Refreshing the shared signal board…";
    const names = Object.keys(feeds).filter((name) => !append || feeds[name].cursor);
    const results = await Promise.allSettled(names.map(async (name) => {
      const cursor = append ? `&cursor=${encodeURIComponent(feeds[name].cursor)}` : "";
      return responseItems(await request(`/api/v1/${name}?limit=100${cursor}`));
    }));
    let failed = 0;
    results.forEach((result, index) => {
      const name = names[index];
      if (result.status === "fulfilled") {
        feeds[name].items = append ? [...feeds[name].items, ...result.value.items] : result.value.items;
        feeds[name].cursor = result.value.next_cursor;
        hasLoaded = true;
      } else {
        failed += 1;
      }
    });
    boardWarning = failed
      ? `${failed === names.length ? "The shared board is unavailable right now." : "Part of the shared board could not be refreshed."} ${hasLoaded ? "Available signals remain visible. " : ""}Use Refresh signals to try again.`
      : "";
    if (!failed) {
      byId("updated").textContent = `Last loaded ${new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}. Only published signals appear here.`;
    }
    loading = false;
    refreshButton.disabled = false;
    moreButton.disabled = false;
    board.setAttribute("aria-busy", "false");
    renderBoard();
  };

  const scheduleRefresh = () => {
    window.clearTimeout(refreshTimer);
    refreshTimer = null;
    if (autoRefresh.checked && !document.hidden) {
      refreshTimer = window.setTimeout(async () => {
        await loadBoard();
        scheduleRefresh();
      }, 60000);
    }
  };

  const fieldMap = { kind: byId("kind"), title: byId("title-input"), summary: byId("summary"), url: byId("source-url"), mission_id: byId("mission-id"), target_id: byId("target-id"), score: byId("score") };

  const updateReviewFields = () => {
    const reviewing = fieldMap.kind.value === "review";
    byId("review-fields").hidden = !reviewing;
    byId("mission-field").hidden = reviewing;
    fieldMap.mission_id.disabled = reviewing;
    fieldMap.url.required = reviewing;
    byId("identity-token").required = reviewing;
    byId("identity-requirement").textContent = reviewing ? "REQUIRED FOR REVIEWS" : "OPTIONAL";
    if (!reviewing) byId("identity-token").setCustomValidity("");
    byId("url-requirement").textContent = reviewing ? "REQUIRED HTTPS EVIDENCE" : "OPTIONAL HTTPS";
    [fieldMap.target_id, fieldMap.score].forEach((input) => {
      input.disabled = !reviewing;
      input.required = reviewing;
      if (!reviewing) input.setCustomValidity("");
    });
    const target = findSignal(fieldMap.target_id.value.trim());
    byId("review-target").textContent = target
      ? `Reviewing ${kindNames[target.kind].toLowerCase()}: ${target.title} · ${target.id}`
      : "Choose a published mission, field note, or project to review. The service verifies the target.";
  };

  const proposalPayload = () => {
    const payload = { kind: fieldMap.kind.value, title: fieldMap.title.value.trim(), summary: fieldMap.summary.value.trim() };
    const identityToken = byId("identity-token").value.trim();
    if ((payload.kind === "review" || identityToken) && !/^[A-Za-z0-9_-]{43}$/.test(identityToken)) {
      byId("identity-token").setCustomValidity("Paste the 43-character scoped Commons identity token. Connect your GitHub account to obtain one; reviews require it.");
    }
    if (payload.title.length < 3 || payload.title.length > 120) {
      fieldMap.title.setCustomValidity("Use a title between 3 and 120 characters, excluding surrounding spaces.");
    }
    if (payload.summary.length < 20 || payload.summary.length > 2000) {
      fieldMap.summary.setCustomValidity("Use a summary between 20 and 2,000 characters, excluding surrounding spaces.");
    }
    const url = fieldMap.url.value.trim();
    if (payload.kind === "review" && !url) fieldMap.url.setCustomValidity("An evidence review requires a public HTTPS evidence URL.");
    if (url) {
      if (!publicUrl(url)) {
        fieldMap.url.setCustomValidity("Use an HTTPS URL with a public hostname and no username or password.");
      } else {
        payload.url = url;
      }
    }
    const missionId = fieldMap.mission_id.value.trim();
    if (missionId && payload.kind !== "review") {
      if (!validId(missionId)) {
        fieldMap.mission_id.setCustomValidity("Use a published mission ID from the board.");
      } else {
        payload.mission_id = missionId;
      }
    }
    if (payload.kind === "review") {
      const targetId = fieldMap.target_id.value.trim();
      const score = Number(fieldMap.score.value);
      if (!validId(targetId)) fieldMap.target_id.setCustomValidity("Choose an existing published mission, field note, or project to review.");
      if (!Number.isInteger(score) || score < 1 || score > 5) fieldMap.score.setCustomValidity("Choose a usefulness score from 1 to 5.");
      payload.target_id = targetId;
      payload.score = score;
    }
    if (!form.reportValidity()) {
      return null;
    }
    if (new TextEncoder().encode(JSON.stringify(payload)).length > 8192) {
      submitStatus.textContent = "This submission exceeds the 8 KB request limit. Shorten the summary or source URL and try again.";
      return null;
    }
    return payload;
  };

  const showReceipt = (result) => {
    if (!validId(result?.id) || typeof result.receipt_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(result.receipt_token) || result.status !== "pending") {
      throw new Error("The service response did not contain a usable receipt. Your proposal may have been received; do not immediately resubmit.");
    }
    receipt = { service: "https://oss-singularity.io", id: result.id, status: result.status, poll_url: `/api/v1/proposals/${result.id}`, receipt_token: result.receipt_token };
    byId("receipt").textContent = JSON.stringify(receipt, null, 2);
    byId("receipt-panel").hidden = false;
    byId("check-id").value = result.id;
    byId("check-token").value = result.receipt_token;
    byId("receipt-status").textContent = "Keep this receipt private. It is not stored in your browser after navigation.";
  };

  form.addEventListener("input", (event) => {
    if (typeof event.target.setCustomValidity === "function") {
      event.target.setCustomValidity("");
    }
    if (event.target === fieldMap.target_id) updateReviewFields();
  });
  fieldMap.kind.addEventListener("change", () => {
    fieldMap.url.setCustomValidity("");
    updateReviewFields();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting) {
      return;
    }
    const payload = proposalPayload();
    if (!payload) {
      return;
    }
    submitting = true;
    submitButton.disabled = true;
    submitStatus.textContent = "Sending your signal to the review queue…";
    try {
      const identityToken = byId("identity-token").value.trim();
      const headers = { "Content-Type": "application/json" };
      if (identityToken) headers.Authorization = `Bearer ${identityToken}`;
      const result = await request("/api/v1/proposals", { method: "POST", headers, body: JSON.stringify(payload) });
      showReceipt(result);
      submitStatus.textContent = "Received for review. Save the private receipt below before leaving this page.";
      form.reset();
      byId("identity-token").value = identityToken;
      updateReviewFields();
    } catch (error) {
      const knownRejection = error.status >= 400 && error.status < 500;
      submitStatus.textContent = knownRejection
        ? error.message
        : `${error.message || "The connection to the shared service failed."} Delivery is uncertain: the proposal may have reached the review queue. This page has not retried it automatically.`;
      if (error.field && Object.hasOwn(fieldMap, error.field)) {
        fieldMap[error.field].setCustomValidity(error.message);
        fieldMap[error.field].reportValidity();
      }
    } finally {
      submitting = false;
      submitButton.disabled = false;
    }
  });

  byId("copy-receipt").addEventListener("click", async () => {
    if (!receipt) {
      return;
    }
    try {
      await navigator.clipboard.writeText(JSON.stringify(receipt, null, 2));
      byId("receipt-status").textContent = "Private receipt copied. Store it somewhere you trust.";
    } catch {
      const range = document.createRange();
      range.selectNodeContents(byId("receipt"));
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      byId("receipt-status").textContent = "Clipboard access is unavailable. The receipt is selected; use your browser's Copy command or download it.";
    }
  });
  byId("download-receipt").addEventListener("click", () => {
    if (!receipt) {
      return;
    }
    const url = URL.createObjectURL(new Blob([`${JSON.stringify(receipt, null, 2)}\n`], { type: "application/json;charset=utf-8" }));
    downloadUrls.add(url);
    const link = element("a");
    link.href = url;
    link.download = `oss-singularity-receipt-${receipt.id}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => { URL.revokeObjectURL(url); downloadUrls.delete(url); }, 1000);
    byId("receipt-status").textContent = "Private receipt download prepared. Keep this file out of public repositories.";
  });

  statusForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (statusButton.disabled) {
      return;
    }
    const id = byId("check-id").value.trim();
    const token = byId("check-token").value.trim();
    const status = byId("proposal-status");
    const result = byId("proposal-result");
    if (!validId(id) || !/^[A-Za-z0-9_-]{43}$/.test(token)) {
      status.textContent = "Enter the proposal ID and complete 43-character private token from your receipt.";
      return;
    }
    statusButton.disabled = true;
    status.textContent = "Checking the submission with your private receipt…";
    result.hidden = true;
    try {
      const proposal = await request(`/api/v1/proposals/${encodeURIComponent(id)}`, { headers: { Authorization: `Bearer ${token}` } });
      if (proposal.id !== id || !["pending", "published", "rejected"].includes(proposal.status) || typeof proposal.title !== "string") {
        throw new Error("The service returned an unexpected status format.");
      }
      result.replaceChildren(element("h3", proposal.title));
      const descriptions = { pending: "Pending review. This signal is not public yet.", published: "Published. This signal is available on the public board.", rejected: "Not published. This proposal was not accepted for the public board." };
      result.append(element("p", descriptions[proposal.status]));
      if (proposal.kind === "review" && Number.isInteger(proposal.score) && Object.hasOwn(scoreNames, proposal.score)) {
        result.append(element("p", `Evidence review: ${proposal.score} / 5 · ${scoreNames[proposal.score]} · Target: ${proposal.target_id}`));
      }
      result.hidden = false;
      status.textContent = `Submission status: ${proposal.status}.`;
    } catch (error) {
      status.textContent = error.message || "Status unavailable. Check your receipt and retry.";
    } finally {
      statusButton.disabled = false;
    }
  });

  const setFilter = (value) => {
    filter = value;
    filters.forEach((candidate) => {
      const selected = candidate.dataset.workshopFilter === filter;
      candidate.setAttribute("aria-pressed", String(selected));
      candidate.classList.toggle("is-active", selected);
    });
  };
  filters.forEach((button) => {
    button.disabled = false;
    button.addEventListener("click", () => {
      setFilter(button.dataset.workshopFilter);
      requestedSignal = null;
      byId("focus-status").hidden = true;
      renderBoard();
    });
  });
  refreshButton.addEventListener("click", () => loadBoard());
  moreButton.addEventListener("click", () => loadBoard(true));
  autoRefresh.addEventListener("change", scheduleRefresh);
  document.addEventListener("visibilitychange", scheduleRefresh);
  window.addEventListener("pagehide", () => {
    window.clearTimeout(refreshTimer);
    controllers.forEach((controller) => controller.abort());
    downloadUrls.forEach((url) => URL.revokeObjectURL(url));
    downloadUrls.clear();
    receipt = null;
    byId("receipt").textContent = "";
    byId("receipt-panel").hidden = true;
    byId("check-token").value = "";
    byId("identity-token").value = "";
  });
  window.addEventListener("pageshow", scheduleRefresh);
  [refreshButton, submitButton, statusButton, autoRefresh].forEach((button) => { button.disabled = false; });
  submitStatus.textContent = "Your signal is sent only when you choose Send for review.";
  updateReviewFields();
  loadBoard();
  const missionLink = new URLSearchParams(location.search).get("mission");
  if (validId(missionLink)) {
    let edited = false;
    form.addEventListener("input", () => { edited = true; }, { once: true });
    request(`/api/v1/missions/${encodeURIComponent(missionLink)}`).then((mission) => {
      if (!validItem(mission) || mission.kind !== "mission" || mission.id !== missionLink) throw new Error("Invalid mission");
      if (!edited && ["title-input", "summary", "mission-id"].every((id) => !byId(id).value)) {
        respondToMission(mission.id);
        submitStatus.textContent = `Work and evidence for: ${mission.title}. Describe what others can inspect.`;
      }
    }).catch(() => {
      if (!edited) submitStatus.textContent = "Mission link unavailable. Choose a published mission on the board.";
    });
  }
})();
