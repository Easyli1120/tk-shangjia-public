// Local preparation -> checked snapshot -> durable server queue.
let publishView=null,publishTimer=null;
const publishCommonFields=['weight','length','width','height','warehouse_id','global_warehouse_id'];
const publishLabels={queued:'排队中',running:'提交中',submitted:'已提交 / 待审核',active:'在售',draft:'平台草稿',failed:'提交失败',rejected:'审核未通过',inactive:'已下架',frozen:'已冻结',deleted:'平台已删除',scheduled:'定时上架',uncertain:'结果待核对'};
async function publishListings(items){
 const chosen=items||[...listingChosen.values()];
 if(!chosen.length)return toast('请先选择商品');
 if(chosen.length>100)return toast('每批最多 100 件，请减少选择');
 return startPublishPreparation(chosen);
}
// Category names and hierarchy come from the shop's current TikTok category tree.
function publishCategoryTree(){
 const v=publishView,source=v.meta?.categories||[];
 if(v.categoryTree?.source===source)return v.categoryTree;
 const nodes=new Map(source.map(c=>[String(c.id),{...c,id:String(c.id),parent_id:String(c.parent_id||'0')}]));
 const allowed=new Set(),leaves=[];
 for(const node of nodes.values()){
  if(!node.is_leaf||!(node.permission_statuses||[]).includes('AVAILABLE'))continue;
  leaves.push(node);let current=node;const visited=new Set();
  while(current&&!visited.has(current.id)){visited.add(current.id);allowed.add(current.id);current=nodes.get(current.parent_id);}
 }
 const children=new Map();
 for(const node of nodes.values())if(allowed.has(node.id)){
  const parent=nodes.has(node.parent_id)?node.parent_id:'0';
  if(!children.has(parent))children.set(parent,[]);children.get(parent).push(node);
 }
 const path=id=>{const result=[],seen=new Set();let n=nodes.get(String(id));while(n&&!seen.has(n.id)){seen.add(n.id);result.unshift(n);n=nodes.get(n.parent_id);}return result;};
 const paths=new Map(leaves.map(n=>[n.id,path(n.id).map(x=>x.local_name).join(' - ')]));
 return v.categoryTree={source,nodes,children,allowed,leaves,path,paths};
}
function publishCategoryPickerState(){
 const v=publishView,p=v.items[v.index],tree=publishCategoryTree(),key=p.market+':'+p.id;
 if(!v.categoryPicker||v.categoryPicker.key!==key||v.categoryPicker.tree!==tree){
  const current=String(p.profile.category_id||''),selected=tree.leaves.some(x=>x.id===current)?current:'';
  v.categoryPicker={key,tree,path:selected?tree.path(selected).map(x=>x.id):[],selected,query:''};
 }
 return v.categoryPicker;
}
function publishCategoryPath(state,id=state.selected){return id?state.tree.path(id).map(n=>n.local_name).join(' > '):''}
function publishCategoryStatus(f,m){
 if(!m)return publishView.connection.selected_shop_id?'已带入类目，正在验证是否为最末级类目及店铺销售权限…':'连接店铺后验证最末级类目及销售权限。';
 if(m.category_message)return m.category_message;
 const category=(m.categories||[]).find(c=>String(c.id)===String(f.category_id));
 if(category?.is_leaf&&(category.permission_statuses||[]).includes('AVAILABLE'))return '已验证：最末级类目，当前店铺可售。';
 const source=(m.categories||[]).find(c=>String(c.id)===String(m.source_category_id));
 if(source&&!source.is_leaf)return '当前类目还不是最末级类目，请点击「选择」继续选择子类目。';
 return m.match_status==='unavailable'?'暂时无法验证类目，请稍后重新打开或选择类目。':'未能验证为当前店铺的可售最末级类目，请点击「选择」确认。';
}
function publishCategoryControl(f,m){
 const source=publishView.items[publishView.index].source?.category||m?.source_category;
 const tree=publishCategoryTree(),path=tree.path(f.category_id).map(n=>n.local_name).join(' > '),value=path||f.category_path||source||'',enabled=tree.leaves.length>0;
 return `<div class="publish-category-field"><label for="publish-category-trigger">商品类目 *</label>
 <input type="hidden" name="category_id" value="${esc(f.category_id||'')}">
 <button class="publish-category-trigger" id="publish-category-trigger" type="button" aria-controls="publish-category-panel" aria-expanded="false" onclick="togglePublishCategoryPicker()" ${enabled?'':'disabled'}><span id="publish-category-value">${esc(value||'请选择末级类目')}</span><span class="publish-category-edit">${enabled?'选择':'等待读取'}</span></button>
 <div class="publish-category-panel" id="publish-category-panel" hidden><div class="publish-category-search"><input class="search" id="publish-category-search" type="search" aria-label="搜索商品类目" placeholder="搜索类目名称，如：连帽衫 / Hoodies" oninput="drawPublishCategoryPicker(this.value)"><button type="button" aria-label="清除搜索" onclick="clearPublishCategorySearch()">×</button></div><div class="publish-category-crumbs" id="publish-category-crumbs"></div><div id="publish-category-tree"></div><div class="publish-category-selection" id="publish-category-selection" role="status">请选择末级类目</div><div class="publish-category-actions"><button class="btn small" type="button" onclick="closePublishCategoryPicker()">取消</button><button class="btn primary small" id="publish-category-apply" type="button" disabled onclick="applyPublishCategory()">使用此类目</button></div></div>
 <span id="publish-category-status" role="status">${esc(publishCategoryStatus(f,m))}</span></div>`;
}
function togglePublishCategoryPicker(){
 const panel=$('#publish-category-panel'),trigger=$('#publish-category-trigger');if(!panel||!trigger||trigger.disabled)return;
 if(!panel.hidden){closePublishCategoryPicker();return}
 const state=publishCategoryPickerState(),current=String(publishView.items[publishView.index].profile.category_id||'');
 state.selected=state.tree.leaves.some(x=>x.id===current)?current:'';state.path=state.selected?state.tree.path(state.selected).map(x=>x.id):[];state.query='';
 panel.hidden=false;trigger.setAttribute('aria-expanded','true');drawPublishCategoryPicker('');
}
function closePublishCategoryPicker(){
 const panel=$('#publish-category-panel'),trigger=$('#publish-category-trigger');if(panel)panel.hidden=true;if(trigger)trigger.setAttribute('aria-expanded','false');
}
function clearPublishCategorySearch(){const input=$('#publish-category-search');if(!input)return;input.value='';drawPublishCategoryPicker('');input.focus()}
function drawPublishCategoryPicker(query=''){
 const state=publishCategoryPickerState(),tree=state.tree,root=$('#publish-category-tree'),crumbs=$('#publish-category-crumbs');if(!root||!crumbs)return;
 state.query=query;const q=query.trim().toLowerCase(),leafIds=new Set(tree.leaves.map(n=>n.id));
 const option=node=>`<button type="button" class="publish-category-option ${state.path.includes(node.id)?'is-active':''}" aria-pressed="${state.selected===node.id}" onclick="pickPublishCategory('${esc(node.id)}',event)"><span>${esc(node.local_name)}</span><span aria-hidden="true">${leafIds.has(node.id)?(state.selected===node.id?'✓':''):'›'}</span></button>`;
 if(q){
  const matches=tree.leaves.filter(node=>(tree.paths.get(node.id)+' '+tree.path(node.id).map(x=>x.english_name||'').join(' ')).toLowerCase().includes(q));
  root.innerHTML=`<div class="publish-category-results">${matches.slice(0,100).map(node=>`<button type="button" class="publish-category-option ${state.selected===node.id?'is-active':''}" aria-pressed="${state.selected===node.id}" onclick="pickPublishCategory('${esc(node.id)}',event)"><span>${esc(tree.paths.get(node.id))}</span><span aria-hidden="true">${state.selected===node.id?'✓':''}</span></button>`).join('')||'<p>未找到可售末级类目，请换个关键词。</p>'}${matches.length>100?'<p>结果超过 100 项，请输入更具体的名称。</p>':''}</div>`;
 }else{
  const parents=['0',...state.path.filter(id=>!tree.nodes.get(id)?.is_leaf)];
  root.innerHTML=`<div class="publish-category-columns">${parents.map((id,index)=>`<div class="publish-category-column" role="group" aria-label="第 ${index+1} 级类目">${(tree.children.get(id)||[]).map(option).join('')||'<p>暂无可售类目</p>'}</div>`).join('')}</div>`;
 }
 const pathNodes=state.path.map(id=>tree.nodes.get(id)).filter(Boolean);
 crumbs.innerHTML=pathNodes.length?pathNodes.map((node,index)=>`<button type="button" onclick="pickPublishCategory('${esc(node.id)}',event)">${esc(node.local_name)}</button>${index<pathNodes.length-1?'<span aria-hidden="true">›</span>':''}`).join(''):'<span>选择类目</span>';
 const selection=$('#publish-category-selection'),apply=$('#publish-category-apply');if(selection)selection.textContent=state.selected?'已选择末级类目：'+publishCategoryPath(state):pathNodes.length?'继续选择末级类目：'+publishCategoryPath(state,pathNodes[pathNodes.length-1].id):'请选择末级类目';if(apply)apply.disabled=!state.selected;
}
function pickPublishCategory(id,event){
 event?.stopPropagation();
 const state=publishCategoryPickerState(),node=state.tree.nodes.get(String(id));if(!node)return;
 state.path=state.tree.path(node.id).map(x=>x.id);state.selected=state.tree.leaves.some(x=>x.id===node.id)?node.id:'';drawPublishCategoryPicker(state.query);
}
async function applyPublishCategory(){
 const state=publishCategoryPickerState();if(!state.selected||publishView.busy)return;
 const p=collectPublishForm(),path=publishCategoryPath(state);p.profile.category_id=state.selected;p.profile.category_path=path;p.profile.category_source='manual';p.profile.category_shop_id=publishView.connection.selected_shop_id;publishView.categoryPicker=null;closePublishCategoryPicker();invalidatePublishCheck();await loadPublishMeta(false);
}
function publishImageOptions(p,value){return '<option value="">未选择</option>'+publishVisibleImages(p).map((x,i)=>`<option value="${esc(x.key)}" ${x.key===value?'selected':''}>图片 ${i+1} · ${esc(x.group_name||'商品图')}</option>`).join('')}
const publishAttributeNames={
 'Region of origin':'原产地','Sleeve type':'袖型','Season':'季节','Back style':'后背款式','Sheer':'透视','Size type':'尺码类型',
 'Pattern':'图案','Closure type':'闭合方式','Material':'材质','Fit type':'版型','Neckline':'领型','Clothing length':'衣长',
 'Style':'款式','Brand':'品牌','Color':'颜色','Size':'尺码','Type':'类型','Bra coverage':'罩杯覆盖度',
 'Manufacturer address (city)':'制造商地址（城市）','Knicker type':'内裤类型','Holiday or occasion':'节日或场合',
 'Manufacturer postal code':'制造商邮编','Bra type':'文胸类型','Sleeve length':'袖长','Feature':'特征','Design':'设计',
 'Chest pad':'胸垫','Pocket description':'口袋描述','Composition':'成分','Manufacturer address (province)':'制造商地址（省）',
 'Strap type':'肩带类型','Quantity per pack':'每包数量','Occasion':'场合','Fabric type':'面料类型','Collar type':'领型',
 'Embellishment':'装饰','Fabric weight':'面料克重','Reprotoxic chemicals':'生殖毒性化学物质','Theme':'主题','Manufacturer':'制造商',
 'Recommended uses for product':'推荐用途','Manufacturer address':'制造商地址','Care instructions':'洗护说明',
 'Apparel fabric stretch':'面料弹性','Carcinogen':'致癌物质','Socks type':'袜子类型','Application area':'适用部位',
 'Waist height':'腰高','Country of origin':'原产国','Panties style':'内裤款式','Washing instructions':'洗涤说明',
 'Weaving method':'编织方式','Bottom style':'下装款式','Manufacturer email':'制造商邮箱','Product benefits':'商品优点',
 'Manufacturer phone number':'制造商电话','Shaper type':'塑身衣类型'
};
const publishAttributeValues={
 'Long sleeve':'长袖','Short sleeve':'短袖','Sleeveless':'无袖','Winter':'冬季','Spring':'春季','Summer':'夏季','Autumn':'秋季','Fall':'秋季',
 'Default':'默认','No':'否','Yes':'是','Not applicable':'不适用','Letters':'字母','Letter print':'字母印花',
 'Normal type':'普通尺码','Plain':'纯色','1/2/3/4 pieces':'1/2/3/4 件',
 '1/2 coverage':'半罩杯','3/4 coverage':'四分之三罩杯','Full coverage':'全罩杯','Stick on':'粘贴式',
 'Bikini':'比基尼','Boy shorts':'男孩短裤','Cheeky':'俏皮款','Disposable':'一次性','G-string':'丁字裤','Hipster':'平角内裤',
 'Christmas':'圣诞节','Halloween':'万圣节',"Children's Day":'儿童节','Chinese new year':'春节','Easter':'复活节','New year':'新年','Thanksgiving':'感恩节','Valentines day':'情人节',
 'Wireless':'无钢圈','Bralette':'无衬里文胸','Seamless':'无痕','3/4 sleeve':'七分袖','No padding':'无胸垫','Removable padding':'可拆卸胸垫','Non removable padding':'不可拆卸胸垫',
 'Cargo pocket':'工装口袋','Hidden pocket':'隐藏口袋','Kangaroo pocket':'袋鼠口袋','Slant pocket':'斜插袋','Slit pocket':'开口袋',
 'Polyester':'聚酯纤维','Nylon':'尼龙','Cotton':'棉','Elastane':'氨纶','Polyamide':'聚酰胺','Polyurethane':'聚氨酯','Acrylic':'腈纶','Linen':'亚麻','Leather':'皮革','Silk':'真丝','Wool':'羊毛','Cashmere':'羊绒','Viscose':'粘胶纤维','Modal':'莫代尔','Rayon':'人造丝','Lace':'蕾丝','Mesh':'网眼','Fleece':'抓绒','Velvet':'天鹅绒','Denim':'牛仔布','Corduroy':'灯芯绒','Spandex':'氨纶','Microfiber':'超细纤维','Satin':'缎面','Latex':'乳胶','Down':'羽绒','Faux fur':'人造毛皮','Faux leather':'人造皮革','Nylon blend':'尼龙混纺','Cotton blend':'棉混纺','Polyester blend':'聚酯纤维混纺','Stretch fabric':'弹力面料','Power mesh':'高弹网眼',
 'Fishnet socks':'网袜','Invisible socks':'隐形袜','Mesh socks-pantyhose':'网眼连裤袜','Compression stockings':'压力袜','Knee socks':'及膝袜','Mesh socks-long':'长款网袜','Knee-length socks':'过膝袜',
 'Waist':'腰部','Stomach':'腹部','HIPS':'臀部','Thigh':'大腿','Breast':'胸部','Full body':'全身','Back':'背部','Arm':'手臂','Postpartum':'产后','Shoulders':'肩部',
 'Low stretch':'低弹','Non stretch':'无弹','High stretch':'高弹','Stretch':'弹力','4-Way Stretch':'四向弹力','Medium stretch':'中弹','Slight stretch':'微弹',
 'China':'中国','Colombia':'哥伦比亚','USA':'美国','American Samoa':'美属萨摩亚','Vietnam':'越南',
 'Cold wash only':'仅冷水洗','Delicate wash':'轻柔洗','Hand wash or professional dry clean':'手洗或专业干洗','Spot-clean':'局部清洁','Damp wipe only':'仅可湿布擦拭','Permanent press cycle':'免烫程序','Dry cleaning':'干洗','Color-sorted wash':'分类洗涤','Machine washable':'可机洗','Surface clean only':'仅表面清洁','Low iron':'低温熨烫','Cool iron':'冷熨','Dry clean only':'仅干洗','Hang dry':'悬挂晾干','Wipe with damp cloth':'湿布擦拭','Spot clean':'局部清洁','Machine wash or professional dry clean':'机洗或专业干洗','Hand wash':'手洗','Wipe clean':'擦拭干净','Warm iron':'中温熨烫','Do not dry clean':'不可干洗','Do not bleach':'不可漂白','Do not iron':'不可熨烫','Hand wash only':'仅手洗','Do not tumble dry':'不可滚筒烘干','Do Not Machine Wash':'不可机洗','Line dry':'晾晒','Hand wash cold':'冷水手洗','Chlorine bleach':'含氯漂白','Use mild detergent':'使用温和洗涤剂','Tumble dry low':'低温滚筒烘干','Flat dry':'平铺晾干','Do not wring':'不可拧干','Dry clean':'干洗','Do not soak':'不可浸泡','Air dry':'自然晾干','Drip dry':'滴干','Tumble dry':'滚筒烘干','Dry in shade':'阴干','Non-chlorine bleach':'非氯漂白','Wash Before First Use':'首次使用前清洗','Turn Inside Out Before Washing':'翻面清洗','Wash Separately':'分开洗涤',
 'Crochet':'钩针编织','Knitting':'针织','Woven':'梭织','Non-braided':'非编织','Plaited':'编辫',
 'Brief':'三角裤','Thong':'丁字裤','Shorts':'短裤','Mid-thigh':'大腿中部','Leggings':'打底裤','Capri':'七分裤','Knee-length':'及膝','Pants':'长裤','Panties':'内裤',
 'Compression':'塑形压缩','Fitted':'合身','Regular':'常规','Slim':'修身','Tight':'紧身','Tight-fitting':'贴身','Snug fit':'舒适贴合','Form-fitting':'贴合身形','Compression fit':'压缩贴合','Close-Fitting':'紧密贴合',
 'Sports':'运动','Casual':'休闲','Basic':'基础','Boho':'波西米亚','Elegant':'优雅','Cute':'可爱','Minimalist':'极简','Preppy':'学院风','Retro':'复古','Fashion':'时尚','Modest':'保守','Vintage':'怀旧','Classic':'经典','Glam':'华丽','Romance':'浪漫','Hawaiian':'夏威夷','Simple':'简约','Korean':'韩系','Sexy':'性感',
 'Back support':'背部支撑','Tummy control':'腹部控制','Bust support':'胸部支撑','Hip Lifting':'提臀','Posture Support':'姿态支撑','Waist Slimming':'腰部塑形','Breathable':'透气','Washable':'可水洗','Smoothing':'平滑','Anti-Slip':'防滑','Comfort':'舒适','Fast drying':'速干','Temperature regulation':'温度调节','Supportive':'支撑性','Support':'支撑',
 'Shapewear corset':'塑身胸衣','Shaping knickers':'塑形内裤','Leg shaper':'腿部塑形','Body Shaper':'全身塑形','Butt Lifting':'提臀','Waist cincher':'收腰带','Shapewear':'塑身衣','Waist trainers':'收腰训练衣','Waist Slimming':'腰部塑形','Underbust':'下胸围','Shapewear bodysuit':'塑身连体衣','Mid thigh shaper':'大腿中部塑形','Strapless':'无肩带','Leotard':'紧身衣','Crotchless':'开裆','Overbust':'上胸围','Abdominal Compression':'腹部压缩','Waist Training':'腰部训练',
 'Not sure':'不确定','Asymmetrical':'不对称','Contrast binding':'撞色包边','Contrast lace':'撞色蕾丝','Criss cross':'交叉','Frill':'荷叶边','Hollow':'镂空','Lettuce trim':'波浪边','Plicated':'褶皱','Raw hem':'毛边','Ruffle':'荷叶边','Shirred':'抽褶','Tiered layer':'分层','Twist':'扭结','Scallop':'扇贝边','Cross':'交叉',
 'Holiday':'节日','Basics':'基础款','Indonesia':'印度尼西亚','UK':'英国','Singapore':'新加坡','Malaysia':'马来西亚','Japan':'日本','Philippines':'菲律宾','Thailand':'泰国','Australia':'澳大利亚','Korea':'韩国','United Kingdom':'英国','Brasil':'巴西','Taiwan':'中国台湾','Argentina':'阿根廷','Germany':'德国','Chile':'智利','France':'法国','Sweden':'瑞典','Canada':'加拿大','India':'印度','America':'美国','Europe':'欧洲','Mexico':'墨西哥','New Zealand':'新西兰','South africa':'南非','Bangladesh':'孟加拉国','Mainland China':'中国大陆',
 'Lapel':'翻领','Mandarin collar':'立领','Peter pan collar':'娃娃领','Shawl collar':'披肩领','Chelsea collar':'切尔西领','Jabot collar':'褶饰领','Bib collar':'围兜领','Swallow collar':'燕尾领','Funnel collar':'漏斗领','Polo collar':'POLO领','Gothic pointed collar':'哥特尖领','Mohair':'马海毛','Knit':'针织','Tencel':'天丝','Felt':'毛毡','Chiffon':'雪纺','Cupro':'铜氨纤维','Rib':'罗纹','Crepe':'绉纱','French terry':'法式毛圈','Metallic':'金属质感','Stain':'缎面','Twill':'斜纹','Jacquard':'提花','Jersey spandex':'弹力针织',
 'Cap sleeve':'盖袖','Halter':'挂脖','Spaghetti strap':'细肩带','Cap':'短袖','Expandable':'可扩展','Foldable':'可折叠','Hangable':'可悬挂','High-capacity':'大容量','Moisture-wicking':'吸湿排汗','Multi-function':'多功能','Posture improvement':'改善姿态','Tummy slimming':'腹部塑形','Waist reduction':'腰围收紧','Sculpting':'塑形','Built-in bra':'内置文胸','Mid-thigh length':'大腿中部长度','Durable':'耐用','Flat Seam':'平整接缝','Contouring':'轮廓塑形','Non-Rolling':'防卷边','Traceless':'无痕','SNAP closure':'按扣闭合','Hourglass':'沙漏型','Hypoallergenic':'低敏','Wire-free':'无钢圈','Front closure':'前开式','Hook and eye':'钩眼扣','Easy on and off':'穿脱方便','Defined Waist':'收腰','Easy care':'易护理','Built-in shapewear':'内置塑身衣','Antibacterial':'抗菌','Secure Closure':'稳固闭合','Unlined':'无衬里','Button front':'前排扣','Post-Surgery':'术后','Reinforced':'加固','Adjustable Buckle':'可调节扣','Antimicrobial':'抑菌','Powernet':'高弹网布','Contoured':'立体剪裁','Hooks':'挂钩','Thermal':'保暖','Corset':'束身衣','See-Through Mesh':'透视网眼','Hip Support':'臀部支撑','Ribbed':'罗纹','Ruched':'抽褶','V-neck':'V领','Wire free':'无钢圈','Support System':'支撑系统','Shaping':'塑形','Shape Retention':'保持版型','Smooth':'顺滑','Boned':'带骨条','Ultra-Thin':'超薄','Lined':'有衬里','Front opening':'前开口','Removable cups':'可拆卸罩杯','Thin Strap':'细肩带','Stretchable':'可拉伸','Mesh panels':'网眼拼接','Tagless':'无标签','Contoured back':'立体后背','Long Torso':'长上身','Moisture Absorption':'吸湿','No pocket':'无口袋','Unprinted':'无印花','Push-up':'聚拢','Hip Pad':'臀垫','Thermal insulation':'保温','Heavy duty fabric':'厚实面料','High Elasticity':'高弹性','Body Contouring':'全身塑形','all-day support':'全天支撑','Adjustable Closure':'可调节闭合','moderate thickness':'适中厚度','Soft Fabric':'柔软面料','light compression':'轻度压缩','smooth fabric':'顺滑面料','Mesh Detailing':'网眼细节','natural movement':'自然活动','figure-flattering':'修饰身形','Firm Control':'强力控制','soft inner lining':'柔软内衬','double-layer fabric':'双层面料','all-day comfort':'全天舒适','Anti-Roll':'防卷边','Stays in Place':'稳固不移','soft inner layer':'柔软内层','targeted support':'针对性支撑','extra support':'额外支撑','can be worn as outerwear':'可作为外穿','high resilience':'高回弹','Anti-Deformation':'防变形','structured support':'结构支撑','Invisible Zipper':'隐形拉链','cooling effect':'清凉效果','Comfortable Straps':'舒适肩带','Non-Restrictive Fit':'不束缚版型','Curved Seams':'弧形接缝',
 'Petite':'小个子','Standard':'标准','Plus size':'大码','Juniors':'青少年','Heavyweight':'厚重','Silicone':'硅胶','Recycled nylon':'再生尼龙','Plastic':'塑料','Neoprene':'氯丁橡胶','Foam':'泡沫','Tulle':'网纱','Boat neck':'船领','Mock neck':'半高领','Collared neck':'翻领','Halter neck':'挂脖领','Off shoulder':'露肩','Scoop neck':'圆领','Asymmetrical neck':'不对称领','Cowl neck':'垂坠领','Deep V-neck':'深V领','Keyhole neckline':'镂空领','Notched':'缺口领','Tie neck':'系带领','U-neck':'U领','Crew neck':'圆领','Square neck':'方领','Sweetheart neck':'心形领','Round neck':'圆领','Jewel Neck':'宝石领','Plunge':'深开领','Turtleneck':'高领','Diamond':'菱形领',
 'Nyepi':'静居节','Rambu solo':'托拉查葬礼节','Songkran':'宋干节','Machine wash、do not dry clean':'机洗，不可干洗','Machine wash、do not dry clean、wash with the soft detergent':'机洗，不可干洗，请使用柔和洗涤剂','Machine wash':'机洗','Cold wash':'冷水洗',
 'Muscle':'肌肉型','Double strap':'双肩带','Top single handle strap':'顶部单提手带','Spaghetti':'细肩带','Double handle':'双提手','Stealth':'隐藏式','Thick':'加厚','Interchangeable':'可替换','Metal':'金属','Ceramic':'陶瓷','Fixed shoulder strap':'固定肩带','Chain':'链条','X-strap':'X型肩带','Toe loop':'脚趾环','PU leather':'PU皮革','Ring':'环形','Strappy':'多肩带','Tank':'背心式','Detachable shoulder strap':'可拆卸肩带','Shoulder straps':'肩带','Clear Straps':'透明肩带','Buckle':'扣环','Button':'纽扣','Drawstring':'抽绳','Hook and bar':'钩扣','Hook and loop':'魔术贴','Lace up':'系带','Magnetic':'磁吸','Tie':'系带','Toggle':'拉绳扣','Zip':'拉链','Pull on':'套穿','SNAP':'按扣','No closure':'无闭合','Clasp':'搭扣','Gusset':'裆部','Shoulder closure':'肩带闭合','Side closure':'侧面闭合','Button Closure':'纽扣闭合',
 'Wood':'木材','PVC':'PVC','Rubber':'橡胶','Bamboo':'竹','Paper':'纸','Copper':'铜','Steel':'钢','Alloy':'合金','Carbon fiber':'碳纤维','TPU':'TPU','Resin':'树脂','Rhinestone':'水钻','Acetate':'醋酸纤维'
};
const publishAttributeNameLookup=Object.fromEntries(Object.entries(publishAttributeNames).map(([key,value])=>[key.toLowerCase(),value]));
const publishAttributeValueLookup=Object.fromEntries(Object.entries(publishAttributeValues).map(([key,value])=>[key.toLowerCase(),value]));
function publishZhLabel(value){const text=String(value||'').trim();return publishAttributeNames[text]||publishAttributeNameLookup[text.toLowerCase()]||text}
function publishZhValue(value){return String(value||'').split(/\s*,\s*/).map(part=>{const text=String(part||'').trim();if(!text)return '';if(publishAttributeValues[text])return publishAttributeValues[text];if(publishAttributeValueLookup[text.toLowerCase()])return publishAttributeValueLookup[text.toLowerCase()];if(text.length>80)return text;return text.replace(/\b(Adjustable|Anti[- ]?chafing|Anti[- ]?pilling|Breathable|Comfortable|Compression|Control|Cooling|Cotton|Cover|Daily|Design|Dry|Elastic|Elastane|Fabric|Fast|Fit|Flexible|Full|High|Light|Lightweight|Low|Machine|Medium|Mesh|Moisture|Non[- ]?slip|Nylon|Odor[- ]?resistant|Padded|Plain|Polyamide|Polyester|Post[- ]?surgical|Removable|Roll|Seamless|Second[- ]?skin|Skin[- ]?friendly|Soft|Stretch|Support|Tummy|Waist|Waterproof|Wide|with|without|and|or|only|for|use|wear|back|body|chest|front|length|neck|open|piece|pocket|shoulder|strap|thigh|touch|up|down|one|two|layer|side|sleeve|style|type|weight|zipper)\b/gi,word=>({'adjustable':'可调节','anti-chafing':'防摩擦','anti-pilling':'防起球','breathable':'透气','comfortable':'舒适','compression':'压缩','control':'控制','cooling':'清凉','cotton':'棉','cover':'覆盖','daily':'日常','design':'设计','dry':'干燥','elastic':'弹性','elastane':'氨纶','fabric':'面料','fast':'快速','fit':'贴合','flexible':'灵活','full':'全','high':'高','light':'轻','lightweight':'轻量','low':'低','machine':'机器','medium':'中等','mesh':'网眼','moisture':'水分','non-slip':'防滑','non slip':'防滑','nylon':'尼龙','odor-resistant':'防异味','padded':'加垫','plain':'纯色','polyamide':'聚酰胺','polyester':'聚酯纤维','post-surgical':'术后','removable':'可拆卸','roll':'卷边','seamless':'无痕','second-skin':'第二层皮肤','skin-friendly':'亲肤','soft':'柔软','stretch':'弹力','support':'支撑','tummy':'腹部','waist':'腰部','waterproof':'防水','wide':'宽','with':'带有','without':'无','and':'和','or':'或','only':'仅','for':'用于','use':'使用','wear':'穿着','back':'背部','body':'身体','chest':'胸部','front':'前部','length':'长度','neck':'领部','open':'开口','piece':'件','pocket':'口袋','shoulder':'肩部','strap':'肩带','thigh':'大腿','touch':'触感','up':'上','down':'下','one':'一','two':'二','layer':'层','side':'侧面','sleeve':'袖','style':'款式','type':'类型','weight':'重量','zipper':'拉链'}[word.toLowerCase()]||word)).replace(/\s+/g,' ').trim()}).join('、')}
function publishAttributeRequired(a){return (a.type==='PRODUCT_PROPERTY'&&['101395','101400'].includes(String(a.id)))||a.is_required||a.is_requried||(a.required_regions||[]).includes('US')}
function publishAttrInput(a,values){
 const selected=new Set((values||[]).map(x=>String(x.id))),custom=(values||[]).find(x=>x.name)?.name||'';
 const labels={'101395':'是否含有生殖毒性化学物质','101400':'是否含有致癌物质','101619':'是否属于危险品或含有害材料','102418':'有机织物'};
 const name=labels[a.id]||publishZhLabel(a.name),required=publishAttributeRequired(a);
 const placeholder=required?'请选择':'未选择，不提交';
 const chosen=(a.values||[]).filter(v=>selected.has(String(v.id)));
 const summary=custom?publishZhValue(custom):chosen.map(v=>publishZhValue(v.name)).join('、')||placeholder;
 return `<div class="publish-attribute"><span>${esc(name)}${required?' *':''}</span>
 <select hidden data-pub-attr="${esc(a.id)}" ${a.is_multiple_selection?'multiple':''}><option value="">${placeholder}</option>${(a.values||[]).map(v=>`<option value="${esc(v.id)}" ${selected.has(String(v.id))?'selected':''}>${esc(publishZhValue(v.name))}</option>`).join('')}</select>
 <details class="publish-attribute-dropdown" ontoggle="openPublishAttribute(this)"><summary aria-label="${esc(name)}" title="${esc(summary)}"><span data-attr-summary data-placeholder="${placeholder}">${esc(summary)}</span><span class="publish-attribute-chevron" aria-hidden="true">⌄</span></summary>
 <div class="publish-attribute-menu"><div class="publish-attribute-menu-head"><small>${a.is_multiple_selection?'可多选':'请选择一项'}</small><button type="button" class="publish-text-button" onclick="clearPublishAttribute(this)">清除</button></div>
 <div class="publish-attribute-options">${(a.values||[]).map(v=>`<label><input type="${a.is_multiple_selection?'checkbox':'radio'}" name="attr-choice-${esc(a.id)}" value="${esc(v.id)}" ${selected.has(String(v.id))&&!custom?'checked':''} onchange="changePublishAttribute(this)"><span>${esc(publishZhValue(v.name))}</span></label>`).join('')}</div>
 ${a.is_customizable?`<label class="publish-attribute-custom">自定义<input class="search" data-pub-custom="${esc(a.id)}" placeholder="填写自定义值" value="${esc(custom)}" oninput="changePublishAttribute(this)"></label>`:''}
 <button type="button" class="btn small publish-attribute-done" onclick="this.closest('details').open=false">完成</button></div></details></div>`;
}
function openPublishAttribute(dropdown){
 if(!dropdown.open)return;
 document.querySelectorAll('.publish-attribute-dropdown[open]').forEach(other=>{if(other!==dropdown)other.open=false});
 const rect=dropdown.getBoundingClientRect(),below=window.innerHeight-rect.bottom-90,above=rect.top-80;
 const up=below<330&&above>below,available=Math.max(180,Math.min(360,up?above:below));
 dropdown.classList.toggle('opens-up',up);
 dropdown.querySelector('.publish-attribute-menu').style.maxHeight=available+'px';
 dropdown.querySelector('.publish-attribute-options').style.maxHeight=Math.max(70,available-160)+'px';
}
function changePublishAttribute(input){
 const field=input.closest('.publish-attribute'),select=field.querySelector('[data-pub-attr]'),custom=field.querySelector('[data-pub-custom]'),choices=[...field.querySelectorAll('.publish-attribute-options input')];
 if(input===custom){if(custom.value.trim())choices.forEach(c=>c.checked=false)}else if(custom)custom.value='';
 const ids=new Set(choices.filter(c=>c.checked).map(c=>c.value));
 for(const option of select.options)option.selected=ids.has(option.value);
 if(!select.multiple&&!ids.size)select.value='';
 const summary=field.querySelector('[data-attr-summary]');
 summary.textContent=custom?.value.trim()||[...select.selectedOptions].filter(o=>o.value).map(o=>o.textContent).join('、')||summary.dataset.placeholder;
 field.querySelector('summary').title=summary.textContent;
 if(input!==custom&&!select.multiple)field.querySelector('details').open=false;
 invalidatePublishCheck();updatePublishMissing();updatePublishCertificates();
}
function clearPublishAttribute(button){
 const field=button.closest('.publish-attribute');
 field.querySelectorAll('.publish-attribute-options input').forEach(c=>c.checked=false);
 const custom=field.querySelector('[data-pub-custom]');if(custom)custom.value='';
 changePublishAttribute(button);
 invalidatePublishMaterial();
}

