"""The generic maker: a client account's clips, cut by viral-clip-engine in its NEUTRAL look.

    factory.py account job <slug> <url|file> --name "Job"   a source in: the engine transcribes it (analyze.py)
    ... a person or a Claude task reads Jobs/<Job>/transcript_compact.txt and writes Jobs/<Job>/slate.json ...
    factory.py make <slug> [--date D] [--slots ..] [--dry-run]   each open slot gets the next slated clip
    factory.py nightly <slug>                                    the same, in the background (one worker per account)

Picking the moments is judgment, so the factory never does it: a slot is filled only from a clip somebody slated.
What the factory does is everything after that, with the engine's own checks and none of them loosened:
  1. the job must be in the neutral look: no Politics preset, look, masthead or brand kit, delivered inside
     the client's own folder, the client's own name on the picture (`handle`), rights recorded, and the slate entry
     names no "template" (those are the Politics's segments). Anything else is refused before a frame is drawn (the
     Politics engine stamps its look on any job delivered under Clipping/);
  2. preflight.py <slug>: a FAIL refuses it; build_all.py <slug> renders it; its length must sit in the account's
     band; grade.py <slug>: a BAD refuses it. The engine reads the client's own Publish/schedule.json and Jobs/
     (CLIP_SCHEDULE, CLIP_JOBS), never the Politics's. A clip goes on that schedule only AFTER it passed: the engine's
     day rules are built on the Politics's segment templates, and a scheduled clip with no "template" is refused, so a
     client clip is checked unscheduled (preflight says "not in schedule.json") and the schedule is the record;
  3. a refused clip is skipped (the next slated one is tried, up to RETRIES more) and is not offered again until its
     slate entry changes. A clip that FAILED for another reason (a timeout, a full disk, the render queue's deadline)
     is offered again on a later run, up to FAIL_CAP times. A slot whose picks were all refused is left failed, so
     the next make fills it from new picks;
  4. packed into the account's own Delivered/<MM.DD.YYYY>/<HHMM slug>/ with one words file per platform from the
     slate (titles, platform_caption, hashtags), marked used, written on the schedule, then collected into this slot
     as an unsigned, ready clip.
One make at a time per account: state/<account>/.render.lock (makers.render_lock, as the GTA maker holds its own) is
taken FIRST, and only then are the board and the picks read, so a slot changed while this make waited (a drop, a
sign-off, another make) is left alone. The Mac is shared step by step by the render queue (until 2026-10-19 that lock
was one file for the whole Mac, and a client's make waited out the GTA night).
A slot is set to making only while it can still be filled. Each clip's heavy steps (its intake, and preflight, render
and grade) also wait their turn in the render queue (renderlock.py) when it is installed.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import collect as K
from . import config as C

RETRIES = 2                 # more slated clips tried for a slot after one is refused
FAIL_CAP = 3                # a slated clip that FAILED (not refused) this many times is set aside like a refused one
USED = "generic-used.json"  # state/<slug>/: every slated clip the maker has used, and what came of it
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
DROP_KEYS = ("_preset", "masthead", "look_accent", "ground", "atlas", "atlas_resolve", "brand")
WORDS = {   # platform -> its paste file, and which slate fields it is written from
    "youtube_shorts": "youtube.txt",
    "tiktok": "tiktok.txt",
    "facebook_reels": "facebook.txt",
    "instagram_reels": "reels.txt",
}


def engine(acct: C.Account) -> Path:
    return acct.skill_dir / "scripts"


def py() -> str:
    from . import makers as M
    return os.environ.get("CLIP_ENGINE_PYTHON") or M.PY_SKILLS


def jobs_dir(acct: C.Account) -> Path:
    return acct.account_dir / "Jobs"


def work_root(acct: C.Account) -> Path:
    return C.STATE / acct.slug / "work"


def key_of(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "job"


def _json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def _real(p: Path) -> Path:
    try:
        return Path(os.path.expanduser(str(p))).resolve()
    except OSError:
        return Path(os.path.expanduser(str(p)))


def _inside(p: Path, top: Path) -> bool:
    p, top = _real(p), _real(top)
    return p == top or top in p.parents


def mark_of(acct: C.Account) -> str:
    """The client's own name on the picture: its @handle, else its name."""
    return acct.handle or acct.name


