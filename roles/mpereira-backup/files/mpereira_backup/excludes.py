"""What a repository's backups leave out by choice, and what they keep that looks rebuildable.

One read-only walk of a repository's sources, the way its backups read them (each source path on its own file system,
the excludes, CACHEDIR.TAG, the build outputs), gives two lists:
  left out: each topmost folder or file the backups leave out, with the reason, its size, and its number of files.
            Nothing below it is listed: restic does not go below it either.
  review:   each topmost folder the backups keep whose name looks like a cache, a build output, a package store, a log
            folder, or a temporary folder, and each kept file whose extension looks rebuildable: candidates for the
            excludes. A name is only a hint: the owner decides.
The walk writes one line per finished top-level folder to a progress file, so "--continue" after a stop skips the
folders it finished.
"""
from __future__ import annotations

import collections
import json
import os
import re
import stat

from . import build_outputs
from .config import Config, Repository
from .quarantine import CACHEDIR_SIGNATURE
from .units import excludes_of, home_paths
from .util import Every, Fail, Log, human_bytes, iso, read_json, show, write_atomic, write_json

NAMES = re.compile(
    r'^(caches?|.*[._-]cache|cache[._-].*|tmp|temp|build|builds|target|dist|out|obj|_build|deps|\.tox|\.nox|\.bundle|'
    r'vendor|bower_components|jspm_packages|pods|carthage|deriveddata|\.stack-work|elm-stuff|\.yarn|\.pnpm-store|'
    r'\.gems?|\.metadata|\.idea|\.vscode(-server)?|site-packages|dist-packages|\.eggs|.*\.egg-info|'
    r'\.ipynb_checkpoints|\.?trash|logs?|\.angular|\.svelte-kit|\.nuxt|\.expo|\.serverless|\.aws-sam|cdk\.out|'
    r'\.dart_tool|\.pub-cache|\.android|\.gradle|\.m2|\.cargo|\.rustup|\.npm|\.venv|venv|__pycache__|\.mypy_cache|'
    r'\.pytest_cache|\.ruff_cache|\.next|\.parcel-cache|\.turbo|\.terraform)$', re.I)
# Files, not archives: a .zip or a .tar is as often a keepsake as a download.
EXTS = re.compile(r'\.(o|obj|class|pyc|pyo|log|tmp|swp|part|crdownload|download|vmem|vmsn|nvram|xcarchive|dsym|'
                  r'ipsw)$', re.I)
MIN_FILE = 1 << 20      # a single file is a candidate from 1 MiB: smaller ones are not worth a pattern
ERRORS_SHOWN = 50


def folder_of(cfg: Config) -> str:
    return os.path.join(cfg.state_dir, 'excludes')


def roots_of(repo: Repository, only: list) -> list:
    """(source, path) for each path restic is given, in order."""
    out = []
    for s in repo.sources:
        if only and s.name not in only:
            continue
        if s.kind == 'folder':
            paths = [s.path]
        elif s.kind == 'paths':
            paths = list(s.paths)
        else:
            paths = home_paths(s)
        out += [(s.name, p) for p in paths if os.path.lexists(p)]
    return out


class Walk:
    def __init__(self, repo: Repository, log: Log):
        self.repo = repo
        self.ex = excludes_of(repo, '')
        self.log = log
        self.errors: list = []
        self.every = Every(60)
        self.seen = 0

    def _error(self, path: str, e: OSError) -> None:
        self.errors.append(f'{show(path)}: {e.strerror or e}')

    def _entries(self, folder: str) -> list:
        try:
            with os.scandir(folder) as it:
                return sorted(it, key=lambda e: e.name)
        except OSError as e:
            self._error(folder, e)
            return []

    def size(self, path: str, dev: int) -> tuple:
        """Bytes and files below PATH, on its file system."""
        b = n = 0
        stack = [path]
        while stack:
            for e in self._entries(stack.pop()):
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError as x:
                    self._error(e.path, x)
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if st.st_dev == dev:
                        stack.append(e.path)
                else:
                    b += st.st_size
                    n += 1
        return b, n

    def why(self, path: str, name: str, folder: str, beside: set, is_dir: bool) -> str:
        p = self.ex.which(path)
        if p:
            return f'exclude {p}'
        if is_dir:
            try:
                with open(os.path.join(path, 'CACHEDIR.TAG'), 'rb') as f:
                    if f.read(len(CACHEDIR_SIGNATURE)) == CACHEDIR_SIGNATURE:
                        return 'CACHEDIR.TAG'
            except OSError:
                pass
            if self.repo.build_outputs and build_outputs.is_output(name, folder, beside):
                return 'build output'
        return ''

    def visit(self, path: str, dev: int, out: dict, flagged: bool) -> tuple:
        """Walks a kept folder. Adds what it finds to OUT; returns the bytes and files the backups keep below it."""
        entries = self._entries(path)
        beside = {e.name for e in entries}
        kept_b = kept_n = 0
        for e in entries:
            self.seen += 1
            if self.every():
                self.log(f'{self.repo.name}: {self.seen:,} entries walked; at {show(e.path)}')
            try:
                st = e.stat(follow_symlinks=False)
            except OSError as x:
                self._error(e.path, x)
                continue
            is_dir = stat.S_ISDIR(st.st_mode)
            if is_dir and st.st_dev != dev:
                out['left_out'].append(['another file system', 0, 0, e.path])
                continue
            reason = self.why(e.path, e.name, path, beside, is_dir)
            if reason:
                b, n = self.size(e.path, dev) if is_dir else (st.st_size, 1)
                out['left_out'].append([reason, b, n, e.path])
                continue
            if is_dir:
                hit = not flagged and bool(NAMES.match(e.name))
                b, n = self.visit(e.path, dev, out, flagged or hit)
                if hit:
                    out['review'].append([b, n, e.path])
            else:
                b, n = st.st_size, 1
                if not flagged and st.st_size >= MIN_FILE and EXTS.search(e.name) and stat.S_ISREG(st.st_mode):
                    out['review'].append([b, n, e.path])
            kept_b += b
            kept_n += n
        return kept_b, kept_n


