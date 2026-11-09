"""Tests of mpereira-backup, with a fake restic (tests/fake-restic) and the real b3sum, in temporary folders.

Run them from the role folder:
  /usr/bin/python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
import warnings
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'files'))

from mpereira_backup import cli, config, dedupe, excludes, jobs, patterns, quarantine, restic, transfer, units, verify  # noqa: E402,E501
from mpereira_backup.util import Fail, Lock, Log  # noqa: E402
from mpereira_backup import util  # noqa: E402

util.FREE_MARGIN = 0  # the tests write a few KB; the drive they run on may be nearly full

B3SUM = shutil.which('b3sum') or '/opt/homebrew/bin/b3sum'
FAKE = os.path.join(HERE, 'fake-restic')
FCLONES = shutil.which('fclones') or '/opt/homebrew/bin/fclones'


class Base(unittest.TestCase):
    """A temporary home with a settings file, a state folder, and the fake restic."""

    def setUp(self):
        # each Log keeps its file open for the life of the process, as the command does
        warnings.filterwarnings('ignore', 'unclosed file <_io.TextIOWrapper', ResourceWarning)
        if not os.path.exists(B3SUM):
            self.skipTest('b3sum is not installed')
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='mpb-test-'))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.mk('home/')
        self.fake = self.mk('fake/')
        self.notes = os.path.join(self.tmp, 'notes.txt')
        os.environ['FAKE_RESTIC_DIR'] = self.fake
        self.addCleanup(os.environ.pop, 'FAKE_RESTIC_DIR', None)
        self.write('home/.config/mpb/mac.password', 'secret\n')

    def mk(self, rel: str, content: str | bytes | None = None) -> str:
        p = os.path.join(self.tmp, rel)
        if rel.endswith('/'):
            os.makedirs(p, exist_ok=True)
            return p.rstrip('/')
        return self.write(rel, content or '')

    def write(self, rel: str, content: str | bytes) -> str:
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(content.encode() if isinstance(content, str) else content)
        return p

    def raw(self, **over) -> dict:
        raw = {
            'home': self.home,
            'state_dir': os.path.join(self.tmp, 'state'),
            'restic': FAKE,
            'b3sum': B3SUM,
            'notify': ['/bin/sh', '-c', 'printf "%s|%s\\n" "$0" "$1" >> ' + self.notes, '{title}', '{message}'],
            'drives': {},
            'repositories': {
                'mac': {'repository': os.path.join(self.tmp, 'repo-mac'),
                        'password_file': '~/.config/mpb/mac.password',
                        'sources': [{'name': 'files', 'kind': 'folder', 'path': os.path.join(self.tmp, 'files'),
                                     'split': ['big'], 'remind_after_days': 7}],
                        'retention': {'keep-daily': 7}},
            },
            'jobs': {'mac': {'kind': 'backup', 'repository': 'mac', 'due': {'every_hours': 1}},
                     'health': {'kind': 'health', 'due': {'daily_at': '10:00'}}},
            'trash_requires': ['mac'],
        }
        raw.update(over)
        return raw

    def cfg(self, **over) -> config.Config:
        return config.parse(self.raw(**over), '<test>')

    def calls(self) -> list:
        try:
            with open(os.path.join(self.fake, 'calls.jsonl')) as f:
                return [json.loads(line) for line in f]
        except FileNotFoundError:
            return []

    def notes_text(self) -> str:
        try:
            with open(self.notes) as f:
                return f.read()
        except FileNotFoundError:
            return ''

    def log(self) -> Log:
        return Log(os.path.join(self.tmp, 'logs'), 'test', echo=False)

    def lock(self, path: str) -> None:
        """Sets the uchg flag on PATH, as Finder's "Locked" does, and clears it before the temporary folder goes."""
        if not hasattr(os, 'chflags'):
            self.skipTest('no file flags here')
        os.chflags(path, stat.UF_IMMUTABLE)
        self.addCleanup(os.chflags, path, 0)


class ConfigTests(Base):
    def test_example_loads(self):
        example = os.path.join(os.path.dirname(HERE), 'files', 'config.example.json')
        cfg = config.load(example)
        self.assertEqual(sorted(cfg.repositories), ['mac', 'offsite', 'portable'])
        self.assertTrue(cfg.repositories['mac'].iexclude_files[0].endswith('/share/rebuildable.exclude'))
        self.assertTrue(os.path.isfile(cfg.repositories['mac'].iexclude_files[0]))

    def test_unknown_setting_fails(self):
        with self.assertRaisesRegex(Fail, 'unknown settings colour'):
            self.cfg(colour='blue')

    def test_bad_due_fails(self):
        raw = self.raw()
        raw['jobs']['mac']['due'] = {'daily_at': '25:00'}
        with self.assertRaisesRegex(Fail, 'HH:MM'):
            config.parse(raw)

    def test_cache_by_where_the_repository_is(self):
        raw = self.raw(drives={'pool': {'mount': os.path.join(self.tmp, 'nothing-mounted'), 'kind': 'disk'}})
        mac = raw['repositories']['mac']
        cfg = config.parse(raw)
        self.assertEqual(restic.global_args(cfg, cfg.repositories['mac']), [])          # not on a drive: restic's
        mac['drive'] = 'pool'
        cfg = config.parse(raw)
        self.assertEqual(restic.global_args(cfg, cfg.repositories['mac']), ['--no-cache'])
        mac['cache_dir'] = os.path.join(self.tmp, 'cache')
        cfg = config.parse(raw)
        self.assertEqual(restic.global_args(cfg, cfg.repositories['mac']), ['--cache-dir', mac['cache_dir']])
        mac['cache_dir'] = os.path.join(self.tmp, 'nothing-mounted', 'cache')
        cfg = config.parse(raw)
        self.assertEqual(restic.global_args(cfg, cfg.repositories['mac']), ['--no-cache'])

    def test_home_is_expanded(self):
        self.assertEqual(self.cfg().repositories['mac'].password_file,
                         os.path.join(self.home, '.config/mpb/mac.password'))


class PatternTests(unittest.TestCase):
    def test_absolute_pattern_matches_the_start(self):
        self.assertTrue(patterns.matches('/Volumes/x/a', '/Volumes/x/a/b'))
        self.assertFalse(patterns.matches('/Volumes/x/a', '/Volumes/y/x/a'))

    def test_relative_pattern_matches_the_end(self):
        self.assertTrue(patterns.matches('node_modules', '/p/q/node_modules'))
        self.assertTrue(patterns.matches('.emacs.d*/elpa', '/h/.emacs.d-old/elpa'))
        self.assertTrue(patterns.matches('emacs.d*/*/elpa', '/l/Projects/emacs.d/2019/elpa'))
        self.assertFalse(patterns.matches('node_modules', '/p/node_modules_x'))

    def test_doubt_leaves_nothing_out(self):
        self.assertFalse(patterns.Excludes(['a', '!b'], []).wholly('/x/a'))
        self.assertFalse(patterns.matches('[ab]', '/x/a'))


class DueTests(unittest.TestCase):
    def job(self, due):
        return config.Job(name='j', kind='health', due=due)

    def t(self, s):
        return datetime.datetime.fromisoformat(s).astimezone()

    def test_every_hours_with_slack(self):
        j = self.job({'every_hours': 1})
        self.assertTrue(jobs.is_due(j, None, self.t('2026-11-08T10:00')))
        self.assertTrue(jobs.is_due(j, self.t('2026-11-08T09:05'), self.t('2026-11-08T10:00')))
        self.assertFalse(jobs.is_due(j, self.t('2026-11-08T09:30'), self.t('2026-11-08T10:00')))

    def test_daily_at(self):
        j = self.job({'daily_at': '02:00'})
        self.assertTrue(jobs.is_due(j, self.t('2026-11-07T02:30'), self.t('2026-11-08T03:00')))
        self.assertFalse(jobs.is_due(j, self.t('2026-11-08T02:10'), self.t('2026-11-08T23:00')))
        self.assertFalse(jobs.is_due(j, self.t('2026-11-07T02:30'), self.t('2026-11-08T01:00')))

    def test_monthly_on(self):
        j = self.job({'monthly_on': 15})
        self.assertTrue(jobs.is_due(j, self.t('2026-10-20T00:00'), self.t('2026-11-15T09:00')))
        self.assertFalse(jobs.is_due(j, self.t('2026-11-15T09:00'), self.t('2026-12-01T09:00')))
        self.assertEqual(jobs.next_due(j, self.t('2026-11-15T09:00'), self.t('2026-11-20T09:00')).day, 15)


class SnapshotTimeTests(unittest.TestCase):
    def test_restic_times(self):
        t = units.snapshot_time
        self.assertEqual(t({'time': '2026-11-08T00:14:37.530248123+01:00'}).isoformat(),
                         '2026-11-08T00:14:37.530248+01:00')
        self.assertEqual(t({'time': '2026-11-08T00:14:37.5Z'}).isoformat(), '2026-11-08T00:14:37.500000+00:00')
        self.assertEqual(t({'time': '2026-11-08T00:14:37-03:00'}).isoformat(), '2026-11-08T00:14:37-03:00')
        with self.assertRaises(Fail):
            t({'time': 'yesterday'})


