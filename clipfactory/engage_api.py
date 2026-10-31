"""The Inbox's calls to Meta and YouTube: the one file of the Inbox that reaches a platform, always through graph.py.

Only two callers ever get here: the social tick's poll (engage.tick, every 5 minutes, reads) and the queued sender
(outward, through engage.execute and engage.find: a reply, a private reply, a message, a hide, a YouTube moderation).
Never a page, a GET, a render or a test (graph.py goes through direct._http, so net.guard refuses an offline run, a
test and a copy of the state).

Reads (architecture section 4, maps/platform-api.md):
    Instagram  GET /{ig}/media?fields=id,comments_count,timestamp,permalink&since=<7 days>
               GET /{media}/comments?fields=id,text,timestamp,username,from{id,username},like_count,hidden,
                   replies{id,text,timestamp,username,from{id,username},like_count,hidden}
    Facebook   GET /{page}/video_reels?fields=id,created_time,comments.summary(true).limit(0)
               GET /{video}/comments?filter=stream&order=reverse_chronological&fields=id,from{id,name},message,
                   created_time,parent{id},is_hidden,can_reply_privately,like_count
               (on either, comments that come back without their words and names, as Instagram answers a token
               without instagram_manage_comments, are a missing permission: graph.Withheld, never blank comments)
    YouTube    commentThreads.list part=snippet,replies allThreadsRelatedToChannelId order=time (1 unit a page;
               needs youtube.force-ssl: a youtube.readonly token is refused, 403 insufficient scopes)
    Messages   GET /{page}/conversations?platform=instagram|messenger&fields=id,updated_time (2 calls a second at
               the most, engage.PACE_S; a thread whose is_owner is false is left alone)
               GET /{conversation}?fields=messages.limit(20){id,created_time,from,to,message}
Writes:
    reply      Instagram POST /{comment}/replies message; Facebook POST /{comment}/comments message;
               YouTube comments.insert (a click only)
    private    POST /{page}/messages recipient={"comment_id"} message={"text"}
    message    POST /{page}/messages recipient={"id"} messaging_type=RESPONSE message={"text"}
    hide       Instagram POST /{comment}?hide=true|false; Facebook POST /{comment}?is_hidden=true|false
    YouTube    comments.setModerationStatus heldForReview|published only (graph.yt_write refuses anything else)
There is no delete here, as in graph.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from . import config as C
from . import direct as D
from . import graph as G

IG_COMMENT_FIELDS = ("id,text,timestamp,username,from{id,username},like_count,hidden,"
                     "replies{id,text,timestamp,username,from{id,username},like_count,hidden}")
FB_COMMENT_FIELDS = "id,from{id,name},message,created_time,parent{id},is_hidden,can_reply_privately,like_count"
MESSAGE_FIELDS = "messages.limit(20){id,created_time,from,to,message}"
PAGE_SIZE = 50
MAX_PAGES = 4                       # 200 comments a post a read: far past what one of our posts gets in 10 minutes


def _pages(acct: C.Account, path: str, params: dict, max_pages: int = MAX_PAGES) -> Tuple[List[dict], bool]:
    """Every row of a Graph list over up to max_pages pages, and whether that was all of them (a post's comments are
    marked gone only after a complete read)."""
    q = dict(params)
    out: List[dict] = []
    for _ in range(max(1, int(max_pages))):
        got = G.gget(acct, path, q)
        out += [r for r in got.get("data") or [] if isinstance(r, dict)]
        paging = got.get("paging") or {}
        after = (paging.get("cursors") or {}).get("after")
        if not paging.get("next") or not after:
            return out, True
        q["after"] = after
    return out, False


def channel_id(acct: C.Account) -> str:
    """The YouTube channel the account's sign-in holds (saved at connect), '' when it was not saved."""
    try:
        return str(D._load(acct, "youtube_token", f"youtube {acct.slug} --client client.json").get("channel_id") or "")
    except Exception:  # noqa: BLE001  (not connected: the poll says so)
        return ""


