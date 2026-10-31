"""Claude's week: the usage meter, its warnings and the night shift's reserve (Andrew, 2026-10-27: "build the usage
warning and the night reserve so the night shift never runs out again").

The week of 10-19 ran out on Sunday 10-25 at about 13:15: every Claude task failed on "You've hit your weekly limit"
until the Monday 14:00 reset, so the 10-26 board was lost (Politics 0 of 15, 24 slots). Measured 10-27 from the
transcripts: the scheduled clip tasks use 1 to 3% of the week a day (the Politics night about 1.1%), the chats the rest
(41% on 10-20, 20% on the 10-26 fix day). So the warning goes where the usage goes: doctor, the 06:30 notice, a Mac
notice when the week gets worse (the 30-minute launchd check), and every Claude chat through a hook. The reserve keeps
the daytime extras off the night's share.

How the % is read (no sign-in, no network):
- the desktop app's own readings, ~/Library/Application Support/Claude/plan-usage-history.json ("sd" = the weekly
  bar, "fh" = the 5-hour one, per org). The app reads them only while its usage tray was opened in the last day, so
  they come hours or days apart;
- after the newest reading this week, an estimate: every Claude Code call in ~/.claude/projects/**/*.jsonl priced at
  API rates, at $30 per 1% (six pairs of readings 10-19 to 10-27 gave $27 to $37 per 1%; $30 reads a little high,
  the safe side). claude.ai chats are not in the transcripts: the next reading puts them back. The estimated part
  carries its own margin (15% of it) on top of the fixed 3%.

The reserve, recomputed every time:
- need_night: the night's own tasks (config/usage.json "night_tasks": the Politics night, both gap-fills, the Politics
  prep and morning fill, REACTIONS) a day, the most of the last 7 days (at least night_floor_pct), times the days to
  the reset, plus the margin;
- need_all: the same for every scheduled task;
- room: 100 - used - need_all, what chats can use before they eat into the clip tasks' week.
Levels: ok; watch (room under 15%, or the last 24 hours' pace runs out before the reset); low (room under 5%);
reserve (no room: `usage gate` stops every daytime extra, the night's tasks still run); out (the week is used up).

Files (state/usage/): scan.json (each transcript's read offset and its cost in 10-minute buckets, 8 days kept),
now.json (the last status, which the chat hook reads), alerts.json (the level last sent as a Mac notice this week),
chats.json (the level each chat was last told).
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover  (Python 3.8)
    ZoneInfo = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "config" / "usage.json"
HISTORY = Path(os.path.expanduser("~/Library/Application Support/Claude/plan-usage-history.json"))
PROJECTS = Path(os.path.expanduser("~/.claude/projects"))
BUCKET = 600                       # seconds: each transcript's cost is kept in 10-minute buckets
KEEP = timedelta(days=8)           # buckets and transcripts older than this are dropped (a week and a day)
FRESH = timedelta(minutes=40)      # now.json younger than this is used as is (the launchd check runs every 30 min)
HOOK_STALE = timedelta(hours=3)    # the chat hook says nothing from a status older than this
TAIL_KEYS = 64                     # message keys kept per transcript: a call written across two reads counts once
STOP = 3                           # `usage gate`'s exit code: the task skips this run
LEVELS = ("ok", "watch", "low", "reserve", "out")
UNCERTAIN = 0.15                   # the estimate's own margin: $27 to $37 per 1% around the $30 used
# API prices per million tokens (input, output, cache read, 5-minute cache write, 1-hour cache write): the prices the
# $30 per 1% was measured with (memory usage-dollar-calibration, 2026-10-26). Unknown models count as Opus.
PRICE = {"opus": (4, 20, 0.20, 5, 8), "fable": (4, 20, 0.20, 5, 8), "sonnet": (2, 10, 0.20, 2.5, 4),
         "haiku": (1, 5, 0.10, 1.25, 2)}
TAG = re.compile(rb'<scheduled-task name=\\?"([^"\\]+)')
DEFAULTS = {
    "org": "",                               # the app's org whose readings count; "" = the newest reading's
    "reset": "2026-10-26T20:00:00Z",         # one weekly reset (Mon 14:00 MDT); the next ones every 7 days
    "timezone": "America/Denver",
    "dollars_per_pct": 30.0,
    "margin_pct": 3.0,
    "watch_room_pct": 15.0,
    "low_room_pct": 5.0,
    "night_floor_pct": 1.5,                  # a day of the night's tasks is never counted under this
    "all_floor_pct": 2.5,                    # nor a day of every scheduled task
    "night_tasks": ["hearing-clip-sweep-2", "politics-early-gap-fill", "gaming-morning-sweep", "politics-prep-auto",
                    "politics-morning-fill-auto", "gta-reactions-daily-auto"],
    "gated_tasks": ["bench-builder-auto", "clip-coach", "clip-weekly-report", "clip-results-coach-auto", "clip-fixer",
                    "politics-news-desk-auto", "clip-morning-review", "clip-factory-views-sync-auto"],
}
TASKS = Path(os.path.expanduser("~/.claude/scheduled-tasks"))   # each task's SKILL.md (its STEP 0 runs the gate)
LIVE = (HISTORY, PROJECTS, TASKS)  # in a test run (CLIP_FACTORY_OFFLINE) these are never read: no real usage in a test


def _offline(path: Path) -> bool:
    """A test run pointed at this Mac's real usage files (the app's readings, the transcripts, the tasks)."""
    return bool(os.environ.get("CLIP_FACTORY_OFFLINE")) and Path(path) in LIVE


# ---------------------------------------------------------------------------------------------------- settings, files

def settings(path: Optional[Path] = None) -> dict:
    out = dict(DEFAULTS)
    try:
        out.update({k: v for k, v in json.loads(Path(path or SETTINGS).read_text()).items() if not k.startswith("_")})
    except (OSError, ValueError):
        pass
    return out


def state_dir() -> Path:
    try:
        from . import config as C
        return C.STATE / "usage"
    except ImportError:   # run as a script (the chat hook): the live folder, or CLIP_FACTORY_STATE
        return Path(os.environ.get("CLIP_FACTORY_STATE") or ROOT / "state") / "usage"


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, path)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _tz(conf: dict):
    return ZoneInfo(conf.get("timezone") or "America/Denver") if ZoneInfo else timezone(timedelta(hours=-6))


# ----------------------------------------------------------------------------------------- the week and the readings

def week(now: datetime, conf: dict) -> Tuple[datetime, datetime]:
    """(start, end) of the weekly window holding `now`: the anchor reset, then every 7 days."""
    anchor = _ts(conf["reset"])
    k = math.floor((now - anchor) / timedelta(days=7))
    start = anchor + timedelta(days=7 * k)
    return start, start + timedelta(days=7)


def readings(path: Optional[Path] = None) -> List[dict]:
    """The app's samples that carry the weekly bar: [{"t": datetime, "org": id, "sd": %, "fh": %}], oldest first."""
    path = Path(path or HISTORY)
    data = {} if _offline(path) else (_read(path) or {})
    out = []
    for s in data.get("samples") or []:
        u = s.get("u") or {}
        if isinstance(s.get("t"), (int, float)) and isinstance(u.get("sd"), (int, float)):
            out.append({"t": datetime.fromtimestamp(s["t"] / 1000, timezone.utc), "org": s.get("org", ""),
                        "sd": float(u["sd"]), "fh": float(u.get("fh") or 0)})
    return sorted(out, key=lambda r: r["t"])


