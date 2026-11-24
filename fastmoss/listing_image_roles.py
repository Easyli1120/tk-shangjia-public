"""Read image text and corners locally; never alter source images."""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from contextlib import nullcontext

import content_review
import platform_support

_lock = threading.Lock()
VERSION = '3-corners'


def ocr_views(path, directory):
    """Create temporary OCR inputs; preserve the original product image."""
    from PIL import Image, ImageOps
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert('RGBA')
        background = Image.new('RGBA', image.size, 'white')
        background.alpha_composite(image)
        image = background.convert('RGB')
    image.thumbnail((2000, 2000))
    full = directory / 'full.png'
    image.save(full)
    views = [('全图', full)]
    width, height = image.size
    cw, ch = max(1, round(width * .55)), max(1, round(height * .4))
    for index, (name, x, y) in enumerate((('左上角', 0, 0), ('右上角', width-cw, 0),
                                          ('左下角', 0, height-ch), ('右下角', width-cw, height-ch))):
        crop = image.crop((x, y, x+cw, y+ch))
        scale = min(3, 1800 / max(crop.size))
        crop = crop.resize((max(1, round(cw*scale)), max(1, round(ch*scale))), Image.Resampling.LANCZOS)
        for mode, view in (('color', crop), ('contrast', ImageOps.autocontrast(ImageOps.grayscale(crop)))):
            target = directory / f'{index}-{mode}.png'
            view.save(target)
            views.append((name, target))
    return views


def role_from_text(text):
    text = text.casefold()
    if re.search(r'\b(?:size\s*(?:chart|guide)|sizing\s*chart|measurement\s*chart)\b|尺码表|尺寸表', text):
        return 'size_chart'
    headers = set(re.findall(r'\b(?:bust|chest|waist|hips?|shoulder|sleeve|inseam|length)\b', text))
    size_rows = set(re.findall(r'(?m)^\s*(?:xxs|xs|s|m|l|xl|xxl|[2-6]xl)\s*$', text))
    if (len(headers) >= 3 or (len(headers) >= 2 and len(size_rows) >= 3 and re.search(r'\bsize\b', text))) and len(re.findall(r'\b\d+(?:\.\d+)?\b', text)) >= 8:
        return 'size_chart'
    if re.search(r'(?m)^\s*(?:warm\s+|kind\s+)?reminder\s*[:!]?\s*$', text):
        return 'reminder'
    return 'product'


