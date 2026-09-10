/**
 * The click-to-capture flow, and the boundary it must not cross.
 *
 * These run the real orchestration with small fakes for the two browser
 * capabilities it uses. The security-shaped assertions are the reason the flow
 * lives in `lib/` at all: that the request destination never comes from the
 * page, that nothing is sent before the page is checked, and that the function
 * injected into the page knows nothing about UniMem.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { isCapturablePage, readSelection, runCapture } from "../lib/capture.js";
import { OUTCOME } from "../lib/outcomes.js";
import { CAPTURES_ENDPOINT } from "../lib/api.js";

const TAB = Object.freeze({ id: 7, url: "https://example.com/a?b=c#d", title: "A page" });

/**
 * Read a module as code, with comments removed.
 *
 * These files document what the connector deliberately does *not* do — "no
 * `chrome.storage`", "no retry" — so a grep over the raw text would match the
 * prose that promises the absence. Stripping comments first means the assertion
 * is about the code.
 */
function codeOf(relativePath) {
  return readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trimStart().startsWith("//"))
    .join("\n");
}

/**
 * Records the whole interaction so order and absence are both assertable.
 *
 * `selection` is read off the options object rather than destructured with a
 * default, because a destructuring default also fires for an explicit
 * `undefined` — and "the page returned no selection object at all" is one of
 * the cases under test.
 */
function harness(options = {}) {
  const { result = { outcome: OUTCOME.COMPLETE }, injectionError } = options;
  const selected = "selection" in options ? options.selection : "selected words";
  const journal = [];
  const envelopes = [];
  const reports = [];

  const deps = {
    executeScript: async (tabId) => {
      journal.push(`executeScript(${tabId})`);
      if (injectionError) {
        throw injectionError;
      }
      return selected;
    },
    sendCapture: async (envelope) => {
      journal.push("sendCapture");
      envelopes.push(envelope);
      return result;
    },
    report: (value) => {
      journal.push(value?.busy === true ? "report(busy)" : `report(${value.outcome})`);
      reports.push(value);
    },
    newId: () => "fixed-test-id",
    now: () => "2026-01-02T03:04:05.678Z",
  };

  return { deps, journal, envelopes, reports };
}

describe("a click on an ordinary page", () => {
  it("extracts the selection and then submits it", async () => {
    const { deps, journal } = harness();

    await runCapture(TAB, deps);

    assert.deepEqual(journal, [
      "report(busy)",
      "executeScript(7)",
      "sendCapture",
      "report(complete)",
    ]);
  });

  it("reports busy before doing any work", async () => {
    const { deps, journal } = harness();

    await runCapture(TAB, deps);

    assert.equal(journal[0], "report(busy)");
  });

  it("builds the envelope from the tab and the selection", async () => {
    const { deps, envelopes } = harness({ selection: "  kept exactly  " });

    await runCapture(TAB, deps);

    assert.equal(envelopes.length, 1);
    assert.equal(envelopes[0].id, "fixed-test-id");
    assert.equal(envelopes[0].payload.text, "  kept exactly  ");
    assert.equal(envelopes[0].source.url, TAB.url);
    assert.equal(envelopes[0].payload.title, TAB.title);
    assert.equal(envelopes[0].context.captured_at, "2026-01-02T03:04:05.678Z");
  });

  it("returns the outcome it reported", async () => {
    const { deps, reports } = harness();

    const result = await runCapture(TAB, deps);

    assert.equal(result.outcome, OUTCOME.COMPLETE);
    assert.equal(reports.at(-1), result);
  });
});

describe("nothing selected", () => {
  for (const [label, selection] of [
    ["no selection object", undefined],
    ["an empty selection", ""],
    ["only spaces", "   "],
    ["only a newline", "\n"],
    ["mixed whitespace", " \t\r\n "],
  ]) {
    it(`stops before the API call when there is ${label}`, async () => {
      const { deps, journal } = harness({ selection });

      const result = await runCapture(TAB, deps);

      assert.equal(result.outcome, OUTCOME.BLANK_SELECTION);
      assert.ok(!journal.includes("sendCapture"));
    });
  }

  it("mints no capture id for a blank click", async () => {
    let minted = 0;
    const { deps } = harness({ selection: "   " });
    deps.newId = () => {
      minted += 1;
      return "should-not-happen";
    };

    await runCapture(TAB, deps);

    assert.equal(minted, 0);
  });
});

describe("pages a selection cannot be captured from", () => {
  const unsupported = [
    "chrome://extensions",
    "chrome://settings/privacy",
    "edge://settings",
    "about:blank",
    "chrome-extension://abcdefghijklmnop/options.html",
    "file:///home/user/notes.txt",
    "devtools://devtools/bundled/inspector.html",
    "view-source:https://example.com",
    "data:text/html,<p>hi</p>",
    "javascript:alert(1)",
  ];

  for (const url of unsupported) {
    it(`refuses ${url} before injecting or calling the API`, async () => {
      const { deps, journal } = harness();

      const result = await runCapture({ ...TAB, url }, deps);

      assert.equal(result.outcome, OUTCOME.UNSUPPORTED_PAGE);
      assert.deepEqual(journal, ["report(busy)", "report(unsupported_page)"]);
    });

    it(`classifies ${url} as not capturable`, () => {
      assert.equal(isCapturablePage(url), false);
    });
  }

  for (const [label, tab] of [
    ["a missing tab", undefined],
    ["a tab with no URL", { id: 1 }],
    ["a tab with a blank URL", { id: 1, url: "" }],
    ["a tab with a malformed URL", { id: 1, url: "not a url" }],
  ]) {
    it(`refuses ${label}`, async () => {
      const { deps, journal } = harness();

      const result = await runCapture(tab, deps);

      assert.equal(result.outcome, OUTCOME.UNSUPPORTED_PAGE);
      assert.ok(!journal.includes("executeScript(undefined)"));
      assert.ok(!journal.includes("sendCapture"));
    });
  }

  for (const url of ["http://example.com/", "https://example.com/a", "http://localhost:3000/x"]) {
    it(`accepts ${url}`, () => {
      assert.equal(isCapturablePage(url), true);
    });
  }
});

