"""Windows user-bound DPAPI. No plaintext secrets or key material on Windows."""
import base64
import ctypes
import json
import os
import secrets
import sys
from pathlib import Path


def _dpapi(content, decrypt=False):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(content)
    source = Blob(len(content), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt.CryptUnprotectData
        second = None
    else:
        function = crypt.CryptProtectData
        second = 'TKShangjia'
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), second, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('Windows 无法保护或读取凭证，请使用保存凭证的 Windows 用户重新配置')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def _key(data):
    path = Path(data) / 'credentials' / 'local.key'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as file: file.write(secrets.token_bytes(32))
    key = path.read_bytes()
    if len(key) != 32: raise ValueError('本机凭证保护文件损坏，请重新配置')
    return key


def seal(data, value):
    raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
    if sys.platform == 'win32':
        return 'dpapi:' + base64.b64encode(_dpapi(raw)).decode('ascii')
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return 'aesgcm:' + base64.b64encode(nonce + AESGCM(_key(data)).encrypt(nonce, raw, b'TKShangjia')).decode('ascii')


def unseal(data, value):
    try:
        kind, encoded = value.split(':', 1)
        raw = base64.b64decode(encoded, validate=True)
        if kind == 'dpapi' and sys.platform == 'win32':
            plain = _dpapi(raw, True)
        elif kind == 'aesgcm' and sys.platform != 'win32':
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            plain = AESGCM(_key(data)).decrypt(raw[:12], raw[12:], b'TKShangjia')
        else:
            raise ValueError()
        return json.loads(plain.decode('utf-8'))
    except Exception:
        raise ValueError('无法读取本机加密凭证，请在原 Windows 用户下运行或重新绑定店铺') from None
