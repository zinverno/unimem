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
