"""Remove one import atomically while keeping data from other imports."""
import datetime, json, sqlite3
from contextlib import nullcontext
from database import ClosingConnection
import history
import listing_trash

def delete_batch(server, batch_id, connection=None):
    with (nullcontext(connection) if connection is not None else server.db()) as c:
        batch=c.execute('SELECT * FROM batches WHERE id=?',(batch_id,)).fetchone()
        if not batch: raise ValueError('导入批次不存在或已删除')
        backup_path=None
        if connection is None:
            backup_dir=server.DB.parent/'backups'; backup_dir.mkdir(exist_ok=True)
            backup_path=backup_dir/('before-delete-batch-%s-%s.sqlite3'%(batch_id,datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')))
            with sqlite3.connect(backup_path,factory=ClosingConnection) as backup: c.backup(backup)
            c.execute('BEGIN IMMEDIATE')
        # File-triggered collections belong to this source and are removed in
        # the same transaction, including workers that have not saved a row yet.
        children=c.execute('SELECT DISTINCT j.batch_id FROM tiktok_jobs j JOIN batches b ON b.id=j.batch_id WHERE j.source_batch_id=?',(batch_id,)).fetchall()
        for child in children:delete_batch(server,child[0],connection=c)
        stamp=server.now()
        c.execute("""UPDATE tiktok_items SET state='cancelled',token=NULL,verification_hold=0,
            error_code='product_deleted',stage='来源已删除',updated_at=?
            WHERE job_id IN (SELECT id FROM tiktok_jobs WHERE batch_id=?)
              AND state IN ('pending','running','retry','partial','failed')""",(stamp,batch_id))
        c.execute("UPDATE tiktok_jobs SET state='deleted',source_batch_id=NULL,updated_at=? WHERE batch_id=?",(stamp,batch_id))
        affected=c.execute('SELECT DISTINCT kind,market,entity_id FROM snapshots WHERE batch_id=? AND entity_id IS NOT NULL',(batch_id,)).fetchall()
        hb=c.execute('SELECT id FROM history_import_batches WHERE legacy_batch_id=?',(batch_id,)).fetchone()
        if hb:
            hid=hb['id']
            pids=[r[0] for r in c.execute('SELECT DISTINCT product_id FROM history_snapshots WHERE import_batch_id=?',(hid,))]
            c.execute('DELETE FROM history_metrics WHERE current_snapshot_id IN (SELECT id FROM history_snapshots WHERE import_batch_id=?) OR previous_snapshot_id IN (SELECT id FROM history_snapshots WHERE import_batch_id=?)',(hid,hid))
            c.execute('DELETE FROM history_review_resolutions WHERE review_id IN (SELECT id FROM history_identity_review WHERE import_batch_id=?)',(hid,))
            c.execute('DELETE FROM history_identity_review WHERE import_batch_id=?',(hid,))
            c.execute('DELETE FROM history_snapshots WHERE import_batch_id=?',(hid,))
            c.execute('DELETE FROM history_file_fingerprints WHERE import_batch_id=?',(hid,))
            c.execute('UPDATE history_upload_attempts SET import_batch_id=NULL WHERE import_batch_id=?',(hid,))
            c.execute('DELETE FROM history_import_batches WHERE id=?',(hid,))
            for pid in pids:
                rows=c.execute('SELECT * FROM history_snapshots WHERE product_id=? ORDER BY observed_at,id',(pid,)).fetchall()
                if not rows:
                    # Preserve any independently recorded manual review resolutions.
                    if not c.execute('SELECT 1 FROM history_review_resolutions WHERE product_id=?',(pid,)).fetchone():
                        c.execute('DELETE FROM history_identities WHERE product_id=?',(pid,)); c.execute('DELETE FROM history_products WHERE id=?',(pid,))
                    continue
                latest=rows[-1]; raw=json.loads(latest['raw_data_json']); t,f,tu,fu,_=history.source_ids(raw)
                c.execute('UPDATE history_products SET product_name=?,shop_name=?,category=?,product_url=?,fastmoss_url=?,image_url=?,first_seen_at=?,last_seen_at=?,updated_at=? WHERE id=?',(raw.get('Product Name'),raw.get('Store Name'),raw.get('Product Category'),tu,fu,raw.get('Product Image'),rows[0]['observed_at'],latest['observed_at'],server.now(),pid))
                # Existing later comparisons may have used the deleted snapshot.
                c.execute('DELETE FROM history_metrics WHERE product_id=?',(pid,))
                prev=None
                for row in rows:
                    pairs=[]
                    for key in ('sales_7d','gmv_7d','sales_28d','gmv_28d'):
                        a=row[key]; b=prev[key] if prev else None; delta=a-b if a is not None and b is not None else None
                        pairs.extend((delta,delta/b if delta is not None and b else None))
                    a=history.number(row['price']); b=history.number(prev['price']) if prev else None; pd=a-b if a is not None and b is not None else None
                    interval=None
                    if prev:
                        try: interval=(datetime.datetime.fromisoformat(row['observed_at'])-datetime.datetime.fromisoformat(prev['observed_at'])).total_seconds()/3600
                        except (ValueError,TypeError): pass
                    trend='首次观察' if not prev else '数据不足' if pairs[0] is None else '上升' if pairs[0]>0 else '下降' if pairs[0]<0 else '持平'
                    c.execute('INSERT INTO history_metrics(product_id,current_snapshot_id,previous_snapshot_id,comparison_interval_hours,sales_7d_delta,sales_7d_growth_rate,gmv_7d_delta,gmv_7d_growth_rate,sales_28d_delta,sales_28d_growth_rate,gmv_28d_delta,gmv_28d_growth_rate,price_delta,growth_trend,calculated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(pid,row['id'],prev['id'] if prev else None,interval,*pairs,pd,trend,server.now()))
                    prev=row
            history.suppress_legacy_duplicate_comparisons(c)
        c.execute('DELETE FROM snapshots WHERE batch_id=?',(batch_id,))
        for item in affected:
            kind,market,eid=item
            row=c.execute('SELECT * FROM snapshots WHERE kind=? AND market=? AND entity_id=? ORDER BY id DESC LIMIT 1',(kind,market,eid)).fetchone()
            if not row:
                if kind=='product':
                    listing_trash.cancel_product_work(c,market,eid,stamp)
                    listing_trash.reset_collected_data(c,market,eid)
                    c.execute('DELETE FROM draft_entries WHERE market=? AND id=?',(market,eid))
                    c.execute('DELETE FROM listing_trash WHERE market=? AND id=?',(market,eid))
                    c.execute("INSERT INTO draft_events(market,product_id,event,source,created_at,reason) VALUES(?,?,'删除','数据上传',?,'导入来源已删除')",(market,eid,stamp))
                c.execute('DELETE FROM records WHERE kind=? AND market=? AND id=?',(kind,market,eid)); continue
            raw=json.loads(row['raw'])
            if kind=='product':
                values=(raw.get('Product Name'),server.ident(raw.get('FastMoss Shop')),raw.get('Store Name'),raw.get('Product Image'),raw.get('Product Category'),raw.get('Selling Price / Price'),*[server.number(raw.get(k)) for k in ('7-Day Sales Volume','7-Day GMV','28-Day Sales Volume','28-Day GMV','Total Sales Volume','Total GMV')],row['id'],kind,market,eid)
            else:
                values=(raw.get('Shop Name'),None,None,None,raw.get('Main Category'),None,server.number(raw.get('Sales in Last 7 Days')),server.number(raw.get('Revenue in Last 7 Days')),None,None,server.number(raw.get('Total Sales')),server.number(raw.get('Total Revenue')),row['id'],kind,market,eid)
            c.execute('UPDATE records SET name=?,shop_id=?,shop_name=?,image=?,category=?,price=?,sales7=?,gmv7=?,sales28=?,gmv28=?,sales_total=?,gmv_total=?,snapshot_id=? WHERE kind=? AND market=? AND id=?',values)
        # Keep shared URLs; source-only images are no longer part of the library.
        for image in c.execute('SELECT * FROM images WHERE batch_id=?',(batch_id,)).fetchall():
            candidates=c.execute("SELECT batch_id,raw FROM snapshots WHERE kind='product' AND market=? AND entity_id=? ORDER BY id DESC",(image['market'],image['product_id'])).fetchall()
            source=next((x['batch_id'] for x in candidates if json.loads(x['raw']).get('Product Image')==image['url']),None)
            if source is not None:c.execute('UPDATE images SET batch_id=? WHERE id=?',(source,image['id']))
            else:c.execute('DELETE FROM images WHERE id=?',(image['id'],))
        c.execute('DELETE FROM batches WHERE id=?',(batch_id,))
        if connection is None:server.refresh_scores(connection=c)
        return {'ok':True,'filename':batch['filename'],'backup':str(backup_path),'affected':len(affected)}
