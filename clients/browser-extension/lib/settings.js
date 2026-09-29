export const TOKEN_KEY = "credential";
export const SETTINGS_KEY = "settings";

export function languagesFrom(value) {
  const list = typeof value === "string" ? value.split(",").map((s) => s.trim()) : value;
  if (!Array.isArray(list) || list.length < 1 || list.length > 10 ||
      list.some((s) => typeof s !== "string" || !/^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*$/.test(s))) {
    throw new Error("invalid_languages");
  }
  return [...list]; // Order, case and repetitions have B1's exact semantics.
}

export function settingsStore(storage) {
  // Chromium supports this restriction. Gecko's API differs; session storage
  // is trusted-only by default. No static/content messaging surface is exposed.
  const ready = Promise.all([storage.local, storage.session].map(async (area) => {
    if (area?.setAccessLevel) await area.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
  }));
  ready.catch(() => {}); // Consumers still fail closed when awaiting readiness.
  return {
    async getToken() {
      await ready;
      const session = await storage.session.get(TOKEN_KEY);
      const local = await storage.local.get(TOKEN_KEY);
      const token = session[TOKEN_KEY] ?? local[TOKEN_KEY];
      return typeof token === "string" && /^[A-Za-z0-9_-]{43}$/.test(token) ? token : null;
    },
    async read() {
      await ready;
      const local = await storage.local.get([SETTINGS_KEY, TOKEN_KEY]);
      return { languages: languagesFrom(local[SETTINGS_KEY]?.languages ?? ["ru", "en"]),
        hasToken: Boolean(await this.getToken()), remembered: Boolean(local[TOKEN_KEY]) };
    },
    async save(token, remember, languages) {
      await ready;
      const ordered = languagesFrom(languages);
      if (typeof token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error("invalid_token");
      if (typeof remember !== "boolean") throw new Error("invalid_settings");
      // Remove the old credential first: a failed replacement cannot fall back
      // to a stale persistent secret on the next browser session.
      await this.removeToken();
      await storage.local.set({ [SETTINGS_KEY]: { languages: ordered } });
      await (remember ? storage.local : storage.session).set({ [TOKEN_KEY]: token });
    },
    async saveLanguages(languages) {
      await ready;
      await storage.local.set({ [SETTINGS_KEY]: { languages: languagesFrom(languages) } });
    },
    async removeToken() {
      await ready;
      await storage.local.remove(TOKEN_KEY);
      await storage.session.remove(TOKEN_KEY);
    },
  };
}
