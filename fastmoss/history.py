"""Append-only product history, isolated from the existing research tables."""
import datetime, hashlib, json, re, urllib.parse
from pathlib import Path

MIGRATION = Path(__file__).parent/'migrations'/'001_history.sql'

def migrate(c):
 c.executescript(MIGRATION.read_text(encoding='utf-8'))

def now(): return datetime.datetime.now().astimezone().isoformat(timespec='seconds')

def source_ids(raw):
 def url(raw_url, hosts, pattern):
  try:
   p=urllib.parse.urlsplit(str(raw_url or '').strip()); host=(p.hostname or '').lower()
   if p.scheme not in ('http','https') or not any(host==h or host.endswith('.'+h) for h in hosts): return None, None
   match=re.search(pattern,p.path,re.I)
   clean=urllib.parse.urlunsplit(('https',host,p.path.rstrip('/'),'',''))
   return (match.group(1) if match else None),clean
  except ValueError:return None,None
 t,tu=url(raw.get('TikTok'),('tiktok.com',),r'/product/(\d{8,})(?:/|$)')
 f,fu=url(raw.get('FastMoss'),('fastmoss.com',),r'/e-commerce/detail/(\d{8,})(?:/|$)')
 ids=[]
 if t:ids.append(('tiktok',t))
 if f:ids.append(('fastmoss',f))
 if tu:ids.append(('url',tu))
 if fu:ids.append(('url',fu))
 return t,f,tu,fu,ids

def number(v):
 s=str('' if v is None else v).strip().replace(',','').replace('$',''); m=re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*([kKmMbB%]?)',s)
 return float(m[1])*{'k':1e3,'m':1e6,'b':1e9,'%':.01,'':1}[m[2].lower()] if m else None

