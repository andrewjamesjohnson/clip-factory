"""Checkpoint stats: each posted clip's numbers read again at 1h, 3h, 6h, 24h, 72h, 7d and 30d after it went public.

The nightly read (stats.py, 03:30) keeps only the latest numbers. This part keeps how each post grew: a short series
per post, so Clip Review can say early that a clip is running hot or starting slow, the Stats tab can draw how each
post grew against the account's usual, and the learner has the same points for every post.

    every 15 minutes   factory.py social checkpoints --quiet   (launchd com.clipfactory.checkpoints; run below)
    by hand            factory.py checkpoints run | backfill | show <account> <id> | status

Per account it is off until `factory.py social on <account> checkpoints`. Off means off: no platform call, no new
file (the 30-day YouTube deletion below still runs on the files already there).

How a read is chosen (run):
- The posts are the boards' records with a post or video id, from 32 days back to today (TikTok is never re-read: an
  'Only me' test is nobody's post, and TikTok has no by-id read on the production app). A YouTube upload counts from
  when it went public (stats.went_public), never from its upload, and is read only once YouTube says it is public.
- A label is due at went + its offset + a small wait that is the same every time for that post (about 10% of the
  offset, 2 minutes to 6 hours), so ten posts at the same slot are not all asked about in the same minute.
- When several labels are due at once (the Mac slept), there is one read, stored under the latest one with its true
  age; the others are written as missed. A point is never made up.
- YouTube: videos.list part statistics,status, 50 ids a call (1 unit), within this machine's units for the day.
  Instagram and Facebook: one insights call per post (40 a platform a tick, 20 when Meta says we are near its limit),
  each asked only when no hold is on the account (outward.held, the poster's own Meta hold), and what Meta's answer
  said about its limits is kept for the guard (outward.note_usage).
- Extra metrics (switch checkpoints.extra) at 24h, 7d and 30d, in calls of their own, so a metric Meta refuses never
  costs the views. A refused metric is asked alone once; one that fails alone is remembered for 30 days
  (state/stats/series/unsupported.json). Facebook's retention graph is read at 7d.
- YouTube Analytics (switch checkpoints.yt_analytics and the yt-analytics.readonly permission) at 72h, 7d and 30d:
  engaged views, average view, shares, subscribers; the retention line at 7d.
- The board's latest numbers are written too (B.edit, only when newer), so the cards and the Stats tab move between
  the nightly reads. The nightly read's own files (pulled, history, read.lock) are never touched.
- YouTube's policy (III.E.4): a YouTube number read 30 days ago is deleted (files only, every run, switch on or off:
  a checkpoint cannot be refreshed, only deleted); while on, each video's current numbers are asked for again every
  29 days, and a video YouTube no longer has loses its numbers here.
- A read that came late (the Mac slept) is marked late: never compared with the usual, never part of it. A point
  from the snapshots read over an hour before its time is marked early, the same way (10.21). A post is in one file
  only (the month it first went public), and two slots holding one id are read once.
- A snapshot's point (backfill) stands until an on-time live read replaces it; a backfill never fills a label whose
  time has not come, and `backfill --redo` takes an early one again from an on-time snapshot when there is one.

Files: state/stats/series/<account>__<outlet>__<YYYY-MM of went public>.json, keyed by the platform's id:
    {"aPFYyOZCnPI": {"went", "board", "slot", "clip", "fmt", "dur", "title",
      "points": [{"label": "24h", "at", "age_min", "views", "likes", "comments", "shares", "extra": {}, "src"}],
      "missed": ["3h"], "early": {"label", "x", "vs_n"}, "refreshed_at", "backfill": false, "gone_at": null}}
src: data (YouTube Data API), insights (Meta), history (backfill from the nightly snapshots). One run at a time
(state/stats/series/.lock, never the nightly read's read.lock).
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import html
import json
import os
import random
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from . import board as B
from . import capabilities as CAP
from . import checkpoints_api as API
from . import config as C
from . import direct as D
from . import graph as G
from . import net
from . import outward as OW
from . import social as SOC
from . import stats as ST
from . import store
from . import switches as SW

LABELS: Tuple[Tuple[str, int], ...] = (("1h", 60), ("3h", 180), ("6h", 360), ("24h", 1440), ("72h", 4320),
                                       ("7d", 10080), ("30d", 43200))
ORDER = [lbl for lbl, _ in LABELS]
OFFSET = dict(LABELS)
OUTLETS = ("facebook_reels", "instagram_reels", "youtube_shorts")       # TikTok is never re-read
PLAT = {"facebook_reels": "facebook", "instagram_reels": "instagram", "youtube_shorts": "youtube"}
NAME = {"facebook_reels": "Facebook", "instagram_reels": "Instagram", "youtube_shorts": "YouTube"}
EARLY_LABELS = ("1h", "3h", "6h")
EXTRA_AT = ("24h", "7d", "30d")
YTA_AT = ("72h", "7d", "30d")
RETENTION_AT = "7d"
HOT, SLOW = 2.0, 0.5                 # x the account's usual at the same label
BASE_LAST, BASE_MIN = 30, 8          # the usual: the median of the last 30 posts at that label, with 8 at the least
SCOPE_DAYS = 31                      # boards from yesterday back this many days (and today)
META_BATCH = 40                      # insights reads per platform per account per tick (halved when slow)
YT_RESERVE = 100                     # YouTube units kept for the signed first comments (two writes)
REFRESH_DAYS = 29                    # YouTube III.E.4: stored numbers refreshed within 30 days
EXPIRE_DAYS = 30                     # YouTube III.E.4: a YouTube number read this long ago is deleted (switch on or off)
GIVE_UP = 4                          # failed reads of one due label before it is written as missed
UNSUPPORTED_DAYS = 30                # a refused metric is asked again after this long
HOT_HOURS = 24                       # the Today band speaks of posts that went up this long ago at the most
STOP_KINDS = ("block", "buc", "rate", "auth", "quota")     # account-wide: the platform waits for the next tick
TRIP_KINDS = ("block", "buc", "rate", "auth", "quota")     # the guard (switch on) hears of these
POST_KINDS = ("perm", "timeout")     # may be about one post (too new, too few viewers): a failed try on that post;
POST_STOP = 2                        # this many posts failing so in one tick and the platform waits for the next
METRICS = ("views", "likes", "comments", "shares")
_FILE = re.compile(r"^(?P<slug>[a-z0-9-]+)__(?P<outlet>[a-z_]+)__(?P<ym>\d{4}-\d{2})\.json$")
_PID = re.compile(r"^[A-Za-z0-9_.:~-]{1,80}$")
SLEEP = time.sleep                   # between Meta calls (tests make it a no-op)
RNG = random.Random()

# the names the Stats tab and the card show for the extra metrics (raw API names are kept in the files)
EXTRA_WORDS = {
    "reach": "Reach", "saved": "Saves", "total_interactions": "Interactions",
    "ig_reels_avg_watch_time": "Avg watch", "ig_reels_video_view_total_time": "Watch time",
    "reels_skip_rate": "Skip rate", "fb_reels_total_plays": "Plays with replays", "fb_reels_replay_count": "Replays",
    "post_impressions_unique": "Reach", "post_video_avg_time_watched": "Avg watch", "post_video_view_time":
    "Watch time", "post_video_followers": "Follows", "engagedViews": "Engaged views",
    "averageViewDuration": "Avg watch", "averageViewPercentage": "Avg watched", "shares": "Shares",
    "subscribersGained": "Subscribers"}


# ------------------------------------------------------------------------------------------------------ small things
def _utc(t: Optional[datetime]) -> datetime:
    t = t or datetime.now(timezone.utc)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return _utc(t).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _when(v) -> Optional[datetime]:
    return ST._when(v) if v else None


def _folder() -> Path:
    """state/stats/series (at call time: never stats.OUT, which is fixed at import)."""
    return Path(C.STATE) / "stats" / "series"


def _path(slug: str, outlet: str, ym: str) -> Path:
    return _folder() / f"{slug}__{outlet}__{ym}.json"


def _ym(went: datetime) -> str:
    return _utc(went).astimezone(timezone.utc).strftime("%Y-%m")


def _on(acct) -> bool:
    return SW.mode(acct, "checkpoints") == "on"


def _accounts(cfg: C.Config) -> List[C.Account]:
    """The owner's accounts that work here with checkpoints switched on."""
    return [a for a in SOC.accounts(cfg) if _on(a)]


