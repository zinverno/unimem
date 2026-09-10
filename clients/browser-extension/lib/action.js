/**
 * Applying feedback to the browser action, safely and to the right tab.
 *
 * `chrome.action` arrives as a parameter for the same reason `fetch` does in
 * `api.js`: the two properties worth guaranteeing here are only observable by
 * watching the calls, and neither should need a browser to check.
 *
 * Two rules, both learned from what the naive version gets wrong.
 *
 * **Feedback is scoped to the tab that was clicked.** `chrome.action`'s badge
 * and title are *global* defaults unless a `tabId` is supplied, so a capture on
 * one page would otherwise leave `OK` — or `!` — sitting on every other tab the
 * user has open, describing something that never happened there.
 *
 * **Applying feedback can never throw or reject.** The tab may have closed
 * while the capture was in flight, which makes both calls fail. There is
 * nowhere left to report that to, and it must not become an unhandled rejection
 * in a service worker whose listener nobody awaits.
 */

import { busyFeedback, feedbackFor } from "./feedback.js";

/**
 * The `chrome.action` details fragment that scopes a call to one tab.
 *
 * Returns an empty object when there is no tab to scope to — a click whose tab
 * Chrome did not give us. Global feedback is the honest fallback there: it is
 * the only surface left, and the alternative is silence.
 */
export function scopeForTab(tab) {
  return typeof tab?.id === "number" ? { tabId: tab.id } : {};
}

/**
 * Show one result on the clicked tab's action.
 *
 * Terminal by construction: it returns nothing, throws nothing, and leaves no
 * pending rejection behind, so a caller can use it as the last thing that
 * happens on any path — including the path that handles a failure.
 */
export function applyFeedback(action, tab, result) {
  const { badge, title } = result?.busy === true ? busyFeedback() : feedbackFor(result);
  const scope = scopeForTab(tab);
  settle(() => action.setBadgeText({ text: badge, ...scope }));
  settle(() => action.setTitle({ title, ...scope }));
}

/**
 * Run one `chrome.action` call and absorb every way it can fail.
 *
 * These APIs both throw synchronously (invalid arguments) and reject
 * asynchronously (the tab went away), and this is the end of the line for
 * either.
 */
function settle(call) {
  try {
    Promise.resolve(call()).catch(() => {});
  } catch {
    // The tab closed mid-capture. There is no surface left to report to.
  }
}
