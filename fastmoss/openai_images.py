"""Official Images API, no SDK or hidden automatic paid-request retries.

Schema/pricing verified 2026-11-13 against developers.openai.com/api.
Only this backend module reads credentials. Never log HTTP headers/raw errors.
"""
import base64
import json
import os
import re
import struct
import urllib.error
import urllib.request
import uuid
import zlib

MODELS = {'sunburst': 'gpt-image-2.6-sunburst', 'flare': 'gpt-image-2.6-flare'}


def configuration(root):
    values = {}
    path = root / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.removeprefix('export ').split('=', 1)
            values[key.strip()] = value.strip().strip('\"\'')
    result = {k: os.environ.get(k, values.get(k, default)) for k, default in {
        'OPENAI_API_KEY': '', 'OPENAI_BASE_URL': 'https://api.openai.com/v1',
        'OPENAI_IMAGE_MODEL': MODELS['sunburst'], 'OPENAI_IMAGE_MODEL_FAST': MODELS['flare'],
        'OPENAI_IMAGE_TIMEOUT': '300', 'IMAGE_PROVIDER': 'openai',
    }.items()}
    import model_manager
    return model_manager.image_configuration(root,result)


class APIError(Exception):
    def __init__(self, message, status=None, request_id=None, retry_after=0, uncertain=False):
        super().__init__(message)
        self.status, self.request_id = status, request_id
        self.retry_after, self.uncertain = retry_after, uncertain


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def png_info(content):
    """Validate PNG structure/CRC; detect alpha capability without dependencies."""
    if not content.startswith(b'\x89PNG\r\n\x1a\n') or len(content) > 50 * 1024 * 1024:
        raise ValueError('API 未返回有效 PNG 图片')
    offset, width, height, alpha, data_seen = 8, 0, 0, False, False
    compressed, header = [], None
    while offset + 12 <= len(content):
        length = struct.unpack('>I', content[offset:offset + 4])[0]
        kind = content[offset + 4:offset + 8]
        chunk = content[offset + 8:offset + 8 + length]
        end = offset + length + 12
        if end > len(content) or zlib.crc32(kind + chunk) & 0xffffffff != struct.unpack('>I', content[end - 4:end])[0]:
            raise ValueError('PNG 文件损坏')
        if kind == b'IHDR':
            if length != 13 or width:
                raise ValueError('PNG 尺寸无效')
            width, height = struct.unpack('>II', chunk[:8])
            header = chunk
            if not width or not height or width * height > 32000000:
                raise ValueError('PNG 尺寸超出限制')
            alpha = chunk[9] in (4, 6)
        if kind == b'tRNS':
            alpha = True
        if kind == b'IDAT':
            data_seen = True
            compressed.append(chunk)
        if kind == b'IEND' and width and height and data_seen and end == len(content):
            pixels = png_transparent_pixels(header, b''.join(compressed)) if alpha else {'transparent_pixels':0, 'fully_transparent_pixels':0}
            return {'width': width, 'height': height, 'alpha_channel': alpha, **pixels}
        offset = end
    raise ValueError('PNG 文件不完整')


def png_transparent_pixels(header, compressed):
    """Count real transparent pixels for non-interlaced 8-bit RGBA/gray-alpha PNG."""
    width, height = struct.unpack('>II', header[:8])
    if header[8] != 8 or header[9] not in (4, 6) or header[12] != 0:
        return {'transparent_pixels':None, 'fully_transparent_pixels':None}  # Requires visual review.
    bpp = 4 if header[9] == 6 else 2
    stride = width * bpp
    expected = (stride + 1) * height
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, expected + 1)
    if len(raw) != expected or not decoder.eof:
        raise ValueError('PNG 像素数据损坏')
    previous = bytearray(stride)
    transparent, fully_transparent = 0, 0
    for y in range(height):
        offset = y * (stride + 1)
        method, row = raw[offset], bytearray(raw[offset + 1:offset + 1 + stride])
        if method > 4:
            raise ValueError('PNG 滤波类型无效')
        if method:
            for x in range(stride):
                a = row[x-bpp] if x >= bpp else 0
                b = previous[x]
                c = previous[x-bpp] if x >= bpp else 0
                if method == 1:
                    predictor = a
                elif method == 2:
                    predictor = b
                elif method == 3:
                    predictor = (a + b) // 2
                else:
                    p = a + b - c
                    pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
                    predictor = a if pa <= pb and pa <= pc else b if pb <= pc else c
                row[x] = (row[x] + predictor) & 255
        alpha_values = row[bpp-1::bpp]
        transparent += sum(value < 255 for value in alpha_values)
        fully_transparent += alpha_values.count(0)
        previous = row
    return {'transparent_pixels':transparent, 'fully_transparent_pixels':fully_transparent}


