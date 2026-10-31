"""The checkpoint reads: each posted clip's numbers asked for by its id, a few times in its first month.

Every call goes through graph.py (and so direct._http and net.guard): gget for Meta, yt_get for the YouTube Data API
(counted against this machine's units), yta_get for YouTube Analytics. Nothing here runs in a page, a render or a
test; only checkpoints.run (the 15-minute timer) and a run typed by hand call it. The answers are parsed the way the
nightly read parses them (direct.yt_list, ig_list, fb_list), so a checkpoint and the 03:30 read never disagree.

    yt_stats(acct, ids)                     videos?part=statistics,status for up to 50 ids: 1 unit
    ig_numbers(acct, media_id)              /{media}/insights?metric=views,likes,comments,shares
    fb_numbers(acct, video_id, comments)    /{video}/video_insights blue_reels_play_count, likes, social actions
    ig_extra(acct, media_id, metrics)       one more insights call: reach, saves, watch time, skip rate ...
    fb_extra(acct, video_id, metrics)       one more video_insights call: plays, reach, watch time, follows ...
    yta_totals(acct, video_id, start, end)  YouTube Analytics: engaged views, average view, shares, subscribers
    yta_retention(acct, video_id, start, end)  YouTube Analytics: the share still watching along the clip

A metric the platform did not send is left out (absent, never 0): a missing number and a zero mean different things
to the reports.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Sequence

from . import config as C
from . import graph as G

YT_BATCH = 50                         # ids per videos.list call (1 unit for up to 50)
IG_BASE = ("views", "likes", "comments", "shares")
FB_BASE = ("blue_reels_play_count", "post_video_likes_by_reaction_type", "post_video_social_actions")
IG_EXTRA = ("reach", "saved", "total_interactions", "ig_reels_avg_watch_time", "ig_reels_video_view_total_time",
            "reels_skip_rate")
FB_EXTRA = ("fb_reels_total_plays", "fb_reels_replay_count", "post_impressions_unique", "post_video_avg_time_watched",
            "post_video_view_time", "post_video_followers")
FB_RETENTION = "post_video_retention_graph"
YTA_METRICS = ("engagedViews", "averageViewDuration", "averageViewPercentage", "shares", "subscribersGained")
YTA_RETENTION = "audienceWatchRatio"


def _num(v) -> Optional[float]:
    """A number from an answer, or None (a dict, a word, a missing value)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        f = float(str(v))
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _value(row: dict):
    """An insights row's value: values[0].value (lifetime), else total_value.value (Instagram's newer shape)."""
    vals = row.get("values")
    if isinstance(vals, list) and vals and isinstance(vals[0], dict) and "value" in vals[0]:
        return vals[0]["value"]
    tv = row.get("total_value")
    if isinstance(tv, dict) and "value" in tv:
        return tv["value"]
    return None


def rows_of(answer: dict) -> Dict[str, object]:
    """{metric name: raw value} from an insights answer; a row with no value is left out."""
    out: Dict[str, object] = {}
    for row in (answer or {}).get("data") or []:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        v = _value(row)
        if v is not None:
            out[str(row["name"])] = v
    return out


# ------------------------------------------------------------------------------------------------------ YouTube
def parse_yt(answer: dict) -> Dict[str, dict]:
    """{video id: {"views", "likes", "comments", "public", "privacy"}} from videos.list (part statistics,status).
    A count YouTube hides (likes switched off) is left out. A video not in the answer is gone, or not ours."""
    out: Dict[str, dict] = {}
    for v in (answer or {}).get("items") or []:
        if not isinstance(v, dict) or not v.get("id"):
            continue
        st = v.get("statistics") or {}
        privacy = str((v.get("status") or {}).get("privacyStatus") or "")
        rec: dict = {"privacy": privacy, "public": privacy == "public"}
        for key, name in (("views", "viewCount"), ("likes", "likeCount"), ("comments", "commentCount")):
            n = _num(st.get(name))
            if n is not None:
                rec[key] = int(n)
        out[str(v["id"])] = rec
    return out


def yt_stats(acct: C.Account, ids: Sequence[str], cfg: Optional[C.Config] = None) -> Dict[str, dict]:
    """One videos.list call (1 unit) for up to 50 ids: their counts and whether each is public."""
    ids = [str(i) for i in ids if i][:YT_BATCH]
    if not ids:
        return {}
    got = G.yt_get(acct, "videos", {"part": "statistics,status", "id": ",".join(ids), "maxResults": YT_BATCH},
                   cfg=cfg)
    return parse_yt(got)


# ------------------------------------------------------------------------------------------------------ Meta
def parse_ig(answer: dict) -> Dict[str, int]:
    """views, likes, comments and shares of one Reel (only those it sent)."""
    raw = rows_of(answer)
    out: Dict[str, int] = {}
    for k in IG_BASE:
        n = _num(raw.get(k))
        if n is not None:
            out[k] = int(n)
    return out


def ig_numbers(acct: C.Account, media_id: str) -> Dict[str, int]:
    """GET /{media}/insights?metric=views,likes,comments,shares (as direct.ig_list reads it)."""
    return parse_ig(G.gget(acct, f"{media_id}/insights", {"metric": ",".join(IG_BASE)}))


