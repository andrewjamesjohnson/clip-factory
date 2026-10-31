"""Clip Review's Inbox behind the page: reading comments and messages, and sending what Andrew clicks.

The module, its state and its command are named "engage" (inbox already means the Desktop Clip Drop folder); only the
tab's label says Inbox. There are no webhooks (Clip Review is loopback only), so the Inbox polls:

    tick (the social timer, every 5 minutes; each account whose engage.read switch is on)
      1. the posts index: which platform post is which clip (30 days of boards and the stats' pulled files)
      2. Instagram's posts of the last 7 days and the Page's reels, with their comment counts
      3. comments only where the count changed or the decay is due (under 48 hours every poll, or every 30 minutes
         when the platform gave a count that did not move; 2 to 7 days hourly; 7 to 30 days every 6 hours): moved
         counts first, then the most overdue; one post that will not read waits on its own (a deleted one for good)
      4. YouTube's comment threads (every engage.youtube_minutes, 1 unit a page, only while 100 units are left)
      5. messages (engage.messages on): Instagram every engage.dm_minutes, Facebook every 30 at the least
      6. each new comment or message tagged (classify.py), stored (socialstore), its person noted; never one older
         than engage.keep_days (prune would take it again) nor one by a person forgotten here
      7. STOP at the start of a message: nothing automated goes to that person again
      8. the new top-level comments and messages (never ours, never replies to replies) to the automations
      9. once a day: what is older than engage.keep_days (45) is removed
    Every read waits out a reading hold, the poster's Meta hold and a missing permission; at 75% of Meta's limit the
    intervals are four times longer. A refusal for a permission (Meta's 10 or 200-299, Google's insufficient scopes,
    or comments that came back without their words and names) stores nothing and marks the permission missing. A
    comment deleted on the platform becomes "gone" (on YouTube only when the read reached the channel's last thread,
    so a busy channel keeps a deleted comment until the prune). Failing message reads rest on their own and never
    pause Andrew's own messages.

    execute / find / still_signed / on_state
      the executor outward's sender calls for reply_public, reply_private, dm, hide, unhide and yt_moderate (the
      calls are in engage_api.py, the one Inbox file that reaches a platform)

The page is engagepage.py; this file never runs in a GET. With engage.read off for an account nothing here calls a
platform or writes a file for it.

    factory.py engage status [account]
    factory.py engage poll <account> [--force]        read now, by hand (engage.read must be on)
    factory.py engage check [--quiet]                 the Inbox's Check now: one read of every account that reads
    factory.py engage list <account> [--state new] [--platform instagram] [--limit 20]
    factory.py engage posts <account>                 rebuild the posts index (files only)
    factory.py engage prune [account]
    factory.py engage forget <account> --person instagram:1784... --by NAME
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import capabilities as CAP
from . import classify as K
from . import config as C
from . import outward as OW
from . import registry as REG
from . import socialstore as SS
from . import store
from . import switches as SW

PLATS = ("instagram", "facebook", "youtube")
META = ("instagram", "facebook")
PLAT_WORDS = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}
OUTLET_OF = {"instagram": "instagram_reels", "facebook": "facebook_reels", "youtube": "youtube_shorts"}
PLAT_OF = {v: k for k, v in OUTLET_OF.items()}
POST_DAYS = 30                       # comments are read on posts up to 30 days old
IG_LIST_DAYS = 7                     # Instagram's media list (with counts) covers the last 7 days
DECAY = ((timedelta(hours=48), timedelta(0)), (timedelta(days=7), timedelta(hours=1)),
         (timedelta(days=POST_DAYS), timedelta(hours=6)))
SLOW_X = 4                           # Meta at 75% of its limit: every interval four times longer
FB_DM_MINUTES = 30
YT_MIN_UNITS = 100                   # YouTube comments are read only while this machine has more units than this
MAX_POSTS = 25                       # posts whose comments are read in one poll (the rest wait for the next)
FRESH_KNOWN = timedelta(minutes=30)  # under 48 hours with an unchanged count: a backstop read for hides and deletes
POST_BACKOFF = timedelta(hours=1)    # one post that would not read waits this long, doubling each time (to a day)
POST_GONE_BACKOFF = timedelta(hours=6)   # Meta's "does not exist, or missing permissions" on one post, doubling
POST_BACKOFF_MAX = timedelta(days=3)
POST_WIDE = ("block", "buc", "rate", "auth", "perm", "quota", "timeout", "transient")   # a no for every post
MAX_CONVERSATIONS = 10
PACE_S = 0.5                         # the Conversations API takes 2 calls a second per account: never faster
DM_FAILS, DM_FAIL_WINDOW, DM_BACKOFF = 3, timedelta(hours=1), timedelta(hours=6)   # failing message reads rest
CHECK_GAP = timedelta(minutes=2)     # Check now, at most this often
PRIVATE_DAYS = 6                     # outward.PRIVATE_REPLY_DAYS: Meta allows 7
TEXT_KINDS = ("reply_public", "reply_private", "dm")
KINDS = ("reply_public", "reply_private", "dm", "hide", "unhide", "yt_moderate", "like")
SLACK = timedelta(seconds=30)        # a 5-minute tick against a 10-minute interval: a little early is on time


# ------------------------------------------------------------------------------------------------------ small helpers
def _now(now: Optional[datetime] = None) -> datetime:
    t = now or datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return _now(t).replace(microsecond=0).isoformat()


def _when(v) -> Optional[datetime]:
    """A platform's time (Graph's +0000 too) or ours, as an aware datetime; None when there is none."""
    if not v:
        return None
    if isinstance(v, datetime):
        return _now(v)
    s = str(v).replace("Z", "+00:00")
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":" and s[-4:].isdigit():
        s = s[:-2] + ":" + s[-2:]
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return _now(t)


def _stamp(v) -> Optional[str]:
    t = _when(v)
    return _iso(t) if t is not None else None


def _is_client(acct) -> bool:
    raw = getattr(acct, "raw", None)
    return isinstance(raw, dict) and str(raw.get("owner") or "self") == "client"


def accounts(cfg: C.Config) -> List[C.Account]:
    """The accounts the Inbox reads for: switched on, on this machine, never a client's (social.accounts)."""
    return [a for a in cfg.accounts.values() if not _is_client(a)]


def _account(cfg: C.Config, slug: str) -> C.Account:
    a = cfg.accounts.get(str(slug or ""))
    if a is None or _is_client(a):
        raise SystemExit(f"{slug or 'that'}: not one of your accounts switched on here "
                         f"({', '.join(x.slug for x in accounts(cfg))})")
    return a


def reading(acct) -> bool:
    return SW.mode(acct, "engage.read") == "on"


def _sha(text: str) -> str:
    """outward's text_sha of an action: the exact words."""
    return hashlib.sha256(str(text or "").encode()).hexdigest()


def _same_words(a: str, b: str) -> bool:
    return " ".join(str(a or "").lower().split()) == " ".join(str(b or "").lower().split())


def _hhmm(cfg: C.Config, t: datetime) -> str:
    lt = _now(t).astimezone(cfg.tz())
    today = _now(cfg.now()).astimezone(cfg.tz()).date() if callable(getattr(cfg, "now", None)) else lt.date()
    return lt.strftime("%H:%M") if lt.date() == today else lt.strftime("%b %d %H:%M")


def _ago(now: datetime, t: Optional[datetime]) -> str:
    if t is None:
        return "never"
    s = max(0, int((now - t).total_seconds()))
    if s < 90:
        return "just now"
    if s < 5400:
        return f"{round(s / 60)} min ago"
    if s < 172800:
        return f"{round(s / 3600)} hours ago"
    return f"{round(s / 86400)} days ago"


def initials(name: str) -> str:
    """Two letters for a person's square: 'some_user' gives SU, 'Andrew' gives AN."""
    import re
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", str(name or "")) if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


# ------------------------------------------------------------------------------------------------------ posts index
def _pulled(acct: C.Account, outlet: str) -> List[dict]:
    """The stats' pulled file for one platform (videos that matched no clip), read from C.STATE at call time."""
    got = store.read(C.STATE / "stats" / "pulled" / f"{acct.slug}__{outlet}.json", {})
    vids = got.get("videos") if isinstance(got, dict) else None
    return [v for v in vids if isinstance(v, dict)] if isinstance(vids, list) else []


