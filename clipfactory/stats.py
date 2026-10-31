"""Stats: every post the factory handed out, with its numbers, in the shape the Clip Factory Views dashboard reads.

    factory.py stats [--days 120] [--no-refresh]

1. Walks every board of the last N days. A slot that reached an outlet has, per platform,
   `slot["outlets"][platform] = {"post_id", "url", "provider", "at", "metrics": {...}}`.
2. Unless --no-refresh, reads every recent video from each connected account, matches them to clips, writes the numbers back
   onto the boards (so the boards stay the one record). Videos that match no clip are kept too.
3. Writes one JSON file per dashboard document under state/stats/dashboard/posts/<doc id>.json, plus
   state/stats/posts.json (everything, flat). A scheduled Claude task pushes those files into the dashboard's
   database with ArtifactData (the database has no other way in).
4. The review page's Stats tab (review.py, statspage.py) reads the same boards and pulled files, and its Update now runs
   `update` too. One read of the platforms runs at a time (state/stats/read.lock), so the 03:30 run and Update now
   never write the same files at once.
5. The pulled files are merged, not replaced: a video older than the read window keeps its last numbers, marked
   frozen. Once a week the whole YouTube uploads list is read again (cheap on the quota); Facebook, Instagram and
   TikTok keep the 60-day window, because each of their videos costs a call of its own.
6. Each post can also carry how long people watched it (WATCH, read by direct.py since 2026-10-19: average watch
   time and share of the clip, Instagram's skip rate, YouTube's engaged views, reach, saves, replays and the retention
   curve). Every one is optional: a post without them counts as before. A field one read did not bring keeps its last
   value. The dashboard documents leave them out (their shape is the dashboard's); posts.json and the Stats tab have them.
7. Each read also adds a snapshot to state/stats/history/YYYY-MM-DD.json (every post's views, likes, comments and
   shares, totals per account and platform, follower counts from followers.py). Views per day and the monthly
   run-rate come from the difference between snapshots, and so does "this month" (month): the views gained since
   the month's first snapshot, a clip that went up this month counted whole.

The dashboard's document ids and post ids are reproduced exactly (see `doc_id` and `post_id`), so a post that
was also imported there from a platform CSV with the same video id merges instead of doubling.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from . import board as B
from . import config as C
from . import copytext as T

OUT = C.STATE / "stats"
# factory platform -> dashboard platform
DASH_PLATFORM = {"youtube_shorts": "youtube", "tiktok": "tiktok", "instagram_reels": "instagram", "facebook_reels": "facebook"}
METRICS = ("views", "likes", "comments", "shares")
# How long people watched (direct.py has where each comes from). Optional on every post; never in the dashboard files.
WATCH = ("watch_s", "watch_total_s", "watch_pct", "skip_pct", "left_3s_pct", "engaged", "engaged_pct", "reach", "saved",
         "plays_total", "replays", "curve")
AVERAGES = ("watch_s", "watch_pct", "skip_pct", "left_3s_pct", "engaged_pct")    # per play: none without a view
FULL_EVERY = timedelta(days=7)      # the whole YouTube uploads list is read again this often
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
PRIVATE = "SELF_ONLY"               # a TikTok post only its owner can see (a test): never counted as a post


def fnv36(s: str) -> str:
    """The dashboard's hash(): FNV-1a 32-bit over UTF-16 code units, in base 36."""
    h = 0x811C9DC5
    data = s.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    digits, out = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while True:
        h, r = divmod(h, 36)
        out = digits[r] + out
        if not h:
            return out


