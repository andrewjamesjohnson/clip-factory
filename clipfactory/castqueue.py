"""The cast queue: every person a Politics source names whom nobody has checked yet, with the stills to look at,
and Andrew's one-tap check on the review page (Clip Review, "Casts to check", at the top of the page).

Why (the 2026-10-17 investigation): about 3 or 4 Politics slots a day were lost because a clip naming two people waits
for the owner's cast check, and the only way to give it was Approve on a clip already cut, or editing JSON by hand.
The 15:00 prep run (task politics-prep) downloads and analyzes tomorrow's sources and writes their casts with `verified`
empty; this module lists those people, and his Confirm writes `verified` in his own name, so the 21:00 run cuts them.

    factory.py casts [--json]                      who waits, per source (read only)
    factory.py casts --confirmed [--since today]   what he confirmed (here, or by Approve at a sign-off): for the runs
    factory.py casts --stills                      pull the missing stills (one small frame per time, from the master)
    factory.py casts --notify                      a Mac notice when anything waits (the 15:00 prep ends with it)

Who is asked about: a person with a name in a job's cast.json (or a cast.prep-*.json proposal beside it) whose
`verified` is empty, only `provisional`, or written by a bot (signoff.BOT_VERIFIED: Patel, Booker, Trump on 10-16),
when a slate puts them on a plate, the file is a proposal, or a bot wrote the check. A person on no plate (an
anchor, a reporter, a voter: the runbook says askers are `questions`, never cast) is never asked about. The times to look at come from the entry's `check_frames` (master seconds; the prep writes them), else from the
bot's own words (`provisional.frames`, `_verify_at`, a bot's `verified`). A still is one small frame per time, cut from
the job's master (project.json "source") into <job>/castcheck/<id>-<sec>.jpg: never a render. Its rules are
viral-clip-engine cast.py's (needs_look, check_frames, rejected_now, rejected_changed, still_name), word for word; a
test holds them equal. A person whose stills cannot be had here (no stills, the video is not on this Mac, and the job
records no link to fetch it again, or the prep wrote "source_gone" in project.json) is not put on the page: it is
listed by `factory.py casts`, and Approve at the sign-off stays its check.

NO BOT CAN CONFIRM. There is no command that does it. "That's <name>", "Not them" and "Undo" come only from the review
page: its token, a browser request from the page itself (Origin; review._from_page), the item's seen key (what the page
showed, as on disk now: a bot that rewrote the entry since makes the tap refuse), and a name on the approvers list that
is no bot's. The write goes through signoff.edit_cast: only a job's cast files, inside signoff.CAST_ROOTS, under a
lock, atomic. A proposal person he confirms is copied into cast.json with his check (the file the engine reads).
"Not them" writes `rejected` (his name, the time, the name, title and times he saw). It STANDS until his own tap takes
it out: Undo, or "That's <name>" after a new look. While it is there the engine's cast check refuses the entry in
every clip, whatever the bot changed since, and Approve refuses a clip already cut. When the bot changes the name,
the title or the times, the person comes back here as a new look ("relook": "You said Not them on <date>; the bot
changed the times"), still refused until he confirms. A cut clip also stays held when its plated person is gone
from cast.json, when the plate carries a name he said Not them to that the cast has since renamed, and when the tap
log says he said Not them and the cast file no longer does (holds()). Every tap is logged in state/castqueue/log.jsonl.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import urllib.parse
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import config as C
from . import signoff as S

STILLS = "castcheck"            # viral-clip-engine cast.STILLS, the same
MAX_FRAMES = 4                  # viral-clip-engine cast.MAX_FRAMES, the same
STILL_HEIGHT = 480              # a still is at most this tall (never taller than the master)
CAST_FILE = S.CAST_FILE         # cast.json, cast.prep-<x>.json
SLATE_FILE = re.compile(r"^slate(?:\.prep-[A-Za-z0-9_-]+)?\.json$")
MOCK = re.compile(r"MOCK-\d{4}-\d{2}-\d{2}$")
OPS = ("cast-confirm", "cast-reject", "cast-undo", "cast-stills")
# viral-clip-engine cast._SECS / cast._CLOCK, the same: "40s and 180s", "12253.5s"; "0:41" or "1:02:03" (only in
# provisional.frames, the runbook's own form; elsewhere a clock time is often caption time, not master time)
_SECS = re.compile(r"(?<![\w.:])(\d{1,6}(?:\.\d{1,2})?) ?s\b")
_CLOCK = re.compile(r"(?<![\w.:])(\d{1,2}):([0-5]\d)(?::([0-5]\d))?(?![\w:])")


# ------------------------------------------------------------------------------------------- the engine's rules
def _norm(s: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def check_frames(p: dict) -> List[float]:
    """The master seconds to look at for one cast person, at most MAX_FRAMES (viral-clip-engine cast.check_frames)."""
    def clean(xs) -> List[float]:
        out: List[float] = []
        for x in xs:
            try:
                v = round(float(x), 1)
            except (TypeError, ValueError):
                continue
            if v >= 0 and v not in out:
                out.append(v)
        return sorted(out)[:MAX_FRAMES]
    cf = p.get("check_frames")
    if isinstance(cf, list):
        got = clean(cf)
        if got:
            return got
    pv = p.get("provisional") if isinstance(p.get("provisional"), dict) else {}
    said = str(p.get("verified") or "")
    picked: List[float] = []            # the bot's notes, in this order; a time within 1 s of one taken is the same
    for text, clock in ((pv.get("frames"), True), (p.get("_verify_at"), False),
                        (said if S.BOT_VERIFIED.search(said) else "", False)):
        text = str(text or "")
        xs: list = [m.group(1) for m in _SECS.finditer(text)]
        if clock:
            for m in _CLOCK.finditer(text):
                a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
                xs.append(a * 60 + b if c is None else a * 3600 + b * 60 + int(c))
        for v in clean(xs):
            if len(picked) < MAX_FRAMES and all(abs(v - w) >= 1.0 for w in picked):
                picked.append(v)
    return sorted(picked)


def rejected_now(p: dict) -> bool:
    """The owner's "Not them" on this entry stands: `rejected` is there, whatever changed since. Only his own tap in
    Clip Review takes it out (Undo, or "That's <name>" after a new look) (cast.rejected_now)."""
    return "rejected" in p


def rejected_changed(p: dict) -> str:
    """What changed since he said Not them: '' (nothing: the same name, title and times, or a rejection nobody can
    read), else "the name", "the title", "the times", joined by " and " (cast.rejected_changed)."""
    r = p.get("rejected")
    if not isinstance(r, dict):
        return ""
    try:
        frames = [round(float(x), 1) for x in (r.get("frames") or [])]
    except (TypeError, ValueError):
        return ""
    what = [w for w, k in (("the name", "name"), ("the title", "title")) if _norm(r.get(k)) != _norm(p.get(k))]
    if frames != check_frames(p):
        what.append("the times")
    return " and ".join(what)


def needs_look(p: dict) -> str:
    """'' (a person checked it), 'empty', 'provisional', 'bot' (a bot wrote `verified`), 'rejected' (he said Not them
    to it as it is) or 'relook' (he said Not them and it changed since: it waits for his new look) (cast.needs_look)."""
    if rejected_now(p):
        return "relook" if rejected_changed(p) else "rejected"
    said = str(p.get("verified") or "").strip()
    if said:
        return "bot" if S.BOT_VERIFIED.search(said) else ""
    return "provisional" if isinstance(p.get("provisional"), dict) else "empty"


def still_name(pid: str, sec: float) -> str:
    """vance, 40 -> 'vance-40.0.jpg' (cast.still_name)."""
    safe = re.sub(r"[^a-z0-9_-]+", "-", str(pid).lower()).strip("-") or "person"
    return f"{safe}-{float(sec):.1f}.jpg"


# ------------------------------------------------------------------------------------------- where the jobs are
def roots() -> List[Path]:
    """The Jobs folders (signoff.CAST_ROOTS, read at call time), each once by its real path."""
    out: List[Path] = []
    for r in S.CAST_ROOTS:
        try:
            rr = Path(r).expanduser().resolve()
        except OSError:
            continue
        if rr.is_dir() and rr not in out:
            out.append(rr)
    return out


def _in_roots(job: Path) -> bool:
    """A job folder sits right inside a Jobs folder, by its real path (a link out of them is no job)."""
    try:
        real = Path(job).resolve()
    except OSError:
        return False
    return any(real.parent == r for r in roots())


def job_dir(name: str) -> Path:
    """The job folder a request names: a plain folder name, right inside a Jobs folder. Anything else refuses."""
    name = str(name or "")
    if not name or name in (".", "..") or name != Path(name).name or "/" in name or "\x00" in name \
            or name.startswith((".", "_")) or MOCK.match(name):
        raise SystemExit("refused: that is not a job folder in the Jobs folders")
    for r in roots():
        d = r / name
        if d.is_dir():
            if not _in_roots(d):
                raise SystemExit(f"refused: {name} leads outside the Jobs folders")
            return d.resolve()
    raise SystemExit(f"refused: no job {name!r} in the Jobs folders")


def jobs() -> List[Path]:
    """Every job folder (real paths, each once), by name. Never MOCK-* (a factory test job) or _/. names."""
    seen: Dict[Path, None] = {}
    for r in roots():
        try:
            kids = sorted(r.iterdir())
        except OSError:
            continue
        for d in kids:
            if d.name.startswith((".", "_")) or MOCK.match(d.name) or not d.is_dir() or not _in_roots(d):
                continue
            m = re.match(r"^(.*) \d$", d.name)          # an iCloud copy of a job folder ("<job> 2"): never a job
            if m and (r / m.group(1)).is_dir():
                continue
            seen.setdefault(d.resolve(), None)
    return sorted(seen, key=lambda p: p.name)


def _read(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _cast_files(job: Path) -> List[str]:
    """cast.json first, then the proposals beside it (an iCloud 'cast 2.json' copy is no cast file)."""
    try:
        names = sorted(f.name for f in job.iterdir() if CAST_FILE.match(f.name) and f.is_file())
    except OSError:
        return []
    return (["cast.json"] if "cast.json" in names else []) + [n for n in names if n != "cast.json"]


def _carded(job: Path) -> Dict[str, List[dict]]:
    """person id -> the slate clips that put them on a plate."""
    out: Dict[str, List[dict]] = {}
    try:
        slates = sorted(f for f in job.iterdir() if SLATE_FILE.match(f.name) and f.is_file())
    except OSError:
        return out
    for f in slates:
        d = _read(f)
        for c in (d.get("clips") if isinstance(d, dict) else None) or []:
            if not isinstance(c, dict):
                continue
            for cd in c.get("cards") or []:
                w = str((cd or {}).get("who") or "").strip().lower() if isinstance(cd, dict) else ""
                if w and not any(u["slug"] == c.get("slug") for u in out.get(w, [])):
                    out.setdefault(w, []).append({"slug": str(c.get("slug") or ""), "template": str(c.get("template") or ""),
                                                  "file": f.name})
    return out


def _master(pj: dict) -> Optional[Path]:
    src = str((pj or {}).get("source") or "").strip()
    if not src:
        return None
    p = Path(os.path.expanduser(src))
    return p if p.is_file() else None


def _stills(job: Path, pid: str, frames: List[float]) -> List[dict]:
    out = []
    for sec in frames:
        p = job / STILLS / still_name(pid, sec)
        try:
            st = p.stat()
            have = p.is_file() and st.st_size > 0
        except OSError:
            st, have = None, False
        out.append({"sec": sec, "path": str(p), "have": have,
                    "stat": [st.st_size, st.st_mtime_ns] if have and st else None})
    return out


def key_of(job: Path, fname: str, p: dict) -> str:
    """What the page shows of one person, as on disk now: the job, the file, the whole entry, each still (size, time).
    The page sends it back with a tap, which refuses when it differs (the bot rewrote it, or a still changed)."""
    pid = str(p.get("id") or "").strip().lower()
    st = [[s["sec"], s["stat"]] for s in _stills(job, pid, check_frames(p))]
    blob = json.dumps([job.name, fname, p, st], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _youtube(url: str) -> bool:
    """YouTube's own hosts only (never a look-alike such as notyoutube.com), over http(s)."""
    try:
        u = urllib.parse.urlparse(str(url or ""))
        host = (u.hostname or "").lower()
    except ValueError:
        return False
    return u.scheme in ("http", "https") and (host in ("youtube.com", "youtu.be") or host.endswith(".youtube.com"))


