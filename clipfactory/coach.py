"""The hook coach: a scorecard for every ready clip, measured in code and written by the Claude task clip-coach.

Two layers, one file per clip: state/<account>/coach/<YYYY-MM-DD>/<HHMM>.json (never in a delivery folder or a Clip
Drafts folder).

  measured   numbers read from the clip's own files, in code: the time to the first spoken word, the words a second in
             the first 3 s, the silence, the longest hole, the picture changes in the first 3 s (ffmpeg, 06:26 to 20:45
             only), the hook on screen, the YouTube title's features, the length against the account's best band, and
             the record of the clip's creator, format, slot and length (learn/features.json, when the learner has
             written it). Four of the five categories are scored 0 to 100 from them (hook .4, keep watching .2, no
             sound .15, density .15; the playbook's coach weights win when learn.playbook is on). Share is the task's
             alone. The skills already cut clean mechanics (a Politics clip talks at 0.4 s, 4 to 5 words a second, with
             little silence), so these numbers sit at 95 to 100 and say little on their own: they are one Mechanics
             line on the card, and a mechanic raises a flag only when it is weak.
  written    the clip-coach task (prompts/coach.md, 05:20 and 12:20) reads `coach card`, judges the moment, the hook
             words and the payoff, and writes five scores 1 to 10, one fix and three other titles with `coach write`.
             Titles only from words in the transcript, the slate or a verified cast.

The card on Clip Review gets a coach fold. Its headline is "Coach" with the written total ("Coach 6.4 of 10", once the
task wrote) and an EXPECTED line from the learner's records: the creator, format, slot and length, each with its n,
worded only from 5 posts on one platform ("MrBossFTW clips: usually 1.3x on Facebook, 9 posts"), else "too few to
say". A record raises a flag only on strong evidence (8 posts or more on one platform, usually under half the usual,
three in four under it). Inside: the records, the written scores, the fix and the three titles, each with "Use this
title" while the clip waits for its sign-off, then "Mechanics 97: talks at 0.4 s, 4.7 words a second". The scorecard
keeps every field the learner and the cards read (measured.total, written.scores, signals.*_record) and adds
headline, expected and mechanics. "Use this title" rewrites only the YouTube title in the clip's
youtube_shorts.txt (GTA: line 1, keeping its " | credit" suffix when it fits in 100; Politics: the TITLE section), and
Approve signs the words as they are then (signoff._as_is). "(undo)" puts the old words back.

A scorecard is stale when its clip_sha is not the slot's clip any more; collect writes a fresh one, the studio job
catches up. The work folders a clip came from can vanish (/private/tmp/clipwork), so at collect the few inputs the
coach reads (the srt, the GTA pack, the Politics render log lines, the GTA grade block, reactions words) are copied into
state/<account>/coach/inputs/<sha16>/.

Off for every account until `factory.py social on <account> coach`: off, it reads nothing, writes nothing and draws
nothing. It never reaches the network and never queues a render.

    factory.py coach pending --date today                         READY clips without a written layer
    factory.py coach card <acct> --date D --slot HH:MM            what the task reads before it judges
    factory.py coach write <acct> --date D --slot HH:MM --file F --by clip-coach
    factory.py coach measure [<acct>] [--date D] [--slot HH:MM] [--scenes]
    factory.py coach show <acct> --date D [--slot HH:MM] [--json]
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shutil
import statistics
import subprocess
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import board as B
from . import config as C
from . import registry as REG
from . import store
from . import switches as SW

V = 1
KEY = "coach"                                    # the switch: switches.MODES["coach"] off/on
CATS = ("hook", "keep_watching", "no_sound", "density", "share")
CAT_WORDS = {"hook": "Hook", "keep_watching": "Keep watching", "no_sound": "No sound", "density": "Density",
             "share": "Share"}
# what the task's five scores judge (prompts/coach.md): the moment, the hook words and the payoff, not the mechanics
WRITTEN_WORDS = {"hook": "Hook words", "keep_watching": "Payoff", "no_sound": "Reads muted", "density": "Moment",
                 "share": "Share"}
RECORDS = ("creator", "format", "slot", "length")   # the EXPECTED line, in this order
SHOW_N = 5                                       # a record is worded from 5 posts on one platform
FLAG_N = 8                                       # a record raises a flag only from 8 posts on one platform,
FLAG_GAP = math.log(2.0)                         # when they usually did under half the usual,
FLAG_SHARE = 0.75                                # and at least three in four of them were under it
OUTLET_ORDER = ("youtube_shorts", "facebook_reels", "instagram_reels", "tiktok")
# learn.LENGTH_BANDS (a test keeps the two the same; learn is not imported here: collect's hook has 0.5 s)
LENGTH_BANDS = (("under 30s", 0, 30), ("30 to 45s", 30, 45), ("45 to 60s", 45, 60), ("60 to 75s", 60, 75),
                ("75s and over", 75, 181))
# a mechanics flag and the signal it is about (the Mechanics line names a weak one first)
FLAG_SIGNAL = {"slow_start": "first_word", "thin_open": "wps_3s", "long_gap": "longest_gap", "quiet": "silence",
               "too_long": "length", "too_short": "length", "no_hook_text": "hook_on_screen", "still_open": "scenes",
               "title_long": "title", "title_short": "title", "no_title": "title"}
WEIGHTS = {"hook": 0.4, "keep_watching": 0.2, "no_sound": 0.15, "density": 0.15}   # share: written only
WINDOW = ("06:26", "20:45")                      # ffmpeg only between these (rule 9: the night is untouched)
SCENE_THRESH = 0.12
SCENE_TIMEOUT_S = 20
SCENE_TRIES = 3                                  # a clip ffmpeg cannot read is tried this often, then left
SCENES_PER_TICK = 8                              # each a 3 s read; a tick never runs long
FIRST_WORD_MAX_S = 1.0                           # the playbook's coach.first_word_max_s wins
POLITICS_FIT_Y = 452                               # viral-clip-engine politicslook.FIT_Y (the broadcast look)
OTHER_FIT_Y = 420                                # clipkit.FIT_Y for any other look
TITLE_MAX = 100                                  # YouTube's title limit
FIX_MAX = 200
WHY_MAX = 400
DASHES = ("\u2014", "\u2013")                    # em and en dash: never in the factory's words
VERDICTS = ("PASS", "FIX", "KILL")              # the coach's one word on a clip (the loops thesis 10.30, item 1)
GRADE_LINE = re.compile(r"^\s*\[\s*(ok|WARN|BAD|\.\.)\s*\]\s+([A-Z][A-Z0-9_]*)\s+(.*)$")
NUM_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
             "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
             "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
             "nineteen": "19", "twenty": "20", "hundred": "100", "thousand": "1000", "million": "1000000",
             "billion": "1000000000"}
_TOKEN = re.compile(r"[A-Za-z0-9$][A-Za-z0-9$'\u2019.%-]*")
# Words that open a title as grammar, not as a name: a capital on one of these at the start is not a name. Any other
# capitalised first word must be in the transcript, the slate or the verified cast, like a capital anywhere else.
STARTERS = frozenset("""
a an the this that these those there here it its he she they we you i his her their our your my me him them us
who what when where why how which whose whom
is was are were be been am do does did done has have had will would can could should shall may might must
not no yes never always ever only just still even now then today tonight soon again once already also too
and but or so if as because while after before until since though although yet nor
to of in on at by for from with without into onto over under about after against between through during inside
outside up down out off near past around behind above below across
all any every each some most many much more less few both either neither none nobody nothing everyone everything
someone something anyone anything one two three first last next new old big huge small best worst real full
watch look see listen wait meet inside here's there's it's that's what's who's how's why's where's
""".split())


# ------------------------------------------------------------------------------------------------------ small helpers
def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def _day(v) -> date:
    return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v)[:10])


def _hhmm(slot: str) -> str:
    return C.normal_slot(str(slot)).replace(":", "")


def is_on(acct) -> bool:
    return SW.mode(acct, KEY) == "on"


def card_path(acct, day, slot: str) -> Path:
    """state/<slug>/coach/<YYYY-MM-DD>/<HHMM>.json (at call time)."""
    return store.path(_slug(acct), "coach", _day(day).isoformat(), _hhmm(slot) + ".json")


def inputs_dir(acct, sha: str) -> Path:
    return store.path(_slug(acct), "coach", "inputs", str(sha or "none")[:16])


def _read_json(p) -> object:
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError, TypeError):
        return None


def _now(cfg: C.Config, now: Optional[datetime] = None) -> datetime:
    now = now or cfg.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=cfg.tz())
    return now.astimezone(cfg.tz())


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def in_window(cfg: C.Config, now: Optional[datetime] = None) -> bool:
    """06:26 <= local time < 20:45: the only hours the coach runs ffmpeg (the night shift is never disturbed)."""
    t = _now(cfg, now).time()
    lo, hi = (dtime(int(x[:2]), int(x[3:])) for x in WINDOW)
    return lo <= t < hi


def _clean(s: object) -> str:
    return " ".join(str(s or "").split())


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _write_text(p: Path, text: str) -> None:
    store._inside(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


# ------------------------------------------------------------------------------------------------------ reading files
def parse_srt(text: str) -> List[Tuple[float, float, str]]:
    """(start, end, text) per cue of an .srt (a broken block is skipped)."""
    out: List[Tuple[float, float, str]] = []
    stamp = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            m = stamp.search(ln)
            if not m:
                continue
            g = [int(x) for x in m.groups()[:3]] + [int(m.group(4).ljust(3, "0"))]
            h = [int(x) for x in m.groups()[4:7]] + [int(m.group(8).ljust(3, "0"))]
            s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
            e = h[0] * 3600 + h[1] * 60 + h[2] + h[3] / 1000.0
            words = " ".join(x.strip() for x in lines[i + 1:])
            if e >= s:
                out.append((s, e, words))
            break
    return sorted(out, key=lambda c: c[0])


def _n_words(text: str) -> int:
    return len([w for w in text.split() if re.search(r"[A-Za-z0-9]", w)])


def srt_signals(cues: Sequence[Tuple[float, float, str]], length: Optional[float]) -> Dict[str, object]:
    """first_word_s, wps_3s, silence_ratio, longest_gap_s (+ its time), wps_all, words from delivered cues.
    Silence = 1 - the time some cue covers / the clip's length; a gap is counted from 0 and between cues (the end card
    after the last word is not a hole)."""
    if not cues:
        return {}
    first = cues[0][0]
    w3 = sum(_n_words(t) for s, _, t in cues if s < 3.0)
    total = sum(_n_words(t) for _, _, t in cues)
    spans: List[List[float]] = []
    for s, e, _ in cues:
        if spans and s <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([s, e])
    end = float(length) if length and length > 0 else max(e for _, e, _ in cues)
    covered = sum(max(0.0, min(e, end) - max(0.0, s)) for s, e in spans)
    gap, gap_at, prev = spans[0][0], 0.0, spans[0][1]
    for s, e in spans[1:]:
        if s - prev > gap:
            gap, gap_at = s - prev, prev
        prev = e
    return {"first_word_s": round(first, 3), "wps_3s": round(w3 / 3.0, 2),
            "silence_ratio": round(max(0.0, 1.0 - covered / end), 3) if end > 0 else None,
            "longest_gap_s": round(max(0.0, gap), 2), "longest_gap_at": round(gap_at, 2),
            "wps_all": round(total / end, 2) if end > 0 else None, "words": total}


def words_signals(words: Sequence[dict]) -> Dict[str, object]:
    """first_word_s and wps_3s from a GTA reactions words.json (already on the delivered clock)."""
    got = []
    for w in words or []:
        try:
            got.append((float(w["start"]), str(w.get("text") or "")))
        except (KeyError, TypeError, ValueError):
            continue
    got = sorted(x for x in got if x[1].strip())
    if not got:
        return {}
    return {"first_word_s": round(got[0][0], 3), "wps_3s": round(len([1 for s, _ in got if s < 3.0]) / 3.0, 2)}


TEMPO = re.compile(r"\btempo:?\s+(?:\S+\s+)?x([\d.]+)")    # the engine: "tempo: <NN-slug> x1.100  (set on the clip)"
_CLIP_NAME = re.compile(r"(?<![\w-])(\d{2}-[a-z][a-z0-9]*(?:-[a-z0-9]+)*)")


def _is_clip(name: str, slug: str) -> bool:
    return bool(slug) and (name == slug or re.sub(r"^\d{2}-", "", name) == re.sub(r"^\d{2}-", "", slug))


def render_lines(text: str, slug: str = "") -> List[str]:
    """The Politics render.log lines the coach keeps: silence removed, tempo, the name cards. With the clip's slug, only
    its own lines: a line naming another clip is left, and a card line (it names no clip) goes with the clip the log
    last named. The same line twice is kept once. A log that never names the clip is read whole."""
    if slug and not any(_is_clip(x, slug) for x in _CLIP_NAME.findall(text)):
        slug = ""
    keep: List[str] = []
    cur = ""
    for ln in text.splitlines():
        named = _CLIP_NAME.findall(ln)
        if named:
            cur = named[0]
        mine = not slug or not cur or _is_clip(cur, slug)
        if named and slug and not any(_is_clip(x, slug) for x in named):
            mine = False
        if mine and (re.search(r"removed [\d.]+\s*s of silence", ln) or TEMPO.search(ln)
                     or re.search(r"\bcard: ", ln)):
            if ln.strip() not in keep:
                keep.append(ln.strip())
    return keep


def render_numbers(lines: Sequence[str]) -> Dict[str, object]:
    out: Dict[str, object] = {}
    cards = []
    for ln in lines:
        m = re.search(r"removed ([\d.]+)\s*s of silence(?: in (\d+) cut)?", ln)
        if m:
            out["silence_removed_s"] = float(m.group(1))
            if m.group(2):
                out["silence_cuts"] = int(m.group(2))
        m = TEMPO.search(ln)
        if m:
            try:
                out["tempo"] = float(m.group(1).rstrip("."))
            except ValueError:
                pass
        m = re.search(r"\bcard: '([^']+)' at \+([\d.]+)s", ln)
        if m:
            cards.append({"name": m.group(1), "at": float(m.group(2))})
    if cards:
        out["cards"] = cards
    return out


def grade_block(text: str, slug: str = "") -> str:
    """The last `## the grade` block of a GTA RUN.md (the last one naming the slug when one does); for a hand job's
    build.log, its last run of grade lines."""
    blocks: List[str] = []
    cur: Optional[List[str]] = None
    for ln in text.splitlines():
        if ln.startswith("## "):
            if cur is not None:
                blocks.append("\n".join(cur))
            cur = [ln] if "grade" in ln.lower() else None
            continue
        if cur is not None:
            cur.append(ln)
    if cur is not None:
        blocks.append("\n".join(cur))
    if blocks:
        named = [b for b in blocks if slug and slug in b]
        return (named or blocks)[-1].strip()
    runs: List[List[str]] = []
    inside = False
    for ln in text.splitlines():
        if GRADE_LINE.match(ln):
            if not inside:
                runs.append([])
            runs[-1].append(ln)
            inside = True
        else:
            inside = False      # a run of grade lines ends at the first other line
    return "\n".join(runs[-1]).strip() if runs else ""


def grade_tags(block: str) -> Dict[str, str]:
    out = {}
    for ln in block.splitlines():
        m = GRADE_LINE.match(ln)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


# ----------------------------------------------------------------------------------------------------- where things are
def _job(acct: C.Account, s: dict, day: date) -> str:
    try:
        from . import bench as BN
        return BN._job_of(acct, s, BN._sched(acct), day.isoformat())
    except Exception:  # noqa: BLE001  (a label: a clip from the drop folder has none)
        return ""


def _slate(acct: C.Account, job: str, slug: str) -> Tuple[dict, dict]:
    if not job or not slug:
        return {}, {}
    try:
        from . import bench as BN
        got = BN._politics_slate(acct, job, slug) if acct.maker == "politics" else BN._gta_slate(acct, job, slug)
        clip, proj = got
        return (clip if isinstance(clip, dict) else {}), (proj if isinstance(proj, dict) else {})
    except Exception:  # noqa: BLE001
        return {}, {}


def _cast(acct: C.Account, job: str, clip: dict) -> dict:
    if acct.maker != "politics" or not job:
        return {}
    try:
        from . import bench as BN
        got = BN.politics_cast(acct, job, clip or {})
        return got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def cast_names(acct: C.Account, job: str, clip: dict) -> Tuple[List[str], List[str]]:
    """(the names verified by a person, every other name) on the Politics: the people on this clip's name cards
    (bench.politics_cast) and every other person in the job's cast.json, so a title can never name someone a bot or
    nobody verified, whether or not he has a card on this clip."""
    if acct.maker != "politics" or not job:
        return [], []
    ok: List[str] = []
    no: List[str] = []
    rows = list((_cast(acct, job, clip) or {}).get("people") or [])
    try:
        from . import bench as BN
        raw = BN._read(acct.account_dir / "Jobs" / job / "cast.json")
        bot = BN.BOT_WORDS
    except Exception:  # noqa: BLE001
        raw, bot = {}, None
    seen = {str(r.get("id") or "").lower() for r in rows if isinstance(r, dict)}
    for p in raw.get("people") or []:
        if not isinstance(p, dict) or str(p.get("id") or "").lower() in seen:
            continue
        said = str(p.get("verified") or "").strip()
        rows.append({"id": p.get("id"), "name": str(p.get("name") or ""), "verified": said,
                     "provisional": not said and isinstance(p.get("provisional"), dict),
                     "by_bot": bool(said and bot is not None and bot.search(said))})
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        good = bool(r.get("verified")) and not r.get("by_bot") and not r.get("provisional")
        (ok if good else no).append(name)
    return ok, [x for x in no if x not in ok]


def _srt_of(s: dict, folder: Optional[Path] = None) -> Optional[Path]:
    """The delivered .srt beside the clip (the drafts folder), else one in the delivery folder."""
    clip = Path(str((s.get("clip") or {}).get("path") or ""))
    cands: List[Path] = []
    if clip.name:
        cands.append(clip.with_suffix(".srt"))
    for d in [clip.parent if clip.name else None, folder]:
        if d is not None and Path(d).is_dir():
            cands += sorted(p for p in Path(d).glob("*.srt") if p.stem not in ("instagram", "youtube", "tiktok")
                            and not re.match(r"^.+ [2-9]$", p.stem))
    for p in cands:
        if p.is_file():
            return p
    return None


def _pack_of(s: dict, folder: Optional[Path]) -> Optional[Path]:
    hhmm = str(s.get("slot") or "").replace(":", "")
    for d in [folder, Path(str(s.get("source_folder"))) if s.get("source_folder") else None]:
        if d is None:
            continue
        p = Path(d).parent / f"{hhmm}.pack.json"
        if p.is_file():
            return p
    return None


def _yt_copy(s: dict) -> Optional[Path]:
    c = (s.get("copy") or {}).get("youtube_shorts") or {}
    return Path(str(c["path"])) if c.get("path") else None


def _copy_title(s: dict) -> str:
    from . import copytext as T
    p = _yt_copy(s)
    try:
        return T.for_platform(p.read_text(), "youtube_shorts")["title"].strip() if p else ""
    except OSError:
        return ""


# ------------------------------------------------------------------------------------------------------ inputs copied
def copy_inputs(cfg: C.Config, acct: C.Account, day: date, s: dict, folder: Optional[Path] = None) -> Dict[str, str]:
    """Copy what the coach reads and may vanish into state/<slug>/coach/inputs/<sha16>/ (file work only, small
    files). {name: where it came from}. Nothing is written into a delivery or drafts folder."""
    sha = str((s.get("clip") or {}).get("sha256") or "")
    if not sha:
        return {}
    d = inputs_dir(acct, sha)
    got: Dict[str, str] = {}
    srt = _srt_of(s, folder)
    if srt is not None:
        _copy(srt, d / "clip.srt")
        got["clip.srt"] = str(srt)
    job = _job(acct, s, day)
    slug = str(s.get("slug") or "")
    if acct.maker == "gta":
        pack = _pack_of(s, folder)
        if pack is not None:
            _copy(pack, d / "pack.json")
            got["pack.json"] = str(pack)
        work = acct.skill_dir / "work" / job if job else None
        if work is not None and work.is_dir():
            words = work / "words.json"
            if words.is_file() and _reactions(acct, s, job, slug):
                _copy(words, d / "words.json")
                got["words.json"] = str(words)
            for name in ("RUN.md", "build.log"):
                p = work / name
                if p.is_file():
                    try:
                        block = grade_block(p.read_text(errors="replace"), slug)
                    except OSError:
                        block = ""
                    if block:
                        _write_text(d / "grade.txt", block + "\n")
                        got["grade.txt"] = str(p)
                        break
    elif acct.maker == "politics":
        _, proj = _slate(acct, job, slug)
        src = str(proj.get("source") or "")
        if src:
            # the newest render log only: render.log and render2.log are two renders of the same clips
            logs = []
            for name in ("render.log", "render2.log"):
                p = Path(src).parent / name
                try:
                    logs.append((p.stat().st_mtime, p))
                except OSError:
                    continue
            lines: List[str] = []
            for _, p in sorted(logs, reverse=True):
                try:
                    lines = render_lines(p.read_text(errors="replace"), slug)
                except OSError:
                    continue
                if lines:
                    _write_text(d / "render.txt", "\n".join(lines) + "\n")
                    got["render.txt"] = str(p)
                    break
    if got:
        old = _read_json(d / "from.json")
        _write_text(d / "from.json", json.dumps(dict(old if isinstance(old, dict) else {}, **got), indent=1))
    return got


def _copy(src: Path, dst: Path) -> None:
    store._inside(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _reactions(acct: C.Account, s: dict, job: str, slug: str) -> bool:
    if str(s.get("format") or "") == "reactions":
        return True
    clip, _ = _slate(acct, job, slug)
    return str(clip.get("template") or "") == "reactions"


# ----------------------------------------------------------------------------------------------------- learn + playbook
def _features(acct) -> List[dict]:
    raw = _read_json(store.path(_slug(acct), "learn", "features.json"))
    rows = raw.get("rows") if isinstance(raw, dict) else raw
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def playbook(acct) -> Tuple[dict, str]:
    """(the playbook's coach block, where it came from): {} unless learn.playbook is on for the account."""
    if SW.mode(acct, "learn.playbook") != "on":
        return {}, "off"
    f = REG.fn("playbook:coach_block")
    if f is not None:
        try:
            got = f(acct)
            if isinstance(got, dict):
                return got, "playbook.py"
        except Exception as e:  # noqa: BLE001
            REG._log_error("playbook:coach_block", e)
    raw = _read_json(store.path(_slug(acct), "playbook.json"))
    blk = raw.get("coach") if isinstance(raw, dict) else None
    return (blk if isinstance(blk, dict) else {}), ("playbook.json" if isinstance(blk, dict) else "none")


def _key(s: object) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(s or "").lower()).split())


