"""The change politics (the owner, 2026-10-30, the loops thesis item 2: "yes, I would like to build all those"). Every
change that goes live gets one row: what changed, the account (and platforms) it touches, when it went live, its
commit and its undo. Once its window has passed, the learner's own numbers give it a verdict:

  KEEP         the posts after it beat the posts before it (the 90% range of the lift is above 1)
  NO EFFECT    no difference the numbers can see; a result too, so keeping it or not is a choice
  ROLL BACK?   the posts after it did worse (the range is below 1): a question for Andrew, never done by itself
  WAIT         its window has not passed, or fewer than 12 posts a side have a 24-hour number (the learner's bar)
  BY HAND      a change that does not aim at views (metric "manual"): a person or the coach judges it

A change is TANGLED when another change on the same account went live within 3 days of it: before and after cannot
tell them apart, so its verdict is printed with the names of the others and nothing is proposed on it.

Views are compared the way the learner compares them (learn.py): rel_24h, a post's 24-hour views against the usual of
its platform, the account's posts on the named platforms (all when none) in the window_days before going live against
the window_days after. The rows are config/changes.json (`factory.py changes add`). Nothing here changes the factory:
it reads, judges and prints.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from . import config as C

WINDOW_DAYS = 7
MIN_N = 12                      # posts a side with a 24-hour number: the learner's "likely" bar (learn.min_n)
TANGLE_DAYS = 3
METRICS = ("views", "manual")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
WHAT_MAX = 200


def path(cfg: C.Config) -> Path:
    """config/changes.json, next to the accounts file."""
    return Path(cfg.path).parent / "changes.json"


def load(cfg: C.Config) -> List[dict]:
    try:
        v = json.loads(path(cfg).read_text())
    except (OSError, ValueError):
        return []
    return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []


def _save(cfg: C.Config, rows: List[dict]) -> None:
    p = path(cfg)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".changes.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def live_at(cfg: C.Config, v) -> Optional[datetime]:
    """'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' in local time (a day alone is its midnight)."""
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            t = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return t.replace(tzinfo=cfg.now().tzinfo)
    return None


def tangled(cfg: C.Config, row: dict, rows: List[dict]) -> List[str]:
    """The other changes on the same account that went live within TANGLE_DAYS of this one."""
    t = live_at(cfg, row.get("live"))
    out = []
    for o in rows:
        if o.get("id") == row.get("id") or o.get("account") != row.get("account"):
            continue
        u = live_at(cfg, o.get("live"))
        if t and u and abs((u - t).total_seconds()) <= TANGLE_DAYS * 86400:
            out.append(str(o.get("id")))
    return out


def check(cfg: C.Config, row: dict, rows: List[dict]) -> dict:
    """A new row, checked. SystemExit says what to change."""
    from . import learn as L
    rid = str(row.get("id") or "").strip()
    if not ID_RE.match(rid):
        raise SystemExit("--id: 2 to 48 small letters, digits and hyphens (hook-fixes-1028)")
    if any(r.get("id") == rid for r in rows):
        raise SystemExit(f"--id {rid} is in the politics already")
    what = " ".join(str(row.get("what") or "").split())
    if not what or len(what) > WHAT_MAX:
        raise SystemExit(f"--what: one line of at most {WHAT_MAX} characters")
    slug = str(row.get("account") or "")
    try:
        cfg.account(slug)
    except Exception:  # noqa: BLE001
        raise SystemExit(f"--account {slug!r}: no such account") from None
    outlets = [o for o in (row.get("outlets") or []) if o]
    bad = [o for o in outlets if o not in L.DASH]
    if bad:
        raise SystemExit(f"--outlets: unknown {', '.join(bad)} (the platforms: {', '.join(L.DASH)})")
    if live_at(cfg, row.get("live")) is None:
        raise SystemExit("--live: 'YYYY-MM-DD HH:MM' (local time) or 'YYYY-MM-DD'")
    metric = row.get("metric") or "views"
    if metric not in METRICS:
        raise SystemExit(f"--metric: {' or '.join(METRICS)}")
    raw = row.get("window_days")
    try:
        win = WINDOW_DAYS if raw in (None, "") else int(raw)
    except (TypeError, ValueError):
        win = 0
    if not 1 <= win <= 60:
        raise SystemExit("--window-days: 1 to 60")
    out = {"id": rid, "what": what, "account": slug, "outlets": outlets, "live": str(row["live"]).strip(),
           "metric": metric, "window_days": win}
    for k in ("commit", "undo", "judge", "by"):
        v = " ".join(str(row.get(k) or "").split())
        if v:
            out[k] = v[:200]
    return out


def add(cfg: C.Config, row: dict) -> str:
    rows = load(cfg)
    clean = check(cfg, row, rows)
    rows.append(clean)
    _save(cfg, rows)
    tg = tangled(cfg, clean, rows)
    note = f"; tangled with {', '.join(tg)} (live within {TANGLE_DAYS} days on the same account)" if tg else ""
    if "engine" in (clean.get("commit", "") + " " + clean["what"]).lower():      # the loops thesis item 7
        note += "; an engine change: `factory.py replay run` before it reaches a night"
    return f"changes: added {clean['id']} ({clean['account']}, live {clean['live']}){note}"


def judge(cfg: C.Config, row: dict, posts: List[dict], now: datetime, rows: Optional[List[dict]] = None) -> dict:
    """One change's verdict from `posts` (learn.rows_for of its account, with baselines)."""
    from . import learn as L
    from . import statmath as SM
    rows = load(cfg) if rows is None else rows
    out = {"id": row.get("id"), "account": row.get("account"), "tangled": tangled(cfg, row, rows),
           "undo": row.get("undo") or ""}
    t = live_at(cfg, row.get("live"))
    win = int(row.get("window_days") or WINDOW_DAYS)
    if (row.get("metric") or "views") != "views":
        return dict(out, verdict="BY HAND", due=bool(t) and now >= t + timedelta(days=win),
                    words="not about views: " + (row.get("judge") or "a person judges it"))
    if t is None:
        return dict(out, verdict="WAIT", words="its live time cannot be read")
    t0, w = t.timestamp(), win * 86400.0
    outs = set(row.get("outlets") or [])
    rr = [r for r in posts if r.get("rel_24h") is not None and (not outs or r.get("outlet") in outs)]
    after = [r["rel_24h"] for r in rr if t0 <= L._went(r) < t0 + w]
    before = [r["rel_24h"] for r in rr if t0 - w <= L._went(r) < t0]
    check_on = (t + timedelta(days=win)).strftime("%Y-%m-%d %H:%M")
    out.update(n_before=len(before), n_after=len(after), check_on=check_on)
    if now.timestamp() < t0 + w:
        return dict(out, verdict="WAIT", due=False,
                    words=f"its {win}-day window ends {check_on} ({len(before)} before, {len(after)} after so far)")
    out["due"] = True
    if len(after) < MIN_N or len(before) < MIN_N:
        return dict(out, verdict="WAIT", words=f"too few posts with a 24-hour number: {len(before)} before, "
                                               f"{len(after)} after (it needs {MIN_N} a side)")
    lift = math.exp(SM.median(after) - SM.median(before))
    ci = SM.bootstrap_ci(after, before, n=getattr(L, "BOOT_N", 2000), seed=L._seed("change", row.get("id")))
    lo, hi = (math.exp(ci[0]), math.exp(ci[1])) if ci else (None, None)
    verdict = "KEEP" if lo is not None and lo > 1.0 else "ROLL BACK?" if hi is not None and hi < 1.0 else "NO EFFECT"
    rng = f", 90% range {lo:.2f}x to {hi:.2f}x" if lo is not None else ""
    return dict(out, verdict=verdict, lift=round(lift, 3), ci90=[round(lo, 3), round(hi, 3)] if lo is not None else None,
                words=f"posts after it did {lift:.2f}x the posts before it{rng} ({len(before)} before, "
                      f"{len(after)} after)")


