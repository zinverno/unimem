import { API_ORIGIN } from "./api.js";
import { ClientError } from "./transport.js";
import { youtubeUrl, validId, validateOperation, safeError } from "./youtube.js";
import { languagesFrom } from "./settings.js";

export const HISTORY_KEY = "youtubeJobs";
export const HISTORY_LIMIT = 50;
const requestFor = (job) => ({ operation_id: job.operation_id, url: job.url, languages: job.languages });

export function jobsController({ local, client, settings, newId = () => crypto.randomUUID(), now = () => new Date().toISOString() }) {
  // ponytail: one background writer serializes this small (50-row) store;
  // use per-record storage/locks only if larger histories become a requirement.
  let tail = Promise.resolve();
  const pending = new Map();
  function serial(key, work) {
    if (pending.has(key)) return pending.get(key);
    const result = tail.then(work);
    tail = result.catch(() => {});
    pending.set(key, result);
    result.finally(() => pending.delete(key)).catch(() => {});
    return result;
  }
  async function list() {
    const rows = (await local.get(HISTORY_KEY))[HISTORY_KEY] ?? [];
    if (!Array.isArray(rows) || rows.length > HISTORY_LIMIT) throw new ClientError("history_corrupt");
    for (const row of rows) {
      if (!validId(row?.operation_id) || row.connection !== API_ORIGIN || youtubeUrl(row.url) !== row.url ||
          !["not_sent", "unconfirmed"].includes(row.delivery) || typeof row.accepted !== "boolean" ||
          typeof row.created_at !== "string" || !Number.isFinite(Date.parse(row.created_at))) throw new ClientError("history_corrupt");
      languagesFrom(row.languages);
      if (row.observed) validateOperation(row.observed, requestFor(row));
      if (row.accepted && !row.observed) throw new ClientError("history_corrupt");
    }
    return rows;
  }
  async function save(rows) { await local.set({ [HISTORY_KEY]: rows }); }
  async function find(id) {
    if (!validId(id)) throw new ClientError("invalid_message");
    const rows = await list();
    const job = rows.find((row) => row.operation_id === id);
    if (!job) throw new ClientError("local_not_found");
    return { rows, job };
  }
  async function observe(rows, job) {
    try {
      job.observed = await client.status(requestFor(job));
      job.observed_at = now();
      job.accepted = true;
      job.error = null;
    } catch (e) { job.error = safeError(e); }
    await save(rows);
    return job;
  }
  async function deliver(rows, job) {
    if (job.accepted) return observe(rows, job);
    if (!await settings.getToken()) throw new ClientError("missing_token");
    job.delivery = "unconfirmed";
    job.error = null;
    await save(rows); // BEFORE POST, including the uncertainty marker.
    try {
      job.observed = await client.submit(requestFor(job));
      job.observed_at = now();
      job.accepted = true;
    } catch (e) {
      job.error = safeError(e);
      if (["unavailable", "timeout", "invalid_response", "response_too_large"].includes(job.error.code)) {
        return observe(rows, job); // ONE observation; never automatic resubmission.
      }
    }
    await save(rows);
    return job;
  }
  async function create(url, languages, rows) {
    if (rows.length >= HISTORY_LIMIT) throw new ClientError("history_full");
    const job = { operation_id: newId(), url, languages: [...languages], connection: API_ORIGIN,
      created_at: now(), accepted: false, delivery: "not_sent", observed: null, observed_at: null, error: null };
    if (!validId(job.operation_id)) throw new ClientError("invalid_message");
    await save([job, ...rows]);
    return job;
  }
  return {
    list,
    choose(url) {
      const source = youtubeUrl(url); // Freeze before any await.
      return serial(`choose:${source}`, async () => {
        const languages = (await settings.read()).languages;
        const rows = await list();
        const previous = rows.find((j) => j.url === source && JSON.stringify(j.languages) === JSON.stringify(languages));
        // Repeated menu choice opens the existing job, even after eviction.
        return previous ?? create(source, languages, rows);
      });
    },
    send(id) {
      return serial(`send:${id}`, async () => {
        const { rows, job } = await find(id);
        if (job.delivery !== "not_sent") return observe(rows, job);
        return deliver(rows, job);
      });
    },
    refresh(id) { return serial(`refresh:${id}`, async () => { const { rows, job } = await find(id); return observe(rows, job); }); },
    retry(id) {
      return serial(`retry:${id}`, async () => {
        const { rows, job } = await find(id);
        await observe(rows, job);
        if (job.accepted || job.error?.code !== "operation_not_found") return job;
        return deliver(rows, job);
      });
    },
    again(id) {
      return serial(`again:${id}`, async () => {
        const { rows, job } = await find(id);
        const fresh = await create(job.url, job.languages, rows);
        return deliver([fresh, ...rows], fresh);
      });
    },
    markdown(id) {
      return serial(`markdown:${id}`, async () => {
        const { job } = await find(id);
        if (!job.observed?.result_available) throw new ClientError("result_not_ready");
        return client.markdown(requestFor(job));
      });
    },
    remove(id) { return serial(`remove:${id}`, async () => { const { rows } = await find(id); await save(rows.filter((j) => j.operation_id !== id)); }); },
  };
}
