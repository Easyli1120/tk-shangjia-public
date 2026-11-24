// Dedicated publishing workspace. Only persisted platform results are shown.
const publishingView={items:[],filter:'all',query:'',request:0,timer:null};
iconPaths.publishing=iconPaths.tasks;
const publishingGroups={pending:['submitted'],active:['active'],attention:['failed','rejected','uncertain','frozen'],other:['queued','running','scheduled','draft','inactive','deleted']};
const publishingFilters=[['all','全部记录'],['pending','审核中'],['active','在售'],['attention','需要处理'],['other','其他状态']];
function stopPublishJobsPolling(){clearTimeout(publishingView.timer);publishingView.request++;}
function publishingTime(value){return value?new Date(Number(value)*1000).toLocaleString('zh-CN',{hour12:false}):'—'}
function publishingMatches(j,filter){if(filter==='all')return true;if(filter==='pending')return j.state==='submitted'&&j.remote_status==='PENDING';if(filter==='other'&&j.state==='submitted'&&j.remote_status!=='PENDING')return true;return (publishingGroups[filter]||[]).includes(j.state)}
function publishingTone(j){return j.state==='active'?'success':publishingGroups.attention.includes(j.state)?'danger':publishingGroups.pending.includes(j.state)?'pending':'neutral'}
async function publishJobsPage(){
 $('#app').innerHTML=heading('PUBLISHING','商品发布记录','查看提交结果与已保存的店铺状态。')+`
 <section class="publishing-page publishing-compact" aria-label="商品发布记录">
  <div class="publishing-stats" id="publishing-stats"></div>
  <div class="panel publishing-results">
   <div class="publishing-tools"><div class="publishing-tabs" id="publishing-tabs" aria-label="按发布状态筛选"></div><input id="publishing-search" class="search" type="search" aria-label="搜索发布记录" placeholder="搜索商品名称、商品 ID 或店铺 ID" value="${esc(publishingView.query)}" oninput="publishingView.query=this.value;drawPublishJobRows()"></div>
   <div class="publishing-meta"><span id="publishing-count">正在读取发布记录…</span></div>
   <div class="publishing-error" id="publishing-error" role="status"></div>
   <div id="publishing-rows" aria-live="polite"></div>
   <div class="publishing-note">提交成功后仍需平台审核；只有平台状态为“在售”才计入在售商品。显示最近 200 条提交记录及已保存的状态。</div>
  </div>
 </section>`;
 await loadPublishJobs();
}
function drawPublishJobFilters(){
 const v=publishingView,count=f=>v.items.filter(j=>publishingMatches(j,f)).length;
 $('#publishing-stats').innerHTML=[['all','提交发布记录','已保存的提交记录'],['pending','审核中','平台确认正在审核'],['active','在售','平台已确认在售'],['attention','需要处理','失败、驳回或结果待核对']].map(([key,label,note])=>`<button class="publishing-stat ${v.filter===key?'selected':''}" type="button" data-job-filter="${key}" aria-pressed="${v.filter===key}"><span>${label}</span><strong>${count(key)}</strong><small>${note}</small></button>`).join('');
 $('#publishing-tabs').innerHTML=publishingFilters.map(([key,label])=>`<button type="button" class="publishing-tab ${v.filter===key?'selected':''}" data-job-filter="${key}" aria-pressed="${v.filter===key}">${label}<span>${count(key)}</span></button>`).join('');
 document.querySelectorAll('[data-job-filter]').forEach(b=>b.onclick=()=>{v.filter=b.dataset.jobFilter;drawPublishJobFilters();drawPublishJobRows()});
}
function drawPublishJobRows(){
 if(state.page!=='publishing'||!$('#publishing-rows'))return;
 const v=publishingView,q=v.query.trim().toLowerCase();
 const items=v.items.filter(j=>publishingMatches(j,v.filter)&&(!q||[j.title,j.product_id,j.remote_id,j.shop_id].some(x=>String(x||'').toLowerCase().includes(q))));
 $('#publishing-count').textContent=`共 ${v.items.length} 条记录 · 当前显示 ${items.length} 条`;
 $('#publishing-rows').innerHTML=items.length?`<div class="publishing-table-head"><span>商品</span><span>状态</span><span>最近更新</span></div>`+items.map(j=>`<article class="publishing-record">
  <div class="publishing-product"><strong title="${esc(j.title)}">${esc(j.title)}</strong><div class="publishing-product-meta"><span>${esc(j.market)} · 来源 ${esc(j.product_id)}</span>${j.remote_id?`<span class="publishing-remote">平台 ID ${esc(j.remote_id)}</span>`:''}</div><details class="publishing-detail" ${publishingGroups.attention.includes(j.state)?'open':''}><summary>发布详情</summary><div class="publishing-detail-meta">目标店铺 ${esc(j.shop_id)} · 首次提交 ${esc(publishingTime(j.created_at))}</div><p>${esc(j.message||'暂未收到平台说明')}</p>${j.remote_status?`<small>平台原始状态：${esc(j.remote_status)}</small>`:''}${j.state==='uncertain'?'<p>请先在商家后台核对是否已创建商品。系统会保留记录，避免重复创建。</p>':''}</details></div>
  <div><span class="publishing-status ${publishingTone(j)}">${esc(j.state==='submitted'?(j.remote_status==='PENDING'?'审核中':'已提交 · 等待状态确认'):publishLabels[j.state]||j.state)}</span></div>
  <div class="publishing-time">${esc(publishingTime(j.updated_at))}</div>
 </article>`).join(''):`<div class="empty publishing-empty"><span class="empty-icon">${uiIcon('publishing')}</span><strong>${v.items.length?'没有符合条件的记录':'还没有发布记录'}</strong><p>${v.items.length?'试试其他状态或搜索关键词。':'从竞品库选择商品并提交上架后，进度和结果会显示在这里。'}</p><button class="btn" onclick="${v.items.length?'resetPublishJobFilters()':"go('selected')"}">${v.items.length?'清除筛选':'前往竞品库'}</button></div>`;
}
function resetPublishJobFilters(){publishingView.filter='all';publishingView.query='';$('#publishing-search').value='';drawPublishJobFilters();drawPublishJobRows()}
async function loadPublishJobs(){
 clearTimeout(publishingView.timer);
 const request=++publishingView.request;
 try{
  const r=await api('/api/shop/listing-jobs');
  if(state.page!=='publishing'||request!==publishingView.request)return;
  publishingView.items=r.items||[];
  $('#publishing-error').textContent='';
  drawPublishJobFilters();drawPublishJobRows();
  if(state.page==='publishing'&&publishingView.items.some(x=>['queued','running'].includes(x.state)))publishingView.timer=setTimeout(()=>{if(state.page==='publishing')loadPublishJobs()},3000);
 }catch(e){if(state.page==='publishing'&&request===publishingView.request)$('#publishing-error').textContent='读取失败：'+e.message;}
}
