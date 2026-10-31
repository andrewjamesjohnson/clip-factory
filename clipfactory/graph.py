"""The social layer's one way to Meta's Graph API and to YouTube: every call a social part makes goes through here.

Each call goes through direct._http, so net.guard refuses it in a test, an offline run or a copy of the state, a key in
an error message is hidden, and the answer's rate-limit headers are kept (direct.last_usage). Nothing here runs in a
GET, a page render or a test: only the queued sender (outward), the timers' reads and a probe typed by hand.

    meta_keys(acct)                        the Page, its token and its Instagram (NotConnected when not signed in)
    gget(acct, "17890/comments", {...})    a Graph read with the Page token
    gpost(acct, "17890/comments", form={"message": "..."})
    gpages(acct, path, params)             every row over Graph's pages (up to max_pages)
    multipart(fields, "source", path, "image/jpeg", "1.jpg")   a body for a photo upload
    yt_get(acct, "commentThreads", {...})  a YouTube read (1 unit), counted
    yt_write(acct, method, params, body)   only a comment, a reply, or holding or publishing a comment (50 units)
    yta_get(acct, {...})                   a YouTube Analytics report
    classify(err)                          ErrorInfo: what kind of no it was (block, buc, rate, perm ...)
    withheld("instagram", rows)            a Withheld (a perm no) when the comments came back without words or names
    endpoint("POST", url)                  "POST /{id}/comments": the call's shape, for the log

There is no delete here, on purpose: nothing in the social layer removes a post, a video or a comment (a grep test
holds that). Graph's "method" field (a verb in disguise) is refused on every call, a POST reaches only an allowed edge
(POST_EDGES) or hides a comment, and the Page token goes to no host but Meta's Graph and upload hosts. YouTube writes
are an allowlist of three methods; a comment can be held for review or published again, never anything stronger, and
its author is never touched.
"""
from __future__ import annotations

import re
import secrets
import socket
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from . import config as C
from . import direct as D
from .stats import NotConnected

YT = "https://www.googleapis.com/youtube/v3"
YTA = "https://youtubeanalytics.googleapis.com/v2/reports"

# YouTube Data API units per call (developers.google.com/youtube/v3/determine_quota_cost): a list is 1, a write 50.
# A read of a resource not named here is refused until its cost is written down (search.list is 100).
YT_COST: Dict[str, int] = {"commentThreads.list": 1, "comments.list": 1, "videos.list": 1, "channels.list": 1,
                           "playlistItems.list": 1, "commentThreads.insert": 50, "comments.insert": 50,
                           "comments.setModerationStatus": 50}
YT_WRITES = ("commentThreads.insert", "comments.insert", "comments.setModerationStatus")
YT_MODERATION = ("heldForReview", "published")          # the only statuses a comment is ever set to
_MODERATION_PARAMS = ("id", "moderationStatus")         # nothing else is ever sent with it


# ------------------------------------------------------------------------------------------------------ Meta
class MetaKeys(NamedTuple):
    page_id: str
    page_token: str
    ig_user_id: str
    ig_username: str
    app_id: str


def meta_keys(acct: C.Account) -> MetaKeys:
    """The account's Meta sign-in (direct._load): NotConnected with the connect command when there is none."""
    m = D._load(acct, "meta", "meta")
    if not m.get("page_token"):
        raise NotConnected(f"run: factory.py connect meta {acct.slug}")
    return MetaKeys(str(m.get("page_id") or ""), str(m["page_token"]), str(m.get("ig_user_id") or ""),
                    str(m.get("ig_username") or ""), str(m.get("app_id") or ""))