def _boards_sig(acct: C.Account, since: date) -> str:
    from . import board as B
    parts = []
    for bp in B.all_boards(acct):
        try:
            if date.fromisoformat(bp.stem) < since:
                continue
            st = bp.stat()
            parts.append(f"{bp.stem}:{st.st_size}:{st.st_mtime_ns}")
        except (ValueError, OSError):
            continue
    for outlet in OUTLET_OF.values():
        p = C.STATE / "stats" / "pulled" / f"{acct.slug}__{outlet}.json"
        try:
            st = p.stat()
            parts.append(f"{outlet}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            pass
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def build_posts_index(cfg: C.Config, acct: C.Account, now: Optional[datetime] = None) -> Dict[str, dict]:
    """{platform_id: {platform, outlet, day, slot, slug, title, url, cover, went, factory}} for 30 days of the
    account's posts: the factory's from its boards, then anything else the stats read (posted by hand)."""
    from . import board as B
    from . import stats as ST
    now = _now(now)
    since = now.astimezone(cfg.tz()).date() - timedelta(days=POST_DAYS)
    idx: Dict[str, dict] = {}
    for bp in B.all_boards(acct):
        try:
            if date.fromisoformat(bp.stem) < since:
                continue
        except ValueError:
            continue
        bd = store.read(bp, {})
        slots = bd.get("slots") if isinstance(bd, dict) else None
        for key, s in sorted((slots or {}).items()):
            if not isinstance(s, dict):
                continue
            for plat, outlet in OUTLET_OF.items():
                rec = (s.get("outlets") or {}).get(outlet)
                if not isinstance(rec, dict):
                    continue
                pid = str(rec.get("platform_id") or rec.get("post_id") or rec.get("video_id") or "")
                if not pid:
                    continue
                try:
                    title = ST._title(s, outlet)
                except Exception:  # noqa: BLE001  (a copy file that will not read: the slug will do)
                    title = str(s.get("source_title") or s.get("slug") or "")
                try:
                    went = ST.went_public(outlet, rec, s)
                except Exception:  # noqa: BLE001
                    went = rec.get("published_at") or s.get("publish_at")
                idx[pid] = {"platform": plat, "outlet": outlet, "day": bp.stem, "slot": str(s.get("slot") or key),
                            "slug": str(s.get("slug") or ""), "title": str(title or "")[:160],
                            "url": str(rec.get("url") or ""), "cover": s.get("cover") or None,
                            "went": _stamp(went), "factory": True}
    cut = now - timedelta(days=POST_DAYS)
    for plat, outlet in OUTLET_OF.items():
        for v in _pulled(acct, outlet):
            pid = str(v.get("platform_id") or "")
            went = _when(v.get("published_at"))
            if not pid or pid in idx or v.get("hidden") or went is None or went < cut:
                continue
            idx[pid] = {"platform": plat, "outlet": outlet, "day": "", "slot": "", "slug": "",
                        "title": str(v.get("text") or "").split("\n", 1)[0][:160], "url": str(v.get("url") or ""),
                        "cover": None, "went": _iso(went), "factory": False}
    return idx


def _index(cfg: C.Config, acct: C.Account, now: datetime) -> Dict[str, dict]:
    """The posts index, rebuilt when a board or a pulled file changed (or an hour passed)."""
    since = now.astimezone(cfg.tz()).date() - timedelta(days=POST_DAYS)
    sig = _boards_sig(acct, since)
    mark = SS.cursor(acct, "_index")
    at = _when(mark.get("at"))
    if mark.get("sig") == sig and at is not None and now - at < timedelta(hours=1) and SS.read(acct, "posts"):
        return SS.posts_index(acct)
    idx = build_posts_index(cfg, acct, now)
    SS.set_posts_index(acct, idx, now)
    SS.set_cursor(acct, "_index", {"sig": sig, "at": _iso(now)})
    return idx


def _post_of(index: Dict[str, dict], plat: str, pid: str, url: str = "") -> dict:
    p = index.get(str(pid)) or {}
    if not url and plat == "youtube":
        url = f"https://www.youtube.com/shorts/{pid}"
    return {"platform_id": str(pid), "outlet": OUTLET_OF[plat], "board": p.get("day") or None,
            "slot": p.get("slot") or None, "url": p.get("url") or url or "", "title": p.get("title") or ""}


# ------------------------------------------------------------------------------------------------ what blocks a read
def _blocked(cfg: C.Config, acct: C.Account, plat: str, need: str, now: datetime) -> str:
    """'' when this read may go; else why not (a reading hold, the poster's Meta hold, a missing permission)."""
    h = OW.held(cfg, acct, plat, "read", now)
    if h:
        return h
    if plat in META:
        try:
            from . import outlets as OL
            mh = OL.meta_hold(acct)
        except Exception:  # noqa: BLE001
            mh = ""
        if mh:
            return mh
    try:
        miss = CAP.missing(acct, plat, need)
    except Exception as e:  # noqa: BLE001  (an unreadable permission file is a missing permission)
        miss = [f"(the permission check failed: {type(e).__name__})"]
    if miss:
        return "waits for " + ", ".join(miss)
    return ""


def _slow(cfg: C.Config, acct: C.Account, plat: str, now: datetime) -> int:
    try:
        return SLOW_X if OW.slow(cfg, acct, plat, now) else 1
    except Exception:  # noqa: BLE001
        return 1


def _due(cur: dict, field: str, minutes: float, now: datetime) -> bool:
    last = _when(cur.get(field))
    return last is None or now - last >= timedelta(minutes=minutes) - SLACK


def _error(cfg: C.Config, acct: C.Account, plat: str, kind: str, e: BaseException, now: datetime) -> str:
    """A read that failed: the guard's tripwire for what the platform said, and the words for the cursor."""
    from . import direct as D
    from .stats import NotConnected
    if isinstance(e, NotConnected):
        return f"not connected ({e})"
    words = f"{type(e).__name__}: {str(e)[:240]}"
    if isinstance(e, D.PlatformError):
        try:
            trip = OW.tripwire(cfg, acct, plat, kind, e, now)
        except Exception as e2:  # noqa: BLE001
            REG._log_error("outward:tripwire", e2)
            trip = None
        if isinstance(trip, dict) and trip.get("why"):
            words += f" ({trip['why']})"
    return words


def _kind_of(e: BaseException) -> Tuple[str, Optional[int], str]:
    """(graph.classify's kind, Meta's code, the words) of a failed read; ('', None, words) for what is not a
    platform's answer."""
    from . import direct as D
    if not isinstance(e, D.PlatformError) and not isinstance(e, OSError):
        return "", None, str(e)
    try:
        from . import graph as G
        info = G.classify(e)
        return str(info.kind), info.code, str(e)
    except Exception:  # noqa: BLE001
        return "", None, str(e)


def _dm_error(cfg: C.Config, acct: C.Account, plat: str, e: BaseException, cur: dict,
              now: datetime) -> Tuple[str, dict]:
    """A message read that failed: (the words, the cursor fields to write). A missing permission marks the message
    permission missing (the read then waits for the next connect); a rate limit, a block, a sign-in that stopped or
    too many calls is the guard's, as a READ (a hold on reads, never on Andrew's own messages); anything else only
    rests the message reads themselves: three in an hour and they wait 6 hours."""
    from . import direct as D
    from .stats import NotConnected
    if isinstance(e, NotConnected):
        return f"not connected ({e})", {}
    words = f"{type(e).__name__}: {str(e)[:240]}"
    kind, _, msg = _kind_of(e)
    if kind == "perm":
        try:
            CAP.mark_missing(acct, plat, "message", msg, now)
        except Exception as e2:  # noqa: BLE001
            REG._log_error("capabilities:mark_missing", e2)
        return words + " (waits for the messages permission: connect again)", {}
    if kind in POST_WIDE and isinstance(e, D.PlatformError):
        return _error(cfg, acct, plat, "read", e, now), {}
    fails = [x for x in cur.get("dm_fails") or [] if (_when(x) or now) > now - DM_FAIL_WINDOW] + [_iso(now)]
    upd: dict = {"dm_fails": fails[-DM_FAILS:]}
    if len(fails) >= DM_FAILS:
        upd = {"dm_fails": [], "dm_rest_until": _iso(now + DM_BACKOFF)}
        words += f" ({DM_FAILS} failures in an hour: messages are read again at {_hhmm(cfg, now + DM_BACKOFF)})"
    return words, upd


def _fresh_usage() -> None:
    """Forget the thread's last rate-limit numbers before a read: another account's answer is never this one's."""
    try:
        from . import direct as D
        D._LAST.usage = {}
    except Exception:  # noqa: BLE001
        pass


def _note_usage(cfg: C.Config, acct: C.Account, plat: str, now: datetime) -> None:
    try:
        from . import direct as D
        OW.note_usage(cfg, acct, plat, D.last_usage(), now)
    except Exception as e:  # noqa: BLE001
        REG._log_error("outward:note_usage", e)


# ------------------------------------------------------------------------------------------------------ items
class _Batch:
    """What one poll of one account found: items to store, posts read in full (for gone), new DM messages."""

    def __init__(self, acct: C.Account, now: datetime):
        self.acct = acct
        self.now = now
        self.have: Dict[str, dict] = {it["id"]: it for it in SS.items(acct, since=now - timedelta(days=46))
                                      if isinstance(it, dict) and it.get("id")}
        self.by_author: Dict[str, List[str]] = {}
        for it in self.have.values():
            key = str((it.get("author") or {}).get("key") or "")
            if key:
                self.by_author.setdefault(key, []).append(str(it.get("text") or ""))
        self.shas = SS.forgotten(acct)                        # people forgotten here: never read back in
        self.keep = SW.settings(acct).get("engage.keep_days")  # nothing older is stored (prune would drop it)
        self.items: List[dict] = []
        self.complete: List[Tuple[str, str, set]] = []        # (platform, post id, comment ids read)
        self.lines: List[str] = []


def _item(plat: str, kind: str, pid: str, parent: Optional[str], post: Optional[dict], author: dict, text: str,
          at, **extra) -> dict:
    stamp = _stamp(at)
    out = {"id": SS.item_id(plat, pid, "dm" if kind == "dm" else "comment"), "platform": plat, "kind": kind,
           "platform_comment_id": pid if kind != "dm" else None, "parent_id": parent, "thread_id": None,
           "post": post, "author": author, "text": str(text or ""), "at": stamp,
           "like_count": int(extra.pop("like_count", 0) or 0),
           "can_reply_privately": extra.pop("can_reply_privately", None)}
    hidden = extra.pop("hidden", False)
    if hidden is not None:                    # None: the platform did not say, so what we know stays
        out["hidden"] = bool(hidden)
    if kind == "comment" and plat in META and stamp:
        out["private_until"] = _iso(_when(stamp) + timedelta(days=PRIVATE_DAYS))   # type: ignore
    out.update(extra)
    return out


def _said_hidden(row: dict, field: str) -> Optional[bool]:
    """The platform's hidden flag, or None when the answer did not carry the field (never read as shown)."""
    return bool(row[field]) if field in row and row[field] is not None else None


def _ig_items(row: dict, post: dict, k: Dict[str, str]) -> List[dict]:
    def author(r: dict) -> dict:
        frm = r.get("from") if isinstance(r.get("from"), dict) else {}
        uid = str(frm.get("id") or "")
        name = str(frm.get("username") or r.get("username") or "")
        mine = str(k.get("ig_username") or "").lower()
        us = bool(uid and uid == k.get("ig_user_id")) or bool(name and name.lower() == mine)
        return {"key": f"instagram:{uid or '@' + name}", "id": uid, "name": name, "is_us": us}
    cid = str(row.get("id") or "")
    out = [_item("instagram", "comment", cid, None, post, author(row), row.get("text") or "", row.get("timestamp"),
                 like_count=row.get("like_count"), hidden=_said_hidden(row, "hidden"))]
    for r in ((row.get("replies") or {}).get("data") or []) if isinstance(row.get("replies"), dict) else []:
        if isinstance(r, dict) and r.get("id"):
            out.append(_item("instagram", "reply", str(r["id"]), cid, post, author(r), r.get("text") or "",
                             r.get("timestamp"), like_count=r.get("like_count"), hidden=_said_hidden(r, "hidden")))
    return out


def _fb_item(row: dict, post: dict, k: Dict[str, str]) -> dict:
    cid = str(row.get("id") or "")
    frm = row.get("from") if isinstance(row.get("from"), dict) else {}
    uid = str(frm.get("id") or "")
    name = str(frm.get("name") or "") or "Someone on Facebook"
    author = {"key": f"facebook:{uid or 'anon-' + cid}", "id": uid, "name": name,
              "is_us": bool(uid and uid == k.get("page_id"))}
    parent = str((row.get("parent") or {}).get("id") or "") or None if isinstance(row.get("parent"), dict) else None
    return _item("facebook", "reply" if parent else "comment", cid, parent, post, author, row.get("message") or "",
                 row.get("created_time"), like_count=row.get("like_count"), hidden=_said_hidden(row, "is_hidden"),
                 can_reply_privately=row.get("can_reply_privately") if parent is None else None)


def _yt_items(thread: dict, index: Dict[str, dict], channel: str) -> List[dict]:
    sn = thread.get("snippet") or {}
    top = sn.get("topLevelComment") or {}
    tsn = top.get("snippet") or {}
    vid = str(sn.get("videoId") or tsn.get("videoId") or "")
    post = _post_of(index, "youtube", vid) if vid else None

    def author(s: dict) -> dict:
        cid = str((s.get("authorChannelId") or {}).get("value") or "")
        return {"key": f"youtube:{cid or '@' + str(s.get('authorDisplayName') or '')}", "id": cid,
                "name": str(s.get("authorDisplayName") or ""), "is_us": bool(cid and cid == channel)}
    top_id = str(top.get("id") or thread.get("id") or "")
    out = [_item("youtube", "comment", top_id, None, post, author(tsn),
                 tsn.get("textOriginal") or tsn.get("textDisplay") or "", tsn.get("publishedAt"),
                 like_count=tsn.get("likeCount"), can_reply=bool(sn.get("canReply", True)))]
    for r in ((thread.get("replies") or {}).get("comments") or []):
        rs = r.get("snippet") or {}
        if r.get("id"):
            out.append(_item("youtube", "reply", str(r["id"]), top_id, post, author(rs),
                             rs.get("textOriginal") or rs.get("textDisplay") or "", rs.get("publishedAt"),
                             like_count=rs.get("likeCount")))
    return out


def _store(cfg: C.Config, acct: C.Account, batch: _Batch) -> Tuple[List[dict], int]:
    """Tag, classify and store what the poll read; note the people; STOP; mark what our own replies answered and what
    the platform deleted or hid. (the new top-level items for the automations, how many new)"""
    now = batch.now
    maker = str(getattr(acct, "maker", "") or "")
    shas = batch.shas | SS.forgotten(acct)                # read again: a forget pressed while this poll ran counts
    gone = {it["id"] for it in batch.items if SS.is_forgotten(str((it.get("author") or {}).get("key") or ""), shas)}
    batch.items = [it for it in batch.items
                   if it["id"] not in gone and not _our_reply_under(it, gone)    # ours @names them: not read back
                   and (it["id"] in batch.have or SS.kept(it.get("at"), now, batch.keep))]
    rows: List[dict] = []
    fresh: List[dict] = []
    seen_now: Dict[str, List[str]] = {}
    for it in batch.items:
        old = batch.have.get(it["id"])
        if old is not None:                       # the platform's fields only: tags, suggestion and window stay
            rows.append({k: v for k, v in it.items() if k not in ("tags", "suggest", "private_until", "post")
                         or (k == "post" and not old.get("post"))})
            continue
        a = it.get("author") or {}
        key = str(a.get("key") or "")
        known = SS.contact(acct, key).get("first_at") is not None or bool(seen_now.get(key))
        history = batch.by_author.get(key, []) + seen_now.get(key, [])
        it["tags"] = K.tags(it.get("text"), maker) if not a.get("is_us") else []
        it["suggest"] = K.suggest(it.get("text"), dict(a, known=known), history, maker)
        it["suggested_reply"] = None
        seen_now.setdefault(key, []).append(str(it.get("text") or ""))
        rows.append(it)
        fresh.append(it)
    new_ids: List[str] = []
    if rows:
        new_ids, _ = SS.upsert_items(acct, rows, now)
    new = set(new_ids)
    after: List[dict] = []
    ours = [it for it in fresh if it["id"] in new and (it.get("author") or {}).get("is_us")
            and it.get("kind") == "reply" and it.get("parent_id")]
    answered = set()                              # read back with our reply under it: not for the automations
    for it in ours:
        answered.add(SS.item_id(it["platform"], str(it["parent_id"])))
        _answered_by_us(acct, it, now)
    for it in fresh:
        if it["id"] not in new:
            continue
        a = it.get("author") or {}
        key = str(a.get("key") or "")
        if a.get("is_us"):
            continue
        if key:
            try:
                SS.touch_contact(acct, key, now, name=str(a.get("name") or ""), inbound=True)
                if "stop" in (it.get("tags") or []) or K.is_stop(it.get("text")):
                    where = "a message" if it.get("kind") == "dm" else f"a {it.get('kind') or 'comment'}"
                    SS.stop_contact(acct, key, now, why=f"{SS.STOP_WHY} in {where} ({it['id']})")
            except SystemExit:
                pass
        if it.get("kind") in ("comment", "dm") and not it.get("hidden") and it["id"] not in answered:
            after.append(dict(it, state="new"))
    _sync_states(acct, batch, now)
    return after, len(new)


def _our_reply_under(it: dict, ids: set) -> bool:
    """Our own reply under one of these comments (a forgotten person's, read in this same poll)?"""
    if not ids or not (it.get("author") or {}).get("is_us") or not it.get("parent_id"):
        return False
    try:
        return SS.item_id(str(it.get("platform") or ""), str(it["parent_id"])) in ids
    except SystemExit:
        return False


def _answered_by_us(acct: C.Account, reply: dict, now: datetime) -> None:
    """Our own reply read back (from the phone app, or the Inbox's): its comment counts as answered."""
    parent = SS.item_id(reply["platform"], str(reply["parent_id"]))
    it = SS.get_item(acct, parent)
    if it is not None and it.get("state") == "new":
        try:
            SS.set_state(acct, parent, "answered", "platform", now, answered=reply.get("at"))
        except SystemExit:
            pass


def _sync_states(acct: C.Account, batch: _Batch, now: datetime) -> None:
    """Hidden or shown on the platform (the phone app too), and deleted there: the item follows."""
    for it in batch.items:
        old = batch.have.get(it["id"])
        if old is None or old.get("state") == "gone" or "hidden" not in it:
            continue
        try:
            if it.get("hidden") and old.get("state") not in ("hidden",):
                SS.set_state(acct, it["id"], "hidden", "platform", now)
            elif not it.get("hidden") and old.get("state") == "hidden":
                back = "answered" if old.get("answered") or _sent(old) else "new"
                SS.set_state(acct, it["id"], back, "platform", now)
        except SystemExit:
            pass
    for plat, pid, ids in batch.complete:
        kinds = ("comment", "reply") if plat == "facebook" or not ids else ("comment",)   # a deleted post: all
        for iid, old in batch.have.items():
            post = old.get("post") or {}
            if (old.get("platform") != plat or str(post.get("platform_id") or "") != pid or iid in ids
                    or old.get("kind") not in kinds or old.get("state") == "gone"):
                continue
            if plat == "youtube" and (old.get("state") == "hidden" or old.get("hidden")):
                continue                          # held for review: YouTube's list leaves it out, it is not deleted
            t = _when(old.get("at"))
            if t is not None and t > now - timedelta(minutes=2):
                continue                          # written while we read: not seen on this read yet
            try:
                SS.set_state(acct, iid, "gone", "platform", now)
            except SystemExit:
                pass


def _sent(item: dict) -> bool:
    """Has a reply of ours (public or private) gone out on this item?"""
    return any(isinstance(r, dict) and r.get("state") == "done" and r.get("kind") in ("reply_public", "reply_private")
               for r in item.get("replies") or [])


# ------------------------------------------------------------------------------------------------------ the polls
def _changed(m: dict, old_count) -> bool:
    """Did the post's comment count move (or is it first seen with comments)?"""
    count = m.get("count")
    if count is None:
        return False
    return count > 0 if old_count is None else count != old_count


def _every(m: dict, old_count, now: datetime, slow: int) -> Optional[timedelta]:
    """How often an unchanged post's comments are read: under 48 hours every poll (every 30 minutes when the
    platform gave its count, which a new comment moves), 2 to 7 days hourly, 7 to 30 days every 6 hours; None for
    never (no comments, or past 30 days)."""
    count = m.get("count")
    if count == 0 and not old_count:
        return None
    went = _when(m.get("at"))
    age = now - went if went is not None else timedelta(0)
    for upto, every in DECAY:
        if age < upto:
            if every == timedelta(0) and count is not None:
                every = FRESH_KNOWN
            return every * slow
    return None


def _post_due(m: dict, old_count, last_fetch: Optional[datetime], now: datetime, slow: int) -> bool:
    """Read this post's comments now? Its count changed (or is first seen with comments), or its decay is due."""
    if _changed(m, old_count):
        return True
    every = _every(m, old_count, now, slow)
    if every is None:
        return False
    return last_fetch is None or now - last_fetch >= every - SLACK


def _order(m: dict, old_count, last_fetch: Optional[datetime], now: datetime, slow: int, poll: timedelta) -> tuple:
    """Which due post is read first: a moved count first (newest post first), then the decay reads by how overdue
    they are against their own interval (never by the post's age, which starved the older posts)."""
    if _changed(m, old_count):
        went = _when(m.get("at"))
        return (0, -(went.timestamp() if went is not None else 0.0))
    if last_fetch is None:
        return (1, -1e12)
    every = _every(m, old_count, now, slow) or poll
    every = max(every, poll)
    return (1, -((now - last_fetch) / every))


def _post_bad(bad: dict, mid: str, now: datetime) -> str:
    """'' when the post may be read now; else why it waits (it would not read before)."""
    b = bad.get(mid)
    if not isinstance(b, dict):
        return ""
    if b.get("gone"):
        return "deleted"
    until = _when(b.get("until"))
    return f"waits until {_iso(until)}" if until is not None and until > now else ""


def _poll_meta(cfg: C.Config, acct: C.Account, plat: str, now: datetime, force: bool, index: Dict[str, dict],
               batch: _Batch) -> List[str]:
    from . import engage_api as API
    words = PLAT_WORDS[plat]
    cur = SS.cursor(acct, plat)
    slow = _slow(cfg, acct, plat, now)
    poll_minutes = int(SW.settings(acct)["engage.poll_minutes"])  # type: ignore
    every = poll_minutes * slow
    if not force and not _due(cur, "read_at", every, now):
        return []
    why = _blocked(cfg, acct, plat, "read_comments", now)
    if why:
        SS.set_cursor(acct, plat, {"waits": why, "checked_at": _iso(now)})
        return [f"  {acct.name} {words} comments: not read, {why}"]
    _fresh_usage()
    try:
        k = API.keys(acct)
        if plat == "instagram":
            listed = API.ig_media(acct, now - timedelta(days=IG_LIST_DAYS))
        else:
            listed = API.fb_reels(acct, now - timedelta(days=POST_DAYS))
    except Exception as e:  # noqa: BLE001  (one platform's failure never stops the others)
        err = _error(cfg, acct, plat, "read", e, now)
        SS.set_cursor(acct, plat, {"error": err, "error_at": _iso(now), "waits": "", "checked_at": _iso(now)})
        return [f"  {acct.name} {words} comments: could not read, {err}"]
    counts = dict(cur.get("counts") or {})
    fetched = dict(cur.get("fetched") or {})
    bad = {k2: v for k2, v in (cur.get("bad") or {}).items() if isinstance(v, dict)}
    cands: Dict[str, dict] = {str(m["id"]): m for m in listed}
    cut = now - timedelta(days=POST_DAYS)
    for pid, p in index.items():
        went = _when(p.get("went"))
        if p.get("platform") == plat and pid not in cands and went is not None and went >= cut:
            cands[pid] = {"id": pid, "count": None, "at": p.get("went"), "url": p.get("url") or ""}
    due = [m for mid, m in cands.items() if not _post_bad(bad, mid, now)
           and _post_due(m, counts.get(mid), _when(fetched.get(mid)), now, slow)]
    poll = timedelta(minutes=poll_minutes)
    due.sort(key=lambda m: _order(m, counts.get(str(m["id"])), _when(fetched.get(str(m["id"]))), now, slow, poll))
    read_n = 0
    err = ""
    failed: List[str] = []
    for m in due[:MAX_POSTS]:
        mid = str(m["id"])
        post = _post_of(index, plat, mid, m.get("url") or "")
        try:
            if plat == "instagram":
                rows, complete = API.ig_comments(acct, mid)
                got = [x for r in rows for x in _ig_items(r, post, k)]
            else:
                rows, complete = API.fb_comments(acct, mid)
                got = [_fb_item(r, post, k) for r in rows if r.get("id")]
        except Exception as e:  # noqa: BLE001
            kind, code, msg = _kind_of(e)
            if kind in POST_WIDE:                  # the platform said no to everything: the guard's, and stop here
                err = _error(cfg, acct, plat, "read", e, now)
                b = bad.get(mid) if isinstance(bad.get(mid), dict) else {}
                if kind in ("timeout", "transient"):   # one post that never answers waits, the others go next time
                    n = int(b.get("n") or 0) + 1
                    bad[mid] = {"n": n, "at": _iso(now), "why": f"{type(e).__name__}: {msg[:160]}",
                                "until": _iso(now + min(POST_BACKOFF * 2 ** (n - 1), POST_BACKOFF_MAX))}
                break
            # one post's no (deleted by hand, taken down, not ours any more): that post waits, the rest are read,
            # and it never counts against the platform's read tripwire nor raises the account's red line
            b = bad.get(mid) if isinstance(bad.get(mid), dict) else {}
            n = int(b.get("n") or 0) + 1
            gone = kind == "gone"
            base = POST_GONE_BACKOFF if "does not exist" in msg else POST_BACKOFF
            bad[mid] = {"n": n, "at": _iso(now), "why": f"{type(e).__name__}: {msg[:160]}", "gone": gone,
                        "until": _iso(now + min(base * 2 ** (n - 1), POST_BACKOFF_MAX))}
            if gone:
                batch.complete.append((plat, mid, set()))      # its comments went with it
            failed.append(mid)
            continue
        batch.items += got
        if complete:
            batch.complete.append((plat, mid, {x["id"] for x in got}))
        fetched[mid] = _iso(now)
        bad.pop(mid, None)
        if m.get("count") is not None:
            counts[mid] = int(m["count"])
        read_n += 1
    if not err and len(failed) >= 3 and not read_n:     # every post refused: that is the account's, say so
        err = f"{len(failed)} posts would not read ({bad[failed[-1]]['why']})"
    for m in cands.values():                     # first sight of a post with no comments: its count is known now
        if m.get("count") is not None and str(m["id"]) not in counts and not m["count"]:
            counts[str(m["id"])] = 0
    keep = set(cands)
    _note_usage(cfg, acct, plat, now)
    SS.set_cursor(acct, plat, {"read_at": _iso(now), "counts": {k2: v for k2, v in counts.items() if k2 in keep},
                               "fetched": {k2: v for k2, v in fetched.items() if k2 in keep},
                               "bad": {k2: v for k2, v in bad.items() if k2 in keep},
                               "posts_read": read_n, "posts_failed": len(failed), "error": err,
                               "error_at": _iso(now) if err else "", "waits": "", "checked_at": _iso(now)})
    return [f"  {acct.name} {words} comments: could not read, {err}"] if err else []


def _poll_youtube(cfg: C.Config, acct: C.Account, now: datetime, force: bool, index: Dict[str, dict],
                  batch: _Batch) -> List[str]:
    from . import engage_api as API
    cur = SS.cursor(acct, "youtube")
    every = int(SW.settings(acct)["engage.youtube_minutes"])  # type: ignore
    if not force and not _due(cur, "read_at", every, now):
        return []
    why = _blocked(cfg, acct, "youtube", "read_comments", now)
    if not why and OW.yt_left(cfg, now) <= YT_MIN_UNITS:
        why = "this machine's YouTube units for today are nearly used up"
    channel = API.channel_id(acct) if not why else ""
    if not why and not channel:
        why = f"the YouTube sign-in has no channel saved (factory.py connect youtube {acct.slug})"
    if why:
        SS.set_cursor(acct, "youtube", {"waits": why, "checked_at": _iso(now)})
        return [f"  {acct.name} YouTube comments: not read, {why}"]
    seen = _when(cur.get("newest"))
    try:
        threads, complete = API.yt_threads(cfg, acct, channel, seen, max_pages=4 if seen is not None else 2)
    except Exception as e:  # noqa: BLE001
        err = _error(cfg, acct, "youtube", "read", e, now)
        SS.set_cursor(acct, "youtube", {"error": err, "error_at": _iso(now), "waits": "", "checked_at": _iso(now)})
        return [f"  {acct.name} YouTube comments: could not read, {err}"]
    newest = seen
    read: Dict[str, set] = {}
    for th in threads:
        got = _yt_items(th, index, channel)
        batch.items += got
        for x in got:
            read.setdefault(str((x.get("post") or {}).get("platform_id") or ""), set()).add(x["id"])
        t = _when(got[0].get("at")) if got else None
        if t is not None and (newest is None or t > newest):
            newest = t
    if complete:                                  # every thread the channel has: a stored one not in it was deleted
        vids = {str((it.get("post") or {}).get("platform_id") or "") for it in batch.have.values()
                if it.get("platform") == "youtube"} | set(read)
        for vid in sorted(vids - {""}):
            batch.complete.append(("youtube", vid, read.get(vid, set())))
    SS.set_cursor(acct, "youtube", {"read_at": _iso(now), "newest": _iso(newest) if newest else None,
                                    "threads_read": len(threads), "error": "", "error_at": "", "waits": "",
                                    "checked_at": _iso(now)})
    return []


def _poll_messages(cfg: C.Config, acct: C.Account, plat: str, now: datetime, force: bool,
                   batch: _Batch) -> List[str]:
    from . import engage_api as API
    words = PLAT_WORDS[plat]
    cur = SS.cursor(acct, plat)
    every = int(SW.settings(acct)["engage.dm_minutes"])  # type: ignore
    if plat == "facebook":
        every = max(every, FB_DM_MINUTES)
    every *= _slow(cfg, acct, plat, now)
    if not force and not _due(cur, "dm_at", every, now):
        return []
    why = _blocked(cfg, acct, plat, "message", now)
    rest = _when(cur.get("dm_rest_until"))
    if not why and rest is not None and rest > now:
        why = f"the last reads failed: read again at {_hhmm(cfg, rest)}"
    if why:
        SS.set_cursor(acct, plat, {"dm_waits": why, "dm_checked_at": _iso(now)})
        return [f"  {acct.name} {words} messages: not read, {why}"]
    seen = dict(cur.get("convs") or {})
    err = ""
    extra: dict = {}
    _fresh_usage()
    try:
        k = API.keys(acct)
        us = k["ig_user_id"] if plat == "instagram" else k["page_id"]
        convs = API.conversations(acct, plat)
        for c in convs[:MAX_CONVERSATIONS]:
            cid = str(c["id"])
            if c.get("is_owner") is False:         # another app owns the thread: never ours to read or answer
                continue
            if seen.get(cid) == c.get("updated_time"):  # unchanged (Check now too): not read again
                continue
            _pace()
            msgs = API.conversation_messages(acct, cid)
            _thread(acct, plat, cid, msgs, us, batch)
            seen[cid] = c.get("updated_time")
    except Exception as e:  # noqa: BLE001
        err, extra = _dm_error(cfg, acct, plat, e, cur, now)
    _note_usage(cfg, acct, plat, now)
    if not err:
        extra = {"dm_fails": [], "dm_rest_until": ""}
    SS.set_cursor(acct, plat, dict({"dm_at": _iso(now), "convs": dict(list(seen.items())[-200:]), "dm_error": err,
                                    "dm_error_at": _iso(now) if err else "", "dm_waits": "",
                                    "dm_checked_at": _iso(now)}, **extra))
    return [f"  {acct.name} {words} messages: could not read, {err}"] if err else []


def _pace() -> None:
    """The Conversations API's 2 calls a second per account: a rest before each thread's read."""
    if PACE_S > 0:
        time.sleep(PACE_S)


def _thread(acct: C.Account, plat: str, conv_id: str, msgs: List[dict], us: str, batch: _Batch) -> None:
    """One conversation read: the thread stored, its new messages from them as Inbox items."""
    tid = SS.thread_id(plat, conv_id)
    other: Dict[str, str] = {}
    rows = []
    for m in msgs:
        frm = m.get("from") if isinstance(m.get("from"), dict) else {}
        fid = str(frm.get("id") or "")
        from_us = bool(fid and fid == us)
        if not from_us and fid and not other:
            other = {"id": fid, "name": str(frm.get("username") or frm.get("name") or "")}
        if from_us and not other:
            for t in ((m.get("to") or {}).get("data") or []) if isinstance(m.get("to"), dict) else []:
                if isinstance(t, dict) and str(t.get("id") or "") != us:
                    other = {"id": str(t.get("id") or ""), "name": str(t.get("username") or t.get("name") or "")}
                    break
        rows.append({"id": str(m.get("id") or ""), "at": _stamp(m.get("created_time")), "from_us": from_us,
                     "text": str(m.get("message") or "")})
    rows = [r for r in rows if r["id"]]
    who = {"key": f"{plat}:{other.get('id') or 'anon-' + conv_id}", "id": other.get("id") or "",
           "name": other.get("name") or "", "is_us": False}
    if SS.is_forgotten(who["key"], batch.shas):
        return                                    # forgotten: their conversation is never stored again
    rows = [r for r in rows if SS.kept(r["at"], batch.now, batch.keep)]
    if not rows:
        return                                    # all of it older than engage.keep_days: prune already took it
    fresh = set(SS.upsert_thread(acct, {"id": tid, "platform": plat, "platform_thread_id": conv_id, "with": who},
                                 rows, batch.now))
    for r in rows:
        if r["id"] in fresh and not r["from_us"]:
            it = _item(plat, "dm", r["id"], None, None, who, r["text"], r["at"])
            it["thread_id"] = tid
            batch.items.append(it)


def poll(cfg: C.Config, acct: C.Account, now: Optional[datetime] = None, force: bool = False) -> List[str]:
    """One read of one account (steps 1 to 8 in the docstring). What it said."""
    now = _now(now)
    st = SW.settings(acct)
    plats = [p for p in (st.get("engage.platforms") or []) if p in PLATS]  # type: ignore
    index = _index(cfg, acct, now)
    batch = _Batch(acct, now)
    lines: List[str] = []
    for plat in META:
        if plat in plats:
            lines += _poll_meta(cfg, acct, plat, now, force, index, batch)
    if "youtube" in plats:
        lines += _poll_youtube(cfg, acct, now, force, index, batch)
    if SW.mode(acct, "engage.messages") == "on":
        for plat in META:
            if plat in plats:
                lines += _poll_messages(cfg, acct, plat, now, force, batch)
    after, n = _store(cfg, acct, batch)
    if n:
        lines.append(f"  {acct.name}: {n} new comment{'s' if n != 1 else ''} or message{'s' if n != 1 else ''}")
    if after:
        for _, said in REG.calls(REG.AFTER_POLL, cfg, acct, after, now):
            lines += [str(x) for x in (said if isinstance(said, list) else []) if x]
    stamp = _iso(now)
    if any(SS.cursor(acct, p).get(f) == stamp for p in PLATS for f in ("read_at", "dm_at")):
        SS.set_cursor(acct, "_poll", {"at": stamp, "new": n})        # "read 4 min ago" only for a real read
    return lines


def _prune_daily(cfg: C.Config, acct: C.Account, now: datetime) -> List[str]:
    """Once a day, for an account that has Inbox files: what is older than engage.keep_days goes."""
    if not SS.root(acct).is_dir():
        return []
    day = now.astimezone(cfg.tz()).date().isoformat()
    if SS.cursor(acct, "_prune").get("day") == day:
        return []
    keep = int(SW.settings(acct)["engage.keep_days"])  # type: ignore
    n = SS.prune(acct, now, keep_days=keep)
    SS.set_cursor(acct, "_prune", {"day": day, "removed": n})
    return [f"  {acct.name} Inbox: {n} records past {keep} days removed"] if n else []


def tick(cfg: C.Config, now: Optional[datetime] = None, quiet: bool = True) -> List[str]:
    """The social tick's step: read every account whose engage.read is on, then the daily prune. Nothing at all for
    an account with it off (no call, no file)."""
    now = _now(now)
    out: List[str] = []
    for a in accounts(cfg):
        try:
            if reading(a):
                out += poll(cfg, a, now)
            out += _prune_daily(cfg, a, now)
        except Exception as e:  # noqa: BLE001  (one account never stops the others)
            REG._log_error("engage:tick", e)
            out.append(f"  {a.name} Inbox: could not read ({type(e).__name__}: {e})"[:300])
    return out


def check(cfg: C.Config, now: Optional[datetime] = None) -> List[str]:
    """Check now: one read of every account that reads, intervals aside (counts and holds still apply), never at
    the same time as the tick's read."""
    now = _now(now)
    out: List[str] = []
    with store.try_lock("engage-tick") as got:
        if not got:
            return ["  the Inbox is being read already"]
        for a in accounts(cfg):
            if not reading(a):
                continue
            try:
                out += poll(cfg, a, now, force=True)
            except Exception as e:  # noqa: BLE001
                REG._log_error("engage:check", e)
                out.append(f"  {a.name} Inbox: could not read ({type(e).__name__}: {e})"[:300])
    return out


def kick_check(cfg: C.Config, now: Optional[datetime] = None) -> str:
    """The Inbox's Check now: start `factory.py engage check` in the background, at most every 2 minutes. Never from
    a test, an offline run or a copy of the state."""
    from . import net
    now = _now(now)
    if not any(reading(a) for a in accounts(cfg)):
        return "Reading comments is off for every account: switch engage.read on first (Log, Switches)."
    p = store.path("social", "engage-check.json")
    last = _when((store.read(p, {}) or {}).get("at"))
    if last is not None and now - last < CHECK_GAP:
        nxt = last + CHECK_GAP
        return f"Read {int((now - last).total_seconds())} seconds ago: the next check can start at {_hhmm(cfg, nxt)}."
    why = net.why_not()
    if why:
        return f"Not started ({why})."
    store.write(p, {"at": _iso(now)})
    log = C.STATE / "logs" / "engage-check.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLIP_FACTORY_LOG_STAMP="1", CLIP_FACTORY_CONFIG=str(cfg.path),
               CLIP_FACTORY_STATE=str(C.STATE))
    with open(log, "a") as out:
        subprocess.Popen([sys.executable, str(C.ROOT / "factory.py"), "engage", "check", "--quiet"], cwd=str(C.ROOT),
                         env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                         start_new_session=True)
    return "Checking now: new comments show up here in a minute."


