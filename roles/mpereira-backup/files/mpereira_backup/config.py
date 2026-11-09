"""The settings file: every path, drive, repository, schedule, and number that belongs to one person. The code holds none.
See config.example.json and the README for each field."""
from __future__ import annotations

import datetime
import json
import os
import re
from dataclasses import dataclass, field

from .util import Fail

DEFAULT_PATH = '~/.config/mpereira-backup/config.json'
PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE_DIR = os.path.join(PACKAGE_DIR, 'share')
JOB_KINDS = ('backup', 'check', 'health', 'copy-media')
DUE_KEYS = ('every_hours', 'every_days', 'daily_at', 'monthly_on')
RETENTION_KEYS = ('keep-last', 'keep-hourly', 'keep-daily', 'keep-weekly', 'keep-monthly', 'keep-yearly', 'keep-within',
                  'keep-tag')


@dataclass
class Drive:
    name: str
    mount: str
    kind: str          # "zfs" (exported on eject) or "disk" (ejected with diskutil)
    pool: str = ''     # the ZFS pool, for "zfs"
    eject: list = field(default_factory=list)   # the command that ejects it; empty: zpool export POOL, or diskutil eject


@dataclass
class Source:
    name: str
    kind: str                      # "folder", "paths", or "home"
    path: str = ''                 # folder: the folder; home: the home folder
    paths: list = field(default_factory=list)
    split: list = field(default_factory=list)
    tag: str = ''
    skip: list = field(default_factory=list)
    add: list = field(default_factory=list)
    drive: str = ''
    remind_after_days: float = 0


@dataclass
class Repository:
    name: str
    repository: str
    password_file: str
    env_file: str = ''
    drive: str = ''
    cache_dir: str = ''
    min_free_bytes: int = 0
    max_added_bytes: int = 0
    sources: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    iexclude: list = field(default_factory=list)
    exclude_files: list = field(default_factory=list)
    iexclude_files: list = field(default_factory=list)
    build_outputs: bool = False
    backup_args: list = field(default_factory=list)
    retention: dict = field(default_factory=dict)
    group_by: str = ''
    forget_after_backup: bool = False
    prune_every_days: float = 0
    prune_args: list = field(default_factory=list)


@dataclass
class Job:
    name: str
    kind: str
    due: dict
    repository: str = ''
    repositories: list = field(default_factory=list)
    read_data_subset: bool = False
    read_new: bool = False     # after each check that passes, read back what a local repository gained since the last
    nice: int = 0
    notify_success: str = ''   # a notification after each run that passes, such as "You can eject the drive"


@dataclass
class Media:
    home: str
    dest: str
    drive: str
    folders: list
    skip: list
    bundles: list
    derived_names: list
    derived_paths: list
    skip_files: list
    since_ns: int = 0   # the first run copies only what changed after this time; later runs start from their stamp


@dataclass
class Quarantine:
    journals: str                  # the folder of the reorganization journals, one BATCH.tsv for each batch
    root: str                      # the quarantine folder: only files under it are checked and purged
    locations: dict                # keeper location (the journal's keeper_location) -> the repositories that must hold it
    min_age_days: float = 60
    check_max_age_days: float = 7
    read_back: dict = field(default_factory=dict)   # repository -> the share of matching keepers read back (0 to 1)
    max_reads: int = 2000


@dataclass
class Dedupe:
    root: str                      # the folder whose duplicates are moved; the quarantine keeps each path under it
    location: str                  # the keeper_location written to the journal: a key of quarantine.locations
    prefer: list                   # folders under root, the first holds the keeper of choice
    replaceable: list              # folders under root whose content no backup must hold: their keepers are class C
    stay: list                     # compiled patterns of paths under root that never move (they can be keepers)
    skip: list                     # folders under root that are never searched
    min_size: int = 1 << 20
    fclones: str = '/opt/homebrew/bin/fclones'
    within_tier: bool = False      # also move a copy whose keeper is in the same "prefer" folder


@dataclass
class Config:
    path: str
    home: str
    state_dir: str
    restic: str
    b3sum: str
    notify: list
    pause_files: list
    drives: dict
    repositories: dict
    jobs: dict
    media: Media | None
    trash_requires: list
    env_path: str
    lock_file: str
    zpool: str
    quarantine: Quarantine | None = None
    dedupe: Dedupe | None = None


def _expand(p: str, home: str) -> str:
    if p == '~':
        return home
    if p.startswith('~/'):
        return os.path.join(home, p[2:])
    return p


