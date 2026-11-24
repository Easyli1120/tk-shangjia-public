#!/usr/bin/env python3
"""FastMoss POD AI: local, dependency-free service."""
import cgi, csv, datetime, hashlib, io, json, mimetypes, os, re, signal, sqlite3, threading, urllib.parse, zipfile
from scoring import rank_products, WEIGHTS, VERSION
import history
import batch_delete
import backup_manager
import product_assets
import collection_queue
import listing_editor
import library_categories
import listing_trash
import tiktok_shop
import shop_listing
import innovation
import visual_analysis
import model_manager
import sku_manager
import runtime_paths
from database import ClosingConnection
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer as ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parent
DATA=runtime_paths.data_directory(ROOT)
DB=runtime_paths.database_path(DATA)
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
PHEAD=['Product Name','Product Status','Store Name','Country/Region','Product Category','Selling Price / Price','Commission Rate','7-Day Sales Volume','7-Day GMV','28-Day Sales Volume','28-Day GMV','Total Sales Volume','Total GMV','Total Number of Creators','Creator Sales rate','Total Number of Promotional Videos','Total Number of Promotional Livestreams','Product Image','FastMoss','TikTok','FastMoss Shop','Estimated Launch date']
SHEAD=['Shop Name','Company Name','Shop Positioning','Country','Main Category','Shop Rating','Sales in Last 7 Days','Revenue in Last 7 Days','Active Products in the Last 7 Days','Proportion of New Product Sales','Total Sales','Total Revenue','Number of Products On Sale','Number of Influencers','FastMoss Shop Detail Page Link']
def db():
 c=sqlite3.connect(DB,timeout=30,factory=ClosingConnection); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def init():
 with db() as c:
  c.executescript('''
 CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY, filename TEXT, imported_at TEXT, source_time TEXT, source_time_basis TEXT, kind TEXT, total INTEGER, imported INTEGER, errors TEXT);
 CREATE TABLE IF NOT EXISTS records(kind TEXT, market TEXT, id TEXT, name TEXT, shop_id TEXT, shop_name TEXT, image TEXT, category TEXT, price TEXT, sales7 REAL, gmv7 REAL, sales28 REAL, gmv28 REAL, sales_total REAL, gmv_total REAL, snapshot_id INTEGER, PRIMARY KEY(kind,market,id));
 CREATE INDEX IF NOT EXISTS records_shop ON records(kind,market,shop_id);
 CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, batch_id INTEGER REFERENCES batches(id), kind TEXT, market TEXT, entity_id TEXT, row_number INTEGER, raw TEXT, normalized TEXT);
 CREATE INDEX IF NOT EXISTS snapshots_entity ON snapshots(kind,market,entity_id,id);
 CREATE TABLE IF NOT EXISTS actions(market TEXT,id TEXT,status TEXT, tags TEXT, notes TEXT, favorite INTEGER DEFAULT 0, updated_at TEXT, PRIMARY KEY(market,id));
 CREATE TABLE IF NOT EXISTS action_history(id INTEGER PRIMARY KEY,market TEXT,product_id TEXT,status TEXT,changed_at TEXT);
 CREATE TABLE IF NOT EXISTS images(id INTEGER PRIMARY KEY,market TEXT,product_id TEXT,url TEXT,local_path TEXT,state TEXT,error TEXT, batch_id INTEGER, UNIQUE(market,product_id,url));
 CREATE TABLE IF NOT EXISTS image_tasks(id INTEGER PRIMARY KEY,market TEXT,product_id TEXT,state TEXT,error TEXT,created_at TEXT,updated_at TEXT);
 CREATE TABLE IF NOT EXISTS ranking_batches(id INTEGER PRIMARY KEY, created_at TEXT, total INTEGER, ranked INTEGER, insufficient INTEGER, auto_count INTEGER, review_count INTEGER, low_count INTEGER, formula_version TEXT, weights TEXT, imported_batch INTEGER);
 CREATE TABLE IF NOT EXISTS ranking_results(batch_id INTEGER,market TEXT,id TEXT,rank INTEGER,score REAL,band TEXT,completeness REAL,components TEXT,product_batch INTEGER,PRIMARY KEY(batch_id,market,id));
 CREATE TABLE IF NOT EXISTS draft_entries(market TEXT,id TEXT,active INTEGER NOT NULL DEFAULT 1,source TEXT,created_at TEXT,reason TEXT,entry_rank INTEGER,entry_score REAL,ranking_batch INTEGER,removed_at TEXT,PRIMARY KEY(market,id));
 CREATE TABLE IF NOT EXISTS draft_events(id INTEGER PRIMARY KEY,market TEXT,product_id TEXT,event TEXT,source TEXT,created_at TEXT,ranking_batch INTEGER,rank INTEGER,score REAL,reason TEXT);
 CREATE TABLE IF NOT EXISTS review_exclusions(ranking_batch INTEGER,market TEXT,id TEXT,created_at TEXT,PRIMARY KEY(ranking_batch,market,id));
 CREATE TABLE IF NOT EXISTS trend_batches(id INTEGER PRIMARY KEY, market TEXT NOT NULL, product_id TEXT NOT NULL, fetched_at TEXT NOT NULL, request_id TEXT, region TEXT, currency TEXT, response_json TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS trend_batches_product ON trend_batches(market,product_id,id DESC);
 CREATE TABLE IF NOT EXISTS trend_points(batch_id INTEGER NOT NULL REFERENCES trend_batches(id), date TEXT NOT NULL, units_sold INTEGER, gmv REAL, PRIMARY KEY(batch_id,date));
 CREATE TABLE IF NOT EXISTS gallery_settings(key TEXT PRIMARY KEY,value TEXT);
 CREATE TABLE IF NOT EXISTS product_scores(market TEXT,id TEXT,score REAL,score_exact REAL,rank INTEGER,band TEXT,shop_rating REAL,product_batch INTEGER,shop_batch INTEGER,components TEXT,computed_at TEXT,PRIMARY KEY(market,id));
 ''')
  cols={row[1] for row in c.execute('PRAGMA table_info(product_scores)')}
  for name,typ in [('completeness','REAL'),('previous_rank','INTEGER'),('ranking_batch','INTEGER')]:
   if name not in cols:c.execute('ALTER TABLE product_scores ADD COLUMN '+name+' '+typ)
  c.execute('CREATE TABLE IF NOT EXISTS listing_edits(market TEXT,id TEXT,title TEXT,price TEXT,updated_at TEXT,PRIMARY KEY(market,id))')
  sku_manager.migrate(c)
  product_assets.migrate(c)
  collection_queue.migrate(c)
  library_categories.migrate(c)
  listing_editor.migrate(c)
  shop_listing.migrate(c)
  shop_listing.upgrade_unpublished_skus(c)
  listing_trash.migrate(c)
  innovation.migrate(c)
  visual_analysis.migrate(c)
  model_manager.seed(c,ROOT)
  sku_manager.backfill(c)
  c.execute("INSERT OR IGNORE INTO draft_entries(market,id,source,created_at,reason) SELECT market,id,'历史记录',updated_at,'升级前已有选品池，来源未知' FROM actions WHERE status IS NOT NULL")
 with db() as c:
  history.migrate(c); history.backfill(c); history.suppress_legacy_duplicate_comparisons(c)
