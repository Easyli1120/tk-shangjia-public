"""Backend model registry. Environment wins; web keys are authenticated ciphertext."""
import base64
import sys
import secure_storage
import runtime_paths
from database import ClosingConnection
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import urllib.parse


def stamp():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def environment(root):
    values = {}
    path = Path(root) / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.removeprefix('export ').split('=', 1)
                values[k.strip()] = v.strip().strip('\"\'')
    values.update(os.environ)
    return values


def migrate(c):
    c.executescript((Path(__file__).parent/'migrations'/'002_ai_models.sql').read_text(encoding='utf-8'))


def seed(c, root):
    env = environment(root)
    base = env.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')
    provider = env.get('IMAGE_PROVIDER', 'openai')
    items = [('tt-terra', 'vision', 'TT-6.1 terra', '小马 AI', 'https://api.lk888.ai/v1', 'tt-6.1-terra', '竞品图片分析、视觉逆向与 Prompt 生成', 1, {}),
      ('image-sunburst', 'image', 'GPT Image 2.6 · Sunburst', provider, base, env.get('OPENAI_IMAGE_MODEL','gpt-image-2.6-sunburst'), '独立印花与服装 Mockup', 1, {'variant':'sunburst'}),
      ('image-flare', 'image', 'GPT Image 2.6 · Flare', provider, base, env.get('OPENAI_IMAGE_MODEL_FAST','gpt-image-2.6-flare'), '快速设计迭代与 Mockup', 0, {'variant':'flare'})]
    for id_, category, name, provider, base, model, purpose, default, options in items:
        c.execute('''INSERT OR IGNORE INTO ai_models(id,category,name,provider,base_url,model_id,purpose,is_default,options,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)''', (id_,category,name,provider,base,model,purpose,default,json.dumps(options),stamp()))


def _master(data):
    path = Path(data) / '.model-master-key'
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as out:
            out.write(secrets.token_bytes(32))
    path.chmod(0o600)
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError('服务端加密主密钥无效，请恢复 data/.model-master-key')
    return key


def _cipher(content, key, decrypt=False):
    # Password via a private file descriptor, never argv/environment or logs.
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, base64.b64encode(hmac.new(key,b'encryption',hashlib.sha256).digest()) + b'\n')
        os.close(write_fd)
        command = ['/usr/bin/openssl','enc','-aes-256-cbc','-pbkdf2','-iter','200000','-pass','fd:'+str(read_fd)]
        if decrypt:
            command.append('-d')
        p = subprocess.run(command,input=content,stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=(read_fd,),timeout=10)
        if p.returncode:
            raise ValueError('密钥加解密失败，请检查服务端 OpenSSL 或恢复主密钥')
        return p.stdout
    finally:
        os.close(read_fd)


def encrypt(data, value):
    if sys.platform=='win32': return secure_storage.seal(data,value)
    key = _master(data)
    blob = _cipher(value.encode(), key)
    tag = hmac.new(hmac.new(key,b'authentication',hashlib.sha256).digest(), blob, hashlib.sha256).digest()
    return 'v1:' + base64.b64encode(tag + blob).decode()


def decrypt(data, value):
    if not value:
        return ''
    if value.startswith('dpapi:'): return secure_storage.unseal(data,value)
    key = _master(data)
    try:
        if not value.startswith('v1:'):
            raise ValueError()
        raw = base64.b64decode(value[3:],validate=True)
        expected = hmac.new(hmac.new(key,b'authentication',hashlib.sha256).digest(),raw[32:],hashlib.sha256).digest()
        if not hmac.compare_digest(raw[:32],expected):
            raise ValueError()
        return _cipher(raw[32:],key,True).decode()
    except Exception:
        raise ValueError('保存的密钥无法校验，请恢复主密钥或重新填写 API Key') from None


def resolve(c, root, data, id_=None, category='vision'):
    row = c.execute('SELECT * FROM ai_models WHERE id=?',(id_,)).fetchone() if id_ else c.execute('SELECT * FROM ai_models WHERE category=? AND is_default=1',(category,)).fetchone()
    if not row or row['category'] != category:
        raise ValueError('请选择有效的模型')
    result = dict(row)
    result['options'] = json.loads(result['options'])
    env = environment(root)
    if category == 'vision':
        fields = {'api_key':'TT_API_KEY','base_url':'TT_BASE_URL','model_id':'TT_MODEL'}
    else:
        variant = result['options'].get('variant','sunburst')
        fields = {'api_key':'OPENAI_API_KEY','base_url':'OPENAI_BASE_URL','model_id':'OPENAI_IMAGE_MODEL_FAST' if variant=='flare' else 'OPENAI_IMAGE_MODEL'}
    result['api_key'] = ''
    result['key_source'] = '未配置'
    for field, variable in fields.items():
        if env.get(variable):
            result[field] = env[variable]
            if field=='api_key':
                result['key_source'] = '环境变量 / 后端 .env'
    if not result['api_key'] and result.get('secret'):
        result['api_key'] = decrypt(data,result['secret'])
        result['key_source'] = '服务端加密存储'
    result.pop('secret',None)
    return result


def public(c, root, data):
    seed(c,root)
    out = []
    for r in c.execute('SELECT id,category FROM ai_models ORDER BY category DESC,id'):
        item = resolve(c,root,data,r['id'],r['category'])
        key = item.pop('api_key')
        item['api_key_masked'] = '••••••••' if key else ''
        item['configured'] = bool(key)
        out.append(item)
    return {'models':out,'budget':budget(c)}


def validate_url(value):
    u = urllib.parse.urlparse(value)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.hostname in ('localhost','127.0.0.1','::1'):
        raise ValueError('Base URL 必须为不含凭证的公网 HTTPS 地址')
    return value.rstrip('/')


