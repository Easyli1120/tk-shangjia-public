// Product selection, creative briefs, and the independent AI result drafts page.
const creative = {query:'', top:false, page:1, items:[], total:0, selected:null,
  details:null, status:null, forms:new Map(), listVersion:0, detailVersion:0, renderVersion:0,
  draftsPage:1, jobs:[], resultPage:1, timer:null, searchTimer:null, submitting:false, pauseBusy:false, mockupSelection:new Set(), savingSelection:false, token:null};
const creativeKey = p => p.market + ':' + p.id;
const creativeDefault = () => ({direction:'图案与主题延展',count:1,retain:'',changes:'',audience:'',extra:'',references:[],model:'sunburst',mode:'both',garment:'both',style:'white',color:'白色',parent_image_id:null});
const creativeDirections = ['图案与主题延展','配色与风格创新','版型与细节优化','场景与系列拓展','自定义方向'];
function stopInnovationPolling(){clearTimeout(creative.timer);clearTimeout(creative.searchTimer);creative.renderVersion++;creative.listVersion++;creative.detailVersion++}
async function creativePost(path, body){let r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d}
function creativeImage(path){return typeof path==='string'&&/^\/(images|innovation-images)\/[A-Za-z0-9_./-]+$/.test(path)?path:''}
function creativeThumb(path, label='参考商品'){let src=creativeImage(path);return src?`<img src="${esc(src)}" alt="${esc(label)}" loading="lazy" draggable="false">`:`<span class="creative-placeholder">${uiIcon('assets')}</span>`}
function creativeForm(){return creative.selected?creative.forms.get(creativeKey(creative.selected)):null}
function rememberCreative(){let form=creativeForm(),el=$('#creative-form');if(!form||!el)return;for(let key of ['direction','count','retain','changes','audience','extra','model','mode','garment','style','color','parent_image_id']){let input=el.elements.namedItem(key);if(input)form[key]=key==='count'?Number(input.value):key==='parent_image_id'?(Number(input.value)||null):input.value}form.dirty=true;creative.token=null;let label=$('#creative-saved');if(label)label.textContent='有未保存的创作要求'}
function openInnovationVisual(){
  if(creative.selected){
    if(vision.product&&creativeKey(vision.product)===creativeKey(creative.selected))return go('visual');
    return openVisual(creative.selected.market,creative.selected.id);
  }
  return go('visual');
}
async function innovationPage(){
  let version=++creative.renderVersion;
  $('#app').innerHTML=heading('HOT PRODUCT INNOVATION','热款创新','从选品池挑选参考款，让灵感变成新款。',`<div class="creative-page-actions"><button class="btn primary" onclick="openInnovationVisual()">${uiIcon('review')} 视觉逆向分析</button><button class="btn" onclick="go('visual-records')">视觉分析记录</button><button class="btn" onclick="go('innovation-drafts')">${uiIcon('inbox')} 查看成品库</button></div>`)+`
    <div class="creative-workspace">
      <section class="panel creative-picker"><div class="creative-section-title"><span>01</span><div><h2>选择参考商品</h2></div></div>
        <label class="creative-search">${uiIcon('review')}<input id="creative-search" placeholder="搜索商品、店铺或 ID" aria-label="搜索参考商品" value="${esc(creative.query)}"></label>
        <div class="creative-picker-meta"><div id="creative-source-count" class="creative-help" aria-live="polite">正在加载…</div><label class="creative-top-filter"><input id="creative-top" type="checkbox" ${creative.top?'checked':''}> 前 20 名</label></div>
        <div id="creative-products" class="creative-products"></div><div id="creative-pagination" class="creative-pagination"></div>
      </section>
      <section class="panel creative-editor"><div class="creative-section-title"><span>02</span><div><h2>定义新款方向</h2></div></div><div id="creative-right"><div class="empty">正在加载参考商品…</div></div></section>
    </div><div class="creative-download-bar"><span>生成素材 <small>包含当前商品的印花、Mockup 和历史版本</small></span><a id="creative-download-all" class="btn" hidden>下载全部素材</a></div><section id="creative-design-results" class="panel creative-history"></section><section id="creative-mockup-results" class="panel creative-history"></section><details class="panel creative-history creative-task-disclosure"><summary><span><strong>生成任务状态</strong><small id="creative-task-summary" aria-live="polite">正在读取任务…</small></span><span class="creative-task-toggle" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 9l7 7 7-7z"/></svg></span></summary><div id="creative-task-status" aria-live="polite"></div></details>`;
  $('#app > .pagehead').classList.add('creative-pagehead');
  $('#creative-search').oninput=e=>{creative.query=e.target.value;creative.page=1;clearTimeout(creative.searchTimer);creative.searchTimer=setTimeout(()=>loadCreativeSources().catch(e=>toast(e.message)),250)};
  $('#creative-top').onchange=e=>{creative.top=e.target.checked;creative.page=1;loadCreativeSources().catch(e=>toast(e.message))};
  creative.status=await api('/api/innovation/status');
  if(state.page!=='innovation'||version!==creative.renderVersion)return;
  await loadCreativeSources();
}
async function loadCreativeSources(){
  let version=++creative.listVersion;
  let data=await api('/api/innovation/products?'+new URLSearchParams({q:creative.query,top:creative.top?'1':'0',page:creative.page}));
  if(state.page!=='innovation'||version!==creative.listVersion)return;
  creative.items=data.items;creative.total=data.total;
  $('#creative-source-count').textContent=`${num(data.total)} 件 · 综合排名`;
  drawCreativeSources();
  if(!creative.selected&&data.items.length)await chooseCreative(0);
  else if(creative.selected&&!$('#creative-form'))await loadCreativeDetail(creative.selected);
  else if(!creative.selected)$('#creative-right').innerHTML='<div class="empty"><strong>选品池暂无商品</strong>先将商品加入选品池，再开始设计。<p><button class="btn" onclick="go(\'selected\')">前往选品池</button></p></div>';
}
function drawCreativeSources(){
  $('#creative-products').innerHTML=creative.items.length?creative.items.map((p,i)=>`<button type="button" class="creative-product ${creative.selected&&creativeKey(p)===creativeKey(creative.selected)?'chosen':''}" onclick="chooseCreative(${i})" aria-pressed="${!!creative.selected&&creativeKey(p)===creativeKey(creative.selected)}">
    ${creativeThumb(p.thumbnail)}<span><strong>${esc(p.name||p.id)}</strong><small>${esc(p.shop_name||'未知店铺')} · ${esc(p.price||'暂无价格')}</small><em>${p.rank?'综合排名 #'+num(p.rank):'暂无排名'}</em></span></button>`).join(''):'<div class="empty">没有找到匹配的商品</div>';
  let pages=Math.max(1,Math.ceil(creative.total/30));
  $('#creative-pagination').innerHTML=`<button class="btn small" onclick="creativePage(-1)" ${creative.page<=1?'disabled':''}>上一页</button><span>${creative.page} / ${pages}</span><button class="btn small" onclick="creativePage(1)" ${creative.page>=pages?'disabled':''}>下一页</button>`;
}
async function creativePage(delta){creative.page+=delta;try{await loadCreativeSources();$('#creative-products').scrollTop=0}catch(e){toast(e.message)}}
async function chooseCreative(index){let product=creative.items[index];if(!product)return;if(!creative.selected||creativeKey(creative.selected)!==creativeKey(product))creative.mockupSelection.clear();creative.selected=product;creative.resultPage=1;creative.token=null;drawCreativeSources();await loadCreativeDetail(product)}
async function loadCreativeDetail(product){
  let version=++creative.detailVersion;$('#creative-right').innerHTML='<div class="empty">正在读取图组…</div>';
  try{
    let d=await api('/api/innovation/product?'+new URLSearchParams({market:product.market,id:product.id}));
    if(state.page!=='innovation'||version!==creative.detailVersion)return;
    creative.details=d;creative.selected=d.product;
    let key=creativeKey(d.product),form=creative.forms.get(key);
    if(!form){form={...creativeDefault(),...(d.brief||{}),dirty:false,saved_at:d.saved_at};if(!d.brief){let first=d.gallery.images.find(x=>creativeImage(x.local_path));if(first)form.references=[first.id]}creative.forms.set(key,form)}
    drawCreativeEditor();await loadCreativeResults();
  }catch(e){if(state.page==='innovation'&&version===creative.detailVersion)$('#creative-right').innerHTML=`<div class="empty"><strong>暂时无法读取商品</strong>${esc(e.message)}<p><button class="btn" onclick="retryCreativeDetail()">重试</button></p></div>`}
}
function retryCreativeDetail(){if(creative.selected)loadCreativeDetail(creative.selected)}
function drawCreativeEditor(){
  let d=creative.details,f=creativeForm(),ready=creative.status?.ready;
  $('#creative-right').innerHTML=`
    <div class="creative-gallery-heading"><b>商品图组 <span>${d.gallery.images.length} 张</span></b><div><button class="creative-link" type="button" onclick="selectCreativeImages(true)">全选</button><button class="creative-link" type="button" onclick="selectCreativeImages(false)">清空</button></div></div>
    <div id="creative-gallery" class="creative-gallery" tabindex="0" aria-label="商品图组，可左右滚动或拖动"></div>
    <p class="creative-help" id="creative-reference-count"></p>
    <form id="creative-form" class="creative-form">
      <div class="creative-form-row"><label>二创方向<select name="direction">${creativeDirections.map(x=>`<option ${x===f.direction?'selected':''}>${x}</option>`).join('')}</select></label>
      <label>独立印花数量<select name="count">${[1,2,4].map(n=>`<option value="${n}" ${n===f.count?'selected':''}>${n} 款</option>`).join('')}</select></label></div>
      <div class="creative-form-row">${creativeSelect('model','AI 模型',f.model,[['sunburst','Sunburst · 精细创作'],['flare','Flare · 快速迭代']])}${creativeSelect('mode','生图模式',f.mode,[['both','两者都要（默认）'],['design','仅印花'],['mockup','仅 Mockup']])}</div>
      <div class="creative-form-row">${creativeSelect('garment','Mockup 商品',f.garment,[['both','T 恤 + 卫衣'],['tshirt','T 恤'],['hoodie','卫衣']])}${creativeSelect('style','Mockup 风格',f.style,creativeStyles)}</div>
      <div class="creative-form-row"><label>服装颜色<input name="color" maxlength="80" value="${esc(f.color)}" placeholder="输入服装颜色，例如黑色" autocomplete="off"></label><label>已有印花（修改 / 仅 Mockup）<select name="parent_image_id" id="creative-parent"><option value="">新设计</option></select></label></div>
      <p class="creative-help">默认高质量 · 1024 × 1024 PNG · 印花透明背景。清空参考图可进行原创文生图。多图去重后优先选取较大文件，最多 8 张。</p>
      <label>保留哪些特征<input name="retain" maxlength="2000" placeholder="例如：宽松版型、刺绣工艺、复古运动风" value="${esc(f.retain)}"></label>
      <label class="creative-changes">希望做哪些变化 <span class="creative-required">*</span><textarea name="changes" rows="1" maxlength="4000" placeholder="例如：延展猫咪主题，使用不同配色与原创图案，生成同系列的新款">${esc(f.changes)}</textarea></label>
      <label>目标人群与使用场景<input name="audience" maxlength="2000" placeholder="例如：美国年轻女性、节日礼物、户外露营" value="${esc(f.audience)}"></label>
      <label>补充提示词<textarea name="extra" rows="1" maxlength="4000" placeholder="例如：居中构图、清晰线条、透明背景、减少装饰">${esc(f.extra)}</textarea></label>
      <details class="creative-prompt"><summary>查看完整提示词</summary><pre id="creative-prompt-preview"></pre></details>
      <div class="creative-service ${ready?'ready':''}"><span class="creative-service-dot"></span><div><strong>${ready?esc(creative.status.provider):'AI 生图待配置密钥'}</strong><p>${esc(creative.status?.message||'正在检查后端配置')}</p></div></div>
      <div class="creative-actions"><button id="creative-generate" class="btn primary" type="submit" ${!ready||creative.submitting?'disabled':''}>${uiIcon('ai')} ${ready?'AI 生图':'AI 生图 · 待配置'}</button><button id="creative-pause" class="btn" type="button" onclick="toggleCreativePause()" disabled>暂停生图</button><button id="creative-save" class="btn" type="button" onclick="saveCreativeBrief()">${uiIcon('selected')} 保存创作要求</button></div>
      <p id="creative-saved" class="creative-help" role="status">${f.dirty?'有未保存的创作要求':f.saved_at?'已保存于 '+esc(f.saved_at.replace('T',' ').slice(0,19)):'创作要求按商品单独保存'}</p>
    </form>`;
  drawCreativeGallery();updateCreativePrompt();
  $('#creative-form').oninput=()=>{rememberCreative();updateCreativePrompt()};
  $('#creative-form').onsubmit=e=>{e.preventDefault();generateCreative()};
}
function drawCreativeGallery(){
  let f=creativeForm(),images=creative.details.gallery.images,strip=$('#creative-gallery');
  strip.innerHTML=images.length?images.map((im,i)=>{let src=creativeImage(im.local_path),selected=f.references.includes(im.id);return `<div class="creative-reference ${selected?'chosen':''}"><button type="button" class="creative-preview" onclick="previewCreative(${i})" ${!src?'disabled':''} aria-label="放大第 ${i+1} 张图片">${creativeThumb(src,'第 '+(i+1)+' 张参考图')}</button><label class="creative-reference-select"><span class="creative-reference-index" aria-hidden="true">${String(i+1).padStart(2,'0')}</span><input type="checkbox" aria-label="选择第 ${i+1} 张参考图${src?'':'（待缓存）'}" data-reference="${im.id}" onchange="toggleCreativeImage(${im.id},this.checked)" ${selected?'checked':''} ${!src?'disabled':''}></label></div>`}).join(''):'<div class="creative-no-gallery">图组尚未缓存，可以先填写并保存创作要求。<button class="btn small" onclick="retryCreativeDetail()">刷新图组</button></div>';
  $('#creative-reference-count').textContent=`已选 ${f.references.length} 张参考图 · 左右拖动或使用下方滚动条查看全部图片，点击图片可放大`;
  let start=null,moved=false;
  strip.onpointerdown=e=>{if(e.pointerType!=='mouse'||e.button!==0||e.target.closest('label'))return;start={x:e.clientX,scroll:strip.scrollLeft,id:e.pointerId};moved=false};
  strip.onpointermove=e=>{if(!start)return;if(Math.abs(e.clientX-start.x)>5){moved=true;strip.setPointerCapture(e.pointerId);strip.classList.add('dragging');strip.scrollLeft=start.scroll-(e.clientX-start.x);e.preventDefault()}};
  const finish=()=>{if(start&&strip.hasPointerCapture(start.id))strip.releasePointerCapture(start.id);start=null;strip.classList.remove('dragging')};
  strip.onpointerup=finish;strip.onpointercancel=finish;strip.onpointerleave=()=>{if(!moved)finish()};strip.onclick=e=>{if(moved){e.preventDefault();e.stopPropagation();moved=false}};if(strip.creativeClickGuard)strip.removeEventListener('click',strip.creativeClickGuard,true);strip.creativeClickGuard=e=>{if(moved){e.preventDefault();e.stopImmediatePropagation();moved=false}};strip.addEventListener('click',strip.creativeClickGuard,true);
}
function toggleCreativeImage(id,checked){let f=creativeForm();f.references=checked?[...new Set([...f.references,id])]:f.references.filter(x=>x!==id);f.dirty=true;creative.token=null;let scroll=$('#creative-gallery').scrollLeft;drawCreativeGallery();$('#creative-gallery').scrollLeft=scroll;$('#creative-gallery').querySelector('[data-reference="'+id+'"]')?.focus({preventScroll:true});$('#creative-saved').textContent='有未保存的创作要求'}
function selectCreativeImages(all){let f=creativeForm();f.references=all?creative.details.gallery.images.filter(im=>creativeImage(im.local_path)).map(im=>im.id):[];f.dirty=true;creative.token=null;drawCreativeGallery();$('#creative-saved').textContent='有未保存的创作要求'}
function updateCreativePrompt(){let f=creativeForm(),p=creative.selected,third=creative.status?.provider_kind==='lk888';$('#creative-prompt-preview').textContent=[third?'生成适合 T 恤 / 卫衣 POD 的独立印花，透明背景，不生成服装照片。':'生成适合 T 恤 / 卫衣 POD 的原创独立印花，透明背景，不生成服装照片。','参考商品：'+(p.name||p.id),'创新方向：'+f.direction,'保留特征：'+(f.retain||(third?'按照用户要求确定':'保留抽象主题，不复制原设计')),'希望改变：'+f.changes,'目标人群与使用场景：'+(f.audience||'由设计方向确定'),'补充要求：'+(f.extra||'无'),`共生成 ${f.count} 款不同方案，每张图片呈现一个独立图案。`,third?'根据用户要求和参考图生成所需图案。':'参考图用于理解抽象主题，禁止复制完整设计、商标和受保护作品。'].join('\n')}
function creativeBody(){let f=creativeForm();return {market:creative.selected.market,id:creative.selected.id,brief:Object.fromEntries(['direction','count','retain','changes','audience','extra','references','model','mode','garment','style','color','parent_image_id'].map(k=>[k,f[k]]))}}
async function saveCreativeBrief(){
  let body=creativeBody(),key=creativeKey(creative.selected),button=$('#creative-save');button.disabled=true;
  try{let d=await creativePost('/api/innovation/brief',body);let f=creative.forms.get(key);if(JSON.stringify(Object.fromEntries(Object.keys(body.brief).map(k=>[k,f[k]])))===JSON.stringify(body.brief)){f.dirty=false;f.saved_at=d.saved_at}if(state.page==='innovation'&&creativeKey(creative.selected)===key&&$('#creative-saved'))$('#creative-saved').textContent=f.dirty?'已保存，仍有后续修改未保存':'已保存于 '+d.saved_at.replace('T',' ').slice(0,19);toast('创作要求已保存到本地')}catch(e){toast('保存失败：'+e.message)}finally{if(button.isConnected)button.disabled=false}
}
async function generateCreative(){
  if(creative.submitting||!creative.status?.ready)return;
  let body=creativeBody();if(body.brief.mode!=='mockup'&&!body.brief.changes.trim()){toast('请填写希望做哪些变化');$('#creative-form').elements.namedItem('changes').focus();return}if(body.brief.mode==='mockup'&&!body.brief.parent_image_id){toast('请先在下方选择已有印花生成 Mockup');return}
  creative.submitting=true;let button=$('#creative-generate');button.disabled=true;button.textContent='正在提交…';
  creative.token=creative.token||crypto.randomUUID();
  try{await creativePost('/api/innovation/generate',{...body,request_id:creative.token});toast('任务已提交，结果将在下方自动显示');await loadCreativeResults()}catch(e){toast(e.message)}finally{creative.submitting=false;if(button.isConnected){button.disabled=false;button.innerHTML=uiIcon('ai')+' AI 生图'}}
}
function previewCreative(index){let im=creative.details.gallery.images[index];creativeLightbox(im.local_path,'参考图 '+(index+1))}
function creativeLightbox(path,title){let src=creativeImage(path);if(!src)return;let dialog=document.createElement('dialog');dialog.className='creative-lightbox';dialog.innerHTML=`<div><strong>${esc(title)}</strong><button type="button" class="btn small">关闭</button></div><img src="${esc(src)}" alt="${esc(title)}">`;document.body.append(dialog);dialog.querySelector('button').onclick=()=>dialog.close();dialog.onclick=e=>{if(e.target===dialog)dialog.close()};dialog.onclose=()=>dialog.remove();dialog.showModal()}
const creativeStyles=[['white','白底商品图'],['flat','平铺展示'],['model','模特上身'],['lifestyle','生活方式']];
const creativeStates={queued:'排队中',running:'正在生成',retry_wait:'限流等待重试',pause_requested:'暂停中 · 等待当前请求返回',paused:'已暂停',done:'已完成',partial:'部分完成',failed:'生成失败',interrupted:'结果待核对'};
function creativeSelect(name,label,value,choices){return `<label>${label}<select name="${name}">${choices.map(([v,t])=>`<option value="${v}" ${value===v?'selected':''}>${t}</option>`).join('')}</select></label>`}
function creativeImages(type){return creative.jobs.flatMap(j=>j.images.filter(i=>i.image_type===type).map(i=>({...i,job:j})))}
function creativeImageCard(im,selectable=false){let mock=im.image_type==='mockup',cfg=im.metadata.mockup;return `<article class="creative-output-card ${selectable&&creative.mockupSelection.has(im.id)?'is-selected':''} ${selectable&&!im.saved_at?'is-selectable':''}" ${selectable&&!im.saved_at?`onclick="creativeToggleMockupCard(event,this,${im.id})"`:''}>
  ${selectable?`<label class="creative-mockup-choice"><input type="checkbox" aria-label="选择商品图 #${im.id}" ${creative.mockupSelection.has(im.id)?'checked':''} ${im.saved_at?'disabled':''} onchange="selectCreativeMockup(${im.id},this.checked)">${im.saved_at?'<span>已保存</span>':''}</label>`:''}
  <button class="creative-result-preview ${!mock?'transparent':''}" data-image="${esc(im.local_path)}" onclick="creativeLightbox(this.dataset.image,'${mock?'Mockup':'独立印花'} v${im.version}')">${creativeThumb(im.local_path)}</button>
  <strong>${mock?(cfg?.garment==='hoodie'?'卫衣':'T 恤'):'独立印花'} <small>v${im.version} · #${im.id}</small></strong>
  <p class="creative-help">${mock?esc(creativeStyles.find(x=>x[0]===cfg?.style)?.[1]||'Mockup')+' · 印花 #'+im.design_image_id:im.metadata.transparent_pixels>0?'PNG · 已验证透明像素':im.metadata.transparent_pixels===0?'PNG · 未检测到透明像素，请检查输出':im.metadata.alpha_channel?'PNG · 含透明通道（待检查透明区域）':'PNG · 未检测到透明通道，请检查输出'}${im.parent_image_id?' · 上一版 #'+im.parent_image_id:''}</p>
  <div class="creative-output-actions"><a class="btn small" href="${esc(im.local_path)}" download="folio-${im.image_type}-${im.id}-v${im.version}.png">下载 PNG</a>
  <button class="btn small" onclick="creativeEditImage(${im.id},'${mock?'mockup':'design'}')">${mock?'重新生成 / 换色':'继续修改'}</button>
  ${!mock?`<button class="btn small" onclick="creativeEditImage(${im.id},'mockup')">生成 Mockup</button>`:''}
  ${mock?`<button class="btn small" onclick="creativeSaveImage(${im.id},this)" ${im.saved_at?'disabled':''}>${im.saved_at?'已保存成品库':'保存成品库'}</button>`:''}</div>
  <details class="creative-prompt" data-creative-detail="image-${im.id}"><summary>模型与提示词</summary><pre>${esc(im.model||im.job.provider)}\n${esc(im.prompt||im.job.prompt)}</pre></details></article>`}
