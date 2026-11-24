"""Local merchandising edits, kept separate from immutable source data."""
import base64
import datetime
import hashlib
import json
import re
import platform_support
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import product_assets
import content_review
import sku_manager

MAX_IMAGE = 8 * 1024 * 1024
MAX_REQUEST = 32 * 1024 * 1024


def migrate(c):
    sku_manager.migrate(c)
    c.executescript('''
    CREATE TABLE IF NOT EXISTS listing_image_edits(
      market TEXT,product_id TEXT,asset TEXT,source_url TEXT,group_name TEXT,
      hidden INTEGER NOT NULL DEFAULT 0,local_path TEXT,updated_at TEXT,
      PRIMARY KEY(market,product_id,asset));
    CREATE TABLE IF NOT EXISTS listing_variant_exclusions(
      market TEXT,product_id TEXT,kind TEXT,item_key TEXT,updated_at TEXT,
      PRIMARY KEY(market,product_id,kind,item_key));
    CREATE TABLE IF NOT EXISTS listing_sku_edits(
      market TEXT,product_id TEXT,sku_id TEXT,price TEXT,updated_at TEXT,
      PRIMARY KEY(market,product_id,sku_id));
    ''')
    # Keep existing local databases usable after adding drag-and-drop order.
    columns={row[1] for row in c.execute('PRAGMA table_info(listing_image_edits)')}
    if 'position' not in columns:
        c.execute('ALTER TABLE listing_image_edits ADD COLUMN position INTEGER')


def asset(url):
    return hashlib.sha256(product_assets.asset_key(url).encode()).hexdigest()


def product(c, market, eid):
    row = c.execute("SELECT * FROM records WHERE kind='product' AND market=? AND id=?", (market, eid)).fetchone()
    if not row:
        raise ValueError('商品不存在')
    return dict(row)


def clean_title(title, shop_name):
    title = str(title or '').strip()
    shop_name = str(shop_name or '').strip()
    if not shop_name or not any(ch.isalnum() for ch in shop_name):
        return title
    title = content_review.sanitize_text(title, [{'name': shop_name, 'kind': '店铺/品牌字段'}], drop_single=False)
    if not title:
        return ''
    # Removing a store name can leave its connector behind, for example
    # ``Cotton Hoodie by Store`` becoming ``Cotton Hoodie by``.
    title = re.sub(r'\s+(?:by|from)\s*$', '', title, flags=re.I)
    title = re.sub(r'^(?:by|from)\s+', '', title, flags=re.I)
    # Keep punctuation that identifies the shop. Allow typography differences
    # in separators and apostrophes. Alphabetic names must match whole words.
    parts = re.split(r"([\s\-‐‑–—]+|['’‘])", shop_name)
    name = ''.join(r"[\s\-‐‑–—]+" if re.fullmatch(r"[\s\-‐‑–—]+", part)
                   else "['’‘]" if part in ("'", "’", "‘") else re.escape(part)
                   for part in parts)
    def boundary(ch):
        return '' if '\u3400' <= ch <= '\u9fff' else r'\w'
    left, right = boundary(shop_name[0]), boundary(shop_name[-1])
    pattern = (r'(?<!' + left + ')' if left else '') + name + (r'(?!' + right + ')' if right else '')
    pattern = r"(?:(?<!\w)(?:by|from)\s+)?(?:" + pattern + r")(?:['’]s\b)?"
    cleaned, count = re.subn(pattern, ' ', title, flags=re.I)
    if not count:
        return title
    cleaned = re.sub(r'\(\s*\)|\[\s*\]|【\s*】', ' ', cleaned)
    cleaned = re.sub(r'\s+([,，:：])', r'\1', cleaned)
    cleaned = re.sub(r'(\s[-–—|])\s+[-–—|]\s+', r'\1 ', cleaned)
    return ' '.join(cleaned.split()).strip(' ,，-‐‑–—|:：')


