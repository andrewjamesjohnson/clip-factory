"""Cards and stories to Facebook and Instagram: the Graph API calls, every one through graph.py.

Only the queued sender (outward.send, from the studio timer) calls these, never a page or a test without fakes. Each
one that publishes calls `mark(fields)` right before the step that cannot be undone, so the card board holds what
was made (the photo, the video, the container) before Meta makes it public: a lost answer is looked up (find_*), a
card is never posted twice.

    Facebook story (a card):  POST /{page}/photos published=false (the JPEG as multipart "source")
                              POST /{page}/photo_stories photo_id
    Facebook post (carousel): POST /{page}/photos published=false, one per slide
                              POST /{page}/feed attached_media[n]={"media_fbid": id}
    Facebook story (a clip):  POST /{page}/video_stories upload_phase=start, the bytes to rupload, then finish
    Instagram story:          POST /{ig}/media media_type=STORIES upload_type=resumable, the bytes to rupload, its
                              status once a minute (5 at most), POST /{ig}/media_publish creation_id
    Instagram carousel:       each slide's MP4 as media_type=VIDEO is_carousel_item=true (resumable), then the
                              CAROUSEL parent with its children, then media_publish

Instagram takes an image only from a public URL and the factory has none, so every Instagram card goes as its 6
second MP4 (cards.py makes it beside the JPEG). Before an Instagram publish the account's content_publishing_limit is
read (kept 30 minutes in state/<slug>/cards/limit.json): a card waits while the publishes used plus the Reels still
due today reach min(25, quota_total / 2), so the Reels always keep their room.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import config as C
from . import direct as D
from . import graph as G
from . import store

STATUS_TRIES = 5                  # Instagram's status, read once a minute, at most this many times
STATUS_WAIT_S = 60
LIMIT_KEEP_S = 1800               # content_publishing_limit is read again after 30 minutes
IG_ROOM = 25                      # every Instagram publish in 24 h: half of Meta's 50 (switches.IG_PUBLISH_DAY)
FIND_SLACK = timedelta(minutes=3)

_sleep = time.sleep               # tests replace it


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _when(v) -> Optional[datetime]:
    return store._when(v) if v not in (None, "") else None


def _ver() -> str:
    return D.GRAPH.rsplit("/", 1)[-1]


def signed_bytes(path: Path, sha: Optional[str]) -> bytes:
    """The file's bytes, read once, and only when they are the bytes Andrew signed (sha = their SHA-256 on the card or
    the story's queued action). The upload sends these very bytes: a file drawn over after the check never goes."""
    data = Path(path).read_bytes()
    if sha is not None and hashlib.sha256(data).hexdigest() != str(sha):
        raise SystemExit(f"{Path(path).name} changed after it was signed: it is not sent")
    return data


def _upload(acct: C.Account, url: str, path: Path, sha: Optional[str] = None) -> dict:
    """The file's bytes to Meta's rupload host (the page token as OAuth, as direct._upload_bytes sends a Reel), checked
    against the signed SHA-256 when one is given."""
    return G.rupload(acct, url, signed_bytes(path, sha))


# ------------------------------------------------------------------------------------------------------ Facebook
def _fb_photo(acct: C.Account, page: str, image: Path, sha: Optional[str] = None) -> str:
    body, ctype = G.multipart({"published": "false"}, "source", Path(image), "image/jpeg", Path(image).name)
    if sha is not None:                          # the bytes in the body are the signed ones, checked in the body itself
        head = body.index(b"\r\n\r\n", body.index(b'name="source"')) + 4
        tail = len(body) - len(f"\r\n--{ctype.split('boundary=', 1)[1]}--\r\n".encode())
        if hashlib.sha256(body[head:tail]).hexdigest() != str(sha):
            raise SystemExit(f"{Path(image).name} changed after it was signed: it is not sent")
    got = G.gpost(acct, f"{page}/photos", data=body, headers={"Content-Type": ctype}, timeout=300)
    pid = str(got.get("id") or "")
    if not pid:
        raise D.PlatformError("Facebook took the photo but gave no id back")
    return pid


