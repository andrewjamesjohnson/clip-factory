"""The news scouts: one per account, finding today's most clippable moments while they are still news.

Repurposed from the desk's Scout (Pheme, Mac Studio Prepared/agents/scout.md, read and left untouched): code counts and
the bot judges. Code lists every source in config/scout.json (YouTube channels and searches by metadata only, the
publishers' own RSS feeds), keeps what is on topic, scores each find, folds the same story told by several outlets into
one, and writes the board. The bot (its `<account>-scout` skill) reads the board, kills what is not a clip and picks
what is; a person can do the same with `--pick` / `--kill`.

    score = (0.3 + 1.2 heat) x log10(1 + views an hour) x recency x (1 + name) x (1 + more outlets) x outlier
            x weight x shape

heat    the title's energy words (config `heat_words`): yelling, clash, trailer, meltdown... capped at 1
views   views an hour since upload; between two runs, the real rate (the live climb)
recency halves every `half_life_h` hours
name    the biggest name in the title (config `people`; GTA also reads the channel name, `people_in_channel`)
outlets distinct outlets telling the same story, +25% each, capped at double: many voices, not one voice ten times
outlier an upload doing several times its own channel's usual views an hour (it went viral): x1.4 at 2x, up to x3
        (config `outlier_cap`); GTA compares views at the same age instead (`outlier_by_views`)
weight  the source's own weight in config
shape   is this the clip itself? (2026-10-17 scout intelligence run, measured on the account's own posts)
        GTA `damp`: GTA 5 / hashtag spam / non-Latin / other people's compilations / a short non-reaction, turned down;
        a title about leaks x0.2 since 2026-10-19 (Andrew: leak counts against a GTA video), so it is never fed.
        GTA `reactions`: a named face reacting to a Rockstar drop keeps 72 h, ranks on its whole reach, and takes the
        full outlets bonus when 5+ channels react to the same drop (the REACTIONS duo, the account's best post).
        Politics `rank`: the raw moment up (C-SPAN and official feeds x1.4, an exchange x1.2, a quote after a colon
        x1.2), commentary down (recap, reacts, compilation, analyst x0.35, and no outlier lift), reuploads and
        "X vs Y:" farm titles x0.4, length fitted to a 65-90 s cut. `block_channels` never reach the board.
`phases` switch dated settings on and off (GTA launch week); a search with `from` / `until` runs only in its dates.

The new looks (the Politics, since 2026-10-21): config `looks` says which words make a find fit THE OUTBURST, THE
FACE-OFF, THE EXHIBIT or THE FLASHBACK, read from its title and description (a search's snippet, a feed's or a
headline's summary, kept on the find as `desc`). The find carries `looks` and `look_words`, every line of the board
says its look, and a New looks block lists the best per look from today's news and from THE VAULT (vault.md). A look
is a label for the night: it never moves a score.

Rights travel with every find. Since 2026-10-16 (the owner's call) the Politics may cut network and C-SPAN footage too:
member / committee / network / cspan  cut (a network or C-SPAN job runs with analyze.py --rights rebroadcast)
reupload  an unknown channel's copy (often a re-cut, sometimes fake): find the original first
For GTA, a roster creator's upload can go straight to the inbox (`auto_feed`); anyone else is a suggestion.

A YouTube channel feed that does not answer (404/500 on 11, then all 12, of the Politics's from 10-17 18:40) is
read through yt-dlp instead: the channel's uploads list, the same list the feed carries (_feed_by_ytdlp). The run's
first line says "(N rss down, read via yt-dlp)", the board keeps them under `fallback`, and doctor and Clip
Review say how many sources answered on the last run (clipfactory/scouthealth.py).

Boards: state/<account>/scout/<date>.json, readable at ~/Desktop/Clip Drafts/Scout/<Account> scout MM.DD.YYYY.md.
Picks and kills persist across days in state/<account>/scout/decisions.json.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config as C

SCOUT_CONFIG = C.ROOT / "config" / "scout.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) clip-factory-scout/1.0"
SEARCH_SP = {"today": "CAMSBAgCEAE%253D", "week": "CAMSBAgDEAE%253D"}  # sort by views, video only, uploaded today / this week
# 2026-10-17 (the owner: the Politics "able to clip anything"): the White House and
# federal agencies are public-domain publishers like a committee; a podcast's or an
# independent creator's own upload is cut like a network copy (credited,
# --rights rebroadcast).
CLEARED = ("member", "committee", "network", "cspan", "white-house", "federal",
           "podcast", "independent")
OFFICIAL_FEEDS = ("member", "committee", "white-house", "federal")
PRIMARY_FEEDS = OFFICIAL_FEEDS + ("cspan",)  # the raw feed itself: an official channel or C-SPAN's own
# Speaker: "the line" (a quote after a colon, 12+ characters), the shape of C-SPAN's best Shorts. A package's scare
# quotes (ABC's 'Katie Zacharia "transitioning out"') and MS NOW's leading-quote guest titles do not match.
_QUOTE = re.compile(r"""(?<=\S):\s*["“'‘](?=\S)(.{12,}?)(?<=\S)["”'’](?=$|[\s.,!?:;)|\]—-])""")
# A channel feed cannot say whether a video is still streaming (a yt-dlp scan can): a "WATCH LIVE" title under six
# hours old is taken as still live, no VOD to cut yet (a Kentucky rally ranked #1 while it aired, 10-17).
LIVE_TITLE = re.compile(r"^\W*(watch\s+)?live\b|\blive\s*[:|]|\blive ?stream\b", re.I)
# A YouTube channel feed's URL. When the feed does not answer, the channel is read through yt-dlp (gather).
YT_FEED = re.compile(r"^https?://(?:www\.)?youtube\.com/feeds/videos\.xml\?(?:[^#]*&)?channel_id=(UC[\w-]{22})"
                     r"(?=$|[&#])")
FEED_N = 15   # a channel feed lists the channel's 15 newest uploads
SHOUTY = {"BREAKING", "NEWS", "LIVE", "WATCH", "JUST", "FULL", "VIDEO", "UPDATE", "REPORT", "SPECIAL", "ALERT", "NEW"}
FIGHT = 0.45   # heat at or over this is a fight: the Politics's beat
DESC_MAX = 240  # a description is kept to this many characters: enough for the words that say what happened
COMMON_CAP = 30  # a title word in more than this many finds proves nothing about a story (see cluster)
STORY_MATCH = 0.17  # two titles match when the words they share carry this much of their joint weight (see cluster)
STORY_FIT = 0.2     # and two stories join only when their word profiles are this alike (cosine)
BREAKING_H = 6.0
# a raw rate this high is breaking on its own, by the account's maker (scout.json "breaking_vph" sets its own); keyed by
# the slugs gaming and politics until 2026-10-19, the same two numbers
BREAKING_VPH = {"gta": 50000.0, "politics": 100000.0}
STOP = set("""the a an and or but of to in on at for with from by as is are was were be been it its this that these those
his her their our your my we you they he she them him not no yes new video full live watch says said after over into
about just more most what when where who why how than then out off up down all any can will would could should has have
had do does did get gets got vs amid during today week news update latest clip clips moment moments shorts""".split())


# --------------------------------------------------------------------------------------------------------- inputs
def load(slug: str, today: Optional[str] = None) -> dict:
    data = json.loads(SCOUT_CONFIG.read_text())
    if slug not in data:
        raise SystemExit(f"no scout for {slug!r} in {SCOUT_CONFIG}")
    return phased(data[slug], today or datetime.now().date().isoformat())


def _active(s: dict, today: str) -> bool:
    """A dated entry (`from` / `until`, local ISO dates, both ends included) is on today."""
    return not ((s.get("from") and today < s["from"]) or (s.get("until") and today > s["until"]))


def phased(scfg: dict, today: str) -> dict:
    """Config `phases` switch dated settings on and off by the calendar (GTA launch week 11-18 to 11-28): `set`
    replaces keys, `add` extends a list or a table."""
    out = dict(scfg)
    for ph in scfg.get("phases", []):
        if not _active(ph, today):
            continue
        out.update(ph.get("set", {}))
        for k, v in ph.get("add", {}).items():
            out[k] = list(out.get(k, [])) + list(v) if isinstance(v, list) else dict(out.get(k, {}), **v)
    return out


def _ytdlp() -> str:
    """Hermes cron runs with a bare PATH: find Homebrew's yt-dlp (the one `brew upgrade` keeps current) by hand."""
    import shutil
    return shutil.which("yt-dlp") or next((p for p in ("/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp")
                                            if Path(p).exists()), "yt-dlp")


def _yt(url: str, n: int, timeout: int = 90) -> List[dict]:
    """A channel tab or a search page, metadata only, with yt-dlp's approximate upload times."""
    r = subprocess.run([_ytdlp(), "--flat-playlist", "-j", "--playlist-end", str(n), "--no-warnings",
                        "--extractor-args", "youtubetab:approximate_date", url],
                       capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    rows = []
    for ln in r.stdout.splitlines():
        try:
            rows.append(json.loads(ln))
        except ValueError:
            pass
    if not rows and r.returncode != 0:
        raise RuntimeError((r.stderr.strip().splitlines() or ["yt-dlp failed"])[-1][:200])
    return rows


def search_url(q: str, when: str) -> str:
    return f"https://www.youtube.com/results?search_query={urllib.request.quote(q)}&sp={SEARCH_SP.get(when, SEARCH_SP['today'])}"


def _rss(url: str, timeout: int = 20) -> List[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        root = ET.fromstring(resp.read())
    out = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        get = {c.tag.split("}")[-1]: c for c in it}
        title = (get["title"].text or "").strip() if "title" in get else ""
        link = ""
        if "link" in get:
            link = (get["link"].text or get["link"].get("href") or "").strip()
        when = None
        for k in ("pubDate", "published", "updated", "date"):
            if k in get and get[k].text:
                when = _parse_when(get[k].text.strip())
                if when:
                    break
        # A YouTube channel feed (feeds/videos.xml) is a list of videos, Shorts included, with their view counts:
        # read as videos, they rank and can be named the video to cut, for no yt-dlp call (2026-10-17).
        vid = (get["videoId"].text or "").strip() if "videoId" in get else ""
        views, author, desc = None, "", ""
        for k in ("description", "summary", "content"):   # a headline's summary (RSS, Atom)
            if k in get and (get[k].text or "").strip():
                desc = get[k].text
                break
        for el in it.iter():
            t = el.tag.split("}")[-1]
            if t == "statistics" and (el.get("views") or "").isdigit():
                views = int(el.get("views"))
            elif t == "author":
                author = next(((c.text or "").strip() for c in el if c.tag.split("}")[-1] == "name"), "")
            elif t == "description" and not desc and (el.text or "").strip():   # a feed's media:description
                desc = el.text
        if title and link:
            out.append({"title": _unescape(title), "url": link, "published": when,
                        "video_id": vid or None, "views": views, "author": author, "desc": _snip(desc)})
    return out


def _parse_when(s: str) -> Optional[datetime]:
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _unescape(s: str) -> str:
    import html
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _snip(s) -> str:
    """A description as plain words: tags out (an escaped tag too), whitespace folded, cut at a word by DESC_MAX."""
    import html
    t = re.sub(r"<[^>]+>", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(s or ""))))
    t = " ".join(t.split())
    if len(t) <= DESC_MAX:
        return t
    return t[:DESC_MAX + 1].rsplit(" ", 1)[0][:DESC_MAX].rstrip(" ,;:")


def roster(acct: C.Account) -> List[dict]:
    """GTA: the account's own watchlist (Publish/watchlist.json), YouTube channels only (Twitch is watch.py's)."""
    p = acct.account_dir / "Publish" / "watchlist.json"
    try:
        wl = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    phase = "prelaunch" if datetime.now().date().isoformat() < (wl.get("prelaunch_until") or "2026-11-12") else "launch"
    out = []
    for ch in wl.get("channels", []):
        if ch.get("platform") != "youtube" or ch.get("phase", "both") not in ("both", phase):
            continue
        out.append({"name": ch["name"], "slug": ch["slug"], "url": ch["url"].rstrip("/") + "/videos",
                    "rights": "roster", "weight": float(ch.get("weight", 1.0)), "kind": ch.get("kind"),
                    "min_minutes": ch.get("min_minutes", 5)})
    return out


# ------------------------------------------------------------------------------------------------------- scoring
def _words_hit(title: str, table: Dict[str, float]) -> List[Tuple[str, float]]:
    t = " " + re.sub(r"[^a-z0-9'&.\- ]+", " ", title.lower()) + " "
    return [(w, v) for w, v in table.items() if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", t)]


def heat(title: str, table: Dict[str, float], rank: Optional[dict] = None) -> Tuple[float, List[str]]:
    rk = rank or {}  # the Politics's `rank` turns capitals down: ALL-CAPS is Fox's house style, not energy
    hits = _words_hit(title, table)
    raw = sum(v for _, v in hits)
    caps = len([w for w in re.findall(r"\b[A-Z]{4,}\b", title) if w not in SHOUTY])
    raw += float(rk.get("caps_heat", 0.15)) * min(caps, 3) + (float(rk.get("bang_heat", 0.15)) if "!" in title else 0.0)
    return round(min(1.0, 1 - math.exp(-raw)), 3), [w for w, _ in hits]


def _who(f: dict, scfg: dict) -> str:
    """Where a name is looked for: the title, and for GTA the channel too (penguinz0's titles rarely say Moist)."""
    return f["title"] + (" " + (f.get("channel") or "") if scfg.get("people_in_channel") else "")


def name_weight(f: dict, scfg: dict) -> float:
    return max([w for _, w in _words_hit(_who(f, scfg), scfg.get("people", {}))] or [0.0])


def is_reaction(f: dict, scfg: dict) -> bool:
    """GTA: a reaction to a Rockstar drop ("Kai Cenat Reacts To GTA 6 - Official Launch Trailer")."""
    rx = scfg.get("reactions") or {}
    return bool(rx) and bool(re.search(rx["match"], f["title"], re.I)) and bool(re.search(rx["event"], f["title"], re.I))


def named_reaction(f: dict, scfg: dict) -> bool:
    return is_reaction(f, scfg) and name_weight(f, scfg) > 0


def is_off_topic(title: str, off: Optional[dict]) -> bool:
    """A `damp.off_topic` rule ({match, unless}) turns this title down: it says `match` and not `unless`. The scout's
    damp and, for an account with gta_maker skip_off_topic, the night (makers.gta_sources) read the same rule for
    the same date, so one dated phase in config/scout.json switches both (Stunts, 2026-11-19)."""
    off = off or {}
    return bool(off.get("match")) and bool(re.search(off["match"], title, re.I)) \
        and not (off.get("unless") and re.search(off["unless"], title, re.I))


def off_topic_rule(slug: str, today: str) -> Optional[dict]:
    """The account's `damp.off_topic` on `today` (local ISO date), its dated phases applied: {} when it has none,
    None when config/scout.json has no readable block for it."""
    try:
        return dict((load(slug, today).get("damp") or {}).get("off_topic") or {})
    except (SystemExit, OSError, ValueError):
        return None


def damp(f: dict, scfg: dict) -> float:
    """GTA's `damp`: what is on topic by its words but is not Gaming's clip. GTA 5 / Online / RP (unless the
    title names GTA 6), hashtag spam, non-Latin titles, other people's compilations, a short that is not a reaction,
    and named reupload channels. 1.0 without the block (the Politics)."""
    d = scfg.get("damp") or {}
    if not d:
        return 1.0
    t, k = f["title"], 1.0
    off = d.get("off_topic") or {}
    if off and f.get("kind") != "clip" and is_off_topic(t, off):   # a stream clip: GTA 5 streamer moments are wanted (10-29)
        k *= float(off.get("factor", 1.0))
    spam = d.get("hashtag_spam") or {}
    if spam and t.count("#") >= int(spam.get("min", 2)):
        k *= float(spam.get("factor", 1.0))
    letters = [c for c in t if c.isalpha()]
    if d.get("non_latin") and letters and sum(c.isascii() for c in letters) < 0.7 * len(letters):
        k *= float(d["non_latin"])
    for pat, x in (d.get("titles") or {}).items():
        if re.search(pat, t, re.I):
            k *= float(x)
    dur = f.get("duration")
    if d.get("short_s") and f["kind"] == "video" and (f.get("short") or (dur and dur <= float(d["short_s"]))) \
            and not is_reaction(f, scfg):
        k *= float(d.get("short_factor", 1.0))
    k *= float((d.get("channels") or {}).get((f.get("channel") or "").strip().lower(), 1.0))
    return round(k, 3)


def _derivative(f: dict, scfg: dict) -> bool:
    """The Politics's commentary: a recap, a reaction, a compilation, an analyst or a panel talking about the moment."""
    pat = (scfg.get("rank") or {}).get("derivative")
    return bool(pat) and f.get("rights") not in PRIMARY_FEEDS and bool(re.search(pat, f["title"], re.I))


def _duration_fit(f: dict, rk: dict) -> float:
    """How well the file's length serves a 65-90 s cut: a Short is a lead, 2-20 minutes is the raw exchange."""
    bands = rk.get("duration_fit")
    if not bands or f["kind"] != "video":
        return 1.0
    d = f.get("duration")
    if not d:
        return float(bands[0][1]) if f.get("short") else 1.0
    for limit, x in bands:
        if d < limit:
            return float(x)
    raw = f.get("rights") in PRIMARY_FEEDS or f.get("was_live")
    return float(rk.get("duration_long_raw" if raw else "duration_long", 1.0))


def _shape(f: dict, scfg: dict, vph: float, deriv: bool) -> float:
    """Is this upload the clip itself? GTA's `damp` and the Politics's `rank`; 1.0 for an account with neither."""
    x = damp(f, scfg)
    rk = scfg.get("rank") or {}
    if not rk:
        return x
    x *= _duration_fit(f, rk)
    if deriv:
        x *= float(rk.get("derivative_x", 1.0))
    r = f.get("rights")
    if r == "reupload":
        x *= float(rk.get("reupload_x", 1.0) if vph < float(rk.get("reupload_viral_vph", 1000))
                   else rk.get("reupload_viral_x", 1.0))
        if rk.get("farm_title") and re.search(rk["farm_title"], f["title"]):
            x *= float(rk.get("farm_x", 1.0))
    elif not deriv:
        if r in PRIMARY_FEEDS:
            x *= float(rk.get("primary_official_x", 1.0))
        elif (rk.get("primary") and re.search(rk["primary"], f["title"], re.I)) \
                or _words_hit(f["title"], scfg.get("format_words", {})):
            x *= float(rk.get("primary_x", 1.0))
        if _QUOTE.search(f["title"]):
            x *= float(rk.get("quote_x", 1.0))
    return x


def rights(title: str, channel: str, given: Optional[str], scfg: dict) -> str:
    t, c = title.lower(), (channel or "").lower()
    if any(k in t for k in scfg.get("network_in_title", [])):
        return "network"  # a network segment re-posted on an allowed channel is still a network segment
    if given:
        return given
    table = scfg.get("rights_by_channel") or {}
    for kind in ("cspan", "network", "white-house", "federal", "committee", "member",
                 "podcast", "independent"):
        if any(k in c for k in table.get(kind, [])):
            return kind
    return "reupload" if table else "other"


def is_gta(who) -> bool:
    """A GTA account's scout: roster and creator lanes, the on-topic rule alone, GTA's breaking rate and words. Decided
    by the account's maker (since 2026-10-19: three GTA accounts), never by its slug. `who`: the account, or True or
    False; anything else (a bare slug) is not GTA."""
    return who if isinstance(who, bool) else getattr(who, "maker", None) == "gta"


def lane(f: dict, who) -> str:
    if f["kind"] == "article":
        return "story"
    if is_gta(who):
        return "roster" if f["rights"] == "roster" else "creator"
    return "cut" if f["rights"] in CLEARED else "find original"


def tokens(title: str) -> set:
    return {w for w in re.findall(r"[a-z][a-z0-9'\-]{3,}", title.lower()) if w not in STOP}


def score(f: dict, scfg: dict) -> float:
    rx = scfg.get("reactions") or {}
    react = named_reaction(f, scfg)
    if react:  # a named reaction ranks on its whole reach: the duo is cut days after the drop (31.5k was cut 2 years on)
        vph = max(f.get("vph") or 0.0, f.get("vph_live") or 0.0)
    else:
        vph = f.get("vph_live") or f.get("vph") or 0.0
    v = math.log10(1.0 + vph) if f["kind"] in ("video", "clip") else 1.0  # an article has no views: a flat 1, its outlets do the work
    age = f.get("age_h")
    half = float(rx.get("half_life_h", 72)) if react else float(scfg.get("half_life_h", 12))
    rec = 0.5 ** ((age if age is not None else 24.0) / half)
    name = name_weight(f, scfg)
    if react and (f.get("wave") or 0) >= int(rx.get("wave_channels", 5)):
        outlets = 2.0  # many faces reacting to one drop is the REACTIONS duo
    else:
        outlets = min(2.0, 1.0 + 0.25 * max(0, f.get("story_outlets", 1) - 1))
    deriv = _derivative(f, scfg)
    o = f.get("outlier") or 0.0
    # a panel going viral says nothing about the moment: commentary gets no outlier lift
    outlier = min(float(scfg.get("outlier_cap", 3.0)), 1.0 + min(o, 10.0) / 5.0) if (o >= 2 and not deriv) else 1.0
    weight = f.get("weight", 1.0)
    val = ((0.3 + 1.2 * f["heat"]) * v * rec * (1.0 + name) * outlets * outlier * weight
           * _shape(f, scfg, vph, deriv))
    if scfg.get("learned"):        # the playbook's approved factors (playbook.py): 1.0 when absent or on any error
        try:
            from . import playbook as PB
            lf = float(PB.learned_factor(f, scfg))
        except Exception:  # noqa: BLE001
            lf = 1.0
        if lf != 1.0:
            val *= lf
    return round(val, 3)


# ------------------------------------------------------------------------------------------------------ new looks
def _look_hits(rx: Optional[str], text: str) -> List[str]:
    """What one look pattern found in the text, lowercased; a long match is folded to its first and last word."""
    out = []
    for m in (re.finditer(rx, text, re.I) if rx else ()):
        w = m.group(0).lower().split()
        out.append(" ".join(w) if len(w) <= 3 else f"{w[0]} ... {w[-1]}")
    return out


def look_fit(text: str, rules: dict, skip: Tuple[str, ...] = ()) -> Tuple[List[str], List[str], List[str]]:
    """(the looks, the words that fitted them, the `careful` words) for a text under config `looks`, in its `order`.
    A look fits on its `strong` pattern alone, or on both halves of one of its `pairs` (a shout AND a crowd, a read
    AND a document, pressing AND a two-person setting); never when its `unless` matches. `skip`: looks not to try."""
    names: List[str] = []
    words: List[str] = []
    careful: List[str] = []
    for name in rules.get("order") or []:
        r = rules.get(name)
        if name in skip or not isinstance(r, dict) or (r.get("unless") and re.search(r["unless"], text, re.I)):
            continue
        got = _look_hits(r.get("strong"), text)
        for a, b in r.get("pairs") or []:
            wa = _look_hits(a, text)
            wb = _look_hits(b, text) if wa else []
            if wa and wb:
                got += wa + wb
        if not got:
            continue
        names.append(name)
        words += [w for i, w in enumerate(got) if w not in words and w not in got[:i]]
        hit = _look_hits(r.get("careful"), text)
        careful += [w for i, w in enumerate(hit) if w not in careful and w not in hit[:i]]
    return names, words, careful


def fit_looks(f: dict, scfg: dict) -> Tuple[List[str], List[str], List[str]]:
    """The new looks a find fits by the words of its title and description (look_fit). None for an account without
    config `looks`, for commentary (the one rule: clip the moment, never people talking about it) and for a story the
    off-topic damp turns down (a protest abroad)."""
    rules = scfg.get("looks") or {}
    if not rules or _derivative(f, scfg) or damp(f, scfg) < 1.0:
        return [], [], []
    return look_fit(f["title"] + "\n" + (f.get("desc") or ""), rules)


# -------------------------------------------------------------------------------------------------------- gather
def _vid_row(e: dict, src: dict, now: datetime) -> Optional[dict]:
    vid = e.get("id")
    if not vid or e.get("ie_key") not in (None, "Youtube") or e.get("live_status") in ("is_live", "is_upcoming"):
        return None  # still live: no VOD to cut yet, and its 'views' are viewers
    ts = e.get("timestamp") or e.get("release_timestamp")
    when = datetime.fromtimestamp(ts, timezone.utc) if ts else None
    age = max(0.25, (now - when).total_seconds() / 3600) if when else None
    views = e.get("view_count")
    desc = _snip(e.get("description"))   # a search's snippet; a channel tab or an uploads list has none
    return {"id": vid, "kind": "video", "url": f"https://www.youtube.com/watch?v={vid}", "title": e.get("title") or "",
            "channel": e.get("channel") or src.get("name") or "", "channel_slug": src.get("slug"),
            "source": src["label"], "given_rights": src.get("rights"), "weight": src.get("weight", 1.0),
            "published": when.isoformat() if when else None, "age_h": round(age, 2) if age else None,
            "views": views, "vph": round(views / age, 1) if (views and age) else None,
            "duration": e.get("duration"), "was_live": e.get("live_status") in ("was_live", "is_live"),
            "short": bool(e.get("duration") and e["duration"] <= 70) or "/shorts/" in (e.get("url") or ""),
            **({"roster_kind": src["kind"], "min_minutes": src.get("min_minutes")} if src.get("kind") else {}),
            **({"person": src["person"]} if src.get("person") else {}),   # the face behind the channel (scout.json)
            **({"desc": desc} if desc else {})}


def feed_channel(url: str) -> Optional[str]:
    """The channel id (UC...) in a YouTube channel feed's URL (feeds/videos.xml?channel_id=), else None."""
    m = YT_FEED.match((url or "").strip())
    return m.group(1) if m else None


def _feed_by_ytdlp(src: dict, cid: str, down: Exception, now: datetime) -> Tuple[List[dict], str]:
    """A YouTube channel feed that did not answer, read through yt-dlp (wave 2, 2026-10-18: 11 of the Politics's 12
    feeds answered 404/500 from 10-17 18:40). First the channel's uploads list (UU + the id after UC: every
    upload newest first, Shorts and past streams included, as the feed lists them; the Videos tab leaves the
    Shorts out), then its Videos tab. Each upload goes through _vid_row, as a channel row's does, under the
    feed's own label, rights and weight, named for its channel (yt-dlp's, not the feed's label); an upload
    yt-dlp cannot call live or not keeps the feed's rule (a "WATCH LIVE" title under six hours is still live).
    Returns (rows, the URL read); an empty uploads list tries the Videos tab. Raises, naming both errors, when
    neither list answers."""
    errs = []
    urls = (f"https://www.youtube.com/playlist?list=UU{cid[2:]}", f"https://www.youtube.com/channel/{cid}/videos")
    for i, url in enumerate(urls):
        try:
            entries = _yt(url, FEED_N)
        except Exception as ex:  # noqa: BLE001  (the next list, then the source did not answer)
            errs.append(f"{type(ex).__name__}: {str(ex)[:100]}")
            continue
        if not entries and i + 1 < len(urls):   # an uploads list that reads empty: the Videos tab too
            errs.append("the uploads list read empty")
            continue
        rows = []
        for e in entries:
            r = _vid_row(e, dict(src, slug=None), now)
            if not r:
                continue
            if not e.get("live_status") and LIVE_TITLE.search(r["title"]) and (r["age_h"] is None or r["age_h"] < 6):
                continue
            r["channel"] = e.get("channel") or e.get("playlist_channel") or e.get("playlist_uploader") or r["channel"]
            rows.append(r)
        return rows, url
    raise RuntimeError(f"rss down ({type(down).__name__}: {str(down)[:80]}); yt-dlp too: {'; '.join(errs)}"[:300])


def _channel_outliers(rows: List[dict], by_views: bool = False) -> None:
    """How many times its own channel's usual views an hour an upload is doing: 'went viral', against itself.

    by_views (GTA, 2026-10-17): views come in front-loaded, so a 1.7-hour-old upload's views an hour against older
    uploads' lifetime averages read every fresh upload as viral (Hazardous at 12.6x, x3, ranked #1 and fed). Instead:
    its views against the channel's settled median (48 h+) scaled to the same age, and not before 6 hours."""
    if by_views:
        settled = sorted(r["views"] for r in rows if r.get("views") and (r.get("age_h") or 0) >= 48)
        if len(settled) < 4:
            return
        med = settled[len(settled) // 2]
        for r in rows:
            if r.get("views") and med > 0 and 6 <= (r.get("age_h") or 0) <= 72:
                r["outlier"] = round(r["views"] / (med * min(1.0, math.sqrt(r["age_h"] / 72.0))), 2)
        return
    rates = sorted(r["vph"] for r in rows if r.get("vph") and (r.get("age_h") or 0) >= 1)
    if len(rates) < 4:
        return
    med = rates[len(rates) // 2]
    for r in rows:
        if r.get("vph") and med > 0 and (r.get("age_h") or 99) <= 72:
            r["outlier"] = round(r["vph"] / med, 2)


# --------------------------------------------------------------------------------------------------- stream clips
# BEST OF GTA (2026-10-29, Andrew: "if IShowSpeed is streaming and he has good clips, we'll just pull clips from him...
# they're just the best clips that we can find. The biggest news, the biggest events, the biggest streamers"): a GTA
# scout with `streams` on also reads the clips viewers made on Twitch and Kick (the skill's streamclips.py, through
# drop.streamclips): the game's most viewed clips of the day and the named streamers' own, as finds of kind "clip".
# A clip is on topic by its category, never its title ("becoming a jet with 48% jet"). Who counts at any size: the
# roster's faces (drop.streamers, as drop mode reads them) and the `streams.streamers` list, their clips on the roster
# lane (fed by the scout like a roster upload, `streams.auto_feed` a day); anyone else from the game's top clips only
# over `follower_floor`, a suggestion (lane creator). English only where Twitch says the language; Kick says nothing
# and has no follower count, so `kick_lookups` channels a run are asked (cached in scout/streams.json). At most
# `per_streamer` clips a streamer. A Twitch clip keeps its VOD and offset: a 30 s clip is too short to cut a 30 to 60 s
# clip from, so the night takes the stream around it (makers.stream_intake); a clip without a VOD is kept only when
# it is `min_clip_s` long. views_x: a clip's views count this many times a YouTube view in the score (a Twitch clip
# with 6,900 views in 10 hours sat level with MrBossFTW's 103K-view news upload on 10-29: the day's top two).
# heat_floor: a clip's title rarely says what happened ("hdgnbsfb"), but viewers clipped it because something did:
# its heat starts there. One streamer on two platforms (xQc streams to Twitch and Kick at once) is one streamer.
# Weighted by size: a named face's own weight (scout.json `people`, as drop mode reads it), anyone else's `weight`,
# times the streamer's size (followers per million to the 0.2, between 0.6 and 1.6: xQc's 12.6M x1.6, 300K x0.79),
# so a fresh clip of a small RP streamer does not outrank the biggest streamer of the day on its first hour alone.
STREAM_DEFAULTS: Dict[str, object] = {
    "on": False, "twitch_games": ["Grand Theft Auto V"], "watch_for": ["Grand Theft Auto VI"],
    "kick_categories": ["grand-theft-auto-v"], "follower_floor": 250000, "languages": ["en"], "streamers": [],
    "per_streamer": 2, "min_views": 300, "min_clip_s": 40, "views_x": 20.0, "weight": 0.6, "kick_lookups": 10,
    "auto_feed": 4, "feed_min_score": 1.0, "street_date": "2026-11-19", "heat_floor": 0.5}
STREAM_GAME = re.compile(r"grand theft auto|\bgta\b", re.I)


def stream_settings(scfg: dict) -> dict:
    """The block's `streams` over STREAM_DEFAULTS ({} of the block: off)."""
    return dict(STREAM_DEFAULTS, **(scfg.get("streams") or {}))


def stream_faces(scfg: dict) -> List[dict]:
    """The streamers who count at any size, each {name, twitch, kick, weight}: the roster's faces (drop.streamers) and
    the `streams.streamers` list, one entry per handle."""
    from . import drop as DR
    out: List[dict] = []
    seen: set = set()
    listed = DR.roster(scfg.get("drop") or {}, scfg) + DR.roster({"streamers": stream_settings(scfg)["streamers"]}, scfg)
    for f in listed:
        keys = {f"twitch:{f['twitch']}" if f.get("twitch") else "", f"kick:{f['kick']}" if f.get("kick") else ""} - {""}
        if not keys or keys & seen:
            continue
        seen |= keys
        out.append(f)
    return out


def _clip_find(c: dict, handle: str, st: dict, now: datetime) -> dict:
    """One stream clip as a find (the row shape _vid_row makes, plus the platform, the follower count, the stream's
    title and, for a Twitch clip, its VOD and offset)."""
    when = _parse_when(c.get("created_at") or "") if c.get("created_at") else None
    age = max(0.25, (now - when).total_seconds() / 3600) if when else None
    views = int(c.get("views") or 0)
    x = float(st.get("views_x") or 1.0)
    who, plat = c.get("_who"), str(c.get("platform") or "")
    size = min(1.6, max(0.6, (float(c["_fol"]) / 1e6) ** 0.2)) if c.get("_fol") else 1.0
    row = {"id": re.sub(r"[^A-Za-z0-9_-]", "", f"{plat}-{c.get('id')}")[:64], "kind": "clip", "url": c.get("url"),
           "title": c.get("title") or f"{c['_name']} on {plat.title()}", "channel": c["_name"],
           "channel_slug": handle, "source": f"{plat.title()} clips", "given_rights": "roster" if who else "stream",
           "weight": round((float(who["weight"]) if who else float(st.get("weight") or 1.0)) * size, 3),
           "published": when.isoformat() if when else None, "age_h": round(age, 2) if age else None,
           "views": views, "vph": round(views / age * x, 1) if age else None, "views_x": x,
           "duration": c.get("duration"), "was_live": True, "short": False, "roster_kind": "streamer",
           "person": c["_name"], "platform": plat, "followers": c.get("_fol"),
           "stream_title": str(c.get("vod_title") or "")[:160], "game": c.get("game") or c.get("_cat") or ""}
    if c.get("_vod"):
        row.update(vod_url=f"https://www.twitch.tv/videos/{c['vod_id']}", vod_offset=float(c["vod_offset"]))
    return row


def stream_rows(games: List[dict], roster_res: Optional[dict], faces: List[dict], st: dict,
                kick_followers: Dict[str, int], skip: set, now: datetime) -> List[dict]:
    """The stream clips worth a place on the board, as finds: GTA by category (a GTA VI category before the street
    date is an early copy, never kept), no leak title, the streamer not on `skip`, English where Twitch says, a named
    face at any size and anyone else over follower_floor, at least min_views, cuttable (a Twitch VOD to take the
    moment from, or min_clip_s long); then a streamer's per_streamer most viewed. No reads of its own (the tests'
    seam). A leak title is never kept, before the street date or after (Andrew 10-19: leak counts against a video)."""
    from . import drop as DR
    known: Dict[str, dict] = {}
    for f in faces:
        if f.get("twitch"):
            known[f"twitch:{f['twitch']}"] = f
        if f.get("kick"):
            known[f"kick:{f['kick']}"] = f
    clips: List[dict] = []
    for g in games or []:
        clips += [dict(c, _cat=g.get("game")) for c in g.get("clips") or []]
    for plat in ("twitch", "kick"):
        for _login, r in ((roster_res or {}).get(plat) or {}).items():
            clips += [dict(c, followers=c.get("followers") if c.get("followers") is not None else r.get("followers"))
                      for c in r.get("clips") or []]
    langs = {x.lower() for x in st.get("languages") or []}
    floor = int(st.get("follower_floor") or 0)
    dates = {"street_date": str(st.get("street_date") or "9999")}
    by: Dict[str, List[dict]] = {}
    seen: set = set()
    for c in clips:
        cid = f"{c.get('platform')}-{c.get('id')}"
        if not c.get("id") or not c.get("url") or cid in seen:
            continue
        seen.add(cid)
        game = str(c.get("game") or c.get("_cat") or "")
        if not STREAM_GAME.search(game) or DR.early_copy(game, dates, now.date()):
            continue
        if re.search(DR.DEFAULTS["leak_titles"], str(c.get("title") or ""), re.I):   # never a leak, at any date
            continue
        h = f"{c.get('platform')}:{c.get('broadcaster')}"
        who = known.get(h)
        name = (who or {}).get("name") or c.get("broadcaster_name") or c.get("broadcaster") or ""
        if {name.lower(), str(c.get("broadcaster") or "").lower()} & skip:
            continue
        if langs and c.get("language") and str(c["language"]).lower() not in langs:
            continue
        fol = c.get("followers")
        if fol is None:
            fol = kick_followers.get(h)
        if not who and (fol is None or int(fol) < floor):
            continue
        if int(c.get("views") or 0) < int(st.get("min_views") or 0):
            continue
        vod = c.get("platform") == "twitch" and bool(c.get("vod_id")) and c.get("vod_offset") is not None
        if not vod and float(c.get("duration") or 0) < float(st.get("min_clip_s") or 0):
            continue
        key = str((who or {}).get("name") or h).lower()        # one person on Twitch and Kick counts once
        by.setdefault(key, []).append(dict(c, _h=h, _who=who, _name=name, _fol=None if fol is None else int(fol),
                                           _vod=vod))
    out: List[dict] = []
    for _key, cs in by.items():
        for c in sorted(cs, key=lambda c: -int(c.get("views") or 0))[: max(1, int(st.get("per_streamer") or 2))]:
            out.append(_clip_find(c, c["_h"], st, now))
    return out


def stream_finds(acct: C.Account, scfg: dict, now: Optional[datetime] = None,
                 fetch=None) -> Tuple[List[dict], Optional[str]]:
    """(finds, the error when a read failed) for the account's `streams` block: the game's clips of the day (and GTA
    VI's category the day it exists, looked up once a day), the named streamers' own clips, the Kick follower counts
    it still lacks. Every read fails soft (Cloudflare, Twitch's 400s, the first-100 cap): what answered is used."""
    from . import drop as DR
    st = stream_settings(scfg)
    now = now or datetime.now(timezone.utc)
    fetch = fetch or (lambda args: DR.streamclips(acct, args))
    faces = stream_faces(scfg)
    cache_p = scout_dir(acct) / "streams.json"
    try:
        cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    except (OSError, ValueError):
        cache = {}
    errs: List[str] = []
    games = list(st.get("twitch_games") or [])
    cat = cache.get("categories") or {}
    if cat.get("checked") != now.date().isoformat():
        found = {}
        for name in st.get("watch_for") or []:
            r = fetch(["category", name])
            if isinstance(r, dict) and r.get("id"):
                found[name] = r["id"]
        cat = {"checked": now.date().isoformat(), "found": found}
    games += [g for g in cat.get("found") or {} if g not in games]
    args = ["game", "--period", "LAST_DAY"] + (["--twitch-game", ",".join(games)] if games else []) + \
        (["--kick-category", ",".join(st["kick_categories"])] if st.get("kick_categories") else [])
    g = fetch(args) if len(args) > 3 else {}
    if not isinstance(g, dict) or g.get("error"):
        errs.append(f"game clips: {str((g or {}).get('error'))[:120]}")
        g = {}
    allg = list(g.get("twitch") or []) + list(g.get("kick") or [])
    errs += [f"{x.get('game')}: {str(x.get('error'))[:80]}" for x in allg if x.get("error")]
    tw = [f["twitch"] for f in faces if f.get("twitch")]
    kk = [f["kick"] for f in faces if f.get("kick")]
    r = fetch(["roster", "--period", "LAST_DAY"] + (["--twitch", ",".join(tw)] if tw else [])
              + (["--kick", ",".join(kk)] if kk else [])) if (tw or kk) else {}
    if not isinstance(r, dict) or r.get("error"):
        errs.append(f"streamers' clips: {str((r or {}).get('error'))[:120]}")
        r = {}
    kfol = {k: int(v) for k, v in (cache.get("kick_followers") or {}).items() if v is not None}
    named = {f"kick:{f['kick']}" for f in faces if f.get("kick")}
    unknown = sorted({c["broadcaster"] for x in g.get("kick") or [] for c in x.get("clips") or []
                      if c.get("broadcaster") and f"kick:{c['broadcaster']}" not in kfol
                      and f"kick:{c['broadcaster']}" not in named})[: int(st.get("kick_lookups") or 0)]
    if unknown:
        lr = fetch(["live", "--kick", ",".join(unknown)])
        for slug, row in ((lr.get("kick") or {}) if isinstance(lr, dict) else {}).items():
            if isinstance(row, dict) and row.get("followers") is not None:
                try:
                    kfol[f"kick:{slug}"] = int(row["followers"])
                except (TypeError, ValueError):
                    pass
    try:
        cache_p.parent.mkdir(parents=True, exist_ok=True)
        cache_p.write_text(json.dumps({"categories": cat, "kick_followers": kfol}, indent=1) + "\n")
    except OSError:
        pass
    skip = {str(x).strip().lower() for x in ((acct.raw or {}).get("gta_maker") or {}).get("skip_channels") or []}
    skip |= {str(x).strip().lower() for x in scfg.get("block_channels") or []}
    rows = stream_rows(allg, r, faces, st, kfol, skip, now)
    return rows, ("; ".join(errs) if errs else None)


def gather(slug: str, scfg: dict, acct: C.Account, workers: int = 6,
           notes: Optional[List[dict]] = None, ytdlp: bool = True) -> Tuple[List[dict], List[str], int]:
    """(finds, failed, sources). `notes`, when given, gets one entry per YouTube channel feed that did not answer
    and was read through yt-dlp instead: {source, rss (its error), videos, via (the URL read)}. ytdlp False (the
    breaking desk's quick check, breaking.quick): a feed that does not answer is a failed source, never yt-dlp."""
    now = datetime.now(timezone.utc)
    said = notes if notes is not None else []
    jobs: List[Tuple[str, dict]] = []
    chans = list(scfg.get("youtube_channels", []))
    if scfg.get("roster") == "watchlist":
        chans = roster(acct) + chans
    for ch in chans:
        jobs.append(("channel", dict(ch, label=ch["name"])))
    today = datetime.now().date().isoformat()  # local, as roster() reads its phase
    for s in scfg.get("youtube_searches", []):
        if not _active(s, today):  # a dated search (debates until Nov 3, hearings from Nov 9) runs only in its dates
            continue
        jobs.append(("search", {"label": f"YouTube search: {s['q']} ({s.get('when', 'today')})", "q": s["q"],
                                "when": s.get("when", "today")}))
    for r in scfg.get("rss", []):
        jobs.append(("rss", dict(r, label=r["name"])))
    if is_gta(acct) and stream_settings(scfg).get("on") and ytdlp:   # Best of GTA: Twitch and Kick clips (10-29)
        jobs.append(("streams", {"label": "Twitch + Kick GTA clips"}))
    by_views = bool(scfg.get("outlier_by_views"))

    def run(job):
        kind, src = job
        try:
            if kind == "channel":
                rows = [x for x in (_vid_row(e, src, now) for e in _yt(src["url"], 12)) if x]
                _channel_outliers(rows, by_views)
                return src["label"], rows, None
            if kind == "search":
                return src["label"], [x for x in (_vid_row(e, src, now) for e in _yt(search_url(src["q"], src["when"]), 15)) if x], None
            if kind == "streams":
                rows, err = stream_finds(acct, scfg, now)
                return src["label"], rows, (err if not rows else None)
            try:
                items = _rss(src["url"])
            except Exception as down:  # a YouTube channel feed that does not answer: the same channel via yt-dlp
                cid = feed_channel(src["url"])
                if not cid or not ytdlp:
                    raise
                rows, via = _feed_by_ytdlp(src, cid, down, now)
                _channel_outliers(rows, by_views)
                said.append({"source": src["label"], "rss": f"{type(down).__name__}: {str(down)[:80]}",
                             "videos": len(rows), "via": via})
                return src["label"], rows, None
            arts, vids = [], []
            for a in items:
                age = (now - a["published"]).total_seconds() / 3600 if a["published"] else None
                age = round(max(0.25, age), 2) if age is not None else None
                when = a["published"].isoformat() if a["published"] else None
                if a.get("video_id"):  # a YouTube channel feed: a video with its views, not a headline
                    if LIVE_TITLE.search(a["title"]) and (age is None or age < 6):
                        continue
                    vids.append({"id": a["video_id"], "kind": "video",
                                 "url": f"https://www.youtube.com/watch?v={a['video_id']}", "title": a["title"],
                                 "channel": a.get("author") or src["name"], "channel_slug": None,
                                 "source": src["label"], "given_rights": src.get("rights"),
                                 "weight": src.get("weight", 1.0), "published": when, "age_h": age,
                                 "views": a.get("views"),
                                 "vph": round(a["views"] / age, 1) if (a.get("views") and age) else None,
                                 "duration": None, "was_live": False, "short": "/shorts/" in a["url"],
                                 **({"desc": a["desc"]} if a.get("desc") else {})})
                    continue
                arts.append({"id": "a" + hashlib.sha1(a["url"].encode()).hexdigest()[:11], "kind": "article",
                             "url": a["url"], "title": a["title"], "channel": src["name"], "source": src["label"],
                             "given_rights": src.get("rights", "network"), "weight": src.get("weight", 1.0),
                             "published": when, "age_h": age, **({"desc": a["desc"]} if a.get("desc") else {})})
            _channel_outliers(vids, by_views)
            return src["label"], vids + arts, None
        except Exception as ex:  # one dead source never stops the run
            return src["label"], [], f"{type(ex).__name__}: {str(ex)[:160]}"

    finds: Dict[str, dict] = {}
    failed: List[str] = []
    descs = bool(scfg.get("looks"))   # only the new looks read a description: no other board carries one
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for label, rows, err in ex.map(run, jobs):
            if err:
                failed.append(f"{label}: {err}")
            for r in rows:
                if not descs:
                    r.pop("desc", None)
                prev = finds.get(r["id"])
                if prev:  # the same video from a channel and a search: keep the channel's (it knows its rights)
                    prev.setdefault("also_in", []).append(label)
                    if (not prev.get("channel_slug") and r.get("channel_slug")) or \
                            (not prev.get("given_rights") and r.get("given_rights")):  # a search row, then its feed's
                        r["also_in"] = prev.get("also_in", [])
                        for k in ("duration", "was_live", "desc"):  # a feed row has no length; the search row knew it
                            if r.get(k) in (None, False) and prev.get(k):
                                r[k] = prev[k]
                        finds[r["id"]] = r
                    elif r.get("desc") and not prev.get("desc"):   # a channel row kept: the search's snippet too
                        prev["desc"] = r["desc"]
                    continue
                finds[r["id"]] = r
    return list(finds.values()), failed, len(jobs)


def keep(f: dict, scfg: dict, who, hours: float) -> bool:
    """On the board at all? A GTA account keeps the on-topic only (scout.json "topic_only" says it for any block); the
    Politics also keeps a named person with heat. `who`: the account (is_gta)."""
    if (f.get("channel") or "").strip().lower() in {c.strip().lower() for c in scfg.get("block_channels", [])}:
        return False  # a farm, a persona or a commentary channel (the Politics's block list) never reaches the board
    if named_reaction(f, scfg):  # a named face reacting to a drop stays cuttable for days (the duo)
        hours = float((scfg.get("reactions") or {}).get("hours", hours))
    if f.get("age_h") is not None and f["age_h"] > hours:
        return False
    on_topic = f.get("kind") == "clip" or re.search(scfg.get("topic") or ".", f["title"], re.I)   # a clip: its category
    if scfg.get("topic_only", is_gta(who)):
        return bool(on_topic)
    named = bool(_words_hit(f["title"], scfg.get("people", {})))
    return bool(on_topic) or (named and f["heat"] > 0)


def _credible(f: dict) -> bool:
    """An outlet worth counting: a source the config names (a feed, a channel, the roster) or a video with real reach."""
    return f["kind"] == "article" or bool(f.get("given_rights")) or (f.get("vph_live") or f.get("vph") or 0) >= 1000


_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_DATED = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(?:19|20)\d\d\b"
                    r"|\b(\d{1,2})[/-](\d{1,2})[/-](?:20)?\d\d\b|\b(?:19|20)\d\d-(\d\d)-(\d\d)\b", re.I)


def _stem(w: str) -> str:
    """Possessive and plural off, so "Omar's" meets "Omar" and "gaggles" meets "gaggle"."""
    w = w[:-2] if w.endswith("'s") else w.rstrip("'")
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    return w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w


def _dates(title: str) -> set:
    """(month, day) of every full date in a title: 'Oct. 16, 2026', '10/16/26', '2026-10-16'."""
    out = set()
    for m in _DATED.finditer(title):
        if m.group(1):
            out.add((_MONTHS.index(m.group(1).lower()[:3]) + 1, int(m.group(2))))
        elif m.group(3):
            out.add((int(m.group(3)), int(m.group(4))))
        else:
            out.add((int(m.group(5)), int(m.group(6))))
    return out


def story_tokens(finds: List[dict], ignore: Optional[set] = None) -> Tuple[List[set], Dict[str, int]]:
    """The words that identify a story, per find, and how many titles carry each: title words stemmed, minus stop
    words, minus every word of the ignored phrases (heat and format words), minus words common to the batch. Shared by
    cluster() and the board's video to cut, so the two always agree on what 'about the same story' means."""
    n = len(finds)
    drop = {_stem(w) for k in (ignore or set()) for w in k.split()}
    # hashtags are a channel's tags, not the story: '#gta5 #gtav #gaming' made one story of every GTA 5 live stream
    toks = [{_stem(w) for w in tokens(re.sub(r"#\w+", " ", f["title"]))} - drop - STOP for f in finds]
    df: Dict[str, int] = {}
    for t in toks:
        for w in t:
            df[w] = df.get(w, 0) + 1
    # 'gta', 'senate': in everything, proves nothing. CAPPED at 30 titles (2026-10-17): at 100 finds 30% is 30, but
    # the wider Politics scout reads ~250, where 30% is 74 and "trump" (72 titles) stopped counting as common; one
    # story then swallowed 120 of 247 finds and named an unrelated White House video as its video to cut.
    common = {w for w, c in df.items() if c > max(6, min(0.3 * n, COMMON_CAP))}
    return [t - common for t in toks], df


def cluster(finds: List[dict], ignore: Optional[set] = None) -> List[List[dict]]:
    """The same story from several outlets is one story, and one story is one event. Heat words ('heated', 'clash')
    are left out: two different fights share them.

    2026-10-17: single-link grouping (any one matching pair joined two groups) chained events one shared name at a
    time (Kennedy vs Omar, Kennedy vs Waters, Waters vs Mnuchin), so the Ilhan Omar raid story took in Hegseth, Crow
    and Speaker Johnson. Now a word weighs by its rarity in this batch; two titles match when they share 2+ words
    carrying STORY_MATCH of their joint weight, never when they carry two different dates (the White House posts
    every gaggle under one title); and stories join closest first, only when a pair across them matches AND the two
    stories' word profiles agree (STORY_FIT). A title that matches one member on a side word fails the profile, so
    nothing chains. Measured on the Politics boards of 10-16 and 10-17, labelled by hand: finds sitting in another
    event's story 58 -> 6, real stories cut into extra pieces 36 -> 37. GTA: the trailer story is unchanged."""
    n = len(finds)
    toks, df = story_tokens(finds, ignore)
    idf = {w: math.log((n + 1) / c) for w, c in df.items()}
    mass = [sum(idf[w] for w in t) for t in toks]
    dates = [_dates(f["title"]) for f in finds]

    def clash(i, j):
        return bool(dates[i] and dates[j] and not dates[i] & dates[j])

    near: Dict[int, set] = {i: set() for i in range(n)}  # titles that match: weighted Jaccard of their words
    for i in range(n):
        for j in range(i + 1, n):
            s = toks[i] & toks[j]
            if len(s) < 2 or clash(i, j):
                continue
            w = sum(idf[x] for x in s)
            if w >= STORY_MATCH * (mass[i] + mass[j] - w):
                near[i].add(j)
                near[j].add(i)

    # centroid linkage: a story's profile is the sum of its titles' unit word vectors
    members = {i: [i] for i in range(n)}
    prof, size = {}, {}
    for i in range(n):
        norm = math.sqrt(sum(idf[w] ** 2 for w in toks[i])) or 1.0
        prof[i] = {w: idf[w] / norm for w in toks[i]}
        size[i] = 1.0

    def fit(a, b):  # cosine of two stories' profiles; None when they hold titles of two different days
        if any(clash(i, j) for i in members[a] if dates[i] for j in members[b] if dates[j]):
            return None
        x, y = sorted((prof[a], prof[b]), key=len)
        return sum(v * y.get(w, 0.0) for w, v in x.items()) / (size[a] * size[b])

    fits = {}
    for a in range(n):
        for b in near[a]:
            f = fit(a, b) if a < b else None
            if f is not None:
                fits[(a, b)] = f
    while fits:
        (a, b), best = max(fits.items(), key=lambda kv: (kv[1], -kv[0][0], -kv[0][1]))
        if best < STORY_FIT:
            break
        members[a] += members.pop(b)
        for w, v in prof.pop(b).items():
            prof[a][w] = prof[a].get(w, 0.0) + v
        size[a] = math.sqrt(sum(v * v for v in prof[a].values()))
        size.pop(b)
        near[a] = (near[a] | near.pop(b)) - {a, b}
        for c in near[a]:
            near[c].discard(b)
            near[c].add(a)
        fits = {k: v for k, v in fits.items() if a not in k and b not in k}
        for c in near[a]:
            f = fit(a, c)
            if f is not None:
                fits[(min(a, c), max(a, c))] = f
    return [[finds[i] for i in sorted(m)] for m in sorted(members.values(), key=min)]


# --------------------------------------------------------------------------------------------------------- board
def scout_dir(acct: C.Account) -> Path:
    d = C.STATE / acct.slug / "scout"
    d.mkdir(parents=True, exist_ok=True)
    return d


def decisions(acct: C.Account) -> dict:
    p = scout_dir(acct) / "decisions.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def save_decisions(acct: C.Account, d: dict) -> None:
    (scout_dir(acct) / "decisions.json").write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n")


def board_path(acct: C.Account, day) -> Path:
    return scout_dir(acct) / f"{day.isoformat()}.json"


def read_board(acct: C.Account, day) -> dict:
    try:
        return json.loads(board_path(acct, day).read_text())
    except (OSError, ValueError):
        return {}


def _carry_published(raw: List[dict], boards: Tuple[dict, ...], now: datetime) -> None:
    """An upload's date can only move earlier. A search gives day-granular dates that slide later each day (Kai's
    launch-trailer reaction read 10-16T00 on the 10-16 board, 10-17T00 on the 10-17 board; it went up 10-15), so the
    earliest of today's date, the earlier boards' dates and when the scout first saw it wins, and age and the rate
    follow."""
    for f in raw:
        if not f.get("published"):
            continue
        marks = [f["published"]] + [b.get(f["id"], {}).get(k) for b in boards for k in ("published", "first_seen")]
        try:
            early = min((m for m in marks if m), key=datetime.fromisoformat)
        except (ValueError, TypeError):
            continue
        if early != f["published"]:
            age = round(max(0.25, (now - datetime.fromisoformat(early)).total_seconds() / 3600), 2)
            f.update(published=early, age_h=age)
            if f.get("views"):
                f["vph"] = round(f["views"] / age, 1)


def _story_counts(stories: List[List[dict]], credible_only: bool = False) -> None:
    """Each find's story, its outlets and its fresh outlets. credible_only (the Politics): a farm upload at 170 views an
    hour does not borrow the real story's seven outlets or its breaking flag."""
    for sid, grp in enumerate(stories):
        real = [g for g in grp if _credible(g)]  # a dozen tiny reupload channels are not a dozen outlets
        outlets = max(1, len({g["channel"] for g in real}))
        fresh = len({g["channel"] for g in real if g.get("age_h") is not None and g["age_h"] <= BREAKING_H})
        for g in grp:
            mine = not credible_only or _credible(g)
            g["story"], g["story_outlets"], g["story_fresh"] = sid, outlets if mine else 1, fresh if mine else 0


def rank(raw: List[dict], scfg: dict, acct: C.Account, hours: float, prev: dict, prev_y: Optional[dict] = None,
         dec: Optional[dict] = None, stamp: Optional[str] = None,
         now: Optional[datetime] = None) -> Tuple[List[dict], List[List[dict]], Optional[dict]]:
    """Everything between reading the sources and writing the board: keep, rights, the climb, stories, scores, the
    reaction wave. No reads or writes of its own, so a saved board can be re-ranked under new rules (a backtest).
    prev is today's board (or yesterday's just after midnight), prev_y yesterday's."""
    now = now or datetime.now(timezone.utc)
    stamp = stamp or now.isoformat(timespec="seconds")
    prev_y, dec = prev_y or {}, dec or {}
    _carry_published(raw, (prev, prev_y), now)
    merged = dict(prev_y)
    merged.update(prev)
    rx = scfg.get("reactions") or {}
    wave_ch = {f["channel"] for f in raw if f["kind"] == "video" and is_reaction(f, scfg) and _credible(f)
               and re.search(scfg.get("topic") or ".", f["title"], re.I)
               and (f.get("age_h") or 0) <= float(rx.get("wave_window_h", 72))}

    names = {r["name"].lower(): r for r in roster(acct)} if scfg.get("roster") == "watchlist" else {}
    finds = []
    for f in raw:
        r = names.get((f.get("channel") or "").lower()) if f.get("kind") != "clip" else None
        if r and f.get("given_rights") != "roster":  # a roster creator's upload found by a search is still roster
            f.update(given_rights="roster", channel_slug=r["slug"], weight=r["weight"])
            if r.get("kind"):
                f.update(roster_kind=r["kind"], min_minutes=r.get("min_minutes"))
        f["heat"], f["heat_words"] = heat(f["title"], scfg.get("heat_words", {}), scfg.get("rank"))
        if f.get("kind") == "clip":       # viewers clipped it: a stream clip's heat starts at streams.heat_floor
            f["heat"] = max(f["heat"], float(stream_settings(scfg).get("heat_floor") or 0))
        if not keep(f, scfg, acct, hours):
            continue
        f["rights"] = "roster" if f.get("given_rights") == "roster" else rights(
            f["title"], f["channel"], f.get("given_rights"), scfg)
        f["people"] = [w for w, _ in _words_hit(_who(f, scfg), scfg.get("people", {}))]
        f["derivative"] = _derivative(f, scfg)
        fmt = [w for w, _ in _words_hit(f["title"], scfg.get("format_words", {}))]
        if fmt:
            f["format"] = fmt  # what kind of video it is (gaggle, town hall...), for the bot
        for k in ("looks", "look_words", "look_careful"):   # a saved board ranked again: today's rules decide
            f.pop(k, None)
        lk, lw, care = fit_looks(f, scfg)
        if lk:  # the new looks it fits (THE OUTBURST...), for the bot: a label, never in the score
            f["looks"], f["look_words"] = lk, lw
            if care:
                f["look_careful"] = care
        f["wave"] = len(wave_ch) if (f["channel"] in wave_ch and is_reaction(f, scfg)) else 0
        old = merged.get(f["id"], {})
        f["first_seen"] = old.get("first_seen") or stamp
        seen = [p for p in old.get("seen", []) if p[1] is not None]
        if f.get("views") is not None:
            seen.append([stamp, f["views"]])
        f["seen"] = seen[-24:]
        f["vph_live"] = _live_rate(f["seen"])
        finds.append(f)

    # format words say what kind of video it is, like heat words say how it went: neither identifies a story
    stories = cluster(finds, set(scfg.get("heat_words", {})) | set(scfg.get("format_words", {})))
    _story_counts(stories, bool((scfg.get("rank") or {}).get("outlets_credible_only")))
    for f in finds:
        f["score"] = score(f, scfg)
        f["shape"] = round(_shape(f, scfg, f.get("vph_live") or f.get("vph") or 0.0, f["derivative"]), 3)
        if scfg.get("learned"):    # shown on the find only when the playbook moved it
            try:
                from . import playbook as PB
                lf = float(PB.learned_factor(f, scfg))
                if lf != 1.0:
                    f["learned"] = round(lf, 3)
            except Exception:  # noqa: BLE001
                pass
        f["lane"] = lane(f, acct)
        f["breaking"] = _breaking(f, acct, scfg)
        d = dec.get(f["id"])
        f["status"] = d["status"] if d else "new"
        if d:
            f["why"] = d.get("why", "")

    wave = None
    if rx and len(wave_ch) >= int(rx.get("wave_channels", 5)):
        ids = [f for f in finds if f.get("wave") and f.get("status") != "killed"
               and (f.get("people") or (f.get("views") or 0) >= 100000)]
        ids.sort(key=lambda f: (-name_weight(f, scfg), -(f.get("views") or 0)))
        wave = {"channels": len(wave_ch), "window_h": float(rx.get("wave_window_h", 72)), "ids": [f["id"] for f in ids]}
    return finds, stories, wave


def kill_listed(acct: C.Account, finds: List[dict]) -> int:
    """A find whose video the owner's RIGHTS Pull or Decline killed whole (reasons.py) shows as killed and is never
    fed. Not written to decisions.json: lifting the kill list entry frees it again. Returns how many."""
    from . import reasons as R
    try:
        dead = R.killed_videos(acct)
    except Exception:  # noqa: BLE001  (an unreadable kill list kills nothing)
        return 0
    n = 0
    for f in finds:
        e = dead.get(R.video_key(f.get("url")) or str(f.get("id") or "")) or dead.get(str(f.get("id") or ""))
        if e and f.get("status") in ("new", "picked"):
            f["status"], f["why"] = "killed", f"on the kill list: {R.says(e)}"
            n += 1
    return n


def fed_today(dec: dict, cfg: C.Config, day, by: str = "scout") -> int:
    """How many finds the scout fed today: auto_feed is a day's allowance, not an hour's (8 fed in 21 hours on 10-17).
    by: "scout-streams" counts the stream clips, which have their own allowance (streams.auto_feed)."""
    n = 0
    for d in dec.values():
        if d.get("status") == "fed" and d.get("by") == by and d.get("at"):
            try:
                if datetime.fromisoformat(d["at"]).astimezone(cfg.tz()).date() == day:
                    n += 1
            except ValueError:
                pass
    return n


def feed_pool(finds: List[dict], scfg: dict) -> List[dict]:
    """What the scout may feed by itself: a roster upload, new, at the score floor, not turned down by `damp`, and
    of a kind Gaming cuts (config feed_kinds: not the GTA 5 physics and comparison channels)."""
    kinds = set(scfg.get("feed_kinds") or [])
    floor = float(scfg.get("feed_min_score", 0))
    return [f for f in finds if f["lane"] == "roster" and f.get("status") == "new" and f["score"] >= floor
            and f.get("shape", 1.0) >= 1.0 and (not kinds or (f.get("roster_kind") or "") in kinds)
            and f["kind"] != "clip"]          # a stream clip has its own allowance (stream_pool)


def stream_pool(finds: List[dict], scfg: dict) -> List[dict]:
    """The stream clips the scout may feed by itself (Best of GTA, 10-29): a named face's (lane roster), new, at
    streams.feed_min_score. A clip's title rarely carries heat, so the uploads' floor would keep every one out."""
    floor = float(stream_settings(scfg).get("feed_min_score") or 0)
    return [f for f in finds if f["kind"] == "clip" and f["lane"] == "roster" and f.get("status") == "new"
            and f["score"] >= floor]


def run(cfg: C.Config, acct: C.Account, hours: Optional[float] = None, feed: Optional[int] = None) -> List[str]:
    scfg = load(acct.slug)
    try:  # the learner's approved factors, after the dated phases (playbook.py; only with learn.playbook on)
        from . import playbook as PB
        scfg = PB.apply_to_scout(acct, scfg)
    except Exception:  # noqa: BLE001  (the scout never stops over the playbook)
        pass
    hours = float(hours or scfg.get("hours", 36))
    t0 = time.time()
    fallback: List[dict] = []   # YouTube channel feeds that did not answer, read via yt-dlp (gather)
    raw, failed, n_sources = gather(acct.slug, scfg, acct, notes=fallback)
    now_local = cfg.now()
    day = now_local.date()
    prev = read_board(acct, day).get("finds", {})
    prev_y = read_board(acct, day - timedelta(days=1)).get("finds", {})
    if not prev:  # just after midnight: carry the climb from yesterday's board
        prev = prev_y
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dec = decisions(acct)
    finds, stories, wave = rank(raw, scfg, acct, hours, prev, prev_y, dec, stamp)
    kill_listed(acct, finds)

    lines = []
    fed = []
    feed_said: List[str] = []      # what feed_gta had to say (an inbox it made, finds another GTA account has)
    n = scfg.get("auto_feed", 0) if feed is None else feed
    if acct.maker == "gta" and n:
        if feed is None:  # the hourly run: a day's allowance, at the floor, of the kinds Gaming cuts
            n -= fed_today(dec, cfg, day)
            pool = feed_pool(finds, scfg)
        else:  # a person asking for N: as before
            pool = [f for f in finds if f["lane"] == "roster" and f["status"] == "new"]
        fed = feed_gta(acct, pool, n, cfg=cfg, said=feed_said) if n > 0 else []
        for f in finds:
            if f["id"] in fed:
                f["status"] = "fed"
        for vid in fed:
            dec[vid] = {"status": "fed", "by": "scout", "at": stamp, "why": "a roster upload the scout ranked top"}
        if fed:
            save_decisions(acct, dec)
    sst = stream_settings(scfg)
    if acct.maker == "gta" and feed is None and sst.get("on"):   # the stream clips' own day allowance (Best of GTA)
        m = int(sst.get("auto_feed") or 0) - fed_today(dec, cfg, day, by="scout-streams")
        cfed = feed_gta(acct, stream_pool(finds, scfg), m, cfg=cfg, said=feed_said) if m > 0 else []
        for f in finds:
            if f["id"] in cfed:
                f["status"] = "fed"
        for vid in cfed:
            dec[vid] = {"status": "fed", "by": "scout-streams", "at": stamp,
                        "why": "a named streamer's clip the scout ranked top"}
        if cfed:
            save_decisions(acct, dec)
            fed = fed + cfed

    rescored = refresh_fed(acct, finds, scfg) if acct.maker == "gta" else []   # audit GTA-8
    board = {"account": acct.slug, "name": acct.name, "date": day.isoformat(), "updated": stamp, "hours": hours,
             "sources": n_sources, "failed": failed, "fallback": fallback, "seconds": round(time.time() - t0, 1),
             "wave": wave,
             "finds": {f["id"]: f for f in finds}}
    board_path(acct, day).write_text(json.dumps(board, indent=1, ensure_ascii=False) + "\n")
    try:                                # the breaking desk's alarms from this board (breaking.py); never stops the scout
        from . import breaking as BK
        alarm_said = BK.on_scout(cfg, acct, board)
    except Exception as e:  # noqa: BLE001
        alarm_said = [f"breaking alarm not written: {type(e).__name__}: {e}"]
    md = write_markdown(cfg, acct, board)
    vids = [f for f in finds if f["kind"] == "video"]
    lines.append(f"{acct.name} scout: {len(vids)} videos and {len(finds) - len(vids)} articles on topic from "
                 f"{n_sources - len(failed)} of {n_sources} sources"
                 f"{f' ({len(fallback)} rss down, read via yt-dlp)' if fallback else ''}, {len(stories)} stories, "
                 f"{len({f['story'] for f in finds if f['breaking']})} of them breaking ({board['seconds']}s)")
    lines += alarm_said                 # after the summary: a quiet log keeps the first and last lines
    if fed:
        lines.append(f"fed {len(fed)} roster upload(s) to the GTA inbox, ahead of tonight's cut: {', '.join(fed)}")
    lines += feed_said
    if rescored:
        lines.append(f"re-scored {len(rescored)} lifted inbox row(s) from this board: {', '.join(rescored)}")
    for f in top(finds, 5):
        lines.append(f"  {f['score']:6.2f}  {_age(f):>5}  {f['lane']:<15} {f['channel'][:22]:<22} {f['title'][:80]}")
    if fallback:
        lines.append("rss down, read via yt-dlp: " + "; ".join(x["source"] for x in fallback[:6])
                     + (f" (+{len(fallback) - 6} more)" if len(fallback) > 6 else ""))
    if failed:
        lines.append(f"{len(failed)} source(s) did not answer: " + "; ".join(x.split(':')[0] for x in failed[:6]))
    lines.append(f"board: {md}")
    return lines


def _live_rate(seen: List[list]) -> Optional[float]:
    """Views an hour between the oldest and newest reading in the last six hours (needs 25 minutes apart)."""
    if len(seen) < 2:
        return None
    pts = [(datetime.fromisoformat(s), v) for s, v in seen]
    last_t, last_v = pts[-1]
    window = [(t, v) for t, v in pts if (last_t - t).total_seconds() <= 6 * 3600]
    t0, v0 = window[0]
    dh = (last_t - t0).total_seconds() / 3600
    return round((last_v - v0) / dh, 1) if dh >= 0.4 and last_v >= v0 else None


def _breaking(f: dict, acct, scfg: Optional[dict] = None) -> bool:
    if f.get("age_h") is None or f["age_h"] > BREAKING_H or f.get("kind") == "clip":
        return False             # a stream clip's rate is scaled (views_x): a moment of a stream, never breaking news
    if f.get("derivative") or (f.get("rights") == "reupload" and not _credible(f)):
        return False  # 'Breaking now' lists moments, not panels about them or farm re-tellings
    rate = f.get("vph_live") or f.get("vph") or 0
    floor = float((scfg or {}).get("breaking_vph") or BREAKING_VPH.get(getattr(acct, "maker", ""), 100000))
    return rate >= floor or f.get("story_fresh", 1) >= 3 or (f.get("outlier") or 0) >= 3


def top(finds: List[dict], n: int, kind: Optional[str] = None) -> List[dict]:
    pool = [f for f in finds if f.get("status") != "killed" and (kind is None or f["kind"] == kind)]
    return sorted(pool, key=lambda f: -f["score"])[:n]


def _age(f: dict) -> str:
    a = f.get("age_h")
    return "?" if a is None else (f"{a * 60:.0f}m" if a < 1 else f"{a:.0f}h")


def _n(v) -> str:
    if v is None:
        return ""
    v = float(v)
    return f"{v / 1e6:.1f}M" if v >= 1e6 else (f"{v / 1e3:.0f}k" if v >= 1e3 else f"{v:.0f}")


def _cell(s: str) -> str:
    return (s or "").replace("|", "/").replace("\n", " ")


def video_to_cut(g: List[dict], rk: Optional[dict] = None,
                 words: Optional[Dict[str, set]] = None) -> Tuple[dict, Optional[dict]]:
    """A story's headline and the video to cut from it: the raw moment, never a panel about it. The headline is the
    best find that is not commentary or a reupload; the video is a cleared one about that headline (sharing two title
    words: a loose story once named "President Trump Hosts First Responders" for the Ilhan Omar investigation, 10-17),
    not commentary first, then an official or C-SPAN feed, then (the Politics) a 2-20 minute file, then the score.
    On 10-17 it names C-SPAN's "You should reconsider" for the Zacharia story, not the MS NOW compilation.
    words (find id -> story_tokens): compare story words, not raw title words. Raw words let the White House's
    departure gaggle in on "trump" + "press" alone (15:40 board, found by the story-grouping session)."""
    rk = rk or {}
    raw = [x for x in g if not x.get("derivative") and x.get("rights") != "reupload"] or g
    best = max(raw, key=lambda x: x["score"])

    def w(x):
        return words[x["id"]] if words is not None and x["id"] in words else tokens(x["title"])

    bt = w(best)
    clean = [x for x in g if x["kind"] == "video" and x.get("rights") in CLEARED + ("roster",)
             and (x is best or len(w(x) & bt) >= 2)]
    clean.sort(key=lambda x: (not x.get("derivative"), x.get("rights") in PRIMARY_FEEDS,
                              bool(rk) and 120 <= (x.get("duration") or 0) <= 1200, x["score"]), reverse=True)
    return best, (clean[0] if clean else None)


def _not_a_fight(f: dict, acct: C.Account) -> bool:
    """The Politics's Fights list is US politics, cut from the source (audit LDG-12, 2026-10-17): a story the
    off-topic damp turns down (France, Paris, parliament... with no US name in the title) and an unknown channel's
    reupload (lane "find original") stay in "Videos, ranked" but are not a Fight. The Politics's maker, not its slug."""
    if acct.maker != "politics":
        return False
    if f.get("rights") == "reupload":
        return True
    try:
        return damp(f, load(acct.slug)) < 1.0
    except (KeyError, TypeError, ValueError, OSError):
        return False


def _look_tag(names: Optional[List[str]], rules: dict) -> str:
    """'OUTBURST · ' in front of a find's line, for the new looks it fits (their config names without 'THE '); ''
    for none, so a board without looks reads as before."""
    tags = [re.sub(r"^THE\s+", "", str(rules[n].get("name") or n).upper()) for n in names or []
            if isinstance(rules.get(n), dict)]
    return "/".join(tags) + " · " if tags else ""


VAULT_ROW = re.compile(r"^\|\s*(V-[A-Z]{2}-\d+)\s*\|")


def _checked_outcomes(text: str, marker: Optional[str]) -> Dict[str, str]:
    """{vault id: what happened next} from vault.md's own sentence ("Outcomes checked so far: V-HR-05 (Stumpf resigned
    2016-10-12, Wells Fargo 8-K), V-FL-07 (...)."): each id with the words in its brackets, to the sentence's end."""
    flat = " ".join(text.split())
    i = flat.find(marker) if marker else -1
    if i < 0:
        return {}
    s, depth = flat[i + len(marker):], 0
    for j, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        elif ch == "." and not depth and (j + 1 == len(s) or s[j + 1] == " "):   # not the dot in berlin.de
            s = s[:j]
            break
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"\b(V-[A-Z]{2}-\d+) \(([^()]*)\)", s)}


def vault_rows(acct: C.Account, scfg: dict) -> Tuple[List[dict], Dict[str, str]]:
    """THE VAULT (the account folder's Reference/vault.md, config looks.vault.path): the rows nobody has taken (an
    empty `cut` column: not cut, prepped or benched) and that are not held (`skip_flags`: HOLD is Andrew's call
    first), in the file's order. Each row gets the new looks its moment fits (look_fit on the moment, the line and
    where it happened; THE FLASHBACK takes every row, so it is not fitted by words) and the scout's `people` it
    names. Also the outcomes checked so far ({id: what happened next}). ([], {}) without looks or the file."""
    rules = scfg.get("looks") or {}
    v = rules.get("vault") or {}
    if not v.get("path"):
        return [], {}
    try:
        text = (Path(acct.account_dir) / v["path"]).read_text()
    except (OSError, UnicodeDecodeError):
        return [], {}
    every = tuple(n for n in rules.get("order") or [] if (rules.get(n) or {}).get("every_vault_row"))
    where, people = v.get("where") or {}, scfg.get("people") or {}
    rows: List[dict] = []
    head: Optional[List[str]] = None
    for ln in text.splitlines():
        if ln.startswith("#"):
            head = None                        # a new section: its table brings its own header
        elif ln.startswith("| id |"):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
        elif head and VAULT_ROW.match(ln):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) != len(head):        # a '|' inside a cell: a row the table cannot read
                continue
            r = dict(zip(head, cells))
            if r.get("cut") or (v.get("skip_flags") and re.search(v["skip_flags"], r.get("flags") or "")):
                continue
            fmt = r.get("format") or ""
            r["looks"] = look_fit(f"{r.get('moment', '')}\n{r.get('the line', '')}\n{where.get(fmt, fmt)}", rules,
                                  skip=every)[0]
            r["names"] = [w for w, _ in _words_hit(r.get("people") or "", people)]
            r["heat_n"] = int(r["heat"]) if str(r.get("heat") or "").isdigit() else 0
            rows.append(r)
    return rows, _checked_outcomes(text, v.get("checked"))