# Where the Page token may go: Graph itself, and Meta's upload hosts for a video's bytes. Any other host (a paging or
# permalink URL out of an answer, a typo) is refused before the token is added.
GRAPH_HOSTS = ("graph.facebook.com", "graph-video.facebook.com", "rupload.facebook.com")
UPLOAD_HOSTS = ("graph-video.facebook.com", "rupload.facebook.com")
# What a POST may reach on Graph: these edges of an object, or an object itself only to hide or show a comment
# (Facebook's is_hidden, Instagram's hide). Anything else (a Reel's description, a page's settings) is an edit the
# social layer never makes.
POST_EDGES = ("comments", "replies", "private_replies", "messages", "photos", "photo_stories", "video_stories",
              "feed", "media", "media_publish")
OBJECT_FORM = ("is_hidden", "hide")


def _graph_url(path: str) -> str:
    """The full URL for a Graph path; an absolute URL only on Meta's own Graph and upload hosts."""
    p = str(path or "")
    if "://" not in p:
        return f"{D.GRAPH}/{p.lstrip('/')}"
    u = urllib.parse.urlsplit(p)
    if u.scheme != "https" or (u.hostname or "").lower() not in GRAPH_HOSTS:
        raise SystemExit(f"{u.hostname or p[:60]}: the Page token goes only to Meta's Graph API")
    return p


def _no_method(path: str, *fields: Optional[dict]) -> None:
    """Graph takes a "method" field to turn a GET or POST into another verb (removing a thing among them): never
    sent."""
    keys = [str(k) for f in fields for k in (f or {})]
    keys += [k for k, _ in urllib.parse.parse_qsl(urllib.parse.urlsplit(str(path or "")).query,
                                                  keep_blank_values=True)]
    if any(k.strip().lower() == "method" for k in keys):
        raise SystemExit("a Graph call never carries a method field: the social layer only reads, comments, "
                         "replies, messages, posts and hides")


def _post_shape(url: str, form: Optional[dict], data: Optional[bytes]) -> None:
    """A POST to Graph reaches one of POST_EDGES, or an object only to hide or show a comment; an upload host takes
    the bytes of a video it was handed."""
    u = urllib.parse.urlsplit(url)
    if (u.hostname or "").lower() in UPLOAD_HOSTS:
        return
    parts = [x for x in u.path.split("/") if x]
    if parts and re.fullmatch(r"v\d+(\.\d+)?", parts[0]):
        parts = parts[1:]
    if len(parts) >= 2 and parts[-1] in POST_EDGES:
        return
    if len(parts) == 1 and data is None and form and set(form) <= set(OBJECT_FORM):
        return
    raise SystemExit(f"POST /{'/'.join(parts)[:80]}: not something the social layer posts to (only "
                     f"{', '.join(POST_EDGES)}, or hiding a comment)")


def gget(acct: C.Account, path: str, params: Optional[dict] = None, timeout: int = 60) -> dict:
    """GET graph.facebook.com/<version>/<path> with the Page token."""
    _no_method(path, params)
    url = _graph_url(path)
    q = dict(params or {})
    q["access_token"] = meta_keys(acct).page_token
    return D._http("GET", url, params=q, timeout=timeout)


def gpost(acct: C.Account, path: str, form: Optional[dict] = None, data: Optional[bytes] = None,
          headers: Optional[dict] = None, timeout: int = 120) -> dict:
    """POST to <path> with the Page token: a form, or a body of bytes (a multipart upload: the token then goes in the
    query, as the body is the file). Only to an allowed edge (POST_EDGES) or to hide a comment; never a method field."""
    _no_method(path, form)
    url = _graph_url(path)
    _post_shape(url, form, data)
    tok = meta_keys(acct).page_token
    if data is not None:
        return D._http("POST", url, params={"access_token": tok}, data=data, headers=headers, timeout=timeout)
    f = dict(form or {})
    f["access_token"] = tok
    return D._http("POST", url, form=f, headers=headers, timeout=timeout)