def _safe_title(c, market, eid, title, source):
    try:
        row = c.execute('SELECT product_json FROM product_capture WHERE market=? AND product_id=?', (market, eid)).fetchone()
        captured = json.loads(row[0]) if row else {}
    except Exception:
        captured = {}
    terms = content_review.source_names(source, captured)
    return clean_title(content_review.sanitize_text(title, terms, drop_single=False), source.get('shop_name'))


def images(c, market, eid, include_hidden=False):
    edits = {r['asset']: dict(r) for r in c.execute(
        'SELECT * FROM listing_image_edits WHERE market=? AND product_id=? ORDER BY updated_at,asset', (market, eid))}
    result, seen = [], set()
    for original in product_assets.payload(c, market, eid)['images']:
        key = asset(original['url'])
        if key in seen:
            continue
        seen.add(key)
        edit = edits.get(key, {})
        item = dict(original, key=key, hidden=bool(edit.get('hidden')), replaced=bool(edit.get('local_path')),
                    original_path=original.get('local_path') or original['url'])
        item['_edit_position']=edit.get('position')
        if edit.get('local_path'):
            item['local_path'] = edit['local_path']
        result.append(item)
    # Preserve locally uploaded replacements even if a later capture changes its manifest.
    for key, edit in edits.items():
        if key not in seen and edit['local_path']:
            result.append(dict(key=key, url=edit['source_url'], local_path=edit['local_path'],
                               original_path=edit['source_url'], group_name=edit['group_name'],
                               hidden=bool(edit['hidden']), replaced=True,
                               _edit_position=edit.get('position')))
    # Saved positions override capture order. Legacy images without a saved
    # position use their source order so a newly appended image stays last.
    for index,item in enumerate(result): item['_source_position']=index
    result.sort(key=lambda item:(item.get('_edit_position') if item.get('_edit_position') is not None else item['_source_position'],
                                 item['_source_position']))
    for item in result:
        item.pop('_edit_position',None);item.pop('_source_position',None)
    return result if include_hidden else [i for i in result if not i['hidden']]


def option_key(group, value):
    return hashlib.sha256(json.dumps([str(group),str(value)],ensure_ascii=False).encode()).hexdigest()


def variant_model(c, market, eid, common=''):
    raw = product_assets.saved_product_data(c, market, eid)['quality'].get('variants', [])
    capture=c.execute('SELECT product_json FROM product_capture WHERE market=? AND product_id=?',(market,eid)).fetchone()
    source=json.loads(capture[0]) if capture else {}
    source_skus={str(v.get('sku_id',v.get('id',''))):v for v in source.get('skus',[])}
    local_skus=sku_manager.variant_map(c,market,eid)
    props={str(p.get('prop_id',p.get('prop_name',''))):p for p in source.get('sale_props',[])}
    edits={r['sku_id']:r['price'] for r in c.execute('SELECT sku_id,price FROM listing_sku_edits WHERE market=? AND product_id=?',(market,eid))}
    excluded={(r['kind'],r['item_key']) for r in c.execute('SELECT kind,item_key FROM listing_variant_exclusions WHERE market=? AND product_id=?',(market,eid))}
    pictures={asset(i['url']):i.get('local_path') or i['url'] for i in images(c,market,eid) if i['url']}
    groups={};result=[]
    for v in raw:
        sid=str(v['id']);keys=[]
        source_variant=source_skus.get(sid,v)
        attrs=source_variant.get('sku_sale_props') or [dict(prop_name=a['name'],prop_value=a['value']) for a in v.get('attributes',[])]
        for a in attrs:
            name=str(a.get('prop_name','规格'));value=str(a.get('prop_value',''))
            gid=str(a.get('prop_id') or name);vid=str(a.get('prop_value_id') or value)
            key=option_key(gid,vid);keys.append(key)
            prop=props.get(gid,{})
            group=groups.setdefault(gid,dict(name=name,has_image=bool(prop.get('has_image')),options={}))
            raw_option=next((o for o in prop.get('sale_prop_values',[]) if str(o.get('prop_value_id') or o.get('prop_value',''))==vid),{})
            picture=raw_option.get('image') or a.get('image') or {}
            urls=picture.get('url_list',[]) if isinstance(picture,dict) else []
            url=urls[0] if urls else ''
            opt=group['options'].setdefault(key,dict(key=key,value=value,image=pictures.get(asset(url),url) if url else '',hidden=('option',key) in excluded,sku_ids=[]))
            opt['sku_ids'].append(sid)
        own=('sku',sid) in excluded
        local=sku_manager.resolve(local_skus,source_variant,sid)
        result.append(dict(v,id=sid,local_variant_id=local.get('variant_id') if local else '',
            seller_sku=local.get('seller_sku','') if local else '',original_sku=local.get('original_sku','') if local else '',
            sku_status=(local.get('platform_state') or '未上架') if local else '未分配',
            source_price=v.get('price'),edit_price=edits.get(sid,''),
            price=edits.get(sid) or common or v.get('price'),option_keys=keys,excluded=own,
            hidden=own or any(('option',k) in excluded for k in keys)))
    return result,[dict(g,options=list(g['options'].values())) for g in groups.values()]


