"""Follower and channel counts for each connected account, read-only, for the Stats tab's paid-program bars.

    stats.update (03:30 and Update now) calls read_all after the video numbers; the counts go into
    state/stats/followers.json (the last good number of each, with when it was read) and the history snapshot.

What is read, with the sign-ins the factory already has (nothing here posts or changes anything):
  YouTube    channels.list part=statistics mine=true: subscriberCount (unless hidden), viewCount, videoCount.
             Also yt_status: videos.list part=status, so a scheduled upload counts from its publish time and a
             private, unlisted or rejected video is not counted as a post.
  Facebook   GET /{page-id}?fields=followers_count,fan_count with the Page token.
  Instagram  GET /{ig-user-id}?fields=followers_count,follows_count,media_count with the Page token.
  TikTok     GET /v2/user/info/ fields follower_count, likes_count, video_count, only when the sign-in has the
             user.info.stats scope. Today's sign-ins have user.info.basic, video.list and video.publish, so TikTok
             says "not readable: needs user.info.stats" until the app has that scope and the account connects again.

One platform that fails never stops the others. No token is ever printed: errors pass through direct._redact.
"""
from __future__ import annotations

import json
import os
from typing import Callable, Dict, List, Optional, Tuple

from . import board as B
from . import config as C
from . import direct as D
from . import stats as ST
from .stats import NotConnected

YT_API = "https://www.googleapis.com/youtube/v3"
TT_STATS = "user.info.stats"
NEEDS_TT_STATS = "not readable: needs user.info.stats"


def _file():
    return ST.OUT / "followers.json"           # looked up each time: tests point stats.OUT at a throwaway


# ------------------------------------------------------------------------------------------------ one platform each
def _yt_auth(acct: C.Account) -> dict:
    from . import outlets as O
    tf = D._tok_path(acct, "youtube_token")
    if not tf.exists():
        raise NotConnected(f"run: factory.py connect youtube {acct.slug} --client <client.json>")
    tok = json.loads(tf.read_text())
    if "youtube.readonly" not in tok.get("scope", "youtube.readonly"):
        raise NotConnected("the YouTube sign-in has no read permission; run connect youtube again")
    return {"Authorization": f"Bearer {O._yt_access_token(tf)}"}


def youtube(acct: C.Account) -> dict:
    r = D._http("GET", f"{YT_API}/channels", params={"part": "statistics", "mine": "true"}, headers=_yt_auth(acct),
                timeout=30)
    items = r.get("items") or []
    if not items:
        raise NotConnected("that Google sign-in has no YouTube channel")
    st = items[0].get("statistics") or {}
    hidden = bool(st.get("hiddenSubscriberCount"))
    return {"followers": None if hidden else int(st.get("subscriberCount") or 0), "hidden": hidden,
            "views": int(st.get("viewCount") or 0), "videos": int(st.get("videoCount") or 0),
            "field": "YouTube channels.statistics.subscriberCount"}


def yt_status(acct: C.Account, ids: List[str]) -> Dict[str, dict]:
    """video id -> {privacy, publish_at, upload, rejection}: 1 quota unit for each 50 videos."""
    auth = _yt_auth(acct)
    out: Dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        r = D._http("GET", f"{YT_API}/videos", headers=auth, timeout=60, params={
            "part": "status", "id": ",".join(ids[i:i + 50]), "maxResults": 50})
        for v in r.get("items") or []:
            st = v.get("status") or {}
            out[v["id"]] = {"privacy": st.get("privacyStatus") or "", "publish_at": st.get("publishAt") or "",
                            "upload": st.get("uploadStatus") or "", "rejection": st.get("rejectionReason") or ""}
    return out


def facebook(acct: C.Account) -> dict:
    m = D._load(acct, "meta", "meta")
    r = D._http("GET", f"{D.GRAPH}/{m['page_id']}", params={"fields": "followers_count,fan_count",
                                                            "access_token": m["page_token"]}, timeout=30)
    return {"followers": int(r.get("followers_count") or 0), "fans": int(r.get("fan_count") or 0),
            "field": "Facebook Page followers_count"}


def instagram(acct: C.Account) -> dict:
    m = D._load(acct, "meta", "meta")
    if not m.get("ig_user_id"):
        raise NotConnected("no Instagram account linked to the Page")
    r = D._http("GET", f"{D.GRAPH}/{m['ig_user_id']}", params={"fields": "followers_count,follows_count,media_count",
                                                               "access_token": m["page_token"]}, timeout=30)
    return {"followers": int(r.get("followers_count") or 0), "media": int(r.get("media_count") or 0),
            "field": "Instagram followers_count"}


def tiktok(acct: C.Account) -> dict:
    t = D._load(acct, "tiktok", "tiktok --client-key KEY")
    scopes = {x.strip() for x in str(t.get("scope") or "").replace(" ", ",").split(",") if x.strip()}
    if TT_STATS not in scopes:
        return {"error": NEEDS_TT_STATS}
    r = D._http("GET", f"{D.TT}/user/info/", params={"fields": "follower_count,following_count,likes_count,video_count"},
                headers={"Authorization": f"Bearer {D._tt_token(acct)}"}, timeout=30)
    u = (r.get("data") or {}).get("user") or {}
    return {"followers": int(u.get("follower_count") or 0), "likes": int(u.get("likes_count") or 0),
            "videos": int(u.get("video_count") or 0), "field": "TikTok user.info follower_count"}


READERS: Dict[str, Callable[[C.Account], dict]] = {
    "youtube_shorts": youtube, "facebook_reels": facebook, "instagram_reels": instagram, "tiktok": tiktok}


# ------------------------------------------------------------------------------------------------ all of them
def load() -> dict:
    """{account: {platform: {followers, ..., at, field} or {error, at, ...the last good numbers}}}"""
    try:
        d = json.loads(_file().read_text())
    except (OSError, ValueError):
        return {}
    return d.get("accounts") or {} if isinstance(d, dict) else {}


def read_all(cfg: C.Config, readers: Optional[Dict[str, Callable[[C.Account], dict]]] = None) -> Tuple[dict, List[str]]:
    """Read every account's counts. A platform that fails keeps its last good numbers, with the reason beside them.
    Returns (the counts, one line per platform that could not be read)."""
    readers = readers if readers is not None else READERS
    stamp = B.utcnow()
    old = load()
    out: dict = {}
    lines: List[str] = []
    for acct in cfg.accounts.values():
        by: dict = {}
        for plat, read in readers.items():
            if plat not in acct.platforms:
                continue
            prev = (old.get(acct.slug) or {}).get(plat) or {}
            paused = ST.read_paused(cfg, acct, plat)
            if paused:                  # the social guard (switch on) holds its reads: the last numbers stay
                by[plat] = dict(prev) if prev else {"error": f"not read now, {paused}"[:200], "tried_at": stamp}
                lines.append(f"  {acct.name} {plat} followers: not read now, {paused}")
                continue
            try:
                got = read(acct)
            except NotConnected as e:
                got = {"error": f"not connected ({e})"}
            except Exception as e:  # noqa: BLE001  (one platform never stops the others)
                got = {"error": D._redact(f"{type(e).__name__}: {e}")[:200]}
            if got.get("error"):
                keep = {k: v for k, v in prev.items() if k not in ("error", "tried_at")}
                by[plat] = {**keep, "error": got["error"], "tried_at": stamp}
                lines.append(f"  {acct.name} {plat} followers: {got['error']}")
            else:
                by[plat] = {**got, "at": stamp}
        out[acct.slug] = by
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"at": stamp, "accounts": out}, indent=1))
    os.replace(tmp, p)
    return out, lines
