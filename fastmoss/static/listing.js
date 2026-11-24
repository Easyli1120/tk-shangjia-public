// Listing workspace. Selection is retained across pages and filters until cleared.
let listingCategory=[],listingCategoryGroups=[],listingCategoryPickerOpen=false;
function listingFilterKey(){return JSON.stringify([state.q,listingCategory]);}
function listingCategoryParam(){return listingCategory.length?encodeURIComponent(JSON.stringify(listingCategory)):'';}
function changeListingCategory(value){listingCategory=value?[value]:[];state.listPage=1;clearListingSelection();render();}
function toggleCategoryPicker(){listingCategoryPickerOpen=!listingCategoryPickerOpen;drawListing();}
function applyListingCategories(){listingCategory=[...document.querySelectorAll('#listing-category-picker input:checked')].map(input=>input.value);listingCategoryPickerOpen=false;state.listPage=1;clearListingSelection();render();}
function listingCategoryControls(){return `<div class="listing-category-filter"><div class="listing-category-choice"><button class="btn listing-category-filter-button" type="button" aria-expanded="${listingCategoryPickerOpen}" aria-controls="listing-category-picker" onclick="toggleCategoryPicker()">按商品类目查看${listingCategory.length?' · 已选 '+listingCategory.length+' 个类目':''}</button></div>${listingCategoryPickerOpen?`<div class="listing-category-picker" id="listing-category-picker"><div class="listing-category-picker-list">${listingCategoryGroups.map(c=>`<label><input type="checkbox" value="${esc(c.category)}" ${listingCategory.includes(c.category)?'checked':''}> <span>${esc(c.category)}</span><small>${c.count} 件</small></label>`).join('')}</div><div class="listing-category-picker-actions"><button class="btn small" type="button" onclick="listingCategoryPickerOpen=false;drawListing()">取消</button><button class="btn primary small" type="button" onclick="applyListingCategories()">应用筛选</button></div></div>`:''}</div>`;}