def walk(cfg: Config, repo: Repository, only: list, continuing: bool, log: Log) -> dict:
    """Walks the sources; returns {'left_out': [[reason, bytes, files, path]], 'review': [[bytes, files, path]],
    'kept': [bytes, files], 'errors': [...]}. Progress: one JSON line per finished top-level entry."""
    d = folder_of(cfg)
    os.makedirs(d, exist_ok=True)
    progress = os.path.join(d, f'{repo.name}-walk.jsonl')
    done = {}
    if continuing:
        try:
            with open(progress, encoding='utf-8', errors='surrogateescape') as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue   # the last line of a stopped walk
                    done[r['top']] = r
        except FileNotFoundError:
            pass
        log(f'{repo.name}: continuing: {len(done):,} top-level entries were walked before')
    else:
        open(progress, 'w').close()
    w = Walk(repo, log)
    total = {'left_out': [], 'review': [], 'kept': [0, 0], 'errors': []}
    with open(progress, 'a', encoding='utf-8', errors='surrogateescape') as pf:
        for source, root in roots_of(repo, only):
            try:
                st = os.lstat(root)
            except OSError as e:
                w._error(root, e)
                continue
            dev = st.st_dev
            up = os.path.dirname(root)
            try:
                around = set(os.listdir(up))
            except OSError:
                around = set()
            if stat.S_ISDIR(st.st_mode) and not w.why(root, os.path.basename(root), up, around, True):
                tops = [(e.path, e.name) for e in w._entries(root)]
                parent, beside = root, {e for _, e in tops}
            else:
                tops = [(root, os.path.basename(root))]
                parent, beside = up, around
            for top, name in tops:
                if top in done:
                    r = done[top]
                else:
                    out = {'left_out': [], 'review': []}
                    errors_before = len(w.errors)
                    try:
                        tst = os.lstat(top)
                    except OSError as e:
                        w._error(top, e)
                        continue
                    is_dir = stat.S_ISDIR(tst.st_mode)
                    kept = [0, 0]
                    if is_dir and tst.st_dev != dev:
                        out['left_out'].append(['another file system', 0, 0, top])
                    else:
                        reason = w.why(top, name, parent, beside, is_dir)
                        if reason:
                            b, n = w.size(top, dev) if is_dir else (tst.st_size, 1)
                            out['left_out'].append([reason, b, n, top])
                        elif is_dir:
                            hit = bool(NAMES.match(name))
                            kept = list(w.visit(top, dev, out, hit))
                            if hit:
                                out['review'].append([kept[0], kept[1], top])
                        else:
                            kept = [tst.st_size, 1]
                            if tst.st_size >= MIN_FILE and EXTS.search(name) and stat.S_ISREG(tst.st_mode):
                                out['review'].append([kept[0], 1, top])
                    r = {'top': top, 'source': source, 'kept': kept, 'errors': w.errors[errors_before:], **out}
                    pf.write(json.dumps(r) + '\n')
                    pf.flush()
                total['left_out'] += r['left_out']
                total['review'] += r['review']
                total['errors'] += r['errors']
                total['kept'][0] += r['kept'][0]
                total['kept'][1] += r['kept'][1]
    return total


def _tsv(rows: list) -> str:
    return ''.join('\t'.join(str(c) for c in r) + '\n' for r in rows)


def _read_tsv(path: str) -> list:
    try:
        with open(path, encoding='utf-8', errors='surrogateescape') as f:
            return [line.rstrip('\n').split('\t') for line in f if line.strip()]
    except FileNotFoundError:
        return []