def _rel(r: dict) -> Optional[float]:
    x = r.get("rel_24h")
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _pid(r: dict, i: int) -> str:
    pid = r.get("pid")
    if pid in (None, ""):
        pid = f"{r.get('board')}|{r.get('slot')}" if r.get("board") and r.get("slot") else f"row{i}"
    return str(pid)


def _matcher(field: str, value):
    if field == "creator_key":
        return lambda r: _key(r.get(field)) == value
    return lambda r: r.get(field) is not None and r.get(field) == value


def _record_where(rows: Sequence[dict], keep) -> Optional[dict]:
    """{n: posts, median_rel} of the learner's rows `keep` takes. features.json has one row per post per outlet, so
    the rows are grouped by post first (pid; else board + slot): each post counts once, at the median of its
    outlets, and n is the number of posts."""
    posts: Dict[str, List[float]] = {}
    for i, r in enumerate(rows):
        x = _rel(r)
        if x is None or not keep(r):
            continue
        posts.setdefault(_pid(r, i), []).append(x)
    if not posts:
        return None
    vals = [statistics.median(v) for v in posts.values()]
    return {"n": len(vals), "median_rel": round(statistics.median(vals), 3)}


def _record(rows: Sequence[dict], field: str, value) -> Optional[dict]:
    return _record_where(rows, _matcher(field, value))


def _outlet_records(rows: Sequence[dict], keep) -> Dict[str, dict]:
    """{outlet: {n, median_rel, under}} of the rows `keep` takes, one platform at a time (the learner never pools
    platforms: each has its own usual). A post counts once on its platform; `under` is how many did under the
    usual. A row with no outlet is under ""."""
    posts: Dict[str, Dict[str, float]] = {}
    for i, r in enumerate(rows):
        x = _rel(r)
        if x is None or not keep(r):
            continue
        posts.setdefault(str(r.get("outlet") or ""), {})[_pid(r, i)] = x
    return {o: {"n": len(d), "median_rel": round(statistics.median(list(d.values())), 3),
                "under": sum(1 for v in d.values() if v < 0)} for o, d in posts.items()}


def length_band(secs) -> Optional[str]:
    """The learner's length band of a clip (learn._length_band)."""
    try:
        secs = float(secs or 0)
    except (TypeError, ValueError):
        return None
    if secs <= 0:
        return None
    for name, lo, hi in LENGTH_BANDS:
        if lo <= secs < hi:
            return name
    return LENGTH_BANDS[-1][0]


def format_words(fmt: str) -> str:
    return " ".join(str(fmt or "").replace("-", " ").replace("_", " ").split()).capitalize()


def records(acct, s: dict, rows: Sequence[dict], creator: str, fmt: str, hour: Optional[int],
            length: Optional[float]) -> Dict[str, dict]:
    """The four records the EXPECTED line reads, from the learner's features.json: {what: {label, n, median_rel,
    by_outlet}}. n and median_rel are across platforms (a post once); by_outlet is one platform at a time. The
    length record is the clip's length band on the platforms that get the clip itself (a platform with its own cut
    is another length)."""
    out: Dict[str, dict] = {}
    want: List[Tuple[str, str, object]] = []
    if creator:
        want.append(("creator", f"{creator} clips", _matcher("creator_key", _key(creator))))
    if fmt:
        want.append(("format", f"{format_words(fmt)} clips", _matcher("format", fmt)))
    if hour is not None:
        want.append(("slot", f"Clips posted between {hour:02d}:00 and {(hour + 1) % 24:02d}:00",
                     _matcher("hour", hour)))
    band = length_band(length)
    cuts = (s or {}).get("cuts") or {}
    main = {p for p in getattr(acct, "platforms", ()) or () if p not in cuts}
    if band and main:
        want.append(("length", f"Clips {band}", lambda r: (not r.get("outlet") or r.get("outlet") in main)
                     and length_band(r.get("sent_s")) == band))
    for what, label, keep in want:
        pooled = _record_where(rows, keep) or {"n": 0, "median_rel": None}
        out[what] = {"label": label, "n": pooled["n"], "median_rel": pooled["median_rel"],
                     "by_outlet": _outlet_records(rows, keep)}
    return out


