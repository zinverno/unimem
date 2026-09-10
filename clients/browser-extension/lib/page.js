/**
 * The whole-page capture flow: what one deliberate right-click actually does.
 *
 * The sibling of `capture.js`, and deliberately not a generalization of it. The
 * two flows share a page policy (`isCapturablePage`) and a network client
 * (`api.js`), and nothing else: a selection capture never reads the page's HTML,
 * and a page capture never reads the selection. Collapsing them into one
 * parameterized flow would make "what did the user ask for?" a runtime flag
 * rather than a thing you can read.
 *
 * The security shape is the same as the selection flow's, and for the same
 * reasons:
 *
 *     page          the current top-level document's own serialization, read by
 *                   an injected function that knows nothing about UniMem
 *       |
 *     executeScript result
 *       |
 *     service worker    the privileged extension origin: builds the envelope
 *       |               and performs the fetch
 *     fixed localhost API
 *
 * **What is captured is a snapshot of the live DOM, not the page's source.**
 * `document.documentElement.outerHTML` is the browser's serialization of the
 * document *as it currently is*: script may have rewritten it since it loaded,
 * the parser may have moved or closed tags, and the doctype is not part of the
 * element being serialized. It is not the HTTP response body, it is not "view
 * source", and this module never pretends otherwise — see ADR-015. What the
 * server guarantees is narrower and exactly true: the string submitted here is
 * the string preserved there.
 */

import { OUTCOME } from "./outcomes.js";
import { isCapturablePage } from "./capture.js";
import { buildWebpageCaptureEnvelope, isBlank, newCaptureId, nowIso } from "./envelope.js";

/**
 * The function injected into the page. Serialize the document, and nothing else.
 *
 * It must stay self-contained: `chrome.scripting.executeScript` serializes it
 * and runs it in the page, so it can close over nothing from this module. Like
 * `readSelection`, it contains no URL, no fetch, and no UniMem concept at all —
 * everything it can reach is already the page's own.
 *
 * `document.documentElement` is the top-level `<html>` element. No `allFrames`,
 * so no iframe document is entered; no shadow root is walked; no stylesheet,
 * image, font, or script is fetched or inlined; and no doctype is invented to
 * make the result look like a file. The returned string is whatever the browser
 * gives back, and it is submitted exactly as returned.
 */
export function readPageHtml() {
  return document.documentElement?.outerHTML;
}

/**
 * Run one whole-page capture, from the menu item to the badge.
 *
 * Mirrors `runCapture`'s contract — it returns the outcome as well as reporting
 * it — so the two paths are equally testable and equally terminal. Nothing is
 * written anywhere but the badge and title: no `chrome.storage`, no history, no
 * queue, and no retry beyond the one bounded resend `api.js` already owns.
 *
 * The order of the three refusals is the point. The URL is judged first, so a
 * browser-internal page is never injected into. The page is read second, and
 * only a read that produced a usable string is allowed to continue. **The
 * capture id and the timestamp are minted last**, after the snapshot exists, so
 * a page that could not be read leaves no capture on the server that the user
 * never got.
 */
export async function runWholePageCapture(tab, deps) {
  const { executeScript, sendCapture, report, newId = newCaptureId, now = nowIso } = deps;

  report(PAGE_BUSY);

  if (!isCapturablePage(tab?.url)) {
    return finish(report, { outcome: OUTCOME.UNSUPPORTED_PAGE });
  }

  let html;
  try {
    html = await executeScript(tab.id);
  } catch {
    // A page that refuses injection is refused here, locally. The exception is
    // dropped rather than surfaced: it would be a Chrome internal message, and
    // the user's next action is the same either way.
    return finish(report, { outcome: OUTCOME.PAGE_CAPTURE_FAILED });
  }

  if (isBlank(html)) {
    // No string, an empty string, or whitespace only. Any of them would build a
    // webpage envelope with nothing in it, and the honest answer is that this
    // page could not be read — not that an empty page was saved.
    return finish(report, { outcome: OUTCOME.PAGE_CAPTURE_FAILED });
  }

  const envelope = buildWebpageCaptureEnvelope({
    id: newId(),
    html,
    url: tab.url,
    title: tab.title,
    capturedAt: now(),
  });

  return finish(report, await sendCapture(envelope));
}

/**
 * Marker for the in-progress report, kept out of the outcome vocabulary.
 *
 * `kind` is what lets the badge's tooltip say which of the two captures is in
 * flight without the feedback mapper learning anything about either flow.
 */
const PAGE_BUSY = Object.freeze({ busy: true, kind: "page" });

function finish(report, result) {
  report(result);
  return result;
}