function publishMaterialKey(p){const f=p.profile;return JSON.stringify([f.title_input,f.description_input,f.category_id,f.attributes])}
function publishMaterialProfile(p){const f=p.profile;return {title_input:f.title_input,description_input:f.description_input,attributes:f.attributes,category_id:f.category_id,material_confirmation:f.material_confirmation,content_language:'en',editor_language:'en'}}
function publishMaterialPanel(p){
 const r=p.materialReview;
 if(r&&!r.applicable)return '';
 return `<section id="publish-material" class="publish-notice publish-material" aria-labelledby="publish-material-heading">
 <strong id="publish-material-heading">面料交叉核对${r?' · '+({consistent:'一致',conflict:'待处理',unknown:'待确认'}[r.status]||'待确认'):''}</strong>
 <p role="status">${esc(p.materialMessage||r?.message||'对照当前标题、详情、采集属性和上架材质，编辑后请重新核对。')}</p>
 ${r?.source_warning?`<p>${esc(r.source_warning)}</p>`:''}
 ${r?.issues?.length?`<ul>${r.issues.map(issue=>`<li>${esc(issue.message)}</li>`).join('')}</ul>`:''}
 ${r?.evidence?.length?`<details><summary>查看来源对照</summary><dl>${r.evidence.map(row=>`<dt>${esc(row.label)}${row.ignored?' · 其他款式，不用于填写':''}</dt><dd>${esc(row.text)}</dd>`).join('')}</dl></details>`:''}
 ${r?.suggestion?`<p><strong>文案中的面料：${esc(r.suggestion)}</strong></p><p>建议仅来自文字，请以实物标签或供应商资料确认；填写只更新材质属性。</p>`:''}
 <div class="publish-toolbar"><button type="button" class="btn small" onclick="checkPublishMaterial()" ${publishView.busy?'disabled':''}>核对当前内容</button>
 ${r?.source_conflict?`<button type="button" class="btn small" onclick="confirmPublishMaterialSource()" ${publishView.busy?'disabled':''}>已按实物修正文案和材质，确认来源冲突</button>`:''}
 ${r?.status==='conflict'&&Object.keys(r.suggested_attributes||{}).length?`<button type="button" class="btn small" onclick="applyPublishMaterial()" ${publishView.busy?'disabled':''}>已核实实物，填写建议</button>`:''}</div></section>`;
}
function refreshPublishMaterial(){
 const el=document.getElementById('publish-material');
 if(el)el.outerHTML=publishMaterialPanel(publishView.items[publishView.index]);
}
function invalidatePublishMaterial(event){
 if(event&&!['publish-title','publish-description'].includes(event.target.id)&&!event.target.closest('.publish-attribute'))return;
 const p=publishView.items[publishView.index];p.materialReview=null;p.materialMessage='内容已修改，请重新核对面料。';refreshPublishMaterial();
}
async function checkPublishMaterial(){
 const v=publishView;if(v.busy||!v.meta?.category_id)return;
 const p=collectPublishForm(),key=publishMaterialKey(p);
 v.busy=true;p.materialReview=null;p.materialMessage='正在交叉核对面料…';refreshPublishMaterial();
 try{
  const result=await shopPost('listing-material-review',{item:{market:p.market,id:p.id},profile:publishMaterialProfile(p)});
  if(!publishOpen(v)||v.items[v.index]!==p)return;
  collectPublishForm();
  if(key!==publishMaterialKey(p)){p.materialMessage='核对期间内容已修改，请重新核对。';return}
  p.materialReview=result;p.materialReviewKey=key;p.materialMessage='';
 }catch(e){p.materialMessage='核对未完成：'+e.message}
 finally{v.busy=false;if(publishOpen(v)&&v.items[v.index]===p)refreshPublishMaterial()}
}
async function applyPublishMaterial(){
 const v=publishView;if(v.busy)return;
 const p=collectPublishForm(),r=p.materialReview;
 if(!r||p.materialReviewKey!==publishMaterialKey(p)){await checkPublishMaterial();return}
 if(!Object.keys(r.suggested_attributes||{}).length)return;
 p.profile.attributes={...p.profile.attributes,...r.suggested_attributes};
 p.materialReview=null;p.materialMessage='已填写确认的材质，正在重新核对。';
 invalidatePublishCheck();redrawPublishSection();await checkPublishMaterial();
}
async function confirmPublishMaterialSource(){
 if(publishView.busy)return;const p=collectPublishForm(),r=p.materialReview;
 if(!r?.source_conflict||p.materialReviewKey!==publishMaterialKey(p))return checkPublishMaterial();
 p.profile.material_confirmation=r.verification_key;invalidatePublishCheck();await checkPublishMaterial();
}

