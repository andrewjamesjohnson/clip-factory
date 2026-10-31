"""Why a clip came back, and never cutting that again: one-tap reason codes on Pull and Decline, the check each code
says should have caught it, and the kill list the night respects.

Since the automatic sign-off (autosign.py, live 2026-10-19 20:15) a clip nobody touched signs itself, so the owner's
Pulls and Declines are the factory's main signal of what went wrong, and until now they were free text nobody counted.
The research pass of 2026-10-19 (Frame.io, Planable, Buffer, Amazon A2I, trust-and-safety QA practice) recommended
one-tap reason codes tied to the checks, plus a kill list, so each failure tightens the checks.

THE CODES (CODES, in the order Clip Review shows them; keys 1 to 9 and 0 on a focused card):
  WRONG NAME   cast check     a name card, caption or word names the wrong person
  FRAMING      framing        a face cut off, the wrong person in shot, the picture badly placed
  CUT          cuts           a cut jumps, stutters or clips a word
  AUDIO        audio          dead air, music over the words, the levels
  LENGTH       length band    too long or too short for what it says
  REPEAT       repeat check   this moment or this picture already went out
  INCOMPLETE   whole thought  the thought starts or ends in the middle
  TEXT         words          the title, captions or words are wrong
  HOOK         hook           the opening does not hold
  RIGHTS       rights         footage we should not post
  Other        (none)         free text
The first six are HARD: the clip is wrong, not weak. Each gate is the skills' own check lines (GATES).

THE ONE LIST (the owner, 2026-10-20): verdicts.py, the learner's log of every Approve, Pull, Decline and Make again,
reads its REASONS from CODES; a Decline or a coded Pull writes its verdict row there too (signoff._verdict).

ON THE SLOT. `reason` (the newest) and `reasons` (each one, oldest first): {op, code, label, gate, hard, text, by,
at, clip, more (Make again's other chips)}, beside the note that says it in words ("declined by Andrew: HOOK (the opening is slow)"). A slot from
before the codes has only its note ("declined by Andrew: weak hook"); reasons_of() reads it as code None with that
text, so old boards read as they always did.

THE KILL LIST: state/<account>/kill-list.json. Every Decline adds an entry (Other and a Decline with no code too), and
a Pull with a code; a Pull with no reason (the way to watch a platform's cut before Approve) and Make again add
nothing. An entry holds the clip's source video and the seconds it used (the segments of a clip that joins passages,
and their span), and its b-roll's identities. What it stops:
  - the GTA night: a candidate sharing more than 15% of its seconds with a killed span of the same video is never
    picked (makers.next_pick), and a RIGHTS kill's whole video is never dealt (makers.gta_sources);
  - collect, for every delivery of both accounts and for `place` and the bench fill, which go through it: a clip from a
    killed span or video, or carrying killed b-roll, is refused like a clip outside the length band (the slot stays
    fillable, that clip is never taken), and the GTA night tries its next candidate;
  - the bench: a spare that would be refused is rejected for good (bench.went_out, the bench's own word for it);
  - the scout: a RIGHTS kill's video shows as killed on the board and is never fed.
B-roll is ENFORCED only for the codes where the picture itself can be the fault (BROLL_CODES: WRONG NAME, REPEAT,
RIGHTS); every entry still records it. A GTA film moment (the trailer's own shots, `film/<id>@<t>` in the skill's
day politics) is never killed: a few trailer shots serve many clips, and a decline for a streamer's crop says nothing
about them. A RIGHTS kill also kills the reels' source videos. The night cannot steer the skill's b-roll before a
render (the skill picks it), so a killed picture costs one render and is refused at collect.
A person approving the same clip again after a coded Pull lifts that Pull's entry: it went out after all.
`factory.py killlist <account>` lists the entries; `--lift ID --why ...` takes one back.

THE GATES' COUNT (the Stats tab). A coded Decline or Pull is a MISS of its code's gate: the gate passed that clip. A
NEEDLESS HOLD: the automatic sign-off held a clip for a check (two or more people nobody checked: the cast check; its
slot over 3 hours past: the late rule) and a person then approved it with the clip and its words unchanged
(signoff.approve writes it in the slot's `gate_holds`). Counted from this change on. The skills' own refusals never
reach a person, so their needless holds cannot be counted.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from . import board as B
from . import config as C


@dataclass(frozen=True)
class Code:
    key: str
    label: str
    gate: str
    hard: bool
    what: str


CODES = (
    Code("wrong-name", "WRONG NAME", "cast", True, "names the wrong person"),
    Code("framing", "FRAMING", "framing", True, "a face cut off, the wrong person in shot"),
    Code("cut", "CUT", "cuts", True, "a cut jumps, stutters or clips a word"),
    Code("audio", "AUDIO", "audio", True, "dead air, music over the words, the levels"),
    Code("length", "LENGTH", "length", True, "too long or too short for what it says"),
    Code("repeat", "REPEAT", "repeat", True, "this moment or picture already went out"),
    Code("incomplete", "INCOMPLETE", "whole", False, "the thought starts or ends in the middle"),
    Code("text", "TEXT", "words", False, "the title, captions or words are wrong"),
    Code("hook", "HOOK", "hook", False, "the opening does not hold"),
    Code("rights", "RIGHTS", "rights", False, "footage we should not post"),
    Code("other", "Other", "", False, "something else: say what"),
)
BY_KEY = {c.key: c for c in CODES}
HARD = tuple(c.key for c in CODES if c.hard)
BROLL_CODES = ("wrong-name", "repeat", "rights")     # the picture itself can be the fault: its b-roll is skipped too
WHOLE_VIDEO = ("rights",)                            # the footage, not the moment: the whole video is killed
# gate -> (its plain name, the check lines that run it in the skills and the factory)
GATES = {
    "cast": ("cast check", "CAST, PLATE, NAMED, SPEAKER; Casts to check"),
    "framing": ("framing", "CROPS, SAFE, HEADROOM, PIPFACE; the grade's FRAME, FACE"),
    "cuts": ("cuts", "STRADDLE, CADENCE; the grade's STUTTER, GLITCH, TAILJUMP, FREEZE"),
    "audio": ("audio", "MUSIC, SILENT, CROSSTALK; the grade's AUDIO, DEADAIR"),
    "length": ("length band", "LENGTH, BANDLEN; collect's seconds"),
    "repeat": ("repeat check", "DAY, ROTATION, REPLAY; one source a day, the 15% rule"),
    "whole": ("whole thought", "PROMISE, ENDING, ARC, CUES"),
    "words": ("words", "TEXT, COPY, BURNED, TAGS; the grade's CENSOR"),
    "hook": ("hook", "HOOK, OPENING; the grade's HOOK, OPEN, COLD"),
    "rights": ("rights", "RIGHTS, SOURCE, ORIGIN; the grade's CLAIM"),
    "late": ("late rule", "the automatic sign-off: a slot over 3 hours past"),
}
GATE_ORDER = ("cast", "framing", "cuts", "audio", "length", "repeat", "whole", "words", "hook", "rights", "late")
MAX_SHARED = 0.15        # the factory's spread rule (bench.MAX_SHARED, gta_maker max_overlap): seconds shared with a cut
SLOT_DIR = re.compile(r"^(\d{4})\s+(.+)$")


# ------------------------------------------------------------------------------------------------------ the codes
def code(key: object) -> Optional[Code]:
    """The code for a key or a label ('hook', 'HOOK', 'wrong name', 'WRONG_NAME'); None for nothing; ValueError for
    anything else."""
    raw = str(key or "").strip()
    if not raw:
        return None
    k = re.sub(r"[\s_]+", "-", raw.lower())
    c = BY_KEY.get(k) or next((c for c in CODES if c.label.lower() == raw.lower()), None)
    if c is None:
        raise ValueError(f"{raw!r} is not a reason code ({', '.join(c.label for c in CODES)})")
    return c


def words(c: Optional[Code], text: str = "") -> str:
    """'HOOK (the opening is slow)', 'HOOK', 'weak hook' (no code), '' (neither)."""
    text = (text or "").strip()
    if c is None:
        return text
    if c.key == "other":
        return text or "Other"
    return f"{c.label} ({text})" if text else c.label


def words_many(keys, text: str = "") -> str:
    """Several codes (Make again's chips) as words: 'HOOK, FRAMING (too dark)'; one code reads as words() does."""
    keys = [k for k in keys or [] if k in BY_KEY]
    if len(keys) <= 1:
        return words(BY_KEY[keys[0]] if keys else None, text)
    text = (text or "").strip()
    labels = ", ".join(BY_KEY[k].label for k in keys)
    return f"{labels} ({text})" if text else labels


def record(slot: dict, op: str, c: Optional[Code], text: str, who: str, more=()) -> dict:
    """Write the reason on the slot: `reason` (the newest) and `reasons` (the last ten, oldest first). `more`: the
    other codes picked with it (Make again's chips), kept beside it; the count and the kill list go by the first."""
    r = {"op": op, "code": c.key if c else None, "label": c.label if c else "", "gate": c.gate if c else "",
         "hard": bool(c and c.hard), "text": (text or "").strip()[:400], "by": who, "at": B.utcnow(),
         "clip": (slot.get("clip") or {}).get("sha256")}
    if more:
        r["more"] = [k for k in more if k in BY_KEY and k != r["code"]]
    if op == "pull" and (slot.get("signoff") or {}).get("auto"):
        r["auto_signed"] = True                # the factory had signed it itself: the automatic sign-off missed it too
    slot["reason"] = r
    slot["reasons"] = ([x for x in slot.get("reasons") or [] if isinstance(x, dict)] + [r])[-10:]
    return r


_LEGACY = re.compile(r"^(\S+) (declined|pulled) by ([^:;]+?)(?:: (.*?))?(?:; already out on .*)?$")


def reasons_of(slot: dict) -> List[dict]:
    """Every reason on the slot, oldest first. A slot from before the codes: read off its notes ('declined by X: why',
    'pulled by X'), with code None."""
    have = [r for r in slot.get("reasons") or [] if isinstance(r, dict)]
    if have:
        return have
    out = []
    for n in slot.get("notes") or []:
        m = _LEGACY.match(str(n))
        if not m:
            continue
        at, verb, who, text = m.groups()
        out.append({"op": "decline" if verb == "declined" else "pull", "code": None, "label": "", "gate": "",
                    "hard": False, "text": (text or "").strip(), "by": who.strip(), "at": at, "legacy": True})
    return out


# ------------------------------------------------------------------------------------------------------ the kill list
def kill_path(acct: C.Account) -> Path:
    return C.STATE / acct.slug / "kill-list.json"


def _read_json(p: Path) -> dict:
    try:
        d = json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _read(acct: C.Account) -> dict:
    d = _read_json(kill_path(acct))
    d["entries"] = [e for e in d.get("entries") or [] if isinstance(e, dict)]
    return d


@contextlib.contextmanager
def _edit(acct: C.Account) -> Iterator[dict]:
    """Lock, read, yield for changes, write back whole (a temp file, then a rename)."""
    p = kill_path(acct)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".lock"), "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        d = _read(acct)
        d.setdefault("account", acct.slug)
        yield d
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
        os.replace(tmp, p)


