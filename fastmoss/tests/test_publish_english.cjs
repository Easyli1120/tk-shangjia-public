const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

async function run(){
 const context=vm.createContext({});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/shop-publish.js'),'utf8'),context);
 const evaluate=code=>vm.runInContext(code,context);
 evaluate(`var product={market:'US',id:'a',title:'Source title',source:{description:'Source details'},profile:{},variants:[]};initializePublishContent(product);`);
 assert.equal(evaluate('product.profile.title_input'),'Source title');
 assert.equal(evaluate('product.profile.description_input'),'Source details');
 assert.equal(evaluate('product.profile.content_language'),'en');
 evaluate(`product.profile.title_input='Edited English';product.profile.description_input='';initializePublishContent(product);`);
 assert.equal(evaluate('product.profile.title_input'),'Edited English');
 assert.equal(evaluate('product.profile.description_input'),'');
 evaluate(`var requests=[];publishView={items:[product],index:0};
 collectPublishForm=()=>product;publishOpen=()=>true;publishImageChanges=()=>[];publishFeedback=()=>{};
 shopPost=async(action,body)=>{requests.push({action,body});return {revision:'saved'}};`);
 assert.equal(await evaluate('savePublishForm(true)'),true);
 assert.deepEqual(Array.from(evaluate('requests.map(r=>r.action)')),['listing-profile']);
 assert.equal(evaluate('product.revision'),'saved');
 assert.equal(evaluate('typeof preparePublishChinese'),'undefined');
 return 'PASS: English initialization, retained edits and empty fields, save without translation';
}
module.exports=run;
if(require.main===module)run().then(console.log).catch(error=>{console.error(error);process.exitCode=1});