def default_bands(acct: C.Account, s: Optional[dict] = None) -> Dict[str, List[float]]:
    """The best length band per outlet when the playbook has none: GTA 30 to 45 s (the skill's clip_band); the Politics
    the band each platform takes (its own cut's band where the slot has a cut for it)."""
    out: Dict[str, List[float]] = {}
    cuts = (s or {}).get("cuts") or {}
    for plat in acct.platforms:
        if acct.maker == "gta":
            out[plat] = [30.0, 45.0]
            continue
        lo, hi = float(acct.min_seconds), float(acct.max_seconds)
        try:
            from . import outlets as O
            lo, hi = (float(x) for x in O.band(acct, plat, cut=plat in cuts))
        except Exception:  # noqa: BLE001
            pass
        out[plat] = [lo, hi]
    return out


# ------------------------------------------------------------------------------------------------------ title features
def title_feat(title: str, names: Sequence[str] = ()) -> Dict[str, object]:
    t = str(title or "")
    letters = [c for c in t if c.isalpha()]
    low = " " + _key(t) + " "
    num = bool(re.search(r"\d", t)) or any(f" {w} " in low for w in NUM_WORDS)
    name = any(n and f" {_key(n)} " in low for n in names if len(_key(n)) >= 3)
    return {"number": num, "question": "?" in t, "name": name, "quote": bool(re.search(r"[\"\u201c\u201d]", t)),
            "caps_ratio": round(sum(1 for c in letters if c.isupper()) / len(letters), 2) if letters else 0.0}


# ------------------------------------------------------------------------------------------------------ the scores
def _lin(v: float, good: float, bad: float) -> float:
    """100 at `good` or better, 0 at `bad` or worse, a straight line between (either direction)."""
    if good == bad:
        return 100.0 if v == good else 0.0
    f = (v - bad) / (good - bad)
    return max(0.0, min(1.0, f)) * 100.0


def _mix(parts: Sequence[Tuple[Optional[float], float]]) -> Optional[int]:
    have = [(v, w) for v, w in parts if v is not None]
    if not have:
        return None
    tw = sum(w for _, w in have)
    return int(round(sum(v * w for v, w in have) / tw)) if tw > 0 else None


def _length_delta(secs: float, band: Sequence[float]) -> float:
    lo, hi = float(band[0]), float(band[1])
    return 0.0 if lo <= secs <= hi else round(secs - hi if secs > hi else secs - lo, 1)


def _fmax(pb: Optional[dict]) -> float:
    fmax = (pb or {}).get("first_word_max_s")
    return float(fmax) if isinstance(fmax, (int, float)) and not isinstance(fmax, bool) and 0 < fmax <= 5 \
        else FIRST_WORD_MAX_S


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def times(rel) -> str:
    """A record's usual as a multiple of the account's usual on that platform (rel is ln of the ratio)."""
    x = math.exp(float(rel))
    return f"{x:.0f}x" if x >= 9.95 else f"{x:.1f}x" if x >= 0.095 else "under 0.1x"


def _by_n(by: dict) -> List[Tuple[str, dict]]:
    """A record's platforms, the most posts first."""
    rank = {o: i for i, o in enumerate(OUTLET_ORDER)}
    return sorted(((str(o), v) for o, v in (by or {}).items() if isinstance(v, dict) and _num(v.get("n"))),
                  key=lambda x: (-int(x[1]["n"]), rank.get(x[0], len(rank)), x[0]))


def _on(outlet: str) -> str:
    from . import outlets as O
    return f" on {O.NAMES.get(outlet, outlet)}" if outlet else ""


def record_flags(sig: dict) -> List[dict]:
    """A flag from a record only on strong evidence: 8 posts or more on one platform that usually did under half the
    usual, three in four of them under it. Thin or mixed records raise none (they are on the EXPECTED line)."""
    out: List[dict] = []
    recs = sig.get("records") if isinstance(sig.get("records"), dict) else {}
    for what in RECORDS:
        r = recs.get(what)
        if not isinstance(r, dict) or not r.get("label"):
            continue
        for o, v in _by_n(r.get("by_outlet") or {}):
            n, med, under = int(v["n"]), v.get("median_rel"), int(v.get("under") or 0)
            if n >= FLAG_N and _num(med) and med <= -FLAG_GAP and under >= FLAG_SHARE * n:
                out.append({"key": f"weak_{what}", "kind": "record",
                            "words": f"{r['label']} have done {times(med)} the usual{_on(o)} over {n} posts, "
                                     f"{under} of them under it."})
                break
    return out


def score(acct, sig: dict, pb: Optional[dict] = None) -> Tuple[Dict[str, Optional[int]], List[dict]]:
    """(measured, flags) from the signals: four categories 0-100 and the weighted total, plus the plain flags (a
    mechanic's only when it is weak, a record's only on strong evidence)."""
    pb = pb or {}
    fmax = _fmax(pb)
    maker = str(getattr(acct, "maker", "") or "")
    flags: List[dict] = []
    fw, w3 = sig.get("first_word_s"), sig.get("wps_3s")
    sc = sig.get("scene_changes_3s")
    on_screen = sig.get("hook_on_screen")
    hook = _mix([(_lin(fw, fmax * 0.3, fmax * 3.0) if isinstance(fw, (int, float)) else None, 0.45),
                 (_lin(w3, 3.0, 0.0) if isinstance(w3, (int, float)) else None, 0.30),
                 ((100.0 if on_screen else 0.0) if on_screen is not None else None, 0.15),
                 ({0: 40.0, 1: 80.0}.get(sc, 100.0) if isinstance(sc, int) else None, 0.10)])
    if isinstance(fw, (int, float)) and fw > fmax:
        flags.append({"key": "slow_start", "words": f"The first word comes at {fw:.1f}s. Open on a spoken word "
                                                    f"(by {fmax:.1f}s)."})
    if isinstance(w3, (int, float)) and w3 < 1.5:
        flags.append({"key": "thin_open", "words": f"Only {int(round(w3 * 3))} words in the first 3 seconds."})
    if on_screen is False:
        flags.append({"key": "no_hook_text", "words": "No hook line on screen was found for this clip."})
    if isinstance(sc, int) and sc == 0 and maker == "gta":
        flags.append({"key": "still_open", "words": "The picture does not change in the first 3 seconds."})
    # keep watching: the length against the best band, the longest hole, the silence
    lens = sig.get("length_vs_best") if isinstance(sig.get("length_vs_best"), dict) else {}
    bands = sig.get("length_best") if isinstance(sig.get("length_best"), dict) else {}
    len_scores = []
    for plat, d in sorted(lens.items()):
        band = bands.get(plat) or [0, 0]
        width = max(5.0, (float(band[1]) - float(band[0])) / 2.0 + 5.0)
        len_scores.append(_lin(abs(float(d)), 0.0, width))
    over = sorted(((p, d) for p, d in lens.items() if d), key=lambda x: -abs(x[1]))
    if over:
        p, d = over[0]
        band = bands.get(p) or [0, 0]
        from . import outlets as O
        name = O.NAMES.get(p, p)
        flags.append({"key": "too_long" if d > 0 else "too_short",
                      "words": f"{name} gets {sig.get('length_by', {}).get(p, sig.get('length_s'))}s; its best band "
                               f"here is {band[0]:.0f} to {band[1]:.0f}s."})
    gap, sil = sig.get("longest_gap_s"), sig.get("silence_ratio")
    keep = _mix([(sum(len_scores) / len(len_scores) if len_scores else None, 0.40),
                 (_lin(gap, 1.2, 4.0) if isinstance(gap, (int, float)) else None, 0.35),
                 (_lin(sil, 0.08, 0.40) if isinstance(sil, (int, float)) else None, 0.25)])
    if isinstance(gap, (int, float)) and gap > 2.0:
        flags.append({"key": "long_gap",
                      "words": f"A {gap:.1f}s hole with no words at {sig.get('longest_gap_at', 0)}s."})
    if isinstance(sil, (int, float)) and sil > 0.25:
        flags.append({"key": "quiet", "words": f"No one speaks for {int(round(sil * 100))}% of the clip."})
    # no sound: words to read (the spoken words are burned in by both skills), the hook on screen, a title that says it
    tc = sig.get("title_chars")
    has_words = (True if sig.get("words") else False) if "words" in sig else None
    nosound = _mix([((100.0 if has_words else 0.0) if has_words is not None else None, 0.40),
                    ((100.0 if on_screen else 0.0) if on_screen is not None else None, 0.40),
                    (_lin(tc, 30, 10) if isinstance(tc, int) and tc <= TITLE_MAX else
                     (60.0 if isinstance(tc, int) else None), 0.20)])
    if isinstance(tc, int) and tc > TITLE_MAX:
        flags.append({"key": "title_long", "words": f"The YouTube title is {tc} characters; YouTube stops at 100."})
    elif isinstance(tc, int) and tc and tc < 20:
        flags.append({"key": "title_short", "words": f"The YouTube title is only {tc} characters."})
    elif tc == 0 and "title" in sig:
        flags.append({"key": "no_title", "words": "The YouTube words file has no title."})
    # density: words a second over the clip and how much of it is talk
    wall = sig.get("wps_all")
    dens = _mix([(_lin(wall, 2.8, 1.0) if isinstance(wall, (int, float)) else None, 0.6),
                 (_lin(sil, 0.08, 0.40) if isinstance(sil, (int, float)) else None, 0.4)])
    for f in flags:
        f["kind"] = "mechanics"
    flags += record_flags(sig)
    measured: Dict[str, Optional[int]] = {"hook": hook, "keep_watching": keep, "no_sound": nosound,
                                          "density": dens, "share": None}
    w = dict(WEIGHTS)
    for k, v in (pb.get("weights") or {}).items() if isinstance(pb.get("weights"), dict) else []:
        if k in WEIGHTS and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1:
            w[k] = float(v)
    measured["total"] = _mix([(float(measured[k]) if measured[k] is not None else None, w[k]) for k in WEIGHTS])
    return measured, flags


# ------------------------------------------------------------------------------------------------------ the headline
def expected(sig: dict) -> List[dict]:
    """The EXPECTED line: the creator, format, slot and length records, each with its n. A record is worded only
    from 5 posts on one platform ("MrBossFTW clips: usually 1.3x on Facebook, 9 posts"); under that it says so
    ("Gaggle clips: 4 posts, too few to say")."""
    recs = sig.get("records") if isinstance(sig.get("records"), dict) else {}
    out: List[dict] = []
    for what in RECORDS:
        r = recs.get(what)
        if not isinstance(r, dict) or not r.get("label"):
            continue
        label = str(r["label"])
        shown = [(o, v) for o, v in _by_n(r.get("by_outlet") or {})
                 if int(v["n"]) >= SHOW_N and _num(v.get("median_rel"))]
        n = int(r.get("n") or 0) if _num(r.get("n")) else 0
        if shown:
            words = f"{label}: usually " + "; ".join(f"{times(v['median_rel'])}{_on(o)}, {int(v['n'])} posts"
                                                     for o, v in shown)
        elif n <= 0:
            words = f"{label}: no posts to go by yet"
        elif n < SHOW_N:
            words = f"{label}: {n} post{'' if n == 1 else 's'}, too few to say"
        else:
            words = f"{label}: {n} posts, too few on any one platform to say"
        out.append({"what": what, "label": label, "n": n, "enough": bool(shown),
                    "outlets": [{"outlet": o, "n": int(v["n"]), "median_rel": v["median_rel"]} for o, v in shown],
                    "words": words})
    return out


