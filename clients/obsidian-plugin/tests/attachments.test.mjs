import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Receiver, loadData, delivery } from "../build/engine.js";
const fixture = JSON.parse(await readFile(new URL("../../../fixtures/obsidian-delivery-v2.json", import.meta.url)));
const original = new Uint8Array(await readFile(new URL("../../../fixtures/obsidian-delivery-v2.png", import.meta.url)));
const clone = x => structuredClone(x);
function harness() {
  let disk = loadData(null), data;
  disk.settings = { ...disk.settings, enabled: true, verified: true, attachments: true, token: "a".repeat(43),
    destination_id: fixture.delivery.destination_id, inbox: "Deep/Nested/Inbox" };
  const h = { d: clone(fixture.delivery), files: new Map(), writes: [], reads: [], calls: [], saves: 0,
    hooks: {}, failedSave: 0, corrupt: false, shortened: false };
  const hook = async name => { await h.hooks[name]?.(); };
  const vault = { configDir: ".obsidian", guard: async () => hook("guard"), exists: async p => { await hook("exists"); return h.files.has(p); },
    mkdir: async (_, check) => { check(); await hook("mkdir"); check(); },
    read: async p => { h.reads.push(p); await hook("read"); if (!h.files.has(p)) throw Error("missing"); return h.files.get(p); },
    readBinary: async p => { h.reads.push(p); await hook("readBinary"); if (!h.files.has(p)) throw Error("missing"); return clone(h.files.get(p)); },
    create: async (p, value) => { assert.ok(!h.files.has(p)); h.files.set(p, value); h.writes.push(p); await hook("create"); },
    createBinary: async (p, value) => { assert.ok(!h.files.has(p)); h.files.set(p, clone(value)); h.writes.push(p); await hook("createBinary"); },
  };
  const http = async (path, body) => {
    h.calls.push(path);
    if (path.endsWith("/destination")) return { destination_id: h.d.destination_id, display_name: "Test", receiver_id: null };
    if (path.endsWith("/capabilities")) return { protocol_version: "2", attachments: body.enabled };
    if (path === "/v1/receiver/deliveries/next") return null;
    if (path.endsWith("/next")) return ["pending", "claimed"].includes(h.d.state) ? clone(h.d) : null;
    if (path.endsWith("/claim")) {
      h.d.state = "claimed"; h.d.lease_expires_at = new Date(Date.now() + 120000).toISOString();
      await hook("claim"); return clone(h.d);
    }
    if (path.includes("/assets/")) {
      assert.equal(disk.journal.length, 1, "intent must precede download");
      await hook("download");
      return h.shortened ? original.slice(1) : h.corrupt ? new Uint8Array(original.length) : clone(original);
    }
    if (path.endsWith("/ack")) {
      assert.equal(body.package_sha256, h.d.package_sha256); assert.equal(body.markdown_sha256, undefined);
      assert.deepEqual(h.files.get(h.assetPath), original); assert.equal(h.files.get(h.notePath), h.d.markdown);
      h.d.state = "imported"; h.d.lease_expires_at = null; await hook("ack"); return clone(h.d);
    }
    if (path.endsWith("/fail")) {
      h.d.error_code = body.error_code; h.d.lease_expires_at = null;
      h.d.state = body.error_code === "file_exists" ? "conflict" : body.error_code === "write_ambiguous" ? "ambiguous" : "failed";
      return clone(h.d);
    }
    return clone(h.d);
  };
  h.make = () => {
    data = loadData(clone(disk));
    h.r = new Receiver(data, http, vault, async () => {
      h.saves++;
      if (h.failedSave === h.saves) throw Error("disk unavailable");
      disk = clone(data); await hook(`save${h.saves}`);
    }, () => {});
    return h.r;
  };
  h.restart = async () => { await h.r.stop(); h.hooks = {}; h.failedSave = 0; return h.make(); };
  h.notePath = disk.settings.inbox + "/" + h.d.suggested_filename;
  h.assetPath = disk.settings.inbox + "/" + h.d.attachments[0].relative_name;
  h.disk = () => disk; h.make(); return h;
}

