// The API uses canonical UTC RFC 3339 strings with nine fractional digits.
// Keep nanoseconds exact until converting a relative duration for display.
function timestampNs(value) {
  const match=/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{9})Z$/.exec(value);
  if(!match) throw Error('Expected a canonical RFC 3339 timestamp; restart AgentBoard and refresh the page.');
  return BigInt(Date.parse(match[1]+'Z'))*1000000n+BigInt(match[2]);
}

// Explain the built-in adapters' field mappings. This describes transformations;
// it does not reconstruct raw records that were not retained during import.
function describeEvent(event) {
  const attrs=event.attributes||{};
  const rollout=event.source==='codex_jsonl', item=event.source==='codex_item';
  const otel=['otlp_trace','otlp_log'].includes(event.source), replay=event.source==='replay';
  const known=rollout||item||otel||replay;
  const inferred=rollout&&(event.kind==='llm'||(event.kind==='user_wait'&&attrs.wait_type==='between_turns'));
  const fields=[];
  const add=(field,value,origin,explanation)=>fields.push({field,value,origin,explanation});
  const missing=(value)=>value===null||value===undefined||value==='';
  const mapped=(field,origin,explanation)=>add(field,event[field],missing(event[field])?'unavailable':origin,missing(event[field])?'No value stored for this field.':explanation);
  let summary='Field origins are not known for this source. The JSON below is the stored normalized event.';
  if(inferred) summary=event.kind==='llm'
    ? 'AgentBoard inferred LLM activity between log records. The timestamps come from the log, but selecting them as an LLM interval is an inference. This is not a recorded model request or a measurement of model latency.'
    : 'AgentBoard inferred a user wait from turn completion to the next prompt. The timestamps come from the log, but the gap may include idle time.';
  else if(rollout) summary=event.timing==='estimated'
    ? 'This interval pairs recorded call and result timestamps. Elapsed time is an estimate of the operation lifetime and may include scheduling, approvals, or delivery overhead.'
    : 'This event was normalized from rollout records. AgentBoard adds classifications, labels, identifiers, and import bookkeeping.';
  else if(item) summary='Timing comes from a recorded Codex item lifetime or reported tool duration. LLM item timing covers output streaming, not full model latency.';
  else if(otel) summary='This event was normalized from OpenTelemetry data. A log start time may be calculated from its timestamp and reported duration; classifications and status are assigned by AgentBoard.';
  else if(replay) summary='AgentBoard created this event during conversation replay. Its timestamps are local replay times, not timestamps from the original session.';

  if(known) {
    let start='Recorded timestamp normalized to UTC RFC 3339.';
    let end='Recorded result timestamp normalized to UTC RFC 3339; clamped to the call start if earlier.';
    let startOrigin='normalized', endOrigin='normalized';
    if(inferred) {
      start='Source timestamp normalized to RFC 3339. Choosing it as an interval boundary is separately an inference.';
      end='Source timestamp normalized to RFC 3339. Choosing it as an interval boundary is separately an inference.';
    } else if(item) {
      start=attrs.basis==='Codex reported tool duration'||attrs.basis?.startsWith('Codex reported tool duration;')
        ? 'Calculated as completed_at_ms minus the item’s reported duration.'
        : 'Recorded started_at_ms normalized from epoch milliseconds to UTC RFC 3339.';
      if(start.startsWith('Calculated')) startOrigin='calculated';
      end='Recorded completed_at_ms normalized from epoch milliseconds to UTC RFC 3339.';
    } else if(event.source==='otlp_trace') {
      start='Recorded startTimeUnixNano normalized to UTC RFC 3339.';
      end='Recorded endTimeUnixNano normalized to UTC RFC 3339.';
    } else if(event.source==='otlp_log') {
      start=event.end_time?'Calculated as the log timestamp minus its reported duration.':'Recorded timeUnixNano or observedTimeUnixNano.';
      if(event.end_time) startOrigin='calculated';
      end='Recorded timeUnixNano or observedTimeUnixNano, used as the end of the operation.';
    } else if(replay) {
      start=end='Local clock measurement recorded by AgentBoard during replay.';
    }
    if(rollout&&attrs.end_time_clamped) {endOrigin='calculated';end='max(result timestamp, call start); the result timestamp preceded the call.';}
    else if(rollout&&!inferred&&event.end_time===event.start_time) {endOrigin='unknown';end='This older zero-length interval may have used the result timestamp or clamped an earlier result to the call start; the original result timestamp was not retained.';}
    mapped('start_time',startOrigin,start);
    mapped('end_time',endOrigin,end);
    const ms=event.end_time==null?null:Number(timestampNs(event.end_time)-timestampNs(event.start_time))/1e6;
    add('duration_ms (calculated)',ms,ms===null?'unavailable':'calculated',ms===null?'No end timestamp; duration is unavailable.':'Difference between the RFC 3339 instants, expressed in milliseconds. Calculation does not make estimated timing a measurement.');
    mapped('timing','inferred','AgentBoard’s quality label for this timing method; not a field copied from the source.');
    mapped('kind','inferred','AgentBoard’s event category, assigned by the adapter.');
    const record=attrs.otel?.record;
    const sourceName=item||otel&&(record?.name||record?.eventName||attrs['event.name']||record?.body?.stringValue)
      ||rollout&&(['tool','user_wait'].includes(event.kind)&&!inferred||['task_complete','turn_aborted','compacted','thread_rolled_back'].includes(event.name));
    const ambiguousName=rollout&&event.name==='tool'||otel&&!record;
    mapped('name',ambiguousName?'unknown':sourceName?'normalized':'inferred',ambiguousName?'The retained data does not distinguish a source name from an application fallback.':sourceName?'Name selected from a source tool name, event name, body, or item type.':'Display label assigned by AgentBoard.');
    mapped('status','inferred','AgentBoard maps exit codes, errors, lifecycle records, or missing results to a status; otherwise defaults to ok. “ok” is not a confidence score.');
    if(inferred&&event.text==='') add('text','','inferred','Empty placeholder. This inferred interval contains no recorded response text.');
    else if(replay) {
      const origin=Object.hasOwn(attrs,'retained_context')?'normalized':['inferred','model_generated'].includes(attrs.content_origin)?attrs.content_origin:'unknown';
      mapped('text',origin,origin==='model_generated'?'Response created by the replay model.':origin==='normalized'?'Retained transcript text or the supplied replacement input.':origin==='inferred'?'Dummy placeholder created by application logic; no model produced it.':'Older replay record does not identify whether a model or dummy produced this text.');
    } else mapped('text','normalized','Source content extracted or joined into text; command argument lists are shell-quoted.');
    const correlated=otel&&(event.trace_id||['session.id','conversation.id','gen_ai.conversation.id','thread.id'].some(key=>attrs[key]));
    const traceInference=otel&&['unique conversation identity in trace','ancestor span conversation identity'].includes(attrs.agentboard_session_association?.basis);
    mapped('session_id',traceInference?'inferred':replay||otel&&!correlated?'calculated':'normalized',traceInference?'Associated using an ancestor span or the sole conversation identity in this trace; see the association basis. Later conflicting evidence can change this inference.':rollout||item?'Session ID read from session_meta.':replay?'Hash of the parent session, selected input, and replay start time.':correlated?'Selected from conversation evidence or an unattributed trace ID; see the association metadata when present.':'Fallback identifier calculated by hashing resource attributes.');
    mapped('turn_id','normalized','Recorded turn ID, possibly propagated from an earlier turn context or lifecycle record.');
    for(const key of ['trace_id','span_id','parent_span_id']) mapped(key,otel?'normalized':'unknown',otel?'Identifier from the decoded telemetry record.':'No built-in mapping for this identifier in this source.');
  } else {
    for(const key of ['start_time','end_time','timing','kind','name','status','text','session_id','turn_id','trace_id','span_id','parent_span_id']) mapped(key,'unknown','This source has no built-in field-origin description.');
  }
  mapped('id',known?'calculated':'unknown',known?'Stable hash calculated from source identifiers or record data; not the original item or span ID.':'Identifier supplied by the adapter; its origin is unknown.');
  mapped('row_id','inferred','SQLite row ID used for pagination.');
  mapped('sequence',known?'calculated':'unknown',rollout||item?'JSONL line number where the adapter emits this event. For inferred spans, this is not necessarily the start record.':known?'Position assigned while processing the telemetry batch or replay.':'Sequence supplied by the adapter; its origin is unknown.');
  mapped('source','inferred','AgentBoard’s label for the adapter and input format.');
  for(const [key,value] of Object.entries(attrs)) {
    let origin='unknown', explanation='Origin is not described for this attribute.';
    if(rollout) {
      if(['basis','wait_type','end_time_clamped'].includes(key)) {origin='inferred';explanation='Explanation or classification added by AgentBoard.';}
      else if(['call_id','output','info','previous_turn_id'].includes(key)) {origin='normalized';explanation=key==='previous_turn_id'?'Recorded ID propagated from the preceding completed turn.':'Extracted from rollout payloads; structured outputs may be serialized as text.';}
      else if(key==='reported_wall_time_ms') {origin='calculated';explanation='Parsed from the tool output’s Wall time text and converted from seconds to milliseconds.';}
    } else if(item) {
      if(key==='item') {origin='normalized';explanation='Preserved payload.item object. This is only the item, not the full rollout record.';}
      else if(['basis','wait_type','end_time_clamped'].includes(key)) {origin='inferred';explanation='Explanation or classification added by AgentBoard.';}
    } else if(otel) {
      origin='normalized';explanation=key==='otel'?'Preserved decoded record, resource, and scope data; not the original wire bytes.':'Attribute decoded from telemetry resource or record data.';
      if(event.kind==='user_wait'&&['basis','wait_type'].includes(key)) {origin='inferred';explanation='Input-request classification added by AgentBoard.';}
      if(['agentboard_session_identity','agentboard_session_association'].includes(key)) {origin='inferred';explanation='Session-association evidence and mapping decisions added by AgentBoard. The decoded source remains in attributes.otel.';}
    } else if(replay) {origin='inferred';explanation='Metadata added by AgentBoard during replay.';}
    add(`attributes.${key}`,value,missing(value)?'unavailable':origin,missing(value)?'No value stored for this attribute.':explanation);
  }
  let evidence=item&&attrs.item?{label:'Preserved Codex item',data:attrs.item}:otel&&attrs.otel?{label:'Preserved decoded telemetry',data:attrs.otel}:null;
  if(event.unified) {
    add('unified',event.unified,'inferred','Read-only timeline composition metadata. Identity matches require a unique call ID in the same session and turn. No timestamp or name matching.');
    if(event.unified.match_status==='matched') {
      summary='One timeline row combines an exact identity match: recorded item timing with rollout call content. Both original normalized events are preserved below.';
      const textField=fields.find(f=>f.field==='text');
      if(textField)textField.explanation='Rollout call text when available, otherwise recorded item command text.';
      evidence={label:'Original normalized records before composition',data:event.unified.records};
    }
  }
  return {summary,fields,evidence};
}
