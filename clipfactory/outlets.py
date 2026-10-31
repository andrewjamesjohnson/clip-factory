"""Outlets: where a SIGNED clip goes. Everything here re-checks the signed hash first and refuses anything that changed.

Approve is the whole hand-over (signoff.approve calls hand_off right after signing): the slot is `scheduled`, the
day's export is rewritten, and the poster (`factory.py post-due`, clipfactory/poster.py) does the rest by itself:

api      the platform posts by itself. YouTube Shorts uploads as soon as the poster can, or, with the outlet's
         `upload_ahead_hours` (on for both accounts since 2026-10-17 night), only that close to its slot; private
         with `publishAt` = the slot, so YouTube itself publishes it at the slot even if this Mac is asleep then.
         The Mac must be awake in that window to upload it (a slot whose time has passed goes public at once; past
         20 h it is left for a person). Facebook Reels and Instagram Reels post at the slot time (clipfactory/direct.py).
         TikTok posts at the slot time through Upload-Post (`"via": "upload-post"`; TikTok will not audit an app
         that posts only to its owner's accounts). An outlet may name `"from"`: a time before which its slots stay
         by hand (they were already handed out for posting by hand). With the account's `"signed_by_hand_stays":
         true`, a slot signed while a platform was posted by hand stays by hand on it after that platform switches to
         api (the Politics's Instagram went api at 14:02 on 2026-10-17 with no "from"; Patel 10-18 07:00, signed at
         13:53 for posting by hand, would have been refused at 07:00). Sign-ins are the owner's own:
         `factory.py connect youtube|meta|upload-post <account>`.
export   the platform is posted by hand: the day's `Ready to post/<platform>/` folder in the drafts holds its clips in
         slot order, each with its words beside it. SCHEDULE.csv lists every post of the day, for every platform,
         with how it goes out.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import collect as K
from . import config as C
from . import copytext as T

TOKENS = Path(os.path.expanduser("~/.config/clip-factory"))
# What connect youtube asks Google for: upload, read the channel's videos, and (since 2026-10-19) YouTube Analytics for
# watch time and retention. Nothing that can change or delete a video. A sign-in from before the third has no watch
# time until the channel is connected again (direct.yt_list says so on each read).
YT_SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly",
             "https://www.googleapis.com/auth/yt-analytics.readonly")
NAMES = {"youtube_shorts": "YouTube", "facebook_reels": "Facebook", "instagram_reels": "Instagram", "tiktok": "TikTok"}


def mode(acct: C.Account, plat: str) -> str:
    return acct.outlets.get(plat, {}).get("mode", "export")


def covers(acct: C.Account, plat: str, publish_at: str) -> bool:
    """True when the platform's API outlet takes a slot at that time: an outlet switched on today (its `"from"`)
    leaves the slots before it to the hands they were already given to."""
    start = acct.outlets.get(plat, {}).get("from")
    if mode(acct, plat) != "api":
        return False
    if not start:
        return True
    try:
        return datetime.fromisoformat(publish_at) >= datetime.fromisoformat(start)
    except (TypeError, ValueError):
        return False


def covers_slot(acct: C.Account, plat: str, slot: dict) -> bool:
    """covers() for one slot as it was signed: with the account's "signed_by_hand_stays", a platform the slot was
    handed out for posting by hand stays by hand, whatever its outlet says now. Without it, the same as covers()."""
    if not covers(acct, plat, slot.get("publish_at")):
        return False
    if acct.raw.get("signed_by_hand_stays") and slot.get("signoff") and plat in (handed_by_hand(slot) or []):
        return False
    return True


def handed_by_hand(slot: dict) -> Optional[List[str]]:
    """The platforms this slot was handed out for posting by hand when it was last scheduled (None when unknown).
    Recorded at the hand-off since 2026-10-18; before that only the board note says it ("scheduled (YouTube and
    Facebook post by themselves; TikTok and Instagram are posted by hand)")."""
    h = slot.get("handed")
    if isinstance(h, dict) and isinstance(h.get("by_hand"), list):
        return list(h["by_hand"])
    back = {v: k for k, v in NAMES.items()}
    for n in reversed(slot.get("notes") or []):
        text = n.split(" ", 1)[-1]
        if text.startswith(("pulled by", "approved by")):
            return None                     # signed again since the last hand-off: not handed out yet
        if not (text.startswith("scheduled (") and text.endswith(")")):
            continue
        hand = [part for part in text[len("scheduled ("):-1].split("; ") if part.endswith(" posted by hand")]
        if not hand:
            return []
        names = hand[0].rsplit(" posted by hand", 1)[0].rsplit(" ", 1)[0]     # 'TikTok and Instagram' (is/are)
        return [back[n.strip()] for n in names.replace(" and ", ", ").split(", ") if n.strip() in back]
    return None


def band(acct: C.Account, plat: str, cut: bool = False) -> Tuple[float, float]:
    """The length a platform takes for this account: its outlet's `min_seconds`/`max_seconds` when set (the Politics's
    Instagram takes about 40s at most), else the account's own band. With `cut`, the band of the platform's OWN cut:
    the outlet's `cut_min_seconds`/`cut_max_seconds` when set (the Politics's YouTube cut, 30-59s), else the same."""
    o = acct.outlets.get(plat, {})
    lo, hi = float(o.get("min_seconds", acct.min_seconds)), float(o.get("max_seconds", acct.max_seconds))
    if cut and o.get("cut_max_seconds") is not None:
        return float(o.get("cut_min_seconds", lo)), float(o["cut_max_seconds"])
    return lo, hi


