(() => {
  "use strict";
  const uuid = (v) => typeof v === "string" && /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(v);
  const missionId = (v) => typeof v === "string" && /^[a-z0-9][a-z0-9-]{0,79}$/.test(v);
  const text = (v, max) => typeof v === "string" && [...v].length <= max;
  const date = (v) => typeof v === "string" && Number.isFinite(Date.parse(v));
  const nullableId = (v) => v === null || uuid(v);
  const shortDate = (v) => new Date(v).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  const profile = (v) => v && uuid(v.identity_id) && Number.isSafeInteger(v.github_id) && v.github_id > 0
    && typeof v.github_login === "string" && /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(v.github_login) && !v.github_login.includes("--")
    && v.verification === "github-account-control" && date(v.verified_at);
  const projectLabels = { open: "Open project", closed: "Closed project", cancelled: "Cancelled project" };
  const milestoneLabels = { open: "Open milestone", done: "Done", cancelled: "Cancelled" };
  const commitmentLabels = { offered: "Offer awaiting coordinator", confirmed: "Bound commitment", declined: "Declined", withdrawn: "Offer withdrawn", ended: "Ended", cancelled: "Cancelled" };
  const commitmentActions = { confirm: "Confirm this contributor", decline: "Decline offer", withdraw: "Withdraw my offer", end: "End this commitment" };
  const summary = (v) => v && uuid(v.id) && missionId(v.mission_id) && text(v.title, 120) && text(v.purpose, 2000)
    && ["open", "closed", "cancelled"].includes(v.status) && Number.isSafeInteger(v.version) && v.version > 0
    && profile(v.coordinator) && date(v.created_at) && date(v.updated_at);
  const milestone = (v) => v && uuid(v.id) && uuid(v.project_id) && nullableId(v.parent_milestone_id)
    && text(v.title, 120) && text(v.purpose, 1000) && text(v.expected_artifact, 500)
    && Array.isArray(v.acceptance) && v.acceptance.length >= 1 && v.acceptance.length <= 8 && v.acceptance.every((s) => text(s, 300) && [...s].length >= 10)
    && ["open", "done", "cancelled"].includes(v.status) && v.scope_version === 1 && Number.isSafeInteger(v.version) && v.version > 0
    && date(v.created_at) && date(v.updated_at)
    && Array.isArray(v.depends_on) && v.depends_on.length <= 10 && v.depends_on.every(uuid)
    && typeof v.blocked === "boolean";
  const commitment = (v, inExport = false) => v && uuid(v.id) && uuid(v.milestone_id) && (inExport || uuid(v.project_id))
    && ["offered", "confirmed", "declined", "withdrawn", "ended", "cancelled"].includes(v.status)
    && v.terms === "volunteer" && v.scope_version === 1
    && date(v.created_at) && date(v.updated_at) && profile(v.contributor) && profile(v.coordinator);
  // Public reads never include another participant's offered commitment;
  // an authenticated participant view may contain offered ones plus a viewer marker.
  const detail = (v, privateView = false) => summary(v)
    && Array.isArray(v.milestones) && v.milestones.length <= 20 && v.milestones.every(milestone)
    && Array.isArray(v.commitments) && v.commitments.length <= 60 && v.commitments.every(commitment)
    && v.commitments.every((c) => privateView || c.status !== "offered")
    && (privateView || v.viewer === undefined)
    && (v.viewer === undefined || (v.viewer && v.viewer.coordinator === true));
  const exported = (v) => v && v.schema_version === 1 && v.kind === "oss-project-export" && date(v.exported_at)
    && summary(v.project) && v.project.scope_version === 1
    && Array.isArray(v.milestones) && v.milestones.length <= 20 && v.milestones.every(milestone)
    && Array.isArray(v.commitments) && v.commitments.length <= 60 && v.commitments.every((c) => commitment(c, true)
      && ["confirmed", "ended"].includes(c.status))
    && v.notice === "An export records coordination decisions and identities; it verifies no artifact and authorizes no payment.";
  const delivery = (v) => v && uuid(v.id) && uuid(v.project_id) && uuid(v.milestone_id)
    && Number.isSafeInteger(v.revision) && v.revision >= 1 && v.revision <= 10
    && v.scope_version === 1 && text(v.summary, 2000)
    && v.artifact && text(v.artifact.url, 2048) && typeof v.artifact.media_type === "string"
    && Number.isSafeInteger(v.artifact.size_bytes) && v.artifact.size_bytes >= 1
    && v.artifact.integrity && v.artifact.integrity.algorithm === "sha256"
    && /^[a-f0-9]{64}$/.test(v.artifact.integrity.digest)
    && (v.artifact.content_identifier === null || /^[a-zA-Z0-9]{9,128}$/.test(v.artifact.content_identifier))
    && (v.evidence_url === null || text(v.evidence_url, 2048))
    && profile(v.author) && date(v.created_at);
  const review = (v) => v && uuid(v.id) && uuid(v.project_id) && uuid(v.milestone_id)
    && Number.isSafeInteger(v.delivery_revision) && v.delivery_revision >= 1 && v.delivery_revision <= 10
    && ["accept", "revision_requested"].includes(v.decision)
    && (v.note === null || (text(v.note, 2000) && [...v.note].length >= 10))
    && profile(v.reviewer) && date(v.created_at);
  window.OssProjects = Object.freeze({ uuid, missionId, text, date, shortDate, profile, projectLabels, milestoneLabels, commitmentLabels, commitmentActions, summary, milestone, commitment, detail, exported, delivery, review });
})();
