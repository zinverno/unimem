import { API_ORIGIN } from "./api.js";
import { ClientError } from "./transport.js";
import { validId, safeError } from "./youtube.js";
const ref = value => typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
const date = value => typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value));

export function fileJobs({ local, client, key, maxBytes, select, validateJob, validate, requestFor, lock,
  newId = () => crypto.randomUUID(), now = () => new Date().toISOString() }) {
  async function list() {
    const rows = (await local.get(key))[key] ?? [];
    if (!Array.isArray(rows) || rows.length > 50) throw new ClientError("history_corrupt");
    for (const j of rows) {
      if (!validId(j?.operation_id) || j.connection !== API_ORIGIN || !date(j.captured_at) ||
          !validateJob(j) || typeof j.declared_mime !== "string" || j.declared_mime.length > 100 ||
          !(j.file_ref === null || ref(j.file_ref)) || typeof j.accepted !== "boolean" ||
          (j.accepted && !j.observed)) throw new ClientError("history_corrupt");
      if (j.observed) validate(j.observed, requestFor(j));
    }
    return rows;
  }
  const save = rows => local.set({ [key]: rows });
  async function observe(rows, job) {
    if (!job.file_ref) return job;
    try { job.observed = await client.status(requestFor(job)); job.accepted = true; job.error = null; }
    catch (error) { job.error = safeError(error); }
    await save(rows); return job;
  }
  async function submit(rows, job) {
    try { job.observed = await client.submit(requestFor(job)); job.accepted = true; job.error = null; }
    catch (error) { job.error = safeError(error); }
    await save(rows); return job;
  }
  return {
    list,
    start(file, selection, progress = () => {}) {
      // Freeze the user's selection before the first async boundary.
      if (!(file instanceof Blob) || file.size === 0 || file.size > maxBytes) throw new ClientError("input_size_limit");
      const fields = select(selection);
      const job = { operation_id: newId(), file_ref: null, declared_mime: file.type, ...fields,
        captured_at: now(), connection: API_ORIGIN, accepted: false, observed: null, error: null };
      if (!validId(job.operation_id) || !date(job.captured_at) || job.declared_mime.length > 100) throw new ClientError("invalid_message");
      return lock(async () => {
        const rows = await list();
        if (rows.length >= 50) throw new ClientError("history_full");
        rows.unshift(job); await save(rows);
        progress("uploading");
        try { job.file_ref = await client.upload(file); }
        catch (error) { job.error = safeError(error); await save(rows); return job; }
        await save(rows); // Source reference durable locally BEFORE the operation POST.
        progress("accepting");
        return submit(rows, job);
      });
    },
    refresh(id, retry = false) {
      return lock(async () => {
        const rows = await list(), job = rows.find(j => j.operation_id === id);
        if (!job) throw new ClientError("local_not_found");
        await observe(rows, job);
        if (retry && !job.accepted && job.file_ref && job.error?.code === "operation_not_found") return submit(rows, job);
        return job;
      });
    },
    remove(id) { return lock(async () => save((await list()).filter(j => j.operation_id !== id))); },
  };
}