def rupload(acct: C.Account, url: str, data: bytes, timeout: int = 1800) -> dict:
    """A video's bytes to Meta's upload host, exactly as direct._upload_bytes sends a Reel: the Page token only in
    the Authorization header (never in the address), offset 0 and the file's size. Only to an upload host."""
    u = urllib.parse.urlsplit(str(url or ""))
    if u.scheme != "https" or (u.hostname or "").lower() not in UPLOAD_HOSTS:
        raise SystemExit(f"{u.hostname or str(url)[:60]}: bytes go only to Meta's upload hosts")
    _no_method(url)
    tok = meta_keys(acct).page_token
    return D._http("POST", url, data=data, timeout=timeout,
                   headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(len(data))})


def gpages(acct: C.Account, path: str, params: Optional[dict] = None, stop: Optional[Callable[[dict], bool]] = None,
           max_pages: int = 10) -> List[dict]:
    """Every row of a Graph list, newest page first, over up to max_pages pages (Graph's `after` cursor). With
    `stop`, the reading ends at the first row it says yes to (that row and the rest are left out): "older than the
    cursor" stops a comment poll without paging back through a post's whole history."""
    q = dict(params or {})
    out: List[dict] = []
    for _ in range(max(1, int(max_pages))):
        got = gget(acct, path, q)
        for row in got.get("data") or []:
            if stop is not None and stop(row):
                return out
            out.append(row)
        paging = got.get("paging") or {}
        after = (paging.get("cursors") or {}).get("after")
        if not paging.get("next") or not after:
            break
        q["after"] = after
    return out