def _since(m: dict, where: str) -> int:
    """copy_media.since, an ISO 8601 time with its zone, in nanoseconds since the epoch; 0 if not set."""
    if 'since' not in m:
        return 0
    try:
        t = datetime.datetime.fromisoformat(str(m['since']))
    except ValueError:
        t = None
    if t is None or t.tzinfo is None:
        raise Fail(f'{where}: "since" must be an ISO 8601 time with its zone, such as 2026-11-07T18:00:00+01:00', 78)
    return int(t.timestamp()) * 1_000_000_000 + t.microsecond * 1000


def _take(d: dict, where: str, known: tuple) -> None:
    unknown = sorted(set(d) - set(known))
    if unknown:
        raise Fail(f'{where}: unknown settings {", ".join(unknown)}', 78)


def _need(d: dict, key: str, where: str, kind: type = str):
    if key not in d:
        raise Fail(f'{where}: "{key}" is missing', 78)
    if not isinstance(d[key], kind):
        raise Fail(f'{where}: "{key}" must be a {kind.__name__}', 78)
    return d[key]


def _list(d: dict, key: str, where: str) -> list:
    v = d.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise Fail(f'{where}: "{key}" must be a list of strings', 78)
    return v


def _due(d, where: str) -> dict:
    if not isinstance(d, dict) or len(d) != 1 or next(iter(d)) not in DUE_KEYS:
        raise Fail(f'{where}: "due" must have one of {", ".join(DUE_KEYS)}', 78)
    if 'daily_at' in d and not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', str(d['daily_at'])):
        raise Fail(f'{where}: "daily_at" must be HH:MM', 78)
    if 'monthly_on' in d and d['monthly_on'] not in range(1, 29):
        raise Fail(f'{where}: "monthly_on" must be a day from 1 to 28', 78)
    for k in ('every_hours', 'every_days'):
        if k in d and (not isinstance(d[k], (int, float)) or d[k] <= 0):
            raise Fail(f'{where}: "{k}" must be a number above 0', 78)
    return d


def load(path: str | None = None) -> Config:
    path = os.path.expanduser(path or os.environ.get('MPEREIRA_BACKUP_CONFIG') or DEFAULT_PATH)
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise Fail(f'no settings file at {path}: copy config.example.json there, or pass --config', 78)
    except ValueError as e:
        raise Fail(f'{path}: not valid JSON: {e}', 78)
    return parse(raw, path)


