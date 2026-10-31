"""The 03:45 GTA retry: the night shift's GTA fallback (audit GTA-10, Andrew's call 13 on 2026-10-17, "install the
retry"; the night shift, 2026-10-19: clipfactory/shift.py has the clock).

    factory.py night-retry gaming [--date today] [--dry-run]

launchd runs it at 03:45 (com.clipfactory.gta-retry, clipfactory/timer.py). It takes the slots the night left failed
or open once everyone else has stopped working the GTA board, and starts `factory.py make --slots ...` in the
background for them:

- It starts only between 03:40 and 05:30 (shift.LAST_PASS): a run launchd fires late (the Mac slept through 03:45)
  still works until 05:30, and never later.
- It waits while anyone else works the GTA board (shift.who_works): the night's worker (it makes its passes until
  05:30), a gap-fill's make, and the GTA second half by hand (the Claude task gaming-morning-sweep, registered with
  `factory.py shift begin`). Still busy at 05:30, it does nothing: they made those slots. It never waits for the Mac
  itself: the render queue gives each step its turn, so a Politics render is no reason to wait or to give up.
- Only slots still open or failed, at least 2 hours away or after Andrew's sign-off (06:45, shift.SIGN), whichever
  comes first: what its make finishes by 06:15 is signed before its slot, so from 04:45 on a failed 07:00 is still
  taken (the night may fill 07:00 until 05:50).
- Nothing new after 05:50 (shift.LAST_START): with the render queue (clipfactory/renderlock.py), no step inside the
  make starts after it (CLIP_RENDERLOCK_UNTIL). And it watches its worker: still running at 06:15 (shift.DONE_BY), it
  is stopped, and the slots it was making are marked failed, so nothing lands while Andrew signs (06:45) and no slot
  sits at "making".

Every check of the make stays: the retry only chooses which slots and when.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from datetime import date, datetime, timedelta
from typing import Callable, List, Optional, Tuple

from . import board as B
from . import config as C
from . import makers as M
from . import shift as SH

NOT_BEFORE = "03:40"     # launchd fires it at 03:45; a run fired before this does nothing
START_BY = SH.LAST_PASS  # 05:30: no new make after it (it waits for the board until then)
UNTIL_AT = SH.LAST_START  # 05:50: no step of its make starts after it (CLIP_RENDERLOCK_UNTIL)
STOP_BY = SH.DONE_BY     # 06:15: its worker is stopped; the 06:30 notice reads the board, he signs about 06:45
LEAD_MIN = 120           # only slots at least 2 hours away (the gap-fill's rule), or after the sign-off (SIGN 06:45)
POLL_S = 60              # how often it looks again while someone else works the board
WATCH_S = 30             # how often it looks at its worker
UNTIL_ENV = "CLIP_RENDERLOCK_UNTIL"   # renderlock.py: a deadline to START, read by every queued step of the make

# the busy check the Claude tasks use: real render processes only (the bench fill reads it; the retry itself no longer
# waits for renders: the render queue gives each step its turn)
_BUSY_COMM = re.compile(r"^([Pp]ython[0-9.]*|ffmpeg|ffprobe|whisper-cli)$")
_BUSY_ARGS = re.compile(r"run\.py|clipkit|analyze\.py|build_all|factory\.py (make|igcut)|ffmpeg|whisper|demucs|"
                        r"multireact")


def busy_now() -> List[str]:
    """The render processes running now (pid, then the command), this process left out."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ucomm=,args="], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for ln in out.splitlines():
        parts = ln.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or int(parts[0]) == os.getpid():
            continue
        if _BUSY_COMM.match(parts[1]) and _BUSY_ARGS.search(parts[2]):
            found.append(f"{parts[0]} {parts[2]}")
    return found


def due_slots(cfg: C.Config, acct: C.Account, day: date, now: datetime,
              lead_min: int = LEAD_MIN) -> Tuple[List[str], List[str]]:
    """(the open or failed slots at least lead_min away or after Andrew's sign-off on the day (shift.SIGN, 06:45),
    whichever is sooner; the open or failed ones before that). The night shift (2026-10-19): a clip its make finishes
    by 06:15 is signed at about 06:45, so a 07:00 slot is takeable all night."""
    bd = B.read(acct, day) or {}
    edge, sign = now + timedelta(minutes=lead_min), cfg.slot_time(day, SH.SIGN)
    if now < sign:
        edge = min(edge, sign)
    want, near = [], []
    for k, s in sorted((bd.get("slots") or {}).items()):
        if s.get("status") not in (B.OPEN, B.FAILED):
            continue
        (want if cfg.slot_time(day, k) >= edge else near).append(k)
    return want, near


