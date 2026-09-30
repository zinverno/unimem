import test from "node:test";
import assert from "node:assert/strict";
import Plugin from "../build/main.js";
import { loadData } from "../build/engine.js";

for (const operation of ["enable", "configure"]) test(`unload while settings persist: ${operation} cannot restart receiving`, async () => {
  const plugin = new Plugin(); plugin.data = loadData(null); plugin.data.settings.verified = true;
  let released, entered, starts = 0;
  const saving = new Promise(r => { entered = r; });
  plugin.saveData = async () => { entered(); await new Promise(r => { released = r; }); };
  const old = { stop: async () => {}, start: () => { starts++; } };
  plugin.receiver = old;
  plugin.app = { vault: { configDir: ".obsidian" } };
  const task = operation === "enable" ? plugin.setReceiving(true) :
    plugin.configure({ ...plugin.data.settings, token: "r".repeat(43) });
  await saving; plugin.onunload(); released(); await task;
  assert.equal(plugin.receiver, old); assert.equal(starts, 0);
});
