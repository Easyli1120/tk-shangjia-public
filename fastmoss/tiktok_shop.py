"""Server-side TikTok Shop OAuth and signed API transport. No browser secrets."""
import base64
import copy
import sys
import secure_storage
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
import threading
import functools
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = 'https://open-api.tiktokglobalshop.com'
AUTH = 'https://auth.tiktok-shops.com/api/v2/token/'
CALLBACK = 'http://127.0.0.1:8765/api/shop/callback'

# The API only returns the scopes that the seller actually granted.  Keep this
# list deliberately small: requesting unrelated scopes is both confusing for
# employees and can make Partner Center review harder.
REQUIRED_SCOPES = (
    'seller.authorization.info',
    'seller.product.basic',
    'seller.product.write',
    'seller.logistics',
)
DEVELOPER_TYPES = {
    'seller_custom': 'Seller Developer / Custom App（仅自己的店铺）',
    'system_integrator': 'System Integrator / Public App（多卖家）',
    'unknown': '尚未确认应用类型',
}
STATUS_TEXT = {
    'not_connected': ('未连接', '还没有完成 TikTok Shop 授权。'),
    'waiting_authorization': ('等待授权', '已开始连接，请在 TikTok Shop 页面完成授权。'),
    'authorizing': ('授权中', '正在等待 TikTok Shop 返回授权结果。'),
    'connected': ('已连接', '已通过 TikTok Shop API 验证。'),
    'token_expired': ('授权过期', '授权令牌已过期，请重新连接店铺。'),
    'refresh_failed': ('刷新失败', '自动刷新授权失败，请重新连接店铺。'),
    'permission_insufficient': ('权限不足', '应用或本次授权缺少商品管理所需权限。'),
    'shop_unavailable': ('店铺不可访问', '当前账号无法访问这个店铺，请检查账号和店铺权限。'),
    'api_error': ('平台异常', 'TikTok Shop API 暂时不可用，请稍后刷新连接。'),
    'revoked': ('已解除授权', '此店铺授权已解除，需要重新连接。'),
}

class ShopError(ValueError):
    pass

class UncertainError(ShopError):
    """The server may have applied a write; never automatically repeat it."""

_config_lock = threading.RLock()
def locked(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with _config_lock: return fn(*args, **kwargs)
    return wrapped


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def sign(path, params, body, secret, multipart=False):
    text = path + ''.join(k + str(params[k]) for k in sorted(params) if k not in ('sign', 'access_token'))
    if not multipart:
        text += body.decode('utf-8')
    return hmac.new(secret.encode(), (secret + text + secret).encode(), hashlib.sha256).hexdigest()


def _key_path(data):
    return Path(data) / 'tiktok-shop-key'


def _encryption_key(data):
    """Create a per-install key and keep it out of the browser and database."""
    path = _key_path(data)
    try:
        value = path.read_bytes()
        if len(value) >= 32:
            return value[:32]
    except OSError:
        pass
    value = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, value)
    finally:
        os.close(fd)
    return value


def _seal(data, value):
    return secure_storage.seal(data, value) if value else ''


