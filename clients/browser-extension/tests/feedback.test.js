/**
 * The badge and title are the entire UI, so they are the entire UI contract.
 *
 * The leak tests are the important ones. A browser action title sits under the
 * cursor, appears in screenshots, and is read by screen readers — so the user's
 * selected text must never reach it, and neither must an exception's message or
 * stack. Both would be easy to include "just for debugging" and hard to notice
 * afterwards.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { BADGE_BUSY, BADGE_FAIL, BADGE_OK, busyFeedback, feedbackFor } from "../lib/feedback.js";
import { OUTCOME } from "../lib/outcomes.js";

const SECRET = "correct horse battery staple";

/** Every result shape the connector can produce, in one place. */
const ALL_RESULTS = [
  { outcome: OUTCOME.COMPLETE, confirmedBy: "post" },
  { outcome: OUTCOME.COMPLETE, confirmedBy: "probe", probed: true },
  { outcome: OUTCOME.BLANK_SELECTION },
  { outcome: OUTCOME.UNSUPPORTED_PAGE },
  { outcome: OUTCOME.INJECTION_FAILED },
  { outcome: OUTCOME.UNAVAILABLE },
  { outcome: OUTCOME.UNAVAILABLE, probed: true },
  { outcome: OUTCOME.UNCONFIRMED },
  { outcome: OUTCOME.DURABLE_STATE, status: "received" },
  { outcome: OUTCOME.DURABLE_STATE, status: "stored" },
  { outcome: OUTCOME.DURABLE_STATE, status: "processing" },
  { outcome: OUTCOME.DURABLE_STATE, status: "failed" },
  { outcome: OUTCOME.SERVER_ERROR, status: 409, code: "capture_already_exists", message: "taken" },
  { outcome: OUTCOME.SERVER_ERROR, status: 503, code: "storage_unavailable", message: "down" },
  { outcome: OUTCOME.SERVER_ERROR, status: 500, code: null, message: null },
  { outcome: OUTCOME.PROTOCOL_ERROR, detail: "the body was not JSON" },
];

describe("badges", () => {
  it("shows ... while sending", () => {
    assert.equal(busyFeedback().badge, BADGE_BUSY);
    assert.equal(BADGE_BUSY, "...");
  });

  it("shows OK only for a confirmed complete capture", () => {
    assert.equal(feedbackFor({ outcome: OUTCOME.COMPLETE }).badge, BADGE_OK);
    assert.equal(BADGE_OK, "OK");
  });

  it("shows OK for a capture confirmed by the recovery probe", () => {
    assert.equal(feedbackFor({ outcome: OUTCOME.COMPLETE, probed: true }).badge, BADGE_OK);
  });

  for (const result of ALL_RESULTS.filter((r) => r.outcome !== OUTCOME.COMPLETE)) {
    it(`shows ! for ${result.outcome}${result.status ? ` (${result.status})` : ""}`, () => {
      assert.equal(feedbackFor(result).badge, BADGE_FAIL);
    });
  }

  it("shows ! for an outcome it has never seen", () => {
    assert.equal(feedbackFor({ outcome: "something_new" }).badge, BADGE_FAIL);
    assert.equal(feedbackFor(undefined).badge, BADGE_FAIL);
  });
});

describe("titles", () => {
  it("says saved on success", () => {
    assert.equal(feedbackFor({ outcome: OUTCOME.COMPLETE }).title, "UniMem: saved");
  });

  it("distinguishes a capture confirmed after a network error", () => {
    const title = feedbackFor({ outcome: OUTCOME.COMPLETE, probed: true }).title;
    assert.match(title, /saved/);
    assert.match(title, /confirmed/);
  });

  it("asks for a selection when there was none", () => {
    assert.equal(
      feedbackFor({ outcome: OUTCOME.BLANK_SELECTION }).title,
      "UniMem: select some text first",
    );
  });

  it("says the service is unavailable on a network failure", () => {
    assert.equal(feedbackFor({ outcome: OUTCOME.UNAVAILABLE }).title, "UniMem: service unavailable");
  });

  it("admits the outcome is unknown when even the probe failed", () => {
    const title = feedbackFor({ outcome: OUTCOME.UNAVAILABLE, probed: true }).title;
    assert.match(title, /unavailable/);
    assert.match(title, /unknown/);
  });

  it("says a capture is still processing", () => {
    assert.equal(
      feedbackFor({ outcome: OUTCOME.DURABLE_STATE, status: "processing" }).title,
      "UniMem: capture is still processing",
    );
  });

  it("names a known server error in plain words", () => {
    assert.equal(
      feedbackFor({ outcome: OUTCOME.SERVER_ERROR, status: 409, code: "capture_already_exists" })
        .title,
      "UniMem: that capture already exists",
    );
  });

  it("falls back to a status for an unknown server error code", () => {
    assert.equal(
      feedbackFor({ outcome: OUTCOME.SERVER_ERROR, status: 418, code: "teapot" }).title,
      "UniMem: capture failed (418)",
    );
  });

  it("never presents a non-complete outcome as saved", () => {
    for (const result of ALL_RESULTS.filter((r) => r.outcome !== OUTCOME.COMPLETE)) {
      assert.ok(!/\bsaved\b/.test(feedbackFor(result).title), JSON.stringify(result));
    }
  });

  it("always starts with the extension's name, so the source is obvious", () => {
    for (const result of [...ALL_RESULTS, undefined, { outcome: "unknown" }]) {
      assert.match(feedbackFor(result).title, /^UniMem: /);
    }
    assert.match(busyFeedback().title, /^UniMem: /);
  });

  it("stays short enough to read in a tooltip", () => {
    for (const result of ALL_RESULTS) {
      assert.ok(feedbackFor(result).title.length <= 60, feedbackFor(result).title);
    }
  });
});

describe("what feedback must never contain", () => {
  it("never contains the selected text", () => {
    // The mapper is given only outcomes, so the selection cannot reach it —
    // but the check is made against a result that smuggles it in anyway.
    const smuggled = { outcome: OUTCOME.SERVER_ERROR, status: 422, code: SECRET, message: SECRET };

    const { badge, title } = feedbackFor(smuggled);

    assert.ok(!title.includes(SECRET));
    assert.ok(!badge.includes(SECRET));
  });

  it("never echoes the server's operator-facing message", () => {
    const result = {
      outcome: OUTCOME.SERVER_ERROR,
      status: 503,
      code: "storage_unavailable",
      message: "the capture record database at /var/lib/unimem/unimem.sqlite3 could not read",
    };

    const { title } = feedbackFor(result);

    assert.ok(!title.includes("/var/lib"));
    assert.ok(!title.includes("sqlite3"));
  });

  it("never contains a raw exception message or stack", () => {
    const error = new TypeError("Failed to fetch");
    const result = { outcome: OUTCOME.UNAVAILABLE, error, detail: error.stack };

    const { title } = feedbackFor(result);

    assert.ok(!title.includes("TypeError"));
    assert.ok(!title.includes("Failed to fetch"));
    assert.ok(!title.includes("at "));
  });

  it("never contains a protocol error's internal detail", () => {
    const result = { outcome: OUTCOME.PROTOCOL_ERROR, detail: "capture_id was 'abc' not 'def'" };

    assert.ok(!feedbackFor(result).title.includes("abc"));
  });

  it("never contains a URL", () => {
    for (const result of ALL_RESULTS) {
      assert.ok(!/https?:\/\//.test(feedbackFor(result).title));
    }
  });
});