class BackupTests(Base):
    def setUp(self):
        super().setUp()
        for rel in ('files/a/1.txt', 'files/big/x/2.txt', 'files/big/y/3.txt', 'files/big/top.txt', 'files/loose.txt'):
            self.mk(rel, rel)

    def test_units_and_parents(self):
        cfg = self.cfg()
        res = units.backup(cfg, 'mac', self.log())
        self.assertEqual(res.rc, 0)
        backups = [c for c in self.calls() if 'backup' in c['args']]
        paths = sorted(tuple(c['args'][c['args'].index('--') + 1:]) for c in backups)
        files = os.path.join(self.tmp, 'files')
        self.assertIn((f'{files}/a',), paths)
        self.assertIn((f'{files}/big/x',), paths)
        self.assertIn((f'{files}/big/y',), paths)
        self.assertIn((f'{files}/big/top.txt', f'{files}/loose.txt'), paths)
        self.assertEqual(len(backups), 4)
        self.assertTrue(all(c['password_file'].endswith('mac.password') for c in backups))
        # the second run gives each unit its parent snapshot
        units.backup(cfg, 'mac', self.log())
        second = [c for c in self.calls() if 'backup' in c['args']][4:]
        self.assertTrue(all('--parent' in c['args'] for c in second))

    def test_a_stopped_backup_continues(self):
        cfg = self.cfg()
        with open(os.path.join(self.fake, 'backup-rc'), 'w') as f:
            f.write('1')
        res = units.backup(cfg, 'mac', self.log())
        self.assertEqual(res.rc, 1)
        os.remove(os.path.join(self.fake, 'backup-rc'))
        # the first unit failed, so nothing is finished; make one unit finished by hand and continue
        prog = os.path.join(cfg.state_dir, 'backups', 'mac-progress.json')
        with open(prog) as f:
            p = json.load(f)
        p['finished'] = [os.path.join(self.tmp, 'files', 'a')]
        with open(prog, 'w') as f:
            json.dump(p, f)
        self.assertTrue(units.can_continue(cfg, 'mac', 24))
        before = len(self.calls())
        res = units.backup(cfg, 'mac', self.log(), continuing=True)
        self.assertEqual(res.rc, 0)
        done = [c for c in self.calls()[before:] if 'backup' in c['args']]
        self.assertEqual(len(done), 3)

    def test_a_paths_source_on_a_drive_that_is_not_mounted_is_skipped(self):
        repos = self.raw()['repositories']
        pool = os.path.join(self.tmp, 'pool')   # a folder, not a mount point
        self.mk('pool/Photos/p.jpg', 'p')
        repos['mac']['sources'] = [{'name': 'photos', 'kind': 'paths', 'paths': [os.path.join(pool, 'Photos')]},
                                   {'name': 'gone', 'kind': 'paths', 'paths': [os.path.join(self.tmp, 'nothing')]}]
        cfg = self.cfg(repositories=repos, drives={'pool': {'mount': pool, 'kind': 'disk'}})
        res = units.backup(cfg, 'mac', self.log())
        self.assertEqual(res.done, [])
        self.assertEqual(set(res.skipped), {'photos', 'gone'})
        self.assertEqual([c for c in self.calls() if 'backup' in c['args']], [])


class JobTests(Base):
    def setUp(self):
        super().setUp()
        self.mk('files/a/1.txt', 'one')

    def test_run_records_and_is_not_due_again(self):
        cfg = self.cfg()
        self.assertEqual(jobs.run(cfg, ['mac']), 0)
        st = jobs.State(cfg)
        self.assertIn('last_backup', st.data['sources']['mac/files'])
        n = len(self.calls())
        self.assertEqual(jobs.run(cfg, ['mac']), 0)
        self.assertEqual(len(self.calls()), n)   # not due

    def test_one_notification_per_failure_and_one_on_recovery(self):
        cfg = self.cfg()
        with open(os.path.join(self.fake, 'backup-rc'), 'w') as f:
            f.write('1')
        self.assertEqual(jobs.run(cfg, ['mac'], force=True), 1)
        self.assertEqual(jobs.run(cfg, ['mac'], force=True), 1)
        self.assertEqual(self.notes_text().count('mac failed'), 1)
        os.remove(os.path.join(self.fake, 'backup-rc'))
        self.assertEqual(jobs.run(cfg, ['mac'], force=True), 0)
        self.assertIn('mac passed', self.notes_text())

    def backup_exits_3_with(self, *errors):
        with open(os.path.join(self.fake, 'backup-rc'), 'w') as f:
            f.write('3')
        with open(os.path.join(self.fake, 'backup-errors'), 'w') as f:
            f.write(''.join(e + '\n' for e in errors))

    def test_only_unreadable_attributes_pass_with_a_warning(self):
        cfg = self.cfg()
        self.backup_exits_3_with('error: can not obtain extended attribute com.apple.FinderInfo for /x/a: xattr.get /x/a '
                                 'com.apple.FinderInfo: value too large to be stored in data type',
                                 'error: can not obtain extended attribute com.apple.system.Security for /x/b: '
                                 'operation not permitted')
        self.assertEqual(jobs.run(cfg, ['mac'], force=True), 0)
        result = jobs.State(cfg).data['jobs']['mac']
        self.assertIn('extended attributes of 2 files', json.dumps(result))
        self.assertNotIn('mac failed', self.notes_text())

    def test_unreadable_files_still_fail(self):
        cfg = self.cfg()
        self.backup_exits_3_with('error: can not obtain extended attribute com.apple.FinderInfo for /x/a: too large',
                                 'error: open /x/c: permission denied')
        self.assertEqual(jobs.run(cfg, ['mac'], force=True), 1)
        self.assertIn('mac failed', self.notes_text())

    def test_a_backup_that_adds_more_than_the_limit_warns(self):
        raw = self.raw()
        raw['repositories']['mac']['max_added_bytes'] = 1 << 30
        cfg = config.parse(raw, '<test>')
        self.write('fake/backup-added', 'Added to the repository: 1.500 GiB (1.200 GiB stored)\n')
        self.assertEqual(jobs.run(cfg, ['mac'], force=True), 0)
        self.assertIn('over its limit', json.dumps(jobs.State(cfg).data['jobs']['mac']))
        self.assertIn('mac grew', self.notes_text())

    def test_a_backup_under_the_limit_does_not_warn(self):
        raw = self.raw()
        raw['repositories']['mac']['max_added_bytes'] = 1 << 30
        cfg = config.parse(raw, '<test>')
        self.write('fake/backup-added', 'Added to the repository: 0 B   (0 B   stored)\n')
        self.assertEqual(jobs.run(cfg, ['mac'], force=True), 0)
        self.assertNotIn('over its limit', json.dumps(jobs.State(cfg).data['jobs']['mac']))
        self.assertNotIn('grew', self.notes_text())

    def test_exit_3_without_error_lines_still_fails(self):
        cfg = self.cfg()
        self.backup_exits_3_with()
        self.assertEqual(jobs.run(cfg, ['mac'], force=True), 1)

    @unittest.skipUnless(sys.platform == 'darwin', 'Full Disk Access is macOS')
    def test_full_disk_access_is_recorded_and_reminded(self):
        from mpereira_backup import util
        cfg = self.cfg()
        probe = self.mk('home/Library/Safari/')
        with mock.patch.object(util, 'FULL_DISK_ACCESS_PROBES', [probe]):
            os.chmod(probe, 0)
            self.addCleanup(os.chmod, probe, 0o755)
            jobs.run(cfg, ['health'], force=True)
            self.assertIs(jobs.State(cfg).data['full_disk_access'], False)
            self.assertIn('no Full Disk Access', self.notes_text())
            os.chmod(probe, 0o755)
            jobs.run(cfg, ['health'], force=True)
            self.assertIs(jobs.State(cfg).data['full_disk_access'], True)

    def test_drive_away_is_a_skip(self):
        raw = self.raw(drives={'pool': {'mount': os.path.join(self.tmp, 'nothing-mounted'), 'kind': 'disk'}})
        raw['repositories']['mac']['drive'] = 'pool'
        cfg = config.parse(raw)
        self.assertEqual(jobs.run(cfg, ['mac']), 0)
        self.assertEqual(jobs.State(cfg).data['jobs']['mac'].get('result'), None)
        self.assertEqual(self.calls(), [])

    def test_a_notice_that_did_not_go_out_is_sent_again(self):
        cfg = self.cfg(notify=['/usr/bin/false'])
        jobs.pause(cfg, 'testing')
        jobs.run(cfg, ['mac'])
        self.assertNotIn('paused_notified', jobs.State(cfg).data)
        jobs.resume(cfg)
        with open(os.path.join(self.fake, 'backup-rc'), 'w') as f:
            f.write('1')
        jobs.run(cfg, ['mac'], force=True)
        self.assertNotIn('failure_notified', jobs.State(cfg).data['jobs']['mac'])
        cfg = self.cfg()
        jobs.run(cfg, ['mac'], force=True)
        self.assertIn('mac failed', self.notes_text())
        self.assertIn('failure_notified', jobs.State(cfg).data['jobs']['mac'])

    def test_paused_runs_only_health(self):
        cfg = self.cfg()
        jobs.pause(cfg, 'testing')
        self.assertEqual(jobs.run(cfg, []), 0)
        self.assertEqual(self.calls(), [])
        self.assertIn('paused', self.notes_text())
        self.assertTrue(jobs.resume(cfg))
        self.assertEqual(jobs.paused(cfg), '')

    def test_overdue_reminder(self):
        cfg = self.cfg()
        st = jobs.State(cfg)
        old = (datetime.datetime.now().astimezone() - datetime.timedelta(days=9)).isoformat(timespec='seconds')
        st.source('mac', 'files')['last_backup'] = old
        st.save()
        jobs.run(cfg, ['health'], force=True)
        self.assertIn('No backup of files in mac for 9 days', self.notes_text())

    def test_locked_run_waits_for_the_next(self):
        cfg = self.cfg()
        lock = Lock(cfg.lock_file)
        self.assertTrue(lock.acquire(wait=False))
        try:
            self.assertEqual(jobs.run(cfg, ['mac']), 0)
            self.assertEqual(self.calls(), [])
        finally:
            lock.release()

    def test_backups_run_before_checks_and_health_last(self):
        kinds = ['health', 'check', 'copy-media', 'backup', 'check', 'backup']
        js = [config.Job(name=f'{k}{i}', kind=k, due={}) for i, k in enumerate(kinds)]
        self.assertEqual([j.name for j in jobs.ordered(js)],
                         ['backup3', 'backup5', 'copy-media2', 'check1', 'check4', 'health0'])

    def test_status_prints(self):
        out = jobs.status(self.cfg())
        self.assertIn('mac/files', out)
        self.assertIn('Jobs', out)