def goal_of(acct: C.Account) -> str:
    """The engine's goal for the account's band: under 65 seconds the TikTok payout gate cannot apply."""
    return "reach" if acct.max_seconds < 65 else "both"


# ------------------------------------------------------------------------------------------------ the look
def look_problems(cfg: C.Config, acct: C.Account, proj: dict) -> List[str]:
    """Why this job would NOT render in the client's neutral look ([] = it would)."""
    from . import accounts as AC
    out: List[str] = []
    if not proj:
        return ["the job has no project.json (run factory.py account job first)"]
    if str(proj.get("_preset") or "") == "hearing":
        out.append("it was made with the Politics preset (--preset hearing)")
    if str(proj.get("look") or "classic").strip().lower() != "classic":
        out.append(f"its look is {proj.get('look')!r}, not the neutral classic look")
    if proj.get("masthead"):
        out.append(f"it carries a masthead ({proj.get('masthead')!r})")
    kit = str((acct.raw.get("client") or {}).get("brand_kit") or "")
    if proj.get("brand") and str(proj.get("brand")) != kit:
        out.append(f"it names the brand kit {proj.get('brand')!r}, not the client's")
    od = str(proj.get("out_dir") or "").strip()
    if not od:
        out.append("it has no out_dir")
    else:
        for home in AC.POLITICS_HOMES:
            if _inside(Path(od), home):
                out.append(f"it delivers under the Politics's folder ({od}): the engine would stamp the Politics look on it")
        if not _inside(Path(od), acct.account_dir):
            out.append(f"it delivers outside {C.possessive(acct.name)} folder ({od})")
        for other in cfg.accounts.values():
            if other.slug != acct.slug and _inside(Path(od), other.account_dir):
                out.append(f"it delivers into {C.possessive(other.name)} folder ({od})")
    if str(proj.get("handle") or "") != mark_of(acct):
        out.append(f"the name on the picture is {proj.get('handle')!r}, not {mark_of(acct)!r}")
    if str(proj.get("rights") or "unverified").lower() == "unverified":
        out.append("its rights are not recorded")
    return sorted(set(out))


def neutralise(acct: C.Account, proj: dict, job: Path, source_url: str = "") -> dict:
    """The job's project.json in the client's look: the classic look, the client's name, its band, its folder."""
    out = {k: v for k, v in proj.items() if k not in DROP_KEYS}
    rights = str((acct.raw.get("client") or {}).get("rights") or "owned")
    out.update(look="classic", handle=mark_of(acct), out_dir=str(job), min_clip=acct.min_seconds,
               max_clip=acct.max_seconds, rights=rights, endcard=False)
    if source_url:
        out["source_url"] = source_url
    if not str(out.get("source_channel") or "").strip():
        out["source_channel"] = acct.name           # the client's own footage: the client published it
    kit = str((acct.raw.get("client") or {}).get("brand_kit") or "")
    if kit:
        out["brand"] = kit
    return out


