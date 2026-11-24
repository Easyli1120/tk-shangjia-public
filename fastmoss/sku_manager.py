"""Local, source-independent SKU identity and allocation.

The capture payload is evidence only.  ``sku_variants.seller_sku`` is the
canonical value used by the listing workflow and is protected by SQLite's
unique constraint.
"""
import datetime
import json
import re
import secrets


SKU_RE = re.compile(r"^TK-\d{8}-\d{6}(?:-[0-9A-F]{16})?$")
SKU_MAX = 999999


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS sku_products(
      product_id INTEGER PRIMARY KEY,
      source_product_id TEXT NOT NULL,
      source_platform TEXT NOT NULL DEFAULT 'tiktok_shop',
      market TEXT NOT NULL,
      product_title TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(source_platform,market,source_product_id)
    );
    CREATE TABLE IF NOT EXISTS sku_variants(
      variant_id INTEGER PRIMARY KEY,
      product_id INTEGER NOT NULL REFERENCES sku_products(product_id) ON DELETE CASCADE,
      variant_key TEXT NOT NULL,
      source_variant_id TEXT,
      color TEXT,
      size TEXT,
      specification TEXT,
      seller_sku TEXT NOT NULL UNIQUE,
      original_sku TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(product_id,variant_key)
    );
    CREATE INDEX IF NOT EXISTS sku_variants_product ON sku_variants(product_id);
    CREATE INDEX IF NOT EXISTS sku_variants_original ON sku_variants(original_sku);
    CREATE TABLE IF NOT EXISTS sku_counters(
      sku_date TEXT PRIMARY KEY,
      next_number INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sku_change_log(
      id INTEGER PRIMARY KEY,
      variant_id INTEGER NOT NULL REFERENCES sku_variants(variant_id),
      old_sku TEXT,
      new_sku TEXT NOT NULL,
      action TEXT NOT NULL,
      changed_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sku_platform_links(
      id INTEGER PRIMARY KEY,
      variant_id INTEGER NOT NULL REFERENCES sku_variants(variant_id),
      shop_id TEXT NOT NULL,
      remote_product_id TEXT,
      remote_sku_id TEXT,
      state TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(variant_id,shop_id)
    );
    ''')


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec='microseconds')


def _date():
    return datetime.datetime.now().astimezone().strftime('%Y%m%d')


def _text(value):
    return re.sub(r'\s+', ' ', str(value or '').strip())


def _norm(value):
    return _text(value).casefold()


def _attrs(variant):
    attrs = variant.get('sku_sale_props') or variant.get('attributes') or []
    result=[]
    for attr in attrs:
        if not isinstance(attr, dict):
            continue
        name = _text(attr.get('prop_name') or attr.get('name') or attr.get('property_name') or '规格')
        value = _text(attr.get('prop_value') or attr.get('value') or attr.get('property_value') or '')
        if name or value:
            result.append((name, value))
    return result


def variant_key(variant, fallback=''):
    attrs = _attrs(variant)
    if attrs:
        return json.dumps(sorted((_norm(name), _norm(value)) for name, value in attrs), ensure_ascii=False, separators=(',', ':'))
    return '__default__' if not fallback else '__source__:' + str(fallback)


def _fields(variant):
    attrs = _attrs(variant)
    color = next((value for name, value in attrs if _norm(name) in ('color','colour','颜色','色彩','顏色')), '')
    size = next((value for name, value in attrs if _norm(name) in ('size','尺码','尺寸','大小')), '')
    other = [(name, value) for name, value in attrs if _norm(name) not in (
        'color','colour','颜色','色彩','顏色','size','尺码','尺寸','大小')]
    return color, size, json.dumps(other, ensure_ascii=False, separators=(',', ':')) if other else ''


def _original(variant):
    for key in ('seller_sku','sku_code','original_sku'):
        value = variant.get(key)
        if value not in (None, ''):
            return str(value)
    return ''


def _next_sku(c):
    """Allocate under the caller's transaction; SQLite serializes writers."""
    day = _date()
    row = c.execute('SELECT next_number FROM sku_counters WHERE sku_date=?', (day,)).fetchone()
    number = int(row[0]) if row else 1
    while number <= SKU_MAX:
        candidate = f'TK-{day}-{number:06d}-{secrets.token_hex(8).upper()}'
        if not c.execute('SELECT 1 FROM sku_variants WHERE seller_sku=?', (candidate,)).fetchone():
            if row:
                c.execute('UPDATE sku_counters SET next_number=? WHERE sku_date=?', (number + 1, day))
            else:
                c.execute('INSERT INTO sku_counters(sku_date,next_number) VALUES(?,?)', (day, number + 1))
            return candidate
        number += 1
    raise ValueError('今日 SKU 流水号已达到 999999，请明天继续生成')


def _product(c, market, source_product_id, title='', source_platform='tiktok_shop'):
    now = _now()
    current = c.execute('SELECT * FROM sku_products WHERE source_platform=? AND market=? AND source_product_id=?',
                        (source_platform, str(market), str(source_product_id))).fetchone()
    if current:
        if title and str(title) != str(current['product_title'] or ''):
            c.execute('UPDATE sku_products SET product_title=?,updated_at=? WHERE product_id=?', (str(title), now, current['product_id']))
        return c.execute('SELECT * FROM sku_products WHERE product_id=?', (current['product_id'],)).fetchone()
    c.execute('''INSERT INTO sku_products(source_product_id,source_platform,market,product_title,created_at,updated_at)
                 VALUES(?,?,?,?,?,?)''', (str(source_product_id), source_platform, str(market), str(title or ''), now, now))
    return c.execute('SELECT * FROM sku_products WHERE source_platform=? AND market=? AND source_product_id=?',
                     (source_platform, str(market), str(source_product_id))).fetchone()


def ensure_product(c, market, source_product_id, source=None, title='', source_platform='tiktok_shop'):
    """Create/reconcile local product variants and return their rows by source ID/key."""
    # Take the SQLite writer lock before reading the counter.  When the caller
    # already owns a transaction (collection/save/listing request), this is a
    # no-op and the allocation remains part of that transaction.
    if not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')
    source = source or {}
    product = _product(c, market, source_product_id, title or source.get('title',''), source_platform)
    variants = [v for v in source.get('skus', []) if isinstance(v, dict)] if isinstance(source.get('skus'), list) else []
    if not variants:
        variants = [{'id': '__default__', 'sku_id': '__default__', 'sku_sale_props': []}]
    existing_keys = {r['variant_key'] for r in c.execute('SELECT variant_key FROM sku_variants WHERE product_id=?', (product['product_id'],))}
    seen_keys = set()
    for index, variant in enumerate(variants):
        source_id = str(variant.get('sku_id') or variant.get('id') or '')
        base_key = variant_key(variant, source_id or str(index))
        key = base_key
        # A repeated attribute combination in one capture is still two
        # saleable variants.  A later capture with the same combination keeps
        # the first stable row and only appends genuinely new duplicates.
        duplicate_in_capture = base_key in seen_keys
        source_row = c.execute('SELECT * FROM sku_variants WHERE product_id=? AND source_variant_id=?',
                               (product['product_id'], source_id)).fetchone() if source_id else None
        if source_row:
            color, size, specification = _fields(variant)
            fields=(source_id,color,size,specification,_original(variant))
            old=(source_row['source_variant_id'],source_row['color'],source_row['size'],source_row['specification'],source_row['original_sku'])
            if fields != old:
                stable_key=key
                if key != source_row['variant_key'] and c.execute('SELECT 1 FROM sku_variants WHERE product_id=? AND variant_key=?', (product['product_id'], key)).fetchone():
                    stable_key=source_row['variant_key']
                c.execute('''UPDATE sku_variants SET variant_key=?,color=?,size=?,specification=?,original_sku=?,updated_at=?
                             WHERE variant_id=?''', (stable_key,color,size,specification,_original(variant),_now(),source_row['variant_id']))
                key=stable_key
            existing_keys.add(key);seen_keys.add(base_key)
            continue
        if key in existing_keys and not duplicate_in_capture:
            row = c.execute('SELECT * FROM sku_variants WHERE product_id=? AND variant_key=?', (product['product_id'], key)).fetchone()
            if row:
                color, size, specification = _fields(variant)
                fields=(source_id,color,size,specification,_original(variant))
                old=(row['source_variant_id'],row['color'],row['size'],row['specification'],row['original_sku'])
                if fields != old:
                    c.execute('''UPDATE sku_variants SET source_variant_id=?,color=?,size=?,specification=?,original_sku=?,updated_at=?
                                 WHERE variant_id=?''', (*fields, _now(), row['variant_id']))
            seen_keys.add(base_key)
            continue
        # A malformed source can contain the same attribute combination twice.
        # Keep both saleable variants distinct while retaining the stable key for
        # normal recaptures.
        if c.execute('SELECT 1 FROM sku_variants WHERE product_id=? AND variant_key=?', (product['product_id'], key)).fetchone():
            key += '|source:' + (source_id or str(index))
            suffix=1
            while key in existing_keys:
                suffix += 1
                key = base_key + '|source:' + (source_id or str(index)) + ':' + str(suffix)
        color, size, specification = _fields(variant)
        c.execute('''INSERT INTO sku_variants(product_id,variant_key,source_variant_id,color,size,specification,seller_sku,original_sku,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)''',
                  (product['product_id'], key, source_id, color, size, specification, _next_sku(c), _original(variant), _now(), _now()))
        existing_keys.add(key);seen_keys.add(base_key)
    return variant_map(c, market, source_product_id, source_platform)


def variant_map(c, market, source_product_id, source_platform='tiktok_shop'):
    product = c.execute('SELECT product_id FROM sku_products WHERE source_platform=? AND market=? AND source_product_id=?',
                        (source_platform, str(market), str(source_product_id))).fetchone()
    if not product:
        return {'product_id': None, 'by_source': {}, 'by_key': {}, 'rows': []}
    rows = [dict(r) for r in c.execute('''SELECT v.*,
        (SELECT l.state FROM sku_platform_links l
         WHERE l.variant_id=v.variant_id ORDER BY l.updated_at DESC LIMIT 1) AS platform_state
        FROM sku_variants v WHERE v.product_id=? ORDER BY v.variant_id''', (product['product_id'],))]
    return {'product_id': product['product_id'], 'by_source': {str(r['source_variant_id']): r for r in rows if r['source_variant_id']},
            'by_key': {r['variant_key']: r for r in rows}, 'rows': rows}


def resolve(mapping, variant, fallback=''):
    source_id = str(variant.get('sku_id') or variant.get('id') or '')
    row = mapping['by_source'].get(source_id)
    if row:
        return row
    key = variant_key(variant, source_id or fallback)
    return mapping['by_key'].get(key)


def ensure_for_capture(c, market, source_product_id, title='', source=None):
    return ensure_product(c, market, source_product_id, source=source or {}, title=title)


def ensure_for_listing(c, market, source_product_id, product, source=None, title=''):
    """Ensure legacy/test projections also get one local row per visible variant."""
    payload = dict(source or {})
    captured = payload.get('skus') if isinstance(payload.get('skus'), list) else []
    known = {str(v.get('sku_id') or v.get('id') or '') for v in captured}
    for variant in product.get('variants', []) if isinstance(product, dict) else []:
        if not isinstance(variant, dict):
            continue
        sid = str(variant.get('id') or '')
        if sid and sid not in known:
            attrs=[{'prop_name':a.get('name','规格'),'prop_value':a.get('value','')} for a in variant.get('attributes',[]) if isinstance(a,dict)]
            captured.append({'sku_id':sid,'sku_sale_props':attrs})
            known.add(sid)
    payload['skus']=captured
    return ensure_product(c, market, source_product_id, source=payload, title=title)


def backfill(c):
    """Initialize SKU rows for captures already present in an old database."""
    rows = c.execute("SELECT market,product_id,product_json FROM product_capture WHERE product_json IS NOT NULL AND product_json<>''").fetchall()
    seen={(str(row['market']),str(row['product_id'])) for row in rows}
    for row in rows:
        try:
            source = json.loads(row['product_json'])
        except (TypeError, ValueError):
            continue
        ensure_product(c, row['market'], row['product_id'], source=source, title=source.get('title',''))
    # Older databases may only have product_collection, which predates the
    # richer product_capture record.  Use its saved variant evidence too.
    for row in c.execute("SELECT market,product_id,sku_json FROM product_collection WHERE sku_json IS NOT NULL AND sku_json<>''").fetchall():
        key=(str(row['market']),str(row['product_id']))
        if key in seen:
            continue
        try:
            source=json.loads(row['sku_json'])
        except (TypeError, ValueError):
            continue
        title_row=c.execute('SELECT name FROM records WHERE kind=\'product\' AND market=? AND id=?', (row['market'],row['product_id'])).fetchone()
        ensure_product(c,row['market'],row['product_id'],source=source,title=title_row[0] if title_row else '')


def validate_sku(value):
    value = str(value or '').strip()
    if not value or len(value) < 6 or len(value) > 50 or re.search(r'\s', value) or any(ord(ch) < 32 for ch in value):
        raise ValueError('商家 SKU 需为 6～50 个字符且不能含空白')
    return value


def apply_skus(c, market, source_product_id, values, source=None, title=''):
    """Persist manual SKU edits, logging every change and enforcing uniqueness."""
    mapping = ensure_product(c, market, source_product_id, source=source or {}, title=title)
    if not isinstance(values, dict):
        raise ValueError('SKU 修改格式无效')
    rows = mapping['rows']
    by_source = mapping['by_source']
    changes=[]
    for source_id, value in values.items():
        row = by_source.get(str(source_id))
        if not row:
            raise ValueError('SKU 不属于该商品，请刷新上架页面')
        new = validate_sku(value)
        if new != row['seller_sku']:
            if c.execute("SELECT 1 FROM sku_change_log WHERE variant_id=? AND old_sku=? AND new_sku=? AND action='upgrade_unpublished'",
                         (row['variant_id'],new,row['seller_sku'])).fetchone():
                raise ValueError('本地 SKU 已升级，请刷新页面后重新打开商品，避免旧页面覆盖新编码')
            changes.append((row, new))
    if len({new for _, new in changes} | {r['seller_sku'] for r in rows if all(r['variant_id'] != x[0]['variant_id'] for x in changes)}) != len(rows):
        raise ValueError('商家 SKU 不能重复')
    now = _now()
    try:
        for row, new in changes:
            c.execute('UPDATE sku_variants SET seller_sku=?,updated_at=? WHERE variant_id=?', (new, now, row['variant_id']))
            c.execute('INSERT INTO sku_change_log(variant_id,old_sku,new_sku,action,changed_at) VALUES(?,?,?,?,?)',
                      (row['variant_id'], row['seller_sku'], new, 'manual_edit', now))
    except Exception as exc:
        if 'UNIQUE constraint failed: sku_variants.seller_sku' in str(exc):
            raise ValueError('商家 SKU 已存在，不能重复') from None
        raise
    return variant_map(c, market, source_product_id)


def values_for(c, market, source_product_id, source=None, title=''):
    mapping = ensure_product(c, market, source_product_id, source=source or {}, title=title)
    return {str(r['source_variant_id']): r['seller_sku'] for r in mapping['rows'] if r['source_variant_id']}


def validate_product(c, market, source_product_id, source=None, title=''):
    mapping = ensure_product(c, market, source_product_id, source=source or {}, title=title)
    rows = mapping['rows']
    errors=[]
    seen=set()
    for row in rows:
        sku=row['seller_sku']
        try: validate_sku(sku)
        except ValueError as exc: errors.append({'variant_id':row['variant_id'],'sku':sku,'error':str(exc)})
        if not sku: errors.append({'variant_id':row['variant_id'],'sku':sku,'error':'SKU 为空'})
        if sku in seen: errors.append({'variant_id':row['variant_id'],'sku':sku,'error':'SKU 重复'})
        seen.add(sku)
    return {'ok':not errors,'errors':errors,'rows':rows}


def record_platform_links(c, shop_id, remote_product_id, seller_rows, state='submitted'):
    now = _now()
    for item in seller_rows or []:
        seller = str(item.get('seller_sku') or '')
        remote_sku = str(item.get('id') or item.get('sku_id') or item.get('global_sku_id') or '')
        row = c.execute('SELECT variant_id FROM sku_variants WHERE seller_sku=?', (seller,)).fetchone()
        if not row:
            continue
        c.execute('''INSERT INTO sku_platform_links(variant_id,shop_id,remote_product_id,remote_sku_id,state,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?) ON CONFLICT(variant_id,shop_id) DO UPDATE SET remote_product_id=excluded.remote_product_id,remote_sku_id=excluded.remote_sku_id,state=excluded.state,updated_at=excluded.updated_at''',
                  (row['variant_id'], str(shop_id), str(remote_product_id or ''), remote_sku, state, now, now))


def list_skus(c, query=''):
    query = str(query or '').strip()
    args=[]; where=''
    if query:
        like='%'+query+'%';where=' WHERE p.product_title LIKE ? OR v.seller_sku LIKE ? OR v.original_sku LIKE ? OR p.source_product_id LIKE ?';args=[like]*4
    return [dict(r) for r in c.execute('''SELECT p.product_id,p.source_product_id,p.source_platform,p.market,p.product_title,p.created_at product_created_at,
        v.variant_id,v.color,v.size,v.specification,v.seller_sku,v.original_sku,v.created_at sku_created_at,
        l.shop_id,l.remote_product_id,l.remote_sku_id,l.state platform_state
        FROM sku_products p JOIN sku_variants v ON v.product_id=p.product_id
        LEFT JOIN sku_platform_links l ON l.variant_id=v.variant_id'''+where+' ORDER BY v.variant_id', args)]