function publishOpen(view){
 return publishView===view&&!$('#modal').classList.contains('hidden')&&!!$('#publish-form');
}
function publishTemplateOptions(templates){
 return '<option value="">选择已保存的模板</option>'+templates.map(t=>
  `<option value="${esc(t.name)}">${esc(t.name)}</option>`).join('');
}
function publishTemplateControls(view){
 const enabled=view.meta?'':'disabled';
 return `<section class="editor-section publish-templates">
  <h2>仓库与包装模板</h2>
  <div class="publish-template-row">
   <select class="search" id="publish-template" aria-label="仓库与包装模板" ${enabled}>${publishTemplateOptions(view.templates)}</select>
   <button class="btn" type="button" onclick="applyPublishTemplate()" ${enabled}>套用到当前商品</button>
  </div>
  <div class="publish-template-row">
   <input class="search" id="publish-template-name" aria-label="模板名称" maxlength="60" placeholder="模板名称" ${enabled}>
   <button class="btn" type="button" onclick="savePublishTemplate(this)" ${enabled}>保存为模板</button>
  </div>
  <div id="publish-template-status" role="status"></div>
 </section>`;
}
function publishRuleRequired(rule){return !!(rule?.is_required||rule?.is_requried||(rule?.required_regions||[]).includes('US'));}
function publishCompanySize(p,m){
 const f=p.profile,selected=(publishView.sizeTemplates||[]).find(t=>String(t.id)===String(f.size_chart_template_id));
 const chart=selected?.image||publishVisibleImages(p).find(im=>im.key===f.size_chart);
 const required=publishRuleRequired(m?.rules?.size_chart);
 return `<section class="publish-company-size" id="publish-company-size">
 <div class="publish-section-heading">
  <h2>尺码表${required?' *':''}</h2>
  <div class="publish-size-actions">
   <span>${chart?'已带入':p.size_chart_candidates?.length?'已识别 '+p.size_chart_candidates.length+' 张候选，待核实':'未上传'}</span>
   <button class="btn small" type="button" onclick="if(!publishView.busy)this.nextElementSibling.click()">${chart?'更换图片':'上传图片'}</button>
   <input type="file" hidden aria-label="${chart?'更换尺码表图片':'上传尺码表图片'}" accept="image/jpeg,image/png" data-publish-upload="size_chart" onchange="uploadPublishImage(this)">
  </div>
 </div>
 <p>选择适用于本商品的尺码表，核对款式、尺码和单位。采集图片仅作候选。</p>
 <input type="hidden" name="size_chart_template_id" value="${esc(f.size_chart_template_id||'')}">
 <input type="hidden" name="size_chart" value="${esc(f.size_chart||'')}">
 ${chart?`<div class="publish-size-preview-card"><img src="${esc(chart.local_path||chart.url)}" alt="当前上架尺码表"><p>${selected?'已使用公司尺码表':'已选商品尺码表'}</p></div>`:'<p class="publish-size-empty">尚未选用尺码表。可从下方候选中核实选择，或上传本商品的实测表。</p>'}
 ${publishSizeCandidates(p,`choosePublishSize`)}
 <p class="publish-file-hint">JPG / PNG · 最大 8 MB · 宽高 1024–4000 px · 仅替换当前商品</p></section>`;
}