class IngestTests(Base):
    def setUp(self):
        super().setUp()
        self.src = self.mk('inbox/')
        self.dest = os.path.join(self.tmp, 'files', 'inbox')
        self.mk('inbox/a.txt', 'aaa')
        self.mk('inbox/sub/b.bin', os.urandom(300_000))
        self.mk('inbox/sub/deep/c.txt', 'ccc')
        os.symlink('a.txt', os.path.join(self.src, 'link'))
        self.trash = os.path.join(self.tmp, 'trash')

    def ingest(self, cfg=None, then_trash=False):
        return transfer.ingest(cfg or self.cfg(), self.src, self.dest, then_trash, self.log(), trash_dir=self.trash)

    def read(self, p):
        with open(p, 'rb') as f:
            return f.read()

    def test_copies_everything(self):
        self.assertEqual(self.ingest(), 0)
        for rel in ('a.txt', 'sub/b.bin', 'sub/deep/c.txt'):
            self.assertEqual(self.read(os.path.join(self.src, rel)), self.read(os.path.join(self.dest, rel)))
        self.assertEqual(os.readlink(os.path.join(self.dest, 'link')), 'a.txt')
        self.assertEqual(os.stat(os.path.join(self.src, 'a.txt')).st_mtime_ns,
                         os.stat(os.path.join(self.dest, 'a.txt')).st_mtime_ns)
        leftovers = [n for _, _, ns in os.walk(self.dest) for n in ns if 'mpereira-backup-copy' in n]
        self.assertEqual(leftovers, [])

    def test_never_overwrites(self):
        self.mk('files/inbox/a.txt', 'different')
        self.mk('files/inbox/sub/deep/c.txt', 'ccc')   # the same bytes: counts as there
        self.assertEqual(self.ingest(), 1)
        self.assertEqual(self.read(os.path.join(self.dest, 'a.txt')), b'different')
        run = transfer.Run(self.cfg(), os.listdir(os.path.join(self.tmp, 'state', 'transfers'))[0], self.log())
        run.load()
        self.assertEqual(run.st('a.txt'), 'conflict')
        self.assertEqual(run.st('sub/deep/c.txt'), 'there')

    def test_trash_waits_for_the_backup(self):
        self.assertEqual(self.ingest(), 0)
        with self.assertRaises(Fail) as e:
            self.ingest(then_trash=True)
        self.assertEqual(e.exception.code, 75)
        self.assertTrue(os.path.exists(os.path.join(self.src, 'a.txt')))

    def test_trash_after_the_backup(self):
        cfg = self.cfg()
        self.ingest(cfg)
        self.assertEqual(units.backup(cfg, 'mac', self.log()).rc, 0)
        self.assertEqual(self.ingest(cfg, then_trash=True), 0)
        self.assertEqual(os.listdir(self.src), [])
        self.assertEqual(self.read(os.path.join(self.trash, 'sub/b.bin')), self.read(os.path.join(self.dest, 'sub/b.bin')))
        self.assertTrue(os.path.islink(os.path.join(self.trash, 'link')))

    def test_trash_keeps_what_the_snapshot_lacks(self):
        cfg = self.cfg()
        self.ingest(cfg)
        units.backup(cfg, 'mac', self.log())
        with open(os.path.join(self.fake, 'ls-missing'), 'w') as f:
            f.write(os.path.join(self.dest, 'sub/b.bin') + '\n')
        with self.assertRaises(Fail):
            self.ingest(cfg, then_trash=True)
        self.assertTrue(os.path.exists(os.path.join(self.src, 'a.txt')))

    def test_trash_keeps_what_changed(self):
        cfg = self.cfg()
        self.ingest(cfg)
        units.backup(cfg, 'mac', self.log())
        p = os.path.join(self.src, 'a.txt')
        st = os.stat(p)
        with open(p, 'wb') as f:
            f.write(b'bbb')   # same size and time: only the hash shows it
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(self.ingest(cfg, then_trash=True), 1)
        self.assertEqual(self.read(p), b'bbb')
        self.assertFalse(os.path.exists(os.path.join(self.src, 'sub')))

    def test_trash_leaves_a_locked_file(self):
        cfg = self.cfg()
        self.ingest(cfg)
        units.backup(cfg, 'mac', self.log())
        p = os.path.join(self.src, 'a.txt')
        self.lock(p)
        self.assertEqual(self.ingest(cfg, then_trash=True), 1)
        self.assertTrue(os.path.exists(p))
        self.assertFalse(os.path.exists(os.path.join(self.src, 'sub')))
        run = transfer.Run(cfg, os.listdir(os.path.join(self.tmp, 'state', 'transfers'))[0], self.log())
        run.load()
        self.assertEqual(run.st('a.txt'), 'stays')

    def test_refuses_to_copy_into_a_nearly_full_drive(self):
        with mock.patch.object(util, 'FREE_MARGIN', 1 << 60):
            with self.assertRaises(Fail) as e:
                self.ingest()
        self.assertEqual(e.exception.code, 75)
        self.assertFalse(os.path.exists(os.path.join(self.dest, 'a.txt')))

    def test_a_stopped_copy_continues(self):
        real = transfer.copy_bytes
        n = {'calls': 0}

        def stop_on_second(src, tmp):
            n['calls'] += 1
            if n['calls'] == 2:
                raise KeyboardInterrupt
            real(src, tmp)

        with mock.patch.object(transfer, 'copy_bytes', stop_on_second):
            with self.assertRaises(KeyboardInterrupt):
                self.ingest()
        self.assertEqual(self.ingest(), 0)
        self.assertEqual(self.read(os.path.join(self.dest, 'sub/b.bin')), self.read(os.path.join(self.src, 'sub/b.bin')))

    def test_a_file_changed_after_the_list_is_not_copied(self):
        run = self.listed()
        with open(os.path.join(self.src, 'a.txt'), 'a') as f:
            f.write('more')
        transfer.copy_all(run, self.src, self.dest, 'test')
        self.assertEqual(run.st('a.txt'), 'changed')
        self.assertFalse(os.path.exists(os.path.join(self.dest, 'a.txt')))

    def listed(self):
        run = transfer.Run(self.cfg(), 'manual', self.log())
        transfer.make_list(run, self.src, lambda _: True, lambda _: True, 'test')
        run.load()
        return run

    def test_a_bad_read_in_the_list_is_found(self):
        run = self.listed()
        good = next(i for i in run.items if i.rel == 'a.txt').b3
        for i in run.items:
            if i.rel == 'a.txt':
                i.b3 = '0' * 64   # as if the card gave other bytes once
        transfer.copy_all(run, self.src, self.dest, 'test')
        self.assertEqual(run.st('a.txt'), 'copied')
        self.assertEqual(run.b3(next(i for i in run.items if i.rel == 'a.txt')), good)

    def test_a_bad_copy_stops_the_run(self):
        run = self.listed()

        def bad_copy(src, tmp):
            with open(tmp, 'xb') as f:
                f.write(b'not the same bytes')
        with mock.patch.object(transfer, 'copy_bytes', bad_copy):
            with self.assertRaisesRegex(Fail, 'reads back with other bytes'):
                transfer.copy_all(run, self.src, self.dest, 'test')
        leftovers = [n for _, _, ns in os.walk(self.dest) for n in ns if 'mpereira-backup-copy' in n]
        self.assertEqual(leftovers, [])
        self.assertEqual(sorted(r for r, row in run.state.items() if row['state'] == 'copied'), ['link'])

    def test_a_file_with_the_temporary_name_is_kept(self):
        kept = self.mk('files/inbox/sub/.b.bin.mpereira-backup-copy', 'yours')
        self.assertEqual(self.ingest(), 0)
        self.assertEqual(self.read(os.path.join(self.dest, 'sub/b.bin')), self.read(os.path.join(self.src, 'sub/b.bin')))
        self.assertEqual(self.read(kept), b'yours')

    def test_a_source_file_with_a_temporary_name_is_not_lost(self):
        self.mk('inbox/.a.txt.mpereira-backup-copy', 'mine')
        self.assertEqual(self.ingest(), 0)
        self.assertEqual(self.read(os.path.join(self.dest, '.a.txt.mpereira-backup-copy')), b'mine')
        self.assertEqual(self.read(os.path.join(self.dest, 'a.txt')), b'aaa')

    def test_trash_keeps_an_original_whose_copy_changed(self):
        cfg = self.cfg()
        self.ingest(cfg)
        units.backup(cfg, 'mac', self.log())
        d = os.path.join(self.dest, 'a.txt')
        st = os.stat(d)
        with open(d, 'wb') as f:
            f.write(b'zzz')   # same size and time as the original
        os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(self.ingest(cfg, then_trash=True), 1)
        self.assertEqual(self.read(os.path.join(self.src, 'a.txt')), b'aaa')
        self.assertFalse(os.path.exists(os.path.join(self.src, 'sub')))

    def test_trash_keeps_an_original_with_a_resource_fork(self):
        a = os.path.join(self.src, 'a.txt')
        try:
            with open(os.path.join(a, '..namedfork', 'rsrc'), 'wb') as f:
                f.write(b'fork')
        except OSError:
            self.skipTest('this file system has no resource forks')
        cfg = self.cfg()
        self.ingest(cfg)
        units.backup(cfg, 'mac', self.log())
        self.assertEqual(self.ingest(cfg, then_trash=True), 1)
        self.assertEqual(transfer.resource_fork_size(a), 4)
        self.assertFalse(os.path.exists(os.path.join(self.src, 'sub')))

    def test_refuses_nested_folders(self):
        with self.assertRaises(Fail):
            transfer.ingest(self.cfg(), self.src, os.path.join(self.src, 'sub'), False, self.log())


