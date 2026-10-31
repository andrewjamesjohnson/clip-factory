"""Cards and stories from real footage: daily stills in each account's own look, signed on their own boards, posted to
Facebook and Instagram; and story reposts of the clips Andrew signed. No AI anywhere: every picture is a clean frame of
the footage a clip was cut from (the public-record source, a GTA clip's own master, or a cast check still pulled from
it), never our own delivered clip, its cuts or its cover (they carry our burned-in captions, hook and handle); with no
clean frame on this Mac a card goes on the account's lockup and flat kit ink. A card that is not about one clip (the
countdown, a carousel cover) never carries anyone's footage: the account's own look alone. Every word comes from the transcript
(verbatim), the slate, the signed copy or the verified cast.

The kinds (switches.settings "cards.kinds", by maker):

    Politics  quote       a line from today's clip, found word for word in its captions, with its speaker only
                                when the cast is verified by a person (one person, or the line inside that person's
                                plate), on a clean frame of the source at that second; it goes 60 to 90 minutes after
                                its clip is live, and is skipped when the clip did not post
                    docket      yesterday's clips (3 or more) as a carousel, at 12:00
                    week_best   Mondays: last week's five most watched, as a carousel, at 19:00
                    news        a news post (2026-10-22): a story the news desk (prompts/news-desk.md, a Claude
                                task) checked in two outlets or a primary release and wrote up as a request in
                                state/<slug>/cards/news/<day>/<id>.json (factory.py cards news add). Two slides: the
                                picture (an atlas portrait or seal) and the headline; THE POLITICS's number or ON THE
                                RECORD's quote and the story. The sources as their logos; the post's own words per
                                platform (captions), signed with the card. The only kind whose words are written,
                                not found: the desk writes them, Andrew signs them
    Gaming       countdown   N DAYS to GTA 6 (2026-11-19): the lockup on the kit's flat ink, never anyone's
                                footage, at 09:00; OUT TODAY on launch day; none after
                    clip_of_day yesterday's most watched clip: a clean frame of its source at the clip's moment (the
                                one across its span that shows the game, not a browser or a website; else the
                                lockup), its title, the creator's credit, 20:30
                    week_best   Mondays: last week's five, on the kit's flat ink, 19:00

    state/<slug>/cards/<YYYY-MM-DD>.json        the cards board of that day (made once a day after 06:26)
    state/<slug>/cards/<YYYY-MM-DD>/<id>/       the grabbed still; r<N>/ per drawing: 1.jpg (1.mp4 for Instagram),
                                                spec.json (a make never draws into the folder a card points at)
    state/<slug>/cards/rules.json               the story repost rule ({"story_repost": {...}}), signed on the page

A card is signed on the Cards tab (Approve: the page sends the card's hash, which covers its id, kind, time, targets,
every file's SHA-256 and its words). Making it again, or a new time, voids the signature. A signed card goes when its
time comes (cards.post on, or practice), through outward (kind card_post, key card:<platform>:<date>/<id>:<target>,
the studio runner), one at a time, never within 30 minutes of a Reel on that platform, 08:00 to 23:30, 2 a day on each
platform at the most (Approve stops at that; a full day waits in the queue). An unsigned card expires 2 hours past its
time. A NEWS post's time is the moment it is signed, and no Reel holds it back (Andrew, 2026-10-29: "The moment it's
made, it gets shipped"): Approve starts the studio's step at once (kick).

Story reposts (cards.story_repost, plus the rule signed on the Cards tab): when a Reel is live on Facebook or
Instagram, the bytes that platform was sent (its own cut, or the clip) go to that platform's story 30 to 60 minutes
later, 60 seconds at the most, 4 a day at the most. A Politics clip over 60 seconds goes only with its signed Instagram
cut. Key story:<platform>:<date> <slot>.

Pictures and video: PIL only in render/card_engine.py, run as [CLIP_ENGINE_PYTHON or makers.PY_SKILLS, "-B", engine,
spec.json] with a 60 second timeout; stills and MP4s with ffmpeg. Both only 06:26 to 20:45 (the night shift renders).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import board as B
from . import config as C
from . import store
from . import switches as SW

# ------------------------------------------------------------------------------------------------------ the kinds
KINDS = ("quote", "docket", "week_best", "countdown", "clip_of_day", "news")
STORY_KINDS = ("quote", "countdown", "clip_of_day")
CAROUSEL_KINDS = ("docket", "week_best", "news")
KIND_TARGETS = {k: ("facebook_story", "instagram_story") for k in STORY_KINDS}
KIND_TARGETS.update({k: ("facebook_feed", "instagram_carousel") for k in CAROUSEL_KINDS})
TARGETS = ("facebook_story", "instagram_story", "facebook_feed", "instagram_carousel")
KIND_WORDS = {"quote": "Quote card", "docket": "The Docket", "week_best": "Week's best", "countdown": "GTA 6 countdown",
              "clip_of_day": "Clip of the day", "news": "News post"}
TARGET_WORDS = {"facebook_story": "Facebook story", "instagram_story": "Instagram story",
                "facebook_feed": "Facebook post", "instagram_carousel": "Instagram carousel"}
STATUSES = ("draft", "ready", "approved", "scheduled", "posted", "declined", "failed", "expired", "skipped")
OPEN = ("draft", "ready")
SIGNED = ("approved", "scheduled")
DONE_STATES = ("done", "practice")
GIVEN_UP = ("skipped", "refused", "gave_up")
STORY_OUTLETS = {"facebook_reels": "facebook", "instagram_reels": "instagram"}
CD_SWITCH = {"card_post": "cards.post", "story": "cards.story_repost"}
DECLINE = [["wrong_picture", "Wrong picture"], ["wrong_words", "Wrong words"], ["wrong_person", "Wrong person"],
           ["wrong_time", "Wrong time"], ["looks_off", "Looks off"], ["not_needed", "Not needed"],
           ["rights", "Rights"], ["other", "Other"]]
DECLINE_CODES = tuple(c for c, _ in DECLINE)

LAUNCH = date(2026, 11, 19)          # GTA 6 (the kit's launch.date): the countdown stops after launch day
MAKE_WINDOW = ((6, 26), (20, 45))    # ffmpeg and PIL: never in the night shift
POST_WINDOW = ((8, 0), (23, 30))     # stories and cards go out by day
EXPIRE_AFTER = timedelta(hours=2)    # an unsigned card, past its time
POST_LATE = timedelta(hours=3)       # a signed card that could not go this long after its time is not sent late
STORY_MAX_S = 60
DOCKET_MIN = 3
CAROUSEL_MAX = 9                     # slides after the cover (Instagram takes 10 children)
WEEK_TOP = 5
STILL_HEIGHT = 1920                  # a grabbed still is at most this tall, never taller than its source
ENGINE = C.ROOT / "render" / "card_engine.py"
ENGINE_TIMEOUT = 60
LOGOS_DIR = C.ROOT / "render" / "logos"   # the outlets' own marks (logos.json says each one's file, licence, page)
# news posts (2026-10-22): what a request may hold, and the words they may not
NEWS_PICTURE = {"person": "portrait", "member": "portrait", "agency": "seal"}
NEWS_LIMITS = {"kicker": 24, "headline": 80, "line": 150, "story": 460, "figure": 16, "label": 110, "quote": 150,
               "name": 40, "title": 40, "facebook": 1500, "instagram": 1500}
NEWS_DASH = re.compile("[-‐-―−]")      # no hyphen or dash anywhere a person reads (Andrew 10.22)
NEWS_PARTY = re.compile(r"\((?:R|D|I)-|\b(?:Republicans?|Democrats?|Democratic|GOP|MAGA)\b", re.I)   # never on the picture
NEWS_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){0,5}$")
MP4_SECONDS = 6
STORY_RULE = {"version": 1, "platforms": ["facebook", "instagram"], "delay_min": [30, 60], "per_day": 4,
              "which": "all", "max_seconds": STORY_MAX_S, "signoff": None, "state": "unsigned", "history": []}
TOP_SCORE = 7.0                      # "which": "top": only clips the coach scored at least this, out of 10
FONTS = ("/System/Library/Fonts/HelveticaNeue.ttc", "/System/Library/Fonts/Supplemental/Impact.ttf",
         "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


# ------------------------------------------------------------------------------------------------------ small helpers
def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def _is_client(acct) -> bool:
    raw = getattr(acct, "raw", None)
    return isinstance(raw, dict) and str(raw.get("owner") or "self") == "client"


def _accounts(cfg: C.Config) -> List[C.Account]:
    """The accounts cards work for: switched on, on this machine (cfg.accounts), never a client's."""
    return [a for a in cfg.accounts.values() if not _is_client(a)]


def _now(cfg: C.Config, now: Optional[datetime] = None) -> datetime:
    t = now or cfg.now()
    if t.tzinfo is None:
        t = t.replace(tzinfo=cfg.tz())
    return t


def _utc(t: datetime) -> datetime:
    return t.astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return _utc(t).replace(microsecond=0).isoformat()


def _when(v) -> Optional[datetime]:
    return store._when(v) if v not in (None, "") else None


def _local(cfg: C.Config, t: datetime) -> datetime:
    return t.astimezone(cfg.tz())


def _inside(cfg: C.Config, now: datetime, window) -> bool:
    lt = _local(cfg, now)
    (sh, sm), (eh, em) = window
    return (sh, sm) <= (lt.hour, lt.minute) < (eh, em)


def can_draw(cfg: C.Config, now: Optional[datetime] = None) -> bool:
    """06:26 to 20:45 local: the only time ffmpeg and PIL run for cards (the night shift renders the clips)."""
    return _inside(cfg, _now(cfg, now), MAKE_WINDOW)


def can_post(cfg: C.Config, now: Optional[datetime] = None) -> bool:
    return _inside(cfg, _now(cfg, now), POST_WINDOW)


def _need_draw(cfg: C.Config, now: Optional[datetime] = None) -> None:
    if not can_draw(cfg, now):
        raise SystemExit("Cards are drawn between 06:26 and 20:45 (the night shift renders the clips): try again "
                         "after 06:26.")


def _hhmm(t) -> str:
    return str(t).replace(":", "")[:4]


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _clock_words(cfg: C.Config, t: Optional[datetime]) -> str:
    if t is None:
        return ""
    lt = _local(cfg, t)
    return lt.strftime("%I:%M %p").lstrip("0")


def _day_words(d: date) -> str:
    return d.strftime("%B ") + str(d.day) + d.strftime(", %Y")


