"""Persistent serial queue. Each product is isolated in a bounded subprocess."""
import json
from pathlib import Path
import subprocess
import platform_support
import sys
import tempfile
import threading
import time
import uuid

import tiktok_collect
import sku_manager
import browser_capture
import library_categories
import listing_editor
import listing_trash

MAX_ATTEMPTS=3
DEFAULT_PACE=0


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS tiktok_jobs(
      id INTEGER PRIMARY KEY AUTOINCREMENT, request_key TEXT NOT NULL UNIQUE,
      batch_id INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'running',
      total INTEGER NOT NULL, duplicates INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS tiktok_items(
      id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES tiktok_jobs(id),
      position INTEGER NOT NULL, url TEXT NOT NULL, product_id TEXT NOT NULL, market TEXT NOT NULL,
      state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
      next_at REAL NOT NULL DEFAULT 0, token TEXT, stage TEXT, error TEXT, error_code TEXT,
      title TEXT, discovered INTEGER NOT NULL DEFAULT 0, cached INTEGER NOT NULL DEFAULT 0,
      missing TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL,
      UNIQUE(job_id,market,url));
    CREATE INDEX IF NOT EXISTS tiktok_items_queue ON tiktok_items(state,next_at,job_id,id);
    CREATE TABLE IF NOT EXISTS tiktok_product_availability(
      market TEXT,product_id TEXT,state TEXT NOT NULL,message TEXT NOT NULL,
      source_url TEXT,checked_at TEXT,PRIMARY KEY(market,product_id));
    CREATE TABLE IF NOT EXISTS tiktok_pacing(
      id INTEGER PRIMARY KEY CHECK(id=1), next_at REAL NOT NULL DEFAULT 0,
      level INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0,
      count INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '');
    INSERT OR IGNORE INTO tiktok_pacing(id) VALUES(1);
    ''')
    if 'verification_hold' not in {row[1] for row in c.execute('PRAGMA table_info(tiktok_items)')}:
        c.execute('ALTER TABLE tiktok_items ADD COLUMN verification_hold INTEGER NOT NULL DEFAULT 0')
        c.execute("UPDATE tiktok_items SET verification_hold=1 WHERE state='running' AND (stage LIKE '%验证码%' OR stage LIKE '%确认验证结果%')")
    if 'draft_event_id' not in {row[1] for row in c.execute('PRAGMA table_info(tiktok_jobs)')}:
        c.execute('ALTER TABLE tiktok_jobs ADD COLUMN draft_event_id INTEGER NOT NULL DEFAULT 0')
    if 'source_batch_id' not in {row[1] for row in c.execute('PRAGMA table_info(tiktok_jobs)')}:
        c.execute('ALTER TABLE tiktok_jobs ADD COLUMN source_batch_id INTEGER')
    c.execute("UPDATE tiktok_pacing SET next_at=0,level=0,successes=0,reason=''")
    c.execute("UPDATE tiktok_items SET next_at=0 WHERE state='retry'")



def submit(server,body):
    key=body.get('request_key')
    if not isinstance(key,str) or not 16<=len(key)<=100:raise ValueError('请求标识无效，请刷新页面后重试')
    with server.db() as c:
        c.execute('BEGIN IMMEDIATE')
        existing=c.execute('SELECT id FROM tiktok_jobs WHERE request_key=?',(key,)).fetchone()
        if existing:return dict(id=existing['id'],reused=True)
        source_batch=body.get('source_batch_id')
        if source_batch is not None:
            if not isinstance(source_batch,int) or isinstance(source_batch,bool):raise ValueError('文件批次无效')
            batch=c.execute("SELECT * FROM batches WHERE id=? AND kind='product'",(source_batch,)).fetchone()
            if not batch or c.execute("SELECT 1 FROM tiktok_jobs WHERE batch_id=? AND state!='deleted'",(source_batch,)).fetchone():
                raise ValueError('商品文件批次不存在或已删除')
            active=c.execute("SELECT id FROM tiktok_jobs WHERE source_batch_id=? AND state IN ('running','paused') ORDER BY id DESC LIMIT 1",(source_batch,)).fetchone()
            if active:return dict(id=active[0],reused=True)
            rows=c.execute("""SELECT DISTINCT s.market,s.entity_id FROM snapshots s
                JOIN records r ON r.kind='product' AND r.market=s.market AND r.id=s.entity_id
                JOIN draft_entries d ON d.market=r.market AND d.id=r.id AND d.active=1
                WHERE s.batch_id=? AND s.kind='product' ORDER BY s.row_number""",(source_batch,)).fetchall()
            if not rows:raise ValueError('此文件没有仍在竞品库中的商品，请重新导入后采集')
            links,duplicates=tiktok_collect.parse_links('\n'.join(
                'https://shop.tiktok.com/view/product/'+row['entity_id']+'?region='+row['market']+'&local=en' for row in rows))
        else:
            links,duplicates=tiktok_collect.parse_links(body.get('links'),body.get('market','US'))
        stamp=server.now()
        bid=c.execute("INSERT INTO batches(filename,imported_at,source_time_basis,kind,total,imported,errors) VALUES(?,?,'商品页面实时采集','product',?,0,'[]')",('TikTok 商品链接采集',stamp,len(links))).lastrowid
        generation=c.execute('SELECT COALESCE(MAX(id),0) FROM draft_events').fetchone()[0]
        jid=c.execute('INSERT INTO tiktok_jobs(request_key,batch_id,total,duplicates,created_at,updated_at,draft_event_id,source_batch_id) VALUES(?,?,?,?,?,?,?,?)',(key,bid,len(links),duplicates,stamp,stamp,generation,source_batch)).lastrowid
        c.executemany('INSERT INTO tiktok_items(job_id,position,url,product_id,market,updated_at) VALUES(?,?,?,?,?,?)',[(jid,i,link['url'],link['product_id'],link['market'],stamp) for i,link in enumerate(links,1)])
        return dict(id=jid,total=len(links),duplicates=duplicates)


def job_status(c,jid):
    row=c.execute('SELECT * FROM tiktok_jobs WHERE id=?',(jid,)).fetchone()
    if not row:raise ValueError('任务不存在')
    result=dict(row);result.pop('request_key',None)
    if result['source_batch_id'] is not None:
        source=c.execute('SELECT filename FROM batches WHERE id=?',(result['source_batch_id'],)).fetchone()
        result['source_filename']=source[0] if source else ''
    counts={r['state']:r['n'] for r in c.execute('SELECT state,count(*) n FROM tiktok_items WHERE job_id=? GROUP BY state',(jid,))}
    result['counts']=counts
    result['regional_unavailable']=c.execute("SELECT count(*) FROM tiktok_items WHERE job_id=? AND state='complete' AND error_code='regional_unavailable'",(jid,)).fetchone()[0]
    result['processed']=sum(counts.get(s,0) for s in ('complete','partial','failed','cancelled'))
    result['attempted']=c.execute('SELECT count(*) FROM tiktok_items WHERE job_id=? AND attempts>0',(jid,)).fetchone()[0]
    current=c.execute("SELECT position,url,title,stage,attempts,discovered,cached,updated_at FROM tiktok_items WHERE job_id=? AND state='running'",(jid,)).fetchone()
    result['current']=dict(current) if current else None
    result['next_at']=c.execute("SELECT min(next_at) FROM tiktok_items WHERE job_id=? AND state='retry'",(jid,)).fetchone()[0]
    return result


def jobs(c):
    return [job_status(c,r[0]) for r in c.execute("SELECT id FROM tiktok_jobs WHERE state!='deleted' ORDER BY id DESC LIMIT 30").fetchall()]


def pacing_status(c):
    row=dict(c.execute('SELECT * FROM tiktok_pacing WHERE id=1').fetchone())
    return dict(next_at=row['next_at'],remaining=max(0,int(row['next_at']-time.time()+.999)),reason=row['reason'])


def items(c,jid,page=1,state=''):
    where='job_id=?';args=[jid]
    if state=='incomplete':where+=" AND state IN ('partial','failed','retry')"
    elif state:
        if state not in ('complete','pending','running','cancelled'):raise ValueError('状态无效')
        where+=' AND state=?';args.append(state)
    total=c.execute('SELECT count(*) FROM tiktok_items WHERE '+where,args).fetchone()[0]
    rows=[]
    for r in c.execute('SELECT * FROM tiktok_items WHERE '+where+' ORDER BY position LIMIT 50 OFFSET ?',args+[(max(1,page)-1)*50]):
        d=dict(r);d.pop('token',None);d['missing']=json.loads(d['missing']);rows.append(d)
    return dict(items=rows,total=total,page=page,job=job_status(c,jid))


def finish_jobs(c,stamp):
    c.execute("""UPDATE tiktok_jobs SET state='finished',updated_at=? WHERE state='running'
      AND NOT EXISTS(SELECT 1 FROM tiktok_items i WHERE i.job_id=tiktok_jobs.id AND i.state IN ('pending','running','retry'))""",(stamp,))


def complete_saved_category_items(server,jid):
    """Reclassify a requested job's saved items without collecting them again."""
    category_missing={'类目缺失',tiktok_collect.CATEGORY_MISSING}
    with server._request_lock,server.db() as c:
        c.execute('BEGIN IMMEDIATE')
        job=c.execute('SELECT state FROM tiktok_jobs WHERE id=?',(jid,)).fetchone()
        if not job or job['state']=='deleted':raise ValueError('任务不存在或已删除')
        changed=0;stamp=server.now()
        rows=c.execute("""SELECT i.*,p.audit_json FROM tiktok_items i
            JOIN product_capture p ON p.market=i.market AND p.product_id=i.product_id
            WHERE i.job_id=? AND i.state IN ('partial','retry','failed')""",(jid,)).fetchall()
        for row in rows:
            missing=json.loads(row['missing'])
            if not missing or not set(missing)<=category_missing:continue
            if row['error_code'] not in ('incomplete',None,''):continue
            if row['cached']<=0 or row['cached']!=row['discovered']:continue
            audit=json.loads(row['audit_json'])
            # A newer saved capture may have other failures. Keep those intact.
            if set(audit.get('missing',[]))-category_missing:continue
            audit['missing']=[]
            c.execute('UPDATE product_capture SET audit_json=?,metadata_ready=1 WHERE market=? AND product_id=?',
                      (json.dumps(audit,ensure_ascii=False),row['market'],row['product_id']))
            c.execute("""UPDATE tiktok_items SET state='complete',missing='[]',error=NULL,error_code=NULL,
                next_at=0,token=NULL,verification_hold=0,stage='页面资料已保存',updated_at=? WHERE id=?""",(stamp,row['id']))
            changed+=1
        if changed:
            pending=c.execute("SELECT 1 FROM tiktok_items WHERE job_id=? AND state IN ('pending','running','retry') LIMIT 1",(jid,)).fetchone()
            c.execute('UPDATE tiktok_jobs SET state=?,updated_at=? WHERE id=?',
                      (job['state'] if pending or job['state'] in ('paused','cancelled') else 'finished',stamp,jid))
        return {'ok':True,'id':jid,'changed':changed}


def control(server,body):
    jid=int(body.get('id',0));action=body.get('action');stamp=server.now()
    with server._request_lock,server.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM tiktok_jobs WHERE id=?',(jid,)).fetchone()
        if not row:raise ValueError('任务不存在')
        if action=='delete':
            if row['state']=='deleted':return {'ok':True,'id':jid,'changed':0}
            # Keep source data for product recovery, but remove the task and its
            # linked products from the visible workspace in one transaction.
            products=[dict(r) for r in c.execute("""SELECT DISTINCT r.market,r.id
                FROM records r JOIN draft_entries d ON d.market=r.market AND d.id=r.id
                WHERE r.kind='product' AND d.active=1 AND (EXISTS (
                    SELECT 1 FROM tiktok_items i WHERE i.job_id=? AND i.market=r.market AND i.product_id=r.id
                ) OR EXISTS (
                    SELECT 1 FROM snapshots s WHERE s.batch_id=? AND s.kind='product' AND s.market=r.market AND s.entity_id=r.id
                ))""",(jid,row['batch_id']))]
            changed=0
            for offset in range(0,len(products),3000):
                changed+=listing_trash.change(c,{'items':products[offset:offset+3000]},False,stamp)['changed']
            c.execute("UPDATE tiktok_items SET state='cancelled',verification_hold=0,token=NULL,stage='任务已删除',updated_at=? WHERE job_id=? AND state IN ('pending','retry','running')",(stamp,jid))
            c.execute("UPDATE tiktok_jobs SET state='deleted',updated_at=? WHERE id=?",(stamp,jid))
            return {'ok':True,'id':jid,'changed':changed}
        if row['state']=='deleted':raise ValueError('任务已删除，不能继续或补采；请重新提交链接')
        if action=='pause':
            if row['state']=='running':c.execute("UPDATE tiktok_jobs SET state='paused',updated_at=? WHERE id=?",(stamp,jid))
        elif action=='resume':
            if row['state']=='paused':c.execute("UPDATE tiktok_jobs SET state='running',updated_at=? WHERE id=?",(stamp,jid))
        elif action=='cancel':
            c.execute("UPDATE tiktok_items SET state='cancelled',verification_hold=0,token=NULL,stage='已停止',updated_at=? WHERE job_id=? AND state IN ('pending','retry','running')",(stamp,jid))
            c.execute("UPDATE tiktok_jobs SET state='cancelled',updated_at=? WHERE id=?",(stamp,jid))
        elif action=='retry':
            if not c.execute('SELECT 1 FROM batches WHERE id=?',(row['batch_id'],)).fetchone():raise ValueError('该任务的导入来源已删除，请重新提交链接')
            c.execute("UPDATE tiktok_items SET state='pending',attempts=0,next_at=0,token=NULL,error=NULL,error_code=NULL,stage='等待补采',updated_at=? WHERE job_id=? AND state IN ('failed','partial','cancelled') AND COALESCE(error_code,'')!='product_deleted'",(stamp,jid))
            c.execute("UPDATE tiktok_jobs SET state='running',updated_at=? WHERE id=?",(stamp,jid))
        else:raise ValueError('操作无效')
        finish_jobs(c,stamp)
        return job_status(c,jid)


def add_collected_draft(server,c,item,pid):
    return server.add_draft(c,item['market'],pid,'链接采集','采集资料保存到竞品库')


def prepare_collection(c, item, pid):
    # Event order also handles delete/re-submit within the same second and
    # short links whose product identity becomes known only after collection.
    generation=c.execute('SELECT draft_event_id FROM tiktok_jobs WHERE id=?',(item['job_id'],)).fetchone()[0]
    deleted=c.execute("SELECT MAX(id) FROM draft_events WHERE market=? AND product_id=? AND event='删除'",(item['market'],pid)).fetchone()[0]
    if deleted and deleted>generation:return False
    if (c.execute('SELECT 1 FROM listing_trash WHERE market=? AND id=?',(item['market'],pid)).fetchone() or
        not c.execute("SELECT 1 FROM records WHERE kind='product' AND market=? AND id=?",(item['market'],pid)).fetchone()):
        listing_trash.reset_collected_data(c,item['market'],pid)
        c.execute('DELETE FROM listing_trash WHERE market=? AND id=?',(item['market'],pid))
    return True


def save_regional_unavailable(server,c,item,result):
    pid=str(result.get('product_id') or item['product_id']);market=item['market'];stamp=server.now()
    if not pid:return # An unresolved short link remains recorded in its task.
    message=tiktok_collect.REGIONAL_UNAVAILABLE
    prior=c.execute("SELECT s.raw FROM records r LEFT JOIN snapshots s ON s.id=r.snapshot_id WHERE r.kind='product' AND r.market=? AND r.id=?",(market,pid)).fetchone()
    raw=json.loads(prior[0]) if prior and prior[0] else dict.fromkeys(server.PHEAD,'')
    raw.update({'Product Status':message,'Country/Region':market,'TikTok':item['url']})
    if not raw.get('Product Name'):raw['Product Name']='商品 '+pid+'（本地区不售）'
    snap=c.execute("SELECT id FROM snapshots WHERE batch_id=? AND kind='product' AND market=? AND entity_id=?",(item['batch_id'],market,pid)).fetchone()
    if snap:
        sid=snap[0];c.execute('UPDATE snapshots SET raw=? WHERE id=?',(json.dumps(raw,ensure_ascii=False),sid))
    else:
        sid=c.execute("INSERT INTO snapshots(batch_id,kind,market,entity_id,row_number,raw,normalized) VALUES(?,'product',?,?,?,?,?)",(item['batch_id'],market,pid,item['position'],json.dumps(raw,ensure_ascii=False),'{}')).lastrowid
    c.execute("INSERT INTO records(kind,market,id,name,snapshot_id) VALUES('product',?,?,?,?) ON CONFLICT(kind,market,id) DO UPDATE SET snapshot_id=excluded.snapshot_id",(market,pid,raw['Product Name'],sid))
    c.execute('INSERT OR REPLACE INTO tiktok_product_availability VALUES(?,?,?,?,?,?)',(market,pid,'regional_unavailable',message,item['url'],stamp))
    add_collected_draft(server,c,item,pid)
    c.execute("UPDATE batches SET imported=(SELECT count(*) FROM snapshots WHERE batch_id=? AND kind='product') WHERE id=?",(item['batch_id'],item['batch_id']))


def save_product(server,c,item,result):
    p=result['product'];quality=result['quality'];images=result['images'];market=item['market'];pid=str(p['product_id']);stamp=server.now()
    c.execute('DELETE FROM tiktok_product_availability WHERE market=? AND product_id=?',(market,pid))
    old_capture=c.execute('SELECT metadata_ready,product_json FROM product_capture WHERE market=? AND product_id=?',(market,pid)).fetchone()
    # An incomplete retry must never replace a previously complete capture.
    if old_capture and old_capture[0] and quality['missing']:
        add_collected_draft(server,c,item,pid)
        return
    prior=c.execute("SELECT s.raw,r.shop_id,r.shop_name FROM records r JOIN snapshots s ON s.id=r.snapshot_id WHERE r.kind='product' AND r.market=? AND r.id=?",(market,pid)).fetchone()
    raw=dict.fromkeys(server.PHEAD,'')
    if prior:raw.update(json.loads(prior[0]))
    price=p.get('price') or {};display=price.get('real_price') or price.get('sale_price_format') or price.get('min_sku_price') or ''
    if not display and quality['variants']:display=quality['variants'][0]['price'] or ''
    seller_id=str(p.get('seller_id') or '')
    shop_name=p.get('shop_name') or ''
    if prior and not shop_name and (not seller_id or seller_id==prior['shop_id']):
        shop_name=prior['shop_name'] or ''
    title=listing_editor.clean_title(p['title'],shop_name)
    raw.update({'Product Name':p['title'],'Country/Region':market,'Store Name':shop_name,'TikTok':item['url'],'Product Image':images[0]['url'] if images else '', 'Product Category':p.get('category') or raw.get('Product Category',''),'Selling Price / Price':str(display)})
    # Same task/product updates its own snapshot rather than manufacturing retry history.
    snap=c.execute("SELECT id FROM snapshots WHERE batch_id=? AND kind='product' AND market=? AND entity_id=?",(item['batch_id'],market,pid)).fetchone()
    if snap:
        sid=snap[0];c.execute('UPDATE snapshots SET raw=?,normalized=? WHERE id=?',(json.dumps(raw,ensure_ascii=False),'{}',sid))
    else:
        sid=c.execute("INSERT INTO snapshots(batch_id,kind,market,entity_id,row_number,raw,normalized) VALUES(?,'product',?,?,?,?,?)",(item['batch_id'],market,pid,item['position'],json.dumps(raw,ensure_ascii=False),'{}')).lastrowid
    c.execute("""INSERT INTO records(kind,market,id,name,shop_id,shop_name,image,category,price,snapshot_id)
      VALUES('product',?,?,?,?,?,?,?,?,?) ON CONFLICT(kind,market,id) DO UPDATE SET
      name=excluded.name,shop_id=excluded.shop_id,shop_name=excluded.shop_name,image=excluded.image,category=excluded.category,price=excluded.price,snapshot_id=excluded.snapshot_id""",(market,pid,title,seller_id or (prior['shop_id'] if prior else ''),shop_name,raw['Product Image'],raw['Product Category'],raw['Selling Price / Price'],sid))
    c.execute('''INSERT INTO product_collection VALUES(?,?,?,?,?,?) ON CONFLICT(market,product_id) DO UPDATE SET sku_json=excluded.sku_json,price_json=excluded.price_json,source_url=excluded.source_url,updated_at=excluded.updated_at''',(market,pid,json.dumps(dict(sale_props=p.get('sale_props',[]),skus=p.get('skus',[]),specifications=p.get('specifications',[])),ensure_ascii=False),json.dumps(price,ensure_ascii=False),item['url'],stamp))
    c.execute('''INSERT INTO product_capture VALUES(?,?,?,?,?,?) ON CONFLICT(market,product_id) DO UPDATE SET product_json=excluded.product_json,audit_json=excluded.audit_json,metadata_ready=excluded.metadata_ready,received_at=excluded.received_at''',(market,pid,json.dumps(p,ensure_ascii=False),json.dumps(quality,ensure_ascii=False),int(not quality['missing']),stamp))
    # Allocate/reconcile local SKUs in the same transaction as the capture.
    # A failed capture transaction therefore cannot leave orphaned allocations.
    sku_manager.ensure_for_capture(c,market,pid,title=p.get('title',''),source=p)
    c.execute("""INSERT INTO gallery_jobs(market,product_id,url,state,created_at,updated_at,discovered,cached,coverage) VALUES(?,?,?,'saved',?,?,?,?,?) ON CONFLICT(market,product_id) DO UPDATE SET url=excluded.url,state='saved',updated_at=excluded.updated_at,discovered=excluded.discovered,cached=excluded.cached,coverage=excluded.coverage""",(market,pid,item['url'],stamp,stamp,len(images),quality['cached'],quality['scope']))
    gid=c.execute('SELECT id FROM gallery_jobs WHERE market=? AND product_id=?',(market,pid)).fetchone()[0]
    c.execute('DELETE FROM gallery_items WHERE job_id=?',(gid,))
    for index,img in enumerate(images):
        c.execute('''INSERT INTO images(market,product_id,url,local_path,state,error,batch_id) VALUES(?,?,?,?,?,?,?) ON CONFLICT(market,product_id,url) DO UPDATE SET local_path=COALESCE(excluded.local_path,images.local_path),state=CASE WHEN excluded.local_path IS NOT NULL THEN excluded.state ELSE images.state END,error=excluded.error''',(market,pid,img['url'],img.get('local_path'),'已缓存' if img.get('local_path') else '下载失败',img.get('error'),item['batch_id']))
        c.execute('INSERT INTO gallery_items VALUES(?,?,?,?)',(gid,img['url'],index,img['group_name']))
    add_collected_draft(server,c,item,pid)
    c.execute("UPDATE batches SET imported=(SELECT count(*) FROM snapshots WHERE batch_id=? AND kind='product') WHERE id=?",(item['batch_id'],item['batch_id']))


class Service:
    def __init__(self,server,runner=None,pace=DEFAULT_PACE):
        self.server=server;self.runner=runner or self.run_child;self.pace=pace
        self.stop_event=threading.Event();self.wake=threading.Event();self.thread=None;self.process=None;self.last_error=''
        self.browser_state='closed'
        self.category_trees={}
        self.browser=browser_capture.BrowserSession(server.DATA,tiktok_collect.CHROME)
    def browser_control(self,action):
        if action!='open':raise ValueError('请保留采集窗口；验证码通过后会自动继续。需要结束时请停止任务。')
        if not self.browser.endpoint:raise ValueError('开始采集后会自动打开共用会话的采集窗口')
        self.browser.show()
        return {'state':self.browser_state}
    def recover(self,pause=False):
        with self.server._request_lock,self.server.db() as c:
            c.execute("UPDATE tiktok_items SET state='pending',token=NULL,stage='服务重启，继续采集',attempts=max(0,attempts-1) WHERE state='running'")
            if pause:c.execute("UPDATE tiktok_jobs SET state='paused' WHERE state='running'")
    def start(self):
        self.recover();self.thread=threading.Thread(target=self.loop,name='tiktok-queue',daemon=True);self.thread.start()
    def claim(self):
        with self.server._request_lock,self.server.db() as c:
            finish_jobs(c,self.server.now())
            row=c.execute("""SELECT i.*,j.batch_id FROM tiktok_items i JOIN tiktok_jobs j ON j.id=i.job_id JOIN batches b ON b.id=j.batch_id
              WHERE j.state='running' AND i.state IN ('pending','retry')
              AND NOT EXISTS (SELECT 1 FROM tiktok_items busy WHERE busy.state='running')
              AND (i.verification_hold=1 OR NOT EXISTS (
                  SELECT 1 FROM tiktok_items held JOIN tiktok_jobs hj ON hj.id=held.job_id
                  WHERE held.verification_hold=1 AND held.state IN ('pending','retry') AND hj.state IN ('running','paused')))
              ORDER BY i.verification_hold DESC,i.attempts,i.id LIMIT 1""").fetchone()
            if not row:return None
            item=dict(row);item['token']=uuid.uuid4().hex;item['attempts']+=1
            c.execute("UPDATE tiktok_items SET state='running',token=?,attempts=?,stage='正在开始采集',updated_at=? WHERE id=?",(item['token'],item['attempts'],self.server.now(),item['id']))
            return item
    def progress(self,item,info):
        with self.server._request_lock,self.server.db() as c:
            c.execute("""UPDATE tiktok_items SET stage=?,title=COALESCE(?,title),discovered=?,cached=?,
                      verification_hold=?,updated_at=?
                      WHERE id=? AND token=? AND state='running'""",
                      (info.get('stage','正在采集'),info.get('title'),info.get('discovered',0),info.get('cached',0),
                       int(bool(info.get('verification'))),self.server.now(),item['id'],item['token']))

    def set_pacing(self,result):
        with self.server._request_lock,self.server.db() as c:
            c.execute("UPDATE tiktok_pacing SET next_at=0,level=0,successes=0,count=count+1,reason='' WHERE id=1")
    def resolve_category(self,item,result):
        product=result['product']
        if not tiktok_collect.confirmed_leaf(product):
            import shop_listing
            try:
                ctx,selected=shop_listing.identity(self.server.DATA)
                if selected.get('region')==item['market']:
                    key=(item['market'],json.dumps(ctx,sort_keys=True))
                    cached=self.category_trees.get(key)
                    if not cached or cached[0]<=time.time():
                        # Cache failures too; an unavailable taxonomy must not
                        # cause extra API requests for every product in a batch.
                        self.category_trees[key]=(time.time()+300,[])
                        response=shop_listing.call(self.server.DATA,ctx,shop_listing.PREFIX+'categories',
                            params={'category_version':'v2','locale':'en-US'})
                        self.category_trees[key]=(time.time()+3600,response.get('categories',[]))
                    tiktok_collect.confirm_category(product,self.category_trees[key][1])
            except (shop_listing.shop.ShopError,OSError,ValueError):
                pass
        result['quality']=tiktok_collect.audit(product,result['images'])
    def run_child(self,item):
        root=self.server.DATA/'collection-work';root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            folder=Path(tmp);spec=folder/'job.json';result=folder/'result.json';progress=folder/'progress.json'
            browser=self.browser.ensure();browser['resume']=bool(item.get('verification_hold'));self.browser_state='collecting'
            spec.write_text(json.dumps(dict(url=item['url'],product_id=item['product_id'],data=str(self.server.DATA),result=str(result),progress=str(progress),browser=browser)),encoding='utf-8')
            command=([sys.executable,'--collect',str(spec)] if getattr(sys,'frozen',False) else [sys.executable,str(Path(tiktok_collect.__file__)),str(spec)])
            proc=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            self.process=proc;last='';active_elapsed=0;previous=time.monotonic();info={};last_saved=0;last_display=None
            try:
                while proc.poll() is None:
                    if self.stop_event.wait(1):return dict(error='服务正在关闭，下次启动继续',code='shutdown')
                    if progress.exists():
                        try:
                            current=progress.read_text(encoding='utf-8')
                            if current!=last:
                                info=json.loads(current);last=current
                                display={k:v for k,v in info.items() if k!='heartbeat'}
                                if display!=last_display or time.monotonic()-last_saved>=15:
                                    self.progress(item,info);last_display=display;last_saved=time.monotonic()
                        except (ValueError,OSError):pass
                    now=time.monotonic()
                    waiting=bool(info.get('verification')) and time.time()-info.get('heartbeat',0)<15
                    if not waiting:active_elapsed+=now-previous
                    previous=now;self.browser_state='verification' if waiting else 'collecting'
                    with self.server.db() as c:
                        live=c.execute("SELECT 1 FROM tiktok_items WHERE id=? AND token=? AND state='running'",(item['id'],item['token'])).fetchone()
                    if not live:return dict(error='任务已停止',code='cancelled')
                    if active_elapsed>tiktok_collect.ITEM_TIMEOUT:return dict(error='单条采集超时，已继续处理后续链接',code='timeout')
                if not result.exists():return dict(error='采集进程异常退出，稍后重试',code='worker')
                return json.loads(result.read_text(encoding='utf-8'))
            finally:
                platform_support.stop_process(proc)
                proc.wait();self.process=None;self.browser_state='ready'
    def acknowledge(self,item,result):
        with self.server._request_lock,self.server.db() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute("SELECT i.* FROM tiktok_items i JOIN tiktok_jobs j ON j.id=i.job_id JOIN batches b ON b.id=j.batch_id WHERE i.id=? AND i.token=? AND i.state='running' AND j.state IN ('running','paused')",(item['id'],item['token'])).fetchone()
            if not current:return
            pid=str(result.get('product',{}).get('product_id') or result.get('product_id') or item['product_id'])
            if ('product' in result or result.get('code')=='regional_unavailable') and not prepare_collection(c,item,pid):
                c.execute("UPDATE tiktok_items SET state='cancelled',token=NULL,verification_hold=0,error_code='product_deleted',stage='商品已删除',updated_at=? WHERE id=?",(self.server.now(),item['id']))
                finish_jobs(c,self.server.now())
                return
            if result.get('code')=='regional_unavailable':
                save_regional_unavailable(self.server,c,item,result)
                c.execute("""UPDATE tiktok_items SET state='complete',next_at=0,token=NULL,verification_hold=0,
                    error=NULL,error_code='regional_unavailable',missing='[]',stage=?,product_id=?,updated_at=? WHERE id=?""",
                    (tiktok_collect.REGIONAL_UNAVAILABLE,result.get('product_id') or item['product_id'],self.server.now(),item['id']))
                c.execute('UPDATE tiktok_jobs SET updated_at=? WHERE id=?',(self.server.now(),item['job_id']))
                finish_jobs(c,self.server.now())
                return
            interrupted=(current['verification_hold'] or result.get('code')=='verification')
            if interrupted and 'product' not in result and result.get('code') not in ('unavailable','identity'):
                c.execute("""UPDATE tiktok_items SET state='retry',verification_hold=1,attempts=max(0,attempts-1),
                          next_at=?,token=NULL,stage='正在恢复当前商品验证窗口',error=?,error_code='verification',
                          updated_at=? WHERE id=?""",(0,result.get('error'),self.server.now(),item['id']))
                return
            c.execute('UPDATE tiktok_items SET verification_hold=0 WHERE id=?',(item['id'],))
            if 'product' in result:result['quality']=tiktok_collect.audit(result['product'],result['images'])
            missing=result.get('quality',{}).get('missing',[]);error=result.get('error') or '；'.join(missing);code=result.get('code') or ('incomplete' if missing else '')
            title=None
            if 'product' in result:
                save_product(self.server,c,item,result)
                saved=c.execute("SELECT name FROM records WHERE kind='product' AND market=? AND id=?",(item['market'],str(result['product']['product_id']))).fetchone()
                title=saved['name']
            terminal='partial' if 'product' in result or current['title'] else 'failed'
            state='complete' if not error else ('retry' if item['attempts']<MAX_ATTEMPTS else terminal)
            q=result.get('quality',{})
            c.execute("UPDATE tiktok_items SET state=?,next_at=?,token=NULL,error=?,error_code=?,stage=?,title=COALESCE(?,title),product_id=?,discovered=?,cached=?,missing=?,updated_at=? WHERE id=?",(state,0,error or None,code or None,'继续处理队列，随后重试' if state=='retry' else '页面资料已保存' if state=='complete' else '需补采',title,result.get('product',{}).get('product_id') or item['product_id'],q.get('discovered',current['discovered']),q.get('cached',current['cached']),json.dumps(missing,ensure_ascii=False) if 'quality' in result else current['missing'],self.server.now(),item['id']))
            c.execute('UPDATE tiktok_jobs SET updated_at=? WHERE id=?',(self.server.now(),item['job_id']))
            finish_jobs(c,self.server.now())
        if 'product' in result:
            library_categories.notify_saved()
    def step(self):
        item=self.claim()
        if not item:return False
        try:
            result=self.runner(item)
        except Exception as exc:result=dict(error='采集进程异常：'+str(exc)[:300],code='worker')
        self.set_pacing(result)
        if not self.stop_event.is_set():
            try:self.acknowledge(item,result)
            except Exception as exc:
                # Save failures roll back the full product, then release the item for retry.
                self.acknowledge(item,dict(error='保存失败：'+str(exc)[:300],code='storage'))
        return True
    def loop(self):
        while not self.stop_event.is_set():
            try:
                worked=self.step()
                self.last_error=''
            except Exception as exc:
                self.last_error=str(exc)[:300];worked=False
                # No subprocess is active here. Release a stranded claim after a DB error.
                try:
                    with self.server._request_lock,self.server.db() as c:
                        c.execute("UPDATE tiktok_items SET state='retry',token=NULL,next_at=?,stage='保存暂不可用，稍后重试' WHERE state='running'",(0,))
                except Exception:pass
            # Waking the worker for a new submission never bypasses claim's gate.
            if not worked:
                self.wake.wait(2);self.wake.clear()
    def stop(self):
        self.stop_event.set();self.wake.set()
        if self.thread:self.thread.join(timeout=5)
        # Keep the dedicated Chrome alive so a service restart can reattach to the same session.
