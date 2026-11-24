const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function setup(){
 const requests=[],panel={outerHTML:''};
 const context=vm.createContext({console,esc:s=>String(s??'').replaceAll('<','&lt;'),
  document:{getElementById:()=>panel},$:()=>null});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/shop-publish.js'),'utf8'),context);
 context.requests=requests;
 vm.runInContext(`
 publishView={items:[{market:'US',id:'a',profile:{category_id:'cat',title_input:'Hoodie',description_input:'Cotton Polyester',attributes:{m:[{id:'c'}],pattern:[{id:'graphic'}]}},checkResult:{status:'passed'}}],index:0,meta:{category_id:'cat'},busy:false,batchCheck:{fingerprint:'old'}};
 collectPublishForm=()=>publishView.items[publishView.index];publishOpen=()=>true;redrawPublishSection=()=>{};
 shopPost=async(action,body)=>{requests.push({action,body});return response};
 `,context);
 return {context,requests,panel,run:code=>vm.runInContext(code,context)};
}

async function run(){
 const a=setup();
 a.context.response={applicable:true,status:'conflict',message:'需要核对',suggestion:'50% Cotton / 50% Polyester',
  evidence:[{label:'详情',text:'<script>bad()</script>'}],suggested_attributes:{m:[{id:'c'},{id:'p'}]}};
 await a.run('checkPublishMaterial()');
 assert.equal(a.requests[0].action,'listing-material-review');
 assert.match(a.panel.outerHTML,/已核实实物，填写建议/);
 assert.doesNotMatch(a.panel.outerHTML,/<script>/);
 assert.deepEqual(JSON.parse(a.run('JSON.stringify(publishView.items[0].profile.attributes.m)')),[{id:'c'}],'review alone never fills');
 a.context.response={applicable:true,status:'consistent',message:'一致',suggested_attributes:{}};
 await a.run('applyPublishMaterial()');
 assert.deepEqual(JSON.parse(a.run('JSON.stringify(publishView.items[0].profile.attributes)')),
  {m:[{id:'c'},{id:'p'}],pattern:[{id:'graphic'}]});
 assert.equal(a.run('publishView.batchCheck'),null);
 assert.equal(a.run('publishView.items[0].checkResult.status'),'stale');
 assert.ok(a.requests.every(r=>r.action==='listing-material-review'),'does not save or submit automatically');
 assert.doesNotMatch(a.panel.outerHTML,/已核实实物，填写建议/);

 const b=setup();b.context.response=a.context.response;
 b.run('publishView.items[0].materialReview={suggested_attributes:{m:[{id:"p"}]}};publishView.items[0].materialReviewKey="outdated"');
 await b.run('applyPublishMaterial()');
 assert.deepEqual(JSON.parse(b.run('JSON.stringify(publishView.items[0].profile.attributes.m)')),[{id:'c'}]);
 assert.equal(b.requests.length,1,'stale suggestion only triggers a new review');

 const c=setup();let release;c.context.pending=new Promise(resolve=>release=resolve);
 c.run('shopPost=async()=>pending');
 const request=c.run('checkPublishMaterial()');
 c.run('publishView.items[0].profile.title_input="Changed"');
 release(a.context.response);await request;
 assert.equal(c.run('publishView.items[0].materialReview'),null);
 assert.match(c.panel.outerHTML,/内容已修改/);
 assert.equal(c.run('publishView.busy'),false);
 c.run('shopPost=async()=>{throw new Error("offline")}');await c.run('checkPublishMaterial()');
 assert.match(c.panel.outerHTML,/offline/);assert.equal(c.run('publishView.busy'),false);
 return 'PASS: material review, explicit filling, preserved other attributes, stale suggestions, changed copy, network failure and no publishing';
}
module.exports=run;
if(require.main===module)run().then(console.log).catch(error=>{console.error(error);process.exitCode=1});
