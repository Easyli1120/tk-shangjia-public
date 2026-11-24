import base64
import copy
import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import zipfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import product_assets
import listing_editor as editor
import server
import shop_listing

PNG = 'data:image/png;base64,' + 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC'


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = pathlib.Path(self.tmp.name)
        self.c = sqlite3.connect(':memory:', check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript('''
          CREATE TABLE records(kind,market,id,name,price,image);
          CREATE TABLE images(id INTEGER PRIMARY KEY,market,product_id,url,local_path,state,error,batch_id);
          CREATE TABLE listing_edits(market,id,title,price,updated_at,PRIMARY KEY(market,id));
          CREATE TABLE draft_entries(market,id,active);
          INSERT INTO records VALUES('product','US','p1','Source title','10.00','');
          INSERT INTO records VALUES('product','US','p2','Other title','10.00','');
          INSERT INTO draft_entries VALUES('US','p1',1);
        ''')
        product_assets.migrate(self.c);editor.migrate(self.c)
        self.c.execute("INSERT INTO gallery_jobs(market,product_id,url,state,created_at,updated_at,discovered,cached) VALUES('US','p1','https://shop.tiktok.com/view/product/p1','done','now','now',2,2)")
        for n in range(2):
            url = 'https://cdn.test/tos-test/photo%d~signed.webp?token=old' % n
            path = '/images/original%d.png' % n
            (self.data/'images').mkdir(exist_ok=True)
            (self.data/path.lstrip('/')).write_bytes(base64.b64decode(PNG.split(',')[1]))
            self.c.execute("INSERT INTO images(market,product_id,url,local_path) VALUES('US','p1',?,?)", (url,path))
            self.c.execute("INSERT INTO gallery_items VALUES(1,?,?,'商品图')", (url,n))
        self.source_variants=[dict(id='s1',attributes=[dict(name='Color',value='Red')],price='10.00',currency='USD'),dict(id='s2',attributes=[dict(name='Color',value='Blue')],price='12.00',currency='USD')]
        self.c.execute("INSERT INTO product_capture VALUES('US','p1','{}',?,1,'now')", (json.dumps(dict(metadata_ready=True,variants=self.source_variants)),))
        self.c.commit()

    def tearDown(self):
        self.c.close();self.tmp.cleanup()

    def body(self):
        p=editor.payload(self.c,'US','p1')
        return dict(market='US',id='p1',title=p['title'],price=p['price'],revision=p['revision'],skus=[],images=[])

    def save(self,body):
        with self.c:return editor.save(self.c,self.data,body)

    def test_competitor_shop_removed_from_library_edits_and_listing(self):
        self.c.execute('ALTER TABLE records ADD COLUMN shop_name TEXT')
        self.c.execute("UPDATE records SET name='[Sleek Tee] Cotton Hoodie by sleek tee',shop_name='Sleek Tee' WHERE id='p1'")
        card=editor.product(self.c,'US','p1')
        editor.apply_card(self.c,card)
        self.assertEqual(card['name'],'Cotton Hoodie')
        self.assertEqual(self.body()['title'],'Cotton Hoodie')
        self.save(dict(self.body(),title='SLEEK-TEE | Printed Hoodie'))
        self.assertEqual(editor.listing_payload(self.c,'US','p1')['title'],'Printed Hoodie')
        self.assertEqual(self.c.execute('SELECT title FROM listing_edits').fetchone()[0],'Printed Hoodie')
        with self.assertRaisesRegex(ValueError,'补充'):
            self.save(dict(self.body(),title='Sleek Tee'))
        for title,shop,expected in [('Necklace','Ace','Necklace'),('星辰服饰纯棉卫衣','星辰服饰','纯棉卫衣'),
                                    ('A+B | Hoodie','A+B','Hoodie'),("Lee’s Hoodies",'Lee','Hoodies')]:
            self.assertEqual(editor.clean_title(title,shop),expected)

    def test_title_price_sku_and_image_edits_persist_without_touching_evidence(self):
        before=editor.payload(self.c,'US','p1')
        body=self.body();body.update(title='Local title',price='18.9',skus=[dict(id='s1',price='19.95')],images=[dict(key=before['images'][0]['key'],hidden=True),dict(key=before['images'][1]['key'],hidden=False,data=PNG)])
        self.save(body)
        p=editor.payload(self.c,'US','p1')
        self.assertEqual((p['title'],p['price']),('Local title','18.90'))
        self.assertEqual([v['price'] for v in p['variants']],['19.95','18.90'])
        current=editor.images(self.c,'US','p1');self.assertEqual(len(current),1)
        self.assertTrue(current[0]['local_path'].startswith('/images/edit-'))
        self.assertTrue((self.data/current[0]['local_path'].lstrip('/')).is_file())
        card=dict(market='US',id='p1',image='old');editor.apply_card(self.c,card)
        self.assertEqual(card['image'],current[0]['local_path']);self.assertEqual(card['listing_price'],'USD 18.90 – 19.95')
        self.assertEqual(len(product_assets.payload(self.c,'US','p1')['images']),2)
        self.assertEqual(product_assets.saved_product_data(self.c,'US','p1')['saved_images'],2)
        self.assertEqual(json.loads(self.c.execute('SELECT audit_json FROM product_capture').fetchone()[0])['variants'],self.source_variants)
        with product_assets.download_archive(self.c,self.data,'US','p1',images=current) as archive:
            with zipfile.ZipFile(archive) as z:self.assertEqual(len(z.namelist()),1)
        # Signed source URL renewal must not resurrect deleted images or discard replacements.
        raw=product_assets.payload(self.c,'US','p1')['images']
        self.c.execute('DELETE FROM gallery_items')
        for n,i in enumerate(raw):
            url=i['url'].replace('~signed.webp?token=old','~new.webp?token=new')
            self.c.execute("INSERT INTO images(market,product_id,url) VALUES('US','p1',?)",(url,))
            self.c.execute("INSERT INTO gallery_items VALUES(1,?,?,'商品图')",(url,n))
        self.assertEqual(editor.images(self.c,'US','p1')[0]['local_path'],current[0]['local_path'])
        self.assertEqual(len(editor.images(self.c,'US','p1')),1)

    def test_listing_form_saves_product_edits_and_profile_together(self):
        shop_listing.migrate(self.c)
        self.c.execute("UPDATE product_capture SET product_json=? WHERE product_id='p1'",(json.dumps({'desc_blocks':[{'type':'text','text':'English description'}]}),))
        body=self.body();body.update(title='Inline title',skus=[dict(id='s1',price='29.95'),dict(id='s2',price='30')])
        profile=dict(description='English description',sku_attributes={'s1':[dict(name='Color',value='Navy')]})
        with patch.object(server,'db',return_value=self.c),patch.object(server,'DATA',self.data):
            result=shop_listing.post(server,'listing-profile',dict(market='US',id='p1',profile=profile,edits=body))
            prepared=editor.listing_payload(self.c,'US','p1')
            self.assertEqual(prepared['title'],'Inline title')
            self.assertEqual([v['price'] for v in prepared['variants']],['29.95','30.00'])
            self.assertEqual(prepared['revision'],result['revision'])
            self.assertEqual(shop_listing.profile(self.c,'US','p1'),profile)
            # A stale form cannot overwrite either the new prices or saved description.
            with self.assertRaises(ValueError):shop_listing.post(server,'listing-profile',dict(market='US',id='p1',profile={'description':'Stale'},edits=body))
            self.assertEqual(shop_listing.profile(self.c,'US','p1'),profile)

    def test_restore_clear_override_and_local_upload(self):
        p=editor.payload(self.c,'US','p1');body=self.body()
        body.update(images=[dict(key=p['images'][0]['key'],hidden=True),dict(add=True,data=PNG)],skus=[dict(id='s1',price='5')])
        self.save(body);self.assertEqual(len(editor.images(self.c,'US','p1')),2)
        body=self.body();body.update(images=[dict(key=p['images'][0]['key'],hidden=False)],skus=[dict(id='s1',price='')])
        self.save(body);self.assertEqual(len(editor.images(self.c,'US','p1')),3)
        self.assertEqual(editor.payload(self.c,'US','p1')['variants'][0]['price'],'10.00')

    def test_invalid_prices_foreign_assets_and_invalid_uploads_are_atomic(self):
        initial=self.body()
        cases=[dict(price='0'),dict(price='1.001'),dict(title=' '),dict(skus=[dict(id='foreign',price='1')]),dict(images=[dict(key='foreign',hidden=True)]),dict(images=[dict(key=editor.images(self.c,'US','p1')[0]['key'],data='data:image/png;base64,SGVsbG8=')])]
        for change in cases:
            body=copy.deepcopy(initial);body.update(change)
            with self.assertRaises(ValueError):self.save(body)
            self.assertEqual(self.body(),initial)
        self.save(dict(initial,title='changed'))
        with self.assertRaisesRegex(ValueError,'其他操作'):self.save(initial)

    def test_all_hidden_stays_empty_and_does_not_affect_other_products(self):
        body=self.body();body['images']=[dict(key=i['key'],hidden=True) for i in editor.images(self.c,'US','p1')]
        self.save(body)
        card=dict(market='US',id='p1',image='old');editor.apply_card(self.c,card)
        self.assertEqual(card['image'],'')
        self.assertEqual(editor.payload(self.c,'US','p2')['title'],'Other title')
        with self.assertRaises(ValueError):editor.payload(self.c,'US','missing')

    def test_option_removal_persists_across_capture_and_keeps_single_sku_exclusions(self):
        p=editor.payload(self.c,'US','p1');blue=p['option_groups'][0]['options'][1]['key']
        before=self.c.execute('SELECT product_json,audit_json FROM product_capture').fetchone()
        body=self.body();body['exclusions']=[dict(kind='option',key=blue,hidden=True)]
        self.save(body)
        self.assertEqual([v['id'] for v in editor.variants(self.c,'US','p1')],['s1'])
        self.assertTrue(editor.payload(self.c,'US','p1')['variants'][1]['hidden'])
        self.assertEqual(tuple(self.c.execute('SELECT product_json,audit_json FROM product_capture').fetchone()),tuple(before))
        # A newly discovered blue SKU must inherit the option exclusion.
        extra=dict(id='s3',attributes=[dict(name='Color',value='Blue')],price='13',currency='USD')
        self.c.execute('UPDATE product_capture SET audit_json=?',(json.dumps(dict(metadata_ready=True,variants=self.source_variants+[extra])),))
        self.assertEqual([v['id'] for v in editor.listing_payload(self.c,'US','p1')['variants']],['s1'])
        body=self.body();body['exclusions']=[dict(kind='sku',key='s2',hidden=True),dict(kind='option',key=blue,hidden=False)]
        self.save(body)
        self.assertEqual([v['id'] for v in editor.variants(self.c,'US','p1')],['s1','s3'])
        body=self.body();body['exclusions']=[dict(kind='sku',key='s2',hidden=False)];self.save(body)
        self.assertEqual(len(editor.variants(self.c,'US','p1')),3)

    def test_invalid_rule_is_atomic_and_all_removed_draft_is_not_publishable(self):
        initial=self.body()
        for rules in [[dict(kind='option',key='foreign',hidden=True)],[dict(kind='sku',key='s1',hidden='yes')],[dict(kind='sku',key='s1',hidden=True)]*2]:
            body=dict(initial,title='Changed',exclusions=rules)
            with self.assertRaises(ValueError):self.save(body)
            self.assertEqual(self.body(),initial)
        body=self.body();body['exclusions']=[dict(kind='sku',key=v['id'],hidden=True) for v in self.source_variants];self.save(body)
        self.assertFalse(editor.listing_payload(self.c,'US','p1')['publishable'])
        self.assertEqual(editor.listing_payload(self.c,'US','p1')['variants'],[])
        self.assertEqual(len(editor.payload(self.c,'US','p1')['variants']),2)
        self.assertEqual(editor.payload(self.c,'US','p2')['variants'],[])

    def test_source_property_identity_images_and_size_intersection(self):
        raw={'sale_props':[{'prop_id':'c','prop_name':'Size','has_image':True,'sale_prop_values':[{'prop_value_id':'blue','prop_value':'Blue','image':{'url_list':['https://cdn.test/tos-test/photo0~signed.webp?token=old']}}]}],
             'skus':[{'sku_id':'s1','sku_sale_props':[{'prop_id':'c','prop_value_id':'blue','prop_name':'Size','prop_value':'Blue'},{'prop_id':'s','prop_value_id':'m','prop_name':'Color','prop_value':'M'}]}]}
        self.c.execute('UPDATE product_capture SET product_json=?',(json.dumps(raw),))
        p=editor.payload(self.c,'US','p1')
        self.assertEqual(p['option_groups'][0]['options'][0]['image'],'/images/original0.png')
        self.assertEqual(len(p['variants'][0]['option_keys']),2)
        size=p['option_groups'][1]['options'][0]['key']
        body=self.body();body['exclusions']=[dict(kind='option',key=size,hidden=True)];self.save(body)
        self.assertTrue(editor.payload(self.c,'US','p1')['variants'][0]['hidden'])

    def test_http_save_preview_download_and_batch_price_roundtrip(self):
        class QuietHandler(server.Handler):
            def log_message(self,*args):pass
        httpd=server.ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
        base='http://127.0.0.1:'+str(httpd.server_port)
        def request(path,body=None,origin=None):
            headers={'Content-Type':'application/json'}
            if origin:headers['Origin']=origin
            req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            return urllib.request.urlopen(req)
        with patch.object(server,'db',return_value=self.c),patch.object(server,'DATA',self.data):
            thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            try:
                with request('/api/listing-editor?market=US&id=p1') as r:original=json.load(r)
                body=self.body();body.update(title='HTTP title',price='22',skus=[dict(id='s1',price='23')],images=[dict(key=original['images'][0]['key'],hidden=False,data=PNG)])
                with request('/api/listing-editor',body) as r:self.assertTrue(json.load(r)['ok'])
                with request('/api/listing-editor?market=US&id=p1') as r:updated=json.load(r)
                self.assertEqual(updated['title'],'HTTP title')
                with request(updated['images'][0]['local_path']) as r:
                    self.assertEqual(r.headers['Content-Type'],'image/png')
                    self.assertEqual(r.read(),base64.b64decode(PNG.split(',')[1]))
                with request('/api/gallery/download?market=US&id=p1') as r:self.assertEqual(r.headers['Content-Type'],'application/zip')
                with self.assertRaises(urllib.error.HTTPError) as error:request('/api/listing-editor',self.body(),origin='https://external.example')
                self.assertEqual(error.exception.code,403)
                body=self.body();body['exclusions']=[dict(kind='sku',key='s2',hidden=True)]
                with request('/api/listing-editor',body) as r:self.assertTrue(json.load(r)['ok'])
                # Uniform batch pricing must replace previously edited individual SKU prices.
                with request('/api/listing-edit',dict(items=[dict(market='US',id='p1',price='25')])) as r:self.assertTrue(json.load(r)['ok'])
                with request('/api/listing-editor?market=US&id=p1') as r:self.assertEqual([v['price'] for v in json.load(r)['variants']],['25','25'])
                size_variants=[dict(id='s1',attributes=[dict(name='Size',value='S')],price='10.00',currency='USD'),dict(id='s2',attributes=[dict(name='Size',value='M')],price='12.00',currency='USD')]
                self.c.execute('UPDATE product_capture SET audit_json=?',(json.dumps(dict(variants=size_variants)),))
                with request('/api/listing-edit',dict(items=[dict(market='US',id='p1',size_prices={'S':'31','M':'32'})])) as r:
                    result=json.load(r);self.assertEqual((result['changed'],result['sku_count']),(1,2))
                with request('/api/listing-editor?market=US&id=p1') as r:
                    self.assertEqual([v['price'] for v in json.load(r)['variants']],['31.00','32.00'])
                with request('/api/listing-payload?market=US&id=p1') as r:
                    prepared=json.load(r);self.assertEqual([v['id'] for v in prepared['variants']],['s1'])
                    self.assertEqual(prepared['variants'][0]['price'],'31.00')
            finally:
                httpd.shutdown();httpd.server_close();thread.join()


if __name__=='__main__':unittest.main()
