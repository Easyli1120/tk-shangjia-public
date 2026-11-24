"""Local saved product data and image archives. No network access or workers."""
import json
import re
import tempfile
import zipfile
import urllib.parse

def migrate(c):
    # Legacy table names are retained for existing saved product data; no task is created.
    for sql in (
        '''CREATE TABLE IF NOT EXISTS gallery_jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT NOT NULL, product_id TEXT NOT NULL,
        url TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', error TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
        discovered INTEGER NOT NULL DEFAULT 0, cached INTEGER NOT NULL DEFAULT 0,
        coverage TEXT, UNIQUE(market,product_id))''',
        '''CREATE INDEX IF NOT EXISTS gallery_jobs_state ON gallery_jobs(state,id)''',
        '''CREATE TABLE IF NOT EXISTS gallery_items(
        job_id INTEGER NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL,
        group_name TEXT NOT NULL, PRIMARY KEY(job_id,url))''',
    ):
        c.execute(sql)
    c.execute('''CREATE TABLE IF NOT EXISTS product_collection(market TEXT NOT NULL,product_id TEXT NOT NULL,sku_json TEXT,price_json TEXT,source_url TEXT,updated_at TEXT,PRIMARY KEY(market,product_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS product_capture(market TEXT,product_id TEXT,product_json TEXT,audit_json TEXT,metadata_ready INTEGER NOT NULL DEFAULT 0,received_at TEXT,PRIMARY KEY(market,product_id))''')


def payload(c, market, product_id):
    row = c.execute('SELECT * FROM gallery_jobs WHERE market=? AND product_id=?', (market, product_id)).fetchone()
    job = dict(row) if row else None
    images = [dict(r) for r in c.execute('''SELECT i.*,g.group_name,g.position FROM gallery_items g
        JOIN images i ON i.url=g.url AND i.market=? AND i.product_id=?
        WHERE g.job_id=? ORDER BY g.position''', (market, product_id, job['id'] if job else -1))]
    if not images:
        images = [dict(r) for r in c.execute('SELECT * FROM images WHERE market=? AND product_id=? ORDER BY id', (market, product_id))]
    return {'images': images, 'url': job['url'] if job else ''}

def download_archive(c, data_dir, market, product_id, images=None):
    """Package this draft's cached gallery in display order, without network I/O."""
    if not c.execute("SELECT 1 FROM draft_entries WHERE market=? AND id=? AND active=1",
                     (market, product_id)).fetchone():
        raise ValueError('该商品不在竞品库中')
    images = [i for i in (payload(c, market, product_id)['images'] if images is None else images) if i.get('local_path')]
    if not images:
        raise ValueError('暂无已保存图片，可在编辑中上传')
    base = (data_dir / 'images').resolve()
    files = []
    for index, item in enumerate(images, 1):
        path = (data_dir / item['local_path'].lstrip('/')).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError('部分缓存图片不可用，暂时无法打包')
        group = re.sub(r'[^\w\-]', '_', item.get('group_name') or '商品图')[:40]
        files.append((path, '%03d_%s%s' % (index, group, path.suffix)))
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

def asset_key(url):
    path = urllib.parse.urlsplit(url).path
    return path.split('~')[0] if '/tos-' in path or '/tt_product/' in path else url

def saved_product_data(c, market, product_id):
    """Read stored source data without visiting a website or scheduling work."""
    row = c.execute('SELECT * FROM product_collection WHERE market=? AND product_id=?', (market, product_id)).fetchone()
    capture = c.execute('SELECT audit_json,product_json FROM product_capture WHERE market=? AND product_id=?', (market, product_id)).fetchone()
    quality = json.loads(capture[0]) if capture and capture[0] else {'variants': []}
    source = json.loads(capture[1]) if capture and capture[1] else {}
    sold = source.get('sold_count')
    sales = {'display': str(sold).strip() if type(sold) in (str,int,float) else '',
             'note': (source.get('sold_count_note') or {}).get('text','')}
    quality.pop('ready', None)
    saved_images = sum(bool(i.get('local_path')) for i in payload(c, market, product_id)['images'])
    return {'saved_images': saved_images, 'skus': json.loads(row['sku_json']) if row and row['sku_json'] else {},
            'prices': json.loads(row['price_json']) if row and row['price_json'] else {},
            'sales': sales, 'description_blocks': [b for b in source.get('desc_blocks',[]) if b.get('type')!='image'],
            'specifications': source.get('specifications',[]),
            'quality': quality, 'updated_at': row['updated_at'] if row else None}


def image_extension(data):
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return '.gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return '.webp'
    if data[4:8] == b'ftyp' and data[8:12] in (b'avif', b'avis'):
        return '.avif'
    return None
