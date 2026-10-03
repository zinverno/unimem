import { API_ORIGIN } from "./api.js";
import { ClientError, jsonBody, httpError } from "./transport.js";
import { validId, safeError } from "./youtube.js";

export const AUDIO_KEY = "audioJobs";
export const AUDIO_PROFILE = "faster-whisper-base-cpu-int8-v1";
export const AUDIO_STATES = { queued: "Принято", running: "Обрабатывается", complete: "Готово", failed: "Ошибка", interrupted: "Прервано" };
const ref = value => typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
const date = value => typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value));
const requestFor = job => ({ operation_id: job.operation_id, file_ref: job.file_ref,
  declared_mime: job.declared_mime, language: job.language, captured_at: job.captured_at, profile: AUDIO_PROFILE });

export function validateAudio(body, request) {
  if (!body || body.kind !== "audio-transcription" || body.operation_id !== request.operation_id ||
      body.file_ref !== request.file_ref || body.language !== request.language ||
      body.declared_mime !== request.declared_mime || body.profile !== AUDIO_PROFILE ||
      Date.parse(body.captured_at) !== Date.parse(request.captured_at) ||
      !Object.hasOwn(AUDIO_STATES, body.state) || !date(body.accepted_at) || !date(body.updated_at) ||
      !(body.started_at === null || date(body.started_at)) || !(body.finished_at === null || date(body.finished_at)) ||
      !(body.capture_id === null || validId(body.capture_id)) || !(body.content_id === null || validId(body.content_id)) ||
      !(body.error_code === null || (typeof body.error_code === "string" && /^[a-z_]{1,80}$/.test(body.error_code))) ||
      body.result_available !== (body.state === "complete") ||
      (body.state === "complete" && (!body.capture_id || !body.content_id || body.error_code !== null)) ||
      (["complete", "failed", "interrupted"].includes(body.state) !== (body.finished_at !== null)) ||
      (["failed", "interrupted"].includes(body.state) && !body.error_code) ||
      (body.state === "queued" && body.started_at !== null) ||
      (body.state !== "queued" && body.started_at === null)) throw new ClientError("invalid_response");
  return Object.fromEntries(["kind", "operation_id", "file_ref", "language", "declared_mime", "captured_at", "profile",
    "state", "accepted_at", "updated_at", "started_at", "finished_at", "capture_id", "content_id", "error_code", "result_available"].map(k => [k, body[k]]));
}

export function audioClient(http, uploadHttp = http) {
  const base = `${API_ORIGIN}/v1/audio/operations`;
  return {
    async upload(file) {
      const form = new FormData(); form.append("file", file, "audio-upload");
      const response = await uploadHttp(`${API_ORIGIN}/v1/uploads`, { method: "POST", body: form });
      if (response.status !== 200) throw await httpError(response);
      const body = await jsonBody(response);
      if (!ref(body?.file_ref) || body.file_ref !== `sha256:${body.sha256}`) throw new ClientError("invalid_response");
      return body.file_ref;
    },
    async submit(request) {
      const response = await http(base, { method: "POST", body: JSON.stringify(request) });
      if (![200, 202].includes(response.status)) throw await httpError(response);
      return validateAudio(await jsonBody(response), request);
    },
    async status(request) {
      const response = await http(`${base}/${request.operation_id}`);
      if (response.status !== 200) throw await httpError(response);
      return validateAudio(await jsonBody(response), request);
    },
    async markdown(job) {
      const response = await http(`${base}/${job.operation_id}/markdown`);
      if (response.status !== 200) throw await httpError(response);
      if (!/^text\/markdown(?:;|$)/i.test(response.headers.get("content-type") ?? "")) throw new ClientError("invalid_response");
      try { return new TextDecoder("utf-8", { fatal: true }).decode(await response.arrayBuffer()); }
      catch { throw new ClientError("invalid_response"); }
    },
  };
}

export function audioJobs({ local, client, lock = work => navigator.locks.request("unimem-audio-jobs", work),
  newId = () => crypto.randomUUID(), now = () => new Date().toISOString() }) {
  async function list() {
    const rows = (await local.get(AUDIO_KEY))[AUDIO_KEY] ?? [];
    if (!Array.isArray(rows) || rows.length > 50) throw new ClientError("history_corrupt");
    for (const j of rows) {
      if (!validId(j?.operation_id) || j.connection !== API_ORIGIN || !date(j.captured_at) ||
          !["auto", "ru", "en"].includes(j.language) || typeof j.declared_mime !== "string" || j.declared_mime.length > 100 ||
          !(j.file_ref === null || ref(j.file_ref)) || typeof j.accepted !== "boolean" ||
          (j.accepted && !j.observed)) throw new ClientError("history_corrupt");
      if (j.observed) validateAudio(j.observed, requestFor(j));
    }
    return rows;
  }
  const save = rows => local.set({ [AUDIO_KEY]: rows });
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
    start(file, language, progress = () => {}) {
      // Freeze the user's selection before the first async boundary.
      if (!(file instanceof Blob) || file.size === 0 || file.size > 32 * 1024 * 1024) throw new ClientError("input_size_limit");
      if (!["auto", "ru", "en"].includes(language)) throw new ClientError("invalid_message");
      const job = { operation_id: newId(), file_ref: null, declared_mime: file.type, language,
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
