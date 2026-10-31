"""Scout health: how many of each account's sources answered on the scout's last run (wave 2, 2026-10-18).

From 18:40 on 10-17, 11 and then all 12 of the Politics's YouTube channel feeds answered 404/500, and nothing said so
outside the scout's own log ("from 77 of 89 sources"). `factory.py doctor` and Clip Review's health line now say it,
per account. The page line is red when under 80% of the sources answered (GOOD), or when the scout's last two due runs
did not finish (missed or failed). Doctor is stricter: one failed run is a NO too.

Read only, never a network call, never makes a folder. The record is the scout's own:
- the NEWEST board it wrote, of any date (state/<account>/scout/<date>.json: sources, failed, fallback, updated). A
  scout that stopped keeps an old board, so it reads stale (red), never healthy. decisions.json is not a board.
- its launchd log (state/logs/<job>.log, the run's first and last lines: "... from N of M sources ..." then
  "board: ..."). Anything after the log's last "board: ..." line (a traceback, an error) is a run that did not finish.
  With a board, that is a failed run newer than the board when the log changed after it. With no board at all, the log
  is the record: its last finished run (timed by the log's change time), or a failed run when the log does not end
  with a finished run's "board: ..." line.

A YouTube feed that is down but was read through yt-dlp instead (scout.py, `fallback` on the board) counts as
answered: its videos reached the board.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from . import config as C

GOOD = 0.8                       # under this share of the sources answering, the line is red
GRACE = timedelta(minutes=15)    # a run that started this recently is still allowed to be running
EARLY = timedelta(minutes=5)     # a board this much before a due run counts for it (a run by hand at :35)
SCOUT_HOURS = [(h, 40) for h in range(5, 23)]   # timer.SCOUT_HOURS (05:40 to 22:40), when timer cannot be read
LOG_LINE = re.compile(r" scout: .* from (\d+) of (\d+) sources")
BOARD_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")   # a day's board; never decisions.json
TAIL = 20000                     # bytes of the log read from its end


def _root() -> Path:
    return C.STATE


def _schedule(slug: Optional[str] = None, cfg: Optional[C.Config] = None) -> List[Tuple[int, int]]:
    """The scouts' hourly starts (timer.SCOUT_HOURS). With a slug, that account's own agent's: an extra GTA account's
    scout runs at :46 or :52, not :40 (timer.account_agents), so doctor names its own minute."""
    try:
        from . import timer as TM
        if slug:
            for agent in list(TM.AGENTS) + TM.account_agents(cfg):
                if list(agent.get("args", []))[:2] == ["scout", slug] and isinstance(agent.get("at"), list):
                    own = sorted((int(h["Hour"]), int(h.get("Minute", 0))) for h in agent["at"])
                    if own:
                        return own
        hours = sorted((int(h["Hour"]), int(h.get("Minute", 0))) for h in TM.SCOUT_HOURS)
        return hours or SCOUT_HOURS
    except Exception:  # noqa: BLE001  (health never fails over the timer module)
        return SCOUT_HOURS


def _log_path(slug: str) -> Optional[Path]:
    """The launchd job's log for this account's scout (timer.AGENTS: args scout <slug>; an extra GTA account's own
    agent, <slug>-scout, once it is switched on: timer.account_agents)."""
    try:
        from . import timer as TM
        for agent in TM.AGENTS:
            if list(agent.get("args", []))[:2] == ["scout", slug]:
                return _root() / "logs" / f"{agent['name']}.log"
        for agent in TM.account_agents():
            if list(agent.get("args", []))[:2] == ["scout", slug]:
                return _root() / "logs" / f"{agent['name']}.log"
    except Exception:  # noqa: BLE001
        pass
    return None


def scout_accounts(cfg: C.Config) -> List[C.Account]:
    """The accounts with a scout: a block of their own in config/scout.json, and not a client (a client has none), nor
    one fed by hand (2026-10-29: "night": false installs no scout agent, so no run is ever due)."""
    try:
        data = json.loads((C.ROOT / "config" / "scout.json").read_text())
    except (OSError, ValueError):
        return []
    return [a for s, a in cfg.accounts.items() if isinstance(data.get(s), dict) and a.raw.get("owner") != "client"
            and not a.fed_by_hand()]


def label_of(failed: str) -> str:
    """'C-SPAN videos and Shorts: HTTPError: HTTP Error 500' -> the source's name (a search's name holds a colon)."""
    m = re.match(r"^(.*?): [A-Za-z_][\w.]*: ", failed or "")
    return m.group(1) if m else (failed or "").split(":")[0]


def _from_board(b: dict) -> Optional[dict]:
    try:
        at = datetime.fromisoformat(str(b["updated"]))
        n = int(b["sources"])
        failed = [str(x) for x in (b.get("failed") or [])]
    except (KeyError, TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    fb = [x.get("source") if isinstance(x, dict) else str(x) for x in (b.get("fallback") or [])]
    return {"at": at, "sources": n, "answered": max(0, n - len(failed)), "failed": [label_of(x) for x in failed],
            "fallback": [x for x in fb if x], "from": "board", "finished": True, "crash": None}


def _newest_board(slug: str) -> Optional[dict]:
    """The newest readable board of any date (by its name: one board per local day, the newest day last)."""
    d = _root() / slug / "scout"                 # never scout.scout_dir(): that one makes the folder
    try:
        names = sorted((p for p in d.iterdir() if BOARD_NAME.match(p.name)), key=lambda p: p.name, reverse=True)
    except OSError:
        return None
    for p in names[:3]:                          # one being written this instant reads broken: take the one before
        try:
            b = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        rec = _from_board(b) if isinstance(b, dict) else None
        if rec:
            return rec
    return None


def _log(slug: str) -> Optional[Tuple[List[str], datetime]]:
    """(the last TAIL bytes of the scout's launchd log as lines, the log's change time in UTC), or None: no log."""
    p = _log_path(slug)
    if p is None:
        return None
    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > TAIL:
                fh.seek(size - TAIL)
            raw = fh.read()
        at = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
    except OSError:
        return None
    lines = raw.decode("utf-8", "replace").splitlines()
    if size > TAIL and lines:
        lines = lines[1:]                        # cut mid-line
    return [ln.rstrip() for ln in lines], at


def _after_board(lines: List[str]) -> Tuple[int, List[str]]:
    """(the index of the log's last 'board: ' line, -1 if none; the non-empty lines after it: a run that failed)."""
    last = max((i for i, ln in enumerate(lines) if ln.startswith("board: ")), default=-1)
    return last, [ln for ln in lines[last + 1:] if ln.strip()]


def _crash(at: datetime, rest: List[str]) -> dict:
    words = rest[-1].strip() if rest else "its log has no finished run"
    return {"at": at, "line": words[:200]}


def _from_log(slug: str, now: datetime) -> Optional[dict]:
    """With no board at all: the log's last run, finished or failed. None: no log, or the first run still going."""
    got = _log(slug)
    if got is None:
        return None
    lines, at = got
    last, rest = _after_board(lines)
    failed = {"at": at, "sources": 0, "answered": 0, "failed": [], "fallback": [], "from": "log", "finished": False}
    if rest:
        return dict(failed, crash=_crash(at, rest))
    if last < 0:
        if now - at < GRACE:                     # launchd made the file and the first run has not printed yet
            return None
        return dict(failed, crash=_crash(at, []))
    for ln in reversed(lines[:last]):            # that run's own 'from N of M sources' line, above its board line
        if ln.startswith("board: "):
            break
        m = LOG_LINE.search(ln)
        if m:
            return {"at": at, "sources": int(m.group(2)), "answered": int(m.group(1)), "failed": [], "fallback": [],
                    "from": "log", "finished": True, "crash": None}
    return dict(failed, crash=_crash(at, ["its log's last run has no 'from N of M sources' line"]))


def _crash_after(slug: str, at: datetime) -> Optional[dict]:
    """A run that failed after the board stamped `at`: words after the log's last board line, the log changed later."""
    got = _log(slug)
    if got is None:
        return None
    lines, changed = got
    _, rest = _after_board(lines)
    return _crash(changed, rest) if rest and changed > at else None


def last_run(cfg: C.Config, acct: C.Account, now: Optional[datetime] = None) -> Optional[dict]:
    """The newest record of this account's scout run: {at (UTC), sources, answered, failed, fallback, from, finished,
    crash}. finished False: no finished run is on record, only a failed one (crash says when and its last line)."""
    now = now or cfg.now()
    rec = _newest_board(acct.slug)
    if rec is None:
        return _from_log(acct.slug, now)
    rec["crash"] = _crash_after(acct.slug, rec["at"])
    return rec


def _dues(now_local: datetime, n: int = 2) -> List[datetime]:
    """The latest n scheduled scout starts at least GRACE ago (local), newest first, across midnight."""
    cut = now_local - GRACE
    times = _schedule()
    out: List[datetime] = []
    for back in range(0, 3):
        day = (cut - timedelta(days=back)).date()
        for hh, mm in reversed(times):
            t = datetime.combine(day, dtime(hh, mm), now_local.tzinfo)
            if t <= cut:
                out.append(t)
                if len(out) == n:
                    return out
    return out + [cut] * (n - len(out))


def last_due(now_local: datetime) -> datetime:
    """The latest scheduled scout start at least GRACE ago (local): the run whose board should be there by now."""
    return _dues(now_local, 1)[0]


def prev_due(now_local: datetime) -> datetime:
    """The scheduled scout start before last_due (local; at 05:56 that is yesterday's 22:40)."""
    return _dues(now_local, 2)[1]


def judge(cfg: C.Config, acct: C.Account, now: Optional[datetime] = None) -> Optional[dict]:
    """last_run plus the verdict: share; low (under GOOD); stale (the last two due runs did not finish: the newest
    finished run is older than both); dead (no finished run on record, the last one failed); bad (any of them).
    crash: a run that failed after the newest board (a doctor NO; the page waits for the two-runs rule). None: no
    record at all."""
    now = now or cfg.now()
    rec = last_run(cfg, acct, now)
    if rec is None:
        return None
    finished = bool(rec.get("finished", True))
    share = rec["answered"] / rec["sources"] if finished and rec["sources"] else 0.0
    low = finished and share < GOOD
    stale = finished and rec["at"] < prev_due(now.astimezone(cfg.tz())) - EARLY
    dead = not finished
    return dict(rec, share=share, low=low, stale=stale, dead=dead, bad=low or stale or dead, name=acct.name,
                slug=acct.slug)


def _when(cfg: C.Config, at: datetime, now: datetime) -> str:
    loc, today = at.astimezone(cfg.tz()), now.astimezone(cfg.tz()).date()
    if loc.date() == today:
        return loc.strftime("%H:%M")
    if loc.date() == today - timedelta(days=1):
        return "yesterday " + loc.strftime("%H:%M")
    return loc.strftime("%m-%d %H:%M")


def review_line(cfg: C.Config, now: datetime, accts: Optional[List[C.Account]] = None) -> Tuple[str, str]:
    """('bad' | 'calm' | '', words) for Clip Review's health line. Red names only the accounts that are off."""
    keep = {a.slug for a in accts} if accts is not None else None
    rows = []
    for a in scout_accounts(cfg):
        if keep is not None and a.slug not in keep:
            continue
        try:
            j = judge(cfg, a, now)
        except Exception:  # noqa: BLE001  (the page never goes down over the scout)
            j = None
        if j:
            rows.append(j)
    if not rows:
        return "", ""
    bad = [j for j in rows if j["bad"]]
    if bad:
        said = []
        for j in bad:
            if j["dead"]:
                said.append(f"{C.possessive(j['name'])} scout failed on its last run "
                            f"({_when(cfg, j['crash']['at'], now)}).")
            elif j["stale"]:
                said.append(f"{C.possessive(j['name'])} scout has not finished a run since "
                            f"{_when(cfg, j['at'], now)}.")
            else:
                said.append(f"{C.possessive(j['name'])} scout: only {j['answered']} of {j['sources']} sources "
                            f"answered on its last run ({_when(cfg, j['at'], now)}).")
        if any(j["low"] and not j["stale"] for j in bad):
            said.append("The scout board lists which.")
        return "bad", " ".join(said)
    return "calm", "Scouts' last run: " + ", ".join(
        f"{j['name']} {j['answered']} of {j['sources']} sources ({_when(cfg, j['at'], now)})" for j in rows) + "."


def doctor(cfg: C.Config, now: Optional[datetime] = None) -> List[Tuple[Optional[bool], str, str]]:
    """(good, what, fix) per account for factory.py doctor; good None = nothing recorded yet (not a failure)."""
    now = now or cfg.now()
    out: List[Tuple[Optional[bool], str, str]] = []
    for a in scout_accounts(cfg):
        try:
            j = judge(cfg, a, now)
        except Exception as e:  # noqa: BLE001
            out.append((False, f"{a.name} scout: cannot read its last run ({type(e).__name__}: {e})",
                        f"look at state/{a.slug}/scout/"))
            continue
        if j is None:
            out.append((None, f"{a.name} scout: no run recorded yet (its next run at :{_schedule(a.slug, cfg)[0][1]:02d} "
                              f"writes one)", ""))
            continue
        log = _log_path(a.slug)
        where = f"state/logs/{log.name}" if log else "its log"
        by_hand = f"read the end of {where}; run it by hand: factory.py scout {a.slug}"
        crash = j.get("crash")
        if j["dead"]:
            out.append((False, f"{a.name} scout: its last run failed ({_when(cfg, crash['at'], now)}) and it has "
                        f"no board yet: {crash['line']}", by_hand))
            continue
        count = (f"{j['answered']} of {j['sources']} sources answered "
                 f"({int(j['share'] * 100)}%)")               # rounded down: 79.7% reads 79%, never a red "80%"
        words = f"{a.name} scout, last run {_when(cfg, j['at'], now)}: {count}"
        if j["fallback"]:
            words += f", {len(j['fallback'])} YouTube feed(s) down and read via yt-dlp"
        if j["failed"]:
            words += ": no answer from " + ", ".join(j["failed"][:4]) + (" ..." if len(j["failed"]) > 4 else "")
        if j["stale"]:
            first, last = _schedule(a.slug, cfg)[0], _schedule(a.slug, cfg)[-1]
            said = (f"{a.name} scout has not finished a run since {_when(cfg, j['at'], now)} (it runs every hour, "
                    f"{first[0]:02d}:{first[1]:02d} to {last[0]:02d}:{last[1]:02d})")
            if crash:
                said += f"; its log ends: {crash['line']}"
            out.append((False, said, f"factory.py timer status; {by_hand}"))
            continue
        if crash:
            out.append((False, f"{a.name} scout: its last run failed ({_when(cfg, crash['at'], now)}): "
                        f"{crash['line']}. The run before ({_when(cfg, j['at'], now)}): {count}", by_hand))
            continue
        out.append((not j["low"], words,
                    f"under {GOOD:.0%}: factory.py scout {a.slug} lists who did not answer; a YouTube channel feed "
                    f"that is down is read via yt-dlp by itself, so look at the others' URLs in config/scout.json; "
                    f"{where} has each run's line. Never update yt-dlp 20:30-06:15 (the night shift's cuts use it)"))
    return out
