"""The spare-clip bench: finished, checked clips kept for the slots the night and the gap-fills leave empty.

    factory.py bench status [account]
    factory.py bench add <account> <clip folder | job> [--slug S] [--until YYYY-MM-DD] [--why ..] [--kind ..]
                         [--by NAME] [--made-on D] [--allow-word WORD --because WHY] [--check]
    factory.py bench fill <account|all> --date today [--dry-run] [--slots 14:00,15:45]
    factory.py bench retire <id> [--why ..]
    factory.py bench render gaming --job JOB --slug SLUG [--until 17:30]   GTA: one clip rendered with no slot
    factory.py igcut politics --bench <id> --slug SLUG --segments ...     a Politics item's own Instagram cut
    factory.py ytcut politics --bench <id> --slug SLUG (--segments ... | --from-instagram)

WHERE. state/<account>/bench/<id>/, id = <made on, YYYYMMDD>-<slug>. Never on the iCloud Desktop (no "NAME 2" copies,
nothing dragged away). An item holds the clip, its cover and captions, the words for each platform, a platform's own
cut where the outlet needs one (the Politics's Instagram), source.json, and bench.json: the job, source links, host,
rights, when it was made and until when it stays good (evergreen_until), its format, seconds and SHA-256, its cast
(the Politics: checked by a person, nothing else), GTA's word templates, and what happened to it (state, uses).

ADD copies (never moves) and refuses, writing nothing, when: the clip is outside the account's seconds, a platform has
no words, the clip (its SHA-256) is already on a board or on the bench, the same clip (its slug) is on a board, was
delivered on one (collect saw it: a clip sent back with Make again included), or is on the bench in any state
(declined, missed and retired ones included: one clip goes on the bench once), Publish/
schedule.json plans it for today or later or had it on a day with no board (it went out by hand then; the Politics's
Publish/posted.json too), it shares more than 15% of its seconds with a clip of the same job OR the same source video
on a board or on the bench (any state), a word dates it ("today", "just dropped", ...: the words and the hook always,
the captions for the words that only make sense on one day) unless --allow-word says why, a file is an iCloud
"NAME 2" copy, the bench is full (accounts.json bench.max). GTA: a countdown card (a number burned into the picture)
or the skill's stock comment question, or a date past bench.until_cap (the day before the launch). The Politics: any
person on the clip whose cast entry a person has not verified (provisional, or a bot's line), or a clip with no cast.
A Politics item that still needs a platform's own cut waits as "needs-cut" until igcut/ytcut --bench makes it (and its
cut's own words pass the same dated-word check).

FILL (launchd com.clipfactory.bench-fill at 06:18, `--date today`; bench.fill in accounts.json must be true). Writes
only on its own posting day between 05:50 (the night shift's last start, shift.LAST_START) and 06:25 (the notice goes
at 06:30, Andrew signs about 06:45; 06:15-08:00 is frozen for deploys). Per account, it waits for quiet: nothing of
that account renders, no worker runs, and its board, Publish/schedule.json and the day's delivery folders have not
changed for 3 minutes (a Claude run between two steps renders nothing, but still writes); not quiet by 06:22, it fills
nothing for that account. Then, in time
order, each slot still open or failed, at least 90 minutes away, never a signed one, never a REACTIONS slot still
held for its duo (before 03:30), and never a slot kept for a clip Publish/schedule.json plans for today that is not on
the board yet (the gap-fill may still deliver it; collect would move it to the first open slot), takes the ready item
that expires first, if: its source is on no slot of that board and no other bench clip that day; GTA: its b-roll reel
source is not in that day's Publish/broll-used-<day>.json; the Politics: its cast is still verified and the day rule
(viral-clip-engine rotation.py, over the boards AND Publish/schedule.json of the 7 days) shows no FAIL it did not show
without it. The add's checks run again first, against everything since: the clip on any board (its SHA-256 or its
slug), planned in Publish/schedule.json, 15% of its seconds shared with a clip of the same job or source; any of them
and the item is rejected for good, with the reason. It goes in through `factory.py place` (place.py): an unsigned
READY draft, refused if its SHA-256 is on any board, words for the posting day (GTA: the skill's copy engine writes
them for that date, the countdown included; the stored words with the countdown rewritten when the job is gone). The
board says "from the bench" and Clip Review shows a Bench tag. Stats credit the item's own source (host, links,
rights). No placement starts after 06:24:30, and no helper runs past it.

AFTER (reconcile, at each fill): signed or out on a platform = used; declined or made again = rejected; not signed
20 hours past its slot = missed (its file stays on that board, so place would refuse it: it is never offered again
and no longer counts as stock, and the builder makes another); past its date = expired; a changed file = broken.

Every check of the clip stays where it is: the bench only keeps what passed them, and place and the day rule judge it
again on the day it goes out.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import board as B
from . import collect as K
from . import config as C

READY, NEEDS_CUT, PLACED, USED, REJECTED, MISSED, EXPIRED, BROKEN, RETIRED = (
    "ready", "needs-cut", "placed", "used", "rejected", "missed", "expired", "broken", "retired")
STATES = (READY, NEEDS_CUT, PLACED, USED, REJECTED, MISSED, EXPIRED, BROKEN, RETIRED)
STOCK = (READY, NEEDS_CUT, PLACED)          # what counts against bench.max
GONE = (PLACED, USED, REJECTED, MISSED, RETIRED)   # another item of the same clip in one of these: this one never goes

NOT_BEFORE = "05:50"    # the night shift's last start (shift.LAST_START): no fresh clip can still begin after it
QUIET_BY = "06:22"      # not quiet by then, the fill gives up on that account for the day
QUIET_S = 180           # quiet = no render, no worker, and the board, schedule.json and delivery folders unchanged this long
STOP_AT = "06:25"       # shift.BENCH_STOP: the 06:30 notice reads the board, he signs about 06:45
LAST_START_S = 30       # no placement starts later than this before STOP_AT (a placement copies files)
LEAD_MIN = 90           # place.py's own: time to watch it before Approve, and for YouTube's upload
POLL_S = 20
HELPER_S = 180          # the copy engine's and the day rule's own timeout; capped to the time left before 06:24:30
RENDER_FROM, RENDER_UNTIL = "08:00", "17:30"   # bench render: daylight only, after the morning freeze (the nights
                                               # belong to the night shift, 21:00-06:15, and the 15:00 prep's analysis)

# fill false (the shipped value too): nothing is placed until Andrew has seen a dry run and it is switched on
DEFAULTS = {"fill": False, "target": 6, "low": 4, "max": 8, "evergreen_days": 30, "until_cap": "",
            "lead_minutes": LEAD_MIN, "launch": "2026-11-19"}
POLITICS_DEFAULTS = {"evergreen_days": 90}

# words that tie a clip to the day it was made. HARD ones are refused in the words, the hook AND the captions; SOFT
# ones in the words and the hook (a streamer's "today we're looking at" in the speech is not news)
DATED_HARD = ("yesterday", "tomorrow", "last night", "earlier today", "hours ago", "minutes ago", "just in",
              "breaking", "live now")
DATED_SOFT = ("today", "tonight", "this morning", "this afternoon", "this evening", "this week", "this weekend",
              "right now", "just now")
DATED_GTA = ("just dropped", "out now", "new trailer")

COUNTDOWN = re.compile(r"\b\d+ days to GTA 6\.")
DAYS_TOKEN = "{{DAYS_TO_GTA6}}"
COPY_NAME = re.compile(r"^.+ [2-9](\.[A-Za-z0-9]+)?$")     # iCloud's "NAME 2.mp4" (and a folder "NAME 2")
MAX_SHARED = 0.15                                          # the factory's spread rule: shared seconds with one cut
# the Politics's new looks (2026-10-21, on since 10-21): segments of their own beside accounts.json's eight formats
# (Publish/day_folder.py SEGMENT); the day rule (rotation.py) knows them too
POLITICS_LOOKS = ("flashback", "faceoff", "exhibit", "outburst")


class Refused(Exception):
    """Why an add, a render or a placement was refused (nothing was written)."""


# ------------------------------------------------------------------------------------------------------ the store
def settings(acct: C.Account) -> dict:
    out = dict(DEFAULTS)
    if acct.maker == "politics":
        out.update(POLITICS_DEFAULTS)
    out.update((acct.raw or {}).get("bench") or {})
    return out


def root(acct: C.Account) -> Path:
    return C.STATE / acct.slug / "bench"


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def items(acct: C.Account) -> List[dict]:
    """Every item on the account's bench (bench.json + "_dir"), oldest-expiring first."""
    out = []
    r = root(acct)
    if not r.is_dir():
        return out
    for d in sorted(r.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "_")) or d.name == "incoming":
            continue
        it = _read(d / "bench.json")
        if it.get("id") == d.name:
            it["_dir"] = str(d)
            out.append(it)
    return sorted(out, key=lambda i: (str(i.get("evergreen_until") or "9999"), str(i.get("made_on") or ""),
                                      i["id"]))


def find(cfg: C.Config, ident: str) -> Tuple[C.Account, dict]:
    for acct in cfg.accounts.values():
        for it in items(acct):
            if it["id"] == ident:
                return acct, it
    raise SystemExit(f"no bench item {ident!r} (factory.py bench status lists them)")


def save(it: dict) -> None:
    d = Path(it["_dir"])
    body = {k: v for k, v in it.items() if not k.startswith("_")}
    body["updated_at"] = B.utcnow()
    tmp = d / "bench.json.tmp"
    tmp.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, d / "bench.json")


def set_state(it: dict, state: str, why: str) -> str:
    was = it.get("state")
    it["state"] = state
    it.setdefault("log", []).append(f"{B.utcnow()} {was} -> {state}: {why}"[:600])
    save(it)
    return f"  {it['id']}: {was} -> {state} ({why})"


def from_bench(slot: dict) -> dict:
    """The slot's bench marker when the clip in it now is the bench clip ({} otherwise: a Make again or a later clip
    leaves the marker of the one before)."""
    b = slot.get("bench") or {}
    return b if b.get("id") and b.get("slug") and b.get("slug") == slot.get("slug") else {}


def from_bench_count(board: dict, statuses=(B.READY,)) -> int:
    return sum(1 for s in (board or {}).get("slots", {}).values() if s.get("status") in statuses and from_bench(s))


# ------------------------------------------------------------------------------------------------------ helpers
def _hm(cfg: C.Config, day: date, hhmm: str) -> datetime:
    return cfg.slot_time(day, hhmm)


def _keys(urls) -> set:
    from . import makers as M
    out = set()
    for u in urls or []:
        if u:
            out.add(str(u))
            v = M.video_id(str(u))
            if v:
                out.add(v)
    return out


def _span(clip: dict) -> Optional[Tuple[float, float]]:
    try:
        segs = [s for s in (clip.get("segments") or []) if s and s[0] is not None]
        if segs:
            return float(min(s[0] for s in segs)), float(max(s[1] for s in segs))
        if clip.get("start") is not None and clip.get("end") is not None:
            return float(clip["start"]), float(clip["end"])
    except (TypeError, ValueError, IndexError):
        pass
    return None


def _overlap(a, b) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0])) / max(0.1, a[1] - a[0])


def _is_copy(p: Path) -> bool:
    return bool(COPY_NAME.match(p.name))


def _lint(text: str, words) -> List[str]:
    low = text.lower()
    return [w for w in words if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low)]


def _srt_text(p: Optional[Path]) -> str:
    if not p or not p.is_file():
        return ""
    keep = []
    for ln in p.read_text(errors="replace").splitlines():
        if ln.strip() and not ln.strip().isdigit() and "-->" not in ln:
            keep.append(ln)
    return "\n".join(keep)


def _sched(acct: C.Account) -> dict:
    d = _read(acct.account_dir / "Publish" / "schedule.json")
    return d if isinstance(d, dict) else {}


