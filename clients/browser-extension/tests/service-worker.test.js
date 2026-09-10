/**
 * The real service worker, driven through the listeners Chrome would call.
 *
 * `service-worker.js` only touches `chrome` inside its listeners and at the
 * `addListener` calls, so stubbing two globals is enough to import the shipped
 * file and invoke the actual registered handlers. That makes the guarantee this
 * file is about a real one rather than a claim about a pattern: Chrome does not
 * await either listener, so **no failure anywhere in either flow may escape
 * it**, and every failure must still land as `!` on the tab that was acted on.
 *
 * Three listeners are registered, and this file drives all three:
 *
 *     chrome.action.onClicked        left click  -> selection capture
 *     chrome.runtime.onInstalled     install     -> create the menu item
 *     chrome.contextMenus.onClicked  right click -> whole-page capture
 */

import assert from "node:assert/strict";
import { after, before, beforeEach, describe, it } from "node:test";

import { BADGE_BUSY, BADGE_FAIL, BADGE_OK } from "../lib/feedback.js";
import { WHOLE_PAGE_MENU_ID } from "../lib/menu.js";

const TAB = Object.freeze({ id: 7, url: "https://example.com/a", title: "A page" });
const OTHER_TAB = Object.freeze({ id: 99, url: "https://example.com/b", title: "Another" });

/** What the stubs did, reset before each test. */
let calls;
/** What the stubs should do, set by each test. */
let scenario;
/** The listeners `service-worker.js` registered when it was imported. */
let listener;
let installedListener;
let menuListener;
/** Every `chrome.contextMenus.create` call, across the whole module's lifetime. */
let menusCreatedAtImport;
/** Rejections Node saw while a test ran. Must always be empty. */
let rejections;

const recordRejection = (reason) => rejections.push(reason);

before(async () => {
  globalThis.chrome = {
    action: {
      onClicked: {
        addListener: (fn) => {
          listener = fn;
        },
      },
      setBadgeText: (details) => {
        calls.badges.push(details);
        return scenario.action();
      },
      setTitle: (details) => {
        calls.titles.push(details);
        return scenario.action();
      },
    },
    scripting: {
      executeScript: async (args) => {
        calls.executeScript.push(args);
        return scenario.executeScript();
      },
    },
    runtime: {
      onInstalled: {
        addListener: (fn) => {
          installedListener = fn;
        },
      },
    },
    contextMenus: {
      onClicked: {
        addListener: (fn) => {
          menuListener = fn;
        },
      },
      create: (item) => {
        calls.menus.push(item);
        return item.id;
      },
    },
  };
  globalThis.fetch = async (url, options) => {
    calls.fetch.push({ url, options });
    return scenario.fetch();
  };

  // Menu calls are recorded from before the import, so that "importing the
  // module — which is what a woken service worker does — creates no menu item"
  // is observable rather than assumed.
  calls = { badges: [], titles: [], executeScript: [], fetch: [], menus: [] };

  // The shipped file, imported once, registering its real listeners.
  await import("../service-worker.js");
  menusCreatedAtImport = [...calls.menus];

  assert.equal(typeof listener, "function", "the service worker registered a click listener");
  assert.equal(
    typeof installedListener,
    "function",
    "the service worker registered an install listener",
  );
  assert.equal(
    typeof menuListener,
    "function",
    "the service worker registered a context-menu click listener",
  );
});

after(() => {
  delete globalThis.chrome;
  delete globalThis.fetch;
});

beforeEach(() => {
  calls = { badges: [], titles: [], executeScript: [], fetch: [], menus: [] };
  scenario = {
    action: () => Promise.resolve(),
    executeScript: () => [{ result: "the selected words" }],
    fetch: () => created(),
  };
  rejections = [];
  process.on("unhandledRejection", recordRejection);
});

function created() {
  return {
    status: 201,
    json: async () => ({
      capture_id: JSON.parse(calls.fetch[0].options.body).id,
      content_id: "content-1",
      status: "complete",
    }),
  };
}

/**
 * Invoke the listener exactly as Chrome does — without awaiting it — then let
 * everything settle and assert that nothing escaped.
 *
 * The tab is always passed explicitly: a default parameter would also fire for
 * an explicit `undefined`, and "Chrome gave us no tab" is one of the cases
 * under test.
 */
async function click(tab) {
  await drive(() => listener(tab), "the click path");
}

