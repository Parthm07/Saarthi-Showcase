import json
import os
import re
import unittest
from unittest.mock import patch

os.environ.setdefault('SESSION_SECRET', 'isolated-website-version-test')
import api


class WebsiteVersionTests(unittest.TestCase):
    def test_update_check_and_html_work_without_database_access(self):
        with patch.object(api, '_get_pool', side_effect=AssertionError('Unexpected database access')):
            version = api.app_version()
            page = api.index()
        release = json.loads(version.body)['version']
        self.assertRegex(release, r'^[a-f0-9]{20}$')
        self.assertEqual(version.headers['cache-control'], 'no-store')
        self.assertEqual(page.headers['cache-control'], 'no-store')
        html = page.body.decode()
        self.assertIn(f'name="saarthi-version" content="{release}"', html)
        assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|css)\?v=([^"]+))"', html)
        self.assertGreater(len(assets), 10)
        self.assertTrue(all(value == release for _, value in assets))
        self.assertEqual(api.index_file().body, page.body)
