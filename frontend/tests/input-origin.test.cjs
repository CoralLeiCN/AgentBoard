const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const source=readFileSync('frontend/app.js','utf8');

function setup(session,events) {
  const nodes=new Map();
  const node=id=>{
    if(!nodes.has(id))nodes.set(id,{textContent:'',innerHTML:'',hidden:false,value:'',options:[]});
    return nodes.get(id);
  };
  node('#source').options=[{value:'codex_jsonl',disabled:false}];
  const counts={};
  events.forEach(event=>counts[event.kind]=(counts[event.kind]||0)+1);
  const context=vm.createContext({URLSearchParams,Date,Map,Set,clearTimeout,setTimeout,
    localStorage:{getItem:()=>null},sessionStorage:{getItem:()=>null},
    document:{querySelector:node,querySelectorAll:()=>[]},
    fetch:async path=>({ok:true,json:async()=>{
      const url=new URL(path,'http://fixture');
      if(url.pathname.endsWith('/stats'))return {sources:['codex_jsonl'],counts,timing:[],elapsed_ms:0,note:'Fixture'};
      if(url.pathname.endsWith('/raw-imports'))return {items:[]};
      if(url.pathname.endsWith('/parallel-groups'))return {items:[],note:''};
      if(url.pathname.endsWith('/events'))return {
        items:events.filter(e=>!url.searchParams.has('kind')||e.kind===url.searchParams.get('kind')),next_cursor:null
      };
      return session;
    }})});
  vm.runInContext(source.slice(0,source.indexOf("$('#back').onclick")),context);
  vm.runInContext("state.tab='inputs';state.features=['replay']",context);
  return {context,node};
}

test('input view uses the human filter, exposes inspection, and keeps context in All events',async()=>{
  const session={id:'s',agent:'codex',title:'Build it',identity_kind:'session',metadata:{},input_origin:{origin:'unknown'}};
  const base={session_id:'s',source:'codex_jsonl',start_time:'2026-09-07T12:00:01.000000000Z'};
  const {context,node}=setup(session,[
    {...base,id:'context',kind:'event',name:'Injected context',text:'<environment_context>synthetic</environment_context>'},
    {...base,id:'human',kind:'user',name:'User input',text:'Build it',attributes:{input_attribution:{origin:'human'}}}
  ]);
  await context.openSession('s');
  assert.equal(node('#input-count').textContent,1);
  assert.match(node('#input-quality').textContent,/inferred/);
  assert.match(node('#event-list').innerHTML,/data-branch="human"/);
  assert.match(node('#event-list').innerHTML,/data-event="human"/);
  assert.doesNotMatch(node('#event-list').innerHTML,/context/);
  vm.runInContext("state.tab='events'",context);
  await context.loadEvents();
  assert.match(node('#event-list').innerHTML,/Injected context/);
  assert.doesNotMatch(node('#event-list').innerHTML,/data-branch/);
});

test('reviewer sessions display a safely escaped parent link and no branch controls',async()=>{
  const parent='parent<script>';
  const {context,node}=setup({id:'review',agent:'codex',title:'Untitled session',identity_kind:'session',metadata:{},
    input_origin:{origin:'internal',parent_session_id:parent}},[
    {id:'internal',kind:'event',name:'Internal input',text:'Review this',start_time:'2026-09-07T12:00:01.000000000Z'}
  ]);
  await context.openSession('review');
  assert.equal(node('#input-count').textContent,0);
  assert.match(node('#session-agent').textContent,/INTERNAL AGENT\/REVIEWER/);
  assert.equal(node('#detail-identity-note').hidden,false);
  assert.match(node('#detail-identity-note').innerHTML,/href="#parent%3Cscript%3E"/);
  assert.match(node('#detail-identity-note').innerHTML,/parent&lt;script&gt;/);
  assert.doesNotMatch(node('#event-list').innerHTML,/data-branch/);
});
