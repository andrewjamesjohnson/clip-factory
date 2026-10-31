"""One gate in front of the live state folder (~/clip-factory/state): no test or offline run reads or writes it.

config.STATE is fixed when clipfactory is first imported, and `python -m unittest discover` imports the test files in
alphabetical order. On 2026-10-17 a new test file sorted first and imported clipfactory before any file had pinned
CLIP_FACTORY_STATE: the run wrote 2030 test boards and test stats into the live folder. So this gate does not rely on
the test files. In a test run (unittest is loaded; the factory itself never imports it) or an offline run
(CLIP_FACTORY_OFFLINE=1), the two signals net.py refuses the network on:
  - STATE is never a live folder: a test gets a throwaway one (its child processes share it), and an offline command
    pointed at the live folder stops with a message;
  - an audit hook refuses every open, listing, write, rename, delete or child process that reaches into a live folder,
    so a path fixed at import (stats.OUT) or built from ROOT by hand cannot get there either.
Real runs (launchd, the review page, a person at the command line) have neither signal: STATE as before, no hook.
"""
from __future__ import annotations

import fcntl
import os
import pwd
import shutil
import sys
import tempfile
from pathlib import Path
from types import CodeType
from typing import Dict, FrozenSet, Optional, Tuple

def _home() -> Path:
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)       # not $HOME, which a test may move
    except KeyError:
        return Path.home()


ROOT = Path(__file__).resolve().parent.parent
# this tree's own state and the installed factory's: a scratch copy of the code must not reach the real one either
LIVE = (ROOT / "state", _home() / "clip-factory" / "state")
WHY = "a test or an offline run never reads or writes the live state folder (clipfactory/livestate.py)"


class LiveState(BaseException):
    """A test or an offline run reached into a live state folder. A BaseException, as pytest's outcomes are, so no
    `except Exception` (or rmtree's ignore_errors) in the code under test can swallow it."""


def test_run() -> bool:
    return os.environ.get("CLIP_FACTORY_OFFLINE") == "1" or "unittest" in sys.modules


def safe(state: Path) -> Path:
    """config.STATE. A real run gets `state` as it is. A test or offline run gets the hook, and never a live folder."""
    if not test_run():
        return state
    install()
    if not is_live(state):
        return state
    if "unittest" not in sys.modules:
        raise SystemExit(f"refused: CLIP_FACTORY_OFFLINE=1 never touches the live state ({state}). For a dry run on the "
                         f"real boards, clone them (cp -cR {state} <scratch>/state) and set CLIP_FACTORY_STATE to it.")
    # a test file that did not pin its own folder, or was imported first (2026-10-17): a throwaway one
    state = Path(tempfile.mkdtemp(prefix="clipfactory-state-"))
    os.environ["CLIP_FACTORY_STATE"] = str(state)       # its child processes share it
    return state


NIGHT_ENV = "CLIP_RENDERLOCK_NIGHT"     # renderlock.NIGHT_ENV; "0": no night limits (renderlock imports config)


def daytime_queue() -> None:
    """In a test run (unittest is loaded), CLIP_RENDERLOCK_NIGHT defaults to 0, for this process and every child.
    The render queue applies the night shift's limits by the real clock (renderlock.night_limits: a step asked for
    between 21:00 and 06:15 starts by 05:50 and is stopped at 06:15), so between 05:50 and 06:15 every test that queues
    a step failed "past its 05:50 deadline" on the clock alone (2026-10-20, 70 failures and 38 errors at 06:10). A test
    of the night limits sets the variable to "" itself, or hands hold/run its own night_limits. An offline run by hand
    and the real runs (launchd, the review page, a person) keep the limits: they never load unittest."""
    if "unittest" in sys.modules:
        os.environ.setdefault(NIGHT_ENV, "0")


