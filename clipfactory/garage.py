"""The maker "garage": Cars's clips, made at night like every account's.

Andrew, 2026-10-29: "Cars is at night... all these clips get made at night... I wake up, I approve them all in
the morning. They get scheduled out throughout the day, 6 to 8 clips for each account."

    factory.py make cars --date D [--slots 07:05,09:35] [--dry-run]     (its 21:00 nightly runs this)

Each open or failed slot of the day gets one vehicle from the day's mix (garage/vehicles.py: car, bike, car, air,
boat, wildcard, and from the 8-a-day rule a car and a military vehicle), in one of the eight formats, cut on one of
our own beats (garage/: the Garage Proof 10.29 kit's engine, run by itself). Per slot, garage/run.py finds and
pulls the footage OUTSIDE the render queue (it waits on YouTube), then cuts and checks it IN the queue, and the clip
is placed as an unsigned draft (place.place: every refusal a placed clip has). A refused or failed slot says why on
its card; the vehicle stays unshown, so a later night can pick it again.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from . import board as B
from . import config as C

REPO = Path(__file__).resolve().parent.parent           # ~/clip-factory: garage/ sits beside clipfactory/
DEFAULTS = {
    "python": str(Path.home() / "opt" / "anaconda3" / "bin" / "python3"),   # the engine needs numpy, OpenCV, SciPy
    "prep_timeout_minutes": 30,
    "render_timeout_minutes": 40,
    "lead_minutes": 90,                                 # place.place refuses a slot under 90 minutes away
    "keep_work_days": 2,
    "spares": 1,                                        # spare edits a night for the bench, when it runs low
    "spares_until": "04:30",                            # no spare starts after this: the night's end is the slots'
    "gta6_from": "2026-11-19",                          # Andrew: "on November 19th, we make the flip"
    "intros": True,                                     # Andrew 10.30 "Intros + real songs": each clip opens on one of
    "intro_days": 7,                                    # his approved intros (garage/intros.py), none again for 7 days
}


def settings(acct: C.Account) -> dict:
    return dict(DEFAULTS, **((acct.raw or {}).get("garage") or {}))


def home(acct: C.Account) -> Path:
    return C.STATE / acct.slug / "garage"


def history(acct: C.Account) -> Dict[str, str]:
    """vehicle slug -> the last posting day it was placed for (the 30-day rule reads it)."""
    try:
        return json.loads((home(acct) / "history.json").read_text())
    except (OSError, ValueError):
        return {}


def remember(acct: C.Account, slug: str, day: date) -> None:
    h = history(acct)
    h[slug] = max(day.isoformat(), h.get(slug, ""))
    home(acct).mkdir(parents=True, exist_ok=True)
    (home(acct) / "history.json").write_text(json.dumps(h, indent=1, sort_keys=True))


def intro_history(acct: C.Account) -> Dict[str, str]:
    """intro id -> the last posting day a clip opened on it."""
    try:
        return json.loads((home(acct) / "intros.json").read_text())
    except (OSError, ValueError):
        return {}


def remember_intro(acct: C.Account, iid: Optional[str], day: date) -> None:
    if not iid:
        return
    h = intro_history(acct)
    h[iid] = max(day.isoformat(), h.get(iid, ""))
    home(acct).mkdir(parents=True, exist_ok=True)
    (home(acct) / "intros.json").write_text(json.dumps(h, indent=1, sort_keys=True))


def with_intros(acct: C.Account, day: date, picks: List[dict], also: List[Optional[str]] = ()) -> List[dict]:
    """Each pick gets an approved intro (Andrew 10.30): the turn moves every day and slot, one used in the last
    intro_days waits, none twice in a night (also: the night's own, for its spares). No library
    (~/cars-intros) = our own beats, as before."""
    st = settings(acct)
    if not st.get("intros"):
        return picks
    import sys
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from garage import intros as GI
    if not GI.index():
        return picks
    since = (day - timedelta(days=int(st.get("intro_days", 7)))).isoformat()
    recent = [k for k, v in intro_history(acct).items() if since <= v < day.isoformat()] + [x for x in also if x]
    out = []
    for i, p in enumerate(picks):
        iid = GI.choose(day.toordinal(), p.get("slot_index", i), recent)
        if iid:
            recent.append(iid)
        out.append(dict(p, intro=iid))
    return out


def file_no(acct: C.Account) -> int:
    """The series number the next clip carries ("CARS  FILE 012"): one up only when a clip is placed, so a
    refused cut never leaves a gap in the files."""
    try:
        return int(json.loads((home(acct) / "file_no.json").read_text())["next"])
    except (OSError, ValueError, KeyError):
        return 1


def placed_file_no(acct: C.Account, n: int) -> None:
    home(acct).mkdir(parents=True, exist_ok=True)
    (home(acct) / "file_no.json").write_text(json.dumps({"next": max(n + 1, file_no(acct))}))


def game_for(acct: C.Account, day: date) -> str:
    """GTA 5 until the launch, GTA 6 from it (a GTA 5 vehicle never posts as GTA 6, nor after the flip)."""
    return "GTA 6" if day.isoformat() >= str(settings(acct)["gta6_from"]) else "GTA 5"


def picks_for(cfg: C.Config, acct: C.Account, day: date, board: dict) -> Dict[str, dict]:
    """slot -> {"vehicle", "format", "rival", "beat", "game"}: the day's mix in the order of its slots. None of them
    on a GTA 6 day before garage/vehicles-gta6.json exists (GTABase's GTA 6 pages, read at the launch)."""
    import sys
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from garage import plan as GP, vehicles as GV
    game = game_for(acct, day)
    if not GV.db_for(game).exists():
        return {}
    slots = sorted(k for k, s in board["slots"].items() if not s.get("spare"))
    shown = {k: v for k, v in history(acct).items() if v < day.isoformat()}
    got = GV.pick_day(GV.load(GV.db_for(game)), day, len(slots), shown)
    out = {}
    got = with_intros(acct, day, list(got))
    for k, p in zip(slots, got):
        out[k] = dict(p, beat=GP.beat_for(day.toordinal(), p["slot_index"]), game=game)
    return out


def _run(st: dict, args: List[str], log_p: Path, timeout: int) -> subprocess.CompletedProcess:
    """garage/run.py under the engine's Python, its output in the log, at nice 10 with one thread a library (the
    Politics's and Gaming's steps keep the Mac first; 4 parallel song scans once put it at load 91); past its
    timeout (or the worker's 06:15 stop) it is stopped with everything it started (makers.run_logged)."""
    from . import makers as M
    from . import net
    if net.why_not() in ("offline", "a test run"):     # the engine pulls from YouTube: never from a test
        with open(log_p, "a") as f:
            f.write(f"FAILED the Garage engine does not run {net.why_not()}\n")
        return subprocess.CompletedProcess(args, 3)
    env = dict(os.environ, PYTHONPATH=str(REPO), GARAGE_PY=st["python"], OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
               OPENBLAS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
    return M.run_logged(["nice", "-n", "10", st["python"], "-m", "garage.run"] + args, log_p, cwd=REPO, env=env,
                        timeout=timeout)


def _last(log_p: Path, word: str) -> str:
    try:
        lines = [x for x in log_p.read_text(errors="ignore").splitlines() if x.startswith(word + " ")]
    except OSError:
        return ""
    return lines[-1][len(word) + 1:] if lines else ""


def describe(p: dict) -> str:
    return f"{p['vehicle']['name']} ({p['format']}" + (f", vs {p['rival']['name']}" if p.get("rival") else "") + ")"


def _cut(acct: C.Account, label: str, p: dict, work: Path, n: int, log_p: Path, fail) -> Optional[dict]:
    """garage/run.py for one pick: the footage outside the render queue, the cut and its checks in it. The result
    (result.json), or None after fail(why, kind) was told why. A cut still running at 06:15 raises ShiftOver."""
    from . import makers as M
    from . import renderlock as RL
    st = settings(acct)
    v, what = p["vehicle"], describe(p)
    out = work / "out"
    args = ["make", "--vehicle", v["slug"], "--format", p["format"], "--out", str(out), "--work", str(work),
            "--file-no", str(n), "--beat", p["beat"], "--game", p.get("game", "GTA 5")] + \
        (["--rival", p["rival"]["slug"]] if p.get("rival") else []) + (["--intro", p["intro"]] if p.get("intro") else [])
    try:                                                 # the footage: network, outside the render queue
        r = _run(st, args + ["--prep-only"], log_p, int(float(st["prep_timeout_minutes"]) * 60))
    except M.ShiftOver:
        fail(f"{what}: the night was over (06:15) while finding the footage", "deadline")
        raise
    except subprocess.TimeoutExpired:
        fail(f"{what}: finding the footage ran past {st['prep_timeout_minutes']} minutes", "timeout")
        return None
    if r.returncode != 0:
        fail(f"{what}: {_last(log_p, 'FAILED') or M.tail(log_p, 2)}", "tool")
        return None
    limit = float(st["render_timeout_minutes"]) * 60
    _, stop_at = RL.night_limits()
    if stop_at is not None:
        limit = max(60.0, min(limit, stop_at - time.time()))
    try:                                                 # the cut and the checks: the render queue
        with RL.hold(acct.slug, f"garage {label}: {v['name']}", log=log_p):
            r = _run(st, args + ["--render-only"], log_p, int(limit))
    except RL.Busy as e:
        fail(f"{what}: the render queue was still busy at the night's deadline ({e})", "deadline")
        return None
    except M.ShiftOver:
        fail(f"{what}: the night was over (06:15) during the cut", "deadline")
        raise
    except subprocess.TimeoutExpired:
        fail(f"{what}: the cut ran past {int(limit // 60)} minutes and was stopped", "timeout")
        return None
    try:
        res = json.loads((out / "result.json").read_text())
    except (OSError, ValueError):
        res = {}
    if r.returncode != 0 or not res:
        kind = "refused" if r.returncode == 2 else "tool"
        fail(f"{what}: {kind}: {_last(log_p, 'REFUSED') or _last(log_p, 'FAILED') or M.tail(log_p, 2)}", kind)
        return None
    return res


def _make_one(cfg: C.Config, acct: C.Account, day: date, k: str, p: dict, log_p: Path) -> str:
    from . import makers as M
    from . import place as PL
    what = describe(p)
    with B.edit(cfg, acct, day) as bd:
        s = bd["slots"][k]
        if s.get("status") not in (B.OPEN, B.FAILED):
            return f"  {k}: filled meanwhile ({s.get('status')})"
        s["status"] = B.MAKING
        B.note(s, f"making {what} on {p['beat']}")
    said: List[str] = []

    def fail(why: str, kind: str) -> None:
        M._fail(cfg, acct, day, [k], why[:600], kind=kind)
        said.append(f"  {k}: {kind.upper() if kind == 'refused' else 'FAILED'} ({why[:220]})")

    n = file_no(acct)
    res = _cut(acct, k, p, home(acct) / "work" / day.isoformat() / k.replace(":", ""), n, log_p, fail)
    if res is None:
        return said[-1]
    with B.edit(cfg, acct, day) as bd:
        s = bd["slots"][k]
        if s.get("status") == B.MAKING:
            s["status"] = B.OPEN
    ok, lines = PL.place(cfg, acct, day, k, clip=Path(res["clip"]), words=Path(res["words"]),
                         cover=Path(res["cover"]) if res.get("cover") else None, sources=res.get("sources") or [],
                         why=f"the night maker: {what}, footage via {', '.join(res.get('creators') or [])}",
                         fmt="edit", host=", ".join(res.get("creators") or []) or None)
    if not ok:
        M._fail(cfg, acct, day, [k], f"{what} made but not placed: " + " ".join(x.strip() for x in lines)[:500],
                kind="place")
        return f"  {k}: {what} made, NOT placed: " + " ".join(x.strip() for x in lines)[:300]
    placed_file_no(acct, n)
    for x in [p["vehicle"]] + ([p["rival"]] if p.get("rival") else []):
        remember(acct, x["slug"], day)
    remember_intro(acct, p.get("intro"), day)
    return f"  {k}: {what}, {res.get('seconds')}s, placed for Approve"


def _make_spares(cfg: C.Config, acct: C.Account, day: date, picks: Dict[str, dict], log_p: Path) -> List[str]:
    """After the day's slots, a spare edit or two for the bench (bench.fill puts one in a slot the night left empty,
    at 06:18), only while the bench is under its "low" and before spares_until: the slots always come first."""
    import sys
    from . import bench as BN
    st, bs = settings(acct), BN.settings(acct)
    if not bs.get("fill") or int(st["spares"]) <= 0:
        return []
    have = len(BN.ready_items(cfg, acct, cfg.now()))
    want = min(int(st["spares"]), int(bs["low"]) - have)
    if want <= 0:
        return [f"  spares: {have} ready on the bench (keep {bs['low']}): none made"]
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from garage import plan as GP, vehicles as GV
    taken = {x["slug"] for p in picks.values() for x in [p["vehicle"]] + ([p["rival"]] if p.get("rival") else [])}
    game = game_for(acct, day)
    if not GV.db_for(game).exists():
        return []
    extra = GV.pick_day(GV.load(GV.db_for(game)), day, 8,
                        {k: v for k, v in history(acct).items() if v < day.isoformat()}, taken=taken, salt="spare")
    # a spare opens on an intro too (10-31: the bench put the night's intro-less spare in 09:35, and it posted)
    extra = with_intros(acct, day, extra[:want], also=[p.get("intro") for p in picks.values()])
    lines = []
    for i, p in enumerate(extra):
        if cfg.now().strftime("%H:%M") >= st["spares_until"] and cfg.now().strftime("%H:%M") < "12:00":
            lines.append(f"  spares: past {st['spares_until']}: no more tonight")
            break
        p = dict(p, beat=GP.beat_for(day.toordinal() + 3, i), game=game)
        said: List[str] = []
        n = file_no(acct)
        work = home(acct) / "work" / day.isoformat() / f"spare{i + 1}"
        res = _cut(acct, f"spare {i + 1}", p, work, n, log_p, lambda why, kind: said.append(f"{kind}: {why}"))
        if res is None:
            lines.append(f"  spare {i + 1}: not made ({said[-1][:220] if said else '?'})")
            continue
        folder = work / "bench" / Path(res["clip"]).stem
        folder.mkdir(parents=True, exist_ok=True)
        for f in [res["clip"]] + ([res["cover"]] if res.get("cover") else []) + \
                 [str(x) for x in Path(res["words"]).glob("*.txt")]:
            shutil.copy2(f, folder / Path(f).name)
        (folder / "source.json").write_text(json.dumps({"urls": res.get("sources") or [],
                                                         "creators": res.get("creators") or []}))
        ok, out = BN.add(cfg, acct, str(folder), why=f"the night maker's spare: {describe(p)}", kind="edit",
                         by="Garage night maker", fmt="edit")
        lines += [f"  spare {i + 1}: {describe(p)}: " + ("on the bench" if ok else "NOT added")] + \
                 ([] if ok else [x for x in out])
        if ok:
            placed_file_no(acct, n)
            for x in [p["vehicle"]] + ([p["rival"]] if p.get("rival") else []):
                remember(acct, x["slug"], day)
            remember_intro(acct, p.get("intro"), day)
    return lines


def _second_pass(cfg: C.Config, acct: C.Account, day: date, picks: Dict[str, dict], todo: List[str],
                 lead: timedelta, log_p: Path) -> List[str]:
    """Each slot the first pass left failed gets one more go, before the spares: another vehicle of its kind on the
    same intro. Andrew 10-31, "none of those intro videos came through": 4 of the 6 failed on footage and the night
    was done by 00:42, so the bench put an intro-less spare in 09:35. picks takes the new vehicles (the spares skip
    them)."""
    import sys
    from . import renderlock as RL
    if not settings(acct).get("second_pass", True):
        return []
    with B.edit(cfg, acct, day) as bd:
        failed = [k for k in todo if bd["slots"][k].get("status") == B.FAILED]
    if not failed:
        return []
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from garage import vehicles as GV
    taken = {x["slug"] for p in picks.values() for x in [p["vehicle"]] + ([p["rival"]] if p.get("rival") else [])}
    extra = GV.pick_day(GV.load(GV.db_for(game_for(acct, day))), day, len(GV.MIX),
                        {k: v for k, v in history(acct).items() if v < day.isoformat()}, taken=taken, salt="again")
    lines = []
    for k in failed:
        p0 = picks[k]
        busy = {x["slug"] for p in picks.values() for x in [p["vehicle"]] + ([p["rival"]] if p.get("rival") else [])}
        free = [p for p in extra if p["vehicle"]["slug"] not in busy and (p.get("rival") or {}).get("slug") not in busy]
        rep = next((p for p in free if p["kind"] == p0["kind"]), None) or (free[0] if free else None)
        if rep is None:
            lines.append(f"  {k}: no other vehicle for a second try: left failed")
            continue
        if cfg.slot_time(day, k) - cfg.now() < lead:
            lines.append(f"  {k}: under {lead.total_seconds() / 60:.0f} minutes away now: no second try")
            continue
        start_by, _ = RL.night_limits()
        if start_by is not None and time.time() >= start_by:
            lines.append(f"  {k}: past the night's last start: no second try")
            continue
        p = dict(rep, slot_index=p0["slot_index"], intro=p0.get("intro"), beat=p0["beat"], game=p0["game"])
        picks[k] = p
        lines.append(_make_one(cfg, acct, day, k, p, log_p).replace(f"  {k}:", f"  {k} (second try):", 1))
    return lines


def tidy(acct: C.Account, today: date) -> None:
    """Pulled footage and renders older than keep_work_days go (a night's work is several GB of 4K)."""
    keep = int(settings(acct)["keep_work_days"])
    root = home(acct) / "work"
    if not root.is_dir():
        return
    for d in root.iterdir():
        try:
            old = date.fromisoformat(d.name) < today - timedelta(days=keep)
        except ValueError:
            continue
        if old:
            subprocess.run(["rm", "-rf", str(d)], check=False)


def make(cfg: C.Config, acct: C.Account, day: date, only: Optional[List[str]] = None, dry_run: bool = False,
         now: Optional[datetime] = None) -> List[str]:
    from . import makers as M
    lock = M.render_lock(acct)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as lf:
        if not dry_run:
            fcntl.flock(lf, fcntl.LOCK_EX)      # one Garage make at a time
        start = now or cfg.now()
        lead = timedelta(minutes=float(settings(acct)["lead_minutes"]))
        with B.edit(cfg, acct, day) as bd:
            board = json.loads(json.dumps(bd))
        picks = picks_for(cfg, acct, day, board)
        lines: List[str] = []
        if not picks and game_for(acct, day) == "GTA 6":
            return [f"{acct.name} {day}: {game_for(acct, day)} from {settings(acct)['gta6_from']}, and no GTA 6 vehicle "
                    f"list yet (python3 -m garage.vehicles_build <GTABase's GTA 6 vehicle pages> --out "
                    f"garage/vehicles-gta6.json): the slots stay open for a drop"]
        todo = []
        for k, s in sorted(board["slots"].items()):
            if s.get("status") not in (B.OPEN, B.FAILED) or (only and k not in only) or s.get("spare"):
                continue
            if cfg.slot_time(day, k) - start < lead:
                lines.append(f"  {k}: under {lead.total_seconds() / 60:.0f} minutes away: left open")
                continue
            if k not in picks:
                lines.append(f"  {k}: no vehicle left for this slot's kind: left open")
                continue
            todo.append(k)
        if not todo:
            return lines + [f"{acct.name} {day}: no open slot to make"]
        if dry_run:
            for k in todo:
                p = picks[k]
                lines.append(f"  {k}: would make {p['vehicle']['name']} ({p['kind']}, {p['format']}"
                             + (f", vs {p['rival']['name']}" if p.get("rival") else "")
                             + (f") after the intro {p['intro']} on its song" if p.get("intro") else f") on {p['beat']}"))
            return lines
        log_p = M.log_path(acct, day, "garage")
        with contextlib.suppress(OSError):
            tidy(acct, day)
        from . import renderlock as RL
        for k in todo:
            if cfg.slot_time(day, k) - (now or cfg.now()) < lead:
                lines.append(f"  {k}: under {lead.total_seconds() / 60:.0f} minutes away now: left open")
                continue
            start_by, _ = RL.night_limits()
            if start_by is not None and time.time() >= start_by:      # 05:50: nothing new starts before Andrew signs
                lines.append(f"  {k}: past the night's last start: left open")
                continue
            lines.append(_make_one(cfg, acct, day, k, picks[k], log_p))
        lines += _second_pass(cfg, acct, day, picks, todo, lead, log_p)
        lines += _make_spares(cfg, acct, day, picks, log_p)
        return lines
