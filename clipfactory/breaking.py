"""THE BREAKING DESK: today's breaking political moment cut and posted the same day (Andrew, 2026-10-28: "want some
scouts to be running during the day as well, because then we can get like current events, like breaking news", then
"Yes, build the daytime breaking desk here, proofs first"; the plan he approved: the next news slot, then his flip
order, a bumped clip to the bench and not the bin, never a slot already posted).

Before it, the scout (scout.py, hourly 05:40 to 22:40) marked a find "breaking" and nothing acted on it in the day:
the first clip of a story was the next night's, 9 to 26 hours later.

THE ALARM (no Claude). After every scout run (scout.run calls on_scout) and every 15 minutes from 06:00 to 19:00
(`factory.py breaking quick`: the scout's YouTube channels read through their RSS feeds, and its news feeds; no
yt-dlp, no searches, nothing written to the scout's board), each of today's finds that is
  - breaking by the scout's own rule (scout._breaking: 6 hours old at most, a moment and not a panel about it, 100K
    views an hour, 3 channels on its story, or 3 times its channel's usual),
  - a video naming someone in US politics (the scout's people), never a re-upload, still "new" on the scout's board,
  - a fight (the scout's heat, scout.FIGHT) or a story BIG_STORY outlets carry,
is an alarm, one per story: state/<account>/breaking/<YYYY-MM-DD>.json. A new one gets a Mac notice; Clip Review's
Today band shows each open one in red. A story the desk took or passed on never alarms again that day.

THE DESK (a Claude task every 30 minutes, 06:20 to 17:50; prompts/politics-breaking-desk.md). `breaking check` first:
in a second it says STOP (exit 3) when no alarm is open, MAX_A_DAY breaking clips are on the board, Claude's week is
low, or no slot is left a clip cut now could reach. Otherwise it cuts the alarm's own moment from the first-hand copy
(the member's, committee's or White House's own channel, C-SPAN or the network itself; never a re-upload or a panel
about it), every check on, and `breaking place` puts it on the day's board:

THE SLOT: the next news slot (news 1 09:50, news 2 13:35), then the plan's flip order, Outburst, Flashback, Hearing,
Sit-down (THE DAILY 8, accounts.json). Never a fight (06:30, 07:50). A slot qualifies when it is at least LEAD away,
nothing of it went out on any platform (YouTube holds a signed clip two hours ahead: that counts as out), no post is
being sent, it holds no breaking clip already, and the day rule (rotation.py over the boards and Publish/
schedule.json, bench.politics_rotation) allows the new clip there.

THE BUMP: the clip in that slot leaves it with no verdict and nothing on the kill list (nobody said it was bad). Its
whole record stays on the slot under "bumped", and the bench takes it for another day (bench.add, bumped=...) when the
bench's own checks allow it (a cast a person checked, no word that dates it, room on the bench); otherwise it stays
in its folder and the line says why. Publish/schedule.json gets the new clip in that slot (a copy of the file first,
in state/<account>/breaking/backups), day_folder.py delivers it, and collect takes it as an unsigned draft: it signs
itself 30 minutes ahead (autosign.py) when its cast allows, else it waits for Andrew's Approve. `breaking undo` puts
the old clip back while the new one has not gone out.

THE EXTRA (the default since 2026-10-28; --bump is the old way above): a slot of its own on top of the eight, at the
minute it is placed (Andrew, 2026-10-29: "The moment it's made, it gets shipped. Not at a certain spot, not in the
future"). It does not sign itself while a platform cut it needs is still to come (cuts_wait, CUTS_WAIT at the most: a
platform with no cut of its own gives up on it). `breaking go`, once the desk made its cuts, ends that wait and starts
the poster: it signs itself, with every check autosign makes, and each platform posts it at once.

    factory.py breaking status [account] [--date D]
    factory.py breaking quick politics [--force]                      launchd, every 15 minutes
    factory.py breaking check politics [--date D]                     exit 0 GO, 3 STOP
    factory.py breaking place politics --date D --job J --slug S --find ID [--slot HH:MM] [--dry-run]
    factory.py breaking go politics --date D --slot HH:MM             its cuts are made: it posts now
    factory.py breaking pass politics --date D --find ID --why ...    the desk looked and does not cut it
    factory.py breaking undo politics --date D --slot HH:MM
    factory.py breaking replay politics --date D                      the dry run: what the day would have done
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import config as C
from . import store

MAX_A_DAY = 2                       # breaking clips on one day's board
LEAD = timedelta(minutes=60)        # a breaking clip goes in at least this long before its slot (autosign signs at 30)
RENDER = timedelta(minutes=45)      # from `check` saying GO to `place`: the source, the render, the platform cuts
BIG_STORY = 5                       # outlets on one story: big news, even with no fight in it
SHORT_S = 20 * 60                   # a source this long or shorter is quick to cut: an alarm's face prefers one
QUICK_FROM, QUICK_UNTIL = "06:00", "19:00"
QUICK_GAP = timedelta(minutes=10)   # the quick check skips a turn this soon after the scout's own reading
DESK_RUNS = ("06:20", "17:50", 30)  # the desk task's first and last run and its minutes apart (the replay's clock)
STOP = 3                            # check's exit code when there is nothing to cut (usage.STOP's)
# THE DAILY 8 (accounts.json, 2026-10-28): the eight slots in clock order, each a lane
DAILY8 = ("fight 1", "fight 2", "news 1", "sit-down", "news 2", "hearing", "flashback", "outburst")
NEWS = ("news 1", "news 2")
FLIP = ("outburst", "flashback", "hearing", "sit-down")     # Andrew's flip order; the fights never flip
# an alarm: new (not asked yet) -> asked (Andrew has the question on his phone) -> yes (his yes: cut it) -> cut;
# or passed (his no, or the desk's), or past (no longer breaking, or no answer in ASK_TTL)
NEW, ASKED, YES, CUT, PASSED, PAST = "new", "asked", "yes", "cut", "passed", "past"
ASK_TTL = timedelta(hours=3)        # a question with no answer this long is past: the story has moved on
# THE EXTRA (Andrew, 2026-10-28: "We'll make 8 to 10 ... always 8 ... if there's some crazy breaking news, then we'll
# have extra"): a breaking clip gets a slot of its own on top of the eight, at the minute it is placed (Andrew,
# 2026-10-29: "I don't want them put in a certain slot ... The moment it's made, it gets shipped")
CUTS_WAIT = timedelta(minutes=30)   # the most it waits for its platform cuts before it signs itself all the same
LAST_EXTRA = "21:30"
# a slot as the bump leaves it: these stay, everything else is the clip's and leaves with it
SKELETON = ("slot", "publish_at", "format", "status", "notes", "spare", "bumped", "breaking", "extra")
RANK = {YES: 0, ASKED: 1, NEW: 2, PAST: 3}  # which of two alarms of one story keeps its key
CHANNEL_ID = re.compile(r"/channel/(UC[\w-]{22})")
_PAGE_ID = (re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'),
            re.compile(r'"externalId":"(UC[\w-]{22})"'),
            re.compile(r'<meta itemprop="identifier" content="(UC[\w-]{22})"'))


# ------------------------------------------------------------------------------------------------------ the file
def path(acct: C.Account, day: date) -> Path:
    return store.path(acct.slug, "breaking", f"{day.isoformat()}.json")


def read(acct: C.Account, day: date) -> dict:
    d = store.read(path(acct, day), {})
    return d if isinstance(d, dict) else {}


def placed(acct: C.Account, day: date) -> List[dict]:
    """The breaking clips placed on the day's board (an undone one is not)."""
    return [p for p in read(acct, day).get("placed") or [] if isinstance(p, dict) and not p.get("undone")]


def open_alarms(acct: C.Account, day: date) -> List[dict]:
    """The day's open alarms, best first."""
    al = (read(acct, day).get("alarms") or {}).values()
    return sorted((a for a in al if isinstance(a, dict) and a.get("status") == NEW),
                  key=lambda a: -float(a.get("score") or 0))


# ------------------------------------------------------------------------------------------------------ the alarm
def _roundup(title: str) -> bool:
    """A show of many stories (the Daily 8 board's own test, daily8.roundup): never a moment to cut."""
    try:
        from . import daily8 as D8
        return bool(D8.roundup(title))
    except Exception:  # noqa: BLE001  (no Daily 8 module: no roundup test)
        return False


def alarm_why(f: dict) -> str:
    """Why a scout find is an alarm ('' when it is not): breaking by the scout's rule, a video naming someone in US
    politics, not a re-upload, a panel about the moment or a roundup show, still new on the scout's board, and a fight
    or a big story."""
    from . import scout as S
    if f.get("kind") != "video" or not f.get("breaking") or (f.get("status") or "new") != "new":
        return ""
    if not f.get("people") or f.get("derivative") or f.get("rights") == "reupload" or _roundup(f.get("title") or ""):
        return ""
    if float(f.get("heat") or 0) >= S.FIGHT:
        return "a fight"
    n = int(f.get("story_outlets") or 0)
    return f"{n} outlets on the story" if n >= BIG_STORY else ""


