/**
 * The connector's HTTP client for the local UniMem capture API.
 *
 * `fetch` is injected rather than imported, so every path below — including the
 * ones that only happen when a network drops mid-request — is exercised by an
 * ordinary unit test with no server and no browser.
 *
 * Three rules shape everything here.
 *
 * **The destination is a constant.** It is never derived from the page URL, the
 * tab, the selection, or anything else a web page can influence. A page that
 * could redirect the capture somewhere else would turn this connector into an
 * exfiltration tool, so the origin is written once, at the top, and read
 * nowhere else.
 *
 * **One user action is one capture, with one id.** The id is minted once, by
 * the caller, before any request. Nothing here mints another, rebuilds the
 * envelope, restamps `captured_at`, or re-reads the selection — so however many
 * requests this module sends, they are all the same logical capture.
 *
 * **The retry is bounded, and it exists only because the server answers it.**
 * A POST that fails at the network layer leaves the outcome genuinely unknown:
 * the server may hold a finished capture the client never heard about. The
 * server now offers a completed replay — resend the same id with the same
 * request and an already-complete capture answers `200` with its existing
 * result — so this client resends, exactly once. There is no retry policy here,
 * no loop, no backoff, and no timer: one ambiguous failure buys one repeat of
 * the identical request, and nothing else does.
 *
 * The hard bound for one user action is **two POSTs and one GET.** An explicit
 * HTTP error is a definite answer and is never retried; the GET is an
 * observation of last resort and never a write.
 */

import { OUTCOME } from "./outcomes.js";

/** The documented default local deployment. Fixed, and never page-derived. */
export const API_ORIGIN = "http://127.0.0.1:8765";

/** Where a capture is submitted. */
export const CAPTURES_ENDPOINT = `${API_ORIGIN}/v1/captures`;

/** This attempt created and completed the capture. */
const CREATED = 201;

/** This was an equivalent resubmission of a capture that was already complete. */
const REPLAYED = 200;

/** The lifecycle status that means the capture is durable and normalized. */
const COMPLETE_STATUS = "complete";

/** The API's typed code for a capture id that is taken. */
const ALREADY_EXISTS_CODE = "capture_already_exists";

/** How a confirmed capture came to be confirmed. */
const CONFIRMED_BY = Object.freeze({
  /** A POST from this client created and completed it. */
  POST: "post",
  /** A POST was answered with an already-complete capture's existing result. */
  REPLAY: "replay",
  /** No POST was answered; a GET found the capture durably complete. */
  PROBE: "probe",
});

/** Where one capture's authoritative record is read. */
export function captureUrl(captureId) {
  return `${CAPTURES_ENDPOINT}/${encodeURIComponent(captureId)}`;
}

/**
 * Submit one capture. Exactly one request; retrying is the caller's decision.
 *
 * Two status codes are success, and they differ in what they claim rather than
 * in what they carry: `201` says this request created and completed the
 * capture, `200` says an equivalent one was already complete and this is its
 * result. Both are checked against the same contract before being called
 * success — a body that is not JSON, a `capture_id` that is not the one
 * submitted, a blank `content_id`, or a status other than `complete` are all
 * protocol failures. Accepting them would mean reporting "saved" on the
 * strength of a response that does not actually say so.
 *
 * Nothing else in the 2xx range is success. A `202` would mean the server
 * accepted work it has not finished, and a `204` would mean it said nothing at
 * all; neither is a capture this client may report as saved.
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
    // capture, and only resending or observing can tell — see `sendCapture`.
    return { outcome: OUTCOME.UNAVAILABLE };
  }

  if (response.status === CREATED) {
    return await readCompleted(response, envelope.id, CONFIRMED_BY.POST);
  }
  if (response.status === REPLAYED) {
    return await readCompleted(response, envelope.id, CONFIRMED_BY.REPLAY);
  }
  return await failureFor(response);
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
    return {
      outcome: OUTCOME.COMPLETE,
      captureId,
      contentId: null,
      confirmedBy: CONFIRMED_BY.PROBE,
    };
  }
  return { outcome: OUTCOME.DURABLE_STATE, captureId, status };
}

/**
 * Resolve an ambiguous POST with one observational GET.
 *
 * The last step available, and only ever the last: it is reached when no POST
 * produced an answer this client can act on. The result is marked `probed` so
 * the user can be told the difference between "saved" and "we went and looked,
 * and it is saved".
 */
