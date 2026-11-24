import subprocess
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import platform_support
import runtime_paths
import model_manager
import openai_images
from database import ClosingConnection


class WindowsBundleTests(unittest.TestCase):
    def test_source_package_excludes_local_credentials_and_screenshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {
                'README.md': 'Example project', '.gitignore': 'fastmoss/data/',
                '.github/workflows/windows-build.yml': 'name: Example',
                'fastmoss/requirements.txt': '',
                'fastmoss/static/shop-guide.js': 'const steps = [];',
                'fastmoss/tests/test_example.cjs': 'console.log("example");',
                'fastmoss/.env': 'PRIVATE-CANARY',
                'fastmoss/data/credentials/tiktok-shop.enc': 'PRIVATE-CANARY',
                'fastmoss/data/database/shop.sqlite3': 'PRIVATE-CANARY',
                'fastmoss/static/tutorial/connection/step-01.png': 'PRIVATE-CANARY',
            }
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')
            script = root / 'fastmoss/packaging/package_source.py'
            script.parent.mkdir(parents=True)
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'packaging/package_source.py', script)
            subprocess.run([sys.executable, str(script)], check=True, capture_output=True)
            with zipfile.ZipFile(next((root / 'release').glob('*.zip'))) as archive:
                self.assertIn('TKShangjia/fastmoss/static/shop-guide.js', archive.namelist())
                self.assertIn('TKShangjia/fastmoss/tests/test_example.cjs', archive.namelist())
                for name in archive.namelist():
                    self.assertNotIn(b'PRIVATE-CANARY', archive.read(name), name)

    def test_image_model_reads_the_employee_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / '安装目录'
            with patch.dict(platform_support.os.environ, {'TKSHANGJIA_DATA_DIR': str(Path(temporary)/'员工数据')}, clear=True):
                data = runtime_paths.data_directory(source)
                with sqlite3.connect(runtime_paths.database_path(data), factory=ClosingConnection) as connection:
                    connection.row_factory = sqlite3.Row
                    model_manager.migrate(connection)
                    model_manager.save(connection, source, data, {'id': 'image-sunburst',
                        'api_key': 'offline-test-secret', 'base_url': 'https://example.com/v1'})
                configuration = openai_images.configuration(source)
                self.assertEqual(configuration['OPENAI_API_KEY'], 'offline-test-secret')
                self.assertEqual(configuration['OPENAI_BASE_URL'], 'https://example.com/v1')
                self.assertFalse(source.exists())

    def test_database_commit_rollback_and_file_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'data.sqlite3'
            with sqlite3.connect(path, factory=ClosingConnection) as connection:
                connection.execute('CREATE TABLE example(value)')
                connection.execute('INSERT INTO example VALUES(1)')
            with self.assertRaises(ValueError):
                with sqlite3.connect(path, factory=ClosingConnection) as connection:
                    connection.execute('INSERT INTO example VALUES(2)')
                    raise ValueError('rollback')
            with sqlite3.connect(path, factory=ClosingConnection) as connection:
                self.assertEqual(connection.execute('SELECT value FROM example').fetchall(), [(1,)])
            path.unlink()  # Windows refuses deletion if a connection still holds this file.

    def test_bundled_browser_without_system_browser(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            browser = root / 'vendor/chrome-win64/chrome.exe'
            browser.parent.mkdir(parents=True)
            browser.touch()
            with patch.object(platform_support, '__file__', str(root/'platform_support.py')), \
                 patch.object(platform_support.sys, 'platform', 'win32'), \
                 patch.dict(platform_support.os.environ, {}, clear=True):
                self.assertEqual(platform_support.chrome_path(), str(browser))

    def test_ocr_uses_bundled_models_and_does_not_mark_failure_clean(self):
        engine = Path('/test with spaces/vendor/tesseract/tesseract.exe')
        with patch.object(platform_support, 'bundled_ocr', return_value=engine), \
             patch.object(Path, 'read_bytes', return_value=b'PNG bytes'), \
             patch.object(platform_support.subprocess, 'run', side_effect=[
                 subprocess.CompletedProcess([], 0, b'Cotton shirt\n'),
                 subprocess.CalledProcessError(1, ['tesseract'])]) as recognize:
            result = platform_support.recognize_images(['image one.png', 'image two.png'])
        self.assertEqual(result, {'image one.png': 'Cotton shirt'})
        self.assertEqual(recognize.call_args_list[0].args[0], [str(engine), 'stdin', 'stdout',
            '--tessdata-dir', 'tessdata', '-l', 'eng+chi_sim', '--psm', '11'])
        self.assertEqual(recognize.call_args_list[0].kwargs['input'], b'PNG bytes')
        self.assertEqual(recognize.call_args_list[0].kwargs['cwd'], engine.parent)


if __name__ == '__main__': unittest.main()
