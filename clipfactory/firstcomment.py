"""The first comment under every post: written at collect, signed with the clip, posted once the post is live.

    collect    on_collect writes a default per platform whose switch is not off, from the clip's real source:
               Gaming       Facebook, YouTube: "Full video from {host}: {source_url}&t=<start>s"
                               Instagram:        "From {host}'s stream on YouTube." (no link: Instagram shows none)
               Politics  Facebook, YouTube: "{source_title}, {host}, {date}. Full {kind}: {source_url}"
                               Instagram:        "{source_title}, {host}, {date}."
               No source known: no default. Never a hashtag, an ask to comment, the caption's question or a dash.
    the card   the First comment fold: a box per platform while the clip waits for Approve (Save, Clear); after
               Approve the words and how they went (off, practice, waits, posted, skipped).
    Approve    signs the extras file with the clip (board.content_hash): an edit after that stops the clip.
    the post   on_posted (the poster's hook) queues the comment through outward a little after the post is live:
               Facebook and Instagram 90 to 300 seconds after published_at (only posts the factory made itself,
               provider "direct"), YouTube 10 to 20 minutes after it is public. A marker on the outlet record
               (outlets[p].social.first_comment) says where it is; sweep (the 5-minute tick) catches a miss.
    sending    outward checks the switch, the permission, the caps and Stop; still_signed checks the clip is still
               signed as it was, with these words; execute makes the one call; find looks for our own comment when
               the answer was lost, so it never goes twice.

Switches (per account, all off): first_comment.facebook_reels, first_comment.instagram_reels (needs
instagram_manage_comments), first_comment.youtube_shorts (needs youtube.force-ssl: connect youtube --with comments).

    factory.py extras status gaming
    factory.py extras comment gaming --date 2026-10-20 --slot 07:00 --platform facebook --text-file words.txt
    factory.py extras poll gaming --date 2026-10-20 --slot 07:00 --options "Yes|No"
    factory.py extras clear gaming --date 2026-10-20 --slot 07:00 --platform instagram
"""
from __future__ import annotations

import argparse
import hashlib
import html
import random
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from . import answers as AN
from . import board as B
from . import config as C
from . import extras as EX
from . import reelextras as RX
from . import switches as SW

OUTLETS = ("facebook_reels", "instagram_reels", "youtube_shorts")
PLAT = {"facebook_reels": "facebook", "instagram_reels": "instagram", "youtube_shorts": "youtube"}
NAMES = {"facebook_reels": "Facebook", "instagram_reels": "Instagram", "youtube_shorts": "YouTube"}
OUTLET_OF = {"facebook": "facebook_reels", "instagram": "instagram_reels", "youtube": "youtube_shorts",
             **{o: o for o in OUTLETS}}
KEY = "first_comment."
LIMIT = {"facebook_reels": 2200, "instagram_reels": 2200, "youtube_shorts": 10000}
LIVE_FOR = timedelta(hours=20)             # a first comment goes within 20 hours of the post going live, or never
YT_PUBLIC_WAIT = timedelta(hours=2)        # YouTube not public 2 hours after its time: no first comment
PUBLIC_LOOKS = (timedelta(minutes=20), timedelta(minutes=40))   # not public yet: look again after these (then the 2h)
NOT_PUBLIC = "not public yet:"             # the skip's words when YouTube has not made the Short public: looked again
URLS = re.compile(r"(?:https?://|www\.)\S+", re.I)
UNSUPPORTED = re.compile(r"unsupported (get|post) request", re.I)
FIND_LIMIT = 100
RNG = random.Random()                      # the delay after the post went live; tests seed it
DASHES = ("\u2014", "\u2013")
HASHTAG = re.compile(r"(?<![\w&/#])#[A-Za-z]\w*")
ASKS = re.compile(r"\b(comment|reply)\s+(below|down|with)\b|\bin the comments\b|\bdrop an?\b[^.?!]{0,24}\bcomments?\b"
                  r"|\blet (me|us) know\b|\btag (a|your|someone)\b|\b(like|follow) and (follow|subscribe|share)\b",
                  re.I)
POLITICS_KIND = {"hearing": "hearing", "markup": "hearing", "testimony": "hearing", "interview": "interview",
               "sitdown": "interview", "podcast": "interview", "sunday": "interview", "briefing": "briefing",
               "presser": "briefing", "press": "briefing", "gaggle": "briefing", "tape": "recording",
               "argument": "recording"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
FINAL = ("done", "practice", "skipped", "refused", "gave_up")
OPS = ("extra-set", "extra-clear", "extra-poll")


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _safe(spec: str, neutral=None):
    """A hook the factory calls (collect, the poster, the page, the doctor) never raises, not even a refusal: what
    went wrong goes to state/logs/social-errors.log once and the neutral answer comes back."""
    def wrap(f):
        def run(*a, **kw):
            try:
                return f(*a, **kw)
            except (Exception, SystemExit) as e:  # noqa: BLE001  (a first comment never stops a clip or a page)
                try:
                    from . import registry as REG
                    REG._log_error(spec, e)
                except Exception:  # noqa: BLE001
                    pass
                return neutral() if callable(neutral) else neutral
        run.__name__, run.__doc__, run.__wrapped__ = f.__name__, f.__doc__, f
        return run
    return wrap


def _sha(text: str) -> str:
    return hashlib.sha256(str(text or "").encode()).hexdigest()


def _utc(now: Optional[datetime] = None) -> datetime:
    t = now or datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return _utc(t).replace(microsecond=0).isoformat()


def _when(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _is_client(acct) -> bool:
    raw = getattr(acct, "raw", None)
    return isinstance(raw, dict) and str(raw.get("owner") or "self") == "client"


def outlets(acct) -> List[str]:
    """The platforms this account's first comments can go to: Facebook, Instagram and YouTube where it posts through
    the API (a post made by hand has no record to comment under)."""
    from . import outlets as O
    plats = getattr(acct, "platforms", None) or []
    return [o for o in OUTLETS if o in plats and O.mode(acct, o) == "api"]


def outlet_of(name: str) -> str:
    o = OUTLET_OF.get(str(name or "").strip().lower())
    if not o:
        raise SystemExit(f"{name}: the platform is facebook, instagram or youtube")
    return o


def mode(acct, outlet: str) -> str:
    return SW.mode(acct, KEY + outlet)


# ------------------------------------------------------------------------------------------------------ the source
def _plain(s: str) -> str:
    """A source title fit for a comment: no long dash or spaced hyphen (a comma instead), no hashtag, one line."""
    s = " ".join(str(s or "").split())
    s = re.sub(r"\s*[\u2014\u2013]\s*|\s+-\s+", ", ", s)
    s = HASHTAG.sub("", s)
    s = re.sub(r"\s*,\s*(,\s*)+", ", ", s)
    s = " ".join(s.split()).strip(" ,;:|")
    if len(s) > 150:
        s = s[:150].rsplit(" ", 1)[0].rstrip(" ,;:")
    return s


def _date_words(v) -> str:
    """'OCTOBER 19 2026', '2026-10-19', 'Oct. 19, 2026' as 'October 19, 2026' ('' when it does not read)."""
    s = " ".join(str(v or "").replace(",", " ").replace(".", " ").split())
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^([A-Za-z]+) (\d{1,2}) (\d{4})$", s)
        if not m:
            return ""
        name = m.group(1).lower()
        hit = [i for i, n in enumerate(MONTHS) if n.lower().startswith(name[:3]) and len(name) >= 3]
        if not hit:
            return ""
        mo, d, y = hit[0] + 1, int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{MONTHS[mo - 1]} {d}, {y}"


def _with_t(url: str, secs) -> str:
    """The source link at the clip's moment: YouTube's &t=<n>s (youtu.be and watch links); others unchanged."""
    try:
        t = int(float(secs))
    except (TypeError, ValueError):
        return url
    if t <= 0:
        return url
    u = urlparse(url)
    host = u.netloc.lower()
    if not (host.endswith("youtube.com") or host == "youtu.be"):
        return url
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k != "t"] + [("t", f"{t}s")]
    return urlunparse(u._replace(query=urlencode(q, safe=":/")))


def _where(url: str) -> str:
    host = urlparse(str(url or "")).netloc.lower()
    if host.endswith("youtube.com") or host == "youtu.be":
        return "YouTube"
    if host.endswith("twitch.tv"):
        return "Twitch"
    if host.endswith("kick.com"):
        return "Kick"
    return ""


def _gta_source(acct, slot: dict) -> dict:
    from . import bench as BN
    host = str(slot.get("host") or "").strip()
    url = str(slot.get("source_url") or "").strip()
    span = slot.get("pick_span")
    start = span[0] if isinstance(span, (list, tuple)) and span else None
    urls = [u for u in slot.get("source_urls") or [] if u]
    if (not url or not host) and slot.get("job") and slot.get("slug"):
        try:
            entry, pj = BN._gta_slate(acct, str(slot["job"]), str(slot["slug"]))
        except Exception:  # noqa: BLE001  (a missing job folder: the slot's own fields are all there is)
            entry, pj = {}, {}
        url = url or str(pj.get("source_url") or "")
        hp = pj.get("host") if isinstance(pj.get("host"), dict) else {}
        host = host or str(hp.get("name") or pj.get("source_channel") or "")
        if start is None and isinstance(entry.get("start"), (int, float)):
            start = entry["start"]
    many = len(urls) > 1 or slot.get("format") == "reactions" or host.count(",") >= 1
    return {"host": _plain(host), "url": url, "t": start, "many": many, "title": "", "date": "", "kind": "video"}


def _politics_source(acct, slot: dict, day) -> dict:
    from . import bench as BN
    day_s = day.isoformat() if isinstance(day, date) else str(slot.get("publish_at") or "")[:10]
    try:
        job = BN._job_of(acct, slot, BN._sched(acct), day_s)
    except Exception:  # noqa: BLE001
        job = ""
    entry, pj = ({}, {})
    if job:
        try:
            entry, pj = BN._politics_slate(acct, job, str(slot.get("slug") or ""))
        except Exception:  # noqa: BLE001
            entry, pj = {}, {}
    url = str(pj.get("source_url") or "").strip()
    title = str(pj.get("source_title") or (slot.get("cast_check") or {}).get("source_title") or "")
    host = str(slot.get("host") or pj.get("source_channel") or urlparse(url).netloc or "")
    fmt = str(entry.get("format") or pj.get("format") or "").lower()
    return {"host": _plain(host), "url": url, "title": _plain(title), "date": _date_words(pj.get("source_date")),
            "kind": POLITICS_KIND.get(fmt, "video"), "t": None, "many": False, "job": job}


def source(acct, slot: dict, day=None) -> dict:
    """What the clip came from, as far as the board and its job say: host, url, title, date, kind (the Politics's
    hearing, interview, briefing or video), t (the GTA moment's start in the source) and many (a reactions clip of
    several creators). {} for an account that is neither GTA nor the Politics."""
    maker = str(getattr(acct, "maker", "") or "")
    if maker == "gta":
        return _gta_source(acct, slot)
    if maker == "politics":
        return _politics_source(acct, slot, day)
    return {}


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower()).split())


