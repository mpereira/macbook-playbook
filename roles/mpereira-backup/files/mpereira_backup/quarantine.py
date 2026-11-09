"""The quarantine of a reorganization: the files that a move of duplicates or a cleanup put aside instead of removing,
and the check that must pass before they are removed for good.

A reorganization writes one journal for each batch, JOURNALS/<BATCH>.tsv, with one line per file and these columns:
batch, source, keeper, keeper_location, class, blake3, size, mtime, quarantine, state. The last line for a source gives
its state ("planned", "moved", "purged", ...). "keeper" is the copy that stays, "blake3" the hash of the keeper and of
the source when the source was moved, and "quarantine" where the source went. A move with no quarantine is a rename, and
the keepers of later batches are followed through it.

"quarantine check BATCH" passes only if, for every file of the batch that is still in the quarantine folder:
  - its keeper, followed through the renames of all journals, is there, outside the quarantine, and hashes now to the
    hash in the journal;
  - each repository of the keeper's location ("quarantine.locations") holds the keeper, unless that repository leaves
    it out by the owner's choice: no source holds it, its excludes match it or a folder above it, a folder above it has
    a valid CACHEDIR.TAG, or a folder above it is a build output. A class "A" keeper that no repository holds fails.
    "Holds it" means: the newest snapshot that holds the path has it with the same size, mtime, ctime, and inode. A
    change of content always changes the ctime, and the keeper was just hashed, so that snapshot holds the same content.
    Where the metadata differs, the file is read back from the snapshot and hashed. A share of the others
    ("quarantine.read_back", per repository) is read back too;
  - each of those repositories passed "mpereira-backup check" (or the check job) within "check_max_age_days".
"quarantine purge BATCH" runs the same check, and when it passes and the newest file in the batch's quarantine folder
was put there more than "min_age_days" ago, removes each file whose size and mtime are still those in the journal, adds
a "purged" line for it to the journal, and removes the folders left empty. Only files under "quarantine.root" are
checked and removed: what a batch moved to the Trash is the owner's to empty.

Hashes are saved as they finish, in STATE_DIR/quarantine/, so a stopped check continues where it stopped.
"""
from __future__ import annotations

import calendar
import concurrent.futures
import datetime
import json
import os
import random
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field

from . import build_outputs, restic
from .config import Config, Repository
from .transfer import BATCH_BYTES, BATCH_FILES, Hasher, nfc
from .units import excludes_of, home_paths, snapshot_time
from .util import Every, Fail, Log, human_age, human_bytes, iso, locked, now, parse_iso, read_json, show, write_json

COLUMNS = ('batch', 'source', 'keeper', 'keeper_location', 'class', 'blake3', 'size', 'mtime', 'quarantine', 'state')
CACHEDIR_SIGNATURE = b'Signature: 8a477f597d28d172789f06886806bc55'
RFC3339 = re.compile(r'^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$')
SHOWN = 50


@dataclass
class Entry:
    batch: str
    source: str
    keeper: str
    location: str
    cls: str
    blake3: str
    size: int
    mtime: int
    quarantine: str
    state: str

    def line(self, state: str) -> str:
        return '\t'.join([self.batch, self.source, self.keeper, self.location, self.cls, self.blake3, str(self.size),
                          str(self.mtime), self.quarantine, state]) + '\n'


@dataclass
class Result:
    checked: int = 0
    passed: int = 0
    failures: list = field(default_factory=list)   # (quarantine path, why)
    blocked: list = field(default_factory=list)    # problems that stop the whole batch
    gone: list = field(default_factory=list)       # entries whose quarantine file is not there any more
    left_out: dict = field(default_factory=dict)   # (repository, why) -> count
    one_backup: int = 0                            # class A keepers that only one repository holds
    entries: list = field(default_factory=list)    # the entries that passed: what a purge may remove

    @property
    def ok(self) -> bool:
        return not self.failures and not self.blocked


# --- journals ---

def journal_path(cfg: Config, batch: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9._-]+', batch) or batch.startswith('.'):
        raise Fail(f'not a batch name: {batch!r}', 64)
    p = os.path.join(_settings(cfg).journals, batch + '.tsv')
    if not os.path.isfile(p):
        raise Fail(f'no journal {show(p)}; "mpereira-backup quarantine list" lists the batches', 66)
    return p


