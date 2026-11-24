// Preparing images is a background task; closing this view only detaches the UI.
let publishPreparation=null;
const preparationStorageKey='listing-preparation';
function preparationOpen(v){return publishPreparation===v&&v.visible&&!$('#modal').classList.contains('hidden')&&!!$('#publish-preparation')}
function rememberPreparation(v){try{sessionStorage.setItem(preparationStorageKey,JSON.stringify({id:v.id,items:v.items}))}catch{}}
function preparationResumeButton(){
 let button=$('#publish-preparation-resume');
 if(!button){button=document.createElement('button');button.id='publish-preparation-resume';button.className='btn primary publish-preparation-resume';button.textContent='查看上架准备';button.onclick=()=>resumePublishPreparation();document.body.append(button)}
 button.hidden=!!(!publishPreparation||publishPreparation.visible||publishPreparation.entered&&publishPreparation.status?.state!=='running');
}
function detachPreparation(){
 const v=publishPreparation;if(!v)return;
 v.visible=false;clearTimeout(v.timer);preparationResumeButton();
}
const closeBeforePreparation=closeModal;
closeModal=function(){closeBeforePreparation();if($('#modal').classList.contains('hidden'))detachPreparation()};
const goBeforePreparation=go;
go=function(page){if(publishPreparation?.visible){closeModal()}return goBeforePreparation(page)};

