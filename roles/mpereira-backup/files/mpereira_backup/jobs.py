"""The scheduled side: which jobs are due, the hourly run, the state of each job and source, the reminders, pause and
resume, status, and eject.

launchd starts `mpereira-backup run` every hour and when a drive mounts. Each run goes through the jobs by kind (backups,
then copy-media, then checks, then health, so health reports on this run's results), in the order of the settings
within a kind, and runs those that are due and whose drives are mounted. A job whose drive is away is skipped, not
failed. One notification goes out when a job starts failing, and one when it passes again; the daily health job
reminds about sources that have had no backup for longer than their "remind_after_days".
"""
from __future__ import annotations

import calendar
import datetime
import os
import subprocess

from . import restic, units, verify
from .units import local_folder
from .config import Config, Job
from .util import Fail, Lock, Log, full_disk_access, human_age, human_bytes, iso, mounted, notify, now, parse_iso, read_json, \
    write_json

SLACK = datetime.timedelta(minutes=10)   # launchd's hourly start drifts; a job due "every hour" runs at each start
CONTINUE_WITHIN_HOURS = 24               # a backup that stopped less than this ago continues where it stopped
TITLE = 'mpereira-backup'


class State:
    """STATE_DIR/state.json. Written only while the lock is held."""

    def __init__(self, cfg: Config):
        self.path = os.path.join(cfg.state_dir, 'state.json')
        self.data = read_json(self.path, {})
        for k in ('jobs', 'sources', 'repos'):
            self.data.setdefault(k, {})

    def job(self, name: str) -> dict:
        return self.data['jobs'].setdefault(name, {})

    def source(self, repo: str, source: str) -> dict:
        s = self.data['sources'].setdefault(f'{repo}/{source}', {})
        s.setdefault('first_seen', iso())
        return s

    def repo(self, name: str) -> dict:
        return self.data['repos'].setdefault(name, {})

    def save(self) -> None:
        write_json(self.path, self.data)


# --- when a job is due ---

def boundary(due: dict, t: datetime.datetime) -> datetime.datetime | None:
    """For a job due at a time of day or a day of the month: the last such time at or before T."""
    if 'daily_at' in due:
        h, m = map(int, due['daily_at'].split(':'))
        b = t.replace(hour=h, minute=m, second=0, microsecond=0)
        return b if b <= t else b - datetime.timedelta(days=1)
    if 'monthly_on' in due:
        d = due['monthly_on']
        b = t.replace(day=d, hour=0, minute=0, second=0, microsecond=0)
        if b <= t:
            return b
        year, month = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
        return b.replace(year=year, month=month, day=min(d, calendar.monthrange(year, month)[1]))
    return None


def is_due(job: Job, last: datetime.datetime | None, t: datetime.datetime) -> bool:
    if last is None:
        return True
    due = job.due
    if 'every_hours' in due:
        return t - last >= datetime.timedelta(hours=due['every_hours']) - SLACK
    if 'every_days' in due:
        return t - last >= datetime.timedelta(days=due['every_days']) - SLACK
    b = boundary(due, t)
    return b is not None and last < b


def next_due(job: Job, last: datetime.datetime | None, t: datetime.datetime) -> datetime.datetime:
    if last is None:
        return t
    due = job.due
    if 'every_hours' in due:
        return last + datetime.timedelta(hours=due['every_hours'])
    if 'every_days' in due:
        return last + datetime.timedelta(days=due['every_days'])
    b = boundary(due, t)
    if b is not None and last < b:
        return b
    if 'daily_at' in due:
        return (b or t) + datetime.timedelta(days=1)
    d = due['monthly_on']
    year, month = (t.year, t.month + 1) if t.month < 12 else (t.year + 1, 1)
    return t.replace(year=year, month=month, day=d, hour=0, minute=0, second=0, microsecond=0)


# --- pause ---

