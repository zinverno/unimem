import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import Plugin from "../build/main.js";
import { Receiver, loadData } from "../build/engine.js";

const fixture = JSON.parse(await readFile(new URL("../../../fixtures/obsidian-delivery-v1.json", import.meta.url)));
const clone = x => structuredClone(x);

test("real persist: one rejected save does not poison later explicit attempts", async () => {
  const plugin = new Plugin(); plugin.data = loadData(null);
  const failure = Error("temporary storage failure"), calls = [], saved = [], results = [];
  plugin.saveData = async snapshot => {
    calls.push(snapshot.settings.inbox);
    if (calls.length === 1) throw failure;
    saved.push(snapshot.settings.inbox);
  };
  for (const inbox of ["First", "Second", "Third"]) {
    plugin.data.settings.inbox = inbox;
    results.push(await plugin.persist().then(() => "saved", error => error));
  }
  assert.deepEqual(calls, ["First", "Second", "Third"]);
  assert.deepEqual(saved, ["Second", "Third"]);
  assert.deepEqual(results, [failure, "saved", "saved"]);
});

test("real persist: queued snapshots stay serial and each caller gets its own outcome", async () => {
  const plugin = new Plugin(); plugin.data = loadData(null);
  let release, active = 0;
  const gate = new Promise(resolve => { release = resolve; });
  const failure = Error("second save failed"), calls = [], saved = [];
  plugin.saveData = async snapshot => {
    assert.equal(++active, 1);
    try {
      calls.push(snapshot.settings.inbox);
      if (calls.length === 1) await gate;
      if (snapshot.settings.inbox === "Second") throw failure;
      saved.push(snapshot.settings.inbox);
    } finally { active--; }
  };
  const queued = [];
  for (const inbox of ["First", "Second", "Third"]) {
    plugin.data.settings.inbox = inbox; queued.push(plugin.persist());
  }
  const results = Promise.allSettled(queued);
  release();
  assert.deepEqual(await results, [
    { status: "fulfilled", value: undefined }, { status: "rejected", reason: failure },
    { status: "fulfilled", value: undefined },
  ]);
  assert.deepEqual(calls, ["First", "Second", "Third"]);
  assert.deepEqual(saved, ["First", "Third"]);
});

// Exercise the real plugin queue; only the disk, HTTP and Vault boundaries are fake.
function importing() {
  const plugin = new Plugin(); plugin.data = loadData(null);
  plugin.data.settings = { ...plugin.data.settings, enabled: true, verified: true,
    destination_id: fixture.delivery.destination_id, destination_name: "Test", token: "a".repeat(43) };
  const h = { plugin, disk: clone(plugin.data), saves: 0, failSave: 0, creates: 0, reads: 0,
    calls: [], files: new Map(), d: clone(fixture.delivery), active: false, notices: [] };
  plugin.saveData = async snapshot => {
    if (++h.saves === h.failSave) throw Error("temporary save failure");
    h.disk = clone(snapshot);
  };
  h.path = `${plugin.data.settings.inbox}/${h.d.suggested_filename}`;
  plugin.receiver = new Receiver(plugin.data, async (path, body) => {
    h.calls.push(path);
    if (path.endsWith("/destination")) return { destination_id: h.d.destination_id, display_name: "Test", receiver_id: plugin.data.receiver_id };
    if (path.endsWith("/next")) return ["pending", "claimed"].includes(h.d.state) && !h.active ? clone(h.d) : null;
    if (path.endsWith("/claim")) {
      assert.equal(h.active, false); h.active = true;
      h.d.state = "claimed"; h.d.lease_expires_at = new Date(Date.now() + 120000).toISOString();
    }
    if (path.endsWith("/ack")) { h.d.state = "imported"; h.d.lease_expires_at = null; }
    if (path.endsWith("/fail")) {
      h.d.state = body.error_code === "file_exists" ? "conflict" : body.error_code === "write_ambiguous" ? "ambiguous" : "failed";
      h.d.error_code = body.error_code; h.d.lease_expires_at = null;
    }
    return clone(h.d);
  }, { configDir: ".obsidian", guard: async () => {}, mkdir: async () => {},
    exists: async path => { h.onExists?.(path); return h.files.has(path); },
    read: async path => { h.reads++; return h.files.get(path); },
    create: async (path, text) => {
      assert.equal(h.disk.journal[0].state, "creating", "create requires durable intent");
      assert.equal(h.files.has(path), false); h.creates++; h.files.set(path, text);
    },
  }, () => plugin.persist(), code => h.notices.push(code));
  h.retry = async () => {
    h.active = false;
    if (h.d.state === "claimed") h.d.lease_expires_at = new Date(Date.now() - 1000).toISOString();
    await plugin.receiver.poll();
  };
  return h;
}

