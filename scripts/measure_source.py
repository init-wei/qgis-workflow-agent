#!/usr/bin/env python3
"""Count selected source blobs from one immutable Git commit or tree.

Run at the repository root. Fails on an unclassified matching path, non-UTF-8
text, a non-regular blob, or an incomplete Git response. Never reads worktree
source files. CSV scope intentionally matches docs/source-lines.csv at 9602db.
"""
import argparse
import csv
import io
import subprocess
import sys
from pathlib import PurePosixPath

EXTENSIONS = {'.py', '.vue', '.ts', '.js', '.css'}
BUILD_CONFIGS = {
    'ui/frontend/postcss.config.js',
    'ui/frontend/tailwind.config.js',
    'ui/frontend/vite.config.js',
}
EXCLUDED_PARTS = {
    '.git', '.runtime', '.target', '.venv', 'venv', '__pycache__',
    '.pytest_cache', '.ruff_cache', '.cache', 'node_modules', 'dist',
    'dist-ssr', 'build', 'coverage', 'vendor', 'third_party', 'third-party',
    'thirdparty', 'generated',
}
CATEGORIES = ('backend', 'qgis_bridge', 'frontend', 'tests', 'scripts',
              'frontend_build_config')
FIELDS = ('path', 'blob_sha', 'category', 'physical_lines', 'nonblank_lines')


def git(*args, input_data=None):
    return subprocess.run(['git', *args], input=input_data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True).stdout


def classify(path):
    parts = PurePosixPath(path).parts
    if any(p in EXCLUDED_PARTS for p in parts):
        return None
    if path.startswith(('shared/', 'ui/backend/frontend/', 'data/test-data/raw/',
                        'data/test-data/derived/')):
        return None
    if path.startswith(('agent/', 'ui/backend/')):
        return 'backend'
    if path.startswith('qgis_mcp_server/'):
        return 'qgis_bridge'
    if path.startswith('ui/frontend/src/'):
        return 'frontend'
    if path.startswith('tests/'):
        return 'tests'
    if path.startswith('scripts/'):
        return 'scripts'
    if path in BUILD_CONFIGS:
        return 'frontend_build_config'
    raise ValueError(f'unclassified source candidate: {path}; review scope before counting')


def measure(ref):
    tree = git('rev-parse', '--verify', '--end-of-options', f'{ref}^{{tree}}').decode().strip()
    raw_tree = git('ls-tree', '-r', '-z', '--full-tree', tree)
    objects = []
    excluded = []
    for item in raw_tree.split(b'\0'):
        if not item:
            continue
        header, raw_path = item.split(b'\t', 1)
        mode, kind, blob_sha = header.decode('ascii').split(' ')
        path = raw_path.decode('utf-8', errors='strict')
        if PurePosixPath(path).suffix not in EXTENSIONS:
            continue
        category = classify(path)
        if category is None:
            excluded.append(path)
            continue
        if mode not in ('100644', '100755') or kind != 'blob':
            raise ValueError(f'source is not a regular blob: {path} ({mode} {kind})')
        objects.append((path, blob_sha, category))
    if not objects:
        raise ValueError('no source blobs found')

    blob_results = git('cat-file', '--batch', input_data=''.join(
        f'{sha}\n' for _, sha, _ in objects).encode('ascii'))
    stream = io.BytesIO(blob_results)
    rows = []
    for path, expected_sha, category in objects:
        header = stream.readline().rstrip(b'\n').decode('ascii')
        fields = header.split(' ')
        if len(fields) != 3 or fields[0] != expected_sha or fields[1] != 'blob':
            raise ValueError(f'unexpected blob header for {path}: {header}')
        size = int(fields[2])
        raw = stream.read(size)
        if len(raw) != size or stream.read(1) != b'\n':
            raise ValueError(f'truncated blob for {path}')
        normalized = raw.decode('utf-8', errors='strict').replace('\r\n', '\n')
        lines = normalized.split('\n') if normalized else []
        if lines and normalized.endswith('\n'):
            lines.pop()
        rows.append(dict(path=path, blob_sha=expected_sha, category=category,
                         physical_lines=len(lines),
                         nonblank_lines=sum(bool(line.strip()) for line in lines)))
    if stream.read():
        raise ValueError('unexpected trailing blob data')
    return tree, rows, excluded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ref', help='immutable Git commit or tree to measure')
    parser.add_argument('--csv', required=True, help='destination for CSV')
    parser.add_argument('--compare', help='compare every CSV field with existing CSV')
    args = parser.parse_args()
    tree, rows, excluded = measure(args.ref)
    if args.compare:
        with open(args.compare, newline='', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            if tuple(reader.fieldnames or ()) != FIELDS:
                raise ValueError(f'CSV field names differ: {reader.fieldnames}')
            old = list(reader)
        actual = [{key: str(row[key]) for key in FIELDS} for row in rows]
        if actual != old:
            old_by_path = {row['path']: row for row in old}
            actual_by_path = {row['path']: row for row in actual}
            for path in sorted(old_by_path.keys() | actual_by_path.keys()):
                if old_by_path.get(path) != actual_by_path.get(path):
                    print(f'mismatch {path}: old={old_by_path.get(path)} new={actual_by_path.get(path)}', file=sys.stderr)
            if actual_by_path == old_by_path:
                print('CSV rows have different order or duplicate paths', file=sys.stderr)
            raise ValueError('comparison failed')
        print(f'comparison: {len(rows)} rows match in path, blob SHA, category and both counts')
    with open(args.csv, 'w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    print(f'ref={args.ref} tree={tree} successful_files={len(rows)} excluded_candidates={len(excluded)}')
    for path in excluded:
        print(f'excluded={path}')
    print('category,files,physical_lines,nonblank_lines')
    for category in CATEGORIES:
        group = [r for r in rows if r['category'] == category]
        print(f'{category},{len(group)},{sum(r["physical_lines"] for r in group)},{sum(r["nonblank_lines"] for r in group)}')
    print(f'total,{len(rows)},{sum(r["physical_lines"] for r in rows)},{sum(r["nonblank_lines"] for r in rows)}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, UnicodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
