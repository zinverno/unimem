import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { receiverClient, serverAddress } from "../build/client.js";
test("only loopback and receiver endpoints", async () => {
  for(const value of ['https://example.org','http://127.0.0.1@evil.test','http://localhost/path','http://localhost/?token=x']) assert.throws(()=>serverAddress(value));
  const client=receiverClient('http://127.0.0.1:8765','r'.repeat(43));
  await assert.rejects(client('/v1/captures'));
});
test("HTTP auth, abort, offline, safe errors and no redirect forwarding", async () => {
  let mode='ok',calls=0;
  const server=createServer((req,res)=>{
    calls++;assert.equal(req.headers.authorization,`Bearer ${'r'.repeat(43)}`);
    if(mode==='wait') return;
    res.setHeader('content-type','application/json');
    if(mode==='ok') res.end('{}');
    else if(mode==='redirect'){res.writeHead(302,{location:'http://example.invalid/secret'});res.end('{}');}
    else {res.statusCode=mode==='unauthorized'?401:500;res.end(JSON.stringify({error:{code:'SECRET and local path'}}));}
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const client=receiverClient(`http://127.0.0.1:${server.address().port}`,'r'.repeat(43));
  try{
    assert.deepEqual(await client('/v1/receiver/destination'),{});
    for(const value of ['redirect','unauthorized','other']){mode=value;await assert.rejects(client('/v1/receiver/destination'),e=>!e.message.includes('SECRET'));}
    assert.equal(calls,4);mode='wait';const control=new AbortController();
    const pending=client('/v1/receiver/destination',undefined,control.signal);control.abort();await assert.rejects(pending,/stopped/);
  }finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
  await assert.rejects(client('/v1/receiver/destination'),/unavailable/);
});
test("v2 binary response is separately bounded; never JSON/base64 and no redirects", async () => {
  let mode = "ok";
  const server = createServer((req, res) => {
    if (mode === "redirect") { res.writeHead(302, { location: "http://example.invalid/file" }); res.end("{}"); return; }
    res.setHeader("content-type", mode === "mime" ? "image/svg+xml" : "image/png");
    if (mode === "large") { res.end(Buffer.alloc(16 * 1024 * 1024 + 1)); return; }
    res.end(Buffer.from([1,2,3]));
  });
  await new Promise(r => server.listen(0, "127.0.0.1", r));
  const client = receiverClient(`http://127.0.0.1:${server.address().port}`, "r".repeat(43));
  const path = "/v2/receiver/deliveries/11111111-1111-4111-8111-111111111111/assets/asset";
  try {
    assert.deepEqual(await client(path), new Uint8Array([1,2,3]));
    for (mode of ["mime", "large", "redirect"]) await assert.rejects(client(path));
    await assert.rejects(client(path + "/../../raw"));
  } finally { server.closeAllConnections(); await new Promise(r => server.close(r)); }
});