# ------------------------------------------------------------------------------------------------------ the executor
def _target(action: dict) -> dict:
    t = action.get("target")
    return t if isinstance(t, dict) else {}


def _person_id(t: dict) -> str:
    return str(t.get("to_id") or str(t.get("to") or "").partition(":")[2] or "")


def execute(cfg: C.Config, acct: C.Account, action: dict) -> dict:
    """Make the one platform call an action stands for (outward calls it outside every lock). {"id", "url",
    "endpoint"}; a refusal raises (direct.PlatformError), as outward expects."""
    from . import engage_api as API
    kind = str(action.get("kind") or "")
    plat = str(action.get("platform") or "")
    t = _target(action)
    text = str(action.get("text") or "")
    cid = str(t.get("comment_id") or "")
    if kind == "reply_public":
        got = API.reply_public(cfg, acct, plat, cid, text)
    elif kind == "reply_private":
        if plat not in META:
            raise SystemExit(f"{PLAT_WORDS.get(plat, plat)} has no private replies")
        got = API.reply_private(acct, cid, text)
    elif kind == "dm":
        to = _person_id(t)
        if not to:
            raise SystemExit("a message needs the person it goes to")
        got = API.send_dm(acct, to, text)
    elif kind in ("hide", "unhide"):
        got = API.set_hidden(acct, plat, cid, kind == "hide")
    elif kind == "like":
        got = API.like_comment(acct, plat, cid)
    elif kind == "yt_moderate":
        status = str(t.get("status") or "")
        got = API.yt_moderate(cfg, acct, cid, status)
    else:
        raise SystemExit(f"{kind}: not something the Inbox sends")
    return {"id": got.get("id"), "url": t.get("post_url") or None, "endpoint": got.get("endpoint")}


