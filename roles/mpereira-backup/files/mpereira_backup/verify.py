"""Checks that a repository gives back what it holds: a read-back of the files a local repository gained since a time,
and a restore of a folder that is compared with the folder on disk.

Every data, index, and snapshot file of a restic repository is named by the SHA-256 of its bytes, so a file whose hash
matches its name is exactly what restic wrote. "restic check" checks the structure; reading back what is new checks the
bytes too, at the cost of reading only what the last backups added, where "--read-data" would read everything.
"""
from __future__ import annotations

import concurrent.futures
import datetime
import hashlib
import os
import shutil
import stat

from . import restic
from .config import Config, Repository
from .quarantine import CACHEDIR_SIGNATURE, Coverage, covering, under
from .transfer import Hasher
from .units import local_folder, snapshot_time
from .util import Every, Fail, Log, human_bytes, iso, need_space, now, parse_iso, show

TOPS = ('data', 'index', 'snapshots')
SHOWN = 50


# --- the read-back of new files ---

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _read_one(path: str) -> str:
    try:
        return _sha256(path)
    except OSError:
        return ''


def read_new(cfg: Config, repo: Repository, since: datetime.datetime, log: Log) -> tuple:
    """Reads back each file of a local repository written since SINCE. Returns (files read, bytes, the paths that do
    not match their names)."""
    folder = local_folder(repo.repository)
    if not folder:
        raise Fail(f'{repo.name} is not on a disk: its new files cannot be read back here; use '
                   f'"mpereira-backup check {repo.name} -- --read-data-subset 1/12"', 64)
    if not os.path.isfile(os.path.join(folder, 'config')):
        raise Fail(f'{show(folder)} is not a restic repository (is its drive mounted?)', 69)
    since_ns = int(since.timestamp() * 1e9)
    todo, total = [], 0
    for top in TOPS:
        for d, _, names in os.walk(os.path.join(folder, top)):
            for n in names:
                p = os.path.join(d, n)
                st = os.lstat(p)
                total += 1
                if st.st_mtime_ns >= since_ns and stat.S_ISREG(st.st_mode):
                    todo.append((p, st.st_size))
    size = sum(s for _, s in todo)
    log(f'{repo.name}: {total:,} files in the repository; {len(todo):,} written since {iso(since)}, '
        f'{human_bytes(size)}: reading them back')
    if total == 0:
        raise Fail(f'{show(folder)} has no data, index, or snapshot files', 69)
    bad, done = [], 0
    every = Every(60)
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        for (p, s), h in zip(todo, pool.map(_read_one, [p for p, _ in todo])):
            done += s
            if h != os.path.basename(p):
                bad.append(p)
            if every():
                log(f'{repo.name}: read back {human_bytes(done)} of {human_bytes(size)}')
    for p in bad[:SHOWN]:
        log(f'{repo.name}: DOES NOT MATCH ITS NAME: {show(p)}')
    log(f'{repo.name}: read back {len(todo):,} files, {human_bytes(size)}; {len(bad):,} do not match')
    return len(todo), size, bad


def since_arg(text: str, last: str | None) -> datetime.datetime:
    """--new-since: an ISO 8601 time, or "last" for the start of the last read-back that passed."""
    if text == 'last':
        t = parse_iso(last)
        if not t:
            raise Fail('no read-back passed before: give a time, such as 2026-11-08T00:00:00+01:00', 64)
        return t
    try:
        t = datetime.datetime.fromisoformat(text[:-1] + '+00:00' if text.endswith(('Z', 'z')) else text)
    except ValueError:
        t = None
    if t is None:
        raise Fail(f'not a time: {text!r}; give an ISO 8601 time, such as 2026-11-08T00:00:00+01:00, or "last"', 64)
    return t if t.tzinfo else t.astimezone()


# --- the restore test ---

def _files(root: str) -> dict:
    """rel path -> lstat, for each file and symbolic link under ROOT."""
    out = {}
    for d, dirs, names in os.walk(root):
        for n in names + [x for x in dirs if os.path.islink(os.path.join(d, x))]:
            p = os.path.join(d, n)
            out[os.path.relpath(p, root)] = os.lstat(p)
    return out


def _remove(folder: str) -> None:
    for d, dirs, _ in os.walk(folder):   # restic restores each folder's mode, and a read-only folder cannot be emptied
        for x in dirs:
            p = os.path.join(d, x)
            if not os.path.islink(p):
                os.chmod(p, 0o700)
    shutil.rmtree(folder)


