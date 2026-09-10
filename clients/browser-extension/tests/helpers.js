/** Small fakes shared by the client tests. No framework, no mocking library. */

/**
 * A `fetch` that answers from a scripted queue and records every call.
 *
 * Recording is what makes "exactly once" checkable: the assertions that matter
 * most in this connector are about requests that must *not* happen.
 */
export function fakeFetch(...responses) {
  const queue = [...responses];
  const calls = [];

  const fetch = async (url, options) => {
    calls.push({ url, options });
    if (queue.length === 0) {
      throw new Error(`unexpected extra request to ${url}`);
    }
    const next = queue.shift();
    if (typeof next === "function") {
      return next(url, options);
    }
    if (next instanceof Error) {
      throw next;
    }
    return next;
  };

  fetch.calls = calls;
  fetch.posts = () => calls.filter((call) => call.options?.method === "POST");
  fetch.gets = () => calls.filter((call) => call.options?.method === undefined);
  return fetch;
}

/** A minimal `Response` stand-in: only what the client actually reads. */
export function jsonResponse(status, body) {
  return {
    status,
    json: async () => body,
  };
}

/** A response whose body is not JSON at all. */
export function brokenResponse(status) {
  return {
    status,
    json: async () => {
      throw new SyntaxError("Unexpected token < in JSON at position 0");
    },
  };
}

/** The error a browser throws when the connection never happened. */
export function networkError() {
  return new TypeError("Failed to fetch");
}

export const SUBMITTED_ID = "3f1b2c7e-9a4d-4e51-8b6f-0c2d7a1e5b93";

export function testEnvelope(overrides = {}) {
  return {
    schema_version: "0.2",
    id: SUBMITTED_ID,
    source: { type: "browser", provider: "unimem-browser-extension", url: "https://example.com/a" },
    payload: { type: "text", mime_type: "text/plain", text: "  selected  ", title: "A page" },
    context: { captured_at: "2026-01-02T03:04:05.678Z", application: "unimem-browser-extension" },
    intent: { action: "save" },
    ...overrides,
  };
}

/**
 * The whole-page equivalent, for proving the HTTP client is modality-blind.
 *
 * The HTML carries edge whitespace, CRLF, a NBSP, and astral-plane characters,
 * so a resend that "tidied" the payload would be visible as a different body
 * rather than only as a different length.
 */
export const PAGE_HTML =
  "  <html>\r\n<head><title>Å \u00a0 — 你好</title></head>" +
  "<body><p>kept exactly 🌍</p></body>\r\n</html>  ";

export function webpageTestEnvelope(overrides = {}) {
  return {
    schema_version: "0.2",
    id: SUBMITTED_ID,
    source: { type: "browser", provider: "unimem-browser-extension", url: "https://example.com/a" },
    payload: { type: "webpage", mime_type: "text/html", html: PAGE_HTML, title: "A page" },
    context: { captured_at: "2026-01-02T03:04:05.678Z", application: "unimem-browser-extension" },
    intent: { action: "save" },
    ...overrides,
  };
}

export function createdBody(overrides = {}) {
  return {
    capture_id: SUBMITTED_ID,
    content_id: "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9",
    status: "complete",
    ...overrides,
  };
}

export function errorBody(code, message) {
  return { error: { code, message } };
}
