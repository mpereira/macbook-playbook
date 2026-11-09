"""Moves duplicate files to the quarantine, keeping one copy of each, with a journal that "quarantine check" and
"quarantine purge" read.

"dedupe plan BATCH" finds the groups of byte-identical files under "dedupe.root" with fclones (or reads an fclones JSON
that you give it), picks the copy that stays in each group, and writes STATE_DIR/dedupe/BATCH/plan.tsv: one line for
each other copy. It moves nothing and reads no file itself.

  Which copy stays: the first folder of "dedupe.prefer" that holds it, then the least nested path, then the oldest
  mtime, then the first path in byte order.
  Never a keeper and never moved: a file smaller than "min_size", a name with a tab or a newline, a file in a
  repository tree (a .git above it) or in a package, anything in "skip", the quarantine, or the journals.
  Never moved, but can be the keeper: a file whose path under the root matches a pattern of "dedupe.stay".
  Not moved unless "within_tier" is true: a copy in the same folder of "dedupe.prefer" as its keeper (or, like its
  keeper, in none of them). By default only a copy whose keeper is in a better folder moves, so the copies inside
  one folder, such as the same file in two machine backups or two albums, stay where they are.
  Listed in review.tsv and not planned, because the keeper decides whether the content is backed up: a group that
  mixes "replaceable" folders with others, a group whose keeper a repository of the location leaves out while it keeps
  a copy that would move, and a file that changed since fclones read it.

"dedupe apply BATCH" moves each planned file to QUARANTINE_ROOT/BATCH/<its path under the root>. Right before each
move it reads the file and its keeper with BLAKE3 and moves the file only if the two are equal (and equal to the
plan's hash, when it has one). A size check is not enough: a file can change and keep its size. A file whose ctime and
mtime are both before the moment fclones started takes its hash from fclones instead: a write sets the ctime to the
time of the write, and no process can set it back. A keeper with many copies is read once, and again only if it
changed. It never overwrites, it moves with rename (never a copy), and it refuses what is not a regular file.

The journal is QUARANTINE_JOURNALS/BATCH.tsv. A "planned" line reaches the disk before its move, and a "moved" line
follows it. Run again after a stop, it goes on: the journal and the disk together say what is done. A refused line
gets a "skipped: REASON" line, the others go on, and the exit is 1. A failed read or move of the disk stops the run.
"""
from __future__ import annotations

import collections
import concurrent.futures
import datetime
import json
import os
import re
import stat
import subprocess
import threading
import time

from .config import Config
from .quarantine import COLUMNS, Coverage, under
from .transfer import LOCKED_WHY, Hasher
from .util import Every, Fail, Log, human_bytes, iso, locked, read_json, show, write_atomic, write_json

PACKAGES = ('.app', '.photoslibrary', '.imovielibrary', '.fcpbundle', '.lrdata', '.sparsebundle', '.bundle',
            '.framework', '.pkg', '.xcodeproj', '.xcworkspace')
SHOWN = 50


class Refused(Exception):
    pass


def settings(cfg: Config):
    if not cfg.dedupe or not cfg.quarantine:
        raise Fail('no "dedupe" settings (they need the "quarantine" settings too)', 78)
    return cfg.dedupe, cfg.quarantine


def batch_name(batch: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9._-]+', batch) or batch.startswith('.'):
        raise Fail(f'not a batch name: {batch!r} (letters, digits, ".", "_", "-")', 64)
    return batch


def folder_of(cfg: Config, batch: str) -> str:
    return os.path.join(cfg.state_dir, 'dedupe', batch_name(batch))


def in_package(path: str) -> bool:
    return any(part.endswith(PACKAGES) for part in path.split('/')[:-1])


class Repos:
    """Whether a folder is in a repository tree: a .git (folder or file) in it or a folder above it."""

    def __init__(self):
        self.seen: dict = {}

    def holds(self, path: str) -> bool:
        folder, walked = os.path.dirname(path), []
        while folder not in self.seen:
            walked.append(folder)
            parent = os.path.dirname(folder)
            if os.path.lexists(os.path.join(folder, '.git')):
                self.seen[folder] = True
                break
            if parent == folder:
                self.seen[folder] = False
                break
            folder = parent
        answer = self.seen[folder]
        for f in walked:
            self.seen[f] = answer
        return answer


