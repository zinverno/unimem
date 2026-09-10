/**
 * The MV3 service worker: the extension's privileged origin, and its only
 * connection to Chrome.
 *
 * Everything here is adapter code. The decisions — what counts as a capturable
 * page, what a blank selection means, what to POST, when to probe, what to show
 * — live in `lib/`, where they are tested without a browser. This file supplies
 * the real `chrome.*` implementations and does nothing else.
 *
 * There is no popup, by design: `chrome.action.onClicked` fires only for an
 * explicit user click, and that click is exactly what grants `activeTab` for
 * the current page. A popup would replace this event and take the gesture with
 * it, leaving the extension needing standing page access it does not want.
 *
 * The fetch happens *here*, in the extension origin that holds the loopback
 * host permission — never in the function injected into the page.
 */

import { readSelection, runCapture } from "./lib/capture.js";
import { sendCapture } from "./lib/api.js";
import { applyFeedback } from "./lib/action.js";
import { OUTCOME } from "./lib/outcomes.js";

chrome.action.onClicked.addListener((tab) => {
  // Chrome does not await this listener, so there is no frame above it that
  // could catch anything: the handler has to be terminal on its own. A failure
  // no outcome describes still owes the user a badge, and leaving the promise
  // to reject would strand it on `...` and log an unhandled rejection.
  runCapture(tab, {
    executeScript: injectSelectionReader,
    sendCapture: (envelope) => sendCapture(envelope, { fetch }),
    report: (result) => applyFeedback(chrome.action, tab, result),
  }).catch(() => {
    applyFeedback(chrome.action, tab, { outcome: OUTCOME.UNEXPECTED_ERROR });
  });
});

/**
 * Read the selection from the clicked tab's top-level document.
 *
 * `activeTab` is granted by the click, and `scripting` is used only from here —
 * after the gesture, never on page load. There are no static `content_scripts`,
 * so no page is touched until the user asks.
 */
async function injectSelectionReader(tabId) {
  const frames = await chrome.scripting.executeScript({
    target: { tabId },
    func: readSelection,
  });
  return frames?.[0]?.result;
}