def is_live(path) -> bool:
    """Is `path` a live state folder or inside one? A relative path is taken from the working folder; symlinks, `..`,
    letter case and macOS's /System/Volumes/Data alias are all seen through."""
    real = _real(os.fsdecode(path))
    if any(real.casefold() == top or real.casefold().startswith(top + os.sep) for top in _tops()):
        return True
    # the same folder under another name (/System/Volumes/Data/...): once symlinks are resolved it is still "state"
    named = [p for p in [Path(real), *Path(real).parents] if p.name.casefold() == "state"]
    live = set(filter(None, map(_inode, LIVE))) if named else set()
    return any(_inode(p) in live for p in named)


# audit event -> its path arguments, each as (index, index of the folder fd it is relative to, or None)
_PATHS = {
    "open": [(0, None)], "os.listdir": [(0, None)], "os.scandir": [(0, None)], "os.walk": [(0, None)],
    "os.chdir": [(0, None)], "pathlib.Path.glob": [(0, None)], "pathlib.Path.rglob": [(0, None)],
    "os.mkdir": [(0, 2)], "os.rmdir": [(0, 1)], "os.remove": [(0, 1)], "os.truncate": [(0, None)],
    "os.utime": [(0, 3)], "os.chmod": [(0, 2)], "os.chown": [(0, 3)], "os.chflags": [(0, None)],
    "os.setxattr": [(0, None)], "os.getxattr": [(0, None)], "os.listxattr": [(0, None)],
    "os.removexattr": [(0, None)], "os.rename": [(0, 2), (1, 3)], "os.link": [(0, 2), (1, 3)], "os.symlink": [(1, 2)],
    "shutil.copyfile": [(0, None), (1, None)], "shutil.copymode": [(0, None), (1, None)],
    "shutil.copystat": [(0, None), (1, None)], "shutil.copytree": [(0, None), (1, None)],
    "shutil.move": [(0, None), (1, None)], "shutil.rmtree": [(0, 1)], "shutil.chown": [(0, None)],
    "shutil.make_archive": [(0, None), (2, None), (3, None)], "shutil.unpack_archive": [(0, None), (1, None)],
    "tempfile.mkstemp": [(0, None)], "tempfile.mkdtemp": [(0, None)], "sqlite3.connect": [(0, None)],
}
# a child process: event -> where its (program or command line, arguments, working folder, environment) are
_SPAWNS = {"subprocess.Popen": (0, 1, 2, 3), "os.system": (0, None, None, None), "os.posix_spawn": (0, 1, None, 2),
           "os.exec": (0, 1, None, 2), "os.spawn": (1, 2, None, 3)}
_installed = False
_TOPS: Optional[FrozenSet[str]] = None
_WALKERS: Optional[Dict[CodeType, Optional[str]]] = None


def install() -> None:
    global _installed
    if not _installed:
        _walkers()                                           # before the hook: never built from inside it
        sys.addaudithook(_hook)
        _installed = True


def _hook(event: str, args: tuple) -> None:
    if event in _PATHS:
        for i, fd_at in _PATHS[event]:
            p = args[i] if i < len(args) else None
            if p is None and event in ("os.listdir", "os.scandir"):
                p = "."
            if not _is_path(p):
                continue                                   # a file descriptor, opened (and checked) before
            p = os.fsdecode(p)
            if not os.path.isabs(p):
                fd = args[fd_at] if fd_at is not None and fd_at < len(args) else _walker_fd() if event == "open" else None
                if isinstance(fd, int) and fd >= 0:          # relative to a folder fd, not to the working folder
                    base = _fd_path(fd)
                    if base is None:
                        continue
                    p = os.path.join(base, p)
            if is_live(p):
                raise LiveState(f"{event} {p}: {WHY}")
    elif event in _SPAWNS:
        prog, argv, cwd, env = (args[i] if i is not None and i < len(args) else None for i in _SPAWNS[event])
        where = os.fsdecode(cwd) if _is_path(cwd) else "."
        words = [os.fsdecode(w) for w in [prog, *(argv if isinstance(argv, (list, tuple)) else [argv])] if _is_path(w)]
        if is_live(where) or any(is_live(os.path.join(where, w)) or _names_live(w) for w in words):
            raise LiveState(f"{event} {' '.join(words)[:300]} (in {where}): {WHY}")
        env = os.environ if env is None else env
        if _env(env, "CLIP_FACTORY_OFFLINE") != "1":   # a child that is the factory guards itself only when offline
            raise LiveState(f"{event} {prog}: a child of a test or an offline run must run with CLIP_FACTORY_OFFLINE=1")
        state = _env(env, "CLIP_FACTORY_STATE")
        if state and is_live(state):
            raise LiveState(f"{event} CLIP_FACTORY_STATE={state}: {WHY}")


