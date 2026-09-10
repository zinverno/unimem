/**
 * The one context-menu item, and the only new UX this connector has.
 *
 * The toolbar action now answers two different questions, and which one the
 * user asked is decided by how they clicked it:
 *
 *     left-click  the icon  ->  save the selection      (chrome.action.onClicked)
 *     right-click the icon  ->  save the whole page     (this menu item)
 *
 * Both are explicit user gestures on the extension's own icon, and both grant
 * `activeTab` for that tab and that gesture alone. That is the whole reason the
 * item lives on the *action* context rather than on the page: a page-context or
 * selection-context entry would put UniMem in the right-click menu of every
 * page the user ever opens, which is a much louder presence than this connector
 * has earned, and it is not what makes the capture possible.
 *
 * The identifiers below are the contract between `chrome.runtime.onInstalled`,
 * which creates the item, and `chrome.contextMenus.onClicked`, which has to
 * recognize it among menu items other extensions registered. They are constants
 * for exactly that reason: a menu whose id is built at runtime is a menu whose
 * click handler cannot be sure the click was meant for it.
 */

/** The stable id this item is created with and recognized by. */
export const WHOLE_PAGE_MENU_ID = "unimem-save-whole-page";

/** What the user reads in the menu. */
export const WHOLE_PAGE_MENU_TITLE = "Save whole page to UniMem";

/**
 * The action's own context, and nothing else.
 *
 * Not `page`, not `selection`, not `link`, not `image`, not `all`.
 */
export const WHOLE_PAGE_MENU_CONTEXTS = Object.freeze(["action"]);

/**
 * Create the item, once, from the installation lifecycle.
 *
 * Chrome keeps registered context menus for the life of an installed extension,
 * so this is called from `chrome.runtime.onInstalled` — which fires on install,
 * on update, and on reload — and *not* on every service-worker start. A worker
 * that wakes up, creates the item again, and finds the id taken is the
 * duplicate-entry bug this arrangement exists to avoid.
 *
 * Terminal by construction, like `applyFeedback`: creating a menu item is
 * something Chrome can refuse, there is no user waiting on the answer, and a
 * throw from an `onInstalled` listener nobody awaits would be an unhandled
 * rejection in the service worker.
 */
export function createWholePageMenu(contextMenus) {
  try {
    contextMenus.create({
      id: WHOLE_PAGE_MENU_ID,
      title: WHOLE_PAGE_MENU_TITLE,
      contexts: [...WHOLE_PAGE_MENU_CONTEXTS],
    });
  } catch {
    // The item already exists, or Chrome refused it. Either way there is
    // nothing to report and nothing to retry: the user has not asked for
    // anything yet.
  }
}

/**
 * Was this click on our item?
 *
 * `chrome.contextMenus.onClicked` is delivered to every listener the extension
 * registered, for every item the extension owns. Anything that is not this id
 * is not this connector's business and must do nothing at all — no capture, no
 * badge, no request.
 */
export function isWholePageMenu(info) {
  return info?.menuItemId === WHOLE_PAGE_MENU_ID;
}