def _todays_names(finds: List[dict], stories: List[List[dict]], peg: dict) -> set:
    """The people (config `people`) today's top stories name, less anyone a quarter of the board names: Trump is in
    every other title, so he ties no vault row to today's news."""
    live = [f for f in finds if f.get("status") != "killed"]
    count: Dict[str, int] = {}
    for f in live:
        for p in f.get("people") or []:
            count[p] = count.get(p, 0) + 1
    top = {p for g in stories[:int(peg.get("stories", 12))] for f in g for p in f.get("people") or []}
    return {p for p in top if count.get(p, 0) <= float(peg.get("common", 0.25)) * max(1, len(live))}


def _new_look_lines(acct: C.Account, scfg: dict, finds: List[dict], stories: List[List[dict]]) -> List[str]:
    """The board's New looks block (config `looks`; [] without): per look, in `order`, today's videos that fit it, by
    score (a reupload is listed: an outburst is often named only by the copies, never by the full rally), then THE
    VAULT's uncut rows that fit it (today's names first, then heat). THE FLASHBACK takes any vault row, so it lists
    the rows whose outcome is checked, then the rows tied to today's news."""
    rules = scfg.get("looks") or {}
    order = [n for n in rules.get("order") or [] if isinstance(rules.get(n), dict)]
    if not order:
        return []
    show = rules.get("show") or {}
    n_news, n_vault = int(show.get("news", 5)), int(show.get("vault", 3))
    rows, checked = vault_rows(acct, scfg)
    names = _todays_names(finds, stories, rules.get("peg") or {})
    for x in rows:
        x["peg"] = [p for p in x["names"] if p in names]
    L = ["## New looks", "",
         "The four new looks, on since 2026-10-21 (Andrew: \"the outbursts especially will do the best, but we really "
         "want those to be energetic\"). A find is listed by the words of its title or description: a lead, never a "
         "check, so watch the moment before you slate it. Today's finds first, by score (a reupload is a lead: cut "
         "from the original), then THE VAULT's uncut rows (today's names first; say the year on the clip). A look "
         "never moves a score, and a new-look slate preflight refuses twice is cut in its usual segment.", ""]
    for name in order:
        r = rules[name]
        L.append(f"**{r.get('name') or name.upper()}**: {r.get('what', '')}")
        news = sorted([f for f in finds if name in (f.get("looks") or []) and f["kind"] == "video"
                       and f.get("status") != "killed"], key=lambda f: -f["score"])
        for f in news[:n_news]:
            line = (f"- **{_cell(f['title'])}**: {f['channel']} ({f['rights']}), {_age(f)} old, "
                    f"{_n(f.get('vph_live') or f.get('vph'))} an hour, heat {f['heat']:.2f} "
                    f"({', '.join((f.get('look_words') or [])[:4])}). {f['url']}  `{f['id']}`")
            if f.get("look_careful") and r.get("careful_note"):
                line += " " + r["careful_note"].format(words=", ".join(f["look_careful"][:3]))
            L.append(line)
        if not news:
            L.append("- Nothing in today's news.")
        if r.get("every_vault_row"):    # the outcome on record first, then the rows today's news makes timely
            pool = (sorted([x for x in rows if x["id"] in checked], key=lambda x: -x["heat_n"])[:n_vault]
                    + sorted([x for x in rows if x["peg"] and x["id"] not in checked],
                             key=lambda x: -x["heat_n"])[:n_vault])
        else:
            pool = sorted([x for x in rows if name in x["looks"]], key=lambda x: (not x["peg"], -x["heat_n"]))
            pool = pool[:n_vault]
        for x in pool:
            peg = f", in today's news: {', '.join(x['peg'])}" if x["peg"] else ""
            line = (f"- Vault `{x['id']}` ({x.get('year', '')}, {x.get('format', '')}, heat {x.get('heat', '')}{peg}): "
                    f"{_cell(x.get('moment', ''))} {x.get('url', '')}")
            if r.get("every_vault_row"):
                line += (f" What happened next (checked): {checked[x['id']]}." if x["id"] in checked else
                         " Check the outcome first: a dated public source for what happened next, or no FLASHBACK.")
            L.append(line)
        L.append("")
    return L


