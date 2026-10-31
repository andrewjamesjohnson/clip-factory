"""The night shift (2026-10-19). Andrew, 2026-10-18: "One night shift from 9 PM ..., Politics and GTA take turns,
refused clips try the next video, all 20 done for my sign-off, 7 days a week", then "Normally, I'm up at 6:45 ... the
timing of everything needs to be correct", and "Yes, clips done by 6:15 and the notification at 6:30 work for me".

THE CLOCK, every night, for the NEXT posting day (Mountain, this Mac's clock). Every other module reads it from here.

    21:00  START       the GTA worker (launchd gta-nightly) and the Politics's Claude run (task hearing-clip-sweep-2)
                       start together for tomorrow's boards; their heavy steps take turns in the render queue
    22:00  the Politics's second half (task politics-early-gap-fill, fires about 22:13) waits in the background for the
           first half to stop (a Claude run can end early: its context, an error), then goes on where it stopped
    03:20  RESCAN_UNTIL the GTA worker, out of sources before it, scans the watchlist again every 40 minutes
           (gta_maker rescan_every) instead of ending; after it, the GTA second half and the retry take over
    03:30  the GTA second half by hand (task gaming-morning-sweep) registers, waits for the GTA workers to stop
    03:45  the GTA retry (launchd gta-retry, nightretry.py) waits for the night's worker and the GTA second half
           to stop, then makes the failed slots again (its fallback when no Claude run comes)
    05:30  LAST_PASS   no new GTA pass, and no GTA intake estimated to end after it
    05:50  LAST_START  nothing new starts (CLIP_RENDERLOCK_UNTIL, every --until): no intake, no render, no cut
    06:15  DONE_BY     everything is finished: the GTA workers stop themselves, the Claude runs stop
    06:18  BENCH       the bench fill (launchd bench-fill; writes nothing after BENCH_STOP 06:25)
    06:30  NOTICE      the morning notice (launchd morning)
    06:45  SIGN        Andrew signs in Clip Review; the slots post 07:00 to 23:00 (a slot he signs after its time
                       posts at once, up to poster.LATE_LIMIT, 20 hours, late)
    06:15-08:00 FREEZE no deploy, no swap, no review-page restart: the boards are final from 06:15, the notice goes
                       at 06:30, he signs from about 06:45, and the 07:00 slot posts in it

WHO WORKS A BOARD TONIGHT (`factory.py shift`). Two Claude runs must never work one account's board at once (two
sessions would pick the same slots and each check the day rule alone), and the Politics's second half must pick up when
a first half stops early (its context, an error). So a Claude run registers:

    factory.py shift day                                         the posting day a run that starts now fills
                                                                 (before 07:00 today, from 07:00 tomorrow)
    factory.py shift begin <account> --date D --task <task id> [--until HH:MM]   at its start (it records its own
                                                                 Claude session, and its own last start: --until,
                                                                 before noon; default LAST_START 05:50)
    factory.py shift check <account> --date D --task <task id> [--until HH:MM]   before each new clip and each
                                                                 render: 0 go on; 3 a later run took over (stop);
                                                                 75 it is past the run's last start (stop)
    factory.py shift wait  <account> --date D --task <task id> [--until 05:50]   blocks until no OTHER run works
                                                                 that board (exit 0), or the deadline (exit 75)
    factory.py shift end   <account> --date D --task <task id> [--why "..."]
    factory.py shift status [account] [--date D]

A run is WORKING while its Claude session is alive and doing something: a command or background task of it is
running (a process other than a plain shell under it), or it did something in the last IDLE_MIN (45) minutes (its
record, its board). A run whose session is gone, that said `end`, or that sat idle IDLE_MIN minutes, has STOPPED. A GTA
worker (state/gaming/.worker.lock) is always working while it runs. Once no registered run works the board, what of
the account is still in the render queue (a render a stopped run left behind) still counts: the next run waits for it.
When no Claude run registered for the night (an old prompt), the board is free once nothing of the account is in the
render queue and its board has not changed for QUIET_MIN minutes. Reads ps and the state folder only; writes only
state/<account>/shift/<date>.json (and its .lock).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import board as B
from . import config as C

START = "21:00"         # the shift starts (gta-nightly, hearing-clip-sweep-2)
LAST_PASS = "05:30"     # no new GTA pass; no GTA intake estimated to end after it
LAST_START = "05:50"    # nothing new starts after it
DONE_BY = "06:15"       # everything finished
BENCH = "06:18"         # the bench fill starts
BENCH_STOP = "06:25"    # the bench fill writes nothing after it
NOTICE = "06:30"        # the morning notice
SIGN = "06:45"          # Andrew signs
FREEZE = ("06:15", "08:00")   # no deploy, no swap, no review-page restart
POLITICS_SECOND_HALF = "22:00"  # task politics-early-gap-fill (cron; it fires about 13 minutes after and waits)
RESCAN_UNTIL = "03:20"        # makers gta_maker rescan_until: no GTA rescan wait ends after it
GTA_SECOND_HALF = "03:30"     # task gaming-morning-sweep (cron; it fires about 11 minutes after)
RETRY = "03:45"               # launchd gta-retry

IDLE_MIN = 45           # a Claude run whose session is alive and did nothing for this long has stopped (10-17's
                        # 19:00 run never went 10 minutes without a command running: 45 is a wide margin)
IDLE_NO_SESSION_MIN = 75   # the same, for a run whose Claude session could not be found (judged by its writes only)
QUIET_MIN = 15          # no run registered: the board unchanged this long, nothing in the queue = free
POLL_S = 60
BUSY_EXIT = 75          # renderlock's: not done, the deadline came first
TAKEN_EXIT = 3          # shift check: a later run took over this board
SHELLS = {"zsh", "-zsh", "bash", "-bash", "sh", "-sh", "dash", "login"}

Ps = Callable[[], Dict[int, Tuple[int, str]]]


def in_freeze(t: datetime) -> bool:
    """Andrew's morning: the boards are final, the notice goes, he signs, 07:00 posts. No deploy, swap or restart."""
    a, b = FREEZE
    return a <= t.strftime("%H:%M") < b