test("v2 frozen cross-language fixture and strict manifest", () => {
  const d = fixture.delivery;
  assert.deepEqual(delivery(d, d.destination_id), d);
  for (const change of [{ protocol_version: "1" }, { attachments: [] }, { attachments: [...d.attachments, ...d.attachments] },
    { package_sha256: "0".repeat(64) }, { attachments: [{ ...d.attachments[0], relative_name: "../x.png" }] },
    { attachments: [{ ...d.attachments[0], size_bytes: 17000000 }] }, { unexpected: true }]) {
    assert.throws(() => delivery({ ...d, ...change }, d.destination_id));
  }
});
test("v1 settings migrate with attachment permission off; existing journals preserved", () => {
  const old = loadData(null); old.version = 1; delete old.settings.attachments;
  const migrated = loadData(old);
  assert.equal(migrated.version, 2); assert.equal(migrated.settings.attachments, false); assert.deepEqual(migrated.journal, old.journal);
});
test("two files, binary first, exact verification, package ACK, no restoration of user files", async () => {
  const h = harness(); await h.r.poll();
  assert.deepEqual(h.writes, [h.assetPath, h.notePath]); assert.equal(h.d.state, "imported");
  assert.equal(h.disk().journal[0].attachment.state, "written"); assert.equal(h.disk().journal[0].acked, true);
  h.files.set(h.notePath, "user text"); h.files.delete(h.assetPath); const reads = h.reads.length;
  await h.r.poll(); await (await h.restart()).poll();
  assert.equal(h.writes.length, 2); assert.equal(h.reads.length, reads); assert.equal(h.files.get(h.notePath), "user text");
});
for (const target of ["notePath", "assetPath"]) test(`pre-existing ${target} conflicts even with identical bytes`, async () => {
  const h = harness(); h.files.set(h[target], target === "notePath" ? h.d.markdown : original);
  await h.r.poll(); assert.equal(h.d.state, "conflict"); assert.equal(h.writes.length, 0); assert.equal(h.reads.length, 0);
});
for (const boundary of ["claim", "download", "createBinary", "save3", "create", "save5", "ack"]) test(`crash/restart at ${boundary}`, async () => {
  const h = harness(); h.hooks[boundary] = () => { void h.r.stop(); };
  await h.r.poll(); await (await h.restart()).poll();
  assert.equal(h.d.state, "imported"); assert.deepEqual(h.writes, [h.assetPath, h.notePath]);
});
for (const boundary of [1, 2, 3, 4, 5, 6]) test(`failed journal save ${boundary} rolls back unconfirmed state`, async () => {
  const h = harness(); h.failedSave = boundary;
  await h.r.poll();
  assert.deepEqual(h.r.data, h.disk());
  if (boundary < 3) assert.equal(h.writes.length, 0);
  if (boundary === 4) {
    // Creating intent was not durably saved: prepared absent note is safe to create.
    assert.equal(h.writes.length, 1);
  }
  await (await h.restart()).poll();
  assert.equal(h.d.state, "imported"); assert.deepEqual(h.writes, [h.assetPath, h.notePath]);
});
for (const field of ["corrupt", "shortened"]) test(`${field} transmission cannot create either file or ACK`, async () => {
  const h = harness(); h[field] = true; await h.r.poll();
  assert.equal(h.d.state, "failed"); assert.equal(h.d.error_code, "digest_mismatch"); assert.equal(h.writes.length, 0);
});
for (const change of ["missing", "changed"]) test(`own attachment ${change} after interrupted create is never replaced`, async () => {
  const h = harness(); h.hooks.createBinary = () => { void h.r.stop(); };
  await h.r.poll();
  if (change === "missing") h.files.delete(h.assetPath); else h.files.set(h.assetPath, new Uint8Array([1]));
  await (await h.restart()).poll(); assert.notEqual(h.d.state, "imported"); assert.equal(h.writes.length, 1);
});
test("attachment disappearing while note is created cannot produce imported", async () => {
  const h = harness(); h.hooks.create = () => { h.files.delete(h.assetPath); };
  await h.r.poll(); assert.equal(h.d.state, "ambiguous"); assert.equal(h.writes.length, 2);
});
test("lost accepted ACK response trusts receipt without re-reading edited files", async () => {
  const h = harness(); h.hooks.ack = () => { throw Error("response lost"); };
  await h.r.poll(); h.files.clear(); const reads = h.reads.length;
  await (await h.restart()).poll(); assert.equal(h.reads.length, reads); assert.equal(h.writes.length, 2); assert.equal(h.disk().journal[0].acked, true);
});
for (const boundary of ["guard", "exists", "mkdir", "download", "createBinary", "readBinary", "create", "read", "save1", "save2", "save3", "save4", "save5"]) {
  test(`settings change after ${boundary} fences subsequent writes and ACK`, async () => {
    const h = harness(); h.hooks[boundary] = () => { h.r.data.settings.inbox = "Changed"; };
    await h.r.poll(); assert.notEqual(h.d.state, "imported");
    assert.equal(h.calls.some(p => p.endsWith("/ack")), false);
    if (!["create", "read", "save5"].includes(boundary)) assert.ok(h.writes.length <= 1);
  });
}
test("lease expiring during attachment download prevents file writes", async () => {
  const h = harness(), now = Date.now;
  h.hooks.download = () => { Date.now = () => now() + 180000; };
  try { await h.r.poll(); } finally { Date.now = now; }
  assert.equal(h.writes.length, 0); assert.notEqual(h.d.state, "imported");
});

test("attachment mismatch with durable journal cannot ACK another manifest", async () => {
  const h = harness(); h.hooks.createBinary = () => { void h.r.stop(); };
  await h.r.poll(); h.disk().journal[0].attachment.sha256 = "0".repeat(64);
  await (await h.restart()).poll(); assert.equal(h.r.error, "journal_corrupt"); assert.equal(h.writes.length, 1);
});