def read_journal(path: str) -> list:
    """The last line for each source, in the order the sources first appear."""
    last: dict = {}
    with open(path, encoding='utf-8', errors='surrogateescape') as f:
        for n, line in enumerate(f, 1):
            c = line.rstrip('\n').split('\t')
            if c[0] == 'batch':
                continue
            if len(c) != len(COLUMNS):
                raise Fail(f'{show(path)}:{n}: not {len(COLUMNS)} columns')
            size = int(c[6]) if c[6].isdigit() else 0
            mtime = int(c[7]) if c[7].isdigit() else 0
            last[c[1]] = Entry(c[0], c[1], c[2], c[3], c[4], c[5], size, mtime, c[8], c[9])
    return list(last.values())


def renames(folder: str) -> dict:
    """source -> keeper for each source of every journal whose last state is "moved". A file replaced in place by a
    copy (source and keeper the same path) is not a rename."""
    out = {}
    for name in sorted(os.listdir(folder)):
        if name.endswith('.tsv'):
            for e in read_journal(os.path.join(folder, name)):
                if e.state == 'moved' and e.keeper not in ('', '-', e.source):
                    out[e.source] = e.keeper
    return out


def follow(path: str, moves: dict) -> str:
    """The keeper's path now. A path that exists is current, so a later folder at an old name cannot redirect it."""
    for _ in range(64):
        if os.path.lexists(path):
            return path
        parts = path.split('/')
        for i in range(len(parts), 1, -1):
            head = '/'.join(parts[:i])
            if head in moves:
                path = moves[head] + path[len(head):]
                break
        else:
            return path
    raise Fail(f'the journals have a loop of renames at {show(path)}')


def under(path: str, folder: str) -> bool:
    folder = folder.rstrip('/')
    return path == folder or path.startswith(folder + '/')


# --- list ---

def listing(cfg: Config) -> str:
    q = _settings(cfg)
    if not os.path.isdir(q.journals):
        raise Fail(f'{show(q.journals)} is not there (is its drive mounted?)', 75)
    rows = []
    for name in sorted(os.listdir(q.journals)):
        if not name.endswith('.tsv'):
            continue
        batch = name[:-4]
        entries = read_journal(os.path.join(q.journals, name))
        held = [e for e in entries if e.quarantine not in ('', '-') and e.state != 'purged']
        if not held and not any(e.state == 'purged' for e in entries):
            continue
        n = size = elsewhere = 0
        newest = 0
        for e in held:
            try:
                st = os.lstat(e.quarantine)
            except OSError:
                continue
            if under(e.quarantine, q.root):
                n += 1
                size += st.st_size if stat.S_ISREG(st.st_mode) else 0
                newest = max(newest, st.st_ctime_ns)
            else:
                elsewhere += 1
        purged = sum(e.state == 'purged' for e in entries)
        if newest:
            put = datetime.datetime.fromtimestamp(newest / 1e9).astimezone()
            ready = put + datetime.timedelta(days=q.min_age_days)
            when = 'now' if ready <= now() else f'{ready:%Y-%m-%d}'
            age = human_age((now() - put).total_seconds())
        else:
            when = age = '-'
        rows.append((batch, f'{n:,}', human_bytes(size), age, when, f'{elsewhere:,}', f'{purged:,}'))
    head = ('batch', 'in quarantine', 'size', 'age', 'purge from', 'elsewhere', 'purged')
    widths = [max(len(r[i]) for r in rows + [head]) for i in range(len(head))]
    out = ['  '.join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in [head] + rows]
    out.append(f'"in quarantine": files under {show(q.root)}. "elsewhere": files the batch put in another place, such '
               f'as the Trash, which a purge leaves alone. "age": since the newest of them was put there.')
    return '\n'.join(out)


# --- what a repository leaves out ---

