const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {usageHTML,usageMoney,tokenNumber,tokenPercent}=require('../usage.js');

function report(overrides={}) {
  const tokens={input_tokens:1000,cached_input_tokens:200,cache_write_input_tokens:100,output_tokens:100,reasoning_output_tokens:40,total_tokens:1100};
  return {tokens,breakdown:{uncached_input_tokens:800,reasoning_percent_of_total:100*40/1100},records:1,priced_records:1,estimated_cost_usd:'0.00538',priced_subtotal_usd:'0.00538',
    coverage:'recorded',source:'token_usage_record',models:[],items:[],findings:{},note:'Standard API value, not a bill.',
    pricing:{verified_at:'2026-09-07',source_url:'https://developers.openai.com/api/docs/pricing'},archive:{id:1},...overrides};
}
test('unknown and true zero stay distinct and small costs are visible',()=>{
  assert.equal(usageMoney(null),'Unavailable');
  assert.equal(usageMoney('0'),'$0.00');
  assert.equal(usageMoney('0.00000002'),'<$0.000001');
  assert.equal(usageMoney('0.00538'),'$0.00538');
  assert.equal(tokenNumber(null),'—');
  assert.equal(tokenNumber(0),'0');
  assert.equal(tokenPercent(null),'—');
  assert.equal(tokenPercent(0),'0%');
  assert.equal(tokenPercent(0.001),'<0.01%');
});
test('rendering separates input subcategories and reasoning from totals',()=>{
  const html=usageHTML(report());
  assert.match(html,/1,100/);
  assert.match(html,/UNCACHED INPUT<\/span><strong>800/);
  assert.match(html,/CACHED INPUT<\/span><strong>200/);
  assert.match(html,/100 cache writes included/);
  assert.match(html,/REASONING \/ TOTAL<\/span><strong>3.64%/);
  assert.match(html,/40 reasoning ÷ 1,100 total tokens/);
  assert.match(html,/EST. API VALUE · USD/);
  assert.match(html,/independent of timeline filters/);
});
test('partial usage is labeled as a subtotal, never a complete bill',()=>{
  const html=usageHTML(report({estimated_cost_usd:null,coverage:'partial',findings:{cumulative_resets:1}}));
  assert.match(html,/PRICED SUBTOTAL/);
  assert.match(html,/A full cost estimate is unavailable/);
  assert.match(html,/Cumulative counter resets skipped: 1/);
});
test('inconsistent summaries show a warning and retain only a priced subtotal',()=>{
  const html=usageHTML(report({estimated_cost_usd:null,coverage:'partial',findings:{usage_summary_mismatches:1}}));
  assert.match(html,/Usage summaries disagree with response records; totals may be incomplete/);
  assert.match(html,/Mismatched summaries: 1/);
  assert.match(html,/PRICED SUBTOTAL/);
  assert.ok(!html.includes('EST. API VALUE'));
});
test('model overrides and recorded model names are escaped',()=>{
  const model='<img src=x onerror=alert(1)>';
  const html=usageHTML(report({model_override:model,models:[{model,records:1,priced_records:0,tokens:report().tokens,breakdown:report().breakdown,priced_subtotal_usd:null}]}));
  assert.ok(!html.includes('<img'));
  assert.match(html,/Pricing assumes &lt;img/);
  assert.match(html,/Unknown|Unavailable/);
});
test('empty usage has an explicit unavailable state',()=>{
  const html=usageHTML(report({records:0,archive:null,note:'Import the original rollout.'}));
  assert.match(html,/Import the original rollout/);
  assert.ok(!html.includes('$0.00'));
});

test('real Codex slice totals render consistently in cards, model groups and history',()=>{
  const source=readFileSync(join(__dirname,'../../examples/fixtures/codex-real-usage-slice.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
  const response=source.findLast(row=>row.type==='token_usage_record');
  // Use the real recorded thread total; the backend tests independently verify response summation.
  const tokens=response.payload.thread_token_usage;
  const breakdown={uncached_input_tokens:36916,reasoning_percent_of_total:100*140/194653};
  const group={model:'gpt-6-astra',tokens,breakdown,records:6,priced_records:6,priced_subtotal_usd:'0.566538'};
  const row={model:'gpt-6-astra',pricing_model:'gpt-6-astra',timestamp:response.timestamp,line_number:16,
    method:'response_usage',tokens:response.payload.usage,
    breakdown:{uncached_input_tokens:341,reasoning_percent_of_total:100*81/36254},
    cost:{usd:'0.049172',components_usd:{input:'0.00341',cache_write:'0',cached_input:'0.035712',output:'0.01005'}},
    cumulative_tokens:194653,cumulative_priced_usd:'0.566538'};
  const html=usageHTML(report({tokens,breakdown,records:6,priced_records:6,estimated_cost_usd:'0.566538',models:[group],items:[row]}));
  assert.match(html,/TOTAL TOKENS<\/span><strong>194,653/);
  assert.match(html,/UNCACHED INPUT<\/span><strong>36,916/);
  assert.match(html,/CACHED INPUT<\/span><strong>156,928/);
  assert.match(html,/REASONING \/ TOTAL<\/span><strong>0.07%/);
  assert.match(html,/140 reasoning ÷ 194,653 total tokens/);
  assert.match(html,/<td>0.07%<small>140 reasoning tokens/);
  assert.match(html,/<td>0.22%<small>81 reasoning tokens/);
  assert.match(html,/<td>341<small class="usage-token-cost">Cost: \$0.00341<\/small><small>0 cache writes included/);
  assert.match(html,/<td>35,712<small class="usage-token-cost">Cost: \$0.035712/);
  assert.match(html,/<td>201<small class="usage-token-cost">Cost: \$0.01005/);
  assert.match(html,/\$0.566538/);
});

test('row costs include cache writes once and distinguish zero from unavailable',()=>{
  const row={model:'gpt-5.6-sol',pricing_model:'gpt-5.6-sol',line_number:2,method:'response_usage',
    tokens:report().tokens,breakdown:report().breakdown,cumulative_tokens:1100,cumulative_priced_usd:'0.00538'};
  const render=cost=>usageHTML(report({items:[{...row,cost}]}));
  const html=render({usd:'0.00538',components_usd:{input:'0.0028',cache_write:'0.0005',cached_input:'0.00008',output:'0.002'}});
  assert.match(html,/<td>800<small class="usage-token-cost">Cost: \$0.0033/);
  assert.match(html,/<td>200<small class="usage-token-cost">Cost: \$0.00008/);
  assert.match(html,/<td>100<small class="usage-token-cost">Cost: \$0.002/);
  assert.match(html,/<td>\$0.00538/);
  assert.equal((render({usd:null,reason:'Unknown model'}).match(/Cost: Unavailable/g)||[]).length,3);
  assert.equal((render({usd:'0',components_usd:{input:'0',cache_write:'0',cached_input:'0',output:'0'}}).match(/Cost: \$0.00</g)||[]).length,3);
});
