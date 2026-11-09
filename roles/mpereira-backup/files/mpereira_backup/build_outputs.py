"""The build outputs and vendored dependencies in a set of folders, as a restic exclude file.

A folder named target, Library, vendor, or bower_components can also hold your own files, so it is listed only when what
sits beside it shows that a build tool or a package manager made it:
  target:  beside Cargo.toml, pom.xml, project.clj, build.sbt, build.gradle(.kts), build.boot, or deps.edn,
           or in an sbt project folder (beside build.properties or a .sbt file)
  Library: beside the Assets and ProjectSettings folders of a Unity project (Unity rebuilds it)
  vendor:  beside .git or a package manager's file (go.mod, Gopkg.toml, glide.yaml, Gemfile, a .gemspec,
           composer.json, package.json, bower.json, Cargo.toml)
  bower_components: beside bower.json, and the folder that a .bowerrc beside bower.json names as its directory
The walk does not go into .git folders (git keeps submodules in .git/modules, some in a folder named vendor), into
node_modules (rebuildable.exclude leaves it out), into the folders it lists, or onto other file systems (backups use
--one-file-system). A folder it cannot read is skipped, and named in the log.
"""
from __future__ import annotations

import json
import os
import re
import time

from .util import Every, Log, iso, show, write_atomic

TARGET_BESIDE = {'Cargo.toml', 'pom.xml', 'project.clj', 'build.sbt', 'build.gradle', 'build.gradle.kts', 'build.boot',
                 'deps.edn'}
VENDOR_BESIDE = {'.git', 'go.mod', 'Gopkg.toml', 'glide.yaml', 'Gemfile', 'composer.json', 'package.json', 'bower.json',
                 'Cargo.toml'}
UNREADABLE_SHOWN = 50


def is_output(name: str, folder: str, beside: set) -> bool:
    if name == 'target':
        return bool(beside & TARGET_BESIDE) or (os.path.basename(folder) == 'project' and
                                                 ('build.properties' in beside or any(b.endswith('.sbt') for b in beside)))
    if name == 'Library':
        return {'Assets', 'ProjectSettings'} <= beside
    if name == 'vendor':
        return bool(beside & VENDOR_BESIDE) or any(b.endswith('.gemspec') for b in beside)
    if name == 'bower_components':
        return 'bower.json' in beside
    return False


def bowerrc_directory(folder: str, beside: set) -> str | None:
    """The folder inside FOLDER that bower installs into when a .bowerrc names one."""
    if not {'.bowerrc', 'bower.json'} <= beside:
        return None
    try:
        with open(os.path.join(folder, '.bowerrc')) as f:
            d = json.load(f).get('directory')
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(d, str) or not d.strip():
        return None
    path = os.path.normpath(os.path.join(folder, d))
    return path if path.startswith(folder + os.sep) and os.path.isdir(path) and not os.path.islink(path) else None


def pattern(path: str) -> str:
    """PATH as a restic pattern that matches only itself: \\ * ? [ are pattern characters, and $ starts a variable."""
    return re.sub(r'([\\*?\[])', r'\\\1', path).replace('$', '$$')


def folders_last_run(path: str) -> int:
    """The number in the "# folders read: N" line of an earlier exclude file, or 0."""
    try:
        with open(path, errors='replace') as f:
            for line in f:
                if not line.startswith('#'):
                    break
                if line.startswith('# folders read: '):
                    return int(line.split(':')[1])
    except (OSError, ValueError):
        pass
    return 0


def find(roots: list, log: Log, previous: int = 0, progress_seconds: float = 60) -> tuple:
    """Returns (the sorted paths found, the number of folders read)."""
    found = set()
    unreadable = 0
    folders = 0
    start = time.monotonic()
    every = Every(progress_seconds)

    def could_not_read(path, error):
        nonlocal unreadable
        unreadable += 1
        if unreadable <= UNREADABLE_SHOWN:
            log(f'build outputs: could not read {show(path)}: {error.strerror}')

    for root in roots:
        try:
            st = os.lstat(root)
        except OSError:
            continue
        if not os.path.isdir(root) or os.path.islink(root):
            continue
        dev = st.st_dev
        stack = [root]
        while stack:
            folder = stack.pop()
            try:
                with os.scandir(folder) as it:
                    entries = list(it)
            except OSError as error:
                could_not_read(folder, error)
                continue
            folders += 1
            if every():
                took = time.monotonic() - start
                line = f'build outputs: {folders:,} folders read, {took / 60:.0f} min so far'
                if 0 < folders < previous:
                    line += (f', {folders * 100 // previous}% of the {previous:,} of the last run, about '
                             f'{took * (previous - folders) / folders / 60:.0f} min left')
                log(line)
            beside = {e.name for e in entries}
            d = bowerrc_directory(folder, beside)
            if d:
                found.add(d)
            for e in entries:
                try:
                    if not e.is_dir(follow_symlinks=False) or e.name in ('.git', 'node_modules') or e.path in found:
                        continue
                    if is_output(e.name, folder, beside - {e.name}):
                        found.add(e.path)
                        continue
                    if e.stat(follow_symlinks=False).st_dev == dev:
                        stack.append(e.path)
                except OSError as error:
                    could_not_read(e.path, error)
    if unreadable:
        log(f'build outputs: {unreadable} folders could not be read; they were not searched')
    return sorted(found), folders


def write(path: str, roots: list, log: Log) -> int:
    """Writes the exclude file for ROOTS to PATH. Returns the number of folders it lists."""
    found, folders = find(roots, log, previous=folders_last_run(path))
    lines = [f'# made by mpereira-backup on {iso()} from: {" ".join(roots)}', f'# folders read: {folders}']
    kept = 0
    for p in found:
        if p != p.strip():
            log(f'build outputs: kept, because restic cannot match a path that starts or ends with a space: {show(p)}')
            continue
        lines.append(pattern(p))
        kept += 1
    write_atomic(path, '\n'.join(lines) + '\n')
    log(f'build outputs: {kept} folders left out, {folders:,} folders read')
    return kept
