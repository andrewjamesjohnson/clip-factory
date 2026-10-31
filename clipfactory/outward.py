"""The one door out of the social layer: every comment, reply, private reply, message, hide, story and card goes here.

    queue -> check -> politics -> execute -> log

A part never calls a platform to speak for the account itself. It queues an action (queue), and the sender (drain,
from the 5-minute social tick or a click's kick) takes each one when it is due: check() says whether it may go now,
later, or never; the politics (keys.json) is written "sending" before the call, so a crash or a lost answer is looked
up, never sent twice; the part's executor makes the call outside every lock; the result goes in the log. The poster
is not rerouted through here: it writes its post rows here and obeys the holds only when the account's guard switch
is on.

    state/social/stop.json                    Stop everything (this machine): {"by", "at", "why"}
    state/social/youtube-units.json           this machine's YouTube units today (Pacific day)
    state/<slug>/outward/queue.json           {"items": {id: action}} still to go
    state/<slug>/outward/keys.json            the politics: {key: {id, state, at, platform, kind, result_id}}
    state/<slug>/outward/holds.json           {"holds": {"instagram|comment": {until, why, code, at, by, releasable,
                                              kind}}}
    state/<slug>/outward/guard.json           recent sends per kind, spacing, words sent, usage headers, failures
    state/<slug>/outward/actions-YYYY-MM.jsonl  the log, one row per thing that happened
    state/<slug>/outward/.lock                the account's outward lock (never held with a board lock)

check() runs the steps in this order: Stop; the switch (off refuses, practice practises); the kind's rules (hide is
only ever a click; YouTube takes nothing automated but the first comment signed with the clip); the permission; messages
reach only app testers until Meta's review; testers mode; the warm-up; holds; YouTube units; the windows (quiet hours,
a private reply within 6 days, a message within 23 hours, YouTube only once public, stories by day); the caps (the
kind's hour and day, its family, all engagement, Instagram's publishes); the spacing; the words. Practice runs every
step with its own counters, so practice paces like the real thing, and logs the exact words with no network call.

A tripwire (Meta's numeric codes first, then its three phrases) writes a hold per account, platform and family: a red
line on the page, a sentence in the morning notice, a doctor NO. The numbers are architecture section 5.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import random
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, NamedTuple, Optional, Sequence, Tuple

from . import config as C
from . import registry as REG
from . import store
from . import switches as SW

try:
    from zoneinfo import ZoneInfo
    PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:  # noqa: BLE001  (no tz data: a fixed UTC-8 is near enough for a unit budget)
    PACIFIC = timezone(timedelta(hours=-8))

RNG = random.Random()                 # the jitter; tests seed it

# ------------------------------------------------------------------------------------------------------ the kinds
PLATFORMS = ("facebook", "instagram", "youtube")
OUTLET_PLAT = {"facebook_reels": "facebook", "instagram_reels": "instagram", "youtube_shorts": "youtube"}
DEFAULT_OUTLET = {v: k for k, v in OUTLET_PLAT.items()}
FAMILIES: Dict[str, Tuple[str, ...]] = {
    "publish": ("post", "story", "card_post"),
    "comment": ("first_comment", "reply_public"),
    "message": ("reply_private", "dm"),
    "moderation": ("hide", "unhide", "yt_moderate", "like"),
    "read": ("read",),
}
FAMILY_OF = {k: f for f, ks in FAMILIES.items() for k in ks}
KINDS = tuple(FAMILY_OF)
MANUAL_ONLY = ("hide", "unhide", "yt_moderate", "like")
ENGAGEMENT = ("comment", "message", "moderation")
WARMUP_KINDS = ("reply_public", "reply_private", "dm")          # automated engagement waits out the warm-up
QUIET_FAMILIES = ("comment", "message")                        # automated ones wait out the quiet hours
DAYTIME_KINDS = ("story", "card_post")                         # stories and cards go 08:00 to 23:30
DAYTIME = ((8, 0), (23, 30))
TEXT_KINDS = ("first_comment", "reply_public", "reply_private", "dm")
CHAR_LIMIT = {"instagram": 2200, "facebook": 8000, "youtube": 10000}
MESSAGE_BYTES = 1000                                           # Meta's Send API
URL_INTENTS = ("asks_source", "asks_full", "part2")            # the only messages a link may go in
DASHES = ("—", "–")
_URL = re.compile(r"(https?://|www\.)\S+", re.I)
STATES = ("queued", "practice", "sending", "done", "failed", "skipped", "lookup", "gave_up", "held", "watch",
          "refused")
FAMILY_WORDS = {"publish": "posts and stories", "comment": "comments and replies", "message": "messages",
                "moderation": "hiding comments", "read": "reading", "*": "everything social"}
NO_POST_WORDS = {"publish": "stories and card posts", "*": "everything social but posts"}   # the guard not on
PLAT_WORDS = {"facebook": "Facebook", "instagram": "Instagram", "youtube": "YouTube"}
KIND_WORDS = {"post": "posts", "story": "stories", "card_post": "card posts", "first_comment": "first comments",
              "reply_public": "replies", "reply_private": "private replies", "dm": "messages", "hide": "hides",
              "unhide": "unhides", "yt_moderate": "YouTube moderations", "like": "likes", "read": "reads"}

# ------------------------------------------------------------------------------------------------------ the numbers
LOST_AFTER = timedelta(minutes=10)         # "sending" this long with no answer: look it up
RETRY_AFTER = timedelta(minutes=5)
MAX_TRIES = 3
MAX_LOOKUPS = 3
MAX_WAITS = 36                             # "not ready yet" (direct.Pending): looked again every 5 minutes, 3 hours
RESEND_ONCE = ("first_comment", "reply_public", "hide", "unhide", "like")     # not found after a lost answer: sent once more
PRIVATE_REPLY_DAYS = 6                     # Meta allows 7
DM_WINDOW = timedelta(hours=23)            # Meta allows 24
DEFAULT_EXPIRES = timedelta(hours=20)
POLITICS_DAYS = 120
LOG_TEXT_DAYS = 90
YT_WRITE_UNITS = 50
SLOW_PCT, READS_PCT = 75.0, 90.0           # BUC: slow down at 75%, stop reading at 90%
FAIL_WINDOW, FAIL_LIMIT, FAIL_HOLD = 3600, 3, 6 * 3600
DAY_S, HOUR_S = 86400, 3600
FORGOTTEN = "[forgotten]"


class Verdict(NamedTuple):
    """What check() says: ok False is a no (code says which step); ok True with a not_before later than now is a
    wait; mode is live or practice; why is the words (a practice note, a wait, a refusal)."""
    ok: bool
    mode: str
    why: str
    not_before: Optional[datetime]
    code: str


# ------------------------------------------------------------------------------------------------------ small helpers
def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def _is_client(acct) -> bool:
    raw = getattr(acct, "raw", None)
    return isinstance(raw, dict) and str(raw.get("owner") or "self") == "client"


def _account(cfg: C.Config, acct) -> C.Account:
    if isinstance(acct, str):
        a = cfg.accounts.get(acct) or cfg.all_accounts.get(acct)
        if a is None:
            raise SystemExit(f"{acct}: not an account in config/accounts.json")
        return a
    return acct


def _accounts(cfg: C.Config) -> List[C.Account]:
    """The accounts this sender works for: switched on, on this machine, never a client's."""
    return [a for a in cfg.accounts.values() if not _is_client(a)]


def _now(now: Optional[datetime] = None) -> datetime:
    t = now or datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return _now(t).replace(microsecond=0).isoformat()


def _when(v) -> Optional[datetime]:
    return store._when(v) if v not in (None, "") else None


def _local(cfg: C.Config, t: datetime) -> datetime:
    tz = cfg.tz() if cfg is not None else None
    return t.astimezone(tz) if tz else t


def _hhmm(cfg: C.Config, t: datetime, now: Optional[datetime] = None) -> str:
    lt = _local(cfg, t)
    ln = _local(cfg, _now(now))
    return lt.strftime("%H:%M") if lt.date() == ln.date() else lt.strftime("%b %d %H:%M")


def plat_of(x) -> str:
    """facebook, instagram or youtube for a platform, an outlet (instagram_reels) or a card target (facebook_story);
    '' for anything else (TikTok has no guard)."""
    x = str(x or "")
    if x in PLATFORMS:
        return x
    if x in OUTLET_PLAT:
        return OUTLET_PLAT[x]
    head = x.partition("_")[0]
    return head if head in PLATFORMS else ""


def _text_sha(text: str) -> str:
    """The words with case and spacing evened out: the same reply typed twice is the same words."""
    return hashlib.sha256(" ".join(str(text or "").lower().split()).encode()).hexdigest()[:24]


def _sha(text: str) -> str:
    return hashlib.sha256(str(text or "").encode()).hexdigest()


def _is_manual(item: dict) -> bool:
    """A click is his own act: the action was signed by the click itself."""
    return str((item.get("signed") or {}).get("what") or "") == "click"


# ------------------------------------------------------------------------------------------------------ the files
def _dir(slug: str) -> Path:
    return store.path(slug, "outward")


def _queue_path(slug: str) -> Path:
    return _dir(slug) / "queue.json"


def _keys_path(slug: str) -> Path:
    return _dir(slug) / "keys.json"


def _holds_path(slug: str) -> Path:
    return _dir(slug) / "holds.json"


def _guard_path(slug: str) -> Path:
    return _dir(slug) / "guard.json"


def _month_path(slug: str, t: datetime) -> Path:
    return _dir(slug) / f"actions-{_now(t).strftime('%Y-%m')}.jsonl"


def _stop_path() -> Path:
    return store.path("social", "stop.json")


def _units_path() -> Path:
    return store.path("social", "youtube-units.json")


def _read(p: Path) -> dict:
    d = store.read(p, {})
    return d if isinstance(d, dict) else {}