# ------------------------------------------------------------------------------------------------------ reads
def ig_media(acct: C.Account, since: datetime) -> List[dict]:
    """The account's Instagram posts since then: [{"id", "count", "at", "url"}]."""
    k = G.meta_keys(acct)
    q = {"fields": "id,comments_count,timestamp,permalink", "since": int(since.timestamp()), "limit": PAGE_SIZE}
    rows, _ = _pages(acct, f"{k.ig_user_id}/media", q, 3)
    return [{"id": str(r.get("id") or ""), "count": int(r.get("comments_count") or 0), "at": r.get("timestamp"),
             "url": r.get("permalink") or ""} for r in rows if r.get("id")]


def fb_reels(acct: C.Account, oldest: datetime) -> List[dict]:
    """The Page's reels back to `oldest`: [{"id", "count", "at", "url"}]."""
    k = G.meta_keys(acct)
    q = {"fields": "id,created_time,comments.summary(true).limit(0)", "limit": 25}
    out: List[dict] = []
    for _ in range(2):
        got = G.gget(acct, f"{k.page_id}/video_reels", q)
        rows = [r for r in got.get("data") or [] if isinstance(r, dict) and r.get("id")]
        for r in rows:
            summ = ((r.get("comments") or {}).get("summary") or {}) if isinstance(r.get("comments"), dict) else {}
            out.append({"id": str(r["id"]), "count": int(summ.get("total_count") or 0), "at": r.get("created_time"),
                        "url": f"https://www.facebook.com/reel/{r['id']}/"})
        last = _when(rows[-1].get("created_time")) if rows else None
        after = ((got.get("paging") or {}).get("cursors") or {}).get("after")
        if not rows or last is None or last < oldest or not (got.get("paging") or {}).get("next") or not after:
            break
        q["after"] = after
    return [r for r in out if (_when(r["at"]) or oldest) >= oldest]


def ig_comments(acct: C.Account, media_id: str) -> Tuple[List[dict], bool]:
    """A post's comments, each with its replies (Instagram nests them), and whether the read was complete. Comments
    that came back without their words or names raise graph.Withheld (a missing permission, never blank comments)."""
    rows, complete = _pages(acct, f"{media_id}/comments", {"fields": IG_COMMENT_FIELDS, "limit": PAGE_SIZE})
    _words_or_raise("instagram", rows)
    return rows, complete


def fb_comments(acct: C.Account, video_id: str) -> Tuple[List[dict], bool]:
    """A reel's comments and replies (one stream, newest first), and whether the read was complete. As ig_comments,
    comments without their words or names raise graph.Withheld."""
    rows, complete = _pages(acct, f"{video_id}/comments", {"filter": "stream", "order": "reverse_chronological",
                                                           "fields": FB_COMMENT_FIELDS, "limit": PAGE_SIZE})
    _words_or_raise("facebook", rows)
    return rows, complete


def _words_or_raise(platform: str, rows: List[dict]) -> None:
    err = G.withheld(platform, rows)
    if err is not None:
        raise err


def yt_threads(cfg: C.Config, acct: C.Account, channel: str, seen_until: Optional[datetime],
               max_pages: int = 4) -> Tuple[List[dict], bool]:
    """The channel's comment threads, newest first, until a page reaches what was read before (1 unit a page), and
    whether that was every thread the channel has (no page left: a stored comment missing from it was deleted)."""
    q = {"part": "snippet,replies", "allThreadsRelatedToChannelId": channel, "order": "time", "maxResults": 50,
         "textFormat": "plainText"}
    out: List[dict] = []
    for _ in range(max(1, max_pages)):
        got = G.yt_get(acct, "commentThreads", q, cfg)
        rows = [r for r in got.get("items") or [] if isinstance(r, dict)]
        out += rows
        older = [r for r in rows if seen_until is not None and (_when(_top(r).get("publishedAt")) or seen_until)
                 <= seen_until]
        tok = got.get("nextPageToken")
        if not tok:
            return out, True
        if older:
            break
        q["pageToken"] = tok
    return out, False


