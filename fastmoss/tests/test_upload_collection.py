import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
import batch_delete
import collection_queue as queue
from test_collection import link, result


class UploadCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
        self.service=queue.Service(server,runner=result)
        self.patches=[patch.object(server,'DB',self.data/'test.sqlite3'),patch.object(server,'DATA',self.data),patch.object(server,'collector',self.service)]
        for p in self.patches:p.start()
        server.init()
        class Quiet(server.Handler):
            def log_message(self,*args):pass
        self.httpd=server.ThreadingHTTPServer(('127.0.0.1',0),Quiet)
        self.thread=threading.Thread(target=self.httpd.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.httpd.shutdown();self.httpd.server_close();self.thread.join()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def csv(self,headers,rows):
        output=io.StringIO();writer=csv.DictWriter(output,headers);writer.writeheader();writer.writerows(rows)
        return output.getvalue().encode()

    def products(self):
        return self.csv(server.PHEAD,[{'Country/Region':'US','TikTok':link(i),'Product Name':'Imported product '+str(i)} for i in range(2)])

    def upload(self,files,origin=None):
        boundary=uuid.uuid4().hex;body=b''
        for name,content in files:
            body+=('--'+boundary+'\r\nContent-Disposition: form-data; name="files"; filename="'+name+'"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+content+b'\r\n'
        body+=('--'+boundary+'--\r\n').encode()
        headers={'Content-Type':'multipart/form-data; boundary='+boundary}
        if origin:headers['Origin']=origin
        request=urllib.request.Request('http://127.0.0.1:'+str(self.httpd.server_port)+'/api/import',data=body,headers=headers)
        with urllib.request.urlopen(request) as response:return json.load(response)

    def test_upload_automatically_queues_and_collects_product_data(self):
        item=self.upload([('products.csv',self.products())])[0]
        jid=item['collection']['id']
        self.assertEqual(item['imported'],2);self.assertTrue(self.service.wake.is_set())
        with server.db() as c:
            self.assertEqual(queue.job_status(c,jid)['counts'],{'pending':2})
            self.assertEqual(queue.job_status(c,jid)['source_batch_id'],item['batch'])
        self.service.step();self.service.step()
        with server.db() as c:
            self.assertEqual(queue.job_status(c,jid)['counts'],{'complete':2})
            self.assertGreater(c.execute('SELECT count(*) FROM gallery_items').fetchone()[0],0)
            captured=json.loads(c.execute('SELECT product_json FROM product_capture LIMIT 1').fetchone()[0])
            self.assertTrue(captured['skus']);self.assertTrue(captured['title'])

    def test_duplicate_upload_reuses_task_with_different_history_and_source_ids(self):
        shops=self.csv(server.SHEAD,[{'Country':'US','Shop Name':'Store','FastMoss Shop Detail Page Link':'https://www.fastmoss.com/shop/detail/1732439064809935743'}])
        self.assertNotIn('collection',self.upload([('shops.csv',shops)])[0])
        data=self.products();first=self.upload([('products.csv',data)])[0]
        self.service.step();self.service.step()
        again=self.upload([('products.csv',data)])[0]
        self.assertNotEqual(again['duplicate_of'],first['batch'])
        self.assertEqual(again['collection']['id'],first['collection']['id'])
        self.assertTrue(again['collection']['reused'])
        with server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM tiktok_jobs').fetchone()[0],1)

    def test_one_failed_file_does_not_block_other_uploaded_files(self):
        items=self.upload([('invalid.csv',b'name\ninvalid'),('products.csv',self.products())])
        self.assertIn('error',items[0]);self.assertIn('collection',items[1])
        with server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM tiktok_jobs').fetchone()[0],1)

    def test_queue_failure_is_reported_and_same_file_can_retry(self):
        data=self.products()
        with patch.object(queue,'submit',side_effect=RuntimeError('temporary queue failure')):
            item=self.upload([('products.csv',data)])[0]
        self.assertEqual(item['imported'],2)
        self.assertIn('自动采集未能启动',item['collection_error'])
        self.assertNotIn('collection',item)
        retry=self.upload([('products.csv',data)])[0]
        self.assertIn('duplicate_of',retry);self.assertIn('collection',retry)

    def test_delete_auto_collection_source_stops_late_result_and_reupload_starts_fresh(self):
        data=self.products();item=self.upload([('products.csv',data)])[0]
        late=self.service.claim();batch_delete.delete_batch(server,item['batch'])
        self.service.acknowledge(late,result(late))
        with server.db() as c:
            self.assertEqual(queue.job_status(c,item['collection']['id'])['state'],'deleted')
            self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0],0)
        again=self.upload([('products.csv',data)])[0]
        self.assertNotEqual(again['collection']['id'],item['collection']['id'])
        self.assertNotIn('duplicate_of',again)

    def test_cross_origin_upload_cannot_start_collection(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.upload([('products.csv',self.products())],origin='https://example.test')
        self.assertEqual(error.exception.code,403)
        with server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM tiktok_jobs').fetchone()[0],0)


if __name__=='__main__':unittest.main()