def jitter_min(pid: str, offset_min: int) -> float:
    """The wait after a label's time, in minutes: the same every time for one post, about 10% of the offset (5% to
    15%), never under 2 minutes or over 6 hours."""
    u = int(hashlib.sha256(str(pid).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return min(360.0, max(2.0, offset_min * 0.1 * (0.5 + u)))


def due_at(went: datetime, pid: str, label: str) -> datetime:
    off = OFFSET[label]
    return _utc(went) + timedelta(minutes=off + jitter_min(pid, off))


def fmt_n(n) -> str:
    """1234 as 1.2K, 12345 as 12K, 1234567 as 1.2M (as the Stats tab writes them)."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0"
    a = abs(n)
    if a >= 1e6:
        return f"{n / 1e6:.1f}".rstrip("0").rstrip(".") + "M"
    if a >= 1e4:
        return f"{n / 1e3:.0f}K"
    if a >= 1e3:
        return f"{n / 1e3:.1f}".rstrip("0").rstrip(".") + "K"
    return str(int(round(n)))


def fmt_x(x: float) -> str:
    return f"{x:.0f}x" if x >= 10 else f"{x:.1f}".rstrip("0").rstrip(".") + "x"


# ------------------------------------------------------------------------------------------------------ the files
_CACHE: Dict[str, Tuple[tuple, object]] = {}
_CACHE_MAX = 4000      # entries: one per series file plus a few per account and platform; past this it starts again


def _memo(name: str, key: tuple, build: Callable[[], object]):
    """What build() returns, kept under `name` while `key` stays the same. The Clips page redraws every card each 15
    seconds, and a card asks about each platform's whole history: one look per change of the files, not per card."""
    got = _CACHE.get(name)
    if got and got[0] == key:
        return got[1]
    val = build()
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.clear()
    _CACHE[name] = (key, val)
    return val


def _stat_key(p: Path) -> Optional[tuple]:
    try:
        st = os.stat(p)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns, st.st_ino)


def _read(p: Path) -> dict:
    """A series file (cached by size and time: the card and the band read them on every redraw)."""
    key = _stat_key(p)
    if key is None:
        return {}

    def build() -> dict:
        data = store.read(p, {})
        return data if isinstance(data, dict) else {}
    return _memo(str(p), key, build)


def _files(slug: Optional[str] = None, outlet: Optional[str] = None) -> List[Tuple[str, str, str, Path]]:
    """(slug, outlet, YYYY-MM, path) of every series file, newest month first. The folder's listing is kept while the
    folder itself is unchanged (a file added, replaced or removed changes it), except in the two seconds after a
    change, when a second change could share its time."""
    d = _folder()
    try:
        st = os.stat(d)
    except OSError:
        return []

    def build() -> List[Tuple[str, str, str, Path]]:
        out = []
        try:
            names = [x.name for x in os.scandir(d)]
        except OSError:
            return []
        for name in names:
            m = _FILE.match(name)
            if m:
                out.append((m["slug"], m["outlet"], m["ym"], d / name))
        return sorted(out, key=lambda x: (x[0], x[1], x[2]), reverse=True)
    if time.time_ns() - st.st_mtime_ns < 2_000_000_000:
        every = build()
    else:
        every = _memo(f"\0files {d}", (st.st_mtime_ns, st.st_ino, st.st_size, st.st_nlink), build)
    return [x for x in every if (not slug or x[0] == slug) and (not outlet or x[1] == outlet)]


def _score(e: dict) -> tuple:
    """Which of two copies of one post's series is the one to keep: the one with more read, then the newer."""
    pts = [p for p in e.get("points") or [] if isinstance(p, dict)]
    return (len(pts) + len(e.get("missed") or []), str(e.get("refreshed_at") or ""),
            max((str(p.get("at") or "") for p in pts), default=""))


def _index(slug: str, outlet: str) -> Dict[str, Tuple[Path, dict, List[Path]]]:
    """pid -> (the file its series lives in, the series, other files holding an older copy of it). A post stays in
    the file of the month it first went public, even when its went-public time moves later (the nightly read writes
    the platform's own time), so it is never in two files; an older copy from before that rule is ignored here and
    dropped at the next write. Built once per change of that account and platform's files (read only: never change
    what it returns)."""
    return _index_keyed(slug, outlet)[0]


def _index_keyed(slug: str, outlet: str) -> Tuple[Dict[str, Tuple[Path, dict, List[Path]]], tuple]:
    """(the index, the files' key it was built from: each file's path, size, time and inode, newest month first)."""
    files = [p for _, _, _, p in _files(slug, outlet)]
    key = tuple((str(p), _stat_key(p)) for p in files)

    def build() -> Dict[str, Tuple[Path, dict, List[Path]]]:
        out: Dict[str, Tuple[Path, dict, List[Path]]] = {}
        for p in files:
            for pid, e in _read(p).items():
                if not isinstance(e, dict):
                    continue
                had = out.get(pid)
                if had is None:
                    out[pid] = (p, e, [])
                elif _score(e) > _score(had[1]):
                    out[pid] = (p, e, had[2] + [had[0]])
                else:
                    had[2].append(p)
        return out
    return _memo(f"\0index {slug} {outlet}", key, build), key


def _entries(slug: str, outlet: str) -> Iterator[Tuple[str, dict]]:
    """Each post's series once (de-duplicated by the platform's id)."""
    for pid, (_, e, _) in _index(slug, outlet).items():
        yield pid, e


def series(slug: str, outlet: str, pid: str) -> Optional[dict]:
    """One post's series (a copy), or None."""
    slug = getattr(slug, "slug", slug)
    got = _index(slug, outlet).get(str(pid))
    return json.loads(json.dumps(got[1])) if got else None


def _home(idx: Dict[str, Tuple[Path, dict, List[Path]]], slug: str, outlet: str, pid: str, went: datetime) -> Path:
    """Where a post's series is written: the file it already lives in, else the month it went public."""
    got = idx.get(pid)
    return got[0] if got else _path(slug, outlet, _ym(went))


def _drop_copies(idx: Dict[str, Tuple[Path, dict, List[Path]]], pids) -> None:
    """Remove the older copies of these posts' series from the other month files."""
    per: Dict[Path, List[str]] = {}
    for pid in pids:
        got = idx.get(pid)
        for p in (got[2] if got else []):
            per.setdefault(p, []).append(pid)
    for p, ps in sorted(per.items()):
        with store.edit(p, dict) as d:
            for pid in ps:
                d.pop(pid, None)


def _point(e: dict, label: str) -> Optional[dict]:
    for pt in e.get("points") or []:
        if isinstance(pt, dict) and pt.get("label") == label:
            return pt
    return None


def latest(e: dict) -> Optional[dict]:
    """The newest point of a series (by label order)."""
    pts = [p for p in e.get("points") or [] if isinstance(p, dict) and p.get("label") in OFFSET]
    return max(pts, key=lambda p: ORDER.index(p["label"])) if pts else None


@contextlib.contextmanager
def _lock(wait: bool = False) -> Iterator[bool]:
    """state/stats/series/.lock: one checkpoint run (or backfill) at a time. Never the nightly read's read.lock."""
    d = _folder()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".lock", "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ------------------------------------------------------------------------------------------------------ the usual
def baseline_n(slug: str, outlet: str, label: str, now: Optional[datetime] = None,
               exclude: Optional[str] = None) -> Tuple[Optional[float], int]:
    """(median views at that label of the account's last 30 posts on that outlet that have one, how many). The
    median is None under 8 posts."""
    slug = getattr(slug, "slug", slug)
    now = _utc(now)
    vals: List[float] = []
    for went, views, pid in _usual(slug, outlet, label):
        if went > now or pid == exclude:
            continue
        vals.append(views)
        if len(vals) >= BASE_LAST:
            break
    if len(vals) < BASE_MIN:
        return None, len(vals)
    return float(statistics.median(vals)), len(vals)


def _by_went(slug: str, outlet: str) -> List[Tuple[datetime, int, str, dict]]:
    """Each post with a went-public time as (went, its place in the index, pid, series), newest first. Built once per
    change of the files, like the index."""
    idx, key = _index_keyed(slug, outlet)

    def build() -> List[Tuple[datetime, int, str, dict]]:
        got = []
        for i, (pid, (_, e, _)) in enumerate(idx.items()):
            went = _when(e.get("went"))
            if went is not None:
                got.append((went, i, pid, e))
        got.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        return got
    return _memo(f"\0went {slug} {outlet}", key, build)


def _usual(slug: str, outlet: str, label: str) -> List[Tuple[datetime, float, str]]:
    """Every post that can count towards the usual at that label (not gone, an on-time read with views, a went-public
    time), as (went, views, pid), newest first. Built once per change of the files, like the index."""
    idx, key = _index_keyed(slug, outlet)

    def build() -> List[Tuple[datetime, float, str]]:
        got = []
        for pid, (_, e, _) in idx.items():
            if e.get("gone_at"):
                continue
            pt = _point(e, label)
            went = _when(e.get("went"))
            if not pt or not isinstance(pt.get("views"), (int, float)) or went is None or not on_time(pt):
                continue
            got.append((went, float(pt["views"]), pid))
        got.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return got
    return _memo(f"\0usual {slug} {outlet} {label}", key, build)


def baseline(slug: str, outlet: str, label: str, now: Optional[datetime] = None) -> Optional[float]:
    """The account's usual views at that label on that outlet: the median of its last 30 posts (n >= 8), or None."""
    return baseline_n(slug, outlet, label, now)[0]


def late_after(label: str) -> float:
    """A read this many minutes after its checkpoint's time is late (the Mac slept): it is kept under that label with
    its true age, but never compared with the usual and never part of it. The wait per post (up to 15%) and a missed
    tick or two are well inside it."""
    off = OFFSET[label]
    return max(60.0, off * 0.5)


def _is_late(pt: dict) -> bool:
    if pt.get("late"):
        return True
    age, lbl = pt.get("age_min"), pt.get("label")
    return (isinstance(age, (int, float)) and not isinstance(age, bool) and lbl in OFFSET
            and age - OFFSET[lbl] > late_after(lbl))


EARLY_BY = 60.0                      # minutes before its checkpoint's time: a point read sooner is early


def _is_early(pt: dict) -> bool:
    """A point read over an hour before its checkpoint's time (only a backfill from the nightly snapshots can be: a
    live read waits for its time). Kept with its true age, like a late read: never compared with the usual and never
    part of it. 10.20: a backfill took 15:51 snapshots as 24h for 10.19 afternoon posts, 22.4 hours in."""
    if pt.get("early") is True:
        return True
    age, lbl = pt.get("age_min"), pt.get("label")
    return (isinstance(age, (int, float)) and not isinstance(age, bool) and lbl in OFFSET
            and OFFSET[lbl] - age > EARLY_BY)


def on_time(pt: dict) -> bool:
    """Neither late nor early: a point that stands for its checkpoint (the usual, x the usual, the learner's 24h)."""
    return not _is_late(pt) and not _is_early(pt)


def _early(views, label: str, base: Tuple[Optional[float], int]) -> Optional[dict]:
    med, n = base
    if label not in EARLY_LABELS or not isinstance(views, (int, float)) or not med or med <= 0:
        return None
    return {"label": label, "x": round(float(views) / med, 2), "vs_n": n}


# ------------------------------------------------------------------------------------------------------ the posts
@dataclass
class Post:
    outlet: str
    pid: str
    went: datetime
    board: date
    slot: str
    s: dict = field(repr=False)
    rec: dict = field(repr=False)

    @property
    def plat(self) -> str:
        return PLAT[self.outlet]


def _pid(rec: dict) -> str:
    return str(rec.get("platform_id") or rec.get("post_id") or rec.get("video_id") or "")


def _board_posts(cfg: C.Config, acct: C.Account, now: datetime, days: Optional[int] = SCOPE_DAYS) -> List[Post]:
    """The account's posts on the boards (with a post or video id, gone public by now), from `days` before
    yesterday up to today; every board when days is None."""
    today = now.astimezone(cfg.tz()).date()
    first = today - timedelta(days=days + 1) if days is not None else date.min
    out: List[Post] = []
    for path in B.all_boards(acct):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day < first or day > today:
            continue
        bd = store.read(path, None)
        if not isinstance(bd, dict):
            continue
        for k, s in sorted((bd.get("slots") or {}).items()):
            if not isinstance(s, dict) or not s.get("publish_at"):
                continue
            for outlet in OUTLETS:
                rec = (s.get("outlets") or {}).get(outlet)
                if not isinstance(rec, dict) or rec.get("privacy") == ST.PRIVATE:
                    continue
                pid = _pid(rec)
                if not pid or not (rec.get("post_id") or rec.get("video_id") or rec.get("platform_id")):
                    continue
                try:
                    went = _when(ST.went_public(outlet, rec, s))
                except (KeyError, TypeError, ValueError):
                    went = None
                if went is None or went > now:
                    continue                      # not public yet: the clock has not started
                out.append(Post(outlet, pid, went, day, str(s.get("slot") or k), s, rec))
    # One post per platform id: the nightly read can match a sibling clip's post (provider "matched"), so two slots
    # may hold one id. It is read once, under the slot that posted it.
    best: Dict[Tuple[str, str], Post] = {}
    for post in out:
        k = (post.outlet, post.pid)
        if k not in best or _rank(post.rec) > _rank(best[k].rec):
            best[k] = post
    return list(best.values())


def _rank(rec: dict) -> Tuple[bool, bool]:
    prov = str(rec.get("provider") or "")
    return prov == "direct", prov != "matched"


def _meta(p: Post) -> dict:
    s = p.s
    try:
        title = ST._title(s, p.outlet)
    except Exception:  # noqa: BLE001  (a words file that will not read never stops a read)
        title = str(s.get("slug") or "")
    return {"went": _iso(p.went), "board": p.board.isoformat(), "slot": p.slot, "clip": str(s.get("slug") or ""),
            "fmt": str(s.get("format") or ""), "dur": round(ST._sent_secs(s, p.outlet), 1), "title": title[:160]}


def _fresh(e: Optional[dict]) -> dict:
    e = dict(e or {})
    e.setdefault("points", [])
    e.setdefault("missed", [])
    e.setdefault("early", None)
    e.setdefault("refreshed_at", None)
    e.setdefault("backfill", False)
    e.setdefault("gone_at", None)
    return e


# ------------------------------------------------------------------------------------------------------ planning
@dataclass
class Plan:
    post: Post
    label: Optional[str]                 # the label read now (None: nothing to read)
    missed: List[str]
    entry: dict
    age_min: int = 0


def _replaceable(e: dict, label: str, age_min: float) -> bool:
    """The label's point came from the nightly snapshots (a backfill) and a live read now would be on time: the live
    read is the checkpoint, so it is asked for and replaces that point (once a live read has failed GIVE_UP times
    there, the snapshot's point stays)."""
    pt = _point(e, label)
    return (pt is not None and pt.get("src") == "history" and not pt.get("live_failed")
            and age_min - OFFSET[label] <= late_after(label))


def plan(post: Post, entry: Optional[dict], now: datetime) -> Optional[Plan]:
    """What one post needs now: a read under the latest due label (when it has none, or only a snapshot's point that
    an on-time read can replace), and the earlier due labels it never got written as missed. None when nothing is
    due or it is gone."""
    e = _fresh(entry)
    if e.get("gone_at"):
        return None
    done = {p.get("label") for p in e["points"] if isinstance(p, dict)} | set(e["missed"])
    passed = [lbl for lbl in ORDER if now >= due_at(post.went, post.pid, lbl)]
    if not passed:
        return None
    last = passed[-1]
    missed = [lbl for lbl in passed[:-1] if lbl not in done]
    age = int(round((now - post.went).total_seconds() / 60))
    if last in done and not _replaceable(e, last, age):
        return Plan(post, None, missed, e, age) if missed else None
    return Plan(post, last, missed, e, age)


# ------------------------------------------------------------------------------------------------------ the run
@dataclass
class Tally:
    reads: int = 0
    missed: int = 0
    gone: int = 0
    refreshed: int = 0
    by: Dict[str, int] = field(default_factory=dict)
    errors: List[dict] = field(default_factory=list)
    lines: List[str] = field(default_factory=list)
    analytics: bool = False


def _unsupported() -> Dict[str, Dict[str, dict]]:
    d = store.read(_folder() / "unsupported.json", {})
    return d if isinstance(d, dict) else {}


def unsupported(platform: str, now: Optional[datetime] = None) -> List[str]:
    """The extra metrics this platform refused in the last 30 days (they are not asked for)."""
    now = _utc(now)
    out = []
    for m, rec in (_unsupported().get(platform) or {}).items():
        at = _when((rec or {}).get("at")) if isinstance(rec, dict) else None
        if at is not None and now - at < timedelta(days=UNSUPPORTED_DAYS):
            out.append(m)
    return sorted(out)


def _remember(platform: str, metric: str, why: str, now: datetime) -> None:
    with store.edit(_folder() / "unsupported.json", dict) as d:
        per = d.get(platform) if isinstance(d.get(platform), dict) else {}
        per[metric] = {"at": _iso(now), "why": str(why)[:200]}
        d[platform] = per


class _Stop(Exception):
    """The platform said stop (a block, a limit, the sign-in): no more calls to it this tick."""

    def __init__(self, why: str, err: Optional[BaseException] = None):
        super().__init__(why)
        self.err = err


def _kind(err: BaseException) -> str:
    try:
        return G.classify(err).kind
    except Exception:  # noqa: BLE001
        return "other"


def _meta_wait(cfg: C.Config, a: C.Account, plat: str, now: datetime) -> str:
    """'' when Meta may be asked now; else why not (a guard hold on reads, or the poster's own Meta hold)."""
    words = OW.held(cfg, a, plat, "read", now)
    if words:
        return words
    from . import outlets as O
    return O.meta_hold(a) or ""


def _after_meta(cfg: C.Config, a: C.Account, plat: str, now: datetime) -> None:
    try:
        OW.note_usage(cfg, a, plat, D.last_usage(), now)
    except Exception:  # noqa: BLE001  (the guard's note never stops a read)
        pass


def _trip(cfg: C.Config, a: C.Account, plat: str, err: BaseException, now: datetime) -> None:
    """A block, a limit or a broken sign-in, heard by the guard when the account's guard is on."""
    if _kind(err) not in TRIP_KINDS or SW.mode(a, "guard") != "on":
        return
    try:
        OW.tripwire(cfg, a, plat, "read", err, now)
    except Exception:  # noqa: BLE001
        pass


def _call(cfg: C.Config, a: C.Account, plat: str, now: datetime, fn, *args):
    """One Meta call between the guard's checks: the holds before it, the usage note after it."""
    wait = _meta_wait(cfg, a, plat, now)
    if wait:
        raise _Stop(f"not read now, {wait}")
    try:
        got = fn(*args)
    except D.PlatformError as e:
        _after_meta(cfg, a, plat, now)
        if _kind(e) in STOP_KINDS:
            _trip(cfg, a, plat, e, now)
            raise _Stop(f"{NAME.get(plat + '_reels', plat.title())} said stop: {str(e)[:160]}", e)
        raise
    except net.Offline as e:
        raise _Stop(str(e)[:160], e)
    except (OSError, TimeoutError) as e:
        raise _Stop(f"no answer: {type(e).__name__}", e)
    else:
        _after_meta(cfg, a, plat, now)
        return got
    finally:
        SLEEP(RNG.uniform(0.2, 0.6))


def _extras(cfg: C.Config, a: C.Account, p: Post, label: str, now: datetime, t: Tally) -> Dict[str, object]:
    """The extra metrics in calls of their own: a refused set is asked one metric at a time, once; a metric refused
    alone is remembered. A failure here never costs the base numbers."""
    plat = p.plat
    out: Dict[str, object] = {}
    reader = API.ig_extra if plat == "instagram" else API.fb_extra
    names = [m for m in (API.IG_EXTRA if plat == "instagram" else API.FB_EXTRA) if m not in unsupported(plat, now)]

    def one_set(ms: List[str]) -> None:
        try:
            out.update(_call(cfg, a, plat, now, reader, a, p.pid, ms))
            return
        except D.PlatformError as e:
            if _kind(e) in POST_KINDS:                  # about the post, not a metric: asking alone would not help
                t.errors.append({"at": _iso(now), "platform": plat, "why": f"extra numbers: {str(e)[:160]}"})
                return
            if len(ms) == 1:
                if _kind(e) == "other" and "metric" in str(e).lower():    # Meta names the metric it will not give
                    _remember(plat, ms[0], str(e), now)
                else:
                    t.errors.append({"at": _iso(now), "platform": plat, "why": f"{ms[0]}: {str(e)[:160]}"})
                return
        for m in ms:
            one_set([m])
    if names:
        one_set(names)
    if plat == "facebook" and label == RETENTION_AT and API.FB_RETENTION not in unsupported(plat, now):
        one_set([API.FB_RETENTION])
    return out


def _read_meta(cfg: C.Config, a: C.Account, plans: List[Plan], now: datetime, t: Tally) -> Dict[str, dict]:
    """pid -> the point for each Meta post read; plans whose read failed are left out (their label stays due)."""
    got: Dict[str, dict] = {}
    if not plans:
        return got
    plat = plans[0].post.plat
    cap = META_BATCH // 2 if OW.slow(cfg, a, plat, now) else META_BATCH
    extra_on = SW.mode(a, "checkpoints.extra") == "on"
    post_fails: Dict[str, int] = {}
    for i, pl in enumerate(plans):
        if i >= cap:
            t.lines.append(f"  {a.name} {NAME[pl.post.outlet]}: {len(plans) - cap} more next time (at most {cap} a "
                           f"tick)")
            break
        p = pl.post
        try:
            if plat == "instagram":
                nums = _call(cfg, a, plat, now, API.ig_numbers, a, p.pid)
            else:
                prev = (p.rec.get("metrics") or {}).get("comments")
                last = latest(pl.entry) or {}
                known = prev if isinstance(prev, int) else last.get("comments")
                nums = _call(cfg, a, plat, now, API.fb_numbers, a, p.pid, known if isinstance(known, int) else None)
        except _Stop as s:
            t.lines.append(f"  {a.name} {NAME[p.outlet]}: {s}")
            if s.err is not None:
                t.errors.append({"at": _iso(now), "platform": plat, "why": str(s)[:200]})
            break
        except D.PlatformError as e:
            k = _kind(e)
            if k == "gone":
                got[p.pid] = {"gone": True}
                continue
            t.errors.append({"at": _iso(now), "platform": plat, "why": str(e)[:200]})
            got[p.pid] = {"failed": True}             # a try on this post; GIVE_UP of them and the label is missed
            if k in POST_KINDS:
                post_fails[k] = post_fails.get(k, 0) + 1
                if post_fails[k] >= POST_STOP:        # more than one post: the account, not the post
                    t.lines.append(f"  {a.name} {NAME[p.outlet]}: {post_fails[k]} posts refused this tick "
                                   f"({str(e)[:120]}); the rest next time")
                    break
            continue
        if not nums:
            got[p.pid] = {"failed": True}
            continue
        pt = dict(nums, label=pl.label, at=_iso(now), age_min=pl.age_min, extra={}, src="insights")
        if extra_on and pl.label in EXTRA_AT:
            try:
                pt["extra"] = _extras(cfg, a, p, pl.label, now, t)
            except _Stop as s:
                t.lines.append(f"  {a.name} {NAME[p.outlet]} extra numbers: {s}")
                got[p.pid] = pt
                break
        got[p.pid] = pt
    return got


def _yt_ok(cfg: C.Config, a: C.Account, now: datetime, calls: int = 1) -> str:
    """'' when YouTube may be asked (no hold on its reads, units left today); else why not."""
    words = OW.held(cfg, a, "youtube", "read", now)
    if words:
        return f"not read now, {words}"
    if OW.yt_left(cfg, now) < calls + YT_RESERVE:
        return "not read now: this machine's YouTube units for today are spent"
    return ""


def _yt_batches(cfg: C.Config, a: C.Account, ids: List[str], now: datetime, t: Tally) -> Tuple[Dict[str, dict], bool]:
    """videos.list for these ids, 50 a call. ({id: numbers}, finished): finished is False when YouTube (or the
    guard) stopped it part way; ids never asked about are not in the answer and not gone."""
    out: Dict[str, dict] = {}
    for i in range(0, len(ids), API.YT_BATCH):
        chunk = ids[i:i + API.YT_BATCH]
        why = _yt_ok(cfg, a, now)
        if why:
            t.lines.append(f"  {a.name} YouTube: {why}")
            return out, False
        try:
            got = API.yt_stats(a, chunk, cfg=cfg)
        except ST.NotConnected as e:
            t.lines.append(f"  {a.name} YouTube: not connected ({e})")
            return out, False
        except (D.PlatformError, OSError, net.Offline) as e:
            t.errors.append({"at": _iso(now), "platform": "youtube", "why": str(e)[:200]})
            t.lines.append(f"  {a.name} YouTube: could not read ({str(e)[:160]})")
            _trip(cfg, a, "youtube", e, now)
            return out, False
        for vid in chunk:
            out[vid] = got.get(vid) or {"gone": True}
    return out, True


def _analytics_ready(a: C.Account) -> bool:
    if SW.mode(a, "checkpoints.yt_analytics") != "on":
        return False
    try:
        return not CAP.missing(a, "youtube", "analytics")
    except Exception:  # noqa: BLE001
        return False


def _pt_day(t: datetime) -> date:
    return _utc(t).astimezone(OW.PACIFIC).date()


def _analytics(cfg: C.Config, a: C.Account, pl: Plan, pt: dict, now: datetime, t: Tally) -> bool:
    """YouTube Analytics into the point's extra at 72h, 7d and 30d (retention at 7d). False when it said stop."""
    p = pl.post
    start, end = _pt_day(p.went), _pt_day(now)
    try:
        pt["extra"].update(API.yta_totals(a, p.pid, start, end, cfg=cfg))
        if pl.label == RETENTION_AT:
            ret = API.yta_retention(a, p.pid, start, end, cfg=cfg)
            if ret:
                pt["extra"]["retention"] = ret
        t.analytics = True
        return True
    except (D.PlatformError, OSError, ST.NotConnected, net.Offline) as e:
        t.errors.append({"at": _iso(now), "platform": "youtube", "why": f"Analytics: {str(e)[:180]}"})
        t.lines.append(f"  {a.name} YouTube Analytics: could not read ({str(e)[:160]})")
        return False


def _read_yt(cfg: C.Config, a: C.Account, plans: List[Plan], now: datetime, t: Tally) -> Dict[str, dict]:
    got: Dict[str, dict] = {}
    if not plans:
        return got
    nums, _ = _yt_batches(cfg, a, [pl.post.pid for pl in plans], now, t)
    yta = _analytics_ready(a)
    for pl in plans:
        n = nums.get(pl.post.pid)
        if n is None:
            continue                               # never asked (stopped part way): due again next tick
        if n.get("gone"):
            got[pl.post.pid] = {"gone": True}
            continue
        if not n.get("public"):                    # not public (yet): a try, so a video kept private is not asked
            got[pl.post.pid] = {"failed": True}    # about every tick; GIVE_UP of them and the label is missed
            continue
        pt = {k: n[k] for k in ("views", "likes", "comments") if k in n}
        pt.update(label=pl.label, at=_iso(now), age_min=pl.age_min, extra={}, src="data")
        if yta and pl.label in YTA_AT:
            yta = _analytics(cfg, a, pl, pt, now, t)
        got[pl.post.pid] = pt
    return got


def _write(a: C.Account, plans: List[Plan], got: Dict[str, dict], now: datetime, t: Tally,
           bases: Dict[Tuple[str, str], Tuple[Optional[float], int]]) -> List[Tuple[Post, dict]]:
    """Each plan's outcome into its series file (store.edit per file). The points written, for the boards."""
    idx = {o: _index(a.slug, o) for o in {pl.post.outlet for pl in plans}}
    by_file: Dict[Path, List[Plan]] = {}
    for pl in plans:
        by_file.setdefault(_home(idx[pl.post.outlet], a.slug, pl.post.outlet, pl.post.pid, pl.post.went),
                           []).append(pl)
    written: List[Tuple[Post, dict]] = []
    for path, pls in sorted(by_file.items()):
        with store.edit(path, dict) as d:
            for pl in pls:
                p = pl.post
                res = got.get(p.pid)
                missed = list(pl.missed)
                if res is None and not missed:
                    continue                              # not asked this tick (held, not public yet): unchanged
                e = _fresh(d.get(p.pid) if isinstance(d.get(p.pid), dict) else pl.entry)
                e.update(_meta(p))
                if res and res.get("gone"):
                    e["gone_at"] = _iso(now)
                    t.gone += 1
                    if p.outlet == "youtube_shorts":      # YouTube III.E.4: its numbers go with it
                        e["points"], e["early"], e["refreshed_at"] = [], None, None
                elif res and not res.get("failed") and pl.label:
                    pt = {k: v for k, v in res.items() if k in METRICS or k in (
                        "label", "at", "age_min", "extra", "src")}
                    if _is_late(pt):
                        pt["late"] = True                 # the Mac slept: kept, never compared with the usual
                    e["points"] = [x for x in e["points"] if x.get("label") != pl.label] + [pt]
                    e["points"].sort(key=lambda x: ORDER.index(x["label"]) if x.get("label") in OFFSET else 99)
                    e["missed"] = [x for x in e["missed"] if x != pl.label]
                    e["refreshed_at"] = _iso(now)
                    e.pop("tries", None)
                    early = None if pt.get("late") else _early(pt.get("views"), pl.label,
                                                               bases.get((p.outlet, pl.label), (None, 0)))
                    if early:
                        e["early"] = early
                    t.reads += 1
                    t.by[p.outlet] = t.by.get(p.outlet, 0) + 1
                    written.append((p, pt))
                elif pl.label and res is not None:       # asked, and it failed: a few tries, then missed
                    tries = e.get("tries") if isinstance(e.get("tries"), dict) else {}
                    n = int(tries.get("n") or 0) + 1 if tries.get("label") == pl.label else 1
                    if n >= GIVE_UP:
                        old = _point(e, pl.label)
                        if old is not None:
                            old["live_failed"] = True     # a snapshot's point stays: not asked for again
                        else:
                            missed.append(pl.label)
                        e.pop("tries", None)
                    else:
                        e["tries"] = {"label": pl.label, "n": n}
                if missed:
                    have = {x.get("label") for x in e["points"] if isinstance(x, dict)}
                    missed = [x for x in missed if x not in have]
                    e["missed"] = sorted(set(e["missed"]) | set(missed),
                                         key=lambda x: ORDER.index(x) if x in OFFSET else 99)
                    t.missed += len(set(missed))
                have = {x.get("label") for x in e["points"] if isinstance(x, dict)}
                e["missed"] = [x for x in e["missed"] if x not in have]      # never both read and missed
                d[p.pid] = e
    for o, ix in idx.items():
        _drop_copies(ix, [pl.post.pid for pl in plans if pl.post.outlet == o])
    return written


def _boards(cfg: C.Config, a: C.Account, written: List[Tuple[Post, dict]], now: datetime) -> int:
    """The board's latest numbers from the points just read, only where they are newer than the board's (the same
    keys as the nightly read writes). A missing number keeps the board's. Returns how many records moved."""
    per_day: Dict[date, List[Tuple[Post, dict]]] = {}
    for p, pt in written:
        per_day.setdefault(p.board, []).append((p, pt))
    n = 0
    for day, items in sorted(per_day.items()):
        bd = B.read(a, day) or {}

        def newer(p: Post, pt: dict, slots: dict) -> bool:
            r = ((slots.get(p.slot) or {}).get("outlets") or {}).get(p.outlet)
            if not isinstance(r, dict) or _pid(r) != p.pid:
                return False
            old = _when((r.get("metrics") or {}).get("at"))
            return old is None or old < _when(pt["at"])           # type: ignore[operator]
        todo = [(p, pt) for p, pt in items if newer(p, pt, bd.get("slots") or {})]
        if not todo:
            continue
        with B.edit(cfg, a, day, create=False) as b:
            for p, pt in todo:
                if not newer(p, pt, b.get("slots") or {}):
                    continue
                r = b["slots"][p.slot]["outlets"][p.outlet]
                old = r.get("metrics") or {}
                r["metrics"] = {**{m: int(pt[m] if isinstance(pt.get(m), (int, float)) else old.get(m) or 0)
                                   for m in METRICS}, "at": pt["at"]}
                n += 1
    return n


def _refresh_yt(cfg: C.Config, a: C.Account, now: datetime, skip: set, t: Tally) -> None:
    """YouTube III.E.4: every stored YouTube series not read in 29 days is asked about again (50 ids a unit); one
    YouTube no longer has loses its numbers."""
    old: Dict[Path, List[str]] = {}
    for pid, (p, e, _) in _index(a.slug, "youtube_shorts").items():
        if e.get("gone_at") or pid in skip or not e.get("points"):
            continue
        last = _when(e.get("refreshed_at")) or max(
            (_when(x.get("at")) for x in e["points"] if isinstance(x, dict) and _when(x.get("at"))),
            default=None)
        if last is None or now - last >= timedelta(days=REFRESH_DAYS):
            old.setdefault(p, []).append(pid)
    ids = [pid for pids in old.values() for pid in pids]
    if not ids:
        return
    nums, _ = _yt_batches(cfg, a, ids, now, t)
    for path, pids in old.items():
        with store.edit(path, dict) as d:
            for pid in pids:
                n = nums.get(pid)
                e = d.get(pid)
                if n is None or not isinstance(e, dict):
                    continue
                if n.get("gone"):
                    e.update(points=[], early=None, refreshed_at=None, gone_at=_iso(now))
                    e.pop("latest", None)
                    t.gone += 1
                else:
                    e["latest"] = dict({k: n[k] for k in ("views", "likes", "comments") if k in n}, at=_iso(now))
                    e["refreshed_at"] = _iso(now)
                    t.refreshed += 1


def _stale_at(x: dict, went: Optional[datetime]) -> Optional[datetime]:
    """When a stored YouTube number was read (its own 'at', else its checkpoint's time)."""
    at = _when(x.get("at"))
    if at is None and went is not None and x.get("label") in OFFSET:
        at = went + timedelta(minutes=OFFSET[x["label"]])
    return at


def _expire_entry(e: dict, now: datetime) -> int:
    """Delete this YouTube series' numbers read EXPIRE_DAYS ago or more (labels, ages and the missed list stay, so the
    post is not read again for them). How many numbers went."""
    cut = now - timedelta(days=EXPIRE_DAYS)
    went = _when(e.get("went"))
    n = 0
    for x in e.get("points") or []:
        if not isinstance(x, dict) or not any(k in x for k in METRICS) and not x.get("extra"):
            continue
        at = _stale_at(x, went)
        if at is not None and at <= cut:
            for k in METRICS:
                x.pop(k, None)
            x["extra"] = {}
            x["expired"] = True
            n += 1
            ea = e.get("early")
            if isinstance(ea, dict) and ea.get("label") == x.get("label"):
                e["early"] = None
    lt = e.get("latest")
    if isinstance(lt, dict) and (_when(lt.get("at")) is None or _when(lt.get("at")) <= cut):    # type: ignore
        e.pop("latest", None)
        n += 1
    return n


def _needs_expiry(e: dict, now: datetime) -> bool:
    cut = now - timedelta(days=EXPIRE_DAYS)
    went = _when(e.get("went"))
    for x in e.get("points") or []:
        if isinstance(x, dict) and (any(k in x for k in METRICS) or x.get("extra")):
            at = _stale_at(x, went)
            if at is not None and at <= cut:
                return True
    lt = e.get("latest")
    return isinstance(lt, dict) and (_when(lt.get("at")) is None or _when(lt.get("at")) <= cut)  # type: ignore


def stale_youtube(slug: Optional[str], now: datetime) -> int:
    """How many stored YouTube series hold numbers read 30 days ago or more (files only)."""
    return sum(1 for _, _, _, p in _files(slug, "youtube_shorts") for e in _read(p).values()
               if isinstance(e, dict) and _needs_expiry(e, now))


def expire_youtube(now: Optional[datetime] = None) -> int:
    """YouTube III.E.4, local files only, for every account whether its checkpoints are on or off: a YouTube number
    read 30 days ago or more is deleted (a checkpoint is how a post stood at that hour, so it cannot be refreshed,
    only deleted; the monthly refresh keeps 'latest' current while the switch is on). How many numbers went. Never
    makes a file or a folder."""
    now = _utc(now)
    n = 0
    for _, _, _, p in _files(None, "youtube_shorts"):
        if not any(isinstance(e, dict) and _needs_expiry(e, now) for e in _read(p).values()):
            continue
        with store.edit(p, dict) as d:
            for e in d.values():
                if isinstance(e, dict):
                    n += _expire_entry(e, now)
    return n


def _run_account(cfg: C.Config, a: C.Account, now: datetime) -> Tally:
    t = Tally()
    posts = _board_posts(cfg, a, now)
    entries: Dict[Tuple[str, str], dict] = {}
    for p in posts:
        if (p.outlet, p.pid) not in entries:
            entries[(p.outlet, p.pid)] = series(a.slug, p.outlet, p.pid) or {}
    plans = [pl for pl in (plan(p, entries.get((p.outlet, p.pid)), now) for p in posts) if pl is not None]
    if not plans:
        _refresh_yt(cfg, a, now, set(), t)
        return t
    bases = {(pl.post.outlet, pl.label): baseline_n(a.slug, pl.post.outlet, pl.label, now)
             for pl in plans if pl.label in EARLY_LABELS}
    got: Dict[str, dict] = {}
    for outlet in OUTLETS:
        mine = [pl for pl in plans if pl.post.outlet == outlet and pl.label]
        mine.sort(key=lambda pl: pl.post.went, reverse=True)       # the youngest first: their numbers move most
        if outlet == "youtube_shorts":
            got.update(_read_yt(cfg, a, mine, now, t))
            continue
        if not mine:
            continue
        try:
            got.update(_read_meta(cfg, a, mine, now, t))
        except ST.NotConnected as e:
            t.lines.append(f"  {a.name} {NAME[outlet]}: not connected ({e})")
    written = _write(a, plans, got, now, t, bases)
    try:
        _boards(cfg, a, written, now)
    except (OSError, ValueError, SystemExit) as e:
        t.lines.append(f"  {a.name}: the board's numbers were not updated ({str(e)[:160]})")
    _refresh_yt(cfg, a, now, {pl.post.pid for pl in plans if pl.post.outlet == "youtube_shorts"}, t)
    return t


def _status_path() -> Path:
    return _folder() / "status.json"


def _note_status(cfg: C.Config, a: C.Account, t: Tally, now: datetime) -> None:
    day = now.astimezone(cfg.tz()).date().isoformat()
    keep = (now.astimezone(cfg.tz()).date() - timedelta(days=14)).isoformat()
    with store.edit(_status_path(), dict) as d:
        accts = d.get("accounts") if isinstance(d.get("accounts"), dict) else {}
        s = accts.get(a.slug) if isinstance(accts.get(a.slug), dict) else {}
        s["last_run"] = _iso(now)
        for key, n in (("reads", t.reads), ("missed", t.missed)):
            per = s.get(key) if isinstance(s.get(key), dict) else {}
            per = {k: v for k, v in per.items() if k >= keep}
            per[day] = int(per.get(day) or 0) + int(n)
            s[key] = per
        if t.reads:
            s["last_read"] = _iso(now)
        if t.analytics:
            s["analytics_at"] = _iso(now)
        if t.errors:
            s["errors"] = ((s.get("errors") if isinstance(s.get("errors"), list) else []) + t.errors)[-10:]
            s["last_error"] = t.errors[-1]
        accts[a.slug] = s
        d["accounts"] = accts


def run(cfg: C.Config, now: Optional[datetime] = None, quiet: bool = True) -> List[str]:
    """The 15-minute read: each account with checkpoints on, the posts whose label is due. What it did, in lines
    ([] when quiet and nothing happened). Nothing at all, not even a file, while every account has it off."""
    now = _utc(now or cfg.now())
    lines: List[str] = []
    try:                                  # YouTube's 30-day rule holds with the switch off too (files only)
        gone = expire_youtube(now)
    except (OSError, ValueError) as e:
        gone, lines = 0, [f"checkpoints: old YouTube numbers not deleted ({type(e).__name__}: {str(e)[:160]})"]
    if gone:
        lines.append(f"checkpoints: {gone} YouTube number{'s' if gone != 1 else ''} read 30 days ago deleted "
                     f"(YouTube's 30-day rule)")
    accts = _accounts(cfg)
    if not accts:
        return lines + ([] if quiet else ["checkpoints: off for every account (factory.py social on <account> "
                                          "checkpoints)"])
    with _lock() as got:
        if not got:
            return lines + ([] if quiet else ["checkpoints: another run is under way"])
        for a in accts:
            try:
                t = _run_account(cfg, a, now)
            except Exception as e:  # noqa: BLE001  (one account never stops the next)
                t = Tally(errors=[{"at": _iso(now), "platform": "", "why": f"{type(e).__name__}: {str(e)[:180]}"}],
                          lines=[f"  {a.name}: the checkpoint read failed ({type(e).__name__}: {str(e)[:160]})"])
            _note_status(cfg, a, t, now)
            if t.reads or t.missed or t.gone or t.refreshed or not quiet:
                parts = [f"{n} {NAME[o]}" for o, n in sorted(t.by.items())]
                words = f"{a.name}: {t.reads} checkpoint read{'s' if t.reads != 1 else ''}"
                words += f" ({', '.join(parts)})" if parts else ""
                words += f"; {t.missed} missed" if t.missed else ""
                words += f"; {t.gone} gone" if t.gone else ""
                words += f"; {t.refreshed} YouTube numbers refreshed" if t.refreshed else ""
                lines.append(words)
            lines += t.lines
    return lines


# ------------------------------------------------------------------------------------------------------ backfill
def _history() -> List[Tuple[datetime, dict]]:
    """Every snapshot of the nightly read (state/stats/history/*.json, read only): (when, {post id: numbers})."""
    d = Path(C.STATE) / "stats" / "history"
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem):
            continue
        for r in (store.read(f, {}) or {}).get("reads") or []:
            at = _when(r.get("at")) if isinstance(r, dict) else None
            if at is not None and isinstance(r.get("posts"), dict):
                out.append((at, r["posts"]))
    return sorted(out, key=lambda x: x[0])


