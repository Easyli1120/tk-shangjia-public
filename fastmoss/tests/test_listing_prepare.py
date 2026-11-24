import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
import collection_queue
import listing_prepare
import listing_trash
from test_collection import link, result


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=Path(self.temp.name)
        self.patches=[patch.object(server,'DATA',self.data),patch.object(server,'DB',self.data/'test.sqlite3')]
        for p in self.patches:p.start()
        server.init()
        collection_queue.submit(server,dict(request_key=uuid.uuid4().hex,links=link(0)+'\n'+link(1)))
        collector=collection_queue.Service(server,runner=result)
        collector.step();collector.step()
        self.items=[dict(market='US',id=collection_queue.tiktok_collect.product_id(link(i))) for i in range(2)]
        self.calls=[]
        def recognize(data,images,**kw):
            self.calls.append(images)
            return [dict(im,ocr_status='checked') for im in images]
        self.service=listing_prepare.Service(server,recognize)
        self.patch_service=patch.object(server,'listing_preparer',self.service);self.patch_service.start()

    def tearDown(self):
        self.service.stop();self.patch_service.stop()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def wait(self,jid):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            status=self.service.status(jid)
            if status['state'] not in ('queued','running'):return status
            time.sleep(.01)
        self.fail('Preparation did not finish')

    def test_start_and_database_requests_remain_available_during_ocr(self):
        entered=threading.Event();release=threading.Event();original=self.service.recognize
        def slow(*args,**kw):entered.set();release.wait(4);return original(*args,**kw)
        self.service.recognize=slow
        httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(httpd.server_port)
        try:
            req=urllib.request.Request(base+'/api/shop/listing-prepare-start',json.dumps({'items':self.items}).encode(),{'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=1) as r:
                self.assertEqual(r.status,202);job=json.load(r)
            self.assertTrue(entered.wait(1))
            with urllib.request.urlopen(base+'/shop-publish.js',timeout=1) as r:self.assertEqual(r.status,200)
            with urllib.request.urlopen(base+'/api/tiktok/jobs',timeout=1) as r:self.assertEqual(r.status,200)
            duplicate=self.service.start(self.items)
            self.assertEqual(duplicate['id'],job['id'])
            # A writer can commit while the recognition callback is still blocked.
            with server.db() as c:c.execute("INSERT OR REPLACE INTO gallery_settings VALUES('prepare_test','ok')")
            release.set();self.assertEqual(self.wait(job['id'])['completed'],2)
            with server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_jobs').fetchone()[0],0)
        finally:
            release.set();httpd.shutdown();httpd.server_close();thread.join()

    def test_failure_is_visible_and_retry_only_reprocesses_failed_items(self):
        original=self.service.recognize;attempt=[0]
        def fail_once(*args,**kw):
            attempt[0]+=1
            if attempt[0]==1:return [dict(im,ocr_status='unavailable',ocr_error='识别程序失败（退出码 7）') for im in args[1]]
            return original(*args,**kw)
        self.service.recognize=fail_once
        jid=self.service.start(self.items)['id'];status=self.wait(jid)
        self.assertEqual((status['state'],status['completed'],status['failed']),('partial',1,1))
        self.assertIn('退出码 7',status['rows'][0]['error'])
        self.assertTrue(status['rows'][0]['issues'])
        self.service.retry(jid);self.assertEqual(self.wait(jid)['completed'],2)
        self.assertEqual(attempt[0],3)

    def test_deleting_product_terminates_running_recognition_and_keeps_next_item(self):
        started=threading.Event();stopped=threading.Event();original=self.service.recognize;calls=[0]
        def recognize(data,images,**kw):
            calls[0]+=1
            if calls[0]==1:
                started.set()
                try:kw['run']([sys.executable,'-c','import time; time.sleep(30)'],timeout=30,capture_output=True,check=True)
                finally:stopped.set()
            return original(data,images,**kw)
        self.service.recognize=recognize
        jid=self.service.start(self.items)['id'];self.assertTrue(started.wait(1))
        with server._request_lock,server.db() as c:listing_trash.change(c,{'items':[self.items[0]]},False,server.now())
        self.assertTrue(stopped.wait(2))
        status=self.wait(jid)
        self.assertEqual(status['rows'][0]['state'],'cancelled')
        self.assertIn('已删除',status['rows'][0]['error'])
        self.assertEqual(status['completed'],1)

    def test_results_changed_after_completion_are_rejected(self):
        jid=self.service.start(self.items)['id'];self.wait(jid)
        self.assertEqual(len(self.service.status(jid,True)['result']['items']),2)
        with server._request_lock,server.db() as c:
            c.execute('INSERT OR REPLACE INTO listing_edits VALUES(?,?,?,?,?)',('US',self.items[0]['id'],'Changed','25',server.now()))
        status=self.service.status(jid,True)
        self.assertEqual(status['failed'],1);self.assertEqual(len(status['result']['items']),1)

    def test_restore_invalidates_all_results_and_restart_is_explicit(self):
        jid=self.service.start(self.items)['id'];self.wait(jid)
        self.service.invalidate('数据库已恢复')
        self.assertEqual(self.service.status(jid,True)['result']['items'],[])
        restarted=listing_prepare.Service(server)
        with self.assertRaisesRegex(ValueError,'服务已重启'):restarted.status(jid)

    def test_expired_tasks_cannot_be_opened(self):
        jid=self.service.start(self.items)['id'];self.wait(jid)
        self.service.jobs[jid]['finished_at']=time.time()-1801
        with self.assertRaisesRegex(ValueError,'过期'):self.service.status(jid)

    def test_selected_images_are_ready_before_unused_images_and_unused_failure_does_not_block(self):
        from unittest.mock import patch
        product=dict(title='Test',images=[dict(key='main',group_name='商品图'),dict(key='unused',group_name='商品图')])
        saved=dict(main_image_keys=['main'],description_image_keys=[])
        waiting=threading.Event();release=threading.Event();calls=[]
        def recognize(data,images,**kw):
            calls.append([im['key'] for im in images])
            if images[0]['key']=='unused':waiting.set();release.wait(3);return [dict(im,ocr_status='unavailable') for im in images]
            return [dict(im,ocr_status='checked') for im in images]
        self.service.recognize=recognize
        def output(data,p,details,form):return dict(images=p['images'],profile=form)
        with patch.object(listing_prepare.shop_listing,'prepare_input',return_value=(product,{},saved)),patch.object(listing_prepare.shop_listing,'prepare_output',side_effect=output):
            jid=self.service.start(self.items[:1])['id']
            try:
                self.assertTrue(waiting.wait(2))
                status=self.service.status(jid,True)
                self.assertEqual(status['completed'],1)
                self.assertEqual(len(status['result']['items']),1)
                self.assertEqual(calls,[['main'],['unused']])
            finally:release.set()
            self.service.thread.join(.1)
            self.assertEqual(self.service.status(jid)['failed'],0)

    def test_can_enter_first_prepared_product_while_next_product_is_running(self):
        release=threading.Event();waiting=threading.Event();calls=[]
        def recognize(data,images,**kw):
            calls.append(images)
            if len(calls)==2:waiting.set();release.wait(3)
            return [dict(im,ocr_status='checked') for im in images]
        self.service.recognize=recognize
        jid=self.service.start(self.items)['id']
        try:
            self.assertTrue(waiting.wait(2))
            status=self.service.status(jid,True)
            self.assertEqual(status['state'],'running')
            self.assertEqual(status['completed'],1)
            self.assertEqual(len(status['result']['items']),1)
        finally:release.set()
        self.wait(jid)


if __name__=='__main__':unittest.main()
