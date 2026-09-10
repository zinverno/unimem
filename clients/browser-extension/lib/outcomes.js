/**
 * The connector's vocabulary of results.
 *
 * One closed set, shared by the API client, the capture flow, and the feedback
 * mapper, so that "what happened" is decided once and rendered once. A caller
 * branches on `outcome` and never on a status code, an exception type, or the
 * text of a message.
 *
 * Only `COMPLETE` is success. Everything else is a reason the selection is not
 * known to be saved, and each is distinct because the user can do something
 * different about it.
 */

export const OUTCOME = Object.freeze({
  /** The server confirmed a durable, complete capture. */
  COMPLETE: "complete",

  /** Nothing was selected, or the selection was only whitespace. Nothing was sent. */
  BLANK_SELECTION: "blank_selection",

  /** Not an http(s) page — a browser internal page, an extension page, a file. */
  UNSUPPORTED_PAGE: "unsupported_page",

  /** The page refused script injection, so the selection could not be read. */
  INJECTION_FAILED: "injection_failed",

  /** The request never reached an HTTP response. The capture's fate is unknown. */
  UNAVAILABLE: "unavailable",

  /** The API answered with a deliberate error envelope. */
  SERVER_ERROR: "server_error",

  /** The API answered, but not in a shape this contract allows. */
  PROTOCOL_ERROR: "protocol_error",

  /** A probe found the capture durably recorded in a state that is not complete. */
  DURABLE_STATE: "durable_state",

  /** A probe found no such capture, so the submission is neither confirmed nor denied. */
  UNCONFIRMED: "unconfirmed",

  /**
   * Something failed that no other outcome describes — a bug, or a browser API
   * that threw. It exists so the click path always has an outcome to report:
   * Chrome does not await the action listener, so a rejection escaping it would
   * be unhandled and would leave the badge stuck on `...`.
   */
  UNEXPECTED_ERROR: "unexpected_error",
});