function publishGarmentType(text){
 const kinds=[['tshirt',/t[ -]?shirt|tee\b|T恤/i],['shapewear',/shapewear|waist trainer|faja\b|塑身|束腰/i],['swimwear',/swim|bikini|泳衣/i],['hoodie',/hoodie|sweatshirt|卫衣/i]];
 const found=kinds.filter(([,pattern])=>pattern.test(text||''));return found.length===1?found[0][0]:'';
}
function publishSizeCandidates(p,handler,index=''){
 const kind=publishGarmentType(p.profile.title_input||p.title),candidates=p.size_chart_candidates||[];
 const templates=(publishView.sizeTemplates||[]).filter(t=>!t.image.product_type||!kind||t.image.product_type===kind);
 return `<div class="publish-size-candidates">${candidates.map((im,i)=>`<article><img src="${esc(im.local_path)}" alt="候选尺码表 ${i+1}" loading="lazy"><p>${im.width?`${im.width} × ${im.height} px · `:''}${esc(im.message)}</p><button class="btn small" type="button" onclick="${handler}(${index!==''?index+',':''}'image',${i})" ${!im.valid?'disabled':''}>已核实适用，选用此表</button></article>`).join('')}
 ${templates.length?`<label>公司尺码表（按商品类型筛选）<select class="search" id="publish-size-template-${index}"><option value="">请选择已核实适用的模板</option>${templates.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}${t.image.product_type?'':' · 未标注类型，需核实'}</option>`).join('')}</select></label><button class="btn small" type="button" onclick="${handler}(${index!==''?index+',':''}'template',document.getElementById('publish-size-template-${index}').value)">已核实适用，使用模板</button>`:''}</div>`;
}
function setPublishSize(p,kind,value){
 if(kind==='image'){
  const im=p.size_chart_candidates?.[value];if(!im?.valid)return false;
  p.profile.size_chart=im.key;p.profile.size_chart_confirmed=im.key;p.profile.size_chart_template_id='';
 }else{
  if(!value)return false;p.profile.size_chart_template_id=value;p.profile.size_chart='';
 }
 p.profile.size_table_rendered=null;return true;
}
function choosePublishSize(kind,value){
 if(publishView.busy)return;const p=collectPublishForm();
 if(setPublishSize(p,kind,value)){invalidatePublishCheck();redrawPublishSection()}
}
function confirmPublishInventory(){
 const p=collectPublishForm();p.profile.inventory_verified={...p.profile.stocks};invalidatePublishCheck();updatePublishMissing();
 publishFeedback('已记录本商品库存核实结果；库存变更后需要重新检查。');
}
function excludePublishSoldOut(){
 const p=collectPublishForm(),zero=p.variants.filter(s=>String(p.profile.stocks?.[s.id]??p.profile.stock)==='0').map(s=>String(s.id));
 p.profile.excluded_sku_ids=[...new Set([...(p.profile.excluded_sku_ids||[]),...zero])];invalidatePublishCheck();redrawPublishSection();
}
function restorePublishInventory(){const p=collectPublishForm();p.profile.excluded_sku_ids=[];invalidatePublishCheck();redrawPublishSection()}

function publishBrandControl(f,m){
 const brands=m?.brands||[];
 const saved=f.brand_id&&f.brand_id!=='unbranded'&&!brands.some(b=>String(b.id)===String(f.brand_id));
 return `<div class="publish-grid publish-brand"><label>品牌 *<select class="search" name="brand_id" onchange="publishView.items[publishView.index].profile.brand_confirmed=true"><option value="">${m?'请选择品牌':'正在读取店铺品牌…'}</option><option value="unbranded" ${f.brand_id==='unbranded'?'selected':''}>无品牌</option>${brands.map(b=>`<option value="${esc(b.id)}" ${String(b.id)===String(f.brand_id)?'selected':''} ${b.brand_status==='UNAVAILABLE'?'disabled':''}>${esc(b.name)}${b.brand_status==='UNAVAILABLE'?'（此类目不可用）':''}</option>`).join('')}${saved?`<option value="${esc(f.brand_id)}" selected>${esc(f.brand_name||f.brand_id)}（已保存）</option>`:''}</select>${m?.brand_message?`<small role="status">${esc(m.brand_message)}</small>`:''}<small>请选择实际商品品牌；授权列表不代表商品归属。</small></label><label><input type="checkbox" name="brand_confirmed" ${f.brand_confirmed?'checked':''}>已核实商品品牌归属</label></div>`;
}
function publishPackageFields(f,m){
 const item=publishView.items[publishView.index],hasValues=['weight','length','width','height'].every(k=>+f[k]>0);
 const loading=!item.packageError&&(!m||m.package_status==='loading')&&!!publishView.connection.selected_shop_id;
 const labels={predicted:'已自动填写',empty:'暂无预测',failed:'获取失败',category_missing:'待识别类目',not_requested:'未获取'};
 const status=item.packageError?'获取失败':loading?'正在获取':hasValues?'已填写':labels[m?.package_status]||'未连接店铺';
 const predicted=hasValues&&Object.keys(f.package_prediction?.values||{}).length>0;
 const message=item.packageError||m?.package_message||(!loading&&predicted?'来源：TikTok 类目包装预测，已自动填入，可按实际包装调整。':'');
 return `<section class="publish-package" id="publish-package" aria-label="自动包装资料" aria-busy="${loading}"><div class="publish-section-heading"><h2>包装信息</h2><span>${predicted&&!loading&&!item.packageError?'已自动填写':status}</span></div><div class="publish-package-values">${[['weight','重量','kg'],['length','长','cm'],['width','宽','cm'],['height','高','cm']].map(([key,label,unit])=>`<label>${label}<div><input name="${key}" type="number" step="${key==='weight'?'any':'1'}" min="${key==='weight'?'0.001':'1'}" value="${esc(f[key]||'')}" aria-label="包装${label}"><span>${unit}</span></div></label>`).join('')}</div>${message?`<p role="status">${esc(message)}</p>`:''}</section>`;
}

function publishSectionHeading(id,title){
 const collapsed=!!publishView.items[publishView.index].collapsedSections?.[id];
 return `<div class="publish-section-heading publish-fold-heading"><h2>${title}</h2><button type="button" class="publish-fold-toggle" aria-label="${collapsed?'展开':'收起'}${title}" aria-expanded="${!collapsed}" aria-controls="${id}-content" data-section-title="${title}" onclick="togglePublishSection('${id}')"><span>${collapsed?'展开':'收起'}</span><span class="publish-fold-chevron" aria-hidden="true">⌃</span></button></div>`;
}
function togglePublishSection(id,collapsed){
 const p=publishView.items[publishView.index],section=document.getElementById(id);
 if(!section)return;
 p.collapsedSections||={};
 const hidden=collapsed??!p.collapsedSections[id];p.collapsedSections[id]=hidden;
 document.getElementById(id+'-content').hidden=hidden;
 const button=section.querySelector('.publish-fold-toggle');
 button.setAttribute('aria-expanded',String(!hidden));
 button.setAttribute('aria-label',(hidden?'展开':'收起')+button.dataset.sectionTitle);
 button.querySelector('span').textContent=hidden?'展开':'收起';
}
function jumpPublishSection(id){
 const target=document.getElementById(id);if(!target)return;
 for(const sectionId of ['publish-variants','publish-compliance']){
  if(document.getElementById(sectionId)?.contains(target))togglePublishSection(sectionId,false);
 }
 for(let parent=target;parent;parent=parent.parentElement){if(parent.tagName==='DETAILS')parent.open=true;}
 target.scrollIntoView({block:'start'});
}
function togglePublishSkus(button){
 const p=publishView.items[publishView.index];p.skusCollapsed=!p.skusCollapsed;
 $('#publish-sku-content').hidden=p.skusCollapsed;
 button.textContent=p.skusCollapsed?'展开规格':'收起规格';button.setAttribute('aria-expanded',String(!p.skusCollapsed));
}
function redrawPublishSection(){const scroll=$('.publish-drawer').scrollTop;drawPublish();$('.publish-drawer').scrollTop=scroll;}
function publishSkuGroups(p){
 const attrs=p.variants.map(s=>p.profile.sku_attributes?.[s.id]||s.attributes||[]);
 const first=attrs[0]||[];
 let colorIndex=first.findIndex(a=>/^(colou?r|colou?r family|颜色|色彩|顏色)$/i.test(a.name.trim()));
 if(colorIndex<0)colorIndex=first.findIndex(a=>/^(style|款式|花色)$/i.test(a.name.trim()));
 const colors=[],bySku=[];
 for(const values of attrs){
  const value=colorIndex<0?'全部尺码':String(values[colorIndex]?.value||'未命名颜色');
  let group=colors.findIndex(c=>c.value===value);
  if(group<0){group=colors.length;colors.push({value,count:0});}
  colors[group].count++;bySku.push(group);
 }
 return {colors,bySku,colorIndex};
}
function selectPublishColor(index){
 const p=publishView.items[publishView.index];p.selectedColor=index;
 document.querySelectorAll('[data-pub-color]').forEach(b=>b.setAttribute('aria-pressed',String(+b.dataset.pubColor===index)));
 document.querySelectorAll('[data-sku-color]').forEach(row=>row.hidden=+row.dataset.skuColor!==index);
 const group=publishSkuGroups(p).colors[index];
 $('#publish-color-heading').textContent=group.value+' · '+group.count+' 个规格';
 $('#publish-sku-rows').scrollTop=0;
}
function togglePublishColors(){
 const p=collectPublishForm();p.colorsExpanded=!p.colorsExpanded;redrawPublishSection();
}
function addPublishColor(){
 const p=collectPublishForm(),groups=publishSkuGroups(p); if(groups.colorIndex<0)return toast('未识别到颜色 / 款式字段');
 const value=prompt('请输入新增的颜色 / 款式名称'); if(!value?.trim())return;
 const source=p.variants.filter((s,i)=>groups.bySku[i]===Math.max(0,p.selectedColor)); if(!source.length)return toast('请先选择一个已有颜色作为规格模板');
 const stamp=Date.now(); source.forEach((base,i)=>{const copy=JSON.parse(JSON.stringify(base));copy.id='manual-'+stamp+'-'+i;copy.original_sku='';copy.seller_sku='';copy.sku_status='未上架';copy.attributes=(copy.attributes||[]).map((a,j)=>j===groups.colorIndex?{...a,value:value.trim()}:{...a});copy.price='';p.variants.push(copy)});
 p.selectedColor=publishSkuGroups(p).colors.findIndex(c=>c.value===value.trim());invalidatePublishCheck();redrawPublishSection();toast('已添加颜色 / 款式，请补充价格、库存和 SKU');
}
function removePublishColor(index){
 const p=collectPublishForm(),groups=publishSkuGroups(p);
 if(!groups.colors[index])return;
 if(groups.colors.length===1)return toast('至少保留一个颜色 / 款式');
 const selected=groups.colors[p.selectedColor]?.value;
 (p.removedColors||=[]).push({variants:[...p.variants],selectedColor:p.selectedColor,removed:p.variants.filter((s,i)=>groups.bySku[i]===index)});
 p.skuExclusions||={};
 p.variants=p.variants.filter((s,i)=>{if(groups.bySku[i]!==index)return true;p.skuExclusions[s.id]=true;return false;});
 p.selectedColor=Math.max(0,publishSkuGroups(p).colors.findIndex(c=>c.value===selected));
 invalidatePublishCheck();redrawPublishSection();
}
function removePublishSku(id){
 const p=collectPublishForm(),index=p.variants.findIndex(s=>String(s.id)===String(id));
 if(index<0)return;
 if(p.variants.length===1)return toast('至少保留一个规格');
 const groups=publishSkuGroups(p),selected=groups.colors[p.selectedColor]?.value;
 const [variant]=p.variants.splice(index,1);
 p.skuExclusions||={};p.skuExclusions[variant.id]=true;
 (p.removedSkus||=[]).push({variant,index});
 p.selectedColor=Math.max(0,publishSkuGroups(p).colors.findIndex(c=>c.value===selected));
 invalidatePublishCheck();redrawPublishSection();
}
function undoPublishSku(){
 const p=collectPublishForm(),last=p.removedSkus?.pop();if(!last)return;
 p.variants.splice(Math.min(last.index,p.variants.length),0,last.variant);p.skuExclusions[last.variant.id]=false;
 invalidatePublishCheck();redrawPublishSection();
}
function undoPublishColor(){
 const p=collectPublishForm(),last=p.removedColors?.pop();if(!last)return;
 p.variants=last.variants;p.selectedColor=last.selectedColor;
 for(const s of last.removed)p.skuExclusions[s.id]=false;
 invalidatePublishCheck();redrawPublishSection();
}

function publishVisibleImages(p){
 const hidden=p.imageEdits||{};
 return (p.images||[]).filter(im=>!im.hidden&&!hidden[im.key]?.hidden);
}
function publishImageChanges(p){
 const visible=publishVisibleImages(p),changes=visible.map((im,index)=>({key:im.key,hidden:false,position:index}));
 for(const [key,edit] of Object.entries(p.imageEdits||{}))if(edit.hidden)changes.push({key,hidden:true});
 return changes;
}
function publishImageGroups(p){
 return [{name:'商品图',images:publishVisibleImages(p)}];
}
function publishImageGallery(p,f){
 const selected=new Set(p.imageSelectionKeys||[]),main=new Set(f.main_image_keys||[]),detail=new Set(f.description_image_keys||[]),groups=publishImageGroups(p);
 if(!groups[0].images.length)return '<div id="publish-image-gallery"><p class="publish-muted">暂无图片，请上传至少一张商品主图。</p></div>';
 const content=groups.map((group,groupIndex)=>`<details class="publish-image-group" data-publish-image-group="${esc(group.name)}" ${groupIndex===0?'open':''}><summary class="publish-image-group-summary"><span><strong>${esc(group.name)}</strong><small>${group.images.length} 张</small></span><span class="publish-image-group-chevron" aria-hidden="true">⌄</span></summary><div class="publish-image-group-body"><div class="publish-image-group-actions"><button class="btn small" type="button" onclick="clearSelectedPublishImages(event)">取消选择</button><button class="btn small editor-delete" type="button" data-pub-image-action onclick="deleteSelectedPublishImages(event)" ${selected.size?'':'disabled'}>删除</button><button class="btn small" type="button" data-pub-image-action onclick="downloadSelectedPublishImages(event)" ${selected.size?'':'disabled'}>批量下载</button><button class="btn small" type="button" data-pub-image-action onclick="openSelectedPublishImageUpload(event)" ${selected.size?'':'disabled'}>批量替换</button><input type="file" hidden multiple accept="image/jpeg,image/png,image/webp,image/gif" aria-label="批量替换图片" onchange="uploadSelectedPublishImages(this)"></div><div class="publish-images">${group.images.map((im,index)=>{const matches=(im.text_matches||[]).map(x=>x.name).join('、');const evidence=[...new Set((im.text_matches||[]).map(x=>(x.region||'全图')+(x.match_type==='variant'?' · 字形近似':'')))].join('、');const note=matches?'疑似竞品店铺/品牌：'+matches+'（'+evidence+'）':'';const src=im.local_path||im.url;const alt=`${group.name} 图片 ${index+1}`;const checkbox=`<input type="checkbox" data-pub-image-select="${esc(im.key)}" ${selected.has(im.key)?'checked':''}>`;return `<article class="publish-image-card ${note?'publish-image-review':''} ${selected.has(im.key)?'publish-image-selected':''}" draggable="true" data-publish-image-card="${esc(im.key)}" title="拖动图片调整顺序"><div class="publish-image-card-tools"><span class="publish-image-drag" aria-hidden="true">⋮⋮</span><label class="publish-image-toggle publish-select-toggle">${checkbox}<span>选择</span></label></div><button type="button" class="publish-image-preview" data-publish-preview-src="${esc(src)}" data-publish-preview-alt="${esc(alt)}" aria-label="放大查看${esc(alt)}"><img src="${esc(src)}" alt="${esc(alt)}" loading="lazy"></button><span>${esc(({size_chart:'尺码表',reminder:'说明图'})[im.role]||'图片 '+(index+1))}${note?`<small>${esc(note)}</small>`:''}</span><select class="publish-image-role" data-pub-image-role="${esc(im.key)}" aria-label="图片 ${index+1} 用途"><option value="none" ${!main.has(im.key)&&!detail.has(im.key)?'selected':''}>不用于上架</option><option value="main" ${main.has(im.key)?'selected':''}>商品主图</option><option value="detail" ${detail.has(im.key)?'selected':''}>详情图片</option></select></article>`}).join('')}</div></div></details>`).join('');
 return `<div id="publish-image-gallery">${content||'<p class="publish-muted">暂无图片，请上传至少一张商品主图。</p>'}</div>`;
}
function publishDescriptionPreview(p){
 const keys=new Set(p.profile.description_image_keys||[]),images=publishVisibleImages(p).filter(im=>keys.has(im.key));
 if(!images.length)return '';
 return `<strong>详情图片预览 · ${images.length} 张</strong><p>以下图片将按顺序显示在详情文字后，可在「商品图片」区域调整用途。</p><div class="publish-description-thumbnails">${images.map((im,index)=>`<figure><img src="${esc(im.local_path||im.url)}" alt="详情图片 ${index+1}" loading="lazy"><figcaption>${index+1}</figcaption></figure>`).join('')}</div>`;
}
function updatePublishImageControls(){
 const p=publishView?.items?.[publishView.index],count=(p?.imageSelectionKeys||[]).length;
 document.querySelectorAll('[data-pub-image-action]').forEach(button=>button.disabled=!count||!!publishView.busy);
 document.querySelectorAll('[data-publish-image-card]').forEach(card=>card.classList.toggle('publish-image-selected',(p?.imageSelectionKeys||[]).includes(card.dataset.publishImageCard)));
 const preview=document.getElementById('publish-description-preview');if(preview&&p)preview.innerHTML=publishDescriptionPreview(p);
}
function changePublishImageRole(event){
 const input=event.target;if(!input.matches('[data-pub-image-role]'))return;
 const p=publishView.items[publishView.index],role=input.value,key=input.dataset.pubImageRole,limit=role==='main'?9:30;
 if(role!=='none'&&[...document.querySelectorAll('[data-pub-image-role]')].filter(x=>x.value===role).length>limit){
  input.value=(p.profile.main_image_keys||[]).includes(key)?'main':(p.profile.description_image_keys||[]).includes(key)?'detail':'none';
  return publishFeedback(role==='main'?'商品主图最多选择 9 张':'商品详情最多放入 30 张图片');
 }
 collectPublishForm();updatePublishImageControls();
}
function clearSelectedPublishImages(event){
 event?.stopPropagation();const p=publishView?.items?.[publishView.index];if(!p)return;
 p.imageSelectionKeys=[];document.querySelectorAll('[data-pub-image-select]').forEach(input=>{input.checked=false});updatePublishImageControls();publishFeedback('已取消操作勾选，主图和详情图片的用途保持不变。');
}
function openSelectedPublishImageUpload(event){
 event?.stopPropagation();const input=event?.currentTarget?.parentElement?.querySelector('input[type=file]');if(input&&!input.disabled)input.click();
}
function downloadSelectedPublishImages(){
 const p=publishView?.items?.[publishView.index],keys=[...(p?.imageSelectionKeys||[])];if(!p||!keys.length)return publishFeedback('请先勾选需要操作的图片');
 const query=new URLSearchParams({market:p.market,id:p.id,keys:JSON.stringify(keys)}),link=document.createElement('a');link.href='/api/gallery/download?'+query.toString();link.download='';document.body.append(link);link.click();link.remove();publishFeedback(`已开始下载 ${keys.length} 张图片，请在下载文件夹中编辑后再批量上传替换。`);
}
function readPublishImageFile(file){
 return new Promise((resolve,reject)=>{if(!['image/jpeg','image/png','image/webp','image/gif'].includes(file.type)||file.size>8*1024*1024)return reject(Error('请选择最大 8 MB 的 JPG、PNG、WebP 或 GIF 图片'));const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('读取图片失败'));reader.readAsDataURL(file)});
}
async function uploadSelectedPublishImages(input){
 const v=publishView,p=v?.items?.[v.index],keys=[...(p?.imageSelectionKeys||[])],files=[...input.files];if(!p||v.busy||!keys.length||!files.length)return;
 if(files.length!==keys.length){input.value='';return publishFeedback(`已选择 ${keys.length} 张图片，请同时上传 ${keys.length} 张替换图，顺序需与下载文件一致。`)}
 v.busy=true;
 try{
  const data=[];for(let i=0;i<files.length;i++){publishFeedback(`正在读取第 ${i+1} / ${files.length} 张替换图…`);data.push(await readPublishImageFile(files[i]))}
  const url='/api/listing-editor?market='+encodeURIComponent(p.market)+'&id='+encodeURIComponent(p.id),before=await api(url),current=before.images.filter(im=>!im.hidden);
  if(keys.some(key=>!current.some(im=>im.key===key)))throw Error('部分图片已被修改，请退出批量模式后重新选择');
  const replacements=new Map(keys.map((key,index)=>[key,data[index]])),images=current.map((im,index)=>({key:im.key,hidden:false,position:index,...(replacements.has(im.key)?{data:replacements.get(im.key)}:{})}));
  const response=await fetch('/api/listing-editor',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({market:p.market,id:p.id,revision:before.revision,title:before.title,price:before.price,images})}),result=await response.json();if(!response.ok)throw Error(result.error||'批量替换失败');
  const after=await api(url);p.images=after.images.filter(i=>!i.hidden);p.revision=after.revision;p.imageSelectionKeys=[];invalidatePublishCheck();drawPublish();publishFeedback(`已替换 ${keys.length} 张图片并保存。`);
 }catch(e){if(publishOpen(v))publishFeedback(e.message)}finally{v.busy=false;input.value='';updatePublishImageControls()}
}
async function deleteSelectedPublishImages(){
 const v=publishView,p=collectPublishForm(),keys=[...(p?.imageSelectionKeys||[])];if(!p||v.busy||!keys.length)return publishFeedback('请先勾选需要删除的图片');
 const remaining=(p.profile.main_image_keys||[]).filter(key=>!keys.includes(key));
 if(!remaining.length)return publishFeedback('至少保留 1 张主图，请先取消选择一张主图');
 p.imageEdits={...p.imageEdits};keys.forEach(key=>{p.imageEdits[key]={...(p.imageEdits[key]||{}),hidden:true}});p.profile.main_image_keys=remaining;invalidatePublishCheck();v.busy=true;
 try{if(await savePublishForm(true)){p.imageSelectionKeys=[];drawPublish();publishFeedback(`已删除 ${keys.length} 张图片并保存。`)}}
 catch(e){if(publishOpen(v))publishFeedback(e.message)}finally{v.busy=false;updatePublishImageControls()}
}
function openPublishImageLightbox(src,alt){
 const dialog=document.createElement('dialog');dialog.className='publish-image-lightbox';dialog.setAttribute('aria-label','商品图片大图');
 const img=document.createElement('img');img.src=src;img.alt=alt;
 const close=document.createElement('button');close.type='button';close.className='publish-image-lightbox-close';close.textContent='×';close.setAttribute('aria-label','关闭大图');close.onclick=()=>dialog.close();
 dialog.append(img,close);document.body.append(dialog);dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close()});dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();
}
function initPublishImageDnD(){
 const gallery=$('#publish-image-gallery');if(!gallery)return;
 gallery.addEventListener('click',event=>{const preview=event.target.closest('[data-publish-preview-src]');if(preview)openPublishImageLightbox(preview.dataset.publishPreviewSrc,preview.dataset.publishPreviewAlt)});
 gallery.addEventListener('dragstart',event=>{const card=event.target.closest('[data-publish-image-card]');if(!card)return;event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',card.dataset.publishImageCard);card.classList.add('is-dragging')});
 gallery.addEventListener('dragend',event=>event.target.closest('[data-publish-image-card]')?.classList.remove('is-dragging'));
 gallery.addEventListener('dragover',event=>{if(event.target.closest('[data-publish-image-card]'))event.preventDefault()});
 gallery.addEventListener('drop',event=>{event.preventDefault();const card=event.target.closest('[data-publish-image-card]'),key=event.dataTransfer.getData('text/plain');if(card&&key&&card.dataset.publishImageCard!==key)reorderPublishImage(key,card.dataset.publishImageCard)});
}
function reorderPublishImage(fromKey,toKey){
 const p=collectPublishForm(),images=publishVisibleImages(p),from=images.findIndex(im=>im.key===fromKey),to=images.findIndex(im=>im.key===toKey);
 if(from<0||to<0||from===to)return;
 const [image]=images.splice(from,1);images.splice(to,0,image);
 const hidden=new Set((p.images||[]).filter(im=>!images.includes(im)).map(im=>im.key));
 p.images=[...images,...(p.images||[]).filter(im=>hidden.has(im.key))];
 invalidatePublishCheck();redrawPublishSection();
}
function publishSkuEditor(p){
 const f=p.profile,groups=publishSkuGroups(p);
 if(!groups.colors[p.selectedColor])p.selectedColor=0;
 return `<div class="publish-skus"><div class="publish-color-label">${groups.colorIndex<0?'规格明细':'先选择颜色 / 款式'}<span>共 ${p.variants.length} 个 SKU <button class="publish-text-button" type="button" aria-expanded="${!p.skusCollapsed}" aria-controls="publish-sku-content" onclick="togglePublishSkus(this)">${p.skusCollapsed?'展开规格':'收起规格'}</button></span></div><div id="publish-sku-content" ${p.skusCollapsed?'hidden':''}>
 <div class="publish-color-options" role="group" aria-label="选择颜色">${groups.colors.map((c,i)=>`<div class="publish-color-card" ${!p.colorsExpanded&&i>=6&&i!==p.selectedColor?'hidden':''}><button type="button" data-pub-color="${i}" aria-pressed="${i===p.selectedColor}" onclick="selectPublishColor(${i})"><strong>${esc(c.value)}</strong><small>${c.count} 个规格</small></button><button class="publish-color-remove" type="button" aria-label="删除颜色 ${esc(c.value)}" title="删除此颜色及对应规格" onclick="removePublishColor(${i})" ${groups.colors.length===1?'disabled':''}>×</button></div>`).join('')}</div>
 <div class="publish-color-actions"><button class="publish-text-button" type="button" onclick="addPublishColor()">＋添加颜色/款式</button>${groups.colors.length>6?`<button class="publish-text-button" type="button" aria-expanded="${!!p.colorsExpanded}" onclick="togglePublishColors()">${p.colorsExpanded?'收起颜色':'展开全部 '+groups.colors.length+' 个颜色'}</button>`:''}${p.removedColors?.length?'<button class="publish-text-button" type="button" onclick="undoPublishColor()">撤销删除颜色</button>':''}${p.removedSkus?.length?'<button class="publish-text-button" type="button" onclick="undoPublishSku()">撤销删除规格</button>':''}</div>
 <h3 id="publish-color-heading">${esc(groups.colors[p.selectedColor]?.value||'规格')} · ${groups.colors[p.selectedColor]?.count||0} 个规格</h3><p class="publish-color-help">下方为当前颜色的尺码、售价与库存，切换颜色会保留已填内容。</p>
 <div class="publish-stock-actions"><button class="btn small" type="button" onclick="excludePublishSoldOut()">排除库存为 0 的规格</button><button class="btn small" type="button" onclick="restorePublishInventory()">恢复本次排除的规格（${(f.excluded_sku_ids||[]).length}）</button><button class="btn small" type="button" onclick="confirmPublishInventory()">已核实当前库存为实际可售库存</button></div><div id="publish-sku-rows">${p.variants.map((s,index)=>`<div class="publish-sku-edit" data-sku-color="${groups.bySku[index]}" ${groups.bySku[index]===p.selectedColor?'':'hidden'}><small class="publish-sku-source">规格 ${esc(s.id)} · 原始竞品 SKU：${esc(s.original_sku||'未提供')} · SKU 状态：${esc(s.sku_status||'未上架')} ${(f.excluded_sku_ids||[]).includes(String(s.id))?' · 已排除本次上架':''}</small><div class="publish-sku-attrs">${(f.sku_attributes?.[s.id]||s.attributes||[]).map((a,i)=>`${i===groups.colorIndex?`<input type="hidden" data-pub-spec="${esc(s.id)}" data-spec-index="${i}" value="${esc(a.value)}">`:`<label>${/^(size|尺码|尺寸)$/i.test(a.name)?'尺码':esc(publishZhLabel(a.name))}<input class="search" aria-label="${esc(publishZhLabel(a.name))} ${esc(s.id)}" data-pub-spec="${esc(s.id)}" data-spec-index="${i}" value="${esc(a.value)}" maxlength="50"></label>`}`).join('')}</div><label>上架售价（USD）<input class="search" aria-label="上架售价 ${esc(s.id)}" type="number" min="0.01" step="0.01" data-pub-price="${esc(s.id)}" value="${esc(s.price||'')}"></label><label>可售库存<input class="search" aria-label="库存 ${esc(s.id)}" type="number" min="0" max="99999" step="1" data-pub-stock="${esc(s.id)}" value="${esc(f.stocks?.[s.id]??f.stock??'')}"></label><small>竞品库存参考：${esc(p.source?.stocks?.[s.id]??'未知')} · 不代表实际可售库存</small><details class="publish-sku-codes" open><summary>本地 SKU（上架使用）</summary><label>本地 SKU *<input class="search" data-pub-seller="${esc(s.id)}" maxlength="50" placeholder="TK-年月日-6位流水号-16位随机码" value="${esc(f.seller_skus?.[s.id]||s.seller_sku||'')}"></label><small>此编码由本机数据库永久保存；修改会记录操作日志。</small></details><button class="publish-sku-remove" type="button" aria-label="删除规格 ${esc(s.id)}" title="删除这个规格" onclick="removePublishSku('${esc(s.id)}')" ${p.variants.length===1?'disabled':''}>×</button></div>`).join('')||'<p>没有有效规格，请先完成商品采集。</p>'}</div></div></div>`;
}
function drawPublish(){
 const v=publishView,p=v.items[v.index],f=p.profile,m=v.meta;
 initializePublishContent(p);
 p.imageEdits||=(p.profile.imageEdits||{});
 p.imageSelectionKeys||=[];
 p.collapsedSections||={};p.collapsedSections['publish-variants']??=false;
 const select=(arr,key,value)=>'<option value="">请选择</option>'+arr.map(x=>`<option value="${esc(x.id)}" ${String(x.id)===String(value)?'selected':''}>${esc(x[key]||x.name||x.id)} · ${esc(x.id)}</option>`).join('');
 $('#modal').innerHTML=`<div class="drawer product-editor publish-drawer" role="dialog" aria-modal="true" aria-labelledby="publish-heading">
  <div class="drawer-top"><div><span class="eyebrow">TIKTOK SHOP · 美国站</span><h2 id="publish-heading">准备上架 · ${v.index+1} / ${v.items.length}</h2></div>
   <button class="close" aria-label="关闭上架" onclick="closeModal()">×</button></div>


  ${v.reviewStarted?publishReviewEditBanner(p):''}
  <div class="publish-notice publish-shop-notice"><label>目标店铺 <select class="search" id="publish-shop" onchange="changePublishShop(this.value)" ${v.busy?'disabled':''}><option value="">请选择已绑定的店铺</option>${(v.connection.shops||[]).map(s=>`<option value="${esc(s.id)}" ${String(s.id)===String(v.connection.selected_shop_id)?'selected':''}>${esc(s.name||s.id)} · ${esc(s.region||'未知')}</option>`).join('')}</select></label><button class="btn small" type="button" onclick="openShopFromPublish()">店铺管理 / 绑定店铺</button></div>
  <div id="publish-missing-panel" class="publish-required-status" aria-live="polite"><strong id="publish-missing-heading">正在检查必填资料</strong><p id="publish-missing"></p><div id="publish-missing-links"></div></div>
  ${publishCoverage(p,m)}
  <form id="publish-form" onsubmit="event.preventDefault();if(!publishView.busy)savePublishForm()"><p class="publish-form-hint">带 * 的项目必填，选填内容已默认收起。已带入的资料保留，核对后即可提交。<br>直接编辑竞品英文标题和详情，保存后按英文原文提交。</p>
   <label class="editor-label">商品标题 *<textarea id="publish-title" rows="2" maxlength="255" placeholder="请输入英文商品标题…">${esc(f.title_input)}</textarea></label>
   ${p.content_review?.requires_review?`<section class="publish-review-warning" role="alert"><strong>需人工复核第三方内容</strong><p>${esc([...(p.content_review.title_matches||[]),...(p.content_review.description_matches||[])].map(x=>x.name).join('、'))}${p.content_review.rights_markers?.length?' · 含权利声明：'+esc(p.content_review.rights_markers.join('、')):''}</p><p>请改用你有权使用的品牌、店铺名和商品描述。系统会在提交前拦截命中项。</p></section>`:''}
   <section class="publish-description"><label class="editor-label">商品详情 *<textarea id="publish-description" name="description_input" rows="12" maxlength="9000" placeholder="请输入英文商品详情，支持分段填写…">${esc(f.description_input)}</textarea></label><p>请核对英文内容；保存后重新打开会保留修改。</p><div id="publish-description-preview" class="publish-description-preview">${publishDescriptionPreview(p)}</div></section>
   ${publishBrandControl(f,m)}
   <div class="publish-grid" id="publish-logistics">${publishCategoryControl(f,m)}
   ${!m||m.warehouses.length===1?`<input type="hidden" name="warehouse_id" value="${esc(m?.warehouses[0]?.id||f.warehouse_id||'')}">`:`<label>店铺发货仓库 *<select class="search" name="warehouse_id">${m?select(m.warehouses,'name',f.warehouse_id):`<option value="${esc(f.warehouse_id||'')}">正在读取</option>`}</select></label>`}
   ${m?.mode==='global'&&m.global_warehouses.length!==1?`<label>全球仓库 *<select class="search" name="global_warehouse_id">${select(m.global_warehouses,'name',f.global_warehouse_id)}</select></label>`:`<input type="hidden" name="global_warehouse_id" value="${esc(m?.global_warehouses?.length===1?m.global_warehouses[0].id:f.global_warehouse_id||'')}">`}</div>
   ${publishPackageFields(f,m)}
   <section class="editor-section" id="publish-photos"><div class="publish-section-heading"><h2>商品图片</h2><span>选择图片后直接操作</span></div>${publishUploadControl("main", "上传新增图片")}${publishImageGallery(p,f)}</section>
   <section class="editor-section" id="publish-variants">${publishSectionHeading('publish-variants','规格、上架售价与库存')}<div id="publish-variants-content" class="publish-fold-content" ${p.collapsedSections?.['publish-variants']?'hidden':''}><p>售价与库存已优先沿用已保存资料，竞品库存仅供参考，请填写真实可售库存。每个规格的本地 SKU 会直接用于 TikTok Shop 上架。</p><div class="publish-batch-row"><input id="publish-price-all" class="search" type="number" min="0.01" step="0.01" placeholder="全部颜色统一售价（USD）"><button class="btn" type="button" onclick="applyPublishPrice()">应用售价</button><input id="publish-stock-all" class="search" type="number" min="1" max="99999" step="1" placeholder="全部颜色统一库存"><button class="btn" type="button" onclick="applyPublishStock()">应用库存</button></div>${publishSkuEditor(p)}</div></section>
   ${publishCompanySize(p,m)}
   ${m?publishMaterialPanel(p):''}
   ${m?`<section class="editor-section" id="publish-compliance">${publishSectionHeading('publish-compliance','商品属性与合规声明')}<div id="publish-compliance-content" class="publish-fold-content" ${p.collapsedSections?.['publish-compliance']?'hidden':''}><p>优先核对下方必填项；选填内容可按需展开，已带入的值会保留。</p>${publishPropertyFields(m,f)}${publishCertifications(m,f)}</div></section>`:''}
   <div id="publish-feedback" role="status" class="editor-error"></div>
   <div class="publish-toolbar"><button class="btn" type="submit">保存当前资料</button></div>
   <div class="editor-footer"><button class="btn" type="button" onclick="movePublish(-1)" ${v.index===0?'disabled':''}>上一件</button><button class="btn" type="button" onclick="movePublish(1)" ${v.index===v.items.length-1?'disabled':''}>下一件</button><button class="btn" type="button" onclick="approvePublishReview()" ${v.busy?'disabled':''}>审核通过</button>${v.reviewStarted?'<button class="btn primary" type="button" onclick="saveAndReviewPublish()">保存并复检，返回清单</button>':`<button class="btn primary" type="button" onclick="checkPublishBatch()">检查本批 ${v.items.length} 件并上架</button>`}</div>
  </form>
 </div>`;
 const form=$('#publish-form');
 $('.publish-drawer').addEventListener('click',event=>{document.querySelectorAll('.publish-attribute-dropdown[open]').forEach(d=>{if(!d.contains(event.target))d.open=false})});
 form.addEventListener('click',event=>{if(!event.target.closest('#publish-category-panel')&&!event.target.closest('#publish-category-trigger'))closePublishCategoryPicker()});
 form.addEventListener('keydown',event=>{if(event.key==='Escape'){if(event.target.closest('#publish-category-panel')||event.target.closest('#publish-category-trigger')){closePublishCategoryPicker();event.stopPropagation();return}const d=event.target.closest('.publish-attribute-dropdown');if(d){d.open=false;d.querySelector('summary').focus();event.stopPropagation()}}});
 form.addEventListener('input',invalidatePublishCheck);
 form.addEventListener('change',invalidatePublishCheck);
 form.addEventListener('input',invalidatePublishMaterial);form.addEventListener('change',invalidatePublishMaterial);
 form.addEventListener('input',event=>{if(['publish-title','publish-description'].includes(event.target.id)){p.profile.title_input=$('#publish-title').value.trim();p.profile.description_input=$('#publish-description').value.trim();}});
 form.addEventListener('change',event=>{changePublishImageRole(event);if(event.target.matches('[data-pub-image-select]'))p.imageSelectionKeys=[...form.querySelectorAll('[data-pub-image-select]:checked')].map(input=>input.dataset.pubImageSelect);updatePublishImageControls()});
 form.addEventListener('input',updatePublishMissing);form.addEventListener('change',updatePublishMissing);form.addEventListener('change',updatePublishCertificates);updatePublishMissing();updatePublishCertificates();
 initPublishImageDnD();
}
function collectPublishForm(){
 const el=document.getElementById('publish-form');if(!el)return;
 const p=publishView.items[publishView.index],f={...p.profile,...Object.fromEntries(new FormData(el))};
 f.content_language='en';f.editor_language='en';f.title_input=el.querySelector('#publish-title').value.trim();f.description_input=el.querySelector('#publish-description').value.trim();
 f.title_en=f.title_input;f.description_en=f.description_input;
 p.title=f.title_input;f.description=p.source?.description||'';
 delete f.highlights;delete f.highlights_zh;delete f.highlights_en;
 delete f.search_keywords;delete f.search_keywords_en;delete f.search_keywords_zh;
 delete f.translated_source;
 for(const key of Object.keys(f))if(key.startsWith('attr-choice-'))delete f[key];
 f.sku_attributes={...f.sku_attributes};
 for(const sku of p.variants){
  const input=[...el.querySelectorAll('[data-pub-price]')].find(x=>x.dataset.pubPrice===sku.id);
  if(input)sku.price=input.value;
  const attrs=(f.sku_attributes[sku.id]||sku.attributes||[]).map(a=>({...a}));
  el.querySelectorAll('[data-pub-spec]').forEach(x=>{if(x.dataset.pubSpec===sku.id)attrs[+x.dataset.specIndex].value=x.value.trim()});
  f.sku_attributes[sku.id]=attrs;
 }
 f.brand_name=el.querySelector('[name=brand_id] option:checked')?.textContent||'';
 const visibleKeys=new Set(publishVisibleImages(p).map(im=>im.key)),roles=[...el.querySelectorAll('[data-pub-image-role]')].filter(x=>visibleKeys.has(x.dataset.pubImageRole));
 f.main_image_keys=roles.filter(x=>x.value==='main').map(x=>x.dataset.pubImageRole);
 f.description_image_keys=roles.filter(x=>x.value==='detail').map(x=>x.dataset.pubImageRole);
 p.imageSelectionKeys=[...el.querySelectorAll('[data-pub-image-select]:checked')].map(x=>x.dataset.pubImageSelect);
 f.seller_skus={...f.seller_skus};el.querySelectorAll('[data-pub-seller]').forEach(x=>f.seller_skus[x.dataset.pubSeller]=x.value.trim());

 if(f.size_table){
  f.size_table={...f.size_table,columns:[...f.size_table.columns],rows:f.size_table.rows.map(r=>[...r])};
  el.querySelectorAll('[data-size-column]').forEach(x=>f.size_table.columns[+x.dataset.sizeColumn]=x.value);
  el.querySelectorAll('[data-size-row]').forEach(x=>f.size_table.rows[+x.dataset.sizeRow][+x.dataset.sizeCell]=x.value);
 }
 f.brand_confirmed=!!el.querySelector('[name=brand_confirmed]')?.checked;
 f.inventory_verified={...f.inventory_verified};f.stocks={...f.stocks};el.querySelectorAll('[data-pub-stock]').forEach(x=>{if(String(f.stocks[x.dataset.pubStock]??'')!==x.value)f.inventory_verified[x.dataset.pubStock]=x.value;f.stocks[x.dataset.pubStock]=x.value});
 f.imageEdits={...p.imageEdits};
 if(publishView.meta?.category_id && publishView.meta.category_id===f.category_id){
 f.attributes={};el.querySelectorAll('[data-pub-attr]').forEach(x=>{f.attributes[x.dataset.pubAttr]=[...x.selectedOptions].filter(o=>o.value).map(o=>({id:o.value}))});
 el.querySelectorAll('[data-pub-custom]').forEach(x=>{if(x.value.trim())f.attributes[x.dataset.pubCustom]=[{name:x.value.trim()}]});
 f.certifications={};el.querySelectorAll('[data-pub-cert]').forEach(x=>{if(x.value)f.certifications[x.dataset.pubCert]=x.value});
 }
 p.profile=f;return p;
}
function publishFeedback(text){const el=document.getElementById('publish-feedback');if(el)el.textContent=text}
function initializePublishContent(p){
 const f=p.profile;
 f.title_input??=f.title_en||p.source?.title||p.title||'';
 f.description_input??=f.description_en||p.source?.description||'';
 f.content_language='en';f.editor_language='en';
}
function invalidatePublishCheck(){
 publishView.changed=true;
 publishView.batchCheck=null;
 const p=publishView.items[publishView.index];
 if(p.checkResult)p.checkResult={...p.checkResult,status:'stale'};
 if($('#publish-confirm'))publishFeedback('资料已修改，请重新检查后提交');
}
async function savePublishForm(quiet=false){
 try{
  const v=publishView;
  const p=collectPublishForm();
  if(!publishOpen(v))return false;
  const result=await shopPost('listing-profile',{market:p.market,id:p.id,profile:p.profile,edits:{revision:p.revision,title:p.title,price:p.price,exclusions:Object.entries(p.skuExclusions||{}).map(([key,hidden])=>({kind:'sku',key,hidden})),skus:p.variants.map(s=>({id:s.id,price:s.price})),images:publishImageChanges(p)}});
  p.revision=result.revision;
  if(!quiet)publishFeedback('标题、规格、售价与上架资料已保存到本机');
  return true;
 }catch(e){publishFeedback(e.message);return false}
}
function applyPublishPrice(){
 const value=$('#publish-price-all').value;
 if(!/^\d+(\.\d{1,2})?$/.test(value)||+value<=0)return publishFeedback('售价须大于零，最多两位小数');
 document.querySelectorAll('[data-pub-price]').forEach(x=>x.value=(+value).toFixed(2));
 collectPublishForm();invalidatePublishCheck();updatePublishMissing();
}

function applyPublishDefaults(form,m){
 const f={...form},old=f.package_prediction||{},values=m.suggested_package||{},applied={},packageCategory=m.package_category_id??m.category_id;
 f.category_id=m.category_id;f.category_path=m.category_path||'';f.category_shop_id=m.shop_id||'';
 if(m.match_status)f.category_source=m.match_status;
 for(const k of ['weight','length','width','height']){
  const automatic=old.values?.[k]!==undefined&&String(f[k])===String(old.values[k]);
  if(!f[k]||automatic){
   if(values[k]!==undefined){f[k]=values[k];applied[k]=values[k];}
   else if(automatic&&(old.category_id!==packageCategory||old.shop_id!==m.shop_id))delete f[k];
  }
 }
 f.package_prediction={category_id:packageCategory,shop_id:m.shop_id,values:applied};
 return f;
}
async function loadPublishMeta(rematch=false){
 const v=publishView;
 if(v.busy||!v.connection.selected_shop_id)return;
 const p=collectPublishForm(), selected=p.profile.category_id||'', previous=v.meta?.category_id||selected;
 const materialKey=publishMaterialKey(p);
 const select=$('#publish-form [name="category_id"]');
 v.busy=true;p.packageError='';
 $('.publish-package').outerHTML=publishPackageFields(p.profile,{...v.meta,package_status:'loading',package_message:''});
 select.disabled=true;
 $('#publish-category-status').textContent='正在识别竞品类目并自动获取仓库与包装资料…';
 try{
  const m=await shopPost('listing-metadata',{
   category_id:selected,shop_id:v.connection.selected_shop_id,
   item:{market:p.market,id:p.id},category_source:p.profile.category_source||'',rematch,profile:publishMaterialProfile(p)
  });
  if(!publishOpen(v))return;
  select.disabled=false;
  collectPublishForm();
  if(materialKey!==publishMaterialKey(p)){
   for(const aid of m.material_review?.attribute_ids||[])delete m.suggested_attributes[aid];
   m.material_review=null;
  }
  const previousProfile=JSON.stringify(p.profile);
  if(m.category_id!==previous){
   p.profile.attributes=validPublishAttributes(p.profile.attributes,m.attributes);
   p.profile.certifications={};
  }
  p.profile=applyPublishDefaults(p.profile,m);
  p.profile.attributes=p.profile.attributes||{};
  for(const [key,value] of Object.entries(m.suggested_attributes||{})){
   if(!p.profile.attributes[key]?.length)p.profile.attributes[key]=value;
  }
  if(m.warehouses.length===1)p.profile.warehouse_id=String(m.warehouses[0].id);
  v.meta=m;
  p.materialReview=m.material_review;p.materialReviewKey=publishMaterialKey(p);p.materialMessage='';
  if(v.reviewStarted&&JSON.stringify(p.profile)!==previousProfile)invalidatePublishCheck();
  const scroll=$('.publish-drawer').scrollTop;
  drawPublish();
  $('.publish-drawer').scrollTop=scroll;
 }catch(e){
  if(publishOpen(v)){
   select.value=previous;
   p.profile.category_id=previous;
   $('#publish-category-status').textContent=e.message;
   collectPublishForm();p.packageError='包装预测未完成：'+e.message;
   $('.publish-package').outerHTML=publishPackageFields(p.profile,v.meta);
  }
 }finally{
  select.disabled=false;
  v.busy=false;
  if(publishOpen(v))refreshPublishMaterial();
 }
}
async function savePublishTemplate(button){
 const v=publishView, name=$('#publish-template-name').value.trim();
 const status=$('#publish-template-status');
 if(!name){status.textContent='请输入模板名称';return}
 if(v.templates.some(t=>t.name===name)&&!confirm('覆盖已有模板「'+name+'」？'))return;
 const {profile}=collectPublishForm();
 const values=Object.fromEntries(publishCommonFields.map(k=>[k,profile[k]]));
 button.disabled=true;
 try{
  const result=await shopPost('listing-template',{name,values,shop_id:v.connection.selected_shop_id});
  v.templates=result.templates;
  if(!publishOpen(v))return;
  const select=$('#publish-template');
  select.innerHTML=publishTemplateOptions(v.templates);
  select.value=name;
  $('#publish-template-name').value='';
  $('#publish-template-status').textContent='已保存模板「'+name+'」';
 }catch(e){
  if(publishOpen(v))$('#publish-template-status').textContent=e.message;
 }finally{button.disabled=false}
}
function applyPublishTemplate(){
 const v=publishView, selected=$('#publish-template').value;
 const status=$('#publish-template-status');
 const template=v.templates.find(t=>t.name===selected);
 if(!template){status.textContent='请选择已保存的模板';return}
 const form=$('#publish-form');
 const warehouseFields=v.meta.mode==='global'?['warehouse_id','global_warehouse_id']:['warehouse_id'];
 if(warehouseFields.some(k=>!(k==='warehouse_id'?v.meta.warehouses:v.meta.global_warehouses).some(w=>String(w.id)===template.values[k]))){
  status.textContent='模板中的仓库当前不可用，请重新选择仓库后更新模板';
  return;
 }
 for(const k of publishCommonFields)form.elements[k].value=template.values[k]||'';
 collectPublishForm();
 invalidatePublishCheck();
 status.textContent='已套用「'+selected+'」。请核对这件商品的实际包装规格。';
}
function applyPublishStock(){const value=document.getElementById('publish-stock-all').value;if(!/^\d+$/.test(value)||+value<0||+value>99999)return publishFeedback('库存须为 0～99999 的整数；0 表示售罄');document.querySelectorAll('[data-pub-stock]').forEach(x=>x.value=value);collectPublishForm();invalidatePublishCheck();updatePublishMissing()}

async function movePublish(delta){
 const v=publishView;
 if(v.busy)return;
 v.busy=true;
 try{if(!await savePublishForm(true)||!publishOpen(v))return}
 finally{v.busy=false}
 v.index+=delta;
 v.meta=null;
 drawPublish();
 if(v.connection.selected_shop_id)await loadPublishMeta();
}
function showPublishJobs(){return go('publishing')}

function publishUploadControl(target,label){
 if(target==='main')return `<div class="publish-image-upload-panel"><label class="publish-upload"><span class="publish-upload-icon" aria-hidden="true">＋</span><span class="publish-upload-copy"><strong>${esc(label)}</strong><small>可一次选择多张 JPG / PNG · 单张最大 8 MB</small></span><span class="publish-upload-action">选择图片</span><input type="file" multiple aria-label="${esc(label)}" accept="image/jpeg,image/png" data-publish-upload="${esc(target)}" onchange="uploadPublishImage(this)"></label></div>`;
 return `<label class="publish-upload"><span class="publish-upload-icon" aria-hidden="true">＋</span><span class="publish-upload-copy"><strong>${esc(label)}</strong><small>选择本地图片</small></span><span class="publish-upload-action">选择图片</span><input type="file" aria-label="${esc(label)}" accept="image/jpeg,image/png" data-publish-upload="${esc(target)}" onchange="uploadPublishImage(this)"></label>`;
}
function validPublishAttributes(values,rules){
 const result={};
 for(const rule of rules||[]){
  if(rule.type!=='PRODUCT_PROPERTY')continue;
  const valid=(values?.[rule.id]||[]).filter(v=>(rule.values||[]).some(x=>String(x.id)===String(v.id))||(rule.is_customizable&&v.name&&!v.id));
  if(valid.length&&(rule.is_multiple_selection||valid.length===1))result[rule.id]=valid;
 }
 return result;
}
async function uploadPublishImage(input){
 const v=publishView,files=[...input.files],target=input.dataset.publishUpload;
 if(!files.length||v.busy)return;
 const p=collectPublishForm();
 v.busy=true;
 try{
  const group='商品图';
  for(let index=0;index<files.length;index++){
   const file=files[index];
   if(!['image/jpeg','image/png'].includes(file.type)||file.size>8*1024*1024)throw Error('请选择最大 8 MB 的 JPG 或 PNG 图片');
   const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(Error('读取失败'));r.readAsDataURL(file)});
   const im=await new Promise((resolve,reject)=>{const x=new Image();x.onload=()=>resolve(x);x.onerror=()=>reject(Error('图片无法解码'));x.src=data});
   if(target==='size_chart'&&(Math.min(im.naturalWidth,im.naturalHeight)<1024||Math.max(im.naturalWidth,im.naturalHeight)>4000))throw Error('尺码表宽高须在 1024～4000 像素之间');
   publishFeedback(files.length>1?`正在上传第 ${index+1} / ${files.length} 张图片…`:'正在上传图片…');
   await storePublishImage(v,p,target,data,false,group);
  }
 }catch(e){if(publishOpen(v))publishFeedback(e.message)}finally{v.busy=false;input.value=''}
}
async function storePublishImage(v,p,target,data,generated=false,groupName='商品图'){
  const url='/api/listing-editor?market='+encodeURIComponent(p.market)+'&id='+encodeURIComponent(p.id);
  const before=await api(url);
  const current=before.images.filter(im=>!im.hidden),images=current.map((im,index)=>({key:im.key,hidden:false,position:index}));
  images.push({add:true,data,group_name:target==='main'?groupName:'本地上传',position:current.length});
  const response=await fetch('/api/listing-editor',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({market:p.market,id:p.id,revision:before.revision,title:before.title,price:before.price,images})});
  const result=await response.json();if(!response.ok)throw Error(result.error||'上传失败');
  const after=await api(url),old=new Set(before.images.map(i=>i.key));
  const added=after.images.find(i=>!old.has(i.key));if(!added)throw Error('图片已保存，请重新打开表单选择');
  p.images=after.images.filter(i=>!i.hidden);p.revision=after.revision;p.imageEdits||={};invalidatePublishCheck();
  p.profile.main_image_keys=Array.isArray(p.profile.main_image_keys)?p.profile.main_image_keys:[];
  if(target==='size_chart'){p.profile.size_chart_template_id='';p.profile.size_chart=added.key;p.profile.size_table_rendered=generated?JSON.parse(JSON.stringify({table:p.profile.size_table,title:p.profile.size_table_title,image_key:added.key})):null;}
  else if(target.startsWith('cert:'))p.profile.certifications={...p.profile.certifications,[target.slice(5)]:added.key};
  else if(!p.profile.main_image_keys.includes(added.key)&&p.profile.main_image_keys.length<9)p.profile.main_image_keys.push(added.key);
  else if(target==='main'){
   p.profile.description_image_keys||=[];
   if(p.profile.description_image_keys.length<30)p.profile.description_image_keys.push(added.key);
  }
  await shopPost('listing-profile',{market:p.market,id:p.id,profile:p.profile});
  if(publishOpen(v)){drawPublish();publishFeedback(target==='size_chart'?'尺码表已更换并保存，仅用于当前商品':'图片已上传并保存'+(target==='main'?(p.profile.description_image_keys||[]).includes(added.key)?'，已自动加入商品详情':p.profile.main_image_keys.includes(added.key)?'，已设为商品主图':'，主图和详情图片已满，新增图片暂不用于上架':''))}
}

function publishPropertyFields(meta,form){
 const rules=meta.attributes.filter(a=>a.type==='PRODUCT_PROPERTY');
 const required=publishAttributeRequired;
 const render=items=>items.map(a=>publishAttrInput(a,form.attributes?.[a.id])).join('');
 const mandatory=rules.filter(required),optional=rules.filter(a=>!required(a)),filled=optional.filter(a=>form.attributes?.[a.id]?.length).length;
 return `${mandatory.length?`<div class="publish-grid">${render(mandatory)}</div>`:'<p class="publish-muted">当前类目暂无必填商品属性。</p>'}${optional.length?`<details class="publish-optional"><summary><span>其他商品属性（选填）</span><small>${optional.length} 项${filled?' · 已带入 '+filled+' 项':''}</small></summary><p class="publish-muted">可留空；已带入的属性会随商品提交。显示“未选择，不提交”表示不会提交该属性。</p><div class="publish-grid">${render(optional)}</div></details>`:''}`;
}
function certificateRequired(rule,attributes){
 return rule.is_required||rule.is_requried||(rule.required_regions||[]).includes('US')||(rule.requirement_conditions||[]).some(c=>(c.region||'US')==='US'&&c.condition_type==='VALUE_ID_MATCH'&&(attributes?.[c.attribute_id]||[]).some(v=>String(v.id)===String(c.attribute_value_id)));
}
function publishCertifications(meta,form){
 return (meta.rules.product_certifications||[]).map(r=>`<details class="publish-certificate" data-cert-section="${esc(r.id)}"><summary>资质图片：${esc(r.name)}<span data-cert-required></span></summary><label>选择资质图片<select class="search" data-pub-cert="${esc(r.id)}">${publishImageOptions(publishView.items[publishView.index],form.certifications?.[r.id])}</select></label>${publishUploadControl('cert:'+r.id,'上传资质图片')}</details>`).join('');
}
function updatePublishCertificates(){
 const form=$('#publish-form');if(!form||!publishView.meta)return;
 const attrs={};form.querySelectorAll('[data-pub-attr]').forEach(x=>attrs[x.dataset.pubAttr]=[...x.selectedOptions].filter(o=>o.value).map(o=>({id:o.value})));
 for(const rule of publishView.meta.rules.product_certifications||[]){
  const section=[...form.querySelectorAll('[data-cert-section]')].find(x=>x.dataset.certSection===String(rule.id));if(!section)continue;
  const needed=certificateRequired(rule,attrs),selected=section.querySelector('select').value;
  section.hidden=!needed&&!selected;
  section.querySelector('[data-cert-required]').textContent=needed?' *':'（已选，可清除）';
  if(needed&&!selected)section.open=true;
 }
}

function updatePublishMissing(){
 const form=$('#publish-form');if(!form)return;
 const p=publishView.items[publishView.index],meta=publishView.meta;
 const value=name=>form.querySelector('[name="'+name+'"]')?.value.trim();
 const missing=[],targets=new Set();
 const add=(label,target)=>{missing.push(label);if(target)targets.add(target);};
 if(!$('#publish-title').value.trim())add('标题','publish-title');
 if(!$('#publish-description')?.value.trim())add('商品详情','publish-description');
 for(const [id,label] of [['publish-title','标题'],['publish-description','商品详情']]){
  if([...($('#'+id)?.value||'')].some(c=>/\p{Letter}/u.test(c)&&! /\p{Script=Latin}/u.test(c)))add(label+'含非英文文字，请修改为英文',id);
 }
 if(!value('brand_id')||!form.querySelector('[name=brand_confirmed]')?.checked)add('核实品牌归属','publish-title');
 const categoryId=value('category_id'),tree=publishCategoryTree(),category=categoryId?tree.nodes.get(String(categoryId)):null;
 if(!categoryId)add(meta?.match_status==='permission_required'?'店铺类目权限':'商品类目','publish-logistics');
 else if(meta&&!category)add('商品类目无法确认，请重新读取类目','publish-logistics');
 else if(meta&&!category.is_leaf)add('商品类目需选择末级类目','publish-logistics');
 else if(meta&&!(category.permission_statuses||[]).includes('AVAILABLE'))add('商品类目暂无店铺销售权限','publish-logistics');
 if(!value('warehouse_id')||(meta?.mode==='global'&&!value('global_warehouse_id')))add('发货仓库','publish-logistics');
 if(['weight','length','width','height'].some(n=>!(+value(n)>0)))add('包装重量与尺寸','publish-logistics');
 if(p.packageError)add('包装资料读取失败，请重新识别','publish-logistics');
 const emptyStock=[...form.querySelectorAll('[data-pub-stock]')].filter(x=>!(p.profile.excluded_sku_ids||[]).includes(x.dataset.pubStock||x.dataset.pubPrice)&&!(+x.value>0)).length;
 if(emptyStock)add(emptyStock+' 个规格的真实库存','publish-variants');
 const emptyPrice=[...form.querySelectorAll('[data-pub-price]')].filter(x=>!(p.profile.excluded_sku_ids||[]).includes(x.dataset.pubStock||x.dataset.pubPrice)&&!(+x.value>0)).length;
 if(emptyPrice)add(emptyPrice+' 个规格的售价','publish-variants');
 const localSkuInputs=[...form.querySelectorAll('[data-pub-seller]')],localSkus=localSkuInputs.map(x=>x.value.trim()),skuSet=new Set();let duplicateLocalSku=false;
 if(localSkuInputs.some(x=>!x.value.trim()))add('本地 SKU 不能为空','publish-variants');
 if(localSkuInputs.some(x=>x.value.trim()&&(/\s/.test(x.value)||x.value.length<6||x.value.length>50)))add('本地 SKU 格式不符合平台限制','publish-variants');
 localSkus.forEach(s=>{if(s&&skuSet.has(s))duplicateLocalSku=true;if(s)skuSet.add(s)});
 if(duplicateLocalSku)add('本批存在重复的本地 SKU','publish-variants');
 const incompleteSku=p.variants.some(s=>(form.elements.sku_attributes?.[s.id]||p.profile.sku_attributes?.[s.id]||s.attributes||[]).some(a=>!String(a.name||'').trim()||!String(a.value||'').trim()));
 if(incompleteSku)add('规格名称或规格值','publish-variants');
 const skuCombinations=new Set(),duplicateSku=p.variants.some(s=>{const attrs=form.elements.sku_attributes?.[s.id]||p.profile.sku_attributes?.[s.id]||s.attributes||[];const key=attrs.map(a=>String(a.name||'').trim()+'='+String(a.value||'').trim()).sort().join('|');if(skuCombinations.has(key))return true;skuCombinations.add(key);return false});
 if(duplicateSku)add('重复的规格组合','publish-variants');
 const mainImageSelected=[...form.querySelectorAll('[data-pub-image-role]')].some(x=>x.value==='main');
 if(!mainImageSelected)add('商品主图','publish-photos');
 if(publishRuleRequired(meta?.rules.size_chart)&&!value('size_chart')&&!value('size_chart_template_id'))add('尺码表','publish-company-size');
 if(value('size_chart')&&p.profile.size_table_rendered&&JSON.stringify(p.profile.size_table_rendered.table)!==JSON.stringify(p.profile.size_table))add('尺码表数据已修改，需重新生成图片','publish-company-size');
 for(const rule of meta?.attributes||[]){
  if(rule.type!=='PRODUCT_PROPERTY'||!publishAttributeRequired(rule))continue;
  const input=[...form.querySelectorAll('[data-pub-attr]')].find(x=>x.dataset.pubAttr===String(rule.id));
  const custom=[...form.querySelectorAll('[data-pub-custom]')].find(x=>x.dataset.pubCustom===String(rule.id));
  if(!input?.value&&!custom?.value.trim())add({'101395':'加州 Prop 65 生殖毒性化学物质声明','101400':'加州 Prop 65 致癌物质声明'}[String(rule.id)]||rule.name,'publish-compliance');
 }
 const attrs={};form.querySelectorAll('[data-pub-attr]').forEach(x=>attrs[x.dataset.pubAttr]=[...x.selectedOptions].filter(o=>o.value).map(o=>({id:o.value})));
 for(const rule of meta?.rules.product_certifications||[]){
  const input=[...form.querySelectorAll('[data-pub-cert]')].find(x=>x.dataset.pubCert===String(rule.id));
  if(certificateRequired(rule,attrs)&&!input?.value)add('资质：'+rule.name,'publish-compliance');
 }
 if(p.content_review?.requires_review)add('标题或描述含疑似品牌或权利用语','publish-title');
 const usedImages=new Set([...form.querySelectorAll('[data-pub-image-role]')].filter(input=>input.value!=='none').map(input=>input.dataset.pubImageRole));
 if(!value('size_chart_template_id'))usedImages.add(value('size_chart'));
 form.querySelectorAll('[data-pub-cert]').forEach(input=>usedImages.add(input.value));
 if(publishVisibleImages(p).some(im=>usedImages.has(im.key)&&(im.text_matches||[]).length))add('用于上架的图片含疑似竞品店铺/品牌','publish-photos');
 const pending=!meta,labels={'publish-title':'标题与品牌','publish-logistics':'类目与包装','publish-variants':'售价与库存','publish-photos':'商品图片','publish-compliance':'属性与合规','publish-company-size':'尺码表'};
 $('#publish-missing-panel').classList.toggle('has-missing',missing.length>0);
 $('#publish-missing-heading').textContent=missing.length?'待补充 '+new Set(missing).size+' 项必填资料':pending?'待读取类目要求':'基础必填资料已齐';
 $('#publish-missing').textContent=missing.length?[...new Set(missing)].join('、')+(pending?'。连接店铺并读取类目后，还会检查属性、尺码表和资质。':''):pending?'连接店铺并读取类目后，继续检查属性、尺码表和资质。':'选填内容可按需展开。提交前仍会检查图片和平台规则。';
 $('#publish-missing-links').innerHTML=[...targets].map(id=>`<button type="button" class="publish-text-button" onclick="jumpPublishSection('${id}')">补充${labels[id]}</button>`).join('');
}
function rememberPublishCoverage(element){
 if(publishView&&element.isConnected)publishView.items[publishView.index].coverageOpen=element.open;
}
function publishCoverage(p,m){
 const source=p.source||{};
 return `<details class="publish-coverage" ${p.coverageOpen?'open':''} ontoggle="rememberPublishCoverage(this)"><summary>查看资料来源与已采集信息</summary><div class="publish-coverage-grid"><div><b>已采集，可直接带入</b><p>${p.images.length} 张图片 · ${p.variants.length} 个 SKU · ${source.description_blocks||0} 段描述 · ${(source.specifications||[]).length} 项属性</p><p>标题、规格与售价已带入；沿用竞品分类，包装重量与尺寸由 TikTok 自动预测。</p></div><div><b>需要你自己的商品资料</b><p>实际库存、品牌归属、合规声明及所需资质。</p><p>在竞品库统一上传尺码表后，会自动带入此处。</p></div></div><details><summary>查看已采集的属性</summary><dl>${(source.specifications||[]).map(a=>`<dt>${esc(a.name)}</dt><dd>${esc(a.value)}</dd>`).join('')||'<p>尚未采集到属性，请补充商品详情资料。</p>'}</dl></details></details>`;
}
function publishSizeTable(p){
 const f=p.profile,t=f.size_table;
 return `<section class="publish-size-builder"><h3>从已采集图片识别尺码表</h3><p>选择包含尺码表的图片，识别后核对数字和单位，可直接生成清晰的上传图。</p><div class="publish-template-row"><select class="search" name="size_source_image" aria-label="尺码表来源图片">${publishImageOptions(p,f.size_source_image)}</select><button class="btn" type="button" onclick="extractPublishSize()">识别尺码表</button></div>${t?`<label>尺码表说明<input class="search" name="size_table_title" value="${esc(f.size_table_title||t.title||'Size chart')}" maxlength="150"></label><div class="publish-size-scroll"><table><thead><tr>${t.columns.map((c,i)=>`<th><input aria-label="尺码表列 ${i+1}" data-size-column="${i}" value="${esc(c)}" maxlength="100"></th>`).join('')}</tr></thead><tbody>${t.rows.map((r,i)=>`<tr>${r.map((c,j)=>`<td><input aria-label="尺码表第 ${i+1} 行第 ${j+1} 列" data-size-row="${i}" data-size-cell="${j}" value="${esc(c)}" maxlength="100"></td>`).join('')}</tr>`).join('')}</tbody></table></div><button class="btn" type="button" onclick="generatePublishSize()">生成尺码表并用于上架</button>`:''}<div id="publish-size-status" role="status"></div></section>`;
}
async function extractPublishSize(){
 const v=publishView;if(v.busy)return;const p=collectPublishForm();
 if(!p.profile.size_source_image)return publishFeedback('请先选择包含尺码表的图片');
 v.busy=true;$('#publish-size-status').textContent='正在识别尺码表，请稍候…';
 try{
  const result=await shopPost('listing-size-extract',{market:p.market,id:p.id,image_key:p.profile.size_source_image});
  if(!publishOpen(v))return;
  collectPublishForm();p.profile.size_table=result.table;p.profile.size_table_title=result.table.title;
  const scroll=$('.publish-drawer').scrollTop;drawPublish();$('.publish-drawer').scrollTop=scroll;
  invalidatePublishCheck();$('#publish-size-status').textContent='识别完成，请核对数字和单位，再生成上传图。';
 }catch(e){if(publishOpen(v))$('#publish-size-status').textContent=e.message}finally{v.busy=false}
}
async function generatePublishSize(){
 const v=publishView;if(v.busy)return;
 v.busy=true;
 try{
  if(!await savePublishForm(true))return;
  const p=collectPublishForm(),t=p.profile.size_table;
  if(!t||!t.rows.length||t.rows.some(r=>r.some(x=>!x.trim())))throw Error('请补齐尺码表中的空白数字或文字');
  const canvas=document.createElement('canvas');canvas.width=1800;canvas.height=Math.max(1100,300+t.rows.length*90);
  const ctx=canvas.getContext('2d');ctx.fillStyle='#ffffff';ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle='#242434';ctx.font='bold 42px Arial';ctx.textAlign='center';ctx.fillText(p.profile.size_table_title||t.title,900,95,1660);
  const cell=1680/t.columns.length,start=200;
  [t.columns,...t.rows].forEach((row,i)=>{
   ctx.fillStyle=i===0?'#292735':i%2?'#f4f3f7':'#ffffff';ctx.fillRect(60,start+i*90,1680,90);
   row.forEach((value,j)=>{ctx.strokeStyle='#ddddE4';ctx.strokeRect(60+j*cell,start+i*90,cell,90);ctx.fillStyle=i===0?'#ffffff':'#252536';ctx.font=(i===0?'bold ':'')+'28px Arial';ctx.fillText(value,60+(j+.5)*cell,start+i*90+54,cell-18)});
  });
  await storePublishImage(v,p,'size_chart',canvas.toDataURL('image/png'),true);
  if(publishOpen(v))publishFeedback('尺码表已生成并保存，将随商品提交；请确保与实际销售商品一致。');
 }catch(e){publishFeedback(e.message)}finally{v.busy=false}
}

async function changePublishShop(id){
 const v=publishView;if(!id||v.busy)return;
 if(!await savePublishForm(true))return;
 v.busy=true;
 try{v.connection=await shopPost('select',{shop_id:id});v.meta=null;v.categoryPicker=null;v.items.forEach(p=>{if(p.checkResult)p.checkResult={...p.checkResult,status:'stale'}});invalidatePublishCheck();v.busy=false;drawPublish();await loadPublishMeta()}
 catch(e){publishFeedback(e.message)}finally{v.busy=false}
}
async function openShopFromPublish(){if(!await savePublishForm(true))return;closeModal();go('shop-management')}
