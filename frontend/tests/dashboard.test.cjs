const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const ctx=vm.createContext({URLSearchParams,Date});
vm.runInContext(readFileSync('frontend/usage.js','utf8'),ctx);
vm.runInContext(readFileSync('frontend/dashboard.js','utf8'),ctx);
const tokens={input_tokens:1000,cached_input_tokens:200,cache_write_input_tokens:0,output_tokens:100,reasoning_output_tokens:40,total_tokens:1100};
function report() {
  const aggregate={tokens,records:1,priced_records:1,unpriced_records:0,priced_subtotal_usd:'0.001065'};
  return {summary:{...aggregate,sessions:2,sessions_with_usage:1,sessions_without_usage:1,partial_sessions:0,excluded_unknown_time_records:0,estimated_cost_usd:null},
    daily:[{...aggregate,day:'2026-09-07'}],undated:{records:0},models:[{...aggregate,model:'gpt-5.4-mini'}],
    items:[{...aggregate,id:'a / <b>',title:'<img src=x onerror=alert(1)>',tags:['<work>'],coverage:'recorded'}],
    note:'Recorded usage; not actual billing.',pricing:{verified_at:'2026-09-07',source_url:'https://developers.openai.com/api/docs/pricing'}};
}
test('inclusive date controls produce UTC half-open bounds and preserve typed metadata',()=>{
  const params=ctx.dashboardParams({start:'2026-09-07',end:'2026-09-07',tags:' work, review,work ',metadata:{'/flag':true,'/count':1,'/name':'1'},q:'a&b'});
  assert.equal(params.get('start'),'2026-09-07T00:00:00Z');
  assert.equal(params.get('end'),'2026-09-08T00:00:00.000Z');
  assert.deepEqual([...params.getAll('tag')],['work','review']);
  assert.deepEqual(JSON.parse(params.get('metadata')),{'/flag':true,'/count':1,'/name':'1'});
  assert.equal(params.get('q'),'a&b');
  assert.equal(ctx.dashboardParams({end:'2026-12-31'}).get('end'),'2027-01-01T00:00:00.000Z');
  assert.equal(ctx.dashboardParams({}).toString(),'');
});
test('missing coverage labels a subtotal and links escaped session names',()=>{
  const html=ctx.dashboardHTML(report());
  assert.match(html,/PRICED SUBTOTAL · USD/);
  assert.match(html,/1 without usage/);
  assert.match(html,/A full cost estimate is unavailable/);
  assert.match(html,/1,100 tokens/);
  assert.match(html,/Totals above include every page/);
  assert.ok(!html.includes('<img'));
  assert.match(html,/#a%20%2F%20%3Cb%3E/);
  assert.match(html,/&lt;work&gt;/);
});
test('known zero remains priced, undated records and empty states are visible',()=>{
  const data=report();data.summary.estimated_cost_usd='0';data.summary.excluded_unknown_time_records=3;
  data.undated={records:2,tokens:{total_tokens:2200}};
  let html=ctx.dashboardHTML(data);
  assert.match(html,/EST. API VALUE · USD/);assert.match(html,/\$0.00/);
  assert.match(html,/3 undated usage records/);assert.match(html,/Included in All time: 2 undated/);
  data.summary.sessions=0;data.summary.estimated_cost_usd=null;data.items=[];data.daily=[];data.models=[];
  html=ctx.dashboardHTML(data);assert.match(html,/No matching sessions/);assert.doesNotMatch(html,/Matching sessions/);
});