def fb_photo_story(acct: C.Account, image: Path, mark: Callable[[dict], None], sha: Optional[str] = None) -> dict:
    """A card as a Facebook Page photo story: the photo unpublished, then the story."""
    k = G.meta_keys(acct)
    pid = _fb_photo(acct, k.page_id, image, sha)
    mark({"photo_id": pid})
    got = G.gpost(acct, f"{k.page_id}/photo_stories", form={"photo_id": pid})
    post = str(got.get("post_id") or got.get("id") or pid)
    return {"id": post, "url": f"https://www.facebook.com/{post}", "endpoint": "POST /{id}/photo_stories"}


def fb_feed(acct: C.Account, images: List[Path], mark: Callable[[dict], None],
            shas: Optional[List[str]] = None, message: Optional[str] = None) -> dict:
    """A carousel as one Facebook Page post with every slide: each photo unpublished, then the post (with the post's
    own words as its message, for a news post)."""
    k = G.meta_keys(acct)
    sh = list(shas) if shas is not None else [None] * len(images)
    ids = [_fb_photo(acct, k.page_id, p, x) for p, x in zip(images, sh)]
    mark({"photo_ids": ids})
    form = {f"attached_media[{i}]": json.dumps({"media_fbid": pid}) for i, pid in enumerate(ids)}
    if message:
        form["message"] = message
    got = G.gpost(acct, f"{k.page_id}/feed", form=form)
    post = str(got.get("id") or "")
    if not post:
        raise D.PlatformError("Facebook took the post but gave no id back")
    return {"id": post, "url": f"https://www.facebook.com/{post}", "endpoint": "POST /{id}/feed"}


def fb_video_story(acct: C.Account, video: Path, mark: Callable[[dict], None], sha: Optional[str] = None) -> dict:
    """A clip (60 s at the most) as a Facebook Page video story: start, the bytes, finish."""
    k = G.meta_keys(acct)
    st = G.gpost(acct, f"{k.page_id}/video_stories", form={"upload_phase": "start"})
    vid = str(st.get("video_id") or "")
    if not vid:
        raise D.PlatformError("Facebook did not start the video story (no video_id)")
    _upload(acct, st.get("upload_url") or f"https://rupload.facebook.com/video-upload/{_ver()}/{vid}", video, sha)
    mark({"video_id": vid})
    got = G.gpost(acct, f"{k.page_id}/video_stories", form={"upload_phase": "finish", "video_id": vid})
    post = str(got.get("post_id") or got.get("id") or vid)
    return {"id": post, "url": f"https://www.facebook.com/{post}", "endpoint": "POST /{id}/video_stories"}


# ------------------------------------------------------------------------------------------------------ Instagram
def _ig(acct: C.Account) -> str:
    """The linked Instagram account's id. None linked is NotConnected (an Exception the sender and the studio loop
    handle), never a SystemExit that would end the whole run."""
    from .stats import NotConnected
    k = G.meta_keys(acct)
    if not k.ig_user_id:
        raise NotConnected("no Instagram account is linked to the Page (factory.py connect meta)")
    return k.ig_user_id


def _wait_ready(acct: C.Account, cid: str) -> None:
    """Instagram's container status once a minute, at most five times: FINISHED goes on; ERROR or EXPIRED, or still
    working after five, is a failed try (outward tries again later with a new container; this one expires)."""
    for i in range(STATUS_TRIES):
        st = G.gget(acct, cid, {"fields": "status_code,status"})
        code = str(st.get("status_code") or "")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise D.PlatformError(f"Instagram could not take it: {st.get('status') or code}")
        if i + 1 < STATUS_TRIES:
            _sleep(STATUS_WAIT_S)
    raise D.PlatformError(f"Instagram was still working on it after {STATUS_TRIES} minutes")