for (const failSave of [1, 2]) test(`journal save ${failSave} rejected before create: no write or imported ACK`, async () => {
  const h = importing(); h.failSave = failSave;
  await h.plugin.receiver.poll();
  assert.equal(h.creates, 0); assert.ok(!h.calls.some(p => p.endsWith("/ack")));
  assert.deepEqual(h.plugin.data.journal, h.disk.journal);
  if (failSave === 2) h.files.set(h.path, h.d.markdown); // Matching bytes are not durable creating intent.
  await h.retry();
  assert.equal(h.d.state, failSave === 1 ? "imported" : "conflict");
  assert.equal(h.creates, failSave === 1 ? 1 : 0);
});

for (const file of ["unchanged", "edited", "missing"]) test(`post-create save failure recovers from durable intent: ${file}`, async () => {
  const h = importing(); h.failSave = 3;
  await h.plugin.receiver.poll();
  assert.equal(h.creates, 1); assert.equal(h.disk.journal[0].state, "creating");
  assert.deepEqual(h.plugin.data.journal, h.disk.journal);
  assert.ok(!h.calls.some(p => p.endsWith("/ack")));
  if (file === "edited") h.files.set(h.path, "user edit");
  if (file === "missing") h.files.clear();
  await h.retry();
  assert.equal(h.creates, 1);
  assert.equal(h.d.state, file === "unchanged" ? "imported" : file === "edited" ? "failed" : "ambiguous");
  if (file === "edited") assert.equal(h.files.get(h.path), "user edit");
  if (file === "missing") assert.equal(h.files.size, 0);
});

test("accepted ACK plus failed local save uses the same receipt without reading edited file", async () => {
  const h = importing(); h.failSave = 4;
  await h.plugin.receiver.poll();
  assert.equal(h.d.state, "imported"); assert.equal(h.disk.journal[0].acked, false);
  assert.equal(h.plugin.data.journal[0].acked, false); assert.equal(h.plugin.data.lastImport, null);
  const reads = h.reads; h.files.set(h.path, "user edit");
  await h.plugin.receiver.poll();
  assert.equal(h.calls.at(-1), `/v1/receiver/deliveries/${h.d.delivery_id}`);
  assert.equal(h.disk.journal[0].acked, true); assert.equal(h.creates, 1); assert.equal(h.reads, reads);
  assert.equal(h.files.get(h.path), "user edit");
});

test("failed conflict journal save stops the attempt instead of reporting a create failure", async () => {
  const h = importing(); h.failSave = 3;
  let checks = 0;
  h.onExists = path => { if (++checks === 2) h.files.set(path, "outside note"); };
  await h.plugin.receiver.poll();
  assert.equal(h.saves, 3); assert.equal(h.creates, 0);
  assert.equal(h.d.state, "claimed");
  assert.ok(!h.calls.some(p => /\/(ack|fail)$/.test(p)));
  assert.deepEqual(h.plugin.data.journal, h.disk.journal);
  assert.equal(h.files.get(h.path), "outside note");
});

