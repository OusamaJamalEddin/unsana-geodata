"""Tests of fetch_geo_sources.py against a web server on this machine.

    python -m unittest discover -s tool/geo -p "test_*.py"
"""
import hashlib
import http.server
import os
import tempfile
import threading
import unittest

import fetch_geo_sources as fetch

GOOD = b'the pinned boundary file\n' * 400
OTHER = b'another file of the same name\n' * 400


class _Handler(http.server.BaseHTTPRequestHandler):
    files = {}
    requests = []

    def do_GET(self):  # noqa: N802, the name the base class calls
        _Handler.requests.append(self.path)
        body = _Handler.files.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _entry(key, body, path=None, **more):
    entry = {
        'key': key, 'name': key, 'version': '1', 'license': 'test', 'path': path or f'downloads/{key}.bin',
        'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
    }
    entry.update(more)
    return entry


class FetchGeoSourcesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.files = {'/good.bin': GOOD, '/other.bin': OTHER}
        _Handler.requests = []
        self.root = tempfile.mkdtemp(prefix='fetch_geo_')
        self.lines = []

    def run_fetch(self, entries, check_only=False):
        return fetch.fetch(entries, self.root, check_only=check_only, log=self.lines.append)

    def path(self, entry):
        return os.path.join(self.root, *entry['path'].split('/'))

    def test_downloads_a_missing_file_and_keeps_it_when_it_is_the_pinned_one(self):
        entry = _entry('a', GOOD, url=self.base + '/good.bin')

        problems = self.run_fetch([entry])

        self.assertEqual(problems, [])
        with open(self.path(entry), 'rb') as stream:
            self.assertEqual(stream.read(), GOOD)
        self.assertFalse(os.path.exists(self.path(entry) + '.part'))

    def test_leaves_a_file_that_is_already_the_pinned_one_and_asks_no_server(self):
        entry = _entry('a', GOOD, url=self.base + '/good.bin')
        self.run_fetch([entry])
        _Handler.requests = []

        problems = self.run_fetch([entry])

        self.assertEqual(problems, [])
        self.assertEqual(_Handler.requests, [])

    def test_refuses_a_download_whose_checksum_is_not_the_pinned_one_and_keeps_nothing(self):
        pinned = _entry('a', GOOD, url=self.base + '/other.bin')
        pinned['bytes'] = len(OTHER)

        problems = self.run_fetch([pinned])

        self.assertEqual(len(problems), 1)
        self.assertIn('SHA-256', problems[0])
        self.assertFalse(os.path.exists(self.path(pinned)))
        self.assertFalse(os.path.exists(self.path(pinned) + '.part'))

    def test_refuses_a_download_of_another_size_and_keeps_nothing(self):
        pinned = _entry('a', GOOD, url=self.base + '/other.bin')

        problems = self.run_fetch([pinned])

        self.assertEqual(len(problems), 1)
        self.assertIn('bytes', problems[0])
        self.assertFalse(os.path.exists(self.path(pinned)))

    def test_replaces_a_damaged_file_with_the_pinned_one(self):
        entry = _entry('a', GOOD, url=self.base + '/good.bin')
        os.makedirs(os.path.dirname(self.path(entry)))
        with open(self.path(entry), 'wb') as stream:
            stream.write(GOOD[:100])

        problems = self.run_fetch([entry])

        self.assertEqual(problems, [])
        with open(self.path(entry), 'rb') as stream:
            self.assertEqual(stream.read(), GOOD)

    def test_keeps_a_damaged_file_when_the_server_does_not_send_the_pinned_one(self):
        pinned = _entry('a', GOOD, url=self.base + '/other.bin')
        os.makedirs(os.path.dirname(self.path(pinned)))
        with open(self.path(pinned), 'wb') as stream:
            stream.write(b'what was there before')

        problems = self.run_fetch([pinned])

        self.assertEqual(len(problems), 1)
        with open(self.path(pinned), 'rb') as stream:
            self.assertEqual(stream.read(), b'what was there before')

    def test_check_only_downloads_nothing_and_names_what_is_missing(self):
        entry = _entry('a', GOOD, url=self.base + '/good.bin')

        problems = self.run_fetch([entry], check_only=True)

        self.assertEqual(len(problems), 1)
        self.assertIn('missing', problems[0])
        self.assertEqual(_Handler.requests, [])
        self.assertFalse(os.path.exists(self.path(entry)))

    def test_an_input_without_a_url_is_never_downloaded_and_says_where_it_comes_from(self):
        entry = _entry('info', GOOD, provided_by='It comes with the other download.')

        problems = self.run_fetch([entry])

        self.assertEqual(len(problems), 1)
        self.assertIn('It comes with the other download.', problems[0])
        self.assertEqual(_Handler.requests, [])

    def test_a_server_error_is_reported_and_keeps_nothing(self):
        entry = _entry('a', GOOD, url=self.base + '/no-such-file.bin')

        problems = self.run_fetch([entry])

        self.assertEqual(len(problems), 1)
        self.assertFalse(os.path.exists(self.path(entry)))
        self.assertFalse(os.path.exists(self.path(entry) + '.part'))

    def test_refuses_a_url_that_is_not_https(self):
        entry = _entry('a', GOOD, url='http://example.org/good.bin')

        problems = self.run_fetch([entry])

        self.assertEqual(len(problems), 1)
        self.assertIn('https', problems[0])

    def test_one_failure_does_not_stop_the_other_downloads(self):
        bad = _entry('bad', GOOD, url=self.base + '/no-such-file.bin')
        good = _entry('good', GOOD, url=self.base + '/good.bin')

        problems = self.run_fetch([bad, good])

        self.assertEqual(len(problems), 1)
        self.assertTrue(os.path.exists(self.path(good)))


class LockFileTest(unittest.TestCase):
    """The committed lock file itself."""

    def test_every_locked_input_is_well_formed(self):
        locked = fetch.geo_sources.load_lock()

        self.assertGreaterEqual(len(locked), 5)
        for key, entry in locked.items():
            self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$', key)
            self.assertGreater(entry['bytes'], 0, key)
            self.assertTrue(entry['path'].startswith('build/datasets/'), key)
            if 'url' in entry:
                self.assertTrue(entry['url'].startswith('https://'), key)
                for moving in ('/main/', '/master/', '/latest/', '/HEAD/'):
                    self.assertNotIn(moving, entry['url'], f'{key} points at a moving reference')


if __name__ == '__main__':
    unittest.main()
