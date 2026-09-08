const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const source=readFileSync(require('node:path').join(__dirname,'../app.js'),'utf8');
function setup(json) {
  const nodes={};
  const state={unclassified:['a','b','c'],classifying:false};
  let reloads=0;
  const context=vm.createContext({state,json,hasFeature:()=>true,$:id=>nodes[id]??=( {} ),
    loadSessions:async()=>reloads++,renderClassification:()=>{}});
  vm.runInContext(source.slice(source.indexOf('async function classifyPage('),source.indexOf('async function openSession(')),context);
  return {state,nodes,run:context.classifyPage,reloads:()=>reloads};
}
test('page batch continues after errors, skips existing labels, and reports dummy results',async()=>{
  const posts=[];
  const fixture=setup(async(path,options)=>{
    if(options){posts.push(path);if(path.includes('/a/'))throw Error('No recorded text');return {dummy:true};}
    return {classification:path.endsWith('/b')?{category:'coding'}:null};
  });
  await fixture.run();
  assert.equal(posts.length,2);
  const progress=fixture.nodes['#classification-progress'].textContent;
  assert.match(progress,/1 classified \(1 dummy\), 1 already classified, 1 failed/);
  assert.match(progress,/a: No recorded text/);
  assert.equal(fixture.state.classifying,false);
  assert.equal(fixture.nodes['#stop-classification'].hidden,true);
  assert.equal(fixture.reloads(),1);
});
test('stop waits for the current request and prevents subsequent calls or duplicate batches',async()=>{
  let finish,started;
  const pending=new Promise(resolve=>started=resolve);
  const posts=[];
  const fixture=setup(async(path,options)=>{
    if(!options)return {classification:null};
    posts.push(path);started();return new Promise(resolve=>finish=resolve);
  });
  const batch=fixture.run();await pending;
  await fixture.run();
  fixture.state.stopClassification=true;
  finish({dummy:false});await batch;
  assert.equal(posts.length,1);
  assert.match(fixture.nodes['#classification-progress'].textContent,/Stopped\. 1 classified/);
});
test('changing the displayed page does not change the batch already requested',async()=>{
  const posts=[];
  let fixture;
  fixture=setup(async(path,options)=>{
    if(!options)return {classification:null};
    posts.push(path);fixture.state.unclassified=['different-page'];return {dummy:false};
  });
  await fixture.run();
  assert.deepEqual(posts,['a','b','c'].map(sid=>`/api/v1/sessions/${sid}/classify`));
});