def _sig_parts(sig: dict, flags: Sequence[dict], fmax: float) -> List[Tuple[str, str, float]]:
    """(signal, words, how good 0 to 100) for each mechanic measured, in the order the Mechanics line prefers."""
    out: List[Tuple[str, str, float]] = []
    fw, w3 = sig.get("first_word_s"), sig.get("wps_3s")
    if _num(fw):
        out.append(("first_word", "talks from the first frame" if fw < 0.05 else f"talks at {fw:.1f} s",
                    _lin(fw, fmax * 0.3, fmax * 3.0)))
    if _num(w3):
        out.append(("wps_3s", f"{w3:.1f} words a second", _lin(w3, 3.0, 0.0)))
    gap, sil = sig.get("longest_gap_s"), sig.get("silence_ratio")
    if _num(gap):
        out.append(("longest_gap", f"a {gap:.1f} s pause at {float(sig.get('longest_gap_at') or 0):.0f} s",
                    _lin(gap, 1.2, 4.0)))
    if _num(sil):
        out.append(("silence", f"{int(round(sil * 100))}% silence", _lin(sil, 0.08, 0.40)))
    lens = sig.get("length_vs_best") if isinstance(sig.get("length_vs_best"), dict) else {}
    bands = sig.get("length_best") if isinstance(sig.get("length_best"), dict) else {}
    off = sorted(((p, d) for p, d in lens.items() if _num(d) and d), key=lambda x: -abs(x[1]))
    if off:
        from . import outlets as O
        p, d = off[0]
        band = bands.get(p) or [0, 0]
        secs = (sig.get("length_by") or {}).get(p, sig.get("length_s"))
        width = max(5.0, (float(band[1]) - float(band[0])) / 2.0 + 5.0)
        out.append(("length", f"{O.NAMES.get(p, p)} gets {float(secs or 0):.0f} s, best {float(band[0]):.0f} to "
                              f"{float(band[1]):.0f} s", _lin(abs(float(d)), 0.0, width)))
    elif _num(sig.get("length_s")):
        out.append(("length", f"{float(sig['length_s']):.0f} s long", 100.0))
    on_screen = sig.get("hook_on_screen")
    if on_screen is not None:
        out.append(("hook_on_screen", "hook on screen" if on_screen else "no hook on screen",
                    100.0 if on_screen else 0.0))
    sc = sig.get("scene_changes_3s")
    if isinstance(sc, int) and not isinstance(sc, bool):
        out.append(("scenes", f"{sc} picture change{'' if sc == 1 else 's'} in the first 3 s",
                    {0: 40.0, 1: 80.0}.get(sc, 100.0)))
    tc = sig.get("title_chars")
    if any(FLAG_SIGNAL.get(f.get("key")) == "title" for f in flags) and isinstance(tc, int):
        out.append(("title", f"a title of {tc} characters" if tc else "no YouTube title", 0.0))
    return out


def mechanics(sig: dict, measured: dict, flags: Sequence[dict], pb: Optional[dict] = None) -> dict:
    """The measured layer in one line: "Mechanics 97: talks at 0.4 s, 4.7 words a second". The two most telling
    signals: a weak one first (one with a flag), else how the clip opens. `weak` names the flagged mechanics."""
    mflags = [f for f in flags or [] if isinstance(f, dict) and f.get("kind", "mechanics") == "mechanics"
              and not str(f.get("key") or "").startswith("weak_")]
    weak_sig = {FLAG_SIGNAL.get(str(f.get("key") or "")) for f in mflags} - {None}
    parts = _sig_parts(sig, mflags, _fmax(pb))
    weak = sorted((p for p in parts if p[0] in weak_sig), key=lambda p: p[2])
    pick = (weak + [p for p in parts if p not in weak])[:2]
    tot = (measured or {}).get("total")
    tot = int(tot) if _num(tot) else None
    head = f"Mechanics {tot}" if tot is not None else "Mechanics"
    words = head + (": " + ", ".join(p[1] for p in pick) if pick else ": not measured yet")
    return {"total": tot, "signals": [p[1] for p in pick], "weak": [str(f.get("key")) for f in mflags],
            "words": words}


def written_total(card: Optional[dict]) -> Optional[float]:
    """The written layer's total: the mean of its five scores, out of 10 (None until the clip-coach task wrote)."""
    w = (card or {}).get("written") if isinstance((card or {}).get("written"), dict) else None
    sc = [v for v in ((w or {}).get("scores") or {}).values() if _num(v)] \
        if isinstance((w or {}).get("scores"), dict) else []
    return round(sum(float(v) for v in sc) / len(sc), 1) if sc else None


def headline(card: dict, exp: Optional[List[dict]] = None) -> dict:
    """The headline: "Coach" and the written total when the task wrote one, then the EXPECTED line (the records
    with 5 posts or more on a platform, else "too few posts to say")."""
    exp = expected(card.get("signals") or {}) if exp is None else exp
    wt = written_total(card)
    enough = [e["words"] for e in exp if e.get("enough")]
    return {"written": wt, "words": f"Coach {wt:.1f} of 10" if wt is not None else "Coach",
            "expected": "; ".join(enough) if enough else "too few posts to say"}


def summarize(card: dict, pb: Optional[dict] = None) -> dict:
    """The headline, the EXPECTED records and the Mechanics line of a scorecard, from its own fields (so a scorecard
    measured before these fields existed reads the same way). Sets them on the card and returns it."""
    sig = card.get("signals") if isinstance(card.get("signals"), dict) else {}
    exp = expected(sig)
    card["expected"] = exp
    card["mechanics"] = mechanics(sig, card.get("measured") or {}, card.get("flags") or [], pb)
    card["headline"] = headline(card, exp)
    return card


# ------------------------------------------------------------------------------------------------------ measuring
def _names_for(acct: C.Account, s: dict, cast: dict) -> List[str]:
    names = []
    if s.get("host"):
        names += [x.strip() for x in str(s["host"]).split(",") if x.strip()]
    for p in (cast.get("people") or []) if isinstance(cast, dict) else []:
        if isinstance(p, dict) and p.get("name"):
            names.append(str(p["name"]))
    return names


def _lengths(acct: C.Account, s: dict) -> Dict[str, float]:
    """The seconds each outlet gets (its own cut when the slot has one: stats._sent_secs)."""
    length = (s.get("clip") or {}).get("seconds")
    length = float(length) if isinstance(length, (int, float)) and not isinstance(length, bool) else 0.0
    try:
        from . import stats as ST
        return {p: float(ST._sent_secs(s, p) or 0.0) for p in acct.platforms}
    except Exception:  # noqa: BLE001
        return {p: length for p in acct.platforms}


def lengths_changed(acct: C.Account, card: Optional[dict], s: dict) -> bool:
    """A platform's own cut joined (or left) the slot after the scorecard was measured: what an outlet gets is not
    what the scorecard says, so its length flags are stale."""
    if not card:
        return False
    was = ((card.get("signals") or {}).get("length_by"))
    if not isinstance(was, dict):
        return False
    now = {p: round(v, 1) for p, v in _lengths(acct, s).items() if v}
    return {k: round(float(v), 1) for k, v in was.items() if isinstance(v, (int, float))} != now


def _title_names(acct: C.Account, day: date, s: dict) -> List[str]:
    """The names title_feat looks for: the creator and the cast (as signals() measures them)."""
    try:
        job = _job(acct, s, day)
        clip_e, _ = _slate(acct, job, str(s.get("slug") or ""))
        return _names_for(acct, s, _cast(acct, job, clip_e))
    except Exception:  # noqa: BLE001
        return _names_for(acct, s, {})


def signals(cfg: C.Config, acct: C.Account, day: date, s: dict, folder: Optional[Path] = None) -> dict:
    """Every measured signal of a slot's clip from files (no ffmpeg, no network): the scene changes are kept from an
    earlier scorecard of the same clip, else missing until the studio job reads them."""
    sig: Dict[str, object] = {"sources": {}, "missing": []}
    src, miss = sig["sources"], sig["missing"]
    sha = str((s.get("clip") or {}).get("sha256") or "")
    inp = inputs_dir(acct, sha) if sha else None
    slug = str(s.get("slug") or "")
    job = _job(acct, s, day)
    clip_e, proj = _slate(acct, job, slug)
    length = (s.get("clip") or {}).get("seconds")
    length = float(length) if isinstance(length, (int, float)) and not isinstance(length, bool) else None
    sig["length_s"] = round(length, 2) if length else None
    # the spoken words: the delivered srt (or its copy), GTA reactions words.json
    srt = inp / "clip.srt" if inp is not None and (inp / "clip.srt").is_file() else _srt_of(s, folder)
    cues: List[Tuple[float, float, str]] = []
    if srt is not None:
        try:
            cues = parse_srt(srt.read_text(errors="replace"))
            src["srt"] = str(srt)
        except OSError:
            cues = []
    if cues:
        sig.update(srt_signals(cues, length))
    words = None
    if inp is not None and (inp / "words.json").is_file():
        words = _read_json(inp / "words.json")
        src["words"] = str(inp / "words.json")
    elif acct.maker == "gta" and job and _reactions(acct, s, job, slug):
        p = acct.skill_dir / "work" / job / "words.json"
        words = _read_json(p)
        if isinstance(words, list):
            src["words"] = str(p)
    if isinstance(words, list) and words_signals(words):
        sig.update(words_signals(words))
        if not cues:
            src.setdefault("srt", "")
    if not cues and not isinstance(words, list):
        miss += ["first_word_s", "wps_3s", "silence_ratio", "longest_gap_s"]
        src["srt"] = ""
    elif not cues:
        miss += ["silence_ratio", "longest_gap_s"]
    # the hook on screen
    hook = ""
    if acct.maker == "gta":
        pack = _read_json(inp / "pack.json") if inp is not None and (inp / "pack.json").is_file() else None
        if not isinstance(pack, dict):
            pp = _pack_of(s, folder)
            pack = _read_json(pp) if pp else None
        if isinstance(pack, dict) and pack.get("hook"):
            hook = _clean(pack["hook"])
            src["hook"] = "pack.json"
    if not hook and clip_e.get("hook"):
        hook = _clean(clip_e["hook"])
        src["hook"] = "slate"
    sig["hook_text"] = hook
    if hook:
        sig["hook_on_screen"] = True
    elif job:
        sig["hook_on_screen"] = False
    else:
        sig["hook_on_screen"] = None
        miss.append("hook_on_screen")
    # the title (the YouTube words, as they post)
    cast = _cast(acct, job, clip_e)
    title = _copy_title(s)
    sig["title"] = title
    sig["title_chars"] = len(title)
    sig["title_feat"] = title_feat(title, _names_for(acct, s, cast))
    src["title"] = str(_yt_copy(s) or "")
    if not _yt_copy(s):
        miss.append("title")
    # the length against the best band, per outlet
    pb, pb_from = playbook(acct)
    src["playbook"] = pb_from
    bands = default_bands(acct, s)
    if isinstance(pb.get("length_best"), dict):
        for plat, band in pb["length_best"].items():
            if (isinstance(band, (list, tuple)) and len(band) == 2
                    and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in band)
                    and band[0] < band[1]):
                bands[str(plat)] = [float(band[0]), float(band[1])]
    by = _lengths(acct, s)
    sig["length_by"] = {p: round(v, 1) for p, v in by.items() if v}
    sig["length_best"] = {p: bands[p] for p in by if p in bands}
    sig["length_vs_best"] = {p: _length_delta(v, bands[p]) for p, v in by.items() if v and p in bands}
    if not sig["length_vs_best"]:
        miss.append("length_vs_best")
    # who it came from, and the records
    creator = _clean(s.get("host"))
    sig["creator"] = creator
    rows = _features(acct)
    src["features"] = len(rows)
    trust_fmt = acct.maker == "politics" or bool(job)
    sig["format"] = str(s.get("format") or "") if trust_fmt else ""
    if not trust_fmt:
        miss.append("format (a clip from the drop folder: its format is not known)")
    try:
        hour = int(str(s.get("slot") or "0:0")[:2])
    except ValueError:
        hour = None
    sig["creator_record"] = _record(rows, "creator_key", _key(creator)) if creator else None
    sig["slot_record"] = _record(rows, "hour", hour) if hour is not None else None
    sig["format_record"] = _record(rows, "format", sig["format"]) if sig["format"] else None
    sig["records"] = records(acct, s, rows, creator, sig["format"], hour, length)
    sig["length_record"] = ({k: sig["records"]["length"][k] for k in ("n", "median_rel")}
                            if (sig["records"].get("length") or {}).get("n") else None)
    if not creator:
        miss.append("creator")
    # the render log (Politics) and the grade (GTA), when they were kept
    if inp is not None and (inp / "render.txt").is_file():
        try:
            sig["render"] = render_numbers((inp / "render.txt").read_text().splitlines())
            src["render_log"] = str(inp / "render.txt")
        except OSError:
            pass
    if inp is not None and (inp / "grade.txt").is_file():
        try:
            sig["grade"] = grade_tags((inp / "grade.txt").read_text())
            src["grade"] = str(inp / "grade.txt")
        except OSError:
            pass
    if job:
        src["job"] = job
    if acct.maker == "politics" and isinstance(cast, dict) and cast.get("status"):
        sig["cast"] = {"status": cast.get("status"),
                       "verified": [p.get("name") for p in cast.get("people") or []
                                    if isinstance(p, dict) and p.get("verified") and not p.get("by_bot")
                                    and not p.get("provisional") and p.get("name")]}
    sig["fit"] = acct.maker == "politics" and str(clip_e.get("mode") or "") == "fit"
    sig["fit_y"] = int(proj.get("fit_y") or (POLITICS_FIT_Y if str(proj.get("look") or "broadcast") == "broadcast"
                                             else OTHER_FIT_Y)) if sig["fit"] else None
    sig["scene_changes_3s"] = None
    return sig


def _blank(acct: C.Account, day: date, s: dict) -> dict:
    return {"v": V, "account": acct.slug, "board": day.isoformat(), "slot": str(s.get("slot") or ""),
            "slug": str(s.get("slug") or ""), "clip_sha": str((s.get("clip") or {}).get("sha256") or ""),
            "measured_at": "", "signals": {}, "flags": [], "measured": {}, "written": None, "predicted": None,
            "used_title": None}


