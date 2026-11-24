import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import listing_image_roles as roles


class CornerOCRTests(unittest.TestCase):
    def test_cache_read_never_waits_for_recognizer_lock_or_starts_ocr(self):
        with roles._lock, patch.object(roles.subprocess,'run') as process:
            result=roles.cached(self.data,[self.image])[0]
        process.assert_not_called()
        self.assertEqual(result['ocr_status'],'unavailable')
        self.assertIn('尚未完成',result['ocr_error'])

    def test_missing_file_and_engine_timeout_report_specific_errors(self):
        missing=roles.annotate(self.data,[dict(self.image,local_path='/images/missing.png')])[0]
        self.assertEqual(missing['ocr_error'],'本地图片文件缺失')
        with patch.object(roles.subprocess,'run',side_effect=subprocess.TimeoutExpired('ocr',30)):
            result=roles.annotate(self.data,[self.image])[0]
        self.assertEqual(result['ocr_status'],'unavailable')
        self.assertIn('超时',result['ocr_error'])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.data = Path(self.folder.name)
        (self.data / 'images').mkdir()
        self.path = self.data / 'images/photo.png'
        Image.new('RGB', (400, 300), 'white').save(self.path)
        self.image = {'key': 'photo', 'local_path': '/images/photo.png'}
        self.tools = self.data / 'image-analysis'
        self.tools.mkdir()
        script = Path(roles.__file__).with_name('scripts') / 'image_text.swift'
        (self.tools / ('image-text-' + hashlib.sha256(script.read_bytes()).hexdigest()[:12])).touch()
        self.outputs = []

    def recognize(self, command, **kwargs):
        paths = json.loads(kwargs['input'])
        self.outputs = paths
        # Same inputs feed either platform's engine; accept Windows text and macOS candidates.
        recognized = {path: 'Dame.ace' for path in paths}
        recognized[paths[0]] = {'text': 'Cotton shirt', 'alternatives': ['Soft fabric']}
        for path in paths:
            with Image.open(path) as image:
                self.assertLessEqual(max(image.size), 2000)
        return subprocess.CompletedProcess(command, 0, json.dumps(recognized))

    def test_all_corners_are_checked_old_cache_is_replaced_and_original_is_unchanged(self):
        original = self.path.read_bytes()
        old_key = '2' + hashlib.sha256(original).hexdigest()
        (self.tools / 'roles.json').write_text(json.dumps({old_key: {'text': '', 'role': 'product'}}))
        with patch.object(roles.subprocess, 'run', side_effect=self.recognize) as process:
            result = roles.annotate(self.data, [self.image])[0]
            cached = roles.annotate(self.data, [self.image])[0]
        self.assertEqual(process.call_count, 1)
        self.assertEqual(result, cached)
        self.assertEqual(result['ocr_status'], 'checked')
        self.assertEqual({r['region'] for r in result['ocr_regions']},
                         {'全图', '左上角', '右上角', '左下角', '右下角'})
        self.assertIn('Soft fabric', result['ocr_text'])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(all(not Path(path).exists() for path in self.outputs))
        Image.new('RGB', (400, 300), 'black').save(self.path)
        with patch.object(roles.subprocess, 'run', side_effect=self.recognize) as process:
            roles.annotate(self.data, [self.image])
        self.assertEqual(process.call_count, 1)

    def test_missing_corner_does_not_become_checked_and_is_retried(self):
        def partial(command, **kwargs):
            result = self.recognize(command, **kwargs)
            values = json.loads(result.stdout)
            values.pop(self.outputs[-1])
            result.stdout = json.dumps(values)
            return result
        with patch.object(roles.subprocess, 'run', side_effect=partial):
            result = roles.annotate(self.data, [self.image])[0]
        self.assertEqual(result['ocr_status'], 'partial')
        with patch.object(roles.subprocess, 'run', side_effect=self.recognize) as process:
            result = roles.annotate(self.data, [self.image])[0]
        self.assertEqual(process.call_count, 1)
        self.assertEqual(result['ocr_status'], 'checked')

    def test_repeated_corner_numbers_do_not_create_false_size_chart(self):
        def recognize(command, **kwargs):
            paths = json.loads(kwargs['input'])
            values = {path: 'Bust Waist Hips 1 2 3' for path in paths}
            return subprocess.CompletedProcess(command, 0, json.dumps(values))
        with patch.object(roles.subprocess, 'run', side_effect=recognize):
            result = roles.annotate(self.data, [self.image])[0]
        self.assertEqual(result['role'], 'product')


if __name__ == '__main__':
    unittest.main()