def _stats_id(a: C.Account, p: Post) -> str:
    """The nightly read's own id for this post (stats.post_id over the same fields it uses)."""
    r = p.rec
    pid = str(r.get("platform_id") or r.get("video_id") or r.get("publish_id") or r.get("post_id") or "")
    return ST.post_id({"account": a.slug, "platform": ST.DASH_PLATFORM[p.outlet], "pid": pid})


def _tol(label: str) -> float:
    """How far from a label's age a snapshot may be and still stand for it (minutes)."""
    return max(20.0, OFFSET[label] * 0.25)


def history_points(went: datetime, reads: List[Tuple[datetime, list]], now: Optional[datetime] = None,
                   pid: str = "") -> Dict[str, dict]:
    """{label: point} from snapshots [(when, [views, likes, comments, shares, went])]: for each label the snapshot
    closest to its age, within a quarter of it (20 minutes at the least), each snapshot used once. One read before
    the label's time by more than an hour is taken only when no other is near, and is marked early. With `now`, a
    label whose time (its due_at, the live reader's own) has not come yet gets no point: the live reader reads it."""
    cands = []
    for at, vals in reads:
        age = (at - went).total_seconds() / 60
        if age < 0 or not isinstance(vals, list):
            continue
        cands.append((age, at, vals))
    used: set = set()
    out: Dict[str, dict] = {}
    for lbl in ORDER:
        if now is not None and _utc(now) < due_at(went, pid, lbl):
            continue
        best = None
        for age, at, vals in cands:
            gap = abs(age - OFFSET[lbl])
            if at in used or gap > _tol(lbl):
                continue
            rank = (OFFSET[lbl] - age > EARLY_BY, gap)            # on time before early, then the closest
            if best is None or rank < best[0]:
                best = (rank, age, at, vals)
        if best is None:
            continue
        _, age, at, vals = best
        used.add(at)
        pt: dict = {"label": lbl, "at": _iso(at), "age_min": int(round(age))}
        for i, m in enumerate(METRICS):
            if i < len(vals) and isinstance(vals[i], (int, float)) and not isinstance(vals[i], bool):
                pt[m] = int(vals[i])
        pt.update(extra={}, src="history")
        if _is_early(pt):
            pt["early"] = True
        out[lbl] = pt
    return out


