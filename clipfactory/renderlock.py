"""The render queue: one heavy job at a time on this Mac, in the order they asked, for every account.

    factory.py renderlock run --account politics --what "patel: render" [--until 05:50] [--stop-at 06:15]
                              [--wait 90] [--turn 60] [--max-hold 240] -- /usr/bin/python3 build_all.py patel-yes-or-no
    factory.py renderlock status [--json]

What queues: the steps that fight over the CPU and the memory. The GTA maker queues its own intake (run.py: the
download, whisper, demucs, the finder) and each clip's render (run.py --pick ... --deliver); igcut queues its render.
A Claude task wraps every heavy step it runs by hand in `renderlock run` (the Politics's analyze.py and build_all.py,
a GTA run.py). Posting (post-due), stats, scout, collect, inbox and the review page never queue: they are light and
must never wait behind a render. `factory.py make` and `factory.py igcut` queue by themselves; never wrap them (give
them a deadline with CLIP_RENDERLOCK_UNTIL, below). A plain download (yt-dlp, fetch.py) needs no turn; analyze.py
does take one (it downloads, then transcribes).

How it holds:
- state/renderlock/lock is an flock. The kernel lets it go the moment its holder's process ends, however it ends (a
  crash, kill -9, a restart), so a dead holder can never block a night. holder.json says who holds it (pid, account,
  what, since, its time limit); a record whose process is gone is ignored.
- Every holder has a time limit (--max-hold, default 4 hours, the factory's own step limit; 0 = none): `run` stops its
  command at the limit and lets go. A holder still there 15 minutes past its limit shows as STUCK on doctor and on the
  review page, with its pid.
- `run` starts its command in a session of its own and records the command's pid. If `run` itself is killed hard
  (kill -9) the command can outlive it: the lock lets go with `run`, but its command still counts as rendering (status,
  doctor, the review page) and waiters wait for it up to its time limit. Past that, the next one takes its turn and
  the orphan shows beside the queue, with its pid.
- Waiters take turns in the order they came: each writes a ticket in state/renderlock/queue/ and refreshes it every
  2 seconds; only the oldest live ticket may take the lock. A ticket whose process is gone, or that has not been
  refreshed for a minute, is skipped and removed.
- How the two accounts alternate (the night shift, 2026-10-19: GTA's worker and the Politics's run share the Mac from
  21:00 to 06:15): every holder lets go after ONE step (the GTA maker holds per intake and per clip; the Politics per
  analyze.py, build_all.py, igcut or ytcut). The other account's step that queued meanwhile is older than the
  holder's next step, so it goes next: the accounts take turns step by step. One step can still be long (a GTA intake
  of a 30-minute stream, an analyze of a three-hour hearing), so a step of one account that has held the queue for
  --turn MIN (default 60) no longer holds the OTHER account up: the other account's oldest waiter runs beside it and
  says so (TURN, logged as "beside" with "turn"). A waiter of the same account still waits its turn.
- --until HH:MM is a deadline to START: at or after it, the command is not run, whether the queue is busy or free
  (exit 75, BUSY_EXIT). A step that never calls `run` (igcut, `make --background`) takes the same deadline from the
  environment: CLIP_RENDERLOCK_UNTIL=05:50 in front of it. `run --until` hands its command that variable too.
  --stop-at HH:MM is a time to be FINISHED by: the command is stopped then (exit 124, as at --max-hold), so a step
  started just before the night's last start (05:50) never runs into Andrew's morning (06:15).
- THE NIGHT'S OWN LIMITS (2026-10-19, review of the night shift): a step asked for between 21:00 and 06:15
  (shift.START, shift.DONE_BY) starts by that night's 05:50 (shift.LAST_START) or not at all, and `run` stops its
  command at 06:15, whether or not the step gave --until/--stop-at (a nine-hour Claude run that forgets a flag once
  must not render while Andrew signs). A flag can only tighten them. CLIP_RENDERLOCK_NIGHT=0 in front turns them off
  for one step (a by-hand render at night that must run past 05:50).
  --wait MIN is patience (default 90): waited that long, it runs anyway beside the holder and says so, so the queue
  never costs a clip, and a hung holder holds the other account up for 90 minutes at most (60 with --turn).
- Re-entrant: `run` hands its command CLIP_RENDERLOCK_TOKEN, so a factory step inside it does not queue behind its
  own parent.

state/renderlock/log.jsonl keeps each turn: who, what, how long it waited and held (the last 2,000 lines).
"""
from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import json
import os
import secrets
import shutil
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from . import config as C