def norm_title(t: str) -> str:
    t = re.sub(r"#\S+", "", str(t or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", t).strip()[:60]


def post_id(p: dict) -> str:
    key = p.get("pid") or p.get("url") or f"{norm_title(p.get('title', ''))}|{str(p.get('at', ''))[:16]}"
    return fnv36("|".join([p["account"], p["platform"], key]))


def doc_id(p: dict, cfg: C.Config) -> str:
    d = datetime.fromisoformat(p["at"]).astimezone(cfg.tz())
    return f"{p['account']}__{p['platform']}__{d.year}-{d.month:02d}-{1 if d.day <= 15 else 2}"


def _title(slot: dict, plat: str) -> str:
    c = slot.get("copy", {}).get(plat) or slot.get("copy", {}).get("youtube_shorts")
    if c and Path(c["path"]).exists():
        try:
            w = T.parse(Path(c["path"]).read_text())
        except OSError:
            w = None
        if w is not None:
            t = w.get("title") or w.get("caption", "").split("\n", 1)[0]
            return t.strip()[:160]
    return (slot.get("source_title") or slot.get("slug") or "").strip()[:160]


def _when(iso) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def went_public(plat: str, rec: dict, s: dict) -> str:
    """When the post went public. A YouTube upload made ahead of its slot stays private until its publish time, and its
    upload time is not when anyone could see it, so it counts from the later of the two (as poster.went_public)."""
    at = rec.get("published_at") or s["publish_at"]
    if plat != "youtube_shorts":
        return at
    go = rec.get("publish_at") or (s.get("publish_at") if rec.get("video_id") else None)
    a, b = _when(at), _when(go)
    return go if a and b and b > a else at


def posts_from_boards(cfg: C.Config, days: int = 120) -> List[dict]:
    since = cfg.now().date() - timedelta(days=days)
    reads = last_reads()
    out: List[dict] = []
    for acct in cfg.accounts.values():
        for path in B.all_boards(acct):
            try:
                day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if day < since:
                continue
            bd = json.loads(path.read_text())
            for s in bd["slots"].values():
                for plat, rec in (s.get("outlets") or {}).items():
                    if plat not in DASH_PLATFORM or not (rec.get("post_id") or rec.get("video_id")):
                        continue
                    if rec.get("privacy") == PRIVATE:      # an 'Only me' TikTok test: nobody else saw it
                        continue
                    m = rec.get("metrics") or {}
                    p = {
                        "account": acct.slug, "platform": DASH_PLATFORM[plat], "title": _title(s, plat),
                        "url": rec.get("url", ""),
                        # a by-hand TikTok has no public id: its publish id keeps it apart from the others
                        "pid": str(rec.get("platform_id") or rec.get("video_id") or rec.get("publish_id")
                                   or rec.get("post_id") or ""),
                        "at": went_public(plat, rec, s),
                        "dur": round((s.get("clip") or {}).get("seconds") or 0), "fmt": s.get("format", ""),
                        "host": s.get("host") or "", "clip": s.get("slug", ""),
                        "slot": s.get("slot", ""), "board": path.stem, "rights": s.get("rights") or "",
                        # a spare from the bench (bench.py): which item, so fresh and spare clips can be told apart
                        "bench": (s.get("bench") or {}).get("id", "") if (s.get("bench") or {}).get("slug") == s.get("slug") else "",
                        **{k: int(m.get(k) or 0) for k in METRICS},
                        **{k: m[k] for k in WATCH if m.get(k) is not None},
                        "src": "sync", "upd": m.get("at") or rec.get("at") or B.utcnow(),
                    }
                    p["id"] = post_id(p)
                    read, got = reads.get(f"{acct.slug}__{plat}"), _when(m.get("at"))
                    if read and got and read - got > timedelta(hours=1):
                        p["frozen"] = True               # outside the read window: the numbers of its last read
                    out.append(p)
    return out


class NotConnected(Exception):
    """The account has no sign-in for that platform yet (factory.py connect ...)."""


def _listers() -> Dict[str, Callable]:
    from . import direct as D
    return {"youtube_shorts": D.yt_list, "tiktok": D.tiktok_list, "instagram_reels": D.ig_list, "facebook_reels": D.fb_list}


def _words(t: str) -> set:
    return {w for w in norm_title(t).split() if len(w) > 2}


def similar(a: str, b: str) -> float:
    na, nb = norm_title(a), norm_title(b)
    if na and nb and (na[:30] == nb[:30]):
        return 1.0
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def _slot_text(s: dict, plat: str) -> str:
    c = (s.get("copy") or {}).get(plat) or (s.get("copy") or {}).get("youtube_shorts")
    try:
        w = T.for_platform(Path(c["path"]).read_text(), plat) if c else None
    except OSError:
        w = None
    if not w:
        return s.get("slug", "").replace("-", " ")
    return w["title"] if plat == "youtube_shorts" else w["caption"].split("\n", 1)[0]


DUR_SLACK = 3.0   # seconds: a words-only match must also be the length the platform was sent (2026-10-17 audit)


def _sent_secs(s: dict, plat: str) -> float:
    """The length that platform was sent: its own cut when the slot has one, else the clip (0 when unknown)."""
    for rec in (((s.get("cuts") or {}).get(plat)), s.get("clip")):
        try:
            v = float((rec or {}).get("seconds") or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v
    return 0.0


def _length_agrees(s: dict, plat: str, v: dict) -> bool:
    """True unless both lengths are known and differ by more than DUR_SLACK (Instagram reports none)."""
    want = _sent_secs(s, plat)
    try:
        got = float(v.get("dur") or 0)
    except (TypeError, ValueError):
        got = 0.0
    return not (want > 0 and got > 0 and abs(want - got) > DUR_SLACK)


def set_aside_ids(rec: dict) -> set:
    """Every OLD YouTube upload this slot's record set aside (the last one and each before it): none is its post."""
    old = rec.get("old") or {}
    return {str(v) for v in [old.get("video_id")] + list(old.get("before") or []) if v}


def match(slots: List[dict], plat: str, videos: List[dict]) -> Dict[str, dict]:
    """slot key ('YYYY-MM-DD HH:MM') -> the platform video that is that clip. A recorded id wins; otherwise the words
    must agree (similar >= 0.6, best pair first), the length must agree when the platform reports one
    (_length_agrees: two clips of one hearing open on the same dateline) and the video must have gone up within 48
    hours of the slot. An OLD
    YouTube upload set aside when its slot was signed again with another clip is no slot's video (it has the same
    title: matched back, the new clip would never go up)."""
    out: Dict[str, dict] = {}
    used = set().union(*[set_aside_ids(((s.get("outlets") or {}).get(plat) or {})) for s in slots])
    by_id = {v["platform_id"]: v for v in videos}
    # an 'Only me' test post is not the public video: a public post of the same clip stays a post of its own
    slots = [s for s in slots if ((s.get("outlets") or {}).get(plat) or {}).get("privacy") != PRIVATE]
    for s in slots:
        rec = (s.get("outlets") or {}).get(plat) or {}
        vid = by_id.get(str(rec.get("platform_id") or rec.get("post_id") or rec.get("video_id") or ""))
        if vid:
            out[s["_key"]] = vid
            used.add(vid["platform_id"])
    pairs = []
    for s in slots:
        if s["_key"] in out:
            continue
        at = datetime.fromisoformat(s["publish_at"])
        text = _slot_text(s, plat)
        for v in videos:
            if v["platform_id"] in used:
                continue
            dt = abs((datetime.fromisoformat(v["published_at"]) - at).total_seconds())
            sc = similar(text, v.get("text", "")) if dt <= 48 * 3600 and _length_agrees(s, plat, v) else 0
            if sc >= 0.6:
                pairs.append((sc, -dt, s["_key"], v))
    for sc, _, key, v in sorted(pairs, key=lambda x: (x[0], x[1]), reverse=True):   # best words, then closest time
        if key not in out and v["platform_id"] not in used:
            out[key] = v
            used.add(v["platform_id"])
    return out


def _status_reader() -> Optional[Callable]:
    try:
        from . import followers as FL
    except ImportError:            # followers.py not copied in: the numbers are still read
        return None
    return FL.yt_status


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _complete(acct: C.Account, plat: str) -> bool:
    """Whether a read lists every video in its window. Through Upload-Post only the newest TikToks get numbers, so a
    TikTok missing from that read is not gone."""
    return not (plat == "tiktok" and (acct.outlets.get("tiktok") or {}).get("via") == "upload-post")


def _board_ids(boards: List[dict], plat: str) -> set:
    """Every video id the boards already hold for that platform (those posts are counted from the boards)."""
    ids = set()
    for bd in boards:
        for s in (bd.get("slots") or {}).values():
            rec = (s.get("outlets") or {}).get(plat) or {}
            if rec.get("privacy") == PRIVATE:
                continue
            ids |= {str(rec[k]) for k in ("platform_id", "post_id", "video_id") if rec.get(k)}
    return ids


def _apply_status(videos: List[dict], status: Dict[str, dict]) -> None:
    """YouTube's own word on each video. An upload made ahead of its slot is private with a publish time: it counts
    from then. A private (no publish time), unlisted or rejected video is kept but not counted as a post."""
    for v in videos:
        st = status.get(v["platform_id"])
        if not st:
            continue
        v["privacy"] = st.get("privacy") or ""
        go, up = _when(st.get("publish_at")), _when(v.get("published_at"))
        if v["privacy"] == "private" and go:
            v["upload_at"] = v.get("published_at")
            if not up or go > up:
                v["published_at"] = go.isoformat()
            v["scheduled"] = True
        elif v["privacy"] in ("private", "unlisted"):
            v["hidden"] = v["privacy"]
        if st.get("upload") in ("rejected", "failed", "deleted"):
            v["hidden"] = st.get("rejection") or st["upload"]


def _secs(x) -> float:
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


def _at_share(c: list, share: float) -> float:
    """The retention curve (21 points, 0 to 100% of the clip) at a share of the clip, drawn straight between points."""
    x = min(max(share, 0.0), 1.0) * 20
    i = min(int(x), 19)
    return float(c[i]) + (float(c[i + 1]) - float(c[i])) * (x - i)


def watch(v: dict, plat: str, secs: float = 0.0, old: Optional[dict] = None) -> dict:
    """The watch fields to keep for one video: what this read brought (direct.py), else what the last read had (YouTube
    Analytics runs two days behind; a curve is read about once a day). Two are worked out here: watch_pct where the
    platform gives only seconds (Instagram, Facebook: the average watch over the length that platform was sent) and
    left_3s_pct, the share of plays gone by 3 seconds on the retention curve (a 6-second clip or shorter has none;
    Facebook's curve sits at 100% for its first buckets on most Reels, so the page does not show it as a skip rate).
    A video nobody has watched has no averages (Facebook gives 0 seconds for no plays, and a 0% would pull every
    median down)."""
    w = {k: v[k] for k in WATCH if v.get(k) is not None}
    for k, val in (old or {}).items():
        if k in WATCH and k not in w and val is not None:
            w[k] = val
    if "views" in v and not v.get("views"):
        for k in AVERAGES:
            w.pop(k, None)
    try:
        if plat != "youtube_shorts" and secs > 0 and "watch_s" in w:
            w["watch_pct"] = round(float(w["watch_s"]) / secs * 100, 1)
        c = w.get("curve")
        if secs >= 6 and isinstance(c, list) and len(c) == 21 and float(c[0]) > 0:
            w["left_3s_pct"] = round(min(100.0, max(0.0, (1 - _at_share(c, 3 / secs) / float(c[0])) * 100)), 1)
    except (TypeError, ValueError):
        pass                          # a field of the wrong kind: the rest still count
    return w


def _notes(acct: C.Account, plat: str) -> List[str]:
    try:
        from . import direct as D
    except ImportError:
        return []
    return D.NOTES.pop(f"{acct.slug}__{plat}", [])


def refresh(cfg: C.Config, days: int = 30, listers: Optional[Dict[str, Callable]] = None,
            full: Optional[bool] = None, status: Optional[Callable] = None) -> List[str]:
    """Pull every recent video from each connected account, match them to the boards, and store the numbers there.
    Videos that match no clip (posted by hand, older work) are kept in state/stats/pulled/ and still reach the
    dashboard, just without format and source. See _refresh."""
    return _refresh(cfg, days=days, listers=listers, full=full, status=status)[0]


def _refresh(cfg: C.Config, days: int = 30, listers: Optional[Dict[str, Callable]] = None,
             full: Optional[bool] = None, status: Optional[Callable] = None) -> Tuple[List[str], int]:
    """(lines, how many account x platform reads worked).

    The pulled file is merged: a video older than the window keeps its last numbers, marked frozen; a video inside
    the window that the platform no longer lists is dropped (deleted). YouTube reads the whole uploads list when the
    last whole read is a week old (`full` forces it on or off); then every board is matched, not just recent ones."""
    if listers is None:
        listers = _listers()
        status = status or _status_reader()
    now = cfg.now()
    since = now - timedelta(days=days)
    pulled = OUT / "pulled"
    pulled.mkdir(parents=True, exist_ok=True)
    stamp = B.utcnow()
    lines: List[str] = []
    ok = 0
    for acct in cfg.accounts.values():
        boards: Dict[date, dict] = {}
        for path in B.all_boards(acct):
            try:
                d = date.fromisoformat(path.stem)
                boards[d] = json.loads(path.read_text())
            except (ValueError, OSError):
                continue
        recent = since.date() - timedelta(days=2)
        for plat, lister in listers.items():
            path = pulled / f"{acct.slug}__{plat}.json"
            prev = _read_json(path)
            last_full = _when(prev.get("full_at"))
            whole = plat == "youtube_shorts" and (full if full is not None
                                                   else not last_full or now - last_full >= FULL_EVERY)
            frm = EPOCH if whole else since
            paused = read_paused(cfg, acct, plat)
            if paused:                        # the social guard (switch on) holds this platform's reads
                lines.append(f"  {acct.name} {plat}: not read now, {paused}")
                continue
            try:
                videos = lister(acct, frm)
            except NotConnected as e:
                lines.append(f"  {acct.name} {plat}: not connected ({e})")
                continue
            except Exception as e:  # noqa: BLE001
                lines.append(f"  {acct.name} {plat}: FAILED to read numbers: {str(e)[:200]}")
                continue
            finally:                          # what the read could not get (no watch time yet, a metric refused)
                lines += [f"  {acct.name} {plat}: {n}" for n in _notes(acct, plat)]
            if plat == "youtube_shorts" and status and videos:
                try:
                    _apply_status(videos, status(acct, [v["platform_id"] for v in videos]))
                except Exception as e:  # noqa: BLE001  (the numbers still count; only public-or-not is unknown)
                    lines.append(f"  {acct.name} {plat}: could not read which videos are public ({str(e)[:120]})")
            slots = [dict(s, _key=f"{d} {s['slot']}", _day=d) for d, bd in boards.items() if whole or d >= recent
                     for s in bd["slots"].values() if s.get("clip") and s["status"] in (B.APPROVED, B.SCHEDULED)]
            hits = match(slots, plat, videos)
            on_board = _board_ids(list(boards.values()), plat) | {v["platform_id"] for v in hits.values()}
            seen = {v["platform_id"] for v in videos}
            before = {str((v or {}).get("platform_id") or ""): v for v in prev.get("videos") or [] if isinstance(v, dict)}
            kept = [dict(v, **watch(v, plat, _secs(v.get("dur")), before.get(v["platform_id"])), read_at=stamp)
                    for v in videos if v["platform_id"] not in on_board]
            for v in prev.get("videos") or []:
                pid = str((v or {}).get("platform_id") or "")
                if not pid or pid in seen or pid in on_board:
                    continue
                at = _when(v.get("published_at"))
                if _complete(acct, plat) and (whole or (at and at >= frm)):
                    continue                  # it should have been in this read: gone from the platform
                kept.append(dict(v, frozen=True, read_at=v.get("read_at") or prev.get("at") or stamp))
            _write_json(path, {"at": stamp, "full_at": stamp if whole else prev.get("full_at", ""), "videos": kept})
            for d in sorted({s["_day"] for s in slots if s["_key"] in hits}):
                with B.edit(cfg, acct, d, create=False) as bd:
                    for k, s in bd["slots"].items():
                        v = hits.get(f"{d} {k}")
                        if not v:
                            continue
                        r = s.setdefault("outlets", {}).setdefault(plat, {})
                        if v["platform_id"] in set_aside_ids(r) and not r.get("video_id"):
                            continue          # set aside while this read was under way: not this slot's post
                        r.setdefault("post_id", v["platform_id"])
                        r.setdefault("provider", "matched")
                        secs = _sent_secs(s, plat) or _secs(v.get("dur"))
                        r.update(platform_id=v["platform_id"], url=v.get("url", ""), published_at=v["published_at"],
                                 metrics={**{m: int(v.get(m) or 0) for m in METRICS},
                                          **watch(v, plat, secs, r.get("metrics")), "at": B.utcnow()})
            ok += 1
            frozen = sum(1 for v in kept if v.get("frozen"))
            timed = sum(1 for v in videos if any(v.get(k) is not None for k in WATCH))
            lines.append(f"  {acct.name} {plat}: {len(videos)} videos, {len(hits)} matched to factory clips"
                         + (" (the whole channel)" if whole else "") + (f"; {frozen} older kept as last read" if frozen else "")
                         + (f"; watch time on {timed}" if timed else ""))
    return lines, ok


def read_paused(cfg: C.Config, acct: C.Account, plat: str) -> str:
    """The social guard's words when it holds this platform's reads (its switch on; Meta or YouTube asked us to wait),
    '' otherwise, and '' whenever the social layer is not built or cannot tell: a read is never stopped by mistake."""
    try:
        from . import registry as REG
        f = REG.fn("outward:read_paused")
        got = f(cfg, acct, plat) if f is not None else ""
        return got if isinstance(got, str) else ""
    except (Exception, SystemExit):  # noqa: BLE001  (a refusal there never stops a read either)
        return ""


def last_reads() -> Dict[str, datetime]:
    """'account__platform' -> when its numbers were last read."""
    d = OUT / "pulled"
    out: Dict[str, datetime] = {}
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        at = _when(_read_json(f).get("at"))
        if at:
            out[f.stem] = at
    return out


def pulled_posts(cfg: C.Config) -> List[dict]:
    """The videos that matched no clip, as dashboard posts. A private or unlisted YouTube video is not a post."""
    out = []
    for f in sorted((OUT / "pulled").glob("*.json")) if (OUT / "pulled").exists() else []:
        slug, plat = f.stem.split("__", 1)
        if slug not in cfg.accounts or plat not in DASH_PLATFORM:
            continue
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for v in data.get("videos", []):
            if v.get("hidden"):
                continue
            p = {"account": slug, "platform": DASH_PLATFORM[plat], "title": (v.get("text") or "").split("\n", 1)[0][:160],
                 "url": v.get("url", ""), "pid": v["platform_id"], "at": v["published_at"], "dur": round(v.get("dur") or 0),
                 "fmt": "", "host": "", "clip": "", **{m: int(v.get(m) or 0) for m in METRICS},
                 **{k: v[k] for k in WATCH if v.get(k) is not None},
                 "src": "sync", "upd": v.get("read_at") or data.get("at") or B.utcnow()}
            p["id"] = post_id(p)
            if v.get("frozen"):
                p["frozen"] = True
            out.append(p)
    return out


PAGE_ONLY = ("slot", "board", "rights", "frozen")     # for the Stats tab; the dashboard files keep their shape


def write(cfg: C.Config, posts: List[dict], out: Optional[Path] = None) -> List[str]:
    out = out or OUT
    posts = [{k: v for k, v in p.items() if k not in PAGE_ONLY} for p in posts]
    docs_dir = out / "dashboard" / "posts"
    if docs_dir.exists():
        for f in docs_dir.glob("*.json"):
            f.unlink()
    docs_dir.mkdir(parents=True, exist_ok=True)
    docs: Dict[str, dict] = {}
    for p in posts:
        did = doc_id(p, cfg)
        acct, plat, period = did.split("__")
        docs.setdefault(did, {"account": acct, "platform": plat, "period": period, "items": {}})["items"][p["id"]] = \
            {k: v for k, v in p.items() if k not in WATCH}
    for did, body in docs.items():
        (docs_dir / f"{did}.json").write_text(json.dumps(body, separators=(",", ":")))
    (out / "posts.json").write_text(json.dumps({"generated_at": B.utcnow(), "posts": posts}, indent=1))
    big = [d for d in docs if (docs_dir / f"{d}.json").stat().st_size > 200_000]
    lines = [f"  {len(posts)} posts in {len(docs)} dashboard documents -> {docs_dir}"]
    if big:
        lines.append(f"  WARNING: {', '.join(big)} are over 200 KB (the dashboard's limit is 256 KB a document)")
    return lines


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, path)


def all_posts(cfg: C.Config, days: int = 120) -> List[dict]:
    """Every post the boards know, plus the videos that matched no clip."""
    posts = posts_from_boards(cfg, days=days)
    seen = {p["id"] for p in posts}
    return posts + [p for p in pulled_posts(cfg) if p["id"] not in seen]


@contextlib.contextmanager
def reading(wait: bool = True) -> Iterator[bool]:
    """One read of the platforms at a time. Yields False when wait=False and another read is running."""
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "read.lock", "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True


# ------------------------------------------------------------------------------------------------------ history
FOLLOWER_KEYS = ("followers", "hidden", "views", "videos", "fans", "media", "likes")


def _int(vals, i: int) -> int:
    try:
        return int(vals[i] or 0)
    except (TypeError, ValueError, IndexError):
        return 0


def snapshot(cfg: C.Config, posts: List[dict], followers: Optional[dict] = None,
             now: Optional[datetime] = None) -> Path:
    """Add this read to state/stats/history/<local day>.json: per post [views, likes, comments, shares, went up
    (epoch)], totals per account and platform, and follower counts. One snapshot an hour: a second read in the
    same hour replaces the first."""
    now = now or cfg.now()
    tz = cfg.tz()
    loc = now.astimezone(tz)
    path = OUT / "history" / f"{loc.date().isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    hour = loc.strftime("%Y-%m-%d %H")
    reads = [r for r in (_read_json(path).get("reads") or []) if isinstance(r, dict) and _when(r.get("at"))
             and _when(r.get("at")).astimezone(tz).strftime("%Y-%m-%d %H") != hour]   # type: ignore[union-attr]
    snap_posts: Dict[str, list] = {}
    totals: Dict[str, dict] = {}
    for p in posts:
        at = _when(p.get("at"))
        snap_posts[p["id"]] = [int(p.get(k) or 0) for k in METRICS] + [int(at.timestamp()) if at else 0]
        t = totals.setdefault(f"{p['account']}__{p['platform']}", {"posts": 0, "views": 0})
        t["posts"] += 1
        t["views"] += int(p.get("views") or 0)
    counts = {slug: {plat: {k: r[k] for k in FOLLOWER_KEYS if k in r} for plat, r in (by or {}).items()
                     if isinstance(r, dict) and not r.get("error")}
              for slug, by in (followers or {}).items() if isinstance(by, dict)}
    reads.append({"at": now.astimezone(timezone.utc).isoformat(timespec="seconds"), "posts": snap_posts,
                  "totals": totals, "followers": counts})
    reads.sort(key=lambda r: _when(r["at"]) or EPOCH)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"day": loc.date().isoformat(), "reads": reads}, separators=(",", ":")))
    os.replace(tmp, path)
    return path