def _us(acct: C.Account, plat: str) -> set:
    from . import engage_api as API
    if plat == "youtube":
        return {API.channel_id(acct)} - {""}
    k = API.keys(acct)
    return {k["ig_user_id"], k["page_id"]} - {""}


def find(cfg: C.Config, acct: C.Account, action: dict) -> Optional[dict]:
    """Did a send with no answer go after all? Our reply under the comment with the same words; for a private reply
    or a message, the conversation's last 20 messages; for a hide, the comment's hidden state. {"id"} or None."""
    from . import engage_api as API
    kind = str(action.get("kind") or "")
    plat = str(action.get("platform") or "")
    t = _target(action)
    text = str(action.get("text") or "")
    cid = str(t.get("comment_id") or "")
    if kind == "reply_public":
        us = _us(acct, plat)
        for r in API.replies_on(cfg, acct, plat, cid):
            if (not r.get("from_id") or r.get("from_id") in us) and _same_words(r.get("text"), text):
                return {"id": r.get("id")}
        return None
    if kind in ("reply_private", "dm"):
        person = _person_id(t)
        if not person:
            return None
        us = _us(acct, plat)
        for m in API.messages_with(acct, plat, person):
            if m.get("from_id") in us and _same_words(m.get("text"), text):
                return {"id": m.get("id")}
        return None
    if kind in ("hide", "unhide"):
        h = API.hidden_now(acct, plat, cid)
        return {"id": cid} if h is not None and h == (kind == "hide") else None
    if kind == "like":
        return {"id": cid} if API.liked_now(acct, plat, cid) else None
    if kind == "yt_moderate":
        st = API.yt_status(cfg, acct, cid)
        return {"id": cid} if st and st == str(t.get("status") or "") else None
    return None