def multipart(fields: Dict[str, str], file_field: str, path: Path, content_type: str,
              filename: str) -> Tuple[bytes, str]:
    """A multipart/form-data body (the plain fields, then the file) and its Content-Type header."""
    boundary = "----clipfactory" + secrets.token_hex(12)
    parts: List[bytes] = []
    for k, v in (fields or {}).items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    safe = re.sub(r'["\r\n]', "_", filename)
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{safe}"\r\n'
                 f"Content-Type: {content_type}\r\n\r\n".encode())
    parts.append(Path(path).read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# ------------------------------------------------------------------------------------------------------ YouTube
def _yt_bearer(acct: C.Account) -> dict:
    from . import outlets as O
    p = D._tok_path(acct, "youtube_token")
    if not p.exists():
        raise NotConnected(f"run: factory.py connect youtube {acct.slug} --client client.json")
    return {"Authorization": f"Bearer {O._yt_access_token(p)}"}


def _spend(cfg: Optional[C.Config], units: int, method: str, write: bool) -> None:
    """Count YouTube units against this machine's day (outward.yt_spend, once it is built). Never stops a call."""
    from . import registry as REG
    f = REG.fn("outward:yt_spend")
    if f is None:
        return
    try:
        f(cfg, units, method, write)
    except Exception as e:  # noqa: BLE001
        REG._log_error("outward:yt_spend", e)


def _yt_call(cfg, method: str, units: int, write: bool, call: Callable[[], dict]) -> dict:
    """Run one YouTube call and count its units when YouTube answered (an error costs units too; a call the guard
    refused or that never reached Google costs none)."""
    try:
        got = call()
    except D.PlatformError as e:
        if getattr(e, "status", None):
            _spend(cfg, units, method, write)
        raise
    _spend(cfg, units, method, write)
    return got


def yt_get(acct: C.Account, resource: str, params: Optional[dict] = None, cfg: Optional[C.Config] = None) -> dict:
    """GET youtube/v3/<resource> (a list: commentThreads, comments, videos, channels, playlistItems), counted."""
    method = f"{resource}.list"
    if method not in YT_COST:
        raise SystemExit(f"{method}: its unit cost is not in graph.YT_COST; add it there first")
    headers = _yt_bearer(acct)
    return _yt_call(cfg, method, YT_COST[method], False,
                    lambda: D._http("GET", f"{YT}/{resource}", params=dict(params or {}), headers=headers, timeout=60))


def _refuse_write(method: str, params: dict, body) -> str:
    """'' when the factory may make this YouTube write; else why not."""
    if method not in YT_WRITES:
        return (f"{method}: the factory never makes that YouTube call (only a comment, a reply, or holding or "
                f"publishing a comment)")
    if method == "comments.setModerationStatus":
        extra = sorted(k for k in params if k not in _MODERATION_PARAMS)
        if extra:
            return f"{method}: never with {', '.join(extra)}"
        if params.get("moderationStatus") not in YT_MODERATION:
            return f"{method}: a comment is only held for review or published, never set to anything else"
        if not params.get("id"):
            return f"{method}: which comment (id) is missing"
        if body:
            return f"{method}: takes no body"
    elif not isinstance(body, dict) or not body.get("snippet"):
        return f"{method}: needs a body with its snippet"
    return ""


def yt_write(acct: C.Account, method: str, params: Optional[dict] = None, body: Optional[dict] = None,
             cfg: Optional[C.Config] = None) -> dict:
    """One of three YouTube writes (YT_WRITES), 50 units each: commentThreads.insert (a top-level comment: the signed
    first comment), comments.insert (a reply) and comments.setModerationStatus (hold for review, or publish again).
    Any other method, or moderation with anything stronger, is refused (SystemExit) before any network."""
    params = dict(params or {})
    why = _refuse_write(method, params, body)
    if why:
        raise SystemExit(f"refused: {why}")
    headers = _yt_bearer(acct)
    resource, _, verb = method.partition(".")
    if verb == "insert":
        params.setdefault("part", "snippet")
        url = f"{YT}/{resource}"
        call = lambda: D._http("POST", url, params=params, body=body, headers=headers, timeout=60)  # noqa: E731
    else:
        url = f"{YT}/comments/setModerationStatus"
        call = lambda: D._http("POST", url, params=params, headers=headers, timeout=60)  # noqa: E731
    return _yt_call(cfg, method, YT_COST[method], True, call)


def yta_get(acct: C.Account, params: dict, cfg: Optional[C.Config] = None) -> dict:
    """A YouTube Analytics report (youtubeanalytics.googleapis.com/v2/reports; needs yt-analytics.readonly). It has
    its own quota, so it is not counted against the Data API's units."""
    headers = _yt_bearer(acct)
    return D._http("GET", YTA, params=dict(params or {}), headers=headers, timeout=60)


# ------------------------------------------------------------------------------------------------------ errors
class ErrorInfo(NamedTuple):
    kind: str                       # block buc rate publish_limit perm auth transient quota policy gone timeout other
    code: Optional[int]
    subcode: Optional[int]
    reason: Optional[str]
    http: Optional[int]
    msg: str
    trace: Optional[str]


BLOCK_WORDS = ("temporarily blocked", "action is blocked", "action was blocked", "we restrict certain activity")
QUOTA_REASONS = ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded")
POLICY_REASONS = ("commentsDisabled", "forbidden", "ineligibleAccount", "operationNotSupported")
# Google's no for a sign-in without the scope (HTTP 403 "Request had insufficient authentication scopes": a
# youtube.readonly token reading a channel's comment threads, 2026-10-20), by reason, then by its words
SCOPE_REASONS = ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT")
SCOPE_WORDS = ("insufficient authentication scopes",)
_CODE_IN_TEXT = re.compile(r"\(#(\d+)\)")

# What a comments read carries only with the comments permission. Instagram answers a token without
# instagram_manage_comments with each comment's id, timestamp, hidden and like_count, and leaves out the words and who
# wrote them (2026-10-20); Facebook's would be the message and from.
COMMENT_WORDS = {"instagram": ("text", "username", "from"), "facebook": ("message", "from")}
COMMENT_PERM = {"instagram": "instagram_manage_comments", "facebook": "pages_read_user_content"}


class Withheld(D.PlatformError):
    """The platform answered a comments read but left the words and the names out of every comment: a missing
    permission said as a quiet answer. classify gives "perm", so it is never stored as blank comments."""


def withheld(platform: str, rows) -> Optional[Withheld]:
    """A Withheld when every comment the platform gave lacks its words and its author; None when one carries them, or
    when there was none (no comment proves nothing either way)."""
    fields = COMMENT_WORDS.get(platform)
    rows = [r for r in rows or [] if isinstance(r, dict)]
    if not fields or not rows or any(f in r for r in rows for f in fields):
        return None
    words = {"instagram": "Instagram", "facebook": "Facebook"}[platform]
    return Withheld(f"{words} gave {len(rows)} comment{'s' if len(rows) != 1 else ''} without the words or who wrote "
                    f"{'them' if len(rows) != 1 else 'it'}: the token lacks {COMMENT_PERM[platform]}")


def _attr(err: BaseException, name: str):
    """An attribute of an error, None when it has none (an HTTPError with no body raises on any unknown name)."""
    try:
        return getattr(err, name, None)
    except Exception:  # noqa: BLE001
        return None


def classify(err: BaseException) -> ErrorInfo:
    """What kind of no an error was, numbers first (PlatformError's attributes), then Meta's own phrases:
    block (368, or "action is blocked" ...), buc (80001-80006), rate (4, 17, 32, 613), publish_limit (9, 2207042),
    perm (10, 200-299, Google's insufficient scopes, a Withheld answer), auth (190, 102), transient (is_transient, 1,
    2), quota and policy (Google's reason), gone (404, or "does not exist" without "missing permission"), timeout (no
    answer at all), other."""
    msg = str(err)
    http = _attr(err, "status")
    if http is None and isinstance(err, urllib.error.HTTPError):
        http = err.code
    code = _attr(err, "meta_code")
    if code is None:                    # an error kept as words only: Meta writes its code as "(#368)"
        m = _CODE_IN_TEXT.search(msg)
        code = int(m.group(1)) if m else None
    sub = _attr(err, "meta_subcode")
    reason = _attr(err, "reason") if isinstance(err, D.PlatformError) else None     # urllib's .reason is words
    reason = reason if isinstance(reason, str) else None
    trace = _attr(err, "trace")

    def out(kind: str) -> ErrorInfo:
        return ErrorInfo(kind, code, sub, reason, http, msg[:400], trace)
    low = msg.lower()
    if code == 368 or any(w in low for w in BLOCK_WORDS):
        return out("block")
    if code is not None and 80001 <= code <= 80006:
        return out("buc")
    if code in (4, 17, 32, 613):
        return out("rate")
    if code == 9 or sub == 2207042:
        return out("publish_limit")
    if code is not None and (code == 10 or 200 <= code <= 299):
        return out("perm")
    if isinstance(err, Withheld) or reason in SCOPE_REASONS or any(w in low for w in SCOPE_WORDS):
        return out("perm")
    if code in (190, 102):
        return out("auth")
    if _attr(err, "transient") is True or code in (1, 2):
        return out("transient")
    if reason in QUOTA_REASONS:
        return out("quota")
    if reason in POLICY_REASONS:
        return out("policy")
    if (http == 404 or "does not exist" in msg) and "missing permission" not in msg:
        return out("gone")
    if not isinstance(err, D.PlatformError) and http is None and isinstance(
            err, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError)):
        return out("timeout")
    return out("other")


_ID = re.compile(r"^(\d+(_\d+)?|[0-9a-f]{24,})$")
_VERSION = re.compile(r"^v\d+\.\d+$")            # Graph's v25.0 (YouTube's v3 and Analytics' v2 stay)


def endpoint(method: str, path: str) -> str:
    """The shape of a call for the log, with no ids, host, version or query: ("POST", ".../v25.0/1789_22/comments?x")
    gives "POST /{id}/comments"; a YouTube call keeps its path ("GET /youtube/v3/commentThreads")."""
    p = urllib.parse.urlparse(path).path if "://" in path else path.split("?", 1)[0]
    segs = [s for s in p.split("/") if s]
    if segs and _VERSION.match(segs[0]):
        segs = segs[1:]
    return f"{method.upper()} /" + "/".join("{id}" if _ID.match(s) else s for s in segs)
