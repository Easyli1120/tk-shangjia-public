let galleryVisible=[],galleryLightbox=null;
const galleryKey=(market,id)=>encodeURIComponent(market+'|'+id);
function galleryCard(x){
 const images=(x.draftDetail?.images||[]).filter(i=>i.local_path),key=galleryKey(x.market,x.id);
 const previous=galleryVisible.findIndex(p=>galleryKey(p.market,p.id)===key);
 if(previous<0)galleryVisible.push(x);else galleryVisible[previous]=x;
 return `<div class="draft-gallery"><div class="draft-thumbs">${images.map((i,n)=>`<button type="button" onclick="event.stopPropagation();openGallery('${key}',${n})"><img loading="lazy" src="${esc(i.local_path)}" alt="商品图片 ${n+1}"></button>`).join('')}</div>${images.length?`<button class="btn small" onclick="event.stopPropagation();downloadGallery('${key}',this)">下载已保存图片</button>`:'暂无已保存图片'}</div>`;
}
function openGallery(key,index){
 const x=galleryVisible.find(x=>galleryKey(x.market,x.id)===key);if(!x)return;
 const images=(x.draftDetail?.images||[]).filter(i=>i.local_path);if(!images.length)return;
 galleryLightbox={images,index,name:x.name};drawGalleryLightbox();
}
function closeGalleryLightbox(){document.getElementById('gallery-lightbox')?.remove();galleryLightbox=null}
function stepGallery(delta){if(!galleryLightbox)return;galleryLightbox.index=(galleryLightbox.index+delta+galleryLightbox.images.length)%galleryLightbox.images.length;drawGalleryLightbox()}
function drawGalleryLightbox(){
 document.getElementById('gallery-lightbox')?.remove();const g=galleryLightbox,i=g.images[g.index],box=document.createElement('div');
 box.id='gallery-lightbox';box.className='gallery-lightbox';box.setAttribute('role','dialog');box.setAttribute('aria-modal','true');box.setAttribute('aria-label','商品图组预览');
 box.innerHTML=`<button class="gallery-lightbox-close" aria-label="关闭图片预览" onclick="closeGalleryLightbox()">×</button><button class="gallery-prev" aria-label="上一张" onclick="stepGallery(-1)">‹</button><figure><img src="${esc(i.local_path)}" alt="${esc(g.name)}"><figcaption>${esc(i.group_name||'商品图')} · ${g.index+1} / ${g.images.length} <a href="${esc(i.local_path)}" target="_blank" rel="noopener">打开原图</a></figcaption></figure><button class="gallery-next" aria-label="下一张" onclick="stepGallery(1)">›</button>`;
 box.addEventListener('click',e=>{if(e.target===box)closeGalleryLightbox()});document.body.append(box);box.querySelector('button').focus();
}
document.addEventListener('keydown',e=>{if(!galleryLightbox)return;if(e.key==='Escape')closeGalleryLightbox();if(e.key==='ArrowLeft')stepGallery(-1);if(e.key==='ArrowRight')stepGallery(1);if(e.key==='Tab'){const controls=[...document.querySelectorAll('#gallery-lightbox button,#gallery-lightbox a')],i=controls.indexOf(document.activeElement);e.preventDefault();controls[(i+(e.shiftKey?-1:1)+controls.length)%controls.length].focus()}});

const galleryDownloads=new Set();
async function downloadGallery(key,button){
 if(galleryDownloads.has(key))return;
 const x=galleryVisible.find(x=>galleryKey(x.market,x.id)===key);if(!x)return;
 galleryDownloads.add(key);button.disabled=true;const label=button.textContent;button.textContent='正在打包…';
 try{
  const response=await fetch('/api/gallery/download?market='+encodeURIComponent(x.market)+'&id='+encodeURIComponent(x.id));
  if(!response.ok){const error=await response.json();throw new Error(error.error||'下载失败')}
  const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download=(x.market+'_'+x.id).replace(/[^A-Za-z0-9_-]/g,'_')+'_images.zip';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  toast('图片压缩包已生成，请查看浏览器下载');
 }catch(e){toast('下载失败：'+e.message)}
 finally{galleryDownloads.delete(key);if(button.isConnected){button.disabled=false;button.textContent=label}}
}