POLL_S = 2.0                     # a waiter looks again this often (and refreshes its ticket)
STALE_TICKET_S = 60.0            # a ticket not refreshed for this long is skipped: its waiter stopped
DEFAULT_WAIT_S = 90 * 60         # waited this long, a step runs anyway beside the holder (one step, never a night)
DEFAULT_MAX_HOLD_S = 4 * 3600    # a holder's time limit: run.py's own step limit in the makers
TURN_S = 60 * 60                 # a holder of ONE account past this lets the OTHER account's oldest waiter run beside
STUCK_GRACE_S = 15 * 60          # past its limit by this much, a holder shows as stuck
SAY_EVERY_S = 300                # a waiter says it is still waiting this often
LOG_KEEP = 2000
LOG_MAX_BYTES = 600_000
TOKEN_ENV = "CLIP_RENDERLOCK_TOKEN"
UNTIL_ENV = "CLIP_RENDERLOCK_UNTIL"     # a deadline to start, HH:MM or epoch seconds, for steps that never call run
NIGHT_ENV = "CLIP_RENDERLOCK_NIGHT"     # "0": no night limits for this step (night_limits)
BUSY_EXIT = 75                   # EX_TEMPFAIL: not run, the queue was still busy at --until
TIMEOUT_EXIT = 124               # the command ran past --max-hold and was stopped


class Busy(Exception):
    """Still not this step's turn at its --until deadline: it did not run."""


@dataclasses.dataclass
class Turn:
    """What hold() gave: the lock (locked), a place beside the holder after a long wait (beside), or nothing new
    because this process, or the `run` that started it, already holds the lock (nested)."""
    locked: bool = False
    beside: bool = False
    nested: bool = False
    waited_s: float = 0.0
    token: str = ""


def root() -> Path:
    return C.STATE / "renderlock"           # looked up each time: tests move config.STATE


def _now() -> float:
    return time.time()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def _alive(pid: object) -> bool:
    try:
        n = int(pid)                        # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    try:
        os.kill(n, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, p)


def _log(event: str, **kw) -> None:
    """One line per turn in state/renderlock/log.jsonl. Never stops a render."""
    try:
        p = root() / "log.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps(dict({"at": _iso(_now()), "event": event, "pid": os.getpid()}, **kw)) + "\n")
        if p.stat().st_size > LOG_MAX_BYTES:
            keep = p.read_text().splitlines()[-LOG_KEEP:]
            _write_text(p, "\n".join(keep) + "\n")
    except OSError:
        pass


def _write_text(p: Path, text: str) -> None:
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


def _proc_start(pid: object) -> Optional[str]:
    """When a process started, as ps prints it (to tell a pid from a later process given the same number), or None."""
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True,  # type: ignore[arg-type]
                           timeout=5)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None


def _child_alive(h: dict) -> bool:
    """The command a `run` started is still running (and its pid was not handed to another process since)."""
    c = h.get("child")
    if not _alive(c):
        return False
    want = h.get("child_start")
    got = _proc_start(c) if want else None
    return not want or got is None or got == want


def _limit(h: dict) -> float:
    return float(h.get("max_hold_s") or DEFAULT_MAX_HOLD_S)


def _orphan(h: dict, gone: Optional[bool] = None) -> bool:
    """holder.json names a `run` that no longer holds the lock (killed hard) while the command it started still runs.
    gone: the caller already knows the recorded `run` does not hold the lock (it holds it itself, or probed it)."""
    if not h or not h.get("child"):
        return False
    if gone is None:
        gone = not _alive(h.get("pid"))
    return bool(gone) and _child_alive(h)


def _pid(h: dict) -> object:
    """The pid to look at: the command's, for an orphan."""
    return h.get("child") if h.get("orphan") else h.get("pid")


def _orphan_beside(prev: dict) -> None:
    """An orphan past its time limit: the next one takes its turn, and the orphan is shown beside the queue."""
    rec = {k: prev.get(k) for k in ("account", "what", "since", "since_ts", "max_hold_s", "child", "child_start")}
    rec.update(pid=prev.get("child"), orphan=True)
    _write(root() / "beside" / f"{prev.get('child')}-orphan.json", rec)
    _log("orphan", account=prev.get("account"), what=prev.get("what"), child=prev.get("child"))


def _beside_alive(b: dict) -> bool:
    return _child_alive(b) if b.get("orphan") else _alive(b.get("pid"))


def _prune_beside() -> None:
    d = root() / "beside"
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        if not _beside_alive(_read(f)):
            with contextlib.suppress(OSError):
                f.unlink()


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%H:%M")


