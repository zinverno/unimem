import { test } from "node:test";
import assert from "node:assert/strict";
import { checkConnection, connectionLines } from "../lib/connection.js";
import { ClientError } from "../lib/transport.js";
const response = (status, body) => new Response(JSON.stringify(body), {status, headers:{"Content-Type":"application/json"}});
const video = { ready:false, asr:true, vision:false, decoder:"av16", sampling:"fixed", asr_profile:"base", vision_profile:"qwen" };
function server({ yt=false, original=true, asr=true, vision=false, auth=true }={}) {
  const calls=[];
  const http = async (url, options={}) => {
    calls.push([new URL(url).pathname, options]);
    if(url.endsWith('/health')) return response(200,{status:'ok'});
    if(!auth) return response(401,{error:{code:'unauthorized'}});
    if(url.endsWith('/destinations')) return response(200,[]);
    if(url.endsWith('/image/capabilities')) return response(200,{original,ocr:false,description:{ready:vision,code:vision?'ready':'vision_profile_missing'}});
    if(url.endsWith('/video/capabilities')) return response(200,{...video,asr,vision});
    return response(404,{error:{code:yt?'operation_not_found':'not_found'}});
  };
  return {http,calls};
}
for(const mode of [{asr:true,original:false},{asr:false,original:true}]) test(`connection independent of YouTube ${JSON.stringify(mode)}`,async()=>{
  const s=server(mode), result=await checkConnection(s.http);
  assert.equal(result.server,true); assert.equal(result.token,true); assert.equal(result.youtube,false); assert.equal(result.error,null);
  assert.equal(result.video.asr,mode.asr); assert.equal(result.image.original,mode.original);
  assert(s.calls.every(([,o])=>!o.method&&!o.body));
  assert(connectionLines(result).join(' ').includes('Connector 0.3.0'));
});
test('credential refusal and offline do not report success',async()=>{
  const s=await checkConnection(server({auth:false}).http);
  assert.equal(s.server,true); assert.equal(s.token,false); assert.equal(s.error.code,'unauthorized');
  const off=await checkConnection(async()=>{throw new ClientError('unavailable')});
  assert.equal(off.server,false); assert.equal(off.token,false);
});
test('missing vision remains explicit; no acquisition',async()=>{
  const s=server({yt:true}); const result=await checkConnection(s.http);
  assert.equal(result.youtube,true); assert.equal(result.image.description.ready,false);
  assert(connectionLines(result).join(' ').includes('файлы модели отсутствуют'));
  assert(s.calls.every(([p])=>!p.includes('/uploads')));
});
