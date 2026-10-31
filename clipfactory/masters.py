"""THE KEPT MASTERS (Daily 8 step 4, 2026-10-29; the plan Andrew took 10.28: "Fight videos sit in a temp folder that a
restart can wipe. Part 2s need them kept safe for 14 days."). A Part 2 is cut from the rest of the SAME video the hit
came from, two or three days later; the night's work folders live in /private/tmp/clipwork, which macOS empties at a
restart. So at collect every Politics clip's work folder is kept as an APFS clone (cp -c: no space until the temp copy
goes, then it keeps its blocks) under state/<account>/masters/<work>/, with keep.json, for KEEP_DAYS; then pruned.

    python3 -m clipfactory.masters status  [account]
    python3 -m clipfactory.masters restore <account> <work>   the kept folder back to /private/tmp/clipwork/<work>
    python3 -m clipfactory.masters prune   [account]          the ones past KEEP_DAYS (collect prunes too)

Only what a cut needs is kept: the master and its info, the transcript, the cues, the project and slate, the 16 kHz
sound and the furniture still. Never the tmp/ folder or renders.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from . import config as C

KEEP_DAYS = 14
CLIPWORK = Path("/private/tmp/clipwork")
KEEP_FILES = ("master.mp4", "master.mkv", "master.webm", "master.mov", "master.info.json", "transcript.srt",
              "transcript.txt", "transcript_compact.txt", "cues.json", "project.json", "slate.json", "audio16k.wav",
              "furniture.jpg", "sections.json", "cast.json", "glossary.json")
KEEP_DIRS = ("captions",)


def root(acct) -> Path:
    return C.STATE / acct.slug / "masters"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def work_of(acct, slot: dict) -> Optional[Path]:
    """The work folder the slot's clip was cut from: its job's project.json "source" sits in it."""
    from . import vscard as VS
    job = VS.slot_job(slot, acct)
    if not job:
        return None
    try:
        proj = json.loads((VS.JOBS / job / "project.json").read_text())
    except (OSError, ValueError):
        return None
    src = Path(str(proj.get("source") or ""))
    return src.parent if src.name and src.parent.name and src.parent != Path(".") else None


def _clone(src: Path, dst: Path) -> None:
    """An APFS clone when it can (cp -c), else a plain copy."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cp", "-c", "-p", str(src), str(dst)], capture_output=True)
    if r.returncode != 0:
        shutil.copy2(src, dst)


def keep(acct, slot: dict, now: Optional[datetime] = None) -> Tuple[Optional[Path], str]:
    """Keep the slot's work folder for KEEP_DAYS (again: its clock starts over). (kept folder, '') or (None, why)."""
    work = work_of(acct, slot)
    if work is None:
        return None, "the clip's job or its work folder is not known"
    if not work.is_dir():
        return None, f"{work} is gone already"
    dst = root(acct) / work.name
    n = 0
    for name in KEEP_FILES:
        f = work / name
        if f.is_file() and not (dst / name).is_file():
            _clone(f, dst / name)
            n += 1
    for name in KEEP_DIRS:
        d = work / name
        if d.is_dir() and not (dst / name).exists():
            shutil.copytree(d, dst / name, copy_function=shutil.copy2)
    if not dst.is_dir():
        return None, f"nothing to keep in {work}"
    t = now or _now()
    meta_p = dst / "keep.json"
    try:
        meta = json.loads(meta_p.read_text())
    except (OSError, ValueError):
        meta = {"work": work.name, "from": str(work), "slugs": []}
    if slot.get("slug") and slot["slug"] not in meta["slugs"]:
        meta["slugs"].append(slot["slug"])
    meta.update(kept=t.isoformat(timespec="seconds"),
                until=(t + timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds"))
    meta_p.write_text(json.dumps(meta, indent=1))
    return dst, ""


def kept(acct) -> List[dict]:
    out = []
    r = root(acct)
    if not r.is_dir():
        return out
    for d in sorted(r.iterdir()):
        try:
            meta = json.loads((d / "keep.json").read_text())
        except (OSError, ValueError):
            continue
        out.append({**meta, "path": str(d)})
    return out


def prune(acct, now: Optional[datetime] = None) -> List[str]:
    """Remove the kept folders past their `until`; the names removed."""
    t = now or _now()
    gone = []
    for m in kept(acct):
        try:
            until = datetime.fromisoformat(str(m["until"]))
        except (KeyError, ValueError):
            continue
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if t >= until:
            shutil.rmtree(m["path"], ignore_errors=True)
            gone.append(Path(m["path"]).name)
    return gone


def restore(acct, work: str) -> Tuple[Optional[Path], str]:
    """The kept folder back where the engine reads it, /private/tmp/clipwork/<work> (files it lacks only)."""
    src = root(acct) / work
    if not src.is_dir():
        return None, f"no kept master for {work} (kept: {', '.join(m['work'] for m in kept(acct)) or 'none'})"
    dst = CLIPWORK / work
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in src.iterdir():
        if f.name == "keep.json":
            continue
        if f.is_dir():
            if not (dst / f.name).exists():
                shutil.copytree(f, dst / f.name, copy_function=shutil.copy2)
                n += 1
        elif not (dst / f.name).exists():
            _clone(f, dst / f.name)
            n += 1
    return dst, f"{n} file(s) back in {dst}"


def after_collect(acct, slot: dict) -> str:
    """At collect (the Politics): keep this clip's master, prune the old ones. Never raises."""
    try:
        dst, why = keep(acct, slot)
        gone = prune(acct)
        return (f"master kept {KEEP_DAYS} days ({dst.name})" if dst else f"master not kept: {why}") + \
            (f"; pruned {len(gone)}" if gone else "")
    except Exception as e:  # noqa: BLE001  (a clip is never lost over its master)
        return f"master not kept: {e}"[:200]


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="masters")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("account", nargs="?", default="politics")
    r = sub.add_parser("restore")
    r.add_argument("account")
    r.add_argument("work")
    p = sub.add_parser("prune")
    p.add_argument("account", nargs="?", default="politics")
    a = ap.parse_args(argv)
    cfg = C.load()
    acct = cfg.account(a.account)
    if a.cmd == "status":
        ms = kept(acct)
        print(f"{acct.name}: {len(ms)} kept master(s), {KEEP_DAYS} days each")
        for m in ms:
            print(f"  {m['work']}  until {m.get('until', '?')[:16]}  {', '.join(m.get('slugs') or [])[:90]}")
        return 0
    if a.cmd == "restore":
        dst, why = restore(acct, a.work)
        print(why)
        return 0 if dst else 1
    gone = prune(acct)
    print(f"pruned {len(gone)}: {', '.join(gone)}" if gone else "nothing past its 14 days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
