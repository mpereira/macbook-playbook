"""One backup of a repository: its sources cut into units, one snapshot each, so that a stopped backup continues from
the units it finished.

A "folder" source (a drive, say) becomes one unit for each entry in it, and the folders named in "split" are cut again,
one unit for each entry in them, so the largest folders are not one snapshot each. The files at the top of a split
folder go together in one unit, tagged with the source's tag. A "home" source is one unit: the visible entries of the
home folder except those in "skip", its hidden files (not its hidden folders, which hold apps' data), and the paths in
"add". A "paths" source is one unit.

Each run of restic is told its parent snapshot, the last snapshot of that unit, so that a file that did not change is
not read again, also on the first run after a backup of the whole folder.
"""
from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass, field

from . import build_outputs, patterns, restic
from .config import Config, Repository, Source, drive_of_path
from .util import Fail, Log, free_bytes, human_bytes, iso, mounted, now, parse_iso, read_json, show, write_json


@dataclass
class Unit:
    name: str          # what the progress file and the log call it: the folder, or the tag
    source: str
    paths: list
    tag: str = ''
    fallback: str = ''  # if the unit has no snapshot yet: the newest snapshot that holds this path is its parent


@dataclass
class Result:
    rc: int = 0                                    # 0, 3 (some files could not be read), or the exit that stopped it
    done: list = field(default_factory=list)       # the sources backed up
    skipped: dict = field(default_factory=dict)    # source -> why it was not backed up
    units: int = 0
    attribute_errors: int = 0                      # error lines that say only that an extended attribute was unreadable
    other_errors: int = 0                          # the other error lines: files or folders restic could not read
    added_bytes: int = 0                           # what the snapshots added to the repository, before compression


def source_drive(cfg: Config, s: Source):
    if s.drive:
        return cfg.drives[s.drive]
    if s.kind in ('folder', 'home'):
        return drive_of_path(cfg, s.path)
    return None


def source_missing(cfg: Config, s: Source) -> str:
    """Why SOURCE cannot be backed up now ('' if it can): its drive is not mounted, or its folder is not there."""
    d = source_drive(cfg, s)
    if d and not mounted(d.mount):
        return f'{d.mount} is not mounted'
    if s.kind in ('folder', 'home') and not os.path.isdir(s.path):
        return f'{s.path} is not a folder'
    if s.kind == 'paths':
        if not s.drive:
            for p in s.paths:
                pd = drive_of_path(cfg, p)
                if pd and not mounted(pd.mount):
                    return f'{pd.mount} is not mounted'
        if not any(os.path.lexists(p) for p in s.paths):
            return 'none of its paths exist'
    return ''


def exclude_args(repo: Repository, build_file: str) -> list:
    args = []
    for f in repo.exclude_files:
        args += ['--exclude-file', f]
    for f in repo.iexclude_files:
        args += ['--iexclude-file', f]
    for p in repo.exclude:
        args += ['--exclude', p]
    for p in repo.iexclude:
        args += ['--iexclude', p]
    if build_file:
        args += ['--exclude-file', build_file]
    return args


def excludes_of(repo: Repository, build_file: str) -> patterns.Excludes:
    exact = list(repo.exclude)
    folded = list(repo.iexclude)
    for f in repo.exclude_files + ([build_file] if build_file else []):
        exact += patterns.read_file(f)
    for f in repo.iexclude_files:
        folded += patterns.read_file(f)
    return patterns.Excludes(exact, folded)


def folder_units(s: Source, ex: patterns.Excludes, log: Log) -> list:
    root = s.path
    dev = os.stat(root).st_dev
    split = {os.path.join(root, p.rstrip('/')) for p in s.split}
    units, loose = [], []

    def walk(folder):
        found = False
        try:
            names = sorted(os.listdir(folder))
        except OSError as e:
            raise Fail(f'cannot read {show(folder)}: {e.strerror}')
        for n in names:
            p = os.path.join(folder, n)
            try:
                st = os.lstat(p)
            except FileNotFoundError:
                continue
            if st.st_dev != dev:
                continue   # another file system: --one-file-system would leave it out of a backup of the whole folder
            if ex.wholly(p):
                continue
            found = True
            if os.path.islink(p) or not os.path.isdir(p):
                loose.append(p)
            elif p in split:
                walk(p)
            else:
                units.append(Unit(p, s.name, [p], fallback=root))
        if not found and folder != root:
            units.append(Unit(folder, s.name, [folder], fallback=root))

    walk(root)
    if loose:
        units.append(Unit(s.tag, s.name, loose, tag=s.tag, fallback=root))
    return units


def home_paths(s: Source) -> list:
    out = []
    for n in sorted(os.listdir(s.path)):
        p = os.path.join(s.path, n)
        if n in s.skip:
            continue
        if n.startswith('.') and os.path.isdir(p) and not os.path.islink(p):
            continue
        out.append(p)
    for a in s.add:
        p = os.path.join(s.path, a)
        if os.path.lexists(p) and p not in out:
            out.append(p)
    return out


def units_of(cfg: Config, repo: Repository, sources: list, ex: patterns.Excludes, log: Log) -> list:
    units = []
    for s in sources:
        if s.kind == 'folder':
            units += folder_units(s, ex, log)
        elif s.kind == 'home':
            units.append(Unit(s.tag, s.name, home_paths(s), tag=s.tag))
        else:
            there = [p for p in s.paths if os.path.lexists(p)]
            for p in s.paths:
                if p not in there:
                    log(f'{s.name}: {show(p)} does not exist; left out')
            if there:
                units.append(Unit(s.tag or s.name, s.name, there, tag=s.tag))
    return units


