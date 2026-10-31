"""Drop mode (Gaming): when Rockstar posts, find the best streamer reactions within minutes and line them up as
a REACTIONS candidate.

The account's best post by far is many named streamers reacting to one Rockstar drop (31,588 views, 8x anything
else), and the official Speed and Kai channels put their own reactions to the 10-15 Launch Trailer up 6 and 10 hours
after it. The clips viewers make on Twitch and Kick exist within minutes of the moment, so this reads those.

Every 5 minutes (launchd `gta-drop`, `factory.py drop gaming`):
1. Rockstar's channel feed (config `drop.rockstar`). A new upload whose title is GTA VI (`film_title`) opens a drop.
   Rockstar posts each trailer two to four times within a minute (two copies of the Launch Trailer 22 s apart), so
   uploads `group_min` minutes apart are one drop, keyed by the first.
2. While a drop is open, each roster face (`drop.streamers`: a name and a Twitch login and/or a Kick slug) gets a
   window: the first `window_h` hours (3) after the upload if the face is live then, else the first
   `face_stream_min` minutes (90) of the face's first stream after the upload, up to `horizon_h` (12) after it. The
   10-15 Launch Trailer went up at 07:00 Mountain: no roster face was live on Twitch then, they went live 2 to 8 hours
   later and reacted then (measured, see the backtest), so a window that closed 3 hours after the upload would have
   found nothing.
3. LISTENING (`listen`): while a face's window is open on Twitch, each run fetches the newest stretch of its
   stream's VOD as audio only (yt-dlp --download-sections, exact cuts, no login, at most listen_chunk_min minutes,
   listen_lag_s behind live) and the skill's scripts/filmmatch.py looks for the drop's own sound in it. A hit is the
   face's first viewing to the second, pauses and rewinds included, on the VOD's clock: the stretch the REACTIONS
   cut takes. On 10-15 it heard Kai, Speed, Joe Bartolozzi, Jynxzi and xQc in the first 30 minutes of their streams;
   only 5 of 553 clips of those hours held the trailer, and nobody clipped Joe's or Speed's at all.
   Their clips (the skill's scripts/streamclips.py: Twitch's public GQL, the same client vodwindow.py uses, and
   Kick's unofficial API, which fails soft) made inside the face's window are kept, their views noted each run,
   and ranked by views a minute since the clip was made x the face's weight (scout.json `people`) x relevance.
   Relevance: clip titles almost never name the trailer ("gng", "😭😭" on 10-15), so a clip in another game's category
   counts x0.3, a title, stream or VOD title naming GTA or the trailer x1.5, and above all `verify`: each top clip is
   fetched small and the skill's scripts/filmmatch.py checks that the drop's own sound is IN it (it hit the
   frame-checked moments of the 10-17 jobs to 0.01 s). A clip without the film is dropped; one with it says which
   film seconds it shows, and the film second the most faces show is the suggested moment.
4. Once `min_faces` faces are heard or have a verified clip, the candidate goes into the GTA inbox
   (Publish/inbox.json, key drop-<film id>, status "reactions", found_by "drop"): the film, the faces (heard first,
   the biggest names first), each face's VOD and the second the film starts in it, its clips, and the film span
   every face watched. The night's maker and watch.py cut only "new" rows, so nothing cuts it by
   itself: the REACTIONS route (the gaming skill, every streamer checked on frames) takes it from there. A Mac
   notice says so once (`notify`).
5. `feed`: Twitch clips by game (GTA V now; GTA VI the day its category exists) and Kick's GTA category, every
   `feed.every_min` minutes (every run while a drop is open): faces beyond the roster with `follower_floor`
   followers or more are suggested on the scout board, never fed.

Rules that hold: before `street_date` (2026-11-19) a title about a leak, an early copy or a street-date break, or a
clip in a GTA VI category (an early copy being played), is never kept. Never two REACTIONS clips on one day: the
candidate names the first day whose REACTIONS slot is free (factory.py place refuses a second one anyway).

State: state/<account>/drop/watch.json (the uploads seen), <film id>.json (one drop), feed.json, clips/ (the
small copies verify checked). Board: the scout board's "Drop mode" and "Faces beyond the roster" blocks
(scout.write_markdown calls board_lines). Backtest: `factory.py drop gaming --backtest <fixture.json>` replays a
saved drop offline.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import config as C

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
SEEN_KEEP = 40      # view counts kept per clip (one a run: the last 3 hours and 20 minutes)
DEFAULTS = {
    "film_title": r"\b(grand theft auto vi|grand theft auto 6|gta ?vi|gta ?6)\b",
    "skip_title": r"\b(gta online|red dead|rdr ?2?|max payne|bully)\b",
    "group_min": 15, "window_h": 3.0, "face_stream_min": 90, "horizon_h": 12.0, "every_min": 5,
    "min_faces": 3, "max_faces": 6, "per_face": 2, "min_age_min": 3.0,
    "street_date": "2026-11-19",
    "leak_titles": r"\b(leak(s|ed|er|ers|ing)?|early cop(y|ies)|street ?date|broke (the )?street|"
                   r"pre-?release (copy|gameplay)|played (it )?early)\b|\bcyber ?leek",
    "topic": r"\b(gta ?(6|vi)?|grand theft auto|rockstar|trailer|the city|leonida|lucia|jason duval)\b",
    "neutral_games": r"^(|just chatting|irl|special events|watch parties|talk shows.*|podcasts?|"
                     r"grand theft auto.*|gta.*)$",
    "other_game_factor": 0.3, "topic_factor": 1.5,
    "verify": True, "verify_max": 8, "verify_total": 60,
    "listen": True, "listen_chunk_min": 15, "listen_faces": 6, "listen_lag_s": 30,
    "notify": True,
}
FEED_DEFAULTS = {"twitch_games": ["Grand Theft Auto V"], "watch_for": ["Grand Theft Auto VI"],
                 "kick_categories": ["grand-theft-auto-v"], "follower_floor": 250000, "every_min": 60, "top": 12,
                 "languages": ["en"]}


# ------------------------------------------------------------------------------------------------- settings
def settings(slug: str) -> dict:
    """scout.json <account>.drop over the defaults; {} (drop mode off) when the block is missing or "on" is false."""
    from . import scout as SC
    try:
        raw = SC.load(slug).get("drop")
    except SystemExit:
        return {}
    if not isinstance(raw, dict) or not raw.get("on", True):
        return {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in raw.items() if k != "feed"})
    out["feed"] = dict(FEED_DEFAULTS, **(raw.get("feed") or {}))
    return out


def _utc(s) -> Optional[datetime]:
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _z(t: Optional[datetime]) -> Optional[str]:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def drop_dir(acct: C.Account) -> Path:
    d = C.STATE / acct.slug / "drop"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def _write(p: Path, data) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(p)


# ------------------------------------------------------------------------------------------------- 1. the watch
def rockstar_uploads(dcfg: dict, rss: Optional[Callable] = None, yt: Optional[Callable] = None,
                     allow_fallback: bool = True) -> Tuple[List[dict], str, Optional[str]]:
    """Rockstar's newest uploads [{id, title, published (UTC ISO), url}], how they were read ('rss' / 'yt-dlp'),
    and the error when neither answered. The channel feed costs one request and carries exact upload times; the
    fallback (the channel's uploads list via yt-dlp, the scout's own _yt) gives approximate times."""
    from . import scout as SC
    rk = dcfg.get("rockstar") or {}
    cid = rk.get("channel_id") or ""
    if not cid:
        return [], "", "no drop.rockstar.channel_id in config/scout.json"
    rss = rss or SC._rss
    try:
        rows = rss(FEED_URL.format(cid=cid))
        out = [{"id": r.get("video_id") or r["url"].rsplit("=", 1)[-1], "title": r.get("title") or "",
                "published": _z(r.get("published")), "url": r["url"]} for r in rows if r.get("url")]
        return out, "rss", None
    except Exception as e:  # noqa: BLE001 - any feed failure falls through to the list
        down = f"{type(e).__name__}: {e}"[:160]
    if not allow_fallback:
        return [], "", f"rss down ({down}); yt-dlp fallback waits its turn"
    yt = yt or SC._yt
    try:
        rows = yt("https://www.youtube.com/playlist?list=UU" + cid[2:], 15)
    except Exception as e:  # noqa: BLE001
        return [], "", f"rss down ({down}); yt-dlp: {type(e).__name__}: {e}"[:300]
    out = []
    for e in rows:
        ts = e.get("timestamp") or e.get("release_timestamp")
        if not e.get("id"):
            continue
        out.append({"id": e["id"], "title": e.get("title") or "",
                    "published": _z(datetime.fromtimestamp(ts, timezone.utc)) if ts else None,
                    "url": "https://www.youtube.com/watch?v=" + e["id"]})
    return out, "yt-dlp", None


def drops_from(uploads: List[dict], dcfg: dict) -> List[dict]:
    """The uploads that are GTA VI drops, grouped: uploads within group_min minutes of the group's first are one drop
    (Rockstar's two to four copies of one trailer). Newest drop first. Each: {id, ids, title, url, published}."""
    film = re.compile(dcfg.get("film_title") or DEFAULTS["film_title"], re.I)
    skip = re.compile(dcfg["skip_title"], re.I) if dcfg.get("skip_title") else None
    rows = sorted((u for u in uploads if u.get("published") and film.search(u.get("title") or "")
                   and not (skip and skip.search(u.get("title") or ""))), key=lambda u: u["published"])
    groups: List[List[dict]] = []
    gap = timedelta(minutes=float(dcfg.get("group_min", 15)))
    for u in rows:
        if groups and _utc(u["published"]) - _utc(groups[-1][0]["published"]) <= gap:
            groups[-1].append(u)
        else:
            groups.append([u])
    out = [{"id": g[0]["id"], "ids": [u["id"] for u in g], "title": g[0]["title"], "url": g[0]["url"],
            "published": g[0]["published"]} for g in groups]
    return sorted(out, key=lambda d: d["published"], reverse=True)


def open_drops(drops: List[dict], dcfg: dict, now: datetime) -> List[dict]:
    """The drops still inside their horizon (horizon_h after the upload, at least window_h)."""
    h = max(float(dcfg.get("horizon_h", 12)), float(dcfg.get("window_h", 3)))
    return [d for d in drops if timedelta(0) <= now - _utc(d["published"]) <= timedelta(hours=h)]


# ------------------------------------------------------------------------------------------------- 2. windows
def roster(dcfg: dict, scfg: Optional[dict] = None) -> List[dict]:
    """drop.streamers with each face's weight (scout.json `people`, 0.3 when unnamed there) and its handles."""
    people = {k.lower(): float(v) for k, v in ((scfg or {}).get("people") or {}).items()}
    out = []
    for s in dcfg.get("streamers") or []:
        name = s.get("name") or s.get("twitch") or s.get("kick")
        if not name or s.get("tile") is False:
            continue
        w = s.get("weight")
        if w is None:
            w = people.get(name.lower(), 0.3)
        out.append({"name": name, "twitch": (s.get("twitch") or "").lower() or None,
                    "kick": (s.get("kick") or "").lower() or None, "weight": float(w)})
    return out


def face_window(face_state: dict, stream: Optional[dict], drop_at: datetime, dcfg: dict,
                now: datetime, plat: Optional[str] = None) -> Optional[dict]:
    """The face's current window for this drop: live at the upload -> [upload, upload + window_h]; went live after it
    (before horizon_h) -> [stream start, + face_stream_min]. A face not heard yet that starts ANOTHER stream inside
    the horizon gets a new window for it (Kai on 10-15: live at the upload in a stream that ended minutes later, his
    reaction 5.6 h on, in the next one). A stream on the OTHER platform opens a new window only once the current one
    has ended: xQc streams to Twitch and Kick at once, and the second feed is the same stream, not a new one. None
    while no window has opened."""
    cur = face_state.get("window")
    if not stream or not stream.get("started_at"):
        return cur
    st = _utc(stream["started_at"])
    horizon = drop_at + timedelta(hours=float(dcfg.get("horizon_h", 12)))
    if cur:
        had = _utc((face_state.get("stream") or {}).get("started_at"))
        if face_state.get("found") or not had or st <= had + timedelta(minutes=10) or st > min(horizon, now):
            return cur
        if plat and face_state.get("platform") and plat != face_state["platform"] and st < _utc(cur["until"]):
            return cur
        return {"from": _z(st), "until": _z(st + timedelta(minutes=float(dcfg.get("face_stream_min", 90)))),
                "via": f"went live again {_z(st)}", "stream": stream.get("title", "")[:120]}
    if st <= drop_at:
        return {"from": _z(drop_at), "until": _z(drop_at + timedelta(hours=float(dcfg.get("window_h", 3)))),
                "via": "live at the upload", "stream": stream.get("title", "")[:120]}
    if st <= min(horizon, now):
        return {"from": _z(st), "until": _z(st + timedelta(minutes=float(dcfg.get("face_stream_min", 90)))),
                "via": f"went live {_z(st)}", "stream": stream.get("title", "")[:120]}
    return None


# ------------------------------------------------------------------------------------------------- 3. the clips
def leak(title: str, dcfg: dict, day: date) -> bool:
    """Before street_date, a title about a leak / an early copy / a street-date break is never kept."""
    if day.isoformat() >= str(dcfg.get("street_date") or "9999"):
        return False
    return bool(re.search(dcfg.get("leak_titles") or DEFAULTS["leak_titles"], title or "", re.I))


def early_copy(game: str, dcfg: dict, day: date) -> bool:
    """Before street_date, a clip in a GTA VI category is someone playing an early copy."""
    return day.isoformat() < str(dcfg.get("street_date") or "9999") and bool(
        re.search(r"grand theft auto (vi|6)|gta ?(vi|6)", game or "", re.I))


def keep_clip(c: dict, win, dcfg: dict, drop_at: datetime) -> Optional[str]:
    """Why a clip is NOT kept, or None to keep it. `win`: the face's window, or a list of its windows."""
    t = _utc(c.get("created_at"))
    if t is None:
        return "no time"
    if t < drop_at:
        return "made before the drop"
    wins = win if isinstance(win, list) else [win]
    if not any(_utc(w["from"]) - timedelta(minutes=2) <= t <= _utc(w["until"]) + timedelta(minutes=2) for w in wins):
        return "outside the face's window"
    day = t.astimezone(timezone.utc).date()
    if leak(c.get("title", ""), dcfg, day) or leak(c.get("vod_title", ""), dcfg, day):
        return "leak rule"
    if early_copy(c.get("game", ""), dcfg, day):
        return "early copy (GTA VI category before the street date)"
    return None


def relevance(c: dict, dcfg: dict, stream_title: str = "") -> float:
    """x1.5 when a title (clip, VOD, stream) names GTA or the trailer; x0.3 in another game's category; verify
    decides above both (score)."""
    topic = re.compile(dcfg.get("topic") or DEFAULTS["topic"], re.I)
    f = 1.0
    if any(topic.search(x or "") for x in (c.get("title"), c.get("vod_title"), stream_title)):
        f *= float(dcfg.get("topic_factor", 1.5))
    if not re.match(dcfg.get("neutral_games") or DEFAULTS["neutral_games"], (c.get("game") or "").strip(), re.I):
        f *= float(dcfg.get("other_game_factor", 0.3))
    return f


def vpm(c: dict, now: datetime, min_age: float = 3.0) -> float:
    """Views a minute since the clip was made (a clip younger than min_age minutes counts min_age)."""
    t = _utc(c.get("created_at"))
    if t is None:
        return 0.0
    age = max((now - t).total_seconds() / 60.0, min_age)
    return float(c.get("views") or 0) / age


def score(c: dict, weight: float, dcfg: dict, now: datetime, stream_title: str = "") -> float:
    v = c.get("verify") or {}
    if v.get("holds") is False:
        return 0.0
    s = math.log10(1.0 + vpm(c, now, float(dcfg.get("min_age_min", 3.0)))) * (1.0 + weight) * relevance(c, dcfg, stream_title)
    if v.get("holds") is True:
        s *= 3.0
    return round(s, 4)


def merge_clips(state: dict, face: dict, rows: List[dict], dcfg: dict, now: datetime) -> int:
    """Fold one face's fetched clips into the drop: new ones kept (keep_clip), known ones re-counted (views +
    seen). Returns how many were new."""
    clips = state.setdefault("clips", {})
    fs = state["faces"][face["name"]]
    win = list(fs.get("windows") or []) + [fs["window"]]
    drop_at = _utc(state["film"]["published"])
    n = 0
    for c in rows:
        key = f"{c.get('platform')}:{c.get('id')}"
        if key in clips:
            clips[key]["views"] = max(float(clips[key].get("views") or 0), float(c.get("views") or 0))
            seen = clips[key].setdefault("seen", [])
            seen.append([_z(now), c.get("views") or 0])
            del seen[:-SEEN_KEEP]
            continue
        why = keep_clip(c, win, dcfg, drop_at)
        if why:
            continue
        row = {k: c.get(k) for k in ("platform", "id", "url", "title", "views", "created_at", "duration", "game",
                                     "broadcaster", "broadcaster_name", "vod_id", "vod_offset", "vod_title")}
        row.update(face=face["name"], first_seen=_z(now), seen=[[_z(now), int(c.get("views") or 0)]])
        clips[key] = row
        n += 1
    return n


def ranked(state: dict, faces: List[dict], dcfg: dict, now: datetime) -> List[dict]:
    """Every kept clip with its score, best first (score 0 = verify found no film in it)."""
    w = {f["name"]: f["weight"] for f in faces}
    out = []
    for key, c in (state.get("clips") or {}).items():
        st = ((state.get("faces") or {}).get(c.get("face")) or {}).get("window") or {}
        c = dict(c, key=key, vpm=round(vpm(c, now, float(dcfg.get("min_age_min", 3.0))), 2))
        c["score"] = score(c, w.get(c.get("face"), 0.3), dcfg, now, st.get("stream", ""))
        out.append(c)
    return sorted(out, key=lambda c: (-c["score"], -(c.get("views") or 0)))


def candidate(rk: List[dict], dcfg: dict, verified_only: bool, found: Optional[Dict[str, dict]] = None,
              weights: Optional[Dict[str, float]] = None) -> dict:
    """The faces, each with its best per_face clips and, when listening found it, its VOD's first viewing; ready at
    min_faces. A face is verified when its VOD holds the film (found) or one of its clips does; verified faces lead,
    the biggest names first (scout.json people: the names the audience searches), then the crowd's clips.
    With verify or listening on, only verified faces count toward ready (the others still show, marked)."""
    per = int(dcfg.get("per_face", 2))
    found = found or {}
    faces: Dict[str, List[dict]] = {}
    for c in rk:
        if (c.get("verify") or {}).get("holds") is False:      # the film is not in it: not a reaction to this drop
            continue
        faces.setdefault(c["face"], [])
        if len(faces[c["face"]]) < per:
            faces[c["face"]].append(c)
    for name in found:
        faces.setdefault(name, [])
    verified = {f for f, cs in faces.items() if f in found or any((c.get("verify") or {}).get("holds") for c in cs)}
    best = {f: max([c.get("score") or 0 for c in cs] or [0]) for f, cs in faces.items()}
    w = weights or {}
    order = sorted(faces, key=lambda f: (f not in verified, -(w.get(f, 0.3) if f in verified else 0), -best[f]))
    order = order[: int(dcfg.get("max_faces", 6))]
    counted = [f for f in order if not verified_only or f in verified]
    return {"faces": [{"name": f, "clips": faces[f], "vod": found.get(f)} for f in order], "counted": counted,
            "ready": len(counted) >= int(dcfg.get("min_faces", 3)), "verified_only": verified_only}


def moment(rk: List[dict], found: Optional[Dict[str, dict]] = None) -> Optional[dict]:
    """Where the faces overlap on the film (their VOD's first viewing, or a verified clip): the longest stretch of
    film seconds shown by the most faces, {film_s (its first second), span [first, last], faces}. The REACTIONS
    route picks its beat inside the span (a beat outside it is missing a face)."""
    cover: Dict[int, set] = {}
    pairs = [(c["face"], (c.get("verify") or {}).get("runs") or []) for c in rk]
    pairs += [(name, (f or {}).get("runs") or []) for name, f in (found or {}).items()]
    for face, runs in pairs:
        for r in runs:
            for s in range(max(0, int(math.ceil(r["film0"]))), int(r["film1"]) + 1):
                cover.setdefault(s, set()).add(face)
    if not cover:
        return None
    top = max(len(v) for v in cover.values())
    runs_: List[List[int]] = []
    for sec in sorted(s for s, v in cover.items() if len(v) == top):
        if runs_ and sec == runs_[-1][-1] + 1 and cover[sec] == cover[runs_[-1][0]]:
            runs_[-1].append(sec)
        else:
            runs_.append([sec])
    best = max(runs_, key=lambda r: (len(r), -r[0]))
    return {"film_s": best[0], "span": [best[0], best[-1]], "faces": sorted(cover[best[0]])}


# ------------------------------------------------------------------------------------------------- the skill's tools
def skill_script(acct: C.Account, name: str) -> Path:
    return acct.skill_dir / "scripts" / name


def streamclips(acct: C.Account, args: List[str], timeout: int = 120) -> dict:
    """The skill's scripts/streamclips.py (stdlib: Twitch GQL + Kick) as JSON; {"error": ...} when it is missing
    or failed."""
    from . import makers as M
    p = skill_script(acct, "streamclips.py")
    if not p.exists():
        return {"error": f"{p} is missing (the gaming skill's drop-mode reader is not installed)"}
    try:
        r = subprocess.run([M.PY_SKILLS, str(p)] + args + ["--json", "-"], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL, cwd=str(p.parent))
        return json.loads(r.stdout or "{}") if r.returncode == 0 else {"error": (r.stderr or "")[-300:]}
    except (subprocess.TimeoutExpired, OSError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def fetch_roster(acct: C.Account, faces: List[dict], since: str) -> dict:
    tw = [f["twitch"] for f in faces if f.get("twitch")]
    kk = [f["kick"] for f in faces if f.get("kick")]
    args = ["roster", "--period", "LAST_DAY", "--since", since, "--vods"]   # --vods: a live stream's opening clips too
    if tw:
        args += ["--twitch", ",".join(tw)]
    if kk:
        args += ["--kick", ",".join(kk)]
    return streamclips(acct, args)


def fetch_live(acct: C.Account, faces: List[dict]) -> dict:
    tw = [f["twitch"] for f in faces if f.get("twitch")]
    kk = [f["kick"] for f in faces if f.get("kick")]
    args = ["live"] + (["--twitch", ",".join(tw)] if tw else []) + (["--kick", ",".join(kk)] if kk else [])
    return streamclips(acct, args, timeout=90)


def _ytdlp() -> str:
    from . import scout as SC
    return SC._ytdlp()


def film_audio(state: dict, film_dir: Path) -> Tuple[Optional[Path], Optional[str]]:
    """The drop's own sound (Rockstar's upload, audio only), fetched once per drop: one YouTube download."""
    film_dir.mkdir(parents=True, exist_ok=True)
    film = next(iter(sorted(film_dir.glob("film.*"))), None)
    if film is not None:
        return film, None
    r = subprocess.run([_ytdlp(), "-q", "--no-warnings", "--no-playlist", "-f", "bestaudio[ext=m4a]/bestaudio",
                        "-o", str(film_dir / "film.%(ext)s"), state["film"]["url"]],
                       capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    film = next(iter(sorted(film_dir.glob("film.*"))), None)
    return (film, None) if film else (None, f"the film's sound did not download ({(r.stderr or '').strip()[-160:]})")


def filmmatch(acct: C.Account, film: Path, srcs: List[str], out: Path, cmd: str = "holds") -> List[dict]:
    """The skill's filmmatch.py over some files: one result per file ({src, holds, runs, events, ...})."""
    from . import makers as M
    fm = skill_script(acct, "filmmatch.py")
    subprocess.run([M.PY_SKILLS, str(fm), cmd, "--film", str(film), "--json", str(out)] + list(srcs),
                   capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL, cwd=str(fm.parent))
    return _read(out, {}).get("reactors") or []


def verify_clips(acct: C.Account, state: dict, rk: List[dict], dcfg: dict, film_dir: Path) -> List[str]:
    """Fetch the top unchecked clips small and ask the skill's filmmatch.py whether the drop's sound is in them.
    The film's audio is fetched once per drop. Returns lines for the run's report."""
    said: List[str] = []
    fm = skill_script(acct, "filmmatch.py")
    if not fm.exists():
        return [f"verify: {fm} is missing; clips ranked unverified"]
    film, err = film_audio(state, film_dir)
    if film is None:
        return [f"verify: {err}; clips unverified"]
    done = sum(1 for c in (state.get("clips") or {}).values() if c.get("verify"))
    todo = [c for c in rk if not c.get("verify") and c.get("url")][: int(dcfg.get("verify_max", 8))]
    todo = todo[: max(0, int(dcfg.get("verify_total", 60)) - done)]
    if not todo:
        return said
    cdir = film_dir / "clips"
    cdir.mkdir(exist_ok=True)
    files = {}
    for c in todo:
        out = cdir / f"{c['platform']}-{re.sub(r'[^A-Za-z0-9_-]+', '', str(c['id']))[:60]}.mp4"
        if not out.exists():
            subprocess.run([_ytdlp(), "-q", "--no-warnings", "--no-playlist", "-f", "worst[height>=360]/worst",
                            "-o", str(out), c["url"]], capture_output=True, text=True, timeout=180,
                           stdin=subprocess.DEVNULL)
        if out.exists() and out.stat().st_size > 0:
            files[str(out)] = c["key"]
        else:
            state["clips"][c["key"]]["verify"] = {"holds": None, "error": "did not download"}
    if not files:
        return said + ["verify: no clip downloaded"]
    res = filmmatch(acct, film, list(files), film_dir / "verify-last.json")
    for m in res:
        key = files.get(m.get("src"))
        if key:
            state["clips"][key]["verify"] = {"holds": bool(m.get("holds")), "runs": m.get("runs") or [],
                                             "error": m.get("error")}
    n_yes = sum(1 for m in res if m.get("holds"))
    said.append(f"verify: {len(files)} clip(s) checked against the film, {n_yes} hold it")
    return said


# ------------------------------------------------------------------------------------------------- 3b. listening
def listen_plan(fs: dict, dcfg: dict, now: datetime) -> Optional[Tuple[float, float]]:
    """The next stretch of the face's stream to listen to, in seconds on its VOD's clock: from where the last one
    ended (10 s back, so a first viewing across the seam is whole) to the live edge (listen_lag_s behind it), never
    outside the face's window, at most listen_chunk_min minutes. None when there is nothing to do yet: found, no
    VOD, the window not open, done, or under a minute new while the window is still open."""
    st, win = fs.get("stream") or {}, fs.get("window") or {}
    if fs.get("found") or not st.get("vod_id") or not st.get("started_at") or not win:
        return None
    s0 = _utc(st["started_at"])
    w0 = (_utc(win["from"]) - s0).total_seconds()
    w1 = (_utc(win["until"]) - s0).total_seconds()
    done = float((fs.get("listen") or {}).get("to") or w0)
    if done >= w1:
        return None
    lo = max(w0, done - 10.0)
    edge = (now - s0).total_seconds() - float(dcfg.get("listen_lag_s", 30))
    hi = min(edge, w1, lo + 60.0 * float(dcfg.get("listen_chunk_min", 15)))
    if hi - lo < 60.0 and edge < w1:
        return None
    return (round(lo, 1), round(hi, 1)) if hi > lo else None


def found_from(res: dict, vod_id: str, lo: float, now: datetime) -> Optional[dict]:
    """filmmatch's map of one stretch (its clock starts at lo) -> the face's first viewing on the VOD's clock."""
    runs = res.get("runs") or []
    if not res.get("holds") or not runs:
        return None
    vr = [dict(r, t0=round(r["t0"] + lo, 2), t1=round(r["t1"] + lo, 2), offset=round(r["offset"] + lo, 2)) for r in runs]
    at = vr[0]["offset"]                      # film 0 on the VOD clock, by the first run (the first viewing)
    return {"vod_id": vod_id, "url": f"https://www.twitch.tv/videos/{vod_id}", "film0_at": round(at, 2),
            "hms": f"{int(at // 3600)}:{int(at % 3600 // 60):02d}:{int(at % 60):02d}", "runs": vr,
            "events": [dict(e, at=round(e["at"] + lo, 2)) for e in res.get("events") or []],
            "film_covered_s": res.get("film_covered_s"), "found_at": _z(now)}


def listen(acct: C.Account, state: dict, dcfg: dict, now: datetime, film_dir: Path) -> List[str]:
    """For the faces whose window is open on Twitch: fetch the newest stretch of the stream's VOD as audio only
    (yt-dlp --download-sections, no login) and find the drop's sound in it. A hit is the face's first viewing to
    the second, pauses and rewinds included: what the REACTIONS cut takes. This does not wait for a viewer to clip
    the moment (on 10-15 nobody clipped Joe Bartolozzi's or Speed's, and Kai's one clip of it had 2 views). A VOD
    that is subscriber-only or switched off says so once and the face falls back to its clips."""
    said: List[str] = []
    todo = []
    for name, fs in (state.get("faces") or {}).items():
        if fs.get("platform") != "twitch" or (fs.get("listen") or {}).get("error"):
            continue
        plan = listen_plan(fs, dcfg, now)
        if plan:
            todo.append((float((fs.get("listen") or {}).get("to") or 0), name, fs, plan))
    if not todo:
        return said
    film, err = film_audio(state, film_dir)
    if film is None:
        return [f"listen: {err}"]
    vdir = film_dir / "vod"
    vdir.mkdir(parents=True, exist_ok=True)
    for _, name, fs, (lo, hi) in sorted(todo)[: int(dcfg.get("listen_faces", 6))]:
        vid = str(fs["stream"]["vod_id"])
        out = vdir / f"{re.sub(r'[^A-Za-z0-9_-]+', '', name)}-{vid}-{int(lo)}.mp4"
        # exact cuts: a stretch asked from 6:00 (a segment's edge) came back from 5:50 on 10-19, which put Kai's
        # first viewing 10 s late; with --force-keyframes-at-cuts the stretch starts where it was asked (audio only:
        # the re-encode is cheap)
        r = subprocess.run([_ytdlp(), "-q", "--no-warnings", "-f", "Audio_Only/audio_only", "--download-sections",
                            f"*{lo:.1f}-{hi:.1f}", "--force-keyframes-at-cuts", "-o", str(out),
                            f"https://www.twitch.tv/videos/{vid}"],
                           capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        ls = fs.setdefault("listen", {})
        if not out.exists() or out.stat().st_size == 0:
            ls["error"] = ((r.stderr or "").strip().splitlines() or ["no audio"])[-1][:200]
            said.append(f"listen: {name}'s VOD {vid} did not answer ({ls['error']}); its clips only")
            continue
        res = (filmmatch(acct, film, [str(out)], vdir / "listen-last.json", cmd="map") or [{}])[0]
        try:
            out.unlink()
        except OSError:
            pass
        ls.update(to=hi, at=_z(now), stretches=int(ls.get("stretches") or 0) + 1)
        f = found_from(res, vid, lo, now)
        if f:
            fs["found"] = f
            said.append(f"listen: {name} watched it at VOD {f['hms']} (film {span(f['runs'])}, "
                        f"{len(f['events'])} pause/rewind)")
    return said


# ------------------------------------------------------------------------------------------------- 4. the inbox
def reactions_day(acct: C.Account, cfg: Optional[C.Config], now: datetime) -> Optional[str]:
    """The first day (today, then tomorrow) with a REACTIONS slot free of a REACTIONS clip; today only while its
    slot is 3.5 hours away or more (2 to cut, place's 90 minutes)."""
    from . import makers as M
    for k in (0, 1):
        day = (now + timedelta(days=k)).date()
        slots = M.reactions_slots(acct, day)
        if not slots:
            continue
        board = _read(C.STATE / acct.slug / f"{day.isoformat()}.json", {})
        rows = board.get("slots") or {}
        rows = list(rows.values()) if isinstance(rows, dict) else rows
        taken = sum(1 for s in rows if isinstance(s, dict) and s.get("format") == "reactions"
                    and s.get("status") in ("ready", "approved", "scheduled", "posted", "making"))
        if taken >= len(slots):
            continue
        if k == 0 and cfg is not None:
            first = min(cfg.slot_time(day, s) for s in slots)
            if first - now.astimezone(first.tzinfo) < timedelta(hours=3.5):
                continue
        return day.isoformat()
    return None


def span(runs: List[dict]) -> str:
    """The film seconds some runs cover, as one span (a rewatch from the start does not hide the end)."""
    return f"{min(r['film0'] for r in runs):.0f}-{max(r['film1'] for r in runs):.0f} s" if runs else "-"


def vod_at(c: dict) -> Optional[dict]:
    """Where the film starts on the streamer's own VOD, from a verified Twitch clip: the clip starts at vod_offset on
    the VOD's clock and plays film second f at clip second f + offset, so film 0 sits at vod_offset + offset (the
    first run: the first viewing). The REACTIONS route pulls that stretch of the VOD (yt-dlp --download-sections),
    not the 30 s clip. None for Kick (its clips carry no VOD offset) or an unverified clip. APPROXIMATE: Twitch's
    clip offsets ran 30 to 60 s late against the stream's own audio on 10-15 (Kai's clip said 8:41, his stream 8:11),
    so a face heard by listening keeps the heard time; this is the fallback."""
    runs = (c.get("verify") or {}).get("runs") or []
    if c.get("platform") != "twitch" or not runs or c.get("vod_offset") is None or not c.get("vod_id"):
        return None
    r = runs[0]
    off = r.get("offset")
    if off is None and r.get("t0") is not None and r.get("film0") is not None:
        off = float(r["t0"]) - float(r["film0"])
    if off is None:
        return None
    at = float(c["vod_offset"]) + float(off)
    return {"id": str(c["vod_id"]), "url": f"https://www.twitch.tv/videos/{c['vod_id']}", "film0_at": round(at, 2),
            "hms": f"{int(at // 3600)}:{int(at % 3600 // 60):02d}:{int(at % 60):02d}"}


def inbox_row(state: dict, cand: dict, mom: Optional[dict], day: Optional[str], now: datetime) -> dict:
    film = state["film"]
    faces = []
    for f in cand["faces"]:
        faces.append({"name": f["name"], "vod": (f.get("vod") and {k: f["vod"][k] for k in (
            "vod_id", "url", "film0_at", "hms", "runs", "events") if k in f["vod"]}), "clips": [
            {"url": c["url"], "platform": c["platform"], "title": c.get("title"), "views": c.get("views"),
             "vpm": c.get("vpm"), "created_at": c.get("created_at"), "game": c.get("game"),
             "film_holds": (c.get("verify") or {}).get("holds"),
             "film": [[r["film0"], r["film1"]] for r in ((c.get("verify") or {}).get("runs") or [])],
             "vod": vod_at(c)}
            for c in f["clips"]]})
    names = [f["name"] for f in cand["faces"]]
    return {"channel": "rockstar-drop", "name": "REACTIONS: " + film["title"], "platform": "multi",
            "kind": "reactions", "status": "reactions", "found_by": "drop",
            "title": f"REACTIONS: {film['title']} ({', '.join(names)})",
            "film": {"id": film["id"], "url": film["url"], "title": film["title"], "published": film["published"]},
            "faces": faces, "names": names, "moment": mom, "for_day": day,
            "ready": cand["ready"], "verified": cand["verified_only"], "ready_at": state.get("ready_at"),
            "updated": _z(now), "seen": _z(now),
            "how": ("the REACTIONS route (gaming skill): fetch each clip at best quality, filmmatch.py map for "
                    "each reactor's first viewing, every streamer checked on frames, never two REACTIONS clips on "
                    "one day; leaks before 2026-11-19 are never cut")}


def write_inbox(acct: C.Account, key: str, row: dict) -> Optional[str]:
    """Upsert the candidate row (temp file + rename, the way the scout and the maker write). The error, or None."""
    p = acct.account_dir / "Publish" / "inbox.json"
    try:
        data = json.loads(p.read_text()) if p.exists() else {"items": {}}
    except (OSError, ValueError) as e:
        return f"inbox not read ({type(e).__name__})"
    items = data.setdefault("items", {})
    if not isinstance(items, dict):
        return "inbox items is not a table"
    was = items.get(key) if isinstance(items.get(key), dict) else {}
    if was.get("status") not in (None, "reactions"):
        return f"inbox row {key} is {was.get('status')!r} (taken by hand): left alone"
    items[key] = row
    tmp = p.with_suffix(".json.drop-tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(p)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        return f"inbox not written ({e})"
    return None


def notify(title: str, body: str) -> None:
    try:
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(body[:220])} with title "
                        f"{json.dumps(title)}"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ------------------------------------------------------------------------------------------------- 5. the GTA-wide feed
def feed_rank(games: List[dict], roster_handles: set, floor: int, top: int, now: datetime,
              followers: Optional[Dict[str, int]] = None, languages: Optional[List[str]] = None) -> List[dict]:
    """Faces beyond the roster: per streamer (platform:login) the best clip by views a minute, streamers with
    follower_floor followers or more (Kick clips carry no follower count: `followers` fills it), in `languages`
    when the platform says (Twitch does; REACTIONS tiles are English speakers), best first."""
    followers = followers or {}
    langs = {x.lower() for x in (languages or [])}
    best: Dict[str, dict] = {}
    for g in games:
        for c in g.get("clips") or []:
            h = f"{c.get('platform')}:{c.get('broadcaster')}"
            if not c.get("broadcaster") or h in roster_handles:
                continue
            if langs and c.get("language") and c["language"].lower() not in langs:
                continue
            fol = c.get("followers")
            if fol is None:
                fol = followers.get(h)
            if fol is None or int(fol) < floor:
                continue
            row = dict(c, followers=int(fol), handle=h, vpm=round(vpm(c, now), 2), category=g.get("game"))
            if h not in best or row["vpm"] > best[h]["vpm"]:
                best[h] = row
    return sorted(best.values(), key=lambda r: -r["vpm"])[:top]


def run_feed(acct: C.Account, dcfg: dict, faces: List[dict], now: datetime, force: bool = False) -> List[str]:
    fcfg = dcfg.get("feed") or {}
    p = drop_dir(acct) / "feed.json"
    old = _read(p, {})
    every = timedelta(minutes=float(fcfg.get("every_min", 60)))
    if not force and old.get("at") and now - _utc(old["at"]) < every:
        return []
    games = list(fcfg.get("twitch_games") or [])
    cat = _read(drop_dir(acct) / "category.json", {})
    if cat.get("checked") != now.date().isoformat():          # GTA VI's category, looked up once a day
        found = {}
        for name in fcfg.get("watch_for") or []:
            r = streamclips(acct, ["category", name])
            if isinstance(r, dict) and r.get("id"):
                found[name] = r["id"]
        cat = {"checked": now.date().isoformat(), "found": found}
        _write(drop_dir(acct) / "category.json", cat)
    games += [g for g in (cat.get("found") or {}) if g not in games]
    args = ["game", "--period", "LAST_DAY"]
    if games:
        args += ["--twitch-game", ",".join(games)]
    if fcfg.get("kick_categories"):
        args += ["--kick-category", ",".join(fcfg["kick_categories"])]
    res = streamclips(acct, args)
    if res.get("error"):
        _write(p, dict(old, error=res["error"], tried=_z(now)))
        return [f"feed: {res['error'][:160]}"]
    handles = {f"twitch:{f['twitch']}" for f in faces if f.get("twitch")} | {f"kick:{f['kick']}" for f in faces
                                                                           if f.get("kick")}
    allg = list(res.get("twitch") or []) + list(res.get("kick") or [])
    floor = int(fcfg.get("follower_floor", 250000))
    kfol = dict(old.get("kick_followers") or {})
    unknown = sorted({c["broadcaster"] for g in res.get("kick") or [] for c in g.get("clips") or []
                      if c.get("broadcaster") and f"kick:{c['broadcaster']}" not in kfol
                      and f"kick:{c['broadcaster']}" not in handles}, key=lambda b: 0)[:10]
    if unknown:                                              # Kick clips carry no follower count: ask for 10 a run
        r = streamclips(acct, ["live", "--kick", ",".join(unknown)])
        for slug, row in ((r.get("kick") or {}) if isinstance(r, dict) else {}).items():
            if row.get("followers") is not None:
                kfol[f"kick:{slug}"] = int(row["followers"])
    sug = feed_rank(allg, handles, floor, int(fcfg.get("top", 12)), now, kfol, fcfg.get("languages") or ["en"])
    errs = [g.get("error") for g in allg if g.get("error")]
    _write(p, {"at": _z(now), "games": [g.get("game") for g in allg], "floor": floor, "suggest": sug,
               "kick_followers": kfol, "errors": errs, "categories": cat.get("found") or {}})
    return [f"feed: {len(sug)} face(s) beyond the roster at {floor:,}+ followers from {', '.join(g.get('game') or '?' for g in allg)}"
            + (f" ({len(errs)} source(s) did not answer)" if errs else "")]


# ------------------------------------------------------------------------------------------------- the run
def tick(acct: C.Account, state: dict, faces: List[dict], dcfg: dict, now: datetime, fetched: dict) -> List[str]:
    """One pass over one open drop, given the fetched roster (streamclips roster JSON): windows, clips, nothing
    else (no network, no files): the live run and the backtest share it."""
    said: List[str] = []
    drop_at = _utc(state["film"]["published"])
    fstate = state.setdefault("faces", {})
    for f in faces:
        fs = fstate.setdefault(f["name"], {})
        rows: List[dict] = []
        for plat in ("twitch", "kick"):
            h = f.get(plat)
            r = ((fetched.get(plat) or {}).get(h) or {}) if h else {}
            if not r:
                continue
            if r.get("error"):
                fs.setdefault("errors", {})[plat] = r["error"][:160]
            win = face_window(fs, r.get("stream"), drop_at, dcfg, now, plat)
            if win and win is not fs.get("window"):
                if fs.get("window"):
                    fs.setdefault("windows", []).append(fs["window"])     # an earlier stream's window: kept
                    fs.pop("listen", None)
                fs["window"] = win
                fs["platform"] = plat
                st = r.get("stream") or {}
                fs["stream"] = {"vod_id": st.get("vod_id"), "started_at": st.get("started_at"), "title": st.get("title")}
                said.append(f"{f['name']}: window {win['from'][11:16]}-{win['until'][11:16]}Z ({win['via']}, {plat})")
            rows += r.get("clips") or []
        if fs.get("window"):
            merge_clips(state, f, rows, dcfg, now)
    return said


def run(cfg: C.Config, acct: C.Account, quiet: bool = False, now: Optional[datetime] = None) -> List[str]:
    """One drop-mode run: the watch, then each open drop, then the feed. One run at a time (a lock file): a run that
    listens to six streams can outlast the 5 minutes, and a second one would fetch the same stretches."""
    import fcntl
    dcfg = settings(acct.slug)
    if not dcfg:
        return [] if quiet else [f"{acct.name}: drop mode is off (no scout.json {acct.slug}.drop, or on: false)"]
    d = drop_dir(acct)
    with open(d / "run.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return [] if quiet else ["drop mode: the last run is still going; this one stands down"]
        return _run(cfg, acct, dcfg, quiet, now)


def _run(cfg: C.Config, acct: C.Account, dcfg: dict, quiet: bool, now: Optional[datetime]) -> List[str]:
    from . import scout as SC
    now = now or datetime.now(timezone.utc)
    lines: List[str] = []
    d = drop_dir(acct)
    watch = _read(d / "watch.json", {"seen": {}})
    last_fb = _utc(watch.get("fallback_at"))
    ups, how, err = rockstar_uploads(dcfg, allow_fallback=not last_fb or now - last_fb >= timedelta(minutes=15))
    if how == "yt-dlp":
        watch["fallback_at"] = _z(now)
    seen = watch.setdefault("seen", {})
    for u in ups:
        seen.setdefault(u["id"], {"title": u["title"], "published": u["published"], "first_seen": _z(now)})
    watch.update(checked=_z(now), how=how, error=err)
    _write(d / "watch.json", watch)
    if err:
        lines.append(f"rockstar: {err}")
    drops = open_drops(drops_from([dict(v, id=k, url="https://www.youtube.com/watch?v=" + k) for k, v in seen.items()],
                                  dcfg), dcfg, now)
    scfg = SC.load(acct.slug)
    faces = roster(dcfg, scfg)
    for dr in drops:
        p = d / f"{dr['id']}.json"
        state = _read(p, {}) or {"film": dr, "opened": _z(now), "faces": {}, "clips": {}}
        state["film"].update({k: dr[k] for k in ("ids", "title", "url", "published")})
        if not state.get("faces") and not quiet:
            lines.append(f"DROP: {dr['title']} (uploaded {dr['published']}): drop mode open for {dcfg['horizon_h']:g} h")
        r = fetch_roster(acct, faces, dr["published"])
        if r.get("error"):
            lines.append(f"roster: {r['error'][:200]}")
            r = {}
        lines += tick(acct, state, faces, dcfg, now, r)
        if dcfg.get("listen"):
            lines += listen(acct, state, dcfg, now, d / dr["id"])
        rk = ranked(state, faces, dcfg, now)
        if dcfg.get("verify") and rk:
            lines += verify_clips(acct, state, [c for c in rk if c["score"] > 0], dcfg, d / dr["id"])
            rk = ranked(state, faces, dcfg, now)
        found = {n: v["found"] for n, v in state["faces"].items() if v.get("found")}
        verified_only = bool(found) or (bool(dcfg.get("verify"))
                                        and any((c.get("verify") or {}).get("holds") is not None for c in rk))
        cand = candidate(rk, dcfg, verified_only, found, {f["name"]: f["weight"] for f in faces})
        mom = moment(rk, found)
        if cand["ready"] and not state.get("ready_at"):
            state["ready_at"] = _z(now)
            if dcfg.get("notify"):
                notify("Gaming drop mode", f"{dr['title']}: {len(cand['counted'])} faces reacting "
                       f"({', '.join(cand['counted'][:4])}). The REACTIONS candidate is in the inbox: run the "
                       f"Gaming REACTIONS task now to cut it.")
        state["updated"] = _z(now)
        state["candidate"] = {"ready": cand["ready"], "faces": [f["name"] for f in cand["faces"]],
                              "counted": cand["counted"], "moment": mom}
        _write(p, state)
        if cand["faces"]:
            e = write_inbox(acct, "drop-" + dr["id"], inbox_row(state, cand, mom, reactions_day(acct, cfg, now), now))
            if e:
                lines.append(f"inbox: {e}")
        lines.append(f"{dr['title']}: {len(state['clips'])} clip(s) kept from "
                     f"{sum(1 for v in state['faces'].values() if v.get('window'))} face window(s); candidate "
                     f"{'READY' if cand['ready'] else 'not ready'}: {', '.join(f['name'] for f in cand['faces']) or '-'}"
                     + (f"; the faces overlap on film {mom['span'][0]}-{mom['span'][1]} s ({len(mom['faces'])})"
                        if mom else ""))
    lines += run_feed(acct, dcfg, faces, now, force=bool(drops))
    if drops:
        refresh_board(cfg, acct)
    return lines


def refresh_board(cfg: C.Config, acct: C.Account) -> None:
    """Re-write today's scout board (markdown) so its Drop mode block is current between the hourly scout runs."""
    from . import scout as SC
    try:
        day = cfg.now().date()
        board = SC.read_board(acct, day)
        if board.get("finds") is not None and board.get("account"):
            SC.write_markdown(cfg, acct, board)
    except (OSError, ValueError, KeyError):
        pass


# ------------------------------------------------------------------------------------------------- the board
def board_lines(acct: C.Account, now: Optional[datetime] = None, days: float = 2.0) -> List[str]:
    """The scout board's blocks: each drop of the last `days` days (its faces, windows, ranked clips, the moment),
    then the faces beyond the roster from the GTA-wide feed. [] when drop mode has nothing to say."""
    now = now or datetime.now(timezone.utc)
    d = C.STATE / acct.slug / "drop"
    if not d.is_dir():
        return []
    dcfg = settings(acct.slug) or dict(DEFAULTS)
    try:
        from . import scout as SC
        people = {"people": SC.load(acct.slug).get("people") or {}}
    except (OSError, ValueError, SystemExit):
        people = {}
    out: List[str] = []
    for p in sorted(d.glob("*.json")):
        if p.name in ("watch.json", "feed.json", "category.json"):
            continue
        st = _read(p, {})
        film = st.get("film") or {}
        at = _utc(film.get("published"))
        if not at or now - at > timedelta(days=days):
            continue
        cand = st.get("candidate") or {}
        h = (now - at).total_seconds() / 3600
        open_ = h <= float(dcfg.get("horizon_h", 12))
        out += ["", f"## Drop mode: {film.get('title')} ({'open' if open_ else 'closed'}, uploaded {h:.1f} h ago)", "",
                f"[{film.get('url')}]({film.get('url')}). Candidate "
                + ("**READY** in the inbox (drop-" + str(film.get('id')) + ")" if cand.get("ready") else "not ready yet")
                + (f", since {st['ready_at'][11:16]}Z" if st.get("ready_at") else "")
                + (f". The faces overlap on film {cand['moment']['span'][0]}-{cand['moment']['span'][1]} s "
                   f"({', '.join(cand['moment']['faces'])})" if (cand.get("moment") or {}).get("span") else "") + "."]
        wins = [(n, v["window"]) for n, v in (st.get("faces") or {}).items() if v.get("window")]
        if wins:
            out.append("Windows: " + "; ".join(f"{n} {w['from'][11:16]}-{w['until'][11:16]}Z" for n, w in wins))
        heard = [(n, v["found"]) for n, v in (st.get("faces") or {}).items() if v.get("found")]
        if heard:
            out += ["", "Heard on their streams (the trailer's sound in the VOD; the first viewing):"]
            for n, f in heard:
                out.append(f"- **{n}**: film 0 at [VOD {f['hms']}]({f['url']}?t={int(f['film0_at'])}s), film "
                           f"{span(f['runs'])}, {len(f.get('events') or [])} pause/rewind")
        rk = ranked(st, roster(dcfg, people) or [], dcfg, now)
        if rk:
            out += ["", "| face | clip | views | a min | film | made |", "|---|---|---|---|---|---|"]
            for c in rk[:12]:
                v = c.get("verify") or {}
                film_s = ("yes " + ", ".join(f"{r['film0']:.0f}-{r['film1']:.0f}" for r in v.get("runs") or [])
                          if v.get("holds") else ("no" if v.get("holds") is False else "?"))
                va = vod_at(c)
                if va:
                    film_s += f", film 0 at [VOD {va['hms']}]({va['url']}?t={int(va['film0_at'])}s)"
                title = (c.get("title") or "").replace("|", "/")[:40] or c.get("platform")
                out.append(f"| {c['face']} | [{title}]({c['url']}) | {c.get('views') or 0:,} | {c['vpm']:.1f} | "
                           f"{film_s} | {(c.get('created_at') or '')[11:16]}Z |")
    feed = _read(d / "feed.json", {})
    sug = feed.get("suggest") or []
    if sug:
        out += ["", f"## Faces beyond the roster ({', '.join(x for x in feed.get('games') or [] if x)}, "
                    f"{int(feed.get('floor') or 0):,}+ followers; suggestions only)", "",
                "| face | followers | clip | views | a min |", "|---|---|---|---|---|"]
        for s in sug:
            title = (s.get("title") or "").replace("|", "/")[:40] or "clip"
            out.append(f"| {s.get('broadcaster_name') or s.get('broadcaster')} ({s.get('platform')}) | "
                       f"{s['followers']:,} | [{title}]({s.get('url')}) | {s.get('views') or 0:,} | {s['vpm']:.1f} |")
    return out


# ------------------------------------------------------------------------------------------------- the backtest
def backtest(fx: dict, dcfg: Optional[dict] = None, step_min: float = 5.0, until_h: Optional[float] = None) -> dict:
    """Replay a saved drop offline. `fx`: {film, streamers: [...], twitch: {login: [clip rows]},
    kick: {slug: [rows]}, streams: {"plat:login": [{started_at, ended_at, title, vod_id}]}, verify: {"plat:id":
    {holds, runs}}, listen: {face: {vod_id, scanned_to, found (found_from's shape) or None}}, fetched_at}. A face
    is heard (listening) at the first step after its first viewing has aired plus the live lag and a minute of
    audio, inside its window and the stretch that was scanned. Each step sees the clips made by then (their views as counted at fetched_at: Twitch and
    Kick keep no view history: each step scales them by the clip's age then over its age at fetched_at, i.e. every
    clip ranks at its settled views a minute; a live run's early order can differ), each face's stream as live between its
    started_at and ended_at, and the verify result once a clip is first ranked. Returns the timeline: when each face's
    window opened, when each clip was first pulled, and when the candidate was ready."""
    dcfg = dict(DEFAULTS, **(dcfg or {}))
    film = dict(fx["film"])
    drop_at = _utc(film["published"])
    faces = roster({"streamers": fx.get("streamers") or dcfg.get("streamers") or []}, {"people": fx.get("people") or {}})
    state = {"film": film, "faces": {}, "clips": {}}
    fetched_at = _utc(fx.get("fetched_at"))
    steps = int(float(until_h or dcfg.get("horizon_h", 12)) * 60 / step_min)
    pulled: Dict[str, dict] = {}
    timeline: List[dict] = []
    ready_at = None
    for k in range(steps + 1):
        now = drop_at + timedelta(minutes=step_min * k)
        fetched = {"twitch": {}, "kick": {}}
        counted = {f"{plat}:{c.get('id')}": c.get("views") for plat in ("twitch", "kick")
                   for rows in (fx.get(plat) or {}).values() for c in rows} if k == 0 else counted
        for plat in ("twitch", "kick"):
            for h, rows in (fx.get(plat) or {}).items():
                live = None
                for s in (fx.get("streams") or {}).get(f"{plat}:{h}") or []:
                    if _utc(s["started_at"]) <= now <= _utc(s.get("ended_at") or _z(now)):
                        live = {"started_at": s["started_at"], "title": s.get("title", ""), "vod_id": s.get("vod_id")}
                        break
                vis = []
                for c in rows:
                    t = _utc(c.get("created_at"))
                    if t and t <= now:
                        c2 = dict(c)
                        if fetched_at and fetched_at > t:      # views scaled to the step's age (linear; a guess)
                            c2["views"] = (c.get("views") or 0) * min(1.0, (now - t) / (fetched_at - t))
                        vis.append(c2)
                fetched[plat][h] = {"stream": live, "clips": vis}
        for line in tick(None, state, faces, dcfg, now, fetched):
            timeline.append({"at": _z(now), "event": line})
        for name, fs in state["faces"].items():          # listening, replayed from the scan that was saved
            lr = (fx.get("listen") or {}).get(name) or {}
            f, st, win = lr.get("found"), fs.get("stream") or {}, fs.get("window") or {}
            if fs.get("found") or not f or not win or str(st.get("vod_id")) != str(lr.get("vod_id")):
                continue
            first = float(f["runs"][0]["t0"])
            heard = _utc(st["started_at"]) + timedelta(seconds=first + float(dcfg.get("listen_lag_s", 30)) + 60)
            if first <= float(lr.get("scanned_to") or 0) and heard <= now and heard <= _utc(win["until"]) + \
                    timedelta(minutes=step_min):
                fs["found"] = dict(f, found_at=_z(now))
                timeline.append({"at": _z(now), "event": f"listen: {name} watched it at VOD {f['hms']} "
                                                         f"(film {span(f['runs'])})"})
        for key, c in state["clips"].items():
            if key not in pulled:
                c["verify"] = (fx.get("verify") or {}).get(key)
                pulled[key] = {"at": _z(now), "face": c["face"], "title": c.get("title"), "url": c.get("url"),
                               "created_at": c.get("created_at"), "game": c.get("game"),
                               "film_holds": (c.get("verify") or {}).get("holds"), "views": counted.get(key),
                               "film": [[r["film0"], r["film1"]] for r in ((c.get("verify") or {}).get("runs") or [])],
                               "vod": vod_at(c)}
        rk = ranked(state, faces, dcfg, now)
        found = {n: v["found"] for n, v in state["faces"].items() if v.get("found")}
        cand = candidate(rk, dcfg, verified_only=bool(fx.get("verify") or fx.get("listen")), found=found,
                         weights={f["name"]: f["weight"] for f in faces})
        if cand["ready"] and not ready_at:
            ready_at = _z(now)
            timeline.append({"at": ready_at, "event": "candidate READY: " + ", ".join(
                f"{f['name']} (" + (f"VOD {f['vod']['hms']}" if f.get("vod") else
                                    (f["clips"][0].get("title") or f["clips"][0]["platform"]) if f["clips"] else "?") + ")"
                for f in cand["faces"] if f["name"] in cand["counted"])})
    final = ranked(state, faces, dcfg, drop_at + timedelta(minutes=step_min * steps))
    found = {n: v["found"] for n, v in state["faces"].items() if v.get("found")}
    return {"film": film, "ready_at": ready_at, "timeline": timeline, "pulled": pulled,
            "candidate": candidate(final, dcfg, verified_only=bool(fx.get("verify") or fx.get("listen")), found=found,
                                   weights={f["name"]: f["weight"] for f in faces}),
            "moment": moment(final, found), "heard": found,
            "faces": {n: v.get("window") for n, v in state["faces"].items()},
            "windows": {n: list(v.get("windows") or []) + ([v["window"]] if v.get("window") else [])
                        for n, v in state["faces"].items()}}


def backtest_report(res: dict, title: str = "") -> List[str]:
    film = res["film"]
    at = _utc(film["published"])

    def rel(s):
        return f"+{(_utc(s) - at).total_seconds() / 3600:.2f} h"

    out = [f"# Drop mode backtest: {title or film['title']}", "",
           f"Uploaded {film['published']} ({film['url']}). Candidate ready: "
           + (f"{res['ready_at']} ({rel(res['ready_at'])})" if res["ready_at"] else "never inside the horizon"), "",
           "## Face windows", ""]
    wins = res.get("windows") or {n: [w] for n, w in res["faces"].items() if w}
    for n, ws in sorted(wins.items(), key=lambda kv: (kv[1][0] if kv[1] else {}).get("from") or "9"):
        out.append(f"- {n}: " + ("; then ".join(f"{w['from'][11:16]}Z-{w['until'][11:16]}Z ({rel(w['from'])}, "
                                                f"{w['via']})" for w in ws) or "no stream seen inside the horizon"))
    heard = res.get("heard") or {}
    out += ["", f"## Heard on their streams ({len(heard)} faces: the trailer's sound in the stream's own audio)", ""]
    for n, f in sorted(heard.items(), key=lambda kv: kv[1].get("found_at") or ""):
        out.append(f"- {rel(f['found_at'])} **{n}**: film 0 at VOD {f['hms']} ({f['url']}), film "
                   f"{span(f['runs'])}, {len(f.get('events') or [])} pauses and rewinds")
    held = {k: p for k, p in res["pulled"].items() if p["film_holds"]}
    out += ["", f"## Clips that hold the film ({len(held)} of {len(res['pulled'])} pulled)", ""]
    for key, p in sorted(held.items(), key=lambda kv: kv[1]["at"]):
        v = p.get("vod") or {}
        out.append(f"- pulled {rel(p['at'])}: **{p['face']}**, clip made {rel(p['created_at'])} ({p.get('views') or 0:,.0f} "
                   f"views by 10-19), film {', '.join(f'{a:.0f}-{b:.0f} s' for a, b in p['film'])}"
                   + (f"; its offset puts film 0 at VOD {v['hms']} (approximate)" if v else "") + f". {p['url']}")
    out += ["", "## Every clip pulled (when drop mode would have kept each)", "",
            "| pulled | face | made | game | film in it | clip |", "|---|---|---|---|---|---|"]
    for key, p in sorted(res["pulled"].items(), key=lambda kv: kv[1]["at"]):
        fh = {True: "yes " + ", ".join(f"{a:.0f}-{b:.0f}" for a, b in p["film"]), False: "no", None: "?"}[p["film_holds"]]
        out.append(f"| {rel(p['at'])} | {p['face']} | {rel(p['created_at'])} | {p.get('game') or ''} | {fh} | "
                   f"[{(p.get('title') or key)[:40].replace('|', '/')}]({p['url']}) |")
    c = res["candidate"]
    out += ["", "## The candidate at the horizon", ""]
    for f in c["faces"]:
        v = f.get("vod")
        out.append(f"- {f['name']}{' (counted)' if f['name'] in c['counted'] else ''}: "
                   + (f"VOD {v['hms']} {v['url']}; " if v else "")
                   + ("; ".join(f"[{(x.get('title') or x['platform'])[:30]}]({x['url']}) "
                                f"(film {'yes' if (x.get('verify') or {}).get('holds') else 'no/?'})" for x in f["clips"])
                      or "no clip"))
    if res.get("moment"):
        m = res["moment"]
        out.append(f"- the faces overlap on film {m['span'][0]}-{m['span'][1]} s ({', '.join(m['faces'])}): the REACTIONS "
                   f"beat goes inside it")
    out += ["", "## Timeline", ""] + [f"- {rel(e['at'])} {e['event']}" for e in res["timeline"]]
    return out