def entries(acct: C.Account, active: bool = True) -> List[dict]:
    es = _read(acct)["entries"]
    return [e for e in es if not e.get("lifted")] if active else es


def video_key(url: object) -> str:
    """A source's identity: its YouTube id, else the link itself."""
    u = str(url or "").strip()
    if not u:
        return ""
    from . import makers as M
    return M.video_id(u) or u.rstrip("/")


def picture_key(row: dict) -> str:
    """A b-roll picture's identity, the skills' own rule (gaming broll.picture_key): the photograph's page, else
    its file."""
    page = str((row or {}).get("page") or "").strip().lower().rstrip("/")
    src = str((row or {}).get("src") or (row or {}).get("asset") or "").strip().lower()
    return page or src


def _segments(clip: dict) -> List[List[float]]:
    out = []
    for s in (clip or {}).get("segments") or []:
        try:
            a, b = float(s[0]), float(s[1])
        except (TypeError, ValueError, IndexError):
            continue
        if b > a:
            out.append([round(a, 2), round(b, 2)])
    if not out:
        try:
            a, b = float(clip["start"]), float(clip["end"])
            if b > a:
                out.append([round(a, 2), round(b, 2)])
        except (KeyError, TypeError, ValueError):
            pass
    return out


def _sched_job(acct: C.Account, slug: str, day: str = "") -> str:
    """The job of a clip by its slug in the account's Publish/schedule.json (that day's entry first)."""
    sched = _read_json(acct.account_dir / "Publish" / "schedule.json")
    for d in ([day] if day in sched else []) + sorted((x for x in sched if x != day), reverse=True):
        slots = sched.get(d) if isinstance(sched.get(d), dict) else {}
        ent = next((e for e in slots.values() if isinstance(e, dict) and e.get("slug") == slug and e.get("job")), None)
        if ent:
            return str(ent["job"])
    return ""


