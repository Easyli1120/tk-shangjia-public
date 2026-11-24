let shopManagementBusy=false,shopManagementTimer=null,shopManagementStatus=null,shopDiagnostics=null,shopGuideStep=0;
const smTime=value=>value?new Date(value*1000).toLocaleString('zh-CN'):'尚未验证';
const smBadge=(text,state='pending')=>`<span class="sm-badge ${state}">${esc(text)}</span>`;
function stopShopManagementPolling(){clearTimeout(shopManagementTimer);shopManagementTimer=null}
async function shopManagementPage(){
 stopShopManagementPolling();
 const d=await api('/api/shop/status');shopManagementStatus=d;
 const params=new URLSearchParams(location.search),callbackError=params.get('shop_error'),connected=params.has('shop_connected');
 if(location.search.includes('shop_'))history.replaceState(null,'',location.pathname+'#shop-management');
 const link=d.service_id?'https://'+(d.authorization_region==='US'?'services.us.tiktokshop.com':'services.tiktokshop.com')+'/open/authorize?service_id='+encodeURIComponent(d.service_id):'';
 $('#app').innerHTML=heading('MY TIKTOK SHOP','店铺管理','按步骤13复制三项内容，即可开始连接自己的店铺。')+
 `<div class="sm-layout"><section class="panel sm-connect-panel"><div class="sm-title"><h2>粘贴三项信息，连接店铺</h2>${smBadge(d.authorized?'已连接店铺':d.authorization_pending?'等待授权':'尚未连接',d.authorized?'success':'pending')}</div>
 <p class="sm-muted">三项信息的获取方式见步骤13。保存后点击「复制授权链接」，粘贴到紫鸟对应的店铺窗口中打开并确认授权，软件会自动接收结果。</p>
 ${callbackError?`<div class="sm-warning" role="alert">${esc(callbackError)}</div>`:''}${connected?'<p class="sm-success" role="status">授权已完成，已从 TikTok 读取到店铺。</p>':''}
 <form id="sm-connect-form"><div class="sm-three-fields">
 <label for="sm-link"><span class="sm-field-number">1</span> 授权链接 <small>Copy authorization link 复制的完整内容</small><input id="sm-link" name="authorization_link" type="url" autocomplete="off" placeholder="粘贴 Copy authorization link 的链接" value="${esc(link)}" maxlength="4096" required></label>
 <label for="sm-key"><span class="sm-field-number">2</span> App Key <small>App key 下方复制的内容</small><input id="sm-key" name="app_key" autocomplete="off" placeholder="粘贴 App Key" value="${esc(d.app_key)}" maxlength="512" required></label>
 <label for="sm-secret"><span class="sm-field-number">3</span> App Secret <small>App secret 下方复制的内容</small><input id="sm-secret" name="app_secret" type="password" autocomplete="new-password" placeholder="${d.secret_configured?'已安全保存；留空继续使用':'粘贴 App Secret'}" maxlength="512" ${d.secret_configured?'':'required'}></label>
 </div><div class="sm-actions"><button class="btn primary" type="submit">保存并连接店铺</button><button class="btn" type="button" onclick="shopShowGuide(12)">查看步骤13</button></div><p class="sm-muted">密钥只在本机加密保存。请在同一台电脑的紫鸟中授权，并保持本软件运行；完成授权前不会显示连接成功。</p><div id="sm-connect-result" role="status" aria-live="polite">${d.authorization_pending?'正在等待 TikTok 授权返回；如需重新复制链接，请再次点击「保存并连接店铺」。':''}</div></form></section>
 <section class="panel" id="sm-tutorial"><div class="sm-title"><div><span class="sm-kicker">操作步骤</span><h2>跟着步骤，完成应用创建和连接</h2></div><a class="btn" href="/employee-guide.html" target="_blank" rel="noopener">全部步骤 / 打印</a></div><nav class="sm-guide-nav" aria-label="教程步骤">${SHOP_STEPS.map((s,i)=>`<button class="btn small" type="button" onclick="shopShowGuide(${i})" aria-label="步骤${i+1}：${esc(s.title)}" data-guide-step="${i}">${i+1}</button>`).join('')}</nav><div id="sm-guide-content"></div></section>
 <section class="panel"><div class="sm-title"><h2>我的店铺</h2><button class="btn" onclick="smDiagnose()" id="sm-diagnose">检测连接</button></div>
 ${d.stores.length?d.stores.map((s,i)=>`<article class="sm-store"><h3>${esc(s.name||s.label||'等待验证店铺')}</h3>${smBadge(s.status.label,s.status.code==='connected'?'success':'pending')}<p>店铺 ID：${esc(s.shop_id||'待平台返回')} · 国家 / 地区：${esc(s.region||'未知')}</p><p>授权时间：${smTime(s.connected_at)} · 最近检测：${smTime(s.last_verified_at)} · ${esc(s.token_status)}</p><details><summary>查看已授权权限</summary><p>${esc((s.scope_status.granted||[]).join('、')||'平台尚未返回')}</p><p>${esc(s.scope_status.label)}</p></details>${s.last_error?`<p class="sm-error">${esc(s.last_error)}</p>`:''}<div class="sm-actions"><button class="btn" onclick="smStoreAction('select',${i})">${String(d.selected_shop_id)===String(s.shop_id)?'当前上架店铺':'设为上架店铺'}</button><button class="btn" onclick="smStoreAction('refresh',${i})">刷新授权</button><button class="btn" onclick="smConnect()">重新授权</button><button class="btn danger" onclick="smStoreAction('revoke',${i})">解除本机绑定</button></div></article>`).join(''):'<p class="sm-muted">尚未连接店铺。完成上方三项信息后点击「保存并连接店铺」。</p>'}
 ${d.selected_shop_id?'<div class="sm-actions"><button class="btn" onclick="smProducts()">查询店铺商品</button></div>':''}<div id="sm-message" role="status" aria-live="polite"></div><div id="sm-checks">${shopDiagnostics?smChecks(shopDiagnostics):''}</div>
 <details class="sm-secondary"><summary>连接帮助与更多设置</summary><p>步骤9请复制本机地址。若平台拒绝本地地址，或你已有自有 HTTPS 回调，可在高级设置中配置。尚未收到真实回调时，地址匹配保持待验证。</p><div class="sm-actions"><button class="btn" onclick="shopShowConfig()">高级回调设置</button><button class="btn danger" onclick="smClear()">清除应用配置</button></div><p>解除本机绑定不等于撤销平台许可；平台许可可在 Seller Center 的应用管理中解除。</p></details></section></div>`;
 $('#sm-connect-form').addEventListener('submit',smSubmitConnection);
 smRenderGuide();
 if(d.authorization_pending||d.stores.some(s=>s.status.code==='authorizing'))smPollConnection();
}
async function smSubmitConnection(e){
 e.preventDefault();if(shopManagementBusy)return;shopManagementBusy=true;
 const form=e.currentTarget,button=form.querySelector('button[type=submit]'),output=$('#sm-connect-result');button.disabled=true;
 output.textContent='正在保存并生成授权链接…';
 try{const result=await shopPost('connect',Object.fromEntries(new FormData(form)));form.elements.app_secret.value='';await shopManagementPage();smAuthorizationMessage(result)}
 catch(error){output.textContent=error.message;form.elements.app_secret.value=''}
 finally{shopManagementBusy=false;button.disabled=false}
}
function smAuthorizationMessage(result){
 const output=$('#sm-connect-result')||$('#sm-message');
 output.replaceChildren(document.createTextNode('已保存，请复制授权链接，粘贴到紫鸟对应的店铺窗口中打开并确认授权。'));
 const actions=document.createElement('div');actions.className='sm-actions';
 const button=document.createElement('button');button.type='button';button.className='btn primary';button.textContent='复制授权链接';
 const link=document.createElement('input');link.type='text';link.value=result.url;link.readOnly=true;link.hidden=true;link.setAttribute('aria-label','本次授权链接');
 button.onclick=async()=>{
  try{await navigator.clipboard.writeText(result.url);toast('授权链接已复制，请粘贴到紫鸟对应的店铺窗口')}
  catch(e){link.hidden=false;link.focus();link.select();toast('自动复制失败，已选中授权链接，请手动复制')}
 };
 actions.append(button,link);output.append(actions);
}
function smPollConnection(){
 shopManagementTimer=setTimeout(async()=>{if(state.page!=='shop-management')return;
 try{const d=await api('/api/shop/status');if(d.authorization_pending||d.stores.some(s=>s.status.code==='authorizing'))smPollConnection();else await shopManagementPage()}
 catch(e){smMessage(e.message)}},3000);
}
function smMessage(message){const e=$('#sm-message');if(e)e.textContent=message;else toast(message)}
function smRenderGuide(){
 const i=shopGuideStep,d=shopManagementStatus,callback=d?.callback;
 const address=callback?.mode==='https'?callback.url:(callback?.entry_url||'');
 $('#sm-guide-content').innerHTML=shopGuideMarkup(i,esc)+(i===8?`<div class="sm-callback-copy"><strong>步骤9 · 复制到 Redirect URL</strong><div class="sm-copy-row"><code id="sm-guide-callback">${esc(address)}</code><button class="btn" onclick="smCopy(document.getElementById('sm-guide-callback').textContent)">复制本机地址</button></div><p>这不是第四项要填写的信息。将它复制到 TikTok 后，回这里粘贴步骤13的三项内容即可。</p></div>`:'')+`<div class="sm-guide-footer"><button class="btn" onclick="shopShowGuide(${i-1})" ${i===0?'disabled':''}>上一步</button><span>${i+1} / 13</span><button class="btn primary" onclick="${i===12?'smFocusConnection()':`shopShowGuide(${i+1})`}">${i===12?'去粘贴三项信息':'下一步'}</button></div>`;
 document.querySelectorAll('[data-guide-step]').forEach(b=>{const active=Number(b.dataset.guideStep)===i;b.classList.toggle('primary',active);if(active)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current')});
}
function shopShowGuide(index){shopGuideStep=Math.max(0,Math.min(12,index));smRenderGuide();$('#sm-tutorial').scrollIntoView({behavior:'smooth',block:'start'})}
function smFocusConnection(){$('#sm-connect-form').scrollIntoView({behavior:'smooth',block:'center'});$('#sm-link').focus({preventScroll:true})}
async function smCopy(text){try{await navigator.clipboard.writeText(text);toast('已复制')}catch(e){toast('无法读取剪贴板权限，请选中文本手动复制')}}
async function shopShowConfig(){
 stopShopManagementPolling();window.scrollTo(0,0);const d=await api('/api/shop/status');shopManagementStatus=d;
 const cb=d.callback;
 $('#app').innerHTML=heading('APPLICATION SETTINGS','我的应用配置','只填写自己的应用，保存在当前电脑。',`<button class="btn" onclick="shopManagementPage()">返回店铺管理</button>`)+`<section class="panel sm-config"><form id="sm-config-form"><label>App Key <small>应用编号，不是 App ID</small><input name="app_key" value="${esc(d.app_key)}" autocomplete="off" maxlength="512" required></label><label>App Secret <small>${d.secret_configured?'已保存；留空保留原值':'应用密钥，仅本机后端使用'}</small><input name="app_secret" type="password" autocomplete="new-password" maxlength="512" ${d.secret_configured?'':'required'}></label><label>Partner Center 官方授权链接 <small>在应用详情点击 Copy authorization link 后粘贴</small><input name="authorization_link" type="url" autocomplete="off" placeholder="https://services.us.tiktokshop.com/open/authorize?service_id=…" value="${esc(d.service_id?'https://'+(d.authorization_region==='US'?'services.us.tiktokshop.com':'services.tiktokshop.com')+'/open/authorize?service_id='+encodeURIComponent(d.service_id):'')}" required></label>
 <div class="sm-warning">保存不等于验证成功。授权后软件将通过真实接口核验密钥。</div><label>当前电脑生成的回调地址 <small>复制到 Partner Center 的 Redirect URL</small><div class="sm-copy-row"><input id="sm-local-url" readonly value="${esc(cb.local_url)}"><button class="btn" type="button" onclick="smCopy(document.getElementById('sm-local-url').value)">复制</button></div></label>${cb.invalid?'<p class="sm-error">旧测试回调地址不可用，已显示本机候选地址。请在 Partner Center 保存后再授权。</p>':''}${cb.changed?'<p class="sm-error">当前回调地址与旧配置不同。请在 Partner Center 更新地址后再授权。</p>':''}
 <label>实际填写到 Partner Center 的 Redirect URL<input name="redirect_url" type="url" required value="${esc((cb.changed||cb.invalid)?cb.local_url:d.redirect_url||cb.local_url)}"></label>
 <details><summary>平台只接受公网 HTTPS 时</summary><p>请准备自己控制的域名和有效 HTTPS 证书，把 <code>/api/shop/callback</code> 与 <code>/api/shop/callback-health</code> 转发到当前电脑专用回调服务 <code>http://127.0.0.1:${esc(cb.port||'等待启动')}</code>。转发时保留原域名 Host，并设置 X-Forwarded-Proto: https。这个监听端口不会提供商品、数据库或配置接口。不要把网页服务端口公开到互联网。</p><p>填写完整 HTTPS 回调地址后，软件会检测它是否确实连到本机；未通过时不会开始授权。开发者不提供中央回调服务器。每位员工需要自己的地址和运行中的转发服务；如果无法准备，请联系平台确认本地回调方案。</p></details>
 <label class="sm-checkbox"><input type="checkbox" name="redirect_confirmed" ${cb.confirmed&&!cb.changed?'checked':''}>我已在 Partner Center 保存并发布完全相同的回调地址</label><label class="sm-checkbox"><input type="checkbox" name="permissions_confirmed" ${d.permissions_confirmed?'checked':''}>我已按教程启用 / 申请商品管理所需权限</label>
 <div class="sm-actions"><button class="btn primary" type="submit">保存配置</button><button class="btn" type="button" onclick="smTestConfig()">测试已保存的应用配置</button><button class="btn danger" type="button" onclick="smClear()">清除配置</button></div><div id="sm-config-result" role="status" aria-live="polite"></div></form></section>`;
 $('#sm-config-form').addEventListener('submit',async e=>{e.preventDefault();const button=e.currentTarget.querySelector('button[type=submit]');button.disabled=true;const form=new FormData(e.currentTarget),body=Object.fromEntries(form);body.redirect_confirmed=form.has('redirect_confirmed');body.permissions_confirmed=form.has('permissions_confirmed');try{shopManagementStatus=await shopPost('config',body);e.target.elements.app_secret.value='';e.target.elements.app_secret.required=false;$('#sm-config-result').innerHTML='<p>已安全保存。尚未验证密钥及店铺权限。</p><button class="btn primary" onclick="smConnect()">连接我的 TikTok Shop 店铺</button>'}catch(err){$('#sm-config-result').textContent=err.message}finally{button.disabled=false}});
}
async function smTestConfig(){const target=$('#sm-config-result');target.textContent='正在执行配置检测…';try{shopDiagnostics=await shopPost('test-config');target.innerHTML=smChecks(shopDiagnostics)}catch(e){target.textContent=e.message}}
async function smClear(){if(!confirm('清除本机应用配置和授权凭证？商品、图片和 SKU 将保留；下次需重新填写和授权。'))return;try{await shopPost('clear-config');shopDiagnostics=null;await shopManagementPage();toast('已清除本机应用配置')}catch(e){toast(e.message)}}
async function smConnect(){
 const form=$('#sm-connect-form');if(form){form.requestSubmit();return}
 await shopManagementPage();smFocusConnection();
}
function smChecks(d){return `<div class="sm-check-list">${d.checks.map(x=>`<div class="sm-check"><strong>${esc(x.name)}</strong>${smBadge(({success:'检测成功',failed:'检测失败',pending:'待验证'})[x.state],x.state)}<p>${esc(x.detail)}</p></div>`).join('')}</div><p class="sm-muted">检测时间：${smTime(d.checked_at)}。商品写入 / 编辑 / 发布未由此检测执行。</p>`}
async function smDiagnose(){const button=$('#sm-diagnose');button.disabled=true;button.textContent='检测中…';try{shopDiagnostics=await shopPost('diagnostics');$('#sm-checks').innerHTML=smChecks(shopDiagnostics)}catch(e){$('#sm-checks').textContent=e.message}finally{button.disabled=false;button.textContent='重新检测'}}
async function smStoreAction(action,index){const s=shopManagementStatus.stores[index];if(action==='revoke'&&!confirm('解除此店铺的本机绑定？如需撤销平台许可，还需要到 Seller Center 的应用管理中解除。'))return;try{await shopPost(action,{shop_id:s.shop_id||s.id});shopDiagnostics=null;await shopManagementPage();toast('操作已完成')}catch(e){smMessage(e.message)}}
async function smProducts(token=''){try{const d=await shopPost('products-search',{page_token:token});const target=$('#sm-message');target.innerHTML=`<h3>平台商品查询结果</h3>${(d.products||[]).length?`<div class="sm-product-list">${d.products.map(x=>`<p><strong>${esc(x.title||x.id)}</strong><br>ID：${esc(x.id)} · ${esc(x.status||'未知')} <button class="btn small" data-remote-product="${esc(x.id)}">查看 / 编辑</button></p>`).join('')}</div>`:'<p>查询成功，当前没有商品。</p>'}`;target.querySelectorAll('[data-remote-product]').forEach(b=>b.onclick=()=>smEditProduct(b.dataset.remoteProduct));if(d.next_page_token){const b=document.createElement('button');b.className='btn';b.textContent='下一页';b.onclick=()=>smProducts(d.next_page_token);target.append(b)}}catch(e){smMessage(e.message)}}
async function smEditProduct(id){
 try{
  const d=await shopPost('products-get',{product_id:id}),p=d.product;
  $('#app').innerHTML=heading('SHOP PRODUCT','编辑店铺商品',`${d.shop_name||d.shop_id} · ${id}`,`<button class="btn" onclick="shopManagementPage()">返回店铺管理</button>`)+`<section class="panel sm-config"><p>修改平台上已有商品的标题与详情。已发布商品的修改可能需要再次审核。</p><form id="sm-remote-form"><label>商品标题<input name="title" maxlength="255" required value="${esc(p.title||'')}"></label><label class="sm-checkbox"><input type="checkbox" name="update_description">同时替换商品详情（替换后使用新文字，原详情中的图片和排版将被移除）</label><label>商品详情文字<textarea name="description_text" rows="14" maxlength="10000">${esc(d.description_text||'')}</textarea></label><p>默认只修改标题，保留平台原商品详情。需要重写详情时再勾选上方选项。</p><p>平台状态：${esc(p.status||'未知')}。价格、库存、规格及图片可继续在 Seller Center 管理。</p><button class="btn primary" type="submit" ${d.region==='US'?'':'disabled'}>核对并提交修改</button><div id="sm-edit-result" role="status"></div></form></section>`;
  $('#sm-remote-form').onsubmit=async e=>{
   e.preventDefault();if(!confirm(`确认修改店铺「${d.shop_name||d.shop_id}」的商品 ${id}？平台可能重新审核。`))return;
   const button=e.currentTarget.querySelector('button');button.disabled=true;
   try{const result=await shopPost('products-edit',{product_id:id,shop_id:d.shop_id,revision:d.revision,confirmed:true,...Object.fromEntries(new FormData(e.currentTarget)),update_description:e.currentTarget.elements.update_description.checked});$('#sm-edit-result').textContent=result.message}
   catch(error){$('#sm-edit-result').textContent=error.message+'；请返回店铺管理重新查询后再操作。'}
  };
 }catch(e){smMessage(e.message)}
}