def lstat_or_none(path: str):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def never_searched(cfg: Config) -> list:
    """The folders under the root that dedupe never reads or moves: "skip", the quarantine, the journals, and each
    local repository."""
    d, q = settings(cfg)
    out = [os.path.join(d.root, s) for s in d.skip] + [q.root, q.journals]
    for repo in cfg.repositories.values():
        if repo.repository.startswith('/'):
            out.append(repo.repository.rstrip('/'))
    return [p for p in out if under(p, d.root)]


# --- plan ---

def _rel(d, path: str) -> str:
    return path[len(d.root) + 1:]


def _tier(d, path: str) -> int:
    rel = _rel(d, path)
    return next((i for i, f in enumerate(d.prefer) if under(rel, f)), len(d.prefer))


def _rank(d, path: str, st) -> tuple:
    return (_tier(d, path), path.count('/'), st.st_mtime_ns, path.encode('utf-8', 'surrogateescape'))


def _replaceable(d, path: str) -> bool:
    rel = _rel(d, path)
    return any(under(rel, f) for f in d.replaceable)


def _stays(d, path: str) -> bool:
    rel = _rel(d, path)
    return any(p.search(rel) for p in d.stay)


def run_fclones(cfg: Config, folders: list, out: str, log: Log, cache: bool) -> float:
    """Runs fclones on FOLDERS into OUT (renamed into place only when fclones finished). Returns the time it started,
    or 0 with --cache: hashes from its cache can be older than the run."""
    d, _ = settings(cfg)
    args = [d.fclones, 'group', '--format', 'json', '--hash-fn', 'blake3', '--min', str(d.min_size), '--hidden',
            '--no-ignore', '--one-fs']
    for p in never_searched(cfg):
        args += ['--exclude', p, '--exclude', p + '/**']
    if cache:
        args.append('--cache')
    args += folders
    started = time.time()
    log(f'dedupe: running {" ".join(show(a) for a in args)}')
    part = out + '.part'
    with open(part, 'wb') as f, open(out + '.log', 'ab') as err:
        try:
            rc = subprocess.run(args, stdout=f, stderr=err).returncode
        except OSError as e:
            raise Fail(f'cannot run {d.fclones}: {e}', 69)
    if rc != 0:
        raise Fail(f'fclones exited with {rc}; its messages are in {show(out)}.log', 1)
    os.rename(part, out)
    return 0.0 if cache else started


ESCAPE = re.compile(r'\\(\\|t|n|r|x[0-9A-Fa-f]{2})')
ESCAPED = {'\\': b'\\', 't': b'\t', 'n': b'\n', 'r': b'\r'}


def unescape(path: str) -> str:
    """A path as fclones writes it in its JSON, back to the name on disk: it writes a backslash as two, tab, newline,
    and carriage return as \\t, \\n, \\r, and each other control character as \\xHH. A name written another way
    is not found on disk, and goes to the review."""
    if '\\' not in path:
        return path
    out, at = b'', 0
    for m in ESCAPE.finditer(path):
        out += path[at:m.start()].encode('utf-8', 'surrogateescape')
        e = m.group(1)
        out += bytes([int(e[1:], 16)]) if e[0] == 'x' else ESCAPED[e]
        at = m.end()
    out += path[at:].encode('utf-8', 'surrogateescape')
    return out.decode('utf-8', 'surrogateescape')


def read_groups(path: str) -> tuple:
    """(groups as (size, hash, files), whether fclones hashed with BLAKE3) from the JSON of fclones group."""
    try:
        with open(path, encoding='utf-8', errors='surrogateescape') as f:
            data = json.load(f)
        cmd = [str(x) for x in data.get('header', {}).get('command', [])]
        groups = [(int(g['file_len']), str(g['file_hash']), [unescape(p) for p in g['files']])
                  for g in data['groups']]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise Fail(f'{show(path)}: not the JSON of fclones group: {e}', 65)
    blake3 = any(a == '--hash-fn' and b == 'blake3' for a, b in zip(cmd, cmd[1:])) or '--hash-fn=blake3' in cmd
    return groups, blake3


