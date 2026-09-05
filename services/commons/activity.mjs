import { response } from './security.mjs';
import { visibleParticipation } from './participations.mjs';

const DAY = 86_400_000;
const contributions = "status = 'published' AND provenance = 'community' AND kind IN ('field-note','project')";

// A snapshot of currently public records, not an event history or online count.
export async function activity(env, now) {
  const start = Math.floor(now / DAY) * DAY - 6 * DAY;
  const end = start + 7 * DAY;
  const result = await env.DB.batch([
    env.DB.prepare(`SELECT
      (SELECT COUNT(*) FROM proposals WHERE status = 'published' AND kind = 'mission') AS missions,
      (SELECT COUNT(*) FROM proposals WHERE status = 'published' AND kind = 'mission' AND provenance = 'seed') AS editorial_missions,
      (SELECT COUNT(*) FROM proposals WHERE ${contributions}) AS contributions,
      (SELECT COUNT(*) FROM participations WHERE ${visibleParticipation} AND state = 'active' AND expires_at > ? AND intent = 'offer') AS offers,
      (SELECT COUNT(*) FROM participations WHERE ${visibleParticipation} AND state = 'active' AND expires_at > ? AND intent = 'need') AS needs`).bind(now, now),
    env.DB.prepare(`SELECT CAST((published_at - ?) / ? AS INTEGER) AS day, COUNT(*) AS count
      FROM proposals WHERE ${contributions} AND published_at >= ? AND published_at < ? GROUP BY day`).bind(start, DAY, start, end),
    env.DB.prepare(`SELECT CAST((published_at - ?) / ? AS INTEGER) AS day, COUNT(*) AS count
      FROM participations WHERE ${visibleParticipation} AND state IN ('active','closed') AND expires_at > ?
      AND published_at >= ? AND published_at < ? GROUP BY day`).bind(start, DAY, now, start, end),
  ]);
  const { editorial_missions, ...totals } = result[0].results[0];
  const days = Array.from({ length: 7 }, (_, index) => ({ date: new Date(start + index * DAY).toISOString().slice(0, 10), contributions: 0, participations: 0 }));
  for (const row of result[1].results) days[row.day].contributions = row.count;
  for (const row of result[2].results) days[row.day].participations = row.count;
  return response({ generated_at: new Date(now).toISOString(), window: { days: 7, timezone: 'UTC' }, totals, editorial_missions, days });
}
