/**
 * The `WEBPAGE` envelope, field by field, and byte for byte.
 *
 * The sibling of `envelope.test.js`. Most of these tests are about exactness:
 * the HTML string the browser handed back must arrive at the server as the same
 * string, and every plausible way to "clean it up" — trimming edge whitespace,
 * rewriting `\r\n`, normalizing Unicode, expanding a NBSP, prefixing a doctype —
 * is asserted *not* to happen.
 *
 * The fixture file is the other half of the cross-language contract: the Python
 * suite validates the same documents against the real `CaptureEnvelope`, so
 * neither side can change the shape without the other's tests failing.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { BlankPageError, buildWebpageCaptureEnvelope } from "../lib/envelope.js";

const FIXTURES = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("./fixtures/browser-webpage-envelopes.json", import.meta.url)),
    "utf8",
  ),
);

const HTML = '<html lang="en"><head><title>A page</title></head><body><p>Hi</p></body></html>';

function inputs(overrides = {}) {
  return {
    id: "b41d6f2a-3c58-4e77-9a10-5d8e2f6c0741",
    html: HTML,
    url: "https://example.com/a?b=c#d",
    title: "A page",
    capturedAt: "2026-01-02T03:04:05.678Z",
    ...overrides,
  };
}

describe("the webpage envelope's shape", () => {
  const envelope = buildWebpageCaptureEnvelope(inputs());

  it("declares canonical schema 0.2, not the extension's own version", () => {
    assert.equal(envelope.schema_version, "0.2");
  });

  it("carries the supplied id, opaquely", () => {
    assert.equal(envelope.id, "b41d6f2a-3c58-4e77-9a10-5d8e2f6c0741");
  });

  it("names the browser as the source type", () => {
    assert.equal(envelope.source.type, "browser");
  });

  it("names this connector as the provider", () => {
    assert.equal(envelope.source.provider, "unimem-browser-extension");
  });

  it("carries the page URL exactly, query and fragment included", () => {
    assert.equal(envelope.source.url, "https://example.com/a?b=c#d");
  });

  it("declares a webpage payload", () => {
    assert.equal(envelope.payload.type, "webpage");
  });

  it("declares the HTML MIME type", () => {
    assert.equal(envelope.payload.mime_type, "text/html");
  });

  it("carries the captured_at it was given", () => {
    assert.equal(envelope.context.captured_at, "2026-01-02T03:04:05.678Z");
  });

  it("names this connector as the capturing application", () => {
    assert.equal(envelope.context.application, "unimem-browser-extension");
  });

  it("declares the save intent", () => {
    assert.deepEqual(envelope.intent, { action: "save" });
  });

  it("carries no field the canonical contract does not define", () => {
    assert.deepEqual(Object.keys(envelope).sort(), [
      "context",
      "id",
      "intent",
      "payload",
      "schema_version",
      "source",
    ]);
    assert.deepEqual(Object.keys(envelope.source).sort(), ["provider", "type", "url"]);
    assert.deepEqual(Object.keys(envelope.payload).sort(), ["html", "mime_type", "title", "type"]);
  });
});

describe("the payload carries the snapshot and nothing else", () => {
  it("has no text, so intake is never asked to choose between two originals", () => {
    const envelope = buildWebpageCaptureEnvelope(inputs());

    assert.equal("text" in envelope.payload, false);
    assert.equal(envelope.payload.text, undefined);
  });

  it("has no file_ref either", () => {
    const envelope = buildWebpageCaptureEnvelope(inputs());

    assert.equal("file_ref" in envelope.payload, false);
    assert.equal(envelope.payload.file_ref, undefined);
  });
});

describe("the HTML is submitted exactly as the page returned it", () => {
  const exact = [
    ["plain markup", HTML],
    ["leading and trailing whitespace", "   <html><body>x</body></html>   "],
    ["a leading newline", "\n<html><body>x</body></html>"],
    ["CRLF line endings", "<html>\r\n<body>\r\nx\r\n</body>\r\n</html>"],
    ["a lone CR", "<html>\r<body>x</body>\r</html>"],
    ["tabs", "<html>\n\t<body>\n\t\tx\n\t</body>\n</html>"],
    ["a literal NBSP character", "<html><body>a b</body></html>"],
    ["an escaped NBSP entity", "<html><body>a&nbsp;b</body></html>"],
    ["decomposed and precomposed Unicode", "<html><body>Å vs Å</body></html>"],
    ["CJK and astral-plane characters", "<html><body>你好 🌍 𝔘</body></html>"],
    ["a BOM in the middle", "<html><body>a﻿b</body></html>"],
    ["an unclosed tag the browser left as it found it", "<html><body><p>x</body></html>"],
    ["no doctype, because outerHTML has none", "<html><body>x</body></html>"],
    ["what looks like a doctype inside the body", "<html><body>&lt;!doctype html&gt;</body></html>"],
  ];

  for (const [label, html] of exact) {
    it(`preserves ${label}`, () => {
      const envelope = buildWebpageCaptureEnvelope(inputs({ html }));

      assert.equal(envelope.payload.html, html);
      assert.equal(envelope.payload.html.length, html.length);
      assert.deepEqual([...envelope.payload.html], [...html]);
    });
  }

  it("prepends no doctype of its own", () => {
    const envelope = buildWebpageCaptureEnvelope(inputs({ html: "<html><body>x</body></html>" }));

    assert.ok(!/^\s*<!doctype/i.test(envelope.payload.html));
    assert.equal(envelope.payload.html.startsWith("<html"), true);
  });

  it("appends nothing either", () => {
    const envelope = buildWebpageCaptureEnvelope(inputs({ html: "<html><body>x</body></html>" }));

    assert.equal(envelope.payload.html.endsWith("</html>"), true);
  });

  it("survives JSON serialization unchanged, which is how it reaches the server", () => {
    const html = "  <html>\r\n<body>Å 你好 🌍</body>\n</html>  ";
    const envelope = buildWebpageCaptureEnvelope(inputs({ html }));

    const roundTripped = JSON.parse(JSON.stringify(envelope));

    assert.equal(roundTripped.payload.html, html);
  });
});

describe("the tab title", () => {
  it("is carried exactly when the tab has one", () => {
    const envelope = buildWebpageCaptureEnvelope(inputs({ title: "  Spaced — Title  " }));

    assert.equal(envelope.payload.title, "  Spaced — Title  ");
  });

  for (const [label, title] of [
    ["undefined", undefined],
    ["null", null],
    ["empty", ""],
    ["spaces", "   "],
    ["a newline", "\n"],
    ["mixed whitespace", " \t\r\n "],
    ["not a string", 12],
  ]) {
    it(`is omitted entirely when it is ${label}`, () => {
      const envelope = buildWebpageCaptureEnvelope(inputs({ title }));

      assert.equal("title" in envelope.payload, false);
    });
  }

  it("is never extracted from the HTML when the tab has none", () => {
    const envelope = buildWebpageCaptureEnvelope(
      inputs({ title: "", html: "<html><head><title>In the markup</title></head></html>" }),
    );

    assert.equal("title" in envelope.payload, false);
    assert.equal(JSON.stringify(envelope.payload).includes('"In the markup"'), false);
  });
});

describe("a snapshot that is not worth sending", () => {
  for (const [label, html] of [
    ["undefined", undefined],
    ["null", null],
    ["empty", ""],
    ["spaces", "   "],
    ["a newline", "\n"],
    ["mixed whitespace", " \t\r\n "],
    ["not a string", 42],
  ]) {
    it(`refuses ${label} rather than building an empty webpage envelope`, () => {
      assert.throws(() => buildWebpageCaptureEnvelope(inputs({ html })), BlankPageError);
    });
  }
});

describe("the builder is pure", () => {
  it("mutates neither its argument object nor the strings in it", () => {
    const supplied = Object.freeze(inputs());
    const before = JSON.parse(JSON.stringify(supplied));

    buildWebpageCaptureEnvelope(supplied);

    assert.deepEqual(JSON.parse(JSON.stringify(supplied)), before);
  });

  it("builds a fresh envelope every time", () => {
    const first = buildWebpageCaptureEnvelope(inputs());
    const second = buildWebpageCaptureEnvelope(inputs());

    assert.notEqual(first, second);
    assert.notEqual(first.payload, second.payload);
    assert.deepEqual(first, second);
  });

  it("reads no clock and mints no id of its own", () => {
    const envelope = buildWebpageCaptureEnvelope(inputs({ capturedAt: "2000-01-01T00:00:00.000Z" }));

    assert.equal(envelope.context.captured_at, "2000-01-01T00:00:00.000Z");
    assert.equal(envelope.id, "b41d6f2a-3c58-4e77-9a10-5d8e2f6c0741");
  });
});

describe("the fixture the Python suite validates", () => {
  it("covers more than one shape", () => {
    assert.ok(FIXTURES.length >= 2);
  });

  for (const testCase of FIXTURES) {
    it(`is reproduced exactly for: ${testCase.name}`, () => {
      assert.deepEqual(buildWebpageCaptureEnvelope(testCase.inputs), testCase.envelope);
    });

    it(`keeps the HTML byte for byte for: ${testCase.name}`, () => {
      const built = buildWebpageCaptureEnvelope(testCase.inputs);

      assert.equal(built.payload.html, testCase.inputs.html);
    });
  }
});
