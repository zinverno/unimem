/**
 * The ambiguous case: a POST that never came back.
 *
 * This is the one place a connector is genuinely tempted to do the wrong thing.
 * The capture id was minted before the request, so the server may hold a
 * finished capture the client never heard about — or nothing at all.
 *
 * The server now answers that question directly. A resubmission of the same
 * capture id carrying the same request is met with `200` and the existing
 * capture's result if that capture is already complete, and with the same `409`
 * as ever if it is not. So the client resends, **once**, and only after a
 * failure at the network layer where the outcome is genuinely unknown. This is
 * not a retry policy: an explicit HTTP error is a definite answer and is never
 * repeated, there is no loop, no backoff, no timer, and nothing is queued for
 * later.
 *
 * Every test here therefore asserts two things: what the user is told, and how
 * many requests it took. The hard bound is **two POSTs and one GET**, and the
 * last block of this file checks it across every path at once — because a bound
 * that holds in each case separately and not in general is not a bound.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { CAPTURES_ENDPOINT, getCapture, recoverNetworkOutcome, sendCapture } from "../lib/api.js";
import { OUTCOME } from "../lib/outcomes.js";
import {
  SUBMITTED_ID,
  brokenResponse,
  createdBody,
  errorBody,
  fakeFetch,
  jsonResponse,
  networkError,
  testEnvelope,
} from "./helpers.js";

const PROBE_URL = `${CAPTURES_ENDPOINT}/${SUBMITTED_ID}`;

function record(status) {
  return { schema_version: "0.2", id: SUBMITTED_ID, status };
}

/** The server's refusal when a capture id is taken and not replayable. */
function conflict() {
  return jsonResponse(409, errorBody("capture_already_exists", "capture 'x' is already stored"));
}

describe("a POST that reached the server is never repeated", () => {
  it("does not resend after a successful 201", async () => {
    const fetch = fakeFetch(jsonResponse(201, createdBody()));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.COMPLETE);
    assert.equal(result.confirmedBy, "post");
    assert.equal(fetch.calls.length, 1);
  });

  it("does not resend after a replay 200", async () => {
    const fetch = fakeFetch(jsonResponse(200, createdBody()));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.COMPLETE);
    assert.equal(result.confirmedBy, "replay");
    assert.equal(fetch.calls.length, 1);
  });

  for (const [status, code] of [
    [409, "capture_already_exists"],
    [422, "unsupported_payload"],
    [422, "invalid_request"],
    [500, "data_integrity_error"],
    [500, "processing_configuration_error"],
    [503, "storage_unavailable"],
  ]) {
    it(`does not resend or probe after ${status} ${code}`, async () => {
      /* The server judged this request. Sending it again would ask the same
       * question, and a GET would answer a question nobody asked. */
      const fetch = fakeFetch(jsonResponse(status, errorBody(code, "no")));

      const result = await sendCapture(testEnvelope(), { fetch });

      assert.equal(result.outcome, OUTCOME.SERVER_ERROR);
      assert.equal(result.status, status);
      assert.equal(fetch.posts().length, 1);
      assert.equal(fetch.calls.length, 1);
    });
  }

  it("does not resend after a protocol failure", async () => {
    const fetch = fakeFetch(jsonResponse(202, createdBody()));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
    assert.equal(fetch.calls.length, 1);
  });
});

