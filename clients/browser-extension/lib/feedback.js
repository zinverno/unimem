/**
 * Turning an outcome into the only UI this connector has: a badge and a title.
 *
 * There is no popup, no notification, and no options page, so these two strings
 * are the entire conversation with the user. They are therefore built from the
 * `outcome` alone — never from an exception, a stack, a response body, or the
 * selected text.
 *
 * That last one matters. The selection is the user's content: it may be a
 * password they highlighted by accident or a paragraph from a private document,
 * and a browser action title is visible to anyone looking at the screen and
 * lands in screenshots. It is request payload data and nothing else, so it
 * never appears here, and it is not logged either.
 */

import { OUTCOME } from "./outcomes.js";

/** Sending. */
export const BADGE_BUSY = "...";
/** The server confirmed a complete capture. */
export const BADGE_OK = "OK";
/** Anything else — not saved, or not confirmed. */
export const BADGE_FAIL = "!";

const BUSY_TITLE = "UniMem: saving selection...";

/** The one fallback, used for any outcome this map has not been taught. */
const FALLBACK_TITLE = "UniMem: capture failed";

/** Human wording for a durable lifecycle state a probe found. */
const DURABLE_STATE_TITLES = Object.freeze({
  received: "UniMem: received, but not yet stored",
  stored: "UniMem: stored, but not yet processed",
  processing: "UniMem: capture is still processing",
  failed: "UniMem: the server could not process this capture",
});

/** Human wording for the typed error codes the API documents. */
const SERVER_ERROR_TITLES = Object.freeze({
  capture_already_exists: "UniMem: that capture already exists",
  content_conflict: "UniMem: the server already has content for this capture",
  invalid_capture_state: "UniMem: the capture is not in a state that can be processed",
  not_found: "UniMem: the capture was not found",
  unsupported_payload: "UniMem: this kind of capture is not supported yet",
  invalid_capture_envelope: "UniMem: the capture was rejected as invalid",
  invalid_request: "UniMem: the capture was rejected as invalid",
  processing_failed: "UniMem: the selection could not be processed",
  processing_configuration_error: "UniMem: the server is not configured to process this",
  data_integrity_error: "UniMem: the server could not read back its own data",
  storage_unavailable: "UniMem: the capture store is unavailable",
});

/** What to show the moment the user clicks, before anything has happened. */
export function busyFeedback() {
  return { badge: BADGE_BUSY, title: BUSY_TITLE };
}

/** What to show once the attempt has resolved, one way or the other. */
export function feedbackFor(result) {
  const outcome = result?.outcome;
  if (outcome === OUTCOME.COMPLETE) {
    return {
      badge: BADGE_OK,
      title: result.probed ? "UniMem: saved (confirmed after a network error)" : "UniMem: saved",
    };
  }
  return { badge: BADGE_FAIL, title: failureTitle(result, outcome) };
}

function failureTitle(result, outcome) {
  switch (outcome) {
    case OUTCOME.BLANK_SELECTION:
      return "UniMem: select some text first";
    case OUTCOME.UNSUPPORTED_PAGE:
      return "UniMem: cannot capture from this page";
    case OUTCOME.INJECTION_FAILED:
      return "UniMem: cannot read the selection on this page";
    case OUTCOME.UNAVAILABLE:
      return result?.probed
        ? "UniMem: service unavailable, capture outcome unknown"
        : "UniMem: service unavailable";
    case OUTCOME.UNCONFIRMED:
      return "UniMem: the capture could not be confirmed";
    case OUTCOME.DURABLE_STATE:
      return DURABLE_STATE_TITLES[result?.status] ?? FALLBACK_TITLE;
    case OUTCOME.SERVER_ERROR:
      return serverErrorTitle(result);
    case OUTCOME.PROTOCOL_ERROR:
      return "UniMem: unexpected response from the API";
    case OUTCOME.UNEXPECTED_ERROR:
      // Deliberately says nothing about what threw. Whatever it was, its
      // message is not written for a person reading a toolbar tooltip.
      return FALLBACK_TITLE;
    default:
      return FALLBACK_TITLE;
  }
}

/**
 * A known error code becomes a sentence; anything else becomes the fallback
 * plus its status. The server's own `message` is deliberately not shown: it is
 * written for an operator reading a log, and this is a browser tooltip.
 */
function serverErrorTitle(result) {
  const known = SERVER_ERROR_TITLES[result?.code];
  if (known !== undefined) {
    return known;
  }
  return typeof result?.status === "number"
    ? `${FALLBACK_TITLE} (${result.status})`
    : FALLBACK_TITLE;
}