def own_pause_file(cfg: Config) -> str:
    return os.path.join(cfg.state_dir, 'PAUSED')


def paused(cfg: Config) -> str:
    """Why the scheduled runs are paused, or '' if they are not."""
    for p in [own_pause_file(cfg)] + cfg.pause_files:
        if os.path.lexists(p):
            try:
                with open(p, errors='replace') as f:
                    why = f.read().strip().replace('\t', ' ')
            except OSError:
                why = ''
            return f'{p}: {why}' if why else p
    return ''


def pause(cfg: Config, reason: str) -> None:
    from .util import write_atomic
    write_atomic(own_pause_file(cfg), f'{iso()} {reason}\n')


def resume(cfg: Config) -> bool:
    try:
        os.remove(own_pause_file(cfg))
        return True
    except FileNotFoundError:
        return False


# --- the jobs ---

class Skip(Exception):
    """The job cannot run now, and that is not a failure: its drive is away."""


def drive_away(cfg: Config, name: str) -> str:
    d = cfg.drives[name]
    return '' if mounted(d.mount) else f'{d.mount} is not mounted'


def run_backup(cfg: Config, job: Job, st: State, log: Log) -> str:
    repo = cfg.repositories[job.repository]
    if repo.drive and drive_away(cfg, repo.drive):
        raise Skip(f'{repo.name} is on {repo.drive}: {drive_away(cfg, repo.drive)}')
    again = units.can_continue(cfg, repo.name, CONTINUE_WITHIN_HOURS)
    if again:
        log(f'{repo.name}: the last backup stopped before its end; this one continues from there')
    res = units.backup(cfg, repo.name, log, continuing=again, nice=job.nice)
    for s in res.done:
        st.source(repo.name, s)['last_backup'] = iso()
    if not res.done and res.skipped and res.rc == 0:
        raise Skip('; '.join(f'{k}: {v}' for k, v in res.skipped.items()))
    if res.rc not in (0, 3):
        raise Fail(f'restic exited with {res.rc}; see the log', res.rc)
    message = f'{len(res.done)} sources backed up in {res.units} snapshots'
    if res.skipped:
        message += '; skipped: ' + '; '.join(f'{k} ({v})' for k, v in res.skipped.items())
    if repo.forget_after_backup:
        rc = restic.run(cfg, repo, restic.forget_args(repo, dry_run=False), log, nice=job.nice)
        if rc != 0:
            raise Fail(f'forget exited with {rc}; see the log', rc)
        r = st.repo(repo.name)
        last = parse_iso(r.get('last_prune'))
        if repo.prune_every_days and (last is None or now() - last >= datetime.timedelta(days=repo.prune_every_days)):
            rc = restic.run(cfg, repo, ['prune'] + repo.prune_args, log, nice=job.nice)
            if rc != 0:
                raise Fail(f'prune exited with {rc}; see the log', rc)
            r['last_prune'] = iso()
    if res.rc == 3 and not (res.attribute_errors and not res.other_errors):
        raise Fail(f'{message}, but restic could not read some files (exit 3); see the log', 3)
    warnings = []
    if res.rc == 3:
        # restic backed up every file; only some extended attributes are missing from the snapshots
        warnings.append(f'restic could not read the extended attributes of {res.attribute_errors} files; see the log')
    if repo.max_added_bytes and res.added_bytes > repo.max_added_bytes:
        # most often a new cache or build folder that no exclude covers yet
        grew = (f'this backup added {human_bytes(res.added_bytes)} to {repo.name}, over its limit of '
                f'{human_bytes(repo.max_added_bytes)}; look in the log for what it added, and exclude what can be '
                f'rebuilt')
        warnings.append(grew)
        notify(cfg.notify, f'{TITLE}: {repo.name} grew', grew[0].upper() + grew[1:] + '.', log)
    for w in warnings:
        log(f'{repo.name}: warning: {w}')
    return '; '.join([message] + [f'warning: {w}' for w in warnings])