def clock(cfg: C.Config, day: date, hhmm: str) -> datetime:
    """A shift time for the posting day `day`: before noon on that day, from noon on the evening before."""
    t = cfg.slot_time(day, C.normal_slot(hhmm))
    return t - timedelta(days=1) if int(hhmm[:2]) >= 12 else t


# ------------------------------------------------------------------------------------------------------ processes
def _ps() -> Dict[int, Tuple[int, str]]:
    """pid -> (ppid, ucomm) of every process (one ps call)."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,ucomm="], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table: Dict[int, Tuple[int, str]] = {}
    for ln in out.splitlines():
        parts = ln.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2].strip() if len(parts) > 2 else "")
    return table


def _lstart(pid: int) -> Optional[str]:
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None


def session_pid(table: Optional[Dict[int, Tuple[int, str]]] = None, me: Optional[int] = None) -> Optional[int]:
    """The Claude session this process runs under: the nearest ancestor named claude (a Claude Code session's own
    process; its commands and background tasks are its children). None outside a Claude session (launchd, a
    terminal)."""
    table = table if table is not None else _ps()
    pid = me or os.getpid()
    for _ in range(40):
        row = table.get(pid)
        if not row:
            return None
        ppid, comm = row
        if pid != (me or os.getpid()) and comm.strip().lower() == "claude":
            return pid
        if ppid in (0, 1) or ppid == pid:
            return None
        pid = ppid
    return None


def busy_under(pid: int, table: Dict[int, Tuple[int, str]]) -> List[str]:
    """What runs under a Claude session now: its descendant processes that are not a plain shell (a command, a
    background task, a wait loop's sleep, a render it started)."""
    kids: Dict[int, List[int]] = {}
    for p, (pp, _) in table.items():
        kids.setdefault(pp, []).append(p)
    out, todo, seen = [], list(kids.get(pid, [])), set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        comm = table.get(p, (0, ""))[1].strip()
        if comm and comm.lower() not in SHELLS:
            out.append(f"{p} {comm}")
        todo += kids.get(p, [])
    return sorted(out)


# ------------------------------------------------------------------------------------------------------ the record
def path(acct: C.Account, day: date) -> Path:
    return C.STATE / acct.slug / "shift" / f"{day.isoformat()}.json"


def read(acct: C.Account, day: date) -> dict:
    try:
        d = json.loads(path(acct, day).read_text())
    except (OSError, ValueError):
        return {"runs": {}}
    return d if isinstance(d, dict) and isinstance(d.get("runs"), dict) else {"runs": {}}


def _write(acct: C.Account, day: date, d: dict) -> None:
    p = path(acct, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.replace(tmp, p)


@contextlib.contextmanager
def _edit(acct: C.Account, day: date):
    """Read, change, write the night's record under its own lock (two runs never drop each other's line)."""
    p = path(acct, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".lock"), "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        d = read(acct, day)
        yield d
        _write(acct, day, d)


def _stamp(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _parse(s: object) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def last_start(run: Optional[dict], until: Optional[str] = None) -> str:
    """A run's own last start (HH:MM): --until given now, else the one it registered with, else LAST_START (05:50).
    A daytime run (the 07:45 morning fill: 11:30) gives its own, or the night's 05:50 would tell it to stop."""
    for v in (until, (run or {}).get("until")):
        if v:
            return C.normal_slot(str(v))
    return LAST_START


def begin(cfg: C.Config, acct: C.Account, day: date, task: str, now: Optional[datetime] = None,
          ps: Optional[Ps] = None, lstart: Optional[Callable[[int], Optional[str]]] = None, me: Optional[int] = None,
          until: Optional[str] = None) -> List[str]:
    """Register this Claude run on the account's board for the night (again: a fresh begin, the old record kept as
    `before`). until: the run's own last start (HH:MM before noon; default LAST_START), which `check` reads."""
    ps, lstart = ps or _ps, lstart or _lstart
    t = now or cfg.now()
    table = ps()
    sid = session_pid(table, me)
    run = {"task": task, "began_at": _stamp(t), "touched_at": _stamp(t), "session": sid,
           "session_start": lstart(sid) if sid else None, "ended_at": None}
    if until:
        run["until"] = C.normal_slot(str(until))
    with _edit(acct, day) as d:
        old = d["runs"].get(task)
        if old:
            run["before"] = {k: old.get(k) for k in ("began_at", "ended_at", "why")}
        d["runs"][task] = run
    others = [f"{r['task']} ({state})" for r, state, _ in states(cfg, acct, day, now=t, ps=lambda: table,
                                                                 lstart=lstart) if r["task"] != task]
    lines = [f"{acct.name} {day}: {task} registered at {t:%H:%M}"
             + (f" (its Claude session, pid {sid})" if sid else " (no Claude session found: judged by what it writes)")]
    if others:
        lines.append("  other runs tonight: " + "; ".join(others))
    lines += handed(acct)
    own = last_start(run)
    if own == LAST_START:
        lines.append(f"  the clock: nothing new after {LAST_START}, finished by {DONE_BY}, the notice at {NOTICE}, "
                     f"Andrew signs about {SIGN}")
    else:
        lines.append(f"  this run's clock: nothing new after {own} (its own --until; `shift check` reads it)")
    return lines


def handed(acct: C.Account) -> List[str]:
    """What every night run is handed at its `shift begin`, in code, so no run can skip it (the owner, 2026-10-31; the
    loops thesis: a rule in a runbook got skipped, a rule in a command's output does not): the playbook Andrew
    approved (the arrow from the morning's lessons to the night), then the account's DEAD ideas (IDEAS.md)."""
    out: List[str] = []
    try:
        from . import playbook as PB
        for ln in PB.brief(acct).splitlines()[1:]:
            if ln.startswith(("What works", "Not proven", "Biggest problems", "Best ")):
                break
            out.append("  " + ln)
    except Exception:  # noqa: BLE001  (registering never fails over the playbook)
        pass
    try:
        from . import ideasfile as IF
        out += IF.lines(acct)
    except Exception:  # noqa: BLE001  (nor over the ideas file)
        pass
    return out

def touch(cfg: C.Config, acct: C.Account, day: date, task: str, now: Optional[datetime] = None) -> None:
    with _edit(acct, day) as d:
        r = d["runs"].get(task)
        if r:
            r["touched_at"] = _stamp(now or cfg.now())


def end(cfg: C.Config, acct: C.Account, day: date, task: str, why: str = "",
        now: Optional[datetime] = None) -> List[str]:
    t = now or cfg.now()
    with _edit(acct, day) as d:
        r = d["runs"].get(task)
        if r:
            r.update(ended_at=_stamp(t), why=why.strip()[:400], touched_at=_stamp(t))
    if not r:
        return [f"{acct.name} {day}: {task} was not registered (shift begin); nothing to end"]
    return [f"{acct.name} {day}: {task} ended at {t:%H:%M}" + (f": {why.strip()[:200]}" if why.strip() else "")]


def _activity(cfg: C.Config, acct: C.Account, day: date) -> Optional[datetime]:
    """The last time the account's board for the day changed (a collect, a note, a make's deal)."""
    try:
        m = B.board_path(acct, day).stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(m, tz=cfg.tz())


def run_state(cfg: C.Config, acct: C.Account, day: date, run: dict, now: datetime,
              table: Dict[int, Tuple[int, str]], lstart: Callable[[int], Optional[str]],
              seen_busy: Optional[datetime] = None) -> Tuple[str, str]:
    """('working' | 'ended' | 'gone' | 'idle', words) for one registered run."""
    if run.get("ended_at"):
        return "ended", f"ended at {str(run['ended_at'])[11:16]}" + (f" ({run['why']})" if run.get("why") else "")
    sid = run.get("session")
    if sid:
        if int(sid) not in table:
            return "gone", f"its Claude session (pid {sid}) is gone"
        want = run.get("session_start")
        if want and lstart(int(sid)) not in (None, want):
            return "gone", f"its Claude session (pid {sid}) is gone (that pid is another process now)"
        busy = busy_under(int(sid), table)
        if busy:
            return "working", f"working (pid {sid}: {busy[0]}{' +' + str(len(busy) - 1) if len(busy) > 1 else ''})"
    marks = [x for x in (_parse(run.get("touched_at")), _parse(run.get("began_at")), _activity(cfg, acct, day),
                         seen_busy) if x is not None]
    last = max(marks) if marks else None
    grace = IDLE_MIN if sid else IDLE_NO_SESSION_MIN
    if last is not None and (now - last).total_seconds() < grace * 60:
        return "working", f"between steps (last active {last:%H:%M})"
    return "idle", (f"idle since {last:%H:%M}" if last else "idle") + f" ({grace} min with nothing running)"


def states(cfg: C.Config, acct: C.Account, day: date, now: Optional[datetime] = None, ps: Optional[Ps] = None,
           lstart: Optional[Callable[[int], Optional[str]]] = None,
           seen_busy: Optional[Dict[str, datetime]] = None) -> List[Tuple[dict, str, str]]:
    ps, lstart = ps or _ps, lstart or _lstart
    t = now or cfg.now()
    table = ps()
    out = []
    for task, r in sorted(read(acct, day)["runs"].items(), key=lambda kv: str(kv[1].get("began_at"))):
        st, words = run_state(cfg, acct, day, r, t, table, lstart, (seen_busy or {}).get(task))
        out.append((r, st, words))
    return out


def worker_running(acct: C.Account) -> bool:
    from . import makers as M
    return M.other_worker_running(acct)


def _queued(acct: C.Account) -> List[str]:
    """What of this account is in the render queue now (holding it, or waiting)."""
    from . import renderlock as RL
    try:
        st = RL.status()
    except Exception:  # noqa: BLE001
        return []
    out = []
    h = st.get("holder")
    if h and h.get("account") == acct.slug:
        out.append(f"rendering: {h.get('what')}")
    out += [f"waiting: {w.get('what')}" for w in st.get("waiting") or [] if w.get("account") == acct.slug]
    out += [f"beside: {b.get('what')}" for b in st.get("beside") or [] if b.get("account") == acct.slug]
    return out


def who_works(cfg: C.Config, acct: C.Account, day: date, me: str = "", now: Optional[datetime] = None,
              ps: Optional[Ps] = None, lstart: Optional[Callable[[int], Optional[str]]] = None,
              seen_busy: Optional[Dict[str, datetime]] = None,
              worker: Optional[Callable[[C.Account], bool]] = None,
              queued: Optional[Callable[[C.Account], List[str]]] = None) -> List[str]:
    """Who else works the account's board now ([] = nobody: the board is free for `me`)."""
    ps, lstart = ps or _ps, lstart or _lstart
    t = now or cfg.now()
    out = []
    if (worker or worker_running)(acct):
        out.append(f"the {acct.name} worker (state/{acct.slug}/.worker.lock)")
    rows = states(cfg, acct, day, now=t, ps=ps, lstart=lstart, seen_busy=seen_busy)
    out += [f"{r['task']}: {words}" for r, st, words in rows if r["task"] != me and st == "working"]
    if not out and (rows or acct.maker == "politics"):
        # nobody registered works it (they stopped, or nothing registered tonight): a render one of them left in the
        # queue (renderlock runs its command in a session of its own, so it outlives a run that crashed) still
        # writes into its job folder: wait for it
        q = (queued or _queued)(acct)
        if q:
            out.append(f"{acct.name} in the render queue ({q[0]})"
                       + (" (left by a run that stopped)" if rows else ""))
    if not rows and acct.maker == "politics":
        # nothing registered tonight (an old prompt): free once nothing of it renders and its board is quiet
        last = _activity(cfg, acct, day)
        if last is not None and (t - last).total_seconds() < QUIET_MIN * 60:
            out.append(f"its board changed at {last:%H:%M} (free after {QUIET_MIN} quiet minutes)")
    return out


def check(cfg: C.Config, acct: C.Account, day: date, task: str, now: Optional[datetime] = None,
          until: Optional[str] = None) -> Tuple[int, str]:
    """Before each new clip and each render: (0, 'go on') | (TAKEN_EXIT, why) | (BUSY_EXIT, why). until: the run's
    own last start (HH:MM before noon), else the one it registered with (`begin --until`), else LAST_START (05:50)."""
    t = now or cfg.now()
    with _edit(acct, day) as d:
        mine = d["runs"].get(task)
        if mine is not None:
            mine["touched_at"] = _stamp(t)
    began = _parse((mine or {}).get("began_at"))
    others = [r for k, r in d["runs"].items() if k != task and not r.get("ended_at")]
    later = [r for r in others if began is not None and (_parse(r.get("began_at")) or began) > began]
    if mine is None and others:
        later = others                                  # it never registered: a run that did works this board
    if later:
        r = later[-1]
        return TAKEN_EXIT, (f"stop: {r['task']} took over {C.possessive(acct.name)} {day} board at "
                            f"{str(r['began_at'])[11:16]}; collect, note and end now")
    own = last_start(mine, until)
    if t >= clock(cfg, day, own):
        if own == LAST_START:
            return BUSY_EXIT, f"stop: it is past {LAST_START}, nothing new starts; collect, note and end by {DONE_BY}"
        return BUSY_EXIT, f"stop: it is past {own}, this run's last start; collect, note and end"
    if own == LAST_START:
        return 0, f"go on: {acct.name} {day}, nothing new after {LAST_START}, finished by {DONE_BY}"
    return 0, f"go on: {acct.name} {day}, nothing new after {own} (this run's own clock)"


def wait(cfg: C.Config, acct: C.Account, day: date, task: str, until: Optional[str] = None,
         now: Optional[Callable[[], datetime]] = None, sleep: Callable[[float], None] = time.sleep,
         say: Callable[[str], None] = print, ps: Optional[Ps] = None, lstart: Optional[Callable[[int], Optional[str]]] = None,
         worker: Optional[Callable[[C.Account], bool]] = None,
         queued: Optional[Callable[[C.Account], List[str]]] = None, poll: float = POLL_S) -> int:
    """Block until nobody else works the board (0), or the deadline (BUSY_EXIT). It remembers when each run was last
    seen busy, so a run between two steps is not taken for stopped until IDLE_MIN minutes passed."""
    ps, lstart = ps or _ps, lstart or _lstart
    tick = now or cfg.now
    stop = clock(cfg, day, until or LAST_START)
    seen: Dict[str, datetime] = {}
    told = None
    while True:
        t = tick()
        table = ps()
        for r in read(acct, day)["runs"].values():
            sid = r.get("session")
            if sid and int(sid) in table and busy_under(int(sid), table):
                seen[r["task"]] = t
        others = who_works(cfg, acct, day, me=task, now=t, ps=lambda: table, lstart=lstart, seen_busy=seen,
                           worker=worker, queued=queued)
        if not others:
            say(f"{acct.name} {day}: free at {t:%H:%M}; nobody else works the board")
            return 0
        if t >= stop:
            say(f"{acct.name} {day}: still not free at {stop:%H:%M} ({others[0]}); not started")
            return BUSY_EXIT
        if told is None or (t - told).total_seconds() >= 15 * 60:
            say(f"{acct.name} {day}: waiting ({t:%H:%M}): " + "; ".join(others))
            told = t
        sleep(poll)


def lines(cfg: C.Config, accts: List[C.Account], day: date, now: Optional[datetime] = None) -> List[str]:
    """`factory.py shift status`."""
    t = now or cfg.now()
    out = [f"the night shift for {day}: starts {START} on {day - timedelta(days=1)}, last pass {LAST_PASS}, nothing "
           f"new after {LAST_START}, finished by {DONE_BY}, bench {BENCH}, notice {NOTICE}, Andrew signs about {SIGN}"
           + ("  (now: the morning freeze, no deploy)" if in_freeze(t) and t.date() == day else "")]
    for acct in accts:
        bd = B.read(acct, day) or {}
        n = B.counts(bd) if bd else {}
        base = B.base_slots(bd) if bd else []
        spare = [s for s in (bd.get("slots") or {}).values() if B.is_spare(s)] if bd else []
        done = (B.READY, B.APPROVED, B.SCHEDULED)
        ready = sum(1 for s in base if s.get("status") in done) if bd else 0
        extra = sum(1 for s in spare if s.get("status") in done)
        # an open spare slot is never missing: it fills only with a clip that passed every check
        out.append(f"{acct.name}: {ready} of {len(base) or len(acct.slots_for(day))} made"
                   + (f", {extra} of {len(spare)} spare slots filled" if spare else "")
                   + (f"; {acct.name} worker running" if acct.maker == "gta" and worker_running(acct) else ""))
        for r, st, words in states(cfg, acct, day, now=t):
            out.append(f"  {r['task']}: {st}, {words}; began {str(r.get('began_at'))[11:16]}")
    return out
