"""Read original document responses from our isolated Chrome via loopback CDP."""
import base64
import hashlib
import json
import os
import re
import select
import socket
import subprocess
import sys
from pathlib import Path
import struct
import time
import urllib.parse

LIMIT = 32 * 1024 * 1024
PAGE_TIMEOUT = 30


class RateLimitError(RuntimeError):
    pass

class Connection:
    def __init__(self, url, deadline):
        u = urllib.parse.urlsplit(url)
        if u.scheme != 'ws' or u.hostname != '127.0.0.1':
            raise ValueError('CDP endpoint must be loopback')
        self.deadline = deadline
        self.sock = socket.create_connection((u.hostname, u.port), timeout=5)
        self.buffer = b''
        self.serial = 0
        self.events = []
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(('GET '+u.path+' HTTP/1.1\r\nHost: 127.0.0.1:'+str(u.port)+'\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: '+key+'\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        while b'\r\n\r\n' not in self.buffer:
            if time.monotonic() >= deadline:
                self.sock.close();raise TimeoutError('CDP handshake deadline')
            chunk = self.sock.recv(4096)
            if not chunk:
                self.sock.close();raise ConnectionError('CDP handshake disconnected')
            self.buffer += chunk
            if len(self.buffer) > 65536: raise ValueError('Invalid websocket handshake')
        headers, self.buffer = self.buffer.split(b'\r\n\r\n', 1)
        accept = base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        if b' 101 ' not in headers.split(b'\r\n')[0] or accept not in headers:
            raise ValueError('CDP websocket refused')
    def read(self, size):
        while len(self.buffer) < size:
            remaining = self.deadline-time.monotonic()
            if remaining <= 0: raise TimeoutError('CDP deadline')
            self.sock.settimeout(min(remaining, 5))
            chunk = self.sock.recv(min(65536, size-len(self.buffer)))
            if not chunk: raise ConnectionError('CDP disconnected')
            self.buffer += chunk
        out, self.buffer = self.buffer[:size], self.buffer[size:]
        return out
    def send_frame(self, data, opcode=1):
        mask = os.urandom(4)
        size = len(data)
        prefix = bytes([128 | opcode])
        prefix += bytes([128 | size]) if size < 126 else (bytes([254])+struct.pack('!H',size) if size < 65536 else bytes([255])+struct.pack('!Q',size))
        self.sock.sendall(prefix+mask+bytes(v ^ mask[i % 4] for i,v in enumerate(data)))
    def receive(self):
        parts = []
        total = 0
        while True:
            a,b = self.read(2)
            size = b & 127
            if size == 126: size = struct.unpack('!H',self.read(2))[0]
            elif size == 127: size = struct.unpack('!Q',self.read(8))[0]
            total += size
            if total > LIMIT: raise ValueError('CDP response too large')
            mask = self.read(4) if b & 128 else None
            body = self.read(size)
            if mask: body = bytes(v ^ mask[i % 4] for i,v in enumerate(body))
            opcode = a & 15
            if opcode == 8: raise ConnectionError('CDP closed')
            if opcode == 9:
                self.send_frame(body,10);continue
            if opcode == 10: continue
            parts.append(body)
            if a & 128: return json.loads(b''.join(parts))
    def call(self, method, params=None, session=None):
        self.serial += 1
        message = dict(id=self.serial,method=method,params=params or {})
        if session: message['sessionId'] = session
        self.send_frame(json.dumps(message).encode())
        while True:
            incoming = self.receive()
            if incoming.get('id') == self.serial:
                if 'error' in incoming: raise RuntimeError(incoming['error'].get('message','CDP error'))
                return incoming.get('result',{})
            if 'method' in incoming: self.events.append(incoming)
    def next_event(self):
        if self.events: return self.events.pop(0)
        if not self.buffer and not select.select([self.sock], [], [], .25)[0]: return {}
        return self.receive()
    def close(self):
        self.sock.close()


def show_collection_tab(connection, session):
    connection.call('Page.bringToFront',session=session)
    if sys.platform!='darwin':return
    # Activate the Chrome process that owns this CDP connection, even when
    # another Chrome profile is the frontmost application on macOS.
    try:
        processes=connection.call('SystemInfo.getProcessInfo').get('processInfo',[])
        pid=next((p['id'] for p in processes if p.get('type')=='browser'),None)
        if pid:
            script="ObjC.import('AppKit'); $.NSRunningApplication.runningApplicationWithProcessIdentifier("+str(int(pid))+").activateWithOptions($.NSApplicationActivateIgnoringOtherApps)"
            subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',script],timeout=3,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    except (OSError,RuntimeError,subprocess.TimeoutExpired):
        pass


class BrowserSession:
    """One ordinary Chrome process and one tab shared by all queue children."""
    def __init__(self, data, executable):
        self.profile = Path(data)/'collector-browser'
        self.executable = executable
        self.endpoint = ''
        self.target = ''
        self.process = None
        self.target_file = self.profile/'collection-target'
    def remember(self):
        self.target_file.write_text(self.target)
        return dict(endpoint=self.endpoint,target=self.target)
    def ensure(self):
        if self.endpoint:
            try:
                c = Connection(self.endpoint, time.monotonic()+5)
                targets = c.call('Target.getTargets')['targetInfos']
                if not any(t['targetId'] == self.target for t in targets):
                    self.target = c.call('Target.createTarget', {'url':'about:blank','newWindow':True,'background':True})['targetId']
                c.close()
                return self.remember()
            except (OSError,ValueError,RuntimeError):
                self.endpoint='';self.target=''
        self.profile.mkdir(parents=True,exist_ok=True)
        active=self.profile/'DevToolsActivePort'
        # Reattach after a service restart to preserve an in-progress human verification.
        if active.exists():
            try:
                port,path=active.read_text(encoding='utf-8').splitlines()[:2]
                endpoint='ws://127.0.0.1:'+port+path
                c=Connection(endpoint,time.monotonic()+3)
                targets=c.call('Target.getTargets')['targetInfos']
                pages=[t for t in targets if t['type']=='page']
                remembered=self.target_file.read_text(encoding='utf-8') if self.target_file.exists() else ''
                matches=[t for t in pages if t['targetId']==remembered]
                self.target=matches[0]['targetId'] if matches else (pages[0]['targetId'] if pages else c.call('Target.createTarget',{'url':'about:blank','newWindow':True,'background':True})['targetId'])
                self.endpoint=endpoint;c.close()
                return self.remember()
            except (OSError,ValueError,RuntimeError):pass
        active.unlink(missing_ok=True)
        if sys.platform != 'darwin' and not self.executable:
            raise RuntimeError('采集需要 Chrome 或 Edge。请安装其中一个浏览器后重新打开软件')
        args=['--no-first-run','--no-default-browser-check','--user-data-dir='+str(self.profile),'--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--no-startup-window']
        cmd=['/usr/bin/open','-g','-W','-n','-a','/Applications/Google Chrome.app','--args']+args if sys.platform=='darwin' else [self.executable]+args
        self.process=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if active.exists():
                try:
                    port,path=active.read_text(encoding='utf-8').splitlines()[:2]
                    self.endpoint='ws://127.0.0.1:'+port+path
                    return self.ensure()
                except (OSError,ValueError):pass
            if self.process.poll() is not None:break
            time.sleep(.1)
        raise RuntimeError('采集浏览器无法启动，请关闭旧版独立验证窗口后重试')
    def show(self):
        spec=self.ensure();c=Connection(spec['endpoint'],time.monotonic()+5)
        try:
            session=c.call('Target.attachToTarget',{'targetId':self.target,'flatten':True})['sessionId']
            show_collection_tab(c,session)
        finally:c.close()


def same_product_page(current, requested):
    def identity(url):
        u=urllib.parse.urlsplit(url)
        if u.scheme!='https' or not (u.hostname=='tiktok.com' or (u.hostname or '').endswith('.tiktok.com')):return None
        match=re.search(r'/(?:product|pdp)/(?:[^/]+/)?(\d{8,})(?:/|$)',u.path)
        if not match:return None
        q=urllib.parse.parse_qs(u.query);market=q.get('region',[''])[0].upper()
        path_market=re.match(r'^/([a-z]{2})/pdp/',u.path)
        if path_market:market=path_market[1].upper()
        return match[1],market
    return bool(current and (current==requested or (identity(current) is not None and identity(current)==identity(requested))))


def read_page(connection, url, accept, target=None, progress=lambda **kw:None, is_verification=lambda body:False, resume=False,is_terminal=lambda body:False):
    target = target or connection.call('Target.createTarget', {'url':'about:blank'})['targetId']
    session = connection.call('Target.attachToTarget', {'targetId':target,'flatten':True})['sessionId']
    reconnects=0;waiting=False;recovery_since=0
    def call(method, params=None):
        nonlocal session,reconnects,recovery_since
        try:return connection.call(method,params,session)
        except RuntimeError as exc:
            if reconnects>=2 or not any(v in str(exc).lower() for v in ('session with given id not found','no session with given id')):raise
            reconnects+=1
            recovery_since=time.monotonic()
            session=connection.call('Target.attachToTarget',{'targetId':target,'flatten':True})['sessionId']
            connection.call('Page.enable',session=session)
            connection.call('Network.enable',{'maxTotalBufferSize':LIMIT,'maxResourceBufferSize':LIMIT//2},session)
            progress(stage='浏览器连接已恢复，继续当前商品',verification=waiting,heartbeat=time.time())
            return connection.call(method,params,session)
    call('Page.enable')
    call('Network.enable', {'maxTotalBufferSize':LIMIT,'maxResourceBufferSize':LIMIT//2})
    frame=call('Page.getFrameTree').get('frameTree',{}).get('frame',{})
    restored_challenge=False
    if resume:
        dom=call('Runtime.evaluate',{'expression':'document.documentElement.outerHTML','returnByValue':True})
        restored_challenge=is_verification(dom.get('result',{}).get('value',''))
    if restored_challenge or same_product_page(frame.get('url',''),url):recovery_since=time.monotonic()
    else:call('Page.navigate', {'url':url})
    documents = {};last_body = '';last_check=0;last_source_check=0;recovery_reloaded=False
    def loaded_source():
        # Network events may predate attachment (restart, sleep or a detached session).
        # Read Chrome's existing main document without navigating away from a challenge.
        frame=call('Page.getResourceTree').get('frameTree',{}).get('frame',{})
        if not same_product_page(frame.get('url',''),url):return ''
        result=call('Page.getResourceContent',{'frameId':frame['id'],'url':frame['url']})
        body=result.get('content','')
        if result.get('base64Encoded'):body=base64.b64decode(body).decode('utf-8','replace')
        if len(body.encode())>LIMIT//2:raise ValueError('Product page too large')
        return body

    active_deadline=time.monotonic()+PAGE_TIMEOUT
    while True:
        now=time.monotonic();connection.deadline=now+10
        if not waiting and now>active_deadline:
            if last_body:return last_body
            raise TimeoutError('Product document did not finish loading')
        if now-last_check>=1:
            last_check=now
            # DOM is a fallback and a challenge detector; the original response supplies SKU data.
            state=call('Runtime.evaluate',{'expression':'document.documentElement.outerHTML','returnByValue':True})
            dom=state.get('result',{}).get('value','')
            challenged=is_verification(dom)
            if not challenged and is_terminal(dom):
                current=call('Page.getFrameTree').get('frameTree',{}).get('frame',{}).get('url','')
                requested=urllib.parse.urlsplit(url)
                redirected_short=requested.hostname in ('vt.tiktok.com','vm.tiktok.com') and current.startswith('https://') and (urllib.parse.urlsplit(current).hostname or '').endswith('.tiktok.com')
                if same_product_page(current,url) or redirected_short:
                    progress(stage='已确认本地区不售，继续下一条',verification=False,heartbeat=time.time())
                    return dom
            if not challenged and (accept(dom) or (last_body and accept(last_body))):
                progress(stage='商品数据已读取，正在保存图片',verification=False,heartbeat=time.time())
                return dom if accept(dom) else last_body
            if not challenged and re.search(r'crawler traffic detected|too many requests',dom,re.I):
                raise RateLimitError('TikTok 暂时限制访问，继续处理后续商品')
            if not challenged and now-last_source_check>=3:
                last_source_check=now
                try:
                    source=loaded_source()
                    if source and accept(source):
                        progress(stage='商品原始数据已恢复，正在保存图片',verification=False,heartbeat=time.time())
                        return source
                except RuntimeError:pass
            if not challenged and not waiting and recovery_since and not recovery_reloaded:
                # Some product documents are no-store. One reload recovers their response
                # only after verifying that no human challenge is currently on screen.
                call('Page.reload');recovery_reloaded=True;active_deadline=now+PAGE_TIMEOUT
            if challenged:
                last_body=''
                if not waiting:
                    waiting=True;recovery_since=0
                    progress(stage='等待你在采集窗口完成验证码',verification=True,heartbeat=time.time())
                    show_collection_tab(connection,session)
                progress(stage='等待你在采集窗口完成验证码；通过后自动继续',verification=True,heartbeat=time.time())
            elif waiting:
                # Resume on the first check after the user clears the challenge.
                # No fixed post-verification timer and no reload of their page.
                waiting=False;recovery_since=0;active_deadline=now+PAGE_TIMEOUT
                progress(stage='验证已完成，正在读取商品',verification=False,heartbeat=time.time())
            else:progress(stage='正在读取商品页面',verification=False,heartbeat=time.time())
        event=connection.next_event()
        if event.get('sessionId') != session:continue
        p=event.get('params',{})
        if event.get('method')=='Network.responseReceived' and p.get('type')=='Document':
            response=p['response'];u=urllib.parse.urlsplit(response['url'])
            if u.scheme=='https' and (u.hostname=='tiktok.com' or (u.hostname or '').endswith('.tiktok.com')):
                # Ignore child frames (including embedded challenge documents).
                main=call('Page.getFrameTree').get('frameTree',{}).get('frame',{})
                if p.get('frameId')!=main.get('id'):continue
                if response.get('status')==429:raise RateLimitError('TikTok 限流（HTTP 429），继续处理后续商品')
                documents[p['requestId']]=response
        if event.get('method')=='Network.loadingFinished' and p.get('requestId') in documents:
            try:
                response=call('Network.getResponseBody',{'requestId':p['requestId']})
                body=response.get('body','')
                if response.get('base64Encoded'):body=base64.b64decode(body).decode('utf-8','replace')
                if len(body.encode())>LIMIT//2:raise ValueError('Product page too large')
                last_body=body
                # The next DOM check confirms there is no active challenge
                # before accepting even an otherwise complete document.
                if accept(body):continue
                if documents[p['requestId']].get('status')==403 and not is_verification(body):
                    raise RateLimitError('TikTok 限制访问（HTTP 403），继续处理后续商品')
            except RateLimitError:raise
            except RuntimeError:pass
