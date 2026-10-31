"""Small JSON files under state/ for the social layer: read, edit under a lock, append a line, prune old months.

Every social part keeps its own state in plain files under C.STATE (state/social/..., state/<account>/social/...,
state/<account>/outward/..., learn/, engage/ ...), never in config/accounts.json: a tracked file stays clean, a merge
stays fast-forward, and an account's files move with it. These helpers are the one way those files are written:

    path("gaming", "social", "settings.json")     C.STATE/gaming/social/settings.json, read at call time
    read(p, {})                                      the JSON, or the default when it is missing or broken
    with edit(p, dict) as d: d["x"] = 1              flock <p>.lock, read, change, write a tmp file, os.replace
    append(p, row)                                   one JSON line, under the same lock
    rows(p, since=...)                               the lines back, a broken line skipped
    prune_months(folder, "actions-", 90)             drop month files (prefix + YYYY-MM) whose month ended long ago
    with try_lock("send") as got: ...                state/social/locks/send.lock without waiting: got is False when busy

Writes refuse a path outside C.STATE, so a test that forgot to move the state, or a typo in a part, can never write
into the code or the drafts. The path is resolved from C.STATE each call (a test moves C.STATE; a path fixed at import
would write into the wrong folder).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Union

from . import config as C

_MONTH = re.compile(r"^(?P<y>\d{4})-(?P<m>\d{2})$")


def path(*parts: str) -> Path:
    """C.STATE joined with these parts (at call time)."""
    return Path(C.STATE).joinpath(*[str(p) for p in parts])


def _inside(p: Path) -> Path:
    """The path itself, or SystemExit when it is not under C.STATE."""
    p = Path(p)
    top = os.path.realpath(C.STATE)
    real = os.path.realpath(p)
    if real != top and not real.startswith(top.rstrip(os.sep) + os.sep):
        raise SystemExit(f"refused: {p} is not under the state folder {C.STATE}")
    return p


def read(p: Union[Path, str], default=None):
    """The JSON in the file, or `default` when it is missing, unreadable or not JSON (a broken file never stops a
    page or a job; doctor says so where it matters)."""
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


@contextlib.contextmanager
def _locked(p: Path) -> Iterator[None]:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(str(p) + ".lock", "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _write(p: Path, data) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=False, default=str))
    os.replace(tmp, p)


@contextlib.contextmanager
def edit(p: Union[Path, str], default_factory: Callable[[], object] = dict) -> Iterator[object]:
    """Lock the file (<p>.lock), read it (default_factory() when missing or broken, or of another type), yield it for
    changes, then write it back atomically. An exception inside the block writes nothing."""
    p = _inside(Path(p))
    with _locked(p):
        fresh = default_factory()
        data = read(p, None)
        if data is None or not isinstance(data, type(fresh)):
            data = fresh
        yield data
        _write(p, data)


def write(p: Union[Path, str], data) -> None:
    """Replace the file's JSON, under its lock."""
    p = _inside(Path(p))
    with _locked(p):
        _write(p, data)


def append(p: Union[Path, str], row: dict) -> None:
    """One JSON line at the end of the file, under its lock (a log never holds half a line)."""
    p = _inside(Path(p))
    line = json.dumps(row, sort_keys=False, default=str).replace("\n", " ")
    with _locked(p):
        with open(p, "a") as f:
            f.write(line + "\n")


def _when(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def rows(p: Union[Path, str], since=None) -> List[dict]:
    """The file's JSON lines as dicts, oldest first; a broken line (a crash mid-write, a hand edit) is skipped. With
    `since` (a datetime, a date or an ISO time), only the rows whose "t" or "at" is at or after it."""
    try:
        text = Path(p).read_text()
    except OSError:
        return []
    cut = _when(since) if since is not None else None
    out: List[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        if cut is not None:
            t = _when(r.get("t") or r.get("at"))
            if t is None or t < cut:
                continue
        out.append(r)
    return out


def prune_months(folder: Union[Path, str], prefix: str, keep_days: int, now: Optional[datetime] = None) -> List[str]:
    """Remove the month files <prefix>YYYY-MM.json / .jsonl in `folder` whose month ended more than keep_days ago (the
    current month and anything newer always stay). The names removed."""
    folder = _inside(Path(folder))
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(days=int(keep_days))).date()
    gone: List[str] = []
    if not folder.is_dir():
        return gone
    for f in sorted(folder.iterdir()):
        if not f.is_file() or not f.name.startswith(prefix):
            continue
        stem = f.name[len(prefix):]
        for ext in (".jsonl", ".json"):
            if stem.endswith(ext):
                stem = stem[: -len(ext)]
                break
        else:
            continue
        m = _MONTH.match(stem)
        if not m:
            continue
        y, mo = int(m["y"]), int(m["m"])
        last = (date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)) if 1 <= mo <= 12 else None
        if last is not None and last < cut:
            f.unlink()
            gone.append(f.name)
    return gone


@contextlib.contextmanager
def try_lock(name: str) -> Iterator[bool]:
    """state/social/locks/<name>.lock taken without waiting: yields True when this run holds it, False when another
    run does (a job step that is still running is skipped, never queued behind itself)."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name)).strip("-") or "lock"
    p = path("social", "locks", safe + ".lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