def questions(slot: dict) -> List[str]:
    """The questions in the clip's captions (every platform's words), as normalised text: a first comment never
    repeats one (the caption asks; the comment says where the clip is from)."""
    out: List[str] = []
    for c in (slot.get("copy") or {}).values() if isinstance(slot, dict) else []:
        p = c.get("path") if isinstance(c, dict) else None
        try:
            text = Path(str(p)).read_text() if p else ""
        except OSError:
            continue
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            for q in re.findall(r"[^.?!\n]*\?", URLS.sub(" ", line)):   # a link's ?t= or ?v= is no question
                n = _norm(q)
                if len(n.split()) >= 3 and n not in out:
                    out.append(n)
    return out


def _repeats(text: str, qs: List[str]) -> str:
    n = _norm(text)
    return next((q for q in qs if q and q in n), "")


def _date_in(title: str, words: str) -> bool:
    """Does the title already say the date (its year and month)? Then the comment does not say it twice."""
    if not words:
        return False
    month, _, rest = words.partition(" ")
    year = rest.rsplit(" ", 1)[-1]
    low = title.lower()
    return year in low and month.lower()[:3] in low


def _join(parts: List[str]) -> str:
    out = ""
    for p in [x for x in parts if x]:
        if not out:
            out = p
        elif out.endswith(("?", "!")):
            out += " " + p
        else:
            out += ", " + p
    return out


def _gta_default(outlet: str, src: dict) -> str:
    host, url = src.get("host") or "", src.get("url") or ""
    if src.get("many"):
        return f"Reactions from {host}." if host else ""
    if outlet == "instagram_reels":
        if not host:
            return ""
        where = _where(url)
        return f"From {C.possessive(host)} stream on {where}." if where else f"From {C.possessive(host)} stream."
    if not url:
        return ""
    link = _with_t(url, src.get("t"))
    return f"Full video from {host}: {link}" if host else f"Full video: {link}"


def _politics_default(outlet: str, src: dict, qs: List[str]) -> str:
    title, host, when, url = src.get("title") or "", src.get("host") or "", src.get("date") or "", src.get("url") or ""
    if title and _repeats(title, qs):
        title = ""                                     # the caption already asks it
    if title and host and _norm(host) in _norm(title):
        host = ""
    if title and _date_in(title, when):
        when = ""
    head = _join([title.rstrip(" .,;:"), host, when])
    if outlet == "instagram_reels":
        if not title:
            return ""
        return head if head.endswith(("?", "!", ".")) else head + "."
    if not url:
        return ""
    lead = (head if head.endswith(("?", "!", ".")) else head + ".") + " " if head else ""
    return f"{lead}Full {src.get('kind') or 'video'}: {url}"


def defaults(acct, slot: dict, outs, day=None) -> Dict[str, str]:
    """The default first comment for each of these platforms, from the clip's source ({} when no source is known).
    Every default passes check_text."""
    src = source(acct, slot, day)
    if not src:
        return {}
    maker = str(getattr(acct, "maker", "") or "")
    qs = questions(slot)
    out: Dict[str, str] = {}
    for o in outs:
        t = _gta_default(o, src) if maker == "gta" else _politics_default(o, src, qs)
        if not t:
            continue
        try:
            out[o] = check_text(o, t, qs=qs, credit=credits(src))
        except SystemExit:
            continue
    return out


# ------------------------------------------------------------------------------------------------------ the words
def credits(src: dict) -> List[str]:
    """The source's own names a first comment credits (its title, its host): the bait check reads the words around
    them, so a hearing called "Follow-Up to the Border Audit" or a creator called "TagTeam" is no ask."""
    out: List[str] = []
    for k in ("title", "host"):
        v = str((src or {}).get(k) or "").strip()
        for w in (v, v.rstrip(" .,;:")):
            if w and w not in out:
                out.append(w)
    return out


def _bait(text: str, credit: Sequence[str]) -> str:
    """Why the words outside the links and the credited names are engagement bait (answers.BAIT, the one list the
    automations use too), or ''."""
    t = URLS.sub(" ", text)
    for c in sorted({str(c) for c in credit or () if str(c or "").strip()}, key=len, reverse=True):
        t = re.sub(re.escape(c), " ", t, flags=re.I)
    return AN.bait(t)