def last_reading(samples: List[dict], conf: dict, start: datetime, now: datetime) -> Optional[dict]:
    """The newest reading of the clip account's org inside this week (None: none yet, or the org is unknown)."""
    org = conf.get("org") or (samples[-1]["org"] if samples else "")
    mine = [r for r in samples if r["org"] == org and start <= r["t"] <= now]
    return mine[-1] if mine else None


# ------------------------------------------------------------------------------------------------ the transcript scan

def _price(model: str) -> tuple:
    m = (model or "").lower()
    return next((v for k, v in PRICE.items() if k in m), PRICE["opus"])


def call_cost(msg: dict) -> float:
    """One assistant message's usage at API prices, in dollars."""
    u = msg.get("usage") or {}
    pin, pout, pread, pw5, pw1 = _price(msg.get("model", ""))
    cc = u.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens") or 0
    w1 = cc.get("ephemeral_1h_input_tokens") or 0
    if not (w5 or w1):
        w1 = u.get("cache_creation_input_tokens") or 0
    return ((u.get("input_tokens") or 0) * pin + (u.get("output_tokens") or 0) * pout
            + (u.get("cache_read_input_tokens") or 0) * pread + w5 * pw5 + w1 * pw1) / 1e6


def _session(path: Path, projects: Path) -> str:
    """The session a transcript belongs to: its own name, or (a subagent's) the folder of the session that ran it."""
    rel = path.relative_to(projects).parts
    return path.stem if len(rel) == 2 else rel[1]