def _sched_job(sched: dict, slug: str, day: str = "") -> str:
    for d in ([day] if day in sched else []) + sorted(sched, reverse=True):
        slots = sched.get(d) if isinstance(sched.get(d), dict) else {}
        ent = next((e for e in slots.values() if isinstance(e, dict) and e.get("slug") == slug and e.get("job")), None)
        if ent:
            return str(ent["job"])
    return ""


def _gta_slate(acct: C.Account, job: str, slug: str) -> Tuple[dict, dict]:
    """(the slate entry, the project config) of a GTA job's clip, read where the skill's day_folder reads them: the
    skill's work/<job>/ first, then the account's Jobs/<job>/_project/, then the skill's references/jobs/<job>/."""
    for sp in (acct.skill_dir / "work" / job / "slate.json", acct.account_dir / "Jobs" / job / "_project" / "slate.json",
               acct.skill_dir / "references" / "jobs" / job / "slate.json"):
        sl = _read(sp)
        hit = next((c for c in sl.get("clips") or [] if isinstance(c, dict) and c.get("slug") == slug), None)
        if hit is not None:
            return hit, _read(sp.with_name("project.json"))
    return {}, {}


def _politics_slate(acct: C.Account, job: str, slug: str) -> Tuple[dict, dict]:
    jd = acct.account_dir / "Jobs" / job
    hit = next((c for c in _read(jd / "slate.json").get("clips") or [] if isinstance(c, dict) and c.get("slug") == slug),
               None)
    return (hit or {}), _read(jd / "project.json")


BOT_WORDS = re.compile(r"(?<![-\w])claude(?![-\w])|(?<![-\w])hermes(?![-\w])|(?<![-\w])bots?(?![-\w])|needs\s+andrew",
                       re.I)   # viral-clip-engine cast.BOT_VERIFIED, word for word (signoff.BOT_VERIFIED)


def politics_cast(acct: C.Account, job: str, clip: dict) -> dict:
    """{status: verified | provisional | unverified | none, people, cast_file, why}. Verified = every person on the
    clip's name cards (every person in the cast when it has no cards) has a `verified` line a person wrote."""
    cf = acct.account_dir / "Jobs" / job / "cast.json"
    cast = _read(cf)
    people = {str(p.get("id") or "").lower(): p for p in cast.get("people") or [] if isinstance(p, dict)}
    who = sorted({str(c.get("who") or "").lower() for c in clip.get("cards") or [] if isinstance(c, dict) and c.get("who")})
    if not people:
        return {"status": "none", "people": [], "cast_file": str(cf),
                "why": f"no cast for {job} (Jobs/{job}/cast.json): a Politics clip names who speaks"}
    wanted = who or sorted(people)
    rows, worst = [], "verified"
    for w in wanted:
        p = people.get(w)
        if p is None:
            rows.append({"id": w, "name": "", "verified": ""})
            worst = "unverified"
            continue
        said = str(p.get("verified") or "").strip()
        row = {"id": w, "name": str(p.get("name") or ""), "verified": said[:200]}
        if not said and isinstance(p.get("provisional"), dict):
            worst = "provisional"
            row["provisional"] = True
        elif not said or BOT_WORDS.search(said):
            worst = "unverified" if worst == "verified" else worst
            row["by_bot"] = bool(said)
        rows.append(row)
    bad = [r for r in rows if not r["verified"] or r.get("by_bot") or r.get("provisional")]
    why = "" if not bad else ("not verified by a person: " + ", ".join(
        f"{r['name'] or r['id']} ({'provisional' if r.get('provisional') else 'a bot wrote verified' if r.get('by_bot') else 'not in cast.json' if not r['name'] else 'verified is empty'})"
        for r in bad))
    return {"status": worst if bad else "verified", "people": rows, "cast_file": str(cf), "why": why}


def _needs_cuts(acct: C.Account, secs: float, rights: str, cuts: Dict[str, dict]) -> Dict[str, str]:
    """platform -> why it needs its own cut that the item does not have (outlets.needs_cut on the item as a slot)."""
    from . import outlets as O
    view = {"clip": {"seconds": secs}, "rights": rights, "cuts": cuts, "publish_at": ""}
    out = {}
    for plat in acct.platforms:
        why = O.needs_cut(acct, view, plat)
        if why:
            out[plat] = why
    return out


def _cut_files(folder: Path) -> Dict[str, Dict[str, Optional[Path]]]:
    """platform -> {mp4, srt, words} of the platform cuts in a folder (collect.CUT_FILES names)."""
    out = {}
    for plat, p in K.cuts_in(folder).items():
        srt = p.with_suffix(".srt")
        words = folder / f"{p.stem}.cut.txt"
        out[plat] = {"mp4": p, "srt": srt if srt.is_file() else None,
                     "words": words if words.is_file() and words.read_text().strip() else None}
    return out


def _all_bench_shas(cfg: C.Config) -> Dict[str, str]:
    out = {}
    for a in cfg.accounts.values():
        for it in items(a):
            if it.get("sha256"):
                out[it["sha256"]] = f"{it['id']} ({it.get('state')})"
    return out


def _job_of(acct: C.Account, slot: dict, sched: dict, day: str) -> str:
    return str((slot.get("cast_check") or {}).get("job") or (slot.get("bench") or {}).get("job") or slot.get("job")
               or _sched_job(sched, str(slot.get("slug") or ""), day) or "")


ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")


def history(cfg: C.Config, acct: C.Account) -> dict:
    """Everything that says a clip went out or is on its way, read once: every board slot of the account (any day,
    any state), Publish/schedule.json, the Politics's Publish/posted.json (its results politics, from August), and every
    item on the bench."""
    boards, days, seen = [], set(), []
    for bp in B.all_boards(acct):
        days.add(bp.stem)
        bd = _read(bp)
        for k, s in sorted((bd.get("slots") or {}).items()):
            if isinstance(s, dict):
                boards.append((bp.stem, k, s))
        # every delivery collect (or place) looked at that day, whatever came of it: a clip sent back with Make again
        # leaves its slot, but not this list
        for sl in sorted(_seen_slugs(bd)):
            seen.append((bp.stem, sl))
    its = items(acct)
    return {"boards": boards, "board_days": days, "seen": seen, "sched": _sched(acct),
            "posted": _read(acct.account_dir / "Publish" / "posted.json") if acct.maker == "politics" else {},
            "bench": its, "spans": {i["id"]: i.get("span") for i in its}}


def _seen_slugs(board: dict) -> set:
    """The slugs of every delivery a board's collect looked at ("HHMM slug" folders in `seen`, and each slot's
    source_folder)."""
    out = {Path(str(k).split("|", 1)[0]).name.split(" ", 1)[-1] for k in board.get("seen") or [] if " " in
           Path(str(k).split("|", 1)[0]).name}
    out |= {Path(str(s["source_folder"])).name.split(" ", 1)[-1] for s in (board.get("slots") or {}).values()
            if isinstance(s, dict) and s.get("source_folder")}
    return out


def _item_keys(it: dict) -> set:
    src = it.get("source") or {}
    return _keys(src.get("urls") or [it.get("source_url")])


def _board_span(acct: C.Account, s: dict, sched: dict, day: str, bench_spans: dict) -> Optional[Tuple[float, float]]:
    """The source seconds a board clip used: a bench clip's own span, else the GTA maker's pick_span, else its job's
    slate (None when nothing says)."""
    bn = from_bench(s)
    if bn and bench_spans.get(bn["id"]):
        sp = bench_spans[bn["id"]]
        return float(sp[0]), float(sp[1])
    if acct.maker == "gta" and isinstance(s.get("pick_span"), (list, tuple)):
        try:
            return float(s["pick_span"][0]), float(s["pick_span"][1])
        except (TypeError, ValueError, IndexError):
            pass
    job = _job_of(acct, s, sched, day)
    if job and s.get("slug"):
        return _span((_gta_slate if acct.maker == "gta" else _politics_slate)(acct, job, s["slug"])[0])
    return None


def bump_of(hist: dict, slug: str, bumped: Optional[dict]) -> Optional[Tuple[str, str]]:
    """(day, slot) the breaking desk bumped this clip from (breaking.py), when that slot's own record says so (a
    "bumped" entry under the clip's slug, not put back); None otherwise. Such a clip was delivered that day and never
    went out: that day's delivery and plan do not count against it, every other check does."""
    if not isinstance(bumped, dict) or bumped.get("slug") != slug:
        return None
    for d, k, s in hist["boards"]:
        if d == bumped.get("day") and k == bumped.get("slot") and any(
                isinstance(e, dict) and e.get("slug") == slug and not e.get("restored") for e in s.get("bumped") or []):
            return d, k
    return None


def went_out(cfg: C.Config, acct: C.Account, slug: str, job: str, span, keys: set, today: date, hist: dict,
             skip_id: str = "", at_add: bool = False, bumped: Optional[dict] = None) -> str:
    """Why this clip may already have gone out, or be on its way ('' when nothing says so). The same checks at the add
    (a refusal) and at each fill (the item is rejected for good): the nightly and the builder draw from the same
    archive, so a clip can be cut again after its spare was added.
      - the same clip (its slug) on any board of the account, any day, any state (its own earlier placement aside),
        or delivered on any day (a board's `seen`: a clip sent back with Make again leaves its slot, not that list);
      - Publish/schedule.json (the Politics's posted.json too) with it (its job and slug): planned for today or later,
        or on a day with no board (before the factory's boards: it went out by hand then). On a day WITH a board, the
        board is the truth: a planned clip that is not on it never came;
      - more than 15% of its source seconds shared with a clip of the same job OR the same source video, on a board or
        on the bench (any state: a spare Andrew declined or that was never signed is never offered again);
      - the bench: another item of the same clip (at the add: any state; at a fill: placed, used, rejected, missed,
        retired);
      - the kill list (reasons.py): a span or video the owner pulled or declined, or b-roll a Pull or Decline killed.
    A clip the breaking desk bumped (`bumped`, proven by its slot's record: bump_of) is not held to its own day's
    delivery, plan or unposted results row."""
    ds = today.isoformat()
    bump = bump_of(hist, slug, bumped)

    def own(s: dict) -> bool:
        return bool(skip_id) and (s.get("bench") or {}).get("id") == skip_id

    for d, k, s in hist["boards"]:
        if s.get("slug") == slug and not own(s):
            return f"the clip {slug!r} is already on a board: {d} {k} ({s.get('status')})"
    for d, sl in hist.get("seen") or []:     # (a ready spare was never placed, so this is never its own placement)
        if sl == slug and not (bump and d == bump[0]):
            return (f"the clip {slug!r} was delivered on {d} (that board's collect saw it: taken, turned away, or "
                    f"sent back with Make again)")
    for d, slots in sorted((hist.get("sched") or {}).items()):
        for k, e in sorted(slots.items()) if isinstance(slots, dict) else []:
            if not isinstance(e, dict) or e.get("slug") != slug or (job and e.get("job") and str(e["job"]) != job):
                continue
            if bump and (d[:10], k) == bump:
                continue
            day_s = d[:10] if ISO_DAY.match(d) else ""
            if not day_s or day_s >= ds:
                return f"Publish/schedule.json plans {slug!r} for {d} {k}: the night delivers it itself"
            if day_s not in hist["board_days"]:
                return (f"Publish/schedule.json had {slug!r} on {d} {k}, a day with no board: it went out then "
                        f"(by hand, before the factory's boards)")
    for d, rows in sorted((hist.get("posted") or {}).items()):
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or r.get("slug") != slug or (job and r.get("job") and str(r["job"]) != job):
                continue
            day_s = d[:10] if ISO_DAY.match(d) else ""
            if bump and day_s == bump[0] and not (r.get("url") or r.get("posted_at")):
                continue
            if r.get("url") or r.get("posted_at") or not day_s or day_s not in hist["board_days"] or day_s >= ds:
                return f"Publish/posted.json lists {slug!r} on {d} ({r.get('destination') or 'a post'})"
    if span:
        memo = hist.setdefault("_memo", {})        # per board slot, read once per fill: its job, sources and span
        for d, k, s in hist["boards"]:
            if not s.get("slug") or own(s):
                continue
            if (d, k) not in memo:
                memo[(d, k)] = (_job_of(acct, s, hist["sched"], d), _slot_sources(acct, s, hist["sched"], d),
                                _board_span(acct, s, hist["sched"], d, hist["spans"]))
            sjob, skeys, sp = memo[(d, k)]
            same_job = bool(job) and sjob == job
            if not same_job and not (keys and keys & skeys):
                continue
            if sp and _overlap(span, sp) > MAX_SHARED:
                return (f"{slug} shares {_overlap(span, sp):.0%} of its seconds with {d} {k} {s['slug']} "
                        f"({'the same job' if same_job else 'the same source video'}; the most is {MAX_SHARED:.0%})")
        for it in hist["bench"]:
            if it["id"] == skip_id or not it.get("span") or it.get("slug") == slug:
                continue
            same_job = bool(job) and it.get("job") == job
            if not same_job and not (keys and keys & _item_keys(it)):
                continue
            sp = (float(it["span"][0]), float(it["span"][1]))
            if _overlap(span, sp) > MAX_SHARED:
                return (f"{slug} shares {_overlap(span, sp):.0%} of its seconds with bench {it['id']} ({it.get('state')}; "
                        f"{'the same job' if same_job else 'the same source video'}; the most is {MAX_SHARED:.0%})")
    for it in hist["bench"]:
        if it["id"] != skip_id and it.get("slug") == slug and (at_add or it.get("state") in GONE):
            return (f"the clip {slug!r} is already on the bench: {it['id']} ({it.get('state')}); one clip goes on the "
                    f"bench once")
    if "_kills" not in hist:                   # the owner's Pulls and Declines (reasons.py), read once per fill
        from . import reasons as R
        try:
            hist["_kills"] = R.entries(acct)
        except Exception:  # noqa: BLE001  (an unreadable kill list kills nothing)
            hist["_kills"] = []
    if hist["_kills"]:
        from . import reasons as R
        try:
            e, why = R.hit(acct, R.bench_identity(acct, slug, job, span, keys), hist["_kills"])
        except Exception:  # noqa: BLE001
            e, why = None, ""
        if e:
            return f"on the kill list: {why}"
    return ""