def annotate(data, images, *, cache_only=False, run=None, progress=None):
    """Cache by image bytes, so recaptures and replacements are re-evaluated."""
    if not images: return images
    root = (Path(data) / 'images').resolve()
    script = Path(__file__).with_name('scripts') / 'image_text.swift'
    tools = Path(data) / 'image-analysis'
    cache_file = tools / 'roles.json'
    result = [dict(im) for im in images]
    run = run or subprocess.run
    with (nullcontext() if cache_only else _lock):
        try:
            cache = json.loads(cache_file.read_text(encoding='utf-8')) if cache_file.exists() else {}
        except (OSError, ValueError):
            cache = {}
        pending = {}; fingerprints = {}
        for image in result:
            image['ocr_status'] = 'unavailable'
            image['ocr_error'] = '图片尚未缓存在本机'
            explicit_role = bool(re.fullmatch(r'尺码表|尺寸表|size[ _-]?(?:chart|guide)', image.get('group_name',''), re.I))
            image['role'] = 'size_chart' if explicit_role else 'unknown'
            if not image.get('local_path'): continue
            path = (Path(data) / image['local_path'].lstrip('/')).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                image['ocr_error'] = '本地图片文件缺失'
                continue
            try:
                fingerprint = VERSION + hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                image['ocr_error'] = '图片读取失败：' + str(exc)
                continue
            image['ocr_error'] = '图片尚未完成识别'
            fingerprints[image['key']] = fingerprint
            if not isinstance(cache.get(fingerprint), dict) or not cache[fingerprint].get('complete', False):
                pending[str(path)] = fingerprint
        error = ''
        if pending and not cache_only:
            try:
                tools.mkdir(parents=True, exist_ok=True)
                if sys.platform == 'win32':
                    command = ['powershell.exe','-NoProfile','-NonInteractive','-Command',script.with_suffix('.ps1').read_text(encoding='utf-8')]
                else:
                    binary = tools / ('image-text-' + hashlib.sha256(script.read_bytes()).hexdigest()[:12])
                    if not binary.exists():
                        if progress: progress('正在准备图片识别程序')
                        run(['/usr/bin/swiftc','-module-cache-path',str(tools/'swift-cache'),str(script),'-o',str(binary)], check=True, capture_output=True, timeout=60)
                    command = [str(binary)]
                with tempfile.TemporaryDirectory(prefix='ocr-', dir=tools) as temporary_views:
                    views = {}
                    for index, path in enumerate(pending):
                        if progress: progress('正在整理识别图片 %d/%d' % (index+1, len(pending)))
                        directory = Path(temporary_views) / str(index)
                        directory.mkdir()
                        try:
                            views[path] = ocr_views(path, directory)
                        except (OSError, ValueError):
                            continue
                    inputs = [str(view) for items in views.values() for _, view in items]
                    if progress: progress('正在识别 %d 张图片（含全图与四角）' % len(views))
                    if sys.platform == 'win32' and platform_support.bundled_ocr().is_file():
                        recognized = platform_support.recognize_images(inputs, run=run)
                    else:
                        process = run(command, input=json.dumps(inputs), text=True, encoding='utf-8', capture_output=True, check=True, timeout=max(30,len(inputs)*5))
                        recognized = json.loads(process.stdout)
                    if not isinstance(recognized, dict): raise ValueError('识别程序返回格式无效')
                    for path, items in views.items():
                        regions = []; full_text = ''
                        for region, view in items:
                            value = recognized.get(str(view))
                            if isinstance(value, str): value = {'text': value}
                            if not isinstance(value, dict): continue
                            if region == '全图': full_text = value.get('text', '')
                            for text in [value.get('text', ''), *value.get('alternatives', [])]:
                                if text and {'region': region, 'text': text} not in regions:
                                    regions.append({'region': region, 'text': text})
                        complete = all(str(view) in recognized for _, view in items)
                        cache[pending[path]] = {'role': role_from_text(full_text), 'role_text': full_text,
                            'text': '\n'.join(dict.fromkeys(r['text'] for r in regions)),
                            'regions': regions, 'complete': complete}
                if progress: progress('正在保存图片识别结果')
                temporary = cache_file.with_suffix('.tmp')
                temporary.write_text(json.dumps(cache),encoding='utf-8');temporary.replace(cache_file)
            except subprocess.TimeoutExpired:
                error = '图片识别超时，请重试'
            except subprocess.CalledProcessError as exc:
                error = '图片识别程序失败（退出码 %s）' % exc.returncode
            except (OSError, ValueError) as exc:
                error = '图片识别失败：' + str(exc)
        for image in result:
            if image['key'] not in fingerprints:
                continue
            entry = cache.get(fingerprints[image['key']], 'unknown')
            if isinstance(entry, str):
                entry = {'role': entry, 'text': ''}
            # Reuse OCR text while applying the current classification rules.
            role_text = entry.get('role_text', entry.get('text', ''))
            role = role_from_text(role_text) if role_text else entry.get('role', 'unknown')
            image['role'] = 'size_chart' if image['role'] == 'size_chart' else role
            image['ocr_text'] = entry.get('text', '')
            image['ocr_regions'] = entry.get('regions', [])
            image['ocr_status'] = ('checked' if entry.get('complete') else 'partial') if fingerprints[image['key']] in cache else 'unavailable'
            image['ocr_error'] = '' if image['ocr_status']=='checked' else error or ('部分图片区域未能识别，请重试' if fingerprints[image['key']] in cache else '图片尚未完成识别')
            image['text_detected'] = bool(str(image['ocr_text']).strip())
    return result


def cached(data, images):
    # Cache reads never wait on an OCR process holding _lock.
    return annotate(data, images, cache_only=True)


def mark_matches(images, terms):
    """Attach OCR evidence and third-party name matches without editing images."""
    return content_review.image_matches(images, terms)
