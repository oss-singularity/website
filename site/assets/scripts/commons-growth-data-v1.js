(() => {
  "use strict";
  // One shared read client for the home's public-record curves. The mission's
  // curve and the road-so-far journey consume this snapshot together, so the
  // project chain — list pages plus every detail — is read once per load or
  // refresh instead of racing twice, and both charts age on the same clock.
  const idPattern = /^[a-z0-9][a-z0-9-]{0,79}$/;
  const stamp = value => typeof value === "string" && value.length <= 32 && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
  // The API's largest page is 100 projects; three pages bound one snapshot's
  // request count while staying far above today's whole commons. When the
  // bound bites, the snapshot says so instead of passing an excerpt off as
  // the complete record.
  const PAGE_LIMIT = 100, PAGE_WINDOW = 3;
  const validListPage = data => data && Array.isArray(data.items) && data.items.length <= PAGE_LIMIT
    && (data.next_cursor === null || (typeof data.next_cursor === "string" && data.next_cursor.length <= 256))
    && data.items.every(item => item && idPattern.test(item.id) && typeof item.mission_id === "string" && item.mission_id.length <= 80
      && ["open", "closed", "cancelled"].includes(item.status) && stamp(item.created_at) !== null);
  // The backend enforces at most 20 milestones per project and no cap on a
  // project's commitment history, so the reader validates entry shapes and
  // follows whatever history the public record actually holds.
  const validDetail = data => data && idPattern.test(data.id) && ["open", "closed", "cancelled"].includes(data.status)
    && stamp(data.created_at) !== null && Array.isArray(data.milestones) && data.milestones.length <= 20
    && Array.isArray(data.commitments)
    && data.milestones.every(item => item && ["open", "done", "cancelled"].includes(item.status) && stamp(item.updated_at) !== null)
    && data.commitments.every(item => item && ["offered", "confirmed", "completed", "ended", "withdrawn", "declined", "cancelled"].includes(item.status) && stamp(item.updated_at) !== null);
  const SERIES = ["milestones", "commitments", "projects"];
  // Events sharing one moment collapse into a single joint jump: the
  // cumulative count leaps straight to its new level, the time axis stays
  // truthful, and no interpolation ever sees two points at the same x.
  const assemble = projects => {
    const events = { milestones: [], commitments: [], projects: [] };
    for (const project of projects) {
      events.projects.push(stamp(project.created_at));
      for (const milestone of project.milestones) if (milestone.status === "done") events.milestones.push(stamp(milestone.updated_at));
      for (const commitment of project.commitments) if (commitment.status === "completed") events.commitments.push(stamp(commitment.updated_at));
    }
    const series = {}, totals = {};
    for (const key of SERIES) {
      totals[key] = events[key].length;
      const jumps = new Map();
      let cumulative = 0;
      for (const t of [...events[key]].sort((a, b) => a - b)) { cumulative += 1; jumps.set(t, cumulative); }
      series[key] = [...jumps].map(([t, count]) => ({ t, count }));
    }
    const starts = projects.map(project => stamp(project.created_at));
    return { series, totals, startedAt: starts.length ? Math.min(...starts) : null };
  };
  const subscribers = new Set();
  let generation = 0, pending = null, snapshot = null, controller = null;
  const notify = state => { for (const listener of [...subscribers]) { try { listener(state); } catch { /* one panel's render never blocks the shared read */ } } };
  const start = () => {
    generation += 1;
    const own = generation;
    controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    const request = path => fetch(path, { signal: controller.signal, credentials: "omit", cache: "no-store", headers: { Accept: "application/json" } });
    pending = (async () => {
      try {
        const projects = [];
        let cursor = null, complete = true;
        for (let page = 0; page < PAGE_WINDOW; page += 1) {
          const query = new URLSearchParams({ limit: String(PAGE_LIMIT) });
          if (cursor) query.set("cursor", cursor);
          const listResponse = await request(`/api/v1/projects?${query}`);
          if (!listResponse.ok) throw new Error("Unavailable");
          const list = await listResponse.json();
          if (!validListPage(list)) throw new Error("Invalid projects page");
          const details = await Promise.all(list.items.map(async item => {
            const response = await request(`/api/v1/projects/${encodeURIComponent(item.id)}`);
            if (!response.ok) throw new Error("Unavailable");
            const detail = await response.json();
            if (!validDetail(detail) || detail.id !== item.id) throw new Error("Invalid project");
            return detail;
          }));
          projects.push(...details);
          cursor = list.next_cursor;
          if (!cursor) break;
        }
        if (cursor) complete = false; // the bounded window ended with more pages waiting
        const { series, totals, startedAt } = assemble(projects);
        if (own !== generation) return; // a newer read owns the cache; this answer is stale
        snapshot = { ok: true, projects, series, totals, startedAt, complete, readAt: Date.now() };
        notify(snapshot);
      } catch {
        if (own === generation) notify({ ok: false, previous: snapshot }); // the last good snapshot stays cached
      } finally {
        clearTimeout(timeout);
        if (own === generation) { pending = null; controller = null; }
      }
    })();
    return pending;
  };
  window.OssGrowthData = Object.freeze({
    // subscribe(listener): receives every state — the initial read, each
    // refresh and failures ({ ok: false }). Returns an unsubscribe function.
    subscribe(listener) {
      subscribers.add(listener);
      if (snapshot?.ok) listener(snapshot);
      else if (!pending) start();
      return () => subscribers.delete(listener);
    },
    // refresh(): renew the shared snapshot now. An in-flight read is joined
    // rather than duplicated, so no older answer can race a newer one and
    // overwrite fresh data.
    refresh() { pending ?? start(); },
  });
  addEventListener("pagehide", () => controller?.abort(), { once: true });
})();