def history(cfg: C.Config, now: Optional[datetime] = None, days: int = 7) -> dict:
    """What the snapshots say about the last `days` days: views each post gained between the oldest snapshot inside
    the window and the newest one. A post that went up after the oldest snapshot counts from zero; a post first seen
    later than that but older (a video the read had not found before) has no starting point and is left out."""
    hd = OUT / "history"
    files = sorted(f for f in hd.glob("*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem)) if hd.exists() else []
    out = {"days": len(files), "first": files[0].stem if files else "", "span": 0.0, "base_at": "", "last_at": "",
           "gains": {}}
    now = now or cfg.now()
    floor_day = (now.astimezone(cfg.tz()) - timedelta(days=days + 1)).date().isoformat()
    snaps = []
    for f in files:
        if f.stem < floor_day:
            continue
        for r in _read_json(f).get("reads") or []:
            at = _when(r.get("at")) if isinstance(r, dict) else None
            if at and isinstance(r.get("posts"), dict):
                snaps.append((at, r))
    if not snaps:
        return out
    snaps.sort(key=lambda x: x[0])
    last_at, last = snaps[-1]
    base_at, base = next(x for x in snaps if x[0] >= last_at - timedelta(days=days, hours=1))
    gains: Dict[str, int] = {}
    for pid, vals in last["posts"].items():
        if pid in base["posts"]:
            g = _int(vals, 0) - _int(base["posts"][pid], 0)
        elif _int(vals, 4) >= base_at.timestamp() - 3600:
            g = _int(vals, 0)
        else:
            continue
        if g > 0:
            gains[pid] = g
    out.update(span=round((last_at - base_at).total_seconds() / 86400, 3), base_at=base_at.isoformat(),
               last_at=last_at.isoformat(), gains=gains)
    return out


def month(cfg: C.Config, now: Optional[datetime] = None) -> dict:
    """This calendar month (local time) from the snapshots: the views each post gained since the month's FIRST
    snapshot, and each follower count's gain over the same reads. A clip that went up this month counts whole (every
    view it has is this month's). An older clip counts what it gained since that first snapshot; one first seen later
    has no starting point and is left out. `partial`: the first snapshot came after the month's first day (the history
    began later, or the 1st's read failed), so older clips count from then, and the page says so."""
    now = now or cfg.now()
    tz = cfg.tz()
    loc = now.astimezone(tz)
    first = loc.date().replace(day=1)
    start = datetime(first.year, first.month, 1, tzinfo=tz).timestamp()
    out = {"month": first.strftime("%Y-%m"), "first_day": first.isoformat(), "base_at": "", "last_at": "",
           "partial": False, "reads": 0, "gains": {}, "followers": {}}
    hd = OUT / "history"
    files = sorted(f for f in hd.glob(f"{out['month']}-*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem)
                   and f.stem <= loc.date().isoformat()) if hd.exists() else []
    snaps = []
    for f in files:
        for r in _read_json(f).get("reads") or []:
            at = _when(r.get("at")) if isinstance(r, dict) else None
            if at and isinstance(r.get("posts"), dict) and at.timestamp() >= start and at <= now + timedelta(minutes=5):
                snaps.append((at, r))
    if not snaps:
        return out
    snaps.sort(key=lambda x: x[0])
    base_at, base = snaps[0]
    last_at, last = snaps[-1]
    gains: Dict[str, int] = {}
    for pid, vals in last["posts"].items():
        if _int(vals, 4) >= start:
            g = _int(vals, 0)                         # went up this month: all of it
        elif pid in base["posts"]:
            g = _int(vals, 0) - _int(base["posts"][pid], 0)
        else:
            continue
        if g > 0:
            gains[pid] = g
    fol: Dict[str, dict] = {}
    for slug, by in (last.get("followers") or {}).items():
        for plat, rec in (by or {}).items():
            n = rec.get("followers") if isinstance(rec, dict) else None
            if not isinstance(n, int):
                continue
            first_n = next((((r.get("followers") or {}).get(slug) or {}).get(plat, {}).get("followers"), at)
                           for at, r in snaps
                           if isinstance((((r.get("followers") or {}).get(slug) or {}).get(plat) or {}).get("followers"),
                                         int))
            fol.setdefault(slug, {})[plat] = {"now": n, "gain": n - first_n[0],
                                              "since": first_n[1].isoformat(timespec="seconds")}
    out.update(base_at=base_at.isoformat(timespec="seconds"), last_at=last_at.isoformat(timespec="seconds"),
               partial=base_at.astimezone(tz).date() > first, reads=len(snaps), gains=gains, followers=fol)
    return out


def _counts_and_history(cfg: C.Config, read_ok: int) -> List[str]:
    """Follower counts (followers.py), then the history snapshot. Neither ever stops the read."""
    lines: List[str] = []
    counts: dict = {}
    try:
        from . import followers as FL
        counts, said = FL.read_all(cfg)
        lines += said
    except Exception as e:  # noqa: BLE001
        lines.append(f"  follower counts: not read ({type(e).__name__})")
    if read_ok:                       # a read where nothing could be read would only repeat the last numbers
        try:
            snapshot(cfg, all_posts(cfg, days=3650), counts)
        except Exception as e:  # noqa: BLE001
            lines.append(f"  history: not written ({type(e).__name__}: {str(e)[:120]})")
    return lines


def update(cfg: C.Config, days: int = 120, fresh: bool = True, wait: bool = True) -> List[str]:
    """Read the numbers (unless fresh=False), then write the dashboard files: `factory.py stats` and Update now.
    A fresh read also reads the follower counts and adds a history snapshot."""
    with reading(wait) as got:
        if not got:
            return ["  another read of the numbers is running: try again in a minute"]
        lines, ok = _refresh(cfg, days=min(days, 60)) if fresh else ([], 0)
        from . import accounts as AC       # a client's posts never reach the owner's dashboard (accounts.py)
        lines = lines + write(cfg, all_posts(AC.own_view(cfg), days=days))
        return lines + (_counts_and_history(cfg, ok) if fresh else [])
