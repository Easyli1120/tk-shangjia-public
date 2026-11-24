"""Recoverable removal from the competitor library; source data stays intact."""
import json

def migrate(c):
    c.execute("CREATE TABLE IF NOT EXISTS listing_trash(market TEXT,id TEXT,deleted_at TEXT NOT NULL,PRIMARY KEY(market,id))")


def items(c):
    return [dict(row) for row in c.execute("""SELECT t.market,t.id,t.deleted_at,
        COALESCE(NULLIF(e.title,''),r.name,t.id) name,r.shop_name
        FROM listing_trash t JOIN records r ON r.kind='product' AND r.market=t.market AND r.id=t.id
        LEFT JOIN listing_edits e ON e.market=t.market AND e.id=t.id
        ORDER BY t.deleted_at DESC,t.market,t.id""")]


def cancel_product_work(c, market, eid, stamp):
    c.execute("""UPDATE tiktok_items SET state='cancelled',token=NULL,verification_hold=0,
        next_at=0,error_code='product_deleted',stage='商品已删除',updated_at=?
        WHERE market=? AND product_id=? AND state IN ('pending','running','retry','partial','failed')""",(stamp,market,eid))
    c.execute("""UPDATE tiktok_jobs SET state='finished',updated_at=? WHERE state='running'
        AND NOT EXISTS(SELECT 1 FROM tiktok_items i WHERE i.job_id=tiktok_jobs.id
                       AND i.state IN ('pending','running','retry'))""",(stamp,))


def reset_collected_data(c, market, eid):
    """Discard deleted source data on a fresh collection; retain manual edits."""
    key=(market,eid)
    c.execute('DELETE FROM gallery_items WHERE job_id IN (SELECT id FROM gallery_jobs WHERE market=? AND product_id=?)',key)
    for table in ('gallery_jobs','product_capture','product_collection','images','tiktok_product_availability'):
        c.execute('DELETE FROM '+table+' WHERE market=? AND product_id=?',key)
    c.execute('DELETE FROM library_leaf_categories WHERE market=? AND product_id=?',key)
    c.execute("""UPDATE records SET snapshot_id=NULL,category=NULL,shop_id=NULL,shop_name=NULL,
        image=NULL,price=NULL,sales7=NULL,gmv7=NULL,sales28=NULL,gmv28=NULL,sales_total=NULL,gmv_total=NULL
        WHERE kind='product' AND market=? AND id=?""",key)
    row=c.execute('SELECT profile FROM shop_listing_profiles WHERE market=? AND product_id=?',key).fetchone()
    if row:
        form=json.loads(row[0])
        if form.get('category_source')!='manual':
            for field in ('category_id','category_path','category_shop_id','category_source','category_sync'):
                form.pop(field,None)
            c.execute('UPDATE shop_listing_profiles SET profile=? WHERE market=? AND product_id=?',(json.dumps(form,ensure_ascii=False),*key))


def change(c, body, restore, stamp):
    requested=body.get('items') if isinstance(body,dict) else None
    if not isinstance(requested,list) or not 1<=len(requested)<=3000:
        raise ValueError('请选择 1 到 3000 件商品')
    keys=[]
    for item in requested:
        if not isinstance(item,dict) or not all(isinstance(item.get(k),str) and item[k].strip() for k in ('market','id')):
            raise ValueError('商品标识无效')
        key=(item['market'],item['id'])
        if key not in keys:keys.append(key)
    # Validate the entire batch before applying any changes.
    for key in keys:
        if not c.execute("SELECT 1 FROM records WHERE kind='product' AND market=? AND id=?",key).fetchone():
            raise ValueError('商品不存在，请刷新后重试')
        if not c.execute('SELECT 1 FROM draft_entries WHERE market=? AND id=?',key).fetchone():
            raise ValueError('商品不在竞品库中')
    changed=0
    for key in keys:
        if restore:
            count=c.execute('DELETE FROM listing_trash WHERE market=? AND id=?',key).rowcount
            if count:c.execute('UPDATE draft_entries SET active=1,removed_at=NULL WHERE market=? AND id=?',key)
        else:
            count=c.execute('UPDATE draft_entries SET active=0,removed_at=? WHERE market=? AND id=? AND active=1',(stamp,*key)).rowcount
            if count:
                c.execute('INSERT INTO listing_trash VALUES(?,?,?) ON CONFLICT(market,id) DO UPDATE SET deleted_at=excluded.deleted_at',(*key,stamp))
                cancel_product_work(c,*key,stamp)
        changed+=count
        if count:c.execute("INSERT INTO draft_events(market,product_id,event,source,created_at,reason) VALUES(?,?,?,'竞品库',?,?)",(*key,'恢复' if restore else '删除',stamp,'从数据恢复还原' if restore else '移入已删除商品，可恢复'))
    return {'ok':True,'changed':changed}