def _at(url: str, sec: float) -> str:
    """The source's own page at that second (YouTube honours t=; another host gets ''): Watch at 0:40."""
    if not url or not _youtube(url):
        return ""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}t={int(sec)}s"


def read_job(job: Path, now: Optional[datetime] = None, rows: Optional[List[dict]] = None) -> Optional[dict]:
    """One job: its people who wait for the owner's look, the ones he said Not them to, the nameless ones, the ones
    on no plate, and the ones whose stills cannot be had here (`stuck`: never on the page; Approve is their check)."""
    files = _cast_files(job)
    if not files:
        return None
    now = now or datetime.now(timezone.utc)
    pj = _read(job / "project.json")
    pj = pj if isinstance(pj, dict) else {}
    carded = _carded(job)
    master = _master(pj)
    url = str(pj.get("source_url") or "")
    gone = str(pj.get("source_gone") or "").strip()     # the prep could not download it again (its words)
    people, said_no, nameless, unplated, stuck = [], [], [], [], []
    main: Dict[str, dict] = {}
    newest = 0.0
    for fname in files:
        data = _read(job / fname)
        if not isinstance(data, dict):
            continue
        try:
            newest = max(newest, (job / fname).stat().st_mtime)
        except OSError:
            pass
        for p in data.get("people") or []:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or "").strip().lower()
            if not pid:
                continue
            if fname == "cast.json":
                main[pid] = p
            elif pid in main:
                continue                # the person is in cast.json already: that entry is the one asked about
            why = needs_look(p)
            if not why:
                continue
            name = str(p.get("name") or "").strip()
            frames = check_frames(p)
            pv = p.get("provisional") if isinstance(p.get("provisional"), dict) else {}
            item = {"job": job.name, "dir": str(job), "file": fname, "id": pid, "name": name,
                    "title": str(p.get("title") or "").strip(), "role": str(p.get("role") or ""), "why": why,
                    "named_as": str(pv.get("named_as") or ""),
                    "bot_words": str(p.get("verified") or "").strip() if why == "bot" else "",
                    "note": str(pv.get("frames") or p.get("_verify_at") or "").strip(),
                    "frames": frames, "stills": _stills(job, pid, frames), "unlocks": carded.get(pid, []),
                    "proposal": fname != "cast.json", "master": bool(master), "url": url,
                    "source_title": str(pj.get("source_title") or pj.get("hearing_title") or ""),
                    "channel": str(pj.get("source_channel") or ""), "key": key_of(job, fname, p),
                    "rejected": (p.get("rejected") if isinstance(p.get("rejected"), dict) else {})
                    if why in ("rejected", "relook") else None,
                    "changed": rejected_changed(p) if why == "relook" else "", "gone": gone}
            if why == "rejected":
                said_no.append(item)
                continue
            if not name:
                nameless.append(pid)
                continue
            if not (why == "bot" or item["proposal"] or item["unlocks"]):
                unplated.append(pid)    # on no plate: an asker is `questions`, never cast (the bot takes it out)
                continue
            if not any(s["have"] for s in item["stills"]) and not master and (not url or gone):
                stuck.append(item)      # no stills, no video on this Mac, no way to fetch it: not on the page
                continue
            people.append(item)
    order = {"relook": 0, "bot": 1, "provisional": 2, "empty": 3}
    people.sort(key=lambda x: (not x["unlocks"], order.get(x["why"], 4), x["name"]))
    return {"job": job.name, "dir": str(job), "people": people, "rejected": said_no, "nameless": nameless,
            "unplated": unplated, "stuck": stuck, "lost": lost_rejections(job, rows),
            "mtime": newest, "source_title": str(pj.get("source_title") or pj.get("hearing_title") or ""),
            "channel": str(pj.get("source_channel") or ""), "master": bool(master), "url": url}


