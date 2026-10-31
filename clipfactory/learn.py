"""The learner: every morning, what went wrong, the best and worst posts, what works and how sure, and proposals.

At 04:40 (`factory.py social learn`, for each account whose `learn` switch is on), or by hand at any time with
`factory.py learn daily` (no switch needed, offline, files only), it reads what is already on disk:

    the day boards            every slot, what was signed, what posted where and when, why a post gave up
    state/stats/series/*      each post's checkpoint numbers (1h 3h 6h 24h 72h 7d 30d), when checkpoints are on
    state/stats/history/*     the stats snapshots: until checkpoints run, the 24-hour number is read off these
                              (between two snapshots, or stretched along a gentle curve, and labelled an estimate)
    coach/<day>/<HHMM>.json   the coach's measured and written scores, when the coach is on
    learn/verdicts.jsonl      Approve, Decline and Make again with their reasons
    scout/decisions.json      which sources the scout picked or fed
    extras/<day>/<HHMM>.json  the first comment, poll, trial and experiment arm signed with each clip

and writes, under state/<slug>/learn/:

    features.json             one row per post per platform (never pooled across platforms): what the clip was,
                              when it went up, its views at each checkpoint, its 24-hour baseline and rel_24h =
                              ln((v24 + 1) / (baseline + 1)), the baseline (the usual) being the median 24-hour views
                              of the same account and platform over the 14 days before the post (30 when fewer than
                              5), only posts that went up before it, 5 at least: none until then
    report-YYYY-MM-DD.json    problems ranked by views lost (with the fix; a sudden drop on a platform and a platform
                              far behind the best one among them), best and worst posts with why, what
    report-YYYY-MM-DD.md      works (n, lift, 90% range, how sure), coach accuracy, publishing health, proposals made,
                              experiments and "did it work" for each rule 14 days after it was applied
    proposals.json            what it proposes; Andrew approves or declines each (Reports tab, or learn approve)
    experiments.json          A/B tests with fixed assignment, at least 10 clips an arm, 21 days at most
    notes-YYYY-MM-DD.json     the results coach's words (what happened in the best and worst clips), shown in the report

and, when config/accounts.json names a "report_desk" folder, the morning file there: Clip report MM.DD.YYYY.md, both
accounts, "In one minute" first, then every clip best to worst with each platform's views, % watched, % skipped and
reach (the board's latest read), and the problems.

How sure: "strong" needs 20 posts on each side, a 90% range that leaves out 1x, q <= 0.1 and the same direction in
the older and the newer half; "likely" needs 12 (settings learn.min_n) and q <= 0.2; "early sign" is 6 a side, shown
and never proposed; and nothing is claimed for a platform until it has 30 posts with a 24-hour number.

Proposals change only the whitelist (scout factors, coach settings, runbook lines, experiments, draft automations);
everything else is advice. Approved ones go into the playbook (playbook.py). Nothing here reaches the network.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import board as B
from . import config as C
from . import playbook as PB
from . import registry as REG
from . import statmath as SM
from . import stats as ST
from . import store
from . import switches as SW
from . import verdicts as VD

V = 1
LABELS = (("1h", 60), ("3h", 180), ("6h", 360), ("24h", 1440), ("72h", 4320), ("7d", 10080), ("30d", 43200))
LABEL_MIN = dict(LABELS)
DASH = {"youtube_shorts": "youtube", "facebook_reels": "facebook", "instagram_reels": "instagram", "tiktok": "tiktok"}
OUTLET_WORDS = {"youtube_shorts": "YouTube", "facebook_reels": "Facebook", "instagram_reels": "Instagram",
                "tiktok": "TikTok"}
CLAIM_MIN = 30                  # posts with a 24-hour number on a platform before anything is claimed for it
WATCH_KEYS = ("watch_pct", "watch_s", "skip_pct", "reach")     # the board's latest read (stats.WATCH), per post
HELD_X, HELD_TOOK_X, HELD_MIN_USUAL = 0.1, 0.5, 20   # held back: under 0.1x where the usual is 20 or more, while
                                                     # another platform took the same clip at 0.5x or more
STRONG_N = 20
EARLY_N = 6
BASE_DAYS, BASE_FALLBACK_DAYS, BASE_MIN = 14, 30, 5
KEEP_DAYS = 120                 # features and reports kept this long
PROPOSAL_DAYS = 14
DID_IT_WORK_DAYS = 14
LATE_MIN = 15                   # minutes after its slot before a post counts as late
SLOW_REL = math.log(0.5)        # a first day under half the usual
BOOT_N = 2000
CONF_ORDER = {"strong": 0, "likely": 1, "early sign": 2, "": 3}
CONF_WORDS = {"strong": "strong", "likely": "likely", "early sign": "early sign: not enough posts yet"}
DECLINE = (("not_enough", "Not enough evidence"), ("against_rule", "Against a standing rule"),
           ("test_first", "Test it first"), ("wrong_reading", "Wrong reading"), ("other", "Other"))
DECLINE_WORDS = dict(DECLINE)
KINDS = ("factor", "coach", "runbook_note", "advice", "experiment", "draft_rule", "revert")
FILE_KINDS = ("runbook_note", "advice", "experiment", "draft_rule")      # what `learn propose --file` may add
EXP_KINDS = ("first_comment", "trial_reel", "pre_hook", "punch_cut")   # a test applied clip by clip (no "tag")
# "given": the arm is the clip's own, set where the change is made (the engine's punch cuts are cut at render, so
# the engine draws the arm and writes it in the clip's plates file; learn reads it, never chooses it)
ASSIGN = ("slot_parity", "day_parity", "alternate", "trial_reel", "given")
JOBS_DIR = Path.home() / "Desktop" / "Clipping" / "Jobs"


def _clip_punch(slot: Optional[dict]) -> dict:
    """The engine's punch-cut record for this clip (<job>/clips/<stem>.cards.json "punch_cuts": eligible, arm, ...),
    found from the draft's cast-check.json; {} when there is none."""
    try:
        clip = Path(str(((slot or {}).get("clip") or {}).get("path") or ""))
        job = str(json.loads((clip.parent / "cast-check.json").read_text()).get("job") or "")
        rec = json.loads((JOBS_DIR / job / "clips" / f"{clip.stem}.cards.json").read_text())
    except (OSError, ValueError, TypeError):
        return {}
    pc = rec.get("punch_cuts") if isinstance(rec, dict) else None
    return dict(pc) if isinstance(pc, dict) else {}
EXP_MIN_N, EXP_MAX_DAYS = 10, 21
INTENTS = ("asks_source", "asks_full", "asks_who", "asks_when", "asks_which", "asks_part2", "question")
INTENT_WORDS = {"asks_source": "where is this from", "asks_full": "where is the full video", "asks_who": "who is that",
                "asks_when": "when was this", "asks_which": "which one is this", "asks_part2": "is there a part 2",
                "question": "other questions"}
MINUTES_SAVED = {"post": 3, "first_comment": 1, "reply_public": 0.5, "reply_private": 1, "dm": 1, "hide": 0.25,
                 "unhide": 0.25, "story": 2, "card_post": 3}
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
EMPTY_STATUSES = {"open": "empty", "making": "empty", "declined": "declined", "refused": "refused", "failed": "failed",
                  "ready": "unsigned"}


# ====================================================================================================== small helpers
def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def _is_client(acct) -> bool:
    raw = getattr(acct, "raw", None)
    return isinstance(raw, dict) and str(raw.get("owner") or "self") == "client"


def _owners(cfg: C.Config) -> List[C.Account]:
    return [a for a in cfg.accounts.values() if not _is_client(a)]


def _when(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _now(cfg: C.Config, now: Optional[datetime] = None) -> datetime:
    now = now or cfg.now()
    return now if now.tzinfo else now.replace(tzinfo=cfg.tz())


def _local(cfg: C.Config, t: datetime) -> datetime:
    return t.astimezone(cfg.tz())


def _s(n: int, one: str = "", many: str = "s") -> str:
    return one if n == 1 else many


def k_words(n) -> str:
    """12400 -> 12.4K, 19177 -> 19.2K, 980 -> 980."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0"
    if abs(n) >= 1e6:
        return f"{n / 1e6:.1f}M"
    if abs(n) >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{n:.0f}"


def x_words(x) -> str:
    """How many times the usual: 0.4x, 2.4x, 825x; under 0.1x."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return ""
    if x < 0.1:
        return "under 0.1x"
    return f"{x:.0f}x" if x >= 10 else f"{x:.1f}x"


def upper_first(s) -> str:
    """The first letter a capital and nothing else touched ("clips from C-SPAN" -> "Clips from C-SPAN"; str.capitalize
    would lower the rest)."""
    s = str(s or "")
    return s[:1].upper() + s[1:]


def _int_words(n) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "0"


def _dir(slug: str) -> Path:
    return store.path(slug, "learn")


def features_path(acct) -> Path:
    return _dir(_slug(acct)) / "features.json"


def report_path(acct, day, ext: str = "json") -> Path:
    d = day.isoformat() if isinstance(day, date) else str(day)
    return _dir(_slug(acct)) / f"report-{d}.{ext}"


def proposals_path(acct) -> Path:
    return _dir(_slug(acct)) / "proposals.json"


def experiments_path(acct) -> Path:
    return _dir(_slug(acct)) / "experiments.json"


def did_path(acct) -> Path:
    return _dir(_slug(acct)) / "did-it-work.json"


def weekly_dir(acct) -> Path:
    return _dir(_slug(acct)) / "weekly"


def _write_text(p: Path, text: str) -> None:
    """A text file under the state folder, written whole (tmp, then os.replace)."""
    p = store._inside(Path(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


def _plain(text: str) -> str:
    """Generated words never carry a long dash or an exclamation mark."""
    return str(text).replace("\u2014", ":").replace("\u2013", " to ").replace("!", ".")


# ====================================================================================================== reading
def _boards(acct) -> List[Tuple[date, dict]]:
    out = []
    for p in B.all_boards(acct):
        try:
            d = date.fromisoformat(p.stem)
            bd = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(bd, dict) and isinstance(bd.get("slots"), dict):
            out.append((d, bd))
    return out


def _history() -> Dict[str, List[Tuple[float, float]]]:
    """Every stats snapshot: post id -> [(read at, epoch seconds; views)]."""
    out: Dict[str, List[Tuple[float, float]]] = {}
    hd = store.path("stats", "history")
    for f in sorted(hd.glob("*.json")) if hd.is_dir() else []:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem):
            continue
        data = store.read(f, {})
        for r in (data.get("reads") or []) if isinstance(data, dict) else []:
            at = _when(r.get("at")) if isinstance(r, dict) else None
            if at is None or not isinstance(r.get("posts"), dict):
                continue
            for pid, vals in r["posts"].items():
                try:
                    out.setdefault(pid, []).append((at.timestamp(), float(vals[0] or 0)))
                except (TypeError, ValueError, IndexError):
                    continue
    return out


def _series(slug: str, outlet: str) -> Dict[str, dict]:
    """The checkpoint series for one account and platform: platform id -> its record (checkpoints.py)."""
    out: Dict[str, dict] = {}
    sd = store.path("stats", "series")
    for f in sorted(sd.glob(f"{slug}__{outlet}__*.json")) if sd.is_dir() else []:
        data = store.read(f, {})
        if isinstance(data, dict):
            out.update({str(k): v for k, v in data.items() if isinstance(v, dict)})
    return out


def _coach(slug: str, day: str, slot: str) -> dict:
    d = store.read(store.path(slug, "coach", day, slot.replace(":", "") + ".json"), {})
    return d if isinstance(d, dict) else {}


def _decisions(slug: str) -> dict:
    d = store.read(store.path(slug, "scout", "decisions.json"), {})
    return d if isinstance(d, dict) else {}


def _reassign() -> list:
    try:
        from . import statspage as SP
        return list(SP.settings().get("reassign") or [])
    except Exception:  # noqa: BLE001
        return []


def _content_account(p: dict, rules: list) -> str:
    try:
        from . import statspage as SP
        return SP.content_account(p, rules)
    except Exception:  # noqa: BLE001
        return str(p.get("account") or "")


def _extras(slot: dict) -> dict:
    try:
        from . import extras as EX
        return EX.read(slot) or {}
    except Exception:  # noqa: BLE001
        return {}


_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})")


def _video_id(url) -> str:
    m = _YT_ID.search(str(url or ""))
    return m.group(1) if m else ""


# ====================================================================================================== features
_CAPS_WORD = re.compile(r"^[A-Z][A-Z0-9']+$")


def title_feat(title: str) -> dict:
    """What a title has: a number, a question, a quote (quotation marks, or words after a colon), a name in its
    middle (a capital not at the start), the share of words in capitals, its length."""
    t = str(title or "").strip()
    words = t.split()
    caps = [w for w in words if len(w) >= 2 and _CAPS_WORD.match(re.sub(r"[^\w']", "", w) or "x")]
    mid = [w for w in words[1:] if w[:1].isalpha()]
    return {"number": bool(re.search(r"\d", t)), "question": "?" in t,
            "quote": bool(re.search(r"[\"\u201c\u201d]", t) or re.search(r":\s*\S", t)),
            "name": any(w[:1].isupper() for w in mid) and not all(w[:1].isupper() for w in mid),
            "caps_ratio": round(len(caps) / len(words), 2) if words else 0.0, "chars": len(t)}


def _curve(h: float) -> float:
    """How a short's views usually build: fast early, slower later (a log curve). Only for estimates."""
    return math.log1p(max(0.0, h) / 2.0)


def _at_age(pts: List[Tuple[float, float]], target: float) -> Optional[float]:
    """Views at `target` minutes, read between the two closest points around it (None unless there is one on
    each side, or one within 10 minutes)."""
    before = [p for p in pts if p[0] <= target]
    after = [p for p in pts if p[0] >= target]
    near = [p for p in pts if abs(p[0] - target) <= 10]
    if near:
        return min(near, key=lambda p: abs(p[0] - target))[1]
    if not before or not after:
        return None
    a = max(before, key=lambda p: p[0])
    b = min(after, key=lambda p: p[0])
    if b[0] == a[0]:
        return a[1]
    return a[1] + (b[1] - a[1]) * (target - a[0]) / (b[0] - a[0])


def views_at(points: List[Tuple[float, float]], series: Dict[str, float], age_min_now: float
             ) -> Tuple[Dict[str, int], str]:
    """(views by label, where the 24-hour number came from: "series" | "snapshots" | "estimate" | ""). A checkpoint
    point wins; then two snapshots on either side of the age; for 24 hours only, a single side stretched along the
    curve (never below a reading before 24 hours, never above one after)."""
    pts = sorted((a, v) for a, v in points if a >= 0)
    out: Dict[str, int] = {}
    src = ""
    for label, mins in LABELS:
        if label in series:
            out[label] = int(round(series[label]))
            if label == "24h":
                src = "series"
            continue
        if age_min_now < mins:
            continue
        v = _at_age(pts, mins)
        if v is not None:
            out[label] = int(round(v))
            if label == "24h":
                src = "snapshots"
    if "24h" not in out and age_min_now >= 1440 and pts:
        lo_cap = max([v for a, v in pts if a <= 1440], default=0.0)
        hi_cap = min([v for a, v in pts if a >= 1440], default=float("inf"))
        after = [p for p in pts if p[0] >= 1440]
        ref = min(after, key=lambda p: p[0]) if after else max(pts, key=lambda p: p[0])
        h = ref[0] / 60.0
        est = ref[1] * (_curve(24.0) / _curve(h)) if _curve(h) > 0 else ref[1]
        est = min(hi_cap, max(lo_cap, est))
        out["24h"] = int(round(est))
        src = "estimate"
    return out, src


def _pb_version_at(hist: List[dict], went: datetime) -> int:
    v = 0
    for r in hist:
        at = _when(r.get("at"))
        if at is not None and at <= went and isinstance(r.get("version"), int):
            v = r["version"]
    return v


def _hour_band(h) -> Optional[str]:
    if h is None:
        return None
    for lo, hi in ((0, 6), (6, 9), (9, 12), (12, 15), (15, 18), (18, 21), (21, 24)):
        if lo <= int(h) < hi:
            return f"{lo:02d}:00 to {hi % 24:02d}:00"
    return None


LENGTH_BANDS = (("under 30s", 0, 30), ("30 to 45s", 30, 45), ("45 to 60s", 45, 60), ("60 to 75s", 60, 75),
                ("75s and over", 75, 181))


def _length_band(s) -> Optional[str]:
    try:
        s = float(s or 0)
    except (TypeError, ValueError):
        return None
    if s <= 0:
        return None
    for name, lo, hi in LENGTH_BANDS:
        if lo <= s < hi:
            return name
    return "75s and over"


def _first_word(x) -> Optional[str]:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return None
    return "under 0.5s" if x < 0.5 else "0.5 to 1.5s" if x <= 1.5 else "over 1.5s"


def _scenes(n) -> Optional[str]:
    if not isinstance(n, (int, float)) or isinstance(n, bool):
        return None
    return "no cut" if n <= 0 else "1 or 2 cuts" if n <= 2 else "3 or more cuts"


def _flag(v) -> Optional[bool]:
    return bool(v) if isinstance(v, bool) else None


# (key, how it reads, level of a row, binary)
FACTORS = (
    ("format", lambda r: r.get("format") or None, False),
    ("creator", lambda r: r.get("creator_key") or None, False),
    ("hour", lambda r: _hour_band(r.get("hour")), False),
    ("weekday", lambda r: DAY_NAMES[r["dow"]] if isinstance(r.get("dow"), int) else None, False),
    ("length", lambda r: _length_band(r.get("sent_s")), False),
    ("title_number", lambda r: _flag((r.get("title_feat") or {}).get("number")), True),
    ("title_question", lambda r: _flag((r.get("title_feat") or {}).get("question")), True),
    ("title_quote", lambda r: _flag((r.get("title_feat") or {}).get("quote")), True),
    ("title_name", lambda r: _flag((r.get("title_feat") or {}).get("name")), True),
    ("title_caps", lambda r: (float((r.get("title_feat") or {}).get("caps_ratio") or 0) >= 0.3)
     if r.get("title_feat") else None, True),
    ("first_word", lambda r: _first_word((r.get("hook_feat") or {}).get("first_word_s")), False),
    ("scenes", lambda r: _scenes((r.get("hook_feat") or {}).get("scene_changes_3s")), False),
    ("hook_on_screen", lambda r: _flag((r.get("hook_feat") or {}).get("hook_on_screen")), True),
    ("first_comment", lambda r: _flag((r.get("extras") or {}).get("first_comment")), True),
    ("cut", lambda r: _flag(r.get("cut")), True),
    ("source", lambda r: r.get("source") or None, False),
)
FACTOR_OF = {k: (f, b) for k, f, b in FACTORS}
SOURCE_WORDS = {"bench": "the bench", "scout": "the scout's picks", "night": "the night run's own picks",
                "drop folder": "the drop folder"}


def factor_words(factor: str, level, name: str = "") -> str:
    if factor == "format":
        return f"the {level} format"
    if factor == "creator":
        return f"clips from {name or level}"
    if factor == "hour":
        return f"posts at {level}"
    if factor == "weekday":
        return f"posts on {level}"
    if factor == "length":
        return f"clips {level} long"
    if factor == "first_word":
        return f"the first word {level} in"
    if factor == "scenes":
        return f"{level} in the first 3 seconds"
    if factor == "source":
        return f"clips from {SOURCE_WORDS.get(level, level)}"
    return {"title_number": "a number in the title", "title_question": "a question in the title",
            "title_quote": "a quote in the title", "title_name": "a name in the title",
            "title_caps": "capital letters in the title", "hook_on_screen": "the hook on screen",
            "first_comment": "a first comment", "cut": "its own cut for the platform"}.get(factor, f"{factor} {level}")