def slate_clip(acct: C.Account, job: str, slug: str) -> Tuple[dict, dict]:
    """(the slate entry, the project config) of a job's clip, where the bench reads them."""
    if not job or not slug:
        return {}, {}
    from . import bench as BN
    try:
        return (BN._gta_slate if acct.maker == "gta" else BN._politics_slate)(acct, job, slug)
    except Exception:  # noqa: BLE001  (an unreadable slate: nothing to say)
        return {}, {}


def gta_broll(acct: C.Account, slug: str, day: str = "") -> Tuple[List[str], List[str], int]:
    """(identities, reel source videos, film moments left out) of a GTA clip's b-roll, from the skill's day politicss
    (Publish/broll-used-<posting day>.json, written by its renders; every day's politics when that day's says nothing)."""
    pub = acct.account_dir / "Publish"
    files = [pub / f"broll-used-{day}.json"] if day else []

    def scan(fs) -> Tuple[List[str], List[str], int]:
        ids: List[str] = []
        srcs: List[str] = []
        film = 0
        for p in fs:
            for e in _read_json(p).get("entries") or []:
                if not isinstance(e, dict) or e.get("slug") != slug:
                    continue
                key, src = str(e.get("key") or ""), str(e.get("source") or "")
                if e.get("kind") == "film" or key.startswith("film/"):
                    film += 1                  # the trailer's own shots: never killed
                    continue
                for v in (f"key:{key}" if key else "", f"pic:{e['picture']}" if e.get("picture") else ""):
                    if v and v not in ids:
                        ids.append(v)
                if src and f"src:{src}" not in srcs:
                    srcs.append(f"src:{src}")
        return ids, srcs, film

    got = scan(files)
    if not any(got[:2]) and not got[2]:
        got = scan(sorted(pub.glob("broll-used-*.json")))
    return got


