"""The pinned downloaded inputs of the boundary datasets.

geo_sources.lock.json, beside this file, names each input with its version,
URL, size and SHA-256. The build scripts read their pins from it, and
fetch_geo_sources.py downloads what is missing. Python 3 standard library only.
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
LOCK_PATH = os.path.join(HERE, 'geo_sources.lock.json')

REQUIRED_FIELDS = ('key', 'name', 'version', 'license', 'path', 'bytes', 'sha256')


class SourceError(Exception):
    """A pinned input is missing, or is not the pinned file."""


def load_lock(path=LOCK_PATH):
    """The locked inputs as {key: entry}, in file order."""
    with open(path, encoding='utf-8') as stream:
        entries = json.load(stream)['files']
    locked = {}
    for entry in entries:
        missing = [field for field in REQUIRED_FIELDS if field not in entry]
        if missing:
            raise SourceError(f'lock entry {entry.get("key")!r} lacks {", ".join(missing)}')
        if ('url' in entry) == ('provided_by' in entry):
            raise SourceError(f'lock entry {entry["key"]!r} needs a url or a provided_by note, not both')
        if entry['key'] in locked:
            raise SourceError(f'lock entry {entry["key"]!r} is listed twice')
        locked[entry['key']] = entry
    return locked


def path_of(entry, root=ROOT):
    return os.path.join(root, *entry['path'].split('/'))


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def problem_with(entry, root=ROOT):
    """Why the file of [entry] is not the pinned file; None when it is."""
    path = path_of(entry, root)
    if not os.path.isfile(path):
        return 'missing'
    size = os.path.getsize(path)
    if size != entry['bytes']:
        return f'{size} bytes, expected {entry["bytes"]}'
    actual = sha256_of(path)
    if actual != entry['sha256']:
        return f'SHA-256 is {actual}, expected {entry["sha256"]}'
    return None
