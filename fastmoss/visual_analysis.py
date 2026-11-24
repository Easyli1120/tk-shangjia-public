"""Durable, append-only multimodal analysis queue; never calls an image model."""
import base64
import datetime
import platform_support
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import uuid

import product_assets
import model_manager as models
from tt_adapter import TTAdapter, TTError, safe_error

PROMPT_VERSION='visual-v1'
PROMPTS=Path(__file__).parent/'prompts'/PROMPT_VERSION
MODES=json.loads((PROMPTS/'modes.json').read_text(encoding='utf-8'))
SCHEMA=json.loads((PROMPTS/'schema.json').read_text(encoding='utf-8'))
PROMPT_DIGEST=hashlib.sha256(b''.join((PROMPTS/name).read_bytes() for name in ('system.md','modes.json','schema.json'))).hexdigest()


def dump(value):return json.dumps(value,ensure_ascii=False)
def now():return models.stamp()


def migrate(c):
    models.migrate(c)
    c.executescript((Path(__file__).parent/'migrations'/'003_visual_analysis.sql').read_text(encoding='utf-8'))


def product(c,market,pid):
    row=c.execute("SELECT market,id,name FROM records WHERE kind='product' AND market=? AND id=?",(market,pid)).fetchone()
    if not row:raise ValueError('商品不存在')
    return dict(row)


def image_path(data,row):
    rel=row.get('local_path') or ''
    p=(Path(data)/rel.lstrip('/')).resolve()
    if not rel.startswith('/images/') or not p.is_relative_to((Path(data)/'images').resolve()) or not p.is_file():
        raise ValueError('图片 #%s 尚未缓存或文件不可用，请先在商品编辑中上传图片'%row['id'])
    return p


def inventory(c,data,market,pid):
    item=product(c,market,pid)
    images=[]
    for row in c.execute('SELECT id,url,local_path,state FROM images WHERE market=? AND product_id=? ORDER BY id',(market,pid)):
        im=dict(row)
        try:
            p=image_path(data,im)
            im['available']=True
            im['bytes']=p.stat().st_size
        except ValueError:
            im['available']=False
        images.append(im)
    return {'product':item,'images':images,'total':len(images),'available':sum(im['available'] for im in images),
            'note':'展示数据库内该商品的全部主图、详情、SKU 和其他已采集图片；源数据未标注类型时不猜测分类。未缓存图片不会静默跳过。'}


def signature(cfg):
    return hashlib.sha256(dump([cfg['base_url'],cfg['model_id'],hashlib.sha256(cfg['api_key'].encode()).hexdigest()]).encode()).hexdigest()


def validate_result(content,pid,count):
    try:
        content=content.strip()
        if content.startswith('```'):content=content.split('\n',1)[1].rsplit('```',1)[0]
        result=json.loads(content)
        if not isinstance(result,dict):raise ValueError()
        for key,typ in SCHEMA.items():
            if key not in result:raise ValueError('缺少 '+key)
            if key in ('product_id','image_count'):continue
            if not isinstance(result[key],list if isinstance(typ,list) else str):raise ValueError('字段类型无效 '+key)
        for key in ('visual_style','design_description','confidence'):
            if not result[key].strip():raise ValueError('空字段 '+key)
        result['product_id']=pid;result['image_count']=count
        return result
    except (ValueError,TypeError,KeyError) as exc:
        raise TTError('TT 结构化结果校验失败：'+str(exc)[:150]) from None


