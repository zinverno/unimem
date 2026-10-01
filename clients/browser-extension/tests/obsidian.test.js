import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { obsidianClient, deliveryReceipt, deliveryStates } from "../lib/obsidian.js";
import { localTransport } from "../lib/transport.js";
const fixture = JSON.parse(await readFile(new URL("../../../fixtures/obsidian-delivery-v1.json", import.meta.url)));
const d = fixture.delivery;
test("shared delivery receipt projects metadata only; receiver credential never returned", () => {
  assert.deepEqual(Object.keys(deliveryStates), fixture.states);
  const result = deliveryReceipt({ ...d, receiver_token: "secret" }, d.source_capture_id, d.destination_id);
  assert.ok(!JSON.stringify(result).includes("secret")); assert.ok(!Object.hasOwn(result, "markdown"));
  for (const bad of [{ state: "complete" }, { destination_id: "other" }, { source_capture_id: "other" }, { suggested_filename: "../note.md" }]) {
    assert.throws(() => deliveryReceipt({ ...d, ...bad }, d.source_capture_id, d.destination_id));
  }
});
test("same capture/destination submission and background restart use server receipt", async () => {
  let received = null, sends = 0;
  const http = async (url, options) => {
    if (options?.method === "POST") { sends++; received ??= { ...d }; return Response.json(received, { status: sends === 1 ? 202 : 200 }); }
    if (url.endsWith("/v1/destinations")) return Response.json([{ destination_id: d.destination_id, display_name: "Main", receiver_token: "secret" }]);
    return Response.json({ suggested_filename: d.suggested_filename, delivery: received });
  };
  const client = obsidianClient(http);
  assert.equal((await client.status(d.source_capture_id, d.destination_id)).delivery, null);
  const [a,b] = await Promise.all([client.send(d.source_capture_id,d.destination_id),client.send(d.source_capture_id,d.destination_id)]);
  assert.equal(a.delivery_id,b.delivery_id);
  received.state = "imported";
  const restored = obsidianClient(http);
  assert.equal((await restored.status(d.source_capture_id,d.destination_id)).delivery.state,"imported");
  assert.equal(sends,2); // The API's unique key coalesces requests across pages/processes.
  assert.ok(!JSON.stringify(await restored.destinations()).includes("secret"));
});
test("browser transport cannot enter receiver routes or send a receiver credential", async () => {
  const browserToken = "b".repeat(43), calls = [];
  const transport = localTransport({ getToken: async()=>browserToken, fetch:async(url,options)=>{calls.push(options);return Response.json([]);} });
  await obsidianClient(transport).destinations();
  assert.equal(calls[0].headers.Authorization, `Bearer ${browserToken}`);
  await assert.rejects(transport("http://127.0.0.1:8765/v1/receiver/destination"));
  assert.equal(calls.length,1);
});
