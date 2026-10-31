"""A platform's own shorter cut of a delivered clip, rendered by the account's skill: the Politics's Instagram takes about
50s (unpublished tests 2026-10-17: 30-50s accepted, 57s refused) and its clips run 65-90, so the nightly run renders
each clip's Instagram cut as well (the band is the outlet's min_seconds/max_seconds).

YouTube (audit call 5, 2026-10-17): a network or C-SPAN clip (rights "rebroadcast") over 60s gets its own YouTube cut of
59s or less, because one Content ID claim blocks a Short over 60s worldwide (at 60s or less it stays up and the claimant
takes the money). `factory.py ytcut` makes it the same way and with the same checks (youtube.mp4 beside the clip; the
band is the outlet's cut_min_seconds/cut_max_seconds), refuses a clip YouTube takes whole (outlets.cut_rule), and with
`--from-instagram` takes the clip's Instagram cut as it is (it passed preflight and grade; no render). `ytcut --missing`
lists the day's clips that still need one. Every cut renders in its work clone's out-ig/ folder, whatever the platform.

    factory.py igcut politics --date 2026-10-18 --slug patel-yes-or-no-reporter \
        --segments 12208-12226,12262-12294.9 [--end 12290] [--hook "WORDS ON THE PICTURE"] [--no-hook2] [--words cut.txt]
        [--set 'cards=[{"who": "patel", "at": 2.0, "hold": 4}]' --set poster_at=9]

The window is the run's own editorial call (it read the transcript; this does not guess): the same moment, tighter, that
still opens on the hook and lands the payoff. `--segments` keeps pieces of it in order (the engine's supercut) so a
middle stretch can go; `--end` keeps the clip's start and ends earlier, only when the payoff lands before the clip's end.
Name cards, figures, poster_at, quote_at and hook2_at are in delivered seconds: a cut that starts later than its clip, or
leaves out a middle piece, must re-time each one the clip has with `--set` (it is refused otherwise). This
  1. finds the clip's job (the skill's Publish/schedule.json) and its work folder (the job's project.json `source`),
  2. clones the work folder (APFS clone: instant, no space) and writes ONE slate entry, `<slug>-ig`, with the
     platform's band (outlets.band) as the job's min_clip/max_clip and "cut_for" set, then runs the skill's
     preflight.py on it: a FAIL refuses the cut before anything renders,
  3. renders it through the skill's own build_all.py with /usr/bin/python3 (the interpreter the main renders use),
  4. checks the result is inside that band and runs the skill's grade.py on it (a BAD refuses it), and refuses it
     when the render dropped a name plate it was given (the skill drops one rather than put it on another face), then
     writes it beside the clip in its delivery folder as `instagram.mp4` (its captions as `instagram.srt`; `--words`
     as `instagram.cut.txt`, the words for that cut), and prints grade's warnings on the plates and the cover and
     every plate the render moved onto another shot,
  5. collects, so the cut joins its clip on the board (an unsigned clip; a signed one waits for Pull + Approve).
The skill's own code is never changed: this only drives it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

from . import collect as K
from . import config as C
from . import outlets as O

PLAT = "instagram_reels"
YOUTUBE = "youtube_shorts"
# platform -> (the cut's slate suffix, its file stem beside the clip: collect.CUT_FILES takes <stem>.mp4 / <stem>.cut.txt)
CUT_NAMES = {PLAT: ("ig", "instagram"), YOUTUBE: ("yt", "youtube")}
# the interpreter the main renders use (the runbook): the first python3 on PATH here is Anaconda 3.13 with another PIL,
# so a clip and its cut were drawn by different interpreters (audit LDG-5)
ENGINE_PYTHON = "/usr/bin/python3"
# what grade.py says about a cut that the person running it should look at
LOOK_AT = ("CARDS", "COVER", "SPLIT")
# timed in DELIVERED seconds, so a cut whose timeline moves must re-time each one the clip has (--set); the broll
# list is in master seconds and the framing strap opens every cut, so neither is here
DELIVERED_TIMED = ("cards", "figures", "poster_at", "quote_at", "hook2_at")


class CutError(SystemExit):
    pass


def _schedule(acct: C.Account) -> dict:
    p = acct.account_dir / "Publish" / "schedule.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        raise CutError(f"cannot read {p}: the skill's day schedule names each clip's job") from None


def find_job(acct: C.Account, slug: str) -> Path:
    for day, slots in sorted(_schedule(acct).items(), reverse=True):
        for ent in (slots or {}).values():
            if (ent or {}).get("slug") == slug:
                job = acct.account_dir / "Jobs" / ent["job"]
                if job.is_dir():
                    return job
    raise CutError(f"no job in Publish/schedule.json carries the clip {slug!r}")


def find_delivery(acct: C.Account, day: date, slug: str) -> Path:
    for _, folder in K.find_delivered(acct, day):
        if folder.name.split(" ", 1)[-1] == slug:
            return folder
    raise CutError(f"no delivered folder '<HHMM> {slug}' for {day} in {', '.join(map(str, acct.delivered_dirs(day)))}")


def _segments(text: str) -> List[Tuple[float, float]]:
    out = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        out.append((float(a), float(b)))
    if not out or any(b <= a for a, b in out) or any(out[i][1] > out[i + 1][0] for i in range(len(out) - 1)):
        raise CutError("--segments: pieces as start-end in master seconds, in order, not overlapping")
    return out


def _start(clip: dict) -> float:
    segs = clip.get("segments")
    return min(float(a) for a, _ in segs) if segs else float(clip["start"])


def engine(acct: C.Account) -> Path:
    """The account's own skill (accounts.json skill_dir): its scripts render and check the cut."""
    return acct.skill_dir / "scripts"