def backfill(cfg: C.Config, now: Optional[datetime] = None, redo: bool = False) -> List[str]:
    """Points for posts from before the checkpoints ran, from the nightly read's snapshots (marked backfill and src
    history). A label with a real point keeps it; a label whose time has not come gets none (the live reader reads
    it). A label long past with no snapshot near it is written as missed, apart from the latest one of a post still
    in reach, which the next run reads. With redo, a snapshot's point read early is taken again from the snapshots
    there are now when one of them is on time (a live read's point is never touched). Local files only."""
    accts = _accounts(cfg)
    if not accts:
        raise SystemExit("checkpoints is off for every account: factory.py social on <account> checkpoints, then "
                         "factory.py checkpoints backfill")
    now = _utc(now or cfg.now())
    hist = _history()
    lines: List[str] = []
    with _lock(wait=True):
        for a in accts:
            posts = _board_posts(cfg, a, now, days=None)
            idx = {o: _index(a.slug, o) for o in OUTLETS}
            per_file: Dict[Path, List[Tuple[Post, Dict[str, dict]]]] = {}
            for p in posts:
                sid = _stats_id(a, p)
                reads = [(at, ps[sid]) for at, ps in hist if sid in ps]
                per_file.setdefault(_home(idx[p.outlet], a.slug, p.outlet, p.pid, p.went), []).append(
                    (p, history_points(p.went, reads, now, p.pid)))
            n_posts = n_points = n_redone = 0
            still_early: List[str] = []
            reach = now - timedelta(days=SCOPE_DAYS)
            for path, items in sorted(per_file.items()):
                if not redo and not any(pts for _, pts in items):
                    continue
                with store.edit(path, dict) as d:
                    for p, pts in items:
                        if not pts and not (redo and isinstance(d.get(p.pid), dict)):
                            continue
                        e = _fresh(d.get(p.pid) if isinstance(d.get(p.pid), dict) else None)
                        if e.get("gone_at"):
                            continue
                        e.update(_meta(p))
                        redone = []
                        if redo:
                            for x in e["points"]:
                                if not isinstance(x, dict) or x.get("src") != "history" or not _is_early(x):
                                    continue
                                new = pts.get(str(x.get("label")))
                                if new is not None and on_time(new):
                                    redone.append(new)
                                else:
                                    still_early.append(f"{p.board:%m.%d} {p.slot} {NAME[p.outlet]} {x.get('label')} "
                                                       f"at {x.get('age_min')} min")
                            if redone:
                                again = {x["label"] for x in redone}
                                e["points"] = [x for x in e["points"] if x.get("label") not in again] + redone
                                n_redone += len(redone)
                        have = {x.get("label") for x in e["points"] if isinstance(x, dict)}
                        add = [pt for lbl, pt in pts.items() if lbl not in have]
                        if not add and not redone:
                            continue
                        e["points"] = sorted(e["points"] + add, key=lambda x: ORDER.index(x["label"])
                                             if x.get("label") in OFFSET else 99)
                        e["backfill"] = True
                        e["refreshed_at"] = e.get("refreshed_at") or max(x["at"] for x in add + redone)
                        labels = {x.get("label") for x in e["points"]}
                        passed = [lbl for lbl in ORDER if now >= due_at(p.went, p.pid, lbl)]
                        last_pt = max((ORDER.index(lbl) for lbl in labels if lbl in OFFSET), default=-1)
                        keep_open = passed and p.went >= reach and ORDER.index(passed[-1]) > last_pt
                        gaps = [lbl for lbl in (passed[:-1] if keep_open else passed) if lbl not in labels]
                        e["missed"] = sorted((set(e["missed"]) | set(gaps)) - labels, key=ORDER.index)
                        d[p.pid] = e
                        n_posts += 1 if add else 0
                        n_points += len(add)
            for o in OUTLETS:
                _drop_copies(idx[o], [p.pid for p in posts if p.outlet == o])
            lines.append(f"{a.name}: {n_posts} post{'s' if n_posts != 1 else ''} backfilled with {n_points} "
                         f"point{'s' if n_points != 1 else ''} from {len(hist)} snapshot{'s' if len(hist) != 1 else ''}")
            if redo:
                lines.append(f"{a.name}: {n_redone} early point{'s' if n_redone != 1 else ''} taken again from an "
                             f"on-time snapshot; {len(still_early)} still early (no snapshot near the time), kept "
                             f"out of the usual" + (": " + "; ".join(still_early[:12]) if still_early else ""))
        gone = expire_youtube(now)       # YouTube's 30-day rule: an old snapshot's YouTube numbers are not kept
        if gone:
            lines.append(f"{gone} YouTube number{'s' if gone != 1 else ''} from 30 days ago or more left out "
                         f"(YouTube's 30-day rule)")
    return lines