def env_until(now: Optional[datetime] = None) -> Optional[float]:
    """The deadline in CLIP_RENDERLOCK_UNTIL (HH:MM, or epoch seconds as `run --until` hands it on), else None."""
    v = (os.environ.get(UNTIL_ENV) or "").strip()
    if not v:
        return None
    try:
        return float(v) if ":" not in v else until_epoch(v, now)
    except ValueError:
        return None


def night_limits(now: Optional[float] = None) -> Tuple[Optional[float], Optional[float]]:
    """(start by, stop at) for a step asked for between the night shift's START (21:00) and DONE_BY (06:15): that
    night's LAST_START (05:50) and DONE_BY (06:15), as epoch seconds; (None, None) in the daytime or with
    CLIP_RENDERLOCK_NIGHT=0. At 05:55 the start-by has just passed: the step does not start."""
    if os.environ.get(NIGHT_ENV, "").strip() == "0":
        return None, None
    from . import shift as SH
    t = datetime.fromtimestamp(_now() if now is None else now).astimezone()
    hm = t.strftime("%H:%M")
    if not (hm >= SH.START or hm < SH.DONE_BY):
        return None, None
    return until_epoch(SH.LAST_START, t), until_epoch(SH.DONE_BY, t)


def _tighter(given: Optional[float], limit: Optional[float]) -> Optional[float]:
    return limit if given is None else given if limit is None else min(given, limit)


# ------------------------------------------------------------------------------------------------------ the queue
def _tickets(prune: bool = True) -> List[Tuple[str, dict, float]]:
    """The live tickets, oldest first: (file name, ticket, seconds since it was refreshed). A ticket whose process is
    gone is dropped (its file removed, with prune); one that went a minute without a refresh (its waiter is stopped)
    is skipped, and keeps its place should it come back."""
    q = root() / "queue"
    out = []
    now = _now()
    for f in sorted(q.glob("*.json")) if q.is_dir() else []:
        t = _read(f)
        try:
            age = now - f.stat().st_mtime
        except OSError:
            continue
        if not t or not _alive(t.get("pid")):
            if prune and (t or age > STALE_TICKET_S):      # an empty file may be a ticket being written
                with contextlib.suppress(OSError):
                    f.unlink()
            continue
        if age > STALE_TICKET_S:
            continue
        out.append((f.name, t, age))
    return sorted(out, key=lambda x: not _hand_first(x[1]))     # stable: everything else keeps its arrival order


HAND_FIRST = ("05:00", "06:15")  # in this window a waiting GTA hand pick goes before anything else in the queue


def _hand_first(t: dict) -> bool:
    """A Gaming hand pick (--what "<HHMM> by hand") waiting between 05:00 and 06:15 takes the next turn. 10-25:
    the 21:00 and 23:00 hand picks queued behind a Politics re-cut and gave up at 05:50 (LESSONS row 5); a re-cut fixes
    a clip the slot already has, a hand pick fills a slot that has none."""
    if t.get("account") != "gaming" or "by hand" not in str(t.get("what") or ""):
        return False
    hm = datetime.fromtimestamp(_now()).astimezone().strftime("%H:%M")
    return HAND_FIRST[0] <= hm < HAND_FIRST[1]


_HELD: Dict[str, str] = {}               # this process's own hold: {"token": ...}


def _reentrant() -> str:
    """The token of a hold this process is already inside (its own, or the `run` that started it), else ''."""
    if _HELD.get("token"):
        return _HELD["token"]
    tok = os.environ.get(TOKEN_ENV, "")
    if not tok:
        return ""
    h = _read(root() / "holder.json")
    return tok if h.get("token") == tok and _alive(h.get("pid")) else ""


def _holder_words(h: dict, names: Optional[Dict[str, str]] = None, now: Optional[float] = None) -> str:
    if not h:
        return "nobody"
    now = now or _now()
    since = float(h.get("since_ts") or now)
    acct = (names or {}).get(str(h.get("account") or ""), str(h.get("account") or "?"))
    if h.get("orphan"):
        return (f"{acct}, {h.get('what') or 'a render'} ({_mins(now - since)}; its renderlock run ended, its command "
                f"pid {h.get('child')} still runs)")
    return f"{acct}, {h.get('what') or 'a render'} ({_mins(now - since)}, pid {h.get('pid')})"


