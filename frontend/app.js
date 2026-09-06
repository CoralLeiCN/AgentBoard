const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {listKind:'session',offset:0, next:null, sid:null, session:null, stats:null, tab:'timeline', events:[], parallelGroups:[], parallelByEvent:new Map(), parallelNote:'', cursor:null, request:0, listRequest:0, features:[]};
let noticeTimer, searchTimer;
let eventView='table', eventRawRequest=0;
try { const saved=localStorage.getItem('agentboard-event-view');if(['json','raw'].includes(saved))eventView=saved; } catch { /* Storage may be disabled. */ }
function notice(text, error=false) { $('#notice').textContent=text; $('#notice').hidden=false; $('#notice').className=error?'error':''; clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>$('#notice').hidden=true, error?12000:5000); }
async function api(path, options={}) {
  const token=sessionStorage.getItem('agentboard-token');
  const headers={...(token?{Authorization:`Bearer ${token}`} : {}),...options.headers};
  const response=await fetch(path,{...options,headers});
  if (!response.ok) { let message; try { message=(await response.json()).detail; } catch { message=response.statusText; } throw Error(typeof message==='string'?message:JSON.stringify(message)); }
  return response;
}
async function json(path, options) { return (await api(path,options)).json(); }
function run(fn) { return (...args)=>Promise.resolve(fn(...args)).catch(e=>notice(e.message,true)); }
async function busy(button, fn) { button.disabled=true; try { return await fn(); } finally { button.disabled=false; } }
function duration(ms) { if(ms==null)return '—'; if(ms<1000)return `${ms.toFixed(ms<10?1:0)} ms`; if(ms<60000)return `${(ms/1000).toFixed(1)} s`; return `${(ms/60000).toFixed(1)} min`; }
function date(value) { return new Date(value).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}); }
function download(text, name, type='application/x-ndjson') { const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000); }
async function loadSessions() {
  const request=++state.listRequest;
  const params=new URLSearchParams({limit:20,offset:state.offset,q:$('#search').value,category:$('#category').value,identity_kind:state.listKind});
  const data=await json(`/api/v1/sessions?${params}`);
  if(request!==state.listRequest)return;
  state.next=data.next_offset;
  const unattributed=state.listKind==='unattributed';
  const counts=data.identity_counts||{};
  $('#total').textContent=data.total;$('#nav-count').textContent=counts.session||0;
  $('#session-count').textContent=counts.session||0;
  $('#unattributed-count').textContent=(counts.unattributed_trace||0)+(counts.unattributed_resource||0);
  $('#list-title').textContent=unattributed?'Unattributed telemetry':'Sessions';
  $('#total-label').textContent=unattributed?'UNATTRIBUTED GROUPS IN THIS VIEW':'SESSIONS IN THIS VIEW';
  $('#identity-column').textContent=unattributed?'TELEMETRY GROUP':'SESSION'; $('#search').placeholder=unattributed?'Search telemetry…':'Search sessions…'; $('#search').setAttribute('aria-label',unattributed?'Search telemetry':'Search sessions');
  $('#identity-note').textContent=unattributed?'These records have no unambiguous conversation identity. Each trace or resource group remains inspectable; it is not counted as a Codex session.':'Sessions are grouped by recorded conversation identity. Counts include imported and observed sessions, including automatic reviewers; they are not a count of human conversations.';
  $('#empty').hidden=data.items.length>0; if(!data.items.length&&unattributed)$('#empty').innerHTML='<h2>No unattributed telemetry matches</h2><p>Change the search or category filter.</p>'; else if(!data.items.length)$('#empty').innerHTML='<h2>No matching sessions</h2><p>Import a rollout, load the demo, or change the filter. Background traces appear under Unattributed telemetry.</p>';
  $('#sessions').innerHTML=data.items.map(s=>`<tr tabindex="0" data-id="${esc(s.id)}"><td><span class="session-name">${esc(s.title==='Untitled session'&&s.identity_kind!=='session'?'Unattributed telemetry':s.title)}</span><span class="session-sub">${esc(s.id.slice(0,28))}</span></td><td><span class="pill">${esc(s.agent)}</span></td><td>${s.classification?`<span class="pill category">${esc(s.classification.category)}</span> ${s.classification.dummy?'<span class="pill dummy">dummy</span>':''}`:'<span class="muted">Unclassified</span>'}</td><td>${date(String(s.started_at))}</td><td>↗</td></tr>`).join('');
  $('#page-info').textContent=data.total?`${state.offset+1}–${state.offset+data.items.length} of ${data.total} ${unattributed?'telemetry groups':'sessions'}`:'No sessions found';
  $('#prev').disabled=state.offset===0;$('#next').disabled=state.next===null;
  document.querySelectorAll('[data-id]').forEach(row=>{const open=()=>location.hash=encodeURIComponent(row.dataset.id);row.onclick=open;row.onkeydown=e=>{if(e.key==='Enter')open();};});
}
function renderClassification(s) { const c=s.classification;$('#classification').hidden=!c;if(c)$('#classification').innerHTML=`<span class="pill category">${esc(c.category)}</span>${esc(c.reason)} <span class="muted">· ${esc(c.model)}${c.dummy?' · dummy fallback':''}</span>`; }
async function openSession(sid) {
  const request=++state.request; state.sid=sid;state.events=[];state.cursor=null;state.parallelGroups=[];state.parallelByEvent=new Map();$('#parallel-summary').hidden=true;
  $('#list-view').hidden=true;$('#detail-view').hidden=false;$('#event-list').innerHTML='<div class="loading">Loading trace…</div>';
  const [s,stats,archives]=await Promise.all([json(`/api/v1/sessions/${encodeURIComponent(sid)}`),json(`/api/v1/sessions/${encodeURIComponent(sid)}/stats`),json(`/api/v1/sessions/${encodeURIComponent(sid)}/raw-imports`)]);
  if(request!==state.request)return;
  $('#export-raw').hidden=!archives.items.length;
  const unidentified=s.identity_kind!=='session'; $('#elapsed-label').textContent=unidentified?'RECORDED TIME RANGE':'SESSION ELAPSED'; $('#elapsed-note').textContent=unidentified?'First start to last recorded end':'Includes gaps between turns'; $('#back').textContent=state.listKind==='unattributed'?'← Unattributed telemetry':'← All sessions'; state.session=s;$('#session-title').textContent=unidentified&&s.title==='Untitled session'?'Unattributed telemetry':s.title;$('#session-id').textContent=s.id;$('#session-agent').textContent=`${s.agent.toUpperCase()} ${unidentified?'· UNATTRIBUTED TELEMETRY':'SESSION'}${s.metadata.dummy?' · DUMMY MODEL':''}`;
  $('#breadcrumbs').textContent=unidentified?'Workspace / Unattributed telemetry / Trace':'Workspace / Sessions / Trace'; $('#detail-identity-note').hidden=!unidentified; $('#detail-identity-note').textContent='This group has no unambiguous conversation identity. It preserves unattributed telemetry and is not counted as a Codex session.';renderClassification(s);
  const sources=new Set(stats.sources||stats.timing.map(t=>t.source));if(!sources.size)sources.add(s.agent==='replay'?'replay':s.agent==='otel'?'otlp_trace':'codex_jsonl');
  if(sources.has('codex_jsonl')||sources.has('codex_item'))sources.add('unified');
  const source=$('#source');for(const option of source.options)option.disabled=!sources.has(option.value);
  source.value=[...source.options].find(o=>!o.disabled).value;
  $('#classify').hidden=unidentified||!state.features.includes('classification');
  await loadEvents();
}
async function loadEvents(more=false) {
  const request=++state.request,sid=state.sid;
  const source=$('#source').value;
  const params=new URLSearchParams({limit:200,after:more?(state.cursor||0):0,source,q:$('#event-search').value});
  if(state.tab==='inputs')params.set('kind','user');
  if(state.tab==='timeline')params.set('timeline','true');
  let page,stats,parallel;
  if(source==='unified') {
    page=await json(`/api/v1/sessions/${encodeURIComponent(sid)}/unified?${params}`);
    stats=page.stats;parallel=page.parallel;
  } else {
    [page,stats,parallel]=await Promise.all([json(`/api/v1/sessions/${encodeURIComponent(sid)}/events?${params}`),json(`/api/v1/sessions/${encodeURIComponent(sid)}/stats?source=${source}`),json(`/api/v1/sessions/${encodeURIComponent(sid)}/parallel-groups?source=${source}`)]);
  }
  if(request!==state.request)return;
  state.stats=stats;
  state.events=more?[...state.events,...page.items]:page.items;state.cursor=page.next_cursor;
  state.parallelGroups=parallel.items;state.parallelNote=parallel.note;state.parallelByEvent=new Map(parallel.items.flatMap(g=>g.event_ids.map(id=>[id,g])));
  $('#elapsed').textContent=duration(stats.elapsed_ms);$('#input-count').textContent=stats.counts.user||0;
  for(const kind of ['llm','tool','user_wait']) {
    const groups=stats.timing.filter(t=>t.kind===kind);
    const measured=groups.find(t=>t.timing==='measured'),estimated=groups.find(t=>t.timing==='estimated');const selected=source==='unified'&&kind==='llm'?(estimated||measured):(measured||estimated);
    $(`#${kind}-time`).textContent=duration(selected?.active_ms);
    const unfinished=groups.filter(t=>t.active_ms==null).reduce((count,t)=>count+t.count,0);
    $(`#${kind}-quality`).textContent=selected?`${source==='unified'?sourceLabel(selected.source)+' · ':''}${selected.timing} · ${selected.count} ${kind==='user_wait'?'wait':'span'}${selected.count===1?'':'s'}${measured&&estimated?' · other timing also present':''}${unfinished?` · ${unfinished} unfinished`:''}`:unfinished?`${unfinished} unfinished · duration unavailable`:kind==='user_wait'?'No recorded waits':'No timed spans available';
  }
  $('#timing-note').textContent=(source==='codex_item'?'LLM item timings cover output streaming only, not full response latency. ':source==='otlp_log'?'LLM log timings measure transport requests, not necessarily the full response. ':'')+stats.note+' Timeline also shows other recorded spans; these are excluded from LLM, tool, and user-wait totals.';
  $('#event-info').textContent=`${state.events.length} events loaded${state.cursor?' · more available':''} · ${source.replaceAll('_',' ')}`;
  $('#more').hidden=state.cursor===null;renderEvents();
}
function setEventView(view,save=false) {
  eventView=['json','raw'].includes(view)?view:'table';
  $('#event-table-view').hidden=eventView!=='table';
  $('#event-json-view').hidden=eventView!=='json';
  $('#event-raw-view').hidden=eventView!=='raw';
  document.querySelectorAll('[data-event-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.eventView===eventView)));
  if(save)try { localStorage.setItem('agentboard-event-view',eventView); } catch { /* Keep the choice in memory when storage is unavailable. */ }
  $('#event-dialog').scrollTop=0;
}
async function loadEventRaw(event) {
  const request=++eventRawRequest;
  $('#event-raw-status').textContent='Loading source lines…';
  $('#event-raw-lines').replaceChildren();
  const ids=event.unified?.source_event_ids||[event.id];
  try {
    const results=await Promise.all(ids.map(id=>json(`/api/v1/sessions/${encodeURIComponent(event.session_id)}/events/${encodeURIComponent(id)}/raw`)));
    if(request!==eventRawRequest)return;
    const lines=new Map();
    for(const result of results)for(const line of result.lines) {
      const key=`${result.archive.id}:${line.line_number}`;
      if(!lines.has(key))lines.set(key,{...line,archive:result.archive});
    }
    const missing=results.filter(result=>!result.available);
    $('#event-raw-status').textContent=lines.size
      ? `${lines.size} source line${lines.size===1?'':'s'}${ids.length>1?` across ${ids.length} normalized events`:''}. Original JSONL text is preserved. Only records representing this event are shown; calculation-only boundaries are excluded.${missing.length?' Some source mappings are unavailable.':''}`
      : [...new Set(missing.map(result=>result.reason))].join(' ');
    for(const line of [...lines.values()].sort((a,b)=>a.archive.id-b.archive.id||a.line_number-b.line_number)) {
      const section=document.createElement('section'), label=document.createElement('p'), pre=document.createElement('pre');
      label.className='raw-line-label';
      label.textContent=`Line ${line.line_number} · Archive #${line.archive.id} · SHA-256 ${line.archive.sha256}`;
      pre.textContent=line.text;
      section.append(label,pre);$('#event-raw-lines').append(section);
    }
  } catch(error) {
    if(request===eventRawRequest)$('#event-raw-status').textContent=`Unable to load source lines: ${error.message}`;
  }
}
function inspectEvent(event) {
  const description=describeEvent(event);
  const labels={normalized:'Normalized',calculated:'Calculated',inferred:'Inferred',model_generated:'Model-generated',unavailable:'Unavailable',unknown:'Unknown'};
  const valueHTML=value=>{
    const text=typeof value==='string'?value:JSON.stringify(value,null,2)??'null';
    return text.length>110?`<details class="field-value"><summary>View value (${text.length.toLocaleString()} characters)</summary><pre>${esc(text)}</pre></details>`:`<code>${esc(text===''?'""':text)}</code>`;
  };
  const group=state.parallelByEvent.get(event.id);
  $('#event-parallel').hidden=!group;
  if(group){$('#event-parallel-title').textContent=`Parallel ${group.display_label||group.label} · Inferred`;$('#event-parallel-summary').textContent=`${group.tool_count} tools · peak ${group.max_concurrency} at once · ${duration(group.overlap_ms)} overlapping time. ${state.parallelNote}`;$('#event-parallel-json').textContent=JSON.stringify(group,null,2);}
  $('#event-title').textContent=event.name;
  $('#event-summary').textContent=description.summary;
  $('#event-basis').textContent=event.attributes?.basis?`Method: ${event.attributes.basis}`:'';
  $('#event-basis').hidden=!event.attributes?.basis;
  $('#event-fields').innerHTML=description.fields.map(f=>`<tr><th scope="row"><code>${esc(f.field)}</code>${valueHTML(f.value)}</th><td><span class="origin ${f.origin}">${labels[f.origin]}</span></td><td>${esc(f.explanation)}</td></tr>`).join('');
  $('#event-evidence').hidden=!description.evidence;
  $('#event-evidence').open=false;
  $('#event-evidence-title').textContent=description.evidence?.label||'Preserved source data';
  $('#event-evidence-json').textContent=description.evidence?JSON.stringify(description.evidence.data,null,2):'';
  $('#event-source-note').textContent=description.evidence?'The preserved source data below is separate from AgentBoard’s normalized event.':'Field origins describe AgentBoard’s normalization. Open Raw JSONL for verified source lines when available.';
  $('#event-json').textContent=JSON.stringify(event,null,2);
  loadEventRaw(event);
  setEventView(eventView);
  $('#event-dialog').showModal();
  $('#event-dialog').scrollTop=0;
}
function sourceLabel(source) { return ({codex_jsonl:'Rollout',codex_item:'Item timing'})[source]||source; }
function eventContext(event) {
  const match=event.unified;
  if(match?.match_status==='matched')return 'Rollout + item timing · exact identity match';
  if(match?.match_status==='child')return sourceLabel(event.source)+' · child operation (recorded parent)';
  return sourceLabel(event.source)+(match?' · '+(match.match_status==='ambiguous'?'ambiguous match':'unmatched identity'):'');
}
function parallelBadge(event) {
  const group=state.parallelByEvent.get(event.id);
  return group?`<span class="parallel-badge" title="Inferred from recorded interval overlap; peak ${group.max_concurrency} tools at once">Parallel ${esc(group.display_label||group.label)}</span>`:'';
}
function renderParallelSummary(events) {
  const panel=$('#parallel-summary');
  panel.hidden=state.tab==='inputs';
  const shown=new Set(events.map(e=>e.id));
  const groups=state.parallelGroups;
  const visible=groups.map(g=>({group:g,count:g.event_ids.filter(id=>shown.has(id)).length})).filter(g=>g.count);
  const view=$('#source').value==='unified'?'view':'source';
  const title=groups.length?`${groups.length} parallel group${groups.length===1?'':'s'} in this ${view}`:`No parallel groups detected in this ${view}`;
  const missing=!(state.stats?.counts.tool)?'No tool events are recorded in this source. ':'';
  panel.innerHTML=`<strong>${title}</strong><p>${missing}Inferred from overlapping recorded tool intervals. Groups use all recorded intervals for each source, including events outside this view.${groups.length?'':' No detected overlap does not establish that all calls ran sequentially.'}</p>`+(groups.length&&!visible.length?'<p>No group members match the loaded view.</p>':'')+`<div class="parallel-cards">${visible.map(({group:g,count})=>`<div class="parallel-card"><strong>Parallel ${esc(g.display_label||g.label)}</strong><span>${g.tool_count} tools · peak ${g.max_concurrency} at once</span><span>${duration(g.overlap_ms)} overlapping time${count<g.tool_count?` · ${count}/${g.tool_count} tools shown`:''}</span></div>`).join('')}</div>`;
}
function renderEvents() {
  $('#export').textContent=state.tab==='inputs'?'↓ Export inputs':$('#source').value==='unified'?'↓ Export source records':'↓ Export JSONL';
  const events=state.tab==='timeline'&&$('#source').value!=='unified'?state.events.filter(e=>['llm','tool','user_wait'].includes(e.kind)||e.end_time!=null):state.events;
  renderParallelSummary(events);
  if(!events.length){
    const counts=state.stats?.counts||{};
    const total=Object.values(counts).reduce((sum,count)=>sum+count,0);
    const operations=state.stats?.timeline_count||0;
    const noOperations=state.tab==='timeline'&&!operations;
    const title=noOperations?'No timed events recorded':'No matching events';
    const message=noOperations?`${total} event${total===1?'':'s'} in this source; none have a recorded interval or an open LLM, tool, or user-wait operation. Open All events to inspect the recorded data.`:$('#event-search').value?'Clear or change the event filter, or choose another source.':state.tab==='inputs'?'No user inputs are recorded in this source. Choose another source or open All events.':'No events are recorded in this source.';
    $('#event-list').innerHTML=`<div class="empty"><h2>${title}</h2><p>${message}</p></div>`;return;
  }
  if(state.tab==='timeline') {
    const sorted=[...events].sort((a,b)=>Number(timestampNs(a.start_time)-timestampNs(b.start_time)));
    const start=timestampNs(sorted[0].start_time);let end=start+1n;for(const e of sorted){const n=timestampNs(e.end_time||e.start_time);if(n>end)end=n;}
    const range=Number(end-start);
    $('#event-list').innerHTML='<div class="timeline-head"><span>OPERATION</span><span>RELATIVE TIMELINE · LOADED EVENTS</span><span>DURATION</span></div>'+sorted.map(e=>{const a=Number(timestampNs(e.start_time)-start)/range*100;const ms=e.end_time?Number(timestampNs(e.end_time)-timestampNs(e.start_time))/1e6:null;const width=e.end_time?Math.max(.3,Number(timestampNs(e.end_time)-timestampNs(e.start_time))/range*100):.3;return `<div tabindex="0" class="timeline-row${e.unified?.match_status==='child'?' child-operation':''}" data-event="${esc(e.id)}"><div class="event-name"><i class="dot ${e.kind}"></i>${e.unified?.match_status==='child'?'↳ ':''}${esc(e.name)}${['user','assistant'].includes(e.kind)?`<span class="message-preview">${esc((e.text||'').slice(0,180))}</span>`:''}<small>${e.kind==='event'?'other span · ':''}${$('#source').value==='unified'?esc(eventContext(e))+' · ':''}${esc(e.timing)}${e.status!=='ok'?' · '+esc(e.status):''}</small>${parallelBadge(e)}</div><div class="track"><div class="bar ${e.kind}${e.end_time==null?' instant':''}" style="left:${Math.min(a,99.7)}%;width:${Math.min(width,100-a)}%"></div></div><span class="event-duration">${e.end_time==null&&!['llm','tool','user_wait'].includes(e.kind)?'event':duration(ms)}</span></div>`;}).join('');
  } else if(state.tab==='inputs') {
    $('#event-list').innerHTML=events.map((e,i)=>`<article class="input-card"><div class="input-card-head"><span>INPUT ${i+1} · ${date(e.start_time)}</span>${state.features.includes('replay')?`<button class="button small" data-branch="${esc(e.id)}">Branch & edit ↗</button>`:''}</div><pre>${esc(e.text||'[Prompt content not recorded]')}</pre></article>`).join('');
  } else {
    $('#event-list').innerHTML=events.map(e=>`<div tabindex="0" class="event-row" data-event="${esc(e.id)}"><span class="pill">${esc(e.kind)}</span><span>${esc(e.name)} ${parallelBadge(e)}${$('#source').value==='unified'?`<small>${esc(eventContext(e))}</small>`:''}</span><span>${date(e.start_time)}</span></div>`).join('');
  }
  document.querySelectorAll('[data-event]').forEach(el=>{const open=()=>inspectEvent(state.events.find(e=>e.id===el.dataset.event));el.onclick=open;el.onkeydown=e=>{if(e.key==='Enter')open();};});
  document.querySelectorAll('[data-branch]').forEach(el=>el.onclick=()=>{state.input=el.dataset.branch;$('#replacement').value=state.events.find(e=>e.id===state.input).text;$('#native-plan').hidden=state.session.agent!=='codex';$('#branch-dialog').showModal();});
}
async function route() { const sid=decodeURIComponent(location.hash.slice(1));if(sid){await openSession(sid);}else{state.request++;state.sid=null;$('#list-view').hidden=false;$('#detail-view').hidden=true;$('#breadcrumbs').textContent='Workspace / Sessions';await loadSessions();} }
$('#back').onclick=()=>{location.hash='';};
$('#home').onclick=run(async()=>{state.listKind='session';state.offset=0;document.querySelectorAll('[data-list-kind]').forEach(b=>b.classList.toggle('active',b.dataset.listKind==='session'));if(location.hash)location.hash='';else await loadSessions();});
document.querySelectorAll('[data-event-view]').forEach(button=>button.onclick=()=>setEventView(button.dataset.eventView,true));
$('#refresh').onclick=run(loadSessions);$('#prev').onclick=run(async()=>{state.offset=Math.max(0,state.offset-20);await loadSessions();});$('#next').onclick=run(async()=>{state.offset=state.next;await loadSessions();});
$('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(run(async()=>{state.offset=0;await loadSessions();}),250);};
document.querySelectorAll('[data-list-kind]').forEach(button=>button.onclick=run(async()=>{state.listKind=button.dataset.listKind;state.offset=0;document.querySelectorAll('[data-list-kind]').forEach(b=>b.classList.toggle('active',b===button));await loadSessions();}));
$('#category').onchange=run(async()=>{state.offset=0;await loadSessions();});
$('#import').onclick=()=>$('#file').click();
$('#file').onchange=run(async()=>{const files=[...$('#file').files];let last;for(const file of files){const result=await json('/api/v1/import/codex',{method:'POST',headers:{'Content-Type':'application/x-ndjson'},body:file});last=result.session_ids[0];notice(`Imported ${file.name}: ${result.inserted_events} new events`);}$('#file').value='';await loadSessions();if(last)location.hash=encodeURIComponent(last);});
$('#demo').onclick=run(()=>busy($('#demo'),async()=>{const response=await fetch('/static/demo.jsonl');if(!response.ok)throw Error('Demo fixture unavailable');const result=await json('/api/v1/import/codex',{method:'POST',headers:{'Content-Type':'application/x-ndjson'},body:await response.text()});notice('Demo imported. Explore the timeline or branch from a user input.');location.hash=encodeURIComponent(result.session_ids[0]);}));
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=run(async()=>{state.tab=button.dataset.tab;document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b===button));await loadEvents();}));
$('#source').onchange=run(()=>loadEvents());$('#more').onclick=run(()=>loadEvents(true));$('#event-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(run(()=>loadEvents()),250);};
$('#classify').onclick=run(()=>busy($('#classify'),async()=>{const result=await json(`/api/v1/sessions/${encodeURIComponent(state.sid)}/classify`,{method:'POST'});state.session.classification=result;renderClassification(state.session);notice(result.dummy?`Classified with dummy model: ${result.fallback_reason}`:'Session classified');}));
$('#export').onclick=run(()=>busy($('#export'),async()=>{const response=await api(`/api/v1/sessions/${encodeURIComponent(state.sid)}/export${state.tab==='inputs'?'?kind=user':''}`);download(await response.text(),'agentboard-events.jsonl');}));
async function branch(mode){const replacement=$('#replacement').value;if(!replacement.trim())throw Error('Enter a replacement input');const result=await json(`/api/v1/sessions/${encodeURIComponent(state.sid)}/${mode}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({input_id:state.input,replacement})});$('#branch-dialog').close();if(mode==='codex-plan'){download(JSON.stringify(result,null,2),'codex-branch-plan.json','application/json');notice('Plan downloaded. Use agentboard resume to execute locally.');}else{notice(result.dummy?'Replay completed with dummy model':'Replay completed');location.hash=encodeURIComponent(result.session_id);}}
$('#replay').onclick=run(()=>busy($('#replay'),()=>branch('replay')));$('#native-plan').onclick=run(()=>busy($('#native-plan'),()=>branch('codex-plan')));
document.querySelectorAll('.close').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('#token').onclick=()=>{$('#api-token').value=sessionStorage.getItem('agentboard-token')||'';$('#token-dialog').showModal();};
$('#save-token').onclick=run(async()=>{sessionStorage.setItem('agentboard-token',$('#api-token').value);$('#token-dialog').close();await init();});
window.onhashchange=run(route);
async function init(){try{const config=await json('/api/v1/config');state.features=config.features;$('#endpoint-hint').textContent=location.host;if(!config.otlp_enabled)$('#ingestion-hint').textContent='Use explicit imports or a database snapshot to inspect a fixed dataset. Live telemetry is disabled here.';$('#environment-note').hidden=config.environment!=='dev';$('#environment-note').textContent=`DEV · ${config.database_name} · Live telemetry ${config.otlp_enabled?'enabled':'disabled — fixed imports and snapshots'}`;$('#connection').textContent=config.environment==='dev'?'● Connected · DEV':'● Connected';await route();}catch(e){$('#connection').textContent='○ Connection required';throw e;}}
run(init)();

$('#export-raw').onclick=run(()=>busy($('#export-raw'),async()=>{const response=await api(`/api/v1/sessions/${encodeURIComponent(state.sid)}/raw`);download(await response.arrayBuffer(),'rollout.jsonl');}));
