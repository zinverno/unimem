// Shared native ES module: Gecko event page and Chromium service worker.
// No popup preserves left-click selection. Popups also support activeTab.
import { getExtension } from "./lib/browser.js";
import { readSelection, runCapture } from "./lib/capture.js";
import { readPageHtml, runWholePageCapture } from "./lib/page.js";
import { installMenus, isWholePageMenu, YOUTUBE_MENU_ID, OPEN_MENU_ID } from "./lib/menu.js";
import { sendCapture } from "./lib/api.js";
import { applyFeedback } from "./lib/action.js";
import { OUTCOME } from "./lib/outcomes.js";
import { settingsStore } from "./lib/settings.js";
import { localTransport } from "./lib/transport.js";
import { youtubeClient, validId, safeError } from "./lib/youtube.js";
import { jobsController } from "./lib/jobs.js";

const api = getExtension();
const settings = settingsStore(api.storage);
const http = localTransport({ getToken: () => settings.getToken(),
  hasPermission: () => api.permissions.contains({ origins: ["http://127.0.0.1/*"] }) });
const client = youtubeClient(http);
const jobs = jobsController({ local: api.storage.local, client, settings });
const open = (id = "") => api.tabs.create({ url: api.runtime.getURL(`manage.html${id ? `#${id}` : ""}`) });
const report = (tab, result) => applyFeedback(api.action, tab, result);
const failed = (tab) => report(tab, { outcome: OUTCOME.UNEXPECTED_ERROR });

async function originalCapture(tab, wholePage) {
  if (!await settings.getToken()) {
    report(tab, { outcome: OUTCOME.SERVER_ERROR, code: "unauthorized", status: 401 });
    await open();
    return;
  }
  await (wholePage ? runWholePageCapture : runCapture)(tab, {
    executeScript: async (tabId) => (await api.scripting.executeScript({
      target: { tabId }, func: wholePage ? readPageHtml : readSelection,
    }))?.[0]?.result,
    sendCapture: async (envelope) => {
      const result = await sendCapture(envelope, { fetch: http });
      if (result.status === 401) await open();
      return result;
    },
    report: (result) => report(tab, result),
  });
}

// Register synchronously on EVERY startup; no awaited initialization.
api.action.onClicked.addListener((tab) => {
  originalCapture({ ...tab }, false).catch(() => failed(tab));
});
let menusInstalling = Promise.resolve();
api.runtime.onInstalled.addListener(() => {
  menusInstalling = menusInstalling.then(() => installMenus(api)).catch(() => failed());
});
api.contextMenus.onClicked.addListener((info, tab) => {
  const source = { ...tab }; // Freeze the acted-on URL before open()/async work.
  let task;
  if (isWholePageMenu(info)) task = originalCapture(source, true);
  else if (info.menuItemId === OPEN_MENU_ID) task = open();
  else if (info.menuItemId === YOUTUBE_MENU_ID) {
    task = (async () => {
      const job = await jobs.choose(source.url);
      await open(job.operation_id);
      if (await settings.getToken()) await jobs.send(job.operation_id);
    })();
  }
  task?.catch((error) => { failed(source); open(`error-${safeError(error).code}`).catch(() => {}); });
});

export function trustedMessage(message, sender) {
  const page = new URL(sender?.url);
  page.hash = "";
  if (sender?.id !== api.runtime.id || page.href !== api.runtime.getURL("manage.html") || sender.frameId > 0) return false;
  if (!message || Object.getPrototypeOf(message) !== Object.prototype) return false;
  const noId = ["list", "check"];
  const withId = ["refresh", "retry", "again", "markdown", "remove", "send"];
  return (noId.includes(message.type) && Object.keys(message).length === 1) ||
    (withId.includes(message.type) && validId(message.id) && Object.keys(message).length === 2);
}
api.runtime.onMessage.addListener((message, sender, respond) => {
  let trusted = false;
  try { trusted = trustedMessage(message, sender); } catch { /* Invalid sender URL. */ }
  if (!trusted) { respond({ ok: false, error: { code: "invalid_message", status: null } }); return false; }
  Promise.resolve().then(() => {
    if (message.type === "check") return client.check();
    if (message.type === "list") return jobs.list();
    return jobs[message.type](message.id);
  }).then((value) => respond({ ok: true, value }), (error) => respond({ ok: false, error: safeError(error) }));
  return true; // Async sendResponse on both engines; never returns a credential.
});
