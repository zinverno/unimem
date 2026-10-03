import { test } from "node:test";
import assert from "node:assert/strict";
import { imageJobs, imageClient, validateImage, IMAGE_KEY, previewMime } from "../lib/image.js";
import { ClientError, localTransport } from "../lib/transport.js";
import { API_ORIGIN } from "../lib/api.js";

const fileRef = `sha256:${"a".repeat(64)}`;
const date = "2026-10-03T00:00:00Z";
function receipt(request, state = "queued") {
  const terminal = ["complete", "failed", "interrupted"].includes(state);
  return { ...request, kind: "image-capture", state, accepted_at: date, updated_at: date,
    started_at: state === "queued" ? null : date, finished_at: terminal ? date : null,
    capture_id: state === "complete" ? "capture-image" : null, content_id: state === "complete" ? "content-image" : null,
    error_code: ["failed", "interrupted"].includes(state) ? "decode_failed" : null, result_available: state === "complete" };
}
function harness() {
  let data = {}, saved = null, uploads = 0, posts = 0, gets = 0;
  const local = { get: async key => ({ [key]: structuredClone(data[key]) }), set: async value => { data = structuredClone({ ...data, ...value }); } };
  const client = {
    upload: async () => { uploads++; return fileRef; },
    submit: async request => { posts++; saved = receipt(request); return saved; },
    status: async () => { gets++; if (!saved) throw new ClientError("operation_not_found", 404); return saved; },
  };
  const options = { local, client, lock: fn => fn(), newId: () => "image-id", now: () => date };
  return { local, client, options, jobs: imageJobs(options), counts: () => ({ uploads, posts, gets }),
    stored: () => data, forgetServer: () => { saved = null; } };
}

test("upload then durable reference then submit; reload and poll do not resubmit or upload", async () => {
  const h = harness(), phases = [];
  const original = h.client.submit;
  h.client.submit = async request => {
    assert.equal(h.stored()[IMAGE_KEY][0].file_ref, fileRef);
    assert.equal(h.stored()[IMAGE_KEY][0].accepted, false);
    return original(request);
  };
  const file = new Blob(["sample image"], { type: "image/ogg" });
  const job = await h.jobs.start(file, "ocr", value => phases.push(value));
  assert.equal(job.accepted, true);
  assert.deepEqual(phases, ["uploading", "accepting"]);
  const restored = imageJobs(h.options);
  await restored.refresh(job.operation_id);
  await restored.refresh(job.operation_id, true);
  assert.deepEqual(h.counts(), { uploads: 1, posts: 1, gets: 2 });
  assert.equal(JSON.stringify(h.stored()).includes("sample image"), false);
  h.forgetServer();
  await restored.refresh(job.operation_id, true);
  assert.equal(h.counts().posts, 1);
  assert.equal((await restored.list())[0].accepted, true);
});

test("interrupted upload never becomes accepted and cannot be retried without the file", async () => {
  const h = harness();
  h.client.upload = async () => { throw new ClientError("unavailable"); };
  const job = await h.jobs.start(new Blob(["data"]), "original");
  assert.equal(job.file_ref, null); assert.equal(job.accepted, false);
  await imageJobs(h.options).refresh(job.operation_id, true);
  assert.equal(h.counts().posts, 0); assert.equal(h.counts().gets, 0);
});

test("lost acceptance response resolves through GET; explicit missing replay keeps all parameters", async () => {
  const h = harness(), requests = [];
  h.client.submit = async request => { requests.push(request); throw new ClientError("timeout"); };
  const job = await h.jobs.start(new Blob(["data"]), "original");
  assert.equal(job.accepted, false);
  await imageJobs(h.options).refresh(job.operation_id, true);
  assert.deepEqual(requests[0], requests[1]);
  assert.equal(requests[0].mode, "original");
  assert.equal(h.counts().uploads, 1);
});