describe("the resend after a lost response", () => {
  it("happens, and happens exactly once", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody()));

    await sendCapture(testEnvelope(), { fetch });

    assert.equal(fetch.posts().length, 2);
    assert.equal(fetch.calls.length, 2);
  });

  it("goes to the same endpoint", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody()));

    await sendCapture(testEnvelope(), { fetch });

    assert.equal(fetch.calls[1].url, CAPTURES_ENDPOINT);
    assert.equal(fetch.calls[0].url, fetch.calls[1].url);
  });

  it("declares JSON, exactly as the first attempt did", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody()));

    await sendCapture(testEnvelope(), { fetch });

    assert.deepEqual(fetch.calls[1].options.headers, { "Content-Type": "application/json" });
    assert.equal(fetch.calls[1].options.method, "POST");
  });

  it("carries the same capture id", async () => {
    /* The whole guarantee rests on this. A fresh id would be a second capture
     * of the same selection, which is the thing the user did not ask for. */
    const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody()));

    await sendCapture(testEnvelope(), { fetch });

    assert.equal(JSON.parse(fetch.calls[1].options.body).id, SUBMITTED_ID);
    assert.equal(JSON.parse(fetch.calls[0].options.body).id, SUBMITTED_ID);
  });

  it("sends a byte-identical body", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody()));

    await sendCapture(testEnvelope(), { fetch });

    assert.equal(fetch.calls[1].options.body, fetch.calls[0].options.body);
  });

  it("sends the very envelope it was given, unmodified", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody()));
    const envelope = testEnvelope();
    const before = JSON.stringify(envelope);

    await sendCapture(envelope, { fetch });

    assert.deepEqual(JSON.parse(fetch.calls[1].options.body), JSON.parse(before));
    assert.equal(JSON.stringify(envelope), before, "the caller's envelope was mutated");
  });

  it("does not restamp captured_at", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody()));
    const envelope = testEnvelope();

    await sendCapture(envelope, { fetch });

    const resent = JSON.parse(fetch.calls[1].options.body);
    assert.equal(resent.context.captured_at, envelope.context.captured_at);
    assert.equal(resent.context.captured_at, "2026-01-02T03:04:05.678Z");
  });

  it("does not re-read the selection, the title, or the URL", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody()));
    const envelope = testEnvelope();

    await sendCapture(envelope, { fetch });

    const resent = JSON.parse(fetch.calls[1].options.body);
    assert.equal(resent.payload.text, envelope.payload.text);
    assert.equal(resent.payload.title, envelope.payload.title);
    assert.equal(resent.source.url, envelope.source.url);
    assert.deepEqual(resent.intent, envelope.intent);
  });

  it("never touches crypto.randomUUID", async () => {
    /* The id belongs to the click, not to the request. `sendCapture` is not
     * even given a way to mint one — asserting it here says so out loud. */
    const original = globalThis.crypto?.randomUUID;
    let minted = 0;
    if (original !== undefined) {
      globalThis.crypto.randomUUID = () => {
        minted += 1;
        return original.call(globalThis.crypto);
      };
    }
    try {
      const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody()));

      await sendCapture(testEnvelope(), { fetch });

      assert.equal(minted, 0);
    } finally {
      if (original !== undefined) {
        globalThis.crypto.randomUUID = original;
      }
    }
  });
});

describe("what the resend is answered with", () => {
  it("201 means the first request never landed", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(201, createdBody()));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.COMPLETE);
    assert.equal(result.confirmedBy, "post");
    assert.equal(result.contentId, "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9");
    assert.equal(fetch.calls.length, 2);
  });

  it("200 means the first request had already completed", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody()));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.COMPLETE);
    assert.equal(result.confirmedBy, "replay");
    assert.equal(result.captureId, SUBMITTED_ID);
    assert.equal(result.contentId, "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9");
    assert.equal(fetch.calls.length, 2, "a resolved replay needs no probe");
  });

  it("a 200 that fails the contract is not success", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody({ status: "stored" })));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
    assert.equal(fetch.calls.length, 2);
  });

  it("a 200 naming another capture is not success", async () => {
    const body = createdBody({ capture_id: "a-different-capture" });
    const fetch = fakeFetch(networkError(), jsonResponse(200, body));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
  });

  for (const [status, code] of [
    [422, "invalid_request"],
    [500, "data_integrity_error"],
    [503, "storage_unavailable"],
  ]) {
    it(`${status} ${code} is returned as it stands, with no probe`, async () => {
      const fetch = fakeFetch(networkError(), jsonResponse(status, errorBody(code, "no")));

      const result = await sendCapture(testEnvelope(), { fetch });

      assert.equal(result.outcome, OUTCOME.SERVER_ERROR);
      assert.equal(result.status, status);
      assert.equal(result.code, code);
      assert.equal(fetch.calls.length, 2);
      assert.equal(fetch.gets().length, 0);
    });
  }
});