def parse_fb(answer: dict, comments: Optional[int] = None) -> Dict[str, int]:
    """views (blue_reels_play_count: plays, without replays), likes (every reaction added up), and comments and
    shares from post_video_social_actions: a dict when Facebook splits them; one combined number otherwise, where
    whatever is not a comment is a share (only when the comments are known, as direct.fb_list does)."""
    raw = rows_of(answer)
    out: Dict[str, int] = {}
    n = _num(raw.get("blue_reels_play_count"))
    if n is not None:
        out["views"] = int(n)
    likes = raw.get("post_video_likes_by_reaction_type")
    if isinstance(likes, dict):
        out["likes"] = int(sum(x for x in (_num(v) for v in likes.values()) if x is not None))
    elif _num(likes) is not None:
        out["likes"] = int(_num(likes))           # type: ignore[arg-type]
    acts = raw.get("post_video_social_actions")
    if isinstance(acts, dict):
        low = {str(k).lower(): _num(v) for k, v in acts.items()}
        out["shares"] = int(low.get("share") or 0)
        out["comments"] = max(int(comments or 0), int(low.get("comment") or 0))
    elif _num(acts) is not None and comments is not None:
        out["comments"] = int(comments)
        out["shares"] = max(0, int(_num(acts)) - int(comments))     # type: ignore[arg-type]
    return out


def fb_numbers(acct: C.Account, video_id: str, comments: Optional[int] = None) -> Dict[str, int]:
    """GET /{video}/video_insights with the nightly read's three metrics, period lifetime."""
    return parse_fb(G.gget(acct, f"{video_id}/video_insights",
                           {"metric": ",".join(FB_BASE), "period": "lifetime"}), comments)


def retention(raw) -> Optional[List[List[float]]]:
    """Facebook's retention graph ({"0": 1.0, "1": 0.93, ...}, a share or a percent of plays at each step) as
    [[where in the clip 0..1, share still watching], ...], at most 100 points; None when it is not that shape."""
    if not isinstance(raw, dict) or not raw:
        return None
    pts = []
    for k, v in raw.items():
        x, y = _num(k), _num(v)
        if x is not None and y is not None:
            pts.append((float(x), float(y)))
    if len(pts) < 2:
        return None
    pts.sort()
    top = max(y for _, y in pts)
    scale = 100.0 if top > 1.5 else 1.0
    last = pts[-1][0] or 1.0
    step = max(1, len(pts) // 100)
    return [[round(x / last, 4), round(y / scale, 4)] for x, y in pts[::step]][:100]


def parse_extra(answer: dict) -> Dict[str, object]:
    """The extra metrics' values: numbers as numbers, the retention graph as points; anything else left out."""
    out: Dict[str, object] = {}
    for k, v in rows_of(answer).items():
        if k == FB_RETENTION:
            pts = retention(v)
            if pts:
                out["retention"] = pts
            continue
        n = _num(v)
        if n is not None:
            out[k] = n
    return out


def ig_extra(acct: C.Account, media_id: str, metrics: Sequence[str]) -> Dict[str, object]:
    """One more insights call, apart from the base read: a metric Instagram refuses never costs the views."""
    return parse_extra(G.gget(acct, f"{media_id}/insights", {"metric": ",".join(metrics)}))


def fb_extra(acct: C.Account, video_id: str, metrics: Sequence[str]) -> Dict[str, object]:
    """One more video_insights call (period lifetime), apart from the base read."""
    return parse_extra(G.gget(acct, f"{video_id}/video_insights", {"metric": ",".join(metrics), "period": "lifetime"}))


# ------------------------------------------------------------------------------------------------------ Analytics
def _report(answer: dict) -> List[Dict[str, object]]:
    heads = [str((h or {}).get("name") or "") for h in (answer or {}).get("columnHeaders") or []]
    return [dict(zip(heads, row)) for row in (answer or {}).get("rows") or [] if isinstance(row, list)]


def parse_yta(answer: dict) -> Dict[str, object]:
    """The one row of a per-video totals report: each metric it sent."""
    rows = _report(answer)
    out: Dict[str, object] = {}
    for k, v in (rows[0] if rows else {}).items():
        n = _num(v)
        if k in YTA_METRICS and n is not None:
            out[k] = n
    return out


def parse_yta_retention(answer: dict) -> Optional[List[List[float]]]:
    """[[elapsedVideoTimeRatio, audienceWatchRatio], ...] (at most 100 points), None when there is none yet."""
    pts = []
    for r in _report(answer):
        x, y = _num(r.get("elapsedVideoTimeRatio")), _num(r.get(YTA_RETENTION))
        if x is not None and y is not None:
            pts.append([round(float(x), 4), round(float(y), 4)])
    pts.sort()
    return pts[:100] if len(pts) >= 2 else None


def yta_totals(acct: C.Account, video_id: str, start: date, end: date,
               cfg: Optional[C.Config] = None) -> Dict[str, object]:
    """YouTube Analytics for one video from the day it went up: engaged views, average view (seconds and percent),
    shares, subscribers gained. Its numbers lag a day or two, so it is read at 72h, 7d and 30d only."""
    return parse_yta(G.yta_get(acct, {"ids": "channel==MINE", "startDate": start.isoformat(),
                                      "endDate": end.isoformat(), "metrics": ",".join(YTA_METRICS),
                                      "filters": f"video=={video_id}"}, cfg=cfg))


def yta_retention(acct: C.Account, video_id: str, start: date, end: date,
                  cfg: Optional[C.Config] = None) -> Optional[List[List[float]]]:
    """YouTube Analytics' audience retention for one video (one video per call): read at 7d."""
    return parse_yta_retention(G.yta_get(acct, {
        "ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(),
        "metrics": YTA_RETENTION, "dimensions": "elapsedVideoTimeRatio", "filters": f"video=={video_id}"}, cfg=cfg))