# ------------------------------------------------------------------------------------------------------ early
def early(cfg: C.Config, acct, now: Optional[datetime] = None) -> List[dict]:
    """The account's posts of the last day that are running hot (2x its usual or more at 1h, 3h or 6h) or starting
    slow (half or less): [{pid, outlet, title, board, slot, label, x, vs_n, kind}], hottest first."""
    now = _utc(now or cfg.now())
    a = acct if isinstance(acct, C.Account) else cfg.account(str(acct))
    if not _on(a):
        return []
    out = []
    for outlet in OUTLETS:
        recent = []
        for went, i, pid, e in _by_went(a.slug, outlet):            # newest first: stop at the first too old
            if now - went > timedelta(hours=HOT_HOURS):
                break
            recent.append((i, pid, e))
        for _, pid, e in sorted(recent, key=lambda r: r[0]):         # in the files' own order, as before
            ea = e.get("early")
            if not isinstance(ea, dict) or e.get("gone_at"):
                continue
            x = ea.get("x")
            if not isinstance(x, (int, float)):
                continue
            kind = "hot" if x >= HOT else "slow" if x <= SLOW else ""
            if not kind:
                continue
            out.append({"pid": pid, "outlet": outlet, "title": str(e.get("title") or e.get("clip") or pid),
                        "board": str(e.get("board") or ""), "slot": str(e.get("slot") or ""),
                        "label": str(ea.get("label") or ""), "x": float(x), "vs_n": int(ea.get("vs_n") or 0),
                        "kind": kind, "account": a.slug})
    return sorted(out, key=lambda r: -r["x"])


