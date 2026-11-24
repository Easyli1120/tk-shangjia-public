// Durable server jobs; this page only displays progress and issues commands.
let collectionTimer=null,collectionBusy=false,collectionJob=null,collectionPage=1,collectionFilter='',collectionRevision=0,collectionDetailsOpen=false;
let collectionDraft='',collectionRequest='';
const collectionDeleting=new Set();
try{collectionDraft=localStorage.getItem('tiktok-link-draft')||'';collectionRequest=localStorage.getItem('tiktok-link-request')||''}catch{}
const collectionLabels={pending:'待采集',running:'采集中',retry:'等待重试',complete:'采集完成',partial:'资料不全',failed:'采集失败',cancelled:'已停止',finished:'处理结束',paused:'已暂停'};
function stopCollectionPolling(){clearTimeout(collectionTimer);collectionTimer=null;collectionRevision++}
function collectionForm(){return `<section class="panel collection-panel"><div class="panelhead"><div><span class="eyebrow">TIKTOK SHOP</span><h2>批量链接采集</h2><p>点击开始后，自动逐条采集商品图、规格图、详情图、尺码表、商品信息、销量和 SKU，无需等待类目识别。页面未提供的销量显示为未知。</p></div></div><label class="collection-label" for="tiktok-links">商品链接</label><textarea id="tiktok-links" rows="4" spellcheck="false" placeholder="https://shop.tiktok.com/view/product/…?region=US&local=en&#10;每行一条，也可用空格分隔" oninput="collectionInput()">${esc(collectionDraft)}</textarea><div class="collection-form-footer"><div><span id="collection-link-count">0 条链接</span></div><button id="collection-start" class="btn primary" onclick="startCollection()">${uiIcon('plus')} 开始采集</button></div><div id="collection-form-error" role="alert"></div></section>`}
function collectionTasks(){return `<details id="collection-history" class="section panel import-history link-import-history"><summary><h2>采集进度 <small id="collection-history-count">—</small></h2><span class="history-fold"><span class="when-closed">展开</span><span class="when-open">收起</span> ⌄</span></summary><div class="link-history-content"><section id="collection-jobs" aria-label="商品采集任务"><p>正在读取采集进度…</p></section><section id="collection-items" class="section"></section></div></details>`}
function collectionInput(reset=true){
 const input=$('#tiktok-links');if(!input)return;
 collectionDraft=input.value;
 if(reset){collectionRequest='';try{localStorage.removeItem('tiktok-link-request')}catch{}}
 try{localStorage.setItem('tiktok-link-draft',collectionDraft)}catch{}
 const count=collectionDraft.trim()?collectionDraft.trim().split(/\s+/).length:0;
 $('#collection-link-count').textContent=`${count.toLocaleString()} 条链接`;
 $('#collection-link-count').classList.toggle('collection-error',count>3000);
 $('#collection-start').disabled=collectionBusy||!count||count>3000;
}
async function collectionPost(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||'操作失败');return d}
async function startCollection(){
 if(collectionBusy)return;const input=$('#tiktok-links');if(!input)return;
 const links=input.value;
 collectionBusy=true;collectionInput(false);input.disabled=true;$('#collection-start').textContent='正在创建任务…';$('#collection-form-error').textContent='';
 if(!collectionRequest)collectionRequest=crypto.randomUUID();
 try{localStorage.setItem('tiktok-link-request',collectionRequest)}catch{}
 try{
  const result=await collectionPost('/api/tiktok/jobs',{links,request_key:collectionRequest});
  collectionJob=result.id;collectionPage=1;collectionDetailsOpen=true;collectionDraft='';collectionRequest='';
  try{localStorage.removeItem('tiktok-link-draft');localStorage.removeItem('tiktok-link-request')}catch{}
  toast(`任务 #${result.id} 已开始${result.duplicates?'，已去除 '+result.duplicates+' 条重复链接':''}`);
  if(state.page==='upload'){input.value='';$('#collection-history').open=true;await pollCollection()}
 }catch(e){if($('#collection-form-error'))$('#collection-form-error').textContent=e.message;else toast(e.message)}
 finally{collectionBusy=false;if($('#tiktok-links')){$('#tiktok-links').disabled=false;$('#collection-start').textContent='开始采集';collectionInput(false)}}
}
function collectionCard(j){
 const c=j.counts,unavailable=j.regional_unavailable||0,need=(c.partial||0)+(c.failed||0),percent=Math.round(j.processed/j.total*100),current=j.current;
 const status=current?.stage?.includes('验证码')?'等待人工验证':j.state==='finished'&&need?'处理结束 · 有待补采':j.state==='finished'&&unavailable?'处理结束 · 有本地区不售商品':collectionLabels[j.state]||j.state;
 return `<article class="panel collection-job ${collectionJob===j.id?'selected':''}"><div class="collection-job-top"><div><strong>采集任务 #${j.id}</strong><span class="collection-status ${need||unavailable?'attention':''}">${esc(status)}</span><small>${esc(j.created_at.replace('T',' ').slice(0,19))} · ${j.total} 条${j.source_filename?' · 来源：'+esc(j.source_filename):''}${j.duplicates?' · 去重 '+j.duplicates+' 条':''}</small></div><div class="collection-actions">${j.state==='running'?`<button class="btn small" onclick="collectionControl(${j.id},'pause')">暂停</button>`:''}${j.state==='paused'?`<button class="btn small" onclick="collectionControl(${j.id},'resume')">继续采集</button>`:''}${['running','paused'].includes(j.state)?`<button class="btn small" onclick="collectionControl(${j.id},'cancel')">停止任务</button>`:''}${need||(c.cancelled||0)?`<button class="btn small" onclick="collectionControl(${j.id},'retry')">补采未完成</button>`:''}<button class="btn small" onclick="selectCollection(${j.id})">${collectionDetailsOpen&&collectionJob===j.id?'收起明细':'查看明细'}</button><button type="button" class="btn small collection-delete" onclick="deleteCollection(${j.id},this)" ${collectionDeleting.has(j.id)?'disabled':''}>${collectionDeleting.has(j.id)?'正在删除…':'删除本次任务'}</button></div></div><div class="collection-progress" role="progressbar" aria-label="任务处理进度" aria-valuenow="${j.processed}" aria-valuemin="0" aria-valuemax="${j.total}"><div style="width:${percent}%"></div></div><div class="collection-counts"><span>已处理 <b>${j.processed} / ${j.total}</b></span><span class="collection-success">资料已保存 <b>${(c.complete||0)-unavailable}</b></span>${unavailable?`<span>本地区不售 <b>${unavailable}</b>（未取得资料）</span>`:''}<span class="${need?'collection-error':''}">待补采 <b>${need}</b></span>${c.cancelled?`<span>已停止 ${c.cancelled}</span>`:''}</div>${current||['running','paused'].includes(j.state)?`<p class="collection-current">${current?`第 ${current.position} 条 · ${esc(current.stage)} · ${current.discovered?'图组已保存 '+current.cached+' / '+current.discovered+' 张':'等待商品页返回图组'}${current.title?' · '+esc(current.title):''}`:j.state==='paused'?'已暂停；当前商品处理结束后不再开始下一条。':j.state==='running'?(c.retry&&!c.pending?'正在重新采集未完成商品。':'后台队列持续处理，已有任务执行时按顺序等待。'):j.state==='finished'?(need?'部分链接未采集完整，可查看原因并补采。':'本次页面返回的商品资料和图组已保存，可在竞品库查看。'):'任务已停止，已保存的商品资料保留。'}</p>`:''}</article>`
}
async function pollCollection(){
 clearTimeout(collectionTimer);const revision=++collectionRevision;
 if(state.page!=='upload'||!$('#collection-jobs'))return;
 try{
  const d=await api('/api/tiktok/jobs');if(revision!==collectionRevision||state.page!=='upload')return;
  const active=d.jobs.find(j=>['running','paused'].includes(j.state));
  if(!collectionJob&&d.jobs.length){collectionJob=active?.id||d.jobs[0].id;if(active)collectionDetailsOpen=true}
  if(active||collectionDetailsOpen)$('#collection-history').open=true;
  $('#collection-history-count').textContent=d.jobs.length;
  $('#collection-jobs').innerHTML=`${d.worker_error?`<p class="collection-error" role="alert">后台状态：${esc(d.worker_error)}；任务已保留，正在恢复。</p>`:''}${collectionBrowserNotice(d)}${d.jobs.map(collectionCard).join('')||'<div class="panel empty">暂无采集任务。上传商品文件后会自动采集，也可粘贴商品链接开始采集。</div>'}`;
  if(collectionJob&&collectionDetailsOpen)await drawCollectionItems(revision);else $('#collection-items').innerHTML='';
 }catch(e){if(revision===collectionRevision&&$('#collection-jobs'))$('#collection-jobs').innerHTML=`<div class="panel collection-error">暂时无法读取进度：${esc(e.message)}。正在重新连接，已提交的任务保留在后台。</div>`}
 finally{if(revision===collectionRevision&&state.page==='upload')collectionTimer=setTimeout(pollCollection,2500)}
}
async function selectCollection(id){collectionDetailsOpen=collectionJob!==id||!collectionDetailsOpen;collectionJob=id;collectionPage=1;collectionRevision++;await pollCollection();if(collectionDetailsOpen)$('#collection-items')?.scrollIntoView({behavior:'smooth',block:'start'})}
async function collectionControl(id,action){
 try{await collectionPost('/api/tiktok/control',{id,action});collectionRevision++;await pollCollection()}catch(e){toast(e.message)}
}
async function deleteCollection(id,button){
 if(collectionDeleting.has(id))return;
 if(!confirm(`删除采集任务 #${id}？\n本任务关联的商品也会从竞品库移除（包含重复采集的同一商品），可在“数据恢复”中恢复商品。\n正在进行的采集会停止；已发布到店铺的商品不受影响。`))return;
 collectionDeleting.add(id);stopCollectionPolling();
 if(button){button.disabled=true;button.textContent='正在删除…'}
 try{
  const result=await collectionPost('/api/tiktok/control',{id,action:'delete'});
  if(collectionJob===id){collectionJob=null;collectionDetailsOpen=false;collectionPage=1;collectionFilter=''}
  clearListingSelection();
  toast(`已删除任务 #${id}，并从竞品库移除 ${result.changed} 件商品`);
 }catch(e){toast('删除失败：'+e.message)}
 finally{collectionDeleting.delete(id);if(state.page==='upload')await pollCollection()}
}
async function drawCollectionItems(revision){
 const jid=collectionJob,page=collectionPage,filter=collectionFilter;
 const d=await api(`/api/tiktok/items?id=${jid}&page=${page}&state=${encodeURIComponent(filter)}`);
 if(revision!==collectionRevision||jid!==collectionJob||!$('#collection-items'))return;
 $('#collection-items').innerHTML=`<div class="panel"><div class="collection-job-top"><h2>任务 #${jid} 明细</h2><div class="collection-actions"><select aria-label="筛选采集状态" onchange="collectionFilter=this.value;collectionPage=1;collectionRevision++;pollCollection()">${[['','全部'],['incomplete','失败 / 资料不全 / 重试'],['complete','采集完成'],['cancelled','已停止']].map(([v,t])=>`<option value="${v}" ${filter===v?'selected':''}>${t}</option>`).join('')}</select><a class="btn small" href="/api/tiktok/export?id=${jid}">导出未完成链接</a></div></div><div class="collection-table-wrap"><table class="table collection-table"><thead><tr><th>序号 / 商品</th><th>状态</th><th>图片（已保存 / 已发现）</th><th>尝试</th><th>进度 / 缺失原因</th></tr></thead><tbody>${d.items.map(x=>`<tr><td><a href="${esc(x.url)}" target="_blank" rel="noopener">${x.position}. ${esc(x.title||x.product_id||'TikTok 短链接')}</a></td><td><span class="${x.state==='complete'?'collection-success':['partial','failed'].includes(x.state)?'collection-error':''}">${esc(x.error_code==='regional_unavailable'?'本地区不售 · 未取得资料':collectionLabels[x.state]||x.state)}</span></td><td>${x.cached} / ${x.discovered}</td><td>${x.attempts} / 3</td><td>${esc(['running','pending'].includes(x.state)?x.stage:x.error||x.stage||'等待开始')}</td></tr>`).join('')||'<tr><td colspan="5">当前筛选下暂无链接</td></tr>'}</tbody></table></div><div class="pagination"><button class="btn small" ${page<=1?'disabled':''} onclick="collectionPage--;collectionRevision++;pollCollection()">上一页</button><span>${page} / ${Math.max(1,Math.ceil(d.total/50))} 页 · ${d.total} 条</span><button class="btn small" ${page*50>=d.total?'disabled':''} onclick="collectionPage++;collectionRevision++;pollCollection()">下一页</button></div></div>`;
}

function collectionBrowserNotice(d){
 if(d.browser_state==='verification')return `<div class="panel collection-browser" role="status"><strong>等待你完成验证码 · 通过后自动继续</strong><p>请在采集程序正在使用的 Chrome 窗口中完成验证。程序保持同一个浏览器、同一个标签页和会话，不会重启或跳转其他商品；验证通过后会自动保存当前商品，再继续下一条。不要在普通浏览器验证，也无需关闭窗口或点击继续。</p><button class="btn primary" onclick="collectionBrowser('open')">显示采集窗口</button></div>`;
 if(d.browser_state==='collecting'||d.browser_state==='ready')return `<div class="panel collection-browser"><strong>采集与验证共用同一会话</strong><p>同一个 Chrome 窗口逐条采集，保存后直接打开下一条。遇到验证码时，队列停在当前商品等你完成验证。请保留采集窗口；可以切回其他窗口工作。</p><button class="btn" onclick="collectionBrowser('open')">显示采集窗口</button></div>`;
 return '';
}
async function collectionBrowser(action){try{await collectionPost('/api/tiktok/browser',{action});await pollCollection()}catch(e){toast(e.message)}}