async function openSkuManager(query=''){
 $('#modal').classList.remove('hidden');
 $('#modal').innerHTML=`<div class="drawer" role="dialog" aria-modal="true" aria-labelledby="sku-manager-heading"><div class="drawer-top"><div><span class="eyebrow">LOCAL SKU</span><h2 id="sku-manager-heading">SKU 管理</h2></div><button class="close" type="button" aria-label="关闭 SKU 管理" onclick="closeModal()">×</button></div><p>这里显示本机永久保存的规格 SKU。可按商品名称、来源商品 ID、原始竞品 SKU 或本地 SKU 搜索。</p><form id="sku-manager-form" class="listing-search" onsubmit="event.preventDefault();openSkuManager(document.getElementById('sku-manager-query').value)"><input id="sku-manager-query" class="search" value="${esc(query)}" placeholder="搜索商品、商品 ID 或 SKU" aria-label="搜索 SKU"><button class="btn primary" type="submit">搜索</button></form><div id="sku-manager-result" class="sku-manager-result">正在读取…</div></div>`;
 const result=$('#sku-manager-result');
 try{
  const data=await api('/api/shop/listing-skus?q='+encodeURIComponent(query));
  result.innerHTML=data.items?.length?`<div class="table-wrap"><table class="table"><thead><tr><th>商品</th><th>颜色</th><th>尺码</th><th>其他规格</th><th>本地 SKU</th><th>原始竞品 SKU</th><th>创建时间</th><th>店铺</th><th>上架状态</th></tr></thead><tbody>${data.items.map(x=>`<tr><td><strong>${esc(x.product_title||'—')}</strong><br><small>${esc(x.market)} · ${esc(x.source_product_id)}</small></td><td>${esc(x.color||'—')}</td><td>${esc(x.size||'—')}</td><td>${esc(x.specification||'—')}</td><td><code>${esc(x.seller_sku)}</code></td><td>${esc(x.original_sku||'未提供')}</td><td>${esc(x.sku_created_at||'—')}</td><td>${esc(x.shop_id||'—')}</td><td>${esc(x.platform_state||'未上架')}</td></tr>`).join('')}</tbody></table></div>`:'<div class="panel empty">暂无 SKU 记录。打开一键上架页面后会自动创建。</div>';
 }catch(e){result.innerHTML='<div class="panel empty">读取 SKU 失败：'+esc(e.message)+'</div>'}
 $('#sku-manager-query')?.focus();
}
let listingChosen=new Map(), listingItems=[], listingEdits=new Map(), listingTotal=0, listingAllKeys=[], listingAllQuery=null;
const oldLibrary=library;
selectionNav=()=>navButton(nav.find(x=>x[0]==='selected'));
nav.splice(nav.findIndex(x=>x[0]==='ai-models'),1);
settings=()=>{$('#app').innerHTML=heading('SETTINGS','系统设置','本地工作区信息。')+'<div class="panel"><h2>数据存储</h2><p>数据库和素材保存在本机。备份前请停止服务，然后复制整个 data 文件夹。</p></div>'};
let listingPollTimer=null,listingLoadId=0,listingFingerprint='';
function scheduleListingRefresh(){
 clearTimeout(listingPollTimer);
 if(state.page!=='selected')return;
 listingPollTimer=setTimeout(async()=>{
  if(state.page!=='selected')return;
  if(document.hidden||!$('#modal').classList.contains('hidden')||document.activeElement?.matches('input:not([type="checkbox"]),textarea,select')||listingDeleteBusy){scheduleListingRefresh();return}
  try{await library('product',true)}catch{scheduleListingRefresh()}
 },5000);
}
library=async(kind,background=false)=>{
 if(state.page!=='selected')return oldLibrary(kind);
 const requestId=++listingLoadId,page=state.listPage,query=state.q,category=listingCategoryParam();
 let[d,edits]=await Promise.all([api('/api/products?draft_filter=in&limit=24&page='+page+'&q='+encodeURIComponent(query)+(category?'&category='+category:'')),api('/api/listing-edits')]);
 if(state.page!=='selected'||requestId!==listingLoadId||state.listPage!==page||state.q!==query||listingCategoryParam()!==category)return;
 if(background&&(!$('#modal').classList.contains('hidden')||document.activeElement?.matches('input:not([type="checkbox"]),textarea,select')||listingDeleteBusy)){scheduleListingRefresh();return}
 const fingerprint=JSON.stringify([d,edits]);
 if(!background||fingerprint!==listingFingerprint){
  listingItems=d.items;listingTotal=d.total;listingCategoryGroups=d.category_groups||[];
  if(listingCategory.some(value=>!listingCategoryGroups.some(c=>c.category===value))){listingCategory=[];clearListingSelection();return library(kind)}
  if(state.listPage>Math.max(1,Math.ceil(d.total/24))){state.listPage=Math.max(1,Math.ceil(d.total/24));return library(kind)}
  listingEdits=new Map(edits.map(x=>[x.market+':'+x.id,x]));
  if(background){listingAllKeys=[];listingAllQuery=null}
  listingFingerprint=fingerprint;drawListing();
 }
 scheduleListingRefresh();
};
function drawListing(){
 let chosen=listingChosen.size,allSelected=listingAllQuery===listingFilterKey()&&listingAllKeys.length>0&&listingAllKeys.every(key=>listingChosen.has(key));
 $('#app').innerHTML=heading('COMPETITOR LIBRARY','竞品库','采集完成的商品会自动显示，管理商品资料并准备上架。')+`
 <div class="panel listing-tools">
  <div class="listing-toolbar"><div class="listing-search"><input class="search" placeholder="搜索商品名称、店铺或 ID" aria-label="搜索商品名称、店铺或 ID" value="${esc(state.q)}" onchange="state.q=this.value;state.listPage=1;render()"><span class="count">共 ${num(listingTotal)} 件</span></div>${listingCategoryControls()}</div>
  <div class="listing-actions"><span class="listing-selected">已选择 <strong>${chosen}</strong> 件</span><button class="btn listing-select" aria-pressed="${allSelected}" onclick="toggleAllListings()">${allSelected?'取消全选':'全选'}</button><button class="btn listing-delete" onclick="deleteListings()" ${chosen?'':'disabled'}>删除</button><button class="btn listing-sku-manage" onclick="openSkuManager()">SKU 管理</button><button class="btn listing-batch" onclick="batchPrice()" ${chosen?'':'disabled'}>一键改价</button><button class="btn listing-size-batch" onclick="batchSizeChart()" ${chosen?'':'disabled'}>批量上传尺码表</button><button class="btn listing-stock-batch" onclick="batchStock()" ${chosen?'':'disabled'}>批量更改库存</button><button class="btn primary listing-publish" onclick="publishListings()" ${chosen?'':'disabled'}>一键上架</button></div>
  <p>${listingCategory.length?'当前显示 '+listingCategory.length+' 个类目；全选将选择当前筛选的商品。':'选择类目后，可对同类商品批量改价、准备上架。'}</p>
 </div>
 <div class="listing-list">${listingItems.map(x=>{
  let key=x.market+':'+x.id,edit=listingEdits.get(key)||{},url='https://shop.tiktok.com/view/product/'+encodeURIComponent(x.id)+'?region='+encodeURIComponent(x.market),img=x.image||'';
  return `<article class="listing-row"><input type="checkbox" aria-label="选择 ${esc(x.name)}" ${listingChosen.has(key)?'checked':''} onchange="chooseListing('${esc(x.market)}','${esc(x.id)}',this.checked)"><div class="listing-image">${img?`<img src="${esc(img)}" alt="商品主图" onerror="this.hidden=true">`:'暂无主图'}</div><div class="listing-info">
   <strong title="${esc(x.name||'待补充商品名称')}">${esc(x.name||'待补充商品名称')}</strong>
   <div class="listing-price">页面售价 ${esc(x.price||'待补充')}　上架价格 ${x.listing_price?esc(x.listing_price):edit.price?'USD '+esc(edit.price):'待设置'}</div>
   <div class="listing-saved">已保存：${savedProductStatus(x.details)} <button class="listing-detail-link" type="button" onclick="showProductData('${esc(x.market)}','${esc(x.id)}')">商品详情</button></div>
   <div>${esc(x.shop_name||'店铺待补充')} · 商品 ID ${esc(x.id)} · ${esc(x.market)} · 类目 ${esc(x.listing_category||x.category||'未填写')}${x.category_message?' · '+esc(x.category_message):''}${x.collection_availability==='regional_unavailable'?' · 采集成功 · 本地区不售':''}</div>
   <a href="${url}" target="_blank" rel="noopener">查看 TikTok 商品链接</a>
   ${x.local_skus?`<details class="listing-sku-summary"><summary>本地 SKU${x.local_sku_count?` · ${x.local_sku_count} 个规格`:''}<span class="when-closed">展开</span><span class="when-open">收起</span></summary><div class="listing-sku-codes">${esc(x.local_skus)}</div></details>`:'<div>本地 SKU 待生成</div>'}
  </div><div class="listing-row-actions"><button class="btn" onclick="publishListings([{market:'${esc(x.market)}',id:'${esc(x.id)}'}])">一键上架</button></div></article>`;
 }).join('')||'<div class="panel empty">暂无商品</div>'}</div>
 <div class="pagination"><button class="btn small" onclick="state.listPage--;render()" ${state.listPage<=1?'disabled':''}>上一页</button><span>第 ${state.listPage} 页 / 共 ${Math.max(1,Math.ceil(listingTotal/24))} 页</span><button class="btn small" onclick="state.listPage++;render()" ${state.listPage*24>=listingTotal?'disabled':''}>下一页</button><form class="page-jump" onsubmit="jumpListingPage(event)" novalidate><label for="listing-jump-page">跳至</label><input id="listing-jump-page" type="number" inputmode="numeric" min="1" max="${Math.max(1,Math.ceil(listingTotal/24))}" step="1" value="${state.listPage}" aria-label="跳转页码"><span>页</span><button class="btn small" type="submit">跳转</button></form></div>`;
}
async function jumpListingPage(event){event.preventDefault();let input=document.getElementById('listing-jump-page'),page=Number(input.value),max=Math.max(1,Math.ceil(listingTotal/24));if(!Number.isInteger(page)||page<1||page>max){toast('请输入 1 到 '+max+' 之间的页码');input.focus();return}state.listPage=page;await render();document.querySelector('.listing-list')?.scrollIntoView({block:'start',behavior:'smooth'})}
function chooseListing(m,id,checked){let k=m+':'+id;checked?listingChosen.set(k,{market:m,id}):listingChosen.delete(k);drawListing()}
function toggleAllListings(){if(listingAllQuery===listingFilterKey() && listingAllKeys.length && listingAllKeys.every(key=>listingChosen.has(key))){listingChosen.clear();listingAllKeys=[];drawListing()}else selectAllListings()}
async function selectAllListings(){try{
 const filterKey=listingFilterKey(),query=state.q,category=listingCategoryParam();
 const url=page=>'/api/products?draft_filter=in&limit=100&page='+page+'&q='+encodeURIComponent(query)+(category?'&category='+category:'');
 let first=await api(url(1)),all=first.items;
 for(let page=2;page<=Math.ceil(first.total/100);page++){let d=await api(url(page));all.push(...d.items)}
 if(filterKey!==listingFilterKey())return;
 all.forEach(x=>listingChosen.set(x.market+':'+x.id,{market:x.market,id:x.id}));listingAllKeys=all.map(x=>x.market+':'+x.id);listingAllQuery=filterKey;drawListing();
}catch(e){toast('选择失败：'+e.message)}}
function listingUnavailable(){toast('店铺接口尚未接入，暂不能上架')}
async function saveListing(items){let r=await fetch('/api/listing-edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items})});let d=await r.json();if(!r.ok)throw Error(d.error);toast('已保存 '+d.changed+' 件商品；上架表单将使用此售价');closeModal();await render()}
let batchPriceState=null;
const batchPriceSizes=['XS','S','M','L','XL','XXL','3XL','4XL','5XL'];
function batchPriceSizeInputs(){return `<div class="batch-price-size-section"><strong>按尺码设置（可选）</strong><small>填写后会覆盖所选商品中对应尺码的上架价格，留空的尺码保持原价。</small><div class="batch-price-size-grid">${batchPriceSizes.map(size=>`<label>${size}<input class="search batch-size-price" id="batch-size-price-${size}" type="text" inputmode="decimal" placeholder="价格" autocomplete="off"></label>`).join('')}</div></div>`;}
function batchPriceSizeValues(){let values={};for(const size of batchPriceSizes){let input=$('#batch-size-price-'+size),value=input?.value.trim()||'';if(value)values[size]=value;}return values;}
function batchPrice(){
 const items=[...listingChosen.values()].map(x=>({...x}));if(!items.length)return toast('请先选择需要改价的商品');
 batchPriceState={items,saving:false};$('#modal').classList.remove('hidden');
 $('#modal').innerHTML=`<div class="drawer listing-price-dialog" role="dialog" aria-modal="true" aria-labelledby="batch-price-heading"><div class="drawer-top"><div><span class="eyebrow">BATCH PRICE</span><h2 id="batch-price-heading">批量设置上架价格</h2></div><button type="button" class="close" aria-label="关闭改价" onclick="if(!batchPriceState.saving)closeModal()">×</button></div><p>应用到所选 <strong>${items.length}</strong> 件商品的 SKU。</p><form id="batch-price-form" novalidate oninput="previewBatchPrice()" onsubmit="event.preventDefault();saveBatchPrice()"><label for="batch-price-base">统一售价（USD，可选）</label><input id="batch-price-base" class="search" type="text" inputmode="decimal" placeholder="例如：25.00，不填则只修改尺码价格" autocomplete="off"><label for="batch-price-discount">折扣（折）</label><input id="batch-price-discount" class="search" type="text" inputmode="decimal" value="10" autocomplete="off"><small>填写统一售价时生效，10 折为原价；8 折表示减价 20%。</small>${batchPriceSizeInputs()}<div class="batch-price-preview" aria-live="polite"><span>价格预览</span><strong id="batch-price-final">—</strong><small id="batch-price-formula">填写统一售价或尺码价格</small></div><p>尺码价格会按每个商品的 S、M、L、XL 等 SKU 规格匹配，未填写的尺码保持原价。</p><div id="batch-price-error" role="alert"></div><div class="listing-price-actions"><button type="button" class="btn" onclick="if(!batchPriceState.saving)closeModal()">取消</button><button id="batch-price-save" type="submit" class="btn primary" disabled>保存到 ${items.length} 件商品</button></div></form></div>`;
 $('#batch-price-base').focus();
}
function batchPriceCalculation(){
 const price=$('#batch-price-base').value.trim(),discount=$('#batch-price-discount').value.trim();
 const sizePrices=batchPriceSizeValues();
 if(!price&&!Object.keys(sizePrices).length)return {error:'请输入统一售价或至少一个尺码价格'};
 if(price&&(!/^\d+(?:\.\d{1,2})?$/.test(price)||Number(price)<=0||Number(price)>999999.99))return {error:'请输入大于 0 的售价，最多两位小数'};
 for(const value of Object.values(sizePrices))if(!/^\d+(?:\.\d{1,2})?$/.test(value)||Number(value)<=0||Number(value)>999999.99)return {error:'尺码价格须为大于 0 的金额，最多两位小数'};
 let final='',discountValue=10;
 if(price){if(!/^\d+(?:\.\d{1,2})?$/.test(discount)||Number(discount)<=0||Number(discount)>10)return {error:'折扣须大于 0 且不超过 10，例如 8 表示八折'};const cents=Math.round(Number(price)*100),rate=Math.round(Number(discount)*100),finalCents=Math.round(cents*rate/1000);if(finalCents<1)return {error:'折后售价不能低于 $0.01'};final=(finalCents/100).toFixed(2);discountValue=Number(discount);}
 return {price:price?(Number(price)).toFixed(2):'',discount:discountValue,final,sizePrices};
}
function previewBatchPrice(){
 const result=batchPriceCalculation();$('#batch-price-final').textContent=result.error?'—':result.final?'统一 $'+result.final+' · '+Object.keys(result.sizePrices).length+' 个尺码':'已填写 '+Object.keys(result.sizePrices).length+' 个尺码价格';
 $('#batch-price-formula').textContent=result.error?'请输入统一售价或尺码价格':result.final?`统一价 $${result.price} × ${result.discount} 折 = $${result.final}`:'将按尺码价格保存';
 $('#batch-price-error').textContent=result.error?result.error:'';
 $('#batch-price-save').disabled=!!result.error||batchPriceState.saving;
 return result;
}
async function saveBatchPrice(){
 const session=batchPriceState;if(!session||session.saving)return;
 const result=previewBatchPrice();if(result.error)return;
 session.saving=true;$('#batch-price-save').disabled=true;$('#batch-price-save').textContent='正在保存…';
 try{
  const response=await fetch('/api/listing-edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items:session.items.map(x=>({...x,price:result.final,size_prices:result.sizePrices}))})});
  const data=await response.json();if(!response.ok)throw Error(data.error||'改价失败');
  closeModal();toast(`已更新 ${data.changed} 件商品的 ${data.sku_count||0} 个尺码 SKU${result.final?'，统一价为 $'+result.final:''}`);await render();
 }catch(error){$('#batch-price-error').textContent=error.message;$('#batch-price-save').disabled=false;$('#batch-price-save').textContent='重新保存';}
 finally{session.saving=false;}
}

// app.js renders once all scripts have loaded (DOMContentLoaded).

function savedProductStatus(c){if(!c)return '暂无商品资料';let variants=c.quality?.variants||[];return `图片 ${c.saved_images||0} 张 · SKU ${variants.length} 个 · 价格 ${variants.length?(variants.every(v=>v.price!==null)?'已保存':'部分缺失'):Object.keys(c.prices||{}).length?'已保存':'未填写'} · 页面销量 ${esc(c.sales?.display||'未知')}`}

function productDataVariantGroups(variants){
 const groups=new Map();
 for(const v of variants){
  const attrs=v.attributes||[],color=attrs.find(a=>/^(colou?r|颜色|色彩)$/i.test(a.name))||attrs.find(a=>/^(style|款式)$/i.test(a.name));
  const key=color?.value||'全部规格';if(!groups.has(key))groups.set(key,[]);
  groups.get(key).push({...v,displayAttributes:attrs.filter(a=>a!==color)});
 }
 return [...groups];
}
function productDataSpecs(q){
 const variants=q?.variants||[];if(!variants.length)return '<div class="product-data-empty">暂未采集到规格资料</div>';
 const groups=productDataVariantGroups(variants);
 return `<div class="product-data-section-head"><h3>颜色与规格</h3><div class="product-data-section-actions"><span>${groups.length} 组 · ${variants.length} 个 SKU</span><button class="product-data-fold" type="button" aria-expanded="true" aria-controls="product-data-specs" onclick="toggleProductDataSection(this)">收起 <span aria-hidden="true">⌃</span></button></div></div><div id="product-data-specs" class="product-data-specs">${groups.map(([color,rows],index)=>`<details class="product-data-group" ${index===0?'open':''}><summary><strong>${esc(color)}</strong><span>${rows.length} 个规格 <i aria-hidden="true">⌄</i></span></summary><div class="product-data-table"><table class="table"><thead><tr><th>规格</th><th>售价</th><th>原价</th></tr></thead><tbody>${rows.map(v=>`<tr><td><strong>${esc(v.displayAttributes.map(a=>a.value).join(' / ')||'默认规格')}</strong><details class="product-data-sku"><summary>SKU 编号</summary><small>${esc(v.id)}</small></details></td><td>${esc(v.price!=null?v.currency+' '+v.price:'价格缺失')}</td><td>${esc(v.original_price!=null?v.currency+' '+v.original_price:'—')}</td></tr>`).join('')}</tbody></table></div></details>`).join('')}</div>`;
}
function toggleProductDataSection(button){
 const content=document.getElementById(button.getAttribute('aria-controls'));if(!content)return;
 const expanded=button.getAttribute('aria-expanded')==='true';content.hidden=expanded;button.setAttribute('aria-expanded',String(!expanded));button.innerHTML=expanded?'展开 <span aria-hidden="true">⌄</span>':'收起 <span aria-hidden="true">⌃</span>';
}
function productDataInfo(c){
 const lines=[];
 function append(value){
  if(typeof value==='string'&&value.trim())lines.push(value.trim());
  else if(Array.isArray(value))value.forEach(append);
  else if(value&&typeof value==='object')append(value.text||value.content);
 }
 for(const block of c.description_blocks||[])if(block.type!=='image')append(block.text||block.content);
 const sales=c.sales||{},specs=c.specifications||[];
 return `<section class="product-data-photos"><div class="product-data-section-head"><h3>页面销量</h3><span>${esc(sales.display||'未知')}</span></div><p class="product-data-note">${esc(sales.display?(sales.note||'页面未说明销量统计范围和周期。'):'商品页未返回销量，无法确认。')}</p></section><section class="product-data-photos"><div class="product-data-section-head"><h3>商品详情与属性</h3></div>${lines.map(line=>`<p>${esc(line)}</p>`).join('')||'<p>页面未返回文字详情，可查看下方详情图。</p>'}${specs.length?`<div class="product-data-table"><table class="table"><thead><tr><th>属性</th><th>内容</th></tr></thead><tbody>${specs.map(item=>`<tr><td>${esc(item.name)}</td><td>${esc(item.value)}</td></tr>`).join('')}</tbody></table></div>`:''}</section>`;
}
async function showProductData(m,id){
 const x=listingItems.find(i=>i.market===m&&i.id===id),c=x?.details||{};
 let gallery;try{gallery=(await api('/api/gallery?items='+encodeURIComponent(JSON.stringify([{market:m,id}])))).items[0]}catch{}
 const images=gallery?.images||[],variants=c.quality?.variants||[];
 $('#modal').classList.remove('hidden');
 $('#modal').innerHTML=`<div class="drawer product-data-drawer" role="dialog" aria-modal="true" aria-labelledby="product-data-heading"><div class="drawer-top"><div><span class="eyebrow">PRODUCT DETAILS</span><h2 id="product-data-heading">商品详情</h2></div><button class="close product-data-close" type="button" aria-label="关闭商品详情" title="关闭" onclick="closeModal()">${uiIcon('close')}</button></div><div class="product-data-body"><h3 class="product-data-title">${esc(x?.name||id)}</h3><div class="product-data-badges"><span>${images.length} 张图片</span><span>${variants.length} 个 SKU</span><span>来源 ${esc(m)}</span></div><p class="product-data-note">价格按采集时的来源币种展示</p><section>${productDataSpecs(c.quality)}</section>${productDataInfo(c)}<section class="product-data-photos"><div class="product-data-section-head"><h3>商品图组</h3><div class="product-data-section-actions">${images.some(i=>i.local_path)?`<a class="btn small" href="/api/gallery/download?market=${encodeURIComponent(m)}&id=${encodeURIComponent(id)}">下载图组</a>`:`<span>${images.length} 张</span>`}<button class="product-data-fold" type="button" aria-expanded="true" aria-controls="product-data-gallery" onclick="toggleProductDataSection(this)">收起 <span aria-hidden="true">⌃</span></button></div></div><div id="product-data-gallery" class="product-data-gallery">${images.map((i,n)=>`<a href="${esc(i.local_path||i.url)}" target="_blank" rel="noopener" aria-label="查看${esc(i.group_name||'商品图')} ${n+1}"><img src="${esc(i.local_path||i.url)}" loading="lazy" alt="${esc(i.group_name||'商品图')}"><span>${esc(i.group_name||'商品图')} ${n+1}</span></a>`).join('')||'<p>暂无已采集图片</p>'}</div></section><details class="product-data-raw"><summary class="product-data-section-head"><h3>原始规格与价格数据</h3><span class="product-data-fold"><span class="when-closed">展开 ⌄</span><span class="when-open">收起 ⌃</span></span></summary><h3>SKU / 规格</h3><pre>${esc(Object.keys(c.skus||{}).length?JSON.stringify(c.skus,null,2):'暂未获取')}</pre><h3>价格</h3><pre>${esc(Object.keys(c.prices||{}).length?JSON.stringify(c.prices,null,2):'暂未获取')}</pre></details></div></div>`;
 $('.product-data-close').focus();
}

function variantTable(q){if(!q?.variants?.length)return '';return '<h2>颜色、规格与价格（'+q.variants.length+' 个 SKU）</h2><div style="overflow-x:auto"><table><thead><tr><th>SKU</th><th>颜色 / 规格</th><th>售价</th><th>原价</th></tr></thead><tbody>'+q.variants.map(v=>'<tr><td>'+esc(v.id)+'</td><td>'+v.attributes.map(a=>esc(a.name)+': '+esc(a.value)).join('<br>')+'</td><td>'+esc(v.price!==null?v.currency+' '+v.price:'价格缺失')+'</td><td>'+esc(v.original_price?v.currency+' '+v.original_price:'—')+'</td></tr>').join('')+'</tbody></table></div>'}

let listingDeleteBusy=false;
function clearListingSelection(){listingChosen.clear();listingAllKeys=[];listingAllQuery=null}
async function deleteListings(){
 if(listingDeleteBusy||!listingChosen.size)return;
 const items=[...listingChosen.values()];
 if(!confirm(`确定删除选中的 ${items.length} 件商品？（包含跨页、跨搜索选择的商品）\n\n商品会移入“设置 → 数据恢复 → 已删除商品”，图片、SKU 和编辑信息会保留，可随时恢复。`))return;
 listingDeleteBusy=true;
 const button=document.querySelector('.listing-delete');if(button)button.disabled=true;
 try{
  const r=await fetch('/api/listings/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items})});
  const d=await r.json();if(!r.ok)throw Error(d.error);
  clearListingSelection();toast('已删除 '+d.changed+' 件商品，可在设置 → 数据恢复中恢复');await render();
 }catch(e){toast('删除失败：'+e.message)}finally{listingDeleteBusy=false;const b=document.querySelector('.listing-delete');if(b)b.disabled=!listingChosen.size}
}

let batchSizeState=null;
function batchSizeChart(){
 const items=[...listingChosen.values()].map(x=>({...x}));if(!items.length)return toast('请先选择商品');
 batchSizeState={items,busy:false};$('#modal').classList.remove('hidden');
 $('#modal').innerHTML=`<div class="drawer listing-price-dialog" role="dialog" aria-modal="true" aria-labelledby="batch-size-heading"><div class="drawer-top"><h2 id="batch-size-heading">批量上传尺码表</h2><button class="close" type="button" aria-label="关闭批量上传" onclick="closeModal()">×</button></div><p>选择一张本地尺码表，上传后直接替换所选 <strong>${items.length}</strong> 件商品的尺码表。</p><label>尺码表适用类型<select class="search" id="batch-size-type"><option value="">其他 / 类型未标注</option><option value="tshirt">T 恤</option><option value="hoodie">卫衣</option><option value="swimwear">泳装</option><option value="shapewear">塑身衣 / 束腰</option></select></label><label><input type="checkbox" id="batch-size-confirm">已核实此表适用于所选全部商品的款式、尺码和单位</label><label class="publish-upload">上传本地尺码表<input id="batch-size-file" type="file" accept="image/jpeg,image/png" onchange="uploadBatchSize(this)"><small>JPG / PNG，最大 8 MB，宽高 1024～4000 像素。</small></label><div id="batch-size-status" role="status"></div><img id="batch-size-preview" class="publish-size-preview" alt="批量上传的尺码表" hidden><div class="listing-price-actions"><button class="btn" id="batch-size-done" type="button" onclick="closeModal()">关闭</button></div></div>`;
}
async function uploadBatchSize(input){
 const session=batchSizeState,file=input.files[0];
 const active=()=>batchSizeState===session&&!$('#modal').classList.contains('hidden')&&$('#modal').contains(input);
 if(!session||!file||session.busy||!active())return;
 if(!$('#batch-size-confirm').checked){input.value='';return toast('请先核实尺码表适用于所选全部商品，并勾选确认')}
 session.busy=true;input.disabled=true;
 const status=$('#batch-size-status');status.textContent='正在读取尺码表…';
 try{
  if(!['image/jpeg','image/png'].includes(file.type)||file.size>8*1024*1024)throw Error('请选择最大 8 MB 的 JPG 或 PNG 图片');
  const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(Error('读取图片失败'));r.readAsDataURL(file)});
  if(!active())return;
  const image=await new Promise((resolve,reject)=>{const im=new Image();im.onload=()=>resolve(im);im.onerror=()=>reject(Error('图片无法解码'));im.src=data});
  if(!active())return;
  if(Math.min(image.naturalWidth,image.naturalHeight)<1024||Math.max(image.naturalWidth,image.naturalHeight)>4000)throw Error('尺码表宽高须在 1024～4000 像素之间');
  status.textContent=`正在替换 ${session.items.length} 件商品的尺码表…`;
  const result=await shopPost('listing-size-batch',{items:session.items,name:file.name,data,product_type:$('#batch-size-type').value});
  if(!active())return;
  status.textContent=`已成功替换 ${result.changed} 件商品的尺码表，上架时自动使用。`;
  const preview=$('#batch-size-preview');preview.src=result.image;preview.hidden=false;
  $('#batch-size-done').textContent='完成';
 }catch(error){if(active())status.textContent='上传失败：'+error.message;}
 finally{session.busy=false;input.disabled=false;input.value='';}
}
