/* Pure rendering helpers shared with the Node regression tests. */
function usageEscape(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function tokenNumber(value) { return value == null ? '—' : value.toLocaleString('en-US'); }
function tokenPercent(value) {
  if(value==null)return '—';
  if(value>0&&value<0.01)return '<0.01%';
  return `${value.toLocaleString('en-US',{maximumFractionDigits:2})}%`;
}
function usageMoney(value) {
  if(value == null)return 'Unavailable';
  const number=Number(value);
  if(number>0&&number<0.000001)return '<$0.000001';
  return `$${number.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:number<1?6:4})}`;
}
function usageTokenCost(cost,category) {
  const parts=cost.components_usd;
  if(cost.usd==null||!parts)return null;
  // Presentation only: match the uncached token column, which includes cache writes.
  if(category==='input')return parts.input==null||parts.cache_write==null?null:Number(parts.input)+Number(parts.cache_write);
  return parts[category]??null;
}
function usageHTML(report) {
  const e=usageEscape,t=report.tokens,b=report.breakdown;
  const findings={
    invalid_usage_records:'Invalid response usage records excluded',missing_response_ids:'Usage records without response IDs excluded',
    conflicting_response_records:'Conflicting response records; first record retained',
    invalid_cumulative_records:'Invalid cumulative records excluded',cumulative_resets:'Cumulative counter resets skipped',
    invalid_cumulative_deltas:'Invalid cumulative differences excluded',tokens_outside_response_records:'Tokens in cumulative totals outside the response records',
    usage_summary_mismatches:'Usage summaries disagree with response records; totals may be incomplete. Mismatched summaries',
  };
  const warnings=Object.entries(report.findings).filter(([key])=>findings[key]).map(([key,count])=>`${findings[key]}: ${tokenNumber(count)}.`);
  if(!report.records)return `<p class="usage-empty">${e(report.archive?'No supported token usage is available in this archive.':report.note)}${warnings.length?' '+e(warnings.join(' ')):''}</p>`;
  const partial=report.estimated_cost_usd==null;
  const cost=partial?report.priced_subtotal_usd:report.estimated_cost_usd;
  const cards=[
    ['TOTAL TOKENS',tokenNumber(t.total_tokens),'Uncached input + cached input + output'],
    ['UNCACHED INPUT',tokenNumber(b.uncached_input_tokens),`${tokenNumber(t.cache_write_input_tokens)} cache writes included`],
    ['CACHED INPUT',tokenNumber(t.cached_input_tokens),'Input tokens read from cache'],
    ['OUTPUT TOKENS',tokenNumber(t.output_tokens),'Includes reasoning tokens'],
    ['REASONING / TOTAL',tokenPercent(b.reasoning_percent_of_total),`${tokenNumber(t.reasoning_output_tokens)} reasoning ÷ ${tokenNumber(t.total_tokens)} total tokens`],
    [partial?'PRICED SUBTOTAL · USD':'EST. API VALUE · USD',usageMoney(cost),`${tokenNumber(report.priced_records)} of ${tokenNumber(report.records)} usage records priced`],
  ];
  return `<div class="metrics usage-metrics">${cards.map(([label,value,note])=>`<div><span class="small-label">${e(label)}</span><strong>${e(value)}</strong><small>${e(note)}</small></div>`).join('')}</div>
    <p class="usage-caption">${report.source==='token_usage_record'?'Per-response usage':'Cumulative token-count differences'} · Whole archived rollout, independent of timeline filters.${report.model_override?` Pricing assumes ${e(report.model_override)} for every record.`:''}${report.coverage==='partial'?' Usage coverage is partial.':''}${partial?' A full cost estimate is unavailable; missing prices are excluded from the subtotal.':''}</p>
    ${warnings.length?`<p class="usage-warning">${e(warnings.join(' '))}</p>`:''}
    <details class="usage-details"><summary>By model · ${report.models.length} ${report.models.length===1?'model':'models'}</summary>
      <div class="table-wrap"><table class="usage-table"><thead><tr><th>RECORDED MODEL</th><th>UNCACHED INPUT</th><th>CACHED INPUT</th><th>OUTPUT</th><th>REASONING / TOTAL</th><th>PRICED SUBTOTAL</th></tr></thead><tbody>
      ${report.models.map(group=>`<tr><td>${e(group.model||'Unknown model')}</td><td>${tokenNumber(group.breakdown.uncached_input_tokens)}<small>${tokenNumber(group.tokens.cache_write_input_tokens)} cache writes included</small></td><td>${tokenNumber(group.tokens.cached_input_tokens)}</td><td>${tokenNumber(group.tokens.output_tokens)}</td><td>${e(tokenPercent(group.breakdown.reasoning_percent_of_total))}<small>${tokenNumber(group.tokens.reasoning_output_tokens)} reasoning tokens</small></td><td>${e(usageMoney(group.priced_subtotal_usd))}<small>${group.priced_records}/${group.records} records priced</small></td></tr>`).join('')}
      </tbody></table></div></details>
    <details class="usage-details" id="usage-record-details"><summary>Usage over time · ${report.items.length.toLocaleString()} of ${report.records.toLocaleString()} records</summary>
      <p class="usage-caption">Token counts with estimated costs in USD. Uncached input cost includes cache writes; output cost includes reasoning.</p>
      <div class="table-wrap"><table class="usage-table"><thead><tr><th>RECORDED AT</th><th>MODEL</th><th>UNCACHED INPUT</th><th>CACHED INPUT</th><th>OUTPUT</th><th>REASONING / TOTAL</th><th>API VALUE</th><th>CUMULATIVE TOKENS</th></tr></thead><tbody>
      ${report.items.map(row=>`<tr><td>${e(row.timestamp?new Date(row.timestamp).toLocaleString():'Unknown time')}<small>Source line ${row.line_number} · ${row.method==='response_usage'?'response':'cumulative difference'}</small></td><td>${e(row.model||'Unknown model')}${row.pricing_model!==row.model?`<small>Priced as ${e(row.pricing_model)}</small>`:''}</td><td>${tokenNumber(row.breakdown.uncached_input_tokens)}<small class="usage-token-cost">Cost: ${e(usageMoney(usageTokenCost(row.cost,'input')))}</small><small>${tokenNumber(row.tokens.cache_write_input_tokens)} cache writes included</small></td><td>${tokenNumber(row.tokens.cached_input_tokens)}<small class="usage-token-cost">Cost: ${e(usageMoney(usageTokenCost(row.cost,'cached_input')))}</small></td><td>${tokenNumber(row.tokens.output_tokens)}<small class="usage-token-cost">Cost: ${e(usageMoney(usageTokenCost(row.cost,'output')))}</small></td><td>${e(tokenPercent(row.breakdown.reasoning_percent_of_total))}<small>${tokenNumber(row.tokens.reasoning_output_tokens)} reasoning tokens</small></td><td>${e(usageMoney(row.cost.usd))}<small>${e(row.cost.reason||(row.cost.long_context?'Long-context rates':'Standard rates'))}</small></td><td>${tokenNumber(row.cumulative_tokens)}<small>${e(usageMoney(row.cumulative_priced_usd))} priced so far</small></td></tr>`).join('')}
      </tbody></table></div></details>
    <p class="usage-caption">${e(report.note)} Prices verified ${e(report.pricing.verified_at)} · <a href="${e(report.pricing.source_url)}" target="_blank" rel="noopener">OpenAI pricing ↗</a>. Archive #${report.archive.id}.</p>`;
}
if(typeof module!=='undefined'&&module.exports)module.exports={usageHTML,usageMoney,tokenNumber,tokenPercent};