# ------------------------------------------------------------------------------------------------ a source in
def new_job(cfg: C.Config, acct: C.Account, source: Optional[str], name: str, source_url: str = "", fmt: str = "",
            dry_run: bool = False) -> List[str]:
    """`factory.py account job`: transcribe a source with the engine into a job of the client's own, in its look."""
    from . import accounts as AC
    from . import makers as M
    if not AC.is_client(acct) or acct.maker != "generic":
        raise SystemExit(f"{acct.name} is not made by the generic maker: account job is for client accounts")
    if not source:
        raise SystemExit("account job needs the source: a URL or a video file")
    is_url = source.startswith(("http://", "https://"))
    if not is_url:
        src = Path(os.path.expanduser(source))
        if not src.is_file():
            raise SystemExit(f"no file {src}")
        if not source_url.startswith("https://"):
            raise SystemExit("a file source needs --source-url https://... (its public link): the engine refuses a "
                             "clip it cannot link back to")
        source = str(src)
    name = " ".join((name or "").split()) or (Path(source).stem if not is_url else "")
    if not name or any(ch in name for ch in '/\\:{}"'):
        raise SystemExit("--name: the job's name (no / \\ : { } or quotes)")
    job = jobs_dir(acct) / name
    work = work_root(acct) / key_of(name)
    if (job / "job.json").exists():
        raise SystemExit(f"there is already a job {name!r} ({job})")
    rights = str((acct.raw.get("client") or {}).get("rights") or "owned")
    cmd = [py(), "analyze.py", source, "--out", str(job), "--goal", goal_of(acct), "--rights", rights]
    if fmt:
        cmd += ["--format", fmt]
    plan = [f"{acct.name}: job {name!r}", f"  work folder {work} (the engine's CLIP_WORK)", f"  clips land in {job}",
            f"  {' '.join(cmd[1:])}"]
    if dry_run:
        return plan + ["  (dry run: nothing run)"]
    job.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    log = M.log_path(acct, cfg.now().date(), f"job-{key_of(name)}")
    env = dict(os.environ, CLIP_WORK=str(work))
    try:
        with (_turn(acct, f"intake: {name}", log) if _queue() is not None else _render_lock(acct)):
            r = M.run_logged(cmd, log, cwd=engine(acct), env=env, timeout=4 * 3600)
    except Exception as e:  # noqa: BLE001
        if _busy(e):
            raise SystemExit(f"{name}: not read, {e}") from None
        raise
    if r.returncode != 0 or not (work / "project.json").is_file():
        raise SystemExit(f"the engine could not read the source (exit {r.returncode}): {M.tail(log, 4)}")
    proj = neutralise(acct, _json(work / "project.json"), job, source_url)
    _write_json(work / "project.json", proj)
    for f in ("transcript_compact.txt", "glossary.json"):
        if (work / f).is_file():
            shutil.copy2(work / f, job / f)
    _write_json(job / "job.json", {"work": str(work), "source": source, "source_url": proj.get("source_url", ""),
                                   "made": B.utcnow(), "by": "factory.py account job"})
    bad = look_problems(cfg, acct, proj)
    return plan + [f"  done: read {job / 'transcript_compact.txt'} and write {job / 'slate.json'} (RUNBOOK.md)"] + \
        ([f"  NOT in the client's look: {'; '.join(bad)}"] if bad else [])


# ------------------------------------------------------------------------------------------------ the picks
def _used(acct: C.Account) -> dict:
    return _json(C.STATE / acct.slug / USED)


def _mark(acct: C.Account, key: str, rec: dict) -> None:
    d = _used(acct)
    if rec.get("result") == "failed":           # counted: a clip that keeps failing is set aside at FAIL_CAP
        rec = dict(rec, fails=int((d.get(key) or {}).get("fails") or 0) + 1)
    d[key] = dict(rec, at=B.utcnow())
    _write_json(C.STATE / acct.slug / USED, d)


def set_aside(used: dict, job: Path, c: dict) -> bool:
    """Not offered again: delivered, refused as it is slated, or failed FAIL_CAP times as it is slated."""
    base = f"{job.name}|{c.get('slug')}"
    if (used.get(base) or {}).get("result") == "delivered":
        return True
    rec = used.get(f"{base}|{entry_hash(c)}") or {}
    return rec.get("result") == "refused" or int(rec.get("fails") or 0) >= FAIL_CAP


