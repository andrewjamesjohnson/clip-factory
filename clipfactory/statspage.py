"""The review page's Stats tab: every post's numbers, every account, every platform, in one place.

    http://127.0.0.1:8781/stats        (the Clips | Stats tabs at the top of the review page)

The numbers are the ones `factory.py stats` reads from YouTube, Facebook, Instagram and TikTok every night at 03:30
(launchd com.clipfactory.stats) and keeps on each slot of the boards, plus the videos on each account that match no
clip (state/stats/pulled). Update now runs the same read in the background (stats.update, one read at a time), and so
does opening the tab when the last read is over an hour old. TikTok numbers are read for the public videos on the
connected accounts; an 'Only me' test post has none and is not counted.

The page gets every post at once and does the adding up itself, so the filters (days, account, platform, count by
content or channel) answer without a trip to the server. state/stats/settings.json holds the payout rates, the monthly
goal, which programs each account is in (eligible) and the reassign rules (a post counted for another account's
content, or left out). Writing them needs the page's token, as Approve does.

Money is two figures, never one: what the programs an account is in would pay (actual), and every view at the plan
rate (an estimate, and labelled so). The run-rate comes from the history snapshots stats.py keeps, and so does
This month: the views gained since the month's first snapshot (stats.month).

How each post grew (checkpoints.py, when it is built and switched on for the account): a Top clips row opens a panel
that asks /stats/series for that post alone (never in /stats/data, which is polled and hashed whole): its views at
1h, 3h, 6h, 24h, 72h, 7d and 30d against the account's usual on that platform, the extra numbers in a table, and the
retention line when Facebook or YouTube gave one. The Top clips can then also rank by Avg watch, Skip rate, Reach and
Saves (?view=extras), once any post has them.

How long people watch (2026-10-19): each post carries the watch fields it has (stats.WATCH: average share of the clip
watched, average seconds, skipped in the first 3 seconds, YouTube's engaged views). The page shows them on each post
in Top clips and as the middle post (median) per account or format and platform, counting only the posts that have
the field. YouTube has them once its channel is signed in with YouTube Analytics; the page names the ones still not.

Why clips came back (reasons.py): each Pull and Decline by its reason code, per account, the week it was given; and
per check, what it missed (a clip it passed that a person pulled or declined) and what it held for nothing (the
automatic sign-off waited on it and a person approved the clip unchanged).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import config as C
from . import outlets as O
from . import poster as P
from . import stats as ST

# Payout per 1,000 views. Every one is an estimate (read 2026-10-17; Meta and TikTok publish no rate):
#   youtube      0.05 per 1,000 ENGAGED views; range $0.01-0.07 (vidIQ 2026). The read counts every play, so it runs high.
#   tiktok       0: Creator Rewards pays nothing on a video of 60 seconds or less.
#   tiktok_long  0.35 per raw view = $0.40-1.00 per 1,000 qualified (Toptal 2026) x 40-70% qualified (platforms.md).
#   instagram    0: no general per-view program (bonuses are invite only).
#   facebook     0.30 IF INVITED; $1-10 (ShortSync 2026), $0.30-5.00 (the engine's platforms.md): the low end.
#   campaign     1.00: clipping campaigns, $0.50-1.50 on Whop (ClipAffiliates 07-2026), $1.25-3 (Elev8or 10-2026).
# eligible: the programs each account is IN. Only those count as actual pay. All false on 2026-10-17: Facebook is
# invite only, and neither account has YouTube's or TikTok's numbers yet. The owner flips them (the page or this file).
# reassign: first rule that fits wins. to = an account (counted as that account's content), "exclude" or "keep".
#   A rule fits on account, platform, before / after (YYYY-MM-DD, the day it went up) and ids (video ids).
#   Count by channel (what each platform counts, for its programs) ignores them.
# followers_typed: a follower count the owner typed on the page where the platform cannot be read yet (TikTok, until
#   its sign-in has user.info.stats): {account: {platform: {"n": 1234, "at": "YYYY-MM-DD"}}}. Shown as typed.
# count_by: what the page counts by until a viewer picks: "channel" (as before 2026-10-17) or "content" (the spec's
#   pick: the August hearing uploads count as the Politics's). An owner call: set "content" to make it the default.
_NO = {"youtube": False, "tiktok": False, "facebook": False, "instagram": False}
DEFAULT_SETTINGS = {"rpm": {"youtube": 0.05, "tiktok": 0.0, "tiktok_long": 0.35, "instagram": 0.0, "facebook": 0.30,
                            "campaign": 1.00},
                    "goal": 1000.0,
                    "eligible": {"gaming": dict(_NO), "politics": dict(_NO)},
                    "reassign": [
                        {"account": "gaming", "platform": "youtube", "before": "2026-09-01", "to": "politics",
                         "why": "hearing uploads on the GTA channel before the accounts split"},
                        {"ids": ["bscYT55A5OA", "oDySe-pZ8gU"], "to": "exclude", "why": "duplicate test uploads"}],
                    "count_by": "channel"}
CAMPAIGN_FEES = 0.10    # clipping campaigns keep about 7-10% of the payout
MIN_GAP = 120       # seconds: Update now never reads the platforms twice inside this
FIELDS = ("id", "account", "platform", "title", "url", "pid", "dur", "fmt", "host", "rights", "clip", "slot", "frozen",
          "upd", "views", "likes", "comments", "shares", "bench")
PLATS = ("youtube", "tiktok", "facebook", "instagram")
WATCH_FIELDS = ("watch_s", "watch_pct", "skip_pct", "left_3s_pct", "engaged_pct", "reach", "saved", "replays")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _dir() -> Path:
    return C.STATE / "stats"


def _when(iso: str) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------------------------------------------ settings
def _rule(r: object) -> Optional[dict]:
    """One reassign rule, cleaned; None when it is not one."""
    if not isinstance(r, dict) or not isinstance(r.get("to"), str) or not r["to"]:
        return None
    out: dict = {"to": r["to"]}
    for k in ("account", "platform", "why"):
        if isinstance(r.get(k), str) and r[k]:
            out[k] = r[k]
    for k in ("before", "after"):
        if isinstance(r.get(k), str) and _DAY.match(r[k]):
            out[k] = r[k]
    if isinstance(r.get("ids"), list):
        out["ids"] = [str(x) for x in r["ids"] if isinstance(x, (str, int))]
    return out if len(set(out) - {"to", "why"}) else None       # a rule that fits everything is a mistake


def settings() -> dict:
    """Payout per 1,000 views by platform, the monthly goal in dollars, the programs each account is in, and the
    reassign rules. A missing or broken file, or a missing key, gives the defaults."""
    try:
        raw = json.loads((_dir() / "settings.json").read_text())
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    rates = raw.get("rpm") if isinstance(raw.get("rpm"), dict) else {}
    rpm = {k: float(rates[k]) if isinstance(rates.get(k), (int, float)) and not isinstance(rates[k], bool)
           and rates[k] >= 0 else v for k, v in DEFAULT_SETTINGS["rpm"].items()}
    goal = raw.get("goal")
    el_raw = raw.get("eligible") if isinstance(raw.get("eligible"), dict) else {}
    eligible = {}
    for acct in sorted(set(DEFAULT_SETTINGS["eligible"]) | set(el_raw)):
        got = el_raw.get(acct) if isinstance(el_raw.get(acct), dict) else {}
        eligible[acct] = {pl: got[pl] if isinstance(got.get(pl), bool) else False for pl in PLATS}
    rules = raw.get("reassign") if isinstance(raw.get("reassign"), list) else DEFAULT_SETTINGS["reassign"]
    # the day each program was ticked In it: actual pay counts only clips that went up from then on
    since_raw = raw.get("eligible_since") if isinstance(raw.get("eligible_since"), dict) else {}
    since = {a: {pl: d for pl, d in v.items() if pl in PLATS and isinstance(d, str) and _DAY.match(d)}
             for a, v in since_raw.items() if isinstance(v, dict)}
    typed_raw = raw.get("followers_typed") if isinstance(raw.get("followers_typed"), dict) else {}
    typed = {a: {pl: {"n": int(t["n"]), "at": str(t.get("at") or "")} for pl, t in v.items()
                 if pl in PLATS and isinstance(t, dict) and isinstance(t.get("n"), int)
                 and not isinstance(t.get("n"), bool) and t["n"] >= 0}
             for a, v in typed_raw.items() if isinstance(v, dict)}
    return {"rpm": rpm,
            "followers_typed": {a: v for a, v in typed.items() if v},
            "goal": float(goal) if isinstance(goal, (int, float)) and not isinstance(goal, bool) and goal >= 0
            else DEFAULT_SETTINGS["goal"],
            "eligible": eligible,
            "eligible_since": since,
            "reassign": [r for r in (_rule(x) for x in rules) if r],
            "count_by": raw["count_by"] if raw.get("count_by") in ("content", "channel") else DEFAULT_SETTINGS["count_by"]}


def content_account(p: dict, rules: List[dict]) -> str:
    """The account whose content a post is ('' = left out), by the first reassign rule that fits."""
    for r in rules:
        if r.get("account") and r["account"] != p.get("account"):
            continue
        if r.get("platform") and r["platform"] != p.get("platform"):
            continue
        day = str(p.get("day") or "")
        if r.get("before") and not day < r["before"]:
            continue
        if r.get("after") and not day >= r["after"]:
            continue
        if "ids" in r and str(p.get("pid") or "") not in r["ids"]:
            continue
        return "" if r["to"] == "exclude" else p["account"] if r["to"] == "keep" else r["to"]
    return str(p.get("account") or "")


def _num(v: object, top: float) -> float:
    if isinstance(v, bool):
        raise SystemExit("a payout rate and the goal must be numbers")
    try:
        x = float(v)    # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SystemExit("a payout rate and the goal must be numbers") from None
    if not 0 <= x <= top:
        raise SystemExit(f"{v} is out of range (0 to {top:,.0f})")
    return round(x, 4)


def save_settings(body: dict) -> Tuple[bool, str]:
    cur = settings()
    rpm = body.get("rpm", {})
    if not isinstance(rpm, dict):
        raise SystemExit("rpm must be an object")
    for k, v in rpm.items():
        if k not in cur["rpm"]:
            raise SystemExit(f"unknown payout rate {k!r}")
        cur["rpm"][k] = _num(v, 1000)
    if "goal" in body:
        cur["goal"] = _num(body["goal"], 10_000_000)
    if "count_by" in body:
        if body["count_by"] not in ("content", "channel"):
            raise SystemExit("count_by is content or channel")
        cur["count_by"] = body["count_by"]
    el = body.get("eligible", {})
    if not isinstance(el, dict):
        raise SystemExit("eligible must be an object")
    for acct, by in el.items():
        if not isinstance(by, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", str(acct)):
            raise SystemExit(f"unknown account {acct!r}")
        for pl, on in by.items():
            if pl not in PLATS or not isinstance(on, bool):
                raise SystemExit(f"eligible {acct} {pl}: say true or false")
            cur["eligible"].setdefault(acct, {x: False for x in PLATS})[pl] = on
            got = cur.setdefault("eligible_since", {}).setdefault(acct, {})
            if on:
                got.setdefault(pl, date.today().isoformat())     # the day it was ticked, kept on later saves
            else:
                got.pop(pl, None)
    typed = body.get("followers_typed", {})
    if not isinstance(typed, dict):
        raise SystemExit("followers_typed must be an object")
    for acct, by in typed.items():
        if not isinstance(by, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", str(acct)):
            raise SystemExit(f"unknown account {acct!r}")
        for pl, n in by.items():
            if pl not in PLATS:
                raise SystemExit(f"followers_typed {acct}: unknown platform {pl!r}")
            got = cur.setdefault("followers_typed", {}).setdefault(acct, {})
            if n is None:
                got.pop(pl, None)
                continue
            if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 1_000_000_000:
                raise SystemExit(f"followers_typed {acct} {pl}: a whole number of followers")
            if (got.get(pl) or {}).get("n") != n:            # the day it was typed, kept while it says the same
                got[pl] = {"n": n, "at": date.today().isoformat()}
    cur["followers_typed"] = {a: v for a, v in (cur.get("followers_typed") or {}).items() if v}
    p = _dir() / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, indent=1))
    os.replace(tmp, p)
    return True, "Saved."


# ------------------------------------------------------------------------------------------------------ Update now
_lock = threading.Lock()
_run: Dict[str, object] = {"running": False, "finished": 0.0, "error": "", "failed": []}


def _reads() -> Dict[str, str]:
    """'account__platform' -> when its numbers were last read (each read rewrites that account's pulled file)."""
    d = _dir() / "pulled"
    out: Dict[str, str] = {}
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            out[f.stem] = str(json.loads(f.read_text()).get("at") or "")
        except (OSError, ValueError, AttributeError):
            continue
    return out


def _last_read() -> float:
    times = [_when(t) for t in _reads().values()]
    return max((t.timestamp() for t in times if t), default=0.0)


def start_update(cfg: C.Config) -> Tuple[bool, str]:
    """Update now: read every connected account's numbers in the background. (it started or is running, words)"""
    with _lock:
        if _run["running"]:
            return True, "Already reading the numbers. They show up here when it finishes."
        now = time.time()
        if any(0 <= now - t < MIN_GAP for t in (float(_run["finished"]), _last_read())):   # type: ignore[arg-type]
            return True, "The numbers were read under two minutes ago."
        _run.update(running=True, error="", failed=[])
    threading.Thread(target=_update, args=(cfg,), name="stats-update", daemon=True).start()
    return True, "Reading the numbers from every connected account. They show up here in a minute or two."


_FAILED = re.compile(r"^\s*(.+?) (youtube_shorts|tiktok|instagram_reels|facebook_reels): FAILED to read numbers: (.*)$")


def _failures(cfg: C.Config, lines: List[str]) -> List[dict]:
    """stats.refresh's FAILED lines as {account, platform (names), key (account__platform), why}."""
    slug = {a.name: a.slug for a in cfg.accounts.values()}
    out = []
    for ln in lines:
        m = _FAILED.match(ln)
        if m:
            out.append({"account": m.group(1), "platform": O.NAMES.get(m.group(2), m.group(2)),
                        "key": f"{slug.get(m.group(1), m.group(1))}__{m.group(2)}", "why": m.group(3)[:200]})
        elif "another read" in ln:
            out.append({"account": "", "platform": "", "key": "", "why": ln.strip()})
    return out


def _update(cfg: C.Config) -> None:
    lines: List[str] = []
    err = ""
    try:
        lines = ST.update(cfg, wait=False)
    except Exception as e:  # noqa: BLE001  (a read that breaks never takes the review page with it)
        err = f"{type(e).__name__}: {e}"[:300]
    with _lock:
        _run.update(running=False, finished=time.time(), error=err, failed=_failures(cfg, lines))


# ------------------------------------------------------------------------------------------------------ the numbers
_cache: Dict[str, object] = {"key": None}


def _files(cfg: C.Config) -> tuple:
    files = [p for a in cfg.accounts.values() for p in B.all_boards(a)]
    pulled = _dir() / "pulled"
    files += sorted(pulled.glob("*.json")) if pulled.exists() else []
    out = []
    for f in files:
        try:
            st = f.stat()
            out.append((str(f), st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(out)


def _went_out(plat: str, rec: dict, s: dict, now: datetime) -> bool:
    if rec.get("privacy") == ST.PRIVATE:          # an 'Only me' TikTok test: nobody else saw it
        return False
    if plat == "youtube_shorts":
        at = _when(rec.get("publish_at") or s.get("publish_at"))
        return bool(rec.get("video_id") or rec.get("post_id")) and bool(at and at <= now)
    return bool(rec.get("post_id"))


def _factory(cfg: C.Config, now: datetime) -> List[dict]:
    """Per account and day up to today: slots, clips made, approved, declined, never signed in time, and per
    platform what went out and what gave up."""
    today = now.astimezone(cfg.tz()).date()
    rows = []
    for acct in cfg.accounts.values():
        for path in B.all_boards(acct):
            day = date.fromisoformat(path.stem)
            if day > today:
                continue
            try:
                bd = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            r = {"account": acct.slug, "day": day.isoformat(), "slots": 0, "made": 0, "approved": 0, "declined": 0,
                 "unsigned": 0, "posted": {}, "gave_up": {}, "private": {}}
            for s in (bd.get("slots") or {}).values():
                if B.empty_spare(s):
                    continue                # an empty spare slot (16 to 20) is fine: never a slot of the day here
                r["slots"] += 1
                st = s.get("status")
                r["made"] += 1 if s.get("clip") else 0
                if st in B.SIGNED:
                    r["approved"] += 1
                elif st == B.DECLINED:
                    r["declined"] += 1
                elif st == B.READY and s.get("clip"):
                    at = _when(s.get("publish_at"))
                    r["unsigned"] += 1 if at and now - at > P.LATE_LIMIT else 0
                for plat, rec in (s.get("outlets") or {}).items():
                    name = ST.DASH_PLATFORM.get(plat)
                    if not name or not isinstance(rec, dict):
                        continue
                    if _went_out(plat, rec, s, now):
                        r["posted"][name] = r["posted"].get(name, 0) + 1
                    elif rec.get("privacy") == ST.PRIVATE and rec.get("post_id"):
                        r["private"][name] = r["private"].get(name, 0) + 1
                    elif rec.get("gave_up"):
                        r["gave_up"][name] = r["gave_up"].get(name, 0) + 1
            rows.append(r)
    return rows


def _reasons(cfg: C.Config) -> dict:
    """Why clips came back (reasons.stat_rows); empty, never an error, when a board cannot be read."""
    from . import reasons as R
    try:
        return R.stat_rows(cfg)
    except Exception:  # noqa: BLE001  (one broken board never takes the tab down)
        return {"codes": [], "gates": [], "reasons": [], "holds": []}


def _posts(cfg: C.Config) -> List[dict]:
    tz = cfg.tz()
    out = []
    for p in ST.all_posts(cfg, days=3650):
        at = _when(p.get("at"))
        if not at:
            continue
        loc = at.astimezone(tz)
        q = {k: p.get(k) for k in FIELDS}
        q.update({k: p[k] for k in WATCH_FIELDS if p.get(k) is not None})     # only the ones it has
        q.update(ts=int(at.timestamp()), day=loc.date().isoformat(), dow=loc.weekday(), hour=loc.hour,
                 hm=loc.strftime("%H:%M"))
        out.append(q)
    return out


def _no_watch(cfg: C.Config) -> List[str]:
    """Accounts whose YouTube sign-in is from before YouTube Analytics was asked for: no watch time until connected again."""
    from . import direct as D
    out = []
    for a in cfg.accounts.values():
        f = O.TOKENS / a.slug / "youtube_token.json"
        try:
            scope = str(json.loads(f.read_text()).get("scope") or "")
        except (OSError, ValueError, AttributeError):
            continue                       # not signed in at all: the platform list says so already
        if D.YT_ANALYTICS not in scope:
            out.append(a.name)
    return out


def _followers() -> dict:
    from . import followers as FL
    return FL.load()


def data(cfg: C.Config, now: Optional[datetime] = None) -> dict:
    """Everything the Stats tab draws. Posts that are not out yet (a YouTube upload that goes public later) are left out.
    Each post carries `content`: the account whose content it is by the reassign rules ('' = left out)."""
    from . import accounts as AC
    cfg = AC.own_view(cfg)                  # a client's posts are on its own page, never in the owner's money
    now = now or cfg.now()
    key = (_files(cfg), int(now.timestamp() // 300))       # boards change, and so do the counts as the day goes on
    if _cache.get("key") != key:
        _cache.update(key=key, posts=_posts(cfg), factory=_factory(cfg, now), reasons=_reasons(cfg))
    cut = now.timestamp()
    sets = settings()
    posts = []
    for p in _cache["posts"]:                               # type: ignore[union-attr]
        if p["ts"] > cut:
            continue
        c = content_account(p, sets["reassign"])
        posts.append(dict(p, content=c if not c or c in cfg.accounts else p["account"]))
    try:
        hist = ST.history(cfg, now=now)
    except Exception:  # noqa: BLE001  (a broken history file never takes the tab down)
        hist = {"days": 0, "first": "", "span": 0.0, "base_at": "", "last_at": "", "gains": {}}
    try:          # this calendar month from the snapshots: views gained since its first read (stats.month)
        mon = ST.month(cfg, now=now)
        mon["followers"] = {a: {ST.DASH_PLATFORM.get(k, k): v for k, v in (by or {}).items()}
                            for a, by in mon["followers"].items()}
    except Exception:  # noqa: BLE001  (a broken history file never takes the tab down)
        mon = {"month": "", "first_day": "", "base_at": "", "last_at": "", "partial": False, "reads": 0,
               "gains": {}, "followers": {}}
    try:          # keyed by the page's platform names (youtube, not youtube_shorts)
        fol = {a: {ST.DASH_PLATFORM.get(k, k): v for k, v in (by or {}).items()} for a, by in _followers().items()
               if isinstance(by, dict)}
    except Exception:  # noqa: BLE001
        fol = {}
    reads = _reads()
    with _lock:
        run = dict(_run)
    done = float(run["finished"])                           # type: ignore[arg-type]
    newer = lambda key: bool(key) and (_when(reads.get(key, "")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp() > done
    failed = [f for f in run["failed"] if not newer(f["key"])]   # type: ignore[union-attr]  (read fine since)
    body = {
        "today": now.astimezone(cfg.tz()).date().isoformat(),
        "accounts": [{"id": a.slug, "name": a.name, "short": str(a.short or a.raw.get("short") or a.name),
                      "min": a.min_seconds, "max": a.max_seconds} for a in cfg.accounts.values()],
        "slots": sorted({s for a in cfg.accounts.values() for s in a.slots}),
        "spares": sorted({s for a in cfg.accounts.values() for s in a.spare_slots}),
        "by_hand": sorted({ST.DASH_PLATFORM[p] for a in cfg.accounts.values() for p in O.hand_platforms(a)
                           if p in ST.DASH_PLATFORM}),
        "posts": posts,
        "factory": _cache["factory"],
        "reasons": _cache.get("reasons") or {"codes": [], "gates": [], "reasons": [], "holds": []},
        "settings": sets,
        "campaign_fees": CAMPAIGN_FEES,
        "history": hist,
        "month": mon,
        "followers": fol,
        "read_at": max(reads.values(), default=""),
        "reading": bool(run["running"]),
        "error": run["error"],
        "failed": failed,
        "no_watch": _no_watch(cfg),
    }
    body["stamp"] = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    return body


# ------------------------------------------------------------------------------------------------------ the page
BODY = """
<div class="filters" role="toolbar" aria-label="What to count">
  <div class="seg" id="f-range" role="group" aria-label="Days"></div>
  <div class="seg" id="f-acct" role="group" aria-label="Account"></div>
  <div class="chips" id="f-plat" role="group" aria-label="Platforms"></div>
  <div class="countby"><span>Count by</span><div class="seg" id="f-count" role="group" aria-label="Count by"></div></div>
</div>
<div class="warnbox" id="note" hidden></div>
<section class="kpis" id="kpis" aria-label="Summary"></section>
<section class="panel">
  <div class="sec-head">
    <div><h2>Views by the day each clip went up</h2><p id="chart-note"></p></div>
    <div class="seg" id="f-split" role="group" aria-label="Lines by"></div>
  </div>
  <ul class="legend" id="legend"></ul>
  <div class="chart" id="chart"></div>
  <details class="as-table"><summary>These numbers as a table</summary><div class="tbl" id="chart-table"></div></details>
</section>
<div class="two">
  <section class="panel">
    <div class="sec-head">
      <div><h2>Top clips</h2><p id="top-note"></p></div>
      <div class="seg" id="f-rank" role="group" aria-label="Rank by" hidden></div>
      <div class="seg" id="f-group" role="group" aria-label="Count"></div>
    </div>
    <div class="tbl"><table class="leaders" id="top"></table></div>
    <div class="foot">
      <button type="button" id="top-more">Show more</button>
      <input type="search" id="top-q" placeholder="Search titles" aria-label="Search titles">
      <button type="button" id="csv">Download CSV</button>
    </div>
  </section>
  <section class="panel">
    <div class="sec-head"><div><h2>Your slots</h2><p>Views per post at each factory slot, by the slot the clip was made for.</p></div></div>
    <div class="bars" id="by-slot"></div>
  </section>
</div>
<div class="two">
  <section class="panel">
    <div class="sec-head"><div><h2>Best times to post</h2><p>Views per post by the day and hour it went up. Outlined: an hour with a factory slot.</p></div></div>
    <div class="heat" id="heat"></div>
    <div class="heat-key" id="heat-key"></div>
  </section>
  <section class="panel">
    <div class="sec-head"><div><h2>By platform</h2><p>Where the views come from.</p></div></div>
    <div class="bars" id="by-plat"></div>
  </section>
</div>
<section>
  <div class="sec-head"><div><h2>What works</h2><p>Views per post. The longer bar wins.</p></div></div>
  <div class="three">
    <div class="panel"><h3>Format</h3><div class="bars" id="by-fmt"></div></div>
    <div class="panel"><h3>Source</h3><div class="bars" id="by-host"></div></div>
    <div class="panel"><h3>Length</h3><div class="bars" id="by-len"></div></div>
  </div>
</section>
<section class="panel">
  <div class="sec-head">
    <div><h2>How long people watch</h2><p>The middle post of each group, counting the posts that have the number. Watched: the average share of the clip played; over 100% means people watched it again. Skipped: Instagram views gone in the first 3 seconds. Engaged: YouTube views that were not a swipe past.</p></div>
    <div class="seg" id="f-wby" role="group" aria-label="Group by"></div>
  </div>
  <div class="tbl"><table class="wtime" id="wtime"></table></div>
  <p class="cmp-s" id="wtime-miss" hidden></p>
</section>
<div class="two">
  <section class="panel">
    <div class="sec-head"><div><h2>The factory</h2><p>Slots and clips by the day they were for.</p></div></div>
    <div class="tbl"><table class="fac" id="fac"></table></div>
  </section>
  <section class="panel">
    <div class="sec-head"><div><h2>Money</h2><p>Actual counts only the programs an account is in. Every other dollar here is an estimate.</p></div></div>
    <div class="money" id="money"></div>
    <div class="goal" id="goal"></div>
    <p class="warnline" id="tt-note" hidden></p>
    <h3>What it takes</h3>
    <ul class="miles" id="miles"></ul>
    <h3>Payout per 1,000 views (estimates)</h3>
    <div class="rates" id="rates"></div>
  </section>
</div>
<section class="panel" id="why-sec">
  <div class="sec-head">
    <div><h2>Why clips came back</h2><p>Each Pull and Decline by its reason, in the week it was given. The days filter above does not apply here.</p></div>
    <div class="weeks"><button type="button" class="step" id="why-prev" aria-label="The week before" title="The week before">&#8249;</button><span id="why-week"></span><button type="button" class="step" id="why-next" aria-label="The week after" title="The week after">&#8250;</button></div>
  </div>
  <div class="why-two">
    <div class="tbl"><table class="fac why-t" id="why-codes"></table></div>
    <div class="tbl"><table class="fac why-t" id="why-gates"></table></div>
  </div>
  <p class="cmp-s" id="why-note"></p>
</section>
<section class="panel">
  <div class="sec-head"><div><h2>Paid programs</h2><p>How close each account is to being paid. Platforms count by channel. Tick In it once an account is in a program.</p></div></div>
  <div class="prog" id="prog"></div>
  <h3 class="cmp-h">Accounts side by side</h3>
  <div class="tbl"><table class="cmp" id="cmp"></table></div>
  <p class="cmp-s" id="cmp-note"></p>
</section>
"""

CSS = """
:root{--p-yt:#eb6834;--p-tt:#1baf7a;--p-ig:#eda100;--p-fb:#2a78d6;--a-1:#008300;--a-2:#6250d6;--a-3:#c2357f;--a-4:#56606e;
--grid:#eceef1;--axis:#c3c9d2;
--h1:#cde2fb;--h2:#9ec5f4;--h3:#6da7ec;--h4:#3987e5;--h5:#256abf;--h6:#184f95;--h7:#0d366b}
@media (prefers-color-scheme:dark){:root[data-theme=dark]{--p-yt:#d95926;--p-tt:#199e70;--p-ig:#c98500;--p-fb:#3987e5;--a-1:#008300;--a-2:#9085e9;
--a-3:#e46aa8;--a-4:#a3adba;
--grid:#232a33;--axis:#3a4350;--h1:#13305a;--h2:#184f95;--h3:#256abf;--h4:#3987e5;--h5:#6da7ec;--h6:#9ec5f4;--h7:#cde2fb}}
body.stats main{display:grid;gap:16px}
body.stats section{margin:0;min-width:0}
.fresh{color:var(--sub);font-size:13px}
.filters{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
.seg{display:inline-flex;flex-wrap:wrap;gap:2px;padding:2px;border:1px solid var(--line);border-radius:6px;background:var(--card)}
.seg button{height:28px;border:0;border-radius:4px;background:none;padding:0 10px;color:var(--sub);font-weight:550}
.seg button:hover{color:var(--ink)}
.seg button[aria-pressed=true]{background:var(--mute);color:var(--ink);box-shadow:inset 0 -2px 0 var(--ink)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:inline-flex;align-items:center;gap:7px}
.chip i{width:10px;height:10px;border-radius:2px;flex:none}
.chip[aria-pressed=false]{color:var(--sub)}
.chip[aria-pressed=false] i{background:none!important;box-shadow:inset 0 0 0 1.5px var(--axis)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;min-width:0}
.sec-head{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:flex-start;justify-content:space-between;margin-bottom:12px}
.sec-head h2{font-size:16px}
.sec-head p{margin:3px 0 0;color:var(--sub);font-size:12.5px}
h3{font-size:13px;font-weight:650;margin:0 0 12px}
.two{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(0,1fr);gap:16px}
.three{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
@media (max-width:980px){.two,.three{grid-template-columns:minmax(0,1fr)}}
.kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.kpi{background:var(--card);padding:14px 16px;min-width:0}
.kpi .k{color:var(--sub);font-size:12.5px}
.kpi .v{font-size:22px;font-weight:650;line-height:1.2;margin-top:6px;overflow-wrap:anywhere}
.kpi.focal .v{font-size:40px;line-height:1;letter-spacing:-.01em}
.kpi .d{color:var(--sub);font-size:12px;margin-top:6px}
.up{color:var(--ok)}.down{color:var(--bad)}
@media (max-width:1180px){.kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:600px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.kpi.focal .v{font-size:32px}}
.legend{display:flex;flex-wrap:wrap;gap:4px 18px;list-style:none;margin:0 0 10px;padding:0;font-size:12.5px;color:var(--sub)}
.legend li{display:inline-flex;align-items:center;gap:6px}
.legend i{width:14px;height:2px;border-radius:1px;flex:none}
.legend b{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}
.chart{position:relative;min-height:200px;overflow:hidden}
.chart svg{display:block;touch-action:pan-y}
.chart svg:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
.empty{color:var(--sub);font-size:13px;padding:24px 0;text-align:center}
.as-table{margin-top:10px}
.tbl{overflow-x:auto}
.tbl table{width:100%;border-collapse:collapse;font-size:12.5px}
.tbl th{text-align:left;font-weight:600;color:var(--sub);padding:6px;border-bottom:1px solid var(--line);white-space:nowrap}
.tbl td{padding:6px;border-bottom:1px solid var(--line)}
.tbl tr:last-child td{border-bottom:0}
.tbl .r{text-align:right;font-variant-numeric:tabular-nums}
.tip{position:fixed;z-index:8;pointer-events:none;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:6px;box-shadow:0 6px 20px rgba(0,0,0,.14);padding:8px 10px;font-size:12px;min-width:150px;max-width:280px}
.tip .t{color:var(--sub);margin-bottom:4px}
.tip .r{display:flex;justify-content:space-between;align-items:center;gap:14px}
.tip .r span{display:inline-flex;align-items:center;gap:6px;color:var(--sub)}
.tip .r i{width:10px;height:2px;flex:none}
.tip .r b{font-weight:650;font-variant-numeric:tabular-nums}
.tip .tot{border-top:1px solid var(--line);margin-top:4px;padding-top:4px}
table.leaders{font-size:13px}
.leaders td{padding:9px 6px;vertical-align:top}
.leaders .n{width:26px;color:var(--sub);font-variant-numeric:tabular-nums}
.leaders .v{text-align:right;font-weight:650;font-size:15px;font-variant-numeric:tabular-nums;white-space:nowrap}
.leaders .ttl{font-weight:550;color:var(--ink);text-decoration:none;overflow-wrap:anywhere}
.leaders a.ttl:hover{color:var(--blue);text-decoration:underline}
.leaders .m{color:var(--sub);font-size:12px;margin-top:2px}
.badges{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.pb{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--sub);background:var(--mute);border-radius:4px;padding:1px 6px;font-variant-numeric:tabular-nums;white-space:nowrap}
.pb i{width:7px;height:7px;border-radius:2px;flex:none}
.foot{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:12px}
.foot input[type=search]{flex:1 1 160px;min-width:140px}
input[type=search],input[type=number]{font:inherit;font-size:13px;height:32px;padding:0 9px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--ink);min-width:0}
input:focus{outline:none;border-color:var(--blue)}
.bars{display:grid;gap:12px}
.bar{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 12px;align-items:baseline;font-size:13px}
.bar .l{display:flex;align-items:center;gap:7px;min-width:0;font-weight:550}
.bar .l span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar .l i{width:10px;height:10px;border-radius:2px;flex:none}
.bar .x{font-weight:650;font-variant-numeric:tabular-nums}
.bar .t{grid-column:1/-1;height:8px}
.bar .f{height:100%;border-radius:0 4px 4px 0;min-width:2px;background:var(--blue)}
.bar .s{grid-column:1/-1;color:var(--sub);font-size:12px}
.heat{display:grid;grid-template-columns:30px repeat(24,minmax(0,1fr));gap:2px}
.heat .hh{font-size:10.5px;color:var(--sub);text-align:center;white-space:nowrap;padding-bottom:3px}
.heat .hl{font-size:11px;color:var(--sub);display:flex;align-items:center}
.heat .c{aspect-ratio:1;border-radius:2px;background:var(--mute)}
.heat .c.fs{box-shadow:inset 0 0 0 1.5px var(--axis)}
.heat .c.on:hover{outline:2px solid var(--ink);outline-offset:-1px}
.heat-key{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;margin-top:12px;font-size:12px;color:var(--sub)}
.heat-key .ramp{display:inline-flex;gap:2px}
.heat-key .ramp i{width:16px;height:10px;border-radius:2px}
.fac td,.fac th{text-align:right;font-variant-numeric:tabular-nums}
.fac td:first-child,.fac th:first-child{text-align:left;font-weight:500;color:var(--sub)}
.fac td.both{font-weight:650}
.fac tr.gap td{padding-top:12px}
.money{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px 16px}
.money .k{color:var(--sub);font-size:12.5px}
.money .v{font-size:22px;font-weight:650;margin-top:4px}
.money .s{color:var(--sub);font-size:12px;margin-top:2px}
.goal{margin:18px 0 14px;display:grid;gap:8px}
.goal label,.rates label{display:grid;gap:4px;color:var(--sub);font-size:12px}
.goal .row{display:flex;flex-wrap:wrap;align-items:flex-end;gap:10px 16px}
.goal .row label{width:140px}
.goal .grow{flex:1;min-width:180px}
.progress{height:6px;background:var(--mute);border-radius:3px;overflow:hidden;margin-top:6px}
.progress i{display:block;height:100%;background:var(--blue)}
.goal p{margin:0;font-size:13px;line-height:1.55}
.warnline{color:var(--warn);font-size:12.5px;margin:0 0 14px}
.rates{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px}
.rates input{width:100%}
.warnbox{font-size:12.5px;color:var(--warn);background:var(--warnbg);padding:8px 10px;border-radius:6px}
.warnbox details{display:inline}.warnbox summary{display:inline;color:inherit;text-decoration:underline;font-size:12.5px}
.warnbox ul{margin:6px 0 0;padding-left:18px;color:var(--ink)}
.seg[hidden]{display:none}
.weeks{display:flex;align-items:center;gap:8px;font-size:13px;font-variant-numeric:tabular-nums}
.weeks .step{height:28px;min-width:28px;padding:0 8px}
.why-two{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px 32px;align-items:start}
.why-t td.z{color:var(--sub);font-weight:400}
.why-t .hard{color:var(--bad);font-size:11px;font-weight:600;margin-left:6px}
.why-t td.lines{font-size:11.5px;color:var(--sub);text-align:left;font-variant-numeric:normal;max-width:240px}
@media (max-width:860px){.why-two{grid-template-columns:minmax(0,1fr)}}
.countby{display:inline-flex;align-items:center;gap:8px;color:var(--sub);font-size:12.5px}
.money .v.est{color:var(--sub)}
.miles{list-style:none;margin:0 0 16px;padding:0;display:grid;gap:8px;font-size:12.5px;line-height:1.5}
.miles li{display:block}
.miles b{display:inline;font-size:12.5px;color:var(--ink);font-weight:650;text-transform:none;letter-spacing:0;margin:0 4px 0 0}
.prog{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 32px}
.prog h3{margin:0 0 4px}
.gate{padding:12px 0;border-top:1px solid var(--line)}
.gh{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;font-size:13px}
.gh .nm{font-weight:650}
.gh .pg{color:var(--sub)}
.gh .in{margin-left:auto;display:inline-flex;align-items:center;gap:6px;color:var(--sub);font-size:12px;cursor:pointer}
.gh .in input{width:15px;height:15px;margin:0}
.meter{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 12px;margin-top:8px;font-size:12.5px}
.meter .x{font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}
.meter .t{grid-column:1/-1;height:6px;background:var(--mute);border-radius:3px;overflow:hidden}
.meter .t i{display:block;height:100%;background:var(--blue)}
.meter .t i.done{background:var(--ok)}
.gate .s{margin:6px 0 0;color:var(--sub);font-size:12px;line-height:1.5}
.gate .w{margin:6px 0 0;color:var(--warn);font-size:12px;line-height:1.5}
.gate .typed{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;margin-top:8px;color:var(--sub);font-size:12px}
.gate .typed input{width:120px}
.cmp-h{margin-top:20px}
.cmp td,.cmp th{text-align:right;font-variant-numeric:tabular-nums}
.cmp td:first-child,.cmp th:first-child{text-align:left}
.cmp td.est{color:var(--sub)}
.cmp tr.both td{font-weight:650}
.cmp-s{margin:8px 0 0;color:var(--sub);font-size:12px}
.wtime td,.wtime th{text-align:right;font-variant-numeric:tabular-nums}
.wtime td:first-child,.wtime th:first-child,.wtime td:nth-child(2),.wtime th:nth-child(2){text-align:left}
.wtime td:first-child{font-weight:600}
.wtime tr.wt-gap td{border-top:1px solid var(--line)}
.wtime td.na{color:var(--sub)}
@media (max-width:980px){.prog{grid-template-columns:minmax(0,1fr)}}
@media (max-width:600px){.panel{padding:14px}.money{grid-template-columns:minmax(0,1fr)}}
.gr-acts{margin-top:8px}
.gr-open{height:30px;padding:0 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);font:inherit;font-size:12.5px;font-weight:550;cursor:pointer}
.gr-open:hover{border-color:var(--sub)}
.gr-open[aria-expanded=true]{background:var(--mute)}
.leaders tr.gr-row>td{padding:0 6px 14px}
.gr{display:grid;gap:10px;border:1px solid var(--line);border-radius:8px;padding:12px;background:var(--card);min-width:0}
.gr-seg{justify-self:start}
.gr-body{display:grid;gap:10px;min-width:0}
.gr-chart{position:relative;min-height:150px;overflow:hidden}
.gr-chart svg{display:block}
.gr-note{margin:0;color:var(--sub);font-size:12.5px;line-height:1.5}
.gr-note b{color:var(--ink);font-weight:650}
.gr h4{margin:4px 0 0;font-size:12.5px;font-weight:650}
.legend i.gr-u{background:var(--axis)}
.gr .tbl td,.gr .tbl th{white-space:nowrap}
@media (max-width:600px){#f-rank button,.gr-seg button,.gr-open{height:40px;min-width:40px}.gr{padding:10px}}
"""

JS = r"""
(() => {
'use strict';
const TOKEN = __TOKEN__;
const PAGE_ACCT = __ACCT__;          /* the header's account switch ('' when the page has none) */
const SERIES = __SERIES__;           /* checkpoints.py is built: how each post grew, and the extra numbers */
const OUT = {youtube: 'youtube_shorts', instagram: 'instagram_reels', facebook: 'facebook_reels'};
const PLAT = [
  {id: 'youtube', name: 'YouTube', c: 'var(--p-yt)'},
  {id: 'tiktok', name: 'TikTok', c: 'var(--p-tt)'},
  {id: 'instagram', name: 'Instagram', c: 'var(--p-ig)'},
  {id: 'facebook', name: 'Facebook', c: 'var(--p-fb)'},
];
const PN = Object.fromEntries(PLAT.map(p => [p.id, p]));
const ACOL = ['var(--a-1)', 'var(--a-2)', 'var(--a-3)', 'var(--a-4)'];   // no platform's colour: a line is an account
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const RANGES = [{id: 7, l: '7 days'}, {id: 30, l: '30 days'}, {id: 90, l: '90 days'}, {id: 0, l: 'All'}];
const KEY = 'clip-review-stats';
const store = (() => { try { return window.localStorage; } catch (e) { return null; } })();
const ui = Object.assign({range: 30, acct: 'all', plats: PLAT.map(p => p.id), split: 'platform', group: 'clip', count: null, wby: 'account'},
  (() => { try { const v = store && store.getItem(KEY); return v ? JSON.parse(v) : {}; } catch (e) { return {}; } })(),
  {topN: 10, q: ''});
if (!RANGES.some(r => r.id === ui.range)) ui.range = 30;
ui.plats = PLAT.map(p => p.id).filter(id => Array.isArray(ui.plats) && ui.plats.includes(id));
if (!ui.plats.length) ui.plats = PLAT.map(p => p.id);
if (!['platform', 'account', 'total'].includes(ui.split)) ui.split = 'platform';
if (!['clip', 'post'].includes(ui.group)) ui.group = 'clip';
if (!['account', 'format'].includes(ui.wby)) ui.wby = 'account';
if (!['content', 'channel'].includes(ui.count)) ui.count = null;   /* null: settings.json count_by decides */
const countBy = () => ui.count || (D && D.settings.count_by) || 'channel';
function saveUi() { try { store && store.setItem(KEY, JSON.stringify({range: ui.range, acct: ui.acct, plats: ui.plats, split: ui.split, group: ui.group, count: ui.count, wby: ui.wby})); } catch (e) {} }

let D = null, AN = {}, P = [], autoAsked = false, pollT = 0, toastT = 0, setT = 0, last = null;
let XT = null;                       /* /stats/series?view=extras: {on: [accounts with checkpoints on], posts} */
const SC = new Map(), OPEN = new Map();   /* series answers by url; the Top clips rows opened (key -> platform) */
ui.rank = 'views';
/* the posts as the Count by switch counts them: by content (the reassign rules in settings.json move a post to the
   account whose content it is, or leave it out) or by channel (where each platform counts it). chan = its channel. */
function viewPosts() {
  return D.posts.map(p => ({...p, chan: p.account, account: countBy() === 'channel' ? p.account : p.content})).filter(p => p.account);
}

/* ------------------------------------------------------------------ helpers */
const $ = s => document.querySelector(s);
function el(tag, attrs, kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'style') n.style.cssText = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const c of [].concat(kids == null ? [] : kids)) if (c != null) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}
function sv(tag, attrs) { const n = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); return n; }
function fmtN(n) {
  if (n == null || isNaN(n)) return '0';
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 0 : 1).replace(/\.0$/, '') + 'B';
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 1 : 2).replace(/\.?0+$/, '') + 'M';
  if (a >= 1e4) return (n / 1e3).toFixed(a >= 1e5 ? 0 : 1).replace(/\.0$/, '') + 'K';
  return Math.round(n).toLocaleString('en-US');
}
const money = n => n >= 1e4 ? '$' + fmtN(n) : '$' + (n < 100 ? n.toFixed(2) : Math.round(n).toLocaleString('en-US'));
const pct = x => (x * 100).toFixed(x < 0.1 ? 1 : 0) + '%';
const sum = (a, f) => a.reduce((s, x) => s + (f(x) || 0), 0);
const median = a => { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const engaged = p => (p.likes || 0) + (p.comments || 0) + (p.shares || 0);
const norm = t => String(t || '').toLowerCase().replace(/#\S+/g, '').replace(/[^a-z0-9]+/g, ' ').trim().slice(0, 60);
/* whole days on 'YYYY-MM-DD' (counted in UTC, so a clock change never moves a day) */
const dnum = s => Math.round(Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) / 864e5);
const dstr = n => new Date(n * 864e5).toISOString().slice(0, 10);
const wday = n => (((n + 3) % 7) + 7) % 7;          /* 0 = Monday: day 0 (1970-01-01) was a Thursday */
const fday = s => MON[+s.slice(5, 7) - 1] + ' ' + (+s.slice(8, 10));
const fhour = h => (h % 12 || 12) + (h < 12 ? 'am' : 'pm');
const cap = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
function rate(p) { const r = D.settings.rpm; return (p.platform === 'tiktok' ? (p.dur > 60 ? r.tiktok_long : r.tiktok) : r[p.platform]) || 0; }
const earn = p => (p.views || 0) / 1000 * rate(p);
/* pay only counts where the channel is in that platform's program (settings.json eligible) */
const paid = p => {
  const a = p.chan || p.account, on = !!(((D.settings.eligible || {})[a] || {})[p.platform]);
  const since = (((D.settings.eligible_since || {})[a] || {})[p.platform]) || '';
  return on && (!since || String(p.day || '') >= since);   /* only clips that went up once it was In it */
};
const actual = p => paid(p) ? earn(p) : 0;
function toast(msg, bad) {
  const t = $('#toast'); t.textContent = msg; t.className = bad ? 'bad' : ''; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, bad ? 15000 : 6000);
}
$('#toast').addEventListener('click', () => { $('#toast').hidden = true; });
const tipEl = $('#tip');
function tipAt(x, y, kids) {
  tipEl.replaceChildren(...kids); tipEl.hidden = false;
  const w = tipEl.offsetWidth, h = tipEl.offsetHeight, vw = innerWidth, vh = innerHeight;
  let left = x + 14; if (left + w > vw - 8) left = x - 14 - w; left = Math.max(8, left);
  let top = y + 14; if (top + h > vh - 8) top = y - 14 - h; top = Math.max(8, top);
  tipEl.style.left = left + 'px'; tipEl.style.top = top + 'px';
}
const tipOff = () => { tipEl.hidden = true; };
const tipRow = (c, name, val) => el('div', {class: 'r'}, [el('span', {}, [c ? el('i', {style: 'background:' + c}) : null, name]), el('b', {}, val)]);

/* ------------------------------------------------------------------ the slice the filters pick */
function bounds() {
  const end = dnum(D.today);
  if (!ui.range) return {start: null, end, pstart: null};
  const start = end - ui.range + 1;
  return {start, end, pstart: start - ui.range};
}
const acctOk = a => ui.acct === 'all' || a === ui.acct;
function slice() {
  const b = bounds(), cur = [], prev = [];
  for (const p of P) {
    if (!acctOk(p.account) || !ui.plats.includes(p.platform)) continue;
    const n = dnum(p.day);
    if (n > b.end) continue;
    if (b.start == null || n >= b.start) cur.push(p); else if (n >= b.pstart) prev.push(p);
  }
  return {cur, prev: b.start == null ? null : prev, b};
}

/* ------------------------------------------------------------------ filters */
function seg(root, opts, cur, pick) {
  root.replaceChildren(...opts.map(o => el('button', {type: 'button', 'aria-pressed': String(o.id === cur), onclick: () => pick(o.id)}, o.l)));
}
function commit() { saveUi(); render(); }
function renderFilters() {
  seg($('#f-range'), RANGES, ui.range, v => { ui.range = v; commit(); });
  if (PAGE_ACCT) $('#f-acct').hidden = true;      /* the header's switch picks the account */
  else seg($('#f-acct'), [{id: 'all', l: 'All accounts'}, ...D.accounts.map(a => ({id: a.id, l: a.name}))], ui.acct, v => { ui.acct = v; commit(); });
  $('#f-plat').replaceChildren(...PLAT.map(p => el('button', {
    type: 'button', class: 'chip', 'aria-pressed': String(ui.plats.includes(p.id)),
    onclick: () => {
      const on = ui.plats.includes(p.id);
      if (on && ui.plats.length === 1) return;
      ui.plats = PLAT.map(q => q.id).filter(id => id === p.id ? !on : ui.plats.includes(id));
      commit();
    }}, [el('i', {style: 'background:' + p.c}), p.name])));
  seg($('#f-split'), [{id: 'platform', l: 'Platform'}, {id: 'account', l: 'Account'}, {id: 'total', l: 'Total'}], ui.split, v => { ui.split = v; commit(); });
  seg($('#f-group'), [{id: 'clip', l: 'Per clip'}, {id: 'post', l: 'Per post'}], ui.group, v => { ui.group = v; ui.topN = 10; commit(); });
  seg($('#f-count'), [{id: 'content', l: 'Content'}, {id: 'channel', l: 'Channel'}], countBy(), v => { ui.count = v; commit(); });
  seg($('#f-wby'), [{id: 'account', l: 'Account'}, {id: 'format', l: 'Format'}], ui.wby, v => { ui.wby = v; commit(); });
  $('#f-count').title = countBy() === 'content' ? 'Each clip counts for the account whose content it is (settings.json reassign).' : 'Each clip counts for the channel it is on, as the platforms count it.';
}

/* ------------------------------------------------------------------ summary */
function renderKpis(cur, prev) {
  const v = sum(cur, p => p.views), pv = prev ? sum(prev, p => p.views) : null;
  const e = sum(cur, earn), pe = prev ? sum(prev, earn) : null;
  const avg = cur.length ? v / cur.length : 0, pavg = prev && prev.length ? pv / prev.length : null;
  const delta = (a, b) => {
    if (!ui.range) return el('div', {class: 'd'}, 'all time');
    if (!b) return el('div', {class: 'd'}, 'nothing in the ' + ui.range + ' days before');
    const ch = (a - b) / b;
    return el('div', {class: 'd'}, [el('span', {class: ch >= 0 ? 'up' : 'down'}, (ch >= 0 ? '▲ ' : '▼ ') + pct(Math.abs(ch))), ' vs the ' + ui.range + ' days before']);
  };
  const tiles = [
    ['Views', fmtN(v), delta(v, pv), true],
    ['Posts', cur.length.toLocaleString('en-US'), delta(cur.length, prev ? prev.length : null)],
    ['Views per post', fmtN(avg), delta(avg, pavg)],
    ['Typical post', fmtN(median(cur.map(p => p.views || 0))), el('div', {class: 'd'}, 'the middle post')],
    ['Engagement', pct(v ? sum(cur, engaged) / v : 0), el('div', {class: 'd'}, 'likes, comments, shares per view')],
    ['If monetized (est.)', money(e), delta(e, pe)],
  ];
  $('#kpis').replaceChildren(...tiles.map(([k, val, d, focal]) => el('div', {class: 'kpi' + (focal ? ' focal' : '')}, [el('div', {class: 'k'}, k), el('div', {class: 'v'}, val), d])));
}

/* ------------------------------------------------------------------ views over time */
function lines() {
  if (ui.split === 'platform') return PLAT.filter(p => ui.plats.includes(p.id)).map(p => ({name: p.name, c: p.c, t: x => x.platform === p.id}));
  if (ui.split === 'account') return D.accounts.map((a, i) => ({id: a.id, name: a.name, c: ACOL[i] || 'var(--sub)', t: x => x.account === a.id})).filter(s => acctOk(s.id));
  return [{name: 'Views', c: 'var(--blue)', t: () => true}];
}
function niceStep(max) { const raw = max / 4, p = 10 ** Math.floor(Math.log10(raw)), q = raw / p; return (q <= 1 ? 1 : q <= 2 ? 2 : q <= 2.5 ? 2.5 : q <= 5 ? 5 : 10) * p; }
function renderChart(cur, b) {
  const box = $('#chart');
  let first = b.start;
  if (first == null) first = cur.length ? Math.min(...cur.map(p => dnum(p.day))) : b.end - 29;
  const weekly = b.end - first + 1 > 100;
  const bucket = n => weekly ? n - wday(n) : n;
  const days = [];
  for (let n = bucket(first); n <= b.end; n += weekly ? 7 : 1) days.push(n);
  const at = new Map(days.map((n, i) => [n, i]));
  const S = lines().map(s => ({...s, v: new Array(days.length).fill(0)}));
  for (const p of cur) { const i = at.get(bucket(dnum(p.day))); if (i == null) continue; for (const s of S) if (s.t(p)) s.v[i] += p.views || 0; }
  $('#chart-note').textContent = (weekly ? 'Each point is a week. ' : '') + 'A clip counts all its views on the day it went up.';
  $('#legend').replaceChildren(...(S.length > 1 ? S.map(s => el('li', {}, [el('i', {style: 'background:' + s.c}), s.name, el('b', {}, fmtN(sum(s.v, x => x)))])) : []));
  const head = n => weekly ? 'Week of ' + fday(dstr(n)) : DOW[wday(n)] + ' ' + fday(dstr(n));
  /* the table twin */
  const rows = days.map((n, i) => ({n, vals: S.map(s => s.v[i])})).filter(r => r.vals.some(v => v)).reverse();
  $('#chart-table').replaceChildren(rows.length ? el('table', {}, [
    el('thead', {}, el('tr', {}, [el('th', {}, weekly ? 'Week of' : 'Day'), ...S.map(s => el('th', {class: 'r'}, s.name)), ...(S.length > 1 ? [el('th', {class: 'r'}, 'Total')] : [])])),
    el('tbody', {}, rows.map(r => el('tr', {}, [el('td', {}, head(r.n)), ...r.vals.map(v => el('td', {class: 'r'}, v.toLocaleString('en-US'))), ...(S.length > 1 ? [el('td', {class: 'r'}, sum(r.vals, x => x).toLocaleString('en-US'))] : [])]))),
  ]) : el('p', {class: 'empty'}, 'No views in this range.'));
  if (!cur.length) { box.replaceChildren(el('div', {class: 'empty'}, 'No posts in this range yet.')); return; }
  /* drawn at the box's real width: text stays 12px at every size */
  const W = Math.max(260, Math.floor(box.clientWidth)), H = W < 560 ? 200 : 260;
  const m = {t: 10, r: 12, b: 24, l: 44};
  const max = Math.max(1, ...S.flatMap(s => s.v));
  const step = niceStep(max), top = Math.ceil(max / step) * step;
  const x = i => m.l + (days.length === 1 ? (W - m.l - m.r) / 2 : i * (W - m.l - m.r) / (days.length - 1));
  const y = v => m.t + (H - m.t - m.b) * (1 - v / top);
  const svg = sv('svg', {width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img', tabindex: 0, 'aria-label': 'Views by the day each clip went up. Arrow keys move along the days.'});
  for (let v = 0; v <= top + 1e-9; v += step) {
    svg.append(sv('line', {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v ? 'var(--grid)' : 'var(--axis)', 'stroke-width': 1}));
    const t = sv('text', {x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 12, fill: 'var(--sub)'}); t.textContent = fmtN(v); svg.append(t);
  }
  const nLab = Math.max(2, Math.min(7, Math.floor((W - m.l - m.r) / 90))), every = Math.max(1, Math.ceil(days.length / nLab));
  days.forEach((n, i) => {
    const lastI = days.length - 1;
    if ((i % every && i !== lastI) || (i !== lastI && lastI - i < every * 0.6)) return;
    const t = sv('text', {x: x(i), y: H - 6, 'text-anchor': days.length === 1 ? 'middle' : i === 0 ? 'start' : i === lastI ? 'end' : 'middle', 'font-size': 12, fill: 'var(--sub)'});
    t.textContent = fday(dstr(n)); svg.append(t);
  });
  for (const s of S) {
    const d = s.v.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ');
    if (S.length === 1) svg.append(sv('path', {d: d + ` L${x(s.v.length - 1)} ${y(0)} L${x(0)} ${y(0)} Z`, fill: s.c, 'fill-opacity': 0.08, stroke: 'none'}));
    svg.append(sv('path', {d, fill: 'none', stroke: s.c, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
  }
  const cross = sv('line', {y1: m.t, y2: H - m.b, stroke: 'var(--axis)', 'stroke-width': 1, visibility: 'hidden'});
  const dots = S.map(s => sv('circle', {r: 4, fill: s.c, stroke: 'var(--card)', 'stroke-width': 2, visibility: 'hidden'}));
  const hit = sv('rect', {x: m.l, y: 0, width: W - m.l - m.r, height: H - m.b, fill: 'transparent'});
  svg.append(cross, ...dots, hit);
  let cur_i = -1;
  const show = (i, cx, cy) => {
    cur_i = i;
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    S.forEach((s, k) => { dots[k].setAttribute('cx', x(i)); dots[k].setAttribute('cy', y(s.v[i])); dots[k].setAttribute('visibility', 'visible'); });
    const kids = [el('div', {class: 't'}, head(days[i])), ...S.map(s => tipRow(s.c, s.name, s.v[i].toLocaleString('en-US')))];
    if (S.length > 1) kids.push(el('div', {class: 'r tot'}, [el('span', {}, 'Total'), el('b', {}, sum(S, s => s.v[i]).toLocaleString('en-US'))]));
    tipAt(cx, cy, kids);
  };
  const hide = () => { cross.setAttribute('visibility', 'hidden'); dots.forEach(d => d.setAttribute('visibility', 'hidden')); tipOff(); };
  const near = ev => { const r = svg.getBoundingClientRect(); const px = ev.clientX - r.left; return Math.max(0, Math.min(days.length - 1, Math.round((px - m.l) / ((W - m.l - m.r) / Math.max(1, days.length - 1))))); };
  hit.addEventListener('pointermove', ev => show(near(ev), ev.clientX, ev.clientY));
  hit.addEventListener('pointerdown', ev => show(near(ev), ev.clientX, ev.clientY));
  hit.addEventListener('pointerleave', hide);
  svg.addEventListener('blur', hide);
  svg.addEventListener('keydown', ev => {
    if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
    ev.preventDefault();
    const i = cur_i < 0 ? days.length - 1 : Math.max(0, Math.min(days.length - 1, cur_i + (ev.key === 'ArrowRight' ? 1 : -1)));
    const r = svg.getBoundingClientRect(); show(i, r.left + x(i), r.top + m.t + 20);
  });
  box.replaceChildren(svg);
}

/* ------------------------------------------------------------------ top clips */
const badge = (pid, txt) => el('span', {class: 'pb'}, [el('i', {style: 'background:' + (PN[pid] ? PN[pid].c : 'var(--sub)')}), (PN[pid] ? PN[pid].name : pid) + (txt ? ' ' + txt : '')]);
const when = p => fday(p.day) + ', ' + p.hm;
function topRows(cur) {
  const q = ui.q.trim().toLowerCase();
  const list = q ? cur.filter(p => String(p.title || '').toLowerCase().includes(q)) : cur;
  if (ui.group === 'post') return list.map(p => ({...p, posts: [p]})).sort((a, b) => (b.views || 0) - (a.views || 0));
  const g = new Map();
  for (const p of list) {
    const k = p.account + '|' + (p.clip || norm(p.title) + '|' + p.day);
    let c = g.get(k);
    if (!c) g.set(k, c = {title: p.title, account: p.account, day: p.day, hm: p.hm, ts: p.ts, fmt: p.fmt, host: p.host, dur: p.dur, posts: [], views: 0});
    c.posts.push(p); c.views += p.views || 0;
    if (p.ts < c.ts) Object.assign(c, {ts: p.ts, day: p.day, hm: p.hm});
    if (!c.title) c.title = p.title;
  }
  return [...g.values()].sort((a, b) => b.views - a.views);
}
const RANKS = [
  {id: 'views', l: 'Views'},
  {id: 'avg_watch_s', l: 'Avg watch', f: v => v.toFixed(1) + 's'},
  {id: 'skip_rate', l: 'Skip rate', f: v => v.toFixed(0) + '%', low: true},
  {id: 'reach', l: 'Reach', f: v => fmtN(v), add: true},
  {id: 'saves', l: 'Saves', f: v => fmtN(v), add: true},
];
const xOf = p => XT && XT.posts ? XT.posts[p.platform + '|' + p.pid] || null : null;
/* a clip's number across its platforms: counts (reach, saves) are added up, rates (avg watch, skip rate) averaged */
function rankVal(c, k) {
  const v = c.posts.map(p => (xOf(p) || {})[k]).filter(x => typeof x === 'number');
  if (!v.length) return null;
  const t = sum(v, x => x), rk = RANKS.find(r => r.id === k) || {};
  return rk.add ? t : t / v.length;
}
function renderRank() {
  const box = $('#f-rank');
  const have = RANKS.filter(r => r.id === 'views' || (XT && Object.values(XT.posts || {}).some(x => typeof x[r.id] === 'number')));
  if (!SERIES || have.length < 2) { box.hidden = true; ui.rank = 'views'; return; }
  if (!have.some(r => r.id === ui.rank)) ui.rank = 'views';
  box.hidden = false;
  seg(box, have, ui.rank, v => { ui.rank = v; ui.topN = 10; renderRank(); renderTop(last.cur); });
}
/* only a post the checkpoints have a series for (the factory's own clips, read since the switch went on) */
let TRK = new Set();
const growable = p => SERIES && XT && (XT.on || []).includes(p.chan || p.account) && OUT[p.platform] && p.pid && TRK.has(p.platform + '|' + p.pid);
const rowKey = c => c.posts.map(p => p.platform + ':' + p.pid).join(',');
function renderTop(cur) {
  let rows = topRows(cur);
  const tbl = $('#top'), rk = RANKS.find(r => r.id === ui.rank) || RANKS[0];
  let note = ui.group === 'clip' ? 'One clip on every platform, its views added up.' : 'Each platform post on its own.';
  if (rk.id !== 'views') {
    const all = rows.length;
    rows = rows.map(c => ({...c, rv: rankVal(c, rk.id)})).filter(c => c.rv != null).sort((a, b) => rk.low ? a.rv - b.rv : b.rv - a.rv);
    note = rk.l + (rk.low ? ', lowest first' : ', highest first') + ': ' + rows.length + ' of ' + all + (ui.group === 'clip' ? ' clips' : ' posts') + ' have it (read at 24h, 7d and 30d).';
  }
  $('#top-note').textContent = note;
  if (!rows.length) {
    tbl.replaceChildren(el('tbody', {}, el('tr', {}, el('td', {class: 'empty'}, ui.q.trim() ? 'No titles match that search.' : rk.id !== 'views' ? 'No post in this range has that number yet.' : 'No posts in this range yet.'))));
  } else {
    tbl.replaceChildren(el('tbody', {}, rows.slice(0, ui.topN).flatMap((c, i) => {
      const ps = [...c.posts].sort((a, b) => (b.views || 0) - (a.views || 0));
      const url = (ps.find(p => p.url) || {}).url;
      const title = c.title || '(no title)';
      const chan = ps.find(p => p.chan && p.chan !== p.account), froze = ps.find(p => p.frozen && p.upd);
      const meta = [AN[c.account], chan ? 'on the ' + AN[chan.chan] + ' channel' : '', when(c), c.fmt ? cap(c.fmt) : '', c.dur ? c.dur + 's' : '', c.host,
        froze ? 'numbers from ' + fday(new Date(froze.upd).toLocaleDateString('en-CA')) : ''].filter(Boolean).join(' · ');
      const views = c.views != null ? c.views : sum(ps, p => p.views);
      const gps = ps.filter(growable), key = rowKey(c), open = gps.length > 0 && OPEN.has(key);
      const row = el('tr', {}, [
        el('td', {class: 'n'}, String(i + 1)),
        el('td', {}, [
          url && /^https:\/\//.test(url) ? el('a', {class: 'ttl', href: url, target: '_blank', rel: 'noopener noreferrer'}, title) : el('span', {class: 'ttl'}, title),
          el('div', {class: 'm'}, meta),
          el('div', {class: 'badges'}, ui.group === 'clip' ? ps.map(p => badge(p.platform, fmtN(p.views) + (p.watch_pct != null ? ' · ' + wpct(p.watch_pct) + ' watched' : ''))) : [badge(c.platform), el('span', {class: 'pb'}, (views ? pct(engaged(c) / views) : '0%') + ' engagement'), ...watchBadges(c)]),
          gps.length ? el('div', {class: 'gr-acts'}, el('button', {type: 'button', class: 'gr-open', 'aria-expanded': String(open),
            onclick: () => { if (OPEN.has(key)) OPEN.delete(key); else OPEN.set(key, ''); renderTop(last.cur); }}, open ? 'Hide how it grew' : 'How it grew')) : null,
        ]),
        el('td', {class: 'v'}, rk.id === 'views' ? fmtN(views) : rk.f(c.rv)),
      ]);
      return open ? [row, el('tr', {class: 'gr-row'}, el('td', {colspan: 3}, growBox(key, gps)))] : [row];
    })));
  }
  const more = $('#top-more');
  more.hidden = rows.length <= ui.topN;
  more.textContent = 'Show ' + Math.min(rows.length - ui.topN, 15) + ' more';
}

/* ------------------------------------------------------------------ how each post grew (checkpoints.py) */
const fx = x => (x >= 10 ? x.toFixed(0) : x.toFixed(1).replace(/\.0$/, '')) + 'x';
const fage = m => m < 120 ? m + ' min' : m < 2880 ? (m / 60).toFixed(1).replace(/\.0$/, '') + ' h' : (m / 1440).toFixed(1).replace(/\.0$/, '') + ' days';
function exFmt(k, v) {
  if (k === 'ig_reels_avg_watch_time' || k === 'post_video_avg_time_watched') return (v / 1000).toFixed(1) + 's';
  if (k === 'averageViewDuration') return v.toFixed(0) + 's';
  if (k === 'ig_reels_video_view_total_time' || k === 'post_video_view_time') { const h = v / 3600000; return h >= 1 ? h.toFixed(1) + ' h' : (v / 60000).toFixed(0) + ' min'; }
  if (k === 'reels_skip_rate') return (v <= 1 ? v * 100 : v).toFixed(0) + '%';
  if (k === 'averageViewPercentage') return v.toFixed(0) + '%';
  return fmtN(v);
}
function growBox(key, gps) {
  const root = el('div', {class: 'gr'});
  const id = OPEN.get(key);
  const pick = gps.find(p => p.platform + ':' + p.pid === id) || gps[0];
  if (gps.length > 1) {
    const s = el('div', {class: 'seg gr-seg', role: 'group', 'aria-label': 'Platform'});
    seg(s, gps.map(p => ({id: p.platform + ':' + p.pid, l: (PN[p.platform] || {}).name || p.platform})), pick.platform + ':' + pick.pid, v => { OPEN.set(key, v); renderTop(last.cur); });
    root.append(s);
  }
  const body = el('div', {class: 'gr-body', 'aria-live': 'polite'}, el('p', {class: 'empty'}, 'Loading how it grew...'));
  root.append(body);
  growLoad(pick).then(d => growDraw(body, d, pick)).catch(() => body.replaceChildren(el('p', {class: 'empty'}, 'Could not load it. The review page may be restarting.')));
  return root;
}
async function growLoad(p) {
  const url = '/stats/series?acct=' + encodeURIComponent(p.chan || p.account) + '&outlet=' + OUT[p.platform] + '&pid=' + encodeURIComponent(p.pid);
  const hit = SC.get(url);
  if (hit && Date.now() - hit.t < 60000) return hit.d;
  const r = await fetch(url, {cache: 'no-store'});
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const d = await r.json();
  SC.set(url, {t: Date.now(), d});
  return d;
}
function growLine(svg, L, vals, x, y, stroke, w) {
  const z = L.map((l, i) => [i, vals[l]]).filter(q => typeof q[1] === 'number');
  /* one read so far: a short level mark at it (a lone moveto draws nothing) */
  const d = z.length === 1 ? 'M' + (x(z[0][0]) - (z[0][0] ? 6 : 0)).toFixed(1) + ' ' + y(z[0][1]).toFixed(1) + ' L' + (x(z[0][0]) + 6).toFixed(1) + ' ' + y(z[0][1]).toFixed(1)
    : z.map((q, k) => (k ? 'L' : 'M') + x(q[0]).toFixed(1) + ' ' + y(q[1]).toFixed(1)).join(' ');
  if (d) svg.append(sv('path', {d, fill: 'none', stroke, 'stroke-width': z.length === 1 ? w + 1 : w, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
}
function growAxes(svg, W, H, m, top, step, y, f) {
  for (let v = 0; v <= top + 1e-9; v += step) {
    svg.append(sv('line', {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v ? 'var(--grid)' : 'var(--axis)', 'stroke-width': 1}));
    const t = sv('text', {x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 12, fill: 'var(--sub)'}); t.textContent = f(v); svg.append(t);
  }
}
function growChart(box, labs, at, uv, c) {
  const W = Math.max(180, Math.floor(box.clientWidth || 300)), H = W < 560 ? 170 : 200;
  const lastI = Math.max(1, ...labs.map((l, i) => (typeof at[l] === 'number' || typeof uv[l] === 'number') ? i : 0));
  const L = labs.slice(0, lastI + 1);
  const max = Math.max(1, ...L.map(l => Math.max(at[l] || 0, uv[l] || 0)));
  const step = niceStep(max), top = Math.ceil(max / step) * step;
  /* the left margin fits the widest axis figure (12px figures are about 7px each), so none is cut at 280px */
  let wide = 1;
  for (let v = 0; v <= top + 1e-9; v += step) wide = Math.max(wide, fmtN(v).length);
  const m = {t: 10, r: 14, b: 24, l: Math.max(44, 14 + 7 * wide)};
  const x = i => m.l + i * (W - m.l - m.r) / (L.length - 1);
  const y = v => m.t + (H - m.t - m.b) * (1 - v / top);
  const svg = sv('svg', {width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Views at each checkpoint after it went public, and the usual for this account.'});
  growAxes(svg, W, H, m, top, step, y, fmtN);
  const lastL = L.length - 1, every = (W - m.l - m.r) / Math.max(1, lastL) < 40 ? 2 : 1;   /* labels never touch */
  L.forEach((l, i) => {
    if ((i % every && i !== lastL) || (i !== lastL && lastL - i < every)) return;
    const t = sv('text', {x: x(i), y: H - 6, 'text-anchor': i === 0 ? 'start' : i === lastL ? 'end' : 'middle', 'font-size': 12, fill: 'var(--sub)'}); t.textContent = l; svg.append(t);
  });
  growLine(svg, L, uv, x, y, 'var(--axis)', 1.5);
  growLine(svg, L, at, x, y, c, 2);
  box.replaceChildren(svg);
}
function growRetention(box, pts) {
  const W = Math.max(180, Math.floor(box.clientWidth || 300)), H = 130, m = {t: 10, r: 14, b: 24, l: 44};
  const max = Math.max(1, ...pts.map(z => z[1]));
  const step = max > 1.2 ? 0.5 : 0.25, top = Math.ceil(max / step) * step;
  const x = r => m.l + r * (W - m.l - m.r), y = v => m.t + (H - m.t - m.b) * (1 - v / top);
  const svg = sv('svg', {width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'The share of viewers still watching along the clip.'});
  growAxes(svg, W, H, m, top, step, y, v => Math.round(v * 100) + '%');
  [[0, 'Start'], [0.5, 'Middle'], [1, 'End']].forEach(([r, w]) => { const t = sv('text', {x: x(r), y: H - 6, 'text-anchor': r === 0 ? 'start' : r === 1 ? 'end' : 'middle', 'font-size': 12, fill: 'var(--sub)'}); t.textContent = w; svg.append(t); });
  svg.append(sv('path', {d: pts.map((z, k) => (k ? 'L' : 'M') + x(z[0]).toFixed(1) + ' ' + y(z[1]).toFixed(1)).join(' '), fill: 'none', stroke: 'var(--blue)', 'stroke-width': 2, 'stroke-linejoin': 'round'}));
  box.replaceChildren(svg);
}
function growDraw(body, d, p) {
  const plat = (PN[p.platform] || {}).name || p.platform, c = (PN[p.platform] || {}).c || 'var(--blue)';
  if (!d.on) { body.replaceChildren(el('p', {class: 'empty'}, 'Checkpoints are off for this account.')); return; }
  const pts = (d.points || []).filter(z => typeof z.views === 'number');
  if (!pts.length) {
    const missed = d.missed || [], gone = (d.points || []).some(z => z.expired);
    const say = d.gone_at ? plat + ' no longer has this post.'
      : gone ? 'YouTube lets these numbers be kept for 30 days, so its older checkpoint reads were deleted here.'
      : d.tracked === false ? 'Not tracked: only the factory\'s own clips, posted since checkpoints went on, are read again.'
      : missed.length ? 'No checkpoint read for this post. Missed: ' + missed.join(', ') + ' (no read near that time).'
      : 'No checkpoint read yet. It is asked for at its next checkpoint.';
    body.replaceChildren(el('p', {class: 'empty'}, say));
    return;
  }
  const labs = (d.labels || []).map(z => z[0]), usual = d.usual || {};
  /* a late read (the Mac slept) or an early one (a backfill) is in the table with its true age, never on the line beside the usual */
  const ontime = pts.filter(z => !z.late && !z.early);
  const at = Object.fromEntries(ontime.map(z => [z.label, z.views])), uv = Object.fromEntries(Object.entries(usual).map(([k, v]) => [k, v.median]));
  const n = Math.max(0, ...Object.values(usual).map(v => v.n || 0));
  const words = [];
  const lastPt = pts[pts.length - 1];
  const ex = d.early && typeof d.early.x === 'number' && (d.early.x >= 2 || d.early.x <= 0.5) ? d.early : null;
  const head = ex ? (ex.x >= 2 ? 'Running hot. ' : 'Slow start. ') : '';
  if (lastPt.late || lastPt.early) words.push(el('span', {}, [head + 'At ' + lastPt.label + ', read ' + (lastPt.late ? 'late' : 'early') + ' (' + fage(lastPt.age_min || 0) + ' after it went public): ', el('b', {}, fmtN(lastPt.views)), '. A' + (lastPt.late ? ' late' : 'n early') + ' read is not compared with your usual. ']));
  else if (uv[lastPt.label]) words.push(el('span', {}, [head + 'At ' + lastPt.label + ': ', el('b', {}, fmtN(lastPt.views)), ', ' + fx(lastPt.views / uv[lastPt.label]) + ' your usual on ' + plat + '. ']));
  else if (ex) words.push(head + fx(ex.x) + ' the usual at ' + ex.label + '. ');
  if (ex && (uv[lastPt.label] || lastPt.late || lastPt.early) && ex.label !== lastPt.label) words.push('At ' + ex.label + ' it was ' + fx(ex.x) + ' the usual. ');
  if ((d.missed || []).length) words.push('Missed: ' + d.missed.join(', ') + ' (no read near that time). ');
  if (d.backfill) words.push('Some points come from the nightly reads. ');
  if (!n) words.push('The usual line shows once 8 posts have a read at the same checkpoint.');
  const box = ontime.length ? el('div', {class: 'gr-chart'}) : null;
  const kids = [
    box && el('ul', {class: 'legend'}, [el('li', {}, [el('i', {style: 'background:' + c}), 'This post']), n ? el('li', {}, [el('i', {class: 'gr-u'}), 'Your usual on ' + plat + ' (median of the last ' + n + ' posts)']) : null]),
    box,
    words.length ? el('p', {class: 'gr-note'}, words) : null,
    el('details', {class: 'as-table'}, [el('summary', {}, 'These numbers as a table'), el('div', {class: 'tbl'}, el('table', {}, [
      el('thead', {}, el('tr', {}, ['Checkpoint', 'Read at', 'Views', 'Likes', 'Comments', 'Shares', 'Usual'].map((h, i) => el('th', {class: i > 1 ? 'r' : null}, h)))),
      el('tbody', {}, pts.map(z => el('tr', {}, [el('td', {}, z.label), el('td', {}, fage(z.age_min || 0) + (z.late ? ' (late)' : z.early ? ' (early)' : '')),
        ...['views', 'likes', 'comments', 'shares'].map(k => el('td', {class: 'r'}, typeof z[k] === 'number' ? z[k].toLocaleString('en-US') : '')),
        el('td', {class: 'r'}, uv[z.label] ? fmtN(uv[z.label]) : '')]))),
    ]))]),
  ];
  const xs = pts.filter(z => z.extra && Object.keys(z.extra).some(k => k !== 'retention'));
  if (xs.length) {
    const keys = [...new Set(xs.flatMap(z => Object.keys(z.extra).filter(k => k !== 'retention' && typeof z.extra[k] === 'number')))];
    kids.push(el('h4', {}, 'More numbers'), el('div', {class: 'tbl'}, el('table', {}, [
      el('thead', {}, el('tr', {}, [el('th', {}, 'Checkpoint'), ...keys.map(k => el('th', {class: 'r', title: k}, (d.names || {})[k] || k))])),
      el('tbody', {}, xs.map(z => el('tr', {}, [el('td', {}, z.label), ...keys.map(k => el('td', {class: 'r'}, typeof z.extra[k] === 'number' ? exFmt(k, z.extra[k]) : ''))]))),
    ])));
  }
  const ret = [...pts].reverse().find(z => z.extra && Array.isArray(z.extra.retention) && z.extra.retention.length > 1);
  const rbox = ret ? el('div', {class: 'gr-chart'}) : null;
  if (ret) kids.push(el('h4', {}, 'Still watching along the clip (at ' + ret.label + ')'), rbox);
  body.replaceChildren(...kids.filter(Boolean));
  if (box) growChart(box, labs, at, uv, c);
  if (ret) growRetention(rbox, ret.extra.retention);
}
async function loadExtras() {
  try {
    const r = await fetch('/stats/series?view=extras', {cache: 'no-store'});
    if (!r.ok) return;
    XT = await r.json();
    TRK = new Set(XT.tracked || []);
  } catch (e) { return; }
  SC.clear();
  if (last) { renderRank(); renderTop(last.cur); }
}

/* ------------------------------------------------------------------ how long people watch */
const wpct = x => Math.round(x) + '%';
/* skipped in the first 3 seconds: Instagram's own figure only. Facebook's retention curve sits at 100% for its first
   buckets on most Reels (2026-10-20 dry run), so a figure read off it is no skip rate; it stays in the CSV. */
const skipOf = p => p.skip_pct;
function watchBadges(p) {
  const out = [];
  if (p.watch_pct != null) out.push(wpct(p.watch_pct) + ' watched' + (p.watch_s != null ? ' (' + p.watch_s.toFixed(1) + 's)' : ''));
  if (skipOf(p) != null) out.push(wpct(skipOf(p)) + ' skipped in 3s');
  if (p.engaged_pct != null) out.push(wpct(p.engaged_pct) + ' engaged');
  if (p.saved) out.push(fmtN(p.saved) + ' saved');
  if (p.replays) out.push(fmtN(p.replays) + ' replays');
  return out.map(t => el('span', {class: 'pb'}, t));
}
function renderWatch(cur) {
  const keyOf = ui.wby === 'account' ? (p => p.account) : (p => p.fmt || '');
  const name = k => ui.wby === 'account' ? (AN[k] || k) : (k ? cap(k) : 'No format');
  const keys = ui.wby === 'account' ? D.accounts.map(a => a.id).filter(acctOk)
    : [...new Set(cur.map(keyOf))].sort((a, b) => cur.filter(p => keyOf(p) === b).length - cur.filter(p => keyOf(p) === a).length);
  const med = (ps, f) => { const v = ps.map(f).filter(x => x != null && !isNaN(x)); return v.length ? median(v) : null; };
  const cell = (v, show, cls) => el('td', {class: v == null ? 'na' : cls || null}, v == null ? '–' : show(v));
  const rows = [];
  for (const k of keys) {
    let first = true;
    for (const pl of PLAT.filter(q => ui.plats.includes(q.id))) {
      const ps = cur.filter(p => keyOf(p) === k && p.platform === pl.id);
      if (!ps.length) continue;
      const timed = ps.filter(p => p.watch_pct != null || p.watch_s != null || skipOf(p) != null || p.engaged_pct != null);
      const sk = med(ps, skipOf);
      rows.push(el('tr', {class: first && rows.length ? 'wt-gap' : null}, [
        el('td', {}, first ? name(k) : ''), el('td', {}, [el('span', {class: 'pb'}, [el('i', {style: 'background:' + pl.c}), pl.name])]),
        el('td', {}, timed.length + ' of ' + ps.length),
        cell(med(ps, p => p.watch_pct), wpct), cell(med(ps, p => p.watch_s), x => x.toFixed(1) + 's'),
        cell(sk, wpct), cell(med(ps, p => p.engaged_pct), wpct),
        cell(med(ps, p => p.views || 0), fmtN),
      ]));
      first = false;
    }
  }
  const tbl = $('#wtime');
  if (!rows.length) tbl.replaceChildren(el('tbody', {}, el('tr', {}, el('td', {class: 'empty'}, 'No posts in this range yet.'))));
  else tbl.replaceChildren(
    el('thead', {}, el('tr', {}, ['', 'Platform', 'Posts with it', 'Watched', 'Seconds', 'Skipped in 3s', 'Engaged', 'Typical views'].map((h, i) => el('th', {title: [null, null, 'Posts that have any watch number. A post is read about two days after it goes up on YouTube.', 'Average share of the clip played', 'Average seconds watched a play', 'Instagram\'s own figure (an estimate). YouTube: see Engaged. Facebook gives none.', 'YouTube engaged views as a share of all views', 'The middle post\'s views'][i]}, h)))),
    el('tbody', {}, rows));
  const miss = $('#wtime-miss'), nw = (D.no_watch || []).filter(n => ui.acct === 'all' || AN[ui.acct] === n);
  miss.hidden = !nw.length || !ui.plats.includes('youtube');
  miss.textContent = nw.length ? 'YouTube has no watch time yet for ' + nw.join(' and ') + ': each channel needs one new sign-in that allows YouTube Analytics.' : '';
}

/* ------------------------------------------------------------------ bar lists */
function bars(root, items, empty) {
  if (!items.length) { root.replaceChildren(el('div', {class: 'empty'}, empty || 'Nothing to compare yet.')); return; }
  const max = Math.max(1, ...items.map(i => i.value));
  root.replaceChildren(...items.map(i => el('div', {class: 'bar'}, [
    el('div', {class: 'l'}, [i.c ? el('i', {style: 'background:' + i.c}) : null, el('span', {}, i.label)]),
    el('div', {class: 'x'}, i.money ? money(i.value) : fmtN(i.value)),
    el('div', {class: 't'}, i.value ? el('div', {class: 'f', style: `width:${(i.value / max * 100).toFixed(1)}%` + (i.c ? ';background:' + i.c : '')}) : null),
    i.sub ? el('div', {class: 's'}, i.sub) : null,
  ])));
}
function groups(posts, key, min) {
  const g = new Map();
  for (const p of posts) { const k = key(p); if (k == null || k === '') continue; if (!g.has(k)) g.set(k, []); g.get(k).push(p); }
  return [...g.entries()].filter(([, a]) => a.length >= (min || 1)).map(([k, a]) => {
    const v = sum(a, p => p.views);
    return {key: k, n: a.length, views: v, avg: v / a.length, eng: v ? sum(a, engaged) / v : 0};
  });
}
const posts_ = n => n + (n === 1 ? ' post' : ' posts');
function renderPlatforms(cur) {
  const tot = sum(cur, p => p.views) || 1;
  bars($('#by-plat'), PLAT.filter(p => ui.plats.includes(p.id)).map(pl => {
    const ps = cur.filter(p => p.platform === pl.id), v = sum(ps, p => p.views);
    const sub = ps.length ? `${pct(v / tot)} of views · ${posts_(ps.length)} · ${fmtN(v / ps.length)} a post · ${money(sum(ps, earn))} est.`
      : D.by_hand.includes(pl.id) ? 'Posted by hand. Its public videos are read here once they are up.' : 'No posts in this range.';
    return {label: pl.name, value: v, c: pl.c, sub};
  }).sort((a, b) => b.value - a.value));
}
function renderSlots(cur) {
  const all = cur.length ? sum(cur, p => p.views) / cur.length : 0;
  const mins = s => +s.slice(0, 2) * 60 + +s.slice(3, 5);
  const nearest = p => { const t = p.hour * 60 + +p.hm.slice(3, 5); let best = null, gap = 21; for (const s of D.slots) { const g = Math.abs(mins(s) - t); if (g < gap) { gap = g; best = s; } } return best; };
  /* a post made for a slot counts at that slot, even when it went out late or the slot times changed since */
  const slotOf = p => p.slot || nearest(p);
  const g = groups(cur, slotOf);
  const by = Object.fromEntries(g.map(r => [r.key, r]));
  const old = g.map(r => r.key).filter(k => !D.slots.includes(k)).sort();
  const items = [...D.slots, ...old].map(s => by[s] ? {label: D.slots.includes(s) ? s : s + ((D.spares || []).includes(s) ? ' (spare slot)' : ' (old slot)'), value: by[s].avg, sub: `${posts_(by[s].n)} · ${all ? (by[s].avg / all).toFixed(1) + 'x your average' : ''}`} : {label: s, value: 0, sub: 'No posts at this slot in this range.'});
  const other = cur.filter(p => !slotOf(p));
  if (other.length) items.push({label: 'Other times', value: sum(other, p => p.views) / other.length, sub: posts_(other.length) + ' away from any slot'});
  bars($('#by-slot'), items.sort((a, b) => b.value - a.value), 'No posts in this range yet.');
}
function renderWorking(cur) {
  bars($('#by-fmt'), groups(cur, p => p.fmt).sort((a, b) => b.avg - a.avg).map(r => ({label: cap(r.key), value: r.avg, sub: `${posts_(r.n)} · ${pct(r.eng)} engagement`})), 'No clips with a format in this range.');
  bars($('#by-host'), groups(cur, p => p.host, 2).sort((a, b) => b.avg - a.avg).slice(0, 8).map(r => ({label: r.key, value: r.avg, sub: `${posts_(r.n)} · ${fmtN(r.views)} views in all`})), 'Needs two posts from one source.');
  const LEN = [[0, 29, 'Under 30s'], [30, 44, '30 to 44s'], [45, 60, '45 to 60s'], [61, 75, '61 to 75s'], [76, 90, '76 to 90s'], [91, 1e9, 'Over 90s']];
  const lenOf = p => { if (!p.dur) return null; const r = LEN.find(([a, b]) => p.dur >= a && p.dur <= b); return r ? r[2] : null; };
  const order = LEN.map(r => r[2]);
  bars($('#by-len'), groups(cur, lenOf).sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key)).map(r => ({label: r.key, value: r.avg, sub: `${posts_(r.n)} · ${pct(r.eng)} engagement`})), 'No lengths in this range.');
}

/* ------------------------------------------------------------------ heatmap */
function renderHeat(cur) {
  const cells = Array.from({length: 7}, () => Array.from({length: 24}, () => ({n: 0, v: 0})));
  for (const p of cur) { const c = cells[p.dow][p.hour]; c.n++; c.v += p.views || 0; }
  const avgs = cells.flat().filter(c => c.n).map(c => c.v / c.n).sort((a, b) => a - b);
  const cuts = [1, 2, 3, 4, 5, 6].map(k => avgs.length ? avgs[Math.min(avgs.length - 1, Math.floor(avgs.length * k / 7))] : 0);
  const shade = a => 'var(--h' + (1 + cuts.filter(x => a >= x).length) + ')';
  const slotHours = new Set(D.slots.map(s => +s.slice(0, 2)));
  const kids = [el('div')];
  for (let h = 0; h < 24; h++) kids.push(el('div', {class: 'hh'}, h % 3 ? '' : (h % 12 || 12) + (h < 12 ? 'a' : 'p')));
  cells.forEach((row, di) => {
    kids.push(el('div', {class: 'hl'}, DOW[di]));
    row.forEach((c, h) => {
      const a = c.n ? c.v / c.n : 0;
      const words = `${DOW[di]} ${fhour(h)}: ` + (c.n ? `${fmtN(a)} views a post, ${posts_(c.n)}` : 'nothing posted');
      kids.push(el('div', {class: 'c' + (slotHours.has(h) ? ' fs' : '') + (c.n ? ' on' : ''), style: c.n ? 'background:' + shade(a) : null,
        role: 'img', 'aria-label': words, 'data-w': words}));
    });
  });
  $('#heat').replaceChildren(...kids);
  $('#heat-key').replaceChildren('Fewer views', el('span', {class: 'ramp'}, [1, 2, 3, 4, 5, 6, 7].map(i => el('i', {style: `background:var(--h${i})`}))), 'More views', el('span', {}, '· Grey: nothing posted'));
}
$('#heat').addEventListener('pointermove', ev => {
  const c = ev.target.closest('.c'); if (!c) { tipOff(); return; }
  tipAt(ev.clientX, ev.clientY, [el('div', {}, c.dataset.w)]);
});
$('#heat').addEventListener('pointerleave', tipOff);

/* ------------------------------------------------------------------ the factory */
function renderFactory() {
  const b = bounds();
  const rows = D.factory.filter(r => acctOk(r.account) && dnum(r.day) <= b.end && (b.start == null || dnum(r.day) >= b.start));
  const accts = D.accounts.filter(a => acctOk(a.id));
  const cols = accts.map(a => ({name: a.name, rs: rows.filter(r => r.account === a.id)}));
  if (cols.length > 1) cols.push({name: 'All', rs: rows, sum: true});
  const plats = PLAT.filter(p => ui.plats.includes(p.id) && (!D.by_hand.includes(p.id) || rows.some(r => r.posted[p.id])));
  const out = [
    ['Slots', r => r.slots], ['Clips made', r => r.made], ['Approved', r => r.approved], ['Declined', r => r.declined],
    ['Not signed in time', r => r.unsigned],
    ...plats.map((p, i) => ['Went out on ' + p.name, r => r.posted[p.id] || 0, i === 0]),
    ['Gave up on a platform', r => sum(Object.entries(r.gave_up), ([k, n]) => ui.plats.includes(k) ? n : 0), !plats.length],
  ];
  const priv = r => sum(Object.entries(r.private || {}), ([k, n]) => ui.plats.includes(k) ? n : 0);
  if (rows.some(r => priv(r))) out.push(['Private test posts (Only me)', priv]);
  const tbl = $('#fac');
  if (!rows.length) { tbl.replaceChildren(el('tbody', {}, el('tr', {}, el('td', {class: 'empty'}, 'No factory days in this range.')))); return; }
  tbl.replaceChildren(
    el('thead', {}, el('tr', {}, [el('th', {}, ''), ...cols.map(c => el('th', {}, c.name))])),
    el('tbody', {}, out.map(([name, f, gap]) => el('tr', {class: gap ? 'gap' : null},
      [el('td', {}, name), ...cols.map(c => el('td', {class: c.sum ? 'both' : null}, sum(c.rs, f).toLocaleString('en-US')))]))));
}

/* ------------------------------------------------------------------ why clips came back (reasons.py) */
function weekOf(s) { const n = dnum(s); return dstr(n - wday(n)); }
function renderReasons() {
  const W = D.reasons || {codes: [], gates: [], reasons: [], holds: []};
  const rows = W.reasons.filter(r => acctOk(r.account)), holds = W.holds.filter(h => acctOk(h.account));
  const now = weekOf(D.today);
  const first = [...rows.map(r => r.week), ...holds.map(h => h.week), now].sort()[0];
  if (!ui.week || ui.week > now || ui.week < first) ui.week = now;
  $('#why-week').textContent = 'Week of ' + fday(ui.week) + (ui.week === now ? ' (this week)' : '');
  $('#why-prev').disabled = ui.week <= first;
  $('#why-next').disabled = ui.week >= now;
  const wk = rows.filter(r => r.week === ui.week), hk = holds.filter(h => h.week === ui.week);
  const accts = D.accounts.filter(a => acctOk(a.id) && (W.reasons.some(r => r.account === a.id) || a.id === ui.acct || ui.acct === 'all'));
  const cols = accts.map(a => ({name: a.short || a.name, f: r => r.account === a.id}));
  if (cols.length > 1) cols.push({name: 'All', f: () => true, sum: true});
  const cell = (n, c) => el('td', {class: (c && c.sum ? 'both ' : '') + (n ? '' : 'z')}, n ? n.toLocaleString('en-US') : '0');
  const gname = Object.fromEntries(W.gates.map(g => [g.key, g.name]));
  const codeRows = W.codes.map(c => el('tr', {title: c.what + (c.gate ? '. The check that should have caught it: the ' + gname[c.gate] : '')},
    [el('td', {}, [c.label, c.hard ? el('span', {class: 'hard'}, 'hard') : null]),
     ...cols.map(col => cell(wk.filter(r => r.code === c.key && col.f(r)).length, col))]));
  const none = wk.filter(r => !r.code);
  if (none.length) codeRows.push(el('tr', {title: 'Declined before the codes, or with no code from the command line'},
    [el('td', {}, 'No code'), ...cols.map(col => cell(none.filter(col.f).length, col))]));
  $('#why-codes').replaceChildren(
    el('thead', {}, el('tr', {}, [el('th', {}, 'Reason'), ...cols.map(c => el('th', {}, c.name))])),
    el('tbody', {}, codeRows));
  const counted = new Set(['cast', 'late']);      /* the checks the automatic sign-off holds a clip for */
  $('#why-gates').replaceChildren(
    el('thead', {}, el('tr', {}, [el('th', {}, 'Check'), el('th', {title: 'Clips it passed that you pulled or declined'}, 'Missed'),
      el('th', {title: 'Of those, for a hard reason'}, 'Hard'), el('th', {title: 'It held the clip, and you approved it unchanged'}, 'Held for nothing')])),
    el('tbody', {}, W.gates.map(g => {
      const m = wk.filter(r => r.gate === g.key), h = hk.filter(x => x.gate === g.key);
      const held = counted.has(g.key)
        ? el('td', {class: h.length ? '' : 'z', title: h.length ? h.filter(x => x.unchanged).length + ' of the ' + h.length + ' it held were approved unchanged' : ''},
            h.length ? h.filter(x => x.unchanged).length + ' of ' + h.length : '0')
        : el('td', {class: 'z', title: 'Not counted: this check refuses a clip before it reaches you'}, '\u2013');
      return el('tr', {title: g.lines}, [el('td', {}, cap(g.name)),
        g.key === 'late' ? el('td', {class: 'z'}, '\u2013') : cell(m.length), g.key === 'late' ? el('td', {class: 'z'}, '\u2013') : cell(m.filter(r => r.hard).length), held]);
    })));
  const auto = wk.filter(r => r.auto).length;
  $('#why-note').textContent = 'Missed: a clip the check passed that you pulled or declined. Held for nothing: the automatic sign-off waited for you on that check and you approved the clip as it was. '
    + 'Only the cast check and the late rule hold clips for you; the skills\' checks refuse a clip before you see it.'
    + (auto ? ' ' + auto + ' of this week\'s pulls were of clips that had signed themselves.' : '');
}
$('#why-prev').addEventListener('click', () => { ui.week = dstr(dnum(ui.week) - 7); renderReasons(); });
$('#why-next').addEventListener('click', () => { ui.week = dstr(dnum(ui.week) + 7); renderReasons(); });

/* ------------------------------------------------------------------ money */
const RATES = [['youtube', 'YouTube'], ['tiktok', 'TikTok, 60s or less'], ['tiktok_long', 'TikTok, over 60s'], ['instagram', 'Instagram'], ['facebook', 'Facebook, if invited'], ['campaign', 'Clipping campaigns']];
const GATES = {
  youtube: {program: 'Partner Program', subs: 1000, views: 10e6, days: 90},
  tiktok: {program: 'Creator Rewards', subs: 10000, views: 1e5, days: 30},
  facebook: {program: 'Content Monetization'},
  instagram: {program: 'No pay per view'},
};
function saveSettings() {
  render();
  clearTimeout(setT);
  setT = setTimeout(async () => {
    try {
      const r = await fetch('/act', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Token': TOKEN},
        body: JSON.stringify({op: 'stats-settings', rpm: D.settings.rpm, goal: D.settings.goal, eligible: D.settings.eligible,
          followers_typed: typedOut()})});
      let d; try { d = await r.json(); } catch (e) { d = {ok: false, message: 'The page sent an answer it could not read. Reload it.'}; }
      if (r.status === 403 && d.stale) { toast('This tab was open before the page restarted. Reloading...'); setTimeout(() => location.reload(), 900); return; }
      toast(d.ok ? 'Saved.' : (d.message || 'Not saved.'), !d.ok);
    } catch (e) { toast('Could not save: the review page did not answer. Try again in a few seconds.', true); }
  }, 600);
}
function numInput(id, val, step, onset) {
  let n = document.getElementById(id);
  if (!n) {
    n = el('input', {id, type: 'number', min: 0, step, inputmode: 'decimal'});
    n.addEventListener('change', () => { const v = Math.max(0, +n.value || 0); n.value = v; onset(v); saveSettings(); });
  }
  if (document.activeElement !== n) n.value = val;
  return n;
}
const per1k = r => '$' + (+r || 0).toFixed(2) + ' per 1,000';
const dayWord = n => n === 1 ? '1 day' : n + ' days';
/* views gained by each post between the history snapshots (stats.history): the only true "views a day" */
function histInfo(scope) {
  const H = D.history || {}, g = H.gains || {}, span = +H.span || 0;
  const rows = scope.map(p => ({p, v: g[p.id] || 0})).filter(x => x.v > 0);
  return {span, ok: span >= 0.9, days: H.days || 0, first: H.first || '', rows};
}
/* this calendar month from the snapshots (stats.month): views gained since the month's first read; a clip that
   went up this month counts whole */
function monthInfo(scope) {
  const M = D.month || {}, g = M.gains || {};
  const rows = scope.map(p => ({p, v: g[p.id] || 0})).filter(x => x.v > 0);
  return {ok: !!M.base_at, partial: !!M.partial, base: M.base_at || '', rows, views: sum(rows, x => x.v)};
}
/* the last 7 days counted from the first day posted in them (the factory has not run 7 days everywhere yet) */
function trailing(scope) {
  const end = dnum(D.today), list = scope.filter(p => dnum(p.day) > end - 7 && dnum(p.day) <= end);
  const first = list.length ? Math.min(...list.map(p => dnum(p.day))) : end;
  const days = Math.max(1, end - first + 1);
  return {list, days, views: sum(list, p => p.views)};
}
function renderMoney(cur) {
  const s = D.settings, goal = +s.goal || 0;
  const scope = P.filter(p => acctOk(p.account) && ui.plats.includes(p.platform));
  const everPaid = Object.values(s.eligible || {}).some(by => Object.values(by || {}).some(Boolean));
  const act = sum(scope, actual), est = sum(cur, earn), estViews = sum(cur, p => p.views);
  const h = histInfo(scope), t = trailing(scope);
  /* run-rate a month, from the history: views gained a day x 30 x the rate */
  const rr = h.ok ? sum(h.rows, x => x.v / 1000 * rate(x.p)) / h.span * 30 : null;
  const rrAct = h.ok ? sum(h.rows, x => paid(x.p) ? x.v / 1000 * rate(x.p) : 0) / h.span * 30 : 0;
  const vDay = h.ok ? sum(h.rows, x => x.v) / h.span : t.views / t.days;
  /* today's mix: each platform's share of the last 7 days of views, and the blended plan rate it gives */
  const mixRows = h.ok ? h.rows.map(x => ({p: x.p, v: x.v})) : t.list.map(p => ({p, v: p.views || 0}));
  const mixV = sum(mixRows, x => x.v), blend = mixV ? sum(mixRows, x => x.v * rate(x.p)) / mixV : 0;
  const tile = (k, v, sub, isEst) => el('div', {}, [el('div', {class: 'k'}, k), el('div', {class: 'v' + (isEst ? ' est' : '')}, v), el('div', {class: 's'}, sub)]);
  const left = Math.max(0, Math.ceil(7 - h.span));
  const histWords = !h.days ? 'History starts with the next read.'
    : h.ok ? `From ${dayWord(Math.max(1, Math.round(h.span)))} of history` + (left ? `, ${dayWord(left)} more to a full week.` : '.')
    : `First week of history: ${dayWord(left || 7)} more.`;
  const rangeWords = ui.range ? `last ${ui.range} days` : 'all time';
  const m = monthInfo(scope);
  const monthWords = !m.ok ? 'Starts with the next read of the numbers.'
    : m.partial ? `Clips from before the month count from ${stamp(m.base)}, the first read this month.`
    : `Since the month's first read, ${stamp(m.base)}.`;
  $('#money').replaceChildren(
    tile('Earned so far (actual)', money(act), everPaid ? 'only the programs ticked In it below' : 'No account is in a paid program yet.'),
    tile('If monetized (estimate)', money(est), `${rangeWords}, ${fmtN(estViews)} views at the plan rates`, true),
    tile('Run-rate a month (estimate)', rr == null ? 'Not yet' : money(rr), histWords, true),
    tile('Goal gap', money(Math.max(0, goal - rrAct)) + ' a month', `goal ${money(goal)} minus actual pay of ${money(rrAct)} a month`),
    tile('Views gained this month', m.ok ? fmtN(m.views) : 'Not yet', monthWords),
    tile('This month (estimate)', m.ok ? money(sum(m.rows, x => x.v / 1000 * rate(x.p))) : 'Not yet',
      m.ok ? `at the plan rates; actual ${money(sum(m.rows, x => paid(x.p) ? x.v / 1000 * rate(x.p) : 0))}` : '', true),
  );
  const goalIn = numInput('goal-in', goal, 50, v => { D.settings.goal = v; });
  const box = $('#goal');
  if (!box.firstChild) {
    box.append(el('div', {class: 'row'}, [el('label', {}, ['Monthly goal ($)', goalIn]), el('div', {class: 'grow'}, [el('div', {class: 'k', id: 'goal-words'}), el('div', {class: 'progress'}, el('i', {id: 'goal-bar'}))])]), el('p', {id: 'goal-math'}), el('p', {id: 'goal-now'}));
  }
  const prog = goal ? Math.min(1, rrAct / goal) : 0;
  $('#goal-words').textContent = goal ? `Actual: ${money(rrAct)} of ${money(goal)} a month (${pct(prog)})` : 'Set a goal to see the math.';
  $('#goal-bar').style.width = (prog * 100).toFixed(1) + '%';
  const gap = Math.max(0, goal - rrAct);
  const mixWords = PLAT.map(pl => ({n: pl.name, v: sum(mixRows.filter(x => x.p.platform === pl.id), x => x.v)})).filter(x => x.v).sort((a, b) => b.v - a.v).map(x => `${x.n} ${pct(x.v / mixV)}`).join(', ');
  const gm = $('#goal-math');
  if (!goal) gm.textContent = '';
  else if (!gap) gm.textContent = 'Actual pay is at the goal.';
  else if (!mixV) gm.textContent = 'No views in the last 7 days to work the gap out from.';
  else if (!blend) gm.textContent = `At today's mix (${mixWords}) no platform pays at the plan rates, so more views alone do not close the gap.`;
  else gm.replaceChildren(el('b', {}, money(gap) + ' more a month'), ' = about ', el('b', {}, fmtN(gap / blend * 1000 / 30) + ' more views a day'),
    ` at today's mix (estimate: ${mixWords}; ${per1k(blend)} blended).`);
  const pDay = t.list.length / t.days;
  $('#goal-now').textContent = `Now: about ${fmtN(vDay)} views a day` + (h.ok ? '' : ' (rough: the views of the last 7 days\' posts)') +
    `, and ${pDay.toFixed(pDay < 10 ? 1 : 0)} posts a day over ${dayWord(t.days)}` + (t.list.length ? `, ${fmtN(t.views / t.list.length)} views a post.` : '.');
  /* what each route takes to reach the goal (a rate is applied: estimates) */
  const r = s.rpm, perDay = rt => rt ? fmtN(goal / rt * 1000 / 30) : '-';
  const short = D.accounts.filter(a => acctOk(a.id) && a.max && a.max <= 60);
  const miles = [
    ['YouTube', `Partner Program: 1,000 subscribers and 10M Shorts views in 90 days (about 111K a day).` + (r.youtube ? ` At that gate Shorts pay about ${money(10e6 / 90 * 30 / 1000 * r.youtube)} a month. ${money(goal)} a month from Shorts alone takes about ${perDay(r.youtube)} views a day (estimate, ${per1k(r.youtube)}).` : '')],
    ['TikTok', `Creator Rewards: 10,000 followers and 100,000 views in 30 days, and only clips over 60s pay.` + (r.tiktok_long ? ` ${money(goal)} a month takes about ${perDay(r.tiktok_long)} views a day (estimate, ${per1k(r.tiktok_long)}).` : '') +
      short.map(a => ` ${a.name} clips run ${Math.round(a.min)}-${Math.round(a.max)}s, so they earn $0 there.`).join('')],
    ['Facebook', `Content Monetization is invite only.` + (r.facebook ? ` If invited, ${money(goal)} a month takes about ${perDay(r.facebook)} plays a day (estimate, ${per1k(r.facebook)}).` : '')],
    ['Clipping campaigns', (r.campaign ? `About ${per1k(r.campaign)}, about ${Math.round((+D.campaign_fees || 0) * 100)}% to fees, no follower gate. ${money(goal)} a month takes about ${perDay(r.campaign * (1 - (+D.campaign_fees || 0)))} views a day (estimate). The only route open today.` : 'Set a rate below to see the math.')],
    ['Instagram', 'No pay per view. Bonuses are invite only.'],
  ];
  $('#miles').replaceChildren(...miles.map(([k, v]) => el('li', {}, [el('b', {}, k), v])));
  const tt = cur.filter(p => p.platform === 'tiktok'), shortT = tt.filter(p => p.dur && p.dur <= 60);
  const note = $('#tt-note');
  note.hidden = !shortT.length;
  if (shortT.length) note.textContent = `TikTok pays Creator Rewards only on videos over one minute: ${shortT.length} of your ${tt.length} TikTok posts here are 60 seconds or shorter (${fmtN(sum(shortT, p => p.views))} views).`;
  const rates = $('#rates');
  const inputs = RATES.map(([k, l]) => [l, numInput('rate-' + k, s.rpm[k] ?? 0, 0.01, v => { D.settings.rpm[k] = v; })]);
  if (!rates.firstChild) rates.append(...inputs.map(([l, n]) => el('label', {}, [l, n])));
}

/* ------------------------------------------------------------------ paid programs */
function fol(acct, plat) { return ((D.followers || {})[acct] || {})[plat] || null; }
/* a reason in a few words: 'not connected', 'not readable: needs user.info.stats', 'not read (HTTP 403 ...)' */
const whyShort = e => { const t = String(e || 'no number'); return /^not /.test(t) ? t.split(' (')[0] : 'not read (' + t.split(': ').slice(0, 2).join(': ').slice(0, 60) + ')'; };
/* a count the owner typed on the page (settings.json followers_typed) where the platform cannot be read yet */
const typedFor = (acct, plat) => (((D.settings.followers_typed || {})[acct] || {})[plat]) || null;
function typedOut() {
  const out = {};
  for (const [a, by] of Object.entries(D.settings.followers_typed || {})) out[a] = Object.fromEntries(Object.entries(by || {}).map(([k, t]) => [k, t && t.n != null ? t.n : null]));
  return out;
}
function typedBox(acct, plat, label) {
  const t = typedFor(acct, plat);
  const n = numInput('typed-' + acct + '-' + plat, t ? t.n : '', 1, v => {
    D.settings.followers_typed = D.settings.followers_typed || {};
    D.settings.followers_typed[acct] = Object.assign({}, D.settings.followers_typed[acct] || {}, {[plat]: {n: Math.round(v), at: D.today}});
  });
  n.setAttribute('aria-label', label);
  return el('label', {class: 'typed'}, [label, n]);
}
/* this month's gain of a follower count, from the snapshots */
function folGain(acct, plat) {
  const r = (((D.month || {}).followers || {})[acct] || {})[plat];
  return r && r.gain != null ? r : null;
}
function folWords(f, typed) {
  if ((!f || f.followers == null) && typed && typed.n != null)
    return {n: typed.n, typed: true, words: 'typed by you ' + fday(String(typed.at || D.today)) + (f && f.error ? ' (' + whyShort(f.error) + ')' : '')};
  if (!f) return {n: null, words: 'not read yet: Update now reads it'};
  if (f.followers == null) return {n: null, words: f.hidden ? 'hidden on the channel' : whyShort(f.error)};
  const when = f.at ? 'as of ' + stamp(f.at) : '';
  return {n: f.followers, words: [when, f.field ? 'from ' + f.field : '', f.error ? 'the last try: ' + whyShort(f.error) : ''].filter(Boolean).join(', ')};
}
function meter(label, have, need, gain) {
  const n = Math.max(0, have || 0), done = n >= need;
  const g = gain ? ` · ${gain.gain >= 0 ? '+' : ''}${fmtN(gain.gain)} this month` : '';
  return el('div', {class: 'meter'}, [
    el('span', {}, label),
    el('span', {class: 'x'}, `${fmtN(n)} of ${fmtN(need)}` + (done ? ' · met' : ` · ${fmtN(need - n)} to go`) + g),
    el('div', {class: 't'}, el('i', {class: done ? 'done' : null, style: `width:${Math.min(100, n / need * 100).toFixed(1)}%`})),
  ]);
}
function inBox(acct, plat) {
  const on = !!(((D.settings.eligible || {})[acct] || {})[plat]);
  const box = el('input', {type: 'checkbox', 'aria-label': 'In the ' + PN[plat].name + ' program'});
  box.checked = on;
  box.addEventListener('change', () => {
    D.settings.eligible = D.settings.eligible || {};
    D.settings.eligible[acct] = Object.assign({}, D.settings.eligible[acct] || {}, {[plat]: box.checked});
    saveSettings();
  });
  return el('label', {class: 'in'}, [box, 'In it']);
}
/* channel numbers (what the platform counts), whatever Count by says */
function chanPosts(acct, plat, days) {
  const cut = Date.now() / 1000 - days * 86400;
  return D.posts.filter(p => p.account === acct && p.platform === plat && (!days || p.ts >= cut));
}
function gateState(a) {
  const yt = fol(a.id, 'youtube'), tt = fol(a.id, 'tiktok');
  const ytViews = sum(chanPosts(a.id, 'youtube', 90), p => p.views), ttViews = sum(chanPosts(a.id, 'tiktok', 30), p => p.views);
  const ytMet = !!yt && yt.followers != null && yt.followers >= GATES.youtube.subs && ytViews >= GATES.youtube.views;
  const ttN = tt && tt.followers != null ? tt.followers : (typedFor(a.id, 'tiktok') || {}).n;
  const ttMet = ttN != null && ttN >= GATES.tiktok.subs && ttViews >= GATES.tiktok.views && !(a.max && a.max <= 60);
  return {yt, tt, ytViews, ttViews, met: (ytMet ? 1 : 0) + (ttMet ? 1 : 0), of: 2, ytMet, ttMet};
}
function renderPrograms() {
  const accts = D.accounts.filter(a => acctOk(a.id));
  $('#prog').replaceChildren(...accts.map(a => {
    const g = gateState(a);
    const yw = folWords(g.yt), tw = folWords(g.tt, typedFor(a.id, 'tiktok')), fw = folWords(fol(a.id, 'facebook')), iw = folWords(fol(a.id, 'instagram'));
    const ttRead = !!g.tt && g.tt.followers != null;      /* TikTok's sign-in can read the count: nothing to type */
    const ytVids = chanPosts(a.id, 'youtube', 0).length;
    const tt30 = chanPosts(a.id, 'tiktok', 30), long = tt30.filter(p => p.dur > 60).length;
    const fb60 = chanPosts(a.id, 'facebook', 60), fbPlays = sum(fb60, p => p.views);
    const gate = (plat, kids) => el('div', {class: 'gate'}, [el('div', {class: 'gh'}, [el('span', {class: 'nm'}, PN[plat].name), el('span', {class: 'pg'}, GATES[plat].program), plat === 'instagram' ? null : inBox(a.id, plat)]), ...kids]);
    const S = t => el('p', {class: 's'}, t), W = t => el('p', {class: 'w'}, t);
    return el('div', {}, [
      el('h3', {}, a.name),
      gate('youtube', [
        yw.n == null ? S('Subscribers: ' + yw.words + '.') : meter('Subscribers', yw.n, GATES.youtube.subs, folGain(a.id, 'youtube')),
        meter('Shorts views, 90 days', g.ytViews, GATES.youtube.views),
        yw.n != null ? S(yw.words + '. Views: clips posted in the last 90 days, every play counted.') : S('Views: clips posted in the last 90 days, every play counted.'),
        ytVids ? null : S('No public videos on this channel yet.'),
        g.ytMet ? W('Gate met: apply in YouTube Studio, then tick In it.') : null,
        Date.now() < Date.UTC(2027, 1, 1) ? S('From Feb 1 2027 the Shorts gate is 20M views in 90 days.') : null,
      ]),
      gate('tiktok', [
        tw.n == null ? S('Followers: ' + tw.words + '.') : meter('Followers', tw.n, GATES.tiktok.subs, tw.typed ? null : folGain(a.id, 'tiktok')),
        ttRead ? null : typedBox(a.id, 'tiktok', tw.typed ? 'Followers in the TikTok app now' : 'Type the follower count from the TikTok app'),
        meter('Views, 30 days', g.ttViews, GATES.tiktok.views),
        tw.n != null ? S(tw.words + '.') : null,
        a.max && a.max <= 60 ? W(`Clips here run ${Math.round(a.min)}-${Math.round(a.max)}s. Only clips over 60s pay, so TikTok pays $0 at this length.`)
          : tt30.length ? S(`${long} of ${tt30.length} TikTok posts in 30 days are over 60s (the ones that can pay).`)
          : S(`Clips here run ${Math.round(a.min)}-${Math.round(a.max)}s` + (a.min > 60 ? ', over 60s, so they can pay once the gate is met.' : '.')),
        g.ttMet ? W('Gate met: join Creator Rewards in TikTok Studio, then tick In it.') : null,
      ]),
      gate('facebook', [
        S('Invite only: check Professional Dashboard > Monetization.' + (fw.n != null ? ` Followers ${fmtN(fw.n)}, ${fw.words}.` : ` Followers: ${fw.words}.`)),
        S(`${fb60.length === 1 ? '1 reel' : fb60.length + ' reels'} and ${fmtN(fbPlays)} plays in 60 days` + (fb60.length ? `, ${fmtN(fbPlays / fb60.length)} a reel.` : '.')),
      ]),
      gate('instagram', [S('Bonuses are invite only.' + (iw.n != null ? ` Followers ${fmtN(iw.n)}, ${iw.words}.` : ` Followers: ${iw.words}.`))]),
    ]);
  }));
}
function renderCompare() {
  const end = dnum(D.today), inDays = (p, n) => dnum(p.day) > end - n;
  const rows = D.accounts.map(a => ({name: a.name, a, ps: P.filter(p => p.account === a.id && ui.plats.includes(p.platform))}));
  if (rows.length > 1) rows.push({name: 'All', ps: P.filter(p => ui.plats.includes(p.platform)), both: true});
  const head = ['', 'Views 7d', '30d', 'All', 'This month', 'Posts', 'Views a post', 'If monetized (est.)', 'Actual', 'Gates cleared'];
  const mg = (D.month || {}).gains || {};
  $('#cmp').replaceChildren(
    el('thead', {}, el('tr', {}, head.map(h => el('th', {}, h)))),
    el('tbody', {}, rows.map(r => {
      const all = sum(r.ps, p => p.views), g = r.a ? gateState(r.a) : null;
      return el('tr', {class: r.both ? 'both' : null}, [
        el('td', {}, r.name),
        el('td', {}, fmtN(sum(r.ps.filter(p => inDays(p, 7)), p => p.views))),
        el('td', {}, fmtN(sum(r.ps.filter(p => inDays(p, 30)), p => p.views))),
        el('td', {}, fmtN(all)),
        el('td', {}, (D.month || {}).base_at ? fmtN(sum(r.ps, p => mg[p.id] || 0)) : '-'),
        el('td', {}, r.ps.length.toLocaleString('en-US')),
        el('td', {}, fmtN(r.ps.length ? all / r.ps.length : 0)),
        el('td', {class: 'est'}, money(sum(r.ps, earn))),
        el('td', {}, money(sum(r.ps, actual))),
        el('td', {}, g ? `${g.met} of ${g.of}` : ''),
      ]);
    })));
  $('#cmp-note').textContent = (countBy() === 'content' ? 'Counted by content: a clip counts for the account whose content it is. ' : 'Counted by channel: a clip counts for the channel it is on. ') +
    'Views 7d and 30d are the views of the clips that went up in those days. This month is the views gained since the month\'s first read (a clip that went up this month counts whole). Gates: YouTube Partner Program and TikTok Creator Rewards.';
}

/* ------------------------------------------------------------------ freshness, Update now */
function stamp(iso) {
  const d = new Date(iso); if (isNaN(d)) return '';
  const t = d.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
  return new Date().toDateString() === d.toDateString() ? 'today ' + t : d.toLocaleDateString('en-US', {month: 'short', day: 'numeric'}) + ', ' + t;
}
function renderFresh() {
  $('#fresh').textContent = D.reading ? 'Reading the numbers now...' : D.read_at ? 'Numbers read ' + stamp(D.read_at) : 'Numbers not read yet';
  $('#update').disabled = !!D.reading;
  const box = $('#note'), f = D.failed || [];
  if (!D.error && !f.length) { box.hidden = true; return; }
  const by = new Map();
  for (const x of f) if (x.account) { if (!by.has(x.account)) by.set(x.account, []); by.get(x.account).push(x.platform); }
  const and = a => a.length > 1 ? a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1] : a[0];
  const parts = [...by.entries()].map(([a, ps]) => a + ' ' + and(ps));
  const head = D.error ? 'The last read stopped before it finished.'
    : parts.length ? 'Could not read the numbers for ' + parts.join('; ') + '. Those are from the read before.' : f.map(x => x.why).join(' ');
  const why = [...(D.error ? [D.error] : []), ...f.filter(x => x.account).map(x => `${x.account} ${x.platform}: ${x.why}`)];
  box.replaceChildren(head + ' ', ...(why.length ? [el('details', {}, [el('summary', {}, 'Why'), el('ul', {}, why.map(t => el('li', {}, t)))])] : []));
  box.hidden = false;
}
async function update(auto) {
  $('#update').disabled = true;
  try {
    const r = await fetch('/act', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Token': TOKEN}, body: JSON.stringify({op: 'stats-update'})});
    let d; try { d = await r.json(); } catch (e) { d = {ok: false, message: 'The page sent an answer it could not read. Reload it.'}; }
    if (r.status === 403 && d.stale) { toast('This tab was open before the page restarted. Reloading...'); setTimeout(() => location.reload(), 900); return; }
    if (!auto || !(r.ok && d.ok)) toast(d.message || 'That did not work.', !(r.ok && d.ok));
  } catch (e) { toast('Could not reach the review page (it may be restarting). Try again in a few seconds.', true); }
  load();
}
$('#update').addEventListener('click', () => update(false));

/* ------------------------------------------------------------------ all of it */
function render() {
  if (PAGE_ACCT) ui.acct = PAGE_ACCT;
  if (!D.accounts.some(a => a.id === ui.acct)) ui.acct = 'all';
  AN = Object.fromEntries(D.accounts.map(a => [a.id, a.name]));
  P = viewPosts();
  renderFilters();
  const {cur, prev, b} = slice();
  last = {cur, b};
  renderFresh();
  renderKpis(cur, prev);
  renderChart(cur, b);
  renderRank();
  renderTop(cur);
  renderPlatforms(cur);
  renderHeat(cur);
  renderSlots(cur);
  renderWorking(cur);
  renderWatch(cur);
  renderFactory();
  renderReasons();
  renderMoney(cur);
  renderPrograms();
  renderCompare();
}
async function load() {
  clearTimeout(pollT);
  try {
    const r = await fetch('/stats/data', {cache: 'no-store'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    const finished = D && D.reading && !d.reading;
    const changed = !D || d.stamp !== D.stamp;
    D = d;
    if (changed) render(); else renderFresh();
    if (SERIES && changed) loadExtras();
    if (finished) toast(d.error || (d.failed || []).length ? 'Some numbers could not be read: see the note at the top.' : 'Numbers updated.', !!(d.error || (d.failed || []).length));
    const age = d.read_at ? Date.now() - new Date(d.read_at) : Infinity;
    if (!autoAsked && !d.reading && age > 3600e3) { autoAsked = true; update(true); return; }
  } catch (e) {
    $('#fresh').textContent = 'Could not reach the review page. It may be restarting.';
  }
  pollT = setTimeout(load, D && D.reading ? 4000 : 60000);
}
$('#top-more').addEventListener('click', () => { ui.topN += 15; renderTop(last.cur); });
$('#top-q').addEventListener('input', ev => { ui.q = ev.target.value; ui.topN = 10; renderTop(last.cur); });
$('#csv').addEventListener('click', () => {
  const cell = v => { let s = String(v == null ? '' : v); if (/^[=+\-@]/.test(s)) s = "'" + s; return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  const head = ['account', 'platform', 'title', 'url', 'posted', 'seconds', 'format', 'source', 'views', 'likes', 'comments', 'shares', 'rights', 'channel',
    'watched_pct', 'watch_seconds', 'skipped_3s_pct', 'gone_by_3s_curve_pct', 'engaged_pct', 'reach', 'saved', 'replays'];
  const rows = [...last.cur].sort((a, b) => b.ts - a.ts).map(p => [AN[p.account] || p.account, PN[p.platform] ? PN[p.platform].name : p.platform, p.title, p.url, p.day + ' ' + p.hm, p.dur, p.fmt, p.host, p.views, p.likes, p.comments, p.shares, p.rights, AN[p.chan] || p.chan,
    p.watch_pct, p.watch_s, p.skip_pct, p.left_3s_pct, p.engaged_pct, p.reach, p.saved, p.replays].map(cell).join(','));
  const a = el('a', {href: URL.createObjectURL(new Blob([[head.join(','), ...rows].join('\n') + '\n'], {type: 'text/csv'})), download: 'clip-stats-' + D.today + '.csv'});
  document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
});
let rz = 0;
new ResizeObserver(() => { cancelAnimationFrame(rz); rz = requestAnimationFrame(() => { if (D && last) renderChart(last.cur, last.b); }); }).observe($('#chart'));
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
load();
})();
"""


def _series_built() -> bool:
    """Whether checkpoints.py is built here (the Top clips rows then open how each post grew; /stats/series)."""
    try:
        from . import registry as REG
        return REG.present("checkpoints")
    except Exception:  # noqa: BLE001  (the tab never goes down over it)
        return False


def page(tok: str, base_css: str, tabs: str, switch: str = "", acct: str = "all") -> str:
    """The Stats tab. `switch` is the header's account switch: with it, the page counts that account (or all of them)
    and its own account buttons step aside, so the choice is made in one place."""
    js = (JS.replace("__TOKEN__", json.dumps(tok)).replace("__ACCT__", json.dumps(acct if switch else ""))
          .replace("__SERIES__", json.dumps(_series_built())))
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Clip Review · Stats</title><style>{base_css}{CSS}</style></head><body class=stats>"
            f"<header><h1>Clip Review</h1>{tabs}{switch}<span class=grow></span>"
            '<span class="fresh" id="fresh" aria-live="polite">Loading the numbers...</span>'
            '<button type="button" id="update">Update now</button></header>'
            f'<main>{BODY}</main><div id="toast" role="status" aria-live="polite" hidden></div>'
            f'<div class="tip" id="tip" hidden></div><script>{js}</script></body></html>')