def still_signed(cfg: C.Config, acct: C.Account, action: dict) -> str:
    """'' when the action is still what was signed: a click's exact words (the sha the click carried), or a rule
    that still holds its signature (rules.still_signed). Else why it must not go."""
    signed = action.get("signed") if isinstance(action.get("signed"), dict) else {}
    what = str(signed.get("what") or "")
    kind = str(action.get("kind") or "")
    if what == "click":
        if kind in TEXT_KINDS and signed.get("text_sha") != _sha(str(action.get("text") or "")):
            return "these are not the words that were clicked"
        return ""
    if what.startswith("rule:"):
        person = str(_target(action).get("to") or "")
        if person and SS.stopped(acct, person):
            return "they said STOP: nothing automated goes to them"
        f = REG.fn("rules:still_signed")
        if f is None:
            return "the automations part (clipfactory/rules.py) is not built here"
        return str(f(cfg, acct, action) or "")
    return "not signed: only a click on the Inbox or a signed rule sends from here"


def _why_of(cfg: C.Config, acct: C.Account, action: dict) -> str:
    """The words of the last log row of this action (why it was refused, skipped or failed)."""
    try:
        since = _when(action.get("at"))
        since = since - timedelta(minutes=1) if since is not None else None
        why = ""
        for r in OW.rows(cfg, acct.slug, since=since):
            if r.get("id") == action.get("id") and r.get("why"):
                why = str(r["why"])
        return why[:300]
    except Exception:  # noqa: BLE001
        return ""