def parse(raw: dict, path: str = '<settings>') -> Config:
    _take(raw, path, ('home', 'state_dir', 'restic', 'b3sum', 'zpool', 'notify', 'pause_files', 'lock_file',
                      'drives', 'repositories', 'jobs', 'copy_media', 'trash_requires', 'quarantine', 'dedupe', 'path',
                      '//'))
    home = raw.get('home') or os.path.expanduser('~')
    x = lambda p: _expand(p, home)   # noqa: E731

    drives = {}
    for name, d in _need(raw, 'drives', path, dict).items():
        w = f'drives.{name}'
        _take(d, w, ('mount', 'kind', 'pool', 'eject', '//'))
        kind = d.get('kind', 'disk')
        if kind not in ('zfs', 'disk'):
            raise Fail(f'{w}: "kind" must be "zfs" or "disk"', 78)
        if kind == 'zfs' and not d.get('pool'):
            raise Fail(f'{w}: a "zfs" drive needs "pool"', 78)
        drives[name] = Drive(name, _need(d, 'mount', w).rstrip('/'), kind, d.get('pool', ''), _list(d, 'eject', w))

    def drive_ref(d: dict, w: str) -> str:
        v = d.get('drive', '')
        if v and v not in drives:
            raise Fail(f'{w}: no drive named "{v}"', 78)
        return v

    repos = {}
    for name, r in _need(raw, 'repositories', path, dict).items():
        w = f'repositories.{name}'
        _take(r, w, tuple(Repository.__dataclass_fields__) + ('//',))
        sources = []
        for i, s in enumerate(r.get('sources', [])):
            sw = f'{w}.sources[{i}]'
            _take(s, sw, tuple(Source.__dataclass_fields__) + ('//',))
            kind = _need(s, 'kind', sw)
            src = Source(name=_need(s, 'name', sw), kind=kind, drive=drive_ref(s, sw),
                         remind_after_days=float(s.get('remind_after_days', 0)), tag=s.get('tag', ''))
            if kind == 'folder':
                src.path = x(_need(s, 'path', sw)).rstrip('/')
                src.split = _list(s, 'split', sw)
                src.tag = src.tag or src.name + '-top-files'
            elif kind == 'paths':
                src.paths = [x(p) for p in _list(s, 'paths', sw)]
                if not src.paths:
                    raise Fail(f'{sw}: "paths" is empty', 78)
            elif kind == 'home':
                src.path = x(s.get('path', '~')).rstrip('/')
                src.skip = _list(s, 'skip', sw)
                src.add = _list(s, 'add', sw)
                src.tag = src.tag or src.name
            else:
                raise Fail(f'{sw}: "kind" must be "folder", "paths", or "home"', 78)
            sources.append(src)
        names = [s.name for s in sources]
        if len(set(names)) != len(names):
            raise Fail(f'{w}: two sources have the same name', 78)
        retention = r.get('retention', {})
        _take(retention, f'{w}.retention', RETENTION_KEYS)
        repos[name] = Repository(
            name=name, repository=x(_need(r, 'repository', w)), password_file=x(_need(r, 'password_file', w)),
            env_file=x(r.get('env_file', '')), drive=drive_ref(r, w), cache_dir=x(r.get('cache_dir', '')),
            min_free_bytes=int(r.get('min_free_bytes', 0)), max_added_bytes=int(r.get('max_added_bytes', 0)),
            sources=sources,
            exclude=[x(p) if p.startswith('~/') else p for p in _list(r, 'exclude', w)],
            iexclude=[x(p) if p.startswith('~/') else p for p in _list(r, 'iexclude', w)],
            exclude_files=[_share(x(p)) for p in _list(r, 'exclude_files', w)],
            iexclude_files=[_share(x(p)) for p in _list(r, 'iexclude_files', w)],
            build_outputs=bool(r.get('build_outputs', False)), backup_args=_list(r, 'backup_args', w),
            retention=retention, group_by=r.get('group_by', ''),
            forget_after_backup=bool(r.get('forget_after_backup', False)),
            prune_every_days=float(r.get('prune_every_days', 0)), prune_args=_list(r, 'prune_args', w))

    jobs = {}
    for name, j in raw.get('jobs', {}).items():
        w = f'jobs.{name}'
        _take(j, w, ('kind', 'due', 'repository', 'repositories', 'read_data_subset', 'read_new', 'nice',
                     'notify_success', '//'))
        kind = _need(j, 'kind', w)
        if kind not in JOB_KINDS:
            raise Fail(f'{w}: "kind" must be one of {", ".join(JOB_KINDS)}', 78)
        job = Job(name=name, kind=kind, due=_due(j.get('due'), w), repository=j.get('repository', ''),
                  repositories=_list(j, 'repositories', w) or list(repos), read_data_subset=bool(j.get('read_data_subset')),
                  read_new=bool(j.get('read_new')),
                  nice=int(j.get('nice', 0)), notify_success=j.get('notify_success', ''))
        if kind == 'backup' and job.repository not in repos:
            raise Fail(f'{w}: a backup job needs "repository", one of {", ".join(repos)}', 78)
        for rn in job.repositories:
            if rn not in repos:
                raise Fail(f'{w}: no repository named "{rn}"', 78)
        jobs[name] = job

    media = None
    if raw.get('copy_media'):
        m = raw['copy_media']
        w = 'copy_media'
        _take(m, w, ('home', 'dest', 'drive', 'folders', 'skip', 'bundles', 'derived_names', 'derived_paths',
                     'skip_files', 'since', '//'))
        media = Media(home=x(m.get('home', '~')).rstrip('/'), dest=x(_need(m, 'dest', w)).rstrip('/'),
                      drive=drive_ref(m, w), folders=_list(m, 'folders', w), skip=_list(m, 'skip', w),
                      bundles=_list(m, 'bundles', w), derived_names=_list(m, 'derived_names', w),
                      derived_paths=_list(m, 'derived_paths', w),
                      skip_files=_list(m, 'skip_files', w) or ['.DS_Store', '.localized'],
                      since_ns=_since(m, w))
        if not media.folders:
            raise Fail(f'{w}: "folders" is empty', 78)
    for j in jobs.values():
        if j.kind == 'copy-media' and not media:
            raise Fail(f'jobs.{j.name}: a copy-media job needs the "copy_media" settings', 78)

    trash_requires = _list(raw, 'trash_requires', path)
    for rn in trash_requires:
        if rn not in repos:
            raise Fail(f'trash_requires: no repository named "{rn}"', 78)

    quarantine = None
    if raw.get('quarantine'):
        q = raw['quarantine']
        w = 'quarantine'
        _take(q, w, tuple(Quarantine.__dataclass_fields__) + ('//',))
        locations = _need(q, 'locations', w, dict)
        for loc, rns in locations.items():
            if not isinstance(rns, list) or any(rn not in repos for rn in rns):
                raise Fail(f'{w}.locations.{loc}: must be a list of repository names', 78)
        read_back = q.get('read_back', {})
        if not isinstance(read_back, dict) or any(rn not in repos or not isinstance(v, (int, float)) or not 0 <= v <= 1
                                                  for rn, v in read_back.items()):
            raise Fail(f'{w}.read_back: must map repository names to numbers from 0 to 1', 78)
        quarantine = Quarantine(journals=x(_need(q, 'journals', w)).rstrip('/'), root=x(_need(q, 'root', w)).rstrip('/'),
                                locations=locations, min_age_days=float(q.get('min_age_days', 60)),
                                check_max_age_days=float(q.get('check_max_age_days', 7)), read_back=read_back,
                                max_reads=int(q.get('max_reads', 2000)))

    dedupe = None
    if raw.get('dedupe'):
        d = raw['dedupe']
        w = 'dedupe'
        _take(d, w, tuple(Dedupe.__dataclass_fields__) + ('//',))
        if not quarantine:
            raise Fail(f'{w}: needs the "quarantine" settings, for the journals and the quarantine folder', 78)
        location = _need(d, 'location', w)
        if location not in quarantine.locations:
            raise Fail(f'{w}.location: "{location}" is not one of quarantine.locations', 78)
        rels = {}
        for key in ('prefer', 'replaceable', 'skip'):
            rels[key] = [r.strip('/') for r in _list(d, key, w)]
            if any(r in ('', '.', '..') or r.startswith('../') or '/../' in r for r in rels[key]):
                raise Fail(f'{w}.{key}: each must be a folder under "root", without .. or a leading /', 78)
        stay = []
        for pat in _list(d, 'stay', w):
            try:
                stay.append(re.compile(pat))
            except re.error as e:
                raise Fail(f'{w}.stay: not a regular expression: {pat!r}: {e}', 78)
        min_size = d.get('min_size', 1 << 20)
        if not isinstance(min_size, int) or min_size < 1:
            raise Fail(f'{w}.min_size: must be a whole number of bytes, 1 or more', 78)
        dedupe = Dedupe(root=x(_need(d, 'root', w)).rstrip('/'), location=location, prefer=rels['prefer'],
                        replaceable=rels['replaceable'], stay=stay, skip=rels['skip'], min_size=min_size,
                        fclones=d.get('fclones', '/opt/homebrew/bin/fclones'), within_tier=d.get('within_tier', False))
        if not isinstance(dedupe.within_tier, bool):
            raise Fail(f'{w}.within_tier: must be true or false', 78)

    notify = raw.get('notify', ['/opt/homebrew/bin/terminal-notifier', '-title', '{title}', '-message', '{message}'])
    state_dir = x(raw.get('state_dir', '~/.local/state/mpereira-backup'))
    return Config(
        path=path, home=home, state_dir=state_dir, lock_file=x(raw.get('lock_file', os.path.join(state_dir, 'lock'))),
        zpool=raw.get('zpool', '/usr/local/zfs/bin/zpool'),
        restic=raw.get('restic', '/opt/homebrew/bin/restic'), b3sum=raw.get('b3sum', '/opt/homebrew/bin/b3sum'),
        notify=notify,
        pause_files=[x(p) for p in _list(raw, 'pause_files', path)], drives=drives, repositories=repos, jobs=jobs,
        media=media, trash_requires=trash_requires, quarantine=quarantine, dedupe=dedupe,
        env_path=raw.get('path', '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'))


def _share(p: str) -> str:
    """A file named without a folder is one of the files that come with the tool (share/), such as rebuildable.exclude."""
    return p if os.sep in p else os.path.join(SHARE_DIR, p)


def drive_of_path(cfg: Config, path: str) -> Drive | None:
    """The drive whose mount holds PATH, if any."""
    for d in cfg.drives.values():
        if path == d.mount or path.startswith(d.mount + '/'):
            return d
    return None