def check_text(outlet: str, text: str, slot: Optional[dict] = None, qs: Optional[List[str]] = None,
               credit: Sequence[str] = ()) -> str:
    """The words, cleaned, or SystemExit with what to change: not empty, within the platform's length, no long dash,
    no hashtag, no ask to comment, no engagement bait (outside the links and the credited source names in credit),
    not the caption's question."""
    if outlet not in OUTLETS:
        raise SystemExit(f"{outlet}: the platform is facebook, instagram or youtube")
    t = "\n".join(ln.rstrip() for ln in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
    if not t:
        raise SystemExit("The box is empty: press Clear for no first comment.")
    if len(t) > LIMIT[outlet]:
        raise SystemExit(f"A {NAMES[outlet]} first comment is {LIMIT[outlet]:,} characters at the most; this is "
                         f"{len(t):,}.")
    if any(d in t for d in DASHES):
        raise SystemExit("No long dashes in a first comment: use a comma or a colon.")
    if HASHTAG.search(t):
        raise SystemExit("No hashtags in a first comment: they belong in the caption.")
    if ASKS.search(t):
        raise SystemExit("No asks to comment or tag: say where the clip is from (Facebook demotes engagement bait).")
    why = _bait(t, credit)
    if why:
        raise SystemExit(f"No engagement bait in a first comment ({why}): say where the clip is from (Facebook "
                         f"demotes engagement bait).")
    q = _repeats(t, qs if qs is not None else questions(slot or {}))
    if q:
        raise SystemExit(f"It repeats the caption's question ({q}): the caption asks it, the comment credits the "
                         f"source.")
    return t


def _slot_of(acct, day: date, slot_key: str) -> dict:
    s = ((B.read(acct, day) or {}).get("slots") or {}).get(C.normal_slot(slot_key))
    if not isinstance(s, dict):
        raise SystemExit(f"{acct.name} {day} {slot_key}: no such slot")
    return s


def set_comment(cfg: C.Config, acct, day: date, slot_key: str, outlet: str, text: str, by: str,
                how: str = "edited") -> str:
    """Write one platform's first comment for a clip that waits for Approve (SystemExit when it is signed)."""
    if outlet not in outlets(acct):
        raise SystemExit(f"{acct.name} does not post to {NAMES.get(outlet, outlet)} through the API: a first comment "
                         f"there has no post to go under")
    slot = _slot_of(acct, day, slot_key)
    try:
        src = source(acct, slot, day)
    except Exception:                       # no source to credit: every word is checked
        src = {}
    t = check_text(outlet, text, slot, credit=credits(src))
    with EX.edit(cfg, acct, day, slot_key, by, f"first comment {PLAT[outlet]}") as x:
        fc = dict(x.get("first_comment") or {})
        fc[outlet] = {"text": t, "from": how if how in EX.FROM else "edited"}
        x["first_comment"] = fc
    return t


def clear_comment(cfg: C.Config, acct, day: date, slot_key: str, outlet: str, by: str) -> bool:
    """Take one platform's first comment off a clip that waits for Approve. Was there one?"""
    with EX.edit(cfg, acct, day, slot_key, by, f"clear first comment {PLAT.get(outlet, outlet)}") as x:
        fc = dict(x.get("first_comment") or {})
        had = fc.pop(outlet, None) is not None
        if fc:
            x["first_comment"] = fc
        else:
            x.pop("first_comment", None)
    return had


# ------------------------------------------------------------------------------------------------------ collect
@_safe("firstcomment:on_collect", None)
def on_collect(ctx) -> None:
    """AFTER_TAKE: the default first comment for each platform whose switch is not off, when the source is known.
    Nothing at all while every first-comment switch is off."""
    acct = ctx.acct
    outs = [o for o in outlets(acct) if mode(acct, o) != SW.OFF]
    if not outs:
        return None
    texts = defaults(acct, ctx.slot, outs, ctx.day)
    if not texts:
        return None
    fc = ctx.extras.setdefault("first_comment", {})
    for o, t in texts.items():
        fc.setdefault(o, {"text": t, "from": "default"})
    return None


# ------------------------------------------------------------------------------------------------------ the marker
def marker(rec) -> dict:
    """outlets[p].social.first_comment of an outlet record ({} when none)."""
    soc = rec.get("social") if isinstance(rec, dict) else None
    m = soc.get("first_comment") if isinstance(soc, dict) else None
    return m if isinstance(m, dict) else {}


def _set_marker(cfg: C.Config, acct, day: date, slot_key: str, outlet: str, upd: dict,
                action: Optional[str] = None) -> bool:
    """Write the marker under the board's lock (never with the outward lock held). With `action`, only while the
    marker is this action's (or there is none): an old action never writes over a newer one."""
    try:
        with B.edit(cfg, acct, day, create=False) as bd:
            s = (bd.get("slots") or {}).get(slot_key)
            rec = ((s or {}).get("outlets") or {}).get(outlet)
            if not isinstance(rec, dict):
                return False
            cur = marker(rec)
            if action and cur.get("action") and cur.get("action") != action:
                return False
            soc = rec.get("social") if isinstance(rec.get("social"), dict) else {}
            new = dict(cur)
            new.update({k: v for k, v in upd.items() if v is not None})
            for k in [k for k, v in upd.items() if v is None]:
                new.pop(k, None)
            soc["first_comment"] = new
            rec["social"] = soc
            return True
    except SystemExit:
        return False


# ------------------------------------------------------------------------------------------------------ queueing
def _missing(acct, outlet: str) -> List[str]:
    from . import registry as REG
    f = REG.fn("capabilities:missing")
    if f is None:
        return ["(the permission check is not built yet)"]
    try:
        return [str(x) for x in f(acct, PLAT[outlet], "comment") or []]
    except Exception as e:  # noqa: BLE001
        return [f"(the permission check failed: {type(e).__name__})"]


def _yt_connect(acct, *add: str) -> str:
    """The connect youtube line with the extras the account already has as well (one --with, nothing lost)."""
    from . import registry as REG
    f = REG.fn("capabilities:yt_connect_line")
    try:
        if f is not None:
            return str(f(acct, *add))
    except Exception:  # noqa: BLE001  (a fix line never fails the doctor)
        pass
    return f"factory.py connect youtube {acct.slug} --client client.json --with {','.join(add)}"


def _delay(acct, outlet: str) -> float:
    st = SW.settings(acct)
    key = "first_comment.youtube_delay_s" if outlet == "youtube_shorts" else "first_comment.delay_s"
    lo, hi = st[key]  # type: ignore
    return RNG.uniform(float(lo), float(hi))


def plan(cfg: C.Config, acct, day: Optional[date], slot: dict, outlet: str, rec: Optional[dict],
         now: datetime, jitter: bool = True) -> Tuple[Optional[dict], str]:
    """(what to queue, '') or (None, why not) for one post's first comment: the clip still signed as it was, its
    words, a post the factory made itself, the time it went live (the queue waits a little past that)."""
    if outlet not in OUTLETS or not isinstance(slot, dict):
        return None, "not a platform with first comments"
    if slot.get("status") not in B.SIGNED:
        return None, "not signed"
    so = slot.get("signoff") or {}
    if not so.get("hash") or B.verify(slot, cached=True):
        return None, "not signed as it is on the board"
    x = slot.get("extras") or {}
    text = EX.first_comment(slot, outlet)
    if not text or not x.get("sha256"):
        return None, "no first comment"
    board_rec = (slot.get("outlets") or {}).get(outlet)
    r = dict(rec or {})
    r.update(board_rec if isinstance(board_rec, dict) else {})
    if outlet == "youtube_shorts":
        pid = str(r.get("video_id") or "")
        if not pid or not r.get("clip_sha"):
            return None, "not an upload the factory made"
        times = [t for t in (_when(r.get("publish_at")) or _when(slot.get("publish_at")), _when(r.get("at"))) if t]
        live = max(times) if times else None
    else:
        pid = str(r.get("post_id") or "")
        if not pid or r.get("provider") != "direct":
            return None, "not a post the factory made (a matched record is never commented on)"
        live = _when(r.get("published_at"))
    if live is None:
        return None, "the post has no time it went live"
    now = _utc(now)
    expires = live + LIVE_FOR
    if expires <= now:
        return None, "more than 20 hours since it went live"
    nb = max(live + timedelta(seconds=_delay(acct, outlet) if jitter else 0), now)
    plat = PLAT[outlet]
    return {"key": f"fc:{plat}:{pid}", "platform": plat, "outlet": outlet, "text": text, "post_id": pid,
            "live": live, "not_before": nb, "expires": expires, "url": str(r.get("url") or ""),
            "signed": {"what": "clip", "by": so.get("by"), "at": so.get("at"), "hash": so.get("hash"),
                       "extras_sha": x.get("sha256"), "text_sha": _sha(text)}}, ""


def _again(acct, outlet: str, mk: dict, slot: dict, now: datetime) -> bool:
    """May a post whose marker says this be queued again? Only when no call for it was ever made (the marker went
    "sending" never) and: it waited for a permission that is there now, YouTube was not public yet and the time to
    look again has come, or it was skipped (a Pull, an edit) and the clip has been signed again since."""
    if not isinstance(mk, dict) or not mk or mk.get("called"):
        return False
    st = str(mk.get("state") or "")
    if st == "waits":
        look = _when(mk.get("look_at"))
        if look is not None:
            return look <= _utc(now)
        return not _missing(acct, outlet)
    if st in ("skipped", "refused"):
        so = (slot.get("signoff") or {}) if isinstance(slot, dict) else {}
        now_sig = (str(so.get("at") or ""), str(so.get("hash") or ""))
        was = (str(mk.get("signed_at") or ""), str(mk.get("signed_hash") or ""))
        return bool(all(now_sig) and any(was) and now_sig != was)
    return False


def queue_one(cfg: C.Config, acct, day: date, slot: dict, outlet: str, rec: Optional[dict], now: datetime,
              by: str = "poster") -> Optional[str]:
    """Queue one post's first comment through outward (a marker on the outlet record says so). None when there is
    nothing to queue."""
    from . import outward as OW
    m = mode(acct, outlet)
    if m == SW.OFF:
        return None
    p, why = plan(cfg, acct, day, slot, outlet, rec, now)
    if p is None:
        return None
    k = str(slot.get("slot") or "")
    name = f"{acct.name} {day} {k} {NAMES[outlet]}"
    state = OW.key_state(cfg, acct, p["key"])
    mk = marker(((slot.get("outlets") or {}).get(outlet)))
    if state and not (state in ("skipped", "refused") and _again(acct, outlet, mk, slot, now)):
        if not mk:                                    # the politics knows it: never queued twice
            _set_marker(cfg, acct, day, k, outlet, {"state": state, "text_sha": p["signed"]["text_sha"],
                                                    "at": _iso(now)})
        return None
    if m == "on":
        miss = _missing(acct, outlet)
        if miss:
            _set_marker(cfg, acct, day, k, outlet, {"state": "waits", "why": "needs " + ", ".join(miss),
                                                    "text_sha": p["signed"]["text_sha"], "at": _iso(now),
                                                    "look_at": None})
            return f"{name}: first comment waits for {', '.join(miss)}"
    target = {"post_id": p["post_id"], "board": day.isoformat(), "slot": k, "outlet": outlet,
              "live_at": _iso(p["live"]), "url": p["url"]}
    try:
        aid = OW.queue(cfg, acct, p["platform"], "first_comment", key=p["key"], text=p["text"], target=target,
                       origin={"by": by}, signed=p["signed"], outlet=outlet, not_before=p["not_before"],
                       expires=p["expires"], now=now)
    except SystemExit as e:
        return f"{name}: first comment not queued ({e})"
    _set_marker(cfg, acct, day, k, outlet, {"state": "queued", "action": aid, "text_sha": p["signed"]["text_sha"],
                                            "at": _iso(now), "not_before": _iso(p["not_before"]),
                                            "mode": "practice" if m == "practice" else "live", "why": None,
                                            "id": None, "url": None, "look_at": None,
                                            "signed_at": p["signed"]["at"], "signed_hash": p["signed"]["hash"]})
    when = p["not_before"].astimezone(cfg.tz()).strftime("%H:%M") if cfg.tz() else _iso(p["not_before"])
    return f"{name}: first comment {'practice' if m == 'practice' else 'queued'} for {when}"


@_safe("firstcomment:on_posted", None)
def on_posted(cfg: C.Config, acct, day: date, slot: dict, outlet: str, rec: dict,
              now: Optional[datetime] = None) -> Optional[str]:
    """FOLLOW_UPS: the post is up. Queue its first comment (switch not off, words signed with the clip, a post the
    factory made), and for an Instagram reel note its poll and trial in practice. Never a platform call here."""
    now = now or cfg.now()
    said: List[str] = []
    if outlet == "instagram_reels":
        try:
            got = RX.on_posted(cfg, acct, day, slot, rec, now)
            if got:
                said.append(got)
        except (Exception, SystemExit) as e:  # noqa: BLE001  (a poll note never stops the first comment)
            from . import registry as REG
            REG._log_error("reelextras:on_posted", e)
    if outlet in OUTLETS and outlet in outlets(acct):
        got = queue_one(cfg, acct, day, slot, outlet, rec, now)
        if got:
            said.append(got)
    return "; ".join(said) or None


def sweep(cfg: C.Config, now: Optional[datetime] = None, quiet: bool = True) -> List[str]:
    """The tick's step: any post of the last 20 hours whose switch is on and whose first comment never went gets it
    queued now: no marker yet (a miss in the poster's hook), a permission it waited for is there, YouTube's time to
    look again has come, or it was skipped (a Pull) and the clip is signed again. Never one a call was made for."""
    from . import social as SOC
    now = now or cfg.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=cfg.tz())
    out: List[str] = []
    for acct in SOC.accounts(cfg):
        outs = [o for o in outlets(acct) if mode(acct, o) != SW.OFF]
        if not outs:
            continue
        today = now.astimezone(cfg.tz()).date() if cfg.tz() else now.date()
        for d in (today - timedelta(days=1), today):
            try:
                bd = B.read(acct, d) or {}
            except (OSError, ValueError):
                continue
            for k, s in sorted((bd.get("slots") or {}).items()):
                if not isinstance(s, dict) or s.get("status") not in B.SIGNED:
                    continue
                for o in outs:
                    rec = (s.get("outlets") or {}).get(o)
                    if not isinstance(rec, dict) or not (rec.get("post_id") or rec.get("video_id")):
                        continue
                    mk = marker(rec)
                    if mk and not _again(acct, o, mk, s, now):
                        continue
                    try:
                        got = queue_one(cfg, acct, d, s, o, rec, now, by="sweep")
                    except Exception as e:  # noqa: BLE001  (one post never stops the sweep)
                        got = f"{acct.name} {d} {k} {NAMES[o]}: {type(e).__name__}: {e}"
                    if got:
                        out.append("  " + got)
    return out