def _views_words(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M views".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1000:.1f}K views".replace(".0K", "K")
    return f"{n} views"


# ------------------------------------------------------------------------------------------------------ the files
def board_path(acct, day) -> Path:
    """state/<slug>/cards/<YYYY-MM-DD>.json (at call time)."""
    return store.path(_slug(acct), "cards", f"{str(day)[:10]}.json")


def card_dir(acct, day, cid: str) -> Path:
    return store.path(_slug(acct), "cards", str(day)[:10], cid)


def rules_path(acct) -> Path:
    return store.path(_slug(acct), "cards", "rules.json")


def read_board(acct, day) -> dict:
    d = store.read(board_path(acct, day), {})
    return d if isinstance(d, dict) else {}


def _blank(acct, day):
    def make() -> dict:
        return {"account": _slug(acct), "date": str(day)[:10], "updated_at": "", "cards": {}, "stories": {}}
    return make


def edit_board(acct, day):
    """The day's cards board under its lock (store.edit). Never with a network call inside."""
    return store.edit(board_path(acct, day), _blank(acct, day))


def _touch(bd: dict) -> None:
    bd["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    bd.setdefault("cards", {})
    bd.setdefault("stories", {})


def card_days(acct) -> List[date]:
    """Every day with a cards board, oldest first."""
    folder = store.path(_slug(acct), "cards")
    out = []
    if folder.is_dir():
        for f in folder.glob("*.json"):
            try:
                out.append(date.fromisoformat(f.stem))
            except ValueError:
                continue
    return sorted(out)


def get_card(acct, day, cid: str) -> Optional[dict]:
    c = (read_board(acct, day).get("cards") or {}).get(cid)
    return c if isinstance(c, dict) else None


def card_hash(card: dict) -> str:
    """What Approve signs: the card's id, kind, time, targets, every image's and video's SHA-256, and its words."""
    files = card.get("files") or {}
    return _sha({"id": card.get("id"), "kind": card.get("kind"), "at": card.get("at"),
                 "targets": list(card.get("targets") or []),
                 "images": [str(i.get("sha256") or "") for i in files.get("images") or [] if isinstance(i, dict)],
                 "videos": [str(v.get("sha256") or "") for v in files.get("videos") or [] if isinstance(v, dict)],
                 "words": card.get("words") or {}})


def file_problems(card: dict, cached: bool = True) -> List[str]:
    """Each file the card's hash covers is there with the bytes it was made with."""
    out = []
    files = card.get("files") or {}
    for f in list(files.get("images") or []) + list(files.get("videos") or []):
        p = Path(str((f or {}).get("path") or ""))
        if not p.is_file():
            out.append(f"{p.name or 'a file'} is gone")
        elif B.sha256_file(p, cached=cached) != f.get("sha256"):
            out.append(f"{p.name} changed after it was made")
    if not (files.get("images") or []):
        out.append("it has no picture")
    return out


def signed_ok(card: dict) -> str:
    """'' when the card is signed as it is now; else why not."""
    so = card.get("signoff") or {}
    if not so.get("hash"):
        return "it is not signed"
    if so["hash"] != card_hash(card):
        return "it changed after it was signed"
    bad = file_problems(card)
    return "; ".join(bad)


def post_time(card: dict) -> Optional[datetime]:
    """When a signed card goes: its own time, or for a quote card the time its clip went live plus its minutes."""
    return _when(card.get("post_at") or card.get("at"))


# ------------------------------------------------------------------------------------------------------ settings
def kinds_for(acct) -> List[str]:
    maker_kinds = SW.CARD_KINDS.get(str(getattr(acct, "maker", "") or ""), [])
    return [k for k in SW.settings(acct)["cards.kinds"] if k in maker_kinds]  # type: ignore


def targets_for(acct, kind: str) -> List[str]:
    want = [t for t in SW.settings(acct)["cards.targets"] if t in TARGETS]  # type: ignore
    return [t for t in want if t in KIND_TARGETS.get(kind, ())]


def _times(acct) -> dict:
    base = dict(SW.SETTINGS["cards.times"])  # type: ignore
    own = SW.settings(acct).get("cards.times")
    if isinstance(own, dict):
        for k, v in own.items():
            base[k] = v
    out = {}
    for k in ("countdown", "clip_of_day", "docket"):
        v = str(base.get(k) or "")
        out[k] = v if re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v) else SW.SETTINGS["cards.times"][k]  # type: ignore
    q = base.get("quote_after_min")
    if isinstance(q, (list, tuple)) and len(q) == 2 and all(isinstance(x, int) for x in q) and 30 <= q[0] <= q[1]:
        out["quote_after_min"] = [int(q[0]), int(q[1])]
    else:
        out["quote_after_min"] = [60, 90]
    wb = str(base.get("week_best") or "Mon 19:00").strip().lower().split()
    day = WEEKDAYS.index(wb[0][:3]) if wb and wb[0][:3] in WEEKDAYS else 0
    hm = wb[1] if len(wb) > 1 and re.match(r"^([01]\d|2[0-3]):[0-5]\d$", wb[1]) else "19:00"
    out["week_best"] = (day, hm)
    return out


def any_part_on(acct) -> bool:
    return any(SW.mode(acct, k) != SW.OFF for k in ("cards.make", "cards.post", "cards.story_repost"))


# ------------------------------------------------------------------------------------------------------ clip boards
def _clip_board(acct, day) -> dict:
    try:
        return B.read(acct, day) or {}
    except (OSError, ValueError):
        return {}


def _posted(s: dict) -> List[Tuple[str, dict]]:
    return [(o, r) for o, r in sorted((s.get("outlets") or {}).items())
            if isinstance(r, dict) and (r.get("post_id") or r.get("video_id"))]


def _series_24h(acct, outlet: str, rec: dict, publish_at: str) -> Optional[int]:
    pid = str(rec.get("platform_id") or rec.get("post_id") or rec.get("video_id") or "")
    if not pid or len(publish_at) < 7:
        return None
    got = store.read(store.path("stats", "series", f"{_slug(acct)}__{outlet}__{publish_at[:7]}.json"), {})
    row = got.get(pid) if isinstance(got, dict) else None
    for p in (row or {}).get("points") or [] if isinstance(row, dict) else []:
        if isinstance(p, dict) and p.get("label") == "24h" and isinstance(p.get("views"), int):
            return int(p["views"])
    return None


def views_of(acct, s: dict) -> int:
    """The clip's views on every platform it went to: each one's 24 hour checkpoint when it was read, else the
    latest number the stats read wrote on the board."""
    total = 0
    for outlet, rec in _posted(s):
        v = _series_24h(acct, outlet, rec, str(s.get("publish_at") or ""))
        if v is None:
            m = rec.get("metrics") or {}
            v = int(m.get("views") or 0) if isinstance(m.get("views"), (int, float)) else 0
        total += max(0, int(v))
    return total


def _copy_text(s: dict, outlet: str) -> str:
    """The signed words of one platform, only while the file is the one that was signed."""
    c = (s.get("copy") or {}).get(outlet) or {}
    p = Path(str(c.get("path") or ""))
    try:
        if not p.is_file() or (c.get("sha256") and B.sha256_file(p, cached=True) != c["sha256"]):
            return ""
        return p.read_text()
    except OSError:
        return ""


def title_of(s: dict) -> str:
    """The clip's own title from its signed copy (YouTube's title; else the first line of a caption), with GTA's
    " | credit" tail taken off. '' when there is none."""
    from . import copytext as CT
    for outlet in ("youtube_shorts", "facebook_reels", "instagram_reels", "tiktok"):
        text = _copy_text(s, outlet)
        if not text.strip():
            continue
        w = CT.for_platform(text, outlet)
        t = (w.get("title") or (w.get("caption") or "").strip().split("\n")[0]).strip()
        t = t.split(" | ")[0].strip()
        if t:
            return t[:140]
    return ""


def _job(acct, s: dict, day) -> str:
    try:
        from . import bench as BN
        return BN._job_of(acct, s, BN._sched(acct), str(day))
    except Exception:  # noqa: BLE001
        return ""


def _castcheck_stills(acct, job: str) -> List[Tuple[str, float, Path]]:
    """(person id, second, path) of each clean frame the cast check pulled from the master."""
    out = []
    folder = Path(acct.account_dir) / "Jobs" / job / "castcheck" if job else None
    if folder is None or not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.jpg")):
        m = re.match(r"^(.+)-(\d+(?:\.\d+)?)$", f.stem)
        if m:
            out.append((m.group(1).lower(), float(m.group(2)), f))
    return out


def _ours(acct, s: dict) -> Tuple[List[Path], List[Path]]:
    """(files, folders) of our own making for a slot: the delivered clip, its platform cuts, its cover and the file a
    placed clip came from; the drafts and delivery folders it sits in and the account's rendered clips (Jobs/*/clips).
    Every one of them carries our burned-in captions, hook, handle or masthead."""
    files = [(s.get("clip") or {}).get("path"), s.get("cover"), (s.get("placed") or {}).get("from")]
    files += [(c or {}).get("path") for c in (s.get("cuts") or {}).values() if isinstance(c, dict)]
    folders = [s.get("folder"), s.get("source_folder")]
    jobs = Path(str(getattr(acct, "account_dir", "") or "")) / "Jobs"
    if jobs.is_dir():
        folders += [str(p) for p in jobs.glob("*/clips")]
    return ([Path(os.path.expanduser(str(f))) for f in files if f],
            [Path(os.path.expanduser(str(f))) for f in folders if f])


def is_ours(acct, s: dict, src: Path) -> bool:
    """True when `src` is one of our delivered files (by path, or by inode: the drafts copy is a hard link of the
    delivered one) or lies inside a folder of ours. A card's picture is never one of these."""
    files, folders = _ours(acct, s)
    try:
        real = src.resolve()
    except OSError:
        return True
    for f in files:
        try:
            if real == f.resolve() or (f.exists() and os.path.samefile(str(src), str(f))):
                return True
        except OSError:
            continue
    for d in folders:
        try:
            if d.resolve() in real.parents:
                return True
        except OSError:
            continue
    return False


def _span_of(acct, s: dict, clip: dict) -> Optional[Tuple[float, float]]:
    """The source seconds the clip was cut from: its slate entry (segments, else start and end), else the GTA maker's
    pick_span on the board."""
    from . import bench as BN
    sp = BN._span(clip or {})
    if sp is None and isinstance(s.get("pick_span"), (list, tuple)) and len(s["pick_span"]) == 2:
        try:
            sp = (float(s["pick_span"][0]), float(s["pick_span"][1]))
        except (TypeError, ValueError):
            sp = None
    return sp if sp is not None and sp[1] > sp[0] else None


def source_frame(acct, d: date, s: dict, out: Path, at: float = 0.4, prefer_game: bool = False) -> Optional[Path]:
    """A clean frame of the footage the clip was cut from (its job's master, the source with nothing of ours on it),
    `at` of the way into the span the clip used (with `prefer_game`, the probe across the span that shows the least
    flat UI: game_second), grabbed at most 1920 tall and never upscaled. None when the master is not on this Mac, no
    span is known, or the "master" is a file of ours (the delivered clip, a cut, the cover, a drafts or delivery
    folder, a rendered clip): never a frame with our captions or hook burned in."""
    from . import bench as BN
    from . import castqueue as CQ
    job = _job(acct, s, d)
    if not job:
        return None
    try:
        clip, pj = (BN._politics_slate if getattr(acct, "maker", "") == "politics" else BN._gta_slate)(
            acct, job, str(s.get("slug") or ""))
    except Exception:  # noqa: BLE001
        return None
    master = CQ._master(pj)
    span = _span_of(acct, s, clip)
    if master is None or span is None or is_ours(acct, s, master):
        return None
    a, b = span
    sec = game_second(master, a, b) if prefer_game else a + (b - a) * at
    return out if _grab(master, sec, out) else None


PROBE_AT = (0.15, 0.25, 0.4, 0.55, 0.7, 0.85)   # where game_second looks across a clip's span
PROBE_W, PROBE_H = 160, 90                       # a probe frame, grey, this small (enough to see flat UI)
PROBE_MARGIN = 0.08                              # a probe must beat the 40% frame by this much flat area to win


def _luma_probe(src: Path, sec: float, timeout: int = 30) -> Optional[bytes]:
    """One frame of a video at `sec` as PROBE_W x PROBE_H grey bytes (ffmpeg, nothing written to disk), or None."""
    cmd = ["nice", "-n", "10", _ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, sec):.2f}",
           "-i", str(src), "-frames:v", "1", "-vf", f"scale={PROBE_W}:{PROBE_H}:flags=area,format=gray",
           "-f", "rawvideo", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and len(r.stdout) == PROBE_W * PROBE_H else None


def chrome_share(px: bytes, w: int = PROBE_W, h: int = PROBE_H) -> Optional[float]:
    """How much of a frame is flat UI: the share of it in long flat runs (10 px or more along a row within 2 grey
    levels: a web page's fills and bars, a browser's chrome, a map's colour fills, a text panel), dark areas left out
    (a night scene is not UI). Game footage is textured: measured on GTA masters 2026-10-20, gameplay 0.003 to 0.05,
    a map website in a browser 0.17 to 0.31. None for a near-black frame (a fade is never the pick)."""
    if not px or len(px) != w * h or sum(px) / len(px) < 24:
        return None
    flat = 0
    for y in range(h):
        o, x = y * w, 0
        while x < w:
            v, e = px[o + x], x + 1
            while e < w and abs(px[o + e] - v) <= 2:
                e += 1
            if e - x >= 10 and v >= 40:
                flat += e - x
            x = e
    return flat / (w * h)


def game_second(master: Path, a: float, b: float) -> float:
    """The second of a clip's span (a..b) that shows the game rather than a browser, a website or other flat UI: the
    probe across the span with the least flat area, when it beats the 40% frame clearly; else 40% (when unsure, or
    when the whole span is the same kind of picture)."""
    base = a + (b - a) * 0.4
    got = []
    for f in PROBE_AT:
        sec = a + (b - a) * f
        score = chrome_share(_luma_probe(master, sec) or b"")
        if score is not None:
            got.append((score, abs(f - 0.4), sec, f))
    if not got:
        return base
    at40 = next((g[0] for g in got if g[3] == 0.4), None)
    best = min(got)
    if at40 is None or best[0] <= at40 - PROBE_MARGIN:
        return best[2]
    return base


def clip_still(acct, s: dict, day, out: Path) -> Optional[Path]:
    """A clean frame for a carousel slide about one clip (the Politics's docket and week's best): the source's master at
    40% into the clip's span; else the cast check's still nearest that span (a clean frame the cast check pulled from
    the master). None when neither is here: the slide is drawn without a picture, never on our cover or our clip."""
    got = source_frame(acct, day, s, out)
    if got is not None:
        return got
    job = _job(acct, s, day)
    frames = _castcheck_stills(acct, job) if job else []
    if not frames:
        return None
    from . import bench as BN
    span = _span_of(acct, s, BN._politics_slate(acct, job, str(s.get("slug") or ""))[0]) if \
        getattr(acct, "maker", "") == "politics" else None
    mid = (span[0] + span[1]) / 2 if span else frames[0][1]
    return min(frames, key=lambda x: abs(x[1] - mid))[2]


def posted_on(acct, d: date) -> List[Tuple[str, dict]]:
    """The slots of that day that went out on at least one platform, by time."""
    bd = _clip_board(acct, d)
    return [(k, s) for k, s in sorted((bd.get("slots") or {}).items())
            if isinstance(s, dict) and s.get("status") in B.SIGNED and _posted(s)]


# ------------------------------------------------------------------------------------------------------ the quote
_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")


def parse_srt(text: str) -> List[Tuple[float, float, str]]:
    """[(start, end, words)] of an .srt."""
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            m = _SRT_TIME.search(ln)
            if m:
                g = [int(x) for x in m.groups()]
                a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
                b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
                out.append((a, b, " ".join(lines[i + 1:])))
                break
    return out


def norm_words(text: str) -> List[str]:
    t = str(text or "").lower().replace("’", "'").replace("‘", "'")
    return _WORD.findall(t)


def find_words(quote: str, cues: Sequence[Tuple[float, float, str]]) -> Optional[Tuple[float, float]]:
    """Where the quote's words run, in order and all of them, across the cues: (start of the cue holding its first
    word, end of the cue holding its last). None when it is not there word for word."""
    want = norm_words(quote)
    if not want:
        return None
    flat: List[Tuple[str, float, float]] = []
    for a, b, words in cues:
        for w in norm_words(words):
            flat.append((w, float(a), float(b)))
    n = len(want)
    for i in range(0, len(flat) - n + 1):
        if all(flat[i + j][0] == want[j] for j in range(n)):
            return (flat[i][1], flat[i + n - 1][2])
    return None


def _read_cues(work: Path) -> List[Tuple[float, float, str]]:
    """The source's own sentence cues (cues.json [[start, end, text]], or transcript.srt) on the source clock."""
    try:
        raw = json.loads((work / "cues.json").read_text())
        return [(float(c[0]), float(c[1]), str(c[2])) for c in raw if isinstance(c, (list, tuple)) and len(c) >= 3]
    except (OSError, ValueError, TypeError):
        pass
    try:
        return parse_srt((work / "transcript.srt").read_text())
    except OSError:
        return []


def _cast_people(cast_file: str) -> Dict[str, dict]:
    try:
        raw = json.loads(Path(cast_file).read_text())
    except (OSError, ValueError):
        return {}
    return {str(p.get("id") or "").lower(): p for p in raw.get("people") or [] if isinstance(p, dict)}


def _plates(acct, job: str, slug: str) -> List[dict]:
    """The name plates as placed on the delivered clip (Jobs/<job>/clips/NN-<slug>.cards.json), clip clock."""
    folder = Path(acct.account_dir) / "Jobs" / job / "clips"
    out = []
    for f in sorted(folder.glob(f"*-{slug}.cards.json")) if folder.is_dir() else []:
        try:
            out += [c for c in json.loads(f.read_text()).get("cards") or [] if isinstance(c, dict)]
        except (OSError, ValueError):
            continue
    return out


def verified_by(text: str) -> str:
    """'speaker verified by Andrew 10-19' from cast.json's verified line ("Andrew, in Clip Review's Casts
    (2026-10-19 06:55): ...")."""
    who = str(text or "").split(",")[0].split(" in ")[0].strip()[:40]
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(text or ""))
    return f"speaker verified by {who or 'a person'}" + (f" {m.group(2)}-{m.group(3)}" if m else "")


def quote_source(acct, day, k: str, s: dict) -> Tuple[Optional[dict], str]:
    """What a quote card for this slot would hold, from what is on disk, or (None, why there is none). The line must
    be the slate's quote, found word for word in the delivered captions; the speaker only from a cast verified by a
    person (one person, or the line inside that person's plate); a line that falls in a question is the reporter's,
    so no card."""
    from . import bench as BN
    job = _job(acct, s, day)
    if not job:
        return None, "no job is known for this clip"
    clip, pj = BN._politics_slate(acct, job, str(s.get("slug") or ""))
    quote = " ".join(str(clip.get("quote") or "").split())
    if not quote:
        return None, "its slate has no quote"
    clip_path = Path(str((s.get("clip") or {}).get("path") or ""))
    try:
        cues = parse_srt(clip_path.with_suffix(".srt").read_text())
    except OSError:
        return None, "its captions (.srt) are not beside the clip"
    span = find_words(quote, cues)
    if span is None:
        return None, "the quote is not in the delivered captions word for word"
    cast = BN.politics_cast(acct, job, clip)
    people = [p for p in cast.get("people") or [] if isinstance(p, dict)]
    work = Path(os.path.expanduser(str(pj.get("source") or ""))).parent if pj.get("source") else None
    src = find_words(quote, _read_cues(work)) if work is not None else None
    if src is not None:
        mid = (src[0] + src[1]) / 2
        for q in clip.get("questions") or []:
            if isinstance(q, (list, tuple)) and len(q) == 2 and float(q[0]) <= mid <= float(q[1]):
                return None, "the line falls inside a question (the reporter's words, not the speaker's)"
    speaker = None
    if cast.get("status") == "verified" and len(people) == 1:
        speaker = people[0]
    else:
        ok = {str(p.get("id") or "").lower(): p for p in people
              if p.get("verified") and not p.get("by_bot") and not p.get("provisional")}
        for pl in _plates(acct, job, str(s.get("slug") or "")):
            who = str(pl.get("who") or "").lower()
            a = float(pl.get("at") or 0)
            b = float(pl.get("end") or (a + float(pl.get("hold") or 0)))
            if who in ok and a - 0.25 <= span[0] and span[1] <= b + 0.25:
                speaker = ok[who]
                break
    if speaker is None:
        return None, ("no speaker verified by a person for this line: "
                      + (cast.get("why") or f"the cast is {cast.get('status')} with {len(people)} people"))
    full = _cast_people(str(cast.get("cast_file") or "")).get(str(speaker.get("id") or "").lower(), {})
    channel = str(pj.get("source_channel") or s.get("host") or "").strip()
    when = str(pj.get("source_date") or "").strip()
    return {"job": job, "quote": quote, "clip_s": [round(span[0], 3), round(span[1], 3)],
            "source_s": [round(src[0], 3), round(src[1], 3)] if src else None,
            "master": str(pj.get("source") or ""), "project": pj,
            "speaker": {"id": str(speaker.get("id") or ""), "name": str(full.get("name") or speaker.get("name") or ""),
                        "title": str(full.get("title") or ""), "verified": str(full.get("verified") or
                                                                              speaker.get("verified") or "")[:200]},
            "source_words": " · ".join(x for x in (channel, when) if x)}, ""


