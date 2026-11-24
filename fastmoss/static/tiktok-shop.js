// 店铺管理与一键上架共用的本机请求助手。
function shopPost(action, body={}){return fetch('/api/shop/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.error||'店铺请求失败');return d})}