def ingest_row(c,batch,raw,row_number,legacy_snapshot_id=None,manual_pid=None,manual_new=False):
 stamp=batch['uploaded_at']; country=str(raw.get('Country/Region') or '').strip().upper(); t,f,tu,fu,ids=source_ids(raw)
 def review(reason):
  c.execute('INSERT INTO history_identity_review(import_batch_id,legacy_snapshot_id,row_number,reason,raw_data_json,created_at) VALUES(?,?,?,?,?,?)',(batch['id'],legacy_snapshot_id,row_number,reason,json.dumps(raw,ensure_ascii=False),stamp));return 'failed'
 if not country:return review('缺少市场或可验证的商品身份')
 if not ids and manual_pid is None and not manual_new:return review('缺少市场或可验证的商品身份')
 matches=set()
 for kind,val in ids:
  hit=c.execute('SELECT product_id FROM history_identities WHERE country=? AND kind=? AND value=?',(country,kind,val)).fetchone()
  if hit:matches.add(hit[0])
 if len(matches)>1 and manual_pid is None:return review('来源标识指向不同商品，需人工确认')
 if manual_new and matches:raise ValueError('来源标识已被其他主档使用，请选择已有商品或先核实冲突')
 pid=manual_pid if manual_pid is not None else next(iter(matches),None); is_new=pid is None
 if pid is not None:
  existing=c.execute('SELECT platform_product_id,fastmoss_product_id FROM history_products WHERE id=?',(pid,)).fetchone()
  if not existing:raise ValueError('目标商品不存在')
  if manual_pid is None and ((t and existing[0] and t!=existing[0]) or (f and existing[1] and f!=existing[1])):return review('来源商品 ID 与现有主档冲突')
  if c.execute('SELECT 1 FROM history_snapshots WHERE product_id=? AND import_batch_id=?',(pid,batch['id'])).fetchone():return 'duplicate'
 else:
  cur=c.execute('INSERT INTO history_products(country,platform_product_id,fastmoss_product_id,product_name,shop_name,category,product_url,fastmoss_url,image_url,first_seen_at,last_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(country,t,f,raw.get('Product Name'),raw.get('Store Name'),raw.get('Product Category'),tu,fu,raw.get('Product Image'),stamp,stamp,stamp,stamp));pid=cur.lastrowid
 if manual_pid is not None and matches and matches!={pid}:t=f=None
 for kind,val in ids:
  if manual_pid is not None and matches and matches!={pid}:continue
  c.execute('INSERT OR IGNORE INTO history_identities(product_id,country,kind,value,basis,confidence) VALUES(?,?,?,?,?,?)',(pid,country,kind,val,'人工确认' if manual_pid is not None or manual_new else '同一来源行提供的链接',1.0 if kind!='url' else .8))
 # Replaying older imports must never regress the newest display values.
 c.execute('''UPDATE history_products SET platform_product_id=COALESCE(platform_product_id,?),fastmoss_product_id=COALESCE(fastmoss_product_id,?),product_name=?,shop_name=?,category=?,product_url=COALESCE(?,product_url),fastmoss_url=COALESCE(?,fastmoss_url),image_url=?,last_seen_at=?,updated_at=? WHERE id=? AND last_seen_at<=?''',(t,f,raw.get('Product Name'),raw.get('Store Name'),raw.get('Product Category'),tu,fu,raw.get('Product Image'),stamp,stamp,pid,stamp))
 fields=['7-Day Sales Volume','7-Day GMV','28-Day Sales Volume','28-Day GMV','Total Sales Volume','Total GMV']
 values=[number(raw.get(x)) for x in fields]
 extras=[number(raw.get(x)) for x in ['Commission Rate','Total Number of Creators','Creator Sales rate','Total Number of Promotional Videos','Total Number of Promotional Livestreams']]
 cur=c.execute('''INSERT INTO history_snapshots(product_id,import_batch_id,legacy_snapshot_id,snapshot_at,snapshot_date,observed_at,time_basis,sales_7d,gmv_7d,sales_28d,gmv_28d,total_sales,total_gmv,price,commission_rate,creator_count,creator_sales_rate,promotional_video_count,promotional_livestream_count,product_status,raw_data_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(pid,batch['id'],legacy_snapshot_id,batch.get('snapshot_at'),(batch.get('snapshot_at') or '')[:10] or None,stamp,batch['time_basis'],*values,raw.get('Selling Price / Price') or None,*extras,raw.get('Product Status') or None,json.dumps(raw,ensure_ascii=False),stamp))
 sid=cur.lastrowid; prev=c.execute('SELECT * FROM history_snapshots WHERE product_id=? AND id<>? AND observed_at<=? ORDER BY observed_at DESC,id DESC LIMIT 1',(pid,sid,stamp)).fetchone(); current=c.execute('SELECT * FROM history_snapshots WHERE id=?',(sid,)).fetchone(); pairs=[]
 for col in ('sales_7d','gmv_7d','sales_28d','gmv_28d'):
  a,b=current[col],prev[col] if prev else None; delta=a-b if a is not None and b is not None else None;pairs.extend([delta,delta/b if delta is not None and b else None])
 a,b=number(current['price']),number(prev['price']) if prev else None;pd=a-b if a is not None and b is not None else None
 interval=None
 if prev:
  try:interval=(datetime.datetime.fromisoformat(stamp)-datetime.datetime.fromisoformat(prev['observed_at'])).total_seconds()/3600
  except (ValueError,TypeError):pass
 trend='首次观察' if not prev else '数据不足' if pairs[0] is None else '上升' if pairs[0]>0 else '下降' if pairs[0]<0 else '持平'
 c.execute('''INSERT INTO history_metrics(product_id,current_snapshot_id,previous_snapshot_id,comparison_interval_hours,sales_7d_delta,sales_7d_growth_rate,gmv_7d_delta,gmv_7d_growth_rate,sales_28d_delta,sales_28d_growth_rate,gmv_28d_delta,gmv_28d_growth_rate,price_delta,growth_trend,calculated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(pid,sid,prev['id'] if prev else None,interval,*pairs,pd,trend,now()))
 return 'new' if is_new else 'existing'

def begin_batch(c,legacy_id,name,content,total,uploaded_at):
 digest=hashlib.sha256(content).hexdigest(); prior=c.execute("SELECT id FROM history_import_batches WHERE file_hash=? AND status='success'",(digest,)).fetchone()
 if prior:return None,prior[0]
 cur=c.execute("INSERT INTO history_import_batches(legacy_batch_id,filename,file_hash,uploaded_at,time_basis,total_rows,status) VALUES(?,?,?,?,?,?,?)",(legacy_id,name,digest,uploaded_at,'上传时间（采集时间未知）',total,'processing'))
 return {'id':cur.lastrowid,'uploaded_at':uploaded_at,'snapshot_at':None,'time_basis':'上传时间（采集时间未知）'},None

def finish_batch(c,batch,counts):
 c.execute("UPDATE history_import_batches SET successful_rows=?,new_products=?,existing_products=?,duplicate_rows=?,failed_rows=?,status='success' WHERE id=?",(counts['new']+counts['existing'],counts['new'],counts['existing'],counts['duplicate'],counts['failed'],batch['id']))

def backfill(c):
 for old in c.execute("SELECT * FROM batches WHERE kind='product' AND id NOT IN (SELECT legacy_batch_id FROM history_import_batches WHERE legacy_batch_id IS NOT NULL) ORDER BY imported_at,id").fetchall():
  uploaded=old['imported_at'];cur=c.execute("INSERT INTO history_import_batches(legacy_batch_id,filename,uploaded_at,time_basis,total_rows,status) VALUES(?,?,?,?,?,?)",(old['id'],old['filename'],uploaded,'历史上传时间（采集时间和文件哈希未知）',old['total'],'processing'))
  batch={'id':cur.lastrowid,'uploaded_at':uploaded,'snapshot_at':None,'time_basis':'历史上传时间（采集时间和文件哈希未知）'};counts=dict.fromkeys(('new','existing','duplicate','failed'),0)
  for row in c.execute('SELECT id,row_number,raw FROM snapshots WHERE batch_id=? ORDER BY id',(old['id'],)).fetchall():counts[ingest_row(c,batch,json.loads(row['raw']),row['row_number'],row['id'])]+=1
  finish_batch(c,batch,counts)

def summary(c):
 scalar=lambda sql:c.execute(sql).fetchone()[0]
 return {'products':scalar('SELECT count(*) FROM history_products'),'snapshots':scalar('SELECT count(*) FROM history_snapshots'),'new_today':scalar("SELECT count(*) FROM history_products WHERE substr(first_seen_at,1,10)=date('now','localtime')"),'uploads_today':scalar("SELECT count(*) FROM history_snapshots WHERE substr(observed_at,1,10)=date('now','localtime')"),'growing':scalar("WITH daily AS (SELECT product_id,substr(observed_at,1,10) day,sales_7d,ROW_NUMBER() OVER(PARTITION BY product_id,substr(observed_at,1,10) ORDER BY observed_at DESC,id DESC) rn FROM history_snapshots WHERE sales_7d IS NOT NULL), ranked AS (SELECT product_id,sales_7d,ROW_NUMBER() OVER(PARTITION BY product_id ORDER BY day DESC) rn FROM daily WHERE rn=1), grouped AS (SELECT product_id,MAX(CASE WHEN rn=1 THEN sales_7d END) a,MAX(CASE WHEN rn=2 THEN sales_7d END) b,MAX(CASE WHEN rn=3 THEN sales_7d END) c FROM ranked WHERE rn<=3 GROUP BY product_id) SELECT count(*) FROM grouped WHERE a>b AND b>c"),'review':scalar("SELECT count(*) FROM history_identity_review WHERE state='pending'")}

def listing(c,q):
 clauses=[];args=[]
 for key,col in [('q',"(p.product_name LIKE ? OR p.platform_product_id LIKE ? OR p.fastmoss_product_id LIKE ?)"),('shop','p.shop_name LIKE ?'),('category','p.category LIKE ?'),('country','p.country = ?'),('date','EXISTS (SELECT 1 FROM history_snapshots ds WHERE ds.product_id=p.id AND substr(ds.observed_at,1,10)=?)')]:
  value=q.get(key,[''])[0].strip()
  if value:
   clauses.append(col);v=('%'+value+'%') if key in ('q','shop','category') else value;args.extend([v]*3 if key=='q' else [v])
 tags=q.get('tag',[])
 choices={'new':"substr(p.first_seen_at,1,10)=date('now','localtime')",'sales7':"m.sales_7d_growth_rate>=0.2",'sales28':"m.sales_28d_growth_rate>=0.2",'gmv':"m.gmv_7d_growth_rate>=0.2",'fall':"m.sales_7d_delta<0",'potential':"m.sales_7d_growth_rate>=0.2 AND s.sales_7d<100",'small':"m.previous_snapshot_id IS NULL AND s.sales_7d<100"}
 for tag in tags:
  if tag in choices:clauses.append(choices[tag])
 base=" FROM history_products p JOIN history_snapshots s ON s.id=(SELECT id FROM history_snapshots WHERE product_id=p.id ORDER BY observed_at DESC,id DESC LIMIT 1) LEFT JOIN history_metrics m ON m.id=(SELECT max(id) FROM history_metrics WHERE current_snapshot_id=s.id) "+(' WHERE '+' AND '.join(clauses) if clauses else '')
 total=c.execute('SELECT count(*)'+base,args).fetchone()[0];order={'sales7':'m.sales_7d_growth_rate','sales28':'m.sales_28d_growth_rate','gmv':'m.gmv_7d_growth_rate'}.get(q.get('sort',[''])[0],'s.observed_at');page=max(1,int(q.get('page',['1'])[0]));items=c.execute('SELECT p.*,s.id snapshot_id,s.sales_7d,s.gmv_7d,s.sales_28d,s.gmv_28d,s.observed_at,s.price,m.sales_7d_delta,m.gmv_7d_delta,m.sales_28d_delta,m.gmv_28d_delta,m.sales_7d_growth_rate,m.sales_28d_growth_rate,m.gmv_7d_growth_rate,m.growth_trend'+base+' ORDER BY '+order+' DESC,p.id LIMIT 30 OFFSET ?',args+[(page-1)*30]).fetchall()
 return {'total':total,'page':page,'items':[dict(x) for x in items]}

def detail(c,pid):
 p=c.execute('SELECT * FROM history_products WHERE id=?',(pid,)).fetchone()
 if not p:return None
 rows=c.execute('''SELECT s.*,b.filename,b.uploaded_at,m.previous_snapshot_id,m.comparison_interval_hours,m.sales_7d_delta,m.sales_7d_growth_rate,m.gmv_7d_delta,m.gmv_7d_growth_rate,m.sales_28d_delta,m.sales_28d_growth_rate,m.gmv_28d_delta,m.gmv_28d_growth_rate,m.price_delta,m.growth_trend FROM history_snapshots s JOIN history_import_batches b ON b.id=s.import_batch_id LEFT JOIN history_metrics m ON m.id=(SELECT max(id) FROM history_metrics WHERE current_snapshot_id=s.id) WHERE s.product_id=? ORDER BY s.observed_at,s.id''',(pid,)).fetchall()
 return {'product':dict(p),'snapshots':[dict(x) for x in rows]}

def review_detail(c,review_id):
 row=c.execute('SELECT r.*,b.filename FROM history_identity_review r JOIN history_import_batches b ON b.id=r.import_batch_id WHERE r.id=?',(review_id,)).fetchone()
 if not row:return None
 data=dict(row);raw=json.loads(data['raw_data_json']);country=str(raw.get('Country/Region') or '').strip().upper();name=str(raw.get('Product Name') or '').strip();shop=str(raw.get('Store Name') or '').strip()
 candidates=c.execute('''SELECT id,product_name,shop_name,country,platform_product_id,fastmoss_product_id FROM history_products WHERE country=? AND (shop_name=? OR product_name=?) ORDER BY CASE WHEN product_name=? THEN 0 ELSE 1 END,id LIMIT 30''',(country,shop,name,name)).fetchall()
 data['raw']=raw;data['candidates']=[dict(x) for x in candidates];return data

def resolve_review(c,review_id,decision,product_id=None):
 row=c.execute('SELECT r.*,b.uploaded_at,b.snapshot_at,b.time_basis FROM history_identity_review r JOIN history_import_batches b ON b.id=r.import_batch_id WHERE r.id=?',(review_id,)).fetchone()
 if not row or row['state']!='pending':raise ValueError('待确认记录不存在或已处理')
 if decision not in ('existing','new'):raise ValueError('确认类型无效')
 raw=json.loads(row['raw_data_json']);market=str(raw.get('Country/Region') or '').strip().upper()
 if decision=='existing':
  product=c.execute('SELECT country FROM history_products WHERE id=?',(product_id,)).fetchone()
  if not product or product[0]!=market:raise ValueError('目标商品不存在或市场不一致')
 batch={'id':row['import_batch_id'],'uploaded_at':row['uploaded_at'],'snapshot_at':row['snapshot_at'],'time_basis':row['time_basis']}
 outcome=ingest_row(c,batch,raw,row['row_number'],row['legacy_snapshot_id'],manual_pid=product_id if decision=='existing' else None,manual_new=decision=='new')
 if outcome not in ('new','existing'):raise ValueError('该批次商品已有快照或身份信息不足，无法确认')
 product=c.execute('SELECT product_id FROM history_snapshots WHERE import_batch_id=? AND (legacy_snapshot_id=? OR raw_data_json=?) ORDER BY id DESC LIMIT 1',(row['import_batch_id'],row['legacy_snapshot_id'],row['raw_data_json'])).fetchone()
 if not product:raise ValueError('确认失败，未找到已保存的快照')
 pid=product[0];c.execute("UPDATE history_identity_review SET state='resolved' WHERE id=?",(review_id,));c.execute('INSERT INTO history_review_resolutions(review_id,product_id,decision,details,resolved_at) VALUES(?,?,?,?,?)',(review_id,pid,decision,'人工确认；冲突的已有身份映射不会改写',now()))
 c.execute('UPDATE history_import_batches SET failed_rows=MAX(0,failed_rows-1),successful_rows=successful_rows+1,new_products=new_products+?,existing_products=existing_products+? WHERE id=?',(int(decision=='new'),int(decision=='existing'),row['import_batch_id']))
 return {'ok':True,'product_id':pid}

def analysis(c,pid):
 d=detail(c,pid)
 if not d:return None
 rows=d['snapshots'];latest=rows[-1];byday={}
 for x in rows:
  day=x['observed_at'][:10]
  if x['sales_7d'] is not None:byday[day]=x
 days=sorted(byday);last3=[byday[k]['sales_7d'] for k in days[-3:]]
 continuous=len(last3)==3 and last3[0]<last3[1]<last3[2]
 sales=latest['sales_7d'];growth=latest['sales_7d_growth_rate'];delta=latest['sales_7d_delta']; comparable=len(days)>=2; tags=[]
 if sales is not None and sales>=100:tags.append('当前热销（规则：近7日销量≥100）')
 if comparable and growth is not None and growth>=.2:tags.append('近期快速增长（规则：变化率≥20%）')
 if continuous:tags.append('连续三次不同上传日上升')
 if latest['previous_snapshot_id'] is None and sales is not None and sales<100:tags.append('新发现、销量尚小（规则：首次观察且销量<100）')
 if comparable and delta is not None and delta<0:tags.append('最近一次滚动销量下降')
 if comparable and sales is not None and sales>=100 and growth is not None and 0<=growth<.1:tags.append('销量较高、近期变化放缓（规则：变化率0–10%）')
 return {'method':'透明规则分析，未调用 AI 模型', 'tags':tags or ['数据不足或暂未满足规则'], 'observations':len(rows),'distinct_upload_days':len(days),'sales_7d':sales,'gmv_7d':latest['gmv_7d'],'sales_7d_delta':delta,'sales_7d_growth_rate':growth,'gmv_7d_growth_rate':latest['gmv_7d_growth_rate'],'pod':'仅凭现有数据无法可靠判断 POD 适配程度，需人工审核商品材质与设计。','design':'尚未进行图片设计识别，无法判断值得借鉴的设计元素。','risk':'滚动窗口变化不是每日销量；来源采集时间未知，历史同名文件可能重复。还需人工检查版权、商标和平台规则。','recommendation':'待人工评估，不自动加入待开发商品库。'}

def suppress_legacy_duplicate_comparisons(c):
 # Old uploads did not retain hashes; identical rows from the same-named source
 # are kept, but are not treated as independent observations of change.
 rows=c.execute('''SELECT m.* FROM history_metrics m JOIN history_snapshots s ON s.id=m.current_snapshot_id JOIN history_snapshots p ON p.id=m.previous_snapshot_id JOIN history_import_batches b ON b.id=s.import_batch_id JOIN history_import_batches pb ON pb.id=p.import_batch_id WHERE m.id=(SELECT max(id) FROM history_metrics WHERE current_snapshot_id=s.id) AND m.growth_trend<>'来源可能重复' AND b.file_hash IS NULL AND pb.file_hash IS NULL AND b.filename=pb.filename AND s.raw_data_json=p.raw_data_json''').fetchall()
 c.executemany('''INSERT INTO history_metrics(product_id,current_snapshot_id,previous_snapshot_id,comparison_interval_hours,growth_trend,calculated_at) VALUES(?,?,?,?,'来源可能重复',?)''',[(r['product_id'],r['current_snapshot_id'],r['previous_snapshot_id'],r['comparison_interval_hours'],now()) for r in rows])
 return len(rows)

def find_existing_file(c,name,content,headers,rows):
 digest=hashlib.sha256(content).hexdigest()
 hit=c.execute('SELECT import_batch_id FROM history_file_fingerprints WHERE file_hash=?',(digest,)).fetchone()
 if hit:return hit[0]
 hit=c.execute("SELECT id FROM history_import_batches WHERE file_hash=? AND status='success'",(digest,)).fetchone()
 if hit:return hit[0]
 # Legacy hashes are unknown. Recognize an original upload only when its filename,
 # row count, field names and every stored raw row agree; preserve the old batch.
 batches=c.execute("SELECT id,legacy_batch_id FROM history_import_batches WHERE filename=? AND total_rows=? AND file_hash IS NULL AND legacy_batch_id IS NOT NULL AND status='success' ORDER BY id",(name,len(rows)-1)).fetchall()
 for batch in batches:
  saved=c.execute('SELECT raw FROM snapshots WHERE batch_id=? ORDER BY row_number,id',(batch['legacy_batch_id'],)).fetchall()
  if len(saved)!=len(rows)-1:continue
  if all(json.loads(old[0])=={k:(values[j] if j<len(values) else '') for j,k in enumerate(headers) if k} for old,values in zip(saved,rows[1:])):
   c.execute('INSERT OR IGNORE INTO history_file_fingerprints VALUES(?,?,?,?)',(digest,batch['id'],'文件名及全部原始行一致',now()))
   return batch['id']
 return None
