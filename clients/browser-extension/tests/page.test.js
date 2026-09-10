/**
 * The whole-page capture flow, and the boundary it must not cross.
 *
 * The sibling of `capture.test.js`, and it asserts the same security shape for
 * the same reasons: that the request destination never comes from the page,
 * that nothing is sent before the page is checked, and that the function
 * injected into the page knows nothing about UniMem.
 *
 * It also asserts something the selection flow never had to: **what the
 * snapshot is not.** `document.documentElement.outerHTML` is a serialization of
 * the live DOM, so the tests below pin that the connector does not go looking
 * for iframes, shadow roots, stylesheets, or images, and does not invent a
 * doctype to make the result look like a file it never received.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { readPageHtml, runWholePageCapture } from "../lib/page.js";
import { readSelection } from "../lib/capture.js";
import { OUTCOME } from "../lib/outcomes.js";
import { CAPTURES_ENDPOINT } from "../lib/api.js";

const TAB = Object.freeze({ id: 7, url: "https://example.com/a?b=c#d", title: "A page" });

const PAGE_HTML = '<html lang="en"><head><title>A page</title></head><body><p>Hi</p></body></html>';

/** Read a module as code, with comments removed. See `capture.test.js`. */
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
 * `html` is read off the options object rather than destructured with a
 * default, because a destructuring default also fires for an explicit
 * `undefined` — and "the injected function returned nothing at all" is one of
 * the cases under test.
 */
function harness(options = {}) {
  const { result = { outcome: OUTCOME.COMPLETE }, injectionError } = options;
  const html = "html" in options ? options.html : PAGE_HTML;
  const journal = [];
  const envelopes = [];
  const reports = [];
  let minted = 0;
  let clockReads = 0;

  const deps = {
    executeScript: async (tabId) => {
      journal.push(`executeScript(${tabId})`);
      if (injectionError) {
        throw injectionError;
      }
      return html;
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
    newId: () => {
      minted += 1;
      return `fixed-test-id-${minted}`;
    },
    now: () => {
      clockReads += 1;
      return "2026-01-02T03:04:05.678Z";
    },
  };

  return {
    deps,
    journal,
    envelopes,
    reports,
    counts: {
      get minted() {
        return minted;
      },
      get clockReads() {
        return clockReads;
      },
    },
  };
}

describe("choosing 'Save whole page' on an ordinary page", () => {
  it("reads the page and then submits it", async () => {
    const { deps, journal } = harness();

    await runWholePageCapture(TAB, deps);

    assert.deepEqual(journal, [
      "report(busy)",
      "executeScript(7)",
      "sendCapture",
      "report(complete)",
    ]);
  });

  it("reports busy before doing any work", async () => {
    const { deps, journal } = harness();

    await runWholePageCapture(TAB, deps);

    assert.equal(journal[0], "report(busy)");
  });

  it("says it is saving a page, not a selection", async () => {
    const { deps, reports } = harness();

    await runWholePageCapture(TAB, deps);

    assert.deepEqual(reports[0], { busy: true, kind: "page" });
  });

  it("builds a webpage envelope from the tab and the snapshot", async () => {
    const { deps, envelopes } = harness();

    await runWholePageCapture(TAB, deps);

    assert.equal(envelopes.length, 1);
    assert.equal(envelopes[0].payload.type, "webpage");
    assert.equal(envelopes[0].payload.mime_type, "text/html");
    assert.equal(envelopes[0].payload.html, PAGE_HTML);
    assert.equal(envelopes[0].source.url, TAB.url);
    assert.equal(envelopes[0].payload.title, TAB.title);
    assert.equal(envelopes[0].context.captured_at, "2026-01-02T03:04:05.678Z");
  });

  it("hands sendCapture the exact string the page returned", async () => {
    const awkward = "  <html>\r\n\t<body>Å — 你好  🌍</body>\n</html>  ";
    const { deps, envelopes } = harness({ html: awkward });

    await runWholePageCapture(TAB, deps);

    assert.equal(envelopes[0].payload.html, awkward);
  });

  it("mints exactly one capture id for one logical page capture", async () => {
    const { deps, counts, envelopes } = harness();

    await runWholePageCapture(TAB, deps);

    assert.equal(counts.minted, 1);
    assert.equal(envelopes[0].id, "fixed-test-id-1");
  });

  it("returns the outcome it reported", async () => {
    const { deps, reports } = harness();

    const result = await runWholePageCapture(TAB, deps);

    assert.equal(result.outcome, OUTCOME.COMPLETE);
    assert.equal(reports.at(-1), result);
  });

  it("reports the result exactly once", async () => {
    const { deps, journal } = harness();

    await runWholePageCapture(TAB, deps);

    assert.equal(journal.filter((entry) => entry.startsWith("report(")).length, 2);
    assert.equal(journal.filter((entry) => entry === "report(complete)").length, 1);
  });
});

describe("pages a snapshot cannot be taken from", () => {
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

      const result = await runWholePageCapture({ ...TAB, url }, deps);

      assert.equal(result.outcome, OUTCOME.UNSUPPORTED_PAGE);
      assert.deepEqual(journal, ["report(busy)", "report(unsupported_page)"]);
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

      const result = await runWholePageCapture(tab, deps);

      assert.equal(result.outcome, OUTCOME.UNSUPPORTED_PAGE);
      assert.ok(!journal.some((entry) => entry.startsWith("executeScript")));
      assert.ok(!journal.includes("sendCapture"));
    });
  }

  it("mints no capture id and reads no clock for a refused page", async () => {
    const { deps, counts } = harness();

    await runWholePageCapture({ ...TAB, url: "chrome://extensions" }, deps);

    assert.equal(counts.minted, 0);
    assert.equal(counts.clockReads, 0);
  });
});