async function preparationRequest(action,body){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
 try{
  const r=await fetch('/api/shop/'+action,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal}:{signal:controller.signal});
  const data=await r.json();if(!r.ok)throw Error(data.error||'准备请求失败');return data;
 }catch(e){throw Error(e.name==='AbortError'?'连接超时，后台任务可能仍在处理；请重新读取进度':e.message)}
 finally{clearTimeout(timer)}
}
function drawPreparation(v){
 if(publishPreparation!==v||!v.visible)return;
 const s=v.status||{},rows=s.rows||[],total=s.total||v.items.length,done=(s.completed||0)+(s.failed||0);
 const terminal=['ready','partial','cancelled'].includes(s.state),busy=v.requesting||!terminal&&!v.error;
 const labels={pending:'等待准备',running:'处理中',ready:'已准备',failed:'失败',cancelled:'已停止'};
 $('#modal').innerHTML=`<div class="drawer publish-preparation" id="publish-preparation" role="dialog" aria-modal="true" aria-labelledby="prepare-heading">
 <div class="drawer-top"><div><h2 id="prepare-heading">正在准备上架资料</h2><p>关闭窗口后仍会继续处理，可随时回来查看。</p></div><button class="close" type="button" aria-label="关闭准备窗口" onclick="closeModal()">×</button></div>
 <div class="publish-preparation-progress" role="status" aria-live="polite"><strong>${done} / ${total} 件已处理</strong><span>已准备 ${s.completed||0} 件 · 失败 / 停止 ${s.failed||0} 件 · 已用 ${s.elapsed||0} 秒</span><progress max="${total}" value="${done}"></progress><p>${esc(v.requesting?'正在连接后台任务…':rows.find(r=>r.state==='running')?.stage||({queued:'等待后台处理',ready:'全部资料准备完成',partial:'部分商品需要处理',cancelled:'准备任务已停止'})[s.state]||'正在读取进度…')}</p></div>
 ${v.error||s.error?`<div class="editor-error" role="alert">${esc(v.error||s.error)}</div>`:''}
 <div class="publish-preparation-rows">${rows.map((r,i)=>`<article class="publish-preparation-row"><strong>${i+1}. ${esc(r.title||r.id)}</strong><span>${labels[r.state]||'等待准备'} · ${esc(r.stage)}</span>${r.error?`<p class="editor-error">${esc(r.error)}</p>`:''}${r.issues?.length?`<ul>${r.issues.map(x=>`<li>图片 ${esc(x.image_key)}：${esc(x.message)}</li>`).join('')}</ul>`:''}</article>`).join('')}</div>
 <div class="editor-footer"><button class="btn" type="button" onclick="closeModal()">后台继续，关闭窗口</button>${v.error?`<button class="btn" type="button" onclick="resumePublishPreparation()" ${v.requesting?'disabled':''}>重新读取进度</button><button class="btn" type="button" onclick="restartPublishPreparation()" ${v.requesting?'disabled':''}>重新准备</button>`:''}${terminal&&s.failed?`<button class="btn" type="button" onclick="retryPublishPreparation()" ${busy?'disabled':''}>重试失败项</button>`:''}${s.completed?`<button class="btn primary" type="button" onclick="enterPreparedListings()" ${v.requesting?'disabled':''}>进入已准备的 ${s.completed} 件商品</button>`:''}</div></div>`;
}
async function startPublishPreparation(items,onReady=null){
 const same=publishPreparation&&!publishPreparation.entered&&JSON.stringify(publishPreparation.items)===JSON.stringify(items);
 if(same){if(onReady)publishPreparation.onReady=onReady;return resumePublishPreparation()}
 if(publishPreparation)clearTimeout(publishPreparation.timer);
 const v={items:items.map(x=>({market:x.market,id:x.id})),visible:true,requesting:true,onReady};publishPreparation=v;
 $('#modal').classList.remove('hidden');drawPreparation(v);preparationResumeButton();
 try{
  const status=await preparationRequest('listing-prepare-start',{items:v.items});v.id=status.id;v.status=status;if(publishPreparation===v)rememberPreparation(v);
 }catch(e){v.error=e.message}
 finally{v.requesting=false;if(preparationOpen(v)){drawPreparation(v);if(v.id&&!v.error)pollPreparation(v)}}
}
async function resumePublishPreparation(){
 const v=publishPreparation;if(!v)return;
 v.visible=true;$('#modal').classList.remove('hidden');v.error='';drawPreparation(v);preparationResumeButton();
 if(v.requesting)return;
 if(!v.id)return restartPublishPreparation();
 return pollPreparation(v);
}
function restartPublishPreparation(){const v=publishPreparation;if(!v)return;clearTimeout(v.timer);publishPreparation=null;return startPublishPreparation(v.items,v.onReady)}
async function pollPreparation(v){
 clearTimeout(v.timer);if(!preparationOpen(v)||v.polling)return;
 v.polling=true;
 try{
  const status=await preparationRequest('listing-prepare-status?id='+encodeURIComponent(v.id));
  if(!preparationOpen(v))return;
  v.status=status;v.error='';drawPreparation(v);
  if(status.state==='ready')return await enterPreparedListings();
  if(['queued','running'].includes(status.state))v.timer=setTimeout(()=>pollPreparation(v),1000);
 }catch(e){if(preparationOpen(v)){v.error=e.message;drawPreparation(v)}}
 finally{v.polling=false}
}
async function retryPublishPreparation(){
 const v=publishPreparation;if(!v||v.requesting)return;
 v.requesting=true;v.error='';drawPreparation(v);
 try{v.status=await preparationRequest('listing-prepare-retry',{id:v.id})}
 catch(e){v.error=e.message}
 finally{v.requesting=false;if(preparationOpen(v)){drawPreparation(v);if(!v.error)pollPreparation(v)}}
}
async function enterPreparedListings(){
 const v=publishPreparation;if(!v||v.requesting||!preparationOpen(v))return;
 v.requesting=true;drawPreparation(v);
 try{
  const status=await preparationRequest('listing-prepare-status?id='+encodeURIComponent(v.id)+'&result=1');
  if(!preparationOpen(v))return;
  // A result may have become stale while the progress window was open.
  if(status.failed!==v.status.failed||!status.result?.items.length){v.status=status;return}
  const d=status.result;v.entered=true;v.visible=false;clearTimeout(v.timer);
  if(status.state!=='running')try{sessionStorage.removeItem(preparationStorageKey)}catch{}
  preparationResumeButton();
  if(v.onReady&&status.state==='ready'){await v.onReady(d);return}
  publishView={items:d.items,index:0,connection:d.connection,templates:d.templates||[],sizeTemplates:d.size_templates||[],meta:null,busy:false};
  drawPublish();if(d.connection.selected_shop_id)await loadPublishMeta();
 }catch(e){v.error=e.message;if(v.entered)toast('读取上架资料失败：'+e.message)}
 finally{v.requesting=false;if(preparationOpen(v))drawPreparation(v)}
}
try{
 const saved=JSON.parse(sessionStorage.getItem(preparationStorageKey)||'null');
 if(saved?.id&&Array.isArray(saved.items)){publishPreparation={...saved,visible:false};preparationResumeButton()}
}catch{}
