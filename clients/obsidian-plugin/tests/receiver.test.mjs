import test from "node:test";
import assert from "node:assert/strict";
import { readFile, mkdtemp, mkdir, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Receiver, loadData, delivery, digest, STATES, FAILURES, UUID } from "../build/engine.js";
import { folder, notePath, rejectSymlinks } from "../build/paths.js";
const fixture = JSON.parse(await readFile(new URL("../../../fixtures/obsidian-delivery-v1.json", import.meta.url)));
const clone = x => structuredClone(x);

function harness() {
  let data = loadData(null), disk;
  data.settings = { ...data.settings, enabled: true, verified: true, token: "a".repeat(43),
    destination_id: fixture.delivery.destination_id, destination_name: "Test" };
  disk = clone(data);
  const h = { files: new Map(), creates: 0, reads: 0, folders: [], calls: [], notices: [],
    d: clone(fixture.delivery), owner: null, active: false, onSave: null, onCreate: null, onClaim: null, onAck: null, onRequest: null,
    mismatch: false, symlink: false, saves: 0 };
  const vault = { configDir: ".obsidian", guard: async () => { if (h.symlink) throw Error("link"); },
    exists: async path => h.files.has(path), mkdir: async path => { h.folders.push(path); },
    read: async path => { h.reads++; if (!h.files.has(path)) throw Error("missing"); return h.files.get(path); },
    create: async (path, text) => { assert.equal(h.files.has(path), false); h.files.set(path, h.mismatch ? text + "changed" : text); h.creates++; await h.onCreate?.(); } };
  const http = async (path, body) => {
    h.calls.push(path); await h.onRequest?.(path);
    if (path.endsWith("/destination")) return { destination_id: h.d.destination_id, display_name: "Test", receiver_id: h.owner };
    if (path.endsWith("/next")) return ["pending", "claimed"].includes(h.d.state) && !h.active ? clone(h.d) : null;
    if (path.endsWith("/claim")) {
      if (h.active) throw Error("lease active");
      h.active = true; h.owner = body.receiver_id;
      h.d.state = "claimed"; h.d.lease_expires_at = new Date(Date.now() + 120000).toISOString();
      await h.onClaim?.(); return clone(h.d);
    }
    if (path.endsWith("/ack")) {
      assert.equal(body.markdown_sha256, h.d.markdown_sha256);
      h.d.state = "imported"; h.d.lease_expires_at = null;
      await h.onAck?.(); return clone(h.d);
    }
    if (path.endsWith("/fail")) {
      h.d.state = body.error_code === "file_exists" ? "conflict" : body.error_code === "write_ambiguous" ? "ambiguous" : "failed";
      h.d.error_code = body.error_code; h.d.lease_expires_at = null; return clone(h.d);
    }
    return clone(h.d);
  };
  h.make = () => {
    data = loadData(clone(disk));
    h.r = new Receiver(data, http, vault, async () => { h.saves++; disk = clone(data); await h.onSave?.(); }, code => h.notices.push(code));
    return h.r;
  };
  h.restart = async () => { await h.r.stop(); h.active = false; h.onSave = h.onCreate = h.onClaim = h.onAck = null; return h.make(); };
  h.path = `${data.settings.inbox}/${h.d.suggested_filename}`;
  h.disk = () => disk;
  h.make(); return h;
}

test("shared Python/TS contract: states, errors, digest, IDs, bounds", () => {
  assert.deepEqual(STATES, fixture.states); assert.deepEqual(FAILURES, fixture.failure_codes);
  assert.deepEqual(delivery(fixture.delivery, fixture.delivery.destination_id), fixture.delivery);
  assert.equal(digest(fixture.delivery.markdown), fixture.delivery.markdown_sha256);
  for (const id of fixture.invalid_ids) assert.equal(UUID.test(id), false, id);
  for (const bad of [{ markdown: "changed" }, { markdown: "x".repeat(1024 * 1024 + 1) }, { source_capture_id: undefined },
    { protocol_version: "2" }, { state: "unknown" }, { error_code: "secret" }, { suggested_filename: "../note.md" }]) {
    assert.throws(() => delivery({ ...fixture.delivery, ...bad }, fixture.delivery.destination_id));
  }
});

