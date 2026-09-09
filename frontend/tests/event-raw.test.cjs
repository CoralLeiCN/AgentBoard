const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const source=readFileSync(require('node:path').join(__dirname,'../app.js'),'utf8');
function setup(json) {
  const element=()=>({textContent:'',children:[],replaceChildren(){this.children=[];},append(...children){this.children.push(...children);}});
  const nodes={'#event-raw-status':element(),'#event-raw-lines':element()};
  const context=vm.createContext({json,hasFeature:()=>true,document:{createElement:element},$:id=>nodes[id]});
  vm.runInContext('let eventRawRequest=0;'+source.slice(source.indexOf('async function loadEventRaw('),source.indexOf('function inspectEvent(')),context);
  return {nodes,load:context.loadEventRaw};
}
test('unified source lines are deduplicated by archive and line, with exact text',async()=>{
  const line={line_number:7,text:' {"text":"<script>é</script>"}\r\n'};
  const {load,nodes}=setup(async()=>({available:true,archive:{id:2,sha256:'hash'},lines:[line]}));
  await load({session_id:'s',id:'a',unified:{source_event_ids:['a','b']}});
  assert.match(nodes['#event-raw-status'].textContent,/1 source line across 2 normalized events/);
  assert.equal(nodes['#event-raw-lines'].children.length,1);
  assert.equal(nodes['#event-raw-lines'].children[0].children[1].textContent,line.text);
});
test('a slow previous event cannot replace the current event source or error state',async()=>{
  let resolveOld;
  const {load,nodes}=setup(path=>path.includes('/old/')?new Promise(resolve=>resolveOld=resolve):Promise.resolve({available:false,reason:'No verified mapping',lines:[]}));
  const old=load({session_id:'s',id:'old'});
  await load({session_id:'s',id:'new'});
  resolveOld({available:true,archive:{id:1,sha256:'hash'},lines:[{line_number:1,text:'{}'}]});
  await old;
  assert.equal(nodes['#event-raw-status'].textContent,'No verified mapping');
  assert.equal(nodes['#event-raw-lines'].children.length,0);
});