def run_check(cfg: Config, job: Job, st: State, log: Log) -> str:
    done, failed, away = [], [], []
    for name in job.repositories:
        repo = cfg.repositories[name]
        if not restic.drive_ready(cfg, repo):
            away.append(name)
            log(f'{name}: skipped, {cfg.drives[repo.drive].mount} is not mounted')
            continue
        args = ['check']
        if job.read_data_subset:
            args += ['--read-data-subset', f'{now().month}/12']
        rc = restic.run(cfg, repo, args, log, nice=job.nice)
        if rc == 0:
            st.repo(name)['last_check'] = iso()
        if rc == 0 and job.read_new and local_folder(repo.repository):
            rc = read_new_since_last(cfg, repo, st, log)
        (done if rc == 0 else failed).append(name)
    if failed:
        raise Fail(f'check failed for {", ".join(failed)}; see the log')
    if not done:
        raise Skip('no repository is reachable: ' + ', '.join(away))
    return f'checked {", ".join(done)}' + (f'; away: {", ".join(away)}' if away else '')


def read_new_since_last(cfg: Config, repo, st: State, log: Log) -> int:
    """Reads back what a local repository gained since the last read-back that passed. The first time, it only
    records the time: "check-data" (--read-data-subset) covers what was there before."""
    r = st.repo(repo.name)
    start = now()
    since = parse_iso(r.get('read_new_since'))
    if not since:
        r['read_new_since'] = iso(start)
        log(f'{repo.name}: from now on, each check reads back what the repository gained since the last one')
        return 0
    _, _, bad = verify.read_new(cfg, repo, since, log)
    if bad:
        return 1
    r['read_new_since'] = iso(start)
    return 0


def reminders(cfg: Config, st: State) -> list:
    """(source key, days without a backup, the limit) for each source past its limit."""
    out = []
    t = now()
    for repo in cfg.repositories.values():
        for s in repo.sources:
            if not s.remind_after_days:
                continue
            rec = st.source(repo.name, s.name)
            last = parse_iso(rec.get('last_backup')) or parse_iso(rec['first_seen']) or t
            days = (t - last).total_seconds() / 86400
            if days > s.remind_after_days:
                out.append((repo.name, s.name, days, s.remind_after_days, rec.get('last_backup')))
    return out


def run_health(cfg: Config, job: Job, st: State, log: Log) -> str:
    late = reminders(cfg, st)
    for repo, source, days, limit, last in late:
        what = f'No backup of {source} in {repo} for {days:.0f} days' if last else \
            f'No backup of {source} in {repo} since this tool started {days:.0f} days ago'
        log(what)
        notify(cfg.notify, f'{TITLE}: backup overdue', f'{what} (the limit is {limit:g}).', log)
    why = paused(cfg)
    if why:
        log(f'backups are paused: {why}')
        notify(cfg.notify, f'{TITLE}: backups paused', f'Scheduled backups are paused ({why}). Run mpereira-backup resume.',
               log)
    no_access = st.data.get('full_disk_access') is False
    if no_access:
        log('the scheduled runs do not have Full Disk Access')
        notify(cfg.notify, f'{TITLE}: no Full Disk Access',
               'The scheduled backups cannot read some folders. Give Full Disk Access to the launcher (see the README).', log)
    failing = [(n, j) for n, j in st.data['jobs'].items() if j.get('failing_since')]
    for n, j in failing:
        log(f'{n} has failed since {j["failing_since"]}: {j.get("message", "")}')
    return f'{len(late)} overdue, {len(failing)} failing' + (', paused' if why else '') + \
        (', no Full Disk Access' if no_access else '')


def run_copy_media(cfg: Config, job: Job, st: State, log: Log) -> str:
    from . import transfer
    m = cfg.media
    assert m
    if m.drive and drive_away(cfg, m.drive):
        raise Skip(drive_away(cfg, m.drive))
    return transfer.copy_media(cfg, log)