def _mins(s: float) -> str:
    m = int(max(0.0, s) // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60:02d} min"


def _acquire(account: str, what: str, wait_s: Optional[float], until: Optional[float], max_hold_s: float,
             say: Callable[[str], None], names: Optional[Dict[str, str]] = None, turn_s: Optional[float] = TURN_S):
    """Take a ticket and wait for the lock. Returns (the open lock file, the holder record), or "busy" (the deadline
    passed first), "beside" (waited wait_s: run anyway) or ("turn", seconds waited) (the next in line, behind a step of
    ANOTHER account that has held the queue turn_s: run beside it)."""
    d = root()
    (d / "queue").mkdir(parents=True, exist_ok=True)
    fh = open(d / "lock", "a+")                      # never truncated; the lock is on the open file
    name = f"{time.time_ns():020d}-{os.getpid()}-{secrets.token_hex(3)}.json"
    tk = d / "queue" / name
    ticket = {"pid": os.getpid(), "account": account, "what": what, "queued_at": _iso(_now())}
    t0 = _now()
    told = 0.0
    beat = 0.0
    try:
        while True:
            if until is not None and _now() >= until:   # a deadline to START: busy or free, nothing starts after it
                fh.close()
                return "busy"
            if tk.exists():
                with contextlib.suppress(OSError):
                    os.utime(tk)                     # still here
            else:
                _write(tk, ticket)                   # first time (or removed by hand): same name, same place
            woke = bool(beat) and _now() - beat > STALE_TICKET_S / 2   # the Mac slept: every ticket looks old
            beat = _now()
            live = _tickets()
            head = live[0][0] if live else name
            if head == name and not woke:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    prev = _read(d / "holder.json")      # this process holds the lock, so its writer does not
                    if _orphan(prev, gone=True) and _now() < float(prev.get("since_ts") or 0) + _limit(prev):
                        fcntl.flock(fh, fcntl.LOCK_UN)   # its command still renders: wait for it, as for a holder
                    else:
                        if _orphan(prev, gone=True):
                            _orphan_beside(prev)
                        _prune_beside()
                        waited = _now() - t0
                        rec = {"pid": os.getpid(), "account": account, "what": what, "since": _iso(_now()),
                               "since_ts": _now(), "max_hold_s": max_hold_s, "waited_s": round(waited, 1),
                               "token": secrets.token_hex(8)}
                        _write(d / "holder.json", rec)
                        if told:
                            say(f"[render queue] my turn after {_mins(waited)}: {what}")
                        return fh, rec
            now = _now()
            if wait_s is not None and now - t0 >= wait_s:
                fh.close()
                return "beside"
            if turn_s and head == name and _other_long(account, turn_s, now):
                fh.close()
                return ("turn", now - t0)
            if not told or now - told >= SAY_EVERY_S:
                h = _read(d / "holder.json")
                who = (_holder_words(h, names) if h and _alive(h.get("pid")) and not _orphan(h)
                       else _holder_words(dict(h, orphan=True), names) if _orphan(h)
                       else "nobody (taking the lock)")
                ahead = sum(1 for n, _, _ in live if n < name)
                say(f"[render queue] waiting ({_mins(now - t0)}): rendering now is {who}"
                    + (f"; {ahead} ahead" if ahead else ""))
                told = now
            time.sleep(POLL_S)
    except BaseException:
        with contextlib.suppress(OSError):
            fh.close()
        raise
    finally:
        with contextlib.suppress(OSError):
            tk.unlink()


def _other_long(account: str, turn_s: float, now: float) -> bool:
    """The queue's holder is a step of ANOTHER account that has held it turn_s or more (an orphan counts: its command
    still renders)."""
    h = _read(root() / "holder.json")
    if not h or str(h.get("account") or "") == account:
        return False
    if not ((_alive(h.get("pid")) and not _orphan(h)) or _orphan(h)):
        return False
    return now - float(h.get("since_ts") or now) >= turn_s


def _release(fh, rec: dict, waited: float) -> None:
    d = root()
    h = _read(d / "holder.json")
    if h.get("token") == rec.get("token"):
        with contextlib.suppress(OSError):
            (d / "holder.json").unlink()
    with contextlib.suppress(OSError):
        fcntl.flock(fh, fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        fh.close()
    _log("left", account=rec.get("account"), what=rec.get("what"), waited_s=round(waited, 1),
         held_s=round(_now() - float(rec.get("since_ts") or _now()), 1))


@contextlib.contextmanager
def hold(account: str, what: str, wait_s: Optional[float] = DEFAULT_WAIT_S, until: Optional[float] = None,
         max_hold_s: float = DEFAULT_MAX_HOLD_S, say: Optional[Callable[[str], None]] = None,
         log: Optional[Path] = None, names: Optional[Dict[str, str]] = None,
         busy: Optional[Callable[[str], BaseException]] = None, turn_s: Optional[float] = TURN_S) -> Iterator[Turn]:
    """Wait for this step's turn in the render queue, hold it for the block, then let the next one go.
    say: where the waiting lines go (default: the log file if given, else nowhere). until: an epoch second to start
    by (default: CLIP_RENDERLOCK_UNTIL, if set); at or after it the block never runs and Busy is raised (or `busy`,
    the caller's own error, e.g. igcut's CutError). wait_s: waited this long, the block runs anyway. turn_s: next in
    line behind a step of another account that has held the queue this long, the block runs beside it (0 = off).
    Between 21:00 and 06:15 the night's own start-by (05:50, night_limits) holds as well: a later until is tightened
    to it, and a step given none gets it."""
    def tell(text: str) -> None:
        if say:
            say(text)
        if log:
            with contextlib.suppress(OSError), open(log, "a") as f:
                f.write(text + "\n")

    if _reentrant():
        yield Turn(nested=True, token=_reentrant())
        return
    if until is None:
        until = env_until()
    night, _ = night_limits()
    if night is not None and (until is None or until > night):
        if until is None:
            tell(f"[render queue] {what}: the night shift's own limit: it starts by {_clock(night)} or not at all")
        until = night
    try:
        got = _acquire(account, what, wait_s, until, max_hold_s, tell, names, turn_s)
    except OSError as e:          # the queue's own folder will not open: the step still runs (the queue never costs a clip)
        tell(f"[render queue] could not queue ({type(e).__name__}: {e}); running without it")
        _log("unqueued", account=account, what=what, error=f"{type(e).__name__}: {e}"[:200])
        yield Turn()
        return
    if got == "busy":
        h = _read(root() / "holder.json")
        live = h and ((_alive(h.get("pid")) and not _orphan(h)) or _orphan(h))
        _log("gave-up", account=account, what=what, until=_iso(until or 0), holder=h.get("what") if live else None,
             holder_account=h.get("account") if live else None)
        why = (f"not run: still not its turn at its {_clock(until or 0)} deadline (rendering now: "
               f"{_holder_words(dict(h, orphan=_orphan(h)), names)})" if live
               else f"not run: it is past its {_clock(until or 0)} deadline")
        if log:                    # the log keeps it; the caller says it once, from the error
            with contextlib.suppress(OSError), open(log, "a") as f:
                f.write(f"[render queue] {what}: {why}\n")
        raise (busy or Busy)(why)
    if got == "beside" or (isinstance(got, tuple) and got[0] == "turn"):
        h = _read(root() / "holder.json")
        waited = float(wait_s or 0) if got == "beside" else float(got[1])
        if got != "beside":
            held = _now() - float(h.get("since_ts") or _now())
            tell(f"[render queue] {_holder_words(h, names)} has held the queue {_mins(held)}, past its turn "
                 f"({_mins(turn_s or 0)}): {what} runs beside it")
        else:
            tell(f"[render queue] waited {_mins(wait_s or 0)}; running anyway beside {_holder_words(h, names)}")
        rec = {"pid": os.getpid(), "account": account, "what": what, "since": _iso(_now()), "since_ts": _now(),
               "beside": h.get("what") or "", "token": secrets.token_hex(8)}
        side = root() / "beside" / f"{os.getpid()}-{rec['token']}.json"
        _write(side, rec)
        _log("beside", account=account, what=what, waited_s=round(waited, 1), holder=h.get("what"),
             **({"turn": True, "holder_account": h.get("account")} if got != "beside" else {}))
        try:
            yield Turn(beside=True, waited_s=waited, token=rec["token"])
        finally:
            with contextlib.suppress(OSError):
                side.unlink()
        return
    fh, rec = got
    waited = float(rec.get("waited_s") or 0)
    _log("took", account=account, what=what, waited_s=waited)
    _HELD["token"] = rec["token"]
    try:
        yield Turn(locked=True, waited_s=waited, token=rec["token"])
    finally:
        _HELD.pop("token", None)
        _release(fh, rec, waited)


def _update_holder(token: str, **kw) -> None:
    p = root() / "holder.json"
    h = _read(p)
    if h.get("token") == token:
        h.update(kw)
        _write(p, h)


# ------------------------------------------------------------------------------------------------------ run
def _stop_group(p: subprocess.Popen) -> None:
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 10)):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            p.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def until_epoch(hhmm: str, now: Optional[datetime] = None) -> float:
    """--until HH:MM as an epoch second: the time the clock reads it that is NEAREST now (yesterday, today or
    tomorrow). At 21:00, 05:50 is tomorrow morning; at 05:55, 05:50 has just passed (so nothing new starts); at
    01:00, 23:30 was last night. The flip is 12 hours away: from 17:50 on, 05:50 means the next morning."""
    h, _, m = hhmm.strip().partition(":")
    hh, mm = int(h), int(m or 0)
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(hhmm)
    now = now or datetime.now().astimezone()
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    best = min((at + timedelta(days=k) for k in (-1, 0, 1)), key=lambda x: abs((x - now).total_seconds()))
    return best.timestamp()