def on_state(cfg: C.Config, acct: C.Account, action: dict, state, result) -> None:
    """What happened to a send, written where the Inbox shows it: the reply on its comment (Sending, Sent, Practice,
    or why not), the comment answered or hidden, the message in its conversation, the person's last word from us."""
    state = str(state or "")
    if not state or state in ("queued", "sending", "gone"):
        return None
    now = _now()
    kind = str(action.get("kind") or "")
    t = _target(action)
    signed = action.get("signed") if isinstance(action.get("signed"), dict) else {}
    manual = signed.get("what") == "click"
    rid = (result or {}).get("id") if isinstance(result, dict) else None
    why = "" if state == "done" else _why_of(cfg, acct, action)
    entry = {"action": action.get("id"), "state": state, "at": _iso(now), "result_id": rid, "why": why,
             "kind": kind}
    iid = str(t.get("item") or "")
    if kind == "dm":
        _dm_state(acct, action, entry, state, now)
        return None
    if not iid or SS.get_item(acct, iid) is None:
        return None
    if not manual:
        entry.update(by="rule", text=str(action.get("text") or ""), private=kind == "reply_private",
                     rule=(action.get("origin") or {}).get("rule"))
    SS.add_reply(acct, iid, entry)
    if state != "done":
        return None
    it = SS.get_item(acct, iid) or {}
    person = str(t.get("to") or (it.get("author") or {}).get("key") or "")
    try:
        if kind in ("reply_public", "reply_private"):
            if it.get("state") == "new":
                SS.set_state(acct, iid, "answered", "click" if manual else "rule", now, answered=_iso(now))
            if person:
                SS.touch_contact(acct, person, now, outbound=True, auto=not manual,
                                 private_reply=str(t.get("comment_id") or "") if kind == "reply_private" else "")
        elif kind == "hide" or (kind == "yt_moderate" and t.get("status") == "heldForReview"):
            SS.set_state(acct, iid, "hidden", "click", now)
        elif kind == "unhide" or (kind == "yt_moderate" and t.get("status") == "published"):
            SS.unhide(acct, iid, "click", "answered" if _sent(it) else "new", now)
    except SystemExit:
        pass
    return None


