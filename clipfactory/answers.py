"""Natural answers: what a reply may say about a clip, read only from what is on disk, never invented.

People under a clip ask the same few things: where is this from, is there a full video, who is that, what hearing is
this, when does GTA 6 come out, is there a part 2. The automations (rules.py) and the Inbox's Suggest button answer
those with the clip's own facts, and with nothing else:

    GTA        the board slot's host, source_title and source_url (with &t= at the clip's own second, pick_span),
               else the job's project.json; days to GTA 6 from the account's kit.json launch.date
    Politics     the job's project.json (source_title, source_url, source_date, host or source_channel); the hearing
               from cast.json hearing{body, date}; the speaker ONLY from a cast a person verified (bench.politics_cast):
               a one-person cast, or the clip's quote inside that person's name card (clips/NN-<slug>.cards.json)
    both       "in the first comment" only when the post's first comment went (its marker on the board) AND its
               words hold a link (Instagram's first comment carries none: there it is never said)

A fact that is not there is simply missing: a text that needs it is never sent (the automation is skipped with
"no_fact"). The Politics stays neutral: source facts only (who, which hearing, when, where the full video is), never
an opinion or a reaction to the politics.

    facts(cfg, acct, post)            {name: words} for the post's clip ({} when the clip cannot be found)
    render(template, facts)           the words with every {name} filled, or None when a fact is missing
    resolve(cfg, acct, item, intent)  a plain public answer to one intent, or None
    suggest(cfg, acct, item)          {text, why} for the Inbox's Suggest, or None
    source_key(cfg, acct, slot, day)  what a Part 2 waits on: the GTA source video, the Politics job

Read only: no network, no writes.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import config as C

# engagement bait: what Facebook demotes and what reads like a bot. Refused at save, in any text: a
# rule's texts (rules.py) and a first comment (firstcomment.py) both read this one list.
BAIT: Tuple[Tuple[str, str], ...] = (
    (r"\bcomment\b[^.?]{0,40}\b(to|and|for|if)\b[^.?]{0,30}\b(get|receive|win|unlock|see|grab)\b",
     'asks people to comment to get something ("comment X to get Y")'),
    (r"\b(type|drop|write|reply with|comment|say)\s+[\"'“‘]?[a-z0-9#]{1,20}[\"'”’]?\s+"
     r"(below|for|to get|and i|and we)\b", "asks people to type a keyword"),
    (r"\btag\b", "asks people to tag someone"),
    (r"\bmention (a|your|two|some|a few)\b", "asks people to mention someone"),
    (r"\bcomment (below|down below|if|your|a|what|yes|1)\b", "asks for comments"),
    (r"\b(let me|let us|lmk)\b[^.?]{0,20}\bin the comments\b", "asks for comments"),
    (r"\blike (and|&|or) (share|follow|subscribe|comment)\b", "asks for likes and shares"),
    (r"\b(share|repost) (this|it|with)\b", "asks for shares"),
    (r"\b(follow|subscribe)\b", "asks people to follow"),
    (r"\b(smash|hit) (that|the) (like|follow)\b", "asks for likes"),
    (r"\blink in (the |my |our )?bio\b", "points to a link in bio"),
    (r"\b(dm|message) (me|us)\b", "asks people to message"),
    (r"\bkeyword\b", "asks for a keyword"),
    (r"\bgiveaway\b|\bfree (gift|money|v-?bucks|robux)\b", "a giveaway"),
)

def bait(text: str) -> str:
    """Why the words are engagement bait ('' when they are not)."""
    low = str(text or "").lower()
    for rx, why in BAIT:
        if re.search(rx, low):
            return why
    return ""


# ------------------------------------------------------------------------------------------------------ the words
# every {name} a rule's text may use, and what it says
PLACEHOLDERS: Dict[str, str] = {
    "mention": "@ and the person's name",
    "host": "who published the source (the streamer, the network)",
    "source_title": "the source video's title",
    "source_url": "the full video's link (messages only)",
    "date": "the day the source aired",
    "kind": "video, stream, hearing, interview ...",
    "speaker": "the person speaking (Politics, only from a verified cast)",
    "speaker_title": "their title (Politics, only from a verified cast)",
    "hearing": "the hearing's committee (Politics, from cast.json)",
    "hearing_date": "the hearing's day (Politics, from cast.json)",
    "gta6_date": "GTA 6's release day (GTA, from kit.json)",
    "days_to_gta6": "how long until GTA 6 comes out (GTA, from kit.json)",
    "first_comment": '"in the first comment" (only once the first comment went)',
    "title": "the post's own title",
    "part2_url": "the next clip's link (the Part 2 message only)",
}
FACT_NAMES = tuple(k for k in PLACEHOLDERS if k != "mention")
URL_FACTS = ("source_url", "part2_url")
_PH = re.compile(r"\{([a-z0-9_]+)\}")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
KIND_WORDS = {"hearing": "hearing", "interview": "interview", "sitdown": "interview", "gaggle": "press gaggle",
              "briefing": "briefing", "speech": "speech", "floor": "floor speech", "address": "address",
              "town hall": "town hall", "debate": "debate", "podcast": "podcast", "rally": "rally",
              "press conference": "press conference", "presser": "press conference"}
INTENT_ORDER = ("asks_source", "asks_full", "asks_who", "asks_which", "asks_when", "asks_part2")
INTENT_WORDS = {"asks_source": "where it is from", "asks_full": "the full video", "asks_who": "who that is",
                "asks_which": "which hearing or show", "asks_when": "when", "asks_part2": "a part 2",
                "question": "any question"}

# the Inbox's Suggest: one plain public answer per intent and maker, the first that every fact fills
DEFAULTS: Dict[str, Dict[str, List[str]]] = {
    "gta": {
        "asks_source": ["{mention} this is {host}, from {source_title}.", "{mention} this is from {host}."],
        "asks_full": ["{mention} the full video is from {host}: {source_title}. The link is {first_comment}.",
                      "{mention} the full video is from {host}: {source_title}."],
        "asks_who": ["{mention} that is {host}, from {source_title}.", "{mention} that is {host}."],
        "asks_which": ["{mention} this is {host}, from {source_title}."],
        "asks_when": ["{mention} GTA 6 comes out {gta6_date}, {days_to_gta6} from now."],
        "asks_part2": ["{mention} thanks for asking. If there is a part 2 from {host}, it will be on this page."],
    },
    "politics": {
        "asks_source": ["{mention} this is from {host}, {date}: {source_title}.",
                        "{mention} this is from {source_title}, {date}."],
        "asks_full": ["{mention} the full {kind} is {source_title} ({host}, {date}). The link is {first_comment}.",
                      "{mention} the full {kind} is {source_title} ({host}, {date})."],
        "asks_who": ["{mention} that is {speaker}, {speaker_title}.", "{mention} that is {speaker}."],
        "asks_which": ["{mention} this is from {hearing}, {hearing_date}.",
                       "{mention} this is from {source_title}, {date}."],
        "asks_when": ["{mention} this was on {date}, {host}.", "{mention} this was on {hearing_date}."],
        "asks_part2": ["{mention} thanks for asking. If there is more of this {kind}, it will be on this page."],
    },
}


# ------------------------------------------------------------------------------------------------------ small helpers
def _read(p: Path) -> dict:
    try:
        d = json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _s(v) -> str:
    return " ".join(str(v or "").split()) if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def day_words(v) -> str:
    """"October 18, 2026" for an ISO day (or "10.18.26" / "10/18/2026"); the words as they are when they are a
    short date written some other way; '' when there is nothing."""
    s = _s(v)
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{2,4})$", s)
        if not m:
            return s if len(s) <= 40 and re.search(r"\d", s) else ""
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
    try:
        date(y, mo, d)
    except ValueError:
        return ""
    return f"{MONTHS[mo - 1]} {d}, {y}"


_URL = re.compile(r"(https?://|www\.)\S+", re.I)


def clean_title(v) -> str:
    """A source title as a reply may say it: no hashtags (#gta6 #shorts), no long dashes, no trailing shouting
    marks. The words are otherwise kept as the source wrote them."""
    s = _s(v)
    if not s:
        return ""
    s = re.sub(r"(^|\s)#[\w]+", " ", s)
    s = re.sub(r"\s*[\u2014\u2013]+\s*", ", ", s)
    s = re.sub(r"[!?]{2,}", lambda m: m.group(0)[0], s)
    s = " ".join(s.split()).strip(" ,;:|")
    return s


def _with_t(url: str, seconds) -> str:
    """The link set to start at the clip's own second (YouTube's t=)."""
    try:
        t = int(float(seconds))
    except (TypeError, ValueError):
        return url
    if t <= 0 or not url:
        return url
    base = re.sub(r"([?&])t=\d+s?(&|$)", lambda m: m.group(1) if m.group(2) else "", url).rstrip("?&")
    return f"{base}{'&' if '?' in base else '?'}t={t}s"


def video_key(url: str) -> str:
    """A source video's own id (YouTube's 11 characters), else the link without its time: what a Part 2 waits on."""
    u = str(url or "")
    m = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})", u)
    if m:
        return "yt:" + m.group(1)
    return re.sub(r"([?&])t=\d+s?", "", u).rstrip("?&")


def _kit(acct) -> dict:
    """The account's brand kit (gaming's assets/brand/<kit>/kit.json)."""
    skill = getattr(acct, "skill_dir", None)
    if not skill:
        return {}
    names = [str(getattr(acct, "kit", "") or ""), "gta"]
    for n in [x for x in names if x]:
        d = _read(Path(skill) / "assets" / "brand" / n / "kit.json")
        if d:
            return d
    return {}


def _today(cfg: C.Config, now: Optional[datetime] = None) -> date:
    t = now or cfg.now()
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(cfg.tz()).date()


# ------------------------------------------------------------------------------------------------------ the clip
def _slot_of(acct, post: dict) -> Tuple[str, str, dict]:
    """(board day, slot key, slot) for a post: its own board and slot, else the Inbox's posts index."""
    board, slot = str(post.get("board") or ""), str(post.get("slot") or "")
    if not (board and slot):
        try:
            from . import socialstore as SS
            ent = SS.posts_index(acct).get(str(post.get("platform_id") or "")) or {}
            board, slot = str(ent.get("day") or board), str(ent.get("slot") or slot)
        except Exception:  # noqa: BLE001  (no index: the post is simply not known)
            pass
    if not (board and slot):
        return "", "", {}
    try:
        bd = B.read(acct, date.fromisoformat(board[:10])) or {}
    except (OSError, ValueError):
        return board, slot, {}
    s = (bd.get("slots") or {}).get(slot)
    return board, slot, (s if isinstance(s, dict) else {})


