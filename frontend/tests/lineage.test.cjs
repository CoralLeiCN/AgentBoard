const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const context=vm.createContext({});
vm.runInContext(readFileSync('frontend/lineage.js','utf8'),context);
const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
function result(id,value,archive=1) {
  return {event_id:id,fields:{'/text':{value,origin:'normalized',available:true,method:'Source text',sources:[{import_id:archive,line_number:2,pointer:'/payload/content',role:'value',present:true}]}},
    archives:[{id:archive,mapping_version:'codex-jsonl-v4',sha256:'hash'}],records:[{import_id:archive,line_number:2,text:' {"content":"<script>alert(1)</script>"}\r\n'}]};
}
test('field evidence shows exact archive, physical line, pointer and escaped raw text',()=>{
  const html=context.lineageTable({id:'e'},[result('e','<img onerror=alert(1)>')],esc);
  assert.match(html,/Archive #1 · Line 2/);
  assert.match(html,/\/payload\/content/);
  assert.match(html,/codex-jsonl-v4 · SHA-256 hash/);
  assert.match(html,/&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(html,/\r\n/);
  assert.doesNotMatch(html,/<script>|<img/);
});
test('unified text selects rollout provenance and falls back to item provenance when empty',()=>{
  const event={id:'item',unified:{match_status:'matched',records:[{id:'rollout',text:'arguments'}]}};
  const results=[result('rollout','arguments',1),result('item','command',2)];
  let view=context.lineageFields(event,results);
  assert.equal(view.fields[0].value,'arguments');
  assert.equal(view.fields[0].sources[0].import_id,1);
  event.unified.records[0].text='';
  view=context.lineageFields(event,results);
  assert.equal(view.fields[0].value,'command');
  assert.equal(view.fields[0].sources[0].import_id,2);
});
test('unknown mappings and missing raw keys are explicit, including null and empty values',()=>{
  const data=result('e','');
  data.fields['/text'].sources[0].present=false;
  data.fields['/other']={value:null,origin:'unknown',available:false,method:'No evidence',sources:[]};
  const html=context.lineageTable({id:'e'},[data],esc);
  assert.match(html,/Absent in source/);
  assert.match(html,/Source evidence unavailable/);
  assert.match(html,/<code>null<\/code>/);
  assert.match(html,/&quot;&quot;/);
});
test('late field responses cannot overwrite a newer inspector or its error state',async()=>{
  const source=readFileSync('frontend/app.js','utf8');
  let resolveOld;
  const nodes={'#event-source-note':{textContent:''},'#event-fields':{innerHTML:'',replaceChildren(){this.innerHTML='';}}};
  const ctx=vm.createContext({$:id=>nodes[id],esc,lineageTable:context.lineageTable,
    json:path=>path.includes('/old/')?new Promise(resolve=>resolveOld=resolve):Promise.resolve(result('new','current'))});
  vm.runInContext('let eventLineageRequest=0;'+source.slice(source.indexOf('async function loadEventLineage('),source.indexOf('function inspectEvent(')),ctx);
  const old=ctx.loadEventLineage({id:'old',session_id:'s',source:'codex_jsonl'});
  await ctx.loadEventLineage({id:'new',session_id:'s',source:'codex_jsonl'});
  const current=nodes['#event-fields'].innerHTML;
  resolveOld(result('old','outdated'));
  await old;
  assert.equal(nodes['#event-fields'].innerHTML,current);
  assert.match(current,/current/);
});