# ------------------------------------------------------------------------------------------------------ pictures
def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def _grab(src: Path, sec: float, out: Path, timeout: int = 60) -> bool:
    """One frame of a video at `sec`, at most STILL_HEIGHT tall and never upscaled, written whole or not at all."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.jpg")
    cmd = ["nice", "-n", "10", _ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, sec):.2f}",
           "-i", str(src), "-frames:v", "1", "-vf", f"scale=-2:'min({STILL_HEIGHT},ih)'", "-q:v", "2", "-y", str(tmp)]
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


def _to_mp4(jpg: Path, mp4: Path, timeout: int = 120) -> None:
    """The card as a 6 second MP4 with a silent AAC track: Instagram takes no image without a public URL, so its
    stories and carousels get this (the same pixels, in the video range a JPEG's full range is not: yuv420p)."""
    tmp = mp4.with_name(mp4.stem + ".part.mp4")
    cmd = ["nice", "-n", "10", _ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1",
           "-framerate", "30", "-i", str(jpg), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t",
           str(MP4_SECONDS), "-vf", "scale=out_range=tv,format=yuv420p", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        ok = r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0
        err = (r.stderr or b"")[-300:].decode(errors="ignore")
    except (OSError, subprocess.SubprocessError) as e:
        ok, err = False, str(e)
    if not ok:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise SystemExit(f"could not make the Instagram copy of {jpg.name}: {err.strip() or 'ffmpeg failed'}")
    os.replace(tmp, mp4)


def engine_python() -> str:
    from . import makers as M
    return os.environ.get("CLIP_ENGINE_PYTHON") or M.PY_SKILLS


def _run_engine(spec_path: Path) -> dict:
    """render/card_engine.py under the engine's Python (the skills' Pillow), one JSON line back."""
    eng = Path(ENGINE)
    cmd = [engine_python(), "-B", str(eng), str(spec_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=ENGINE_TIMEOUT, cwd=str(eng.parent),
                           env=dict(os.environ))
    except subprocess.TimeoutExpired:
        raise SystemExit(f"the card engine took over {ENGINE_TIMEOUT} seconds") from None
    except OSError as e:
        raise SystemExit(f"the card engine would not start: {e}") from None
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    try:
        got = json.loads(lines[-1]) if lines else {}
    except ValueError:
        got = {}
    if r.returncode != 0 or not got.get("ok"):
        why = got.get("error") or (r.stderr or "").strip()[-300:] or f"exit {r.returncode}"
        raise SystemExit(f"the card engine could not draw it: {why}")
    return got


def _copy_still(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
    return dst


def _new_run(acct, day, cid: str) -> Path:
    """A fresh folder for one drawing of a card (state/<slug>/cards/<day>/<id>/r<N>/): a make never draws into the
    folder a card already points at, so the files a signed or queued card holds are never drawn over."""
    top = card_dir(acct, day, cid)
    store._inside(top)
    top.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(f.name[1:]) for f in top.glob("r*") if f.is_dir() and f.name[1:].isdigit()] or [0])
    while True:
        folder = top / f"r{n}"
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            n += 1


def _run_of(card: Optional[dict]) -> Optional[Path]:
    """The r<N> folder a card's files are in (None for none)."""
    for f in ((card or {}).get("files") or {}).get("images") or []:
        p = Path(str((f or {}).get("path") or "")).parent
        if re.match(r"^r\d+$", p.name):
            return p
    return None


def _drop_run(folder: Optional[Path]) -> None:
    """Remove one drawing that no card points at (only an r<N> folder under state/<slug>/cards/)."""
    if folder is None or not re.match(r"^r\d+$", folder.name):
        return
    try:
        store._inside(folder)
    except SystemExit:
        return
    shutil.rmtree(folder, ignore_errors=True)


def render(cfg: C.Config, acct, day, card: dict, slides: List[dict], size: Sequence[int]) -> dict:
    """Draw the card's slides into a new folder state/<slug>/cards/<day>/<id>/r<N>/ (N.jpg, and N.mp4 for an Instagram
    target), and return its "files" and "render". Writes nothing outside the state folder, and never into a folder a
    card already points at: the caller swaps the new files in under the board's lock, or drops them. A drawing that
    fails leaves nothing behind."""
    folder = _new_run(acct, day, card["id"])
    try:
        return _render_into(folder, acct, card, slides, size)
    except BaseException:
        _drop_run(folder)
        raise


def _render_into(folder: Path, acct, card: dict, slides: List[dict], size: Sequence[int]) -> dict:
    look = "politics" if getattr(acct, "maker", "") == "politics" else "gta"
    work = []
    for i, s in enumerate(slides):
        s = dict(s)
        st = s.get("still")
        if st:
            dst = folder / f"still-{i + 1}.jpg"
            if Path(st) != dst:
                _copy_still(Path(st), dst)
            s["still"] = str(dst)
        work.append(s)
    outs = [str(folder / f"{i + 1}.jpg") for i in range(len(work))]
    spec = {"v": 1, "look": look, "kind": card["kind"], "skill_dir": str(getattr(acct, "skill_dir", "")),
            "kit": str(getattr(acct, "kit", "") or "gta"), "size": list(size), "slides": work, "outputs": outs}
    spec_path = folder / "spec.json"
    tmp = folder / "spec.json.part"
    tmp.write_text(json.dumps(spec, indent=1, ensure_ascii=False))
    os.replace(tmp, spec_path)
    got = _run_engine(spec_path)
    dims = {str(f.get("path")): (f.get("w"), f.get("h")) for f in got.get("files") or [] if isinstance(f, dict)}
    images = []
    for o in outs:
        p = Path(o)
        if not p.is_file() or p.stat().st_size == 0:
            raise SystemExit(f"the card engine did not write {p.name}")
        store._inside(p)
        w, h = dims.get(o, (size[0], size[1]))
        images.append({"path": o, "sha256": B.sha256_file(p), "w": int(w or size[0]), "h": int(h or size[1])})
    videos = []
    if any(t.startswith("instagram_") for t in card.get("targets") or []):
        for im in images:
            mp4 = Path(im["path"]).with_suffix(".mp4")
            _to_mp4(Path(im["path"]), mp4)
            videos.append({"path": str(mp4), "sha256": B.sha256_file(mp4), "seconds": MP4_SECONDS})
    return {"files": {"images": images, "videos": videos},
            "render": {"spec_sha": B.sha256_file(spec_path), "engine": got.get("engine") or "card_engine",
                       "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}}


# ------------------------------------------------------------------------------------------------------ the builders
def _base_card(cfg: C.Config, acct, day: date, cid: str, kind: str, at: datetime) -> dict:
    return {"id": cid, "kind": kind, "status": "ready", "at": _iso(at), "targets": targets_for(acct, kind),
            "after": None, "source": {}, "files": {"images": [], "videos": []}, "words": {}, "render": {},
            "signoff": None, "outlets": {}, "notes": []}


def build_quotes(cfg: C.Config, acct, day: date, now: datetime, only: Optional[str] = None) -> List[tuple]:
    """(card, slides, size) for each of today's clips that has a quote card to make, and ("skip", why) lines."""
    out: List[tuple] = []
    t = _times(acct)
    bd = _clip_board(acct, day)
    for k, s in sorted((bd.get("slots") or {}).items()):
        cid = f"quote-{_hhmm(k)}"
        if only and only != cid:
            continue
        if not isinstance(s, dict) or s.get("status") not in (B.READY,) + tuple(B.SIGNED) or not s.get("clip"):
            continue
        got, why = quote_source(acct, day, k, s)
        if got is None:
            out.append(("skip", f"{k}: no quote card ({why})"))
            continue
        folder = card_dir(acct, day, cid)
        still = None
        from . import castqueue as CQ
        master = CQ._master(got["project"])            # the job's clean source, while it is still on this Mac
        if master is not None and is_ours(acct, s, master):
            master = None                              # never a file of ours (it carries our captions)
        if master is not None and got.get("source_s"):
            a, b = got["source_s"]
            if _grab(master, a + min(1.0, (b - a) / 2), folder / "still-1.jpg"):
                still = folder / "still-1.jpg"
        if still is None:
            frames = [x for x in _castcheck_stills(acct, got["job"]) if x[0] == got["speaker"]["id"].lower()]
            if frames:
                mid = (got["source_s"][0] if got.get("source_s") else frames[0][1])
                still = min(frames, key=lambda x: abs(x[1] - mid))[2]
        if still is None:
            out.append(("skip", f"{k}: no quote card (no clean frame: the master is gone and there is no cast check "
                                f"still of the speaker)"))
            continue
        live = datetime.fromisoformat(str(s["publish_at"]))
        card = _base_card(cfg, acct, day, cid, "quote", live + timedelta(minutes=t["quote_after_min"][0]))
        outlet = next((o for o in ("instagram_reels", "facebook_reels") if o in (acct.platforms or [])),
                      "instagram_reels")
        card["after"] = {"slot": k, "outlet": outlet, "minutes": list(t["quote_after_min"])}
        sp = got["speaker"]
        card["source"] = {"board": day.isoformat(), "slot": k, "clip_sha": str((s.get("clip") or {}).get("sha256") or ""),
                          "job": got["job"], "quote": {"text": got["quote"], "source_s": got["source_s"],
                                                       "clip_s": got["clip_s"]},
                          "speaker": {"name": sp["name"], "title": sp["title"], "verified": sp["verified"]},
                          "line": verified_by(sp["verified"])}
        card["words"] = {"quote": got["quote"], "name": sp["name"], "title": sp["title"],
                         "source": got["source_words"]}
        slides = [{"layout": "quote", "still": str(still), "quote": got["quote"], "name": sp["name"],
                   "title": sp["title"], "source": got["source_words"]}]
        out.append((card, slides, (1080, 1920)))
    return out


def _carousel_items(cfg: C.Config, acct, picks: List[Tuple[date, str, dict]], with_views: bool,
                    folder: Path) -> Tuple[list, list]:
    items, slides = [], []
    gta = getattr(acct, "maker", "") != "politics"
    for n, (d, k, s) in enumerate(picks, 1):
        title = title_of(s)
        if not title:
            continue
        views = views_of(acct, s) if with_views else 0
        host = str(s.get("host") or "").strip()
        when = datetime.fromisoformat(str(s["publish_at"]))
        item = {"board": d.isoformat(), "slot": k, "title": title, "host": host}
        if with_views:
            item["views"] = views
        items.append(item)
        if gta:
            slides.append({"layout": "best_item", "rank": f"#{len(items)}", "title": title,
                           "credit": f"From {host}" if host else "", "figure": _views_words(views) if with_views else
                           when.strftime("%A")})
        else:
            st = clip_still(acct, s, d, folder / f"still-src-{len(items)}.jpg")
            slides.append({"layout": "best_item" if with_views else "docket_item", "still": str(st) if st else "",
                           "title": title, "time": (when.strftime("%A ") if with_views else "")
                           + _clock_words(cfg, when), "host": host,
                           "tab": f"No. {len(items)}" if with_views else "THE DOCKET",
                           "figure": _views_words(views) if with_views else "", "source": host})
    return items, slides


def build_docket(cfg: C.Config, acct, day: date, now: datetime) -> List[tuple]:
    """Yesterday's clips that went out (3 or more), as one carousel at the docket time."""
    t = _times(acct)
    y = day - timedelta(days=1)
    picks = [(y, k, s) for k, s in posted_on(acct, y)][:CAROUSEL_MAX]
    cid = f"docket-{_hhmm(t['docket'])}"
    titled = sum(1 for _, _, s in picks if title_of(s))     # counted first: no frame is grabbed for a skipped card
    if titled < DOCKET_MIN:
        return [("skip", f"docket: {titled} clips with a title went out yesterday ({DOCKET_MIN} at the least)")]
    items, slides = _carousel_items(cfg, acct, picks, with_views=False, folder=card_dir(acct, day, cid))
    card = _base_card(cfg, acct, day, cid, "docket", cfg.slot_time(day, t["docket"]))
    card["source"] = {"board": y.isoformat(), "slots": [i["slot"] for i in items]}
    card["words"] = {"date": _day_words(y), "items": items}
    cover = {"layout": "docket_cover", "label": "THE DOCKET", "figure": str(len(items)),
             "sub": "clips on the record" if len(items) != 1 else "clip on the record", "date": _day_words(y),
             "source": ""}
    return [(card, [cover] + slides, (1080, 1350))]


def build_week_best(cfg: C.Config, acct, day: date, now: datetime) -> List[tuple]:
    """On its weekday (Mondays): the last seven days' five most watched clips, as one carousel."""
    t = _times(acct)
    wd, hm = t["week_best"]
    if day.weekday() != wd:
        return []
    pool = []
    for i in range(7, 0, -1):
        d = day - timedelta(days=i)
        for k, s in posted_on(acct, d):
            pool.append((views_of(acct, s), d, k, s))
    pool = [p for p in pool if p[0] > 0]
    pool.sort(key=lambda p: (-p[0], p[1], p[2]))
    picks = [(d, k, s) for _, d, k, s in pool[:WEEK_TOP]]
    cid = f"week_best-{_hhmm(hm)}"
    titled = sum(1 for _, _, s in picks if title_of(s))
    if titled < 3:
        return [("skip", f"week_best: {titled} clips with views and a title last week (3 at the least)")]
    items, slides = _carousel_items(cfg, acct, picks, with_views=True, folder=card_dir(acct, day, cid))
    first, last = day - timedelta(days=7), day - timedelta(days=1)
    span = f"{first.strftime('%B')} {first.day} to {last.day}, {last.year}" if first.month == last.month else \
        f"{first.strftime('%B')} {first.day} to {last.strftime('%B')} {last.day}, {last.year}"
    card = _base_card(cfg, acct, day, cid, "week_best", cfg.slot_time(day, hm))
    card["source"] = {"from": first.isoformat(), "to": last.isoformat(),
                      "slots": [f"{i['board']} {i['slot']}" for i in items]}
    card["words"] = {"from": first.isoformat(), "to": last.isoformat(), "items": items}
    if getattr(acct, "maker", "") == "politics":
        cover = {"layout": "best_cover", "label": "THE WEEK'S BEST", "figure": str(len(items)), "sub": "most watched",
                 "date": span, "source": ""}
    else:
        cover = {"layout": "best_cover", "label": "THIS WEEK'S", "figure": f"TOP {len(items)}", "date": span}
    return [(card, [cover] + slides, (1080, 1350))]


def build_countdown(cfg: C.Config, acct, day: date, now: datetime) -> List[tuple]:
    """N DAYS to GTA 6 in the account's own look only: its lockup on the kit's flat ink (launch day: OUT TODAY; none
    after). Never a frame of anyone's footage: the countdown is not about one clip, so no creator's picture belongs on
    it (a creator's frame also brought its own countdown along: a fan map's "32 DAYS TO GO" over our "30")."""
    t = _times(acct)
    days = (LAUNCH - day).days
    if days < 0:
        return []
    cid = f"countdown-{_hhmm(t['countdown'])}"
    card = _base_card(cfg, acct, day, cid, "countdown", cfg.slot_time(day, t["countdown"]))
    card["source"] = {"launch": LAUNCH.isoformat(), "art": "ink"}
    card["words"] = {"days": days, "date": _day_words(LAUNCH)}
    return [(card, [{"layout": "countdown", "days": days, "date": _day_words(LAUNCH)}], (1080, 1920))]


def build_clip_of_day(cfg: C.Config, acct, day: date, now: datetime) -> List[tuple]:
    """Yesterday's most watched clip: a clean frame of the footage it was cut from (its job's master at the clip's
    moment), its title, the creator's credit. With no master on this Mac, the title and the credit on the kit's lockup
    and flat ink. Never a frame of our delivered clip or its cover: both carry our burned-in captions and hook."""
    t = _times(acct)
    y = day - timedelta(days=1)
    pool = [(views_of(acct, s), k, s) for k, s in posted_on(acct, y)]
    pool = [p for p in pool if title_of(p[2])]
    if not pool:
        return [("skip", "clip_of_day: no clip with a title went out yesterday")]
    pool.sort(key=lambda p: (-p[0], p[1]))
    views, k, s = pool[0]
    cid = f"clip_of_day-{_hhmm(t['clip_of_day'])}"
    still = source_frame(acct, y, s, card_dir(acct, day, cid) / "still-1.jpg", prefer_game=True)
    host = str(s.get("host") or "").strip()
    card = _base_card(cfg, acct, day, cid, "clip_of_day", cfg.slot_time(day, t["clip_of_day"]))
    card["source"] = {"board": y.isoformat(), "slot": k, "clip_sha": str((s.get("clip") or {}).get("sha256") or ""),
                      "views": views, "art": "master" if still is not None else "ink",
                      "line": f"our {k} clip of {y.strftime('%b %d')}" + (f", from {host}" if host else "")}
    card["words"] = {"hook": title_of(s), "credit": f"From {host}" if host else "", "board": y.isoformat(), "slot": k}
    slide = {"layout": "clip_of_day", "still": str(still) if still else "", "hook": title_of(s),
             "credit": f"From {host}" if host else "", "label": "Clip of the day"}
    return [(card, [slide], (1080, 1920))]


# ------------------------------------------------------------------------------------------------------ news posts
_ATLAS_CACHE: Dict[str, tuple] = {}


def news_dir(acct, day) -> Path:
    """state/<slug>/cards/news/<YYYY-MM-DD>/: the news desk's requests of that day, one <id>.json each."""
    return store.path(_slug(acct), "cards", "news", str(day)[:10])


def _atlas(acct) -> Tuple[Path, dict]:
    """(the atlas folder, its entities by id) from the account's skill (the clips' own picture shelf), read once per
    change of its registry."""
    root = Path(str(getattr(acct, "skill_dir", "") or "")) / "broll-library" / "atlas"
    reg = root / "registry.json"
    try:
        stamp = reg.stat().st_mtime
    except OSError:
        return root, {}
    got = _ATLAS_CACHE.get(str(reg))
    if got and got[0] == stamp:
        return root, got[1]
    try:
        ents = json.loads(reg.read_text()).get("entities") or {}
    except (OSError, ValueError, AttributeError):
        ents = {}
    if isinstance(ents, list):
        ents = {str(e.get("id")): e for e in ents if isinstance(e, dict)}
    _ATLAS_CACHE[str(reg)] = (stamp, ents)
    return root, ents


def news_picture(acct, eid: str) -> Tuple[Optional[dict], str]:
    """The picture a news post shows: an atlas person's portrait or an agency's seal, with the atlas's own words for
    it (name, title, eyebrow) and its credit and licence. (picture, '') or (None, why not)."""
    root, ents = _atlas(acct)
    e = ents.get(str(eid or ""))
    if not isinstance(e, dict):
        return None, f"picture {eid!r} is not in the atlas"
    kind = NEWS_PICTURE.get(str(e.get("kind") or ""))
    if not kind:
        return None, f"picture {eid} is a {e.get('kind')} card: a news post shows a person or an agency"
    img = next((root / "img" / f"{eid}{x}" for x in (".jpg", ".png") if (root / "img" / f"{eid}{x}").is_file()), None)
    if img is None:
        return None, f"picture {eid} has no image on this Mac"
    if not str(e.get("licence") or "").strip():
        return None, f"picture {eid} has no licence on record"
    return {"kind": kind, "path": str(img), "name": str(e.get("name") or ""), "title": str(e.get("title") or ""),
            "eyebrow": str(e.get("eyebrow") or "") if kind == "portrait" else "", "atlas": str(eid),
            "credit": str(e.get("credit") or ""), "licence": str(e.get("licence") or ""),
            "pd": str(e.get("licence_class") or "") == "pd" or "public domain" in str(e.get("licence")).lower(),
            "page": str(e.get("page") or "")}, ""


def _logos() -> dict:
    try:
        got = json.loads((LOGOS_DIR / "logos.json").read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in got.items() if isinstance(v, dict) and not k.startswith("_")}


def logo_for(acct, name: str) -> Optional[dict]:
    """A source's logo for the engine: the outlet's own mark (render/logos/logos.json; an "atlas" entry points at an
    agency's seal), else an agency's seal from the atlas by its exact name. None when there is none."""
    reg = _logos()
    got = reg.get(name)
    if got is None:
        got = next((v for k, v in reg.items() if k.lower() == str(name).lower()), None)
    root, ents = _atlas(acct)
    if isinstance(got, dict):
        if got.get("atlas"):
            p = root / "img" / f"{got['atlas']}.png"
        else:
            p = LOGOS_DIR / str(got.get("file") or "")
        if p.is_file():
            return {"name": name, "path": str(p), "how": str(got.get("how") or "recolour"),
                    "scale": float(got.get("scale") or 1.0)}
        return None
    for eid, e in ents.items():
        if isinstance(e, dict) and e.get("kind") == "agency" and str(e.get("name") or "").lower() == str(name).lower():
            p = root / "img" / f"{eid}.png"
            if p.is_file():
                return {"name": name, "path": str(p), "how": "keep", "scale": 1.15}
    return None


def _news_drawn(req: dict) -> Dict[str, str]:
    """Every piece of a request's words a person reads on the picture, by field."""
    hero = req.get("hero") if isinstance(req.get("hero"), dict) else {}
    out = {k: str(req.get(k) or "") for k in ("kicker", "headline", "line", "story")}
    for k in ("figure", "label", "quote", "name", "title"):
        if hero.get(k):
            out[k] = str(hero.get(k))
    return out


def check_news(acct, req: dict) -> Tuple[List[str], dict]:
    """Every problem with a news request (none: it may be drawn), and what it resolves to: its picture and the
    sources' logos. The rules are Andrew's: no hyphen or dash anywhere a person reads; no question for a headline; no
    party label on the picture; at least two places the facts were checked; every source shown as its own logo."""
    bad: List[str] = []
    if not isinstance(req, dict):
        return ["it is not a JSON object"], {}
    rid = str(req.get("id") or "")
    if not NEWS_ID.match(rid) or len(rid) > 32:
        bad.append("id: a few lowercase words joined by - (php-flaw), 32 characters at the most")
    at = str(req.get("at") or "")
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", at) or not ("08:00" <= at <= "22:00"):
        bad.append("at: the time it goes, HH:MM between 08:00 and 22:00")
    hero = req.get("hero") if isinstance(req.get("hero"), dict) else {}
    kind = hero.get("kind")
    if kind == "figure":
        need = ("figure", "label")
    elif kind == "quote":
        need = ("quote", "name", "title")
    else:
        need = ()
        bad.append('hero: {"kind": "figure", "figure", "label"} or {"kind": "quote", "quote", "name", "title"}')
    for k in need:
        if not str(hero.get(k) or "").strip():
            bad.append(f"hero.{k} is empty")
    if kind == "figure" and not re.search(r"\d", str(hero.get("figure") or "")):
        bad.append("hero.figure must be a number (70%, $4.5 million)")
    drawn = _news_drawn(req)
    for k in ("kicker", "headline", "line", "story"):
        if not drawn.get(k, "").strip():
            bad.append(f"{k} is empty")
    for k, v in drawn.items():
        if len(v) > NEWS_LIMITS.get(k, 200):
            bad.append(f"{k} is {len(v)} characters ({NEWS_LIMITS.get(k, 200)} at the most)")
        if NEWS_DASH.search(v):
            bad.append(f"{k} has a hyphen or a dash: reword it")
        if NEWS_PARTY.search(v):
            bad.append(f"{k} names a party: no party label on the picture")
    if "?" in drawn.get("headline", ""):
        bad.append("headline is a question: say what happened")
    cap = req.get("caption") if isinstance(req.get("caption"), dict) else {}
    for plat in ("facebook", "instagram"):
        v = str(cap.get(plat) or "")
        if not v.strip():
            bad.append(f"caption.{plat} is empty")
            continue
        if len(v) > NEWS_LIMITS[plat]:
            bad.append(f"caption.{plat} is {len(v)} characters ({NEWS_LIMITS[plat]} at the most)")
        if NEWS_DASH.search(v):
            bad.append(f"caption.{plat} has a hyphen or a dash: reword it")
        if re.search(r"https?://|www\.", v, re.I):
            bad.append(f"caption.{plat} has a link: name the sources instead")
    if len(re.findall(r"(?<!\w)#\w", str(cap.get("instagram") or ""))) > 5:
        bad.append("caption.instagram has more than 5 hashtags")
    checked = [u for u in req.get("checked") or [] if isinstance(u, str) and re.match(r"^https?://", u)]
    if len(checked) < 2:
        bad.append("checked: the two or more pages the facts were checked on (URLs)")
    names = [str(x) for x in req.get("sources") or [] if str(x).strip()]
    logos = []
    if not 1 <= len(names) <= 3:
        bad.append("sources: one to three outlets, each shown as its logo")
    for n in names[:3]:
        lg = logo_for(acct, n)
        if lg is None:
            known = ", ".join(sorted(_logos()))
            bad.append(f"sources: no logo for {n!r} (outlets with one: {known}; or an agency by its atlas name)")
        else:
            logos.append(lg)
    pic, why = news_picture(acct, str(req.get("picture") or ""))
    if pic is None:
        bad.append(why)
    return bad, {"picture": pic, "sources": logos, "names": names[:3], "checked": checked}


def _dateline(day: date) -> str:
    return f"{day.strftime('%A')} · {day.strftime('%B')} {day.day} {day.year}"


def build_news(cfg: C.Config, acct, day: date, now: datetime, only: Optional[str] = None) -> List[tuple]:
    """(card, slides, size) for each of the day's news requests that is not a card yet (or the one `only` names),
    and ("skip", why) for a request that breaks a rule."""
    out: List[tuple] = []
    folder = news_dir(acct, day)
    if not folder.is_dir():
        return out
    have = read_board(acct, day).get("cards") or {}
    for f in sorted(folder.glob("*.json")):
        cid = f"news-{f.stem}"
        if (only and cid != only) or (not only and cid in have):
            continue
        try:
            req = json.loads(f.read_text())
        except (OSError, ValueError) as e:
            out.append(("skip", f"news {f.stem}: the request cannot be read ({e})"))
            continue
        bad, got = check_news(acct, req)
        if not bad and str(req.get("id")) != f.stem:
            bad = [f"its id {req.get('id')!r} is not its file name"]
        if bad:
            out.append(("skip", f"news {f.stem}: " + "; ".join(bad)))
            continue
        pic = got["picture"]
        cap = {p: str(req["caption"][p]).strip() for p in ("facebook", "instagram")}
        if not pic["pd"] and pic.get("credit"):          # a licence that asks for credit gets it in the post's words
            for p in cap:
                cap[p] += f"\n\nPicture: {pic['credit']}, {pic['licence']}"
        card = _base_card(cfg, acct, day, cid, "news", cfg.slot_time(day, str(req["at"])))
        card["source"] = {"request": str(f), "by": str(req.get("by") or "news desk"), "checked": got["checked"],
                          "picture": {k: pic[k] for k in ("atlas", "credit", "licence", "page")},
                          "sources": got["names"]}
        words = {"dateline": _dateline(day), "kicker": str(req["kicker"]), "headline": str(req["headline"]),
                 "line": str(req["line"]), "hero": {k: str(v) for k, v in req["hero"].items()},
                 "story": str(req["story"]), "sources": got["names"],
                 "picture": {k: pic[k] for k in ("name", "title", "eyebrow")}, "caption": cap}
        card["words"] = words
        picture = {k: pic[k] for k in ("kind", "path", "name", "title", "eyebrow")}
        common = {"dateline": words["dateline"], "sources": got["sources"], "picture": picture}
        slides = [dict(common, layout="news", kicker=words["kicker"], headline=words["headline"], line=words["line"]),
                  dict(common, layout="news_more", hero=words["hero"], story=words["story"])]
        out.append((card, slides, (1080, 1350)))
    return out


def add_news(cfg: C.Config, acct, req: dict, by: str, now: Optional[datetime] = None) -> List[str]:
    """Line a news request up for today (checked first; the card post cap a day at the most) and draw it now
    when cards may be drawn. What it did, line by line."""
    now = _now(cfg, now)
    day = _local(cfg, now).date()
    if "news" not in kinds_for(acct):
        raise SystemExit(f"{acct.name}: news posts are not switched on (factory.py social set {_slug(acct)} cards.kinds "
                         "news)")
    req = dict(req or {})
    req["by"] = by or str(req.get("by") or "") or "news desk"
    req["added_at"] = _iso(now)
    bad, _ = check_news(acct, req)
    if bad:
        raise SystemExit("Not added: " + "; ".join(bad))
    folder = news_dir(acct, day)
    store._inside(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{req['id']}.json"
    if path.exists():
        raise SystemExit(f"Not added: news {req['id']} is already lined up today")
    have = sorted(p.stem for p in folder.glob("*.json"))
    cap = day_cap(acct)
    if len(have) >= cap:
        raise SystemExit(f"Not added: {len(have)} news posts are lined up today ({', '.join(have)}), {cap} a day at "
                         "the most")
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(req, indent=1, ensure_ascii=False))
    os.replace(tmp, path)
    lines = [f"  {acct.name} news {req['id']}: lined up for {req['at']}"]
    if can_draw(cfg, now) and SW.mode(acct, "cards.make") != SW.OFF:
        with store.try_lock(f"cards-make-{_slug(acct)}") as mine:
            if mine:
                lines += make(cfg, acct, day, now, kinds=["news"], mark=False)
            else:
                lines.append("  cards are being drawn right now: the studio timer draws it within 10 minutes")
    else:
        lines.append("  it is drawn when cards.make is on, between 06:26 and 20:45")
    return lines


def make_news(cfg: C.Config, acct, now: datetime) -> List[str]:
    """Draw today's news requests that are not cards yet (the desk lines them up through the day), under the lock
    the day's make holds."""
    today = _local(cfg, now).date()
    if "news" not in kinds_for(acct) or not can_draw(cfg, now) or SW.mode(acct, "cards.make") == SW.OFF:
        return []
    folder = news_dir(acct, today)
    if not folder.is_dir():
        return []
    bd = read_board(acct, today)
    have, refused = bd.get("cards") or {}, bd.get("news_refused") or {}
    waiting = [p for p in sorted(folder.glob("*.json")) if f"news-{p.stem}" not in have and p.stem not in refused]
    if not waiting:
        return []
    lines: List[str] = []
    broke = {}
    for p in waiting:                            # a request that breaks a rule is said once, not every 10 minutes
        try:
            bad, _ = check_news(acct, json.loads(p.read_text()))
        except (OSError, ValueError) as e:
            bad = [f"the request cannot be read ({e})"]
        if bad:
            broke[p.stem] = "; ".join(bad)[:400]
            lines.append(f"  {acct.name} news {p.stem}: not drawn, {broke[p.stem]}")
    if broke:
        with edit_board(acct, today) as b2:
            _touch(b2)
            b2.setdefault("news_refused", {}).update({k: {"at": _iso(now), "why": v} for k, v in broke.items()})
    if len(broke) == len(waiting):
        return lines
    with store.try_lock(f"cards-make-{_slug(acct)}") as mine:
        if mine:
            lines += make(cfg, acct, today, now, kinds=["news"], mark=False)
    return lines


def news_caption(card: dict, outlet: str) -> str:
    """The signed words a news post goes with on that outlet ('' for any other card)."""
    if card.get("kind") != "news":
        return ""
    plat = str(outlet or "").split("_")[0]
    return str(((card.get("words") or {}).get("caption") or {}).get(plat) or "")


BUILDERS = {"quote": build_quotes, "docket": build_docket, "week_best": build_week_best,
            "countdown": build_countdown, "clip_of_day": build_clip_of_day, "news": build_news}


# ------------------------------------------------------------------------------------------------------ making
def _finish_card(cfg: C.Config, acct, day: date, card: dict, slides: list, size, keep: Optional[dict]) -> dict:
    if keep:                                    # Make again keeps the time he set and the notes
        card["at"] = keep.get("at") or card["at"]
        card["notes"] = list(keep.get("notes") or [])
    got = render(cfg, acct, day, card, slides, size)
    card.update(got)
    card["signoff"] = None
    card["outlets"] = {}
    card["status"] = "ready" if card.get("targets") else "draft"
    if not card.get("targets"):
        card["notes"].append(f"{B.utcnow()} nowhere to post it: cards.targets has no "
                             + " or ".join(KIND_TARGETS.get(card["kind"], ())))
    return card


def make(cfg: C.Config, acct, day: date, now: Optional[datetime] = None, kinds: Optional[Sequence[str]] = None,
         only: Optional[str] = None, again: bool = False, by: str = "", mark: bool = True) -> List[str]:
    """Draw the day's cards (each kind once; `again` or `only` draws one again). What it did, line by line. mark=False
    (a news request drawn through the day) leaves the day's "made" alone, so the daily make still runs."""
    now = _now(cfg, now)
    _need_draw(cfg, now)
    have = read_board(acct, day).get("cards") or {}
    want = [k for k in (kinds or kinds_for(acct)) if k in BUILDERS]
    if only:
        kind = str((have.get(only) or {}).get("kind") or only.rsplit("-", 1)[0])
        want = [kind] if kind in BUILDERS else []
    lines: List[str] = []
    skipped: List[dict] = []                 # why a card was not made, kept for the Cards tab
    for kind in want:
        try:
            built = (BUILDERS[kind](cfg, acct, day, now, only=only) if kind in ("quote", "news")
                     else BUILDERS[kind](cfg, acct, day, now))
        except SystemExit as e:
            lines.append(f"  {acct.name} {kind}: {e}")
            skipped.append({"kind": kind, "words": str(e)[:300]})
            continue
        except Exception as e:  # noqa: BLE001  (one kind never stops the others)
            lines.append(f"  {acct.name} {kind}: could not make it ({type(e).__name__}: {e})"[:300])
            skipped.append({"kind": kind, "words": f"could not make it ({type(e).__name__}: {e})"[:300]})
            continue
        for got in built:
            if got[0] == "skip":
                lines.append(f"  {acct.name} {got[1]}")
                skipped.append({"kind": kind, "words": str(got[1])[:300]})
                continue
            card, slides, size = got
            if only and kind not in ("quote", "news"):    # one card of its kind a day: Make again keeps its id
                card["id"] = only
            old = have.get(card["id"])
            if only and card["id"] != only:
                continue
            if old and not (again or only):
                continue
            if old and old.get("status") in ("scheduled", "posted"):
                lines.append(f"  {acct.name} {card['id']}: it is {old['status']}; it is not made again")
                continue
            try:
                card = _finish_card(cfg, acct, day, card, slides, size, old if (old and only) else None)
            except SystemExit as e:
                lines.append(f"  {acct.name} {card['id']}: {e}")
                skipped.append({"kind": kind, "id": card["id"], "words": str(e)[:300]})
                continue
            if old and (again or only):
                card["notes"].append(f"{B.utcnow()} made again" + (f" by {by}" if by else ""))
            new_run, kept, was = _run_of(card), False, None
            with edit_board(acct, day) as bd:
                _touch(bd)
                cur = bd["cards"].get(card["id"])
                # the card as it is NOW decides: queued or posted (or signed meanwhile, for the daily make) keeps it,
                # and this drawing is dropped; the files the card points at were never touched
                if isinstance(cur, dict) and (cur.get("status") in ("scheduled", "posted") or
                                              (cur.get("status") in SIGNED and not (again or only))):
                    kept = True
                else:
                    was = _run_of(cur) if isinstance(cur, dict) else None
                    bd["cards"][card["id"]] = card
            if kept:
                _drop_run(new_run)
                lines.append(f"  {acct.name} {card['id']}: it was signed meanwhile; it is kept as signed")
                continue
            if was is not None and was != new_run:
                _drop_run(was)               # the card no longer points at it (its queued action, if any, is refused)
            lines.append(f"  {acct.name} {card['id']}: made ({len(card['files']['images'])} "
                         f"{'slide' if len(card['files']['images']) == 1 else 'slides'}), "
                         f"{'waits for Approve' if card['status'] == 'ready' else 'nowhere to post it'}")
    if not only and mark:
        with edit_board(acct, day) as bd:
            _touch(bd)
            bd["made"] = {"at": _iso(now), "kinds": want, "skipped": skipped[:40]}
    return lines


def remake(cfg: C.Config, acct, day: date, cid: str, by: str, now: Optional[datetime] = None) -> str:
    card = get_card(acct, day, cid)
    if card is None:
        raise SystemExit(f"{cid}: no such card on {day}")
    if card.get("status") in ("scheduled", "posted"):
        raise SystemExit(f"{cid} is {card['status']}: it is not made again")
    lines = make(cfg, acct, day, now, only=cid, by=by)
    if not any(f"{cid}: made (" in ln for ln in lines):
        raise SystemExit("; ".join(x.strip() for x in lines) or f"{cid}: there is nothing to make it from now")
    return f"{KIND_WORDS.get(card['kind'], card['kind'])} made again: look at it, then Approve it"


# ------------------------------------------------------------------------------------------------------ signing
def _card_words(cfg: C.Config, card: dict) -> str:
    t = post_time(card)
    return (f"{KIND_WORDS.get(card.get('kind'), card.get('kind'))} at {_clock_words(cfg, t) if t else '?'} to "
            + (", ".join(TARGET_WORDS.get(x, x) for x in card.get("targets") or []) or "nowhere"))


def day_cap(acct) -> int:
    """Card posts a day on each platform (outward's card_post cap for this account)."""
    return int(SW.caps_for(acct, "card_post", False)[1])


def _plats(card: dict) -> List[str]:
    return list(dict.fromkeys(str(t).split("_")[0] for t in card.get("targets") or []))


def _cap_problem(acct, cards: Dict[str, dict], card: dict) -> str:
    """'' when signing this card keeps every platform it goes to within the day's card posts; else why not. Counts the
    cards of that day's board already signed, queued or posted."""
    cap = day_cap(acct)
    for plat in _plats(card):
        taken = [c for c in cards.values() if isinstance(c, dict) and c.get("id") != card.get("id")
                 and c.get("status") in SIGNED + ("posted",) and plat in _plats(c)]
        if len(taken) >= cap:
            names = ", ".join(f"{KIND_WORDS.get(c.get('kind'), c.get('id'))} ({c.get('status')})" for c in taken)
            return (f"{plat.capitalize()} takes {cap} card {'post' if cap == 1 else 'posts'} a day at the most, and "
                    f"{names} {'has' if len(taken) == 1 else 'have'} them: decline one of those first")
    return ""


def show_news(cfg: C.Config, acct, day, nid: str) -> str:
    """One drawn news post as Andrew is sent it on his phone: state, time, words, each picture and the hash that signs
    exactly these pictures (`cards news sign`)."""
    card = (read_board(acct, day).get("cards") or {}).get(f"news-{nid}")
    if not isinstance(card, dict):
        raise SystemExit(f"news {nid}: not drawn on {day} (cards news list says why)")
    req = (card.get("source") or {}).get("request") or ""
    try:
        cap = (json.loads(Path(req).read_text()).get("caption") or {}) if req else {}
    except (OSError, ValueError):
        cap = {}
    w = card.get("words") or {}
    at = _when(card.get("at"))
    lines = [f"  news {nid}: {card.get('status')}, goes {_local(cfg, at).strftime('%H:%M') if at else '?'} to "
             f"{', '.join(card.get('targets') or []) or 'nowhere'}",
             f"  headline: {w.get('headline') or ''}", f"  kicker: {w.get('kicker') or ''}"]
    imgs = ((card.get("files") or {}).get("images") or [])
    lines.append(f"  slides: {len(imgs)}")
    lines += [f"  picture: {i.get('path')}" for i in imgs]
    for plat in ("facebook", "instagram"):
        if cap.get(plat):
            lines.append(f"  {plat} caption: " + str(cap[plat]).replace("\n", " / "))
    bad = file_problems(card, cached=False)
    lines.append(f"  problems: {'; '.join(bad)}" if bad else f"  hash: {card_hash(card)}")
    return "\n".join(lines)


def approve(cfg: C.Config, acct, day, cid: str, seen: str, by: str, now: Optional[datetime] = None) -> str:
    """Sign one card exactly as the page showed it (seen = its hash then)."""
    now = _now(cfg, now)
    with edit_board(acct, day) as bd:
        _touch(bd)
        card = bd["cards"].get(cid)
        if not isinstance(card, dict):
            raise SystemExit(f"{cid}: no such card on {day}")
        if card.get("status") not in OPEN:
            raise SystemExit(f"It is {card.get('status')}: only a card waiting for Approve can be signed.")
        if not card.get("targets"):
            raise SystemExit("It has nowhere to go: add a target to cards.targets, then Make again.")
        bad = file_problems(card, cached=False)
        if bad:
            raise SystemExit("It cannot be signed: " + "; ".join(bad) + ". Make it again.")
        h = card_hash(card)
        if not seen or seen != h:
            raise SystemExit("The card changed after the page showed it: look at it again, then Approve.")
        end = _when(card.get("at"))
        if end is not None and now > end + EXPIRE_AFTER and card.get("kind") != "quote":
            raise SystemExit("Its time has passed: Change time first.")
        full = _cap_problem(acct, bd["cards"], card)
        if full:
            raise SystemExit(f"Not signed: {full}.")
        card["signoff"] = {"by": by, "at": _iso(now), "hash": h}
        card["status"] = "approved"
        card.setdefault("notes", []).append(f"{B.utcnow()} signed by {by}")
        news = card.get("kind") == "news"
        if news:                                # it goes now, not at the desk's time (post_at is not in the hash)
            card["post_at"] = _iso(now)
        words = _card_words(cfg, card)
    if not news:
        return f"Signed: {words}."
    mode = SW.mode(acct, "cards.post")
    if mode == SW.OFF:
        return f"Signed: {words}. News posts are switched off (cards.post): it waits until they are on."
    return f"Signed: {words}. " + ("Practice only: the words are logged, nothing is posted. " if mode == "practice"
                                   else "Going now. ") + f"({kick(cfg)})"


_KIDS: List[subprocess.Popen] = []


def kick(cfg: C.Config) -> str:
    """The studio's step now, in the background (poster.kick's way), so a news post signed a moment ago is queued and
    sent at once instead of at the timer's next turn (every 10 minutes). Never from a test or an offline run."""
    from . import net
    why = net.why_not()
    if why:
        return f"sending not started: {why}"
    log = C.STATE / "logs" / "studio.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLIP_FACTORY_LOG_STAMP="1", CLIP_FACTORY_CONFIG=str(cfg.path), CLIP_FACTORY_STATE=str(C.STATE))
    _KIDS[:] = [p for p in _KIDS if p.poll() is None]      # reap the finished ones (the review page runs for days)
    with open(log, "a") as out:
        _KIDS.append(subprocess.Popen([sys.executable, str(C.ROOT / "factory.py"), "social", "studio", "--quiet"],
                                      cwd=str(C.ROOT), env=env, stdin=subprocess.DEVNULL, stdout=out,
                                      stderr=subprocess.STDOUT, start_new_session=True))
    return "sending started"


def approve_all(cfg: C.Config, acct, day, seen: Dict[str, str], by: str, now: Optional[datetime] = None) -> str:
    """Sign each card the page showed, earliest first, until a platform's card posts for the day are taken: the rest
    stay unsigned and say why."""
    done, why = [], []
    have = read_board(acct, day).get("cards") or {}

    def order(cid: str):
        t = post_time(have.get(cid) or {})
        return (t.isoformat() if t else "9", cid)
    for cid in sorted(seen or {}, key=order):
        try:
            approve(cfg, acct, day, cid, str(seen[cid]), by, now)
            done.append(cid)
        except SystemExit as e:
            why.append(f"{cid}: {e}")
    if not done:
        raise SystemExit("Nothing was signed. " + " ".join(why))
    return f"Signed {len(done)}: {', '.join(done)}." + (f" Not signed: {' '.join(why)}" if why else "")


def decline(cfg: C.Config, acct, day, cid: str, reasons: Sequence[str], note: str, by: str,
            now: Optional[datetime] = None) -> str:
    bad = [r for r in reasons or [] if r not in DECLINE_CODES]
    if bad:
        raise SystemExit(f"{', '.join(bad)}: not a reason (the reasons: {', '.join(DECLINE_CODES)})")
    with edit_board(acct, day) as bd:
        _touch(bd)
        card = bd["cards"].get(cid)
        if not isinstance(card, dict):
            raise SystemExit(f"{cid}: no such card on {day}")
        if card.get("status") == "posted":
            raise SystemExit("It is posted: it can no longer be declined here.")
        going = _on_its_way(acct, day, card)
        if going:
            raise SystemExit(f"It is on its way to {going}: it can no longer be declined here.")
        card["status"] = "declined"
        card["signoff"] = None
        card["declined"] = {"by": by, "at": _iso(_now(cfg, now)), "reasons": list(reasons or []),
                            "note": str(note or "")[:300]}
        words = ", ".join(lbl for c, lbl in DECLINE if c in (reasons or []))
        card.setdefault("notes", []).append(f"{B.utcnow()} declined by {by}" + (f" ({words})" if words else "")
                                            + (f": {str(note)[:200]}" if note else ""))
    return "Declined: it will not post."


def _on_its_way(acct, day, card: dict) -> str:
    """The targets a queued card is already being sent to (outward's politics says sending or done), in words; '' when
    none: a card still waiting in the queue can be declined or re-timed, and its queued action is then refused at
    send time (still_signed), so nothing goes that is not signed as it is."""
    from . import outward as OW
    if card.get("status") != "scheduled":
        return ""
    out = []
    for target in card.get("targets") or []:
        plat = str(target).split("_")[0]
        key = f"card:{plat}:{str(day)[:10]}/{card.get('id')}:{target}"
        try:
            st = OW.key_state(None, acct, key)
        except Exception:  # noqa: BLE001
            st = ""
        if st in ("sending", "done"):
            out.append(TARGET_WORDS.get(target, target))
    return " and ".join(out)


def set_time(cfg: C.Config, acct, day, cid: str, hhmm: str, by: str, now: Optional[datetime] = None) -> str:
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(hhmm or "")):
        raise SystemExit("A time like 18:30.")
    h, m = (int(x) for x in hhmm.split(":"))
    if (h, m) < POST_WINDOW[0] or (h, m) >= POST_WINDOW[1]:
        raise SystemExit("Cards go between 08:00 and 23:30.")
    d = date.fromisoformat(str(day)[:10])
    with edit_board(acct, day) as bd:
        _touch(bd)
        card = bd["cards"].get(cid)
        if not isinstance(card, dict):
            raise SystemExit(f"{cid}: no such card on {day}")
        if card.get("status") == "posted":
            raise SystemExit("It is posted: its time can no longer change.")
        if card.get("kind") == "quote":
            raise SystemExit("A quote card goes 60 to 90 minutes after its clip is live; its time follows the clip.")
        going = _on_its_way(acct, day, card)
        if going:
            raise SystemExit(f"It is on its way to {going}: its time can no longer change.")
        was = card.get("status")
        card["at"] = _iso(cfg.slot_time(d, hhmm))
        card.pop("post_at", None)
        if was in SIGNED + ("expired", "declined", "skipped", "failed"):
            card["signoff"] = None             # a new time is a new card to sign: what was queued is refused at send
            card["status"] = "ready" if card.get("targets") else "draft"
            card["outlets"] = {}
        card.setdefault("notes", []).append(f"{B.utcnow()} time set to {hhmm} by {by}")
    return f"Its time is now {hhmm}" + (": Approve it again." if was not in OPEN else ".")


# ------------------------------------------------------------------------------------------------------ the story rule
def read_rule(acct) -> dict:
    raw = store.read(rules_path(acct), {})
    got = raw.get("story_repost") if isinstance(raw, dict) else None
    out = json.loads(json.dumps(STORY_RULE))
    if isinstance(got, dict):
        out.update({k: v for k, v in got.items() if k in STORY_RULE})
    return out


def rule_hash(rule: dict) -> str:
    return _sha({k: rule.get(k) for k in ("platforms", "delay_min", "per_day", "which", "max_seconds")})


def rule_signed(rule: dict) -> bool:
    so = rule.get("signoff") or {}
    return rule.get("state") == "signed" and bool(so.get("hash")) and so["hash"] == rule_hash(rule)


def _rule_fields(fields: dict, cur: dict) -> dict:
    out = {k: cur.get(k) for k in ("platforms", "delay_min", "per_day", "which", "max_seconds")}
    if not isinstance(fields, dict):
        return out
    if "platforms" in fields:
        p = fields["platforms"]
        if not isinstance(p, list) or not p or any(x not in ("facebook", "instagram") for x in p):
            raise SystemExit("platforms: facebook, instagram, or both")
        out["platforms"] = [x for x in ("facebook", "instagram") if x in p]
    if "which" in fields:
        if fields["which"] not in ("all", "top"):
            raise SystemExit("which: all (every signed clip) or top (the clips the coach scored 7 or more out of 10)")
        out["which"] = fields["which"]
    if "per_day" in fields:
        n = fields["per_day"]
        try:
            n = int(n)
        except (TypeError, ValueError):
            raise SystemExit("per_day: a whole number") from None
        if not 1 <= n <= SW.HARD[("story", "auto")][1]:
            raise SystemExit(f"per_day: 1 to {SW.HARD[('story', 'auto')][1]}")
        out["per_day"] = n
    if "delay_min" in fields:
        v = fields["delay_min"]
        try:
            a, b = int(v[0]), int(v[1])
        except (TypeError, ValueError, IndexError):
            raise SystemExit("delay_min: two numbers of minutes, [from, to]") from None
        if not 30 <= a <= b <= 240:
            raise SystemExit("delay_min: from 30 minutes (never within 30 minutes of the Reel) to 240")
        out["delay_min"] = [a, b]
    out["max_seconds"] = STORY_MAX_S
    return out


def rule_words(rule: dict) -> str:
    plats = " and ".join(p.capitalize() for p in rule.get("platforms") or []) or "no platform"
    d0, d1 = (rule.get("delay_min") or [30, 60])[:2]
    which = "every signed clip" if rule.get("which") == "all" else "the clips the coach scored 7 or more out of 10"
    return (f"Each Reel live on {plats} goes to that platform's story {d0} to {d1} minutes later, as the same signed "
            f"bytes, 60 seconds at the most, {rule.get('per_day')} a day at the most, for {which}.")


def sign_rule(cfg: C.Config, acct, fields: dict, seen_version, by: str, now: Optional[datetime] = None) -> str:
    now = _now(cfg, now)
    with store.edit(rules_path(acct), dict) as raw:
        cur = read_rule(acct) if not isinstance(raw.get("story_repost"), dict) else \
            dict(json.loads(json.dumps(STORY_RULE)), **raw["story_repost"])
        if seen_version is not None and str(seen_version) != str(cur.get("version")):
            raise SystemExit("The rule changed after the page showed it: look at it again.")
        new = _rule_fields(fields, cur)
        changed = any(new[k] != cur.get(k) for k in new)
        if changed:
            cur.update(new)
            cur["version"] = int(cur.get("version") or 1) + 1
        cur["signoff"] = {"by": by, "at": _iso(now), "hash": rule_hash(cur), "version": cur["version"]}
        cur["state"] = "signed"
        hist = list(cur.get("history") or [])[-40:]
        hist.append({"at": _iso(now), "by": by, "what": "signed", "version": cur["version"]})
        cur["history"] = hist
        raw["story_repost"] = cur
        words = rule_words(cur)
    return f"Story reposts signed (version {cur['version']}): {words}"


def pause_rule(cfg: C.Config, acct, by: str, now: Optional[datetime] = None) -> str:
    now = _now(cfg, now)
    with store.edit(rules_path(acct), dict) as raw:
        cur = dict(json.loads(json.dumps(STORY_RULE)), **(raw.get("story_repost") or {}))
        cur["state"] = "paused"
        hist = list(cur.get("history") or [])[-40:]
        hist.append({"at": _iso(now), "by": by, "what": "paused", "version": cur.get("version")})
        cur["history"] = hist
        raw["story_repost"] = cur
    return "Story reposts are paused: none is queued until the rule is signed again."


# ------------------------------------------------------------------------------------------------------ story reposts
def _coach_total(acct, day, k: str) -> Optional[float]:
    """The coach's score of a clip out of 10 (coach/<day>/<HHMM>.json, ARCHITECTURE 3.7): the mean of the written
    layer's five scores (each 1 to 10) once the clip-coach task wrote them. None until then: the measured total is
    mechanics only (97 to 100 on nearly every clip, 2026-10-20), so it never picks the top clips."""
    got = store.read(store.path(_slug(acct), "coach", str(day), f"{_hhmm(k)}.json"), {})
    got = got if isinstance(got, dict) else {}
    w = got.get("written") if isinstance(got.get("written"), dict) else {}
    sc = [v for v in (w.get("scores") or {}).values() if isinstance(v, (int, float)) and not isinstance(v, bool)] \
        if isinstance(w.get("scores"), dict) else []
    if sc:
        return round(sum(float(v) for v in sc) / len(sc), 2)
    return None


def _stories_today(acct, day) -> int:
    st = read_board(acct, day).get("stories") or {}
    return sum(1 for v in st.values() if isinstance(v, dict) and v.get("state") not in GIVEN_UP + ("off",))


def story_for(cfg: C.Config, acct, day, s: dict, outlet: str, rec: dict, now: Optional[datetime] = None) -> str:
    """Queue the story repost of a Reel that just went live, when the switch and the signed rule allow it. What it
    did ('' for nothing). Reads nothing at all while the switch is off."""
    plat = STORY_OUTLETS.get(outlet)
    if not plat or _is_client(acct):
        return ""
    mode = SW.mode(acct, "cards.story_repost")
    if mode == SW.OFF:
        return ""
    now = _now(cfg, now)
    day = date.fromisoformat(str(day)[:10])
    k = str(s.get("slot") or "")
    rule = read_rule(acct)
    if not rule_signed(rule) or plat not in (rule.get("platforms") or []):
        return ""
    key = f"{k}|{plat}"
    if key in (read_board(acct, day).get("stories") or {}):
        return ""
    why = ""
    live = _when(rec.get("published_at"))
    src = (s.get("cuts") or {}).get(outlet) or s.get("clip") or {}
    path = Path(str(src.get("path") or ""))
    secs = float(src.get("seconds") or 0)
    sha = str(src.get("sha256") or "")
    if rec.get("provider") != "direct" or live is None or not rec.get("post_id"):
        return ""
    if s.get("status") not in B.SIGNED or not (s.get("signoff") or {}).get("hash"):
        why = "the clip is not signed"
    elif rec.get("clip_sha") and sha and rec["clip_sha"] != sha:
        why = "the platform has another file than the signed one"
    elif secs <= 0 or secs > min(float(rule.get("max_seconds") or STORY_MAX_S), STORY_MAX_S):
        why = (f"{secs:.0f} seconds is over a story's 60" + (" (a Politics clip goes only with its signed Instagram cut)"
                                                             if getattr(acct, "maker", "") == "politics" else ""))
    elif not path.is_file():
        why = "its file is gone"
    elif rule.get("which") == "top" and _coach_total(acct, day, k) is None:
        why = "the coach has not written a score for it yet"
    elif rule.get("which") == "top" and (_coach_total(acct, day, k) or 0) < TOP_SCORE:
        why = "not one of the top clips (the coach's score)"
    elif _stories_today(acct, day) >= min(int(rule.get("per_day") or 4), int(SW.settings(acct)["cards.story_per_day"])):
        why = "the day's story reposts are used"
    if why:
        with edit_board(acct, day) as bd:
            _touch(bd)
            bd["stories"][key] = {"state": "skipped", "why": why, "at": _iso(now)}
        return f"story repost {k} {plat}: skipped, {why}"
    from . import outward as OW
    d0, d1 = (rule.get("delay_min") or [30, 60])[:2]
    nb = live + timedelta(minutes=OW.RNG.randint(int(d0), int(d1)))
    so = rule.get("signoff") or {}
    try:
        aid = OW.queue(cfg, acct, plat, "story", key=f"story:{plat}:{day.isoformat()} {k}", text="",
                       target={"board": day.isoformat(), "slot": k, "outlet": f"{plat}_story", "from": outlet,
                               "post_id": str(rec.get("post_id") or ""), "file": str(path), "sha": sha,
                               "seconds": secs},
                       origin={"by": "cards", "rule": f"story_repost@v{rule.get('version')}", "via": "studio"},
                       signed={"what": f"cards-rule:story_repost@v{rule.get('version')}", "by": so.get("by"),
                               "at": so.get("at"), "hash": so.get("hash"), "clip_sha": sha},
                       outlet=f"{plat}_story", runner="studio", not_before=nb, expires=live + timedelta(hours=8),
                       now=now)
    except SystemExit as e:
        return f"story repost {k} {plat}: {e}"
    with edit_board(acct, day) as bd:
        _touch(bd)
        bd["stories"][key] = {"state": "queued", "action": aid, "at": _iso(now), "not_before": _iso(nb),
                              "mode": mode, "file": path.name}
    return f"story repost {k} {plat}: queued for {_clock_words(cfg, nb)}"


def on_posted(cfg: C.Config, acct, day, slot: dict, outlet: str, rec: dict, now=None) -> Optional[str]:
    """The poster's follow-up (social.on_posted): a Reel went live, so its story repost is queued (switch and signed
    rule permitting). Only queues and writes the cards board."""
    if outlet not in STORY_OUTLETS or SW.mode(acct, "cards.story_repost") == SW.OFF:
        return None
    words = story_for(cfg, acct, day, slot, outlet, rec, now)
    return words or None


def _story_sweep(cfg: C.Config, acct, now: datetime) -> List[str]:
    """A Reel whose follow-up was missed (the poster stopped, the review page was down): queued here within 3 hours
    of going live."""
    if SW.mode(acct, "cards.story_repost") == SW.OFF:
        return []
    out = []
    today = _local(cfg, now).date()
    for k, s in posted_on(acct, today):
        for outlet, rec in _posted(s):
            live = _when(rec.get("published_at"))
            if outlet in STORY_OUTLETS and live is not None and timedelta(0) <= _utc(now) - live <= timedelta(hours=3):
                w = story_for(cfg, acct, today, s, outlet, rec, now)
                if w:
                    out.append(f"  {acct.name} {w}")
    return out


# ------------------------------------------------------------------------------------------------------ the day's run
def _reel_near(cfg: C.Config, acct, plat: str, now: datetime) -> Optional[datetime]:
    """A Reel of this account on this platform due or live within 30 minutes of now: the time the card may go."""
    outlet = {"facebook": "facebook_reels", "instagram": "instagram_reels"}.get(plat)
    if not outlet or outlet not in (getattr(acct, "platforms", None) or []):
        return None
    gap = timedelta(seconds=SW.REEL_GAP_S)
    t = _utc(now)
    best = None
    lt = _local(cfg, now).date()
    for d in (lt - timedelta(days=1), lt):
        for k, s in sorted((_clip_board(acct, d).get("slots") or {}).items()):
            if not isinstance(s, dict) or s.get("status") not in B.SIGNED:
                continue
            rec = (s.get("outlets") or {}).get(outlet) or {}
            when = _when(rec.get("published_at")) or _when(s.get("publish_at"))
            if when is None:
                continue
            if when - gap <= t <= when + gap:
                end = when + gap
                best = end if best is None or end > best else best
    return best


def go_time(cfg: C.Config, acct, card: dict) -> Tuple[Optional[datetime], str]:
    """The earliest a card can go: its time, moved past the 30 minutes either side of any Reel on a platform it goes
    to (the day's ready and signed slots), and into 08:00 to 23:30. (time, why it moved or ''). Spacing behind
    another card and its jitter come on top; the page says "about"."""
    t = post_time(card)
    if t is None:
        return None, ""
    if card.get("kind") == "news" and card.get("post_at"):
        return t, ""                            # a signed news post goes at once: no Reel moves it
    gap = timedelta(seconds=SW.REEL_GAP_S)
    why = ""
    plats = _plats(card)
    d = _local(cfg, t).date()
    reels = []
    for plat in plats:
        outlet = {"facebook": "facebook_reels", "instagram": "instagram_reels"}.get(plat)
        if not outlet or outlet not in (getattr(acct, "platforms", None) or []):
            continue
        for dd in (d - timedelta(days=1), d):
            for k, s in sorted((_clip_board(acct, dd).get("slots") or {}).items()):
                if not isinstance(s, dict) or s.get("status") not in (B.READY,) + tuple(B.SIGNED):
                    continue
                rec = (s.get("outlets") or {}).get(outlet) or {}
                when = _when(rec.get("published_at")) or _when(s.get("publish_at"))
                if when is not None:
                    reels.append((when, k))
    moved = True
    while moved:
        moved = False
        for when, k in sorted(reels):
            if when - gap <= t < when + gap:
                t = when + gap
                why = f"the {k} Reel keeps 30 minutes clear"
                moved = True
    lt = _local(cfg, t)
    if (lt.hour, lt.minute) < POST_WINDOW[0]:
        t = lt.replace(hour=POST_WINDOW[0][0], minute=POST_WINDOW[0][1], second=0, microsecond=0)
        why = why or "cards go from 08:00"
    return t, why


def _reels_due(cfg: C.Config, acct, now: datetime) -> int:
    if "instagram_reels" not in (getattr(acct, "platforms", None) or []):
        return 0
    n = 0
    t = _utc(now)
    for k, s in sorted((_clip_board(acct, _local(cfg, now).date()).get("slots") or {}).items()):
        if not isinstance(s, dict) or s.get("status") not in B.SIGNED:
            continue
        rec = (s.get("outlets") or {}).get("instagram_reels") or {}
        when = _when(s.get("publish_at"))
        if not rec.get("post_id") and when is not None and when > t:
            n += 1
    return n


def _advance(cfg: C.Config, acct, day: date, now: datetime) -> List[str]:
    """Expire what was never signed, skip a quote whose clip did not post, and queue each signed card whose time
    came (cards.post on or practice)."""
    from . import outward as OW
    lines: List[str] = []
    bd = read_board(acct, day)
    t = _utc(now)
    post_mode = SW.mode(acct, "cards.post")
    for cid, card in sorted((bd.get("cards") or {}).items()):
        if not isinstance(card, dict):
            continue
        st = card.get("status")
        at = _when(card.get("at"))
        if st in OPEN:
            if at is not None and t > at + EXPIRE_AFTER:
                with edit_board(acct, day) as b2:
                    _touch(b2)
                    c = b2["cards"].get(cid) or {}
                    if c.get("status") in OPEN:
                        c["status"] = "expired"
                        c.setdefault("notes", []).append(f"{B.utcnow()} expired: not signed by 2 hours past its time")
                lines.append(f"  {acct.name} {cid}: expired (not signed in time)")
            continue
        if st not in SIGNED:
            continue
        upd: dict = {}
        if card.get("kind") == "quote" and card.get("after"):
            aft = card["after"]
            src = card.get("source") or {}
            s = (_clip_board(acct, date.fromisoformat(str(src.get("board") or day))).get("slots") or {}).get(
                aft.get("slot")) or {}
            live = None                       # the first of its Reels to go live (Instagram, else Facebook)
            for o in dict.fromkeys([aft.get("outlet"), "instagram_reels", "facebook_reels"]):
                rec = (s.get("outlets") or {}).get(o) or {}
                t_o = _when(rec.get("published_at")) if rec.get("provider") == "direct" and rec.get("post_id") \
                    else None
                if t_o is not None and (live is None or t_o < live):
                    live = t_o
            if str((s.get("clip") or {}).get("sha256") or "") != str(src.get("clip_sha") or ""):
                upd = {"status": "skipped", "why": "its clip changed after the card was made"}
            elif s.get("status") in (B.DECLINED, B.REFUSED, B.FAILED):
                upd = {"status": "skipped", "why": "its clip did not post"}
            elif live is not None and not card.get("post_at"):
                lo, hi = (aft.get("minutes") or [60, 90])[:2]
                span = max(0, int(hi) - int(lo))
                mins = int(lo) + (int(hashlib.sha256(cid.encode()).hexdigest(), 16) % (span + 1) if span else 0)
                upd = {"post_at": _iso(live + timedelta(minutes=mins))}
            elif live is None and at is not None and t > at + EXPIRE_AFTER:
                upd = {"status": "skipped", "why": "its clip did not post"}
            if upd:
                with edit_board(acct, day) as b2:
                    _touch(b2)
                    c = b2["cards"].get(cid) or {}
                    if "status" in upd:
                        c["status"] = upd["status"]
                        c.setdefault("notes", []).append(f"{B.utcnow()} skipped: {upd['why']}")
                    else:
                        c["post_at"] = upd["post_at"]
                    card = dict(c)
                if "status" in upd:
                    lines.append(f"  {acct.name} {cid}: skipped, {upd['why']}")
                    continue
            if not card.get("post_at"):
                continue
        when = post_time(card)
        if post_mode == SW.OFF or when is None or t < when:
            continue
        if t > when + POST_LATE and card.get("status") == "approved":
            with edit_board(acct, day) as b2:
                _touch(b2)
                c = b2["cards"].get(cid) or {}
                c["status"] = "skipped"
                c.setdefault("notes", []).append(f"{B.utcnow()} skipped: it could not go within 3 hours of its time")
            lines.append(f"  {acct.name} {cid}: skipped, it could not go within 3 hours of its time")
            continue
        bad = signed_ok(card)
        if bad:
            with edit_board(acct, day) as b2:
                _touch(b2)
                c = b2["cards"].get(cid) or {}
                c["status"] = "failed"
                c.setdefault("notes", []).append(f"{B.utcnow()} not sent: {bad}")
            lines.append(f"  {acct.name} {cid}: not sent, {bad}")
            continue
        so = card.get("signoff") or {}
        for target in card.get("targets") or []:
            if (card.get("outlets") or {}).get(target):
                continue
            plat = target.split("_")[0]
            near = None if card.get("kind") == "news" else _reel_near(cfg, acct, plat, now)   # news: at once
            key = f"card:{plat}:{day.isoformat()}/{cid}:{target}"
            if OW.key_state(cfg, acct, key) in ("queued", "sending"):
                # a try queued for an earlier signature (the card was declined or re-timed and signed again): it is
                # refused when it comes up (still_signed), then this one is queued
                lines.append(f"  {acct.name} {cid} {target}: waits for its earlier queued try to clear")
                continue
            try:
                aid = OW.queue(cfg, acct, plat, "card_post", key=key,
                               text=news_caption(card, target),     # a news post's signed words; '' for the rest
                               target={"board": day.isoformat(), "item": cid, "outlet": target,
                                       "kind": card.get("kind")},
                               origin={"by": "cards", "rule": None, "via": "studio"},
                               signed={"what": f"card:{day.isoformat()}/{cid}", "by": so.get("by"), "at": so.get("at"),
                                       "hash": so.get("hash")},
                               outlet=target, runner="studio", not_before=max(when, near) if near else when,
                               expires=card_expiry(acct, when), now=now)
                rec = {"state": "queued", "action": aid, "at": _iso(now), "mode": post_mode}
                lines.append(f"  {acct.name} {cid}: queued for {TARGET_WORDS.get(target, target)}")
            except SystemExit as e:
                rec = {"state": "skipped", "why": str(e)[:300], "at": _iso(now)}
                lines.append(f"  {acct.name} {cid} {target}: {e}")
            with edit_board(acct, day) as b2:
                _touch(b2)
                c = b2["cards"].get(cid) or {}
                c.setdefault("outlets", {})[target] = rec
                if c.get("status") == "approved":
                    c["status"] = "scheduled"
    return lines


def card_expiry(acct, when: datetime) -> datetime:
    """How long a queued card may wait in outward: 3 hours past its time, plus one full card_post spacing and its
    jitter and 30 minutes, so a try that failed (outward set the 3 hour spacing when it began) still gets its retries
    before the action expires, and a card queued behind another card's spacing is not lost."""
    gap, jit = SW.caps_for(acct, "card_post", False)[2:4]
    return when + POST_LATE + timedelta(seconds=int(gap) + int(jit)) + timedelta(minutes=30)


def _gate(cfg: C.Config, a, item: dict, now: datetime) -> str:
    """'' when the item may be handed to outward.send now; else why it waits here (nothing is written). Every check is
    a local read; Instagram's publishing limit (a Graph read) only once outward's own check says the item may go live
    now, so Stop everything, a hold and the poster's Meta hold are never read past."""
    from . import outward as OW
    if item.get("state") != "queued":
        return ""
    ex = _when(item.get("expires"))
    if ex is not None and ex <= now:
        return ""                             # past its expiry: outward.send writes it off ("its time passed")
    plat = str(item.get("platform") or "")
    if _reel_near(cfg, a, plat, now) and _target(item).get("kind") != "news":    # a news post never waits for one
        return "a Reel is within 30 minutes"
    v = OW.check(cfg, a, item.get("outlet") or plat, str(item.get("kind") or ""), origin=item.get("origin"),
                 text=item.get("text", ""), target=item.get("target"), manual=False, force=False,
                 signed=item.get("signed"), now=now, force_practice=bool(item.get("force_practice")))
    if not v.ok and v.code == "cap":
        return v.why                          # the day's cap: it waits (outward would skip it for good)
    if not v.ok or v.mode != "live" or (v.not_before is not None and v.not_before > now):
        return ""                             # outward.send records the refusal, the wait or the practice
    if plat == "instagram":
        from . import cards_api as API
        try:
            words = API.room_words(API.publish_limit(a, now), _reels_due(cfg, a, now))
        except (Exception, SystemExit):  # noqa: BLE001  (the limit could not be read: the executor reads it again)
            words = ""
        if words:
            return words                      # it waits: the Reels keep their room
    return ""


def _send_one(cfg: C.Config, now: datetime) -> List[str]:
    """One queued card or story goes (outward.send), the first that is due on any account; none while a Reel on
    that platform is within 30 minutes. One account's trouble never stops the others."""
    from . import outward as OW
    out: List[str] = []
    for a in _accounts(cfg):
        try:
            for item in OW.due(cfg, a, now, runner="studio"):
                if _gate(cfg, a, item, now):
                    continue
                res = OW.send(cfg, a, item, now)
                st = res.get("state")
                words = OW._line(a, item, res) if hasattr(OW, "_line") else ""
                return out + [words or f"  {a.name} {item.get('kind')} {item.get('outlet')}: {st} "
                                       f"{res.get('why') or ''}".rstrip()]
        except (Exception, SystemExit) as e:  # noqa: BLE001
            out.append(f"  {a.name} cards: could not send ({type(e).__name__}: {e})"[:300])
    return out


def make_today(cfg: C.Config, acct, now: datetime, wait_s: float = 0) -> List[str]:
    """The day's cards, made once (cards.make on, 06:26 to 20:45, not made yet), under a lock no second run waits
    behind: the studio tick and the 06:30 notice never draw the same day twice. wait_s: how long to wait for a make
    another run holds (the notice waits for it to count the cards)."""
    import time
    today = _local(cfg, now).date()
    if not can_draw(cfg, now) or SW.mode(acct, "cards.make") == SW.OFF or read_board(acct, today).get("made"):
        return []
    end = time.monotonic() + max(0.0, wait_s)
    while True:
        with store.try_lock(f"cards-make-{_slug(acct)}") as mine:
            if mine:
                if read_board(acct, today).get("made"):
                    return []
                return make(cfg, acct, today, now)
        if time.monotonic() >= end:
            return []
        time.sleep(2)


def tick(cfg: C.Config, now: Optional[datetime] = None, quiet: bool = True) -> List[str]:
    """The studio timer's step (every 10 minutes): make the day's cards once after 06:26 (cards.make), expire and skip,
    queue signed cards whose time came (cards.post), catch a missed story repost, then send one queued card or story
    (08:00 to 23:30). Nothing between 21:00 and 06:25 except a send before 23:30; nothing at all while every cards
    switch is off."""
    now = _now(cfg, now)
    lines: List[str] = []
    accts = [a for a in _accounts(cfg) if any_part_on(a)]
    if not accts:
        return lines
    today = _local(cfg, now).date()
    drawing = can_draw(cfg, now)
    for a in accts:
        try:
            if drawing:
                lines += make_today(cfg, a, now)
                lines += make_news(cfg, a, now)        # the news desk lines requests up through the day
            for d in (today - timedelta(days=1), today):
                if board_path(a, d).is_file():
                    lines += _advance(cfg, a, d, now)
            lines += _story_sweep(cfg, a, now)
        except SystemExit as e:
            lines.append(f"  {a.name} cards: {e}")
    if can_post(cfg, now):
        lines += _send_one(cfg, now)
    return lines


# ------------------------------------------------------------------------------------------------------ the sender's side
def _target(action: dict) -> dict:
    t = action.get("target")
    return t if isinstance(t, dict) else {}


def _mark(acct, action: dict, fields: dict) -> None:
    """The attempt, on the cards board, before the step that cannot be undone."""
    t = _target(action)
    day = t.get("board")
    with edit_board(acct, day) as bd:
        _touch(bd)
        att = dict(fields, at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(), action=action.get("id"))
        if action.get("kind") == "story":
            rec = bd["stories"].setdefault(f"{t.get('slot')}|{action.get('platform')}", {})
            rec["attempt"] = att
        else:
            c = bd["cards"].get(t.get("item")) or {}
            c.setdefault("outlets", {}).setdefault(t.get("outlet"), {})["attempt"] = att


def _attempt(acct, action: dict) -> dict:
    t = _target(action)
    bd = read_board(acct, t.get("board"))
    if action.get("kind") == "story":
        rec = (bd.get("stories") or {}).get(f"{t.get('slot')}|{action.get('platform')}") or {}
    else:
        rec = (((bd.get("cards") or {}).get(t.get("item")) or {}).get("outlets") or {}).get(t.get("outlet")) or {}
    att = rec.get("attempt") if isinstance(rec, dict) else None
    return att if isinstance(att, dict) else {}


def execute(cfg: C.Config, acct, action: dict) -> dict:
    """outward's executor for card_post and story: the Graph calls (cards_api), outside every lock."""
    from . import cards_api as API
    t = _target(action)
    outlet = str(t.get("outlet") or "")
    att = _attempt(acct, action)
    if att and att.get("action") == action.get("id"):       # a try that may have gone: look before anything else
        got = API.find(acct, outlet, _when(att.get("at")) or datetime.now(timezone.utc))
        if got:
            return dict(got, endpoint="looked up after an earlier try")

    def mark(fields: dict) -> None:
        _mark(acct, action, fields)
    bad = still_signed(cfg, acct, action)          # again, right before the bytes go (outward checked it first)
    if bad:
        raise SystemExit(f"not sent: {bad}")
    if action.get("kind") == "story":
        p = Path(str(t.get("file") or ""))
        sha = str(t.get("sha") or "")
        if not p.is_file() or not sha or B.sha256_file(p) != sha:
            raise SystemExit("the signed file is gone or changed")
        if outlet == "facebook_story":
            return API.fb_video_story(acct, p, mark, sha=sha)
        if outlet == "instagram_story":
            return API.ig_story(acct, p, mark, sha=sha)
        raise SystemExit(f"{outlet}: not a story")
    card = get_card(acct, t.get("board"), str(t.get("item") or ""))
    if card is None:
        raise SystemExit("the card is gone")
    files = card.get("files") or {}
    images = [Path(i["path"]) for i in files.get("images") or []]
    videos = [Path(v["path"]) for v in files.get("videos") or []]
    ishas = [str(i.get("sha256") or "") for i in files.get("images") or []]
    vshas = [str(v.get("sha256") or "") for v in files.get("videos") or []]
    if outlet.startswith("instagram_"):
        lim = API.publish_limit(acct, cfg.now())
        words = API.room_words(lim, _reels_due(cfg, acct, cfg.now()))
        if words:
            raise SystemExit(words)
        if not videos:
            raise SystemExit("the card has no Instagram copy (MP4): Make it again")
    if not images:
        raise SystemExit("the card has no picture: Make it again")
    # every upload sends the bytes it read only when they hash to what the card (and so the signature) holds
    if outlet == "facebook_story":
        return API.fb_photo_story(acct, images[0], mark, sha=ishas[0])
    words = news_caption(card, outlet) or None     # a news post goes with its signed words; the other cards with none
    if outlet == "facebook_feed":
        return API.fb_feed(acct, images, mark, shas=ishas, message=words)
    if outlet == "instagram_story":
        return API.ig_story(acct, videos[0], mark, sha=vshas[0])
    if outlet == "instagram_carousel":
        return API.ig_carousel(acct, videos, mark, shas=vshas, caption=words)
    raise SystemExit(f"{outlet}: not a place cards go")


def find(cfg: C.Config, acct, action: dict) -> Optional[dict]:
    """A lost answer: what went up on that outlet since the try began."""
    from . import cards_api as API
    since = _when(action.get("sent_at")) or _when(_attempt(acct, action).get("at")) or datetime.now(timezone.utc)
    try:
        return API.find(acct, str(_target(action).get("outlet") or ""), since)
    except SystemExit as e:              # a look-up that cannot run is a failed look-up, never the end of the run
        raise RuntimeError(str(e) or "the look-up could not run") from None


def still_signed(cfg: C.Config, acct, action: dict) -> str:
    """'' when what is about to go is still exactly what Andrew signed; else why it does not go."""
    t = _target(action)
    signed = action.get("signed") or {}
    if action.get("kind") == "story":
        rule = read_rule(acct)
        if not rule_signed(rule):
            return "the story repost rule is not signed (or was changed or paused)"
        if signed.get("hash") != rule_hash(rule):
            return "the story repost rule changed after this was queued"
        if action.get("platform") not in (rule.get("platforms") or []):
            return "the story repost rule no longer takes this platform"
        bd = _clip_board(acct, date.fromisoformat(str(t.get("board"))[:10]))
        s = (bd.get("slots") or {}).get(t.get("slot")) or {}
        if s.get("status") not in B.SIGNED:
            return "the clip is no longer signed"
        src = (s.get("cuts") or {}).get(t.get("from")) or s.get("clip") or {}
        if str(src.get("sha256") or "") != str(t.get("sha") or "") or signed.get("clip_sha") != t.get("sha"):
            return "the clip changed after its story was queued"
        if float(t.get("seconds") or 0) > STORY_MAX_S:
            return "over a story's 60 seconds"
        p = Path(str(t.get("file") or ""))
        if not p.is_file():
            return "its file is gone"
        return ""
    card = get_card(acct, t.get("board"), str(t.get("item") or ""))
    if card is None:
        return "the card is gone"
    if card.get("status") not in SIGNED:
        return f"the card is {card.get('status')}"
    if signed.get("what") != f"card:{str(t.get('board'))[:10]}/{card.get('id')}":
        return "it was queued for another card"
    if (card.get("signoff") or {}).get("hash") != signed.get("hash"):
        return "the card was signed again or changed after it was queued"
    if t.get("outlet") not in (card.get("targets") or []):
        return "the card no longer goes there"
    return signed_ok(card)


POLICY_CODES = ("cap", "off", "window", "kind", "stop")


def _rows_of(cfg: C.Config, acct, action: dict) -> List[dict]:
    """outward's log rows of this one action (the last two days), oldest first."""
    from . import outward as OW
    aid = action.get("id")
    since = datetime.now(timezone.utc) - timedelta(days=2)
    try:
        return [r for r in OW.rows(cfg, _slug(acct), since=since, kinds=[action.get("kind")]) if r.get("id") == aid]
    except Exception:  # noqa: BLE001
        return []


def _outcome(cfg: C.Config, acct, action: dict, state: str) -> Tuple[str, bool]:
    """(why, a failure?) of an action that will not go again. A failure (red on the Clips page) is a try that went
    wrong: gave up, a refusal with an error, the signed bytes changed, a missing permission, or a try that failed
    before its time ran out. A rule that held it back (the day's cap, the switch, the window) is a skip, not a
    failure."""
    rows = _rows_of(cfg, acct, action)
    last = next((r for r in reversed(rows) if r.get("state") == state), rows[-1] if rows else {})
    why = str(last.get("why") or "")
    failed_tries = [r for r in rows if r.get("state") == "failed"]
    if failed_tries and "its time passed" in why:
        why = f"{why} (after a failed try: {failed_tries[-1].get('why') or 'an error'})"
    if state in ("gave_up", "refused"):
        return why or state, True
    code = str(last.get("code") or "")
    bad = bool(last.get("error")) or code in ("signed", "perm") or bool(failed_tries) or not last
    if code in POLICY_CODES:
        bad = False
    return why or state, bad


def on_state(cfg: C.Config, acct, action: dict, state, result) -> None:
    """The card board follows what the sender did: posted, practised, skipped, given up. An action queued for an
    earlier signature of the card (it was signed again, re-timed or made again since) changes nothing on it."""
    t = _target(action)
    day = t.get("board")
    if not day or state in (None, "queued", "sending", "gone"):
        return None
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rec_new = {"state": state, "at": now, "action": action.get("id")}
    bad = False
    if state == "done":
        r = result if isinstance(result, dict) else {}
        rec_new.update({"post_id": r.get("id"), "url": r.get("url") or "", "published_at": now, "provider": "direct",
                        "mode": "live"})
    elif state == "practice":
        rec_new["mode"] = "practice"
    else:
        why, bad = _outcome(cfg, acct, action, str(state))
        rec_new["why"] = why[:300]
        rec_new["failure"] = bad
    with edit_board(acct, day) as bd:
        _touch(bd)
        if action.get("kind") == "story":
            key = f"{t.get('slot')}|{action.get('platform')}"
            old = bd["stories"].get(key) or {}
            bd["stories"][key] = dict(old, **rec_new)
            return None
        card = bd["cards"].get(t.get("item"))
        if not isinstance(card, dict):
            return None
        signed = action.get("signed") or {}
        if state != "done" and (card.get("signoff") or {}).get("hash") != signed.get("hash"):
            return None                         # a stale action: the card moved on (a post that went is still kept)
        outs = card.setdefault("outlets", {})
        old = outs.get(t.get("outlet")) or {}
        outs[t.get("outlet")] = dict(old, **rec_new)
        recs = [outs.get(x) or {} for x in card.get("targets") or []]
        states = [str(r.get("state") or "") for r in recs]
        if any(s == "done" for s in states):
            if all(s in DONE_STATES + GIVEN_UP for s in states) or card.get("status") not in SIGNED:
                card["status"] = "posted"      # what went is the truth, whatever the card said
            return None
        if card.get("status") not in SIGNED:
            return None                         # declined or re-timed meanwhile: it stays as Andrew left it
        if states and all(s in DONE_STATES + GIVEN_UP for s in states):
            if any(s in DONE_STATES for s in states):
                card["status"] = "posted"
            elif any(r.get("failure") for r in recs):
                card["status"] = "failed"
                card.setdefault("notes", []).append(f"{B.utcnow()} did not post: {rec_new.get('why') or state}")
            else:
                card["status"] = "skipped"
                card.setdefault("notes", []).append(f"{B.utcnow()} not sent: {rec_new.get('why') or state}")
    return None


# ------------------------------------------------------------------------------------------------------ the page's numbers
def _recent_cards(cfg: C.Config, acct, now: datetime, back: int = 1, ahead: int = 1):
    today = _local(cfg, now).date()
    for i in range(-back, ahead + 1):
        d = today + timedelta(days=i)
        for cid, c in sorted((read_board(acct, d).get("cards") or {}).items()):
            if isinstance(c, dict):
                yield d, cid, c


def to_sign(cfg: C.Config, acct, now: datetime) -> List[Tuple[date, str, dict]]:
    t = _utc(now)
    out = []
    for d, cid, c in _recent_cards(cfg, acct, now, 0, 1):
        at = _when(c.get("at"))
        if c.get("status") == "ready" and (at is None or t <= at + EXPIRE_AFTER):
            out.append((d, cid, c))
    return out


def problems(cfg: C.Config, acct, now: datetime) -> List[str]:
    """Signed cards and stories of the last two days that did not go."""
    out = []
    for d, cid, c in _recent_cards(cfg, acct, now, 1, 0):
        if c.get("status") == "failed" and (c.get("signoff") or c.get("outlets")):
            out.append(f"{acct.name} {KIND_WORDS.get(c.get('kind'), cid)} of {d.strftime('%b %d')} did not post")
    today = _local(cfg, now).date()
    for d in (today - timedelta(days=1), today):
        for key, st in sorted((read_board(acct, d).get("stories") or {}).items()):
            if isinstance(st, dict) and st.get("state") == "gave_up":
                out.append(f"{acct.name} story repost {key.replace('|', ' ')} of {d.strftime('%b %d')}: "
                           f"{st.get('why') or 'not sure it went'}")
    return out


def health(cfg: C.Config, now: datetime, accts) -> Tuple[str, str]:
    """A red line on the Clips page when a signed card or story did not go."""
    bad = []
    for a in accts or []:
        if _is_client(a) or not any_part_on(a):
            continue
        bad += problems(cfg, a, _now(cfg, now))
    if not bad:
        return ("", "")
    more = f" (and {len(bad) - 2} more)" if len(bad) > 2 else ""
    return ("bad", "Cards: " + "; ".join(bad[:2]) + more + ". Look on the Cards tab.")


def summary(cfg: C.Config, now: datetime, accts) -> List[dict]:
    """The Today band's chip: cards waiting for Approve (and any that did not post)."""
    now = _now(cfg, now)
    n, bad = 0, 0
    for a in accts or []:
        if _is_client(a) or not any_part_on(a):
            continue
        n += len(to_sign(cfg, a, now))
        bad += len(problems(cfg, a, now))
    out = []
    if n:
        out.append({"key": "cards", "words": f"Cards: {n} to sign", "href": "/cards", "level": "calm", "order": 40})
    if bad:
        out.append({"key": "cards-bad", "words": f"Cards: {bad} did not post", "href": "/cards", "level": "warn",
                    "order": 41})
    return out


def morning_line(cfg: C.Config, now=None) -> str:
    """'N cards to sign' for the 06:30 notice. The studio tick makes the day's cards on its first pass after 06:26,
    which may land after the notice: so the notice makes them itself when they are not made yet (or waits for the
    make that is running), then counts."""
    now = _now(cfg, now)
    for a in _accounts(cfg):
        if any_part_on(a):
            try:
                make_today(cfg, a, now, wait_s=240)
            except (Exception, SystemExit):  # noqa: BLE001  (the notice always goes)
                pass
    n = sum(len(to_sign(cfg, a, now)) for a in _accounts(cfg) if any_part_on(a))
    return f"{n} {'card' if n == 1 else 'cards'} to sign" if n else ""


_CAL = {"posted": "posted", "approved": "scheduled", "scheduled": "scheduled", "ready": "review", "draft": "review",
        "failed": "problem"}


def cal_items(cfg: C.Config, now: datetime, accts) -> Dict[str, Dict[str, List[dict]]]:
    """The calendar's second strip: each card and story repost by day and account (40 days either side of now).
    An account with every cards switch off shows nothing, like the other cards hooks: a board left from when cards
    were on (or made by `cards make`) is never expired, posted or reposted while off, so its marks would be stale."""
    now = _now(cfg, now)
    today = _local(cfg, now).date()
    out: Dict[str, Dict[str, List[dict]]] = {}
    for a in accts or []:
        if _is_client(a) or not any_part_on(a):
            continue
        for d in card_days(a):
            if abs((d - today).days) > 40:
                continue
            bd = read_board(a, d)
            items = []
            for c in (bd.get("cards") or {}).values():
                s = _CAL.get(str((c or {}).get("status") or ""))
                if s:
                    items.append({"k": "card", "s": s})
            for st in (bd.get("stories") or {}).values():
                state = str((st or {}).get("state") or "")
                s = ("posted" if state in DONE_STATES else "scheduled" if state in ("queued", "sending", "held")
                     else "problem" if state in ("gave_up", "failed") else "")
                if s:
                    items.append({"k": "story", "s": s})
            if items:
                out.setdefault(d.isoformat(), {})[a.slug] = items
    return out


# ------------------------------------------------------------------------------------------------------ doctor
def _engine_check() -> Tuple[bool, str]:
    try:
        r = subprocess.run([engine_python(), "-c", "import PIL, fontTools"], capture_output=True, text=True,
                           timeout=10)
        tail = (r.stderr or "").strip().splitlines()
        return r.returncode == 0, (tail[-1] if r.returncode and tail else "")
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """What the cards wait for: "--" lines while every cards switch is off; the engine, its fonts and the Instagram
    stories test once one is on. Read only, no network."""
    from . import capabilities as CAP
    rows: List[Tuple[Optional[bool], str, str]] = []
    accts = _accounts(cfg)
    on = [a for a in accts if any_part_on(a)]
    for a in accts:
        if a in on:
            continue
        rows.append((None, f"{a.name} cards: off (factory.py social on {a.slug} cards.make; Instagram stories not "
                           f"tested: factory.py cards probe {a.slug}); cards.post and cards.story_repost (with its "
                           f"rule signed on the Cards tab) start at --practice, then wait for the go-live checklist",
                     ""))
    if not on:
        return rows
    ok, why = _engine_check()
    if ok:
        rows.append((True, f"cards: the engine Python ({engine_python()}) imports PIL and fontTools", ""))
    else:
        rows.append((False, "cards: the engine Python cannot import PIL and fontTools" + (f" ({why})" if why else ""),
                     f"{engine_python()} -m pip install --user pillow fonttools"))
    missing = [f for f in FONTS if not os.path.exists(f)]
    rows.append((False, "cards: fonts missing: " + ", ".join(missing), "they come with macOS: restore them") if missing
                else (True, "cards: Helvetica Neue, Impact and DIN Condensed are there", ""))
    if not Path(ENGINE).is_file():
        rows.append((False, f"cards: {ENGINE} is missing", "git checkout render/card_engine.py"))
    for a in on:
        if getattr(a, "maker", "") == "gta" and SW.mode(a, "cards.make") != SW.OFF:
            kit = str(getattr(a, "kit", "") or "gta")
            kf = Path(str(getattr(a, "skill_dir", ""))) / "assets" / "brand" / kit / "kit.json"
            if not kf.is_file():
                rows.append((False, f"{a.name} cards: no brand kit '{kit}' in the skill ({kf}): no card is drawn "
                                    f"(never on another account's brand)", f"add assets/brand/{kit}/kit.json to the "
                                    f"gaming skill, or factory.py social off {a.slug} cards.make"))
        mk, po, sr = (SW.mode(a, k) for k in ("cards.make", "cards.post", "cards.story_repost"))
        rows.append((None, f"{a.name} cards: made {mk}, posted {po}, story reposts {sr}", ""))
        wants_ig = any(t.startswith("instagram_") for t in SW.settings(a)["cards.targets"]) or (  # type: ignore
            sr != SW.OFF and "instagram" in (read_rule(a).get("platforms") or []))
        pr = CAP.probe_result(a, "ig_stories")
        val = (pr or {}).get("value") if isinstance(pr, dict) else None
        if not wants_ig:
            rows.append((None, f"Instagram stories on {a.name}: not a target here", ""))
        elif not isinstance(val, dict):
            rows.append((None, f"Instagram stories on {a.name}: not tested (factory.py cards probe {a.slug})", ""))
        elif val.get("ok"):
            rows.append((True, f"Instagram stories on {a.name}: tested {str(pr.get('at'))[:10]}, they work", ""))
        elif val.get("kind") == "creator":
            rows.append((False, f"Instagram stories on {a.name}: refused, it is a Creator account",
                         "Andrew's call: switch the Instagram account to Business (it changes the music the app "
                         "offers), or take instagram_story out of cards.targets"))
        else:
            rows.append((False, f"Instagram stories on {a.name}: {val.get('words') or 'refused'}",
                         f"factory.py cards probe {a.slug}"))
        if sr != SW.OFF and not rule_signed(read_rule(a)):
            rows.append((None, f"{a.name} story reposts: wait for the rule to be signed on the Cards tab", ""))
        for p in problems(cfg, a, _now(cfg, now)):
            rows.append((False, p, "look on the Cards tab"))
    return rows


# ------------------------------------------------------------------------------------------------------ the command
def _account(cfg: C.Config, slug: str):
    a = cfg.account(slug)
    if _is_client(a):
        raise SystemExit(f"{a.name} is a client's account: cards are for the owner's own accounts")
    return a


def status_lines(cfg: C.Config, acct, day: date) -> List[str]:
    bd = read_board(acct, day)
    out = [f"{acct.name} cards on {day}" + (f" (made {str((bd.get('made') or {}).get('at'))[:16]})"
                                            if bd.get("made") else " (not made yet)")]
    for cid, c in sorted((bd.get("cards") or {}).items()):
        t = post_time(c)
        out.append(f"  {cid}: {c.get('status')}, {_clock_words(cfg, t) if t else '?'}, "
                   + (", ".join(c.get("targets") or []) or "no target")
                   + "".join(f"; {o} {r.get('state')}" for o, r in sorted((c.get("outlets") or {}).items())
                             if isinstance(r, dict)))
    for key, st in sorted((bd.get("stories") or {}).items()):
        out.append(f"  story {key}: {(st or {}).get('state')} {(st or {}).get('why') or ''}".rstrip())
    rule = read_rule(acct)
    out.append(f"  story repost rule: {rule.get('state')}, version {rule.get('version')}"
               + (" (signed as it is)" if rule_signed(rule) else ""))
    return out


def cli(cfg: C.Config, argv: List[str]) -> None:
    """factory.py cards status|make|probe|selftest|tick ..."""
    ap = argparse.ArgumentParser(prog="factory.py cards", description="cards and stories (clipfactory/cards.py)")
    sub = ap.add_subparsers(dest="action", required=True)
    q = sub.add_parser("status", help="the day's cards, their state, and the story repost rule")
    q.add_argument("account", nargs="?")
    q.add_argument("--date", default="today")
    q = sub.add_parser("make", help="draw the day's cards now (06:26 to 20:45 only)")
    q.add_argument("account")
    q.add_argument("--date", default="today")
    q.add_argument("--kind", action="append", choices=list(KINDS))
    q.add_argument("--again", action="store_true", help="draw a card that exists again (voids its signature)")
    q = sub.add_parser("probe", help="test Instagram stories on this account (an unpublished container; network)")
    q.add_argument("account")
    sub.add_parser("selftest", help="can the engine Python draw a card here")
    q = sub.add_parser("tick", help="the studio timer's card step, now")
    q.add_argument("--quiet", action="store_true")
    q = sub.add_parser("news", help="news posts: add (line a request up for today), check (only check it), list, "
                                    "find (an atlas person or agency for the picture), logos (the sources with one), "
                                    "show (one drawn post: its pictures, words and hash), sign (on Andrew's own "
                                    "'post it' in chat, with the hash show gave)")
    q.add_argument("op", choices=("add", "check", "ask", "list", "find", "logos", "show", "sign"))
    q.add_argument("account")
    q.add_argument("file", nargs="?", help="the request, a JSON file (add, check); the words to look for (find)")
    q.add_argument("--by", default="news desk", help="who wrote it (the news desk task, or a person)")
    q.add_argument("--date", default="today")
    q.add_argument("--seen", default="", help="sign: the hash show printed for the pictures Andrew was sent")
    q.add_argument("--what", default="", help="ask: the news in one plain line for Andrew's phone")
    a = ap.parse_args(list(argv))
    if a.action == "news":
        acct = _account(cfg, a.account)
        if a.op == "logos":
            for n, v in sorted(_logos().items()):
                print(f"  {n}" + (f"  (the {v['atlas']} seal)" if v.get("atlas") else ""))
            print("  or any agency by its atlas name (cards news find <account> <words>)")
            return
        if a.op == "find":
            words = str(a.file or "").lower().split()
            if not words:
                raise SystemExit("cards news find: give a name to look for")
            _, ents = _atlas(acct)
            hits = [(eid, e) for eid, e in ents.items() if isinstance(e, dict) and e.get("kind") in NEWS_PICTURE
                    and all(w in str(e.get("name") or "").lower() for w in words)]
            for eid, e in sorted(hits, key=lambda x: (not x[1].get("current"), str(x[1].get("name"))))[:15]:
                pic, why = news_picture(acct, eid)
                print(f"  {eid}  {e.get('kind')}  {e.get('name')}  {e.get('title') or ''}"
                      + ("" if pic else f"  (not usable: {why})"))
            if not hits:
                print("  none: pick a story with a person or an agency the atlas has")
            return
        if a.op in ("show", "sign"):
            day = C.parse_day(a.date, cfg)
            if not a.file:
                raise SystemExit(f"cards news {a.op}: give the news id")
            nid = a.file[5:] if a.file.startswith("news-") else a.file
            if a.op == "sign":
                if not a.by or a.by == "news desk" or not a.seen:
                    raise SystemExit("cards news sign: give --seen <hash from show> and --by with Andrew's own words")
                why = cfg.why_off(acct)
                if why:
                    raise SystemExit(why)
                print("  " + approve(cfg, acct, day, f"news-{nid}", a.seen, a.by))
                return
            print(show_news(cfg, acct, day, nid))
            return
        if a.op == "list":
            day = C.parse_day(a.date, cfg)
            bd = read_board(acct, day)
            folder = news_dir(acct, day)
            for p in sorted(folder.glob("*.json")) if folder.is_dir() else []:
                c = (bd.get("cards") or {}).get(f"news-{p.stem}") or {}
                why = ((bd.get("news_refused") or {}).get(p.stem) or {}).get("why")
                print(f"  {p.stem}: " + (f"card {c.get('status')}" if c else f"not drawn, {why}" if why else
                                         "waiting to be drawn"))
            return
        if not a.file:
            raise SystemExit(f"cards news {a.op}: give the request's JSON file")
        try:
            req = json.loads(Path(a.file).read_text())
        except (OSError, ValueError) as e:
            raise SystemExit(f"{a.file}: cannot read it as JSON ({e})")
        if a.op == "ask":                        # the phone's Yes / No (clipfactory/newsphone.py, 2026-10-29)
            from . import newsphone
            if not a.what:
                raise SystemExit("cards news ask: give --what, the news in one plain line")
            print("\n".join(newsphone.ask(cfg, acct, req, a.what)))
            return
        if a.op == "check":
            bad, got = check_news(acct, req)
            print("  ok: it may be lined up" if not bad else "\n".join(f"  {b}" for b in bad))
            if bad:
                raise SystemExit(1)
            return
        why = cfg.why_off(acct)
        if why:
            raise SystemExit(why)
        for ln in add_news(cfg, acct, req, by=a.by):
            print(ln)
        return
    if a.action == "status":
        day = C.parse_day(a.date, cfg)
        for acct in ([_account(cfg, a.account)] if a.account else _accounts(cfg)):
            for ln in status_lines(cfg, acct, day):
                print(ln)
    elif a.action == "make":
        acct = _account(cfg, a.account)
        why = cfg.why_off(acct)
        if why:
            raise SystemExit(why)
        for ln in make(cfg, acct, C.parse_day(a.date, cfg), kinds=a.kind, again=a.again,
                       by=cfg.approvers[0] if cfg.approvers else "") or ["  nothing to make"]:
            print(ln)
    elif a.action == "probe":
        from . import capabilities as CAP
        from . import cards_api as API
        acct = _account(cfg, a.account)
        got = API.probe_stories(acct)
        CAP.put_probe(acct, "ig_stories", got)
        print(f"{acct.name} Instagram stories: {got.get('words')}")
    elif a.action == "selftest":
        eng = Path(ENGINE)
        skill = next((x.skill_dir for x in cfg.all_accounts.values() if x.maker == "politics"), None)
        cmd = [engine_python(), "-B", str(eng), "--selftest"] + (["--skill", str(skill)] if skill else [])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=ENGINE_TIMEOUT, cwd=str(eng.parent))
        print((r.stdout or r.stderr or "").strip() or f"exit {r.returncode}")
        if r.returncode:
            raise SystemExit(1)
    elif a.action == "tick":
        for ln in tick(cfg, quiet=a.quiet):
            print(ln)