def _job(acct, slot: dict, day: str) -> str:
    try:
        from . import bench as BN
        return BN._job_of(acct, slot, BN._sched(acct), day)
    except Exception:  # noqa: BLE001
        return ""


def _srt_cues(p: Optional[Path]) -> List[Tuple[float, float, str]]:
    if p is None or not p.is_file():
        return []
    out: List[Tuple[float, float, str]] = []
    try:
        blocks = re.split(r"\n\s*\n", p.read_text(errors="replace"))
    except OSError:
        return []

    def secs(x: str) -> float:
        h, m, s = x.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    for b in blocks:
        lines = [ln.strip() for ln in b.strip().splitlines() if ln.strip()]
        arrow = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if arrow is None:
            continue
        try:
            a, z = (secs(x.strip().split()[0]) for x in lines[arrow].split("-->"))
        except (ValueError, IndexError):
            continue
        out.append((a, z, " ".join(lines[arrow + 1:])))
    return out


def _words(s: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", str(s or "").lower())


def quote_time(srt: Optional[Path], quote: str) -> Optional[float]:
    """When the clip's quote starts on the clip's own clock: its first words found in a row in the delivered srt
    (each cue's words spread over the cue). None when they are not there."""
    want = _words(quote)[:5]
    if len(want) < 2:
        return None
    seq: List[Tuple[str, float]] = []
    for a, z, text in _srt_cues(srt):
        ws = _words(text)
        for i, w in enumerate(ws):
            seq.append((w, a + (z - a) * i / max(1, len(ws))))
    words = [w for w, _ in seq]
    for i in range(len(words) - len(want) + 1):
        if words[i:i + len(want)] == want:
            return seq[i][1]
    return None


def _verified(acct, job: str, who: str) -> bool:
    """Did a person (never a bot) verify this one member of the cast? (bench.politics_cast on that person alone)"""
    try:
        from . import bench as BN
        return BN.politics_cast(acct, job, {"cards": [{"who": who}]}).get("status") == "verified"
    except Exception:  # noqa: BLE001
        return False


def _speaker(acct, job: str, slot: dict, clip: dict) -> Tuple[str, str, str]:
    """(name, title, how) of the person speaking, only from a cast a person verified: the whole cast is one person,
    or the clip's quote falls inside that person's name card. ('', '', why not) otherwise."""
    cf = Path(acct.account_dir) / "Jobs" / job / "cast.json"
    people = [p for p in _read(cf).get("people") or [] if isinstance(p, dict)]
    by_id = {str(p.get("id") or "").lower(): p for p in people}
    if not people:
        return "", "", "no cast for this clip"
    if len(people) == 1:
        p = people[0]
        if _verified(acct, job, str(p.get("id") or "")):
            return _s(p.get("name")), _s(p.get("title")), "a one-person cast a person verified"
        return "", "", "the cast is not verified by a person"
    slug = str(slot.get("slug") or "")
    quote = _s((clip or {}).get("quote"))
    clip_path = (slot.get("clip") or {}).get("path") if isinstance(slot.get("clip"), dict) else None
    srt = Path(str(clip_path)).with_suffix(".srt") if clip_path else None
    at = quote_time(srt, quote) if quote else None
    if at is None:
        return "", "", "more than one person in the cast and the quote is not placed"
    cards: List[dict] = []
    for f in sorted((Path(acct.account_dir) / "Jobs" / job / "clips").glob(f"*{slug}.cards.json")) if slug else []:
        cards += [c for c in _read(f).get("cards") or [] if isinstance(c, dict)]
    for c in cards:
        shot = c.get("shot")
        try:
            t0, t1 = float(shot[0]), float(shot[1])
        except (TypeError, ValueError, IndexError):
            continue
        who = str(c.get("who") or "").lower()
        if t0 <= at <= t1 and who in by_id:
            if not _verified(acct, job, who):
                return "", "", "the quote is inside a name card a person has not verified"
            p = by_id[who]
            return _s(p.get("name")), _s(p.get("title")), "the quote inside a verified person's name card"
    return "", "", "the quote is not inside anyone's name card"


def facts(cfg: C.Config, acct, post: dict, now: Optional[datetime] = None) -> Dict[str, str]:
    """What may be said about the post's clip, each fact only when it is on disk ({} when the clip is not found).
    "_from" says where each fact came from (for the page and the Inbox's Suggest); "_why" what is missing."""
    post = post if isinstance(post, dict) else {}
    out: Dict[str, str] = {}
    src: Dict[str, str] = {}
    gaps: Dict[str, str] = {}
    board, slot_key, slot = _slot_of(acct, post)
    title = _s(post.get("title"))
    if title:
        out["title"], src["title"] = title, "the post"
    maker = str(getattr(acct, "maker", "") or "")
    if maker == "gta":
        kit = _kit(acct)
        launch = str(((kit.get("launch") or {}) if isinstance(kit.get("launch"), dict) else {}).get("date") or "")
        try:
            ld = date.fromisoformat(launch[:10])
            days = (ld - _today(cfg, now)).days
            if days > 0:
                out["gta6_date"] = day_words(launch)
                out["days_to_gta6"] = f"{days} day{'s' if days != 1 else ''}"
                src["gta6_date"] = src["days_to_gta6"] = "kit.json launch.date"
            else:
                gaps["days_to_gta6"] = "GTA 6 is out (kit.json launch.date has passed)"
        except ValueError:
            gaps["gta6_date"] = "no launch date in the account's kit.json"
    if not slot:
        gaps["clip"] = "the post's clip is not on a board here"
        out["_from"] = json.dumps(src)
        out["_why"] = json.dumps(gaps)
        return out
    outlet = str(post.get("outlet") or "")
    fc = (((slot.get("outlets") or {}).get(outlet) or {}).get("social") or {}).get("first_comment") \
        if outlet else None
    if isinstance(fc, dict) and fc.get("state") == "done":
        words = ""
        try:
            from . import extras as EX
            words = EX.first_comment(slot, outlet)
        except Exception:  # noqa: BLE001
            words = ""
        if _URL.search(words or ""):
            out["first_comment"], src["first_comment"] = "in the first comment", "the post's first comment"
        else:
            gaps["first_comment"] = "the first comment there has no link"
    job = _job(acct, slot, board)
    if maker == "gta":
        url, stitle, host = _s(slot.get("source_url")), clean_title(slot.get("source_title")), _s(slot.get("host"))
        start = (slot.get("pick_span") or [None])[0] if isinstance(slot.get("pick_span"), (list, tuple)) else None
        where = "the board"
        if job and not (url and stitle):
            try:
                from . import bench as BN
                clip, proj = BN._gta_slate(acct, job, str(slot.get("slug") or ""))
            except Exception:  # noqa: BLE001
                clip, proj = {}, {}
            url = url or _s(proj.get("source_url"))
            stitle = stitle or clean_title(proj.get("source_title"))
            host = host or _s((proj.get("host") or {}).get("name") if isinstance(proj.get("host"), dict) else "")
            if start is None and clip.get("start") is not None:
                start = clip.get("start")
            where = "the job's project.json"
        if host:
            out["host"], src["host"] = host, "the board"
        if stitle:
            out["source_title"], src["source_title"] = stitle, where
        if url:
            out["source_url"], src["source_url"] = _with_t(url, start), where + (" and pick_span" if start else "")
        kind = "stream" if re.search(r"\b(stream|live|vod)\b", (stitle or "").lower()) else "video"
        out["kind"], src["kind"] = kind, "the source title"
        for k in ("host", "source_title", "source_url"):
            if k not in out:
                gaps[k] = "not on the board or in the job"
    elif maker == "politics":
        clip, proj = {}, {}
        if job:
            try:
                from . import bench as BN
                clip, proj = BN._politics_slate(acct, job, str(slot.get("slug") or ""))
            except Exception:  # noqa: BLE001
                clip, proj = {}, {}
        pj = f"Jobs/{job}/project.json" if job else "project.json"
        host = _s(slot.get("host")) or _s(proj.get("source_channel"))
        if host:
            out["host"], src["host"] = host, "the board" if _s(slot.get("host")) else pj
        for k in ("source_title", "source_url"):
            v = _s(proj.get(k)) or (_s((slot.get("cast_check") or {}).get("source_title"))
                                     if k == "source_title" and isinstance(slot.get("cast_check"), dict) else "")
            if k == "source_title":
                v = clean_title(v)
            if v:
                out[k], src[k] = v, pj
        d = day_words(proj.get("source_date"))
        if d:
            out["date"], src["date"] = d, pj
        fmt = str(clip.get("format") or proj.get("format") or "").lower()
        kind = next((w for k, w in KIND_WORDS.items() if k in fmt), "") or "video"
        out["kind"], src["kind"] = kind, "the slate's format" if fmt else "the default"
        cast = _read(Path(acct.account_dir) / "Jobs" / job / "cast.json") if job else {}
        hearing = cast.get("hearing") if isinstance(cast.get("hearing"), dict) else {}
        if _s(hearing.get("body")):
            out["hearing"], src["hearing"] = _s(hearing.get("body")), f"Jobs/{job}/cast.json"
        hd = day_words(hearing.get("date"))
        if hd:
            out["hearing_date"], src["hearing_date"] = hd, f"Jobs/{job}/cast.json"
        if job:
            name, ttl, how = _speaker(acct, job, slot, clip)
            if name:
                out["speaker"], src["speaker"] = name, how
                if ttl:
                    out["speaker_title"], src["speaker_title"] = ttl, how
            else:
                gaps["speaker"] = how
        else:
            gaps["job"] = "no job for this clip (cast_check, the schedule)"
        for k in ("host", "source_title", "source_url", "date"):
            if k not in out:
                gaps.setdefault(k, f"not in {pj}")
    out["_from"] = json.dumps(src)
    out["_why"] = json.dumps(gaps)
    return out


def source_key(cfg: C.Config, acct, slot: dict, day: str = "") -> str:
    """What a Part 2 waits on: the GTA source video (its id), the Politics job. '' when nothing says."""
    if not isinstance(slot, dict):
        return ""
    if str(getattr(acct, "maker", "") or "") == "gta":
        url = _s(slot.get("source_url"))
        if not url:
            job = _job(acct, slot, day)
            if job:
                try:
                    from . import bench as BN
                    url = _s(BN._gta_slate(acct, job, str(slot.get("slug") or ""))[1].get("source_url"))
                except Exception:  # noqa: BLE001
                    url = ""
        return video_key(url) if url else ""
    job = _job(acct, slot, day)
    return f"job:{job}" if job else ""


def post_source_key(cfg: C.Config, acct, post: dict) -> str:
    board, _, slot = _slot_of(acct, post if isinstance(post, dict) else {})
    return source_key(cfg, acct, slot, board) if slot else ""


# ------------------------------------------------------------------------------------------------------ the words
def placeholders(template: str) -> List[str]:
    return _PH.findall(str(template or ""))


def render(template: str, fx: Dict[str, str]) -> Optional[str]:
    """The template with every {name} filled from the facts; None when one of them is missing (or not a name a
    text may use). The words are tidied (no double spaces)."""
    missing = False

    def fill(m) -> str:
        nonlocal missing
        k = m.group(1)
        v = fx.get(k) if not k.startswith("_") else None
        if not isinstance(v, str) or not v.strip():
            missing = True
            return ""
        return v.strip()
    out = _PH.sub(fill, str(template or ""))
    if missing:
        return None
    return " ".join(out.split())


def mention(item: dict) -> str:
    a = item.get("author") if isinstance(item.get("author"), dict) else {}
    name = _s(a.get("name")).lstrip("@")
    return "@" + name if name else ""


def item_facts(cfg: C.Config, acct, item: dict, now: Optional[datetime] = None,
               cache: Optional[dict] = None) -> Dict[str, str]:
    """facts() for the item's post plus {mention}, with an optional cache per post for a whole poll."""
    post = item.get("post") if isinstance(item.get("post"), dict) else {}
    key = (getattr(acct, "slug", acct), str(post.get("platform_id") or ""), str(post.get("board") or ""),
           str(post.get("slot") or ""), str(post.get("outlet") or ""))
    if cache is not None and key in cache:
        fx = dict(cache[key])
    else:
        fx = facts(cfg, acct, post, now)
        if cache is not None:
            cache[key] = dict(fx)
    m = mention(item)
    if m:
        fx["mention"] = m
    return fx


def resolve(cfg: C.Config, acct, item: dict, intent: str, now: Optional[datetime] = None) -> Optional[str]:
    """A plain public answer to one intent from the item's clip (no link: a public reply never carries one), or None
    when a fact it needs is not on disk."""
    maker = str(getattr(acct, "maker", "") or "")
    temps = (DEFAULTS.get(maker) or {}).get(str(intent or ""))
    if not temps:
        return None
    fx = item_facts(cfg, acct, item if isinstance(item, dict) else {}, now)
    for t in temps:
        got = render(t, fx)
        if got:
            return got
    return None


def _tags(item: dict, maker: str) -> List[str]:
    tags = item.get("tags")
    if isinstance(tags, list) and tags:
        return [str(t) for t in tags]
    try:
        from . import rules as RU
        return RU.tags_for(str(item.get("text") or ""), maker)
    except Exception:  # noqa: BLE001
        return []


def suggest(cfg: C.Config, acct, item: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """{text, why} for the Inbox's Suggest: an answer to the first thing the comment asks that the clip's own facts
    can answer. None when it asks nothing answerable, or a fact is missing."""
    if not isinstance(item, dict):
        return None
    maker = str(getattr(acct, "maker", "") or "")
    tags = _tags(item, maker)
    fx = item_facts(cfg, acct, item, now)
    try:
        src = json.loads(fx.get("_from") or "{}")
    except ValueError:
        src = {}
    for intent in INTENT_ORDER:
        if intent not in tags:
            continue
        for t in (DEFAULTS.get(maker) or {}).get(intent) or []:
            got = render(t, fx)
            if got:
                used = [k for k in placeholders(t) if k != "mention"]
                where = sorted({src.get(k, "") for k in used if src.get(k)})
                why = f"answers {INTENT_WORDS.get(intent, intent)}" + (
                    f", from {', '.join(where)}" if where else "")
                return {"text": got, "why": why, "intent": intent}
    return None