describe("a page that cannot be read", () => {
  it("fails locally when injection is refused", async () => {
    const { deps, journal } = harness({ injectionError: new Error("Cannot access contents") });

    const result = await runWholePageCapture(TAB, deps);

    assert.equal(result.outcome, OUTCOME.PAGE_CAPTURE_FAILED);
    assert.ok(!journal.includes("sendCapture"));
  });

  it("mints no capture id when injection is refused", async () => {
    const { deps, counts } = harness({ injectionError: new Error("Cannot access contents") });

    await runWholePageCapture(TAB, deps);

    assert.equal(counts.minted, 0);
    assert.equal(counts.clockReads, 0);
  });

  it("does not surface the browser's exception", async () => {
    const { deps } = harness({ injectionError: new Error("Cannot access chrome:// URL") });

    const result = await runWholePageCapture(TAB, deps);

    assert.equal(JSON.stringify(result).includes("chrome://"), false);
  });

  for (const [label, html] of [
    ["no result at all", undefined],
    ["a null result", null],
    ["an empty string", ""],
    ["only spaces", "   "],
    ["only a newline", "\n"],
    ["mixed whitespace", " \t\r\n "],
    ["a number", 42],
    ["an object", { outerHTML: "<html></html>" }],
    ["an array of frame results", [{ result: "<html></html>" }]],
  ]) {
    it(`treats ${label} as an unavailable snapshot`, async () => {
      const { deps, journal, counts } = harness({ html });

      const result = await runWholePageCapture(TAB, deps);

      assert.equal(result.outcome, OUTCOME.PAGE_CAPTURE_FAILED);
      assert.ok(!journal.includes("sendCapture"));
      assert.equal(counts.minted, 0);
      assert.equal(counts.clockReads, 0);
    });
  }

  it("never leaks the page into the reported result", async () => {
    const { deps } = harness({ html: "   " });

    const result = await runWholePageCapture(TAB, deps);

    assert.deepEqual(result, { outcome: OUTCOME.PAGE_CAPTURE_FAILED });
  });
});

describe("the blank check is a question, never an edit", () => {
  for (const html of [
    "  <html></html>  ",
    "\r\n<html>\r\n<body>x</body>\r\n</html>\r\n",
    "\t<html><body> </body></html>\t",
    "<html><body>Å vs Å — 你好 🌍</body></html>",
  ]) {
    it(`submits ${JSON.stringify(html)} unchanged`, async () => {
      const { deps, envelopes } = harness({ html });

      await runWholePageCapture(TAB, deps);

      assert.equal(envelopes[0].payload.html, html);
      assert.equal(envelopes[0].payload.html.length, html.length);
    });
  }
});

