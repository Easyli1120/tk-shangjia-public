"""Bounded TikTok page acquisition and product-only parsing (no captcha solver)."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
from html.parser import HTMLParser

import product_assets
import platform_support
import shutil

CHROME = platform_support.chrome_path()
MAX_LINKS = 3000
MAX_PAGE = 16 * 1024 * 1024
ITEM_TIMEOUT = 300
MARKETS = {'US','GB','ID','TH','VN','MY','PH','SG','MX','BR','DE','FR','IT','ES','IE','JP'}
CATEGORY_MISSING = '未确认最末级类目（需末级类目名称、ID 和平台依据）'
REGIONAL_UNAVAILABLE = '采集成功 · 本地区不售'


class CaptureError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def allowed_url(url, image=False):
    try:
        u = urllib.parse.urlsplit(url)
        domains = ('tiktok.com',) if not image else ('tiktok.com','tiktokcdn.com','tiktokcdn-us.com','tiktokcdn-eu.com','ttcdn-us.com','byteimg.com','ibytedtos.com','ibyteimg.com')
        return u.scheme == 'https' and u.port in (None,443) and not u.username and not u.password and any((u.hostname or '').lower() == d or (u.hostname or '').lower().endswith('.'+d) for d in domains)
    except (ValueError,TypeError):
        return False


def product_id(url):
    u = urllib.parse.urlsplit(url)
    match = re.search(r'/(?:product|pdp)/(?:[^/]+/)?(\d{8,})(?:/|$)',u.path)
    if match:
        return match[1]
    q = urllib.parse.parse_qs(u.query)
    for k in ('product_id','productId'):
        if re.fullmatch(r'\d{8,}',q.get(k,[''])[0]):
            return q[k][0]
    return ''


def parse_links(value, market='US'):
    if market not in MARKETS:
        raise ValueError('不支持的市场')
    if not isinstance(value,str) or len(value)>2_000_000:
        raise ValueError('请粘贴商品链接（最多 3000 条）')
    tokens = [s.strip('，,;；') for s in re.split(r'\s+',value.strip()) if s.strip('，,;；')]
    if not 1 <= len(tokens) <= MAX_LINKS:
        raise ValueError('每次请输入 1–3000 条链接，用换行或空格分隔')
    result,seen,errors = [],set(),[]
    for i,token in enumerate(tokens,1):
        token=html.unescape(token)
        if not allowed_url(token) or len(token)>4096:
            errors.append(str(i));continue
        u=urllib.parse.urlsplit(token);pid=product_id(token)
        short=(u.hostname in ('vm.tiktok.com','vt.tiktok.com') or u.path.startswith('/t/'))
        if not pid and not short:
            errors.append(str(i));continue
        q=urllib.parse.parse_qs(u.query)
        region=q.get('region',[market])[0].upper()
        path_market=re.match(r'^/([a-z]{2})/pdp/',u.path)
        if path_market:region=path_market[1].upper()
        if region not in MARKETS:
            errors.append(str(i));continue
        url=('https://shop.tiktok.com/view/product/'+pid+'?region='+region+'&local=en') if pid else token
        key=(region,pid or url)
        if key not in seen:
            result.append(dict(url=url,product_id=pid,market=region));seen.add(key)
    if errors:
        raise ValueError('第 '+', '.join(errors[:10])+' 条不是有效 TikTok Shop 商品链接或市场无效；请修正后提交')
    return result,len(tokens)-len(result)


class Page(HTMLParser):
    def __init__(self):
        super().__init__();self.documents=[];self.script=None;self.parts=[];self.meta={};self.hidden=0;self.visible=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('script','style'):self.hidden+=1
        if tag=='script' and ('json' in a.get('type','') or a.get('id','').startswith('__')):self.script=a;self.parts=[]
        if tag=='meta':self.meta[a.get('property',a.get('name',''))]=a.get('content','')
    def handle_data(self,data):
        if self.script is not None:self.parts.append(data)
        if not self.hidden:self.visible.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.script is not None:
            try:self.documents.append(json.loads(''.join(self.parts)))
            except (ValueError,RecursionError):pass
            self.script=None
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)


def walk(node):
    stack=[node]
    while stack:
        item=stack.pop()
        if isinstance(item,dict):
            yield item
            stack.extend(v for v in item.values() if isinstance(v,(dict,list)))
        elif isinstance(item,list):stack.extend(reversed(item))


def category_evidence(product):
    """Read category paths attached to this product, without walking recommendations."""
    paths=[]
    def add(value, category_id='', leaf=None):
        if isinstance(value,str):
            names=[s.strip() for s in value.split('>') if s.strip()]
        elif isinstance(value,list):
            names=[]
            for node in value:
                if isinstance(node,str): names.append(node.strip());continue
                if not isinstance(node,dict): continue
                name=node.get('category_name') or node.get('category_name_en') or node.get('name') or node.get('local_name')
                if name: names.append(str(name).strip())
            # A path endpoint must carry its own identity; a product-level ID
            # can refer to a different (deeper) node than a public breadcrumb.
            category_id,leaf='',None
            if value and isinstance(value[-1],dict):
                last=value[-1]
                if last.get('category_name') or last.get('category_name_en') or last.get('name') or last.get('local_name'):
                    category_id=last.get('category_id') or last.get('id') or ''
                    leaf=last.get('is_leaf',last.get('category_is_leaf'))
        else: return
        names=[n for n in names if n]
        if names: paths.append(dict(names=names,id=str(category_id or ''),leaf=leaf))
    cid=product.get('category_id') or product.get('categoryId') or ''
    leaf=product.get('category_is_leaf')
    add(product.get('category') or product.get('category_name'),cid,leaf)
    for field in ('category_path','categories'):
        add(product.get(field),cid,leaf)
    info=product.get('category_info')
    if isinstance(info,dict):
        add(info.get('recommended_categories'))
        for field in ('category_path','categories'):
            add(info.get(field))
        add(info.get('category_name'),info.get('category_id',''),info.get('is_leaf'))
    return paths


def confirmed_leaf(product):
    return bool(str(product.get('category') or '').strip() and
                str(product.get('category_id') or '').strip() not in ('','0') and
                product.get('category_is_leaf') is True)


def confirm_category(product, categories):
    """Confirm an observed category against the same market's official tree.

    Parent categories and title recommendations are never evidence of the
    competitor's leaf. Require a unique exact ID/path and a complete ancestry.
    """
    nodes={str(n['id']):n for n in categories if n.get('id')}
    normalize=lambda s: ' '.join(str(s).split()).casefold()
    def path_for(cid):
        path=[];seen=set()
        while cid not in ('','0'):
            if cid in seen or cid not in nodes:return []
            seen.add(cid);node=nodes[cid]
            if not (node.get('local_name') or node.get('name')):return []
            path.insert(0,node)
            cid=str(node.get('parent_id') or '')
        return path
    evidence=product.get('category_evidence') or category_evidence(product)
    matches={}
    for entry in evidence:
        names=[normalize(n) for n in entry.get('names',[])];cid=str(entry.get('id') or '')
        for key in ([cid] if cid else nodes):
            node=nodes.get(key,{})
            if node.get('is_leaf') is not True:continue
            path=path_for(key)
            if not path:continue
            labels=[{normalize(n.get('local_name','')),normalize(n.get('name','')),normalize(n.get('english_name',''))} for n in path]
            # Check names even for an ID: public and seller taxonomy versions
            # may reuse IDs. Do not silently turn a parent path into a leaf.
            if names and len(names)<=len(labels) and all(n in label for n,label in zip(names,labels[-len(names):])):
                matches[key]=path
    if len(matches)!=1:return False
    cid,path=next(iter(matches.items()))
    product.update(category=' > '.join(n.get('local_name') or n['name'] for n in path),
                   category_id=cid,category_is_leaf=True,category_source='official_category_tree')
    return True


def regional_unavailable(body):
    page=Page();page.feed(body)
    visible=' '.join(page.visible).lower()
    return bool(re.search(r'此国家或地区未提供的商品|此國家或地區未提供的商品|(?:商品|产品).{0,12}(?:不在|未在|不支持|不适用).{0,8}(?:国家|地区)|(?:product|item) (?:is )?(?:not available|unavailable) in (?:this|your) (?:country|region)|not available in this country',visible))


def extract_product(body,expected_id=''):
    page=Page();page.feed(body)
    if not verification_page(body) and regional_unavailable(body):
        raise CaptureError('regional_unavailable',REGIONAL_UNAVAILABLE)
    candidates=[];categories=[];product_categories={}
    canonical_id=product_id(page.meta.get('og:url',''))
    for doc in page.documents:
        for n in walk(doc):
            info=n.get('product_info') or {}
            if isinstance(info,dict) and info.get('product_id'):
                product_categories.setdefault(str(info['product_id']),[]).extend(category_evidence(n))
            pid=str(n.get('product_id',n.get('productId','')))
            if re.fullmatch(r'\d{8,}',pid) and (not expected_id or pid==expected_id) and n.get('title') and isinstance(n.get('skus'),list):
                candidates.append(n)
            if n.get('@type')=='BreadcrumbList':
                categories=[str(v.get('name') or v.get('item',{}).get('name','')) for v in sorted(n.get('itemListElement',[]),key=lambda v:v.get('position',0)) if '/c/' in str(v.get('item',''))]
    if not expected_id:
        candidates=[n for n in candidates if str(n.get('product_id',n.get('productId','')))==canonical_id] if canonical_id else candidates
        if len({str(n.get('product_id',n.get('productId',''))) for n in candidates})>1:
            raise CaptureError('identity','短链接未能确定唯一商品，需使用商品详情链接')
    if not candidates:
        visible=' '.join(page.visible).lower()
        if re.search(r'security check|verify.*human|drag the slider|verify to continue|验证码|拖动滑块',visible):
            raise CaptureError('verification','TikTok 要求人工验证，队列将继续处理当前商品')
        if 'crawler traffic detected' in body.lower():
            raise CaptureError('rate_limit','TikTok 暂时限制访问，稍后自动重试')
        if re.search(r'not available in this country|not available in your|product.*unavailable|product.*not found|sold out',visible):
            raise CaptureError('unavailable','商品不可用、已下架或当前地区不支持访问')
        raise CaptureError('missing_data','页面未返回匹配商品的完整 SKU 数据，不能确认采集完整')
    p=max(candidates,key=lambda n:len(n.get('skus',[]))+len(n.get('images',[])))
    p=json.loads(json.dumps(p));p['product_id']=str(p.get('product_id',p.get('productId','')))
    evidence=category_evidence(p)+product_categories.get(p['product_id'],[])
    if categories: evidence.append(dict(names=categories,id='',leaf=None))
    # A short public breadcrumb must not overwrite a deeper product category.
    # Keep the ID and path from the same evidence; only explicit flags mean leaf.
    chosen=max(evidence,key=lambda v:(v['leaf'] is True,len(v['names']),bool(v['id']))) if evidence else None
    p['category']=' > '.join(chosen['names']) if chosen else ''
    p.pop('category_id',None);p.pop('category_is_leaf',None)
    p['category_evidence']=evidence
    if chosen:
        if chosen['id']: p['category_id']=chosen['id']
        if isinstance(chosen['leaf'],bool): p['category_is_leaf']=chosen['leaf']
    if confirmed_leaf(p):p['category_source']='product_page'
    p['shop_name']=p.get('shop_name') or (p.get('seller') or {}).get('name','')
    return p


def manifest(p):
    found={}
    def add(v,group):
        if isinstance(v,str):
            if not allowed_url(v,True):return
            key=product_assets.asset_key(v)
            if key not in found:found[key]=dict(url=v,alternatives=[v],group_name=group)
        elif isinstance(v,list):
            for x in v:add(x,group)
        elif isinstance(v,dict):
            urls=v.get('url_list') or v.get('urlList')
            if isinstance(urls,list) and urls:
                valid=[u for u in urls if isinstance(u,str) and allowed_url(u,True)]
                if valid:
                    add(valid[0],group);item=found.get(product_assets.asset_key(valid[0]))
                    if item:item['alternatives']=list(dict.fromkeys(item['alternatives']+valid))
            else:
                for k in ('url','src','image','images','image_url','imageUrl','extended_images','sku_image'):
                    if k in v:add(v[k],group)
    add(p.get('images',[]),'商品图')
    for key in ('sale_props','skus'):
        for n in walk(p.get(key,[])):
            for k in ('image','images','sku_image','extended_images'):
                if k in n:add(n[k],'规格图')
    for n in walk(p.get('desc_blocks',[])):
        if 'image' in n:add(n['image'],'详情图')
    add(p.get('size_guide',{}),'尺码表')
    return list(found.values())


def audit(p,images):
    variants=[];missing=[];props=p.get('sale_props',[])
    for i,s in enumerate(p.get('skus',[])):
        price=s.get('price') or {};attrs=s.get('sku_sale_props') or []
        amount=price.get('sale_price_decimal',price.get('sale_price_format'))
        currency=price.get('currency_name') or (p.get('price') or {}).get('currency')
        v=dict(id=str(s.get('sku_id') or s.get('id') or ''),price=amount,currency=currency or '',original_price=price.get('origin_price_decimal',price.get('origin_price_format')),attributes=[dict(name=a.get('prop_name','规格'),value=a.get('prop_value','')) for a in attrs],stock=s.get('stock'))
        variants.append(v)
        if not v['id']:missing.append('SKU 标识缺失')
        if amount is None or str(amount)=='':missing.append('部分 SKU 价格缺失')
        if not currency:missing.append('价格币种缺失')
        if props and len(attrs)<len(props):missing.append('部分 SKU 颜色/规格缺失')
    if not p.get('title'):missing.append('标题缺失')
    # Category evidence is optional for collection; publishing validates its
    # own category requirements separately.
    if not p.get('images'):missing.append('商品主图组缺失')
    if not variants:missing.append('SKU 列表缺失')
    for prop in props:
        expected={str(v.get('prop_value_id') or v.get('prop_value')) for v in prop.get('sale_prop_values',[])}
        actual={str(a.get('prop_value_id') or a.get('prop_value')) for s in p.get('skus',[]) for a in s.get('sku_sale_props',[]) if str(a.get('prop_id'))==str(prop.get('prop_id'))}
        if expected-actual:missing.append('规格选项未覆盖全部 SKU')
    if not images or not any(x.get('group_name')=='商品图' and x.get('local_path') for x in images):missing.append('商品主图未保存')
    if any(not x.get('local_path') for x in images):missing.append('部分图片未下载')
    return dict(variants=variants,missing=list(dict.fromkeys(missing)),discovered=len(images),cached=sum(bool(i.get('local_path')) for i in images),scope='当前页面返回的商品图、规格图、详情图、尺码表、商品信息、销量及 SKU')


def download(url,target,image=False,timeout=20):
    # The timeout covers the entire redirect chain, not each redirect separately.
    deadline=time.monotonic()+timeout
    for _ in range(6):
        if not allowed_url(url,image):raise CaptureError('url','来源网址不在 TikTok 允许域名内')
        remaining=deadline-time.monotonic()
        if remaining<=0:raise CaptureError('timeout','下载跳转超时')
        header=str(target)+'.headers'
        try:
            r=subprocess.run([shutil.which('curl') or 'curl','--silent','--show-error','--compressed','--connect-timeout',str(min(8,remaining)),'--max-time',str(remaining),'--max-filesize',str(MAX_PAGE),'--proto','=https','--user-agent','Mozilla/5.0','--dump-header',header,'--output',str(target),'--write-out','%{http_code}',url],capture_output=True,timeout=remaining+1)
            status=int(r.stdout.decode().strip() or '0')
            if r.returncode:raise CaptureError('network','网络连接失败或下载超时')
            if status in (301,302,303,307,308):
                headers=Path(header).read_text(encoding='utf-8',errors='replace')
                locations=re.findall(r'^location:\s*(.*)$',headers,re.I|re.M)
                if not locations:raise CaptureError('network','跳转地址缺失')
                url=urllib.parse.urljoin(url,locations[-1].strip());continue
            if status in (403,429):raise CaptureError('rate_limit','TikTok 限制访问（HTTP %s）'%status)
            if status!=200:raise CaptureError('network','下载失败（HTTP %s）'%status)
            if target.stat().st_size>MAX_PAGE:raise CaptureError('size','响应超过大小限制')
            return url
        finally:
            Path(header).unlink(missing_ok=True)
    raise CaptureError('network','跳转次数过多')


def verification_page(body):
    page=Page();page.feed(body)
    return bool(re.search(r'security check|verify.*human|drag the slider|verify to continue|验证码|拖动滑块|拖動滑塊|完成验证', ' '.join(page.visible).lower()))


def browser_page(url,data,browser=None,progress=lambda **kw:None):
    import browser_capture
    if not browser:
        raise CaptureError('browser','采集浏览器会话尚未准备好')
    connection=browser_capture.Connection(browser['endpoint'],time.monotonic()+10)
    try:
        def valid(body):
            try:extract_product(body,product_id(url));return True
            except CaptureError:return False
        return browser_capture.read_page(connection,url,valid,browser['target'],progress,verification_page,resume=browser.get('resume',False),is_terminal=regional_unavailable)
    except browser_capture.RateLimitError as exc:
        raise CaptureError('rate_limit',str(exc))
    except (TimeoutError,subprocess.TimeoutExpired):
        raise CaptureError('timeout','商品页面加载超时，已转到下一条')
    except (OSError,ValueError,RuntimeError) as exc:
        raise CaptureError('browser','商品页面读取失败：'+str(exc)[:120])
    finally:connection.close()


def cache_image(item,folder,deadline):
    item=dict(item)
    key=hashlib.sha256(product_assets.asset_key(item['url']).encode()).hexdigest()
    cached=next((f for f in folder.glob(key+'.*') if f.suffix in ('.jpg','.png','.webp','.gif','.avif')),None)
    if cached and product_assets.image_extension(cached.read_bytes()):
        item['local_path']='/images/tiktok/'+cached.name;return item
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        target=Path(temp)/'download'
        for alternative in item['alternatives']:
            remaining=deadline-time.monotonic()
            if remaining<3:
                item['error']='本条下载时间已用完，后续补采继续';break
            try:
                download(alternative,target,True,min(12,remaining-2))
                ext=product_assets.image_extension(target.read_bytes())
                if not ext:raise CaptureError('image','来源返回的内容不是有效图片')
                destination=folder/(key+ext);os.replace(target,destination)
                item['local_path']='/images/tiktok/'+destination.name;break
            except (CaptureError,subprocess.TimeoutExpired,OSError) as exc:
                item['error']=str(exc)
                if isinstance(exc,CaptureError):item['error_code']=exc.code
    if item.get('local_path'):
        item.pop('error',None);item.pop('error_code',None)
    return item


def capture(url,expected_id,data,progress=lambda **kw:None,browser=None):
    data=Path(data);start=time.monotonic();wait_started=None;wait_seconds=0;verification_seen=False
    def page_progress(**info):
        nonlocal wait_started,wait_seconds,verification_seen
        if info.get('verification'):verification_seen=True
        now=time.monotonic()
        if info.get('verification') and wait_started is None:wait_started=now
        elif not info.get('verification') and wait_started is not None:
            wait_seconds+=now-wait_started;wait_started=None
        progress(**info)
    progress(stage='正在加载商品页')
    body=browser_page(url,data,browser,page_progress)
    try:p=extract_product(body,expected_id)
    except CaptureError as exc:
        if exc.code=='regional_unavailable':
            page=Page();page.feed(body)
            return dict(code=exc.code,message=REGIONAL_UNAVAILABLE,product_id=expected_id or product_id(page.meta.get('og:url','')),verification_seen=verification_seen)
        # Persist only structural diagnostics, never cookies, headers or whole pages.
        page=Page();page.feed(body)
        title=re.search(r'<title[^>]*>(.*?)</title>',body,re.I|re.S)
        matches=[]
        for doc in page.documents:
            for node in walk(doc):
                if str(node.get('product_id',node.get('productId','')))==expected_id:
                    matches.append(dict(keys=sorted(node),sku_count=len(node['skus']) if isinstance(node.get('skus'),list) else None,status=node.get('status')))
        diagnostic=dict(product_id=expected_id,code=exc.code,page_title=html.unescape(title[1])[:300] if title else '',json_documents=len(page.documents),matching_nodes=matches[:10])
        folder=data/'collection-diagnostics';folder.mkdir(exist_ok=True)
        key=expected_id if expected_id.isdigit() else hashlib.sha256(url.encode()).hexdigest()
        target=folder/(key+'.json');temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(diagnostic,ensure_ascii=False),encoding='utf-8');os.replace(temp,target)
        raise

    if wait_started is not None:wait_seconds+=time.monotonic()-wait_started
    # Reserve time for validation and atomic result writing before the supervisor's deadline.
    deadline=start+ITEM_TIMEOUT+wait_seconds-15
    progress(stage='正在整理商品图组、商品信息、销量和 SKU',title=p['title'])
    images=manifest(p);folder=data/'images'/'tiktok';folder.mkdir(parents=True,exist_ok=True)
    progress(stage='正在保存图片 0/%d'%len(images),discovered=len(images),cached=0,title=p['title'])
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='product-image') as pool:
        futures={pool.submit(cache_image,item,folder,deadline):index for index,item in enumerate(images)}
        for completed,future in enumerate(as_completed(futures),1):
            index=futures[future]
            try:images[index]=future.result()
            except Exception as exc:images[index]['error']='图片保存异常：'+str(exc)[:200]
            progress(stage='已处理图片 %d/%d'%(completed,len(images)),discovered=len(images),cached=sum(bool(i.get('local_path')) for i in images),title=p['title'])
    quality=audit(p,images)
    return dict(product=p,images=images,quality=quality,verification_seen=verification_seen,
                rate_limited=any(i.get('error_code')=='rate_limit' for i in images))


def main(job_file):
    def terminate(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM,terminate)
    job_path=Path(job_file);job=json.loads(job_path.read_text(encoding='utf-8'))
    verification_seen=False
    def progress(**kw):
        nonlocal verification_seen
        verification_seen |= bool(kw.get('verification'))
        path=Path(job['progress']);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(kw,ensure_ascii=False),encoding='utf-8');os.replace(temp,path)
    try:result=capture(job['url'],job['product_id'],job['data'],progress,job.get('browser'))
    except CaptureError as exc:result=dict(error=str(exc),code=exc.code)
    except Exception as exc:result=dict(error='采集异常：'+str(exc)[:300],code='internal')
    result['verification_seen']=verification_seen
    target=Path(job['result']);temp=target.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');os.replace(temp,target)

if __name__=='__main__': main(sys.argv[1])