def variants(c, market, eid, common='', include_hidden=False):
    values,_=variant_model(c,market,eid,common)
    return values if include_hidden else [v for v in values if not v['hidden']]


def listing_payload(c, market, eid):
    """Authoritative local listing projection; never exports excluded combinations."""
    data=payload(c,market,eid)
    data['variants']=[v for v in data['variants'] if not v['hidden']]
    active_keys={k for v in data['variants'] for k in v['option_keys']}
    data['option_groups']=[dict(g,options=[o for o in g['options'] if o['key'] in active_keys]) for g in data['option_groups']]
    data['images']=[i for i in data['images'] if not i['hidden']]
    missing=[]
    if not data['title']:missing.append('请补充商品标题')
    if not data.get('category'):missing.append('商品类目缺失')
    if not data['images']:missing.append('没有保留的商品图片')
    if not data['variants']:missing.append('没有保留的 SKU')
    if any(not v.get('price') for v in data['variants']):missing.append('部分 SKU 价格缺失')
    data['missing']=missing
    data['publishable']=not missing
    data['store_connected']=False
    return data


def revision(c, market, eid):
    rows = []
    for table, col in [('listing_edits', 'id'), ('listing_image_edits', 'product_id'), ('listing_sku_edits', 'product_id'), ('listing_variant_exclusions','product_id')]:
        rows.append(sorted([dict(r) for r in c.execute('SELECT * FROM '+table+' WHERE market=? AND '+col+'=?', (market, eid))], key=lambda r: json.dumps(r, sort_keys=True)))
    rows.append(sorted([dict(r) for r in c.execute('''SELECT v.* FROM sku_variants v JOIN sku_products p ON p.product_id=v.product_id
        WHERE p.market=? AND p.source_product_id=?''',(market,eid))], key=lambda r: json.dumps(r, sort_keys=True)))
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def payload(c, market, eid):
    p = product(c, market, eid)
    edit = c.execute('SELECT * FROM listing_edits WHERE market=? AND id=?', (market, eid)).fetchone()
    edit = dict(edit) if edit else {}
    values,groups=variant_model(c,market,eid,edit.get('price') or '')
    return dict(market=market, id=eid, title=_safe_title(c, market, eid, edit.get('title') or p['name'], p),
                source_shop_name=p.get('shop_name') or '', price=edit.get('price') or '',
                category=p.get('category') or '', source_price=p['price'] or '', images=images(c, market, eid, True),
                variants=values, option_groups=groups, revision=revision(c, market, eid))


def price(value):
    value = str(value or '').strip()
    if value and (not re.fullmatch(r'\d{1,9}(?:\.\d{1,2})?', value) or Decimal(value) <= 0):
        raise ValueError('价格须大于零，最多两位小数')
    return format(Decimal(value), '.2f') if value else ''