def report(cfg: Config, repo: Repository, only: list, continuing: bool, top: int, log: Log) -> int:
    d = folder_of(cfg)
    t = walk(cfg, repo, only, continuing, log)
    left_path = os.path.join(d, f'{repo.name}-left-out.tsv')
    before = {r[-1] for r in _read_tsv(left_path)}
    had_before = os.path.exists(left_path)
    if had_before:
        os.replace(left_path, os.path.join(d, f'{repo.name}-left-out.previous.tsv'))
    left = sorted(t['left_out'], key=lambda r: (-r[1], r[3]))
    review = sorted(t['review'], key=lambda r: (-r[0], r[2]))
    write_atomic(left_path, _tsv(left))
    write_atomic(os.path.join(d, f'{repo.name}-review.tsv'), _tsv(review))
    write_atomic(os.path.join(d, f'{repo.name}-errors.txt'), ''.join(e + '\n' for e in t['errors']))
    write_json(os.path.join(d, f'{repo.name}-report.json'), {'time': iso(), 'sources': only or 'all',
                                                             'kept': t['kept'], 'left_out': len(left),
                                                             'errors': len(t['errors'])})
    by = collections.defaultdict(lambda: [0, 0, 0])
    for reason, b, n, _ in left:
        by[reason][0] += 1
        by[reason][1] += b
        by[reason][2] += n
    lines = [f'{repo.name}: the backups keep {human_bytes(t["kept"][0])} in {t["kept"][1]:,} files, and leave out '
             f'{human_bytes(sum(r[1] for r in left))} in {sum(r[2] for r in left):,} files, in {len(left):,} places',
             '', 'Left out, by reason (places, size, files):']
    for reason, (c, b, n) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        lines.append(f'  {c:>7,}  {human_bytes(b):>10}  {n:>11,}  {reason}')
    lines += ['', f'The {min(top, len(left))} largest places left out:']
    lines += [f'  {human_bytes(b):>10}  {n:>11,}  {show(p)}  ({reason})' for reason, b, n, p in left[:top]]
    if had_before:
        new = [r for r in left if r[3] not in before]
        gone = sorted(before - {r[3] for r in left})
        lines += ['', f'New since the last report: {len(new):,}' + (f' (the {top} largest)' if len(new) > top else '')]
        lines += [f'  {human_bytes(b):>10}  {n:>11,}  {show(p)}  ({reason})' for reason, b, n, p in new[:top]]
        if gone:
            lines += [f'No longer left out (or gone): {len(gone):,}']
            lines += [f'  {show(p)}' for p in gone[:top]]
    lines += ['', f'Kept, and named like something rebuildable: {len(review):,}; "mpereira-backup excludes review '
                  f'{repo.name}" shows the ones not shown before.']
    if t['errors']:
        lines += [f'Could not read {len(t["errors"]):,} paths (the backups cannot read them either):']
        lines += [f'  {e}' for e in t['errors'][:ERRORS_SHOWN]]
    lines += ['', f'All of it: {show(d)}/{repo.name}-left-out.tsv, -review.tsv, -errors.txt']
    print('\n'.join(lines))
    log(f'{repo.name}: report written: {len(left):,} places left out, {len(review):,} to review, '
        f'{len(t["errors"]):,} paths not readable')
    return 0


def review(cfg: Config, repo: Repository, show_all: bool, top: int, log: Log) -> int:
    d = folder_of(cfg)
    path = os.path.join(d, f'{repo.name}-review.tsv')
    meta = read_json(os.path.join(d, f'{repo.name}-report.json'), None)
    if meta is None:
        raise Fail(f'no report of {repo.name} yet: run "mpereira-backup excludes report {repo.name}" first', 69)
    rows = [(int(b), int(n), p) for b, n, p in _read_tsv(path)]
    seen_path = os.path.join(d, f'{repo.name}-reviewed.json')
    seen = set(read_json(seen_path, []))
    shown = rows if show_all else [r for r in rows if r[2] not in seen]
    print(f'{repo.name}: from the report of {meta["time"]}: {len(rows):,} kept places are named like something '
          f'rebuildable; {len(shown):,} ' + ('in all' if show_all else 'not shown before') + ':')
    for b, n, p in shown[:top]:
        print(f'  {human_bytes(b):>10}  {n:>11,}  {show(p)}')
    if len(shown) > top:
        print(f'  … and {len(shown) - top:,} more, smaller (--top N shows more)')
    print('To leave one out: add a pattern to the repository\'s excludes in the settings, or a CACHEDIR.TAG in the '
          'folder if a program rebuilds it.')
    write_json(seen_path, sorted(seen | {p for _, _, p in shown[:top]}))
    log(f'{repo.name}: review: showed {min(top, len(shown)):,} of {len(rows):,}')
    return 0
