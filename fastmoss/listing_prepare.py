"""Short database phases and cancellable, single-worker listing image preparation."""
import copy
import subprocess
import threading
import time
import uuid
from contextlib import closing

import listing_editor
import listing_image_roles
import shop_listing
import tiktok_shop


class PreparationStopped(RuntimeError):
    pass


def priority_keys(images, form):
    usable=[im for im in images if im.get('role') not in ('size_chart','reminder') and not im.get('hidden')]
    main=form.get('main_image_keys',[im['key'] for im in usable if im.get('group_name')=='商品图'][:9])
    detail=form.get('description_image_keys',[im['key'] for im in usable if im['key'] not in main][:30])
    keys=set(main)|set(detail)|set((form.get('certifications') or {}).values())
    if not form.get('size_chart_template_id'):keys.add(form.get('size_chart'))
    return keys


def version(c, item):
    key=(item['market'],item['id'])
    row=c.execute('''SELECT r.snapshot_id,cap.received_at,
        (SELECT MAX(id) FROM draft_events WHERE market=r.market AND product_id=r.id)
        FROM records r JOIN draft_entries d ON d.market=r.market AND d.id=r.id
        LEFT JOIN product_capture cap ON cap.market=r.market AND cap.product_id=r.id
        WHERE r.kind='product' AND d.active=1 AND r.market=? AND r.id=?''',key).fetchone()
    if not row: return None
    return shop_listing.digest([list(row),listing_editor.revision(c,*key),shop_listing.profile(c,*key)])