class Coverage:
    """Whether a repository's backups hold a path, by its sources and the owner's choice of what to leave out."""

    def __init__(self, repo: Repository):
        self.repo = repo
        self.ex = excludes_of(repo, '')
        self.homes: dict = {}
        self.cache_tags: dict = {}
        self.listings: dict = {}

    def _home(self, s) -> list:
        if s.name not in self.homes:
            self.homes[s.name] = home_paths(s)
        return self.homes[s.name]

    def _beside(self, folder: str) -> set:
        if folder not in self.listings:
            try:
                self.listings[folder] = set(os.listdir(folder))
            except OSError:
                self.listings[folder] = set()
        return self.listings[folder]

    def _tagged(self, folder: str) -> bool:
        if folder not in self.cache_tags:
            try:
                with open(os.path.join(folder, 'CACHEDIR.TAG'), 'rb') as f:
                    self.cache_tags[folder] = f.read(len(CACHEDIR_SIGNATURE)) == CACHEDIR_SIGNATURE
            except OSError:
                self.cache_tags[folder] = False
        return self.cache_tags[folder]

    def left_out(self, path: str) -> str:
        """Why the repository's backups leave PATH out ('' if they hold it): the reason, with the folder it is about."""
        root = ''
        for s in self.repo.sources:
            if s.kind == 'folder' and under(path, s.path):
                try:
                    if os.lstat(path).st_dev != os.stat(s.path).st_dev:
                        continue   # another file system: --one-file-system leaves it out
                except OSError:
                    pass
                root = s.path
            elif s.kind == 'paths':
                root = next((p for p in s.paths if under(path, p)), '')
            elif s.kind == 'home':
                root = next((p for p in self._home(s) if under(path, p)), '')
            if root:
                break
        if not root:
            return 'no source holds it'
        parts = path.split('/')
        prefixes = ['/'.join(parts[:i]) for i in range(2, len(parts) + 1)]
        for p in prefixes:
            if self.ex.wholly(p):
                return f'excluded: {p}'
        for p in prefixes[:-1]:
            if under(p, root) and self._tagged(p):
                return f'CACHEDIR.TAG: {p}'
        if self.repo.build_outputs:
            for p in prefixes[:-1]:
                parent = os.path.dirname(p)
                if under(p, root) and p != root and build_outputs.is_output(os.path.basename(p), parent,
                                                                           self._beside(parent)):
                    return f'build output: {p}'
        return ''


# --- restic ---

def restic_ns(text: str) -> int:
    """A time that restic wrote, as nanoseconds since the epoch. A float would lose the last digits."""
    m = RFC3339.match(text or '')
    if not m:
        return -1
    y, mo, d, h, mi, s, frac, tz = m.groups()
    secs = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s)))
    if tz != 'Z':
        off = int(tz[1:3]) * 3600 + int(tz[4:6]) * 60
        secs -= off if tz[0] == '+' else -off
    return secs * 10**9 + int((frac or '').ljust(9, '0'))


def snapshot_nodes(cfg: Config, repo: Repository, snap_id: str, wanted: set) -> dict:
    """path -> (type, size, mtime, ctime, inode) for the wanted paths (in NFC) in a snapshot, read in one stream."""
    folders = sorted({os.path.dirname(p) for p in wanted})
    args = ['--no-lock', 'ls', '--json', '--recursive', snap_id] + _outermost(folders)
    nodes = {}
    with tempfile.TemporaryFile() as err, subprocess.Popen(
            restic.command(cfg, repo, args), env=restic.environment(cfg, repo), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=err) as p:
        assert p.stdout
        for line in p.stdout:
            try:
                n = json.loads(line)
            except ValueError:
                continue
            if n.get('struct_type', 'node') != 'node' or 'path' not in n:
                continue
            path = nfc(n['path'])
            if path in wanted:
                nodes[path] = (n.get('type'), n.get('size', 0), restic_ns(n.get('mtime', '')),
                               restic_ns(n.get('ctime', '')), n.get('inode'))
        rc = p.wait()
        err.seek(0)
        msg = err.read().decode('utf-8', 'replace').strip().splitlines()
    if rc != 0:
        raise Fail(f'restic ls on {repo.name} exited with {rc}: {msg[-1] if msg else "no message"}')
    return nodes


def _outermost(folders: list) -> list:
    """The folders that are not inside another one of them."""
    out: list = []
    for f in sorted(folders):
        if not out or not under(f, out[-1]):
            out.append(f)
    return out