test("missing credentials/upload error retains draft; local persistence failure prevents POST", async () => {
  const h = harness();
  h.local.set = async () => { throw new Error("storage unavailable"); };
  await assert.rejects(() => imageJobs(h.options).start(new Blob(["data"]), "ocr"));
  assert.deepEqual(h.counts(), { uploads: 0, posts: 0, gets: 0 });
  assert.throws(() => h.jobs.start(new Blob(), "ocr"), /input_size_limit/);
  assert.throws(() => h.jobs.start(new Blob(["x"]), "other"), /invalid_message/);
});

test("image responses must agree with saved identity, parameters and completed evidence", () => {
  const request = { operation_id: "id", file_ref: fileRef, mode: "ocr", declared_mime: "", captured_at: date };
  const valid = receipt(request, "complete");
  assert.equal(validateImage({ ...valid, secret: "not persisted" }, request).secret, undefined);
  for (const change of [{ kind: "youtube" }, { file_ref: `sha256:${"b".repeat(64)}` }, { mode: "original" },
     { capture_id: null }, { result_available: false }, { finished_at: null }, { started_at: null }]) {
    assert.throws(() => validateImage({ ...valid, ...change }, request), /invalid_response/);
  }
});

test("binary upload is FormData with browser boundary and protected fixed-origin transport", async () => {
  let sent;
  const http = localTransport({ getToken: async () => "a".repeat(43), fetch: async (url, options) => {
    sent = { url, ...options };
    return new Response(JSON.stringify({ file_ref: fileRef, sha256: "a".repeat(64) }), { headers: { "Content-Type": "application/json" } });
  } });
  assert.equal(await imageClient(http).upload(new Blob(["image bytes"])), fileRef);
  assert.equal(sent.url, `${API_ORIGIN}/v1/uploads`);
  assert.equal(sent.headers["Content-Type"], undefined);
  assert.match(sent.headers.Authorization, /^Bearer /);
  assert.equal(sent.redirect, "error"); assert.equal(sent.credentials, "omit");
  assert.equal(await sent.body.get("file").text(), "image bytes");
  await assert.rejects(() => http("http://evil.test/v1/uploads"), /invalid_destination/);
});

test("preview checks content signature and decoded dimensions before Blob URL", async () => {
  const { readFile } = await import("node:fs/promises");
  const png = await readFile(new URL("../../../fixtures/obsidian-delivery-v2.png", import.meta.url));
  assert.equal(await previewMime(new Blob([png])), "image/png");
  await assert.rejects(() => previewMime(new Blob([png], { type: "image/jpeg" })), /mime_mismatch/);
  await assert.rejects(() => previewMime(new Blob(["GIF89a"])), /unsupported_format/);
  const bomb = new Uint8Array(png); new DataView(bomb.buffer).setUint32(16, 20000000);
  await assert.rejects(() => previewMime(new Blob([bomb])), /pixel_limit/);
});

test("description is opt-in and durable reload never resubmits recognition", async () => {
  const h = harness();
  const j = await h.jobs.start(new Blob(["pixels"]), "describe");
  assert.equal(j.mode, "describe");
  await imageJobs(h.options).refresh(j.operation_id);
  assert.equal((await imageJobs(h.options).list())[0].mode, "describe");
  assert.deepEqual(h.counts(), { uploads: 1, posts: 1, gets: 1 });
});

test("description readiness comes only from the fixed protected API and never prepares weights", async () => {
  const calls = [];
  const client = imageClient(localTransport({ getToken: async () => "a".repeat(43), fetch: async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify({ description: { ready: true, code: "ready", model: "Qwen/Qwen3-VL-2B-Instruct-GGUF" } }), { headers: { "Content-Type": "application/json" } });
  } }));
  assert.equal((await client.capabilities()).ready, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `${API_ORIGIN}/v1/image/capabilities`);
  assert.equal(calls[0].options.headers.Authorization, `Bearer ${"a".repeat(43)}`);
});
