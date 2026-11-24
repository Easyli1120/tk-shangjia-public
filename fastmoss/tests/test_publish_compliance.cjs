const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function run(){
 const context=vm.createContext({esc:value=>String(value??'')});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/shop-publish.js'),'utf8'),context);
 context.rules=['101395','101400'].map(id=>({id,name:'CA prop 65',type:'PRODUCT_PROPERTY',is_requried:false,
  values:[{id:'yes',name:'Yes'},{id:'no',name:'No'}]}));
 context.rules.push({id:'material',name:'Material',type:'PRODUCT_PROPERTY',values:[]});
 context.form={attributes:{'101395':[{id:'no'}],'101400':[{id:'no'}]}};
 const html=vm.runInContext('publishPropertyFields({attributes:rules},form)',context);
 const optional=html.indexOf('<details class="publish-optional"');
 for(const id of ['101395','101400']){
  assert.ok(html.indexOf(`data-pub-attr="${id}"`)<optional);
 }
 assert.match(html,/是否含有生殖毒性化学物质 \*/);
 assert.match(html,/是否含有致癌物质 \*/);
 assert.equal((html.match(/value="no" selected>否/g)||[]).length,2);
 assert.ok(html.indexOf('data-pub-attr="material"')>optional);
 context.form.attributes['101395']=[{id:'yes'}];
 const edited=vm.runInContext('publishAttrInput(rules[0],form.attributes["101395"])',context);
 assert.match(edited,/value="yes" selected>是/);
 assert.doesNotMatch(edited,/value="no" selected/);
 assert.equal(Boolean(vm.runInContext('publishAttributeRequired({id:"101395",type:"CERTIFICATION"})',context)),false);
 return 'PASS: both compliance fields required and visible, No selected, manual Yes preserved, other optional fields unchanged';
}
module.exports=run;
if(require.main===module)console.log(run());