def _dm_state(acct: C.Account, action: dict, entry: dict, state: str, now: datetime) -> None:
    t = _target(action)
    tid = str(t.get("item") or t.get("thread") or "")
    if not tid or SS.thread(acct, tid) is None:
        return
    with SS.edit(acct, "threads") as d:
        th = (d.get("threads") or {}).get(tid)
        if not isinstance(th, dict):
            return
        pend = [p for p in th.get("pending") or [] if isinstance(p, dict)]
        for p in pend:
            if p.get("action") == entry["action"]:
                p.update(entry)
                break
        else:
            pend.append(dict(entry, text=str(action.get("text") or ""), by="rule"))
        th["pending"] = pend[-10:]
    if state == "done":
        msg = {"id": str(entry.get("result_id") or action.get("id")), "at": _iso(now), "from_us": True,
               "text": str(action.get("text") or "")}
        SS.upsert_thread(acct, {"id": tid}, [msg], now)
        try:
            SS.set_thread_state(acct, tid, "done", "click", now)
            person = str(t.get("to") or "")
            if person:
                signed = action.get("signed") if isinstance(action.get("signed"), dict) else {}
                SS.touch_contact(acct, person, now, outbound=True, auto=signed.get("what") != "click")
        except SystemExit:
            pass


# ------------------------------------------------------------------------------------------------------ counting
def unanswered(acct: C.Account, now: Optional[datetime] = None) -> Tuple[int, int, int]:
    """(comments to answer, conversations with something unread, comments suggested for hiding)."""
    now = _now(now)
    keep = int(SW.settings(acct)["engage.keep_days"])  # type: ignore
    n = hide = 0
    for it in SS.items(acct, since=now - timedelta(days=keep)):
        if (it.get("author") or {}).get("is_us") or it.get("kind") == "dm":
            continue
        if it.get("state") == "new" and not it.get("hidden"):
            if (it.get("suggest") or {}).get("hide"):
                hide += 1
            else:
                n += 1
    m = sum(1 for t in SS.threads(acct) if t.get("state") == "new" and int(t.get("unread") or 0) > 0)
    return n, m, hide


def notes(acct: C.Account) -> List[Tuple[str, str, str, bool]]:
    """What the cursor says waits or failed, for the parts read now only: (platform, "comments"|"messages", words,
    failed). A platform taken out of engage.platforms, or messages switched off, leaves nothing behind (its last
    error is never cleared by a read that no longer happens)."""
    if not reading(acct):
        return []
    plats = [p for p in (SW.settings(acct).get("engage.platforms") or []) if p in PLATS]  # type: ignore
    msgs = SW.mode(acct, "engage.messages") == "on"
    out: List[Tuple[str, str, str, bool]] = []
    for plat in plats:
        cur = SS.cursor(acct, plat)
        fields = [("waits", "comments", False), ("error", "comments", True)]
        if msgs and plat in META:
            fields += [("dm_waits", "messages", False), ("dm_error", "messages", True)]
        for f, what, failed in fields:
            if cur.get(f):
                out.append((plat, what, str(cur[f]), failed))
    return out


def last_read(acct: C.Account) -> Optional[datetime]:
    return _when(SS.cursor(acct, "_poll").get("at"))


# ---------------------------------------------------------------------------------------------------- the page's plugs
def health(cfg: C.Config, now: datetime, accts) -> Tuple[str, str]:
    """A red line on the Clips page when an account's last read failed (a broken sign-in, a refusal); '' otherwise
    (a permission still to come is the doctor's, not a red line)."""
    bad: List[str] = []
    for a in [x for x in accts or [] if not _is_client(x)]:
        for plat, what, words, failed in notes(a):
            if failed:
                bad.append(f"{a.name} {PLAT_WORDS[plat]} {what}: the last read failed ({words[:120]})")
    if not bad:
        return ("", "")
    more = f" (and {len(bad) - 2} more)" if len(bad) > 2 else ""
    return ("bad", "Inbox: " + "; ".join(bad[:2]) + more + ".")


def summary(cfg: C.Config, now: datetime, accts) -> List[dict]:
    """The Today band's Inbox chip: "Inbox: 6 to answer" (and the messages, and what is suggested for hiding).
    [] while no account reads."""
    acc = [a for a in accts or [] if not _is_client(a) and reading(a)]
    if not acc:
        return []
    n = m = hide = 0
    for a in acc:
        x, y, z = unanswered(a, now)
        n, m, hide = n + x, m + y, hide + z
    href = "/inbox" + (f"?acct={acc[0].slug}" if len(acc) == 1 and len(accts or []) == 1 else "")
    out: List[dict] = []
    if n or m:
        words = "Inbox: " + " and ".join(w for w in (f"{n} to answer" if n else "",
                                                     f"{m} message{'s' if m != 1 else ''}" if m else "") if w)
        out.append({"key": "inbox", "words": words, "href": href, "level": "calm", "order": 20})
    if hide:
        out.append({"key": "inbox-hide", "words": f"{hide} suggested to hide",
                    "href": href + ("&" if "?" in href else "?") + "filter=hide", "level": "warn", "order": 21})
    return out


def morning_line(cfg: C.Config, now: datetime) -> str:
    """'6 comments to answer and 2 messages' for the 06:30 notice's Also line; '' when there is none."""
    n = m = 0
    for a in accounts(cfg):
        if reading(a):
            x, y, _ = unanswered(a, now)
            n, m = n + x, m + y
    parts = [f"{n} comment{'s' if n != 1 else ''} to answer" if n else "",
             f"{m} message{'s' if m != 1 else ''}" if m else ""]
    return " and ".join(p for p in parts if p)