# ------------------------------------------------------------------------------------------------------ the executor
def _board_slot(acct, action: dict) -> Tuple[Optional[date], Optional[dict]]:
    t = action.get("target") or {}
    try:
        day = date.fromisoformat(str(t.get("board") or ""))
    except ValueError:
        return None, None
    s = ((B.read(acct, day) or {}).get("slots") or {}).get(str(t.get("slot") or ""))
    return day, (s if isinstance(s, dict) else None)


def still_signed(cfg: C.Config, acct, action: dict) -> str:
    """'' when the comment may still go: the slot signed with the same signature, nothing changed since, the same
    extras file and words, and the post still on the record. Else the words for the log (it is skipped)."""
    signed = action.get("signed") or {}
    if signed.get("what") != "clip":
        return "a first comment goes only signed with its clip"
    day, s = _board_slot(acct, action)
    if s is None:
        return "the slot is gone from its board"
    if s.get("status") not in B.SIGNED:
        return "pulled: the clip is not signed any more"
    if (s.get("signoff") or {}).get("hash") != signed.get("hash"):
        return "signed again since: these words were for the earlier signature"
    probs = B.verify(s, cached=True)
    if probs:
        return "changed after signing: " + "; ".join(probs)
    if (s.get("extras") or {}).get("sha256") != signed.get("extras_sha"):
        return "the first comment file is not the one that was signed"
    outlet = str(action.get("outlet") or "")
    text = EX.first_comment(s, outlet)
    if not text or _sha(text) != signed.get("text_sha") or _sha(action.get("text") or "") != signed.get("text_sha"):
        return "the first comment is not the words that were signed"
    rec = (s.get("outlets") or {}).get(outlet) or {}
    pid = str((action.get("target") or {}).get("post_id") or "")
    have = str(rec.get("video_id") if outlet == "youtube_shorts" else rec.get("post_id") or "")
    if not pid or have != pid:
        return "the post is not on the board any more"
    if outlet != "youtube_shorts" and rec.get("provider") != "direct":
        return "not a post the factory made"
    return ""


def _target_day(action: dict) -> Optional[date]:
    try:
        return date.fromisoformat(str((action.get("target") or {}).get("board") or ""))
    except ValueError:
        return None


