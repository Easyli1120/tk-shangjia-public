// Local product edits are staged until Save. Source records remain unchanged.
let productEditor=null;
const closeBeforeProductEditor=closeModal;
closeModal=function(){
  if(productEditor?.saving)return;
  if(productEditor?.dirty&&!confirm('尚有未保存的修改，确定关闭？'))return;
  productEditor=null;
  closeBeforeProductEditor();
};

async function editListing(m,id){
  try{
    let d=await api('/api/listing-editor?market='+encodeURIComponent(m)+'&id='+encodeURIComponent(id));
    productEditor={...d,ruleChanges:new Map(),dirty:false,saving:false,images:d.images.map(i=>({...i,changed:false})),variants:d.variants.map(v=>({...v,changed:false}))};
    $('#modal').classList.remove('hidden');
    $('#modal').innerHTML=`<div class="drawer product-editor" role="dialog" aria-modal="true" aria-labelledby="editor-heading"><form id="product-edit-form" onsubmit="saveProductEditor(event)"><div class="drawer-top"><div><span class="eyebrow">PRODUCT EDITOR · ${esc(d.id)}</span><h2 id="editor-heading">编辑商品</h2></div><button class="close" type="button" aria-label="关闭编辑" onclick="closeModal()">×</button></div><p class="editor-intro">修改保存在本机；原始资料会保留，你的修改单独保存。</p><section class="editor-section"><label class="editor-label" for="edit-title">商品名称 / 标题</label><textarea id="edit-title" rows="3" maxlength="2000" required oninput="productEditor.dirty=true">${esc(d.title)}</textarea></section><section class="editor-section"><div class="editor-section-head"><h2>商品规格与价格</h2><span>页面售价 ${esc(d.source_price||'暂未保存')}</span></div><label class="editor-label" for="edit-price">统一上架价格（USD，可留空使用来源价格）</label><div class="editor-price-tools"><input id="edit-price" class="search" type="number" min="0.01" max="999999999.99" step="0.01" placeholder="使用来源价格" value="${esc(d.price)}" oninput="productEditor.dirty=true"><button class="btn" type="button" onclick="applyEditorPrice()" ${d.variants.length?'':'disabled'}>应用到保留的 SKU</button></div><p>单独填写的 SKU 价格优先；清空后使用统一价格或来源价格。</p><div id="editor-variants"></div></section><section class="editor-section"><div class="editor-section-head"><h2>商品图片 <span id="editor-image-count"></span></h2><button class="btn" type="button" onclick="pickEditorImage(-1)">上传图片</button></div><p>逐张删除或从本地更换。第一张保留的图片作为主图；删除后可恢复。支持 JPG、PNG、WebP、GIF，每张最大 8 MB。</p><input id="editor-file" type="file" accept="image/jpeg,image/png,image/webp,image/gif" hidden onchange="readEditorImage(this)"><div id="editor-images" class="editor-images"></div></section><div id="editor-error" class="editor-error" role="alert"></div><div class="editor-footer"><span>保存后应用到竞品库及图组下载</span><button class="btn" type="button" onclick="closeModal()">取消</button><button id="editor-save" class="btn primary" type="submit">保存修改</button></div></form></div>`;
    drawEditorVariants();drawEditorImages();
  }catch(e){toast('打开编辑失败：'+e.message)}
}