def _slate_reels(clip: dict) -> List[str]:
    """The b-roll reel source a GTA slate clip rendered with (the skill writes it on the clip: duo/pip _reel_source,
    a quiz's reel_source)."""
    out = []
    for v in ((clip.get("duo") or {}).get("_reel_source"), (clip.get("pip") or {}).get("_reel_source"),
              (clip.get("quiz") or {}).get("reel_source")):
        if v and str(v) not in out:
            out.append(str(v))
    return out


def _reel_sources(acct: C.Account, slug: str) -> List[str]:
    """The b-roll reel sources a GTA clip used, by slug in the skill's day politicss (Publish/broll-used-<date>.json):
    only when its slate does not say (a folder added with no job)."""
    out = []
    for p in sorted((acct.account_dir / "Publish").glob("broll-used-*.json")):
        for e in _read(p).get("entries") or []:
            if isinstance(e, dict) and e.get("slug") == slug and e.get("source") and e["source"] not in out:
                out.append(str(e["source"]))
    return out


def _render_words(tmpl: str, day: date, launch: date) -> str:
    n = (launch - day).days
    if n > 0:
        return tmpl.replace(DAYS_TOKEN, f"{n} days to GTA 6.")
    t = tmpl.replace(DAYS_TOKEN, "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return re.sub(r"(?<=\S)  +(?=\S)", " ", t)


# ------------------------------------------------------------------------------------------------------ add
def add(cfg: C.Config, acct: C.Account, what: str, slug: Optional[str] = None, until: Optional[str] = None,
        why: str = "", kind: str = "", by: str = "", allow: Optional[Dict[str, str]] = None,
        made_on: Optional[str] = None, job: Optional[str] = None, fmt: Optional[str] = None, check: bool = False,
        now: Optional[datetime] = None, bumped: Optional[dict] = None) -> Tuple[bool, List[str]]:
    """(added, lines). A refusal writes nothing and says why. bumped ({day, slot, slug}): a clip the breaking desk
    took off that slot (breaking.py); with check, the bump is supposed done (the slot no longer holding it)."""
    tmp = Path(tempfile.mkdtemp(prefix="clipfactory-bench-"))
    try:
        return _add(cfg, acct, what, slug, until, why, kind, by, dict(allow or {}), made_on, job, fmt, check,
                    now or cfg.now(), tmp, bumped)
    except Refused as e:
        return False, [f"  REFUSED, nothing written: {e}"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _gather_folder(cfg, acct, folder: Path, slug, job) -> dict:
    if not folder.is_dir():
        raise Refused(f"no folder at {folder}")
    if _is_copy(folder):
        raise Refused(f"{folder.name} is an iCloud copy (\"NAME 2\"): use the original folder")
    mains = [p for p in folder.glob("*.mp4") if p.name not in K._CUT_NAMES]
    if not mains:
        raise Refused(f"no clip (.mp4) in {folder}")
    clip = K.main_clip(folder)
    if _is_copy(clip):
        raise Refused(f"{clip.name} is an iCloud copy (\"NAME 2.mp4\"): use the original")
    m = K.SLOT_DIR.match(folder.name)
    slug = slug or (m.group(2) if m else None) or re.sub(r"^\d{1,2}[-_ ]", "", clip.stem)
    covers = [p for p in folder.glob("*.jpg") if not _is_copy(p)]
    cover = next(iter(sorted(covers)), None)
    srt = next(iter(sorted(p for p in folder.glob("*.srt") if not _is_copy(p)
                           and f"{p.stem}.mp4" not in K._CUT_NAMES)), None)
    words = {}
    for plat in acct.platforms:
        p = K.pick_copy(folder, plat)
        if p is not None:
            words[p.name] = p.read_text()
    src = _read(folder / "source.json")
    cc = _read(folder / "cast-check.json")
    job = job or str(cc.get("job") or "")
    if not job and m:
        pack = _read(folder.parent / f"{m.group(1)}.pack.json")
        if pack.get("slug") == slug and pack.get("job"):
            job = str(pack["job"])
    if not job:
        job = _sched_job(_sched(acct), slug)
    return {"clip": clip, "cover": cover, "srt": srt, "words": words, "cuts": _cut_files(folder), "slug": slug,
            "job": job, "host": str(src.get("host") or src.get("publisher") or ""), "rights": str(src.get("rights") or ""),
            "cast_check": cc, "words_from": "template", "from": str(folder)}


def _gather_job(cfg, acct, job: str, slug: Optional[str], day: date, tmp: Path) -> dict:
    from . import place as PL
    if not slug:
        raise Refused("a job needs --slug (the clip's slug on its slate)")
    if acct.maker == "gta":
        (tmp / "words").mkdir(parents=True, exist_ok=True)
        try:
            info = PL.skill_words(acct, job, slug, day, tmp / "words")
        except PL.Refused as e:
            raise Refused(str(e)) from None
        if info.get("question_default"):
            raise Refused("the comment question is the skill's stock one (it went out on breakdowns, news and stunts "
                          "on 10-17): write the clip's own \"question\" on its slate, then add it again")
        jd = Path(info["out_dir"]) if info.get("out_dir") else acct.account_dir / "Jobs" / job
        clip = PL._job_file(jd / "clips", slug, "mp4")
        if clip is None:
            raise Refused(f"no single rendered clip for {slug!r} in {jd / 'clips'} (render it first: factory.py "
                          f"bench render {acct.slug} --job {job} --slug {slug})")
        words = {p.name: p.read_text() for p in sorted((tmp / "words").glob("*.txt"))}
        return {"clip": clip, "cover": PL._job_file(jd / "clips", slug, "jpg"),
                "srt": PL._job_file(jd / "captions", slug, "srt"), "words": words, "cuts": {}, "slug": slug,
                "job": job, "host": str(info.get("host") or ""), "rights": str(info.get("rights") or ""),
                "urls": list(info.get("sources") or []), "title": str(info.get("title") or ""),
                "template": str(info.get("template") or ""), "words_from": "skill", "from": f"job {job}"}
    jd = acct.account_dir / "Jobs" / job
    if not jd.is_dir():
        raise Refused(f"no job folder {jd}")
    def one(pat: str) -> Optional[Path]:
        hits = sorted(p for p in jd.glob(pat) if not _is_copy(p))
        return hits[0] if hits else None
    clip = one(f"clips/[0-9][0-9]-{slug}.mp4")
    if clip is None:
        raise Refused(f"no rendered clip clips/NN-{slug}.mp4 in {jd}")
    staged = one(f"staged/[0-9][0-9]-{slug}")
    words = {}
    if staged and staged.is_dir():
        for plat in acct.platforms:
            p = K.pick_copy(staged, plat)
            if p is not None:
                words[p.name] = p.read_text()
    return {"clip": clip, "cover": one(f"clips/[0-9][0-9]-{slug}.jpg"), "srt": one(f"captions/[0-9][0-9]-{slug}.srt"),
            "words": words, "cuts": {}, "slug": slug, "job": job, "host": "", "rights": "", "words_from": "stored",
            "from": f"job {job}"}


def _add(cfg, acct, what, slug, until, why, kind, by, allow, made_on, job, asked_fmt, check, now, tmp, bumped=None):
    from . import place as PL
    today = now.date()
    st = settings(acct)
    stock = [i for i in items(acct) if i.get("state") in STOCK]
    if len(stock) >= int(st["max"]):
        raise Refused(f"the {acct.name} bench holds {len(stock)} (bench.max {st['max']} in accounts.json): retire one "
                      f"first")
    p = Path(os.path.expanduser(what))
    g = (_gather_folder(cfg, acct, p, slug, job) if (p.is_dir() or os.sep in what or what.startswith(("~", ".")))
         else _gather_job(cfg, acct, what, slug, today, tmp))
    slug = re.sub(r"[^a-z0-9]+", "-", str(g["slug"]).lower()).strip("-")[:60]
    if not slug:
        raise Refused("no slug for the clip")
    clip: Path = g["clip"]
    if clip.suffix.lower() != ".mp4":
        raise Refused(f"{clip.name} is not an .mp4")
    for f in [clip, g.get("cover"), g.get("srt")]:
        if f and _is_copy(Path(f)):
            raise Refused(f"{Path(f).name} is an iCloud copy (\"NAME 2\"): use the original")
    secs = K.probe_seconds(clip)
    if secs is None:
        raise Refused(f"ffprobe could not read {clip.name}")
    if not acct.min_seconds - 0.05 <= secs <= acct.max_seconds + 0.05:
        raise Refused(f"{clip.name} is {secs:.1f}s; {acct.name} takes {acct.min_seconds:.0f} to {acct.max_seconds:.0f} "
                      f"seconds")
    missing = [plat for plat in acct.platforms
               if not any(n in g["words"] and g["words"][n].strip() for n in K.COPY_FILES.get(plat, [f"{plat}.txt"]))]
    if missing:
        raise Refused(f"no words for {', '.join(missing)}")
    sha = B.sha256_file(clip)
    where = PL.on_boards(cfg, sha)
    bump_at = (f"{acct.name} {bumped.get('day')} {bumped.get('slot')} (" if isinstance(bumped, dict) and check
               and bumped.get("slug") == slug else None)
    if where and not (bump_at and where.startswith(bump_at)):
        raise Refused(f"{clip.name} is already on a board: {where}")
    held = _all_bench_shas(cfg).get(sha)
    if held:
        raise Refused(f"{clip.name} is already on the bench: {held}")
    hist = history(cfg, acct)
    if bump_at:                          # asked before the bump: the slot as the bump will leave it
        hist["boards"] = [(d, k, dict({x: v for x, v in s.items() if x != "slug"},
                                      bumped=list(s.get("bumped") or []) + [{"slug": slug}]))
                          if (d, k) == (bumped.get("day"), bumped.get("slot")) else (d, k, s)
                          for d, k, s in hist["boards"]]
    on = next((f"{d} {k} ({s.get('status')})" for d, k, s in hist["boards"] if s.get("slug") == slug), "")
    if on:
        raise Refused(f"the clip {slug!r} is already on a board: {on}")

    # where it came from, from its job
    jobname = str(g.get("job") or "")
    slate, proj = ({}, {})
    if jobname:
        slate, proj = (_gta_slate if acct.maker == "gta" else _politics_slate)(acct, jobname, slug)
    if acct.maker == "politics" and not jobname:
        raise Refused("the Politics's bench needs the clip's job (--job), so its cast and its day rule can be checked")
    if acct.maker == "politics" and not slate:
        raise Refused(f"{jobname}/slate.json has no clip {slug!r}: the day rule reads the slate")
    urls = list(g.get("urls") or [])
    for u in [proj.get("source_url")] + [s.get("url") if isinstance(s, dict) else s for s in proj.get("sources") or []]:
        for m_ in re.findall(r"https?://\S+", str(u or "")):
            if m_ not in urls:
                urls.append(m_)
    hostd = proj.get("host")
    host = g.get("host") or (hostd.get("name") if isinstance(hostd, dict) else hostd) or proj.get("source_channel") or ""
    if not host and urls:
        from urllib.parse import urlparse
        host = urlparse(urls[0]).netloc
    rights = str(g.get("rights") or proj.get("rights") or ("unverified" if acct.maker == "gta" else ""))
    template = str(g.get("template") or slate.get("template") or "").strip().lower()
    if acct.maker in ("gta", "garage"):    # 2026-10-29: a Garage spare has no slate, it says its format (edit)
        fmt = "reactions" if template == "reactions" else (K.GTA_LANES.get(template) or "")
        if not fmt:
            fmt = K.GTA_LANES.get(str(proj.get("_run_template") or "").strip().lower(), "")
        fmt = asked_fmt or fmt
        ok = set(acct.slot_formats) | set(acct.formats) | {"reactions"}
        if not fmt:
            raise Refused(f"no format for {slug!r} (no job slate says its template): say it with --format "
                          f"({', '.join(sorted(ok))})")
        if fmt not in ok:
            raise Refused(f"format {fmt!r} is not one of {', '.join(sorted(ok))}")
    else:
        if asked_fmt and template and asked_fmt != template:
            raise Refused(f"--format {asked_fmt} is not the clip's own segment ({template}, on its slate)")
        fmt = template or ""
        if not fmt:
            raise Refused(f"{jobname}/slate.json gives {slug!r} no segment (template): the day rule needs it")
        if fmt not in tuple(acct.formats) + POLITICS_LOOKS:
            raise Refused(f"segment {fmt!r} is not one of {', '.join(tuple(acct.formats) + POLITICS_LOOKS)}")
    hook = str(slate.get("hook") or "")
    span = _span(slate)

    # the checks a spare clip needs on top of the ones it passed when it was made
    if acct.maker == "gta":
        cc = slate.get("count_card") or {}
        if template == "countdown" or (isinstance(cc, dict) and cc.get("number")):
            raise Refused("a countdown card (a number of days burned into the picture) is right on one day only")
        cap = str(st.get("until_cap") or "")
    else:
        cap = ""
        listed = [str(p.get("id") or "").lower() for p in g.get("cast_check", {}).get("people") or []
                  if isinstance(p, dict)]
        # the file is what was unchecked when the clip was delivered; a person may have checked them since (Approve on
        # a CAST TO CONFIRM clip writes cast.json): the job's cast.json is the truth now
        if listed and (not all(listed) or politics_cast(acct, jobname, {"cards": [{"who": w} for w in listed]})
                       .get("status") != "verified"):
            raise Refused("cast-check.json beside the clip: someone on it is not checked by a person yet (provisional "
                          "or a bot's line). The bench takes verified casts only")
    cast = politics_cast(acct, jobname, slate) if acct.maker == "politics" else {}
    if cast and cast["status"] != "verified":
        raise Refused(f"the cast is {cast['status']}: {cast['why']}. The bench takes verified casts only (a person "
                      f"checks the faces at sign-off: Approve on a CAST TO CONFIRM clip writes it)")
    dup = went_out(cfg, acct, slug, jobname, span, _keys(urls), today, hist, at_add=True, bumped=bumped)
    if dup:
        raise Refused(dup)
    hits = []
    words_all = DATED_HARD + DATED_SOFT + (DATED_GTA if acct.maker == "gta" else ())
    for name, text in sorted(g["words"].items()):
        hits += [(name, w) for w in _lint(text, words_all)]
    hits += [("the hook", w) for w in _lint(hook, words_all)]
    hits += [("the captions", w) for w in _lint(_srt_text(g.get("srt")), DATED_HARD + (DATED_GTA if acct.maker == "gta" else ()))]
    for plat, c in g["cuts"].items():
        if c.get("words"):
            hits += [(Path(c["words"]).name, w) for w in _lint(Path(c["words"]).read_text(), words_all)]
    spoken = _lint(_srt_text(g.get("srt")), DATED_SOFT)
    left = [(w_, x) for w_, x in hits if x not in allow]
    if left:
        raise Refused("a word dates it: " + "; ".join(f"{x!r} in {w_}" for w_, x in left[:6])
                      + ". Fix the words, or keep it with --allow-word WORD --because WHY")
    made = date.fromisoformat(made_on) if made_on else \
        datetime.fromtimestamp(clip.stat().st_mtime, cfg.tz()).date()
    if made > today:
        raise Refused(f"--made-on {made} is after today")
    if until:
        try:
            good = date.fromisoformat(until)
        except ValueError:
            raise Refused(f"--until {until!r}: a date, YYYY-MM-DD") from None
        if cap and good > date.fromisoformat(cap):
            raise Refused(f"--until {good} is past {cap} (bench.until_cap: the day before the launch, when every GTA 6 "
                          f"clip's words change)")
    else:
        good = today + timedelta(days=int(st["evergreen_days"]))
        if cap:
            good = min(good, date.fromisoformat(cap))
    if good <= today:
        raise Refused(f"evergreen_until {good} is not after today")
    ident = f"{made.strftime('%Y%m%d')}-{slug}"
    dest = root(acct) / ident
    if dest.exists():
        raise Refused(f"{ident} is already on the bench")

    cuts = {}
    for plat, c in g["cuts"].items():
        from . import outlets as O
        csecs = K.probe_seconds(c["mp4"])
        lo, hi = O.band(acct, plat, cut=True)
        if csecs is None or not lo - 0.05 <= csecs <= hi + 0.05:
            raise Refused(f"its {O.NAMES.get(plat, plat)} cut is {csecs}s, outside {lo:.0f}-{hi:.0f}s")
        cuts[plat] = {"name": c["mp4"].name, "sha256": B.sha256_file(c["mp4"]), "seconds": csecs,
                      "words": c["words"].name if c.get("words") else "",
                      "words_sha256": B.sha256_text(c["words"].read_text()) if c.get("words") else ""}
    need = _needs_cuts(acct, secs, rights, cuts)
    state = NEEDS_CUT if need else READY
    tmpl = {}
    if acct.maker == "gta":
        tmpl = {n: COUNTDOWN.sub(DAYS_TOKEN, t) for n, t in g["words"].items()}
    item = {
        "v": 1, "id": ident, "account": acct.slug, "slug": slug, "hook": hook,
        "title": next(iter(g["words"].get("youtube.txt", "").splitlines()), "")[:200],
        "made_on": made.isoformat(), "added_at": B.utcnow(), "added_by": by or "", "evergreen_until": good.isoformat(),
        "evergreen_why": why[:400], "kind": kind or fmt, "format": fmt, "job": jobname, "from": g["from"],
        "source": {"urls": urls, "title": str(g.get("title") or proj.get("source_title") or proj.get("hearing_title") or ""),
                   "host": host, "rights": rights, "date": str(proj.get("hearing_date") or proj.get("date") or "")},
        "source_url": urls[0] if urls else "", "seconds": secs, "sha256": sha, "span": list(span) if span else None,
        "files": {"clip": f"{slug}.mp4", "cover": f"{slug}.jpg" if g.get("cover") else "",
                  "srt": f"{slug}.srt" if g.get("srt") else "",
                  "words": {n: B.sha256_text(t) for n, t in g["words"].items()}, "cuts": cuts,
                  "templates": {f"{n}.tmpl": B.sha256_text(t) for n, t in tmpl.items()}},
        "words_from": g["words_from"], "words_template": {n: f"{n}.tmpl" for n in tmpl},
        "cast": cast, "gta": ({"reel_sources": _slate_reels(slate) or _reel_sources(acct, slug), "template": template,
                               "reel_from": "slate" if _slate_reels(slate) else "the day politicss"}
                              if acct.maker == "gta" else {}),
        "checks": {"dated_words_allowed": allow, "spoken_dated_words": spoken},
        "needs": need, "state": state, "uses": [], "log": [],
    }
    if isinstance(bumped, dict) and bump_of(hist, slug, bumped):
        item["bumped_from"] = {k: str(bumped.get(k) or "") for k in ("day", "slot", "slug")}
    said = [f"  {acct.name} bench {ident}: {secs:.1f}s {fmt}" + (f", {host}" if host else "")
            + f", good until {good}" + (f", from {jobname}" if jobname else "")
            + ("; words for the posting day from the skill's copy engine" if g["words_from"] == "skill" else "")]
    if spoken:
        said.append(f"  note: the captions say {', '.join(repr(w) for w in spoken)}: listen that it is not news")
    for plat, w in need.items():
        said.append(f"  waits for its {plat} cut ({w}): factory.py {'ytcut' if plat == 'youtube_shorts' else 'igcut'} "
                    f"{acct.slug} --bench {ident} --slug {slug} --segments A-B[,C-D]")
    if check:
        return True, said + ["  --check: nothing written"]
    root(acct).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".add-{ident}-", dir=str(root(acct))))
    try:
        shutil.copy2(clip, work / f"{slug}.mp4")
        if B.sha256_file(work / f"{slug}.mp4") != sha:
            raise Refused("the copy of the clip does not match it (a disk problem?)")
        if g.get("cover"):
            shutil.copy2(g["cover"], work / f"{slug}.jpg")
        if g.get("srt"):
            shutil.copy2(g["srt"], work / f"{slug}.srt")
        for n, t in g["words"].items():
            (work / n).write_text(t)
        for n, t in tmpl.items():
            (work / f"{n}.tmpl").write_text(t)
        for plat, c in g["cuts"].items():
            shutil.copy2(c["mp4"], work / c["mp4"].name)
            if c.get("srt"):
                shutil.copy2(c["srt"], work / c["srt"].name)
            if c.get("words"):
                shutil.copy2(c["words"], work / c["words"].name)
        (work / "source.json").write_text(json.dumps({"host": host, "rights": rights}) + "\n")
        (work / "bench.json").write_text(json.dumps(item, indent=1, ensure_ascii=False) + "\n")
        os.rename(work, dest)
    finally:
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
    _log(acct, "add", id=ident, state=state, by=by)
    return True, said + [f"  on the bench: {dest} ({state})"]


