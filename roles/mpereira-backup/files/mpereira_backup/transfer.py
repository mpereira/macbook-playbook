"""Copying files to a drive so that none is lost: `ingest` and `copy-media`.

Both work in the same four phases, each recorded as it goes, so a run that stops continues where it stopped:

  list   Walk the source, and hash every file with BLAKE3 (b3sum, in batches, four at a time).
  copy   Copy each file beside its destination under a temporary name, read the copy back with BLAKE3, and only when the
         hash is the listed one, give it its name. A destination that is already there counts when it has the listed
         bytes, and is otherwise a conflict: nothing is overwritten. A file that changed after the list is not copied.
  trash  (ingest --then-trash only) Each original whose copy passed moves to the Trash, after it is hashed again and
         found unchanged, and only when each repository in "trash_requires" has a snapshot, taken after the copy, that
         holds every copy with its size. What does not pass stays where it is, and is listed.
  done   The counts, and up to 50 of the files that stay.

The state of a run is in STATE_DIR/transfers/NAME/: meta.json (the source, the destination, and when each phase ended),
list.jsonl (one line per file or link), and journal.jsonl (one line per change, appended and synced before the next).
Nothing is deleted: originals go to the Trash, which you empty.
"""
from __future__ import annotations

import concurrent.futures
import datetime
import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import unicodedata
from dataclasses import asdict, dataclass

from . import restic
from .config import Config, Media, drive_of_path
from .util import Every, Fail, Log, human_bytes, iso, locked, mounted, need_space, now, parse_iso, read_json, show, \
    write_json

LOCKED_WHY = 'locked: the uchg or schg flag is on it or its folder; clear it (chflags nouchg PATH) to move it'

BATCH_FILES = 200
BATCH_BYTES = 256 << 20
SF_DATALESS = 0x40000000          # an iCloud or File Provider file whose bytes are not on this Mac
SHOWN = 50
FINAL = ('copied', 'there', 'conflict', 'changed', 'gone', 'dataless', 'unreadable')
KEPT = ('copied', 'there')        # the states whose original has a copy at its destination


@dataclass
class Item:
    kind: str          # 'f' a file, 'l' a link
    rel: str           # the path under the source, as its bytes (surrogate escapes)
    size: int = 0
    mtime_ns: int = 0
    ctime_ns: int = 0
    b3: str = ''       # files: the BLAKE3 of the bytes, '' if not hashed (dataless, unreadable)
    target: str = ''   # links: where it points
    note: str = ''     # why it was not hashed


def nfc(p: str) -> str:
    return unicodedata.normalize('NFC', p)


# --- hashing ---

class Hasher:
    def __init__(self, b3sum: str):
        self.b3sum = b3sum

    def one(self, path: str) -> str:
        r = subprocess.run([self.b3sum, '--no-mmap', '--no-names', '--', path], capture_output=True)
        if r.returncode:
            raise OSError(r.stderr.decode('utf-8', 'replace').strip() or f'b3sum exited with {r.returncode}')
        return r.stdout.decode().strip()

    def batch(self, paths: list) -> list:
        """The hashes of PATHS, '' for each one that cannot be read."""
        r = subprocess.run([self.b3sum, '--no-mmap', '--no-names', '--'] + paths, capture_output=True)
        out = r.stdout.decode().split()
        if r.returncode == 0 and len(out) == len(paths):
            return out
        hashes = []
        for p in paths:   # one at a time, to know which file fails
            try:
                hashes.append(self.one(p))
            except OSError:
                hashes.append('')
        return hashes

    def many(self, paths: list, sizes: list, log: Log, label: str) -> list:
        chunks, chunk, size = [], [], 0
        for p, s in zip(paths, sizes):
            chunk.append(p)
            size += s
            if len(chunk) >= BATCH_FILES or size >= BATCH_BYTES:
                chunks.append(chunk)
                chunk, size = [], 0
        if chunk:
            chunks.append(chunk)
        out = []
        every = Every(60)
        total = sum(sizes)
        done = 0
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            for c, hashes in zip(chunks, pool.map(self.batch, chunks)):
                out += hashes
                done += sum(sizes[len(out) - len(c):len(out)])
                if every():
                    log(f'{label}: hashed {len(out):,} of {len(paths):,} files, {human_bytes(done)} of {human_bytes(total)}')
        return out


