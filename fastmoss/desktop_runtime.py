"""Bind before advertising ports; expose only two paths on the callback listener."""
import json
import os
import secrets
import socket
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import runtime_paths
import tiktok_shop


UI_PORT = 8765
UI_URL = 'http://127.0.0.1:8765/'


class FixedPortError(OSError):
    pass


class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = sys.platform != 'win32'

    def server_bind(self):
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def bind_server(data, handler):
    try:
        return LocalHTTPServer(('127.0.0.1', UI_PORT), handler)
    except OSError:
        raise FixedPortError(
            '固定地址 ' + UI_URL + ' 无法启动：8765 端口被占用或被系统限制。'
            '请先退出旧版 TK 上架助手或占用该端口的程序，再重新打开软件。'
            '软件不会自动更换端口，TikTok 中的 Redirect URL 无需修改。'
        ) from None


def start_callback(data, ui_port):
    runtime = {'ui_url': 'http://127.0.0.1:%d/' % ui_port, 'probe': secrets.token_urlsafe(24)}

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass  # Never log authorization query strings.

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == '/api/shop/callback-health':
                body = json.dumps({'probe': runtime['probe']}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
                return
            if parsed.path != '/api/shop/callback':
                self.send_error(404); return
            configured = urllib.parse.urlsplit(tiktok_shop.load(data).get('redirect_url',''))
            scheme = 'https' if configured.scheme=='https' else 'http'
            tiktok_shop.receive_callback(self,data,scheme+'://'+self.headers.get('Host','')+parsed.path)

    previous = runtime_paths.read_runtime(data)
    try:
        listener = LocalHTTPServer(('127.0.0.1', int(previous.get('callback_port', 0))), CallbackHandler)
    except OSError:
        listener = LocalHTTPServer(('127.0.0.1', 0), CallbackHandler)
    runtime['callback_url'] = 'http://127.0.0.1:%d/api/shop/callback' % listener.server_port
    runtime['callback_port'] = listener.server_port
    tiktok_shop.RUNTIME = runtime
    runtime_paths.write_runtime(data, {'port': ui_port, 'callback_port': listener.server_port, 'pid': os.getpid()})
    threading.Thread(target=listener.serve_forever, daemon=True).start()
    return listener