def refresh(cfg: C.Config, acct: C.Account, ident: str) -> List[str]:
    """After igcut/ytcut --bench: record the item's platform cuts, then ready once no platform still needs one."""
    from . import outlets as O
    it = next((i for i in items(acct) if i["id"] == ident), None)
    if it is None:
        return [f"  no bench item {ident}"]
    d = Path(it["_dir"])
    cuts, bad, worded = {}, [], {}
    allow = (it.get("checks") or {}).get("dated_words_allowed") or {}
    dated = DATED_HARD + DATED_SOFT + (DATED_GTA if acct.maker == "gta" else ())
    for plat, c in _cut_files(d).items():
        secs = K.probe_seconds(c["mp4"])
        lo, hi = O.band(acct, plat, cut=True)
        if secs is None or not lo - 0.05 <= secs <= hi + 0.05:
            bad.append(f"its {O.NAMES.get(plat, plat)} cut is {secs}s, outside {lo:.0f}-{hi:.0f}s")
            continue
        text = c["words"].read_text() if c.get("words") else ""
        # the cut's own words (igcut/ytcut --words) go out with it: the add's dated-word check, on them too
        hits = [w for w in _lint(text, dated) if w not in allow]
        if hits:
            worded[plat] = (f"a word dates its cut's words ({c['words'].name}: {', '.join(repr(w) for w in hits[:4])}): "
                            f"make the cut again with corrected --words")
            bad.append(worded[plat])
            continue
        cuts[plat] = {"name": c["mp4"].name, "sha256": B.sha256_file(c["mp4"]), "seconds": secs,
                      "words": c["words"].name if c.get("words") else "",
                      "words_sha256": B.sha256_text(text) if c.get("words") else ""}
    it["files"]["cuts"] = cuts
    it["needs"] = _needs_cuts(acct, float(it.get("seconds") or 0), str((it.get("source") or {}).get("rights") or ""),
                              cuts)
    it["needs"].update(worded)          # a cut with dated words would go out with the clip: it waits until fixed
    have = ", ".join("%s %.1fs" % (O.NAMES.get(p, p), c["seconds"]) for p, c in cuts.items()) or "none"
    lines = [f"  {ident}: cuts {have}"]
    lines += [f"  {ident}: {b}" for b in bad]
    if it.get("state") in (READY, NEEDS_CUT):
        want = NEEDS_CUT if it["needs"] or bad else READY
        if want != it["state"]:
            lines.append(set_state(it, want, "its cuts" + (" are all made" if want == READY else " still missing")))
            return lines
    save(it)
    return lines


