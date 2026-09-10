/**
 * The real service worker, driven through the listener Chrome would call.
 *
 * `service-worker.js` only touches `chrome` inside the listener and at the one
 * `addListener` call, so stubbing two globals is enough to import the shipped
 * file and invoke the actual registered handler. That makes the guarantee this
 * file is about a real one rather than a claim about a pattern: Chrome does not
 * await the listener, so **no failure anywhere in the flow may escape it**, and
 * every failure must still land as `!` on the tab that was clicked.
 */

import assert from "node:assert/strict";
import { after, before, beforeEach, describe, it } from "node:test";

import { BADGE_BUSY, BADGE_FAIL, BADGE_OK } from "../lib/feedback.js";

const TAB = Object.freeze({ id: 7, url: "https://example.com/a", title: "A page" });
const OTHER_TAB = Object.freeze({ id: 99, url: "https://example.com/b", title: "Another" });

/** What the stubs did, reset before each test. */
let calls;
/** What the stubs should do, set by each test. */
let scenario;
/** The listener `service-worker.js` registered when it was imported. */
let listener;
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
  };
  globalThis.fetch = async (url, options) => {
    calls.fetch.push({ url, options });
    return scenario.fetch();
  };

  // The shipped file, imported once, registering its real listener.
  await import("../service-worker.js");
  assert.equal(typeof listener, "function", "the service worker registered a click listener");
});

after(() => {
  delete globalThis.chrome;
  delete globalThis.fetch;
});

beforeEach(() => {
  calls = { badges: [], titles: [], executeScript: [], fetch: [] };
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
  const returned = listener(tab);
  assert.equal(returned, undefined, "Chrome ignores the listener's return value");
  for (let tick = 0; tick < 10; tick += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  process.off("unhandledRejection", recordRejection);
  assert.deepEqual(
    rejections.map(String),
    [],
    "the click path must never leave an unhandled rejection",
  );
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