def _say(text: str) -> None:
    print(text, flush=True)              # before the command's own output, which goes to the same place


def run(account: str, what: str, cmd: List[str], wait_s: Optional[float] = DEFAULT_WAIT_S,
        until: Optional[float] = None, max_hold_s: Optional[float] = DEFAULT_MAX_HOLD_S,
        say: Callable[[str], None] = _say, names: Optional[Dict[str, str]] = None,
        stop_at: Optional[float] = None, turn_s: Optional[float] = TURN_S) -> int:
    """`factory.py renderlock run`: wait for the turn, run the command (its output goes where this one's goes), let go.
    Returns the command's exit status (128 + N when a signal N ended it), BUSY_EXIT when it did not start by --until
    (or its turn came at or after --stop-at), TIMEOUT_EXIT when it ran past --max-hold or reached --stop-at and was
    stopped (max_hold_s None or 0: no limit), 127/126 when the command is not there or will not run. Between 21:00
    and 06:15 the night's own limits hold as well (night_limits: start by 05:50, stopped at 06:15), whatever the flags
    say: a flag only tightens them."""
    if not cmd:
        raise SystemExit("renderlock run: say the command after --")
    night_until, night_stop = night_limits()
    given = until if until is not None else env_until()
    if night_until is not None and (given is None or stop_at is None):
        say(f"[render queue] {what}: the night shift's own limits (a flag left out): it starts by "
            f"{_clock(_tighter(given, night_until) or 0)} or not at all, and is stopped at "
            f"{_clock(_tighter(stop_at, night_stop) or 0)} if it still runs")
    until, stop_at = _tighter(given, night_until), _tighter(stop_at, night_stop)
    limit = float(max_hold_s) if max_hold_s and max_hold_s > 0 else None
    try:
        with hold(account, what, wait_s=wait_s, until=until, max_hold_s=limit or 0, say=say, names=names,
                  turn_s=turn_s) as turn:
            by_stop = False
            if stop_at is not None:
                left = stop_at - _now()
                if left <= 0:
                    say(f"[render queue] {what}: not run: its turn came at or after its {_clock(stop_at)} stop time")
                    _log("gave-up", account=account, what=what, until=_iso(stop_at), holder=None, holder_account=None)
                    return BUSY_EXIT
                if limit is None or left < limit:
                    limit, by_stop = left, True
            env = dict(os.environ)
            if turn.token and not turn.nested:
                env[TOKEN_ENV] = turn.token
            if until is not None:
                env[UNTIL_ENV] = str(int(until))     # a step it starts that outlives it (make --background) keeps it
            awake = None
            if shutil.which("caffeinate") and os.environ.get("CLIP_FACTORY_OFFLINE") != "1":
                with contextlib.suppress(OSError):    # the Mac stays awake while it renders
                    awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                p = subprocess.Popen(cmd, env=env, start_new_session=True)
            except (FileNotFoundError, PermissionError, NotADirectoryError) as e:
                if awake is not None:
                    with contextlib.suppress(OSError):
                        awake.terminate()
                missing = not isinstance(e, PermissionError)
                say(f"[render queue] {what}: cannot run {cmd[0]!r}: "
                    + ("no such command (give its full path)" if missing else "it is not allowed to run"))
                _log("not-started", account=account, what=what, error=f"{type(e).__name__}"[:80])
                return 127 if missing else 126
            if turn.locked:
                _update_holder(turn.token, child=p.pid, child_start=_proc_start(p.pid), cmd=" ".join(cmd)[:300])
            caught: List[int] = []

            def stop(signum, _frame):
                caught.append(signum)
                _stop_group(p)
                raise SystemExit(128 + signum)

            old = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
            try:
                rc = p.wait(timeout=limit)
                if rc < 0:                    # ended by a signal: the shell's way of saying so
                    rc = 128 - rc
            except subprocess.TimeoutExpired:
                _stop_group(p)
                if by_stop:
                    say(f"[render queue] {what}: still running at its {_clock(stop_at or 0)} stop time and was "
                        f"stopped; the queue moves on")
                else:
                    say(f"[render queue] {what}: ran past {_mins(limit or 0)} and was stopped; the queue moves on")
                _log("stopped", account=account, what=what, max_hold_s=limit, **({"stop_at": _iso(stop_at or 0)}
                                                                                     if by_stop else {}))
                rc = TIMEOUT_EXIT
            finally:
                for s, h in old.items():
                    signal.signal(s, h)
                if awake is not None:
                    with contextlib.suppress(OSError):
                        awake.terminate()
            return rc
    except Busy as e:
        say(f"[render queue] {what}: {e}")
        return BUSY_EXIT