describe("a resend answered with 409 capture_already_exists", () => {
  /* The id is taken — almost certainly by this client's own first request —
   * but the server could not answer it as a completed replay. The capture may
   * still be processing, or may have failed. That is worth exactly one look. */

  it("triggers exactly one observational GET", async () => {
    const fetch = fakeFetch(networkError(), conflict(), jsonResponse(200, record("processing")));

    await sendCapture(testEnvelope(), { fetch });

    assert.equal(fetch.posts().length, 2);
    assert.equal(fetch.gets().length, 1);
    assert.equal(fetch.calls.length, 3);
  });

  it("looks up the very id it submitted", async () => {
    const fetch = fakeFetch(networkError(), conflict(), jsonResponse(200, record("processing")));

    await sendCapture(testEnvelope(), { fetch });

    assert.equal(fetch.calls[2].url, PROBE_URL);
  });

  it("reports a complete capture as success", async () => {
    const fetch = fakeFetch(networkError(), conflict(), jsonResponse(200, record("complete")));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.COMPLETE);
    assert.equal(result.confirmedBy, "probe");
    assert.equal(result.probed, true);
  });

  for (const status of ["received", "stored", "processing", "failed"]) {
    it(`reports the durable state '${status}' rather than inventing one`, async () => {
      const fetch = fakeFetch(networkError(), conflict(), jsonResponse(200, record(status)));

      const result = await sendCapture(testEnvelope(), { fetch });

      assert.equal(result.outcome, OUTCOME.DURABLE_STATE);
      assert.equal(result.status, status);
      assert.notEqual(result.outcome, OUTCOME.COMPLETE);
    });
  }

  it("sends no third POST, whatever the GET says", async () => {
    for (const observed of [
      jsonResponse(200, record("failed")),
      jsonResponse(404, errorBody("not_found", "gone")),
      networkError(),
    ]) {
      const fetch = fakeFetch(networkError(), conflict(), observed);

      await sendCapture(testEnvelope(), { fetch });

      assert.equal(fetch.posts().length, 2);
    }
  });

  it("does not probe after a 409 that is some other conflict", async () => {
    /* Only "this capture id is taken" is worth looking into. A `content_conflict`
     * is the server saying something else entirely. */
    const fetch = fakeFetch(networkError(), jsonResponse(409, errorBody("content_conflict", "no")));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.SERVER_ERROR);
    assert.equal(fetch.calls.length, 2);
    assert.equal(fetch.gets().length, 0);
  });

  it("does not probe after a 409 whose body carried no code", async () => {
    const fetch = fakeFetch(networkError(), brokenResponse(409));

    await sendCapture(testEnvelope(), { fetch });

    assert.equal(fetch.gets().length, 0);
  });
});

describe("a resend that also fails at the network layer", () => {
  it("triggers exactly one observational GET", async () => {
    const fetch = fakeFetch(networkError(), networkError(), jsonResponse(200, record("complete")));

    await sendCapture(testEnvelope(), { fetch });

    assert.equal(fetch.posts().length, 2);
    assert.equal(fetch.gets().length, 1);
  });

  it("confirms success when the capture is durably complete", async () => {
    const fetch = fakeFetch(networkError(), networkError(), jsonResponse(200, record("complete")));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.COMPLETE);
    assert.equal(result.confirmedBy, "probe");
    assert.equal(result.probed, true);
    assert.equal(result.captureId, SUBMITTED_ID);
  });

  for (const status of ["received", "stored", "processing", "failed"]) {
    it(`reports the durable state '${status}'`, async () => {
      const fetch = fakeFetch(networkError(), networkError(), jsonResponse(200, record(status)));

      const result = await sendCapture(testEnvelope(), { fetch });

      assert.equal(result.outcome, OUTCOME.DURABLE_STATE);
      assert.equal(result.status, status);
    });
  }

  it("reports unconfirmed when there is no such capture", async () => {
    const gone = jsonResponse(404, errorBody("not_found", "no such capture"));
    const fetch = fakeFetch(networkError(), networkError(), gone);

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.UNCONFIRMED);
    assert.equal(result.probed, true);
  });

  it("reports the outcome as unknown when the GET fails too", async () => {
    const fetch = fakeFetch(networkError(), networkError(), networkError());

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.UNAVAILABLE);
    assert.equal(result.probed, true);
    assert.equal(fetch.calls.length, 3);
  });

  it("stops even when the GET is inconclusive", async () => {
    const fetch = fakeFetch(networkError(), networkError(), brokenResponse(200));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
    assert.equal(fetch.calls.length, 3);
  });
});