def group_why(hits: List[dict]) -> str:
    """Why a story alarms, over all its alarming finds (its face may be a first-hand copy that alone would not)."""
    whys = [alarm_why(f) for f in hits]
    if "a fight" in whys:
        return "a fight"
    n = max((int(f.get("story_outlets") or 0) for f, w in zip(hits, whys) if w), default=0)
    return f"{n} outlets on the story" if n else next((w for w in whys if w), "")


def _rep_key(f: dict) -> tuple:
    """How well a find stands for its story: a first-hand short copy (the member's, committee's, White House's or
    federal channel, C-SPAN), then any short one, then the score."""
    from . import scout as S
    dur = f.get("duration")
    short = dur in (None, "") or float(dur) <= SHORT_S
    return ((f.get("rights") or "") in S.PRIMARY_FEEDS and short, short, round(float(f.get("score") or 0), 3))


def _key_people(people, common: set) -> set:
    return {str(p) for p in people or []} - common


def first_hand(every: List[dict], kp: set) -> List[dict]:
    """The first-hand copies of a story the scout filed apart (its stories are drawn from title words): today's
    videos from a member's, committee's, White House or federal channel or C-SPAN that name one of its key people."""
    from . import scout as S
    if not kp:
        return []
    return [f for f in every if f.get("kind") == "video" and (f.get("rights") or "") in S.PRIMARY_FEEDS
            and kp & set(f.get("people") or []) and (f.get("status") or "new") == "new"
            and f.get("age_h") is not None and float(f["age_h"]) <= S.BREAKING_H and not _roundup(f.get("title") or "")]


def _facts(f: dict) -> dict:
    return {"title": str(f.get("title") or ""), "channel": str(f.get("channel") or ""), "url": str(f.get("url") or ""),
            "people": list(f.get("people") or []), "why": alarm_why(f), "heat": f.get("heat"),
            "vph": f.get("vph_live") or f.get("vph"), "views": f.get("views"), "outlets": f.get("story_outlets"),
            "score": round(float(f.get("score") or 0), 2), "lane": f.get("lane"), "rights": f.get("rights"),
            "minutes": round(float(f["duration"]) / 60, 1) if f.get("duration") not in (None, "") else None,
            "looks": list(f.get("looks") or []), "published": f.get("published"), "age_h": f.get("age_h"),
            "rep": f.get("id"), "rep_key": list(_rep_key(f))}


def _n(v) -> str:
    v = float(v or 0)
    return f"{v / 1e6:.1f}M" if v >= 1e6 else (f"{v / 1e3:.0f}K" if v >= 1e3 else f"{v:.0f}")


def push_words(a: dict) -> str:
    """An alarm in one line, the way the notice and the push say it: who, what, where it is, how fast."""
    who = " and ".join(str(p).title() for p in (a.get("people") or [])[:2])
    pace = f", {_n(a['vph'])} views an hour" if a.get("vph") else ""
    return f"{who}: {str(a.get('title') or '')[:110]} ({a.get('channel') or '?'}{pace})"