def rows_for(cfg: C.Config, acct, now: Optional[datetime] = None,
             hist: Optional[Dict[str, List[Tuple[float, float]]]] = None) -> List[dict]:
    """One row per factory post per platform (a by-hand TikTok test, a post another account's content took by the
    stats reassign rules, a cap-test post past the usual daily cap (poster._daily_cap: its numbers say nothing about
    the clip), and a record with no post id are left out)."""
    now = _now(cfg, now)
    slug = _slug(acct)
    hist = _history() if hist is None else hist
    rules = _reassign()
    dec = _decisions(slug)
    pb_hist = PB.history(acct)
    series = {o: _series(slug, o) for o in DASH}
    from . import checkpoints as CP          # one rule for what a checkpoint point stands for
    rows: List[dict] = []
    for day, bd in _boards(acct):
        for k, s in sorted(bd["slots"].items()):
            if not isinstance(s, dict):
                continue
            outs = s.get("outlets") or {}
            x = None
            for outlet, rec in sorted(outs.items()):
                if outlet not in DASH or not isinstance(rec, dict):
                    continue
                if not (rec.get("post_id") or rec.get("video_id")) or rec.get("privacy") == ST.PRIVATE:
                    continue
                if rec.get("past_usual_cap"):
                    continue                    # a cap-test post past the usual cap: Facebook barely shows those
                pid = str(rec.get("platform_id") or rec.get("video_id") or rec.get("publish_id")
                          or rec.get("post_id") or "")
                try:
                    went = _when(ST.went_public(outlet, rec, s))
                except (KeyError, TypeError):
                    went = None
                if went is None or not pid:
                    continue
                loc = _local(cfg, went)
                if _content_account({"account": slug, "platform": DASH[outlet], "day": loc.date().isoformat(),
                                     "pid": pid}, rules) != slug:
                    continue
                if x is None:
                    x = _extras(s)
                pt = {"account": slug, "platform": DASH[outlet], "pid": pid, "url": rec.get("url", ""),
                      "title": "", "at": went.isoformat()}
                m = rec.get("metrics") or {}
                m_at = _when(m.get("at"))
                # a snapshot holds every post, read or not (an unread one at 0), and after a post's last read a copy
                # of that read: only points up to the record's last read are readings, and never a 0 (the fill of a
                # post no read had reached yet; a real 0 says nothing views do not already say)
                points = [((at - went.timestamp()) / 60.0, v) for at, v in hist.get(ST.post_id(pt), [])
                          if m_at is not None and at <= m_at.timestamp() and v > 0]
                if m_at is not None and isinstance(m.get("views"), (int, float)):
                    points.append(((m_at - went).total_seconds() / 60.0, float(m["views"])))
                ser = series[outlet].get(pid) or {}
                # a point read late (the Mac slept) or early (a backfill took a snapshot hours before the time) is not
                # that checkpoint's number: the snapshots around the time stand in, as for a post with no series
                ser_pts = {str(p.get("label")): float(p["views"]) for p in ser.get("points") or []
                           if isinstance(p, dict) and isinstance(p.get("views"), (int, float)) and CP.on_time(p)}
                age_min = (now - went).total_seconds() / 60.0
                views, src = views_at(points, ser_pts, age_min)
                coach = _coach(slug, day.isoformat(), k)
                clip_sha = str((s.get("clip") or {}).get("sha256") or "")
                if coach.get("clip_sha") and clip_sha and coach.get("clip_sha") != clip_sha:
                    coach = {}                                   # a scorecard for an older clip in this slot
                sig = coach.get("signals") or {}
                scores = ((coach.get("written") or {}).get("scores") or {})
                vals = [float(v) for v in (scores.values() if isinstance(scores, dict) else scores or [])
                        if isinstance(v, (int, float)) and not isinstance(v, bool)]
                fc = (x.get("first_comment") or {}) if isinstance(x, dict) else {}
                fc_state = str(((rec.get("social") or {}).get("first_comment") or {}).get("state") or "") \
                    if isinstance(rec.get("social"), dict) else ""
                if fc_state == "done":
                    has_fc = True                                # posted
                elif not x:
                    has_fc = None
                elif not fc.get(outlet):
                    has_fc = False                               # signed with none for this platform
                else:
                    has_fc = None                                # practice, waiting, failed: not known to be up
                try:
                    pa = datetime.fromisoformat(str(s["publish_at"]))
                    late = max(0.0, (went - pa).total_seconds() / 60.0)
                except (KeyError, TypeError, ValueError):
                    late = 0.0
                vid = _video_id(s.get("source_url"))
                bench = (s.get("bench") or {}).get("id", "") if (s.get("bench") or {}).get("slug") == s.get("slug") \
                    else ""
                if bench:
                    source = "bench"
                elif vid and (dec.get(vid) or {}).get("status") in ("picked", "fed"):
                    source = "scout"
                else:
                    source = str(s.get("source") or "night")
                try:
                    own = ST._title(s, outlet)                   # the words that platform carried
                    title = ST._title(s, "youtube_shorts")       # the short title, to show
                except Exception:  # noqa: BLE001
                    own = title = str(s.get("source_title") or s.get("slug") or "")
                ser_last = (ser.get("points") or [{}])[-1] if ser.get("points") else {}
                rows.append({
                    "pid": pid, "outlet": outlet, "board": day.isoformat(), "slot": k, "hour": loc.hour,
                    "dow": loc.weekday(), "slug": s.get("slug", ""), "format": s.get("format", ""),
                    "host": s.get("host") or "", "creator_key": PB.name_key(s.get("host") or ""),
                    "rights": s.get("rights") or "", "source": source, "bench": bench,
                    "sent_s": round(ST._sent_secs(s, outlet), 1), "cut": bool((s.get("cuts") or {}).get(outlet)),
                    "title": title, "title_feat": title_feat(own),
                    "hook_feat": {k2: sig.get(k2) for k2 in ("first_word_s", "wps_3s", "scene_changes_3s",
                                                             "hook_on_screen") if k2 in sig},
                    "coach": {"measured": (coach.get("measured") or {}).get("total"),
                              "written": round(sum(vals) / len(vals) * 10, 1) if vals else None},
                    "extras": {"first_comment": has_fc,
                               "poll": bool((x.get("poll") or {}).get(outlet)) if x else False,
                               "trial": bool((x.get("trial") or {}).get(outlet)) if x else False},
                    "experiment": dict(x.get("experiment") or {}) if isinstance(x, dict) and x.get("experiment")
                    else None,
                    "playbook_v": _pb_version_at(pb_hist, went),
                    "went": went.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
                    "late_min": round(late, 1), "age_h": round(age_min / 60.0, 1),
                    "views": views, "v24_src": src,
                    "views_now": int(m.get("views") or 0) if isinstance(m.get("views"), (int, float)) else
                    (int(ser_last.get("views") or 0) if ser_last else 0),
                    "extra": ser_last.get("extra") or {} if isinstance(ser_last, dict) else {},
                    "watch": {w: m[w] for w in WATCH_KEYS if isinstance(m.get(w), (int, float))
                              and not isinstance(m.get(w), bool)},
                    "url": rec.get("url", ""), "baseline_24h": None, "rel_24h": None, "_rank": _post_rank(rec),
                })
    # one row a post: two slots can hold one platform id (the nightly read matched a sibling clip's post, provider
    # "matched"); it counts once, under the slot that posted it, as checkpoints reads it
    best: Dict[Tuple[str, str], int] = {}
    for i, r in enumerate(rows):
        key = (r["outlet"], r["pid"])
        if key not in best or r["_rank"] > rows[best[key]]["_rank"]:
            best[key] = i
    keep = set(best.values())
    for r in rows:
        r.pop("_rank", None)
    return [r for i, r in enumerate(rows) if i in keep]


def _post_rank(rec: dict) -> Tuple[bool, bool]:
    """Which of two slots holding one platform id is the post's own: the one that posted it, then one not matched
    by the nightly read (the same rule as checkpoints._rank)."""
    prov = str(rec.get("provider") or "")
    return prov == "direct", prov != "matched"


def _went(r: dict) -> float:
    t = _when(r.get("went"))
    return t.timestamp() if t else 0.0


def _v24(r: dict) -> Optional[float]:
    v = (r.get("views") or {}).get("24h")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


Item = Tuple[float, float, dict]          # (went, epoch seconds; 24-hour views; the row)


def _items(rows: List[dict], outlet: str) -> List[Item]:
    """One platform's posts with a 24-hour number."""
    return [(_went(r), v, r) for r in rows if r.get("outlet") == outlet for v in (_v24(r),) if v is not None]


def _before(items: Sequence[Item], t: float) -> List[Item]:
    """The posts a usual at epoch time t rests on: those that went up BEFORE t within 14 days, or within 30 when
    fewer than 5 went up in 14; [] while fewer than 5 (no usual yet)."""
    for days in (BASE_DAYS, BASE_FALLBACK_DAYS):
        got = [it for it in items if t - days * 86400 <= it[0] < t]
        if len(got) >= BASE_MIN:
            return got
    return []


def _usual_of(items: Sequence[Item], t: float) -> Tuple[Optional[float], int]:
    """(the usual, how many posts it rests on) at epoch time t, from one account and platform's posts: the median
    24-hour views of the posts in _before. A post never counts toward its own usual, nor a later post toward an
    earlier one's. (None, how many went up in the 30 days before t) while there are fewer than 5."""
    got = _before(items, t)
    if got:
        return SM.median([v for _, v, _ in got]), len(got)
    return None, sum(1 for w, _, _ in items if t - BASE_FALLBACK_DAYS * 86400 <= w < t)


def add_baselines(rows: List[dict]) -> None:
    """Each row's baseline (its usual: the median 24-hour views of the same account and platform over the 14 days
    before it went up, 30 when fewer than 5, counting only posts that went up before it; none under 5) and, when the
    row has its own 24-hour number, rel_24h = ln((v24 + 1) / (baseline + 1)). A post with no usual has no rel_24h,
    so it stays out of best, worst, slow starts and what works. Platforms are never pooled; `rows` is one account."""
    for outlet in {r.get("outlet") for r in rows}:
        items = _items(rows, outlet)
        for r in rows:
            if r.get("outlet") != outlet:
                continue
            v = _v24(r)
            base, n = _usual_of(items, _went(r))
            r["baseline_24h"] = round(base, 1) if base is not None else None
            r["baseline_n"] = n if base is not None else 0
            r["rel_24h"] = (round(math.log((v + 1.0) / (base + 1.0)), 4) if base is not None and v is not None
                            else None)


def baseline_for(rows: List[dict], outlet: str, when: datetime) -> Tuple[Optional[float], int]:
    """(the usual 24-hour views on a platform at a time, how many posts it rests on), by the same rule as a post's
    own: posts that went up before `when`, 14 days back (30 when fewer than 5); None under 5."""
    return _usual_of(_items(rows, outlet), when.timestamp())