/**
 * Activate the whole-page menu item exactly as Chrome does.
 *
 * `info` defaults to our own item, so a test that cares about a different menu
 * id says so explicitly rather than by omission.
 */
async function chooseMenuItem(tab, info = { menuItemId: WHOLE_PAGE_MENU_ID }) {
  await drive(() => menuListener(info, tab), "the context-menu path");
}

async function drive(invoke, what) {
  const returned = invoke();
  assert.equal(returned, undefined, "Chrome ignores the listener's return value");
  for (let tick = 0; tick < 10; tick += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  process.off("unhandledRejection", recordRejection);
  assert.deepEqual(rejections.map(String), [], `${what} must never leave an unhandled rejection`);
}

const lastBadge = () => calls.badges.at(-1);
const lastTitle = () => calls.titles.at(-1);

describe("a successful click", () => {
  it("ends on OK, scoped to the clicked tab", async () => {
    await click(TAB);

    assert.deepEqual(lastBadge(), { text: BADGE_OK, tabId: 7 });
    assert.deepEqual(lastTitle(), { title: "UniMem: saved", tabId: 7 });
  });

  it("shows busy first, also scoped to the clicked tab", async () => {
    await click(TAB);

    assert.deepEqual(calls.badges[0], { text: BADGE_BUSY, tabId: 7 });
  });

  it("scopes every single action call to the clicked tab", async () => {
    await click(OTHER_TAB);

    for (const details of [...calls.badges, ...calls.titles]) {
      assert.equal(details.tabId, 99);
    }
  });

  it("injects into the clicked tab and posts to the fixed endpoint", async () => {
    await click(TAB);

    assert.deepEqual(calls.executeScript[0].target, { tabId: 7 });
    assert.equal(calls.fetch.length, 1);
    assert.equal(calls.fetch[0].url, "http://127.0.0.1:8765/v1/captures");
    assert.equal(calls.fetch[0].options.method, "POST");
  });
});

describe("every failure still ends in safe feedback", () => {
  const failures = [
    [
      "the page refuses injection",
      () => {
        scenario.executeScript = () => {
          throw new Error("Cannot access contents of url \"chrome://extensions\".");
        };
      },
      "UniMem: cannot read the selection on this page",
    ],
    [
      "nothing is selected",
      () => {
        scenario.executeScript = () => [{ result: "   " }];
      },
      "UniMem: select some text first",
    ],
    [
      "the API is not running",
      () => {
        scenario.fetch = () => {
          throw new TypeError("Failed to fetch");
        };
      },
      "UniMem: service unavailable, capture outcome unknown",
    ],
    [
      "the API answers with a conflict",
      () => {
        scenario.fetch = () => ({
          status: 409,
          json: async () => ({ error: { code: "capture_already_exists", message: "taken" } }),
        });
      },
      "UniMem: that capture already exists",
    ],
    [
      "the API answers with an unreadable body",
      () => {
        scenario.fetch = () => ({
          status: 201,
          json: async () => {
            throw new SyntaxError("Unexpected token <");
          },
        });
      },
      "UniMem: unexpected response from the API",
    ],
    [
      "the flow throws something no outcome describes",
      () => {
        scenario.fetch = () => {
          // Not a network error and not a Response — a bug, surfacing as a
          // rejection from deep inside the client.
          return { get status() {
            throw new RangeError("something no outcome describes");
          } };
        };
      },
      "UniMem: capture failed",
    ],
  ];

  for (const [label, arrange, expectedTitle] of failures) {
    it(`shows ! and a readable title when ${label}`, async () => {
      arrange();

      await click(TAB);

      assert.equal(lastBadge().text, BADGE_FAIL);
      assert.equal(lastTitle().title, expectedTitle);
    });

    it(`keeps that feedback on the clicked tab when ${label}`, async () => {
      arrange();

      await click(TAB);

      assert.equal(lastBadge().tabId, 7);
      assert.equal(lastTitle().tabId, 7);
    });

    it(`leaks no selected text or stack when ${label}`, async () => {
      arrange();

      await click(TAB);

      const shown = JSON.stringify([...calls.badges, ...calls.titles]);
      assert.ok(!shown.includes("the selected words"));
      // A stack frame, rather than the bare word "at" — "that capture" is a
      // perfectly good thing for a title to say.
      assert.ok(!/\bat\s+\S+:\d+/.test(shown), shown);
      assert.ok(!shown.includes("\\n    at "));
      for (const noise of ["Error", "TypeError", "RangeError", "SyntaxError", "chrome://"]) {
        assert.ok(!shown.includes(noise), `${noise} reached the UI`);
      }
    });
  }

  it("never leaves the badge stuck on busy", async () => {
    scenario.fetch = () => {
      throw new RangeError("boom");
    };

    await click(TAB);

    assert.notEqual(lastBadge().text, BADGE_BUSY);
  });

  it("survives a tab that closed while the capture was in flight", async () => {
    scenario.action = () => Promise.reject(new Error("No tab with id: 7."));

    await click(TAB);

    // The assertion that matters is inside `click`: no unhandled rejection.
    assert.ok(calls.badges.length >= 1);
  });

  it("survives an action API that throws synchronously", async () => {
    scenario.action = () => {
      throw new Error("No tab with id: 7.");
    };

    await click(TAB);

    assert.ok(calls.badges.length >= 1);
  });
});

describe("a click on a page that cannot be captured", () => {
  it("sends nothing and injects nothing", async () => {
    await click({ id: 3, url: "chrome://extensions", title: "Extensions" });

    assert.deepEqual(calls.executeScript, []);
    assert.deepEqual(calls.fetch, []);
    assert.equal(lastTitle().title, "UniMem: cannot capture from this page");
    assert.equal(lastBadge().tabId, 3);
  });

  it("falls back to global feedback only when there is no tab at all", async () => {
    await click(undefined);

    assert.equal(lastBadge().text, BADGE_FAIL);
    assert.ok(!("tabId" in lastBadge()));
    assert.deepEqual(calls.fetch, []);
  });
});

describe("registering the whole-page menu item", () => {
  it("creates nothing merely by starting the service worker", () => {
    // A woken worker re-runs this module. If registration happened at import,
    // every wake would try to create the item again and the user would end up
    // with duplicates or a silent failure.
    assert.deepEqual(menusCreatedAtImport, []);
  });

  it("creates exactly one item when the extension is installed", async () => {
    installedListener();

    assert.equal(calls.menus.length, 1);
  });

  it("creates it with the stable id, exact title, and action context", async () => {
    installedListener();

    assert.deepEqual(calls.menus[0], {
      id: "unimem-save-whole-page",
      title: "Save whole page to UniMem",
      contexts: ["action"],
    });
  });

  it("survives Chrome refusing the item, without an unhandled rejection", async () => {
    globalThis.chrome.contextMenus.create = () => {
      throw new Error("Cannot create item with duplicate id unimem-save-whole-page");
    };

    try {
      assert.doesNotThrow(() => installedListener());
    } finally {
      globalThis.chrome.contextMenus.create = (item) => {
        calls.menus.push(item);
        return item.id;
      };
    }
  });
});

describe("choosing 'Save whole page to UniMem'", () => {
  beforeEach(() => {
    scenario.executeScript = () => [{ result: "<html><body>the page body</body></html>" }];
  });

  it("ends on OK, scoped to the tab the menu was opened over", async () => {
    await chooseMenuItem(TAB);

    assert.deepEqual(lastBadge(), { text: BADGE_OK, tabId: 7 });
    assert.deepEqual(lastTitle(), { title: "UniMem: saved", tabId: 7 });
  });

  it("shows busy first, and says it is saving a page", async () => {
    await chooseMenuItem(TAB);

    assert.deepEqual(calls.badges[0], { text: BADGE_BUSY, tabId: 7 });
    assert.deepEqual(calls.titles[0], { title: "UniMem: saving page...", tabId: 7 });
  });

  it("scopes every single action call to that tab", async () => {
    await chooseMenuItem(OTHER_TAB);

    for (const details of [...calls.badges, ...calls.titles]) {
      assert.equal(details.tabId, 99);
    }
  });

  it("injects the page reader into that tab and posts to the fixed endpoint", async () => {
    await chooseMenuItem(TAB);

    assert.deepEqual(calls.executeScript[0].target, { tabId: 7 });
    assert.equal(calls.executeScript[0].func.name, "readPageHtml");
    assert.equal(calls.executeScript[0].allFrames, undefined);
    assert.equal(calls.fetch.length, 1);
    assert.equal(calls.fetch[0].url, "http://127.0.0.1:8765/v1/captures");
    assert.equal(calls.fetch[0].options.method, "POST");
  });

  it("posts a schema-0.2 WEBPAGE envelope carrying the exact snapshot", async () => {
    await chooseMenuItem(TAB);

    const submitted = JSON.parse(calls.fetch[0].options.body);
    assert.equal(submitted.schema_version, "0.2");
    assert.equal(submitted.source.type, "browser");
    assert.equal(submitted.source.url, "https://example.com/a");
    assert.equal(submitted.payload.type, "webpage");
    assert.equal(submitted.payload.mime_type, "text/html");
    assert.equal(submitted.payload.html, "<html><body>the page body</body></html>");
    assert.equal(submitted.payload.title, "A page");
    assert.equal(submitted.payload.text, undefined);
    assert.equal(submitted.payload.file_ref, undefined);
    assert.equal(submitted.intent.action, "save");
  });

  it("does not read the selection on the way", async () => {
    await chooseMenuItem(TAB);

    assert.equal(calls.executeScript.length, 1);
    assert.notEqual(calls.executeScript[0].func.name, "readSelection");
  });

  const failures = [
    [
      "the page refuses injection",
      () => {
        scenario.executeScript = () => {
          throw new Error("Cannot access contents of url \"chrome://extensions\".");
        };
      },
      "UniMem: could not read this page",
    ],
    [
      "the page returns nothing",
      () => {
        scenario.executeScript = () => [{}];
      },
      "UniMem: could not read this page",
    ],
    [
      "the page returns only whitespace",
      () => {
        scenario.executeScript = () => [{ result: "  \r\n " }];
      },
      "UniMem: could not read this page",
    ],
    [
      "the API is not running",
      () => {
        scenario.fetch = () => {
          throw new TypeError("Failed to fetch");
        };
      },
      "UniMem: service unavailable, capture outcome unknown",
    ],
    [
      "the flow throws something no outcome describes",
      () => {
        scenario.fetch = () => ({
          get status() {
            throw new RangeError("something no outcome describes");
          },
        });
      },
      "UniMem: capture failed",
    ],
  ];

  for (const [label, arrange, expectedTitle] of failures) {
    it(`shows ! and a readable title when ${label}`, async () => {
      arrange();

      await chooseMenuItem(TAB);

      assert.equal(lastBadge().text, BADGE_FAIL);
      assert.equal(lastTitle().title, expectedTitle);
      assert.equal(lastBadge().tabId, 7);
    });

    it(`leaks no page HTML or stack when ${label}`, async () => {
      arrange();

      await chooseMenuItem(TAB);

      const shown = JSON.stringify([...calls.badges, ...calls.titles]);
      assert.ok(!shown.includes("the page body"));
      assert.ok(!shown.includes("<html"));
      assert.ok(!/\bat\s+\S+:\d+/.test(shown), shown);
      for (const noise of ["Error", "TypeError", "RangeError", "SyntaxError", "chrome://"]) {
        assert.ok(!shown.includes(noise), `${noise} reached the UI`);
      }
    });
  }

  it("sends nothing at all from a page it cannot capture", async () => {
    await chooseMenuItem({ id: 3, url: "chrome://extensions", title: "Extensions" });

    assert.deepEqual(calls.executeScript, []);
    assert.deepEqual(calls.fetch, []);
    assert.equal(lastTitle().title, "UniMem: cannot capture from this page");
    assert.equal(lastBadge().tabId, 3);
  });

  it("never leaves the badge stuck on busy", async () => {
    scenario.fetch = () => {
      throw new RangeError("boom");
    };

    await chooseMenuItem(TAB);

    assert.notEqual(lastBadge().text, BADGE_BUSY);
  });

  it("survives a tab that closed while the page capture was in flight", async () => {
    scenario.action = () => Promise.reject(new Error("No tab with id: 7."));

    await chooseMenuItem(TAB);

    assert.ok(calls.badges.length >= 1);
  });
});

describe("a context-menu click that is not ours", () => {
  for (const menuItemId of [
    "some-other-extension-item",
    "unimem-save-whole-page-2",
    "",
    12,
    undefined,
  ]) {
    it(`does nothing at all for ${JSON.stringify(menuItemId)}`, async () => {
      await chooseMenuItem(TAB, { menuItemId });

      assert.deepEqual(calls.badges, []);
      assert.deepEqual(calls.titles, []);
      assert.deepEqual(calls.executeScript, []);
      assert.deepEqual(calls.fetch, []);
    });
  }
});
