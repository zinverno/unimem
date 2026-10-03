import { test } from "node:test";
import assert from "node:assert/strict";
import { videoJobs, videoClient, validateVideo, VIDEO_KEY } from "../lib/video.js";
import { localTransport, ClientError } from "../lib/transport.js";
import { API_ORIGIN } from "../lib/api.js";
import { obsidianClient } from "../lib/obsidian.js";
const now = "2026-10-03T00:00:00Z", file_ref = `sha256:${"a".repeat(64)}`;
const selection = { speech: true, language: "en", frames: true, describe: true,
  decoder: "mp4-h264-aac/1", sampling: "quarters-first-pts/1", asr_profile: "asr-pinned", vision_profile: "vision-pinned" };
function receipt(request, state = "queued") {
  return { ...request, kind: "video-notes", state, stage: state === "running" ? "frame_2" : "waiting",
    accepted_at: now, updated_at: now, started_at: state === "queued" ? null : now, finished_at: state === "complete" ? now : null,
    capture_id: state === "complete" ? "capture" : null, content_id: state === "complete" ? "content" : null,
    error_code: null, result_available: state === "complete" };
}
function harness() {
  let data = {}, accepted = null, uploads = 0, posts = 0;
  const local = { get: async key => structuredClone({ [key]: data[key] }), set: async value => { data = structuredClone(value); } };
  const client = { upload: async () => { uploads++; return file_ref; },
    submit: async request => { posts++; assert.equal(data[VIDEO_KEY][0].file_ref, file_ref); accepted = receipt(request); return accepted; },
    status: async () => { if (!accepted) throw new ClientError("operation_not_found"); return accepted; } };
  const options = { local, client, lock: fn => fn(), newId: () => "video-one", now: () => now };
  return { options, client, jobs: videoJobs(options), data: () => data, counts: () => [uploads, posts] };
}
test("video freezes modes/profiles before upload, restores one ID and never resubmits accepted work", async () => {
  const h = harness(), chosen = { ...selection }, promise = h.jobs.start(new Blob(["video"]), chosen);
  chosen.language = "ru"; chosen.describe = false;
  const j = await promise;
  assert.equal(j.language, "en"); assert.equal(j.describe, true); assert.equal(j.accepted, true);
  await videoJobs(h.options).refresh(j.operation_id, true);
  assert.deepEqual(h.counts(), [1, 1]);
  assert.equal(h.data()[VIDEO_KEY][0].vision_profile, "vision-pinned");
  assert.equal(JSON.stringify(h.data()).includes('"markdown"'), false);
});
test("video unknown acceptance retries exactly the same mode/profile identity without upload", async () => {
  const h = harness(), requests = [];
  h.client.submit = async r => { requests.push(r); throw new ClientError("timeout"); };
  const j = await h.jobs.start(new Blob(["video"]), selection);
  await videoJobs(h.options).refresh(j.operation_id, true);
  assert.deepEqual(requests[0], requests[1]); assert.equal(h.counts()[0], 1);
});
test("video rejects impossible selections and mismatched responses", () => {
  const h = harness();
  for (const change of [{ frames: false }, { asr_profile: null }, { vision_profile: null }, { language: "xx" }])
    assert.throws(() => h.jobs.start(new Blob(["video"]), { ...selection, ...change }), /invalid_message/);
  const request = { ...selection, operation_id: "video-one", file_ref, captured_at: now, declared_mime: "video/mp4" };
  assert.equal(validateVideo(receipt(request, "running"), request).stage, "frame_2");
  for (const change of [{ language: "ru" }, { decoder: "other" }, { sampling: "other" }, { speech: false }, { vision_profile: "other" }, { stage: "99%" }, { content_id: null }])
    assert.throws(() => validateVideo({ ...receipt(request, "complete"), ...change }, request), /invalid_response/);
});
test("video and v3 use protected fixed-origin routes; receiver routes and arbitrary inputs stay forbidden", async () => {
  const paths = [];
  const http = localTransport({ getToken: async () => "a".repeat(43), fetch: async (url, opts) => {
    paths.push(url); assert.equal(opts.redirect, "error"); assert.equal(opts.credentials, "omit");
    return new Response(JSON.stringify({ ready: true, asr: true, vision: true, ...selection }), { headers: { "Content-Type": "application/json" } });
  } });
  await videoClient(http).capabilities();
  await http(`${API_ORIGIN}/v1/video/operations/video-one/frames/frame-1`);
  await http(`${API_ORIGIN}/v3/deliveries`);
  for (const path of ["/v3/receiver/deliveries/next", "/v1/video/operations/../etc/passwd", "/v1/video/url", "/v1/video/operations/video-one?token=x"])
    await assert.rejects(() => http(API_ORIGIN + path), /invalid_destination/);
  assert.equal(paths.length, 3);
});
test("v3 browser receipt never accepts first-frame-only v2 as a completed package", async () => {
  const { readFile } = await import("node:fs/promises");
  const fixture = JSON.parse(await readFile(new URL("../../../fixtures/obsidian-delivery-v3.json", import.meta.url))).delivery;
  const http = async () => new Response(JSON.stringify(fixture), { headers: { "Content-Type": "application/json" } });
  assert.equal((await obsidianClient(http, "3").send(fixture.source_capture_id, fixture.destination_id)).delivery_id, fixture.delivery_id);
  await assert.rejects(() => obsidianClient(http, "2").send(fixture.source_capture_id, fixture.destination_id), /invalid_response/);
});
