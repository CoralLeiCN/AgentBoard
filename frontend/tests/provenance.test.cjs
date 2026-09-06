// Run with: node --test frontend/tests/provenance.test.cjs (no npm dependencies).
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const context=vm.createContext({});
vm.runInContext(readFileSync('frontend/provenance.js','utf8'),context);
const event={id:'e',row_id:3,sequence:9,session_id:'s',source:'codex_jsonl',kind:'llm',name:'LLM response (estimated)',timing:'estimated',start_time:'2026-09-06T08:51:24.032000000Z',end_time:'2026-09-06T08:51:26.870000000Z',text:'',attributes:{basis:'gap between rollout items; includes orchestration'}};
const fields=(overrides={})=>Object.fromEntries(context.describeEvent({...event,...overrides}).fields.map(f=>[f.field,f]));

test('source timestamps are normalized independently of interval inference',()=>{
  const f=fields();
  assert.equal(f.start_time.origin,'normalized');
  assert.equal(f.end_time.origin,'normalized');
  assert.equal(f['duration_ms (calculated)'].origin,'calculated');
  assert.equal(f['duration_ms (calculated)'].value,2838);
  for(const key of ['kind','timing','name','text','attributes.basis']) assert.equal(f[key].origin,'inferred');
  assert.equal(f.id.origin,'calculated');
  assert.equal(f.session_id.origin,'normalized');
});
test('nanosecond differences survive browser arithmetic',()=>{
  const f=fields({start_time:'2023-11-14T22:13:20.000000001Z',end_time:'2023-11-14T22:13:20.000000002Z'});
  assert.equal(f['duration_ms (calculated)'].value,0.000001);
});
test('derived starts are calculated, recorded item starts are normalized',()=>{
  const reported=fields({source:'codex_item',kind:'tool',attributes:{basis:'Codex reported tool duration',item:{type:'CommandExecution'}}});
  assert.equal(reported.start_time.origin,'calculated');
  assert.equal(reported.end_time.origin,'normalized');
  assert.equal(reported['attributes.item'].origin,'normalized');
  assert.equal(fields({source:'codex_item',attributes:{}}).start_time.origin,'normalized');
  assert.equal(fields({source:'otlp_log'}).start_time.origin,'calculated');
  assert.equal(fields({source:'otlp_trace'}).start_time.origin,'normalized');
});
test('unfinished operations have no duration, clamped ends are calculated',()=>{
  const f=fields({end_time:null});
  assert.equal(f.end_time.origin,'unavailable');
  assert.equal(f['duration_ms (calculated)'].value,null);
  assert.equal(fields({kind:'tool',attributes:{end_time_clamped:true}}).end_time.origin,'calculated');
});
test('model output, dummy output, and retained content have distinct origins',()=>{
  const replay=(attributes)=>fields({source:'replay',kind:'assistant',text:'Answer',attributes});
  assert.equal(replay({content_origin:'model_generated'}).text.origin,'model_generated');
  assert.equal(replay({content_origin:'inferred'}).text.origin,'inferred');
  assert.equal(replay({retained_context:true}).text.origin,'normalized');
  assert.equal(replay({retained_context:false}).text.origin,'normalized');
  assert.equal(replay({}).text.origin,'unknown');
  assert.equal(replay({content_origin:'<script>'}).text.origin,'unknown');
  assert.equal(replay({content_origin:'model_generated'}).start_time.origin,'normalized');
  assert.equal(fields({kind:'assistant',text:'Imported response'}).text.origin,'normalized');
});
test('unknown adapter origins are not guessed',()=>{
  const f=fields({source:'plugin'});
  assert.equal(f.start_time.origin,'unknown');
  assert.equal(f.id.origin,'unknown');
});

test('trace-wide session association is labeled inferred separately from preserved telemetry',()=>{
  const f=fields({source:'otlp_trace',attributes:{
    agentboard_session_association:{basis:'unique conversation identity in trace'},
    otel:{record:{name:'startup_prewarm'}}
  }});
  assert.equal(f.session_id.origin,'inferred');
  assert.equal(f['attributes.agentboard_session_association'].origin,'inferred');
  assert.equal(f['attributes.otel'].origin,'normalized');
});


test('unified exact match explains composition and preserves both source records',()=>{
  const records=[{id:'rollout'},{id:'item'}];
  const description=context.describeEvent({...event,source:'codex_item',kind:'tool',
    attributes:{item:{id:'call'},basis:'Codex reported tool duration'},
    unified:{match_status:'matched',source_event_ids:['rollout','item'],records}});
  assert.match(description.summary,/exact identity match/);
  assert.equal(description.evidence.data,records);
  assert.equal(description.fields.find(f=>f.field==='unified').origin,'inferred');
  assert.match(description.fields.find(f=>f.field==='text').explanation,/Rollout call text/);
});
