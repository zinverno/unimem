import { API_ORIGIN } from "./api.js";
import { ClientError, jsonBody, httpError } from "./transport.js";
import { languagesFrom } from "./settings.js";

export const OPERATIONS = `${API_ORIGIN}/v1/youtube/operations`;
export const TERMINAL = new Set(["complete", "failed", "interrupted"]);
export const validId = (id) => typeof id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(id);

export function youtubeUrl(input) {
  try {
    if (typeof input !== "string" || input.length > 4096 || /[\s\\]/.test(input)) throw 0;
    const url = new URL(input);
    // URL normalizes explicit default ports: inspect the original authority too.
    if (!/^https?:$/.test(url.protocol) || url.username || url.password || /:\d/.test(input.split("/")[2])) throw 0;
    if ([...url.searchParams].length > 40) throw 0;
    let id;
    if (url.hostname === "youtu.be") id = url.pathname.slice(1);
    else if (["youtube.com", "www.youtube.com", "m.youtube.com"].includes(url.hostname)) {
      if (url.pathname === "/watch" && url.searchParams.getAll("v").length === 1) id = url.searchParams.get("v");
      else id = /^\/(?:shorts|embed|live)\/([A-Za-z0-9_-]{11})$/.exec(url.pathname)?.[1];
    }
    if (!/^[A-Za-z0-9_-]{11}$/.test(id ?? "")) throw 0;
    return `https://www.youtube.com/watch?v=${id}`;
  } catch { throw new ClientError("invalid_url"); }
}

export function validateOperation(body, request) {
  const date = (v) => typeof v === "string" && v.length <= 40 && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
  const nullableId = (v) => v === null || validId(v);
  const state = body?.state;
  if (!body || body.operation_id !== request.operation_id || body.url !== request.url ||
      JSON.stringify(body.languages) !== JSON.stringify(request.languages) ||
      !["queued", "running", ...TERMINAL].includes(state) ||
      !date(body.accepted_at) || !date(body.updated_at) ||
      !(body.started_at === null || date(body.started_at)) || !(body.finished_at === null || date(body.finished_at)) ||
      !nullableId(body.capture_id) || !nullableId(body.content_id) ||
      !(body.error_code === null || (typeof body.error_code === "string" && /^[a-z_]{1,80}$/.test(body.error_code))) ||
      body.result_available !== (state === "complete") ||
      (state === "complete" && (!body.capture_id || !body.content_id || body.error_code !== null)) ||
      (TERMINAL.has(state) !== (body.finished_at !== null)) ||
      (state === "queued" && body.started_at !== null) || (state !== "queued" && body.started_at === null) ||
      (["queued", "running"].includes(state) && (body.capture_id !== null || body.content_id !== null || body.error_code !== null)) ||
      (["failed", "interrupted"].includes(state) && !body.error_code) ||
      (body.content_id !== null && body.capture_id === null)) throw new ClientError("invalid_response");
  // Project the receipt, not arbitrary server fields/content, into local storage.
  return Object.fromEntries(["operation_id", "url", "languages", "state", "accepted_at", "updated_at",
    "started_at", "finished_at", "capture_id", "content_id", "error_code", "result_available"].map((key) => [key, body[key]]));
}

export function youtubeClient(fetch) {
  return {
    async check() {
      const result = { server: false, token: false, youtube: false, error: null };
      try {
        const health = await fetch(`${API_ORIGIN}/health`);
        result.server = health.status === 200 && (await jsonBody(health))?.status === "ok";
        if (!result.server) throw new ClientError("invalid_response");
        const probe = await fetch(`${OPERATIONS}/probe-${crypto.randomUUID()}`);
        const error = await httpError(probe);
        if (probe.status !== 404 || error.code !== "operation_not_found") throw error;
        result.token = true;
        result.youtube = true;
      } catch (e) { result.error = safeError(e); }
      return result;
    },
    async submit(request) {
      languagesFrom(request.languages);
      const response = await fetch(OPERATIONS, { method: "POST", body: JSON.stringify(request) });
      if (![200, 202].includes(response.status)) throw await httpError(response);
      return validateOperation(await jsonBody(response), request);
    },
    async status(request) {
      const response = await fetch(`${OPERATIONS}/${request.operation_id}`);
      if (response.status !== 200) throw await httpError(response);
      return validateOperation(await jsonBody(response), request);
    },
    async markdown(request) {
      const response = await fetch(`${OPERATIONS}/${request.operation_id}/markdown`);
      if (response.status !== 200) throw await httpError(response);
      if (!/^text\/markdown(?:;|$)/i.test(response.headers.get("content-type") ?? "")) throw new ClientError("invalid_response");
      try {
        return new TextDecoder("utf-8", { fatal: true }).decode(await response.arrayBuffer());
      } catch { throw new ClientError("invalid_response"); }
    },
  };
}

export function safeError(error) {
  return error instanceof ClientError ? { code: error.code, status: error.status } : { code: "storage_or_internal_error", status: null };
}
