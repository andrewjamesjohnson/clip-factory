"""THE COVER: the still a feed shows for a clip before anyone presses play, set on each platform that takes one.

Andrew's gaps review, 2026-10-28: no cover was set on any platform. Every Reel went out on whatever the app picked,
which is the first frame, and the first frame is now often a VS card (vscard.py) and from 11-03 a pre-hook
(prehook.py): a still that says nothing about the clip, on the profile grid and the Reels tab for good.

Where the cover comes from. The engine writes a poster beside every clip it renders (clips/NN-<slug>.jpg,
clipkit.poster): the second the night's author pinned on the slate ("poster_at", a face, moved clear of b-roll and a
plate's wipe), else the sharpest frame of the hook hold. In the big frame (2026-10-28) that still is the headline
over the speaker's face. collect copies it into the draft as the slot's "cover" and Clip Review shows it on the card,
so the cover a platform gets is the one Andrew sees before he signs.

Never the jpg itself, and never a guess at a time. The jpg is not part of what Andrew signs (board.content_hash: the
clip, its cuts, its words), and the file a platform gets is no longer the clip the engine made: a VS card or a
pre-hook goes in front at collect, and Instagram gets its own 30-40s cut, so the poster's second is somewhere else in
every file. So the cover is FOUND in the very file being posted: each of its frames, 10 a second at 72x128 in grey,
against the jpg, and the closest one wins when it is close (MATCH_MAX; the same picture under other caption words is
about 3-8, another shot or a VS card 20 and up). The cover is then always a frame of the signed file, at the
poster's moment wherever it now sits. Finding it reads the file once (1-2 s for a 90 s clip).

The misses, 2026-10-30 (Andrew, "Yes, fix it"): 8 of 26 clips still went out on the frame the app picks, 4 of the
Politics's 10 Instagram cuts on 10-29 and 4 of the 16 GTA-side clips on 10-30. Two causes, two fixes:
  A fast shot. Ten frames a second step over the poster when the picture moves fast (a car at speed: the frames
  either side of it were 13-17 off, the same picture to the eye). So when nothing is close, the first look's three
  best spots are looked at again, 60 frames a second for a fifth of a second either side (_closer). That found all
  four (0.1-2.9 off).
  A cut without the poster's moment. Instagram's 30-40 s cut is its own render, and the poster's second is often not
  in it (other words under the face, now and then another headline). Then the cover is the frame of that file most
  like the poster, the same face under the same headline at another second, when it is within LOOK_MAX, and never
  in what the factory put in front of the render (a VS card or a pre-hook, opener()). The record says
  "look_alike": true. 10-29's four were 12.4-34.5 off, each the same speaker in the same frame.
Past both (nothing within LOOK_MAX, or the jpg is from another render) there is no cover and the clip goes as it
always went.

A fight's cover is its VS card (Andrew 10.30, on a FACE-OFF clip's card: "that would be the thumbnail for that type
of clip"): the card's frame where it is whole, both fighters, both names under them, VS at rest, just before FIGHT!
takes its place (vscard.full_at, kept on the card's record as vs_at). Only in a file that opens on the card (a cut
with no room for it has none) and only for a card made since (an older record has no vs_at: the poster as above).

What each platform gets (each checked against its own docs, 2026-10-28):
  Instagram  thumb_offset (milliseconds) on the Reel's container: the frame at that point is the cover. No public
             link needed. Its cover_url would need the jpg on a public server, and the factory serves nothing.
  Facebook   that frame as a JPEG, POSTed to /{video-id}/thumbnails with is_preferred=true (the Reels publish takes
             no cover; Meta's Reels guide points at the video thumbnails edge for one). Once the Reel is up and on
             the board, never before (direct.fb_post): a refused cover costs the post nothing. The edge lists
             pages_manage_engagement among its permissions, which the Politics's key has (read 10-28), and
             pages_read_user_content, which it does not: if Meta holds to that, the record says so and the Reel
             keeps the frame Facebook picked.
  YouTube    nothing. thumbnails.set needs a channel allowed custom thumbnails; custom Shorts thumbnails reached
             Partner Program channels first (from 2026-07-25), set in Studio on a desktop; the Data API's page does
             not mention Shorts at all; and the Shorts feed shows no thumbnail. 50 quota units a clip for a still
             only the channel page and search might show, which no test here can prove without posting: not wired.
             Look once in Studio (a Short of the channel, Edit, Thumbnail): if the box is there, it is worth wiring.
  TikTok     not wired: by hand for both accounts. Its Content Posting API takes video_cover_timestamp_ms, the same
             idea as Instagram's thumb_offset, for the day it posts here.

THE ART (coverart.py, 2026-10-30): with the cover_art switch on too, a slot with a designed cover gives Facebook that
image instead of the frame, and Instagram a link to it (cover_url: the image as an unpublished photo on the account's
Page) beside thumb_offset; a refused link (the container, or Instagram failing the reel) is remembered for that slot
and the next try goes on the frame (direct.ig_post).

The switch, per account, off unless switched on (state/<account>/social/settings.json, switches.py):

    factory.py social on politics cover

A cover never fails a post. Off: nothing here runs and the records are as they always were. On, with no jpg, no
close frame, no ffmpeg or numpy, any error, Instagram refusing a container that has thumb_offset (it is asked again
without it, direct.ig_post), or Meta refusing the thumbnail: the clip goes (or has gone) exactly as before, and the
platform's record on the board says why under "cover": {"none": why} or {"set": false, "why": ...}.
"""
from __future__ import annotations

