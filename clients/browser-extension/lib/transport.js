import { API_ORIGIN } from "./api.js";

export class ClientError extends Error {
  constructor(code, status = null) { super(code); this.code = code; this.status = status; }
}

// Only fixed-origin data routes. Never forward a credential to a redirect.
export function localTransport({ getToken, fetch: fetchImpl = globalThis.fetch,
  hasPermission = async () => true, timeoutMs = 20000, maxBytes = 8 * 1024 * 1024 }) {
  return async (url, options = {}) => {
    const target = new URL(url);
    if (target.origin !== API_ORIGIN || target.username || target.password || target.search || target.hash ||
        !/^\/(health|v1\/captures(?:\/[A-Za-z0-9_-]+)?|v1\/youtube\/operations(?:\/[A-Za-z0-9_-]+(?:\/markdown)?)?)$/.test(target.pathname)) {
      throw new ClientError("invalid_destination");
    }
    if (!await hasPermission()) throw new ClientError("permission_denied");
    const token = target.pathname === "/health" ? null : await getToken();
    if (!token && target.pathname !== "/health") throw new ClientError("missing_token");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(target.href, { ...options,
        headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        redirect: "error", credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer",
        signal: controller.signal });
      if (response.status >= 300 && response.status < 400) throw new ClientError("invalid_response");
      if (Number(response.headers.get("content-length")) > maxBytes) throw new ClientError("response_too_large");
      const reader = response.body?.getReader();
      const chunks = [];
      let size = 0;
      try {
        while (reader) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > maxBytes) throw new ClientError("response_too_large");
          chunks.push(value);
        }
      } finally { reader?.releaseLock(); }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      return new Response(response.status === 204 ? null : bytes, { status: response.status, headers: response.headers });
    } catch (error) {
      controller.abort();
      if (error instanceof ClientError) throw error;
      throw new ClientError(controller.signal.aborted && error?.name === "AbortError" ? "timeout" : "unavailable");
    } finally { clearTimeout(timer); }
  };
}

export async function jsonBody(response) {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get("content-type") ?? "")) throw new ClientError("invalid_response");
  try { return await response.json(); } catch { throw new ClientError("invalid_response"); }
}

export async function httpError(response) {
  let code = `http_${response.status}`;
  // No upstream message, HTML, stack or arbitrary code ever reaches storage/UI.
  const known = new Set(["operation_not_found", "operation_conflict", "queue_full", "operation_history_full",
    "markdown_unavailable", "result_not_ready", "unauthorized", "origin_denied", "request_limit",
    "operation_storage_unavailable", "operation_corrupt", "invalid_url", "invalid_languages"]);
  try {
    const body = await jsonBody(response);
    if (known.has(body?.error?.code)) code = body.error.code;
  } catch { /* The status is still useful, but it proves no capability. */ }
  return new ClientError(code, response.status);
}
