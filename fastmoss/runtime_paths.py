"""Writable per-user storage; source assets are never a Windows data directory."""
import json
import os
import sys
from pathlib import Path


def data_directory(root):
    if os.environ.get('TKSHANGJIA_DATA_DIR'):
        data = Path(os.environ['TKSHANGJIA_DATA_DIR']).expanduser().resolve()
    elif sys.platform == 'win32':
        data = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'TKShangjia'
    else:
        data = Path(root) / 'data'  # Preserve existing macOS installations.
    for name in ('config', 'database', 'products', 'images', 'logs', 'credentials'):
        (data / name).mkdir(parents=True, exist_ok=True)
    return data


def database_path(data):
    if os.environ.get('FASTMOSS_DB_PATH'):
        return Path(os.environ['FASTMOSS_DB_PATH'])
    legacy = data / 'fastmoss.sqlite3'
    return legacy if legacy.exists() else data / 'database' / 'fastmoss.sqlite3'


def read_runtime(data):
    try:
        return json.loads((Path(data) / 'config' / 'runtime.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def write_runtime(data, value):
    path = Path(data) / 'config' / 'runtime.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value), encoding='utf-8')
    temp.replace(path)