class CopyMediaTests(Base):
    def media(self):
        return {'home': self.home, 'dest': os.path.join(self.tmp, 'files', 'inbox', '{date}-media'),
                'folders': ['Pictures'], 'skip': ['Pictures/Photos Library.photoslibrary'],
                'bundles': ['.fcpbundle'], 'derived_names': ['Render Files'], 'derived_paths': []}

    def test_copies_new_media_only(self):
        self.mk('home/Pictures/one.jpg', 'one')
        self.mk('home/Pictures/.DS_Store', 'x')
        self.mk('home/Pictures/Photos Library.photoslibrary/db', 'db')
        self.mk('home/Pictures/cut.fcpbundle/Render Files/r.mov', 'render')
        self.mk('home/Pictures/cut.fcpbundle/Original Media/o.mov', 'orig')
        self.mk('home/Documents/doc.txt', 'doc')
        cfg = self.cfg(copy_media=self.media())
        msg = transfer.copy_media(cfg, self.log())
        self.assertIn('2 files', msg)
        dest = cfg.media.dest.replace('{date}', datetime.date.today().isoformat())
        got = sorted(os.path.relpath(os.path.join(r, n), dest) for r, _, ns in os.walk(dest) for n in ns)
        self.assertEqual(got, ['Pictures/cut.fcpbundle/Original Media/o.mov', 'Pictures/one.jpg'])
        self.assertEqual(transfer.copy_media(cfg, self.log()), 'nothing new')
        self.mk('home/Pictures/two.jpg', 'two')
        self.assertIn('1 files', transfer.copy_media(cfg, self.log()))
        self.assertTrue(os.path.exists(os.path.join(dest, 'Pictures/two.jpg')))

    def test_first_run_starts_at_since(self):
        self.mk('home/Pictures/old.jpg', 'old')
        time.sleep(0.05)
        since = datetime.datetime.now().astimezone().isoformat()
        time.sleep(0.05)
        self.mk('home/Pictures/new.jpg', 'new')
        cfg = self.cfg(copy_media=dict(self.media(), since=since))
        self.assertIn('1 files', transfer.copy_media(cfg, self.log()))
        dest = cfg.media.dest.replace('{date}', datetime.date.today().isoformat())
        self.assertEqual(os.listdir(os.path.join(dest, 'Pictures')), ['new.jpg'])
        with self.assertRaises(Fail) as e:
            self.cfg(copy_media=dict(self.media(), since='2026-11-07 18:00'))
        self.assertEqual(e.exception.code, 78)


class LockTests(Base):
    def test_a_lock_that_cannot_be_opened_is_an_error(self):
        self.mk('afile', 'x')
        with self.assertRaises(Fail) as e:
            Lock(os.path.join(self.tmp, 'afile', 'lock')).acquire(wait=False)
        self.assertEqual(e.exception.code, 69)


class CliTests(Base):
    def test_status_and_restic_errors(self):
        path = os.path.join(self.tmp, 'config.json')
        with open(path, 'w') as f:
            json.dump(self.raw(), f)
        self.assertEqual(cli.main(['--config', path, 'status']), 0)
        self.assertEqual(cli.main(['--config', path, 'backup', 'nope']), 64)
        self.assertEqual(cli.main(['--config', os.path.join(self.tmp, 'missing.json'), 'status']), 78)

    def test_forget_dry_run_passes_retention(self):
        path = os.path.join(self.tmp, 'config.json')
        with open(path, 'w') as f:
            json.dump(self.raw(), f)
        self.assertEqual(cli.main(['--config', path, 'forget', 'mac', '--dry-run']), 0)
        args = self.calls()[-1]['args']
        self.assertEqual(args[args.index('forget'):], ['forget', '--keep-daily', '7', '--dry-run'])


class ResticCommandTests(Base):
    def test_the_restic_command_holds_the_lock_while_restic_runs(self):
        path = os.path.join(self.tmp, 'config.json')
        with open(path, 'w') as f:
            json.dump(self.raw(), f)
        real, held, ran = cli._locked, [], []

        def locked(cfg, log):
            held.append(real(cfg, log))
            return held[-1]

        def execve(prog, argv, env):
            ran.append(argv)
            self.assertTrue(os.get_inheritable(held[0].fd))
            self.assertFalse(Lock(held[0].path).acquire(wait=False))
        with mock.patch.object(cli, '_locked', locked), mock.patch.object(restic.os, 'execve', execve):
            cli.main(['--config', path, 'restic', 'mac', 'snapshots'])
        held[0].release()
        self.assertEqual(ran[0][-1], 'snapshots')