def _short(title: str, n: int = 30) -> str:
    t = " ".join(str(title or "").split())
    return t if len(t) <= n else t[:n].rstrip(" ,.:;") + "..."


def summary(cfg: C.Config, now: datetime, accts) -> List[dict]:
    """The Today band: the two hottest posts of the last day ("Running hot: Vance had not heard 2.4x at 3h") and the
    slowest one. Nothing while checkpoints is off."""
    rows: List[dict] = []
    for a in accts or []:
        if SOC._is_client(a) or not _on(a):
            continue
        rows += early(cfg, a, now)
    rows.sort(key=lambda r: -r["x"])     # the hottest across every account, before one chip per clip
    seen: set = set()
    clips = []
    for r in rows:                       # one chip per clip: its platform furthest from the usual
        k = (r["account"], r["board"], r["slot"])
        if k not in seen:
            seen.add(k)
            clips.append(r)
    hot = [r for r in clips if r["kind"] == "hot"][:2]
    slow = sorted((r for r in rows if r["kind"] == "slow" and (r["account"], r["board"], r["slot"]) not in
                   {(h["account"], h["board"], h["slot"]) for h in hot}), key=lambda r: r["x"])[:1]
    out = []
    for i, r in enumerate(hot + slow):
        head = "Running hot" if r["kind"] == "hot" else "Slow start"
        out.append({"key": f"checkpoints-{r['kind']}-{r['pid']}",
                    "words": f"{head}: {_short(r['title'])} {fmt_x(r['x'])} at {r['label']}",
                    "href": f"/?date={r['board']}&acct={r['account']}", "level": "calm", "order": 30 + i})
    return out