def cut_target(cfg: C.Config, acct: C.Account, ident: str, slug: str) -> Tuple[Path, Path]:
    """(the item's folder, its job folder) for igcut/ytcut --bench."""
    if acct.maker != "politics":
        raise SystemExit(f"--bench cuts are for the Politics's items (Instagram, YouTube); {acct.name} needs none")
    it = next((i for i in items(acct) if i["id"] == ident), None)
    if it is None:
        raise SystemExit(f"no bench item {ident} on {acct.name}")
    if it.get("slug") != slug:
        raise SystemExit(f"bench item {ident} is the clip {it.get('slug')!r}, not {slug!r}")
    if it.get("state") not in (READY, NEEDS_CUT):
        raise SystemExit(f"bench item {ident} is {it.get('state')}: only a ready or needs-cut item takes a cut")
    jd = acct.account_dir / "Jobs" / str(it.get("job") or "")
    if not it.get("job") or not jd.is_dir():
        raise SystemExit(f"bench item {ident}: its job folder is gone ({jd})")
    return Path(it["_dir"]), jd


# ------------------------------------------------------------------------------------------------------ reconcile
def reconcile(cfg: C.Config, acct: C.Account, now: datetime, write: bool = True) -> List[str]:
    """What happened to each placed item, and which ones have expired. `write` False: say it, change nothing."""
    from . import poster as P
    out = []
    for it in items(acct):
        st = it.get("state")
        new, why = None, ""
        if st == PLACED and it.get("uses"):
            u = it["uses"][-1]
            try:
                bd = B.read(acct, date.fromisoformat(u["date"])) or {}
            except (KeyError, ValueError):
                bd = {}
            s = (bd.get("slots") or {}).get(u.get("slot"))
            if bd and s is not None:
                mine = s.get("slug") == it.get("slug") and (s.get("clip") or {}).get("sha256") == it.get("sha256")
                out_on = [p for p, r in (s.get("outlets") or {}).items() if isinstance(r, dict)
                          and (r.get("post_id") or r.get("video_id") or r.get("publish_id"))]
                if not mine:
                    new, why = REJECTED, f"{u['date']} {u['slot']} was made again or holds another clip"
                elif out_on:
                    new, why = USED, f"went out on {', '.join(sorted(out_on))} ({u['date']} {u['slot']})"
                elif s.get("status") in B.SIGNED:
                    new, why = USED, f"signed for {u['date']} {u['slot']}"
                elif s.get("status") == B.DECLINED:
                    new, why = REJECTED, f"declined on {u['date']} {u['slot']}"
                elif s.get("status") == B.READY:
                    try:
                        late = now - datetime.fromisoformat(s["publish_at"]) > P.LATE_LIMIT
                    except (KeyError, ValueError):
                        late = False
                    if late:
                        # its file stays on that board, and place refuses a SHA-256 on any board: it can never be
                        # placed again, so it no longer counts as stock (the 10:00 builder makes another)
                        new, why = MISSED, (f"not signed for {u['date']} {u['slot']} in time; its clip stays on that "
                                            f"board, so it is not offered again")
        if new is None and st in (READY, NEEDS_CUT) and str(it.get("evergreen_until") or "") < now.date().isoformat():
            new, why = EXPIRED, f"evergreen until {it.get('evergreen_until')}"
        if new and new != st:
            if write:
                if it.get("uses"):
                    it["uses"][-1]["outcome"] = new
                out.append(set_state(it, new, why))
            else:
                out.append(f"  {it['id']}: would be {new} ({why})")
    return out


# ------------------------------------------------------------------------------------------------------ status
def ready_items(cfg: C.Config, acct: C.Account, now: datetime, shas: Optional[Dict[str, str]] = None,
                hist: Optional[dict] = None) -> List[dict]:
    """The items a fill could still place: ready, not past their date, their clip on no board (place refuses a
    SHA-256 that is on one), and not gone out by the fill's own went_out checks (its slug or moment already on a board
    or posted: the fill rejects such an item, so it is no stock whatever its state says; 10-24/10-25 status said 8
    ready where the dry run placed 1)."""
    shas = _board_shas(cfg) if shas is None else shas
    hist = history(cfg, acct) if hist is None else hist
    return [i for i in items(acct) if i.get("state") == READY
            and str(i.get("evergreen_until") or "") >= now.date().isoformat() and (i.get("sha256") or "") not in shas
            and not went_out(cfg, acct, i["slug"], str(i.get("job") or ""), i.get("span"), _item_keys(i), now.date(),
                             hist, skip_id=i["id"], bumped=i.get("bumped_from"))]


def to_make(cfg: C.Config, acct: C.Account, now: datetime, shas: Optional[Dict[str, str]] = None,
            ready: Optional[List[dict]] = None) -> int:
    """How many the builder should make: up to the target, never past bench.max (ready, needs-cut and placed count;
    a ready item that can never be placed is no stock and leaves room)."""
    st = settings(acct)
    ready = ready_items(cfg, acct, now, shas) if ready is None else ready
    ok = {i["id"] for i in ready}
    stock = sum(1 for i in items(acct) if i.get("state") in STOCK and (i.get("state") != READY or i["id"] in ok))
    return max(0, min(int(st["target"]) - len(ready), int(st["max"]) - stock))


def status(cfg: C.Config, acct: Optional[C.Account] = None, now: Optional[datetime] = None) -> List[str]:
    now = now or cfg.now()
    out = []
    shas = _board_shas(cfg)
    for a in ([acct] if acct else [x for x in cfg.accounts.values() if x.raw.get("owner") != "client"]):
        st = settings(a)
        its = items(a)
        ready = ready_items(cfg, a, now, shas)
        line = f"{a.name} bench: {len(ready)} ready (keep {st['low']} to {st['target']})"
        n = {s: sum(1 for i in its if i.get("state") == s) for s in STATES}
        n[READY] -= len(ready)                   # a ready item that cannot be placed any more shows as such
        line += "".join(f", {v} {k}" for k, v in n.items() if v and k != READY)
        if n[READY]:
            line += f", {n[READY]} ready but not placeable (past its date, or its clip or moment on a board or posted)"
        line += f"; to make: {to_make(cfg, a, now, shas, ready)}"
        if len(ready) < int(st["low"]):
            line += "  <- low: the 10:00 bench builder makes more"
        if not st.get("fill"):
            line += "  (bench.fill is off: nothing is placed)"
        out.append(line)
        for i in its:
            if i.get("state") in (RETIRED, EXPIRED) and not acct:
                continue
            uses = ", ".join(f"{u.get('date')} {u.get('slot')} {u.get('outcome') or 'placed'}" for u in i.get("uses") or [])
            out.append(f"  {i['id']:<48} {i.get('state'):<9} {float(i.get('seconds') or 0):>4.0f}s {i.get('format', ''):<8} "
                       f"until {i.get('evergreen_until')}  {(i.get('source') or {}).get('host', '')}"
                       + (f"  [{uses}]" if uses else "")
                       + ("  needs: " + ", ".join(i.get("needs") or {}) if i.get("state") == NEEDS_CUT else ""))
        out += reconcile(cfg, a, now, write=False)
    return out


def doctor_lines(cfg: C.Config, now: Optional[datetime] = None) -> List[str]:
    now = now or cfg.now()
    out = []
    shas = _board_shas(cfg)
    for a in cfg.accounts.values():
        if a.raw.get("owner") == "client":
            continue
        st = settings(a)
        ready = ready_items(cfg, a, now, shas)
        out.append(f"{a.name}: {len(ready)} spare clip(s) ready (keep {st['low']} to {st['target']})"
                   + ("" if st.get("fill") else "; bench.fill is off"))
    return out


def low_lines(cfg: C.Config, now: Optional[datetime] = None) -> List[str]:
    now = now or cfg.now()
    out = []
    shas = None
    for a in cfg.accounts.values():
        if a.raw.get("owner") == "client" or not settings(a).get("fill"):
            continue
        st = settings(a)
        shas = _board_shas(cfg) if shas is None else shas
        ready = ready_items(cfg, a, now, shas)
        if len(ready) < int(st["low"]):
            out.append(f"  {a.name} bench: {len(ready)} spare clip(s) left (the 10:00 builder keeps {st['low']} to "
                       f"{st['target']})")
    return out


def retire(cfg: C.Config, ident: str, why: str = "") -> List[str]:
    acct, it = find(cfg, ident)
    was = it.get("state")
    if was == RETIRED:
        return [f"  {ident}: retired already"]
    lines = [set_state(it, RETIRED, why or "retired by hand")]
    if was == PLACED and it.get("uses"):
        u = it["uses"][-1]
        lines.append(f"  it is still on {acct.name} {u.get('date')} {u.get('slot')}: Decline it on its card to take "
                     f"it off that board")
    return lines


