import { request } from "node:http";
import { SafeError, MAX_BYTES } from "./contract";

export function serverAddress(value: string): string {
  try {
    const u = new URL(value);
    if (u.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(u.hostname) ||
        u.username || u.password || u.search || u.hash || u.pathname !== "/") throw 0;
    return u.origin;
  } catch { throw new SafeError("invalid_settings"); }
}

export function receiverClient(base: string, token: string) {
  const origin = serverAddress(base);
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw new SafeError("invalid_settings");
  return (path: string, body?: object, signal?: AbortSignal): Promise<unknown> => {
    if (!/^\/v1\/receiver\/(destination|deliveries\/(next|[0-9a-f-]{36}(?:\/(claim|ack|fail))?))$/.test(path)) {
      return Promise.reject(new SafeError("invalid_request"));
    }
    return new Promise((resolve, reject) => {
      const req = request(origin + path, { method: body ? "POST" : "GET", signal,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } }, res => {
        const chunks: Buffer[] = []; let size = 0;
        res.on("data", (chunk: Buffer) => {
          size += chunk.length;
          // JSON escaping can expand each UTF-8 byte up to six bytes.
          if (size > MAX_BYTES * 6 + 8192) { req.destroy(); reject(new SafeError("response_too_large")); }
          else chunks.push(chunk);
        });
        res.on("error", () => reject(new SafeError("unavailable")));
        res.on("end", () => {
          clearTimeout(timer);
          let json: unknown;
          try { json = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks))); }
          catch { reject(new SafeError("invalid_response")); return; }
          if (res.statusCode !== 200) {
            const code = (json as { error?: { code?: string } })?.error?.code;
            const allowed = ["receiver_mismatch", "lease_active", "lease_expired", "claim_mismatch", "delivery_terminal", "delivery_not_found"];
            reject(new SafeError(res.statusCode === 401 ? "unauthorized" :
              res.statusCode === 429 ? "request_limit" : allowed.includes(code ?? "") ? code! : "unavailable"));
          } else resolve(json);
        });
      });
      const timer = setTimeout(() => { req.destroy(); reject(new SafeError("timeout")); }, 15000);
      req.on("error", () => { clearTimeout(timer); reject(new SafeError(signal?.aborted ? "stopped" : "unavailable")); });
      req.end(body ? JSON.stringify(body) : undefined);
    });
  };
}