# a rights value (collect.source_of reads it from the Politics job's project.json) in a card's words
RIGHTS_WORDS = {"rebroadcast": "a network or C-SPAN clip", "unverified": "a clip with unverified rights",
                "unknown": "a clip with no rights on record"}


def rights_of(acct: C.Account, slot: dict) -> str:
    """The slot's rights, lower case: the board's (collect wrote them from collect.source_of), else read now the same
    way from the slot's delivery folder (a source.json beside the clip, or the Politics job's project.json), so the
    poster, the card and `ytcut` judge one clip alike; '' when nothing says (a hand drop has no job)."""
    rights = str(slot.get("rights") or "").strip().lower()
    if rights or not slot.get("source_folder"):
        return rights
    try:
        got = K.source_of(acct, Path(slot["source_folder"]), str(slot.get("publish_at") or "")[:10])
    except Exception:  # noqa: BLE001  (a broken source file never stops a post or a card: nothing says, then)
        return ""
    return str(got.get("rights") or "").strip().lower()


def cut_rule(acct: C.Account, slot: dict, plat: str) -> str:
    """Why this platform takes the slot's clip only as its own cut although the clip is inside its band ('' when it
    takes the clip whole): the outlet's `cut_when`, {"rights": [...], "over_seconds": N, "why": "..."}. The Politics's
    YouTube (audit call 5, 2026-10-17): a network or C-SPAN clip (rights "rebroadcast") over 60s, because one Content
    ID claim blocks a Short over 60s worldwide. Rights come from rights_of; a clip nothing says anything about counts
    as "unknown", which the live rule leaves out (a hand drop goes to YouTube whole, as before)."""
    rule = acct.outlets.get(plat, {}).get("cut_when") or {}
    if not rule:
        return ""
    secs = float((slot.get("clip") or {}).get("seconds") or 0)
    if secs <= float(rule.get("over_seconds", 0)):
        return ""
    rights = rights_of(acct, slot) or "unknown"
    if rights not in [str(r).lower() for r in rule.get("rights") or []]:
        return ""
    what = RIGHTS_WORDS.get(rights, f"a clip with rights {rights!r}")
    return f"{what} of {secs:.0f}s; {rule.get('why') or NAMES.get(plat, plat) + ' takes it only as its own cut'}"


def needs_cut(acct: C.Account, slot: dict, plat: str) -> str:
    """Why this platform cannot take the slot's clip itself and needs its own cut ('' when it can, or when the slot
    has that cut): the clip is outside the platform's band, or the outlet's cut_when covers it (cut_rule)."""
    if (slot.get("cuts") or {}).get(plat):
        return ""
    lo, hi = band(acct, plat)
    secs = float((slot.get("clip") or {}).get("seconds") or 0)
    if secs and not lo - 0.05 <= secs <= hi + 0.05:
        return f"the clip is {secs:.0f}s and {NAMES.get(plat, plat)} takes {lo:.0f}-{hi:.0f}s for this account"
    return cut_rule(acct, slot, plat)


def clip_for(acct: C.Account, slot: dict, plat: str) -> Path:
    """The file that goes to this platform: its own cut when the slot has one, else the slot's clip, if the platform
    takes it (its band, and the outlet's cut_when: the Politics's YouTube takes a network or C-SPAN clip over 60s only
    as its own cut). Otherwise refused, said why (not retried)."""
    cut = (slot.get("cuts") or {}).get(plat)
    if cut:
        return Path(cut["path"])
    why = needs_cut(acct, slot, plat)
    if why:
        from . import direct as D
        raise D.Refused(f"no {NAMES.get(plat, plat)} cut: {why}")
    return Path(slot["clip"]["path"])


def auto_platforms(acct: C.Account) -> List[str]:
    """The platforms that post by themselves (an API outlet), in the account's order."""
    return [p for p in acct.platforms if mode(acct, p) == "api"]


def hand_platforms(acct: C.Account) -> List[str]:
    """The platforms a person posts by hand, from the day's Ready to post folder."""
    return [p for p in acct.platforms if mode(acct, p) != "api"]


def _names(plats: List[str]) -> str:
    """['youtube_shorts', 'tiktok'] -> 'YouTube and TikTok'"""
    n = [NAMES.get(p, p) for p in plats]
    if len(n) < 2:
        return "".join(n)
    return ", ".join(n[:-1]) + " and " + n[-1]


def routes(acct: C.Account) -> str:
    """'YouTube, Facebook and Instagram post by themselves; TikTok is posted by hand'"""
    auto, hand = auto_platforms(acct), hand_platforms(acct)
    parts = []
    if auto:
        parts.append(f"{_names(auto)} post{'s by itself' if len(auto) == 1 else ' by themselves'}")
    if hand:
        parts.append(f"{_names(hand)} {'is' if len(hand) == 1 else 'are'} posted by hand")
    return "; ".join(parts)