KINDS = {'backup': run_backup, 'check': run_check, 'health': run_health, 'copy-media': run_copy_media}


def record(cfg: Config, st: State, job: Job, ok: bool, message: str, log: Log) -> None:
    j = st.job(job.name)
    j['last_attempt'] = iso()
    j['message'] = message
    if ok:
        j['last_success'] = iso()
        j['result'] = 'ok'
        if j.get('failure_notified'):
            notify(cfg.notify, f'{TITLE}: {job.name} passed', f'{job.name} passes again: {message}', log)
        j.pop('failing_since', None)
        j.pop('failure_notified', None)
        if job.notify_success:
            notify(cfg.notify, f'{TITLE}: {job.name}', job.notify_success, log)
    else:
        j['result'] = 'failed'
        j.setdefault('failing_since', iso())
        # one notification for each failure; one that did not go out is tried again at the next failed run
        if not j.get('failure_notified') and notify(cfg.notify, f'{TITLE}: {job.name} failed', message, log):
            j['failure_notified'] = iso()


def run_job(cfg: Config, job: Job, st: State, runlog: Log) -> str:
    """Runs one job now, records it, and returns 'ok', 'failed', or 'skipped'."""
    log = Log(os.path.join(cfg.state_dir, 'logs'), job.name)
    log(f'--- {job.name} ({job.kind}) started')
    try:
        message = KINDS[job.kind](cfg, job, st, log)
    except Skip as e:
        log(f'{job.name} skipped: {e}')
        runlog(f'{job.name}: skipped: {e}')
        st.job(job.name).update(last_skip=iso(), skip_reason=str(e))
        st.save()
        return 'skipped'
    except Fail as e:
        log(f'{job.name} failed: {e}')
        runlog(f'{job.name}: failed: {e}')
        record(cfg, st, job, False, str(e), log)
        st.save()
        return 'failed'
    except Exception as e:   # a bug: recorded and notified as a failure, so it does not fail in silence
        log(f'{job.name} failed with an error in mpereira-backup: {e!r}')
        import traceback
        log(traceback.format_exc().rstrip())
        record(cfg, st, job, False, f'error in mpereira-backup: {e!r}', log)
        st.save()
        return 'failed'
    log(f'{job.name} passed: {message}')
    runlog(f'{job.name}: passed: {message}')
    record(cfg, st, job, True, message, log)
    st.save()
    return 'ok'


# The order of a run: the backups first, so a check never holds back a backup; health last, so it sees this run.
# Ansible writes config.json with its keys sorted, so the order of the settings alone is not kept.
KIND_ORDER = {'backup': 0, 'copy-media': 1, 'check': 2, 'health': 3}


def ordered(selected: list) -> list:
    """SELECTED jobs by kind, in the order of the settings within a kind."""
    return sorted(selected, key=lambda job: KIND_ORDER.get(job.kind, len(KIND_ORDER)))


def run(cfg: Config, names: list, force: bool = False) -> int:
    """What launchd starts. Runs the jobs in NAMES (all if empty) that are due, or all of them with FORCE.
    Exit 0 unless a job failed (1)."""
    runlog = Log(os.path.join(cfg.state_dir, 'logs'), 'run')
    for n in names:
        if n not in cfg.jobs:
            raise Fail(f'no job named "{n}"; the jobs are: {", ".join(cfg.jobs)}', 64)
    selected = ordered([cfg.jobs[n] for n in (names or list(cfg.jobs))])
    why = paused(cfg)
    access = full_disk_access()
    failed = 0
    for job in selected:
        if why and job.kind != 'health':
            runlog(f'{job.name}: skipped, paused: {why}')
            continue
        lock = Lock(cfg.lock_file)
        if not lock.acquire(wait=False):
            runlog(f'locked by {lock.holder() or "another process"}; the rest wait for the next run')
            break
        try:
            st = State(cfg)
            if why and not st.data.get('paused_notified'):
                # a notification that did not go out is tried again at the next run
                if notify(cfg.notify, f'{TITLE}: backups paused', f'Scheduled backups are paused ({why}).', runlog):
                    st.data['paused_notified'] = iso()
                    st.save()
            elif not why and st.data.pop('paused_notified', None):
                st.save()
            if access is not None and st.data.get('full_disk_access') is not access:
                runlog(f'Full Disk Access: {"yes" if access else "no"}')
                st.data['full_disk_access'] = access
                st.save()
            last = parse_iso(st.job(job.name).get('last_success'))
            if not force and not is_due(job, last, now()):
                continue
            if run_job(cfg, job, st, runlog) == 'failed':
                failed += 1
        finally:
            lock.release()
    return 1 if failed else 0


