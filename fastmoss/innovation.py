"""Local creative briefs and durable generation jobs.

No provider protocol is assumed. A verified provider adapter must be supplied to
Service before generation is enabled. Adapters return image bytes, never URLs.
"""
import datetime
import hashlib
import json
import re
import threading
import tempfile
import zipfile
import time
import os
import shutil
import platform_support

from openai_images import OpenAIImages, LK888Images, APIError, MODELS, png_info, estimate_cost
import uuid

import product_assets
import model_manager

DIRECTIONS = ('图案与主题延展', '配色与风格创新', '版型与细节优化', '场景与系列拓展', '自定义方向')


def stamp():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS innovation_briefs(
        market TEXT, product_id TEXT, content TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(market,product_id));
    CREATE TABLE IF NOT EXISTS innovation_jobs(
        id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
        market TEXT NOT NULL, product_id TEXT NOT NULL, product TEXT NOT NULL,
        brief TEXT NOT NULL, prompt TEXT NOT NULL, provider TEXT NOT NULL,
        state TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS innovation_images(
        id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES innovation_jobs(id),
        position INTEGER NOT NULL, local_path TEXT NOT NULL, UNIQUE(job_id,position));
    CREATE INDEX IF NOT EXISTS innovation_jobs_created ON innovation_jobs(created_at DESC);
    ''')
    # Additive migration: reuse the existing task/image tables and their IDs.
    additions = {
        'innovation_jobs': {'stage': "TEXT DEFAULT 'queued'", 'retry_at': 'REAL DEFAULT 0'},
        'innovation_images': {'image_type': "TEXT NOT NULL DEFAULT 'legacy'", 'model': 'TEXT',
            'prompt': 'TEXT', 'created_at': 'TEXT', 'version': 'INTEGER NOT NULL DEFAULT 1',
            'parent_image_id': 'INTEGER REFERENCES innovation_images(id)',
            'design_image_id': 'INTEGER REFERENCES innovation_images(id)',
            'saved_at': 'TEXT', 'metadata': "TEXT NOT NULL DEFAULT '{}'", 'call_id': 'INTEGER'},
    }
    for table, fields in additions.items():
        existing = {r[1] for r in c.execute('PRAGMA table_info(' + table + ')')}
        for name, kind in fields.items():
            if name not in existing:
                c.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + ' ' + kind)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS ai_mockup_configs(
        id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES innovation_jobs(id),
        design_image_id INTEGER NOT NULL REFERENCES innovation_images(id),
        garment TEXT NOT NULL, style TEXT NOT NULL, color TEXT NOT NULL,
        renderer TEXT NOT NULL DEFAULT 'openai', template_id TEXT, placement TEXT,
        UNIQUE(job_id,design_image_id,garment));
    CREATE TABLE IF NOT EXISTS ai_image_calls(
        id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES innovation_jobs(id),
        step_key TEXT NOT NULL, image_type TEXT NOT NULL, model TEXT NOT NULL,
        prompt TEXT NOT NULL, count INTEGER NOT NULL, parent_image_id INTEGER,
        design_image_id INTEGER, config_id INTEGER REFERENCES ai_mockup_configs(id),
        state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT, UNIQUE(job_id,step_key));
    CREATE TABLE IF NOT EXISTS ai_api_logs(
        id INTEGER PRIMARY KEY, call_id INTEGER NOT NULL REFERENCES ai_image_calls(id),
        endpoint TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
        status INTEGER, request_id TEXT, outcome TEXT NOT NULL, error TEXT,
        usage TEXT, estimated_cost_usd REAL, pricing_version TEXT);
    CREATE INDEX IF NOT EXISTS innovation_images_parent ON innovation_images(parent_image_id);
    CREATE INDEX IF NOT EXISTS ai_calls_state ON ai_image_calls(state);
    """)

    for name in ('provider_cost', 'cost_unit'):
        if name not in {r[1] for r in c.execute('PRAGMA table_info(ai_api_logs)')}:
            c.execute('ALTER TABLE ai_api_logs ADD COLUMN ' + name + (' REAL' if name == 'provider_cost' else ' TEXT'))
    for name, kind in {'provider_task_id': 'TEXT', 'remote_payload': 'TEXT', 'provider_cost': 'REAL', 'cost_unit': 'TEXT'}.items():
        if name not in {r[1] for r in c.execute('PRAGMA table_info(ai_image_calls)')}:
            c.execute('ALTER TABLE ai_image_calls ADD COLUMN ' + name + ' ' + kind)

    c.execute("""UPDATE ai_mockup_configs SET renderer='lk888' WHERE renderer='openai'
        AND job_id IN (SELECT id FROM innovation_jobs WHERE provider='tt-image-2.6')""")
    for row in c.execute("SELECT id,metadata FROM innovation_images WHERE image_type='mockup' AND model='tt-image-2.6'").fetchall():
        meta = json.loads(row['metadata'] or '{}')
        if meta.get('mockup', {}).get('renderer') == 'openai':
            meta['mockup']['renderer'] = 'lk888'
            c.execute('UPDATE innovation_images SET metadata=? WHERE id=?', (json.dumps(meta), row['id']))


def product(c, market, product_id, require_draft=True):
    row = c.execute('''SELECT r.market,r.id,r.name,r.shop_name,r.price,r.image,sc.rank
        FROM records r JOIN draft_entries d ON d.market=r.market AND d.id=r.id AND d.active=1
        LEFT JOIN product_scores sc ON sc.market=r.market AND sc.id=r.id
        WHERE r.kind='product' AND r.market=? AND r.id=?''', (market, product_id)).fetchone()
    if not row and not require_draft:
        row = c.execute("SELECT market,id,name,shop_name,price,image FROM records WHERE kind='product' AND market=? AND id=?", (market,product_id)).fetchone()
    if not row:
        raise ValueError('商品已不在选品池，请重新选择')
    return dict(row)


def sources(c, q):
    page = max(1, int(q.get('page', ['1'])[0]))
    where = ["r.kind='product'", 'd.active=1']
    args = []
    search = q.get('q', [''])[0].strip()[:200]
    if search:
        where.append('(r.name LIKE ? OR r.shop_name LIKE ? OR r.id LIKE ?)')
        args.extend(['%' + search + '%'] * 3)
    shop = q.get('shop', [''])[0]
    if shop:
        where.append('r.shop_name=?')
        args.append(shop)
    if q.get('top', [''])[0] == '1':
        where.append('sc.rank<=20')
    base = ''' FROM records r JOIN draft_entries d ON d.market=r.market AND d.id=r.id
        LEFT JOIN product_scores sc ON sc.market=r.market AND sc.id=r.id WHERE ''' + ' AND '.join(where)
    total = c.execute('SELECT count(*)' + base, args).fetchone()[0]
    items = [dict(r) for r in c.execute('''SELECT r.market,r.id,r.name,r.shop_name,r.price,sc.rank,
        (SELECT i.local_path FROM images i WHERE i.market=r.market AND i.product_id=r.id
         AND i.local_path IS NOT NULL ORDER BY i.id LIMIT 1) thumbnail''' + base +
        ' ORDER BY sc.rank IS NULL,sc.rank,r.market,r.id LIMIT 30 OFFSET ?', args + [(page - 1) * 30])]
    shops = [r[0] for r in c.execute('''SELECT DISTINCT r.shop_name FROM records r JOIN draft_entries d
        ON d.market=r.market AND d.id=r.id WHERE r.kind='product' AND d.active=1
        AND r.shop_name IS NOT NULL ORDER BY r.shop_name''')]
    return {'items': items, 'total': total, 'page': page, 'shops': shops}


def detail(c, market, product_id):
    item = product(c, market, product_id)
    row = c.execute('SELECT content,updated_at FROM innovation_briefs WHERE market=? AND product_id=?',
                    (market, product_id)).fetchone()
    return {'product': item, 'gallery': product_assets.payload(c, market, product_id),
            'brief': json.loads(row['content']) if row else None, 'saved_at': row['updated_at'] if row else None}


def normalize(body):
    if not isinstance(body, dict):
        raise ValueError('创作要求格式无效')
    result = {}
    for key, default, limit in [('direction', DIRECTIONS[0], 80), ('retain', '', 2000),
                                ('changes', '', 4000), ('audience', '', 2000), ('extra', '', 4000),
                                ('mockup_changes', '', 2000)]:
        value = body.get(key, default)
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError('创作要求过长或格式无效')
        result[key] = value.strip()
    count = body.get('count', 1)
    if type(count) is not int or count not in (1, 2, 4):
        raise ValueError('每次请选择 1、2 或 4 款')
    refs = body.get('references', [])
    if not isinstance(refs, list) or len(refs) > 100 or any(type(i) is not int or i < 1 for i in refs):
        raise ValueError('参考图选择无效')
    for key, default, choices in (
        ('model', 'sunburst', ('sunburst', 'flare')),
        ('mode', 'both', ('design', 'mockup', 'both')),
        ('garment', 'both', ('tshirt', 'hoodie', 'both')),
        ('style', 'white', ('white', 'flat', 'model', 'lifestyle')),
    ):
        value = body.get(key, default)
        if value not in choices:
            raise ValueError('生图选项无效：' + key)
        result[key] = value
    color = body.get('color', '白色')
    if not isinstance(color, str) or not 1 <= len(color.strip()) <= 80:
        raise ValueError('请填写服装颜色（最多 80 字）')
    parent = body.get('parent_image_id')
    if parent is not None and (type(parent) is not int or parent < 1):
        raise ValueError('父级图片无效')
    visual = body.get('visual_prompt_version_id')
    if visual is not None:
        if type(visual) is not int or visual < 1:
            raise ValueError('视觉 Prompt 版本无效')
        result['visual_prompt_version_id'] = visual
    result.update(color=color.strip(), parent_image_id=parent, count=count, references=list(dict.fromkeys(refs)))
    return result


def validate_references(c, data_dir, market, product_id, references):
    available = {r['id']: r for r in product_assets.payload(c, market, product_id)['images']}
    base = (data_dir / 'images').resolve()
    paths = []
    for image_id in references:
        row = available.get(image_id)
        path = (data_dir / str((row or {}).get('local_path') or '').lstrip('/')).resolve()
        if not row or not row.get('local_path') or not path.is_relative_to(base) or not path.is_file():
            raise ValueError('所选参考图不可用，请重新选择已缓存的图片')
        if path.stat().st_size > 25 * 1024 * 1024:
            raise ValueError('参考图片超过 25 MB，请选择其他图片')
        paths.append(path)
    return paths


def save_brief(c, data_dir, body):
    market, pid = str(body.get('market', 'US')), str(body.get('id', ''))
    brief = normalize(body.get('brief'))
    product(c, market, pid, require_draft=not bool(brief.get('visual_prompt_version_id')))
    validate_references(c, data_dir, market, pid, brief['references'])
    updated = stamp()
    c.execute('''INSERT INTO innovation_briefs VALUES(?,?,?,?) ON CONFLICT(market,product_id)
        DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at''',
        (market, pid, json.dumps(brief, ensure_ascii=False), updated))
    return {'ok': True, 'saved_at': updated, 'brief': brief}


def prompt_for(item, brief, third_party=False):
    intro = ('创作适合 T 恤、卫衣 POD 印刷的独立印花。' if third_party else
             '创作适合 T 恤、卫衣 POD 印刷的原创独立印花。')
    lines = [intro + '只输出图案，不要服装、模特、摄影场景或 Mockup。透明背景 PNG，高质量、清晰边缘。',
        '参考商品：' + str(item['name'] or item['id']),
        '创新方向：' + brief['direction'],
        '保留特征：' + (brief['retain'] or ('按照用户要求确定' if third_party else '保留抽象主题，不复制原设计')),
        '希望改变：' + brief['changes'],
        '目标人群与使用场景：' + (brief['audience'] or '由设计方向确定'),
        '补充要求：' + (brief['extra'] or '无'),
        '共生成 %d 张独立设计，每张只有一个完整图案，不拼图。' % brief['count']]
    lines.append('根据用户要求和参考图生成所需图案。' if third_party else
        '参考图仅用于理解抽象主题；禁止直接复制竞品完整设计、商标、品牌标识或受保护角色与作品。重新组织构图、文字和装饰。')
    return '\n'.join(lines)


def image_payload(row):
    result = dict(row)
    result['metadata'] = json.loads(result.get('metadata') or '{}')
    return result


def job_payload(c, row):
    result = dict(row)
    for key in ('product', 'brief'):
        result[key] = json.loads(result[key])
    result.pop('request_hash', None)
    result['images'] = [image_payload(r) for r in c.execute(
        'SELECT * FROM innovation_images WHERE job_id=? ORDER BY position', (row['id'],))]
    result['calls'] = [dict(r) for r in c.execute(
        'SELECT id,image_type,state,attempts,error,provider_task_id,provider_cost,cost_unit FROM ai_image_calls WHERE job_id=? ORDER BY id', (row['id'],))]
    result['logs'] = [dict(r) for r in c.execute("""SELECT l.* FROM ai_api_logs l JOIN ai_image_calls a
        ON a.id=l.call_id WHERE a.job_id=? ORDER BY l.id""", (row['id'],))]
    costs = [r['estimated_cost_usd'] for r in result['logs'] if r['outcome'] in ('done', 'unknown')]
    result['estimated_cost_usd'] = round(sum(costs), 8) if costs and all(x is not None for x in costs) else None
    remote_costs = {x['request_id']: x['provider_cost'] for x in result['logs'] if x['request_id'] and x['provider_cost'] is not None}
    for call in result['calls']:
        if call['provider_task_id']:
            remote_costs.setdefault(call['provider_task_id'], call['provider_cost'])
    platform_costs = list(remote_costs.values())
    result['provider_cost'] = round(sum(platform_costs), 8) if platform_costs and all(x is not None for x in platform_costs) else None
    result['cost_unit'] = '平台算力' if platform_costs else None
    return result


def drafts(c, q):
    page = max(1, int(q.get('page', ['1'])[0]))
    where, args = [], []
    if q.get('id'):
        where.extend(['product_id=?', 'market=?'])
        args.extend([q['id'][0], q.get('market', ['US'])[0]])
    clause = ' WHERE ' + ' AND '.join(where) if where else ''
    rows = c.execute('SELECT * FROM innovation_jobs' + clause + ' ORDER BY created_at DESC,id DESC LIMIT 20 OFFSET ?',
                     args + [(page - 1) * 20]).fetchall()
    result = {'items': [job_payload(c, row) for row in rows], 'page': page,
            'total': c.execute('SELECT count(*) FROM innovation_jobs' + clause, args).fetchone()[0],
            'images': c.execute('SELECT count(*) FROM innovation_images').fetchone()[0]}
    if q.get('id'):
        result['product_images'] = c.execute('SELECT count(*) FROM innovation_images i JOIN innovation_jobs j ON j.id=i.job_id WHERE j.product_id=? AND j.market=?', args).fetchone()[0]
        result['product_tasks'] = dict(c.execute("SELECT state,count(*) FROM innovation_jobs WHERE product_id=? AND market=? AND state IN ('queued','running','retry_wait','pause_requested','paused') GROUP BY state", args).fetchall())
    return result



def library(c, q):
    """Paginate by product, keeping all its saved mockups together."""
    page = max(1, int(q.get('page', ['1'])[0]))
    base = """FROM innovation_jobs j JOIN innovation_images i ON i.job_id=j.id
              WHERE i.image_type='mockup' AND i.saved_at IS NOT NULL"""
    groups = c.execute('SELECT j.market,j.product_id,max(i.saved_at) latest ' + base +
                       ' GROUP BY j.market,j.product_id ORDER BY latest DESC,j.market,j.product_id LIMIT 20 OFFSET ?',
                       ((page - 1) * 20,)).fetchall()
    items = []
    for group in groups:
        rows = c.execute("""SELECT j.* FROM innovation_jobs j WHERE j.market=? AND j.product_id=?
            AND EXISTS (SELECT 1 FROM innovation_images i WHERE i.job_id=j.id
            AND i.image_type='mockup' AND i.saved_at IS NOT NULL)
            ORDER BY j.created_at DESC,j.id DESC""", (group['market'], group['product_id'])).fetchall()
        for row in rows:
            job = job_payload(c, row)
            job['images'] = [image for image in job['images'] if image['image_type'] == 'mockup' and image['saved_at']]
            items.append(job)
    return {'items': items, 'page': page,
            'total': c.execute('SELECT count(*) FROM (SELECT j.market,j.product_id ' + base +
                               ' GROUP BY j.market,j.product_id)').fetchone()[0],
            'images': c.execute("SELECT count(*) FROM innovation_images WHERE image_type='mockup' AND saved_at IS NOT NULL").fetchone()[0]}


def get_job(c, job_id):
    row = c.execute('SELECT * FROM innovation_jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise ValueError('任务不存在')
    return job_payload(c, row)


def save_image(c, image_id):
    row = c.execute('SELECT * FROM innovation_images WHERE id=?', (image_id,)).fetchone()
    if not row:
        raise ValueError('图片不存在')
    if row['image_type'] != 'mockup':
        raise ValueError('成品库仅保存可上架的服装商品图，请先生成 Mockup')
    # Keep the linked production design available for subsequent editing.
    c.execute('UPDATE innovation_images SET saved_at=coalesce(saved_at,?) WHERE id IN (?,?)',
              (stamp(), image_id, row['design_image_id']))
    return {'ok': True}



def save_images(c, body):
    """Save selected mockups for one product, validating the entire batch first."""
    ids = body.get('image_ids')
    market, product_id = body.get('market'), body.get('id')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(type(i) is not int or i < 1 for i in ids):
        raise ValueError('请选择 1 至 500 张商品图')
    if not isinstance(market, str) or not isinstance(product_id, str):
        raise ValueError('商品信息无效')
    ids = list(dict.fromkeys(ids))
    placeholders = ','.join('?' for _ in ids)
    rows = c.execute("""SELECT i.id,i.saved_at FROM innovation_images i JOIN innovation_jobs j ON j.id=i.job_id
        WHERE i.id IN (""" + placeholders + """) AND i.image_type='mockup' AND j.market=? AND j.product_id=?""",
        ids + [market, product_id]).fetchall()
    if len(rows) != len(ids):
        raise ValueError('部分图片不存在或不属于当前商品，请刷新后重试')
    newly_saved = sum(row['saved_at'] is None for row in rows)
    for image_id in ids:
        save_image(c, image_id)
    return {'ok': True, 'saved': newly_saved, 'total': len(ids)}


def generated_path(data_dir, row):
    base = (data_dir / 'innovation').resolve()
    path = (base / row['local_path'].removeprefix('/innovation-images/')).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError('原始印花文件不存在，请恢复完整素材备份')
    return path



def download_archive(c, data_dir, market, product_id):
    """Package all generated images for a product, including historical versions."""
    rows = c.execute("""SELECT i.* FROM innovation_images i
        JOIN innovation_jobs j ON j.id=i.job_id
        WHERE j.market=? AND j.product_id=? ORDER BY i.id""", (market, product_id)).fetchall()
    if not rows:
        raise ValueError('当前商品暂无生成素材')
    files = []
    for row in rows:
        path = generated_path(data_dir, row)
        kind = row['image_type'] if row['image_type'] in ('design', 'mockup') else 'other'
        files.append((path, f"{kind}/{kind}-{row['id']}-v{row['version']}.png"))
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
            for path, name in files:
                archive.write(path, name)
        output.seek(0)
        return output
    except Exception:
        output.close()
        raise


def mockup_prompt(brief, garment):
    styles = {'white': '纯白底电商商品图，无模特，正面完整展示', 'flat': '俯拍平铺商品摄影，完整衣身，平整铺放',
              'model': '成年模特上身图，胸前图案完整清晰可见', 'lifestyle': '真实生活方式场景，成年模特穿着，突出胸前印花'}
    return '\n'.join(['将输入图片中的独立印花印在一件' + ('T 恤' if garment == 'tshirt' else '连帽卫衣') + '胸前中央。',
        '服装颜色：' + brief['color'], '展示风格：' + styles[brief['style']],
        '输入图是唯一印花素材。尽可能原样保留图案、文字拼写、配色、比例和布局；不要重绘设计，不增加文字标识。',
        '印花自然贴合布料并具有真实印刷质感。输出一张高质量服装 Mockup 商品摄影，非拼图。',
        '目标人群与场景：' + brief['audience'],
        '商品图补充修改要求（不得改变输入印花）：' + (brief.get('mockup_changes') or '无')])


class Service:
    def __init__(self, server, adapter=None):
        self.server, self.adapter = server, adapter
        self.media = isinstance(adapter, LK888Images)
        self.epoch = 0
        self.closed, self.wake = threading.Event(), threading.Event()
        self.thread = None
        self.lockfile = None

    def status(self, choice=None):
        if self.adapter:
            token=model_manager.image_variant.set(choice)
            try:return self.adapter.status()
            finally:model_manager.image_variant.reset(token)
        return {'ready': False, 'provider': None, 'message': '请在后端 .env 配置 OPENAI_API_KEY。', 'models': MODELS}

    def model_for(self, choice):
        return self.adapter.model if self.media else MODELS[choice]

    def recover(self):
        with self.server.db() as c:
            c.execute("UPDATE ai_image_calls SET state='unknown',error='服务中断，请核对账单后重试' WHERE state='running'")
            c.execute("UPDATE ai_api_logs SET outcome='unknown',finished_at=? WHERE outcome='running'", (stamp(),))
            c.execute("""UPDATE innovation_jobs SET state='interrupted',error=?,updated_at=?
                WHERE state='running'""", ('服务中断；已保存的图片保留，未确认的收费请求不会自动重发。', stamp()))
            c.execute("UPDATE innovation_jobs SET state='paused',updated_at=? WHERE state='pause_requested'", (stamp(),))

            # Known upstream jobs resume GET/download only, never POST again.
            c.execute("UPDATE ai_image_calls SET state='pending',error=NULL WHERE state='unknown' AND provider_task_id IS NOT NULL")
            c.execute("UPDATE innovation_jobs SET state='interrupted',error='服务中断；请核对账单后重试' WHERE state='paused' AND EXISTS(SELECT 1 FROM ai_image_calls a WHERE a.job_id=innovation_jobs.id AND a.state='unknown')")
            c.execute("""UPDATE innovation_jobs SET state='queued',error=NULL WHERE state='interrupted'
                AND EXISTS(SELECT 1 FROM ai_image_calls a WHERE a.job_id=innovation_jobs.id AND a.state='pending' AND a.provider_task_id IS NOT NULL)
                AND NOT EXISTS(SELECT 1 FROM ai_image_calls a WHERE a.job_id=innovation_jobs.id AND a.state='unknown')""")

    def invalidate(self):
        self.epoch += 1

    def start(self):
        # Prevent two local processes consuming the same paid queue.
        self.lockfile = open(str(self.server.DB) + '.innovation.lock', 'a+')
        platform_support.lock_file(self.lockfile)
        self.recover()
        if self.adapter:
            self.thread = threading.Thread(target=self._loop, daemon=True, name='innovation-worker')
            self.thread.start()

    def stop(self):
        self.closed.set()
        self.wake.set()
        # Keep the process lock until an in-flight paid call has finished or process exits.

    def submit(self, body):
        token = body.get('request_id', '')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', token):
            raise ValueError('请求标识无效，请刷新后重试')
        brief = normalize(body.get('brief'))
        market, pid = str(body.get('market', 'US')), str(body.get('id', ''))
        fingerprint = hashlib.sha256(json.dumps([market, pid, brief], sort_keys=True).encode()).hexdigest()
        with self.server._request_lock, self.server.db() as c:
            c.execute('BEGIN IMMEDIATE')
            existing = c.execute('SELECT * FROM innovation_jobs WHERE request_id=?', (token,)).fetchone()
            if existing:
                if existing['request_hash'] != fingerprint:
                    raise ValueError('请求标识已使用，请重新发起生成')
                return job_payload(c, existing)
            # Also catch repeated clicks/new tabs that use a different token.
            existing = c.execute("""SELECT * FROM innovation_jobs WHERE request_hash=? AND
                state IN ('queued','running','retry_wait','pause_requested','paused') ORDER BY created_at DESC LIMIT 1""", (fingerprint,)).fetchone()
            if existing:
                return job_payload(c, existing)
            if isinstance(self.adapter,(OpenAIImages,LK888Images)):
                model_manager.seed(c,self.server.ROOT)
                profile=model_manager.resolve(c,self.server.ROOT,self.server.DATA,'image-'+brief['model'],'image')
                if not profile['enabled']:
                    raise ValueError('所选生图模型已停用，请在 AI 模型管理启用')
            if not self.status(brief['model'])['ready']:
                raise ValueError(self.status(brief['model'])['message'])
            visual_prompt = None
            if brief.get('visual_prompt_version_id'):
                visual_prompt = c.execute('''SELECT p.*,a.target_model FROM visual_prompt_versions p JOIN visual_analysis_jobs a ON a.id=p.job_id
                    WHERE p.id=? AND a.market=? AND a.product_id=? AND a.state='done' ''',
                    (brief['visual_prompt_version_id'],market,pid)).fetchone()
                if not visual_prompt or visual_prompt['task_type'] != brief['mode']:
                    raise ValueError('视觉分析 Prompt 与商品或生成类型不匹配')
            item = product(c, market, pid, require_draft=not bool(visual_prompt))
            if brief['mode'] != 'mockup' and not brief['changes']:
                raise ValueError('请填写希望做哪些变化')
            parent = None
            paths = []
            if brief['parent_image_id']:
                parent = c.execute("""SELECT i.*,j.market,j.product_id FROM innovation_images i
                    JOIN innovation_jobs j ON j.id=i.job_id WHERE i.id=?""", (brief['parent_image_id'],)).fetchone()
                if not parent or parent['market'] != market or parent['product_id'] != pid:
                    raise ValueError('父级图片不属于当前商品')
                if brief['mode'] == 'mockup' and parent['image_type'] == 'mockup':
                    design = c.execute('SELECT * FROM innovation_images WHERE id=?', (parent['design_image_id'],)).fetchone()
                else:
                    design = parent
                if not design or design['image_type'] != 'design':
                    raise ValueError('请选择独立印花作为输入')
                paths = [generated_path(self.server.DATA, design)]
            elif brief['mode'] == 'mockup' and not visual_prompt:
                raise ValueError('仅 Mockup 模式必须先选择已有独立印花')
            else:
                paths = validate_references(c, self.server.DATA, market, pid, brief['references'])
            job_id = uuid.uuid4().hex
            folder = self.server.DATA / 'innovation' / job_id / 'references'
            folder.mkdir(parents=True)
            try:
                # Deduplicate identical files, prefer larger clear source files, cap input cost.
                unique = {}
                for path in paths:
                    content = path.read_bytes()
                    ext = product_assets.image_extension(content)
                    if ext not in ('.png', '.jpg', '.jpeg', '.webp'):
                        raise ValueError('参考图仅支持 PNG、JPEG、WebP')
                    unique.setdefault(hashlib.sha256(content).hexdigest(), (content, ext))
                selected = sorted(unique.values(), key=lambda x: len(x[0]), reverse=True)[:8]
                for i, (content, ext) in enumerate(selected):
                    (folder / ('%03d%s' % (i, ext))).write_bytes(content)
                brief['reference_selection'] = {'selected': len(paths), 'used': len(selected),
                    'strategy': '去重后按文件大小降序选取最多 8 张（清晰度启发式，非语义评分）'}
                save_brief(c, self.server.DATA, body)
                now = stamp()
                prompt = prompt_for(item, brief, third_party=self.media)
                if visual_prompt:
                    prompt = visual_prompt['prompt_en'] + '\nAvoid: ' + visual_prompt['negative_prompt']
                    prompt += ('\nCreate one original isolated print graphic only. Transparent PNG, no garment, no model, no scene.'
                               if brief['mode']=='design' else '\nCreate one garment mockup photograph. Garment color: '+brief['color']+'. '+brief['mockup_changes'])
                if parent and brief['mode'] != 'mockup':
                    prompt = '输入是上一版独立印花，在其基础上按以下要求修改，未要求修改的特征保留。\n' + prompt
                c.execute("""INSERT INTO innovation_jobs(id,request_id,request_hash,market,product_id,product,
                    brief,prompt,provider,state,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, token, fingerprint, market, pid, json.dumps(item, ensure_ascii=False),
                     json.dumps(brief, ensure_ascii=False), prompt, self.model_for(brief['model']), 'queued', None, now, now))
                if brief['mode'] == 'mockup' and not (visual_prompt and not parent):
                    self._mockup_calls(c, job_id, brief, design['id'], parent['id'])
                else:
                    for index in range(brief['count'] if self.media else 1):
                        one_prompt = (prompt.replace('共生成 %d 张独立设计' % brief['count'], '共生成 1 张独立设计') +
                                      '\n本次仅输出一张完整独立设计，不拼图；这是系列变体 %d。' % (index + 1)) if self.media else prompt
                        c.execute("""INSERT INTO ai_image_calls(job_id,step_key,image_type,model,prompt,count,parent_image_id)
                            VALUES(?, ?, ?, ?, ?, ?, ?)""",
                            (job_id, 'design-%d' % index, brief['mode'] if visual_prompt else 'design', self.model_for(brief['model']), one_prompt,
                             1 if self.media else brief['count'], parent['id'] if parent else None))
            except Exception:
                shutil.rmtree(folder.parent)
                raise
            result = get_job(c, job_id)
        self.wake.set()
        return result

    def _mockup_calls(self, c, job_id, brief, design_id, parent_id=None):
        garments = ['tshirt', 'hoodie'] if brief['garment'] == 'both' else [brief['garment']]
        for garment in garments:
            chosen_prompt = mockup_prompt(brief, garment)
            if brief.get('visual_prompt_version_id'):
                saved=c.execute('SELECT prompt_en,negative_prompt FROM visual_prompt_versions WHERE id=?',(brief['visual_prompt_version_id'],)).fetchone()
                chosen_prompt += '\nVisual report prompt: '+saved['prompt_en']+'\nAvoid: '+saved['negative_prompt']
            c.execute("""INSERT OR IGNORE INTO ai_mockup_configs(job_id,design_image_id,garment,style,color,renderer)
                VALUES(?,?,?,?,?,?)""", (job_id, design_id, garment, brief['style'], brief['color'], 'lk888' if self.media else 'openai'))
            config = c.execute('SELECT id FROM ai_mockup_configs WHERE job_id=? AND design_image_id=? AND garment=?',
                               (job_id, design_id, garment)).fetchone()[0]
            c.execute("""INSERT OR IGNORE INTO ai_image_calls(job_id,step_key,image_type,model,prompt,count,
                parent_image_id,design_image_id,config_id) VALUES(?,?,'mockup',?,?,1,?,?,?)""",
                (job_id, 'mockup-%s-%s' % (design_id, garment), self.model_for(brief['model']), chosen_prompt,
                 parent_id or design_id, design_id, config))

    def pause_product(self, body):
        market, product_id, action = body.get('market'), body.get('id'), body.get('action')
        if not isinstance(market, str) or not isinstance(product_id, str) or action not in ('pause', 'resume'):
            raise ValueError('暂停操作参数无效')
        with self.server._request_lock, self.server.db() as c:
            if action == 'pause':
                changed = c.execute("""UPDATE innovation_jobs SET state=CASE WHEN state='running' THEN 'pause_requested' ELSE 'paused' END,
                    updated_at=? WHERE market=? AND product_id=? AND state IN ('queued','running','retry_wait')""",
                    (stamp(), market, product_id)).rowcount
            else:
                changed = c.execute("""UPDATE innovation_jobs SET state=CASE WHEN state='pause_requested' THEN 'running' ELSE 'queued' END,
                    retry_at=0,updated_at=? WHERE market=? AND product_id=? AND state IN ('paused','pause_requested')""",
                    (stamp(), market, product_id)).rowcount
        if action == 'resume' and changed:
            self.wake.set()
        return {'ok': True, 'changed': changed}

    def retry(self, body):
        with self.server._request_lock, self.server.db() as c:
            job = get_job(c, str(body.get('job_id', '')))
            if job['state'] in ('queued', 'running', 'retry_wait', 'pause_requested', 'paused'):
                return job
            if job['provider'] != self.model_for(job['brief']['model']):
                raise ValueError('该历史任务属于另一服务商，请使用当前配置新建生成任务。')
            unknown = c.execute("SELECT 1 FROM ai_image_calls WHERE job_id=? AND state='unknown'", (job['id'],)).fetchone()
            if unknown and body.get('acknowledge_charge') is not True:
                raise ValueError('上次调用结果不确定。请先核对服务商账单；确认仍要重新收费生成后再重试。')
            # A failed remote task may be recreated only by explicit retry. Successful or
            # unfinished remote tasks keep their IDs so downloads/polls cannot bill twice.
            for remote in c.execute("SELECT id,remote_payload FROM ai_image_calls WHERE job_id=? AND state IN ('failed','unknown') AND provider_task_id IS NOT NULL", (job['id'],)).fetchall():
                payload = json.loads(remote['remote_payload'] or '{}')
                if payload.get('is_final') is True and payload.get('state') != 'success':
                    c.execute('UPDATE ai_image_calls SET provider_task_id=NULL,remote_payload=NULL,provider_cost=NULL,cost_unit=NULL WHERE id=?', (remote['id'],))
            changed = c.execute("UPDATE ai_image_calls SET state='pending',error=NULL,attempts=0 WHERE job_id=? AND state IN ('failed','unknown')", (job['id'],)).rowcount
            if not changed:
                raise ValueError('没有可重试的失败步骤；已完成图片不会重复生成')
            c.execute("UPDATE innovation_jobs SET state='queued',error=NULL,retry_at=0,updated_at=? WHERE id=?", (stamp(), job['id']))
            result = get_job(c, job['id'])
        self.wake.set()
        return result

    def _loop(self):
        while not self.closed.is_set():
            try:
                worked = self.run_one()
            except Exception:
                # A local disk/DB failure must never cause a paid call to be resent.
                worked = False
            if not worked:
                self.wake.wait(2)
                self.wake.clear()

    def run_one(self):
        with self.server._request_lock:
            if self.closed.is_set() or not self.adapter:
                return False
            with self.server.db() as c:
                row = c.execute("""SELECT * FROM innovation_jobs WHERE state IN ('queued','retry_wait')
                    AND retry_at<=? ORDER BY created_at,id LIMIT 1""", (time.time(),)).fetchone()
                if not row:
                    return False
                job = job_payload(c, row)
                callrow = c.execute("SELECT * FROM ai_image_calls WHERE job_id=? AND state='pending' ORDER BY id LIMIT 1", (job['id'],)).fetchone()
                if not callrow:
                    self._finish(c, job['id'])
                    return True
                call = dict(callrow)
                epoch = self.epoch
                c.execute("UPDATE innovation_jobs SET state='running',stage=?,updated_at=? WHERE id=?", (call['image_type'], stamp(), job['id']))
                c.execute("UPDATE ai_image_calls SET state='running',attempts=attempts+1 WHERE id=?", (call['id'],))
                if call['image_type'] == 'mockup' and call['design_image_id']:
                    design = c.execute('SELECT * FROM innovation_images WHERE id=?', (call['design_image_id'],)).fetchone()
                    paths = None
                else:
                    paths = []
                log_id = c.execute("""INSERT INTO ai_api_logs(call_id,endpoint,started_at,outcome)
                    VALUES(?,?,?,'running')""", (call['id'], ('/skills/task-status' if call['provider_task_id'] else '/media/generate') if self.media else '/images/edits' if paths or call['image_type']=='mockup' else '/images/generations', stamp())).lastrowid
        response = None
        profile_token = model_manager.image_variant.set(job['brief']['model'])
        try:
            if call['model'] != self.model_for(job['brief']['model']):
                raise APIError('当前服务商与任务不一致，请恢复原配置或新建任务。')
            if paths is None:
                paths = [generated_path(self.server.DATA, design)]
            else:
                paths = sorted((self.server.DATA / 'innovation' / job['id'] / 'references').iterdir())
            if not self.media:
                with self.server.db() as c:
                    c.execute('UPDATE ai_api_logs SET endpoint=? WHERE id=?', ('/images/edits' if paths else '/images/generations', log_id))
            if self.media:
                def update_remote(task_id, payload):
                    call['provider_task_id'] = task_id
                    if payload is not None:
                        call['remote_payload'] = json.dumps(payload)
                    with self.server.db() as c:
                        c.execute('UPDATE ai_image_calls SET provider_task_id=?,remote_payload=? WHERE id=?',
                                  (task_id, call['remote_payload'], call['id']))
                        c.execute('UPDATE ai_api_logs SET request_id=? WHERE id=?', (task_id, log_id))
                        if payload and payload.get('is_final') is True:
                            cost = payload.get('cost')
                            if isinstance(cost, (int, float)):
                                c.execute("UPDATE ai_image_calls SET provider_cost=?,cost_unit='平台算力' WHERE id=?", (cost, call['id']))
                                c.execute("UPDATE ai_api_logs SET provider_cost=?,cost_unit='平台算力' WHERE id=?", (cost, log_id))
                            c.execute('UPDATE ai_api_logs SET usage=? WHERE id=?',
                                      (json.dumps({k: payload.get(k) for k in ('cost','state','error_code','channel_group','refunded','refunded_amount')}), log_id))
                response = self.adapter.generate_remote(call['prompt'], [p.read_bytes() for p in paths], job['brief']['model'],
                    'transparent' if call['image_type']=='design' else 'opaque', call['provider_task_id'],
                    json.loads(call['remote_payload'] or 'null'), update_remote)
            else:
                response = self.adapter.generate(call['prompt'], [p.read_bytes() for p in paths], call['count'],
                                                 call['model'], 'transparent' if call['image_type'] == 'design' else 'opaque')
            images = response['images']
            if not 1 <= len(images) <= call['count']:
                raise APIError('返回图片数量无效', uncertain=True)
            metadata = [png_info(content) for content in images]
            with self.server._request_lock:
                if epoch != self.epoch or self.closed.is_set():
                    return True
                folder = self.server.DATA / 'innovation' / job['id']
                # Keep a private receipt before any DB commit; file failures never resend automatically.
                receipt = {k: v for k, v in response.items() if k != 'images'}
                (folder / ('call-%s-receipt.json' % call['id'])).write_text(json.dumps(receipt))
                with self.server.db() as c:
                    position = c.execute('SELECT coalesce(max(position),0) FROM innovation_images WHERE job_id=?', (job['id'],)).fetchone()[0]
                    parent = c.execute('SELECT version,image_type FROM innovation_images WHERE id=?', (call['parent_image_id'],)).fetchone()
                    version = parent['version'] + 1 if parent and parent['image_type'] == call['image_type'] else 1
                    for i, content in enumerate(images):
                        filename = 'call-%d-%02d.png' % (call['id'], i + 1)
                        target = folder / filename
                        with open(str(target) + '.tmp', 'wb') as handle:
                            handle.write(content); handle.flush(); os.fsync(handle.fileno())
                        os.replace(str(target) + '.tmp', target)
                        meta = metadata[i]
                        meta['variant'] = job['brief']['model']
                        if call['config_id']:
                            meta['mockup'] = dict(c.execute('SELECT * FROM ai_mockup_configs WHERE id=?', (call['config_id'],)).fetchone())
                        new_id = c.execute("""INSERT INTO innovation_images(job_id,position,local_path,image_type,model,prompt,
                            created_at,version,parent_image_id,design_image_id,metadata,call_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (job['id'], position + i + 1, '/innovation-images/' + job['id'] + '/' + filename,
                             call['image_type'], call['model'], call['prompt'], stamp(), version, call['parent_image_id'],
                             call['design_image_id'], json.dumps(meta), call['id'])).lastrowid
                        if call['image_type'] == 'design' and job['brief']['mode'] == 'both':
                            self._mockup_calls(c, job['id'], job['brief'], new_id)
                    error = 'API 返回图片少于请求数量，已保留所有返回图片。' if len(images) != call['count'] else None
                    c.execute("UPDATE ai_image_calls SET state='done',error=? WHERE id=?", (error, call['id']))
                    c.execute("""UPDATE ai_api_logs SET outcome='done',finished_at=?,status=200,request_id=?,usage=?,
                        estimated_cost_usd=?,pricing_version=?,provider_cost=?,cost_unit=? WHERE id=?""",
                        (stamp(), response.get('request_id'), json.dumps(response.get('usage') or {k:response.get(k) for k in ('provider_cost','cost_unit','variant')}),
                         estimate_cost(call['model'], response.get('usage')), 'provider-reported' if self.media else '2026-11-13-standard',
                         response.get('provider_cost'), response.get('cost_unit'), log_id))
                    self._finish(c, job['id'])
        except Exception as exc:
            error = exc if isinstance(exc, APIError) else APIError('未能保存有效图片；已完成的其他图片保留，请核对账单与本地文件。', uncertain=True)
            if self.media and call['provider_task_id']:
                error.uncertain = False  # Safe resume using known upstream task.
                error.retry_after = 0
            with self.server._request_lock:
                if epoch != self.epoch or self.closed.is_set():
                    return True
                with self.server.db() as c:
                    retry = error.retry_after > 0 and call['attempts'] < 2
                    c.execute('UPDATE ai_image_calls SET state=?,error=? WHERE id=?',
                              ('pending' if retry else 'unknown' if error.uncertain else 'failed', str(error), call['id']))
                    c.execute('UPDATE ai_api_logs SET outcome=?,finished_at=?,status=?,request_id=?,error=?,usage=coalesce(?,usage),estimated_cost_usd=? WHERE id=?',
                              ('unknown' if error.uncertain else 'failed', stamp(), error.status,
                               error.request_id or call['provider_task_id'] or (response or {}).get('request_id'), str(error),
                               json.dumps(response.get('usage')) if response else None, estimate_cost(call['model'], (response or {}).get('usage')), log_id))
                    if retry:
                        c.execute("UPDATE innovation_jobs SET state=CASE WHEN state='pause_requested' THEN 'paused' ELSE 'retry_wait' END,retry_at=?,error=?,updated_at=? WHERE id=?",
                                  (time.time() + error.retry_after * (call['attempts'] + 1), str(error), stamp(), job['id']))
                    else:
                        # Pause remaining calls on a provider failure; retry resumes only missing steps.
                        saved = c.execute('SELECT count(*) FROM innovation_images WHERE job_id=?', (job['id'],)).fetchone()[0]
                        c.execute('UPDATE innovation_jobs SET state=?,error=?,updated_at=? WHERE id=?',
                                  ('partial' if saved else 'interrupted' if error.uncertain else 'failed', str(error), stamp(), job['id']))
        finally:
            model_manager.image_variant.reset(profile_token)
        return True

    def _finish(self, c, job_id):
        calls = c.execute('SELECT state,error FROM ai_image_calls WHERE job_id=?', (job_id,)).fetchall()
        if any(r['state'] == 'pending' for r in calls):
            current = c.execute('SELECT state FROM innovation_jobs WHERE id=?', (job_id,)).fetchone()[0]
            state = 'paused' if current == 'pause_requested' else 'queued'
        elif any(r['state'] != 'done' or r['error'] for r in calls):
            state = 'partial'
        else:
            state = 'done'
        errors = '；'.join(dict.fromkeys(r['error'] for r in calls if r['error'])) or None
        c.execute('UPDATE innovation_jobs SET state=?,error=?,retry_at=0,updated_at=? WHERE id=?', (state, errors, stamp(), job_id))