def _drops_a_piece(orig: dict, segs: List[Tuple[float, float]]) -> bool:
    """True when the cut leaves out a stretch inside the clip, so what follows it comes earlier in the cut."""
    have = [tuple(map(float, x)) for x in orig.get("segments") or [(orig["start"], orig["end"])]]
    def inside(a: float, b: float) -> bool:
        return any(x <= a + 0.05 and b <= y + 0.05 for x, y in have)
    gaps = [(segs[i][1], segs[i + 1][0]) for i in range(len(segs) - 1) if segs[i + 1][0] - segs[i][1] > 0.05]
    return any(inside(a, b) for a, b in gaps)


def _timed(orig: dict, hook2_kept: bool) -> List[str]:
    """The clip's fields timed in delivered seconds that a moved timeline would put on other moments."""
    out = []
    for k in DELIVERED_TIMED:
        v = orig.get(k)
        if k == "hook2_at" and not (hook2_kept and orig.get("hook2")):
            continue
        if k == "quote_at" and not (isinstance(v, (int, float)) and v > 0.5):
            continue              # the quote panel is up from frame one; only a later entry is a time
        if k == "figures" and not any(isinstance(f, dict) and f.get("at") is not None for f in v or []):
            continue
        if v not in (None, [], ""):
            out.append(k)
    return out


def _check_times(clipd: dict, hi: float) -> None:
    """Every delivered-seconds time the cut carries must fall inside the cut (at most `hi` long)."""
    times = [(f"{k}[{i}].at", f.get("at")) for k in ("cards", "figures") for i, f in enumerate(clipd.get(k) or [])
             if isinstance(f, dict)] + [(k, clipd.get(k)) for k in ("poster_at", "hook2_at", "quote_at")]
    for name, t in times:
        if isinstance(t, str) and t.strip().lower().startswith("pivot"):
            continue              # the engine's own pivot-relative time
        if t is not None and not (isinstance(t, (int, float)) and 0 <= t < hi):
            raise CutError(f"--set: {name} = {t!r} is not a time inside the cut (0-{hi:.0f}s, seconds into the cut)")


def _main_plates(job: Optional[Path], slug: str) -> Optional[List[str]]:
    """The plates the clip itself went out with (its own render's <clip>.cards.json in the job's clips/); None unread."""
    rec = next(iter(sorted((job / "clips").glob(f"*{slug}.cards.json"))), None) if job is not None else None
    if rec is None:
        return None
    try:
        return [str(r.get("who") or r.get("name") or "") for r in json.loads(rec.read_text()).get("cards") or []]
    except (OSError, ValueError, AttributeError):
        return None


