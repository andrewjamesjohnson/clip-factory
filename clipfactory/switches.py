"""The social layer's switches, per account: what is off, what practises, what is on, and the numbers it keeps to.

Every part ships off. The switches live in state/<account>/social/settings.json, never in config/accounts.json (no
tracked file changes when one is flipped, and they move with the account):

    {"v": 1, "modes": {"guard": "watch", "replies": "practice"}, "settings": {"created": "2026-09-01"},
     "acks": {"automations": {"by": "Andrew", "at": "..."}}, "history": [{"at", "by", "via", "key", "from", "to"}]}

A key that is absent, or holds a value it may not take, is "off" (mode) or its default (settings). Off means the part
does nothing at all; practice runs the whole path and logs the exact words with no network call; on (and testers, for
automations) sends. Going live with anything that speaks for the account (automations, first comments, card and story
posts, the Instagram poll, trial reels) needs the go-live checklist: practice long enough, no practice row over a cap
or a hold, "I read these" pressed on the Log page, the permission there, and for automations the account's creation
date. Every write keeps a history row and a dated copy in state/config-backups/social-<slug>-<stamp>.json.

    factory.py social on gaming first_comment.facebook_reels --practice
    factory.py social set gaming warmup_days 28
    factory.py social created gaming 2026-09-01

HARD holds the anti-flag numbers (per account and platform). The settings' "caps" may only lower them (and only
lengthen a gap), never raise them. state/social/settings.json holds what belongs to this machine as a whole: the
YouTube units it may spend in a day.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config as C
from . import store

# ------------------------------------------------------------------------------------------------------ the switches
OFF = "off"
LIVE = ("on", "testers")             # values that reach real people
_FC = ("off", "practice", "on")
MODES: Dict[str, Tuple[str, ...]] = {
    "guard": ("off", "watch", "on"),
    "engage.read": ("off", "on"),
    "engage.messages": ("off", "on"),
    "replies": _FC,
    "automations": ("off", "practice", "testers", "on"),
    "first_comment.facebook_reels": _FC,
    "first_comment.instagram_reels": _FC,
    "first_comment.youtube_shorts": _FC,
    "cards.make": ("off", "on"),
    "cards.post": _FC,
    "cards.story_repost": _FC,
    "coach": ("off", "on"),
    "checkpoints": ("off", "on"),
    "checkpoints.extra": ("off", "on"),
    "checkpoints.yt_analytics": ("off", "on"),
    "learn": ("off", "on"),
    "learn.playbook": ("off", "on"),
    "learn.experiments": _FC,
    "ideas": ("off", "on"),
    "poll": _FC,
    "trial_reels": _FC,
    "vs_card": ("off", "on"),        # THE VS CARD at collect (vscard.py, 2026-10-28)
    "cover": ("off", "on"),          # each Reel's cover: the clip's poster frame (covers.py, 2026-10-28)
    "cover_art": ("off", "on"),      # a designed cover made at collect, set where it can be (coverart.py, 2026-10-30)
    "pre_hook.fallback": ("off", "on"),  # the pre-hook on a clip that opens on nothing (prehook.py, 2026-10-29)
}

# the settings and their defaults (cards.kinds depends on the account's maker: CARD_KINDS)
SETTINGS: Dict[str, object] = {
    "created": "",                                   # YYYY-MM-DD the account was made (the warm-up clock)
    "warmup_days": 28,
    "quiet_hours": ["00:30", "07:00"],
    "testers": [],                                   # [{platform, id, name}]: people with a role on the Meta app
    "messages_access": "standard",                   # standard until Meta's review gives advanced
    "caps": {},                                      # lower only: {"reply_public/auto": {"per_hour": 3}}
    "engage.platforms": ["instagram", "facebook", "youtube"],
    "engage.poll_minutes": 10,
    "engage.youtube_minutes": 15,
    "engage.dm_minutes": 15,                         # Facebook's messages: every 30 at the least
    "engage.keep_days": 45,
    "first_comment.delay_s": [90, 300],
    "first_comment.youtube_delay_s": [600, 1200],
    "cards.kinds": [],
    "cards.targets": ["facebook_story"],
    "cards.times": {"countdown": "09:00", "clip_of_day": "20:30", "docket": "12:00", "quote_after_min": [60, 90],
                    "week_best": "Mon 19:00"},
    "cards.story_per_day": 4,
    "ideas.max_channels": 20,
    "learn.min_n": 12,
    "pre_hook.fallback_from": "",                    # YYYY-MM-DD: its first board ("" = every board while on)
}
CARD_KINDS = {"politics": ["quote", "docket", "week_best", "news"], "gta": ["countdown", "clip_of_day", "week_best"]}
ALL_CARD_KINDS = ("quote", "docket", "week_best", "countdown", "clip_of_day", "news")
PLATFORMS = ("instagram", "facebook", "youtube")

# ------------------------------------------------------------------------------------------------------ the numbers
# (kind, "manual" | "auto") -> (per hour, per day, gap seconds, jitter seconds), per account and platform.
# per hour None: posts go at their slots. Architecture section 5; caps in settings may only lower these.
HARD: Dict[Tuple[str, str], Tuple[Optional[int], int, int, int]] = {
    ("post", "auto"): (None, 25, 600, 0),
    ("post", "manual"): (None, 25, 600, 0),
    ("story", "auto"): (2, 6, 2700, 900),
    ("story", "manual"): (2, 6, 2700, 900),
    ("card_post", "auto"): (1, 5, 5400, 1200),     # 5 a day, 90 min apart (Andrew 10.28: "3 to 5 posts a day")
    ("card_post", "manual"): (1, 5, 5400, 1200),
    ("first_comment", "auto"): (6, 12, 60, 0),
    ("first_comment", "manual"): (6, 12, 60, 0),
    ("reply_public", "manual"): (20, 120, 30, 60),
    ("reply_public", "auto"): (6, 30, 60, 180),
    ("reply_private", "manual"): (20, 60, 30, 60),
    ("reply_private", "auto"): (8, 40, 90, 210),
    ("dm", "manual"): (20, 100, 20, 40),
    ("dm", "auto"): (6, 30, 60, 120),
    ("hide", "manual"): (30, 150, 5, 15),
    ("unhide", "manual"): (30, 150, 5, 15),
    ("yt_moderate", "manual"): (10, 10, 30, 0),
}
# a platform's own lower day: YouTube public replies by hand 25 (units); posts per platform a day
PLATFORM_DAY: Dict[Tuple[str, str, str], int] = {("youtube", "reply_public", "manual"): 25}
POST_DAY = {"instagram": 25, "facebook": 25, "youtube": 10}
FAMILY_HARD: Dict[str, Tuple[int, int]] = {"comment": (30, 150), "message": (25, 120), "moderation": (30, 150),
                                           "engagement": (40, 250)}
IG_PUBLISH_DAY = 25                  # every Instagram publish in 24 h (Reels, stories, cards): half of Meta's 50
REEL_GAP_S = 1800                    # a story or a card never within 30 minutes of a Reel
KINDS = sorted({k for k, _ in HARD})

# ------------------------------------------------------------------------------------------------------ the checklist
PRACTICE_MIN_DAYS = 2
OVER_WINDOW_DAYS = 2
# feature -> rows (practice rows that are enough), days (or this long in practice), kinds (the log rows it writes),
# outlet (only rows for this outlet), needs ((platform, need) for capabilities.missing)
CHECKLIST: Dict[str, dict] = {
    "automations": {"rows": 20, "days": 7, "kinds": ("reply_public", "reply_private", "dm"), "outlet": "",
                    "needs": (("instagram", "comment"), ("facebook", "comment"))},
    "first_comment.facebook_reels": {"rows": 10, "days": 7, "kinds": ("first_comment",), "outlet": "facebook_reels",
                                     "needs": (("facebook", "comment"),)},
    "first_comment.instagram_reels": {"rows": 10, "days": 7, "kinds": ("first_comment",),
                                      "outlet": "instagram_reels", "needs": (("instagram", "comment"),)},
    "first_comment.youtube_shorts": {"rows": 10, "days": 7, "kinds": ("first_comment",), "outlet": "youtube_shorts",
                                     "needs": (("youtube", "comment"),)},
    "cards.post": {"rows": 4, "days": 7, "kinds": ("card_post",), "outlet": "", "needs": ()},     # needs: its targets
    "cards.story_repost": {"rows": 4, "days": 7, "kinds": ("story",), "outlet": "", "needs": ()},
    "poll": {"rows": 3, "days": 14, "kinds": ("poll",), "outlet": "instagram_reels",
             "needs": (("instagram", "publish"),)},
    "trial_reels": {"rows": 3, "days": 14, "kinds": ("trial",), "outlet": "instagram_reels",
                    "needs": (("instagram", "publish"),)},
}

HISTORY_KEEP = 500
GLOBAL = {"v": 1, "youtube_units": 2500}
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------------------------------------------------ reading
def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def _maker(acct) -> str:
    return str(getattr(acct, "maker", "") or "")


def _is_client(acct) -> bool:
    raw = getattr(acct, "raw", None)
    return isinstance(raw, dict) and str(raw.get("owner") or "self") == "client"


def file_path(acct) -> Path:
    """state/<slug>/social/settings.json (at call time)."""
    return store.path(_slug(acct), "social", "settings.json")


_SEEN: Dict[str, tuple] = {}       # path -> ((size, mtime_ns), data): mode() runs for every card on every redraw


def _raw(acct) -> dict:
    p = file_path(acct)
    try:
        st = os.stat(p)
    except OSError:
        return {}
    key = (st.st_size, st.st_mtime_ns, st.st_ino)
    got = _SEEN.get(str(p))
    if got and got[0] == key:
        return got[1]
    data = store.read(p, {})
    data = data if isinstance(data, dict) else {}
    if len(_SEEN) > 200:
        _SEEN.clear()
    _SEEN[str(p)] = (key, data)
    return data


def _section(raw: dict, name: str) -> dict:
    v = raw.get(name)
    return v if isinstance(v, dict) else {}


def mode(acct, key: str) -> str:
    """One switch's value for the account: "off" when absent, unknown, of a value it may not take, for a client
    account, or when the file is broken."""
    if key not in MODES or _is_client(acct):
        return OFF
    v = _section(_raw(acct), "modes").get(key)
    return v if isinstance(v, str) and v in MODES[key] else OFF


def modes(acct) -> Dict[str, str]:
    """Every switch and its value."""
    return {k: mode(acct, k) for k in MODES}


def any_on(acct) -> bool:
    return any(v != OFF for v in modes(acct).values())


def _default(key: str, acct=None):
    if key == "cards.kinds":
        return list(CARD_KINDS.get(_maker(acct), []))
    v = SETTINGS[key]
    return json.loads(json.dumps(v))           # a copy: a caller never changes the defaults


def settings(acct) -> Dict[str, object]:
    """Every setting for the account: what settings.json holds when it is valid, else the default (flat keys, as
    SETTINGS)."""
    own = _section(_raw(acct), "settings")
    out: Dict[str, object] = {}
    for k in SETTINGS:
        if k in own:
            try:
                out[k] = _check_setting(k, own[k], acct)
                continue
            except SystemExit:
                pass
        out[k] = _default(k, acct)
    return out


def global_settings() -> Dict[str, object]:
    """state/social/settings.json over the defaults ({"v": 1, "youtube_units": 2500}): this machine's YouTube units
    a day for the social layer (2,500 is 50 writes)."""
    own = store.read(store.path("social", "settings.json"), {})
    out = dict(GLOBAL)
    if isinstance(own, dict):
        u = own.get("youtube_units")
        if isinstance(u, int) and not isinstance(u, bool) and 0 <= u <= 10000:
            out["youtube_units"] = u
    return out


# ------------------------------------------------------------------------------------------------------ checking values
def _int(key: str, v, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
        raise SystemExit(f"{key}: a whole number")
    v = int(v)
    if lo is not None and v < lo:
        raise SystemExit(f"{key}: {lo} at the least")
    if hi is not None and v > hi:
        raise SystemExit(f"{key}: {hi} at the most")
    return v


def _pair(key: str, v, lo: int, hi: int) -> List[int]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise SystemExit(f"{key}: two numbers of seconds, [from, to]")
    a, b = _int(key, v[0], lo, hi), _int(key, v[1], lo, hi)
    if a > b:
        raise SystemExit(f"{key}: the first number is the smaller one")
    return [a, b]


def _names(key: str, v, allowed) -> List[str]:
    if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) for x in v):
        raise SystemExit(f"{key}: a list of names")
    bad = [x for x in v if x not in allowed]
    if bad:
        raise SystemExit(f"{key}: {', '.join(bad)} is not one of {', '.join(allowed)}")
    return list(dict.fromkeys(v))


def _check_caps(v) -> dict:
    if not isinstance(v, dict):
        raise SystemExit('caps: {"reply_public/auto": {"per_hour": 3, "per_day": 20}}')
    out = {}
    for k, lim in v.items():
        kind, _, how = str(k).partition("/")
        hows = [how] if how else ["auto", "manual"]
        if how not in ("", "auto", "manual") or not any((kind, h) in HARD for h in hows):
            raise SystemExit(f"caps: {k} is not a kind (one of {', '.join(KINDS)}, with /auto or /manual)")
        if not isinstance(lim, dict):
            raise SystemExit(f"caps: {k}: per_hour, per_day, gap_s or jitter_s")
        clean = {}
        for name, n in lim.items():
            if name not in ("per_hour", "per_day", "gap_s", "jitter_s"):
                raise SystemExit(f"caps: {k}: {name} is not per_hour, per_day, gap_s or jitter_s")
            n = _int(f"caps {k} {name}", n, 0, 7 * 86400)
            for h in hows:
                hard = HARD.get((kind, h))
                if hard is None:
                    continue
                i = ("per_hour", "per_day", "gap_s", "jitter_s").index(name)
                if i < 2 and hard[i] is not None and n > hard[i]:
                    raise SystemExit(f"caps: {k} {name} {n} is above the ceiling {hard[i]}: caps may only lower it")
            clean[name] = n
        out[str(k)] = clean
    return out


_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun", "monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")


def _check_times(v) -> dict:
    """cards.times: only the known keys; HH:MM for countdown, clip_of_day and docket; "Ddd HH:MM" for week_best;
    quote_after_min two whole minutes [a, b], 30 <= a <= b."""
    if not isinstance(v, dict):
        raise SystemExit('cards.times: {"countdown": "09:00", ...}')
    known = tuple(SETTINGS["cards.times"])  # type: ignore
    out = {}
    for k, x in v.items():
        if k not in known:
            raise SystemExit(f"cards.times: {k} is not one of {', '.join(known)}")
        if k in ("countdown", "clip_of_day", "docket"):
            if not isinstance(x, str) or not _HHMM.match(x):
                raise SystemExit(f'cards.times: {k} is a clock time, "09:00"')
            out[k] = x
        elif k == "week_best":
            parts = x.split() if isinstance(x, str) else []
            if len(parts) != 2 or parts[0].lower() not in _WEEKDAYS or not _HHMM.match(parts[1]):
                raise SystemExit('cards.times: week_best is a day and a clock time, "Mon 19:00"')
            out[k] = x
        elif k == "quote_after_min":
            if not (isinstance(x, (list, tuple)) and len(x) == 2
                    and all(isinstance(n, int) and not isinstance(n, bool) for n in x) and 30 <= x[0] <= x[1]):
                raise SystemExit("cards.times: quote_after_min is two whole minutes [a, b], 30 <= a <= b")
            out[k] = [int(x[0]), int(x[1])]
    return out


def _check_setting(key: str, v, acct=None):
    """The value as stored, or SystemExit with what is wrong with it."""
    if key in ("created", "pre_hook.fallback_from"):
        if v == "":
            return ""
        if not isinstance(v, str) or not _DAY.match(v):
            raise SystemExit(f"{key}: YYYY-MM-DD")
        try:
            date.fromisoformat(v)
        except ValueError:
            raise SystemExit(f"{key}: YYYY-MM-DD, a real day") from None
        return v
    if key == "warmup_days":
        return _int(key, v, 14, 365)
    if key == "quiet_hours":
        if not isinstance(v, (list, tuple)) or len(v) != 2 or not all(isinstance(x, str) and _HHMM.match(x) for x in v):
            raise SystemExit('quiet_hours: two clock times, ["00:30", "07:00"]')
        return list(v)
    if key == "testers":
        if not isinstance(v, list):
            raise SystemExit("testers: a list of {platform, id, name}")
        out = []
        for t in v:
            if not isinstance(t, dict) or t.get("platform") not in PLATFORMS or not str(t.get("id") or "").strip():
                raise SystemExit("testers: each one {platform: instagram|facebook|youtube, id, name}")
            out.append({"platform": t["platform"], "id": str(t["id"]).strip(), "name": str(t.get("name") or "")})
        return out
    if key == "messages_access":
        if v not in ("standard", "advanced"):
            raise SystemExit("messages_access: standard or advanced")
        return v
    if key == "caps":
        return _check_caps(v)
    if key == "engage.platforms":
        return _names(key, v, PLATFORMS)
    if key == "engage.poll_minutes":
        return _int(key, v, 5, 1440)
    if key == "engage.youtube_minutes":
        return _int(key, v, 15, 1440)
    if key == "engage.dm_minutes":
        return _int(key, v, 15, 1440)
    if key == "engage.keep_days":
        return _int(key, v, 1, 45)
    if key == "first_comment.delay_s":
        return _pair(key, v, 60, 3600)
    if key == "first_comment.youtube_delay_s":
        return _pair(key, v, 60, 7200)
    if key == "cards.kinds":
        return _names(key, v, ALL_CARD_KINDS)
    if key == "cards.targets":
        if not isinstance(v, list) or not all(isinstance(x, str) and re.match(r"^(facebook|instagram)_[a-z_]+$", x)
                                              for x in v):
            raise SystemExit("cards.targets: a list such as facebook_story, instagram_story")
        return list(dict.fromkeys(v))
    if key == "cards.times":
        return _check_times(v)
    if key == "cards.story_per_day":
        return _int(key, v, 0, HARD[("story", "auto")][1])
    if key == "ideas.max_channels":
        return _int(key, v, 1, 20)
    if key == "learn.min_n":
        return _int(key, v, 6, 1000)
    raise SystemExit(f"{key}: not a setting; the settings are: {', '.join(SETTINGS)}")


def problems(acct) -> List[str]:
    """What is wrong in the account's settings.json: a broken file, an unknown key, a value a key may not take, a cap
    above its ceiling. [] when it is fine (or absent)."""
    p = file_path(acct)
    if not p.exists():
        return []
    raw = store.read(p, None)
    if not isinstance(raw, dict):
        return [f"{p} is not readable JSON (every part reads as off)"]
    out = []
    for k, v in _section(raw, "modes").items():
        if k not in MODES:
            out.append(f"{k} is not a switch")
        elif v not in MODES[k]:
            out.append(f"{k} is {v!r}, not one of {', '.join(MODES[k])} (read as off)")
    for k, v in _section(raw, "settings").items():
        if k not in SETTINGS:
            out.append(f"{k} is not a setting")
            continue
        try:
            _check_setting(k, v, acct)
        except SystemExit as e:
            out.append(f"{e} (the default is used)")
    return out


# ------------------------------------------------------------------------------------------------------ writing
def _now(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return _now(t).replace(microsecond=0).isoformat()


def _account(cfg: C.Config, acct) -> C.Account:
    a = cfg.account(acct) if isinstance(acct, str) else acct
    if _is_client(a):
        raise SystemExit(f"{a.name} is a client's account: the social layer is for the owner's own accounts")
    return a


def _save(cfg: C.Config, acct: C.Account, change, by: str, via: str, key: str, old, new,
          now: Optional[datetime]) -> None:
    """Change the file under its lock, add the history row, keep a dated copy."""
    t = _now(now)
    p = file_path(acct)
    with store.edit(p, dict) as raw:
        raw["v"] = 1
        change(raw)
        hist = raw.get("history") if isinstance(raw.get("history"), list) else []
        hist.append({"at": _iso(t), "by": str(by or ""), "via": via, "key": key, "from": old, "to": new})
        raw["history"] = hist[-HISTORY_KEEP:]
    backups = store.path("config-backups")
    backups.mkdir(parents=True, exist_ok=True)
    stamp = t.strftime("%Y%m%dT%H%M%S%f")
    copy, n = backups / f"social-{acct.slug}-{stamp}.json", 1
    while copy.exists():                    # two writes in one instant each keep their copy
        n += 1
        copy = backups / f"social-{acct.slug}-{stamp}-{n}.json"
    shutil.copy2(p, copy)


def _put(section: str, key: str, value):
    def change(raw: dict) -> None:
        d = raw.get(section) if isinstance(raw.get(section), dict) else {}
        d[key] = value
        raw[section] = d
    return change


def set_mode(cfg: C.Config, acct, key: str, value: str, by: str, page: bool = False,
             now: Optional[datetime] = None) -> str:
    """Flip one switch. A value that reaches real people (on, testers) on a part with a checklist is refused with
    the items still open. What it says."""
    a = _account(cfg, acct)
    if key not in MODES:
        raise SystemExit(f"{key}: not a switch; the switches are: {', '.join(MODES)}")
    value = str(value or "").strip().lower()
    if value not in MODES[key]:
        raise SystemExit(f"{key}: {value or 'nothing'} is not one of {', '.join(MODES[key])}")
    old = mode(a, key)
    if value == old:
        return f"{a.name}: {key} is already {value}"
    if value in LIVE and key in CHECKLIST:
        open_items = [w for done, w in checklist(cfg, a, key, now) if not done]
        if open_items:
            raise SystemExit(f"{a.name}: {key} stays {old}; before {value}: " + "; ".join(open_items))
    _save(cfg, a, _put("modes", key, value), by, "page" if page else "command", key, old, value, now)
    return f"{a.name}: {key} is now {value} (was {old})"


def set_setting(cfg: C.Config, acct, key: str, value, by: str, page: bool = False,
                now: Optional[datetime] = None) -> str:
    """Change one setting, checked for its type and its floor. What it says."""
    a = _account(cfg, acct)
    if key not in SETTINGS:
        raise SystemExit(f"{key}: not a setting; the settings are: {', '.join(SETTINGS)}")
    clean = _check_setting(key, value, a)
    old = settings(a).get(key)
    _save(cfg, a, _put("settings", key, clean), by, "page" if page else "command", key, old, clean, now)
    return f"{a.name}: {key} is now {json.dumps(clean)}"


def ack_practice(cfg: C.Config, acct, feature: str, by: str, page: bool, now: Optional[datetime] = None) -> str:
    """"I read these" on the Log page: he has read the practice rows of a part up to now (go-live item 3)."""
    if not page:
        raise SystemExit("Only a click on the review page can say the practice rows were read.")
    a = _account(cfg, acct)
    if feature not in CHECKLIST:
        raise SystemExit(f"{feature}: no practice rows to read (the parts with a checklist: {', '.join(CHECKLIST)})")
    entry = {"by": str(by or ""), "at": _iso(_now(now))}
    _save(cfg, a, _put("acks", feature, entry), by, "page", "ack:" + feature, None, entry["at"], now)
    return f"{a.name}: the practice rows of {feature} are read"


def add_tester(cfg: C.Config, acct, platform: str, pid: str, name: str, by: str,
               now: Optional[datetime] = None) -> str:
    """A person with a role on the Meta app (or his own YouTube): testers mode sends only to these."""
    a = _account(cfg, acct)
    t = _check_setting("testers", [{"platform": platform, "id": pid, "name": name}])[0]
    cur = [x for x in settings(a)["testers"] if (x["platform"], x["id"]) != (t["platform"], t["id"])]  # type: ignore
    new = cur + [t]
    _save(cfg, a, _put("settings", "testers", new), by, "command", "testers", len(cur), len(new), now)
    return f"{a.name}: {t['name'] or t['id']} on {t['platform']} is a tester"


def remove_tester(cfg: C.Config, acct, platform: str, pid: str, by: str, now: Optional[datetime] = None) -> str:
    a = _account(cfg, acct)
    cur = settings(a)["testers"]
    new = [x for x in cur if (x["platform"], x["id"]) != (platform, str(pid))]  # type: ignore
    if len(new) == len(cur):  # type: ignore
        return f"{a.name}: {platform} {pid} was not a tester"
    _save(cfg, a, _put("settings", "testers", new), by, "command", "testers", len(cur), len(new), now)  # type: ignore
    return f"{a.name}: {platform} {pid} is no longer a tester"


def is_tester(acct, platform: str, pid: Optional[str] = None) -> bool:
    """Is this person a tester? (platform, id), or one person key "instagram:1784..." as `platform`."""
    if pid is None:
        platform, _, pid = str(platform).partition(":")
    return any(t["platform"] == platform and t["id"] == str(pid) for t in settings(acct)["testers"])  # type: ignore


# ------------------------------------------------------------------------------------------------------ clocks
def _first_post(acct: C.Account) -> Optional[date]:
    """The day the factory first posted for the account (the earliest outlet with a post id on any board)."""
    from . import board as B
    first: Optional[date] = None
    for bp in B.all_boards(acct):
        bd = store.read(bp, {})
        for s in (bd.get("slots") or {}).values() if isinstance(bd, dict) else []:
            if not isinstance(s, dict):
                continue
            for o in (s.get("outlets") or {}).values():
                if not isinstance(o, dict) or not (o.get("post_id") or o.get("video_id")):
                    continue
                t = store._when(o.get("published_at") or o.get("at"))
                d = t.date() if t is not None else date.fromisoformat(bp.stem)
                if first is None or d < first:
                    first = d
        if first is not None:
            return first            # boards are oldest first: the first board with a post holds the earliest
    return first


def warmup_until(cfg: C.Config, acct) -> Optional[date]:
    """The day automated engagement may start: the later of created + warmup_days and the factory's first post + 14
    days. None while it cannot be known (no creation date set, or nothing posted yet): still warming up."""
    a = cfg.account(acct) if isinstance(acct, str) else acct
    s = settings(a)
    if not s["created"]:
        return None
    first = _first_post(a)
    if first is None:
        return None
    return max(date.fromisoformat(str(s["created"])) + timedelta(days=int(s["warmup_days"])),  # type: ignore
               first + timedelta(days=14))


def quiet(cfg: C.Config, acct, now: Optional[datetime] = None) -> Optional[datetime]:
    """Inside the account's quiet hours (00:30 to 07:00 by default, local time): the moment they end. None outside
    them. Automated engagement waits until then."""
    tz = cfg.tz()
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    start, end = settings(acct)["quiet_hours"]  # type: ignore
    sh, sm = (int(x) for x in str(start).split(":"))
    eh, em = (int(x) for x in str(end).split(":"))
    s = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    e = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    if s == e:
        return None
    if s < e:                       # 00:30 to 07:00 on one day
        return e if s <= now < e else None
    if now >= s:                    # 23:00 to 06:00 across midnight
        return e + timedelta(days=1)
    return e if now < e else None


def caps_for(acct, kind: str, manual: bool) -> Tuple[Optional[int], int, int, int]:
    """(per hour, per day, gap s, jitter s) for one kind: HARD, lowered by the account's caps (a gap or a jitter
    only lengthened). A kind with no row for that way of sending gives zeros (never allowed)."""
    how = "manual" if manual else "auto"
    hard = HARD.get((kind, how))
    if hard is None:
        return (0, 0, 0, 0)
    ph, pd, gap, jit = hard
    caps = settings(acct)["caps"]
    for k in (kind, f"{kind}/{how}"):
        lim = caps.get(k) if isinstance(caps, dict) else None
        if not isinstance(lim, dict):
            continue
        if isinstance(lim.get("per_hour"), int):
            ph = lim["per_hour"] if ph is None else min(ph, lim["per_hour"])
        if isinstance(lim.get("per_day"), int):
            pd = min(pd, lim["per_day"])
        if isinstance(lim.get("gap_s"), int):
            gap = max(gap, lim["gap_s"])
        if isinstance(lim.get("jitter_s"), int):
            jit = max(jit, lim["jitter_s"])
    return (ph, pd, gap, jit)


# ------------------------------------------------------------------------------------------------------ the checklist
def practice_since(acct, key: str) -> Optional[datetime]:
    """When the switch went to practice (or testers) and has stayed there since; None when it is not practising."""
    if mode(acct, key) not in ("practice", "testers"):
        return None
    since = None
    hist = _raw(acct).get("history")
    for h in reversed(hist if isinstance(hist, list) else []):
        if not isinstance(h, dict) or h.get("key") != key:
            continue
        if h.get("to") in ("practice", "testers"):
            since = store._when(h.get("at"))
            continue
        break
    return since


def _feature_rows(cfg: C.Config, acct, feature: str, since: datetime, would_only: bool = True) -> List[dict]:
    """The practice rows outward logged for this part since then (none while outward is not built). A row counts
    for a part when its "feature" names it, or its kind is one of the part's kinds (and its outlet the part's).
    Only the part's own runs count: never his own clicks (signed by the click, governed by `replies`), and for
    automations only a rule's (origin.by "rule"). would_only (the default): only the would-sends (state "practice");
    False keeps every state of the part's practice rows (skipped, held, refused), for the cap and hold scan."""
    from . import registry as REG
    f = REG.fn("outward:rows")
    if f is None:
        return []
    spec = CHECKLIST[feature]
    out = []
    try:
        for r in f(cfg, _slug(acct), since=since):
            if not isinstance(r, dict) or r.get("mode") != "practice":
                continue
            if would_only and r.get("state") != "practice":
                continue
            if str((r.get("signed") if isinstance(r.get("signed"), dict) else {}).get("what") or "") == "click":
                continue
            if feature == "automations" and \
                    str((r.get("origin") if isinstance(r.get("origin"), dict) else {}).get("by") or "") != "rule":
                continue
            if r.get("feature") == feature or (r.get("kind") in spec["kinds"] and
                                               (not spec["outlet"] or r.get("outlet") == spec["outlet"])):
                out.append(r)
    except Exception:  # noqa: BLE001  (an unreadable log keeps the item open, never raises)
        return []
    return out