class QuarantineTests(Base):
    """A batch moved files/... duplicates to quarantine/b1/...; the mac repository backs up files/."""

    def setUp(self):
        super().setUp()
        self.files = self.mk('files/')
        self.root = self.mk('quarantine/')
        self.journals = self.mk('journal/')
        self.lines = []
        self.add('b1', 'files/a.txt', 'one')
        self.add('b1', 'files/sub/b.txt', 'two')
        self.snapshot()
        self.checked()

    def add(self, batch, keeper_rel, content, cls='A', location='mac', keeper=None, journal_hash=None):
        k = self.write(keeper_rel, content) if keeper is None else keeper
        q = self.write(f'quarantine/{batch}/{keeper_rel}', content)
        src = os.path.join(self.tmp, 'old', keeper_rel)
        st = os.lstat(q)
        h = journal_hash or transfer.Hasher(B3SUM).one(q)
        self.journal(batch, [batch, src, k, location, cls, h, str(st.st_size), str(st.st_mtime_ns), q, 'moved'])
        return k, q

    def journal(self, batch, cols):
        p = os.path.join(self.journals, batch + '.tsv')
        new = not os.path.exists(p)
        with open(p, 'a') as f:
            if new:
                f.write('\t'.join(quarantine.COLUMNS) + '\n')
            f.write('\t'.join(cols) + '\n')

    def snapshot(self):
        with open(os.path.join(self.fake, 'snapshots.json'), 'w') as f:
            json.dump([{'id': '1' * 64, 'time': '2026-11-08T00:00:00.000000000+01:00', 'paths': [self.files]}], f)

    def checked(self, when=None):
        st = os.path.join(self.tmp, 'state')
        os.makedirs(st, exist_ok=True)
        with open(os.path.join(st, 'state.json'), 'w') as f:
            json.dump({'repos': {'mac': {'last_check': (when or jobs.iso())}}}, f)

    def qcfg(self, **q):
        settings = {'journals': self.journals, 'root': self.root, 'locations': {'mac': ['mac']},
                    'read_back': {'mac': 1.0}}
        settings.update(q)
        return self.cfg(quarantine=settings)

    def check(self, **q):
        return quarantine.check(self.qcfg(**q), 'b1', self.log())

    def fake_list(self, name, paths):
        with open(os.path.join(self.fake, name), 'w') as f:
            f.write('\n'.join(paths) + '\n')

    def test_a_whole_backed_up_batch_passes_and_reads_back(self):
        r = self.check()
        self.assertTrue(r.ok, (r.failures, r.blocked))
        self.assertEqual(r.passed, 2)
        self.assertEqual(sum(c['args'][-2:-1] != [] and 'dump' in c['args'] for c in self.calls()), 2)

    def test_a_changed_keeper_fails(self):
        self.write('files/a.txt', 'ONE')
        r = self.check()
        self.assertFalse(r.ok)
        self.assertIn('other content', r.failures[0][1])

    def test_a_keeper_whose_first_read_set_its_ctime_passes(self):
        real, seen = transfer.Hasher.batch, set()

        def read(hasher, paths):
            out = real(hasher, paths)
            for p in paths:
                if p not in seen:
                    seen.add(p)
                    os.chmod(p, 0o640)
            return out
        with mock.patch.object(transfer.Hasher, 'batch', read):
            r = self.check()
        self.assertTrue(r.ok, (r.failures, r.blocked))
        with mock.patch.object(transfer.Hasher, 'batch', side_effect=AssertionError('read again')):
            self.assertTrue(self.check().ok)   # the saved hashes hold for the new ctime

    def test_a_keeper_whose_second_read_differs_fails(self):
        real = transfer.Hasher.batch

        def read(hasher, paths):
            out = real(hasher, paths)
            for p in paths:
                os.chmod(p, 0o640)
            return out
        with mock.patch.object(transfer.Hasher, 'batch', read), \
                mock.patch.object(transfer.Hasher, 'one', return_value='0' * 64):
            r = self.check()
        self.assertFalse(r.ok)
        self.assertEqual({why for _, why in r.failures},
                         {f'keeper {os.path.join(self.files, "a.txt")} changed while it was checked',
                          f'keeper {os.path.join(self.files, "sub/b.txt")} changed while it was checked'})

    def test_a_keeper_the_snapshot_does_not_have_fails(self):
        self.fake_list('ls-missing', [os.path.join(self.files, 'a.txt')])
        r = self.check()
        self.assertEqual([why for _, why in r.failures], ['mac: snapshot 11111111 does not have it'])

    def test_a_keeper_with_no_snapshot_fails(self):
        os.remove(os.path.join(self.fake, 'snapshots.json'))
        r = self.check()
        self.assertEqual(len(r.failures), 2)

    def test_a_snapshot_with_other_content_fails(self):
        a = os.path.join(self.files, 'a.txt')
        self.fake_list('ls-stale', [a])
        self.fake_list('dump-corrupt', [a])
        r = self.check(read_back={'mac': 0})
        self.assertEqual([why for _, why in r.failures], ['mac: snapshot 11111111 holds other content'])

    def test_differing_metadata_is_read_back_and_can_pass(self):
        self.fake_list('ls-stale', [os.path.join(self.files, 'a.txt')])
        r = self.check(read_back={'mac': 0})
        self.assertTrue(r.ok, r.failures)
        self.assertEqual(sum('dump' in c['args'] for c in self.calls()), 1)

    def test_a_keeper_in_the_quarantine_fails(self):
        self.add('b1', 'files/c.txt', 'three', keeper=os.path.join(self.root, 'b1/files/a.txt'))
        r = self.check()
        self.assertTrue(any('in the quarantine' in why for _, why in r.failures))

    def test_a_missing_keeper_fails(self):
        os.remove(os.path.join(self.files, 'a.txt'))
        r = self.check()
        self.assertTrue(any('No such file' in why for _, why in r.failures), r.failures)

    def test_an_old_check_blocks(self):
        self.checked((datetime.datetime.now().astimezone() - datetime.timedelta(days=8)).isoformat())
        r = self.check()
        self.assertFalse(r.ok)
        self.assertIn('no passing check within 7 days', r.blocked[0])

    def test_class_a_left_out_by_every_repository_fails_but_class_c_passes(self):
        self.add('b1', 'files/cache/x.tmp', 'x', cls='A')
        self.add('b1', 'files/cache/y.tmp', 'y', cls='C')
        repos = self.raw()['repositories']
        repos['mac']['exclude'] = ['*.tmp']
        cfg = config.parse(self.raw(repositories=repos, quarantine={
            'journals': self.journals, 'root': self.root, 'locations': {'mac': ['mac']}}), '<test>')
        r = quarantine.check(cfg, 'b1', self.log())
        self.assertEqual([os.path.basename(p) for p, _ in r.failures], ['x.tmp'])
        self.assertEqual(r.passed, 3)
        self.assertEqual(r.left_out, {('mac', f'excluded: {self.files}/cache/x.tmp'): 1,
                                      ('mac', f'excluded: {self.files}/cache/y.tmp'): 1})

    def test_a_keeper_is_followed_through_later_renames(self):
        old = os.path.join(self.files, 'a.txt')
        new = os.path.join(self.files, 'renamed/a.txt')
        os.makedirs(os.path.dirname(new))
        os.rename(old, new)
        self.journal('ren', ['ren', self.files + '/a.txt', new, 'mac', 'A', '-', '0', '0', '-', 'moved'])
        r = self.check()
        self.assertTrue(r.ok, r.failures)

    def test_a_file_replaced_in_place_is_not_a_rename(self):
        a = os.path.join(self.files, 'a.txt')
        self.journal('swap', ['swap', a, a, 'mac', 'A', '-', '0', '0', '-', 'moved'])
        os.remove(a)
        r = self.check()
        self.assertFalse(r.ok)
        self.assertTrue(any('a.txt' in why for _, why in r.failures), r.failures)

    def test_hashes_are_kept_for_the_next_run(self):
        self.assertTrue(self.check(read_back={'mac': 0}).ok)
        cfg = self.qcfg(read_back={'mac': 0})
        cfg.b3sum = '/usr/bin/false'
        r = quarantine.check(cfg, 'b1', self.log())
        self.assertTrue(r.ok, r.failures)

    def test_purge_waits_for_the_age(self):
        rc = quarantine.purge(self.qcfg(), 'b1', self.log())
        self.assertEqual(rc, 75)
        self.assertTrue(os.path.exists(os.path.join(self.root, 'b1/files/a.txt')))

    def test_purge_removes_journals_and_leaves_no_empty_folders(self):
        cfg = self.qcfg(min_age_days=0)
        self.assertEqual(quarantine.purge(cfg, 'b1', self.log(), dry_run=True), 0)
        self.assertTrue(os.path.exists(os.path.join(self.root, 'b1/files/a.txt')))
        self.assertEqual(quarantine.purge(cfg, 'b1', self.log()), 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'b1')))
        states = [e.state for e in quarantine.read_journal(os.path.join(self.journals, 'b1.tsv'))]
        self.assertEqual(states, ['purged', 'purged'])
        self.assertTrue(os.path.exists(os.path.join(self.files, 'a.txt')))
        self.assertIn('purged', quarantine.listing(cfg))

    def test_purge_removes_nothing_when_the_check_fails(self):
        self.write('files/a.txt', 'ONE')
        self.assertEqual(quarantine.purge(self.qcfg(min_age_days=0), 'b1', self.log()), 1)
        self.assertTrue(os.path.exists(os.path.join(self.root, 'b1/files/sub/b.txt')))

    def test_purge_leaves_a_file_that_changed_in_the_quarantine(self):
        q = os.path.join(self.root, 'b1/files/a.txt')
        self.write('quarantine/b1/files/a.txt', 'one, changed')
        self.assertEqual(quarantine.purge(self.qcfg(min_age_days=0), 'b1', self.log()), 1)
        self.assertTrue(os.path.exists(q))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'b1/files/sub')))

    def test_purge_leaves_a_file_whose_content_changed_with_the_same_size_and_time(self):
        q = os.path.join(self.root, 'b1/files/a.txt')
        st = os.stat(q)
        self.write('quarantine/b1/files/a.txt', 'NEW')
        os.utime(q, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(quarantine.purge(self.qcfg(min_age_days=0), 'b1', self.log()), 1)
        with open(q) as f:
            self.assertEqual(f.read(), 'NEW')
        self.assertFalse(os.path.exists(os.path.join(self.root, 'b1/files/sub')))

    def test_purge_leaves_a_file_rewritten_after_it_was_hashed(self):
        q = os.path.join(self.root, 'b1/files/a.txt')
        real = transfer.Hasher.many

        def many(hasher, paths, *a):
            out = real(hasher, paths, *a)
            if q in paths:
                st = os.stat(q)
                with open(q, 'w') as f:
                    f.write('NEW')
                os.utime(q, ns=(st.st_atime_ns, st.st_mtime_ns))
            return out
        with mock.patch.object(transfer.Hasher, 'many', many):
            self.assertEqual(quarantine.purge(self.qcfg(min_age_days=0), 'b1', self.log()), 1)
        with open(q) as f:
            self.assertEqual(f.read(), 'NEW')
        self.assertFalse(os.path.exists(os.path.join(self.root, 'b1/files/sub')))

    def test_purge_removes_a_file_whose_read_set_only_its_ctime(self):
        real = transfer.Hasher.many

        def many(hasher, paths, *a):
            out = real(hasher, paths, *a)
            for p in paths:
                os.chmod(p, 0o640)
            return out
        with mock.patch.object(transfer.Hasher, 'many', many):
            self.assertEqual(quarantine.purge(self.qcfg(min_age_days=0), 'b1', self.log()), 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'b1')))

    def test_a_failed_read_back_is_read_again(self):
        with mock.patch.object(quarantine, 'dump_hash', return_value=''):
            self.assertFalse(self.check().ok)
        r = self.check()
        self.assertTrue(r.ok, (r.failures, r.blocked))

    def test_purge_leaves_a_locked_file(self):
        q = os.path.join(self.root, 'b1/files/a.txt')
        self.lock(q)
        self.assertEqual(quarantine.purge(self.qcfg(min_age_days=0), 'b1', self.log()), 1)
        self.assertTrue(os.path.exists(q))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'b1/files/sub')))

    def test_list(self):
        out = quarantine.listing(self.qcfg())
        self.assertRegex(out, r'b1\s+2\s+6 B\s+0 min\s+20\d\d-')

    def test_a_bad_batch_name_is_refused(self):
        with self.assertRaises(Fail):
            quarantine.check(self.qcfg(), '../b1', self.log())