def _log_rows() -> List[dict]:
    """state/castqueue/log.jsonl, oldest first (a line nobody can read is skipped)."""
    try:
        rows = (C.STATE / "castqueue" / "log.jsonl").read_text().splitlines()
    except OSError:
        return []
    out = []
    for ln in rows:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def standing(job_name: str, rows: Optional[List[dict]] = None) -> Dict[str, dict]:
    """person id -> his "Not them" row in the tap log for this job, when no Undo or check of that person came after
    it. The log is the factory's own record, so it stands even when the cast file no longer says so."""
    last: Dict[str, dict] = {}
    for r in _log_rows() if rows is None else rows:
        if r.get("job") == job_name and r.get("op") in ("reject", "undo", "confirm", "approve-confirm"):
            last[str(r.get("person") or "").strip().lower()] = r
    return {k: r for k, r in last.items() if k and r.get("op") == "reject"}


def lost_rejections(job: Path, rows: Optional[List[dict]] = None) -> List[dict]:
    """His standing Not them rows (standing()) whose person no longer carries `rejected` in the job's cast files, or
    is gone from them: the bot took it out, which it must never do. Their cut clips stay held (holds())."""
    st = standing(job.name, rows)
    if not st:
        return []
    have = set()
    for f in _cast_files(job):
        d = _read(job / f)
        for q in (d.get("people") if isinstance(d, dict) else None) or []:
            if isinstance(q, dict) and rejected_now(q):
                have.add(str(q.get("id") or "").strip().lower())
    return [dict(r, person=k) for k, r in sorted(st.items()) if k not in have]


def pending(cfg: Optional[C.Config] = None, now: Optional[datetime] = None) -> List[dict]:
    """Every job with someone waiting (or said Not them to): sources with a plate waiting first, newest first."""
    out = []
    rows = _log_rows()                  # the tap log, read once for every job
    for job in jobs():
        j = read_job(job, now, rows)
        if j and (j["people"] or j["rejected"] or j["stuck"] or j["lost"]):
            out.append(j)
    out.sort(key=lambda j: (not any(p["unlocks"] for p in j["people"]), -j["mtime"], j["job"]))
    return out


def counts(jobs_: List[dict]) -> Tuple[int, int]:
    """(people waiting, clips that plate one of them)."""
    n = sum(len(j["people"]) for j in jobs_)
    clips = {(j["job"], u["slug"]) for j in jobs_ for p in j["people"] for u in p["unlocks"]}
    return n, len(clips)