def validate_upload(value):
    if not isinstance(value, str) or len(value) > MAX_IMAGE * 4 // 3 + 100:
        raise ValueError('每张图片最大 8 MB')
    match = re.fullmatch(r'data:image/(?:jpeg|png|gif|webp);base64,([A-Za-z0-9+/=\r\n]+)', value)
    if not match:
        raise ValueError('仅支持 JPG、PNG、WebP 或 GIF 图片')
    try:
        data = base64.b64decode(match[1], validate=True)
    except ValueError:
        raise ValueError('图片内容无效')
    ext = product_assets.image_extension(data)
    if not data or len(data) > MAX_IMAGE or ext not in ('.jpg', '.png', '.gif', '.webp'):
        raise ValueError('图片内容无效或超过 8 MB')
    platform_support.image_dimensions(data)
    return data, ext


def save(c, data_dir, body):
    market, eid = str(body.get('market', 'US')), str(body.get('id', ''))
    source = product(c, market, eid)
    if body.get('revision') != revision(c, market, eid):
        raise ValueError('该商品已被其他操作修改，请重新打开编辑后保存')
    title = _safe_title(c, market, eid, body.get('title'), source)
    if not title and body.get('title'):
        raise ValueError('请补充去掉竞品店铺名后的商品标题')
    if not title or len(title) > 2000:
        raise ValueError('商品名称不能为空，且最多 2000 个字符')
    common = price(body.get('price'))
    sku_changes = body.get('skus', [])
    image_changes = body.get('images', [])
    if not isinstance(sku_changes, list) or len(sku_changes) > 5000 or not isinstance(image_changes, list) or len(image_changes) > 300:
        raise ValueError('修改数量无效')
    values,groups=variant_model(c,market,eid)
    valid_skus = {str(v['id']) for v in values}
    valid_options={o['key'] for g in groups for o in g['options']}
    rule_changes=body.get('exclusions',[])
    if not isinstance(rule_changes,list) or len(rule_changes)>10000:raise ValueError('规格修改数量无效')
    rules=[];seen_rules=set()
    for item in rule_changes:
        if not isinstance(item,dict):raise ValueError('规格修改数据无效')
        kind=item.get('kind');key=str(item.get('key',''));hidden=item.get('hidden')
        valid=valid_skus if kind=='sku' else valid_options if kind=='option' else set()
        # Allow undoing stale rules after a source refresh, but not adding foreign ones.
        exists=c.execute('SELECT 1 FROM listing_variant_exclusions WHERE market=? AND product_id=? AND kind=? AND item_key=?',(market,eid,kind,key)).fetchone()
        if (key not in valid and not (hidden is False and exists)) or not isinstance(hidden,bool) or (kind,key) in seen_rules:
            raise ValueError('规格不属于该商品、已更新或修改数据无效，请重新打开编辑')
        seen_rules.add((kind,key));rules.append((kind,key,hidden))
    skus = []
    for item in sku_changes:
        sid = str(item.get('id', ''))
        if sid not in valid_skus:
            raise ValueError('SKU 不属于该商品或来源已更新，请重新打开编辑')
        skus.append((sid, price(item.get('price'))))
    known = {i['key']: i for i in images(c, market, eid, True)}
    # A capture may refresh its manifest while the editor is open; original evidence is retained.
    for r in c.execute('SELECT url FROM images WHERE market=? AND product_id=?', (market, eid)):
        known.setdefault(asset(r['url']), dict(url=r['url'], group_name='商品图'))
    changes, uploads, seen = [], {}, set()
    source_count=len(product_assets.payload(c,market,eid)['images'])
    next_position=max(source_count-1,max((int(row['position']) for row in c.execute(
        'SELECT position FROM listing_image_edits WHERE market=? AND product_id=? AND position IS NOT NULL',(market,eid))
        if row['position'] is not None), default=-1))+1
    for item in image_changes:
        key = str(item.get('key', ''))
        if item.get('add'):
            key = 'local-' + uuid.uuid4().hex
            group_name=str(item.get('group_name') or '本地上传').strip()[:40]
            original = dict(url='', group_name=group_name or '本地上传')
            if not item.get('data'):
                raise ValueError('新增图片内容缺失')
        else:
            if key not in known:
                raise ValueError('图片不属于该商品，请重新打开编辑')
            original = known[key]
        position=item.get('position')
        if position is not None and (not isinstance(position,int) or isinstance(position,bool) or position<0 or position>10000):
            raise ValueError('图片排序数据无效')
        if position is None and item.get('add'):
            position=next_position;next_position+=1
        if key in seen or not isinstance(item.get('hidden', False), bool):
            raise ValueError('图片修改数据无效')
        seen.add(key)
        local_path = None
        if item.get('data'):
            content, ext = validate_upload(item['data'])
            local_path = '/images/edit-' + hashlib.sha256(content).hexdigest() + ext
            uploads[local_path] = content
        changes.append((key, original, item.get('hidden', False), local_path, position))
    # Validate everything first; rejected edits never partially change product data.
    base = Path(data_dir) / 'images'; base.mkdir(exist_ok=True)
    for path, content in uploads.items():
        dest = Path(data_dir) / path.lstrip('/')
        if not dest.exists():
            with tempfile.NamedTemporaryFile(dir=base, delete=False) as file:
                temporary = Path(file.name)
            try:
                temporary.write_bytes(content)
                temporary.replace(dest)
            finally:
                temporary.unlink(missing_ok=True)
    stamp = datetime.datetime.now().astimezone().isoformat(timespec='microseconds')
    c.execute('''INSERT INTO listing_edits(market,id,title,price,updated_at) VALUES(?,?,?,?,?)
        ON CONFLICT(market,id) DO UPDATE SET title=excluded.title,price=excluded.price,updated_at=excluded.updated_at''',
              (market, eid, title, common, stamp))
    for kind,key,hidden in rules:
        if hidden:c.execute('INSERT OR REPLACE INTO listing_variant_exclusions VALUES(?,?,?,?,?)',(market,eid,kind,key,stamp))
        else:c.execute('DELETE FROM listing_variant_exclusions WHERE market=? AND product_id=? AND kind=? AND item_key=?',(market,eid,kind,key))
    for sid, amount in skus:
        if not amount:
            c.execute('DELETE FROM listing_sku_edits WHERE market=? AND product_id=? AND sku_id=?', (market, eid, sid))
        else:
            c.execute('''INSERT INTO listing_sku_edits VALUES(?,?,?,?,?) ON CONFLICT(market,product_id,sku_id)
                DO UPDATE SET price=excluded.price,updated_at=excluded.updated_at''', (market, eid, sid, amount, stamp))
    for key, original, hidden, path, position in changes:
        c.execute('''INSERT INTO listing_image_edits
            (market,product_id,asset,source_url,group_name,hidden,local_path,updated_at,position)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(market,product_id,asset)
            DO UPDATE SET group_name=excluded.group_name,hidden=excluded.hidden,
              local_path=COALESCE(excluded.local_path,listing_image_edits.local_path),
              updated_at=excluded.updated_at,position=COALESCE(excluded.position,listing_image_edits.position)''',
                  (market, eid, key, original.get('url', ''), original.get('group_name', '商品图'), int(hidden), path, stamp, position))
    return {'ok': True, 'changed': 1}


def apply_card(c, item):
    """Use local edits in the library while leaving source data unchanged."""
    market, eid = item['market'], item['id']
    if c.execute('SELECT 1 FROM listing_image_edits WHERE market=? AND product_id=? LIMIT 1', (market, eid)).fetchone():
        current = images(c, market, eid)
        item['image'] = (current[0].get('local_path') or current[0]['url']) if current else ''
    common = c.execute('SELECT price,title FROM listing_edits WHERE market=? AND id=?', (market, eid)).fetchone()
    item['name'] = _safe_title(c, market, eid, (common and common[1]) or item.get('name'), item)
    values = variants(c, market, eid, common[0] if common else '')
    # Source prices remain separate; only show a listing price if it was explicitly edited.
    if (common and common[0]) or any(v['edit_price'] for v in values):
        amounts = sorted({Decimal(v['price']) for v in values if v.get('price') is not None})
        currencies = {v.get('currency') for v in values}
        if amounts and len(currencies) == 1:
            item['listing_price'] = (next(iter(currencies)) or '') + ' ' + format(amounts[0], '.2f') + ((' – ' + format(amounts[-1], '.2f')) if len(amounts) > 1 else '')
