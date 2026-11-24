const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
function setup(saved=null){
 const elements={},storage=new Map(saved?[['listing-preparation',JSON.stringify(saved)]]:[]),requests=[],timers=new Map();let next=0;
 const modal={innerHTML:'',classList:{hidden:true,contains(){return this.hidden},add(){this.hidden=true},remove(){this.hidden=false}}};
 const context=vm.createContext({console,AbortController,
  setTimeout:fn=>{timers.set(++next,fn);return next},clearTimeout:id=>timers.delete(id),
  sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  document:{createElement:()=>({}),body:{append:e=>{elements['#'+e.id]=e}}},
  $:s=>s==='#modal'?modal:s==='#publish-preparation'?(modal.innerHTML.includes('id="publish-preparation"')?{}:null):elements[s],
  closeModal:()=>modal.classList.add('hidden'),go:()=>{},esc:s=>String(s??''),toast:()=>{},
  fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,reply:(body,status=200)=>resolve({ok:status<400,json:async()=>body})})),
  drawPublish:()=>{modal.innerHTML='EDITOR'},loadPublishMeta:async()=>{},publishView:null,
 });
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/shop-prepare.js'),'utf8'),context);
 return {run:code=>vm.runInContext(code,context),requests,modal,storage,elements,context};
}
const items=[{market:'US',id:'p1'}],running={id:'job',state:'running',total:1,completed:0,failed:0,rows:[{id:'p1',state:'running',stage:'正在识别图片'}]};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function test(){
 const a=setup();a.context.items=items;
 const starting=a.run('startPublishPreparation(items)');
 assert.equal(a.modal.classList.hidden,false);assert.match(a.modal.innerHTML,/0 \/ 1/);
 await a.run('startPublishPreparation(items)');assert.equal(a.requests.length,1,'double click must not create a second job');
 a.run('closeModal()');a.requests[0].reply(running);await starting;
 assert.equal(a.requests.length,1,'closed window must not start polling');assert.equal(a.modal.classList.hidden,true);
 assert.equal(a.elements['#publish-preparation-resume'].hidden,false);
 assert.equal(JSON.parse(a.storage.get('listing-preparation')).id,'job');
 const resumed=a.run('resumePublishPreparation()');assert.equal(a.requests.length,2);
 a.run('go("shop-management")');a.requests[1].reply({...running,state:'ready',completed:1});await resumed;
 assert.equal(a.requests.length,2);assert.equal(a.modal.classList.hidden,true,'late response must not reopen editor');

 const b=setup({id:'job',items});const open=b.run('resumePublishPreparation()');
 b.requests[0].reply({...running,state:'partial',failed:1,rows:[{id:'p1',state:'failed',stage:'正在识别图片',error:'识别程序失败（退出码 7）',issues:[{image_key:'photo',message:'图片读取失败'}]}]});await open;
 assert.match(b.modal.innerHTML,/退出码 7/);assert.match(b.modal.innerHTML,/重试失败项/);assert.match(b.modal.innerHTML,/photo/);
 const retry=b.run('retryPublishPreparation()');assert.match(b.requests[1].url,/listing-prepare-retry/);
 b.requests[1].reply(running);await retry;
 assert.match(b.requests[2].url,/listing-prepare-status/);
 b.requests[2].reply({...running,state:'ready',completed:1,rows:[{id:'p1',state:'ready'}]});await tick();
 assert.match(b.requests[3].url,/result=1/);
 b.requests[3].reply({...running,state:'ready',completed:1,result:{items:[{id:'p1'}],connection:{selected_shop_id:''}}});await tick();
 assert.equal(b.modal.innerHTML,'EDITOR');assert.equal(b.storage.has('listing-preparation'),false);

 const c=setup({id:'old',items});const stale=c.run('resumePublishPreparation()');
 c.requests[0].reply({error:'准备任务已过期或服务已重启'},400);await stale;
 assert.match(c.modal.innerHTML,/服务已重启/);assert.match(c.modal.innerHTML,/重新准备/);

 const d=setup({id:'job',items});const ready=d.run('resumePublishPreparation()');
 d.requests[0].reply({...running,state:'ready',completed:1});await tick();
 d.requests[1].reply({...running,state:'partial',completed:0,failed:1,rows:[{id:'p1',state:'failed',error:'资料已改变'}],result:{items:[]}});await ready;
 assert.notEqual(d.modal.innerHTML,'EDITOR');assert.match(d.modal.innerHTML,/资料已改变/);

 const early=setup({id:'job',items});const loading=early.run('resumePublishPreparation()');
 early.requests[0].reply({...running,total:2,completed:1});await loading;
 assert.match(early.modal.innerHTML,/进入已准备的 1 件商品/);
 assert.doesNotMatch(early.modal.innerHTML,/enterPreparedListings\(\)" disabled/);
 console.log('PASS: immediate progress, duplicate clicks, background resume, late responses, failure retry, restart and stale results');
}
if(require.main===module)test().catch(e=>{console.error(e);process.exitCode=1});
module.exports=test;
