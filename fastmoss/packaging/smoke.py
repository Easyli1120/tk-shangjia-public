"""Launch the actual desktop executable in fresh, independent data directories."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request


def get(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


def start(command, data):
    env = dict(os.environ, TKSHANGJIA_DATA_DIR=str(data), FASTMOSS_PORT='54322')
    env.pop('FASTMOSS_DB_PATH', None)
    if sys.platform == 'win32':
        # Launch with only Windows on PATH: no Python, Node, or external OCR tools.
        env['PATH'] = os.environ['SystemRoot'] + '\\System32;' + os.environ['SystemRoot']
        env.pop('PYTHONPATH', None)
    process = subprocess.Popen(command + ['--no-browser'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Desktop exited during startup; see its isolated logs')
        try:
            runtime = json.loads((data/'config'/'runtime.json').read_text(encoding='utf-8'))
            health = get('http://127.0.0.1:%d/api/health' % runtime['port'])
            if health['version'] == '2026.11.20-independent-shop' and runtime['pid'] == process.pid:
                assert runtime['port'] == 8765
                return process, runtime
        except (OSError, ValueError, KeyError): pass
        time.sleep(.15)
    process.terminate(); process.wait(timeout=10)
    raise RuntimeError('Desktop startup timed out')


def run(command):
    processes=[]
    with tempfile.TemporaryDirectory(prefix='tk-install-test-') as tmp:
        root=Path(tmp)
        try:
            p,one=start(command,root/'员工一');processes.append(p)
            duplicate_env = dict(os.environ, TKSHANGJIA_DATA_DIR=str(root/'员工一'))
            subprocess.run(command + ['--no-browser'], env=duplicate_env, check=True, timeout=15)
            assert json.loads((root/'员工一/config/runtime.json').read_text(encoding='utf-8'))['pid'] == p.pid
            conflict_env = dict(os.environ, TKSHANGJIA_DATA_DIR=str(root/'员工二'))
            conflict = subprocess.run(command + ['--no-browser'], env=conflict_env, timeout=15)
            assert conflict.returncode == 1
            assert not (root/'员工二/config/runtime.json').exists()
            assert '8765' in (root/'员工二/logs/desktop.log').read_text(encoding='utf-8')
            for name in ('员工一','员工二'):
                data=root/name
                if name == '员工二':
                    # A fresh computer, including upgrades from a previously random port.
                    (data/'config/runtime.json').write_text(json.dumps({'port':54321}),encoding='utf-8')
                    p,runtime=start(command,data);processes.append(p)
                else:runtime=one
                assert runtime['port'] == 8765
                assert (data/'database'/'fastmoss.sqlite3').is_file()
                assert all((data/folder).is_dir() for folder in ('config','database','products','images','logs','credentials'))
                status=get('http://127.0.0.1:%d/api/shop/status' % runtime['port'])
                assert not status['configured'] and not status['authorized']
                assert status['callback']['entry_url'] == 'http://127.0.0.1:8765/'
                assert get('http://127.0.0.1:%d/api/shop/callback-health' % runtime['callback_port'])['probe']
                with urllib.request.urlopen('http://127.0.0.1:%d/shop-management.js' % runtime['port']) as response:
                    assert b'shopManagementPage' in response.read()
                with urllib.request.urlopen('http://127.0.0.1:%d/' % runtime['port']) as response:
                    assert response.status == 200
                p.terminate();p.wait(timeout=10);processes.remove(p)
            if sys.platform == 'win32' and len(command) == 1:
                report = root/'components.json'
                env = dict(os.environ, PATH=os.environ['SystemRoot']+'\\System32;'+os.environ['SystemRoot'])
                subprocess.run(command + ['--check-installation', str(report)], env=env, timeout=180)
                checks = json.loads(report.read_text(encoding='utf-8'))
                print(json.dumps(checks, ensure_ascii=False))
                assert checks['passed'], checks
            print('PASS: fixed 8765 address, no port fallback, duplicate startup, legacy port upgrade, isolated Unicode data paths, database creation, callback listener, assets, no bundled credentials')
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                process.wait(timeout=10)


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--executable');args=parser.parse_args()
    run([str(Path(args.executable).resolve())] if args.executable else [sys.executable,str(Path(__file__).resolve().parents[1]/'desktop.py')])
