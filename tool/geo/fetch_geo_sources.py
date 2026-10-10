"""Downloads the pinned inputs of the boundary datasets and checks them.

Run from the repository root:

    python tool/geo/fetch_geo_sources.py            download what is missing
    python tool/geo/fetch_geo_sources.py --check    only check; download nothing
    python tool/geo/fetch_geo_sources.py tbb cgaz   only the named inputs
    python tool/geo/fetch_geo_sources.py --root=DIR download under DIR instead
                                                    of the repository, to check
                                                    the pins from scratch

geo_sources.lock.json names every input with its version, URL, size and
SHA-256. A file that is already the pinned one is left alone. A download is
written beside its place and moved there only when its size and checksum are
the pinned ones; anything else is deleted, so a wrong file never takes the
place of a pinned one. The exit code is 1 when an input is still not in place.

Python 3 standard library only. Tests: test_fetch_geo_sources.py.
"""
import hashlib
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import geo_sources

USER_AGENT = 'unsana-build/1.0 (boundary dataset inputs)'
TIMEOUT_SECONDS = 120
BLOCK = 1 << 20
# A web server on this machine, for the tests.
LOCAL_HOSTS = ('127.0.0.1', 'localhost')


def _allowed(url):
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == 'https' or (parts.scheme == 'http' and parts.hostname in LOCAL_HOSTS)


def _download(entry, target):
    """Writes the response to [target]. Returns its size and SHA-256."""
    request = urllib.request.Request(entry['url'], headers={'User-Agent': USER_AGENT})
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response, open(target, 'wb') as out:
        while True:
            block = response.read(BLOCK)
            if not block:
                break
            size += len(block)
            if size > entry['bytes']:
                break  # Larger than the pinned file: not worth reading on.
            digest.update(block)
            out.write(block)
    return size, digest.hexdigest()


def _fetch_one(entry, root):
    """Downloads one input. Returns why it failed, or None."""
    if not _allowed(entry['url']):
        return 'its URL is not https'
    path = geo_sources.path_of(entry, root)
    part = path + '.part'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        size, checksum = _download(entry, part)
        if size != entry['bytes']:
            return f'the server sent {"more than " if size > entry["bytes"] else ""}{size} bytes, expected {entry["bytes"]}'
        if checksum != entry['sha256']:
            return f'the download has SHA-256 {checksum}, expected {entry["sha256"]}'
        os.replace(part, path)
        return None
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        return f'the download failed: {error}'
    finally:
        if os.path.exists(part):
            os.remove(part)


def fetch(entries, root=geo_sources.ROOT, check_only=False, log=print):
    """Puts every input of [entries] in place. Returns the problems left."""
    problems = []
    for entry in entries:
        found = geo_sources.problem_with(entry, root)
        if found is None:
            log(f'ok       {entry["key"]}: {entry["path"]}')
            continue
        if check_only:
            problems.append(f'{entry["key"]}: {entry["path"]} is {found}')
        elif 'provided_by' in entry:
            problems.append(f'{entry["key"]}: {entry["path"]} is {found}. {entry["provided_by"]}')
        else:
            log(f'fetching {entry["key"]}: {entry["url"]}')
            failure = _fetch_one(entry, root)
            if failure is None:
                log(f'ok       {entry["key"]}: downloaded, {entry["bytes"]} bytes, checksum as pinned')
                continue
            problems.append(f'{entry["key"]}: {failure}')
        log(f'PROBLEM  {problems[-1]}')
    return problems


def main(arguments):
    check_only = '--check' in arguments
    keys = [argument for argument in arguments if not argument.startswith('--')]
    locked = geo_sources.load_lock()
    unknown = [key for key in keys if key not in locked]
    if unknown:
        sys.exit(f'not in the lock file: {", ".join(unknown)}')
    entries = [locked[key] for key in keys] if keys else list(locked.values())
    roots = [argument.split('=', 1)[1] for argument in arguments if argument.startswith('--root=')]
    root = os.path.abspath(roots[-1]) if roots else geo_sources.ROOT
    problems = fetch(entries, root, check_only=check_only)
    print(f'{len(entries) - len(problems)} of {len(entries)} inputs in place')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