def _top(thread: dict) -> dict:
    return (((thread.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {})


def conversations(acct: C.Account, platform: str) -> List[dict]:
    """The newest conversations of the Page's Instagram or Messenger inbox: [{"id", "updated_time"}]."""
    k = G.meta_keys(acct)
    got = G.gget(acct, f"{k.page_id}/conversations", {"platform": "instagram" if platform == "instagram"
                                                      else "messenger", "fields": "id,updated_time", "limit": 25})
    return [r for r in got.get("data") or [] if isinstance(r, dict) and r.get("id")]


def conversation_messages(acct: C.Account, conv_id: str) -> List[dict]:
    """A conversation's last 20 messages (the most the API gives details for)."""
    got = G.gget(acct, str(conv_id), {"fields": MESSAGE_FIELDS})
    return [m for m in ((got.get("messages") or {}).get("data") or []) if isinstance(m, dict)]


# ------------------------------------------------------------------------------------------------------ writes
def reply_public(cfg: C.Config, acct: C.Account, platform: str, comment_id: str, text: str) -> dict:
    """A public reply under a comment. {"id", "endpoint"}."""
    if platform == "instagram":
        got = G.gpost(acct, f"{comment_id}/replies", form={"message": text})
        return {"id": got.get("id"), "endpoint": G.endpoint("POST", f"{comment_id}/replies")}
    if platform == "facebook":
        got = G.gpost(acct, f"{comment_id}/comments", form={"message": text})
        return {"id": got.get("id"), "endpoint": G.endpoint("POST", f"{comment_id}/comments")}
    if platform == "youtube":
        got = G.yt_write(acct, "comments.insert", {"part": "snippet"},
                         {"snippet": {"parentId": comment_id, "textOriginal": text}}, cfg)
        return {"id": got.get("id"), "endpoint": "POST /youtube/v3/comments"}
    raise SystemExit(f"{platform}: the Inbox replies only on Instagram, Facebook and YouTube")


def reply_private(acct: C.Account, comment_id: str, text: str) -> dict:
    """The one private message Meta allows to the person behind a comment (within 7 days of it)."""
    k = G.meta_keys(acct)
    got = G.gpost(acct, f"{k.page_id}/messages", form={"recipient": json.dumps({"comment_id": comment_id}),
                                                       "message": json.dumps({"text": text})})
    return {"id": got.get("message_id") or got.get("id"), "endpoint": G.endpoint("POST", f"{k.page_id}/messages"),
            "to": got.get("recipient_id")}


def send_dm(acct: C.Account, to_id: str, text: str) -> dict:
    """An answer in a conversation (inside the 24 hours after their last message)."""
    k = G.meta_keys(acct)
    got = G.gpost(acct, f"{k.page_id}/messages", form={"recipient": json.dumps({"id": to_id}),
                                                       "messaging_type": "RESPONSE",
                                                       "message": json.dumps({"text": text})})
    return {"id": got.get("message_id") or got.get("id"), "endpoint": G.endpoint("POST", f"{k.page_id}/messages")}


def set_hidden(acct: C.Account, platform: str, comment_id: str, hidden: bool) -> dict:
    """Hide or show a comment on our own post (Instagram hide=, Facebook is_hidden=)."""
    field = {"instagram": "hide", "facebook": "is_hidden"}.get(platform)
    if field is None:
        raise SystemExit(f"{platform}: hiding is on Instagram and Facebook (YouTube holds a comment for review)")
    G.gpost(acct, str(comment_id), form={field: "true" if hidden else "false"})
    return {"id": str(comment_id), "endpoint": G.endpoint("POST", str(comment_id))}


def like_comment(acct: C.Account, platform: str, comment_id: str) -> dict:
    """Like a comment as the Page (Facebook only: the Instagram and YouTube APIs have no comment like)."""
    if platform != "facebook":
        raise SystemExit(f"{platform}: liking a comment is on Facebook only")
    G.gpost(acct, f"{comment_id}/likes", form={})
    return {"id": str(comment_id), "endpoint": G.endpoint("POST", f"{comment_id}/likes")}


def liked_now(acct: C.Account, platform: str, comment_id: str) -> bool:
    """Has the Page liked this comment (a lost answer is looked for here)."""
    if platform != "facebook":
        return False
    got = G.gget(acct, str(comment_id), {"fields": "user_likes"}) or {}
    return bool(got.get("user_likes"))


def yt_moderate(cfg: C.Config, acct: C.Account, comment_id: str, status: str) -> dict:
    """Hold a YouTube comment for review, or publish it again (graph.yt_write allows nothing stronger)."""
    G.yt_write(acct, "comments.setModerationStatus", {"id": comment_id, "moderationStatus": status}, None, cfg)
    return {"id": str(comment_id), "endpoint": "POST /youtube/v3/comments/setModerationStatus"}


# ------------------------------------------------------------------------------------------------------ look-ups
def replies_on(cfg: C.Config, acct: C.Account, platform: str, parent_id: str) -> List[dict]:
    """The replies under a comment, each {"id", "text", "from_id", "name"} (a lost answer is looked for here)."""
    if platform == "instagram":
        rows, _ = _pages(acct, f"{parent_id}/replies", {"fields": "id,text,timestamp,username,from{id,username}",
                                                        "limit": PAGE_SIZE}, 2)
        return [{"id": r.get("id"), "text": r.get("text") or "", "from_id": str((r.get("from") or {}).get("id") or ""),
                 "name": r.get("username") or ""} for r in rows]
    if platform == "facebook":
        rows, _ = _pages(acct, f"{parent_id}/comments", {"fields": "id,message,from{id,name},created_time",
                                                         "order": "reverse_chronological", "limit": PAGE_SIZE}, 2)
        return [{"id": r.get("id"), "text": r.get("message") or "", "from_id": str(_from(r).get("id") or ""),
                 "name": _from(r).get("name") or ""} for r in rows]
    if platform == "youtube":
        got = G.yt_get(acct, "comments", {"part": "snippet", "parentId": parent_id, "maxResults": 100,
                                          "textFormat": "plainText"}, cfg)
        out = []
        for r in got.get("items") or []:
            sn = r.get("snippet") or {}
            out.append({"id": r.get("id"), "text": sn.get("textOriginal") or sn.get("textDisplay") or "",
                        "from_id": str((sn.get("authorChannelId") or {}).get("value") or ""),
                        "name": sn.get("authorDisplayName") or ""})
        return out
    return []


def _from(r: dict) -> dict:
    f = r.get("from")
    return f if isinstance(f, dict) else {}


def messages_with(acct: C.Account, platform: str, person_id: str) -> List[dict]:
    """The last 20 messages of the conversation with this person ({"id", "text", "from_id"})."""
    k = G.meta_keys(acct)
    got = G.gget(acct, f"{k.page_id}/conversations", {
        "platform": "instagram" if platform == "instagram" else "messenger", "user_id": person_id,
        "fields": MESSAGE_FIELDS})
    out: List[dict] = []
    for conv in got.get("data") or []:
        for m in ((conv.get("messages") or {}).get("data") or []):
            out.append({"id": m.get("id"), "text": m.get("message") or "",
                        "from_id": str((m.get("from") or {}).get("id") or "")})
    return out


def hidden_now(acct: C.Account, platform: str, comment_id: str) -> Optional[bool]:
    """Is the comment hidden on the platform now (None when it does not say)?"""
    field = {"instagram": "hidden", "facebook": "is_hidden"}.get(platform)
    if field is None:
        return None
    got = G.gget(acct, str(comment_id), {"fields": f"id,{field}"})
    v = got.get(field)
    return bool(v) if isinstance(v, bool) else None


def yt_status(cfg: C.Config, acct: C.Account, comment_id: str) -> Optional[str]:
    """A YouTube comment's moderation status as its owner sees it (None when not given)."""
    got = G.yt_get(acct, "comments", {"part": "snippet", "id": comment_id, "textFormat": "plainText"}, cfg)
    items = got.get("items") or []
    return str(((items[0] if items else {}).get("snippet") or {}).get("moderationStatus") or "") or None


def _when(v) -> Optional[datetime]:
    if not v:
        return None
    s = str(v).replace("Z", "+00:00")
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":          # Graph's +0000
        s = s[:-2] + ":" + s[-2:]
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def keys(acct: C.Account) -> Dict[str, str]:
    """{"page_id", "ig_user_id", "ig_username"}: who "us" is on Meta (no token in it)."""
    k = G.meta_keys(acct)
    return {"page_id": k.page_id, "ig_user_id": k.ig_user_id, "ig_username": k.ig_username}