def _ig_video(acct: C.Account, ig: str, video: Path, form: dict, sha: Optional[str] = None) -> str:
    c = G.gpost(acct, f"{ig}/media", form=dict(form, upload_type="resumable"))
    cid = str(c.get("id") or "")
    if not cid:
        raise D.PlatformError("Instagram made no container")
    _upload(acct, c.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{_ver()}/{cid}", video, sha)
    _wait_ready(acct, cid)
    return cid


def _publish(acct: C.Account, ig: str, cid: str, mark: Callable[[dict], None], what: str) -> dict:
    mark({"container_id": cid})
    pub = G.gpost(acct, f"{ig}/media_publish", form={"creation_id": cid})
    mid = str(pub.get("id") or "")
    if not mid:
        raise D.PlatformError("Instagram published but gave no id back")
    url = ""
    try:
        url = str(G.gget(acct, mid, {"fields": "permalink"}).get("permalink") or "")
    except Exception:  # noqa: BLE001  (it is up; the link is a nicety)
        url = ""
    return {"id": mid, "url": url, "endpoint": f"POST /{{id}}/media_publish ({what})"}


def ig_story(acct: C.Account, video: Path, mark: Callable[[dict], None], sha: Optional[str] = None) -> dict:
    """A card's MP4 or a clip (60 s at the most) as an Instagram story."""
    ig = _ig(acct)
    cid = _ig_video(acct, ig, video, {"media_type": "STORIES"}, sha)
    return _publish(acct, ig, cid, mark, "story")


def ig_carousel(acct: C.Account, videos: List[Path], mark: Callable[[dict], None],
                shas: Optional[List[str]] = None, caption: Optional[str] = None) -> dict:
    """A carousel of the slides' MP4s as one Instagram post (each child's bytes checked as it is uploaded), with the
    post's own words as its caption, for a news post."""
    ig = _ig(acct)
    if len(videos) < 2:
        raise SystemExit("an Instagram carousel needs two slides at the least")
    sh = list(shas) if shas is not None else [None] * len(videos)
    kids = [_ig_video(acct, ig, v, {"media_type": "VIDEO", "is_carousel_item": "true"}, x)
            for v, x in list(zip(videos, sh))[:10]]
    form = {"media_type": "CAROUSEL", "children": ",".join(kids)}
    if caption:
        form["caption"] = caption
    parent = G.gpost(acct, f"{ig}/media", form=form)
    pid = str(parent.get("id") or "")
    if not pid:
        raise D.PlatformError("Instagram made no carousel container")
    _wait_ready(acct, pid)
    return _publish(acct, ig, pid, mark, "carousel")


# ------------------------------------------------------------------------------------------------------ room
def _limit_path(acct: C.Account) -> Path:
    return store.path(acct.slug, "cards", "limit.json")


def publish_limit(acct: C.Account, now: Optional[datetime] = None) -> dict:
    """{"quota_total", "quota_usage", "at"}: Instagram's content_publishing_limit, read again after 30 minutes."""
    now = now or _now()
    p = _limit_path(acct)
    got = store.read(p, {})
    at = _when(got.get("at")) if isinstance(got, dict) else None
    if isinstance(got, dict) and at is not None and (now - at).total_seconds() < LIMIT_KEEP_S:
        return got
    ig = _ig(acct)
    row = ((G.gget(acct, f"{ig}/content_publishing_limit", {"fields": "config,quota_usage"}).get("data") or [{}])[0]
           or {})
    total = (row.get("config") or {}).get("quota_total")
    fresh = {"quota_total": int(total) if isinstance(total, (int, float)) else 50,
             "quota_usage": int(row.get("quota_usage") or 0), "at": _iso(now)}
    store.write(p, fresh)
    return fresh


def room_words(limit: dict, reels_due: int) -> str:
    """'' when a card may take an Instagram publish now; else why it waits."""
    total = int(limit.get("quota_total") or 50)
    used = int(limit.get("quota_usage") or 0)
    room = min(IG_ROOM, total // 2)
    if used + int(reels_due) >= room:
        return (f"Instagram's publishes: {used} used in 24 hours and {reels_due} Reels still due today, against "
                f"{room} kept for this account: the card waits so the Reels keep their room")
    return ""


# ------------------------------------------------------------------------------------------------------ looking up
def _rows(acct: C.Account, path: str, fields: str) -> List[dict]:
    got = G.gget(acct, path, {"fields": fields, "limit": 25})
    return [r for r in got.get("data") or [] if isinstance(r, dict)]


def _first_since(rows: List[dict], since: datetime, keys=("timestamp", "created_time", "creation_time")) -> Optional[dict]:
    best = None
    for r in rows:
        t = None
        for k in keys:
            v = r.get(k)
            if isinstance(v, (int, float)):
                t = datetime.fromtimestamp(v, timezone.utc)
            elif v:
                t = _when(str(v).replace("+0000", "+00:00"))
            if t is not None:
                break
        if t is not None and t >= since - FIND_SLACK and (best is None or t < best[0]):
            best = (t, r)
    return best[1] if best else None


def find(acct: C.Account, outlet: str, since: datetime) -> Optional[dict]:
    """What went up on that outlet since the try began ({"id", "url"}), or None: a lost answer looked up before
    anything goes again."""
    if outlet in ("facebook_story", "facebook_video_story"):
        k = G.meta_keys(acct)
        r = _first_since(_rows(acct, f"{k.page_id}/stories", "post_id,creation_time,url,status"), since)
        return {"id": str(r.get("post_id") or r.get("id") or ""), "url": str(r.get("url") or "")} if r else None
    if outlet == "facebook_feed":
        k = G.meta_keys(acct)
        r = _first_since(_rows(acct, f"{k.page_id}/feed", "id,created_time,permalink_url"), since)
        return {"id": str(r.get("id") or ""), "url": str(r.get("permalink_url") or "")} if r else None
    if outlet == "instagram_story":
        r = _first_since(_rows(acct, f"{_ig(acct)}/stories", "id,timestamp,permalink"), since)
        return {"id": str(r.get("id") or ""), "url": str(r.get("permalink") or "")} if r else None
    if outlet == "instagram_carousel":
        rows = [r for r in _rows(acct, f"{_ig(acct)}/media", "id,media_type,timestamp,permalink")
                if r.get("media_type") == "CAROUSEL_ALBUM"]
        r = _first_since(rows, since)
        return {"id": str(r.get("id") or ""), "url": str(r.get("permalink") or "")} if r else None
    raise ValueError(f"{outlet}: not a place cards go")


# ------------------------------------------------------------------------------------------------------ the probe
CREATOR_WORDS = ("not an instagram business", "is not an instagram business", "business account")


def probe_stories(acct: C.Account) -> dict:
    """Can this Instagram account take stories from the API: one unpublished STORIES container (it is never
    published and Instagram drops it after 24 hours). {"ok", "kind", "words", "container"?}. A Creator account
    is refused with (#10) on some apps: that is a NO, and switching to Business is Andrew's call."""
    from .stats import NotConnected
    try:
        ig = _ig(acct)
        got = G.gpost(acct, f"{ig}/media", form={"media_type": "STORIES", "upload_type": "resumable"})
        cid = str(got.get("id") or "")
        return {"ok": bool(cid), "kind": "ok" if cid else "other", "container": cid,
                "words": ("Instagram stories work here (an unpublished test container was made; it expires in 24 "
                          "hours)" if cid else "Instagram answered without a container id")}
    except NotConnected as e:
        return {"ok": False, "kind": "not_connected", "words": f"not connected: {e}"}
    except SystemExit as e:
        return {"ok": False, "kind": "refused", "words": str(e)}
    except D.PlatformError as e:
        info = G.classify(e)
        text = str(e).lower()
        if any(w in text for w in CREATOR_WORDS) or (info.code == 10 and "business" in text):
            return {"ok": False, "kind": "creator", "code": info.code,
                    "words": ("Instagram refused stories for this account because it is a Creator account. "
                              "Switching it to Business is Andrew's call (it changes the music the app offers).")}
        return {"ok": False, "kind": info.kind, "code": info.code, "words": f"Instagram refused it ({info.kind}): {e}"[:400]}