# ====================================================================================================== what works
def _seed(*parts) -> int:
    return int(hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def confidence(n: int, n_rest: int, ci: Optional[Sequence[float]], q: float, split: Optional[bool], outlet_n: int,
               min_n: int = 12) -> str:
    """strong | likely | early sign | '' (see the module's words)."""
    if n < EARLY_N or n_rest < EARLY_N:
        return ""
    if outlet_n < CLAIM_MIN:
        return "early sign"
    excl = bool(ci) and (ci[0] > 1.0 or ci[1] < 1.0)
    strong_n = max(STRONG_N, int(min_n))
    if n >= strong_n and n_rest >= strong_n and excl and q <= 0.1 and split is True:
        return "strong"
    if n >= int(min_n) and n_rest >= int(min_n) and excl and q <= 0.2 and split is True:
        return "likely"
    return "early sign"


def analyse(rows: List[dict], min_n: int = 12, day: str = "", boot_n: Optional[int] = None) -> List[dict]:
    """Each factor level against the rest of its platform's posts, by rel_24h: lift = exp(median in the group -
    median in the rest), a 90% bootstrap range (seeded), Mann-Whitney p, Benjamini-Hochberg q across every test
    of the day, the split-half check and how sure. Levels with fewer than 6 posts on a side are not tested."""
    boot_n = BOOT_N if boot_n is None else boot_n
    tests: List[dict] = []
    names = {r.get("creator_key"): r.get("host") for r in rows if r.get("creator_key") and r.get("host")}
    for outlet in sorted({r["outlet"] for r in rows}):
        rr = [r for r in rows if r["outlet"] == outlet and r.get("rel_24h") is not None]
        # the claim gate counts posts with a 24-hour number on the platform, as the report's "N of 30" does (a
        # platform's first five have a 24-hour number and no usual yet)
        outlet_n = sum(1 for r in rows if r["outlet"] == outlet
                       and (_v24(r) is not None or r.get("rel_24h") is not None))
        for key, fn, binary in FACTORS:
            lv = []
            for r in rr:
                try:
                    lv.append((fn(r), r))
                except Exception:  # noqa: BLE001
                    lv.append((None, r))
            known = [(L, r) for L, r in lv if L is not None]
            counts = Counter(L for L, _ in known)
            levels = [True] if binary and set(counts) == {True, False} else [] if binary else sorted(counts, key=str)
            for L in levels:
                g = [r for lvl, r in known if lvl == L]
                rest = [r for lvl, r in known if lvl != L]
                if len(g) < EARLY_N or len(rest) < EARLY_N:
                    continue
                a = [r["rel_24h"] for r in g]
                b = [r["rel_24h"] for r in rest]
                d = SM.median(a) - SM.median(b)
                ci = SM.bootstrap_ci(a, b, n=boot_n, seed=_seed(day, outlet, key, L))
                _, p = SM.mann_whitney(a, b)
                split = SM.split_half([(_went(r), r["rel_24h"]) for r in g], [(_went(r), r["rel_24h"]) for r in rest])
                tests.append({"factor": key, "level": L if not binary else "yes", "outlet": outlet,
                              "words": factor_words(key, L, names.get(L, "") if key == "creator" else ""),
                              "n": len(g), "n_rest": len(rest),
                              "lift": round(math.exp(d), 3),
                              "ci90": [round(math.exp(ci[0]), 3), round(math.exp(ci[1]), 3)] if ci else None,
                              "p": round(p, 4), "split_half": split, "outlet_n": outlet_n,
                              "median_group": round(SM.median(a), 4), "median_rest": round(SM.median(b), 4)})
    qs = SM.bh([t["p"] for t in tests])
    for t, q in zip(tests, qs):
        t["q"] = round(q, 4)
        t["confidence"] = confidence(t["n"], t["n_rest"], t["ci90"], q, t["split_half"], t["outlet_n"], min_n)
    tests = [t for t in tests if t["confidence"]]
    tests.sort(key=lambda t: (CONF_ORDER[t["confidence"]], -abs(math.log(max(t["lift"], 1e-9)))))
    return tests


def sure_words(w: dict) -> str:
    """The small "how sure" line: the counts, the range and whether it holds in old and new posts."""
    ci = w.get("ci90") or [None, None]
    rng = f", range {ci[0]:.1f}x to {ci[1]:.1f}x" if ci[0] is not None else ""
    held = {True: ", holds in older and newer posts", False: ", older and newer posts disagree"}.get(
        w.get("split_half"), "")
    return f"{w.get('n')} posts against {w.get('n_rest')}{rng}{held}"


# ====================================================================================================== problems
def _expected_outlets(acct, rows: List[dict]) -> List[str]:
    """The platforms a signed clip posts to by itself (outlets in api mode), plus any it posted to before."""
    outs = [o for o, v in (getattr(acct, "outlets", {}) or {}).items()
            if o in DASH and isinstance(v, dict) and v.get("mode") == "api"]
    for r in rows:
        if r["outlet"] not in outs and r["outlet"] != "tiktok":
            outs.append(r["outlet"])
    return sorted(outs)


def _last_verdicts(acct) -> Dict[Tuple[str, str], dict]:
    out: Dict[Tuple[str, str], dict] = {}
    for v in VD.rows(acct):
        out[(str(v.get("board")), str(v.get("slot")))] = v
    return out


FIX = {
    "empty": "Keep the bench full so an empty slot still posts (factory.py bench fill {slug} --date today).",
    "declined": "After a decline, press Make again or let the bench fill it, so the slot still posts.",
    "refused": "Read why on each card; pick sources the rules allow.",
    "failed": "Read the last note on each failed slot; night-retry runs at 03:45 "
              "(factory.py night-retry {slug} --date today).",
    "unsigned": "Approve or decline before the slot's time: an unsigned clip never posts.",
    "no_cut": "Make the platform's own cut in the nightly run (factory.py igcut or ytcut), so the clip posts there.",
    "gave_up": "Read why on the card, then Try again while it is under 20 hours late.",
    "failed_post": "Read the error on the card and Try again; factory.py doctor checks the sign-in.",
    "not_posted": "The poster has no record of it: look at the card, then run factory.py doctor.",
    "late": "Sign before the slot's time: a late post misses the hours it was planned for.",
    "slow_start": "Compare each with the best posts: the hook and the first seconds decide the first day.",
    "weak": "Fewer of these on {outlet}, or press Test it first on the proposal.",
}
# where a platform says a post was held back (a sudden drop, or a platform far behind the others)
CHECK_WHERE = {"youtube_shorts": "a copyright claim or a limited video in YouTube Studio",
               "facebook_reels": "a rights claim or a limited reel in the Page's Professional dashboard",
               "instagram_reels": "a limited reel under Account status in the Instagram app",
               "tiktok": "a muted or limited video in TikTok Studio"}
DROP_MIN_N, DROP_MAX_N = 4, 5   # a sudden drop: the last N posts with a 24-hour number against the N before them
DROP_PARTS = 3                  # ... when the last N's median is under a third of the N before's
GAP_SHARE, GAP_MIN_N = 0.1, 8   # a platform gap: a usual under a tenth of the best platform's, 8 posts under both
OVERLAP_KINDS = ("weak", "slow", "drop", "gap")     # how posts did: kept out of the day's views-lost total


def _fix(key: str, slug: str, outlet: str = "") -> str:
    base = key.split(":", 1)[0]
    return FIX.get(base, "").format(slug=slug, outlet=OUTLET_WORDS.get(outlet, outlet))


def _ow(o: str) -> str:
    return OUTLET_WORDS.get(o, o)


def _ows(outlets) -> str:
    """Platforms in words: "YouTube", "Facebook and Instagram", "Facebook, Instagram and YouTube"."""
    names = sorted(_ow(o) for o in outlets)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _days_words(cfg: C.Config, rs: Sequence[dict]) -> str:
    """The days some posts went up on, local: "10.19" or "10.16 to 10.18"."""
    ds = sorted(_local(cfg, _when(r["went"])).date() for r in rs)
    return _window_words(ds[0], ds[-1]) if ds else ""


def _median_of(rs: Sequence[dict], f, positive: bool = True) -> Optional[float]:
    vals = []
    for r in rs:
        try:
            x = f(r)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(x, (int, float)) and not isinstance(x, bool) and (x > 0 or not positive):
            vals.append(float(x))
    return SM.median(vals)


def compare_words(cfg: C.Config, a: Sequence[dict], b: Sequence[dict]) -> str:
    """Two groups of posts side by side, a first, in numbers: "length 41s against 38s, own cut on 0 of 5 against 0 of
    5, titles 52 against 48 characters, posted about 14:00 against 19:00" (a part neither group carries is left out)."""
    parts = []
    la, lb = _median_of(a, lambda r: r.get("sent_s")), _median_of(b, lambda r: r.get("sent_s"))
    if la and lb:
        parts.append(f"length {la:.0f}s against {lb:.0f}s")
    parts.append(f"own cut on {sum(1 for r in a if r.get('cut'))} of {len(a)} against "
                 f"{sum(1 for r in b if r.get('cut'))} of {len(b)}")
    ta = _median_of(a, lambda r: (r.get("title_feat") or {}).get("chars"))
    tb = _median_of(b, lambda r: (r.get("title_feat") or {}).get("chars"))
    if ta and tb:
        parts.append(f"titles {ta:.0f} against {tb:.0f} characters")

    def mins(r):
        t = _local(cfg, _when(r["went"]))
        return t.hour * 60 + t.minute
    ha, hb = _median_of(a, mins, positive=False), _median_of(b, mins, positive=False)
    if ha is not None and hb is not None:
        parts.append(f"posted about {int(round(ha / 60.0)) % 24:02d}:00 against {int(round(hb / 60.0)) % 24:02d}:00")
    return ", ".join(parts)


def drops(cfg: C.Config, rows: List[dict], window: Tuple[date, date]) -> List[dict]:
    """A sudden drop on a platform: the median 24-hour views of its last N posts with a 24-hour number (N = 5, or 4
    with 8 or 9 posts) is under a third of the median of the N before them. Only posts of the 30 days up to the
    window's end count, and the last of them must have gone up in the window (a platform that stopped posting has
    nothing sudden to say). Views lost: the N before's median less each of the last N's views."""
    lo, hi = window
    end = datetime.combine(hi + timedelta(days=1), dtime(0, 0), tzinfo=cfg.tz()).timestamp()
    out = []
    for outlet in sorted({r["outlet"] for r in rows}):
        items = sorted((it for it in _items(rows, outlet) if end - BASE_FALLBACK_DAYS * 86400 <= it[0] < end),
                       key=lambda it: it[0])
        n = min(DROP_MAX_N, len(items) // 2)
        if n < DROP_MIN_N:
            continue
        last, before = items[-n:], items[-2 * n:-n]
        if not lo <= _local(cfg, _when(last[-1][2]["went"])).date() <= hi:
            continue
        m_last, m_before = SM.median([v for _, v, _ in last]), SM.median([v for _, v, _ in before])
        if not m_before or m_last * DROP_PARTS >= m_before:          # a third exactly is not under a third
            continue
        out.append({"outlet": outlet, "n": n, "last": [r for _, _, r in last], "before": [r for _, _, r in before],
                    "median_last": m_last, "median_before": m_before,
                    "lost": sum(max(0.0, m_before - v) for _, v, _ in last)})
    return out


def gaps(cfg: C.Config, acct, rows: List[dict], day: date) -> List[dict]:
    """A platform far behind the account's best: its usual at the end of the day under a tenth of the best
    platform's, each usual resting on 8 posts or more."""
    end = datetime.combine(day + timedelta(days=1), dtime(0, 0), tzinfo=cfg.tz()).timestamp()
    us: Dict[str, Tuple[float, List[dict]]] = {}
    for o in sorted({r["outlet"] for r in rows}):
        got = _before(_items(rows, o), end)
        if len(got) >= GAP_MIN_N:
            us[o] = (SM.median([v for _, v, _ in got]), [r for _, _, r in got])
    if len(us) < 2:
        return []
    best = max(sorted(us), key=lambda o: us[o][0])
    weak = sorted((o for o in us if o != best and us[o][0] < us[best][0] * GAP_SHARE), key=lambda o: us[o][0])
    out = []
    for i, o in enumerate(weak):
        a, b = us[best][1], us[o][1]
        kb = {_clip_key(r) for r in b}
        same = bool(kb) and len(kb & {_clip_key(r) for r in a}) >= 0.8 * len(kb)      # mostly the same clips
        out.append({"outlet": o, "best": best, "usual": us[o][0], "usual_best": us[best][0], "n": len(b),
                    "n_best": len(a), "weakest": i == 0, "rows": b, "rows_best": a, "same_clips": same})
    return out


def problems(cfg: C.Config, acct, rows: List[dict], boards: List[Tuple[date, dict]], day: date, now: datetime,
             works: List[dict], window: Tuple[date, date]) -> List[dict]:
    """What cost views on `day`, biggest first: slots that never posted (empty, declined, refused, failed to make,
    not signed) at the usual views times the platforms; posts that failed or gave up at the usual views; late posts
    at the share of the first day missed; then how posts did, in the posts window: a sudden drop on a platform, slow
    starts and weak factors (the usual less the actual), and a platform far behind the best one. The usual is each
    platform's own at the slot's time (posts before it, see _before); a platform with no usual yet is left out of a
    figure and named in `unknown`. How posts did overlaps the rest (the same posts, the day before as well) and is
    not added into the day's total; a post in a drop is not listed again as a slow start."""
    slug = _slug(acct)
    outs = _expected_outlets(acct, rows)
    verd = _last_verdicts(acct)
    acc: Dict[str, dict] = {}

    def add(key: str, lost: float, ev: dict, outlet: str = "", kind: str = "slot", unknown: Sequence[str] = (),
            **extra) -> None:
        p = acc.setdefault(key, {"key": key, "n": 0, "views_lost": 0.0, "evidence": [], "outlet": outlet,
                                 "kind": kind, "reasons": Counter(), "max_late_h": 0.0, "unknown": set()})
        p["n"] += 1
        p["views_lost"] += max(0.0, float(lost or 0))
        if len(p["evidence"]) < 10:
            p["evidence"].append(ev)
        for r in VD.normalize(extra.get("reasons") or []):      # one list: old codes read as today's
            p["reasons"][r] += 1
        p["max_late_h"] = max(p["max_late_h"], float(extra.get("late_h") or 0))
        p["unknown"] |= set(unknown)

    for d, bd in boards:
        if d != day:
            continue
        for k, s in sorted(bd["slots"].items()):
            if not isinstance(s, dict):
                continue
            if B.empty_spare(s):                                  # an empty spare is fine, never a lost slot
                continue
            try:
                pa = datetime.fromisoformat(str(s["publish_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            if pa > now:
                continue
            st = s.get("status")
            ev = {"board": d.isoformat(), "slot": k, "why": B.last_note(s)[:160]}
            usual = {o: baseline_for(rows, o, pa)[0] for o in outs}
            if st in EMPTY_STATUSES:
                lost = sum(v for v in usual.values() if v is not None)
                reasons = (verd.get((d.isoformat(), k)) or {}).get("reasons") or [] if st == "declined" else []
                add(EMPTY_STATUSES[st], lost, ev, reasons=reasons, unknown=[o for o, v in usual.items() if v is None])
                continue
            if st not in B.SIGNED or pa > now - timedelta(hours=1):
                continue
            recs = s.get("outlets") or {}
            for o in outs:
                rec = recs.get(o) or {}
                if rec.get("post_id") or rec.get("video_id") or rec.get("capped"):
                    continue                    # capped: the outlet's daily_cap, by design (poster.CAP_WHY)
                why = str(rec.get("why") or rec.get("last_error") or "")
                if rec.get("gave_up"):
                    key = f"no_cut:{o}" if re.search(r"\bno \w+ cut\b", why, re.I) else f"gave_up:{o}"
                elif rec.get("tries") or rec.get("last_error"):
                    key = f"failed_post:{o}"
                else:
                    key = f"not_posted:{o}"
                add(key, usual[o] or 0, dict(ev, outlet=o, why=why[:160]), outlet=o, kind="post",
                    unknown=[o] if usual[o] is None else [])
    lo, hi = window
    dropped = drops(cfg, rows, window)
    in_drop = {(r["outlet"], r["pid"]) for dr in dropped for r in dr["last"]}
    for r in rows:
        loc = _local(cfg, _when(r["went"])).date()
        b = r.get("baseline_24h")
        if loc == day and r.get("late_min", 0) >= LATE_MIN:
            late_h = r["late_min"] / 60.0
            add("late", float(b) * min(late_h, 24.0) / 24.0 if b is not None else 0.0,
                {"board": r["board"], "slot": r["slot"], "outlet": r["outlet"], "why": f"{late_h:.1f} hours late"},
                outlet="", kind="late", late_h=late_h, unknown=[r["outlet"]] if b is None else [])
        if lo <= loc <= hi and r.get("rel_24h") is not None and r["rel_24h"] <= SLOW_REL \
                and (r["outlet"], r["pid"]) not in in_drop:
            add("slow_start", float(r["baseline_24h"]) - float(r["views"]["24h"]),
                {"board": r["board"], "slot": r["slot"], "outlet": r["outlet"],
                 "why": f"{_int_words(r['views']['24h'])} at 24 hours, the usual {_int_words(r['baseline_24h'])}"},
                kind="slow")
    for w in works:
        if w["lift"] >= 1.0 or w["confidence"] not in ("strong", "likely"):
            continue                      # an early sign stays under What works; it is not a problem yet
        fn, binary = FACTOR_OF[w["factor"]]
        key = f"weak:{w['factor']}:{w['level']}:{w['outlet']}"
        for r in rows:
            loc = _local(cfg, _when(r["went"])).date()
            if r["outlet"] != w["outlet"] or r.get("rel_24h") is None or not (lo <= loc <= hi):
                continue
            try:
                L = fn(r)
            except Exception:  # noqa: BLE001
                continue
            if (binary and L is True) or (not binary and L == w["level"]):
                add(key, float(r["baseline_24h"]) - float(r["views"]["24h"]),
                    {"board": r["board"], "slot": r["slot"], "outlet": r["outlet"]}, outlet=w["outlet"], kind="weak")
        if key in acc:
            acc[key]["work"] = w
    out = []
    for key, p in acc.items():
        n = p["n"]
        o = p["outlet"]
        ow = _ow(o)
        base_key = key.split(":", 1)[0]
        if base_key == "empty":
            words = f"{n} slot{_s(n)} had no clip by {'its' if n == 1 else 'their'} time"
        elif base_key == "declined":
            words = f"{n} declined clip{_s(n)} not replaced"
            if p["reasons"]:
                words += " (" + VD.words([c for c, _ in p["reasons"].most_common(3)]) + ")"
        elif base_key == "refused":
            words = f"{n} slot{_s(n)} refused by the rules"
        elif base_key == "failed":
            words = f"{n} slot{_s(n)} failed to make"
        elif base_key == "unsigned":
            words = f"{n} clip{_s(n)} not signed by {'its' if n == 1 else 'their'} time"
        elif base_key == "no_cut":
            words = f"{n} clip{_s(n)} could not post to {ow}: no {ow} cut"
        elif base_key == "gave_up":
            words = f"{n} post{_s(n)} to {ow} given up"
        elif base_key == "failed_post":
            words = f"{n} post{_s(n)} to {ow} failed"
        elif base_key == "not_posted":
            words = f"{n} signed clip{_s(n)} never reached {ow}"
        elif base_key == "late":
            words = f"{n} post{_s(n)} went up late (up to {p['max_late_h']:.1f} hours)"
        elif base_key == "slow_start":
            words = f"{n} post{_s(n)} started at under half the usual"
        else:
            w = p.get("work") or {}
            words = f"{upper_first(w.get('words', key))} on {ow}: {x_words(w.get('lift') or 1)} the usual"
        href = (f"/?date={day.isoformat()}&acct={slug}" if p["kind"] in ("slot", "post", "late")
                else f"/reports?view=today&acct={slug}")
        item = {"key": key, "words": _plain(words), "n": n, "views_lost": int(round(p["views_lost"])),
                "fix": _plain(_fix(key, slug, o)), "href": href, "evidence": p["evidence"], "kind": p["kind"],
                "overlaps": p["kind"] in OVERLAP_KINDS, "unknown": sorted(p["unknown"])}
        if p.get("work"):
            item["work"] = {k: p["work"][k] for k in ("factor", "level", "outlet", "lift", "confidence")}
        out.append(item)
    for dr in dropped:
        o, n = dr["outlet"], dr["n"]
        words = (f"{_ow(o)} views dropped: the last {n} posts ({_days_words(cfg, dr['last'])}) got "
                 f"{_int_words(dr['median_last'])} at 24 hours, the {n} before ({_days_words(cfg, dr['before'])}) "
                 f"got {_int_words(dr['median_before'])}")
        fix = (f"Check these posts for {CHECK_WHERE.get(o, 'a claim or a limit on the platform')}, then the upload "
               f"cut, the title and the posting time. The last {n} against the {n} before: "
               f"{compare_words(cfg, dr['last'], dr['before'])}.")
        out.append({"key": f"drop:{o}", "words": _plain(words), "n": n, "views_lost": int(round(dr["lost"])),
                    "fix": _plain(fix), "href": f"/reports?view=today&acct={slug}", "kind": "drop", "overlaps": True,
                    "unknown": [], "median_last": dr["median_last"], "median_before": dr["median_before"],
                    "evidence": [{"board": r["board"], "slot": r["slot"], "outlet": o,
                                  "why": f"{_int_words(r['views']['24h'])} at 24 hours"} for r in dr["last"]]})
    name = getattr(acct, "name", slug)
    for g in gaps(cfg, acct, rows, day):
        o, best = g["outlet"], g["best"]
        head = f"{_ow(o)} is {C.possessive(name)} weakest platform" if g["weakest"] else \
            f"{_ow(o)} is far behind {_ow(best)} on {name}"
        words = (f"{head}: {_int_words(g['usual'])} views is usual there, {_int_words(g['usual_best'])} on "
                 f"{_ow(best)}")
        fix = (f"Do on {_ow(o)} what works on {_ow(best)}. {_ow(best)} against {_ow(o)}: "
               f"{compare_words(cfg, g['rows_best'], g['rows'])}.")
        if g["same_clips"]:
            fix += (f" The same clips go to both, so check {_ow(o)} itself for "
                    f"{CHECK_WHERE.get(o, 'a claim or a limit on the platform')}.")
        out.append({"key": f"gap:{o}", "words": _plain(words), "n": g["n"], "views_lost": 0, "fix": _plain(fix),
                    "href": f"/reports?view=today&acct={slug}", "kind": "gap", "overlaps": True, "unknown": [],
                    "evidence": [], "usual": g["usual"], "usual_best": g["usual_best"], "best": best,
                    "n_best": g["n_best"]})
    out.sort(key=lambda p: (-p["views_lost"], p["kind"] == "gap", p["key"]))     # a gap, no day's loss, comes last
    return out


SHOWN_KINDS = ("slot", "post", "late", "drop", "gap")


def shown_problems(rep: dict) -> List[dict]:
    """The problems a report shows: every one with views lost, and every slot, post, late post, drop or gap even
    when its views lost cannot be counted (no usual yet)."""
    return [p for p in rep.get("problems") or [] if p.get("views_lost") or p.get("kind") in SHOWN_KINDS]


def lost_words(p: dict) -> str:
    """How many views a problem cost, in words ('' for a platform gap, which is not one day's loss)."""
    if p.get("kind") == "gap":
        return ""
    unknown = list(p.get("unknown") or [])
    if p.get("views_lost"):
        return (f"about {_int_words(p['views_lost'])} views lost"
                + (f", not counting {_ows(unknown)} (no usual yet there)" if unknown else ""))
    if unknown:
        return f"views lost not known (no usual yet on {_ows(unknown)})"
    return "views lost not known yet"


def which_words(p: dict, most: int = 5) -> str:
    """The posts or slots a problem is about, "10.19 07:00 YouTube, ...", ending "and N more" when cut short (the
    count and the list always agree); '' when it lists none."""
    ev = [f"{str(e.get('board', ''))[5:].replace('-', '.')} {e.get('slot', '')}"
          + (f" {_ow(e.get('outlet'))}" if e.get("outlet") else "") for e in p.get("evidence") or []]
    ev = [x.strip() for x in ev if x.strip()]
    if not ev:
        return ""
    shown = ev[:most]
    more = max(int(p.get("n") or 0), len(ev)) - len(shown)
    return ", ".join(shown) + (f", and {more} more" if more > 0 else "")


# ====================================================================================================== best, worst
def _why(r: dict, works: List[dict], best: bool) -> List[str]:
    out = []
    if r.get("rel_24h") is not None:
        out.append(f"{x_words(math.exp(r['rel_24h']))} the usual")
    named = set()
    for w in works:
        if w["outlet"] != r["outlet"] or (w["lift"] > 1.0) != best or w.get("confidence") not in ("strong", "likely"):
            continue                      # an early sign is not a reason yet
        fn, binary = FACTOR_OF[w["factor"]]
        try:
            L = fn(r)
        except Exception:  # noqa: BLE001
            continue
        if (binary and L is True) or (not binary and L == w["level"]):
            out.append(f"{w['words']}: {w['lift']:.1f}x")
            named.add(w["factor"])
        if len(out) >= 3:
            break
    late = float(r.get("late_min") or 0)
    if late >= LATE_MIN:
        out.append(f"{late / 60:.1f} hours late")
    elif best:
        out.append("on time")
    if r.get("format") and "format" not in named:
        out.append(f"{r['format']} format")
    if r.get("host") and "creator" not in named:
        out.append(f"from {r['host']}")
    if (r.get("coach") or {}).get("measured") is not None:
        out.append(f"coach {r['coach']['measured']}")
    if (r.get("extras") or {}).get("first_comment") is False and not best:
        out.append("no first comment")
    return [_plain(x) for x in out[:5]]


def _post(r: dict, works: List[dict], best: bool) -> dict:
    return {"pid": r["pid"], "outlet": r["outlet"], "title": r.get("title") or r.get("slug") or "", "url": r.get("url"),
            "board": r["board"], "slot": r["slot"], "slug": r.get("slug"), "host": r.get("host") or "",
            "views_24h": (r.get("views") or {}).get("24h"),
            "views_now": r.get("views_now"), "rel": r.get("rel_24h"),
            "x": round(math.exp(r["rel_24h"]), 2) if r.get("rel_24h") is not None else None,
            "estimate": r.get("v24_src") in ("snapshots", "estimate"), "v24_src": r.get("v24_src"),
            "baseline_24h": r.get("baseline_24h"), "baseline_n": r.get("baseline_n", 0),
            "why": _why(r, works, best)}


def best_worst(cfg: C.Config, rows: List[dict], works: List[dict], day: date, k: int = 5
               ) -> Tuple[List[dict], List[dict], Tuple[date, date]]:
    """The 5 best (10% or more over the usual) and 5 worst (10% or more under it) posts by rel_24h among those that
    went up the day before and on the day (7 days when that gives fewer than 4), each with why."""
    for span in (1, 6):
        lo = day - timedelta(days=span)
        pool = [r for r in rows if r.get("rel_24h") is not None and lo <= _local(cfg, _when(r["went"])).date() <= day]
        if len(pool) >= 4 or span == 6:
            break
    pool.sort(key=lambda r: (-r["rel_24h"], r["pid"]))
    best = [_post(r, works, True) for r in pool if r["rel_24h"] >= math.log(1.1)][:k]          # 10% over the usual
    worst = [_post(r, works, False) for r in reversed(pool) if r["rel_24h"] <= math.log(0.9)][:k]
    return best, worst, (lo, day)


# ====================================================================================================== every clip
def watch_usual(rows: List[dict], outlet: str, t: float) -> Dict[str, list]:
    """One platform's usual watch numbers at epoch time t: for each key the platform reports (watch_pct, watch_s,
    skip_pct, reach), the median over its posts of the 14 days before t that carry it, from 5 posts up;
    {key: [median, n]}. The numbers are each post's latest read, not a 24-hour point."""
    out: Dict[str, list] = {}
    for w in WATCH_KEYS:
        vals = [float(r["watch"][w]) for r in rows if r.get("outlet") == outlet and w in (r.get("watch") or {})
                and t - BASE_DAYS * 86400 <= _went(r) < t]
        if len(vals) >= BASE_MIN:
            out[w] = [round(SM.median(vals), 1), len(vals)]
    return out


def ranked(cfg: C.Config, rows: List[dict], win: Tuple[date, date]) -> List[dict]:
    """Every clip of the window whose posts have a 24-hour number, best to worst: one entry a clip, its platforms
    side by side. A platform under a tenth of its usual (a usual of 20 or more) while another platform took the same
    clip at half its usual or more is marked held back: that platform held it back, not the clip (YouTube held four
    Politics Shorts for about 27 hours on 10.19 and then released them: the 24-hour number is what the first day got).
    The clip's x is its 24-hour views on the platforms that took it and have a usual, over the sum of those usuals,
    so the platform that brings the most views weighs the most; a held-back post is shown beside it, never in it. A
    clip with no usual on any platform that took it comes last."""
    pool = [r for r in rows if win[0] <= _local(cfg, _when(r["went"])).date() <= win[1] and _v24(r) is not None]
    out = []
    for rs in by_clip(pool):
        rs = sorted(rs, key=lambda r: (list(DASH).index(r["outlet"]) if r["outlet"] in DASH else 9))
        posts = []
        for r in rs:
            x = math.exp(r["rel_24h"]) if r.get("rel_24h") is not None else None
            posts.append({"outlet": r["outlet"], "views_24h": int(_v24(r) or 0), "baseline_24h": r.get("baseline_24h"),
                          "x": round(x, 2) if x is not None else None, "estimate": r.get("v24_src") == "estimate",
                          "watch": dict(r.get("watch") or {}), "views_now": r.get("views_now"),
                          "late_min": r.get("late_min") or 0, "url": r.get("url", ""), "held_back": False})
        took = [p for p in posts if p["x"] is not None and p["x"] >= HELD_TOOK_X]
        for p in posts:
            if (p["x"] is not None and p["x"] < HELD_X and float(p["baseline_24h"] or 0) >= HELD_MIN_USUAL
                    and any(q is not p for q in took)):
                p["held_back"] = True
        r0 = rs[0]
        c = {"board": r0["board"], "slot": r0["slot"], "slug": r0.get("slug") or "",
             "title": r0.get("title") or r0.get("slug") or "", "format": r0.get("format") or "",
             "host": r0.get("host") or "", "views_24h": sum(p["views_24h"] for p in posts),
             "held_back": [p["outlet"] for p in posts if p["held_back"]], "posts": posts}
        out.append(_with_x(c))
    return _in_order(out)


def _took(c: dict) -> List[dict]:
    """The posts that count in a clip's x: on a platform that took it (not held back) and has a usual."""
    return [p for p in c.get("posts") or [] if p.get("baseline_24h") is not None and not p.get("held_back")]


def _with_x(c: dict) -> dict:
    """The clip with its x, usual and views on the platforms that took it (views_took, usual_24h, x)."""
    took = _took(c)
    usual = sum(float(p["baseline_24h"]) for p in took)
    c["views_took"] = sum(int(p.get("views_24h") or 0) for p in took)
    c["usual_24h"] = round(usual, 1) if took else None
    c["x"] = round(c["views_took"] / usual, 2) if took and usual > 0 else None
    return c


def _in_order(clips: List[dict]) -> List[dict]:
    clips.sort(key=lambda c: (c["x"] is None, -(c["x"] or 0.0), -int(c.get("views_24h") or 0), c["board"],
                              c["slot"]))
    for i, c in enumerate(clips, 1):
        c["rank"] = i
    return clips


def clips_of(rep: dict) -> List[dict]:
    """A report's clips as its words show them, best to worst, each x on the platforms that took the clip (a report
    written before 10.21 counted a held-back post in it; the posts it kept say which were held back)."""
    return _in_order([_with_x(copy.deepcopy(c)) for c in rep.get("clips") or [] if isinstance(c, dict)])


# ====================================================================================================== coach, health
def _clip_key(r: dict) -> Tuple[str, str]:
    """The clip a row is a post of: its board and slot (a row without them stands alone)."""
    if r.get("board") and r.get("slot"):
        return str(r["board"]), str(r["slot"])
    return "", str(r.get("pid") or id(r))


def by_clip(rows: List[dict]) -> List[List[dict]]:
    """Rows grouped by clip, in the order each clip first appears: one clip on three platforms is one clip."""
    out: Dict[Tuple[str, str], List[dict]] = {}
    for r in rows:
        out.setdefault(_clip_key(r), []).append(r)
    return list(out.values())


def coach_accuracy(rows: List[dict]) -> dict:
    """How well the coach's scores foresaw the first day: Spearman's rho between the score and rel_24h, with a 90%
    range from 15 clips. The score is the clip's, so a clip counts once: its rel_24h is the mean over the platforms
    it posted to (three platforms never pass for three clips)."""
    out: dict = {}
    for which in ("measured", "written"):
        pairs = []
        for grp in by_clip([r for r in rows if r.get("rel_24h") is not None
                            and isinstance((r.get("coach") or {}).get(which), (int, float))]):
            pairs.append((grp[0]["coach"][which], sum(float(r["rel_24h"]) for r in grp) / len(grp)))
        n = len(pairs)
        rho = SM.spearman([a for a, _ in pairs], [b for _, b in pairs]) if n >= 3 else None
        ci = SM.fisher_ci(rho, n) if rho is not None and n >= 15 else None
        out[which] = {"n": n, "rho": round(rho, 3) if rho is not None else None,
                      "ci90": [round(ci[0], 3), round(ci[1], 3)] if ci else None}
    m = out["measured"]
    if m["n"] == 0:
        out["words"] = "No coach scores on posted clips yet."
    elif m["rho"] is None or m["n"] < 15:
        out["words"] = f"Too few scored clips to tell yet ({m['n']})."
    else:
        out["words"] = (f"The coach's score and the first day's views go together at {m['rho']:.2f} over {m['n']} "
                        f"clips (range {m['ci90'][0]:.2f} to {m['ci90'][1]:.2f}; 1 would be perfect, 0 no link).")
    return out


def publishing(cfg: C.Config, acct, boards: List[Tuple[date, dict]], rows: List[dict], day: date,
               now: datetime) -> dict:
    """Publishing on the day: per platform, what posted, failed or gave up, the success rate, late posts, and the time
    the factory saved (an estimate: 3 minutes a post, and the social layer's own sends from its log)."""
    outs = _expected_outlets(acct, rows)
    by: Dict[str, Dict[str, int]] = {o: {"posted": 0, "failed": 0, "gave_up": 0, "missing": 0, "late": 0}
                                     for o in outs}
    slots = {"planned": 0, "signed": 0}
    for d, bd in boards:
        if d != day:
            continue
        for k, s in bd["slots"].items():
            if not isinstance(s, dict):
                continue
            if B.empty_spare(s):                                  # an empty spare is fine, never a lost slot
                continue
            slots["planned"] += 1
            if s.get("status") not in B.SIGNED:
                continue
            slots["signed"] += 1
            for o in outs:
                rec = (s.get("outlets") or {}).get(o) or {}
                if rec.get("post_id") or rec.get("video_id"):
                    by[o]["posted"] += 1
                elif rec.get("capped"):
                    continue                    # the outlet's daily_cap: never a failure
                elif rec.get("gave_up"):
                    by[o]["gave_up"] += 1
                elif rec.get("tries") or rec.get("last_error"):
                    by[o]["failed"] += 1
                else:
                    try:
                        due = datetime.fromisoformat(str(s["publish_at"])) <= now - timedelta(hours=1)
                    except (KeyError, TypeError, ValueError):
                        due = False
                    if due:
                        by[o]["missing"] += 1
    for r in rows:
        if r["outlet"] in by and r["board"] == day.isoformat() and r.get("late_min", 0) >= LATE_MIN:
            by[r["outlet"]]["late"] += 1
    posted = sum(v["posted"] for v in by.values())
    bad = sum(v["failed"] + v["gave_up"] + v["missing"] for v in by.values())
    social: Dict[str, Dict[str, int]] = {}
    rows_fn = REG.fn("outward:rows")
    if rows_fn is not None:
        try:
            start = datetime.combine(day, dtime(0, 0), tzinfo=cfg.tz())
            for r in rows_fn(cfg, acct, since=start, until=start + timedelta(days=1)):
                kind, st = str(r.get("kind") or ""), str(r.get("state") or "")
                if kind == "post":
                    continue
                social.setdefault(kind, {}).setdefault(st, 0)
                social[kind][st] += 1
        except Exception:  # noqa: BLE001
            social = {}
    saved = posted * MINUTES_SAVED["post"] + sum(
        MINUTES_SAVED.get(kind, 0) * by_st.get("done", 0) for kind, by_st in social.items())
    rate = round(posted / (posted + bad), 3) if posted + bad else None
    return {"day": day.isoformat(), "slots": slots, "by_outlet": by, "posted": posted, "failed": bad,
            "success_rate": rate, "time_saved_min": round(saved, 1), "social": social,
            # posts, not slots: a slot that failed to make is under Biggest problems, not here
            "words": _plain(f"{posted} post{_s(posted)} went out from {slots['signed']} signed clip"
                            f"{_s(slots['signed'])}, {bad} failed or gave up"
                            + (f" ({rate * 100:.0f}% went through)" if rate is not None else "")
                            + f"; about {saved / 60:.1f} hours of posting by hand saved")}


def verdict_counts(acct, lo: date, hi: date) -> dict:
    out = {"approve": 0, "decline": 0, "remake": 0, "pull": 0, "reasons": {}}
    for v in VD.rows(acct):
        try:
            d = date.fromisoformat(str(v.get("board")))
        except ValueError:
            continue
        if not lo <= d <= hi:
            continue
        if v.get("verdict") in out:
            out[v["verdict"]] += 1
        for c in VD.normalize(v.get("reasons") or []):          # one list: old codes read as today's
            out["reasons"][c] = out["reasons"].get(c, 0) + 1
    out["reasons"] = dict(sorted(out["reasons"].items(), key=lambda kv: -kv[1]))
    return out


# ====================================================================================================== proposals
def proposals(acct) -> Dict[str, dict]:
    d = store.read(proposals_path(acct), {})
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def _pid(kind: str, key: str, now: datetime) -> str:
    return f"p-{now:%Y%m%d}-{hashlib.sha1((kind + '|' + key).encode()).hexdigest()[:6]}"


def _current(acct, key: str):
    pb = PB.read(acct)
    for r in pb["rules"]:
        if r.get("key") == key:
            return r.get("value")
    return None


def _evidence(w: dict, window: str) -> dict:
    return {"metric": "rel_24h", "n": w["n"], "n_rest": w["n_rest"], "lift": w["lift"], "ci90": w["ci90"],
            "p": w["p"], "q": w.get("q"), "split_half": w["split_half"], "confidence": w["confidence"],
            "outlet": w["outlet"], "window": window}


def _from_work(acct, w: dict, rows: List[dict]) -> Optional[dict]:
    """The proposal a finding makes (factor, coach or advice), or None."""
    f, L, o, lift = w["factor"], w["level"], w["outlet"], float(w["lift"])
    ow = OUTLET_WORDS.get(o, o)
    if f == "creator":
        key = f"scout.channels.{L}"
        who = str(w.get("words") or "").replace("clips from ", "", 1) or L
        cur = float(_current(acct, key) or 1.0)
        to = PB._clamp(cur * math.sqrt(lift))              # half the measured lift, in log terms: cautious
        if abs(to - cur) < 0.02:
            return None
        return {"kind": "factor", "key": key, "from": cur, "to": to, "name": who,
                "words": f"The scout ranks {C.possessive(who)} videos {to:g}x (now {cur:g}x): their clips run {lift:.1f}x the "
                         f"usual on {ow}", "risk": "andrew_only"}
    if f == "length" and lift > 1.0:
        band = next(((lo, hi) for name, lo, hi in LENGTH_BANDS if name == L), None)
        if band is None:
            return None
        lo, hi = max(band[0], 10), min(band[1], 120)
        return {"kind": "coach", "key": f"coach.length_best.{o}", "from": _current(acct, f"coach.length_best.{o}"),
                "to": [lo, hi], "words": f"The coach's best length on {ow} becomes {lo} to {hi} seconds: clips "
                                         f"{L} long run {lift:.1f}x the usual", "risk": "safe"}
    if f == "first_word" and lift > 1.0 and L in ("under 0.5s", "0.5 to 1.5s"):
        to = 0.5 if L == "under 0.5s" else 1.5
        return {"kind": "coach", "key": "coach.first_word_max_s", "from": _current(acct, "coach.first_word_max_s"),
                "to": to, "words": f"The coach wants the first word within {to:g} seconds: those run {lift:.1f}x "
                                   f"the usual on {ow}", "risk": "safe"}
    if f in ("title_number", "title_question", "title_quote", "title_name", "title_caps", "hook_on_screen"):
        part = "prefer" if lift > 1.0 else "avoid"
        what = factor_words(f, L)
        return {"kind": "coach", "key": f"coach.{part}.{PB.slug_key(what)}", "from": None, "to": what,
                "words": f"The coach {'prefers' if part == 'prefer' else 'marks down'} {what}: {lift:.1f}x the usual "
                         f"on {ow}", "risk": "safe"}
    words = factor_words(f, L)
    more = "More" if lift > 1.0 else "Fewer"
    return {"kind": "advice", "key": f"advice.{f}.{PB.slug_key(str(L))}.{o}", "from": None, "to": None,
            "words": f"{more} {words} on {ow}: {lift:.1f}x the usual", "risk": "safe"}


def _declined_before(props: Dict[str, dict], kind: str, key: str) -> Optional[dict]:
    got = [p for p in props.values() if p.get("kind") == kind and p.get("key") == key and p.get("state") == "declined"]
    return max(got, key=lambda p: str((p.get("decided") or {}).get("at") or "")) if got else None


def _topic(prop: dict) -> str:
    """What a proposal is about, whichever way it points (coach.prefer.X and coach.avoid.X are one topic)."""
    key = str(prop.get("key") or "")
    for part in ("coach.prefer.", "coach.avoid."):
        if key.startswith(part):
            key = "coach.mark." + key[len(part):]
    return f"{prop.get('kind')}|{key}"


def _stance(prop: dict) -> str:
    """Which way a proposal points: a factor up or down, a coach setting's value (prefer or avoid is in its key),
    advice for more or fewer; '' for the kinds that point no way."""
    kind = prop.get("kind")
    if kind == "factor":
        try:
            return "up" if float(prop["to"]) > float(prop.get("from") or 1.0) else "down"
        except (KeyError, TypeError, ValueError):
            return ""
    if kind == "coach":
        return f"{prop.get('key')}={json.dumps(prop.get('to'), sort_keys=True)}"
    if kind == "advice":
        lift = (prop.get("evidence") or {}).get("lift")
        return ("more" if float(lift) > 1.0 else "fewer") if isinstance(lift, (int, float)) else ""
    return ""


def _agreed(cands: List[Tuple[dict, dict]]) -> List[dict]:
    """The day's proposals, one a topic. A setting the whole account shares (a scout factor, the first word, a coach
    mark) can be found on several platforms: when they point the same way the first (the strongest) leads and the
    others are named in its words and kept in its evidence; when they disagree nothing is proposed."""
    groups: Dict[str, List[Tuple[dict, dict]]] = {}
    for w, prop in cands:
        groups.setdefault(_topic(prop), []).append((w, prop))
    out: List[dict] = []
    for grp in groups.values():
        if len({_stance(p) for _, p in grp}) > 1:
            continue
        w0, lead = grp[0]
        seen = {w0["outlet"]}
        for w, _ in grp[1:]:
            if w["outlet"] in seen:
                continue
            seen.add(w["outlet"])
            lead["words"] = f"{lead['words']}, {float(w['lift']):.1f}x on {OUTLET_WORDS.get(w['outlet'], w['outlet'])}"
            lead["evidence"].setdefault("also", []).append(
                {k: w.get(k) for k in ("outlet", "n", "lift", "ci90", "p", "confidence")})
        out.append(lead)
    return out


def _put(acct, prop: dict, props: Dict[str, dict], now: datetime) -> Optional[str]:
    """Add a proposal unless the same change is already open (its evidence, and the words and change resting on it,
    are brought up to date), an open one on the same topic points the other way (both left as they are), or it was
    declined and the evidence has not doubled since. Its id, when it was added."""
    topic, stance = _topic(prop), _stance(prop)
    if any(p.get("state") == "open" and _topic(p) == topic and _stance(p) != stance for p in props.values()):
        return None                       # an open one says the opposite: it keeps its own words and evidence
    open_same = [p for p in props.values() if p.get("kind") == prop["kind"] and p.get("key") == prop["key"]
                 and p.get("state") == "open"]
    if open_same:
        o = open_same[0]                  # the words and change follow the evidence they rest on
        o["evidence"] = prop.get("evidence") or o.get("evidence")
        for k in ("from", "to", "name"):
            if prop.get(k) is not None:
                o[k] = prop[k]
        if prop.get("words"):
            o["words"] = _plain(prop["words"])
        if prop.get("backtest"):
            o["backtest"] = prop["backtest"]
        return None
    in_force = None
    for p in props.values():
        at = _when((p.get("decided") or {}).get("at"))
        if p.get("kind") == prop["kind"] and p.get("key") == prop["key"] and p.get("state") == "approved" \
                and at is not None and now - at < timedelta(days=PB.RULE_DAYS):
            if prop["kind"] not in PLAYBOOK_KINDS:
                return None               # advice noted, an experiment run: not again for 60 days
            if in_force is None:
                in_force = {r.get("key") for r in PB.read(acct, now)["rules"]}
            if _rule_key(prop) in in_force:
                return None               # it stands; once taken back (or ended) it may be proposed again
    dec = _declined_before(props, prop["kind"], prop["key"])
    n_now = int((prop.get("evidence") or {}).get("n") or 0)
    if dec is not None:
        n_then = int(dec.get("n_at_decline") or (dec.get("evidence") or {}).get("n") or 0)
        if n_now < 2 * max(1, n_then):
            return None
    pid = _pid(prop["kind"], prop["key"], now)
    while pid in props:
        pid += "x"
    prop.update(id=pid, account=_slug(acct), made=_iso(now), state="open", decided=None,
                expires=_iso(now + timedelta(days=PROPOSAL_DAYS)))
    prop["words"] = _plain(prop.get("words") or "")
    prop.setdefault("by", "learner")
    prop.setdefault("risk", "safe")
    prop.setdefault("backtest", None)
    props[pid] = prop
    return pid


PLAYBOOK_KINDS = ("factor", "coach", "runbook_note", "draft_rule")


def _rule_key(prop: dict) -> str:
    """The playbook key an approved proposal of this change was kept under."""
    try:
        return PB.check(prop.get("key"), prop.get("to"))[0]
    except SystemExit:
        return str(prop.get("key") or "")


def _expire(props: Dict[str, dict], now: datetime) -> None:
    for p in props.values():
        end = _when(p.get("expires"))
        if p.get("state") == "open" and end is not None and end <= now:
            p["state"] = "expired"


def _unanswered_intents(acct, now: datetime) -> Counter:
    """How often each question went unanswered in the Inbox in the last 7 days (engage store, read only)."""
    c: Counter = Counter()
    d = store.path(_slug(acct), "engage")
    since = now - timedelta(days=7)
    for f in sorted(d.glob("items-*.json")) if d.is_dir() else []:
        data = store.read(f, {})
        items = data.get("items") if isinstance(data, dict) else None
        for it in (items or {}).values() if isinstance(items, dict) else []:
            if not isinstance(it, dict) or it.get("state") not in ("new", None) or it.get("answered"):
                continue
            at = _when(it.get("at"))
            if at is None or at < since:
                continue
            for t in it.get("tags") or []:
                if t in INTENTS:
                    c[t] += 1
    return c


def _rule_intents(acct) -> set:
    raw = store.read(store.path(_slug(acct), "engage", "rules.json"), {})
    rules = raw.get("rules") if isinstance(raw, dict) and "rules" in raw else raw
    vals = rules.values() if isinstance(rules, dict) else rules if isinstance(rules, list) else []
    out = set()
    for r in vals:
        if isinstance(r, dict):
            out |= set(((r.get("trigger") or {}).get("intents")) or [])
    return out


def make_proposals(cfg: C.Config, acct, works: List[dict], rows: List[dict], now: datetime, window: str,
                   did: List[dict], scfg: Optional[dict] = None) -> List[str]:
    """New proposals from the day's findings (strong or likely only), the Inbox's most frequent unanswered
    questions, and each rule that did not work. Open ones expire after 14 days. The ids made."""
    made: List[str] = []
    with store.edit(proposals_path(acct), dict) as props:
        _expire(props, now)
        cands: List[Tuple[dict, dict]] = []
        for w in works:
            if w["confidence"] not in ("strong", "likely"):
                continue
            prop = _from_work(acct, w, rows)
            if prop is None:
                continue
            prop["evidence"] = _evidence(w, window)
            cands.append((w, prop))
        for prop in _agreed(cands):
            if prop["kind"] == "factor":
                try:
                    prop["backtest"] = backtest(cfg, acct, prop, scfg=scfg, now=now)
                except (Exception, SystemExit) as e:  # noqa: BLE001  (no scout rules here: still proposed)
                    prop["backtest"] = {"boards": 0, "into_top10": 0, "out_of_top10": 0,
                                        "why": f"could not re-rank: {type(e).__name__}"}
            got = _put(acct, prop, props, now)
            if got:
                made.append(got)
        have = _rule_intents(acct)
        for intent, n in _unanswered_intents(acct, now).most_common(3):
            if n < 5 or intent in have:
                continue
            got = _put(acct, {"kind": "draft_rule", "key": f"draft_rule.{intent}", "from": None,
                              "to": {"intent": intent, "n": n, "words": INTENT_WORDS.get(intent, intent)},
                              "words": f"Draft an automation for \"{INTENT_WORDS.get(intent, intent)}\": asked {n} "
                                       f"times in 7 days with no answer", "risk": "andrew_only",
                              "evidence": {"metric": "unanswered", "n": n, "window": "7 days"}}, props, now)
            if got:
                made.append(got)
        for d in did:
            if d.get("verdict") != "worse" or not d.get("rule"):
                continue
            got = _put(acct, {"kind": "revert", "key": f"revert.{d['rule']}", "from": d.get("version"),
                              "to": {"rule": d["rule"], "key": d.get("key"), "words": d.get("words")}, "risk": "safe",
                              "words": f"Take back \"{d['words']}\": its posts did worse after it "
                                       f"({d['after']['median_x']:.1f}x against {d['before']['median_x']:.1f}x, weak "
                                       f"evidence)", "evidence": {"metric": "rel_24h", "n": d["after"]["n"],
                                                                  "window": "14 days either side"}}, props, now)
            if got:
                made.append(got)
    return made


def _find(acct, pid: str) -> dict:
    p = proposals(acct).get(str(pid))
    if p is None:
        raise SystemExit(f"{getattr(acct, 'name', acct)}: no proposal {pid}")
    return p


def approve(cfg: C.Config, acct, pid: str, by: str, now: Optional[datetime] = None) -> str:
    """Andrew approves a proposal: a factor, coach setting, runbook line or draft rule goes into the playbook; an
    experiment starts (signed by him); a revert brings back the earlier playbook; advice is noted only."""
    now = _now(cfg, now).astimezone(timezone.utc)
    p = _find(acct, pid)
    if p.get("state") != "open":
        raise SystemExit(f"{pid} is {p.get('state')}, not open")
    kind = p.get("kind")
    if kind in ("factor", "coach", "runbook_note"):
        said = PB.apply(cfg, acct, p, by, now)
    elif kind == "draft_rule":
        said = PB.apply(cfg, acct, p, by, now) + "; " + _hand_draft(cfg, acct, p, by, now)
    elif kind == "revert":
        said = PB.take_back(cfg, acct, take_back_rule(p), by, now, proposal=str(pid))
    elif kind == "experiment":
        spec = p.get("to") if isinstance(p.get("to"), dict) else {}
        eid = new_experiment(cfg, acct, question=spec.get("question") or p.get("words"), kind=str(spec.get("kind")),
                             arms=spec.get("arms") or {"A": "as now", "B": "the change"},
                             assign=spec.get("assign", "alternate"), outlets=spec.get("outlets") or [],
                             by=by, signed=True, proposal=pid, now=now)
        said = f"{getattr(acct, 'name', acct)}: experiment {eid} started"
    else:
        said = f"{getattr(acct, 'name', acct)}: noted (advice changes nothing by itself)"
    with store.edit(proposals_path(acct), dict) as props:
        q = props.get(pid)
        if q is not None:
            q["state"] = "approved"
            q["decided"] = {"by": str(by), "at": _iso(now), "why": "", "reason": ""}
    return said


def take_back_rule(p: dict) -> str:
    """The one rule a take-back proposal removes (its id)."""
    to = p.get("to")
    if isinstance(to, dict) and to.get("rule"):
        return str(to["rule"])
    return str(p.get("key") or "").split(".", 1)[-1]


DRAFT_FN = "rules:draft_from"


def _hand_draft(cfg: C.Config, acct, p: dict, by: str, now: datetime) -> str:
    """An approved draft automation: handed to the Automations part as an unsigned practice draft when it offers
    rules.draft_from(cfg, acct, intent, words, by, now) -> str; otherwise noted only (what it says)."""
    spec = p.get("to") if isinstance(p.get("to"), dict) else {}
    f = REG.fn(DRAFT_FN)
    if f is not None:
        try:
            return str(f(cfg, acct, str(spec.get("intent") or ""), str(spec.get("words") or ""), by, now))
        except (Exception, SystemExit) as e:  # noqa: BLE001  (the note stands; the draft is made by hand)
            REG._log_error(DRAFT_FN, e)
    return "nothing is drafted or sent by this: make the rule on Inbox, Automations (it starts in practice)"


def draft_words(acct) -> str:
    """Where an approved draft automation goes, for the confirm (true to what approve does)."""
    if REG.fn(DRAFT_FN) is not None:
        return "the Automations page gets it as an unsigned draft in practice; nothing sends until you sign it there"
    return ("it is noted only: nothing is drafted or sent by this; make the rule on Inbox, Automations (it starts in "
            "practice)")


def decline(cfg: C.Config, acct, pid: str, by: str, reason: str, note: str = "",
            now: Optional[datetime] = None) -> str:
    """Andrew declines a proposal with a reason. "Test it first" makes an experiment instead (it waits for him to
    start it). The same change is not proposed again until its evidence doubles."""
    now = _now(cfg, now).astimezone(timezone.utc)
    if reason not in DECLINE_WORDS:
        raise SystemExit(f"reason: one of {', '.join(k for k, _ in DECLINE)}")
    p = _find(acct, pid)
    if p.get("state") != "open":
        raise SystemExit(f"{pid} is {p.get('state')}, not open")
    said = f"{getattr(acct, 'name', acct)}: declined ({DECLINE_WORDS[reason].lower()})"
    if reason == "test_first":
        spec = experiment_for(p)
        if spec is None:
            raise SystemExit(NOT_TESTABLE)
        eid = new_experiment(cfg, acct, question=f"Does it hold: {p.get('words')}", by=by, signed=False,
                             proposal=pid, now=now, **spec)
        said += f"; experiment {eid} is ready to start on the Experiments view"
    with store.edit(proposals_path(acct), dict) as props:
        q = props.get(pid)
        if q is not None:
            q["state"] = "declined"
            q["decided"] = {"by": str(by), "at": _iso(now), "why": str(note or "")[:300], "reason": reason}
            q["n_at_decline"] = int((q.get("evidence") or {}).get("n") or 0)
    return said


NOT_TESTABLE = ("This change cannot be split clip by clip, so an experiment could not test it: approve it or "
                "decline it with another reason.")


def experiment_for(p: dict) -> Optional[dict]:
    """The experiment "Test it first" makes of a proposal, or None when its change cannot be carried out clip by
    clip (a scout factor acts before a clip exists; a coach setting, a runbook line or a draft automation is not a
    per-clip treatment). Today that is a first comment kept or left off."""
    if str(p.get("key", "")).startswith("advice.first_comment"):
        ev = p.get("evidence") or {}
        return {"kind": "first_comment", "arms": {"A": "with the first comment", "B": "no first comment"},
                "assign": "alternate", "outlets": [ev["outlet"]] if ev.get("outlet") in DASH else []}
    return None


def _file_evidence(acct, by: str, day: Optional[date]) -> dict:
    """What a filed proposal rests on, when the file says nothing: the results coach's names the clips it read
    ("results coach, clips of 10.19"); anything else is the weekly report's."""
    if "results-coach" not in str(by):
        return {"metric": "weekly report", "n": 0}
    if day is None:
        return {"metric": "results coach", "n": 0}
    rep = store.read(report_path(acct, day), None)
    boards = sorted({str(c.get("board")) for c in (rep.get("clips") or []) if isinstance(c, dict)}) \
        if isinstance(rep, dict) else []
    try:
        ds = [date.fromisoformat(b) for b in boards]
    except ValueError:
        ds = []
    said = f"clips of {_window_words(ds[0], ds[-1])}" if ds else f"report for {day:%m.%d}"
    return {"metric": f"results coach, {said}", "n": 0, "day": day.isoformat()}


def propose_file(cfg: C.Config, acct, path: Path, by: str, now: Optional[datetime] = None,
                 day: Optional[date] = None) -> List[str]:
    """The weekly task's and the results coach's proposals (a JSON list): each {kind: runbook_note|advice|experiment|
    draft_rule, words, and to: the runbook line | {question, kind, arms, assign, outlets} | {intent, n}}. Checked;
    the ids added. `day` is the report they rest on (the results coach's evidence names its clips' day)."""
    now = _now(cfg, now).astimezone(timezone.utc)
    try:
        items = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise SystemExit(f"{path}: not readable JSON ({e})") from None
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        raise SystemExit(f"{path}: a list of proposals")
    clean = []
    for it in items:
        if not isinstance(it, dict) or it.get("kind") not in FILE_KINDS:
            raise SystemExit(f"each proposal's kind is one of {', '.join(FILE_KINDS)}")
        kind = it["kind"]
        words = PB._text(str(it.get("words") or ""), 300, "words")
        to = it.get("to")
        if kind == "runbook_note":
            line = PB._text(to if isinstance(to, str) else words, PB.RUNBOOK_CHARS, "a runbook line")
            key, to = PB.check("runbook.x", line)
        elif kind == "draft_rule":
            spec = to if isinstance(to, dict) else {}
            intent = str(spec.get("intent") or "")
            key, to = PB.check(f"draft_rule.{intent}", spec)
        elif kind == "experiment":
            spec = to if isinstance(to, dict) else {}
            _check_experiment(spec)
            key = f"experiment.{PB.slug_key(spec.get('question') or words)}"
            to = spec
        else:
            key = f"advice.weekly.{PB.slug_key(words)}"
            to = None
        clean.append({"kind": kind, "key": key, "from": None, "to": to, "words": words, "by": str(by),
                      "risk": "safe", "evidence": it.get("evidence") if isinstance(it.get("evidence"), dict) else
                      _file_evidence(acct, by, day)})
    made = []
    with store.edit(proposals_path(acct), dict) as props:
        for prop in clean:
            got = _put(acct, prop, props, now)
            if got:
                made.append(got)
    return made


# ====================================================================================================== backtest
def backtest(cfg: C.Config, acct, proposal: dict, scfg: Optional[dict] = None, boards: int = 7,
             now: Optional[datetime] = None) -> dict:
    """A scout factor proposal re-ranked over the last saved scout boards with scout.rank() (pure): how many finds
    would move into the top 10, and out. The rules are the scout's own (with today's playbook), plus the change."""
    from . import scout as SC
    if not str(proposal.get("key", "")).startswith("scout."):
        return {"boards": 0, "into_top10": 0, "out_of_top10": 0}
    key, value = PB.check(proposal["key"], proposal.get("to"))
    _, part, name = key.split(".", 2)
    if scfg is None:
        scfg = SC.load(_slug(acct))
    base = dict(scfg)
    blk = PB.scout_block(acct)
    if any(blk.values()):
        base["learned"] = blk
    new = dict(base)
    learned = {p: dict((base.get("learned") or {}).get(p) or {}) for p in PB.SCOUT_PARTS}
    learned[part][name] = value
    new["learned"] = learned
    files = sorted(store.path(_slug(acct), "scout").glob("????-??-??.json"))[-boards:] \
        if store.path(_slug(acct), "scout").is_dir() else []
    into = out = used = 0
    moved: List[str] = []
    for f in files:
        bd = store.read(f, {})
        finds = list((bd.get("finds") or {}).values()) if isinstance(bd, dict) else []
        if not finds:
            continue
        hours = float(bd.get("hours") or scfg.get("hours", 36))
        when = _when(bd.get("updated")) or _now(cfg, now)

        def top(rules: dict) -> List[str]:
            got, _, _ = SC.rank(copy.deepcopy(finds), rules, acct, hours, {}, {}, {}, None, when)
            got = [g for g in got if g.get("kind") == "video"]
            got.sort(key=lambda g: -float(g.get("score") or 0))
            return [g["id"] for g in got[:10]]
        a, b = top(base), top(new)
        used += 1
        ins = [i for i in b if i not in a]
        into += len(ins)
        out += len([i for i in a if i not in b])
        titles = {g["id"]: g.get("title", "") for g in finds}
        moved += [titles.get(i, i)[:60] for i in ins][: max(0, 3 - len(moved))]
    return {"boards": used, "into_top10": into, "out_of_top10": out, "examples": moved}


# ====================================================================================================== experiments
def experiments(acct) -> Dict[str, dict]:
    d = store.read(experiments_path(acct), {})
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def _check_experiment(spec: dict) -> None:
    if not isinstance(spec, dict) or not str(spec.get("question") or "").strip():
        raise SystemExit("experiment: {question, kind, arms {A, B}, assign, outlets}")
    if spec.get("kind") not in EXP_KINDS:
        raise SystemExit(f"experiment kind: one of {', '.join(EXP_KINDS)} (a test arm B can carry out clip by clip)")
    if spec.get("assign", "alternate") not in ASSIGN:
        raise SystemExit(f"experiment assign: one of {', '.join(ASSIGN)}")
    arms = spec.get("arms") or {"A": "as now", "B": "the change"}
    if not isinstance(arms, dict) or sorted(arms) != ["A", "B"]:
        raise SystemExit("experiment arms: {\"A\": words, \"B\": words}")
    bad = [o for o in spec.get("outlets") or [] if o not in DASH]
    if bad:
        raise SystemExit(f"experiment outlets: {', '.join(bad)} is not a platform")


def new_experiment(cfg: C.Config, acct, *, question: str, kind: str, arms: Optional[dict] = None,
                   assign: str = "alternate", outlets: Sequence[str] = (), by: str = "", signed: bool = False,
                   proposal: str = "", now: Optional[datetime] = None) -> str:
    """A new A/B test: fixed assignment, the metric rel_24h, at least 10 posts an arm, 21 days at most. Signed
    (running) when Andrew approved it; a draft otherwise, until he starts it."""
    now = _now(cfg, now).astimezone(timezone.utc)
    spec = {"question": question, "kind": kind, "arms": arms or {"A": "as now", "B": "the change"},
            "assign": "trial_reel" if kind == "trial_reel" else assign, "outlets": list(outlets or [])}
    if kind == "trial_reel":
        spec["outlets"] = ["instagram_reels"]
    _check_experiment(spec)
    with store.edit(experiments_path(acct), dict) as exps:
        n = 1 + max([int(k.split("-")[1]) for k in exps if re.fullmatch(r"e-\d+", k)] or [0])
        eid = f"e-{n:04d}"
        exps[eid] = {"id": eid, "question": _plain(spec["question"])[:300], "kind": kind,
                     "arms": {k: _plain(v)[:120] for k, v in spec["arms"].items()}, "assign": spec["assign"],
                     "outlets": spec["outlets"], "metric": "rel_24h", "min_n": EXP_MIN_N, "max_days": EXP_MAX_DAYS,
                     "state": "running" if signed else "draft", "posts": {"A": [], "B": []}, "result": None,
                     "proposal": proposal, "made": _iso(now), "made_by": str(by),
                     "signed": {"by": str(by), "at": _iso(now)} if signed else None,
                     "started": _iso(now) if signed else None, "ended": None}
    return eid


def sign_experiment(cfg: C.Config, acct, eid: str, by: str, now: Optional[datetime] = None) -> str:
    now = _now(cfg, now).astimezone(timezone.utc)
    with store.edit(experiments_path(acct), dict) as exps:
        e = exps.get(eid)
        if not isinstance(e, dict):
            raise SystemExit(f"no experiment {eid}")
        if e.get("state") != "draft":
            raise SystemExit(f"{eid} is {e.get('state')}: only a draft can be started")
        e.update(state="running", signed={"by": str(by), "at": _iso(now)}, started=_iso(now))
    return f"{getattr(acct, 'name', acct)}: experiment {eid} started; new clips get arm A or B as they are collected"


def stop_experiment(cfg: C.Config, acct, eid: str, by: str, now: Optional[datetime] = None) -> str:
    now = _now(cfg, now).astimezone(timezone.utc)
    with store.edit(experiments_path(acct), dict) as exps:
        e = exps.get(eid)
        if not isinstance(e, dict):
            raise SystemExit(f"no experiment {eid}")
        if e.get("state") not in ("draft", "running"):
            raise SystemExit(f"{eid} is already {e.get('state')}")
        e.update(state="stopped", ended=_iso(now), stopped_by=str(by))
    return f"{getattr(acct, 'name', acct)}: experiment {eid} stopped; clips collected from now on get no arm"


def arm_for(exp: dict, day: date, slot_key: str, n_so_far: int, slots: Sequence[str] = ()) -> str:
    """The arm a clip gets, fixed by the rule (never by chance): the slot's place in the day, the day itself, or
    taking turns (a trial reel takes turns too)."""
    how = exp.get("assign", "alternate")
    if how == "slot_parity":
        idx = list(slots).index(slot_key) if slot_key in slots else int(slot_key.replace(":", "")) // 100
        return "A" if idx % 2 == 0 else "B"
    if how == "day_parity":
        return "A" if day.toordinal() % 2 == 0 else "B"
    return "A" if n_so_far % 2 == 0 else "B"


def on_collect(ctx) -> None:
    """collect's hook (only while learn.experiments is not off): each running, signed experiment gives the clip an
    arm. On: the arm goes into the clip's extras (signed with it) and does what it says (arm B of a first-comment
    test drops the first comment; arm B of a trial-reel test makes the Instagram post a trial reel). Practice: the
    arm is written down in experiments.json only, and nothing about the clip changes."""
    acct = ctx.acct
    mode = SW.mode(acct, "learn.experiments")
    if mode == "off":
        return None
    slot = ctx.slot
    key = str(slot.get("slot") or "")
    day_s = ctx.day.isoformat()
    clip_sha = str((slot.get("clip") or {}).get("sha256") or "")
    pre_hooks: List[str] = []              # arm B of a pre_hook test: the files change after the lock (ffmpeg)
    with store.edit(experiments_path(acct), dict) as exps:
        for eid, e in sorted(exps.items()):
            if not isinstance(e, dict) or e.get("state") != "running" or not e.get("signed"):
                continue
            if e.get("start_on") and day_s < str(e["start_on"]):
                continue                   # signed ahead of its first board (the pre-hook test: 2026-11-03)
            outlets = list(e.get("outlets") or [])
            posts = e.setdefault("posts", {"A": [], "B": []})
            why_not = "" if mode == "on" else "the experiments switch is practice"
            if not why_not:
                why_not = _cannot_apply(acct, e, ctx.extras, slot)
            entry = {"board": day_s, "slot": key, "clip_sha": clip_sha, "at": _iso(datetime.now(timezone.utc)),
                     "practice": bool(why_not)}
            if why_not:
                entry["why"] = why_not
            # a clip taken into a slot again (Make again, a new delivery) keeps the slot's arm: collect cleared the
            # last clip's extras, so the arm and what it does are put on the new clip, and its entry follows it
            have = next(((a, i) for a in ("A", "B") for i, p in enumerate(posts.get(a) or [])
                         if p.get("board") == day_s and p.get("slot") == key), None)
            given = _clip_punch(slot).get("arm") if e.get("assign") == "given" else None
            if have is not None and given in (None, have[0]):
                arm = have[0]
                posts[arm][have[1]] = entry
            else:
                if have is not None:           # a new clip in the slot whose own arm is the other one
                    posts[have[0]].pop(have[1])
                n = sum(len(posts.get(a) or []) for a in ("A", "B"))
                arm = given if given in ("A", "B") else arm_for(e, ctx.day, key, n, getattr(acct, "slots", []) or [])
                posts.setdefault(arm, []).append(entry)
            if why_not:
                continue
            ctx.extras.setdefault("experiment", {})[eid] = arm
            if arm == "B" and e.get("kind") == "first_comment":
                fc = ctx.extras.get("first_comment")
                if isinstance(fc, dict):
                    for o in outlets or list(fc):
                        fc.pop(o, None)
                    if not fc:
                        ctx.extras.pop("first_comment", None)
            if arm == "B" and e.get("kind") == "trial_reel":
                ctx.extras.setdefault("trial", {})["instagram_reels"] = {
                    "graduation_strategy": "SS_PERFORMANCE", "experiment": eid, "arm": arm}
            if arm == "B" and e.get("kind") == "pre_hook":
                pre_hooks.append(eid)
    for eid in pre_hooks:
        from . import prehook as PH
        _rec, why = PH.apply(ctx.cfg, acct, slot)
        if why:                            # it could not go on: the clip is as delivered and does not count
            ctx.extras.get("experiment", {}).pop(eid, None)
            if not ctx.extras.get("experiment"):
                ctx.extras.pop("experiment", None)
            with store.edit(experiments_path(acct), dict) as exps:
                for p in ((exps.get(eid) or {}).get("posts") or {}).get("B") or []:
                    if p.get("board") == day_s and p.get("slot") == key:
                        p.update(practice=True, why=why)
    return None


def _cannot_apply(acct, e: dict, extras: dict, slot: Optional[dict] = None) -> str:
    """Why an experiment cannot change this clip ('' when it can): a test whose arm B would change nothing is only
    written down (as practice), so it never counts toward a result."""
    kind = e.get("kind")
    if kind == "punch_cut":
        # cut at render by the engine, which drew the arm: a clip it wrote no eligible record for is not in the test
        rec = _clip_punch(slot)
        if not rec:
            return "no punch-cut record with the clip (the big frame or the punch cuts are off, or an older render)"
        if not rec.get("eligible"):
            return f"not in the punch-cut test: {rec.get('why') or 'not a one-camera clip'}"
        return ""
    if kind == "pre_hook":
        # asked of BOTH arms, so a clip too long for any pre-hook is left out of A as well as B (no length bias)
        from . import prehook as PH
        if (slot or {}).get("vs_card"):
            return "it opens on its VS card (vscard.py): one opener, never two"
        # THE OPENER RULE (Daily 8 step 3, 2026-10-29): a pre-hook only ever goes on a calm start, so the test
        # compares calm starts with and without one; a fight, a shout or a strong first line sits out of both arms
        from . import opener as OP
        ok, why = OP.calm(acct, slot or {})
        if not ok:
            return f"not a calm start ({why}): the opener rule gives it no pre-hook"
        lib = PH.library()
        if not lib:
            return "the pre-hook library is empty or missing"
        files = PH.ceilings(acct, slot or {})
        if not files:
            return "the slot holds no clip"
        if PH.pick(acct, files, lib) is None:
            return "no pre-hook fits under the tightest ceiling"
        return ""
    if kind == "trial_reel":
        return "" if SW.mode(acct, "trial_reels") == "on" else "it waits for the trial reels switch"
    if kind == "first_comment":
        fc = extras.get("first_comment") if isinstance(extras, dict) else None
        outs = list(e.get("outlets") or []) or (list(fc) if isinstance(fc, dict) else [])
        if not isinstance(fc, dict) or not outs or not any(fc.get(o) for o in outs):
            return "the clip has no first comment to leave off"
        # in practice a first comment is only rehearsed: neither arm would have one posted, so nothing differs
        from . import firstcomment as FC
        if any(fc.get(o) and FC.mode(acct, o) != "on" for o in outs):
            return "it waits for the first comment switch"
        return ""
    return "a label only: arm B changes nothing about the clip"


def conclude(cfg: C.Config, acct, rows: List[dict], now: datetime) -> List[dict]:
    """Each running experiment's count so far; one with 10 clips an arm with a 24-hour number (or past 21 days) is
    concluded: B against A by rel_24h, a 90% range, and a proposal from its result. A clip counts once: its value is
    the mean rel_24h over the experiment's platforms it posted to (each platform is already against its own usual),
    so three platforms never pass for three clips. Clips the test could not change (practice) are left out."""
    by_slot: Dict[Tuple[str, str], List[dict]] = {}
    for r in rows:
        by_slot.setdefault((r["board"], r["slot"]), []).append(r)
    out: List[dict] = []
    results: List[Tuple[str, dict]] = []
    with store.edit(experiments_path(acct), dict) as exps:
        for eid, e in sorted(exps.items()):
            if not isinstance(e, dict):
                continue
            arms: Dict[str, List[float]] = {"A": [], "B": []}
            for arm in ("A", "B"):
                for p in (e.get("posts") or {}).get(arm, []):
                    if p.get("practice"):
                        continue
                    vals = [float(r["rel_24h"]) for r in by_slot.get((p.get("board"), p.get("slot")), [])
                            if (not e.get("outlets") or r["outlet"] in e["outlets"]) and r.get("rel_24h") is not None]
                    if vals:
                        arms[arm].append(sum(vals) / len(vals))
            na, nb = len(arms["A"]), len(arms["B"])
            e["counts"] = {"A": na, "B": nb}
            if e.get("state") == "running":
                started = _when(e.get("started")) or now
                if e.get("start_on"):          # signed ahead: its days count from its first board
                    started = max(started, _when(f"{e['start_on']}T00:00:00+00:00") or started)
                due = na >= int(e.get("min_n") or EXP_MIN_N) and nb >= int(e.get("min_n") or EXP_MIN_N)
                late = now - started >= timedelta(days=int(e.get("max_days") or EXP_MAX_DAYS))
                if due or late:
                    a, b = arms["A"], arms["B"]
                    if a and b:
                        d = SM.median(b) - SM.median(a)
                        ci = SM.bootstrap_ci(b, a, n=BOOT_N, seed=_seed(eid))
                        _, p = SM.mann_whitney(b, a)
                        lift = math.exp(d)
                        # out of days short of min_n an arm: the counts and lift are written down, and no winner is
                        # named (one clip an arm gives a range of one point, which "excludes 1" on paper only)
                        sure = due and ci is not None and (math.exp(ci[0]) > 1 or math.exp(ci[1]) < 1)
                        winner = ("B" if lift > 1 else "A") if sure else ""
                        words = (f"{e['arms']['B']} ran {lift:.1f}x against {e['arms']['A']} over {nb} and {na} clips"
                                 + ("" if sure else ": no clear winner" if due else ", ended short: no clear winner"))
                        e["result"] = {"n_a": na, "n_b": nb, "lift": round(lift, 3),
                                       "ci90": [round(math.exp(ci[0]), 3), round(math.exp(ci[1]), 3)] if ci else None,
                                       "p": round(p, 4), "winner": winner, "words": _plain(words),
                                       "short": not due}
                    else:
                        e["result"] = {"n_a": na, "n_b": nb, "winner": "", "short": True,
                                       "words": "Ended after 21 days without enough clips in both arms."}
                    e["state"] = "done"
                    e["ended"] = _iso(now)
                    results.append((eid, e))
            out.append({"id": eid, "question": e.get("question"), "state": e.get("state"), "counts": e["counts"],
                        "result": e.get("result")})
    for eid, e in results:
        res = e.get("result") or {}
        if not res.get("winner") or e.get("kind") not in EXP_KINDS:
            continue                      # a label-only test (an old "tag") changed nothing: it names no keeper
        with store.edit(proposals_path(acct), dict) as props:
            pid = _put(acct, {"kind": "advice", "key": f"advice.experiment.{eid}", "from": None, "to": None,
                              "words": f"Experiment {eid}: {res['words']}. Keep arm {res['winner']}.",
                              "evidence": {"metric": "rel_24h", "n": res.get("n_b"), "n_rest": res.get("n_a"),
                                           "lift": res.get("lift"), "ci90": res.get("ci90"), "p": res.get("p"),
                                           "window": "the experiment"}}, props, now)
        if pid:
            with store.edit(experiments_path(acct), dict) as exps:
                if eid in exps:
                    exps[eid]["proposal_result"] = pid
    return out


# ====================================================================================================== did it work
PREFER_FACTORS = ("title_number", "title_question", "title_quote", "title_name", "title_caps", "hook_on_screen")
NOT_MEASURABLE = "not measurable: no group of posts it changes to compare"
NOT_IN_FORCE = "not in force: the playbook switch was off"


def _rule_rows(rule: dict, rows: List[dict]) -> Optional[List[dict]]:
    """The posts a rule is about (None when no group of posts can be told apart for it: a runbook line, a draft
    automation, a coach weight, a scout person or heat word, which the rows do not carry)."""
    key, v = str(rule.get("key") or ""), rule.get("value")
    if key.startswith("scout.channels."):
        name = key.split(".", 2)[2]
        return [r for r in rows if r.get("creator_key") == name]
    if key.startswith("coach.length_best."):
        o = key.split(".", 2)[2]
        try:
            lo, hi = float(v[0]), float(v[1])
        except (TypeError, ValueError, IndexError):
            return None
        return [r for r in rows if r["outlet"] == o and lo <= float(r.get("sent_s") or 0) <= hi]
    if key == "coach.first_word_max_s":
        return [r for r in rows if isinstance((r.get("hook_feat") or {}).get("first_word_s"), (int, float))
                and r["hook_feat"]["first_word_s"] <= float(v)]
    if key.startswith(("coach.prefer.", "coach.avoid.")):
        f = next((f for f in PREFER_FACTORS if factor_words(f, "yes") == str(v)), None)
        if f is None:
            return None
        fn = FACTOR_OF[f][0]
        out = []
        for r in rows:
            try:
                if fn(r) is True:
                    out.append(r)
            except Exception:  # noqa: BLE001
                continue
        return out
    return None


def _switch_on_from(acct, at: datetime) -> Optional[datetime]:
    """When the rule approved at `at` came into force: `at` itself when learn.playbook was on then, else the first
    time the switch went on after it; None while it has not been on since (the scout and coach never read it)."""
    raw = store.read(SW.file_path(acct), {})
    hist = [h for h in (raw.get("history") if isinstance(raw, dict) else None) or []
            if isinstance(h, dict) and h.get("key") == "learn.playbook" and _when(h.get("at")) is not None]
    hist.sort(key=lambda h: _when(h["at"]))
    before = [h for h in hist if _when(h["at"]) <= at]
    if before:
        state = before[-1].get("to")
    elif hist:
        state = hist[0].get("from")
    else:
        state = SW.mode(acct, "learn.playbook")          # no history: as it stands now
    if state == "on":
        return at
    nxt = next((h for h in hist if _when(h["at"]) > at and h.get("to") == "on"), None)
    return _when(nxt["at"]) if nxt else None


def did_it_work(cfg: C.Config, acct, rows: List[dict], now: datetime) -> List[dict]:
    """14 days after a rule came into force (approved while learn.playbook was on, or the switch went on after): its
    clips' rel_24h in the 14 days after against the 14 days before (3 clips a side at least). Weak evidence by nature (other things changed
    too), and labelled so. Worse by 10% or more files a take-back of that one rule. A rule never in force is "not in
    force" (asked again each day); one with no group of posts to compare is "not measurable"."""
    pb = PB.read(acct, now)
    done = store.read(did_path(acct), {})
    done = done if isinstance(done, dict) else {}
    out: List[dict] = []
    hist = PB.history(acct)
    wrote = False
    for rule in pb["rules"]:
        rid = str(rule.get("id") or "")
        approved = _when(rule.get("approved"))
        if not rid or approved is None or now - approved < timedelta(days=DID_IT_WORK_DAYS):
            continue
        if rid in done:
            out.append(done[rid])
            continue
        ver = int(rule.get("version") or 0)
        prev = max([int(h["version"]) for h in hist if isinstance(h.get("version"), int) and h["version"] < ver],
                   default=0)
        row = {"rule": rid, "key": rule.get("key"), "words": PB.rule_words(rule),
               "version": ver, "version_before": prev, "checked": _iso(now), "evidence": "weak",
               "before": {"n": 0, "median_x": None}, "after": {"n": 0, "median_x": None}}
        group = _rule_rows(rule, rows)
        if group is None:
            row["verdict"] = NOT_MEASURABLE
            done[rid] = row
            wrote = True
            out.append(row)
            continue
        at = _switch_on_from(acct, approved)
        if at is None:
            row["verdict"] = NOT_IN_FORCE                # not kept: it is asked again once the switch is on
            out.append(row)
            continue
        if now - at < timedelta(days=DID_IT_WORK_DAYS):
            continue
        row["in_force_from"] = _iso(at)
        # a clip counts once (its mean rel_24h over the platforms it posted to, at its first post's time): the
        # rule is about clips, so one clip on three platforms never passes for three posts
        clips = []
        for grp in by_clip([r for r in group if r.get("rel_24h") is not None and _when(r.get("went"))]):
            clips.append((min(_when(r["went"]) for r in grp), sum(float(r["rel_24h"]) for r in grp) / len(grp)))
        before = [v for w, v in clips if at - timedelta(days=DID_IT_WORK_DAYS) <= w < at]
        after = [v for w, v in clips if at <= w < at + timedelta(days=DID_IT_WORK_DAYS)]
        row["before"] = {"n": len(before), "median_x": round(math.exp(SM.median(before)), 3) if before else None}
        row["after"] = {"n": len(after), "median_x": round(math.exp(SM.median(after)), 3) if after else None}
        if len(before) < 3 or len(after) < 3:
            row["verdict"] = "too few posts to tell"
        else:
            change = math.exp(SM.median(after) - SM.median(before))
            row["change"] = round(change, 3)
            row["verdict"] = "better" if change >= 1.1 else "worse" if change <= 0.9 else "about the same"
        done[rid] = row
        wrote = True
        out.append(row)
    if wrote:
        store.write(did_path(acct), done)
    return out


def did_it_work_of(acct) -> Dict[str, dict]:
    """The "did it work" checks made so far, by rule id (read only, for the Playbook view)."""
    d = store.read(did_path(acct), {})
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


# ====================================================================================================== the report
def _window_words(lo: date, hi: date) -> str:
    return f"{lo:%m.%d} to {hi:%m.%d}" if lo != hi else f"{hi:%m.%d}"


def report(cfg: C.Config, acct, day: Optional[date] = None, now: Optional[datetime] = None, write: bool = True,
           propose: bool = True, scfg: Optional[dict] = None) -> dict:
    """The day's report for one account (yesterday's by default). With write, features.json, report-<day>.json and
    report-<day>.md are written and proposals and experiments brought up to date."""
    now = _now(cfg, now)
    day = day or (_local(cfg, now).date() - timedelta(days=1))
    boards = _boards(acct)
    rows = rows_for(cfg, acct, now)
    add_baselines(rows)
    end = datetime.combine(day + timedelta(days=1), dtime(0, 0), tzinfo=cfg.tz())
    upto = [r for r in rows if _when(r["went"]) < end]
    min_n = int(SW.settings(acct).get("learn.min_n") or 12)
    works = analyse(upto, min_n, day.isoformat())
    best, worst, win = best_worst(cfg, upto, works, day)
    clips = ranked(cfg, upto, (day - timedelta(days=1), day))
    probs = problems(cfg, acct, upto, boards, day, now, works, win)
    n24 = Counter(r["outlet"] for r in upto if (r.get("views") or {}).get("24h") is not None)
    on_day = [r for r in rows if r["board"] == day.isoformat() or _local(cfg, _when(r["went"])).date() == day]
    views_day = sum(int(r.get("views_now") or 0) for r in on_day)
    prev = store.read(report_path(acct, day - timedelta(days=1)), {})
    views_prev = ((prev.get("kpis") or {}).get("views_day") if isinstance(prev, dict) else None)
    hl = publishing(cfg, acct, boards, rows, day, now)
    # each platform's usual at the end of the day (none while it has fewer than 5 posts with a 24-hour number), and
    # the posts of the window left out of best and worst because they went up before their platform had one
    usual = {}
    for o in sorted(set(_expected_outlets(acct, upto)) | set(n24)):
        u, n = _usual_of(_items(upto, o), end.timestamp())
        usual[o] = {"usual": round(u, 1) if u is not None else None, "n": n, "posts_24h": n24.get(o, 0)}
    in_win = [r for r in upto if win[0] <= _local(cfg, _when(r["went"])).date() <= win[1] and _v24(r) is not None]
    left_out = Counter(r["outlet"] for r in in_win
                       if r.get("rel_24h") is None and (usual.get(r["outlet"]) or {}).get("usual") is not None)
    est_n = sum(1 for r in upto if _v24(r) is not None and r.get("v24_src") in ("snapshots", "estimate"))
    rep = {
        "v": V, "account": _slug(acct), "name": getattr(acct, "name", _slug(acct)), "day": day.isoformat(),
        "made": _iso(now), "made_local": _local(cfg, now).strftime("%H:%M"),
        "window": {"from": win[0].isoformat(), "to": win[1].isoformat()},
        "kpis": {"posts_day": len(on_day), "views_day": views_day, "views_prev": views_prev,
                 "change": round(views_day / views_prev - 1, 3) if views_prev else None,
                 "views_lost": sum(p["views_lost"] for p in probs if not p.get("overlaps")),
                 "slots_planned": hl["slots"]["planned"], "slots_signed": hl["slots"]["signed"],
                 "success_rate": hl["success_rate"], "posts_24h": dict(n24)},
        "claims": {o: n24.get(o, 0) >= CLAIM_MIN for o in sorted(set(n24) | {r["outlet"] for r in rows})},
        "estimate": est_n > 0, "estimate_n": est_n, "n_24h": sum(n24.values()),
        "usual": usual, "left_out": dict(sorted(left_out.items())),
        "window_n": {"with_24h": len(in_win), "with_usual": sum(1 for r in in_win if r.get("rel_24h") is not None)},
        "problems": probs, "best": best, "worst": worst, "works": works, "clips": clips,
        "watch_usual": {o: watch_usual(upto, o, end.timestamp())
                        for o in sorted({p["outlet"] for c in clips for p in c["posts"]})},
        "notes": store.read(notes_path(acct, day), None),
        "coach_accuracy": coach_accuracy(upto), "health": hl,
        "verdicts": verdict_counts(acct, day - timedelta(days=6), day),
        "proposals_made": [], "experiments": [], "did_it_work": [],
    }
    if write:
        rep["experiments"] = conclude(cfg, acct, rows, now.astimezone(timezone.utc))
        rep["did_it_work"] = did_it_work(cfg, acct, rows, now.astimezone(timezone.utc))
        if propose:
            rep["proposals_made"] = make_proposals(cfg, acct, works, upto, now.astimezone(timezone.utc),
                                                   _window_words(win[0], win[1]), rep["did_it_work"], scfg=scfg)
        props = proposals(acct)
        rep["kpis"]["open_proposals"] = sum(1 for p in props.values() if p.get("state") == "open")
        keep = _local(cfg, now).date() - timedelta(days=KEEP_DAYS)
        store.write(features_path(acct), {"v": V, "account": _slug(acct), "made": _iso(now),
                                          "rows": [r for r in rows if r["board"] >= keep.isoformat()]})
        store.write(report_path(acct, day), rep)
        _write_text(report_path(acct, day, "md"), markdown(rep, props))
        _prune(acct, keep)
    return rep


def _prune(acct, keep: date) -> None:
    d = _dir(_slug(acct))
    for f in [*d.glob("report-*.*"), *d.glob("notes-*.*")] if d.is_dir() else []:
        m = re.match(r"(?:report|notes)-(\d{4}-\d{2}-\d{2})\.(json|md)$", f.name)
        if m and m.group(1) < keep.isoformat():
            try:
                f.unlink()
            except OSError:
                pass


def estimate_words(rep: dict) -> str:
    """Where the 24-hour numbers came from, when not all from a checkpoint ('' when every one did)."""
    est, n = int(rep.get("estimate_n") or 0), int(rep.get("n_24h") or 0)
    if not est and not rep.get("estimate"):
        return ""
    if not n or est >= n:
        return "The 24-hour numbers are read off the stats snapshots (an estimate) until checkpoint stats are on."
    return (f"{est} of the {n} posts with a 24-hour number have it from the stats snapshots, not a checkpoint "
            f"(an estimate).")


def usual_lines(rep: dict) -> List[str]:
    """One line for each platform with no usual yet: "No usual yet on Instagram: 4 posts with a 24-hour number so
    far, and it takes 5." Its posts are in no best, worst or views-lost figure until then."""
    out = []
    for o, u in sorted((rep.get("usual") or {}).items()):
        if not isinstance(u, dict) or u.get("usual") is not None:
            continue
        n, total = int(u.get("n") or 0), int(u.get("posts_24h") or 0)
        when = "so far" if n >= total else "in the last 30 days"
        out.append(f"No usual yet on {_ow(o)}: {n} post{_s(n)} with a 24-hour number {when}, and it takes "
                   f"{BASE_MIN}.")
    return out


def left_out_words(rep: dict) -> str:
    """The window's posts left out of best and worst for want of a usual when they went up ('' when none)."""
    lo = {o: n for o, n in (rep.get("left_out") or {}).items() if n}
    if not lo:
        return ""
    n = sum(lo.values())
    return (f"Left out of best and worst: {n} post{_s(n)} with fewer than {BASE_MIN} earlier posts on "
            f"{'its' if n == 1 else 'their'} platform to compare with ("
            + ", ".join(f"{_ow(o)} {k}" for o, k in sorted(lo.items(), key=lambda kv: _ow(kv[0]))) + ").")


def kept_apart_words(rep: dict) -> str:
    """Which shown problems are not in the day's views-lost total ('' when none has a figure)."""
    names = {"slow": "slow starts", "drop": "drops", "weak": "weak spots"}
    kinds = [names[k] for k in ("drop", "slow", "weak")
             if any(p.get("kind") == k and p.get("views_lost") for p in shown_problems(rep))]
    if not kinds:
        return ""
    said = kinds[0] if len(kinds) == 1 else ", ".join(kinds[:-1]) + " and " + kinds[-1]
    return f" The {said} below are kept out of this total: they are about how posts did, over more days than this one."


def none_words(rep: dict, best: bool) -> str:
    """Why the best (or worst) list is empty, in the window's own counts."""
    if not isinstance(rep.get("window_n"), dict):                  # a report made before 10.20 has no counts
        return "None above the usual yet." if best else "None below the usual yet."
    win = rep.get("window") or {}
    span = f"posts that went up {_window_words(date.fromisoformat(win['from']), date.fromisoformat(win['to']))}" \
        if win.get("from") and win.get("to") else "posts of the window"
    wn = rep.get("window_n") or {}
    with24, withu = int(wn.get("with_24h") or 0), int(wn.get("with_usual") or 0)
    if not with24:
        return f"No {span} have a 24-hour number yet."
    if not withu:
        return f"None of the {with24} {span} with a 24-hour number has a usual to compare with yet."
    return (f"None of the {withu} {span} with a usual ran 10% or more {'over' if best else 'under'} it.")


def markdown(rep: dict, props: Optional[Dict[str, dict]] = None) -> str:
    """The report as Andrew reads it: biggest problems first (views lost, with the fix), then the best and worst
    posts with why, what works with how sure, the coach, publishing, proposals. Short lines, plain words."""
    d = date.fromisoformat(rep["day"])
    k = rep["kpis"]
    out = [f"# {rep['name']}: report for {DAY_NAMES[d.weekday()]} {d:%m.%d.%Y}", ""]
    line = f"Made at {rep['made_local']}." if rep.get("made_local") else ""
    est = estimate_words(rep)
    if est:
        line += " " + est
    out += [line.strip(), ""]
    ch = k.get("change")
    out.append(f"Posts that went up that day, every platform: {k['posts_day']}, {_int_words(k['views_day'])} views "
               f"so far"
               + (f" ({'up' if ch >= 0 else 'down'} {abs(ch) * 100:.0f}% on the day before at the same point)."
                  if ch is not None else "."))
    out.append(f"Slots planned {k['slots_planned']}, signed {k['slots_signed']}. Views lost by the day's slots and "
               f"posts, estimated: about {_int_words(k['views_lost'])}." + kept_apart_words(rep))
    out += usual_lines(rep)
    out += ["", "## Biggest problems", ""]
    probs = shown_problems(rep)
    out += problem_lines(probs)
    if "clips" in rep:
        out += ["", "## Every clip, best to worst", ""] + clip_lines(rep)
    thin = sorted({_ow(b["outlet"]) for b in rep["best"] + rep["worst"] if int(b.get("baseline_n") or 0) < 10})
    for title, items in (("Best posts", rep["best"]), ("Worst posts", rep["worst"])):
        out += ["", f"## {title}", ""]
        if title == "Best posts":
            if thin:
                out.append(f"The usual is still rough on {', '.join(thin)}: it rests on fewer than 10 earlier posts.")
            lo_w = left_out_words(rep)
            if lo_w:
                out.append(lo_w)
        if not items:
            out.append(none_words(rep, title == "Best posts"))
        for i, b in enumerate(items, 1):
            est = " (a rough estimate)" if b.get("v24_src") == "estimate" else ""
            usual = (f", the usual {_int_words(b['baseline_24h'])}" if b.get("baseline_24h") is not None else "")
            out.append(f"{i}. \"{str(b['title'])[:90]}\" on {_ow(b['outlet'])}: "
                       f"{_int_words(b['views_24h'])} at 24 hours{est}{usual}.")
            if b.get("why"):
                out.append(f"   Why: {'; '.join(b['why'])}.")
    out += ["", "## What works", ""]
    if not rep["works"]:
        need = ", ".join(f"{_ow(o)} {n} of {CLAIM_MIN}" for o, n in
                         sorted((rep["kpis"].get("posts_24h") or {}).items()))
        out.append("Nothing stands out yet." + (f" Posts with a 24-hour number: {need}." if need else ""))
    for w in rep["works"][:10]:
        out.append(f"- {upper_first(w['words'])} on {_ow(w['outlet'])}: "
                   f"{x_words(w['lift'])} the usual ({CONF_WORDS.get(w['confidence'], w['confidence'])}).")
        out.append(f"  How sure: {sure_words(w)}.")
    no_claims = [_ow(o) for o, ok in sorted((rep.get("claims") or {}).items()) if not ok]
    if no_claims and rep["works"]:
        out.append(f"No claims yet for {', '.join(no_claims)}: fewer than {CLAIM_MIN} posts with a 24-hour number.")
    out += ["", "## Coach", "", rep["coach_accuracy"].get("words", "")]
    out += ["", "## Publishing", "", rep["health"].get("words", "") + "."]
    for o, v in sorted((rep["health"].get("by_outlet") or {}).items()):
        out.append(f"- {_ow(o)}: {v['posted']} posted, {v['failed']} failed, {v['gave_up']} gave up, "
                   + (f"{v.get('missing', 0)} never sent, " if v.get("missing") else "") + f"{v['late']} late.")
    vc = rep.get("verdicts") or {}
    if vc.get("decline") or vc.get("remake"):
        reasons = ", ".join(f"{VD.LABELS.get(c, c).lower()} {n}" for c, n in (vc.get("reasons") or {}).items())
        out.append(f"Declined in the last 7 days: {vc.get('decline', 0)}, made again: {vc.get('remake', 0)}"
                   + (f" ({reasons})." if reasons else "."))
    if rep.get("did_it_work"):
        out += ["", "## Did it work", ""]
        for d_ in rep["did_it_work"]:
            if d_["verdict"] in (NOT_MEASURABLE, NOT_IN_FORCE):
                out.append(f"- {d_['words']}: {d_['verdict']}.")
            else:
                out.append(f"- {d_['words']}: {d_['verdict']} (weak evidence; {d_['before']['n']} posts before, "
                           f"{d_['after']['n']} after).")
    if rep.get("experiments"):
        out += ["", "## Experiments", ""]
        for e in rep["experiments"]:
            res = (e.get("result") or {}).get("words")
            out.append(f"- {e['id']} {e['question']}: {e['state']}, A {e['counts']['A']} B {e['counts']['B']}"
                       + (f". {res}" if res else "") + ".")
    props = props or {}
    made_p = [props[i] for i in rep.get("proposals_made") or [] if i in props]
    open_n = sum(1 for p in props.values() if p.get("state") == "open")
    out += ["", "## Proposals", ""]
    if made_p:
        for p in made_p:
            out.append(f"- {p['words']} ({p['kind']}, {p['id']}).")
    out.append(f"{open_n} open: approve or decline them on Clip Review, Reports, Proposals." if open_n
               else "None open.")
    return _plain("\n".join(out).rstrip() + "\n")


def dated_words(p: dict, rep: Optional[dict] = None) -> str:
    """A problem's words with the day it belongs to (a report made the morning after puts the report day's problems
    beside the clips of the day before, so each line says which). Slots, posts and late posts are the report day's
    ("8 slots failed to make on 10.20"); slow starts and weak spots name the days their posts went up; a drop names
    its own days already; a platform gap is the usuals at the end of the report day."""
    words = str(p.get("words") or "")
    try:
        day = date.fromisoformat(str((rep or {}).get("day") or ""))
    except ValueError:
        return words
    kind = p.get("kind")
    if kind in ("slot", "post", "late"):
        head, sep, rest = words.partition(": ")         # "1 clip could not post to Instagram on 10.20: no ... cut"
        return f"{head} on {day:%m.%d}{sep}{rest}"
    if kind == "gap":
        return f"{words} (the usuals at the end of {day:%m.%d})"
    if kind in ("slow", "weak"):
        ev = [e for e in p.get("evidence") or [] if isinstance(e, dict)]
        days = []
        for e in ev:
            try:
                days.append(date.fromisoformat(str(e.get("board") or "")))
            except ValueError:
                pass
        win = (rep or {}).get("window") or {}
        if days and len(days) >= int(p.get("n") or 0):
            return f"{words} (posts of {_window_words(min(days), max(days))})"
        if win.get("from") and win.get("to"):
            return (f"{words} (posts of "
                    f"{_window_words(date.fromisoformat(win['from']), date.fromisoformat(win['to']))})")
    return words


def problem_lines(probs: List[dict], most: int = 8, rep: Optional[dict] = None) -> List[str]:
    """The numbered problems with their fix and which slots ("None found." when there are none); with the report,
    each says the day it belongs to (dated_words)."""
    out = [] if probs else ["None found."]
    for i, p in enumerate(probs[:most], 1):
        lost = lost_words(p)
        out.append(f"{i}. {dated_words(p, rep)}" + (f": {lost}." if lost else "."))
        out.append(f"   Fix: {p['fix']}")
        wh = which_words(p)
        if wh:
            out.append(f"   Which: {wh}.")
    if len(probs) > most:
        out.append(f"{len(probs) - most} more on Clip Review, Reports.")
    return out


# ------------------------------------------------------------------------------------------- every clip, in words
WATCH_WORDS = (("watch_pct", "{:.0f}% watched"), ("watch_s", "{:.0f}s on average"),
               ("skip_pct", "{:.0f}% skipped"), ("reach", "reach {:,.0f}"))


def _went_words(day: str, slot: str) -> str:
    try:
        return f"{date.fromisoformat(day):%m.%d} {slot}"
    except (TypeError, ValueError):
        return str(slot)


def watch_words(w: dict, keys: Sequence[str] = ("watch_pct", "skip_pct", "reach")) -> str:
    """'30% watched, 49% skipped, reach 127' from a post's watch numbers (or a usual's [median, n] pairs)."""
    out = []
    for k, f in WATCH_WORDS:
        v = (w or {}).get(k)
        v = v[0] if isinstance(v, (list, tuple)) else v
        if k in keys and isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(f.format(v))
    return ", ".join(out)


def post_words(p: dict) -> str:
    """'Facebook 1,011 (4.0x), 13% watched': one platform of a clip, as the ranking shows it ('YouTube 16 (the usual
    is 949)' for a post that platform held back: no x, it is not the clip's)."""
    x = p.get("x")
    if p.get("held_back"):
        how = f"the usual is {_int_words(p.get('baseline_24h'))}"
    elif x is not None:
        how = x_words(x)
    else:
        how = "no usual yet"
    s = f"{_ow(p['outlet'])} {_int_words(p.get('views_24h'))} ({how}{', a rough estimate' if p.get('estimate') else ''})"
    ww = watch_words(p.get("watch") or {})
    if ww:
        s += f", {ww}"
    if float(p.get("late_min") or 0) >= LATE_MIN:
        s += f", {float(p['late_min']) / 60:.1f} hours late"
    return s


def cut_words(text: str, most: int) -> str:
    """At most `most` characters, cut at a word boundary with "..." when it is longer (never mid-word)."""
    t = " ".join(str(text or "").split())
    if len(t) <= most:
        return t
    head = t[:max(1, most - 3) + 1]
    head = head[:head.rfind(" ")] if " " in head else head[:max(1, most - 3)]
    return head.rstrip(" ,.:;!?-") + "..."


def _clip_name(c: dict, most: int = 90) -> str:
    return f"\"{cut_words(c.get('title') or c.get('slug') or 'a clip', most)}\""


def _platform_lines(c: dict, indent: str = "   ") -> List[str]:
    """A clip's platforms: the ones that took it on one line, then the ones that held it back on their own line,
    beside the clip and never in its x."""
    took = [p for p in c.get("posts") or [] if not p.get("held_back")]
    held = [p for p in c.get("posts") or [] if p.get("held_back")]
    out = [indent + "; ".join(post_words(p) for p in took) + "."] if took else []
    if held:
        out.append(f"{indent}Held back, not in its x: " + "; ".join(post_words(p) for p in held) + ".")
    return out


def _held_words(c: dict) -> str:
    """'YouTube held it back (8, the usual 1,258)' for each platform that held the clip back ('' when none did)."""
    return "; ".join(f"{_ow(p['outlet'])} held it back ({_int_words(p['views_24h'])}, the usual "
                     f"{_int_words(p['baseline_24h'])})" for p in c.get("posts") or [] if p.get("held_back"))


def _took_words(c: dict) -> str:
    """'Facebook 502, Instagram 150': the platforms that took the clip, with their views at 24 hours."""
    return ", ".join(f"{_ow(p['outlet'])} {_int_words(p['views_24h'])}" for p in c.get("posts") or []
                     if not p.get("held_back"))


def _clip_x(c: dict) -> str:
    return f"{x_words(c['x'])} the usual" if c.get("x") is not None else "no usual yet"


def _notes_for(rep: dict) -> Dict[Tuple[str, str], str]:
    n = rep.get("notes") or {}
    return {(str(c.get("board")), str(c.get("slot"))): str(c.get("what") or "")
            for c in n.get("clips") or [] if isinstance(c, dict) and c.get("what")}


def clip_lines(rep: dict) -> List[str]:
    """Every clip of the window, best to worst: one numbered line each (its x, when and what it was), its platforms
    on the next line, and what happened in it when the results coach wrote it."""
    clips = clips_of(rep)
    if not clips:
        return ["No clip has a full first day in this report's window yet."]
    days = sorted({c["board"] for c in clips})
    out = [f"Clips that went up {', '.join(_went_words(d, '').strip() for d in days)}, ranked by their views at 24 "
           f"hours against the usual on the platforms that took them (a post a platform held back is shown beside "
           f"its clip, not in its x). Watch numbers are the latest read."]
    # each post's x is against its own usual (the posts before it); the watch numbers' usual is the platform's now
    usual = [f"{_ow(o)} {ww}" for o, w in sorted((rep.get("watch_usual") or {}).items(), key=lambda kv: _ow(kv[0]))
             for ww in (watch_words(w),) if ww]
    if usual:
        out.append("Usual watch numbers: " + "; ".join(usual) + ".")
    notes = _notes_for(rep)
    out.append("")
    for c in clips:
        what = ", ".join(x for x in (_went_words(c["board"], c["slot"]), c.get("format"),
                                     f"from {c['host']}" if c.get("host") else "") if x)
        out.append(f"{c['rank']}. {_clip_name(c)}: {_clip_x(c)} ({what}).")
        out += _platform_lines(c)
        if notes.get((c["board"], c["slot"])):
            out.append(f"   What happened: {notes[(c['board'], c['slot'])]}")
    return out


def minute_lines(rep: dict, open_n: int = 0) -> List[str]:
    """The report in a minute: the clips' total against the usual, the best and the worst clip, the biggest
    problem, the coach's summary when written, and the changes waiting for Andrew."""
    clips = clips_of(rep)
    head = f"**{rep['name']}**"
    held_n = sum(1 for c in clips for p in c["posts"] if p.get("held_back"))
    if clips:
        days = sorted({c["board"] for c in clips})
        with_u = [c for c in clips if c.get("x") is not None]
        tot = sum(c["views_24h"] for c in clips)
        usual = sum(float(c["usual_24h"]) for c in with_u)
        x = sum(c["views_took"] for c in with_u) / usual if with_u and usual > 0 else None
        head += (f" (clips of {', '.join(_went_words(d, '').strip() for d in days)}): {len(clips)} clip{_s(len(clips))}, "
                 f"{_int_words(tot)} views at 24 hours"
                 + ((f", {x_words(x)} the usual" + (" on the platforms that took them" if held_n else "") + ".")
                    if x is not None else "."))
    else:
        head += ": no clip with a full first day yet."
    out = [head]
    ranked_u = [c for c in clips if c.get("x") is not None]
    for label, c in (("Best", ranked_u[0] if ranked_u else None),
                     ("Worst", ranked_u[-1] if len(ranked_u) > 1 else None)):
        if c is not None:
            held = _held_words(c)
            out.append(f"- {label}: {_clip_name(c, 70)} {x_words(c['x'])}: {_took_words(c)}"
                       + (f"; {held}" if held else "") + ".")
    if held_n:
        outs = sorted({_ow(p["outlet"]) for c in clips for p in c["posts"] if p.get("held_back")})
        out.append(f"- Held back: {held_n} post{_s(held_n)} under a tenth of the usual on {', '.join(outs)} at 24 "
                   f"hours while another platform took the same clip: the platform, not the clip; check "
                   f"{'it' if held_n == 1 else 'them'} there.")
    probs = shown_problems(rep)
    if probs:
        p = probs[0]
        lost = lost_words(p)
        out.append(f"- Biggest problem: {dated_words(p, rep)}" + (f": {lost}." if lost else "."))
    summ = str((rep.get("notes") or {}).get("summary") or "")
    if summ:
        out.append(f"- Coach: {summ}")
    out.append(f"- Waiting for you: {open_n} change{_s(open_n)} to sign on Clip Review, Reports." if open_n
               else "- Waiting for you: nothing to sign.")
    return out


# ------------------------------------------------------------------------------------------- the file on the desk
DESK_NAME = "Clip report {:%m.%d.%Y}.md"


def desk_dir(cfg: C.Config) -> Optional[Path]:
    """The folder Andrew reads the morning report in (config/accounts.json "report_desk"; None when it is not set).
    A test or an offline run never writes outside a temp folder, whatever the config says."""
    try:
        v = json.loads(Path(cfg.path).read_text()).get("report_desk")
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if not v or not isinstance(v, str):
        return None
    p = Path(os.path.expanduser(v))
    from . import livestate as LS
    if LS.test_run():
        tmp = os.path.realpath(tempfile.gettempdir())
        if not os.path.realpath(p).startswith(tmp.rstrip(os.sep) + os.sep):
            return None
    return p


def desk_path(cfg: C.Config, day: date) -> Optional[Path]:
    """The morning's file for the report on `day` (it is named for the morning after, when it is read)."""
    d = desk_dir(cfg)
    return d / DESK_NAME.format(day + timedelta(days=1)) if d is not None else None


def desk_text(reps: List[dict], open_by: Dict[str, int], made_local: str = "") -> str:
    """Both accounts in one file: In one minute first, then each account's clips best to worst and its problems."""
    day = date.fromisoformat(reps[0]["day"])
    morn = day + timedelta(days=1)
    out = [f"# Clip report, {DAY_NAMES[morn.weekday()]} {morn:%m.%d.%Y}", ""]
    out.append((f"Made at {made_local}. " if made_local else "") + "Each clip is ranked on its first 24 hours "
               "against the usual on each platform; a clip shows up the morning after its first day is over.")
    out += ["", "## In one minute", ""]
    for r in reps:
        out += minute_lines(r, open_by.get(r["account"], 0)) + [""]
    for r in reps:
        k = r.get("kpis") or {}
        out += ["---", "", f"# {r['name']}", ""]
        out.append(f"Yesterday ({date.fromisoformat(r['day']):%m.%d}): slots planned {k.get('slots_planned', 0)}, "
                   f"signed {k.get('slots_signed', 0)}, {k.get('posts_day', 0)} posts so far.")
        out += usual_lines(r)
        out += ["", "## Every clip, best to worst", ""] + clip_lines(r)
        out += ["", "## Biggest problems", ""] + problem_lines(shown_problems(r), most=5, rep=r)
        extra = [str(x) for x in (r.get("notes") or {}).get("problems") or [] if x]
        if extra:
            out += ["", "What the coach saw:"] + [f"- {x}" for x in extra]
        works = [w for w in r.get("works") or [] if w.get("confidence") in ("strong", "likely")]
        if works:
            out += ["", "## What works", ""]
            out += [f"- {upper_first(w['words'])} on {_ow(w['outlet'])}: {x_words(w['lift'])} the usual "
                    f"({w['confidence']})." for w in works[:5]]
        hl = (r.get("health") or {}).get("words")
        if hl:
            out += ["", f"Publishing: {hl}."]
        out.append("")
    out.append("The full report for each account is on Clip Review, Reports. The changes the coach proposes wait "
               "there for your Approve; nothing changes until you sign it.")
    return _plain("\n".join(out).rstrip() + "\n")


def rewrite_for_proposals(cfg: C.Config, acct, day: date, now: Optional[datetime] = None) -> Optional[Path]:
    """After proposals are added: the day's report words and the morning file again, so "Waiting for you" counts
    them (10.21: the coach's notes rewrote the file at 05:09:33, its 2 proposals came at 05:09:42, and the file said
    nothing to sign). The morning file's path, or None when there is none to write."""
    rep = store.read(report_path(acct, day), None)
    if isinstance(rep, dict):
        _write_text(report_path(acct, day, "md"), markdown(rep, proposals(acct)))
    return write_desk(cfg, day, now)


def write_desk(cfg: C.Config, day: date, now: Optional[datetime] = None) -> Optional[Path]:
    """The morning file for `day`'s reports (every owner account with a report that day), written whole. None when
    no folder is set, the folder is missing, or there is no report yet."""
    p = desk_path(cfg, day)
    if p is None or not p.parent.is_dir():
        return None
    reps = []
    for a in _owners(cfg):
        r = store.read(report_path(a, day), None)
        if isinstance(r, dict) and r.get("day") == day.isoformat():
            reps.append(r)
    if not reps:
        return None
    open_by = {r["account"]: sum(1 for q in proposals(cfg.account(r["account"])).values() if q.get("state") == "open")
               for r in reps}
    made = max((str(r.get("made_local") or "") for r in reps), default="")
    tmp = p.with_name("." + p.name + ".tmp")
    tmp.write_text(desk_text(reps, open_by, made))
    os.replace(tmp, p)
    return p


# ------------------------------------------------------------------------------------------- the results coach
NOTE_CHARS, NOTE_CLIPS, NOTE_PROBLEMS = 300, 8, 3


def notes_path(acct, day) -> Path:
    return _dir(_slug(acct)) / f"notes-{day}.json"


def write_notes(cfg: C.Config, acct, day: date, path: Path, by: str, now: Optional[datetime] = None) -> str:
    """The results coach's words for a day's report (a JSON file): {"summary": one or two sentences, "clips":
    [{"board", "slot", "what"}] for clips in that report's ranking, "problems": [lines]}. Checked (each clip must be
    in the ranking; no long dashes, no exclamation marks, 300 characters a note), kept beside the report, then the
    report and the morning file are written again with them. Never changes a number."""
    now = _now(cfg, now)
    rep = store.read(report_path(acct, day), None)
    if not isinstance(rep, dict):
        raise SystemExit(f"{getattr(acct, 'name', acct)}: no report for {day} yet (factory.py learn daily first)")
    try:
        data = json.loads(Path(path).expanduser().read_text())
    except (OSError, ValueError) as e:
        raise SystemExit(f"{path}: not readable JSON ({e})") from None
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: an object with summary, clips and problems")
    have = {(c["board"], c["slot"]) for c in rep.get("clips") or []}
    clips = []
    for c in data.get("clips") or []:
        if not isinstance(c, dict):
            raise SystemExit("each clip note is {\"board\": \"YYYY-MM-DD\", \"slot\": \"HH:MM\", \"what\": \"...\"}")
        key = (str(c.get("board") or ""), str(c.get("slot") or ""))
        if key not in have:
            raise SystemExit(f"clip {key[0]} {key[1]} is not in the report's ranking for {day} "
                             f"(factory.py learn clips {_slug(acct)} --date {day} lists them)")
        clips.append({"board": key[0], "slot": key[1], "what": PB._text(c.get("what"), NOTE_CHARS, "what happened")})
    if len(clips) > NOTE_CLIPS:
        raise SystemExit(f"at most {NOTE_CLIPS} clip notes (the best and the worst)")
    probs = [PB._text(x, 200, "a problem") for x in data.get("problems") or []]
    if len(probs) > NOTE_PROBLEMS:
        raise SystemExit(f"at most {NOTE_PROBLEMS} problems")
    summ = data.get("summary")
    notes = {"day": day.isoformat(), "by": str(by), "at": _iso(now),
             "summary": PB._text(summ, NOTE_CHARS, "summary") if summ else "", "clips": clips, "problems": probs}
    store.write(notes_path(acct, day), notes)
    rep["notes"] = notes
    store.write(report_path(acct, day), rep)
    _write_text(report_path(acct, day, "md"), markdown(rep, proposals(acct)))
    desk = write_desk(cfg, day, now)
    return (f"{getattr(acct, 'name', acct)}: notes for {day} kept ({len(clips)} clip{_s(len(clips))}, "
            f"{len(probs)} problem{_s(len(probs))})" + (f"; {desk}" if desk else ""))


def clips_text(cfg: C.Config, acct, day: date) -> List[str]:
    """What the results coach reads for each ranked clip of a day's report: its numbers on every platform against
    the usual, the coach's scorecard from before it posted (when there is one) and its whole transcript."""
    rep = store.read(report_path(acct, day), None)
    if not isinstance(rep, dict):
        raise SystemExit(f"{getattr(acct, 'name', acct)}: no report for {day} yet (factory.py learn daily first)")
    try:
        from . import coach as CO
    except Exception:  # noqa: BLE001  (the coach part may not be built here)
        CO = None
    clips = clips_of(rep)
    days = sorted({c["board"] for c in clips})
    went = ", ".join(_went_words(d, "").strip() for d in days) or "none yet"
    out = [f"{rep['name']} ({_slug(acct)}): report for {day}, {len(clips)} clips best to worst",
           f"Report day {day:%m.%d}: its problems below belong to {day:%m.%d} unless a line names other days. The "
           f"clips ranked went up {went}: a clip shows up the morning after its first day is over."]
    out += [ln for ln in clip_lines(rep)[:2]]
    out += ["", f"Problems (report day {day:%m.%d}):"] + [f"  {ln}" for ln in
                                                          problem_lines(shown_problems(rep), most=5, rep=rep)]
    boards: Dict[str, dict] = {}
    for c in clips:
        bd = boards.setdefault(c["board"], store.read(store.path(_slug(acct), f"{c['board']}.json"), {}) or {})
        s = (bd.get("slots") or {}).get(c["slot"]) or {}
        out += ["", f"{c['rank']}. board {c['board']} slot {c['slot']} ({c.get('slug')}): {_clip_name(c)}: "
                    f"{_clip_x(c)}"] + _platform_lines(c)
        out.append(f"   format {c.get('format') or 'not known'}, creator or source {c.get('host') or 'not known'}, "
                   f"{(s.get('clip') or {}).get('seconds', '?')}s")
        sc = {}
        if CO is not None:
            try:
                sc = CO.load(acct, date.fromisoformat(c["board"]), c["slot"]) or {}
            except Exception:  # noqa: BLE001
                sc = {}
        wr = sc.get("written") or {}
        if wr.get("scores"):
            out.append(f"   coach before it posted: {json.dumps(wr.get('scores'), sort_keys=True)}; fix: "
                       f"{wr.get('fix', '')}; why: {wr.get('why', '')}")
        elif (sc.get("measured") or {}).get("total") is not None:
            out.append(f"   coach before it posted: measured {sc['measured']['total']} (no written scores)")
        cues = []
        if CO is not None and s:
            try:
                sha = str((s.get("clip") or {}).get("sha256") or "")
                inp = CO.inputs_dir(acct, sha)
                srt = inp / "clip.srt" if (inp / "clip.srt").is_file() else CO._srt_of(s)
                cues = CO.parse_srt(srt.read_text(errors="replace")) if srt is not None else []
            except Exception:  # noqa: BLE001
                cues = []
        out.append("   transcript (each line at its cue's own start):" if cues else
                   "   transcript: (none beside this clip)")
        out += [f"     {ln}" for ln in transcript_lines(cues)]
    return out


def transcript_lines(cues: Sequence[Tuple[float, float, str]], most: int = 400) -> List[str]:
    """A clip's cues, one line each at the cue's own start ('[0:23] words ...', seconds rounded down), in time
    order; the first `most` lines. 10.21: 10-second blocks made "lands at 0:20" mean anywhere from 0:20 to 0:29."""
    out = []
    for st, _, words in sorted(cues, key=lambda c: float(c[0])):
        w = " ".join(str(words).split())
        if w:
            s = max(0, int(float(st)))
            out.append(f"[{s // 60}:{s % 60:02d}] {w}")
    return out[:most]


# ====================================================================================================== the week
def week(cfg: C.Config, acct, end: Optional[date] = None, now: Optional[datetime] = None) -> dict:
    """The last 7 reports put together, for the weekly written report and the Week view."""
    now = _now(cfg, now)
    end = end or (_local(cfg, now).date() - timedelta(days=1))
    start = end - timedelta(days=6)
    reps = []
    for i in range(7):
        r = store.read(report_path(acct, start + timedelta(days=i)), None)
        if isinstance(r, dict):
            reps.append(r)
    probs: Dict[str, dict] = {}
    for r in reps:
        for p in r.get("problems") or []:
            q = probs.setdefault(p["key"], {"key": p["key"], "words": p["words"], "n": 0, "views_lost": 0,
                                            "fix": p.get("fix"), "days": 0, "overlaps": p.get("overlaps", False)})
            q["n"] += int(p.get("n") or 0)
            q["views_lost"] += int(p.get("views_lost") or 0)
            q["days"] += 1
    rows = store.read(features_path(acct), {})
    rows = rows.get("rows") if isinstance(rows, dict) else []
    wk = [r for r in rows or [] if isinstance(r, dict) and start.isoformat() <= r.get("board", "") <= end.isoformat()]
    latest = reps[-1] if reps else {}
    ranked = sorted([r for r in wk if r.get("rel_24h") is not None], key=lambda r: -r["rel_24h"])
    works = latest.get("works") or []
    pat = REG.fn("ideas:patterns")
    try:
        ideas = pat(acct) if pat is not None else None
    except Exception:  # noqa: BLE001
        ideas = None
    props = proposals(acct)
    iso = end.isocalendar()
    return {"account": _slug(acct), "name": getattr(acct, "name", _slug(acct)), "week": f"{iso[0]}-W{iso[1]:02d}",
            "from": start.isoformat(), "to": end.isoformat(), "reports": len(reps), "posts": len(wk),
            "views": sum(int(r.get("views_now") or 0) for r in wk),
            "views_24h": sum(int((r.get("views") or {}).get("24h") or 0) for r in wk),
            "problems": sorted(probs.values(), key=lambda p: -p["views_lost"]),
            "best": [_post(r, works, True) for r in ranked[:5]],
            "worst": [_post(r, works, False) for r in list(reversed(ranked))[:5] if r not in ranked[:5]],
            "works": works, "coach_accuracy": latest.get("coach_accuracy"),
            "verdicts": verdict_counts(acct, start, end),
            "health": {"posted": sum(int((r.get("health") or {}).get("posted") or 0) for r in reps),
                       "failed": sum(int((r.get("health") or {}).get("failed") or 0) for r in reps)},
            "proposals": {"open": sum(1 for p in props.values() if p.get("state") == "open"),
                          "decided": [{"id": p["id"], "state": p["state"], "words": p.get("words"),
                                       "reason": (p.get("decided") or {}).get("reason")} for p in props.values()
                                      if (p.get("decided") or {}).get("at", "") >= start.isoformat()]},
            "experiments": [{"id": k, "question": e.get("question"), "state": e.get("state"),
                             "counts": e.get("counts"), "result": e.get("result")}
                            for k, e in sorted(experiments(acct).items())],
            "playbook": {"version": PB.read(acct)["version"], "rules": len(PB.read(acct)["rules"])},
            "ideas": ideas}


# ====================================================================================================== the job
def daily(cfg: C.Config, now: Optional[datetime] = None, quiet: bool = True, day: Optional[date] = None,
          accts: Optional[Sequence] = None, by_hand: bool = False) -> List[str]:
    """The 04:40 job: yesterday's report for each account whose `learn` switch is on (by hand: every owner account,
    or the ones named, switch or not), then the morning file on Andrew's desk (accounts.json "report_desk") with every
    account that has a report that day. What it says."""
    now = _now(cfg, now)
    if accts is None:
        accts = [a for a in _owners(cfg) if by_hand or SW.mode(a, "learn") == "on"]
    out: List[str] = []
    days = set()
    for a in accts:
        try:
            rep = report(cfg, a, day=day, now=now)
        except Exception as e:  # noqa: BLE001  (one account never stops the next)
            REG._log_error(f"learn:daily {a.slug}", e)
            out.append(f"{a.name}: the report failed ({type(e).__name__}: {e})")
            continue
        k = rep["kpis"]
        out.append(f"{a.name}: report for {rep['day']}: {len(rep['problems'])} problem"
                   f"{_s(len(rep['problems']))}, about {_int_words(k['views_lost'])} views lost, "
                   f"{len(rep['proposals_made'])} new proposal{_s(len(rep['proposals_made']))} "
                   f"({report_path(a, rep['day'], 'md')})")
        days.add(date.fromisoformat(rep["day"]))
    for d in sorted(days):
        try:
            got = write_desk(cfg, d, now)
        except Exception as e:  # noqa: BLE001  (the reports are kept whatever happens to the morning file)
            REG._log_error("learn:desk", e)
            out.append(f"the morning file failed ({type(e).__name__}: {e})")
            continue
        if got is not None:
            out.append(f"morning file: {got}")
    return out if (out or not quiet) else []


# ====================================================================================================== the plugs
CARD_CSS = ""          # the tag is styled in place (registry.card_css() stays "" until a part needs a class)
XP_BOX = "border:1px solid var(--line);border-radius:6px;padding:0 10px;margin-top:8px;font-size:12.5px"
XP_SUM = "display:flex;align-items:center;gap:4px;min-height:40px;cursor:pointer;color:var(--sub);font-weight:550"


def card_part(cfg: C.Config, acct, day: date, s: dict, now: datetime) -> str:
    """The experiment tag on a draft card: which experiment the clip is in and its arm ('' when it is in none, or
    while learn.experiments is off)."""
    if SW.mode(acct, "learn.experiments") == "off":
        return ""
    key = str(s.get("slot") or "")
    day_s = day.isoformat() if isinstance(day, date) else str(day)
    tags = []
    for eid, e in sorted(experiments(acct).items()):
        for arm in ("A", "B"):
            for p in (e.get("posts") or {}).get(arm, []):
                if p.get("board") == day_s and p.get("slot") == key:
                    tags.append((eid, arm, e, p.get("practice")))
    if not tags:
        return ""
    import html as _h
    parts = []
    for eid, arm, e, practice in tags[:2]:
        what = (e.get("arms") or {}).get(arm, "")
        parts.append(f'<details class="xp-tag" data-k="xp-{_h.escape(eid)}" style="{XP_BOX}">'
                     f'<summary style="{XP_SUM}">Experiment {_h.escape(eid)}: <b style="color:var(--ink)">arm {arm}</b>'
                     f'{" (practice)" if practice else ""}</summary><p style="margin:0 0 8px;color:var(--sub)">'
                     f'{_h.escape(str(e.get("question") or ""))} Arm {arm}: {_h.escape(str(what))}.</p></details>')
    return "".join(parts)


def _latest(acct, now: datetime, cfg: C.Config) -> dict:
    y = _local(cfg, now).date() - timedelta(days=1)
    r = store.read(report_path(acct, y), None)
    return r if isinstance(r, dict) else {}


def summary(cfg: C.Config, now: datetime, accts) -> List[dict]:
    """The Today band: yesterday's views and change, the best post, how many problems, open proposals. [] while
    every learn switch is off."""
    on = [a for a in accts or [] if not _is_client(a) and SW.mode(a, "learn") == "on"]
    if not on:
        return []
    now = _now(cfg, now)
    views = prev = 0
    has_prev = True
    best = None
    n_prob = n_open = 0
    href_acct = f"&acct={on[0].slug}" if len(on) == 1 else ""
    for a in on:
        r = _latest(a, now, cfg)
        k = r.get("kpis") or {}
        views += int(k.get("views_day") or 0)
        if k.get("views_prev"):
            prev += int(k["views_prev"])
        else:
            has_prev = False
        for b in r.get("best") or []:
            if best is None or float(b.get("views_24h") or 0) > float(best[1].get("views_24h") or 0):
                best = (a, b)
        # the day's own losses, and a sudden drop on a platform (it needs a look today, though it overlaps)
        n_prob += sum(1 for p in r.get("problems") or [] if p.get("views_lost")
                      and (not p.get("overlaps") or p.get("kind") == "drop"))
        n_open += sum(1 for p in proposals(a).values() if p.get("state") == "open")
    out: List[dict] = []
    if any(_latest(a, now, cfg) for a in on):
        ch = ""
        if has_prev and prev:
            pct = (views / prev - 1) * 100
            ch = f", {'up' if pct >= 0 else 'down'} {abs(pct):.0f}%"
        out.append({"key": "learn-views", "words": f"Yesterday {k_words(views)} views{ch}",
                    "href": f"/reports?view=today{href_acct}", "level": "calm", "order": 10})
    if best is not None:
        out.append({"key": "learn-best", "words": f"Best: {_best_name(best[1])} {k_words(best[1].get('views_24h'))} on "
                                                  f"{OUTLET_WORDS.get(best[1].get('outlet'), '')}".strip(),
                    "href": f"/reports?view=today{href_acct}", "level": "calm", "order": 11})
    if n_prob:
        out.append({"key": "learn-problems", "words": f"{n_prob} problem{_s(n_prob)}",
                    "href": f"/reports?view=today{href_acct}", "level": "warn", "order": 12})
    if n_open:
        out.append({"key": "learn-proposals", "words": f"{n_open} proposal{_s(n_open)}",
                    "href": f"/reports?view=proposals{href_acct}", "level": "calm", "order": 13})
    return out


def _best_name(b: dict, most: int = 32) -> str:
    """Who or what the best post was, for a chip: its creator or speaker when the slot names one, else its title cut
    at a word (never a slug's first word: GTA slugs are phrases such as "the-thing-has-completely-crashed")."""
    host = str(b.get("host") or "").strip()
    if host:
        return host[:most]
    t = " ".join(str(b.get("title") or "").split())
    if len(t) <= most:
        return f'"{t}"' if t else "a post"
    cut = t[:most + 1].rsplit(" ", 1)[0] or t[:most]
    return f'"{cut}..."'


def morning_line(cfg: C.Config, now: datetime) -> str:
    """'1 proposal' for the 06:30 notice's "Also:" line, after "the clip report is in Daily Reviews" when this
    morning's file is there ('' when there is neither, or every learn switch is off)."""
    on = [a for a in _owners(cfg) if SW.mode(a, "learn") == "on"]
    n = sum(1 for a in on for p in proposals(a).values() if p.get("state") == "open")
    parts = []
    dp = desk_path(cfg, _local(cfg, _now(cfg, now)).date() - timedelta(days=1)) if on else None
    if dp is not None and dp.is_file():
        parts.append(f"the clip report is in {dp.parent.name}")
    if n:
        parts.append(f"{n} proposal{_s(n)}")
    return "; ".join(parts)


def health(cfg: C.Config, now: datetime, accts) -> Tuple[str, str]:
    """A calm line above the Clips page when an account's report for yesterday was not made by 05:30."""
    now = _now(cfg, now)
    if _local(cfg, now).time() < dtime(5, 30):
        return ("", "")
    miss = [a.name for a in accts or [] if not _is_client(a) and SW.mode(a, "learn") == "on"
            and not report_path(a, _local(cfg, now).date() - timedelta(days=1)).exists()]
    if not miss:
        return ("", "")
    return ("calm", f"Reports: yesterday's report was not made for {', '.join(miss)} "
                    f"(factory.py learn daily makes it now).")



def doctor(cfg: C.Config, now: Optional[datetime] = None) -> List[Tuple[Optional[bool], str, str]]:
    """Per account: whether the 04:40 report runs and was made, open proposals, the playbook switch, and how many
    posts have a 24-hour number (claims wait for 30 on a platform). Read only."""
    now = _now(cfg, now)
    out: List[Tuple[Optional[bool], str, str]] = []
    for a in _owners(cfg):
        try:
            out += _doctor_account(cfg, a, now)
        except Exception as e:  # noqa: BLE001  (the doctor never stops over one account)
            out.append((False, f"{getattr(a, 'name', a)} learn: its check failed ({type(e).__name__}: {e})",
                        f"look at state/{a.slug}/learn/"))
    try:
        if any(SW.mode(a, "learn") == "on" for a in _owners(cfg)):
            out.append(_doctor_desk(cfg, now))
    except Exception as e:  # noqa: BLE001
        out.append((False, f"morning file: its check failed ({type(e).__name__}: {e})", "factory.py learn desk"))
    return out


def _doctor_desk(cfg: C.Config, now: datetime) -> Tuple[Optional[bool], str, str]:
    """The morning file on Andrew's desk: off (no report_desk), its folder missing, made, or not made by 05:30."""
    d = desk_dir(cfg)
    if d is None:
        return (None, "morning file: off (config/accounts.json \"report_desk\" names the folder it goes in)", "")
    if not d.is_dir():
        return (False, f"morning file: its folder {d} is missing", "make the folder, or fix report_desk")
    p = desk_path(cfg, _local(cfg, now).date() - timedelta(days=1))
    if p is not None and p.is_file():
        at = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        return (True, f"morning file: {p.name} in {d.name}, written {_local(cfg, at):%H:%M}", "")
    if _local(cfg, now).time() >= dtime(5, 30):
        return (False, f"morning file: {p.name if p else ''} not made in {d.name}", "factory.py learn desk")
    return (None, f"morning file: made at 04:40 in {d.name}", "")


def _doctor_account(cfg: C.Config, a, now: datetime) -> List[Tuple[Optional[bool], str, str]]:
    """One account's doctor lines (see doctor)."""
    out: List[Tuple[Optional[bool], str, str]] = []
    y = _local(cfg, now).date() - timedelta(days=1)
    on = SW.mode(a, "learn") == "on"
    props = proposals(a)
    n_open = sum(1 for p in props.values() if p.get("state") == "open")
    if on:
        rp = report_path(a, y)
        if rp.exists():
            rep = store.read(rp, {})
            made = _when(rep.get("made")) if isinstance(rep, dict) else None
            at = _local(cfg, made).strftime("%H:%M") if made else "?"
            out.append((True, f"{a.name} reports: made {at}, {n_open} open proposal{_s(n_open)}", ""))
        elif _local(cfg, now).time() >= dtime(5, 30):
            out.append((False, f"{a.name} reports: yesterday's report was not made",
                        f"factory.py learn daily --account {a.slug}"))
        else:
            out.append((None, f"{a.name} reports: on, made at 04:40", ""))
    else:
        out.append((None, f"{a.name} reports: off (the 04:40 report waits for factory.py social on {a.slug} "
                          f"learn; factory.py learn daily makes one by hand)", ""))
    if SW.mode(a, "learn.playbook") == "on":
        pb = PB.read(a, now)
        out.append((None, f"{a.name} playbook: on, version {pb['version']}, {len(pb['rules'])} rule"
                          f"{_s(len(pb['rules']))} in force", ""))
    else:
        out.append((None, f"{a.name} playbook: off (the scout and coach read no learned factors)", ""))
    if SW.mode(a, "learn.experiments") != "off":
        run = sum(1 for e in experiments(a).values() if e.get("state") == "running")
        out.append((None, f"{a.name} experiments: {SW.mode(a, 'learn.experiments')}, {run} running", ""))
    else:
        out.append((None, f"{a.name} experiments: off (signed experiments wait for factory.py social on "
                          f"{a.slug} learn.experiments --practice; factory.py learn experiments lists them)", ""))
    feats = store.read(features_path(a), None)
    rows = feats.get("rows") if isinstance(feats, dict) else None
    if isinstance(rows, list):
        n24 = Counter(r.get("outlet") for r in rows if isinstance(r, dict)
                      and (r.get("views") or {}).get("24h") is not None)
        total = sum(n24.values())
        ready = [OUTLET_WORDS.get(o, o) for o, n in sorted(n24.items()) if n >= CLAIM_MIN]
        if ready:
            out.append((None, f"{a.name}: {total} posts with a 24h point: claims made for {', '.join(ready)}", ""))
        else:
            out.append((None, f"{a.name}: {total} posts with a 24h point: numbers only, no claims yet", ""))
    return out


# ====================================================================================================== the command
def _acct(cfg: C.Config, slug: str):
    a = cfg.account(slug)
    if _is_client(a):
        raise SystemExit(f"{a.name} is a client's account: the learner is for the owner's own accounts")
    return a


def _by(cfg: C.Config, by: Optional[str]) -> str:
    return str(by or (cfg.approvers[0] if cfg.approvers else "factory.py"))


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.py learn", description="the learner: reports, proposals, playbook")
    sub = ap.add_subparsers(dest="action", required=True)
    q = sub.add_parser("daily", help="write the day's report (yesterday by default; every account, switch or not)")
    q.add_argument("account", nargs="?")
    q.add_argument("--account", dest="account_opt")
    q.add_argument("--date", help="YYYY-MM-DD, today or yesterday (the day reported)")
    q.add_argument("--json", action="store_true")
    q.add_argument("--quiet", action="store_true")
    q.add_argument("--no-proposals", action="store_true", help="write the report only")
    q = sub.add_parser("week", help="the last 7 reports together")
    q.add_argument("account")
    q.add_argument("--date", help="the week's last day (yesterday by default)")
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("brief", help="40 lines for the runbooks: the playbook, what works, problems, best posts")
    q.add_argument("account")
    q = sub.add_parser("propose", help="add proposals from a JSON file (the weekly task, the results coach); the "
                                       "day's report and the morning file are written again with them")
    q.add_argument("account")
    q.add_argument("--file", required=True)
    q.add_argument("--by", default="clip-weekly-report")
    q.add_argument("--date", help="the day reported (yesterday by default)")
    q = sub.add_parser("approve", help="approve a proposal")
    q.add_argument("account")
    q.add_argument("id")
    q.add_argument("--by")
    q = sub.add_parser("decline", help="decline a proposal")
    q.add_argument("account")
    q.add_argument("id")
    q.add_argument("--reason", required=True, choices=[k for k, _ in DECLINE])
    q.add_argument("--note", default="")
    q.add_argument("--by")
    q = sub.add_parser("proposals", help="the proposals")
    q.add_argument("account")
    q.add_argument("--all", action="store_true")
    q = sub.add_parser("playbook", help="the playbook in force")
    q.add_argument("account")
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("revert", help="make the playbook an earlier version again")
    q.add_argument("account")
    q.add_argument("version", type=int)
    q.add_argument("--by")
    q = sub.add_parser("backtest", help="re-rank the last 7 scout boards with a proposal's factor")
    q.add_argument("account")
    q.add_argument("id")
    q = sub.add_parser("experiments", help="the experiments; --sign or --stop one")
    q.add_argument("account")
    g = q.add_mutually_exclusive_group()
    g.add_argument("--sign", metavar="ID")
    g.add_argument("--stop", metavar="ID")
    q.add_argument("--by")
    q = sub.add_parser("features", help="the per-post rows the report rests on")
    q.add_argument("account")
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("clips", help="the report's clips best to worst, with numbers and transcripts (the results "
                                     "coach reads it)")
    q.add_argument("account")
    q.add_argument("--date", help="the day reported (yesterday by default)")
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("notes", help="the results coach's words for a report (a JSON file)")
    q.add_argument("account")
    q.add_argument("--file", required=True)
    q.add_argument("--date", help="the day reported (yesterday by default)")
    q.add_argument("--by", default="clip-results-coach")
    q = sub.add_parser("desk", help="write the morning file again from the reports on disk")
    q.add_argument("--date", help="the day reported (yesterday by default)")
    q = sub.add_parser("weekly-save", help="keep the weekly written report under state/<account>/learn/weekly/")
    q.add_argument("account")
    q.add_argument("--file", required=True)
    q.add_argument("--week", help="YYYY-Www (this week by default)")
    return ap


def _day_arg(cfg: C.Config, s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    if s == "yesterday":
        return cfg.now().date() - timedelta(days=1)
    return C.parse_day(s, cfg)


def cli(cfg: C.Config, argv: List[str]) -> None:
    a = _parser().parse_args(list(argv))
    act = a.action
    if act == "daily":
        slug = a.account or a.account_opt
        accts = [_acct(cfg, slug)] if slug else _owners(cfg)
        day = _day_arg(cfg, a.date)
        if a.json:
            reps = [report(cfg, x, day=day, propose=not a.no_proposals) for x in accts]
            print(json.dumps(reps if len(reps) != 1 else reps[0], indent=1, default=str))
            return
        if a.no_proposals:
            for x in accts:
                rep = report(cfg, x, day=day, propose=False)
                print(f"{x.name}: report for {rep['day']} ({report_path(x, rep['day'], 'md')})")
            return
        for ln in daily(cfg, quiet=False, day=day, accts=accts, by_hand=True):
            if not a.quiet or "failed" in ln:
                print(ln)
    elif act == "week":
        w = week(cfg, _acct(cfg, a.account), end=_day_arg(cfg, a.date))
        if a.json:
            print(json.dumps(w, indent=1, default=str))
        else:
            print(f"{w['name']} week {w['week']} ({w['from']} to {w['to']}): {w['posts']} posts, "
                  f"{_int_words(w['views'])} views, {w['reports']} daily reports")
            for p in w["problems"][:6]:
                print(f"  problem: {p['words']} ({p['days']} day{_s(p['days'])}): about {_int_words(p['views_lost'])} "
                      f"views lost")
            for b in w["best"][:3]:
                print(f"  best: {str(b['title'])[:70]} on {OUTLET_WORDS.get(b['outlet'])}: "
                      f"{_int_words(b['views_24h'])} at 24 hours")
    elif act == "brief":
        print(PB.brief(_acct(cfg, a.account)))
    elif act == "propose":
        acct = _acct(cfg, a.account)
        day = _day_arg(cfg, a.date) or (cfg.now().date() - timedelta(days=1))
        made = propose_file(cfg, acct, Path(a.file), a.by, day=day)
        desk = rewrite_for_proposals(cfg, acct, day) if made else None
        print(f"{acct.name}: {len(made)} proposal{_s(len(made))} added" + (f": {', '.join(made)}" if made else
                                                                          " (the same ones are open or declined)")
              + (f"; {desk}" if desk else ""))
    elif act == "approve":
        print(approve(cfg, _acct(cfg, a.account), a.id, _by(cfg, a.by)))
    elif act == "decline":
        print(decline(cfg, _acct(cfg, a.account), a.id, _by(cfg, a.by), a.reason, a.note))
    elif act == "proposals":
        acct = _acct(cfg, a.account)
        for pid, p in sorted(proposals(acct).items()):
            if a.all or p.get("state") == "open":
                print(f"{pid}  {p.get('state'):<9} {p.get('kind'):<12} {p.get('words')}")
    elif act == "playbook":
        acct = _acct(cfg, a.account)
        pb = PB.read(acct)
        if a.json:
            print(json.dumps(pb, indent=1))
        else:
            print(f"{acct.name}: playbook version {pb['version']} (switch {SW.mode(acct, 'learn.playbook')})")
            for r in pb["rules"]:
                print(f"  {PB.rule_words(r)} (until {str(r.get('expires'))[:10]})")
            if not pb["rules"]:
                print("  nothing approved yet")
    elif act == "revert":
        print(PB.revert(cfg, _acct(cfg, a.account), a.version, _by(cfg, a.by)))
    elif act == "backtest":
        acct = _acct(cfg, a.account)
        p = _find(acct, a.id)
        print(json.dumps(backtest(cfg, acct, p), indent=1))
    elif act == "experiments":
        acct = _acct(cfg, a.account)
        if a.sign:
            print(sign_experiment(cfg, acct, a.sign, _by(cfg, a.by)))
        elif a.stop:
            print(stop_experiment(cfg, acct, a.stop, _by(cfg, a.by)))
        else:
            for eid, e in sorted(experiments(acct).items()):
                c = {arm: len((e.get("posts") or {}).get(arm) or []) for arm in ("A", "B")}
                print(f"{eid}  {e.get('state'):<8} A {c['A']} B {c['B']}  {e.get('question')}")
    elif act == "features":
        acct = _acct(cfg, a.account)
        rows = rows_for(cfg, acct)
        add_baselines(rows)
        if a.json:
            print(json.dumps(rows, indent=1, default=str))
        else:
            for r in rows:
                v = (r.get("views") or {}).get("24h")
                print(f"{r['board']} {r['slot']} {r['outlet']:<16} 24h {v if v is not None else '-':>7} "
                      f"rel {r['rel_24h'] if r['rel_24h'] is not None else '-':>7}  {str(r['title'])[:60]}")
    elif act == "clips":
        acct = _acct(cfg, a.account)
        day = _day_arg(cfg, a.date) or (cfg.now().date() - timedelta(days=1))
        if a.json:
            rep = store.read(report_path(acct, day), None)
            if not isinstance(rep, dict):
                raise SystemExit(f"{acct.name}: no report for {day} yet (factory.py learn daily first)")
            print(json.dumps({"day": rep["day"], "clips": rep.get("clips") or [], "usual": rep.get("usual"),
                              "watch_usual": rep.get("watch_usual"), "notes": rep.get("notes")}, indent=1))
        else:
            for ln in clips_text(cfg, acct, day):
                print(ln)
    elif act == "notes":
        acct = _acct(cfg, a.account)
        day = _day_arg(cfg, a.date) or (cfg.now().date() - timedelta(days=1))
        print(write_notes(cfg, acct, day, Path(a.file), a.by))
    elif act == "desk":
        day = _day_arg(cfg, a.date) or (cfg.now().date() - timedelta(days=1))
        got = write_desk(cfg, day)
        print(f"morning file: {got}" if got else "no morning file: report_desk is not set in config/accounts.json, "
                                                 "its folder is missing, or there is no report for that day")
    elif act == "weekly-save":
        acct = _acct(cfg, a.account)
        try:
            text = Path(a.file).read_text()
        except OSError as e:
            raise SystemExit(f"{a.file}: {e}") from None
        if len(text) > 200_000:
            raise SystemExit(f"{a.file}: over 200 KB")
        wk = a.week or "{}-W{:02d}".format(*cfg.now().date().isocalendar()[:2])
        if not re.fullmatch(r"\d{4}-W\d{2}", wk):
            raise SystemExit("--week: YYYY-Www")
        dest = weekly_dir(acct) / f"{wk}.md"
        _write_text(dest, _plain(text))
        print(f"{acct.name}: kept as {dest}")

