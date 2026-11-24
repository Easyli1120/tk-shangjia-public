import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import batch_delete
import collection_queue as queue
import library_categories as categories
import listing_trash
import server
import shop_listing
from test_collection import link, result


class DeletionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
        self.patches=[patch.object(server,'DB',self.data/'test.sqlite3'),patch.object(server,'DATA',self.data)]
        for p in self.patches:p.start()
        server.init()
        self.service=queue.Service(server,runner=result)
        self.pid=queue.tiktok_collect.product_id(link(0))

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def submit(self,links=None):
        return queue.submit(server,{'request_key':uuid.uuid4().hex,'links':links or link(0)})['id']

    def delete(self,pid=None,restore=False):
        with server.db() as c:
            return listing_trash.change(c,{'items':[{'market':'US','id':pid or self.pid}]},restore,server.now())

    def import_products(self,count=1):
        output=io.StringIO();writer=csv.DictWriter(output,server.PHEAD);writer.writeheader()
        for i in range(count):
            writer.writerow({'Country/Region':'US','TikTok':link(i),'Product Name':'Printed garment '+str(i),'Product Category':'Tops'})
        return server.import_file('products.csv',output.getvalue().encode())['batch']

    def test_delete_running_product_cancels_token_and_cannot_retry_after_restore(self):
        self.submit();self.service.step()
        jid=self.submit();item=self.service.claim()
        self.service.progress(item,{'stage':'等待验证码','verification':True})
        self.delete();self.delete(restore=True)
        queue.control(server,{'id':jid,'action':'retry'})
        self.service.acknowledge(item,result(item))
        self.service.recover()
        self.assertIsNone(self.service.claim())
        with server.db() as c:
            row=c.execute('SELECT * FROM tiktok_items WHERE id=?',(item['id'],)).fetchone()
            self.assertEqual((row['state'],row['token'],row['verification_hold'],row['error_code']),('cancelled',None,0,'product_deleted'))
            self.assertEqual(c.execute('SELECT count(*) FROM product_capture').fetchone()[0],1)

    def test_new_collection_in_same_second_replaces_deleted_capture_even_if_partial(self):
        with patch.object(server,'now',return_value='2026-11-23 12:00:00'):
            self.submit();self.service.step();self.delete()
            jid=self.submit();item=self.service.claim();fresh=result(item,False)
            fresh['product']['title']='New source title';fresh['product']['sold_count']='7'
            with server.db() as c:
                c.execute('INSERT INTO listing_edits VALUES(?,?,?,?,?)',('US',self.pid,'Manual title','42.00',server.now()))
                c.execute('INSERT INTO shop_listing_profiles VALUES(?,?,?,?)',('US',self.pid,json.dumps({'category_id':'old','category_path':'Old path','stock':'9'}),0))
            self.service.acknowledge(item,fresh)
        with server.db() as c:
            saved=c.execute('SELECT * FROM product_capture').fetchone()
            self.assertEqual(saved['metadata_ready'],0)
            self.assertEqual(json.loads(saved['product_json'])['sold_count'],'7')
            self.assertEqual(c.execute('SELECT active FROM draft_entries').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM listing_trash').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT title FROM listing_edits').fetchone()[0],'Manual title')
            self.assertEqual(shop_listing.profile(c,'US',self.pid),{'stock':'9'})
            self.assertEqual(queue.job_status(c,jid)['counts'],{'retry':1})

    def test_unresolved_old_short_link_cannot_overwrite_new_collection(self):
        self.submit();self.service.step()
        old=self.submit('https://vt.tiktok.com/abc/');late=self.service.claim()
        self.delete()
        new=self.submit()
        self.service.acknowledge(late,result(dict(late,product_id=self.pid)))
        self.service.step()
        with server.db() as c:
            self.assertEqual(queue.job_status(c,old)['counts'],{'cancelled':1})
            self.assertEqual(queue.job_status(c,new)['counts'],{'complete':1})
            self.assertEqual(c.execute('SELECT active FROM draft_entries').fetchone()[0],1)

    def test_delete_running_source_removes_orphans_and_discards_late_result(self):
        jid=self.submit(link(0)+'\n'+link(1));self.service.step();late=self.service.claim()
        with server.db() as c:bid=queue.job_status(c,jid)['batch_id']
        deleted=batch_delete.delete_batch(server,bid)
        self.assertTrue(Path(deleted['backup']).exists())
        self.service.acknowledge(late,result(late));self.service.recover()
        with server.db() as c:
            self.assertEqual(queue.job_status(c,jid)['state'],'deleted')
            for table in ('records','draft_entries','product_capture','product_collection','gallery_jobs','gallery_items','images'):
                self.assertEqual(c.execute('SELECT count(*) FROM '+table).fetchone()[0],0,table)
        with self.assertRaisesRegex(ValueError,'任务已删除'):
            queue.control(server,{'id':jid,'action':'retry'})
        self.submit();self.service.step()
        with server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM product_capture').fetchone()[0],1)

    def test_delete_one_source_keeps_other_source_and_queue(self):
        bid=self.import_products()
        self.submit();self.service.step()
        other=self.submit(link(8));running=self.service.claim()
        batch_delete.delete_batch(server,bid)
        self.service.acknowledge(running,result(running))
        with server.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM product_capture').fetchone()[0],2)
            self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['total'],2)
            self.assertEqual(queue.job_status(c,other)['counts'],{'complete':1})

    def classify(self,callback,legacy=False):
        server.tiktok_shop.save(self.data,{'selected_shop_id':'store'})
        tree=[{'id':'root','local_name':'Tops','parent_id':'0'},
              {'id':'leaf','local_name':'Shirts','parent_id':'root','is_leaf':True,'permission_statuses':['AVAILABLE']},
              {'id':'other','local_name':'Hoodies','parent_id':'root','is_leaf':True,'permission_statuses':['AVAILABLE']}]
        with patch.object(shop_listing,'identity',return_value=({'selected_shop_id':'store'},{})), \
             patch.object(shop_listing,'metadata',return_value={'mode':'global','categories':tree}), \
             patch.object(shop_listing,'bilingual_categories',return_value=tree), \
             patch.object(shop_listing,'call',side_effect=callback) as api:
            if legacy:
                shop_listing._category_sync_lock.acquire()
                shop_listing.sync_listing_categories(server)
                self.assertEqual(shop_listing._category_sync_state['error'],'')
            else:categories.classify_pending(server)
        return api

    def test_source_delete_during_category_request_stops_remaining_products(self):
        bid=self.import_products(2)
        def delete_in_flight(*args,**kwargs):
            batch_delete.delete_batch(server,bid)
            raise shop_listing.shop.ShopError('request ended after deletion')
        api=self.classify(delete_in_flight)
        self.assertEqual(api.call_count,1)
        with server.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM library_leaf_categories').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0],0)

    def test_deleted_reimported_same_snapshot_id_rejects_old_category_result(self):
        bid=self.import_products()
        def replace_in_flight(*args,**kwargs):
            batch_delete.delete_batch(server,bid)
            self.assertEqual(self.import_products(),bid) # SQLite reuses this source ID.
            return {'leaf_category_id':'leaf'}
        api=self.classify(replace_in_flight)
        self.assertEqual(api.call_count,1)
        with server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM library_leaf_categories').fetchone()[0],0)
        api=self.classify(lambda *a,**k:{'leaf_category_id':'leaf'})
        self.assertEqual(api.call_count,1)
        with server.db() as c:self.assertEqual(c.execute('SELECT path FROM library_leaf_categories').fetchone()[0],'Tops > Shirts')

    def test_listing_category_sync_discards_deleted_then_restored_product(self):
        self.import_products()
        def delete_in_flight(*args,**kwargs):
            self.delete();self.delete(restore=True)
            return {'leaf_category_id':'leaf'}
        api=self.classify(delete_in_flight,legacy=True)
        self.assertEqual(api.call_count,1)
        with server.db() as c:self.assertEqual(shop_listing.profile(c,'US',self.pid),{})
        self.classify(lambda *a,**k:{'leaf_category_id':'leaf'},legacy=True)
        with server.db() as c:self.assertEqual(shop_listing.profile(c,'US',self.pid)['category_id'],'leaf')

    def test_http_source_delete_automatically_stops_running_task(self):
        jid=self.submit();item=self.service.claim()
        with server.db() as c:bid=queue.job_status(c,jid)['batch_id']
        class Quiet(server.Handler):
            def log_message(self,*args):pass
        httpd=server.ThreadingHTTPServer(('127.0.0.1',0),Quiet)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        try:
            request=urllib.request.Request('http://127.0.0.1:'+str(httpd.server_port)+'/api/batches/delete',
                data=json.dumps({'id':bid}).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request) as response:self.assertTrue(json.load(response)['ok'])
            self.service.acknowledge(item,result(item))
            with server.db() as c:
                self.assertEqual(queue.job_status(c,jid)['state'],'deleted')
                self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0],0)
        finally:
            httpd.shutdown();httpd.server_close();thread.join()

    def test_file_collection_enqueues_active_products_and_reuses_running_job(self):
        bid=self.import_products(2)
        with server.db() as c:original=[tuple(r) for r in c.execute('SELECT id,raw FROM snapshots WHERE batch_id=?',(bid,))]
        request={'source_batch_id':bid,'request_key':uuid.uuid4().hex}
        created=queue.submit(server,request)
        self.assertEqual(created['total'],2)
        self.assertEqual(queue.submit(server,{**request,'request_key':uuid.uuid4().hex})['id'],created['id'])
        with server.db() as c:
            status=queue.job_status(c,created['id'])
            self.assertEqual(status['source_batch_id'],bid)
            self.assertEqual(status['source_filename'],'products.csv')
            self.assertNotEqual(status['batch_id'],bid)
        self.service.step();self.service.step()
        with server.db() as c:
            self.assertEqual(queue.job_status(c,created['id'])['counts'],{'complete':2})
            self.assertEqual(c.execute('SELECT count(*) FROM product_capture').fetchone()[0],2)
            self.assertGreater(c.execute('SELECT count(*) FROM gallery_items').fetchone()[0],0)
            self.assertEqual([tuple(r) for r in c.execute('SELECT id,raw FROM snapshots WHERE batch_id=?',(bid,))],original)

    def test_file_collection_excludes_deleted_products_and_rejects_missing_source(self):
        bid=self.import_products(2);self.delete()
        request={'source_batch_id':bid,'request_key':uuid.uuid4().hex}
        self.assertEqual(queue.submit(server,request)['total'],1)
        batch_delete.delete_batch(server,bid)
        with self.assertRaisesRegex(ValueError,'不存在或已删除'):
            queue.submit(server,{**request,'request_key':uuid.uuid4().hex})

    def test_file_source_delete_cancels_derived_collection_and_preserves_other_products(self):
        bid=self.import_products(2)
        jid=queue.submit(server,{'source_batch_id':bid,'request_key':uuid.uuid4().hex})['id']
        self.service.step();late=self.service.claim()
        other=self.submit(link(8))
        with server.db() as c:child=queue.job_status(c,jid)['batch_id']
        batch_delete.delete_batch(server,bid)
        self.service.acknowledge(late,result(late));self.service.step()
        with server.db() as c:
            self.assertEqual(queue.job_status(c,jid)['state'],'deleted')
            self.assertIsNone(queue.job_status(c,jid)['source_batch_id'])
            self.assertEqual(c.execute('SELECT count(*) FROM batches WHERE id IN (?,?)',(bid,child)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM product_capture').fetchone()[0],1)
            self.assertEqual(queue.job_status(c,other)['counts'],{'complete':1})

    def test_file_source_delete_rolls_back_linked_collection_on_failure(self):
        bid=self.import_products()
        jid=queue.submit(server,{'source_batch_id':bid,'request_key':uuid.uuid4().hex})['id'];self.service.step()
        with patch.object(listing_trash,'reset_collected_data',side_effect=RuntimeError('simulated storage error')):
            with self.assertRaises(RuntimeError):batch_delete.delete_batch(server,bid)
        with server.db() as c:
            self.assertEqual(queue.job_status(c,jid)['state'],'finished')
            self.assertEqual(queue.job_status(c,jid)['source_batch_id'],bid)
            self.assertEqual(c.execute('SELECT count(*) FROM batches').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT count(*) FROM product_capture').fetchone()[0],1)


if __name__=='__main__':unittest.main()
