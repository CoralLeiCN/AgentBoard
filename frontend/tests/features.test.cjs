const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');

const html=readFileSync('frontend/index.html','utf8');
const app=readFileSync('frontend/app.js','utf8').replace('run(init)();','');
const event={id:'tool-1',session_id:'session-1',source:'codex_jsonl',kind:'tool',name:'Run checks',
  start_time:'2026-09-07T12:00:00.000000000Z',end_time:'2026-09-07T12:00:01.000000000Z',
  timing:'measured',status:'ok',attributes:{}};

function setup(features=[],{adapters=['codex'],source='codex_jsonl',savedView='raw',agent='codex'}={}) {
  function element(attrs={}) {
    return {attrs,dataset:Object.fromEntries(Object.entries(attrs).filter(([key])=>key.startsWith('data-')).map(([key,value])=>[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase()),value])),
      hidden:'hidden' in attrs,value:'',textContent:'',innerHTML:'',children:[],files:[],disabled:false,
      classList:{toggle(){}},setAttribute(name,value){this.attrs[name]=value;},
      replaceChildren(){this.children=[];},append(...nodes){this.children.push(...nodes);},
      showModal(){this.open=true;},close(){this.open=false;},click(){this.clicked=true;},
      querySelectorAll(){return [];}};
  }
  function parse(markup) {
    return [...markup.matchAll(/<\w+\b([^>]*)>/g)].map(([,text])=>element(Object.fromEntries(
      [...text.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map(([,key,value])=>[key,value||'']))));
  }
  const staticNodes=parse(html),dynamic=new Map();
  function all() {
    for(const node of staticNodes)if(node._markup!==node.innerHTML){node._markup=node.innerHTML;dynamic.set(node,parse(node.innerHTML));}
    return [...staticNodes,...[...dynamic.values()].flat()];
  }
  const node=id=>all().find(item=>item.attrs.id===id.replace(/^#/,''))||null;
  const query=selector=>{
    const match=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
    return match?all().filter(item=>match[1] in item.attrs&&(match[2]===undefined||item.attrs[match[1]]===match[2])):[];
  };
  const config={features,adapters,environment:'dev',database_name:'fixture.sqlite3',
    feature_catalog:features.map(name=>({name,description:`Enable ${name}`,enabled:true,requires:[]}))};
  const current={...event,source};
  const session={id:'session-1',agent,title:'Saved session',identity_kind:'session',metadata:{},started_at:event.start_time,
    classification:{category:'coding',reason:'Previously classified',model:'old',dummy:false}};
  const stats={sources:[source],counts:{tool:1,user:1},timing:[],elapsed_ms:1000,timeline_count:1,note:'Fixture'};
  const calls=[];
  const context=vm.createContext({URLSearchParams,Date,Map,Set,Blob,
    URL:{createObjectURL:()=>'',revokeObjectURL(){}},
    localStorage:{getItem:()=>savedView,setItem(){}},sessionStorage:{getItem:()=>null,setItem(){}},
    setTimeout:()=>0,clearTimeout(){},location:{hash:'',host:'localhost:4319'},window:{},
    document:{querySelector:node,querySelectorAll:query,createElement:()=>element()},
    usageHTML:()=>'<p>Usage loaded</p>',
    fetch:async(path,options)=>{
      calls.push({path,options});
      const url=new URL(path,'http://fixture');
      let data;
      if(url.pathname==='/api/v1/config')data=config;
      else if(url.pathname==='/api/v1/sessions')data={items:[session],next_offset:null,total:1,identity_counts:{session:1}};
      else if(url.pathname==='/api/v1/sessions/session-1')data=session;
      else if(url.pathname.endsWith('/stats'))data=stats;
      else if(url.pathname.endsWith('/events'))data={items:[current],next_cursor:null};
      else if(url.pathname.endsWith('/unified'))data={items:[current],next_cursor:null,stats,parallel:null};
      else if(url.pathname.endsWith('/parallel-groups'))data={items:[],note:'Fixture'};
      else if(url.pathname.endsWith('/raw-imports'))data={items:[{id:1}]};
      else if(url.pathname.endsWith('/raw'))data={available:false,lines:[],reason:'No mapping'};
      else if(url.pathname.endsWith('/usage'))data={archive:{id:1},pricing:{models:{}},next_cursor:null};
      else if(url.pathname.endsWith('/codex-plan'))data={mode:'native'};
      else if(url.pathname.endsWith('/replay'))data={session_id:'replayed',dummy:true};
      else throw Error(`Unexpected request: ${path}`);
      return {ok:true,json:async()=>data,text:async()=>JSON.stringify(data)};
    }});
  vm.runInContext(readFileSync('frontend/provenance.js','utf8'),context);
  vm.runInContext(app,context);
  return {context,node,query,calls,config,event:current};
}

test('all optional features can be disabled while saved sessions, timelines and event details remain usable',async()=>{
  const {context,node,query,calls,event}=setup();
  await context.init();
  assert.equal(node('#connection').textContent,'● Connected · DEV');
  assert.match(node('#sessions').innerHTML,/Saved session/);
  assert.equal(node('#category').hidden,true);
  assert.ok(query('[data-feature]').every(item=>item.hidden));
  await context.openSession('session-1');
  assert.equal(node('#source').value,'codex_jsonl');
  assert.match(node('#event-list').innerHTML,/Run checks/);
  assert.equal(node('#parallel-summary').hidden,true);
  assert.equal(node('#classification').hidden,true);
  context.inspectEvent(event);
  assert.equal(node('#event-dialog').open,true);
  assert.equal(node('#event-table-view').hidden,false,'Disabled raw archive overrides the saved raw view');
  assert.match(node('#event-fields').innerHTML,/start_time/);
  assert.doesNotMatch(node('#event-source-note').textContent,/Raw JSONL/);
  await Promise.resolve();
  assert.ok(calls.every(({path})=>/^\/api\/v1\/(config|sessions(?:\/session-1(?:\/events|\/stats)?)?)(?:\?|$)/.test(path)),JSON.stringify(calls));
  assert.equal(new URL(calls.find(({path})=>path.startsWith('/api/v1/sessions?')).path,'http://fixture').searchParams.has('category'),false);
  const count=calls.length;
  for(const id of ['demo','classify','classify-page','export','export-raw','usage-refresh','usage-more','usage-export','replay','native-plan'])await node(id).onclick();
  await query('[data-tab="inputs"]')[0].onclick();
  assert.equal(calls.length,count,'Disabled actions must not request optional endpoints');
});

test('the source picker supports sources contributed by adapters without requiring an ingestion feature',async()=>{
  const {context,node,calls}=setup([],{source:'custom/source&signal',agent:'custom',adapters:[]});
  await context.init();await context.openSession('session-1');
  assert.equal(node('#source').value,'custom/source&signal');
  assert.match(node('#source').innerHTML,/custom\/source&amp;signal/);
  assert.ok(calls.filter(({path})=>path.includes('/stats?')).every(({path})=>new URL(path,'http://fixture').searchParams.get('source')==='custom/source&signal'));
  assert.equal(node('#import').hidden,true);
});

test('unified timeline operates independently of parallel group inference',async()=>{
  const {context,node,calls}=setup(['unified_timeline']);
  await context.init();await context.openSession('session-1');
  assert.equal(node('#source').value,'unified');
  assert.ok(calls.some(({path})=>path.includes('/unified?')));
  assert.equal(node('#parallel-summary').hidden,true);
  assert.ok(calls.every(({path})=>!path.includes('/parallel-groups')));
  assert.match(node('#event-list').innerHTML,/Run checks/);
});

test('archive and usage inspection do not expose exports when export is disabled',async()=>{
  const {context,node,calls,event}=setup(['raw_archive','token_usage']);
  await context.init();await context.openSession('session-1');await context.loadUsage();
  context.inspectEvent(event);await Promise.resolve();
  assert.equal(node('#usage-panel').hidden,false);
  assert.equal(node('#usage-export').hidden,true);
  assert.equal(node('#export-raw').hidden,true);
  assert.ok(calls.some(({path})=>path.endsWith('/events/tool-1/raw')));
  assert.ok(calls.every(({path})=>!path.includes('/raw-imports')&&!path.includes('/lineage')&&!path.includes('/export')));
});

test('Codex import controls require both the import feature and the Codex adapter',async()=>{
  for(const [features,adapters,hidden] of [[['import'],['codex'],false],[['import'],['custom'],true],[[],['codex'],true]]) {
    const {context,node}=setup(features,{adapters});await context.init();
    assert.equal(node('#import').hidden,hidden);
    assert.equal(node('#demo').hidden,hidden);
  }
});

test('native resume and replay expose only their respective branch actions',async()=>{
  for(const feature of ['native_resume','replay']) {
    const {context,node,query,calls}=setup(['inputs',feature]);
    await context.init();await context.openSession('session-1');
    vm.runInContext("state.tab='inputs';state.events=[{...state.events[0],kind:'user',text:'Original input'}];renderEvents();",context);
    assert.match(node('#event-list').innerHTML,/data-branch/);
    query('[data-branch]')[0].onclick();
    assert.equal(node('#native-plan').hidden,feature!=='native_resume');
    assert.equal(node('#replay').hidden,feature!=='replay');
    node('#replacement').value='Replacement input';
    await node(feature==='native_resume'?'native-plan':'replay').onclick();
    assert.ok(calls.some(({path})=>path.endsWith(feature==='native_resume'?'/codex-plan':'/replay')));
    assert.ok(calls.every(({path})=>!path.endsWith(feature==='native_resume'?'/replay':'/codex-plan')));
  }
});
