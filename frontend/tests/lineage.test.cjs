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
test('field evidence shows archive and pointer without embedding collapsed raw text',()=>{
  const html=context.lineageTable({id:'e'},[result('e','<img onerror=alert(1)>')],esc);
  assert.match(html,/Archive #1 · Line 2/);
  assert.match(html,/\/payload\/content/);
  assert.match(html,/codex-jsonl-v4 · SHA-256 hash/);
  assert.match(html,/data-source-key="1:2"/);
  assert.match(html,/&lt;img onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html,/alert\(1\)<\/script>|&lt;script&gt;/);
  assert.doesNotMatch(html,/<script>|<img/);
});
test('expanding sources sets exact text safely and collapsing releases it',()=>{
  const data=[result('e','',1),result('other','',2)];
  data[1].records[0].text='different archive, same physical line\n';
  const details=['1:2','2:2','missing:2'].map(sourceKey=>{
    const pre={textContent:'',set innerHTML(value){throw Error('Source text must not be parsed as HTML');}};
    return {dataset:{sourceKey},open:false,querySelector:()=>pre};
  });
  context.bindLineageSources({querySelectorAll:()=>details},data);
  for(const [index,section] of details.entries()) {
    assert.equal(section.querySelector('pre').textContent,'');
    section.open=true;section.ontoggle();
    assert.equal(section.querySelector('pre').textContent,data[index]?.records[0].text??'Source line unavailable');
    section.open=false;section.ontoggle();
    assert.equal(section.querySelector('pre').textContent,'');
    section.open=true;section.ontoggle();
    assert.equal(section.querySelector('pre').textContent,data[index]?.records[0].text??'Source line unavailable');
  }
});
test('large structured records are not repeated for each collapsed field',()=>{
  const data=result('e','');
  data.fields=Object.fromEntries(Array.from({length:128},(_,i)=>[
    `/attributes/item/result/${i}`,{...data.fields['/text'],value:'x'.repeat(1024)}
  ]));
  data.records[0].text=JSON.stringify({payload:{item:{result:Array(128).fill('x'.repeat(1024))}}});
  const html=context.lineageTable({id:'e'},[data],value=>{
    assert.notEqual(value,data.records[0].text,'Collapsed sources must not serialize full raw records');
    return esc(value);
  });
  assert.equal((html.match(/class="field-source"/g)||[]).length,128);
  assert.ok(Buffer.byteLength(html)<300_000,'Initial markup should scale with field values, not fields × record size');
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
  const nodes={'#event-source-note':{textContent:''},'#event-fields':{innerHTML:'',querySelectorAll:()=>[],replaceChildren(){this.innerHTML='';}}};
  const ctx=vm.createContext({hasFeature:()=>true,$:id=>nodes[id],esc,lineageTable:context.lineageTable,bindLineageSources:context.bindLineageSources,
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
