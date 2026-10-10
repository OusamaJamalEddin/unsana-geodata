"""Tests of fetch_overture_divisions.py with a stand-in for DuckDB: no network.

    python -m unittest discover -s tool/geo -p "test_*.py"
"""
import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_overture_divisions as fetch  # noqa: E402
import geo_sources  # noqa: E402

PINNED = b'the pinned copy\n' * 300
OTHER = b'the same rows, written differently\n' * 300


def _entry(key, body=PINNED, rows=7):
    return {
        'key': key, 'name': key, 'version': '2026-09-23.1', 'license': 'test',
        'path': f'copies/{key}.parquet', 'provided_by': 'a copy', 'remote': f's3://bucket/release/{key}.parquet',
        'columns': 'id, names, geometry', 'rows': rows, 'written_by': 'DuckDB 1.5.6',
        'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
    }


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    """Writes [body] for a COPY statement and answers the row count with [rows]."""

    def __init__(self, body=PINNED, rows=7, fails=False):
        self.body, self.rows, self.fails = body, rows, fails
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append(statement)
        if statement.startswith('COPY'):
            if self.fails:
                raise OSError('no route to the bucket')
            target = statement.split("TO '", 1)[1].split("'", 1)[0]
            with open(target, 'wb') as stream:
                stream.write(self.body)
        return _Result((self.rows,))


def _never():
    raise AssertionError('the check must not open DuckDB')


class FetchOvertureDivisionsTest(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.root = self._folder.name
        self.addCleanup(self._folder.cleanup)
        self.entry = _entry('overture_division')
        self.path = geo_sources.path_of(self.entry, self.root)

    def fetch(self, connection=None, **more):
        connect = (lambda: connection) if connection else _never
        return fetch.fetch([self.entry], self.root, log=lambda line: None, connect=connect, **more)

    def test_only_the_entries_with_a_remote_file_are_overture_copies(self):
        locked = {'cgaz': {'key': 'cgaz', 'url': 'https://example.org'}, 'a': _entry('a'), 'b': _entry('b')}
        self.assertEqual([entry['key'] for entry in fetch.overture_entries(locked)], ['a', 'b'])

    def test_the_copy_statement_reads_the_pinned_columns_of_the_pinned_remote_file(self):
        self.assertEqual(
            fetch.copy_statement(self.entry, 'D:/out/file.part'),
            "COPY (SELECT id, names, geometry FROM read_parquet('s3://bucket/release/overture_division.parquet')) "
            "TO 'D:/out/file.part' (FORMAT PARQUET, COMPRESSION ZSTD)")

    def test_a_copy_that_is_the_pinned_file_is_put_in_place(self):
        self.assertEqual(self.fetch(_Connection()), [])
        with open(self.path, 'rb') as stream:
            self.assertEqual(stream.read(), PINNED)
        self.assertFalse(os.path.exists(self.path + '.part'))

    def test_a_file_already_in_place_is_left_alone_and_nothing_is_read(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, 'wb') as stream:
            stream.write(PINNED)
        self.assertEqual(self.fetch(), [])

    def test_a_check_reports_a_missing_file_and_copies_nothing(self):
        problems = self.fetch(check_only=True)
        self.assertEqual(problems, ['overture_division: copies/overture_division.parquet is missing'])
        self.assertFalse(os.path.exists(self.path))

    def test_a_copy_with_another_row_count_is_refused(self):
        problems = self.fetch(_Connection(rows=6))
        self.assertEqual(problems, ['overture_division: the copy holds 6 rows, expected 7'])
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.path + '.part'))

    def test_a_copy_with_the_pinned_rows_but_other_bytes_is_refused_and_removed(self):
        problems = self.fetch(_Connection(body=OTHER))
        self.assertEqual(len(problems), 1)
        self.assertIn('holds the pinned 7 rows but is', problems[0])
        self.assertIn('DuckDB 1.5.6', problems[0])
        self.assertFalse(os.path.exists(self.path))

    def test_a_wrong_file_in_place_is_replaced_by_the_pinned_copy(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, 'wb') as stream:
            stream.write(OTHER)
        self.assertEqual(self.fetch(_Connection()), [])
        with open(self.path, 'rb') as stream:
            self.assertEqual(stream.read(), PINNED)

    def test_a_failed_copy_is_reported_and_leaves_nothing_behind(self):
        problems = self.fetch(_Connection(fails=True))
        self.assertEqual(problems, ['overture_division: the copy failed: no route to the bucket'])
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.path + '.part'))


class LockedOvertureCopiesTest(unittest.TestCase):
    """The real lock file: what the build of the city boundaries reads."""

    def setUp(self):
        self.entries = fetch.overture_entries(geo_sources.load_lock())

    def test_one_places_file_and_eight_area_files_of_one_release(self):
        self.assertEqual(sorted(entry['key'] for entry in self.entries),
                         ['overture_division'] + [f'overture_division_area_{number:02d}' for number in range(8)])
        self.assertEqual({entry['version'] for entry in self.entries}, {'2026-09-23.1'})

    def test_every_copy_names_its_remote_file_of_that_release_its_columns_rows_and_writer(self):
        for entry in self.entries:
            self.assertTrue(entry['remote'].startswith(
                's3://overturemaps-us-west-2/release/2026-09-23.1/theme=divisions/type=division'), entry['key'])
            self.assertIn('geometry', entry['columns'])
            self.assertGreater(entry['rows'], 0)
            self.assertEqual(entry['written_by'], 'DuckDB 1.5.6')

    def test_the_area_files_hold_the_rows_of_the_release(self):
        areas = [entry for entry in self.entries if entry['key'].startswith('overture_division_area_')]
        self.assertEqual(sum(entry['rows'] for entry in areas), 1080667)

    def test_the_license_names_the_open_database_license_and_its_sources(self):
        for entry in self.entries:
            self.assertIn('ODbL 1.0', entry['license'])
            self.assertIn('OpenStreetMap', entry['license'])


if __name__ == '__main__':
    unittest.main()