def _fix_for(acct: C.Account, plat: str, miss: List[str]) -> str:
    if any("not read yet" in x for x in miss):
        return f"factory.py social probe {acct.slug} scopes"
    if plat == "youtube":                         # reading a channel's comments takes force-ssl (--with comments)
        return CAP.yt_connect_line(acct, "comments")
    return (f"tick them on the system user's token (SETUP-POSTING, comments permissions), then factory.py connect "
            f"meta {acct.slug}")


def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """Per account: off and how to switch it on; each platform it reads and what that waits for; when it last read
    and how many wait for an answer; messages and Meta's review; replies. Read only, never raises."""
    now = _now(now) if isinstance(now, datetime) else _now()
    out: List[Tuple[Optional[bool], str, str]] = []
    for a in accounts(cfg):
        try:
            if not reading(a):
                out.append((None, f"{a.name} inbox: off (factory.py social on {a.slug} engage.read); engage.messages "
                                  f"and replies wait for it, messages also for Meta's review (Advanced Access)", ""))
                continue
            st = SW.settings(a)
            plats = [p for p in (st.get("engage.platforms") or []) if p in PLATS]  # type: ignore
            ok_plats = []
            for plat in plats:
                miss = CAP.missing(a, plat, "read_comments")
                cur = SS.cursor(a, plat)
                if miss:
                    out.append((False, f"{a.name} inbox on {PLAT_WORDS[plat]}: on, but the token lacks "
                                       f"{', '.join(miss)}", _fix_for(a, plat, miss)))
                elif cur.get("error"):
                    at = _when(cur.get("error_at"))
                    out.append((False, f"{a.name} inbox on {PLAT_WORDS[plat]}: the last read failed"
                                       f"{' at ' + _hhmm(cfg, at) if at else ''}: {str(cur['error'])[:160]}",
                                f"factory.py engage poll {a.slug} --force"))
                else:
                    ok_plats.append(PLAT_WORDS[plat])
            last = last_read(a)
            if last is None:
                out.append((None, f"{a.name} inbox: on, not read yet (the social tick reads every "
                                  f"{st['engage.poll_minutes']} minutes)", ""))
            elif ok_plats:
                n, m, _ = unanswered(a, now)
                stale = now - last > timedelta(hours=1)
                out.append((not stale, f"{a.name} inbox ({', '.join(ok_plats)}): read {_ago(now, last)}, "
                                       f"{n} unanswered" + (f", {m} unread conversations" if m else ""),
                            "factory.py timer status (the social tick reads the Inbox)" if stale else ""))
            msgs = SW.mode(a, "engage.messages")
            if msgs == "on":
                mp = [p for p in plats if p in META]
                miss = sorted({x for p in mp for x in CAP.missing(a, p, "message")})
                if miss:
                    out.append((None, f"{a.name} messages: wait for Meta's review (Advanced Access): "
                                      f"{', '.join(miss)}", ""))
                elif st.get("messages_access") != "advanced":
                    out.append((None, f"{a.name} messages: read; answers reach only app testers until Meta's review "
                                      f"(Advanced Access)", ""))
                for plat, what, words, failed in notes(a):
                    if what == "messages" and failed:
                        out.append((False, f"{a.name} messages on {PLAT_WORDS[plat]}: the last read failed: "
                                           f"{words[:160]}", f"factory.py engage poll {a.slug} --force"))
            else:
                out.append((None, f"{a.name} messages: off; they wait for Meta's review (Advanced Access)", ""))
            out.append((None, f"{a.name} replies from the Inbox: {SW.mode(a, 'replies')}" +
                        (f" (factory.py social on {a.slug} replies --practice)" if SW.mode(a, "replies") == "off"
                         else ""), ""))
            for name in ("threads", "contacts", "saved", "cursor", "posts"):
                p = SS.root(a) / f"{name}.json"
                if p.exists() and not isinstance(store.read(p, None), dict):
                    out.append((False, f"{a.name}: state/{a.slug}/engage/{name}.json will not read",
                                "move it aside (the Inbox starts it again empty)"))
        except Exception as e:  # noqa: BLE001  (the doctor never stops over one account)
            out.append((False, f"{getattr(a, 'name', a)} inbox: could not check ({type(e).__name__}: {e})"[:300],
                        f"look at state/{a.slug}/engage/"))
    return out


# ------------------------------------------------------------------------------------------------------ the command
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory.py engage", description="Clip Review's Inbox: comments and messages.")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("status", help="what each account reads, when, and what waits for an answer")
    s.add_argument("account", nargs="?")
    s = sub.add_parser("poll", help="read one account now (engage.read must be on)")
    s.add_argument("account")
    s.add_argument("--force", action="store_true", help="read every platform now, intervals aside")
    sub.add_parser("check", help="the Inbox's Check now: one read of every account that reads")
    s = sub.add_parser("list", help="the newest comments and messages")
    s.add_argument("account")
    s.add_argument("--state", choices=SS.STATES)
    s.add_argument("--platform", choices=PLATS)
    s.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("posts", help="rebuild the posts index (which post is which clip)")
    s.add_argument("account")
    s = sub.add_parser("prune", help="remove what is older than engage.keep_days")
    s.add_argument("account", nargs="?")
    s = sub.add_parser("forget", help="remove a person from the Inbox and blank them in the log")
    s.add_argument("account")
    s.add_argument("--person", required=True, help="platform:id, as the Inbox shows it")
    s.add_argument("--by", required=True)
    return p


def cli(cfg: C.Config, argv: List[str]) -> None:
    """factory.py engage ... (the module docstring lists the commands)."""
    quiet = "--quiet" in argv
    argv = [x for x in argv if x != "--quiet"]
    a = _parser().parse_args(argv or ["status"])
    now = _now(cfg.now())
    lines: List[str] = []
    if a.cmd in (None, "status"):
        accts = [_account(cfg, a.account)] if getattr(a, "account", None) else accounts(cfg)
        for x in accts:
            if not reading(x):
                lines.append(f"{x.name}: reading comments is off (factory.py social on {x.slug} engage.read)")
                continue
            n, m, hide = unanswered(x, now)
            lines.append(f"{x.name}: read {_ago(now, last_read(x))}; {n} to answer, {m} unread conversations, "
                         f"{hide} suggested to hide; replies {SW.mode(x, 'replies')}, messages "
                         f"{SW.mode(x, 'engage.messages')}")
            for plat, what, words, failed in notes(x):
                lines.append(f"  {PLAT_WORDS[plat]} {what}: {'failed' if failed else 'waits'}, {words}")
    elif a.cmd == "poll":
        x = _account(cfg, a.account)
        if not reading(x):
            raise SystemExit(f"{x.name}: reading comments is off (factory.py social on {x.slug} engage.read)")
        with store.try_lock("engage-tick") as got:
            if not got:
                raise SystemExit("the Inbox is being read already: try again in a minute")
            lines += poll(cfg, x, now, force=a.force) or [f"{x.name}: read, nothing new"]
    elif a.cmd == "check":
        lines += check(cfg, now)
    elif a.cmd == "list":
        x = _account(cfg, a.account)
        got = SS.items(x, states=[a.state] if a.state else None, platforms=[a.platform] if a.platform else None)
        for it in got[:max(1, a.limit)]:
            who = (it.get("author") or {}).get("name") or "?"
            tags = ",".join(it.get("tags") or [])
            lines.append(f"{it.get('id')}  {it.get('state'):<8} {PLAT_WORDS.get(it.get('platform'), '')} {who}: "
                         f"{str(it.get('text') or '')[:90]}" + (f"  [{tags}]" if tags else ""))
        if not got:
            lines.append(f"{x.name}: nothing in the Inbox")
    elif a.cmd == "posts":
        x = _account(cfg, a.account)
        idx = build_posts_index(cfg, x, now)
        SS.set_posts_index(x, idx, now)
        lines.append(f"{x.name}: {len(idx)} posts in the index ({sum(1 for p in idx.values() if p['factory'])} from "
                     f"the factory)")
    elif a.cmd == "prune":
        accts = [_account(cfg, a.account)] if a.account else accounts(cfg)
        for x in accts:
            keep = int(SW.settings(x)["engage.keep_days"])  # type: ignore
            n = SS.prune(x, now, keep_days=keep) if SS.root(x).is_dir() else 0
            lines.append(f"{x.name}: {n} records past {keep} days removed")
    elif a.cmd == "forget":
        x = _account(cfg, a.account)
        n = SS.forget(x, a.person, a.by, cfg)
        lines.append(f"{x.name}: {a.person} forgotten ({n} records changed)")
    if not quiet:
        for ln in lines:
            print(ln)
    return None