def _plates(clipd: dict, out: Path, logf: Path, shown: Optional[List[str]] = None) -> List[str]:
    """Where the render put each name plate (the skill's <clip>.cards.json). A plate the cut was given that the render
    dropped refuses the cut: the skill drops one when no shot of that person can hold it, rather than name the wrong
    face, and a Politics clip names who is speaking. A plate it moved onto another shot is a frame to look at.
    THE CUT MATCHES ITS CLIP (2026-10-31): `shown` is the plates the clip itself went out with (_main_plates). When the
    clip's own render dropped a plate too (10-31 11:50 Dunleavy and 20:40 El-Sayed: "cards": [] on the posted clips),
    the cut without it is the same clip, so it goes out with a note instead of leaving Instagram or YouTube empty; a
    plate the clip shows and the cut lost still refuses."""
    rec = next(iter(sorted(out.glob(f"*{clipd['slug']}.cards.json"))), None)
    if rec is None:
        return []
    try:
        placed = list(json.loads(rec.read_text()).get("cards") or [])
    except (OSError, ValueError, AttributeError):
        return [f"{rec.name} will not parse: check every plate by eye"]
    want = [str(c.get("who") or c.get("name") or "") for c in clipd.get("cards") or [] if isinstance(c, dict)]
    got = [str(r.get("who") or r.get("name") or "") for r in placed]
    missing = []
    for w in want:
        if w in got:
            got.remove(w)
        else:
            missing.append(w)
    bare = [w for w in missing if shown is not None and w not in shown]
    missing = [w for w in missing if w not in bare]
    if missing:
        raise CutError(f"the render dropped the plate for {', '.join(missing)}: no shot of them near the time it was "
                       f"typed can hold it (the skill will not put it on another face; log {logf}). Time it with --set "
                       "cards=... on a shot where they are on camera and speaking, and run the cut again")
    return ([f"no plate for {', '.join(bare)}: the clip itself went out without one (no shot of them could hold it), "
             "so the cut matches it"] if bare else []) + \
        [f"plate {r.get('name')!r} typed +{r.get('typed_at')}s, placed +{r.get('at')}s on another shot "
         f"({r.get('moved')}): pull a frame at +{r.get('check_at')}s" for r in placed if r.get("warn")]


def _gate(py: str, eng: Path, script: str, slug: str, env: dict, logf: Path, what: str, timeout: int) -> str:
    """Run one of the skill's checks on the cut. A failure (exit status not 0) refuses the cut. Returns its log."""
    with open(logf, "w") as lf:
        r = subprocess.run([py, script, slug], cwd=str(eng), stdout=lf, stderr=subprocess.STDOUT, env=env,
                           timeout=timeout)
    text = logf.read_text() if logf.exists() else ""
    if r.returncode:
        bad = [ln.strip() for ln in text.splitlines() if "[FAIL]" in ln or "[ BAD  ]" in ln or ln.startswith("NOT READY")]
        raise CutError(f"{what} refused the cut (exit {r.returncode}); fix it and run the cut again. Log {logf}:\n  "
                       + "\n  ".join(bad[:12] or text.splitlines()[-12:]))
    return text


def _words(words: Optional[Path]) -> Optional[str]:
    if not words:
        return None
    text = Path(words).read_text()
    if not text.strip():
        raise CutError(f"{words} is empty")
    return text


PICK_TRIES = 3          # --pick: the most renders one cut gets (cutpick measures each inside the band first)
PICK_TIMEOUT = 1800     # cutpick decodes the clip and measures its picks again exactly as the render will


def _clone(work: Path, tag: str, slug: str, job: Path, lo: float, hi: float, plat: str) -> Path:
    """The cut's own work folder: an APFS clone of the job's (instant, no space), its project set to the platform's
    band, its own out-ig/ and the job's cast."""
    stamp = time.strftime("%H%M%S")
    cut_work = work.parent / f"{work.name}-{tag}-{re.sub(r'[^a-z0-9]+', '-', slug.lower())}-{stamp}"
    r = subprocess.run(["cp", "-cR", str(work), str(cut_work)], capture_output=True, text=True)
    if r.returncode:
        raise CutError(f"could not clone {work}: {r.stderr.strip()[:300]}")
    proj = json.loads((cut_work / "project.json").read_text())
    proj.update(out_dir=str(cut_work / "out-ig"), min_clip=lo, max_clip=hi, source=str(cut_work / "master.mp4"),
                cut_for=plat)   # the skill's preflight and grade judge a platform's own cut as one
    (cut_work / "out-ig").mkdir(exist_ok=True)
    if (job / "cast.json").is_file():          # the engine reads the job's cast (names on the cards) from out_dir
        shutil.copy2(job / "cast.json", cut_work / "out-ig" / "cast.json")
    (cut_work / "project.json").write_text(json.dumps(proj, indent=1))
    return cut_work


