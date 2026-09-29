import assert from 'node:assert/strict';
import { it } from 'node:test';
import { settingsStore } from '../lib/settings.js';
import { localTransport, ClientError } from '../lib/transport.js';
import { youtubeClient, validateOperation, youtubeUrl } from '../lib/youtube.js';
import { jobsController, HISTORY_KEY, HISTORY_LIMIT } from '../lib/jobs.js';
import { downloadMarkdown } from '../lib/presentation.js';
import { readSelection } from '../lib/capture.js';
import { readPageHtml } from '../lib/page.js';
import { readFileSync } from 'node:fs';
const TOKEN = 'synthetic-credential_'.padEnd(43, 'x');
const URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
const NOW = '2026-09-29T10:00:00Z';
function area() {
  let data = {};
  return { fail: false, calls: [], async get() { if(this.fail) throw Error(); return structuredClone(data); },
    async set(value) { if(this.fail) throw Error(); this.calls.push(structuredClone(value)); data = {...data,...structuredClone(value)}; },
    async remove(key) { delete data[key]; }, async setAccessLevel(value) { this.access = value; } };
}
function receipt(request, state = 'queued') {
  const terminal = ['complete','failed','interrupted'].includes(state);
  return { ...request, state, accepted_at: NOW, updated_at: NOW, started_at: state === 'queued' ? null : NOW,
    finished_at: terminal ? NOW : null, capture_id: state === 'complete' ? 'capture-1' : null,
    content_id: state === 'complete' ? 'content-1' : null, result_available: state === 'complete',
    error_code: ['failed','interrupted'].includes(state) ? 'captions_unavailable' : null };
}
const response = (status, body) => new Response(JSON.stringify(body), {status, headers:{'Content-Type':'application/json'}});
const errorResponse = (status, code) => response(status, {error:{code,message:TOKEN}});
async function harness() {
  const storage = {local:area(),session:area()}; const settings = settingsStore(storage);
  await settings.save(TOKEN, false, 'ru,en');
  const calls = [], server = new Map();
  let lost = false, offline = false;
  const http = localTransport({getToken: () => settings.getToken(), fetch: async (url, opts) => {
    calls.push({url,opts});
    assert.equal(opts.redirect,'error'); assert.equal(opts.credentials,'omit');
    assert.equal(opts.headers.Authorization, url.endsWith('/health') ? undefined : `Bearer ${TOKEN}`);
    assert.ok(!url.includes(TOKEN));
    if (offline) throw Error('network');
    if (url.endsWith('/health')) return response(200,{status:'ok'});
    if(opts.method === 'POST') {
      const req = JSON.parse(opts.body);
      assert.ok((await storage.local.get())[HISTORY_KEY].some(j=>j.operation_id===req.operation_id && j.delivery==='unconfirmed'));
      const existing = server.get(req.operation_id);
      const body = existing ?? receipt(req);
      server.set(req.operation_id,body);
      if(lost) { lost=false; throw Error('lost response'); }
      return response(existing ? 200 : 202, body);
    }
    const id = url.split('/').at(-1);
    return server.has(id) ? response(200,server.get(id)) : errorResponse(404,'operation_not_found');
  }});
  const client = youtubeClient(http);
  const rebuild = () => jobsController({local:storage.local, client,settings});
  return {storage,settings,calls,server,client,jobs:rebuild(),rebuild,lose:()=>{lost=true;},offline:()=>{offline=true;}};
}
it('credential is session-only by default, replace/delete remove both copies and restrict supported areas', async()=>{
 const h=await harness(); assert.equal((await h.storage.local.get()).credential,undefined);
 assert.equal((await h.storage.session.get()).credential,TOKEN);
 assert.deepEqual(h.storage.local.access,{accessLevel:'TRUSTED_CONTEXTS'});
 await h.settings.save('z'.repeat(43),true,['en','ru']); assert.equal((await h.storage.session.get()).credential,undefined);
 assert.equal(await h.settings.getToken(),'z'.repeat(43));
 await h.settings.save(TOKEN,false,'ru,en'); assert.equal((await h.storage.local.get()).credential,undefined);
 await h.settings.removeToken(); assert.equal(await h.settings.getToken(),null);
 await assert.rejects(h.settings.save('invalid',false,'ru,en'));
 for(const langs of ['', 'r', 'ru,en,', Array(11).fill('en')]) await assert.rejects(h.settings.saveLanguages(langs));
});
it('check is read-only; only typed operation_not_found proves readiness, not generic 404/HTML', async()=>{
 const h=await harness(); assert.deepEqual(await h.client.check(),{server:true,token:true,youtube:true,error:null});
 assert.equal(h.server.size,0); assert.equal((await h.jobs.list()).length,0); assert.equal(h.calls.length,2);
 for(const bad of [response(404,{detail:'Not Found'}),new Response('<html/>',{status:404}),response(404,{}),response(200,{})]) {
  let n=0;const c=youtubeClient(async()=>++n===1?response(200,{status:'ok'}):bad); const s=await c.check();
  assert.equal(s.server,true); assert.equal(s.token,false); assert.equal(s.youtube,false);
 }
 await h.settings.removeToken(); assert.equal((await h.client.check()).error.code,'missing_token');
});
it('freezes two tab/SPA sources, preserves language order, persists ID before first POST and coalesces double clicks',async()=>{
 const h=await harness(); const tab={url:URL}; const first=h.jobs.choose(tab.url); tab.url='https://youtu.be/aaaaaaaaaaa';
 const [a,duplicate,b]=await Promise.all([first,h.jobs.choose(URL),h.jobs.choose(tab.url)]);
 assert.equal(a.operation_id,duplicate.operation_id); assert.notEqual(a.operation_id,b.operation_id);
 await Promise.all([h.jobs.send(a.operation_id),h.jobs.send(a.operation_id),h.jobs.refresh(a.operation_id)]);
 assert.equal(h.calls.filter(x=>x.opts.method==='POST').length,1);
 assert.equal(h.server.get(a.operation_id).url,URL); assert.deepEqual(h.server.get(a.operation_id).languages,['ru','en']);
});
it('lost response probes once, restart reads same ID without POST, accepted-but-missing never resubmits',async()=>{
 const h=await harness(); const j=await h.jobs.choose(URL); h.lose();
 const accepted=await h.jobs.send(j.operation_id); assert.equal(accepted.accepted,true);
 assert.equal(h.calls.length,2); const fresh=h.rebuild(); assert.equal((await fresh.choose(URL)).operation_id,j.operation_id);
 await fresh.refresh(j.operation_id); h.server.clear(); await fresh.retry(j.operation_id);
 assert.equal(h.calls.filter(x=>x.opts.method==='POST').length,1);
 const [observed]=await fresh.list(); assert.equal(observed.accepted,true); assert.equal(observed.error.code,'operation_not_found');
 assert.equal(observed.observed.state,'queued');
});
it('unconfirmed delivery retries explicitly with same parameters, new operation requires again',async()=>{
 const h=await harness(); const j=await h.jobs.choose(URL);
 await h.storage.local.set({[HISTORY_KEY]:[{...j,delivery:'unconfirmed'}]});
 await Promise.all([h.jobs.retry(j.operation_id),h.jobs.retry(j.operation_id)]);
 assert.equal(h.server.size,1); assert.equal(h.calls.filter(x=>x.opts.method==='POST').length,1);
 const [one,two]=await Promise.all([h.jobs.again(j.operation_id),h.jobs.again(j.operation_id)]);
 assert.equal(one.operation_id,two.operation_id);assert.notEqual(one.operation_id,j.operation_id);assert.equal(h.server.size,2);
});
it('storage failure before POST fails closed; history has explicit cap and never evicts unresolved records',async()=>{
 const h=await harness(); const job=await h.jobs.choose(URL);h.storage.local.fail=true;
 await assert.rejects(h.jobs.send(job.operation_id)); assert.equal(h.calls.length,0);
 h.storage.local.fail=false; await h.storage.local.set({[HISTORY_KEY]:Array.from({length:HISTORY_LIMIT},(_,i)=>({...job,operation_id:`id-${i}`}))});
 await assert.rejects(h.jobs.choose('https://youtu.be/aaaaaaaaaaa'),/history_full/);
 assert.equal((await h.jobs.list()).length,HISTORY_LIMIT);assert.equal(h.calls.length,0);
});
it('offline/401/403/429/507/conflict and invalid receipts never replace last server state with failed',async()=>{
 for(const [status,code] of [[401,'unauthorized'],[403,'origin_denied'],[429,'queue_full'],[507,'operation_history_full'],[409,'operation_conflict']]){
  const h=await harness();const j=await h.jobs.choose(URL);await h.jobs.send(j.operation_id);
  const client=youtubeClient(async()=>errorResponse(status,code)); const jobs=jobsController({local:h.storage.local,settings:h.settings,client});
  const seen=await jobs.refresh(j.operation_id);assert.equal(seen.observed.state,'queued');assert.equal(seen.error.status,status);assert.ok(Number.isFinite(Date.parse(seen.observed_at)));
 }
 const h=await harness();const j=await h.jobs.choose(URL);await h.jobs.send(j.operation_id);h.offline();
 const seen=await h.jobs.refresh(j.operation_id);assert.equal(seen.observed.state,'queued');assert.equal(seen.error.code,'unavailable');
});
it('202 queued and 200 running/failed/interrupted/complete use validated body, IDs and result consistency',async()=>{
 const req={operation_id:'op',url:URL,languages:['ru','en']};
 for(const state of ['queued','running','failed','interrupted','complete']) {
  const body=receipt(req,state);const client=youtubeClient(async()=>response(state==='queued'?202:200,body));assert.equal((await client.submit(req)).state,state);
 }
 const good=receipt(req,'complete');
 for(const bad of [{operation_id:'other'},{url:'https://evil.invalid'},{languages:['en','ru']},{state:'partial'},{result_available:false},{capture_id:null},{content_id:null},{error_code:'<script>'},{started_at:null},{finished_at:null}]) assert.throws(()=>validateOperation({...good,...bad},req),/invalid_response/);
 assert.throws(()=>validateOperation(receipt(req,'queued'),{...req,languages:['en']}));
});
it('transport rejects destinations/redirects, limits streamed bytes/deadlines, never supplies page credentials',async()=>{
 let sent=0; const http=localTransport({getToken:async()=>TOKEN,maxBytes:2,fetch:async()=>{sent++;return new Response('abc');}});
 for(const url of ['https://evil.invalid','http://127.0.0.1:8765/health?token=x','http://127.0.0.1:8766/health'])await assert.rejects(http(url),/invalid_destination/);
 assert.equal(sent,0);await assert.rejects(http('http://127.0.0.1:8765/health'),/response_too_large/);
 const redirect=localTransport({getToken:async()=>TOKEN,fetch:async()=>new Response(null,{status:302,headers:{Location:'https://evil.invalid'}})});
 await assert.rejects(redirect('http://127.0.0.1:8765/health'),/invalid_response/);
 const slow=localTransport({getToken:async()=>TOKEN,timeoutMs:5,fetch:async(_,o)=>new Promise((_,reject)=>o.signal.addEventListener('abort',()=>reject(new DOMException('timeout','AbortError'))))});
 await assert.rejects(slow('http://127.0.0.1:8765/health'),/timeout/);
});
it('Markdown is read from the authorized result route without submit; download is safe, explicit and revocable',async()=>{
 const req={operation_id:'op',url:URL,languages:['ru','en']};let calls=[];
 const c=youtubeClient(async(url,o)=>{calls.push([url,o]);return new Response('<script>untrusted</script>\nПривет',{headers:{'Content-Type':'text/markdown'}});});
 const md=await c.markdown(req);assert.ok(md.includes('Привет'));assert.equal(calls.length,1);assert.equal(calls[0][1],undefined);assert.ok(calls[0][0].endsWith('/op/markdown'));
 let revoked=false, options;const urls={createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{revoked=true;}};
 const result=await downloadMarkdown({downloads:{download:async(o)=>{options=o;return 1;}}},md,'../../bad',urls);
 assert.equal(options.saveAs,true);assert.equal(options.conflictAction,'uniquify');assert.match(options.filename,/^youtube-[A-Za-z0-9_-]+\.md$/);result.release();assert.equal(revoked,true);
 revoked=false;await assert.rejects(downloadMarkdown({downloads:{download:async()=>{throw Error('cancel');}}},md,'op',urls),/download_failed/);assert.equal(revoked,true);
});
it('site injection has no secret/http/storage; management renders as text, never exports credentials in messages',()=>{
 for(const fn of [readSelection,readPageHtml]) assert.doesNotMatch(fn.toString(),/token|credential|fetch|postMessage|storage|127\.0\.0\.1/);
 const ui=readFileSync(new globalThis.URL('../manage.js',import.meta.url),'utf8');assert.doesNotMatch(ui,/innerHTML|outerHTML|insertAdjacentHTML|console\./);assert.match(ui,/textContent = markdown/);
 assert.doesNotMatch(ui,/sendMessage\([^\n]*(token|credential)/);
});
it('accepts explicit YouTube forms, rejects credentials, duplicate IDs, ports and other sources',()=>{
 assert.equal(youtubeUrl('https://youtu.be/dQw4w9WgXcQ?t=1'),URL);
 for(const input of ['https://youtube.com/watch?v=dQw4w9WgXcQ&v=aaaaaaaaaaa','https://x:pass@youtube.com/watch?v=dQw4w9WgXcQ','https://youtube.com:443/watch?v=dQw4w9WgXcQ','https://evil.invalid/watch?v=dQw4w9WgXcQ'])assert.throws(()=>youtubeUrl(input));
});
it('denied host permission prevents every request, including anonymous health', async()=>{
 let requests=0;
 const http=localTransport({getToken:async()=>TOKEN,hasPermission:async()=>false,fetch:async()=>{requests++;}});
 await assert.rejects(http('http://127.0.0.1:8765/health'),/permission_denied/);
 await assert.rejects(http('http://127.0.0.1:8765/v1/youtube/operations/op'),/permission_denied/);
 assert.equal(requests,0);
});
it('failed Markdown retrieval leaves completed operation and last observation intact', async()=>{
 const h=await harness();const j=await h.jobs.choose(URL);await h.jobs.send(j.operation_id);
 h.server.set(j.operation_id,receipt({operation_id:j.operation_id,url:j.url,languages:j.languages},'complete'));
 await h.jobs.refresh(j.operation_id);const before=await h.jobs.list();
 const client={markdown:async()=>{throw new ClientError('markdown_unavailable',503);}};
 const jobs=jobsController({local:h.storage.local,settings:h.settings,client});
 await assert.rejects(jobs.markdown(j.operation_id),/markdown_unavailable/);
 assert.deepEqual(await jobs.list(),before);assert.equal(h.calls.filter(c=>c.opts.method==='POST').length,1);
});