def _mark(cfg: C.Config, acct, action: dict, upd: dict) -> bool:
    """Write this action's marker (only while the marker is this action's)."""
    day = _target_day(action)
    if day is None:
        return False
    t = action.get("target") or {}
    return _set_marker(cfg, acct, day, str(t.get("slot") or ""), str(action.get("outlet") or ""), upd,
                       action=action.get("id"))


def _marker_of(acct, action: dict) -> dict:
    """The marker on the board for this action's post ({} when there is none)."""
    day, s = _board_slot(acct, action)
    rec = ((s or {}).get("outlets") or {}).get(str(action.get("outlet") or ""))
    return marker(rec)


def _sending(cfg: C.Config, acct, action: dict) -> None:
    """The marker goes "sending" before the call that cannot be undone, and says a call was made for this post (a
    post with a call made is never queued again by itself)."""
    _mark(cfg, acct, action, {"state": "sending", "action": action.get("id"), "at": _iso(cfg.now()), "why": None,
                              "called": True})


def _unsupported(e: BaseException) -> bool:
    """Meta's "Unsupported post request" or "Unsupported get request": this id takes no comments (a reel's video id
    whose comments live on its post id)."""
    return bool(UNSUPPORTED.search(str(e)))


def _fb_post_id(acct, video_id: str) -> str:
    """A Facebook reel's post id (PAGEID_POSTID), for when its video id takes no comments."""
    from . import graph as G
    got = G.gget(acct, video_id, {"fields": "post_id"})
    return str(got.get("post_id") or "")