import json
import math
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from . import config as C

SWITCH = "cover"
FPS = 10                  # frames a second looked at: the cover is within a twentieth of a second of the poster
W, H = 72, 128            # each frame in grey at this size (9:16): enough to tell one picture from another
MATCH_MAX = 12.0          # mean grey difference (0-255) a frame may have from the poster and still be it
FINE_FPS = 60             # the second look, when the first finds nothing close: frames a second (a fast shot)
FINE_S = 0.2              # ... this long either side of each of the first look's best spots
FINE_SPOTS = 3            # ... at this many of them, half a second apart at least
LOOK_MAX = 35.0           # a cut without the poster's moment: the furthest the frame most like it may be, to stand in
SKIP_PAD = 0.3            # the look-alike starts this far past what the factory put in front (a VS card, a pre-hook)
DECODE_S = 120            # ffmpeg's longest read of one file; past it, no cover
JPEG_Q = "2"              # the frame Facebook gets, at ffmpeg's best JPEG quality (the engine's poster is the same)


# ------------------------------------------------------------------------------------------------------ the switch
def on(acct) -> bool:
    from . import switches as SW
    return SW.mode(acct, SWITCH) == "on"


def still(slot: dict) -> Optional[Path]:
    """The slot's cover still (the engine's poster, copied in at collect), None when there is none on disk."""
    p = slot.get("cover") if isinstance(slot, dict) else None
    if not p:
        return None
    p = Path(str(p))
    return p if p.is_file() else None


# ------------------------------------------------------------------------------------------------------ finding it
def _grey(src: Path, vf: str, start: Optional[float] = None, dur: Optional[float] = None):
    """The frames ffmpeg makes of `src` through `vf`, as an (n, H, W) array of signed numbers (numpy); only the `dur`
    seconds from `start` when they are given."""
    import numpy as np
    seek = ["-ss", f"{max(start, 0.0):.3f}", "-t", f"{dur:.3f}"] if start is not None and dur else []
    out = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", *seek, "-i", str(src), "-vf",
                          f"{vf},scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, timeout=DECODE_S).stdout
    n = len(out) // (W * H)
    return np.frombuffer(out[:n * W * H], np.uint8).reshape(n, H, W).astype(np.int16)