test("unavailable attachment capability cannot block an ordinary v1 import", async () => {
  const h = harness(); h.r.data.settings.attachments = true;
  h.onRequest = async path => { if (path.startsWith("/v2/")) throw Error("old server"); };
  await h.r.poll();
  assert.equal(h.d.state, "imported"); assert.equal(h.creates, 1);
  assert.equal(h.calls.some(path => path.startsWith("/v2/")), false);
});
test("read-only connection; disabled and unverified receive do nothing", async () => {
  const h = harness(); h.r.data.settings.enabled = false;
  await h.r.poll(); assert.equal(h.calls.length, 0);
  await h.r.check(); assert.deepEqual(h.calls, ["/v1/receiver/destination"]);
  assert.equal(h.creates, 0); assert.equal(h.folders.length, 0); assert.equal(h.reads, 0);
  h.calls = []; h.r.data.settings.enabled = true; h.r.data.settings.verified = false;
  await h.r.poll(); assert.equal(h.calls.length, 0);
});
test("valid create, folder, exact read-back, ACK, duplicate poll, user-owned file", async () => {
  const h = harness(); await Promise.all([h.r.poll(), h.r.poll(), h.r.poll()]);
  assert.equal(h.creates, 1); assert.deepEqual(h.folders, ["Inbox/UniMem"]);
  assert.equal(h.files.get(h.path), h.d.markdown); assert.equal(h.d.state, "imported");
  assert.equal(h.disk().journal[0].acked, true);
  h.files.set(h.path, "user edit"); await h.r.poll(); await (await h.restart()).poll();
  assert.equal(h.creates, 1); assert.equal(h.files.get(h.path), "user edit");
  h.files.delete(h.path); await h.r.poll(); assert.equal(h.files.size, 0);
});
test("existing file, even identical, is conflict and never read/adopted", async () => {
  const h = harness(); h.files.set(h.path, h.d.markdown); await h.r.poll();
  assert.equal(h.d.state, "conflict"); assert.equal(h.creates, 0); assert.equal(h.reads, 0);
  assert.equal(h.files.get(h.path), fixture.delivery.markdown);
});
test("digest mismatch never ACKs or modifies the written file", async () => {
  const h = harness(); h.mismatch = true; await h.r.poll();
  assert.equal(h.d.state, "failed"); assert.equal(h.d.error_code, "digest_mismatch");
  assert.ok(h.files.get(h.path).endsWith("changed")); assert.ok(!h.calls.some(x => x.endsWith("/ack")));
});
for (const boundary of ["claim-before-journal", "prepared", "create-before-update", "written-before-ack", "ack-response-lost", "ack-before-local-save"]) {
  test(`restart recovery: ${boundary}`, async () => {
    const h = harness();
    if (boundary === "claim-before-journal") h.onClaim = () => { void h.r.stop(); };
    if (boundary === "prepared") h.onSave = () => { if (h.disk().journal[0]?.state === "prepared") void h.r.stop(); };
    if (boundary === "create-before-update") h.onCreate = () => { void h.r.stop(); };
    if (boundary === "written-before-ack") h.onSave = () => { if (h.disk().journal[0]?.state === "written") void h.r.stop(); };
    if (boundary === "ack-response-lost") h.onAck = () => { throw Error("lost response"); };
    if (boundary === "ack-before-local-save") h.onAck = () => { void h.r.stop(); };
    await h.r.poll(); await (await h.restart()).poll();
    assert.equal(h.creates, 1); assert.equal(h.d.state, "imported"); assert.equal(h.disk().journal[0].acked, true);
    await h.r.poll(); assert.equal(h.creates, 1);
  });
}
test("creating intent with no file is ambiguous; deletion is never repaired", async () => {
  const h = harness(); h.onSave = () => { if (h.disk().journal[0]?.state === "creating") void h.r.stop(); };
  await h.r.poll(); await (await h.restart()).poll();
  assert.equal(h.d.state, "ambiguous"); assert.equal(h.creates, 0);
});
test("lost ACK plus edited/deleted file trusts server receipt without touching vault", async () => {
  const h = harness(); h.onAck = () => { throw Error("lost response"); };
  await h.r.poll(); h.files.delete(h.path); const reads = h.reads;
  await (await h.restart()).poll(); assert.equal(h.reads, reads); assert.equal(h.creates, 1);
  assert.equal(h.disk().journal[0].acked, true);
});
test("configuration switch and second installation fail closed", async () => {
  const h = harness(); h.onCreate = () => { void h.r.stop(); };
  await h.r.poll(); await h.restart(); h.r.data.settings.inbox = "NewInbox"; await h.r.poll();
  assert.equal(h.d.error_code, "config_changed"); assert.equal(h.creates, 1);
  const other = harness(); other.owner = fixture.receiver_id; await other.r.poll(); assert.equal(other.creates, 0);
});
test("unload during HTTP prevents create/ACK and future polling", async () => {
  const h = harness(); h.onRequest = () => { void h.r.stop(); };
  await h.r.poll(); await h.r.poll(); assert.equal(h.creates, 0); assert.equal(h.calls.length, 1);
});
test("expired claim is not permission to write; retained intent becomes review", async () => {
  const h = harness(); h.onClaim = () => { h.d.lease_expires_at = new Date(Date.now() + 1000).toISOString(); };
  await h.r.poll(); assert.equal(h.creates, 0); await (await h.restart()).poll();
  assert.equal(h.d.state, "ambiguous");
});
test("server rollback cannot recreate an ACKed delivery", async () => {
  const h = harness(); await h.r.poll(); h.d = clone(fixture.delivery); h.active = false; h.files.clear();
  await h.r.poll(); assert.equal(h.creates, 1); assert.equal(h.r.error, "receipt_mismatch");
});
test("traversal and platform-specific paths rejected independently", () => {
  for (const bad of ["", "/Inbox", "../Inbox", "Inbox/../x", "Inbox//x", ".obsidian", "Inbox/.obsidian", "C:/x", "Inbox\\x", "Inbox\0x", "Inbox/x.", "CON", "Inbox/LPT1", "Inbox/%2e%2e", "Inbox/\u0085x"]) assert.throws(() => folder(bad));
  for (const bad of ["../x.md", "/x.md", "x.md", "unimem-" + "a".repeat(64) + ".md/../x"]) assert.throws(() => notePath("Inbox/UniMem", bad));
  assert.throws(() => folder("config/Inbox", "config"));
});
test("real symlink rejection, corrupt journal refusal, no destructive API", async () => {
  const root = await mkdtemp(join(tmpdir(), "unimem-path-")); await mkdir(join(root, "real"));
  await symlink(join(root, "real"), join(root, "link"));
  await assert.rejects(rejectSymlinks(root, "link/new.md"));
  await rejectSymlinks(root, "real/new.md");
  assert.throws(() => loadData({ version: 1 }));
  const h = harness(); h.symlink = true; await h.r.poll(); assert.equal(h.creates, 0);
  for (const file of ["engine.ts", "main.ts", "client.ts"]) {
    const source = await readFile(new URL(`../src/${file}`, import.meta.url), "utf8");
    assert.doesNotMatch(source, /\.(?:modify|append|delete|rename|trash)\s*\(|console\./);
  }
});