function drawEditorImages(){
  let e=productEditor;if(!e)return;
  $('#editor-image-count').textContent='· '+e.images.filter(i=>!i.hidden).length+' 张';
  let main=e.images.findIndex(i=>!i.hidden);
  $('#editor-images').innerHTML=e.images.map((i,n)=>`<article class="editor-image ${i.hidden?'is-deleted':''}"><button type="button" class="editor-image-preview" onclick="previewEditorImage(${n})" aria-label="查看第 ${n+1} 张商品图片大图"><img src="${esc(i.data||i.local_path||i.url)}" alt="${esc(i.group_name||'商品图')} ${n+1}" loading="lazy"><span>${i.hidden?'已删除':n===main?'主图':esc(i.group_name||'商品图')}</span></button><div class="editor-image-actions">${i.hidden?`<button class="btn small" type="button" onclick="toggleEditorImage(${n})">恢复图片</button>`:`<button class="btn small" type="button" onclick="pickEditorImage(${n})">本地更换</button><button class="btn small editor-delete" type="button" onclick="toggleEditorImage(${n})">删除</button>`}</div></article>`).join('')||'<div class="editor-empty">暂无图片，可本地上传或可导入商品资料。</div>';
}
function previewEditorImage(n){
  const item=productEditor?.images[n];if(!item)return;
  const dialog=document.createElement('dialog');dialog.className='image-lightbox';dialog.setAttribute('aria-label','商品图片大图');
  const img=document.createElement('img');img.src=item.data||item.local_path||item.url;img.alt=(item.group_name||'商品图')+' '+(n+1);
  const close=document.createElement('button');close.type='button';close.className='image-lightbox-close';close.textContent='×';close.setAttribute('aria-label','关闭大图');close.onclick=()=>dialog.close();
  dialog.append(img,close);document.body.append(dialog);dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close()});dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();
}
function toggleEditorImage(n){if(productEditor.saving)return;let i=productEditor.images[n];i.hidden=!i.hidden;i.changed=true;productEditor.dirty=true;drawEditorImages()}
function pickEditorImage(n){if(productEditor.saving)return;productEditor.fileIndex=n;$('#editor-file').value='';$('#editor-file').click()}
async function readEditorImage(input){
  let file=input.files[0],editor=productEditor,index=editor?.fileIndex;if(!file||!editor)return;
  try{
    if(!['image/jpeg','image/png','image/webp','image/gif'].includes(file.type))throw Error('请选择 JPG、PNG、WebP 或 GIF 图片');
    if(file.size>8*1024*1024)throw Error('每张图片最大 8 MB');
    let data=await new Promise((resolve,reject)=>{let reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('本地图片读取失败'));reader.readAsDataURL(file)});
    await new Promise((resolve,reject)=>{let image=new Image();image.onload=()=>image.naturalWidth*image.naturalHeight<=100000000&&image.naturalWidth<=30000&&image.naturalHeight<=30000?resolve():reject(Error('图片尺寸过大'));image.onerror=()=>reject(Error('图片无法解码'));image.src=data});
    if(editor!==productEditor||editor.saving)return;
    if(index<0)editor.images.push({key:'',add:true,hidden:false,group_name:'本地上传',data,changed:true});
    else Object.assign(editor.images[index],{data,hidden:false,changed:true});
    editor.dirty=true;drawEditorImages();$('#editor-error').textContent='';
  }catch(e){if(editor===productEditor)$('#editor-error').textContent=e.message}
}
function setEditorSku(n,value){let e=productEditor;e.variants[n].edit_price=value;e.variants[n].changed=true;e.dirty=true}
function applyEditorPrice(){
  let input=$('#edit-price');if(!input.value||!input.reportValidity())return;
  productEditor.variants.forEach((v,n)=>{if(!editorSkuHidden(v))setEditorSku(n,input.value)});drawEditorVariants();
}
async function saveProductEditor(event){
  event.preventDefault();let e=productEditor;if(!e||e.saving)return;
  if(!$('#edit-title').value.trim()){$('#editor-error').textContent='请输入商品名称';$('#edit-title').focus();return}
  let body={market:e.market,id:e.id,revision:e.revision,title:$('#edit-title').value,price:$('#edit-price').value,exclusions:[...e.ruleChanges.values()],skus:e.variants.filter(v=>v.changed).map(v=>({id:v.id,price:v.edit_price})),images:e.images.filter(i=>i.changed&&!(i.add&&i.hidden)).map(i=>({key:i.key,add:!!i.add,hidden:i.hidden,...(i.data?{data:i.data}:{})}))};
  let json=JSON.stringify(body);if(new Blob([json]).size>32*1024*1024){$('#editor-error').textContent='本次图片总量过大，请减少图片修改后分次保存（单次请求最多 32 MB）';return}
  e.saving=true;$('#editor-error').textContent='';$('#editor-save').textContent='正在保存…';$('#product-edit-form').querySelectorAll('button,input,textarea').forEach(el=>el.disabled=true);
  try{
    let response=await fetch('/api/listing-editor',{method:'POST',headers:{'Content-Type':'application/json'},body:json}),result=await response.json();if(!response.ok)throw Error(result.error||'保存失败');
    e.dirty=false;e.saving=false;closeModal();toast('商品修改已保存');await render();
  }catch(error){e.saving=false;$('#editor-error').textContent=error.message;$('#editor-save').textContent='保存修改';$('#product-edit-form').querySelectorAll('button,input,textarea').forEach(el=>el.disabled=false)}
}