function creativeTaskCard(job){let busy=['queued','running','retry_wait'].includes(job.state);return `<article class="creative-task-row"><div><strong>${esc(creativeStates[job.state]||job.state)}${busy?' · '+(job.stage==='mockup'?'服装 Mockup':job.stage==='design'?'独立印花':'等待执行'):''}</strong><p>${esc(job.created_at.replace('T',' ').slice(0,19))} · ${esc(job.provider)} · 已存 ${job.images.length} 张 · ${job.provider_cost!=null?'已确认 '+job.provider_cost.toFixed(4)+' 平台算力':job.estimated_cost_usd==null?'费用待 API 确认':'估算 $'+job.estimated_cost_usd.toFixed(4)}</p>${job.error?`<p class="creative-error">${esc(job.error)}</p>`:''}</div>
  ${!busy&&job.calls.some(x=>['failed','unknown'].includes(x.state))?`<button class="btn small" onclick="creativeRetry('${job.id}')">重试未完成步骤</button>`:''}
  <details class="creative-prompt" data-creative-detail="task-${job.id}"><summary>调用记录</summary><pre>${esc(JSON.stringify({references:job.brief.reference_selection,calls:job.calls,logs:job.logs},null,2))}</pre></details></article>`}
async function loadCreativeResults(){
  if(state.page!=='innovation'||!creative.selected)return;
  clearTimeout(creative.timer);let key=creativeKey(creative.selected),version=creative.renderVersion;
  try{let [d,status]=await Promise.all([api('/api/innovation/drafts?'+new URLSearchParams({id:creative.selected.id,market:creative.selected.market,page:creative.resultPage})),api('/api/innovation/status')]);
    if(state.page!=='innovation'||version!==creative.renderVersion||key!==creativeKey(creative.selected))return;
    let expanded=[...document.querySelectorAll('[data-creative-detail][open]')].map(el=>el.dataset.creativeDetail);
    creative.jobs=d.items;creative.status=status;
    let button=$('#creative-generate');if(button&&!creative.submitting){button.disabled=!status.ready;button.innerHTML=uiIcon('ai')+(status.ready?' AI 生图':' AI 生图 · 待配置密钥')}
    let service=$('.creative-service');if(service){service.classList.toggle('ready',status.ready);service.querySelector('strong').textContent=status.ready?status.provider:'AI 生图待配置密钥';service.querySelector('p').textContent=status.message}
    let tasks=d.product_tasks||{},active=(tasks.queued||0)+(tasks.running||0)+(tasks.retry_wait||0),waiting=tasks.pause_requested||0,paused=tasks.paused||0;let pause=$('#creative-pause');if(pause&&!creative.pauseBusy){pause.disabled=!(active+waiting+paused);pause.dataset.action=active?'pause':'resume';pause.textContent=active?'暂停生图':waiting?'取消暂停':'继续生图';pause.title='暂停后，已发出的请求会继续并可能产生费用；后续步骤停止。'}
    $('#creative-task-summary').textContent=`共 ${d.total} 个任务`+(active?` · ${active} 个进行中`:'')+(waiting?` · ${waiting} 个暂停中`:'')+(paused?` · ${paused} 个已暂停`:'');
    $('#creative-task-status').innerHTML=(d.items.map(creativeTaskCard).join('')||'<p class="creative-help">尚无生成任务。填写创作要求后点击 AI 生图。</p>')+`<div class="creative-pagination"><button class="btn small" onclick="creativeResultPage(-1)" ${creative.resultPage<=1?'disabled':''}>上一页</button><span>${creative.resultPage} / ${Math.max(1,Math.ceil(d.total/20))}</span><button class="btn small" onclick="creativeResultPage(1)" ${creative.resultPage*20>=d.total?'disabled':''}>下一页</button></div>`;
    let download=$('#creative-download-all');download.hidden=!d.product_images;download.href='/api/innovation/download?'+new URLSearchParams({id:creative.selected.id,market:creative.selected.market});
    for(let im of creativeImages('mockup'))if(im.saved_at)creative.mockupSelection.delete(im.id);
    for(let [type,title] of [['design','AI 印花结果'],['mockup','AI Mockup 结果']]){let images=creativeImages(type),container=$('#creative-'+type+'-results'),scroll=container.querySelector('.creative-results')?.scrollLeft||0;container.innerHTML=`<div class="creative-result-heading"><h2>${title} <small>${images.length} 张</small></h2>${type==='mockup'&&(images.length||creative.mockupSelection.size)?`<div class="creative-bulk-actions"><button class="btn small" type="button" onclick="selectVisibleMockups()">全选本页</button><button class="btn small" type="button" onclick="clearMockupSelection()">清空</button><button id="creative-save-selected" class="btn primary small" type="button" onclick="saveSelectedMockups()" ${!creative.mockupSelection.size||creative.savingSelection?'disabled':''}>保存至成品库（${creative.mockupSelection.size}）</button></div>`:''}</div><p class="creative-help">${type==='design'?'原始独立印花是生产素材；请核对印刷尺寸与清晰度。':'AI Mockup 用于展示，无法保证像素级还原；生产请使用关联的原始印花。'}</p>`+(images.length?'<div class="creative-results">'+images.map(im=>creativeImageCard(im,type==='mockup')).join('')+'</div><p class="creative-scroll-hint">左右滑动查看更多素材</p>':'<div class="creative-task-message">暂无结果，生成成功后会在这里显示。</div>');let row=container.querySelector('.creative-results');if(row)row.scrollLeft=scroll}
    for(let el of document.querySelectorAll('[data-creative-detail]'))el.open=expanded.includes(el.dataset.creativeDetail);
    let select=$('#creative-parent'),f=creativeForm();if(select){let designs=creativeImages('design');select.innerHTML='<option value="">新设计</option>'+designs.map(im=>`<option value="${im.id}" ${im.id===f.parent_image_id?'selected':''}>印花 #${im.id} · v${im.version}</option>`).join('')}
  }catch(e){if($('#creative-task-status'))$('#creative-task-status').textContent='任务查询失败：'+e.message;if($('#creative-task-summary'))$('#creative-task-summary').textContent='查询失败，请展开查看'}
  if(state.page==='innovation')creative.timer=setTimeout(loadCreativeResults,4000);
}
async function toggleCreativePause(){
  if(creative.pauseBusy||!creative.selected)return;
  let button=$('#creative-pause'),action=button.dataset.action,product={market:creative.selected.market,id:creative.selected.id};
  if(!action)return;creative.pauseBusy=true;button.disabled=true;
  try{let result=await creativePost('/api/innovation/pause',{...product,action});toast(!result.changed?'当前没有可操作的任务':action==='pause'?'已暂停后续步骤；已发出的请求会继续并可能产生费用':'已继续未完成步骤');}
  catch(e){toast(e.message)}finally{creative.pauseBusy=false;if(state.page==='innovation'&&creative.selected&&creativeKey(product)===creativeKey(creative.selected))await loadCreativeResults()}
}
function creativeResultPage(delta){creative.resultPage+=delta;loadCreativeResults()}
async function creativeSaveImage(id,button){button.disabled=true;try{await creativePost('/api/innovation/save-image',{image_id:id});creative.mockupSelection.delete(id);button.textContent='已保存成品库';toast('已保存到成品库；Mockup 同时保留原始印花');if(state.page==='innovation')await loadCreativeResults()}catch(e){button.disabled=false;toast(e.message)}}
function updateMockupSelectionUI(){
  let save=$('#creative-save-selected');if(save){save.textContent=`保存至成品库（${creative.mockupSelection.size}）`;save.disabled=!creative.mockupSelection.size||creative.savingSelection}
  for(let input of document.querySelectorAll('#creative-mockup-results .creative-mockup-choice input'))input.closest('.creative-output-card').classList.toggle('is-selected',input.checked);
}
function creativeToggleMockupCard(event,card,id){
  if(event.target.closest('button,a,input,label,details'))return;
  let input=card.querySelector('.creative-mockup-choice input');if(!input||input.disabled)return;
  input.checked=!input.checked;selectCreativeMockup(id,input.checked);
}
function selectCreativeMockup(id,checked){if(checked)creative.mockupSelection.add(id);else creative.mockupSelection.delete(id);updateMockupSelectionUI()}
function selectVisibleMockups(){for(let im of creativeImages('mockup'))if(!im.saved_at)creative.mockupSelection.add(im.id);for(let input of document.querySelectorAll('#creative-mockup-results .creative-mockup-choice input:not(:disabled)'))input.checked=true;updateMockupSelectionUI()}
function clearMockupSelection(){creative.mockupSelection.clear();for(let input of document.querySelectorAll('#creative-mockup-results .creative-mockup-choice input'))input.checked=false;updateMockupSelectionUI()}
async function saveSelectedMockups(){
  if(creative.savingSelection||!creative.selected||!creative.mockupSelection.size)return;
  let product={market:creative.selected.market,id:creative.selected.id},ids=[...creative.mockupSelection];creative.savingSelection=true;updateMockupSelectionUI();
  try{let result=await creativePost('/api/innovation/save-images',{...product,image_ids:ids});if(creative.selected&&creativeKey(product)===creativeKey(creative.selected))for(let id of ids)creative.mockupSelection.delete(id);toast(`已保存 ${result.saved} 张商品图至成品库`)}
  catch(e){toast(e.message)}finally{creative.savingSelection=false;if(state.page==='innovation'&&creative.selected&&creativeKey(product)===creativeKey(creative.selected))await loadCreativeResults()}
}
async function creativeRetry(id){let job=creative.jobs.find(j=>j.id===id);if(!job)return;let unknown=job.calls.some(x=>x.state==='unknown');if(unknown&&!confirm('上次请求结果不确定。请先核对服务商账单。继续重试可能再次计费，确认仍要重试？'))return;try{await creativePost('/api/innovation/retry',{job_id:id,acknowledge_charge:unknown});toast('仅重试未完成步骤，已保存图片保留');if(state.page==='innovation')await loadCreativeResults();else await innovationDraftsPage()}catch(e){toast(e.message)}}
function creativeEditImage(id,mode){
  let im=creative.jobs.flatMap(j=>j.images.map(i=>({...i,job:j}))).find(x=>x.id===id);if(!im)return;
  let cfg=im.metadata.mockup||{},brief={...creativeDefault(),...im.job.brief,mode,parent_image_id:id,count:1,garment:cfg.garment||'both',style:cfg.style||'white',color:cfg.color||'白色',mockup_changes:''};
  let dialog=document.createElement('dialog');dialog.className='creative-edit-dialog';dialog.innerHTML=`<form class="creative-form"><h2>${mode==='design'?'继续修改独立印花':'生成 / 修改 Mockup'}</h2><p class="creative-help">${mode==='design'?'以上一版印花为参考，保存新版本。':'始终使用原始独立印花，保留图案与文字。'}</p>${creativeSelect('model','AI 模型',brief.model,[['sunburst','Sunburst'],['flare','Flare']])}${mode==='design'?'<label>修改要求<textarea name="changes" required maxlength="4000" placeholder="例如：保留主题，改为美式复古配色，减少装饰"></textarea></label>':creativeSelect('garment','服装',brief.garment,[['both','T 恤 + 卫衣'],['tshirt','T 恤'],['hoodie','卫衣']])+creativeSelect('style','展示风格',brief.style,creativeStyles)+`<label>服装颜色<input name="color" maxlength="80" value="${esc(brief.color)}" required></label><label>其他修改要求<textarea name="mockup_changes" maxlength="2000" placeholder="例如：调整背景、灯光或模特姿势，保留原始印花"></textarea></label>`}<p class="creative-error" role="status"></p><div class="creative-actions"><button class="btn primary" type="submit">开始生成</button><button class="btn" type="button" data-close>取消</button></div></form>`;
  document.body.append(dialog);dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();let token=crypto.randomUUID(),sending=false;
  dialog.querySelector('form').oninput=()=>{if(!sending)token=crypto.randomUUID()};
  dialog.querySelector('form').onsubmit=async e=>{e.preventDefault();if(sending)return;sending=true;let form=e.target,button=form.querySelector('[type="submit"]');button.disabled=true;for(let [k,v] of new FormData(form))brief[k]=v;try{await creativePost('/api/innovation/generate',{market:im.job.market,id:im.job.product_id,brief,request_id:token});dialog.close();toast(state.page==='innovation'?'已提交新版本任务':'已提交修改任务，请到热款创新查看结果并保存');if(state.page==='innovation')await loadCreativeResults();else await innovationDraftsPage()}catch(err){form.querySelector('[role="status"]').textContent=err.message;button.disabled=false;sending=false}};dialog.showModal();
}
async function innovationDraftsPage(){
  let version=++creative.renderVersion;
  let d=await api('/api/innovation/library?page='+creative.draftsPage);
  if(state.page!=='innovation-drafts'||version!==creative.renderVersion)return;
  let closedProducts=new Set([...document.querySelectorAll('.creative-draft-results:not([open])')].map(el=>el.dataset.product));
  creative.jobs=d.items;
  let groups=new Map();
  for(let job of d.items){let key=JSON.stringify([job.market,job.product_id]);if(!groups.has(key))groups.set(key,{key,job,images:[]});groups.get(key).images.push(...job.images.map(im=>({...im,job})));}
  $('#app').innerHTML=heading('DESIGN LIBRARY','成品库',`已保存、准备上架的商品图，共 ${num(d.images)} 张。原始印花会保留用于后续修改。`,`<button class="btn primary" onclick="go('innovation')">开始新款创作</button>`)+(groups.size?`<div class="creative-drafts">${[...groups.values()].map(group=>`<article class="panel creative-draft"><h2>${esc(group.job.product.name||group.job.product_id)}</h2><details class="creative-draft-results" data-product="${esc(group.key)}" ${closedProducts.has(group.key)?'':'open'}><summary>商品图 <span>${group.images.length} 张</span></summary><div class="creative-results" tabindex="0" aria-label="商品图，可左右滚动">${group.images.map(im=>creativeImageCard(im)).join('')}</div></details></article>`).join('')}</div><div class="creative-pagination"><button class="btn" onclick="creativeDraftsPage(-1)" ${creative.draftsPage<=1?'disabled':''}>上一页</button><span>${creative.draftsPage} / ${Math.max(1,Math.ceil(d.total/20))}</span><button class="btn" onclick="creativeDraftsPage(1)" ${creative.draftsPage*20>=d.total?'disabled':''}>下一页</button></div>`:'<div class="panel empty">暂无已保存的商品图。请在热款创新中生成 Mockup，并将准备上架的图片保存至成品库。</div>');

}
function creativeDraftsPage(delta){creative.draftsPage+=delta;innovationDraftsPage().catch(e=>toast(e.message))}
