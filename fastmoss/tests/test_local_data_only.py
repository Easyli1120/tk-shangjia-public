"""Regression checks for removing every collection entry while preserving imports."""
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import server


class LocalDataOnlyTests(unittest.TestCase):
    def test_import_creates_local_product_without_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            with patch.object(server, 'DB', data/'test.sqlite3'), patch.object(server, 'DATA', data):
                server.init()
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(server.PHEAD)
                row = dict.fromkeys(server.PHEAD, '')
                row.update({'Product Name': 'Imported shirt', 'Country/Region': 'US', 'TikTok': 'https://shop.tiktok.com/view/product/1732794700125868708', 'Product Category': 'Clothing', 'Selling Price / Price': '25.90'})
                writer.writerow([row[k] for k in server.PHEAD])
                server.import_file('products.csv', output.getvalue().encode())
                with server.db() as c:
                    record = c.execute('SELECT * FROM records').fetchone()
                    self.assertEqual(record['name'], 'Imported shirt')
                    self.assertEqual(record['category'], 'Clothing')
                    self.assertEqual(c.execute('SELECT count(*) FROM gallery_jobs').fetchone()[0], 0)
                    self.assertEqual(c.execute('SELECT count(*) FROM image_tasks').fetchone()[0], 0)
                    self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='collection_runs'").fetchone())

    def test_stale_clients_cannot_resume_or_submit_collection(self):
        class QuietHandler(server.Handler):
            def log_message(self, *args): pass
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            for method in ('GET', 'POST'):
                for path in ('/api/browser/status', '/api/browser/next', '/api/browser/capture', '/api/collector-control', '/api/collection-run', '/api/collection-run/items', '/api/link-import', '/api/trend/refresh', '/api/tasks'):
                    req = urllib.request.Request('http://127.0.0.1:%d%s' % (httpd.server_port, path), method=method, data=b'{"action":"resume"}' if method=='POST' else None, headers={'Content-Type':'application/json'})
                    with self.subTest(method=method, path=path), self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(req)
                    self.assertEqual(error.exception.code, 410)
                    self.assertIn('已删除', json.load(error.exception)['error'])
        finally:
            httpd.shutdown();httpd.server_close();thread.join()

if __name__ == '__main__': unittest.main()
