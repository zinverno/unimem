/**
 * Building the canonical `CaptureEnvelope` a browser capture becomes.
 *
 * There are two builders, one per capture intent, and they are deliberately
 * separate rather than one builder with a mode flag. A selection produces a
 * `TEXT` payload carrying `payload.text`; a whole page produces a `WEBPAGE`
 * payload carrying `payload.html`. Nothing decides between them at runtime:
 * the caller already knows which gesture the user made, and an envelope whose
 * payload type depends on which field happened to be populated is exactly the
 * ambiguity `CaptureIntake` refuses on the other side.
 *
 * The shape here is schema `0.2` of UniMem's own contract, not a browser
 * dialect of it: `browser` is already a `CaptureSourceType`, `text` and
 * `webpage` are already `CapturePayloadType` members, `html` already belongs on
 * the payload, `captured_at` is already required, `title` already belongs on
 * the payload, and `save` is already an `IntentAction`. Nothing in this file
 * asked the server to change, and fixtures built by both builders are validated
 * against the real Python contract in the repository's own test suite.
 *
 * The one rule worth stating twice: **what the page returned is submitted
 * exactly as the page returned it** — the selected text in one case, the
 * document serialization in the other. `trim()` appears once, to decide whether
 * a string is blank, and its result is never what gets sent. Leading and
 * trailing whitespace, tabs, CRLF, NBSP, combining marks, and astral-plane
 * characters all travel through unchanged, because the canonical contracts
 * preserve submitted material and a connector that "tidied" it would be the one
 * place that lost it.
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

/** Raised when the page produced no serialization worth capturing. */
export class BlankPageError extends Error {
  constructor() {
    super("the page snapshot is empty or contains only whitespace");
    this.name = "BlankPageError";
  }
}

/**
 * Is this string worth capturing?
 *
 * The *only* place `trim()` is allowed, and only as a question — its result is
 * never what gets submitted. A non-string is blank too: the page had no
 * selection object at all, or `executeScript` came back with nothing.
 */
export function isBlank(text) {
  return typeof text !== "string" || text.trim() === "";
}

/**
 * Mint an opaque capture id.
 *
 * `crypto.randomUUID()` and nothing else. The id is deliberately *not* derived
 * from the URL, the selected text, the page HTML, a digest, or the clock: those
 * would make two deliberate captures of the same passage or the same page
 * collide, or leak page content into an identifier that travels further than
 * the capture does. Every gesture is a new capture event, and gets a new
 * identity.
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

/**
 * Build the envelope for one whole-page snapshot.
 *
 * `html` is the string `document.documentElement.outerHTML` returned, and it is
 * placed on the payload untouched — not trimmed, not re-serialized, not
 * line-ending normalized, not Unicode normalized, and never prefixed with a
 * doctype this connector did not receive. `document.documentElement` is an
 * element; its serialization has no doctype in it, and inventing one would make
 * the submitted snapshot claim to be a document it is not.
 *
 * `payload.text` and `payload.file_ref` are absent, deliberately and not by
 * omission: `CaptureIntake` refuses a webpage envelope that carries `html`
 * alongside either of them rather than choosing which one to store, so a
 * connector that "helpfully" also sent the visible text would make every page
 * capture a `422`.
 *
 * `title` is the *tab's* title, and only when the tab has one. It is never
 * extracted from the HTML here: the server already reads the document's own
 * `<title>` when no capture title is submitted, and doing it in two places
 * would mean two answers to the same question.
 */
export function buildWebpageCaptureEnvelope({ id, html, url, title, capturedAt }) {
  if (isBlank(html)) {
    throw new BlankPageError();
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
      type: "webpage",
      mime_type: "text/html",
      // The document's serialization, untouched. See the module docstring.
      html,
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
