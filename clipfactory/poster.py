"""The poster: what a person signed goes out by itself, at its slot, straight to each platform's own API.

    factory.py post-due [--dry-run]     launchd runs it every 10 minutes; Approve starts one at once (kick)

Each run looks at yesterday's, today's and tomorrow's boards and
  0. signs, under the factory's own name, each ready clip whose slot is 30 minutes away (autosign.py, when
     "auto_sign" is on: the owner's call of 2026-10-19; a pulled clip, or one naming two or more people nobody
     checked, waits for a person),
  1. hands off anything signed but not yet scheduled (outlets.hand_off: re-checks the signature, writes the export),
  2. posts what is due: Facebook Reels, Instagram Reels and TikTok (through Upload-Post) at the slot time, and
     YouTube for a slot whose time has come before it could upload,
  3. uploads to YouTube ahead of time, private with publishAt = the slot, so YouTube publishes it then even if this
     Mac is asleep.

Before every post it re-reads the slot (a Pull while it was busy is honoured) and re-hashes the clip and its words:
anything changed after signing is refused. A platform that fails three times on one slot is left alone, said on the
board; a refusal that trying again cannot change (outside the length band, not connected) is left alone at once.
Nothing posts more than 20 hours late (a person decides), and one platform of one account never gets two posts within
10 minutes, so a Mac that slept catches up one post at a time. One bad slot never stops the others.

What a platform already holds is written on the board before the step that cannot be undone (the YouTube upload
session, the Facebook video, the Instagram container) and the post the moment it is public, so a lost answer or a
crash is looked up on the next run, never posted a second time. A Pull while a post is on its way stops it before
that step; an upload that lands after a Pull is kept on the board with a loud note and a flag for the card.
Each run writes state/post-due.health (health_words: the one red line the review page shows when posting is broken).
A YouTube outlet may spread its uploads ("upload_ahead_hours", "upload_gap_minutes"); without them every signed
slot uploads as soon as it can, as before. With an account's social guard switched on (clipfactory/outward.py), a
platform Meta blocked or asked to slow down waits under a hold, not one of the tries; watch only logs; off, as before.

The bots never run this. It is code that only moves what was signed: by a person, or by its own automatic
sign-off (autosign.py), which runs the same checks Approve does.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from . import board as B
from . import config as C
from . import copytext as T

MAX_TRIES = 3            # a platform that fails three times on one slot is left alone (said so on the board)
LATE_LIMIT = timedelta(hours=20)    # older than this and a slot is not posted late; a person decides
SPACING = timedelta(minutes=10)     # never two posts on one platform of one account closer together than this
RETRY_AFTER = timedelta(minutes=5)  # a failed try waits for a later run (the next tick), not the next pass
# Meta's upload service answering "ProcessingFailedError: Request processing failed" (rupload.facebook.com) fails a
# whole upload on Meta's side, in spells, and far more often for longer clips: the SAME file then goes through on a
# later try (2026-10-18 to 10-20: five failures, then posted; a spell lasts 20 minutes to hours). Three tries ten
# minutes apart gave up inside one spell, so these get more tries, further apart, before a person is asked.
UPLOAD_FAILS = ("ProcessingFailedError",)
UPLOAD_GAPS = (5, 10, 15, 30, 45, 60, 90, 120, 120)   # minutes after each such failed try: 10 tries over about 8 hours
# This Mac stalling, not a platform: ffprobe giving no length (direct.Unreadable). 2026-10-30 06:39-07:15 it hung on
# three accounts' clips while the poster read their bytes fine; the same spacing as Meta's spells.
STALLS = ("could not read the length",)
YT_AHEAD_PER_PASS = 3    # YouTube uploads ahead of time per account per pass; due posts are looked at in between
MAX_PASSES = 12          # a run goes round again while uploads are left or a kick came in meanwhile
YOUTUBE = "youtube_shorts"
NOW_BUSY = timedelta(minutes=15)    # "Post to TikTok now" is sending it: nothing else sends it meanwhile
STALE_RUN = 25 * 60      # seconds awake with no clean post-due before the review page says posting has stopped
STUCK_RUN = 45 * 60      # seconds awake one post-due may hold the lock before the page says it is stuck

Poster = Callable[[C.Account, Path, dict, dict], dict]
Uploader = Callable[[C.Account, dict], str]


def _posters() -> Dict[str, Poster]:
    from . import direct as D
    return {"instagram_reels": D.ig_post, "facebook_reels": D.fb_post, "tiktok": D.tiktok_post}


def _uploader() -> Uploader:
    from . import outlets as O
    return O.youtube_upload


def post_due(cfg: C.Config, now: Optional[datetime] = None, dry_run: bool = False,
             posters: Optional[Dict[str, Poster]] = None, uploader: Optional[Uploader] = None) -> List[str]:
    if dry_run:
        return _pass(cfg, now or cfg.now(), True, posters, uploader)[0]
    # one post-due at a time, whoever runs it (launchd, a kick, a person): a second one leaves word and stops
    lock, again = C.STATE / "post-due.lock", C.STATE / "post-due.again"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            again.touch()
            _health(last_run=_stamp())
            return ["  another post-due is running; it goes round again when it is done"]
        _health(running_since=_stamp(), run_clock=_clock())
        lines: List[str] = []
        try:
            for _ in range(MAX_PASSES):
                again.unlink(missing_ok=True)
                out, more = _pass(cfg, now or cfg.now(), False, posters, uploader)
                lines += out
                if not (more or again.exists()):
                    break
        except BaseException as e:      # a bad edit in direct.py or outlets.py stops every run: say so on the page
            h = _read_health()
            fails = int(h.get("fails") or 0) + 1
            _health(last_run=_stamp(), last_error=f"{type(e).__name__}: {str(e)[:300]}", error_at=_stamp(),
                    fails=fails, running_since="")
            if fails == NOTIFY_AFTER:
                _tell_owner(f"Posting broke {fails} runs in a row ({type(e).__name__}: {str(e)[:100]}). "
                            f"Nothing posts until it is fixed.")
            raise
        broke = next((ln.strip() for ln in lines if " broke " in ln), "")
        _health(last_run=_stamp(), last_ok=_stamp(), ok_clock=_clock(), last_error="", fails=0, running_since="",
                broke=broke[:300])
        return lines


def kick(cfg: C.Config) -> str:
    """Start a post-due now, in the background, so what was just approved goes out without waiting for the timer:
    YouTube uploads at once (with the outlet's upload_ahead_hours, only a slot that close to its time), a slot
    whose time has passed posts at once. Never from a test or an offline run."""
    from . import net
    why = net.why_not()                 # a test, an offline run, or a copy of the state never starts one
    if why:
        return f"posting not started ({why})"
    log = C.STATE / "logs" / "post-due.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLIP_FACTORY_LOG_STAMP="1", CLIP_FACTORY_CONFIG=str(cfg.path), CLIP_FACTORY_STATE=str(C.STATE))
    _KIDS[:] = [p for p in _KIDS if p.poll() is None]      # reap the finished ones (the review page runs for days)
    with open(log, "a") as out:
        _KIDS.append(subprocess.Popen([sys.executable, str(C.ROOT / "factory.py"), "post-due"], cwd=str(C.ROOT),
                                      env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                      start_new_session=True))
    return "posting started"


_KIDS: List[subprocess.Popen] = []


# ------------------------------------------------------------------------------------------------------ one pass
def _pass(cfg: C.Config, now: datetime, dry_run: bool, posters: Optional[Dict[str, Poster]],
          uploader: Optional[Uploader]) -> Tuple[List[str], bool]:
    from . import outlets as O
    posters = posters if posters is not None else _posters()
    uploader = uploader if uploader is not None else _uploader()
    days = [now.date() + timedelta(days=d) for d in (-1, 0, 1)]
    lines: List[str] = []
    more = False
    from . import autosign as A
    try:                                       # the automatic sign-off first, so what it signs goes out this pass
        lines += A.run(cfg, now, dry_run=dry_run)
    except Exception as e:  # noqa: BLE001  (a broken sign-off never stops what is already signed from posting)
        lines.append(f"  automatic sign-off broke ({type(e).__name__}: {str(e)[:200]})")
    if not dry_run:
        for acct in cfg.accounts.values():
            for day in days:
                try:
                    bd = B.read(acct, day)
                    if bd and any(s["status"] == B.APPROVED and s.get("signoff") for s in bd["slots"].values()):
                        lines += [f"  {acct.name} {day}{ln[1:]}" for ln in O.hand_off(cfg, acct, day)
                                  if not ln.startswith("  export")]
                except Exception as e:  # noqa: BLE001  (the hand-off of one day never stops the posting)
                    lines.append(f"  {acct.name} {day}: hand-off broke ({type(e).__name__}: {str(e)[:200]})")
    for acct in cfg.accounts.values():         # what has a time first, for every account
        api = [p for p in O.auto_platforms(acct) if p == YOUTUBE or p in posters]
        for day, s in _scheduled(acct, days, signed=dry_run):
            at = _at(cfg, acct, day, s, dry_run)
            if at is None or at > now:
                continue
            for plat in api:
                if plat == YOUTUBE and day < now.date() and not _yt_room_for_yesterday(acct, now):
                    continue                    # today's clips first: yesterday's only with budget left over
                line = _guarded(cfg, acct, day, s, plat, now, dry_run, posters, uploader, days)
                if line:
                    lines.append(line)
    for acct in cfg.accounts.values():         # then YouTube ahead of time, a few at a time
        if YOUTUBE not in O.auto_platforms(acct):
            continue
        tried = 0
        for day, s in _scheduled(acct, days, signed=dry_run):
            rec = (s.get("outlets") or {}).get(YOUTUBE) or {}
            at = _at(cfg, acct, day, s, True)
            if rec.get("video_id") or rec.get("gave_up") or at is None or at <= now:
                continue
            ahead = _yt_ahead(acct)
            if ahead is not None and at - now > ahead:
                break                           # spread out: this one (and every later one) uploads nearer its time
            if not O.yt_connected(acct) or O.yt_hold(acct):
                break                           # every upload waits for the same reason (the page says which)
            if tried >= YT_AHEAD_PER_PASS:
                more = True
                break
            line = _guarded(cfg, acct, day, s, YOUTUBE, now, dry_run, posters, uploader, days)
            if line:
                lines.append(line)
                tried += 1
    return lines, more and not dry_run


def _yt_room_for_yesterday(acct: C.Account, now: datetime) -> bool:
    """True when a late upload of yesterday may go: the 24-hour budget still has room after every slot of today still
    waiting for YouTube. 10-24: when the pause lifted, 10 of 10-23's clips went first and 10-24's own were refused at
    06:30, so the pause rolled on day after day. Since 2026-10-28 every slot of today counts, signed or not yet (a
    draft, an open slot the morning may fill): counting only the signed ones let yesterday's leftovers take the free
    places overnight, before the morning's clips were signed."""
    from . import outlets as O
    waiting = 0
    for s in ((B.read(acct, now.date()) or {}).get("slots") or {}).values():
        if not isinstance(s, dict) or B.empty_spare(s) or s.get("status") in (B.DECLINED, B.REFUSED):
            continue
        rec = (s.get("outlets") or {}).get(YOUTUBE) or {}
        if rec.get("video_id") or rec.get("gave_up"):
            continue
        if s.get("status") in B.SIGNED and s.get("signoff") and not O.covers_slot(acct, YOUTUBE, s):
            continue                        # signed for YouTube by hand
        waiting += 1
    return O.yt_room(acct, now.timestamp()) > waiting


def _at(cfg: C.Config, acct: C.Account, day: date, s: dict, quiet: bool) -> Optional[datetime]:
    """The slot's time, or None (said on the board once) when the board holds something that is not a time."""
    try:
        return datetime.fromisoformat(s["publish_at"])
    except (KeyError, TypeError, ValueError):
        if not quiet:
            try:
                with B.edit(cfg, acct, day, create=False) as bd:
                    B.note_once(bd["slots"][s["slot"]], f"not posted: its time is unreadable ({s.get('publish_at')!r})")
            except Exception:  # noqa: BLE001
                pass
        return None


def _scheduled(acct: C.Account, days: List[date], signed: bool = False) -> Iterator[Tuple[date, dict]]:
    """Scheduled slots in time order (a dry run also shows the signed ones a real run would hand off first)."""
    want = B.SIGNED if signed else (B.SCHEDULED,)
    for day in days:
        bd = B.read(acct, day)
        for s in sorted((bd or {}).get("slots", {}).values(), key=lambda s: s["slot"]):
            if s["status"] in want and s.get("signoff"):
                yield day, s


def _guarded(cfg, acct, day, s, plat, now, dry_run, posters, uploader, days) -> Optional[str]:
    """One platform of one slot; whatever goes wrong is said on the board, never raised (the other slots still go)."""
    try:
        return _one(cfg, acct, day, s, plat, now, dry_run, posters, uploader, days)
    except Exception as e:  # noqa: BLE001
        msg = f"{plat}: the poster broke on this slot ({type(e).__name__}: {str(e)[:200]})"
        if not dry_run:
            try:
                with B.edit(cfg, acct, day, create=False) as bd:
                    B.note_once(bd["slots"][s["slot"]], msg)
            except Exception:  # noqa: BLE001
                pass
        return f"  {acct.name} {day} {s['slot']} {msg}"


def _one(cfg: C.Config, acct: C.Account, day: date, s: dict, plat: str, now: datetime, dry_run: bool,
         posters: Dict[str, Poster], uploader: Uploader, days: List[date]) -> Optional[str]:
    """Post (or, for YouTube, upload) one platform of one slot when it is its turn. A line when something happened."""
    from . import direct as D
    from . import outlets as O
    from .stats import NotConnected
    rec = (s.get("outlets") or {}).get(plat) or {}
    if not O.covers_slot(acct, plat, s):
        return None                             # before the outlet was switched on: that one is posted by hand
    if rec.get("provider") == "matched" and not dry_run and _wrong_match(plat, s, rec):
        _record(cfg, acct, day, s["slot"], plat, {}, f"{plat}: the stats read tied this slot to another post "
                f"({rec.get('url') or rec.get('post_id')}: same opening words, earlier); it posts as planned",
                drop=MATCHED)
        rec = {}
    if rec.get("post_id") or rec.get("video_id") or rec.get("gave_up"):
        return None
    label = f"  {acct.name} {day} {s['slot']} {plat}"
    at = datetime.fromisoformat(s["publish_at"])
    late = now - at

    def give_up(why: str, said: str) -> str:
        if not dry_run:
            _record(cfg, acct, day, s["slot"], plat, {"gave_up": True, "why": why}, f"{plat}: {said}")
        return f"{label}: {said}"

    if at > now and plat != YOUTUBE:
        return None                             # not its time yet (YouTube alone goes ahead, with publishAt)
    ahead = _yt_ahead(acct) if plat == YOUTUBE else None
    if ahead is not None and at - now > ahead:
        return None                             # the outlet spreads uploads out: this one goes nearer its time
    if late > LATE_LIMIT:
        hours = int(late.total_seconds() // 3600)
        return give_up(f"{hours}h past its slot", f"not posted, {hours}h past its slot (left for a person)")
    if rec.get("tries") and rec.get("tried_at") and now - datetime.fromisoformat(rec["tried_at"]) < retry_after(rec):
        return None                             # the last try failed a moment ago: a later run tries again
    cap = _daily_cap(acct, plat, day)
    usual = _daily_cap(acct, plat)              # the cap on a day "daily_cap_by_day" does not name
    before = _posted_today(acct, day, plat, s["slot"]) if cap or usual else 0
    if cap and ranked_out(acct, day, s["slot"], cap):
        if not dry_run:
            _record(cfg, acct, day, s["slot"], plat, {"gave_up": True, "capped": True,
                                                       "why": CAP_RANK_WHY.format(n=cap)},
                    f"{plat}: not posted, {CAP_RANK_WHY.format(n=cap)}")
        return f"{label}: not posted, {CAP_RANK_WHY.format(n=cap)}"
    if cap and before >= cap:
        if not dry_run:
            _record(cfg, acct, day, s["slot"], plat, {"gave_up": True, "capped": True, "why": CAP_WHY.format(n=cap)},
                    f"{plat}: not posted, {CAP_WHY.format(n=cap)}")
        return f"{label}: not posted, {CAP_WHY.format(n=cap)}"
    if standin(s):
        return give_up("a stand-in test clip", "not posted, it is a stand-in test clip (factory.py make --standin)")
    if not (s.get("copy") or {}).get(plat):
        return give_up("no words for this platform", "not posted, the slot has no words for it")
    if plat == "tiktok" and tiktok_busy(rec, now):
        return None                             # "Post to TikTok now" is sending it this minute
    if plat == "tiktok" and D.tiktok_needs_choice(acct, s):
        return None                             # TikTok wants the person to choose who sees it: the card asks
    fresh = _fresh(acct, day, s)
    if fresh is None:
        return None                             # pulled or re-signed while this run was busy
    problems = B.verify(fresh)
    if problems:
        why = "; ".join(problems)
        return give_up(f"changed after signing: {why}", f"REFUSED, {why}")
    if plat == YOUTUBE and (not O.yt_connected(acct) or O.yt_hold(acct)):
        return None                             # waits; the review page says why (not connected, or the cap)
    gap = _yt_gap(acct) if plat == YOUTUBE else None
    if gap is not None:
        last = _last_upload(acct, days)
        if last and timedelta(0) <= now - last < gap:
            return None                         # not two uploads to one channel within the outlet's gap
    if plat in O.META and O.meta_hold(acct):
        return None                             # Meta refused the whole app: waits, asked again hourly
    if _guard_waits(cfg, acct, plat, now):
        return None                             # the social guard (switch on) holds this platform's posts
    if late >= timedelta(0):
        last = _last_public(acct, days, plat, now)
        if last and now - last < SPACING:
            return None                         # the next run takes it: one post per 10 minutes per platform
    if dry_run:                                 # the file the real run would send, through the same walls
        verb = "upload" if plat == YOUTUBE else "post"
        try:
            f = O.clip_for(acct, fresh, plat)
            D.in_band(acct, f, plat, cut=bool((fresh.get("cuts") or {}).get(plat)))
        except D.Refused as e:
            return f"{label}: would NOT {verb}: {str(e)[:200]}"
        except D.Unreadable as e:               # this Mac stalled: a real run counts it as a try and tries again
            return f"{label}: would NOT {verb} now: {str(e)[:200]} (one of the tries; a later run tries again)"
        return f"{label}: would {verb} {f.parent.name}/{f.name}"
    payload = dict(fresh, _attempt=rec.get("attempt"), _still=lambda: _fresh(acct, day, s) is not None,
                   _mark=lambda upd: _mark(cfg, acct, day, s["slot"], plat, upd, now))
    _busy(acct, day, s["slot"], plat)
    _fresh_usage()                              # the guard reads only this platform's own answer's numbers
    try:
        if plat == YOUTUBE:
            vid = uploader(acct, payload)
            res = {"video_id": vid, "url": f"https://www.youtube.com/shorts/{vid}", "publish_at": fresh["publish_at"],
                   "at": _utc(now), **_shas(fresh, plat, payload.get("_from_attempt"))}
        else:
            words = T.for_platform(Path(fresh["copy"][plat]["path"]).read_text(), plat)
            res = {**posters[plat](acct, O.clip_for(acct, fresh, plat), words, payload), "provider": "direct",
                   "at": _utc(now), **_shas(fresh, plat, payload.get("_from_attempt"))}
    except D.Stopped as e:                      # pulled while on its way, before anything went out: nothing to record
        return f"{label}: {str(e)[:200]}"
    except (D.Refused, NotConnected) as e:     # trying again cannot change it
        return give_up(str(e)[:300], f"not posted, {str(e)[:200]}")
    except D.Pending as e:                      # the platform has it and is still at work: not a try
        _record(cfg, acct, day, s["slot"], plat, {"waiting": str(e)[:300], "tried_at": _utc(now)}, f"{plat}: {str(e)[:200]}")
        return f"{label}: {str(e)[:200]}"
    except Exception as e:  # noqa: BLE001  (any platform error is reported on the board, never raised)
        err = str(e)
        if plat == YOUTUBE and any(k in err for k in O.CAP_ERRORS):
            O.yt_cool(acct, err)                # the channel's cap: every YouTube upload waits a day; not one of the tries
            _record(cfg, acct, day, s["slot"], plat, {"last_error": err[:400]}, f"{plat}: waits a day, YouTube's upload cap")
            return f"{label}: YouTube's upload cap, every upload waits a day: {err[:160]}"
        if plat in O.META and any(k in err for k in O.META_HOLD_ERRORS):
            O.meta_cool(acct, err)              # the whole app, not this post: Facebook and Instagram wait; not a try
            _record(cfg, acct, day, s["slot"], plat, {"last_error": err[:400]},
                    f"{plat}: waits, Meta refused the app ({err[:160]})", error=e)
            return f"{label}: Meta refused the app, Facebook and Instagram wait an hour: {err[:160]}"
        held = _guard_trip(cfg, acct, plat, e, now)
        if held:                                # the guard (switch on) paused this platform's posts: not a try
            _record(cfg, acct, day, s["slot"], plat, {"last_error": err[:400]}, f"{plat}: waits, {held}", error=e)
            return f"{label}: waits, {held}"
        tries = int(rec.get("tries", 0)) + 1
        upd = {"tries": tries, "last_error": err[:400], "tried_at": _utc(now)}
        if upload_failed(err):
            upd["upload_fails"] = int(rec.get("upload_fails", 0)) + 1
        most = max_tries({**rec, **upd})
        if tries >= most and isinstance(e, D.LookupFailed):
            # the earlier try could not be checked: it may be up. A person looks, and Try again then sends it fresh
            name = O.NAMES.get(plat, plat)
            upd.update(gave_up=True, lookup_failed=True,
                       why=f"could not check the last try {tries} times ({err[:160]}). Look on {name} for this clip: "
                           f"if it is not there, press Try again (it then sends it fresh)")
        elif tries >= most:
            upd.update(gave_up=True, why=f"failed {tries} times: {err[:200]}")
        _record(cfg, acct, day, s["slot"], plat, upd, f"{plat}: post failed ({tries}/{most}): {err[:200]}",
                error=e)
        return f"{label}: FAILED ({tries}/{most}) {err[:200]}"
    finally:
        _busy_done()
    if plat == YOUTUBE:
        O.yt_count(acct)
    elif plat in O.META:
        O.meta_clear(acct)
    done = ("tries", "last_error", "tried_at", "why", "waiting", "attempt", "upload_fails")
    late_word = _landed_late(cfg, acct, day, s, plat, res)     # a Pull or a new signature while it was on its way
    if late_word:
        return f"{label}: {late_word}"
    if plat == YOUTUBE and changed_since(res, fresh, plat):    # an earlier try's upload, of an earlier clip
        old = old_upload(dict(res, old=rec.get("old")))         # keeps any upload set aside before it
        _record(cfg, acct, day, s["slot"], plat, old, f"{plat}: {old['why']}", drop=done)
        return f"{label}: {old['why']}"
    if usual and before >= usual:
        res["past_usual_cap"] = usual           # lifted by the cap test: the usual cap holds it back (learn skips it)
    _record(cfg, acct, day, s["slot"], plat, res,
            f"{plat}: {'uploaded' if plat == YOUTUBE else 'posted'} {res.get('url') or res.get('post_id')}"
            + (f". {res['flag']}" if res.get("flag") else ""), drop=done)
    _after_post(cfg, acct, day, s["slot"], plat, res, now)
    if plat == YOUTUBE and at > now:
        return f"{label}: uploaded {res['url']}, YouTube publishes it at {s['slot']}"
    return f"{label}: posted {res.get('url') or res.get('post_id')}"


def upload_failed(err: str) -> bool:
    """Meta's upload service failed the whole upload on its side (UPLOAD_FAILS): a later try usually goes through."""
    return any(k in str(err) for k in UPLOAD_FAILS)


def stalled(err: str) -> bool:
    """This Mac could not read the clip's length (STALLS): a later try usually reads it."""
    return any(k in str(err) for k in STALLS)


def max_tries(rec: dict) -> int:
    """How many tries a platform of a slot gets: more, further apart, once Meta's upload service has failed it or
    this Mac stalled on its length."""
    err = rec.get("last_error") or ""
    return len(UPLOAD_GAPS) + 1 if rec.get("upload_fails") or upload_failed(err) or stalled(err) else MAX_TRIES


def retry_after(rec: dict) -> timedelta:
    """How long after a failed try the next one waits: RETRY_AFTER, or the next of UPLOAD_GAPS after an upload that
    Meta's side failed or a length this Mac could not read (both come in spells, so waiting longer each time gives the
    next try a better chance)."""
    err = rec.get("last_error") or ""
    if not (upload_failed(err) or stalled(err)):
        return RETRY_AFTER
    n = min(max(int(rec.get("upload_fails") or rec.get("tries") or 1), 1), len(UPLOAD_GAPS))
    return timedelta(minutes=UPLOAD_GAPS[n - 1])


def next_try(rec: dict) -> Optional[datetime]:
    """When a platform that failed is tried again (the first run after its wait), None when it is not waiting."""
    if not rec.get("tries") or not rec.get("tried_at") or rec.get("gave_up"):
        return None
    try:
        return datetime.fromisoformat(rec["tried_at"]) + retry_after(rec)
    except (TypeError, ValueError):
        return None


def _landed_late(cfg: C.Config, acct: C.Account, day: date, s: dict, plat: str, res: dict) -> str:
    """After a post or an upload went through: if the slot was pulled or signed again meanwhile, keep what the platform
    now holds on the board, loudly, so a person can take it down (it cannot be undone from here). '' when the slot is
    still as it was."""
    if _fresh(acct, day, s) is not None:
        return ""
    from . import outlets as O
    name = O.NAMES.get(plat, plat)
    done = ("tries", "last_error", "tried_at", "why", "waiting", "attempt", "upload_fails")
    with B.edit(cfg, acct, day, create=False) as bd:
        t = bd["slots"][s["slot"]]
        signed = t["status"] in B.SIGNED and t.get("signoff")
        r = t.setdefault("outlets", {}).setdefault(plat, {})
        r.update(res)
        for k in done:
            r.pop(k, None)
        if signed and plat == YOUTUBE and changed_since(r, t, plat):
            t["outlets"][plat] = old_upload(r)
            said = f"{plat}: uploaded while it was signed again with another clip. {t['outlets'][plat]['why']}"
        elif signed and changed_since(r, t, plat):
            r["flag"] = f"{name} has the version from before it was signed again: delete it there if it should not stay up"
            said = f"{plat}: posted {res.get('url') or res.get('post_id')}. {r['flag']}"
        elif signed:
            r.pop("flag", None)
            said = f"{plat}: {'uploaded' if plat == YOUTUBE else 'posted'} {res.get('url') or res.get('post_id')}"
        elif plat == YOUTUBE:
            at = _hhmm(cfg, s["publish_at"])
            r["flag"] = (f"YouTube got this clip while you pulled it: it goes public at {at} unless you make it "
                         f"private in Studio, https://studio.youtube.com/video/{res['video_id']}/edit")
            said = f"{plat}: UPLOADED WHILE YOU PULLED IT. {r['flag']}"
        else:
            r["flag"] = f"{name} posted it while you pulled it: delete it there if it should not stay up"
            said = f"{plat}: POSTED WHILE YOU PULLED IT ({res.get('url') or res.get('post_id')}). {r['flag']}"
        B.note(t, said)
    return said


def _shas(slot: dict, plat: str, att: Optional[dict] = None) -> dict:
    """What a post was made from (its file and its words), so a slot signed again can tell whether the platform has
    the clip it shows now (2026-10-17 audit: a re-approved clip never reached YouTube, the old one did)."""
    src = (slot.get("cuts") or {}).get(plat) or slot.get("clip") or {}
    now = {"clip_sha": str(src.get("sha256") or ""),
           "copy_sha": str(((slot.get("copy") or {}).get(plat) or {}).get("sha256") or "")}
    if not att or not att.get("hash") or att["hash"] == (slot.get("signoff") or {}).get("hash"):
        return now
    # sent under an earlier signature: what that try sent ("" when it did not say: then it is not known)
    return {"clip_sha": str(att.get("clip_sha") or ""), "copy_sha": str(att.get("copy_sha") or "")}


def same_clip(rec: dict, slot: dict, plat: str) -> Optional[bool]:
    """Was the platform's copy made from the file and the words the slot holds now? False when either differs, None
    when that is not known (a post from before 2026-10-18 that no Pull has dated back: backfill_shas)."""
    src = (slot.get("cuts") or {}).get(plat) or slot.get("clip") or {}
    words = ((slot.get("copy") or {}).get(plat) or {}).get("sha256")
    known = False
    for had, has in ((rec.get("clip_sha"), src.get("sha256")), (rec.get("copy_sha"), words)):
        if had and has:
            if had != has:
                return False
            known = True
    return True if known else None


def changed_since(rec: dict, slot: dict, plat: str) -> bool:
    """True when the platform's copy was made from another file or other words than the slot holds now (False when
    it is the same, or not known)."""
    return same_clip(rec, slot, plat) is False


def backfill_shas(slot: dict) -> None:
    """Posts made before 2026-10-18 carry no record of the file and words they were made from. Called by Pull while
    the slot is still as it was signed (before a waiting cut joins it): the board's hashes are then those of what the
    poster sent, so a clip signed again later can tell whether the platform has it. A post the stats read matched by
    its words is left alone (the factory did not send it)."""
    for plat, r in (slot.get("outlets") or {}).items():
        if not isinstance(r, dict) or r.get("clip_sha") or r.get("copy_sha"):
            continue
        if r.get("video_id") or (r.get("post_id") and r.get("provider") == "direct"):
            r.update(_shas(slot, plat), shas_from="the board at Pull")


def old_upload(rec: dict) -> dict:
    """A YouTube record whose video is an older version of the slot: kept under "old", and the platform left as not
    posted with what to do (make the old one private, then Try again uploads the clip as it is now)."""
    from .stats import set_aside_ids
    old = {k: rec.get(k) for k in ("video_id", "url", "publish_at", "at", "clip_sha", "copy_sha")}
    before = sorted(set_aside_ids(rec) - {str(old.get("video_id") or "")})
    if before:                  # every earlier set-aside upload too: the stats read must never tie one back
        old["before"] = before
    return {"gave_up": True, "why": old_why(old), "old": old}


def old_why(old: dict) -> str:
    vid = old.get("video_id", "")
    return (f"YouTube has the OLD clip ({vid}): make it private in Studio, https://studio.youtube.com/video/{vid}/edit, "
            f"then press Try again")


def _mark(cfg: C.Config, acct: C.Account, day, slot: str, plat: str, upd: dict, now: datetime) -> None:
    """direct/outlets write what the platform holds the moment it holds it (None removes a key). A post id written
    here is a post: the poster never sends that platform again for this slot."""
    with B.edit(cfg, acct, day, create=False) as bd:
        r = bd["slots"][slot].setdefault("outlets", {}).setdefault(plat, {})
        for k, v in upd.items():
            if v is None:
                r.pop(k, None)
            else:
                r[k] = v
        if upd.get("post_id"):
            r.setdefault("at", _utc(now))
            r.setdefault("provider", "direct")


MATCHED = ("post_id", "platform_id", "url", "published_at", "metrics", "provider")   # what the stats read writes


def old_rematch(rec: dict) -> bool:
    """The stats read tied a set-aside YouTube upload (an OLD clip, kept under "old") back to its slot by the title:
    then nothing would ever upload the clip as it is now (2026-10-17 review). stats.refresh never writes one now;
    Pull and Try again take off any that is there all the same."""
    from .stats import set_aside_ids
    old = set_aside_ids(rec)
    return bool(old) and not rec.get("video_id") and rec.get("provider") == "matched" and \
        bool(old & {str(rec.get("post_id") or ""), str(rec.get("platform_id") or "")})


def undo_old_rematch(rec: dict) -> bool:
    """Take such a match off a record (Pull and Try again, on the board they hold). True when there was one."""
    if not old_rematch(rec):
        return False
    for k in MATCHED:
        rec.pop(k, None)
    return True


def _wrong_match(plat: str, s: dict, rec: dict) -> bool:
    """A post id the stats read gave this slot by its words alone, which cannot be this slot's: nothing shows the
    poster ever sent it, and it went up well before the slot's time (the poster never posts early). The Politics's
    captions all open on the same dateline, so a sibling clip's post matched (2026-10-17 audit). A platform the slot
    was handed out for by hand is left alone: that early post may be the owner's own, by hand."""
    from . import outlets as O
    if plat == YOUTUBE or any(rec.get(k) for k in ("attempt", "tries", "tried_at", "waiting", "at", "last_error")):
        return False
    if plat in (O.handed_by_hand(s) or []):
        return False
    try:
        return datetime.fromisoformat(rec["published_at"]) < datetime.fromisoformat(s["publish_at"]) - timedelta(minutes=15)
    except (KeyError, TypeError, ValueError):
        return False


def standin(s: dict) -> bool:
    """A `make --standin` test clip (35s, inside GTA's band): it never goes to a real account."""
    if str(s.get("slug") or "").startswith("standin-"):
        return True
    if re.match(r"^\d{2}-standin-\d{4}\.mp4$", Path(str((s.get("clip") or {}).get("path") or "")).name):
        return True
    for c in (s.get("copy") or {}).values():
        try:
            if T.parse(Path(c["path"]).read_text())["title"].startswith("STAND-IN"):
                return True
        except (OSError, KeyError, TypeError):
            continue
    return False


def tiktok_busy(rec: dict, now: datetime) -> bool:
    """True while "Post to TikTok now" is sending this clip (started under 15 minutes ago): nothing sends it again."""
    try:
        return bool(rec.get("now_since")) and now - datetime.fromisoformat(rec["now_since"]) < NOW_BUSY
    except (TypeError, ValueError):
        return False


CAP_WHY = "the day's first {n} posts went out; Facebook shows hardly any after them (by design, not a failure)"
CAP_RANK_WHY = ("the day has more than {n} clips and Facebook takes the night's best {n}; this one is not among them "
                "(by design, not a failure)")


def ranked_out(acct: C.Account, day: date, slot: str, cap: int) -> bool:
    """Best of GTA (2026-10-29): from 11-19 Gaming and Stunts post 8 a day and Facebook shows only the first 6,
    so the first 6 by time left the day's biggest moment off Facebook whenever it sat at 19:15 or 21:00. On a day with
    more base slots than the cap whose slots carry the night's rank (makers.plan_best), the cap keeps the `cap`
    best-ranked slots (a slot with no rank after every ranked one, then by time), and this slot is out when it is not
    among them. False on a day no slot carries a rank (the Politics), or with no more slots than the cap: as before."""
    bd = B.read(acct, day) or {}
    base = {k: o for k, o in (bd.get("slots") or {}).items() if isinstance(o, dict) and not B.is_spare(o)}
    if cap <= 0 or len(base) <= cap or not any(o.get("rank") for o in base.values()):
        return False

    def key(k: str) -> tuple:
        try:
            return (0, int(base[k]["rank"]), k) if base[k].get("rank") else (1, 0, k)
        except (TypeError, ValueError):
            return (1, 0, k)
    return slot in base and slot not in sorted(base, key=key)[:cap]


def _daily_cap(acct: C.Account, plat: str, day: Optional[date] = None) -> int:
    """The outlet's "daily_cap": at most this many posts of a day go to that platform (0: no cap). 2026-10-26 (Andrew:
    "Yes, build all 6"): the Politics's Facebook posts 1-2 made 94% of its Facebook views and posts 7+ got 13 views at
    most (37 posts, 10-19 to 10-25), whatever the hour; Instagram and YouTube show no such cliff. The night puts the
    hottest clips first, so the day's first posts are its best.
    "daily_cap_by_day" ({"YYYY-MM-DD": n}) sets it for the board days it names, 0 = no cap that day: the Politics's
    Facebook cap test, 2026-10-27 to 11-02 (Andrew, 2026-10-26: "Yes, run the Facebook test"), lifts it on
    alternate days to see whether posting every clip brings more views or followers. A day it does not name, or a
    value that is not a whole number, keeps "daily_cap"."""
    out = acct.outlets.get(plat, {}) or {}
    by = out.get("daily_cap_by_day")
    if day is not None and isinstance(by, dict) and day.isoformat() in by:
        try:
            return max(0, int(by[day.isoformat()]))
        except (TypeError, ValueError):
            pass
    try:
        return max(0, int(out.get("daily_cap") or 0))
    except (TypeError, ValueError):
        return 0


def _posted_today(acct: C.Account, day: date, plat: str, skip: str) -> int:
    """How many slots of the day already went out on this platform (another slot than `skip`)."""
    bd = B.read(acct, day) or {}
    return sum(1 for k, o in (bd.get("slots") or {}).items()
               if k != skip and isinstance(o, dict) and ((o.get("outlets") or {}).get(plat) or {}).get("post_id"))


def _yt_ahead(acct: C.Account) -> Optional[timedelta]:
    """The YouTube outlet's "upload_ahead_hours": a slot uploads only this close to its time (None: at once)."""
    v = acct.outlets.get(YOUTUBE, {}).get("upload_ahead_hours")
    try:
        return timedelta(hours=float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _yt_gap(acct: C.Account) -> Optional[timedelta]:
    """The YouTube outlet's "upload_gap_minutes": never two uploads to the channel closer than this (None: no gap)."""
    v = acct.outlets.get(YOUTUBE, {}).get("upload_gap_minutes")
    try:
        return timedelta(minutes=float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _last_upload(acct: C.Account, days: List[date]) -> Optional[datetime]:
    last = None
    for day in days:
        for s in ((B.read(acct, day) or {}).get("slots") or {}).values():
            r = (s.get("outlets") or {}).get(YOUTUBE) or {}
            try:
                t = datetime.fromisoformat(r["at"]) if r.get("video_id") and r.get("at") else None
            except (TypeError, ValueError):
                t = None
            if t and (last is None or t > last):
                last = t
    return last


def yt_wait_words(cfg: C.Config, acct: C.Account, s: dict, now: datetime) -> str:
    """The card's YouTube row for a signed slot not uploaded yet: 'uploading now', or when it will upload."""
    if s.get("status") != B.SCHEDULED:
        return "uploads once scheduled"
    ahead = _yt_ahead(acct)
    at = datetime.fromisoformat(s["publish_at"])
    if ahead is not None and at - now > ahead:
        return f"uploads at {_hhmm(cfg, (at - ahead).isoformat())}, goes public at {_hhmm(cfg, s['publish_at'])}"
    return "uploading now"


def flag_words(s: dict) -> str:
    """One loud line for the card when a platform got this clip in a way a person must look at ('' when none)."""
    return " ".join(str(r["flag"]) for r in (s.get("outlets") or {}).values() if isinstance(r, dict) and r.get("flag"))


def _hhmm(cfg: C.Config, iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(cfg.tz()).strftime("%H:%M")
    except (TypeError, ValueError):
        return str(iso)


# ------------------------------------------------------------------------------------- what is on its way right now
def _busy(acct: C.Account, day: date, slot: str, plat: str) -> None:
    try:
        tmp = C.STATE / "post-due.busy.tmp"
        tmp.write_text(json.dumps({"account": acct.slug, "date": day.isoformat(), "slot": slot, "platform": plat,
                                   "pid": os.getpid(), "at": time.time()}))
        os.replace(tmp, C.STATE / "post-due.busy")
    except OSError:
        pass


def _busy_done() -> None:
    try:
        (C.STATE / "post-due.busy").unlink()
    except OSError:
        pass


def in_flight(acct: C.Account, day: date, slot: str) -> str:
    """The platform a running post-due is sending this slot to right now ('' when none): Pull says so."""
    try:
        b = json.loads((C.STATE / "post-due.busy").read_text())
        if (b.get("account"), b.get("date"), b.get("slot")) != (acct.slug, day.isoformat(), slot):
            return ""
        if time.time() - float(b.get("at") or 0) > 3600:
            return ""
        os.kill(int(b["pid"]), 0)               # that post-due is still running
        return str(b.get("platform") or "")
    except (OSError, ValueError, KeyError, TypeError):
        return ""


# ------------------------------------------------------------------------------------------------------ health
def _stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clock() -> List[float]:
    """[seconds this Mac has slept since it started, when it started]: an hour asleep is not an hour without posting."""
    try:
        raw, up = time.clock_gettime(time.CLOCK_MONOTONIC_RAW), time.clock_gettime(time.CLOCK_UPTIME_RAW)
        return [raw - up, time.time() - raw]
    except (AttributeError, OSError, ValueError):
        return [0.0, 0.0]


NOTIFY_AFTER = 3   # failed post-due runs in a row before the Mac says so (audit PP-15)


def _tell_owner(text: str) -> None:
    """One Mac notification when posting has broken NOTIFY_AFTER runs in a row: the page's red line is seen only
    while the page is open. Never from a test, an offline run or a copy of the state (net.why_not)."""
    from . import net
    if net.why_not():
        return
    try:
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(text)} with title "
                        f"{json.dumps('Clip factory: posting stopped')} sound name \"Basso\""],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def _read_health() -> dict:
    try:
        return json.loads((C.STATE / "post-due.health").read_text())
    except (OSError, ValueError):
        return {}


def _health(**upd) -> None:
    """state/post-due.health: the last run, the last clean run, the last error. Never stops a run."""
    try:
        h = _read_health()
        h.update(upd)
        p = C.STATE / "post-due.health"
        tmp = p.with_name("post-due.health.tmp")
        tmp.write_text(json.dumps(h, indent=1))
        os.replace(tmp, p)
    except OSError:
        pass


def _awake_since(iso: str, clock, now: float) -> float:
    """Seconds this Mac was awake between `iso` and `now` (the sleep in between taken out when it can be known)."""
    gap = now - datetime.fromisoformat(iso).timestamp()
    slept, boot = _clock()
    try:
        slept0, boot0 = float(clock[0]), float(clock[1])
        if abs(boot - boot0) < 120 and slept >= slept0:
            gap -= slept - slept0
    except (TypeError, ValueError, IndexError):
        pass
    return gap


def health_words(cfg: C.Config, now: Optional[datetime] = None) -> str:
    """'' while posting runs as it should, else one short red sentence for the top of the review page."""
    h = _read_health()
    if not h:
        return ""
    t = (now or cfg.now()).timestamp()
    try:
        ok = h.get("last_ok") or ""
        if h.get("last_error"):                 # a clean run clears it
            runs = int(h.get("fails") or 1)
            return (f"Posting broke at {_hhmm(cfg, h['error_at'])}" + (f" ({runs} runs in a row)" if runs > 1 else "")
                    + f": {h['last_error'][:140]}. Nothing posts until it is fixed.")
        if h.get("running_since") and _awake_since(h["running_since"], h.get("run_clock"), t) > STUCK_RUN:
            return f"Posting has been stuck since {_hhmm(cfg, h['running_since'])}: see state/logs/post-due.log."
        if ok and not h.get("running_since") and _awake_since(ok, h.get("ok_clock"), t) > STALE_RUN:
            return f"Posting has not run since {_hhmm(cfg, ok)}: the timer may be off (factory.py timer status)."
        if h.get("broke"):
            return f"Posting hit an error on its last run: {h['broke'][:160]}"
    except (TypeError, ValueError, KeyError):
        return ""
    return ""


def hand_out_drift(cfg: C.Config, now: Optional[datetime] = None) -> List[str]:
    """Signed slots handed out for posting by hand on a platform that now posts by itself: it may go out twice, or be
    refused (Patel 10-18 07:00, 2026-10-17). factory.py doctor lists them (drift_items: each with the owner's options)."""
    return [line for line, _ in drift_items(cfg, now)]


OWNERS_CALL = "Andrew's call; a bot reports this and changes nothing"


def drift_items(cfg: C.Config, now: Optional[datetime] = None) -> List[Tuple[str, str]]:
    """hand_out_drift as (what is wrong, what the owner can do). Worded as the owner's call: the Politics nightly (a bot)
    runs doctor, and must not act on it."""
    from . import direct as D
    from . import outlets as O
    now = now or cfg.now()
    out = []
    for acct in cfg.accounts.values():
        for day in (now.date(), now.date() + timedelta(days=1)):
            for s in sorted(((B.read(acct, day) or {}).get("slots") or {}).values(), key=lambda s: s["slot"]):
                if s["status"] not in B.SIGNED or not s.get("signoff"):
                    continue
                for plat in O.handed_by_hand(s) or []:
                    rec = (s.get("outlets") or {}).get(plat) or {}
                    if not O.covers_slot(acct, plat, s) or rec.get("post_id") or rec.get("video_id"):
                        continue
                    name = O.NAMES.get(plat, plat)
                    line = (f"{acct.name} {day} {s['slot']} {s.get('slug', '')}: {name} was signed to be posted by "
                            f"hand, but now it posts by itself")
                    try:
                        O.clip_for(acct, s, plat)
                        refused = ""
                    except D.Refused as e:
                        refused = str(e)
                    cut_waits = any(f"a new {name} cut arrived after signing" in n for n in s.get("notes") or [])
                    # name the files themselves, not their Ready to post copies: the next hand-off of that day
                    # rebuilds those folders and drops a platform that now posts by itself
                    c = (s.get("copy") or {}).get(plat) or (s.get("copy") or {}).get(YOUTUBE) or {}
                    words = f" with the words in {c['path']}" if c.get("path") else ""
                    if refused and cut_waits:
                        from . import collect as K
                        cut = K.cuts_in(Path(s["source_folder"])).get(plat) if s.get("source_folder") else None
                        line += f"; as signed it would NOT post ({refused}), and a new {name} cut is waiting"
                        fix = (f"{OWNERS_CALL}: in Clip Review Pull it, watch the new {name} cut, approve it again (it "
                               f"then posts by itself)"
                               + (f"; or post that cut by hand: {cut}{words}" if cut else ""))
                    elif refused:
                        clip = (s.get("clip") or {}).get("path", "")
                        line += f"; as signed it would NOT post ({refused})"
                        fix = (f"{OWNERS_CALL}: it needs its own {name} cut first; or post the clip by hand if "
                               f"{name} takes its length there: {clip}{words}")
                    else:
                        fix = (f"{OWNERS_CALL}: it posts by itself at {_hhmm(cfg, s['publish_at'])}, so do not also "
                               f"post it by hand")
                    out.append((line, fix))
    return out


def tiktok_now(cfg: C.Config, acct: C.Account, day: date, slot: str, now: Optional[datetime] = None) -> str:
    """The card's "Post to TikTok now": this signed clip, to TikTok, at once, with the choices made on the card.
    Runs in its own process (spawn_tiktok_now); what happened is written on the board for the card to show."""
    from . import direct as D
    from . import outlets as O
    from .stats import NotConnected
    now = now or cfg.now()
    s = ((B.read(acct, day) or {}).get("slots") or {}).get(slot)
    if not s or s["status"] not in B.SIGNED or not s.get("signoff"):
        return f"{slot}: not posted, it is not signed"
    rec = (s.get("outlets") or {}).get("tiktok") or {}
    if rec.get("post_id"):
        return f"{slot}: already on TikTok"
    if standin(s):
        return f"{slot}: not posted, it is a stand-in test clip"
    problems = B.verify(s)
    if problems:
        return f"{slot}: REFUSED, {'; '.join(problems)}"
    c = (s.get("copy") or {}).get("tiktok")
    if not c:
        return f"{slot}: not posted, it has no TikTok words"
    words = T.for_platform(Path(c["path"]).read_text(), "tiktok")
    with B.edit(cfg, acct, day, create=False) as bd:       # one at a time: a second click or a second tab stops here
        t = bd["slots"][slot]
        r = t.setdefault("outlets", {}).setdefault("tiktok", {})
        busy = bool(r.get("post_id")) or tiktok_busy(r, now)
        if not busy:
            r.update(waiting="posting to TikTok now", tried_at=_utc(now), now_since=_utc(now))
            r.pop("last_error", None)
            B.note_once(t, "tiktok: Post to TikTok now")
    if busy:
        return f"{slot}: already on its way to TikTok; the card says when it is up"
    try:
        res = D.tiktok_post(acct, O.clip_for(acct, s, "tiktok"), words, dict(s, _manual=True))
    except Exception as e:  # noqa: BLE001  (whatever it was, the card must not stay on "posting now")
        err = str(e) if isinstance(e, (D.PlatformError, NotConnected, OSError)) else f"{type(e).__name__}: {e}"
        _record(cfg, acct, day, slot, "tiktok", {"last_error": err[:400], "tried_at": _utc(now)},
                f"tiktok: {err[:200]}", drop=("waiting", "now_since"))
        return f"{slot}: TikTok said no: {err[:300]}"
    _record(cfg, acct, day, slot, "tiktok", {**res, "provider": "direct", "manual": True, "at": _utc(now)},
            f"tiktok: posted ({D.PRIVACY_NAMES.get(res.get('privacy', ''), res.get('privacy', ''))}) "
            f"{res.get('url') or res.get('post_id')}",
            drop=("waiting", "last_error", "tries", "tried_at", "why", "now_since"))
    return f"{slot}: on TikTok {res.get('url') or res.get('post_id')}"


def spawn_tiktok_now(cfg: C.Config, acct: C.Account, day: date, slot: str) -> bool:
    """Start tiktok_now in the background (an upload and TikTok's processing take minutes). False when offline."""
    from . import net
    if not net.allowed():
        return False
    log = C.STATE / "logs" / "tiktok-now.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLIP_FACTORY_CONFIG=str(cfg.path), CLIP_FACTORY_STATE=str(C.STATE))
    _KIDS[:] = [p for p in _KIDS if p.poll() is None]
    with open(log, "a") as out:
        _KIDS.append(subprocess.Popen([sys.executable, str(C.ROOT / "factory.py"), "tiktok-now", acct.slug,
                                       day.isoformat(), slot], cwd=str(C.ROOT), env=env, stdin=subprocess.DEVNULL,
                                      stdout=out, stderr=subprocess.STDOUT, start_new_session=True))
    return True


def _utc(now: datetime) -> str:
    """The poster's own clock (a test passes its own `now`) as the boards write time: UTC, to the second."""
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _fresh(acct: C.Account, day: date, s: dict) -> Optional[dict]:
    """The slot as it is on disk now, if it is still scheduled under the same signature (else None)."""
    bd = B.read(acct, day) or {}
    t = bd.get("slots", {}).get(s["slot"])
    if not t or t["status"] not in B.SIGNED or (t.get("signoff") or {}).get("hash") != (s.get("signoff") or {}).get("hash"):
        return None
    return t


def went_public(rec: dict, slot: dict) -> Optional[datetime]:
    """When that post became public: a YouTube upload at its publishAt, or at the upload if that came later."""
    try:
        if rec.get("video_id"):
            at = datetime.fromisoformat(slot["publish_at"])
            up = datetime.fromisoformat(rec["at"]) if rec.get("at") else at
            return max(at, up)
        if rec.get("post_id"):
            return datetime.fromisoformat(rec.get("at") or rec.get("published_at"))
    except (TypeError, ValueError):
        return None
    return None


def _last_public(acct: C.Account, days: List[date], plat: str, now: datetime) -> Optional[datetime]:
    last = None
    for day in days:
        for s in ((B.read(acct, day) or {}).get("slots") or {}).values():
            t = went_public((s.get("outlets") or {}).get(plat) or {}, s)
            if t and t <= now and (last is None or t > last):
                last = t
    return last


def _record(cfg: C.Config, acct: C.Account, day, slot: str, plat: str, upd: dict, text: str, drop=(),
            error: Optional[BaseException] = None) -> bool:
    """Write what happened to one platform of one slot, and say it on the board once. True when it was news. The
    social guard's log gets the same row once the board is written (only while its switch is on)."""
    with B.edit(cfg, acct, day, create=False) as bd:
        t = bd["slots"][slot]
        r = t.setdefault("outlets", {}).setdefault(plat, {})
        r.update(upd)
        for k in drop:
            r.pop(k, None)
        news = B.note_once(t, text)
    _social_note(cfg, acct, day, slot, plat, upd, text, error)
    return news


# ------------------------------------------------------------------------------------------------ the social guard
# The social layer (clipfactory/outward.py, reached through the plug board) never reroutes a post: with the account's
# guard switch on it may hold a platform's posts and log each one; off, or not built, each of these does nothing.
# Each runs in its own try: posting never stops over the social layer.
def _social(spec: str):
    from . import registry as REG
    return REG.fn(spec)


def _fresh_usage() -> None:
    """Forget the rate-limit numbers of the last answer on this thread (direct.last_usage) before a platform is
    tried: an earlier post's Meta numbers, or another account's, never count as this one's (a YouTube upload does not
    go through direct._http, so nothing would replace them)."""
    try:
        from . import direct as D
        D._LAST.usage = {}
    except Exception:  # noqa: BLE001
        pass


def _guard_waits(cfg: C.Config, acct: C.Account, plat: str, now: datetime) -> bool:
    """True while a guard hold covers this platform's posts (guard on)."""
    try:
        f = _social("outward:post_gate")
        return bool(f(cfg, acct, plat, now)) if f is not None else False
    except (Exception, SystemExit):  # noqa: BLE001  (a part's refusal never stops posting)
        return False


def _guard_trip(cfg: C.Config, acct: C.Account, plat: str, err: BaseException, now: datetime) -> str:
    """The hold's words when the guard (switch on) paused this platform's posts over this refusal; else ''."""
    try:
        f = _social("outward:post_tripwire")
        got = f(cfg, acct, plat, err, now) if f is not None else ""
        return got if isinstance(got, str) else ""
    except (Exception, SystemExit):  # noqa: BLE001  (a part's refusal never stops posting)
        return ""


def _after_post(cfg: C.Config, acct: C.Account, day, slot: str, plat: str, res: dict, now: datetime) -> None:
    """After a platform took the clip: the social layer's follow-ups (a first comment, a story) are queued."""
    try:
        f = _social("social:on_posted")
        if f is not None:
            f(cfg, acct, day, slot, plat, res, now)
    except (Exception, SystemExit) as e:  # noqa: BLE001  (a part's refusal never stops posting)
        _social_error("social:on_posted", e)


def _social_note(cfg: C.Config, acct: C.Account, day, slot: str, plat: str, upd: dict, text: str,
                 error: Optional[BaseException]) -> None:
    try:
        f = _social("outward:record_post")
        if f is not None:
            f(cfg, acct, day, slot, plat, upd, text, error=error)
    except (Exception, SystemExit) as e:  # noqa: BLE001  (a part's refusal never stops posting)
        _social_error("outward:record_post", e)


def _social_error(spec: str, e: BaseException) -> None:
    try:
        from . import registry as REG
        REG._log_error(spec, e)
    except (Exception, SystemExit):  # noqa: BLE001  (a part's refusal never stops posting)
        pass


def goes_out(slot: dict, now: datetime) -> str:
    """'at 14:00', 'tomorrow at 07:00', 'now (its time has passed)', 'never: too late' for a scheduled or ready slot."""
    at = datetime.fromisoformat(slot["publish_at"])
    if at > now:
        hhmm = at.astimezone(now.tzinfo).strftime("%H:%M") if now.tzinfo else at.strftime("%H:%M")
        days = (at.astimezone(now.tzinfo).date() - now.date()).days if now.tzinfo else (at.date() - now.date()).days
        return f"at {hhmm}" if days == 0 else f"tomorrow at {hhmm}" if days == 1 else f"on {at.date()} at {hhmm}"
    return "now (its time has passed)" if now - at <= LATE_LIMIT else "never: too late to post (over 20 hours)"
