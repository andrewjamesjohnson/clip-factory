"""THE DAILY 8 BOARD (2026-10-28, step 2 of the Daily 8 plan, the scout upgrade; Andrew: "Start step 2"; the plan:
Desktop/CLIP FACTORY/Daily 8 Plan 10.28). The scout's board, turned into tomorrow's eight slots, each lane with a pick
and a backup, and what the night needs to know before it cuts:

    the lanes   06:30 fight 1 · 07:50 fight 2 · 09:50 news 1 · 11:50 sit-down · 13:35 news 2 · 15:55 hearing ·
                18:20 flashback · 20:40 outburst; today's finds first, THE VAULT where a lane is about history
    news level  normal, big or huge, from how many outlets carry the day's top story against the next one: a big day
                gives 20:40 and then 18:20 to that story, a huge day 15:55 and 11:50 too; the fights never flip
    part 2      the last three days' hits (YouTube 2,500+, Instagram 1,000+ or Facebook 5,000+) and how much of their
                video is left; the best one not sequelled yet takes 07:50 as PART 2 (a Part 2 that hits, PART 3; never
                a Part 4) and that day's FIGHT 2 moves to 20:40, in the outburst's place (step 4, 2026-10-29)
    moments     each prepped job read from its own transcript (no YouTube call): how long the fight runs (3+ minutes
                of back and forth = ROUNDS), the dodges (the same question asked again), the paper (their own words,
                an email, a tweet or a number read out to them = THE PAGE)

Read only on the factory: it writes the board (Markdown for Andrew and the night, JSON beside it) and nothing else.

    python3 ~/clip-factory/factory.py daily8 politics [--date YYYY-MM-DD] [--out DIR]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config as C
from . import scout as S

LANES = (
    ("06:30", "fight1", "FIGHT 1", "the hottest two-person clash (THE FACE-OFF when both faces are on screen)"),
    ("07:50", "fight2", "FIGHT 2", "the next hottest clash, in another look than 06:30"),
    ("09:50", "news1", "NEWS 1", "today's gaggle or briefing: Trump or the White House with reporters"),
    ("11:50", "sitdown", "SIT-DOWN", "a TV interview, Sunday show or podcast: someone pressed who won't answer"),
    ("13:35", "news2", "NEWS 2", "the story every network is running today"),
    ("15:55", "hearing", "HEARING", "THE ROOM: a witness pressed (today's, else THE VAULT's)"),
    ("18:20", "flashback", "FLASHBACK", "a fight from history out of THE VAULT, its outcome on the record"),
    ("20:40", "outburst", "OUTBURST", "a shout breaks in (today's, else THE VAULT's)"),
)
FLIP_BIG, FLIP_HUGE = ("outburst", "flashback"), ("hearing", "sitdown")   # the order slots go to the day's story
PICK_ORDER = ("fight1", "fight2", "news2", "news1", "sitdown", "hearing", "outburst", "flashback")
BIG, HUGE, AHEAD = 8, 12, 1.5          # outlets on the top story, and how far ahead of the next one it must be
HIT = {"youtube": 2500, "instagram": 1000, "facebook": 5000}            # a hit in its first days (the plan, 10.28)
HIT_DAYS = 3
FIGHT_LOOKS = {"faceoff", "outburst"}
NEWS_FORMATS = {"gaggle", "briefing", "spray", "street"}
SIT_FORMATS = {"interview", "podcast", "townhall", "debate"}
VAULT_LANE = {"hearing": "HEARINGS", "sitdown": "SIT-DOWN", "flashback": None, "outburst": None}   # None: any


# ------------------------------------------------------------------------------------------------------ the finds
def _engine_formats(acct):
    try:
        sys.path.insert(0, str(Path(acct.skill_dir) / "scripts"))
        import formats as F                    # the engine's own words for each format (presets/formats.json)
        return F
    except Exception:                          # noqa: BLE001  (the board still lanes by heat and looks)
        return None


def fmt_of(f: dict, F) -> str:
    """The format a find's title, channel and description say (the engine's detector), '' when none does."""
    if F is None:
        return ""
    try:
        sc, _why = F.text_scores({"title": f.get("title"), "channel": f.get("channel"), "description": f.get("desc")})
    except Exception:                          # noqa: BLE001
        return ""
    best = max(sc.items(), key=lambda kv: kv[1], default=("", 0.0))
    return best[0] if best[1] >= 2.0 else ""


def is_fight(f: dict) -> bool:
    return (f.get("heat") or 0) >= S.FIGHT or bool(FIGHT_LOOKS & set(f.get("looks") or []))


def candidates(board: dict, F) -> List[dict]:
    """Today's videos worth a slot, best first, each with its format: ones the scout says to cut as they are (lane
    'cut') from a channel whose rights it clears (S.CLEARED). A reupload (the scout's 'find original': late-night shows,
    commentary channels, other countries' feeds) never takes a slot; the scout's own report finds its original."""
    out = []
    for f in (board.get("finds") or {}).values():
        if f.get("kind") != "video" or f.get("status") == "killed" or f.get("derivative") or f.get("short"):
            continue
        if f.get("lane") != "cut" or f.get("rights") not in S.CLEARED:
            continue
        given = f.get("format")                   # the scout's own guess, a name or a list of them
        given = (given[0] if given else "") if isinstance(given, list) else str(given or "")
        out.append({**f, "fmt": given or fmt_of(f, F)})
    return sorted(out, key=lambda f: -(f.get("score") or 0))


# ------------------------------------------------------------------------------------------------------ news level
def stories(cands: List[dict]) -> List[dict]:
    """The day's stories by outlets: [{story, outlets, finds}], most outlets first."""
    by: Dict[int, dict] = {}
    for f in cands:
        s = by.setdefault(f.get("story"), {"story": f.get("story"), "outlets": 0, "finds": []})
        s["outlets"] = max(s["outlets"], f.get("story_outlets") or 0)
        s["finds"].append(f)
    return sorted(by.values(), key=lambda s: (-s["outlets"], -max(x.get("score") or 0 for x in s["finds"])))


def news_level(sts: List[dict]) -> Tuple[str, str]:
    """('normal' | 'big' | 'huge', why): big when the top story is on BIG+ outlets and AHEAD times the next one's,
    huge at HUGE+."""
    if not sts:
        return "normal", "no stories on the board"
    top = sts[0]["outlets"]
    nxt = sts[1]["outlets"] if len(sts) > 1 else 0
    title = sts[0]["finds"][0]["title"]
    why = f"the top story is on {top} outlets (the next on {nxt}): {title[:90]}"
    if top >= HUGE and top >= AHEAD * max(1, nxt):
        return "huge", why
    if top >= BIG and top >= AHEAD * max(1, nxt):
        return "big", why
    return "normal", why


# ------------------------------------------------------------------------------------------------------ the lanes
MIN_HEAT, SIT_HEAT = 0.25, 0.3           # a lane that is not news needs a clash in its words


ROUNDUP = re.compile(r"\bupdate\b|\bepisode\b|\bep\.? ?\d|\bfull (show|episode|event)\b|^\W*(watch )?live\b", re.I)


def roundup(title: str) -> bool:
    """A show of many stories (a morning update, a full episode, a live stream, three topics in a row): its word
    'fight' is a headline's, not a clash on camera."""
    return bool(ROUNDUP.search(title or "")) or (title or "").count(",") >= 2


def _fit(lane: str, f: dict) -> bool:
    fm, heat = f.get("fmt") or "", f.get("heat") or 0
    if lane in ("fight1", "fight2", "sitdown", "outburst") and roundup(f.get("title")):
        return False
    if lane in ("fight1", "fight2"):
        return is_fight(f)
    if lane == "news1":
        return fm in NEWS_FORMATS
    if lane == "sitdown":
        return fm in SIT_FORMATS and heat >= SIT_HEAT
    if lane == "hearing":
        return fm == "hearing" and heat >= MIN_HEAT
    if lane == "outburst":
        return "outburst" in (f.get("looks") or []) and heat >= MIN_HEAT
    return False


def _two(f: dict) -> int:
    """1 for a clash between two people (two named, or two faces on screen), else 0: the fight lanes take those first
    (one person sounding off is not a fight)."""
    return 1 if len(f.get("people") or []) >= 2 or "faceoff" in (f.get("looks") or []) else 0


def _vault_for(lane: str, rows: List[dict], names: set) -> List[dict]:
    want = VAULT_LANE.get(lane, "")
    if want == "":
        return []
    out = [r for r in rows if (want is None or r.get("section", "").startswith(want))
           and (lane != "outburst" or "outburst" in (r.get("looks") or []))]
    # FLASHBACK runs before OUTBURST and takes any row: it leaves the shouting ones for OUTBURST
    shout = (lambda r: 1 if "outburst" in (r.get("looks") or []) else 0) if lane == "flashback" else (lambda r: 0)
    return sorted(out, key=lambda r: (shout(r), -(1 if names & set(r.get("names") or []) else 0), -r.get("heat_n", 0)))


def _pick(f: dict) -> dict:
    return {"from": "today", "title": f["title"], "url": f["url"], "channel": f.get("channel"), "fmt": f.get("fmt"),
            "heat": f.get("heat"), "score": f.get("score"), "looks": f.get("looks") or [], "rights": f.get("rights"),
            "people": f.get("people") or [], "views": f.get("views"), "id": f.get("id")}


def _vpick(r: dict) -> dict:
    return {"from": "vault", "title": f"{r.get('year', '')[:4]}: {r.get('moment', '')}", "url": r.get("url"),
            "channel": r.get("channel"), "fmt": r.get("format"), "heat": r.get("heat_n"), "looks": r.get("looks") or [],
            "id": r.get("id"), "line": r.get("the line"), "people": r.get("names") or []}


def lanes(cands: List[dict], sts: List[dict], vault: List[dict], level: str) -> List[dict]:
    """The eight slots, each with a pick and a backup (None when nothing fits): today's finds that fit the lane, best
    first; a lane about history (or short of news) from THE VAULT; FIGHT 2 in another format than FIGHT 1 when it can
    be; on a big or huge day the flipped slots take the day's top story (then the next stories). The lanes choose in
    PICK_ORDER (NEWS 2 before NEWS 1, OUTBURST before FLASHBACK, which takes any vault row) and print in slot order."""
    used: set = set()
    names = {p for s in sts[:12] for f in s["finds"] for p in f.get("people") or []}
    flipped = set(FLIP_BIG if level in ("big", "huge") else ()) | set(FLIP_HUGE if level == "huge" else ())
    story_q = [f for s in sts for f in sorted(s["finds"], key=lambda x: -(x.get("score") or 0))]
    out: List[dict] = []
    spec = {lane: (hhmm, lane, name, what) for hhmm, lane, name, what in LANES}
    for lane in PICK_ORDER:
        hhmm, lane, name, what = spec[lane]
        picks: List[dict] = []
        if lane in flipped or lane == "news2":
            pool = [f for f in story_q if f["id"] not in used]
            picks = [_pick(f) for f in pool[:2]]
            name, what = (("NEWS (big day)", "the day's top story") if lane in flipped else (name, what))
        elif lane == "flashback":
            picks = [_vpick(r) for r in _vault_for(lane, vault, names) if r["id"] not in used][:2]
        else:
            pool = [f for f in cands if f["id"] not in used and _fit(lane, f)]
            if lane in ("fight1", "fight2"):
                pool.sort(key=lambda f: (-_two(f), -(f.get("score") or 0)))
            elif lane == "news1":                          # reporters at Trump or the White House first
                pool.sort(key=lambda f: (-(f.get("fmt") in ("gaggle", "spray")), -(f.get("score") or 0)))
            elif lane == "sitdown":                        # the hottest exchange, not the biggest channel
                pool.sort(key=lambda f: (-(f.get("heat") or 0), -(f.get("score") or 0)))
            if lane == "news1" and sts:                    # NEWS 2 has the top story: NEWS 1 is another one
                pool = [f for f in pool if f.get("story") != sts[0]["story"]] or pool
            if lane == "fight2" and out and out[0]["pick"]:
                other = [f for f in pool if f.get("fmt") != out[0]["pick"].get("fmt")]
                pool = other + [f for f in pool if f not in other]
            picks = [_pick(f) for f in pool[:2]]
            if len(picks) < 2:
                picks += [_vpick(r) for r in _vault_for(lane, vault, names) if r["id"] not in used][:2 - len(picks)]
        for p in picks:
            used.add(p["id"])
        out.append({"slot": hhmm, "lane": lane, "name": name, "what": what, "flipped": lane in flipped,
                    "pick": picks[0] if picks else None, "backup": picks[1] if len(picks) > 1 else None})
    return sorted(out, key=lambda ln: ln["slot"])


# ------------------------------------------------------------------------------------------------------ part 2
def _media_seconds(p: Path) -> Optional[float]:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                           capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _read(p: Path) -> str:
    try:
        return p.read_text()
    except OSError:
        return ""


def _source_of(acct, slug: str, day: str) -> dict:
    """Where a posted clip came from: its job, the video, how long the video is and where in it the clip sits."""
    jobs = Path.home() / "Desktop" / "Clipping" / "Jobs"
    try:
        bd = json.loads((C.STATE / acct.slug / f"{day}.json").read_text())
        slot = next(s for s in bd["slots"].values() if s.get("slug") == slug)
        cc = Path(slot["clip"]["path"]).parent / "cast-check.json"
        job = json.loads(cc.read_text())["job"] if cc.is_file() else next(
            d.name for d in jobs.iterdir() if f'"slug": "{slug}"' in _read(d / "slate.json"))
        jd = jobs / job
        proj = json.loads((jd / "project.json").read_text())
        clip = next(c for c in json.loads((jd / "slate.json").read_text())["clips"] if c.get("slug") == slug)
    except (OSError, ValueError, KeyError, StopIteration, TypeError):
        return {}
    spans = [s for s in clip.get("segments") or [] if isinstance(s, (list, tuple)) and len(s) >= 2]
    if not spans and clip.get("start") is not None and clip.get("end") is not None:     # one span, the older way
        spans = [(float(clip["start"]), float(clip["end"]))]
    total = _media_seconds(Path(proj.get("source") or ""))
    used = (min(s[0] for s in spans), max(s[1] for s in spans)) if spans else None
    return {"job": job, "url": proj.get("source_url"), "video": proj.get("source_title"), "minutes": total and
            round(total / 60, 1), "used": used, "left_after": total and used and round((total - used[1]) / 60, 1),
            "left_before": used and round(used[0] / 60, 1), "work": str(Path(proj.get("source") or "").parent)}


def hits(acct, now: Optional[datetime] = None) -> List[dict]:
    """The last HIT_DAYS days' clips over the bar on any platform, biggest first, each with its source."""
    now = now or datetime.now(timezone.utc)
    try:
        posts = json.loads((C.STATE / "stats" / "posts.json").read_text())["posts"]
    except (OSError, ValueError, KeyError):
        return []
    by: Dict[str, dict] = {}
    for p in posts:
        if p.get("account") != acct.slug or not p.get("clip"):
            continue
        try:
            at = datetime.fromisoformat(str(p["at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if now - at > timedelta(days=HIT_DAYS):
            continue
        h = by.setdefault(p["clip"], {"clip": p["clip"], "title": p.get("title"), "views": {}, "at": at})
        h["views"][p["platform"]] = max(h["views"].get(p["platform"], 0), int(p.get("views") or 0))
        h["at"] = min(h["at"], at)
    out = []
    for h in by.values():
        if any(h["views"].get(k, 0) >= v for k, v in HIT.items()):
            day = h["at"].astimezone().date().isoformat()
            out.append({**h, "at": h["at"].isoformat(), "day": day, "source": _source_of(acct, h["clip"], day)})
    return sorted(out, key=lambda h: -sum(h["views"].values()))


# ------------------------------------------------------------------------------------------------------ the sequel
# THE SEQUEL (Daily 8 step 4, 2026-10-29; the plan Andrew took 10.28): "Part 2 takes the 07:50 slot. That day's second
# fight moves to 20:40. Part 2 comes from: 1) the rest of the same video 2) what happened next 3) that person's next
# fight. Part 3 only if Part 2 is a hit too." Every part works on its own (it names who's who in its first line and
# opens on its own punch line), keeps the title with "| Part 2", and its VS card says PART 2 across the top.
PART_SLOT, MOVED_TO = "07:50", "20:40"
MAX_PART = 3
MIN_LEFT = 1.0                         # minutes of the same video a Part 2 needs, else it comes from what came next


def _part_of(acct, clip: str, day: str) -> int:
    """Which part a posted clip is (its slot's "sequel" from collect, else 1)."""
    try:
        bd = json.loads((C.STATE / acct.slug / f"{day}.json").read_text())
        slot = next(s for s in bd["slots"].values() if s.get("slug") == clip)
        return int((slot.get("sequel") or {}).get("part") or 1)
    except (OSError, ValueError, StopIteration, KeyError, TypeError):
        return 1


def sequelled(acct, posting: str, back: int = HIT_DAYS + 3) -> set:
    """The clips that already have their next part on a board (the boards of the days before `posting`, and its own)."""
    out = set()
    end = date.fromisoformat(posting)
    for i in range(back + 1):
        d = (end - timedelta(days=i)).isoformat()
        try:
            bd = json.loads((C.STATE / acct.slug / f"{d}.json").read_text())
        except (OSError, ValueError):
            continue
        for s in (bd.get("slots") or {}).values():
            of = str((s.get("sequel") or {}).get("of") or "")
            if of:
                out.add(of)
    return out


def sequel_plan(acct, hit_list: List[dict], posting: str) -> Optional[dict]:
    """The one hit that takes 07:50 tomorrow as its next part, or None."""
    done = sequelled(acct, posting)
    for h in hit_list:                                   # biggest first
        if h["clip"] in done or h.get("day", "") >= posting:
            continue
        part = _part_of(acct, h["clip"], h.get("day", "")) + 1
        if part > MAX_PART:
            continue
        s = h.get("source") or {}
        left = max(float(s.get("left_after") or 0), float(s.get("left_before") or 0))
        if s.get("minutes") and left >= MIN_LEFT:
            where = f"the rest of the same video ({left:g} min left)"
        else:
            where = "what happened next (\"N DAYS LATER\"), else that person's next fight"
        title = str(h.get("title") or h["clip"]).split(" | Part ")[0]
        return {"part": part, "of": h["clip"], "title": f"{title} | Part {part}", "where": where,
                "views": h.get("views"), "source": s, "day": h.get("day")}
    return None


def apply_sequel(lanes_: List[dict], plan: Optional[dict]) -> List[dict]:
    """The board with the sequel in: 07:50 is the part, FIGHT 2 moves to 20:40 (the outburst sits out that day)."""
    if not plan:
        return lanes_
    by = {ln["slot"]: ln for ln in lanes_}
    f2, last = by.get(PART_SLOT), by.get(MOVED_TO)
    if f2 is None:
        return lanes_
    s = plan.get("source") or {}
    pick = {"from": "part", "id": plan["of"], "title": plan["title"], "channel": s.get("video") or "",
            "url": s.get("url"), "fmt": "the same look as its first part", "work": s.get("work"),
            "job": s.get("job"), "sequel": {"part": plan["part"], "of": plan["of"]}, "where": plan["where"]}
    moved = dict(f2, slot=MOVED_TO, name=f"{f2['name']} (moved for PART {plan['part']})")
    part = dict(f2, lane=f"part{plan['part']}", name=f"PART {plan['part']}",
                what=f"the next part of a hit: {plan['where']}", pick=pick, backup=f2.get("pick"), flipped=False)
    out = []
    for ln in lanes_:
        if ln["slot"] == PART_SLOT:
            out.append(part)
        elif ln["slot"] == MOVED_TO and last is not None:
            out.append(moved)
        else:
            out.append(ln)
    return out


# ------------------------------------------------------------------------------------------------------ moments
QUESTION = re.compile(r"\?\s*$|^\W*(will|would|did|do|does|are|is|can|could|have|has|why|how|what|when|who)\b", re.I)
DODGE = re.compile(r"answer the question|yes or no|you didn'?t answer|i'?m asking you|reclaiming my time|that'?s not "
                   r"what i asked|(it'?s )?a simple question|just answer|i asked you|you'?re not answering", re.I)
PAPER = re.compile(r"\byou (wrote|tweeted|posted|signed|testified)\b|\byou said\b.{0,40}\b(in|on|last) "
                   r"((19|20)\d\d|year|week|month|january|february|march|april|may|june|july|august|september|"
                   r"october|november|december)\b|\byour (own )?words\b|\bin your (own )?(letter|email|statement|book|"
                   r"memo)\b|\blet me read\b|\bi'?ll read\b|\bquote,|\bquote,? unquote\b|\bi'?m quoting\b|"
                   r"\bthis (email|letter|memo|document|tweet|report)\b|\bexhibit\b|\bpage \d+\b", re.I)
CLASH = re.compile(r"excuse me|let me finish|let me answer|you'?re (lying|wrong)|that'?s (false|a lie|not true)|"
                   r"\bno, no\b|how dare|shame on you|stop talking|you'?re out of order", re.I)
CUE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def cues(srt: str) -> List[Tuple[float, float, str]]:
    out, cur = [], None
    for ln in srt.splitlines():
        m = CUE.search(ln)
        if m:
            g = [int(x) for x in m.groups()]
            cur = [g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000, ""]
            out.append(cur)
        elif cur is not None and ln.strip() and not ln.strip().isdigit():
            cur[2] = (cur[2] + " " + ln.strip()).strip()
    return [(a, b, t) for a, b, t in out]


def _words(t: str) -> set:
    return {w for w in re.findall(r"[a-z']+", t.lower()) if len(w) >= 4}


def moments(cs: List[Tuple[float, float, str]], window: float = 60.0, step: float = 15.0, hot: float = 4.0) -> dict:
    """What a transcript holds: fight spans (60 s windows with `hot`+ questions, clashes and dodges, joined; their
    length; 3+ minutes = ROUNDS), the dodges (a question asked again within 4 minutes, at least half its words the
    same, or a 'yes or no' / 'answer the question'), and the paper (their own words, an email, a tweet, a page)."""
    if not cs:
        return {"fights": [], "dodges": [], "paper": [], "rounds": False}
    qs = [(a, t) for a, _b, t in cs if QUESTION.search(t)]
    dodges = [{"at": round(a, 1), "line": t[:120]} for a, _b, t in cs if DODGE.search(t)]
    for i, (a, t) in enumerate(qs):
        wa = _words(t)
        for a0, t0 in qs[max(0, i - 8):i]:
            w0 = _words(t0)
            if len(wa) >= 3 and len(w0) >= 3 and a - a0 <= 240 and len(wa & w0) / len(wa | w0) >= 0.5:
                dodges.append({"at": round(a, 1), "line": f"asked again: {t[:110]}"})
                break
    dodges.sort(key=lambda d: d["at"])
    paper = [{"at": round(a, 1), "line": t[:140]} for a, _b, t in cs if PAPER.search(t)]
    end = cs[-1][1]
    marks = [(a, 1.0) for a, _t in qs] + [(a, 2.0) for a, _b, t in cs if CLASH.search(t)] + \
        [(d["at"], 2.0) for d in dodges]
    spans: List[List[float]] = []
    t = 0.0
    while t < end:
        s = sum(w for a, w in marks if t <= a < t + window)
        if s >= hot:
            if spans and t <= spans[-1][1]:
                spans[-1][1] = t + window
            else:
                spans.append([t, t + window])
        t += step
    fights = [{"from": round(a, 1), "to": round(min(b, end), 1), "minutes": round((min(b, end) - a) / 60, 1)}
              for a, b in spans]
    fights.sort(key=lambda f: -f["minutes"])
    return {"fights": fights[:3], "dodges": dodges[:8], "paper": paper[:6],
            "rounds": bool(fights and fights[0]["minutes"] >= 3.0)}


PREP_JOB = re.compile(r"^JOB (.+?): PREPPED")
PREP_WORK = re.compile(r"work dir (/\S+?)[.,;\s]")


def prepped(posting_day: str) -> List[dict]:
    """The 15:00 prep's jobs for a posting day, each with its work folder (Sweeps/<day>-prep.md)."""
    p = Path.home() / "Desktop" / "Clipping" / "Sweeps" / f"{posting_day}-prep.md"
    try:
        text = p.read_text()
    except OSError:
        return []
    out, cur = [], None
    for ln in text.splitlines():
        m = PREP_JOB.match(ln)
        if m:
            cur = {"job": m.group(1).strip(), "work": None}
            out.append(cur)
        elif cur and not cur["work"]:
            w = PREP_WORK.search(ln + " ")
            if w:
                cur["work"] = w.group(1).replace("/tmp/", "/private/tmp/", 1) if w.group(1).startswith("/tmp/") \
                    else w.group(1)
    return out


def job_moments(work: Optional[str]) -> Optional[dict]:
    if not work:
        return None
    srt = Path(work) / "transcript.srt"
    try:
        return moments(cues(srt.read_text()))
    except OSError:
        return None


# ------------------------------------------------------------------------------------------------------ the board
def build(cfg: C.Config, slug: str, scout_day: str) -> dict:
    acct = cfg.account(slug)
    posting = (date.fromisoformat(scout_day) + timedelta(days=1)).isoformat()
    board = json.loads((C.STATE / slug / "scout" / f"{scout_day}.json").read_text())
    cands = candidates(board, _engine_formats(acct))
    sts = stories(cands)
    level, why = news_level(sts)
    vault, _outcomes = S.vault_rows(acct, S.load(slug, scout_day))
    for r in vault:
        r.setdefault("section", "")
    _sections(acct, vault, S.load(slug, scout_day))
    jobs = [{**j, "moments": job_moments(j["work"])} for j in prepped(posting)]
    got = hits(acct)
    plan = sequel_plan(acct, got, posting)
    return {"account": slug, "scout_day": scout_day, "posting_day": posting, "made": datetime.now().isoformat(
        timespec="seconds"), "level": level, "level_why": why,
        "lanes": apply_sequel(lanes(cands, sts, vault, level), plan), "part2": got, "sequel": plan, "jobs": jobs}


def _sections(acct, rows: List[dict], scfg: dict) -> None:
    """Each vault row's section (FLOOR, TAPE, HEARINGS, SIT-DOWN, GAGGLE), read from vault.md's headings."""
    try:
        text = (Path(acct.account_dir) / ((scfg.get("looks") or {}).get("vault") or {})["path"]).read_text()
    except (OSError, KeyError, TypeError):
        return
    sec, where = "", {}
    for ln in text.splitlines():
        if ln.startswith("## "):
            sec = ln[3:].split(":")[0].strip().upper()
        m = S.VAULT_ROW.match(ln)
        if m:
            where[m.group(1)] = sec
    for r in rows:
        r["section"] = where.get(r.get("id"), "")


def _mmss(s: float) -> str:
    return f"{int(s // 60)}:{int(s % 60):02d}"


def markdown(b: dict) -> str:
    d = datetime.fromisoformat(b["posting_day"]).strftime("%m.%d.%Y")
    lvl = {"normal": "🟢 NORMAL day · 2 news", "big": "🟡 BIG day · 4 news", "huge": "🔴 HUGE day · 6 news"}[b["level"]]
    out = [f"# Politics · THE DAILY 8 · {d}", "", f"## {lvl}", f"- 📌 {b['level_why']}", ""]
    out += ["## 🎬 The 8 slots", ""]
    for ln in b["lanes"]:
        flip = " (flipped to the story)" if ln["flipped"] else ""
        out.append(f"### {ln['slot']} · {ln['name']}{flip}")
        out.append(f"- 📌 {ln['what']}")
        for tag, p in (("👉 Pick", ln["pick"]), ("↪ Backup", ln["backup"])):
            if not p:
                out.append(f"- {tag}: ⚠️ nothing fits today")
                continue
            src = "THE VAULT " + str(p.get("id")) if p["from"] == "vault" else (p.get("channel") or "")
            fm = f" · {p['fmt']}" if p.get("fmt") else ""
            out.append(f"- {tag}: **{p['title'][:110]}** ({src}{fm}) {p.get('url') or ''}")
        out.append("")
    out += ["## 🔁 Part 2 (the last 3 days' hits)", ""]
    seq = b.get("sequel")
    if seq:
        out.append(f"- 👉 **PART {seq['part']} at {PART_SLOT}**: {seq['title'][:100]}, from {seq['where']}. "
                   f"FIGHT 2 moves to {MOVED_TO}.")
        out.append(f"  - 📌 Slate: \"sequel\": {{\"part\": {seq['part']}, \"of\": \"{seq['of']}\"}}. A new hook, "
                   f"its own punch line first, who's who in its first line. Gone from the temp folder? "
                   f"`python3 -m clipfactory.masters restore politics <work>`")
    if not b["part2"]:
        out.append("- ✅ No hit over the bar right now")
    for h in b["part2"]:
        v = ", ".join(f"{k} {n:,}" for k, n in sorted(h["views"].items()))
        s = h["source"]
        left = (f"{s['left_after']} min left after it, {s['left_before']} before it, of {s['minutes']} min"
                if s.get("minutes") else "its video's length is not known here")
        out.append(f"- 📈 **{(h.get('title') or h['clip'])[:100]}** ({h['day']}): {v}")
        out.append(f"  - 🎬 {left} · {s.get('url') or ''}")
    out += ["", "## 🔍 Moments in tonight's prepped jobs", ""]
    if not b["jobs"]:
        out.append("- ⚠️ No prep file for this day yet (the 15:00 prep writes it)")
    for j in b["jobs"]:
        m = j["moments"]
        if not m:
            out.append(f"- **{j['job']}**: no transcript yet")
            continue
        f0 = m["fights"][0] if m["fights"] else None
        fight = f"fight {_mmss(f0['from'])}-{_mmss(f0['to'])} ({f0['minutes']} min)" if f0 else "no long fight"
        tags = [fight] + (["🥊 ROUNDS"] if m["rounds"] else []) + \
            ([f"{len(m['dodges'])} dodges"] if m["dodges"] else []) + (["📄 THE PAGE"] if m["paper"] else [])
        out.append(f"- **{j['job']}**: " + " · ".join(tags))
        for p in m["paper"][:2]:
            out.append(f"  - 📄 {_mmss(p['at'])} \"{p['line']}\"")
        for x in m["dodges"][:2]:
            out.append(f"  - 🥊 {_mmss(x['at'])} \"{x['line']}\"")
    return "\n".join(out) + "\n"


def write(b: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = datetime.fromisoformat(b["posting_day"]).strftime("%m.%d.%Y")
    md = out_dir / f"Politics Daily 8 {d}.md"
    md.write_text(markdown(b))
    md.with_suffix(".json").write_text(json.dumps(b, indent=1, default=str))
    return md


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="daily8")
    ap.add_argument("account")
    ap.add_argument("--date", help="the scout's day (default today); the board is for the day after")
    ap.add_argument("--out", help="where to write (default the scout's own folder)")
    a = ap.parse_args(argv)
    cfg = C.load()
    day = a.date or date.today().isoformat()
    b = build(cfg, a.account, day)
    out = Path(a.out) if a.out else cfg.drafts_root / "Scout"      # beside the scout's own board
    print(write(b, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