# ------------------------------------------------------------------------------------------- stills
def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def grab(master: Path, sec: float, out: Path, timeout: int = 60) -> bool:
    """One small frame of the master at `sec` (at most STILL_HEIGHT tall, never upscaled): the same call as
    viral-clip-engine cast.py --frames --pull. Writes `out` whole or not at all."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.jpg")
    cmd = ["nice", "-n", "10", _ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{sec:.2f}",
           "-i", str(master), "-frames:v", "1", "-vf", f"scale=-2:'min({STILL_HEIGHT},ih)'", "-q:v", "4", "-y", str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        ok = r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if ok:
        os.replace(tmp, out)
    else:
        try:
            tmp.unlink()
        except OSError:
            pass
    return ok


def pull(job: Path, fname: str, pid: str) -> Tuple[int, int, str]:
    """The missing stills of one person: (pulled, still missing, why not)."""
    data = _read(job / fname)
    p = _find(data, pid) if isinstance(data, dict) else None
    if p is None:
        return 0, 0, f"{pid} is not in {fname}"
    frames = check_frames(p)
    if not frames:
        return 0, 0, "the entry gives no times to look at (the 15:00 prep writes check_frames)"
    master = _master(_read(job / "project.json") or {})
    todo = [s for s in _stills(job, pid, frames) if not s["have"]]
    if not todo:
        return 0, 0, ""
    if master is None:
        return 0, len(todo), "the source video is not on this Mac any more (the 15:00 prep downloads it again)"
    got = sum(1 for s in todo if grab(master, s["sec"], Path(s["path"])))
    return got, len(todo) - got, "" if got == len(todo) else "ffmpeg could not read the master at those times"


def pull_all(cfg: Optional[C.Config] = None) -> List[str]:
    """`factory.py casts --stills`: every waiting person's missing stills."""
    lines = []
    for j in pending(cfg):
        for p in j["people"]:
            got, left, why = pull(Path(j["dir"]), p["file"], p["id"])
            if got or left:
                lines.append(f"  {j['job']}: {p['name']}: {got} pulled" + (f", {left} missing ({why})" if left else ""))
    return lines or ["  every waiting person has their stills (or gives no times to look at)"]


def still_ok(p: Path) -> bool:
    """A still the review page may show: a .jpg in a job's castcheck folder, inside the Jobs folders."""
    try:
        p = Path(p).resolve()
    except OSError:
        return False
    return p.suffix.lower() == ".jpg" and p.parent.name == STILLS and p.is_file() and _in_roots(p.parent.parent)


# ------------------------------------------------------------------------------------------- the taps
def _find(data: Optional[dict], pid: str) -> Optional[dict]:
    for p in (data or {}).get("people") or []:
        if isinstance(p, dict) and str(p.get("id") or "").strip().lower() == pid:
            return p
    return None


def _clock(sec: float) -> str:
    """40 -> '0:40', 4325 -> '1:12:05', 1.5 -> '0:01.5' (the tenth only when it is not a whole second: his check
    line names the exact times of the stills he saw)."""
    v = round(float(sec), 1)
    s = int(v)
    tenth = int(round((v - s) * 10))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    tail = f"{s:02d}" + (f".{tenth}" if tenth else "")
    return f"{h}:{m:02d}:{tail}" if h else f"{m}:{tail}"


def _person(cfg: C.Config, by: object) -> str:
    by = str(by or "").strip()
    if not by or S.BOT_VERIFIED.search(by):
        raise SystemExit("refused: a cast is checked by a person on the approvers list, never by a bot")
    led = next((a for a in cfg.accounts.values() if a.maker == "politics"), None)
    return S._who(cfg, by, led)


def verified_line(who: str, stamp: str, secs: List[float], name: str, title: str, why: str, named_as: str,
                  again: str = "") -> str:
    """His `verified`: who, where, when, the times he looked at, and who it is. Never a bot's words (BOT_VERIFIED),
    or the engine would send it to the sign-off again."""
    at = ", ".join(_clock(x) for x in secs)
    how = {"bot": "over an unchecked note, kept as verified_before",
           "provisional": f"provisional: named in the source's title as {named_as!r}"}.get(why, "")
    if again:
        how = (f"a new look, after his Not them of {again}" + (f"; {how}" if how else ""))
    line = (f"{who}, in Clip Review's Casts ({stamp}): looked at the stills at {at} of the master and confirmed "
            f"this is {name}, {title}" + (f" ({how})" if how else ""))
    if S.BOT_VERIFIED.search(line):
        line = (f"{who}, in Clip Review's Casts ({stamp}): looked at the stills at {at} of the master and confirmed "
                f"the person the plate names")
    return line


def _set_verified(p: dict, line: str) -> None:
    was = str(p.get("verified") or "").strip()
    if was:
        p["verified_before"] = was
    p["verified"] = line


def _lift(p: dict, who: str, how: str) -> None:
    """His own tap takes his Not them out (Undo, or That's <name> after a new look): kept in `rejected_undone`."""
    if "rejected" in p:
        r = p.pop("rejected")
        p.setdefault("rejected_undone", []).append(dict(r if isinstance(r, dict) else {"was": r}, undone_by=who,
                                                        undone_at=B.utcnow(), how=how))


def _base(p: dict) -> str:
    """needs_look as if he had never said Not them (what the entry is under it: empty, provisional, bot, '')."""
    return needs_look({k: v for k, v in p.items() if k != "rejected"})


def _cast_py(cfg: C.Config) -> Path:
    """viral-clip-engine's scripts/cast.py: the Politics account's own engine."""
    led = next((a for a in cfg.accounts.values() if a.maker == "politics"), None)
    return (led.skill_dir if led else Path("~/.claude/skills/viral-clip-engine").expanduser()) / "scripts" / "cast.py"


def _engine_knows(cfg: C.Config) -> bool:
    """Does the engine's cast.py hold the cast queue's rules yet (DEPLOY.md: the engine goes in after the factory)?"""
    try:
        return "def rejected_changed(" in _cast_py(cfg).read_text()
    except OSError:
        return False


def _day(iso: object, cfg: Optional[C.Config] = None) -> str:
    """An ISO time as the day he reads: 'Oct 18' ('' when it is not a time)."""
    try:
        t = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return t.astimezone(cfg.tz() if cfg else None).strftime("%b %d").replace(" 0", " ")


def log(op: str, **kw) -> None:
    """One line per tap (and per Approve that wrote a check) in state/castqueue/log.jsonl."""
    p = C.STATE / "castqueue" / "log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(dict({"at": B.utcnow(), "op": op}, **kw), sort_keys=True) + "\n")


def _verdict(cfg: C.Config, job: Path) -> str:
    """viral-clip-engine's own `cast.py --check` on the job, in a few words ('' when the engine is not there)."""
    script = _cast_py(cfg)
    if not script.is_file():
        return ""
    py = os.environ.get("CLIP_ENGINE_PYTHON") or "/usr/bin/python3"     # makers.PY_SKILLS: the engine's own Python
    try:
        r = subprocess.run([py, "-B", str(script), "--check", "--job", str(job)],
                           capture_output=True, text=True, timeout=60, cwd=str(script.parent))
    except (OSError, subprocess.SubprocessError):
        return ""
    if r.returncode == 0:
        return "Its cast check passes: the next run can cut its clips."
    bad = [ln.strip()[5:] for ln in r.stdout.splitlines() if ln.strip().startswith("BAD ")]
    return ("Its cast check still fails on what the bot fixes" + (f": {bad[0][:200]}" if bad else "")
            + (f" (+{len(bad) - 1} more)" if len(bad) > 1 else "") + ".")