def _signed(board: dict) -> List[dict]:
    return [s for s in sorted(board["slots"].values(), key=lambda s: s["slot"])
            if s["status"] in B.SIGNED and s.get("signoff")]


def hand_off(cfg: C.Config, acct: C.Account, day: date, dry_run: bool = False) -> List[str]:
    """Every signed slot of the day -> `scheduled`, and the day's export rewritten from them.

    Approve calls it right after signing; the poster calls it too, for anything signed before Approve scheduled (or
    whose hand-off died). Each signature is re-checked: a clip or caption changed after signing is not scheduled."""
    if dry_run:
        bd = B.read(acct, day)
        if not bd:
            return [f"  no board for {acct.slug} on {day}"]
        good = [s for s in _signed(bd) if not B.verify(s)]
        return ([f"  {s['slot']}: would be scheduled" for s in good if s["status"] == B.APPROVED]
                + export(cfg, acct, day, good, dry_run=True))
    lines: List[str] = []
    with B.edit(cfg, acct, day, create=False) as bd:      # under the board's lock: one export of a day at a time
        good = []
        for s in _signed(bd):
            problems = B.verify(s)
            if problems:
                why = "; ".join(problems)
                if B.note_once(s, f"not scheduled: {why}"):
                    lines.append(f"  {s['slot']}: NOT scheduled, {why}")
                continue
            good.append(s)
            if s["status"] == B.APPROVED:
                s["status"] = B.SCHEDULED
                s["handed"] = {"by_hand": [p for p in acct.platforms if not covers(acct, p, s["publish_at"])],
                               "at": B.utcnow()}
                B.note(s, f"scheduled ({routes(acct)})")
                lines.append(f"  {s['slot']} {s.get('slug', '')}: scheduled")
        lines += export(cfg, acct, day, good, dry_run=False)
    return lines


def schedule(cfg: C.Config, acct: C.Account, day: date, dry_run: bool = False) -> List[str]:
    """`factory.py schedule`: hand off whatever is signed for the day (Approve already does this by itself)."""
    if not B.read(acct, day):
        return [f"no board for {acct.slug} on {day}"]
    return hand_off(cfg, acct, day, dry_run=dry_run) or [f"{acct.name} {day}: nothing signed yet"]


def export(cfg: C.Config, acct: C.Account, day: date, slots: List[dict], dry_run: bool) -> List[str]:
    """SCHEDULE.csv (every post of the day and how it goes out) and a Ready to post folder for each platform posted by
    hand. A platform that posts by itself gets no folder: a second copy to post by hand would only post it twice."""
    out = K.drafts_dir(cfg, acct, day)
    by_hand = {p: [s for s in slots if not covers_slot(acct, p, s)] for p in acct.platforms}
    hand = [p for p in acct.platforms if by_hand[p]]
    rows = []
    for s in slots:
        for plat in acct.platforms:
            c = s.get("copy", {}).get(plat)
            if not c:
                continue
            words = T.for_platform(Path(c["path"]).read_text(), plat)
            rows.append({"date": day.isoformat(), "time": s["slot"], "publish_at": s["publish_at"], "platform": plat,
                         "goes_out": "by itself" if covers_slot(acct, plat, s) else "by hand",
                         "video": ((s.get("cuts") or {}).get(plat) or s["clip"])["path"], "cover": s.get("cover") or "",
                         "title": words["title"].strip(),
                         "caption": words["caption"].strip(), "signed_by": s["signoff"]["by"]})
    if dry_run:
        return [f"  export: would write {len(rows)} rows to {out / 'SCHEDULE.csv'}"]
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "SCHEDULE.csv.tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["date"])
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, out / "SCHEDULE.csv")
    for plat in acct.platforms:  # one folder per hand platform: the clips renamed by slot, each with its words beside it
        pd = out / "Ready to post" / plat
        if pd.exists():
            shutil.rmtree(pd)
        if plat not in hand:
            continue
        pd.mkdir(parents=True)
        for s in by_hand[plat]:     # an outlet switched on today still leaves its earlier slots here
            name = f"{s['slot'].replace(':', '')} {s.get('slug', '')}".strip()
            src = Path(((s.get("cuts") or {}).get(plat) or s["clip"])["path"])     # the platform's own cut if it has one
            if _same_fs(src, pd):
                os.link(src, pd / f"{name}.mp4")
            else:
                shutil.copy2(src, pd / f"{name}.mp4")
            c = s.get("copy", {}).get(plat)
            if c and plat == "youtube_shorts":
                shutil.copy2(c["path"], pd / f"{name}.txt")
            elif c:     # the caption as it would post: no # notes, a Politics caption opens on its hook line (10-17)
                paste = T.for_platform(Path(c["path"]).read_text(), plat)["caption"].strip()
                (pd / f"{name}.txt").write_text(paste + "\n")
    rp = out / "Ready to post"
    if rp.is_dir() and not any(rp.iterdir()):
        rp.rmdir()
    where = f" and Ready to post/ ({_names(hand)} by hand)" if hand and slots else ""
    return [f"  export: {len(slots)} signed clips -> SCHEDULE.csv{where}"]