def prepare_image(path):
    content=path.read_bytes()
    if len(content)>25*1024*1024:raise ValueError('图片超过 25 MB，请先压缩源图')
    ext=product_assets.image_extension(content)
    if ext not in ('.jpg','.jpeg','.png','.webp'):raise ValueError('视觉模型仅支持 JPEG / PNG / WebP 图片')
    # Prefer macOS's bundled image processor; no third-party Python dependencies.
    if Path('/usr/bin/sips').exists():
        with tempfile.TemporaryDirectory(prefix='fastmoss-vision-') as folder:
            src=Path(folder)/('source'+ext);out=Path(folder)/'input.jpg'
            src.write_bytes(content)
            p=subprocess.run(['/usr/bin/sips','-s','format','jpeg','-s','formatOptions','82','-Z','1600',str(src),'--out',str(out)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            if p.returncode==0 and out.is_file():
                converted=out.read_bytes()
                if len(converted)<len(content) or ext=='.webp':content=converted;ext='.jpg'
    if not Path('/usr/bin/sips').exists():
        content=platform_support.thumbnail_jpeg(content);ext='.jpg'
    if len(content)>5*1024*1024:raise ValueError('图片压缩后仍超过 5 MB；请安装可用的 sips 或预先压缩图片')
    mime={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp'}[ext]
    return 'data:'+mime+';base64,'+base64.b64encode(content).decode()


def job(c,id_):
    row=c.execute('SELECT * FROM visual_analysis_jobs WHERE id=?',(id_,)).fetchone()
    if not row:raise ValueError('分析记录不存在')
    d=dict(row)
    for key in ('source_images','result','usage'):d[key]=json.loads(d[key]) if d[key] else None
    d.pop('model_signature',None);d.pop('fingerprint',None)
    d['batches']=[dict(r) for r in c.execute('SELECT id,position,image_ids,state,error FROM visual_analysis_batches WHERE job_id=? ORDER BY position',(id_,))]
    d['prompt_versions']=[dict(r) for r in c.execute('SELECT * FROM visual_prompt_versions WHERE job_id=? ORDER BY id DESC',(id_,))]
    d['generations']=[dict(r) for r in c.execute('SELECT * FROM visual_generation_links WHERE analysis_id=? ORDER BY id DESC',(id_,))]
    return d


def history(c,market,pid):
    return [dict(r) for r in c.execute('''SELECT id,mode,model,state,image_count,progress,error,cached_from,usage,created_at,prompt_version
        FROM visual_analysis_jobs WHERE market=? AND product_id=? ORDER BY created_at DESC LIMIT 200''',(market,pid))]


def records(c,query):
    """Paginated cross-product ledger; counts come from stored task relationships."""
    page=max(1,int(query.get('page',['1'])[0]));limit=min(100,max(1,int(query.get('limit',['20'])[0])))
    search=query.get('q',[''])[0].strip();state=query.get('state',[''])[0]
    where=[];args=[]
    if search:
        where.append('(j.product_name LIKE ? OR j.product_id LIKE ? OR j.market LIKE ?)')
        args.extend(['%'+search+'%']*3)
    if state:
        if state not in ('queued','running','done','failed','interrupted'):raise ValueError('未知分析状态')
        where.append('j.state=?');args.append(state)
    base=' FROM visual_analysis_jobs j'+(' WHERE '+' AND '.join(where) if where else '')
    summary=dict(c.execute('''SELECT count(*) tasks,
        count(DISTINCT json_array(j.market,j.product_id)) products,
        coalesce(sum(j.state='done'),0) completed,
        coalesce(sum(j.state IN ('queued','running')),0) active,
        coalesce(sum(j.state IN ('failed','interrupted')),0) failed'''+base,args).fetchone())
    page=min(page,max(1,(summary['tasks']+limit-1)//limit))
    fields='''SELECT j.id,j.market,j.product_id,j.product_name,j.kind,j.mode,j.model,j.state,
        j.image_count,j.progress,j.error,j.cached_from,j.created_at,j.updated_at,
        (j.result IS NOT NULL) report_ready,
        (SELECT count(*) FROM visual_analysis_batches b WHERE b.job_id=j.id) batch_count,
        (SELECT count(*) FROM visual_analysis_batches b WHERE b.job_id=j.id AND b.state='done') batches_done,
        (SELECT count(*) FROM visual_prompt_versions p WHERE p.job_id=j.id) prompt_count,
        (SELECT count(*) FROM visual_generation_links g WHERE g.analysis_id=j.id) generation_count,
        (SELECT count(*) FROM innovation_jobs n JOIN visual_generation_links g ON g.generation_id=n.id
            WHERE g.analysis_id=j.id AND n.state IN ('queued','running','retry_wait')) generation_active,
        (SELECT count(*) FROM innovation_images i JOIN visual_generation_links g ON g.generation_id=i.job_id
            WHERE g.analysis_id=j.id) generated_images,
        (SELECT count(*) FROM innovation_images i JOIN visual_generation_links g ON g.generation_id=i.job_id
            WHERE g.analysis_id=j.id AND i.saved_at IS NOT NULL AND i.image_type='mockup') saved_mockups'''
    items=[dict(r) for r in c.execute(fields+base+' ORDER BY j.created_at DESC,j.id DESC LIMIT ? OFFSET ?',args+[limit,(page-1)*limit])]
    return {'items':items,'total':summary['tasks'],'page':page,'limit':limit,'summary':summary}


def usage_sum(usages):
    totals={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0,'missing_usage_calls':0}
    for u in usages:
        if not isinstance(u,dict) or not isinstance(u.get('total_tokens'),(int,float)):
            totals['missing_usage_calls']+=1;continue
        for key in ('prompt_tokens','completion_tokens','total_tokens'):
            value=u.get(key,0)
            if isinstance(value,(int,float)):totals[key]+=value
    return totals


def stats(c):
    today=datetime.datetime.now().astimezone().date().isoformat()
    rows=[dict(r) for r in c.execute('SELECT usage,created_at,outcome FROM visual_api_calls')]
    def summarize(items):
        return {'calls':len(items),'successful_calls':sum(r['outcome']=='success' for r in items),
                **usage_sum([json.loads(r['usage']) if r['usage'] else None for r in items])}
    day=summarize([r for r in rows if r['created_at'][:10]==today]);total=summarize(rows)
    budget=models.budget(c)
    return {'today':day,'total':total,'daily_token_limit':budget['daily_token_limit'],
            'budget_warning':bool(budget['daily_token_limit'] and day['total_tokens']>=budget['daily_token_limit']),
            'cost':None,'cost_note':'未配置模型价格，仅统计实际返回的 Token；缺失用量单独标记。',
            'queue':{r['state']:r['n'] for r in c.execute('SELECT state,count(*) n FROM visual_analysis_jobs GROUP BY state')}}


def save_prompt(c,body):
    j=job(c,body.get('job_id'))
    if j['state']!='done':raise ValueError('请先完成分析')
    task=body.get('task_type','design')
    if task not in ('design','mockup'):raise ValueError('生图类型无效')
    result=j['result'];prefix='standalone_print' if task=='design' else 'mockup'
    values=[]
    for key,default in [('prompt_en',result[prefix+'_prompt_en']),('prompt_zh',result[prefix+'_prompt_zh']),('negative_prompt',result['negative_prompt'])]:
        value=body.get(key,default)
        if not isinstance(value,str) or len(value)>16000:raise ValueError('Prompt 过长或格式无效')
        values.append(value)
    if not values[0].strip():raise ValueError('英文 Prompt 不能为空')
    id_=c.execute('INSERT INTO visual_prompt_versions(job_id,task_type,prompt_en,prompt_zh,negative_prompt,created_at) VALUES(?,?,?,?,?,?)',(j['id'],task,*values,now())).lastrowid
    return dict(c.execute('SELECT * FROM visual_prompt_versions WHERE id=?',(id_,)).fetchone())


class Service:
    def __init__(self,server,adapter_factory=TTAdapter):
        self.server,self.adapter_factory=server,adapter_factory
        self.closed,self.wake=threading.Event(),threading.Event()
        self.epoch=0;self.thread=None;self.lockfile=None

    def recover(self):
        with self.server.db() as c:
            c.execute("UPDATE visual_analysis_jobs SET state='failed',error='服务中断；已完成分批结果保留，请手动重试',updated_at=? WHERE state='running'",(now(),))
            c.execute("UPDATE visual_analysis_batches SET state='failed',error='服务中断' WHERE state='running'")

    def invalidate(self):self.epoch+=1
    def stop(self):self.closed.set();self.wake.set()
    def start(self):
        self.lockfile=open(str(self.server.DB)+'.vision.lock','a+')
        platform_support.lock_file(self.lockfile)
        self.recover()
        self.thread=threading.Thread(target=self._loop,daemon=True,name='tt-vision-worker');self.thread.start()

    def submit(self,body):
        market,pid=str(body.get('market','US')),str(body.get('id',''))
        mode=body.get('mode','style');kind=body.get('kind','analysis')
        if mode not in MODES or kind not in ('analysis','connection'):raise ValueError('分析模式无效')
        size=body.get('batch_size',4)
        if type(size) is not int or not 1<=size<=8:raise ValueError('每批请选择 1–8 张图片')
        token=body.get('request_id','')
        if not isinstance(token,str) or not __import__('re').fullmatch(r'[A-Za-z0-9_-]{16,80}',token):raise ValueError('请求标识无效')
        with self.server._request_lock,self.server.db() as c:
            c.execute('BEGIN IMMEDIATE');models.seed(c,self.server.ROOT)
            existing=c.execute('SELECT id FROM visual_analysis_jobs WHERE request_id=?',(token,)).fetchone()
            cfg=models.resolve(c,self.server.ROOT,self.server.DATA,body.get('model_config_id'))
            if not cfg['enabled'] or not cfg['api_key']:raise ValueError('请先在 AI 模型管理配置并启用 TT 视觉模型专用 API Key（TT_API_KEY）')
            inv=inventory(c,self.server.DATA,market,pid);images=inv['images']
            ids=body.get('image_ids')
            if ids is None:ids=[im['id'] for im in images]
            if not isinstance(ids,list) or not ids or len(ids)>500 or any(type(i) is not int for i in ids) or len(set(ids))!=len(ids):raise ValueError('请选择 1–500 张不重复图片；数量更多时请明确分组提交')
            available={im['id']:im for im in images}
            chosen=[]
            for id_ in ids:
                if id_ not in available:raise ValueError('所选图片不属于当前商品')
                im=available[id_];path=image_path(self.server.DATA,im)
                chosen.append({'id':id_,'hash':hashlib.sha256(path.read_bytes()).hexdigest(),'local_path':im['local_path']})
            target=models.resolve(c,self.server.ROOT,self.server.DATA,body.get('target_model_id'),category='image')
            if not target['enabled']:raise ValueError('所选生图模型已停用')
            target_data={k:target[k] for k in ('id','model_id','provider','options')}
            fingerprint=hashlib.sha256(dump([market,pid,[(im['id'],im['hash']) for im in chosen],signature(cfg),mode,kind,PROMPT_VERSION,PROMPT_DIGEST,target_data,size,bool(body.get('stream'))]).encode()).hexdigest()
            if existing:
                prior=c.execute('SELECT fingerprint FROM visual_analysis_jobs WHERE id=?',(existing['id'],)).fetchone()
                if prior['fingerprint']!=fingerprint:raise ValueError('请求标识已使用且内容发生变化，请重新提交')
                return job(c,existing['id'])
            active=c.execute("SELECT id FROM visual_analysis_jobs WHERE fingerprint=? AND state IN ('queued','running') LIMIT 1",(fingerprint,)).fetchone()
            if active:return job(c,active['id'])
            cache=None
            if not body.get('force') and kind=='analysis':
                cache=c.execute("SELECT * FROM visual_analysis_jobs WHERE fingerprint=? AND state='done' ORDER BY created_at DESC LIMIT 1",(fingerprint,)).fetchone()
            id_=uuid.uuid4().hex;folder=self.server.DATA/'visual-analysis'/id_
            folder.mkdir(parents=True)
            try:
                for i,im in enumerate(chosen):
                    src=image_path(self.server.DATA,im)
                    dest=folder/('%04d'%i+src.suffix)
                    shutil.copyfile(src,dest)
                    if hashlib.sha256(dest.read_bytes()).hexdigest()!=im['hash']:raise ValueError('图片在提交期间发生变化，请刷新后重试')
                    im['snapshot']=dest.name
                stamp=now()
                c.execute('''INSERT INTO visual_analysis_jobs(id,market,product_id,product_name,request_id,fingerprint,kind,model_config_id,model,model_signature,mode,prompt_version,prompt_digest,target_model,image_count,source_images,batch_size,stream,state,result,usage,cached_from,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (id_,market,pid,inv['product']['name'] or pid,token,fingerprint,kind,cfg['id'],cfg['model_id'],signature(cfg),mode,PROMPT_VERSION,PROMPT_DIGEST,dump(target_data),len(chosen),dump(chosen),size,int(bool(body.get('stream'))),'done' if cache else 'queued',cache['result'] if cache else None,dump({'cache_hit':True,'total_tokens':0}) if cache else None,cache['id'] if cache else None,stamp,stamp))
                if cache:
                    c.execute('UPDATE visual_analysis_jobs SET progress=image_count WHERE id=?',(id_,))
                else:
                    for i in range(0,len(chosen),size):
                        c.execute('INSERT INTO visual_analysis_batches(job_id,position,image_ids) VALUES(?,?,?)',(id_,i//size,dump(ids[i:i+size])))
                result=job(c,id_)
            except Exception:
                shutil.rmtree(folder,ignore_errors=True);raise
        self.wake.set();return result

    def retry(self,body):
        with self.server._request_lock,self.server.db() as c:
            j=job(c,body.get('job_id'))
            if j['state']!='failed':raise ValueError('只能重试失败任务')
            cfg=models.resolve(c,self.server.ROOT,self.server.DATA,j['model_config_id'])
            row=c.execute('SELECT model_signature,prompt_digest FROM visual_analysis_jobs WHERE id=?',(j['id'],)).fetchone()
            if signature(cfg)!=row['model_signature'] or row['prompt_digest']!=PROMPT_DIGEST:raise ValueError('模型配置或 Prompt 已变化，请新建分析以保留历史可比性')
            if c.execute("SELECT 1 FROM visual_analysis_jobs a JOIN visual_analysis_jobs b ON a.fingerprint=b.fingerprint WHERE b.id=? AND a.state IN ('queued','running')",(j['id'],)).fetchone():raise ValueError('相同分析已在队列中')
            c.execute("UPDATE visual_analysis_jobs SET state='queued',error=NULL,updated_at=? WHERE id=?",(now(),j['id']))
            c.execute("UPDATE visual_analysis_batches SET state='pending',error=NULL WHERE job_id=? AND state!='done'",(j['id'],))
        self.wake.set();return {'id':j['id'],'state':'queued'}

    def _loop(self):
        while not self.closed.is_set():
            try:worked=self.run_one()
            except Exception:worked=False
            if not worked:self.wake.wait(2);self.wake.clear()

    def run_one(self):
        with self.server._request_lock,self.server.db() as c:
            if self.closed.is_set():return False
            row=c.execute("SELECT * FROM visual_analysis_jobs WHERE state='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if not row:return False
            j=dict(row);cfg={}
            epoch=self.epoch
            c.execute("UPDATE visual_analysis_jobs SET state='running',updated_at=? WHERE id=?",(now(),j['id']))
            batches=[dict(r) for r in c.execute('SELECT * FROM visual_analysis_batches WHERE job_id=? ORDER BY position',(j['id'],))]
        try:
            with self.server.db() as c:cfg=models.resolve(c,self.server.ROOT,self.server.DATA,j['model_config_id'])
            if signature(cfg)!=j['model_signature'] or j['prompt_digest']!=PROMPT_DIGEST:raise ValueError('模型配置或 Prompt 版本已改变，请新建分析')
            adapter=self.adapter_factory(cfg)
            selected=json.loads(j['source_images']);target=json.loads(j['target_model'])
            def record(batch_id):
                def log(item):
                    with self.server._request_lock,self.server.db() as c:
                        if epoch!=self.epoch:return
                        c.execute('''INSERT INTO visual_api_calls(job_id,batch_id,attempt,model,status,request_id,outcome,usage,error,duration_ms,created_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(j['id'],batch_id,item['attempt'],j['model'],item.get('status'),item.get('request_id'),item['outcome'],dump(item.get('usage')) if item.get('usage') else None,safe_error(item.get('error',''),cfg['api_key']),item.get('duration_ms'),now()))
                return log
            system=(PROMPTS/'system.md').read_text(encoding='utf-8')+'\nSchema: '+dump(SCHEMA)+'\nMode: '+MODES[j['mode']]['instruction']+'\nTARGET_IMAGE_MODEL: '+dump(target)
            results=[]
            for batch in batches:
                if self.closed.is_set() or epoch!=self.epoch:return True
                if batch['state']=='done':results.append(json.loads(batch['result']));continue
                with self.server.db() as c:c.execute("UPDATE visual_analysis_batches SET state='running' WHERE id=?",(batch['id'],))
                ids=json.loads(batch['image_ids']);images=[im for im in selected if im['id'] in ids]
                content=[{'type':'text','text':'分析下列真实商品图片；不要从标题猜测。商品 ID '+j['product_id']+'；本批 '+str(len(images))+' 张。原始图片 ID 顺序：'+dump(ids)}]
                for im in images:
                    path=self.server.DATA/'visual-analysis'/j['id']/im['snapshot']
                    if hashlib.sha256(path.read_bytes()).hexdigest()!=im['hash']:raise ValueError('图片快照校验失败，请重新分析')
                    content.extend([{'type':'text','text':'图片 ID '+str(im['id'])},{'type':'image_url','image_url':{'url':prepare_image(path),'detail':'high'}}])
                response=adapter.chat([{'role':'system','content':system},{'role':'user','content':content}],stream=bool(j['stream']),on_attempt=record(batch['id']))
                result=validate_result(response['content'],j['product_id'],len(images));results.append(result)
                with self.server._request_lock,self.server.db() as c:
                    if epoch!=self.epoch:return True
                    c.execute("UPDATE visual_analysis_batches SET state='done',result=?,usage=?,error=NULL WHERE id=?",(dump(result),dump(response.get('usage')),batch['id']))
                    c.execute('UPDATE visual_analysis_jobs SET progress=?,updated_at=? WHERE id=?',(sum(r['image_count'] for r in results),now(),j['id']))
            if len(results)>1:
                messages=[{'role':'system','content':system},
                  {'role':'user','content':'以下是同一商品全部 '+str(j['image_count'])+' 张图片的分批真实视觉观察，请合并为一个完整 JSON 报告，不丢失分歧或不确定项；不新增不存在的细节。'},
                  {'role':'assistant','content':dump(results)},
                  {'role':'user','content':'综合上述各批次，保持所有 schema 字段，并在 observations 列出覆盖到的原始图片 ID。'}]
                if len(dump(messages))>400000:raise ValueError('分批报告超过合并上下文限制，请减少单次提交图片数量；已完成各批结果已保存')
                response=adapter.chat(messages,stream=bool(j['stream']),on_attempt=record(None))
                result=validate_result(response['content'],j['product_id'],j['image_count'])
            else:result=results[0]
            with self.server._request_lock,self.server.db() as c:
                if epoch!=self.epoch:return True
                usage=usage_sum([json.loads(r[0]) if r[0] else None for r in c.execute('SELECT usage FROM visual_api_calls WHERE job_id=?',(j['id'],))])
                c.execute("UPDATE visual_analysis_jobs SET state='done',result=?,usage=?,progress=image_count,error=NULL,updated_at=? WHERE id=?",(dump(result),dump(usage),now(),j['id']))
                if j['kind']=='connection':
                    c.execute('UPDATE ai_models SET last_test_at=?,last_test_ok=1,last_test_error=NULL,vision_verified=1 WHERE id=?',(now(),cfg['id']))
        except Exception as exc:
            message=safe_error(str(exc),cfg.get('api_key',''))
            with self.server._request_lock,self.server.db() as c:
                if epoch!=self.epoch:return True
                usage=usage_sum([json.loads(r[0]) if r[0] else None for r in c.execute('SELECT usage FROM visual_api_calls WHERE job_id=?',(j['id'],))])
                c.execute("UPDATE visual_analysis_jobs SET state='failed',error=?,usage=?,updated_at=? WHERE id=?",(message,dump(usage),now(),j['id']))
                c.execute("UPDATE visual_analysis_batches SET state='failed',error=? WHERE job_id=? AND state='running'",(message,j['id']))
                if j['kind']=='connection':c.execute('UPDATE ai_models SET last_test_at=?,last_test_ok=0,last_test_error=?,vision_verified=0 WHERE id=?',(now(),message,j['model_config_id']))
        return True


def generate(server,body):
    if not server.innovator:raise ValueError('生图服务尚未启动')
    with server.db() as c:
        j=job(c,body.get('job_id'))
        if j['state']!='done':raise ValueError('请先完成分析')
        prompt=c.execute('SELECT * FROM visual_prompt_versions WHERE id=? AND job_id=?',(body.get('prompt_version_id'),j['id'])).fetchone()
        if not prompt:raise ValueError('请先保存 Prompt 版本')
        cfg=models.resolve(c,server.ROOT,server.DATA,body.get('target_model_id'),category='image')
        if not cfg['enabled']:raise ValueError('生图模型已停用')
        # Check the chosen target still matches the adapter behind the existing queue.
        variant=cfg['options'].get('variant','sunburst')
        if server.innovator.model_for(variant)!=cfg['model_id']:raise ValueError('生图配置与当前服务不一致，请重启服务')
        brief={'mode':prompt['task_type'],'model':variant,'count':body.get('count',1),
          'direction':'自定义方向','changes':'根据视觉逆向报告创作原创设计',
          'references':[],'visual_prompt_version_id':prompt['id'],
          'garment':body.get('garment','tshirt'),'style':body.get('style','white'),
          'color':body.get('color','白色'),'mockup_changes':body.get('mockup_changes',''),
          'parent_image_id':body.get('parent_image_id')}
    result=server.innovator.submit({'market':j['market'],'id':j['product_id'],'request_id':body.get('request_id'),'brief':brief})
    with server.db() as c:
        if not c.execute('SELECT 1 FROM visual_generation_links WHERE analysis_id=? AND generation_id=?',(j['id'],result['id'])).fetchone():
            c.execute('INSERT INTO visual_generation_links(analysis_id,prompt_version_id,generation_id,created_at) VALUES(?,?,?,?)',(j['id'],prompt['id'],result['id'],now()))
    return result
