import copy
import io
import json
import pathlib
import sqlite3
import sys
import tempfile
import types
import unittest
import urllib.error
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import shop_listing as pub
import tiktok_shop as shop
import library_categories
from database import ClosingConnection
real_metadata=pub.metadata
real_local_image=pub.local_image


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=pathlib.Path(self.temp.name)
        def db():
            c=sqlite3.connect(self.data/'test.db',factory=ClosingConnection);c.row_factory=sqlite3.Row;return c
        self.server=types.SimpleNamespace(DATA=self.data,db=db)
        with db() as c:
            pub.migrate(c)
            c.execute('CREATE TABLE product_capture(market TEXT,product_id TEXT,product_json TEXT)')
            c.execute('INSERT INTO product_capture VALUES(?,?,?)',('US','p1',json.dumps({'desc_blocks':[{'type':'text','text':'Cotton shirt'}]})))
        self.ctx={'app_key':'app','service_id':'service','selected_shop_id':'store'}
        self.selected={'id':'store','region':'US','name':'Test store'}
        shop.save(self.data,{**self.ctx,'app_secret':'secret','token':{'access_token':'token','granted_scopes':list(shop.REQUIRED_SCOPES)+['seller.shop.info','seller.global_product.category.info','seller.global_product.info','seller.global_product.write']},'shops':[self.selected]})
        self.p=dict(market='US',id='p1',title='Test shirt',price='20.00',images=[{'key':'img','local_path':'/images/a.jpg','group_name':'商品图'}],variants=[{'id':'sku1','price':'20.00','currency':'USD','attributes':[{'name':'Color','value':'Blue'}]}])
        self.form=dict(brand_id='unbranded',brand_confirmed=True,description='Cotton shirt',category_id='cat',warehouse_id='wh',global_warehouse_id='gwh',weight='0.2',length='20',width='15',height='3',stocks={'sku1':'10'})
        self.meta=dict(mode='global',categories=[{'id':'cat','is_leaf':True,'permission_statuses':['AVAILABLE']}],warehouses=[{'id':'wh'}],global_warehouses=[{'id':'gwh'}],attributes=[],rules={})
        self.patches=[patch.object(pub.listing_editor,'listing_payload',side_effect=lambda *a:copy.deepcopy(self.p)),patch.object(pub.listing_editor,'product',return_value={}),patch.object(pub,'metadata',side_effect=lambda *a,**k:copy.deepcopy(self.meta)),patch.object(pub,'local_image',return_value={'path':'/images/a.jpg','sha256':'abc','ext':'.jpg','width':1200,'height':1200}),patch.object(pub.threading,'Thread')]
        self.patches.extend([patch.object(pub,'shop_brands',return_value=[]),patch.object(pub,'bilingual_categories',side_effect=lambda data,ctx,base:base['categories']),patch.object(pub,'recommend_properties',return_value={}),patch.object(pub.listing_image_roles,'annotate',side_effect=lambda data,images,**kw:[dict(im,ocr_status='checked') for im in images])])
        self.patches.append(patch.object(library_categories,'saved_path',return_value=''))
        for p in self.patches:p.start()
        self.save()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def save(self): pub.post(self.server,'listing-profile',{'market':'US','id':'p1','profile':self.form})
    def check(self): return pub.post(self.server,'listing-check',{'items':[{'market':'US','id':'p1'}]})

    def test_missing_ocr_cache_requests_background_recognition_before_submission(self):
        with patch.object(pub.listing_image_roles,'annotate',side_effect=lambda data,images,**kw:[dict(im,ocr_status='unavailable',ocr_error='需要识别') for im in images]):
            response=self.check()
        self.assertFalse(response['ready'])
        self.assertEqual(response['errors'][0]['issues'][0]['kind'],'image_ocr_pending')
        with self.server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_jobs').fetchone()[0],0)

    def test_preflight_returns_independent_problems_together(self):
        self.form.update(brand_id='',brand_confirmed=False,stocks={'sku1':'0'},weight='',length='',width='',height='')
        self.meta['rules']['size_chart']={'is_required':True}
        self.meta['attributes']=[dict(id='fit',name='Fit',type='PRODUCT_PROPERTY',is_required=True,values=[])]
        self.save();result=self.check()
        kinds={issue['kind'] for issue in result['errors'][0]['issues']}
        self.assertTrue({'brand','sold_out','package','attribute','size_chart'}.issubset(kinds),result)
        with self.server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_jobs').fetchone()[0],0)

    def test_sold_out_exclusion_only_affects_listing_payload_and_is_reversible(self):
        self.p['variants'].append(dict(id='sku2',price='22',currency='USD',attributes=[dict(name='Color',value='Red')]))
        self.form.update(stocks={'sku1':'10','sku2':'0'},excluded_sku_ids=['sku2']);self.save()
        result=self.check();self.assertTrue(result['ready'],result)
        with self.server.db() as c:
            snap=pub.build_snapshot(c,self.data,'US','p1',self.ctx,self.selected,self.meta)
        self.assertEqual(len(snap['skus']),1)
        self.assertEqual(len(self.p['variants']),2)
        self.form['excluded_sku_ids']=[];self.save()
        self.assertTrue(any(x['kind']=='sold_out' for x in self.check()['errors'][0]['issues']))

    def test_reference_inventory_requires_confirmation_and_brand_is_never_inferred(self):
        with self.server.db() as c:c.execute('UPDATE product_capture SET product_json=?',(json.dumps({'skus':[{'sku_id':'sku1','stock':10}],'desc_blocks':[{'type':'text','text':'Cotton shirt'}]}),))
        result=self.check()
        self.assertTrue(any(x['kind']=='inventory_reference' for x in result['errors'][0]['issues']))
        self.form['inventory_verified']={'sku1':'10'};self.save();self.assertTrue(self.check()['ready'])
        with patch.object(pub,'shop_brands',return_value=[dict(id='99',name='Shop brand')]):
            meta=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'include_package':False})
            self.assertIsNone(meta['suggested_brand'])
            self.assertNotIn('brand_id',pub.apply_listing_defaults({},dict(meta,suggested_brand={'id':'99','name':'Shop brand'})))

    def test_duplicate_source_attributes_are_not_last_value_wins(self):
        specs=[dict(name='Material',value='Cotton'),dict(name='Material',value='Polyester')]
        rules=[dict(id='m',name='Material',type='PRODUCT_PROPERTY',is_customizable=True)]
        self.assertEqual(pub.match_source_attributes(specs,rules),{})

    def test_unresolved_parent_returns_only_official_available_descendant_leaves(self):
        self.p.update(title='Shapewear',category='Women Shapewear')
        self.meta['categories']=[dict(id='parent',name='Women Shapewear',parent_id='0',is_leaf=False),
            dict(id='one',name='Body',parent_id='parent',is_leaf=True,permission_statuses=['AVAILABLE']),
            dict(id='two',name='Waist',parent_id='parent',is_leaf=True,permission_statuses=['AVAILABLE']),
            dict(id='private',name='Private',parent_id='parent',is_leaf=True,permission_statuses=['INVITE_ONLY']),
            dict(id='other',name='T-shirts',parent_id='0',is_leaf=True,permission_statuses=['AVAILABLE'])]
        with patch.object(pub,'recommend_category',return_value=('','')),patch.object(pub,'refine_source_category',return_value=''):
            meta=pub.listing_metadata(self.server,self.ctx,{'item':dict(market='US',id='p1'),'include_package':False})
        self.assertEqual(meta['category_id'],'')
        self.assertEqual([c['id'] for c in meta['category_candidates']],['one','two'])
        self.assertEqual(meta['category_candidates'][0]['path'],'Women Shapewear > Body')

    def test_detected_size_chart_candidates_keep_dimensions_and_require_confirmation(self):
        self.p['images'].append(dict(key='chart',local_path='/images/chart.jpg',role='size_chart',group_name='商品图'))
        output=pub.prepare_output(self.data,copy.deepcopy(self.p),dict(title='Test shirt',description='Cotton shirt'),{})
        self.assertEqual(output['size_chart_candidates'][0]['width'],1200)
        self.assertTrue(output['size_chart_candidates'][0]['valid'])
        self.assertNotIn('chart',output['profile']['main_image_keys'])
        self.assertFalse(output['profile'].get('size_chart'))
        with patch.object(pub,'local_image',return_value=dict(width=800,height=1200)):
            output=pub.prepare_output(self.data,copy.deepcopy(self.p),dict(title='Test shirt',description='Cotton shirt'),{})
        self.assertFalse(output['size_chart_candidates'][0]['valid'])

    def test_company_template_for_tshirt_cannot_be_used_for_shapewear(self):
        with self.server.db() as c:c.execute('INSERT INTO shop_size_templates VALUES(?,?,?,?)',('tee','T-shirt',json.dumps({'local_path':'/images/chart.jpg','product_type':'tshirt'}),0))
        self.p['title']='Waist trainer shapewear';self.form['size_chart_template_id']='tee';self.save()
        self.assertIn('适用类型',self.check()['errors'][0]['error'])
    def submit(self,check=None):
        return pub.post(self.server,'listing-submit',{'items':[{'market':'US','id':'p1'}],'fingerprint':(check or self.check())['fingerprint']})
    def job(self):
        with self.server.db() as c:return dict(c.execute('SELECT * FROM shop_listing_jobs').fetchone())
    def mock_api(self,path_status='SUCCESS',uncertain=False):
        def api(data,ctx,path,**kw):
            if path.endswith('/listing_check'):return {'check_result':'PASS'}
            if path.endswith('global_products'):
                if uncertain:raise shop.UncertainError('timeout')
                return {'global_product_id':'global1','global_skus':[{'id':'gsku1','seller_sku':kw['payload']['skus'][0]['seller_sku']}]}
            if path.endswith('/publish'):
                return {'publish_result':[{'region':'US','status':path_status}], 'products':[{'region':'US','shop_id':'store','id':'remote1'}] if path_status!='FAILED' else []}
            if path.endswith('/products'):return {'product_id':'remote1'}
            return {'status':'ACTIVATE'}
        return api

    def run_job(self,**kw):
        with patch.object(pub,'upload',return_value={'uri':'uri1'}),patch.object(pub,'call',side_effect=self.mock_api(**kw)) as api:
            pub.run_one(self.server,self.job());return api.call_args_list

    def test_global_workflow_prices_and_inventory_and_duplicate_guard(self):
        self.submit();calls=self.run_job();self.assertEqual(len(calls),2)
        created=calls[0].kwargs['payload'];published=calls[1].kwargs['payload']
        self.assertEqual(created['skus'][0]['inventory'],[{'global_warehouse_id':'gwh','quantity':10}])
        self.assertEqual(published['publish_target'][0]['skus'][0]['price'],{'sale_price':'20.00','currency':'USD'})
        self.assertEqual(self.job()['state'],'submitted');self.assertEqual(self.job()['remote_id'],'remote1')
        with self.assertRaisesRegex(shop.ShopError,'已有发布记录'): self.submit()

    def test_ocr_issue_links_to_exact_image_and_clears_after_replacement(self):
        self.p['shop_name']='Example House'
        self.p['images'][0]['ocr_text']='Example House'
        checked=self.check()
        self.assertFalse(checked['ready'])
        self.assertEqual(checked['checked'],[])
        error=checked['errors'][0]
        self.assertEqual((error['market'],error['id']),('US','p1'))
        self.assertEqual(error['issues'][0]['image_key'],'img')
        self.assertEqual(error['issues'][0]['section'],'publish-photos')
        self.assertEqual(error['issues'][0]['label'],'商品图 · 第 1 张')
        self.assertIn('Example House',error['issues'][0]['message'])
        self.assertNotIn('img',error['error'])
        self.p['images'][0]['ocr_text']='Cotton fabric'
        checked=self.check()
        self.assertTrue(checked['ready'],checked)
        self.assertEqual(checked['checked'],[{'market':'US','id':'p1'}])
        with self.server.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_jobs').fetchone()[0],0)

    def test_partial_batch_returns_only_verified_products_as_checked(self):
        with self.server.db() as c:
            c.execute('INSERT INTO product_capture VALUES(?,?,?)',('US','p2',json.dumps({'desc_blocks':[{'type':'text','text':'Cotton shirt'}]})))
        pub.post(self.server,'listing-profile',{'market':'US','id':'p2','profile':self.form})
        original=pub.build_snapshot
        def snapshot(c,data,market,eid,*args):
            if eid=='p2':raise shop.ShopError('SKU 库存须大于零')
            return original(c,data,market,eid,*args)
        with patch.object(pub,'build_snapshot',side_effect=snapshot):
            result=pub.post(self.server,'listing-check',{'items':[{'market':'US','id':eid} for eid in ('p1','p2')]})
        self.assertFalse(result['ready'])
        self.assertEqual(result['checked'],[{'market':'US','id':'p1'}])
        self.assertEqual(result['errors'][0]['id'],'p2')
        self.assertEqual(result['errors'][0]['issues'],[])

    def test_excluded_ocr_image_passes_and_is_not_reused_for_sku(self):
        self.p['shop_name']='Example House'
        self.p['images'].append({'key':'excluded','local_path':'/images/excluded.jpg','group_name':'商品图','ocr_text':'Example House'})
        self.p['variants'][0]['option_keys']=['blue']
        self.p['option_groups']=[{'name':'Color','options':[{'key':'blue','image':'/images/excluded.jpg'}]}]
        self.form.update(main_image_keys=['img'],description_image_keys=[])
        self.save()
        checked=self.check()
        self.assertTrue(checked['ready'],checked)
        reviews=checked['items'][0]['image_reviews']
        self.assertEqual(next(i for i in reviews if i['key']=='excluded')['text_matches'][0]['name'],'Example House')
        with self.server.db() as c:
            snap=pub.build_snapshot(c,self.data,'US','p1',self.ctx,self.selected,self.meta)
            self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_jobs').fetchone()[0],0)
        self.assertNotIn('attribute_file',snap['skus'][0])
        self.assertNotIn('/images/excluded.jpg',json.dumps(snap))
        self.p['option_groups'][0]['options'][0]['image']='/images/a.jpg'
        with self.server.db() as c:
            snap=pub.build_snapshot(c,self.data,'US','p1',self.ctx,self.selected,self.meta)
        self.assertIn('attribute_file',snap['skus'][0])

    def test_ocr_image_is_blocked_in_every_selected_upload_role(self):
        self.p['shop_name']='Example House'
        self.p['images'].append({'key':'flagged','local_path':'/images/flagged.jpg','group_name':'商品图','ocr_text':'Example House'})
        self.meta['rules']['product_certifications']=[{'id':'label','name':'Label'}]
        original=copy.deepcopy(self.form)
        for selection in ({'main_image_keys':['img','flagged']},{'description_image_keys':['flagged']},
                          {'size_chart':'flagged'},{'certifications':{'label':'flagged'}}):
            with self.subTest(selection=selection):
                self.form={**original,'main_image_keys':['img'],'description_image_keys':[],**selection}
                self.save()
                checked=self.check()
                self.assertFalse(checked['ready'])
                self.assertEqual(checked['errors'][0]['issues'][0]['image_key'],'flagged')

    def test_excluding_ocr_image_does_not_bypass_inventory_check(self):
        self.p['shop_name']='Example House'
        self.p['images'].append({'key':'excluded','local_path':'/images/excluded.jpg','ocr_text':'Example House'})
        self.form.update(main_image_keys=['img'],description_image_keys=[],stocks={'sku1':'0'})
        self.save()
        checked=self.check()
        self.assertFalse(checked['ready'])
        self.assertIn('SKU 库存',checked['errors'][0]['error'])

    def test_competitor_description_replaces_saved_translation_and_reaches_payload(self):
        self.form.update(description='Old translated description',description_zh='旧中文',translated_source='未完成')
        with self.server.db() as c:
            c.execute('INSERT OR REPLACE INTO shop_listing_profiles VALUES(?,?,?,?)',('US','p1',pub.dump(self.form),0))
        prepared=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]['profile']
        self.assertEqual(prepared['description'],'Cotton shirt')
        self.assertEqual(prepared['previous_copy']['description_zh'],'旧中文')
        self.assertEqual(prepared['description_input'],'Cotton shirt')
        self.assertNotIn('translated_source',prepared)
        self.save()
        self.submit();calls=self.run_job()
        self.assertEqual(calls[0].kwargs['payload']['description'],'<p>Cotton shirt</p>')

    def test_legacy_highlights_are_ignored_in_platform_description(self):
        self.form.update(description_zh='中文详情',description_en='English details',
                         highlights_zh='中文亮点',highlights_en='English highlight',
                         translation_source='Test shirt\nCotton shirt',
                         translation_input='Test shirt\n中文详情\n中文亮点')
        self.save();self.submit();calls=self.run_job()
        self.assertEqual(calls[0].kwargs['payload']['description'],
                         '<p>Cotton shirt</p>')

    def test_source_english_replaces_old_chinese_draft_without_model_calls(self):
        self.p['title']='旧的中文标题'
        self.form.update(content_language='auto',editor_language='zh',title_input='运营修改的中文',
                         description_input='中文详情',title_en='Old AI translation',description_en='Old AI details')
        with self.server.db() as c:
            c.execute('UPDATE product_capture SET product_json=?',
                      (json.dumps({'title':'Original cotton shirt','desc_blocks':[{'type':'text','text':'Original English details.'}]}),))
        self.save()
        with patch.object(pub.model_manager,'resolve',side_effect=AssertionError('AI must not be used')),patch.object(pub,'TTAdapter') as adapter:
            prepared=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]['profile']
            checked=self.check()
            self.assertTrue(checked['ready'],checked)
            self.submit(checked);calls=self.run_job()
            adapter.assert_not_called()
        self.assertEqual(prepared['title_input'],'Original cotton shirt')
        self.assertEqual(prepared['description_input'],'Original English details.')
        self.assertEqual(prepared['previous_copy']['title_input'],'运营修改的中文')
        self.assertEqual(calls[0].kwargs['payload']['title'],'Original cotton shirt')
        self.assertEqual(calls[0].kwargs['payload']['description'],'<p>Original English details.</p>')
        with self.server.db() as c:
            self.assertEqual(pub.profile(c,'US','p1')['previous_copy']['description_input'],'中文详情')

    def test_manual_english_changes_persist_and_reach_platform_verbatim(self):
        title="Men's Cotton T-Shirt #Summer (2-Pack)"
        description='100% cotton.\nKeep this line & punctuation.'
        self.form.update(content_language='en',editor_language='en',title_input=title,description_input=description,
                         title_en='Stale title',description_en='Stale description')
        self.save()
        with patch.object(pub.model_manager,'resolve',side_effect=AssertionError('AI must not be used')),patch.object(pub,'TTAdapter') as adapter:
            self.submit();calls=self.run_job();adapter.assert_not_called()
        self.assertEqual(calls[0].kwargs['payload']['title'],title)
        self.assertEqual(calls[0].kwargs['payload']['description'],'<p>100% cotton.</p><p>Keep this line &amp; punctuation.</p>')
        prepared=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]['profile']
        self.assertEqual(prepared['title_input'],title)
        self.assertEqual(prepared['description_input'],description)

    def test_english_entered_in_legacy_auto_form_is_preserved(self):
        self.form.update(content_language='auto',title_input='Manually edited English shirt',
                         description_input='Manually edited English details.')
        self.save()
        self.assertTrue(self.check()['ready'])
        prepared=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]['profile']
        self.assertEqual(prepared['title_input'],self.form['title_input'])
        self.assertEqual(prepared['description_input'],self.form['description_input'])
        self.assertEqual(prepared['content_language'],'en')

    def test_explicit_english_form_is_not_reset_when_cleared(self):
        self.form.update(content_language='en',editor_language='en',title_input='',description_input='')
        self.save()
        prepared=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]['profile']
        self.assertEqual(prepared['title_input'],'');self.assertEqual(prepared['description_input'],'')
        self.assertFalse(self.check()['ready'])

    def test_non_latin_copy_blocks_check_and_direct_submit_without_translation(self):
        self.form.update(content_language='en',editor_language='en',title_input='Cotton shirt',description_input='Cotton details.')
        self.save();checked=self.check();self.assertTrue(checked['ready'])
        for key,text in (('title_input','中文标题'),('description_input','Cotton 上衣'),
                         ('description_input','綿のシャツ'),('description_input','Хлопковая рубашка')):
            with self.subTest(key=key,text=text),patch.object(pub,'TTAdapter') as adapter:
                edited=dict(self.form);edited[key]=text
                pub.post(self.server,'listing-profile',{'market':'US','id':'p1','profile':edited})
                for result in (self.check(),self.submit(checked)):
                    self.assertFalse(result['ready'])
                    self.assertIn('非英文文字',result['errors'][0]['error'])
                    self.assertEqual(result['errors'][0]['issues'][0]['section'],'publish-'+key.removesuffix('_input'))
                adapter.assert_not_called()
        with self.server.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_jobs').fetchone()[0],0)

    def test_english_symbols_and_latin_brand_names_are_allowed(self):
        self.assertFalse(pub.contains_non_latin_text("Pokémon T-Shirt • 100% cotton — 2× XL ✨"))
        for text in ('上衣','シャツ','рубашка'):
            self.assertTrue(pub.contains_non_latin_text(text))

    def test_size_charts_are_excluded_from_new_and_saved_image_assignments(self):
        self.p['images']=[{'key':f'img{i}','local_path':f'/images/{i}.jpg','group_name':'商品图','role':'size_chart' if i in (2,10) else 'product'} for i in range(19)]
        for saved in (False,True):
            with self.subTest(saved=saved):
                if saved:
                    self.form.update(main_image_keys=[f'img{i}' for i in range(9)],description_image_keys=[f'img{i}' for i in range(9,19)])
                self.save()
                form=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]['profile']
                self.assertFalse({'img2','img10'} & set(form['main_image_keys']+form['description_image_keys']))
                self.assertEqual(len(form['main_image_keys'])+len(form['description_image_keys']),17)
                self.assertFalse(form.get('size_chart'))

    def test_nineteen_images_save_as_nine_main_and_ten_description_images(self):
        self.p['images']=[{'key':f'img{i}','local_path':f'/images/{i}.jpg','group_name':'商品图'} for i in range(19)]
        self.form.update(main_image_keys=[f'img{i}' for i in range(9)],description_image_keys=[f'img{i}' for i in range(9,19)])
        self.save()
        prepared=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]['profile']
        self.assertEqual(prepared['description_image_keys'],self.form['description_image_keys'])
        def image(data,item,**kwargs):
            return {'path':item['local_path'],'sha256':item['local_path'],'ext':'.jpg','width':1200,'height':1600}
        def uploaded(data,ctx,file,use_case):
            return {'url':'https://p-oec-va.ibyteimg.com'+file['path']+'?a=1&b=2'} if use_case=='DESCRIPTION_IMAGE' else {'uri':file['path']}
        for mode in ('local','global'):
            with self.subTest(mode=mode),patch.object(pub,'local_image',side_effect=image),patch.object(pub,'upload',side_effect=uploaded) as upload:
                with self.server.db() as c:snap=pub.build_snapshot(c,self.data,'US','p1',self.ctx,self.selected,{**self.meta,'mode':mode})
                payload=pub.create_payload(self.data,snap)
                self.assertEqual(len(payload['main_images']),9)
                self.assertEqual(len(snap['description_images']),10)
                self.assertTrue(payload['description'].startswith('<p>Cotton shirt</p>'))
                self.assertEqual(payload['description'].count('<img '),10)
                self.assertIn('width="1200" height="1600"',payload['description'])
                self.assertIn('?a=1&amp;b=2',payload['description'])
                self.assertLess(payload['description'].index('/9.jpg'),payload['description'].index('/18.jpg'))
                self.assertEqual([call.args[3] for call in upload.call_args_list],['MAIN_IMAGE']*9+['DESCRIPTION_IMAGE']*10)

    def test_prepare_automatically_assigns_remaining_images_to_description(self):
        self.p['images']=[{'key':f'img{i}','local_path':f'/images/{i}.jpg','group_name':'商品图'} for i in range(19)]
        item=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]
        self.assertEqual(item['profile']['main_image_keys'],[f'img{i}' for i in range(9)])
        self.assertEqual(item['profile']['description_image_keys'],[f'img{i}' for i in range(9,19)])
        self.form=item['profile'];self.save()
        reopened=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]['profile']
        self.assertEqual(reopened['description_image_keys'],self.form['description_image_keys'])
        self.form={'main_image_keys':[f'img{i}' for i in range(1,10)]};self.save()
        existing=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]['profile']
        self.assertEqual(existing['main_image_keys'],self.form['main_image_keys'])
        self.assertEqual(existing['description_image_keys'],['img0']+[f'img{i}' for i in range(10,19)])

    def test_automatic_description_images_preserve_manual_choices_including_empty(self):
        self.p['images'] += [{'key':f'detail{i}','group_name':'商品图'} for i in range(3)]
        for keys in ([],['detail1']):
            with self.subTest(keys=keys):
                self.form.update(main_image_keys=['img'],description_image_keys=keys);self.save()
                prepared=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]['profile']
                self.assertEqual(prepared['description_image_keys'],keys)

    def test_automatic_description_images_limit_and_hidden_images(self):
        product={'variants':[], 'images':[{'key':f'img{i}','group_name':'商品图'} for i in range(45)]}
        product['images'][9]['hidden']=True
        form=pub.reuse_source_fields({'main_image_keys':[f'img{i}' for i in range(9)],'imageEdits':{'img10':{'hidden':True}}},
                                     {'description':'Test'},product)
        self.assertEqual(form['description_image_keys'],[f'img{i}' for i in range(11,41)])

    def test_invalid_description_image_selection_blocks_submission(self):
        self.p['images'] += [{'key':f'detail{i}','local_path':f'/images/{i}.jpg','group_name':'商品图'} for i in range(31)]
        self.form['main_image_keys']=['img']
        for keys,reason in [(['missing'],'已被移除'),(['img'],'一种用途'),(['detail0','detail0'],'不能重复'),
                            ([f'detail{i}' for i in range(31)],'30 张'),('detail0','格式错误'),([{}],'格式错误')]:
            with self.subTest(keys=keys):
                self.form['description_image_keys']=keys;self.save()
                result=self.check();self.assertFalse(result['ready']);self.assertIn(reason,result['errors'][0]['error'])

    def test_description_selection_and_image_changes_invalidate_review(self):
        self.p['images'] += [{'key':'detail','local_path':'/images/detail.jpg','group_name':'商品图'}]
        self.form.update(main_image_keys=['img'],description_image_keys=['detail']);self.save()
        checked=self.check()
        self.form['description_image_keys']=[];self.save()
        with self.assertRaisesRegex(shop.ShopError,'已改变'):self.submit(checked)
        self.form['description_image_keys']=['detail'];self.save()
        def image(data,item,**kwargs):
            return {'path':item['local_path'],'sha256':'changed' if kwargs.get('description') else 'abc','ext':'.jpg','width':1200,'height':1200}
        with patch.object(pub,'local_image',side_effect=image):
            with self.assertRaisesRegex(shop.ShopError,'已改变'):self.submit(checked)

    def test_description_html_limit_stops_job_before_product_creation(self):
        self.p['images'].append({'key':'detail','local_path':'/images/detail.jpg','group_name':'商品图'})
        self.form.update(main_image_keys=['img'],description_image_keys=['detail']);self.save();self.submit()
        def uploaded(data,ctx,file,use_case):
            return {'url':'https://p-oec-va.ibyteimg.com/'+'a'*10000} if use_case=='DESCRIPTION_IMAGE' else {'uri':'main'}
        with patch.object(pub,'upload',side_effect=uploaded),patch.object(pub,'call') as api:
            pub.run_one(self.server,self.job());api.assert_not_called()
        self.assertEqual(self.job()['state'],'failed');self.assertIn('10000',self.job()['message'])

    def test_description_upload_uses_platform_url_and_checks_file_integrity(self):
        content=b'test-image';(self.data/'images').mkdir(exist_ok=True);(self.data/'images/a.jpg').write_bytes(content)
        file={'path':'/images/a.jpg','sha256':pub.hashlib.sha256(content).hexdigest(),'ext':'.jpg','width':1200,'height':1200}
        with patch.object(pub,'call',return_value={'uri':'platform-uri','url':'https://p-oec-va.ibyteimg.com/test\n'}) as api:
            self.assertEqual(pub.upload(self.data,self.ctx,file,'DESCRIPTION_IMAGE'),{'url':'https://p-oec-va.ibyteimg.com/test'})
            self.assertIn(b'DESCRIPTION_IMAGE',api.call_args.kwargs['binary'])
            self.assertEqual(pub.upload(self.data,self.ctx,file,'MAIN_IMAGE'),{'uri':'platform-uri'})
        for url in ('','/images/a.jpg','javascript:alert(1)','https://user:password@example.com/image'):
            with self.subTest(url=url),patch.object(pub,'call',return_value={'uri':'uri','url':url}):
                with self.assertRaisesRegex(shop.ShopError,'有效图片地址'):pub.upload(self.data,self.ctx,file,'DESCRIPTION_IMAGE')
        (self.data/'images/a.jpg').write_bytes(b'changed')
        with patch.object(pub,'call') as api:
            with self.assertRaisesRegex(shop.ShopError,'图片内容已改变'):pub.upload(self.data,self.ctx,file,'DESCRIPTION_IMAGE')
            api.assert_not_called()

    def test_description_image_dimensions_allow_small_images_but_reject_4000(self):
        from PIL import Image
        (self.data/'images').mkdir(exist_ok=True)
        Image.new('RGB',(200,300)).save(self.data/'images/detail.png')
        item={'local_path':'/images/detail.png'}
        self.assertEqual(real_local_image(self.data,item,description=True)['width'],200)
        with self.assertRaises(shop.ShopError):real_local_image(self.data,item)
        Image.new('RGB',(4000,300)).save(self.data/'images/detail.png')
        with self.assertRaisesRegex(shop.ShopError,'详情图片宽高'):real_local_image(self.data,item,description=True)

    def test_sku_upgrade_preserves_remote_uncertain_and_manual_identities(self):
        with self.server.db() as c:
            for index,pid in enumerate(('fresh','failed','remote','history','uncertain','queued','linked','manual','custom')):
                mapping=pub.sku_manager.ensure_product(c,'US',pid,source={'skus':[{'sku_id':pid}]})
                vid=mapping['rows'][0]['variant_id'];old=f'TK-20261119-{index:06d}'
                c.execute('UPDATE sku_variants SET seller_sku=? WHERE variant_id=?',(old,vid))
                if pid in ('failed','remote','history','uncertain','queued'):
                    table='shop_listing_job_history' if pid=='history' else 'shop_listing_jobs'
                    state=pid if pid in ('uncertain','queued') else 'failed'
                    c.execute(f'INSERT INTO {table}(id,shop_id,market,product_id,state,remote_id,global_response) VALUES(?,?,?,?,?,?,?)',
                              (pid,'store','US',pid,state,'remote1' if pid=='remote' else '', '{"global_product_id":"g1"}' if pid=='history' else ''))
                if pid=='linked':pub.sku_manager.record_platform_links(c,'store','remote1',[{'seller_sku':old,'id':'remoteSku'}])
                if pid=='manual':c.execute('INSERT INTO sku_change_log(variant_id,old_sku,new_sku,action,changed_at) VALUES(?,?,?,?,?)',(vid,'MANUAL-OLD',old,'manual_edit','now'))
                if pid=='custom':c.execute("UPDATE sku_variants SET seller_sku='CUSTOM-SKU' WHERE variant_id=?",(vid,))
            self.assertEqual(pub.upgrade_unpublished_skus(c),2)
            upgraded=c.execute("SELECT v.source_variant_id,l.old_sku,l.new_sku FROM sku_change_log l JOIN sku_variants v ON v.variant_id=l.variant_id WHERE l.action='upgrade_unpublished'").fetchall()
            self.assertEqual({r[0] for r in upgraded},{'fresh','failed'})
            self.assertTrue(all(r[2].startswith(r[1]+'-') and len(r[2])==35 for r in upgraded))
            self.assertEqual(pub.upgrade_unpublished_skus(c),0)
            old=next(row[1] for row in upgraded if row[0]=='fresh')
            with self.assertRaisesRegex(ValueError,'本地 SKU 已升级'):
                pub.sku_manager.apply_skus(c,'US','fresh',{'fresh':old})

    def test_missing_captured_description_cannot_use_stale_translation(self):
        with self.server.db() as c:c.execute('DELETE FROM product_capture')
        self.form['description']='Old translation';self.save()
        result=self.check()
        self.assertFalse(result['ready'])
        self.assertIn('未采集到竞品描述',result['errors'][0]['error'])

    def test_compliance_declarations_are_required_and_default_to_official_no(self):
        self.form['category_id']='901776';self.save()
        self.meta['categories']=[{'id':'901776','is_leaf':True,'permission_statuses':['AVAILABLE']}]
        self.meta['attributes']=[{'id':aid,'name':'CA prop 65','type':'PRODUCT_PROPERTY',
                                 'is_requried':False,'values':[{'id':aid+'-yes','name':'Yes','is_default':True},{'id':aid+'-no','name':'No'}]}
                                for aid in ('101395','101400')]
        result=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'category_id':'901776','include_package':False})
        self.assertTrue(all(pub.required(a) for a in result['attributes']))
        expected={aid:[{'id':aid+'-no'}] for aid in ('101395','101400')}
        self.assertEqual(result['suggested_attributes'],expected)
        self.assertFalse(result['rules'].get('size_chart',{}).get('is_required'))
        checked=self.check();self.assertTrue(checked['ready'],checked)
        with self.server.db() as c:
            snap=pub.build_snapshot(c,self.data,'US','p1',self.ctx,self.selected,self.meta)
        self.assertEqual(snap['payload']['product_attributes'],[{'id':aid,'values':values} for aid,values in expected.items()])
        self.assertEqual(checked['items'][0]['profile']['attributes'],expected)

    def test_compliance_defaults_preserve_manual_yes_and_other_optional_attributes(self):
        rules=[{'id':aid,'name':'CA prop 65','type':'PRODUCT_PROPERTY','values':[{'id':'yes','name':'Yes'},{'id':'no','name':'否'}]}
               for aid in ('101395','101400')]
        rules.append({'id':'101619','name':'Hazardous materials','type':'PRODUCT_PROPERTY','values':[{'id':'no','name':'No'}]})
        form={'category_id':'cat','attributes':{'101395':[{'id':'yes'}],'101400':[]}}
        result=pub.apply_listing_defaults(form,{**self.meta,'category_id':'cat','attributes':rules})
        self.assertEqual(result['attributes'],{'101395':[{'id':'yes'}],'101400':[{'id':'no'}]})
        self.assertFalse(pub.required(rules[2]))
        self.assertEqual(form['attributes']['101400'],[])

    def test_missing_official_no_choice_is_not_guessed_and_blocks_submission(self):
        self.meta['attributes']=[{'id':'101395','name':'CA Prop 65: Reprotoxic chemicals','type':'PRODUCT_PROPERTY',
                                 'values':[{'id':'yes','name':'Yes','is_default':True}]}]
        checked=self.check()
        self.assertFalse(checked['ready'])
        self.assertIn('生殖毒性',checked['errors'][0]['error'])
        self.assertFalse(checked['items'][0]['profile']['attributes'].get('101395'))

    def test_submit_rechecks_required_compliance_after_clearing_saved_value(self):
        self.meta['attributes']=[{'id':'101400','name':'CA Prop 65: Carcinogen','type':'PRODUCT_PROPERTY','values':[{'id':'no','name':'No'}]}]
        checked=self.check()
        self.assertTrue(checked['ready'],checked)
        self.form['attributes']={};self.save()
        result=self.submit(checked)
        self.assertFalse(result['ready'])
        self.assertIn('致癌',result['errors'][0]['error'])

    def test_list_description_is_reused_once_including_nested_content(self):
        source={'desc_blocks':[{'type':'image','image':{}},
                {'type':'ul','content':['Cotton blend',{'text':'Crew neck'}],
                 'rich_content':[{'text':'Cotton blend'}]},
                {'type':'ol','content':['Wash cold']}]}
        with self.server.db() as c:
            c.execute('UPDATE product_capture SET product_json=?',(json.dumps(source),))
            details=pub.source_details(c,'US','p1')
        self.assertEqual(details['description'],'Cotton blend\n\nCrew neck\n\nWash cold')
        self.assertEqual(details['description_blocks'],3)
        self.assertEqual(details['description_source'],'text')
        self.assertTrue(self.check()['ready'])

    def test_image_only_description_reuses_explicit_product_facts_only(self):
        source={'desc_blocks':[{'type':'image','image':{}}], 'specifications':[
            {'name':'Material','value':'Polyester:100%'},
            {'name':'Closure type','value':'Zipper'},
            {'name':'Dangerous goods','value':'No'},
            {'name':'CA prop 65','value':'No'}]}
        with self.server.db() as c:
            c.execute('UPDATE product_capture SET product_json=?',(json.dumps(source),))
            details=pub.source_details(c,'US','p1')
        self.assertEqual(details['description'],'Material: Polyester:100%\n\nClosure type: Zipper')
        self.assertEqual(details['description_source'],'specifications')
        self.assertTrue(self.check()['ready'])

    def test_edited_specification_and_sku_code_reach_platform_payload(self):
        self.form.update(sku_attributes={'sku1':[{'name':'Color','value':'Navy'}]},seller_skus={'sku1':'MY-NAVY'},identifiers={'sku1':{'type':'UPC','code':'012345678905'}})
        self.save();self.submit();calls=self.run_job();sku=calls[0].kwargs['payload']['skus'][0]
        self.assertEqual(sku['sales_attributes'],[{'name':'Color','value_name':'Navy'}])
        self.assertEqual(sku['seller_sku'],'MY-NAVY')
        self.assertEqual(sku['identifier_code'],{'type':'UPC','code':'012345678905'})
        self.form['sku_attributes']['sku1'][0]['value']='';self.save();self.assertFalse(self.check()['ready'])

    def test_removed_translation_endpoint_never_calls_model(self):
        with patch.object(pub,'TTAdapter') as adapter:
            for action in ('listing-translate','listing-translate-preview'):
                with self.assertRaisesRegex(shop.ShopError,'自动翻译已移除'):
                    pub.post(self.server,action,{'text':'蓝色棉上衣'})
            adapter.assert_not_called()

    def test_old_size_render_and_small_main_image_block_submission(self):
        self.p['images'].append({'key':'chart','local_path':'/images/chart.jpg','group_name':'尺码表'})
        table={'columns':['Size','Bust(in)'],'rows':[['S','44.8']]}
        self.form.update(size_chart='chart',size_table=table,size_table_title='Size chart',size_table_rendered={'image_key':'chart','title':'Size chart','table':copy.deepcopy(table)})
        self.form['size_table']['rows'][0][1]='45.0';self.save()
        self.assertIn('重新生成',self.check()['errors'][0]['error'])
        self.form.pop('size_table_rendered');self.save()
        with patch.object(pub,'local_image',return_value={'path':'/images/a.jpg','sha256':'abc','ext':'.jpg','width':500,'height':500}):
            self.assertIn('600',self.check()['errors'][0]['error'])

    def test_draft_is_not_active(self):
        self.submit();self.run_job(path_status='DRAFT');self.assertEqual(self.job()['state'],'draft')

    def test_timeout_blocks_recreate(self):
        self.submit();self.run_job(uncertain=True);self.assertEqual(self.job()['state'],'uncertain')
        with self.assertRaises(shop.ShopError):self.submit()

    def test_retry_publish_reuses_global_product(self):
        self.submit();self.run_job(path_status='FAILED');self.assertTrue(self.job()['global_response'])
        self.submit();calls=self.run_job();self.assertEqual(len(calls),1);self.assertTrue(calls[0].args[2].endswith('/global1/publish'))

    def test_changed_global_snapshot_cannot_create_again(self):
        self.submit();self.run_job(path_status='FAILED');self.form['weight']='0.3';self.save()
        with self.assertRaisesRegex(shop.ShopError,'已经创建'):self.submit()

    def test_source_or_stock_change_invalidates_review(self):
        checked=self.check();self.form['stocks']['sku1']='5';self.save()
        with self.assertRaisesRegex(shop.ShopError,'已改变'):self.submit(checked)

    def test_metadata_order_does_not_invalidate_review(self):
        self.meta['attributes']=[{'id':aid,'name':aid,'type':'PRODUCT_PROPERTY','is_customizable':True} for aid in ('material','fit')]
        self.meta['rules']['product_certifications']=[{'id':cid,'name':cid} for cid in ('safety','label')]
        self.form.update(attributes={'material':[{'name':'Cotton'}],'fit':[{'name':'Loose'}]},certifications={'safety':'img','label':'img'})
        self.save();checked=self.check()
        self.meta['attributes'].reverse()
        self.meta['rules']['product_certifications'].reverse()
        self.assertEqual(checked['fingerprint'],self.check()['fingerprint'])
        self.assertTrue(self.submit(checked)['ready'])

    def test_attribute_value_change_still_invalidates_review(self):
        self.meta['attributes']=[{'id':'material','name':'Material','type':'PRODUCT_PROPERTY','is_customizable':True}]
        self.form['attributes']={'material':[{'name':'Cotton'}]}
        self.save();checked=self.check()
        self.form['attributes']['material']=[{'name':'Polyester'}];self.save()
        result=self.submit(checked)
        self.assertFalse(result['ready'])
        self.assertEqual(result['errors'][0]['issues'][0]['kind'],'material_conflict')
        with self.server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_jobs').fetchone()[0],0)

    def test_image_order_and_target_shop_changes_still_invalidate_review(self):
        self.p['images'].append({'key':'img2','local_path':'/images/b.jpg','group_name':'商品图'})
        def image(data,item):
            return {'path':item['local_path'],'sha256':item['local_path'],'ext':'.jpg','width':1200,'height':1200}
        with patch.object(pub,'local_image',side_effect=image):
            checked=self.check();self.p['images'].reverse()
            with self.assertRaisesRegex(shop.ShopError,'已改变'):self.submit(checked)
            self.p['images'].reverse()
            with patch.object(pub,'identity',return_value=({**self.ctx,'selected_shop_id':'other'},self.selected)):
                with self.assertRaisesRegex(shop.ShopError,'已改变'):self.submit(checked)

    def test_missing_inventory_and_required_attributes_block(self):
        self.form['stocks']={};self.save();self.assertFalse(self.check()['ready'])
        self.form['stocks']={'sku1':'10'};self.save();self.meta['attributes']=[{'id':'material','name':'Material','type':'PRODUCT_PROPERTY','is_requried':True}]
        self.assertIn('Material',self.check()['errors'][0]['error'])

    def material_fixture(self):
        self.meta['attributes']=[dict(id='material',name='Material',type='PRODUCT_PROPERTY',is_multiple_selection=True,
                                     values=[dict(id='cotton',name='Cotton'),dict(id='polyester',name='Polyester')])]
        self.form.update(content_language='en',editor_language='en',title_input='Cotton Polyester Hoodie',
                         description_input='T-shirt: 100% Cotton\nHoodie: 50% Cotton 50% Polyester',
                         attributes={'material':[{'id':'cotton'}]})
        with self.server.db() as c:
            raw={'specifications':[dict(name='Material',value='Cotton')],
                 'desc_blocks':[dict(type='text',text=self.form['description_input'])]}
            c.execute('UPDATE product_capture SET product_json=?',(json.dumps(raw),))
        self.save()

    def test_preflight_automatically_completes_materials_and_sends_full_blend(self):
        self.material_fixture()
        direct=pub.post(self.server,'listing-submit',{'items':[{'market':'US','id':'p1'}],'fingerprint':'bypass'})
        self.assertFalse(direct['ready'])
        with self.server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_jobs').fetchone()[0],0)
        checked=self.check();self.assertTrue(checked['ready'],checked)
        self.assertTrue(checked['items'][0]['material_completed'])
        self.assertEqual(checked['items'][0]['material_review']['status'],'consistent')
        self.assertEqual(checked['items'][0]['profile']['attributes']['material'],[{'id':'cotton'},{'id':'polyester'}])
        self.submit(checked)
        calls=self.run_job()
        self.assertEqual(calls[0].kwargs['payload']['product_attributes'],
                         [{'id':'material','values':[{'id':'cotton'},{'id':'polyester'}]}])

    def test_material_metadata_completes_omission_instead_of_using_cotton_default(self):
        self.material_fixture();self.form['attributes']={};self.save()
        self.meta['attributes'][0]['values'][0]['is_default']=True
        result=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'category_id':'cat','include_package':False})
        self.assertEqual(result['suggested_attributes']['material'],[{'id':'cotton'},{'id':'polyester'}])
        self.assertEqual(result['material_review']['suggestion'],'50% Cotton / 50% Polyester')
        self.assertEqual(result['material_review']['automatic_attributes'],{})

    def test_material_ratio_conflict_stays_on_review_list_and_cannot_submit(self):
        self.material_fixture()
        self.form['description_input']='Hoodie: 100% Cotton';self.save()
        checked=self.check()
        self.assertFalse(checked['ready'])
        self.assertFalse(checked['items'][0]['material_completed'])
        self.assertEqual(checked['items'][0]['material_review']['status'],'conflict')
        self.assertEqual(checked['items'][0]['profile']['attributes']['material'],[{'id':'cotton'}])
        direct=pub.post(self.server,'listing-submit',{'items':[{'market':'US','id':'p1'}],'fingerprint':'bypass'})
        self.assertFalse(direct['ready'])
        with self.server.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_jobs').fetchone()[0],0)

    def test_material_completion_is_idempotent(self):
        self.material_fixture()
        first=self.check();second=self.check()
        self.assertTrue(first['items'][0]['material_completed'])
        self.assertFalse(second['items'][0]['material_completed'])
        self.assertEqual(first['fingerprint'],second['fingerprint'])

    def test_material_preview_uses_unsaved_edits_without_saving_them(self):
        self.material_fixture()
        draft=copy.deepcopy(self.form)
        draft.update(title_input='Polyester Hoodie',description_input='100% Polyester',attributes={})
        result=pub.post(self.server,'listing-material-review',{'item':{'market':'US','id':'p1'},'profile':draft})
        self.assertEqual(result['suggestion'],'100% Polyester')
        self.assertEqual(result['suggested_attributes']['material'],[{'id':'polyester'}])
        with self.server.db() as c:
            self.assertEqual(pub.profile(c,'US','p1')['title_input'],self.form['title_input'])
            self.assertEqual(json.loads(c.execute('SELECT product_json FROM product_capture').fetchone()[0])['specifications'][0]['value'],'Cotton')

    def test_consistent_material_is_filled_before_check(self):
        self.material_fixture()
        self.form.update(title_input='Cotton T-shirt',description_input='100% Cotton',attributes={});self.save()
        result=self.check()
        self.assertTrue(result['ready'],result)
        self.assertTrue(result['items'][0]['material_completed'])
        self.assertEqual(result['items'][0]['profile']['attributes']['material'],[{'id':'cotton'}])

    def test_category_recommendations_keep_manual_choice_and_require_sellable_leaf(self):
        item={'market':'US','id':'p1'}
        self.meta['categories'].append({'id':'blocked','is_leaf':True,'permission_statuses':[]})
        with patch.object(pub,'call',side_effect=[{'leaf_category_id':'blocked'},{'leaf_category_id':'cat'}]) as api:
            saved=pub.listing_metadata(self.server,self.ctx,{'item':item,'category_id':'cat','include_package':False})
            self.assertEqual(saved['match_status'],'')
            api.assert_not_called()
            auto=pub.listing_metadata(self.server,self.ctx,{'item':item,'include_package':False})
            self.assertEqual((auto['category_id'],auto['match_status']),('cat','matched'))
            self.assertEqual(api.call_args.kwargs['payload'],{'product_title':'Test shirt','category_version':'v2'})
        with patch.object(pub,'call',return_value={'leaf_category_id':'blocked'}):
            auto=pub.listing_metadata(self.server,self.ctx,{'item':item,'include_package':False})
            self.assertEqual((auto['category_id'],auto['match_status']),('','no_match'))

    def test_long_title_still_requests_official_leaf_recommendation(self):
        title='Hoodie '+('printed clothing '*25)
        with patch.object(pub,'call',return_value={'leaf_category_id':'cat'}) as api:
            self.assertEqual(pub.recommend_category(self.data,self.ctx,title,'local',{'cat'}),('cat','matched'))
        self.assertEqual(api.call_args.kwargs['payload']['product_title'],title[:255])

    def test_recommendation_includes_captured_category_context_but_not_guessed_leaf(self):
        source={'category':'Menswear > Tops > Hoodies & Sweatshirts','category_id':'parent'}
        with patch.object(pub,'call',return_value={'leaf_category_id':'cat'}) as api:
            self.assertEqual(pub.recommend_category(self.data,self.ctx,'Printed clothing','local',{'cat'},source),('cat','matched'))
        self.assertEqual(api.call_args.kwargs['payload']['origin_platform_category'],
                         {'id':'parent','name':'Hoodies & Sweatshirts','full_name':source['category']})

    def test_explicit_hoodie_refinement_stays_inside_source_branch(self):
        categories=[{'id':'parent','parent_id':'0'},
            {'id':'hoodie','parent_id':'parent','english_name':"Men's Hoodies",'is_leaf':True,'permission_statuses':['AVAILABLE']},
            {'id':'sweatshirt','parent_id':'parent','english_name':"Men's Sweatshirts",'is_leaf':True,'permission_statuses':['AVAILABLE']},
            {'id':'outside','parent_id':'0','english_name':"Women's Hoodies",'is_leaf':True,'permission_statuses':['AVAILABLE']}]
        self.assertEqual(pub.refine_source_category('Printed Pullover Hoodie','parent',categories),'hoodie')
        self.assertEqual(pub.refine_source_category('Printed Crewneck Sweatshirt','parent',categories),'sweatshirt')
        self.assertEqual(pub.refine_source_category('Hoodie & T-Shirt Option','parent',categories),'')
        self.assertEqual(pub.refine_source_category('Sweatshirt Hoodie','parent',categories),'')

    def test_auto_leaf_sync_saves_shop_path_and_preserves_manual_and_other_fields(self):
        self.p['category']='Tops'
        self.meta['categories']=[{'id':'parent','parent_id':'0','local_name':'Tops','permission_statuses':['AVAILABLE']},
            {'id':'cat','parent_id':'parent','local_name':'Hoodies','is_leaf':True,'permission_statuses':['AVAILABLE']},
            {'id':'other','parent_id':'parent','local_name':'Sweatshirts','is_leaf':True,'permission_statuses':['AVAILABLE']}]
        with self.server.db() as c:
            c.executescript("CREATE TABLE records(kind TEXT,market TEXT,id TEXT); CREATE TABLE draft_entries(market TEXT,id TEXT,active INTEGER); CREATE TABLE draft_events(id INTEGER PRIMARY KEY,market TEXT,product_id TEXT);")
            c.execute("INSERT INTO records VALUES('product','US','p1')")
            c.execute("INSERT INTO draft_entries VALUES('US','p1',1)")
        self.form.update(category_id='other',category_source='manual',stock='27');self.save()
        pub._category_sync_lock.acquire()
        with patch.object(pub,'recommend_category') as recommend:
            pub.sync_listing_categories(self.server)
        recommend.assert_not_called()
        with self.server.db() as c: saved=pub.profile(c,'US','p1')
        self.assertEqual(saved['category_id'],'other')
        self.assertEqual(saved['category_source'],'manual')
        self.assertEqual(saved['category_path'],'Tops > Sweatshirts')
        self.assertEqual(saved['category_shop_id'],'store');self.assertEqual(saved['stock'],'27')
        self.assertEqual(saved['category_sync']['status'],'matched')
        # An automatic match without a saved leaf calls TikTok and stores the full leaf path.
        self.form.pop('category_source');self.form.pop('category_id');self.save()
        pub._category_sync_lock.acquire()
        with patch.object(pub,'recommend_category',return_value=('cat','matched')) as recommend:
            pub.sync_listing_categories(self.server)
        recommend.assert_called_once()
        with self.server.db() as c: saved=pub.profile(c,'US','p1')
        self.assertEqual(saved['category_path'],'Tops > Hoodies')
        self.assertEqual(saved['category_source'],'competitor_refined')
        pub._category_sync_lock.acquire()
        with patch.object(pub,'recommend_category') as recommend:
            pub.sync_listing_categories(self.server)
        recommend.assert_not_called()

    def test_competitor_category_precedes_title_recommendation(self):
        self.meta['categories'] = [
            {'id':'root','local_name':'男装与男士内衣','parent_id':'0'},
            {'id':'suits','local_name':'男士西服与套装','parent_id':'root'},
            {'id':'cat','local_name':'男装套装','parent_id':'suits','is_leaf':True,'permission_statuses':['AVAILABLE']}]
        self.p['category'] = '男装与男士内衣 > 男士西服与套装 > 男装套装'
        with patch.object(pub,'call') as api:
            result=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'include_package':False})
        api.assert_not_called()
        self.assertEqual((result['category_id'],result['match_status']),('cat','competitor'))
        self.meta['categories'][-1]['permission_statuses']=[]
        self.assertEqual(pub.source_category({'category_id':'cat','category':self.p['category']},self.meta['categories']),'')

    def test_library_category_replaces_raw_breadcrumb_and_is_revalidated(self):
        path='男装 > 男士腰夹束身衣'
        raw={'category':'女装 > 女士塑身衣','category_id':'old','category_is_leaf':True}
        with self.server.db() as c:
            c.execute('UPDATE product_capture SET product_json=?',(json.dumps(raw),))
        self.meta['categories']=[
            {'id':'root','local_name':'男装','parent_id':'0','is_leaf':False},
            {'id':'cat','local_name':'男士腰夹束身衣','parent_id':'root','is_leaf':True,'permission_statuses':['AVAILABLE']},
            {'id':'old','local_name':'女士塑身衣','parent_id':'0','is_leaf':True,'permission_statuses':['AVAILABLE']}]
        with patch.object(library_categories,'saved_path',return_value=path),patch.object(pub,'recommend_category') as recommend:
            prepared=pub.prepare(self.server,[{'market':'US','id':'p1'}])['items'][0]
            self.assertEqual(prepared['source']['category'],path)
            self.assertEqual(prepared['source']['category_id'],'')
            self.assertIsNone(prepared['source']['category_is_leaf'])
            result=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'include_package':False})
            self.assertEqual((result['category_id'],result['category_path']),('cat',path))
            self.meta['categories'][1]['permission_statuses']=['INVITE_ONLY']
            result=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'include_package':False})
            self.assertEqual(result['category_id'],'')
            self.assertEqual(result['match_status'],'permission_required')
            self.assertIn('INVITE_ONLY',result['category_message'])
            recommend.assert_not_called()
        with self.server.db() as c:
            self.assertEqual(json.loads(c.execute('SELECT product_json FROM product_capture').fetchone()[0]),raw)

    def test_old_competitor_category_refines_only_within_its_descendants(self):
        self.p['category']='Sets'
        self.meta['categories']=[{'id':'parent','local_name':'Sets','parent_id':'0','is_leaf':False,'permission_statuses':['AVAILABLE']},
            {'id':'cat','local_name':'Long','parent_id':'parent','is_leaf':True,'permission_statuses':['AVAILABLE']},
            {'id':'short','local_name':'Short','parent_id':'parent','is_leaf':True,'permission_statuses':['AVAILABLE']},
            {'id':'other','local_name':'Shirts','parent_id':'0','is_leaf':True,'permission_statuses':['AVAILABLE']}]
        with patch.object(pub,'call',return_value={'leaf_category_id':'other'}):
            r=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'include_package':False})
            self.assertEqual(r['category_id'],'')
        with patch.object(pub,'call',return_value={'leaf_category_id':'cat'}):
            r=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'include_package':False})
            self.assertEqual((r['category_id'],r['match_status']),('cat','competitor_refined'))
        with patch.object(pub,'call') as api:
            r=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'category_id':'other','category_source':'manual','include_package':False})
            self.assertEqual(r['category_id'],'other');api.assert_not_called()


    def test_restricted_source_category_reports_permissions_without_switching_branch(self):
        self.p['category']="Kids' Fashion > 男童服饰 > 男童短袖及衬衫"
        self.meta['categories']=[
            {'id':'kids','local_name':"Kids' Fashion",'parent_id':'0','is_leaf':False,'permission_statuses':['AVAILABLE']},
            {'id':'boys','local_name':'男童服饰','parent_id':'kids','is_leaf':False,'permission_statuses':['AVAILABLE']},
            {'id':'tops','local_name':'男童短袖及衬衫','parent_id':'boys','is_leaf':False,'permission_statuses':['INVITE_ONLY']},
            {'id':'hoodies','local_name':'男童连帽衫和运动衫','parent_id':'tops','is_leaf':True,'permission_statuses':['INVITE_ONLY']},
            {'id':'cat','local_name':'成人卫衣','parent_id':'0','is_leaf':True,'permission_statuses':['AVAILABLE']}]
        with patch.object(pub,'recommend_category') as recommend:
            result=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'include_package':False})
        recommend.assert_not_called()
        self.assertEqual(result['category_id'],'')
        self.assertEqual(result['match_status'],'permission_required')
        self.assertIn('INVITE_ONLY',result['category_message'])
        self.assertIn('男童短袖及衬衫',result['category_message'])
        with patch.object(pub,'predict_package',return_value={}): checked=self.check()
        self.assertFalse(checked['ready'])
        self.assertIn('INVITE_ONLY',checked['errors'][0]['error'])
        # An available child is still usable, even if its parent is invite-only.
        self.meta['categories'][3]['permission_statuses']=['AVAILABLE']
        with patch.object(pub,'recommend_category',return_value=('hoodies','matched')):
            result=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'include_package':False})
        self.assertEqual(result['category_id'],'hoodies')
        self.assertEqual(result['category_message'],'')

    def test_package_prediction_uses_recognized_source_even_without_listing_permission(self):
        self.p['category']='Restricted Tops'
        self.meta['categories']=[
            {'id':'parent','local_name':'Restricted Tops','parent_id':'0','is_leaf':False,'permission_statuses':['INVITE_ONLY']},
            {'id':'child','local_name':'Hoodies','parent_id':'parent','is_leaf':True,'permission_statuses':['INVITE_ONLY']}]
        prediction={'weight':'0.131','length':'22','width':'20','height':'2'}
        with patch.object(pub,'predict_package',return_value=prediction) as predict:
            result=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'}})
        predict.assert_called_once_with(self.data,self.ctx,'parent')
        self.assertEqual(result['category_id'],'')
        self.assertEqual(result['match_status'],'permission_required')
        self.assertEqual(result['package_status'],'predicted')
        form=pub.apply_listing_defaults({},result)
        self.assertEqual(form['weight'],'0.131')
        self.assertEqual(form['category_id'],'')
        self.assertEqual(form['package_prediction']['category_id'],'parent')
        form['weight']='0.7'
        self.assertEqual(pub.apply_listing_defaults(form,result)['weight'],'0.7')
        with patch.object(pub,'predict_package',return_value={}) as predict:
            empty=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'}})
            self.assertEqual(empty['package_status'],'empty')
            self.assertEqual(empty['suggested_package'],{})
        with patch.object(pub,'predict_package',side_effect=shop.ShopError('temporarily unavailable')):
            failed=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'}})
            self.assertEqual(failed['package_status'],'failed')
            self.assertIn('temporarily unavailable',failed['package_message'])
        with patch.object(pub,'predict_package') as predict:
            skipped=pub.listing_metadata(self.server,self.ctx,{'item':{'market':'US','id':'p1'},'include_package':False})
            predict.assert_not_called()
            self.assertEqual(skipped['package_status'],'not_requested')

    def test_captured_stock_remains_reference_and_filters_document_images(self):
        source={'description':'Captured','stocks':{'sku1':'67','sku2':'0','sku3':'99'}}
        product={'variants':[{'id':'sku1'},{'id':'sku2'},{'id':'sku3'}], 'images':[
            {'key':'photo','group_name':'商品图','role':'product'},
            {'key':'chart','group_name':'商品图','role':'size_chart'},
            {'key':'note','group_name':'商品图','role':'reminder'}]}
        f=pub.reuse_source_fields({'stocks':{'sku1':'200','sku3':'0'},'main_image_keys':['photo','chart','note']},source,product)
        self.assertEqual(f['stocks'],{'sku1':'200','sku3':'0'})
        self.assertEqual(f['reference_stocks'],source['stocks'])
        self.assertEqual(f['main_image_keys'],['photo'])
        self.assertNotIn('size_chart',f)
        self.assertNotIn('chart',f['description_image_keys'])
        self.assertEqual(pub.reuse_source_fields({},source,product)['main_image_keys'],['photo'])

    def test_category_change_retains_valid_saved_attributes(self):
        rules=[{'id':'fit','type':'PRODUCT_PROPERTY','values':[{'id':'loose','name':'Loose'}]},
               {'id':'material','type':'PRODUCT_PROPERTY','values':[{'id':'cotton','name':'Cotton'}]}]
        old={'category_id':'old','attributes':{'fit':[{'id':'loose'}],'material':[{'id':'invalid'}]},'certifications':{'old':'file'}}
        updated=pub.apply_listing_defaults(old,{**self.meta,'category_id':'cat','attributes':rules,'suggested_attributes':{'material':[{'id':'cotton'}]}})
        self.assertEqual(updated['attributes'],{'fit':[{'id':'loose'}],'material':[{'id':'cotton'}]})
        self.assertEqual(updated['certifications'],{})

    def test_package_prediction_converts_units_and_rejects_partial_response(self):
        response={'package_weight':{'unit':'GRAM','value':'500'},
                  'package_dimensions':{'unit':'INCH','length':12,'width':8,'height':2}}
        with patch.object(pub,'call',return_value=response) as api:
            values=pub.predict_package(self.data,self.ctx,'cat')
            self.assertEqual(values,{'weight':'0.500','length':'31','width':'21','height':'6'})
            self.assertEqual(api.call_args.args[2],'/product/202609/products/package/predict')
            self.assertEqual(api.call_args.kwargs['payload'],{'category_id':'cat'})
            pub.predict_package(self.data,self.ctx,'cat');self.assertEqual(api.call_count,1)
        del response['package_dimensions']['height']
        with patch.object(pub,'call',return_value=response),self.assertRaises(shop.ShopError):
            pub.predict_package(self.data,self.ctx,'different')

    def test_fractional_gram_predictions_and_saved_weights_reach_api_with_valid_precision(self):
        response={'package_weight':{'unit':'KILOGRAM','value':'0.4765'},
                  'package_dimensions':{'unit':'CENTIMETER','length':'23','width':'24','height':'6'}}
        for unit,value,expected in [('KILOGRAM','0.4765','0.477'),('GRAM','476.5','0.477'),
                                    ('POUND','1','0.454'),('OUNCE','1','0.029')]:
            with self.subTest(unit=unit),patch.object(pub,'call',return_value={**response,'package_weight':{'unit':unit,'value':value}}):
                self.assertEqual(pub.predict_package(self.data,self.ctx,'category-'+unit)['weight'],expected)
        for original,expected in [('0.4765','0.477'),('0.00001','0.001'),('0.476000','0.476'),('1000','1000'),('0.2','0.2')]:
            self.assertEqual(pub.package_details({**self.form,'weight':original})['weight'],expected)
        # Previously saved profiles are normalized at the payload boundary too.
        self.form['weight']='0.4765';self.save();self.meta['mode']='local'
        self.submit();calls=self.run_job()
        self.assertEqual(self.job()['state'],'submitted')
        for request in calls:
            self.assertEqual(request.kwargs['payload']['package_weight'],{'unit':'KILOGRAM','value':'0.477'})

    def test_batch_check_fills_unopened_products_and_preserves_manual_package(self):
        self.form.pop('category_id')
        for key in pub.PACKAGE_FIELDS:self.form.pop(key)
        self.save()
        predicted={'weight':'0.5','length':'30','width':'20','height':'10'}
        with patch.object(pub,'recommend_category',return_value=('cat','matched')),patch.object(pub,'predict_package',return_value=predicted):
            result=pub.post(self.server,'listing-check',{'items':[{'market':'US','id':'p1'},{'market':'US','id':'p2'}]})
        self.assertEqual(len(result['items']),2)
        with self.server.db() as c:
            for pid in ['p1','p2']:
                f=pub.profile(c,'US',pid)
                self.assertEqual(f['category_id'],'cat');self.assertEqual(f['weight'],'0.5')
                self.assertEqual(f['warehouse_id'],'wh')
        f['weight']='0.7'
        meta={**self.meta,'category_id':'new','shop_id':'store','suggested_package':{**predicted,'weight':'1','height':'15'}}
        changed=pub.apply_listing_defaults(f,meta)
        self.assertEqual(changed['weight'],'0.7');self.assertEqual(changed['height'],'15')
        meta['suggested_package']={}
        changed=pub.apply_listing_defaults(f,meta)
        self.assertNotIn('height',changed);self.assertEqual(changed['weight'],'0.7')

    def test_batch_category_preserves_fields_and_refreshes_automatic_package(self):
        self.form.update(category_id='old',attributes={'old':[{'id':'value'}]},certifications={'old':'file'},
                         package_prediction={'category_id':'old','shop_id':'store','values':{'weight':'0.2'}})
        self.save()
        body={'items':[{'market':'US','id':'p1'},{'market':'US','id':'p2'}],'category_id':'cat','shop_id':'store'}
        with patch.object(pub,'predict_package',return_value={'weight':'0.477','length':'23','width':'24','height':'6'}):
            result=pub.post(self.server,'listing-category-batch',body)
        self.assertEqual(result['count'],2)
        with self.server.db() as c:
            form=pub.profile(c,'US','p1');second=pub.profile(c,'US','p2')
        self.assertEqual(form['category_source'],'manual');self.assertEqual(form['category_id'],'cat')
        self.assertEqual(form['stocks'],self.form['stocks']);self.assertEqual(form['description'],'Cotton shirt')
        self.assertEqual(form['weight'],'0.477');self.assertEqual(form['length'],'20')
        self.assertEqual(form['attributes'],{});self.assertEqual(form['certifications'],{})
        self.assertEqual(second['weight'],'0.477')
        with patch.object(pub,'recommend_category') as recommendation,patch.object(pub,'predict_package',return_value={}):
            meta=pub.listing_metadata(self.server,self.ctx,{'item':body['items'][0],'category_id':'cat','category_source':'manual'})
            self.assertEqual(meta['category_id'],'cat');recommendation.assert_not_called()

    def test_batch_category_rejects_parent_restricted_and_changed_shop(self):
        body={'items':[{'market':'US','id':'p1'}],'category_id':'cat','shop_id':'store'}
        for node in [{'id':'cat','is_leaf':False,'permission_statuses':['AVAILABLE']},
                     {'id':'cat','is_leaf':True,'permission_statuses':['INVITE_ONLY']}]:
            self.meta['categories']=[node]
            with self.assertRaisesRegex(shop.ShopError,'末级类目'):pub.post(self.server,'listing-category-batch',body)
        with self.assertRaisesRegex(shop.ShopError,'店铺已改变'):
            pub.post(self.server,'listing-category-batch',{**body,'shop_id':'other'})

    def test_batch_category_validates_whole_batch_before_saving(self):
        body={'items':[{'market':'US','id':'p1'},{'market':'US','id':'missing'}],'category_id':'cat','shop_id':'store'}
        with patch.object(pub,'predict_package',return_value={}),patch.object(pub.listing_editor,'product',side_effect=[{},ValueError('missing')]):
            with self.assertRaisesRegex(ValueError,'missing'):pub.post(self.server,'listing-category-batch',body)
        with self.server.db() as c:self.assertNotIn('category_source',pub.profile(c,'US','p1'))

    def test_batch_stock_updates_every_variant_including_zero(self):
        self.form['stocks']={'sku1':'0','sku2':'25'};self.save()
        self.p['variants'].append({**self.p['variants'][0],'id':'sku2'})
        result=pub.post(self.server,'listing-stock-batch',{'items':[{'market':'US','id':'p1'},{'market':'US','id':'p2'}],'stock':'150'})
        self.assertEqual(result['sku_count'],4);self.assertEqual(result['count'],2)
        with self.server.db() as c:
            for pid in ['p1','p2']:
                f=pub.profile(c,'US',pid);self.assertEqual(f['stocks'],{'sku1':'150','sku2':'150'})
                reused=pub.reuse_source_fields(f,{'description':'text','stocks':{'sku1':'0'}},self.p,self.data)
                self.assertEqual(reused['stocks']['sku1'],'150')
        for invalid in ['0','-1','1.5','100000','']:
            with self.assertRaises(shop.ShopError):pub.post(self.server,'listing-stock-batch',{'items':[{'market':'US','id':'p1'}],'stock':invalid})

    def test_batch_stock_missing_product_does_not_partially_save(self):
        with patch.object(pub.listing_editor,'listing_payload',side_effect=[self.p,ValueError('missing')]):
            with self.assertRaisesRegex(ValueError,'missing'):
                pub.post(self.server,'listing-stock-batch',{'items':[{'market':'US','id':'p1'},{'market':'US','id':'missing'}],'stock':'200'})
        with self.server.db() as c:self.assertEqual(pub.profile(c,'US','p1')['stocks'],self.form['stocks'])

    def test_empty_package_prediction_never_fills_fake_values(self):
        for key in pub.PACKAGE_FIELDS:self.form.pop(key)
        self.save()
        with patch.object(pub,'predict_package',return_value={}):result=self.check()
        self.assertFalse(result['ready'])
        self.assertIn('平台暂未返回',result['errors'][0]['error'])
        self.assertNotIn('weight',result['items'][0]['profile'])

    def test_template_round_trip_is_shop_scoped_and_validates_values(self):
        body={'name':'袋装 T 恤','shop_id':'store','values':self.form.copy()}
        result=pub.post(self.server,'listing-template',body)['templates']
        self.assertEqual(result,[{'name':'袋装 T 恤','values':{
            'warehouse_id':'wh','global_warehouse_id':'gwh','weight':'0.2',
            'length':'20','width':'15','height':'3'}}])
        items=[{'market':'US','id':'p1'}]
        self.assertEqual(pub.prepare(self.server,items)['templates'],result)
        body['values']['width']='bad'
        with self.assertRaisesRegex(shop.ShopError,'包装宽'):
            pub.post(self.server,'listing-template',body)
        cfg=shop.load(self.data);cfg['selected_shop_id']='other'
        cfg['shops'].append({'id':'other','region':'US'});shop.save(self.data,cfg)
        self.assertEqual(pub.prepare(self.server,items)['templates'],[])
        with self.assertRaisesRegex(shop.ShopError,'目标店铺已改变'):
            pub.post(self.server,'listing-template',body)

    def test_target_change_stops_before_upload(self):
        self.submit();cfg=shop.load(self.data);cfg['selected_shop_id']='other';cfg['shops'].append({'id':'other','region':'US'});shop.save(self.data,cfg)
        with patch.object(pub,'upload') as upload:pub.run_one(self.server,self.job());upload.assert_not_called()
        self.assertEqual(self.job()['state'],'failed')

    def test_local_api_has_idempotency_key(self):
        self.meta['mode']='local';self.submit();calls=self.run_job()
        self.assertEqual(calls[-1].kwargs['payload']['idempotency_key'],self.job()['id']);self.assertEqual(calls[-1].kwargs['payload']['save_mode'],'LISTING')

    def test_platform_preflight_failure_never_creates_product(self):
        self.meta['mode']='local';self.submit()
        with patch.object(pub,'upload',return_value={'uri':'uri1'}),patch.object(pub,'call',return_value={'check_result':'FAILED','fail_reasons':[{'message':'Invalid warehouse'}]}) as api:
            pub.run_one(self.server,self.job())
        self.assertEqual(api.call_count,1)
        self.assertTrue(api.call_args.args[2].endswith('/listing_check'))
        self.assertEqual(self.job()['state'],'failed')
        self.assertEqual(self.job()['stage'],'checking')
        self.assertIn('Invalid warehouse',self.job()['message'])

    def test_crash_recovery_does_not_resend(self):
        self.submit();pub.update(self.server,self.job()['id'],state='running',stage='creating')
        with self.server.db() as c:pub.recover(c)
        self.assertEqual(self.job()['state'],'uncertain')

    def test_only_verified_active_counts_as_active(self):
        self.submit();self.run_job()
        for status,expected in [('ACTIVATE','active'),('FAILED','rejected'),('PLATFORM_DEACTIVATED','inactive'),('FREEZE','frozen'),('DELETED','deleted'),('SCHEDULED','scheduled'),('UNKNOWN','uncertain')]:
            with self.subTest(status=status),patch.object(pub,'call',return_value={'status':status}):
                pub.post(self.server,'listing-refresh',{'id':self.job()['id']})
                self.assertEqual(self.job()['state'],expected)
                if expected!='rejected':
                    with self.assertRaises(shop.ShopError):self.submit()

    def test_reading_jobs_keeps_saved_status_without_platform_sync(self):
        self.submit();self.run_job();saved=self.job()
        with patch.object(pub,'start_status_sync') as sync,patch.object(pub,'call') as api:
            for _ in range(2):
                result=pub.get(self.server,'listing-jobs',{})
                self.assertEqual(result['items'][0]['id'],saved['id'])
                self.assertEqual(result['items'][0]['state'],saved['state'])
            sync.assert_not_called()
            api.assert_not_called()
        self.assertEqual(self.job()['updated_at'],saved['updated_at'])

    def test_background_status_sync_updates_live_and_caches_checks(self):
        self.submit();self.run_job()
        pub._status_sync_checked.clear()
        with patch.object(pub,'call',return_value={'status':'ACTIVATE'}) as api:
            pub._status_sync_lock.acquire()
            pub.sync_listing_statuses(self.server,self.ctx)
            self.assertEqual(self.job()['state'],'active')
            self.assertEqual(api.call_count,1)
            pub._status_sync_lock.acquire()
            pub.sync_listing_statuses(self.server,self.ctx)
            self.assertEqual(api.call_count,1)
            pub._status_sync_lock.acquire()
            pub.sync_listing_statuses(self.server,self.ctx,force=True)
            self.assertEqual(api.call_count,2)
        self.assertFalse(pub._status_sync_state['running'])
        pub._status_sync_checked.clear()

    def test_status_sync_keeps_last_known_state_on_api_failure(self):
        self.submit();self.run_job()
        with patch.object(pub,'call',side_effect=shop.ShopError('network error')):
            pub._status_sync_lock.acquire()
            pub.sync_listing_statuses(self.server,self.ctx,force=True)
        self.assertEqual(self.job()['state'],'submitted')
        self.assertEqual(len(pub._status_sync_state['errors']),1)
        self.assertFalse(pub._status_sync_lock.locked())
        pub._status_sync_checked.clear()

    def test_status_refresh_does_not_query_another_shop(self):
        self.submit();self.run_job()
        with patch.object(pub,'call') as api:
            with self.assertRaises(shop.ShopError):
                pub.refresh_listing_status(self.server,self.job()['id'],{'selected_shop_id':'different'})
            api.assert_not_called()
        self.assertEqual(self.job()['state'],'submitted')

    def test_rejected_product_creates_fresh_attempt_and_preserves_history(self):
        self.submit();self.run_job();first=self.job()
        pub.update(self.server,first['id'],state='rejected',remote_status='FAILED',message='PDP inconsistent')
        self.form['weight']='0.3';self.save();self.submit();second=self.job()
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(second['state'],'queued')
        self.assertIsNone(second['global_response'])
        self.assertEqual(second['remote_id'],'')
        calls=self.run_job();self.assertEqual(len(calls),2)
        self.assertEqual(calls[0].kwargs['payload']['external_global_product_id'],'folio:'+second['id'])
        with self.server.db() as c:
            old=dict(c.execute('SELECT * FROM shop_listing_job_history').fetchone())
            visible=pub.jobs(c);pub.migrate(c)
            self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_job_history').fetchone()[0],1)
        self.assertEqual(old['id'],first['id']);self.assertEqual(old['remote_id'],'remote1')
        self.assertEqual(old['message'],'PDP inconsistent');self.assertEqual(len(visible),2)
        self.assertTrue(all('snapshot' not in x for x in visible))
        with patch.object(pub,'call',return_value={'status':'FAILED'}):
            pub.post(self.server,'listing-refresh',{'id':first['id']})
        self.assertEqual(self.job()['state'],'submitted')

    def test_rejected_local_product_uses_new_idempotency_key(self):
        self.meta['mode']='local';self.submit();self.run_job();first=self.job()
        pub.update(self.server,first['id'],state='rejected',remote_status='FAILED')
        self.submit();second=self.job();calls=self.run_job()
        created=next(x.kwargs['payload'] for x in calls if x.args[2].endswith('/products'))
        self.assertNotEqual(first['id'],created['idempotency_key'])
        self.assertEqual(created['idempotency_key'],second['id'])
        with self.assertRaisesRegex(shop.ShopError,'已有发布记录'):self.submit()

    def test_running_and_uncertain_jobs_still_block_recreation(self):
        self.submit();jid=self.job()['id']
        for state in ('queued','running','uncertain','submitted'):
            pub.update(self.server,jid,state=state)
            with self.subTest(state=state),self.assertRaises(shop.ShopError):self.submit()
        with self.server.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_jobs').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM shop_listing_job_history').fetchone()[0],0)

    def test_secrets_never_return_in_status_or_jobs(self):
        self.submit();status=shop.public_status(self.data);self.assertNotIn('app_secret',status);self.assertNotIn('token',status)
        with self.server.db() as c:result=pub.jobs(c)
        self.assertNotIn('snapshot',result[0])

    def test_http_permission_error_is_actionable_and_redacted(self):
        body=json.dumps({'code':105005,'message':'missing scope secret token','request_id':'r1'}).encode()
        failure=urllib.error.HTTPError('https://example.test',401,'Unauthorized',{},io.BytesIO(body))
        with patch.object(shop.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=failure
            with self.assertRaises(shop.ShopError) as caught:
                shop.request('https://example.test',{'app_secret':'secret','token':{'access_token':'token'}})
        self.assertNotIsInstance(caught.exception,shop.UncertainError)
        self.assertIn('105005',str(caught.exception));self.assertIn('重新授权',str(caught.exception))
        self.assertNotIn('secret',str(caught.exception));self.assertNotIn('token',str(caught.exception))

    def test_metadata_excludes_return_warehouses(self):
        responses=[{'permissions':[]},{'categories':[]},{'warehouses':[
            {'id':'sales','type':'SALES_WAREHOUSE'},
            {'id':'returns','type':'RETURN_WAREHOUSE'},
            {'id':'unknown','type':'UNKNOWN'}]}]
        # setUp mocks metadata for submission tests; exercise the real function here.
        with patch.object(pub,'call',side_effect=responses):
            result=real_metadata(self.data,self.ctx,fresh=True)
        self.assertEqual([w['id'] for w in result['warehouses']],['sales'])

    def test_shop_listing_rules_override_global_seller_permission(self):
        for methods,mode in [(['LOCAL_REPLICATION'],'local'),('LOCAL_REPLICATION','local'),(['GLOBAL_PUBLISHING','LOCAL_REPLICATION'],'local'),(['GLOBAL_PUBLISHING'],'global')]:
            with self.subTest(methods=methods):
                responses=[{'permissions':['MANAGE_GLOBAL_PRODUCT']},{'listing_methods':methods},{'categories':[]},{'warehouses':[]}]
                if mode=='global': responses.append({'global_warehouses':[]})
                with patch.object(pub,'call',side_effect=responses) as api:
                    result=real_metadata(self.data,self.ctx,fresh=True)
                self.assertEqual(result['mode'],mode)
                self.assertEqual(api.call_args_list[1].args[2],'/product/202507/global_listing_rules')
                self.assertNotIn('shop',api.call_args_list[1].kwargs)  # use selected shop cipher
                self.assertEqual(api.call_args_list[2].args[2],pub.PREFIX+('categories' if mode=='local' else 'global_categories'))
                self.assertEqual(api.call_args_list[2].kwargs['shop'],mode=='local')
                self.assertEqual(api.call_args_list[2].kwargs['params']['locale'],'zh-CN')

    def test_unknown_shop_rules_never_fall_back_to_wrong_creation_api(self):
        with patch.object(pub,'call',side_effect=[{'permissions':['MANAGE_GLOBAL_PRODUCT']},{'listing_methods':[]}]) as api:
            with self.assertRaisesRegex(shop.ShopError,'上架方式'):real_metadata(self.data,self.ctx,fresh=True)
            self.assertEqual(api.call_count,2)

    def test_cross_border_local_flow_uses_shop_inventory_and_final_sale_price(self):
        self.meta['mode']='local';self.form.pop('global_warehouse_id');self.save()
        cfg=shop.load(self.data);cfg['shops'][0]['seller_type']='CROSS_BORDER';shop.save(self.data,cfg)
        self.submit();calls=self.run_job()
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0].args[2],pub.PREFIX+'products/listing_check')
        self.assertEqual(calls[1].args[2],pub.PREFIX+'products')
        sku=calls[1].kwargs['payload']['skus'][0]
        self.assertEqual(sku['inventory'],[{'warehouse_id':'wh','quantity':10}])
        self.assertEqual(sku['price']['sale_price'],'20.00')
        self.assertEqual(self.job()['remote_id'],'remote1')

    def test_failed_global_create_can_retry_via_local_but_created_global_cannot(self):
        self.submit();pub.update(self.server,self.job()['id'],state='failed')
        self.meta['mode']='local';self.assertTrue(self.submit()['ready'])
        self.assertEqual(json.loads(self.job()['snapshot'])['mode'],'local')
        pub.update(self.server,self.job()['id'],state='failed',global_response='{"global_product_id":"g1"}')
        self.meta['mode']='global'
        with self.assertRaisesRegex(shop.ShopError,'已经创建'):self.submit()

    def test_api_context_pins_shop_before_request(self):
        cfg={**shop.load(self.data),'selected_shop_id':'other'}
        with patch.object(shop,'token_config',return_value=cfg),patch.object(shop,'request') as request:
            with self.assertRaises(shop.ShopError):shop.api(self.data,'/test',context=self.ctx)
            request.assert_not_called()

    def test_backup_restore_preserves_remote_submission_ledger(self):
        import backup_manager
        self.server.DB=self.data/'test.db'
        self.server.init=lambda: None
        with self.server.db() as c:
            c.executescript('CREATE TABLE records(id); CREATE TABLE snapshots(id); CREATE TABLE batches(id);')
        folder=backup_manager.directory(self.server)
        with self.server.db() as c,sqlite3.connect(folder/'old.sqlite3',factory=ClosingConnection) as old:c.backup(old)
        self.submit();self.run_job()
        backup_manager.restore(self.server,'old.sqlite3')
        self.assertEqual(self.job()['remote_id'],'remote1')
        with self.assertRaises(shop.ShopError):self.submit()

    def test_private_config_permissions(self):
        path=self.data/'credentials'/'tiktok-shop.enc'
        if sys.platform=='win32':
            self.assertTrue(path.read_text().startswith('dpapi:'))
        else:
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_batch_size_replaces_only_selected_profiles_and_preserves_other_fields(self):
        with self.server.db() as c:
            c.execute('INSERT INTO shop_listing_profiles VALUES(?,?,?,?)',('US','other',pub.dump({'size_chart':'original'}),1))
        with patch.object(pub.listing_editor,'validate_upload',return_value=(b'chart bytes','.png')):
            result=pub.post(self.server,'listing-size-batch',{'items':[{'market':'US','id':'p1'},{'market':'US','id':'p2'},{'market':'US','id':'p1'}],'data':'image','name':'size.png'})
        self.assertEqual(result['changed'],2)
        with self.server.db() as c:
            first=pub.profile(c,'US','p1');second=pub.profile(c,'US','p2')
            self.assertEqual(first['size_chart_template_id'],second['size_chart_template_id'])
            self.assertEqual(first['weight'],self.form['weight'])
            self.assertEqual(first['size_chart'],'')
            self.assertEqual(pub.profile(c,'US','other')['size_chart'],'original')
        self.assertEqual((self.data/result['image'].lstrip('/')).read_bytes(),b'chart bytes')
        prepared=pub.prepare(self.server,[{'market':'US','id':'p1'}])
        chart_id=prepared['items'][0]['profile']['size_chart_template_id']
        chart=next(t for t in prepared['size_templates'] if t['id']==chart_id)
        self.assertEqual(chart['image']['local_path'],result['image'])

    def test_batch_size_invalid_selection_is_atomic(self):
        with patch.object(pub.listing_editor,'validate_upload',return_value=(b'chart bytes','.png')),patch.object(pub.listing_editor,'product',side_effect=[{},ValueError('missing product')]):
            with self.assertRaisesRegex(ValueError,'missing product'):
                pub.post(self.server,'listing-size-batch',{'items':[{'market':'US','id':'p1'},{'market':'US','id':'missing'}],'data':'image'})
        with self.server.db() as c:
            self.assertEqual(pub.profile(c,'US','p1'),self.form)
            self.assertEqual(c.execute('SELECT count(*) FROM shop_size_templates').fetchone()[0],0)

    def test_company_size_template_is_independent_and_reusable(self):
        (self.data/'images').mkdir()
        source=self.data/'images/a.jpg';source.write_bytes(b'test chart bytes')
        result=pub.post(self.server,'listing-size-template',{'name':'Company hoodies','market':'US','id':'p1','image_key':'img'})
        chart=result['size_templates'][0]
        stored=self.data/chart['image']['local_path'].lstrip('/')
        self.assertEqual(stored.read_bytes(),b'test chart bytes')
        source.unlink()
        self.assertTrue(stored.exists())
        # Another product can select this chart without copying its image key.
        self.form.update(size_chart_template_id=result['id'],size_chart='missing-old-image')
        self.meta['rules']={'size_chart':{'is_required':True}}
        self.save();checked=self.check();self.assertTrue(checked['ready'])
        self.submit(checked);calls=self.run_job()
        self.assertEqual(calls[0].kwargs['payload']['size_chart'],{'image':{'uri':'uri1'}})
        with self.assertRaisesRegex(shop.ShopError,'同名'):
            pub.post(self.server,'listing-size-template',{'name':'Company hoodies','market':'US','id':'p1','image_key':'img'})

    def test_missing_company_chart_blocks_publish_and_invalid_source_blocks_save(self):
        self.form['size_chart_template_id']='missing-template';self.save()
        self.assertIn('模板不存在',self.check()['errors'][0]['error'])
        with self.assertRaisesRegex(shop.ShopError,'先上传'):
            pub.post(self.server,'listing-size-template',{'name':'Chart','market':'US','id':'p1','image_key':'outside-product'})
        with patch.object(pub,'local_image',return_value={'width':500,'height':1500}):
            with self.assertRaisesRegex(shop.ShopError,'1024'):
                pub.post(self.server,'listing-size-template',{'name':'Chart','market':'US','id':'p1','image_key':'img'})

if __name__=='__main__':unittest.main()