def plan(cfg: Config, batch: str, folders: list, fclones_json: str, hashed_after: float | None, cache: bool,
         log: Log) -> int:
    d, q = settings(cfg)
    batch_name(batch)
    if os.path.exists(os.path.join(q.journals, batch + '.tsv')):
        raise Fail(f'batch {batch} has a journal already: plan a new batch, with another name', 64)
    if not os.path.isdir(d.root):
        raise Fail(f'{show(d.root)} is not there (is its drive mounted?)', 69)
    folders = [os.path.abspath(f).rstrip('/') for f in folders] or [d.root]
    for f in folders:
        if not under(f, d.root) or not os.path.isdir(f):
            raise Fail(f'{show(f)} is not a folder under {show(d.root)}', 64)
    out = folder_of(cfg, batch)
    os.makedirs(out, exist_ok=True)
    meta_path = os.path.join(out, 'plan.json')
    if fclones_json:
        groups_path = os.path.abspath(fclones_json)
        after = hashed_after or 0.0
    else:
        groups_path = os.path.join(out, 'fclones.json')
        old = read_json(meta_path, {})
        if os.path.isfile(groups_path) and old.get('folders') == folders and 'hashed_after' in old:
            after = float(old['hashed_after'])
            started = datetime.datetime.fromtimestamp(float(old.get('fclones_started') or 0)).astimezone()
            log(f'dedupe: using the finished fclones run of {iso(started)} in {show(groups_path)}')
        else:
            after = run_fclones(cfg, folders, groups_path, log, cache)
            write_json(meta_path, {'batch': batch, 'folders': folders, 'hashed_after': after,
                                   'fclones_started': after or time.time()})
    groups, blake3 = read_groups(groups_path)
    use_hashes = blake3 and after > 0
    if fclones_json and hashed_after and not blake3:
        log('dedupe: that fclones run did not use --hash-fn blake3, so apply reads every file')

    skip = never_searched(cfg)
    repos = Repos()
    coverages = [Coverage(cfg.repositories[rn]) for rn in q.locations[d.location]]
    rows, review, left = [], [], collections.Counter()
    seen = set()
    every = Every(60)
    for number, (size, digest, files) in enumerate(groups, 1):
        if every():
            log(f'dedupe {batch}: looked at {number - 1:,} of {len(groups):,} groups; {len(rows):,} files planned')
        kept = []
        for path in files:
            if path in seen:
                raise Fail(f'{show(groups_path)}: a file in two groups: {show(path)}', 65)
            seen.add(path)
            st = lstat_or_none(path)
            if not under(path, d.root) or path == d.root:
                left['not under the root'] += 1
            elif any(under(path, s) for s in skip):
                left['in a folder that dedupe skips'] += 1
            elif size < d.min_size:
                left[f'under {human_bytes(d.min_size)}'] += 1
            elif '\t' in path or '\n' in path:
                left['a tab or a newline in the name'] += 1
            elif in_package(path):
                left['in a package'] += 1
            elif repos.holds(path):
                left['in a repository tree'] += 1
            elif st is None or not stat.S_ISREG(st.st_mode) or st.st_size != size:
                left['gone or changed since fclones read it'] += 1
                review.append(('gone or changed since fclones read it', str(number), str(size), path))
            else:
                kept.append((path, st))
        if len(kept) < 2:
            continue
        ranked = sorted(kept, key=lambda item: _rank(d, *item))
        keeper, others = ranked[0][0], []
        keeper_tier = _tier(d, keeper)
        for path, _ in ranked[1:]:
            if _stays(d, path):
                left['matches "stay"'] += 1
            elif not d.within_tier and _tier(d, path) == keeper_tier:
                left['the keeper is in the same "prefer" folder ("within_tier" is off)'] += 1
            else:
                others.append(path)
        if not others:
            continue
        kinds = {_replaceable(d, p) for p, _ in kept}
        reason = ''
        if len(kinds) > 1:
            reason = 'mixes replaceable folders with others'
        else:
            for cov in coverages:
                if cov.left_out(keeper) and any(not cov.left_out(p) for p in others):
                    reason = f'{cov.repo.name} leaves out the keeper and keeps a copy'
                    break
        if reason:
            for path in [keeper] + others:
                review.append((reason, str(number), str(size), path))
            continue
        cls = 'C' if kinds == {True} else 'A'
        for path in others:
            rows.append((path, keeper, d.location, cls, str(size), digest if use_hashes else '-'))

    write_atomic(os.path.join(out, 'review.tsv'), ''.join('\t'.join(r) + '\n' for r in review))
    write_atomic(os.path.join(out, 'plan.tsv'), ''.join('\t'.join(r) + '\n' for r in rows))
    meta = read_json(meta_path, {})
    meta.update(batch=batch, folders=folders, fclones=groups_path, hashed_after=after if use_hashes else 0,
                planned=iso(), root=d.root, location=d.location, lines=len(rows),
                bytes=sum(int(r[4]) for r in rows))
    write_json(meta_path, meta)

    totals = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        t = totals['/'.join(_rel(d, os.path.dirname(r[0])).split('/')[:2])]
        t[0] += 1
        t[1] += int(r[4])
    for f in sorted(totals, key=lambda f: -totals[f][1])[:30]:
        log(f'dedupe {batch}: {show(f)}: {totals[f][0]:,} files, {human_bytes(totals[f][1])} to move')
    for reason in sorted(left):
        log(f'dedupe {batch}: {left[reason]:,} files left out: {reason}')
    log(f'dedupe {batch}: {len(rows):,} files, {human_bytes(meta["bytes"])} to move, of {len(groups):,} groups; '
        f'{len(review):,} lines to review in {show(os.path.join(out, "review.tsv"))}')
    log(f'dedupe {batch}: the plan is {show(os.path.join(out, "plan.tsv"))}; nothing moved. Next: '
        f'"mpereira-backup dedupe apply {batch} --dry-run"')
    return 0