def act(cfg: C.Config, body: dict, page: bool = False) -> Tuple[bool, str]:
    """One tap in Casts: cast-confirm | cast-reject | cast-undo | cast-stills. (did it, what to say)

    `page` is review._from_page: True only for a browser request from the review page itself."""
    op = str(body.get("op") or "")
    if op not in OPS:
        raise SystemExit(f"unknown action {op!r}")
    if not page:
        return False, ("Refused: a cast is checked only on the review page, by a person (Clip Review, Casts). "
                       "No command or script does it.")
    who = _person(cfg, body.get("by"))
    job = job_dir(str(body.get("job") or ""))
    fname = str(body.get("file") or "")
    if not CAST_FILE.match(fname) or not (job / fname).is_file():
        raise SystemExit("refused: that is not a cast file of this job")
    if fname != "cast.json" and not (job / "cast.json").is_file():
        raise SystemExit("refused: this job has no cast.json to take the person in")
    pid = str(body.get("person") or "").strip().lower()
    if not pid:
        raise SystemExit("refused: which person?")
    if op == "cast-stills":
        got, left, why = pull(job, fname, pid)
        if not got and not left:
            return (not why), (why or "The stills are there.")
        log("stills", by=who, job=job.name, file=fname, person=pid, pulled=got, missing=left)
        return got > 0, (f"{got} still{'s' if got != 1 else ''} pulled." if got else "No stills.") + \
            (f" {left} could not be: {why}." if left else "")
    seen = str(body.get("seen") or "")
    if not seen:
        raise SystemExit("the page must say what it showed (reload it)")
    stamp = cfg.now().strftime("%Y-%m-%d %H:%M")
    said: Dict[str, str] = {}

    def change(datas: Dict[str, dict]) -> None:
        p = _find(datas[fname], pid)
        if p is None:
            raise SystemExit(f"NOT done: {pid} is not in {fname} any more (the bot changed the cast). Reload the page")
        if key_of(job, fname, p) != seen:
            raise SystemExit("NOT done: it changed since the page showed it (the bot rewrote it, or a still changed). "
                             "Look at it again")
        name, title, why = str(p.get("name") or "").strip(), str(p.get("title") or "").strip(), needs_look(p)
        said["name"] = name or pid
        if op == "cast-undo":
            if why not in ("rejected", "relook"):
                raise SystemExit("NOT done: there is no Not them to undo on this entry")
            _lift(p, who, "undo")
            return
        if why == "rejected":
            raise SystemExit("NOT done: you said not them to this already. Press Undo first"
                             if op == "cast-confirm" else "NOT done: you said not them to this already")
        if not why:
            raise SystemExit(f"NOT done: {said['name']} is checked already")
        if not name or not title:
            raise SystemExit("NOT done: the entry has no name or title yet (the bot fills it)")
        frames = check_frames(p)
        shown = [s["sec"] for s in _stills(job, pid, frames) if s["have"]]
        if frames and not shown:
            raise SystemExit("NOT done: no stills to look at yet. Press Pull the stills")
        if op == "cast-reject":          # what he saw: the stills at `seen` (every time to look at is in `frames`)
            if why == "relook":
                old = p.get("rejected")
                p.setdefault("rejected_before", []).append(old if isinstance(old, dict) else {"was": old})
            p["rejected"] = {"by": who, "at": B.utcnow(), "name": name, "title": title, "frames": frames,
                             "seen": shown, "said": "not this person (Clip Review, Casts)"}
            return
        if not shown:
            raise SystemExit("NOT done: no stills to look at yet. The 15:00 prep writes the times")
        pv = p.get("provisional") if isinstance(p.get("provisional"), dict) else {}
        r = p.get("rejected") if isinstance(p.get("rejected"), dict) else {}
        again = (_day(r.get("at")) or "an earlier day") if why == "relook" else ""
        line = verified_line(who, stamp, shown, name, title, _base(p), str(pv.get("named_as") or ""), again)
        if fname != "cast.json":         # a proposal: the person goes into cast.json, the file the engine reads
            q = _find(datas["cast.json"], pid)
            if q is None:
                new = json.loads(json.dumps(p))
                new.pop("verified_before", None)
                _lift(new, who, "confirmed after a new look")
                _set_verified(new, line)
                new["added_from"] = fname
                datas["cast.json"].setdefault("people", []).append(new)
            elif _norm(q.get("name")) == _norm(name) and _norm(q.get("title")) == _norm(title) and needs_look(q):
                _lift(q, who, "confirmed after a new look")
                _set_verified(q, line)
            else:
                raise SystemExit(f"NOT done: cast.json already has {pid} as {q.get('name')!r}: the bot must make that "
                                 "one entry first")
        _lift(p, who, "confirmed after a new look")
        _set_verified(p, line)
        said["line"] = line

    files = ["cast.json"] + ([fname] if fname != "cast.json" else [])
    S.edit_cast(job, files, change)
    name = said.get("name", pid)
    log(op[5:], by=who, job=job.name, file=fname, person=pid, name=name, line=said.get("line", ""))
    if op == "cast-reject":
        if not _engine_knows(cfg):
            return True, (f"Noted: not {name}. A clip already cut with them cannot be signed. The engine update is "
                          f"not in yet, so tonight's run may still cut one: Decline it.")
        return True, (f"Noted: not {name}. Nothing that plates them is cut or signed until the bot fixes the entry "
                      f"and you confirm it here.")
    if op == "cast-undo":
        return True, f"Undone: {name} waits for your check again."
    left = [p["name"] for p in (read_job(job) or {}).get("people", [])]
    if left:
        return True, f"{name} confirmed. {len(left)} more on this source wait for you: {', '.join(left)}."
    return True, f"{name} confirmed. Nothing else on this source waits for you. " + _verdict(cfg, job)


def _said_of(p: dict) -> List[dict]:
    """Every Not them he said to this entry and did not take back: the one that stands, the ones a new look replaced,
    and the ones a later "That's <name>" lifted (never one he undid)."""
    out = [p["rejected"]] if isinstance(p.get("rejected"), dict) else []
    out += [r for r in p.get("rejected_before") or [] if isinstance(r, dict)]
    out += [r for r in p.get("rejected_undone") or [] if isinstance(r, dict) and r.get("how") != "undo"]
    return out


