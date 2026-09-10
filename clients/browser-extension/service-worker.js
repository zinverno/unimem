/**
 * The MV3 service worker: the extension's privileged origin, and its only
 * connection to Chrome.
 *
 * Everything here is adapter code. The decisions — what counts as a capturable
 * page, what a blank selection means, what a missing page snapshot means, what
 * to POST, when to probe, what to show — live in `lib/`, where they are tested
 * without a browser. This file supplies the real `chrome.*` implementations and
 * does nothing else.
 *
 * The toolbar icon answers two questions, and which one the user asked is
 * decided by how they clicked it:
 *
 *     left-click  the icon                       ->  save the selection
 *     right-click the icon -> "Save whole page"  ->  save the page's HTML
 *
 * There is no popup, by design: `chrome.action.onClicked` fires only for an
 * explicit user click, and that click is exactly what grants `activeTab` for
 * the current page. A popup would replace this event and take the gesture with
 * it, leaving the extension needing standing page access it does not want.
 * Executing a context-menu item is the same kind of gesture and grants the same
 * `activeTab`, which is why whole-page capture could be added without one new
 * host permission — `contextMenus` is the only permission this added, and it
 * grants access to no website at all.
 *
 * The fetch happens *here*, in the extension origin that holds the loopback
 * host permission — never in the function injected into the page.
 */

import { readSelection, runCapture } from "./lib/capture.js";
import { readPageHtml, runWholePageCapture } from "./lib/page.js";
import { createWholePageMenu, isWholePageMenu } from "./lib/menu.js";
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
 * Register the whole-page menu item from the installation lifecycle.
 *
 * `onInstalled` fires on install, update, and reload — not every time the
 * service worker wakes up. Chrome keeps registered menu items for the life of
 * the installation, so creating the item here is what stops a woken worker from
 * adding a second copy of it.
 */
chrome.runtime.onInstalled.addListener(() => {
  createWholePageMenu(chrome.contextMenus);
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  // Every listener hears every menu item this extension owns. Anything that is
  // not ours does nothing at all: no capture, no badge, no request.
  if (!isWholePageMenu(info)) {
    return;
  }
  // Terminal for the same reason the click listener is, and by the same means.
  runWholePageCapture(tab, {
    executeScript: injectPageReader,
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

/**
 * Read the top-level document's current serialization from the acted-on tab.
 *
 * The same shape as the selection reader, and the same permissions story:
 * `activeTab` granted by the menu activation, `scripting` used once, after the
 * gesture. No `allFrames`, so no iframe document is entered, and the injected
 * function fetches nothing.
 */
async function injectPageReader(tabId) {
  const frames = await chrome.scripting.executeScript({
    target: { tabId },
    func: readPageHtml,
  });
  return frames?.[0]?.result;
}