def judge_all(cfg: C.Config, now: Optional[datetime] = None, only_due: bool = False) -> List[dict]:
    """Every row's verdict (only_due: the rows whose window has passed)."""
    from . import learn as L
    now = now or cfg.now()
    rows = load(cfg)
    posts: Dict[str, Optional[List[dict]]] = {}
    out = []
    for row in rows:
        slug = str(row.get("account") or "")
        if slug not in posts:
            try:
                pr = L.rows_for(cfg, cfg.account(slug), now)
                L.add_baselines(pr)
                posts[slug] = pr
            except Exception as e:  # noqa: BLE001  (one account that cannot be read never hides the others)
                posts[slug] = None
                out.append({"id": "", "account": slug, "verdict": "WAIT", "tangled": [], "due": True,
                            "words": f"its posts cannot be read ({type(e).__name__}: {str(e)[:120]})"})
        if posts[slug] is None:
            continue
        j = judge(cfg, row, posts[slug], now, rows)
        if only_due and not j.get("due", True):
            continue
        out.append(j)
    return out


def line(j: dict) -> str:
    tg = f"  TANGLED with {', '.join(j['tangled'])}" if j.get("tangled") else ""
    undo = f" (undo: {j['undo']})" if j.get("verdict") == "ROLL BACK?" and j.get("undo") else ""
    return f"{j['verdict']:<11} {j.get('account', ''):<15} {j.get('id', '')}: {j.get('words', '')}{undo}{tg}"


# ------------------------------------------------------------------------------------------------------------ the CLI
def add_parser(sub) -> None:
    p = sub.add_parser("changes", help="the change politics: every live change and its verdict (clipfactory/changes.py)")
    p.add_argument("action", nargs="?", default="list", choices=["list", "due", "add"])
    p.add_argument("--id")
    p.add_argument("--what")
    p.add_argument("--account")
    p.add_argument("--outlets", default="", help="add: a comma list of platforms (all when none)")
    p.add_argument("--live", help="add: when it went live, 'YYYY-MM-DD HH:MM' local")
    p.add_argument("--metric", default="views", choices=METRICS)
    p.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    p.add_argument("--commit", default="")
    p.add_argument("--undo", default="", help="add: how to take it back (a kit, a tag, a switch)")
    p.add_argument("--judge", default="", help="add, metric manual: who or what judges it")
    p.add_argument("--by", default="", help="add: who added the row")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd)


def cmd(cfg: C.Config, a) -> None:
    if a.action == "add":
        print(add(cfg, {"id": a.id, "what": a.what, "account": a.account, "live": a.live, "metric": a.metric,
                        "outlets": [o.strip() for o in (a.outlets or "").split(",") if o.strip()],
                        "window_days": a.window_days, "commit": a.commit, "undo": a.undo, "judge": a.judge,
                        "by": a.by}))
        return
    res = judge_all(cfg, only_due=(a.action == "due"))
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return
    if not res:
        print("changes: nothing due" if a.action == "due" else "changes: the politics is empty (factory.py changes add)")
        return
    for j in res:
        print(line(j))