def holds(cc: dict, cfg: Optional[C.Config] = None) -> List[dict]:
    """Why a cut clip's CAST TO CONFIRM list cannot be signed now: [{"name", "why", "words"}], one per person held.

    why: "rejected" (he said Not them to the entry as it is), "relook" (he said Not them and it changed since: it
    waits for his new look in Casts), "renamed" (the plate carries a name he said Not them to; the cast has renamed
    the entry since, but this clip keeps the old name: only Decline or Make again clears it), "gone" (the plated
    person is no longer in cast.json, so nobody can check the name on the plate), "log" (the tap log says he said
    Not them and nothing took it back, though the cast file no longer says so)."""
    if not cc or not cc.get("people"):
        return []
    path = Path(str(cc.get("cast_file") or "")).expanduser()
    data = _read(path) if S.cast_place_ok(path) and path.is_file() else None
    by = ({str(p.get("id") or "").strip().lower(): p for p in data.get("people") or [] if isinstance(p, dict)}
          if isinstance(data, dict) else None)
    log_ = standing(str(cc.get("job") or path.parent.name))
    out = []
    for c in cc.get("people") or []:
        cid = str(c.get("id") or "").strip().lower()
        shown = str(c.get("name") or c.get("id") or "?")
        plate = (_norm(c.get("name")), _norm(c.get("title")))
        p = by.get(cid) if by is not None else None
        lg = log_.get(cid) or next((r for r in log_.values() if plate[0] and _norm(r.get("name")) == plate[0]), None)
        now_ = ((_norm(p.get("name")), _norm(p.get("title")) if c.get("title") else plate[1])
                if p is not None else plate)
        named = [r for r in (_said_of(p) if p is not None else [])
                 if _norm(r.get("name")) == plate[0] and (not c.get("title") or _norm(r.get("title")) == plate[1])]
        if by is not None and p is None:
            out.append({"name": shown, "why": "gone", "words": f"{shown} is no longer in the job's cast.json, so "
                                                              f"nobody can check the name on its plate"})
        elif p is not None and rejected_now(p):
            r = p.get("rejected") if isinstance(p.get("rejected"), dict) else {}
            what, day = rejected_changed(p), _day(r.get("at"), cfg)
            if what:
                out.append({"name": shown, "why": "relook",
                            "words": f"you said Not them to {r.get('name') or shown}{' on ' + day if day else ''}; "
                                     f"the bot changed {what} since, so it waits for your new look in Casts"})
            else:
                out.append({"name": shown, "why": "rejected",
                            "words": f"you said {shown} is not who the plate names"
                                     f"{' (' + day + ', Clip Review, Casts)' if day else ' (Clip Review, Casts)'}"})
        elif p is not None and plate[0] and plate != now_ and named:
            out.append({"name": shown, "why": "renamed",
                        "words": f"you said Not them to the plate this clip carries ({shown}); the cast entry has "
                                 f"changed since, but a cut clip keeps its plate"})
        elif lg:
            day = _day(lg.get("at"), cfg)
            out.append({"name": shown, "why": "log",
                        "words": f"you said {lg.get('name') or shown} is not who the plate names"
                                 f"{' on ' + day if day else ''}, and the cast file no longer says so"})
    return out


def hold_note(cc: dict, cfg: Optional[C.Config] = None) -> str:
    """holds() in one sentence for the clip card and Approve ('' when nothing holds the clip)."""
    hs = holds(cc, cfg)
    if not hs:
        return ""
    fix = ("confirm them in Casts at the top of the page, or Decline it" if all(h["why"] == "relook" for h in hs)
           else "Decline it, or Make again")
    return "; ".join(h["words"] for h in hs) + f". It cannot be signed: {fix}."


def cast_now(cc: dict, cfg: Optional[C.Config] = None) -> Tuple[List[str], List[str]]:
    """A cut clip's CAST TO CONFIRM list against its job's cast file now: (held, checked). held: holds()' names.
    checked: the people a person has checked since it was cut (in Casts, or by an Approve)."""
    hs = holds(cc, cfg)
    held = [h["name"] for h in hs]
    if not cc or not cc.get("people"):
        return held, []
    path = Path(str(cc.get("cast_file") or "")).expanduser()
    data = _read(path) if S.cast_place_ok(path) and path.is_file() else None
    by = ({str(p.get("id") or "").strip().lower(): p for p in data.get("people") or [] if isinstance(p, dict)}
          if isinstance(data, dict) else {})
    checked = []
    for c in cc.get("people") or []:
        p = by.get(str(c.get("id") or "").strip().lower())
        shown = str(c.get("name") or c.get("id") or "?")
        if p is not None and shown not in held and needs_look(p) == "":
            checked.append(shown)
    return held, checked


def rejected_names(cc: dict) -> List[str]:
    """The people on a cut clip's CAST TO CONFIRM list held by a Not them (holds())."""
    return cast_now(cc)[0]


def since(cfg: C.Config, s: Optional[str]) -> datetime:
    """today (default), yesterday, YYYY-MM-DD (its midnight), HH:MM (today), or an ISO time; local time."""
    s = (s or "today").strip()
    tz = cfg.tz()
    today = cfg.now().date()
    if s == "today":
        return datetime.combine(today, dtime(0), tz)
    if s == "yesterday":
        return datetime.combine(today - timedelta(days=1), dtime(0), tz)
    if re.match(r"^\d{1,2}:\d{2}$", s):
        h, m = (int(x) for x in s.split(":"))
        return datetime.combine(today, dtime(h, m), tz)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return datetime.combine(date.fromisoformat(s), dtime(0), tz)
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=tz)