def _walker_fd() -> Optional[int]:
    """shutil.rmtree and os.fwalk open each entry by name relative to their folder's fd, which the open event leaves out."""
    try:
        f = sys._getframe(2)                                 # 0 is this, 1 the hook, 2 whoever opened the file
    except ValueError:
        return None
    local = _walkers().get(f.f_code)
    fd = f.f_locals.get(local) if local else None
    return fd if isinstance(fd, int) else None               # None: the top folder, from the working folder


def _walkers() -> Dict[CodeType, Optional[str]]:
    """The stdlib walkers' code -> the local holding the folder fd their open() is relative to. Told by the shape of the
    code, not its name, file or Python version: 3.12.x rewrote both walkers mid-series, 3.14 renamed rmtree's step, and
    from 3.11 os is frozen ("<frozen os>"), so on 3.13 neither the old name nor the old file matched.
      - a step that pops a `stack` (3.12.x on) opens the popped entry at `dirfd`; its `topfd` is what that open returns;
      - a recursive walker (topfd a parameter, 3.9 to 3.12.x) opens each child at `topfd`;
      - their callers rmtree (3.11+) and fwalk open the top folder at `dir_fd`."""
    global _WALKERS
    if _WALKERS is None:
        found: Dict[CodeType, Optional[str]] = {}
        for mod, names in ((shutil, ("rmtree", "_rmtree_safe_fd", "_rmtree_safe_fd_step")), (os, ("fwalk", "_fwalk"))):
            for name in names:
                code = getattr(getattr(mod, name, None), "__code__", None)
                if isinstance(code, CodeType):
                    params = code.co_varnames[:code.co_argcount + code.co_kwonlyargcount]
                    found[code] = next((local for shape, local in (("stack", "dirfd"), ("topfd", "topfd"),
                                                                   ("dir_fd", "dir_fd")) if shape in params), None)
        _WALKERS = found
    return _WALKERS


def _names_live(text: str) -> bool:
    """Does a command line name a live state folder inside it (a shell string, an --out=... argument)?"""
    t = text.casefold()
    for top in _tops() | {"~/clip-factory/state", "$home/clip-factory/state", "${home}/clip-factory/state"}:
        i = t.find(top)
        while i >= 0:
            if i + len(top) == len(t) or t[i + len(top)] in "/ \t\n'\";:,=)&|<>":
                return True
            i = t.find(top, i + 1)
    return False


def _tops() -> FrozenSet[str]:
    global _TOPS
    if _TOPS is None:
        _TOPS = frozenset(form.casefold() for p in LIVE for form in (str(p), _real(str(p))))
    return _TOPS


def _real(p: str) -> str:
    try:
        return os.path.realpath(p)
    except (OSError, ValueError):
        return os.path.abspath(p)


def _fd_path(fd: int) -> Optional[str]:
    try:
        return os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0])   # macOS
    except (OSError, ValueError, AttributeError):
        return None


def _inode(p) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(p)
    except (OSError, ValueError):
        return None
    return st.st_dev, st.st_ino


def _is_path(p) -> bool:
    return isinstance(p, (str, bytes, os.PathLike))


def _env(env, key: str) -> Optional[str]:
    for k in (key, key.encode()):
        try:
            v = env.get(k)
        except (TypeError, AttributeError):
            continue
        if v is not None:
            return os.fsdecode(v)
    return None
