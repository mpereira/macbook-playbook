"""Runs restic against a repository from the settings: its environment, its cache, and its output in the log."""
from __future__ import annotations

import json
import re
import os
import shlex
import subprocess

from .config import Config, Repository
from .util import Fail, Log, mounted

# Restic reads these from the environment. They are removed before a run, so only the settings say which repository it is.
RESTIC_VARS = ('RESTIC_REPOSITORY', 'RESTIC_REPOSITORY_FILE', 'RESTIC_PASSWORD', 'RESTIC_PASSWORD_FILE',
               'RESTIC_PASSWORD_COMMAND', 'RESTIC_CACHE_DIR', 'RESTIC_KEY_HINT')
PROGRESS_FPS = '0.0167'   # one progress line a minute when the output is not a terminal


def read_env_file(path: str) -> dict:
    """The KEY=VALUE lines of a file such as b2.env, with or without "export", values quoted as a shell quotes them.
    The file is read, never run, so it can hold only settings."""
    env = {}
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        raise Fail(f'the environment file {path} does not exist', 78)
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        key, eq, value = line.partition('=')
        if not eq or not key.replace('_', 'a').isalnum():
            raise Fail(f'{path}, line {n}: not a KEY=VALUE line', 78)
        try:
            parts = shlex.split(value, comments=True)
        except ValueError:
            raise Fail(f'{path}, line {n}: the value is not quoted correctly', 78)
        env[key] = parts[0] if parts else ''
    return env


def drive_ready(cfg: Config, repo: Repository) -> bool:
    return not repo.drive or mounted(cfg.drives[repo.drive].mount)


def environment(cfg: Config, repo: Repository) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in RESTIC_VARS}
    env['PATH'] = cfg.env_path
    env['HOME'] = cfg.home
    env['RESTIC_PROGRESS_FPS'] = env.get('RESTIC_PROGRESS_FPS', PROGRESS_FPS)
    if repo.env_file:
        env.update(read_env_file(repo.env_file))
    env['RESTIC_REPOSITORY'] = repo.repository
    env['RESTIC_PASSWORD_FILE'] = repo.password_file
    return env


def global_args(cfg: Config, repo: Repository) -> list:
    """The cache folder. A repository on a drive has no cache unless the settings name one: restic reads the drive about
    as fast as the cache, and a cache of a home folder's trees grows to gigabytes on the Mac's disk. A repository
    elsewhere (B2, S3) uses restic's cache. A cache folder on a drive that is not mounted is not used: making it would
    put a folder where the drive mounts, and the drive could then not mount there."""
    if not repo.cache_dir:
        return ['--no-cache'] if repo.drive else []
    from .config import drive_of_path
    d = drive_of_path(cfg, repo.cache_dir)
    if d and not mounted(d.mount):
        return ['--no-cache']
    return ['--cache-dir', repo.cache_dir]


def command(cfg: Config, repo: Repository, args: list) -> list:
    return [cfg.restic] + global_args(cfg, repo) + list(args)


def check_ready(cfg: Config, repo: Repository) -> None:
    if not drive_ready(cfg, repo):
        d = cfg.drives[repo.drive]
        raise Fail(f'{repo.name} is on {d.name}, and {d.mount} is not mounted', 69)
    if not os.path.isfile(repo.password_file):
        raise Fail(f'{repo.name}: the password file {repo.password_file} does not exist', 78)


ADDED = re.compile(r'Added to the repository: ([0-9.]+) (B|KiB|MiB|GiB|TiB)\b')
UNITS = {'B': 1, 'KiB': 1 << 10, 'MiB': 1 << 20, 'GiB': 1 << 30, 'TiB': 1 << 40}


def run(cfg: Config, repo: Repository, args: list, log: Log, nice: int = 0, errors: list = None,
        added: list = None) -> int:
    """Runs restic and writes each line it prints to the log. Returns its exit code. If ERRORS is a list, the lines that
    start with "error: " (the files that restic could not read) are added to it. If ADDED is a list, the bytes that
    each backup says it added to the repository (before compression) are added to it."""
    check_ready(cfg, repo)
    argv = command(cfg, repo, args)
    log('restic', ' '.join(shlex.quote(a) for a in args))
    pre = (lambda: os.nice(nice)) if nice else None
    try:
        p = subprocess.Popen(argv, env=environment(cfg, repo), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, preexec_fn=pre)
    except OSError as e:
        raise Fail(f'cannot run {cfg.restic}: {e.strerror}', 69)
    with p:
        assert p.stdout
        for raw in p.stdout:
            line = raw.decode('utf-8', 'replace').rstrip('\n')
            log('  ' + line)
            if errors is not None and line.startswith('error: '):
                errors.append(line)
            m = ADDED.match(line) if added is not None else None
            if m:
                added.append(int(float(m.group(1)) * UNITS[m.group(2)]))
    rc = p.returncode
    if rc < 0:
        rc = 128 - rc   # stopped by a signal: the shell's number
    log(f'restic exited with {rc}')
    return rc


def attribute_error(line: str) -> bool:
    """True if the error line says only that restic could not read an extended attribute of a file: restic then still
    backs up the file, its contents and its other metadata."""
    return line.startswith('error: can not obtain extended attribute ')


def capture(cfg: Config, repo: Repository, args: list) -> str:
    """Runs restic and returns what it prints on stdout. Fails on any exit other than 0."""
    check_ready(cfg, repo)
    p = subprocess.run(command(cfg, repo, args), env=environment(cfg, repo), stdin=subprocess.DEVNULL,
                       capture_output=True)
    if p.returncode != 0:
        err = p.stderr.decode('utf-8', 'replace').strip().splitlines()
        raise Fail(f'restic {args[0]} on {repo.name} exited with {p.returncode}: {err[-1] if err else "no message"}',
                   p.returncode)
    return p.stdout.decode('utf-8', 'replace')


def snapshots(cfg: Config, repo: Repository) -> list:
    return json.loads(capture(cfg, repo, ['--no-lock', 'snapshots', '--json']) or '[]')


def exec_restic(cfg: Config, repo: Repository, args: list) -> None:
    """Replaces this process with restic, for commands by hand: restore, ls, find, mount."""
    check_ready(cfg, repo)
    env = environment(cfg, repo)
    if 'RESTIC_PROGRESS_FPS' not in os.environ:
        del env['RESTIC_PROGRESS_FPS']   # in a terminal, restic's own progress display
    argv = command(cfg, repo, args)
    os.execve(argv[0], argv, env)


def forget_args(repo: Repository, dry_run: bool) -> list:
    if not repo.retention:
        raise Fail(f'{repo.name} has no "retention" settings', 78)
    args = ['forget']
    if repo.group_by:
        args += ['--group-by', repo.group_by]
    for key, value in repo.retention.items():
        for v in (value if isinstance(value, list) else [value]):
            args += [f'--{key}', str(v)]
    if dry_run:
        args.append('--dry-run')
    return args