def _same_fs(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


# ------------------------------------------------------------------------------------------------- YouTube
def yt_token_file(acct: C.Account) -> Path:
    return TOKENS / acct.slug / "youtube_token.json"


def yt_connected(acct: C.Account) -> bool:
    return yt_token_file(acct).exists()


def youtube_upload(acct: C.Account, slot: dict) -> str:
    """Upload one signed slot's clip, private with publishAt = its slot (YouTube publishes it then; a time already
    passed publishes it at once, per the videos resource docs). Returns the video id. The poster calls this.

    The poster also passes what an earlier try of this slot left (slot["_attempt"]: the upload session it opened, the
    title and the length) and a pen to write the next one down before any bytes go (slot["_mark"]). A try whose answer
    was lost (a timeout after YouTube had the file) is then finished or found on the channel, never sent again as a
    second video (2026-10-17 audit). The outlet's "category_id" files it (20 Gaming, 25 News & Politics)."""
    from . import direct as D
    token_file = yt_token_file(acct)
    if not token_file.exists():
        raise D.Refused(f"not connected; run: factory.py connect youtube {acct.slug} --client <client.json>")
    opts = acct.outlets.get("youtube_shorts", {})
    words = T.parse(Path(slot["copy"]["youtube_shorts"]["path"]).read_text())
    tags = [t.strip() for t in words.get("tags", "").split(",") if t.strip()]
    if not tags and opts.get("tags_from_hashtags"):      # the GTA copy has no TAGS section: its hashtags, then
        tags = list(dict.fromkeys(re.findall(r"(?<![\w#])#(\w{2,})", words["body"])))
    meta = {
        "snippet": {"title": words["title"].strip()[:100], "description": words["body"].strip().encode()[:4900].decode(errors="ignore"),
                    "categoryId": str(opts.get("category_id", "20")),
                    "tags": tags[:15]},
        "status": {"privacyStatus": "private", "publishAt": _utc(slot["publish_at"]),
                   "selfDeclaredMadeForKids": bool(opts.get("made_for_kids", False)),
                   # a slot may say so itself (a cloned or realistic synthetic voice), else the account's setting
                   "containsSyntheticMedia": bool(slot.get("altered", opts.get("altered", False)))},
    }
    clip = clip_for(acct, slot, "youtube_shorts")
    D.in_band(acct, clip, "youtube_shorts", cut=bool((slot.get("cuts") or {}).get("youtube_shorts")))
    src = (slot.get("cuts") or {}).get("youtube_shorts") or slot.get("clip") or {}
    return _yt_upload(token_file, clip, meta, mark=slot.get("_mark"), attempt=slot.get("_attempt"),
                      seconds=float(src.get("seconds") or 0), sig=str((slot.get("signoff") or {}).get("hash") or ""),
                      sha=str(src.get("sha256") or ""), still=slot.get("_still"), report=slot,
                      words_sha=str(((slot.get("copy") or {}).get("youtube_shorts") or {}).get("sha256") or ""))


# --------------------------------------------------------------------------------- YouTube caps (2026-10-16)
# A new channel that is not phone-verified gets a small daily upload cap. Hitting it ("uploadLimitExceeded") or the API
# quota must stop every further try for a day: each refused try can still leave a broken "still copying" entry in
# Studio and pushes the reset further out. And the factory never sends a channel more than its day's slots in 24 hours.
CAP_ERRORS = ("uploadLimitExceeded", "exceeded the number of videos", "quotaExceeded", "dailyLimitExceeded")
COOL_HOURS = 24


def _yt_state(acct: C.Account) -> Path:
    return C.STATE / acct.slug / "youtube-uploads.json"


def _yt_read(acct: C.Account) -> dict:
    p = _yt_state(acct)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {"uploads": [], "cool_until": 0, "why": ""}


def _yt_write(acct: C.Account, d: dict) -> None:
    p = _yt_state(acct)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.replace(tmp, p)


def yt_hold(acct: C.Account, now: float = None) -> str:
    """Why YouTube must not be tried for this account right now ('' when it may). The same words for as long as the
    hold lasts, so the poster notes it on the board once."""
    now = time.time() if now is None else now
    d = _yt_read(acct)
    if d.get("cool_until", 0) > now:
        until = time.strftime("%b %d %H:%M", time.localtime(d["cool_until"]))
        return (f"YouTube paused for this channel until {until} after: {d.get('why', '')[:120]}. "
                f"The phone is verified (10-26), so this is YouTube's own daily cap: keep the daily_budget under it")
    recent = [t for t in d.get("uploads", []) if t > now - 86400]
    budget = yt_budget(acct)
    if len(recent) >= budget:
        return (f"{len(recent)} uploads in the last 24 hours already (the day's {acct.daily_target} slots)"
                if budget == acct.daily_target else
                f"{len(recent)} uploads in the last 24 hours already (its daily YouTube budget is {budget})")
    return ""


def yt_budget(acct: C.Account) -> int:
    """Uploads the channel takes in 24 hours: the youtube_shorts outlet's "daily_budget" (2026-10-26: the Politics makes
    15 a day, its channel not phone-verified takes about 10 to 12, and one refusal pauses it a whole day), else the
    day's slots. The rest of the day goes to the other platforms only.
    Not capped at the day's slots any more (2026-10-28): with the Daily 8 that cap made the budget 8, so a rolling 24
    hours with 8 uploads had no room left. Each morning's upload waited for yesterday's to age out, later every day:
    the 06:30 went up 98 minutes late on 10-28, and 15:55 and 17:05 never went."""
    v = acct.outlets.get("youtube_shorts", {}).get("daily_budget")
    try:
        b = int(v) if v not in (None, "") else acct.daily_target
    except (TypeError, ValueError):
        b = acct.daily_target
    return max(1, b)


def yt_room(acct: C.Account, now: float = None) -> int:
    """Uploads left in the rolling 24 hours (0 while paused)."""
    now = time.time() if now is None else now
    d = _yt_read(acct)
    if d.get("cool_until", 0) > now:
        return 0
    return max(0, yt_budget(acct) - sum(1 for t in d.get("uploads", []) if t > now - 86400))


def yt_paused(acct: C.Account, now: float = None) -> str:
    """'YouTube paused until <time>' while the cap's day-long pause lasts ('' otherwise): doctor and the 06:30 notice
    say it first (10-23 to 10-25 they said nothing for three days)."""
    now = time.time() if now is None else now
    d = _yt_read(acct)
    if d.get("cool_until", 0) <= now:
        return ""
    return (f"{acct.name}: YouTube paused until {time.strftime('%b %d %H:%M', time.localtime(d['cool_until']))} "
            f"(daily upload limit); today's clips go first when it lifts")


def yt_cool(acct: C.Account, why: str, now: float = None) -> None:
    now = time.time() if now is None else now
    d = _yt_read(acct)
    d.update(cool_until=now + COOL_HOURS * 3600, why=why.replace("\n", " ")[:300])
    _yt_write(acct, d)


def yt_count(acct: C.Account, now: float = None) -> None:
    now = time.time() if now is None else now
    d = _yt_read(acct)
    d["uploads"] = [t for t in d.get("uploads", []) if t > now - 86400] + [now]
    _yt_write(acct, d)


# ----------------------------------------------------------------------------------- Meta holds (2026-10-17)
# A refusal about the whole app or sign-in, not one post ("API access blocked": Meta restricted the app, as it did to
# both apps on 10-17; a dead token; Meta's rate limits) must not use up every slot's three tries. Facebook and
# Instagram wait for the account instead, the poster asks Meta again once an hour, and the waiting slots go out (up to
# 20 hours late) as soon as Meta lets them.
META = ("facebook_reels", "instagram_reels")
META_HOLD_ERRORS = ("API access blocked", "Error validating access token", "Session has expired",
                    "session has been invalidated", "Application request limit reached", "User request limit reached",
                    "Page request limit reached", "exceeded the rate limit")
META_RETRY = 3600


def _meta_state(acct: C.Account) -> Path:
    return C.STATE / acct.slug / "meta-hold.json"


def meta_hold(acct: C.Account, now: float = None) -> str:
    """Why Facebook and Instagram must not be tried for this account right now ('' when they may)."""
    now = time.time() if now is None else now
    try:
        d = json.loads(_meta_state(acct).read_text())
    except (OSError, ValueError):
        return ""
    if d.get("until", 0) <= now:
        return ""
    why = d.get("why", "")
    said = why.split(": ", 1)[-1][:120]
    fix = (" Meta has restricted the app: its alerts are at developers.facebook.com/apps" if "API access blocked" in why
           else f" Sign in again: factory.py connect meta {acct.slug}" if ("token" in why or "ession" in why) else "")
    until = time.strftime("%H:%M", time.localtime(d["until"]))
    return f"Facebook and Instagram paused, Meta said: {said} (asked again at {until}).{fix}"


def meta_cool(acct: C.Account, why: str, now: float = None) -> None:
    now = time.time() if now is None else now
    p = _meta_state(acct)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"until": now + META_RETRY, "why": why.replace("\n", " ")[:300]}, indent=1))
    os.replace(tmp, p)