# ------------------------------------------------------------------------------------------------------ the card
# The card lines wear the Clips page's own .note look (grey, 12.5px), with the figures inked in place, so this part
# adds no CSS of its own: the seam's registry test pins card_css() to "" while no part needs any (seam_issues).
CARD_CSS = ""
_INK = ' style="color:var(--ink);font-weight:650"'
_X = {"hot": ' style="color:var(--ok);font-weight:650"', "slow": ' style="color:var(--warn);font-weight:650"',
      "": _INK}


def card_line(acct, outlet: str, pid: str, now: datetime) -> str:
    """'3h: 1.2K, 2.4x your usual on Facebook' for one post ('' when it has no point yet)."""
    e = series(getattr(acct, "slug", acct), outlet, pid)
    if not e or e.get("gone_at"):
        return ""
    pts = [x for x in e.get("points") or [] if isinstance(x, dict) and x.get("label") in OFFSET
           and isinstance(x.get("views"), (int, float))]
    pt = max(pts, key=lambda x: ORDER.index(x["label"])) if pts else None
    if not pt:
        return ""
    views = f'<b{_INK}>{html.escape(fmt_n(pt["views"]))}</b>'
    if not on_time(pt):                   # read late (the Mac slept) or early: its true age, no comparison
        said = f'{pt["label"]} (read at {html.escape(_age_words(pt.get("age_min")))}): {views} on {NAME[outlet]}'
        return f'<div class="note cp-line">{said}</div>'
    med, _ = baseline_n(getattr(acct, "slug", acct), outlet, pt["label"], now, exclude=pid)
    cls = ""
    if med and med > 0:
        x = float(pt["views"]) / med
        cls = "hot" if x >= HOT else "slow" if x <= SLOW else ""
        said = f'{pt["label"]}: {views}, <b{_X[cls]}>{fmt_x(x)}</b> your usual on {NAME[outlet]}'
    else:
        said = f'{pt["label"]}: {views} on {NAME[outlet]}'
    return f'<div class="note cp-line{" " + cls if cls else ""}">{said}</div>'


def _age_words(m) -> str:
    """An age in minutes as the Stats tab writes it: 80 min, 23 h, 4 days."""
    try:
        m = float(m)
    except (TypeError, ValueError):
        return "a later time"
    if m < 120:
        return f"{m:.0f} min"
    if m < 2880:
        return f"{m / 60:.1f}".rstrip("0").rstrip(".") + " h"
    return f"{m / 1440:.1f}".rstrip("0").rstrip(".") + " days"


def card_part(cfg: C.Config, acct: C.Account, day: date, s: dict, now: datetime) -> str:
    """A posted card's checkpoint lines, one per platform with a point ('' while checkpoints is off)."""
    if not _on(acct) or not isinstance(s, dict) or s.get("status") not in B.SIGNED:
        return ""
    now = _utc(now)
    lines = []
    for outlet in OUTLETS:
        rec = (s.get("outlets") or {}).get(outlet)
        pid = _pid(rec) if isinstance(rec, dict) else ""
        if pid:
            ln = card_line(acct, outlet, pid, now)
            if ln:
                lines.append(ln)
    return f'<div class="cp" aria-label="How it is doing">{"".join(lines)}</div>' if lines else ""


# ------------------------------------------------------------------------------------------------------ the Stats tab
def _norm_extra(outlet: str, extra: dict) -> Dict[str, float]:
    """The four numbers the Stats tab can rank by, from whichever platform's names: avg_watch_s, skip_rate (percent),
    reach, saves. Absent stays absent."""
    out: Dict[str, float] = {}
    for k, div in (("ig_reels_avg_watch_time", 1000.0), ("post_video_avg_time_watched", 1000.0),
                   ("averageViewDuration", 1.0)):
        v = extra.get(k)
        if isinstance(v, (int, float)) and "avg_watch_s" not in out:
            out["avg_watch_s"] = round(float(v) / div, 1)
    v = extra.get("reels_skip_rate")
    if isinstance(v, (int, float)):
        out["skip_rate"] = round(float(v) * (100.0 if v <= 1 else 1.0), 1)
    for k in ("reach", "post_impressions_unique"):
        v = extra.get(k)
        if isinstance(v, (int, float)) and "reach" not in out:
            out["reach"] = float(v)
    v = extra.get("saved")
    if isinstance(v, (int, float)):
        out["saves"] = float(v)
    return out


def _latest_extra(e: dict) -> dict:
    for pt in sorted((p for p in e.get("points") or [] if isinstance(p, dict) and p.get("label") in OFFSET),
                     key=lambda p: ORDER.index(p["label"]), reverse=True):
        if isinstance(pt.get("extra"), dict) and pt["extra"]:
            return pt["extra"]
    return {}


def _json(code: int, obj) -> Tuple[int, str, bytes]:
    return code, "application/json; charset=utf-8", json.dumps(obj, separators=(",", ":")).encode()


def _bad(words: str) -> Tuple[int, str, bytes]:
    return 400, "text/plain; charset=utf-8", f"bad request: {words}".encode()


def get_series(cfg: C.Config, q: dict) -> Tuple[int, str, bytes]:
    """GET /stats/series: files only, never the network.
    ?acct=&outlet=&pid=   one post: its points, missed labels, early read and the account's usual at each label.
    ?view=extras          per post, the numbers the Top clips can rank by ({"on": [accounts], "posts": {...}}), and
                          which posts have a series at all ("tracked": ["platform|id"]), so the Stats tab offers How
                          it grew only where there is something to show."""
    from . import accounts as AC
    own = AC.own_view(cfg)
    on = [a for a in SOC.accounts(own) if _on(a)]
    if str(q.get("view") or "") == "extras":
        posts: Dict[str, dict] = {}
        tracked: List[str] = []
        for a in on:
            for outlet in OUTLETS:
                for pid, e in _entries(a.slug, outlet):
                    key = f"{ST.DASH_PLATFORM[outlet]}|{pid}"
                    tracked.append(key)
                    if e.get("gone_at"):
                        continue
                    got = _norm_extra(outlet, _latest_extra(e))
                    if got:
                        posts[key] = got
        return _json(200, {"on": [a.slug for a in on], "posts": posts, "tracked": sorted(set(tracked))})
    slug, outlet, pid = str(q.get("acct") or ""), str(q.get("outlet") or ""), str(q.get("pid") or "")
    if outlet not in OUTLETS:
        return _bad("outlet must be one of " + ", ".join(OUTLETS))
    if not _PID.match(pid):
        return _bad("pid")
    a = own.accounts.get(slug)
    if a is None or SOC._is_client(a):
        return _bad("acct")
    now = _utc(cfg.now())
    body: dict = {"acct": slug, "outlet": outlet, "pid": pid, "on": _on(a), "labels": [list(x) for x in LABELS],
                  "names": EXTRA_WORDS}
    if not _on(a):
        return _json(200, body)
    e = series(slug, outlet, pid)
    body["tracked"] = e is not None
    if e is not None:
        if e.get("gone_at"):
            e["points"] = [] if outlet == "youtube_shorts" else e.get("points") or []
        e.pop("tries", None)
        for pt in e.get("points") or []:     # the Stats tab keeps a late or an early read off the usual's line
            if isinstance(pt, dict) and pt.get("label") in OFFSET:
                if _is_late(pt):
                    pt["late"] = True
                elif _is_early(pt):
                    pt["early"] = True
        body.update(e)
    usual = {}
    for lbl in ORDER:
        med, n = baseline_n(slug, outlet, lbl, now, exclude=pid)
        if med is not None:
            usual[lbl] = {"median": med, "n": n}
    body["usual"] = usual
    return _json(200, body)