def now(): return datetime.datetime.now().astimezone().isoformat(timespec='seconds')
def number(x):
 s=str('' if x is None else x).strip().replace(',','').replace('$',''); m=re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*([kKmMbB%]?)',s)
 if not m:return None
 return float(m[1])*{'k':1000,'m':1000000,'b':1000000000,'%':.01,'':1}[m[2].lower()]
def ident(url):
 m=re.search(r'/([0-9]{8,})(?:[/?#]|$)',str(url or '')); return m.group(1) if m else None
def colnum(ref):
 n=0
 for ch in re.match(r'[A-Z]+',ref)[0]: n=n*26+ord(ch)-64
 return n-1
def read_rows(name,content):
 if name.lower().endswith('.csv'):
  text=content.decode('utf-8-sig'); return list(csv.reader(io.StringIO(text)))
 if not name.lower().endswith('.xlsx'): raise ValueError('仅支持 .xlsx 和 UTF-8 CSV 文件')
 with zipfile.ZipFile(io.BytesIO(content)) as z:
  names=z.namelist(); shared=[]
  if 'xl/sharedStrings.xml' in names:
   shared=[''.join(t.text or '' for t in e.iter('{%s}t'%NS['s'])) for e in ET.fromstring(z.read('xl/sharedStrings.xml'))]
  sheets=[n for n in names if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',n)]
  for sheet in sheets:
   root=ET.fromstring(z.read(sheet)); rows=[]
   for row in root.findall('.//s:sheetData/s:row',NS):
    vals=[]
    for cell in row.findall('s:c',NS):
     idx=colnum(cell.get('r')); vals.extend(['']*max(0,idx+1-len(vals))); v=cell.find('s:v',NS); typ=cell.get('t')
     vals[idx]=shared[int(v.text)] if typ=='s' and v is not None else (''.join(t.text or '' for t in cell.iter('{%s}t'%NS['s'])) if typ=='inlineStr' else (v.text if v is not None else ''))
    rows.append(vals)
   if rows:
    for pos,row in enumerate(rows[:20]):
     headers=set(str(x).strip() for x in row)
     if set(PHEAD).issubset(headers) or set(SHEAD).issubset(headers):return rows[pos:]
  raise ValueError('未找到包含完整 FastMoss 字段的工作表')
def import_file(name, content):
 rows=read_rows(name,content)
 if not rows: raise ValueError('文件为空')
 headers=[str(x).strip() for x in rows[0]]; h=set(headers)
 kind='product' if set(PHEAD).issubset(h) else 'shop' if set(SHEAD).issubset(h) else None
 if not kind: raise ValueError('无法识别文件：缺少商品或店铺的必需列')
 stamp=re.search(r'(20\d{6})[_-](\d{6})',name); source=None
 if stamp:
  try: source=datetime.datetime.strptime(''.join(stamp.groups()),'%Y%m%d%H%M%S').isoformat(sep=' ')
  except ValueError: pass
 errors=[]; imported=0
 with db() as c:
  if kind=='product':
   prior=history.find_existing_file(c,name,content,headers,rows)
   if prior:return {'batch':None,'kind':kind,'total':len(rows)-1,'imported':0,'duplicate_of':prior,'errors':['文件已导入，未重复创建快照']}
  uploaded=now()
  cur=c.execute('INSERT INTO batches(filename,imported_at,source_time,source_time_basis,kind,total,imported,errors) VALUES(?,?,?,?,?,?,0,?)',(name,uploaded,source,'文件名推定，非数据统计截止时间' if source else '未知',kind,len(rows)-1,'[]')); bid=cur.lastrowid
  if kind=='product':
   hb,_=history.begin_batch(c,bid,name,content,len(rows)-1,uploaded); counts=dict.fromkeys(('new','existing','duplicate','failed'),0)
  for i,values in enumerate(rows[1:],2):
   raw={k:(values[j] if j<len(values) else '') for j,k in enumerate(headers) if k}; market=str(raw.get('Country/Region' if kind=='product' else 'Country','')).strip().upper(); eid=ident(raw.get('TikTok') or raw.get('FastMoss')) if kind=='product' else ident(raw.get('FastMoss Shop Detail Page Link'))
   # A row with no stable ID is retained as a raw snapshot, but never guessed by name.
   normalized={k:number(v) for k,v in raw.items() if number(v) is not None}
   cur=c.execute('INSERT INTO snapshots(batch_id,kind,market,entity_id,row_number,raw,normalized) VALUES(?,?,?,?,?,?,?)',(bid,kind,market,eid,i,json.dumps(raw,ensure_ascii=False),json.dumps(normalized,ensure_ascii=False))); sid=cur.lastrowid
   if kind=='product':counts[history.ingest_row(c,hb,raw,i,sid)]+=1
   if not eid or not market: errors.append({'row':i,'reason':'缺少可识别的 ID 或市场；原始行已保留'}); continue
   if kind=='product':
    if not c.execute("SELECT 1 FROM records WHERE kind='product' AND market=? AND id=?",(market,eid)).fetchone():
     listing_trash.reset_collected_data(c,market,eid)
    shopid=ident(raw['FastMoss Shop']); values2=(kind,market,eid,raw['Product Name'],shopid,raw['Store Name'],raw['Product Image'],raw['Product Category'],raw['Selling Price / Price'],number(raw['7-Day Sales Volume']),number(raw['7-Day GMV']),number(raw['28-Day Sales Volume']),number(raw['28-Day GMV']),number(raw['Total Sales Volume']),number(raw['Total GMV']),sid)
   else:
    values2=(kind,market,eid,raw['Shop Name'],None,None,None,raw['Main Category'],None,number(raw['Sales in Last 7 Days']),number(raw['Revenue in Last 7 Days']),None,None,number(raw['Total Sales']),number(raw['Total Revenue']),sid)
   c.execute('''INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(kind,market,id) DO UPDATE SET name=excluded.name,shop_id=excluded.shop_id,shop_name=excluded.shop_name,image=excluded.image,category=excluded.category,price=excluded.price,sales7=excluded.sales7,gmv7=excluded.gmv7,sales28=excluded.sales28,gmv28=excluded.gmv28,sales_total=excluded.sales_total,gmv_total=excluded.gmv_total,snapshot_id=excluded.snapshot_id''',values2)
   if kind=='product' and raw['Product Image']:
    c.execute("INSERT OR IGNORE INTO images(market,product_id,url,state,batch_id) VALUES(?,?,?,'待下载',?)",(market,eid,raw['Product Image'],bid))
   if kind=='product':add_draft(c,market,eid,'文件导入','导入竞品库')
   imported+=1
  c.execute('UPDATE batches SET imported=?, errors=? WHERE id=?',(imported,json.dumps(errors,ensure_ascii=False),bid))
  if kind=='product':history.finish_batch(c,hb,counts)
 return {'batch':bid,'kind':kind,'total':len(rows)-1,'imported':imported,'errors':errors,'history':counts if kind=='product' else None}
def add_draft(c,market,eid,source,reason,ranking_batch=None):
 if c.execute("SELECT 1 FROM listing_trash WHERE market=? AND id=?",(market,eid)).fetchone():return False
 row=c.execute("SELECT sc.rank,sc.score_exact,sc.band FROM records r LEFT JOIN product_scores sc ON sc.market=r.market AND sc.id=r.id WHERE r.kind='product' AND r.market=? AND r.id=?",(market,eid)).fetchone()
 if not row: raise ValueError('商品不存在')
 entry=c.execute('SELECT active,reason FROM draft_entries WHERE market=? AND id=?',(market,eid)).fetchone()
 if entry and entry['active']:
  return False
 stamp=now(); rank=row['rank'];score=row['score_exact']
 c.execute("INSERT INTO draft_entries(market,id,active,source,created_at,reason,entry_rank,entry_score,ranking_batch,removed_at) VALUES(?,?,1,?,?,?,?,?,?,NULL) ON CONFLICT(market,id) DO UPDATE SET active=1,source=excluded.source,created_at=excluded.created_at,reason=excluded.reason,entry_rank=excluded.entry_rank,entry_score=excluded.entry_score,ranking_batch=excluded.ranking_batch,removed_at=NULL",(market,eid,source,stamp,reason,rank,score,ranking_batch))
 c.execute("INSERT INTO draft_events(market,product_id,event,source,created_at,ranking_batch,rank,score,reason) VALUES(?,?,'加入',?,?,?,?,?,?)",(market,eid,source,stamp,ranking_batch,rank,score,reason))
 c.execute("INSERT INTO actions(market,id,status,tags,notes,favorite,updated_at) VALUES(?,?,NULL,'','',0,?) ON CONFLICT(market,id) DO NOTHING",(market,eid,stamp))
 return True

def refresh_scores(imported_batch=None,connection=None):
 if connection is not None:return _refresh_scores(connection,imported_batch)
 with db() as c:
  c.execute('BEGIN IMMEDIATE')
  return _refresh_scores(c,imported_batch)
def _refresh_scores(c,imported_batch=None):
 rows=c.execute("""SELECT p.*,ps.batch_id product_batch,ps.raw product_raw,ss.batch_id shop_batch,ss.raw shop_raw
   FROM records p JOIN snapshots ps ON ps.id=p.snapshot_id
   LEFT JOIN records sh ON sh.kind='shop' AND sh.market=p.market AND sh.id=p.shop_id
   LEFT JOIN snapshots ss ON ss.id=sh.snapshot_id WHERE p.kind='product'""").fetchall()
 products=[]
 for row in rows:
  p=dict(row); raw=json.loads(p.pop('product_raw') or '{}'); shop=json.loads(p.pop('shop_raw') or '{}')

  for key,header in [('sales7','7-Day Sales Volume'),('gmv7','7-Day GMV'),('sales28','28-Day Sales Volume'),('gmv28','28-Day GMV'),('sales_total','Total Sales Volume'),('gmv_total','Total GMV')]:p[key]=number(raw.get(header))
  c.execute("UPDATE records SET sales7=?,gmv7=?,sales28=?,gmv28=?,sales_total=?,gmv_total=? WHERE kind='product' AND market=? AND id=?",tuple(p[k] for k in ('sales7','gmv7','sales28','gmv28','sales_total','gmv_total'))+(p['market'],p['id']))
  p['shop_rating']=number(shop.get('Shop Rating'))
  p['creators']=number(raw.get('Total Number of Creators'));p['videos']=number(raw.get('Total Number of Promotional Videos'));p['livestreams']=number(raw.get('Total Number of Promotional Livestreams'))
  products.append(p)
 ranked=rank_products(products); stamp=now()
 counts={band:sum(p['band']==band for p in ranked) for band in ('自动入箱','人工筛选','低优先级','数据不足')}
 previous={(r['market'],r['id']):r['rank'] for r in c.execute('SELECT market,id,rank FROM product_scores')} if c.execute('SELECT 1 FROM ranking_batches WHERE formula_version=? LIMIT 1',(VERSION,)).fetchone() else {}
 bid=c.execute('INSERT INTO ranking_batches(created_at,total,ranked,insufficient,auto_count,review_count,low_count,formula_version,weights,imported_batch) VALUES(?,?,?,?,?,?,?,?,?,?)',(stamp,len(ranked),len(ranked)-counts['数据不足'],counts['数据不足'],counts['自动入箱'],counts['人工筛选'],counts['低优先级'],VERSION,json.dumps(WEIGHTS),imported_batch)).lastrowid
 c.execute('DELETE FROM product_scores')
 for p in ranked:
  components=json.dumps(p['components'],ensure_ascii=False)
  c.execute('INSERT INTO product_scores(market,id,score,score_exact,rank,band,shop_rating,product_batch,shop_batch,components,computed_at,completeness,previous_rank,ranking_batch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(p['market'],p['id'],p['score_exact'],p['score_exact'],p['rank'],p['band'],p['shop_rating'],p['product_batch'],p['shop_batch'],components,stamp,p['completeness'],previous.get((p['market'],p['id'])),bid))
  c.execute('INSERT INTO ranking_results VALUES(?,?,?,?,?,?,?,?,?)',(bid,p['market'],p['id'],p['rank'],p['score_exact'],p['band'],p['completeness'],components,p['product_batch']))
 return {'batch':bid,'total':len(ranked),'ranked':len(ranked)-counts['数据不足'],**counts}
def asdict(row): return dict(row) if row else None
def latest_trend(c,market,eid):
 batch=c.execute('SELECT id,fetched_at,request_id,region,currency FROM trend_batches WHERE market=? AND product_id=? ORDER BY id DESC LIMIT 1',(market,eid)).fetchone()
 if not batch:return None
 result=dict(batch); result['points']=[dict(r) for r in c.execute('SELECT date,units_sold,gmv FROM trend_points WHERE batch_id=? ORDER BY date',(batch['id'],))];return result

def result_rows(c,kind,q):
 where=['r.kind=?']; args=[kind]; search=q.get('q',[''])[0].strip()
 if search:
  where.append('(r.name LIKE ? OR r.id LIKE ? OR r.shop_name LIKE ? OR EXISTS (SELECT 1 FROM listing_edits le WHERE le.market=r.market AND le.id=r.id AND le.title LIKE ?))');args.extend(['%'+search+'%']*4)
 if q.get('shop',[''])[0]: where.append('r.shop_id=?'); args.append(q['shop'][0])
 if q.get('selected',[''])[0]=='1': where.append('de.active=1')
 if q.get('draft_filter',[''])[0]=='in':where.append('de.active=1')
 if q.get('draft_filter',[''])[0]=='out':where.append('COALESCE(de.active,0)=0')
 band=q.get('band',[''])[0]
 if kind=='product' and band in ('自动入箱','人工筛选','低优先级','数据不足'):where.append('sc.band=?');args.append(band)
 if q.get('exclude_review',[''])[0]=='1':where.append("NOT EXISTS (SELECT 1 FROM review_exclusions ex WHERE ex.ranking_batch=sc.ranking_batch AND ex.market=r.market AND ex.id=r.id)")
 if kind=='product' and q.get('potential',[''])[0]=='1': where.append("sc.band='人工筛选'")
 order={'sales7':'r.sales7','gmv7':'r.gmv7','sales28':'r.sales28','gmv28':'r.gmv28','total':'r.sales_total','name':'r.name','recent':'r.snapshot_id','score':'sc.rank'}.get(q.get('sort',['recent'])[0],'r.snapshot_id'); page=max(1,int(q.get('page',['1'])[0])); limit=min(100,max(1,int(q.get('limit',['24'])[0]))); base=" FROM records r LEFT JOIN actions a ON a.market=r.market AND a.id=r.id LEFT JOIN product_scores sc ON sc.market=r.market AND sc.id=r.id AND r.kind='product' LEFT JOIN draft_entries de ON de.market=r.market AND de.id=r.id WHERE "+' AND '.join(where)
 category_groups=[];category_expr="COALESCE(NULLIF(TRIM(r.category),''),'未分类')"
 leaf_projection=''
 if kind=='product' and q.get('draft_filter',[''])[0]=='in':
  selected=str(tiktok_shop.load(DATA).get('selected_shop_id') or '')
  availability_join=" LEFT JOIN tiktok_product_availability av ON av.market=r.market AND av.product_id=r.id"
  base=base.replace(' WHERE ',library_categories.JOINS+library_categories.PROFILE_JOIN+availability_join+' WHERE ',1)
  shop_id_sql="'"+selected.replace("'","''")+"'"
  resolved="CASE WHEN json_extract(cp.profile,'$.category_shop_id')="+shop_id_sql+" THEN NULLIF(json_extract(cp.profile,'$.category_path'),'') END"
  category_expr="CASE WHEN av.state='regional_unavailable' THEN '"+library_categories.SKIPPED+"' ELSE COALESCE("+resolved+",NULLIF(leaf.path,''),'"+library_categories.PENDING+"') END"
  leaf_projection=", "+category_expr+" AS listing_category, CASE WHEN av.state IS NOT 'regional_unavailable' AND json_extract(cp.profile,'$.category_shop_id')="+shop_id_sql+" AND NULLIF(leaf.path,'') IS NULL AND ("+resolved+") IS NULL THEN json_extract(cp.profile,'$.category_sync.message') END AS category_message"
  selected_categories=[]
  for raw_category in q.get('category',[]):
   try:
    parsed=json.loads(raw_category)
   except (TypeError,ValueError):
    parsed=raw_category
   selected_categories.extend(parsed if isinstance(parsed,list) else [parsed])
  selected_categories=list(dict.fromkeys(str(value) for value in selected_categories if str(value).strip()))
  category_groups=[dict(row) for row in c.execute('SELECT '+category_expr+' AS category, count(*) AS count'+base+' GROUP BY '+category_expr+' ORDER BY count(*) DESC, category ASC',args)]
  if selected_categories:
   base+=' AND '+category_expr+' IN ('+','.join('?' for _ in selected_categories)+')';args.extend(selected_categories)
 total=c.execute('SELECT count(*)'+base,args).fetchone()[0]
 sql="SELECT r.*,(SELECT av.state FROM tiktok_product_availability av WHERE av.market=r.market AND av.product_id=r.id) collection_availability"+leaf_projection+',sc.score,sc.rank,sc.band,sc.shop_rating,sc.product_batch,sc.shop_batch,sc.components,sc.completeness,sc.previous_rank,sc.ranking_batch,de.active draft_active,de.source draft_source,de.created_at draft_created_at,de.entry_rank,de.entry_score,de.reason draft_reason,a.status,a.tags,a.notes,a.favorite,(SELECT count(*) FROM images i WHERE i.market=r.market AND i.product_id=r.id) image_count,(SELECT count(*) FROM records p WHERE p.kind=\'product\' AND p.market=r.market AND p.shop_id=r.id) product_count,(SELECT count(*) FROM sku_variants sv JOIN sku_products sp ON sp.product_id=sv.product_id WHERE sp.market=r.market AND sp.source_product_id=r.id) local_sku_count,(SELECT group_concat(sv.seller_sku, \' · \') FROM sku_variants sv JOIN sku_products sp ON sp.product_id=sv.product_id WHERE sp.market=r.market AND sp.source_product_id=r.id) local_skus'+base+' ORDER BY '+order+(' IS NULL ASC, sc.rank ASC' if order=='sc.rank' else ' ASC' if order=='r.name' else ' DESC')+', r.market ASC, r.id ASC LIMIT ? OFFSET ?'
 items=[dict(x) for x in c.execute(sql,args+[limit,(page-1)*limit])]
 if kind=='product':
  for x in items:
   x['trend']=latest_trend(c,x['market'],x['id'])
   img=c.execute("SELECT local_path,url FROM images WHERE market=? AND product_id=? ORDER BY CASE WHEN local_path IS NOT NULL AND local_path<>'' THEN 0 ELSE 1 END,id LIMIT 1",(x['market'],x['id'])).fetchone()
   if img:x['image']=img['local_path'] or x['image'] or img['url']
   if q.get('selected',[''])[0]=='1':x['draftDetail']=product_assets.payload(c,x['market'],x['id'])
   if q.get('draft_filter',[''])[0]=='in':
    x['details']=product_assets.saved_product_data(c,x['market'],x['id'])
    listing_editor.apply_card(c,x)
 return {'total':total,'page':page,'items':items,'category_groups':category_groups,'category_sync':dict(shop_listing._category_sync_state) if kind=='product' else {}}
def detail(c,kind,market,eid):
 row=c.execute('SELECT r.*,s.raw,s.normalized,b.id batch_id,b.filename,b.imported_at,b.source_time,b.source_time_basis FROM records r JOIN snapshots s ON s.id=r.snapshot_id JOIN batches b ON b.id=s.batch_id WHERE r.kind=? AND r.market=? AND r.id=?',(kind,market,eid)).fetchone()
 if not row:return None
 d=dict(row);d['score_info']=asdict(c.execute('SELECT * FROM product_scores WHERE market=? AND id=?',(market,eid)).fetchone()) if kind=='product' else None;d['raw']=json.loads(d['raw']);d['normalized']=json.loads(d['normalized']);d['images']=[dict(x) for x in c.execute('SELECT * FROM images WHERE market=? AND product_id=?',(market,eid))] if kind=='product' else []; d['history']=[dict(x) for x in c.execute('SELECT s.id,s.raw,b.filename,b.imported_at,b.source_time FROM snapshots s JOIN batches b ON b.id=s.batch_id WHERE s.kind=? AND s.market=? AND s.entity_id=? ORDER BY s.id DESC',(kind,market,eid))];d['details']=product_assets.saved_product_data(c,market,eid) if kind=='product' else None;d['draft']=asdict(c.execute('SELECT * FROM draft_entries WHERE market=? AND id=?',(market,eid)).fetchone());d['action']=asdict(c.execute('SELECT * FROM actions WHERE market=? AND id=?',(market,eid)).fetchone()); return d
class Handler(BaseHTTPRequestHandler):
 def log_message(self, fmt, *args):
  if self.path.startswith("/api/shop/") or tiktok_shop.is_callback_request(self.path):return
  super().log_message(fmt,*args)
 def respond(self,obj,status=200):
  b=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');
  self.end_headers();self.wfile.write(b)
 def do_OPTIONS(self):
  self.send_error(405)
 def do_GET(self):
  if not tiktok_shop.local_request_allowed(self):return self.respond({'error':'请从本机软件地址访问'},403)
  if tiktok_shop.is_callback_request(self.path):
   tiktok_shop.receive_callback(self,DATA,'http://'+self.headers.get('Host','')+urllib.parse.urlsplit(self.path).path);return
  if tiktok_shop.handle(self,__import__(__name__)):return
  u=urllib.parse.urlparse(self.path); path=u.path; q=urllib.parse.parse_qs(u.query)
  try:
   with db() as c:
    if path=='/api/tiktok/jobs':return self.respond({'jobs':collection_queue.jobs(c),'pacing':collection_queue.pacing_status(c),'worker_error':collector.last_error if collector else '采集服务尚未启动','browser_state':collector.browser_state if collector else 'closed','needs_verification':bool(c.execute("SELECT 1 FROM tiktok_items WHERE error_code='verification' AND state IN ('failed','retry') LIMIT 1").fetchone())})
    if path=='/api/tiktok/items':return self.respond(collection_queue.items(c,int(q.get('id',['0'])[0]),int(q.get('page',['1'])[0]),q.get('state',[''])[0]))
    if path=='/api/tiktok/export':
     jid=int(q.get('id',['0'])[0]);collection_queue.job_status(c,jid)
     rows=c.execute("SELECT url FROM tiktok_items WHERE job_id=? AND state IN ('failed','partial','retry','cancelled') ORDER BY position",(jid,)).fetchall()
     content=('\n'.join(r[0] for r in rows)+'\n').encode('utf-8');self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Content-Disposition','attachment; filename="tiktok-incomplete-%d.txt"'%jid);self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
    if path.startswith(('/api/browser/','/api/collection-run')) or path in ('/api/tasks','/api/collector-control','/api/link-import','/api/trend/refresh'):return self.respond({'error':'商品采集功能已删除'},410)
    if path.startswith(('/api/ai/','/api/visual/','/api/innovation/')): return self.respond({'error':'功能已停用'},404)
    if path=='/api/listing-trash':return self.respond(listing_trash.items(c))
    if path=='/api/listing-edits':return self.respond([dict(x) for x in c.execute('SELECT * FROM listing_edits')])
    if path=='/api/listing-payload':return self.respond(listing_editor.listing_payload(c,q.get('market',['US'])[0],q.get('id',[''])[0]))
    if path=='/api/listing-editor':return self.respond(listing_editor.payload(c,q.get('market',['US'])[0],q.get('id',[''])[0]))
    if path=='/api/ai/models':return self.respond(model_manager.public(c,ROOT,DATA))
    if path=='/api/visual/product':return self.respond(visual_analysis.inventory(c,DATA,q.get('market',['US'])[0],q.get('id',[''])[0]))
    if path=='/api/visual/job':return self.respond(visual_analysis.job(c,q.get('id',[''])[0]))
    if path=='/api/visual/history':return self.respond(visual_analysis.history(c,q.get('market',['US'])[0],q.get('id',[''])[0]))
    if path=='/api/visual/records':return self.respond(visual_analysis.records(c,q))
    if path=='/api/visual/stats':return self.respond(visual_analysis.stats(c))
    if path=='/api/visual/modes':return self.respond(visual_analysis.MODES)
    if path=='/api/health':return self.respond({'app':'fastmoss-pod-ai','version':'2026.11.20-independent-shop'})
    if path=='/api/innovation/status':return self.respond((innovator or innovation.Service(__import__(__name__))).status())
    if path=='/api/innovation/products':return self.respond(innovation.sources(c,q))
    if path=='/api/innovation/product':return self.respond(innovation.detail(c,q.get('market',['US'])[0],q.get('id',[''])[0]))
    if path=='/api/innovation/drafts':return self.respond(innovation.drafts(c,q))
    if path=='/api/innovation/library':return self.respond(innovation.library(c,q))
    if path=='/api/innovation/job':return self.respond(innovation.get_job(c,q.get('id',[''])[0]))
    if path in ('/api/gallery/download','/api/innovation/download'):
     market=q.get('market',['US'])[0];eid=q.get('id',[''])[0]
     kwargs={}
     if path=='/api/gallery/download':
      images=listing_editor.images(c,market,eid)
      if 'keys' in q:
       try: keys=json.loads(q.get('keys',['[]'])[0])
       except (TypeError,ValueError): raise ValueError('图片选择无效')
       if not isinstance(keys,list) or len(keys)>300 or any(not isinstance(key,str) for key in keys):raise ValueError('图片选择无效')
       selected=set(keys);images=[image for image in images if image.get('key') in selected]
      kwargs={'images':images}
     try: archive=(product_assets.download_archive if path=='/api/gallery/download' else innovation.download_archive)(c,DATA,market,eid,**kwargs)
     except ValueError as e:return self.respond({'error':str(e)},409)
     with archive:
      archive.seek(0,2);size=archive.tell();archive.seek(0)
      filename=re.sub(r'[^A-Za-z0-9_-]','_',market+'_'+eid)+('_materials.zip' if path=='/api/innovation/download' else '_images.zip')
      self.send_response(200);self.send_header('Content-Type','application/zip');self.send_header('Content-Disposition','attachment; filename="'+filename+'"');self.send_header('Content-Length',str(size));self.send_header('Cache-Control','no-store');self.end_headers()
      while True:
       chunk=archive.read(1024*1024)
       if not chunk:break
       self.wfile.write(chunk)
     return
    if path=='/api/gallery':
     items=json.loads(q.get('items',['[]'])[0])
     if not isinstance(items,list) or len(items)>100:raise ValueError('商品数量无效')
     result={}
     result['items']=[{'market':x['market'],'id':x['id'],**product_assets.payload(c,x['market'],x['id']),'images':listing_editor.images(c,x['market'],x['id'])} for x in items]
     return self.respond(result)
    if path=='/api/backups':return self.respond(backup_manager.list_backups(__import__(__name__)))
    if path=='/api/history/summary':return self.respond(history.summary(c))
    if path=='/api/history/products':return self.respond(history.listing(c,q))
    if path=='/api/history/detail':return self.respond(history.detail(c,int(q.get('id',['0'])[0])) or {'error':'未找到'})
    if path=='/api/history/attempts':return self.respond([dict(x) for x in c.execute('SELECT * FROM history_upload_attempts ORDER BY id DESC LIMIT 100')])
    if path=='/api/history/batches':return self.respond([dict(x) for x in c.execute('SELECT * FROM history_import_batches ORDER BY id DESC LIMIT 100')])
    if path=='/api/history/review':return self.respond([dict(x) for x in c.execute("SELECT id,import_batch_id,row_number,reason,state FROM history_identity_review WHERE state='pending' ORDER BY id DESC LIMIT 100")])
    if path=='/api/history/review-detail':return self.respond(history.review_detail(c,int(q.get('id',['0'])[0])) or {'error':'未找到'})
    if path=='/api/history/analysis':return self.respond(history.analysis(c,int(q.get('id',['0'])[0])) or {'error':'未找到'})
    if path=='/api/overview':
     stats={k:c.execute('SELECT count(*) FROM records WHERE kind=?',(k,)).fetchone()[0] for k in ['product','shop']}; stats.update({'ai_images':c.execute('SELECT count(*) FROM innovation_images').fetchone()[0],'matched_products':c.execute("SELECT count(*) FROM records p JOIN records s ON s.kind='shop' AND s.market=p.market AND s.id=p.shop_id WHERE p.kind='product'").fetchone()[0],'matched_shops':c.execute("SELECT count(DISTINCT p.market||':'||p.shop_id) FROM records p JOIN records s ON s.kind='shop' AND s.market=p.market AND s.id=p.shop_id WHERE p.kind='product'").fetchone()[0], 'gmv_total':c.execute("SELECT sum(gmv_total) FROM records WHERE kind='product'").fetchone()[0], 'images':c.execute('SELECT count(DISTINCT market||id) FROM records WHERE kind=\'product\' AND image IS NOT NULL AND image<>\'\'').fetchone()[0], 'batches':[dict(x) for x in c.execute('SELECT * FROM batches ORDER BY id DESC LIMIT 5')]});return self.respond(stats)
    if path in ['/api/products','/api/shops']:return self.respond(result_rows(c,'product' if path.endswith('products') else 'shop',q))
    if path=='/api/detail':return self.respond(detail(c,q.get('kind',['product'])[0],q.get('market',['US'])[0],q.get('id',[''])[0]) or {'error':'未找到'},200)
    if path=='/api/scoring':
     return self.respond({'current':asdict(c.execute('SELECT * FROM ranking_batches ORDER BY id DESC LIMIT 1').fetchone()),'previous':asdict(c.execute('SELECT * FROM ranking_batches ORDER BY id DESC LIMIT 1 OFFSET 1').fetchone())})
    if path=='/api/batches':return self.respond([dict(x) for x in c.execute('SELECT * FROM batches ORDER BY id DESC')])
    if path=='/api/assets':return self.respond([dict(x) for x in c.execute('SELECT i.*,r.name,r.shop_name FROM images i JOIN records r ON r.kind=\'product\' AND r.market=i.market AND r.id=i.product_id ORDER BY i.id DESC LIMIT 100')])
   if path.startswith('/innovation-images/'):
    with db() as c:
     if not c.execute('SELECT 1 FROM innovation_images WHERE local_path=?',(path,)).fetchone():self.send_error(404);return
    f=(DATA/'innovation'/path.removeprefix('/innovation-images/')).resolve();base=(DATA/'innovation').resolve()
   elif path.startswith('/images/'):
    f=(DATA/path.lstrip('/')).resolve(); base=(DATA/'images').resolve()
   else:
    f=(ROOT/'static'/('index.html' if path=='/' else path.lstrip('/'))).resolve(); base=(ROOT/'static').resolve()
   if not f.is_relative_to(base) or not f.is_file():self.send_error(404);return
   b=f.read_bytes();self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(str(f))[0] or 'application/octet-stream');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-cache' if f.suffix in ('.html','.css','.js') else 'private, max-age=3600');self.end_headers();self.wfile.write(b)
  except Exception as e:self.respond({'error':str(e)},500)
 def do_POST(self):
  if not tiktok_shop.local_request_allowed(self,post=True):return self.respond({'error':'请从本机软件地址操作'},403)
  if tiktok_shop.handle(self,__import__(__name__),post=True):return
  try:
   path=urllib.parse.urlsplit(self.path).path
   if path.startswith(('/api/browser/','/api/collection-run')) or path in ('/api/collector-control','/api/link-import','/api/tasks','/api/trend/refresh'):return self.respond({'error':'商品采集功能已删除'},410)
   if self.path.startswith(('/api/ai/','/api/visual/','/api/innovation/')):return self.respond({'error':'功能已停用'},404)
   if self.path=='/api/import':
    host=self.headers.get('Host','');origin=self.headers.get('Origin')
    if host.split(':')[0] not in ('127.0.0.1','localhost') or (origin and origin!='http://'+host):return self.respond({'error':'仅允许本机操作'},403)
    form=cgi.FieldStorage(fp=self.rfile,headers=self.headers,environ={'REQUEST_METHOD':'POST','CONTENT_TYPE':self.headers.get('Content-Type','')}); files=form['files'] if 'files' in form else []; files=files if isinstance(files,list) else [files]; out=[]
    for f in files:
     filename=os.path.basename(f.filename or ''); content=f.file.read(); stamp=now()
     try:
      item=import_file(filename,content);out.append(item);status='duplicate' if item.get('duplicate_of') else 'success'
     except Exception as e:
      item={'filename':filename,'error':str(e)};out.append(item);status='failed'
     if item.get('kind')=='product' and (item.get('imported') or item.get('duplicate_of')):
      source_batch=item.get('batch')
      try:
       with db() as c:
        if item.get('duplicate_of'):
         source=c.execute('SELECT legacy_batch_id FROM history_import_batches WHERE id=?',(item['duplicate_of'],)).fetchone()
         if not source or source[0] is None:raise ValueError('原始文件来源不存在，请重新导入')
         source_batch=source[0]
        previous=c.execute("SELECT id,total FROM tiktok_jobs WHERE source_batch_id=? AND state!='deleted' ORDER BY id DESC LIMIT 1",(source_batch,)).fetchone() if item.get('duplicate_of') else None
       item['collection']=(dict(id=previous['id'],total=previous['total'],reused=True) if previous else
          collection_queue.submit(__import__(__name__),{'source_batch_id':source_batch,'request_key':os.urandom(16).hex()}))
       if collector:collector.wake.set()
      except Exception as e:
       item['collection_error']='文件已导入，但自动采集未能启动：'+str(e)
     with db() as c:c.execute('INSERT INTO history_upload_attempts(filename,file_hash,uploaded_at,status,import_batch_id,details) VALUES(?,?,?,?,?,?)',(filename,hashlib.sha256(content).hexdigest(),stamp,status,item.get('batch') or item.get('duplicate_of'),json.dumps(item,ensure_ascii=False)))
    return self.respond(out)
   length=int(self.headers.get('Content-Length','0'))
   if self.path.startswith('/api/tiktok/'):
    host=self.headers.get('Host','');origin=self.headers.get('Origin')
    if host.split(':')[0] not in ('127.0.0.1','localhost') or (origin and origin!='http://'+host):return self.respond({'error':'仅允许本机操作'},403)
    if length<0 or length>2_100_000 or self.headers.get_content_type()!='application/json':return self.respond({'error':'需要 JSON 请求，链接内容过大'},400)
   if self.path in ('/api/listing-editor','/api/listing-edit','/api/listings/delete','/api/listings/restore'):
    host=self.headers.get('Host','');origin=self.headers.get('Origin')
    if host.split(':')[0] not in ('127.0.0.1','localhost') or (origin and origin!='http://'+host):return self.respond({'error':'仅允许本机操作'},403)
    if length<0 or length>listing_editor.MAX_REQUEST or self.headers.get_content_type()!='application/json':return self.respond({'error':'需要 JSON 请求，内容最多 32 MB；可分次保存图片'},400)
   if self.path.startswith(('/api/innovation/','/api/ai/','/api/visual/')) and (length>100000 or self.headers.get_content_type()!='application/json'):
    return self.respond({'error':'需要 JSON 请求，且内容不得超过 100 KB'},400)
   body=json.loads(self.rfile.read(length) or b'{}')
   if self.path=='/api/tiktok/browser':
    if not collector:return self.respond({'error':'采集服务尚未启动'},503)
    return self.respond(collector.browser_control(body.get('action')))
   if self.path in ('/api/tiktok/jobs','/api/tiktok/control'):
    if not collector:return self.respond({'error':'采集服务尚未启动'},503)
    result=collection_queue.submit(__import__(__name__),body) if self.path.endswith('/jobs') else collection_queue.control(__import__(__name__),body)
    collector.wake.set()
    return self.respond(result,202 if self.path.endswith('/jobs') else 200)
   if self.path in ('/api/listings/delete','/api/listings/restore'):
    with db() as c:result=listing_trash.change(c,body,self.path.endswith('/restore'),now())
    return self.respond(result)
   if self.path=='/api/listing-editor':
    with db() as c:result=listing_editor.save(c,DATA,body)
    return self.respond(result)
   if self.path=='/api/listing-edit':
    items=body.get('items',[])
    if not isinstance(items,list) or not 1<=len(items)<=3000:raise ValueError('商品数量无效')
    size_match_count=0
    with db() as c:
     for item in items:
      market=str(item.get('market','US'));eid=str(item.get('id','')); price=str(item.get('price','')).strip();title=str(item.get('title','')).strip();size_prices=item.get('size_prices') or {}
      if not isinstance(size_prices,dict) or len(size_prices)>30:raise ValueError('尺码价格格式无效')
      normalized_sizes={}
      for size,amount in size_prices.items():
       size_key=re.sub(r'\s+','',str(size)).casefold();amount=str(amount or '').strip()
       if not size_key or (amount and (not re.fullmatch(r'\d+(?:\.\d{1,2})?',amount) or float(amount)<=0)):raise ValueError('尺码价格须为大于零的金额')
       if amount:normalized_sizes[size_key]=format(float(amount),'.2f')
      source=listing_editor.product(c,market,eid)
      if title:
       title=listing_editor.clean_title(title,source.get('shop_name'))
       if not title:raise ValueError('请补充去掉竞品店铺名后的商品标题')
      if price and (not re.fullmatch(r'\d+(?:\.\d{1,2})?',price) or float(price)<=0):raise ValueError('价格须为大于零的金额')
      c.execute("INSERT INTO listing_edits VALUES(?,?,?,?,?) ON CONFLICT(market,id) DO UPDATE SET title=COALESCE(NULLIF(excluded.title,''),listing_edits.title),price=COALESCE(NULLIF(excluded.price,''),listing_edits.price),updated_at=excluded.updated_at",(market,eid,title,price,now()))
      if price:c.execute('DELETE FROM listing_sku_edits WHERE market=? AND product_id=?',(market,eid))
      if normalized_sizes:
       for variant in listing_editor.variants(c,market,eid,price,include_hidden=True):
        for attribute in variant.get('attributes',[]):
         name=str(attribute.get('name','')).casefold();value=re.sub(r'\s+','',str(attribute.get('value',''))).casefold()
         if re.search(r'^(?:size|尺码|尺寸)$',name) and value in normalized_sizes:
          c.execute('''INSERT INTO listing_sku_edits VALUES(?,?,?,?,?) ON CONFLICT(market,product_id,sku_id)
              DO UPDATE SET price=excluded.price,updated_at=excluded.updated_at''',(market,eid,str(variant['id']),normalized_sizes[value],now()))
          size_match_count+=1;break
    return self.respond({'ok':True,'changed':len(items),'sku_count':size_match_count})
   if self.path=='/api/ai/models/save':
    with db() as c:result=model_manager.save(c,ROOT,DATA,body)
    return self.respond(result)
   if self.path=='/api/ai/budget':
    with db() as c:result=model_manager.budget(c,body)
    return self.respond(result)
   if self.path in ('/api/visual/analyze','/api/ai/test'):
    if not visualizer:return self.respond({'error':'视觉分析服务尚未启动'},503)
    if self.path=='/api/ai/test':
     with db() as c:
      model=model_manager.resolve(c,ROOT,DATA,body.get('model_config_id'),body.get('category','vision'))
      if model['category']=='image':return self.respond(model_manager.test_image(c,ROOT,DATA,model['id']))
      if not body.get('id'):
       reference=c.execute("SELECT market,product_id,id FROM images WHERE local_path IS NOT NULL AND local_path<>'' ORDER BY id LIMIT 1").fetchone()
       if not reference:raise ValueError('需要至少一张已缓存的真实商品图片才能验证视觉连接')
       body.update(market=reference['market'],id=reference['product_id'],image_ids=[reference['id']])
     body['kind']='connection';body['force']=True
    try:result=visualizer.submit(body)
    except Exception as exc:
     if self.path=='/api/ai/test':
      with db() as c:c.execute('UPDATE ai_models SET last_test_at=?,last_test_ok=0,last_test_error=?,vision_verified=0 WHERE id=?',(now(),str(exc)[:700],body.get('model_config_id','tt-terra')))
     raise
    return self.respond(result,202)
   if self.path=='/api/visual/retry':
    if not visualizer:return self.respond({'error':'视觉分析服务尚未启动'},503)
    return self.respond(visualizer.retry(body),202)
   if self.path=='/api/visual/prompt':
    with db() as c:result=visual_analysis.save_prompt(c,body)
    return self.respond(result)
   if self.path=='/api/visual/generate':return self.respond(visual_analysis.generate(__import__(__name__),body),202)
   if self.path=='/api/innovation/brief':
    with db() as c:result=innovation.save_brief(c,DATA,body)
    return self.respond(result)
   if self.path=='/api/innovation/generate':
    if not innovator:return self.respond({'error':'AI 后台服务尚未启动'},503)
    return self.respond(innovator.submit(body),202)
   if self.path=='/api/innovation/pause':
    if not innovator:return self.respond({'error':'AI 后台服务尚未启动'},503)
    return self.respond(innovator.pause_product(body))
   if self.path=='/api/innovation/retry':
    if not innovator:return self.respond({'error':'AI 后台服务尚未启动'},503)
    return self.respond(innovator.retry(body),202)
   if self.path=='/api/innovation/save-images':
    with db() as c:result=innovation.save_images(c,body)
    return self.respond(result)
   if self.path=='/api/innovation/save-image':
    with db() as c:result=innovation.save_image(c,int(body.get('image_id',0)))
    return self.respond(result)
   if self.path=='/api/backups/restore':
    with db() as c:
     if c.execute("SELECT 1 FROM shop_listing_jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():raise ValueError('请等待上架任务结束后再恢复数据库')
    with db() as c:
     if c.execute("SELECT 1 FROM tiktok_jobs WHERE state IN ('running','paused') LIMIT 1").fetchone() or (collector and (collector.process or collector.browser_state!='closed')):raise ValueError('请先停止采集任务、关闭验证浏览器，并等待当前商品处理结束后恢复数据库')
    if listing_preparer:listing_preparer.invalidate('数据库已恢复，旧准备任务已停止，请重新准备')
    if innovator:innovator.invalidate()
    if visualizer:visualizer.invalidate()
    try:result=backup_manager.restore(__import__(__name__),body.get('name'))
    finally:

     if innovator:innovator.recover()
     if visualizer:visualizer.recover()
     if collector:collector.recover(pause=True)
    return self.respond(result)
   if self.path=='/api/batches/delete':
    return self.respond(batch_delete.delete_batch(__import__(__name__),int(body.get('id',0))))
   if self.path=='/api/history/resolve':
    with db() as c:result=history.resolve_review(c,int(body.get('id',0)),body.get('decision'),body.get('product_id'))
    return self.respond(result)
   if self.path in ('/api/drafts','/api/review-exclude'):
    items=body.get('items',[])
    if not isinstance(items,list) or len(items)>1000:raise ValueError('商品数量无效')
    operation=body.get('action','add'); changed=0
    with db() as c:
     latest=c.execute('SELECT id FROM ranking_batches ORDER BY id DESC LIMIT 1').fetchone()
     for item in items:
      market=item.get('market','US');eid=item['id']
      if self.path=='/api/review-exclude':
       if not latest:raise ValueError('暂无排名批次')
       changed+=c.execute('INSERT OR IGNORE INTO review_exclusions VALUES(?,?,?,?)',(latest['id'],market,eid,now())).rowcount
      elif operation=='add':changed+=int(add_draft(c,market,eid,'人工加入','人工筛选加入选品池',latest['id'] if latest else None))
      elif operation=='remove':
       changed+=c.execute("UPDATE draft_entries SET active=0,removed_at=?,reason='用户移出选品池' WHERE market=? AND id=? AND active=1",(now(),market,eid)).rowcount
       c.execute("INSERT INTO draft_events(market,product_id,event,source,created_at,reason) VALUES(?,?,'移出','人工',?,'用户移出选品池')",(market,eid,now()))
      else:raise ValueError('操作无效')
    return self.respond({'ok':True,'changed':changed})
   self.respond({'error':'未知操作'},404)
  except Exception as e:self.respond({'error':str(e)},400)
# Serialize requests so restore cannot race with imports or other writes.
_request_lock=threading.RLock()
innovator=None
visualizer=None
collector=None
listing_preparer=None
def _serialized(method):
 def wrapped(self):
  with _request_lock:return method(self)
 return wrapped
Handler.do_GET=_serialized(Handler.do_GET)
Handler.do_POST=_serialized(Handler.do_POST)

def run(open_browser=False):
 global collector, httpd, innovator, visualizer
 def terminate(*_):raise KeyboardInterrupt
 signal.signal(signal.SIGTERM,terminate)
 init()
 with db() as c:shop_listing.recover(c)
 # Restore previously imported links which were excluded by the old ranking rule.
 with db() as c:
  migrated=c.execute("SELECT 1 FROM gallery_settings WHERE key='competitor_library_v1'").fetchone()
  if not migrated:
   links=c.execute("""SELECT DISTINCT r.market,r.id FROM records r JOIN snapshots s ON s.id=r.snapshot_id JOIN batches b ON b.id=s.batch_id WHERE r.kind='product' AND b.filename='批量商品链接'""").fetchall()
   for item in links: add_draft(c,item['market'],item['id'],'链接导入','导入竞品库')
   c.execute("INSERT INTO gallery_settings VALUES('competitor_library_v1','1')")
 from desktop_runtime import bind_server, start_callback
 httpd=bind_server(DATA,Handler)
 port=httpd.server_port
 callback_server=start_callback(DATA,port)
 if open_browser:
  import webbrowser
  webbrowser.open('http://127.0.0.1:%d/'%port)
 innovator=None;visualizer=None
 library_categories.start(__import__(__name__))
 collector=collection_queue.Service(__import__(__name__));collector.start()
 print('FastMoss POD AI: http://127.0.0.1:%d'%port,flush=True)
 try:httpd.serve_forever()
 except KeyboardInterrupt:pass
 finally:
  if listing_preparer:listing_preparer.stop()
  collector.stop()
  callback_server.shutdown(); callback_server.server_close()
  httpd.server_close()

if __name__=='__main__': run()