def save(c, root, data, body):
    seed(c,root)
    id_ = body.get('id')
    row = c.execute('SELECT * FROM ai_models WHERE id=?',(id_,)).fetchone()
    if not row:
        raise ValueError('模型不存在')
    fields = {}
    for key in ('name','provider','base_url','model_id','purpose'):
        value = body.get(key,row[key])
        if not isinstance(value,str) or not value.strip() or len(value)>500:
            raise ValueError('模型字段无效：'+key)
        fields[key] = value.strip()
    fields['base_url'] = validate_url(fields['base_url'])
    if row['category']=='image':
        # These profiles configure the existing verified adapters; no guessed protocols.
        if fields['provider'] not in ('openai','lk888') or fields['provider'] != row['provider']:
            raise ValueError('请保留现有生图服务商；切换协议请通过后端配置并重启')
    enabled = int(bool(body.get('enabled',row['enabled'])))
    default = int(bool(body.get('is_default',row['is_default'])))
    if default and not enabled:
        raise ValueError('默认模型必须启用')
    if not enabled and row['is_default']:
        default=0
    if default:
        c.execute('UPDATE ai_models SET is_default=0 WHERE category=?',(row['category'],))
    secret = row['secret']
    value = body.get('api_key','')
    if not isinstance(value,str) or len(value)>1000 or '\n' in value or '\r' in value:
        raise ValueError('API Key 格式无效')
    if value:
        secret=encrypt(data,value.strip())
    changed = value or any(fields[k]!=row[k] for k in ('base_url','model_id'))
    c.execute('''UPDATE ai_models SET name=?,provider=?,base_url=?,model_id=?,purpose=?,enabled=?,is_default=?,secret=?,updated_at=?,
        vision_verified=CASE WHEN ? THEN 0 ELSE vision_verified END,last_test_ok=CASE WHEN ? THEN NULL ELSE last_test_ok END,
        last_test_at=CASE WHEN ? THEN NULL ELSE last_test_at END,last_test_error=CASE WHEN ? THEN NULL ELSE last_test_error END WHERE id=?''',
        (*[fields[k] for k in ('name','provider','base_url','model_id','purpose')],enabled,default,secret,stamp(),bool(changed),bool(changed),bool(changed),bool(changed),id_))
    return public(c,root,data)


def budget(c, body=None):
    if body is not None:
        value=body.get('daily_token_limit',0)
        if type(value) is not int or not 0<=value<=10**10:
            raise ValueError('每日 Token 预警阈值无效')
        c.execute("INSERT OR REPLACE INTO ai_model_settings VALUES('daily_token_limit',?)",(str(value),))
    row=c.execute("SELECT value FROM ai_model_settings WHERE key='daily_token_limit'").fetchone()
    return {'daily_token_limit':int(row[0]) if row else 0}


# Worker-local image profile selection keeps queued jobs on their selected preset.
import contextvars
image_variant=contextvars.ContextVar('fastmoss_image_variant',default=None)


def image_configuration(root,values):
    import sqlite3
    data=runtime_paths.data_directory(root)
    path=runtime_paths.database_path(data)
    if not path.is_file():return values
    with sqlite3.connect(path,factory=ClosingConnection) as c:
        c.row_factory=sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='ai_models'").fetchone():return values
        choice=image_variant.get()
        row=c.execute('SELECT id FROM ai_models WHERE id=?',('image-'+choice,)).fetchone() if choice else c.execute("SELECT id FROM ai_models WHERE category='image' AND is_default=1").fetchone()
        if not row:return values
        cfg=resolve(c,root,data,row['id'],'image')
        values['OPENAI_API_KEY']=cfg['api_key'] if cfg['enabled'] else ''
        values['OPENAI_BASE_URL']=cfg['base_url']
        for variant,field in [('sunburst','OPENAI_IMAGE_MODEL'),('flare','OPENAI_IMAGE_MODEL_FAST')]:
            profile=resolve(c,root,data,'image-'+variant,'image')
            values[field]=profile['model_id']
        return values


def test_image(c,root,data,id_):
    import urllib.request
    import urllib.error
    from openai_images import NoRedirect
    from tt_adapter import safe_error
    cfg=resolve(c,root,data,id_,'image')
    ok=False;message=''
    try:
        if not cfg['enabled'] or not cfg['api_key']:raise ValueError('请配置并启用生图模型')
        request=urllib.request.Request(cfg['base_url'].rstrip('/')+'/models',headers={'Authorization':'Bearer '+cfg['api_key']})
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=25) as response:
            payload=json.loads(response.read(2*1024*1024))
        items=payload.get('data',[])
        if isinstance(items,dict):items=items.get('models',items.get('list',[]))
        found=any(isinstance(m,dict) and (m.get('id')==cfg['model_id'] or m.get('model')==cfg['model_id']) for m in items)
        if not found:raise ValueError('接口可达，但 /models 未列出 '+cfg['model_id']+'；请核对权限。生图能力须以真实生成任务为准')
        ok=True;message='鉴权与模型列表连接成功；真实生图能力以生成任务结果为准'
    except urllib.error.HTTPError as exc:
        message='HTTP '+str(exc.code)+'：'+safe_error(exc.read(4096).decode('utf-8','replace'),cfg['api_key'])
    except Exception as exc:
        message=safe_error(str(exc),cfg['api_key'])
    c.execute('UPDATE ai_models SET last_test_at=?,last_test_ok=?,last_test_error=? WHERE id=?',(stamp(),int(ok),None if ok else message,id_))
    return {'ok':ok,'message':message,'last_test_at':stamp(),'vision_verified':False}