def write_markdown(cfg: C.Config, acct: C.Account, board: dict) -> Path:
    finds = list(board["finds"].values())
    day = datetime.fromisoformat(board["date"]).date()
    out_dir = cfg.drafts_root / "Scout"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{acct.name} scout {day.strftime('%m.%d.%Y')}.md"
    when = datetime.fromisoformat(board["updated"]).astimezone(cfg.tz()).strftime("%a %d %b, %H:%M") + " Mountain"
    scfg = load(acct.slug)
    rules = scfg.get("looks") or {}

    def tag(f: dict) -> str:   # 'OUTBURST · ' when the find fits a new look
        return _look_tag(f.get("looks"), rules)

    L = [f"# {acct.name} scout, {when}", "",
         f"{board['sources'] - len(board['failed'])} of {board['sources']} sources read, last {board['hours']:.0f} hours. "
         f"Rerun: `python3 ~/clip-factory/factory.py scout {acct.slug}`. Pick: `--pick ID --why \"...\"`, kill: `--kill ID [ID ...] --why \"...\"`.",
         ""]
    if not is_gta(acct):
        L += ["Rights: **cut** finds can be cut as they are: a member's or a committee's own channel, or (since "
              "2026-10-16) a network or C-SPAN copy, which runs with `analyze.py --rights rebroadcast` and may draw a "
              "Content ID claim. **find original** means an unknown channel's copy, often a re-cut and sometimes fake: "
              "find where it first aired.", ""]
    else:
        L += ["**roster** finds can go straight to the inbox (the scout feeds the top ones itself). **creator** finds are "
              "outside the roster: a suggestion, and a name to add to the watchlist if it keeps showing up.", ""]

    picks = [f for f in finds if f.get("status") in ("picked", "fed")]
    if picks:
        L += ["## Picked", ""]
        for f in sorted(picks, key=lambda f: -f["score"]):
            L.append(f"- {tag(f)}**{_cell(f['title'])}** ({f['channel']}, {_age(f)}) {f['url']}  `{f['id']}` "
                     f"{f['status']}" + (f": {f['why']}" if f.get("why") else ""))
        L.append("")

    if is_gta(acct):   # drop mode (clipfactory/drop.py): the open Rockstar drop's reactions, the faces beyond the roster
        from . import drop as DR
        try:
            dl = DR.board_lines(acct)
        except (OSError, ValueError, KeyError, TypeError):
            dl = []
        if dl:
            L += dl[1:] + [""]

    brk = [f for f in top(finds, 50) if f["breaking"]]
    if brk:
        L += ["## Breaking now", ""]
        for f in brk[:8]:
            L.append(f"- {tag(f)}**{_cell(f['title'])}**: {f['channel']}, {_age(f)} old, "
                     f"{_n(f.get('vph_live') or f.get('vph'))} views an hour, {f['lane']}. {f['url']}  `{f['id']}`")
        L.append("")

    wave = board.get("wave")
    if wave:
        byid = board["finds"]
        L += ["## Reaction wave", "",
              f"{wave['channels']} channels reacted to the same Rockstar drop in the last {wave['window_h']:.0f} hours. "
              "Cut the REACTIONS duo from the named faces below (gaming skill, multireact.py plan). A reupload only "
              "finds the moment: cut from the streamer's own upload.", ""]
        for i in wave["ids"][:12]:
            f = byid.get(i)
            if not f:
                continue
            mins = f"{int(f['duration'] // 60)}m, " if f.get("duration") else ""
            L.append(f"- **{f['channel']}** ({', '.join(f.get('people') or []) or 'no name'}): {_n(f.get('views'))} views, "
                     f"{mins}{_age(f)} old. {f['url']}  `{f['id']}`")
        L.append("")

    fights = [f for f in top(finds, 60, "video") if f["heat"] >= FIGHT and _credible(f) and not f.get("derivative")
              and not _not_a_fight(f, acct)]
    if fights:
        L += (["## Fights", "", "The heated ones: yelling, a clash, a refusal pressed, a meltdown. Ranked by score.", ""]
              if not is_gta(acct) else
              ["## Hottest", "", "The high-energy ones: a trailer, a reaction, a rage, a chase. Ranked by score.", ""])
        for f in fights[:10]:
            L.append(f"- {tag(f)}**{_cell(f['title'])}**: {f['channel']} ({f['rights']}), {_age(f)} old, "
                     f"{_n(f.get('vph_live') or f.get('vph'))} an hour, heat {f['heat']:.2f} ({', '.join(f['heat_words'][:4])}). "
                     f"{f['url']}  `{f['id']}`")
        L.append("")

    groups: Dict[int, List[dict]] = {}
    for f in finds:
        if f.get("status") != "killed":
            groups.setdefault(f["story"], []).append(f)
    ranked = sorted(groups.values(), key=lambda g: -max(x["score"] for x in g))
    L += ["## Top stories", "", "One line per story; the outlets telling it, and its best video.", ""]
    rk = scfg.get("rank") or {}
    toks, _df = story_tokens(finds, set(scfg.get("heat_words", {})) | set(scfg.get("format_words", {})))
    words = {f["id"]: t for f, t in zip(finds, toks)}
    for g in ranked[:12]:
        best, cut = video_to_cut(g, rk, words)
        vids = sorted([x for x in g if x["kind"] == "video"], key=lambda x: -x["score"])
        clean = [cut] if cut else []
        heatw = sorted({w for x in g for w in x["heat_words"]})
        fits = set(best.get("looks") or []) | set((cut or {}).get("looks") or [])   # the headline's and the cut's
        line = (f"- {_look_tag([n for n in rules.get('order') or [] if n in fits], rules)}**{_cell(best['title'])}**: "
                f"{len({x['channel'] for x in g})} outlet(s)"
                + (f", heat: {', '.join(heatw[:5])}" if heatw else ""))
        if clean:
            line += f". Video to cut ({clean[0]['rights']}): {clean[0]['channel']} {clean[0]['url']} `{clean[0]['id']}`"
        elif vids:
            line += f". Video ({vids[0]['rights']}): {vids[0]['channel']} {vids[0]['url']} `{vids[0]['id']}`"
        else:
            line += f". No video yet: {best['url']}"
        L.append(line)
    L.append("")
    L += _new_look_lines(acct, scfg, finds, ranked)

    L += ["## Videos, ranked", "", "| score | age | views | an hour | channel | rights | lane | title | id |",
          "| ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |"]
    for f in top(finds, 30, "video"):
        L.append(f"| {f['score']:.2f} | {_age(f)} | {_n(f.get('views'))} | {_n(f.get('vph_live') or f.get('vph'))} | "
                 f"{_cell(f['channel'])[:28]} | {f['rights']} | {f['lane']} | {tag(f)}[{_cell(f['title'])[:90]}]({f['url']}) | `{f['id']}` |")
    L.append("")

    clips = top(finds, 15, "clip")       # Best of GTA (2026-10-29): the streamers' Twitch and Kick clips
    if clips:
        L += ["## Stream clips, ranked", "",
              "The day's Twitch and Kick clips (streams): a named face's are fed to the inbox; the rest are suggestions.",
              "", "| score | age | clip views | streamer | followers | lane | clip | id |",
              "| ---: | ---: | ---: | --- | ---: | --- | --- | --- |"]
        for f in clips:
            L.append(f"| {f['score']:.2f} | {_age(f)} | {_n(f.get('views'))} | {_cell(f['channel'])[:24]} "
                     f"({f.get('platform')}) | {_n(f.get('followers'))} | {f['lane']} | "
                     f"[{_cell(f['title'])[:70]}]({f['url']}) | `{f['id']}` |")
        L.append("")

    arts = top(finds, 12, "article")
    if arts:
        L += ["## Headlines", ""]
        for f in arts:
            L.append(f"- {tag(f)}{_cell(f['title'])} ({f['channel']}, {_age(f)}) {f['url']}")
        L.append("")
    killed = [f for f in finds if f.get("status") == "killed"]
    if killed:
        L += ["## Killed", ""] + [f"- {_cell(f['title'])} `{f['id']}`: {f.get('why', '')}" for f in killed[:20]] + [""]
    L += _kill_list_lines(acct)
    if board["failed"]:
        L += ["## Sources that did not answer", ""] + [f"- {x}" for x in board["failed"]] + [""]
    if board.get("fallback"):
        L += ["## Feeds down, read via yt-dlp", ""] + [
            f"- {x.get('source')}: the feed said {x.get('rss')}; yt-dlp found {x.get('videos')}"
            for x in board["fallback"] if isinstance(x, dict)] + [""]
    path.write_text("\n".join(L))
    return path