def measure(cfg: C.Config, acct: C.Account, day: date, s: dict, folder: Optional[Path] = None,
            now: Optional[datetime] = None) -> dict:
    """Measure a slot's clip from its files and write its scorecard (the written layer, the title used and the scene
    changes of the same clip are kept; a new clip starts fresh). The scorecard."""
    day = _day(day)
    now = _now(cfg, now)
    sig = signals(cfg, acct, day, s, folder)
    pb, _ = playbook(acct)
    p = card_path(acct, day, s["slot"])
    with store.edit(p, dict) as d:
        same = bool(d) and d.get("clip_sha") == str((s.get("clip") or {}).get("sha256") or "")
        keep = {k: d.get(k) for k in ("written", "predicted", "used_title", "title_log")} if same else {}
        old = d.get("signals") if same and isinstance(d.get("signals"), dict) else {}
        for k in ("scene_changes_3s", "scene_times", "scene_tries", "scene_error"):
            if k in old and old[k] is not None:
                sig[k] = old[k]
        if sig.get("scene_changes_3s") is None:
            sig["missing"].append("scene_changes_3s")
        new = _blank(acct, day, s)
        new["measured_at"] = _iso(now)
        new["signals"] = sig
        new["measured"], new["flags"] = score(acct, sig, pb)
        new.update({k: v for k, v in keep.items() if v is not None})
        summarize(new, pb)
        d.clear()
        d.update(new)
        out = dict(d)
    return out


def load(acct, day, slot: str) -> Optional[dict]:
    d = _read_json(card_path(acct, day, slot))
    return d if isinstance(d, dict) else None


def fresh(card: Optional[dict], s: dict) -> bool:
    """The scorecard is of the clip now in the slot (a new clip makes it stale)."""
    return bool(card) and str(card.get("clip_sha") or "") == str((s.get("clip") or {}).get("sha256") or "") != ""