describe("the page never chooses the destination", () => {
  it("the API endpoint is a module constant, not a tab field", async () => {
    const { deps, envelopes } = harness();
    const hostile = {
      id: 7,
      url: "https://evil.example/?api=http://attacker.invalid",
      title: "http://attacker.invalid",
    };

    await runWholePageCapture(hostile, deps);

    // The page's URL and title travelled as metadata, and nowhere else.
    assert.equal(envelopes[0].source.url, hostile.url);
    assert.equal(envelopes[0].payload.title, hostile.title);
    assert.equal(CAPTURES_ENDPOINT, "http://127.0.0.1:8765/v1/captures");
  });

  it("the page HTML is payload data only", async () => {
    const hostile = '<html><body><a href="http://attacker.invalid/steal">x</a></body></html>';
    const { deps, envelopes } = harness({ html: hostile });

    await runWholePageCapture(TAB, deps);

    assert.equal(envelopes[0].payload.html, hostile);
    assert.equal(envelopes[0].source.url, TAB.url);
  });
});

describe("the injected page reader", () => {
  const source = readPageHtml.toString();

  it("returns the top-level document element's serialization", () => {
    assert.match(source, /document\.documentElement\?\.outerHTML/);
  });

  it("returns it without trimming or otherwise touching it", () => {
    for (const forbidden of ["trim", "replace", "normalize", "slice", "concat", "+"]) {
      assert.ok(!source.includes(forbidden), forbidden);
    }
  });

  it("does not read the selection", () => {
    assert.ok(!source.includes("getSelection"));
    assert.notEqual(source, readSelection.toString());
  });

  it("performs no network request", () => {
    for (const forbidden of ["fetch", "XMLHttpRequest", "sendBeacon", "WebSocket", "import("]) {
      assert.ok(!source.includes(forbidden), forbidden);
    }
  });

  it("knows nothing about the API", () => {
    assert.ok(!source.includes("127.0.0.1"));
    assert.ok(!source.includes("UniMem"));
    assert.ok(!source.includes("captures"));
  });

  it("closes over nothing, so it survives serialization into the page", () => {
    assert.ok(!source.includes("CAPTURES_ENDPOINT"));
    assert.ok(!source.includes("OUTCOME"));
    assert.doesNotThrow(() => new Function(`return ${source}`));
  });

  it("reads no iframe document", () => {
    for (const forbidden of ["iframe", "frames", "contentDocument", "contentWindow"]) {
      assert.ok(!source.includes(forbidden), forbidden);
    }
  });

  it("walks no shadow root", () => {
    for (const forbidden of ["shadowRoot", "attachShadow", "assignedNodes", "getInnerHTML"]) {
      assert.ok(!source.includes(forbidden), forbidden);
    }
  });

  it("synthesizes no doctype", () => {
    assert.ok(!/doctype/i.test(source));
    assert.ok(!source.includes("XMLSerializer"));
    assert.ok(!source.includes("documentElement.outerHTML}`"));
  });

  it("gathers no resource", () => {
    for (const forbidden of [
      "styleSheets",
      "getComputedStyle",
      "querySelectorAll",
      "images",
      "canvas",
      "toDataURL",
      "createObjectURL",
      "localStorage",
      "sessionStorage",
      "cookie",
    ]) {
      assert.ok(!source.includes(forbidden), forbidden);
    }
  });

  it("inspects no form", () => {
    for (const forbidden of ["forms", "FormData", "elements", "value"]) {
      assert.ok(!source.includes(forbidden), forbidden);
    }
  });
});

describe("the page module's own source", () => {
  const pageSource = codeOf("../lib/page.js");

  it("does not ask for all frames", () => {
    assert.ok(!pageSource.includes("allFrames"));
  });

  it("does not read the selection", () => {
    assert.ok(!pageSource.includes("getSelection"));
    assert.ok(!pageSource.includes("readSelection"));
  });

  it("performs no fetch of its own", () => {
    assert.ok(!pageSource.includes("fetch("));
    assert.ok(!pageSource.includes("XMLHttpRequest"));
  });

  it("writes no persistent state", () => {
    for (const api of ["chrome.storage", "localStorage", "indexedDB"]) {
      assert.ok(!pageSource.includes(api), api);
    }
  });

  it("schedules no automatic retry", () => {
    for (const word of ["setTimeout", "setInterval"]) {
      assert.ok(!pageSource.includes(word), word);
    }
  });

  it("logs nothing", () => {
    assert.ok(!pageSource.includes("console."));
  });
});
