const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

async function run(){
 const fields=new Map(),requests=[];
 const field=id=>{if(!fields.has(id))fields.set(id,{value:'',disabled:false,textContent:'',classList:{toggle(){}},scrollIntoView(){}});return fields.get(id)};
 let finishRequest;
 const context=vm.createContext({
  $:field,localStorage:{getItem(){return ''},setItem(){},removeItem(){}},
  crypto:{randomUUID:()=> 'collection-test-request'},state:{page:'upload'},
  toast(){},library(){},nav:[['ai-models']],clearTimeout(){},setTimeout(){},
  esc:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'),
  fetch:async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});await new Promise(resolve=>finishRequest=resolve);return {ok:true,json:async()=>({id:8,total:1})}}
 });
 for(const file of ['collection.js','listing.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../static',file),'utf8'),context);
 const evaluate=code=>vm.runInContext(code,context);
 evaluate('const originalPollCollection=pollCollection;pollCollection=async()=>{}');
 field('#tiktok-links').value='https://shop.tiktok.com/view/product/1732439064809935743';
 const first=evaluate('startCollection()');
 await evaluate('startCollection()');
 assert.equal(requests.length,1,'double click must not start another job');
 assert.equal(requests[0].url,'/api/tiktok/jobs');
 assert.equal(requests[0].body.links,field('#tiktok-links').value);
 assert.equal(field('#tiktok-links').disabled,true);
 finishRequest();await first;
 assert.equal(evaluate('collectionJob'),8);
 assert.equal(evaluate('collectionDetailsOpen'),true);
 assert.equal(field('#collection-history').open,true);
 assert.equal(field('#tiktok-links').value,'');
 const app=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
 const batchTable=app.slice(app.indexOf('function batchTable('),app.indexOf('\nasync function deleteSource'));
 context.num=String;evaluate(batchTable);
 const table=evaluate(`batchTable([{id:9,filename:'products.xlsx',kind:'product',imported:39,total:39,imported_at:'2026-11-23T16:32:08'}])`);
 assert.doesNotMatch(table,/开始采集|startBatchCollection/);
 assert.match(table,/删除此次来源/);
 evaluate(app.slice(app.indexOf('async function sendFiles('),app.indexOf('\nfunction draftGallery')));
 context.FormData=class{append(){}};
 const uploads=[];
 context.fetch=async(url)=>{uploads.push(url);return {ok:true,json:async()=>[{batch:9,imported:39,total:39,collection:{id:12,total:39}}]}};
 evaluate('upload=async()=>{};collectionDetailsOpen=false;collectionJob=8');
 await evaluate("sendFiles([{name:'products.xlsx'}])");
 assert.deepEqual(uploads,['/api/import'],'upload alone must create the job on the server');
 assert.equal(evaluate('collectionJob'),12);
 assert.equal(evaluate('collectionDetailsOpen'),true);
 assert.match(evaluate('collectionTasks()'),/采集进度/);
 context.api=async()=>({jobs:[{id:9,state:'running',created_at:'2026-11-23T16:32:08',total:39,processed:0,counts:{pending:38,running:1},source_filename:'products.xlsx',current:{position:1,stage:'正在保存图片',cached:2,discovered:12}}]});
 evaluate('pollCollection=originalPollCollection;drawCollectionItems=async()=>{};collectionJob=null;collectionDetailsOpen=false');
 await evaluate('pollCollection()');
 assert.equal(field('#collection-history').open,true);
 assert.equal(evaluate('collectionDetailsOpen'),true);
 assert.equal(evaluate('collectionJob'),9);
 assert.match(field('#collection-jobs').innerHTML,/图组已保存 2 \/ 12 张/);
 assert.match(field('#collection-jobs').innerHTML,/products.xlsx/);
 const skipped=evaluate(`collectionCard({id:8,state:'finished',created_at:'2026-11-23T16:45:11',total:39,processed:39,counts:{complete:39},regional_unavailable:39})`);
 assert.match(skipped,/资料已保存 <b>0<\/b>/);
 assert.match(skipped,/本地区不售 <b>39<\/b>（未取得资料）/);
 assert.match(evaluate("savedProductStatus({sales:{display:'0'}})"),/页面销量 0/);
 assert.match(evaluate('savedProductStatus({})'),/页面销量 未知/);
 const info=evaluate(`productDataInfo({sales:{display:'2.1K+',note:'全球累计，不含退货'},description_blocks:[{type:'text',text:'<script>unsafe</script>'}],specifications:[{name:'Material',value:'Cotton'}]})`);
 assert.match(info,/2\.1K\+/);assert.match(info,/全球累计，不含退货/);
 assert.match(info,/&lt;script&gt;/);assert.doesNotMatch(info,/<script>/);
 assert.match(info,/Cotton/);
 return 'PASS: link/file queue submission, duplicate-click protection, visible image progress, sales/description rendering';
}
if(require.main===module)run().then(console.log).catch(error=>{console.error(error);process.exitCode=1});
module.exports=run;