def estimate_cost(model, usage):
    # USD per million tokens, Standard tier, pricing snapshot 2026-11-13.
    # Unknown/missing usage is NULL, never an invented zero-cost success.
    if model not in MODELS.values() or not isinstance(usage, dict):
        return None
    detail = usage.get('input_tokens_details', {})
    if not all(k in detail for k in ('text_tokens', 'image_tokens')) or 'output_tokens' not in usage:
        return None
    cached = detail.get('cached_tokens_details', {})
    if detail.get('cached_tokens', 0) and not cached:
        return None
    text, image = detail['text_tokens'], detail['image_tokens']
    ct, ci = cached.get('text_tokens', 0), cached.get('image_tokens', 0)
    return round(((text - ct) * 5 + (image - ci) * 8 + ct * 1.25 + ci * 2 + usage['output_tokens'] * 30) / 1e6, 8)


class OpenAIImages:
    name = 'OpenAI Images API'

    def __init__(self, root):
        self.root = root

    def status(self):
        cfg = configuration(self.root)
        valid = cfg['OPENAI_BASE_URL'].rstrip('/') == 'https://api.openai.com/v1'
        valid = valid and cfg['OPENAI_IMAGE_MODEL'] == MODELS['sunburst'] and cfg['OPENAI_IMAGE_MODEL_FAST'] == MODELS['flare']
        ready = bool(cfg['OPENAI_API_KEY']) and valid
        return {'ready': ready, 'provider': self.name, 'models': MODELS,
                'account_verified': False,
                'message': ('已配置后端密钥；账户权限与额度以实际调用结果为准。' if ready else
                            '请在后端 .env 配置 OPENAI_API_KEY。' if valid else
                            '请使用官方 Base URL 和已核实的 GPT Image 2.6 模型 ID。')}

    def generate(self, prompt, references, count, model, background):
        cfg = configuration(self.root)
        if not self.status()['ready'] or model not in MODELS.values():
            raise APIError(self.status()['message'])
        fields = {'model': model, 'prompt': prompt, 'n': count, 'quality': 'high',
                  'size': '1024x1024', 'output_format': 'png', 'background': background}
        endpoint = '/images/edits' if references else '/images/generations'
        if references:
            boundary = 'folio-' + uuid.uuid4().hex
            parts = []
            for key, value in fields.items():
                parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, key, value)).encode())
            for i, content in enumerate(references):
                import product_assets
                ext = product_assets.image_extension(content)
                mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}.get(ext)
                if not mime:
                    raise APIError('参考图仅支持 PNG、JPEG、WebP')
                parts.extend([('--%s\r\nContent-Disposition: form-data; name="image[]"; filename="reference-%d%s"\r\nContent-Type: %s\r\n\r\n' % (boundary, i, ext, mime)).encode(), content, b'\r\n'])
            parts.append(('--%s--\r\n' % boundary).encode())
            data, content_type = b''.join(parts), 'multipart/form-data; boundary=' + boundary
        else:
            data, content_type = json.dumps(fields).encode(), 'application/json'
        request = urllib.request.Request(cfg['OPENAI_BASE_URL'].rstrip('/') + endpoint, data=data,
                                        headers={'Authorization': 'Bearer ' + cfg['OPENAI_API_KEY'], 'Content-Type': content_type})
        try:
            timeout = max(30, min(600, int(cfg['OPENAI_IMAGE_TIMEOUT'])))
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                request_id = response.headers.get('x-request-id')
                raw = response.read(280 * 1024 * 1024 + 1)
                if len(raw) > 280 * 1024 * 1024:
                    raise APIError('响应超过安全大小，结果待核对。', request_id=request_id, uncertain=True)
                try:
                    payload = json.loads(raw)
                    images = [base64.b64decode(im['b64_json'], validate=True) for im in payload['data']]
                    if not 1 <= len(images) <= count:
                        raise ValueError('count')
                    for content in images:
                        png_info(content)
                except (ValueError, KeyError, TypeError):
                    raise APIError('API 返回了无效或不完整图片；请用请求 ID 核对账单。', request_id=request_id, uncertain=True)
                return {'images': images, 'usage': payload.get('usage'), 'request_id': request_id}
        except urllib.error.HTTPError as exc:
            request_id = exc.headers.get('x-request-id')
            try:
                error = json.loads(exc.read(65536)).get('error', {})
                code = error.get('code')
            except (ValueError, AttributeError):
                code = None
            messages = {400: '请求参数或内容被 API 拒绝。', 401: 'API Key 无效，请检查后端 .env。',
                        403: '账户没有模型权限，可能需要组织验证。', 404: '账户无法访问该模型或接口。',
                        429: 'API 限流，请稍后重试。'}
            message = 'API 额度不足，请检查账户账单。' if code == 'insufficient_quota' else messages.get(exc.code, 'API 服务异常，结果需要核对。')
            try:
                delay = min(3600, max(2, int(exc.headers.get('Retry-After', '15'))))
            except ValueError:
                delay = 60
            raise APIError(message, exc.code, request_id,
                           delay if exc.code == 429 and code != 'insufficient_quota' else 0,
                           uncertain=exc.code >= 500 or exc.code in (408, 409)) from None
        except APIError:
            raise
        except Exception:
            raise APIError('网络超时或连接中断，无法确认是否扣费；请先核对 OpenAI 记录。', uncertain=True) from None


