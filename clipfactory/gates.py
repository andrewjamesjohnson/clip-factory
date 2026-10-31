"""Check the checkers (the owner, 2026-10-31, the loops thesis item 3: "all of it"). The gates refuse clips quietly
(BURNED read dark suits as burned-in captions; the POLITICS card and the OPENING rule clashed), and a refused good clip
looks like "not enough sources". So every refusal is counted and a few of each gate are shown to Andrew each week, to
say right or wrong; a gate wrong one time in three gets a fix row in LESSONS.md.

Two sources:
  the run journal   a GTA candidate refused at preflight (makers.py writes "gate <account>" lines: the gates and their
                    [FAIL] words) since 2026-10-31
  the night runs    the REFUSED and [FAIL] lines in the Politics night runs' own transcripts (the engine prints them to
                    the run; the usage scan knows each run's file)
It reads and prints; it changes nothing.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from . import journal as J

CODE = r"[A-Z][A-Z_]{2,11}"
FAIL_LINE = re.compile(r"\[FAIL\]\s+(" + CODE + r")\b[^\n\"\\]{0,160}")
REFUSED_LINE = re.compile(r"\bREFUSED\b:?\s+(" + CODE + r")\b[^\n\"\\]{0,160}")
NIGHT_TASKS = ("hearing-clip-sweep-2", "politics-early-gap-fill", "politics-morning-fill-auto", "bench-builder-auto")


def from_journal(days: float = 7.0, now: Optional[datetime] = None) -> List[dict]:
    out = []
    for r in J.read(days * 24, now):
        if not str(r.get("job", "")).startswith("gate ") or r.get("status") != "refused":
            continue
        for g in r.get("gates") or ["?"]:
            out.append({"gate": str(g), "account": r["job"][5:], "at": r.get("at", ""), "where": f"{r.get('day', '')} "
                        f"{r.get('slot', '')} candidate {r.get('candidate', '')}".strip(),
                        "why": (r.get("why") or {}).get(g, "") if isinstance(r.get("why"), dict) else ""})
    return out


def from_nights(days: float = 7.0, now: Optional[datetime] = None, files: Optional[Dict[str, dict]] = None) -> List[dict]:
    from . import nightpath as NP
    now = now or datetime.now()
    files = NP.scan_files() if files is None else files
    out = []
    for p, v in files.items():
        if v.get("task") not in NIGHT_TASKS or not os.path.exists(p):
            continue
        t = datetime.fromtimestamp(os.path.getmtime(p))
        if now - t > timedelta(days=days):
            continue
        try:
            with open(p, errors="ignore") as fh:
                text = "".join(ln for ln in fh if '"tool_result"' in ln)
        except OSError:
            continue
        seen = set()
        for rx in (FAIL_LINE, REFUSED_LINE):
            for m in rx.finditer(text):
                line = " ".join(m.group(0).split())[:200]
                if line in seen:
                    continue
                seen.add(line)
                out.append({"gate": m.group(1), "account": "politics" if "politics" in v["task"] or "hearing" in v["task"]
                            else str(v["task"]), "at": t.strftime("%Y-%m-%dT%H:%M"), "where": v["task"], "why": line})
    return out


def report(days: float = 7.0, per: int = 5, now: Optional[datetime] = None, files=None) -> dict:
    rows = from_journal(days, now) + from_nights(days, now, files)
    count = Counter(r["gate"] for r in rows)
    samples: Dict[str, List[dict]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["at"], reverse=True):
        if len(samples[r["gate"]]) < per:
            samples[r["gate"]].append(r)
    return {"days": days, "count": dict(count.most_common()), "samples": dict(samples)}


def words(rep: dict) -> List[str]:
    if not rep["count"]:
        return [f"gates: no refusal recorded in {rep['days']:g} days (GTA refusals are recorded from 2026-10-31)"]
    out = [f"gates: refusals in the last {rep['days']:g} days, most first. For each sample: right refusal or wrong?"]
    for g, n in rep["count"].items():
        out.append(f"{g}: {n}")
        for s in rep["samples"].get(g, []):
            out.append(f"  - {s['at'][:16].replace('T', ' ')} {s['account']} {s['where']}: {s['why'] or '(no words)'}"[:230])
    return out


# ------------------------------------------------------------------------------------------------------------ the CLI
def add_parser(sub) -> None:
    p = sub.add_parser("gates", help="every gate's refusals and a few to check by hand (clipfactory/gates.py)")
    p.add_argument("--days", type=float, default=7.0)
    p.add_argument("--per", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd)


def cmd(cfg, a) -> None:
    rep = report(a.days, a.per)
    print(json.dumps(rep, indent=1) if a.json else "\n".join(words(rep)))
