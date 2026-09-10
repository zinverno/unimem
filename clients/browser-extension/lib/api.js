/**
 * The connector's HTTP client for the local UniMem capture API.
 *
 * `fetch` is injected rather than imported, so every path below — including the
 * ones that only happen when a network drops mid-request — is exercised by an
 * ordinary unit test with no server and no browser.
 *
 * Two rules shape everything here.
 *
 * **The destination is a constant.** It is never derived from the page URL, the
 * tab, the selection, or anything else a web page can influence. A page that
 * could redirect the capture somewhere else would turn this connector into an
 * exfiltration tool, so the origin is written once, at the top, and read
 * nowhere else.
 *
 * **A capture is POSTed exactly once, ever.** The server deliberately has no
 * idempotency: a response lost after the server committed would make a retry
 * with the same id a `409`, and a retry with a fresh id a duplicate capture the
 * user never asked for. So when a POST fails at the network layer — the one
 * case where the outcome is genuinely unknown — the client *observes* instead of
 * retrying, with a single GET on the id it already generated. That probe reads;
 * it never writes, never re-POSTs, and never turns a server failure into a
 * local success.
 */

import { OUTCOME } from "./outcomes.js";

/** The documented default local deployment. Fixed, and never page-derived. */
export const API_ORIGIN = "http://127.0.0.1:8765";

/** Where a capture is submitted. */
export const CAPTURES_ENDPOINT = `${API_ORIGIN}/v1/captures`;

/** The only status the POST contract calls success. */
const CREATED = 201;

/** The lifecycle status that means the capture is durable and normalized. */
const COMPLETE_STATUS = "complete";

/** Where one capture's authoritative record is read. */
export function captureUrl(captureId) {
  return `${CAPTURES_ENDPOINT}/${encodeURIComponent(captureId)}`;
}

/**
 * Submit one capture. Exactly one request, and no retry of any kind.
 *
 * A `201` is checked against the contract before it is called success: a body
 * that is not JSON, a `capture_id` that is not the one submitted, a blank
 * `content_id`, or a status other than `complete` are all protocol failures.
 * Accepting them would mean reporting "saved" on the strength of a response
 * that does not actually say so.
 */
export async function submitCapture(envelope, { fetch }) {
  let response;
  try {
    response = await fetch(CAPTURES_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(envelope),
    });
  } catch {
    // No HTTP response ever arrived. The server may or may not hold this
    // capture, and only an observation can tell — see `recoverNetworkOutcome`.
    return { outcome: OUTCOME.UNAVAILABLE };
  }

  if (response.status !== CREATED) {
    return await failureFor(response);
  }
  return await readCreated(response, envelope.id);
}

/**
 * Read one capture's authoritative lifecycle record.
 *
 * Observation only. This never advances, repairs, or re-submits anything: the
 * server owns lifecycle, and a connector that "fixed" a stranded capture would
 * be inventing state the server declined to invent.
 */
export async function getCapture(captureId, { fetch }) {
  let response;
  try {
    response = await fetch(captureUrl(captureId));
  } catch {
    return { outcome: OUTCOME.UNAVAILABLE };
  }

  if (response.status === 404) {
    return { outcome: OUTCOME.UNCONFIRMED, captureId };
  }
  if (response.status !== 200) {
    return await failureFor(response);
  }

  const record = await readJson(response);
  if (record === undefined) {
    return protocolError("the capture record was not valid JSON");
  }
  const status = record?.status;
  if (typeof status !== "string" || status === "") {
    return protocolError("the capture record carried no lifecycle status");
  }
  if (status === COMPLETE_STATUS) {
    return { outcome: OUTCOME.COMPLETE, captureId, contentId: null, confirmedBy: "probe" };
  }
  return { outcome: OUTCOME.DURABLE_STATE, captureId, status };
}

/**
 * Resolve an ambiguous POST with one observational GET.
 *
 * Called only after a POST that never produced an HTTP response. The result is
 * marked `probed` so the user can be told the difference between "saved" and
 * "we went and looked, and it is saved".
 */
export async function recoverNetworkOutcome(captureId, { fetch }) {
  const observed = await getCapture(captureId, { fetch });
  return { ...observed, probed: true };
}

/**
 * The whole client flow: POST once, and observe once if — and only if — the
 * POST never reached an HTTP response.
 *
 * A real HTTP failure (409, 422, 500, 503) is a definite answer and is returned
 * as it stands. Probing after one would add a request without adding knowledge,
 * and probing after a `409` in particular is how "already exists" quietly
 * becomes "saved".
 */
export async function sendCapture(envelope, { fetch }) {
  const submitted = await submitCapture(envelope, { fetch });
  if (submitted.outcome !== OUTCOME.UNAVAILABLE) {
    return submitted;
  }
  return await recoverNetworkOutcome(envelope.id, { fetch });
}

async function readCreated(response, submittedId) {
  const body = await readJson(response);
  if (body === undefined || body === null || typeof body !== "object") {
    return protocolError("the created response body was not a JSON object");
  }
  if (body.capture_id !== submittedId) {
    return protocolError("the created response named a different capture");
  }
  if (typeof body.content_id !== "string" || body.content_id.trim() === "") {
    return protocolError("the created response carried no content id");
  }
  if (body.status !== COMPLETE_STATUS) {
    return protocolError("the created response did not report a complete capture");
  }
  return {
    outcome: OUTCOME.COMPLETE,
    captureId: body.capture_id,
    contentId: body.content_id,
    confirmedBy: "post",
  };
}

/**
 * Classify a response that is not the expected success.
 *
 * A 4xx/5xx is the API answering deliberately, so its typed `code` and
 * `message` are kept for the caller. Any other unexpected status is a protocol
 * problem — notably a `200`, which this contract never promises and which must
 * not be mistaken for a created capture.
 */
async function failureFor(response) {
  if (response.status >= 400) {
    const { code, message } = await readErrorEnvelope(response);
    return { outcome: OUTCOME.SERVER_ERROR, status: response.status, code, message };
  }
  return protocolError(`the API answered ${response.status} where 201 was required`);
}

/**
 * Read the API's stable error envelope, tolerating one that is not there.
 *
 * A malformed error body is still a connector failure with a status — never an
 * exception that escapes, and never a success.
 */
async function readErrorEnvelope(response) {
  const body = await readJson(response);
  const error = body?.error;
  if (error === undefined || error === null || typeof error !== "object") {
    return { code: null, message: null };
  }
  return {
    code: typeof error.code === "string" ? error.code : null,
    message: typeof error.message === "string" ? error.message : null,
  };
}

/** Parse a body, reporting unparseable as `undefined` rather than throwing. */
async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

function protocolError(detail) {
  return { outcome: OUTCOME.PROTOCOL_ERROR, detail };
}