def _kill_list_lines(acct: C.Account) -> List[str]:
    """The kill list on the board (reasons.py): the seconds and videos the owner pulled or declined, so a run
    slating tonight's clips never cuts them again (collect refuses one that does)."""
    from . import reasons as R
    try:
        es = [e for e in R.entries(acct) if e.get("video")][-20:]
    except Exception:  # noqa: BLE001
        return []
    if not es:
        return []
    out = ["## Kill list: never cut these again", "",
           "Pulled or declined by a person. A clip with more than 15% of its seconds inside a span here is refused at "
           f"collect; check one first: `factory.py killlist {acct.slug} --check URL --span A-B`.", ""]
    for e in reversed(es):
        what = "the whole video" if e.get("whole_video") else ", ".join(
            f"{a:.0f}-{b:.0f}s" for a, b in (e.get("segments") or [])) or "?"
        out.append(f"- `{e['video']}` {what}: {R.says(e)} ({_cell(str(e.get('slug') or ''))})")
    return out + [""]


# ---------------------------------------------------------------------------------------------- picks and feeding
def decide(cfg: C.Config, acct: C.Account, ids: List[str], status: str, why: str, by: str) -> List[str]:
    """--pick / --kill: the bot's or the owner's call on a find. A GTA pick goes into the account's inbox."""
    from . import makers as M       # lazy both ways: makers reads the scout only inside night_off_topic
    day = cfg.now().date()
    board = read_board(acct, day)
    finds = board.get("finds", {})
    if not finds:
        board = read_board(acct, day - timedelta(days=1))
        finds = board.get("finds", {})
    dec = decisions(acct)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = []
    for i in ids:
        f = finds.get(i)
        if not f:
            lines.append(f"{i}: not on the scout board (run `factory.py scout {acct.slug}` first)")
            continue
        dec[i] = {"status": status, "by": by, "at": stamp, "why": why, "title": f["title"], "url": f["url"]}
        f["status"], f["why"] = status, why
        if status == "picked" and acct.maker == "gta" and f["kind"] == "video":
            said: List[str] = []
            fed = feed_gta(acct, [f], 1, force=True, cfg=cfg, said=said)
            lines += said
            lines.append(f"{i}: picked and put at the top of the GTA inbox" if fed else
                         (f"{i}: picked, not fed" if said else f"{i}: picked (already in the inbox)"))
            kind = row_kind(acct, f)
            if fed and not M.takes_kind(acct, kind):    # a roster channel of the wrong kind: fed, never cut
                lines.append(f"{i}: but no slot of {acct.name} cuts an inbox row of kind {kind!r} (its gameplay "
                             f"slots take {', '.join(sorted(M.playing_kinds(M.gta_settings(acct))))}): give the "
                             f"channel one of those kinds in Publish/watchlist.json, or kill the pick")
            post = cfg.posting_day()
            if fed and M.off_topic_title(f, M.night_off_topic(acct, post)):   # the game switch (skip_off_topic)
                lines.append(f"{i}: but the night does not cut it: its title is off topic for {acct.name} on {post} "
                             f"(config/scout.json damp.off_topic for that date, gta_maker skip_off_topic): kill the "
                             f"pick")
        elif status == "killed" and acct.maker == "gta" and unfeed_gta(acct, i, why):
            lines.append(f"{i}: killed and taken out of the GTA inbox (the scout had fed it)")
        else:
            lines.append(f"{i}: {status}")
    save_decisions(acct, dec)
    if board:
        board_path(acct, datetime.fromisoformat(board["date"]).date()).write_text(
            json.dumps(board, indent=1, ensure_ascii=False) + "\n")
        lines.append(f"board: {write_markdown(cfg, acct, board)}")
    return lines


