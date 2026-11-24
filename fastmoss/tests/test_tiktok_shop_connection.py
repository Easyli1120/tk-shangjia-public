import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tiktok_shop as shop


class TikTokShopConnectionTests(unittest.TestCase):
    def test_new_connection_seals_secret_and_reports_real_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = pathlib.Path(tmp)
            shop.save(data, {
                'app_key': 'app', 'app_secret': 'secret', 'service_id': 'service',
                'connections': [{
                    'id': 'c1', 'label': '美国店铺 A', 'shop_id': 'shop-1', 'name': '真实店铺',
                    'region': 'US', 'status': 'connected', 'granted_scopes': list(shop.REQUIRED_SCOPES),
                    'token_enc': shop._seal(data, {'access_token': 'access', 'refresh_token': 'refresh',
                                                   'access_token_expire_in': time.time() + 3600}),
                }], 'selected_shop_id': 'shop-1', 'active_connection_id': 'c1',
            })
            raw = (data / 'credentials' / 'tiktok-shop.enc').read_text()
            self.assertNotIn('"app_secret": "secret"', raw)
            self.assertNotIn('"access_token": "access"', raw)
            status = shop.public_status(data)
            self.assertEqual(status['stores'][0]['shop_id'], 'shop-1')
            self.assertEqual(status['stores'][0]['status']['code'], 'connected')
            self.assertNotIn('token', status)

    def test_authorize_state_is_single_use_and_exchange_creates_pending_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = pathlib.Path(tmp)
            shop.save(data, {'app_key': 'app', 'app_secret': 'secret', 'service_id': 'service'})
            with patch.object(shop.secrets, 'token_urlsafe', return_value='state-1'):
                shop.authorize_url(data, {'label': '美国店铺 A', 'market': 'US'})
            token = {'access_token': 'access', 'refresh_token': 'refresh', 'user_type': 0,
                     'granted_scopes': list(shop.REQUIRED_SCOPES), 'access_token_expire_in': time.time() + 3600,
                     'refresh_token_expire_in': time.time() + 7200}
            with patch.object(shop, 'request', return_value=token):
                result = shop.exchange(data, {'state': 'state-1', 'code': 'code-1'})
            self.assertTrue(result['ok'])
            status = shop.public_status(data)
            self.assertEqual(status['stores'][0]['status']['code'], 'authorizing')
            with self.assertRaisesRegex(shop.ShopError, '授权状态无效'):
                shop.exchange(data, {'state': 'state-1', 'code': 'code-1'})

    def test_revoke_does_not_reuse_another_shop_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = pathlib.Path(tmp)
            connections = []
            for shop_id, token in [('one', 'token-one'), ('two', 'token-two')]:
                connections.append({'id': shop_id, 'label': shop_id, 'shop_id': shop_id, 'name': shop_id,
                                    'region': 'US', 'status': 'connected', 'granted_scopes': list(shop.REQUIRED_SCOPES),
                                    'token_enc': shop._seal(data, {'access_token': token, 'refresh_token': 'r'})})
            shop.save(data, {'app_key': 'app', 'app_secret': 'secret', 'service_id': 'service',
                             'connections': connections, 'selected_shop_id': 'one', 'active_connection_id': 'one'})
            shop.revoke(data, 'one')
            cfg = shop.load(data)
            self.assertEqual(cfg.get('selected_shop_id'), 'two')
            self.assertEqual(shop._connection_token(data, next(x for x in cfg['connections'] if x['shop_id'] == 'two'))['access_token'], 'token-two')
            self.assertEqual(shop.public_status(data)['stores'][0]['status']['code'], 'revoked')


if __name__ == '__main__':
    unittest.main()
