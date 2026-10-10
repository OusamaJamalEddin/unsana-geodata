"""Copies the pinned files of the Overture Maps divisions theme and checks them.

Run from the repository root, with the Python environment of requirements.txt:

    python tool/geo/fetch_overture_divisions.py            copy what is missing
    python tool/geo/fetch_overture_divisions.py --check    only check; copy nothing
    python tool/geo/fetch_overture_divisions.py --root=DIR copy under DIR instead
                                                           of the repository

Overture publishes each release as Parquet files in a public bucket and asks
users to read only the data they need. So an input here is not a download of
a whole remote file: it is a copy of selected columns, made with DuckDB as
the Overture documentation describes. geo_sources.lock.json names each copy
with its release, remote file, columns, row count, size and SHA-256.

The size and the SHA-256 are those of the copy as DuckDB wrote it, with the
DuckDB version named in the lock entry. They are not those of the remote
file. A copy that is not the pinned one is deleted and reported: the build
never reads it. One remote file is read at a time, on one thread, so the
requests follow each other.

Needs duckdb with its httpfs and spatial extensions, which DuckDB installs
from its own extension repository on first use. Tests:
test_fetch_overture_divisions.py.
"""
import os
import sys

import geo_sources

MEMORY_LIMIT = '6GB'
S3_REGION = 'us-west-2'


def overture_entries(locked):
    """The lock entries that are copies of Overture files, in file order."""
    return [entry for entry in locked.values() if 'remote' in entry]


def copy_statement(entry, target):
    """The DuckDB statement that copies the pinned columns of the remote file to [target]."""
    return (f"COPY (SELECT {entry['columns']} FROM read_parquet('{entry['remote']}')) "
            f"TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)")


def connect():
    import duckdb  # Only needed to copy; a check reads no Parquet.
    connection = duckdb.connect()
    connection.execute('INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;')
    connection.execute(f"SET s3_region='{S3_REGION}';")
    connection.execute('SET threads=1;')
    connection.execute(f"SET memory_limit='{MEMORY_LIMIT}';")
    connection.execute('SET preserve_insertion_order=false;')
    return connection


def _copy_one(connection, entry, root):
    """Copies one input. Returns why it failed, or None."""
    path = geo_sources.path_of(entry, root)
    part = path + '.part'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        connection.execute(copy_statement(entry, part.replace('\\', '/')))
        rows = connection.execute('SELECT count(*) FROM read_parquet(?)', [part.replace('\\', '/')]).fetchone()[0]
        if rows != entry['rows']:
            return f'the copy holds {rows} rows, expected {entry["rows"]}'
        os.replace(part, path)
        found = geo_sources.problem_with(entry, root)
        if found is not None:
            os.remove(path)
            return (f'the copy holds the pinned {rows} rows but is {found}. The pin is the file as '
                    f'{entry["written_by"]} wrote it; another version may write other bytes')
        return None
    except Exception as error:  # DuckDB raises its own error types for the network and the file.
        return f'the copy failed: {error}'
    finally:
        if os.path.exists(part):
            os.remove(part)


def fetch(entries, root=geo_sources.ROOT, check_only=False, log=print, connect=connect):
    """Puts every input of [entries] in place. Returns the problems left."""
    problems = []
    connection = None
    for entry in entries:
        found = geo_sources.problem_with(entry, root)
        if found is None:
            log(f'ok       {entry["key"]}: {entry["path"]}')
            continue
        if check_only:
            problems.append(f'{entry["key"]}: {entry["path"]} is {found}')
        else:
            log(f'copying  {entry["key"]}: {entry["remote"]}')
            connection = connection or connect()
            failure = _copy_one(connection, entry, root)
            if failure is None:
                log(f'ok       {entry["key"]}: copied, {entry["bytes"]} bytes, checksum as pinned')
                continue
            problems.append(f'{entry["key"]}: {failure}')
        log(f'PROBLEM  {problems[-1]}')
    return problems


def main(arguments):
    check_only = '--check' in arguments
    keys = [argument for argument in arguments if not argument.startswith('--')]
    entries = overture_entries(geo_sources.load_lock())
    unknown = [key for key in keys if key not in {entry['key'] for entry in entries}]
    if unknown:
        sys.exit(f'not an Overture input of the lock file: {", ".join(unknown)}')
    if keys:
        entries = [entry for entry in entries if entry['key'] in keys]
    roots = [argument.split('=', 1)[1] for argument in arguments if argument.startswith('--root=')]
    root = os.path.abspath(roots[-1]) if roots else geo_sources.ROOT
    problems = fetch(entries, root, check_only=check_only)
    print(f'{len(entries) - len(problems)} of {len(entries)} inputs in place')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
