"""The judge graded against Andrew (the owner, 2026-10-31, the loops thesis item 18: "17 and 18"). The hook coach is the
factory's AI judge. Google's harness course (Annie Wang, 10.30) says a judge can be wrong and can drift, so it is graded
against a person: Andrew's own calls are the golden set. Every Approve, Decline, Make again and Pull he made himself
(state/<slug>/learn/verdicts.jsonl, by a person, never "Clip Factory (auto)") is joined to the coach's word and average
for the same clip: saved inside the call since 2026-10-31 (verdicts.record), else the slot's scorecard when it still
describes that clip (same clip_sha).

Two grades:
  words    the coach's PASS, FIX or KILL against his call. PASS with an Approve, or FIX/KILL with a Decline, Make again
           or Pull, is agreement (the words exist from 2026-10-31).
  scores   how often a clip he approved has a higher coach average (of the five scores) than a clip he turned down:
           AUC = (wins + ties/2) / (n_yes x n_no), 0.5 a coin flip, 1.0 always. It works on the history too.
Drift: the last 7 days against the 7 before, said when a grade drops by 0.1 or more (6 calls a side at least).
The gate (autosign coach_gate "on") is ready only at 12 calls with words and 70% agreement: a guide; switching is his.
It reads and prints; it changes nothing.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from . import config as C
from . import verdicts as VD

AUTO = "Clip Factory (auto)"
YES = ("approve",)
NO = ("decline", "remake", "pull")
WORDS = ("PASS", "FIX", "KILL")
DRIFT = 0.1
READY_N = 12
READY_AGREE = 0.7


def _avg(written: dict) -> Optional[float]:
    sc = [v for v in (written.get("scores") or {}).values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(sum(sc) / len(sc), 2) if sc else None


def coach_of(acct, row: dict) -> Optional[dict]:
    """The coach's word and average for the clip of this call, or None when nothing describes that clip."""
    c = row.get("coach")
    if isinstance(c, dict):
        return c
    sha = str(row.get("clip_sha") or "")
    if not sha:
        return None
    try:
        from . import coach as CO
        card = CO.load(acct, row.get("board"), str(row.get("slot") or ""))
    except Exception:  # noqa: BLE001
        return None
    if not card or str(card.get("clip_sha") or "") != sha:
        return None
    w = card.get("written") or {}
    word, avg = str(w.get("verdict") or ""), _avg(w)
    return {"word": word, "avg": avg} if (word or avg is not None) else None


def pairs(acct, since: Optional[str] = None) -> List[dict]:
    """His own calls with the coach's word and average, oldest first (calls at or after `since`, an ISO time)."""
    out = []
    for r in VD.rows(acct):
        if str(r.get("by") or "") in ("", AUTO) or r.get("verdict") not in YES + NO:
            continue
        if since and str(r.get("at") or "") < since:
            continue
        c = coach_of(acct, r)
        if not c:
            continue
        out.append({"at": str(r.get("at") or ""), "board": r.get("board"), "slot": r.get("slot"),
                    "call": "yes" if r["verdict"] in YES else "no", "verdict": r["verdict"],
                    "word": str(c.get("word") or "").upper(), "avg": c.get("avg")})
    return out


def auc(yes: List[float], no: List[float]) -> Optional[float]:
    """How often a clip he approved has a higher coach average than one he turned down (ties count half)."""
    if not yes or not no:
        return None
    wins = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in yes for b in no)
    return wins / (len(yes) * len(no))


def grade(ps: List[dict]) -> Dict[str, Optional[float]]:
    worded = [p for p in ps if p["word"] in WORDS]
    agree = sum(1 for p in worded if (p["word"] == "PASS") == (p["call"] == "yes"))
    yes = [p["avg"] for p in ps if p["call"] == "yes" and p["avg"] is not None]
    no = [p["avg"] for p in ps if p["call"] == "no" and p["avg"] is not None]
    return {"n": len(ps), "words_n": len(worded), "words_agree": agree / len(worded) if worded else None,
            "held_share": sum(1 for p in worded if p["word"] != "PASS") / len(worded) if worded else None,
            "yes_n": len(yes), "no_n": len(no), "auc": auc(yes, no)}


def report(acct, now: Optional[datetime] = None, days: int = 28) -> dict:
    now = now or datetime.now(timezone.utc)
    iso = lambda t: t.astimezone(timezone.utc).replace(microsecond=0).isoformat()  # noqa: E731
    ps = pairs(acct, iso(now - timedelta(days=days)))
    last = [p for p in ps if p["at"] >= iso(now - timedelta(days=7))]
    prev = [p for p in ps if iso(now - timedelta(days=14)) <= p["at"] < iso(now - timedelta(days=7))]
    g_all, g_last, g_prev = grade(ps), grade(last), grade(prev)
    drift = [k for k in ("words_agree", "auc")
             if g_last[k] is not None and g_prev[k] is not None and g_last[k] <= g_prev[k] - DRIFT
             and min(g_last["n"], g_prev["n"]) >= 6]
    ready = g_all["words_n"] >= READY_N and (g_all["words_agree"] or 0.0) >= READY_AGREE
    return {"account": acct.slug, "name": acct.name, "days": days, "all": g_all, "last7": g_last, "prev7": g_prev,
            "drift": drift, "gate_ready": ready}


def _pct(x: Optional[float]) -> str:
    return f"{round(100 * x)}%" if x is not None else "-"


def words(rep: dict) -> List[str]:
    g = rep["all"]
    out = [f"{rep['name']}: {g['n']} of your own calls in {rep['days']} days have the coach's scorecard"]
    if g["auc"] is None:
        out.append(f"  scores: too few to compare ({g['yes_n']} approved, {g['no_n']} turned down with a scorecard)")
    else:
        out.append(f"  scores: a clip you approved scored higher than one you turned down {_pct(g['auc'])} of the time "
                   f"({g['yes_n']} approved, {g['no_n']} turned down; 50% is a coin flip)")
    if g["words_n"]:
        out.append(f"  words: the coach agreed with you on {_pct(g['words_agree'])} of {g['words_n']} calls; it said FIX "
                   f"or KILL on {_pct(g['held_share'])}")
    else:
        out.append("  words: none yet (the coach writes PASS, FIX or KILL from 2026-10-31)")
    for k in rep["drift"]:
        out.append(f"  DRIFT: {k} fell from {_pct(rep['prev7'][k])} to {_pct(rep['last7'][k])} this week")
    out.append("  gate: ready to switch on (your call)" if rep["gate_ready"] else
               f"  gate: not ready (needs {READY_N} calls with words and {round(100 * READY_AGREE)}% agreement)")
    return out


# ------------------------------------------------------------------------------------------------------------ the CLI
def add_parser(sub) -> None:
    p = sub.add_parser("judge", help="the coach graded against Andrew's own calls (clipfactory/judge.py)")
    p.add_argument("account", nargs="?", default="")
    p.add_argument("--days", type=int, default=28)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd)


def cmd(cfg: C.Config, a) -> None:
    accts = [cfg.account(a.account)] if a.account else [x for x in cfg.accounts.values() if x.active]
    reps = [report(x, days=a.days) for x in accts]
    if a.json:
        print(json.dumps(reps, indent=1))
        return
    for rep in reps:
        print("\n".join(words(rep)))
