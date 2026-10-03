import { API_ORIGIN } from "./api.js";
import { ClientError, jsonBody, httpError } from "./transport.js";
import { validId } from "./youtube.js";
import { fileJobs } from "./file-jobs.js";
export const VIDEO_KEY = "videoJobs";
export const VIDEO_STATES = { queued: "Принято", running: "Обрабатывается", complete: "Готово", failed: "Ошибка", interrupted: "Прервано" };
export const VIDEO_STAGES = { waiting: "Ожидание обработки", extracting: "Извлечение", transcribing: "Распознавание речи", frame_1: "Описание кадра 1", frame_2: "Описание кадра 2", frame_3: "Описание кадра 3", saving: "Сохранение результата" };
const fields = ["operation_id", "file_ref", "declared_mime", "captured_at", "speech", "frames", "describe", "language", "asr_profile", "vision_profile", "decoder", "sampling"];
const requestFor = j => Object.fromEntries(fields.map(k => [k, j[k]]));
const date = v => typeof v === "string" && v.length <= 40 && Number.isFinite(Date.parse(v));
function selection(s) {
  if (!s || ["speech", "frames", "describe"].some(k => typeof s[k] !== "boolean") ||
      !["ru", "en", "auto"].includes(s.language) || (s.describe && !s.frames) ||
      ["decoder", "sampling"].some(k => typeof s[k] !== "string" || !s[k] || s[k].length > 100) ||
      [["speech", "asr_profile"], ["describe", "vision_profile"]].some(([on, p]) => s[on] ? typeof s[p] !== "string" || !s[p] || s[p].length > 100 : s[p] !== null)) throw new ClientError("invalid_message");
  return Object.fromEntries(fields.slice(4).map(k => [k, s[k]]));
}
export function validateVideo(body, request) {
  if (!body || body.kind !== "video-notes" || fields.some(k => k === "captured_at"
    ? Date.parse(body[k]) !== Date.parse(request[k]) : body[k] !== request[k]) ||
      !Object.hasOwn(VIDEO_STATES, body.state) || !Object.hasOwn(VIDEO_STAGES, body.stage) ||
      !date(body.accepted_at) || !date(body.updated_at) ||
      !(body.started_at === null || date(body.started_at)) || !(body.finished_at === null || date(body.finished_at)) ||
      !(body.capture_id === null || validId(body.capture_id)) || !(body.content_id === null || validId(body.content_id)) ||
      !(body.error_code === null || (typeof body.error_code === "string" && /^[a-z_]{1,80}$/.test(body.error_code))) ||
      body.result_available !== (body.state === "complete") ||
      (body.state === "complete" && (!body.capture_id || !body.content_id || body.error_code !== null)) ||
      (["complete", "failed", "interrupted"].includes(body.state) !== (body.finished_at !== null)) ||
      (["failed", "interrupted"].includes(body.state) && !body.error_code) ||
      ((body.state === "queued") !== (body.started_at === null))) throw new ClientError("invalid_response");
  return Object.fromEntries([...fields, "kind", "state", "stage", "accepted_at", "updated_at", "started_at", "finished_at", "capture_id", "content_id", "error_code", "result_available"].map(k => [k, body[k]]));
}
export function videoJobs(options) {
  return fileJobs({ ...options, key: VIDEO_KEY, maxBytes: 32 * 1024 * 1024,
    lock: options.lock ?? (work => navigator.locks.request("unimem-video-jobs", work)),
    select: selection, validateJob: j => { try { selection(j); return true; } catch { return false; } },
    validate: validateVideo, requestFor,
  });
}
export function videoClient(http, uploadHttp = http, binaryHttp = http) {
  const base = `${API_ORIGIN}/v1/video/operations`;
  async function json(url, body) {
    const response = await http(url, body ? { method: "POST", body: JSON.stringify(body) } : {});
    if (![200, 202].includes(response.status)) throw await httpError(response);
    return jsonBody(response);
  }
  return {
    async capabilities() {
      const c = await json(`${API_ORIGIN}/v1/video/capabilities`);
      if (!c || ["ready", "asr", "vision"].some(k => typeof c[k] !== "boolean") ||
          ["decoder", "sampling", "asr_profile", "vision_profile"].some(k => typeof c[k] !== "string" || c[k].length > 100)) throw new ClientError("invalid_response");
      return c;
    },
    async upload(file) {
      const form = new FormData(); form.append("file", file, "video-upload");
      const response = await uploadHttp(`${API_ORIGIN}/v1/uploads`, { method: "POST", body: form });
      if (response.status !== 200) throw await httpError(response);
      const body = await jsonBody(response);
      if (!/^sha256:[0-9a-f]{64}$/.test(body?.file_ref) || body.file_ref !== `sha256:${body.sha256}`) throw new ClientError("invalid_response");
      return body.file_ref;
    },
    async submit(request) { return validateVideo(await json(base, request), request); },
    async status(request) { return validateVideo(await json(`${base}/${request.operation_id}`), request); },
    async result(job) {
      const r = await json(`${base}/${job.operation_id}/result`);
      if (typeof r?.markdown !== "string" || r.markdown_bytes !== new TextEncoder().encode(r.markdown).length ||
          r.markdown_bytes > 1024 * 1024 || !Array.isArray(r.attachments) || r.attachments.length > 3 ||
          r.delivery_version !== (r.attachments.length ? "3" : "1") ||
          !r.content || r.content.type !== "video" || r.content.source?.capture_id !== job.observed.capture_id ||
          r.attachments.some(a => !validId(a.asset_id) || a.mime_type !== "image/png" ||
            !Number.isSafeInteger(a.size_bytes) || a.size_bytes < 1 || a.size_bytes > 2 * 1024 * 1024 ||
            !/^[0-9a-f]{64}$/.test(a.sha256) || !/^unimem-[0-9a-f]{64}\.png$/.test(a.relative_name))) throw new ClientError("invalid_response");
      return r;
    },
    async frame(job, asset) {
      const response = await binaryHttp(`${base}/${job.operation_id}/frames/${asset.asset_id}`);
      if (response.status !== 200) throw await httpError(response);
      const bytes = await response.arrayBuffer();
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
      if (bytes.byteLength !== asset.size_bytes || hash !== asset.sha256 || response.headers.get("content-type") !== "image/png") throw new ClientError("invalid_response");
      return new Blob([bytes], { type: "image/png" });
    },
  };
}
