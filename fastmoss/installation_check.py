"""Exercise bundled components in a temporary workspace, without real accounts."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.parse


def run(report):
    import browser_capture
    import listing_image_roles
    import platform_support
    import secure_storage
    from PIL import Image, ImageDraw, ImageFont

    checks = {}
    report.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='安装验证-', dir=report.parent) as temporary:
            data = Path(temporary)
            secret = secure_storage.seal(data, {'test': 'local-only'})
            assert secure_storage.unseal(data, secret) == {'test': 'local-only'}
            assert secret.startswith('dpapi:')
            checks['windows_credentials'] = True
            (data / 'images').mkdir()
            picture = Image.new('RGB', (800, 400), 'white')
            ImageDraw.Draw(picture).text((40, 40), 'INSTALL CHECK', fill='black', font=ImageFont.load_default(size=48))
            picture.save(data / 'images/check.png')
            engine = platform_support.bundled_ocr()
            version = subprocess.run([str(engine), '--version'], capture_output=True, timeout=20)
            checks['ocr_engine_version'] = version.stdout.decode('utf-8', 'replace').splitlines()[:1]
            assert version.returncode == 0, ('OCR engine', version.returncode, version.stderr.decode('utf-8', 'replace'))
            result = listing_image_roles.annotate(data, [{'key': 'check', 'local_path': '/images/check.png'}])[0]
            if result['ocr_status'] != 'checked':
                diagnostic = subprocess.run([str(engine), 'stdin', 'stdout', '--tessdata-dir', 'tessdata',
                    '-l', 'eng+chi_sim', '--psm', '11'], cwd=engine.parent,
                    input=(data/'images/check.png').read_bytes(), capture_output=True, timeout=20)
                checks['ocr_diagnostic'] = {'exit_code': diagnostic.returncode, 'error': diagnostic.stderr.decode('utf-8', 'replace')}
            assert result['ocr_status'] == 'checked' and 'INSTALL' in result['ocr_text'].upper(), result
            checks['bundled_ocr_and_corner_scans'] = True
            executable = platform_support.chrome_path()
            assert 'vendor' in Path(executable).parts
            browser = browser_capture.BrowserSession(data, executable)
            connection = None
            try:
                endpoint = browser.ensure()
                connection = browser_capture.Connection(endpoint['endpoint'], time.monotonic() + 60)
                session = connection.call('Target.attachToTarget', {'targetId': endpoint['target'], 'flatten': True})['sessionId']
                assert connection.call('Runtime.evaluate', {'expression': '6 * 7', 'returnByValue': True}, session)['result']['value'] == 42
                checks['bundled_browser'] = True
                # Use a local fixture to verify the frozen --collect worker without TikTok traffic.
                product = {'product_id': '1234567890123456789', 'title': 'Installation check product',
                           'images': [], 'skus': [{'sku_id': '123', 'price': {'sale_price_decimal': '10'}}]}
                html = '<script type="application/json">' + json.dumps(product) + '</script>'
                job = {'url': 'data:text/html,' + urllib.parse.quote(html), 'product_id': product['product_id'],
                       'data': str(data), 'result': str(data / 'result.json'),
                       'progress': str(data / 'progress.json'), 'browser': endpoint}
                spec = data / 'job.json'
                spec.write_text(json.dumps(job), encoding='utf-8')
                subprocess.run([sys.executable, '--collect', str(spec)], check=True, timeout=60)
                collected = json.loads((data / 'result.json').read_text(encoding='utf-8'))
                assert collected.get('product', {}).get('title') == product['title'], collected
                checks['frozen_collection_worker'] = True
            finally:
                if connection:
                    try: connection.call('Browser.close')
                    except (OSError, ValueError, RuntimeError): pass
                    connection.close()
                if browser.process:
                    try: browser.process.wait(timeout=10)
                    except subprocess.TimeoutExpired: platform_support.stop_process(browser.process)
        checks['passed'] = True
    except Exception as error:
        checks.update(passed=False, error=str(error))
    report.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    if not checks['passed']:
        raise SystemExit(1)