def dump_hash(cfg: Config, repo: Repository, snap_id: str, path: str) -> str:
    """The hash of PATH as the snapshot holds it, read back from the repository; '' if it cannot be read."""
    with subprocess.Popen(restic.command(cfg, repo, ['--no-lock', 'dump', snap_id, path]),
                          env=restic.environment(cfg, repo), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as d:
        b = subprocess.run([cfg.b3sum, '--no-names'], stdin=d.stdout, capture_output=True)
        assert d.stdout
        d.stdout.close()
        if d.wait() != 0 or b.returncode != 0:
            return ''
    return b.stdout.decode().strip()


def covering(snaps: list, path: str) -> dict | None:
    """The newest snapshot that holds PATH: one of its paths is PATH or a folder above it."""
    have = [s for s in snaps if any(under(path, p) for p in s.get('paths') or [])]
    return max(have, key=snapshot_time) if have else None


# --- the check ---

class Saved:
    """Hashes kept between runs: STATE_DIR/quarantine/BATCH-hashes.json and BATCH-reads.json."""

    def __init__(self, cfg: Config, batch: str):
        folder = os.path.join(cfg.state_dir, 'quarantine')
        os.makedirs(folder, exist_ok=True)
        self.hashes_path = os.path.join(folder, f'{batch}-hashes.json')
        self.reads_path = os.path.join(folder, f'{batch}-reads.json')
        self.hashes = read_json(self.hashes_path, {})
        self.reads = read_json(self.reads_path, {})

    def hash_of(self, path: str, st: os.stat_result) -> str:
        v = self.hashes.get(path)
        return v[4] if v and v[:4] == [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino] else ''

    def save(self) -> None:
        write_json(self.hashes_path, self.hashes)
        write_json(self.reads_path, self.reads)


def hash_keepers(cfg: Config, saved: Saved, stats: dict, log: Log, label: str) -> dict:
    """path -> hash now ('' if it cannot be read), reusing the saved hash of a file whose metadata did not change."""
    out = {}
    todo = []
    for p, st in stats.items():
        h = saved.hash_of(p, st)
        if h:
            out[p] = h
        else:
            todo.append(p)
    if out:
        log(f'{label}: {len(out):,} keepers hashed by an earlier run and unchanged since')
    chunks, chunk, size = [], [], 0
    for p in sorted(todo):
        chunk.append(p)
        size += stats[p].st_size
        if len(chunk) >= BATCH_FILES or size >= BATCH_BYTES:
            chunks.append(chunk)
            chunk, size = [], 0
    if chunk:
        chunks.append(chunk)
    hasher = Hasher(cfg.b3sum)
    total = sum(stats[p].st_size for p in todo)
    done = 0
    every = Every(60)
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        for c, hashes in zip(chunks, pool.map(hasher.batch, chunks)):
            for p, h in zip(c, hashes):
                out[p] = h
                st = stats[p]
                if h:
                    saved.hashes[p] = [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, h]
                done += st.st_size
            saved.save()
            if every():
                log(f'{label}: hashed {len(out):,} of {len(stats):,} keepers, {human_bytes(done)} of '
                    f'{human_bytes(total)}')
    return out


def settled(cfg: Config, saved: Saved, path: str, before: os.stat_result, h: str) -> bool:
    """True if the keeper did not change while it was hashed (to H). Only the ctime changed: OpenZFS on macOS sets it
    on the first read of a file, and an attribute change sets it too. A second read that gives H, with no further
    change, shows the content held; the saved hash then holds for the new stat."""
    st = os.lstat(path)
    if (st.st_size, st.st_mtime_ns, st.st_ctime_ns) == (before.st_size, before.st_mtime_ns, before.st_ctime_ns):
        return True
    if not h or (st.st_ino, st.st_size, st.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns):
        return False
    try:
        again = Hasher(cfg.b3sum).one(path)
    except OSError:
        return False
    last = os.lstat(path)
    if again != h or (last.st_size, last.st_mtime_ns, last.st_ctime_ns) != (st.st_size, st.st_mtime_ns, st.st_ctime_ns):
        return False
    saved.hashes[path] = [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, h]
    saved.save()
    return True


def check(cfg: Config, batch: str, log: Log, rng: random.Random | None = None) -> Result:
    q = _settings(cfg)
    path = journal_path(cfg, batch)
    label = f'quarantine {batch}'
    entries = [e for e in read_journal(path) if under(e.quarantine, q.root) and e.state != 'purged']
    res = Result()
    todo = []
    for e in entries:
        if os.path.lexists(e.quarantine):
            todo.append(e)
        else:
            res.gone.append(e)
    res.checked = len(todo)
    log(f'{label}: {len(todo):,} files in {show(q.root)} to check; {len(res.gone):,} in the journal are not there')
    if not todo:
        return res

    moves = renames(q.journals)
    keeper = {}     # entry source -> the keeper's path now
    fail = {}       # entry source -> why
    for e in todo:
        if e.keeper in ('', '-'):
            fail[e.source] = f'no keeper (state {e.state})'
            continue
        if not re.fullmatch(r'[0-9a-f]{64}', e.blake3):
            fail[e.source] = 'no hash in the journal'
            continue
        if e.location not in q.locations:
            fail[e.source] = f'keeper location "{e.location}" is not in quarantine.locations'
            continue
        k = follow(e.keeper, moves)
        if under(k, q.root):
            fail[e.source] = f'its keeper is in the quarantine: {show(k)}'
            continue
        keeper[e.source] = k

    stats = {}
    for k in set(keeper.values()):
        try:
            st = os.lstat(k)
        except OSError as err:
            stats[k] = err.strerror or 'cannot be read'
            continue
        stats[k] = st if stat.S_ISREG(st.st_mode) else 'not a regular file'
    for src, k in keeper.items():
        if isinstance(stats[k], str):
            fail[src] = f'keeper {show(k)}: {stats[k]}'

    # Which repositories must hold each keeper.
    coverage = {}
    need: dict = {}   # repository -> set of keeper paths
    by_source = {e.source: e for e in todo}
    for src, k in keeper.items():
        if src in fail:
            continue
        e = by_source[src]
        holders, chosen = [], False
        for rn in q.locations[e.location]:
            if rn not in coverage:
                coverage[rn] = Coverage(cfg.repositories[rn])
            why = coverage[rn].left_out(k)
            if why:
                chosen = chosen or why != 'no source holds it'
                key = (rn, re.sub(r': .*', '', why) if why == 'no source holds it' else why)
                res.left_out[key] = res.left_out.get(key, 0) + 1
            else:
                holders.append(rn)
                need.setdefault(rn, set()).add(k)
        # "R": rebuildable, such as a build output. While a repository leaves it out by choice, the keeper is enough;
        # once none does, it is checked as "A".
        backed = e.cls == 'A' or (e.cls == 'R' and not chosen)
        if backed and not holders:
            fail[src] = f'class {e.cls}, but no repository of "{e.location}" holds {show(k)}'
        elif backed and len(holders) == 1:
            res.one_backup += 1

    # Each repository that must hold a keeper: reachable, and checked lately.
    st = read_json(os.path.join(cfg.state_dir, 'state.json'), {})
    for rn in sorted(need):
        repo = cfg.repositories[rn]
        if not restic.drive_ready(cfg, repo):
            res.blocked.append(f'{rn}: {cfg.drives[repo.drive].mount} is not mounted')
            continue
        last = parse_iso((st.get('repos', {}).get(rn) or {}).get('last_check'))
        if not last or (now() - last).total_seconds() > q.check_max_age_days * 86400:
            res.blocked.append(f'{rn}: no passing check within {q.check_max_age_days:g} days (last: '
                               f'{iso(last) if last else "never"}); run: mpereira-backup check {rn}')

    # The keepers' content now.
    saved = Saved(cfg, batch)
    live = {k: stats[k] for k in set(keeper.values()) if not isinstance(stats[k], str)}
    log(f'{label}: hashing {len(live):,} keepers, {human_bytes(sum(s.st_size for s in live.values()))}')
    hashes = hash_keepers(cfg, saved, live, log, label)
    for src, k in keeper.items():
        if src in fail:
            continue
        if not hashes.get(k):
            fail[src] = f'keeper {show(k)} cannot be read'
        elif hashes[k] != by_source[src].blake3:
            fail[src] = f'keeper {show(k)} has other content than the journal says'
    for k, s in live.items():
        if not settled(cfg, saved, k, s, hashes.get(k, '')):
            for src in [x for x, y in keeper.items() if y == k]:
                fail[src] = f'keeper {show(k)} changed while it was checked'

    # Each repository holds the keepers it must hold.
    rng = rng or random.Random()
    for rn in sorted(need):
        if any(b.startswith(f'{rn}:') for b in res.blocked):
            continue
        repo = cfg.repositories[rn]
        wanted = {k for k in need[rn] if hashes.get(k)}
        snaps = restic.snapshots(cfg, repo)
        groups: dict = {}
        bad: dict = {}
        for k in wanted:
            s = covering(snaps, k)
            if s is None:
                bad[k] = f'{rn}: no snapshot holds it'
            else:
                groups.setdefault(s['id'], set()).add(k)
        reads, identical = [], []
        for sid, ks in sorted(groups.items()):
            nodes = snapshot_nodes(cfg, repo, sid, {nfc(k) for k in ks})
            for k in sorted(ks):
                n = nodes.get(nfc(k))
                s = live[k]
                if n is None:
                    bad[k] = f'{rn}: snapshot {sid[:8]} does not have it'
                elif n[0] != 'file':
                    bad[k] = f'{rn}: snapshot {sid[:8]} has it as {n[0]}'
                elif n[1:] == (s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino):
                    identical.append((sid, k))
                else:
                    reads.append((sid, k))
        differ = len(reads)
        for sid, k in reads[q.max_reads:]:
            bad[k] = f'{rn}: its metadata differs from snapshot {sid[:8]}, and more than {q.max_reads:,} do'
        reads = reads[:q.max_reads]
        share = q.read_back.get(rn, 0.01)
        sample = rng.sample(identical, min(len(identical), max(1, round(len(identical) * share)))) if identical and \
            share > 0 else []
        todo_reads = reads + [x for x in sample if x not in reads]
        log(f'{label}: {rn}: {len(identical):,} keepers match their snapshot, {differ:,} differ in metadata; reading '
            f'back {len(todo_reads):,}')
        done = saved.reads.setdefault(rn, {})
        every = Every(60)
        for i, (sid, k) in enumerate(todo_reads, 1):
            key = f'{sid} {k}'
            if not done.get(key):   # a read that failed before is tried again
                done[key] = dump_hash(cfg, repo, sid, k)
                saved.save()
            if not done[key]:
                bad[k] = f'{rn}: cannot be read back from snapshot {sid[:8]}'
            elif done[key] != hashes.get(k):
                bad[k] = f'{rn}: snapshot {sid[:8]} holds other content'
            if every():
                log(f'{label}: {rn}: read back {i:,} of {len(todo_reads):,}')
        for src, k in keeper.items():
            if src not in fail and k in bad:
                fail[src] = bad[k]

    for e in todo:
        if e.source in fail:
            res.failures.append((e.quarantine, fail[e.source]))
        else:
            res.entries.append(e)
    res.passed = len(res.entries)
    return res


def report(res: Result, batch: str, log: Log) -> None:
    label = f'quarantine {batch}'
    for (rn, why), n in sorted(res.left_out.items()):
        log(f'{label}: {rn} leaves out {n:,} keepers by choice ({why})')
    if res.one_backup:
        log(f'{label}: {res.one_backup:,} class A keepers are in one repository only')
    for e in res.gone[:SHOWN]:
        log(f'{label}: not in the quarantine any more: {show(e.quarantine)}')
    reasons: dict = {}
    for _, why in res.failures:
        k = re.sub(r'\{[^}]*\}|/[^ ]*', '…', why)
        reasons[k] = reasons.get(k, 0) + 1
    for why, n in sorted(reasons.items(), key=lambda x: -x[1]):
        log(f'{label}: {n:,} fail: {why}')
    for p, why in res.failures[:SHOWN]:
        log(f'{label}: FAIL {show(p)}: {why}')
    for b in res.blocked:
        log(f'{label}: BLOCKED: {b}')
    log(f'{label}: {res.passed:,} of {res.checked:,} pass; {len(res.failures):,} fail; {len(res.blocked)} blocking '
        f'problems' + ('' if res.ok else '; NOT A PASS'))


# --- purge ---

def purge(cfg: Config, batch: str, log: Log, dry_run: bool = False) -> int:
    q = _settings(cfg)
    label = f'quarantine {batch}'
    path = journal_path(cfg, batch)
    newest = 0
    for e in read_journal(path):
        if under(e.quarantine, q.root) and e.state != 'purged':
            try:
                newest = max(newest, os.lstat(e.quarantine).st_ctime_ns)
            except OSError:
                pass
    if not newest:
        log(f'{label}: nothing in {show(q.root)} to remove')
        return 0
    put = datetime.datetime.fromtimestamp(newest / 1e9).astimezone()
    ready = put + datetime.timedelta(days=q.min_age_days)
    if ready > now():
        log(f'{label}: nothing removed: the newest file was put in the quarantine on {iso(put)}; a purge may start on '
            f'{ready:%Y-%m-%d} ({q.min_age_days:g} days later)')
        return 75
    res = check(cfg, batch, log)
    report(res, batch, log)
    if not res.ok:
        log(f'{label}: nothing removed: the check did not pass')
        return 1
    if not res.entries:
        log(f'{label}: nothing to remove')
        return 0
    removed = size = 0
    kept = []
    todo = []
    for e in res.entries:
        try:
            st = os.lstat(e.quarantine)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_size != e.size or (e.mtime and st.st_mtime_ns != e.mtime):
            kept.append((e, 'its size or mtime is not the journal\'s'))
        elif locked(e.quarantine):
            kept.append((e, 'locked: the uchg or schg flag is on it or its folder'))
        else:
            todo.append((e, st))
    # The check read the keepers; each file removed here must itself still hold the bytes the journal names.
    log(f'{label}: reading {len(todo):,} quarantined files, {human_bytes(sum(st.st_size for _, st in todo))}, before '
        'they go')
    hashes = Hasher(cfg.b3sum).many([e.quarantine for e, _ in todo], [st.st_size for _, st in todo], log, label)
    with open(path, 'a', encoding='utf-8', errors='surrogateescape') as journal:
        for (e, st), h in zip(todo, hashes):
            if h != e.blake3:
                kept.append((e, 'its content is not the journal\'s' if h else 'it cannot be read'))
                continue
            why = still(cfg, e, st)
            if why is None:
                continue
            if why:
                kept.append((e, why))
                continue
            if dry_run:
                removed += 1
                size += st.st_size
                continue
            os.unlink(e.quarantine)
            journal.write(e.line('purged'))
            journal.flush()
            removed += 1
            size += st.st_size
        if not dry_run:
            os.fsync(journal.fileno())
    for e, why in kept[:SHOWN]:
        log(f'{label}: left in the quarantine, {why}: {show(e.quarantine)}')
    if not dry_run:
        tops = {os.path.join(q.root, os.path.relpath(e.quarantine, q.root).split('/')[0]) for e in res.entries}
        for top in sorted(tops):
            for folder, _, _ in sorted(os.walk(top, topdown=False), key=lambda t: -t[0].count('/')):
                try:
                    os.rmdir(folder)   # only an empty folder
                except OSError:
                    pass
    verb = 'would remove' if dry_run else 'removed'
    log(f'{label}: {verb} {removed:,} files, {human_bytes(size)}; {len(kept):,} left because they changed, cannot be '
        'read, or are locked')
    return 1 if kept else 0


def still(cfg: Config, e: Entry, st: os.stat_result) -> str | None:
    """Right before a quarantined file goes: '' when it is as it was when it was hashed (stat ST), None when it is gone,
    else why it stays. A file whose ctime, and nothing else, changed is read once more: OpenZFS on macOS sets the ctime
    on the first read, and a write that keeps the size and the mtime sets it too. It may go only when the second read
    gives the journal's hash and nothing changed during that read."""
    try:
        now_st = os.lstat(e.quarantine)
    except FileNotFoundError:
        return None
    if (now_st.st_ino, now_st.st_size, now_st.st_mtime_ns) != (st.st_ino, st.st_size, st.st_mtime_ns):
        return 'it changed while it was read'
    if now_st.st_ctime_ns == st.st_ctime_ns:
        return ''
    try:
        again = Hasher(cfg.b3sum).one(e.quarantine)
        last = os.lstat(e.quarantine)
    except OSError:
        return 'it changed while it was read'
    if again != e.blake3:
        return 'its content is not the journal\'s'
    if (last.st_ino, last.st_size, last.st_mtime_ns, last.st_ctime_ns) != \
            (now_st.st_ino, now_st.st_size, now_st.st_mtime_ns, now_st.st_ctime_ns):
        return 'it changed while it was read'
    return ''


def _settings(cfg: Config):
    if not cfg.quarantine:
        raise Fail('no "quarantine" settings', 78)
    return cfg.quarantine