# ------------------------------------------------------------------------------------------------------ fill
def _log(acct: C.Account, event: str, **kw) -> None:
    try:
        root(acct).mkdir(parents=True, exist_ok=True)
        with open(root(acct) / "log.jsonl", "a") as f:
            f.write(json.dumps({"at": B.utcnow(), "event": event, **kw}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _busy() -> List[str]:
    from . import nightretry as NR
    return NR.busy_now()


def _markers(acct: C.Account) -> List[str]:
    return [str(acct.skill_dir), str(acct.account_dir), f" {acct.slug}", f"/{acct.skill_dir.name}/"]


def _busy_for(acct: C.Account, lines: List[str], cfg: C.Config) -> str:
    """The first render process that may be this account's ('' when none). A process that names only ANOTHER
    account's skill or folder (the GTA worker's run.py, its ffmpeg under the skill's work/) does not hold this one up;
    one that names no account (a Politics build_all.py run by its relative name) holds up every account."""
    others = [m for a in cfg.accounts.values() if a.slug != acct.slug for m in _markers(a)]
    mine = _markers(acct)
    for ln in lines:
        if any(m in ln for m in mine) or not any(m in ln for m in others):
            return ln[:160]
    return ""


@contextlib.contextmanager
def _fill_lock(acct: C.Account):
    root(acct).mkdir(parents=True, exist_ok=True)
    f = open(root(acct) / ".fill.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def _sig(acct: C.Account, day: date) -> tuple:
    """What a gap-fill or a collect changes as it works, for the quiet rule: the day board's slots (state, clip, and
    what collect has looked at: the poster's notes and posts do not count), Publish/schedule.json, and the day's
    delivery folders (the skill's day folder, the factory's own staging)."""
    bd = B.read(acct, day) if B.board_path(acct, day).exists() else None
    out: list = [tuple(sorted((k, s.get("status"), s.get("slug"), (s.get("clip") or {}).get("sha256"),
                               s.get("source_folder")) for k, s in ((bd or {}).get("slots") or {}).items())),
                 len((bd or {}).get("seen") or [])]
    p = acct.account_dir / "Publish" / "schedule.json"
    try:
        st = p.stat()
        out.append((st.st_mtime_ns, st.st_size))
    except OSError:
        out.append(None)
    for d in list(acct.delivered_dirs(day)) + [C.STATE / acct.slug / "delivered" / day.isoformat()]:
        try:
            out.append(tuple(sorted((e.name, e.stat().st_mtime_ns) for e in os.scandir(d))))
        except OSError:
            out.append(None)
    return tuple(out)


def fill(cfg: C.Config, which: str, day: date, dry_run: bool = False, slots: Optional[List[str]] = None,
         now: Optional[Callable[[], datetime]] = None, sleep: Callable[[float], None] = time.sleep,
         busy: Callable[[], List[str]] = _busy, say: Callable[[str], None] = print,
         rotation: Optional[Callable] = None, quiet_s: int = QUIET_S) -> List[str]:
    """The fill. Returns what it said (and says each line as it goes, for the launchd log).

    Each account is filled once it is QUIET: nothing of it renders (busy_now: a render that names only the other
    account does not count; one that names no account counts for both), no worker of it runs, and its board,
    Publish/schedule.json and the day's delivery folders have not changed for quiet_s seconds. A Claude gap-fill
    between two steps (stage.py, day_folder.py, before its collect) renders nothing, so the process check alone cannot
    see it; what it writes, this can. Not quiet by 06:22: nothing is filled for that account."""
    from . import makers as M
    clock = now or cfg.now
    lines: List[str] = []

    def tell(s: str) -> None:
        lines.append(s)
        say(s)

    accts = ([a for a in cfg.accounts.values() if a.raw.get("owner") != "client" or (a.raw.get("bench") or {}).get("fill")]
             if which == "all" else [cfg.account(which)])
    t = clock()
    if not dry_run:
        start, stop = _hm(cfg, t.date(), NOT_BEFORE), _hm(cfg, t.date(), STOP_AT)
        if t.date() != day or not start <= t < stop:
            tell(f"bench fill {day}: not run at {t:%Y-%m-%d %H:%M}; it fills its own posting day between {NOT_BEFORE} "
                 f"and {STOP_AT} only (after the night shift, before the notice): --dry-run shows what it would do")
            return lines
    quiet_by = _hm(cfg, t.date(), QUIET_BY)
    rot = rotation or politics_rotation

    def run(a: C.Account) -> None:
        lines_a = _fill_account(cfg, a, day, dry_run, slots, clock, tell, rot)
        if not dry_run and lines_a:
            _note_board(cfg, a, day, lines_a, clock)

    pending = []
    for a in accts:
        if not settings(a).get("fill") and not dry_run:
            tell(f"{a.name}: bench.fill is off in config/accounts.json; nothing placed")
        else:
            pending.append(a)
    sigs: Dict[str, tuple] = {}
    since: Dict[str, datetime] = {}
    said_wait = False
    while pending:
        b = busy()
        t_now = clock()
        waits: Dict[str, str] = {}
        for a in list(pending):
            why = _busy_for(a, b, cfg) or (f"the {a.name} worker" if M.other_worker_running(a) else "")
            sig = _sig(a, day)
            if why or sig != sigs.get(a.slug):
                sigs[a.slug], since[a.slug] = sig, t_now
            calm = (t_now - since[a.slug]).total_seconds()
            if why:
                waits[a.slug] = why
            elif calm < quiet_s and not dry_run:
                waits[a.slug] = (f"quiet for {int(calm)} s of the {quiet_s} s it needs (no render, no worker, and "
                                 f"its board, Publish/schedule.json and the day's delivery folders unchanged)")
            else:
                pending.remove(a)
                run(a)
        if not pending:
            break
        if dry_run:
            for a in pending:
                tell(f"(dry run) {a.name}: the Mac is busy now ({waits[a.slug]}); the 06:18 fill waits for quiet, at "
                     f"most until {QUIET_BY}")
                run(a)
            break
        if clock() >= quiet_by:
            for a in pending:
                w = waits.get(a.slug, "")
                why = (f"it was not quiet by {QUIET_BY}: {w}" if w.startswith("quiet for ")
                       else f"the Mac was still busy at {QUIET_BY} ({w})")
                tell(f"{a.name} {day}: nothing filled: {why}")
                _log(a, "fill", day=day.isoformat(), placed=[], why=why)
                if B.read(a, day) is not None:            # the morning message says why the bench did nothing
                    _note_board(cfg, a, day, [f"nothing filled: {why}"], clock)
            break
        if not said_wait:
            tell("waiting for quiet: " + "; ".join(f"{a.name}: {waits[a.slug]}" for a in pending))
            said_wait = True
        sleep(POLL_S)
    return lines


REPORT_TAG = "Bench 06:18:"
OLD_TAGS = ("Bench 05:05:",)    # the line's name before the night shift (2026-10-19): replaced the same way


def summary(said: List[str]) -> str:
    """'Bench 06:18: 14:00 and 15:45 from the bench; 17:30 nothing fits.' (the board report's last line)."""
    got = [ln.strip()[:5] for ln in said if ": from the bench, " in ln and re.match(r"^\s*\d\d:\d\d: ", ln)]
    none = [ln.strip()[:5] for ln in said if ": nothing on the bench fits" in ln and re.match(r"^\s*\d\d:\d\d: ", ln)]
    kept = [ln.strip()[:5] for ln in said if re.match(r"^\s*\d\d:\d\d: kept for ", ln)]
    parts = []
    if got:
        parts.append(" and ".join(got) + " from the bench")
    if none:
        parts.append(" and ".join(none) + " nothing on the bench fits")
    if kept:
        parts.append(" and ".join(kept) + " left for a clip the night planned (not on the board yet)")
    if not parts:
        parts.append(said[0].strip()[:200])
    return f"{REPORT_TAG} " + "; ".join(parts) + "."


def _note_board(cfg: C.Config, acct: C.Account, day: date, said: List[str], clock) -> None:
    """What the bench did, on the board: in full (bench_report) and as the last line of the night's report, which
    the review page and the 06:30 message show first."""
    if not said or clock() >= _hm(cfg, day, STOP_AT):
        return
    try:
        with B.edit(cfg, acct, day) as bd:
            bd["bench_report"] = "; ".join(s.strip() for s in said)[:1500]
            bd["bench_report_at"] = B.utcnow()
            kept = [ln for ln in str(bd.get("report") or "").splitlines()
                    if not ln.startswith((REPORT_TAG,) + OLD_TAGS)]
            if not any("no open or failed slot" in ln for ln in said):   # a full board needs no line from the bench
                kept.append(summary(said))
            bd["report"] = "\n".join(kept).strip()[:4000]
    except OSError:
        pass


def _verify_files(it: dict) -> str:
    d = Path(it["_dir"])
    f = it.get("files") or {}
    p = d / str(f.get("clip") or "")
    if not p.is_file():
        return f"the clip is gone ({p.name})"
    if B.sha256_file(p) != it.get("sha256"):
        return "the clip changed on the bench"
    for n, h in list((f.get("words") or {}).items()) + list((f.get("templates") or {}).items()):
        if not (d / n).is_file() or B.sha256_text((d / n).read_text()) != h:
            return f"its words changed on the bench ({n})"
    for plat, c in (f.get("cuts") or {}).items():
        if not (d / c["name"]).is_file() or B.sha256_file(d / c["name"]) != c.get("sha256"):
            return f"its {plat} cut changed on the bench"
        if c.get("words_sha256") and (not (d / str(c.get("words") or "")).is_file()
                                      or B.sha256_text((d / c["words"]).read_text()) != c["words_sha256"]):
            return f"its {plat} cut's words changed on the bench ({c.get('words')})"
    # place copies every cut (and its .cut.txt) in the folder: one nobody recorded would go out unchecked
    have = {p: (x["mp4"].name, x["words"].name if x.get("words") else "") for p, x in _cut_files(d).items()}
    want = {p: (c.get("name"), c.get("words") or "") for p, c in (f.get("cuts") or {}).items()}
    if have != want:
        return f"its cuts on the bench are not the ones recorded ({sorted(have)} on disk, {sorted(want)} recorded)"
    return ""


def _slot_sources(acct: C.Account, s: dict, sched: dict, day: str) -> set:
    urls = [s.get("source_url")] + list(s.get("source_urls") or [])
    if acct.maker == "politics" and s.get("slug"):
        job = _job_of(acct, s, sched, day)
        if job:
            urls.append(_read(acct.account_dir / "Jobs" / job / "project.json").get("source_url"))
    return _keys([u for u in urls if u])


def planned_pending(acct: C.Account, day: date, board: dict, sched: dict) -> Dict[str, str]:
    """slot -> why the fill leaves it alone: Publish/schedule.json plans a clip for the day that is not on the board
    yet and that collect has not looked at (a gap-fill may still deliver it: it renders nothing between its steps).
    Its own slot when that slot can still take it; otherwise collect moves it to the first open slot (the Politics: then
    the first failed one; GTA: the first open one that is not a REACTIONS slot, and only when its time is not a slot
    today), so that one is kept for it. A slot whose planned clip collect already took or turned away is not kept."""
    ds = day.isoformat()
    slots = board.get("slots") or {}
    on = {s.get("slug") for s in slots.values() if s.get("slug")}
    looked = _seen_slugs(board)
    out: Dict[str, str] = {}
    floating: List[str] = []
    day_plan = sched.get(ds) if isinstance(sched.get(ds), dict) else {}
    for k, e in sorted(day_plan.items()):
        if not isinstance(e, dict) or not e.get("slug") or e["slug"] in on or e["slug"] in looked:
            continue
        try:
            key = C.normal_slot(str(k))
        except SystemExit:
            key = str(k)
        s = slots.get(key)
        if s is not None and s.get("status") in B.FILLABLE:
            out[key] = (f"kept for {e['slug']}: Publish/schedule.json plans it here and it is not on the board yet "
                        f"(the gap-fill may still deliver it)")
        elif s is not None and acct.maker == "gta":
            continue                       # GTA: a clip only fills the slot it was cut for; it waits, it never moves
        else:
            floating.append(e["slug"])
    by_time = sorted(slots.values(), key=lambda s: s["slot"])
    if not B.spares_may_fill({"slots": slots}):
        # a spare slot (16 to 20) is not free until every base slot holds a clip (2026-10-21)
        by_time = [s for s in by_time if not B.is_spare(s)]
    free = [s["slot"] for s in by_time if s.get("status") == B.OPEN and s["slot"] not in out
            and not (acct.maker == "gta" and s.get("format") == "reactions")]
    if acct.maker != "gta":
        free += [s["slot"] for s in by_time if s.get("status") in B.FILLABLE and s.get("status") != B.OPEN
                 and s["slot"] not in out]
    for slug, k in zip(floating, free):
        out[k] = (f"kept for {slug}: Publish/schedule.json plans it for the day, not in a slot that can take it, and "
                  f"collect puts it in the first open slot")
    return out


def _fill_account(cfg, acct, day, dry_run, only, clock, tell, rotation) -> List[str]:
    from . import makers as M
    said: List[str] = []

    def out(s: str) -> None:
        said.append(s)
        tell(s)

    st = settings(acct)
    if not st.get("fill"):
        if not dry_run:
            tell(f"{acct.name}: bench.fill is off in config/accounts.json; nothing placed")
            return said
        tell(f"(dry run) {acct.name}: bench.fill is off, so the 06:18 fill places nothing until it is switched on; "
             f"this is what it would place")
    with (_fill_lock(acct) if not dry_run else contextlib.nullcontext(True)) as got:
        if not got:
            tell(f"{acct.name}: another bench fill is running; not starting a second")
            return said
        now = clock()
        for ln in reconcile(cfg, acct, now, write=not dry_run):
            tell(ln)
        board = B.read(acct, day) or B.new_board(cfg, acct, day)
        sched = _sched(acct)
        held = M.reactions_held(cfg, acct, day, board, now=now) if acct.maker == "gta" else set()
        kept = planned_pending(acct, day, board, sched)
        lead = timedelta(minutes=max(LEAD_MIN, int(st.get("lead_minutes") or LEAD_MIN)))
        todo = []
        spares_wait = not B.spares_may_fill(board)      # a spare slot (16 to 20) only once every base slot holds a clip
        waiting = []
        for k, s in sorted(board["slots"].items()):
            if only and k not in only:
                continue
            if s.get("status") not in (B.OPEN, B.FAILED):
                continue
            if spares_wait and B.is_spare(s) and not only:      # a person who names a spare slot gets it
                waiting.append(k)
                continue
            if k in held:
                out(f"  {k}: held for the day's REACTIONS clip until {M.gta_settings(acct).get('reactions_hold_until')}; "
                    f"no spare goes there before then")
                continue
            if cfg.slot_time(day, k) - now < lead:
                out(f"  {k}: {int((cfg.slot_time(day, k) - now).total_seconds() // 60)} minutes away (a spare needs "
                    f"{int(lead.total_seconds() // 60)}); left as it is")
                continue
            if k in kept:
                out(f"  {k}: {kept[k]}; left as it is")
                continue
            todo.append(k)
        if waiting:
            out(f"  spare slots {', '.join(waiting)}: wait until every base slot holds a clip")
        cands = [i for i in items(acct) if i.get("state") == READY
                 and str(i.get("evergreen_until") or "") >= day.isoformat() and str(i.get("made_on") or "") <= day.isoformat()]
        if not todo:
            out(f"{acct.name} {day}: no open or failed slot 90+ minutes away; the bench has {len(cands)} ready")
            return said
        if not cands:
            out(f"{acct.name} {day}: {len(todo)} slot(s) empty ({', '.join(todo)}), and no spare clip is ready "
                f"(factory.py bench status)")
            return said
        taken: set = set()
        for k, s in board["slots"].items():
            if s.get("status") in (B.READY, B.APPROVED, B.SCHEDULED, B.MAKING):
                taken |= _slot_sources(acct, s, sched, day.isoformat())
        slugs_today = {s.get("slug") for s in board["slots"].values() if s.get("slug")}
        reels_today = set(_read(acct.account_dir / "Publish" / f"broll-used-{day.isoformat()}.json").get("sources") or [])
        shas = _board_shas(cfg)
        hist = history(cfg, acct)
        # every candidate once, before any slot: its files, then the add's checks again against everything since it
        # was added (the night may have cut and posted the same clip): any hit and it is rejected for good
        for it in list(cands):
            bad = _verify_files(it)
            if bad:
                cands.remove(it)
                out(set_state(it, BROKEN, bad) if not dry_run else f"  {it['id']}: would be broken ({bad})")
                continue
            where = shas.get(it.get("sha256") or "")
            gone = (f"already on a board ({where})" if where else
                    went_out(cfg, acct, it["slug"], str(it.get("job") or ""), it.get("span"), _item_keys(it), day,
                             hist, skip_id=it["id"], bumped=it.get("bumped_from")))
            if gone:
                cands.remove(it)
                out(set_state(it, REJECTED, gone) if not dry_run else f"  {it['id']}: would be rejected ({gone})")
        if not cands:
            out(f"{acct.name} {day}: {len(todo)} slot(s) empty ({', '.join(todo)}), and no spare clip on the bench can "
                f"go out (factory.py bench status)")
            return said
        placed = []
        last_write = _hm(cfg, day, STOP_AT) - timedelta(seconds=LAST_START_S)   # a placement copies files

        def left_s() -> float:
            """Seconds a helper may run: its own timeout, never past the last start (a dry run has no deadline)."""
            return float(HELPER_S) if dry_run else max(1.0, min(HELPER_S, (last_write - clock()).total_seconds()))

        stop = False
        for k in todo:
            if stop:
                break
            if not dry_run and clock() >= last_write:
                out(f"  {k} and later: not filled; it is {STOP_AT} (the fill writes nothing after it)")
                break
            why_not = []
            for it in list(cands):
                w, gone = _why_not(cfg, acct, day, k, it, taken, slugs_today, reels_today, shas, board, sched, rotation,
                                   left_s)
                if gone:                       # it went out (or is on its way) since it was added: never again
                    cands.remove(it)
                    why_not.append(f"{it['id']}: {w}")
                    tell(set_state(it, REJECTED, w) if not dry_run else f"  {it['id']}: would be rejected ({w})")
                    continue
                if w:
                    why_not.append(f"{it['id']}: {w}")
                    continue
                if not dry_run and clock() >= last_write:      # checked before EVERY placement, not once a slot
                    out(f"  {k} and later: not filled; it is {STOP_AT} (the fill writes nothing after it)"
                        + (f"; tried first: {'; '.join(why_not)[:500]}" if why_not else ""))
                    stop = True
                    break
                ok, msg = _place(cfg, acct, day, k, it, now=clock(), dry_run=dry_run, timeout=left_s(),
                                 clock=None if dry_run else clock,
                                 deadline=None if dry_run else _hm(cfg, day, STOP_AT) - timedelta(seconds=10))
                if ok:
                    out(f"  {k}: from the bench, {it['id']} ({it.get('format')}, {float(it.get('seconds') or 0):.0f}s"
                        + (f", {(it.get('source') or {}).get('host')}" if (it.get('source') or {}).get('host') else "")
                        + f", good until {it.get('evergreen_until')})" + (" (dry run: nothing written)" if dry_run else "")
                        + (f"; passed over {'; '.join(why_not)[:500]}" if why_not else ""))
                    cands.remove(it)
                    taken |= _item_keys(it)
                    slugs_today.add(it["slug"])
                    reels_today |= set((it.get("gta") or {}).get("reel_sources") or [])
                    shas[it.get("sha256") or ""] = f"{acct.name} {day} {k}"
                    placed.append(k)
                    if not dry_run:                # the day rule of the next slot sees this one
                        board = B.read(acct, day) or board
                    else:                          # a dry run writes nothing: the next slot sees it on a copy
                        board = json.loads(json.dumps(board))
                        board["slots"][k].update(status=B.READY, slug=it["slug"], format=it.get("format") or "",
                                                 bench={"id": it["id"], "slug": it["slug"], "job": it.get("job") or ""},
                                                 clip={"sha256": it.get("sha256"), "seconds": it.get("seconds")})
                    break
                why_not.append(f"{it['id']}: {msg}")
                if "minutes away" in msg or "just filled" in msg or "only an open or failed slot" in msg:
                    break
            if k not in placed and not stop:
                out(f"  {k}: nothing on the bench fits" + (f" ({'; '.join(why_not)[:600]})" if why_not else ""))
        if not dry_run:
            _log(acct, "fill", day=day.isoformat(), placed=placed, empty=[k for k in todo if k not in placed])
    return said


def _board_shas(cfg: C.Config) -> Dict[str, str]:
    """Every clip and cut SHA-256 on every board of every account (place.on_boards, read once per fill)."""
    out: Dict[str, str] = {}
    for a in cfg.accounts.values():
        for bp in B.all_boards(a):
            for s in (_read(bp).get("slots") or {}).values():
                for h in B.clip_shas(s):
                    if h:
                        out.setdefault(h, f"{a.name} {bp.stem} {s.get('slot')} ({s.get('status')})")
    return out


def _why_not(cfg, acct, day, k, it, taken, slugs_today, reels_today, shas, board, sched, rotation,
             left_s: Callable[[], float] = lambda: float(HELPER_S)) -> Tuple[str, bool]:
    """(why this item does not go into slot k, gone). gone True: the same clip went on a board in this fill, or its
    clip is on one (the went_out checks ran once before the slots): rejected for good. gone False: not on this slot or
    this day (the day's source, its reel, the day rule, a cast unchecked since)."""
    where = shas.get(it.get("sha256") or "")
    if where:
        return f"already on a board ({where})", True
    if it.get("slug") in slugs_today:
        return "the same clip went on the day a moment ago (another bench item)", True
    if _item_keys(it) & taken:
        return "its source is already on the day (one source at most once a day)", False
    if acct.maker == "gta":
        rs = set((it.get("gta") or {}).get("reel_sources") or []) & reels_today
        if rs:
            return f"its b-roll reel source {sorted(rs)[0]} is taken that day (Publish/broll-used-{day}.json)", False
        launch = date.fromisoformat(str(settings(acct).get("launch") or "2026-11-19"))
        if it.get("words_from") != "skill" and day >= launch:
            return (f"its stored words were written before the launch ({launch}); a clip past it needs the skill's "
                    f"words"), False
    if acct.maker == "politics":
        slate = _politics_slate(acct, str(it.get("job") or ""), it["slug"])[0]
        if not slate:
            return f"its job's slate is gone ({it.get('job')}): the day rule cannot be read", False
        cast = politics_cast(acct, str(it.get("job") or ""), slate)
        if cast["status"] != "verified":
            return f"its cast is {cast['status']} now ({cast['why']})", False
        try:
            new = rotation(cfg, acct, day, k, it, board, sched, timeout=left_s())
        except Exception as e:  # noqa: BLE001  (a helper that cannot answer refuses the clip, never lets it through)
            return f"the day rule could not be checked ({type(e).__name__}: {str(e)[:160]})", False
        if new:
            return f"the day rule says no: {new[0][:200]}", False
    return "", False


ENGINE_GONE = ("copy engine", "has no clip", "no Publish/day_folder.py")


def _place(cfg, acct, day, k, it, now: datetime, dry_run: bool, timeout: float = HELPER_S,
           clock: Optional[Callable[[], datetime]] = None, deadline: Optional[datetime] = None) -> Tuple[bool, str]:
    """One item into slot k through place.py. `deadline` (the fill: 06:25 less a few seconds): the words are written
    first, and when the clock has passed it by then, nothing is placed."""
    from . import place as PL
    d = Path(it["_dir"])
    f = it.get("files") or {}
    clip = d / f["clip"]
    cover = d / f["cover"] if f.get("cover") else None
    src = it.get("source") or {}
    urls = list(src.get("urls") or ([it["source_url"]] if it.get("source_url") else []))
    why = f"from the bench: {it['id']}"
    host = src.get("host") or None
    tmp = Path(tempfile.mkdtemp(prefix="clipfactory-bench-words-"))
    try:
        words, how = None, ""
        if acct.maker == "gta" and it.get("words_from") == "skill" and it.get("job"):
            # the skill's copy engine writes the posting day's words (its countdown), as place --job does, with the
            # time left before 06:24:30 as its timeout (a hung engine never runs into the notice)
            (tmp / "engine").mkdir()
            try:
                info = PL.skill_words(acct, it["job"], it["slug"], day, tmp / "engine", timeout=max(1, int(timeout)))
            except PL.Refused as e:
                if not any(w in str(e) for w in ENGINE_GONE) or "ran past" in str(e):
                    return False, str(e)[:300]
            else:
                if info.get("question_default"):
                    return False, ("the comment question is the skill's stock one now (its slate changed since the "
                                   "add): write the clip's own question on its slate")
                words, how = tmp / "engine", "the skill's copy engine"
        if words is None and acct.maker == "gta":
            launch = date.fromisoformat(str(settings(acct).get("launch") or "2026-11-19"))
            if day >= launch:
                return False, f"the skill's copy engine is not there and {day} is past the launch: no words"
            (tmp / "stored").mkdir()
            for n, tn in (it.get("words_template") or {}).items():
                (tmp / "stored" / n).write_text(_render_words((d / tn).read_text(), day, launch))
            words, how = tmp / "stored", "the stored words, the countdown for the day"
        elif words is None:
            words, how = d, "the stored words"
        if deadline is not None and clock is not None:
            now = clock()
            if now >= deadline:
                return False, f"it is {now:%H:%M:%S}: the words took too long, nothing placed after {STOP_AT}"
        ok, lines = PL.place(cfg, acct, day, k, clip=clip, words=words, cover=cover, sources=urls, why=why,
                             fmt=it.get("format") or None, host=host, check=dry_run, now=now)
        return _after(cfg, acct, day, k, it, ok, lines, dry_run, now, how)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _after(cfg, acct, day, k, it, ok, lines, dry_run, now, how) -> Tuple[bool, str]:
    msg = " ".join(ln.strip() for ln in lines if ln.strip())
    if not ok:
        return False, msg.replace("REFUSED, nothing written: ", "")[:300]
    if dry_run:
        return True, msg
    src = it.get("source") or {}
    with B.edit(cfg, acct, day) as bd:
        s = bd["slots"][k]
        if s.get("slug") != it["slug"] or (s.get("clip") or {}).get("sha256") != it.get("sha256"):
            return False, "the slot holds another clip right after the placement"
        s["bench"] = {"id": it["id"], "slug": it["slug"], "job": it.get("job") or "", "made_on": it.get("made_on"),
                      "evergreen_until": it.get("evergreen_until"), "kind": it.get("kind") or "",
                      "host": src.get("host") or "", "placed_at": B.utcnow(), "words": how}
        if src.get("rights"):
            s["rights"] = src["rights"]
        if src.get("title") and not s.get("source_title"):
            s["source_title"] = src["title"]
        if it.get("job") and not s.get("job"):
            s["job"] = it["job"]
        B.note(s, f"from the bench: {it['id']}, cut {it.get('made_on')} from {src.get('host') or 'its source'}, "
                  f"good until {it.get('evergreen_until')} (words: {how})")
    it.setdefault("uses", []).append({"date": day.isoformat(), "slot": k, "at": B.utcnow(), "outcome": ""})
    set_state(it, PLACED, f"{day} {k}")
    _log(acct, "placed", id=it["id"], day=day.isoformat(), slot=k)
    return True, msg


# ------------------------------------------------------------------------------------------------------ the day rule
ROTATION = r'''
import contextlib, io, json, sys
from pathlib import Path
spec = json.loads(Path(sys.argv[1]).read_text())
sys.path.insert(0, ".")
import rotation as R
R.JOBS = Path(spec["jobs"])
_fp = R.fingerprint
def _fingerprint(e):
    if getattr(e, "stub", False):        # a clip with no slate to read: never "the same" as another on any axis
        return ({ax: "stub %s %s %s" % (e.day, e.slot, ax) for ax in R.DAY_AXES}, [])
    return _fp(e)
R.fingerprint = _fingerprint
def entry(day, x):
    if not x.get("stub"):
        e = R.Entry(day, x["slot"], x["job"], x["slug"])
        if not e.error or x.get("candidate"):
            return e
    e = R.Entry.__new__(R.Entry)
    e.day, e.slot, e.job, e.slug = day, x["slot"], x.get("job") or "", x.get("slug") or ""
    e.clip = {"template": x.get("template") or ""}
    e.cfg = {"source_url": x.get("source_url") or ("slot %s %s" % (day, x["slot"]))}
    e.error = None
    e.stub = True
    return e
def fails(days, day):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        es = days.get(day, [])
        R.check_same_day(day, es)
        R.check_sources(day, es)
        R.check_variety({d: [e for e in lst if not getattr(e, "stub", False)] for d, lst in days.items()}, day)
    return [ln.strip() for ln in buf.getvalue().splitlines() if "[FAIL]" in ln]
day = spec["day"]
days = {d: [entry(d, x) for x in xs] for d, xs in spec["days"].items()}
cand = entry(day, dict(spec["candidate"], candidate=True))
if cand.error:
    print(json.dumps({"error": cand.error}))
    sys.exit(0)
before = fails(days, day)
days.setdefault(day, []).append(cand)
after = fails(days, day)
print(json.dumps({"before": before, "after": after}))
'''


def rotation_days(cfg: C.Config, acct: C.Account, day: date, slot_key: str, board: dict, sched: dict) -> dict:
    """{date: [entry]} for the 7 days up to `day`: the boards' clips that are (or will be) out, plus Publish/
    schedule.json's clips that are on no board (a clip the board refused or a person declined is not on the day).
    An entry whose job cannot be found is a stub: its segment and source only."""
    days: Dict[str, list] = {}
    for n in range(7):
        d = day - timedelta(days=n)
        ds = d.isoformat()
        bd = board if d == day else (B.read(acct, d) or {})
        slots = (bd.get("slots") or {})
        on_board = {s.get("slug"): s for s in slots.values() if s.get("slug")}
        rows = []
        for k, s in sorted(slots.items()):
            if s.get("status") not in (B.READY, B.APPROVED, B.SCHEDULED) or not s.get("slug"):
                continue
            job = _job_of(acct, s, sched, ds)
            if job and (acct.account_dir / "Jobs" / job / "slate.json").is_file():
                rows.append({"slot": k, "job": job, "slug": s["slug"]})
            else:
                rows.append({"slot": k, "slug": s["slug"], "stub": True, "template": str(s.get("format") or ""),
                             "source_url": str(s.get("source_url") or "")})
        for k, e in sorted((sched.get(ds) or {}).items()):
            if not isinstance(e, dict) or not e.get("slug") or e["slug"] in on_board:
                continue
            if d == day and k == slot_key:
                # the slot the spare goes into: what was planned there never came (a planned clip collect has not
                # looked at yet keeps its slot: planned_pending, so the fill never asks the rule about that slot)
                continue
            rows.append({"slot": k, "job": str(e.get("job") or ""), "slug": e["slug"]})
        if rows:
            days[ds] = rows
    return days


def politics_rotation(cfg: C.Config, acct: C.Account, day: date, slot_key: str, it: dict, board: dict,
                    sched: dict, timeout: float = HELPER_S, jobs: Optional[Path] = None) -> List[str]:
    """The FAIL lines viral-clip-engine's rotation.py prints for the day with this spare in slot_key that it does not
    print without it (empty = the day rule allows it). Raises when the helper cannot answer (a timeout included:
    the fill gives it only the time left before 06:24:30). jobs: another Jobs folder to read (a breaking desk proof's,
    breaking.day_rule)."""
    from . import makers as M
    spec = {"jobs": str(jobs or acct.account_dir / "Jobs"), "day": day.isoformat(),
            "days": rotation_days(cfg, acct, day, slot_key, board, sched),
            "candidate": {"slot": slot_key, "job": it.get("job") or "", "slug": it["slug"]}}
    scripts = acct.skill_dir / "scripts"
    if not (scripts / "rotation.py").is_file():
        raise RuntimeError(f"no rotation.py in {scripts}")
    with tempfile.TemporaryDirectory(prefix="clipfactory-rotation-") as td:
        sp = Path(td) / "spec.json"
        sp.write_text(json.dumps(spec))
        r = subprocess.run([M.PY_SKILLS, "-c", ROTATION, str(sp)], capture_output=True, text=True,
                           timeout=max(1.0, float(timeout)), cwd=str(scripts))
    if r.returncode != 0:
        raise RuntimeError(((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1])
    got = json.loads((r.stdout or "").strip().splitlines()[-1])
    if got.get("error"):
        raise RuntimeError(got["error"])
    before = list(got.get("before") or [])
    return [ln for ln in got.get("after") or [] if ln not in before]


# ------------------------------------------------------------------------------------------------------ render (GTA)
def render(cfg: C.Config, acct: C.Account, job: str, slug: str, until: str = RENDER_UNTIL,
           now: Optional[datetime] = None, runner: Optional[Callable] = None) -> Tuple[bool, List[str]]:
    """GTA: render one slated clip of a job for the bench with the skill's own deliver stage (preflight, render,
    grade, words), and NO slot: the job's posting date is taken off first, so run.py writes no Publish/schedule.json
    entry and builds no Desktop run folder (a clip under a time that is not a slot would be collected into the first
    open slot). In the render queue, in daylight only. Then look at it and `bench add` it."""
    from . import makers as M
    from . import renderlock as RL
    if acct.maker != "gta":
        return False, [f"  REFUSED: bench render is for Gaming; the Politics renders in its job with build_all.py "
                       f"under the render queue, then `bench add {acct.slug} <job> --slug S` and igcut --bench"]
    t = now or cfg.now()
    lo, hi = _hm(cfg, t.date(), RENDER_FROM), _hm(cfg, t.date(), until)
    if not lo <= t < hi:
        return False, [f"  REFUSED: bench renders run in daylight, {RENDER_FROM} to {until} (it is {t:%H:%M}); the "
                       f"nights belong to the night shift"]
    work = acct.skill_dir / "work" / job
    pj = work / "project.json"
    proj = _read(pj)
    if not proj:
        return False, [f"  REFUSED: no job {job} ({pj})"]
    clip, _ = _gta_slate(acct, job, slug)
    if not clip:
        return False, [f"  REFUSED: {job} has no clip {slug!r} on its slate (stage 2: run.py {job} --pick N)"]
    cc = clip.get("count_card") or {}
    if str(clip.get("template") or "").lower() == "countdown" or (isinstance(cc, dict) and cc.get("number")):
        return False, ["  REFUSED: a countdown card is right on one day only; never on the bench"]
    sched = _sched(acct)
    for d, slots in sched.items():
        for k, e in (slots or {}).items() if isinstance(slots, dict) else []:
            if isinstance(e, dict) and e.get("job") == job:
                return False, [f"  REFUSED: Publish/schedule.json already has {job} at {d} {k}: that job delivers into "
                               f"a slot; make the bench clip in a job of its own"]
    if proj.get("date"):
        proj.setdefault("bench", {})["was_date"] = proj["date"]
        proj.pop("date", None)
    proj.setdefault("bench", {})["rendered_for_bench_at"] = B.utcnow()
    tmp = pj.with_suffix(".json.bench-tmp")
    tmp.write_text(json.dumps(proj, indent=1))
    os.replace(tmp, pj)
    cmd = [M.PY_SKILLS, str(acct.skill_dir / "scripts" / "run.py"), job, "--deliver", "--slug", slug]
    env_before = {k: os.environ.get(k) for k in M.GTA_ENV_KEYS}      # the account's folder and kit, as the night's
    os.environ.update({k: v for k, v in M.gta_env(acct).items() if k in env_before})
    try:
        rc = (runner or RL.run)(acct.slug, f"bench: {slug}", cmd, until=RL.until_epoch(until),
                                names={k: v.name for k, v in cfg.accounts.items()})
    finally:
        for k, v in env_before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    lines = [f"  run.py {job} --deliver --slug {slug}: exit {rc}"]
    after = _sched(acct)
    leaked = [f"{d} {k}" for d, slots in after.items() if isinstance(slots, dict)
              for k, e in slots.items() if isinstance(e, dict) and e.get("job") == job]
    if leaked:
        return False, lines + [f"  STOP: the deliver wrote Publish/schedule.json ({', '.join(leaked)}): take that entry "
                               f"out by hand before 21:00, and any 'Gaming Run' folder it made"]
    if rc == RL.BUSY_EXIT:
        return False, lines + [f"  not rendered: the render queue was still busy at {until}"]
    if rc != 0:
        return False, lines + [f"  the render stopped (exit {rc}): read work/{job}/RUN.md"]
    out_dir = Path(str(proj.get("out_dir") or acct.account_dir / "Jobs" / job))
    from . import place as PL
    got = PL._job_file(out_dir / "clips", slug, "mp4")
    if got is None:
        return False, lines + [f"  no clip for {slug} in {out_dir / 'clips'}"]
    return True, lines + [f"  rendered for the bench: {got}",
                          f"  look at its frames and its sheet, then: factory.py bench add {acct.slug} {job} --slug {slug} "
                          f"--until YYYY-MM-DD --why \"...\""]
