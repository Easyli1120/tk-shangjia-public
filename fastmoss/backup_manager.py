"""List and restore local SQLite backups; caller serializes all server requests."""
import datetime, sqlite3
from database import ClosingConnection
from pathlib import Path

def directory(server):
    path=server.DB.parent/'backups';path.mkdir(exist_ok=True);return path

def list_backups(server):
    items=[]
    for p in directory(server).glob('*.sqlite3'):
        if p.is_symlink() or not p.is_file():continue
        stat=p.stat()
        items.append({'name':p.name,'size':stat.st_size,'created_at':datetime.datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec='seconds')})
    return sorted(items,key=lambda x:(x['created_at'],x['name']),reverse=True)

def restore(server,name):
    if not isinstance(name,str) or not name or Path(name).name!=name or not name.endswith('.sqlite3'):
        raise ValueError('备份文件名无效')
    path=directory(server)/name
    if path.is_symlink() or not path.is_file():raise ValueError('备份不存在')
    uri=path.resolve().as_uri()+'?mode=ro'
    with sqlite3.connect(uri,uri=True,factory=ClosingConnection) as src:
        check=src.execute('PRAGMA integrity_check').fetchone()
        if not check or check[0]!='ok':raise ValueError('备份完整性检查失败')
        tables={x[0] for x in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'records','snapshots','batches'}<=tables:raise ValueError('该文件不是可恢复的 FastMoss 数据库')
        safeguard=directory(server)/('before-restore-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
        with server.db() as current,sqlite3.connect(safeguard,factory=ClosingConnection) as saved:
            current.backup(saved)
            # Remote writes cannot be undone by restoring an older local backup.
            has_ledger=current.execute("SELECT 1 FROM sqlite_master WHERE name='shop_listing_jobs'").fetchone()
            ledger=[dict(r) for r in current.execute('SELECT * FROM shop_listing_jobs')] if has_ledger else []
        try:
            with server.db() as dest:src.backup(dest)
            server.init()
            with server.db() as verified:
                for job in ledger:
                    verified.execute('INSERT OR REPLACE INTO shop_listing_jobs ('+','.join(job)+') VALUES ('+','.join('?' for _ in job)+')',tuple(job.values()))
                if verified.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or verified.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('恢复后的数据库校验失败')
        except Exception:
            with sqlite3.connect(safeguard,factory=ClosingConnection) as saved,server.db() as dest:saved.backup(dest)
            raise
    return {'ok':True,'name':name,'safety_backup':safeguard.name}