def meta_clear(acct: C.Account) -> None:
    try:
        _meta_state(acct).unlink()
    except OSError:
        pass


def _utc(iso: str) -> str:
    from datetime import datetime, timezone
    return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _yt_access_token(token_file: Path) -> str:
    from . import direct as D
    from . import net
    net.guard("a YouTube token refresh")
    tok = json.loads(token_file.read_text())
    if tok.get("access_token") and tok.get("expires_at", 0) > time.time() + 60:
        return tok["access_token"]
    with D.token_lock(token_file):          # the poster, the stats read and the page may all refresh at once
        tok = json.loads(token_file.read_text())
        if tok.get("access_token") and tok.get("expires_at", 0) > time.time() + 60:
            return tok["access_token"]      # another process refreshed it while this one waited
        data = urllib.parse.urlencode({"client_id": tok["client_id"], "client_secret": tok["client_secret"],
                                       "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}).encode()
        with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data, timeout=60) as r:
            fresh = json.loads(r.read())
        tok.update(access_token=fresh["access_token"], expires_at=time.time() + int(fresh.get("expires_in", 3600)))
        D.write_private(token_file, json.dumps(tok))     # whole or not at all: a reader never sees half a file
    return tok["access_token"]


def _yt_upload(token_file: Path, clip: Path, meta: dict, mark=None, attempt: Optional[dict] = None,
               seconds: float = 0.0, sig: str = "", sha: str = "", still=None, report: Optional[dict] = None,
               words_sha: str = "") -> str:
    from . import net
    net.guard("a YouTube upload")
    access = _yt_access_token(token_file)
    size = clip.stat().st_size
    if attempt:                 # an earlier try's answer never came: finish that upload or find it, never a second
        from . import direct as D
        try:
            vid = _yt_settle(access, attempt, clip, size, sig)
        except D.Pending:
            raise
        except Exception as e:  # noqa: BLE001  (it may be up: a person looks before it is sent fresh)
            raise D.LookupFailed(f"could not check the last YouTube upload: {str(e)[:300]}") from None
        if vid:
            if report is not None:
                report["_from_attempt"] = attempt
            return vid
    if still is not None and not still():
        from . import direct as D
        raise D.Stopped("not uploaded: it was pulled or signed again while this run was busy")
    init = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=json.dumps(meta).encode(), method="POST",
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    try:
        with urllib.request.urlopen(init, timeout=120) as r:
            session = r.headers["Location"]
        if mark:                # written down before the bytes go: a lost answer is looked up next time
            mark({"attempt": {"session": session, "title": (meta.get("snippet") or {}).get("title", ""),
                              "seconds": seconds, "size": size, "hash": sig, "clip_sha": sha, "copy_sha": words_sha,
                              "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}})
        return _yt_put(access, session, clip, size, 0)
    except urllib.error.HTTPError as e:     # YouTube says WHY in the body; a bare "400 Bad Request" says nothing
        raise RuntimeError(f"YouTube refused the upload ({e.code}): {e.read().decode(errors='replace')[:800]}") from None


def _yt_put(access: str, session: str, clip: Path, size: int, start: int) -> str:
    """Send the clip (from byte `start`, to finish an upload YouTube has part of) into an open upload session."""
    h = {"Authorization": f"Bearer {access}", "Content-Type": "video/mp4"}
    if start:
        h["Content-Range"] = f"bytes {start}-{size - 1}/{size}"
    put = urllib.request.Request(session, data=clip.read_bytes()[start:], method="PUT", headers=h)
    with urllib.request.urlopen(put, timeout=1800) as r:
        return json.loads(r.read())["id"]


def _yt_session(access: str, session: str, size: int) -> Tuple[str, Optional[object]]:
    """What became of an upload session: ('done', video id) | ('partial', next byte) | ('gone', None)."""
    req = urllib.request.Request(session, data=b"", method="PUT", headers={
        "Authorization": f"Bearer {access}", "Content-Range": f"bytes */{size}", "Content-Length": "0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return "done", json.loads(r.read())["id"]
    except urllib.error.HTTPError as e:
        if e.code == 308:                   # "Resume Incomplete": Range says what YouTube has (bytes=0-N)
            rng = str(e.headers.get("Range") or "")
            try:
                return "partial", (int(rng.rsplit("-", 1)[1]) + 1) if rng else 0
            except ValueError:
                return "partial", 0
        if e.code in (404, 410):
            return "gone", None
        raise RuntimeError(f"YouTube did not say what became of the last upload ({e.code}): "
                           f"{e.read().decode(errors='replace')[:300]}") from None


def _yt_settle(access: str, att: dict, clip: Path, size: int, sig: str) -> str:
    """The video an earlier try made ('' when it surely made none, so a new upload may go)."""
    same = att.get("size") == size and (not att.get("hash") or not sig or att.get("hash") == sig)
    sess = att.get("session")
    if sess:
        state, val = _yt_session(access, str(sess), size)
        if state == "done":
            return str(val)
        if state == "partial":
            if not same:
                return ""                   # half an upload of a clip that has changed since: never finish it
            try:
                return _yt_put(access, str(sess), clip, size, int(val or 0))
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"YouTube refused the rest of the upload ({e.code}): "
                                   f"{e.read().decode(errors='replace')[:500]}") from None
    return _yt_find(access, str(att.get("title") or ""), float(att.get("seconds") or 0), str(att.get("at") or ""))


def _yt_find(access: str, title: str, seconds: float, since: str) -> str:
    """The channel's own copy of a clip an earlier try sent: same title, uploaded since that try, same length."""
    from . import direct as D
    if not title:
        return ""
    api, auth = "https://www.googleapis.com/youtube/v3", {"Authorization": f"Bearer {access}"}
    ch = D._http("GET", f"{api}/channels", params={"part": "contentDetails", "mine": "true"}, headers=auth)
    if not ch.get("items"):
        return ""
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = D._http("GET", f"{api}/playlistItems", headers=auth, params={
        "part": "contentDetails", "playlistId": uploads, "maxResults": 15})
    ids = [i["contentDetails"]["videoId"] for i in pl.get("items") or []]
    if not ids:
        return ""
    vs = D._http("GET", f"{api}/videos", headers=auth, params={
        "part": "snippet,contentDetails,status", "id": ",".join(ids), "maxResults": 50})
    after = datetime.fromisoformat(since) - timedelta(minutes=10) if since else None
    for v in sorted(vs.get("items") or [], key=lambda v: (v.get("snippet") or {}).get("publishedAt", "")):
        sn = v.get("snippet") or {}
        if sn.get("title", "").strip() != title.strip():
            continue
        if after and sn.get("publishedAt") and datetime.fromisoformat(D._iso(sn["publishedAt"])) < after:
            continue
        if (v.get("status") or {}).get("uploadStatus") in ("failed", "rejected", "deleted"):
            continue
        dur = D._iso_dur((v.get("contentDetails") or {}).get("duration", ""))
        if not dur:
            raise D.Pending(f"YouTube has a copy of this clip still processing ({v['id']}); the next run looks again")
        if seconds and abs(dur - seconds) > 2.5:
            continue
        return str(v["id"])
    return ""


def yt_channel(access: str) -> dict:
    """{id, title, handle} of the channel a fresh sign-in reached (channels?mine=true: the channel picked at Google's
    prompt). A Google login with no channel refuses."""
    from . import direct as D
    try:
        got = D._http("GET", "https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                      headers={"Authorization": f"Bearer {access}"})
    except D.PlatformError as e:
        raise SystemExit(f"nothing saved: YouTube did not say which channel the sign-in reached ({e}); connect "
                         f"again") from None
    items = got.get("items") or []
    if not items:
        raise SystemExit("nothing saved: that Google sign-in has no YouTube channel. Connect again and choose the "
                         "channel itself at Google's prompt")
    sn = items[0].get("snippet") or {}
    return {"id": str(items[0].get("id") or ""), "title": str(sn.get("title") or ""),
            "handle": str(sn.get("customUrl") or "")}


def yt_refusal(cfg: Optional[C.Config], acct: C.Account, ch: dict) -> str:
    """'' when the channel is this account's; else why not. The GTA channels can be Brand Accounts under one Google
    login (NEW-ACCOUNTS.md), so Google's chooser lists them all and one wrong pick would post one account's clips on
    another's channel (review 2026-10-19). Refused: a channel another account's sign-in holds (its channel_id, saved
    since then), one whose @handle is another account's, and one whose @handle is none of this account's ("handle"
    and its "links"). Handles compare in any case: YouTube's @Stunts is Stunts' @stunts. A
    channel with no @handle is saved, and said."""
    from . import direct as D
    other = D.held_by(TOKENS, acct.slug, "youtube_token", "channel_id", ch.get("id"))
    if other:
        return (f"the channel {ch.get('title')} is the one {other} is signed in to. Connect again and choose "
                f"{C.possessive(acct.name)} channel at Google's prompt")
    h = str(ch.get("handle") or "").strip().lstrip("@").lower()     # customUrl: "@gaming"
    if not h:
        return ""
    every = (cfg.all_accounts or cfg.accounts) if cfg else {}
    for o in every.values():
        if o.slug != acct.slug and h in o.handles():
            return (f"the channel {ch.get('title')} (@{h}) is {C.possessive(o.name)}. Connect again and choose "
                    f"{C.possessive(acct.name)} channel at Google's prompt")
    mine = acct.handles()
    if mine and h not in mine:
        named = sorted(x for x in mine if not x.isdigit()) or sorted(mine)     # a Facebook Page id is no @handle
        return (f"the channel {ch.get('title')} is @{h}, and config/accounts.json gives {acct.name} "
                f"{', '.join('@' + x for x in named)}. If @{h} is {C.possessive(acct.name)}, put "
                f"https://www.youtube.com/@{h} in its links.youtube_shorts, then connect again; if not, connect again "
                f"and choose {C.possessive(acct.name)} channel")
    return ""


# connect youtube --with: what the social layer may also ask for. comments (youtube.force-ssl) writes the signed first
# comment and his own replies, and Google also gives it the power to change or delete videos, which no code here uses
# (graph.yt_write allows three calls only); analytics reads retention (yt-analytics.readonly). Since the watch-time
# fold (2026-10-20) analytics is also in YT_SCOPES (top of this file), so every connect asks for it; --with analytics
# stays accepted (the fix lines name it) and asks for nothing extra.
YT_EXTRA = {"comments": "https://www.googleapis.com/auth/youtube.force-ssl",
            "analytics": "https://www.googleapis.com/auth/yt-analytics.readonly"}


def yt_scope(extra=()) -> str:
    """The scope string Google is asked for: YT_SCOPES (upload, read, YouTube Analytics), plus each --with that was
    given and is not already among them."""
    bad = [x for x in extra if x not in YT_EXTRA]
    if bad:
        raise SystemExit(f"--with: {', '.join(bad)} is not one of {', '.join(YT_EXTRA)}")
    return " ".join(list(YT_SCOPES) + [YT_EXTRA[x] for x in YT_EXTRA if x in extra and YT_EXTRA[x] not in YT_SCOPES])


def _scope_tail(s: str) -> str:
    return str(s).strip().rstrip("/").rsplit("/", 1)[-1]


def yt_held(acct: C.Account) -> List[str]:
    """The --with extras this channel's sign-in already has: from capabilities.json and from the token file's own
    scope. connect_youtube asks for them again, because a new Google sign-in covers only what it asked for."""
    have = set()
    try:
        from . import capabilities as CAP
        have.update(CAP.yt_held(acct))
    except (Exception, SystemExit):  # noqa: BLE001  (a connect never fails over its permission list)
        pass
    try:
        tok = json.loads((TOKENS / acct.slug / "youtube_token.json").read_text())
        got = {_scope_tail(x) for x in str(tok.get("scope") or "").split()}
        have.update(x for x, u in YT_EXTRA.items() if _scope_tail(u) in got)
    except (OSError, ValueError, AttributeError):
        pass
    return [x for x in YT_EXTRA if x in have]


def connect_youtube(acct: C.Account, client_file: Path, cfg: Optional[C.Config] = None, extra=()) -> str:
    """The owner's own Google sign-in, once per channel. Loopback flow. The new GTA channels are Brand Accounts under
    Gaming's Google login, so the channel picked at Google's prompt is read back and checked (yt_refusal) before
    anything is saved, and its name is said. `extra` ("comments", "analytics") asks for more than upload and read.
    The extras the channel already has are asked for again (yt_held) and Google is told to keep earlier grants
    (include_granted_scopes): the new token replaces the old one, so a connect with `--with analytics` alone would
    otherwise lose comments. One the person unticks at the prompt is named as lost."""
    import http.server
    import secrets
    import socket
    import threading
    import webbrowser

    extra = tuple(extra or ())
    yt_scope(extra)                     # an unknown --with is refused before anything else
    client = json.loads(client_file.read_text())
    client = client.get("installed") or client.get("web") or client
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    redirect = f"http://127.0.0.1:{port}/"
    state = secrets.token_urlsafe(16)
    got: Dict[str, str] = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state and "code" in q:
                got["code"] = q["code"]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Connected. You can close this tab.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    held = [x for x in yt_held(acct) if x not in extra]
    asked = tuple(x for x in YT_EXTRA if x in extra or x in held)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code", "state": state,
        "scope": yt_scope(asked), "include_granted_scopes": "true",
        "access_type": "offline", "prompt": "select_account consent"})
    if held:
        it = "it" if len(held) == 1 else "them"
        print(f"{acct.name} already has {', '.join(held)} on YouTube. Google asks for {it} again: leave {it} "
              f"ticked to keep {it}.")
    print(f"Sign in as the {acct.name} channel's Google account in the browser that opens:\n{url}")
    webbrowser.open(url)
    for _ in range(600):
        if "code" in got:
            break
        time.sleep(0.5)
    srv.server_close()
    if "code" not in got:
        raise SystemExit("no sign-in within five minutes")
    data = urllib.parse.urlencode({"code": got["code"], "client_id": client["client_id"],
                                   "client_secret": client["client_secret"], "redirect_uri": redirect,
                                   "grant_type": "authorization_code"}).encode()
    from . import net
    net.guard("the YouTube sign-in")
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data, timeout=60) as r:
        tok = json.loads(r.read())
    said = save_youtube(cfg, acct, client, tok)
    granted = {_scope_tail(x) for x in str(tok.get("scope") or "").split()}
    left = [x for x in asked if _scope_tail(YT_EXTRA[x]) not in granted]
    asked_left = [x for x in left if x in extra]
    lost = [x for x in left if x not in extra]
    if asked_left:                      # Google's consent screen lets each permission be unticked
        said += (f". Google did not grant {', '.join(asked_left)} (unticked at the prompt): connect again with "
                 f"--with {','.join(asked)} and tick it")
    if lost:
        said += (f". {acct.name} no longer has {', '.join(lost)} on YouTube, which it had before (unticked at the "
                 f"prompt): connect again with --with {','.join(asked)} and tick it to get it back")
    return said


def save_youtube(cfg: Optional[C.Config], acct: C.Account, client: dict, tok: dict) -> str:
    """The checked half of connect_youtube: which channel the sign-in reached, refused unless it is this account's,
    then the token file with the channel's id (the next account's connect compares against it)."""
    from . import direct as D
    out = TOKENS / acct.slug / "youtube_token.json"
    ch = yt_channel(tok["access_token"])
    why = yt_refusal(cfg, acct, ch)
    if why:
        raise SystemExit(f"nothing saved: {why}")
    D.write_private(out, json.dumps({"client_id": client["client_id"], "client_secret": client["client_secret"],
                                     "refresh_token": tok["refresh_token"], "access_token": tok["access_token"],
                                     "scope": tok.get("scope", ""),
                                     "expires_at": time.time() + int(tok.get("expires_in", 3600)),
                                     "channel_id": ch["id"], "channel_title": ch["title"],
                                     "channel_handle": ch["handle"]}))
    try:                                # what the sign-in may do, for the social parts (only the scope names)
        from . import capabilities as CAP
        CAP.save_scopes(acct, "youtube", str(tok.get("scope") or "").split(), "connect")
    except (Exception, SystemExit):  # noqa: BLE001  (a sign-in is never lost over its permission list)
        pass
    said = f"the channel {ch['title']}" + (f" ({ch['handle']})" if ch["handle"] else
                                            " (it has no @handle to check: make sure it is the right one)")
    # Google's consent page lets each permission be unticked: without YouTube Analytics the posting still works
    watch = "" if D.YT_ANALYTICS in tok.get("scope", "") else (
        "\nNo watch time: the YouTube Analytics permission was not ticked. Connect again and tick every box.")
    return f"connected {acct.name} to YouTube, {said}; token at {out} (mode 600){watch}"