class LK888Images:
    """Documented LK888 async media protocol; one durable task per output."""
    name = 'LK888 · TT Image 2.6（第三方）'
    model = 'tt-image-2.6'

    def __init__(self, root):
        self.root = root

    def status(self):
        cfg = configuration(self.root)
        valid = cfg['OPENAI_BASE_URL'].rstrip('/') == 'https://api.lk888.ai/v1'
        ready = bool(cfg['OPENAI_API_KEY']) and valid
        return {'ready': ready, 'provider': self.name, 'provider_kind': 'lk888', 'models': {'sunburst': self.model, 'flare': self.model},
                'account_verified': False, 'message': '第三方异步生图 · Sunburst / Flare · 高质量 PNG；费用按平台实际算力记录。' if ready else
                '请在后端 .env 配置第三方密钥及 https://api.lk888.ai/v1。'}

    def request(self, path, body=None):
        cfg = configuration(self.root)
        if not self.status()['ready']:
            raise APIError(self.status()['message'])
        request = urllib.request.Request(cfg['OPENAI_BASE_URL'].rstrip('/') + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + cfg['OPENAI_API_KEY'], 'Content-Type': 'application/json', 'Accept-Language': 'zh-CN'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=90) as response:
                payload = json.loads(response.read(2 * 1024 * 1024))
            if body is not None and payload.get('code') != 200:
                code = payload.get('error_code', '')
                messages = {'insufficient_balance': '平台余额不足', 'quota_exceeded': 'API Key 额度不足',
                            'key_config_error': '密钥未开通所需模型或渠道', 'invalid_request': '平台拒绝请求参数',
                            'content_policy_violation': '平台内容审核未通过', 'channel_unavailable': '平台暂无可用渠道'}
                raise APIError(messages.get(code, '平台未创建任务，请检查平台调用记录') + ('（' + code + '）' if re.fullmatch(r'[a-z_]{1,60}', code) else ''),
                               status=payload.get('code'))
            return payload
        except urllib.error.HTTPError as exc:
            messages = {401: '第三方密钥无效', 402: '第三方余额或额度不足', 403: '密钥没有所需渠道权限',
                        400: '平台拒绝请求参数', 404: '平台接口或任务不存在', 429: '平台限流，请稍后重试'}
            raise APIError(messages.get(exc.code, '第三方服务异常'), exc.code,
                           retry_after=15 if exc.code == 429 else 0, uncertain=body is not None and exc.code >= 500) from None
        except APIError:
            raise
        except Exception:
            raise APIError('第三方连接超时或响应无效；已取得任务号时可继续查询，不会重新提交。', uncertain=body is not None) from None

    def submit(self, prompt, references, version, background):
        import product_assets
        if version not in ('sunburst', 'flare'):
            raise APIError('无效模型版本')
        if any(len(x) > 10*1024*1024 for x in references) or sum(map(len, references)) > 30*1024*1024:
            raise APIError('参考图超过平台限制：单张 10 MB、合计 30 MB')
        images = []
        for content in references:
            ext = product_assets.image_extension(content)
            mime = {'.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.webp':'image/webp'}.get(ext)
            if not mime:
                raise APIError('不支持的参考图片格式')
            images.append('data:' + mime + ';base64,' + base64.b64encode(content).decode())
        params = {'resolution': '1K', 'version': version, 'background': background, 'output_format': 'png', 'quality': 'high', 'size': '1024x1024'}
        if images:
            params['images'] = images
        result = self.request('/media/generate', {'model': self.model, 'prompt': prompt, 'params': params})
        task_id = str(result.get('data', {}).get('task_id', ''))
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', task_id):
            raise APIError('平台未返回有效任务号，请核对平台账单后重试。', uncertain=True)
        return task_id

    def generate_remote(self, prompt, references, version, background, task_id, final, on_update):
        import time
        if not task_id:
            task_id = self.submit(prompt, references, version, background)
            # Persist immediately, before the first read-only status query.
            on_update(task_id, None)
        deadline = time.monotonic() + 1200
        failures = 0
        while not final or final.get('is_final') is not True:
            try:
                final = self.request('/skills/task-status?task_id=' + task_id)
                failures = 0
                on_update(task_id, final)
            except APIError as exc:
                failures += 1
                if failures >= 6 or exc.status in (401, 403, 404):
                    raise
            if final and final.get('is_final') is True:
                break
            if time.monotonic() >= deadline:
                raise APIError('平台任务仍未完成，已保存任务号；点击重试将继续查询，不会重复提交。')
            time.sleep(5)
        if final.get('state') != 'success' or final.get('error'):
            code = str(final.get('error_code', 'unknown'))
            safe_code = code if re.fullmatch(r'[a-z_]{1,60}', code) else 'unknown'
            raise APIError('平台生成失败（' + safe_code + '），请查看调用记录。', request_id=task_id)
        urls = final.get('result_urls') or [final.get('result_url')]
        if len(urls) != 1 or not urls[0]:
            raise APIError('平台已完成但未返回单张图片地址；保留任务号以便继续下载。', request_id=task_id)
        content = download_public_image(urls[0])
        png_info(content)
        return {'images': [content], 'request_id': task_id, 'usage': None,
                'provider_cost': final.get('cost'), 'cost_unit': '平台算力', 'variant': version}


def download_public_image(url):
    """Unauthenticated, bounded download; credentials never leave API host."""
    import ipaddress
    import socket
    import urllib.parse
    # Each redirect is independently checked; private/local destinations are refused.
    for _ in range(5):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise APIError('平台图片地址不符合 HTTPS 公网要求')
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
            raise APIError('平台图片地址不是公网地址')
        try:
            with urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(url), timeout=90) as response:
                content = response.read(50*1024*1024 + 1)
            if len(content) > 50*1024*1024:
                raise APIError('平台图片超过 50 MB')
            return content
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get('Location'):
                url = urllib.parse.urljoin(url, exc.headers['Location'])
                continue
            raise APIError('平台图片下载失败；重试将下载已有任务结果。') from None
    raise APIError('平台图片重定向次数过多')