describe("a page that refuses injection", () => {
  it("fails locally without calling the API", async () => {
    const { deps, journal } = harness({ injectionError: new Error("Cannot access contents of url") });

    const result = await runCapture(TAB, deps);

    assert.equal(result.outcome, OUTCOME.INJECTION_FAILED);
    assert.ok(!journal.includes("sendCapture"));
  });

  it("does not surface the browser's exception", async () => {
    const { deps } = harness({ injectionError: new Error("Cannot access chrome:// URL") });

    const result = await runCapture(TAB, deps);

    assert.equal(JSON.stringify(result).includes("chrome://"), false);
  });
});

describe("the page never chooses the destination", () => {
  it("the API endpoint is a module constant, not a tab field", async () => {
    const { deps, envelopes } = harness();
    const hostile = {
      id: 7,
      url: "https://evil.example/?api=http://attacker.invalid",
      title: "http://attacker.invalid",
    };

    await runCapture(hostile, deps);

    // The page's URL and title travelled as metadata, and nowhere else.
    assert.equal(envelopes[0].source.url, hostile.url);
    assert.equal(envelopes[0].payload.title, hostile.title);
    assert.equal(CAPTURES_ENDPOINT, "http://127.0.0.1:8765/v1/captures");
  });

  it("the selected text is payload data only", async () => {
    const { deps, envelopes } = harness({ selection: "http://attacker.invalid/steal" });

    await runCapture(TAB, deps);

    assert.equal(envelopes[0].payload.text, "http://attacker.invalid/steal");
    assert.equal(envelopes[0].source.url, TAB.url);
  });
});

describe("the injected function", () => {
  const source = readSelection.toString();

  it("reads the selection and returns it", () => {
    assert.match(source, /getSelection\(\)\?\.toString\(\)/);
  });

  it("performs no network request", () => {
    assert.ok(!source.includes("fetch"));
    assert.ok(!source.includes("XMLHttpRequest"));
  });

  it("knows nothing about the API", () => {
    assert.ok(!source.includes("127.0.0.1"));
    assert.ok(!source.includes("UniMem"));
    assert.ok(!source.includes("captures"));
  });

  it("closes over nothing, so it survives serialization into the page", () => {
    assert.ok(!source.includes("CAPTURES_ENDPOINT"));
    assert.ok(!source.includes("OUTCOME"));
    // Reconstructing it from its own source must still be valid code.
    assert.doesNotThrow(() => new Function(`return ${source}`));
  });

  it("does not ask for all frames, so it reads the top-level document", () => {
    assert.ok(!source.includes("allFrames"));
  });
});

describe("the service worker's own source", () => {
  const workerSource = codeOf("../service-worker.js");

  it("is where the fetch happens", () => {
    assert.match(workerSource, /sendCapture\(envelope, \{ fetch \}\)/);
  });

  it("injects only the selection reader", () => {
    assert.match(workerSource, /func: readSelection/);
  });

  it("does not inject all frames", () => {
    assert.ok(!workerSource.includes("allFrames"));
  });

  it("writes no persistent state", () => {
    assert.ok(!workerSource.includes("chrome.storage"));
    assert.ok(!workerSource.includes("localStorage"));
    assert.ok(!workerSource.includes("indexedDB"));
  });

  it("registers no context menu, alarm, or notification", () => {
    for (const api of ["contextMenus", "alarms", "notifications", "tabs.query", "webRequest"]) {
      assert.ok(!workerSource.includes(api), api);
    }
  });

  it("logs nothing", () => {
    assert.ok(!workerSource.includes("console."));
  });

  it("contains no retry or backoff path", () => {
    for (const word of ["retry", "backoff", "setTimeout", "setInterval", "attempts"]) {
      assert.ok(!workerSource.toLowerCase().includes(word), word);
    }
  });
});

describe("the connector keeps no persistent state", () => {
  it("no library module touches storage", () => {
    for (const name of ["api", "capture", "envelope", "feedback", "outcomes"]) {
      const source = codeOf(`../lib/${name}.js`);
      assert.ok(!source.includes("chrome.storage"), name);
      assert.ok(!source.includes("localStorage"), name);
      assert.ok(!source.includes("indexedDB"), name);
    }
  });

  it("no library module schedules an automatic retry", () => {
    for (const name of ["api", "capture"]) {
      const source = codeOf(`../lib/${name}.js`);
      assert.ok(!source.includes("setTimeout"), name);
      assert.ok(!source.includes("setInterval"), name);
    }
  });
});