def _needs(acct, feature: str) -> List[Tuple[str, str]]:
    needs = list(CHECKLIST[feature]["needs"])
    if feature in ("cards.post", "cards.story_repost"):
        for t in settings(acct)["cards.targets"]:  # type: ignore
            plat, _, what = str(t).partition("_")
            needs.append((plat, "publish_story" if what == "story" else "publish"))
    return needs


def checklist(cfg: C.Config, acct, feature: str, now: Optional[datetime] = None) -> List[Tuple[bool, str]]:
    """The go-live checklist of a part, each item (done, words). [] for a part that has none."""
    if feature not in CHECKLIST:
        return []
    from . import registry as REG
    spec = CHECKLIST[feature]
    now = _now(now)
    out: List[Tuple[bool, str]] = []
    since = practice_since(acct, feature)
    rows = _feature_rows(cfg, acct, feature, since) if since is not None else []             # the would-sends
    seen = _feature_rows(cfg, acct, feature, since, would_only=False) if since is not None else []
    days = (now - since).total_seconds() / 86400 if since is not None else 0.0
    enough = days >= PRACTICE_MIN_DAYS and (len(rows) >= spec["rows"] or days >= spec["days"])
    so_far = (f"{int(days)} days and {len(rows)} rows so far" if since is not None else "not practising now")
    out.append((enough, f"practice for {PRACTICE_MIN_DAYS} days and {spec['rows']} rows (or {spec['days']} days): "
                        f"{so_far}"))
    window = now - timedelta(days=OVER_WINDOW_DAYS)
    recent = [r for r in seen if (store._when(r.get("t") or r.get("at")) or now) >= window]
    over = [r for r in recent if r.get("state") in ("held", "refused") or str(r.get("code") or "") in ("cap", "hold")]
    out.append((since is not None and not over,
                f"no practice row over a cap or a hold in the last {OVER_WINDOW_DAYS} days"
                + (f": {len(over)} were" if over else "")))
    ack = store._when((_section(_raw(acct), "acks").get(feature) or {}).get("at"))
    last = max((store._when(r.get("t") or r.get("at")) for r in seen if store._when(r.get("t") or r.get("at"))),
               default=None)
    out.append((bool(rows) and ack is not None and last is not None and ack >= last,
                '"I read these" pressed on the Log page after the last practice rows'))
    missing: List[str] = []
    f = REG.fn("capabilities:missing")
    if f is None:
        missing = ["(the permission check, clipfactory/capabilities.py, is not built yet)"]
    else:
        for plat, need in _needs(acct, feature):
            try:
                got = f(acct, plat, need)
            except Exception as e:  # noqa: BLE001  (an unreadable permission file keeps the item open)
                got = [f"(cannot read: {type(e).__name__})"]
            missing += [str(x) for x in (got or [])]
    out.append((not missing, "the permission is there" + (": missing " + ", ".join(missing) if missing else "")))
    if feature == "automations":
        out.append((bool(settings(acct)["created"]), "the account's creation date is set "
                                                     f"(factory.py social created {_slug(acct)} YYYY-MM-DD)"))
    return out


# ------------------------------------------------------------------------------------------------------ doctor
def _on_words(acct) -> str:
    on = [f"{k} {v}" for k, v in modes(acct).items() if v != OFF]
    return "on: " + ", ".join(on) if on else "every social part is off"


def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """One "--" line per owner account saying which parts are on, and what the guard waits for while it is off (no
    other part speaks for it); a NO for each bad value in its settings.json."""
    out: List[Tuple[Optional[bool], str, str]] = []
    for a in cfg.accounts.values():
        if _is_client(a):
            continue
        out.append((None, f"{a.name}: {_on_words(a)}", ""))
        if mode(a, "guard") == OFF:
            out.append((None, f"{a.name} guard: off (watch it first: factory.py social set {a.slug} guard watch; "
                              f"then factory.py social on {a.slug} guard)", ""))
        for p in problems(a):
            out.append((False, f"{a.name} social settings: {p}",
                        f"factory.py social set {a.slug} <key> <value>, or fix state/{a.slug}/social/settings.json"))
    return out


def history(acct) -> List[dict]:
    h = _raw(acct).get("history")
    return [x for x in h if isinstance(x, dict)] if isinstance(h, list) else []


def is_live(acct, key: str) -> bool:
    return mode(acct, key) in LIVE