def _cutpick(py: str, eng: Path, cut_work: Path, job: Path, slug: str, log, near: str = "") -> dict:
    """The skill's cutpick.py on the cut's work folder (with `near`, a hand cut it measures first): its JSON result.
    Raises CutError when it says nothing readable."""
    logf = cut_work / "cutpick.log"
    log(f"{'measuring the hand cut ' + near if near else 'picking the cut'} with the skill's cutpick.py ({py}); "
        f"log {logf}")
    with open(logf, "w") as lf:
        r = subprocess.run([py, "cutpick.py", slug, "--slate", str(job / "slate.json"), "--json"]
                           + (["--near", near] if near else []), cwd=str(eng), stdout=subprocess.PIPE, stderr=lf,
                           text=True, env=dict(os.environ, CLIP_WORK=str(cut_work)), timeout=PICK_TIMEOUT)
    try:
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise CutError(f"cutpick.py said nothing readable (exit {r.returncode}); log {logf}") from None


def _pick(py: str, eng: Path, cut_work: Path, job: Path, slug: str, log) -> List[dict]:
    """cutpick.py's choices for the cut, best first: [{segments, speed, set, seconds, ...}]. Refuses when the skill
    has no cutpick.py, or nothing fits (said why, with the closest it found)."""
    if not (eng / "cutpick.py").is_file():
        raise CutError(f"--pick needs the skill's cutpick.py ({eng / 'cutpick.py'} is not there): give --segments")
    res = _cutpick(py, eng, cut_work, job, slug, log)
    top = res.get("pick")
    if not top:
        closest = "; ".join(",".join(f"{a:.1f}-{b:.1f}" for a, b in segs) for segs in res.get("closest") or [])
        raise CutError(f"cutpick found no cut for {slug}: {res.get('why') or 'nothing fits'}"
                       + (f". Closest: {closest}" if closest else "") + f". Log {cut_work / 'cutpick.log'}")
    _picked(top, log)
    return [top] + list(res.get("next") or [])       # each with its own re-timed cards, figures and poster


def _measured(py: str, eng: Path, work: Path, tag: str, slug: str, job: Path, lo: float, hi: float, plat: str,
              hand: str, log) -> Tuple[Path, List[dict]]:
    """A HAND CUT IS MEASURED BEFORE IT RENDERS (2026-10-29). Typed by eye, hand cuts missed the band 18 times
    10-17 to 10-29: the render takes out 5-14 s of silence nobody sees, preflight found each miss only after the
    clone, and the retries by eye swung both ways (Chillicothe 23.5 s, then over the span guard). The skill's
    cutpick.py measures it the render's way first. (the cut's work folder, choices): one that fits comes back as the
    only choice, marked "hand", with its cards, figures, poster and pages re-timed for it; one that misses comes back
    as the whole-sentence cuts that fit keeping the most of its time. No choices when cutpick cannot measure it or
    nothing near it fits: the hand cut then goes on as typed and preflight decides, as before."""
    cut_work = _clone(work, tag, slug, job, lo, hi, plat)
    try:
        res = _cutpick(py, eng, cut_work, job, slug, log, near=hand)
    except (CutError, subprocess.TimeoutExpired, OSError) as e:
        log(f"  the hand cut was not measured ({' '.join(str(e).split())[:200]}); it goes on as typed")
        return cut_work, []
    top, why = res.get("pick"), (res.get("near") or {}).get("why") or ""
    if not top:
        log(f"  the hand cut misses ({why or res.get('why')}) and no cut near it fits; it goes on as typed")
        return cut_work, []
    if top.get("hand"):
        log(f"the hand cut fits: {float(top['seconds']):.1f}s by the render's own measure")
        for n in (top.get("notes") or [])[1:]:
            log(f"  note: {n}")
        return cut_work, [top]
    log(f"the hand cut {hand} misses: {why}; taking the cut that fits nearest to it "
        f"({float((res.get('near') or {}).get('kept') or 0):.0%} of its time kept)")
    _picked(top, log)
    return cut_work, [top] + list(res.get("next") or [])