def _task_of(path: Path) -> str:
    """The scheduled task that started this session ('chat' for any other), from its first user message."""
    try:
        with open(path, "rb") as fh:
            for i, line in enumerate(fh):
                if i > 60:
                    break
                if b'"type":"user"' in line:
                    m = TAG.search(line[:4000])
                    return m.group(1).decode() if m else "chat"
    except OSError:
        pass
    return "chat"


def scan(projects: Optional[Path] = None, now: Optional[datetime] = None, state: Optional[Path] = None) -> dict:
    """Read what each transcript changed in the last 8 days added since the last scan; save and return the record."""
    projects = Path(projects or PROJECTS)
    now = now or datetime.now(timezone.utc)
    state = state or state_dir()
    if _offline(projects):
        return {"at": now.isoformat(), "files": {}}
    rec = _read(state / "scan.json") or {}
    files: Dict[str, dict] = rec.get("files") or {}
    cutoff = (now - KEEP).timestamp()
    paths = sorted(projects.glob("**/*.jsonl"), key=lambda p: len(p.relative_to(projects).parts))   # sessions first
    sessions: Dict[str, str] = {}
    for p in paths:
        try:
            st = p.stat()
        except OSError:
            continue
        key = str(p)
        if st.st_mtime < cutoff:
            files.pop(key, None)
            continue
        f = files.get(key)
        if f is None or st.st_size < f.get("off", 0):          # new, or rewritten: from the top
            sid = _session(p, projects)
            task = _task_of(p) if len(p.relative_to(projects).parts) == 2 else sessions.get(sid, "chat")
            f = files[key] = {"off": 0, "task": task, "keys": [], "b": {}}
        if len(p.relative_to(projects).parts) == 2:
            sessions[p.stem] = f["task"]
        if st.st_size == f["off"]:
            continue
        seen = list(f.get("keys") or [])
        b = f.setdefault("b", {})
        with open(p, "rb") as fh:
            fh.seek(f["off"])
            for line in fh:
                if not line.endswith(b"\n"):                     # a line still being written: next time
                    break
                f["off"] += len(line)
                if b'"usage"' not in line or b'"assistant"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                msg = d.get("message") or {}
                if d.get("type") != "assistant" or not msg.get("usage") or not d.get("timestamp"):
                    continue
                k = f"{msg.get('id')}|{d.get('requestId')}"
                if k in seen:
                    continue
                seen.append(k)
                t = _ts(d["timestamp"]).timestamp()
                if t < cutoff:
                    continue
                slot = str(int(t // BUCKET))
                b[slot] = round(b.get(slot, 0.0) + call_cost(msg), 6)
        f["keys"] = seen[-TAIL_KEYS:]
        for slot in [s for s in b if int(s) * BUCKET < cutoff]:
            del b[slot]
    for key in [k for k in files if not Path(k).exists()]:
        if not files[key].get("b"):
            del files[key]
    rec = {"at": now.isoformat(), "files": files}
    _write(state / "scan.json", rec)
    return rec


def dollars(rec: dict, a: datetime, b: datetime, only=None) -> float:
    """Dollars spent between a and b (each 10-minute bucket counted when its middle falls inside); `only` picks the
    tasks (a function of the task name)."""
    lo, hi = a.timestamp(), b.timestamp()
    total = 0.0
    for f in (rec.get("files") or {}).values():
        if only is not None and not only(f.get("task", "chat")):
            continue
        for slot, c in (f.get("b") or {}).items():
            mid = int(slot) * BUCKET + BUCKET / 2
            if lo <= mid < hi:
                total += c
    return total


def task_days(rec: dict, now: datetime, conf: dict, days: int = 7) -> Dict[str, Dict[str, float]]:
    """The last `days` whole local days: {"YYYY-MM-DD": {"night": $, "all": $}} of the scheduled tasks."""
    tz = _tz(conf)
    night = set(conf.get("night_tasks") or [])
    today = now.astimezone(tz).date()
    want = {(today - timedelta(days=i)).isoformat() for i in range(1, days + 1)}
    out = {d: {"night": 0.0, "all": 0.0} for d in want}
    for f in (rec.get("files") or {}).values():
        task = f.get("task", "chat")
        if task == "chat":
            continue
        for slot, c in (f.get("b") or {}).items():
            d = datetime.fromtimestamp(int(slot) * BUCKET, tz).date().isoformat()
            if d in out:
                out[d]["all"] += c
                if task in night:
                    out[d]["night"] += c
    return out


# --------------------------------------------------------------------------------------------------------- the status

def status(now: Optional[datetime] = None, conf: Optional[dict] = None, rec: Optional[dict] = None,
           samples: Optional[List[dict]] = None) -> dict:
    """The week as it stands: % used, the reserve, the room for chats, the pace and the level."""
    now = now or datetime.now(timezone.utc)
    conf = conf or settings()
    rec = rec if rec is not None else (_read(state_dir() / "scan.json") or {})
    samples = samples if samples is not None else readings()
    dpp = float(conf["dollars_per_pct"])
    start, end = week(now, conf)
    r = last_reading(samples, conf, start, now)
    since = r["t"] if r else start
    est = dollars(rec, since, now) / dpp
    used = (r["sd"] if r else 0.0) + est
    days = task_days(rec, now, conf)
    night_day = max([v["night"] for v in days.values()] + [0.0]) / dpp
    all_day = max([v["all"] for v in days.values()] + [0.0]) / dpp
    night_day = max(night_day, float(conf["night_floor_pct"]))
    all_day = max(all_day, float(conf["all_floor_pct"]))
    left = max(0.0, (end - now).total_seconds() / 86400)
    margin = float(conf["margin_pct"]) + UNCERTAIN * est
    need_night = night_day * left + margin
    need_all = all_day * left + margin
    room = 100.0 - used - need_all
    pace = dollars(rec, now - timedelta(hours=24), now) / dpp          # % a day, at the last 24 hours' rate
    eta = now + timedelta(days=(100.0 - used) / pace) if pace > 0 and used < 100 else None
    early = bool(eta and eta < end)
    if used >= 99.5 or (r and r["sd"] >= 100):
        level = "out"
    elif room <= 0:
        level = "reserve"
    elif room < float(conf["low_room_pct"]):
        level = "low"
    elif room < float(conf["watch_room_pct"]) or early:
        level = "watch"
    else:
        level = "ok"
    return {"at": now.isoformat(), "week_start": start.isoformat(), "week_end": end.isoformat(),
            "used": round(min(used, 100.0), 1), "estimated": round(est, 1),
            "reading_at": r["t"].isoformat() if r else None, "reading": r["sd"] if r else None,
            "scan_at": rec.get("at"), "night_day": round(night_day, 2), "all_day": round(all_day, 2),
            "need_night": round(need_night, 1), "need_all": round(need_all, 1), "room": round(room, 1),
            "pace": round(pace, 1), "eta": eta.isoformat() if eta else None, "early": early, "level": level,
            "timezone": conf.get("timezone")}


def refresh(now: Optional[datetime] = None, projects: Optional[Path] = None, conf: Optional[dict] = None) -> dict:
    """Scan the new transcript lines, work out the status, keep it in now.json."""
    now = now or datetime.now(timezone.utc)
    rec = scan(projects, now)
    st = status(now, conf, rec)
    _write(state_dir() / "now.json", st)
    return st


def current(now: Optional[datetime] = None, max_age: timedelta = FRESH) -> Optional[dict]:
    """now.json when it is fresh, else a new count. None when the count cannot be made (never fails a caller)."""
    now = now or datetime.now(timezone.utc)
    st = _read(state_dir() / "now.json")
    try:
        if st and now - _ts(st["at"]) < max_age and week(now, settings())[0].isoformat() == st.get("week_start"):
            return st
        return refresh(now)
    except Exception:  # noqa: BLE001  (a meter fault never stops a clip task)
        return st


# -------------------------------------------------------------------------------------------------------------- words

def _when(iso: Optional[str], st: dict) -> str:
    if not iso:
        return "?"
    tz = ZoneInfo(st.get("timezone") or "America/Denver") if ZoneInfo else timezone(timedelta(hours=-6))
    return _ts(iso).astimezone(tz).strftime("%a %H:%M")


def basis(st: dict) -> str:
    if st.get("reading_at"):
        return f"the app's reading {st['reading']:.0f}% at {_when(st['reading_at'], st)} + {st['estimated']:.0f}% counted since"
    return "counted from the transcripts (no reading of the app this week)"


def line(st: dict) -> str:
    """One line for doctor and `factory.py usage`."""
    out = (f"Claude's week {st['used']:.0f}% used ({basis(st)}); the clip tasks need {st['need_all']:.0f}% to the "
           f"reset {_when(st['week_end'], st)} (the night alone {st['need_night']:.0f}%); room for chats "
           f"{max(st['room'], 0):.0f}%")
    if st.get("early"):
        out += f"; at the last day's pace ({st['pace']:.0f}% a day) it runs out {_when(st['eta'], st)}"
    return out


def short(st: dict) -> str:
    """The 06:30 notice's line ('' when the week is fine): first in the notice after posting faults."""
    if st["level"] == "ok":
        return ""
    if st["level"] == "out":
        return f"Claude's week is used up until {_when(st['week_end'], st)}: no Claude clip task can run."
    if st["level"] == "reserve":
        return (f"Claude's week {st['used']:.0f}% used: in the night shift's reserve, daytime clip extras stopped; "
                f"stop other Claude work until {_when(st['week_end'], st)}.")
    out = f"Claude's week {st['used']:.0f}% used, {max(st['room'], 0):.0f}% left for chats before the clips' reserve"
    if st.get("early"):
        out += f"; at this pace it runs out {_when(st['eta'], st)}, the reset is {_when(st['week_end'], st)}"
    return out + "."


def chat_words(st: dict) -> str:
    """What the chat hook adds to a chat's context when the week is not fine."""
    head = {"watch": "watch", "low": "LOW", "reserve": "IN THE NIGHT SHIFT'S RESERVE", "out": "USED UP"}[st["level"]]
    out = [f"[Clip factory usage guard: Claude's weekly limit {head}] {line(st)}."]
    if st["level"] == "out":
        out.append("Nothing more runs until the reset; tell Andrew so.")
    elif st["level"] == "reserve":
        out.append("The daytime clip extras have stopped themselves so the night shift can run. Tell Andrew in one "
                   "short warning line at the top of your reply, and suggest he leaves non-urgent Claude work until "
                   "the reset. Use as little as you can: no subagents, no long scans.")
    else:
        out.append("Tell Andrew in one short warning line at the top of your reply (the % used, when it runs out at "
                   "this pace, what the clips need), then carry on with his request. Keep this chat lean: fewer "
                   "subagents and long scans.")
    return " ".join(out)


# ---------------------------------------------------------------------------------------- the gate, check, hook, doctor

def gate(task: str, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """A scheduled task's first step: (go, the line it prints). The night's own tasks always go; the others stop once
    the week is in the reserve. No count (a fault) is a go: the meter never costs a clip."""
    conf = settings()
    st = current(now)
    if not st:
        return True, f"GO: {task} (no usage count yet; usage.py never stops a run over the meter)"
    if task in (conf.get("night_tasks") or []):
        return True, f"GO: {task} is the night shift's own work. {line(st)}"
    if st["level"] in ("reserve", "out"):
        return False, (f"STOP: {task} skips this run. Claude's week is {st['used']:.0f}% used and the clip night shift "
                       f"needs its last {st['need_night']:.0f}% to reach the reset {_when(st['week_end'], st)} "
                       f"(clipfactory/usage.py). Andrew can still run it by hand.")
    return True, f"GO: {task}. {line(st)}"


def check(notify: bool = False, now: Optional[datetime] = None) -> List[str]:
    """The 30-minute launchd check: count, then a Mac notice when the level got worse this week (or the pace now runs
    out before the reset). Returns the lines it would print."""
    st = refresh(now)
    path = state_dir() / "alerts.json"
    told = _read(path) or {}
    if told.get("week") != st["week_start"]:
        told = {"week": st["week_start"], "level": "ok", "early": False}
    worse = LEVELS.index(st["level"]) > LEVELS.index(told.get("level", "ok"))
    newly_early = st["early"] and not told.get("early")
    out = [line(st)]
    if (worse or newly_early) and st["level"] != "ok":
        text = short(st)
        out.append(f"notice: {text}")
        if notify:
            subprocess.run(["osascript", "-e", f"display notification {json.dumps(text[:220])} with title "
                            f"{json.dumps('Claude usage')} sound name \"Glass\""], capture_output=True, timeout=30)
        told.update(level=st["level"], early=st["early"], at=st["at"])
    elif LEVELS.index(st["level"]) < LEVELS.index(told.get("level", "ok")):
        told.update(level=st["level"], early=st["early"])          # better again: the next worsening is told
    _write(path, told)
    return out


def hook(stdin_text: str, now: Optional[datetime] = None) -> str:
    """The chat hook (UserPromptSubmit): the guard's words when the week is not fine, once per chat and level; ''
    otherwise. Reads now.json only (no scan): it runs before every prompt."""
    now = now or datetime.now(timezone.utc)
    st = _read(state_dir() / "now.json")
    if not st or st.get("level", "ok") == "ok" or now - _ts(st["at"]) > HOOK_STALE:
        return ""
    try:
        sid = (json.loads(stdin_text or "{}") or {}).get("session_id") or "?"
    except ValueError:
        sid = "?"
    path = state_dir() / "chats.json"
    told = _read(path) or {}
    if told.get("week") != st["week_start"]:
        told = {"week": st["week_start"], "sessions": {}}
    was = told["sessions"].get(sid, "ok")
    if LEVELS.index(st["level"]) <= LEVELS.index(was):
        return ""
    told["sessions"][sid] = st["level"]
    _write(path, told)
    return chat_words(st)


def ungated(tasks: Optional[Path] = None, conf: Optional[dict] = None) -> List[str]:
    """The daytime tasks whose installed SKILL.md has lost its STEP 0 (`usage gate --task <id>`); a task not
    installed here is skipped."""
    tasks = Path(tasks or TASKS)
    conf = conf or settings()
    out: List[str] = []
    if _offline(tasks):
        return out
    for t in conf.get("gated_tasks") or []:
        p = tasks / t / "SKILL.md"
        try:
            if f"usage gate --task {t}" not in p.read_text():
                out.append(t)
        except OSError:
            continue
    return out


def doctor(now: Optional[datetime] = None, tasks: Optional[Path] = None) -> List[tuple]:
    """(good, text, fix) rows; good None = said, not a failure."""
    missing = ungated(tasks)
    gate_row = (not missing, "every daytime Claude task runs the usage gate first" if not missing else
                f"no usage gate in {', '.join(missing)}: they would run on into the night shift's reserve",
                "put STEP 0 back at the top of each SKILL.md (a copy: SKILL.md.before-usage-gate-20261027 has the "
                "text without it; any gated task shows the line)")
    st = current(now)
    if not st:
        return [(False, "Claude's week: no usage count (clipfactory/usage.py could not read the transcripts)",
                 "factory.py usage check, and read the error"), gate_row]
    rows: List[tuple] = [gate_row]
    if st["level"] in ("reserve", "out"):
        rows.append((False, line(st), "stop non-urgent Claude work until the reset; the night's tasks still run"))
    elif st["level"] in ("watch", "low"):
        rows.append((None, f"{st['level'].upper()}: {line(st)}", ""))
    else:
        rows.append((True, line(st), ""))
    if not st.get("reading_at"):
        rows.append((None, "no reading of the app this week: open the usage bars in the Claude app once a day so it "
                           "reads the real % (the estimate between readings is ±15%)", ""))
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    """Run as a script: `python3 clipfactory/usage.py hook` (the chat hook, stdin = the hook's JSON). Never fails."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["hook"]:
        try:
            text = hook(sys.stdin.read() if not sys.stdin.isatty() else "")
        except Exception:  # noqa: BLE001  (a chat never stops over the guard)
            text = ""
        if text:
            print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
