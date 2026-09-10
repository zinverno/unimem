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
  { outcome: OUTCOME.COMPLETE, confirmedBy: "replay" },
  { outcome: OUTCOME.COMPLETE, confirmedBy: "probe", probed: true },
  { outcome: OUTCOME.BLANK_SELECTION },
  { outcome: OUTCOME.UNSUPPORTED_PAGE },
  { outcome: OUTCOME.INJECTION_FAILED },
  { outcome: OUTCOME.PAGE_CAPTURE_FAILED },
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

  it("says saved for a capture the server replayed", () => {
    const title = feedbackFor({ outcome: OUTCOME.COMPLETE, confirmedBy: "replay" }).title;

    assert.equal(title, "UniMem: saved (confirmed retry)");
  });

  it("shows OK for a replay, because a replay is a saved capture", () => {
    assert.equal(feedbackFor({ outcome: OUTCOME.COMPLETE, confirmedBy: "replay" }).badge, BADGE_OK);
  });

  it("reads as reassurance rather than as an error", () => {
    /* The user clicked once and their selection is saved once. Nothing here may
     * suggest a failure, a duplicate, or something needing their attention. */
    const title = feedbackFor({ outcome: OUTCOME.COMPLETE, confirmedBy: "replay" }).title;

    assert.match(title, /saved/);
    for (const alarming of [/error/i, /fail/i, /duplicate/i, /conflict/i, /warning/i, /409/]) {
      assert.doesNotMatch(title, alarming);
    }
  });

  it("keeps the three confirmations distinguishable", () => {
    const titleFor = (confirmedBy) => feedbackFor({ outcome: OUTCOME.COMPLETE, confirmedBy }).title;

    assert.equal(new Set(["post", "replay", "probe"].map(titleFor)).size, 3);
  });

  it("still says saved for a success it has no vocabulary for", () => {
    assert.equal(feedbackFor({ outcome: OUTCOME.COMPLETE }).title, "UniMem: saved");
    assert.equal(feedbackFor({ outcome: OUTCOME.COMPLETE, confirmedBy: "new-thing" }).title, "UniMem: saved");
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

describe("the vocabulary is covered", () => {
  it("has a title for every outcome the connector can produce", () => {
    // A new outcome with no wording falls through to "UniMem: capture failed",
    // which is a truthful answer but rarely the useful one. This is the test
    // that makes adding one a deliberate act.
    const described = new Set(ALL_RESULTS.map((result) => result.outcome));

    assert.deepEqual(
      Object.values(OUTCOME).filter((outcome) => !described.has(outcome)),
      [OUTCOME.UNEXPECTED_ERROR],
    );
  });
});

describe("whole-page wording", () => {
  it("says the page could not be read, and nothing about why", () => {
    const { badge, title } = feedbackFor({ outcome: OUTCOME.PAGE_CAPTURE_FAILED });

    assert.equal(badge, BADGE_FAIL);
    assert.equal(title, "UniMem: could not read this page");
  });

  it("never tells someone who asked to save a page to select some text", () => {
    const title = feedbackFor({ outcome: OUTCOME.PAGE_CAPTURE_FAILED }).title;

    assert.ok(!/select/i.test(title));
    assert.ok(!/selection/i.test(title));
  });

  it("says it is saving a page while a page capture is in flight", () => {
    assert.deepEqual(busyFeedback("page"), { badge: BADGE_BUSY, title: "UniMem: saving page..." });
  });

  it("still says it is saving a selection for the selection flow", () => {
    assert.deepEqual(busyFeedback("selection"), {
      badge: BADGE_BUSY,
      title: "UniMem: saving selection...",
    });
  });

  it("falls back to the selection wording for a busy report that names no kind", () => {
    assert.deepEqual(busyFeedback(), busyFeedback("selection"));
    assert.deepEqual(busyFeedback("something-else"), busyFeedback("selection"));
  });

  it("reports a confirmed page capture with the same OK the selection gets", () => {
    assert.equal(feedbackFor({ outcome: OUTCOME.COMPLETE, confirmedBy: "post" }).badge, BADGE_OK);
    assert.equal(feedbackFor({ outcome: OUTCOME.COMPLETE, confirmedBy: "post" }).title, "UniMem: saved");
  });

  it("never puts page markup in the title", () => {
    const smuggled = {
      outcome: OUTCOME.SERVER_ERROR,
      status: 422,
      code: "<html><body>secret</body></html>",
      message: "<html><body>secret</body></html>",
    };

    const { title } = feedbackFor(smuggled);

    assert.ok(!title.includes("<html"));
    assert.ok(!title.includes("secret"));
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

  it("never leaks a capture through the replay path either", () => {
    /* The success path now carries a capture id and a content id in the result.
     * Neither is secret, and neither belongs in a tooltip — and the selection,
     * smuggled in beside them, must not appear whatever the path. */
    const smuggled = {
      outcome: OUTCOME.COMPLETE,
      confirmedBy: "replay",
      captureId: "3f1b2c7e-9a4d-4e51-8b6f-0c2d7a1e5b93",
      contentId: "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9",
      text: SECRET,
      message: SECRET,
    };

    const { badge, title } = feedbackFor(smuggled);

    assert.ok(!title.includes(SECRET));
    assert.ok(!badge.includes(SECRET));
    assert.ok(!title.includes("3f1b2c7e"));
    assert.ok(!title.includes("1a2b3c4d"));
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