def find(clip: Path, jpg: Path) -> Tuple[Optional[float], Optional[float]]:
    """(the second of `clip` whose frame is the still `jpg`, how far it is), (None, the nearest) when no frame is
    within MATCH_MAX, (None, None) when either could not be read."""
    at, dist, _ = _search(clip, jpg)
    return at, dist


def _search(clip: Path, jpg: Path, skip: Optional[float] = None) -> Tuple[Optional[float], Optional[float], str]:
    """(second, how far, "poster") at the frame of `clip` that is the still `jpg`; when none is, and `skip` is given
    (the seconds the factory put in front of the render, opener()), (second, how far, "look_alike") at the frame past
    them most like it, within LOOK_MAX; (None, the nearest, "") when neither, (None, None, "") when unreadable."""
    ref = _grey(jpg, "null")
    frames = _grey(clip, f"fps={FPS}")
    if not len(ref) or not len(frames):
        return None, None, ""
    diff = abs(frames - ref[0]).mean(axis=(1, 2))
    i = int(diff.argmin())
    best = float(diff[i])
    if best <= MATCH_MAX:
        return round(i / FPS, 2), round(best, 1), "poster"
    at, fine = _closer(clip, ref[0], diff)
    if fine is not None and fine <= MATCH_MAX:
        return at, round(fine, 1), "poster"
    nearest = round(min(best, fine) if fine is not None else best, 1)
    if skip is None:
        return None, nearest, ""
    k0 = int(math.ceil((max(skip, 0.0) + SKIP_PAD) * FPS))
    rest = diff[k0:]
    if len(rest) and float(rest.min()) <= LOOK_MAX:
        k = int(rest.argmin())
        return round((k0 + k) / FPS, 2), round(float(rest[k]), 1), "look_alike"
    return None, nearest, ""


def _closer(clip: Path, ref, diff) -> Tuple[Optional[float], Optional[float]]:
    """The second look: (second, how far) of the frame most like `ref` at FINE_FPS, FINE_S either side of the first
    look's FINE_SPOTS best spots (`diff`, one a FPS-th of a second apart); (None, None) when nothing could be read."""
    spots: List[float] = []
    for j in diff.argsort():
        t = int(j) / FPS
        if all(abs(t - s) >= 0.5 for s in spots):
            spots.append(t)
        if len(spots) == FINE_SPOTS:
            break
    at, best = None, None
    for t in spots:
        start = max(t - FINE_S, 0.0)
        frames = _grey(clip, f"fps={FINE_FPS}", start, 2 * FINE_S)
        if not len(frames):
            continue
        d = abs(frames - ref).mean(axis=(1, 2))
        k = int(d.argmin())
        if best is None or float(d[k]) < best:
            at, best = round(start + k / FINE_FPS, 3), float(d[k])
    return at, best


def opener(slot: dict, clip: Path) -> float:
    """The seconds the factory put in front of the engine's render in `clip` (a VS card, a pre-hook): its record's
    seconds past its delivered_seconds (the slot's clip or a platform's cut, whichever is this file), else 0."""
    rows = [slot.get("clip")] + list((slot.get("cuts") or {}).values()) if isinstance(slot, dict) else []
    for row in rows:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        try:
            same = str(row["path"]) == str(clip) or Path(str(row["path"])).resolve() == Path(clip).resolve()
            if same:
                was = row.get("delivered_seconds")
                return max(0.0, float(row.get("seconds") or 0) - float(was)) if was is not None else 0.0
        except (OSError, TypeError, ValueError):
            return 0.0
    return 0.0


def vs_moment(slot: dict, clip: Path) -> Optional[float]:
    """The second of `clip` where its VS card is whole (both fighters, both names, VS, before FIGHT!), when the file
    opens on the card; None when it does not (no card, or a cut that had no room for one) or the card was made before
    its record kept that second (vs_at, vscard.full_at, 2026-10-30)."""
    v = slot.get("vs_card") if isinstance(slot, dict) else None
    if not isinstance(v, dict) or not isinstance(v.get("vs_at"), (int, float)):
        return None
    secs = float(v.get("seconds") or 0)
    if not 0 < float(v["vs_at"]) < secs or opener(slot, clip) < secs - 0.15:
        return None
    return round(float(v["vs_at"]), 3)


