import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
import collection_queue as q
import tiktok_collect as t
import browser_capture
import shop_listing

FIXTURE=json.loads((Path(__file__).parent/'fixtures/tiktok-product.json').read_text())
TREE=[dict(id='10',parent_id='0',local_name='Toys',is_leaf=False),
      dict(id='20',parent_id='10',local_name='Arts & Crafts',is_leaf=True)]

def link(i):return 'https://shop.tiktok.com/view/product/'+str(1732439064809935743+i)+'?region=US&local=en'

def result(item,complete=True):
    p=copy.deepcopy(FIXTURE);p['product_id']=item['product_id'];p['category']='Toys > Arts & Crafts' if complete else '';p['category_id']='20';p['category_is_leaf']=True
    imgs=t.manifest(p)
    for x in imgs:x['local_path']='/images/test.png'
    if not complete:imgs[0]['local_path']=None
    return dict(product=p,images=imgs,quality=t.audit(p,imgs))

class ParserTests(unittest.TestCase):
    def test_link_limits_dedup_and_hosts(self):
        links,dupes=t.parse_links(link(0)+'\n'+link(0)+'\n'+link(1))
        self.assertEqual((len(links),dupes),(2,1))
        self.assertEqual(len(t.parse_links('\n'.join(link(i) for i in range(3000)))[0]),3000)
        for value in ('\n'.join(link(i) for i in range(3001)), 'https://tiktok.com.attacker.test/view/product/123456789', 'https://localhost/view/product/123456789','https://shop.tiktok.com:8443/view/product/123456789',link(0)+' wrong'):
            with self.assertRaises(ValueError):t.parse_links(value)
        self.assertEqual(t.parse_links('https://shop.tiktok.com/gb/pdp/name/123456789')[0][0]['market'],'GB')
        self.assertEqual(t.parse_links('https://vt.tiktok.com/abc/')[0][0]['product_id'],'')
    def test_region_unavailable_detects_visible_message_without_using_recommendations(self):
        for message in ('此国家或地区未提供的商品', '此國家或地區未提供的商品', 'Product not available in this country', 'This item is unavailable in your region'):
            body='<h1>'+message+'</h1><script type="application/json">'+json.dumps(FIXTURE)+'</script>'
            with self.subTest(message=message),self.assertRaises(t.CaptureError) as error:t.extract_product(body,'123456789')
            self.assertEqual(error.exception.code,'regional_unavailable')
        self.assertFalse(t.regional_unavailable('<script>"此国家或地区未提供的商品"</script><h1>Loading</h1>'))
        with self.assertRaises(t.CaptureError) as error:t.extract_product('<h1>Verify that you are human</h1><p>此国家或地区未提供的商品</p>','123456789')
        self.assertEqual(error.exception.code,'verification')

    def test_exact_identity_and_no_recommendation_images(self):
        p=copy.deepcopy(FIXTURE);other=copy.deepcopy(p);other['product_id']='999999999999';other['images']=[{'url_list':['https://p16.tiktokcdn.com/other.png']}]
        page='<script type="application/json">'+json.dumps({'product':p,'recommended':[other]})+'</script><script type="application/ld+json">'+json.dumps({'@type':'BreadcrumbList','itemListElement':[{'position':1,'name':'TikTok Shop','item':'https://shop.tiktok.com/us'},{'position':2,'name':'Toys','item':'https://shop.tiktok.com/us/c/toys/1'}]})+'</script>'
        extracted=t.extract_product(page,p['product_id']);self.assertEqual(extracted['category'],'Toys')
        self.assertFalse(any('other.png' in i['url'] for i in t.manifest(extracted)))
        with self.assertRaises(t.CaptureError):t.extract_product(page,'888888888')
        for body in ('<title>Security Check</title>','<h1>Verify that you are human</h1>'):
            with self.assertRaises(t.CaptureError) as e:t.extract_product(body,p['product_id'])
            self.assertEqual(e.exception.code,'verification')
    def test_missing_sku_and_images_still_block_completion(self):
        p=copy.deepcopy(FIXTURE);imgs=t.manifest(p)
        a=t.audit(p,imgs);self.assertNotIn('类目缺失',a['missing']);self.assertIn('部分图片未下载',a['missing'])
        self.assertEqual(len(a['variants']),3)
        p['category']='Toys';p['skus'][0]['price']={};p['skus'][0]['sku_sale_props']=[]
        a=t.audit(p,imgs);self.assertIn('部分 SKU 价格缺失',a['missing']);self.assertIn('部分 SKU 颜色/规格缺失',a['missing'])

    def test_product_leaf_path_wins_over_public_breadcrumb_and_other_products(self):
        p=copy.deepcopy(FIXTURE)
        p['category']='Toys > Arts & Crafts'
        p['category_path']=[{'id':'10','name':'Toys'},{'id':'20','name':'Arts & Crafts'},
                            {'id':'30','name':'Drawing'},{'id':'40','name':'Drawing Kits','is_leaf':True}]
        other=copy.deepcopy(p);other['product_id']='999999999999';other['category_path'][-1]['id']='wrong'
        page='<script type="application/json">'+json.dumps({'product':p,'recommended':[other]})+'</script>'
        page+='<script type="application/ld+json">'+json.dumps({'@type':'BreadcrumbList','itemListElement':[
            {'position':1,'name':'Toys','item':'https://shop.tiktok.com/us/c/toys/10'}]})+'</script>'
        extracted=t.extract_product(page,p['product_id'])
        self.assertEqual(extracted['category'],'Toys > Arts & Crafts > Drawing > Drawing Kits')
        self.assertEqual(extracted['category_id'],'40')
        self.assertIs(extracted['category_is_leaf'],True)

    def test_product_bound_category_info_is_not_overwritten_by_breadcrumb(self):
        p=copy.deepcopy(FIXTURE)
        nav=[{'category_id':str(i),'category_name':name} for i,name in enumerate(['Toys','Crafts','Drawing','Kits'],1)]
        page='<script type="application/json">'+json.dumps({'product_info':p,'category_info':{'recommended_categories':nav}})+'</script>'
        extracted=t.extract_product(page,p['product_id'])
        self.assertEqual((extracted['category'],extracted['category_id']),('Toys > Crafts > Drawing > Kits','4'))
        self.assertNotIn('category_is_leaf',extracted)

    def test_leaf_requires_evidence_not_path_depth(self):
        p=result({'product_id':'123456789'})['product']
        for leaf,cid in [(None,'20'),(False,'20'),('true','20'),(True,'')]:
            p.update(category='Toys > Arts & Crafts > Drawing > Kits',category_is_leaf=leaf,category_id=cid)
            self.assertFalse(t.confirmed_leaf(p))
            self.assertNotIn(t.CATEGORY_MISSING,t.audit(p,[])['missing'])
        p.update(category='Arts & Crafts',category_id='20',category_is_leaf=True)
        self.assertNotIn(t.CATEGORY_MISSING,t.audit(p,[])['missing'])

    def test_official_tree_confirms_only_exact_unique_observed_leaf(self):
        p={'category':'Arts & Crafts','category_id':'20'}
        self.assertTrue(t.confirm_category(p,TREE))
        self.assertEqual(p['category'],'Toys > Arts & Crafts')
        self.assertTrue(t.confirmed_leaf(p))
        for source in [dict(category='Toys',category_id='10'),dict(category='Toys',category_id='20'),
                       dict(category='Art',category_id='20'),dict(category='Craft kit',title='Arts & Crafts')]:
            self.assertFalse(t.confirm_category(source,TREE))
        duplicate=TREE+[dict(id='30',parent_id='0',local_name='Arts & Crafts',is_leaf=True)]
        self.assertFalse(t.confirm_category(dict(category='Arts & Crafts'),duplicate))
        self.assertFalse(t.confirm_category(dict(category='Arts & Crafts',category_id='20'),TREE[1:]))

    def test_path_does_not_inherit_unrelated_product_leaf_id(self):
        p=copy.deepcopy(FIXTURE)
        p.update(category_id='20',category_is_leaf=True,category_path=[{'name':'Toys'}])
        body='<script type="application/json">'+json.dumps(p)+'</script>'
        extracted=t.extract_product(body,p['product_id'])
        self.assertEqual(extracted['category'],'Toys')
        self.assertFalse(t.confirmed_leaf(extracted))
        self.assertNotIn('category_id',extracted)
        p['category_path']=[{'name':'Toys'},{'id':'20','is_leaf':True}]
        extracted=t.extract_product('<script type="application/json">'+json.dumps(p)+'</script>',p['product_id'])
        self.assertFalse(t.confirmed_leaf(extracted))

