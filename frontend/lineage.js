// The backend owns field mappings. Unified rows select the same text source as compose().
function lineageFields(event, results) {
  const primary=results.find(result=>result.event_id===event.id)||results[0];
  const fields=Object.entries(primary.fields).map(([path,description])=>({path,...description}));
  if(event.unified?.match_status==='matched') {
    const original=event.unified.records[0];
    if(original.text) {
      const source=results.find(result=>result.event_id===original.id);
      const index=fields.findIndex(field=>field.path==='/text');
      fields[index]={path:'/text',...source.fields['/text'],method:'Unified view selects rollout call text. '+source.fields['/text'].method};
    }
  }
  const records=new Map(results.flatMap(result=>result.records.map(record=>[`${record.import_id}:${record.line_number}`,record])));
  const archives=new Map(results.flatMap(result=>result.archives.map(archive=>[archive.id,archive])));
  return {fields,records,archives};
}

function lineageTable(event, results, escapeHTML) {
  const {fields,records,archives}=lineageFields(event,results);
  const valueHTML=value=>{
    const text=typeof value==='string'?value:JSON.stringify(value,null,2)??'null';
    return text.length>110?`<details class="field-value"><summary>View value (${text.length.toLocaleString()} characters)</summary><pre>${escapeHTML(text)}</pre></details>`:`<code>${escapeHTML(text===''?'""':text)}</code>`;
  };
  const labels={normalized:'Normalized',calculated:'Calculated',inferred:'Inferred',model_generated:'Model-generated',unknown:'Unknown'};
  return fields.map(field=>{
    const origin=Object.hasOwn(labels,field.origin)?field.origin:'unknown';
    const evidence=field.sources.map(source=>{
      const archive=archives.get(source.import_id),record=records.get(`${source.import_id}:${source.line_number}`);
      return `<details class="field-source"><summary>Archive #${escapeHTML(source.import_id)} · Line ${escapeHTML(source.line_number)} · <code>${escapeHTML(source.pointer||'(whole record)')}</code></summary><p>${escapeHTML(source.role)} · ${source.present?'Present':'Absent in source'} · ${escapeHTML(archive?.mapping_version)} · SHA-256 ${escapeHTML(archive?.sha256)}</p><pre>${escapeHTML(record?.text||'Source line unavailable')}</pre></details>`;
    }).join('');
    const unavailable=field.available?'':' <span class="muted">Source evidence unavailable.</span>';
    return `<tr><th scope="row"><code>${escapeHTML(field.path)}</code>${valueHTML(field.value)}</th><td><span class="origin ${origin}">${labels[origin]}</span></td><td>${escapeHTML(field.method)}${unavailable}${evidence}</td></tr>`;
  }).join('');
}
