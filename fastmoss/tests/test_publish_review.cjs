const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function setup(){
 const modal={classList:{contains:()=>false}},form={inert:false},drawer={scrollTop:120};
 const controls={'#modal':modal,'.publish-drawer':drawer,'#publish-form':form};
 const requests=[],context=vm.createContext({console,setTimeout,$:id=>controls[id],document:{querySelector:()=>drawer},toast:()=>{}});
 for(const name of ['shop-publish.js','shop-publish-review.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../static',name),'utf8'),context);
 context.controls=controls;context.requests=requests;
 vm.runInContext(`
 renderReview=drawPublishReview;
 publishView={items:[{market:'US',id:'a',profile:{},images:[{key:'image',text_matches:[{name:'Old'}]}]},{market:'US',id:'b',profile:{},images:[]}],index:0,busy:false};
 publishReviewOpen=()=>true;publishOpen=()=>true;
 drawPublishReview=()=>{delete controls['#publish-form'];publishView.reviewStarted=true;publishView.reviewScreen=true};
 savePublishForm=async()=>true;initializePublishContent=()=>{};publishFeedback=()=>{};
 shopPost=async(action,body)=>{requests.push({action,body});return response};
 `,context);
 return {context,controls,requests,run:code=>vm.runInContext(code,context)};
}

