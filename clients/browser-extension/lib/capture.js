/**
 * The selection capture flow: what one deliberate left click actually does.
 *
 * Its sibling is `page.js`, which is what a deliberate right click on the same
 * icon does. This file is unchanged by that addition: a selection capture never
 * reads the page's HTML, and nothing here knows the other flow exists.
 *
 * Every browser API this needs arrives as an injected dependency, so the whole
 * sequence — including the paths that only happen on a browser internal page or
 * when a page refuses injection — runs under `node --test` with small fakes.
 * `service-worker.js` supplies the real Chrome implementations and adds nothing
 * else.
 *
 * The security shape of the flow is the point:
 *
 *     page          selection text only, read by an injected function that
 *                   knows nothing about UniMem
 *       |
 *     executeScript result
 *       |
 *     service worker    the privileged extension origin: builds the envelope
 *       |               and performs the fetch
 *     fixed localhost API
 *
 * The injected function never fetches. It runs in page-origin territory, where
 * the extension's `host_permissions` do not apply and page script sits beside
 * it; the request belongs to the extension service worker, which is the
 * component the loopback host permission was granted to. That split is also why
 * the server needs no CORS middleware.
 *
 * The page contributes exactly two things, and both are *data*: the selected
 * text becomes the payload, and the URL and title become capture metadata.
 * Neither ever influences where the request goes.
 */

import { OUTCOME } from "./outcomes.js";
import { buildCaptureEnvelope, isBlank, newCaptureId, nowIso } from "./envelope.js";

/** The page schemes an ordinary content capture makes sense on. */
const CAPTURABLE_PROTOCOLS = new Set(["http:", "https:"]);

/**
 * The function injected into the page. Read the selection, and nothing else.
 *
 * It must stay self-contained: `chrome.scripting.executeScript` serializes it
 * and runs it in the page, so it can close over nothing from this module. It
 * deliberately contains no URL, no fetch, and no UniMem concept at all —
 * everything it could leak is already the page's own.
 *
 * No `allFrames`, so this reads the top-level document's selection. A selection
 * inside a cross-origin iframe is out of scope for this connector and is not
 * worth `<all_urls>`.
 */
export function readSelection() {
  return window.getSelection()?.toString();
}

/**
 * Can content be captured from this URL?
 *
 * Browser internal pages (`chrome://`, `edge://`), extension pages, and `file:`
 * URLs are refused locally and visibly. Broadening permissions to reach them is
 * not a trade this connector makes.
 *
 * Shared by both capture flows on purpose. "Which pages will UniMem touch?" is
 * one question with one answer, and a whole-page flow with its own URL policy
 * would be a second answer nobody compared to the first.
 */
export function isCapturablePage(url) {
  if (typeof url !== "string" || url === "") {
    return false;
  }
  try {
    return CAPTURABLE_PROTOCOLS.has(new URL(url).protocol);
  } catch {
    return false;
  }
}

/**
 * Run one capture, from the click to the badge.
 *
 * Returns the outcome as well as reporting it, so a test can assert on the
 * result rather than on the UI. Nothing is written anywhere but the badge and
 * title: there is no `chrome.storage`, no history, and no persistent retry queue
 * or state. There is no general retry loop or policy here either — but a POST
 * that fails at the network layer leaves the outcome genuinely unknown, and
 * `sendCapture` resolves that ambiguity with its own bounded recovery (see
 * `api.js`). The server remains the only authority on what happened.
 */
export async function runCapture(tab, deps) {
  const { executeScript, sendCapture, report, newId = newCaptureId, now = nowIso } = deps;

  report(OUTCOME_BUSY);

  if (!isCapturablePage(tab?.url)) {
    return finish(report, { outcome: OUTCOME.UNSUPPORTED_PAGE });
  }

  let selection;
  try {
    selection = await executeScript(tab.id);
  } catch {
    // A page that refuses injection is refused here, locally. The exception
    // itself is dropped rather than surfaced: it would be a Chrome internal
    // message, and the user's action is the same either way.
    return finish(report, { outcome: OUTCOME.INJECTION_FAILED });
  }

  if (isBlank(selection)) {
    // Nothing is sent, and no capture id is minted: a blank click must not
    // leave a failed capture on the server that the user never intended.
    return finish(report, { outcome: OUTCOME.BLANK_SELECTION });
  }

  const envelope = buildCaptureEnvelope({
    id: newId(),
    selection,
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
const OUTCOME_BUSY = Object.freeze({ busy: true, kind: "selection" });

function finish(report, result) {
  report(result);
  return result;
}