def _yt_public(cfg: C.Config, acct, video_id: str, action: dict) -> None:
    """Raise unless YouTube shows the Short as public. Not public yet: a skip that says so, and the marker waits with
    the time to look again (20 minutes, then 40, then the 2-hour mark), so a slow Short costs a few looks, not a
    try every 5 minutes against the caps. Gone, or still not public 2 hours after its time: skipped for good."""
    from . import graph as G
    got = G.yt_get(acct, "videos", {"part": "status", "id": video_id}, cfg=cfg)
    items = got.get("items") or []
    if not items:
        raise SystemExit(f"YouTube has no video {video_id} on this channel any more")
    st = str(((items[0] or {}).get("status") or {}).get("privacyStatus") or "")
    if st == "public":
        return None
    now = _utc(cfg.now())
    live = _when((action.get("target") or {}).get("live_at"))
    looks = int(_marker_of(acct, action).get("looks") or 0) + 1
    nxt = now + PUBLIC_LOOKS[min(looks, len(PUBLIC_LOOKS)) - 1]
    if live is not None and nxt - live > YT_PUBLIC_WAIT:
        mins = max(0, int((now - live).total_seconds() // 60))
        raise SystemExit(f"YouTube still shows it {st or 'not public'} {mins} minutes after its time: no first "
                         f"comment (it waits 2 hours at the most)")
    when = _local(cfg, _iso(nxt)) or _iso(nxt)
    _mark(cfg, acct, action, {"state": "waits", "why": f"YouTube has not made it public yet ({st or 'unknown'}): "
                                                       f"it looks again at {when}",
                              "look_at": _iso(nxt), "looks": looks})
    raise SystemExit(f"{NOT_PUBLIC} YouTube shows {video_id} {st or 'not public'}; it looks again at {when}")


def execute(cfg: C.Config, acct, action: dict) -> dict:
    """The one call (outside every lock): Facebook POST /{video}/comments (on the reel's post id when Facebook says
    "Unsupported post request": that id goes on the marker first, so a lost answer is looked for where it went),
    Instagram POST /{media}/comments, YouTube commentThreads.insert once the Short is public. {"id", "url",
    "endpoint"}."""
    from . import direct as D
    from . import graph as G
    plat = str(action.get("platform") or "")
    t = action.get("target") or {}
    pid = str(t.get("post_id") or "")
    text = str(action.get("text") or "")
    if not pid or not text:
        raise SystemExit("a first comment needs its post and its words")
    if plat == "youtube":
        _yt_public(cfg, acct, pid, action)
        _sending(cfg, acct, action)
        got = G.yt_write(acct, "commentThreads.insert", {"part": "snippet"},
                         {"snippet": {"videoId": pid, "topLevelComment": {"snippet": {"textOriginal": text}}}},
                         cfg=cfg)
        cid = str(got.get("id") or "")
        return {"id": cid, "url": f"https://www.youtube.com/watch?v={pid}" + (f"&lc={cid}" if cid else ""),
                "endpoint": "POST /youtube/v3/commentThreads"}
    if plat not in ("facebook", "instagram"):
        raise SystemExit(f"{plat}: first comments go to Facebook, Instagram and YouTube")
    _sending(cfg, acct, action)
    if plat == "instagram":
        got = G.gpost(acct, f"{pid}/comments", form={"message": text})
        return {"id": str(got.get("id") or ""), "url": str(t.get("url") or ""), "endpoint": "POST /{id}/comments"}
    alt = str(_marker_of(acct, action).get("alt") or "")
    if alt and alt != pid:                       # this reel's comments live on its post id: straight there
        got = G.gpost(acct, f"{alt}/comments", form={"message": text})
        return {"id": str(got.get("id") or ""), "url": str(t.get("url") or ""), "endpoint": "POST /{id}/comments"}
    try:
        got = G.gpost(acct, f"{pid}/comments", form={"message": text})
    except D.PlatformError as e:
        if not _unsupported(e):
            raise
        alt = _fb_post_id(acct, pid)
        if not alt or alt == pid:
            raise
        if not _mark(cfg, acct, action, {"alt": alt}):
            raise SystemExit(f"could not write the reel's post id {alt} on the board: not sent (so a lost answer "
                             f"is always looked for where it went)")
        got = G.gpost(acct, f"{alt}/comments", form={"message": text})
    return {"id": str(got.get("id") or ""), "url": str(t.get("url") or ""), "endpoint": "POST /{id}/comments"}


def _same(a: str, b: str) -> bool:
    return " ".join(str(a or "").split()) == " ".join(str(b or "").split())


def find(cfg: C.Config, acct, action: dict) -> Optional[dict]:
    """Our own comment with these exact words under the post, when an answer was lost ({"id", "url"} or None). By
    the commenter's id: the Page, the Instagram account, the YouTube channel. Facebook looks under the video id and
    under the reel's post id when the comment went there (the marker's alt)."""
    from . import direct as D
    from . import graph as G
    plat = str(action.get("platform") or "")
    t = action.get("target") or {}
    pid = str(t.get("post_id") or "")
    text = str(action.get("text") or "")
    if plat == "youtube":
        tok = D._load(acct, "youtube_token", f"youtube {acct.slug} --client client.json")
        me = str(tok.get("channel_id") or "")
        # the newest threads, not a word search: ours went minutes ago, and a search for a link is not reliable
        got = G.yt_get(acct, "commentThreads", {"part": "snippet", "videoId": pid, "order": "time",
                                                "maxResults": "50", "textFormat": "plainText"}, cfg=cfg)
        for it in got.get("items") or []:
            sn = (((it or {}).get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
            who = str((sn.get("authorChannelId") or {}).get("value") or "")
            if me and who == me and (_same(sn.get("textOriginal"), text) or _same(sn.get("textDisplay"), text)):
                cid = str(it.get("id") or "")
                return {"id": cid, "url": f"https://www.youtube.com/watch?v={pid}&lc={cid}"}
        return None
    keys = G.meta_keys(acct)
    if plat == "instagram":
        rows = G.gget(acct, f"{pid}/comments", {"fields": "id,text,username,from", "limit": "50"}).get("data") or []
        for c in rows:
            frm = c.get("from") or {}
            mine = (keys.ig_user_id and str(frm.get("id") or "") == keys.ig_user_id) or \
                   (keys.ig_username and str(c.get("username") or frm.get("username") or "") == keys.ig_username)
            if mine and _same(c.get("text"), text):
                return {"id": str(c.get("id") or ""), "url": str(t.get("url") or "")}
        return None

    def look(obj: str) -> Optional[dict]:
        rows = G.gget(acct, f"{obj}/comments", {"fields": "id,message,from", "filter": "toplevel",
                                                "order": "reverse_chronological", "limit": str(FIND_LIMIT)})
        for c in rows.get("data") or []:
            if str((c.get("from") or {}).get("id") or "") == keys.page_id and _same(c.get("message"), text):
                return {"id": str(c.get("id") or ""), "url": str(t.get("url") or "")}
        return None
    alt = str(_marker_of(acct, action).get("alt") or "")
    try:
        got = look(pid)
    except D.PlatformError as e:
        if not _unsupported(e):
            raise
        got = None                           # the video id takes no comments: only its post id can have ours
    if got:
        return got
    # execute writes the post id on the marker before it posts there: no alt, never posted there
    return look(alt) if alt and alt != pid else None


def _why(cfg: C.Config, acct, action: dict) -> str:
    """The last log row's words for this action (on_state is told only the state)."""
    from . import outward as OW
    since = _utc(cfg.now()) - timedelta(days=2)
    why = ""
    try:
        for r in OW.rows(cfg, acct.slug, since=since, kinds=("first_comment",)):
            if r.get("id") == action.get("id") and r.get("why"):
                why = str(r["why"])
    except Exception:  # noqa: BLE001
        return ""
    return why[:300]


@_safe("firstcomment:on_state", None)
def on_state(cfg: C.Config, acct, action: dict, state: str, result) -> None:
    """Mirror what outward did onto the marker: done (with the comment's id and link), practice, skipped (why),
    or back in the queue."""
    t = action.get("target") or {}
    try:
        day = date.fromisoformat(str(t.get("board") or ""))
    except ValueError:
        return None
    now = _utc(cfg.now())
    st = str(state or "")
    upd: dict = {"action": action.get("id"), "at": _iso(now)}
    if st == "done":
        r = result if isinstance(result, dict) else {}
        upd.update(state="done", id=r.get("id") or None, url=r.get("url") or None, why=None)
    elif st == "practice":
        upd.update(state="practice", would_at=_iso(now), why=None)
    elif st in ("skipped", "refused", "gave_up"):
        why = _why(cfg, acct, action)
        if st == "skipped" and why.startswith(NOT_PUBLIC):
            upd.update(state="waits")                # YouTube not public yet: execute set when to look again
        else:
            upd.update(state=st, why=why or None)
    elif st in ("failed", "held", "queued"):
        upd.update(state="queued", why=_why(cfg, acct, action) or None)
    elif st == "sending":
        upd.update(state="sending")
    else:
        return None
    _set_marker(cfg, acct, day, str(t.get("slot") or ""), str(action.get("outlet") or ""), upd,
                action=action.get("id"))
    return None


# ------------------------------------------------------------------------------------------------------ the card
WORDS = "{words}"
# The Save confirm quotes the words in the box as they are when it is pressed: the Clips page's data-x handler fills
# {words} in the button's data-ask-t from the form's data-w fields (data-ask holds the words as drawn, for no script).
CARD_CSS = """
.fc{border-top:1px solid var(--line);margin-top:6px;padding-top:4px}
.fc>summary{cursor:pointer;font-size:13px;font-weight:600;min-height:32px;display:flex;align-items:center;gap:6px}
.fc-o{display:flex;flex-direction:column;gap:6px;padding:8px 0;border-bottom:1px solid var(--line);min-width:0}
.fc-o:last-child{border-bottom:0}
.fc-l{display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:12.5px;font-weight:600}
.fc-m{font-weight:550;color:var(--sub);font-size:11.5px;line-height:18px;padding:0 6px;border:1px solid var(--line);border-radius:4px}
.fc-m.on{color:var(--ok);border-color:var(--ok)}
.fc-m.warn{color:var(--warn);border-color:var(--warn)}
.fc-t{font:inherit;font-size:16px;line-height:1.4;width:100%;box-sizing:border-box;min-height:72px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);resize:vertical}
.fc-b{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.fc-n{font-size:12px;color:var(--sub)}
.fc-txt{font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}
.fc-s{font-size:12.5px;color:var(--sub);overflow-wrap:anywhere}
.fc-s.ok{color:var(--ok)}
.fc-s.bad{color:var(--bad)}
.fc-s.warn{color:var(--warn)}
.fc-s a{color:var(--blue)}
.fc-opts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
.fc-opts input{font:inherit;font-size:16px;height:36px;padding:0 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);min-width:0;width:100%;box-sizing:border-box}
.fc-warn{font-size:12.5px;color:var(--warn);background:var(--warnbg);padding:6px 8px;border-radius:6px;overflow-wrap:anywhere}
@media (max-width:600px){.fc>summary{min-height:40px}.fc-opts{grid-template-columns:1fr}.fc-opts input{height:40px}
.fc-b button{height:40px;min-width:40px}}
"""


def _local(cfg: C.Config, v) -> str:
    t = _when(v)
    if t is None:
        return ""
    tz = cfg.tz()
    return (t.astimezone(tz) if tz else t).strftime("%H:%M")


def state_line(cfg: C.Config, acct, s: dict, outlet: str) -> Tuple[str, str, str]:
    """(class, words, link) for a signed clip's first comment on one platform: off, practice, waits, queued,
    sending, posted, skipped."""
    m = mode(acct, outlet)
    rec = (s.get("outlets") or {}).get(outlet)
    mk = marker(rec)
    st = str(mk.get("state") or "")
    why = str(mk.get("why") or "").strip().rstrip(".")
    if st == "done":
        return "ok", "posted", str(mk.get("url") or (rec or {}).get("url") or "")
    if st == "practice":
        at = _local(cfg, mk.get("would_at") or mk.get("at"))
        return "warn", f"practice: would have posted {at}, nothing went out", ""
    if st == "queued":
        when = _local(cfg, mk.get("not_before"))
        if mk.get("mode") == "practice":
            return "warn", f"practice: would post {when}".rstrip(), ""
        return "", (f"queued: posts about {when}" if when else "queued") + (f" ({why})" if why else ""), ""
    if st == "sending":
        return "", "sending", ""
    if st == "waits":
        return "warn", f"waits: {why or 'a permission'}", ""
    if st in ("skipped", "refused", "gave_up"):
        if m != SW.OFF and _again(acct, outlet, mk, s, cfg.now()):
            return "", "signed again: it queues within 5 minutes", ""
        return "bad", f"skipped: {why or st}", ""
    if m == SW.OFF:
        return "", "off", ""
    up = isinstance(rec, dict) and bool(rec.get("post_id") or rec.get("video_id"))
    if up:                                  # the post is up with no marker: say why none goes, or that one will
        p, why_not = plan(cfg, acct, None, s, outlet, rec, cfg.now(), jitter=False)
        if p is None:
            return "", f"none: {why_not}", ""
    miss = _missing(acct, outlet) if m == "on" else []
    if miss:
        return "warn", "waits: needs " + ", ".join(miss), ""
    if m == "practice":
        if up:
            return "warn", "practice: the Log notes it within 5 minutes", ""
        return "warn", "practice: it waits for the post, then the Log notes it", ""
    return "", "queues within 5 minutes" if up else "waits for the post", ""


def _mode_chip(acct, outlet: str) -> str:
    m = mode(acct, outlet)
    cls = " on" if m == "on" else " warn" if m == "practice" else ""
    return f'<span class="fc-m{cls}">{esc(m)}</span>'


@_safe("firstcomment:card_part", "")
def card_part(cfg: C.Config, acct, day: date, s: dict, now: datetime) -> str:
    """The card's First comment fold ('' while every first-comment, poll and trial switch is off and the clip has
    no extras). Open while the clip waits for Approve: a box per platform with Save and Clear; after Approve, the
    words and how they went."""
    if _is_client(acct) or not isinstance(s, dict) or not s.get("clip"):
        return ""
    st = s.get("status")
    ready = st == B.READY
    if not ready and st not in B.SIGNED:
        return ""
    outs = outlets(acct)
    data = EX.read(s)
    fc = data.get("first_comment") if isinstance(data.get("first_comment"), dict) else {}
    switched = any(mode(acct, o) != SW.OFF for o in outs) or \
        (RX.has_reels(acct) and (SW.mode(acct, RX.POLL) != SW.OFF or SW.mode(acct, RX.TRIAL) != SW.OFF))
    if not switched and not (s.get("extras") or {}).get("path"):
        return ""
    a, k = esc(acct.slug), esc(s.get("slot") or "")
    rows: List[str] = []
    for o in outs:
        ent = fc.get(o) if isinstance(fc.get(o), dict) else {}
        text = str(ent.get("text") or "")
        if mode(acct, o) == SW.OFF and not text:
            continue
        name = NAMES[o]
        if ready:
            how = {"default": "from the source", "edited": "edited", "task": "written by the nightly run"}.get(
                str(ent.get("from") or ""), "") if text else "none: the post goes with no first comment"
            miss = _missing(acct, o) if mode(acct, o) != SW.OFF else []
            need = f'<span class="fc-n">needs {esc(", ".join(miss))}</span>' if miss else ""
            ask_t = (f"Save this as the first comment under the {name} post of {acct.name} {s.get('slot')}: "
                     f"\"{WORDS}\"? It goes out a few minutes after the post is live, and only once you approve the "
                     f"clip.")
            ask_clear = (f"Remove the first comment under the {name} post of {acct.name} {s.get('slot')}? The post "
                         f"then goes with none.")
            clear = (f'<button type="button" data-op="extra-clear" data-x="" data-acct="{a}" data-slot="{k}" '
                     f'data-ask="{esc(ask_clear)}">Clear</button>' if text else "")
            fid = f"fc-{a}-{day:%Y%m%d}-{k.replace(':', '')}-{o}"
            rows.append(f'<div class="fc-o" data-x-form="">'
                        f'<label class="fc-l" for="{fid}">{name} {_mode_chip(acct, o)}{need}</label>'
                        f'<textarea id="{fid}" class="fc-t" name="text" data-w="" rows="3" maxlength="{LIMIT[o]}" '
                        f'spellcheck="true">{esc(text)}</textarea>'
                        f'<input type="hidden" name="outlet" value="{o}"><input type="hidden" name="part" '
                        f'value="first_comment"><div class="fc-b"><button type="button" data-op="extra-set" '
                        f'data-x="" data-acct="{a}" data-slot="{k}" data-ask="{esc(ask_t.replace(WORDS, text))}" '
                        f'data-ask-t="{esc(ask_t)}">Save</button>{clear}'
                        f'<span class="fc-n">{esc(how)}</span></div></div>')
        else:
            cls, words, link = state_line(cfg, acct, s, o)
            if link:
                words_html = f'<a href="{esc(link)}" target="_blank" rel="noopener noreferrer">{esc(words)}</a>'
            else:
                words_html = esc(words)
            body = f'<div class="fc-txt">{esc(text)}</div>' if text else '<div class="fc-s">none</div>'
            rows.append(f'<div class="fc-o"><div class="fc-l">{name} {_mode_chip(acct, o)}</div>{body}'
                        f'<div class="fc-s{" " + cls if cls else ""}">{words_html}</div></div>')
    rows.append(RX.card_html(cfg, acct, day, s, ready))
    inner = "".join(r for r in rows if r)
    if not inner:
        return ""
    return (f'<details class="fc" data-k="fc"{" open" if ready else ""}><summary>First comment</summary>'
            f'{inner}</details>')


# ------------------------------------------------------------------------------------------------------ clicks
def act(cfg: C.Config, body: dict, page: bool = False, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """extra-set (one platform's words), extra-clear (one platform's words, the poll or the trial flag) and
    extra-poll (the poll's options), from the card's First comment fold. Only a click on the page, only while the
    clip waits for Approve."""
    from . import socialui as SX
    op = str(body.get("op") or "")
    if op not in OPS:
        raise SystemExit(f"{op or 'that'}: not a first comment click")
    SX.need_page(page, "change a clip's first comment or poll")
    acct = cfg.account(str(body.get("account") or ""))
    if _is_client(acct):
        raise SystemExit(f"{acct.name} is a client's account: first comments are for the owner's own accounts")
    try:
        day = date.fromisoformat(str(body.get("date") or ""))
    except ValueError:
        raise SystemExit("which day? (reload the page)")
    slots = body.get("slots")
    if not isinstance(slots, list) or len(slots) != 1 or not isinstance(slots[0], str):
        raise SystemExit("one clip at a time")
    key = C.normal_slot(slots[0])
    fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
    by = str(body.get("by") or (cfg.approvers[0] if cfg.approvers else "")).strip() or "review page"
    s = _slot_of(acct, day, key)
    if s.get("status") in B.SIGNED:
        raise SystemExit("It is signed: Pull it to change its first comment.")
    if s.get("status") != B.READY:
        raise SystemExit(f"{key}: there is no clip waiting for Approve")
    seen = body.get("seen")
    if isinstance(seen, str) and seen and seen != B.seen_key(s):
        raise SystemExit("This clip changed since the page showed it: look at it again, then Save.")
    where = f"{acct.name} {key}"
    if op == "extra-poll":
        opts = [str(fields.get(f) or "") for f in RX.FIELDS]
        words = RX.set_poll(cfg, acct, day, key, opts, by)
        return True, f"{words}. Approve signs it with the clip."
    part = str(fields.get("part") or "first_comment")
    if op == "extra-clear" and part in ("poll", "trial"):
        return True, RX.clear(cfg, acct, day, key, part, by) + "."
    outlet = outlet_of(str(fields.get("outlet") or ""))
    if op == "extra-clear":
        had = clear_comment(cfg, acct, day, key, outlet, by)
        return True, (f"{where}: no first comment on {NAMES[outlet]} now." if had else
                      f"{where}: there was no first comment on {NAMES[outlet]}.")
    text = fields.get("text")
    if not isinstance(text, str):
        raise SystemExit("the words are missing (reload the page)")
    t = set_comment(cfg, acct, day, key, outlet, text, by)
    return True, (f'{where}: the first comment on {NAMES[outlet]} is now: "{t}". Approve signs it with the clip.')


# ------------------------------------------------------------------------------------------------------ doctor
def _count(cfg: C.Config, acct, outlet: str, states, practice: bool, since: datetime) -> int:
    from . import outward as OW
    n = 0
    try:
        for r in OW.rows(cfg, acct.slug, since=since, kinds=("first_comment",), states=states):
            if r.get("outlet") == outlet and (r.get("mode") == "practice") == practice and \
                    not str(r.get("why") or "").startswith(NOT_PUBLIC):
                n += 1
    except Exception:  # noqa: BLE001
        return 0
    return n


def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """One line per account and platform: off (and what it waits for), practice, or on with what went in 7 days; a
    NO when it is on but the permission is not there. Read only, no network; never raises (a NO says what broke)."""
    try:
        return _doctor(cfg, now)
    except (Exception, SystemExit) as e:  # noqa: BLE001
        return [(False, f"firstcomment: its check failed ({type(e).__name__}: {e})"[:300],
                 "look at clipfactory/firstcomment.py")]


def _doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    from . import social as SOC
    now = _utc(now or cfg.now())
    week = now - timedelta(days=7)
    rows: List[Tuple[Optional[bool], str, str]] = []
    for a in SOC.accounts(cfg):
        for o in outlets(a):
            m = mode(a, o)
            label = f"{a.name} first comments on {NAMES[o]}"
            miss = _missing(a, o)
            probe = f"factory.py social probe {a.slug} scopes"
            unread = any(x.startswith("(not read yet") or x.startswith("(the permission check") for x in miss)
            if unread:                         # the permissions were never read: the fix is to read them
                miss = [f"its permissions to be read ({probe})"]
                fix = probe
            elif o == "youtube_shorts":
                fix = _yt_connect(a, "comments")
            else:
                names = [x for x in miss if not x.startswith("(")]
                fix = (f"factory.py connect meta {a.slug} with {', '.join(names)} ticked" if names else
                       f"factory.py connect meta {a.slug}")
            if m == SW.OFF:
                if o == "youtube_shorts" and miss:          # not granted, or not read: opting in grants it
                    rows.append((None, f"{label}: off (opt in: connect youtube --with comments; that also grants "
                                       f"delete power, which the factory never uses)", ""))
                elif miss:
                    rows.append((None, f"{label}: off (waits for {', '.join(miss)})", ""))
                else:
                    rows.append((None, f"{label}: off (try it: factory.py social on {a.slug} {KEY}{o} --practice)",
                                 ""))
            elif m == "practice":
                n = _count(cfg, a, o, ("practice",), True, week)
                what = "waits for " + ", ".join(miss) if miss else f"{n} practised in 7 days"
                rows.append((None, f"{label}: practice ({what})", ""))
            elif miss:
                rows.append((False, f"{label}: on, but it waits for {', '.join(miss)}", fix))
            else:
                done = _count(cfg, a, o, ("done",), False, week)
                bad = _count(cfg, a, o, ("skipped", "gave_up", "refused"), False, week)
                more = f", {bad} did not go (the Log says why)" if bad else ""
                rows.append((True, f"{label}: on, {done} posted in 7 days{more}", ""))
        rows += RX.doctor_rows(cfg, a, now)
    return rows


# ------------------------------------------------------------------------------------------------------ the command
def _account(cfg: C.Config, slug: str):
    a = cfg.account(slug)
    if _is_client(a):
        raise SystemExit(f"{a.name} is a client's account: first comments are for the owner's own accounts")
    return a


def _day(s: Optional[str], cfg: C.Config) -> date:
    if not s:
        return cfg.now().date()
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise SystemExit(f"--date {s}: use YYYY-MM-DD")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.py extras",
                                 description="a clip's first comments, Instagram poll and trial flag (clipfactory/"
                                             "firstcomment.py, reelextras.py). Changes only while the clip waits for "
                                             "Approve.")
    sub = ap.add_subparsers(dest="action", required=True)
    q = sub.add_parser("status", help="each clip's first comments, poll and trial, and how they went")
    q.add_argument("account")
    q.add_argument("--date", help="YYYY-MM-DD (default: today and tomorrow)")
    q.add_argument("--quiet", action="store_true")

    def slot_args(q, by=True):
        q.add_argument("account")
        q.add_argument("--date", required=True, help="YYYY-MM-DD")
        q.add_argument("--slot", required=True, help="HH:MM")
        if by:
            q.add_argument("--by", help="who (default: the first approver)")
    q = sub.add_parser("comment", help="write one platform's first comment")
    slot_args(q)
    q.add_argument("--platform", required=True, help="facebook, instagram or youtube")
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--text-file", help="a file holding the words")
    g.add_argument("--text", help="the words")
    q.add_argument("--from", dest="how", default="task", choices=["task", "edited"],
                   help="task (the nightly run, the default) or edited (a person)")
    q = sub.add_parser("defaults", help="the default first comments from the clip's source")
    slot_args(q)
    q.add_argument("--write", action="store_true", help="put them on the clip (each platform whose switch is not off)")
    q = sub.add_parser("poll", help="the Instagram poll's options")
    slot_args(q)
    q.add_argument("--options", required=True, help='"Yes|No" (two to four, 25 characters each)')
    q = sub.add_parser("trial", help="post the reel as a trial reel, for a signed experiment's arm")
    slot_args(q)
    q.add_argument("--experiment", required=True)
    q.add_argument("--arm", required=True)
    q.add_argument("--graduation", default="SS_PERFORMANCE", choices=list(RX.GRADUATION))
    q = sub.add_parser("clear", help="take a first comment, the poll or the trial flag off")
    slot_args(q)
    q.add_argument("--platform", help="facebook, instagram or youtube (with --what first_comment)")
    q.add_argument("--what", default="first_comment", choices=["first_comment", "poll", "trial", "all"])
    return ap


def _by(cfg: C.Config, by: Optional[str]) -> str:
    return str(by or (cfg.approvers[0] if cfg.approvers else "factory.py"))


def _status(cfg: C.Config, acct, days: List[date]) -> List[str]:
    out: List[str] = []
    outs = outlets(acct)
    reel = (f"; poll {SW.mode(acct, RX.POLL)}, trial reels {SW.mode(acct, RX.TRIAL)}" if RX.has_reels(acct) else "")
    out.append(f"{acct.name}: first comments " + ", ".join(f"{NAMES[o]} {mode(acct, o)}" for o in outs) + reel)
    for d in days:
        bd = B.read(acct, d) or {}
        lines: List[str] = []
        for k, s in sorted((bd.get("slots") or {}).items()):
            if not isinstance(s, dict) or not s.get("clip"):
                continue
            data = EX.read(s)
            if not data and not (s.get("extras") or {}).get("path"):
                continue
            lines.append(f"  {k} {s.get('status')}" + ("" if data else ": its extras file is gone"))
            fc = data.get("first_comment") or {}
            for o in OUTLETS:
                ent = fc.get(o) if isinstance(fc, dict) else None
                if not isinstance(ent, dict):
                    continue
                state = "" if s.get("status") == B.READY else "  [" + state_line(cfg, acct, s, o)[1] + "]"
                lines.append(f"    {NAMES[o]} ({ent.get('from')}): {ent.get('text')}{state}")
            opts = RX.options(s)
            if opts:
                lines.append(f"    Instagram poll: {' / '.join(opts)}")
            tr = RX.trial(s)
            if tr:
                lines.append(f"    trial reel: experiment {tr.get('experiment')}, arm {tr.get('arm')}, "
                             f"{tr.get('graduation_strategy')}")
        n = sum(1 for ln in lines if not ln.startswith("    "))
        out.append(f"{d}: " + (f"{n} clip{'' if n == 1 else 's'} with extras" if n else "no clip with extras"))
        out += lines
    return out


def cli(cfg: C.Config, argv: List[str]) -> None:
    """factory.py extras status|comment|defaults|poll|trial|clear ..."""
    a = _parser().parse_args(list(argv))
    acct = _account(cfg, a.account)
    if a.action == "status":
        days = [_day(a.date, cfg)] if a.date else [cfg.now().date(), cfg.now().date() + timedelta(days=1)]
        for ln in _status(cfg, acct, days):
            print(ln)
        return None
    day = _day(a.date, cfg)
    key = C.normal_slot(a.slot)
    by = _by(cfg, a.by)
    if a.action == "comment":
        outlet = outlet_of(a.platform)
        if a.text_file:
            try:
                text = Path(a.text_file).expanduser().read_text()
            except OSError as e:
                raise SystemExit(f"--text-file: {e}")
        else:
            text = a.text
        t = set_comment(cfg, acct, day, key, outlet, text, by, how=a.how)
        print(f"{acct.name} {day} {key}: the first comment on {NAMES[outlet]} is now: {t}")
    elif a.action == "defaults":
        s = _slot_of(acct, day, key)
        outs = outlets(acct)
        texts = defaults(acct, s, outs, day)
        for o in outs:
            print(f"  {NAMES[o]}: {texts.get(o) or '(none: no source known)'}")
        if a.write:
            on = {o: t for o, t in texts.items() if mode(acct, o) != SW.OFF}
            if not on:
                print("nothing written: no platform with a default has its switch on")
                return None
            with EX.edit(cfg, acct, day, key, by, "defaults") as x:
                fc = dict(x.get("first_comment") or {})
                for o, t in on.items():
                    fc[o] = {"text": t, "from": "default"}
                x["first_comment"] = fc
            print(f"written for {', '.join(NAMES[o] for o in on)}")
    elif a.action == "poll":
        print(RX.set_poll(cfg, acct, day, key, RX.parse_options(a.options), by, how="task"))
    elif a.action == "trial":
        print(RX.set_trial(cfg, acct, day, key, a.experiment, a.arm, by, graduation=a.graduation))
    elif a.action == "clear":
        if a.what == "all":
            with EX.edit(cfg, acct, day, key, by, "clear all") as x:
                for part in list(x):
                    x.pop(part, None)
            print(f"{acct.name} {day} {key}: no first comment, poll or trial now")
        elif a.what in ("poll", "trial"):
            print(RX.clear(cfg, acct, day, key, a.what, by))
        else:
            if not a.platform:
                raise SystemExit("clear: which --platform (facebook, instagram or youtube), or --what all")
            outlet = outlet_of(a.platform)
            had = clear_comment(cfg, acct, day, key, outlet, by)
            print(f"{acct.name} {day} {key}: " + (f"no first comment on {NAMES[outlet]} now" if had else
                                                  f"there was no first comment on {NAMES[outlet]}"))
    return None
