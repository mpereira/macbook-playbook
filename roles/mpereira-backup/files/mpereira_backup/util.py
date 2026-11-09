"""Small pieces every command uses: the log, times, atomic writes, the lock, mounts, and notifications."""
from __future__ import annotations

import datetime
import errno
import fcntl
import json
import os
import subprocess
import sys
import time

LOG_LIMIT = 10_000_000   # a log over 10 MB is renamed to NAME.log.1 when a command opens it


class Fail(Exception):
    """A failure the command reports in one line and exits with CODE."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def iso(t: datetime.datetime | None = None) -> str:
    return (t or now()).isoformat(timespec='seconds')


def parse_iso(s: str | None) -> datetime.datetime | None:
    return datetime.datetime.fromisoformat(s) if s else None


def show(p: str) -> str:
    """A path as text that can be printed, whatever bytes its name has."""
    return p.encode('utf-8', 'surrogateescape').decode('utf-8', 'replace')


class Log:
    """Lines with an ISO 8601 time on the left, in STATE/logs/NAME.log, and on stderr when stderr is a terminal."""

    def __init__(self, folder: str | None, name: str, echo: bool | None = None):
        self.echo = sys.stderr.isatty() if echo is None else echo
        self.f = None
        if folder:
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, name + '.log')
            try:
                if os.path.getsize(path) > LOG_LIMIT:
                    os.replace(path, path + '.1')
            except FileNotFoundError:
                pass
            self.path = path
            self.f = open(path, 'a', encoding='utf-8', errors='replace')

    def __call__(self, *parts: object) -> None:
        line = ' '.join(str(p) for p in parts)
        for one in line.split('\n'):
            stamped = f'{iso()} {one}'
            if self.f:
                self.f.write(stamped + '\n')
                self.f.flush()
            if self.echo or not self.f:
                print(stamped, file=sys.stderr, flush=True)


def write_atomic(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.tmp{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8', errors='surrogateescape') as f:   # names as their bytes on disk
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path: str, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path: str, data) -> None:
    write_atomic(path, json.dumps(data, indent=2, sort_keys=True) + '\n')


class Lock:
    """One lock for every command that reads or writes a repository, a drive, or the state. flock: the kernel lets it go
    when the process ends, so a crash leaves no stale lock."""

    def __init__(self, path: str):
        self.path = path
        self.fd = None

    def acquire(self, wait: bool) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as e:
            raise Fail(f'cannot open the lock file {self.path}: {e.strerror}', 69)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except OSError as e:
            os.close(self.fd)
            self.fd = None
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return False
            raise
        os.ftruncate(self.fd, 0)
        os.write(self.fd, f'{os.getpid()} {iso()} {" ".join(sys.argv[1:])}\n'.encode())
        return True

    def holder(self) -> str:
        try:
            with open(self.path) as f:
                return f.read().strip()
        except OSError:
            return ''

    def release(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def mounted(mount: str) -> bool:
    """True if MOUNT is a mount point now. A folder left where a drive mounts is not one, so nothing writes into it."""
    try:
        return os.path.isdir(mount) and not os.path.islink(mount) and os.path.ismount(mount)
    except OSError:
        return False


def free_bytes(path: str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


FREE_MARGIN = 10 * 1000 ** 3  # what a copy or a restore leaves free on its drive, at the least


def need_space(path: str, size: int, what: str) -> None:
    """Fails when the drive of PATH (or of its nearest folder that exists) has less free space than SIZE plus
    FREE_MARGIN: a Mac whose disk fills up stops working well, and a copy that stops for lack of space helps no one."""
    p = os.path.abspath(path)
    while not os.path.exists(p):
        p = os.path.dirname(p)
    free = free_bytes(p)
    if free < size + FREE_MARGIN:
        raise Fail(f'{what}: {human_bytes(size)} to write to {show(path)}, and its drive has {human_bytes(free)} free; '
                   f'it must keep {human_bytes(FREE_MARGIN)} free besides', 75)


# chflags(1): uchg/schg (immutable) and uappnd/sappnd (append only). Such a file, or a file in such a folder, cannot be
# renamed or removed until the flag is cleared.
LOCKED = 0x2 | 0x4 | 0x20000 | 0x40000


def locked(path: str) -> bool:
    """True if PATH or its folder has a flag that stops a rename or a removal."""
    for p in (path, os.path.dirname(path)):
        try:
            if getattr(os.lstat(p), 'st_flags', 0) & LOCKED:
                return True
        except FileNotFoundError:
            pass
    return False


# Folders that macOS lets a program read only with Full Disk Access. The first that exists tells whether this one has it.
FULL_DISK_ACCESS_PROBES = ['~/Library/Safari', '~/Library/Mail', '~/Library/Messages']


def full_disk_access() -> bool | None:
    """Whether this process has Full Disk Access; None if it cannot tell (not macOS, or none of the folders exists)."""
    if sys.platform != 'darwin':
        return None
    for p in FULL_DISK_ACCESS_PROBES:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            try:
                os.listdir(p)
                return True
            except PermissionError:
                return False
    return None


def notify(command: list[str], title: str, message: str, log: Log | None = None) -> bool:
    """Runs the notification command with {title} and {message} filled in, and says whether it went out. A notification
    that fails is logged, and never fails the command. With no command, nothing goes out and nothing is to retry: True."""
    if not command:
        return True
    argv = [a.replace('{title}', title).replace('{message}', message) for a in command]
    try:
        subprocess.run(argv, check=True, capture_output=True, timeout=30)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        if log:
            log(f'notification failed ({e}): {title}: {message}')
        return False


def human_bytes(n: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(n) < 1000 or unit == 'TB':
            return f'{n:.1f} {unit}' if unit != 'B' else f'{int(n)} B'
        n /= 1000
    return f'{n:.1f} TB'


def human_age(seconds: float) -> str:
    if seconds < 3600:
        return f'{int(seconds // 60)} min'
    if seconds < 172800:
        return f'{seconds / 3600:.0f} h'
    return f'{seconds / 86400:.0f} days'


class Every:
    """True once every SECONDS: for progress lines once a minute."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.last = time.monotonic()

    def __call__(self) -> bool:
        if time.monotonic() - self.last >= self.seconds:
            self.last = time.monotonic()
            return True
        return False
