"""Ideas: what is winning in each account's niche, read from benchmark channels' Shorts (the social layer, 2026-10-20).

Each account keeps a short list of benchmark YouTube channels (at most 20, `ideas.max_channels`). Once a day, at 14:20
(the `ideas` timer: `factory.py social ideas --quiet`), each channel's Shorts tab is listed through yt-dlp, metadata
only (titles and view counts; no video is downloaded), and the day's numbers are kept as a snapshot. Views per hour
come from the difference between two days' reads of the same Short, so day one has none.

What yt-dlp's flat Shorts listing really gives (checked against yt-dlp 2026.8.19, shortsLockupViewModel): the title and
YouTube's ROUNDED view label ("1.2M views" reads 1200000, "12K views" 12000), and no length. So a count is treated as
only as exact as its label (label_step), views per hour are set only when a count moved by at least VPH_MIN_STEPS steps
of its label (else unknown, never 0), and the length (`dur`) is kept only when yt-dlp gives one.

From a week of snapshots the title patterns of the winners (the top quarter by views per hour) are set against the
bottom half and against our own titles; the coach (`coach card`), the learner and the weekly report read them through
`patterns(acct)` and `winners(acct)`.

Nothing here posts, comments or signs anything, and nothing is chosen for Andrew: the channel list starts empty, the
Ideas page (Reports, Ideas) suggests the scout's own YouTube channels, and he adds them with one click. The switch
(`ideas`, per account, off until he turns it on) gates every read: with it off this module runs no subprocess at all.

Where it reaches the network: only yt-dlp, only from run() (the daytime job, or `factory.py ideas run` by hand), and
only right after net.guard("ideas: yt-dlp"). Never in a page, a GET, the doctor or a test. Never at night (20:30 to
06:15, the night shift's), never at :13 to :17 (the Politics's collect) or :38 to :52 (the hourly scouts' YouTube
listings): one YouTube listing at a time from this Mac. A run that reaches those minutes waits them out and goes on,
for at most MAX_RUN_S; the accounts take turns going first, day by day. A YouTube bot check ("Sign in to confirm you're
not a bot") or a 429 holds Ideas for about a day (HOLD_HOURS, ending before the next day's 14:20) for the whole Mac:
state/social/ideas-hold.json plus each account's own state/<slug>/ideas/hold.json. When the day's scout already saw
a bot check, Ideas stands down for the day without asking.

    state/<slug>/ideas/channels.json            {"channels": [{url, name, why, added_by, added_at}]}
    state/<slug>/ideas/snapshots/YYYY-MM-DD.json {at, channels: [{url, ok, n, why, at}], shorts: [{id, channel, curl,
                                                  title, views, dur, url, at, vph, approx, new}]}
    state/<slug>/ideas/patterns.json            {window_days, features: [{feature, share_top, share_bottom,
                                                  share_ours, n}], winners: [top 20 by views per hour]}
    state/<slug>/ideas/hold.json                {until, why, at, kind}   (a bot check or 429: about a day)
    state/social/ideas-hold.json                the same hold for the whole Mac (an account switched on later too)
    state/<slug>/ideas/last.json                the last read, plus a stand-down or a skipped day (doctor, page)
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import shutil
import subprocess
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import board as B
from . import config as C
from . import net
from . import store
from . import switches as SW

KEY = "ideas"                               # the switch (switches.MODES) and the timer job (registry.JOBS)
MAX_CHANNELS = 20                           # never more per account, whatever the setting says
PLAYLIST_END = 30                           # the newest 30 Shorts of each channel
WINDOW_DAYS = 7                             # patterns read a week of snapshots
VPH_MAX_GAP_DAYS = 4                        # views per hour only against a read at most this old
VPH_MIN_STEPS = 3                           # a rounded count must move this many label steps to give views per hour
KEEP_DAYS = 35                              # snapshots older than this are removed
HOLD_HOURS = 23                             # a bot check or a 429 holds Ideas this long (ends before next day's 14:20)
MAX_RUN_S = 3600                            # a run waits out the scouts' minutes, but reads for an hour at most
SLEEP_S = (5.0, 15.0)                       # a random wait between two channels
YTDLP_TIMEOUT_S = 120
NIGHT = ("20:30", "06:15")                  # the night shift's hours: no yt-dlp at all
BUSY_MINUTES = ((13, 17), (38, 52))         # the Politics's collect (:15) and the hourly scouts (:40, :46, :52)
SCOUT_TAIL = 60000                          # bytes of a scout log read from its end
BOT = re.compile(r"sign in to confirm|confirm you.re not a bot", re.I)
TOO_MANY = re.compile(r"\b429\b|too many requests", re.I)

RNG = random.Random()                       # the wait between channels (a test seeds it)
_sleep = time.sleep                         # (a test replaces it)
_mono = time.monotonic

# the title features, in the order the page and the report show them
FEATURES: Tuple[Tuple[str, str], ...] = (
    ("question", "Asks a question"),
    ("number", "Has a number"),
    ("named_person", "Names a person"),
    ("quote", "Quotes someone"),
    ("caps", "Words in capitals"),
    ("reacts", "Says reacts"),
    ("len_short", "Short title (under 40 letters)"),
    ("len_mid", "Middle title (40 to 70 letters)"),
    ("len_long", "Long title (over 70 letters)"),
)
FEATURE_WORDS = dict(FEATURES)
QUESTION_START = re.compile(r"^(who|what|why|how|when|where|which|is|are|was|were|do|does|did|can|could|will|would|"
                            r"should|has|have)\b", re.I)
REACTS = re.compile(r"\breact(s|ed|ing|ion|ions)?\b", re.I)
QUOTE = re.compile(r"[\"“”][^\"“”]{3,}[\"“”]|‘[^‘’]{3,}’|"
                   r"(?:^|\s)'[^']{3,}'(?:\s|$|[.,:;?])")
WORD = re.compile(r"[A-Za-z][A-Za-z'’.-]*")
# acronyms that are not shouting, and Title Case words that are not names
ACRONYMS = {"GTA", "VI", "V", "IV", "US", "USA", "UK", "TV", "FBI", "CIA", "DOJ", "NFL", "NBA", "AI", "CEO", "GOP",
            "DC", "NYC", "LA", "PS5", "PC", "RP", "IRL", "OK", "I", "A", "AM", "PM", "EU", "UN", "NATO", "ICE", "IRS",
            "FCC", "SEC", "CNN", "MSNBC", "NBC", "ABC", "CBS", "BBC", "PBS", "NPR", "WWE", "UFC", "MMA", "XQC"}
COMMON = set("""a about after again against all almost already also always am an and another any are around as at away
back bad be because been before behind being best better big biggest both breaking but by can cannot caught chat check
clip clips come comes coming could crazy day days did didnt do does doesnt doing done dont down during each end
ending ever every everyone everything exposed fake fast few final finally first for found free from full funny game
gameplay games get gets getting give go goes going gone good got great had has have he her here hilarious him his
history hot how huge i if in insane inside into is it its just keep kids know last late latest leak leaked left let
life like little live long look looks lost made make makes making man many may me meet mode more most much must my
myself need never new news next night no not nothing now of off official officially old on once one only or other our
out over part people play played player playing plays police probably prove proves put rage react reacted reacting
reaction reactions reacts real really reason reasons reveal revealed reveals review right rp run said same say says
season secret see seen she should show shows side so some something soon speech start started stop story stream
streamer streaming such super take takes talk talks than that the their them then there these they thing things
think this those time times to today told too top trailer trailers truth try trying two ultimate under until up us
very video videos vs wait want wants was watch watching way we week went were what when where which while who why will
win wins with without won world worst would wow yes you your yours nobody somebody everybody anyone someone
anybody talking talked actually literally arrested caught gets got getting broke breaks broken destroyed destroys
shocked shocking shocks wild epic moment moments ever officially confirmed confirms leaks rumor rumors update
updates hearing hearings senate house congress president senator judge court""".split())


# ------------------------------------------------------------------------------------------------------ small helpers
def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def _name(acct) -> str:
    return str(getattr(acct, "name", "") or _slug(acct))


def _is_client(acct) -> bool:
    raw = getattr(acct, "raw", None)
    return isinstance(raw, dict) and str(raw.get("owner") or "self") == "client"


def accounts(cfg: C.Config) -> List[C.Account]:
    """The owner's own accounts that work on this machine (cfg.accounts: switched on, here), never a client's."""
    return [a for a in cfg.accounts.values() if not _is_client(a)]


def _account(cfg: C.Config, slug: str) -> C.Account:
    a = cfg.account(str(slug or ""))
    if _is_client(a):
        raise SystemExit(f"{a.name} is a client's account: Ideas is for the owner's own accounts")
    return a


def folder(acct) -> Path:
    """state/<slug>/ideas (at call time)."""
    return store.path(_slug(acct), "ideas")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _when(v) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _local(cfg: C.Config, t: datetime) -> datetime:
    tz = cfg.tz()
    if t.tzinfo is None:
        t = t.replace(tzinfo=tz or timezone.utc)
    return t.astimezone(tz) if tz else t


def _hm(s: str) -> Tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def is_on(acct) -> bool:
    return SW.mode(acct, KEY) == "on"


def max_channels(acct) -> int:
    try:
        n = int(SW.settings(acct).get("ideas.max_channels") or MAX_CHANNELS)
    except (TypeError, ValueError):
        n = MAX_CHANNELS
    return max(1, min(MAX_CHANNELS, n))


# ------------------------------------------------------------------------------------------------------ channel links
_HANDLE = re.compile(r"^@[A-Za-z0-9._-]{3,100}$")
_UC = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_CNAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_TABS = {"videos", "shorts", "streams", "featured", "live", "playlists", "community", "about", "podcasts", "releases"}


def normalize_url(url: str) -> str:
    """A YouTube channel's link in one form (https://www.youtube.com/@Handle, /channel/UC..., or /c/Name), its tab
    (/videos, /shorts ...) and any ?query taken off. SystemExit, in plain words, for anything else: only a channel on
    youtube.com is read."""
    raw = str(url or "").strip()
    if not raw:
        raise SystemExit("Type the channel's YouTube link, such as https://www.youtube.com/@Handle")
    if raw.startswith("@"):
        raw = "https://www.youtube.com/" + raw
    if "://" not in raw:
        raw = "https://" + raw
    try:
        u = urllib.parse.urlsplit(raw)
    except ValueError:
        raise SystemExit(f"{url}: not a link")
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or host not in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        raise SystemExit(f"{url}: only a YouTube channel's link is read (youtube.com/@Handle, /channel/UC..., /c/Name)")
    if u.port not in (None, 80, 443) or u.username or u.password:
        raise SystemExit(f"{url}: only a plain youtube.com link is read")
    parts = [p for p in urllib.parse.unquote(u.path).split("/") if p]
    while parts and parts[-1].lower() in _TABS and len(parts) > 1:
        parts.pop()
    if len(parts) == 1 and _HANDLE.match(parts[0]):
        return "https://www.youtube.com/" + parts[0]
    if len(parts) == 2 and parts[0] == "channel" and _UC.match(parts[1]):
        return "https://www.youtube.com/channel/" + parts[1]
    if len(parts) == 2 and parts[0] == "c" and _CNAME.match(parts[1]):
        return "https://www.youtube.com/c/" + parts[1]
    raise SystemExit(f"{url}: not a channel link; use youtube.com/@Handle, youtube.com/channel/UC... or "
                     f"youtube.com/c/Name")


def _same(a: str, b: str) -> bool:
    return str(a or "").rstrip("/").lower() == str(b or "").rstrip("/").lower()


def _name_from(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail[1:] if tail.startswith("@") else tail


def _clean(s: object, n: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


# ------------------------------------------------------------------------------------------------------ the list
def channels(acct) -> List[dict]:
    """The account's benchmark channels, in the order they were added (a broken file reads as none)."""
    d = store.read(folder(acct) / "channels.json", {})
    rows = d.get("channels") if isinstance(d, dict) else None
    out = []
    for r in rows or []:
        if isinstance(r, dict) and isinstance(r.get("url"), str) and r["url"]:
            out.append(r)
    return out