def _unseal(data, value):
    if not value:
        return {}
    if str(value).startswith(('dpapi:', 'aesgcm:')):
        return secure_storage.unseal(data, value)
    try:
        if not str(value).startswith('v1:'):
            return {}
        blob = base64.urlsafe_b64decode(str(value)[3:].encode())
        nonce, encrypted, mac = blob[:16], blob[16:-32], blob[-32:]
        key = _encryption_key(data)
        if not hmac.compare_digest(mac, hmac.new(key, nonce + encrypted, hashlib.sha256).digest()):
            return {}
        stream = b''.join(hashlib.sha256(key + nonce + i.to_bytes(4, 'big')).digest()
                          for i in range((len(encrypted) + 31) // 32))[:len(encrypted)]
        return json.loads(bytes(a ^ b for a, b in zip(encrypted, stream)).decode())
    except (ValueError, TypeError, OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _status(code):
    label, explanation = STATUS_TEXT.get(code, STATUS_TEXT['api_error'])
    return {'code': code, 'label': label, 'explanation': explanation}


def _scope_info(scopes):
    granted = [str(x) for x in scopes or []]
    missing = [x for x in REQUIRED_SCOPES if x not in granted]
    if 'seller.fs.order&fufillment.management' in granted and 'seller.authorization.info' in missing:
        missing.remove('seller.authorization.info')
    return {'granted': granted, 'missing': missing,
            'ok': not missing, 'label': '正常' if not missing else '缺少：' + '、'.join(missing)}


def _connection_token(data, connection):
    if not isinstance(connection, dict):
        return {}
    if connection.get('token_enc'):
        return _unseal(data, connection['token_enc'])
    # Legacy records from the first implementation kept the token in the root
    # JSON object.  They remain readable and are migrated on the next save.
    return connection.get('token') or {}


def _hydrate(data, cfg):
    """Load the active connection into the compatibility token fields."""
    if not cfg.get('app_secret') and cfg.get('app_secret_enc'):
        cfg['app_secret'] = _unseal(data, cfg.get('app_secret_enc')) or ''
    connections = cfg.get('connections')
    if not isinstance(connections, list):
        return cfg
    active_id = cfg.get('active_connection_id', '')
    selected = cfg.get('selected_shop_id', '')
    active = next((x for x in connections if x.get('id') == active_id), None)
    if active is None and selected:
        active = next((x for x in connections if str(x.get('shop_id')) == str(selected)), None)
    if active is None:
        active = next((x for x in connections if x.get('status') in ('connected', 'authorizing')), None)
    if active:
        cfg['active_connection_id'] = active.get('id', '')
        cfg['token'] = _connection_token(data, active)
        if active.get('shop_id') and not selected:
            cfg['selected_shop_id'] = active['shop_id']
    return cfg


def credential_path(data):
    return Path(data) / 'credentials' / 'tiktok-shop.enc'


def load(data):
    path = credential_path(data)
    if path.exists():
        cfg = secure_storage.unseal(data, path.read_text(encoding='utf-8'))
        return _hydrate(data, cfg)
    legacy = Path(data) / 'tiktok-shop-private.json'
    if not legacy.exists(): return {}
    cfg = json.loads(legacy.read_text(encoding='utf-8'))
    cfg = _hydrate(data, cfg)
    save(data, cfg)
    legacy.unlink()  # Complete migration; never leave plaintext credentials behind.
    return cfg


def save(data, cfg):
    path = credential_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    disk_cfg = copy.deepcopy(cfg)
    if isinstance(disk_cfg.get('connections'), list):
        disk_cfg.pop('token', None)
    sealed = secure_storage.seal(data, disk_cfg)
    fd, name = tempfile.mkstemp(prefix='.shop-', dir=path.parent)
    try:
        if hasattr(os, 'fchmod'): os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(sealed); file.flush(); os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def _legacy_connections(data, cfg):
    """Expose the old single-token config as cards until it is reauthorized."""
    token = cfg.get('token') or {}
    shops = cfg.get('shops') or []
    if not shops and token.get('access_token'):
        shops = [{'id': cfg.get('selected_shop_id', ''), 'name': token.get('seller_name', ''),
                  'region': cfg.get('authorization_region', 'US')}]
    result = []
    for shop in shops:
        if not shop.get('id'):
            continue
        scope = _scope_info(token.get('granted_scopes'))
        code = 'connected' if token.get('access_token') else 'not_connected'
        if not scope['ok'] and token.get('access_token'):
            code = 'permission_insufficient'
        result.append({
            'id': 'legacy-' + str(shop['id']), 'label': shop.get('name') or shop['id'],
            'shop_id': shop.get('id'), 'name': shop.get('name') or shop['id'],
            'region': shop.get('region') or '', 'seller_type': shop.get('seller_type', ''),
            'status': _status(code), 'scope_status': scope,
            'token_status': '正常' if token.get('access_token') else '未获得令牌',
            'connected_at': cfg.get('last_verified_at'), 'last_verified_at': cfg.get('last_verified_at'),
            'last_error': '', 'owner': '本机工作区',
        })
    return result


def _migrate_legacy(data, cfg):
    """Move the first-generation single-token record into per-shop storage."""
    if isinstance(cfg.get('connections'), list):
        return cfg['connections']
    token = cfg.get('token') or {}
    shops = cfg.get('shops') or []
    connections = []
    for shop in shops:
        if not shop.get('id'):
            continue
        connections.append({
            'id': 'legacy-' + str(shop['id']), 'label': shop.get('name') or shop['id'],
            'shop_id': shop['id'], 'name': shop.get('name') or shop['id'],
            'region': shop.get('region') or '', 'seller_type': shop.get('seller_type', ''),
            'status': 'connected' if token.get('access_token') else 'not_connected',
            'granted_scopes': list(token.get('granted_scopes') or []),
            'connected_at': cfg.get('last_verified_at'), 'last_verified_at': cfg.get('last_verified_at'),
            'last_error': '', 'owner': '本机工作区', 'token_enc': _seal(data, token) if token else '',
        })
    cfg['connections'] = connections
    if cfg.get('selected_shop_id'):
        cfg['active_connection_id'] = 'legacy-' + str(cfg['selected_shop_id'])
    return connections


def _public_connection(data, connection):
    token = _connection_token(data, connection)
    scope = _scope_info(token.get('granted_scopes', connection.get('granted_scopes', [])))
    code = connection.get('status') or ('connected' if token.get('access_token') else 'not_connected')
    now = time.time()
    if code == 'connected' and token.get('access_token_expire_in', 0) and token.get('access_token_expire_in') <= now:
        code = 'token_expired' if token.get('refresh_token_expire_in', 0) <= now else 'refresh_failed'
    if code == 'connected' and not scope['ok']:
        code = 'permission_insufficient'
    return {
        'id': connection.get('id', ''), 'label': connection.get('label') or connection.get('name') or connection.get('shop_id', ''),
        'shop_id': connection.get('shop_id', ''), 'name': connection.get('name') or connection.get('shop_id', ''),
        'region': connection.get('region') or '', 'seller_type': connection.get('seller_type', ''),
        'status': _status(code), 'scope_status': scope,
        'token_status': ('未获得凭证' if not token.get('access_token') else '已到期，需刷新' if token.get('access_token_expire_in',0)<=now else '有效期内，仍需接口验证'),
        'connected_at': connection.get('connected_at'), 'last_verified_at': connection.get('last_verified_at'),
        'last_error': connection.get('last_error', ''), 'owner': connection.get('owner', '本机工作区'),
    }


def public_status(data):
    cfg = load(data)
    token = cfg.get('token', {})
    connections = cfg.get('connections')
    stores = [_public_connection(data, x) for x in connections if isinstance(x, dict)] if isinstance(connections, list) else _legacy_connections(data, cfg)
    selected = str(cfg.get('selected_shop_id', ''))
    connected = [x for x in stores if x['status']['code'] == 'connected']
    abnormal = [x for x in stores if x['status']['code'] not in ('connected', 'not_connected')]
    pending = [x for x in stores if x['status']['code'] in ('not_connected', 'waiting_authorization', 'authorizing')]
    developer_type = cfg.get('developer_type', 'seller_custom')
    if developer_type not in DEVELOPER_TYPES:
        developer_type = 'unknown'
    multi_ready = bool(cfg.get('multi_seller_authorized')) and developer_type == 'system_integrator'
    assessment = {
        'type': developer_type, 'label': DEVELOPER_TYPES[developer_type],
        'multi_seller_supported': multi_ready,
        'state': 'ready' if multi_ready else 'single_seller_only' if developer_type == 'seller_custom' else 'needs_admin_review',
        'message': ('当前代码使用 Seller Developer / Custom App 路线，只能连接应用所有者自己的店铺。'
                    if developer_type == 'seller_custom' else
                    '应用类型尚未确认；请管理员在 Partner Center 核对后保存。' if developer_type == 'unknown' else
                    '当前应用尚未确认已通过多卖家授权和审核，员工连接其他店铺前请先完成发布/审核。'),
        'missing_conditions': ([] if multi_ready else
                               ['System Integrator / Public App 应用类型', '多卖家授权资格与 Partner Center 审核'] if developer_type != 'seller_custom'
                               else ['System Integrator / Public App 应用类型', '多卖家授权资格与应用审核']),
    }
    return dict(
        app_key=cfg.get('app_key', ''), service_id=cfg.get('service_id', ''),
        authorization_region=cfg.get('authorization_region', 'US'), redirect_url=cfg.get('redirect_url') or RUNTIME.get('callback_url',''),
        secret_configured=bool(cfg.get('app_secret')), configured=bool(cfg.get('app_key') and cfg.get('app_secret') and cfg.get('service_id')),
        authorized=bool(token.get('access_token') and token.get('access_token_expire_in',0)>time.time()
                        and cfg.get('last_verified_at') and cfg.get('shops')
                        and any(x['status']['code'] in ('connected','permission_insufficient')
                                and (not selected or str(x['shop_id'])==selected) for x in stores)),
        access_expires_at=token.get('access_token_expire_in'),
        refresh_expires_at=token.get('refresh_token_expire_in'), seller_name=token.get('seller_name', ''),
        granted_scopes=token.get('granted_scopes', []), shops=[{k: s.get(k) for k in ('id', 'name', 'region', 'seller_type')} for s in cfg.get('shops', [])],
        selected_shop_id=selected, active_connection_id=cfg.get('active_connection_id', ''), last_verified_at=cfg.get('last_verified_at'),
        stores=stores, stats={'connected': len(connected), 'normal': len(connected), 'abnormal': len(abnormal), 'pending': len(pending)},
        developer_assessment=assessment, local_access={'isolated': True, 'message': '此 Windows 用户的本机独立工作区；不连接开发者电脑。'},
        callback=callback_status(cfg), storage='Windows DPAPI' if sys.platform=='win32' else 'AES-GCM（本机文件权限保护）',
        app_verified_at=cfg.get('app_verified_at'), last_error=cfg.get('last_error',''),
        permissions_confirmed=bool(cfg.get('permissions_confirmed')),
        authorization_pending=bool(cfg.get('oauth_state',{}).get('expires',0)>time.time()),
        required_scopes=list(REQUIRED_SCOPES), review_status=cfg.get('review_status', 'private'),
        app_status=cfg.get('app_status', ''), multi_seller_authorized=bool(cfg.get('multi_seller_authorized')),
    )


@locked
def configure(data, body):
    if not isinstance(body,dict): raise ShopError('应用配置格式无效')
    old = load(data)
    cfg = dict(old)
    for key in ('app_key','service_id','authorization_region','redirect_url', 'developer_type',
                'review_status', 'app_status'):
        if key in body: cfg[key] = str(body[key]).strip()
    if body.get('authorization_link'):
        u=urllib.parse.urlsplit(str(body['authorization_link']).strip())
        if u.scheme!='https' or u.hostname not in ('services.us.tiktokshop.com','services.tiktokshop.com') or u.path!='/open/authorize' or u.username or u.password or u.port not in (None,443):
            raise ShopError('请复制 Partner Center 的卖家授权链接，不能使用达人或第三方链接')
        cfg['service_id']=urllib.parse.parse_qs(u.query).get('service_id',[''])[0]
        cfg['authorization_region']='US' if u.hostname=='services.us.tiktokshop.com' else 'ROW'
    if 'multi_seller_authorized' in body:
        cfg['multi_seller_authorized'] = bool(body.get('multi_seller_authorized'))
    if body.get('app_secret'): cfg['app_secret'] = str(body['app_secret']).strip()
    elif cfg.get('app_key') != old.get('app_key'):
        raise ShopError('更换 App Key 时，请同时粘贴同一应用的 App Secret')
    if not all(cfg.get(k) for k in ('app_key','app_secret','service_id')):
        raise ShopError('请先在 TikTok 完成卖家开发者入驻并创建 Custom App，然后填写 App Key、App Secret 和 Service ID')
    if any(len(cfg[k])>512 for k in ('app_key','app_secret','service_id')):
        raise ShopError('应用凭证长度无效')
    if cfg.get('authorization_region','US') not in ('US','ROW'):
        raise ShopError('授权市场必须为 US 或 ROW')
    if cfg.get('developer_type', 'seller_custom') not in DEVELOPER_TYPES:
        raise ShopError('应用类型选择无效')
    redirect = urllib.parse.urlsplit(cfg.get('redirect_url') or RUNTIME.get('callback_url') or CALLBACK)
    if redirect.username or redirect.password or redirect.query or redirect.fragment or (redirect.path!='/api/shop/callback' and redirect.geturl() not in local_callback_urls()):
        raise ShopError('请使用当前软件首页地址或 /api/shop/callback，且不能包含查询参数')
    if not (redirect.scheme=='https' and redirect.hostname or redirect.scheme=='http' and redirect.hostname in ('127.0.0.1','localhost')):
        raise ShopError('回调地址须为 HTTPS 或本机回调地址')
    if redirect.hostname in ('test.com','example.com') or (redirect.hostname or '').endswith('.example'):
        raise ShopError('请使用本机生成的地址或自己准备的真实 HTTPS 回调地址')
    if redirect.scheme=='http' and RUNTIME.get('callback_url') and redirect.geturl() not in local_callback_urls():
        raise ShopError('本地地址已变化，请复制软件当前生成的回调地址')
    cfg['redirect_url'] = redirect.geturl()
    cfg['redirect_confirmed_url'] = cfg['redirect_url'] if body.get('redirect_confirmed') is True else ''
    cfg['permissions_confirmed'] = body.get('permissions_confirmed') is True
    if any(cfg.get(k)!=old.get(k) for k in ('app_key','app_secret','service_id','authorization_region','redirect_url')):
        for k in ('token','shops','selected_shop_id','active_connection_id','connections','oauth_state','last_verified_at','last_callback_url','callback_verified_at','app_verified_at'): cfg.pop(k,None)
    save(data,cfg)
    return public_status(data)


@locked
def connect_from_fields(data, body):
    """The three copied values are sufficient to start, never to prove authorization."""
    values = {k: str(body.get(k) or '').strip() for k in ('authorization_link','app_key','app_secret')}
    if not values['authorization_link'] or not values['app_key']:
        raise ShopError('请粘贴步骤13中的授权链接、App Key 和 App Secret')
    old = load(data)
    redirect = old.get('redirect_url','')
    if redirect not in local_callback_urls() and not redirect.startswith('https://'):
        redirect = RUNTIME.get('ui_url') or RUNTIME.get('callback_url')
    if not redirect:
        raise ShopError('本机连接服务尚未启动，请重新打开软件')
    configure(data, {**values, 'redirect_url':redirect,
                    'redirect_confirmed':old.get('redirect_confirmed_url')==redirect,
                    'permissions_confirmed':old.get('permissions_confirmed') is True})
    cfg = load(data); cfg['connection_mode']='quick'; save(data,cfg)
    preflight(data, require_confirmation=False)
    return authorize_url(data)


def request(url, cfg, body=None, headers=None, method='GET'):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=35) as response:
            raw=response.read(12*1024*1024+1)
        if len(raw)>12*1024*1024: raise UncertainError('TikTok 响应过大，需核对提交结果')
        result=json.loads(raw)
    except UncertainError:
        raise
    except urllib.error.HTTPError as e:
        # TikTok returns actionable platform errors (including missing scopes)
        # with non-2xx HTTP status codes. Preserve the code without leaking tokens.
        try:
            result=json.loads(e.read(65536))
        except (ValueError,UnicodeError,OSError):
            result=None
        if not isinstance(result,dict) or not result.get('code'):
            suffix='；写入结果需在商家后台核对' if method!='GET' else '，请检查连接与应用权限'
            raise UncertainError('TikTok 请求失败（HTTP %s）'%e.code+suffix) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UncertainError('无法连接 TikTok API；写入结果不确定，请勿重复提交') from None
    except (ValueError,UnicodeError):
        raise UncertainError('TikTok 返回了无法解析的响应，请核对提交结果') from None
    if not isinstance(result,dict): raise UncertainError('TikTok 响应格式无效')
    if result.get('code')!=0:
        msg=str(result.get('message','未知错误'))
        request_id=str(result.get('request_id','未知'))
        error_code=str(result.get('code','未知'))
        for value in [cfg.get('app_secret'),*(cfg.get('redact_values') or []),*[(cfg.get('token') or {}).get(k) for k in ('access_token','refresh_token')]]:
            if value:
                msg=msg.replace(str(value),'[已隐藏]')
                request_id=request_id.replace(str(value),'[已隐藏]')
                error_code=error_code.replace(str(value),'[已隐藏]')
        error=UncertainError if str(result.get('code'))=='36009003' or 'internal error' in msg.lower() else ShopError
        if str(result.get('code'))=='105005':
            msg='TikTok 拒绝此接口的权限校验。请核对应用权限及令牌授权范围；缺少权限时开通并重新授权。若两者均已包含所需权限，请将下方请求号提供给 TikTok 支持排查。'+msg
        raise error('TikTok 错误 %s：%s（请求号 %s）'%(error_code[:40],msg[:600],request_id[:120]))
    return result.get('data') or {}


@locked
def authorize_url(data, details=None):
    cfg=load(data)
    if not all(cfg.get(k) for k in ('app_key','app_secret','service_id')): raise ShopError('请先保存应用配置')
    state=secrets.token_urlsafe(32)
    details = details if isinstance(details, dict) else {}
    label = str(details.get('label') or details.get('name') or '').strip()[:80]
    market = str(details.get('market') or 'US').strip().upper()
    if market not in ('US','ROW'):
        raise ShopError('请选择美国或其他市场授权入口')
    _migrate_legacy(data, cfg)
    cfg['oauth_state']={'hash':hashlib.sha256(state.encode()).hexdigest(),'expires':time.time()+1800,
                        'label': label, 'market': market, 'created_at': int(time.time())}
    save(data,cfg)
    domain='services.us.tiktokshop.com' if cfg.get('authorization_region','US')=='US' else 'services.tiktokshop.com'
    return {'url':'https://'+domain+'/open/authorize?'+urllib.parse.urlencode({'service_id':cfg['service_id'],'state':state}),
            'redirect_url':cfg.get('redirect_url',CALLBACK)}


@locked
def exchange(data, params, callback_url=None):
    cfg=load(data)
    pending=cfg.get('oauth_state',{})
    state=str(params.get('state',''))
    if not state or pending.get('expires',0)<time.time() or not hmac.compare_digest(pending.get('hash',''),hashlib.sha256(state.encode()).hexdigest()):
        raise ShopError('授权状态无效或过期，请从本网页重新发起授权')
    cfg.pop('oauth_state',None)
    save(data,cfg)
    code=params.get('code') or params.get('auth_code')
    if params.get('error') or not code or code=='null': raise ShopError('店铺未授权，请重新连接')
    query={'app_key':cfg['app_key'],'app_secret':cfg['app_secret'],'auth_code':code,'grant_type':'authorized_code'}
    if params.get('app_key') and params['app_key']!=cfg['app_key']:
        raise ShopError('回调应用与当前配置不一致，请重新发起授权')
    result=request(AUTH+'get?'+urllib.parse.urlencode(query),{**cfg,'redact_values':[code]})
    if not result.get('access_token') or not result.get('refresh_token'): raise ShopError('授权响应未包含有效令牌')
    if result.get('user_type') not in (0,4,5): raise ShopError('授权账号不是卖家账号，请使用店铺主账号')
    cfg['token']=result
    if callback_url and cfg.get('connection_mode')=='quick': cfg['redirect_url']=callback_url
    cfg['last_callback_url']=callback_url or cfg.get('redirect_url','')
    cfg['callback_verified_at']=int(time.time())
    label = str(pending.get('label') or result.get('seller_name') or 'TikTok Shop 店铺').strip()[:80]
    connection = {
        'id': secrets.token_urlsafe(12), 'label': label, 'shop_id': '', 'name': '',
        'region': pending.get('market', 'US'), 'seller_type': '', 'status': 'authorizing',
        'granted_scopes': list(result.get('granted_scopes') or []), 'connected_at': int(time.time()),
        'last_verified_at': None, 'last_error': '', 'owner': '本机工作区',
        'token_enc': _seal(data, result),
    }
    connections = _migrate_legacy(data, cfg)
    connections.append(connection)
    cfg['connections'] = connections
    cfg['active_connection_id'] = connection['id']
    cfg.pop('shops', None)
    cfg.pop('selected_shop_id', None)
    cfg.pop('last_verified_at', None)
    _audit(cfg, 'authorize_completed', '')
    save(data,cfg)
    return {'ok':True, 'connection_id': connection['id'], 'label': label}


def _active_connection(cfg):
    connections = cfg.get('connections')
    if not isinstance(connections, list):
        return None
    active_id = cfg.get('active_connection_id', '')
    selected = cfg.get('selected_shop_id', '')
    return (next((x for x in connections if x.get('id') == active_id), None)
            or next((x for x in connections if str(x.get('shop_id')) == str(selected)), None))


def _persist_active_token(data, cfg):
    connection = _active_connection(cfg)
    if connection is not None:
        connection['token_enc'] = _seal(data, cfg.get('token', {}))
        connection['granted_scopes'] = list((cfg.get('token') or {}).get('granted_scopes') or [])


def _mark_connection(data, cfg, status, error=''):
    connection = _active_connection(cfg)
    if connection is not None:
        connection['status'] = status
        connection['last_error'] = error
        save(data, cfg)


def _audit(cfg, action, shop_id=''):
    log = cfg.get('audit_log') if isinstance(cfg.get('audit_log'), list) else []
    log.append({'action': action, 'shop_id': str(shop_id or ''), 'at': int(time.time()), 'operator': '本机工作区'})
    cfg['audit_log'] = log[-100:]


@locked
def token_config(data, force=False):
    cfg=load(data)
    token=cfg.get('token',{})
    if not token.get('access_token'): raise ShopError('尚未获得店铺授权，请先连接店铺')
    if force or token.get('access_token_expire_in',0)<=time.time()+300:
        if not token.get('refresh_token') or token.get('refresh_token_expire_in',0)<=time.time():
            _mark_connection(data, cfg, 'token_expired', '刷新令牌已到期')
            raise ShopError('店铺授权已到期，请重新连接店铺')
        q={'app_key':cfg['app_key'],'app_secret':cfg['app_secret'],'refresh_token':token['refresh_token'],'grant_type':'refresh_token'}
        try:
            updated=request(AUTH+'refresh?'+urllib.parse.urlencode(q),cfg)
        except ShopError:
            _mark_connection(data, cfg, 'refresh_failed', 'TikTok 返回刷新失败')
            raise ShopError('自动刷新授权失败，请重新连接店铺')
        if not updated.get('access_token') or not updated.get('refresh_token'): raise ShopError('刷新令牌失败，请重新授权')
        if updated.get('user_type',token.get('user_type')) not in (0,4,5): raise ShopError('刷新返回的账号类型不匹配，请重新授权')
        if updated.get('open_id') and token.get('open_id') and updated['open_id']!=token['open_id']: raise ShopError('刷新返回的账号不匹配，请重新授权')
        cfg['token']={**token,**updated, 'granted_scopes':updated.get('granted_scopes',[])}
        _persist_active_token(data, cfg)
        save(data,cfg)
    return cfg


@locked
def api(data, path, params=None, payload=None, shop=True, binary=None, content_type='application/json', context=None, method=None):
    cfg=token_config(data)
    if context and any(str(cfg.get(k,''))!=str(context.get(k,'')) for k in ('app_key','service_id','selected_shop_id')):
        raise ShopError('目标店铺或应用配置已改变，请切回本次任务的店铺后重试')
    require_scope(cfg, path, payload is not None or binary is not None, method)
    q=dict(params or {},app_key=cfg['app_key'],timestamp=int(time.time()))
    if shop:
        selected=next((s for s in cfg.get('shops',[]) if s['id']==cfg.get('selected_shop_id')),None)
        if not selected: raise ShopError('请先选择已授权的目标店铺')
        q['shop_cipher']=selected['cipher']
    raw=binary if binary is not None else json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode() if payload is not None else b''
    q['sign']=sign(path,q,raw,cfg['app_secret'],content_type.startswith('multipart/form-data'))
    return request(API+path+'?'+urllib.parse.urlencode(q),cfg,raw if payload is not None or binary is not None else None,
        {'Content-Type':content_type,'x-tts-access-token':cfg['token']['access_token']},
        method or ('POST' if payload is not None or binary is not None else 'GET'))


@locked
def sync_shops(data):
    try:
        result=api(data,'/authorization/202309/shops',shop=False)
    except ShopError as exc:
        cfg = load(data)
        message = str(exc)
        status = 'shop_unavailable' if 'shop' in message.lower() or '店铺' in message else 'api_error'
        if '105005' in message or '权限' in message:
            status = 'permission_insufficient'
        _mark_connection(data, cfg, status, message[:300])
        raise
    shops=result.get('shops')
    if not isinstance(shops,list): raise ShopError('TikTok 未返回店铺列表')
    cfg=load(data)
    shops=[s for s in shops if s.get('cipher') and s.get('id')]
    if not shops:
        _mark_connection(data,cfg,'shop_unavailable','平台未返回已授权店铺')
        raise ShopError('平台未返回可用店铺，请使用店铺所有者账号重新授权')
    cfg['shops'] = shops
    connections = cfg.get('connections') if isinstance(cfg.get('connections'), list) else []
    active = _active_connection(cfg)
    if active is None:
        active = {'id': secrets.token_urlsafe(12), 'label': cfg.get('seller_name') or 'TikTok Shop 店铺',
                  'region': cfg.get('authorization_region', 'US'), 'owner': '本机工作区'}
        connections.append(active)
    token = cfg.get('token', {})
    for index, shop in enumerate(shops):
        existing = next((x for x in connections if str(x.get('shop_id')) == str(shop['id'])), None)
        target = existing or (active if index == 0 and not active.get('shop_id') else None)
        if target is None:
            target = {'id': secrets.token_urlsafe(12), 'label': active.get('label') or shop.get('name') or shop['id'],
                      'region': shop.get('region') or '', 'owner': active.get('owner', '本机工作区')}
            connections.append(target)
        target.update({'shop_id': shop['id'], 'name': shop.get('name') or shop['id'],
                       'region': shop.get('region') or '',
                       'seller_type': shop.get('seller_type', ''), 'status': 'connected',
                       'last_verified_at': int(time.time()), 'connected_at': target.get('connected_at') or active.get('connected_at') or int(time.time()), 'last_error': '',
                       'granted_scopes': list(token.get('granted_scopes') or []),
                       'token_enc': _seal(data, token)})
    # A previously authorized shop may disappear from the API response. Keep
    # its card so the user can see that it needs attention, but never reuse its
    # token for a different shop.
    returned_ids = {str(x['id']) for x in shops}
    for connection in connections:
        if connection.get('shop_id') and str(connection['shop_id']) not in returned_ids and connection.get('status') == 'connected':
            connection['status'] = 'shop_unavailable'
            connection['last_error'] = 'TikTok 未在已授权店铺列表中返回该店铺'
    cfg['connections'] = [x for x in connections if x.get('shop_id')]
    if len(shops)==1:
        cfg['selected_shop_id']=shops[0]['id']
    elif cfg.get('selected_shop_id') not in [s['id'] for s in shops]:
        cfg.pop('selected_shop_id',None)
    selected = cfg.get('selected_shop_id')
    selected_conn = next((x for x in connections if str(x.get('shop_id')) == str(selected)), None)
    if selected_conn:
        cfg['active_connection_id'] = selected_conn['id']
        cfg['token'] = _connection_token(data, selected_conn)
    cfg['last_verified_at']=int(time.time())
    cfg['app_verified_at']=int(time.time())
    save(data,cfg)
    return public_status(data)


@locked
def select_shop(data, sid):
    cfg=load(data)
    sid = str(sid or '')
    connection = next((x for x in cfg.get('connections', []) if str(x.get('shop_id')) == sid), None)
    if connection is None and sid not in [str(s['id']) for s in cfg.get('shops',[])]: raise ShopError('请选择已授权的店铺')
    if connection and (connection.get('status') == 'revoked' or not _connection_token(data,connection).get('access_token')):
        raise ShopError('此店铺已解除绑定，请重新授权')
    cfg['selected_shop_id']=sid
    if connection:
        cfg['active_connection_id'] = connection['id']
        cfg['token'] = _connection_token(data, connection)
    _audit(cfg, 'select', sid)
    save(data,cfg)
    return public_status(data)


@locked
def revoke(data, sid):
    sid = str(sid or '')
    cfg = load(data)
    connections = cfg.get('connections') if isinstance(cfg.get('connections'), list) else []
    connection = next((x for x in connections if str(x.get('shop_id')) == sid or str(x.get('id')) == sid), None)
    if connection is None:
        raise ShopError('找不到要解除的店铺')
    connection['status'] = 'revoked'
    connection['token_enc'] = ''
    cfg['shops'] = [s for s in cfg.get('shops',[]) if str(s.get('id')) != str(connection.get('shop_id'))]
    connection['last_error'] = '已移除本机授权；如需撤销平台许可，请到 Seller Center 的应用管理中解除'
    if str(cfg.get('selected_shop_id', '')) == str(connection.get('shop_id', '')):
        cfg.pop('selected_shop_id', None)
        cfg.pop('active_connection_id', None)
        cfg.pop('token', None)
    cfg['connections'] = connections
    cfg['audit_log'] = (cfg.get('audit_log') or [])[-99:] + [{'action': 'revoke', 'shop_id': sid, 'at': int(time.time()), 'operator': '本机工作区'}]
    save(data, cfg)
    return public_status(data)


@locked
def verify_connection(data, sid=''):
    cfg = load(data)
    sid = str(sid or cfg.get('selected_shop_id') or '')
    if sid:
        select_shop(data, sid)
    return sync_shops(data)


def local_request_allowed(handler, post=False):
    host=handler.headers.get('Host','')
    if host not in ('127.0.0.1:%d'%handler.server.server_port,'localhost:%d'%handler.server.server_port): return False
    origin=handler.headers.get('Origin')
    if origin and origin!='http://'+host: return False
    if handler.headers.get('Sec-Fetch-Site')=='cross-site' and (post or not is_callback_request(handler.path)): return False
    return True


def handle(handler, server, post=False):
    """Return True if handled. Callback codes never appear in HTTP access logs."""
    parsed=urllib.parse.urlsplit(handler.path)
    if not parsed.path.startswith('/api/shop/'): return False
    try:
        if not local_request_allowed(handler,post):
            handler.respond({'error':'仅允许从本机网页访问店铺接口'},403); return True
        if post:
            length=int(handler.headers.get('Content-Length','0'))
            limit=12*1024*1024 if parsed.path=='/api/shop/listing-size-batch' else 100000
            if not 0<length<=limit or handler.headers.get_content_type()!='application/json': raise ShopError('请求格式无效或内容过大')
            body=json.loads(handler.rfile.read(length))
            if not isinstance(body,dict): raise ShopError('请求格式无效')
            action=parsed.path.rsplit('/',1)[-1]
            if action=='config': result=configure(server.DATA,body)
            elif action=='connect': result=connect_from_fields(server.DATA,body)
            elif action=='authorize':
                preflight(server.DATA, require_confirmation=load(server.DATA).get('connection_mode')!='quick')
                result=authorize_url(server.DATA,body)
            elif action=='sync': result=sync_shops(server.DATA)
            elif action=='select': result=select_shop(server.DATA,body.get('shop_id'))
            elif action=='verify': result=verify_connection(server.DATA,body.get('shop_id',''))
            elif action=='refresh':
                if body.get('shop_id'): select_shop(server.DATA,body['shop_id'])
                token_config(server.DATA,force=True)
                result=sync_shops(server.DATA)
            elif action in ('diagnostics','test-config'): result=diagnostics(server.DATA)
            elif action=='clear-config':
                save(server.DATA,{})
                result=public_status(server.DATA)
            elif action=='products-search': result=api(server.DATA,'/product/202309/products/search',params={'page_size':20,**({'page_token':body['page_token']} if body.get('page_token') else {})},payload={})
            elif action in ('products-get','products-edit'):
                import remote_products
                result=remote_products.handle(server.DATA,action,body)
            elif action=='revoke': result=revoke(server.DATA,body.get('shop_id') or body.get('connection_id'))
            else:
                import shop_listing
                result=shop_listing.post(server,action,body)
        else:
            if parsed.path=='/api/shop/status': result=public_status(server.DATA)
            elif parsed.path=='/api/shop/callback':
                receive_callback(handler, server.DATA, 'http://'+handler.headers.get('Host','')+parsed.path)
                return True
            else:
                import shop_listing
                result=shop_listing.get(server,parsed.path.rsplit('/',1)[-1],{k:v[0] for k,v in urllib.parse.parse_qs(parsed.query).items()})
        handler.respond(result,202 if post and parsed.path in ('/api/shop/listing-prepare-start','/api/shop/listing-prepare-retry') else 200)
    except (ValueError,TypeError) as exc:
        handler.respond({'error':str(exc)},400)
    except Exception:
        handler.respond({'error':'店铺接口处理失败，请检查服务日志或重试'},500)
    return True


RUNTIME = {}


def local_callback_urls():
    ui = RUNTIME.get('ui_url','')
    urls = [x for x in (RUNTIME.get('callback_url'), ui, ui+'api/shop/callback' if ui else '') if x]
    return urls + [x.replace('127.0.0.1','localhost') for x in urls]


def is_callback_request(path):
    parsed = urllib.parse.urlsplit(path)
    return parsed.path=='/api/shop/callback' or (parsed.path=='/' and bool(set(urllib.parse.parse_qs(parsed.query,keep_blank_values=True)) & {'code','auth_code','state','error'}))


def receive_callback(handler, data, actual_url):
    parsed = urllib.parse.urlsplit(handler.path)
    try:
        if len(handler.path)>8192: raise ShopError('授权回调参数过长，请重新连接')
        cfg = load(data)
        expected = cfg.get('redirect_url','')
        allowed = {expected}
        if cfg.get('connection_mode')=='quick' and not expected.startswith('https://'):
            allowed.update(local_callback_urls())
        if actual_url not in allowed:
            raise ShopError('回调地址与当前配置不一致，请核对步骤9的地址后重新连接')
        if actual_url.startswith('https://') and handler.headers.get('X-Forwarded-Proto')!='https':
            raise ShopError('HTTPS 回调转发设置不正确')
        query = urllib.parse.parse_qs(parsed.query,keep_blank_values=True)
        if any(len(v)!=1 for v in query.values()): raise ShopError('授权回调参数重复，请重新连接')
        complete_callback(data, {k:v[0] for k,v in query.items()}, actual_url)
        target = '?shop_connected=1#shop-management'
    except (ValueError,TypeError) as exc:
        target = '?shop_error='+urllib.parse.quote(str(exc)[:300])+'#shop-management'
    except Exception:
        target = '?shop_error='+urllib.parse.quote('回调处理失败，请回到店铺管理重新连接')+'#shop-management'
    handler.send_response(303)
    handler.send_header('Location',RUNTIME.get('ui_url','/')+target)
    handler.send_header('Cache-Control','no-store')
    handler.send_header('Referrer-Policy','no-referrer')
    handler.end_headers()


def callback_status(cfg):
    local = RUNTIME.get('callback_url', '')
    configured = cfg.get('redirect_url', '')
    invalid = urllib.parse.urlsplit(configured).hostname in ('test.com','example.com')
    return {'local_url': local, 'entry_url': RUNTIME.get('ui_url') or local, 'url': configured or local,
            'mode': 'https' if configured.startswith('https://') else 'local',
            'running': bool(local), 'port': RUNTIME.get('callback_port'),
            'invalid': invalid,
            'confirmed': bool(configured and cfg.get('redirect_confirmed_url') == configured),
            'changed': bool(configured.startswith('http://') and local and configured not in local_callback_urls()),
            'verified': bool(configured and cfg.get('last_callback_url') == configured),
            'verified_at': cfg.get('callback_verified_at')}


def probe_callback(url):
    if not RUNTIME.get('probe') or not url:
        raise ShopError('本机回调服务尚未启动，请重新打开软件')
    health = url.rsplit('/', 1)[0] + '/callback-health'
    try:
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
        with opener.open(health, timeout=8) as response:
            value = json.loads(response.read(4096))
        if not hmac.compare_digest(str(value.get('probe','')), RUNTIME['probe']):
            raise ValueError()
    except Exception:
        raise ShopError('回调地址未连接到本软件。请检查地址、端口和 HTTPS 转发设置') from None


def preflight(data, require_confirmation=True):
    cfg = load(data)
    if not all(cfg.get(k) for k in ('app_key','app_secret','service_id')):
        raise ShopError('请先填写自己的 App Key、App Secret 和 Partner Center 授权链接')
    callback = callback_status(cfg)
    if callback['invalid']:
        raise ShopError('旧测试回调地址不能使用，请复制本机生成地址或配置自己的 HTTPS 回调')
    if callback['changed']:
        raise ShopError('原回调端口被占用，已分配新端口。请复制新地址到 Partner Center 保存，再更新本页配置')
    if require_confirmation and not callback['confirmed']:
        raise ShopError('请在 Partner Center 保存完全一致的 Redirect URL，再勾选已保存并保存本页配置')
    if require_confirmation and not cfg.get('permissions_confirmed'):
        raise ShopError('请先按教程在 Partner Center 启用所需权限。软件将在授权后核对实际获得的权限')
    probe_callback(callback['local_url'])
    if callback['mode'] == 'https': probe_callback(callback['url'])


@locked
def complete_callback(data, params, callback_url=None):
    exchange(data, params, callback_url)
    result = sync_shops(data)
    if not result.get('shops') or not result.get('authorized'):
        raise ShopError('尚未验证到已授权店铺，请重新连接')
    cfg = load(data)
    cfg['last_error'] = ''
    save(data, cfg)
    return result


def required_scope(path, writing=False, method=None):
    if path == '/seller/202309/permissions' or path == '/logistics/202309/global_warehouses':
        return 'seller.shop.info'
    if path.startswith('/authorization/'):
        return 'seller.authorization.info'
    if path.startswith('/logistics/'):
        return 'seller.logistics'
    if path.startswith('/product/'):
        if path.endswith('/images/upload') or path.endswith('/global_listing_rules'):
            return 'seller.product.basic'
        if '/global_categories' in path or path.endswith(('/global_attributes','/global_rules')):
            return 'seller.global_product.category.info'
        # These POST operations read or validate; they do not modify a product.
        reading = path.endswith(('/search','/recommend','/predict','/listing_check')) or not writing
        global_product = 'global_' in path
        if global_product:
            return 'seller.global_product.info' if reading else 'seller.global_product.write'
        return 'seller.product.basic' if reading else 'seller.product.write'
    return ''


def require_scope(cfg, path, writing=False, method=None):
    scope = required_scope(path, writing, method)
    if scope=='seller.authorization.info' and 'seller.fs.order&fufillment.management' in (cfg.get('token') or {}).get('granted_scopes',[]):
        return
    if scope and scope not in (cfg.get('token') or {}).get('granted_scopes', []):
        raise ShopError('缺少权限 '+scope+'。请到 Partner Center → App & Service → Manage API 开通并发布变更，然后回本软件重新授权')


@locked
def diagnostics(data):
    cfg = load(data)
    rows = []
    def add(key, name, state, detail):
        rows.append(dict(key=key, name=name, state=state, detail=detail))
    callback = callback_status(cfg)
    try:
        probe_callback(callback['local_url'])
        add('callback','本机回调服务','success','服务响应正常，正在本机等待授权返回')
    except ShopError as exc:
        add('callback','本机回调服务','failed',str(exc))
    if callback['mode']=='https':
        try:
            probe_callback(callback['url'])
            add('https','HTTPS 回调转发','success','已确认此 HTTPS 地址连接到当前软件')
        except ShopError as exc: add('https','HTTPS 回调转发','failed',str(exc))
    add('redirect','Partner Center 回调地址匹配',
        'failed' if callback['invalid'] or callback['changed'] else 'success' if callback['verified'] else 'pending',
        '旧测试地址不能使用，请复制本机地址或配置自己的 HTTPS 回调' if callback['invalid'] else
        '已收到该地址的真实授权回调' if callback['verified'] and not callback['changed'] else
        '端口已变化，请更新 Partner Center 地址' if callback['changed'] else
        '软件不能读取 Partner Center 设置；请保存完全一致的地址。收到真实回调后才能验证匹配')
    verification_error = ''
    verified = False
    if (cfg.get('token') or {}).get('access_token'):
        try:
            sync_shops(data); verified = True
        except ShopError as exc: verification_error = str(exc)
    for key, name in (('app_key','App Key 接口验证'),('app_secret','App Secret 接口验证')):
        add(key,name,'success' if verified else 'failed' if not cfg.get(key) or verification_error else 'pending',
            '已使用本机凭证签名并成功读取 TikTok 已授权店铺' if verified else
            verification_error or ('请填写自己的 '+name.replace(' 接口验证','') if not cfg.get(key) else '已安全保存，等待首次授权后执行真实接口验证；非空不代表正确'))
    cfg = load(data); token = cfg.get('token') or {}
    scope = _scope_info(token.get('granted_scopes'))
    add('authorization','店铺授权','success' if verified else 'failed' if verification_error else 'pending',
        '已通过真实店铺接口验证' if verified else verification_error or '请点击连接我的 TikTok Shop 店铺')
    add('token','授权凭证有效性','success' if verified else 'pending',
        '平台已接受当前凭证' if verified else '尚未通过平台验证；本地有效期仅供参考')
    add('scopes','商品管理权限','success' if token and scope['ok'] else 'failed' if token else 'pending',
        '实际授权包含基础商品读写权限；具体接口仍受平台校验' if token and scope['ok'] else
        '请在 Manage API 开通并重新授权：'+ '、'.join(scope['missing']))
    add('shops','获取店铺信息','success' if verified else 'pending',
        '已读取 '+str(len(cfg.get('shops') or []))+' 家店铺' if verified else '完成授权后重试')
    if verified and cfg.get('selected_shop_id'):
        try:
            api(data,'/product/202309/products/search',params={'page_size':1},payload={})
            add('products','商品查询接口','success','已执行真实商品查询；未创建、修改或发布商品')
        except ShopError as exc: add('products','商品查询接口','failed',str(exc))
    else:
        add('products','商品查询接口','pending','请先授权并选择一家店铺')
    return {'checks':rows, 'ok':all(x['state']=='success' for x in rows),
            'write_tested':False, 'checked_at':int(time.time()), 'status':public_status(data)}