def _politics_broll(clip: dict) -> List[str]:
    out = []
    for b in (clip or {}).get("broll") or []:
        if not isinstance(b, dict) or b.get("approved") is False:
            continue
        k = picture_key(b)
        if k and f"pic:{k}" not in out:
            out.append(f"pic:{k}")
    return out


def _identity(acct: C.Account, job: str, slug: str, day: str, url: str = "",
              segs: Optional[List[List[float]]] = None) -> dict:
    clip, proj = slate_clip(acct, job, slug)
    url = url or str(proj.get("source_url") or "")
    segs = segs or _segments(clip)
    if acct.maker == "gta":
        broll, srcs, film = gta_broll(acct, slug, day)
        from . import bench as BN
        for r in BN._slate_reels(clip):          # the reels the slate says it rendered with
            if f"src:{r}" not in srcs:
                srcs.append(f"src:{r}")
    else:
        broll, srcs, film = _politics_broll(clip), [], 0
    out = {"job": job, "slug": slug, "source_url": url, "video": video_key(url), "segments": segs,
           "broll": broll, "broll_src": srcs, "film_moments": film}
    if segs:
        out["span"] = [min(s[0] for s in segs), max(s[1] for s in segs)]
    return out


def slot_identity(cfg: C.Config, acct: C.Account, day: date, s: dict) -> dict:
    """What a board slot's clip is made of: its job, source video, the seconds it used and its b-roll. GTA: the
    maker's pick (pick_span, the exact candidate) first; a bench clip: the bench item's span and links; else the job's
    slate. Empty fields when nothing says."""
    from . import bench as BN
    slug = str(s.get("slug") or "")
    day_s = day.isoformat()
    bn = BN.from_bench(s)
    item = {}
    if bn:
        p = BN.root(acct) / str(bn.get("id")) / "bench.json"
        item = _read_json(p)
    job = str((s.get("cast_check") or {}).get("job") or item.get("job") or bn.get("job") or s.get("job") or "")
    folder = Path(str(s.get("source_folder") or ""))
    if not job and s.get("source_folder") and folder.is_dir():
        return folder_identity(acct, folder, day_s, s)
    job = job or _sched_job(acct, slug, day_s)
    url = str(s.get("source_url") or item.get("source_url") or "")
    segs: List[List[float]] = []
    if acct.maker == "gta" and isinstance(s.get("pick_span"), (list, tuple)) and not bn:
        segs = _segments({"segments": [s["pick_span"]]})
    elif item.get("span"):
        segs = _segments({"segments": [item["span"]]})
    out = _identity(acct, job, slug, day_s, url, segs)
    if bn and item.get("gta", {}).get("reel_sources"):
        out["broll_src"] = sorted(set(out["broll_src"]) | {f"src:{r}" for r in item["gta"]["reel_sources"]})
    return out


