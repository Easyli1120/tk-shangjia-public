// Cross-product ledger and an inspectable trail of actual stored work.
const visualRecords={page:1,q:'',state:'',expanded:null,request:0,searchTimer:null};
async function visualRecordsPage(){
 const rev=vision.revision;
 const modes=await api('/api/visual/modes');
 if(state.page!=='visual-records'||rev!==vision.revision)return;
 vision.modes=modes;
 $('#app').innerHTML=heading('VISUAL ANALYSIS RECORDS','视觉分析记录','查看做过视觉分析的商品，追踪图组、分析、Prompt、生图与成品保存的完整流程。',`<button class="btn primary" onclick="go('innovation')">返回热款创新</button>`)+`
 <div id="visual-record-summary" class="visual-stats"></div>
 <div class="toolbar"><input id="visual-record-search" class="search" aria-label="搜索分析商品" placeholder="搜索商品名称、ID 或市场" value="${esc(visualRecords.q)}">
 <select id="visual-record-state" aria-label="筛选分析状态"><option value="">全部状态</option>${['queued','running','done','failed','interrupted'].map(s=>`<option value="${s}" ${visualRecords.state===s?'selected':''}>${visualLabels[s]}</option>`).join('')}</select><button class="btn" onclick="loadVisualRecords()">刷新记录</button><span class="count">包含正式分析与标记的连接测试</span></div>
 <section class="panel visual-record-list"><div id="visual-record-list" aria-live="polite">正在读取分析记录…</div></section>
 <section id="visual-record-flow" class="panel section" hidden></section>`;
 $('#visual-record-search').oninput=e=>{visualRecords.q=e.target.value;visualRecords.page=1;visualRecords.expanded=null;clearTimeout(visualRecords.searchTimer);visualRecords.searchTimer=setTimeout(loadVisualRecords,250)};
 $('#visual-record-search').onkeydown=e=>{if(e.key==='Enter'){clearTimeout(visualRecords.searchTimer);loadVisualRecords()}};
 $('#visual-record-state').onchange=e=>{visualRecords.state=e.target.value;visualRecords.page=1;visualRecords.expanded=null;loadVisualRecords()};
 await loadVisualRecords();
}
async function loadVisualRecords(){
 if(state.page!=='visual-records')return;
 visualRecords.q=$('#visual-record-search').value;visualRecords.state=$('#visual-record-state').value;
 clearTimeout(vision.timer);
 const rev=vision.revision,request=++visualRecords.request;
 try{
  const d=await api('/api/visual/records?'+new URLSearchParams({page:visualRecords.page,q:visualRecords.q,state:visualRecords.state}));
  if(state.page!=='visual-records'||rev!==vision.revision||request!==visualRecords.request)return;
  visualRecords.page=d.page;
  $('#visual-record-summary').innerHTML=[['涉及商品',d.summary.products],['分析记录',d.summary.tasks],['分析完成',d.summary.completed],['排队 / 分析中',d.summary.active],['失败 / 中断',d.summary.failed]].map(([label,n])=>`<span><small>${label}</small><b>${num(n)}</b></span>`).join('');
  $('#visual-record-list').innerHTML=d.items.length?`<div class="visual-table-scroll"><table class="table visual-ledger"><thead><tr><th>商品 / 分析时间</th><th>模式 / 类型</th><th>分析状态</th><th>流程进度</th><th>操作</th></tr></thead><tbody>${d.items.map(j=>`<tr><td><b>${esc(j.product_name)}</b><small>${esc(j.market)} · ${esc(j.product_id)}</small><small>${esc(j.created_at)}</small></td><td>${esc(vision.modes[j.mode]?.name||j.mode)}<small>${j.kind==='connection'?'连接测试':'正式分析'}${j.cached_from?' · 使用缓存':''}</small></td><td><span class="visual-record-status ${esc(j.state)}">${esc(visualLabels[j.state]||j.state)}</span><small>${j.progress} / ${j.image_count} 张 · ${j.batches_done} / ${j.batch_count} 批</small>${j.error?`<small class="visual-record-error">${esc(j.error)}</small>`:''}</td><td><div class="visual-flow-mini">${visualFlowPill('图组',j.image_count>0)}${visualFlowPill('报告',j.report_ready)}${visualFlowPill('Prompt',j.prompt_count>0)}${visualFlowPill('生图',j.generated_images>0)}${visualFlowPill('成品',j.saved_mockups>0)}</div><small>${j.prompt_count} 个 Prompt 版本 · ${j.generation_count} 个生图任务${j.generation_active?` · ${j.generation_active} 个进行中`:``}</small></td><td><button class="btn small" onclick="visualRecordFlow('${j.id}',true)">查看流程</button><button class="creative-link" onclick="visualRecordOpen('${j.id}')">打开分析</button></td></tr>`).join('')}</tbody></table></div><div class="pagination"><button class="btn small" ${d.page<=1?'disabled':''} onclick="visualRecordsPageBy(-1)">上一页</button><span class="count">第 ${d.page} / ${Math.max(1,Math.ceil(d.total/d.limit))} 页 · ${d.total} 条</span><button class="btn small" ${d.page*d.limit>=d.total?'disabled':''} onclick="visualRecordsPageBy(1)">下一页</button></div>`:`<div class="empty"><strong>${visualRecords.q||visualRecords.state?'没有符合条件的分析记录':'还没有视觉分析记录'}</strong><p>${visualRecords.q||visualRecords.state?'调整搜索词或状态筛选后重试。':'在热款创新选择商品并提交视觉分析后，这里会显示真实任务与流程。'}</p><button class="btn" onclick="go('innovation')">前往热款创新</button></div>`;
  if(visualRecords.expanded&&d.items.some(j=>j.id===visualRecords.expanded))await visualRecordFlow(visualRecords.expanded);
  else{$('#visual-record-flow').hidden=true;visualRecords.expanded=null}
  if(state.page==='visual-records'&&rev===vision.revision&&request===visualRecords.request&&(d.summary.active||d.items.some(j=>j.generation_active)))vision.timer=setTimeout(loadVisualRecords,5000);
 }catch(e){if(state.page==='visual-records'&&rev===vision.revision)toast('读取分析记录失败：'+e.message)}
}
function visualFlowPill(label,complete){return `<span class="${complete?'complete':''}">${label}${complete?' ✓':''}</span>`}
function visualRecordsPageBy(delta){visualRecords.page+=delta;visualRecords.expanded=null;loadVisualRecords()}
function visualFlowStep(label,status,detail,complete=false){return `<li class="${complete?'complete':''}"><div><b>${label}</b><span>${esc(status)}</span></div><p>${esc(detail)}</p></li>`}
async function visualRecordFlow(id,scroll=false){
 const rev=vision.revision;visualRecords.expanded=id;
 try{
  const j=await api('/api/visual/job?id='+encodeURIComponent(id));
  const generations=await Promise.all(j.generations.map(g=>api('/api/innovation/job?id='+encodeURIComponent(g.generation_id))));
  if(state.page!=='visual-records'||rev!==vision.revision||visualRecords.expanded!==id)return;
  const images=generations.flatMap(g=>g.images),saved=images.filter(i=>i.image_type==='mockup'&&i.saved_at),prints=images.filter(i=>i.image_type==='design');
  const active=generations.filter(g=>['queued','running','retry_wait'].includes(g.state)).length,failed=generations.filter(g=>['failed','interrupted'].includes(g.state)).length;
  const generationStatus=active?active+' 个进行中':failed?failed+' 个失败':generations.length&&generations.every(g=>g.state==='done')?'已完成':generations.length?'已提交 '+generations.length+' 个任务':'尚未提交';
  const el=$('#visual-record-flow');el.hidden=false;
  el.innerHTML=`<div class="visual-progress-head"><div><div class="visual-kicker">WORKFLOW / ${esc(j.id)}</div><h2>${esc(j.product_name)}</h2></div><button class="btn" onclick="visualRecordOpen('${j.id}')">打开分析工作区</button></div><p>${esc(j.market)} · ${esc(j.product_id)} · ${esc(vision.modes[j.mode]?.name||j.mode)}${j.kind==='connection'?' · 连接测试':''}<br>创建 ${esc(j.created_at)} · 更新 ${esc(j.updated_at)}</p>
  <ol class="visual-flow-steps">
  ${visualFlowStep('1. 商品图组','已记录',`${j.image_count} 张输入图片快照，按 ${j.batch_size} 张分批`,true)}
  ${visualFlowStep('2. 视觉分析',visualLabels[j.state]||j.state,`${j.progress} / ${j.image_count} 张 · ${j.batches.filter(b=>b.state==='done').length} / ${j.batches.length} 批完成${j.cached_from?' · 复用缓存报告':''}`,j.state==='done')}
  ${visualFlowStep('3. 分析报告',j.result?'已生成':'尚未生成',j.result?'视觉规律、元素拆解与原创设计建议已保存':'分析完成后生成结构化报告',!!j.result)}
  ${visualFlowStep('4. Prompt 版本',j.prompt_versions.length?'已保存':'尚未保存',`${j.prompt_versions.length} 个版本；可在分析工作区查看、编辑与对比`,j.prompt_versions.length>0)}
  ${visualFlowStep('5. 图片生成',generationStatus,`已生成 ${images.length} 张图片，其中独立印花 ${prints.length} 张`,images.length>0)}
  ${visualFlowStep('6. 成品保存',saved.length?'已保存':'尚未保存',`已收藏 ${saved.length} 张 Mockup 到成品库；独立印花生成后自动保留`,saved.length>0)}
  </ol>${j.error?`<p class="visual-warning">分析错误：${esc(j.error)}</p>`:''}
  ${j.batches.length?`<h3>分析批次</h3><div class="visual-batch-list">${j.batches.map(b=>`<div>第 ${b.position+1} 批 · ${esc(visualLabels[b.state]||({pending:'待处理'})[b.state]||b.state)}<small>图片 ID：${esc(JSON.parse(b.image_ids).join('、'))}</small>${b.error?`<p class="visual-warning">${esc(b.error)}</p>`:''}</div>`).join('')}</div>`:''}
  ${generations.length?`<h3>生图任务</h3>${generations.map(g=>`<div class="visual-generated-job"><b>${esc(g.provider)}</b> · ${esc(({running:'生成中'})[g.state]||visualLabels[g.state]||g.state)} · ${esc(g.created_at)}<small>${g.images.length} 张结果 · ${g.images.filter(i=>i.saved_at&&i.image_type==='mockup').length} 张 Mockup 已收藏</small>${g.error?`<p class="visual-warning">${esc(g.error)}</p>`:''}</div>`).join('')}`:''}`;
  if(scroll)el.scrollIntoView({behavior:'smooth',block:'start'});
 }catch(e){if(state.page==='visual-records'&&rev===vision.revision)toast('读取流程失败：'+e.message)}
}
async function visualRecordOpen(id){
 try{
  const j=await api('/api/visual/job?id='+encodeURIComponent(id));
  if(state.page!=='visual-records')return;
  vision.product={market:j.market,id:j.product_id};vision.job=null;vision.currentPrompt=null;vision.parentImageId=null;vision.task='design';
  await go('visual');
  if(state.page==='visual'&&vision.product?.id===j.product_id&&vision.product?.market===j.market){await visualOpenJob(id);visualDrawHistory()}
 }catch(e){toast('打开分析失败：'+e.message)}
}