test("failed connection-check save does not publish verified settings", async () => {
  const h = importing(); h.plugin.data.settings.verified = false; h.disk = clone(h.plugin.data);
  h.failSave = 1; await h.plugin.receiver.check();
  assert.equal(h.plugin.data.settings.verified, false); assert.equal(h.creates, 0);
  await h.plugin.receiver.check(); assert.equal(h.disk.settings.verified, true);
});

for (const operation of ["enable", "disable", "configure"]) test(`failed ${operation} save preserves confirmed settings and accepts explicit retry`, async () => {
  const h = importing(), p = h.plugin;
  p.data.settings.enabled = operation === "disable"; h.disk = clone(p.data);
  const before = clone(p.data), old = p.receiver;
  p.app = { vault: { configDir: ".obsidian" } };
  let reconnects = 0, starts = 0;
  p.connect = () => { reconnects++; p.receiver = { stop: async () => {}, start: () => { starts++; } }; };
  const change = () => operation === "configure" ? p.configure({ ...p.data.settings, inbox: "OtherInbox" }) : p.setReceiving(operation === "enable");
  h.failSave = 1; await assert.rejects(change());
  assert.deepEqual(p.data, before); assert.equal(p.receiver, old); assert.equal(reconnects, 0);
  assert.ok(p.settingsError); await old.poll(); assert.equal(h.creates, 0);
  await change();
  assert.deepEqual(p.data, h.disk); assert.equal(p.settingsError, ""); assert.equal(reconnects, 1);
  assert.equal(starts, operation === "enable" ? 1 : 0);
  assert.equal(p.data.receiver_id, before.receiver_id);
});

test("overlapping setting changes cannot overwrite a failed save rollback", async () => {
  const h = importing(), p = h.plugin, before = clone(p.data.settings);
  let enter, reject;
  const entered = new Promise(r => { enter = r; });
  p.saveData = () => { enter(); return new Promise((_, r) => { reject = r; }); };
  const first = p.setReceiving(false);
  const rejected = assert.rejects(first, /disk/);
  await entered;
  await assert.rejects(p.setReceiving(true), /settings_busy/);
  reject(Error("disk")); await rejected;
  assert.deepEqual(p.data.settings, before);
});

for (const enabled of [false, true]) test(`settings toggle handles save rejection and renders confirmed ${enabled}`, async () => {
  const p = new Plugin(), initial = loadData(null);
  initial.settings = { ...initial.settings, token: "r".repeat(43), verified: true, enabled,
    destination_id: fixture.delivery.destination_id, destination_name: "Test" };
  let tab, fail = false, starts = 0;
  p.loadData = async () => clone(initial);
  p.saveData = async () => { if (fail) throw Error("private disk details"); };
  p.app = { workspace: { onLayoutReady: () => {} } };
  p.addCommand = () => {};
  p.addSettingTab = value => { tab = value; };
  p.connect = () => { p.receiver = { stop: async () => {}, start: () => { starts++; }, status: "Подключено" }; };
  await p.onload();
  tab.containerEl = { controls: [], children: [],
    empty() { this.controls = []; this.children = []; },
    createEl(tag, options = {}) {
      const child = { tag, ...options, setText(text) { this.text = text; } };
      this.children.push(child); return child;
    },
  };
  tab.display(); fail = true;
  const toggle = () => tab.containerEl.controls.find(c => c.name === "Принимать материалы");
  await toggle().change(!enabled); // Real settings callback must handle its rejection.
  assert.equal(toggle().value, enabled); assert.equal(p.data.settings.enabled, enabled);
  assert.equal(starts, 0);
  assert.ok(tab.containerEl.children.some(c => c.attr?.role === "alert" && c.text.includes("Приём остановлен")));
  assert.ok(!JSON.stringify(tab.containerEl.children).includes("private disk details"));
  fail = false; await toggle().change(!enabled);
  assert.equal(toggle().value, !enabled); assert.equal(p.settingsError, "");
});
