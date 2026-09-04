(() => {
  const byId = (id) => document.getElementById(`workshop-${id}`);
  const challengeForm = byId("identity-challenge-form");
  const verifyForm = byId("identity-verify-form");
  if (!challengeForm || !verifyForm) return;

  const createButton = byId("create-proof");
  const verifyButton = byId("verify-identity");
  const challengeStatus = byId("identity-challenge-status");
  const verifyStatus = byId("identity-verify-status");
  const tokenInput = byId("identity-token");
  const controllers = new Set();
  const downloadUrls = new Set();
  const loginPattern = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/;
  const tokenPattern = /^[A-Za-z0-9_-]{43}$/;
  let challenge = null;
  let identityReceipt = null;
  let busy = false;
  let expiryTimer = null;

  const validLogin = (value) => typeof value === "string" && loginPattern.test(value) && !value.includes("--");
  const syncButtons = () => {
    createButton.disabled = busy;
    verifyButton.disabled = busy || !challenge;
    byId("copy-proof").disabled = !challenge;
    byId("download-proof").disabled = !challenge;
  };

  const post = async (path, body, bearer) => {
    const controller = new AbortController();
    controllers.add(controller);
    const timer = window.setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, {
        method: "POST", credentials: "omit", cache: "no-store", signal: controller.signal,
        headers: { Accept: "application/json", "Content-Type": "application/json", ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}) }, body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!response.ok) {
        const rejected = typeof result?.error?.code === "string" && typeof result?.error?.message === "string" && result.error.message.length > 0;
        const error = new Error(rejected ? result.error.message : `The identity service returned HTTP ${response.status}.`);
        error.status = response.status;
        error.code = result?.error?.code;
        error.apiRejection = rejected;
        const delay = Number(result?.retry_after_seconds || response.headers.get("Retry-After"));
        if (response.status === 429 && delay > 0) error.message += ` Try again in ${Math.ceil(delay)} seconds.`;
        throw error;
      }
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("The identity service did not respond in time.");
      throw error;
    } finally {
      window.clearTimeout(timer);
      controllers.delete(controller);
    }
  };

  const showChallenge = (result) => {
    const expiry = Date.parse(result?.expires_at);
    const proof = result?.proof;
    if (typeof result?.id !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(result.id)
      || !proof || proof.challenge_id !== result.id || typeof proof.network !== "string" || !tokenPattern.test(result.challenge_token || "")
      || !tokenPattern.test(proof.nonce || "") || result.gist_filename !== "oss-singularity-identity.json"
      || !Number.isFinite(expiry) || expiry <= Date.now()) {
      throw new Error("The service returned an unusable or expired account-control proof.");
    }
    challenge = { id: result.id, token: result.challenge_token, proof: { network: proof.network, challenge_id: proof.challenge_id, nonce: proof.nonce }, expires_at: result.expires_at };
    byId("identity-proof").textContent = JSON.stringify(challenge.proof, null, 2);
    byId("proof-expiry").textContent = `Proof expires ${new Date(expiry).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })} in your local time.`;
    byId("proof-panel").hidden = false;
    byId("gist-url").value = "";
    byId("identity-rotate").checked = false;
    verifyStatus.textContent = "Publish the exact public proof from the named GitHub account, then paste the gist URL here.";
    byId("proof-status").textContent = "This proof is public. It contains no GitHub password or Commons API token.";
    window.clearTimeout(expiryTimer);
    expiryTimer = window.setTimeout(() => {
      challenge = null;
      verifyStatus.textContent = "This proof has expired. Create a fresh public proof before verifying.";
      syncButtons();
    }, Math.max(1, expiry - Date.now()));
  };

  challengeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const login = byId("github-login").value.trim();
    if (!validLogin(login)) {
      challengeStatus.textContent = "Use a GitHub login of 1–39 letters, digits, or hyphens, starting and ending with a letter or digit, without consecutive hyphens.";
      byId("github-login").focus();
      return;
    }
    busy = true;
    syncButtons();
    challengeStatus.textContent = "Creating a short-lived public account-control proof…";
    try {
      showChallenge(await post("/api/v1/identity-challenges", { github_login: login }));
      challengeStatus.textContent = "Proof created. Publish it as a public gist, then verify it below.";
    } catch (error) {
      challengeStatus.textContent = error.apiRejection || (error.status >= 400 && error.status < 500)
        ? error.message : `${error.message || "The connection failed."} A challenge may have been created. Nothing has been retried automatically.`;
    } finally {
      busy = false;
      syncButtons();
    }
  });

  const showIdentity = (result) => {
    const identity = result?.identity;
    if (!identity || !validLogin(identity.github_login) || typeof identity.id !== "string"
      || !/^[A-Za-z0-9_-]{1,80}$/.test(identity.id) || !tokenPattern.test(result.api_token || "")
      || typeof identity.review_eligible !== "boolean") {
      throw new Error("The service response did not include a usable private identity receipt.");
    }
    const publicFields = ["id", "github_id", "github_login", "github_created_at", "created_at", "verified_at", "review_eligible", "review_eligible_at"];
    const profile = Object.fromEntries(publicFields.map((key) => [key, identity[key]]));
    profile.github_url = `https://github.com/${encodeURIComponent(identity.github_login)}`;
    identityReceipt = { service: "https://oss-singularity.io", identity: profile, api_token: result.api_token, rotated: result.rotated === true };
    byId("identity-receipt").textContent = JSON.stringify(identityReceipt, null, 2);
    byId("identity-receipt-panel").hidden = false;
    tokenInput.value = result.api_token;
    tokenInput.setCustomValidity("");
    tokenInput.dispatchEvent(new Event("input", { bubbles: true }));
    const eligibleAt = new Date(identity.review_eligible_at);
    const eligibility = identity.review_eligible ? "This account is eligible to submit evidence reviews."
      : `This account can submit other signals. Reviews become available after the 30-day account age requirement${Number.isNaN(eligibleAt.getTime()) ? "." : ` on ${eligibleAt.toLocaleString()}.`}`;
    byId("identity-connected").textContent = `GitHub account control verified for @${identity.github_login}. ${eligibility}`;
    byId("identity-save-status").textContent = result.rotated ? "The previous Commons token is invalid. Save this replacement privately." : "Save this scoped Commons token privately; it is not a GitHub access token.";
    challenge = null;
    window.clearTimeout(expiryTimer);
    byId("proof-panel").hidden = true;
  };

  verifyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !challenge) return;
    if (Date.parse(challenge.expires_at) <= Date.now()) {
      challenge = null;
      verifyStatus.textContent = "This proof has expired. Create a fresh proof to continue.";
      syncButtons();
      return;
    }
    let gist;
    try { gist = new URL(byId("gist-url").value.trim()); } catch { /* Validate below. */ }
    if (!gist || gist.protocol !== "https:" || gist.hostname !== "gist.github.com" || gist.username || gist.password
      || gist.port || gist.search || gist.hash || !/^\/[A-Za-z0-9-]+\/[A-Fa-f0-9]{5,64}\/?$/.test(gist.pathname)) {
      verifyStatus.textContent = "Use the public gist's HTTPS page URL, such as https://gist.github.com/your-login/your-gist-id, without a query or fragment.";
      byId("gist-url").focus();
      return;
    }
    const body = { challenge_id: challenge.id, gist_url: gist.href };
    if (byId("identity-rotate").checked) body.rotate = true;
    busy = true;
    syncButtons();
    verifyStatus.textContent = "Checking the public gist owner and proof with GitHub…";
    try {
      showIdentity(await post("/api/v1/identities", body, challenge.token));
      challengeStatus.textContent = "Account control verified. Your private identity receipt is ready below.";
    } catch (error) {
      if (error.code === "identity_exists") {
        verifyStatus.textContent = "This GitHub account already has a Commons identity. Use its saved token, or explicitly select Replace the existing Commons token and verify again. Replacement invalidates the old token.";
      } else {
        verifyStatus.textContent = error.apiRejection || (error.status >= 400 && error.status < 500) ? error.message
          : `${error.message || "The connection failed."} Verification may have completed without delivering the token. Nothing was retried. If needed, create a fresh proof and explicitly replace the existing token.`;
      }
    } finally {
      busy = false;
      syncButtons();
    }
  });

  const copy = async (text, source, status, message) => {
    try {
      await navigator.clipboard.writeText(text);
      status.textContent = message;
    } catch {
      const range = document.createRange();
      range.selectNodeContents(source);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      status.textContent = "Clipboard access is unavailable. The JSON is selected; use your browser's Copy command or download it.";
    }
  };
  const download = (value, filename, status) => {
    const url = URL.createObjectURL(new Blob([`${JSON.stringify(value, null, 2)}\n`], { type: "application/json;charset=utf-8" }));
    downloadUrls.add(url);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => { URL.revokeObjectURL(url); downloadUrls.delete(url); }, 1000);
    status.textContent = "JSON download prepared. Keep private identity receipts out of public gists and repositories.";
  };
  byId("copy-proof").addEventListener("click", () => {
    if (challenge) copy(JSON.stringify(challenge.proof, null, 2), byId("identity-proof"), byId("proof-status"), "Public proof copied. Publish this JSON in the named public gist file.");
  });
  byId("download-proof").addEventListener("click", () => {
    if (challenge) download(challenge.proof, "oss-singularity-identity.json", byId("proof-status"));
  });
  byId("copy-identity").addEventListener("click", () => {
    if (identityReceipt) copy(JSON.stringify(identityReceipt, null, 2), byId("identity-receipt"), byId("identity-save-status"), "Private Commons identity receipt copied. Store it somewhere you trust.");
  });
  byId("download-identity").addEventListener("click", () => {
    if (identityReceipt) download(identityReceipt, `oss-singularity-identity-${identityReceipt.identity.id}.json`, byId("identity-save-status"));
  });
  document.querySelectorAll("[data-open-identity]").forEach((link) => link.addEventListener("click", () => { byId("identity-wizard").open = true; }));
  window.addEventListener("pagehide", () => {
    controllers.forEach((controller) => controller.abort());
    downloadUrls.forEach((url) => URL.revokeObjectURL(url));
    downloadUrls.clear();
    window.clearTimeout(expiryTimer);
    challenge = null;
    identityReceipt = null;
    tokenInput.value = "";
    byId("identity-receipt").textContent = "";
    byId("identity-proof").textContent = "";
    byId("identity-receipt-panel").hidden = true;
    byId("proof-panel").hidden = true;
    challengeStatus.textContent = "Proofs and private tokens are cleared after navigation. Create a fresh proof when needed.";
    syncButtons();
  });
  syncButtons();
  challengeStatus.textContent = "Start with your public GitHub login. A proof is created only when you choose Create a public proof.";
})();
