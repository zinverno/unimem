/**
 * The ambiguous case: a POST that never came back.
 *
 * This is the one place a connector is genuinely tempted to do the wrong thing.
 * The capture id was minted before the request, so the server may hold a
 * finished capture the client never heard about. Retrying would either collide
 * (`409`, on the same id) or silently duplicate the user's capture (a fresh
 * id) — so the client *looks* instead, exactly once, and reports what it found.
 *
 * Every test here therefore asserts two things: what the user is told, and that
 * no second POST happened.
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

describe("a POST that reached the server needs no probe", () => {
  it("does not probe after a successful 201", async () => {
    const fetch = fakeFetch(jsonResponse(201, createdBody()));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.COMPLETE);
    assert.equal(fetch.calls.length, 1);
  });

  for (const status of [409, 422, 500, 503]) {
    it(`does not probe after ${status}`, async () => {
      const fetch = fakeFetch(jsonResponse(status, errorBody("storage_unavailable", "no")));

      await sendCapture(testEnvelope(), { fetch });

      assert.equal(fetch.calls.length, 1);
      assert.equal(fetch.posts().length, 1);
    });
  }
});

describe("a POST that failed at the network layer", () => {
  it("triggers exactly one observational GET", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, record("complete")));

    await sendCapture(testEnvelope(), { fetch });

    assert.equal(fetch.posts().length, 1);
    assert.equal(fetch.gets().length, 1);
    assert.equal(fetch.calls.length, 2);
  });

  it("probes the very id it submitted", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, record("complete")));

    await sendCapture(testEnvelope(), { fetch });

    assert.equal(fetch.calls[1].url, PROBE_URL);
    assert.ok(fetch.calls[1].url.includes(SUBMITTED_ID));
  });

  it("never sends a second POST", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(404, errorBody("not_found", "gone")));

    await sendCapture(testEnvelope(), { fetch });

    assert.equal(fetch.posts().length, 1);
  });

  it("confirms success when the probe finds a complete capture", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(200, record("complete")));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.COMPLETE);
    assert.equal(result.probed, true);
    assert.equal(result.confirmedBy, "probe");
    assert.equal(result.captureId, SUBMITTED_ID);
  });

  for (const status of ["received", "stored", "processing", "failed"]) {
    it(`reports the durable state '${status}' without retrying or mutating it`, async () => {
      const fetch = fakeFetch(networkError(), jsonResponse(200, record(status)));

      const result = await sendCapture(testEnvelope(), { fetch });

      assert.equal(result.outcome, OUTCOME.DURABLE_STATE);
      assert.equal(result.status, status);
      assert.notEqual(result.outcome, OUTCOME.COMPLETE);
      assert.equal(fetch.posts().length, 1);
      assert.equal(fetch.calls.length, 2);
    });
  }

  it("reports unconfirmed when the probe finds nothing", async () => {
    const fetch = fakeFetch(networkError(), jsonResponse(404, errorBody("not_found", "no such")));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.UNCONFIRMED);
    assert.equal(result.probed, true);
    assert.equal(fetch.calls.length, 2);
  });

  it("reports the outcome as unknown when the probe also fails", async () => {
    const fetch = fakeFetch(networkError(), networkError());

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.UNAVAILABLE);
    assert.equal(result.probed, true);
    assert.equal(fetch.calls.length, 2);
  });

  it("probes at most once even when the probe is inconclusive", async () => {
    const fetch = fakeFetch(networkError(), brokenResponse(200));

    const result = await sendCapture(testEnvelope(), { fetch });

    assert.equal(result.outcome, OUTCOME.PROTOCOL_ERROR);
    assert.equal(fetch.calls.length, 2);
  });
});

describe("reading a capture directly", () => {
  it("uses GET, with no body", async () => {
    const fetch = fakeFetch(jsonResponse(200, record("complete")));

    await getCapture(SUBMITTED_ID, { fetch });

    assert.equal(fetch.calls[0].options, undefined);
    assert.equal(fetch.posts().length, 0);
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
    const fetch = fakeFetch(jsonResponse(200, record("processing")));

    const direct = await getCapture(SUBMITTED_ID, { fetch: fakeFetch(jsonResponse(200, record("processing"))) });
    const probed = await recoverNetworkOutcome(SUBMITTED_ID, { fetch });

    assert.equal(direct.probed, undefined);
    assert.equal(probed.probed, true);
  });
});