def add(cfg: C.Config, acct, url: str, name: str = "", why: str = "", by: str = "",
        now: Optional[datetime] = None) -> str:
    """Add one benchmark channel (checked, in one form, never twice, at most max_channels). What it says."""
    a = acct if hasattr(acct, "slug") else _account(cfg, acct)
    clean = normalize_url(url)
    by = _clean(by, 60) or "factory.py"
    now = now or cfg.now()
    limit = max_channels(a)
    said = ""
    with store.edit(folder(a) / "channels.json", dict) as d:
        rows = [r for r in (d.get("channels") or []) if isinstance(r, dict) and r.get("url")]
        if any(_same(r["url"], clean) for r in rows):
            said = f"{a.name}: {clean} is already on the list"
        elif len(rows) >= limit:
            raise SystemExit(f"{a.name} has {len(rows)} benchmark channels, the most it keeps ({limit}). Remove one "
                             f"first.")
        else:
            rows.append({"url": clean, "name": _clean(name, 80) or _name_from(clean), "why": _clean(why, 160),
                         "added_by": by, "added_at": _iso(now)})
            said = f"{a.name}: {clean} added ({len(rows)} of {limit}); Ideas reads it at 14:20"
        d["channels"] = rows
    return said


def remove(cfg: C.Config, acct, url: str, by: str = "", now: Optional[datetime] = None) -> str:
    """Take one channel off the list (its past snapshots stay until they age out)."""
    a = acct if hasattr(acct, "slug") else _account(cfg, acct)
    try:
        clean = normalize_url(url)
    except SystemExit:
        clean = str(url or "").strip()
    gone = None
    with store.edit(folder(a) / "channels.json", dict) as d:
        rows = [r for r in (d.get("channels") or []) if isinstance(r, dict) and r.get("url")]
        keep = []
        for r in rows:
            if gone is None and _same(r["url"], clean):
                gone = r
                continue
            keep.append(r)
        d["channels"] = keep
    if gone is None:
        raise SystemExit(f"{a.name}: {clean} is not on the list")
    return f"{a.name}: {gone.get('name') or clean} removed ({len(keep)} left)"


