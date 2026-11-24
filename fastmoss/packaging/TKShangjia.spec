# Build on Windows x64 with Python 3.12. Never include developer data or .env.
from pathlib import Path
root = Path(SPECPATH).parent
assets = []
for folder in ('static', 'migrations', 'prompts'):
    for file in (root / folder).rglob('*'):
        if file.is_file() and file.suffix in ('.html','.css','.js','.svg','.sql','.json','.md'):
            assets.append((str(file), str(file.relative_to(root).parent)))
assets.append((str(root / 'scripts' / 'image_text.ps1'), 'scripts'))
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)], binaries=[], datas=assets,
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [('X utf8', None, 'OPTION')], exclude_binaries=True,
          name='TKShangjia', debug=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='TKShangjia')