def confirmed(cfg: C.Config, after: datetime) -> List[dict]:
    """The checks written since `after` (his taps here, and Approve at a sign-off), oldest first, each once."""
    p = C.STATE / "castqueue" / "log.jsonl"
    out, seen = [], set()
    try:
        rows = p.read_text().splitlines()
    except OSError:
        return []
    for ln in rows:
        try:
            r = json.loads(ln)
            at = datetime.fromisoformat(r["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if r.get("op") not in ("confirm", "approve-confirm") or at < after:
            continue
        k = (r.get("job"), r.get("person"))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def confirmed_lines(cfg: C.Config, s: Optional[str], as_json: bool = False) -> List[str]:
    after = since(cfg, s)
    rows = confirmed(cfg, after)
    if as_json:
        return [json.dumps(rows, indent=1)]
    if not rows:
        return [f"  no cast confirmed since {after:%Y-%m-%d %H:%M}"]
    return [f"  {datetime.fromisoformat(r['at']).astimezone(cfg.tz()):%m-%d %H:%M}  {r.get('job')}  "
            f"{r.get('name') or r.get('person')}  ({'Casts' if r['op'] == 'confirm' else 'Approve at a sign-off'})"
            for r in rows]


def lines(cfg: C.Config, as_json: bool = False) -> List[str]:
    """`factory.py casts`: who waits, per source."""
    js = pending(cfg)
    if as_json:
        return [json.dumps(js, indent=1, default=str)]
    n, clips = counts(js)
    out = [f"Casts waiting for a person (Clip Review, at the top): {n} in {sum(1 for j in js if j['people'])} sources"
           + (f", {clips} clips plate them" if clips else "")]
    for j in js:
        out.append(f"  {j['job']}" + (f"  ({j['channel']})" if j["channel"] else ""))
        for p in j["people"]:
            have = sum(1 for s in p["stills"] if s["have"])
            what = {"bot": "a bot marked it checked", "provisional": "provisional (named in the title)",
                    "empty": "not checked",
                    "relook": f"you said not them; {p['changed']} changed since: it waits for your new look"}[p["why"]]
            out.append(f"    {p['id']:<12} {p['name']}, {p['title']}: {what}; "
                       f"times {' '.join(_clock(x) for x in p['frames']) or 'none'}; stills {have}/{len(p['frames'])}"
                       + (f"; plates it: {', '.join(u['slug'] for u in p['unlocks'])}" if p["unlocks"] else "")
                       + (f"; a proposal in {p['file']}" if p["proposal"] else "")
                       + ("; the source video is not on this Mac (the 15:00 prep downloads it again)"
                          if not p["master"] and have < len(p["frames"]) else ""))
        for p in j["rejected"]:
            out.append(f"    {p['id']:<12} {p['name']}: you said not them; the bot fixes the entry")
        for p in j["stuck"]:
            out.append(f"    {p['id']:<12} {p['name']}: not on the page: no stills, the source video is not on this "
                       f"Mac" + (f" and cannot be downloaded again ({p['gone'][:80]})" if p["gone"] else
                                 " and the job records no link to it")
                       + ("; Approve at the sign-off is the check of a clip naming them" if p["why"] == "bot" else
                          "; the bot finds the source again (findsource.py) or leaves its plates out"))
        for r in j["lost"]:
            out.append(f"    {r['person']:<12} {r.get('name') or r['person']}: you said not them "
                       f"({str(r.get('at') or '')[:16]}), and the cast file no longer says so (the bot must never "
                       f"take it out): its cut clips stay held")
        if j["nameless"]:
            out.append(f"    no name yet (the bot fills it or takes it out): {', '.join(j['nameless'])}")
        if j["unplated"] and j["people"]:
            out.append(f"    on no plate, not asked (an asker is `questions`, not cast): {', '.join(j['unplated'])}")
    out.append("  (no command confirms a cast: only a person, on the review page)")
    return out


def morning_line(cfg: C.Config) -> str:
    n, clips = counts(pending(cfg))
    if not n:
        return ""
    return (f"Casts: {n} {'person waits' if n == 1 else 'people wait'} for your check"
            + (f" ({clips} clip{'s' if clips != 1 else ''} name them)" if clips else "")
            + ". They are at the top of Clip Review.")


def notify(cfg: C.Config) -> str:
    """A Mac notice when anything waits (Clip Review.app shows it; a click opens the page). '' when nothing waits.
    Only from the live state folder and online: a test or a copy never shows one."""
    line = morning_line(cfg)
    if not line:
        return ""
    line = "Casts to check (not drafts): " + line[len("Casts: "):]
    live = (C.ROOT / "state").resolve()
    if os.environ.get("CLIP_FACTORY_OFFLINE") or C.STATE.resolve() != live:
        return line + " (not shown: a test or a copy)"
    app = Path(os.path.expanduser("~/Applications/Clip Review.app"))
    if app.exists():
        (C.STATE / "notify-pending.txt").write_text(line[:220])
        subprocess.run(["open", "-g", str(app)], capture_output=True, timeout=30)
    else:
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(line[:220])} with title "
                        f"{json.dumps('Casts to check')}"], capture_output=True, timeout=30)
    return line


# ------------------------------------------------------------------------------------------- the review page
def _esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _wrap(tag: str, cls: str, inner: str, attrs: str = "") -> str:
    """review._wrap, the same: data-h lets the page swap only what changed."""
    h = hashlib.sha1(f"{cls}|{attrs}|{inner}".encode()).hexdigest()[:12]
    return f'<{tag} class="{cls}" data-h="{h}"{attrs}>{inner}</{tag}>'


def _file_url(p: str) -> str:
    """review._file_url, the same."""
    try:
        st = os.stat(p)
        v = f"{st.st_size:x}{st.st_mtime_ns:x}"
    except OSError:
        v = "0"
    return f"/file?p={urllib.parse.quote(p)}&v={v}"