@contextlib.contextmanager
def _locked(slug: str) -> Iterator[None]:
    """The account's outward lock (state/<slug>/outward/.lock): one sender at a time per account. Never taken with a
    board lock held, and never held across a platform call."""
    p = store._inside(_dir(slug) / ".lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _items(slug: str) -> Dict[str, dict]:
    got = _read(_queue_path(slug)).get("items")
    return {k: v for k, v in got.items() if isinstance(v, dict)} if isinstance(got, dict) else {}


def _queue_get(slug: str, aid: str) -> Optional[dict]:
    got = _items(slug).get(aid)
    return dict(got) if got else None


def _queue_put(slug: str, item: dict) -> None:
    with store.edit(_queue_path(slug), dict) as q:
        items = q.get("items") if isinstance(q.get("items"), dict) else {}
        items[item["id"]] = item
        q["items"] = items


def _queue_drop(slug: str, aid: str) -> None:
    with store.edit(_queue_path(slug), dict) as q:
        items = q.get("items") if isinstance(q.get("items"), dict) else {}
        items.pop(aid, None)
        q["items"] = items


def _politics(slug: str) -> Dict[str, dict]:
    return {k: v for k, v in _read(_keys_path(slug)).items() if isinstance(v, dict)}


def key_state(cfg: C.Config, acct, key: str) -> str:
    """The politics's word for a key ('' when it was never queued): queued, sending, done, practice, skipped, refused,
    gave_up. A part reads it to know a first comment went, or was practised."""
    return str((_politics(_slug(acct)).get(key) or {}).get("state") or "")


def _set_key(slug: str, key: str, state: str, item: dict, now: datetime, result_id=None) -> None:
    with store.edit(_keys_path(slug), dict) as k:
        old = k.get(key) if isinstance(k.get(key), dict) else {}
        k[key] = {"id": item.get("id"), "state": state, "at": _iso(now), "platform": item.get("platform"),
                  "kind": item.get("kind"), "result_id": result_id if result_id is not None else old.get("result_id")}


def _guard(slug: str) -> dict:
    return _read(_guard_path(slug))


@contextlib.contextmanager
def _guard_edit(slug: str) -> Iterator[dict]:
    with store.edit(_guard_path(slug), dict) as g:
        yield g


# ------------------------------------------------------------------------------------------------------ the log
def record(cfg: C.Config, acct, platform, kind: str, *, state: str, **fields) -> None:
    """One row in the account's log (state/<slug>/outward/actions-YYYY-MM.jsonl): t, the account, platform, kind, mode
    (live or practice), state, then the action's own fields and any of result, error, usage, why, would_at, endpoint."""
    slug = _slug(acct)
    t = _now(fields.pop("now", None))
    row = {"t": _iso(t), "acct": slug, "platform": plat_of(platform) or str(platform or ""), "kind": kind,
           "mode": str(fields.pop("mode", "") or "live"), "state": state}
    for k, v in fields.items():
        if k not in row:
            row[k] = v
    store.append(_month_path(slug, t), row)


def _row(cfg: C.Config, slug: str, item: dict, state: str, mode: str, now: datetime, **extra) -> None:
    fields = {k: v for k, v in item.items()
              if k not in ("acct", "platform", "kind", "state", "mode", "t", "text_mark", "count_mark")}
    fields.update(extra)
    record(cfg, slug, item.get("platform"), str(item.get("kind") or ""), state=state, mode=mode, now=now, **fields)


def _error_fields(err: Optional[BaseException]) -> Optional[dict]:
    if err is None:
        return None
    try:
        from . import graph as G
        i = G.classify(err)
        return {"http": i.http, "code": i.code, "subcode": i.subcode, "reason": i.reason, "kind": i.kind,
                "msg": i.msg, "trace": i.trace}
    except Exception:  # noqa: BLE001
        return {"kind": "other", "msg": str(err)[:400]}


def _month_files(slug: str) -> List[Path]:
    d = _dir(slug)
    return sorted(d.glob("actions-*.jsonl")) if d.is_dir() else []


def rows(cfg: C.Config, slug=None, since=None, until=None, kinds=None, states=None) -> Iterator[dict]:
    """The log's rows, oldest first: one account's (an Account or a slug) or every account's, between since and until
    (datetimes, dates or ISO times), of these kinds and states only when given."""
    slugs = [_slug(slug)] if slug else [a.slug for a in _accounts(cfg)]
    lo = _when(since) if since is not None else None
    hi = _when(until) if until is not None else None
    kinds = set(kinds) if kinds else None
    states = set(states) if states else None
    for s in slugs:
        for f in _month_files(s):
            month = f.stem[len("actions-"):]
            if lo is not None and month < lo.strftime("%Y-%m"):
                continue
            if hi is not None and month > hi.strftime("%Y-%m"):
                continue
            for r in store.rows(f, since=lo):
                if hi is not None:
                    t = _when(r.get("t"))
                    if t is None or t > hi:
                        continue
                if kinds is not None and r.get("kind") not in kinds:
                    continue
                if states is not None and r.get("state") not in states:
                    continue
                yield r


# ------------------------------------------------------------------------------------------------------ Stop
def stop(cfg: C.Config, by: str, why: str = "") -> str:
    """Stop everything on this machine: no comment, reply, message, hide, story or card goes out until start().
    Signed clips keep posting. What it says."""
    if not str(by or "").strip():
        raise SystemExit("Stop everything needs who stops it (--by NAME)")
    store.write(_stop_path(), {"by": str(by), "at": _iso(_now()), "why": str(why or "")[:300]})
    return (f"Stopped by {by}: no comment, reply, message, story or card goes out until Start again. Signed clips "
            f"keep posting.")


def start(cfg: C.Config, by: str) -> str:
    """Start everything again after a stop. What waited goes out at its turn (anything past its time is dropped)."""
    p = _stop_path()
    if not p.exists():
        return "Nothing was stopped."
    p.unlink()
    return f"Started again by {by}: what was waiting goes out at its turn."


def stopped(cfg: C.Config) -> str:
    """'' when the social layer may send; else the words for the red line (who stopped it, when, why)."""
    p = _stop_path()
    if not p.exists():
        return ""
    d = _read(p)
    at = _when(d.get("at"))
    when = f" at {_hhmm(cfg, at)}" if at is not None else ""
    why = str(d.get("why") or "").strip().rstrip(".")
    return (f"Stop everything is on (by {d.get('by') or 'someone'}{when}{': ' + why if why else ''}): nothing social "
            f"goes out. Signed clips keep posting.")


# ------------------------------------------------------------------------------------------------------ holds
def _holds(slug: str) -> Dict[str, dict]:
    got = _read(_holds_path(slug)).get("holds")
    return {k: v for k, v in got.items() if isinstance(v, dict)} if isinstance(got, dict) else {}


def _active(slug: str, keys: Sequence[str], now: datetime) -> Optional[Tuple[str, dict]]:
    """The hold among these keys that runs longest, or None."""
    best: Optional[Tuple[str, dict]] = None
    for k, h in _holds(slug).items():
        if k not in keys:
            continue
        until = _when(h.get("until"))
        if until is None or until <= now:
            continue
        if best is None or until > _when(best[1].get("until")):
            best = (k, h)
    return best


def active_holds(cfg: C.Config, acct, now: Optional[datetime] = None) -> List[Tuple[str, dict]]:
    """Every hold still running for the account, by key."""
    now = _now(now)
    return sorted((k, h) for k, h in _holds(_slug(acct)).items()
                  if _when(h.get("until")) is not None and _when(h.get("until")) > now)


def _hold_keys(platform: str, kind: str) -> List[str]:
    fam = FAMILY_OF.get(kind, kind)
    return [f"{platform}|{fam}", f"{platform}|{kind}", f"{platform}|*"]


def hold_words(cfg: C.Config, key: str, h: dict, now: Optional[datetime] = None, posts: bool = True) -> str:
    """'Instagram comments and replies paused until 14:20: Meta said it was blocked (368)'. posts=False when the
    posts do not obey it (the guard is not on): a publish hold then pauses stories and card posts only, and an
    everything hold everything but the posts."""
    plat, _, fam = key.partition("|")
    what = FAMILY_WORDS.get(fam) or KIND_WORDS.get(fam, fam)
    if not posts and fam in NO_POST_WORDS:
        what = NO_POST_WORDS[fam]
    until = _when(h.get("until"))
    when = f" until {_hhmm(cfg, until, now)}" if until is not None else ""
    why = str(h.get("why") or "").strip().rstrip(".")
    return f"{PLAT_WORDS.get(plat, plat)} {what} paused{when}" + (f": {why}" if why else "")


def held(cfg: C.Config, acct, platform, kind: str, now: Optional[datetime] = None) -> str:
    """'' when no hold covers this kind on this platform; else the hold's words."""
    now = _now(now)
    plat = plat_of(platform)
    got = _active(_slug(acct), _hold_keys(plat, kind), now)
    return hold_words(cfg, got[0], got[1], now) if got else ""


def hold(cfg: C.Config, acct, platform, family: str, seconds: float, why: str, code=None, releasable: bool = True,
         by: str = "guard", now: Optional[datetime] = None, kind: str = "") -> dict:
    """Pause one family (or one kind, or "*") on one platform of one account for this long. A hold already running
    longer is kept; a hold that may not be released early stays so. The hold, with its "key"."""
    now = _now(now)
    plat = plat_of(platform) or str(platform)
    key = f"{plat}|{family}"
    until = now + timedelta(seconds=max(60.0, float(seconds)))
    with store.edit(_holds_path(_slug(acct)), dict) as d:
        holds = d.get("holds") if isinstance(d.get("holds"), dict) else {}
        old = holds.get(key) if isinstance(holds.get(key), dict) else None
        old_until = _when(old.get("until")) if old else None
        running = old is not None and old_until is not None and old_until > now
        free = bool(releasable) and (not running or bool(old.get("releasable", True)))   # type: ignore
        if running and old_until > until:                                                 # type: ignore
            h = dict(old, releasable=free)                                                # type: ignore
        else:
            h = {"until": _iso(until), "why": str(why)[:300], "code": code, "at": _iso(now), "by": str(by),
                 "releasable": free, "kind": str(kind or "")}
        holds[key] = h
        d["holds"] = holds
    return dict(h, key=key)


def release(cfg: C.Config, acct, hold_key: str, by: str, now: Optional[datetime] = None) -> str:
    """End a hold early, by hand. Refused for a hold Meta or YouTube set that may not be cut short."""
    a = _account(cfg, acct)
    now = _now(now)
    if not str(by or "").strip():
        raise SystemExit("release needs who releases it (--by NAME)")
    with store.edit(_holds_path(a.slug), dict) as d:
        holds = d.get("holds") if isinstance(d.get("holds"), dict) else {}
        h = holds.get(hold_key)
        until = _when(h.get("until")) if isinstance(h, dict) else None
        if not isinstance(h, dict) or until is None or until <= now:
            raise SystemExit(f"{a.name}: no hold {hold_key} is running (factory.py social holds {a.slug})")
        if not h.get("releasable", True):
            raise SystemExit(f"{a.name}: {hold_key} cannot be released early: it ends by itself at "
                             f"{_hhmm(cfg, until, now)}")
        holds.pop(hold_key)
        hist = d.get("history") if isinstance(d.get("history"), list) else []
        hist.append(dict(h, key=hold_key, released_by=str(by), released_at=_iso(now)))
        d["history"] = hist[-100:]
        d["holds"] = holds
    return f"{a.name}: {hold_key} released by {by}"


def _meta_hold_until(acct) -> Tuple[str, Optional[datetime]]:
    """The poster's own Meta hold (state/<slug>/meta-hold.json): its words and when it ends."""
    try:
        from . import outlets as O
        words = O.meta_hold(acct)
        if not words:
            return "", None
        d = _read(C.STATE / _slug(acct) / "meta-hold.json")
        until = datetime.fromtimestamp(float(d.get("until") or 0), timezone.utc)
        return words, until
    except Exception:  # noqa: BLE001
        return "", None


# ------------------------------------------------------------------------------------------------------ counting
def _bump(slug: str, platform: str, kind: str, manual: bool, now: datetime, practice: bool,
          gap_s: int = 0, text: str = "") -> Optional[float]:
    """Count one send (or one practised send) for the caps, set the next spacing, keep the words' digest. When
    there are words: when the same words were last kept before this (None for never), so _unmark_text can put it
    back if this send never went out."""
    ts = _now(now).timestamp()
    prev: Optional[float] = None
    with _guard_edit(slug) as g:
        sect = "practice" if practice else "recent"
        rec = g.get(sect) if isinstance(g.get(sect), dict) else {}
        k = f"{platform}|{kind}|{'manual' if manual else 'auto'}"
        rec[k] = [x for x in (rec.get(k) or []) if isinstance(x, (int, float)) and ts - x < DAY_S] + [ts]
        g[sect] = rec
        fam = FAMILY_OF.get(kind, "")
        if gap_s and fam:
            nx = g.get("next_practice" if practice else "next")
            nx = nx if isinstance(nx, dict) else {}
            nx[f"{platform}|{fam}"] = max(float(nx.get(f"{platform}|{fam}") or 0), ts + gap_s)
            g["next_practice" if practice else "next"] = nx
        if text:
            tk = "texts_practice" if practice else "texts"
            tx = g.get(tk) if isinstance(g.get(tk), dict) else {}
            tx = {s: v for s, v in tx.items() if isinstance(v, (int, float)) and ts - v < DAY_S}
            prev = tx.get(_text_sha(text))
            tx[_text_sha(text)] = ts
            g[tk] = tx
    return prev


def _unmark_text(slug: str, item: dict) -> None:
    """The words did not go out (a clear refusal, or not found after a lost answer): their digest comes off the
    24-hour repeat check, back to what it was before this send, so a try again or Send again is not refused as words
    that went out. Only the digest this send wrote is touched; the caps and the spacing stay counted."""
    mark = item.pop("text_mark", None)
    text = str(item.get("text") or "")
    if not text or not isinstance(mark, list) or len(mark) != 2:
        return
    ts, prev = mark
    sha = _text_sha(text)
    with _guard_edit(slug) as g:
        tx = g.get("texts") if isinstance(g.get("texts"), dict) else {}
        if tx.get(sha) != ts:
            return                                  # a later send of the same words owns it now
        if isinstance(prev, (int, float)) and not isinstance(prev, bool):
            tx[sha] = prev
        else:
            tx.pop(sha, None)
        g["texts"] = tx


def _uncount(slug: str, item: dict) -> None:
    """It never reached the platform (offline) or the platform was not ready for it (direct.Pending): the send this
    try counted for the caps comes off again, so a wait never uses up the day's caps. The spacing stays."""
    mark = item.pop("count_mark", None)
    if not isinstance(mark, list) or len(mark) != 2:
        return
    k, ts = mark
    with _guard_edit(slug) as g:
        rec = g.get("recent") if isinstance(g.get("recent"), dict) else {}
        have = rec.get(k)
        if isinstance(have, list) and ts in have:
            have.remove(ts)
            rec[k] = have
            g["recent"] = rec


def _stamps(g: dict, platform: str, kinds: Sequence[str], hows: Sequence[str], window_s: float, now: datetime,
            practice: bool) -> List[float]:
    ts = _now(now).timestamp()
    rec = g.get("practice" if practice else "recent")
    rec = rec if isinstance(rec, dict) else {}
    out: List[float] = []
    for k in kinds:
        for h in hows:
            out += [float(x) for x in rec.get(f"{platform}|{k}|{h}") or []
                    if isinstance(x, (int, float)) and 0 <= ts - x < window_s]
    return sorted(out)


def counts(cfg: C.Config, acct, platform, kind_or_family: str, window_s: float, now: Optional[datetime] = None,
           practice: bool = False) -> int:
    """How many sends of a kind, a family, "engagement" (comments, messages and moderation) or "*" went out on this
    platform in the last window_s seconds (practised ones with practice=True)."""
    kinds = _kinds_of(kind_or_family)
    return len(_stamps(_guard(_slug(acct)), plat_of(platform), kinds, ("auto", "manual"), window_s, _now(now),
                       practice))


def _kinds_of(kind_or_family: str) -> Tuple[str, ...]:
    if kind_or_family == "engagement":
        return tuple(k for f in ENGAGEMENT for k in FAMILIES[f])
    if kind_or_family == "*":
        return KINDS
    return FAMILIES.get(kind_or_family) or (kind_or_family,)


# ------------------------------------------------------------------------------------------------------ usage
def slow(cfg: C.Config, acct, platform, now: Optional[datetime] = None) -> bool:
    """Meta's Business Use Case usage was 75% or more within the hour: polls slow down and the caps halve."""
    now = _now(now)
    plat = plat_of(platform)
    g = _guard(_slug(acct))
    su = (g.get("slow_until") or {}).get(plat) if isinstance(g.get("slow_until"), dict) else None
    if isinstance(su, (int, float)) and su > now.timestamp():
        return True
    u = (g.get("usage") or {}).get(plat) if isinstance(g.get("usage"), dict) else None
    if isinstance(u, dict) and isinstance(u.get("buc_pct"), (int, float)) and u["buc_pct"] >= SLOW_PCT:
        at = _when(u.get("at"))
        return at is not None and now - at < timedelta(hours=1)
    return False


META_HOSTS = ("facebook.com", "instagram.com")
GOOGLE_HOSTS = ("googleapis.com", "google.com")


def _host_in(host: str, domains: Sequence[str]) -> bool:
    h = str(host or "").lower()
    return any(h == d or h.endswith("." + d) for d in domains)


def usage_for(platform, usage) -> dict:
    """What direct.last_usage() said, only when it is this platform's own: Meta's numbers for Facebook and Instagram
    (an answer with no host is taken as Meta's, as every one of Meta's has its host), nothing of Meta's for YouTube.
    A YouTube upload goes through urllib, not direct._http, so the thread's last numbers may still be an earlier
    Facebook post's: they never become a YouTube hold."""
    if not isinstance(usage, dict) or not usage:
        return {}
    plat = plat_of(platform)
    host = str(usage.get("host") or "")
    if plat in ("facebook", "instagram"):
        return dict(usage) if not host or _host_in(host, META_HOSTS) else {}
    if plat == "youtube" and _host_in(host, GOOGLE_HOSTS) and "retry_after" in usage:
        return {"host": host, "retry_after": usage["retry_after"]}
    return {}


def _regain_hold(cfg: C.Config, a, plat: str, regain: float, code, now: datetime) -> dict:
    mins = max(30.0, float(regain) + 15)
    return hold(cfg, a, plat, "*", mins * 60, f"Meta asked us to wait {int(round(regain))} minutes (too many calls)",
                code=code, releasable=False, now=now, kind="buc")


def note_usage(cfg: C.Config, acct, platform, usage: dict, now: Optional[datetime] = None) -> str:
    """Keep what a platform's answer said about its rate limits, and act on it: a regain time pauses everything on
    that platform until then (plus 15 minutes, 30 at the least); 90% stops the reads for an hour; 75% slows down for
    an hour. Nothing at all while every social part of the account is off, or when the numbers are not this
    platform's own (usage_for). A hold only when something obeys it (the guard on, or another part on): with the
    guard on watch alone, a "watch" row says what it would have paused. What it did, '' for nothing."""
    usage = usage_for(platform, usage)
    if not usage:
        return ""
    a = _account(cfg, acct)
    if _is_client(a) or not SW.any_on(a):
        return ""
    now = _now(now)
    plat = plat_of(platform)
    if not plat:
        return ""
    keep = {k: usage[k] for k in ("buc_pct", "regain_min", "app_pct", "page_pct", "retry_after") if k in usage}
    keep["at"] = _iso(now)
    said: List[str] = []
    buc = usage.get("buc_pct")
    with _guard_edit(a.slug) as g:
        u = g.get("usage") if isinstance(g.get("usage"), dict) else {}
        u[plat] = keep
        g["usage"] = u
        if isinstance(buc, (int, float)) and buc >= SLOW_PCT:
            su = g.get("slow_until") if isinstance(g.get("slow_until"), dict) else {}
            su[plat] = now.timestamp() + HOUR_S
            g["slow_until"] = su
            said.append(f"{PLAT_WORDS.get(plat, plat)} at {int(buc)}% of Meta's limit: slower for an hour")
    regain = usage.get("regain_min")
    plan = None                          # (family, seconds, why)
    if isinstance(regain, (int, float)) and regain > 0:
        plan = ("*", max(30.0, float(regain) + 15) * 60,
                f"Meta asked us to wait {int(round(regain))} minutes (too many calls)")
    elif isinstance(buc, (int, float)) and buc >= READS_PCT:
        plan = ("read", HOUR_S, f"Meta's limit at {int(buc)}%")
    if plan is None:
        return "; ".join(said)
    if not _holds_obeyed(a):             # the guard on watch alone: nothing would obey a hold, so none is written
        fam, secs, why = plan
        words = f"would pause {FAMILY_WORDS.get(fam, fam)} for {int(secs // 60)} minutes: {why}"
        record(cfg, a, plat, "post", state="watch", mode="live", now=now, key=f"usage:{plat}", runner="poster",
               origin={"by": "guard", "rule": None, "via": None}, why=words, usage=keep)
        said.append(f"{PLAT_WORDS.get(plat, plat)}: {words}")
        return "; ".join(said)
    if plan[0] == "*":
        h = _regain_hold(cfg, a, plat, regain, None, now)
    else:
        h = hold(cfg, a, plat, "read", HOUR_S, plan[2], releasable=False, now=now, kind="buc")
    said.append(hold_words(cfg, h["key"], h, now, posts=_guard_mode(a) == "on"))
    return "; ".join(said)


def _holds_obeyed(a) -> bool:
    """Whether anything on the account obeys a hold: the posts with the guard on, the other parts while they are on.
    The guard on watch obeys none (watch only logs)."""
    try:
        return any(v != SW.OFF and (k != "guard" or v == "on") for k, v in SW.modes(a).items())
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------------------------------------------ YouTube units
def _pt_day(now: datetime) -> str:
    return _now(now).astimezone(PACIFIC).date().isoformat()


def _pt_reset(now: datetime) -> datetime:
    """00:15 Pacific after now: when YouTube's quota day has turned over."""
    lt = _now(now).astimezone(PACIFIC)
    nxt = datetime(lt.year, lt.month, lt.day, 0, 15, tzinfo=PACIFIC) + timedelta(days=1)
    if lt.hour == 0 and lt.minute < 15:
        nxt -= timedelta(days=1)
    return nxt.astimezone(timezone.utc)


def yt_spend(cfg: Optional[C.Config], units: int, method: str, write: bool, now: Optional[datetime] = None) -> None:
    """Count YouTube units against this machine's day (graph.py calls it after YouTube answered). Never raises."""
    try:
        now = _now(now)
        day = _pt_day(now)
        with store.edit(_units_path(), dict) as d:
            if d.get("day") != day:
                d.clear()
                d.update({"day": day, "reads": 0, "writes": 0, "units": 0, "by": {}})
            d["units"] = int(d.get("units") or 0) + int(units)
            k = "writes" if write else "reads"
            d[k] = int(d.get(k) or 0) + 1
            by = d.get("by") if isinstance(d.get("by"), dict) else {}
            by[str(method)] = int(by.get(str(method)) or 0) + int(units)
            d["by"] = by
    except Exception as e:  # noqa: BLE001  (counting never stops a call)
        REG._log_error("outward:yt_spend", e)


def yt_used(now: Optional[datetime] = None) -> int:
    d = _read(_units_path())
    return int(d.get("units") or 0) if d.get("day") == _pt_day(_now(now)) else 0


def yt_left(cfg: Optional[C.Config], now: Optional[datetime] = None) -> int:
    """The YouTube units this machine may still spend today (state/social/settings.json youtube_units, 2,500)."""
    return int(SW.global_settings()["youtube_units"]) - yt_used(now)  # type: ignore


# ------------------------------------------------------------------------------------------------------ check
def switch_of(kind: str, outlet: str = "", manual: bool = False) -> str:
    """The switch that governs a kind: first_comment.<outlet>, replies (clicks), automations (rules), cards.post,
    cards.story_repost, guard (posts), engage.read."""
    if kind == "first_comment":
        return "first_comment." + str(outlet or "")
    if kind == "story":
        return "cards.story_repost"
    if kind == "card_post":
        return "cards.post"
    if kind == "post":
        return "guard"
    if kind == "read":
        return "engage.read"
    if kind in MANUAL_ONLY:
        return "replies"
    if kind in ("reply_public", "reply_private", "dm"):
        return "replies" if manual else "automations"
    return ""


def _missing(a, plat: str, kind: str) -> List[str]:
    try:
        from . import capabilities as CAP
        need = CAP.NEED_OF_KIND.get(kind)
        if not need:
            return []
        return [str(x) for x in CAP.missing(a, plat, need) or []]
    except Exception as e:  # noqa: BLE001  (an unreadable permission file is a missing permission)
        return [f"(the permission check failed: {type(e).__name__})"]


def _tester(a, plat: str, person) -> bool:
    person = str(person or "")
    if not person:
        return False
    try:
        return SW.is_tester(a, person) if ":" in person else SW.is_tester(a, plat, person)
    except Exception:  # noqa: BLE001
        return False


def _daytime_wait(cfg: C.Config, now: datetime) -> Optional[datetime]:
    """Outside 08:00 to 23:30 local: the next 08:00. None inside."""
    lt = _local(cfg, now)
    (sh, sm), (eh, em) = DAYTIME
    s = lt.replace(hour=sh, minute=sm, second=0, microsecond=0)
    e = lt.replace(hour=eh, minute=em, second=0, microsecond=0)
    if lt < s:
        return s.astimezone(timezone.utc)
    if lt >= e:
        return (s + timedelta(days=1)).astimezone(timezone.utc)
    return None


def _person_of(target: dict) -> str:
    return str(target.get("to") or target.get("person") or "")


def check(cfg: C.Config, acct, platform, kind: str, *, origin=None, text: str = "", target: Optional[dict] = None,
          manual: bool = False, force: bool = False, signed: Optional[dict] = None, now: Optional[datetime] = None,
          force_practice: bool = False) -> Verdict:
    """May this go now, later, or never? The steps, in order: Stop, the switch, the kind's rules, the permission,
    messages' testers, testers mode, the warm-up, holds, YouTube units, windows, caps, spacing, the words. `platform`
    may be an outlet (instagram_reels): a first comment's switch is its outlet's."""
    a = _account(cfg, acct)
    now = _now(now)
    target = target if isinstance(target, dict) else {}
    origin = origin if isinstance(origin, dict) else {}
    signed = signed if isinstance(signed, dict) else {}
    plat = plat_of(platform)
    outlet = str(platform) if str(platform) in OUTLET_PLAT else str(target.get("outlet") or "")
    automated = not manual
    force = bool(force) and bool(manual)                     # force only ever lifts a click's caps

    # 1. Stop
    words = stopped(cfg)
    if words:
        return Verdict(False, "", words, None, "stop")
    if plat not in PLATFORMS or kind not in FAMILY_OF:
        return Verdict(False, "", f"{platform} {kind}: not something the social layer sends", None, "kind")
    fam = FAMILY_OF[kind]
    if kind == "first_comment" and not outlet:
        outlet = DEFAULT_OUTLET[plat]

    # 2. the switch
    key = switch_of(kind, outlet, manual)
    m = SW.mode(a, key) if key in SW.MODES else SW.OFF
    if m == SW.OFF:
        return Verdict(False, "off", f"{a.name}: {key or kind} is off", None, "off")
    mode = "practice" if (m == "practice" or force_practice) else "live"
    notes: List[str] = []

    def no(code: str, why: str) -> Verdict:
        return Verdict(False, mode, why, None, code)

    # 3. the kind's rules
    if automated and kind in MANUAL_ONLY:
        return no("kind", f"{KIND_WORDS[kind]} are only ever a click on the review page")
    if automated and plat == "youtube" and fam not in ("read",) and kind != "post" and \
            not (kind == "first_comment" and signed.get("what") == "clip"):
        return no("kind", "YouTube takes nothing automated but the first comment signed with the clip")

    # 4. the permission
    miss = _missing(a, plat, kind)
    if miss:
        if mode == "live":
            return no("perm", "waits for " + ", ".join(miss))
        notes.append("would wait for " + ", ".join(miss))

    # 5. messages: only app testers until Meta's review
    person = _person_of(target)
    if fam == "message" and SW.settings(a).get("messages_access") != "advanced" and not _tester(a, plat, person):
        if mode == "live":
            mode = "practice"
        notes.append("DMs reach only app testers until Meta's review")

    # 6. testers mode
    if automated and m == "testers" and not _tester(a, plat, person):
        if mode == "live":
            mode = "practice"
        notes.append("testers mode: not a tester")

    # 7. the warm-up
    if automated and kind in WARMUP_KINDS:
        try:
            wu = SW.warmup_until(cfg, a)
        except Exception:  # noqa: BLE001
            wu = None
        if wu is None or _local(cfg, now).date() < wu:
            mode = "practice"
            notes.append(f"warming up until {wu}" if wu else
                         f"warming up (factory.py social created {a.slug} YYYY-MM-DD, and the factory's first post)")
    practice = mode == "practice"
    code = ""
    waits: List[Tuple[datetime, str, str]] = []

    def wait(t: Optional[datetime], why: str, c: str) -> None:
        if t is not None and t > now:
            waits.append((t, why, c))

    # 8. holds
    got = _active(a.slug, _hold_keys(plat, kind), now)
    if got:
        w = hold_words(cfg, got[0], got[1], now)
        if practice:
            notes.append("would wait: " + w)
            code = code or "hold"
        else:
            wait(_when(got[1].get("until")), w, "hold")
    if plat in ("facebook", "instagram") and fam != "read":
        mw, mu = _meta_hold_until(a)
        if mw:
            if practice:
                notes.append("would wait: " + mw)
                code = code or "hold"
            else:
                wait(mu if mu is not None and mu > now else now + timedelta(hours=1), mw, "hold")

    # 9. YouTube units
    if plat == "youtube" and fam not in ("read", "publish") and yt_left(cfg, now) < YT_WRITE_UNITS:
        if practice:
            notes.append("YouTube units for today would be used up")
        else:
            wait(_pt_reset(now), "this machine's YouTube units for today are used up", "units")

    # 10. windows
    if automated and fam in QUIET_FAMILIES:
        try:
            q = SW.quiet(cfg, a, now)
        except Exception:  # noqa: BLE001
            q = None
        if q is not None:
            wait(_now(q), "quiet hours", "quiet")
    if automated and kind in DAYTIME_KINDS:
        wait(_daytime_wait(cfg, now), "stories and cards go between 08:00 and 23:30", "window")
    at = _when(target.get("at"))
    if kind == "reply_private" and at is not None and now - at > timedelta(days=PRIVATE_REPLY_DAYS):
        return no("window", f"a private reply only within {PRIVATE_REPLY_DAYS} days of the comment")
    if kind == "dm":
        last_in = _when(target.get("last_in_at")) or at
        if last_in is not None and now - last_in > DM_WINDOW:
            return no("window", "a message only within 23 hours of theirs")
    if plat == "youtube" and fam == "comment":
        wait(_when(target.get("live_at")), "YouTube comments wait until the video is public", "window")

    # 11. caps
    g = _guard(a.slug)
    how = "manual" if manual else "auto"
    ph, pd, gap, jit = SW.caps_for(a, kind, manual)
    if (kind, how) not in SW.HARD:
        return no("kind", f"{KIND_WORDS.get(kind, kind)} are never sent {'by hand' if manual else 'by themselves'}")
    if kind == "post":
        pd = min(pd, SW.POST_DAY.get(plat, pd))
    pd = min(pd, SW.PLATFORM_DAY.get((plat, kind, how), pd))
    if slow(cfg, a, plat, now):
        ph = max(1, ph // 2) if ph else ph
        pd = max(1, pd // 2) if pd else pd          # halved, never lifted: a cap of 0 stays 0
        notes.append("Meta's usage is high: caps halved")

    def full(stamps: List[float], per_hour: Optional[int], per_day: Optional[int], what: str) -> Optional[Verdict]:
        if per_day == 0 or per_hour == 0:           # the account's caps say never (no first send to wait on)
            return no("cap", f"{what}: the account's caps allow none (factory.py social set {a.slug} caps)")
        if per_day is not None:
            day = [x for x in stamps if now.timestamp() - x < DAY_S]
            if len(day) >= per_day:
                nxt = datetime.fromtimestamp(day[0] + DAY_S, timezone.utc)
                if automated:
                    return no("cap", f"{what}: {per_day} a day is the most (the next at {_hhmm(cfg, nxt, now)})")
                return no("cap", f"{what}: the day's {per_day} are used; the next can go at {_hhmm(cfg, nxt, now)} "
                                 f"(or send it anyway)")
        if per_hour is not None:
            hour = [x for x in stamps if now.timestamp() - x < HOUR_S]
            if len(hour) >= per_hour:
                wait(datetime.fromtimestamp(hour[0] + HOUR_S, timezone.utc), f"{what}: {per_hour} an hour is the most",
                     "cap")
        return None

    if not force:
        checks = [(_stamps(g, plat, (kind,), (how,), DAY_S, now, practice), ph, pd, KIND_WORDS.get(kind, kind))]
        if fam in SW.FAMILY_HARD:
            fh, fd = SW.FAMILY_HARD[fam]
            checks.append((_stamps(g, plat, FAMILIES[fam], ("auto", "manual"), DAY_S, now, practice), fh, fd,
                           FAMILY_WORDS[fam]))
        if fam in ENGAGEMENT:
            eh, ed = SW.FAMILY_HARD["engagement"]
            checks.append((_stamps(g, plat, _kinds_of("engagement"), ("auto", "manual"), DAY_S, now, practice), eh,
                           ed, "comments, replies, messages and hides together"))
        if plat == "instagram" and fam == "publish":
            checks.append((_stamps(g, plat, FAMILIES["publish"], ("auto", "manual"), DAY_S, now, practice), None,
                           SW.IG_PUBLISH_DAY, "Instagram publishes"))
        for stamps, per_hour, per_day, what in checks:
            v = full(stamps, per_hour, per_day, what)
            if v is not None:
                return v
    if kind == "story" or (kind == "card_post" and (target or {}).get("kind") != "news"):   # a news post goes at once
        reels = _stamps(g, plat, ("post",), ("auto", "manual"), SW.REEL_GAP_S, now, False)
        if reels:
            wait(datetime.fromtimestamp(reels[-1] + SW.REEL_GAP_S, timezone.utc),
                 "never within 30 minutes of a Reel", "gap")

    # 12. spacing
    nx = g.get("next_practice" if practice else "next")
    nxt = (nx or {}).get(f"{plat}|{fam}") if isinstance(nx, dict) else None
    if isinstance(nxt, (int, float)) and nxt > now.timestamp():
        jitter = RNG.randint(0, int(jit)) if jit else 0
        wait(datetime.fromtimestamp(nxt + jitter, timezone.utc), "spaced out", "gap")

    # 13. the words
    if kind in TEXT_KINDS:
        t = str(text or "")
        if not t.strip():
            return no("text", "no words to send")
        if fam == "message":
            if len(t.encode("utf-8")) > MESSAGE_BYTES:
                return no("text", f"a message is {MESSAGE_BYTES} bytes at the most")
        elif len(t) > CHAR_LIMIT[plat]:
            return no("text", f"a {PLAT_WORDS[plat]} comment is {CHAR_LIMIT[plat]} characters at the most")
        if automated and kind != "first_comment":
            if any(d in t for d in DASHES):
                return no("text", "the words have a long dash")
            if _URL.search(t) and not (fam == "message" and target.get("intent") in URL_INTENTS):
                return no("text", "a link only goes in a message that answers an ask for the source or the full video")
        if kind != "first_comment":
            seen = (g.get("texts_practice" if practice else "texts") or {})
            ts = seen.get(_text_sha(t)) if isinstance(seen, dict) else None
            if isinstance(ts, (int, float)) and now.timestamp() - ts < DAY_S:
                if automated:
                    return no("repeat", "the same words went out in the last 24 hours")
                if not force:
                    return no("repeat", "the same words went out in the last 24 hours (send it anyway, or change them)")

    if waits:
        t, why, c = max(waits, key=lambda w: w[0])
        return Verdict(True, mode, "; ".join([why] + notes), t, c)
    return Verdict(True, mode, "; ".join(notes), None, code)


# ------------------------------------------------------------------------------------------------------ queue
def _new_id(now: datetime) -> str:
    return f"oa-{_now(now).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2)}"


def queue(cfg: C.Config, acct, platform, kind: str, *, key: str, text: str, target: Optional[dict], origin: dict,
          signed: dict, outlet: str = "", runner: str = "social", not_before: Optional[datetime] = None,
          expires: Optional[datetime] = None, force_practice: bool = False, force: bool = False,
          now: Optional[datetime] = None) -> str:
    """Put one action in the account's queue for the sender. Refused (SystemExit) when its key is already queued,
    on its way, or done: nothing is ever sent twice. The action's id."""
    a = _account(cfg, acct)
    if _is_client(a):
        raise SystemExit(f"{a.name} is a client's account: the social layer is for the owner's own accounts")
    now = _now(now)
    if not outlet and str(platform) in OUTLET_PLAT:
        outlet = str(platform)
    plat = plat_of(platform) or plat_of(outlet)
    if plat not in PLATFORMS:
        raise SystemExit(f"{platform}: the social layer sends only to Facebook, Instagram and YouTube")
    if kind not in FAMILY_OF or kind in ("post", "read"):
        raise SystemExit(f"{kind}: not something the sender sends")
    if not str(key or "").strip():
        raise SystemExit("an action needs its politics key")
    if kind == "first_comment" and not outlet:
        outlet = DEFAULT_OUTLET[plat]
    text = str(text or "")
    with _locked(a.slug):
        led = _politics(a.slug).get(key) or {}
        if led.get("state") in ("queued", "sending", "done"):
            raise SystemExit(f"{a.name}: {key} is already {led['state']}: it is never sent twice")
        if any(i.get("key") == key and i.get("state") in ("queued", "sending") for i in _items(a.slug).values()):
            raise SystemExit(f"{a.name}: {key} is already queued: it is never sent twice")
        item = {"id": _new_id(now), "key": key, "acct": a.slug, "platform": plat, "outlet": str(outlet or ""),
                "kind": kind, "runner": str(runner or "social"),
                "origin": dict(origin or {}), "signed": dict(signed or {}),
                "target": dict(target or {}), "text": text, "text_sha": _sha(text) if text else "",
                "force_practice": bool(force_practice), "force": bool(force),
                "not_before": _iso(not_before) if not_before else _iso(now),
                "expires": _iso(expires) if expires else _iso(now + DEFAULT_EXPIRES),
                "state": "queued", "tries": 0, "lookups": 0, "lost_retry": 0, "at": _iso(now)}
        _queue_put(a.slug, item)
        _set_key(a.slug, key, "queued", item, now)
        _row(cfg, a.slug, item, "queued", "live", now)
    return item["id"]


def due(cfg: C.Config, acct, now: Optional[datetime] = None, runner: str = "social") -> List[dict]:
    """The account's actions for this runner whose time has come (and not past their expiry), oldest first, with any
    left "sending" with no answer for 10 minutes (the lost-answer look-up)."""
    now = _now(now)
    out = []
    for item in _items(_slug(acct)).values():
        if str(item.get("runner") or "social") != runner:
            continue
        st = item.get("state")
        if st == "queued":
            nb = _when(item.get("not_before"))
            if nb is not None and nb > now:
                continue
            out.append(item)
        elif st == "sending":
            sent = _when(item.get("sent_at"))
            if sent is None or now - sent >= LOST_AFTER:
                out.append(item)
    return sorted(out, key=lambda i: (str(i.get("not_before") or ""), str(i.get("at") or ""), str(i.get("id"))))


def _expired(item: dict, now: datetime) -> bool:
    ex = _when(item.get("expires"))
    return ex is not None and ex <= now and item.get("state") == "queued"


# ------------------------------------------------------------------------------------------------------ send
def _executor(kind: str):
    name = REG.EXECUTORS.get(kind)
    return REG.load(name) if name else None


def _finish(cfg: C.Config, slug: str, item: dict, state: str, mode: str, now: datetime, **extra) -> dict:
    """An action that will not go again: off the queue, its politics key set, its row written."""
    _queue_drop(slug, item["id"])
    _set_key(slug, item["key"], state, item, now, result_id=(extra.get("result") or {}).get("id"))
    _row(cfg, slug, item, state, mode, now, **extra)
    out = {"id": item["id"], "state": state, "why": str(extra.get("why") or "")}
    if extra.get("result"):
        out["result"] = extra["result"]
    return out


def _on_state(cfg: C.Config, a, item: dict, res: dict) -> None:
    exe = _executor(str(item.get("kind") or ""))
    f = getattr(exe, "on_state", None) if exe is not None else None
    if callable(f):
        try:
            f(cfg, a, item, res.get("state"), res.get("result"))
        except (Exception, SystemExit) as e:  # noqa: BLE001  (its refusal never stops the sender)
            REG._log_error(f"{REG.EXECUTORS.get(item.get('kind'))}:on_state", e)


def _plat_name(item: dict) -> str:
    return PLAT_WORDS.get(str(item.get("platform") or ""), str(item.get("platform") or "the platform"))


def send(cfg: C.Config, acct, action, now: Optional[datetime] = None) -> dict:
    """Send one queued action (an action dict or its id): the politics, check(), the executor's still_signed, practice
    or the politics's "sending", the call outside every lock, then the result. What happened: {"id", "state", "why",
    "result"?, "not_before"?}."""
    a = _account(cfg, acct)
    now = _now(now)
    aid = str(action.get("id") if isinstance(action, dict) else action)
    go = False
    lost = False
    res: dict = {}
    with _locked(a.slug):
        item = _queue_get(a.slug, aid)
        if item is None or item.get("state") not in ("queued", "sending"):
            return {"id": aid, "state": "gone", "why": "not in the queue"}
        led = _politics(a.slug).get(item["key"]) or {}
        manual = _is_manual(item)
        if led.get("state") == "done":           # it went: off the queue, the politics stays done
            _queue_drop(a.slug, item["id"])
            _row(cfg, a.slug, item, "skipped", "live", now, why="already sent")
            res = {"id": aid, "state": "skipped", "why": "already sent"}
        elif item.get("state") == "sending" or (led.get("state") == "sending" and led.get("id") == item["id"]):
            since = _when(item.get("sent_at")) or _when(led.get("at"))
            if since is not None and now - since < LOST_AFTER:
                return {"id": aid, "state": "sending", "why": "on its way"}
            lost = True
        elif _expired(item, now):
            res = _finish(cfg, a.slug, item, "skipped", "live", now, why="its time passed before it could go")
        else:
            v = check(cfg, a, item.get("outlet") or item["platform"], item["kind"], origin=item.get("origin"),
                      text=item.get("text", ""), target=item.get("target"), manual=manual,
                      force=bool(item.get("force")), signed=item.get("signed"), now=now,
                      force_practice=bool(item.get("force_practice")))
            if v.code == "stop":
                return {"id": aid, "state": "queued", "why": v.why}
            if not v.ok:
                res = _finish(cfg, a.slug, item, "refused" if manual else "skipped", v.mode or "live", now,
                              why=v.why, code=v.code)
            elif v.not_before is not None and v.not_before > now:
                item["not_before"] = _iso(v.not_before)
                item["why"] = v.why
                _queue_put(a.slug, item)
                return {"id": aid, "state": "queued", "why": v.why, "not_before": item["not_before"]}
            else:
                exe = _executor(item["kind"])
                still = ""
                f = getattr(exe, "still_signed", None) if exe is not None else None
                if callable(f):
                    try:
                        still = str(f(cfg, a, item) or "")
                    except SystemExit as e:             # its refusal in words ("It is signed: Pull it ..."): no
                        still = (str(e) or "it is not signed as it is now")[:300]
                    except Exception as e:  # noqa: BLE001  (cannot tell it is still signed: it does not go)
                        still = f"could not check it is still signed ({type(e).__name__}: {e})"[:300]
                elif v.mode == "live":
                    still = "the part that sends it is not built yet"
                if still:
                    res = _finish(cfg, a.slug, item, "refused" if manual else "skipped", v.mode, now, why=still,
                                  code="signed")
                elif v.mode == "practice":
                    gap = SW.caps_for(a, item["kind"], manual)[2]
                    _bump(a.slug, item["platform"], item["kind"], manual, now, True, gap, item.get("text", ""))
                    res = _finish(cfg, a.slug, item, "practice", "practice", now, why=v.why, code=v.code,
                                  would_at=_iso(now))
                else:
                    gap = SW.caps_for(a, item["kind"], manual)[2]
                    prev = _bump(a.slug, item["platform"], item["kind"], manual, now, False, gap, item.get("text", ""))
                    if item.get("text"):            # taken off again if the words never went out (_unmark_text)
                        item["text_mark"] = [now.timestamp(), prev]
                    item["count_mark"] = [f"{item['platform']}|{item['kind']}|{'manual' if manual else 'auto'}",
                                          now.timestamp()]      # taken off again if it never left here (_uncount)
                    item["state"] = "sending"
                    item["sent_at"] = _iso(now)
                    _queue_put(a.slug, item)
                    _set_key(a.slug, item["key"], "sending", item, now)
                    _row(cfg, a.slug, item, "sending", "live", now, why=v.why)
                    go = True
    if lost:
        return _lost(cfg, a, item, now)
    if not go:
        _on_state(cfg, a, item, res)
        return res
    return _execute(cfg, a, item, exe, now)


def _execute(cfg: C.Config, a, item: dict, exe, now: datetime) -> dict:
    """The platform call, outside every lock, then the result under the lock again."""
    from . import direct as D
    err: Optional[BaseException] = None
    got = None
    plat = str(item.get("platform") or "")
    try:
        D._LAST.usage = {}                  # an earlier answer's numbers (another account's) are never this one's
    except Exception:  # noqa: BLE001
        pass
    try:
        got = exe.execute(cfg, a, item)
    except (Exception, SystemExit) as e:  # noqa: BLE001  (every outcome is written down)
        err = e
    usage: dict = {}
    try:
        usage = usage_for(plat, D.last_usage())
    except Exception:  # noqa: BLE001
        usage = {}
    with _locked(a.slug):
        cur = _queue_get(a.slug, item["id"])
        if cur is None:                                       # forgotten meanwhile (forget drops it from the queue)
            res = _forgotten_meanwhile(cfg, a, item, got, err, now, usage)
        elif err is None:
            r = got if isinstance(got, dict) else {}
            result = {"id": r.get("id"), "url": r.get("url")}
            res = _finish(cfg, a.slug, cur, "done", "live", now, result=result, usage=usage or None,
                          endpoint=r.get("endpoint"))
        else:
            res = _failed(cfg, a, cur, err, now, usage)
    if err is None and usage:
        try:
            note_usage(cfg, a, plat, usage, now)
        except Exception as e:  # noqa: BLE001
            REG._log_error("outward:note_usage", e)
    if cur is not None:                 # a person forgotten meanwhile: nothing more is written about them anywhere
        _on_state(cfg, a, item, res)
    return res


def _forgotten_meanwhile(cfg: C.Config, a, item: dict, got, err: Optional[BaseException], now: datetime,
                         usage: dict) -> dict:
    """Forget ran while the platform call was out (under the account's lock). What happened is still written down,
    but without the person's key, id, name or words, the politics key forget dropped stays dropped, and it never goes
    back in the queue (no try again, no look-up)."""
    row = _blank(item, True)
    if err is None:
        r = got if isinstance(got, dict) else {}
        result = {"id": r.get("id"), "url": r.get("url")}
        why = "sent; the person was forgotten while it went"
        _row(cfg, a.slug, row, "done", "live", now, why=why, result=result, usage=usage or None,
             endpoint=r.get("endpoint"))
        return {"id": item["id"], "state": "done", "why": why, "result": result}
    why = f"not sent again: the person was forgotten while it went ({type(err).__name__})"
    _row(cfg, a.slug, row, "skipped", "live", now, why=why, error=_error_fields(err), usage=usage or None)
    return {"id": item["id"], "state": "skipped", "why": why}


def _failed(cfg: C.Config, a, item: dict, err: BaseException, now: datetime, usage: dict) -> dict:
    """What a refusal or no answer means for the action (under the account's lock)."""
    import socket
    import urllib.error
    from . import direct as D
    from . import net
    from .stats import NotConnected
    manual = _is_manual(item)
    efields = _error_fields(err)
    msg = str(err)[:300] or type(err).__name__
    slug = a.slug
    if isinstance(err, SystemExit):
        _unmark_text(slug, item)
        return _finish(cfg, slug, item, "refused" if manual else "skipped", "live", now, why=msg, error=efields)
    if isinstance(err, NotConnected):
        _unmark_text(slug, item)
        return _finish(cfg, slug, item, "skipped", "live", now, why=f"not connected: {msg}", error=efields)
    if isinstance(err, net.Offline):              # never reached the platform: back in the queue, not a try
        _unmark_text(slug, item)
        _uncount(slug, item)
        item["state"] = "queued"
        item["not_before"] = _iso(now + RETRY_AFTER)
        item.pop("sent_at", None)
        _queue_put(slug, item)
        _set_key(slug, item["key"], "queued", item, now)
        _row(cfg, slug, item, "failed", "live", now, why=f"not sent: {msg}", error=efields)
        return {"id": item["id"], "state": "queued", "why": msg}
    if isinstance(err, D.Pending):                # the platform is not ready for it yet: looked again, not a try
        _unmark_text(slug, item)
        _uncount(slug, item)
        waits = int(item.get("waits") or 0) + 1
        item["waits"] = waits
        if waits > MAX_WAITS:
            return _finish(cfg, slug, item, "gave_up", "live", now,
                           why=f"still not ready after {waits - 1} looks: {msg}", error=efields)
        item["state"] = "queued"
        item["not_before"] = _iso(now + RETRY_AFTER)
        item.pop("sent_at", None)
        _queue_put(slug, item)
        _set_key(slug, item["key"], "queued", item, now)
        _row(cfg, slug, item, "queued", "live", now, why=f"not ready yet ({waits}/{MAX_WAITS}): {msg}",
             error=efields)
        return {"id": item["id"], "state": "queued", "why": f"not ready yet: {msg}"}
    if not isinstance(err, D.PlatformError):
        # Every refusal made here before a request (SystemExit, not connected, offline, not ready) is above. Anything
        # else may come after the platform did it: no answer at all (a timeout, a dropped connection), or a 200 whose
        # body broke off (IncompleteRead) or was not JSON. Never a clear no: it stays "sending" and is
        # looked up before anything else, so a message or a reply is never sent twice blind.
        if isinstance(err, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError)):
            why, said = "no answer: looked up in 10 minutes", "no answer: it is looked up before anything else"
        else:
            why = f"no clear answer ({type(err).__name__}: {msg[:120]}): looked up in 10 minutes"
            said = "no clear answer: it is looked up before anything else"
        _row(cfg, slug, item, "lookup", "live", now, why=why, error=efields, usage=usage or None)
        return {"id": item["id"], "state": "sending", "why": said}
    kind = (efields or {}).get("kind") or "other"
    trip = None
    if isinstance(err, D.PlatformError):
        try:
            trip = tripwire(cfg, a, item["platform"], item["kind"], err, now)
        except Exception as e:  # noqa: BLE001
            REG._log_error("outward:tripwire", e)
    if isinstance(err, D.PlatformError) and _no_clear_answer(err, kind):
        # a 5xx or a transient error: the platform may have done it before it failed (a gateway timeout after the
        # comment was made). Like no answer at all: it stays "sending" and is looked up before anything else.
        _row(cfg, slug, item, "lookup", "live", now, why=f"no clear answer ({msg[:120]}): looked up in 10 minutes",
             error=efields, usage=usage or None)
        return {"id": item["id"], "state": "sending", "why": "no clear answer: it is looked up before anything else"}
    _unmark_text(slug, item)                    # a clear no: the words did not go out
    if trip and trip.get("until"):              # the platform asked us to wait: after the hold, not a try
        item["state"] = "queued"
        item["not_before"] = trip["until"]
        item.pop("sent_at", None)
        _queue_put(slug, item)
        _set_key(slug, item["key"], "queued", item, now)
        words = hold_words(cfg, trip["key"], trip, now)
        _row(cfg, slug, item, "held", "live", now, why=words, error=efields, usage=usage or None)
        return {"id": item["id"], "state": "held", "why": words, "not_before": trip["until"]}
    if trip and trip.get("perm"):
        return _finish(cfg, slug, item, "refused" if manual else "skipped", "live", now, why=trip["why"],
                       error=efields, code="perm")
    if kind in ("gone", "policy"):                # the comment went, or comments are off there: trying cannot help
        return _finish(cfg, slug, item, "refused" if manual else "skipped", "live", now,
                       why=f"{_plat_name(item)} said no: {msg}", error=efields)
    tries = int(item.get("tries") or 0) + 1
    item["tries"] = tries
    if tries >= MAX_TRIES:
        return _finish(cfg, slug, item, "gave_up", "live", now, why=f"failed {tries} times: {msg}", error=efields)
    item["state"] = "queued"
    item["not_before"] = _iso(now + RETRY_AFTER * tries)
    item.pop("sent_at", None)
    _queue_put(slug, item)
    _set_key(slug, item["key"], "queued", item, now)
    _row(cfg, slug, item, "failed", "live", now, why=f"failed ({tries}/{MAX_TRIES}): {msg}", error=efields)
    return {"id": item["id"], "state": "failed", "why": msg}


def _no_clear_answer(err: BaseException, kind: str) -> bool:
    """A platform answer that does not say whether the action was done: HTTP 500 or over, or an error the platform
    calls transient. A refusal made here before any call (direct.Refused, Stopped) is a clear no."""
    from . import direct as D
    if isinstance(err, (D.Refused, D.Stopped)):
        return False
    status = getattr(err, "status", None)
    return kind in ("transient", "timeout") or (isinstance(status, int) and not isinstance(status, bool)
                                                and status >= 500)


def _lost(cfg: C.Config, a, item: dict, now: datetime) -> dict:
    """"sending" with no answer for 10 minutes: look on the platform (the executor's find). Found: done. Not found: a
    first comment, a public reply or a hide goes once more; a private reply, a message, a story or a card never goes
    again by itself (a person looks). A look-up that fails three times gives up the same way."""
    exe = _executor(str(item.get("kind") or ""))
    f = getattr(exe, "find", None) if exe is not None else None
    got, err = None, None
    if not callable(f):                         # nothing can look: never resent blind
        err = LookupError("the part that looks it up is not built yet")
    else:
        try:
            got = f(cfg, a, item)
        except (Exception, SystemExit) as e:  # noqa: BLE001  (a look-up that fails or refuses is counted)
            err = e
    look = f"Not sure it went: look on {_plat_name(item)}, then press Send again"
    with _locked(a.slug):
        cur = _queue_get(a.slug, item["id"])
        if cur is None:
            return {"id": item["id"], "state": "gone", "why": "not in the queue"}
        if err is None and isinstance(got, dict) and got:
            res = _finish(cfg, a.slug, cur, "done", "live", now, result={"id": got.get("id"), "url": got.get("url")},
                          why="found after a lost answer")
        elif err is not None:
            cur["lookups"] = int(cur.get("lookups") or 0) + 1
            if cur["lookups"] >= MAX_LOOKUPS:
                res = _finish(cfg, a.slug, cur, "gave_up", "live", now, why=f"{look} (could not check: {err})"[:400],
                              error=_error_fields(err))
            else:
                cur["sent_at"] = _iso(now)
                _queue_put(a.slug, cur)
                _row(cfg, a.slug, cur, "lookup", "live", now, why=f"could not check ({cur['lookups']}/{MAX_LOOKUPS})",
                     error=_error_fields(err))
                return {"id": cur["id"], "state": "sending", "why": "could not check yet"}
        elif cur.get("kind") in RESEND_ONCE and int(cur.get("lost_retry") or 0) < 1:
            cur["lost_retry"] = int(cur.get("lost_retry") or 0) + 1
            _unmark_text(a.slug, cur)                   # it was not there: the words did not go out
            cur["state"] = "queued"
            cur["not_before"] = _iso(now)
            cur.pop("sent_at", None)
            _queue_put(a.slug, cur)
            _set_key(a.slug, cur["key"], "queued", cur, now)
            _row(cfg, a.slug, cur, "lookup", "live", now, why="not found after a lost answer: it goes once more")
            return {"id": cur["id"], "state": "queued", "why": "not found: it goes once more"}
        else:
            res = _finish(cfg, a.slug, cur, "gave_up", "live", now, why=look)
    _on_state(cfg, a, item, res)
    return res


def _line(a, item: dict, res: dict) -> str:
    st = res.get("state")
    if st in (None, "queued", "sending", "gone"):
        return ""
    what = f"{a.name} {item.get('platform')} {KIND_WORDS.get(item.get('kind'), item.get('kind'))}"
    if st == "done":
        r = res.get("result") or {}
        return f"  {what}: sent {r.get('url') or r.get('id') or ''}".rstrip()
    if st == "practice":
        return f"  {what}: practice, would send: {str(item.get('text') or '')[:120]}"
    return f"  {what}: {st}, {res.get('why') or ''}".rstrip(", ")


def drain(cfg: C.Config, now: Optional[datetime] = None, budget_s: float = 180) -> List[str]:
    """Send what is due on every account, one at a time, for up to budget_s seconds (the rest go on the next tick).
    One drain at a time on this machine (state/social/locks/send.lock); nothing while Stop everything is on. What
    happened, one line per action that went, practised, failed or was left."""
    out: List[str] = []
    with store.try_lock("send") as got:
        if not got or stopped(cfg):
            return out
        started = time.monotonic()
        for a in _accounts(cfg):
            for item in due(cfg, a, now):
                if time.monotonic() - started > budget_s:
                    out.append("  time is up: the rest go on the next tick")
                    return out
                try:
                    res = send(cfg, a, item, now)
                except (Exception, SystemExit) as e:  # noqa: BLE001  (one action never stops the others)
                    REG._log_error("outward:send", e)
                    out.append(f"  {a.name} {item.get('kind')}: could not send ({type(e).__name__}: {e})"[:300])
                    continue
                line = _line(a, item, res)
                if line:
                    out.append(line)
    return out


def drain_job(cfg: C.Config, now: Optional[datetime] = None, quiet: bool = True) -> List[str]:
    """The social tick's step: send what is due."""
    return drain(cfg, now)


# ------------------------------------------------------------------------------------------------------ tripwires
def _publishes(slug: str, plat: str, now: datetime) -> List[float]:
    return _stamps(_guard(slug), plat, FAMILIES["publish"], ("auto", "manual"), DAY_S, now, False)


def _publish_limit_until(slug: str, plat: str, now: datetime) -> datetime:
    """Instagram's publishing limit: until the oldest publish of the last 24 hours ages out (an hour at the least)."""
    pubs = _publishes(slug, plat, now)
    t = datetime.fromtimestamp(pubs[0] + DAY_S, timezone.utc) if pubs else now
    return max(t, now + timedelta(hours=1))


def _multi(cfg: C.Config, a, plat: str, families: Sequence[str], seconds: float, why: str, code, releasable: bool,
           now: datetime, kind: str) -> dict:
    hs = [hold(cfg, a, plat, f, seconds, why, code=code, releasable=releasable, now=now, kind=kind) for f in families]
    first = dict(hs[0])
    first["keys"] = [h["key"] for h in hs]
    return first


def tripwire(cfg: C.Config, acct, platform, kind: str, err: BaseException,
             now: Optional[datetime] = None) -> Optional[dict]:
    """What a refusal means for what comes next (architecture section 5): a hold written (the hold, with its "key"),
    {"perm": True, "why"} for a permission the sign-in lacks (marked missing until the next connect), or None."""
    from . import graph as G
    from . import direct as D
    a = _account(cfg, acct)
    now = _now(now)
    plat = plat_of(platform)
    if not plat:
        return None
    info = G.classify(err)
    fam = FAMILY_OF.get(kind, "comment")
    code = info.code
    said = f" ({code})" if code is not None else (f" ({info.reason})" if info.reason else "")
    try:
        usage = usage_for(plat, D.last_usage())
    except Exception:  # noqa: BLE001
        usage = {}
    regain = usage.get("regain_min") if isinstance(usage, dict) else None
    if isinstance(regain, (int, float)) and regain > 0:
        return _regain_hold(cfg, a, plat, regain, code, now)
    k = info.kind
    if k == "block":
        if fam == "publish":
            return hold(cfg, a, plat, "publish", 12 * HOUR_S, f"Meta blocked it{said}", code=code, releasable=True,
                        now=now, kind=k)
        return _multi(cfg, a, plat, ENGAGEMENT, 24 * HOUR_S, f"Meta blocked it{said}", code, False, now, k)
    if k == "buc":
        secs = max(HOUR_S, float(regain or 0) * 60)
        if fam == "publish":
            return hold(cfg, a, plat, "publish", secs, f"too many calls to Meta{said}", code=code, now=now, kind=k)
        return _multi(cfg, a, plat, ENGAGEMENT + ("read",), secs, f"too many calls to Meta{said}", code, True, now, k)
    if k == "rate":
        ts = now.timestamp()
        with _guard_edit(a.slug) as g:
            rates = g.get("rates") if isinstance(g.get("rates"), dict) else {}
            mine = [x for x in rates.get(plat) or [] if isinstance(x, (int, float)) and ts - x < DAY_S]
            again = bool(mine)
            rates[plat] = mine + [ts]
            g["rates"] = rates
        secs = 6 * HOUR_S if again else HOUR_S
        return _multi(cfg, a, plat, ENGAGEMENT + ("read",), secs,
                      f"Meta's rate limit{said}" + (", the second in a day" if again else ""), code, True, now, k)
    if k == "publish_limit":
        until = _publish_limit_until(a.slug, plat, now)
        return hold(cfg, a, plat, "publish", (until - now).total_seconds(), f"Instagram's publishing limit{said}",
                    code=code, releasable=True, now=now, kind=k)
    if k == "perm":
        try:
            from . import capabilities as CAP
            CAP.mark_missing(a, plat, kind, info.msg, now)
            need = CAP.NEED_OF_KIND.get(kind, kind)
        except Exception as e:  # noqa: BLE001
            REG._log_error("capabilities:mark_missing", e)
            need = kind
        return {"perm": True, "why": f"{PLAT_WORDS.get(plat, plat)} refused the permission{said}: waits for "
                                     f"{need.replace('_', ' ')} (connect again)"}
    if k == "auth":
        group = ("facebook", "instagram") if plat in ("facebook", "instagram") else (plat,)   # one Page token
        got = {p: _multi(cfg, a, p, ENGAGEMENT + ("read",), DAY_S,
                         f"the sign-in stopped working{said}: factory.py connect meta {a.slug}", code, True, now, k)
               for p in group}
        return got[plat]
    if k == "quota":
        fams = ("comment", "moderation") + (("read",) if info.reason in ("quotaExceeded", "dailyLimitExceeded")
                                            else ())
        until = _pt_reset(now)
        return _multi(cfg, a, plat, fams, (until - now).total_seconds(), f"YouTube's quota{said}", code, False, now,
                      k)
    if k == "other":
        ts = now.timestamp()
        with _guard_edit(a.slug) as g:
            fails = g.get("fails") if isinstance(g.get("fails"), dict) else {}
            fk = f"{plat}|{kind}"
            mine = [x for x in fails.get(fk) or [] if isinstance(x, (int, float)) and ts - x < FAIL_WINDOW] + [ts]
            fails[fk] = mine
            g["fails"] = fails
        if len(mine) >= FAIL_LIMIT:
            return hold(cfg, a, plat, kind, FAIL_HOLD, f"{FAIL_LIMIT} failures in an hour", code=code,
                        releasable=True, now=now, kind="failures")
    return None


# ------------------------------------------------------------------------------------------------------ the poster
def _guard_mode(a) -> str:
    try:
        return SW.mode(a, "guard")
    except Exception:  # noqa: BLE001
        return SW.OFF


def record_post(cfg: C.Config, acct, day, slot: str, outlet: str, upd: dict, text: str,
                error: Optional[BaseException] = None) -> None:
    """The poster's row for one platform of one slot (poster._record): done for a post id, gave_up, failed for a
    counted try, held for an error alone. A post is counted for Instagram's publishes and the 30 minutes before a
    story while any social part is on; the row is written only while the guard is watch or on."""
    a = _account(cfg, acct)
    if _is_client(a):
        return None
    plat = plat_of(outlet)
    if not plat or not isinstance(upd, dict) or not SW.any_on(a):
        return None
    if upd.get("post_id") or upd.get("video_id"):
        state = "done"
    elif upd.get("gave_up"):
        state = "gave_up"
    elif upd.get("tries"):
        state = "failed"
    elif upd.get("last_error"):
        state = "held"
    else:
        return None
    now = _now()
    if state == "done":
        _bump(a.slug, plat, "post", False, now, False)
    if _guard_mode(a) == SW.OFF:
        return None
    pid = upd.get("post_id") or upd.get("video_id")
    fields = {"key": f"post:{outlet}:{day} {slot}", "outlet": outlet, "runner": "poster",
              "origin": {"by": "poster", "rule": None, "via": None}, "signed": {"what": "clip"},
              "target": {"board": str(day), "slot": slot, "post_id": pid}, "why": str(text or "")[:400]}
    if pid:
        fields["result"] = {"id": pid, "url": upd.get("url")}
    if error is not None:
        fields["error"] = _error_fields(error)
    record(cfg, a, plat, "post", state=state, mode="live", now=now, **fields)
    return None


def post_gate(cfg: C.Config, acct, outlet: str, now: Optional[datetime] = None) -> str:
    """'' when the poster may post; the hold's words while the guard is on and a publish (or everything) hold runs
    on that platform."""
    a = _account(cfg, acct)
    plat = plat_of(outlet)
    if not plat or _is_client(a) or _guard_mode(a) != "on":
        return ""
    now = _now(now)
    got = _active(a.slug, [f"{plat}|publish", f"{plat}|*"], now)
    return hold_words(cfg, got[0], got[1], now) if got else ""


def post_tripwire(cfg: C.Config, acct, outlet: str, err: BaseException, now: Optional[datetime] = None) -> str:
    """A post the platform refused: with the guard on, a block (368), a Business Use Case limit or a regain time, or
    Instagram's publishing limit writes a publish hold and gives its words (the poster waits, not a try). Watch logs
    what it would have done. Rate limits stay with the poster's own Meta hold."""
    from . import graph as G
    from . import direct as D
    a = _account(cfg, acct)
    plat = plat_of(outlet)
    g = _guard_mode(a)
    if not plat or g == SW.OFF or _is_client(a):
        return ""
    now = _now(now)
    info = G.classify(err)
    code = info.code
    said = f" ({code})" if code is not None else ""
    try:
        usage = usage_for(plat, D.last_usage())
    except Exception:  # noqa: BLE001
        usage = {}
    regain = usage.get("regain_min") if isinstance(usage, dict) else None
    plan = None                          # (family, seconds, why, releasable)
    if isinstance(regain, (int, float)) and regain > 0:
        plan = ("*", max(30.0, regain + 15) * 60, f"Meta asked us to wait {int(round(regain))} minutes", False)
    elif info.kind == "block":
        plan = ("publish", 12 * HOUR_S, f"Meta blocked publishing{said}", True)
    elif info.kind == "buc" or (code is not None and 80001 <= code <= 80006):
        plan = ("publish", HOUR_S, f"too many calls to Meta{said}", True)
    elif info.kind == "publish_limit":
        until = _publish_limit_until(a.slug, plat, now)
        plan = ("publish", (until - now).total_seconds(), f"Instagram's publishing limit{said}", True)
    if plan is None:
        return ""
    fam, secs, why, free = plan
    if g == "watch":
        record(cfg, a, plat, "post", state="watch", mode="live", now=now, outlet=outlet,
               key=f"post:{outlet}", runner="poster", origin={"by": "poster", "rule": None, "via": None},
               why=f"would pause {FAMILY_WORDS.get(fam, fam)} for {int(secs // 60)} minutes: {why}",
               error=_error_fields(err))
        return ""
    h = hold(cfg, a, plat, fam, secs, why, code=code, releasable=free, now=now, kind=info.kind)
    return hold_words(cfg, h["key"], h, now)


def read_paused(cfg: C.Config, acct, outlet_or_platform, now: Optional[datetime] = None) -> str:
    """'' when the stats and followers reads may ask this platform; with the guard on, the words of a reading (or
    everything) hold, or the poster's own Meta hold for Facebook and Instagram."""
    a = _account(cfg, acct)
    plat = plat_of(outlet_or_platform)
    if not plat or _is_client(a) or _guard_mode(a) != "on":
        return ""
    now = _now(now)
    got = _active(a.slug, [f"{plat}|read", f"{plat}|*"], now)
    if got:
        return hold_words(cfg, got[0], got[1], now)
    if plat in ("facebook", "instagram"):
        mw, _ = _meta_hold_until(a)
        return mw
    return ""


# ------------------------------------------------------------------------------------------------------ forget, prune
PERSON_KEYS = ("to", "to_id", "person", "name", "author")     # a target's fields that name or identify a person


def _mentions(row: dict, person: str) -> bool:
    t = row.get("target") if isinstance(row.get("target"), dict) else {}
    by_id = f"{row.get('platform') or ''}:{t.get('to_id')}" if t.get("to_id") else ""
    return person in (str(t.get("to") or ""), str(t.get("person") or ""), str(row.get("person") or ""), by_id)


def _blank(row: dict, person_too: bool) -> dict:
    t = dict(row.get("target")) if isinstance(row.get("target"), dict) else {}
    for k in PERSON_KEYS:
        if t.get(k):
            t[k] = FORGOTTEN if person_too or k in ("name", "author") else t[k]
    out = dict(row, target=t)
    if out.get("text"):
        out["text"] = FORGOTTEN
    if out.get("person") and person_too:
        out["person"] = FORGOTTEN
    return out


def _strip(row: dict) -> dict:
    """A row past 90 days: no words, no names; what happened stays."""
    out = {k: v for k, v in row.items() if k not in ("text", "person")}
    t = dict(row.get("target")) if isinstance(row.get("target"), dict) else {}
    for k in PERSON_KEYS:
        t.pop(k, None)
    out["target"] = t
    out["pruned"] = True
    return out


def _rewrite(p: Path, change) -> int:
    """Rewrite a log file row by row (change gives the new row or None for the same): how many changed."""
    n = 0
    with store._locked(store._inside(p)):
        try:
            lines = p.read_text().splitlines()
        except OSError:
            return 0
        out = []
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                out.append(line)
                continue
            new = change(r) if isinstance(r, dict) else None
            if new is None:
                out.append(line)
            else:
                n += 1
                out.append(json.dumps(new, default=str).replace("\n", " "))
        if n:
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text("".join(x + "\n" for x in out if x.strip()))
            os.replace(tmp, p)
    return n


def forget(cfg: C.Config, acct, person_key: str) -> int:
    """Forget a person ("instagram:1784..."): in the account's log their key, name and words become "[forgotten]",
    and anything still queued for them is dropped. How many records changed."""
    slug = _slug(acct)
    person = str(person_key or "").strip()
    if not person:
        raise SystemExit("forget needs the person, platform:id")
    n = 0
    with _locked(slug):
        for f in _month_files(slug):
            n += _rewrite(f, lambda r: _blank(r, True) if _mentions(r, person) else None)
        for aid, item in _items(slug).items():
            if _mentions(item, person):
                _queue_drop(slug, aid)
                with store.edit(_keys_path(slug), dict) as k:
                    k.pop(item.get("key"), None)
                n += 1
    return n


def prune_job(cfg: C.Config, now: Optional[datetime] = None, quiet: bool = True) -> List[str]:
    """Once a day: the politics keeps 120 days, log rows older than 90 days lose their words and names, the counters
    keep a day, holds that ended go to the history."""
    now = _now(now)
    day = now.date().isoformat()
    mark = store.path("social", "prune.json")
    if _read(mark).get("day") == day:
        return []
    out: List[str] = []
    cut_keys = now - timedelta(days=POLITICS_DAYS)
    cut_text = now - timedelta(days=LOG_TEXT_DAYS)
    ts = now.timestamp()
    for a in _accounts(cfg):
        slug = a.slug
        if not _dir(slug).is_dir():
            continue
        gone = 0
        with store.edit(_keys_path(slug), dict) as k:
            for key in [x for x, v in k.items() if not isinstance(v, dict) or
                        (_when(v.get("at")) or now) < cut_keys]:
                k.pop(key, None)
                gone += 1
        blanked = 0
        for f in _month_files(slug):
            if f.stem[len("actions-"):] > cut_text.strftime("%Y-%m"):
                continue

            def old(r: dict) -> Optional[dict]:
                t = _when(r.get("t"))
                if r.get("pruned") or t is None or t >= cut_text:
                    return None
                return _strip(r)
            blanked += _rewrite(f, old)
        with _guard_edit(slug) as g:
            for sect in ("recent", "practice"):
                rec = g.get(sect) if isinstance(g.get(sect), dict) else {}
                g[sect] = {k2: [x for x in v if isinstance(x, (int, float)) and ts - x < DAY_S]
                           for k2, v in rec.items() if isinstance(v, list)}
                g[sect] = {k2: v for k2, v in g[sect].items() if v}
            for sect in ("texts", "texts_practice"):
                tx = g.get(sect) if isinstance(g.get(sect), dict) else {}
                g[sect] = {s: v for s, v in tx.items() if isinstance(v, (int, float)) and ts - v < DAY_S}
            for sect in ("next", "next_practice", "slow_until"):
                nx = g.get(sect) if isinstance(g.get(sect), dict) else {}
                g[sect] = {s: v for s, v in nx.items() if isinstance(v, (int, float)) and v > ts}
        with store.edit(_holds_path(slug), dict) as d:
            holds = d.get("holds") if isinstance(d.get("holds"), dict) else {}
            ended = {k2: h for k2, h in holds.items() if not isinstance(h, dict) or
                     (_when(h.get("until")) or now) <= now}
            if ended:
                hist = d.get("history") if isinstance(d.get("history"), list) else []
                hist += [dict(h, key=k2, ended=True) for k2, h in ended.items() if isinstance(h, dict)]
                d["history"] = hist[-100:]
                d["holds"] = {k2: h for k2, h in holds.items() if k2 not in ended}
        if gone or blanked:
            out.append(f"  {a.name}: {gone} politics keys past {POLITICS_DAYS} days, {blanked} log rows past "
                       f"{LOG_TEXT_DAYS} days cleared")
    store.write(mark, {"day": day})
    return out


# ------------------------------------------------------------------------------------------------------ the page
def _own(accts) -> List:
    return [a for a in (accts or []) if not _is_client(a)]


def shown_holds(cfg: C.Config, acct, now: Optional[datetime] = None) -> List[Tuple[str, dict, str]]:
    """The holds running that something obeys, with their words: none while every part of the account is off (a
    hold left from before holds nothing back then), and with the guard not on, worded without the posts (only the
    guard holds a post)."""
    a = _account(cfg, acct)
    if _is_client(a) or not _holds_obeyed(a):
        return []
    now = _now(now)
    posts = _guard_mode(a) == "on"
    return [(k, h, hold_words(cfg, k, h, now, posts=posts)) for k, h in active_holds(cfg, a, now)]


def health(cfg: C.Config, now: datetime, accts) -> Tuple[str, str]:
    """The red line above the Clips page: Stop everything, or the holds running ('' when there are none)."""
    words = stopped(cfg)
    if words:
        return ("bad", words)
    now = _now(now)
    lines = [f"{a.name}: {w}" for a in _own(accts) for _k, _h, w in shown_holds(cfg, a, now)]
    if not lines:
        return ("", "")
    more = f" (and {len(lines) - 3} more)" if len(lines) > 3 else ""
    return ("bad", "; ".join(lines[:3]) + more + ".")


def summary(cfg: C.Config, now: datetime, accts) -> List[dict]:
    """The Today band's chips: Stop, the holds, the sends that failed in the last day. [] with every part off."""
    now = _now(now)
    out: List[dict] = []
    words = stopped(cfg)
    if words:
        out.append({"key": "stop", "words": "Stopped: nothing social goes out", "href": "/log", "level": "bad",
                    "order": 0})
    own = [a for a in _own(accts) if SW.any_on(a)]
    n = sum(len(shown_holds(cfg, a, now)) for a in own)
    if n:
        out.append({"key": "holds", "words": f"{n} paused", "href": "/log?view=holds", "level": "bad", "order": 1})
    bad = 0
    for a in own:
        if SW.any_on(a):
            bad += sum(1 for _ in rows(cfg, a, since=now - timedelta(days=1), states=("gave_up", "failed")))
    if bad:
        out.append({"key": "failed", "words": f"{bad} send{'s' if bad != 1 else ''} failed", "href": "/log",
                    "level": "warn", "order": 40})
    return out


def morning_lines(cfg: C.Config, now: Optional[datetime] = None) -> List[str]:
    """The 06:30 notice's hold sentences: Stop everything, then each hold still running."""
    now = _now(now)
    out: List[str] = []
    words = stopped(cfg)
    if words:
        out.append(words)
    for a in _accounts(cfg):
        for _k, _h, w in shown_holds(cfg, a, now):
            out.append(f"{a.name}: {w}.")
    return out


def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """Stop everything, each hold (a NO, with how to release it or when it ends), files that will not read, actions
    stuck on their way, the last day's sends, and this machine's YouTube units. Read only; nothing for an account
    whose parts are all off (a hold left from before holds nothing back then)."""
    now = _now(now)
    out: List[Tuple[Optional[bool], str, str]] = []
    words = stopped(cfg)
    if words:
        out.append((None, words, ""))
    for a in _accounts(cfg):
        try:
            on = SW.any_on(a)
            if not on:
                continue
            holds = shown_holds(cfg, a, now)
            for name in ("queue.json", "keys.json", "holds.json", "guard.json"):
                p = _dir(a.slug) / name
                if p.exists() and not isinstance(store.read(p, None), dict):
                    out.append((False, f"{a.name}: state/{a.slug}/outward/{name} will not read",
                                "move it aside (the sender starts it again empty)"))
            for k, h, w in holds:
                until = _when(h.get("until"))
                fix = (f"factory.py social release {a.slug} {k} --by NAME" if h.get("releasable", True) else
                       f"it ends by itself at {_hhmm(cfg, until, now)}" if until else "it ends by itself")
                out.append((False, f"{a.name}: {w}", fix))
            stuck = [i for i in _items(a.slug).values() if i.get("state") == "sending" and
                     (_when(i.get("sent_at")) or now) <= now - LOST_AFTER]
            if stuck:
                out.append((False, f"{a.name}: {len(stuck)} action{'s' if len(stuck) != 1 else ''} sent with no "
                                   f"answer yet (looked up on the next tick)", "factory.py social send"))
            if on:
                day = {}
                for r in rows(cfg, a, since=now - timedelta(days=1)):
                    day[r.get("state")] = day.get(r.get("state"), 0) + 1
                if day:
                    words = ", ".join(f"{n} {s}" for s, n in sorted(day.items()) if s)
                    out.append((None, f"{a.name} outward, last 24 hours: {words}", ""))
        except Exception as e:  # noqa: BLE001  (the doctor never stops over one account)
            out.append((False, f"{getattr(a, 'name', a)} outward: could not read ({type(e).__name__}: {e})",
                        f"look at state/{_slug(a)}/outward/"))
    used = yt_used(now)
    if used:
        out.append((None, f"YouTube units today on this machine: {used} of {yt_left(cfg, now) + used}", ""))
    return out