def unfeed_gta(acct: C.Account, vid: str, why: str) -> bool:
    """A kill takes back what the scout fed: its inbox row leaves the queue (status 'killed'; the maker and watch.py
    cut only 'new' rows, and a scan never re-adds a known video). Only rows the scout itself fed and nobody cut yet;
    watch.py's own rows and a person's `pull --url` are left alone. 2026-10-17: the old rules had fed GTA 5 stunt
    channels, and a kill used to leave them at the top of the night's cut."""
    p = acct.account_dir / "Publish" / "inbox.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return False
    row = (data.get("items") or {}).get(vid) if isinstance(data.get("items"), dict) else None
    if not row or row.get("found_by") != "scout" or row.get("status") != "new":
        return False
    row.update(status="killed", killed_why=why,
               killed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    tmp = p.with_suffix(".json.scout-tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(p)
    return True


def refresh_fed(acct: C.Account, finds: List[dict], scfg: dict) -> List[str]:
    """GTA (audit GTA-8, 2026-10-17): the rows this scout fed keep their lift for 30 hours (`scout_until`), but at the
    score they had when fed: MrBoss gewxOv6RNcg sat at 13.79 at 21:00 while the board had it at 2.40, so it took three
    slots ahead of better finds. Each run re-scores the lifted rows still waiting ('new', fed by the scout, lift not
    run out) that are on this board as 'fed': 10 + the live score, or the plain score once the live score is under
    feed_min_score. A person's pick, a row not on the board, and every row while the GTA worker is cutting are left
    alone. Returns the ids re-scored."""
    from . import makers as M       # lazy both ways: makers reads the scout only inside night_off_topic
    if M.other_worker_running(acct):   # the account's worker (the 21:00 night, the 03:45 retry, a gap-fill's make)
        return []
    p = acct.account_dir / "Publish" / "inbox.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    items = data.get("items")
    if not isinstance(items, dict):
        return []
    live = {f["id"]: f for f in finds if f.get("kind") == "video" and f.get("status") == "fed"}
    floor = float(scfg.get("feed_min_score", 0))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    moved: List[str] = []
    for vid, row in items.items():
        f = live.get(vid)
        if not f or not isinstance(row, dict) or row.get("found_by") != "scout" or row.get("status") != "new":
            continue
        if not row.get("scout_until") or str(row["scout_until"]) < now:
            continue                              # the lift ran out: makers reads base_score, as it always did
        sc = float(f.get("score") or 0)
        new = round(10.0 + sc, 3) if sc >= floor else round(min(sc, 5.0), 3)
        if abs(float(row.get("score") or 0) - new) < 0.25:
            continue                              # the same rank: no write
        row.update(score=new, base_score=round(min(sc, 5.0), 3), rescored_at=now)
        moved.append(vid)
    if moved:
        tmp = p.with_suffix(".json.rescore-tmp")      # not feed_gta's .scout-tmp: the two never share a temp file
        try:
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
            tmp.replace(p)
        except OSError:                               # the iCloud Desktop said no: a label, never the hourly board
            try:
                tmp.unlink()
            except OSError:
                pass
            return []
    return moved


def channel_people(acct: C.Account) -> Dict[str, str]:
    """scout.json youtube_channels "person", by the channel's slug and by its name (lowercase): Live Speedy ->
    iShowSpeed. A find saved on a board before the field existed carries no person, so feed_gta looks it up here."""
    try:
        chans = load(acct.slug).get("youtube_channels", [])
    except (OSError, ValueError, SystemExit):
        return {}
    out: Dict[str, str] = {}
    for ch in chans:
        if ch.get("person"):
            out[str(ch.get("slug") or "")] = ch["person"]
            out[str(ch.get("name") or "").lower()] = ch["person"]
    out.pop("", None)
    return out


def claimed(cfg: Optional[C.Config], acct: C.Account) -> Dict[str, str]:
    """video id -> the other GTA account whose inbox has it fed or cut (2026-10-19: one source video, one GTA account;
    the rosters are kept apart by niche and this is the backstop). A row the maker cut (status cut) is that account's;
    so is one the scout fed (found_by scout, still new) while that account is switched on: a switched-off account cuts
    nothing, so a fed row left in its inbox (from before a switch-off, or written by hand) never keeps a video from the
    accounts that work. A killed row is free again. Nothing without cfg."""
    if cfg is None:
        return {}
    out: Dict[str, str] = {}
    for other in (cfg.all_accounts or cfg.accounts).values():
        if other.maker != "gta" or other.slug == acct.slug or other.account_dir == acct.account_dir:
            continue
        try:
            items = json.loads((other.account_dir / "Publish" / "inbox.json").read_text()).get("items")
        except (OSError, ValueError, AttributeError):
            continue
        for vid, row in (items.items() if isinstance(items, dict) else []):
            if isinstance(row, dict) and (row.get("status") == "cut" or (
                    other.active and row.get("found_by") == "scout" and row.get("status") == "new")):
                out.setdefault(str(vid), other.name)
    return out


def row_kind(acct: C.Account, f: dict) -> str:
    """The inbox kind feed_gta writes: the roster's kind for a roster upload, else makers.found_kind ("scout" on GTA
    Gaming; the first playing kind on an account with gameplay slots only, whose gameplay lane cuts nothing else)."""
    from . import makers as M       # lazy, as in refresh_fed (makers reads the scout lazily too)
    return f.get("roster_kind") or M.found_kind(acct)


def feed_gta(acct: C.Account, finds: List[dict], n: int, force: bool = False, cfg: Optional[C.Config] = None,
             said: Optional[List[str]] = None) -> List[str]:
    """Put the best finds into Publish/inbox.json in watch.py's own row shape, scored above everything watch.py ranks
    (its scores run about 0 to 5), so tonight's maker cuts them first. The lift lasts 30 hours (`scout_until`), after
    which makers.gta_sources reads the row at its plain score again. A new account with no inbox yet gets one
    ({"items": {}}). With cfg, a video another GTA account has fed or cut is never fed here (claimed). `said` gets a
    line for each of those."""
    said = said if said is not None else []
    p = acct.account_dir / "Publish" / "inbox.json"
    if not p.exists() and acct.account_dir.is_dir():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.scout-tmp")
        tmp.write_text(json.dumps({"items": {}}, indent=1) + "\n")
        tmp.replace(p)
        said.append(f"made {p} (there was no inbox yet)")
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        said.append(f"{acct.name}: inbox not read ({type(e).__name__}: {p}); nothing fed")
        return []
    items = data.setdefault("items", {})
    if not isinstance(items, dict):
        return []
    theirs = claimed(cfg, acct)
    roster_min = {r["slug"]: r.get("min_minutes", 5) for r in roster(acct)}
    faces = channel_people(acct)
    fed: List[str] = []
    now = datetime.now(timezone.utc)
    for f in sorted(finds, key=lambda f: -f["score"]):
        if len(fed) >= n:
            break
        if f["kind"] not in ("video", "clip") or f["id"] in items:
            continue
        if f["id"] in theirs:            # one source video, one GTA account
            said.append(f"{f['id']}: not fed, {theirs[f['id']]} has it (one source video, one GTA account)")
            continue
        mins = f.get("min_minutes") or roster_min.get(f.get("channel_slug"), 5)  # the source's own minimum first
        long_enough = (f.get("duration") or 0) >= 60 * float(mins) or f.get("was_live") or f["kind"] == "clip"
        if not force and (f.get("short") or not long_enough):
            continue
        slug = f.get("channel_slug") or re.sub(r"[^a-z0-9]+", "", f["channel"].lower())[:24] or "creator"
        # the row's name is the FACE (2026-10-17): 'Live Speedy' as the host missed the cam registry (ishowspeed) and put
        # the channel's name in every title; scout.json youtube_channels "person" says who it is
        who = f.get("person") or faces.get(f.get("channel_slug") or "") or faces.get((f.get("channel") or "").lower())
        items[f["id"]] = {
            "channel": slug, "name": who or f["channel"], "platform": "youtube", "kind": row_kind(acct, f),
            "title": f["title"],
            "url": f["url"], "duration": f.get("duration"), "views": f.get("views"), "was_live": bool(f.get("was_live")),
            "uploaded": (f.get("published") or "").replace("+00:00", "Z") or None,
            "seen": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "new",
            "score": round(10.0 + f["score"], 3), "base_score": round(min(f["score"], 5.0), 3),
            "scout_until": (now + timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), "found_by": "scout"}
        if f["kind"] == "clip":          # a stream clip (Best of GTA): the night takes the stream around it
            items[f["id"]].update({k: f[k] for k in ("platform", "vod_url", "vod_offset", "followers",
                                                     "stream_title", "game") if f.get(k) is not None})
            items[f["id"]]["clip"] = True
        fed.append(f["id"])
    if fed:
        tmp = p.with_suffix(".json.scout-tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(p)
    return fed
