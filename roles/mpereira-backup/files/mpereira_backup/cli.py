"""mpereira-backup: restic backups of a Mac and its drives, on a schedule, and copies of files that lose nothing.

Commands that change a repository, a drive, or the state wait for the lock, so only one runs at a time, and the
scheduled run skips while one of them holds it. A pause stops only the scheduled runs: what you start by hand runs.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import config, dedupe, excludes, jobs, quarantine, restic, transfer, units, verify
from .util import Fail, Lock, Log, iso, parse_iso

EPILOG = """\
The settings are in ~/.config/mpereira-backup/config.json, or the file that --config or MPEREIRA_BACKUP_CONFIG names.
The logs are in STATE_DIR/logs (STATE_DIR is "state_dir" in the settings). See the README for each command."""


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='mpereira-backup', description=__doc__.split('\n')[0], epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', help='the settings file')
    sub = p.add_subparsers(dest='command', metavar='COMMAND')
    sub.required = True

    s = sub.add_parser('run', help='run the jobs that are due (what launchd starts every hour and when a drive mounts)')
    s.add_argument('jobs', nargs='*', metavar='JOB', help='only these jobs')
    s.add_argument('--force', action='store_true', help='run them also if they are not due')

    sub.add_parser('status', help='the drives, the age of each backup, and the state of each job')

    s = sub.add_parser('backup', help='back up one repository now')
    s.add_argument('repository')
    s.add_argument('--continue', dest='continuing', action='store_true',
                   help='skip the snapshots that the last, stopped backup finished')
    s.add_argument('--source', action='append', default=[], help='only this source (more than once for more)')
    s.set_defaults(extra=[])   # after --: more arguments for restic backup, such as --dry-run (see main)

    s = sub.add_parser('forget', help='remove the snapshots that the retention settings do not keep (prune frees the space)')
    s.add_argument('repository')
    s.add_argument('--dry-run', action='store_true', help='show what it would remove, and remove nothing')

    s = sub.add_parser('prune', help='free the space of the snapshots that forget removed')
    s.add_argument('repository')
    s.set_defaults(extra=[])   # after --: more arguments for restic prune

    s = sub.add_parser('check', help='check that a repository is whole')
    s.add_argument('repository')
    s.add_argument('--new-since', metavar='TIME',
                   help='then read back each file a local repository gained since TIME (ISO 8601), or since the last '
                        'read-back that passed ("last")')
    s.set_defaults(extra=[])   # after --: more arguments for restic check, such as --read-data-subset 1/12

    s = sub.add_parser('restic', help='run any restic command on a repository: snapshots, ls, find, restore, mount')
    s.add_argument('repository')
    s.add_argument('args', nargs=argparse.REMAINDER)

    s = sub.add_parser('pause', help='stop the scheduled runs until resume')
    s.add_argument('reason', nargs='+')
    sub.add_parser('resume', help='start the scheduled runs again')

    s = sub.add_parser('eject', help='wait for the running job, then eject a drive')
    s.add_argument('drive')

    s = sub.add_parser('ingest', help='copy a folder to a drive, check every copy, and with --then-trash move the '
                                      'originals to the Trash once the backups hold the copies')
    s.add_argument('source')
    s.add_argument('dest')
    s.add_argument('--then-trash', action='store_true')

    s = sub.add_parser('restore-test', help='restore a folder from the newest snapshot that holds it, and compare it '
                                            'with the folder on disk')
    s.add_argument('repository')
    s.add_argument('path')
    s.add_argument('--snapshot', default='', help='this snapshot (an ID or its start) instead of the newest')
    s.add_argument('--into', default='', help='restore into this folder (default STATE_DIR/restore-test/REPOSITORY)')

    sub.add_parser('copy-media', help='copy the new photos and videos to "copy_media.dest" now')

    s = sub.add_parser('quarantine', help='the files a reorganization put aside: list them, check that their keepers '
                                          'are whole and backed up, and purge them')
    qs = s.add_subparsers(dest='action', metavar='ACTION')
    qs.required = True
    qs.add_parser('list', help='each batch: the files in the quarantine, their age, and when a purge may start')
    q = qs.add_parser('check', help='check that every keeper of a batch is whole and in the backups that must hold it')
    q.add_argument('batch')
    q = qs.add_parser('purge', help='run the check, and if it passes and the batch is old enough, remove its quarantine')
    q.add_argument('batch')
    q.add_argument('--dry-run', action='store_true', help='show what it would remove, and remove nothing')

    s = sub.add_parser('excludes', help='what a repository\'s backups leave out, and what they keep that looks '
                                        'rebuildable')
    es = s.add_subparsers(dest='action', metavar='ACTION')
    es.required = True
    e = es.add_parser('report', help='walk the sources; list each place the backups leave out, with its size and why')
    e.add_argument('repository')
    e.add_argument('--source', action='append', default=[], help='only this source (more than once for more)')
    e.add_argument('--continue', dest='continuing', action='store_true',
                   help='skip the top-level folders that the last, stopped walk finished')
    e.add_argument('--top', type=int, default=30, help='how many of the largest to show (default 30)')
    e = es.add_parser('review', help='from the last report: the kept places named like a cache or a build output, '
                                     'that were not shown before')
    e.add_argument('repository')
    e.add_argument('--all', action='store_true', help='also the ones shown before')
    e.add_argument('--top', type=int, default=50, help='how many to show (default 50)')

    s = sub.add_parser('dedupe', help='find the duplicate files under "dedupe.root" and move all copies but one to the '
                                      'quarantine')
    ds = s.add_subparsers(dest='action', metavar='ACTION')
    ds.required = True
    d = ds.add_parser('plan', help='find the duplicates and pick the copy that stays; writes a plan and moves nothing')
    d.add_argument('batch', help='a new batch name, such as dedupe-2027-01')
    d.add_argument('folders', nargs='*', metavar='FOLDER', help='only these folders under the root')
    d.add_argument('--fclones', dest='fclones_json', default='', metavar='JSON',
                   help='read this output of "fclones group --format json" instead of running fclones')
    d.add_argument('--hashed-after', default='', metavar='TIME',
                   help='with --fclones and --hash-fn blake3: when that run started (ISO 8601), so that apply can take '
                        'the hashes of the files that did not change since')
    d.add_argument('--cache', action='store_true', help='let fclones use its hash cache (apply then reads every file)')
    d = ds.add_parser('apply', help='move the planned files to the quarantine, each after a BLAKE3 comparison with '
                                    'its keeper')
    d.add_argument('batch')
    d.add_argument('--jobs', type=int, default=4, help='files at a time (default 4)')
    d.add_argument('--dry-run', action='store_true', help='check the plan against the disk, and move nothing')
    return p


def _extra(args: list) -> list:
    return args[1:] if args[:1] == ['--'] else args


def parse(argv: list) -> argparse.Namespace:
    """The arguments after the first "--" go to restic as they are (backup, prune, check), so that the options of
    mpereira-backup can come anywhere before it. "restic" takes everything after the repository."""
    p = parser()
    if '--' in argv:
        i = argv.index('--')
        a, rest = p.parse_known_args(argv[:i])
        if a.command != 'restic':
            if rest:
                p.error(f'unrecognized arguments: {" ".join(rest)}')
            if not hasattr(a, 'extra'):
                p.error(f'{a.command} takes no arguments after --')
            a.extra = argv[i + 1:]
            return a
    return p.parse_args(argv)


def _repo(cfg: config.Config, name: str) -> config.Repository:
    if name not in cfg.repositories:
        raise Fail(f'no repository named "{name}"; the repositories are: {", ".join(cfg.repositories)}', 64)
    return cfg.repositories[name]


def _locked(cfg: config.Config, log: Log) -> Lock:
    lock = Lock(cfg.lock_file)
    if not lock.acquire(wait=False):
        log(f'waiting for the lock, held by: {lock.holder() or "another process"}')
        lock.acquire(wait=True)
    return lock


def main(argv: list | None = None) -> int:
    a = parse(list(sys.argv[1:] if argv is None else argv))
    try:
        cfg = config.load(a.config)
        return command(cfg, a)
    except Fail as e:
        print(f'mpereira-backup: {e}', file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print('mpereira-backup: stopped; run the same command again to continue', file=sys.stderr)
        return 130


def command(cfg: config.Config, a: argparse.Namespace) -> int:
    logs = os.path.join(cfg.state_dir, 'logs')
    c = a.command
    if c == 'run':
        return jobs.run(cfg, a.jobs, a.force)
    if c == 'status':
        print(jobs.status(cfg))
        return 0
    if c == 'restic':
        repo = _repo(cfg, a.repository)
        lock = _locked(cfg, Log(logs, 'restic', echo=True))
        os.set_inheritable(lock.fd, True)   # restic keeps the flock until it exits
        restic.exec_restic(cfg, repo, _extra(a.args))
        return 0   # not reached
    if c == 'pause':
        jobs.pause(cfg, ' '.join(a.reason))
        print('scheduled runs paused; "mpereira-backup resume" starts them again')
        return 0
    if c == 'resume':
        print('scheduled runs resumed' if jobs.resume(cfg) else 'they were not paused by mpereira-backup pause')
        why = jobs.paused(cfg)
        if why:
            print(f'still paused by {why}')
        return 0
    if c == 'eject':
        return jobs.eject(cfg, a.drive, Log(logs, 'eject', echo=True))
    if c == 'quarantine' and a.action == 'list':
        print(quarantine.listing(cfg))
        return 0
    if c == 'quarantine' and a.action == 'check':
        # Read-only, so it takes no lock: it can run beside a backup.
        log = Log(logs, 'quarantine', echo=True)
        res = quarantine.check(cfg, a.batch, log)
        quarantine.report(res, a.batch, log)
        return 0 if res.ok else 1

    if c == 'excludes':
        # Read-only: no lock.
        log = Log(logs, 'excludes')
        repo = _repo(cfg, a.repository)
        for s in getattr(a, 'source', []):
            if s not in {x.name for x in repo.sources}:
                raise Fail(f'{repo.name} has no source "{s}"', 64)
        if a.action == 'report':
            return excludes.report(cfg, repo, a.source, a.continuing, a.top, log)
        return excludes.review(cfg, repo, a.all, a.top, log)

    if c == 'dedupe' and a.action == 'plan':
        # It reads the drive and writes only in STATE_DIR: no lock.
        hashed_after = 0.0
        if a.hashed_after:
            if not a.fclones_json:
                raise Fail('--hashed-after goes with --fclones', 64)
            hashed_after = verify.since_arg(a.hashed_after, None).timestamp()
        return dedupe.plan(cfg, a.batch, a.folders, a.fclones_json, hashed_after, a.cache,
                           Log(logs, 'dedupe', echo=True))

    log = Log(logs, c.replace('-', '_') if c != 'backup' else f'backup-{a.repository}', echo=True)
    lock = _locked(cfg, log)
    try:
        if c == 'backup':
            repo = _repo(cfg, a.repository)
            for s in a.source:
                if s not in [x.name for x in repo.sources]:
                    raise Fail(f'{repo.name} has no source named "{s}"', 64)
            extra = _extra(a.extra)
            res = units.backup(cfg, repo.name, log, continuing=a.continuing, extra=extra, sources=a.source or None)
            if not any(x in ('--dry-run', '-n') for x in extra) and res.done:
                st = jobs.State(cfg)
                for s in res.done:
                    st.source(repo.name, s)['last_backup'] = iso()
                st.save()
            for s, why in res.skipped.items():
                log(f'{repo.name}: {s} was not backed up: {why}')
            return res.rc
        if c == 'forget':
            repo = _repo(cfg, a.repository)
            return restic.run(cfg, repo, restic.forget_args(repo, a.dry_run), log)
        if c == 'prune':
            repo = _repo(cfg, a.repository)
            rc = restic.run(cfg, repo, ['prune'] + repo.prune_args + _extra(a.extra), log)
            if rc == 0:
                st = jobs.State(cfg)
                st.repo(repo.name)['last_prune'] = iso()
                st.save()
            return rc
        if c == 'check':
            repo = _repo(cfg, a.repository)
            st = jobs.State(cfg)
            r = st.repo(repo.name)
            since = verify.since_arg(a.new_since, r.get('read_new_since')) if a.new_since else None
            start = iso()
            rc = restic.run(cfg, repo, ['check'] + _extra(a.extra), log)
            if rc == 0:
                r['last_check'] = start
                st.save()
            if rc == 0 and since:
                _, _, bad = verify.read_new(cfg, repo, since, log)
                if bad:
                    return 1
                last = parse_iso(r.get('read_new_since'))
                if not last or since <= last:   # everything since the last read-back is read now
                    r['read_new_since'] = start
                    st.save()
            return rc
        if c == 'restore-test':
            repo = _repo(cfg, a.repository)
            rc = verify.restore_test(cfg, repo, a.path, a.into or verify.default_into(cfg, repo), a.snapshot, log,
                                     own_into=not a.into)
            if rc == 0:
                st = jobs.State(cfg)
                st.repo(repo.name).update(last_restore_test=iso(), last_restore_test_path=os.path.abspath(a.path))
                st.save()
            return rc
        if c == 'ingest':
            return transfer.ingest(cfg, a.source, a.dest, a.then_trash, log)
        if c == 'copy-media':
            print(transfer.copy_media(cfg, log))
            return 0
        if c == 'quarantine':
            return quarantine.purge(cfg, a.batch, log, a.dry_run)
        if c == 'dedupe':
            return dedupe.apply(cfg, a.batch, a.jobs, a.dry_run, log)
        raise AssertionError(c)
    finally:
        lock.release()