class BrowserTests(unittest.TestCase):
    class Connection:
        def __init__(self,url,doms,events=()):
            self.url=url;self.doms=list(doms);self.events=list(events);self.calls=[];self.clock=100
        def call(self,method,params=None,session=None):
            self.calls.append(method)
            if method=='Target.attachToTarget':return {'sessionId':'s'}
            if method=='Page.getFrameTree':return {'frameTree':{'frame':{'id':'main','url':self.url}}}
            if method=='Runtime.evaluate':
                dom=self.doms.pop(0) if len(self.doms)>1 else self.doms[0]
                return {'result':{'value':dom}}
            if method=='Page.getResourceTree':raise RuntimeError('No resource')
            return {}
        def next_event(self):
            self.clock+=2
            return self.events.pop(0) if self.events else {}

    def test_reuses_current_page_and_does_not_reload_immediately_after_verification(self):
        c=self.Connection(link(0),['challenge product','waiting','waiting','product'])
        progress=[]
        with patch.object(browser_capture.time,'monotonic',side_effect=lambda:c.clock):
            body=browser_capture.read_page(c,link(0),lambda b:'product' in b,'tab',
                lambda **info:progress.append(info),lambda b:'challenge' in b)
        self.assertEqual(body,'product')
        self.assertNotIn('Page.navigate',c.calls);self.assertNotIn('Page.reload',c.calls)
        self.assertEqual(c.calls.count('Page.bringToFront'),1)
        self.assertTrue(progress[0]['verification']);self.assertFalse(progress[-1]['verification'])

    def test_region_unavailable_returns_at_first_check_without_reload(self):
        body='<h1>此国家或地区未提供的商品</h1>'
        c=self.Connection(link(0),[body])
        with patch.object(browser_capture.time,'monotonic',side_effect=lambda:c.clock):
            observed=browser_capture.read_page(c,link(0),lambda b:False,'tab',is_terminal=t.regional_unavailable)
        self.assertEqual(observed,body);self.assertEqual(c.clock,100)
        self.assertNotIn('Page.reload',c.calls)
        with patch.object(t,'browser_page',return_value=body),patch.object(t,'cache_image') as images:
            captured=t.capture(link(0),t.product_id(link(0)),'.')
        self.assertEqual(captured['code'],'regional_unavailable');images.assert_not_called()

    def test_verification_clears_on_first_check_without_extra_wait_or_reload(self):
        c=self.Connection(link(0),['challenge']*20+['loading','product'])
        progress=[]
        with patch.object(browser_capture.time,'monotonic',side_effect=lambda:c.clock):
            body=browser_capture.read_page(c,link(0),lambda b:b=='product','tab',
                lambda **info:progress.append(info),lambda b:b=='challenge')
        self.assertEqual(body,'product')
        self.assertNotIn('Page.reload',c.calls)
        self.assertTrue(any(p.get('stage')=='验证已完成，正在读取商品' and p['verification'] is False for p in progress))
        self.assertEqual(c.calls.count('Page.bringToFront'),1)
        self.assertLess(c.clock,150)

    def test_document_without_product_stops_waiting_at_load_timeout(self):
        c=self.Connection(link(0),['loading'])
        with patch.object(browser_capture.time,'monotonic',side_effect=lambda:c.clock):
            with self.assertRaises(TimeoutError):browser_capture.read_page(c,link(0),lambda b:False,'tab')
        self.assertLessEqual(c.clock,134)
        self.assertEqual(c.calls.count('Page.reload'),1)

    def test_http_429_is_a_queue_rate_limit_signal(self):
        event={'sessionId':'s','method':'Network.responseReceived','params':{
            'requestId':'r','frameId':'main','type':'Document','response':{'url':link(0),'status':429}}}
        c=self.Connection(link(0),['loading'],[event])
        with patch.object(browser_capture.time,'monotonic',side_effect=lambda:c.clock):
            with self.assertRaises(browser_capture.RateLimitError):
                browser_capture.read_page(c,link(0),lambda b:False,'tab')


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
        self.patches=[patch.object(server,'DB',self.data/'test.sqlite3'),patch.object(server,'DATA',self.data),
                      patch.object(shop_listing,'start_category_sync')]
        for p in self.patches:p.start()
        server.init()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def submit(self,n=1):return q.submit(server,{'request_key':uuid.uuid4().hex,'links':'\n'.join(link(i) for i in range(n))})['id']
    def test_start_collects_all_image_groups_information_sales_and_skus_without_category(self):
        import base64
        p=copy.deepcopy(FIXTURE);p['product_id']=t.product_id(link(0))
        image=lambda name:{'url_list':['https://p16.tiktokcdn.com/'+name+'.png']}
        p['images']=[image('main-'+str(i)) for i in range(12)]
        p['sale_props']=[];p['skus'][0]['sku_image']=image('variant')
        p['desc_blocks']=[{'type':'text','text':'Cotton shirt'}, {'type':'image','image':image('detail')}]
        p['size_guide']={'image':image('size-chart')}
        p['specifications']=[{'name':'Material','value':'Cotton'}]
        p['sold_count']='257024';p['sold_count_note']={'text':'全球累计成交件数，不含退货'}
        html='<script type="application/json">'+json.dumps(p)+'</script>'
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        service=q.Service(server,runner=lambda item:t.capture(item['url'],item['product_id'],self.data))
        class Quiet(server.Handler):
            def log_message(self,*args):pass
        httpd=server.ThreadingHTTPServer(('127.0.0.1',0),Quiet)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        try:
            with patch.object(server,'collector',service),patch.object(t,'browser_page',return_value=html), \
                 patch.object(t,'download',side_effect=lambda url,target,*args:target.write_bytes(png)), \
                 patch.object(service,'resolve_category',side_effect=AssertionError('采集不应等待类目接口')):
                service.start()
                try:
                    body=json.dumps({'links':link(0),'request_key':uuid.uuid4().hex}).encode()
                    request=urllib.request.Request('http://127.0.0.1:%s/api/tiktok/jobs'%httpd.server_port,data=body,headers={'Content-Type':'application/json'})
                    with urllib.request.urlopen(request,timeout=5) as response:jid=json.load(response)['id']
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        with server.db() as c:status=q.job_status(c,jid)
                        if status['state']=='finished':break
                        time.sleep(.02)
                    self.assertEqual(status['counts'],{'complete':1})
                finally:service.stop()
        finally:httpd.shutdown();httpd.server_close();thread.join()
        with server.db() as c:
            data=server.product_assets.saved_product_data(c,'US',p['product_id'])
            self.assertEqual(data['saved_images'],15)
            self.assertEqual(data['sales'],{'display':'257024','note':'全球累计成交件数，不含退货'})
            self.assertEqual(data['description_blocks'],[{'type':'text','text':'Cotton shirt'}])
            self.assertEqual(data['specifications'],p['specifications'])
            self.assertEqual(len(data['quality']['variants']),len(p['skus']))
            self.assertEqual(c.execute('SELECT count(*) FROM sku_variants').fetchone()[0],len(p['skus']))
            self.assertEqual({r[0] for r in c.execute('SELECT group_name FROM gallery_items')},{'商品图','规格图','详情图','尺码表'})
            for row in c.execute('SELECT local_path FROM images'):
                self.assertEqual((self.data/row[0].lstrip('/')).read_bytes(),png)

    def test_saved_sales_keep_unknown_zero_and_formatted_values_distinct(self):
        self.submit();service=q.Service(server,runner=result);service.step()
        with server.db() as c:
            for value,display in [(None,''),(0,'0'),('2.1K+','2.1K+')]:
                p=copy.deepcopy(FIXTURE);p['sold_count']=value
                c.execute('UPDATE product_capture SET product_json=?',(json.dumps(p),))
                self.assertEqual(server.product_assets.saved_product_data(c,'US',t.product_id(link(0)))['sales']['display'],display)

    def test_submit_3000_and_idempotency_atomic_validation(self):
        key=uuid.uuid4().hex;body={'request_key':key,'links':'\n'.join(link(i) for i in range(3000))}
        first=q.submit(server,body);second=q.submit(server,body);self.assertEqual(first['id'],second['id'])
        with self.assertRaises(ValueError):q.submit(server,dict(request_key=uuid.uuid4().hex,links=link(0)+' invalid'))
        with server.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM tiktok_items').fetchone()[0],3000)
            self.assertEqual(c.execute('SELECT count(*) FROM batches').fetchone()[0],1)
    def test_100_links_continue_after_failures_and_retry_cap(self):
        jid=self.submit(100);seen=[]
        def run(item):
            seen.append(item['position'])
            if item['position'] in (2,50):raise TimeoutError('test timeout')
            return result(item,complete=item['position']!=70)
        service=q.Service(server,runner=run,pace=0)
        for _ in range(100):self.assertTrue(service.step())
        self.assertEqual(seen,list(range(1,101)))
        with server.db() as c:
            status=q.job_status(c,jid);self.assertEqual(status['counts']['complete'],97);self.assertEqual(status['counts']['retry'],3)
            self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0],98)
        for _ in range(2):
            with server.db() as c:c.execute('UPDATE tiktok_items SET next_at=0')
            for _ in range(3):service.step()
        with server.db() as c:
            status=q.job_status(c,jid);self.assertEqual(status['state'],'finished');self.assertEqual(status['processed'],100)
            self.assertEqual(status['counts']['failed'],2);self.assertEqual(status['counts']['partial'],1)
            self.assertEqual(c.execute('SELECT max(attempts) FROM tiktok_items').fetchone()[0],3)
            self.assertEqual(c.execute('SELECT count(*) FROM snapshots').fetchone()[0],98)
    def test_restart_resume_cancel_and_stale_ack(self):
        jid=self.submit(3);service=q.Service(server,runner=result,pace=0)
        service.step();abandoned=service.claim();service.recover()
        with server.db() as c:
            self.assertEqual(q.job_status(c,jid)['counts'],{'complete':1,'pending':2})
            self.assertEqual(c.execute('SELECT attempts FROM tiktok_items WHERE id=?',(abandoned['id'],)).fetchone()[0],0)
        q.control(server,dict(id=jid,action='pause'));self.assertFalse(service.step())
        q.control(server,dict(id=jid,action='resume'));item=service.claim()
        q.control(server,dict(id=jid,action='cancel'));service.acknowledge(item,result(item))
        with server.db() as c:self.assertEqual(q.job_status(c,jid)['counts'],{'complete':1,'cancelled':2})
        q.control(server,dict(id=jid,action='retry'));service.step();service.step()
        with server.db() as c:self.assertEqual(q.job_status(c,jid)['counts'],{'complete':3})
    def test_delete_task_removes_linked_products_and_keeps_recovery_data(self):
        jid=self.submit(2);service=q.Service(server,runner=result,pace=0)
        service.step();service.step()
        other=q.submit(server,{'request_key':uuid.uuid4().hex,'links':link(8)})['id'];service.step()
        pid=t.product_id(link(0))
        with server.db() as c:
            c.execute('INSERT INTO listing_edits VALUES(?,?,?,?,?)',('US',pid,'Keep edited title','42.00',server.now()))
        deleted=q.control(server,dict(id=jid,action='delete'))
        self.assertEqual(deleted['changed'],2)
        with server.db() as c:
            self.assertEqual([j['id'] for j in q.jobs(c)],[other])
            self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['total'],1)
            self.assertEqual(len(server.listing_trash.items(c)),2)
            self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0],3)
            self.assertEqual(c.execute('SELECT count(*) FROM product_capture').fetchone()[0],3)
            self.assertEqual(c.execute('SELECT title FROM listing_edits WHERE id=?',(pid,)).fetchone()[0],'Keep edited title')
            server.listing_trash.change(c,{'items':[{'market':'US','id':pid}]},True,server.now())
        # An HTTP retry must not delete a product that has since been restored.
        self.assertEqual(q.control(server,dict(id=jid,action='delete'))['changed'],0)
        server.init();service.recover()
        with server.db() as c:
            self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['total'],2)
            self.assertEqual([j['id'] for j in q.jobs(c)],[other])
        for action in ('retry','resume','cancel','pause'):
            with self.assertRaisesRegex(ValueError,'任务已删除'):q.control(server,dict(id=jid,action=action))

    def test_delete_running_task_blocks_late_result_and_releases_queue(self):
        jid=self.submit(3);service=q.Service(server,runner=result,pace=0)
        service.step();late=service.claim()
        service.progress(late,{'stage':'等待验证码','verification':True})
        other=q.submit(server,{'request_key':uuid.uuid4().hex,'links':link(8)})['id']
        self.assertEqual(q.control(server,dict(id=jid,action='delete'))['changed'],1)
        service.acknowledge(late,result(late));service.recover()
        with server.db() as c:
            self.assertEqual(q.job_status(c,jid)['counts'],{'complete':1,'cancelled':2})
            self.assertEqual(c.execute('SELECT count(*) FROM tiktok_items WHERE job_id=? AND (token IS NOT NULL OR verification_hold=1)',(jid,)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0],1)
            self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['total'],0)
        self.assertEqual(service.claim()['job_id'],other)

    def test_delete_empty_task_and_invalid_task(self):
        jid=self.submit()
        self.assertEqual(q.control(server,dict(id=jid,action='delete'))['changed'],0)
        with self.assertRaisesRegex(ValueError,'任务不存在'):q.control(server,dict(id=999,action='delete'))
        with server.db() as c:self.assertEqual(q.jobs(c),[])

    def test_delete_task_resolves_short_links_and_shared_products_once(self):
        service=q.Service(server,runner=result,pace=0)
        first=self.submit();service.step()
        jid=q.submit(server,{'request_key':uuid.uuid4().hex,'links':'https://vt.tiktok.com/abc/'})['id']
        item=service.claim();data=result(dict(item,product_id=t.product_id(link(0))))
        service.acknowledge(item,data)
        self.assertEqual(q.control(server,dict(id=jid,action='delete'))['changed'],1)
        with server.db() as c:
            self.assertEqual([j['id'] for j in q.jobs(c)],[first])
            self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['total'],0)
            self.assertEqual(len(server.listing_trash.items(c)),1)

    def test_delete_task_rolls_back_when_library_change_fails(self):
        jid=self.submit();service=q.Service(server,runner=result,pace=0);service.step()
        original=server.listing_trash.change
        def fail_after_write(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('simulated database error')
        with patch.object(q.listing_trash,'change',side_effect=fail_after_write):
            with self.assertRaises(RuntimeError):q.control(server,dict(id=jid,action='delete'))
        with server.db() as c:
            self.assertEqual(q.job_status(c,jid)['state'],'finished')
            self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['total'],1)
            self.assertEqual(server.listing_trash.items(c),[])

    def test_region_unavailable_is_complete_and_saved_in_library_without_fake_data(self):
        jid=self.submit(2);service=q.Service(server,runner=lambda item:dict(code='regional_unavailable'))
        self.assertTrue(service.step())
        with server.db() as c:
            item=c.execute('SELECT * FROM tiktok_items WHERE job_id=? ORDER BY id',(jid,)).fetchone()
            self.assertEqual((item['state'],item['attempts'],item['error_code']),('complete',1,'regional_unavailable'))
            self.assertEqual(q.job_status(c,jid)['regional_unavailable'],1)
            self.assertIsNone(item['error']);self.assertEqual(item['next_at'],0)
            products=server.result_rows(c,'product',{'draft_filter':['in']})
            self.assertEqual(products['total'],1)
            self.assertEqual(products['items'][0]['collection_availability'],'regional_unavailable')
            self.assertEqual(c.execute('SELECT count(*) FROM product_capture').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM images').fetchone()[0],0)
        self.assertTrue(service.step());self.assertFalse(service.step())
        self.assertEqual(q.control(server,{'id':jid,'action':'delete'})['changed'],2)

    def test_region_unavailable_preserves_existing_capture_and_late_results_stay_deleted(self):
        self.submit();service=q.Service(server,runner=result);service.step()
        with server.db() as c:before=tuple(c.execute('SELECT product_json,audit_json FROM product_capture').fetchone())
        jid=self.submit();item=service.claim();service.acknowledge(item,{'code':'regional_unavailable','verification_seen':True})
        with server.db() as c:
            self.assertEqual(before,tuple(c.execute('SELECT product_json,audit_json FROM product_capture').fetchone()))
            self.assertEqual(q.job_status(c,jid)['counts'],{'complete':1})
        next_job=self.submit();item=service.claim();q.control(server,{'id':next_job,'action':'delete'})
        service.acknowledge(item,{'code':'regional_unavailable'})
        with server.db() as c:self.assertEqual(server.result_rows(c,'product',{'draft_filter':['in']})['total'],0)

    def test_partial_saved_and_manual_edits_preserved(self):
        jid=self.submit();service=q.Service(server,runner=lambda i:result(i,False),pace=0)
        for _ in range(3):
            with server.db() as c:c.execute('UPDATE tiktok_items SET next_at=0')
            service.step()
        pid=t.product_id(link(0))
        with server.db() as c:
            self.assertEqual(c.execute('SELECT metadata_ready FROM product_capture').fetchone()[0],0)
            c.execute('INSERT INTO listing_edits VALUES(?,?,?,?,?)',('US',pid,'Manual title','99.00',server.now()))
        q.control(server,dict(id=jid,action='retry'))
        service.runner=result;service.step()
        with server.db() as c:
            self.assertEqual(q.job_status(c,jid)['counts'],{'complete':1})
            data=server.listing_editor.listing_payload(c,'US',pid)
            self.assertEqual(data['title'],'Manual title');self.assertTrue(data['publishable']);self.assertEqual(len(data['variants']),3)
            self.assertEqual(c.execute('SELECT count(*) FROM snapshots').fetchone()[0],1)
    def test_hung_process_is_killed_and_next_item_can_run(self):
        self.submit(2);service=q.Service(server,pace=0);script=self.data/'hang.py';script.write_text('import time\ntime.sleep(30)')
        with patch.object(t,'__file__',str(script)),patch.object(t,'ITEM_TIMEOUT',0.1),patch.object(service.browser,'ensure',return_value={}):service.step()
        self.assertIsNone(service.process)
        service.runner=result;service.step()
        with server.db() as c:
            states=[r[0] for r in c.execute('SELECT state FROM tiktok_items ORDER BY id')]
            self.assertEqual(c.execute('SELECT error_code FROM tiktok_items ORDER BY id LIMIT 1').fetchone()[0],'timeout')
            self.assertEqual(states,['retry','complete'])
    def test_verification_browser_reuses_session_and_rejects_close(self):
        service=q.Service(server)
        with patch.object(service.browser,'show') as show:
            with self.assertRaisesRegex(ValueError,'开始采集后'):
                service.browser_control('open')
            show.assert_not_called()
            service.browser.endpoint='ws://127.0.0.1:9222/test-session'
            service.browser_state='verification'
            self.assertEqual(service.browser_control('open')['state'],'verification')
            show.assert_called_once_with()
            with self.assertRaisesRegex(ValueError,'请保留采集窗口'):
                service.browser_control('close')
            self.assertEqual(service.browser.endpoint,'ws://127.0.0.1:9222/test-session')

    def test_old_cooldown_is_cleared_on_startup(self):
        jid=self.submit();service=q.Service(server,runner=result)
        with server.db() as c:
            c.execute("UPDATE tiktok_pacing SET next_at=?,reason='平台限流后冷却'",(time.time()+1800,))
            c.execute("UPDATE tiktok_items SET state='retry',error_code='rate_limit',next_at=? WHERE job_id=?",(time.time()+600,jid))
        server.init()
        with server.db() as c:self.assertEqual(q.pacing_status(c)['remaining'],0)
        self.assertTrue(service.step())

    def test_rate_limit_moves_on_without_global_cooldown_and_is_bounded(self):
        jid=self.submit(3);service=q.Service(server,runner=lambda i:dict(error='HTTP 429',code='rate_limit'))
        with patch.object(q.time,'time',return_value=1000):
            for _ in range(9):self.assertTrue(service.step())
            self.assertFalse(service.step())
            with server.db() as c:
                self.assertEqual(q.pacing_status(c)['remaining'],0)
                self.assertEqual(q.job_status(c,jid)['counts'],{'failed':3})
                self.assertEqual(c.execute('SELECT max(attempts) FROM tiktok_items').fetchone()[0],3)

    def test_verification_holds_across_disconnect_and_restart_then_advances_immediately(self):
        self.submit(2);service=q.Service(server,runner=result)
        with patch.object(q.time,'time',return_value=1000):
            item=service.claim();service.progress(item,dict(verification=True,stage='等待验证码'))
            self.assertIsNone(service.claim())
            service.acknowledge(item,dict(error='标签页断开',code='browser'))
            restarted=q.Service(server,runner=result);restarted.recover()
            self.submit()
            current=restarted.claim()
            self.assertEqual(current['id'],item['id'])
            self.assertEqual(current['attempts'],1)
            restarted.acknowledge(current,result(current))
            self.assertEqual(restarted.claim()['position'],2)

    def test_every_failure_type_retries_without_a_timer_or_unbounded_attempts(self):
        for code in ('timeout','network','browser','storage','rate_limit'):
            with self.subTest(code=code):
                jid=self.submit();service=q.Service(server,runner=lambda item:dict(error='test',code=code))
                with patch.object(q.time,'time',return_value=1000):
                    for _ in range(3):self.assertTrue(service.step())
                    self.assertFalse(service.step())
                with server.db() as c:
                    self.assertEqual(q.job_status(c,jid)['counts'],{'failed':1})
                    self.assertEqual(c.execute('SELECT next_at FROM tiktok_items WHERE job_id=?',(jid,)).fetchone()[0],0)

    def test_finished_verification_does_not_cause_an_endless_recovery_loop(self):
        jid=self.submit();service=q.Service(server);item=service.claim()
        service.progress(item,{'verification':True,'stage':'等待验证码'})
        service.progress(item,{'verification':False,'stage':'验证已完成，正在读取商品'})
        service.acknowledge(item,{'code':'timeout','error':'test','verification_seen':True})
        with server.db() as c:
            row=c.execute('SELECT state,verification_hold,attempts,next_at,error_code FROM tiktok_items WHERE job_id=?',(jid,)).fetchone()
            self.assertEqual(tuple(row),('retry',0,1,0,'timeout'))
        self.assertEqual(service.claim()['attempts'],2)

    def test_product_completion_does_not_start_background_category_matching(self):
        self.submit();service=q.Service(server,runner=result)
        with patch.object(shop_listing,'start_category_sync') as sync,patch.object(service,'resolve_category') as resolve:
            self.assertTrue(service.step());sync.assert_not_called();resolve.assert_not_called()

    def test_completed_twentieth_verified_product_has_no_delay(self):
        self.submit(2);service=q.Service(server)
        def verified(item):
            service.progress(item,dict(verification=True,stage='验证码'))
            return dict(result(item),verification_seen=True)
        service.runner=verified
        with server.db() as c:c.execute('UPDATE tiktok_pacing SET count=19')
        with patch.object(q.time,'time',return_value=1000):
            self.assertTrue(service.step())
            with server.db() as c:self.assertEqual(q.pacing_status(c)['remaining'],0)
            self.assertTrue(service.step())

    def test_collection_saves_without_waiting_for_category_apis(self):
        jid=self.submit(2)
        def run(item):
            r=result(item);r['product'].pop('category_is_leaf');return r
        service=q.Service(server,runner=run,pace=0)
        with patch.object(shop_listing,'identity',return_value=({'selected_shop_id':'shop'}, {'region':'US'})), \
             patch.object(shop_listing,'call',return_value={'categories':TREE}) as api:
            service.step();service.step();api.assert_not_called()
        with server.db() as c:
            self.assertEqual(q.job_status(c,jid)['counts'],{'complete':2})
            p=json.loads(c.execute('SELECT product_json FROM product_capture').fetchone()[0])
            self.assertEqual(p['category'],'Toys > Arts & Crafts')
            self.assertNotIn('category_is_leaf',p)

    def test_parent_category_is_complete_even_with_stale_category_audit(self):
        jid=self.submit();service=q.Service(server,pace=0);item=service.claim()
        r=result(item);r['product'].update(category='Toys',category_id='10',category_is_leaf=False)
        r['quality']['missing']=[t.CATEGORY_MISSING]
        service.acknowledge(item,r)
        with server.db() as c:
            self.assertEqual(q.job_status(c,jid)['counts'],{'complete':1})
            self.assertEqual(c.execute('SELECT metadata_ready FROM product_capture').fetchone()[0],1)
            self.assertEqual(json.loads(c.execute('SELECT missing FROM tiktok_items').fetchone()[0]),[])

    def test_unavailable_tree_does_not_block_completion_or_repeat_per_item(self):
        jid=self.submit(2)
        def run(item):
            r=result(item);r['product'].pop('category_is_leaf');return r
        service=q.Service(server,runner=run,pace=0)
        with patch.object(shop_listing,'identity',return_value=({'selected_shop_id':'shop'}, {'region':'US'})), \
             patch.object(shop_listing,'call',side_effect=shop_listing.shop.ShopError('offline')) as api:
            service.step();service.step();api.assert_not_called()
        with server.db() as c:self.assertEqual(q.job_status(c,jid)['counts'],{'complete':2})

    def test_different_market_never_uses_selected_shops_tree(self):
        service=q.Service(server,pace=0);item={'product_id':'123456789','market':'GB'}
        r=result(item);r['product'].pop('category_is_leaf')
        with patch.object(shop_listing,'identity',return_value=({}, {'region':'US'})),patch.object(shop_listing,'call') as api:
            service.resolve_category(item,r);api.assert_not_called()
        self.assertEqual(r['quality']['missing'],[])

    def test_missing_category_completes_without_retry(self):
        jid=self.submit();service=q.Service(server,pace=0);item=service.claim()
        r=result(item)
        for key in ('category','category_id','category_is_leaf'):r['product'].pop(key,None)
        service.acknowledge(item,r)
        with server.db() as c:
            self.assertEqual(q.job_status(c,jid)['state'],'finished')
            self.assertEqual(q.job_status(c,jid)['counts'],{'complete':1})
            self.assertEqual(c.execute('SELECT metadata_ready FROM product_capture').fetchone()[0],1)
        self.assertIsNone(service.claim())

    def test_reclassify_only_requested_saved_category_failures(self):
        jid=self.submit(3);service=q.Service(server,runner=result,pace=0)
        for _ in range(3):service.step()
        other=q.submit(server,{'request_key':uuid.uuid4().hex,'links':link(8)})['id'];service.step()
        with server.db() as c:
            for row in c.execute('SELECT id,job_id,position,market,product_id FROM tiktok_items').fetchall():
                missing=[t.CATEGORY_MISSING]
                if row['job_id']==jid and row['position']==2:missing.append('部分图片未下载')
                if row['job_id']==jid and row['position']==3:
                    # Real network failures must not be marked complete.
                    c.execute("UPDATE tiktok_items SET state='retry',error='limited',error_code='rate_limit' WHERE id=?",(row['id'],))
                    continue
                c.execute("UPDATE tiktok_items SET state='partial',missing=?,error=?,error_code='incomplete' WHERE id=?",(json.dumps(missing),';'.join(missing),row['id']))
                capture=c.execute('SELECT audit_json FROM product_capture WHERE market=? AND product_id=?',(row['market'],row['product_id'])).fetchone()
                audit=json.loads(capture[0]);audit['missing']=missing
                c.execute('UPDATE product_capture SET metadata_ready=0,audit_json=? WHERE market=? AND product_id=?',(json.dumps(audit),row['market'],row['product_id']))
            c.execute("UPDATE tiktok_jobs SET state='running' WHERE id=?",(jid,))
            before=[tuple(r) for r in c.execute('SELECT market,product_id,product_json FROM product_capture ORDER BY product_id')]
        self.assertEqual(q.complete_saved_category_items(server,jid)['changed'],1)
        self.assertEqual(q.complete_saved_category_items(server,jid)['changed'],0)
        with server.db() as c:
            self.assertEqual(q.job_status(c,jid)['counts'],{'complete':1,'partial':1,'retry':1})
            self.assertEqual(q.job_status(c,jid)['state'],'running')
            self.assertEqual(q.job_status(c,other)['counts'],{'partial':1})
            row=c.execute('SELECT * FROM tiktok_items WHERE job_id=? AND position=1',(jid,)).fetchone()
            self.assertIsNone(row['error']);self.assertIsNone(row['error_code']);self.assertEqual(row['missing'],'[]')
            capture=c.execute('SELECT metadata_ready,audit_json FROM product_capture WHERE market=? AND product_id=?',(row['market'],row['product_id'])).fetchone()
            self.assertEqual(capture[0],1);self.assertEqual(json.loads(capture[1])['missing'],[])
            self.assertEqual(before,[tuple(r) for r in c.execute('SELECT market,product_id,product_json FROM product_capture ORDER BY product_id')])
        self.assertEqual(q.complete_saved_category_items(server,other)['changed'],1)
        with server.db() as c:self.assertEqual(q.job_status(c,other)['state'],'finished')

    def test_image_rate_limit_does_not_delay_next_product(self):
        self.submit(2)
        def run(item):
            r=result(item);r['rate_limited']=True;return r
        service=q.Service(server,runner=run)
        with patch.object(q.time,'time',return_value=1000):
            service.step();self.assertTrue(service.step())
            with server.db() as c:self.assertEqual(q.pacing_status(c)['remaining'],0)
    def test_http_submit_status_pagination_and_origin(self):
        class Quiet(server.Handler):
            def log_message(self,*a):pass
        httpd=server.ThreadingHTTPServer(('127.0.0.1',0),Quiet);thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(httpd.server_port)
        def request(path,body=None,origin=None):
            headers={'Content-Type':'application/json'}
            if origin:headers['Origin']=origin
            req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body else None,headers=headers)
            with urllib.request.urlopen(req) as r:return json.load(r)
        try:
            with patch.object(server,'collector',q.Service(server)):
                body=dict(request_key=uuid.uuid4().hex,links='\n'.join(link(i) for i in range(100)))
                d=request('/api/tiktok/jobs',body);self.assertEqual(d['total'],100)
                self.assertEqual(len(request('/api/tiktok/items?id=%s&page=2'%d['id'])['items']),50)
                self.assertEqual(len(request('/api/tiktok/jobs')['jobs']),1)
                with self.assertRaises(urllib.error.HTTPError) as e:request('/api/tiktok/jobs',body,'https://evil.example')
                self.assertEqual(e.exception.code,403)
                deleted=request('/api/tiktok/control',{'id':d['id'],'action':'delete'})
                self.assertEqual(deleted['changed'],0)
                self.assertEqual(request('/api/tiktok/jobs')['jobs'],[])
        finally:httpd.shutdown();httpd.server_close();thread.join()

if __name__=='__main__':unittest.main()
