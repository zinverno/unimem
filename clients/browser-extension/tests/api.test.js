/**
 * The HTTP client: one request, checked answers, and no retry anywhere.
 *
 * Two families of assertion run through this file. The first is that a success
 * is only ever reported when the server actually said so, in the shape the
 * contract promises — a `201` whose body disagrees with the request is a
 * protocol failure, not a save. The second is that the request count is exactly
 * what the design allows, because the server has no idempotency and a second
 * POST is a duplicate capture or a spurious conflict.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { API_ORIGIN, CAPTURES_ENDPOINT, captureUrl, submitCapture } from "../lib/api.js";
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

describe("the destination", () => {
  it("is the documented local default", () => {
    assert.equal(API_ORIGIN, "http://127.0.0.1:8765");
    assert.equal(CAPTURES_ENDPOINT, "http://127.0.0.1:8765/v1/captures");
  });

  it("is where the POST actually goes", async () => {
    const fetch = fakeFetch(jsonResponse(201, createdBody()));

    await submitCapture(testEnvelope(), { fetch });

    assert.equal(fetch.calls[0].url, "http://127.0.0.1:8765/v1/captures");
  });

  it("never comes from the page under capture", async () => {
    const fetch = fakeFetch(jsonResponse(201, createdBody()));
    const envelope = testEnvelope();
    envelope.source.url = "https://evil.example/collect";
    envelope.payload.text = "http://attacker.invalid/steal";

    await submitCapture(envelope, { fetch });

    assert.equal(fetch.calls[0].url, CAPTURES_ENDPOINT);
    assert.equal(fetch.calls.length, 1);
  });

  it("escapes a capture id into the read path", () => {
    assert.equal(captureUrl("a/b c"), `${CAPTURES_ENDPOINT}/a%2Fb%20c`);
  });
});

describe("the request", () => {
  it("is a single POST", async () => {
    const fetch = fakeFetch(jsonResponse(201, createdBody()));

    await submitCapture(testEnvelope(), { fetch });

    assert.equal(fetch.posts().length, 1);
    assert.equal(fetch.calls.length, 1);
  });

  it("declares JSON", async () => {
    const fetch = fakeFetch(jsonResponse(201, createdBody()));

    await submitCapture(testEnvelope(), { fetch });

    assert.deepEqual(fetch.calls[0].options.headers, { "Content-Type": "application/json" });
  });

  it("sends the envelope unchanged", async () => {
    const fetch = fakeFetch(jsonResponse(201, createdBody()));
    const envelope = testEnvelope();

    await submitCapture(envelope, { fetch });

    assert.deepEqual(JSON.parse(fetch.calls[0].options.body), envelope);
  });

  it("does not rewrite the selected text on the way out", async () => {
    const fetch = fakeFetch(jsonResponse(201, createdBody()));
    const text = "  Å\r\n\tkept  ";

    await submitCapture(testEnvelope({ payload: { type: "text", text } }), { fetch });

    assert.equal(JSON.parse(fetch.calls[0].options.body).payload.text, text);
  });
});

describe("a valid 201", () => {
  it("is success", async () => {
    const fetch = fakeFetch(jsonResponse(201, createdBody()));

    const result = await submitCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.COMPLETE);
    assert.equal(result.captureId, SUBMITTED_ID);
    assert.equal(result.contentId, "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9");
    assert.equal(result.confirmedBy, "post");
  });
});

describe("a response that is not the contract's success", () => {
  it("rejects 200, which this contract never promises", async () => {
    const fetch = fakeFetch(jsonResponse(200, createdBody()));

    const result = await submitCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
  });

  for (const status of [202, 204, 301]) {
    it(`rejects ${status}`, async () => {
      const fetch = fakeFetch(jsonResponse(status, createdBody()));

      const result = await submitCapture(testEnvelope(), { fetch });

      assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
    });
  }

  it("rejects a 201 whose body is not JSON", async () => {
    const fetch = fakeFetch(brokenResponse(201));

    const result = await submitCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
  });

  it("rejects a 201 whose body is not an object", async () => {
    const fetch = fakeFetch(jsonResponse(201, "created"));

    const result = await submitCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
  });

  it("rejects a 201 naming a different capture", async () => {
    const fetch = fakeFetch(jsonResponse(201, createdBody({ capture_id: "someone-elses-id" })));

    const result = await submitCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
  });

  for (const [label, content_id] of [
    ["blank", ""],
    ["whitespace", "   "],
    ["missing", undefined],
    ["not a string", 7],
  ]) {
    it(`rejects a 201 whose content id is ${label}`, async () => {
      const fetch = fakeFetch(jsonResponse(201, createdBody({ content_id })));

      const result = await submitCapture(testEnvelope(), { fetch });

      assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
    });
  }

  for (const status of ["processing", "stored", "failed", "", undefined]) {
    it(`rejects a 201 reporting status ${JSON.stringify(status)}`, async () => {
      const fetch = fakeFetch(jsonResponse(201, createdBody({ status })));

      const result = await submitCapture(testEnvelope(), { fetch });

      assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
    });
  }
});

describe("deliberate server errors", () => {
  const cases = [
    [409, "capture_already_exists", "capture record 'x' is already stored"],
    [422, "unsupported_payload", "this phase accepts inline text only"],
    [422, "processing_failed", "the raw object is empty"],
    [500, "data_integrity_error", "stored data could not be read back"],
    [503, "storage_unavailable", "the capture store is currently unavailable"],
  ];

  for (const [status, code, message] of cases) {
    it(`reports ${status} ${code} as failure`, async () => {
      const fetch = fakeFetch(jsonResponse(status, errorBody(code, message)));

      const result = await submitCapture(testEnvelope(), { fetch });

      assert.equal(result.outcome, OUTCOME.SERVER_ERROR);
      assert.equal(result.status, status);
    });

    it(`retains the typed code and message for ${status} ${code}`, async () => {
      const fetch = fakeFetch(jsonResponse(status, errorBody(code, message)));

      const result = await submitCapture(testEnvelope(), { fetch });

      assert.equal(result.code, code);
      assert.equal(result.message, message);
    });

    it(`does not retry after ${status}`, async () => {
      const fetch = fakeFetch(jsonResponse(status, errorBody(code, message)));

      await submitCapture(testEnvelope(), { fetch });

      assert.equal(fetch.posts().length, 1);
      assert.equal(fetch.calls.length, 1);
    });
  }

  it("never turns 409 into success", async () => {
    const fetch = fakeFetch(jsonResponse(409, errorBody("capture_already_exists", "taken")));

    const result = await submitCapture(testEnvelope(), { fetch });

    assert.notEqual(result.outcome, OUTCOME.COMPLETE);
  });

  for (const [label, body] of [
    ["not JSON at all", undefined],
    ["an empty object", {}],
    ["error as a string", { error: "boom" }],
    ["error as null", { error: null }],
    ["a code that is not a string", { error: { code: 500, message: 1 } }],
  ]) {
    it(`stays a safe failure when the error body is ${label}`, async () => {
      const response = body === undefined ? brokenResponse(503) : jsonResponse(503, body);
      const fetch = fakeFetch(response);

      const result = await submitCapture(testEnvelope(), { fetch });

      assert.equal(result.outcome, OUTCOME.SERVER_ERROR);
      assert.equal(result.status, 503);
      assert.equal(typeof result.code === "string" || result.code === null, true);
    });
  }
});

describe("a network-layer failure", () => {
  it("is reported as unavailable rather than thrown", async () => {
    const fetch = fakeFetch(networkError());

    const result = await submitCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.UNAVAILABLE);
  });

  it("does not itself retry the POST", async () => {
    const fetch = fakeFetch(networkError());

    await submitCapture(testEnvelope(), { fetch });

    assert.equal(fetch.posts().length, 1);
  });
});
