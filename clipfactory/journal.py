"""The run journal (the owner, 2026-10-30, the loops thesis item 6: "I want 3, 6, 7, 9, 12, 15"): one line per run of
every launchd job (factory.py's main, when launchd started it) and one per start of a Claude task through its usage
gate, in state/journal/<YYYY-MM-DD>.jsonl: when, the job, its status (ok, exit, failed, started, skipped), the exit
code and the seconds. The doctor reads it for jobs that keep failing (what came out, not "is it loaded"), and
`factory.py journal` shows it. Recording never stops what it records: every error of its own is swallowed.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import store

BAD = ("exit", "failed")         # a non-zero exit, or an error that ended the run
FAILS_IN_A_ROW = 3               # the doctor says NO from this many bad runs in a row of one job


def path(day) -> Path:
    """state/journal/<YYYY-MM-DD>.jsonl (at call time)."""
    return store.path("journal", f"{day.isoformat()}.jsonl")


def record(job: str, status: str, **fields) -> None:
    """One line for one run. Never raises."""
    try:
        now = datetime.now().astimezone()
        p = path(now.date())
        p.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": now.isoformat(timespec="seconds"), "job": str(job)[:80], "status": str(status)}
        row.update({k: v for k, v in fields.items() if v is not None})
        with p.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception:  # noqa: BLE001  (the journal never stops a job)
        pass


def read(hours: float = 24.0, now: Optional[datetime] = None) -> List[dict]:
    """The lines of the last `hours`, oldest first."""
    now = now or datetime.now().astimezone()
    since = now - timedelta(hours=hours)
    out: List[dict] = []
    day = since.date()
    while day <= now.date():
        try:
            lines = path(day).read_text().splitlines()
        except OSError:
            lines = []
        for ln in lines:
            try:
                r = json.loads(ln)
                t = datetime.fromisoformat(r["at"])
            except (ValueError, KeyError, TypeError):
                continue
            if since <= t <= now:
                out.append(r)
        day += timedelta(days=1)
    return sorted(out, key=lambda r: r.get("at", ""))


def jobs(rows: List[dict]) -> Dict[str, dict]:
    """Per job: its runs, the bad ones, how many of its last runs in a row were bad, and its last status."""
    out: Dict[str, dict] = OrderedDict()
    for r in rows:
        j = out.setdefault(str(r.get("job") or "?"),
                           {"runs": 0, "bad": 0, "in_a_row": 0, "last": "", "status": "", "code": None})
        bad = r.get("status") in BAD
        j["runs"] += 1
        j["bad"] += int(bad)
        j["in_a_row"] = j["in_a_row"] + 1 if bad else 0
        j.update(last=str(r.get("at") or ""), status=str(r.get("status") or ""), code=r.get("code"))
    return out


def doctor(now: Optional[datetime] = None) -> List[Tuple[Optional[bool], str, str]]:
    """The doctor's rows: (good, words, fix); good None is said, not a failure."""
    rows = read(24.0, now)
    if not rows:
        return [(None, "no runs recorded in the last 24 hours (it starts with the next launchd job)", "")]
    js = jobs(rows)
    out: List[Tuple[Optional[bool], str, str]] = []
    for name, j in js.items():
        if j["in_a_row"] >= FAILS_IN_A_ROW:
            out.append((False, f"{name}: its last {j['in_a_row']} runs did not finish ok ({j['status']} {j['code']}, "
                               f"last at {j['last'][11:16]})", f"factory.py journal show --job '{name}'"))
    launchd = {n: j for n, j in js.items() if not n.startswith("task ")}
    tasks = [n[5:] for n in js if n.startswith("task ")]
    out.append((True, f"{len(launchd)} launchd jobs ran {sum(j['runs'] for j in launchd.values())} times in 24 hours "
                      f"({sum(j['bad'] for j in launchd.values())} not ok)", ""))
    if tasks:
        out.append((None, f"Claude tasks started in 24 hours: {', '.join(tasks)}", ""))
    return out


# ------------------------------------------------------------------------------------------------------------ the CLI
def add_parser(sub) -> None:
    p = sub.add_parser("journal", help="the run journal: every launchd job's runs and every Claude task's start "
                                       "(clipfactory/journal.py)")
    p.add_argument("action", nargs="?", default="jobs", choices=["jobs", "show", "cost"])
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--job", default="")
    p.set_defaults(fn=cmd)


def costs(state=None) -> List[Tuple[str, float, int]]:
    """Claude's cost per scheduled task over the usage scan's 8 days (task, dollars at API prices, sessions), from the
    usage guard's own record (usage.py scan.json): chats are "chat". The thesis item 6's cost per job."""
    from . import usage as U
    try:
        files = json.loads((Path(state or U.state_dir()) / "scan.json").read_text()).get("files") or {}
    except (OSError, ValueError):
        return []
    cost: Dict[str, float] = {}
    runs: Dict[str, int] = {}
    for v in files.values():
        c = sum(float(x) for x in (v.get("b") or {}).values())
        if c > 0:
            t = str(v.get("task") or "chat")
            cost[t] = cost.get(t, 0.0) + c
            runs[t] = runs.get(t, 0) + 1
    return sorted(((t, c, runs[t]) for t, c in cost.items()), key=lambda x: -x[1])


def cmd(cfg, a) -> None:
    if a.action == "cost":
        rows = costs()
        tot = sum(c for _, c, _ in rows) or 1.0
        for t, c, n in rows:
            print(f"{t:<34} ${c:9.2f}  {100 * c / tot:5.1f}%  sessions {n}")
        if not rows:
            print("journal: the usage scan has nothing yet (factory.py usage check)")
        return
    rows = read(a.hours)
    if a.job:
        rows = [r for r in rows if r.get("job") == a.job]
    if not rows:
        print(f"journal: nothing in the last {a.hours:g} hours")
        return
    if a.action == "show":
        for r in rows:
            extra = " ".join(f"{k}={v}" for k, v in r.items() if k not in ("at", "job", "status"))
            print(f"{str(r.get('at'))[5:16].replace('T', ' ')}  {r.get('status', ''):<8} {r.get('job', '')}  {extra}".rstrip())
        return
    for name, j in jobs(rows).items():
        print(f"{name:<34} runs {j['runs']:>4}  not ok {j['bad']:>3}  last {j['status']} at "
              f"{j['last'][5:16].replace('T', ' ')}")