class Service:
    def __init__(self, server, recognize=None):
        self.server=server
        self.recognize=recognize or listing_image_roles.annotate
        self.lock=threading.RLock()
        self.wake=threading.Event()
        self.jobs={}
        self.thread=None
        self.stopping=False

    def _shop(self):
        return tiktok_shop.public_status(self.server.DATA)['selected_shop_id']

    def start(self, items, retry_id=None):
        if not isinstance(items,list) or not 1<=len(items)<=100:
            raise ValueError('每次请选择 1～100 件商品')
        keys=[]
        for item in items:
            if not isinstance(item,dict) or not all(isinstance(item.get(k),str) and item[k] for k in ('market','id')):
                raise ValueError('商品标识无效')
            key={k:item[k] for k in ('market','id')}
            if key not in keys: keys.append(key)
        with self.server._request_lock:
            shop_id=self._shop()
            with closing(self.server.db()) as c, c: versions=[version(c,item) for item in keys]
            with self.lock:
                now=time.time()
                self.jobs={k:j for k,j in self.jobs.items() if not j.get('finished_at') or now-j['finished_at']<1800}
                if retry_id:
                    job=self.jobs.get(retry_id)
                    if not job: raise ValueError('准备任务已过期或服务已重启，请重新准备')
                    if job['shop_id']!=shop_id: raise ValueError('目标店铺已改变，请重新准备')
                    if job['state'] in ('queued','running'): return self._status(job)
                    job['run']+=1
                    job['rows']=[dict(row) for row in job['rows']]
                    for row,rev in zip(job['rows'],versions):
                        if row['state']!='ready' or row['version']!=rev:
                            row.update(state='pending',version=rev,error='',issues=[],result=None,stage='等待重试')
                    job.update(state='queued',finished_at=None,error='')
                else:
                    for job in self.jobs.values():
                        if job['shop_id']==shop_id and job['items']==keys and job['state'] in ('queued','running'):
                            return self._status(job)
                    jid=uuid.uuid4().hex
                    job=dict(id=jid,items=keys,shop_id=shop_id,state='queued',run=0,created_at=now,finished_at=None,error='',
                        rows=[dict(**item,version=rev,state='pending',title='',stage='等待准备',error='',issues=[],result=None) for item,rev in zip(keys,versions)])
                    self.jobs[jid]=job
                if not self.thread or not self.thread.is_alive():
                    self.stopping=False
                    self.thread=threading.Thread(target=self._loop,name='listing-prepare',daemon=True)
                    self.thread.start()
                self.wake.set()
                return self._status(job)

    def retry(self, jid):
        with self.lock:
            job=self.jobs.get(jid)
            if not job: raise ValueError('准备任务已过期或服务已重启，请重新准备')
            items=copy.deepcopy(job['items'])
        return self.start(items,retry_id=jid)

    def _status(self, job):
        rows=[{k:r[k] for k in ('market','id','title','state','stage','error','issues')} for r in job['rows']]
        return dict(id=job['id'],state=job['state'],total=len(rows),completed=sum(r['state']=='ready' for r in rows),
            failed=sum(r['state'] in ('failed','cancelled') for r in rows),rows=rows,error=job['error'],
            elapsed=int((job.get('finished_at') or time.time())-job['created_at']))

    def status(self, jid, result=False):
        with self.server._request_lock:
            with self.lock:
                job=self.jobs.get(jid)
                if not job or (job.get('finished_at') and time.time()-job['finished_at']>=1800):
                    raise ValueError('准备任务已过期或服务已重启，请重新准备；已完成的图片识别缓存仍可复用')
            if result:
                with closing(self.server.db()) as c, c:
                    revisions=[version(c,r) for r in job['rows']]
                    connection=tiktok_shop.public_status(self.server.DATA)
                    templates=shop_listing.templates(c,connection['selected_shop_id'])
                    sizes=shop_listing.size_templates(c)
                with self.lock:
                    for row,rev in zip(job['rows'],revisions):
                        if row['state']=='ready' and (row['version']!=rev or job['shop_id']!=connection['selected_shop_id']):
                            row.update(state='failed',result=None,error='商品资料或目标店铺已改变，请重新准备',stage='结果已失效')
                    if job['state']=='ready' and any(r['state']!='ready' for r in job['rows']):job['state']='partial'
                    response=self._status(job)
                    response['result']=dict(items=[copy.deepcopy(r['result']) for r in job['rows'] if r['state']=='ready'],
                        connection=connection,templates=templates,size_templates=sizes)
                    return response
            with self.lock:return self._status(job)

    def invalidate(self, reason='准备任务已停止，请重新准备'):
        with self.lock:
            for job in self.jobs.values():
                job['run']+=1
                job.update(state='cancelled',error=reason,finished_at=time.time())
                for row in job['rows']:
                    row.update(state='cancelled',result=None,error=reason,stage='已停止')
        self.wake.set()

    def stop(self):
        self.invalidate('服务已停止，请重新准备')
        self.stopping=True
        self.wake.set()
        if self.thread:self.thread.join(timeout=3)

    def _current(self, job, row):
        with self.lock:
            if self.stopping or job['state']=='cancelled' or row['state']=='cancelled' or row.get('run')!=job['run']:
                raise PreparationStopped(job['error'] or '准备任务已停止')
        with self.server._request_lock,closing(self.server.db()) as c, c:
            current=version(c,row)
            if current is None:raise PreparationStopped('商品或来源已删除，已停止准备')
            if current!=row['version']:raise PreparationStopped('商品资料已改变，请重新准备')
            if self._shop()!=job['shop_id']:raise PreparationStopped('目标店铺已改变，请重新准备')

    def _run_process(self, job, row, command, **kwargs):
        timeout=kwargs.pop('timeout',60);check=kwargs.pop('check',False)
        kwargs.pop('capture_output',None)
        payload=kwargs.pop('input',None)
        self._current(job,row)
        with subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kwargs) as proc:
            deadline=time.monotonic()+timeout
            try:
                while True:
                    self._current(job,row)
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise subprocess.TimeoutExpired(command,timeout)
                    try:
                        out,err=proc.communicate(input=payload,timeout=min(.5,remaining))
                        break
                    except subprocess.TimeoutExpired:payload=None
                self._current(job,row)
                if check and proc.returncode:raise subprocess.CalledProcessError(proc.returncode,command,out,err)
                return subprocess.CompletedProcess(command,proc.returncode,out,err)
            finally:
                if proc.poll() is None:proc.kill();proc.communicate()

    def _item(self, job, row):
        try:
            self._current(job,row)
            with self.server._request_lock,closing(self.server.db()) as c, c:
                self._current(job,row)
                p,details,saved=shop_listing.prepare_input(c,row,dict(selected_shop_id=job['shop_id']))
                # SKU allocation is a local preparation write, before the version is frozen.
                revision=version(c,row)
                with self.lock:row.update(version=revision,title=p['title'],stage='正在读取商品资料')
            def progress(stage):
                self._current(job,row)
                with self.lock:row['stage']=stage
            p['images']=listing_image_roles.cached(self.server.DATA,p['images'])
            keys=priority_keys(p['images'],saved)
            priority=[im for im in p['images'] if im['key'] in keys]
            remaining=[im for im in p['images'] if im['key'] not in keys and im.get('ocr_status')!='checked']
            progress('优先识别用于上架的 %d 张图片'%len(priority))
            recognized=self.recognize(self.server.DATA,priority,
                run=lambda command,**kw:self._run_process(job,row,command,**kw),progress=progress)
            by_key={im['key']:im for im in recognized}
            p['images']=[by_key.get(im['key'],im) for im in p['images']]
            self._current(job,row)
            output=shop_listing.prepare_output(self.server.DATA,p,details,saved)
            used=priority_keys(output['images'],output['profile'])
            issues=[dict(image_key=im['key'],message=im.get('ocr_error') or '图片未完成识别')
                    for im in p['images'] if im['key'] in used and im.get('ocr_status')!='checked']
            if issues:
                with self.lock:row['issues']=issues
                raise ValueError('%d 张图片识别未完成：%s' % (len(issues),issues[0]['message']))
            with self.server._request_lock:
                self._current(job,row)
                with self.lock:row.update(state='ready',stage='上架图片已准备；剩余 %d 张将在后台识别'%len(remaining) if remaining else '准备完成',result=output,remaining=remaining)
        except PreparationStopped as exc:
            with self.lock:row.update(state='cancelled',stage='已停止',error=str(exc),result=None)
        except Exception as exc:
            with self.lock:row.update(state='failed',error=str(exc) or type(exc).__name__,result=None)

    def _remaining(self, job, row):
        images=row.pop('remaining',[])
        if not images:return
        try:
            self._current(job,row)
            recognized=self.recognize(self.server.DATA,images,run=lambda command,**kw:self._run_process(job,row,command,**kw))
            self._current(job,row)
            with self.lock:
                result=row['result'];by_key={im['key']:im for im in recognized}
                result['images']=listing_image_roles.mark_matches([by_key.get(im['key'],im) for im in result['images']],result.get('content_review',{}).get('terms',[]))
                result['size_chart_candidates']=shop_listing.size_chart_candidates(self.server.DATA,result['images'])
                failed=sum(im.get('ocr_status')!='checked' for im in recognized)
                row['stage']='上架资料已准备；%d 张未使用图片识别失败，不影响当前选图'%failed if failed else '全部图片识别完成'
        except Exception as exc:
            with self.lock:row['stage']='上架资料已准备；未使用图片后台识别已停止：'+str(exc)

    def _loop(self):
        while not self.stopping:
            with self.lock:
                job=next((j for j in self.jobs.values() if j['state']=='queued'),None)
                if job:job['state']='running';run=job['run'];rows=job['rows']
            if not job:
                self.wake.wait(1);self.wake.clear();continue
            for row in rows:
                with self.lock:
                    if job['state']=='cancelled' or self.stopping or run!=job['run']:break
                    if row['state']!='pending':continue
                    row['state']='running';row['run']=run
                self._item(job,row)
            with self.lock:
                if job['state']!='cancelled' and run==job['run']:
                    job['state']='ready' if all(r['state']=='ready' for r in job['rows']) else 'partial'
                    job['finished_at']=time.time()
            for row in rows:
                if row['state']=='ready':self._remaining(job,row)


def service(server):
    # HTTP dispatch already holds the server request lock.
    if not getattr(server,'listing_preparer',None):server.listing_preparer=Service(server)
    return server.listing_preparer