def _scout_block(slug: str) -> dict:
    try:
        d = json.loads((C.ROOT / "config" / "scout.json").read_text())
    except (OSError, ValueError):
        return {}
    b = d.get(slug) if isinstance(d, dict) else None
    return b if isinstance(b, dict) else {}


def suggestions(acct) -> List[dict]:
    """The scout's own YouTube channels (config/scout.json youtube_channels) not on the list yet, in the scout's
    order: {url, name, why}. Only suggested; Andrew adds them."""
    have = channels(acct)
    out: List[dict] = []
    seen = set()
    for ch in _scout_block(_slug(acct)).get("youtube_channels") or []:
        if not isinstance(ch, dict):
            continue
        try:
            url = normalize_url(str(ch.get("url") or ""))
        except SystemExit:
            continue
        low = url.lower()
        if low in seen or any(_same(r["url"], url) for r in have):
            continue
        seen.add(low)
        kind = str(ch.get("kind") or ch.get("rights") or "").strip()
        why = "the scout's channel" + (f" ({kind})" if kind else "")
        out.append({"url": url, "name": _clean(ch.get("name") or ch.get("person") or _name_from(url), 80),
                    "why": why})
    return out


def seed(cfg: C.Config, acct, by: str = "", now: Optional[datetime] = None) -> str:
    """Add the scout's channels (suggestions) up to the most the account keeps. What it says."""
    a = acct if hasattr(acct, "slug") else _account(cfg, acct)
    want = suggestions(a)
    if not want:
        return f"{a.name}: the scout lists no YouTube channel that is not on the list already"
    now = now or cfg.now()
    by = _clean(by, 60) or "factory.py"
    limit = max_channels(a)
    added: List[str] = []
    with store.edit(folder(a) / "channels.json", dict) as d:
        rows = [r for r in (d.get("channels") or []) if isinstance(r, dict) and r.get("url")]
        for s in want:
            if len(rows) >= limit:
                break
            if any(_same(r["url"], s["url"]) for r in rows):
                continue
            rows.append({"url": s["url"], "name": s["name"], "why": s["why"], "added_by": by, "added_at": _iso(now)})
            added.append(s["name"])
        d["channels"] = rows
    left = len(want) - len(added)
    said = f"{a.name}: {len(added)} of the scout's channels added ({len(rows)} of {limit})"
    if left:
        said += f"; {left} left out ({limit} is the most)"
    return said


# ------------------------------------------------------------------------------------------------------ holds
def _machine_hold_path() -> Path:
    return store.path("social", "ideas-hold.json")


def _running(h, now: datetime) -> Optional[dict]:
    if not isinstance(h, dict):
        return None
    until = _when(h.get("until"))
    return h if until is not None and until > now else None


def hold(acct, now: Optional[datetime] = None) -> Optional[dict]:
    """The hold still running for the account ({until, why, at, kind}), or None: its own, else the Mac's (a bot
    check is for the whole Mac, so it covers an account switched on after it too) unless Andrew released this account
    since."""
    now = now or datetime.now(timezone.utc)
    own = store.read(folder(acct) / "hold.json", {})
    h = _running(own, now)
    if h is not None:
        return h
    m = _running(store.read(_machine_hold_path(), {}), now)
    if m is None:
        return None
    rel = _when(((own.get("released") or {}) if isinstance(own, dict) else {}).get("at"))
    mat = _when(m.get("at"))
    if rel is not None and mat is not None and rel >= mat:
        return None
    return m


def _put_hold(acct, now: datetime, kind: str, why: str) -> None:
    store.write(folder(acct) / "hold.json", {"until": _iso(now + timedelta(hours=HOLD_HOURS)), "why": why,
                                             "at": _iso(now), "kind": kind})


def _hold_all(cfg: C.Config, now: datetime, kind: str, why: str) -> None:
    """A bot check or a 429 is about this Mac, not one account: the hold goes on every owned account with Ideas on
    (whichever account the run was for) and on the Mac itself (state/social/ideas-hold.json)."""
    for b in accounts(cfg):
        if is_on(b):
            _put_hold(b, now, kind, why)
    store.write(_machine_hold_path(), {"until": _iso(now + timedelta(hours=HOLD_HOURS)), "why": why,
                                       "at": _iso(now), "kind": kind})


def release(cfg: C.Config, acct, by: str, now: Optional[datetime] = None) -> str:
    """End a hold early (Andrew's call: YouTube's bot check may have passed)."""
    a = acct if hasattr(acct, "slug") else _account(cfg, acct)
    now = now or cfg.now()
    h = hold(a, now)
    if h is None:
        return f"{a.name}: Ideas has no hold"
    store.write(folder(a) / "hold.json", {"until": None, "released": {"by": _clean(by, 60) or "factory.py",
                                                                      "at": _iso(now)}, "was": h})
    return f"{a.name}: the Ideas hold is released; it reads again at the next 14:20"


# ------------------------------------------------------------------------------------------------------ when it may run
def refusal(cfg: C.Config, now: datetime) -> str:
    """'' when yt-dlp may run now, else why not, in plain words."""
    t = _local(cfg, now)
    hm = (t.hour, t.minute)
    if hm >= _hm(NIGHT[0]) or hm < _hm(NIGHT[1]):
        return f"not at night ({NIGHT[0]} to {NIGHT[1]} belongs to the night shift)"
    for lo, hi in BUSY_MINUTES:
        if lo <= t.minute <= hi:
            return f"not at :{lo:02d} to :{hi:02d} (the collect and the scouts read YouTube then)"
    return ""