def _ends_at(orig: dict, end: float) -> str:
    """--end as pieces for the measure: the clip's own spans up to `end` (the last one runs on to it). '' when `end`
    is not inside the clip (render() refuses it below, as before)."""
    try:
        spans = sorted((float(a), float(b)) for a, b in (orig.get("segments") or [[orig["start"], orig["end"]]]))
    except (KeyError, TypeError, ValueError):
        return ""
    if not spans[0][0] < end <= spans[-1][1] + 4:
        return ""
    keep = [(a, min(b, end)) for a, b in spans if a < end - 0.05]
    if not keep:
        return ""
    keep[-1] = (keep[-1][0], end)
    return ",".join(f"{a:.2f}-{b:.2f}" for a, b in keep)


def _picked(top: dict, log) -> None:
    log(f"picked: --segments {top['segments_arg']} at x{top.get('speed')}"
        + (" (slower than the clip's own speed)" if top.get("speed_changed") else "")
        + f", {top['seconds']:.1f}s by the render's own measure, {top['pieces']} piece(s)")
    log(f"  opens: {str(top.get('opens'))[:140]!r}")
    log(f"  ends:  {str(top.get('ends'))[-140:]!r}")
    for n in top.get("notes") or []:
        log(f"  note: {n}")


def _render_cut(py: str, eng: Path, env: dict, clipd: dict, cut_work: Path, lo: float, hi: float, name: str,
                log, acct: C.Account) -> Tuple[Path, float, Path]:
    """The skill's preflight, its render and the band check, for the cut in cut_work/slate.json:
    (the mp4, its seconds, the render log). A refusal at any of them raises CutError."""
    log(f"checking {clipd['slug']} with the skill's preflight.py ({py}, {eng})")
    _gate(py, eng, "preflight.py", clipd["slug"], env, cut_work / "preflight.log", "preflight", 900)
    logf = cut_work / "igcut.log"
    log(f"rendering {clipd['slug']} ({lo:.0f}-{hi:.0f}s band) in {cut_work.name}; log {logf}")
    from . import renderlock as RL      # the render waits its turn in the render queue (state/renderlock)
    with RL.hold(acct.slug, f"cut: {clipd['slug']}", say=log, busy=CutError), open(logf, "w") as lf:
        r = subprocess.run([py, "build_all.py", clipd["slug"]], cwd=str(eng), stdout=lf, stderr=subprocess.STDOUT,
                           env=env, timeout=3600)
    outs = sorted((cut_work / "out-ig" / "clips").glob(f"*{clipd['slug']}.mp4"))
    if r.returncode or not outs:
        tail = logf.read_text()[-1200:] if logf.exists() else ""
        raise CutError(f"the render failed (exit {r.returncode}); the end of its log:\n{tail}")
    mp4 = outs[0]
    secs = K.probe_seconds(mp4)
    if secs is None or not lo - 0.05 <= secs <= hi + 0.05:
        raise CutError(f"the cut came out {secs}s, outside {name}'s {lo:.0f}-{hi:.0f}s here: "
                       "tighten the window (end earlier, or drop a middle piece) and run it again")
    return mp4, secs, logf


def missing(cfg: C.Config, acct: C.Account, day: date, plat: str = YOUTUBE) -> List[str]:
    """`ytcut --missing`: every clip on the day's board that needs its own cut for this platform and has none (the
    outlet's band, or its cut_when: outlets.needs_cut), with the command that makes it. A signed one takes the cut only
    after Pull + Approve; a clip the platform already has is left out."""
    from . import board as B
    bd = B.read(acct, day)
    if not bd:
        return [f"  no board for {acct.slug} on {day}"]
    name, cmd = O.NAMES.get(plat, plat), "ytcut" if plat == YOUTUBE else "igcut"
    out = []
    for s in sorted(bd["slots"].values(), key=lambda x: x["slot"]):
        rec = (s.get("outlets") or {}).get(plat) or {}
        if not s.get("clip") or s.get("status") not in B.SIGNABLE + B.SIGNED or rec.get("video_id") or rec.get("post_id"):
            continue
        why = O.needs_cut(acct, s, plat)
        if not why:
            continue
        how = f"factory.py {cmd} {acct.slug} --date {day.isoformat()} --slug {s.get('slug')} --segments A-B[,C-D]"
        if plat != PLAT and (s.get("cuts") or {}).get(PLAT):
            how += " (or --from-instagram: no render)"
        signed = "; signed: once the cut is made, Pull and Approve take it" if s.get("status") in B.SIGNED else ""
        out.append(f"  {s['slot']} {s.get('slug', '')}: no {name} cut ({why}){signed}. {how}")
    return out or [f"  {acct.name} {day}: every clip has the {name} cut it needs"]


