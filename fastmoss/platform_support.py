"""Small platform boundaries used by the existing collection and image flows."""
import io
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path


def chrome_path():
    candidates = [os.environ.get('TKSHANGJIA_BROWSER', '')]
    if sys.platform == 'win32':
        candidates.append(str(Path(__file__).parent / 'vendor/chrome-win64/chrome.exe'))
        for base in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            for suffix in ('Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe'):
                candidates.append(str(Path(os.environ.get(base, '')) / suffix))
    else:
        candidates += ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
        candidates += [shutil.which(name) or '' for name in ('google-chrome', 'chromium', 'chromium-browser')]
    return next((p for p in candidates if p and Path(p).is_file()), '')


def bundled_ocr():
    return Path(__file__).parent / 'vendor/tesseract/tesseract.exe'


def recognize_images(paths, run=None):
    """The Windows installer carries the engine and English/Chinese models."""
    engine = bundled_ocr()
    run = run or subprocess.run
    recognized = {}
    for path in paths:
        try:
            # Native OCR libraries may interpret filenames using the Windows ANSI codepage.
            result = run([str(engine), 'stdin', 'stdout', '--tessdata-dir',
                'tessdata', '-l', 'eng+chi_sim', '--psm', '11'],
                input=Path(path).read_bytes(), cwd=engine.parent,
                capture_output=True, check=True, timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            recognized[path] = result.stdout.decode('utf-8').strip()
        except (OSError, subprocess.SubprocessError):
            if run is not subprocess.run: raise
            pass  # Missing results remain unverified and are retried by the caller.
    return recognized


def lock_file(file):
    if sys.platform == 'win32':
        import msvcrt
        file.seek(0)
        if not file.read(1):
            file.write('0'); file.flush()
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)


def stop_process(proc):
    if proc.poll() is not None:
        return
    try:
        if sys.platform == 'win32':
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if sys.platform == 'win32': proc.kill()
            else: os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def image_dimensions(content):
    from PIL import Image
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            if width * height > 100000000 or max(width, height) > 30000:
                raise ValueError('图片尺寸过大')
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            image.load()
        return width, height
    except Exception:
        raise ValueError('图片无法解码或尺寸过大，请重新选择图片') from None


def thumbnail_jpeg(content):
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(content)) as image:
        image = ImageOps.exif_transpose(image).convert('RGB')
        image.thumbnail((1600, 1600))
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=82)
        return output.getvalue()