def folder_identity(acct: C.Account, folder: Path, day: str, slot: Optional[dict] = None) -> dict:
    """What a delivered `HHMM slug` folder's clip is made of (collect's view, before it is taken)."""
    m = SLOT_DIR.match(folder.name)
    slug = m.group(2) if m else ""
    if not slug:
        return {}
    from . import collect as K
    if acct.maker == "gta":
        prov = K.gta_provenance(acct, folder, day)
        job, url = str(prov.get("job") or ""), str(prov.get("source_url") or "")
        segs: List[List[float]] = []
        s = slot or {}
        if job and s.get("job") == job and isinstance(s.get("pick_span"), (list, tuple)):
            segs = _segments({"segments": [s["pick_span"]]})      # the maker's pick, written just before it rendered
        return _identity(acct, job, slug, day, url, segs)
    job = str(_read_json(folder / "cast-check.json").get("job") or "") or _sched_job(acct, slug, day)
    return _identity(acct, job, slug, day)


def _union_inside(segs: List[List[float]], killed: List[List[float]]) -> float:
    """The share of segs' seconds that lie inside the killed segments."""
    total = sum(b - a for a, b in segs)
    if total <= 0:
        return 0.0
    inside = 0.0
    for a, b in segs:
        cuts = sorted((max(a, ka), min(b, kb)) for ka, kb in killed if min(b, kb) > max(a, ka))
        end = a
        for x, y in cuts:
            x = max(x, end)
            if y > x:
                inside += y - x
                end = y
    return inside / total


def says(e: dict) -> str:
    """'HOOK, declined 10.19 07:00 by Andrew'."""
    what = words(BY_KEY.get(str(e.get("code") or "")), "") or "no code"
    try:
        d = date.fromisoformat(str(e.get("date")))
        when = f"{d.month:02d}.{d.day:02d} {e.get('slot', '')}".strip()
    except ValueError:
        when = str(e.get("slot") or "")
    return f"{what}, {'pulled' if e.get('op') == 'pull' else 'declined'} {when} by {e.get('by', '?')}"


def enforced_broll(e: dict) -> set:
    if not e.get("broll_enforced"):
        return set()
    return set(e.get("broll") or []) | (set(e.get("broll_src") or []) if e.get("whole_video") else set())


def hit(acct: C.Account, ident: dict, kills: Optional[List[dict]] = None) -> Tuple[Optional[dict], str]:
    """(the kill entry this clip runs into, why in words), or (None, '')."""
    if not ident:
        return None, ""
    kills = entries(acct) if kills is None else kills
    vids = {v for v in [ident.get("video")] + list(ident.get("videos") or []) if v}
    mine = set(ident.get("broll") or []) | set(ident.get("broll_src") or [])
    for e in kills:
        vid = e.get("video")
        if vid and vid in vids:
            if e.get("whole_video"):
                return e, f"its source video {vid} is on the kill list ({says(e)})"
            segs, dead = ident.get("segments") or [], e.get("segments") or []
            if segs and dead:
                share = _union_inside(segs, dead)
                if share > MAX_SHARED:
                    return e, (f"{share:.0%} of its seconds are a killed span of {vid} "
                               f"({dead[0][0]:.0f}-{dead[-1][1]:.0f}s; {says(e)})")
        same = enforced_broll(e) & mine
        if same:
            return e, f"its b-roll {sorted(same)[0].split(':', 1)[-1]} is on the kill list ({says(e)})"
    return None, ""


def bench_identity(acct: C.Account, slug: str, job: str, span, keys) -> dict:
    """A bench spare as the kill list sees it: its source links (every one a video), its span and its b-roll."""
    segs = _segments({"segments": [span]}) if span else []
    ident = _identity(acct, job, slug, "", "", segs)
    ident["videos"] = sorted({video_key(k) for k in keys or () if k})
    return ident


def span_killed(acct: C.Account, video: str, span: Tuple[float, float],
                kills: Optional[List[dict]] = None) -> Tuple[Optional[dict], str]:
    """The GTA night's pick: a candidate's span of a video against the kill list (no b-roll yet: not rendered)."""
    if not video:
        return None, ""
    return hit(acct, {"video": video, "segments": [[float(span[0]), float(span[1])]]}, kills)