# --- apply ---

Line = collections.namedtuple('Line', 'source keeper location cls size digest destination')
Row = collections.namedtuple('Row', 'keeper blake3 size mtime quarantine state')


def read_plan(cfg: Config, batch: str) -> tuple:
    d, q = settings(cfg)
    folder = folder_of(cfg, batch)
    path = os.path.join(folder, 'plan.tsv')
    meta = read_json(os.path.join(folder, 'plan.json'), None)
    if meta is None or not os.path.isfile(path) or 'planned' not in meta:
        raise Fail(f'no finished plan for batch {batch}: run "mpereira-backup dedupe plan {batch}" first', 66)
    if meta.get('root') != d.root or meta.get('location') != d.location:
        raise Fail(f'the plan of {batch} was made with another "dedupe.root" or "location": plan it again', 78)
    to = os.path.join(q.root, batch)
    skip = never_searched(cfg)
    lines, sources, keepers = [], set(), set()
    with open(path, encoding='utf-8', errors='surrogateescape', newline='\n') as f:
        for n, text in enumerate(f, 1):
            c = text.rstrip('\n').split('\t')
            where = f'{show(path)}:{n}'
            if len(c) != 6 or c[2] != d.location or c[3] not in ('A', 'C') or not c[4].isdigit():
                raise Fail(f'{where}: need file, keeper, {d.location}, class A or C, size, and hash', 65)
            source, keeper, _, cls, size, digest = c
            if digest != '-' and (len(digest) != 64 or set(digest) - set('0123456789abcdef')):
                raise Fail(f'{where}: not a BLAKE3 hash: {digest}', 65)
            for p in (source, keeper):
                if not under(p, d.root) or p == d.root or any(under(p, s) for s in skip):
                    raise Fail(f'{where}: not a file dedupe may touch: {show(p)}', 65)
            if source == keeper or source in sources:
                raise Fail(f'{where}: a second line for {show(source)}, or a file that is its own keeper', 65)
            if _replaceable(d, keeper) != (cls == 'C'):
                raise Fail(f'{where}: class {cls} does not match the keeper {show(keeper)}', 65)
            sources.add(source)
            keepers.add(keeper)
            lines.append(Line(source, keeper, c[2], cls, int(size), digest, to + source[len(d.root):]))
    both = sources & keepers
    if both:   # two lines could each move the other's keeper
        raise Fail(f'{show(path)}: a keeper that the plan also moves: {show(sorted(both)[0])}', 65)
    if not under(to, q.root) or os.stat(q.root if os.path.isdir(q.root) else os.path.dirname(q.root)).st_dev != \
            os.stat(d.root).st_dev:
        raise Fail(f'{show(q.root)} must be on the same file system as {show(d.root)}: a move must be a rename', 78)
    return lines, float(meta.get('hashed_after') or 0)


