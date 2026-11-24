import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import server
import listing_trash

class TrashTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.data=pathlib.Path(self.tmp.name)
  self.patches=[patch.object(server,'DB',self.data/'test.sqlite3'),patch.object(server,'DATA',self.data)]
  for p in self.patches:p.start()
  server.init()
  with server.db() as c:
   for market,eid in [('US','p1'),('US','p2'),('GB','p1')]:
    c.execute("INSERT INTO records(kind,market,id,name) VALUES('product',?,?,?)",(market,eid,'Product '+eid))
    server.add_draft(c,market,eid,'test','test')
   c.execute("INSERT INTO listing_edits VALUES('US','p1','Edited title','19.99','now')")
   c.execute("INSERT INTO images(market,product_id,url,local_path) VALUES('US','p1','https://example.test/kept.png','/images/kept.png')")
  (self.data/'images').mkdir();(self.data/'images/kept.png').write_bytes(b'original')
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def change(self,items,restore=False):
  with server.db() as c:return listing_trash.change(c,{'items':items},restore,server.now())
 def test_leaf_category_filter_uses_current_shop_and_keeps_captured_category(self):
  server.tiktok_shop.save(self.data,{'selected_shop_id':'store'})
  with server.db() as c:
   c.execute("UPDATE records SET category='女装 > 卫衣' WHERE market='US'")
   form={'category_id':'leaf','category_path':'女装 > 卫衣 > 连帽衫','category_shop_id':'store'}
   c.execute('INSERT INTO shop_listing_profiles VALUES(?,?,?,?)',('US','p1',json.dumps(form),0))
   result=server.result_rows(c,'product',{'draft_filter':['in'],'category':['女装 > 卫衣 > 连帽衫']})
   self.assertEqual(result['total'],1)
   self.assertEqual(result['items'][0]['listing_category'],'女装 > 卫衣 > 连帽衫')
   self.assertEqual(result['items'][0]['category'],'女装 > 卫衣')
   self.assertEqual(server.library_categories.saved_path(c,'US','p1','store'),'女装 > 卫衣 > 连帽衫')
   self.assertEqual(server.library_categories.saved_path(c,'US','p1','other'),'')
   self.assertEqual(sum(x['count'] for x in result['category_groups']),3)
   # Another shop must not inherit this shop's resolved classification.
   server.tiktok_shop.save(self.data,{'selected_shop_id':'other'})
   result=server.result_rows(c,'product',{'draft_filter':['in'],'category':['女装 > 卫衣 > 连帽衫']})
   self.assertEqual(result['total'],0)

 def test_category_filter_counts_pagination_and_search(self):
  with server.db() as c:
   c.execute("UPDATE records SET category='女装 > 卫衣' WHERE market='US' AND id='p1'")
   c.execute("UPDATE records SET category='男装 > 卫衣' WHERE market='US' AND id='p2'")
   for eid, category in [('p1','女装 > 卫衣'),('p2','男装 > 卫衣')]:
    capture={'category_id':eid,'category_is_leaf':True,'category':category}
    c.execute('INSERT INTO product_capture(market,product_id,product_json) VALUES(?,?,?)',('US',eid,json.dumps(capture)))
   c.commit()
   server.library_categories.classify_pending(server)
   result=server.result_rows(c,'product',{'draft_filter':['in'],'category':['女装 > 卫衣'],'limit':['1']})
   self.assertEqual(result['total'],1)
   self.assertEqual([(x['market'],x['id']) for x in result['items']],[('US','p1')])
   result=server.result_rows(c,'product',{'draft_filter':['in'],'category':[json.dumps(['女装 > 卫衣','男装 > 卫衣'],ensure_ascii=False)]})
   self.assertEqual(result['total'],2)
   self.assertEqual({x['category']:x['count'] for x in result['category_groups']},{'女装 > 卫衣':1,'男装 > 卫衣':1,'待识别末级类目':1})
   result=server.result_rows(c,'product',{'draft_filter':['in'],'category':['待识别末级类目']})
   self.assertEqual(result['items'][0]['market'],'GB')
   result=server.result_rows(c,'product',{'draft_filter':['in'],'q':['p2']})
   self.assertEqual(result['category_groups'],[{'category':'男装 > 卫衣','count':1}])
  self.change([{'market':'US','id':'p2'}])
  with server.db() as c:
   self.assertEqual(sum(x['count'] for x in server.result_rows(c,'product',{'draft_filter':['in']})['category_groups']),2)

 def test_parent_is_resolved_in_background_even_for_restricted_branch(self):
  mod=server.library_categories
  server.tiktok_shop.save(self.data,{'selected_shop_id':'store'})
  with server.db() as c:
   c.execute("UPDATE records SET category='Tops',name='Soft Hoodie' WHERE market='US' AND id='p1'")
   c.execute("UPDATE draft_entries SET active=0 WHERE id<>'p1' OR market<>'US'")
  with server.db() as c:c.execute("DELETE FROM listing_edits")
  categories=[{'id':'1','local_name':'Tops','parent_id':'0'},
              {'id':'2','local_name':'Hoodies','parent_id':'1','is_leaf':True,'permission_statuses':['INVITE_ONLY']},
              {'id':'3','local_name':'Sweatshirts','parent_id':'1','is_leaf':True}]
  with patch.object(mod.listing,'identity',return_value=({},{})), patch.object(mod.listing,'metadata',return_value={'mode':'local'}) as meta, patch.object(mod.listing,'bilingual_categories',return_value=categories):
   mod.classify_pending(server)
   with server.db() as c:
    result=server.result_rows(c,'product',{'draft_filter':['in'],'category':['Tops > Hoodies']})
    self.assertEqual(result['total'],1)
    self.assertEqual(result['category_groups'],[{'category':'Tops > Hoodies','count':1}])
    self.assertEqual(mod.saved_path(c,'US','p1','store'),'Tops > Hoodies')
    self.assertEqual(mod.saved_path(c,'US','p1','other'),'Tops > Hoodies')
   mod.classify_pending(server)
   meta.assert_called_once()
   with server.db() as c:
    c.execute("UPDATE records SET name='Crewneck sweatshirt' WHERE market='US' AND id='p1'")
    self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['items'][0]['listing_category'],mod.PENDING)
    self.assertEqual(mod.saved_path(c,'US','p1','store'),'')
   mod.classify_pending(server)
   with server.db() as c:
    self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['items'][0]['listing_category'],'Tops > Sweatshirts')

 def test_delete_restore_preserves_edits_assets_and_other_markets(self):
  items=[{'market':'US','id':'p1'},{'market':'US','id':'p1'}]
  self.assertEqual(self.change(items)['changed'],1)
  self.assertEqual(self.change(items)['changed'],0)
  server.init() # deleted entries must stay deleted after restart
  with server.db() as c:
   self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['total'],2)
   self.assertEqual(listing_trash.items(c)[0]['name'],'Edited title')
   self.assertFalse(server.add_draft(c,'US','p1','文件导入','重新导入'))
   c.execute("UPDATE listing_edits SET price='29.99' WHERE id='p1'")
  self.assertEqual(self.change(items,True)['changed'],1)
  self.assertEqual(self.change(items,True)['changed'],0)
  with server.db() as c:
   self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['total'],3)
   self.assertEqual(listing_trash.items(c),[])
   self.assertEqual(c.execute('SELECT price FROM listing_edits').fetchone()[0],'29.99')
   self.assertEqual(c.execute('SELECT count(*) FROM images').fetchone()[0],1)
  self.assertEqual((self.data/'images/kept.png').read_bytes(),b'original')
 def test_invalid_batch_is_atomic(self):
  for items in [[],[None],[{'market':'US','id':'p1'},{'market':'US','id':'missing'}]]:
   with self.assertRaises(ValueError):self.change(items)
  with server.db() as c:
   self.assertEqual(listing_trash.items(c),[])
   self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['total'],3)
 def test_http_delete_list_restore_and_origin_validation(self):
  class Quiet(server.Handler):
   def log_message(self,*args):pass
  httpd=server.ThreadingHTTPServer(('127.0.0.1',0),Quiet)
  thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
  base='http://127.0.0.1:'+str(httpd.server_port)
  def request(path,body=None,origin=None):
   headers={'Content-Type':'application/json'}
   if origin:headers['Origin']=origin
   req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body else None,headers=headers)
   with urllib.request.urlopen(req) as r:return json.load(r)
  try:
   body={'items':[{'market':'US','id':'p1'},{'market':'US','id':'p2'}]}
   with self.assertRaises(urllib.error.HTTPError) as error:request('/api/listings/delete',body,'https://other.test')
   self.assertEqual(error.exception.code,403)
   self.assertEqual(request('/api/listings/delete',body)['changed'],2)
   self.assertEqual(len(request('/api/listing-trash')),2)
   self.assertEqual(request('/api/listings/restore',body)['changed'],2)
   self.assertEqual(request('/api/listing-trash'),[])
  finally:httpd.shutdown();httpd.server_close();thread.join()

if __name__=='__main__':unittest.main()
