/* Dashboard presentation: all totals come from the server, independent of pagination. */
function dashboardParams(values) {
  const p=new URLSearchParams();
  if(values.start)p.set('start',`${values.start}T00:00:00Z`);
  if(values.end){const end=new Date(`${values.end}T00:00:00Z`);end.setUTCDate(end.getUTCDate()+1);p.set('end',end.toISOString());}
  for(const key of ['q','agent','producer','model'])if(values[key])p.set(key,values[key]);
  for(const tag of [...new Set((values.tags||'').split(',').map(s=>s.trim()).filter(Boolean))])p.append('tag',tag);
  if(Object.keys(values.metadata||{}).length)p.set('metadata',JSON.stringify(values.metadata));
  return p;
}
function dashboardHTML(report) {
  const e=usageEscape,n=tokenNumber,m=usageMoney,s=report.summary,t=s.tokens;
  const partial=s.estimated_cost_usd==null;
  const cards=[
    ['SESSIONS',n(s.sessions),`${n(s.sessions_with_usage)} with usage · ${n(s.sessions_without_usage)} without usage`],
    ['RECORDED TOKENS',n(t.total_tokens),`${n(s.records)} usage records`],
    [partial?'PRICED SUBTOTAL · USD':'EST. API VALUE · USD',m(partial?s.priced_subtotal_usd:s.estimated_cost_usd),`${n(s.priced_records)} of ${n(s.records)} records priced`],
    ['INPUT TOKENS',n(t.input_tokens),`${n(t.cached_input_tokens)} cached · ${n(t.cache_write_input_tokens)} cache writes included`],
    ['OUTPUT TOKENS',n(t.output_tokens),`${n(t.reasoning_output_tokens)} reasoning tokens included`],
    ['USAGE COVERAGE',s.partial_sessions||s.sessions_without_usage?'Incomplete':s.records?'Recorded':'Unavailable',`${n(s.partial_sessions)} partial sessions · ${n(s.unpriced_records)} unpriced records`],
  ];
  const groupTable=(groups,label,key)=>`<div class="table-wrap"><table class="dashboard-table"><thead><tr><th>${label}</th><th>INPUT</th><th>CACHED INPUT</th><th>OUTPUT</th><th>TOTAL TOKENS</th><th>PRICED SUBTOTAL</th></tr></thead><tbody>${groups.map(g=>`<tr><td>${e(g[key]||'Unknown model')}</td><td>${n(g.tokens.input_tokens)}</td><td>${n(g.tokens.cached_input_tokens)}</td><td>${n(g.tokens.output_tokens)}</td><td>${n(g.tokens.total_tokens)}</td><td>${e(m(g.priced_subtotal_usd))}<small>${n(g.priced_records)}/${n(g.records)} records priced</small></td></tr>`).join('')}</tbody></table></div>`;
  const max=Math.max(1,...report.daily.map(d=>d.tokens.total_tokens||0));
  return `<section class="panel usage-panel"><div class="metrics usage-metrics">${cards.map(([label,value,note])=>`<div><span class="small-label">${e(label)}</span><strong>${e(value)}</strong><small>${e(note)}</small></div>`).join('')}</div>
    ${partial&&s.sessions?'<p class="usage-warning">A full cost estimate is unavailable. The subtotal includes only priced records; missing usage and prices are not zero.</p>':''}
    ${s.excluded_unknown_time_records?`<p class="usage-warning">${n(s.excluded_unknown_time_records)} undated usage records in sessions matching the metadata filters could not be assigned to this period.</p>`:''}
    <p class="usage-caption">${e(report.note)} Prices verified ${e(report.pricing.verified_at)} · <a href="${e(report.pricing.source_url)}" target="_blank" rel="noopener">Pricing source ↗</a>.</p></section>
    ${!s.sessions?'<div class="panel empty"><h2>No matching sessions</h2><p>Try a wider period or remove a filter.</p></div>':''}
    ${report.daily.length?`<section class="panel usage-panel"><div class="toolbar"><h2>Daily usage <span class="muted">· UTC</span></h2></div><div class="dashboard-days">${report.daily.map(d=>`<div class="dashboard-day"><span>${e(d.day)}</span><div class="dashboard-track"><div style="width:${100*(d.tokens.total_tokens||0)/max}%"></div></div><strong>${n(d.tokens.total_tokens)} tokens</strong><span>${e(m(d.priced_subtotal_usd))}</span></div>`).join('')}</div><details class="usage-details"><summary>Daily token and cost breakdown</summary>${groupTable(report.daily,'DAY (UTC)','day')}</details></section>`:''}
    ${report.undated.records?`<p class="timing-note">Included in All time: ${n(report.undated.records)} undated records (${n(report.undated.tokens.total_tokens)} tokens), excluded from the daily chart.</p>`:''}
    ${report.models.length?`<section class="panel usage-panel"><div class="toolbar"><h2>By recorded model</h2></div>${groupTable(report.models,'MODEL','model')}</section>`:''}
    ${s.sessions?`<section class="panel"><div class="toolbar"><h2>Matching sessions</h2><span class="muted">Totals above include every page</span></div><div class="table-wrap"><table class="dashboard-table"><thead><tr><th>SESSION / TAGS</th><th>RECORDED TOKENS</th><th>PRICED SUBTOTAL</th><th>COVERAGE</th></tr></thead><tbody>${report.items.map(item=>`<tr><td><a class="session-name" href="#${encodeURIComponent(item.id)}">${e(item.title)}</a><small>${e(item.tags.join(' · ')||'No tags')}</small></td><td>${n(item.tokens.total_tokens)}</td><td>${e(m(item.priced_subtotal_usd))}<small>${item.priced_records}/${item.records} records priced</small></td><td>${e(item.coverage)}</td></tr>`).join('')}</tbody></table></div></section>`:''}`;
}
if(typeof module!=='undefined'&&module.exports)module.exports={dashboardHTML,dashboardParams};