def killed_videos(acct: C.Account, kills: Optional[List[dict]] = None) -> Dict[str, dict]:
    """video -> the entry that killed the whole video (RIGHTS)."""
    kills = entries(acct) if kills is None else kills
    return {str(e["video"]): e for e in kills if e.get("whole_video") and e.get("video")}


def kill(cfg: C.Config, acct: C.Account, day: date, s: dict, r: dict) -> str:
    """Put a declined or pulled slot's clip on the account's kill list. Returns the line to say ('' when nothing was
    added). Never raises: a kill list that cannot be read or written is said, not fatal."""
    try:
        ident = slot_identity(cfg, acct, day, s)
    except Exception as e:  # noqa: BLE001  (a broken slate or folder: the entry says what it could not read)
        ident = {"slug": s.get("slug"), "error": f"{type(e).__name__}: {str(e)[:200]}"}
    c = BY_KEY.get(str(r.get("code") or ""))
    entry = {"at": r.get("at") or B.utcnow(), "by": r.get("by"), "op": r.get("op"),
             "code": c.key if c else None, "label": c.label if c else "", "gate": c.gate if c else "",
             "hard": bool(c and c.hard), "text": r.get("text", ""), "date": day.isoformat(), "slot": s.get("slot"),
             "slug": s.get("slug"), "job": ident.get("job", ""), "clip": (s.get("clip") or {}).get("sha256"),
             "video": ident.get("video", ""), "source_url": ident.get("source_url", ""),
             "segments": ident.get("segments") or [], "span": ident.get("span"),
             "whole_video": bool(c and c.key in WHOLE_VIDEO and ident.get("video")),
             "broll": ident.get("broll") or [], "broll_src": ident.get("broll_src") or [],
             "film_moments": ident.get("film_moments", 0), "broll_enforced": bool(c and c.key in BROLL_CODES),
             "lifted": None}
    if ident.get("error"):
        entry["error"] = ident["error"]
    try:
        with _edit(acct) as d:
            n = sum(1 for e in d["entries"] if e.get("date") == entry["date"] and e.get("slot") == entry["slot"])
            entry["id"] = f"{day.strftime('%Y%m%d')}-{str(s.get('slot', '')).replace(':', '')}" + (f"-{n + 1}" if n else "")
            d["entries"].append(entry)
    except OSError as e:
        return f"NOT on the kill list ({e}): the night may cut it again"
    parts = []
    if entry["whole_video"]:
        parts.append(f"its whole source video {entry['video']}")
    elif entry["video"] and entry["segments"]:
        parts.append(f"{entry['video']} {entry['span'][0]:.0f}-{entry['span'][1]:.0f}s")
    if entry["broll_enforced"] and (entry["broll"] or (entry["whole_video"] and entry["broll_src"])):
        parts.append(f"{len(enforced_broll(entry))} b-roll picture(s)")
    if not parts:
        return f"on the kill list as {entry['id']}, but nothing to skip: its source span could not be read"
    return f"on the kill list ({entry['id']}): the night and the bench skip {' and '.join(parts)}"


def lift_slot(acct: C.Account, day: date, slot: str, clip_sha: Optional[str], who: str) -> List[str]:
    """A person approved this slot's clip again: the coded Pulls of that same clip are lifted (it went out after all).
    Returns the ids lifted."""
    if not kill_path(acct).exists():
        return []
    lifted = []
    with _edit(acct) as d:
        for e in d["entries"]:
            if (not e.get("lifted") and e.get("op") == "pull" and e.get("date") == day.isoformat()
                    and e.get("slot") == slot and clip_sha and e.get("clip") == clip_sha):
                e["lifted"] = {"by": who, "at": B.utcnow(), "why": "approved again: it went out after all"}
                lifted.append(str(e.get("id")))
    return lifted


def lift(cfg: C.Config, acct: C.Account, ident: str, why: str, who: str = "") -> List[str]:
    """`factory.py killlist <account> --lift ID --why ...`: take an entry back (kept, marked lifted)."""
    if not why.strip():
        raise SystemExit("say why with --why")
    with _edit(acct) as d:
        e = next((e for e in d["entries"] if e.get("id") == ident), None)
        if e is None:
            raise SystemExit(f"{ident!r} is not on {C.possessive(acct.name)} kill list")
        if e.get("lifted"):
            return [f"  {ident}: already lifted ({e['lifted'].get('why', '')})"]
        e["lifted"] = {"by": who or "the command line", "at": B.utcnow(), "why": why.strip()[:300]}
    return [f"  {ident}: lifted; the night and the bench may use it again"]