def pick(acct, slot: dict, clip: Path) -> Tuple[Optional[float], dict]:
    """(the second of `clip` that is the cover, what to write down). (None, {}) while the switch is off; (None,
    {"none": why}) when it is on and there is no cover to give."""
    if not on(acct):
        return None, {}
    vs = vs_moment(slot, Path(clip))
    if vs is not None:
        return vs, {"at_s": vs, "from": "vs_card"}
    jpg = still(slot)
    if jpg is None:
        return None, {"none": "the slot has no cover still (the engine writes one beside every clip it renders)"}
    try:
        at, dist, how = _search(Path(clip), jpg, skip=opener(slot, Path(clip)))
    except ImportError:
        return None, {"none": "numpy is not installed for this Python"}
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return None, {"none": f"could not read the frames: {type(e).__name__}: {e}"[:200]}
    if at is None:
        return None, {"none": (f"no frame of {Path(clip).name} is the cover still {jpg.name} or like it (nearest "
                               f"{dist}, at most {MATCH_MAX:g}, a look-alike {LOOK_MAX:g}): the frame the app picks"
                               if dist is not None else f"could not read {Path(clip).name} or {jpg.name}")}
    rec = {"at_s": at, "match": dist, "still": jpg.name}
    return at, ({**rec, "look_alike": True} if how == "look_alike" else rec)


def frame(clip: Path, at: float, out: Path) -> Path:
    """The frame of `clip` at `at` seconds, written as a full-size JPEG at `out`."""
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{at:.2f}", "-i", str(clip), "-frames:v", "1",
                    "-q:v", JPEG_Q, str(out)], capture_output=True, timeout=DECODE_S, check=True)
    if not out.is_file() or out.stat().st_size < 1000:
        raise ValueError(f"ffmpeg made no frame of {clip.name} at {at:.2f}s")
    return out


# ------------------------------------------------------------------------------------------------------ the platforms
def art(acct, slot: dict) -> Optional[Path]:
    """The slot's designed cover (coverart.py, 2026-10-30) when both switches are on (cover and cover_art), None
    otherwise: then the cover is the poster frame, as before."""
    if not on(acct):
        return None
    try:
        from . import coverart as CA
        return CA.path_of(slot) if CA.on(acct) else None
    except Exception:  # noqa: BLE001  (no art: the frame)
        return None


def _refused_file(acct) -> Path:
    return C.STATE / acct.slug / "social" / "cover-url-refused.json"


def _key(slot: dict) -> str:
    return f"{slot.get('publish_at') or slot.get('slot')} {slot.get('slug') or ''}"


