"""Restic's exclude patterns, matched as restic matches them, to know which folders a backup leaves out as a whole.

A folder that the excludes leave out gets no snapshot of its own. A wrong "left out" would leave a folder out of the
backups, so every doubt is answered "not left out": a pattern this module cannot match exactly matches nothing, and a
set with a negation ("!pattern") leaves out nothing. Restic then still applies every pattern; the cost of a doubt is an
empty snapshot.
"""
from __future__ import annotations

import fnmatch
import os
import re


def read_file(path: str) -> list:
    """The patterns of an exclude file as restic reads it: lines trimmed, blank lines and # comments skipped, and
    $NAME and ${NAME} replaced from the environment ($$ is one $)."""
    out = []
    with open(path, encoding='utf-8', errors='surrogateescape') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                out.append(expand(line))
    return out


def expand(s: str) -> str:
    def one(m):
        if m.group(0) == '$$':
            return '$'
        return os.environ.get(m.group(1) or m.group(2), '')
    return re.sub(r'\$\$|\$\{([^}]*)\}|\$([A-Za-z0-9_]+)', one, s)


def _component(pat: str, name: str) -> bool:
    if not any(c in pat for c in '*?[\\'):
        return pat == name
    if '[' in pat or '\\' in pat:
        # Character classes and escapes differ between Go and Python: match only a pattern that is a plain name
        # once its escapes are removed.
        plain = re.sub(r'\\(.)', r'\1', pat)
        if re.search(r'(?<!\\)[*?\[]', pat):
            return False
        return plain == name
    return fnmatch.fnmatchcase(name, pat)


def _full(pat: list, path: list) -> bool:
    """True if the pattern components match all of PATH's components; ** matches any number of them."""
    if not pat:
        return not path
    if pat[0] == '**':
        return any(_full(pat[1:], path[i:]) for i in range(len(path) + 1))
    return bool(path) and _component(pat[0], path[0]) and _full(pat[1:], path[1:])


def matches(pattern: str, path: str) -> bool:
    """True if the restic exclude PATTERN leaves out the absolute PATH when restic reaches it, as restic's filter.Match
    decides: an absolute pattern matches the first components of the path (so a folder's pattern also matches all in
    it), and a relative pattern matches its last components."""
    pattern = pattern.rstrip('/')
    parts = [p for p in pattern.split('/') if p and p != '.']
    if not parts:
        return False
    names = [p for p in path.split('/') if p]
    if pattern.startswith('/'):
        return any(_full(parts, names[:k]) for k in range(1, len(names) + 1))
    return any(_full(parts, names[k:]) for k in range(len(names)))


class Excludes:
    """The patterns of one backup: case-sensitive (--exclude, --exclude-file) and case-insensitive (--iexclude)."""

    def __init__(self, exact: list, folded: list):
        self.exact = exact
        self.folded = [p.lower() for p in folded]
        self.negated = any(p.startswith('!') for p in exact + folded)

    def wholly(self, path: str) -> bool:
        return bool(self.which(path))

    def which(self, path: str) -> str:
        """The first pattern that leaves PATH out, or '' (always '' with a negated pattern: restic then reads below
        an excluded folder, so nothing is left out wholly)."""
        if self.negated:
            return ''
        hit = next((p for p in self.exact if matches(p, path)), '')
        if hit:
            return hit
        lower = path.lower()
        return next((p for p in self.folded if matches(p, lower)), '')