def _as_posted(acct: C.Account, day: date, slug: str, folder: Path) -> dict:
    """The clip as the poster sees it, without its cuts: its board slot once collected (the very rights and length the
    poster and the card judge, so ytcut never refuses a cut the poster asks for), else the delivered clip with its
    folder, whose rights outlets.rights_of reads the way collect will record them."""
    from . import board as B
    slots = (B.read(acct, day) or {}).get("slots") or {}
    s = next((x for x in slots.values() if x.get("slug") == slug and x.get("clip")), None)
    if s:
        return dict(s, cuts={})
    return {"source_folder": str(folder), "publish_at": day.isoformat(),
            "clip": {"seconds": K.probe_seconds(K.main_clip(folder)) or 0.0}}


def _from_instagram(cfg: C.Config, acct: C.Account, day: date, slug: str, plat: str, folder: Path,
                    words: Optional[Path], lo: float, hi: float, collect: bool = True) -> List[str]:
    """The clip's Instagram cut taken as this platform's cut, as it is: igcut made it with the skill's preflight and
    grade, and Instagram's 30-50s sits inside YouTube's 30-59s. No render, for a night with no time left for one. The
    Instagram cut's own words (instagram.cut.txt) are Instagram's, so when it has them, --words gives this platform's."""
    stem = CUT_NAMES[plat][1]
    name = O.NAMES.get(plat, plat)
    src = folder / "instagram.mp4"
    if not src.is_file():
        raise CutError(f"{slug} has no Instagram cut to take ({src}): make the {name} cut with --segments or --end")
    if (folder / "instagram.cut.txt").is_file() and not words:
        raise CutError(f"{slug}'s Instagram cut has its own words (instagram.cut.txt: the clip's would overclaim for "
                       f"it), so the {name} cut needs its own too: --words <a copy of the clip's {stem}.txt, corrected>")
    text = _words(words)
    secs = K.probe_seconds(src)
    if secs is None or not lo - 0.05 <= secs <= hi + 0.05:
        raise CutError(f"the Instagram cut is {secs}s, outside {name}'s {lo:.0f}-{hi:.0f}s here: make the {name} cut "
                       "with --segments or --end")
    shutil.copy2(src, folder / f"{stem}.mp4")
    if (folder / "instagram.srt").is_file():
        shutil.copy2(folder / "instagram.srt", folder / f"{stem}.srt")
    if text is not None:
        (folder / f"{stem}.cut.txt").write_text(text)
    out = [f"  {slug}: {name} cut {secs:.1f}s -> {folder / (stem + '.mp4')} (its Instagram cut, which passed "
           "preflight and grade)"]
    if text is None:                           # the clip's own words go with a cut that may leave half of it out
        out.append(f"  {slug}: its {name} words are the clip's {stem}.txt: if they claim anything this cut leaves "
                   "out, run it again with --words <a corrected copy>")
    return out + (K.collect(cfg, acct, day) if collect else [])