def show(cfg: C.Config, acct: C.Account, all_: bool = False) -> List[str]:
    es = entries(acct, active=not all_)
    if not es:
        return [f"{acct.name}: the kill list is empty" + ("" if all_ else " (--all shows lifted entries)")]
    out = [f"{acct.name}: {len(es)} on the kill list ({kill_path(acct)})"]
    for e in es:
        what = (f"whole video {e['video']}" if e.get("whole_video") else
                f"{e['video']} {e['span'][0]:.0f}-{e['span'][1]:.0f}s" if e.get("video") and e.get("span") else
                "no source span")
        br = enforced_broll(e)
        out.append(f"  {e.get('id')}  {says(e)}  {e.get('slug', '')}: {what}"
                   + (f"; b-roll skipped: {len(br)}" if br else
                      (f"; b-roll recorded, not skipped ({len(e.get('broll') or [])})" if e.get("broll") else ""))
                   + (f"  LIFTED: {e['lifted'].get('why')}" if e.get("lifted") else ""))
    return out


def check(cfg: C.Config, acct: C.Account, url: str, span: str = "", day: Optional[date] = None) -> List[str]:
    """`factory.py killlist <account> --check URL [--span A-B[,C-D]] [--date D]`: may the night cut these seconds of
    this video? For the Politics's run, before it slates a clip: the kill list, then (with --span) a repeat (repeat())."""
    segs = []
    for part in [p for p in span.split(",") if p.strip()]:
        try:
            a, b = part.split("-", 1)
            segs.append([float(a), float(b)])
        except ValueError:
            raise SystemExit(f"--span {part!r}: seconds as A-B (several as A-B,C-D)") from None
    vid = video_key(url)
    whole = killed_videos(acct).get(vid)
    if whole:
        return [f"  NO: {vid} is on the kill list as a whole video ({says(whole)})"]
    if not segs:
        dead = [e for e in entries(acct) if e.get("video") == vid and e.get("segments")]
        if not dead:
            return [f"  ok: nothing of {vid} is on the kill list"]
        return [f"  {vid}: these seconds are killed (more than 15% of a clip inside one is refused):"] + [
            f"    {', '.join(f'{a:.0f}-{b:.0f}' for a, b in e['segments'])}  ({says(e)})" for e in dead]
    e, why = hit(acct, {"video": vid, "segments": segs})
    if e:
        return [f"  NO: {why}"]
    rep = repeat(cfg, acct, url, segs, day)
    if rep:
        return [f"  NO: a repeat: {rep}"]
    return [f"  ok: {vid} {span} is not on the kill list, and no board, post or spare has these seconds"]


def repeat(cfg: C.Config, acct: C.Account, url: str, segs: List[List[float]], day: Optional[date] = None) -> str:
    """Why these seconds of this video are a repeat ('' when not): the bench's went_out checks (more than 15% of them
    on any board of the account, any day, on a post or a spare), and the same video already on the posting day's
    board (one source once a day). At pick time, before the slate: 10-25 the night picked moments posted on 10-22 and
    10-23 and found out only after the render (3 slots lost)."""
    from . import bench as BN
    day = day or cfg.now().date()
    keys = BN._keys([url])
    span = (min(a for a, _ in segs), max(b for _, b in segs))
    hist = BN.history(cfg, acct)
    why = BN.went_out(cfg, acct, "this pick", "", span, keys, day, hist)
    if why:
        return why
    bd = B.read(acct, day) or {}
    for k, sl in sorted((bd.get("slots") or {}).items()):
        if isinstance(sl, dict) and sl.get("slug") and keys & BN._slot_sources(acct, sl, hist["sched"], day.isoformat()):
            return f"the same video is already on {day} {k} ({sl['slug']}): one source once a day"
    return ""