function editorSkuHidden(v){return v.excluded||productEditor.option_groups.some(g=>g.options.some(o=>o.hidden&&v.option_keys.includes(o.key)))}
function setEditorExclusion(kind,key,hidden){
  if(productEditor.saving)return;
  productEditor.ruleChanges.set(kind+':'+key,{kind,key,hidden});productEditor.dirty=true;
  drawEditorVariants();
}
function toggleEditorOption(g,n){let o=productEditor.option_groups[g].options[n];o.hidden=!o.hidden;setEditorExclusion('option',o.key,o.hidden)}
function toggleEditorVariant(n){let v=productEditor.variants[n];v.excluded=!v.excluded;setEditorExclusion('sku',v.id,v.excluded)}
function previewVariantImage(g,n){
  let o=productEditor.option_groups[g].options[n];if(!o.image)return;
  const dialog=document.createElement('dialog');dialog.className='image-lightbox';dialog.setAttribute('aria-label',o.value+' 大图');
  const img=document.createElement('img');img.src=o.image;img.alt=o.value;
  const close=document.createElement('button');close.type='button';close.className='image-lightbox-close';close.textContent='×';close.setAttribute('aria-label','关闭大图');close.onclick=()=>dialog.close();
  dialog.append(img,close);document.body.append(dialog);dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close()});dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();
}
function drawEditorVariants(){
  let e=productEditor,active=e.variants.filter(v=>!editorSkuHidden(v)).length;
  $('#editor-variants').innerHTML=`<div class="variant-summary"><strong>上架规格 · 保留 ${active} / ${e.variants.length} 个 SKU</strong><span>删除颜色或尺码，将排除所有对应 SKU；保存后生效，可随时恢复。</span></div>${e.option_groups.map((g,gi)=>`<div class="variant-group"><h3>${g.has_image?'颜色 / 款式':'规格选项'} <small>来源字段：${esc(g.name)}</small></h3><div class="variant-options">${g.options.map((o,oi)=>`<article class="variant-option ${o.hidden?'is-deleted':''} ${o.image?'with-image':''}">${o.image?`<button type="button" class="variant-picture" onclick="previewVariantImage(${gi},${oi})" aria-label="查看 ${esc(o.value)} 大图"><img src="${esc(o.image)}" alt="${esc(o.value)}" loading="lazy"></button>`:''}<strong>${esc(o.value)}</strong><small>${o.sku_ids.length} 个 SKU${o.hidden?' · 已删除':''}</small><button type="button" class="btn small ${o.hidden?'':'editor-delete'}" onclick="toggleEditorOption(${gi},${oi})" aria-label="${o.hidden?'恢复':'删除'} ${esc(o.value)} 全部规格">${o.hidden?'恢复选项':'删除选项'}</button></article>`).join('')}</div></div>`).join('')}${!active?'<p class="editor-error">当前没有可上架的 SKU，请恢复至少一个规格组合后再上架。</p>':''}${e.variants.length?`<div class="editor-skus"><table class="table"><thead><tr><th>SKU / 颜色规格</th><th>来源价格</th><th>上架价格</th><th>操作</th></tr></thead><tbody>${e.variants.map((v,n)=>{let hidden=editorSkuHidden(v);return `<tr class="${hidden?'sku-excluded':''}"><td><strong>${esc(v.attributes.map(a=>a.name+': '+a.value).join(' · ')||'默认规格')}</strong><small>${esc(v.id)}${hidden?' · 不参与上架':''}</small></td><td>${esc(v.currency)} ${esc(v.source_price??'缺失')}</td><td><label class="sr-only" for="sku-price-${n}">SKU ${esc(v.id)} 的上架价格</label><input id="sku-price-${n}" type="number" min="0.01" max="999999999.99" step="0.01" value="${esc(v.edit_price)}" placeholder="${esc($('#edit-price').value||v.source_price||'待补充')}" oninput="setEditorSku(${n},this.value)" ${hidden?'disabled':''}></td><td>${hidden&&!v.excluded?'<small>恢复上方选项后启用</small>':`<button type="button" class="btn small ${v.excluded?'':'editor-delete'}" onclick="toggleEditorVariant(${n})">${v.excluded?'恢复 SKU':'删除 SKU'}</button>`}</td></tr>`}).join('')}</tbody></table></div>`:'<div class="editor-empty">暂无已保存的 SKU，保存完成后可编辑规格。</div>'}`;
}