def _alive(pid: int) -> bool:
    """The worker's starter (caffeinate) is still running. It is this process's child, so reap it when it ends (a
    child that ended and was never reaped would still answer kill(0))."""
    try:
        got, _ = os.waitpid(pid, os.WNOHANG)
        if got == pid:
            return False
        return True
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _args_of(pid: int) -> str:
    try:
        return subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _stop_worker(pid: int) -> str:
    """Stop the worker this retry started (its whole process group: caffeinate and factory.py make). The make stops
    its running step's own group on SIGTERM (factory.py cmd_make), as Ctrl-C does."""
    args = _args_of(pid)
    if "factory.py" not in args or "--_worker" not in args:
        return f"not stopped: pid {pid} is no longer the retry's worker"
    for sig, wait in ((signal.SIGTERM, 90), (signal.SIGKILL, 20)):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        for _ in range(wait):
            if not _alive(pid):
                break
            time.sleep(1)
        else:
            continue
        break
    return f"stopped the worker (pid {pid}) at {STOP_BY}: nothing lands while the owner signs"


def _others(cfg: C.Config, acct: C.Account, day: date, t: datetime) -> List[str]:
    """Who else works the GTA board now (shift.who_works: the worker, a registered Claude run)."""
    return SH.who_works(cfg, acct, day, me="night-retry", now=t)


def run(cfg: C.Config, acct: C.Account, day: date, dry_run: bool = False,
        now: Optional[Callable[[], datetime]] = None, sleep: Callable[[float], None] = time.sleep,
        others: Optional[Callable[[datetime], List[str]]] = None, spawn: Optional[Callable] = None,
        alive: Callable[[int], bool] = _alive, stop: Callable[[int], str] = _stop_worker,
        say: Callable[[str], None] = print) -> List[str]:
    """The retry. Returns what it said (and says each line as it goes, for the launchd log)."""
    clock = now or cfg.now
    lines: List[str] = []
    who = others or (lambda t: _others(cfg, acct, day, t))

    def tell(s: str) -> None:
        lines.append(s)
        say(s)

    if acct.maker != "gta":
        raise SystemExit(f"night-retry is for a GTA account; {C.possessive(acct.name)} gap-fill is a Claude task")
    t = clock()
    start, start_by, until, stop_by = (cfg.slot_time(t.date(), x) for x in (NOT_BEFORE, START_BY, UNTIL_AT, STOP_BY))
    if not start <= t < start_by:
        tell(f"{acct.name}: not run at {t:%H:%M}; the retry starts only between {NOT_BEFORE} and {START_BY} "
             f"(the night shift's GTA fallback, before the {SH.NOTICE} notice)")
        return lines
    waited = False
    while True:
        t = clock()
        busy = who(t)
        if not busy:
            break
        if t >= start_by:
            tell(f"{acct.name}: not run; the board was still being worked at {START_BY} ({busy[0][:160]}): it made "
                 f"those slots")
            return lines
        if not waited:
            tell(f"waiting while {busy[0][:160]} works the board (the retry takes what is left once it stops, until "
                 f"{START_BY}; a Politics render is no reason to wait: the render queue gives each its turn)")
            waited = True
        if dry_run:
            tell(f"(dry run: would wait until nobody else works the board, at most until {START_BY})")
            break
        sleep(POLL_S)
    if t >= start_by:
        tell(f"{acct.name}: not run; it is past {START_BY}")
        return lines
    want, near = due_slots(cfg, acct, day, t)
    if near:
        tell(f"under 2 hours away and before the {SH.SIGN} sign-off, left alone: {', '.join(near)}")
    if not want:
        tell(f"{acct.name} {day}: no open or failed slot 2 hours away or after the {SH.SIGN} sign-off; nothing to "
             f"make again")
        return lines
    argv = ["make", acct.slug, "--date", day.isoformat(), "--slots", ",".join(want)]
    if dry_run:
        tell(f"would start: factory.py {' '.join(argv)} --background (nothing new after {UNTIL_AT}, "
             f"stopped at {STOP_BY})")
        return lines
    before = os.environ.get(UNTIL_ENV)
    if before is None:
        os.environ[UNTIL_ENV] = str(int(until.timestamp()))   # the render queue: no step starts after 05:50
    try:
        said = (spawn or M.spawn_background)(argv, acct, day)
    finally:
        if before is None:
            os.environ.pop(UNTIL_ENV, None)
    tell(f"making again {', '.join(want)}: {said}")
    m = re.search(r"\(pid (\d+)\)", said)
    if not m:
        return lines      # another worker got there first: it makes them
    pid = int(m.group(1))
    while alive(pid):
        if clock() >= stop_by:
            tell(stop(pid))
            for _ in range(120):          # the make lets go of the account's worker lock as it ends
                if not M.other_worker_running(acct):
                    break
                sleep(1)
            for ln in M.reset_stale_making(cfg, acct, day):
                tell(ln)
            break
        sleep(WATCH_S)
    else:
        tell("the worker finished")
    return lines
