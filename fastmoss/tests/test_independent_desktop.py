import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import desktop_runtime
import runtime_paths
import secure_storage
import tiktok_shop as shop
import remote_products
import platform_support


class IndependentDesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=Path(self.temp.name)
        (self.data/'config').mkdir()
        self.runtime=patch.object(shop,'RUNTIME',{});self.runtime.start()
        self.cfg={'app_key':'test-key','app_secret':'test-secret-private','service_id':'test-service',
                  'redirect_url':'http://127.0.0.1:8765/api/shop/callback'}
        self.token={'access_token':'test-access-private','refresh_token':'test-refresh-private','user_type':0,
                    'access_token_expire_in':int(time.time())+3600,'refresh_token_expire_in':int(time.time())+86400,
                    'granted_scopes':list(shop.REQUIRED_SCOPES)}
    def tearDown(self):
        self.runtime.stop();self.temp.cleanup()
    def authorized(self):
        self.cfg.update(token=self.token,shops=[{'id':'123','name':'测试店铺','region':'US','cipher':'private-cipher','seller_type':'LOCAL'}],selected_shop_id='123',last_verified_at=int(time.time()))
        shop.save(self.data,self.cfg)
    def state(self):
        shop.save(self.data,self.cfg)
        return urllib.parse.parse_qs(urllib.parse.urlsplit(shop.authorize_url(self.data)['url']).query)['state'][0]
    def test_ciphertext_restart_tamper_and_independent_keys(self):
        shop.save(self.data,{**self.cfg,'token':self.token})
        raw=shop.credential_path(self.data).read_text()
        for secret in ('test-key','test-secret-private','test-access-private','test-refresh-private'):
            self.assertNotIn(secret,raw)
        self.assertEqual(shop.load(self.data)['app_secret'],self.cfg['app_secret'])
        encoded=raw[:-5]+('A' if raw[-5]!='A' else 'B')+raw[-4:]
        with self.assertRaises(ValueError):secure_storage.unseal(self.data,encoded)
        if sys.platform!='win32':
            with tempfile.TemporaryDirectory() as other:
                with self.assertRaises(ValueError):secure_storage.unseal(Path(other),raw)
    @unittest.skipUnless(sys.platform=='win32','Native Windows DPAPI requires Windows')
    def test_native_dpapi_no_exported_key(self):
        shop.save(self.data,self.cfg)
        self.assertTrue(shop.credential_path(self.data).read_text().startswith('dpapi:'))
        self.assertFalse((self.data/'credentials'/'local.key').exists())
        self.assertEqual(shop.load(self.data)['app_secret'],self.cfg['app_secret'])
    def test_legacy_plaintext_migration(self):
        legacy=self.data/'tiktok-shop-private.json';legacy.write_text(json.dumps(self.cfg))
        self.assertEqual(shop.load(self.data)['app_key'],'test-key')
        self.assertFalse(legacy.exists());self.assertTrue(shop.credential_path(self.data).exists())
    def test_windows_user_data_layout(self):
        with patch.object(runtime_paths.sys,'platform','win32'),patch.dict(os.environ,{'LOCALAPPDATA':str(self.data)},clear=True):
            data=runtime_paths.data_directory(Path('/unused'))
            self.assertEqual(data,self.data/'TKShangjia')
            self.assertEqual(runtime_paths.database_path(data),data/'database'/'fastmoss.sqlite3')
    def test_fixed_port_ignores_old_runtime_and_environment(self):
        runtime_paths.write_runtime(self.data,{'port':54321})
        with patch.dict(os.environ,{'FASTMOSS_PORT':'54322'}),patch.object(desktop_runtime,'LocalHTTPServer') as listener:
            self.assertIs(desktop_runtime.bind_server(self.data,BaseHTTPRequestHandler),listener.return_value)
        listener.assert_called_once_with(('127.0.0.1',8765),BaseHTTPRequestHandler)
    def test_fixed_port_conflict_does_not_choose_another_port(self):
        first=desktop_runtime.LocalHTTPServer(('127.0.0.1',0),BaseHTTPRequestHandler)
        try:
            with patch.object(desktop_runtime,'UI_PORT',first.server_port):
                with self.assertRaisesRegex(desktop_runtime.FixedPortError,'不会自动更换端口'):
                    desktop_runtime.bind_server(self.data,BaseHTTPRequestHandler)
            self.assertEqual(runtime_paths.read_runtime(self.data),{})
        finally:first.server_close()
    def test_callback_only_listener(self):
        callback=desktop_runtime.start_callback(self.data,8765)
        try:
            shop.probe_callback(shop.RUNTIME['callback_url'])
            with self.assertRaises(urllib.error.HTTPError) as failure:
                urllib.request.urlopen('http://127.0.0.1:%d/api/shop/status'%callback.server_port)
            self.assertEqual(failure.exception.code,404)
            self.assertEqual(shop.callback_status({})['entry_url'],'http://127.0.0.1:8765/')
        finally:callback.shutdown();callback.server_close()
    def test_callback_port_reuse(self):
        first=desktop_runtime.start_callback(self.data,8765);port=first.server_port
        first.shutdown();first.server_close()
        second=desktop_runtime.start_callback(self.data,8765)
        try:self.assertEqual(second.server_port,port)
        finally:second.shutdown();second.server_close()
    def test_browser_redirect_does_not_authorize_without_shop(self):
        state=self.state()
        with patch.object(shop,'request',side_effect=[self.token,{'shops':[]}]):
            with self.assertRaisesRegex(shop.ShopError,'未返回可用店铺'):
                shop.complete_callback(self.data,{'state':state,'code':'fake-code'})
        self.assertFalse(shop.public_status(self.data)['authorized'])
    def test_exchange_only_pending_then_real_shop_required(self):
        state=self.state()
        shops={'shops':[{'id':'123','cipher':'cipher','region':'US','name':'测试店铺'}]}
        with patch.object(shop,'request',side_effect=[self.token,shops]) as request:
            result=shop.complete_callback(self.data,{'state':state,'code':'fake-code'})
        self.assertTrue(result['authorized']);self.assertEqual(result['shops'][0]['name'],'测试店铺')
        self.assertIn('grant_type=authorized_code',request.call_args_list[0].args[0])
        self.assertNotIn('fake-code',shop.credential_path(self.data).read_text())
        with self.assertRaisesRegex(shop.ShopError,'无效或过期'):shop.complete_callback(self.data,{'state':state,'code':'fake-code'})
    def test_expired_wrong_and_denied_state(self):
        state=self.state()
        with patch.object(shop,'request') as network:
            with self.assertRaises(shop.ShopError):shop.exchange(self.data,{'state':'wrong','code':'x'})
            with self.assertRaises(shop.ShopError):shop.exchange(self.data,{'state':state,'error':'access_denied'})
            network.assert_not_called()
        self.assertNotIn('oauth_state',shop.load(self.data))
        state=self.state();cfg=shop.load(self.data);cfg['oauth_state']['expires']=0;shop.save(self.data,cfg)
        with self.assertRaises(shop.ShopError):shop.exchange(self.data,{'state':state,'code':'x'})
    def test_callback_app_mismatch_and_creator_rejected(self):
        state=self.state()
        with patch.object(shop,'request') as request:
            with self.assertRaisesRegex(shop.ShopError,'应用与当前'):shop.exchange(self.data,{'state':state,'code':'x','app_key':'different'})
            request.assert_not_called()
        state=self.state()
        with patch.object(shop,'request',return_value={**self.token,'user_type':1}):
            with self.assertRaisesRegex(shop.ShopError,'卖家账号'):shop.exchange(self.data,{'state':state,'code':'x'})
    def test_refresh_does_not_keep_revoked_scopes(self):
        self.authorized()
        with patch.object(shop,'request',return_value={**self.token,'granted_scopes':['seller.authorization.info']}):
            cfg=shop.token_config(self.data,force=True)
        self.assertEqual(cfg['token']['granted_scopes'],['seller.authorization.info'])
        with self.assertRaisesRegex(shop.ShopError,'seller.product.basic'):
            shop.api(self.data,'/product/202309/products/search',payload={})
    def test_redirect_placeholder_foreign_link_and_changed_port_rejected(self):
        for body in ({'redirect_url':'https://test.com/api/shop/callback'},
                     {'authorization_link':'https://evil.invalid/open/authorize?service_id=1'}):
            with self.assertRaises(shop.ShopError):shop.configure(self.data,{**self.cfg,**body})
        shop.RUNTIME={'callback_url':'http://127.0.0.1:8999/api/shop/callback'}
        with self.assertRaisesRegex(shop.ShopError,'本地地址已变化'):shop.configure(self.data,self.cfg)
    def test_nonempty_credentials_are_not_verified(self):
        shop.save(self.data,self.cfg)
        with patch.object(shop,'probe_callback'),patch.object(shop,'request') as request:
            result=shop.diagnostics(self.data)
        request.assert_not_called();self.assertFalse(result['ok'])
        states={x['key']:x['state'] for x in result['checks']}
        self.assertEqual(states['app_key'],'pending');self.assertEqual(states['app_secret'],'pending')
        self.assertEqual(states['redirect'],'pending');self.assertFalse(result['write_tested'])
    def test_three_fields_start_authorization_without_claiming_success(self):
        callback=desktop_runtime.start_callback(self.data,8765)
        try:
            body={'app_key':'own-key','app_secret':'own-secret','authorization_link':'https://services.us.tiktokshop.com/open/authorize?service_id=own-service&state=discard-this'}
            with patch('webbrowser.open') as browser:
                result=shop.connect_from_fields(self.data,body)
            browser.assert_not_called()
            cfg=shop.load(self.data)
            self.assertEqual(cfg['redirect_url'],'http://127.0.0.1:8765/')
            self.assertEqual(cfg['service_id'],'own-service')
            self.assertNotIn('discard-this',result['url'])
            self.assertTrue(urllib.parse.parse_qs(urllib.parse.urlsplit(result['url']).query)['state'][0])
            self.assertFalse(shop.public_status(self.data)['authorized'])
            self.assertTrue(shop.public_status(self.data)['authorization_pending'])
            self.assertFalse(cfg['permissions_confirmed'])
            with patch('webbrowser.open') as browser:
                shop.connect_from_fields(self.data,{**body,'app_secret':''})
            browser.assert_not_called()
            self.assertEqual(shop.load(self.data)['app_secret'],'own-secret')
            with self.assertRaisesRegex(shop.ShopError,'同时粘贴'):
                shop.connect_from_fields(self.data,{**body,'app_key':'other-key','app_secret':''})
            self.assertEqual(shop.load(self.data)['app_key'],'own-key')
        finally:callback.shutdown();callback.server_close()
    def test_quick_connect_replaces_obsolete_local_port(self):
        callback=desktop_runtime.start_callback(self.data,8765)
        try:
            shop.save(self.data,{**self.cfg,'redirect_url':'http://127.0.0.1:54321/'})
            with patch('webbrowser.open'):
                shop.connect_from_fields(self.data,{'app_key':'own-key','app_secret':'own-secret','authorization_link':'https://services.us.tiktokshop.com/open/authorize?service_id=own-service'})
            self.assertEqual(shop.load(self.data)['redirect_url'],'http://127.0.0.1:8765/')
            self.assertFalse(shop.public_status(self.data)['authorized'])
        finally:callback.shutdown();callback.server_close()
    def test_homepage_and_api_callback_accept_real_http_with_one_time_state(self):
        import server
        http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        callback=desktop_runtime.start_callback(self.data,http.server_port)
        opener=urllib.request.build_opener(shop.NoRedirect())
        def visit(path,headers=None):
            request=urllib.request.Request(shop.RUNTIME['ui_url'].rstrip('/')+path,headers=headers or {})
            try:opener.open(request,timeout=5)
            except urllib.error.HTTPError as response:
                return response.code,response.headers.get('Location','')
            self.fail('Expected redirect or denial')
        try:
            with patch.object(server,'DATA',self.data),patch('webbrowser.open') as browser:
                for path in ('/','/api/shop/callback'):
                    result=shop.connect_from_fields(self.data,{'app_key':'key','app_secret':'secret','authorization_link':'https://services.us.tiktokshop.com/open/authorize?service_id=service'})
                    state=urllib.parse.parse_qs(urllib.parse.urlsplit(result['url']).query)['state'][0]
                    query='?'+urllib.parse.urlencode({'state':state,'code':'fake-code'})
                    with patch.object(shop,'request') as network:
                        self.assertEqual(visit(path+query,{'Host':'evil.invalid'})[0],403)
                        self.assertIn('shop_error=',visit(path+query+'&state=extra')[1])
                        network.assert_not_called()
                    shops={'shops':[{'id':'123','cipher':'cipher','region':'US','name':'Fake shop'}]}
                    with patch.object(shop,'request',side_effect=[self.token,shops]):
                        code,target=visit(path+query,{'Sec-Fetch-Site':'cross-site'})
                    self.assertEqual(code,303);self.assertIn('shop_connected=1',target)
                    self.assertEqual(shop.load(self.data)['last_callback_url'],shop.RUNTIME['ui_url'].rstrip('/')+path)
                    self.assertIn('shop_error=',visit(path+query)[1])
                browser.assert_not_called()
        finally:callback.shutdown();callback.server_close();http.shutdown();http.server_close()
    def test_scope_checks_do_not_send_unauthorized_api(self):
        self.authorized();self.cfg['token']['granted_scopes']=['seller.product.basic'];shop.save(self.data,self.cfg)
        with patch.object(shop,'request') as request:
            with self.assertRaisesRegex(shop.ShopError,'seller.product.write'):
                shop.api(self.data,'/product/202309/products',payload={})
            request.assert_not_called()
        self.assertEqual(shop.required_scope('/product/202309/images/upload',True),'seller.product.basic')
        self.assertEqual(shop.required_scope('/logistics/202309/global_warehouses'),'seller.shop.info')
        self.assertEqual(shop.required_scope('/product/202507/global_listing_rules'),'seller.product.basic')
    def test_public_status_has_no_secret_token_or_cipher(self):
        self.authorized();text=json.dumps(shop.public_status(self.data))
        for value in ('test-secret-private','test-access-private','test-refresh-private','private-cipher'):
            self.assertNotIn(value,text)
    def test_expired_or_failed_connection_is_not_authorized(self):
        self.authorized()
        self.assertTrue(shop.public_status(self.data)['authorized'])
        self.cfg['token']['access_token_expire_in']=int(time.time())-1
        shop.save(self.data,self.cfg)
        self.assertFalse(shop.public_status(self.data)['authorized'])
        self.cfg['token']['access_token_expire_in']=int(time.time())+3600
        shop._migrate_legacy(self.data,self.cfg)
        self.cfg['connections'][0]['status']='api_error'
        shop.save(self.data,self.cfg)
        self.assertFalse(shop.public_status(self.data)['authorized'])
    def test_real_http_error_redacts_secret_code_and_request_id(self):
        cfg={**self.cfg,'token':self.token,'redact_values':['fake-code']}
        result={'code':105005,'message':'test-secret-private test-access-private fake-code','request_id':'test-refresh-private'}
        class ApiHandler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(403);self.end_headers();self.wfile.write(json.dumps(result).encode())
        http=ThreadingHTTPServer(('127.0.0.1',0),ApiHandler);threading.Thread(target=http.serve_forever,daemon=True).start()
        try:
            with self.assertRaises(shop.ShopError) as error:shop.request('http://127.0.0.1:%d/test'%http.server_port,cfg)
            for value in ('test-secret-private','test-access-private','test-refresh-private','fake-code'):self.assertNotIn(value,str(error.exception))
        finally:http.shutdown();http.server_close()
    def test_partial_edit_pins_shop_revision_and_preserves_other_fields(self):
        self.authorized();product={'id':'234','title':'Before','description':'<p>Details</p>','status':'ACTIVATE'}
        with patch.object(shop,'api',return_value=product) as api:
            viewed=remote_products.handle(self.data,'products-get',{'product_id':'234'})
            body={'product_id':'234','shop_id':'123','revision':viewed['revision'],'confirmed':True,'title':'After','description':'<p>Details</p>'}
            with self.assertRaisesRegex(shop.ShopError,'店铺已改变'):remote_products.handle(self.data,'products-edit',{**body,'shop_id':'other'})
            with self.assertRaisesRegex(shop.ShopError,'商品已变化'):remote_products.handle(self.data,'products-edit',{**body,'revision':'stale'})
            remote_products.handle(self.data,'products-edit',body)
            self.assertEqual(api.call_args.kwargs['payload'],{'title':'After','category_version':'v2','save_mode':'LISTING'})
            remote_products.handle(self.data,'products-edit',{**body,'update_description':True,'description_text':'Cotton & linen\nSoft fabric'})
            self.assertEqual(api.call_args.kwargs['payload']['description'],'<p>Cotton &amp; linen</p><p>Soft fabric</p>')
    def test_http_callback_validates_host_duplicate_and_one_time_state(self):
        callback=desktop_runtime.start_callback(self.data,8765)
        self.cfg['redirect_url']=shop.RUNTIME['callback_url']
        state=self.state()
        opener=urllib.request.build_opener(shop.NoRedirect())
        def callback_result(query,host=None):
            request=urllib.request.Request(shop.RUNTIME['callback_url']+'?'+query,headers={'Host':host} if host else {})
            try:opener.open(request,timeout=5)
            except urllib.error.HTTPError as response:
                self.assertEqual(response.code,303)
                self.assertEqual(response.headers['Referrer-Policy'],'no-referrer')
                return response.headers['Location']
            self.fail('Expected browser redirect')
        try:
            query=urllib.parse.urlencode({'state':state,'code':'private-fake-code'})
            with patch.object(shop,'request') as network:
                self.assertIn('shop_error=',callback_result(query,'evil.invalid'))
                self.assertIn('shop_error=',callback_result(query+'&state=other'))
                network.assert_not_called()
            shops={'shops':[{'id':'123','cipher':'cipher','name':'Store','region':'US'}]}
            with patch.object(shop,'request',side_effect=[self.token,shops]):
                self.assertEqual(callback_result(query),'http://127.0.0.1:8765/?shop_connected=1#shop-management')
            self.assertIn('shop_error=',callback_result(query))
            self.assertTrue(shop.public_status(self.data)['authorized'])
        finally:callback.shutdown();callback.server_close()
    def test_post_rejects_foreign_origin_and_dns_rebinding(self):
        from types import SimpleNamespace
        h=SimpleNamespace(headers={'Host':'127.0.0.1:8765','Origin':'https://evil.invalid'},server=SimpleNamespace(server_port=8765),path='/api/shop/config')
        self.assertFalse(shop.local_request_allowed(h,post=True))
        h.headers={'Host':'evil.invalid:8765'}
        self.assertFalse(shop.local_request_allowed(h,post=True))
        h.headers={'Host':'127.0.0.1:8765','Origin':'http://127.0.0.1:8765'}
        self.assertTrue(shop.local_request_allowed(h,post=True))
    def test_desktop_entry_requests_default_browser_and_locks_single_instance(self):
        import desktop
        import types
        fake=types.SimpleNamespace(run=unittest.mock.Mock())
        with patch.dict(os.environ,{'TKSHANGJIA_DATA_DIR':str(self.data)}),patch.dict(sys.modules,{'server':fake}),patch.object(sys,'argv',['TKShangjia']),patch.object(sys,'stdout',sys.stdout),patch.object(sys,'stderr',sys.stderr):
            desktop.main()
        fake.run.assert_called_once_with(open_browser=True)

    def test_duplicate_desktop_opens_fixed_address(self):
        import desktop
        runtime_paths.write_runtime(self.data,{'port':8765})
        with patch.dict(os.environ,{'TKSHANGJIA_DATA_DIR':str(self.data)}),patch.object(platform_support,'lock_file',side_effect=OSError),patch.object(sys,'argv',['TKShangjia']),patch('webbrowser.open') as browser:
            desktop.main()
        browser.assert_called_once_with('http://127.0.0.1:8765/')

    def test_duplicate_legacy_port_requires_restart(self):
        import desktop
        runtime_paths.write_runtime(self.data,{'port':54321})
        with patch.dict(os.environ,{'TKSHANGJIA_DATA_DIR':str(self.data)}),patch.object(platform_support,'lock_file',side_effect=OSError),patch.object(sys,'argv',['TKShangjia','--no-browser']),patch('webbrowser.open') as browser:
            with self.assertRaises(SystemExit) as failure:desktop.main()
        self.assertEqual(failure.exception.code,1)
        browser.assert_not_called()
        self.assertIn('退出旧版',(self.data/'logs'/'desktop.log').read_text())

    def test_english_listing_does_not_need_ai_and_preserves_manual_content(self):
        import shop_listing
        source={'description':'Source text','stocks':{}}
        product={'title':'Original','variants':[],'images':[]}
        form={'content_language':'en','title_en':'Manual title','description_en':'Manual description','highlights_en':'Cotton'}
        reused=shop_listing.reuse_source_fields(form,source,product)
        self.assertEqual(reused['description_en'],'Manual description')
        self.assertNotIn('highlights_en',reused)
        self.assertEqual(shop_listing.saved_listing_content(form,source,product),('Manual title','Manual description'))
        with self.assertRaisesRegex(shop.ShopError,'英文文案'):
            shop_listing.saved_listing_content({**form,'title_en':'中文'},source,product)

    def test_corrupt_image_does_not_pass_upload_validation(self):
        with self.assertRaises(ValueError):platform_support.image_dimensions(b'not-an-image')

if __name__=='__main__':unittest.main()