def refused(acct, slot: dict, why: str) -> None:
    """Instagram failed a container that had the art's link: the next try goes without it (the reel never waits on
    its cover twice)."""
    from . import store
    with store.edit(_refused_file(acct), dict) as d:
        d[_key(slot)] = {"why": str(why)[:300], "at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def was_refused(acct, slot: dict) -> bool:
    try:
        return _key(slot) in json.loads(_refused_file(acct).read_text())
    except (OSError, ValueError):
        return False


def page_photo_url(acct, img: Path) -> str:
    """A link Instagram can fetch the art from (its cover_url takes a link only, and the factory serves nothing):
    the image as an unpublished photo on the account's own Facebook Page (published=false: on no feed, no
    timeline), the address of its largest rendition."""
    from . import direct as D
    m = D._load(acct, "meta", "meta")
    tok, page = m["page_token"], m["page_id"]
    body, ctype = D._multipart([("published", "false"), ("access_token", tok)], "source", img,
                               filename="cover.jpg", ctype="image/jpeg")
    got = D._http("POST", f"{D.GRAPH}/{page}/photos", data=body, headers={"Content-Type": ctype}, timeout=120)
    info = D._http("GET", f"{D.GRAPH}/{got['id']}", params={"fields": "images", "access_token": tok}, timeout=60)
    imgs = sorted((x for x in info.get("images") or [] if x.get("source")), key=lambda x: -int(x.get("width") or 0))
    if not imgs:
        raise ValueError("Facebook gave no address for the cover photo")
    return str(imgs[0]["source"])


def ig_form(acct, slot: dict, clip: Path) -> Tuple[dict, dict]:
    """(the Instagram container's cover fields, what to write down): the art's link as cover_url when the slot has
    art (Instagram takes cover_url over thumb_offset), and {"thumb_offset": "<ms>"} at the cover's frame of the file
    Instagram gets (its cut); ({}, ...) and the Reel takes the frame the app picks, as before."""
    at, rec = pick(acct, slot, clip)
    form: dict = {}
    if at is not None:
        ms = int(round(at * 1000))
        form, rec = {"thumb_offset": str(ms)}, {**rec, "how": "thumb_offset", "ms": ms}
    a = art(acct, slot)
    if a is not None and not was_refused(acct, slot):
        try:
            form["cover_url"] = page_photo_url(acct, a)
            rec = {**{k: v for k, v in rec.items() if k != "none"}, "how": "cover_url", "art": a.name}
        except Exception as e:  # noqa: BLE001  (no link: the frame, as before)
            rec = {**rec, "art_none": f"{type(e).__name__}: {e}"[:200]}
    return form, rec


def fb_request(acct, video_id: str, img: Path) -> Tuple[str, bytes, str]:
    """(url, body, content type) of the thumbnail Facebook gets for one Reel: the image as `source`, preferred. The
    Page's key rides in the body, never in the address."""
    from . import direct as D
    tok = D._load(acct, "meta", "meta")["page_token"]
    body, ctype = D._multipart([("is_preferred", "true"), ("access_token", tok)], "source", img,
                               filename="cover.jpg", ctype="image/jpeg")
    return f"{D.GRAPH}/{video_id}/thumbnails", body, ctype


def fb_thumbnail(acct, slot: dict, clip: Path, video_id: str) -> dict:
    """Set a posted Facebook Reel's cover to the slot's art (coverart.py) when it has one, else to the cover's frame
    of the file it was made from. What to write down: {} while the switch is off; {"none": why}; {..., "set":
    True}; {..., "set": False, "why": Meta's words}."""
    from . import direct as D
    a = art(acct, slot)
    if a is not None:
        at, rec = None, {"art": a.name}
    else:
        at, rec = pick(acct, slot, clip)
        if at is None:
            return rec
    if not video_id:
        return rec
    rec = {**rec, "how": "thumbnail"}
    try:
        with tempfile.TemporaryDirectory(prefix="clipfactory-cover-") as td:
            img = a if a is not None else frame(Path(clip), at, Path(td) / "cover.jpg")
            url, body, ctype = fb_request(acct, video_id, img)
            got = D._http("POST", url, data=body, headers={"Content-Type": ctype}, timeout=120)
    except D.PlatformError as e:
        return {**rec, "set": False, "why": str(e)[:300]}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as e:
        return {**rec, "set": False, "why": f"{type(e).__name__}: {e}"[:300]}
    if isinstance(got, dict) and got.get("success") is False:
        return {**rec, "set": False, "why": "Facebook answered success: false"}
    return {**rec, "set": True}


# ------------------------------------------------------------------------------------------------------ the doctor
def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """One line per owner account: the cover switch and what it does. Read only."""
    from . import switches as SW
    out: List[Tuple[Optional[bool], str, str]] = []
    for a in cfg.accounts.values():
        if SW._is_client(a):
            continue
        if on(a):
            out.append((None, f"{a.name} cover: on (Instagram and Facebook Reels open on the clip's poster frame; "
                              f"YouTube picks its own)", ""))
        else:
            out.append((None, f"{a.name} cover: off; each Reel shows the frame the app picks (often the VS card). "
                              f"On: factory.py social on {a.slug} cover", ""))
    return out