class Journal:
    def __init__(self, path: str, batch: str):
        self.path, self.batch = path, batch
        self.last: dict = {}
        self.lock = threading.Lock()
        if os.path.exists(path):
            with open(path, 'rb+') as f:   # a stop in a write leaves part of a line, whose move did not follow
                data = f.read()
                if data and not data.endswith(b'\n'):
                    f.truncate(data.rfind(b'\n') + 1)
            with open(path, encoding='utf-8', errors='surrogateescape', newline='\n') as f:
                for n, text in enumerate(f, 1):
                    c = text.rstrip('\n').split('\t')
                    if c[0] == 'batch':
                        continue
                    if len(c) != len(COLUMNS):
                        raise Fail(f'{show(path)}:{n}: not {len(COLUMNS)} columns', 65)
                    self.last[c[1]] = Row(c[2], c[5], c[6], c[7], c[8], c[9])
        self.file = None   # type: ignore[assignment]

    def open(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.file = open(self.path, 'a', encoding='utf-8', errors='surrogateescape', newline='\n')
        if self.file.tell() == 0:
            self.file.write('\t'.join(COLUMNS) + '\n')
            self.file.flush()

    def row(self, line: Line, state: str, size: object = '-', mtime: object = '-', digest: str = '-',
            sync: bool = False) -> None:
        with self.lock:
            self.file.write('\t'.join((self.batch, line.source, line.keeper, line.location, line.cls, digest,
                                       str(size), str(mtime), line.destination, state)) + '\n')
            self.file.flush()
            if sync:
                os.fsync(self.file.fileno())
            self.last[line.source] = Row(line.keeper, digest, str(size), str(mtime), line.destination, state)


def _stamp(st) -> tuple:
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


class Reader:
    """BLAKE3 of files that do not change while they are read, from the plan when it is still true."""

    def __init__(self, hasher: Hasher, hashed_after: float):
        self.hasher, self.after = hasher, hashed_after
        self.lock = threading.Lock()
        self.keepers: dict = {}   # path -> (stamp, hash)
        self.taken = self.read = self.reread = 0

    def digest(self, path: str, st, planned: str) -> tuple:
        """(hash, the stat that the hash holds for). Refused if the file changed while it was read."""
        if planned != '-' and self.after and st.st_ctime < self.after and st.st_mtime < self.after:
            with self.lock:
                self.taken += 1
            return planned, st
        h = self.hasher.one(path)
        now = os.lstat(path)
        if _stamp(now) != _stamp(st):
            # Only the ctime changed: OpenZFS on macOS sets it on the first read of a file, and an attribute change
            # sets it too. A second read that gives the same hash, with no further change, shows the content held.
            if _stamp(now)[:3] != _stamp(st)[:3] or self.hasher.one(path) != h or _stamp(os.lstat(path)) != _stamp(now):
                raise Refused(f'it changed while it was read: {show(path)}')
            with self.lock:
                self.reread += 1
        with self.lock:
            self.read += 1
        return h, now

    def keeper(self, path: str, st, planned: str) -> str:
        with self.lock:
            seen = self.keepers.get(path)
        if seen and _stamp(st) in seen[0]:
            return seen[1]
        h, now = self.digest(path, st, planned)
        with self.lock:
            self.keepers[path] = ({_stamp(st), _stamp(now)}, h)
        return h


class Tally:
    def __init__(self, lines: list):
        self.lock = threading.Lock()
        self.total, self.bytes_total = len(lines), sum(x.size for x in lines)
        self.moved = self.already = self.bytes_done = self.bytes_moved = 0
        self.skipped: list = []

    def line(self, verb: str = 'moved') -> str:
        return (f'{self.moved:,} {verb} ({human_bytes(self.bytes_moved)}), {self.already:,} done by an earlier run, '
                f'{len(self.skipped):,} skipped, of {self.total:,} lines')


def _apply_line(line: Line, journal: Journal, tally: Tally, reader: Reader, repos: Repos, dry_run: bool) -> None:
    def skip(reason: str) -> None:
        if not dry_run:
            journal.row(line, 'skipped: ' + reason)
        with tally.lock:
            tally.skipped.append(f'{reason}: {show(line.source)}')

    s, dst = lstat_or_none(line.source), lstat_or_none(line.destination)
    last = journal.last.get(line.source)
    if s is None:
        if dst is not None and last and last.quarantine == line.destination and last.state == 'moved':
            with tally.lock:
                tally.already += 1
        elif dst is not None and last and last.quarantine == line.destination and last.state == 'planned' and \
                (str(dst.st_size), str(dst.st_mtime_ns)) == (last.size, last.mtime):
            if not dry_run:   # the stop came between the move and its line
                journal.row(line, 'moved', last.size, last.mtime, last.blake3)
            with tally.lock:
                tally.moved += 1
                tally.bytes_moved += dst.st_size
        elif dst is not None:
            skip('the file is gone, and the journal does not say that it moved to the quarantine')
        else:
            skip('the file is gone')
        return
    if dst is not None:
        return skip('the quarantine path exists')
    if not stat.S_ISREG(s.st_mode):
        return skip('not a regular file')
    if locked(line.source):
        return skip(LOCKED_WHY)
    if s.st_size != line.size:
        return skip(f'the plan says {line.size} bytes, and it has {s.st_size}')
    k = lstat_or_none(line.keeper)
    if k is None:
        return skip('the keeper is gone')
    if not stat.S_ISREG(k.st_mode):   # a link to the file would hash the same, and the move would break it
        return skip('the keeper is not a regular file')
    if (k.st_dev, k.st_ino) == (s.st_dev, s.st_ino):   # another spelling of one file, or a hard link
        return skip('the keeper and the file are one file')
    if repos.holds(line.source) or repos.holds(line.keeper):
        return skip('in a repository tree')
    if in_package(line.source) or in_package(line.keeper):
        return skip('in a package')
    if dry_run:
        with tally.lock:
            tally.moved += 1
            tally.bytes_moved += s.st_size
        return
    try:
        file_hash, settled = reader.digest(line.source, s, line.digest)
        keeper_hash = reader.keeper(line.keeper, k, line.digest)
    except Refused as e:
        return skip(str(e))
    if file_hash != keeper_hash:
        return skip('the file and the keeper differ')
    if line.digest != '-' and file_hash != line.digest:
        return skip('the file and the keeper are equal, and differ from the hash in the plan')
    if _stamp(os.lstat(line.source)) != _stamp(settled) or lstat_or_none(line.destination) is not None:
        return skip('it changed while it was read')
    os.makedirs(os.path.dirname(line.destination), exist_ok=True)
    journal.row(line, 'planned', s.st_size, s.st_mtime_ns, file_hash, sync=True)
    try:
        os.rename(line.source, line.destination)   # never a copy: a rename fails across file systems
    except PermissionError as e:
        return skip(f'cannot move it: {e.strerror}')
    a = lstat_or_none(line.destination)
    if a is None or a.st_ino != s.st_ino or a.st_size != s.st_size or os.path.lexists(line.source):
        raise OSError(f'the move did not keep the inode and the size, or left the file: {show(line.source)}')
    journal.row(line, 'moved', s.st_size, s.st_mtime_ns, file_hash)
    with tally.lock:
        tally.moved += 1
        tally.bytes_moved += s.st_size


def apply(cfg: Config, batch: str, jobs: int, dry_run: bool, log: Log) -> int:
    d, q = settings(cfg)
    if jobs < 1:
        raise Fail('--jobs must be 1 or more', 64)
    if not os.path.isdir(d.root):
        raise Fail(f'{show(d.root)} is not there (is its drive mounted?)', 69)
    lines, after = read_plan(cfg, batch)
    label = f'dedupe {batch}' + (' (dry run)' if dry_run else '')
    journal = Journal(os.path.join(q.journals, batch + '.tsv'), batch)
    if not dry_run:
        journal.open()
    tally, reader, repos = Tally(lines), Reader(Hasher(cfg.b3sum), after), Repos()
    log(f'{label}: {len(lines):,} lines, {human_bytes(tally.bytes_total)}, into {show(os.path.join(q.root, batch))}; '
        f'the journal is {show(journal.path)}')
    stop = threading.Event()
    failures: list = []
    every = Every(60)

    def work(line: Line) -> None:
        if stop.is_set():
            return
        try:
            _apply_line(line, journal, tally, reader, repos, dry_run)
        except OSError as e:   # a failing disk: more moves would not help, and a run after the fix goes on
            failures.append(show(str(e)))
            stop.set()
        with tally.lock:
            tally.bytes_done += line.size
            due = every()
        if due:
            log(f'{label}: {human_bytes(tally.bytes_done)} of {human_bytes(tally.bytes_total)}: {tally.line()}')

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for future in [pool.submit(work, x) for x in lines]:
            future.result()
    for text in tally.skipped[:SHOWN]:
        log(f'{label}: skipped: {text}')
    log(f'{label}: {tally.line("would move" if dry_run else "moved")}')
    if not dry_run:
        log(f'{label}: {reader.taken:,} hashes taken from fclones, {reader.read:,} files read now'
            + (f' ({reader.reread:,} read twice: only their ctime changed during the first read)' if reader.reread else ''))
    if failures:
        log(f'{label}: STOPPED: {failures[0]}; fix it, then run the same command again to go on')
        return 1
    if not dry_run and tally.moved + tally.already:
        log(f'{label}: next: "mpereira-backup quarantine check {batch}" once the backups have run')
    return 1 if tally.skipped else 0