# --- the walk ---

def walk(root: str, keep_dir, keep_file, log: Log) -> tuple:
    """(items, other) under ROOT: each file and link that KEEP_FILE(rel) accepts, in folders that KEEP_DIR(rel) accepts.
    A link to a folder is a link. OTHER counts what is neither (sockets, pipes), which stay. A folder that cannot be read
    stops the walk: a copy that silently missed a folder would let its originals look done."""
    items, other = [], 0
    stack = ['']
    while stack:
        rel_dir = stack.pop()
        folder = os.path.join(root, rel_dir) if rel_dir else root
        try:
            with os.scandir(folder) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as e:
            raise Fail(f'cannot read {show(folder)}: {e.strerror} (a launchd job needs Full Disk Access for some folders)')
        for e in entries:
            rel = os.path.join(rel_dir, e.name) if rel_dir else e.name
            st = e.stat(follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                if keep_dir(rel):
                    stack.append(rel)
                continue
            if not keep_file(rel):
                continue
            if stat.S_ISLNK(st.st_mode):
                items.append(Item('l', rel, 0, st.st_mtime_ns, st.st_ctime_ns, target=os.readlink(e.path)))
            elif stat.S_ISREG(st.st_mode):
                it = Item('f', rel, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                if getattr(st, 'st_flags', 0) & SF_DATALESS:
                    it.note = 'dataless'
                items.append(it)
            else:
                other += 1
    items.sort(key=lambda i: i.rel)
    return items, other


# --- the state of a run ---

class Run:
    def __init__(self, cfg: Config, name: str, log: Log):
        self.cfg = cfg
        self.log = log
        self.work = os.path.join(cfg.state_dir, 'transfers', name)
        self.meta_path = os.path.join(self.work, 'meta.json')
        self.list_path = os.path.join(self.work, 'list.jsonl')
        self.journal_path = os.path.join(self.work, 'journal.jsonl')
        self.meta = read_json(self.meta_path, {})
        self.items: list = []
        self.state: dict = {}     # rel -> the last journal row
        self.journal = None

    def set(self, **kw) -> None:
        self.meta.update(kw)
        write_json(self.meta_path, self.meta)

    def put_aside(self) -> None:
        """Keeps a finished run's files under its start time, so a new run starts empty."""
        if os.path.isdir(self.work) and self.meta:
            stamp = re.sub(r'[^0-9T]', '', self.meta.get('started', iso()))[:15]
            aside, n = f'{self.work}.{stamp}', 1
            while os.path.lexists(aside):
                n += 1
                aside = f'{self.work}.{stamp}-{n}'
            os.rename(self.work, aside)
        self.meta = {}

    def save_list(self, items: list) -> None:
        os.makedirs(self.work, exist_ok=True)
        tmp = self.list_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            for it in items:
                f.write(json.dumps(asdict(it)) + '\n')
        os.replace(tmp, self.list_path)
        self.items = items

    def load(self) -> None:
        with open(self.list_path, encoding='utf-8') as f:
            self.items = [Item(**json.loads(line)) for line in f if line.strip()]
        self.state = {}
        if os.path.exists(self.journal_path):
            with open(self.journal_path, encoding='utf-8') as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue   # a line cut off by a stop: the change it would record is checked again
                    self.state[row['rel']] = row

    def write(self, rel: str, state: str, **kw) -> None:
        if self.journal is None:
            os.makedirs(self.work, exist_ok=True)
            self.journal = open(self.journal_path, 'a', encoding='utf-8')
        row = dict(rel=rel, state=state, time=iso(), **kw)
        self.journal.write(json.dumps(row) + '\n')
        self.journal.flush()
        os.fsync(self.journal.fileno())
        self.state[rel] = row

    def b3(self, it: Item) -> str:
        """IT's hash: the copy phase's, when it found that the list's read was a bad one."""
        return (self.state.get(it.rel) or {}).get('b3') or it.b3

    def st(self, rel: str) -> str:
        r = self.state.get(rel)
        return r['state'] if r else ''


def make_list(run: Run, source: str, keep_dir, keep_file, label: str, since_ns: int = 0) -> None:
    run.log(f'{label}: listing {show(source)}' + (f', what changed after {iso(datetime.datetime.fromtimestamp(since_ns / 1e9).astimezone())}' if since_ns else ''))
    items, other = walk(source, keep_dir, keep_file, run.log)
    if since_ns:
        items = [i for i in items if i.ctime_ns > since_ns]
    if other:
        run.log(f'{label}: {other} items are neither files nor links; they are not copied')
    todo = [i for i in items if i.kind == 'f' and not i.note]
    run.log(f'{label}: {len(items):,} files and links, {human_bytes(sum(i.size for i in items))}; hashing {len(todo):,} files')
    hashes = Hasher(run.cfg.b3sum).many([os.path.join(source, i.rel) for i in todo], [i.size for i in todo], run.log, label)
    for i, h in zip(todo, hashes):
        i.b3 = h
        if not h:
            i.note = 'unreadable'
    run.save_list(items)


# --- copy ---

def same_file(path: str, it: Item, hasher: Hasher) -> bool:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if it.kind == 'l':
        return stat.S_ISLNK(st.st_mode) and os.readlink(path) == it.target
    if not stat.S_ISREG(st.st_mode) or st.st_size != it.size:
        return False
    try:
        return hasher.one(path) == it.b3
    except OSError:
        return False


def unchanged(path: str, it: Item) -> bool:
    """The original is still the size, time and kind that the list says (its bytes are compared later, by hash)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if it.kind == 'l':
        return stat.S_ISLNK(st.st_mode) and os.readlink(path) == it.target
    return stat.S_ISREG(st.st_mode) and (st.st_size, st.st_mtime_ns) == (it.size, it.mtime_ns)


def temp_name(dst: str, taken: frozenset = frozenset()) -> str:
    """A name beside DST that no file has and that is not in TAKEN (where the copied files go). A file with such a name
    is never removed: it can be a file of yours, or one copied from the source. A temporary copy that a run left when it was killed stays, and has "mpereira-backup-copy"
    in its name."""
    folder, name = os.path.split(dst)
    tmp = os.path.join(folder, f'.{name}.mpereira-backup-copy')
    n = 1
    while os.path.lexists(tmp) or tmp in taken:
        n += 1
        tmp = os.path.join(folder, f'.{name}.{n}.mpereira-backup-copy')
    return tmp


def place(tmp: str, dst: str) -> None:
    """Gives the checked copy its name, never over a file that is there."""
    try:
        os.link(tmp, dst)
        os.unlink(tmp)
    except OSError as e:
        if e.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV):
            raise
        if os.path.lexists(dst):   # a file system without hard links (exFAT): check, then rename
            raise FileExistsError(errno.EEXIST, 'already there', dst)
        os.rename(tmp, dst)


RESOURCE_FORK_WHY = 'it has a resource fork, which the copy does not hold'


def resource_fork_size(path: str) -> int:
    """The size of a file's macOS resource fork, 0 if it has none. Only the data fork is copied and hashed, so an
    original with a resource fork never goes to the Trash."""
    try:
        return os.stat(os.path.join(path, '..namedfork', 'rsrc')).st_size
    except OSError:
        return 0


def copy_bytes(src: str, tmp: str) -> None:
    with open(src, 'rb') as fi, open(tmp, 'xb') as fo:
        while True:
            b = fi.read(8 << 20)
            if not b:
                break
            fo.write(b)
        fo.flush()
        os.fsync(fo.fileno())
    s = os.stat(src)
    os.chmod(tmp, stat.S_IMODE(s.st_mode))
    os.utime(tmp, ns=(s.st_atime_ns, s.st_mtime_ns))


def copy_all(run: Run, source: str, dest: str, label: str) -> None:
    hasher = Hasher(run.cfg.b3sum)
    todo = [i for i in run.items if run.st(i.rel) not in FINAL]
    total = sum(i.size for i in todo)
    need_space(dest, total, label)
    run.log(f'{label}: copying {len(todo):,} files and links, {human_bytes(total)}, to {show(dest)}'
            + (f' ({len(run.items) - len(todo):,} done by an earlier run)' if len(todo) < len(run.items) else ''))
    every = Every(60)
    done_bytes = 0
    batch: list = []   # (item, destination, temporary copy) not yet read back
    size = 0

    def flush():
        nonlocal size, done_bytes
        if not batch:
            return
        hashes = hasher.batch([t for _, _, t in batch])
        for (it, dst, tmp), h in zip(batch, hashes):
            fixed = {}
            if h != it.b3:
                src = os.path.join(source, it.rel)
                if not unchanged(src, it):
                    os.unlink(tmp)
                    run.write(it.rel, 'changed', why='changed while it was copied')
                    continue
                # A card or an old disk can give one bad read. Two more reads of the original decide which read was bad:
                # if both give the copy's hash, the list's read was; otherwise the copy or the drive is bad, and the run
                # stops before it copies more.
                again = [hasher.batch([src])[0] for _ in range(2)]
                if not h or again != [h, h]:
                    os.unlink(tmp)
                    raise Fail(f'the copy of {show(it.rel)} reads back with other bytes than the original: stopped. '
                               f'Check both drives, then run again.')
                run.log(f'{label}: {show(it.rel)} read with other bytes once, when it was listed; three reads since agree')
                fixed = {'b3': h}
            try:
                place(tmp, dst)
            except FileExistsError:
                os.unlink(tmp)
                run.write(it.rel, 'there' if same_file(dst, it, hasher) else 'conflict', dst=dst)
                continue
            run.write(it.rel, 'copied', dst=dst, **fixed)
            done_bytes += it.size
        batch.clear()
        size = 0
        if every():
            run.log(f'{label}: copied {human_bytes(done_bytes)} of {human_bytes(total)}')

    names = frozenset(os.path.join(dest, it.rel) for it in todo)
    try:
        for it in todo:
            src, dst = os.path.join(source, it.rel), os.path.join(dest, it.rel)
            if it.note:
                run.write(it.rel, it.note)
                continue
            if os.path.lexists(dst):
                run.write(it.rel, 'there' if same_file(dst, it, hasher) else 'conflict', dst=dst)
                continue
            if not os.path.lexists(src):
                run.write(it.rel, 'gone')
                continue
            if not unchanged(src, it):
                run.write(it.rel, 'changed', why='changed after the list')
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if it.kind == 'l':
                os.symlink(it.target, dst)
                run.write(it.rel, 'copied', dst=dst)
                continue
            tmp = temp_name(dst, names)
            try:
                copy_bytes(src, tmp)
            except FileExistsError:
                run.write(it.rel, 'unreadable', why='its temporary name was taken while it was copied')
                continue
            except OSError as e:
                if os.path.lexists(tmp):
                    os.unlink(tmp)
                if e.errno == errno.ENOSPC:
                    raise Fail(f'{show(dest)}: the drive is full; stopped')
                run.write(it.rel, 'unreadable', why=e.strerror)
                continue
            batch.append((it, dst, tmp))
            size += it.size
            if len(batch) >= BATCH_FILES or size >= BATCH_BYTES:
                flush()
        flush()
    finally:
        for _, _, tmp in batch:   # a stop leaves no temporary copies behind
            if os.path.lexists(tmp):
                os.unlink(tmp)


# --- trash ---

def trash_folder(cfg: Config, source: str, label: str) -> str:
    """Where the originals go: the Trash of the volume they are on, in a folder named after the date and the run."""
    name = f'{now():%Y-%m-%d %H.%M} {label}'
    if os.stat(source).st_dev == os.stat(cfg.home).st_dev:
        return os.path.join(cfg.home, '.Trash', name)
    mount = os.path.realpath(source)
    while not os.path.ismount(mount):
        mount = os.path.dirname(mount)
    return os.path.join(mount, '.Trashes', str(os.getuid()), name)


def backed_up(run: Run, dest: str, kept: list, after: datetime.datetime, times: list | None = None) -> list:
    """Why the copies cannot count as backed up yet: one line per repository in "trash_requires" that has no snapshot,
    taken after AFTER, holding each copy with its size. The time of each snapshot used is added to TIMES."""
    from .units import snapshot_time
    problems = []
    for name in run.cfg.trash_requires:
        repo = run.cfg.repositories[name]
        if not restic.drive_ready(run.cfg, repo):
            problems.append(f'{name}: {run.cfg.drives[repo.drive].mount} is not mounted, so its snapshots cannot be read')
            continue
        snaps = [s for s in restic.snapshots(run.cfg, repo) if snapshot_time(s) > after and
                 any(dest == p or dest.startswith(p.rstrip('/') + '/') for p in s.get('paths') or [])]
        if not snaps:
            problems.append(f'{name} has no snapshot of {show(dest)} taken after the copy ended ({iso(after)})')
            continue
        snap = max(snaps, key=snapshot_time)
        if times is not None:
            times.append(snapshot_time(snap))
        have = {}
        for line in restic.capture(run.cfg, repo, ['--no-lock', 'ls', '--json', '--recursive', snap['id'], dest]).splitlines():
            try:
                node = json.loads(line)
            except ValueError:
                continue
            if node.get('type') in ('file', 'symlink') and 'path' in node:
                have[nfc(node['path'])] = node.get('size', 0)
        missing = [it for it in kept if have.get(nfc(os.path.join(dest, it.rel)), -1) != it.size]
        if missing:
            problems.append(f'{name}: snapshot {snap["id"][:8]} lacks {len(missing):,} of the copies, such as '
                            f'{show(missing[0].rel)}')
    return problems


def trash_all(run: Run, source: str, dest: str, label: str, trash_dir: str = '') -> None:
    hasher = Hasher(run.cfg.b3sum)
    kept = [i for i in run.items if run.st(i.rel) in KEPT]
    after = parse_iso(run.meta['copied'])
    assert after
    times = []
    problems = backed_up(run, dest, kept, after, times)
    if problems:
        for p in problems:
            run.log(f'{label}: not yet backed up: {p}')
        raise Fail('the originals stay until the backups hold the copies: run this again after the next backup of '
                   + ', '.join(run.cfg.trash_requires), 75)
    base = run.meta.get('trash_dir') or trash_dir or trash_folder(run.cfg, source, label)
    run.set(trash_dir=base)
    # a stop between the journal row and the move: the row says planned; the move did or did not happen
    for it in kept:
        r = run.state.get(it.rel)
        if r and r['state'] == 'planned':
            src = os.path.join(source, it.rel)
            if not os.path.lexists(src) and os.path.lexists(r['to']):
                run.write(it.rel, 'trashed', to=r['to'])
            elif os.path.lexists(src):
                run.state[it.rel] = r['before']
    todo = [i for i in run.items if run.st(i.rel) in KEPT]
    run.log(f'{label}: checking {len(todo):,} originals and their copies before they go to {show(base)}')
    files = [i for i in todo if i.kind == 'f' and unchanged(os.path.join(source, i.rel), i)]
    now_hashes = dict(zip([i.rel for i in files], hasher.many([os.path.join(source, i.rel) for i in files],
                                                              [i.size for i in files], run.log, label)))
    # A copy changed since the snapshots holds other bytes than they do, so its ctime must be older than each of them,
    # and its bytes must still be the original's.
    snap_ns = int(min(times).timestamp() * 1e9) if times else 0
    copies = [i for i in files if os.path.isfile(os.path.join(dest, i.rel))]
    copy_hashes = dict(zip([i.rel for i in copies], hasher.many([os.path.join(dest, i.rel) for i in copies],
                                                                [i.size for i in copies], run.log, f'{label}: copies')))
    moved = 0
    for it in todo:
        src, dst = os.path.join(source, it.rel), os.path.join(dest, it.rel)
        before = run.state[it.rel]
        if not unchanged(src, it) or (it.kind == 'f' and now_hashes.get(it.rel) != run.b3(it)):
            run.write(it.rel, 'stays', why='changed since it was copied', before=before)
            continue
        try:
            st = os.lstat(dst)
        except FileNotFoundError:
            st = None
        if st is None or (it.kind == 'f' and (not stat.S_ISREG(st.st_mode) or st.st_size != it.size)) or \
                (it.kind == 'l' and not same_file(dst, it, hasher)):
            run.write(it.rel, 'stays', why='its copy is not there with its size', before=before)
            continue
        if (times and st.st_ctime_ns > snap_ns) or (it.kind == 'f' and copy_hashes.get(it.rel) != run.b3(it)):
            run.write(it.rel, 'stays', why='its copy changed since it was copied or backed up', before=before)
            continue
        if locked(src):
            run.write(it.rel, 'stays', why=LOCKED_WHY, before=before)
            continue
        if it.kind == 'f' and resource_fork_size(src):
            run.write(it.rel, 'stays', why=RESOURCE_FORK_WHY, before=before)
            continue
        to = os.path.join(base, it.rel)
        if os.path.lexists(to):
            raise Fail(f'already in the Trash folder: {show(to)}')
        os.makedirs(os.path.dirname(to), exist_ok=True)
        run.write(it.rel, 'planned', to=to, before=before)
        try:
            os.rename(src, to)
        except PermissionError as e:
            run.write(it.rel, 'stays', why=f'cannot move it: {e.strerror}', before=before)
            continue
        run.write(it.rel, 'trashed', to=to)
        moved += 1
    # the folders left empty, deepest first; the source folder itself stays
    for folder, dirs, names in sorted(os.walk(source, topdown=False), key=lambda t: -t[0].count('/')):
        if folder != source and not os.listdir(folder):
            try:
                os.rmdir(folder)
            except OSError:
                pass
    run.log(f'{label}: {moved:,} originals moved to {show(base)}')


# --- report ---

def report(run: Run, label: str) -> tuple:
    """(the counts line, the number of items that stay where they are) and up to 50 of those in the log."""
    counts: dict = {}
    for it in run.items:
        s = run.st(it.rel) or 'not done'
        counts[s] = counts.get(s, 0) + 1
    stays = [(it, run.state.get(it.rel, {})) for it in run.items if run.st(it.rel) not in ('copied', 'there', 'trashed')]
    for it, r in stays[:SHOWN]:
        run.log(f'{label}: stays: {show(it.rel)}: {r.get("state", "not done")}{": " + r["why"] if r.get("why") else ""}')
    if len(stays) > SHOWN:
        run.log(f'{label}: and {len(stays) - SHOWN:,} more; all are in {run.journal_path}')
    line = ', '.join(f'{n:,} {s}' for s, n in sorted(counts.items()))
    return line, len(stays)


# --- the two commands ---

def _check_dest(cfg: Config, source: str, dest: str) -> None:
    for a, b in ((source, dest), (dest, source)):
        if a == b or a.startswith(b.rstrip('/') + '/'):
            raise Fail(f'{show(source)} and {show(dest)} are inside each other', 64)
    d = drive_of_path(cfg, dest)
    if d and not mounted(d.mount):
        raise Fail(f'{d.mount} is not mounted', 69)


def ingest(cfg: Config, source: str, dest: str, then_trash: bool, log: Log, trash_dir: str = '') -> int:
    """Copies everything under SOURCE to DEST at the same paths. With THEN_TRASH, the originals that were copied go to
    the Trash once the backups hold the copies. Exit 0 if every file was copied (and trashed), 1 if some stay."""
    source, dest = os.path.abspath(source).rstrip('/'), os.path.abspath(dest).rstrip('/')
    if not os.path.isdir(source):
        raise Fail(f'{show(source)} is not a folder', 66)
    _check_dest(cfg, source, dest)
    key = hashlib.sha1(f'{source}\0{dest}'.encode('utf-8', 'surrogateescape')).hexdigest()[:10]
    name = 'ingest-' + re.sub(r'[^A-Za-z0-9._-]', '_', os.path.basename(source))[:40] + '-' + key
    label = f'ingest {os.path.basename(source)}'
    run = Run(cfg, name, log)
    if run.meta.get('trashed') or (run.meta.get('copied') and not then_trash):
        log(f'{label}: the last run of this source and destination ended on {run.meta.get("trashed") or run.meta["copied"]}; '
            f'this is a new run')
        run.put_aside()
    if not run.meta.get('listed'):
        run.set(source=source, dest=dest, started=iso())
        make_list(run, source, lambda rel: True, lambda rel: True, label)
        run.set(listed=iso())
    else:
        log(f'{label}: continuing the run started on {run.meta["started"]}')
    run.load()
    if not run.meta.get('copied'):
        copy_all(run, source, dest, label)
        run.set(copied=iso())
    if then_trash:
        trash_all(run, source, dest, label, trash_dir)
        run.set(trashed=iso())
    line, stays = report(run, label)
    log(f'{label}: {line}')
    return 1 if stays else 0


def media_filters(m: Media):
    skip = {s.rstrip('/') for s in m.skip}
    tops = [f.rstrip('/') for f in m.folders]

    def in_bundle(rel: str) -> bool:
        return any(part.endswith(tuple(m.bundles)) for part in rel.split('/')[:-1]) if m.bundles else False

    def derived(rel: str) -> bool:
        return in_bundle(rel) and (os.path.basename(rel) in m.derived_names or rel.endswith(tuple(m.derived_paths)))

    def keep_dir(rel: str) -> bool:
        inside = any(rel == t or rel.startswith(t + '/') or t.startswith(rel + '/') for t in tops)
        return inside and rel not in skip and not derived(rel)

    def keep_file(rel: str) -> bool:
        return any(rel.startswith(t + '/') for t in tops) and os.path.basename(rel) not in m.skip_files and \
            rel not in skip
    return keep_dir, keep_file


def copy_media(cfg: Config, log: Log) -> str:
    """The new photos and videos in the media folders, copied to a new folder at "dest". Changes nothing at the source.
    The next run takes what changed after this one started listing."""
    m = cfg.media
    if not m:
        raise Fail('there are no "copy_media" settings', 78)
    label = 'copy-media'
    run = Run(cfg, 'copy-media', log)
    stamp_path = os.path.join(cfg.state_dir, 'transfers', 'copy-media-since.json')
    if run.meta.get('copied'):
        run.put_aside()
    if not run.meta.get('listed'):
        started = now()
        dest = m.dest.replace('{date}', f'{started:%Y-%m-%d}')
        _check_dest(cfg, m.home, dest)
        since = read_json(stamp_path, {}).get('since_ns', 0) or m.since_ns
        run.set(source=m.home, dest=dest, started=iso(started), since_ns=since, list_started_ns=time_ns(started))
        keep_dir, keep_file = media_filters(m)
        make_list(run, m.home, keep_dir, keep_file, label, since_ns=since)
        run.set(listed=iso())
    else:
        log(f'{label}: continuing the run started on {run.meta["started"]}')
        _check_dest(cfg, m.home, run.meta['dest'])
    dest = run.meta['dest']
    run.load()
    if not run.items:
        write_json(stamp_path, {'since_ns': run.meta['list_started_ns'], 'run': run.meta['started']})
        run.set(copied=iso())
        return 'nothing new'
    copy_all(run, m.home, dest, label)
    run.set(copied=iso())
    line, stays = report(run, label)
    log(f'{label}: {line}')
    copied = [i for i in run.items if run.st(i.rel) in KEPT]
    message = f'{len(copied):,} files, {human_bytes(sum(i.size for i in copied))}, in {show(dest)}'
    if stays:
        # The stamp does not move: the next run lists these again.
        raise Fail(f'{message}; {stays:,} could not be copied (see the log); the next run tries them again')
    write_json(stamp_path, {'since_ns': run.meta['list_started_ns'], 'run': run.meta['started']})
    return message


def time_ns(t: datetime.datetime) -> int:
    return int(t.timestamp() * 1_000_000_000)
