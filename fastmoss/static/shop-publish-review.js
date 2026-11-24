// Keep the batch and its review results while moving between the list and editor.
function publishReviewOpen(v){return publishView===v&&!$('#modal').classList.contains('hidden')&&!!document.querySelector('.publish-drawer')}
function publishReviewStatus(p){return p.checkResult?.status||'pending'}
function publishReviewIssues(error){
 if(error.issues?.length)return error.issues;
 const message=error.error||'检查未完成，请重试';
 const routes=[[/尺码表/,'publish-company-size'],[/SKU|规格|库存|售价|价格/,'publish-variants'],[/类目|仓库/,'publish-logistics'],[/包装|重量|尺寸/,'publish-package'],[/资质|属性|声明/,'publish-compliance'],[/图片|主图|OCR/,'publish-photos'],[/描述|详情/,'publish-description']];
 return [{section:routes.find(([pattern])=>pattern.test(message))?.[1]||'publish-title',label:'查看并修改',message}];
}
function publishReviewEditBanner(p){
 const status=publishReviewStatus(p),issues=p.checkResult?.issues||[];
 return `<div class="publish-review-editbar"><button class="btn small" type="button" onclick="saveAndReviewPublish()">← 保存并复检，返回清单</button><span>正在修改第 ${publishView.index+1} 件 · ${status==='passed'?'上次检验通过':'修改后重新检验'}</span></div>${issues.length?`<div class="publish-review-editissues"><strong>本次待处理</strong>${issues.map((issue,i)=>`<button type="button" onclick="focusPublishIssue(${i})">${esc(issue.label)}：${esc(issue.message)} ↗</button>`).join('')}</div>`:''}`;
}
function publishReviewMaterial(p){
 const r=p.checkResult?.materialReview;if(!r?.applicable)return '';
 const label=p.checkResult.status==='stale'?'面料待复检':p.checkResult.materialCompleted?'已自动补全面料':r.status==='consistent'?'面料核对一致':'面料需要核实';
 return `<div class="publish-review-material"><p><strong>${label}${r.suggestion?'：'+esc(r.suggestion):''}</strong></p>${r.issues?.length?'<p>当前证据无法自动确定，已暂停此商品上架。</p>':''}
 ${r.evidence?.length?`<details><summary>查看面料核对依据</summary><dl>${r.evidence.map(row=>`<dt>${esc(row.label)}${row.ignored?' · 其他款式':''}</dt><dd>${esc(row.text)}</dd>`).join('')}</dl></details>`:''}</div>`;
}
function publishReviewQuickFix(p,index){
 const issues=p.checkResult?.issues||[],options=p.reviewOptions||{},has=kind=>issues.some(i=>i.kind===kind),f=p.profile;
 if(!issues.some(i=>['brand','category','inventory','inventory_reference','sold_out','package','attribute','image_ocr_pending'].includes(i.kind)||i.section==='publish-company-size'))return '';
 const input=(key,label)=>`<label>${label}<input class="search" name="${key}" value="${esc(f[key]||'')}" inputmode="decimal"></label>`;
 return `<details class="publish-review-quick" open><summary>直接处理此商品</summary><form id="publish-quick-${index}" onsubmit="event.preventDefault();savePublishQuick(${index})"><fieldset ${publishView.busy?'disabled':''}>
 ${has('brand')?`<label>实际商品品牌<select class="search" name="brand_id"><option value="">请选择</option><option value="unbranded" ${f.brand_id==='unbranded'?'selected':''}>无品牌</option>${(options.brands||[]).filter(b=>b.brand_status!=='UNAVAILABLE').map(b=>`<option value="${esc(b.id)}" ${String(f.brand_id)===String(b.id)?'selected':''}>${esc(b.name)}</option>`).join('')}</select></label><label><input type="checkbox" name="brand_confirmed" ${f.brand_confirmed?'checked':''}>已核实品牌归属</label>`:''}
 ${has('category')?`<label>官方可售末级类目<select class="search" name="category_id"><option value="">请根据实际商品确认</option>${(options.category_candidates||[]).map(c=>`<option value="${esc(c.id)}">${esc(c.path)}</option>`).join('')}</select></label><p>${esc(options.category_candidates?.[0]?.evidence||options.category_message||'暂无能确认的候选，请打开商品查看官方类目树。')}</p>`:''}
 ${has('inventory')||has('inventory_reference')||has('sold_out')?`<label>本商品保留规格的统一实际库存<input class="search" name="stock_all" type="number" min="0" max="99999" step="1" placeholder="留空保留现值，0 表示售罄"></label><label><input type="checkbox" name="inventory_confirmed">已核实当前库存为本商品实际可售库存</label>${has('sold_out')?'<label><input type="checkbox" name="exclude_zero">排除所有库存为 0 的 SKU（保留原规格资料）</label>':''}`:''}
 ${has('package')?`<div class="publish-grid">${[['weight','包装重量（kg）'],['length','包装长（cm）'],['width','包装宽（cm）'],['height','包装高（cm）']].map(([key,label])=>input(key,label)).join('')}</div>`:''}
 ${issues.filter(i=>i.kind==='attribute').map(issue=>{const rule=(options.attributes||[]).find(r=>String(r.id)===issue.attribute_id);return rule?`<label>${esc(rule.name)}<select class="search" data-quick-attribute="${esc(rule.id)}" ${rule.is_multiple_selection?'multiple':''}><option value="">请选择</option>${(rule.values||[]).map(v=>`<option value="${esc(v.id)}" ${(f.attributes?.[rule.id]||[]).some(a=>String(a.id)===String(v.id))?'selected':''}>${esc(v.name)}</option>`).join('')}</select>${rule.is_customizable?`<input class="search" data-quick-custom="${esc(rule.id)}" aria-label="${esc(rule.name)} 自定义值" placeholder="可填写已核实的自定义值">`:''}</label>`:''}).join('')}
 ${has('image_ocr_pending')?`<button class="btn small" type="button" onclick="recognizeReviewImages(${index})">后台识别已选图片</button>`:''}
 ${issues.some(i=>i.section==='publish-company-size')?publishSizeCandidates(p,'chooseReviewSize',index):''}
 <button class="btn small primary" type="submit">保存以上修改并复检</button><p>面料冲突、图片替换和逐规格改价可通过对应问题进入编辑。</p></fieldset></form></details>`;
}
async function chooseReviewSize(index,kind,value){
 const v=publishView;if(v.busy)return;
 const p=v.items[index];if(!setPublishSize(p,kind,value))return;
 await savePublishQuick(index);
}
async function recognizeReviewImages(index){
 const v=publishView;if(v.busy)return;const p=v.items[index];
 await startPublishPreparation([{market:p.market,id:p.id}],async prepared=>{
  publishView=v;
  const item=prepared.items.find(x=>x.market===p.market&&x.id===p.id);
  if(item){p.images=item.images;p.size_chart_candidates=item.size_chart_candidates;}
  drawPublishReview(true);await checkPublishReview([index]);
 });
}
async function savePublishQuick(index){
 const v=publishView;if(v.busy)return;const p=v.items[index],el=document.getElementById('publish-quick-'+index);if(!el)return;
 const values=new FormData(el),f={...p.profile};
 for(const key of ['weight','length','width','height'])if(values.has(key))f[key]=values.get(key).trim();
 if(values.has('brand_id')){f.brand_id=values.get('brand_id');f.brand_confirmed=values.has('brand_confirmed')}
 if(values.get('category_id')){if(values.get('category_id')!==f.category_id){f.attributes={};f.certifications={};}f.category_id=values.get('category_id');f.category_source='manual';f.category_shop_id=v.connection.selected_shop_id}
 const stock=values.get('stock_all');
 if(stock){if(!/^\d+$/.test(stock)||+stock>99999)return publishFeedback('库存须为 0～99999 的整数');f.stocks={...f.stocks};for(const sku of p.variants)if(!(f.excluded_sku_ids||[]).includes(String(sku.id)))f.stocks[sku.id]=stock;}
 if(values.has('inventory_confirmed'))f.inventory_verified={...f.stocks};
 if(values.has('exclude_zero'))f.excluded_sku_ids=[...new Set([...(f.excluded_sku_ids||[]),...p.variants.filter(s=>String(f.stocks?.[s.id]??f.stock)==='0').map(s=>String(s.id))])];
 f.attributes={...f.attributes};el.querySelectorAll('[data-quick-attribute]').forEach(input=>f.attributes[input.dataset.quickAttribute]=[...input.selectedOptions].filter(o=>o.value).map(o=>({id:o.value})));
 el.querySelectorAll('[data-quick-custom]').forEach(input=>{if(input.value.trim())f.attributes[input.dataset.quickCustom]=[{name:input.value.trim()}]});
 v.busy=true;v.batchCheck=null;p.checkResult={...p.checkResult,status:'stale'};
 try{await shopPost('listing-profile',{market:p.market,id:p.id,profile:f});p.profile=f;v.busy=false;await checkPublishReview([index])}
 catch(e){publishFeedback('保存失败：'+e.message)}finally{v.busy=false}
}
function drawPublishReview(restore=false){
 const v=publishView,total=v.items.length,passed=v.items.filter(p=>publishReviewStatus(p)==='passed').length;
 v.reviewStarted=true;v.reviewScreen=true;v.reviewFilter||='pending';
 const visible=v.items.map((p,index)=>({p,index})).filter(({p})=>v.reviewFilter==='all'||(v.reviewFilter==='passed'?publishReviewStatus(p)==='passed':publishReviewStatus(p)!=='passed'));
 const ready=passed===total,confirmed=ready&&!!v.batchCheck;
 $('#modal').innerHTML=`<div class="drawer product-editor publish-drawer publish-review-dashboard" role="dialog" aria-modal="true" aria-labelledby="publish-review-heading">
 <div class="drawer-top"><div><span class="eyebrow">TIKTOK SHOP · 上架前检查</span><h2 id="publish-review-heading">${ready?'本批商品已通过检验':'逐项修改，完成本批上架'}</h2></div><button class="close" aria-label="关闭检查清单" onclick="closeModal()">×</button></div>
 <div class="publish-review-summary ${ready?'is-ready':''}"><div><strong>${passed}<small> / ${total} 件通过</small></strong><span>${ready?'资料已就绪，可在下方确认上架':'点击红色问题或图片，直接进入对应商品修改'}</span></div><div class="publish-review-progress" role="progressbar" aria-label="商品检验进度" aria-valuenow="${passed}" aria-valuemin="0" aria-valuemax="${total}"><span style="width:${passed/total*100}%"></span></div><p>① 点击问题修改　→　② 保存并复检　→　③ 返回这里上架</p></div>
 <div class="publish-review-filters" role="group" aria-label="筛选检验状态">${[['pending','待处理',total-passed],['passed','已通过',passed],['all','全部',total]].map(([key,label,count])=>`<button type="button" aria-pressed="${v.reviewFilter===key}" onclick="filterPublishReview('${key}')" ${v.busy?'disabled':''}>${label}<b>${count}</b></button>`).join('')}<span>目标店铺：${esc(v.connection.shops?.find(s=>String(s.id)===String(v.connection.selected_shop_id))?.name||v.connection.selected_shop_id||'未选择')}</span></div>
 <div class="publish-review-cards">${visible.map(({p,index})=>{
  const status=publishReviewStatus(p),issues=p.checkResult?.issues||[],photo=publishVisibleImages(p)[0];
  const labels={passed:'✓ 检验通过',failed:'! 待修改',stale:'↻ 已修改 · 待复检',pending:'待检验',checking:'正在检查'};
  return `<article class="publish-review-card is-${status}"><div class="publish-review-product">${photo?`<img src="${esc(photo.local_path||photo.url)}" alt="商品缩略图" loading="lazy">`:''}<div><small>第 ${index+1} 件 · ${esc(p.market)} · ${esc(p.id)}</small><h3><button type="button" onclick="editPublishReview(${index})" ${v.busy?'disabled':''}>${esc(p.profile.title_input||p.title)}</button></h3></div><span class="publish-review-badge">${labels[status]}</span></div>
  ${publishReviewMaterial(p)}
  ${publishReviewQuickFix(p,index)}
  ${status==='passed'?'<p class="publish-review-passed">已保存并通过检验，可继续处理其他商品。</p>':`<div class="publish-review-issues">${issues.map((issue,i)=>{
   const image=p.images.find(im=>im.key===issue.image_key);
   return `<button type="button" class="publish-review-issue" onclick="editPublishReview(${index},${i})" ${v.busy?'disabled':''}>${image?`<img src="${esc(image.local_path||image.url)}" alt="${esc(issue.label)}" loading="lazy">`:'<span class="publish-review-issue-icon" aria-hidden="true">!</span>'}<span><strong>${esc(issue.label)}</strong><span>${esc(issue.message)}</span></span><b>去修改 ↗</b></button>`;
  }).join('')||'<p>尚无最新检验结果，请重新检验。</p>'}</div>`}
  <div class="publish-review-card-actions"><button class="btn small" type="button" onclick="skipPublishReview(${index})" ${v.busy?'disabled':''}>不上架此商品</button><button class="btn small" type="button" onclick="editPublishReview(${index})" ${v.busy?'disabled':''}>${status==='passed'?'查看商品':'打开上架编辑'}</button>${status!=='passed'?`<button class="btn small" type="button" onclick="checkPublishReview([${index}])" ${v.busy?'disabled':''}>重新检验此商品</button>`:''}</div></article>`;
 }).join('')||`<div class="publish-review-empty"><strong>${ready?'✓ 全部问题已处理':'暂无此状态的商品'}</strong><p>${ready?'所有商品已保存并通过检验。':'可切换上方筛选查看其他商品。'}</p></div>`}</div>
 <div id="publish-feedback" class="publish-review-feedback" role="status">${esc(v.reviewMessage||'')}${v.checkProgress?`<p>已检查 ${v.checkProgress.done} / ${v.checkProgress.total} 件 · 当前 ${esc(v.checkProgress.title||'')}<progress max="${v.checkProgress.total}" value="${v.checkProgress.done}"></progress>${v.busy?'<button class="btn small" type="button" onclick="publishView.stopChecking=true;this.disabled=true">暂停检查，先处理已完成项</button>':''}</p>`:''}</div>
 <div class="editor-footer publish-review-footer"><div><strong>${ready?'已通过 '+total+' 件':'还有 '+(total-passed)+' 件待处理'}</strong><small>${confirmed?'确认后创建真实商品并进入平台审核':'上架前会再次核对整批资料和目标店铺'}</small></div><button class="btn" type="button" onclick="checkPublishBatch()" ${v.busy?'disabled':''}>${v.busy?'正在检验…':'重新检验整批'}</button><button class="btn primary" id="publish-confirm" type="button" onclick="${confirmed?'submitPublishReview()':'checkPublishBatch()'}" ${v.busy||!ready?'disabled':''}>${confirmed?'确认上架 '+total+' 件':'整批检查并上架'}</button></div></div>`;
 if(restore)$('.publish-drawer').scrollTop=v.reviewScroll||0;
}
function filterPublishReview(filter){if(publishView.busy)return;publishView.reviewFilter=filter;publishView.reviewScroll=0;drawPublishReview()}
function skipPublishReview(index){
 const v=publishView;if(!v||v.busy||!v.items[index])return;
 v.reviewScroll=$('.publish-drawer').scrollTop;
 v.items.splice(index,1);v.batchCheck=null;v.meta=null;
 if(!v.items.length){closeModal();publishView=null;toast('本批商品已全部移出上架');return}
 v.index=Math.max(0,Math.min(v.index-(index<v.index?1:0),v.items.length-1));
 v.reviewMessage=`已移出本次上架，剩余 ${v.items.length} 件商品。`;
 drawPublishReview(true);
}
async function editPublishReview(index,issueIndex=0){
 const v=publishView;if(v.busy)return;
 v.reviewScroll=$('.publish-drawer').scrollTop;v.reviewScreen=false;v.index=index;v.meta=null;drawPublish();
 focusPublishIssue(issueIndex);
 if(v.connection.selected_shop_id)await loadPublishMeta();
 if(publishOpen(v))focusPublishIssue(issueIndex);
}
function focusPublishIssue(index){
 const p=publishView.items[publishView.index],issue=p.checkResult?.issues?.[index];if(!issue)return;
 jumpPublishSection(issue.section);
 if(issue.section==='publish-variants'&&p.skusCollapsed)togglePublishSkus($('#publish-sku-content').parentElement.querySelector('.publish-text-button'));
 const target=issue.image_key?[...document.querySelectorAll('[data-publish-image-card]')].find(el=>el.dataset.publishImageCard===issue.image_key):document.getElementById(issue.section);
 if(!target)return;
 for(let parent=target;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
 target.scrollIntoView({block:'center'});target.classList.add('publish-issue-focus');
 target.setAttribute('tabindex','-1');target.focus({preventScroll:true});
 setTimeout(()=>target.classList.remove('publish-issue-focus'),2200);
}
function applyPublishReviewResult(v,result,indices){
 for(const index of indices){
  const p=v.items[index],same=x=>x.market===p.market&&x.id===p.id;
  const updated=(result.items||[]).find(same),error=(result.errors||[]).find(same),passed=(result.checked||[]).some(same);
  if(updated){p.profile=updated.profile;p.reviewOptions=updated.review_options||p.reviewOptions;}
  p.checkResult={status:error?'failed':passed?'passed':'pending',issues:error?publishReviewIssues(error):[],
   materialReview:updated?.material_review,materialCompleted:!!updated?.material_completed};
  for(const im of p.images){
   const review=updated?.image_reviews?.find(image=>image.key===im.key),issue=error?.issues?.find(issue=>issue.image_key===im.key);
   if(review||issue){im.text_matches=review?.text_matches||issue.matches||[];im.review_required=!!im.text_matches.length}
  }
 }
 v.batchCheck=indices.length===v.items.length&&result.ready?result:null;
}
async function saveAndReviewPublish(){return checkPublishReview([publishView.index])}
async function approvePublishReview(){return checkPublishReview([publishView.index],true)}
async function checkPublishBatch(){if(!publishView?.items.length)return;return checkPublishReview(publishView.items.map((_,i)=>i))}
async function checkPublishReview(indices,manual=false){
 const v=publishView;if(v.busy)return;
 const fromEditor=!!$('#publish-form');let saved=!fromEditor;
 if(!fromEditor)v.reviewScroll=$('.publish-drawer').scrollTop;
 v.busy=true;v.batchCheck=null;
 v.reviewMessage='正在保存并检验商品，自动核对和补全面料，同时检查图片、文案、类目与规格…';
 if(fromEditor){$('#publish-form').inert=true;publishFeedback(v.reviewMessage)}else drawPublishReview(true);
 try{
  if(fromEditor){
   if(!await savePublishForm(true)||!publishOpen(v))return;
   saved=true;
   if(indices.length===v.items.length)for(const p of v.items){
    if(p===v.items[v.index])continue;
    initializePublishContent(p);
    await shopPost('listing-profile',{market:p.market,id:p.id,profile:p.profile});
    if(!publishReviewOpen(v))return;
   }
  }
  v.changed=false;
  const items=indices.map(i=>({market:v.items[i].market,id:v.items[i].id}));
  v.stopChecking=false;v.checkProgress={done:0,total:indices.length,title:''};let completed=0;
  for(const index of indices){
   if(v.stopChecking||!publishReviewOpen(v))break;
   const p=v.items[index];v.checkProgress.title=p.profile.title_input||p.title||p.id;
   p.checkResult={...p.checkResult,status:'checking'};drawPublishReview(true);
   const result=await shopPost('listing-check',{items:[{market:p.market,id:p.id}]});
   if(!publishReviewOpen(v))return;
   if(v.changed){v.reviewMessage='资料在检验期间发生变化，请重新检验。';return}
   applyPublishReviewResult(v,result,[index]);v.checkProgress.done++;
   completed+=(result.items||[]).filter(item=>item.market===p.market&&item.id===p.id&&item.material_completed).length;
   v.reviewMessage=`已检查 ${v.checkProgress.done} / ${indices.length} 件；检查结果已逐件保留。`;drawPublishReview(true);
  }
  // Only a full, fresh batch response authorizes submission; per-item successes
  // are useful immediately but cannot reuse a previous batch fingerprint.
  if(indices.length>1&&indices.length===v.items.length&&!v.stopChecking&&v.items.every(p=>publishReviewStatus(p)==='passed')){
   v.reviewMessage='正在核对整批资料版本和目标店铺…';drawPublishReview(true);
   const result=await shopPost('listing-check',{items});if(!publishReviewOpen(v))return;
   applyPublishReviewResult(v,result,indices);
  }
  const failures=indices.filter(i=>publishReviewStatus(v.items[i])!=='passed').length;
  v.reviewMessage=(completed?`已自动补全 ${completed} 件商品面料。`:'')+(v.stopChecking?'检查已暂停，已完成的结果保留，可先处理。':!failures?(manual?'人工审核通过，已保存并返回清单。':'已保存，检验通过。'):`已保存，本次还有 ${failures} 件商品需要处理。`);
  v.busy=false;drawPublishReview(true);
 }catch(e){
  for(const i of indices)if(v.items[i].checkResult?.status==='checking')v.items[i].checkResult={...v.items[i].checkResult,status:'pending'};
  v.reviewMessage='检验未完成：'+e.message;
  if(publishReviewOpen(v)){if(saved)drawPublishReview(true);else publishFeedback(v.reviewMessage)}
 }finally{
  v.busy=false;
  if(publishReviewOpen(v)){if($('#publish-form'))$('#publish-form').inert=false;else drawPublishReview(true)}
 }
}
async function submitPublishReview(){
 const v=publishView,r=v?.batchCheck;if(!v?.items.length||v.busy||!r||v.items.some(p=>publishReviewStatus(p)!=='passed'))return;
 v.busy=true;v.reviewMessage='正在提交本批商品…';drawPublishReview(true);
 try{
  const items=v.items.map(p=>({market:p.market,id:p.id})),result=await shopPost('listing-submit',{items,fingerprint:r.fingerprint});
  if(!result.ready){
   applyPublishReviewResult(v,result,v.items.map((_,i)=>i));v.reviewMessage='提交前复核发现变化，请处理后重新检验。';
  }else{
   if(publishReviewOpen(v)){closeModal();await showPublishJobs()}
   toast('已加入上架队列');
  }
 }catch(e){v.batchCheck=null;v.items.forEach(p=>p.checkResult={...p.checkResult,status:'stale'});v.reviewMessage=e.message}
 finally{v.busy=false;if(publishReviewOpen(v))drawPublishReview(true)}
}
