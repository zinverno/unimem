// All used APIs support promises on our minimum versions, except menus.create.
export const getExtension = () => globalThis.browser ?? globalThis.chrome;

export function createMenu(api, item) {
  return new Promise((resolve, reject) => {
    try {
      api.contextMenus.create(item, () => {
        // Read lastError in the callback on BOTH engines; never log its message.
        const error = api.runtime.lastError;
        if (error) reject(new Error("menu_unavailable"));
        else resolve();
      });
    } catch { reject(new Error("menu_unavailable")); }
  });
}