def _local(cfg: C.Config, iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(cfg.tz()).strftime("%b %d, %H:%M")
    except (TypeError, ValueError):
        return str(iso or "")


def _item(cfg: C.Config, p: dict) -> dict:
    cid = "cq-" + hashlib.sha1(f"{p['job']}|{p['file']}|{p['id']}".encode()).hexdigest()[:12]
    attrs = (f' id="{cid}" data-key="{_esc(p["key"])}" data-job="{_esc(p["job"])}" data-file="{_esc(p["file"])}" '
             f'data-person="{_esc(p["id"])}"')
    if p["why"] == "rejected":
        r = p.get("rejected") or {}
        try:
            at = ", ".join(_clock(x) for x in (r.get("seen") or r.get("frames") or []))
        except (TypeError, ValueError):
            at = ""
        top = _wrap("div", "top", f'<span class="cast-name">{_esc(p["name"] or p["id"])}</span>'
                                  f'<span class="tag t-mute">You said not them</span>')
        bottom = _wrap("div", "bottom",
                       f'<div class="note">You said the stills{" at " + _esc(at) if at else ""} are not '
                       f'{_esc(p["name"])} ({_esc(_local(cfg, r.get("at", "")))}). Nothing that plates them is cut or '
                       f'signed. The bot fixes the name or the times; then it comes back here.</div>'
                       f'<div class="btns"><button data-op="cast-undo">Undo</button></div>')
        html_ = f'<article class="card cast said"{attrs}>{top}{bottom}</article>'
        return {"id": cid, "key": p["key"], "top": top, "bottom": bottom, "html": html_}
    tag = {"bot": ("t-bad", "Marked checked by a bot, not by you"),
           "provisional": ("t-ready", f'Named in the title as "{p["named_as"]}"'),
           "empty": ("t-warn", "Not checked yet"),
           "relook": ("t-bad", "You said Not them before: look again")}[p["why"]]
    top = _wrap("div", "top", f'<span class="cast-name">{_esc(p["name"])}</span>'
                              f'<span class="tag {tag[0]}">{_esc(tag[1])}</span>'
                              f'<span class="cast-title">{_esc(p["title"])}</span>')
    out = []
    if p["why"] == "relook":
        r = p.get("rejected") or {}
        day = _day(r.get("at"), cfg)
        out.append(f'<div class="alert">You said Not them to {_esc(r.get("name") or p["name"])}'
                   f'{" on " + _esc(day) if day else ""}; the bot changed {_esc(p["changed"])} since. Is this '
                   f'{_esc(p["name"])}? Nothing that plates them is cut or signed until you confirm it here.</div>')
    have = [s for s in p["stills"] if s["have"]]
    if have:
        figs = "".join(f'<figure><a href="{_esc(_file_url(s["path"]))}" target="_blank" rel="noopener">'
                       f'<img src="{_esc(_file_url(s["path"]))}" alt="{_esc(p["name"])} at {_clock(s["sec"])}" '
                       f'loading="lazy"></a><figcaption>{_clock(s["sec"])}</figcaption></figure>' for s in have)
        out.append(f'<div class="stills">{figs}</div>')
    elif not p["frames"]:
        out.append('<div class="alert">No times to look at yet: the 15:00 prep writes them.</div>')
    elif p["master"]:
        out.append(f'<div class="ask">No stills yet ({len(p["frames"])} to pull from the source video).</div>')
    else:
        out.append('<div class="alert">No stills yet: the source video is not on this Mac any more. The 15:00 prep '
                   'downloads it again and pulls them.</div>')
    src = " · ".join(x for x in (p["channel"], f'"{p["source_title"]}"' if p["source_title"] else "") if x)
    watch = "".join(f' <a href="{_esc(_at(p["url"], s))}" target="_blank" rel="noopener">Watch at {_clock(s)}</a>'
                    for s in p["frames"][:2] if _at(p["url"], s))
    out.append(f'<div class="meta">{_esc(src or p["job"])}{watch}</div>')
    if p["note"]:
        out.append(f'<div class="note">The bot\'s note: {_esc(p["note"][:300])}</div>')
    if p["why"] == "bot":
        out.append(f'<div class="note">What the bot wrote: {_esc(p["bot_words"][:240])}</div>')
    if p["unlocks"]:
        out.append(f'<div class="note">On the plate in: {_esc(", ".join(u["slug"] for u in p["unlocks"]))}</div>')
    else:
        out.append('<div class="note">On no plate yet.</div>')
    if p["proposal"]:
        out.append(f'<div class="note">New in this cast ({_esc(p["file"])}): your check adds them to it.</div>')
    btns = []
    if have:
        btns.append(f'<button class="go" data-op="cast-confirm">That\'s {_esc(p["name"])}</button>')
    elif p["frames"] and p["master"]:
        btns.append('<button class="go" data-op="cast-stills">Pull the stills</button>')
    if have or not p["frames"]:        # Not them only over what he can see (or an entry with no times at all)
        ask = (f"Not {p['name']}? Nothing that plates them is cut or signed until the bot fixes the name or the "
               f"times and you confirm it here.")
        btns.append(f'<button data-op="cast-reject" data-ask="{_esc(ask)}">Not them</button>')
    if btns:
        out.append(f'<div class="btns">{"".join(btns)}</div>')
    bottom = _wrap("div", "bottom", "".join(out))
    html_ = f'<article class="card cast"{attrs}>{top}{bottom}</article>'
    return {"id": cid, "key": p["key"], "top": top, "bottom": bottom, "html": html_}


def section(cfg: C.Config, now: Optional[datetime] = None, acct: str = "all") -> dict:
    """The Casts section: {"n": people waiting, "head", "items"}. Empty (and hidden) when nothing waits, or when the
    page shows an account that is not the Politics. The page never goes down over it."""
    led = [a.slug for a in cfg.accounts.values() if a.maker == "politics"]
    if not led or (acct not in ("", "all") and acct not in led):
        return {"n": 0, "head": _wrap("div", "casts-head", ""), "items": []}
    try:
        js = pending(cfg)
    except Exception as e:  # noqa: BLE001
        head = _wrap("div", "casts-head", f'<h2>Casts to check</h2><span class="alert">The cast queue could not be '
                                          f'read ({_esc(type(e).__name__)}: {_esc(str(e)[:160])}).</span>')
        return {"n": 0, "head": head, "items": [], "error": str(e)}
    items = [_item(cfg, p) for j in js for p in j["people"]] + [_item(cfg, p) for j in js for p in j["rejected"]]
    n, clips = counts(js)
    if n:
        head = (f'<h2>Casts to check <span class="n">{n}</span></h2><span class="calm">Is this who the plate names? '
                f'Your tap is the cast check'
                + (f"; {clips} clip{'s' if clips != 1 else ''} name them" if clips else "") + '.</span>')
    elif items:
        head = '<h2>Casts</h2><span class="calm">Nothing waits for you. The bot is fixing what you said was not them.</span>'
    else:
        head = ""
    return {"n": n, "head": _wrap("div", "casts-head", head), "items": items}


def section_html(sec: dict) -> str:
    """The section as the page first draws it (hidden when it holds nothing; /live fills it in later)."""
    hidden = "" if sec["items"] else " hidden"
    return (f'<section class="casts" id="casts" aria-label="Casts to check"{hidden}>{sec["head"]}'
            f'<div class="grid" id="casts-grid">{"".join(i["html"] for i in sec["items"])}</div></section>')


CSS = """
.casts{margin-bottom:26px}
.casts-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-height:34px;margin-bottom:12px}
.casts-head h2{font-size:18px}.casts-head .n{color:var(--warn);font-variant-numeric:tabular-nums}
.casts-head .calm{color:var(--sub);font-size:13.5px}
#casts-grid{grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:20px}
.cast{border-color:var(--warn)}.cast.said{border-color:var(--line)}
.cast .top{flex-wrap:wrap;gap:6px 8px}
.cast-name{font-weight:650;font-size:15px}
.cast-title{flex-basis:100%;color:var(--sub);font-size:12px;letter-spacing:.02em}
.stills{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
.stills figure{margin:0}
.stills img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;background:#000;border-radius:4px}
.stills figcaption{font-size:11.5px;color:var(--sub);font-variant-numeric:tabular-nums;margin-top:2px}
.cast .meta a{color:var(--blue);text-decoration:none;margin-left:8px;white-space:nowrap}
.cast .meta a::after{content:" ↗";font-size:11px}
@media (max-width:600px){#casts-grid{grid-template-columns:1fr}}
"""