class VerifyTests(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.mk('repo-mac/')
        self.write('repo-mac/config', 'x')
        self.files = self.mk('files/')
        self.write('files/a.txt', 'one')
        self.write('files/sub/b.txt', 'two')
        os.symlink('a.txt', os.path.join(self.files, 'link'))
        self.path = os.path.join(self.tmp, 'config.json')
        with open(self.path, 'w') as f:
            json.dump(self.raw(), f)

    def pack(self, content: bytes, name: str = '') -> str:
        import hashlib
        name = name or hashlib.sha256(content).hexdigest()
        return self.write(f'repo-mac/data/{name[:2]}/{name}', content)

    def snapshot(self, hours=24):
        t = (datetime.datetime.now().astimezone() + datetime.timedelta(hours=hours)).isoformat()
        with open(os.path.join(self.fake, 'snapshots.json'), 'w') as f:
            json.dump([{'id': '2' * 64, 'time': t, 'paths': [self.files]}], f)

    def test_read_new_finds_a_file_that_does_not_match_its_name(self):
        self.pack(b'good')
        bad = self.pack(b'bad', '0' * 64)
        cfg = self.cfg()
        n, _, wrong = verify.read_new(cfg, cfg.repositories['mac'], datetime.datetime(2000, 1, 1).astimezone(),
                                      self.log())
        self.assertEqual((n, wrong), (2, [bad]))
        n, _, wrong = verify.read_new(cfg, cfg.repositories['mac'], datetime.datetime.now().astimezone() +
                                      datetime.timedelta(hours=1), self.log())
        self.assertEqual((n, wrong), (0, []))

    def test_read_new_refuses_a_repository_on_a_server(self):
        repos = self.raw()['repositories']
        repos['mac']['repository'] = 'b2:bucket:mac'
        cfg = self.cfg(repositories=repos)
        with self.assertRaises(Fail):
            verify.read_new(cfg, cfg.repositories['mac'], datetime.datetime.now().astimezone(), self.log())

    def test_check_new_since_last_moves_its_stamp_only_when_it_passes(self):
        self.pack(b'good')
        self.assertEqual(cli.main(['--config', self.path, 'check', 'mac', '--new-since', 'last']), 64)
        self.assertEqual(cli.main(['--config', self.path, 'check', 'mac', '--new-since', '2000-01-01T00:00:00Z']), 0)
        st = read_state(self.tmp)
        self.assertIn('read_new_since', st['repos']['mac'])
        self.assertIn('last_check', st['repos']['mac'])
        self.pack(b'bad', 'f' * 64)
        os.utime(os.path.join(self.repo, 'data', 'ff', 'f' * 64), (time.time() + 60, time.time() + 60))
        self.assertEqual(cli.main(['--config', self.path, 'check', 'mac', '--new-since', 'last']), 1)
        self.assertEqual(read_state(self.tmp)['repos']['mac']['read_new_since'], st['repos']['mac']['read_new_since'])

    def test_the_check_job_reads_new_files_after_the_first_run(self):
        raw = self.raw()
        raw['jobs']['check'] = {'kind': 'check', 'due': {'every_days': 7}, 'read_new': True}
        cfg = config.parse(raw, '<test>')
        st = jobs.State(cfg)
        self.assertIn('checked mac', jobs.run_check(cfg, cfg.jobs['check'], st, self.log()))
        self.assertIn('read_new_since', st.repo('mac'))
        st.repo('mac')['read_new_since'] = '2000-01-01T00:00:00+00:00'
        self.pack(b'bad', 'e' * 64)
        with self.assertRaises(Fail):
            jobs.run_check(cfg, cfg.jobs['check'], st, self.log())

    def test_restore_test_passes_and_removes_the_copy(self):
        self.snapshot()
        self.assertEqual(cli.main(['--config', self.path, 'restore-test', 'mac', self.files]), 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'state', 'restore-test', 'mac')))
        self.assertIn('last_restore_test', read_state(self.tmp)['repos']['mac'])

    def test_restore_test_refuses_a_nearly_full_drive(self):
        self.snapshot()
        with mock.patch.object(util, 'FREE_MARGIN', 1 << 60):
            self.assertEqual(cli.main(['--config', self.path, 'restore-test', 'mac', self.files]), 75)
        self.assertNotIn('restore', [a for c in self.calls() for a in c['args']])

    def test_restore_test_fails_on_other_content_and_keeps_the_copy(self):
        self.snapshot()
        with open(os.path.join(self.fake, 'dump-corrupt'), 'w') as f:
            f.write(os.path.join(self.files, 'sub/b.txt') + '\n')
        cfg = self.cfg()
        into = os.path.join(self.tmp, 'restored')
        self.assertEqual(verify.restore_test(cfg, cfg.repositories['mac'], self.files, into, '', self.log()), 1)
        self.assertTrue(os.path.exists(into + self.files + '/sub/b.txt'))

    def test_restore_test_fails_on_a_file_the_snapshot_lacks_unless_excluded(self):
        self.snapshot()
        self.write('files/skip.tmp', 'x')
        with open(os.path.join(self.fake, 'ls-missing'), 'w') as f:
            f.write(os.path.join(self.files, 'a.txt') + '\n' + os.path.join(self.files, 'skip.tmp') + '\n')
        repos = self.raw()['repositories']
        repos['mac']['exclude'] = ['*.tmp']
        cfg = self.cfg(repositories=repos)
        into = os.path.join(self.tmp, 'restored')
        log = self.log()
        self.assertEqual(verify.restore_test(cfg, cfg.repositories['mac'], self.files, into, '', log), 1)
        with open(os.path.join(self.tmp, 'logs', 'test.log')) as f:
            text = f.read()
        self.assertIn('a.txt: not restored', text)
        self.assertNotIn('skip.tmp: not restored', text)

    def test_restore_test_refuses_a_folder_that_is_not_empty(self):
        self.snapshot()
        mine = self.write('restored/mine.txt', 'keep')
        cfg = self.cfg()
        with self.assertRaises(Fail):
            verify.restore_test(cfg, cfg.repositories['mac'], self.files, os.path.dirname(mine), '', self.log())
        self.assertTrue(os.path.exists(mine))
        self.assertNotIn('restore', [a for c in self.calls() for a in c['args']])

    def test_restore_test_into_an_empty_folder_leaves_the_folder(self):
        self.snapshot()
        into = self.mk('restored/')
        cfg = self.cfg()
        self.assertEqual(verify.restore_test(cfg, cfg.repositories['mac'], self.files, into, '', self.log()), 0)
        self.assertEqual(os.listdir(into), [])

    def test_restore_test_replaces_what_a_failed_test_left_in_its_own_folder(self):
        self.snapshot()
        self.write('state/restore-test/mac/old.txt', 'left')
        self.assertEqual(cli.main(['--config', self.path, 'restore-test', 'mac', self.files]), 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'state', 'restore-test', 'mac')))

    def test_restore_test_into_a_relative_folder(self):
        self.snapshot()
        cfg = self.cfg()
        old = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, old)
        self.assertEqual(verify.restore_test(cfg, cfg.repositories['mac'], self.files, 'restored', '', self.log()), 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'restored')))

    def test_restore_test_fails_on_another_size_of_an_unchanged_file(self):
        self.snapshot()
        empty = self.write('files/empty.txt', '')
        with open(os.path.join(self.fake, 'dump-corrupt'), 'w') as f:
            f.write(empty + '\n')   # the restored copy gets one byte
        cfg = self.cfg()
        into = os.path.join(self.tmp, 'restored')
        self.assertEqual(verify.restore_test(cfg, cfg.repositories['mac'], self.files, into, '', self.log()), 1)

    def test_restore_test_fails_on_a_link_restored_for_an_unchanged_file(self):
        self.snapshot()
        cfg = self.cfg()
        into = os.path.join(self.tmp, 'restored')
        real = restic.run

        def run(*a, **k):
            rc = real(*a, **k)
            b = into + os.path.join(self.files, 'sub/b.txt')
            os.remove(b)
            os.symlink('elsewhere', b)
            return rc
        with mock.patch.object(verify.restic, 'run', run):
            self.assertEqual(verify.restore_test(cfg, cfg.repositories['mac'], self.files, into, '', self.log()), 1)
        self.assertTrue(os.path.islink(into + os.path.join(self.files, 'sub/b.txt')))

    def test_files_changed_since_the_snapshot_are_not_compared(self):
        self.snapshot(hours=-1)
        with open(os.path.join(self.fake, 'dump-corrupt'), 'w') as f:
            f.write(os.path.join(self.files, 'a.txt') + '\n')
        cfg = self.cfg()
        self.assertEqual(verify.restore_test(cfg, cfg.repositories['mac'], self.files,
                                             os.path.join(self.tmp, 'restored'), '', self.log()), 0)


