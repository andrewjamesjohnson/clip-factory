"""Replay real nights before an engine change goes live (the owner, 2026-10-31, the loops thesis item 7: "Yes, build 7").
On 10-28 28 of 30 Politics clips lost their hook and the VS card never fired, live for days. A replay runs real nights'
own jobs again through the engine as it is now, on frozen copies, and says what changed before the next night uses it.

  factory.py replay freeze [--nights 3]   clone (APFS, no extra space while the originals live) every job the last N
                                          Politics boards used, and its source video, into state/replay/golden/<board>/;
                                          write down what the real night made (each clip's length, cold open, cards)
                                          and the engine's preflight verdicts on it today: the golden set
  factory.py replay run [--render N]      preflight again on a fresh clone of each frozen job: every check that
                                          changed and every new FAIL; --render N also renders N clips and compares
                                          length, cold open and cards with what the night made
  factory.py replay show                  the last run's report

Preflight renders nothing (seconds a job); a render is 1 to 3 minutes a clip, so run it in the daytime, after an engine
change and before the night. Nothing real is ever touched: each copy's project.json points out_dir and source at the
copy itself, so a replay can never write into a job, a delivery folder or a board. Politics jobs only (the GTA skill runs
another way). Transcripts are not kept with the jobs, so OPENING (the in-points) is not checked on a replay.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config as C
from . import store

ENGINE = Path.home() / ".claude" / "skills" / "viral-clip-engine" / "scripts"
PY = os.environ.get("CLIP_ENGINE_PY") or str(Path.home() / "opt" / "anaconda3" / "bin" / "python3")
JOBS = Path.home() / "Desktop" / "Clipping" / "Jobs"
ACCOUNT = "politics"
CHECK = re.compile(r"^\s*\[(ok  |FAIL|\.\.  |warn|WARN|NO  )\]\s+([A-Z_]+)\s*(.*)$")
VERDICT = re.compile(r"^(ALL CLEAR.*|STOPPED.*|.*REFUSED.*)$")
SECONDS_SLACK = 0.5


def root() -> Path:
    return store.path("replay").resolve()     # absolute: the engine runs from its own folder


def safe(name: str) -> str:
    """A job folder name without spaces (the engine refuses a CLIP_WORK with a space: ffmpeg's filter graph)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "job"


def _clone(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-cRp", str(src), str(dst)], check=True)


def _seconds(p: Path) -> Optional[float]:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return round(float(out), 2)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def made(job: Path) -> Dict[str, dict]:
    """What a night made, per clip file: its length, its cold open's words, its cards' names."""
    out: Dict[str, dict] = {}
    for mp4 in sorted(list((job / "clips").glob("*.mp4")) + list(job.glob("[0-9][0-9]-*.mp4"))):
        d: dict = {"seconds": _seconds(mp4), "cold_open": None, "cards": []}
        co = mp4.with_suffix(".coldopen.json")
        if co.is_file():
            try:
                d["cold_open"] = str(json.loads(co.read_text()).get("text") or "")[:120]
            except ValueError:
                d["cold_open"] = "?"
        cj = mp4.with_suffix(".cards.json")
        if cj.is_file():
            try:
                d["cards"] = [str(c.get("name") or c.get("kind") or "card") for c in json.loads(cj.read_text()).get("cards") or []]
            except (ValueError, AttributeError):
                d["cards"] = ["?"]
        out[mp4.name] = d
    return out


def checks(out: str) -> Dict[str, str]:
    """Preflight's verdicts: {GATE: 'ok|FAIL|..: words'} (a gate printed again for another clip is GATE#2) and the
    final VERDICT line."""
    got: Dict[str, str] = {}
    for ln in out.splitlines():
        m = CHECK.match(ln)
        if m:
            key, n = m.group(2), 2
            while key in got:
                key, n = f"{m.group(2)}#{n}", n + 1
            got[key] = f"{m.group(1).strip()}: {' '.join(m.group(3).split())}"
        elif VERDICT.match(ln.strip()):
            got["VERDICT"] = ln.strip()
    return got


def _point_at_itself(work: Path, master: Optional[Path]) -> None:
    """The copy's project.json: out_dir is the copy itself, the source its own cloned master."""
    pj = work / "project.json"
    p = json.loads(pj.read_text())
    p["out_dir"] = str(work)
    if master is not None:
        p["source"] = str(master)
    pj.write_text(json.dumps(p, indent=1))


def run_engine(script: str, work: Path, args: Tuple[str, ...] = (), timeout: int = 900) -> Tuple[int, str]:
    work = Path(work).resolve()
    if " " in str(work):
        raise SystemExit(f"replay: a work folder with a space cannot run the engine: {work}")
    r = subprocess.run([PY, script, *args], cwd=str(ENGINE), env=dict(os.environ, CLIP_WORK=str(work)),
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def freeze_job(src: Path, board: str) -> dict:
    """Clone one job and its master, point the copy at itself, and write its golden record next to it."""
    dst = root() / "golden" / board / safe(src.name)
    golden = {"board": board, "job": src.name, "dir": dst.name, "source_job": str(src), "frozen_at": datetime.now().isoformat(
        timespec="seconds"), "made": made(src)}
    p = json.loads((src / "project.json").read_text())
    master = Path(str(p.get("source") or ""))
    if not master.is_file():
        golden["skip"] = "its source video is gone"
    else:
        _clone(src, dst)
        own = dst / ("master" + master.suffix)
        subprocess.run(["cp", "-c", str(master), str(own)], check=True)
        _point_at_itself(dst, own)
        rc, out = run_engine("preflight.py", dst)
        golden["preflight"] = checks(out)
        golden["preflight_rc"] = rc
    dst.parent.mkdir(parents=True, exist_ok=True)
    (dst.parent / f"{dst.name}.golden.json").write_text(json.dumps(golden, indent=1))
    return golden


def jobs_of_night(cfg: C.Config, day: date) -> List[Path]:
    from . import board as B
    from . import bench as BN
    acct = cfg.account(ACCOUNT)
    bd = B.read(acct, day) or {}
    out: List[Path] = []
    for s in (bd.get("slots") or {}).values():
        if not isinstance(s, dict) or not s.get("clip"):
            continue
        try:
            j = BN._job_of(acct, s, BN._sched(acct), day.isoformat())
        except Exception:  # noqa: BLE001
            j = s.get("job")
        if not j:
            continue
        p = Path(str(j))
        p = p if p.is_absolute() else JOBS / str(j)
        if (p / "slate.json").is_file() and (p / "project.json").is_file() and p not in out:
            out.append(p)
    return out


def freeze(cfg: C.Config, nights: int = 3, today: Optional[date] = None) -> List[str]:
    today = today or date.today()
    lines, found = [], 0
    for back in range(1, 15):
        if found >= nights:
            break
        day = today - timedelta(days=back)
        jobs = jobs_of_night(cfg, day)
        if not jobs:
            continue
        found += 1
        for src in jobs:
            if (root() / "golden" / day.isoformat() / f"{safe(src.name)}.golden.json").is_file():
                lines.append(f"  {day} {src.name}: frozen already")
                continue
            g = freeze_job(src, day.isoformat())
            lines.append(f"  {day} {src.name}: " + (g.get("skip") or f"{len(g['made'])} clip(s), preflight "
                                                   f"{g['preflight'].get('VERDICT', 'no verdict')}"))
    return [f"replay freeze: {found} night(s)"] + lines


def compare(golden: dict, now: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """(changed checks, new FAILs) between the golden preflight and now."""
    old = golden.get("preflight") or {}
    changed = [f"{k}: {old.get(k, '-')}  ->  {now.get(k, '-')}" for k in sorted(set(old) | set(now)) if old.get(k) != now.get(k)]
    new_fail = [k for k, v in now.items() if v.startswith("FAIL") and not str(old.get(k, "")).startswith("FAIL")]
    return changed, new_fail


def compare_made(golden: dict, now: Dict[str, dict]) -> List[str]:
    out = []
    for name, g in (golden.get("made") or {}).items():
        n = now.get(name)
        if n is None:
            out.append(f"{name}: not made")
            continue
        if g.get("seconds") and n.get("seconds") and abs(g["seconds"] - n["seconds"]) > SECONDS_SLACK:
            out.append(f"{name}: {g['seconds']} s -> {n['seconds']} s")
        if bool(g.get("cold_open")) != bool(n.get("cold_open")):
            out.append(f"{name}: cold open {'lost' if g.get('cold_open') else 'new'}")
        if g.get("cards") != n.get("cards"):
            out.append(f"{name}: cards {g.get('cards')} -> {n.get('cards')}")
    return out


def run(render: int = 0) -> List[str]:
    goldens = sorted((root() / "golden").glob("*/*.golden.json"))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = root() / "runs" / stamp
    report: dict = {"at": stamp, "jobs": []}
    rendered, n_same, n_changed, fails = 0, 0, 0, 0
    for gp in goldens:
        g = json.loads(gp.read_text())
        if g.get("skip"):
            continue
        frozen = gp.parent / g.get("dir", safe(g["job"]))
        work = base / g["board"] / frozen.name
        _clone(frozen, work)
        own = next(iter(sorted(work.glob("master.*"))), None)
        _point_at_itself(work, own)
        rc, out = run_engine("preflight.py", work)
        changed, new_fail = compare(g, checks(out))
        row = {"board": g["board"], "job": g["job"], "changed": changed, "new_fail": new_fail}
        if render and rendered < render and not new_fail:
            run_engine("build_all.py", work, timeout=1800)
            row["made"] = compare_made(g, made(work))
            rendered += 1
        n_same += not changed and not row.get("made")
        n_changed += bool(changed or row.get("made"))
        fails += bool(new_fail)
        report["jobs"].append(row)
    (root() / "last.json").write_text(json.dumps(report, indent=1))
    for old in sorted((root() / "runs").glob("*"))[:-2]:      # the last two runs are kept
        shutil.rmtree(old, ignore_errors=True)
    return words(report) if goldens else ["replay: nothing frozen yet (factory.py replay freeze)"]


def words(report: dict) -> List[str]:
    jobs = report.get("jobs") or []
    same = [j for j in jobs if not j["changed"] and not j.get("made")]
    out = [f"replay {report.get('at', '')}: {len(jobs)} real jobs, {len(same)} the same, "
           f"{len(jobs) - len(same)} changed, {sum(1 for j in jobs if j['new_fail'])} with a new FAIL"]
    for j in jobs:
        if j["new_fail"]:
            out.append(f"  NEW FAIL {j['board']} {j['job']}: {', '.join(j['new_fail'])}")
        for c in (j["changed"] + j.get("made", []))[:6]:
            out.append(f"  {j['board']} {j['job']}: {c}"[:230])
    return out


# ------------------------------------------------------------------------------------------------------------ the CLI
def add_parser(sub) -> None:
    p = sub.add_parser("replay", help="replay real nights through the engine before a change goes live (clipfactory/replay.py)")
    p.add_argument("action", choices=["freeze", "run", "show"])
    p.add_argument("--nights", type=int, default=3)
    p.add_argument("--render", type=int, default=0)
    p.set_defaults(fn=cmd)


def cmd(cfg: C.Config, a) -> None:
    if a.action == "freeze":
        print("\n".join(freeze(cfg, a.nights)))
    elif a.action == "run":
        print("\n".join(run(a.render)))
    else:
        p = root() / "last.json"
        print("\n".join(words(json.loads(p.read_text()))) if p.is_file() else "replay: no run yet")