export async function recoverNetworkOutcome(captureId, { fetch }) {
  const observed = await getCapture(captureId, { fetch });
  return { ...observed, probed: true };
}

/**
 * The whole client flow for one user action.
 *
 *     POST  ──► an HTTP response ────────────────► that is the answer
 *       │
 *       └─ no response at all
 *              │
 *            POST (same envelope, same id)
 *              ├─ 201 ─► created: the first request never landed
 *              ├─ 200 ─► replayed: the first request had completed
 *              ├─ 409 capture_already_exists ─► GET, and report what is there
 *              ├─ another HTTP error ─────────► that is the answer
 *              └─ no response again ──────────► GET, and report what is there
 *
 * A real HTTP failure — 409, 422, 500, 503 — is the server answering
 * deliberately, and is returned as it stands. Retrying it would resend a
 * request the server has already judged, and probing after it would add a
 * request without adding knowledge: probing after a `409` in particular is how
 * "already exists" quietly becomes "saved".
 *
 * The `409` *after* the retry is the one exception, and it is a different
 * question. It means the capture id is taken — almost certainly by this
 * client's own first request — but that the server could not answer it as a
 * completed replay: the capture may still be processing, or may have failed.
 * That is worth one look, and the look is a GET, which reads and never writes.
 */
export async function sendCapture(envelope, { fetch }) {
  const submitted = await submitCapture(envelope, { fetch });
  if (submitted.outcome !== OUTCOME.UNAVAILABLE) {
    return submitted;
  }
  return await resendAfterNetworkFailure(envelope, { fetch });
}

/**
 * The second and final POST: the identical request, under the identical id.
 *
 * This is what the server's completed-replay guarantee is for. The envelope is
 * the object the caller passed in, untouched — same id, same selection, same
 * `captured_at`, same title, same intent — because the guarantee is only
 * offered to a request the server can prove is the same one. Rebuilding any
 * part of it here would turn a replay into a conflict.
 */
async function resendAfterNetworkFailure(envelope, { fetch }) {
  const resent = await submitCapture(envelope, { fetch });

  if (resent.outcome === OUTCOME.UNAVAILABLE || isDuplicateConflict(resent)) {
    return await recoverNetworkOutcome(envelope.id, { fetch });
  }
  return resent;
}

/** Did the server refuse this POST because the capture id is already taken? */
function isDuplicateConflict(result) {
  return (
    result.outcome === OUTCOME.SERVER_ERROR &&
    result.status === 409 &&
    result.code === ALREADY_EXISTS_CODE
  );
}

/**
 * Check a success body against the contract before believing it.
 *
 * `confirmedBy` is the only thing that distinguishes a created capture from a
 * replayed one, because the bodies are identical — deliberately, on the server
 * side too. It is kept so the user can be told which happened without the
 * response needing a field that restates its own status code.
 */
async function readCompleted(response, submittedId, confirmedBy) {
  const body = await readJson(response);
  if (body === undefined || body === null || typeof body !== "object") {
    return protocolError("the success response body was not a JSON object");
  }
  if (body.capture_id !== submittedId) {
    return protocolError("the success response named a different capture");
  }
  if (typeof body.content_id !== "string" || body.content_id.trim() === "") {
    return protocolError("the success response carried no content id");
  }
  if (body.status !== COMPLETE_STATUS) {
    return protocolError("the success response did not report a complete capture");
  }
  return {
    outcome: OUTCOME.COMPLETE,
    captureId: body.capture_id,
    contentId: body.content_id,
    confirmedBy,
  };
}

/**
 * Classify a response that is not one of the contract's successes.
 *
 * A 4xx/5xx is the API answering deliberately, so its typed `code` and
 * `message` are kept for the caller. Any other unexpected status is a protocol
 * problem — notably a `202` or a `204`, neither of which this contract
 * promises and neither of which may be mistaken for a saved capture.
 */
async function failureFor(response) {
  if (response.status >= 400) {
    const { code, message } = await readErrorEnvelope(response);
    return { outcome: OUTCOME.SERVER_ERROR, status: response.status, code, message };
  }
  return protocolError(`the API answered ${response.status} where 201 or 200 was required`);
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