def restore_test(cfg: Config, repo: Repository, path: str, into: str, snap_id: str, log: Log,
                 own_into: bool = False) -> int:
    """Restores PATH from the newest snapshot that holds it (or SNAP_ID) into INTO, and compares it with PATH on disk.
    Passes when every restored file that did not change on disk since has the same content, and every file on disk
    that is older than the snapshot and that the repository does not leave out by choice was restored.
    INTO must be empty or not exist, unless OWN_INTO says it is this tool's own folder (default_into): what a test
    that failed left there is then removed first. A test that passes removes what it restored, and INTO if it made it."""
    path = os.path.abspath(path).rstrip('/')
    into = os.path.abspath(into).rstrip('/')
    label = f'restore-test {repo.name}'
    if not os.path.isdir(path):
        raise Fail(f'{show(path)} is not a folder', 66)
    if not restic.drive_ready(cfg, repo):
        raise Fail(f'{repo.name}: {cfg.drives[repo.drive].mount} is not mounted', 69)
    snaps = restic.snapshots(cfg, repo)
    if snap_id:
        snap = next((s for s in snaps if s['id'].startswith(snap_id)), None)
        if snap is None:
            raise Fail(f'{repo.name} has no snapshot {snap_id}', 64)
        if not any(under(path, p) for p in snap.get('paths') or []):
            raise Fail(f'snapshot {snap_id} does not hold {show(path)}', 64)
    else:
        snap = covering(snaps, path)
        if snap is None:
            raise Fail(f'no snapshot of {repo.name} holds {show(path)} (a folder split into several snapshots: pick '
                       'a folder inside one of them)', 64)
    sid = snap['id']
    taken = snapshot_time(snap)
    if under(into, path) or under(path, into):
        raise Fail('the restore folder and the folder to test must not be inside each other', 64)
    made = not os.path.lexists(into)
    if not made:
        if not os.path.isdir(into) or os.path.islink(into):
            raise Fail(f'{show(into)} is not a folder', 64)
        if os.listdir(into):
            if not own_into:
                raise Fail(f'{show(into)} is not empty: give an empty folder, or one that does not exist', 64)
            log(f'{label}: removing what an earlier test left in {show(into)}')
            _remove(into)
            made = True
    disk = _files(path)
    need_space(into, sum(st.st_size for st in disk.values()), label)
    os.makedirs(into, exist_ok=True)
    with open(os.path.join(into, 'CACHEDIR.TAG'), 'wb') as f:   # no backup keeps the restored copy
        f.write(CACHEDIR_SIGNATURE + b'\n')
    log(f'{label}: restoring {show(path)} from snapshot {sid[:8]} ({iso(taken)}) into {show(into)}')
    rc = restic.run(cfg, repo, ['restore', sid, '--target', into, '--include', path], log)
    if rc != 0:
        log(f'{label}: restic restore exited with {rc}; the restored files stay in {show(into)}')
        return rc
    copy = into + path
    restored = _files(copy) if os.path.isdir(copy) else {}
    taken_ns = int(taken.timestamp() * 1e9)
    same, changed, gone, fails = [], 0, 0, []
    for rel, r in restored.items():
        o = disk.get(rel)
        if o is None:
            gone += 1
        elif stat.S_ISLNK(r.st_mode) or stat.S_ISLNK(o.st_mode):
            if not (stat.S_ISLNK(r.st_mode) and stat.S_ISLNK(o.st_mode)):
                if o.st_ctime_ns > taken_ns:
                    changed += 1
                else:   # unchanged on disk since the snapshot, so its copy must be of the same type
                    fails.append((rel, 'the restored copy is a link and the file on disk is not, or the reverse'))
            elif o.st_ctime_ns > taken_ns:
                changed += 1
            elif os.readlink(os.path.join(copy, rel)) != os.readlink(os.path.join(path, rel)):
                fails.append((rel, 'the restored link points elsewhere'))
        elif o.st_ctime_ns > taken_ns:
            changed += 1
        elif (o.st_size, o.st_mtime_ns) != (r.st_size, r.st_mtime_ns):
            # the file on disk did not change since the snapshot, so its copy must have its size and time
            fails.append((rel, 'the restored file has another size or modification time'))
        else:
            same.append(rel)
    hasher = Hasher(cfg.b3sum)
    sizes = [restored[rel].st_size for rel in same]
    log(f'{label}: comparing {len(same):,} files by content, {human_bytes(2 * sum(sizes))} to read')
    a = hasher.many([os.path.join(copy, rel) for rel in same], sizes, log, f'{label}: restored')
    b = hasher.many([os.path.join(path, rel) for rel in same], sizes, log, f'{label}: on disk')
    for rel, x, y in zip(same, a, b):
        if not x or not y:
            fails.append((rel, 'cannot be read'))
        elif x != y:
            fails.append((rel, 'the restored file has other content'))
    cov = Coverage(repo)
    missing = left_out = 0
    for rel, o in disk.items():
        if rel in restored or o.st_ctime_ns > taken_ns:
            continue
        if cov.left_out(os.path.join(path, rel)):
            left_out += 1
        else:
            missing += 1
            fails.append((rel, 'not restored, and older than the snapshot'))
    for rel, why in fails[:SHOWN]:
        log(f'{label}: FAIL {show(os.path.join(path, rel))}: {why}')
    log(f'{label}: {len(restored):,} files restored; {len(same):,} compared by content; {changed:,} changed and '
        f'{gone:,} removed on disk since the snapshot; {left_out:,} on disk left out by choice; {len(fails):,} fail')
    if fails:
        log(f'{label}: FAILED; the restored files stay in {show(into)} for a look')
        return 1
    if made:
        _remove(into)
    else:   # a folder that was empty before: only what this test put in it goes
        _remove(os.path.join(into, path.lstrip('/').split('/')[0]))
        os.remove(os.path.join(into, 'CACHEDIR.TAG'))
    log(f'{label}: passed; removed the restored copy')
    return 0


def default_into(cfg: Config, repo: Repository) -> str:
    return os.path.join(cfg.state_dir, 'restore-test', repo.name)


def stamp(t: datetime.datetime | None = None) -> str:
    return iso(t or now())