def render(cfg: C.Config, acct: C.Account, day: date, slug: str, end: Optional[float] = None,
           segments: Optional[str] = None, hook: Optional[str] = None, hook2: Optional[bool] = None,
           words: Optional[Path] = None, python: Optional[str] = None, sets: Optional[List[str]] = None,
           log=print, plat: str = PLAT, from_instagram: bool = False, into: Optional[Path] = None,
           job_dir: Optional[Path] = None, pick: bool = False) -> List[str]:
    """`into` + `job_dir` (bench.py, `--bench ID`): the cut is made into a bench item's folder for that job's clip,
    found by neither a delivery folder nor Publish/schedule.json, and nothing is collected (no slot holds it yet).

    `pick` (`--pick`, 2026-10-22): the skill's cutpick.py chooses the window instead of the night's eye: whole
    sentences of the clip, in one piece or two, measured by the render's own steps (its silence removal, its
    out-point and its span guards), so the cut lands inside the band; it also re-times the clip's cards, figures
    and poster for the cut. When a picked cut still renders outside the band, the next one it measured is tried
    (at most PICK_TRIES renders).

    A hand cut (`--segments`, `--end`, 2026-10-29) is measured the same way before anything renders (_measured): it
    renders as typed when it fits, and the cut that fits nearest to it when it misses."""
    if plat not in CUT_NAMES:
        raise CutError(f"no cut is made for {plat}: {', '.join(CUT_NAMES)}")
    tag, stem = CUT_NAMES[plat]
    name = O.NAMES.get(plat, plat)
    lo, hi = O.band(acct, plat, cut=True)
    folder = into if into is not None else find_delivery(acct, day, slug)
    job = job_dir if job_dir is not None else find_job(acct, slug)
    project = json.loads((job / "project.json").read_text())
    if plat != PLAT:     # a cut only the outlet's rule asks for (the Politics's YouTube): none for a clip it takes whole
        view = _as_posted(acct, day, slug, folder)
        why = O.needs_cut(acct, view, plat)
        if not why:
            raise CutError(f"{slug}: {name} takes this clip whole ({float(view['clip']['seconds'] or 0):.0f}s, rights "
                           f"{O.rights_of(acct, view) or 'not recorded'}), so it needs no {name} cut")
        log(f"{slug} needs a {name} cut: {why}")
    if from_instagram:
        if plat == PLAT or segments or end is not None or hook or sets or hook2 is False or pick:
            raise CutError("--from-instagram takes the clip's Instagram cut as it is, for YouTube: no --segments, "
                           "--end, --pick, --hook, --no-hook2 or --set with it")
        return _from_instagram(cfg, acct, day, slug, plat, folder, words, lo, hi, collect=into is None)
    work = Path(project.get("source", "")).parent
    if not (work / "master.mp4").exists():
        raise CutError(f"the job's work folder is gone ({work}): the cut is rendered in the nightly run, while it is there")
    slate = json.loads((job / "slate.json").read_text())
    orig = next((c for c in slate.get("clips", []) if c.get("slug") == slug), None)
    if orig is None:
        raise CutError(f"{job.name}/slate.json has no clip {slug!r}")
    py = python or os.environ.get("CLIP_ENGINE_PYTHON") or (
        ENGINE_PYTHON if Path(ENGINE_PYTHON).exists() else shutil.which("python3") or "python3")
    eng = engine(acct)
    choices: List[dict] = []
    cut_work: Optional[Path] = None
    if pick:
        if segments or end is not None:
            raise CutError("--pick chooses the cut's pieces itself: no --segments or --end with it")
        cut_work = _clone(work, tag, slug, job, lo, hi, plat)
        choices = _pick(py, eng, cut_work, job, slug, log)
        segments = ",".join(f"{a:.2f}-{b:.2f}" for a, b in choices[0]["segments"])
    elif (segments or end is not None) and (eng / "cutpick.py").is_file():
        hand = ",".join(f"{a:.2f}-{b:.2f}" for a, b in _segments(segments)) if segments else _ends_at(orig, end)
        if hand:
            cut_work, choices = _measured(py, eng, work, tag, slug, job, lo, hi, plat, hand, log)
            if choices and not choices[0].get("hand"):
                segments, end = ",".join(f"{a:.2f}-{b:.2f}" for a, b in choices[0]["segments"]), None
            elif choices and end is not None and orig.get("segments"):
                segments, end = hand, None     # a clip of pieces ends where it was measured, not on its last piece

    clipd = deepcopy(orig)
    clipd["slug"] = f"{slug}-{tag}"
    if segments:
        clipd["segments"] = [list(x) for x in _segments(segments)]
        clipd.pop("start", None), clipd.pop("end", None)
    elif end is not None:
        if not orig["start"] < end <= orig["end"] + 4:
            raise CutError(f"--end {end} is outside the clip ({orig['start']}-{orig['end']})")
        clipd["end"] = end
    else:
        raise CutError("say where the cut ends (--end) or which pieces it keeps (--segments)")
    clipd["hard_out"] = True                   # no run-on past the chosen end: the band is a hard limit here
    clipd.pop("min_len", None)
    if hook:
        clipd["hook"] = hook
    if hook2 is False:
        clipd.pop("hook2", None), clipd.pop("hook2_at", None)
    picked_sets = dict(choices[0].get("set") or {}) if choices else {}
    if choices:
        clipd["speed"] = choices[0].get("speed")
        clipd.update(picked_sets)            # the clip's cards, figures and poster, re-timed for the cut by cutpick
    for kv in sets or []:          # e.g. cards=[{"who": "warsh", "at": 4.5, "hold": 4}]  poster_at=10: seconds into the CUT
        k, _, v = kv.partition("=")
        try:
            clipd[k.strip()] = json.loads(v)
        except ValueError:
            raise CutError(f"--set {kv!r}: the value must be JSON (a number, a list, a quoted string)") from None

    # A CUT THAT STARTS LATER, OR LEAVES OUT A MIDDLE PIECE, MOVES EVERYTHING TIMED IN DELIVERED SECONDS: the clip's
    # name cards, figures, poster_at, quote_at and hook2_at would land on other moments (the 10-17 Warsh and Atkins
    # cuts put plates on the wrong faces). Each one the clip has is re-timed for the cut with --set, or it is refused.
    lead = _start(clipd) - _start(orig)
    dropped = bool(segments) and _drops_a_piece(orig, [tuple(x) for x in clipd["segments"]])
    if lead > 0.05 or dropped:
        given = {kv.partition("=")[0].strip() for kv in sets or []} | set(picked_sets)
        missing = [k for k in _timed(orig, hook2 is not False) if k not in given]
        if missing:
            moved = (f"starts {lead:.1f}s after its clip" if lead > 0.05 else "leaves out a piece of its clip")
            raise CutError(f"the cut {moved}, so the clip's {', '.join(missing)} would land on other moments: time "
                           "each one for the cut with --set (seconds into the cut; [] drops a list), e.g. "
                           "--set 'cards=[{\"who\": \"patel\", \"at\": 2.0, \"hold\": 4}]' --set poster_at=9 "
                           "--set figures=[]")
    _check_times(clipd, hi)

    if cut_work is None:
        cut_work = _clone(work, tag, slug, job, lo, hi, plat)
    env = dict(os.environ, CLIP_WORK=str(cut_work))
    tries = [None] + [c for c in choices[1:PICK_TRIES]] if choices else [None]
    failed: List[str] = []
    for n, other in enumerate(tries):
        if other is not None:              # the next pick cutpick measured inside the band
            clipd["segments"] = [list(x) for x in other["segments"]]
            clipd["speed"] = other.get("speed")
            clipd.update(other.get("set") or {})
            for kv in sets or []:          # a person's own --set wins over the pick's
                k, _, v = kv.partition("=")
                clipd[k.strip()] = json.loads(v)
            _check_times(clipd, hi)
            log(f"try {n + 1} of {len(tries)}: the next pick, --segments "
                + ",".join(f"{a:.2f}-{b:.2f}" for a, b in other["segments"]))
        (cut_work / "slate.json").write_text(json.dumps({**{k: v for k, v in slate.items() if k != "clips"},
                                                          "clips": [clipd]}, indent=1))
        try:
            mp4, secs, logf = _render_cut(py, eng, env, clipd, cut_work, lo, hi, name, log, acct)
            graded = _gate(py, eng, "grade.py", clipd["slug"], env, cut_work / "grade.log", "grade", 900)
            break
        except CutError as e:
            if n + 1 >= len(tries):
                if failed:
                    raise CutError(f"{e}\n  (picks tried before it: {'; '.join(failed)})") from None
                raise
            failed.append(" ".join(str(e).split())[:200])
            log(f"  that pick failed ({failed[-1][:160]}); trying the next")
    look = [ln.strip() for ln in graded.splitlines()
            if "[ WARN ]" in ln and ln.split("]", 1)[-1].split()[:1] and ln.split("]", 1)[-1].split()[0] in LOOK_AT]
    look += _plates(clipd, cut_work / "out-ig" / "clips", logf, _main_plates(job, slug))
    text = _words(words)                       # before anything is delivered: an empty words file delivers nothing
    shutil.copy2(mp4, folder / f"{stem}.mp4")
    srt = next(iter(sorted((cut_work / "out-ig" / "captions").glob(f"*{clipd['slug']}.srt"))), None)
    if srt:
        shutil.copy2(srt, folder / f"{stem}.srt")
    if text is not None:
        (folder / f"{stem}.cut.txt").write_text(text)
    out = [f"  {slug}: {name} cut {secs:.1f}s -> {folder / (stem + '.mp4')} (preflight and grade passed)"]
    out += [f"    LOOK: {ln}" for ln in look]
    if into is None:                           # a bench item's cut waits on the bench; nothing to collect
        out += K.collect(cfg, acct, day)
    return out