# ------------------------------------------------------------------------------------------------------ status
def _probe_free() -> bool:
    """True when nobody holds the lock (taken and let go at once). Only asked when a live pid claims it."""
    p = root() / "lock"
    if not p.exists():
        return True
    with open(p, "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(f, fcntl.LOCK_UN)
    return True


def recent(n: int = 5) -> List[dict]:
    try:
        lines = (root() / "log.jsonl").read_text().splitlines()[-max(n * 3, n):]
    except OSError:
        return []
    out = []
    for ln in lines:
        with contextlib.suppress(ValueError):
            d = json.loads(ln)
            if isinstance(d, dict):
                out.append(d)
    return out[-n:]


def status(now: Optional[float] = None) -> dict:
    """{state: free | busy | stuck, holder, stale (a holder record whose process is gone), waiting, beside, recent}.
    Reads only: a stale record is reported, never cleaned here (the next holder writes over it)."""
    now = now or _now()
    d = root()
    h = _read(d / "holder.json")
    holder, stale = None, None
    if h:
        held_now = _alive(h.get("pid")) and not _probe_free()
        if held_now or _orphan(h, gone=True):         # an orphan: its `run` was killed hard, its command renders on
            held = now - float(h.get("since_ts") or now)
            holder = dict(h, held_s=round(held, 1), over=held > _limit(h) + STUCK_GRACE_S, orphan=not held_now)
            holder.pop("token", None)
        else:
            stale = {k: v for k, v in h.items() if k != "token"}
    waiting = [dict(t, ticket=n, waited_s=round(now - _ticket_t0(n, now), 1)) for n, t, _ in _tickets(prune=False)]
    beside = []
    for f in sorted((d / "beside").glob("*.json")) if (d / "beside").is_dir() else []:
        b = _read(f)
        if b and _beside_alive(b):
            b.pop("token", None)
            beside.append(dict(b, held_s=round(now - float(b.get("since_ts") or now), 1)))
    state = "stuck" if holder and holder["over"] else "busy" if holder or beside else "free"
    return {"state": state, "holder": holder, "stale": stale, "waiting": waiting, "beside": beside,
            "recent": recent()}


def _ticket_t0(name: str, now: float) -> float:
    try:
        return int(name.split("-", 1)[0]) / 1e9
    except ValueError:
        return now


def _names(cfg: Optional[C.Config]) -> Dict[str, str]:
    if cfg is None:
        return {}
    return {slug: a.name for slug, a in cfg.accounts.items()}


def _hhmm(cfg: Optional[C.Config], ts: float) -> str:
    try:
        tz = cfg.tz() if cfg is not None else None
    except Exception:  # noqa: BLE001
        tz = None
    d = datetime.fromtimestamp(ts, timezone.utc)
    return (d.astimezone(tz) if tz else d.astimezone()).strftime("%H:%M")


def lines(cfg: Optional[C.Config] = None, st: Optional[dict] = None) -> List[str]:
    """`renderlock status`, in plain words."""
    st = st or status()
    names = _names(cfg)
    out = []
    h = st["holder"]
    if h:
        limit = float(h.get("max_hold_s") or DEFAULT_MAX_HOLD_S)
        out.append(("STUCK: " if h["over"] else "rendering: ") + _holder_words(h, names)
                   + f", since {_hhmm(cfg, float(h.get('since_ts') or _now()))}, its limit {_mins(limit)}"
                   + (f"; waited {_mins(float(h.get('waited_s') or 0))} first" if float(h.get("waited_s") or 0) >= 60 else ""))
        if h["over"]:
            out.append(f"  it is past its limit: look at it (ps -p {_pid(h)}); stop it if it hangs, and the "
                       f"queue moves on")
        elif h.get("orphan"):
            out.append(f"  the queue waits for it up to its limit (ps -p {_pid(h)}); stop it (kill -TERM -{_pid(h)}) "
                       f"if it should not finish")
    else:
        out.append("free: nothing is rendering in the queue")
    for w in st["waiting"]:
        acct = names.get(str(w.get("account") or ""), w.get("account"))
        out.append(f"  waiting: {acct}, {w.get('what')} ({_mins(float(w.get('waited_s') or 0))}, pid {w.get('pid')})")
    for b in st["beside"]:
        acct = names.get(str(b.get("account") or ""), b.get("account"))
        why = "its renderlock run ended, past its limit" if b.get("orphan") else "waited past its patience"
        out.append(f"  beside the queue ({why}): {acct}, {b.get('what')} "
                   f"({_mins(float(b.get('held_s') or 0))}, pid {b.get('pid')})")
    if st["stale"] and not h:
        s = st["stale"]
        out.append(f"  (the last holder, pid {s.get('pid')} {s.get('what')!r}, ended without letting go; the lock let "
                   f"go with it, nothing to do)")
    for e in st["recent"]:
        if e.get("event") in ("left", "stopped", "gave-up", "beside"):
            acct = names.get(str(e.get("account") or ""), e.get("account"))
            ts = _parse(e.get("at"))
            when = _hhmm(cfg, ts) if ts else "?"
            word = {"left": f"held {_mins(float(e.get('held_s') or 0))}, waited {_mins(float(e.get('waited_s') or 0))}",
                    "stopped": "STOPPED at its limit", "gave-up": "not run (past its deadline)",
                    "beside": "ran beside the queue"}[e["event"]]
            out.append(f"  last: {when} {acct}, {e.get('what')}: {word}")
    return out


def _parse(iso: object) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except ValueError:
        return None


def health_words(cfg: Optional[C.Config] = None, now: Optional[float] = None) -> Tuple[str, str]:
    """(level, words) for the review page: ('', '') when nothing is rendering, ('busy', ...) while a render holds the
    queue, ('stuck', ...) once a holder is past its limit. Never raises."""
    try:
        st = status(now)
    except Exception:  # noqa: BLE001  (the review page never goes down over this)
        return "", ""
    names = _names(cfg)
    h = st["holder"]
    n = len(st["waiting"])
    more = f" {n} more waiting." if n else ""
    if h and h["over"]:
        return "stuck", (f"The render queue is stuck: {_holder_words(h, names)} has held it since "
                         f"{_hhmm(cfg, float(h.get('since_ts') or 0))}, past its limit. Nothing else renders until it "
                         f"stops (factory.py renderlock status).{more}")
    if h:
        gone = (f" Its renderlock run ended; its command (pid {h.get('child')}) still runs, and the queue waits for it "
                f"up to its limit." if h.get("orphan") else "")
        return "busy", (f"Rendering now: {names.get(str(h.get('account')), h.get('account'))}, {h.get('what')}, since "
                        f"{_hhmm(cfg, float(h.get('since_ts') or 0))}.{gone}{more}")
    if st["beside"]:
        b = st["beside"][0]
        if b.get("orphan"):
            return "stuck", (f"A render is running past its limit outside the queue: "
                             f"{names.get(str(b.get('account')), b.get('account'))}, {b.get('what')} (pid {b.get('pid')})."
                             f" Look at it (factory.py renderlock status).{more}")
        return "busy", f"Rendering now: {names.get(str(b.get('account')), b.get('account'))}, {b.get('what')}.{more}"
    return "", ""


def doctor(cfg: Optional[C.Config] = None) -> List[Tuple[bool, str, str]]:
    """(good, what, fix) lines for factory.py doctor."""
    try:
        st = status()
    except Exception as e:  # noqa: BLE001
        return [(False, f"render queue: cannot read state/renderlock ({type(e).__name__}: {e})",
                 "look at the folder's permissions")]
    names = _names(cfg)
    h = st["holder"]
    out: List[Tuple[bool, str, str]] = []
    if h and h["over"]:
        out.append((False, f"render queue STUCK: {_holder_words(h, names)}, past its limit",
                    f"look at it (ps -p {_pid(h)}); stop it if it hangs (kill {_pid(h)}), and the queue "
                    f"moves on"))
    elif h:
        out.append((True, f"render queue: rendering {_holder_words(h, names)}"
                    + (f", {len(st['waiting'])} waiting" if st["waiting"] else ""), ""))
    else:
        out.append((True, "render queue: free" + (f", {len(st['waiting'])} waiting" if st["waiting"] else ""), ""))
    for b in st["beside"]:
        if b.get("orphan"):
            out.append((False, f"render queue: {names.get(str(b.get('account')), b.get('account'))}, {b.get('what')} "
                        f"is still running past its limit after its renderlock run ended (pid {b.get('pid')})",
                        f"look at it (ps -p {b.get('pid')}); stop it if it hangs (kill -TERM -{b.get('pid')})"))
            continue
        out.append((True, f"render queue: {names.get(str(b.get('account')), b.get('account'))}, {b.get('what')} is "
                    f"running beside it (it waited past its patience)", ""))
    return out