describe("the hard bound, across every path there is", () => {
  /* Each entry is a whole scripted conversation. The assertions below run over
   * all of them at once, so a future path that quietly adds a request has to
   * break this block rather than only its own test. */
  const CONVERSATIONS = [
    ["created", [jsonResponse(201, createdBody())]],
    ["replayed on the first try", [jsonResponse(200, createdBody())]],
    ["refused outright", [conflict()]],
    ["rejected as invalid", [jsonResponse(422, errorBody("invalid_request", "no"))]],
    ["server error", [jsonResponse(500, errorBody("data_integrity_error", "no"))]],
    ["unavailable", [jsonResponse(503, errorBody("storage_unavailable", "no"))]],
    ["protocol failure", [jsonResponse(202, createdBody())]],
    ["lost, then created", [networkError(), jsonResponse(201, createdBody())]],
    ["lost, then replayed", [networkError(), jsonResponse(200, createdBody())]],
    ["lost, then refused", [networkError(), jsonResponse(500, errorBody("x", "no"))]],
    ["lost, then a conflict, then complete", [networkError(), conflict(), jsonResponse(200, record("complete"))]],
    ["lost, then a conflict, then processing", [networkError(), conflict(), jsonResponse(200, record("processing"))]],
    ["lost, then a conflict, then nothing", [networkError(), conflict(), jsonResponse(404, errorBody("not_found", "no"))]],
    ["lost twice, then complete", [networkError(), networkError(), jsonResponse(200, record("complete"))]],
    ["lost twice, then nothing", [networkError(), networkError(), jsonResponse(404, errorBody("not_found", "no"))]],
    ["lost entirely", [networkError(), networkError(), networkError()]],
  ];

  for (const [name, script] of CONVERSATIONS) {
    it(`sends at most two POSTs and one GET: ${name}`, async () => {
      const fetch = fakeFetch(...script);

      await sendCapture(testEnvelope(), { fetch });

      assert.ok(fetch.posts().length <= 2, `${fetch.posts().length} POSTs`);
      assert.ok(fetch.gets().length <= 1, `${fetch.gets().length} GETs`);
      assert.ok(fetch.calls.length <= 3, `${fetch.calls.length} requests`);
    });

    it(`always produces an outcome: ${name}`, async () => {
      const fetch = fakeFetch(...script);

      const result = await sendCapture(testEnvelope(), { fetch });

      assert.ok(Object.values(OUTCOME).includes(result.outcome), result.outcome);
    });
  }

  it("resolves without waiting on a timer", async () => {
    /* No backoff, no `setTimeout`, no sleep between attempts: the flow is a
     * straight line of awaited requests. If a delay were ever introduced, this
     * would be the test that noticed. */
    const realSetTimeout = globalThis.setTimeout;
    let scheduled = 0;
    globalThis.setTimeout = (...args) => {
      scheduled += 1;
      return realSetTimeout(...args);
    };
    try {
      const fetch = fakeFetch(networkError(), conflict(), jsonResponse(200, record("complete")));

      await sendCapture(testEnvelope(), { fetch });

      assert.equal(scheduled, 0);
    } finally {
      globalThis.setTimeout = realSetTimeout;
    }
  });

  it("keeps no state between calls", async () => {
    /* Nothing is remembered anywhere — no module-level counter, no
     * `chrome.storage`, no queue. Two identical user actions behave
     * identically, and a service worker that died in between changes nothing. */
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const fetch = fakeFetch(networkError(), jsonResponse(200, createdBody()));

      const result = await sendCapture(testEnvelope(), { fetch });

      assert.equal(result.outcome, OUTCOME.COMPLETE);
      assert.equal(fetch.posts().length, 2);
    }
  });
});

describe("reading a capture directly", () => {
  it("uses GET, with no body", async () => {
    const fetch = fakeFetch(jsonResponse(200, record("complete")));

    await getCapture(SUBMITTED_ID, { fetch });

    assert.equal(fetch.calls[0].options, undefined);
    assert.equal(fetch.posts().length, 0);
  });

  it("escapes the id into the path", async () => {
    const fetch = fakeFetch(jsonResponse(200, record("complete")));

    await getCapture("a/b c", { fetch });

    assert.equal(fetch.calls[0].url, `${CAPTURES_ENDPOINT}/a%2Fb%20c`);
  });

  it("reports a record with no status as a protocol error", async () => {
    const fetch = fakeFetch(jsonResponse(200, { id: SUBMITTED_ID }));

    const result = await getCapture(SUBMITTED_ID, { fetch });

    assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
  });

  it("passes a server error through with its status", async () => {
    const fetch = fakeFetch(jsonResponse(503, errorBody("storage_unavailable", "down")));

    const result = await getCapture(SUBMITTED_ID, { fetch });

    assert.equal(result.outcome, OUTCOME.SERVER_ERROR);
    assert.equal(result.status, 503);
  });

  it("marks a recovery probe as observational", async () => {
    const direct = await getCapture(SUBMITTED_ID, {
      fetch: fakeFetch(jsonResponse(200, record("processing"))),
    });
    const probed = await recoverNetworkOutcome(SUBMITTED_ID, {
      fetch: fakeFetch(jsonResponse(200, record("processing"))),
    });

    assert.equal(direct.probed, undefined);
    assert.equal(probed.probed, true);
  });
});
