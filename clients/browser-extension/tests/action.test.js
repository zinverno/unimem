/**
 * Applying feedback: to the right tab, and without ever escaping.
 *
 * Both properties are invisible in the happy path and only bite in the two
 * situations nobody clicks through by hand — a second tab open alongside, and a
 * tab closed while a capture was in flight. So they are asserted here rather
 * than reviewed.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { applyFeedback, scopeForTab } from "../lib/action.js";
import { BADGE_BUSY, BADGE_FAIL, BADGE_OK } from "../lib/feedback.js";
import { OUTCOME } from "../lib/outcomes.js";

/** A `chrome.action` stand-in that records every call. */
function fakeAction({ throwSync = false, rejectAsync = false } = {}) {
  const badges = [];
  const titles = [];
  const fail = () => {
    if (throwSync) {
      throw new Error("No tab with id: 7.");
    }
    return rejectAsync ? Promise.reject(new Error("No tab with id: 7.")) : Promise.resolve();
  };
  return {
    badges,
    titles,
    setBadgeText: (details) => {
      badges.push(details);
      return fail();
    },
    setTitle: (details) => {
      titles.push(details);
      return fail();
    },
  };
}

const TAB = Object.freeze({ id: 7, url: "https://example.com/a", title: "A page" });

describe("feedback is scoped to the clicked tab", () => {
  it("scopes the badge to the tab id", () => {
    const action = fakeAction();

    applyFeedback(action, TAB, { outcome: OUTCOME.COMPLETE });

    assert.deepEqual(action.badges, [{ text: BADGE_OK, tabId: 7 }]);
  });

  it("scopes the title to the tab id", () => {
    const action = fakeAction();

    applyFeedback(action, TAB, { outcome: OUTCOME.COMPLETE });

    assert.deepEqual(action.titles, [{ title: "UniMem: saved", tabId: 7 }]);
  });

  it("scopes the busy report too, so `...` does not appear on other tabs", () => {
    const action = fakeAction();

    applyFeedback(action, TAB, { busy: true });

    assert.deepEqual(action.badges, [{ text: BADGE_BUSY, tabId: 7 }]);
    assert.equal(action.titles[0].tabId, 7);
  });

  it("never sets a global default when a tab id is available", () => {
    const action = fakeAction();

    for (const result of [
      { busy: true },
      { outcome: OUTCOME.COMPLETE },
      { outcome: OUTCOME.BLANK_SELECTION },
      { outcome: OUTCOME.SERVER_ERROR, status: 409, code: "capture_already_exists" },
      { outcome: OUTCOME.UNEXPECTED_ERROR },
    ]) {
      applyFeedback(action, TAB, result);
    }

    for (const details of [...action.badges, ...action.titles]) {
      assert.equal(details.tabId, 7);
    }
  });

  it("keeps one tab's result off another tab", () => {
    const action = fakeAction();

    applyFeedback(action, { id: 1 }, { outcome: OUTCOME.COMPLETE });
    applyFeedback(action, { id: 2 }, { outcome: OUTCOME.BLANK_SELECTION });

    assert.deepEqual(action.badges, [
      { text: BADGE_OK, tabId: 1 },
      { text: BADGE_FAIL, tabId: 2 },
    ]);
  });

  describe("when there is no tab to scope to", () => {
    for (const [label, tab] of [
      ["no tab at all", undefined],
      ["a tab with no id", { url: "https://example.com/" }],
      ["a tab whose id is not a number", { id: "7" }],
    ]) {
      it(`falls back to the global default given ${label}`, () => {
        const action = fakeAction();

        applyFeedback(action, tab, { outcome: OUTCOME.UNSUPPORTED_PAGE });

        assert.deepEqual(action.badges, [{ text: BADGE_FAIL }]);
        assert.ok(!("tabId" in action.badges[0]));
      });
    }

    it("is the only case that produces global feedback", () => {
      assert.deepEqual(scopeForTab(TAB), { tabId: 7 });
      assert.deepEqual(scopeForTab(undefined), {});
      assert.deepEqual(scopeForTab({}), {});
      assert.deepEqual(scopeForTab({ id: 0 }), { tabId: 0 });
    });
  });
});

describe("applying feedback is terminal", () => {
  it("returns nothing", () => {
    assert.equal(applyFeedback(fakeAction(), TAB, { outcome: OUTCOME.COMPLETE }), undefined);
  });

  it("absorbs a chrome API that throws synchronously", () => {
    const action = fakeAction({ throwSync: true });

    assert.doesNotThrow(() => applyFeedback(action, TAB, { outcome: OUTCOME.COMPLETE }));
    // Both calls were still attempted: one failing does not skip the other.
    assert.equal(action.badges.length, 1);
    assert.equal(action.titles.length, 1);
  });

  it("absorbs a chrome API that rejects because the tab closed", async () => {
    const action = fakeAction({ rejectAsync: true });
    const rejections = [];
    const record = (reason) => rejections.push(reason);
    process.on("unhandledRejection", record);

    applyFeedback(action, TAB, { outcome: OUTCOME.COMPLETE });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));

    process.off("unhandledRejection", record);
    assert.deepEqual(rejections, []);
  });
});

describe("an unexpected failure still reaches the user", () => {
  it("shows the failure badge", () => {
    const action = fakeAction();

    applyFeedback(action, TAB, { outcome: OUTCOME.UNEXPECTED_ERROR });

    assert.equal(action.badges[0].text, BADGE_FAIL);
  });

  it("shows a human-readable title", () => {
    const action = fakeAction();

    applyFeedback(action, TAB, { outcome: OUTCOME.UNEXPECTED_ERROR });

    assert.equal(action.titles[0].title, "UniMem: capture failed");
  });

  it("says nothing about what threw", () => {
    const action = fakeAction();
    const error = new TypeError("Cannot read properties of undefined (reading 'result')");

    applyFeedback(action, TAB, { outcome: OUTCOME.UNEXPECTED_ERROR, error, stack: error.stack });

    const title = action.titles[0].title;
    assert.ok(!title.includes("TypeError"));
    assert.ok(!title.includes("Cannot read"));
    assert.ok(!title.includes("at "));
  });
});
