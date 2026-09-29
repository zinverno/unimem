import { createMenu } from "./browser.js";
export const WHOLE_PAGE_MENU_ID = "unimem-save-whole-page";
export const WHOLE_PAGE_MENU_TITLE = "Сохранить всю страницу в UniMem";
export const WHOLE_PAGE_MENU_CONTEXTS = Object.freeze(["action"]);
export const YOUTUBE_MENU_ID = "unimem-youtube";
export const OPEN_MENU_ID = "unimem-open";
export const isWholePageMenu = (info) => info?.menuItemId === WHOLE_PAGE_MENU_ID;
export async function createWholePageMenu(contextMenus, runtime = {}) {
  await createMenu({ contextMenus, runtime }, { id: WHOLE_PAGE_MENU_ID, title: WHOLE_PAGE_MENU_TITLE, contexts: [...WHOLE_PAGE_MENU_CONTEXTS] });
}
export async function installMenus(api) {
  // Only onInstalled: stable IDs survive event-page/worker eviction.
  await api.contextMenus.removeAll();
  await createWholePageMenu(api.contextMenus, api.runtime);
  await createMenu(api, { id: YOUTUBE_MENU_ID, title: "YouTube → Markdown", contexts: ["action"] });
  await createMenu(api, { id: OPEN_MENU_ID, title: "Открыть UniMem", contexts: ["action"] });
}