def scout_bot_check(cfg: C.Config, now: datetime) -> str:
    """Words naming where a scout saw YouTube's bot check today ('' when none): today's scout board's `failed` list,
    or a run from today in a scout's launchd log (state/logs/*scout*.log). Read only."""
    day = _local(cfg, now).date()
    stamp = day.isoformat()
    scouts = set()
    try:
        d = json.loads((C.ROOT / "config" / "scout.json").read_text())
        scouts = {k for k, v in d.items() if isinstance(v, dict)}
    except (OSError, ValueError):
        pass
    scouts |= {a.slug for a in cfg.accounts.values()}
    for slug in sorted(scouts):
        b = store.read(store.path(slug, "scout", f"{stamp}.json"), {})
        failed = b.get("failed") if isinstance(b, dict) else None
        for x in failed or []:
            if BOT.search(str(x)):
                return f"{slug}'s scout board"
    logs = store.path("logs")
    try:
        names = sorted(p for p in logs.iterdir() if p.is_file() and "scout" in p.name and p.name.endswith(".log"))
    except OSError:
        names = []
    for p in names:
        try:
            size = p.stat().st_size
            changed = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
            with p.open("rb") as fh:
                if size > SCOUT_TAIL:
                    fh.seek(size - SCOUT_TAIL)
                text = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        lines = text.splitlines()
        if not any(ln.startswith("== ") for ln in lines):
            # a log with no run stamps: only when it changed today, its last lines
            if _local(cfg, changed).date() == day and any(BOT.search(ln) for ln in lines[-200:]):
                return f"the scout log {p.name}"
            continue
        today = False
        for ln in lines:
            if ln.startswith("== "):
                today = ln[3:13] == stamp
                continue
            if today and BOT.search(ln):
                return f"the scout log {p.name}"
    return ""


def ytdlp() -> Optional[str]:
    """yt-dlp's path (Homebrew's, as the scout finds it), or None when it is not installed."""
    got = shutil.which("yt-dlp")
    if got:
        return got
    for p in ("/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp"):
        if Path(p).exists():
            return p
    return None


