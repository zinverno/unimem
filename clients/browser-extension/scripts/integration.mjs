// Invoked by the Python TCP test. No browser/add-on runtime claim.
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { settingsStore } from '../lib/settings.js';
import { localTransport } from '../lib/transport.js';
import { youtubeClient } from '../lib/youtube.js';
import { jobsController } from '../lib/jobs.js';
import { sendCapture } from '../lib/api.js';
import { buildCaptureEnvelope, buildWebpageCaptureEnvelope } from '../lib/envelope.js';
const [phase,file] = process.argv.slice(2);
const local={async get(){try{return JSON.parse(await readFile(file,'utf8'));}catch{return {}; }},async set(value){await writeFile(file,JSON.stringify({...await this.get(),...value}));},async remove(key){const d=await this.get();delete d[key];await writeFile(file,JSON.stringify(d));}};
let credential;
const session={get:async()=>({credential}),set:async(v)=>{credential=v.credential;},remove:async()=>{credential=undefined;}};
const settings=settingsStore({local,session}); await settings.save(process.env.UNIMEM_TEST_TOKEN,false,'ru,en');
let posts=0,gets=0,dropPost=phase==='submit',dropGet=false;
const transport=localTransport({getToken:()=>settings.getToken(),fetch:async(url,options)=>{
 if(options.method==='POST') posts++;else gets++;
 const response=await fetch(url,options);
 if(url.endsWith('/v1/youtube/operations') && dropPost){dropPost=false;dropGet=true;await response.arrayBuffer();throw Error('lost POST response');}
 if(url.includes('/v1/youtube/operations/') && dropGet){dropGet=false;await response.arrayBuffer();throw Error('lost GET response');}
 return response;
}});
const client=youtubeClient(transport);
let jobs=jobsController({local,client,settings});
assert.deepEqual(await client.check(),{server:true,token:true,youtube:true,error:null});
if(phase==='submit'){
 for(const whole of [false,true]){
  const envelope=(whole?buildWebpageCaptureEnvelope:buildCaptureEnvelope)({id:crypto.randomUUID(),selection:'  browser text 😺  ',html:'<html><body><p>Browser page 😺</p><script>never execute</script></body></html>',url:'https://example.org/source',title:'Synthetic',capturedAt:new Date().toISOString()});
  const result=await sendCapture(envelope,{fetch:transport});assert.equal(result.outcome,'complete');assert.equal(result.confirmedBy,'post');
 }
 const job=await jobs.choose('https://youtu.be/abcdefghijk?t=1');
 const [a,b]=await Promise.all([jobs.send(job.operation_id),jobs.send(job.operation_id)]);
 assert.equal(a.operation_id,b.operation_id);assert.equal(a.accepted,false);
 // Simulates a destroyed/recreated background with only storage surviving.
 jobs=jobsController({local,client,settings});
 const retry=await jobs.retry(job.operation_id);assert.equal(retry.accepted,true);
 // Explicit same-ID replay exercises real B1's 200, never a second acquisition.
 const replay=await client.submit({operation_id:job.operation_id,url:job.url,languages:job.languages});assert.equal(replay.operation_id,job.operation_id);
 await assert.rejects(client.submit({operation_id:job.operation_id,url:job.url,languages:['en','ru']}),/operation_conflict/);
 assert.equal(posts,5); // 2 old captures + 1 YouTube submit + 1 replay + 1 conflict.
 console.log(JSON.stringify({id:job.operation_id,posts,gets}));
}else{
 const [job]=await jobs.list(); const result=await jobs.refresh(job.operation_id);assert.equal(result.observed.state,'complete');
 const markdown=await jobs.markdown(job.operation_id);assert.ok(markdown.includes('Привет'));
 assert.equal(posts,0);console.log(JSON.stringify({id:job.operation_id,posts,gets,markdown:true}));
}
