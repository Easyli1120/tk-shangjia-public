// Shop category choices are loaded from TikTok for the currently connected shop.
let batchCategoryState=null,batchStockState=null;
function batchDialogActive(session,kind){return (kind==='category'?batchCategoryState:batchStockState)===session&&!$('#modal').classList.contains('hidden')&&!!document.getElementById('batch-'+kind+'-dialog');}
async function batchCategory(){
 const items=[...listingChosen.values()].map(x=>({...x}));
 if(!items.length)return toast('请先勾选商品');
 if(items.length>100)return toast('每批最多更改 100 件商品的类目');
 const s=batchCategoryState={items,path:[],selected:'',busy:false};
 $('#modal').classList.remove('hidden');
 $('#modal').innerHTML=`<div class="drawer batch-category-dialog" id="batch-category-dialog" role="dialog" aria-modal="true" aria-labelledby="batch-category-heading"><div class="drawer-top"><h2 id="batch-category-heading">批量更改商品类目</h2><button class="close" aria-label="关闭批量类目" onclick="closeModal()">×</button></div><p>为已勾选的 <strong>${items.length}</strong> 件商品选择上架类目。</p><div id="batch-category-body"><p role="status">正在读取店铺可售类目…</p></div></div>`;
 try{
  const data=await shopPost('listing-category-options',{});if(!batchDialogActive(s,'category'))return;
  s.shopId=data.shop_id;s.nodes=new Map(data.categories.map(c=>[String(c.id),{...c,id:String(c.id),parent_id:String(c.parent_id||'0')}]));
  const allowed=new Set();s.leaves=[];
  for(const c of s.nodes.values())if(c.is_leaf&&(c.permission_statuses||[]).includes('AVAILABLE')){
   s.leaves.push(c);let n=c;const seen=new Set();
   while(n&&!seen.has(n.id)){seen.add(n.id);allowed.add(n.id);n=s.nodes.get(n.parent_id);}
  }
  s.children=new Map();for(const c of s.nodes.values())if(allowed.has(c.id)){
   const parent=s.nodes.has(c.parent_id)?c.parent_id:'0';if(!s.children.has(parent))s.children.set(parent,[]);s.children.get(parent).push(c);
  }
  s.pathFor=id=>{const path=[],seen=new Set();let n=s.nodes.get(id);while(n&&!seen.has(n.id)){seen.add(n.id);path.unshift(n);n=s.nodes.get(n.parent_id);}return path;};
  s.paths=new Map(s.leaves.map(c=>[c.id,s.pathFor(c.id).map(n=>n.local_name).join(' > ')]));
  $('#batch-category-body').innerHTML=`<div class="batch-category-shop">${esc(data.shop_name||data.shop_id)} · 本店可售类目</div><input class="search" id="batch-category-search" type="search" aria-label="搜索商品类目" placeholder="搜索类目名称，如：连帽衫 / Hoodies" oninput="drawBatchCategories(this.value)"><div id="batch-category-tree"></div><div class="batch-category-selection" id="batch-category-selection" role="status">请选择末级类目</div><p class="batch-category-note">保存后，上架将沿用此类目，并按新类目更新自动包装资料。竞品库将按所选末级类目显示。</p><div class="editor-error" id="batch-category-error" role="status"></div><div class="listing-price-actions"><button class="btn" type="button" onclick="closeModal()">取消</button><button class="btn primary" id="batch-category-save" type="button" disabled onclick="saveBatchCategory()">应用到 ${items.length} 件商品</button></div>`;
  drawBatchCategories('');
 }catch(e){if(batchDialogActive(s,'category'))$('#batch-category-body').innerHTML=`<p class="editor-error" role="status">${esc(e.message)}</p><button class="btn" onclick="batchCategory()">重新读取</button>`;}
}
function drawBatchCategories(query=''){
 const s=batchCategoryState,q=query.trim().toLowerCase();
 const option=c=>`<button type="button" class="batch-category-option ${s.path.includes(c.id)?'is-active':''}" aria-pressed="${s.selected===c.id}" onclick="pickBatchCategory('${esc(c.id)}')"><span>${esc(c.local_name)}</span><span aria-hidden="true">${c.is_leaf?(s.selected===c.id?'✓':''):'›'}</span></button>`;
 if(q){
  const matches=s.leaves.filter(c=>(s.paths.get(c.id)+' '+s.pathFor(c.id).map(n=>n.english_name||'').join(' ')).toLowerCase().includes(q));
  $('#batch-category-tree').innerHTML=`<div class="batch-category-results">${matches.slice(0,100).map(c=>`<button type="button" class="batch-category-option ${s.selected===c.id?'is-active':''}" aria-pressed="${s.selected===c.id}" onclick="pickBatchCategory('${esc(c.id)}')"><span>${esc(s.paths.get(c.id))}</span><span aria-hidden="true">${s.selected===c.id?'✓':''}</span></button>`).join('')||'<p>未找到可售类目，请换个关键词。</p>'}${matches.length>100?'<p>结果超过 100 项，请输入更具体的名称。</p>':''}</div>`;
 }else{
  const parents=['0',...s.path.filter(id=>!s.nodes.get(id).is_leaf)];
  $('#batch-category-tree').innerHTML=`<div class="batch-category-columns">${parents.map((id,i)=>`<div class="batch-category-column" role="group" aria-label="第 ${i+1} 级类目">${(s.children.get(id)||[]).map(option).join('')||'<p>暂无可售类目</p>'}</div>`).join('')}</div>`;
 }
}
function pickBatchCategory(id){
 const s=batchCategoryState;if(s.busy)return;
 s.path=s.pathFor(id).map(c=>c.id);s.selected=s.nodes.get(id).is_leaf?id:'';
 drawBatchCategories($('#batch-category-search').value);
 $('#batch-category-selection').textContent=(s.selected?'已选择：':'继续选择：')+s.pathFor(id).map(c=>c.local_name).join(' > ');
 $('#batch-category-save').disabled=!s.selected;
}
async function saveBatchCategory(){
 const s=batchCategoryState;if(s.busy||!s.selected)return;
 s.busy=true;$('#batch-category-save').disabled=true;$('#batch-category-save').textContent='正在应用…';$('#batch-category-error').textContent='';
 try{
  const result=await shopPost('listing-category-batch',{items:s.items,shop_id:s.shopId,category_id:s.selected});
  if(!batchDialogActive(s,'category'))return;
  $('#batch-category-body').innerHTML=`<div class="batch-edit-success" role="status"><strong>已更改 ${result.count} 件商品的上架类目</strong><p>${esc(result.category_path)}</p></div><div class="listing-price-actions"><button class="btn primary" onclick="closeModal()">完成</button></div>`;
 }catch(e){if(batchDialogActive(s,'category')){$('#batch-category-error').textContent=e.message;$('#batch-category-save').disabled=false;$('#batch-category-save').textContent='重新应用';}}finally{s.busy=false;}
}
function batchStock(){
 const items=[...listingChosen.values()].map(x=>({...x}));if(!items.length)return toast('请先勾选商品');
 if(items.length>100)return toast('每批最多更改 100 件商品的库存');
 batchStockState={items,busy:false};$('#modal').classList.remove('hidden');
 $('#modal').innerHTML=`<div class="drawer listing-price-dialog" id="batch-stock-dialog" role="dialog" aria-modal="true" aria-labelledby="batch-stock-heading"><div class="drawer-top"><h2 id="batch-stock-heading">批量更改库存</h2><button class="close" aria-label="关闭批量库存" onclick="closeModal()">×</button></div><p>将已勾选的 <strong>${items.length}</strong> 件商品的全部 SKU，统一设置为以下库存。包含当前库存为 0 的规格。</p><form onsubmit="event.preventDefault();saveBatchStock()"><label for="batch-stock-value">每个 SKU 的库存</label><input class="search" id="batch-stock-value" type="number" min="1" max="99999" step="1" required placeholder="请输入实际可售数量"><small>填写 1–99999 的整数，保存后用于后续上架。</small><div class="editor-error" id="batch-stock-error" role="status"></div><div class="listing-price-actions"><button class="btn" type="button" onclick="closeModal()">取消</button><button class="btn primary" id="batch-stock-save" type="submit">应用到全部 SKU</button></div></form></div>`;
}
async function saveBatchStock(){
 const s=batchStockState;if(s.busy)return;
 const stock=$('#batch-stock-value').value;
 if(!/^\d+$/.test(stock)||+stock<1||+stock>99999){$('#batch-stock-error').textContent='请填写 1–99999 的整数';return;}
 s.busy=true;$('#batch-stock-save').disabled=true;$('#batch-stock-save').textContent='正在应用…';
 try{
  const result=await shopPost('listing-stock-batch',{items:s.items,stock});if(!batchDialogActive(s,'stock'))return;
  $('#batch-stock-dialog form').innerHTML=`<div class="batch-edit-success" role="status"><strong>已更新 ${result.count} 件商品 · ${result.sku_count} 个 SKU</strong><p>每个 SKU 的库存：${result.stock}</p></div><div class="listing-price-actions"><button class="btn primary" type="button" onclick="closeModal()">完成</button></div>`;
 }catch(e){if(batchDialogActive(s,'stock')){$('#batch-stock-error').textContent=e.message;$('#batch-stock-save').disabled=false;$('#batch-stock-save').textContent='重新应用';}}finally{s.busy=false;}
}