# ------------------------------------------------------------------------------------------------------ reading
def _list_shorts(exe: str, url: str) -> Tuple[str, List[dict], str]:
    """One channel's Shorts tab, metadata only: ("ok", entries, "") or ("bot"|"429"|"error", [], why). The caller has
    called net.guard right before."""
    argv = [exe, "--flat-playlist", "--dump-single-json", "--playlist-end", str(PLAYLIST_END),
            url.rstrip("/") + "/shorts"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=YTDLP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "error", [], f"no answer in {YTDLP_TIMEOUT_S}s"
    except OSError as e:
        return "error", [], f"{type(e).__name__}: {e}"[:200]
    err = str(r.stderr or "")
    if r.returncode != 0:
        last = (err.strip().splitlines() or ["yt-dlp failed"])[-1][:200]
        if BOT.search(err):
            return "bot", [], last
        if TOO_MANY.search(err):
            return "429", [], last
        return "error", [], last
    try:
        d = json.loads(r.stdout or "{}")
    except ValueError:
        return "error", [], "yt-dlp's answer was not JSON"
    entries = d.get("entries") if isinstance(d, dict) else None
    return "ok", [e for e in (entries or []) if isinstance(e, dict)], ""


def _short_row(e: dict, ch: dict, at: datetime) -> Optional[dict]:
    """One Short from yt-dlp's flat Shorts listing. `views` is YouTube's rounded label as yt-dlp parses it (see
    label_step); `dur` is None unless yt-dlp gives a length, which its flat Shorts listing does not."""
    vid = str(e.get("id") or "").strip()
    if not vid:
        return None
    try:
        views = int(e.get("view_count")) if e.get("view_count") is not None else None
    except (TypeError, ValueError):
        views = None
    try:
        dur = round(float(e.get("duration")), 1) if e.get("duration") is not None else None
    except (TypeError, ValueError):
        dur = None
    url = str(e.get("url") or "")
    if not url.startswith("https://"):
        url = f"https://www.youtube.com/shorts/{urllib.parse.quote(vid)}"
    return {"id": vid, "channel": str(ch.get("name") or _name_from(ch["url"])), "curl": ch["url"],
            "title": _clean(e.get("title"), 300), "views": views, "dur": dur, "url": url, "at": _iso(at),
            "vph": None, "new": True}


def label_step(v) -> int:
    """How far apart two neighbouring values of YouTube's view label are around `v`: the Shorts tab shows "987 views",
    "1.2K", "12K", "1.2M", "12M", so yt-dlp's count moves in steps of 1, 100, 1000, 100000, 1000000 ... A count that is
    not a multiple of its step was not rounded, so its step is 1."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return 1
    if v < 1000:
        return 1
    step = 1_000_000_000
    for top, s in ((10_000, 100), (1_000_000, 1000), (10_000_000, 100_000), (1_000_000_000, 1_000_000),
                   (10_000_000_000, 100_000_000)):
        if v < top:
            step = s
            break
    return step if v % step == 0 else 1


def snapshots_dir(acct) -> Path:
    return folder(acct) / "snapshots"


def snapshot(acct, day: date) -> dict:
    d = store.read(snapshots_dir(acct) / f"{day.isoformat()}.json", {})
    return d if isinstance(d, dict) else {}


def _snapshot_days(acct) -> List[date]:
    out = []
    try:
        for p in snapshots_dir(acct).iterdir():
            if re.match(r"^\d{4}-\d{2}-\d{2}\.json$", p.name):
                try:
                    out.append(date.fromisoformat(p.name[:10]))
                except ValueError:
                    pass
    except OSError:
        pass
    return sorted(out)


def _before(acct, day: date) -> Dict[str, dict]:
    """Each Short's newest row from the snapshots before `day` (at most VPH_MAX_GAP_DAYS old), each with the time it
    was read: one day a channel did not answer costs it nothing, the day before still counts."""
    out: Dict[str, dict] = {}
    for d in reversed(_snapshot_days(acct)):
        if d >= day:
            continue
        if (day - d).days > VPH_MAX_GAP_DAYS:
            break
        s = snapshot(acct, d)
        ch_at = {str(c.get("url", "")).lower(): c.get("at") for c in s.get("channels") or []
                 if isinstance(c, dict) and c.get("ok")}
        for r in s.get("shorts") or []:
            if not isinstance(r, dict) or not r.get("id") or str(r["id"]) in out:
                continue
            at = r.get("at") or ch_at.get(str(r.get("curl", "")).lower()) or s.get("at")
            if _when(at) is not None:
                out[str(r["id"])] = dict(r, at=at)
    return out


def _with_vph(rows: List[dict], before: Dict[str, dict]) -> List[dict]:
    """Views per hour against the Short's last earlier read (None on a first sighting or day one), and `new` for a
    Short no earlier read had. A rounded count (label_step) gives views per hour only when it moved by VPH_MIN_STEPS
    steps or more, marked approx; less than that is unknown (None, approx), never 0."""
    for r in rows:
        p = before.get(r["id"])
        r["new"] = p is None
        if p is None or r.get("views") is None or p.get("views") is None:
            continue
        t1, t0 = _when(r.get("at")), _when(p.get("at"))
        hours = (t1 - t0).total_seconds() / 3600 if t1 is not None and t0 is not None else 0
        if hours <= 0.5:
            continue
        v, pv = int(r["views"]), int(p["views"])
        step = max(label_step(v), label_step(pv))
        if step == 1:
            r["vph"] = round(max(0.0, (v - pv) / hours), 1)
            continue
        r["approx"] = True
        if v - pv >= VPH_MIN_STEPS * step:
            r["vph"] = round((v - pv) / hours, 1)
    return rows


def _prune(acct, today: date) -> None:
    cut = today - timedelta(days=KEEP_DAYS)
    for d in _snapshot_days(acct):
        if d < cut:
            try:
                (snapshots_dir(acct) / f"{d.isoformat()}.json").unlink()
            except OSError:
                pass


def _write_last(acct, **rec) -> None:
    """A read: last.json is replaced (a stand-down or a skipped day before it is over)."""
    try:
        store.write(folder(acct) / "last.json", rec)
    except (OSError, SystemExit):
        pass


def _note(acct, **rec) -> None:
    """A day that did not read (a stand-down, a refusal, a run that ran out of time before this account): put beside
    the last read in last.json, which is kept, so nothing reads as a read that did not happen."""
    try:
        with store.edit(folder(acct) / "last.json", dict) as d:
            d.update(rec)
    except (OSError, SystemExit, ValueError):
        pass


def last(acct) -> dict:
    d = store.read(folder(acct) / "last.json", {})
    return d if isinstance(d, dict) else {}


def _busy_end(cfg: C.Config, t: datetime) -> Optional[datetime]:
    """When the scouts' or the collect's minutes that `t` falls in are over (None when it is in none of them)."""
    lt = _local(cfg, t)
    for lo, hi in BUSY_MINUTES:
        if lo <= lt.minute <= hi:
            return lt.replace(minute=hi, second=0, microsecond=0) + timedelta(minutes=1)
    return None


def _clear_to_read(cfg: C.Config, now: datetime, t0: float) -> Tuple[datetime, str]:
    """(the time, '') when the next listing may go now, waiting out the scouts' minutes when the run reached them;
    (the time, why it stops) at night or when the run has read for MAX_RUN_S."""
    for _ in range(8):
        el = max(0.0, _mono() - t0)
        later = now + timedelta(seconds=el)
        hm = f"{_local(cfg, later):%H:%M}"
        if el > MAX_RUN_S:
            return later, f"stopped at {hm}, out of time (a run reads for {MAX_RUN_S // 60} minutes at most)"
        why = refusal(cfg, later)
        if not why:
            return later, ""
        end = _busy_end(cfg, later)
        if end is None:
            return later, f"stopped at {hm}, {why}"
        wait = max(1.0, (end - _local(cfg, later)).total_seconds())
        if el + wait > MAX_RUN_S:
            return later, f"stopped at {hm}, {why}, and the run is out of time"
        _sleep(wait)
    later = now + timedelta(seconds=max(0.0, _mono() - t0))
    return later, f"stopped at {_local(cfg, later):%H:%M}, the clock did not move"


def run(cfg: C.Config, now: Optional[datetime] = None, quiet: bool = True, only: Optional[str] = None) -> List[str]:
    """The 14:20 job: each account with Ideas on lists its benchmark channels' Shorts through yt-dlp (net.guard
    first, a random 5 to 15 seconds between channels, the scouts' minutes waited out), writes the day's snapshot and
    the week's patterns. With every switch off it does nothing at all (no subprocess, no file). What it did, in lines
    (nothing when quiet and there was nothing to read)."""
    now = now or cfg.now()
    accts = [a for a in accounts(cfg) if is_on(a) and (only is None or a.slug == only)]
    if not accts:
        return [] if quiet or only is None else [f"ideas: {only} is off (factory.py social on {only} ideas)"]
    lines: List[str] = []
    day = _local(cfg, now).date()
    why = refusal(cfg, now)
    if why:
        for a in accts:
            _note(a, skipped={"day": day.isoformat(), "at": _iso(now), "why": f"did not read, {why}"})
        return [f"ideas: did not read, {why}"]
    exe = ytdlp()
    if not exe:
        return ["ideas: yt-dlp is not installed (brew install yt-dlp)"]
    seen = scout_bot_check(cfg, now)
    if seen:
        for a in accts:
            _note(a, stood_down="the scouts saw a YouTube bot check", where=seen, down_day=day.isoformat(),
                  down_at=_iso(now))
        return [f"ideas: stood down today (the scouts saw a YouTube bot check: {seen})"]
    k = day.toordinal() % len(accts)                # the accounts take turns going first, day by day
    accts = accts[k:] + accts[:k]
    t0 = _mono()
    first = True
    stop = ""
    for a in accts:
        h = hold(a, now)
        if h is not None:
            until = _when(h.get("until"))
            lines.append(f"ideas: {a.name} waits until {_local(cfg, until):%b %d %H:%M} ({h.get('why') or 'a hold'})")
            continue
        chans = channels(a)[: max_channels(a)]
        if not chans:
            if not quiet:
                lines.append(f"ideas: {a.name} has no benchmark channels yet (Reports, Ideas: Add the scout's "
                             f"channels)")
            continue
        if stop:
            _note(a, skipped={"day": day.isoformat(), "at": _iso(now), "why": stop})
            continue
        read: List[dict] = []
        shorts: List[dict] = []
        for ch in chans:
            if not first:
                _sleep(RNG.uniform(*SLEEP_S))
            first = False
            later, why = _clear_to_read(cfg, now, t0)
            if why:
                stop = why
                break
            try:
                net.guard("ideas: yt-dlp")
            except net.Offline as e:
                stop = str(e)
                break
            kind, entries, err = _list_shorts(exe, ch["url"])
            if kind in ("bot", "429"):
                words = ("YouTube's bot check" if kind == "bot" else "YouTube said too many requests (429)")
                _hold_all(cfg, later, kind, words)
                read.append({"url": ch["url"], "ok": False, "n": 0, "why": err, "at": _iso(later)})
                stop = f"{words}: Ideas holds {HOLD_HOURS} hours on this Mac"
                break
            if kind != "ok":
                read.append({"url": ch["url"], "ok": False, "n": 0, "why": err, "at": _iso(later)})
                continue
            rows = [r for r in (_short_row(e, ch, later) for e in entries[:PLAYLIST_END]) if r]
            read.append({"url": ch["url"], "ok": True, "n": len(rows), "why": "", "at": _iso(later)})
            shorts += rows
        if not read:
            _note(a, skipped={"day": day.isoformat(), "at": _iso(now), "why": stop or "nothing read"})
            continue
        _save_snapshot(cfg, a, day, now, read, shorts)
        try:
            write_patterns(cfg, a, now)
        except (OSError, SystemExit, ValueError) as e:
            lines.append(f"ideas: {a.name} patterns not written ({type(e).__name__}: {e})")
        _prune(a, day)
        answered = sum(1 for r in read if r["ok"])
        error = next((r["why"] for r in read if not r["ok"] and r.get("why")), "")
        _write_last(a, at=_iso(now), day=day.isoformat(), channels=len(chans), read=len(read), answered=answered,
                    shorts=len(shorts), unread=len(chans) - len(read), stopped=stop, error=error)
        lines.append(f"ideas: {a.name}: {answered} of {len(chans)} channels answered, {len(shorts)} Shorts"
                     + (f" ({len(chans) - len(read)} not reached)" if len(read) < len(chans) else ""))
    if stop:
        lines.append(f"ideas: {stop}")
    return lines


def _save_snapshot(cfg: C.Config, acct, day: date, at: datetime, read: List[dict], shorts: List[dict]) -> None:
    """Today's snapshot. A second run the same day replaces only the channels that answered again: a channel that
    did not answer this time keeps its earlier rows (its failure noted beside them), and the channels not read again
    are kept as they were. Each row carries the time it was read."""
    shorts = _with_vph(shorts, _before(acct, day))
    p = snapshots_dir(acct) / f"{day.isoformat()}.json"
    with store.edit(p, dict) as d:
        ok_urls = {r["url"].lower() for r in read if r["ok"]}
        failed = {r["url"].lower(): r for r in read if not r["ok"]}
        kept_ok = set()
        chans = []
        for c in d.get("channels") or []:
            if not isinstance(c, dict):
                continue
            u = str(c.get("url", "")).lower()
            if u in ok_urls:
                continue
            if u in failed:
                if not c.get("ok"):
                    continue                        # an earlier failure: the new one replaces it
                c = dict(c, retry={"at": failed[u].get("at"), "why": failed[u].get("why")})
                kept_ok.add(u)
            chans.append(c)
        chans += [r for r in read if r["ok"] or r["url"].lower() not in kept_ok]
        old_sh = [s for s in d.get("shorts") or [] if isinstance(s, dict)
                  and str(s.get("curl", "")).lower() not in ok_urls]
        ids = {s["id"] for s in shorts}
        d.setdefault("at", _iso(at))
        d["updated"] = _iso(at)
        d["day"] = day.isoformat()
        d["channels"] = chans
        d["shorts"] = [s for s in old_sh if s.get("id") not in ids] + shorts


# ------------------------------------------------------------------------------------------------------ title patterns
def _people(acct) -> List[str]:
    ppl = _scout_block(_slug(acct)).get("people") or {}
    names = list(ppl) if isinstance(ppl, dict) else [str(x) for x in ppl if isinstance(x, str)]
    return [n for n in names if isinstance(n, str) and len(n) >= 3]


def _names_a_person(t: str) -> bool:
    """A capitalised word mid-sentence that is not a common word ("... with Vance"). In a Title Case title every word
    is capitalised, so there it takes two such words in a row, as a first and last name ("Kai Cenat")."""
    toks = re.findall(r"[A-Za-z][A-Za-z'\u2019-]*|[.!?:|]", t)
    words = [x for x in toks if x not in ".!?:|" and len(x) >= 3]
    title_case = bool(words) and sum(1 for w in words if w[:1].isupper()) / len(words) >= 0.6
    start, run_ = True, 0
    for tok in toks:
        if tok in ".!?:|":
            start, run_ = True, 0
            continue
        plain = tok.strip("'\u2019-")
        name_like = (plain[:1].isupper() and not plain.isupper() and len(plain) >= 3
                     and plain.lower().replace("'", "").replace("\u2019", "") not in COMMON)
        if name_like and not start:
            run_ += 1
            if run_ >= (2 if title_case else 1):
                return True
        elif name_like and start:
            run_ = 1
        else:
            run_ = 0
        start = False
    return False


def features_of(title: str, people: Sequence[str] = ()) -> Dict[str, object]:
    """The title's features (FEATURES), each True or False, plus caps_share (0 to 1). A named person is a name from
    the scout's people list, or a capitalised word mid-sentence that is not a common word."""
    t = str(title or "").strip()
    low = t.lower()
    words = WORD.findall(t)
    letters = [w for w in words if sum(ch.isalpha() for ch in w) >= 2]
    shout = [w for w in letters if w.isupper() and w.strip(".'’-") not in ACRONYMS]
    caps_share = round(len(shout) / len(letters), 3) if letters else 0.0
    named = any(re.search(r"\b" + re.escape(p.lower()) + r"\b", low) for p in people)
    if not named:
        named = _names_a_person(t)
    n = len(t)
    return {"question": "?" in t or bool(QUESTION_START.match(t)),
            "number": bool(re.search(r"\d", t)),
            "named_person": named,
            "quote": bool(QUOTE.search(t)),
            "caps": caps_share >= 0.3,
            "reacts": bool(REACTS.search(t)),
            "len_short": n < 40, "len_mid": 40 <= n <= 70, "len_long": n > 70,
            "caps_share": caps_share}


def _latest_rows(acct, since: date) -> List[dict]:
    """Each Short once (its newest snapshot row) from the snapshots on or after `since`."""
    best: Dict[str, dict] = {}
    for d in _snapshot_days(acct):
        if d < since:
            continue
        for r in snapshot(acct, d).get("shorts") or []:
            if isinstance(r, dict) and r.get("id"):
                prev = best.get(r["id"])
                if prev is None or r.get("vph") is not None or prev.get("vph") is None:
                    best[str(r["id"])] = dict(r, day=d.isoformat())
    return list(best.values())


def _our_titles(acct, today: date, days: int = WINDOW_DAYS) -> List[str]:
    """Our own titles from the boards of the last `days` days: every signed clip (approved or scheduled), its YouTube
    title as the review page shows it."""
    from . import copytext as T
    out: List[str] = []
    for i in range(days + 1):
        d = today - timedelta(days=i)
        try:
            bd = B.read(acct, d) or {}
        except (OSError, ValueError):
            continue
        for s in (bd.get("slots") or {}).values():
            if not isinstance(s, dict) or s.get("status") not in B.SIGNED:
                continue
            copy = s.get("copy") or {}
            c = copy.get("youtube_shorts") or next(iter(copy.values()), None) if isinstance(copy, dict) else None
            title = ""
            if isinstance(c, dict) and c.get("path"):
                try:
                    title = T.parse(Path(c["path"]).read_text())["title"].strip()
                except (OSError, KeyError):
                    title = ""
            if not title:
                title = str(s.get("slug") or "").replace("-", " ").strip()
            if title:
                out.append(title)
    return out


def _share(rows: Sequence[dict], key: str) -> Optional[float]:
    if not rows:
        return None
    return round(sum(1 for r in rows if r.get(key)) / len(rows), 3)


def _median(xs: List[float]) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else round((xs[m - 1] + xs[m]) / 2, 1)


WINNER_KEYS = ("id", "channel", "title", "views", "dur", "url", "vph", "approx", "new", "day")


def compute_patterns(cfg: C.Config, acct, now: Optional[datetime] = None) -> dict:
    """The week's patterns: the winners (top quarter by views per hour) against the bottom half and against our own
    titles, feature by feature, and the 20 best Shorts by views per hour."""
    now = now or cfg.now()
    today = _local(cfg, now).date()
    rows = _latest_rows(acct, today - timedelta(days=WINDOW_DAYS - 1))
    ppl = _people(acct)
    scored = sorted((r for r in rows if r.get("vph") is not None), key=lambda r: (-float(r["vph"]), str(r["id"])))
    for r in rows:
        r["f"] = features_of(r.get("title") or "", ppl)
    n = len(scored)
    top = scored[: max(1, math.ceil(n / 4))] if n >= 4 else []
    bottom = scored[n - n // 2:] if n >= 4 else []
    ours = [features_of(t, ppl) for t in _our_titles(acct, today)]
    feats = []
    for key, words in FEATURES:
        feats.append({"feature": key, "words": words,
                      "share_top": _share([r["f"] for r in top], key),
                      "share_bottom": _share([r["f"] for r in bottom], key),
                      "share_ours": _share(ours, key), "n": n})

    def slim(r: dict) -> dict:
        return {k: r.get(k) for k in WINNER_KEYS}
    return {"window_days": WINDOW_DAYS, "at": _iso(now), "account": _slug(acct), "n": n, "n_top": len(top),
            "n_bottom": len(bottom), "n_ours": len(ours), "features": feats,
            "length": {"top_median_s": _median([r.get("dur") for r in top]),
                       "bottom_median_s": _median([r.get("dur") for r in bottom])},
            "winners": [slim(r) for r in scored[:20]]}


def write_patterns(cfg: C.Config, acct, now: Optional[datetime] = None) -> dict:
    pat = compute_patterns(cfg, acct, now)
    store.write(folder(acct) / "patterns.json", pat)
    return pat


def patterns(acct) -> dict:
    """The account's last patterns (patterns.json): {} before the first run. For the coach, the learner and the
    weekly report."""
    d = store.read(folder(acct) / "patterns.json", {})
    return d if isinstance(d, dict) else {}


def winners(acct, days: int = WINDOW_DAYS, n: int = 10) -> List[dict]:
    """The best Shorts of the last `days` days by views per hour (newest numbers of each). On day one, before any
    views per hour, by views instead (their vph is None)."""
    if not _snapshot_days(acct):
        return []
    today = _snapshot_days(acct)[-1]
    rows = _latest_rows(acct, today - timedelta(days=max(1, int(days)) - 1))
    with_vph = [r for r in rows if r.get("vph") is not None]
    if with_vph:
        rows = sorted(with_vph, key=lambda r: (-float(r["vph"]), str(r["id"])))
    else:
        rows = sorted(rows, key=lambda r: (-(r.get("views") or 0), str(r["id"])))
    return [{k: r.get(k) for k in WINNER_KEYS} for r in rows[: max(0, int(n))]]


# ------------------------------------------------------------------------------------------------------ the page's part
def section(cfg: C.Config, acct) -> str:
    """The account's Ideas, as HTML (winners, patterns, channels): the Ideas page draws it, and the Reports page may
    show it too. Files only."""
    from . import ideaspage
    a = acct if hasattr(acct, "slug") else _account(cfg, acct)
    return ideaspage.section_html(cfg, a)


# ------------------------------------------------------------------------------------------------------ doctor
def _today_note(lr: dict, today: str) -> str:
    """Why today did not read, from last.json ('' when it did, or nothing says)."""
    if lr.get("day") == today:
        return ""
    if lr.get("stood_down") and lr.get("down_day") == today:
        return "stood down today (the scouts saw a YouTube bot check)"
    sk = lr.get("skipped")
    if isinstance(sk, dict) and sk.get("day") == today:
        return f"not read today ({sk.get('why') or 'skipped'})"
    return ""


def doctor(cfg: C.Config, now: Optional[datetime] = None) -> List[Tuple[Optional[bool], str, str]]:
    """One line per account: off (what it waits for), how the last read went, a hold, a day the scouts' bot check
    stood it down, a day it did not read and why. A NO only for a part that is on and cannot work: no read for two
    days that nothing explains, or a read where no channel answered (yt-dlp broken). Read only: no network, no folder
    made."""
    now = now or cfg.now()
    out: List[Tuple[Optional[bool], str, str]] = []
    today = _local(cfg, now).date().isoformat()
    stood = False
    any_on = False
    off: List[str] = []
    for a in accounts(cfg):
        try:
            chans = channels(a)
            n = len(chans)
            if not is_on(a):
                off.append(f"{a.name} ({n} channel{'' if n == 1 else 's'})")
                continue
            any_on = True
            h = hold(a, now)
            lr = last(a)
            if h is not None:
                until = _when(h.get("until"))
                out.append((None, f"{a.name} ideas: paused until {_local(cfg, until):%b %d %H:%M} "
                                  f"({h.get('why') or 'a hold'})",
                            f"factory.py ideas release {a.slug} --by NAME"))
                continue
            if lr.get("stood_down") and lr.get("down_day") == today and lr.get("day") != today:
                stood = True
                continue
            if not n:
                out.append((None, f"{a.name} ideas: on, no benchmark channels yet (Reports, Ideas: Add the scout's "
                                  f"channels)", ""))
                continue
            at = _when(lr.get("at"))
            note = _today_note(lr, today)
            if at is None:
                out.append((None, f"{a.name} ideas: {n} channels, "
                                  + (note if note else "not read yet (the first read is at 14:20)"), ""))
                continue
            when = (f"{_local(cfg, at):%H:%M}" if _local(cfg, at).date().isoformat() == today
                    else f"{_local(cfg, at):%b %d %H:%M}")
            down = _when(lr.get("down_at"))
            if now - at > timedelta(hours=50):
                if down is not None and now - down <= timedelta(hours=50):
                    out.append((None, f"{a.name} ideas: {n} channels, last read {when}; stood down "
                                      f"{_local(cfg, down):%b %d} (the scouts saw a YouTube bot check)", ""))
                else:
                    out.append((False, f"{a.name} ideas: {n} channels, last read {when}"
                                       + (f"; {note}" if note else ""),
                                "factory.py timer status (the ideas timer), then factory.py ideas run " + a.slug))
                continue
            if int(lr.get("read") or 0) > 0 and not int(lr.get("answered") or 0):
                err = str(lr.get("error") or "")
                out.append((False, f"{a.name} ideas: none of the {int(lr.get('read') or 0)} channels read {when} "
                                   f"answered" + (f" ({err[:120]})" if err else ""),
                            f"brew upgrade yt-dlp, then factory.py ideas run {a.slug}"))
                continue
            line = f"{a.name} ideas: {n} channels, read {when}, {int(lr.get('answered') or 0)} answered"
            if lr.get("stopped"):
                line += f" ({lr['stopped']})"
            if note:
                line += f"; {note}"
            out.append((None, line, ""))
        except Exception as e:  # noqa: BLE001  (the doctor never stops over one account)
            out.append((False, f"{getattr(a, 'name', a)} ideas: could not read ({type(e).__name__}: {e})",
                        f"look at state/{_slug(a)}/ideas/"))
    if off:
        out.insert(0, (None, "ideas: off for " + ", ".join(off) + "; it waits for its switch (factory.py social on "
                             "<account> ideas), after Reports, Ideas: Add the scout's channels", ""))
    if stood:
        out.append((None, "ideas: stood down today (the scouts saw a YouTube bot check)", ""))
    if any_on and not ytdlp():
        out.append((False, "ideas: yt-dlp is not installed", "brew install yt-dlp"))
    return out


# ------------------------------------------------------------------------------------------------------ the command
def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.py ideas", description="what wins in each niche (clipfactory/ideas.py)")
    sub = ap.add_subparsers(dest="action", required=True)
    q = sub.add_parser("run", help="read the benchmark channels now (daytime only; the 14:20 timer does it)")
    q.add_argument("account", nargs="?")
    q.add_argument("--quiet", action="store_true")
    q = sub.add_parser("status", help="each account's channels, last read, hold and best Shorts")
    q.add_argument("account", nargs="?")
    q = sub.add_parser("channels", help="the benchmark channels")
    q.add_argument("account")
    q = sub.add_parser("suggest", help="the scout's YouTube channels not on the list yet")
    q.add_argument("account")
    q = sub.add_parser("add", help="add a benchmark channel by its YouTube link")
    q.add_argument("account")
    q.add_argument("url")
    q.add_argument("--name", default="")
    q.add_argument("--why", default="")
    q.add_argument("--by", required=True)
    q = sub.add_parser("remove", help="take a channel off the list")
    q.add_argument("account")
    q.add_argument("url")
    q.add_argument("--by", required=True)
    q = sub.add_parser("seed", help="add the scout's YouTube channels (up to the most kept)")
    q.add_argument("account")
    q.add_argument("--by", required=True)
    q = sub.add_parser("winners", help="the best Shorts by views per hour")
    q.add_argument("account")
    q.add_argument("--days", type=int, default=WINDOW_DAYS)
    q.add_argument("-n", type=int, default=10)
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("patterns", help="title patterns: winners against the bottom half and against ours")
    q.add_argument("account", nargs="?")
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("release", help="end a hold early")
    q.add_argument("account")
    q.add_argument("--by", required=True)
    sub.add_parser("doctor", help="the doctor's Ideas lines alone")
    return ap


def _pct(v) -> str:
    return "  -" if v is None else f"{round(float(v) * 100):3d}%"


def _num(v) -> str:
    if v is None:
        return "-"
    v = float(v)
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.1f}K"
    return f"{v:.0f}"


def _pattern_lines(a, pat: dict) -> List[str]:
    if not pat:
        return [f"{a.name}: no patterns yet (they come after two days of reads)"]
    out = [f"{a.name}: {pat.get('n', 0)} Shorts with views per hour over {pat.get('window_days', WINDOW_DAYS)} days "
           f"(top {pat.get('n_top', 0)}, bottom {pat.get('n_bottom', 0)}, ours {pat.get('n_ours', 0)})",
           f"  {'feature':34} {'top':>5} {'bottom':>7} {'ours':>5}"]
    for f in pat.get("features") or []:
        out.append(f"  {FEATURE_WORDS.get(f.get('feature'), f.get('feature')):34} {_pct(f.get('share_top')):>5} "
                   f"{_pct(f.get('share_bottom')):>7} {_pct(f.get('share_ours')):>5}")
    return out


def cli(cfg: C.Config, argv: List[str]) -> None:
    """factory.py ideas <action> ... (the timer runs `factory.py social ideas --quiet`, the same run)."""
    a = _parser().parse_args(list(argv))
    act = a.action
    if act == "run":
        if a.account:
            _account(cfg, a.account)
        if not any(is_on(x) for x in accounts(cfg) if a.account is None or x.slug == a.account):
            said = run(cfg, quiet=a.quiet, only=a.account)          # off: it says so, and takes no lock
        else:
            # the same lock the timer's run takes (social.run_job: state/social/locks/ideas-run.lock), so a run by
            # hand never lists YouTube beside the 14:20 one
            with store.try_lock("ideas-run") as got:
                said = (run(cfg, quiet=a.quiet, only=a.account) if got else
                        ["ideas: already reading (the 14:20 run or another by hand); nothing more was started"])
        for ln in said:
            print(ln)
    elif act == "status":
        now = cfg.now()
        for acct in ([_account(cfg, a.account)] if a.account else accounts(cfg)):
            lr = last(acct)
            h = hold(acct, now)
            print(f"{acct.name}: ideas {SW.mode(acct, KEY)}, {len(channels(acct))} channels"
                  + (f", last read {lr.get('at')} ({lr.get('answered', 0)} answered)" if lr.get("at") else "")
                  + (f", paused until {h.get('until')}" if h else ""))
            for w in winners(acct, n=5):
                print(f"  {_num(w.get('vph')):>6}/h  {w.get('channel')}: {w.get('title')}")
    elif act == "channels":
        acct = _account(cfg, a.account)
        rows = channels(acct)
        print(f"{acct.name}: {len(rows)} of {max_channels(acct)} benchmark channels")
        for r in rows:
            print(f"  {r.get('name')}: {r.get('url')}" + (f" ({r['why']})" if r.get("why") else ""))
    elif act == "suggest":
        acct = _account(cfg, a.account)
        rows = suggestions(acct)
        print(f"{acct.name}: {len(rows)} of the scout's channels not on the list")
        for r in rows:
            print(f"  {r['name']}: {r['url']} ({r['why']})")
    elif act == "add":
        print(add(cfg, _account(cfg, a.account), a.url, a.name, a.why, a.by))
    elif act == "remove":
        print(remove(cfg, _account(cfg, a.account), a.url, a.by))
    elif act == "seed":
        print(seed(cfg, _account(cfg, a.account), a.by))
    elif act == "winners":
        acct = _account(cfg, a.account)
        rows = winners(acct, days=a.days, n=a.n)
        if a.json:
            print(json.dumps(rows, indent=1))
            return
        for w in rows:
            ln = f"  {_num(w.get('vph')):>6}/h  {_num(w.get('views')):>6} views  {w.get('channel')}: {w.get('title')}"
            print(ln)
        if not rows:
            print(f"{acct.name}: nothing read yet")
    elif act == "patterns":
        accts = [_account(cfg, a.account)] if a.account else [x for x in accounts(cfg) if is_on(x) or patterns(x)]
        if a.json:
            print(json.dumps({x.slug: patterns(x) for x in accts}, indent=1))
            return
        for x in accts:
            for ln in _pattern_lines(x, patterns(x)):
                print(ln)
    elif act == "release":
        print(release(cfg, _account(cfg, a.account), a.by))
    elif act == "doctor":
        for good, text, fix in doctor(cfg):
            print(f"  {'-- ' if good is None else 'ok ' if good else 'NO '} {text}"
                  + (f"\n        fix: {fix}" if fix and good is False else ""))