def take_reading(cfg: C.Config, acct: C.Account, finds: Dict[str, dict], how: str,
                 now: Optional[datetime] = None, notify: bool = True) -> List[str]:
    """One reading of today's finds (the scout's board, or the quick check's) into today's alarms: one per story. The
    scout's stories are re-drawn every run, so a story is also the same story as an earlier alarm that names one of
    its people (anyone but the names in a tenth of the day's finds: the President is in half of them). Its face is the
    first-hand short copy (_rep_key). A story the desk took or passed on never alarms again today; an open alarm the
    reading no longer finds breaking is past. A new alarm gets a Mac notice."""
    now = now or cfg.now()
    day = now.date()
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    every = [f for f in (finds or {}).values() if isinstance(f, dict) and f.get("id")]
    stories: Dict[str, List[dict]] = {}
    for f in every:
        stories.setdefault(str(f.get("story", f["id"])), []).append(f)
    named: Dict[str, int] = {}
    for f in every:
        for p in set(f.get("people") or []):
            named[p] = named.get(p, 0) + 1
    common = {p for p, n in named.items() if n > max(5, len(every) // 10)}
    fresh: List[dict] = []
    with store.edit(path(acct, day), dict) as d:
        d.setdefault("account", acct.slug)
        d.setdefault("date", day.isoformat())
        alarms = d.setdefault("alarms", {})
        live = set()
        for fs in stories.values():
            hits = [f for f in fs if alarm_why(f)]
            if not hits:
                continue
            ids = {f["id"] for f in fs}
            kp = _key_people([p for f in hits for p in f.get("people") or []], common)
            same = [k for k, a in alarms.items() if k in ids or a.get("rep") in ids
                    or kp & _key_people(a.get("people_all"), common)]
            if any(alarms[k].get("status") in (CUT, PASSED) for k in same):
                continue                    # the desk took this story, or passed on it
            hits += [f for f in first_hand(every, kp) if f["id"] not in {h["id"] for h in hits}]
            best = max(hits, key=_rep_key)
            if same:
                k = min(same, key=lambda x: (RANK.get(alarms[x].get("status"), 9), alarms[x].get("first_seen") or ""))
                for x in same:
                    if x != k and alarms[x].get("status") in (NEW, PAST):
                        alarms.pop(x)       # one story, one alarm: an asked or approved one keeps its key
                a = alarms[k]
                if a.get("status") == PAST or _rep_key(best) >= tuple(a.get("rep_key") or (False, False, -1.0)):
                    a.update(_facts(best))
                if a.get("status") == PAST:
                    a["status"] = NEW       # breaking again (a second wave of views)
            else:
                k = best["id"]
                a = alarms[k] = {"id": k, "status": NEW, "first_seen": stamp, "by": how, **_facts(best)}
                fresh.append(a)
            a["why"] = group_why(hits)
            a["people_all"] = sorted(set(a.get("people_all") or []) | {p for f in hits for p in f.get("people") or []})
            a["also"] = [{"id": f["id"], "title": str(f.get("title") or "")[:100], "channel": f.get("channel"),
                          "url": f.get("url"), "rights": f.get("rights"),
                          "minutes": round(float(f["duration"]) / 60, 1) if f.get("duration") not in (None, "") else None}
                         for f in sorted(hits, key=_rep_key, reverse=True) if f["id"] != a.get("rep")][:4]
            a["last_seen"] = stamp
            live.add(k)
        for k, a in alarms.items():
            if a.get("status") == NEW and k not in live:
                a.update(status=PAST, past_at=stamp)
            elif a.get("status") == ASKED and a.get("asked_at") and \
                    now - datetime.fromisoformat(a["asked_at"]) > ASK_TTL:
                a.update(status=PAST, past_at=stamp, why_past=f"no answer in {_mins(ASK_TTL)}")
        d.update(updated=stamp, last_reading=how)
        n_open = sum(1 for a in alarms.values() if a.get("status") == NEW)
    lines = [f"breaking: {n_open} open alarm(s)" + ("" if not fresh else f", {len(fresh)} new: " + "; ".join(
        f"{a['title'][:70]} ({a['channel']}, {a['why']})" for a in fresh))]
    if fresh and notify:
        _notice(f"{acct.name}: breaking", push_words(fresh[0])
                + (f" (+{len(fresh) - 1} more)" if len(fresh) > 1 else ""))
    return lines


def on_scout(cfg: C.Config, acct: C.Account, board: dict) -> List[str]:
    """scout.run's hook, after it writes the day's board: the Politics's alarms from it."""
    if acct.maker != "politics":
        return []
    return take_reading(cfg, acct, board.get("finds") or {}, "scout")


# ------------------------------------------------------------------------------------------------------ the quick check
def channel_ids(acct: C.Account, scfg: dict, resolve: bool = True) -> Dict[str, str]:
    """{a scout channel's page URL: its UC... id}: read off the URL, else looked up once on the channel's page (its
    canonical link) and kept in state/<account>/breaking/channel-ids.json; a lookup that failed is tried again the next
    day."""
    p = store.path(acct.slug, "breaking", "channel-ids.json")
    known = store.read(p, {}) or {}
    today = date.today().isoformat()
    out: Dict[str, str] = {}
    todo: List[str] = []
    for ch in scfg.get("youtube_channels") or []:
        u = str(ch.get("url") or "")
        m = CHANNEL_ID.search(u)
        if m:
            out[u] = m.group(1)
        elif (known.get(u) or {}).get("id"):
            out[u] = known[u]["id"]
        elif resolve and u and (known.get(u) or {}).get("tried") != today:
            todo.append(u)
    if todo:
        with store.edit(p, dict) as kn:
            for u in todo:
                cid = _lookup(u)
                kn[u] = {"id": cid, "tried": today}
                if cid:
                    out[u] = cid
    return out


def _lookup(url: str) -> str:
    """A channel page's own id (UC...), '' when it cannot be read (offline: never)."""
    if os.environ.get("CLIP_FACTORY_OFFLINE"):
        return ""
    from . import scout as S
    page = re.sub(r"/(videos|streams|shorts|featured)/?$", "", url)
    try:
        req = urllib.request.Request(page, headers={"User-Agent": S.UA, "Accept-Language": "en-US,en"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read(2_000_000).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001  (a page that does not answer: tried again tomorrow)
        return ""
    for rx in _PAGE_ID:
        m = rx.search(html)
        if m:
            return m.group(1)
    return ""


def feeds_for(acct: C.Account, scfg: dict, resolve: bool = True) -> Tuple[List[dict], List[str]]:
    """The quick check's sources: each scout channel's RSS feed (YouTube's 15 newest uploads, with their views), then
    the scout's own RSS list. And the channels with no feed id yet."""
    ids = channel_ids(acct, scfg, resolve=resolve)
    feeds: List[dict] = []
    unknown: List[str] = []
    for ch in scfg.get("youtube_channels") or []:
        cid = ids.get(str(ch.get("url") or ""))
        if not cid:
            unknown.append(str(ch.get("name") or ch.get("url")))
            continue
        feeds.append({"name": ch["name"], "url": f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}",
                      "rights": ch.get("rights"), "weight": ch.get("weight", 1.0)})
    have = {f["url"] for f in feeds}
    feeds += [dict(r) for r in scfg.get("rss") or [] if r.get("url") not in have]
    return feeds, unknown


def quick(cfg: C.Config, acct: C.Account, now: Optional[datetime] = None, force: bool = False) -> List[str]:
    """THE 15-MINUTE CHECK (launchd com.clipfactory.breaking-quick): the scout's channels through their RSS feeds and
    its news feeds, no yt-dlp and no searches; ranked by the scout's own rank() together with the rest of today's
    scout board (a story counts every outlet), then into today's alarms. Nothing is written to the scout's board:
    the nightly reads it as the scout left it."""
    from . import scout as S
    now = now or cfg.now()
    hm = now.strftime("%H:%M")
    if acct.maker != "politics":
        return [f"{acct.name}: the breaking desk is the Politics's"]
    if not force and not QUICK_FROM <= hm < QUICK_UNTIL:
        return [f"{acct.name} breaking quick: {hm} is outside {QUICK_FROM}-{QUICK_UNTIL}; nothing read"]
    day = now.date()
    board = S.read_board(acct, day)
    if not force and board.get("updated"):
        try:
            if now - datetime.fromisoformat(board["updated"]) < QUICK_GAP:
                return [f"{acct.name} breaking quick: the scout read every source at "
                        f"{datetime.fromisoformat(board['updated']).astimezone(cfg.tz()):%H:%M}; nothing to add"]
        except ValueError:
            pass
    scfg = S.load(acct.slug)
    try:  # the learner's approved factors, as scout.run reads them
        from . import playbook as PB
        scfg = PB.apply_to_scout(acct, scfg)
    except Exception:  # noqa: BLE001
        pass
    feeds, unknown = feeds_for(acct, scfg)
    t0 = time.time()
    raw, failed, n = S.gather(acct.slug, dict(scfg, youtube_channels=[], youtube_searches=[], rss=feeds), acct,
                              workers=8, ytdlp=False)
    prev = board.get("finds") or {}
    prev_y = S.read_board(acct, day - timedelta(days=1)).get("finds") or {}
    got = {r["id"] for r in raw}
    for r in raw:                       # a feed row knows no length or live flag: the scout's board does
        old = prev.get(r["id"]) or {}
        for k in ("duration", "was_live", "channel_slug", "also_in"):
            if r.get(k) in (None, False, [], "") and old.get(k):
                r[k] = old[k]
    stale: Dict[str, dict] = {}
    for k, f in prev.items():           # the rest of the board, so a story counts every outlet the scout found
        if k not in got and isinstance(f, dict):
            g = copy.deepcopy(f)
            g.pop("views", None)        # no new reading for what the feeds did not read
            raw.append(g)
            stale[k] = f
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    finds, _stories, _wave = S.rank(raw, scfg, acct, float(scfg.get("hours", 36)), prev, prev_y, S.decisions(acct),
                                    stamp)
    S.kill_listed(acct, finds)
    for f in finds:                     # not read now: the scout's own word on it stands
        old = stale.get(f["id"])
        if old:
            for k in ("breaking", "score", "vph_live", "views", "seen"):
                f[k] = old.get(k)
    said = take_reading(cfg, acct, {f["id"]: f for f in finds}, "quick", now=now)
    head = (f"{acct.name} breaking quick: {len(got)} videos and headlines from {n - len(failed)} of {n} feeds "
            f"({time.time() - t0:.0f}s, no yt-dlp)")
    if unknown:
        head += f"; {len(unknown)} channel(s) with no feed id yet: {', '.join(unknown[:4])}"
    return [head] + said


# ------------------------------------------------------------------------------------------------------ the slots
def lanes(acct: C.Account) -> Dict[str, str]:
    """THE DAILY 8's lane of each slot time (accounts.json's eight slots in clock order); {} when the account does
    not post eight a day."""
    slots = sorted(acct.slots)
    return dict(zip(slots, DAILY8)) if len(slots) == len(DAILY8) else {}


def order(acct: C.Account, board: dict) -> List[str]:
    """The slots a breaking clip may take, first choice first: the news slots in clock order, then the flip order.
    A fight is never on it."""
    ln = lanes(acct)
    on = set((board or {}).get("slots") or {})
    news = [k for k in sorted(ln) if ln[k] in NEWS and k in on]
    return news + [k for lane in FLIP for k in sorted(ln) if ln[k] == lane and k in on]


def _out(s: dict) -> List[str]:
    return sorted(p for p, r in (s.get("outlets") or {}).items()
                  if isinstance(r, dict) and (r.get("post_id") or r.get("video_id")))


def _mins(td: timedelta) -> str:
    m = int(td.total_seconds() // 60)
    return f"{m // 60}h{m % 60:02d}" if m >= 120 else f"{m} min"


def why_not(cfg: C.Config, acct: C.Account, day: date, board: dict, k: str, at: datetime) -> str:
    """Why slot k cannot take a breaking clip placed at `at` ('' when it can): too close, a maker in it, out on a
    platform (YouTube's upload two hours ahead included), being sent, or a breaking clip in it already."""
    from . import poster as P
    s = ((board or {}).get("slots") or {}).get(k)
    if not isinstance(s, dict):
        return "not a slot on this board"
    when = cfg.slot_time(day, k)
    if when - at < LEAD:
        return f"{_mins(when - at)} away, under {_mins(LEAD)}" if when > at else "its time has passed"
    if s.get("status") == B.MAKING:
        return "a maker is filling it right now"
    out = _out(s)
    if out:
        return "already out on " + ", ".join(out)
    fly = P.in_flight(acct, day, k)
    if fly:
        return f"{fly} is sending it right now"
    if s.get("breaking"):
        return "it holds a breaking clip already"
    return ""


def _strip(acct: C.Account, s: dict) -> dict:
    """Take the clip off the slot: every field but the slot's own (SKELETON) leaves it, and is returned with the
    status and segment it had. The slot is open again."""
    rec = {k: v for k, v in s.items() if k not in SKELETON}
    rec.update(status=s.get("status"), format=s.get("format"))
    for k in list(s):
        if k not in SKELETON:
            s.pop(k)
    s["status"] = B.OPEN
    if acct.maker == "politics":
        s["format"] = "any"             # collect writes the new clip's segment
    return rec


def _sched(acct: C.Account) -> dict:
    from . import bench as BN
    return BN._sched(acct)


def day_rule(cfg: C.Config, acct: C.Account, day: date, board: dict, k: str, job: str, slug: str,
             jobs_dir: Optional[Path] = None) -> List[str]:
    """The day rule's new FAIL lines with this clip in slot k and the slot's own clip gone (empty: it allows it).
    jobs_dir: a proof job outside the account's Jobs folder (looked at beside every real job)."""
    from . import bench as BN
    b2 = copy.deepcopy(board)
    if k in (b2.get("slots") or {}):
        _strip(acct, b2["slots"][k])
    if not jobs_dir:
        return BN.politics_rotation(cfg, acct, day, k, {"job": job, "slug": slug}, b2, _sched(acct))
    with tempfile.TemporaryDirectory(prefix="clipfactory-breaking-jobs-") as td:
        real = acct.account_dir / "Jobs"
        for j in (real.iterdir() if real.is_dir() else []):
            os.symlink(j, Path(td) / j.name)
        if not (Path(td) / job).exists():
            os.symlink(Path(jobs_dir) / job, Path(td) / job)
        return BN.politics_rotation(cfg, acct, day, k, {"job": job, "slug": slug}, b2, _sched(acct), jobs=Path(td))


def pick(cfg: C.Config, acct: C.Account, day: date, board: dict, at: datetime, job: str = "", slug: str = "",
         only: Optional[str] = None, jobs_dir: Optional[Path] = None) -> Tuple[Optional[str], List[str]]:
    """The slot a breaking clip placed at `at` takes, and a line for each slot looked at (the day rule only with a
    clip to ask it about)."""
    ln = lanes(acct)
    said: List[str] = []
    for k in ([C.normal_slot(only)] if only else order(acct, board)):
        why = why_not(cfg, acct, day, board, k, at)
        if not why and ln.get(k) in ("fight 1", "fight 2"):
            why = "a fight slot never moves"
        if not why and job and slug:
            try:
                fails = day_rule(cfg, acct, day, board, k, job, slug, jobs_dir)
            except Exception as e:  # noqa: BLE001  (a rule nobody could ask is a no)
                why = f"the day rule could not be asked ({e})"
            else:
                if fails:
                    why = "the day rule says no: " + "; ".join(fails[:2])
        s = (board.get("slots") or {}).get(k) or {}
        held = f", holds {s.get('slug')} ({s.get('status')})" if s.get("slug") else f" ({s.get('status', '?')})"
        said.append(f"  {k} {ln.get(k, '?')}{held}: {why or 'TAKES IT'}")
        if not why:
            return k, said
    return None, said


# ------------------------------------------------------------------------------------------------------ the desk
def _usage() -> Optional[dict]:
    try:
        from . import usage as U
        return U.current()
    except Exception:  # noqa: BLE001  (the meter never stops a run)
        return None


def alarms_in(acct: C.Account, day: date, *statuses: str) -> List[dict]:
    """The day's alarms in these states, best first."""
    al = (read(acct, day).get("alarms") or {}).values()
    return sorted((a for a in al if isinstance(a, dict) and a.get("status") in statuses),
                  key=lambda a: -float(a.get("score") or 0))


def extra_time(cfg: C.Config, acct: C.Account, day: date, board: dict, at: datetime) -> Optional[str]:
    """THE EXTRA's slot: the minute of `at` itself, so the poster sends it the moment it is signed (the nearest free
    minute before it when a slot of the day has that one: never later than `at`). None on another day or after
    LAST_EXTRA."""
    taken = set((board or {}).get("slots") or {})
    t = at.replace(second=0, microsecond=0)
    if t.date() != day or t > cfg.slot_time(day, LAST_EXTRA):
        return None
    while f"{t:%H:%M}" in taken and t.date() == day:
        t -= timedelta(minutes=1)
    return f"{t:%H:%M}" if t.date() == day else None


def plain_what(a: dict) -> str:
    """The news in one short line when the desk gave none: the video's title without its quote marks, cut at 90."""
    t = re.sub(r"[\"\u201c\u201d]", "", str(a.get("title") or "")).strip()
    return t if len(t) <= 90 else t[:89].rstrip() + "\u2026"


def ask_words(a: dict, what: str = "") -> str:
    """The push that asks Andrew, under 200 characters: the news in a line, then the question (Andrew, 10.28:
    "very simple and short ... Would you like the post or not? Yes or no")."""
    q = " Post it? Yes or no."
    head = "BREAKING: "
    body = (what or plain_what(a)).strip().rstrip(".") + "."
    room = 200 - len(head) - len(q)
    return head + (body if len(body) <= room else body[:room - 1].rstrip() + "\u2026") + q


FONTS = Path.home() / ".claude/skills/viral-clip-engine/assets/fonts"


def _picture(a: dict) -> Optional[bytes]:
    """The moment's own picture: its YouTube thumbnail (the big one, else the standard one)."""
    vid = a.get("rep") or a.get("id")
    if not re.fullmatch(r"[\w-]{11}", str(vid or "")) or "youtu" not in str(a.get("url") or "youtube"):
        return None
    for size in ("maxresdefault", "hqdefault"):
        try:
            with urllib.request.urlopen(f"https://i.ytimg.com/vi/{vid}/{size}.jpg", timeout=10) as r:
                b = r.read()
            if len(b) > 3000:       # YouTube's grey "no thumbnail" is about 1 KB
                return b
        except Exception:  # noqa: BLE001  (no picture is a card without one)
            continue
    return None


def ask_card(acct: C.Account, day: date, a: dict, what: str = "", at: str = "") -> Path:
    """One picture for the phone: how the clip would look (the Politics strap, a red BREAKING tab, the headline over
    the moment's own picture), then where it is from and when it would post. state/<acct>/breaking/cards/."""
    import io
    from PIL import Image, ImageDraw, ImageFont
    W, H, TOP, PH = 1080, 1240, 330, 608
    ink, sub, red = (245, 243, 238), (150, 150, 146), (200, 16, 46)     # no gold on a Politics card (Andrew 10.30)

    def font(w, px):
        try:
            return ImageFont.truetype(str(FONTS / f"Clip{w}.ttf"), px)
        except OSError:
            return ImageFont.load_default()

    im = Image.new("RGB", (W, H), (11, 11, 11))
    d = ImageDraw.Draw(im)
    d.text((48, 52), "POLITICS", font=font(600, 26), fill=ink)
    d.text((48, 84), "POLITICS", font=font(600, 26), fill=ink)
    d.line((178, 54, 178, 112), fill=(70, 70, 70), width=2)
    d.text((196, 52), str(a.get("channel") or ""), font=font(500, 26), fill=sub)
    d.text((196, 84), f"{day:%B} {day.day} {day:%Y}".upper(), font=font(500, 24), fill=sub)
    f = font(600, 26)
    tw = d.textlength("BREAKING", font=f)
    d.rectangle((48, 138, 48 + tw + 28, 178), fill=red)
    d.text((62, 142), "BREAKING", font=f, fill=ink)
    who = " AND ".join(str(p).upper() for p in (a.get("people") or [])[:2])
    d.text((48 + tw + 46, 142), who, font=f, fill=ink)
    f, lines, cur = font(600, 58), [], ""
    for w in (what or plain_what(a)).upper().split():
        if d.textlength(f"{cur} {w}".strip(), font=f) > W - 96 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    if len(lines) > 2:
        lines = [lines[0], lines[1].rstrip(".,") + "\u2026"]
    for i, ln in enumerate(lines):
        d.text((48, 196 + i * 64), ln, font=f, fill=ink)
    pic = _picture(a)
    if pic:
        p = Image.open(io.BytesIO(pic)).convert("RGB")
        s = max(W / p.width, PH / p.height)
        p = p.resize((round(p.width * s), round(p.height * s)))
        x, y = (p.width - W) // 2, (p.height - PH) // 2
        im.paste(p.crop((x, y, x + W, y + PH)), (0, TOP))
    else:
        d.rectangle((0, TOP, W, TOP + PH), fill=(28, 28, 28))
        d.text((48, TOP + PH // 2 - 20), "no picture for this one", font=font(500, 34), fill=sub)
    d.text((48, TOP + PH + 44), f"From {a.get('channel') or '?'}", font=font(500, 34), fill=sub)
    if at:
        d.text((48, TOP + PH + 96), "Posts the moment it's made, an extra post", font=font(500, 34), fill=sub)
    d.text((48, H - 140), "POST IT?", font=font(600, 64), fill=ink)
    d.text((48 + d.textlength("POST IT?  ", font=font(600, 64)), H - 140), "YES  OR  NO", font=font(600, 64),
           fill=ink)
    out = store.path(acct.slug, "breaking", "cards", f"{day.isoformat()}-{a.get('id')}.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, quality=88)
    return out


def ask_message(a: dict, what: str = "", at: str = "") -> List[str]:
    """The desk's last words in the Claude app, under the picture: four short lines and the question."""
    who = " and ".join(str(p).title() for p in (a.get("people") or [])[:2]) or "?"
    return ["\U0001f534 BREAKING",
            f"\U0001f4f0 {(what or plain_what(a)).strip().rstrip('.')}.",
            f"\U0001f3ac The clip: {who}, from {a.get('channel') or '?'}.",
            "\U0001f550 Posts the moment it's made, an extra post.",
            "\U0001f449 Post it? Yes or No"]


def _line(a: dict) -> List[str]:
    return [f"  {a['id']}  score {a.get('score')}  {a.get('why')}  {a.get('title')}",
            f"      {a.get('channel')} ({a.get('rights')}, scout lane: {a.get('lane')}) {a.get('url')}  "
            f"people: {', '.join(a.get('people') or [])}  {_n(a.get('vph'))} views an hour"]


def check(cfg: C.Config, acct: C.Account, day: Optional[date] = None,
          now: Optional[datetime] = None) -> Tuple[int, List[str]]:
    """The desk's first step, under a second, writing nothing. Its first line:
      GO CUT <id>   Andrew said yes to this alarm: cut it now
      GO ASK <id>   a new alarm: ask Andrew (`breaking ask` gives the push), then wait for his yes or no
      STOP: why     (exit STOP) nothing to do: no alarm, a question waiting for him, two clips placed, no room left
                    today, or Claude's week low."""
    now = now or cfg.now()
    day = day or now.date()
    if acct.maker != "politics":
        return STOP, [f"STOP: the breaking desk works for the Politics only, not for {acct.name}"]
    st = _usage()
    if st and st.get("level") in ("low", "reserve", "out"):
        return STOP, [f"STOP: Claude's week is {st['level']} ({float(st.get('used') or 0):.0f}% used): no breaking cut "
                      f"this run"]
    done = placed(acct, day)
    if len(done) >= MAX_A_DAY:
        return STOP, [f"STOP: {len(done)} breaking clips are on today's board ({', '.join(p['slot'] for p in done)}); "
                      f"the most is {MAX_A_DAY}"]
    board = B.read(acct, day)
    if not board:
        return STOP, [f"STOP: no board for {day}"]
    k = extra_time(cfg, acct, day, board, now + RENDER)
    yes, new, asked = alarms_in(acct, day, YES), alarms_in(acct, day, NEW), alarms_in(acct, day, ASKED)
    if not k:
        return STOP, [f"STOP: too late today for a breaking clip (one cut now would post after {LAST_EXTRA})"]
    if yes:
        a = yes[0]
        return 0, [f"GO CUT {a['id']}: Andrew said yes ({a.get('yes_words') or 'yes'}). A clip cut now posts the "
                   f"moment it is made (about {k}, an extra slot)."] + _line(a)
    if new:
        a = new[0]
        return 0, [f"GO ASK {a['id']}: a new alarm. Run `breaking ask`, it pushes to his phone by itself (Yes / No buttons); "
                   f"also send its words with PushNotification, then wait for Andrew's yes or no. Cut now, it would post the moment it is made (about {k}, an extra slot)."] + _line(a) + \
            [ln for b in new[1:3] for ln in _line(b)]
    if asked:
        return STOP, ["STOP: waiting for Andrew's yes or no on " + "; ".join(
            f"{a['id']} ({a.get('title', '')[:60]}, asked {str(a.get('asked_at') or '')[11:16]})" for a in asked)]
    upd = read(acct, day).get("updated")
    return STOP, ["STOP: no open breaking alarm" + (f" (last reading {upd})" if upd else "")]


def ask(cfg: C.Config, acct: C.Account, day: date, find: str, by: str = "", what: str = "",
        now: Optional[datetime] = None, card: bool = True) -> Tuple[bool, List[str]]:
    """Mark the alarm asked and give the ask: line 1 the push (PushNotification), then PICTURE: the card
    (SendUserFile), then MESSAGE: the desk's last words, which Andrew answers yes or no."""
    now = now or cfg.now()
    with store.edit(path(acct, day), dict) as d:
        a = (d.get("alarms") or {}).get(find)
        if not a:
            return False, [f"  {find}: no such alarm on {day}"]
        if a.get("status") not in (NEW, ASKED):
            return False, [f"  {find}: {a.get('status')}, nothing to ask"]
        a.update(status=ASKED, asked_at=now.isoformat(timespec="seconds"), asked_by=by or "breaking desk")
        if what:
            a["what"] = what[:160]
        a = dict(a)
    try:
        board = B.read(acct, day)
        at = (extra_time(cfg, acct, day, board, now + RENDER) or "") if board else ""
    except Exception:  # noqa: BLE001  (no time is a message without one)
        at = ""
    lines = [ask_words(a, what)]
    pic = None
    if card:
        try:
            pic = ask_card(acct, day, a, what, at)
            lines.append(f"PICTURE: {pic}")
        except Exception as e:  # noqa: BLE001  (no card still asks)
            lines.append(f"PICTURE: none ({e})")
    lines.append("PHONE: " + phone_ask(acct, day, a, what, at, pic))
    return True, lines + ["MESSAGE:"] + ask_message(a, what, at)


def phone_ask(acct: C.Account, day: date, a: dict, what: str = "", at: str = "", pic: Optional[Path] = None) -> str:
    """The ask on Andrew's phone (clipfactory/phone.py, 10.29: Claude's PushNotification never reached it): the
    line, the card, Yes / No buttons. A button press comes back through `phone poll` as `breaking yes|no`."""
    from . import phone
    msg = (what or plain_what(a)).strip().rstrip(".") + "." + \
        "\nPosts the moment it's made, an extra post.\nPost it?"
    ref = f"{acct.slug} {day.isoformat()} {a['id']}"
    ok, why = phone.send(f"BREAKING \u00b7 {acct.name}", msg, image=pic, tags="rotating_light",
                         buttons=[("Yes, post it", f"breaking yes {ref}"), ("No", f"breaking no {ref}")])
    return f"sent ({why})" if ok else f"not sent ({why})"


def phone_answer(cfg: C.Config, words: List[str]) -> List[str]:
    """A Yes / No button from the phone: ["yes"|"no", account, day, alarm id]."""
    from . import phone
    if len(words) != 4 or words[0] not in ("yes", "no"):
        return [f"  phone: not a breaking answer {words}"]
    verb, slug, d, find = words
    acct, day = cfg.account(slug), date.fromisoformat(d)
    if verb == "yes":
        out = say_yes(cfg, acct, day, find, by="Andrew (phone)", words="Yes, post it (the phone button)")
        phone.send("\u2705 Got it: cutting it", "The breaking desk cuts it on its next run (within 30 minutes) and "
                   "it posts by itself.", priority="default")
    else:
        out = pass_on(cfg, acct, day, find, "Andrew said no on his phone", by="Andrew (phone)")
    return out


def say_yes(cfg: C.Config, acct: C.Account, day: date, find: str, by: str, words: str = "") -> List[str]:
    """Andrew's yes to an alarm (his own words kept): the desk cuts it, and it posts by itself."""
    with store.edit(path(acct, day), dict) as d:
        a = (d.get("alarms") or {}).get(find)
        if not a:
            return [f"  {find}: no such alarm on {day}"]
        if a.get("status") in (CUT, PASSED):
            return [f"  {find}: {a.get('status')} already"]
        a.update(status=YES, yes_by=by, yes_words=(words or "yes")[:300], yes_at=B.utcnow())
    return [f"  {find}: yes from {by}: the desk cuts it"]


def _job_clip(jd: Path, slug: str) -> Optional[Path]:
    hits = sorted(p for p in jd.glob(f"clips/[0-9][0-9]-{slug}.mp4") if not re.search(r" \d+\.mp4$", p.name))
    return hits[0] if hits else None


def _set_schedule(acct: C.Account, day: date, k: str, entry: Optional[dict]) -> Optional[dict]:
    """Publish/schedule.json with `entry` ({job, slug}) in the day's slot k (None: the slot comes out); a copy of the
    file goes to state/<account>/breaking/backups first. Returns what the slot held."""
    p = acct.account_dir / "Publish" / "schedule.json"
    sched = json.loads(p.read_text()) if p.exists() else {}
    if p.exists():
        bk = store.path(acct.slug, "breaking", "backups")
        bk.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, bk / f"schedule-{datetime.now():%Y%m%d-%H%M%S-%f}.json")
    days = sched.setdefault(day.isoformat(), {})
    was = days.get(k)
    if entry is None:
        days.pop(k, None)
    else:
        days[k] = entry
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".breaking-tmp")
    tmp.write_text(json.dumps(sched, indent=1) + "\n")
    os.replace(tmp, p)
    return was if isinstance(was, dict) else None


def deliver(acct: C.Account, day: date) -> str:
    """Publish/day_folder.py for the day, the nightly's own delivery (it copies every scheduled clip of the day into
    its 'HHMM slug' folder and never moves or deletes one). '' when it ran, else why not."""
    script = acct.account_dir / "Publish" / "day_folder.py"
    if not script.is_file():
        return f"no {script}"
    r = subprocess.run(["/usr/bin/python3", str(script), day.isoformat()], cwd=str(script.parent),
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return "day_folder.py: " + (((r.stderr or r.stdout).strip().splitlines()) or ["failed"])[-1][:200]
    return ""


def _delivered(acct: C.Account, day: date, k: str, slug: str) -> Optional[Path]:
    from . import collect as K
    return next((f for sk, f in K.find_delivered(acct, day) if sk == k and f.name.split(" ", 1)[1] == slug), None)


def place(cfg: C.Config, acct: C.Account, day: date, job: str, slug: str, find: str = "", by: str = "",
          slot: Optional[str] = None, dry_run: bool = False, now: Optional[datetime] = None,
          jobs_dir: Optional[Path] = None, bump: bool = False) -> Tuple[bool, List[str]]:
    """A rendered breaking clip (the job's clips/NN-<slug>.mp4) onto the day's board: THE EXTRA by default, a slot of
    its own at extra_time() (8 to 10 a day); bump=True, one of the eight instead (pick(): the news slots, then the
    flip order), its clip to the bench. dry_run: says what would happen and writes nothing (a proof job outside the
    Jobs folder, jobs_dir, only dry)."""
    from . import bench as BN
    from . import collect as K
    now = now or cfg.now()
    who = by or "breaking desk"
    if acct.maker != "politics":
        return False, [f"  REFUSED: the breaking desk works for the Politics only, not for {acct.name}"]
    if jobs_dir and not dry_run:
        return False, ["  REFUSED: a job outside the account's Jobs folder (--jobs-dir) is a proof; --dry-run only"]
    done = placed(acct, day)
    if len(done) >= MAX_A_DAY and not dry_run:
        return False, [f"  REFUSED: {len(done)} breaking clips are on the board already; the most is {MAX_A_DAY}"]
    jd = (Path(jobs_dir) if jobs_dir else acct.account_dir / "Jobs") / job
    clip = _job_clip(jd, slug)
    if clip is None:
        return False, [f"  REFUSED: no rendered clip clips/NN-{slug}.mp4 in {jd}"]
    if slug in {s.get("slug") for s in ((B.read(acct, day) or {}).get("slots") or {}).values()}:
        return False, [f"  REFUSED: {slug} is on the {day} board already (one clip, one slot a day)"]
    board = B.read(acct, day)
    if not board:
        return False, [f"  REFUSED: no board for {day}"]
    if not bump:
        return _place_extra(cfg, acct, day, board, job, slug, find, who, slot, dry_run, now, jobs_dir)
    k, lines = pick(cfg, acct, day, board, now, job=job, slug=slug, only=slot, jobs_dir=jobs_dir)
    if not k:
        return False, lines + ["  REFUSED: no slot takes it"]
    alarm = (read(acct, day).get("alarms") or {}).get(find) or {}
    s0 = board["slots"][k]
    old = s0.get("slug") if s0.get("status") in (B.READY, B.APPROVED, B.SCHEDULED) else ""
    old_entry = (_sched(acct).get(day.isoformat()) or {}).get(k) or {}
    old_job = str(old_entry.get("job") or "") if old_entry.get("slug") == old else ""
    lane = lanes(acct).get(k, "?")
    if dry_run:
        lines.append(f"  --dry-run: {slug} would go in {k} ({lane})"
                     + (f", and {old} ({s0.get('status')}) would leave it for the bench" if old else ", an empty slot"))
        if old and s0.get("source_folder"):
            ok, bl = BN.add(cfg, acct, s0["source_folder"], slug=old, job=old_job or None, by=who, check=True,
                            kind="bumped", bumped={"day": day.isoformat(), "slot": k, "slug": old}, now=now)
            lines += [f"  the bench, asked: {x.strip()}" for x in bl[:3]]
        return True, lines + ["  --dry-run: nothing written"]

    stamp = B.utcnow()
    entry: Optional[dict] = None
    with B.edit(cfg, acct, day, create=False) as bd:
        s = bd["slots"][k]
        why = why_not(cfg, acct, day, bd, k, cfg.now())
        if why:
            return False, lines + [f"  REFUSED at the bump: {k} {why}"]
        from . import signoff as SO
        SO._clear_extras(s)             # its first comment and poll go with it (as Make again does)
        rec = _strip(acct, s)
        if rec.get("slug"):
            entry = {"at": stamp, "by": who, "slug": rec["slug"], "job": old_job, "status": rec.get("status"),
                     "for": {"find": find, "slug": slug, "title": alarm.get("title", "")}, "fields": rec}
            s.setdefault("bumped", []).append(entry)
        s["breaking"] = {"find": find, "title": alarm.get("title", ""), "url": alarm.get("url", ""), "job": job,
                         "slug": slug, "at": stamp, "by": who}
        B.note(s, f"breaking desk: {slug} takes this slot"
               + (f"; {rec['slug']} leaves it for the bench (bumped, no verdict)" if rec.get("slug") else ""))
    err = ""
    try:
        _set_schedule(acct, day, k, {"job": job, "slug": slug})
        err = deliver(acct, day)
        folder = None if err else _delivered(acct, day, k, slug)
        if not err and folder is None:
            err = f"day_folder.py made no '{k.replace(':', '')} {slug}' folder"
        if not err:
            lines += K.collect(cfg, acct, day, only=[k])
            s2 = ((B.read(acct, day) or {}).get("slots") or {}).get(k) or {}
            if s2.get("slug") != slug or s2.get("status") != B.READY:
                err = f"collect did not take it: {k} is {s2.get('status')} ({B.last_note(s2)})"
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    if err:
        return False, lines + [f"  FAILED: {err}"] + _put_back(cfg, acct, day, k, slug, entry, old_entry, who, err)

    bench = "the slot was empty"
    if entry and entry["fields"].get("source_folder"):
        ok, bl = BN.add(cfg, acct, entry["fields"]["source_folder"], slug=entry["slug"], job=old_job or None,
                        why=f"bumped from {day} {k} by a breaking clip ({slug}); never posted", kind="bumped",
                        by=who, bumped={"day": day.isoformat(), "slot": k, "slug": entry["slug"]})
        ident = next((i["id"] for i in BN.items(acct) if i.get("slug") == entry["slug"]
                      and (i.get("bumped_from") or {}).get("day") == day.isoformat()), "") if ok else ""
        bench = (f"on the bench ({ident})" if ok else
                 "kept in its folder, the bench said no: " + (bl[0].split("nothing written:", 1)[-1].strip() if bl else "?"))
        with B.edit(cfg, acct, day, create=False) as bd:
            for e in bd["slots"][k].get("bumped") or []:
                if e.get("at") == stamp:
                    e.update(bench=bench, bench_id=ident)
        lines.append(f"  {entry['slug']}: {bench}")
    with store.edit(path(acct, day), dict) as d:
        a = (d.setdefault("alarms", {})).get(find)
        if a:
            a.update(status=CUT, slot=k, slug=slug, cut_at=stamp)
        d.setdefault("placed", []).append({"slot": k, "lane": lane, "job": job, "slug": slug, "find": find,
                                           "title": alarm.get("title", ""), "at": stamp, "by": who,
                                           "bumped": entry["slug"] if entry else "", "bench": bench})
    signs = cfg.slot_time(day, k) - timedelta(minutes=30)
    lines.append(f"  {k} ({lane}): {slug} is in, an unsigned draft; it signs itself about {signs:%H:%M} if its cast "
                 f"allows, else it waits for your Approve")
    _notice(f"{acct.name}: breaking clip in {k}",
            f"{alarm.get('title') or slug}"[:120] + (f". {entry['slug']} went to the bench." if entry else "")
            + f" Signs itself about {signs:%H:%M} unless you Pull it.")
    return True, lines


def _place_extra(cfg: C.Config, acct: C.Account, day: date, board: dict, job: str, slug: str, find: str, who: str,
                 slot: Optional[str], dry_run: bool, now: datetime, jobs_dir: Optional[Path]) -> Tuple[bool, List[str]]:
    """THE EXTRA: a slot of its own for the breaking clip, nothing bumped."""
    from . import collect as K
    k = C.normal_slot(slot) if slot else extra_time(cfg, acct, day, board, now)
    if not k:
        return False, [f"  REFUSED: too late today for a breaking clip (the last goes out at {LAST_EXTRA})"]
    if k in (board.get("slots") or {}):
        return False, [f"  REFUSED: {k} is a slot of the day already; an extra slot needs a time of its own"]
    try:
        fails = day_rule(cfg, acct, day, board, k, job, slug, jobs_dir)
    except Exception as e:  # noqa: BLE001  (a rule nobody could ask is a no)
        return False, [f"  REFUSED: the day rule could not be asked ({e})"]
    if fails:
        return False, [f"  REFUSED: the day rule says no to {slug} at {k}: " + "; ".join(fails[:2])]
    alarm = (read(acct, day).get("alarms") or {}).get(find) or {}
    lines = [f"  {k}: an extra slot (the day's slots: {', '.join(sorted(board.get('slots') or {}))})"]
    if dry_run:
        return True, lines + [f"  --dry-run: {slug} would go out at {k}, an extra slot, the moment its cuts are made; "
                              "nothing written"]
    stamp = B.utcnow()
    with B.edit(cfg, acct, day, create=False) as bd:
        if k in bd["slots"]:
            return False, lines + [f"  REFUSED: {k} was taken a moment ago"]
        bd["slots"][k] = {"slot": k, "publish_at": cfg.slot_time(day, k).isoformat(),
                          "format": "any" if acct.maker == "politics" else "", "status": B.OPEN, "notes": [],
                          "extra": True,
                          "breaking": {"find": find, "title": alarm.get("title", ""), "url": alarm.get("url", ""),
                                       "job": job, "slug": slug, "at": stamp, "by": who,
                                       "cuts_by": (now + CUTS_WAIT).isoformat(timespec="seconds"),
                                       "yes": {x: alarm.get(f"yes_{x}") for x in ("by", "words", "at")}}}
        bd["slots"] = dict(sorted(bd["slots"].items()))
        B.note(bd["slots"][k], f"breaking desk: an extra slot for {slug}")
    err = ""
    try:
        _set_schedule(acct, day, k, {"job": job, "slug": slug})
        err = deliver(acct, day)
        if not err and _delivered(acct, day, k, slug) is None:
            err = f"day_folder.py made no '{k.replace(':', '')} {slug}' folder"
        if not err:
            lines += K.collect(cfg, acct, day, only=[k])
            s2 = ((B.read(acct, day) or {}).get("slots") or {}).get(k) or {}
            if s2.get("slug") != slug or s2.get("status") != B.READY:
                err = f"collect did not take it: {k} is {s2.get('status')} ({B.last_note(s2)})"
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    if err:
        out = [f"  FAILED: {err}"]
        try:
            _set_schedule(acct, day, k, None)
        except Exception as e:  # noqa: BLE001
            out.append(f"  Publish/schedule.json NOT put back ({e}): take {k} out by hand")
        with B.edit(cfg, acct, day, create=False) as bd:
            bd["slots"].pop(k, None)
        f = _delivered(acct, day, k, slug)
        if f is not None:
            f.rename(f.with_name(f"_not placed {f.name}"))
        return False, lines + out + [f"  {k}: the extra slot taken off the board again"]
    with store.edit(path(acct, day), dict) as d:
        a = (d.setdefault("alarms", {})).get(find)
        if a:
            a.update(status=CUT, slot=k, slug=slug, cut_at=stamp)
        d.setdefault("placed", []).append({"slot": k, "lane": "extra", "job": job, "slug": slug, "find": find,
                                           "title": alarm.get("title", ""), "at": stamp, "by": who, "bumped": "",
                                           "bench": "nothing bumped (an extra slot)"})
    from . import autosign as AS
    s2 = ((B.read(acct, day) or {}).get("slots") or {}).get(k) or {}
    waits = cuts_wait(acct, s2, now)
    held = "" if waits else AS.why_not(cfg, acct, s2, now)
    if waits:
        said, short = f"{waits}: make its cuts, then `breaking go`, and it posts that moment", "posts once its cuts are made"
    elif held:
        said, short = f"it waits for your Approve in Clip Review ({held})", "waits for your Approve"
    else:
        from . import poster as P
        said, short = f"it needs no cut of its own and posts now ({P.kick(cfg)})", "posting now"
    lines.append(f"  {k} (extra): {slug} is in; {said}")
    _notice(f"{acct.name}: breaking clip, {short}", f"{alarm.get('title') or slug}"[:120])
    return True, lines


def cuts_wait(acct: C.Account, s: dict, now: datetime) -> str:
    """Why a breaking clip does not sign itself yet ('' when nothing holds it here): an extra slot whose desk is still
    making a platform cut it needs (outlets.needs_cut), until `breaking go` or its cuts_by (CUTS_WAIT after it was
    placed), so no platform gives up on it for want of its cut. autosign.why_not asks this."""
    brk = s.get("breaking") if isinstance(s, dict) else None
    if not isinstance(brk, dict) or not s.get("extra") or brk.get("go_at") or not brk.get("cuts_by"):
        return ""
    try:
        until = datetime.fromisoformat(str(brk["cuts_by"]))
    except ValueError:
        return ""
    if now >= until:
        return ""
    from . import outlets as O
    need = [O.NAMES.get(p, p) for p in O.auto_platforms(acct) if O.needs_cut(acct, s, p)]
    if not need:
        return ""
    return f"it waits for its {' and '.join(need)} cut, {until:%H:%M} at the most"


def go(cfg: C.Config, acct: C.Account, day: date, k: str, by: str = "",
       now: Optional[datetime] = None) -> Tuple[bool, List[str]]:
    """The desk's last step, its platform cuts made: the breaking clip goes now. The wait for its cuts ends and the
    poster starts at once (poster.kick): it signs itself, with every check autosign makes, and every platform posts
    it, its time being now. A cast nobody checked still waits for Andrew's Approve; a cut that never came is said."""
    from . import autosign as AS
    from . import outlets as O
    from . import poster as P
    now = now or cfg.now()
    k = C.normal_slot(k)
    who = by or "breaking desk"
    with B.edit(cfg, acct, day, create=False) as bd:
        s = (bd.get("slots") or {}).get(k)
        if not isinstance(s, dict) or not isinstance(s.get("breaking"), dict):
            return False, [f"  REFUSED: {k} holds no breaking clip on {day}"]
        s["breaking"].update(go_at=now.isoformat(timespec="seconds"), go_by=who)
        B.note(s, f"breaking desk: go, its cuts are made ({who})")
        s = copy.deepcopy(s)
    lines = []
    uncut = [O.NAMES.get(p, p) for p in O.auto_platforms(acct) if O.needs_cut(acct, s, p)]
    if uncut:
        lines.append(f"  {k}: no {' or '.join(uncut)} cut on it, so {' and '.join(uncut)} gives up on it (make the cut, "
                     f"`collect`, then `breaking go` again)")
    if s.get("status") in B.SIGNED:
        lines.append(f"  {k}: signed already, posting now")
    else:
        held = AS.why_not(cfg, acct, s, now)
        if held:
            return True, lines + [f"  {k}: waits for Andrew's Approve in Clip Review ({held})"]
        lines.append(f"  {k}: signs itself and posts now")
    return True, lines + [f"  {P.kick(cfg)}"]


def _put_back(cfg: C.Config, acct: C.Account, day: date, k: str, slug: str, entry: Optional[dict],
              old_entry: dict, who: str, why: str) -> List[str]:
    """A place that failed halfway: the slot, Publish/schedule.json and the delivery as they were."""
    out = []
    try:
        _set_schedule(acct, day, k, old_entry or None)
        out.append(f"  Publish/schedule.json: {k} back to {old_entry.get('slug') or 'nothing'}")
    except Exception as e:  # noqa: BLE001
        out.append(f"  Publish/schedule.json NOT put back ({e}): set {k} by hand")
    with B.edit(cfg, acct, day, create=False) as bd:
        s = bd["slots"][k]
        _strip(acct, s)
        s.pop("breaking", None)
        if entry:
            for e in s.get("bumped") or []:
                if e.get("at") == entry["at"]:
                    e["restored"] = {"at": B.utcnow(), "by": who, "why": why}
            s.update(entry["fields"])
        B.note(s, f"breaking desk: {slug} did not go in ({why}); " + (f"{entry['slug']} is back" if entry else "open"))
    out.append(f"  {k}: " + (f"{entry['slug']} back as it was" if entry else "open again"))
    f = _delivered(acct, day, k, slug)
    if f is not None:                   # never collected later into another open slot
        f.rename(f.with_name(f"_not placed {f.name}"))
        out.append(f"  {f.name}: renamed '_not placed ...' (collect leaves it alone)")
    return out


def undo(cfg: C.Config, acct: C.Account, day: date, k: str, by: str = "") -> Tuple[bool, List[str]]:
    """The breaking clip out of slot k and the clip it bumped back in, while nothing of the breaking clip went out
    and no person signed it (the factory's own signature is fine). The bumped clip's bench copy is retired: it is on
    its board again. Its first comment does not come back (the bump took it, as Make again does)."""
    from . import autosign as A
    from . import bench as BN
    from . import poster as P
    k = C.normal_slot(k)
    who = by or "breaking desk"
    with B.edit(cfg, acct, day, create=False) as bd:
        s = bd["slots"].get(k)
        if not isinstance(s, dict) or not s.get("breaking"):
            return False, [f"  {k}: no breaking clip here"]
        out = _out(s)
        if out:
            return False, [f"  {k}: the breaking clip is already out on {', '.join(out)}; Pull it to stop the rest"]
        if P.in_flight(acct, day, k):
            return False, [f"  {k}: a platform is sending it right now; try again in a minute"]
        so = s.get("signoff") or {}
        if s.get("status") in B.SIGNED and so.get("by") and so.get("by") != A.BY:
            return False, [f"  {k}: {so['by']} signed the breaking clip; Pull it first"]
        entry = next((e for e in reversed(s.get("bumped") or []) if not e.get("restored")), None)
        brk = s.get("breaking") or {}
        extra = bool(s.get("extra"))
        if extra:
            bd["slots"].pop(k)          # an extra slot was only ever the breaking clip's
        else:
            _strip(acct, s)
            s.pop("breaking", None)
            if entry:
                s.update(entry["fields"])
                entry["restored"] = {"at": B.utcnow(), "by": who, "why": "breaking desk undo"}
            B.note(s, f"breaking desk undone by {who}: {brk.get('slug')} out"
                   + (f", {entry['slug']} back" if entry else ", the slot open"))
    lines = [f"  {k}: {brk.get('slug')} out" + (", the extra slot off the board" if extra else
                                                 f", {entry['slug']} back as it was" if entry else ", open again")]
    _set_schedule(acct, day, k, {"job": entry["job"], "slug": entry["slug"]} if entry and entry.get("job") else None)
    if entry and entry.get("bench_id"):
        lines += BN.retire(cfg, entry["bench_id"], why=f"breaking desk undo: back in {day} {k}")
    with store.edit(path(acct, day), dict) as d:
        for p in d.get("placed") or []:
            if p.get("slot") == k and p.get("slug") == brk.get("slug") and not p.get("undone"):
                p["undone"] = {"at": B.utcnow(), "by": who}
        a = (d.get("alarms") or {}).get(brk.get("find") or "")
        if a:
            a.update(status=PASSED, why_passed=f"undone by {who}")
    return True, lines


def pass_on(cfg: C.Config, acct: C.Account, day: date, find: str, why: str, by: str = "") -> List[str]:
    """The desk looked at an alarm and does not cut it (no first-hand copy, not a real moment, the board has its
    story): it never alarms again today."""
    with store.edit(path(acct, day), dict) as d:
        a = (d.get("alarms") or {}).get(find)
        if not a:
            return [f"  {find}: no such alarm on {day}"]
        a.update(status=PASSED, why_passed=why[:300], passed_by=by or "breaking desk", passed_at=B.utcnow())
    return [f"  {find}: passed ({why[:120]})"]


# ------------------------------------------------------------------------------------------------------ what it says
def status_lines(cfg: C.Config, acct: C.Account, day: date) -> List[str]:
    d = read(acct, day)
    al = sorted((d.get("alarms") or {}).values(), key=lambda a: (a.get("status") != NEW, -float(a.get("score") or 0)))
    out = [f"{acct.name} breaking desk {day}: {sum(1 for a in al if a.get('status') == NEW)} open alarm(s), "
           f"{len(placed(acct, day))} of {MAX_A_DAY} placed" + (f" (last reading {d['updated']}, {d.get('last_reading')})"
                                                               if d.get("updated") else "")]
    for a in al:
        out.append(f"  {a.get('status', '?'):<6} {a['id']}  {a.get('why')}  {push_words(a)}"
                   + (f"  -> {a['slot']}" if a.get("slot") else "") + (f"  ({a['why_passed']})" if a.get("why_passed") else ""))
    for p in d.get("placed") or []:
        out.append(f"  placed {p['slot']} {p.get('lane')}: {p['slug']}" + (f", bumped {p['bumped']} ({p.get('bench')})"
                                                                         if p.get("bumped") else "")
                   + (" UNDONE" if p.get("undone") else ""))
    return out


def summary(cfg: C.Config, now: datetime, accts) -> List[dict]:
    """Clip Review's Today band: each open alarm in red (two at most, then a count), each breaking clip placed."""
    out: List[dict] = []
    for a in accts or []:
        if getattr(a, "maker", "") != "politics":
            continue
        day = now.astimezone(cfg.tz()).date() if now.tzinfo else now.date()
        al = alarms_in(a, day, NEW, ASKED, YES)
        for x in al[:2]:
            said = {ASKED: "BREAKING, waiting for your yes: ", YES: "BREAKING, being cut: "}.get(x.get("status"),
                                                                                              "BREAKING: ")
            out.append({"key": f"breaking:{x['id']}", "words": said + push_words(x), "level": "bad", "order": 0})
        if len(al) > 2:
            out.append({"key": "breaking:more", "words": f"+{len(al) - 2} more breaking", "level": "bad", "order": 0})
        for p in placed(a, day):
            out.append({"key": f"breaking:placed:{p['slot']}", "level": "warn", "order": 1,
                        "words": f"Breaking clip in {p['slot']}: {(p.get('title') or p['slug'])[:70]}"
                                 + (f" ({p['bumped']} to the bench)" if p.get("bumped") else "")})
    return out


def _notice(title: str, body: str) -> None:
    if os.environ.get("CLIP_FACTORY_OFFLINE"):
        return
    try:
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(body[:220])} with title "
                        f"{json.dumps(title)} sound name \"Glass\""], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ------------------------------------------------------------------------------------------------------ the dry run
def _published(f: dict) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(f.get("published")))
    except ValueError:
        return None


def alarm_from(f: dict, acct: C.Account, scfg: Optional[dict] = None) -> Optional[datetime]:
    """When a find on today's scout board would first have alarmed (None: never). The board keeps the find's views
    at every hourly reading ("seen") and its story as it stands now: it counts from the first reading at which it was
    6 hours old or less and its numbers then (views an hour; outlets and its channel's usual as now) pass the scout's
    breaking rule."""
    from . import scout as S
    if not alarm_why(dict(f, breaking=True)):
        return None
    pub = _published(f)
    if pub is None:
        return None
    floor = float((scfg or {}).get("breaking_vph") or S.BREAKING_VPH.get(acct.maker, 100000))
    for stamp, views in f.get("seen") or []:
        t = datetime.fromisoformat(stamp)
        age = (t - pub).total_seconds() / 3600
        if age > S.BREAKING_H:
            break
        vph = float(views or 0) / max(age, 0.25)
        if vph >= floor or (f.get("story_fresh") or 1) >= 3 or (f.get("outlier") or 0) >= 3:
            return t
    return None


def replay(cfg: C.Config, acct: C.Account, day: date) -> List[str]:
    """THE DRY RUN: today's scout board read the way the alarm and the desk would have read it during the day; it
    writes nothing. One alarm per story (alarm_from); the desk at DESK_RUNS takes the best open alarm, its clip is
    placed RENDER later in the slot pick() would give on the board as it stood (a signed slot is YouTube's from two
    hours before its time), at most MAX_A_DAY; the bench is asked about each bumped clip."""
    from . import bench as BN
    from . import scout as S
    tz = cfg.tz()
    sb = S.read_board(acct, day)
    finds = sb.get("finds") or {}
    try:
        scfg = S.load(acct.slug)
    except SystemExit:
        scfg = {}
    every_f = [f for f in finds.values() if isinstance(f, dict) and f.get("id")]
    named: Dict[str, int] = {}
    for f in every_f:
        for p in set(f.get("people") or []):
            named[p] = named.get(p, 0) + 1
    common = {p for p, n in named.items() if n > max(5, len(every_f) // 10)}
    stories: Dict[str, List[dict]] = {}
    for f in every_f:
        stories.setdefault(str(f.get("story", f["id"])), []).append(f)
    groups: List[dict] = []             # the scout's stories, joined where they name the same person (take_reading)
    for fs in stories.values():
        hits = [(f, alarm_from(f, acct, scfg)) for f in fs]
        hits = [(f, t) for f, t in hits if t is not None]
        if not hits:
            continue
        kp = _key_people([p for f, _ in hits for p in f.get("people") or []], common)
        g = next((g for g in groups if kp & g["kp"]), None)
        if g:
            g["hits"] += hits
            g["kp"] |= kp
        else:
            groups.append({"hits": hits, "kp": kp})
    first, last, every = DESK_RUNS
    t = datetime.combine(day, datetime.strptime(first, "%H:%M").time(), tz)
    end = datetime.combine(day, datetime.strptime(last, "%H:%M").time(), tz)
    alarms = []
    for g in groups:
        until = max(_published(f) + timedelta(hours=S.BREAKING_H) for f, _ in g["hits"])
        at = min(at for _, at in g["hits"])
        held = {f["id"] for f, _ in g["hits"]}
        best = max([f for f, _ in g["hits"]] + [f for f in first_hand(every_f, g["kp"]) if f["id"] not in held
                                               and _published(f) and _published(f) <= until], key=_rep_key)
        if until > t and at <= end + RENDER:    # breaking at some point of the desk's day
            hits = [dict(f, breaking=True) for f, _ in g["hits"]]
            alarms.append({"f": best, "at": at, "until": until, "why": group_why(hits),
                           "facts": dict(_facts(dict(best, breaking=True)), why=group_why(hits))})
    alarms.sort(key=lambda a: a["at"])
    board = copy.deepcopy(B.read(acct, day) or {"slots": {}})
    base = sorted(k for k, s in (board.get("slots") or {}).items() if not B.is_spare(s))
    out = [f"{acct.name} {day}: the breaking desk's dry run on the scout board of "
           f"{datetime.fromisoformat(sb['updated']).astimezone(tz):%H:%M}" if sb.get("updated") else
           f"{acct.name} {day}: no scout board", ""]
    if lanes(acct) and base != sorted(acct.slots):
        board = {"slots": {k: {"slot": k} for k in acct.slots}}
        out[1] = (f"(the {day} board had {len(base)} slots; this run uses the Daily 8's eight, as every board has from "
                  f"2026-10-29)")
        out.append("")
    for a in alarms:
        at = a["at"].astimezone(tz)
        out.append(f"{at:%H:%M}{'' if at.date() == day else at.strftime(' (%m-%d)')}  ALARM  {a['why']}: "
                   f"{push_words(a['facts'])}" + (f", {a['facts']['minutes']} min" if a["facts"].get("minutes") else ""))
    if not alarms:
        return out + ["  no alarm all day"]
    out.append("")
    taken: set = set()
    n = 0
    while t <= end and n < MAX_A_DAY:
        live = [a for a in alarms if a["at"] <= t < a["until"] and a["f"]["id"] not in taken]
        if live:
            a = max(live, key=lambda x: float(x["f"].get("score") or 0))
            taken.add(a["f"]["id"])
            out.append(f"{t:%H:%M}  ASK    your phone: {ask_words(a['facts'])}")
            out.append("       (the dry run takes your yes at once)")
            out.append(f"{t:%H:%M}  CUT    \"{a['facts']['title'][:90]}\" from {a['facts']['channel']} "
                       f"({a['facts']['rights']})")
            at = t + RENDER
            k = extra_time(cfg, acct, day, board, at)
            if not k:
                out.append(f"{at:%H:%M}  too late today for a breaking clip: it waits for the night")
            else:
                out.append(f"{at:%H:%M}  PLACE  an extra slot at {k}; it posts the moment its cuts are made")
                board.setdefault("slots", {})[k] = {"slot": k, "extra": True, "breaking": {"find": a["f"]["id"]}}
                n += 1
            out.append("")
        t += timedelta(minutes=every)
    return out
