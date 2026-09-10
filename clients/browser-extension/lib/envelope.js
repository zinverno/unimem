/**
 * Building the canonical `CaptureEnvelope` a browser selection becomes.
 *
 * The shape here is schema `0.2` of UniMem's own contract, not a browser
 * dialect of it: `browser` is already a `CaptureSourceType`, `text` is already
 * a `CapturePayloadType`, `captured_at` is already required, `title` already
 * belongs on the payload, and `save` is already an `IntentAction`. Nothing in
 * this file asked the server to change, and a fixture built by
 * `buildCaptureEnvelope` is validated against the real Python contract in the
 * repository's own test suite.
 *
 * The one rule worth stating twice: **the selection is submitted exactly as the
 * page returned it.** `trim()` appears once, to decide whether a selection is
 * blank, and its result is never what gets sent. Leading and trailing
 * whitespace, tabs, CRLF, combining marks, and astral-plane characters all
 * travel through unchanged, because the canonical contracts preserve submitted
 * text and a connector that "tidied" it would be the one place that lost it.
 */

/** The canonical contract version this connector emits. */
export const SCHEMA_VERSION = "0.2";

/** Identifies this connector as the producer, on both `source` and `context`. */
export const CONNECTOR_NAME = "unimem-browser-extension";

/** Raised when a selection carries nothing worth capturing. */
export class BlankSelectionError extends Error {
  constructor() {
    super("the selection is empty or contains only whitespace");
    this.name = "BlankSelectionError";
  }
}

/**
 * Is this string worth capturing?
 *
 * The *only* place `trim()` is allowed, and only as a question. A non-string
 * (the page had no selection object at all) is blank too.
 */
export function isBlank(text) {
  return typeof text !== "string" || text.trim() === "";
}

/**
 * Mint an opaque capture id.
 *
 * `crypto.randomUUID()` and nothing else. The id is deliberately *not* derived
 * from the URL, the selected text, a digest, or the clock: those would make two
 * deliberate captures of the same passage collide, or leak page content into an
 * identifier that travels further than the capture does. Every click is a new
 * capture event, and gets a new identity.
 */
export function newCaptureId() {
  return crypto.randomUUID();
}

/**
 * Build the envelope for one selection.
 *
 * `capturedAt` is a parameter rather than a call to `Date.now()` inside, so the
 * timestamp is testable and the function stays pure. `title` is omitted
 * entirely when the page has none — never replaced with the URL, the hostname,
 * or the first line of the selection, because a fabricated title is
 * indistinguishable from one the page actually had.
 */
export function buildCaptureEnvelope({ id, selection, url, title, capturedAt }) {
  if (isBlank(selection)) {
    throw new BlankSelectionError();
  }

  const envelope = {
    schema_version: SCHEMA_VERSION,
    id,
    source: {
      type: "browser",
      provider: CONNECTOR_NAME,
      url,
    },
    payload: {
      type: "text",
      mime_type: "text/plain",
      // The page's string, untouched. See the module docstring.
      text: selection,
    },
    context: {
      captured_at: capturedAt,
      application: CONNECTOR_NAME,
    },
    intent: {
      action: "save",
    },
  };

  if (!isBlank(title)) {
    envelope.payload.title = title;
  }

  return envelope;
}

/** The current instant as a UTC ISO-8601 string, which the contract requires. */
export function nowIso() {
  return new Date().toISOString();
}