# ------------------------------------------------------------------------------------------------------ doctor
def _ago(t: Optional[datetime], now: datetime) -> str:
    if t is None:
        return "never"
    mins = int((now - t).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 120:
        return f"{mins} min ago"
    if mins < 48 * 60:
        return f"{mins // 60} hours ago"
    return f"{mins // 1440} days ago"


def tracked(slug: str, now: datetime) -> int:
    """Posts with a series that went public in the last 31 days and are not gone."""
    cut = now - timedelta(days=SCOPE_DAYS)
    n = 0
    for outlet in OUTLETS:
        for _, e in _entries(slug, outlet):
            went = _when(e.get("went"))
            if went is not None and went >= cut and not e.get("gone_at"):
                n += 1
    return n


def _status(slug: str) -> dict:
    d = store.read(_status_path(), {})
    s = ((d or {}).get("accounts") or {}).get(slug) if isinstance(d, dict) else None
    return s if isinstance(s, dict) else {}


def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """Per account: off and how to switch it on; or how many posts it tracks, when it last read and what it missed
    today; what the extra metrics and YouTube Analytics wait for. Files only, no network, never raises."""
    now = _utc(now or cfg.now())
    out: List[Tuple[Optional[bool], str, str]] = []
    for a in SOC.accounts(cfg):
        try:
            old = stale_youtube(a.slug, now)
            if old:                       # YouTube's 30-day rule: the 15-minute run deletes them, on or off
                out.append((False, f"{a.name} checkpoints: {old} YouTube post{'s still hold' if old != 1 else ' still holds'} "
                                   f"numbers read 30 days ago or more (YouTube keeps them 30 days at most)",
                            "factory.py checkpoints run (it deletes them; the timer does it every 15 minutes)"))
            if not _on(a):
                out.append((None, f"{a.name} checkpoints: off (factory.py social on {a.slug} checkpoints); "
                                  f"checkpoints.extra and checkpoints.yt_analytics wait for it, YouTube Analytics also "
                                  f"for yt-analytics.readonly ({CAP.yt_connect_line(a, 'analytics', client='F')}) "
                                  f"and the API enabled", ""))
                continue
            st = _status(a.slug)
            last_run, last_read = _when(st.get("last_run")), _when(st.get("last_read"))
            today = now.astimezone(cfg.tz()).date().isoformat()
            missed = int((st.get("missed") or {}).get(today) or 0) if isinstance(st.get("missed"), dict) else 0
            n = tracked(a.slug, now)
            words = (f"{a.name} checkpoints: {n} post{'s' if n != 1 else ''} tracked, last read "
                     f"{_ago(last_read, now)}, {missed} missed today")
            if last_run is None:
                out.append((None, f"{words}; the timer has not run it yet (every 15 minutes)",
                            "factory.py timer install"))
            elif now - last_run > timedelta(hours=2):
                out.append((False, f"{words}; the last run was {_ago(last_run, now)}",
                            "factory.py timer install (com.clipfactory.checkpoints), or factory.py checkpoints run"))
            else:
                out.append((True, words, ""))
            err = st.get("last_error") if isinstance(st.get("last_error"), dict) else None
            if err and _when(err.get("at")) and now - _when(err.get("at")) < timedelta(hours=24):   # type: ignore
                out.append((None, f"{a.name} checkpoints: last error {_ago(_when(err.get('at')), now)}, "
                                  f"{err.get('platform') or 'a platform'}: {str(err.get('why') or '')[:160]}", ""))
            if SW.mode(a, "checkpoints.extra") == "on":
                skip = sorted(set(unsupported("instagram", now)) | set(unsupported("facebook", now)))
                out.append((None, f"{a.name} extra numbers: on at 24h, 7d and 30d"
                            + (f" (not given here: {', '.join(skip)})" if skip else ""), ""))
            else:
                out.append((None, f"{a.name} extra numbers: off (factory.py social on {a.slug} checkpoints.extra)",
                            ""))
            if SW.mode(a, "checkpoints.yt_analytics") != "on":
                out.append((None, f"{a.name} YouTube Analytics: off (factory.py social on {a.slug} "
                                  f"checkpoints.yt_analytics)", ""))
            elif CAP.missing(a, "youtube", "analytics"):
                out.append((None, f"{a.name} YouTube Analytics: needs yt-analytics.readonly ("
                                  f"{CAP.yt_connect_line(a, 'analytics', client='F')}) and the API enabled in the "
                                  f"politics project", ""))
            else:
                out.append((True, f"{a.name} YouTube Analytics: read at 72h, 7d and 30d, last "
                                  f"{_ago(_when(st.get('analytics_at')), now)}", ""))
        except Exception as e:  # noqa: BLE001  (the doctor never stops over one account)
            out.append((False, f"{getattr(a, 'name', a)} checkpoints: could not read ({type(e).__name__}: {e})",
                        "look at state/stats/series/"))
    return out


# ------------------------------------------------------------------------------------------------------ the command
def _show(cfg: C.Config, slug: str, pid: str, outlet: Optional[str]) -> List[str]:
    a = cfg.account(slug)
    outs = [outlet] if outlet else list(OUTLETS)
    for o in outs:
        e = series(a.slug, o, pid)
        if e is None:
            continue
        now = _utc(cfg.now())
        lines = [f"{a.name} {NAME[o]} {pid}: {e.get('title') or e.get('clip') or ''}",
                 f"  went public {e.get('went')}, board {e.get('board')} {e.get('slot')}"
                 + (", backfilled from the nightly snapshots" if e.get("backfill") else "")
                 + (f", gone since {e.get('gone_at')}" if e.get("gone_at") else "")]
        for pt in e.get("points") or []:
            med, n = baseline_n(a.slug, o, pt.get("label"), now, exclude=pid) if pt.get("label") in OFFSET else (None, 0)
            nums = ", ".join(f"{m} {pt[m]}" for m in METRICS if m in pt)
            vs = f", {fmt_x(float(pt['views']) / med)} the usual of {n}" if med and isinstance(
                pt.get("views"), (int, float)) else ""
            if pt.get("expired"):
                nums = "numbers deleted (YouTube's 30-day rule)"
            if _is_late(pt):
                vs = ", read late (never compared with the usual)"
            elif _is_early(pt):
                vs = ", read early (never compared with the usual)"
            lines.append(f"  {str(pt.get('label')):>3} at {pt.get('age_min')} min ({pt.get('src')}): {nums}{vs}")
            for k, v in sorted((pt.get("extra") or {}).items()):
                if k != "retention":
                    lines.append(f"        {EXTRA_WORDS.get(k, k)} ({k}): {v}")
                else:
                    lines.append(f"        retention: {len(v)} points")
        if e.get("missed"):
            lines.append(f"  missed: {', '.join(e['missed'])}")
        if isinstance(e.get("early"), dict):
            ea = e["early"]
            lines.append(f"  early: {fmt_x(float(ea.get('x') or 0))} the usual at {ea.get('label')} "
                         f"(of {ea.get('vs_n')} posts)")
        return lines
    raise SystemExit(f"{a.name}: no checkpoint series for {pid} (factory.py checkpoints status {a.slug})")


def _status_lines(cfg: C.Config, slug: Optional[str]) -> List[str]:
    now = _utc(cfg.now())
    accts = [cfg.account(slug)] if slug else SOC.accounts(cfg)
    out = []
    for a in accts:
        st = _status(a.slug)
        out.append(f"{a.name} ({a.slug}): checkpoints {SW.mode(a, 'checkpoints')}, extra numbers "
                   f"{SW.mode(a, 'checkpoints.extra')}, YouTube Analytics {SW.mode(a, 'checkpoints.yt_analytics')}")
        n = tracked(a.slug, now)
        out.append(f"  {n} post{'s' if n != 1 else ''} tracked; last run {_ago(_when(st.get('last_run')), now)}, last "
                   f"read {_ago(_when(st.get('last_read')), now)}")
        today = now.astimezone(cfg.tz()).date().isoformat()
        reads = (st.get("reads") or {}).get(today, 0) if isinstance(st.get("reads"), dict) else 0
        missed = (st.get("missed") or {}).get(today, 0) if isinstance(st.get("missed"), dict) else 0
        out.append(f"  today: {reads} read{'s' if reads != 1 else ''}, {missed} missed")
        for r in early(cfg, a, now) if _on(a) else []:
            out.append(f"  {'running hot' if r['kind'] == 'hot' else 'slow start'}: {r['title'][:60]} "
                       f"{fmt_x(r['x'])} at {r['label']} on {NAME[r['outlet']]}")
        for err in (st.get("errors") or [])[-3:] if isinstance(st.get("errors"), list) else []:
            out.append(f"  error {err.get('at')}: {err.get('platform')}: {str(err.get('why'))[:140]}")
    for plat in ("instagram", "facebook"):
        skip = unsupported(plat, now)
        if skip:
            out.append(f"{plat.title()} does not give: {', '.join(skip)} (asked again after 30 days)")
    return out


def cli(cfg: C.Config, argv: List[str]) -> None:
    """factory.py checkpoints run | backfill [--redo] | show <account> <id> [--outlet O] | status [account]"""
    ap = argparse.ArgumentParser(prog="factory.py checkpoints",
                                 description="each post's numbers at 1h, 3h, 6h, 24h, 72h, 7d and 30d")
    sub = ap.add_subparsers(dest="what", required=True)
    q = sub.add_parser("run", help="read what is due now (the timer does this every 15 minutes)")
    q.add_argument("--quiet", action="store_true")
    q = sub.add_parser("backfill", help="points for older posts from the nightly read's snapshots (no network)")
    q.add_argument("--redo", action="store_true", help="also take a snapshot's early point again from an on-time "
                                                       "snapshot, where one is near its time now")
    q = sub.add_parser("show", help="one post's series")
    q.add_argument("account")
    q.add_argument("pid", help="the platform's id of the post (a YouTube video id, a Reel id)")
    q.add_argument("--outlet", choices=OUTLETS)
    q = sub.add_parser("status", help="each account's switch, last read, missed labels, hot and slow posts")
    q.add_argument("account", nargs="?")
    a = ap.parse_args(list(argv))
    if a.what == "run":
        lines = run(cfg, quiet=a.quiet)
    elif a.what == "backfill":
        lines = backfill(cfg, redo=a.redo)
    elif a.what == "show":
        lines = _show(cfg, a.account, a.pid, a.outlet)
    else:
        lines = _status_lines(cfg, a.account)
    for ln in lines:
        print(ln)