class ExcludesTests(Base):
    def setUp(self):
        super().setUp()
        self.files = self.mk('files/')
        self.write('files/keep.txt', 'k')
        self.write('files/node_modules/pkg/index.js', 'x' * 100)
        self.write('files/Proj/Cargo.toml', '')
        self.write('files/Proj/target/debug/bin', 'x' * 50)
        self.write('files/tagged/CACHEDIR.TAG', quarantine.CACHEDIR_SIGNATURE.decode() + '\n')
        self.write('files/tagged/blob', 'x' * 30)
        self.write('files/app/Cache/data', 'x' * 20)
        self.write('files/app/Cache/build/inner', 'x' * 5)
        self.write('files/notes.log', 'x' * (2 << 20))
        self.write('files/small.log', 'x')
        repos = self.raw()['repositories']
        repos['mac'].update(exclude=['node_modules'], build_outputs=True)
        self.c = self.cfg(repositories=repos)
        self.repo = self.c.repositories['mac']

    def run_report(self, continuing=False):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(excludes.report(self.c, self.repo, [], continuing, 30, self.log()), 0)
        return out.getvalue()

    def rows(self, name):
        with open(os.path.join(self.tmp, 'state', 'excludes', f'mac-{name}.tsv')) as f:
            return [line.rstrip('\n').split('\t') for line in f]

    def test_the_report_lists_each_topmost_place_left_out_with_its_reason_and_size(self):
        self.run_report()
        left = {r[3][len(self.files) + 1:]: (r[0], int(r[1]), int(r[2])) for r in self.rows('left-out')}
        self.assertEqual(left, {'node_modules': ('exclude node_modules', 100, 1),
                                'Proj/target': ('build output', 50, 1),
                                'tagged': ('CACHEDIR.TAG', 30 + 44, 2)})

    def test_the_review_lists_kept_rebuildable_names_once(self):
        self.run_report()
        review = {r[2][len(self.files) + 1:]: int(r[0]) for r in self.rows('review')}
        self.assertEqual(review, {'app/Cache': 25, 'notes.log': 2 << 20})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            excludes.review(self.c, self.repo, False, 50, self.log())
        self.assertIn('app/Cache', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            excludes.review(self.c, self.repo, False, 50, self.log())
        self.assertNotIn('app/Cache', out.getvalue())
        self.assertIn('0 not shown before', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            excludes.review(self.c, self.repo, True, 50, self.log())
        self.assertIn('app/Cache', out.getvalue())

    def test_a_second_report_shows_what_is_new_and_a_stopped_walk_continues(self):
        self.run_report()
        self.write('files/more/node_modules/a', 'x')
        text = self.run_report()
        self.assertIn('New since the last report: 1', text)
        self.assertIn('more/node_modules', text)
        progress = os.path.join(self.tmp, 'state', 'excludes', 'mac-walk.jsonl')
        with open(progress) as f:
            lines = f.readlines()
        with open(progress, 'w') as f:   # a walk stopped after one top-level entry, mid-line
            f.writelines(lines[:1])   # Proj; app/ comes after it
            f.write('{"top": "/x", "sour')
        self.write('files/app/Cache/new', 'x')   # app/ is walked again, and sees the new file
        self.run_report(continuing=True)
        review = {r[2][len(self.files) + 1:]: int(r[0]) for r in self.rows('review')}
        self.assertEqual(review['app/Cache'], 26)

    def test_review_needs_a_report(self):
        with self.assertRaises(Fail):
            excludes.review(self.c, self.repo, False, 50, self.log())

    def test_which_names_the_pattern(self):
        ex = patterns.Excludes(['*.tmp'], ['CACHES'])
        self.assertEqual(ex.which('/a/b.tmp'), '*.tmp')
        self.assertEqual(ex.which('/a/Caches'), 'caches')
        self.assertEqual(ex.which('/a/b'), '')


class DedupeTests(Base):
    """A pool with library, archive, inbox and replaceable folders; the pool repository backs up all of it but the
    folders named "nobackup"."""

    def setUp(self):
        super().setUp()
        self.pool = self.mk('pool/')
        self.q = os.path.join(self.pool, '.quarantine')
        self.journals = os.path.join(self.pool, '.journal')

    def dcfg(self, **d) -> config.Config:
        repos = self.raw()['repositories']
        repos['pool'] = {'repository': os.path.join(self.tmp, 'repo-pool'), 'password_file': '~/.config/mpb/mac.password',
                         'sources': [{'name': 'pool', 'kind': 'folder', 'path': self.pool}],
                         'exclude': ['nobackup'], 'retention': {'keep-daily': 7}}
        settings = {'root': self.pool, 'location': 'pool',
                    'prefer': ['library', 'archive', 'inbox', 'replaceable/Music', 'replaceable'],
                    'replaceable': ['replaceable'],
                    'stay': [r'^(?!library/)(?:.*/)?(?i:exports?)/'],
                    'skip': ['.quarantine', '.journal'], 'min_size': 4, 'fclones': FCLONES}
        settings.update(d)
        return self.cfg(repositories=repos, quarantine={'journals': self.journals, 'root': self.q,
                                                        'locations': {'pool': ['pool']}}, dedupe=settings)

    def need_fclones(self):
        if not os.path.exists(FCLONES):
            self.skipTest('fclones is not installed')

    def plan(self, cfg=None, batch='d1', folders=(), groups=None, hashed_after=0.0):
        cfg = cfg or self.dcfg()
        js = ''
        if groups is not None:
            js = self.write('groups.json', json.dumps({
                'header': {'command': ['fclones', 'group', '--hash-fn', 'blake3']},
                'groups': [{'file_len': os.path.getsize(fs[0]), 'file_hash': h, 'files': fs} for h, fs in groups]}))
        rc = dedupe.plan(cfg, batch, list(folders), js, hashed_after, False, self.log())
        self.assertEqual(rc, 0)
        folder = os.path.join(self.tmp, 'state', 'dedupe', batch)
        with open(os.path.join(folder, 'plan.tsv')) as f:
            lines = [x.rstrip('\n').split('\t') for x in f]
        with open(os.path.join(folder, 'review.tsv')) as f:
            review = [x.rstrip('\n').split('\t') for x in f]
        return lines, review

    def p(self, rel):
        return os.path.join(self.pool, rel)

    def journal_rows(self, batch='d1'):
        with open(os.path.join(self.journals, batch + '.tsv')) as f:
            return [x.rstrip('\n').split('\t') for x in f][1:]

    def h(self, path):
        return transfer.Hasher(B3SUM).one(path)

    def test_plan_picks_the_keeper_and_leaves_out_what_must_not_move(self):
        self.need_fclones()
        self.write('pool/archive/deep/a/photo.jpg', 'photo')
        self.write('pool/library/photo.jpg', 'photo')
        self.write('pool/inbox/photo.jpg', 'photo')
        self.write('pool/inbox/Exports/photo.jpg', 'photo')          # stays
        self.write('pool/archive/code/.git/HEAD', 'ref')
        self.write('pool/archive/code/photo.jpg', 'photo')                    # repository tree
        self.write('pool/archive/Some.app/Contents/photo.jpg', 'photo')       # package
        self.write('pool/inbox/tiny', 'abc')
        self.write('pool/library/tiny', 'abc')                               # under min_size
        self.write('pool/.quarantine/old/photo.jpg', 'photo')                # skipped
        self.write('pool/replaceable/torrents/x/song.flac', 'song!')
        self.write('pool/replaceable/Music/A/song.flac', 'song!')
        lines, review = self.plan()
        planned = {x[0]: x for x in lines}
        self.assertEqual(sorted(planned), sorted([self.p('archive/deep/a/photo.jpg'), self.p('inbox/photo.jpg'),
                                                  self.p('replaceable/torrents/x/song.flac')]))
        self.assertEqual(planned[self.p('inbox/photo.jpg')][1:4], [self.p('library/photo.jpg'), 'pool', 'A'])
        self.assertEqual(planned[self.p('replaceable/torrents/x/song.flac')][1:4],
                         [self.p('replaceable/Music/A/song.flac'), 'pool', 'C'])
        self.assertEqual(planned[self.p('inbox/photo.jpg')][5], self.h(self.p('library/photo.jpg')))
        self.assertEqual(review, [])

    def test_plan_reads_the_names_that_fclones_escapes(self):
        self.need_fclones()
        k = self.write('pool/library/back\\slash \x01.bin', 'photo')
        s = self.write('pool/inbox/back\\slash \x01.bin', 'photo')
        lines, review = self.plan()
        self.assertEqual([x[:2] for x in lines], [[s, k]])
        self.assertEqual(review, [])

    def test_plan_sends_mixed_and_left_out_keepers_to_review(self):
        a = self.write('pool/library/song.flac', 'song!')
        b = self.write('pool/replaceable/Music/song.flac', 'song!')
        c = self.write('pool/library/nobackup/doc.pdf', 'doc!!')
        d = self.write('pool/archive/doc.pdf', 'doc!!')
        lines, review = self.plan(groups=[('-', [a, b]), ('-', [c, d])])
        self.assertEqual(lines, [])
        reasons = {r[3]: r[0] for r in review}
        self.assertEqual(reasons[a], 'mixes replaceable folders with others')
        self.assertEqual(reasons[d], 'pool leaves out the keeper and keeps a copy')

    def test_plan_leaves_copies_inside_one_prefer_folder_unless_within_tier(self):
        k = self.write('pool/archive/machines/mac1/a.bin', 'aaaa1')
        s = self.write('pool/archive/machines/mac2/deeper/a.bin', 'aaaa1')
        o1 = self.write('pool/other/x.bin', 'xxxx1')                 # in no prefer folder, like its keeper
        o2 = self.write('pool/other/deeper/x.bin', 'xxxx1')
        i = self.write('pool/inbox/a.bin', 'aaaa1')                  # a better folder holds its keeper: moves
        groups = [('-', [k, s, i]), ('-', [o1, o2])]
        lines, review = self.plan(groups=groups)
        self.assertEqual([x[:2] for x in lines], [[i, k]])
        self.assertEqual(review, [])
        lines, _ = self.plan(cfg=self.dcfg(within_tier=True), batch='d2', groups=groups)
        self.assertEqual(sorted(x[:2] for x in lines), sorted([[s, k], [i, k], [o2, o1]]))

    def test_plan_refuses_a_batch_with_a_journal(self):
        os.makedirs(self.journals)
        self.write('pool/.journal/d1.tsv', '\t'.join(quarantine.COLUMNS) + '\n')
        with self.assertRaises(Fail):
            dedupe.plan(self.dcfg(), 'd1', [], '', 0.0, False, self.log())

    def setup_two(self):
        k1 = self.write('pool/library/a.bin', 'aaaa1')
        s1 = self.write('pool/inbox/a.bin', 'aaaa1')
        k2 = self.write('pool/library/b.bin', 'bbbb2')
        s2 = self.write('pool/archive/x/b.bin', 'bbbb2')
        return k1, s1, k2, s2

    def test_apply_moves_with_a_journal_and_a_second_run_finds_it_done(self):
        k1, s1, k2, s2 = self.setup_two()
        self.plan(groups=[('-', [k1, s1]), ('-', [k2, s2])])
        cfg = self.dcfg()
        self.assertEqual(dedupe.apply(cfg, 'd1', 2, True, self.log()), 0)
        self.assertTrue(os.path.exists(s1) and not os.path.exists(self.journals))
        self.assertEqual(dedupe.apply(cfg, 'd1', 2, False, self.log()), 0)
        self.assertFalse(os.path.exists(s1) or os.path.exists(s2))
        self.assertTrue(os.path.isfile(os.path.join(self.q, 'd1', 'inbox', 'a.bin')))
        self.assertTrue(os.path.isfile(os.path.join(self.q, 'd1', 'archive', 'x', 'b.bin')))
        rows = self.journal_rows()
        self.assertEqual(sorted(r[9] for r in rows), ['moved', 'moved', 'planned', 'planned'])
        self.assertEqual({r[5] for r in rows if r[1] == s1}, {self.h(k1)})
        self.assertEqual(dedupe.apply(cfg, 'd1', 2, False, self.log()), 0)
        self.assertEqual(len(self.journal_rows()), 4)
        entries = quarantine.read_journal(os.path.join(self.journals, 'd1.tsv'))
        self.assertEqual({e.state for e in entries}, {'moved'})
        self.assertIn('d1', quarantine.listing(cfg))

    def test_apply_skips_a_file_that_changed_and_a_gone_keeper(self):
        k1, s1, k2, s2 = self.setup_two()
        self.plan(groups=[('-', [k1, s1]), ('-', [k2, s2])])
        self.write('pool/inbox/a.bin', 'aaaa9')   # same size, other content
        os.remove(k2)
        self.assertEqual(dedupe.apply(self.dcfg(), 'd1', 1, False, self.log()), 1)
        self.assertTrue(os.path.exists(s1) and os.path.exists(s2))
        states = {r[1]: r[9] for r in self.journal_rows()}
        self.assertEqual(states, {s1: 'skipped: the file and the keeper differ', s2: 'skipped: the keeper is gone'})

    def test_apply_skips_a_locked_file(self):
        k1, s1, k2, s2 = self.setup_two()
        self.plan(groups=[('-', [k1, s1]), ('-', [k2, s2])])
        self.lock(s1)
        self.assertEqual(dedupe.apply(self.dcfg(), 'd1', 2, False, self.log()), 1)
        self.assertTrue(os.path.exists(s1))
        self.assertFalse(os.path.exists(s2))
        states = {r[1]: r[9] for r in self.journal_rows()}
        self.assertTrue(states[s1].startswith('skipped: locked'), states[s1])
        self.assertEqual(states[s2], 'moved')

    def test_apply_skips_a_hard_link_to_the_keeper(self):
        k1 = self.write('pool/library/a.bin', 'aaaa1')
        s1 = self.p('inbox/a.bin')
        os.makedirs(os.path.dirname(s1))
        os.link(k1, s1)
        os.makedirs(os.path.join(self.tmp, 'state', 'dedupe', 'd1'))
        self.write('state/dedupe/d1/plan.tsv', f'{s1}\t{k1}\tpool\tA\t5\t-\n')
        self.write('state/dedupe/d1/plan.json', json.dumps({'planned': 'x', 'root': self.pool, 'location': 'pool'}))
        self.assertEqual(dedupe.apply(self.dcfg(), 'd1', 1, False, self.log()), 1)
        self.assertTrue(os.path.exists(s1))
        self.assertEqual(self.journal_rows()[0][9], 'skipped: the keeper and the file are one file')

    def test_apply_goes_on_after_a_stop_between_the_move_and_its_line(self):
        k1, s1, k2, s2 = self.setup_two()
        self.plan(groups=[('-', [k1, s1]), ('-', [k2, s2])])
        dst = os.path.join(self.q, 'd1', 'inbox', 'a.bin')
        st = os.lstat(s1)
        os.makedirs(os.path.dirname(dst))
        os.makedirs(self.journals)
        with open(os.path.join(self.journals, 'd1.tsv'), 'w') as f:
            f.write('\t'.join(quarantine.COLUMNS) + '\n')
            f.write('\t'.join(['d1', s1, k1, 'pool', 'A', self.h(s1), str(st.st_size), str(st.st_mtime_ns), dst,
                               'planned']) + '\n')
            f.write('d1\t' + s2 + '\tpart of a li')   # a stop in the middle of a write
        os.rename(s1, dst)
        self.assertEqual(dedupe.apply(self.dcfg(), 'd1', 1, False, self.log()), 0)
        last = {e.source: e.state for e in quarantine.read_journal(os.path.join(self.journals, 'd1.tsv'))}
        self.assertEqual(last, {s1: 'moved', s2: 'moved'})

    def test_apply_takes_the_hashes_of_files_unchanged_since_fclones_started(self):
        k1, s1, k2, s2 = self.setup_two()
        good = self.h(k1)
        self.plan(groups=[(good, [k1, s1]), ('f' * 64, [k2, s2])], hashed_after=time.time() + 60)
        out = os.path.join(self.tmp, 'logs', 'test.log')
        # b.bin's hash in the plan is wrong, and taken on trust for both copies: they are equal to it, so it moves.
        self.assertEqual(dedupe.apply(self.dcfg(), 'd1', 1, False, self.log()), 0)
        with open(out) as f:
            self.assertIn('4 hashes taken from fclones, 0 files read now', f.read())

    def test_apply_reads_files_that_changed_after_fclones_started(self):
        k1, s1, k2, s2 = self.setup_two()
        self.plan(groups=[('f' * 64, [k1, s1])], hashed_after=time.time() - 3600)
        self.assertEqual(dedupe.apply(self.dcfg(), 'd1', 1, False, self.log()), 1)
        self.assertEqual(self.journal_rows()[0][9],
                         'skipped: the file and the keeper are equal, and differ from the hash in the plan')

    def first_read_sets_ctime(self, method='one'):
        """Hasher.METHOD as on OpenZFS on macOS: the first read of each file changes its ctime (here: its mode)."""
        real, seen = getattr(transfer.Hasher, method), set()

        def read(hasher, arg):
            out = real(hasher, arg)
            for p in ([arg] if isinstance(arg, str) else arg):
                if p not in seen:
                    seen.add(p)
                    os.chmod(p, 0o640)
            return out
        return mock.patch.object(transfer.Hasher, method, read)

    def test_apply_reads_again_a_file_whose_first_read_set_its_ctime(self):
        k1, s1, k2, s2 = self.setup_two()
        self.plan(groups=[('-', [k1, s1]), ('-', [k2, s2])])
        with self.first_read_sets_ctime():
            self.assertEqual(dedupe.apply(self.dcfg(), 'd1', 1, False, self.log()), 0)
        self.assertEqual({r[9] for r in self.journal_rows()}, {'planned', 'moved'})
        with open(os.path.join(self.tmp, 'logs', 'test.log')) as f:
            self.assertIn('4 read twice: only their ctime changed', f.read())

    def test_apply_refuses_a_file_whose_second_read_differs(self):
        k1, s1, k2, s2 = self.setup_two()
        self.plan(groups=[('-', [k1, s1])])
        real, calls = transfer.Hasher.one, []

        def read(hasher, path):
            calls.append(path)
            if path == s1 and calls.count(s1) == 1:
                os.chmod(path, 0o640)
            return ('0' * 64) if path == s1 and calls.count(s1) == 2 else real(hasher, path)
        with mock.patch.object(transfer.Hasher, 'one', read):
            self.assertEqual(dedupe.apply(self.dcfg(), 'd1', 1, False, self.log()), 1)
        self.assertTrue(os.path.exists(s1))
        self.assertEqual(self.journal_rows()[0][9], f'skipped: it changed while it was read: {s1}')

    def test_a_plan_that_moves_a_keeper_is_refused(self):
        os.makedirs(os.path.join(self.tmp, 'state', 'dedupe', 'd1'))
        a, b, c = self.p('library/a'), self.p('inbox/a'), self.p('archive/a')
        self.write('state/dedupe/d1/plan.tsv', f'{b}\t{a}\tpool\tA\t5\t-\n{c}\t{b}\tpool\tA\t5\t-\n')
        self.write('state/dedupe/d1/plan.json', json.dumps({'planned': 'x', 'root': self.pool, 'location': 'pool'}))
        with self.assertRaises(Fail):
            dedupe.read_plan(self.dcfg(), 'd1')

    def test_config_checks_the_dedupe_settings(self):
        with self.assertRaises(Fail):
            self.dcfg(location='nowhere')
        with self.assertRaises(Fail):
            self.dcfg(prefer=['../up'])
        with self.assertRaises(Fail):
            self.dcfg(stay=['('])
        with self.assertRaises(Fail):
            self.dcfg(within_tier='yes')

    def test_cli_parses_dedupe(self):
        a = cli.parse(['dedupe', 'plan', 'd2', '/Volumes/pool/archive', '--fclones', 'g.json', '--hashed-after',
                       '2026-11-08T00:00:00Z'])
        self.assertEqual((a.batch, a.folders, a.fclones_json), ('d2', ['/Volumes/pool/archive'], 'g.json'))
        a = cli.parse(['dedupe', 'apply', 'd2', '--dry-run', '--jobs', '2'])
        self.assertEqual((a.dry_run, a.jobs), (True, 2))


class ParseTests(unittest.TestCase):
    def test_options_after_the_repository_are_not_given_to_restic(self):
        a = cli.parse(['check', 'mac', '--new-since', 'last', '--', '--read-data-subset', '1/12'])
        self.assertEqual((a.new_since, a.extra), ('last', ['--read-data-subset', '1/12']))
        a = cli.parse(['backup', 'mac', '--continue'])
        self.assertEqual((a.continuing, a.extra), (True, []))
        a = cli.parse(['restic', 'mac', 'ls', '--json', '--', 'x'])
        self.assertEqual(cli._extra(a.args), ['ls', '--json', '--', 'x'])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.parse(['status', '--', 'x'])


def read_state(tmp: str) -> dict:
    with open(os.path.join(tmp, 'state', 'state.json')) as f:
        return json.load(f)


if __name__ == '__main__':
    unittest.main()