def total(acct, day, slot: str) -> Optional[int]:
    """The measured total of a clip's scorecard, for the learner (None when there is none)."""
    try:
        d = load(acct, day, slot)
        t = ((d or {}).get("measured") or {}).get("total")
        return int(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else None
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------------------------ collect's hook
def on_collect(ctx) -> None:
    """collect._take's hook (inside the board's lock): copy the inputs that may vanish and write the scorecard from
    files. Off: nothing at all. Never raises, never runs ffmpeg."""
    try:
        acct = ctx.acct
        if not is_on(acct):
            return None
        s = ctx.slot
        if not isinstance(s, dict) or not (s.get("clip") or {}).get("sha256"):
            return None
        folder = Path(ctx.folder) if getattr(ctx, "folder", None) else None
        day = _day(ctx.day)
        try:
            copy_inputs(ctx.cfg, acct, day, s, folder)
        except Exception as e:  # noqa: BLE001  (the copies are a help, never a reason to lose the scorecard)
            REG._log_error("coach:copy_inputs", e)
        measure(ctx.cfg, acct, day, s, folder)
    except Exception as e:  # noqa: BLE001  (a clip is never lost over its coach)
        REG._log_error("coach:on_collect", e)
    return None


# ------------------------------------------------------------------------------------------------------ scene changes
def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def scene_cmd(clip: Path, crop: str = "") -> List[str]:
    vf = (f"crop={crop}," if crop else "") + f"select='gt(scene,{SCENE_THRESH})',showinfo"
    return ["nice", "-n", "10", _ffmpeg(), "-hide_banner", "-nostdin", "-t", "3", "-i", str(clip), "-vf", vf,
            "-f", "null", "-"]


def parse_scenes(stderr: str) -> List[float]:
    """The times (s) showinfo printed for each frame the scene filter let through."""
    out = []
    for ln in (stderr or "").splitlines():
        if "showinfo" not in ln.lower() and "Parsed_showinfo" not in ln:
            continue
        m = re.search(r"pts_time:\s*(-?\d+(?:\.\d+)?)", ln)
        if m:
            out.append(round(float(m.group(1)), 3))
    return sorted(t for t in out if 0 <= t < 3.0)


def scenes(cfg: C.Config, clip: Path, fit_y: Optional[int] = None, now: Optional[datetime] = None) -> List[float]:
    """Picture changes in the first 3 s (ffmpeg's scene score, on the picture window for a Politics fit layout).
    Refuses outside 06:26 to 20:45."""
    if not in_window(cfg, now):
        raise SystemExit("the coach runs ffmpeg only from 06:26 to 20:45")
    crop = f"w=iw:h=iw*9/16:x=0:y=ih*{int(fit_y)}/1920" if fit_y else ""
    r = subprocess.run(scene_cmd(clip, crop), capture_output=True, text=True, timeout=SCENE_TIMEOUT_S,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg exit {r.returncode}: {(r.stderr or '').strip().splitlines()[-1:]}")
    return parse_scenes(r.stderr)


def _scene_into(cfg: C.Config, acct: C.Account, day: date, s: dict, now: datetime) -> str:
    """Read one clip's scene changes into its scorecard. What happened, in words."""
    if not in_window(cfg):
        return f"{acct.name} {s['slot']}: picture changes wait for 06:26 to 20:45"
    card = load(acct, day, s["slot"])
    sig = dict((card or {}).get("signals") or {})
    clip = Path(str((s.get("clip") or {}).get("path") or ""))
    tries = int(sig.get("scene_tries") or 0) + 1
    try:
        if not clip.is_file():
            raise RuntimeError(f"the clip is not there: {clip}")
        got = scenes(cfg, clip, sig.get("fit_y") if sig.get("fit") else None)   # its window: the clock now
        upd = {"scene_changes_3s": len(got), "scene_times": got, "scene_tries": tries, "scene_error": None}
        said = f"{acct.name} {s['slot']}: {len(got)} picture change{'' if len(got) == 1 else 's'} in the first 3s"
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as e:
        upd = {"scene_tries": tries, "scene_error": f"{type(e).__name__}: {str(e)[:200]}"}
        said = f"{acct.name} {s['slot']}: scene changes not read ({upd['scene_error']})"
    pb, _ = playbook(acct)
    with store.edit(card_path(acct, day, s["slot"]), dict) as d:
        if not fresh(d, s):
            return f"{acct.name} {s['slot']}: the clip changed while it was read; next time"
        sg = d.setdefault("signals", {})
        sg.update(upd)
        miss = [m for m in sg.get("missing") or [] if m != "scene_changes_3s"]
        if sg.get("scene_changes_3s") is None:
            miss.append("scene_changes_3s")
        sg["missing"] = miss
        d["measured"], d["flags"] = score(acct, sg, pb)
        summarize(d, pb)
    return said


# ------------------------------------------------------------------------------------------------------ the studio job
def _slots(cfg: C.Config, acct: C.Account, days: Sequence[date], statuses) -> List[Tuple[date, dict]]:
    out = []
    for d in days:
        try:
            bd = B.read(acct, d) or {}
        except (OSError, ValueError):
            continue
        for k, s in sorted((bd.get("slots") or {}).items()):
            if isinstance(s, dict) and s.get("status") in statuses and (s.get("clip") or {}).get("sha256"):
                out.append((d, s))
    return out


def accounts_on(cfg: C.Config) -> List[C.Account]:
    try:
        from . import social as SOC
        accts = SOC.accounts(cfg)
    except Exception:  # noqa: BLE001
        accts = list(cfg.accounts.values())
    return [a for a in accts if is_on(a)]


def tick(cfg: C.Config, now: Optional[datetime] = None, quiet: bool = True) -> List[str]:
    """The studio job (every 10 minutes): from 06:26 to 20:45, for every ready and signed clip of today and tomorrow,
    write a scorecard where there is none for this clip (or one measured before the records were read), then read the
    scene changes ffmpeg has not read yet (at most SCENES_PER_TICK). Outside those hours, or with the coach off, it
    does nothing at all."""
    now = _now(cfg, now)
    accts = accounts_on(cfg)
    if not accts or not in_window(cfg, now):
        return []
    said: List[str] = []
    today = now.date()
    budget = SCENES_PER_TICK
    for a in accts:
        for d, s in _slots(cfg, a, (today, today + timedelta(days=1)), (B.READY,) + B.SIGNED):
            card = load(a, d, s["slot"])
            older = "records" not in ((card or {}).get("signals") or {})    # measured before the records were read
            if not fresh(card, s) or lengths_changed(a, card, s) or older:
                try:
                    copy_inputs(cfg, a, d, s, Path(s["source_folder"]) if s.get("source_folder") else None)
                except Exception as e:  # noqa: BLE001
                    REG._log_error("coach:copy_inputs", e)
                card = measure(cfg, a, d, s, Path(s["source_folder"]) if s.get("source_folder") else None, now)
                said.append(f"{a.name} {s['slot']}: measured, {((card.get('measured') or {}).get('total'))}")
            sg = card.get("signals") or {}
            if sg.get("scene_changes_3s") is None and int(sg.get("scene_tries") or 0) < SCENE_TRIES and budget > 0:
                if not in_window(cfg):          # the clock now, not when the studio job began
                    return [] if quiet and not said else said
                budget -= 1
                said.append(_scene_into(cfg, a, d, s, now))
    return [] if quiet and not said else said


# ---------------------------------------------------------------------------------------------------- the written layer
def _tokens(text: str) -> List[str]:
    return _TOKEN.findall(str(text or ""))


def _norm(tok: str) -> str:
    t = tok.lower().strip(".'\u2019%-")
    t = re.sub(r"['\u2019]s$", "", t)
    return NUM_WORDS.get(t, t)


def vocabulary(cfg: C.Config, acct: C.Account, day: date, s: dict) -> Tuple[set, set]:
    """(the words a title may use for names and numbers, the names it may not use): every word of the transcript,
    the hook, the slate, every platform's words file, the creator and the verified cast; on the Politics, the names of
    people in the cast who are not verified by a person."""
    words: List[str] = []
    sha = str((s.get("clip") or {}).get("sha256") or "")
    inp = inputs_dir(acct, sha) if sha else None
    srt = inp / "clip.srt" if inp is not None and (inp / "clip.srt").is_file() else _srt_of(s)
    if srt is not None:
        try:
            words += [t for c in parse_srt(srt.read_text(errors="replace")) for t in _tokens(c[2])]
        except OSError:
            pass
    if inp is not None and (inp / "words.json").is_file():
        w = _read_json(inp / "words.json")
        words += [t for x in w if isinstance(x, dict) for t in _tokens(x.get("text"))] if isinstance(w, list) else []
    for c in (s.get("copy") or {}).values():
        try:
            words += _tokens(Path(str(c.get("path"))).read_text())
        except (OSError, TypeError):
            pass
    job = _job(acct, s, day)
    clip_e, proj = _slate(acct, job, str(s.get("slug") or ""))

    def walk(v) -> None:
        if isinstance(v, str):
            words.extend(_tokens(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for k in ("hook", "hook2", "quote", "titles", "platform_caption", "speaker", "tags", "framing", "credit",
              "question", "keywords"):
        walk(clip_e.get(k))
    for k in ("source_title", "source_channel", "host"):
        walk(proj.get(k))
    for k in ("host", "source_title"):
        walk(s.get(k))
    pack = _read_json(inp / "pack.json") if inp is not None and (inp / "pack.json").is_file() else None
    walk(pack if isinstance(pack, dict) else None)
    walk(acct.name)
    if acct.maker == "gta":
        words += ["GTA", "6", "VI", "Rockstar", "Vice", "City", "Leonida"]
    banned: set = set()
    ok, no = cast_names(acct, job, clip_e)
    for name in ok:
        words += _tokens(name)
    allowed = {_norm(x) for name in ok for x in _tokens(name)}
    for name in no:          # a surname a verified person shares is not banned (two Kennedys, one verified)
        banned |= {_norm(x) for x in _tokens(name) if len(x) > 2} - allowed
    return {_norm(t) for t in words}, banned


def check_title(title: str, vocab: Optional[set] = None, banned: Optional[set] = None) -> str:
    """'' when the title may be used; else what is wrong with it."""
    t = str(title)
    if not t.strip():
        return "a title is empty"
    if "\n" in t or "\r" in t:
        return "a title has a line break"
    if len(t.strip()) > TITLE_MAX:
        return f"a title is {len(t.strip())} characters (at most {TITLE_MAX}): {t.strip()[:60]}"
    if any(d in t for d in DASHES):
        return f"a title has an em or en dash: {t.strip()}"
    if "!" in t:
        return f"a title has an exclamation mark: {t.strip()}"
    if "<" in t or ">" in t:
        return f"a title has < or >, which YouTube does not take in a title: {t.strip()}"
    if vocab is None:
        return ""
    unknown, wrong = [], []
    for i, tok in enumerate(_tokens(t)):
        n = _norm(tok)
        if not n:
            continue
        if banned and n in banned and (tok[:1].isupper() or tok.isupper()):
            wrong.append(tok)
            continue
        is_num = bool(re.search(r"\d", tok))
        cap = tok[:1].isupper() and len(tok) > 1
        # the first word is a name unless it is a word that opens a sentence (a made-up name opens titles most)
        is_name = (cap and (i > 0 or n not in STARTERS)) or (tok.isupper() and len(tok) > 1 and n not in STARTERS)
        if (is_num or is_name) and n not in vocab:
            unknown.append(tok)
    if wrong:
        return (f"a title names someone the cast does not have verified by a person: {', '.join(wrong)} "
                f"(name only verified people)")
    if unknown:
        return (f"a title uses names or numbers that are not in the transcript, the slate or the cast: "
                f"{', '.join(unknown)} ({t.strip()})")
    return ""


def validate(data: object, vocab: Optional[set] = None, banned: Optional[set] = None) -> dict:
    """The written layer as the task gave it, checked: five whole scores 1 to 10, a fix of at most 200 characters,
    exactly three titles of at most 100, no em or en dash, no "!", no line breaks, and an optional verdict (PASS, FIX
    or KILL; a KILL says why). SystemExit says what to change."""
    if not isinstance(data, dict):
        raise SystemExit("the file must hold one JSON object: {scores, fix, titles, why}")
    sc = data.get("scores")
    if not isinstance(sc, dict):
        raise SystemExit(f"scores must be an object with {', '.join(CATS)}")
    extra = sorted(set(sc) - set(CATS))
    if extra:
        raise SystemExit(f"scores has keys it does not know: {', '.join(extra)} (the keys: {', '.join(CATS)})")
    scores = {}
    for k in CATS:
        v = sc.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 10:
            raise SystemExit(f"scores.{k} must be a whole number from 1 to 10 (got {v!r})")
        scores[k] = v
    fix = data.get("fix")
    if not isinstance(fix, str) or not fix.strip():
        raise SystemExit("fix must be one sentence")
    fix = fix.strip()
    if len(fix) > FIX_MAX:
        raise SystemExit(f"fix is {len(fix)} characters (at most {FIX_MAX})")
    titles = data.get("titles")
    if not isinstance(titles, list) or len(titles) != 3 or not all(isinstance(t, str) for t in titles):
        raise SystemExit(f"titles must be exactly three titles (got {len(titles) if isinstance(titles, list) else 0})")
    why = data.get("why", "")
    if not isinstance(why, str):
        raise SystemExit("why must be text")
    why = why.strip()
    if len(why) > WHY_MAX:
        raise SystemExit(f"why is {len(why)} characters (at most {WHY_MAX})")
    for name, text in (("fix", fix), ("why", why)):
        if "\n" in text or "\r" in text:
            raise SystemExit(f"{name} has a line break")
        if any(d in text for d in DASHES):
            raise SystemExit(f"{name} has an em or en dash")
        if "!" in text:
            raise SystemExit(f"{name} has an exclamation mark")
    clean = []
    for t in titles:
        bad = check_title(t, vocab, banned)
        if bad:
            raise SystemExit(bad)
        clean.append(" ".join(t.split()))
    if len({x.lower() for x in clean}) != 3:
        raise SystemExit("the three titles must differ")
    verdict = data.get("verdict") or ""
    if not isinstance(verdict, str) or (verdict.strip() and verdict.strip().upper() not in VERDICTS):
        raise SystemExit(f"verdict must be one word: {', '.join(VERDICTS)} (got {verdict!r})")
    verdict = verdict.strip().upper()
    if verdict == "KILL" and not why:
        raise SystemExit("a KILL says why in why")
    out = {"scores": scores, "fix": fix, "titles": clean, "why": why}
    if verdict:
        out["verdict"] = verdict
    return out


def write(cfg: C.Config, acct: C.Account, day: date, slot: str, data: object, by: str,
          now: Optional[datetime] = None) -> str:
    """The task's written layer into the clip's scorecard: READY clips only, the coach on, the scorecard of this clip
    (measured first when it is missing or stale)."""
    if not is_on(acct):
        raise SystemExit(f"the coach is off for {acct.name} (factory.py social on {acct.slug} coach)")
    if not str(by or "").strip():
        raise SystemExit("--by names who wrote it (the task's name, clip-coach)")
    day = _day(day)
    slot = C.normal_slot(slot)
    s = ((B.read(acct, day) or {}).get("slots") or {}).get(slot)
    if not isinstance(s, dict) or not s.get("clip"):
        raise SystemExit(f"{acct.name} {day} {slot}: no clip in that slot")
    if s.get("status") != B.READY:
        raise SystemExit(f"{acct.name} {day} {slot} is {s.get('status')}: the coach writes only for a clip waiting "
                         f"for its sign-off")
    vocab, banned = vocabulary(cfg, acct, day, s)
    got = validate(data, vocab, banned)
    cur = _copy_title(s)
    if cur:
        p = _yt_copy(s)
        text = p.read_text() if p is not None and p.is_file() else ""
        same = [t for t in got["titles"] if (final_title(text, t) if text else t).lower() == cur.lower()]
        if same:
            raise SystemExit(f"a title is the one the clip has now: {same[0]} (write three other titles)")
    now = _now(cfg, now)
    if not fresh(load(acct, day, slot), s):
        measure(cfg, acct, day, s, Path(s["source_folder"]) if s.get("source_folder") else None, now)
    with store.edit(card_path(acct, day, slot), dict) as d:
        if not fresh(d, s):
            raise SystemExit(f"{acct.name} {day} {slot}: the clip changed while this was written; run coach card again")
        d["written"] = dict(got, by=str(by).strip()[:60], at=_iso(now))
        summarize(d, playbook(acct)[0])
    w = sum(got["scores"].values()) / 5.0
    return f"{acct.name} {day} {slot}: written by {by} (average {w:.1f} of 10, three titles)"


# ------------------------------------------------------------------------------------------------------ the title swap
def final_title(text: str, title: str) -> str:
    """The YouTube title as it would be after the swap: a GTA file keeps its " | credit" suffix when it fits."""
    from . import copytext as T
    t = " ".join(str(title).split())
    if _sectioned(text):
        return t
    old = T.parse(text)["title"]
    if " | " in old and " | " not in t:
        suffix = old[old.index(" | "):]
        if len(t + suffix) <= TITLE_MAX:
            return t + suffix
    return t


def _sectioned(text: str) -> bool:
    from . import copytext as T
    return any(ln.strip() in T.HEADS for ln in text.splitlines()
               if not re.match(r"^#\s", ln) and ln.strip() != "#" and not T.NOTE_LINE.match(ln.strip()))


def swap_title(text: str, title: str) -> str:
    """The words file with only its YouTube title changed (GTA: the first line; Politics: the TITLE section). Every
    other byte stays."""
    from . import copytext as T
    new_t = final_title(text, title)
    lines = text.split("\n")
    if _sectioned(text):
        idx = next((i for i, ln in enumerate(lines) if ln.strip() == "TITLE"), None)
        if idx is None:      # no TITLE section: one before the first section
            first = next(i for i, ln in enumerate(lines) if ln.strip() in T.HEADS)
            lines[first:first] = ["TITLE", new_t, ""]
        else:
            j = next((i for i in range(idx + 1, len(lines)) if lines[i].strip() in T.HEADS), len(lines))
            body = [i for i in range(idx + 1, j) if lines[i].strip()]
            if body:
                lines[body[0]] = new_t
                for i in reversed(body[1:]):
                    del lines[i]
            else:
                lines.insert(idx + 1, new_t)
        return "\n".join(lines)
    first = next((i for i, ln in enumerate(lines) if ln.strip() and not re.match(r"^#\s", ln) and ln.strip() != "#"
                  and not T.NOTE_LINE.match(ln.strip())), None)
    if first is None:
        return new_t + "\n" + text
    lead = lines[first][:len(lines[first]) - len(lines[first].lstrip())]
    lines[first] = lead + new_t
    return "\n".join(lines)


def _check_ask(title: str) -> str:
    t = " ".join(str(title or "").split())
    if not t:
        raise SystemExit("There is no title to use.")
    if len(t) > TITLE_MAX:
        raise SystemExit(f"That title is {len(t)} characters; YouTube takes at most {TITLE_MAX}.")
    if any(d in t for d in DASHES) or "!" in t:
        raise SystemExit("That title has a dash or an exclamation mark; the factory's words never do.")
    if "<" in t or ">" in t:
        raise SystemExit("YouTube does not take < or > in a title.")
    if "\n" in str(title) or "\r" in str(title):
        raise SystemExit("A title is one line.")
    return t


def _target(cfg: C.Config, body: dict) -> Tuple[C.Account, date, str, dict]:
    fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
    acct = cfg.account(str(body.get("account") or ""))
    if not is_on(acct):
        raise SystemExit(f"The coach is off for {acct.name}.")
    raw_day = str(fields.get("date") or body.get("date") or "")
    try:
        day = date.fromisoformat(raw_day[:10])
    except ValueError:
        raise SystemExit("The page did not say which day (reload it).")
    slots = body.get("slots")
    if not isinstance(slots, list) or len(slots) != 1 or not isinstance(slots[0], str):
        raise SystemExit("Say which clip: one slot.")
    return acct, day, C.normal_slot(slots[0]), fields


def _seen_ok(s: dict, seen) -> None:
    if not isinstance(seen, str) or not seen:
        raise SystemExit("the page must say what it showed (reload it)")
    if seen != B.seen_key(s):
        raise SystemExit("This clip or its words changed since the page showed them. Look again, then try.")


def _write_words(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + ".coach-tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


def use_title(cfg: C.Config, acct: C.Account, day: date, slot: str, title: str, by: str, seen: str,
              now: Optional[datetime] = None) -> str:
    """Rewrite only the YouTube title of a READY clip's words file (under the board's lock) and record it on the
    scorecard. Approve signs the words as they are then."""
    want = _check_ask(title)
    now = _now(cfg, now)
    from . import copytext as T
    with B.edit(cfg, acct, day, create=False) as bd:
        s = (bd.get("slots") or {}).get(slot)
        if not isinstance(s, dict) or not s.get("clip"):
            raise SystemExit(f"No clip in {slot}.")
        if s.get("status") != B.READY:
            raise SystemExit("It is signed: Pull it to change its title." if s.get("status") in B.SIGNED
                             else f"{slot} is {s.get('status')}: only a clip waiting for its sign-off takes a title.")
        _seen_ok(s, seen)
        vocab, banned = vocabulary(cfg, acct, day, s)
        bad = check_title(want, vocab, banned)
        if bad:
            raise SystemExit(f"The coach will not use that title: {bad}.")
        p = _yt_copy(s)
        if p is None or not p.is_file():
            raise SystemExit(f"{slot} has no YouTube words file to change.")
        before = p.read_text()
        after = swap_title(before, want)
        was, now_t = T.for_platform(before, "youtube_shorts"), T.for_platform(after, "youtube_shorts")
        if now_t["title"] != final_title(before, want) or now_t["caption"] != was["caption"] \
                or now_t.get("tags", "") != was.get("tags", ""):
            raise SystemExit("The words file has a shape the coach cannot change safely; change the title by hand.")
        if after == before:
            return f"{acct.name} {slot}: that is already the YouTube title."
        _write_words(p, after)
        B.note(s, f"YouTube title set from the coach by {by}: {now_t['title']}")
        sha = str((s.get("clip") or {}).get("sha256") or "")
        names = _title_names(acct, day, s)
    rec = {"title": now_t["title"], "was": was["title"], "by": by, "at": _iso(now), "path": str(p),
           "before": before, "after_sha": B.sha256_text(after)}
    try:
        with store.edit(card_path(acct, day, slot), dict) as d:
            if d and d.get("clip_sha") == sha:
                d["used_title"] = rec
                d.setdefault("title_log", []).append({k: rec[k] for k in ("title", "was", "by", "at")})
                d["title_log"] = d["title_log"][-10:]
                sg = d.setdefault("signals", {})
                sg.update(title=now_t["title"], title_chars=len(now_t["title"]),
                          title_feat=title_feat(now_t["title"], names))
                pb = playbook(acct)[0]
                d["measured"], d["flags"] = score(acct, sg, pb)
                summarize(d, pb)
    except (OSError, SystemExit) as e:
        REG._log_error("coach:use_title record", e)
    return (f"{acct.name} {day} {slot}: the YouTube title is now \"{now_t['title']}\". Approve signs it with the "
            f"clip.")


def undo_title(cfg: C.Config, acct: C.Account, day: date, slot: str, by: str, seen: str,
               now: Optional[datetime] = None) -> str:
    """Put back the words file as it was before "Use this title" (READY only, and only when nothing else changed it
    since)."""
    now = _now(cfg, now)
    card = load(acct, day, slot) or {}
    rec = card.get("used_title") if isinstance(card.get("used_title"), dict) else None
    if not rec or not isinstance(rec.get("before"), str):
        raise SystemExit("There is no title from the coach to undo.")
    with B.edit(cfg, acct, day, create=False) as bd:
        s = (bd.get("slots") or {}).get(slot)
        if not isinstance(s, dict) or s.get("status") != B.READY:
            raise SystemExit("It is signed: Pull it to change its title." if isinstance(s, dict)
                             and s.get("status") in B.SIGNED else "Only a clip waiting for its sign-off can change.")
        _seen_ok(s, seen)
        p = _yt_copy(s)
        if p is None or str(p) != str(rec.get("path")) or not p.is_file():
            raise SystemExit("The words file is not the one the coach changed.")
        if B.sha256_text(p.read_text()) != rec.get("after_sha"):
            raise SystemExit("The words were changed after the title was set; change them by hand.")
        _write_words(p, rec["before"])
        B.note(s, f"YouTube title put back by {by}: {rec.get('was')}")
        names = _title_names(acct, day, s)
    with store.edit(card_path(acct, day, slot), dict) as d:
        d["used_title"] = None
        d.setdefault("title_log", []).append({"title": rec.get("was"), "was": rec.get("title"), "by": by,
                                              "at": _iso(now), "undo": True})
        d["title_log"] = d["title_log"][-10:]
        sg = d.setdefault("signals", {})
        sg.update(title=rec.get("was") or "", title_chars=len(rec.get("was") or ""),
                  title_feat=title_feat(rec.get("was") or "", names))
        pb = playbook(acct)[0]
        d["measured"], d["flags"] = score(acct, sg, pb)
        summarize(d, pb)
    return f"{acct.name} {day} {slot}: the YouTube title is back to \"{rec.get('was')}\"."


def act(cfg: C.Config, body: dict, page: bool = False, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """coach-title (Use this title) and coach-undo, from a card's coach fold. Only a click on the page."""
    from . import socialui as SX
    op = str(body.get("op") or "")
    if op not in ("coach-title", "coach-undo"):
        raise SystemExit(f"{op or 'that'}: not a coach click")
    SX.need_page(page, "change a clip's title")
    acct, day, slot, fields = _target(cfg, body)
    by = str(body.get("by") or (cfg.approvers[0] if cfg.approvers else "Clip Review"))
    if op == "coach-title":
        return True, use_title(cfg, acct, day, slot, str(fields.get("title") or ""), by, body.get("seen"), now)
    return True, undo_title(cfg, acct, day, slot, by, body.get("seen"), now)


# ------------------------------------------------------------------------------------------------------ the card part
CARD_CSS = """
details.cx{border-top:1px solid var(--line);padding-top:8px}
details.cx>summary{font-weight:600;color:var(--ink)}
details.cx>summary .cx-sub{font-weight:400;color:var(--sub)}
details.cx>summary .cx-exp{display:block;margin-top:2px;font-weight:400;color:var(--sub);overflow-wrap:anywhere}
.cx-k{font-size:11px;font-weight:650;text-transform:uppercase;letter-spacing:.05em;color:var(--sub)}
.cx-b{display:flex;flex-direction:column;gap:10px;margin-top:8px;font-size:12.5px;min-width:0}
.cx-recs{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:3px}
.cx-recs li{color:var(--ink);overflow-wrap:anywhere}
.cx-recs li.cx-thin{color:var(--sub)}
.cx-mech{color:var(--ink);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.cx-flags{margin:0;padding:0 0 0 16px;display:flex;flex-direction:column;gap:3px;color:var(--warn)}
.cx-w{display:flex;flex-direction:column;gap:6px;border-top:1px solid var(--line);padding-top:8px}
.cx-h{font-size:11px;color:var(--sub);font-weight:650;text-transform:uppercase;letter-spacing:.05em}
.cx-fix{color:var(--ink)}
.cx-t{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:6px 0;border-top:1px solid var(--line)}
.cx-t span{flex:1 1 160px;min-width:0;overflow-wrap:anywhere;color:var(--ink)}
.cx-t button{flex:0 0 auto}
.cx-used{color:var(--ink)}
.cx-sig,.cx-muted{color:var(--sub);overflow-wrap:anywhere}
@media (max-width:600px){details.cx>summary{display:list-item;min-height:40px;padding:10px 0;line-height:20px;
font-size:14px;box-sizing:border-box}
.cx-b{font-size:14px}.cx-t button,.cx-used button{height:40px;min-width:40px;font-size:16px}}
"""


def _split_flags(flags: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    """(record flags, mechanics flags) to show. A weak_ flag with no kind is from the old rule (5 posts were enough)
    and is not shown: the studio job measures that scorecard again."""
    got = [f for f in flags or [] if isinstance(f, dict) and f.get("words")]
    return ([f for f in got if f.get("kind") == "record"],
            [f for f in got if f.get("kind") != "record" and not str(f.get("key") or "").startswith("weak_")])


def _flag_list(flags: Sequence[dict]) -> str:
    got = [f for f in flags if isinstance(f, dict) and f.get("words")]
    return ('<ul class="cx-flags">' + "".join(f"<li>{esc(f['words'])}</li>" for f in got) + "</ul>") if got else ""


def fold_lines(card: dict, pb: Optional[dict] = None) -> List[str]:
    """What the card's coach fold says, as plain lines (coach show prints them): the headline, the EXPECTED
    records, the record flags, the Mechanics line and its flags, the written layer."""
    c = summarize(dict(card), pb)
    hd, mech = c["headline"], c["mechanics"]
    out = [hd["words"] + ("" if hd.get("written") is not None else " (not written yet)"),
           f"Expected: {hd['expected']}"]
    out += [f"  {e['words']}" for e in c["expected"]]
    rflags, mflags = _split_flags(c.get("flags") or [])
    out += [f"  flag: {f['words']}" for f in rflags]
    out.append(mech["words"])
    out += [f"  flag: {f['words']}" for f in mflags]
    w = c.get("written") if isinstance(c.get("written"), dict) else None
    if w:
        sc = w.get("scores") or {}
        out.append(f"Written by {w.get('by', '')}: "
                   + ", ".join(f"{WRITTEN_WORDS[k]} {sc.get(k)}" for k in CATS if isinstance(sc.get(k), int)))
        if w.get("fix"):
            out.append(f"Fix: {w['fix']}")
        out += [f"Title: {t}" for t in w.get("titles") or [] if isinstance(t, str)]
    return out


def _title_form(acct: C.Account, day: date, s: dict, title: str, current_text: str) -> str:
    final = final_title(current_text, title) if current_text else title
    ask = (f'Use "{final}" as the YouTube title of {acct.name} {s["slot"]} on {day.isoformat()}? Only the YouTube '
           f'words file changes; Approve signs it with the clip.')
    return (f'<div class="cx-t" data-x-form><span>{esc(title)}</span>'
            f'<input type="hidden" name="title" value="{esc(title)}">'
            f'<input type="hidden" name="date" value="{esc(day.isoformat())}">'
            f'<button type="button" data-op="coach-title" data-x data-acct="{esc(acct.slug)}" '
            f'data-slot="{esc(s["slot"])}" data-ask="{esc(ask)}">Use this title</button></div>')


def card_part(cfg: C.Config, acct: C.Account, day: date, s: dict, now: datetime) -> str:
    """The coach fold under a draft card ('' when the coach is off or the slot has no clip). Files only. Its
    headline is "Coach" with the written total (once the clip-coach task wrote one) and the EXPECTED line from the
    learner's records; inside, the records with their n, the written layer, then one Mechanics line."""
    if not is_on(acct) or not isinstance(s, dict) or not (s.get("clip") or {}).get("sha256"):
        return ""
    day = _day(day)
    card = load(acct, day, s.get("slot", ""))
    ready = s.get("status") == B.READY
    if not fresh(card, s):
        today = _now(cfg, now).date()
        if s.get("status") not in (B.READY,) + tuple(B.SIGNED) or day not in (today, today + timedelta(days=1)):
            return ""        # the studio job measures only ready and signed clips of today and tomorrow
        why = ("this clip has not been measured yet" if not card else "the scorecard is of an earlier clip")
        return (f'<details class="cx" data-k="cx"><summary>Coach <span class="cx-sub">not measured yet</span></summary>'
                f'<div class="cx-b"><div class="cx-muted">{esc(why.capitalize())}. It is measured when a clip is '
                f'collected, and every 10 minutes from 06:26 to 20:45.</div></div></details>')
    pb, _ = playbook(acct)
    card = summarize(dict(card), pb)
    sig = card.get("signals") or {}
    hd, mech = card["headline"], card["mechanics"]
    w = card.get("written") if isinstance(card.get("written"), dict) else None
    if hd.get("written") is not None:
        head = f'Coach {hd["written"]:.1f} <span class="cx-sub">of 10</span>'
    else:
        head = 'Coach <span class="cx-sub">not written yet</span>'
    summary = (f'{head}<span class="cx-exp"><span class="cx-k">Expected</span> {esc(hd["expected"])}</span>')
    rflags, mflags = _split_flags(card.get("flags") or [])
    parts = []
    if card["expected"]:
        thin = ' class="cx-thin"'
        rows = "".join(f'<li{"" if e["enough"] else thin}>{esc(e["words"])}</li>' for e in card["expected"])
        parts.append(f'<div class="cx-h">Expected, from the records</div><ul class="cx-recs">{rows}</ul>')
    parts.append(_flag_list(rflags))
    later = [f'<div class="cx-mech">{esc(mech["words"])}</div>', _flag_list(mflags)]
    if sig.get("hook_text"):
        later.append(f'<div class="cx-sig">Hook on screen: {esc(sig["hook_text"])}</div>')
    ut = card.get("used_title") if isinstance(card.get("used_title"), dict) else None
    if ut:
        undo = ""
        if ready:
            ask = (f'Put back the YouTube title "{ut.get("was", "")}" for {acct.name} {s["slot"]} on '
                   f'{day.isoformat()}?')
            undo = (f'<span data-x-form><input type="hidden" name="date" value="{esc(day.isoformat())}">'
                    f'<button type="button" class="link" data-op="coach-undo" data-x data-acct="{esc(acct.slug)}" '
                    f'data-slot="{esc(s["slot"])}" data-ask="{esc(ask)}">(undo)</button></span>')
        parts.append(f'<div class="cx-used">YouTube title from the coach: {esc(ut.get("title", ""))} '
                     f'({esc(ut.get("by", ""))}) {undo}</div>')
    if w:
        sc = w.get("scores") or {}
        line = ", ".join(f"{WRITTEN_WORDS[k]} {sc.get(k)}" for k in CATS if isinstance(sc.get(k), int))
        wp = [f'<div class="cx-h">Written by {esc(w.get("by", ""))}</div>', f'<div>{esc(line)}</div>']
        if w.get("fix"):
            wp.append(f'<div class="cx-fix">Fix: {esc(w["fix"])}</div>')
        if w.get("why"):
            wp.append(f'<div class="cx-muted">{esc(w["why"])}</div>')
        titles = [t for t in w.get("titles") or [] if isinstance(t, str)]
        if titles:
            text = ""
            p = _yt_copy(s)
            try:
                text = p.read_text() if p else ""
            except OSError:
                text = ""
            cur = _copy_title(s)
            for t in titles:
                if ready and text and final_title(text, t) != cur:
                    wp.append(_title_form(acct, day, s, t, text))
                else:
                    wp.append(f'<div class="cx-t"><span>{esc(t)}</span></div>')
        parts.append('<div class="cx-w">' + "".join(wp) + "</div>")
    else:
        parts.append('<div class="cx-muted">The written scores, the fix and three titles come from the clip-coach '
                     'task at 05:20 and 12:20.</div>')
    parts += later
    miss = [x for x in sig.get("missing") or [] if isinstance(x, str)]
    if miss:
        nice = {"scene_changes_3s": "picture changes (read from 06:26 to 20:45)", "first_word_s": "the first word",
                "wps_3s": "words in the first 3s", "silence_ratio": "silence", "longest_gap_s": "the longest pause",
                "hook_on_screen": "the hook on screen", "title": "the title", "creator": "the creator",
                "length_vs_best": "the length"}
        parts.append('<div class="cx-muted">Not measured: ' + esc(", ".join(nice.get(x, x) for x in miss)) + ".</div>")
    return (f'<details class="cx" data-k="cx"><summary>{summary}</summary>'
            f'<div class="cx-b">{"".join(parts)}</div></details>')


# ------------------------------------------------------------------------------------------------------ doctor
def doctor(cfg: C.Config, now: Optional[datetime] = None) -> List[Tuple[Optional[bool], str, str]]:
    """Read only: one line while the coach is off everywhere; per account with it on, how many ready clips are
    measured and written today."""
    try:
        now = _now(cfg, now)
        on = accounts_on(cfg)
        if not on:
            return [(None, "coach: off (it waits for factory.py social on <account> coach)", "")]
        rows: List[Tuple[Optional[bool], str, str]] = []
        today = now.date()
        for a in on:
            todo = _slots(cfg, a, (today, today + timedelta(days=1)), (B.READY,) + B.SIGNED)
            measured = written = scened = 0
            for d, s in todo:
                c = load(a, d, s["slot"])
                if not fresh(c, s):
                    continue
                measured += 1
                if isinstance(((c or {}).get("signals") or {}).get("scene_changes_3s"), int):
                    scened += 1
                w = (c or {}).get("written")
                if isinstance(w, dict) and str(w.get("at") or ""):
                    try:
                        at = datetime.fromisoformat(str(w["at"]).replace("Z", "+00:00")).astimezone(cfg.tz()).date()
                    except ValueError:
                        at = None
                    if at == today:
                        written += 1
            rows.append((True, f"coach: {a.name}: {measured} of {len(todo)} ready clips measured ({scened} with "
                               f"picture changes), {written} written today", ""))
            if not _ever_written(a):
                rows.append((None, f"coach: {a.name}: no written scorecards yet (create the clip-coach task at "
                                   f"go-live)", ""))
        if not (shutil.which("ffmpeg") or Path("/opt/homebrew/bin/ffmpeg").is_file()):
            rows.append((False, "coach: ffmpeg is not found, so picture changes are not measured",
                         "brew install ffmpeg"))
        return rows
    except Exception as e:  # noqa: BLE001  (doctor never raises)
        return [(False, f"coach: its check failed ({type(e).__name__}: {e})", "look at clipfactory/coach.py")]


def _ever_written(acct) -> bool:
    root = store.path(_slug(acct), "coach")
    if not root.is_dir():
        return False
    for d in sorted((p for p in root.iterdir() if re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)), reverse=True)[:14]:
        for f in d.glob("*.json"):
            c = _read_json(f)
            if isinstance(c, dict) and isinstance(c.get("written"), dict):
                return True
    return False


# ------------------------------------------------------------------------------------------------------ the command
def _account(cfg: C.Config, slug: str) -> C.Account:
    a = cfg.account(slug)
    raw = getattr(a, "raw", None)
    if isinstance(raw, dict) and str(raw.get("owner") or "self") == "client":
        raise SystemExit(f"{a.name} is a client's account: the coach is for the owner's own accounts")
    return a


def _slot_of(cfg: C.Config, acct: C.Account, day: date, slot: str) -> dict:
    slot = C.normal_slot(slot)
    s = ((B.read(acct, day) or {}).get("slots") or {}).get(slot)
    if not isinstance(s, dict) or not s.get("clip"):
        raise SystemExit(f"{acct.name} {day} {slot}: no clip in that slot")
    return s


def pending(cfg: C.Config, day: date, accts: Optional[Sequence[C.Account]] = None,
            now: Optional[datetime] = None) -> List[Tuple[C.Account, dict]]:
    """READY clips of the day, still ahead of their time, with no written layer for the clip in the slot."""
    now = _now(cfg, now)
    out = []
    for a in accts if accts is not None else accounts_on(cfg):
        for d, s in _slots(cfg, a, (day,), (B.READY,)):
            try:
                if datetime.fromisoformat(str(s["publish_at"])) <= now:
                    continue
            except (KeyError, TypeError, ValueError):
                pass
            c = load(a, d, s["slot"])
            if fresh(c, s) and isinstance((c or {}).get("written"), dict):
                continue
            out.append((a, s))
    return out


def card_text(cfg: C.Config, acct: C.Account, day: date, s: dict) -> List[str]:
    """What the clip-coach task reads before it judges a clip."""
    from . import copytext as T
    from . import outlets as O
    c = load(acct, day, s["slot"])
    if not fresh(c, s):
        c = measure(cfg, acct, day, s, Path(s["source_folder"]) if s.get("source_folder") else None)
    sig = c.get("signals") or {}
    m = c.get("measured") or {}
    out = [f"{acct.name} ({acct.slug}) {day} {s['slot']} {s.get('slug', '')}: {s.get('status')}",
           f"clip: {sig.get('length_s')}s, format {sig.get('format') or 'not known'}, creator "
           f"{sig.get('creator') or 'not known'}",
           "measured (0 to 100): total " + str(m.get("total")) + "; " + ", ".join(
               f"{CAT_WORDS[k]} {m.get(k) if m.get(k) is not None else 'not yet'}" for k in CATS if k != "share")
           + "; Share is yours alone"]
    sm = summarize(dict(c), playbook(acct)[0])
    out.append(f"{sm['mechanics']['words']} (the mechanics, usually fine: score the moment, the hook words and the "
               f"payoff, not these)")
    out.append(f"expected (the learner's records; worded from {SHOW_N} posts on one platform): "
               f"{sm['headline']['expected']}")
    out += [f"  {e['words']}" for e in sm["expected"]] or ["  (no records: the learner has not written features.json)"]
    out.append("flags:" + ("" if c.get("flags") else " none"))
    out += [f"  - {f.get('words')}" for f in c.get("flags") or [] if isinstance(f, dict)]
    out.append("signals:")
    for k in ("first_word_s", "wps_3s", "silence_ratio", "longest_gap_s", "longest_gap_at", "wps_all",
              "scene_changes_3s", "length_by", "length_best", "length_vs_best", "creator_record", "slot_record",
              "format_record", "length_record", "render", "grade", "title_feat"):
        if k in sig and sig[k] not in (None, {}, []):
            out.append(f"  {k}: {json.dumps(sig[k], sort_keys=True)}")
    if sig.get("missing"):
        out.append("  not measured: " + ", ".join(str(x) for x in sig["missing"]))
    out.append(f"hook on screen: {sig.get('hook_text') or '(none found)'}")
    if acct.slug == "cars":                       # Andrew 10.30: the coach judges the edit against its song
        try:
            import sys as _sys
            _repo = str(Path(__file__).resolve().parent.parent)
            if _repo not in _sys.path:
                _sys.path.insert(0, _repo)
            from garage import rhythm as _R
            out.append("the edit against its song (garage/rhythm.py, the picture cuts vs the song's hits): "
                       + _R.words(_R.measure(str((s.get("clip") or {}).get("path") or ""))))
        except Exception as e:                            # a missing file or ffmpeg trouble never stops the card
            out.append(f"the edit against its song: not measured ({type(e).__name__})")
    out.append("titles now:")
    for plat, cp in sorted((s.get("copy") or {}).items()):
        try:
            w = T.for_platform(Path(cp["path"]).read_text(), plat)
        except (OSError, KeyError, TypeError):
            continue
        first = (w["title"] if plat == "youtube_shorts" else w["caption"].strip().split("\n")[0]).strip()
        out.append(f"  {O.NAMES.get(plat, plat)}: {first}")
    job = _job(acct, s, day)
    clip_e, _ = _slate(acct, job, str(s.get("slug") or ""))
    if isinstance(clip_e.get("titles"), dict):
        out.append("slate titles: " + "; ".join(f"{k}: {v}" for k, v in sorted(clip_e["titles"].items()) if v))
    if clip_e.get("quote"):
        out.append(f"slate quote: {clip_e['quote']}")
    if acct.maker == "politics":
        ok, no = cast_names(acct, job, clip_e)
        no += [str(p.get("id")) for p in (_cast(acct, job, clip_e) or {}).get("people") or []
               if isinstance(p, dict) and not p.get("name") and p.get("id")]
        out.append("verified people (the only names a title may use): " + (", ".join(ok) or "none"))
        if no:
            out.append("NOT verified (never name them): " + ", ".join(str(x) for x in no))
    else:
        out.append("GTA has no verified cast: name only the creator on the board (" + (s.get("host") or "none") + ")")
    out.append("first 15 s of the transcript:")
    sha = str((s.get("clip") or {}).get("sha256") or "")
    inp = inputs_dir(acct, sha)
    srt = inp / "clip.srt" if (inp / "clip.srt").is_file() else _srt_of(s)
    cues = []
    if srt is not None:
        try:
            cues = parse_srt(srt.read_text(errors="replace"))
        except OSError:
            cues = []
    early = [c for c in cues if c[0] < 15.0]
    out += [f"  [{st:05.2f}] {t}" for st, _, t in early] or ["  (no transcript beside this clip)"]
    pb, pb_from = playbook(acct)
    out.append(f"playbook coach block ({pb_from}): " + (json.dumps(pb, sort_keys=True) if pb else "none"))
    f = REG.fn("ideas:patterns")
    pat = None
    if f is not None:
        try:
            pat = f(acct)
        except Exception as e:  # noqa: BLE001
            REG._log_error("ideas:patterns", e)
    out.append("ideas patterns: " + (json.dumps(pat, sort_keys=True)[:1500] if pat else "none"))
    w = c.get("written")
    if isinstance(w, dict):
        out.append(f"written already by {w.get('by')} at {w.get('at')}: {json.dumps(w.get('scores'))}")
    out.append(f"to write: factory.py coach write {acct.slug} --date {day} --slot {s['slot']} --file F --by clip-coach")
    return out


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.py coach", description="the hook coach (clipfactory/coach.py)")
    sub = ap.add_subparsers(dest="action", required=True)
    q = sub.add_parser("pending", help="READY clips without a written scorecard")
    q.add_argument("--date", default="today")
    q.add_argument("--account")
    q = sub.add_parser("card", help="what the clip-coach task reads for one clip")
    q.add_argument("account")
    q.add_argument("--date", required=True)
    q.add_argument("--slot", required=True)
    q = sub.add_parser("write", help="the written layer: five scores, one fix, three titles (a JSON file)")
    q.add_argument("account")
    q.add_argument("--date", required=True)
    q.add_argument("--slot", required=True)
    q.add_argument("--file", required=True)
    q.add_argument("--by", required=True)
    q = sub.add_parser("measure", help="measure clips from their files again (--scenes: ffmpeg too, 06:26 to 20:45)")
    q.add_argument("account", nargs="?")
    q.add_argument("--date", default="today")
    q.add_argument("--slot")
    q.add_argument("--scenes", action="store_true")
    q = sub.add_parser("show", help="the scorecards of a day")
    q.add_argument("account")
    q.add_argument("--date", default="today")
    q.add_argument("--slot")
    q.add_argument("--json", action="store_true")
    return ap


def cli(cfg: C.Config, argv: List[str]) -> None:
    """factory.py coach pending|card|write|measure|show ..."""
    a = _parser().parse_args(list(argv))
    day = C.parse_day(a.date, cfg)
    if a.action == "pending":
        if a.account and not is_on(_account(cfg, a.account)):
            print(f"coach: off for {a.account} (factory.py social on {a.account} coach)")
            return
        accts = [_account(cfg, a.account)] if a.account else accounts_on(cfg)
        if not accts:
            print("coach: off for every account (factory.py social on <account> coach)")
            return
        got = pending(cfg, day, accts)
        if not got:
            print(f"coach: nothing waiting for a written scorecard on {day}")
        for acct, s in got:
            print(f"{acct.slug} {day} {s['slot']} {s.get('slug', '')}")
        return
    if a.action == "card":
        acct = _account(cfg, a.account)
        if not is_on(acct):
            raise SystemExit(f"the coach is off for {acct.name} (factory.py social on {acct.slug} coach)")
        for ln in card_text(cfg, acct, day, _slot_of(cfg, acct, day, a.slot)):
            print(ln)
        return
    if a.action == "write":
        acct = _account(cfg, a.account)
        try:
            data = json.loads(Path(a.file).expanduser().read_text())
        except OSError as e:
            raise SystemExit(f"cannot read {a.file}: {e}")
        except ValueError as e:
            raise SystemExit(f"{a.file} is not JSON: {e}")
        print(write(cfg, acct, day, a.slot, data, a.by))
        return
    if a.action == "measure":
        accts = [_account(cfg, a.account)] if a.account else accounts_on(cfg)
        if not any(is_on(x) for x in accts):
            print("coach: off for every account (factory.py social on <account> coach)")
            return
        now = _now(cfg)
        for acct in accts:
            if not is_on(acct):
                print(f"{acct.name}: the coach is off")
                continue
            for d, s in _slots(cfg, acct, (day,), (B.READY,) + B.SIGNED):
                if a.slot and s["slot"] != C.normal_slot(a.slot):
                    continue
                folder = Path(s["source_folder"]) if s.get("source_folder") else None
                try:
                    copy_inputs(cfg, acct, d, s, folder)
                except Exception as e:  # noqa: BLE001
                    print(f"  {acct.name} {s['slot']}: inputs not copied ({type(e).__name__}: {e})")
                c = measure(cfg, acct, d, s, folder, now)
                print(f"{acct.name} {d} {s['slot']}: measured {c['measured'].get('total')}")
                if a.scenes:
                    if in_window(cfg, now):
                        print("  " + _scene_into(cfg, acct, d, s, now))
                    else:
                        print("  picture changes wait for 06:26 to 20:45")
        return
    if a.action == "show":
        acct = _account(cfg, a.account)
        root = store.path(acct.slug, "coach", day.isoformat())
        files = sorted(root.glob("*.json")) if root.is_dir() else []
        if a.slot:
            files = [f for f in files if f.stem == _hhmm(a.slot)]
        if not files:
            print(f"{acct.name} {day}: no scorecards")
            return
        pb, _ = playbook(acct)
        for f in files:
            c = _read_json(f)
            if not isinstance(c, dict):
                print(f"{f.name}: unreadable")
                continue
            if a.json:
                print(json.dumps(c, indent=1, sort_keys=True))
                continue
            m = c.get("measured") or {}
            w = c.get("written") if isinstance(c.get("written"), dict) else None
            print(f"{c.get('slot')} {c.get('slug')}: measured {m.get('total')} ("
                  + ", ".join(f"{CAT_WORDS[k]} {m.get(k)}" for k in CATS if k != "share") + ")"
                  + (f"; written by {w.get('by')}: " + ", ".join(f"{CAT_WORDS[k]} {w['scores'].get(k)}" for k in CATS)
                     if w else "; not written"))
            print("  the card says:")
            for ln in fold_lines(c, pb):
                print(f"    {ln}")
            if isinstance(c.get("used_title"), dict):
                print(f"  YouTube title from the coach: {c['used_title'].get('title')}")
        return