def entry_hash(c: dict) -> str:
    return hashlib.sha256(json.dumps(c, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def candidates(acct: C.Account) -> Tuple[List[Tuple[Path, dict]], List[str]]:
    """(every slated clip not used yet, oldest job first and in slate rank; what could not be read)."""
    used = _used(acct)
    out: List[Tuple[Path, dict]] = []
    said: List[str] = []
    root = jobs_dir(acct)
    jobs = [j for j in root.iterdir() if j.is_dir() and (j / "job.json").is_file()] if root.is_dir() else []
    jobs.sort(key=lambda j: (str(_json(j / "job.json").get("made") or ""), j.name))
    for job in jobs:
        if not (job / "slate.json").is_file():
            continue
        od = str(_json(Path(str(_json(job / "job.json").get("work") or "")) / "project.json").get("out_dir") or "")
        if od and _real(Path(od)) != _real(job):    # an iCloud "Ep 1 2" copy, or a renamed folder: never cut twice
            said.append(f"{job.name}: skipped, its work belongs to the job folder {Path(od).name!r} (a copy, or a "
                        f"renamed folder: put the name back)")
            continue
        slate = _json(job / "slate.json")
        clips = slate.get("clips") if isinstance(slate.get("clips"), list) else None
        if clips is None:
            said.append(f"{job.name}/slate.json will not parse (or has no \"clips\")")
            continue
        for c in sorted((x for x in clips if isinstance(x, dict)), key=lambda x: str(x.get("rank", "")).zfill(4)):
            slug = str(c.get("slug") or "")
            if not SLUG_RE.match(slug) or not isinstance(c.get("rank"), int):
                said.append(f"{job.name}: a clip with no rank or slug ({slug or '?'}) is skipped")
                continue
            if set_aside(used, job, c):
                continue
            out.append((job, c))
    return out, said


def words_for(acct: C.Account, c: dict) -> Dict[str, str]:
    """One paste file per platform from the slate entry (the shapes copytext.parse reads). A platform with no words
    is left out, and the clip is refused for it before anything renders."""
    titles = c.get("titles") if isinstance(c.get("titles"), dict) else {}
    cap = str(c.get("platform_caption") or "").strip()
    tags = str(c.get("hashtags") or "").strip()
    hook = str(c.get("hook") or "").strip()
    yt = str(titles.get("youtube") or hook).strip()
    tt = str(titles.get("tiktok") or cap).strip()
    out: Dict[str, str] = {}
    for plat in acct.platforms:
        if plat == "youtube_shorts" and yt and cap:
            out[WORDS[plat]] = f"TITLE\n{yt[:100]}\n\nDESCRIPTION\n{cap}\n" + (f"\nTAGS\n{tags}\n" if tags else "")
        elif plat == "tiktok" and tt:
            out[WORDS[plat]] = f"CAPTION\n{tt}" + (f"\n\n{tags}" if tags else "") + "\n"
        elif plat == "facebook_reels" and cap:
            out[WORDS[plat]] = f"CAPTION\n{cap}\n"
        elif plat == "instagram_reels" and cap:
            out[WORDS[plat]] = f"CAPTION\n{cap}" + (f"\n\n{tags}" if tags else "") + "\n"
    return out


# ------------------------------------------------------------------------------------------------ one clip
class Refused(Exception):
    """A check said no (the result is 'refused'); anything else that stops a clip is 'failed'."""


def _render_lock(acct: C.Account):
    """state/<account>/.render.lock (makers.render_lock), as the GTA maker takes its own: one make of this account at
    a time."""
    from . import makers as M

    @contextlib.contextmanager
    def held():
        lock = M.render_lock(acct)
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            yield
    return held()


def _queue():
    """The render queue (clipfactory/renderlock.py), or None where it is not installed."""
    try:
        from . import renderlock as RL
    except ImportError:
        return None
    return RL


def _busy(e: BaseException) -> bool:
    """The render queue's deadline passed before this step's turn: not the clip's fault, so never counted on it."""
    RL = _queue()
    return RL is not None and isinstance(e, getattr(RL, "Busy", ()))


class _Stop(Exception):
    """The rest of this make waits for another run (the render queue's deadline)."""


def _turn(acct: C.Account, what: str, log: Path):
    """A heavy step's turn in the render queue; without the queue, nothing (the make's own lock already holds)."""
    RL = _queue()
    return RL.hold(acct.slug, what, log=log) if RL is not None else contextlib.nullcontext()


def _gate(cmd: List[str], acct: C.Account, env: dict, log: Path, what: str, timeout: int) -> str:
    from . import makers as M
    at = M._size(log)
    try:
        r = M.run_logged(cmd, log, cwd=engine(acct), env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{what} ran past {timeout // 60} minutes") from None
    text = M._since(log, at)
    if r.returncode:
        bad = [ln.strip() for ln in text.splitlines() if "[FAIL]" in ln or "[ BAD  ]" in ln or ln.startswith("NOT READY")]
        if what in ("preflight", "grade"):
            raise Refused(f"{what} refused it: " + " | ".join(bad[:6] or text.strip().splitlines()[-3:]))
        raise RuntimeError(f"{what} failed (exit {r.returncode}): " + " | ".join(text.strip().splitlines()[-3:]))
    return text


def _schedule(acct: C.Account, day: date, slot: str, job: str, slug: str) -> None:
    """The client's own Publish/schedule.json: the record of which clip went into which slot, written only after the
    clip passed every check (cut_one never runs a clip that is already on it)."""
    p = acct.account_dir / "Publish" / "schedule.json"
    d = _json(p)
    d.setdefault(day.isoformat(), {})[slot] = {"job": job, "slug": slug}
    _write_json(p, d)


def cut_one(cfg: C.Config, acct: C.Account, day: date, slot: str, job: Path, c: dict, log: Path) -> Path:
    """Check, render, grade and pack one slated clip into the slot's delivery folder. Raises Refused or another
    error with the reason."""
    slug = str(c["slug"])
    meta = _json(job / "job.json")
    work = Path(str(meta.get("work") or ""))
    proj = _json(work / "project.json")
    bad = look_problems(cfg, acct, proj)
    if bad:
        raise Refused("not in the client's look: " + "; ".join(bad))
    if " " in str(work):
        raise Refused(f"the work folder has a space in it ({work}): the engine cannot use it")
    if str(c.get("template") or "").strip():
        raise Refused(f"it names the template {c.get('template')!r}: the templates are the Politics's segments, "
                      "never a client's (take \"template\" out of its slate entry)")
    words = words_for(acct, c)
    missing = [p for p in acct.platforms if WORDS.get(p) not in words]
    if missing:
        raise Refused(f"the slate gives no words for {', '.join(missing)} (titles, platform_caption)")
    slate = _json(job / "slate.json")         # the engine's copy: only entries it can read (a rank and a slug)
    slate["clips"] = [x for x in slate.get("clips") or [] if isinstance(x, dict) and isinstance(x.get("rank"), int)
                      and SLUG_RE.match(str(x.get("slug") or ""))]
    _write_json(work / "slate.json", slate)
    # the client's schedule, WITHOUT this clip on it: it goes on only after it passed (fill). The engine refuses a
    # scheduled clip with no Politics segment "template", and a client clip has none
    env = dict(os.environ, CLIP_WORK=str(work), CLIP_SCHEDULE=str(acct.account_dir / "Publish" / "schedule.json"),
               CLIP_JOBS=str(jobs_dir(acct)))
    name = f"{int(c['rank']):02d}-{slug}"
    out = Path(str(proj["out_dir"]))
    mp4 = out / "clips" / f"{name}.mp4"
    with _turn(acct, f"clip {slot}: {slug}", log):
        _gate([py(), "preflight.py", slug], acct, env, log, "preflight", 900)
        _gate([py(), "build_all.py", slug], acct, env, log, "the render", 3600)
        if not mp4.is_file():
            raise RuntimeError(f"the render left no {mp4.name} in {out / 'clips'}")
        secs = K.probe_seconds(mp4)
        if secs is None or not acct.min_seconds - 0.05 <= secs <= acct.max_seconds + 0.05:
            raise Refused(f"it came out {secs}s, outside {acct.min_seconds:.0f}-{acct.max_seconds:.0f}s")
        _gate([py(), "grade.py", slug], acct, env, log, "grade", 900)
    dst = acct.delivered_dirs(day)[0] / f"{slot.replace(':', '')} {slug}"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    shutil.copy2(mp4, dst / mp4.name)
    for extra in (out / "clips" / f"{name}.jpg", out / "captions" / f"{name}.srt"):
        if extra.is_file():
            shutil.copy2(extra, dst / extra.name)
    for fname, text in words.items():
        (dst / fname).write_text(text)
    (dst / "source.json").write_text(json.dumps({"host": str(proj.get("source_channel") or acct.name),
                                                  "rights": str(proj.get("rights") or "")}) + "\n")
    return dst


def _claim(cfg: C.Config, acct: C.Account, day: date, slot: str, why: str) -> str:
    """Set the slot to making, but only while it can still be filled: '' when it was, else the status it has now
    (a drop, a sign-off or another make got there first, and it is left exactly as it is)."""
    with B.edit(cfg, acct, day) as bd:
        s = bd["slots"].get(slot)
        if s is None:
            return "not on the board"
        if s["status"] not in B.FILLABLE:
            return s["status"]
        s["status"] = B.MAKING
        B.note(s, why[:600])
    return ""


def _settle(cfg: C.Config, acct: C.Account, day: date, slot: str, status: str, why: str) -> None:
    """End this make's hold on a slot: only a slot still 'making' (this make's) changes."""
    with B.edit(cfg, acct, day) as bd:
        s = bd["slots"].get(slot)
        if s is not None and s["status"] == B.MAKING:
            s["status"] = status
            B.note(s, why[:600])


def fill(cfg: C.Config, acct: C.Account, day: date, slot: str, cands: List[Tuple[Path, dict]], log: Path) -> str:
    """The next slated clip that passes every check, into this slot (cands loses what it uses). Raises _Stop when the
    render queue's deadline has passed: the rest waits for another run."""
    why: List[str] = []
    refused_all = True
    tried = 0
    while cands and tried < 1 + RETRIES:
        job, c = cands.pop(0)
        if set_aside(_used(acct), job, c):          # used since the list was read
            continue
        tried += 1
        slug = str(c["slug"])
        base = f"{job.name}|{slug}"
        now_is = _claim(cfg, acct, day, slot, f"cutting {slug} from {job.name}")
        if now_is:
            cands.insert(0, (job, c))                # not used: the next slot takes it
            return f"  {slot}: left as it is ({now_is}): it changed while this make waited"
        try:
            dst = cut_one(cfg, acct, day, slot, job, c, log)
        except Refused as e:
            _mark(acct, f"{base}|{entry_hash(c)}", {"result": "refused", "why": str(e)[:600], "day": day.isoformat(),
                                                     "slot": slot})
            why.append(f"{slug}: {e}")
            continue
        except Exception as e:  # noqa: BLE001  (one clip never stops the night)
            if _busy(e):                             # not the clip's fault: nothing counted on it
                cands.insert(0, (job, c))
                _settle(cfg, acct, day, slot, B.FAILED, f"not cut: {e}")
                raise _Stop(f"  {slot}: FAILED, not cut: {e}") from None
            _mark(acct, f"{base}|{entry_hash(c)}", {"result": "failed", "why": str(e)[:600], "day": day.isoformat(),
                                                     "slot": slot})
            why.append(f"{slug}: {e}")
            refused_all = False
            continue
        # passed: marked used FIRST (a clip is never cut twice), then the record, then this slot takes it
        _mark(acct, base, {"result": "delivered", "day": day.isoformat(), "slot": slot, "folder": str(dst)})
        _schedule(acct, day, slot, job.name, slug)
        got = K.collect(cfg, acct, day, only=[slot])
        s = (B.read(acct, day) or {}).get("slots", {}).get(slot) or {}
        if s.get("status") == B.MAKING:
            _settle(cfg, acct, day, slot, B.FAILED, f"cut and packed ({dst.name}), but collect did not take it: "
                    + ("; ".join(x.strip() for x in got) or "nothing")[:400])
            return f"  {slot}: FAILED, {slug} packed in {dst} but not collected"
        if s.get("slug") != slug:
            return f"  {slot}: {s.get('status')} with {s.get('slug')}; {slug} was cut and waits in {dst}"
        return (f"  {slot}: {slug} from {job.name}, checked, cut and collected ({s.get('status')})"
                + (f" (skipped first: {'; '.join(why)})" if why else ""))
    if why:
        # failed, never refused: a failed slot stays fillable, so the next make fills it from new picks
        _settle(cfg, acct, day, slot, B.FAILED, ("every pick was refused: " if refused_all else "") + "; ".join(why))
        return (f"  {slot}: FAILED, {'every pick was refused' if refused_all else 'not made'}: {'; '.join(why)}")
    with B.edit(cfg, acct, day) as bd:
        s = bd["slots"].get(slot)
        if s is not None and s["status"] in B.FILLABLE:
            B.note_once(s, "no clip slated: write slate.json in a job (RUNBOOK.md)")
    return f"  {slot}: {(s or {}).get('status', 'open')}, nothing left slated"


# ------------------------------------------------------------------------------------------------ the maker
def _open_slots(acct: C.Account, day: date, only: Optional[List[str]]) -> List[str]:
    bd = B.read(acct, day) or {"slots": {}}
    return [s["slot"] for s in sorted(bd["slots"].values(), key=lambda s: s["slot"])
            if s["status"] in B.FILLABLE and (not only or s["slot"] in only)]


def make(cfg: C.Config, acct: C.Account, day: date, only: Optional[List[str]] = None,
         dry_run: bool = False) -> List[str]:
    from . import accounts as AC
    from . import makers as M
    if not AC.is_client(acct):
        raise SystemExit(f"{acct.name}: the generic maker makes client accounts only")
    if dry_run:                                      # reads only: no lock, nothing written
        todo = _open_slots(acct, day, only)
        cands, said = candidates(acct)
        lines = [f"  note: {x}" for x in said]
        if not todo:
            return lines + [f"{acct.name} {day}: no open slot"]
        if not cands:
            return lines + [f"{acct.name} {day}: nothing slated in {jobs_dir(acct)}; {len(todo)} slots stay open"]
        for k, (job, c) in zip(todo, cands):
            bad = look_problems(cfg, acct, _json(Path(str(_json(job / "job.json").get("work") or "")) / "project.json"))
            lines.append(f"  {k}: would cut {c['slug']} from {job.name}"
                         + (f" (REFUSED: not in the client's look: {'; '.join(bad)})" if bad else ""))
        for k in todo[len(cands):]:
            lines.append(f"  {k}: nothing slated for it")
        return lines
    log = M.log_path(acct, day, "make")
    # the lock FIRST: another make of this account may hold it, and anything read before the wait would be stale
    with _render_lock(acct):
        with B.edit(cfg, acct, day):                 # the board exists (makers.make makes it too)
            pass
        lines = M.reset_stale_making(cfg, acct, day)
        todo = _open_slots(acct, day, only)
        cands, said = candidates(acct)
        lines += [f"  note: {x}" for x in said]
        if not todo:
            return lines + [f"{acct.name} {day}: no open slot"]
        if not cands:
            with B.edit(cfg, acct, day) as b2:
                for k in todo:
                    B.note_once(b2["slots"][k], "no clip slated: write slate.json in a job (RUNBOOK.md)")
            return lines + [f"{acct.name} {day}: nothing slated in {jobs_dir(acct)}; {len(todo)} slots stay open"]
        for i, k in enumerate(todo):
            try:
                lines.append(fill(cfg, acct, day, k, cands, log))
            except _Stop as e:
                lines.append(str(e))
                if todo[i + 1:]:
                    lines.append(f"  {', '.join(todo[i + 1:])}: left for the next run (the render queue's deadline)")
                break
    return lines