# ------------------------------------------------------------------------------------------------------ the gates' count
def held_by(cfg: C.Config, acct: C.Account, s: dict, now: datetime) -> Optional[dict]:
    """The check the automatic sign-off is holding this ready clip for ({gate, why}), None when it is not held, or
    held by a person's own call (a Pull, a Not them) or by nothing a gate decides."""
    from . import autosign as A
    st = A.settings(cfg, acct)
    if not st["on"] or s.get("status") != B.READY or not s.get("clip"):
        return None
    at = A.slot_at(s)
    if at is not None and now - at > timedelta(hours=st["late_hours"]):
        return {"gate": "late", "why": f"its slot passed more than {st['late_hours']:g} hours ago"}
    if A.held_by_pull(s):
        return None
    from . import signoff as S
    if S._said_not_them(s, cfg):
        return None
    waits = A.cast_waits(cfg, s, st)
    return {"gate": "cast", "why": waits} if waits else None


def _week(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def _local_day(cfg: C.Config, iso: str, fallback: str) -> str:
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (t.astimezone(cfg.tz()) if t.tzinfo else t).date().isoformat()
    except (TypeError, ValueError):
        return fallback


def stat_rows(cfg: C.Config) -> dict:
    """For the Stats tab: {"codes": [...], "gates": [...], "reasons": rows, "holds": rows}. One reason per clip per
    slot (a Pull then a Decline of the same clip counts once, the newest); a Pull with no reason is not counted. Each
    row's week is the Monday of the local day it was given."""
    rows, holds = [], []
    for acct in cfg.accounts.values():
        for p in B.all_boards(acct):
            bd = _read_json(p)
            for k, s in sorted((bd.get("slots") or {}).items()):
                if not isinstance(s, dict):
                    continue
                latest: Dict[str, dict] = {}
                for r in reasons_of(s):
                    if r.get("op") == "pull" and not r.get("code"):
                        continue               # a Pull with no reason: watching a cut, not a verdict
                    latest[str(r.get("clip") or r.get("at"))] = r
                for r in latest.values():
                    day = _local_day(cfg, r.get("at"), p.stem)
                    rows.append({"account": acct.slug, "board": p.stem, "slot": k, "day": day,
                                 "week": _week(date.fromisoformat(day)), "op": r.get("op"), "code": r.get("code"),
                                 "gate": r.get("gate") or "", "hard": bool(r.get("hard")),
                                 "auto": bool(r.get("auto_signed")), "legacy": bool(r.get("legacy"))})
                for h in s.get("gate_holds") or []:
                    if not isinstance(h, dict):
                        continue
                    day = _local_day(cfg, h.get("at"), p.stem)
                    holds.append({"account": acct.slug, "board": p.stem, "slot": k, "day": day,
                                  "week": _week(date.fromisoformat(day)), "gate": h.get("gate"),
                                  "unchanged": bool(h.get("unchanged"))})
    return {"codes": [{"key": c.key, "label": c.label, "gate": c.gate, "hard": c.hard, "what": c.what} for c in CODES],
            "gates": [{"key": g, "name": GATES[g][0], "lines": GATES[g][1]} for g in GATE_ORDER],
            "reasons": rows, "holds": holds}


def doctor_lines(cfg: C.Config) -> List[tuple]:
    """(good or None, text, fix) per account for factory.py doctor: what its kill list holds, or that it cannot be
    read (then nothing is skipped: the night may cut a killed span again)."""
    out = []
    for acct in cfg.accounts.values():
        p = kill_path(acct)
        if not p.exists():
            out.append((None, f"{acct.name}: kill list empty (a Decline, or a Pull with a reason, adds to it)", ""))
            continue
        try:
            raw = json.loads(p.read_text())
            es = [e for e in (raw.get("entries") or []) if isinstance(e, dict) and not e.get("lifted")]
        except (OSError, ValueError, AttributeError) as e:
            out.append((False, f"{acct.name}: kill list unreadable ({type(e).__name__}): nothing is skipped",
                        f"fix or move {p}; factory.py killlist {acct.slug} --all reads it"))
            continue
        spans = sum(1 for e in es if e.get("video") and e.get("segments") and not e.get("whole_video"))
        whole = sum(1 for e in es if e.get("whole_video"))
        pics = sum(len(enforced_broll(e)) for e in es)
        blind = sum(1 for e in es if not e.get("video") and not enforced_broll(e))
        out.append((None, f"{acct.name}: kill list {len(es)} ({spans} span(s), {whole} whole video(s), {pics} b-roll "
                          f"picture(s) skipped" + (f"; {blind} with nothing to skip" if blind else "") + ")", ""))
    return out
