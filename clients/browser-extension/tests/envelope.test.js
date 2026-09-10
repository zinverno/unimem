/**
 * The envelope: canonical shape, and text that survives untouched.
 *
 * The exactness tests are the ones that matter. A connector is the last place a
 * selection can be quietly "cleaned up" before it becomes durable, and the
 * canonical contracts preserve what they are given — so anything this file lets
 * through is lost for good.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  BlankSelectionError,
  CONNECTOR_NAME,
  SCHEMA_VERSION,
  buildCaptureEnvelope,
  isBlank,
  newCaptureId,
  nowIso,
} from "../lib/envelope.js";

const CAPTURED_AT = "2026-01-02T03:04:05.678Z";
const PAGE_URL = "https://example.com/articles/canonical?ref=x#frag";

function build(overrides = {}) {
  return buildCaptureEnvelope({
    id: "cap-1",
    selection: "selected words",
    url: PAGE_URL,
    title: "A page title",
    capturedAt: CAPTURED_AT,
    ...overrides,
  });
}

describe("canonical shape", () => {
  it("declares schema version 0.2 exactly", () => {
    assert.equal(build().schema_version, "0.2");
    assert.equal(SCHEMA_VERSION, "0.2");
  });

  it("names browser as the source type", () => {
    assert.equal(build().source.type, "browser");
  });

  it("names this connector as the provider", () => {
    assert.equal(build().source.provider, "unimem-browser-extension");
    assert.equal(CONNECTOR_NAME, "unimem-browser-extension");
  });

  it("carries the page URL exactly, query and fragment included", () => {
    assert.equal(build().source.url, PAGE_URL);
  });

  it("declares a text payload", () => {
    assert.equal(build().payload.type, "text");
  });

  it("declares text/plain", () => {
    assert.equal(build().payload.mime_type, "text/plain");
  });

  it("sets the capturing application", () => {
    assert.equal(build().context.application, "unimem-browser-extension");
  });

  it("asks the system to save", () => {
    assert.equal(build().intent.action, "save");
  });

  it("preserves a supplied id exactly", () => {
    assert.equal(build({ id: "  an-odd-id  " }).id, "  an-odd-id  ");
  });

  it("carries no field the contract does not define", () => {
    const envelope = build();
    assert.deepEqual(Object.keys(envelope).sort(), [
      "context",
      "id",
      "intent",
      "payload",
      "schema_version",
      "source",
    ]);
  });
});

describe("the selection is submitted exactly as the page returned it", () => {
  const cases = [
    ["leading spaces", "   leading"],
    ["trailing spaces", "trailing   "],
    ["both", "  both  "],
    ["a leading newline", "\nline"],
    ["a trailing newline", "line\n"],
    ["CRLF line endings", "one\r\ntwo\r\nthree"],
    ["tabs", "\tcol\tcol\t"],
    ["blank lines between paragraphs", "one\n\n\ntwo"],
    ["combining marks that NFC would fold", "Å vs Å"],
    ["CJK", "你好世界"],
    ["an astral-plane emoji", "\u{1f30d}\u{1f9ea}"],
    ["a non-breaking space", "a b"],
    ["a byte order mark mid-string", "a﻿b"],
    ["a zero-width joiner sequence", "\u{1f469}‍\u{1f4bb}"],
    ["a right-to-left string", "שלום"],
    ["a lone surrogate-free astral pair with text", "x\u{1d11e}y"],
  ];

  for (const [label, selection] of cases) {
    it(`preserves ${label}`, () => {
      assert.equal(build({ selection }).payload.text, selection);
    });
  }

  it("preserves the string identically, not merely equivalently", () => {
    const selection = "  Å vs Å — 你好 \u{1f30d}\r\n\tindented\n\ntrailing   \n ﻿end  ";
    const text = build({ selection }).payload.text;

    assert.equal(text, selection);
    assert.equal(text.length, selection.length);
    assert.equal([...text].length, [...selection].length);
  });

  it("uses trim() only to decide blankness, never to transform", () => {
    const padded = "\n\t  content  \t\n";

    assert.equal(isBlank(padded), false);
    assert.equal(build({ selection: padded }).payload.text, padded);
    assert.notEqual(build({ selection: padded }).payload.text, padded.trim());
  });
});

describe("blank selections are rejected locally", () => {
  for (const [label, selection] of [
    ["an empty string", ""],
    ["one space", " "],
    ["many spaces", "     "],
    ["a tab", "\t"],
    ["a newline", "\n"],
    ["CRLF", "\r\n"],
    ["mixed whitespace", " \t\r\n "],
    ["undefined (no selection object)", undefined],
    ["null", null],
  ]) {
    it(`treats ${label} as blank`, () => {
      assert.equal(isBlank(selection), true);
    });

    it(`refuses to build an envelope from ${label}`, () => {
      assert.throws(() => build({ selection }), BlankSelectionError);
    });
  }

  it("treats a non-string as blank rather than stringifying it", () => {
    assert.equal(isBlank(42), true);
    assert.equal(isBlank({}), true);
  });
});

describe("the page title", () => {
  it("is preserved exactly when it is non-blank", () => {
    const title = "  Spaced — Title\twith tabs  ";
    assert.equal(build({ title }).payload.title, title);
  });

  for (const [label, title] of [
    ["missing", undefined],
    ["null", null],
    ["empty", ""],
    ["only spaces", "   "],
    ["only whitespace", " \t\n "],
  ]) {
    it(`is omitted entirely when ${label}`, () => {
      const payload = build({ title }).payload;
      assert.ok(!("title" in payload));
    });
  }

  it("is never invented from the URL, hostname, or selection", () => {
    const envelope = build({ title: "", selection: "some words", url: PAGE_URL });
    assert.ok(!("title" in envelope.payload));
    assert.equal(JSON.stringify(envelope.payload).includes("example.com"), false);
  });
});

describe("captured_at", () => {
  it("is whatever the caller supplies, so it is testable", () => {
    assert.equal(build({ capturedAt: CAPTURED_AT }).context.captured_at, CAPTURED_AT);
  });

  it("comes from nowIso() in production, as a UTC ISO-8601 instant", () => {
    const stamp = nowIso();
    assert.match(stamp, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    assert.equal(new Date(stamp).toISOString(), stamp);
  });
});

describe("capture ids", () => {
  it("are UUIDs", () => {
    assert.match(newCaptureId(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it("are independent across deliberate invocations", () => {
    const ids = new Set(Array.from({ length: 200 }, newCaptureId));
    assert.equal(ids.size, 200);
  });

  it("are not derived from the selection, the URL, or the clock", () => {
    const first = newCaptureId();
    const second = newCaptureId();
    assert.notEqual(first, second);
    assert.ok(!first.includes("example"));
  });
});

describe("cross-language fixture", () => {
  const fixture = JSON.parse(
    readFileSync(fileURLToPath(new URL("./fixtures/browser-envelopes.json", import.meta.url)), "utf8"),
  );

  it("covers more than one representative case", () => {
    assert.ok(fixture.length >= 2);
  });

  for (const { name, inputs, envelope } of fixture) {
    it(`matches what the builder produces: ${name}`, () => {
      assert.deepEqual(buildCaptureEnvelope(inputs), envelope);
    });
  }

  it("is the same file the Python contract test validates", () => {
    // If this drifts, the Python side fails too — which is the point of
    // sharing one file rather than writing the shape down twice.
    for (const { inputs, envelope } of fixture) {
      assert.equal(envelope.payload.text, inputs.selection);
    }
  });
});