def snapshot_time(s: dict) -> datetime.datetime:
    t = s['time']
    # restic writes nanoseconds; Python reads at most microseconds
    m = re.fullmatch(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?', t)
    if not m:
        raise Fail(f'cannot read the snapshot time {t!r}')
    head, digits, zone = m.groups()
    frac = f'.{(digits + "000000")[:6]}' if digits else ''
    zone = '+00:00' if zone in (None, 'Z') else zone
    return datetime.datetime.fromisoformat(head + frac + zone)


def parent_of(snaps: list, u: Unit) -> str:
    if u.tag:
        mine = [s for s in snaps if u.tag in (s.get('tags') or [])]
    else:
        mine = [s for s in snaps if sorted(s.get('paths') or []) == sorted(u.paths)]
    if not mine and u.fallback:
        mine = [s for s in snaps if u.fallback in (s.get('paths') or [])]
    return max(mine, key=snapshot_time)['id'] if mine else ''


def local_folder(repository: str) -> str:
    """The folder of a repository on a disk, or '' for one on a server (b2:, s3:, sftp:, rest:, rclone:)."""
    return repository if repository.startswith('/') else ''


def backup(cfg: Config, name: str, log: Log, continuing: bool = False, extra: list | None = None,
           nice: int = 0, sources: list | None = None) -> Result:
    """Backs up repository NAME. CONTINUING skips the units that the last run finished, and reuses its list of build
    outputs: use it only when the files did not change much since that run. SOURCES limits it to those source names."""
    repo = cfg.repositories[name]
    restic.check_ready(cfg, repo)
    extra = extra or []
    dry = any(a in ('--dry-run', '-n') for a in extra)
    res = Result()
    present = []
    for s in repo.sources:
        if sources and s.name not in sources:
            continue
        why = source_missing(cfg, s)
        if why:
            res.skipped[s.name] = why
            log(f'{name}: {s.name} skipped: {why}')
        else:
            present.append(s)
    if not present:
        res.rc = 0 if res.skipped else 1
        if not res.skipped:
            log(f'{name}: no sources')
        return res

    state_dir = os.path.join(cfg.state_dir, 'backups')
    progress_path = os.path.join(state_dir, f'{name}-progress.json')
    progress = read_json(progress_path, {}) if continuing else {}
    finished = set(progress.get('finished', []))

    build_file = ''
    if repo.build_outputs:
        build_file = os.path.join(state_dir, f'{name}-build-outputs.exclude')
        if continuing and os.path.isfile(build_file):
            log(f'{name}: using the list of build outputs of the last run')
        else:
            roots = []
            for s in present:
                roots += [s.path] if s.kind == 'folder' else home_paths(s) if s.kind == 'home' else s.paths
            try:
                build_outputs.write(build_file, roots, log)
            except Exception as e:   # the backup goes on without the list: it then keeps more, not less
                log(f'{name}: the list of build outputs could not be made ({e}); this backup keeps them')
                build_file = ''

    ex = excludes_of(repo, build_file)
    units = units_of(cfg, repo, present, ex, log)
    res.units = len(units)
    snaps = restic.snapshots(cfg, repo)
    if not dry and not continuing:
        progress = {'started': iso(), 'finished': []}
        write_json(progress_path, progress)
    base = ['backup', '--one-file-system', '--exclude-caches'] + exclude_args(repo, build_file)
    folder = local_folder(repo.repository)
    for i, u in enumerate(units, 1):
        head = f'{name}: {i} of {len(units)}: {show(u.name)}'
        if u.name in finished:
            log(f'{head}: finished by the last run')
            continue
        paths = [p for p in u.paths if os.path.lexists(p)]
        if not paths:
            log(f'{head}: gone')
            continue
        if repo.min_free_bytes and folder:
            free = free_bytes(folder)
            if free < repo.min_free_bytes:
                raise Fail(f'{name}: only {human_bytes(free)} free where it is, under {human_bytes(repo.min_free_bytes)}; '
                           f'backup stopped', 75)
        log(head)
        args = list(base)
        if u.tag:
            args += ['--tag', u.tag]
        parent = parent_of(snaps, u)
        if parent:
            args += ['--parent', parent]
        errors, added = [], []
        rc = restic.run(cfg, repo, args + repo.backup_args + extra + ['--'] + paths, log, nice=nice, errors=errors,
                        added=added)
        res.added_bytes += sum(added)
        attributes = sum(1 for e in errors if restic.attribute_error(e))
        res.attribute_errors += attributes
        res.other_errors += len(errors) - attributes
        if rc not in (0, 3):
            res.rc = rc
            log(f'{name}: restic stopped at {show(u.name)} with exit {rc}; the next run continues from here')
            break
        if rc == 3:
            res.rc = 3
        if not dry:
            progress.setdefault('finished', []).append(u.name)
            write_json(progress_path, progress)
    if res.rc in (0, 3):
        res.done = [s.name for s in present]
        if not dry:
            write_json(progress_path, {'started': progress.get('started', iso()), 'finished': [], 'completed': iso()})
    return res


def can_continue(cfg: Config, name: str, within_hours: float) -> bool:
    """True if the last backup of NAME stopped before its end less than WITHIN_HOURS ago."""
    p = read_json(os.path.join(cfg.state_dir, 'backups', f'{name}-progress.json'), {})
    started = parse_iso(p.get('started'))
    return bool(p.get('finished')) and not p.get('completed') and started is not None and \
        (now() - started).total_seconds() < within_hours * 3600
