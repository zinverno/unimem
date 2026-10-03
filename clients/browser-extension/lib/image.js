import { API_ORIGIN } from "./api.js";
import { ClientError, jsonBody, httpError } from "./transport.js";
import { validId } from "./youtube.js";

import { fileJobs } from "./file-jobs.js";

export const IMAGE_KEY = "imageJobs";
export const IMAGE_STATES = { queued: "Принято", running: "Обрабатывается", complete: "Готово", failed: "Ошибка", interrupted: "Прервано" };
const ref = value => typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
const date = value => typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value));
const requestFor = job => ({ operation_id: job.operation_id, file_ref: job.file_ref,
  declared_mime: job.declared_mime, mode: job.mode, captured_at: job.captured_at });

export function validateImage(body, request) {
  if (!body || body.kind !== "image-capture" || body.operation_id !== request.operation_id ||
      body.file_ref !== request.file_ref || body.mode !== request.mode ||
      body.declared_mime !== request.declared_mime ||
      Date.parse(body.captured_at) !== Date.parse(request.captured_at) ||
      !Object.hasOwn(IMAGE_STATES, body.state) || !date(body.accepted_at) || !date(body.updated_at) ||
      !(body.started_at === null || date(body.started_at)) || !(body.finished_at === null || date(body.finished_at)) ||
      !(body.capture_id === null || validId(body.capture_id)) || !(body.content_id === null || validId(body.content_id)) ||
      !(body.error_code === null || (typeof body.error_code === "string" && /^[a-z_]{1,80}$/.test(body.error_code))) ||
      body.result_available !== (body.state === "complete") ||
      (body.state === "complete" && (!body.capture_id || !body.content_id || body.error_code !== null)) ||
      (["complete", "failed", "interrupted"].includes(body.state) !== (body.finished_at !== null)) ||
      (["failed", "interrupted"].includes(body.state) && !body.error_code) ||
      (body.state === "queued" && body.started_at !== null) ||
      (body.state !== "queued" && body.started_at === null)) throw new ClientError("invalid_response");
  return Object.fromEntries(["kind", "operation_id", "file_ref", "mode", "declared_mime", "captured_at",
    "state", "accepted_at", "updated_at", "started_at", "finished_at", "capture_id", "content_id", "error_code", "result_available"].map(k => [k, body[k]]));
}

export function imageClient(http, uploadHttp = http, binaryHttp = http) {
  const base = `${API_ORIGIN}/v1/image/operations`;
  return {
    async capabilities() {
      const response = await http(`${API_ORIGIN}/v1/image/capabilities`);
      if (response.status !== 200) throw await httpError(response);
      const body = await jsonBody(response), d = body?.description;
      if (typeof d?.ready !== "boolean" || typeof d.code !== "string" ||
          d.code.length > 80 || (d.ready && (typeof d.model !== "string" || d.model.length > 160))) throw new ClientError("invalid_response");
      return d;
    },
    async upload(file) {
      const form = new FormData(); form.append("file", file, "image-upload");
      const response = await uploadHttp(`${API_ORIGIN}/v1/uploads`, { method: "POST", body: form });
      if (response.status !== 200) throw await httpError(response);
      const body = await jsonBody(response);
      if (!ref(body?.file_ref) || body.file_ref !== `sha256:${body.sha256}`) throw new ClientError("invalid_response");
      return body.file_ref;
    },
    async submit(request) {
      const response = await http(base, { method: "POST", body: JSON.stringify(request) });
      if (![200, 202].includes(response.status)) throw await httpError(response);
      return validateImage(await jsonBody(response), request);
    },
    async status(request) {
      const response = await http(`${base}/${request.operation_id}`);
      if (response.status !== 200) throw await httpError(response);
      return validateImage(await jsonBody(response), request);
    },
    async result(job) {
      const response = await http(`${base}/${job.operation_id}/result`);
      if (response.status !== 200) throw await httpError(response);
      const r = await jsonBody(response), a = r?.attachment;
      if (typeof r?.markdown !== "string" || !["not_requested", "text", "empty", "skipped"].includes(r.ocr_status) ||
          r.markdown_bytes !== new TextEncoder().encode(r.markdown).length || r.markdown_bytes > 1024 * 1024 ||
          !a || !validId(a.asset_id) || !["image/png", "image/jpeg"].includes(a.mime_type) ||
          !Number.isSafeInteger(a.size_bytes) || a.size_bytes < 1 || a.size_bytes > 16 * 1024 * 1024 ||
          !/^[0-9a-f]{64}$/.test(a.sha256) || !/^unimem-[0-9a-f]{64}\.(png|jpg)$/.test(a.relative_name)) throw new ClientError("invalid_response");
      if (job.mode === "describe" && (!r.description ||
          !["described", "unclear"].includes(r.description.answer?.status) ||
          typeof r.description.answer.description !== "string" || !r.description.answer.description.trim() ||
          r.description.answer.description.length > 8000 || r.ocr_status !== "not_requested")) throw new ClientError("invalid_response");
      return r;
    },
    async original(job, asset) {
      const response = await binaryHttp(`${base}/${job.operation_id}/original`);
      if (response.status !== 200) throw await httpError(response);
      const bytes = await response.arrayBuffer();
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
      if (bytes.byteLength !== asset.size_bytes || hash !== asset.sha256 || response.headers.get("content-type") !== asset.mime_type) throw new ClientError("invalid_response");
      return new Blob([bytes], { type: asset.mime_type });
    },
  };
}

export function imageJobs(options) {
  return fileJobs({ ...options, key: IMAGE_KEY, maxBytes: 16 * 1024 * 1024,
    lock: options.lock ?? (work => navigator.locks.request("unimem-image-jobs", work)),
    requestFor, validate: validateImage, validateJob: j => ["original", "ocr", "describe"].includes(j.mode),
    select: mode => { if (!["original", "ocr", "describe"].includes(mode)) throw new ClientError("invalid_message"); return { mode }; },
  });
}

// Read only the bounded header before allowing the browser to allocate a preview.
// Server decode validation remains authoritative; no extension or MIME guessing.
export async function previewMime(file) {
  if (!(file instanceof Blob) || !file.size || file.size > 16 * 1024 * 1024) throw new ClientError("input_size_limit");
  const b = new Uint8Array(await file.slice(0, 1024 * 1024).arrayBuffer()), v = new DataView(b.buffer);
  let mime, width = 0, height = 0;
  if (b.length >= 33 && [137,80,78,71,13,10,26,10].every((n, i) => b[i] === n) &&
      v.getUint32(8) === 13 && v.getUint32(12) === 0x49484452) {
    mime = "image/png"; width = v.getUint32(16); height = v.getUint32(20);
  } else if (b[0] === 255 && b[1] === 216) {
    mime = "image/jpeg";
    let at = 2;
    for (let count = 0; count < 256 && at + 4 < b.length; count++) {
      if (b[at++] !== 255) break;
      while (b[at] === 255) at++;
      const marker = b[at++];
      if (at + 2 > b.length || marker === 218 || marker === 217) break;
      const size = v.getUint16(at);
      if (size < 2 || at + size > b.length) break;
      if ([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker) && size >= 8) {
        height = v.getUint16(at + 3); width = v.getUint16(at + 5); break;
      }
      at += size;
    }
  }
  if (!mime || !width || !height) throw new ClientError("unsupported_format");
  if (width * height > 40_000_000) throw new ClientError("pixel_limit");
  if (!["", "application/octet-stream", mime].includes(file.type)) throw new ClientError("mime_mismatch");
  return mime;
}
