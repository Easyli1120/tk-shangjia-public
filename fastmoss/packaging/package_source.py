"""Package source only, using an allowlist that excludes every runtime directory."""
import hashlib
from pathlib import Path
import zipfile


def package():
    root = Path(__file__).resolve().parents[2]
    release = root / 'release'
    release.mkdir(exist_ok=True)
    files = [root / 'README.md', root / '.gitignore', root / '.github/workflows/windows-build.yml']
    app = root / 'fastmoss'
    files += list(app.glob('*.py')) + [app / 'requirements.txt']
    allowed = {
        'static': {'.html', '.css', '.js', '.svg'},
        'migrations': {'.sql'}, 'prompts': {'.json', '.md'},
        'scripts': {'.py', '.swift', '.ps1'}, 'docs': {'.md'},
        'tests': {'.py', '.json', '.cjs'},
        'packaging': {'.py', '.ps1', '.spec', '.iss', '.txt'},
    }
    for folder, suffixes in allowed.items():
        files += [p for p in (app / folder).rglob('*')
                  if p.is_file() and p.suffix in suffixes and '__pycache__' not in p.parts]
    destination = release / 'TKShangjia-source-2026.11.24.zip'
    manifest = []
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(set(files)):
            relative = file.relative_to(root).as_posix()
            content = file.read_bytes()
            archive.writestr('TKShangjia/' + relative, content)
            manifest.append(hashlib.sha256(content).hexdigest() + '  ' + relative)
        archive.writestr('TKShangjia/SOURCE-MANIFEST.sha256', '\n'.join(manifest) + '\n')
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(digest + '  ' + destination.name + '\n', encoding='utf-8')
    print('%s: %d source files, %d bytes, SHA-256 %s' % (destination.name, len(manifest), destination.stat().st_size, digest))


if __name__ == '__main__':
    package()