async function run(){
 // A mixed batch must not mark failures (or missing responses) as passed.
 const a=setup();
 a.context.response={ready:false,checked:[{market:'US',id:'b'}],errors:[{market:'US',id:'a',error:'OCR',issues:[{kind:'image_ocr',image_key:'image',section:'publish-photos',message:'Example House',matches:[{name:'Example House'}]}]}]};
 await a.run('checkPublishBatch()');
 assert.equal(a.run('publishView.items[0].checkResult.status'),'failed');
 assert.equal(a.run('publishView.items[1].checkResult.status'),'passed');
 assert.equal(a.run('publishView.items[0].images[0].text_matches[0].name'),'Example House');
 assert.equal(a.run('publishView.batchCheck'),null);
 assert.ok(!a.requests.some(r=>r.action==='listing-submit'));

 // Rechecking one repaired product preserves other results; it cannot authorize a batch.
 a.context.response={ready:true,fingerprint:'one-item',checked:[{market:'US',id:'a'}],items:[{market:'US',id:'a',profile:{},image_reviews:[{key:'image',text_matches:[]}]}]};
 await a.run('checkPublishReview([0])');
 assert.equal(a.run('publishView.items.every(p=>p.checkResult.status==="passed")'),true);
 assert.equal(a.run('publishView.items[0].images[0].text_matches.length'),0);
 assert.equal(a.run('publishView.batchCheck'),null);
 assert.equal(a.requests.at(-1).body.items.length,1);
 a.context.response={ready:true,fingerprint:'whole-batch',checked:[{market:'US',id:'a'},{market:'US',id:'b'}]};
 await a.run('checkPublishBatch()');
 assert.equal(a.run('publishView.batchCheck.fingerprint'),'whole-batch');
 a.run('invalidatePublishCheck()');
 assert.equal(a.run('publishView.items[0].checkResult.status'),'stale');
 assert.equal(a.run('publishView.items[1].checkResult.status'),'passed');
 await a.run('submitPublishReview()');
 assert.ok(!a.requests.some(r=>r.action==='listing-submit'));

 // Saving failure retains the editor and never checks stale persisted data.
 const b=setup();b.run('savePublishForm=async()=>false');
 await b.run('saveAndReviewPublish()');
 assert.ok(b.controls['#publish-form']);assert.equal(b.controls['#publish-form'].inert,false);
 assert.equal(b.requests.length,0);

 // Network failure cannot show green, and leaves the list usable for retry.
 const c=setup();c.run('shopPost=async()=>{throw Error("offline")}');
 await c.run('saveAndReviewPublish()');
 assert.equal(c.run('publishView.items[0].checkResult.status'),'pending');
 assert.equal(c.run('publishView.busy'),false);
 assert.equal(c.run('publishView.reviewScreen'),true);
 assert.match(c.run('publishView.reviewMessage'),/offline/);

 // A second click while checking must not start a duplicate request.
 const d=setup();let release;
 d.context.waiting=new Promise(resolve=>release=resolve);
 d.run('shopPost=async(action,body)=>{requests.push({action,body});return waiting}');
 delete d.controls['#publish-form'];
 const pending=d.run('checkPublishReview([0])');
 await d.run('checkPublishReview([0])');assert.equal(d.requests.length,1);
 release({ready:true,checked:[{market:'US',id:'a'}]});await pending;
 assert.equal(d.run('publishView.busy'),false);
 d.run('applyPublishReviewResult(publishView,{ready:false,errors:[]},[0])');
 assert.equal(d.run('publishView.items[0].checkResult.status'),'pending');

 // Manual approval saves before checking, preserves excluded OCR hints, and never submits.
 const e=setup();
 e.run('savePublishForm=async()=>{requests.push({action:"save"});return true}');
 e.context.response={ready:true,checked:[{market:'US',id:'a'}],items:[{market:'US',id:'a',profile:{main_image_keys:['clean'],description_image_keys:[]},image_reviews:[{key:'image',text_matches:[{name:'Example House'}]}]}]};
 await e.run('approvePublishReview()');
 assert.deepEqual(e.requests.map(r=>r.action),['save','listing-check']);
 assert.equal(e.requests[1].body.items.length,1);
 assert.equal(e.run('publishView.items[0].checkResult.status'),'passed');
 assert.equal(e.run('publishView.items[1].checkResult'),undefined);
 assert.equal(e.run('publishView.items[0].images[0].text_matches[0].name'),'Example House');
 assert.match(e.run('publishView.reviewMessage'),/人工审核通过/);
 assert.equal(e.run('publishView.batchCheck'),null);
 e.context.response={ready:false,checked:[],errors:[{market:'US',id:'a',error:'SKU 库存须大于零'}]};
 await e.run('approvePublishReview()');
 assert.equal(e.run('publishView.items[0].checkResult.status'),'failed');
 assert.doesNotMatch(e.run('publishView.reviewMessage'),/审核通过/);
 assert.ok(!e.requests.some(r=>r.action==='listing-submit'));

 const f=setup();f.run('savePublishForm=async()=>false');
 await f.run('approvePublishReview()');
 assert.equal(f.requests.length,0);
 assert.equal(f.controls['#publish-form'].inert,false);

 // Skipped products cannot reuse the old batch approval or reach check/submit requests.
 const g=setup();delete g.controls['#publish-form'];
 g.run('publishView.index=1;publishView.items.forEach(p=>p.checkResult={status:"passed"});publishView.batchCheck={fingerprint:"old-batch"}');
 g.run('publishView.busy=true;skipPublishReview(0)');
 assert.equal(g.run('publishView.items.length'),2,'cannot change a batch during check/submit');
 g.run('publishView.busy=false;skipPublishReview(0)');
 assert.equal(g.run('publishView.items[0].id'),'b');
 assert.equal(g.run('publishView.index'),0);
 assert.equal(g.run('publishView.batchCheck'),null);
 await g.run('submitPublishReview()');assert.equal(g.requests.length,0);
 g.context.response={ready:true,fingerprint:'remaining-batch',checked:[{market:'US',id:'b'}]};
 await g.run('checkPublishBatch()');
 assert.deepEqual(JSON.parse(JSON.stringify(g.requests[0].body.items)),[{market:'US',id:'b'}]);
 g.context.response={ready:false,errors:[]};await g.run('submitPublishReview()');
 assert.equal(g.requests[1].action,'listing-submit');
 assert.deepEqual(JSON.parse(JSON.stringify(g.requests[1].body.items)),[{market:'US',id:'b'}]);
 assert.equal(g.requests[1].body.fingerprint,'remaining-batch');
 g.run('closeModal=()=>{controls.closed=true};skipPublishReview(0)');
 assert.equal(g.controls.closed,true);assert.equal(g.run('publishView'),null);
 await g.run('checkPublishBatch()');await g.run('submitPublishReview()');
 assert.equal(g.requests.length,2,'an empty batch cannot check or submit');
 // Material completion is reported inline; checking never navigates to an editor.
 const h=setup();delete h.controls['#publish-form'];
 h.context.response={ready:false,checked:[{market:'US',id:'a'}],items:[
  {market:'US',id:'a',profile:{title_input:'Hoodie'},material_completed:true,
   material_review:{applicable:true,status:'consistent',suggestion:'50% Cotton / 50% Polyester',issues:[],evidence:[{label:'详情',text:'<script>bad()</script>'}]}},
  {market:'US',id:'b',profile:{title_input:'Conflicting hoodie'},material_completed:false,
   material_review:{applicable:true,status:'conflict',issues:[{message:'比例冲突'}],evidence:[]}}
 ],errors:[{market:'US',id:'b',issues:[{kind:'material_conflict',section:'publish-material',label:'面料',message:'比例冲突'}]}]};
 h.run('editPublishReview=()=>{throw new Error("unexpected navigation")}');
 await h.run('checkPublishBatch()');
 assert.equal(h.requests.length,2);assert.ok(h.requests.every(r=>r.action==='listing-check'&&r.body.items.length===1));
 assert.equal(h.run('publishView.items[0].checkResult.materialCompleted'),true);
 assert.match(h.run('publishView.reviewMessage'),/已自动补全 1 件/);
 h.run('esc=s=>String(s??"").replaceAll("<","&lt;");publishVisibleImages=()=>[];publishView.connection={shops:[]};publishView.reviewFilter="all";renderReview()');
 assert.match(h.controls['#modal'].innerHTML,/已自动补全面料/);
 assert.match(h.controls['#modal'].innerHTML,/当前证据无法自动确定/);
 assert.doesNotMatch(h.controls['#modal'].innerHTML,/<script>/);
 assert.doesNotMatch(h.controls['#modal'].innerHTML,/直接处理此商品/);
 assert.match(h.controls['#modal'].innerHTML,/去修改 ↗/);
 h.run('publishView.items[0].checkResult.status="stale"');
 assert.match(h.run('publishReviewMaterial(publishView.items[0])'),/面料待复检/);
 // A paused batch retains completed results and does not start the next item.
 const progress=setup();delete progress.controls['#publish-form'];let finish;
 progress.context.waiting=new Promise(resolve=>finish=resolve);
 progress.run('shopPost=async(action,body)=>{requests.push({action,body});return waiting}');
 const checking=progress.run('checkPublishBatch()');
 assert.equal(progress.run('publishView.items[0].checkResult.status'),'checking');
 progress.run('publishView.stopChecking=true');
 finish({ready:true,checked:[{market:'US',id:'a'}]});await checking;
 assert.equal(progress.requests.length,1);
 assert.equal(progress.run('publishView.items[0].checkResult.status'),'passed');
 assert.equal(progress.run('publishView.items[1].checkResult'),undefined);
 assert.equal(progress.run('publishView.batchCheck'),null);
 assert.match(progress.run('publishView.reviewMessage'),/暂停/);
 // Inline inventory exclusion and brand confirmation save only the current
 // product profile, without deleting its SKU rows or submitting a product.
 const quick=setup();delete quick.controls['#publish-form'];
 quick.run(`publishView.connection={selected_shop_id:'shop'};publishView.items[0].profile={stocks:{one:'4',two:'0'}};publishView.items[0].variants=[{id:'one'},{id:'two'}];
  FormData=class extends Map{constructor(){super([['brand_id','unbranded'],['brand_confirmed','on'],['inventory_confirmed','on'],['exclude_zero','on']])}};
  document.getElementById=()=>({querySelectorAll:()=>[]});
  checkPublishReview=async indices=>{requests.push({action:'check',indices})};`);
 quick.context.response={ok:true};await quick.run('savePublishQuick(0)');
 assert.equal(quick.requests[0].action,'listing-profile');
 assert.deepEqual(JSON.parse(JSON.stringify(quick.requests[0].body.profile.excluded_sku_ids)),['two']);
 assert.equal(quick.requests[0].body.profile.brand_confirmed,true);
 assert.equal(quick.requests[0].body.profile.inventory_verified.one,'4');
 assert.equal(quick.run('publishView.items[0].variants.length'),2);
 assert.equal(quick.requests[1].action,'check');
 return 'PASS: batch review, inline material completion and conflicts, no navigation, manual approval, failures and no automatic submission';
}
module.exports=run;
if(require.main===module)run().then(console.log).catch(error=>{console.error(error);process.exitCode=1});
