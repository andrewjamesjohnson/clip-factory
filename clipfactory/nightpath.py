"""The night's path (the owner, 2026-10-31, the loops thesis item 19, from Google's harness course: grade the path, not
only the answer; an agent that guessed right this time fails another time). The night runs follow a runbook. This
checks, from each run's own transcript (the usage scan's record of ~/.claude/projects: the run and its helpers), that
it took its required steps. A run that skipped registering or ending its shift is a NO in the doctor (the gap-fills
read that record); a skipped read is said. `factory.py path` shows every run. It reads; it changes nothing.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REQUIRED: Dict[str, List[str]] = {
    "hearing-clip-sweep-2": ["shift begin", "shift check", "daily8", "learn brief", "shift end"],
    "politics-early-gap-fill": ["shift wait", "shift begin", "shift end"],
    "gaming-morning-sweep": ["shift wait", "shift begin", "learn brief", "shift end"],
}
HARD = ("shift begin", "shift end")      # the night's own record, which the other runs wait on


def scan_files(state: Optional[Path] = None) -> Dict[str, dict]:
    from . import usage as U
    p = Path(state or U.state_dir()) / "scan.json"
    try:
        return json.loads(p.read_text()).get("files") or {}
    except (OSError, ValueError):
        return {}


def tool_text(path: str) -> str:
    """Every tool call of a run and of its helpers (subagents), as one text."""
    files = [path] + glob.glob(os.path.join(path[:-len(".jsonl")], "**", "*.jsonl"), recursive=True)
    out = []
    for f in files:
        try:
            with open(f, errors="ignore") as fh:
                out += [ln for ln in fh if '"tool_use"' in ln]
        except OSError:
            continue
    return "".join(out)


def runs(hours: float = 36.0, now: Optional[datetime] = None, files: Optional[Dict[str, dict]] = None) -> List[dict]:
    """The night runs of the last `hours`, oldest first, each with its required steps counted."""
    now = now or datetime.now()
    files = scan_files() if files is None else files
    out = []
    for p, v in files.items():
        task = v.get("task")
        if task not in REQUIRED or not os.path.exists(p):
            continue
        t = datetime.fromtimestamp(os.path.getmtime(p))
        if now - t > timedelta(hours=hours):
            continue
        text = tool_text(p)
        if not text:                        # a run that never called a tool (the 10-25 run-out): the journal says that
            continue
        steps = {s: text.count(s) for s in REQUIRED[task]}
        out.append({"task": task, "ended": t.strftime("%m-%d %H:%M"), "steps": steps,
                    "missing": [s for s, n in steps.items() if not n]})
    return sorted(out, key=lambda r: r["ended"])


def doctor(now: Optional[datetime] = None, files: Optional[Dict[str, dict]] = None
           ) -> List[Tuple[Optional[bool], str, str]]:
    latest: Dict[str, dict] = {}
    for r in runs(36.0, now, files):
        latest[r["task"]] = r
    if not latest:
        return [(None, "no night run in the last 36 hours", "")]
    rows: List[Tuple[Optional[bool], str, str]] = []
    for task, r in sorted(latest.items()):
        hard = [s for s in r["missing"] if s in HARD]
        if hard:
            rows.append((False, f"{task} (ended {r['ended']}) never ran: {', '.join(hard)}", "factory.py path"))
        elif r["missing"]:
            rows.append((None, f"{task} (ended {r['ended']}) skipped: {', '.join(r['missing'])}", ""))
        else:
            rows.append((True, f"{task} (ended {r['ended']}): every required step", ""))
    return rows


# ------------------------------------------------------------------------------------------------------------ the CLI
def add_parser(sub) -> None:
    p = sub.add_parser("path", help="the night runs' required steps, from their own transcripts (clipfactory/nightpath.py)")
    p.add_argument("--hours", type=float, default=72.0)
    p.set_defaults(fn=cmd)


def cmd(cfg, a) -> None:
    rs = runs(a.hours)
    if not rs:
        print(f"path: no night run in the last {a.hours:g} hours")
        return
    for r in rs:
        marks = "  ".join(f"{'OK' if n else 'NO'} {s}" for s, n in r["steps"].items())
        print(f"{r['task']:<24} ended {r['ended']}  {marks}")