# --- status ---

def status(cfg: Config) -> str:
    st = State(cfg)
    t = now()
    lines = []
    why = paused(cfg)
    if why:
        lines += [f'PAUSED: {why}', '']
    lock = Lock(cfg.lock_file)
    if lock.acquire(wait=False):
        lock.release()
    else:
        lines += [f'running: {lock.holder()}', '']
    lines.append('Drives:')
    for d in cfg.drives.values():
        lines.append(f'  {d.name:12} {d.mount:28} {"mounted" if mounted(d.mount) else "not mounted"}')
    lines += ['', 'Sources (last backup, reminder after):']
    for repo in cfg.repositories.values():
        for s in repo.sources:
            rec = st.data['sources'].get(f'{repo.name}/{s.name}', {})
            last = parse_iso(rec.get('last_backup'))
            age = f'{human_age((t - last).total_seconds())} ago' if last else 'never'
            late = ' OVERDUE' if s.remind_after_days and last and (t - last).days >= s.remind_after_days else ''
            limit = f'{s.remind_after_days:g} days' if s.remind_after_days else '-'
            lines.append(f'  {repo.name + "/" + s.name:28} {age:16} {limit:10}{late}')
    lines += ['', 'Jobs (last success, result, next due):']
    for job in cfg.jobs.values():
        j = st.data['jobs'].get(job.name, {})
        last = parse_iso(j.get('last_success'))
        age = f'{human_age((t - last).total_seconds())} ago' if last else 'never'
        nd = next_due(job, last, t)
        due = 'now' if nd <= t else nd.strftime('%Y-%m-%d %H:%M')
        result = j.get('result', '-')
        if j.get('failing_since'):
            result = f'failing since {j["failing_since"][:16]}'
        lines.append(f'  {job.name:14} {age:16} {result:32} {due}')
        if j.get('result') == 'failed' and j.get('message'):
            lines.append(f'  {"":14} {j["message"]}')
    return '\n'.join(lines)


# --- eject ---

def eject(cfg: Config, name: str, log: Log) -> int:
    if name not in cfg.drives:
        raise Fail(f'no drive named "{name}"; the drives are: {", ".join(cfg.drives)}', 64)
    d = cfg.drives[name]
    if not mounted(d.mount):
        log(f'{d.mount} is not mounted; nothing to eject')
        return 0
    lock = Lock(cfg.lock_file)
    if not lock.acquire(wait=False):
        log(f'waiting for the running job to finish: {lock.holder()}')
        lock.acquire(wait=True)
    try:
        argv = d.eject or (['/usr/bin/sudo', cfg.zpool, 'export', d.pool] if d.kind == 'zfs'
                           else ['/usr/sbin/diskutil', 'eject', d.mount])
        log('running: ' + ' '.join(argv))
        rc = subprocess.run(argv).returncode
        if rc != 0 or mounted(d.mount):
            raise Fail(f'{d.mount} is still mounted (exit {rc}); do not unplug it', rc or 1)
        log(f'{d.name} is ejected; you can unplug it')
        return 0
    finally:
        lock.release()
