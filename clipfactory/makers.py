"""Makers fill a day's open slots by driving the account's own skill. The factory never cuts video itself.

gta     A GTA account (Gaming; since 2026-10-19 also Stunts and Roleplay, the GAMEPLAY lane only), end to end
        with no stops, each from its own folder: the watchlist's newest on-topic uploads -> one skill job per source ->
        `run.py <job> --pick N --deliver` per slot. The pick is the skill's own candidate ranking (moments.py), so a
        night's clips are the finder's top moments; the bot may hand-pick and re-hook first (see the bot's skill).
        A slot cuts underlay or pip (the B-ROLL lane) or gameplay (the GAMEPLAY lane: only a creator PLAYING, and
        the skill picks the cam shape off the measured facecam).
        The picks skip what preflight would refuse (music over its cap, the stream's intro, outro and donation
        reads), and a pick refused at preflight (or at the grade on HOOK or DEADAIR) tries the next candidate.
        The night (make_passes, 2026-10-18; the night shift, 2026-10-19, clipfactory/shift.py): shortest
        sources first, one over 30 minutes (or of unknown length) only when nothing shorter is left, no intake that
        would end after 05:30 (checked again when its turn in the render queue comes) and no step started after
        05:50, up to 8 passes until 05:30, the worker stopped by itself at 06:15 (a failed slot gets the next
        source; a source whose intake failed is never dealt again that day, and a row is given up after 1 timeout
        or 2 failures: intake_failed on the inbox row, written only when an intake of the same pass or a later one
        worked, so a night with no network blames no video; a pass whose every intake failed ends the passes).
        Out of sources (or after a network night) before 03:20, the worker waits 40 minutes, scans the watchlist
        again and makes another pass (rescan_every, rescan_until).
        A reaction to a Rockstar drop is cut with the film (run.py --film). A REACTIONS slot (accounts.json
        reactions_slots) is left for the 13:00 REACTIONS task's clip (factory.py place) until 03:30 on its
        day, then cut here as a normal clip. Settings: GTA_MAKER_DEFAULTS,
        overridden per account by accounts.json "gta_maker".
politics  Politics has no stop-free path: a hearing needs its cast checked and a slate written, which is the
        bot's work (its Hermes skill), not code. `make` says so and exits; `collect` takes what the bot delivered.
generic A client account (accounts.py): viral-clip-engine in its neutral look, from clips somebody slated, with the
        engine's own preflight and grade; never the Politics's look, folders or day rules (generic.py).
standin A rehearsal: ffmpeg draws a test clip and paste files per slot, so the whole line (drafts, review, sign-off,
        outlets) can be proved without touching the skills or the internet.

Long work runs detached (`--background`): one worker per account, one make at a time per account
(state/<account>/.render.lock, render_lock), wrapped in `caffeinate -i` so the Mac does not sleep mid-render. Each
heavy step (a source's intake, each clip's render) also waits its turn in the render queue (clipfactory/renderlock.py,
state/renderlock), which the Politics's renders, the other GTA accounts' and any client's share, so two steps never
render at once. Until 2026-10-19 the make lock was one file for the whole Mac (state/.render.lock): a second GTA
account's night would have waited behind Gaming's whole night instead of taking turns step by step.
One source video is cut for one GTA account only: the first to cut it (or hold it on a board) wins (held_elsewhere).
The kill list (reasons.py, the owner's Pulls and Declines): a candidate sharing more than 15% of its seconds with a
killed span of the same video is never picked, a video killed whole (RIGHTS) is never dealt, and a clip collect
refuses for killed b-roll makes the slot try its next candidate.
"""
from __future__ import annotations

import dataclasses
import fcntl
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import board as B
from . import clipformats as CF
from . import collect as K
from . import config as C

PY_SKILLS = "/usr/bin/python3"   # both skills are pinned to the system Python 3.9 (their own notes)


def render_lock(acct: C.Account) -> Path:
    """One make at a time for this account (state/<account>/.render.lock): the GTA worker holds it all night, a hand
    make or a client's make waits for it. The Mac itself is shared step by step by the render queue (renderlock.py)."""
    return C.STATE / acct.slug / ".render.lock"


def log_path(acct: C.Account, day: date, what: str) -> Path:
    p = C.STATE / acct.slug / "logs" / f"{day.isoformat()}-{what}.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class ShiftOver(BaseException):
    """The worker's watchdog (gta_maker stop_at, 06:15: shift.DONE_BY): the night is over. A BaseException, so no
    source's `except Exception` takes it for that source's own failure: it ends the run, and make_passes fails the
    slots it was making. Never held against an upload."""


_STOP_AT: List[float] = []      # the running worker's stop time (epoch seconds), set by make_passes for its run


def run_logged(cmd: List[str], log: Path, cwd: Optional[Path] = None, env: Optional[dict] = None,
               timeout: int = 4 * 3600) -> subprocess.CompletedProcess:
    """One skill step, its output appended to the log. A step that runs past its timeout is stopped with everything
    it started (whisper, ffmpeg), then TimeoutExpired is raised: the caller fails its slots and goes on. Inside a
    worker with a stop time (make_passes, stop_at 06:15) a step still running then is stopped the same way and
    ShiftOver is raised instead: the night is over, nothing is blamed."""
    stop = _STOP_AT[0] if _STOP_AT else None
    left = (stop - time.time()) if stop else None
    if left is not None and left <= 0:
        raise ShiftOver(f"it is past {_hhmm(stop)}, the worker's stop time: {' '.join(cmd[:3])} was not started")
    limit = timeout if left is None else max(1.0, min(float(timeout), left))
    with open(log, "a") as f:
        f.write(f"\n$ {' '.join(cmd)}\n")
        f.flush()
        p = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env, stdout=f, stderr=subprocess.STDOUT,
                             text=True, start_new_session=True)
        try:
            p.wait(timeout=limit)
        except BaseException:          # the timeout, or Ctrl-C at the terminal: the step's whole group stops
            _stop_group(p)
            if not isinstance(sys.exc_info()[1], subprocess.TimeoutExpired):
                raise
            if left is not None and limit < timeout:
                f.write(f"\n[factory] stopped at {_hhmm(stop)}: the worker's stop time (the night is over)\n")
                raise ShiftOver(f"{' '.join(cmd[:3])} was still running at {_hhmm(stop)} and was stopped") from None
            f.write(f"\n[factory] stopped: ran past {timeout} s\n")
            raise
        return subprocess.CompletedProcess(cmd, p.returncode)


def _hhmm(epoch: Optional[float]) -> str:
    return datetime.fromtimestamp(epoch).strftime("%H:%M") if epoch else "?"


def _stop_group(p: subprocess.Popen) -> None:
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 10)):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            p.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def tail(path: Path, n: int = 6) -> str:
    try:
        return " | ".join(path.read_text().strip().splitlines()[-n:])
    except OSError:
        return ""


# ---------------------------------------------------------------------------------------------------------- GTA
# Every setting the GTA maker reads; accounts.json "gta_maker" overrides any of them for the account.
GTA_MAKER_DEFAULTS: Dict[str, object] = {
    # the picks (2026-10-17): moments.json marks what preflight will refuse; skip it, and try the next on a refusal
    "music_cap": None,           # None: skip what moments.json marks "preflight refuses" (the job's own MUSIC cap);
                                 # a number also skips a candidate with more music than that share
    "furniture_from_s": 600,     # the intro and outro marks (first 60 s, last 90 s) skip a pick only on a source
                                 # this long: on a 4-minute reaction they cover most of it, the verdict included
    "retries": 2,                # more candidates tried after a preflight refusal (TOOL is not retried; LENGTH is,
                                 # since run.py already tried the same span at 1.0x: 10-18, audit gta-yield)
    # the film a reaction reacts to (run.py --film): the row is a reaction and its title names the drop
    "films": [{"match": r"\blaunch trailer\b", "cues": "work/launch-trailer/cues.json"}],
    "reaction_match": r"\breact(s|ed|ing|ion|ions)?\b",
    "max_age_days": 14,          # an inbox row uploaded longer ago than this is not cut (0 = no limit)
    # THE SCAN'S WINDOW (2026-10-20, GTA Part 2): watch.py marks an upload older than this many days at FIRST SIGHT
    # "old", before its topic test, and nothing ever looks at it again. At 2 days the 10-16 scan, after a 40-day gap,
    # buried 33 on-topic uploads of the two weeks before (7 were reopened by hand on 10-19 and gave both of 10-20's
    # GTA clips). 7 days: a missed night or two strands nothing; max_age_days still bounds what is cut.
    "watch_since_days": 7,
    # an inbox row whose title matches this is never cut by the night ("" = none). 2026-10-19, Andrew: "make leak
    # count against a GTA video". The 10.19 08:45 and 14:00 clips came from MrBossFTW's "Cyberleek Has CRASHED
    # OUT...The End Of The GTA 6 Gameplay Leaks!", fed by the watchlist scan, not the scout; a faceless creator's
    # picture IS the footage, and Rockstar takes down leaked footage hardest (Reference/gta6-account-memo.md 3.1).
    # The scout turns the same titles down (scout.json damp.titles). A person can still cut one with the skill.
    "skip_titles": r"\b(leak|leaks|leaked|leaker|leakers|leaking)\b|\bcyber ?leek",
    "skip_channels": [],         # watchlist slugs the night never cuts, rows already in the inbox included (2026-10-26:
                                 # Onespot, music under every talk; its watchlist row is phase "off" too)
    # THE GAME SWITCH (2026-10-19, Andrew on Stunts: "for now we're going to use GTA 5... when the game comes
    # out, we'll switch"). On: the night never cuts an inbox row whose title the account's own scout turns down as
    # off topic on the posting day (config/scout.json damp.off_topic with its dated phases: scout.off_topic_rule), so
    # one date in that file switches the scout and the night together. The roster scan feeds the night most of its
    # sources, and before this it read no date at all. Off = as before (Gaming, Roleplay).
    "skip_off_topic": False,
    # the comment question: the skill's default is kept only for these hook shapes (gtacopy.default_question)
    "question_defaults_ok": ["quote", "list", "reveal", "claim", "contrast", "date"],
    # OFF since 2026-10-17 evening: the skill's gtacopy writes the question at fill time (one owner for the rule)
    "question_rewrite": False,
    # OWNER CALLS, off = the old behaviour
    "spread": False,             # deal sources round-robin over the day, short sources fewer clips, no near-repeats
    "short_source_s": 360,       #   with spread: a source shorter than this ...
    "short_source_clips": 2,     #   ... gives at most this many clips
    "max_overlap": 0.15,         #   with spread: a pick sharing more than this share of its seconds with one cut is skipped
    "duo_skips_singles": False,  # a day with a REACTIONS slot cuts no single clip of the duo's reactors
    # REACTIONS slots (accounts.json "reactions_slots" while "reactions_until"): the night leaves them for the
    # 13:00 REACTIONS task's clip; one still empty at this time on its own day is cut as a normal clip
    "reactions_hold_until": "03:30",
    "duo_sources": "work/*reactions*/src/*.info.json",   # the duo's sources (yt-dlp info files under the skill)
    # THE NIGHT'S SHAPE (2026-10-18, audit gta-yield): 10-17's night spent 96% of its hours on intakes and made
    # one pass, so a failed source failed its slots for the day. THE NIGHT SHIFT (2026-10-19, clipfactory/shift.py):
    # the worker runs from 21:00 to 05:30 beside the Politics's run, each step taking its turn in the render queue.
    "max_source_s": 1800,        # a source longer than this is dealt only after every shorter one (0 = no cap)
    "unknown_source_s": 3600,    # a source of unknown length (a scout row of a past stream) counts as this long:
                                 # dealt last, and at night started only when nothing else is left (0 = as short)
    "intake_x": 4,               # an intake takes up to this many times the source's length (10-17: 1.5x to 4.1x)
    "night_until": "05:30",      # an intake whose estimate (length x intake_x) ends after this is not started, and
                                 # one whose turn in the render queue came late is checked again then (a time before
                                 # noon is on the posting day; after night_until no estimate stops one)
    "deadline_x": 3,             # the same for a run with a start deadline (CLIP_RENDERLOCK_UNTIL: the night's
                                 # render_until 05:50, the 03:45 retry's, the GTA second half's): length x this must
                                 # end by it (0 = no such guard; 3, not 4: 10-17's 4.1x was with the Politics sharing
                                 # the CPU)
    "render_until": "05:50",     # the worker (the 21:00 night) starts no intake or render after this when its run
                                 # has no deadline of its own: a slot not started fails "deadline" (shift.LAST_START:
                                 # nothing new starts after it, for either account; "" = no such deadline)
    "passes": 8,                 # the worker (make --background, the 21:00 night) makes open or failed slots again
                                 # from the next sources, up to this many passes in all (1 = one pass, as before);
                                 # the passes also end when no source is left, at pass_until, or on a network night
    "pass_until": "05:30",       # no new pass starts at or after this (shift.LAST_PASS)
    "rescan_every": 40,          # the night's worker with no waiting source left (or after a network night) waits
                                 # this many minutes, then scans the watchlist again and makes another pass, instead
                                 # of ending (uploads keep coming after midnight; nothing else scans then). Only from
                                 # 21:00 (shift.START) and while the wait ends by rescan_until (0 = end, as before)
    "rescan_until": "03:20",     # no rescan wait ends after this (shift.RESCAN_UNTIL): the GTA second half (~03:41)
                                 # and the 03:45 retry take over what is left, with their own fresh scan
    "stop_at": "06:15",          # the worker's own watchdog (shift.DONE_BY): a step still running then is stopped
                                 # and its slots marked failed, so nothing lands while Andrew signs ("" = none)
    "intake_fails_max": 2,       # an inbox row whose intake failed this many times is not cut again ...
    "intake_timeouts_max": 1,    # ... nor one that timed out this many times (a 4-hour timeout never repeats)
    # THE LANES' SOURCES (2026-10-19, the new GTA accounts): the inbox kinds a GAMEPLAY slot takes (a creator PLAYING;
    # Roleplay's is "rp"), and an optional allowlist for the b-roll lane (None = every kind, as before)
    "playing_kinds": ["gameplay", "physics", "streamer"],
    "broll_kinds": None,
    # THE WINDOW INTAKE (2026-10-19, OWNER CALL, off = as before). The richest GTA supply is long: streamer VODs on
    # Twitch and YouTube (NoPixel RP, playthroughs: 2-19 h), and the night never started one (length x intake_x ends
    # after 05:30 for anything over about two hours). On: a source longer than window_from_s (or of unknown length)
    # is taken in as the 1-3 windows the skill's vodwindow.py picks off PUBLIC signals (the clips viewers made of the
    # VOD, its chat rate, its game chapters, YouTube's most-replayed, loudness), not whole: the pick runs before the
    # intake queues (no render turn is held while Twitch answers), run.py --windows auto takes in only those
    # stretches, and the time guards and the deal count the source as window_total_s long.
    "windows": False,
    "window_from_s": 2700,       # with windows: a source longer than this is windowed (45 min: a whole 30-min upload
                                 # is still taken whole)
    "window_total_s": 2400,      # the windows' total (run.py --window-total): what the guards count the intake as
    "window_max": 3,             # at most this many windows ...
    "window_min_s": 600,         # ... each at least this long ...
    "window_len_s": 1500,        # ... and at most this long
    "window_pick_s": 600,        # the pick's own time (metadata, the clips, the chat sample), added to the estimate
    "window_pick_timeout_s": 1800,
    # THE HARVEST (2026-10-20, measured on the first window job): analyze bakes the master's shots into the skill's
    # b-roll library, about 39 s a shot on this Mac, up to 400 a source; on Buddha's 40 minutes of windows it was 42+
    # of the intake's minutes. Only the B-ROLL lane ever reads those shots, and the library is shared with Gaming
    # (another creator's footage there is tier 3, an open owner call). "auto": harvest only for an account with a
    # b-roll lane (Gaming as before; Stunts and Roleplay, gameplay only, skip it); true or false say so.
    "harvest": "auto",
    # BEST OF GTA (2026-10-29, Andrew: "Gaming is just like everything GTA... they're just the best clips that we can
    # find. The biggest news, the biggest events, the biggest streamers"). A slot of format "best" takes its layout
    # from the source dealt to it (best_template), never from a rotation: the sources in the inbox's order (the
    # biggest first), at most best_per_streamer clips of one streamer a day, on a news day at least best_news_min of
    # best_news_kinds, a second slot only for a source longer than best_second_min_s. A Twitch clip with its VOD is
    # taken in as the stream around it (stream_intake): vod_before_s before its offset, vod_after_s after its end.
    "best_per_streamer": 2,
    "best_news_kinds": ["news", "breakdown"],
    "best_news_min": 1,
    "best_pip_kinds": ["reaction"],
    "best_second_min_s": 900,
    "vod_before_s": 90,
    "vod_after_s": 120,
    # THE AFTERNOON PREP (2026-10-29, Andrew: "the clips get made at night"): `factory.py prep <account>` takes in the
    # sources the night would deal (download, demucs, whisper, the candidates), so the night only picks and renders.
    # The same job names as the night's (job_name), which run.py finds "analyzed already". The timer runs it at
    # prep.at while prep.on is true (timer.prep_agents); nothing starts after prep.until.
    "prep": {"on": False, "at": "15:00", "until": "20:30", "max_sources": 8},
}
REACTIONS_HELD = ("Held for the day's REACTIONS clip: the 13:00 REACTIONS task cuts it with the skill (anchor.py, "
                  "multireact.py) and places it here with factory.py place. Still empty at {until} on the day, it "
                  "is cut as a normal clip (a later pass of the night, the GTA second half or the 03:45 retry), so "
                  "the day still has ten.")
REACTIONS_BY_HAND = REACTIONS_HELD          # its name before 2026-10-18


def gta_settings(acct: C.Account) -> dict:
    out = dict(GTA_MAKER_DEFAULTS)
    out.update((acct.raw or {}).get("gta_maker") or {})
    ds = acct.day_shift() if hasattr(acct, "day_shift") else None
    if ds:                              # an account that cuts in the day: the night's clock moves into it
        out.update(day_shift_clock(ds[1]))
    return out


def day_shift_clock(until: str) -> dict:
    """The night's clock settings for a day shift that ends at `until` (2026-10-29, accounts.json "day_shift"): the
    worker stops itself at `until` (stop_at), starts no intake or render after 45 minutes before it (render_until),
    no new pass and no intake whose estimate ends after an hour before it (pass_until, night_until), and never waits
    to rescan (a rescan is the night's). Each is after noon (config.day_shift_problem), so night_time reads it on the
    day before the posting day: the day the shift runs."""
    m = int(until[:2]) * 60 + int(until[3:])
    at = lambda x: f"{x // 60:02d}:{x % 60:02d}"     # noqa: E731
    return {"stop_at": until, "render_until": at(m - 45), "pass_until": at(m - 60), "night_until": at(m - 60),
            "rescan_every": 0}


GTA_ENV_KEYS = ("GTA_ACCOUNT", "QM_SLOP_LIB", "QM_WATCHLIST", "QM_INBOX", "GTA_PRESET", "QM_BRAND")


def gta_env(acct: C.Account) -> dict:
    """The skill's steps run on the account's own folder and kit. watch.py reads only QM_WATCHLIST and QM_INBOX (it
    defaults to ~/Desktop/Gaming), so without them a second account's scan filled Gaming's inbox; GTA_PRESET and
    QM_BRAND name its brand kit (Gaming's is "gta", what the skill uses when told nothing)."""
    env = dict(os.environ)
    env["GTA_ACCOUNT"] = str(acct.account_dir)
    env["QM_SLOP_LIB"] = str(acct.account_dir / "Library" / "slop")
    env["QM_WATCHLIST"] = str(acct.account_dir / "Publish" / "watchlist.json")
    env["QM_INBOX"] = str(acct.account_dir / "Publish" / "inbox.json")
    kit = acct.kit or "gta"
    env["GTA_PRESET"] = kit
    env["QM_BRAND"] = kit
    return env


def video_id(url: Optional[str]) -> Optional[str]:
    """The video's own id: YouTube (watch?v=, youtu.be, /shorts/, /live/) or a Twitch VOD number. None otherwise."""
    if not url:
        return None
    try:
        u = urlparse(str(url))
    except ValueError:
        return None
    host = (u.netloc or "").lower()
    if "youtube.com" in host or "youtu.be" in host:
        v = (parse_qs(u.query).get("v") or [None])[0]
        if v:
            return v
        parts = [x for x in u.path.split("/") if x]
        if "youtu.be" in host and parts:
            return parts[0]
        if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
            return parts[1]
    if "twitch.tv" in host:
        m = re.search(r"/videos/(\d+)", u.path)
        if m:
            return m.group(1)
        m = re.search(r"^/(?:[^/]+/)?clip/([A-Za-z0-9_-]+)", u.path) if "clips." not in host else \
            re.search(r"^/([A-Za-z0-9_-]+)", u.path)
        if m:                       # a viewer's clip (Best of GTA, 2026-10-29): twitch.tv/<who>/clip/<slug>
            return m.group(1)
    if "kick.com" in host:
        m = re.search(r"/clips/(clip_[A-Za-z0-9]+)", u.path)
        if m:
            return m.group(1)
    return None


def _inbox_rows(data: dict) -> List[dict]:
    items = data.get("items", [])
    if isinstance(items, dict):  # keyed by video id
        items = list(items.values())
    return [i for i in items if isinstance(i, dict)]


def skipped_title(i: dict, st: dict) -> bool:
    """An inbox row the night never cuts for its title (gta_maker skip_titles: the leak videos since 10-19)."""
    pat = str(st.get("skip_titles") or "")
    return bool(pat) and bool(re.search(pat, str(i.get("title") or ""), re.I))


def night_off_topic(acct: C.Account, day: Optional[date] = None) -> Optional[dict]:
    """The off_topic rule the night holds the account's inbox to for `day`, the posting day (today when None): {}
    when gta_maker skip_off_topic is off (every account but Stunts) or the account's scout has no off_topic that
    day; None when skip_off_topic is on and config/scout.json has no readable block for it (the night says so)."""
    if not gta_settings(acct).get("skip_off_topic"):
        return {}
    from . import scout as SC        # lazy both ways: the scout reads makers lazily too
    return SC.off_topic_rule(acct.slug, (day or date.today()).isoformat())


def off_topic_title(i: dict, off: Optional[dict]) -> bool:
    """An inbox row whose title the rule from night_off_topic turns down (scout.is_off_topic)."""
    if not off:
        return False
    from . import scout as SC
    return SC.is_off_topic(str(i.get("title") or ""), off)


def gta_sources(acct: C.Account, used: set, day: Optional[date] = None) -> List[dict]:
    """The inbox's waiting uploads, best first, minus anything already cut (used_sources: by URL or video id),
    anything uploaded more than max_age_days ago (45 'new' rows dated back to early September on 10-17), any
    title skip_titles names (the leak videos, 10-19) and, with skip_off_topic, any title the account's scout turns
    down as off topic for `day`, the posting day (Stunts' game switch, night_off_topic)."""
    p = acct.account_dir / "Publish" / "inbox.json"
    if CF.vault_on(acct) and day:           # Stunts' vault (clipformats.py): rows for the moments fresh uploads miss
        CF.vault_top_up(acct, day, used)
    if not p.exists():
        return []
    rows = _inbox_rows(json.loads(p.read_text()))
    st = gta_settings(acct)
    days = float(st.get("max_age_days") or 0)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ") if days else ""
    off = night_off_topic(acct, day) or {}

    def waiting(i: dict) -> bool:
        if i.get("status") != "new" or i.get("url") in used or (video_id(i.get("url")) or "\0") in used:
            return False
        if intake_gave_up(i, st):           # its intake failed or timed out often enough (mark_inbox_failed)
            return False
        if skipped_title(i, st):            # a leak video (skip_titles): never cut by the night
            return False
        if str(i.get("channel") or "") in (st.get("skip_channels") or []):   # a channel taken off (10-26: Onespot)
            return False
        if off_topic_title(i, off):         # the game switch (skip_off_topic): off topic on the posting day
            return False
        up = str(i.get("uploaded") or "")
        return not (cutoff and up and up < cutoff)
    fresh = sorted((i for i in rows if waiting(i)), key=lambda i: -_inbox_score(i))
    # A LAST-RESORT ROW IS DEALT LAST (2026-10-20, GTA Part 2): a row marked `last_resort` (GTA 5 / RP on a GTA 6
    # night, score 0.05) went through the one-upload-per-channel pass like any other, so a channel's ONLY upload
    # outranked a second GTA 6 upload of a channel already dealt: on 10-20 the first pass dealt Onespot's GTA 5
    # helicopters and TimTheTatman's GTA 5 story (4 slots) ahead of MrBossFTW's and TGG's reopened GTA 6 videos.
    # The last-resort rows now follow every other row, best first among themselves.
    last = [i for i in fresh if i.get("last_resort")]
    fresh = [i for i in fresh if not i.get("last_resort")]
    # one upload per channel first (best first), then the rest: a night should not be one creator
    firsts, rest, seen = [], [], set()
    for i in fresh:
        (rest if i.get("channel") in seen else firsts).append(i)
        seen.add(i.get("channel"))
    return firsts + rest + last


def watch_since(st: dict) -> float:
    """watch.py scan's --since: gta_maker "watch_since_days" (7 since 2026-10-20), never under 1 day, never past
    max_age_days when that is set (an upload too old to cut is not worth calling new)."""
    try:
        since = float(st.get("watch_since_days") or 7)
    except (TypeError, ValueError):
        since = 7.0
    cap = float(st.get("max_age_days") or 0)
    return max(1.0, min(since, cap) if cap else since)


def _inbox_score(i: dict) -> float:
    """A row the news scout lifted (scout.feed_gta) ranks first for 30 hours, then at its plain score."""
    until = i.get("scout_until")
    if until and until < time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()):
        return float(i.get("base_score") or 0)
    return float(i.get("score") or 0)


def used_sources(acct: C.Account) -> set:
    """Every source already cut, by URL and by video id: each board's slots (source_url, and the source_urls a drop
    or REACTIONS slot may carry) and every skill job that delivered a clip. A clip made by hand and dropped in has
    no source on its board (TGG 11_srAYn_rA filled 10-16 21:00 that way), but its job does. Every GTA account's jobs
    count (the skill's work/ is one folder for all of them); the other GTA accounts' boards are held_elsewhere.
    An open or failed slot that holds no clip and keeps sources_tried counts no source_url (10-18, audit gta-yield):
    nothing was cut from it (its intake failed, or the run stopped). Its day keeps it out by sources_tried
    (tried_on), later days by its inbox row (status 'cut' after a good intake, intake_failed after failed ones);
    before, a failed intake's source stayed out for good unless a later pass happened to deal its slot another
    source. A slot from before (no sources_tried) still counts its source_url, as it did."""
    used = board_sources(acct)
    for u in skill_cut_sources(acct):
        used |= _ids(u)
    return used


def held_elsewhere(cfg: C.Config, acct: C.Account) -> set:
    """The sources the other GTA accounts cut or hold on their boards (2026-10-19: one source video, one GTA account;
    the first to cut it or hold it on a board wins). Empty while no other GTA account has a board here."""
    out: set = set()
    for other in other_gta(cfg, acct):
        out |= board_sources(other, strict=False)
    return out


def _ids(u) -> set:
    """A source by its URL and by its video id."""
    if not u:
        return set()
    vid = video_id(u)
    return {u, vid} if vid else {u}


def board_sources(acct: C.Account, strict: bool = True) -> set:
    """The sources on the account's boards (used_sources' rule): every slot's source_url, unless it holds no clip and
    is open or failed with sources_tried (nothing was cut from it), and the source_urls a drop or REACTIONS slot
    carries. strict False (another account's boards): a board that cannot be read is skipped, never this night's
    end."""
    used: set = set()
    for bp in B.all_boards(acct):
        try:
            slots = json.loads(bp.read_text())["slots"].values()
        except (OSError, ValueError, KeyError, AttributeError):
            if strict:
                raise
            continue
        for s in slots:
            if s.get("clip") or s.get("status") not in (B.OPEN, B.FAILED) or "sources_tried" not in s:
                used |= _ids(s.get("source_url"))
            for u in s.get("source_urls") or []:
                used |= _ids(u)
    return used


def other_gta(cfg: C.Config, acct: C.Account) -> List[C.Account]:
    """Every other GTA account in the file, switched on or not (a source one cut stays its own after a switch-off);
    one on another machine has no boards here."""
    every = cfg.all_accounts or cfg.accounts
    return [a for a in every.values() if a.maker == "gta" and a.slug != acct.slug]


def taken_by(cfg: C.Config, acct: C.Account, item: dict) -> str:
    """The other GTA account that holds this source on a board now ('' when none): read again just before a source
    is marked making, since every GTA night deals at 21:00 and two could deal one video in the same minute."""
    want = _ids(item.get("url"))
    for other in other_gta(cfg, acct):
        if want & board_sources(other, strict=False):
            return other.name
    return ""


def skill_cut_sources(acct: C.Account) -> List[str]:
    """The source URL of every skill job that delivered at least one clip (its out_dir manifest). Rehearsals and
    the _-named proof and audit jobs do not count."""
    out = []
    for pj in sorted((acct.skill_dir / "work").glob("*/project.json")):
        if pj.parent.name.startswith("_"):
            continue
        try:
            cfg = json.loads(pj.read_text())
            if cfg.get("rehearsal") or not cfg.get("source_url"):
                continue
            man = Path(str(cfg.get("out_dir") or "")) / "manifest.json"
            if cfg.get("out_dir") and man.is_file() and (json.loads(man.read_text()).get("clips") or {}):
                out.append(str(cfg["source_url"]))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return out


def mark_inbox_cut(acct: C.Account, item: dict, day: date, job: str) -> str:
    """The inbox row of a source the factory cut says so (status 'cut', the day, the job): watch.py, the scout and a
    hand session see it is taken. 10-17's four cut sources were still 'new' the next evening."""
    p = acct.account_dir / "Publish" / "inbox.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return "  inbox: not marked cut (inbox.json unreadable)"
    items = data.get("items")
    vid = video_id(item.get("url"))
    hit = items.get(vid) if isinstance(items, dict) and vid else None
    if not isinstance(hit, dict):
        hit = next((r for r in _inbox_rows(data) if r.get("url") == item.get("url")), None)
    if hit is None:
        return "  inbox: no row for this source (not marked)"
    hit.update(status="cut", cut_for=day.isoformat(), cut_job=job,
               cut_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    try:
        tmp = p.with_suffix(".json.factory-tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(p)
    except OSError as e:                    # never fails a source's slots after a good intake
        return f"  inbox: not marked cut ({e})"
    return "  inbox: marked cut"


def intake_gave_up(row: dict, st: dict) -> bool:
    """The row's intake failed or timed out often enough not to try it again (intake_failed, mark_inbox_failed):
    intake_timeouts_max timeouts (1) or intake_fails_max failures (2), a timeout counting as a failure too."""
    f = row.get("intake_failed")
    if not isinstance(f, dict):
        return False
    try:
        n, t = int(f.get("n") or 0), int(f.get("timeouts") or 0)
    except (TypeError, ValueError):
        return False
    return t >= max(1, int(st.get("intake_timeouts_max") or 1)) or n >= max(1, int(st.get("intake_fails_max") or 2))


def given_up(acct: C.Account) -> int:
    """How many waiting ('new') inbox rows gta_sources leaves out because their intake failed (intake_gave_up)."""
    try:
        rows = _inbox_rows(json.loads((acct.account_dir / "Publish" / "inbox.json").read_text()))
    except (OSError, ValueError):
        return 0
    st = gta_settings(acct)
    return sum(1 for i in rows if i.get("status") == "new" and intake_gave_up(i, st))


def title_skips(acct: C.Account) -> int:
    """How many waiting ('new') inbox rows gta_sources leaves out for their title (skip_titles: leak videos)."""
    try:
        rows = _inbox_rows(json.loads((acct.account_dir / "Publish" / "inbox.json").read_text()))
    except (OSError, ValueError):
        return 0
    st = gta_settings(acct)
    return sum(1 for i in rows if i.get("status") == "new" and skipped_title(i, st))


def off_topic_skips(acct: C.Account, off: Optional[dict]) -> int:
    """How many waiting ('new') inbox rows gta_sources leaves out as off topic (skip_off_topic, night_off_topic's
    rule); a leak title is counted once, by title_skips."""
    if not off:
        return 0
    try:
        rows = _inbox_rows(json.loads((acct.account_dir / "Publish" / "inbox.json").read_text()))
    except (OSError, ValueError):
        return 0
    st = gta_settings(acct)
    return sum(1 for i in rows if i.get("status") == "new" and not skipped_title(i, st) and off_topic_title(i, off))


def mark_inbox_failed(acct: C.Account, item: dict, why: str, timeout: bool = False) -> str:
    """A source whose intake failed or timed out says so on its inbox row, written the way mark_inbox_cut writes
    (a temp file, then a rename): intake_failed {n, timeouts, last_error, at}. Its slots fail as before; gta_sources
    gives the row up after 1 timeout or 2 failures (intake_gave_up), and the day never deals it again (tried_on).
    10-16's rP52QhtFjf8 (a yt-dlp 403) stayed 'new' and only its age kept it out; a 4-hour timeout would have run
    again the next night (audit gta-yield). A timeout is noted at once; a plain failure only when the run settles
    (settle_intakes), if an intake of its pass or a later one worked."""
    p = acct.account_dir / "Publish" / "inbox.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return "  inbox: intake failure not noted (inbox.json unreadable)"
    items = data.get("items")
    vid = video_id(item.get("url"))
    hit = items.get(vid) if isinstance(items, dict) and vid else None
    if not isinstance(hit, dict):
        hit = next((r for r in _inbox_rows(data) if r.get("url") == item.get("url")), None)
    if hit is None:
        return "  inbox: no row for this source (intake failure not noted)"
    was = hit.get("intake_failed") if isinstance(hit.get("intake_failed"), dict) else {}
    try:
        n, t = int(was.get("n") or 0) + 1, int(was.get("timeouts") or 0) + (1 if timeout else 0)
    except (TypeError, ValueError):
        n, t = 1, (1 if timeout else 0)
    hit["intake_failed"] = {"n": n, "timeouts": t, "last_error": str(why or "")[:300],
                            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        tmp = p.with_suffix(".json.factory-tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(p)
    except OSError as e:                    # a note only: the slots are failed either way
        return f"  inbox: intake failure not noted ({e})"
    if intake_gave_up(hit, gta_settings(acct)):
        return f"  inbox: intake failure {n} noted; this upload is not cut again"
    return f"  inbox: intake failure {n} noted; not dealt again today, may be tried on another night"


def tried_on(board: Optional[dict]) -> set:
    """Every source a slot of this day's board was made from (sources_tried, by URL and by video id). A later pass
    deals a failed slot another source and a hand cut credits a slot to its own, both of which overwrite the slot's
    source_url; this keeps the old one, so a source whose intake failed is never dealt again on its day (the
    critic's re-deal bug, 10-17)."""
    out: set = set()
    for s in ((board or {}).get("slots") or {}).values():
        for u in s.get("sources_tried") or []:
            if u:
                out.add(u)
                vid = video_id(u)
                if vid:
                    out.add(vid)
    return out


@dataclasses.dataclass
class IntakeRun:
    """What the intakes of one run did (a worker's passes, or one plain make): per pass the good intakes, the failed
    ones (a timeout, the tool itself, a plain failure) and whether the watchlist scan failed; and the failures not yet
    held against their uploads. When the network, yt-dlp or YouTube's bot check breaks, every intake fails within
    seconds: blaming each video then would use up the inbox and give rows up for good (review of gta-yield, 10-18).
    So a plain failure is held against its upload (intake_failed, and sources_tried for the day) only when an intake
    of the same pass or a later one worked (settle_intakes, at the run's end); a timeout always counts."""
    passes: List[dict] = dataclasses.field(default_factory=list)
    pending: List[dict] = dataclasses.field(default_factory=list)

    def start_pass(self) -> dict:
        self.passes.append({"ok": 0, "failed": 0, "scan_failed": False})
        return self.passes[-1]

    @property
    def now(self) -> dict:
        return self.passes[-1] if self.passes else self.start_pass()

    def good(self) -> None:
        self.now["ok"] += 1

    def bad(self, item: dict, why: str, slots: List[str], hold: Optional[bool] = True, tally: bool = True) -> None:
        """A failed intake. hold True: a plain failure, held against the upload at the run's end if an intake of its
        pass or a later one worked; False: never held (the tool itself, a turn the render queue refused), only taken
        out of its slots' sources_tried; None: nothing to settle (a timeout, noted at once). tally: it counts as a
        failed intake of the pass (not a turn the render queue refused: that is no download failing)."""
        if tally:
            self.now["failed"] += 1
        if hold is not None:
            self.pending.append({"pass": len(self.passes) - 1, "item": item, "why": why, "slots": list(slots),
                                 "count": bool(hold)})

    def all_failed(self, k: int = -1) -> bool:
        """Pass k had intake failures and not one good intake."""
        p = self.passes[k] if self.passes else {}
        return bool(p.get("failed")) and not p.get("ok")


def settle_intakes(cfg: C.Config, acct: C.Account, day: date, run: IntakeRun) -> List[str]:
    """The run's end: each failure held back (IntakeRun) is held against its upload when an intake of its pass or a
    later one worked (mark_inbox_failed; its slots keep it in sources_tried, so the day never deals it again);
    otherwise it is not: its intake_failed is not written and it leaves its slots' sources_tried, so the next run
    (the GTA second half, the 03:45 retry, the next night) may deal it again."""
    if not run.pending:
        return []
    good = [k for k, p in enumerate(run.passes) if p.get("ok")]
    last_good = max(good) if good else -1
    held = [e for e in run.pending if e["count"] and e["pass"] <= last_good]
    freed = [e for e in run.pending if not any(e is h for h in held)]
    run.pending = []
    lines = [mark_inbox_failed(acct, e["item"], e["why"]) for e in held]
    if freed:
        with B.edit(cfg, acct, day) as bd:
            for e in freed:
                for k in e["slots"]:
                    t = bd["slots"].get(k)
                    if t and t.get("sources_tried"):
                        t["sources_tried"] = [u for u in t["sources_tried"] if u != e["item"].get("url")]
        tool = [e for e in freed if not e["count"]]
        net = [e for e in freed if e["count"]]
        if net:
            lines.append(f"{len(net)} intake failure(s) not held against their upload(s): no intake of their pass or "
                         f"after it worked (the network, yt-dlp or YouTube's bot check, not the videos); the next "
                         f"run may deal them again")
        if tool:
            lines.append(f"{len(tool)} intake(s) stopped by the tool itself or not started by the render queue: not "
                         f"held against their upload(s); the next run may deal them again")
    return lines


def _secs(item: dict) -> Optional[float]:
    try:
        v = float(item.get("duration"))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def est_secs(item: dict, st: dict) -> Optional[float]:
    """The source's length as the deal and the time guards count it: its own, else unknown_source_s (an hour: a scout
    row of a past stream has no length and may run for hours), else None (unknown_source_s 0: as before)."""
    return _secs(item) or (float(st.get("unknown_source_s") or 0) or None)


def harvest_on(acct: C.Account) -> bool:
    """Whether the intake bakes the master's shots into the b-roll library (gta_maker harvest; "auto": only for an
    account with a b-roll lane)."""
    v = gta_settings(acct).get("harvest", "auto")
    if isinstance(v, str) and v.strip().lower() == "auto":
        return "broll" in _lanes(acct)
    return bool(v)


def windowed(item: dict, st: dict) -> bool:
    """With gta_maker windows on, a source longer than window_from_s (or of unknown length, counted as
    unknown_source_s) is taken in as windows (vodwindow.py), not whole."""
    if not st.get("windows"):
        return False
    secs = est_secs(item, st)
    return secs is None or secs > float(st.get("window_from_s") or 0)


def intake_secs(item: dict, st: dict) -> Optional[float]:
    """The seconds the intake works on: the source's own (est_secs), or for a windowed source the windows' total
    (window_total_s, never more than the source) plus the pick's own time (window_pick_s, taken once, not x intake_x:
    the pick waits on the network and leaves the CPU free)."""
    secs = est_secs(item, st)
    if not windowed(item, st):
        return secs
    total = float(st.get("window_total_s") or 2400)
    return min(secs, total) if secs else total


def by_length(sources: List[dict], st: dict) -> List[dict]:
    """The sources as dealt: one longer than max_source_s (30 minutes) only after every shorter one (10-17's 4,497 s
    TGG breakdown alone was a 2-to-5-hour intake that could time out at 4 hours), otherwise in the inbox's order.
    One of unknown length counts as unknown_source_s (an hour): dealt last."""
    cap = float(st.get("max_source_s") or 0)
    if not cap:
        return list(sources)
    long_ = [i for i in sources if (intake_secs(i, st) or 0) > cap]     # a windowed VOD counts as its windows
    gone = {id(i) for i in long_}
    return [i for i in sources if id(i) not in gone] + long_


def shortest_first(item: dict, st: Optional[dict] = None) -> tuple:
    """The order the night runs its sources in: the shortest intake first (an unknown length last; a windowed VOD
    as long as its windows, intake_secs)."""
    s = intake_secs(item, st) if st and windowed(item, st) else _secs(item)
    return (s is None, s or 0.0)


def night_time(cfg: C.Config, day: date, hhmm: str) -> datetime:
    """A night setting (night_until, pass_until, render_until, stop_at) as a clock time for the posting day's night:
    before noon it is on the posting day itself (05:30 = the morning after the 21:00 run), from noon on the evening
    before."""
    k = C.normal_slot(str(hhmm))
    t = cfg.slot_time(day, k)
    return t - timedelta(days=1) if int(k[:2]) >= 12 else t


def intake_too_late(cfg: C.Config, acct: C.Account, day: date, item: dict, now: Optional[datetime] = None,
                    picked: bool = False) -> str:
    """'' when the source's intake may start now; else why not. Before night_until (05:30 on the posting day) an
    intake whose estimate (the source's length x intake_x) ends after it is not started: the night stays inside
    its hours and its clips can still start before 05:50. A run with a start deadline (CLIP_RENDERLOCK_UNTIL: the
    night's render_until and the 03:45 retry's, 05:50) starts no intake estimated (length x deadline_x; 0 = off) to
    end after it either: no clip of it could be cut, and the run is stopped at 06:15. Otherwise (a daytime make) no
    estimate stops one. `now`: when the intake's turn came (_make_source asks again after a wait in the queue). A source of unknown length counts as unknown_source_s (an hour: at night it starts only near 21:00,
    when it is all that is left); with unknown_source_s 0 it is never stopped."""
    st = gta_settings(acct)
    secs = intake_secs(item, st)
    if not secs:
        return ""
    long_ = (f"length unknown (counted as {secs / 60:.0f} min)" if not _secs(item) else f"{secs / 60:.0f} min long")
    pick = 0.0
    if windowed(item, st):              # the windows' total, x intake_x, after the pick's own minutes (picked: done)
        pick = 0.0 if picked else float(st.get("window_pick_s") or 0)
        whole = _secs(item)
        long_ = (f"a {whole / 3600:.1f} h VOD" if whole else "a VOD of unknown length") + \
            f" taken in as {secs / 60:.0f} min of windows"
    t = now or cfg.now()
    eta = t + timedelta(seconds=pick + secs * float(st.get("intake_x") or 4))
    hhmm = str(st.get("night_until") or "")
    if hhmm:
        try:
            until = night_time(cfg, day, hhmm)
        except SystemExit:
            until = None
        if until is not None and t < until < eta:
            return f"{long_}, its intake would end about {eta:%H:%M}, after {hhmm} (night_until); left for a later run"
    from . import renderlock as RL
    by, dx = RL.env_until(), float(st.get("deadline_x") or 0)
    if by is not None and dx:
        last = datetime.fromtimestamp(by, tz=t.tzinfo)
        end = t + timedelta(seconds=pick + secs * dx)
        if t < last < end:      # already past it: the render queue refuses the intake itself, as before
            return (f"{long_}, its intake would end about {end:%H:%M}, after this run's start deadline "
                    f"{last:%H:%M} (CLIP_RENDERLOCK_UNTIL): no clip of it could be cut; left for a later run")
    return ""


def duo_held(acct: C.Account, board: dict) -> set:
    """OWNER CALL (duo_skips_singles, off by default): on a day with a REACTIONS slot, the duo's reactors (the skill's
    REACTIONS source files) are not cut as single clips too: 10-18 had Speed x3 and Kai x3 planned from the same two
    videos the hand REACTIONS jobs used."""
    st = gta_settings(acct)
    slots = (board or {}).get("slots", {}).values()
    duo = [s for s in slots if s.get("format") == "reactions" and s.get("status") != B.DECLINED]
    if not st.get("duo_skips_singles") or not duo:
        return set()
    held: set = set()
    for s in duo:
        for u in s.get("source_urls") or []:
            held.add(u)
            held.add(video_id(u) or u)
    for p in acct.skill_dir.glob(str(st.get("duo_sources") or "")):
        try:
            info = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        for k in ("id", "webpage_url"):
            if info.get(k):
                held.add(str(info[k]))
    return held


def reactions_slots(acct: C.Account, day: date) -> List[str]:
    """The day's REACTIONS slots: accounts.json "reactions_slots" (one or two slot times) while "reactions_until" (a
    date) has not passed. Empty for an account without them."""
    raw = acct.raw or {}
    until = str(raw.get("reactions_until") or "")
    if until and day.isoformat() > until:
        return []
    out: List[str] = []
    for s in raw.get("reactions_slots") or []:
        try:
            k = C.normal_slot(str(s))
        except SystemExit:
            continue
        if k in acct.active_slots() and k not in out:
            out.append(k)
    return out[:2]


def reactions_held(cfg: C.Config, acct: C.Account, day: date, board: dict, now: Optional[datetime] = None) -> set:
    """The open slots the night leaves for the day's REACTIONS clip (the 13:00 REACTIONS task places it): the REACTIONS
    slots, and a slot set to `reactions` by hand, until reactions_hold_until (03:30) on the day itself. Not held: a
    released slot (factory.py place --release), or any once the day has as many REACTIONS clips as REACTIONS slots."""
    slots = (board or {}).get("slots") or {}
    rx = set(reactions_slots(acct, day)) | {k for k, s in slots.items() if s.get("format") == "reactions"}
    if not rx:
        return set()
    try:
        cutoff = cfg.slot_time(day, C.normal_slot(str(gta_settings(acct).get("reactions_hold_until") or "03:30")))
    except SystemExit:
        cutoff = cfg.slot_time(day, "03:30")
    if (now or cfg.now()) >= cutoff:
        return set()
    filled = [k for k, s in slots.items()
              if s.get("format") == "reactions" and s.get("status") in (B.READY, B.APPROVED, B.SCHEDULED, B.MAKING)]
    if len(filled) >= max(1, len(reactions_slots(acct, day))):
        return set()
    return {k for k in rx if k in slots and slots[k].get("status") in (B.OPEN, B.FAILED)
            and not slots[k].get("reactions_released")}


def rotation_format(acct: C.Account, slot: str) -> str:
    """The format a new board deals this slot (board.new_board): a REACTIONS slot with no REACTIONS clip is cut as it."""
    fmts = acct.formats or ["underlay"]
    act = acct.active_slots()
    return fmts[act.index(slot) % len(fmts)] if slot in act else fmts[0]


def film_for(acct: C.Account, item: dict) -> Tuple[Optional[Path], str]:
    """(the film's cues.json for run.py --film, a line to say about it). A reaction row (its inbox kind, or 'reacts'
    in its title) whose title names a drop in `films` is cut with that film: the finder then weighs the reactor,
    not the trailer's own cuts. The film job must be on disk (its cues.json and master.mp4)."""
    st = gta_settings(acct)
    title = str(item.get("title") or "")
    if item.get("kind") != "reaction" and not re.search(str(st.get("reaction_match") or "$^"), title, re.I):
        return None, ""
    for f in st.get("films") or []:
        if not re.search(str(f.get("match") or "$^"), title, re.I):
            continue
        p = Path(os.path.expanduser(str(f.get("cues") or "")))
        p = p if p.is_absolute() else acct.skill_dir / p
        if p.is_file() and p.with_name("master.mp4").is_file():
            return p, f"the film: {p.parent.name}"
        return None, f"the film is not on disk ({p}); cut without it"
    return None, ""


GTA_TEMPLATES = ("underlay", "pip", "gameplay")   # what the factory hands run.py --template
PLAYING = {"streamer", "physics", "gameplay"}      # the skill's board.py: a creator PLAYING feeds the GAMEPLAY lane


def playing_kinds(st: dict) -> set:
    """The inbox kinds a GAMEPLAY slot takes: gta_maker playing_kinds (default PLAYING; Roleplay's is rp)."""
    got = st.get("playing_kinds")
    return {str(k) for k in got} if isinstance(got, (list, tuple, set)) and got else set(PLAYING)


def broll_kinds(st: dict) -> Optional[set]:
    """The inbox kinds the b-roll lane (underlay, pip) takes: gta_maker broll_kinds, None (every kind) by default."""
    got = st.get("broll_kinds")
    return {str(k) for k in got} if isinstance(got, (list, tuple, set)) else None


BEST = "best"     # Best of GTA (2026-10-29): a slot whose layout follows the source dealt to it (best_template)


def gta_template(acct: C.Account, fmt: str) -> str:
    """The slot's format when the account allows it (slot_formats) and the skill cuts it, else underlay. "best" stays
    "best" where the account allows it: plan_gta deals it, and the source decides its layout (best_template)."""
    if fmt == BEST and BEST in (acct.slot_formats or acct.formats):
        return BEST
    return fmt if fmt in GTA_TEMPLATES and fmt in (acct.slot_formats or acct.formats) else "underlay"


def _lanes(acct: C.Account) -> set:
    """The lanes the account's slots can be cut in: "gameplay", "broll" or both (a format the skill does not cut is
    underlay, as gta_template says; a "best" slot can be either)."""
    out: set = set()
    for f in (acct.slot_formats or acct.formats or ["underlay"]):
        t = gta_template(acct, f)
        out |= {"gameplay", "broll"} if t == BEST else {"gameplay" if t == "gameplay" else "broll"}
    return out


def best_template(acct: C.Account, item: dict, st: Optional[dict] = None) -> str:
    """The layout a "best" slot takes from its source (Andrew 10-29: the best moments of the top creators): a creator
    PLAYING (playing_kinds; a stream clip is a streamer playing) the GAMEPLAY lane, where run.py picks gameplay,
    gameplay-pip or gameplay-clean off the measured facecam; a face reacting (best_pip_kinds) PIP, the face over what
    they react to; anything else (news, a breakdown, a scout pick) UNDERLAY, the voice over the footage it means."""
    st = st if st is not None else gta_settings(acct)
    kind = str(item.get("kind") or "")
    if kind in playing_kinds(st):
        return "gameplay"
    if kind in {str(k) for k in (st.get("best_pip_kinds") or [])}:
        return "pip"
    return "underlay"


def _host_key(item: dict) -> str:
    return str(item.get("name") or item.get("host") or item.get("channel") or "").strip().lower()


def plan_best(acct: C.Account, queue: List[dict], sources: List[dict], st: dict,
              board: Optional[dict] = None) -> tuple:
    """Best of GTA's deal for the "best" slots: ([(source, slots, template)], slots left). The sources in the inbox's
    order are its ranking (gta_sources: the scout's lifted finds first, the biggest first). One slot a source, then
    a second slot for a source longer than best_second_min_s while slots remain; never more than best_per_streamer
    of one streamer on the day (the board's clips made already count); a day with news (best_news_kinds) gets at
    least best_news_min of it, the best news on the earliest slot (fresh while it is news); the rest go to the
    slots in time order, the biggest first: Facebook shows the day's first posts best. Each slot dealt carries its
    "rank" (1 = the biggest), which Facebook's daily cap reads on a day with more slots than the cap (poster)."""
    queue = sorted(queue, key=lambda s: s["slot"])
    broll_ok = broll_kinds(st)
    cap = max(1, int(st.get("best_per_streamer") or 2))
    news_kinds = {str(k) for k in (st.get("best_news_kinds") or [])}
    made: Dict[str, int] = {}
    for k, t in ((board or {}).get("slots") or {}).items():
        if t.get("status") not in (B.OPEN, B.FAILED) and t.get("host"):
            made[_host_key(t)] = made.get(_host_key(t), 0) + 1
    fit = [i for i in sources
           if best_template(acct, i, st) == "gameplay" or broll_ok is None or i.get("kind") in broll_ok]
    picks: List[dict] = []

    def take(i: dict) -> bool:
        if len(picks) >= len(queue) or made.get(_host_key(i), 0) >= cap:
            return False
        picks.append(i)
        made[_host_key(i)] = made.get(_host_key(i), 0) + 1
        return True
    news = [i for i in fit if i.get("kind") in news_kinds]
    for i in news[: max(0, int(st.get("best_news_min") or 0))]:
        take(i)
    for i in fit:
        if all(i is not p for p in picks):
            take(i)
    if acct.clips_per_source >= 2:              # slots still open: a long source's second moment
        for i in list(picks):
            if len(picks) >= len(queue):
                break
            if not i.get("clip") and (est_secs(i, st) or 0) >= float(st.get("best_second_min_s") or 0):
                take(i)
    order = {id(i): n for n, i in enumerate(fit)}
    firsts = [p for n, p in enumerate(picks) if all(p is not q for q in picks[:n])]
    seconds = [p for n, p in enumerate(picks) if any(p is q for q in picks[:n])]
    seq = sorted(firsts, key=lambda i: order.get(id(i), len(fit))) + seconds      # the biggest first, seconds last
    lead = next((i for i in seq if i.get("kind") in news_kinds), None)
    if lead is not None:                        # the best news first, while it is fresh
        seq.remove(lead)
        seq.insert(0, lead)
    dealt: Dict[int, list] = {}
    for slot, item in zip(queue, seq):
        slot["rank"] = order.get(id(item), len(fit)) + 1
        dealt.setdefault(id(item), [item, []])[1].append(slot)
    plan = [(item, slots, best_template(acct, item, st)) for item, slots in dealt.values()]
    return plan, queue[len(seq):]


def takes_kind(acct: C.Account, kind: str) -> bool:
    """Whether some slot of the account cuts an inbox row of this kind (plan_gta's rule): a gameplay slot takes its
    playing_kinds, a b-roll slot its broll_kinds (every kind by default)."""
    st, lanes = gta_settings(acct), _lanes(acct)
    broll_ok = broll_kinds(st)
    return (("gameplay" in lanes and kind in playing_kinds(st))
            or ("broll" in lanes and (broll_ok is None or kind in broll_ok)))


def found_kind(acct: C.Account) -> str:
    """The inbox kind of a find the scout feeds that is not on the roster (a pick from its searches): "scout", which
    the b-roll lane takes, as it always was. An account whose slots are all gameplay (Stunts, Roleplay) has no
    b-roll lane, and its gameplay lane takes only its playing_kinds, so a "scout" row there was never cut (review
    2026-10-19): it gets the account's first playing kind, as a person's pick says the video fits the account."""
    if "broll" in _lanes(acct):
        return "scout"
    got = gta_settings(acct).get("playing_kinds")
    kinds = [str(k) for k in got] if isinstance(got, (list, tuple)) and got else sorted(PLAYING)
    return kinds[0]


def plan_gta(acct: C.Account, todo: List[dict], sources: List[dict], board: Optional[dict] = None) -> tuple:
    """([(source, slots, template)], [slots left with no source]). One source feeds up to clips_per_source slots in
    one template (one skill job per source). Gameplay slots take only creators PLAYING, and plan first since that
    kind is the scarcer; the b-roll slots take the other uploads in slot order, in their first slot's template.
    With `spread` (an owner call) the sources are dealt round-robin over the slots instead, and a short source
    gives fewer clips. A source over max_source_s is dealt only after every shorter one (by_length)."""
    st = gta_settings(acct)
    sources = by_length(sources, st)
    best = [s for s in todo if gta_template(acct, s["format"]) == BEST]          # Best of GTA (2026-10-29)
    todo = [s for s in todo if gta_template(acct, s["format"]) != BEST]
    gameplay = [s for s in todo if gta_template(acct, s["format"]) == "gameplay"]
    broll = [s for s in todo if gta_template(acct, s["format"]) != "gameplay"]
    plan, left, taken = [], [], set()
    playing, broll_ok = playing_kinds(st), broll_kinds(st)
    for lane, queue in (("gameplay", gameplay), ("broll", broll)):
        fit = [i for i in sources
               if i.get("url") not in taken and not (lane == "gameplay" and i.get("kind") not in playing)
               and not (lane == "broll" and broll_ok is not None and i.get("kind") not in broll_ok)]
        if CF.has_moments(acct):            # Stunts' formats (clipformats.py): each slot the source fitting its moment
            dealt, queue = CF.deal_by_moment(acct, queue, fit, st)
        elif st.get("spread"):
            dealt, queue = _deal_round_robin(acct, queue, fit, st)
        else:
            dealt = []
            for item in fit:
                if not queue:
                    break
                take, queue = queue[: acct.clips_per_source], queue[acct.clips_per_source:]
                dealt.append((item, take))
        for item, take in dealt:
            plan.append((item, take, "gameplay" if lane == "gameplay" else gta_template(acct, take[0]["format"])))
            taken.add(item.get("url"))
        left += queue
    if best:
        got, rest = plan_best(acct, best, [i for i in sources if i.get("url") not in taken], st, board)
        plan += got
        left += rest
    return sorted(plan, key=lambda p: p[1][0]["slot"]), sorted(left, key=lambda s: s["slot"])


def _deal_round_robin(acct: C.Account, queue: List[dict], fit: List[dict], st: dict) -> tuple:
    """Slot 1 to source A, slot 2 to B, ... then A again: three slots of one video never run back to back. A source
    under short_source_s gives at most short_source_clips. Returns ([(source, slots)], slots left over)."""
    def room(i: dict) -> int:
        try:
            secs = float(i.get("duration"))
        except (TypeError, ValueError):
            secs = None
        if secs is not None and secs < float(st.get("short_source_s") or 0):
            return max(0, min(acct.clips_per_source, int(st.get("short_source_clips") or acct.clips_per_source)))
        return acct.clips_per_source
    chosen: List[list] = []
    total = 0
    cap = float(st.get("max_source_s") or 0)
    for i in fit:
        if total >= len(queue):
            break
        r = room(i)
        if r and cap and (intake_secs(i, st) or 0) > cap:  # a long source (dealt last, by_length) takes only what nothing
            r = min(r, len(queue) - total)          # shorter covers: one 75-minute intake for one slot, not three
        if r:
            chosen.append([i, r, []])
            total += r
    rest, k = [], 0
    for s in queue:
        for _ in range(len(chosen)):
            src = chosen[k % len(chosen)]
            k += 1
            if len(src[2]) < src[1]:
                src[2].append(s)
                break
        else:
            rest.append(s)
    return [(i, take) for i, _, take in chosen if take], rest


def job_name(item: dict, day: date, template: str, tag: str = "") -> str:
    """One skill job per source. The video id ends it: two of Kai's VODs titled 'GTA 6 LIVE REACTION *First Look*'
    made one name, and the second intake was skipped as 'analyzed already' and cut the first video again. tag: the
    account's job_tag after "factory-" (factory-clips-20261120-...): every GTA account's jobs share the skill's work/,
    and Gaming's have none, so its names are as they were."""
    stem = re.sub(r"[^a-z0-9]+", "-", f"{item.get('channel', 'src')}-{item.get('title', '')}".lower()).strip("-")[:48]
    vid = re.sub(r"[^A-Za-z0-9_-]", "", video_id(item.get("url")) or "")[:20]
    tag = re.sub(r"[^a-z0-9]+", "-", (tag or "").lower()).strip("-")
    return f"factory-{tag + '-' if tag else ''}{day.strftime('%Y%m%d')}-{template}-{stem}" + (f"-{vid}" if vid else "")


def killed_ids(acct: C.Account) -> set:
    """The videos the kill list holds whole (a RIGHTS Pull or Decline): the night never deals them."""
    from . import reasons as R
    try:
        return set(R.killed_videos(acct))
    except Exception:  # noqa: BLE001  (an unreadable kill list kills nothing; doctor says so)
        return set()


def job_video(acct: C.Account, job: str) -> str:
    """The source video of a GTA skill job (its project.json source_url, as collect.gta_provenance reads it)."""
    from . import reasons as R
    for p in (acct.skill_dir / "work" / job / "project.json", acct.account_dir / "Jobs" / job / "_project" / "project.json"):
        try:
            url = json.loads(p.read_text()).get("source_url")
        except (OSError, ValueError, AttributeError):
            continue
        if url:
            return R.video_key(url)
    return ""


def drop_killed(acct: C.Account, job: str, cands: Optional[List[dict]]) -> Tuple[Optional[List[dict]], List[str]]:
    """The candidates whose seconds the kill list does not hold (more than 15% inside a killed span of the job's
    video), and one line per candidate left out."""
    from . import reasons as R
    if not cands:
        return cands, []
    try:
        kills = R.entries(acct)
    except Exception:  # noqa: BLE001
        return cands, []
    video = job_video(acct, job) if kills else ""
    if not video:
        return cands, []
    keep, said = [], []
    for c in cands:
        try:
            span = (float(c["start"]), float(c["end"]))
        except (KeyError, TypeError, ValueError):
            keep.append(c)
            continue
        e, why = R.span_killed(acct, video, span, kills)
        if e:
            said.append(f"candidate {c['n']} skipped (kill list: {why})")
        else:
            keep.append(c)
    return keep, said


# ----------------------------------------------------------------------------------------------- the picks
def gta_candidates(acct: C.Account, job: str) -> Tuple[Optional[List[dict]], List[str]]:
    """(the job's candidates worth cutting, best first, or None when moments.json cannot be read; one line per
    candidate skipped and why). Skipped: whatever moments.json marks "preflight refuses" (more music than the job's
    MUSIC cap; since 2026-10-19 a span with no whole sentence for its hook, which preflight's HOOK line refuses),
    more music than music_cap, a donation or housekeeping read, and on a long source the stream's intro and outro."""
    st = gta_settings(acct)
    try:
        mj = json.loads((acct.skill_dir / "work" / job / "moments.json").read_text())
        cands = [c for c in (mj.get("candidates") or []) if isinstance(c, dict) and c.get("n")]
    except (OSError, ValueError, AttributeError):
        return None, []
    seconds = float(mj.get("seconds") or 0)
    keep, skipped = [], []
    for c in cands:
        why = []
        music = float(c.get("music_share") or 0)
        cap = st.get("music_cap")
        refused = [str(e).replace(" - preflight refuses", "") for e in c.get("events") or []
                   if "preflight refuses" in str(e)]
        if refused:
            why += refused                  # "music 60%", "no whole sentence for a hook"
        elif cap is not None and music > float(cap):
            why.append(f"music {music:.0%}")
        furn = [str(f) for f in c.get("furniture") or []]
        why += [f for f in furn if f.startswith("housekeeping")]
        if seconds >= float(st.get("furniture_from_s") or 0):
            why += [f for f in furn if f in ("intro", "outro")]
        if why:
            skipped.append(f"candidate {c['n']} skipped ({', '.join(why)})")
        else:
            keep.append(c)
    return keep, skipped


def _overlap(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """The share of span a's seconds that span b also holds."""
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0])) / max(0.1, a[1] - a[0])


def next_pick(cands: List[dict], tried: set, spans: List[Tuple[float, float]], st: dict) -> Optional[dict]:
    """The best candidate not tried yet; with `spread`, one that does not repeat a clip already cut from this job."""
    for c in cands:
        if c["n"] in tried:
            continue
        try:
            span = (float(c["start"]), float(c["end"]))
        except (KeyError, TypeError, ValueError):
            span = None
        if st.get("spread") and span and any(_overlap(span, s) > float(st.get("max_overlap") or 1) for s in spans):
            continue
        return c
    return None


PREFLIGHT_STOP = re.compile(r"STOPPED at 'preflight")
PREFLIGHT_FAIL = re.compile(r"\[FAIL\]\s+([A-Z][A-Z0-9_-]*)")
# TOOL: the next candidate fails the same. LENGTH is retried since 10-18 (audit gta-yield): run.py already tried the
# same span at 1.0x, and another span's silences differ (10-17: all three TGG pip first picks LENGTH, 27.6-29.5 s)
NOT_RETRIED = {"TOOL"}
# a stop at preflight whose [FAIL] lines scrolled out of run.py's 24-line echo (3 of 10 on 10-17) is retried, unless
# the run's text says the tool itself refused
TOOL_SAID = re.compile(r"NOT READY: the tool itself|\[FAIL\]\s+TOOL\b|the tool moved")
# a stop at the grade (after the render) that another candidate may pass: the hook check (the first seconds show what
# the hook says) and dead air. Any other stop at the grade fails the slot, as before.
GRADE_STOP = re.compile(r"STOPPED at the grade\b[^\n]*")
GRADE_NAMED = re.compile(r"\(the ([A-Z][A-Z0-9_-]*) line above\)")
GRADE_BAD = re.compile(r"\[ BAD  \]\s+([A-Z][A-Z0-9_-]*)")
GRADE_RETRIED = {"HOOK", "DEADAIR", "STUTTER"}
# a stop at the band (the GTA skill, 654926c, 2026-10-18: no Rockstar or creator footage for these words, and the
# non-GTA reel is off): another candidate's words may match, so it is a refused pick, retried like preflight's
BAND_STOP = re.compile(r"STOPPED at the band\b")


def preflight_refusal(out: str) -> Optional[List[str]]:
    """run.py's STOPPED line first (stopped_gates: the gates read off preflight's whole output, gta-skill2), else the
    gates read off the log as before (_read_refusal)."""
    named = stopped_gates(out)
    return named if named is not None else _read_refusal(out)


def _read_refusal(out: str) -> Optional[List[str]]:
    """The gates preflight refused when the run stopped at preflight (['MUSIC', ...]), or the grade's gate when it
    stopped at the grade on one in GRADE_RETRIED (['HOOK at the grade']); None when it stopped anywhere else (the
    slate, the render, another grade line, a timeout) or did not stop."""
    if BAND_STOP.search(out) and not PREFLIGHT_STOP.search(out):
        return ["BAND"]
    if PREFLIGHT_STOP.search(out):
        return sorted(set(PREFLIGHT_FAIL.findall(out)))
    m = GRADE_STOP.search(out)
    if not m:
        return None
    named = set(GRADE_NAMED.findall(m.group(0))) or set(GRADE_BAD.findall(out))
    return sorted(f"{g} at the grade" for g in named) if named and named <= GRADE_RETRIED else None


# THE STOPPED LINE FIRST (gta-skill2, 2026-10-18): run.py names the gates preflight refused in its STOPPED line, read
# off preflight's WHOLE output ("STOPPED at 'preflight' (exit 1; refused: MUSIC, SET)"). The maker read only the
# step's 24-line echo, where a clip's own [FAIL] lines scroll away above the set's: three of the first night's ten
# refusals (10-17) read as no gate and were not retried.
STOPPED_REFUSED = re.compile(r"STOPPED at 'preflight[^'\n]*' \(exit -?\d+; refused: ([A-Z][A-Z0-9_-]*(?:, [A-Z][A-Z0-9_-]*)*)\)")


def fail_words(out: str) -> Dict[str, str]:
    """Each gate's own [FAIL] words in a preflight's output ({gate: words}), for `factory.py gates`."""
    got: Dict[str, str] = {}
    for m in re.finditer(r"\[FAIL\]\s+([A-Z][A-Z_]{2,11})\s+([^\n]{0,200})", out or ""):
        got.setdefault(m.group(1), " ".join(m.group(2).split()))
    return got


def stopped_gates(out: str) -> Optional[List[str]]:
    """The gates the LAST preflight STOPPED line in `out` names (sorted, as preflight_refusal gives them), or None when
    no STOPPED line names any (an older run.py, a preflight that printed no [FAIL] line, a stop anywhere else)."""
    found = STOPPED_REFUSED.findall(out or "")
    return sorted({g.strip() for g in found[-1].split(",")}) if found else None


def _last_preflight(out: str) -> str:
    """The last preflight run of a pick, from run.py's last '== preflight' step header on (the whole text when
    there is none). run.py asks preflight again at 1.0x after LENGTH or CUES: a gate that run fixed must not
    block the retry of the one that refused last (PROMISE, MUSIC). gta-skill, 2026-10-17."""
    i = out.rfind("\n== preflight")
    return out[i:] if i >= 0 else out


def _since(log: Path, offset: int) -> str:
    try:
        with open(log, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _size(log: Path) -> int:
    try:
        return log.stat().st_size
    except OSError:
        return 0


def _job_history(acct: C.Account, day: date, job: str) -> Tuple[set, List[Tuple[float, float]]]:
    """(candidate numbers already tried for this job, the spans already cut from it), off the day's board."""
    tried, spans = set(), []
    for s in ((B.read(acct, day) or {}).get("slots") or {}).values():
        if s.get("job") != job:
            continue
        tried.update(int(n) for n in s.get("picks_tried") or [])
        if s.get("pick_span") and s.get("status") not in (B.FAILED, B.OPEN, B.MAKING):
            spans.append((float(s["pick_span"][0]), float(s["pick_span"][1])))
    return tried, spans


def cut_gta_slots(cfg: C.Config, acct: C.Account, day: date, job: str, template: str, slots: List[dict],
                  log: Path, env: dict) -> List[str]:
    """One pick per slot from the job's candidates (gta_candidates), each delivered, packed and collected. A pick
    refused at preflight (any gate but TOOL, LENGTH included, and a stop whose gates scrolled out of the log unless
    it says the tool itself), or at the grade on HOOK or DEADAIR, tries the next candidate, up to `retries` more."""
    st = gta_settings(acct)
    scripts = acct.skill_dir / "scripts"
    lines: List[str] = []
    cands, skipped = gta_candidates(acct, job)
    cands, killed = drop_killed(acct, job, cands)
    skipped += killed
    if skipped:
        lines.append("  " + "; ".join(skipped))
    tried, spans = _job_history(acct, day, job)
    for order, s in enumerate(slots, start=1):
        hh = s["slot"].replace(":", "")
        retries = int(st.get("retries") or 0) if cands is not None else 0
        while True:
            if cands is None:            # no moments.json to read: the slot's place in the job, as before
                pick = {"n": order} if order not in tried else None
            else:
                pick = next_pick(CF.rank_for_slot(acct, job, cands, s), tried, spans, st)   # the slot's moment first
            if pick is None:
                _fail(cfg, acct, day, [s["slot"]], "no candidate left to cut on this source (the rest would be "
                                                    "refused, or were tried)", kind="no-candidate")
                lines.append(f"  {s['slot']}: FAILED (no candidate left on this source)")
                break
            n = int(pick["n"])
            tried.add(n)
            span = [pick["start"], pick["end"]] if "start" in pick and "end" in pick else None
            with B.edit(cfg, acct, day) as bd:
                t = bd["slots"][s["slot"]]
                t["picks_tried"] = sorted(set(t.get("picks_tried") or []) | {n})
                t["pick"], t["pick_span"] = n, span
                t.pop("kill_hit", None)
            at = _size(log)
            # the template again: the skill guesses it from a job's name, and a factory job name is not its shape
            from . import renderlock as RL      # each clip's render waits its turn in the render queue
            try:
                # a slot with a moment: its kind and time into project.json, the hook written from the video
                hook = CF.stamp_job(acct, job, s)
                with RL.hold(acct.slug, f"clip {s['slot']} (candidate {n})", log=log):
                    r = run_logged([PY_SKILLS, "run.py", job, "--pick", str(n), "--template", template,
                                    "--date", day.isoformat(), "--slot", hh, "--deliver"]
                                   + (["--hook", hook] if hook else []), log, cwd=scripts, env=env)
            except subprocess.TimeoutExpired as e:
                _fail(cfg, acct, day, [s["slot"]], f"candidate {n} ran past {int(e.timeout or 0) // 3600} hours "
                                                    f"and was stopped", kind="timeout")
                lines.append(f"  {s['slot']}: FAILED (candidate {n} timed out)")
                break
            if r.returncode == 0:
                if span:
                    spans.append((float(span[0]), float(span[1])))
                lines += _deliver_slot(cfg, acct, day, s["slot"], log, env)
                hit = (((B.read(acct, day) or {}).get("slots") or {}).get(s["slot"]) or {}).get("kill_hit")
                if hit and hit.get("candidate") == n and retries > 0:   # collect refused it: killed b-roll (reasons.py)
                    retries -= 1
                    with B.edit(cfg, acct, day) as bd:
                        t = bd["slots"][s["slot"]]
                        if t.get("status") == B.FAILED:          # being made again, as before the refusal
                            t["status"] = B.MAKING
                            t.pop("fail_kind", None)
                            t.pop("failed_at", None)
                        B.note(t, f"candidate {n} refused at collect (on the kill list); trying the next")
                    lines.append(f"  {s['slot']}: candidate {n} refused at collect (kill list); trying the next")
                    continue
                break
            gates = preflight_refusal(_last_preflight(_since(log, at)))     # the last preflight run only
            if gates is not None:                     # every refusal counted for `factory.py gates` (the loops thesis item 3)
                try:
                    from . import journal as J
                    J.record(f"gate {acct.slug}", "refused", gates=gates, day=str(day), slot=s.get("slot"), candidate=n,
                             why=fail_words(_last_preflight(_since(log, at))))
                except Exception:  # noqa: BLE001  (counting never changes a pick)
                    pass
            said = _since(log, at)                    # the pick's whole output: the tool may speak in any run of it
            tool = "TOOL" in (gates or []) or bool(TOOL_SAID.search(said))
            if gates is not None and not tool and not (NOT_RETRIED & set(gates)) and retries > 0:
                retries -= 1
                which = ", ".join(gates) or "its gates scrolled out of the log"
                with B.edit(cfg, acct, day) as bd:
                    B.note(bd["slots"][s["slot"]], f"candidate {n} refused ({which}); trying the next")
                lines.append(f"  {s['slot']}: candidate {n} refused ({which}); trying the next")
                continue
            _fail(cfg, acct, day, [s["slot"]], f"candidate {n} failed: {tail(log)}",
                  kind="tool" if tool else ("refused" if gates is not None else "run"))
            lines.append(f"  {s['slot']}: FAILED ({tail(log, 2)})")
            break
    return lines


def _deliver_slot(cfg: C.Config, acct: C.Account, day: date, slot: str, log: Path, env: dict) -> List[str]:
    """Pack the slot, collect it, and leave it failed (never 'making') when either does not happen."""
    line = pack_gta_slot(acct, day, slot, log, env)
    if line.startswith(f"  {slot}: packing FAILED"):
        _drop_pack(acct, day, slot)
        _fail(cfg, acct, day, [slot], line.strip(), kind="pack")
        return [line]
    # from the pack only: --deliver rebuilds the skill's Desktop run folder with every clip of the day under its own
    # slot names, and collect would hand those copies to whichever slot is still open
    got = K.collect(cfg, dataclasses.replace(acct, delivered=[]), day, only=[slot])
    out = [line] + got
    st = pack_state(acct, day, slot)
    with B.edit(cfg, acct, day) as bd:
        t = bd["slots"][slot]
        t.pop("question_written", None)
        w = st.get("written") if isinstance(st.get("written"), dict) else None
        if w and t.get("status") == B.READY:
            t["question_written"] = w
            B.note(t, f"comment question written by the factory ({w.get('why')}): {w.get('question')}")
        if t.get("status") == B.MAKING:
            t["status"] = B.FAILED
            t.update(fail_kind="collect", failed_at=fail_stamp())
            B.note(t, "packed, but collect did not take it: " + ("; ".join(x.strip() for x in got) or "nothing")[:400])
            out.append(f"  {slot}: FAILED (packed, not collected)")
    return out


def reset_stale_making(cfg: C.Config, acct: C.Account, day: date) -> List[str]:
    """A slot left 'making' by a run that stopped (a timeout, a crash, a restart) is failed, so this run and the
    morning retry make it again. Only with no other worker running; make_gta holds the account's render lock, so no
    other cut of this account is mid-way."""
    board = B.read(acct, day)
    stale = sorted(k for k, s in ((board or {}).get("slots") or {}).items() if s.get("status") == B.MAKING)
    if not stale or other_worker_running(acct):
        return []
    with B.edit(cfg, acct, day) as bd:
        for k in stale:
            t = bd["slots"][k]
            if t.get("status") == B.MAKING:
                t["status"] = B.FAILED
                t.update(fail_kind="stopped", failed_at=fail_stamp())
                B.note(t, "the run making this slot stopped; failed so the next make cuts it from another source")
    return [f"{', '.join(stale)}: left 'making' by a run that stopped; marked failed, made again"]


def make_gta(cfg: C.Config, acct: C.Account, day: date, only: Optional[List[str]] = None,
             dry_run: bool = False, skip: Optional[set] = None, scan: bool = True,
             run: Optional[IntakeRun] = None) -> List[str]:
    """One pass over the day's open and failed slots. skip: sources not to deal (make_passes: one skipped for time
    this run; a source this pass skips for time is added). scan: the watchlist scan first (a later pass of the same
    night reads the inbox the first pass's scan left). run: the worker's IntakeRun (make_passes starts each pass in
    it and settles it at the end); without one this pass is the run and settles its intakes itself."""
    own = run is None
    run = run if run is not None else IntakeRun()
    if own:
        run.start_pass()
    lines: List[str] = []
    try:
        lines = _make_gta(cfg, acct, day, only, dry_run, skip, scan, run)
    finally:
        if own and not dry_run:          # also on a kill (cmd_make turns SIGTERM into SystemExit)
            lines = lines + settle_intakes(cfg, acct, day, run)
    return lines


def _make_gta(cfg: C.Config, acct: C.Account, day: date, only: Optional[List[str]], dry_run: bool,
              skip: Optional[set], scan: bool, run: IntakeRun) -> List[str]:
    scripts = acct.skill_dir / "scripts"
    env = gta_env(acct)
    log = log_path(acct, day, "make")
    lines: List[str] = []
    if not dry_run:
        lines += reset_stale_making(cfg, acct, day)
    if not dry_run and scan:
        try:
            since = watch_since(gta_settings(acct))
            r = run_logged([PY_SKILLS, "watch.py", "scan", f"--since={since:g}"], log, cwd=scripts, env=env, timeout=1800)
            lines.append(f"watchlist scan: {'ok' if r.returncode == 0 else 'FAILED (' + tail(log, 3) + ')'}")
            run.now["scan_failed"] = r.returncode != 0
        except subprocess.TimeoutExpired:
            lines.append("watchlist scan: FAILED (ran past 30 minutes, stopped); cutting from the inbox as it is")
            run.now["scan_failed"] = True

    board = B.read(acct, day) or {}
    mine = [s for s in sorted(board.get("slots", {}).values(), key=lambda s: s["slot"])
            if s["status"] in (B.OPEN, B.FAILED) and (not only or s["slot"] in only)]
    held_rx = reactions_held(cfg, acct, day, board)
    until = str(gta_settings(acct).get("reactions_hold_until") or "03:30")
    for s in mine:
        if s["slot"] in held_rx:
            lines.append(f"{s['slot']} (reactions): left open for the day's REACTIONS clip (factory.py place) until "
                         f"{until} on {day}")
            if not dry_run:
                with B.edit(cfg, acct, day) as bd:
                    t = bd["slots"][s["slot"]]
                    if t.get("format") != "reactions":
                        B.note(t, f"format {t.get('format')} -> reactions")
                        t["format"] = "reactions"
                    B.note_once(t, REACTIONS_HELD.format(until=until))
        elif s.get("format") == "reactions":
            s["format"] = rotation_format(acct, s["slot"])     # no REACTIONS clip came: a normal clip, as dealt
            lines.append(f"{s['slot']} (reactions): no REACTIONS clip in it; cut as a normal clip ({s['format']})")
    todo = [s for s in mine if s["slot"] not in held_rx]
    if held_rx and not dry_run:
        board = B.read(acct, day) or board          # the held slots now say reactions (duo_held reads that)
    if not todo:
        return lines + ["no open slots"]
    # never a source this day already tried (tried_on: a failed intake), nor one this run skipped for time, nor one
    # another GTA account cut or holds (held_elsewhere)
    used = used_sources(acct) | held_elsewhere(cfg, acct) | tried_on(board) | set(skip or ())
    dead = killed_ids(acct)
    sources = gta_sources(acct, used | dead, day)
    n_dead = sum(1 for i in gta_sources(acct, used, day) if (video_id(i.get("url")) or i.get("url")) in dead) \
        if dead else 0
    if n_dead:
        lines.append(f"{n_dead} upload(s) not cut: their video is on the kill list (RIGHTS: factory.py killlist "
                     f"{acct.slug})")
    gave_up = given_up(acct)
    if gave_up:
        lines.append(f"{gave_up} upload(s) not cut again: their intake failed or timed out before (intake_failed "
                     f"on the inbox row)")
    n_skip = title_skips(acct)
    if n_skip:
        st = gta_settings(acct)
        what = ("is about leaks" if st.get("skip_titles") == GTA_MAKER_DEFAULTS["skip_titles"]
                else "is one the account's skip_titles turns away (leaks, and its own additions)")
        lines.append(f"{n_skip} upload(s) not cut: their title {what} (gta_maker skip_titles, 10-19)")
    off = night_off_topic(acct, day)
    if off is None:
        lines.append(f"gta_maker skip_off_topic is on, but config/scout.json has no readable block for {acct.slug}: "
                     f"no title was held to the game switch")
    n_off = off_topic_skips(acct, off)
    if n_off:
        lines.append(f"{n_off} upload(s) not cut: off topic for {acct.name} on {day} (config/scout.json "
                     f"damp.off_topic for that date, gta_maker skip_off_topic)")
    held = duo_held(acct, board)
    if held:
        kept = gta_sources(acct, used | held, day)
        n_held = len(sources) - len(kept)
        if n_held:
            lines.append(f"{n_held} upload(s) held for the day's REACTIONS duo (duo_skips_singles): not cut as singles")
        sources = kept
    if not sources:
        return lines + [f"no waiting uploads in {acct.account_dir / 'Publish' / 'inbox.json'}: nothing to cut"]
    # an intake that could not end in time (night_until; a run's start deadline) is not dealt now: the next one is
    late = [(i, why) for i, why in ((i, intake_too_late(cfg, acct, day, i)) for i in sources) if why]
    if late:
        too_late = {id(i) for i, _ in late}
        sources = [i for i in sources if id(i) not in too_late]
        for i, why in late:
            if skip is not None:
                skip.add(i["url"])
        lines += [f"not dealt now: {i.get('name') or i.get('channel') or 'a creator'}, "
                  f"{str(i.get('title') or '')[:50].rstrip()}: {why}" for i, why in late[:3]] + \
            ([f"  ... and {len(late) - 3} more"] if len(late) > 3 else [])
        if not sources:
            return lines + ["no waiting upload fits the time left: nothing to cut now"]

    plan, left = plan_gta(acct, todo, sources, board)
    for s in left:
        why = (f"no waiting upload of a creator PLAYING (inbox kinds "
               f"{', '.join(sorted(playing_kinds(gta_settings(acct))))})"
               if gta_template(acct, s["format"]) == "gameplay" else "no waiting upload left")
        lines.append(f"{s['slot']} ({gta_template(acct, s['format'])}): {why}; left open")

    for item, slots, template in sorted(plan, key=lambda p: shortest_first(p[0], gta_settings(acct))):   # the shortest intake first
        job = job_name(item, day, template, acct.job_tag)
        host = item.get("name") or item.get("channel") or "the creator"
        film, film_says = film_for(acct, item)
        lines.append(f"source: {host}, {item.get('title', '')[:70]} -> {', '.join(s['slot'] for s in slots)} ({template})"
                     + (f"; {film_says}" if film_says else ""))
        late = intake_too_late(cfg, acct, day, item)     # the time the earlier intakes of this pass took
        if late:
            lines.append(f"  not cut now: {late}")
            if skip is not None:
                skip.add(item["url"])
            if not dry_run:
                # failed (never left open): the next pass, the retry or a gap-fill takes the next source. Checked
                # inside the board's edit: a clip dropped or placed in since the night read the board is left as it
                # is, and a slot that had already failed keeps its own failure (fail_kind, failed_at)
                _fail(cfg, acct, day, [s["slot"] for s in slots], f"not cut from {host} now: {late}", kind="late",
                      only_if=(B.OPEN, B.FAILED))
            continue
        if dry_run:
            continue
        other = taken_by(cfg, acct, item)
        if other:                        # one source video, one GTA account: the slots stay open for the next source
            lines.append(f"  not cut: {other} took this upload meanwhile (one source, one GTA account)")
            continue
        with B.edit(cfg, acct, day) as bd:
            # a slot filled since the night read the board (a clip placed or dropped in) is left as it is
            gone = [s["slot"] for s in slots if bd["slots"][s["slot"]].get("status") not in (B.OPEN, B.FAILED)]
            slots = [s for s in slots if s["slot"] not in gone]
            for s in slots:
                t = bd["slots"][s["slot"]]
                for k in ("source_urls", "placed", "source"):   # what a clip placed or dropped here before carried
                    t.pop(k, None)
                if t.get("job") != job:      # a slot made again from another source: the old job's picks do not count
                    for k in ("picks_tried", "pick", "pick_span", "question_written"):
                        t.pop(k, None)
                t.update(status=B.MAKING, format=template, source_url=item["url"], source_title=item.get("title"),
                         host=host, job=job)
                if s.get("rank"):            # Best of GTA: the night's ranking, read by Facebook's daily cap
                    t["rank"] = int(s["rank"])
                tried = [u for u in t.get("sources_tried") or [] if u]      # the day's history (tried_on)
                t["sources_tried"] = (tried + ([item["url"]] if item["url"] not in tried else []))[-20:]
                t.pop("fail_kind", None)
                t.pop("failed_at", None)
                B.note(t, f"making from {item['url']}")
        if gone:
            lines.append(f"  {', '.join(gone)}: filled while the night ran (a clip placed or dropped in); left as it is")
        if not slots:
            continue
        try:
            lines += _make_source(cfg, acct, day, item, slots, template, job, host, film, log, env, run)
        except Exception as e:  # noqa: BLE001 - one source's surprise never stops the night or strands its slots
            why = f"the maker hit an error on this source: {type(e).__name__}: {str(e)[:300]}"
            _fail(cfg, acct, day, [s["slot"] for s in slots], why,
                  kind="deadline" if type(e).__name__ == "Busy" else "error", only_if=(B.MAKING,))
            lines.append(f"  FAILED: {why}")
    return lines


def prep_settings(acct: C.Account) -> dict:
    """gta_maker "prep" over GTA_MAKER_DEFAULTS' (its keys one by one, so a block naming only "on" keeps the rest)."""
    out = dict(GTA_MAKER_DEFAULTS["prep"])            # type: ignore[arg-type]
    got = ((acct.raw or {}).get("gta_maker") or {}).get("prep")
    if isinstance(got, dict):
        out.update(got)
    return out


def prep_gta(cfg: C.Config, acct: C.Account, day: date, dry_run: bool = False,
             now: Optional[datetime] = None) -> List[str]:
    """THE AFTERNOON PREP (2026-10-29, Andrew: "the clips get made at night"): the intakes the night would run for
    `day`, the posting day, run now, so the night only picks and renders. The night's own deal on the board as it
    stands (plan_gta: the same sources, slots and layouts, a REACTIONS slot held), up to prep.max_sources, the
    shortest first; each the night's own command (run.py <url> stage 1: download, demucs, whisper, the candidates) in
    the night's job name (job_name), which the night's run.py then finds "analyzed already". A job analyzed already
    is skipped; a windowed VOD is left to the night (its pick writes to the board); nothing starts whose estimate
    (length x intake_x) would end after prep.until. Never a board status, an inbox mark or a failed slot: if the
    night deals otherwise, a prepped job only waits on disk."""
    st, ps = gta_settings(acct), prep_settings(acct)
    now = now or cfg.now()
    board = B.read(acct, day) or B.new_board(cfg, acct, day)
    held = reactions_held(cfg, acct, day, board, now)
    todo = [dict(t) for t in sorted(board.get("slots", {}).values(), key=lambda t: t["slot"])
            if t.get("status") in (B.OPEN, B.FAILED) and t["slot"] not in held and not B.is_spare(t)]
    if not todo:
        return [f"prep {acct.name} {day}: no open slots"]
    used = used_sources(acct) | held_elsewhere(cfg, acct) | tried_on(board) | killed_ids(acct)
    plan, _ = plan_gta(acct, todo, gta_sources(acct, used, day), board)
    try:
        hh, mm = (int(x) for x in str(ps.get("until") or "20:30").split(":"))
        until = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except (TypeError, ValueError):
        until = now.replace(hour=20, minute=30, second=0, microsecond=0)
    lines = [f"prep {acct.name} {day}: {len(plan)} source(s) the night would deal"]
    env = gta_env(acct)
    log = log_path(acct, day, "prep")
    t = now
    for item, slots, template in sorted(plan, key=lambda p: shortest_first(p[0], st))[: int(ps.get("max_sources") or 8)]:
        job = job_name(item, day, template, acct.job_tag)
        host = item.get("name") or item.get("channel") or "the creator"
        work = acct.skill_dir / "work" / job
        if (work / "project.json").exists() and (work / "cues.json").exists():
            lines.append(f"  {host}: analyzed already ({job})")
            continue
        if windowed(item, st):
            lines.append(f"  {host}: a long VOD, left to the night's window pick")
            continue
        est = float(intake_secs(item, st) or 0) * float(st.get("intake_x") or 4)
        if t + timedelta(seconds=est) > until:
            lines.append(f"  {host}: not started, its intake (~{est / 60:.0f} min) would end after {until:%H:%M}")
            continue
        src, extra = stream_intake(item, st)
        cmd = ["nice", "-n", "10", PY_SKILLS, "run.py", src, "--template", template, "--date", day.isoformat(),
               "--slot", slots[0]["slot"].replace(":", ""), "--host", host, "--job", job, "--rights", "unverified"]
        cmd += extra + ([] if harvest_on(acct) else ["--no-harvest"])
        lines.append(f"  {host}: {str(item.get('title') or '')[:60]} -> {template} ({job})")
        if dry_run:
            continue
        from . import renderlock as RL
        try:
            with RL.hold(acct.slug, f"prep: {host}", log=log, until=until.timestamp()):
                r = run_logged(cmd, log, cwd=acct.skill_dir / "scripts", env=env)
        except RL.Busy as e:
            lines.append(f"    not started: {e}")
            break
        except subprocess.TimeoutExpired:
            lines.append("    stopped: ran past its time")
            continue
        lines.append("    prepped" if r.returncode == 0 else f"    intake failed (the night tries again): {tail(log, 2)}")
        t = cfg.now()
    return lines


class _LateAtTurn(Exception):
    """An intake whose turn in the render queue came too late to end in time (intake_too_late, asked again)."""

    def __init__(self, why: str, waited_s: float):
        super().__init__(why)
        self.why, self.waited_s = why, float(waited_s or 0)


def _make_source(cfg: C.Config, acct: C.Account, day: date, item: dict, slots: List[dict], template: str, job: str,
                 host: str, film: Optional[Path], log: Path, env: dict,
                 run: Optional[IntakeRun] = None) -> List[str]:
    """One source: the intake (analyze + the candidates), its inbox row marked cut, then its slots. A failed intake
    goes in the run (IntakeRun): held against the upload at the run's end only if an intake of its pass or a later
    one worked; a timeout is noted on its row at once."""
    run = run if run is not None else IntakeRun()
    keys = [s["slot"] for s in slots]
    first = slots[0]["slot"].replace(":", "")
    st = gta_settings(acct)
    src, extra = stream_intake(item, st)
    cmd = [PY_SKILLS, "run.py", src, "--template", template, "--date", day.isoformat(),
           "--slot", first, "--host", host, "--job", job, "--rights", "unverified"] + extra
    if not harvest_on(acct):
        cmd += ["--no-harvest"]          # a gameplay-only account never reads the harvested shots (gta_maker harvest)
    picked = False
    if windowed(item, st):
        # THE WINDOW INTAKE (gta_maker windows): the pick first, OUTSIDE the render queue (it waits on Twitch and
        # yt-dlp's metadata, not the CPU), into the job's own folder, where run.py --windows auto finds and reuses it
        stop = pick_windows(acct, item, job, host, log, env, run, keys, cfg, day)
        if stop is not None:
            return stop
        picked = True
        cmd += ["--windows", "auto", "--window-max", str(int(st.get("window_max") or 3)),
                "--window-min", f"{float(st.get('window_min_s') or 600) / 60:g}",
                "--window-len", f"{float(st.get('window_len_s') or 1500) / 60:g}",
                "--window-total", f"{float(st.get('window_total_s') or 2400) / 60:g}"]
    from . import renderlock as RL      # the intake waits its turn in the render queue (state/renderlock)
    at = _size(log)
    try:
        with RL.hold(acct.slug, f"intake: {host}", log=log) as turn:
            # the night shift (2026-10-19): the Politics's steps share the queue, so a turn may come long after the
            # deal; an intake that could no longer end in time is not started then (its slots fail 'late' and the
            # upload is not blamed)
            late = intake_too_late(cfg, acct, day, item, picked=picked) if turn.waited_s >= 60 else ""
            if late:
                raise _LateAtTurn(late, turn.waited_s)
            r = run_logged(cmd + (["--film", str(film)] if film else []), log, cwd=acct.skill_dir / "scripts",
                           env=env)
    except _LateAtTurn as e:
        why = f"{e.why} (its turn in the render queue came after {int(e.waited_s // 60)} min of waiting)"
        _fail(cfg, acct, day, keys, f"not cut from {host} now: {why}", kind="late", only_if=(B.MAKING,))
        run.bad(item, why, keys, hold=False, tally=False)
        return [f"  not cut now: {why}"]
    except subprocess.TimeoutExpired as e:
        why = f"intake ran past {int(e.timeout or 0) // 3600} hours and was stopped"
        _fail(cfg, acct, day, keys, why, kind="timeout")
        run.bad(item, why, keys, hold=None)       # a 4-hour timeout always counts against the upload
        return ["  intake FAILED: timed out, stopped", mark_inbox_failed(acct, item, why, timeout=True)]
    except RL.Busy as e:                          # not started by the run's deadline: the upload was never tried
        _fail(cfg, acct, day, keys, f"intake {e}", kind="deadline")
        run.bad(item, str(e), keys, hold=False, tally=False)
        return [f"  intake not started: {e}"]
    if r.returncode != 0:
        if TOOL_SAID.search(_since(log, at)):     # the tool itself, not the source: the inbox row is not marked
            _fail(cfg, acct, day, keys, f"intake failed: {tail(log)}", kind="tool")
            run.bad(item, "the tool itself", keys, hold=False)
            return [f"  intake FAILED (the tool itself): {tail(log, 2)}"]
        _fail(cfg, acct, day, keys, f"intake failed: {tail(log)}", kind="intake")
        run.bad(item, f"intake failed: {tail(log, 2)}", keys)
        return [f"  intake FAILED: {tail(log, 2)}",
                "  inbox: held against this upload only if another intake of this pass or a later one works"]
    run.good()
    return [mark_inbox_cut(acct, item, day, job)] + cut_gta_slots(cfg, acct, day, job, template, slots, log, env)


def stream_intake(item: dict, st: dict) -> Tuple[str, List[str]]:
    """(the URL run.py takes in, its extra arguments) for one inbox row. A Twitch clip with its VOD (Best of GTA,
    2026-10-29: scout.stream_finds): the stream around it, vod_before_s before the clip's offset to vod_after_s after
    its end, as one --windows span (the offset runs 30 to 60 s off the stream's own clock, and a 30 s clip is too
    short to cut a 30 to 60 s clip from); a Kick clip (Kick gives no offset) and every other row: its own URL."""
    if item.get("clip") and item.get("vod_url") and item.get("vod_offset") is not None:
        try:
            off = float(item["vod_offset"])
            dur = float(item.get("duration") or 0)
            a = max(0, int(off - float(st.get("vod_before_s") or 90)))
            b = int(math.ceil(off + dur + float(st.get("vod_after_s") or 120)))
            return str(item["vod_url"]), ["--windows", f"{a}-{b}"]
        except (TypeError, ValueError):
            pass
    return str(item["url"]), []


WINDOW_OFF_TOPIC = re.compile(r"^no chapter of this VOD is on topic")


def pick_windows(acct: C.Account, item: dict, job: str, host: str, log: Path, env: dict, run: IntakeRun,
                 keys: List[str], cfg: C.Config, day: date) -> Optional[List[str]]:
    """The window pick for a windowed source (vodwindow.py pick -> <skill>/work/<job>/windows.json). None when it
    picked windows (the intake goes on); else the source's lines, its slots failed. A VOD whose chapters are all
    another game (Just Chatting, a Call of Duty night) is turned down for good: its inbox row goes off-topic, never
    dealt again. Any other empty pick (no signal answered: the network, Twitch) is a plain intake failure, held
    against the upload only if another intake of the run worked (IntakeRun), as a failed download is."""
    st = gta_settings(acct)
    work = acct.skill_dir / "work" / job
    work.mkdir(parents=True, exist_ok=True)
    wj = work / "windows.json"
    try:
        have = json.loads(wj.read_text()) if wj.exists() else {}
    except (OSError, ValueError):
        have = {}
    if have.get("url") == item.get("url") and have.get("windows"):
        return None                                      # picked on an earlier pass: reused
    cmd = [PY_SKILLS, "vodwindow.py", "pick", item["url"], f"--max-windows={int(st.get('window_max') or 3)}",
           f"--min={float(st.get('window_min_s') or 600) / 60:g}", f"--max={float(st.get('window_len_s') or 1500) / 60:g}",
           f"--total={float(st.get('window_total_s') or 2400) / 60:g}", f"--json={wj}"]
    try:
        run_logged(cmd, log, cwd=acct.skill_dir / "scripts", env=dict(env, QM_WORK=str(work)),
                   timeout=int(st.get("window_pick_timeout_s") or 1800))
    except subprocess.TimeoutExpired:
        why = "the window pick ran past its time and was stopped"
        _fail(cfg, acct, day, keys, f"{why} ({host})", kind="intake")
        run.bad(item, why, keys)
        return [f"  window pick FAILED: {why}"]
    try:
        have = json.loads(wj.read_text()) if wj.exists() else {}
    except (OSError, ValueError):
        have = {}
    if have.get("windows"):
        tot = sum(float(w["end"]) - float(w["start"]) for w in have["windows"])
        with B.edit(cfg, acct, day) as bd:
            for k in keys:
                t = bd["slots"].get(k)
                if t:
                    B.note(t, f"windows: {len(have['windows'])} of the VOD, {tot / 60:.0f} min "
                              f"({', '.join(_hms(w['start']) + '-' + _hms(w['end']) for w in have['windows'])})")
        return None
    why = str(have.get("why_none") or "the pick wrote nothing")
    if WINDOW_OFF_TOPIC.match(why):
        _fail(cfg, acct, day, keys, f"not cut from {host}: {why}", kind="intake", only_if=(B.MAKING,))
        return [f"  no window: {why}", mark_inbox_off_topic(acct, item, why)]
    _fail(cfg, acct, day, keys, f"no window in {host}'s VOD: {why}", kind="intake", only_if=(B.MAKING,))
    run.bad(item, f"no window: {why}", keys)
    return [f"  no window: {why}",
            "  inbox: held against this upload only if another intake of this pass or a later one works"]


def _hms(s: object) -> str:
    m, sec = divmod(int(float(s)), 60)
    return f"{m // 60}:{m % 60:02d}:{sec:02d}"


def mark_inbox_off_topic(acct: C.Account, item: dict, why: str) -> str:
    """A VOD the window pick found off topic by its own chapters: its inbox row goes off-topic (what watch.py writes
    for a title off topic), with the reason, so no night deals it again."""
    p = acct.account_dir / "Publish" / "inbox.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return "  inbox: not marked off-topic (inbox.json unreadable)"
    items = data.get("items")
    vid = video_id(item.get("url"))
    hit = items.get(vid) if isinstance(items, dict) and vid else None
    if not isinstance(hit, dict):
        hit = next((r for r in _inbox_rows(data) if r.get("url") == item.get("url")), None)
    if hit is None:
        return "  inbox: no row for this source (not marked)"
    hit.update(status="off-topic", off_topic_why=str(why)[:200],
               off_topic_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    try:
        tmp = p.with_suffix(".json.factory-tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(p)
    except OSError as e:
        return f"  inbox: not marked off-topic ({e})"
    return "  inbox: marked off-topic (its chapters are another game); not dealt again"


PACK = r"""
import json, shutil, sys
from datetime import date
from pathlib import Path
publish, date_s, slot, out_root = sys.argv[1:5]
ask = sys.argv[5] if len(sys.argv) > 5 else ""   # the factory's comment question (the slate's was the default)
sys.path.insert(0, publish)
import day_folder as DF
ent = json.loads((Path(publish) / "schedule.json").read_text()).get(date_s, {}).get(slot)
if not ent or not ent.get("slug"):
    sys.exit(f"schedule.json has no clip at {date_s} {slot}")
job, slug = ent["job"], ent["slug"]
if ask:
    # into the slate that holds the clip (the work slate first, as clip_entry reads it): later packs say the same
    for sp in DF.slate_paths(job):
        if not sp.exists():
            continue
        sl = json.loads(sp.read_text())
        hit = next((c for c in sl.get("clips", []) if c.get("slug") == slug), None)
        if hit is not None:
            hit["question"] = ask
            sp.write_text(json.dumps(sl, indent=1, ensure_ascii=False))
            break
clip, cfg = DF.clip_entry(job, slug)
dst = Path(out_root) / f"{slot.replace(':', '')} {slug}"
if dst.exists():
    shutil.rmtree(dst)
dst.mkdir(parents=True)
jd = DF.JOBS / job
for p in list((jd / "clips").glob(f"*{slug}*.mp4")) + list((jd / "clips").glob(f"*{slug}*.jpg")) + list((jd / "captions").glob(f"*{slug}*.srt")):
    shutil.copy2(p, dst / p.name)
packs = DF.gc.packs(clip, cfg, date.fromisoformat(date_s)) if (DF.gc is not None and clip) else None
DF.paste_files(dst, clip, cfg, packs)
st = {"slug": slug, "job": job, "hook": str(clip.get("hook") or ""), "template": str(clip.get("template") or ""),
      "source_title": str(cfg.get("source_title") or ""), "question": "", "question_default": False, "shape": "",
      "who": "", "hook_s": ""}
try:
    if packs:
        st["question_default"] = bool(packs["tiktok"].get("question_default"))
        st["who"] = str(packs["tiktok"].get("who") or "")
        st["question"] = str(packs["tiktok"].get("pinned") or "")
    if DF.gc is not None and clip:
        pt = DF.gc._parts(clip, cfg, None)
        st.update(question=str(pt.get("question") or st["question"]), shape=str(pt.get("shape") or ""),
                  hook_s=str(pt.get("hook_s") or ""))
except Exception as e:  # noqa: BLE001
    st["error"] = f"{type(e).__name__}: {e}"
(Path(out_root) / (slot.replace(":", "") + ".pack.json")).write_text(json.dumps(st, indent=1))
print(f"packed {slot} {slug}: {', '.join(sorted(p.name for p in dst.iterdir()))}")
"""


def gta_pack_root(acct: C.Account, day: date) -> Path:
    return C.STATE / acct.slug / "delivered" / day.isoformat()


def pack_state(acct: C.Account, day: date, slot: str) -> dict:
    """What the last pack of a slot said about its words (PACK writes <HHMM>.pack.json beside the slot folders)."""
    try:
        return json.loads((gta_pack_root(acct, day) / f"{slot.replace(':', '')}.pack.json").read_text())
    except (OSError, ValueError):
        return {}


def _drop_pack(acct: C.Account, day: date, slot: str) -> None:
    """A failed pack leaves no half folder behind for the end-of-night collect to take."""
    for d in gta_pack_root(acct, day).glob(f"{slot.replace(':', '')} *"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


def pack_gta_slot(acct: C.Account, day: date, slot: str, log: Path, env: dict) -> str:
    """The account's day_folder.py only builds its own seven slots, so the factory packs each of its slots itself,
    with the skill's own copy engine (gtacopy through day_folder's helpers), into state/<account>/delivered/<date>/.
    A clip whose comment question is the skill's generic default is packed again with one written here
    (question_for)."""
    out = gta_pack_root(acct, day)
    out.mkdir(parents=True, exist_ok=True)
    cmd = [PY_SKILLS, "-c", PACK, str(acct.account_dir / "Publish"), day.isoformat(), slot, str(out)]
    try:
        r = run_logged(cmd, log, cwd=acct.account_dir / "Publish", env=env, timeout=600)
    except subprocess.TimeoutExpired:
        return f"  {slot}: packing FAILED (ran past 10 minutes)"
    if r.returncode != 0:
        return f"  {slot}: packing FAILED ({tail(log, 2)})"
    st = pack_state(acct, day, slot)
    ask, why = question_for(st, gta_settings(acct))
    if not ask:
        return f"  {slot}: packed"
    try:
        r = run_logged(cmd + [ask], log, cwd=acct.account_dir / "Publish", env=env, timeout=600)
    except subprocess.TimeoutExpired:
        return f"  {slot}: packing FAILED (the second pack, with the written question, ran past 10 minutes)"
    if r.returncode != 0:
        return f"  {slot}: packing FAILED (the second pack, with the written question: {tail(log, 2)})"
    st2 = pack_state(acct, day, slot)
    st2["written"] = {"question": ask, "why": why, "was": st.get("question") or "", "slug": st.get("slug") or ""}
    (out / f"{slot.replace(':', '')}.pack.json").write_text(json.dumps(st2, indent=1))
    return f"  {slot}: packed; comment question written ({why}): {ask}"


# the comment question (2026-10-17): gtacopy gives every clip with no question its shape's default, and the 'event'
# one, "Would you have made it out?", went out on breakdowns, news and stunts; an editor rewrote them by hand at 04:30
QUESTION_WORDS = {"is", "are", "was", "were", "do", "does", "did", "can", "could", "will", "would", "should", "who",
                  "what", "when", "where", "why", "how", "which", "has", "have", "had"}
CHASE = re.compile(r"\b(cops?|police|chase[ds]?|escape[ds]?|wanted|getaway|heist|pursuit|busted|stars?)\b", re.I)
_AUX = re.compile(r"^(?P<subj>\S+(?:\s+\S+){0,5}?)\s+(?P<aux>is|are|was|were|can|will|could|would|should|has|have)\s+"
                  r"(?P<rest>\S.*)$", re.I)
_LOWER_FIRST = {"this", "that", "these", "those", "the", "a", "an", "his", "her", "their", "our", "my", "your", "he",
                "she", "they", "we", "you", "it", "there", "everyone", "nobody", "someone", "people", "every"}
_PARTICIPLE = re.compile(r"^(been|got|gotten|made|done|gone|seen|taken|given|shown|known|become|\w+ed|\w+en)$", re.I)


def _plain(hook: str) -> str:
    """The hook as a sentence when the skill gave none: quotes and end marks off, first letter up, GTA kept."""
    h = re.sub(r"[“”\"]", "", str(hook or "")).strip().rstrip(".!?").strip()
    if h.isupper():
        h = re.sub(r"\bgta\b", "GTA", h[:1].upper() + h[1:].lower(), flags=re.I)
    return h


def question_from_hook(hook_s: str) -> str:
    """A question made from the clip's own hook: the hook itself when it asks one ('Is this map even Leonida?'), or a
    statement turned round on its verb ('Lucia is calling from prison' -> 'Is Lucia calling from prison?'). '' when
    the hook has no such verb."""
    h = str(hook_s or "").strip().rstrip(".!").strip()
    if not h:
        return ""
    words = h.split()
    if h.endswith("?") or words[0].lower() in QUESTION_WORDS:
        return h.rstrip("?").strip() + "?"
    m = _AUX.match(h)
    if not m:
        return ""
    subj, aux, rest = m.group("subj"), m.group("aux").lower(), m.group("rest").rstrip("?").strip()
    if subj.split()[0].lower() in _LOWER_FIRST:
        subj = subj[:1].lower() + subj[1:]
    if aux in ("has", "have") and not _PARTICIPLE.match(rest.split()[0]):
        return f"{'Does' if aux == 'has' else 'Do'} {subj} have {rest}?"   # 'GTA 6 has dog breeds' -> 'Does GTA 6 have'
    return f"{aux.capitalize()} {subj} {rest}?"


def question_for(st: dict, settings: Optional[dict] = None) -> Tuple[str, str]:
    """(the comment question to write, why) when the pack's question is the skill's default and that default does
    not fit; ('', '') to keep the pack as it is. Kept: a typed question, the defaults of the shapes in
    question_defaults_ok ('Is iShowSpeed right?' on a quote names the face), and 'Would you have made it out?' on a
    chase. Written: the hook's own question, else a stunt or play question, else a plain one. Never refused: a
    refused pack would leave the slot with no clip at all."""
    if not (settings or GTA_MAKER_DEFAULTS).get("question_rewrite"):
        return "", ""                       # the skill writes the question (gtacopy, gaming ae31f22)
    if not st or not st.get("question_default"):
        return "", ""
    ok = set((settings or GTA_MAKER_DEFAULTS).get("question_defaults_ok") or [])
    shape = str(st.get("shape") or "event")
    hook_s = str(st.get("hook_s") or "") or _plain(str(st.get("hook") or ""))
    if shape == "quote" and "quote" in ok:
        return "", ""
    if shape != "quote":
        q = question_from_hook(hook_s)
        if q and q.rstrip("?").lower() != str(st.get("question") or "").rstrip("?").lower():
            return q[:150], "from the hook"
    if shape in ok and shape != "quote":
        return "", ""
    if shape == "event" and CHASE.search(f"{st.get('hook') or ''} {hook_s}"):
        return "", ""
    if str(st.get("template") or "").startswith("gameplay"):
        return "Could you pull this off?", "a play, no question in the hook"
    return "Did you see that coming?", "no question in the hook"


def question_card_line(slot: dict) -> str:
    """For the review card (review.py): the comment question the factory wrote, to read before Approve ('' if none)."""
    w = slot.get("question_written") if isinstance(slot.get("question_written"), dict) else None
    if not w or slot.get("status") != B.READY or (w.get("slug") and w.get("slug") != slot.get("slug")):
        return ""                    # none, not waiting for a sign-off, or written for a clip this slot no longer holds
    return f"Comment question written by the factory: \"{w.get('question')}\". Change it before Approve if it does not fit."


def fail_stamp() -> str:
    """When a slot failed, to the microsecond (make_passes tells a pass's new failures from older ones by it)."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _fail(cfg: C.Config, acct: C.Account, day: date, slots: List[str], why: str, kind: str = "",
          only_if: Optional[tuple] = None) -> List[str]:
    """Fail the slots, saying why. fail_kind and failed_at say what failed when: make_passes stops when a pass's new
    failures are all the tool itself ("tool"); the other kinds are intake, timeout, no-candidate, refused, run,
    pack, collect, late, deadline, error. only_if: fail only the slots whose status, read inside the board's edit, is
    one of these (a slot the worker is not making itself may be filled by a drop or a place meanwhile); a slot that
    was FAILED already keeps its own fail_kind and failed_at then (one not cut 'late' was not tried again). Returns the
    slots failed."""
    done = []
    with B.edit(cfg, acct, day) as bd:
        for k in slots:
            t = bd["slots"][k]
            if only_if is not None and t.get("status") not in only_if:
                continue
            was_failed = only_if is not None and t.get("status") == B.FAILED and t.get("fail_kind")
            t["status"] = B.FAILED
            B.note(t, why[:600])
            if not was_failed:
                t.update(fail_kind=kind or "other", failed_at=fail_stamp())
            done.append(k)
    return done


# --------------------------------------------------------------------------------------------------- stand-in
def make_standin(cfg: C.Config, acct: C.Account, day: date, only: Optional[List[str]] = None,
                 seconds: float = 35.0, out_root: Optional[Path] = None) -> List[str]:
    """Draw a labelled test clip and paste files for each open slot, in the skills' delivered shape, then collect."""
    root = out_root or (C.STATE / acct.slug / "standin" / day.isoformat())
    root.mkdir(parents=True, exist_ok=True)
    board = B.read(acct, day) or B.new_board(cfg, acct, day)
    for s in sorted(board["slots"].values(), key=lambda s: s["slot"]):
        if s["status"] not in B.FILLABLE or (only and s["slot"] not in only):
            continue
        hh = s["slot"].replace(":", "")
        d = root / f"{hh} standin-{acct.slug}-{hh}"
        d.mkdir(parents=True, exist_ok=True)
        mp4 = d / f"01-standin-{hh}.mp4"
        if not mp4.exists():
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=1080x1920:rate=30:duration={seconds}",
                 "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(mp4)],
                check=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(mp4), "-frames:v", "1",
                            str(d / f"01-standin-{hh}.jpg")], check=True)
        words = {
            "youtube.txt": f"STAND-IN {s['slot']} title\n\nA rehearsal clip for {acct.name}. #shorts",
            "tiktok.txt": f"Stand-in caption for {s['slot']} #gta6 #rehearsal",
            "facebook.txt": f"A stand-in post for {acct.name} at {s['slot']}.",
            "reels.txt": f"Stand-in reel caption {s['slot']}",
        }
        for name, text in words.items():
            (d / name).write_text(text + "\n")
    return K.collect(cfg, acct, day, extra=[root], only=only)


# ------------------------------------------------------------------------------------------------- dispatch
def make(cfg: C.Config, acct: C.Account, day: date, only: Optional[List[str]] = None, dry_run: bool = False,
         standin: bool = False) -> List[str]:
    with B.edit(cfg, acct, day):  # make sure the board exists
        pass
    if standin:
        return make_standin(cfg, acct, day, only)
    if acct.maker == "gta":
        lock = render_lock(acct)
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w") as lf:
            if not dry_run:
                fcntl.flock(lf, fcntl.LOCK_EX)  # one make of this account at a time (the queue shares the Mac)
            return make_gta(cfg, acct, day, only, dry_run)
    if acct.maker == "politics":
        return [f"{acct.name} is made by its Hermes bot (a hearing needs its cast checked and a slate written).",
                f"  the bot delivers into {acct.delivered_dirs(day)[0]}, then runs: factory.py collect {acct.slug} --date {day}"]
    if acct.maker == "generic":         # a client account: viral-clip-engine in its neutral look (generic.py)
        from . import generic as G
        return G.make(cfg, acct, day, only, dry_run)
    if acct.maker == "lab":             # Lab: Andrew's own console takes, the skill's TEST lane (lab.py)
        from . import lab as L
        return L.make(cfg, acct, day, only, dry_run)
    if acct.maker == "garage":          # 2026-10-29: Cars's night maker, one vehicle a clip (garage.py)
        from . import garage as GA
        return GA.make(cfg, acct, day, only, dry_run)
    if acct.maker == C.HAND:            # 2026-10-29: nothing cuts for it yet (Cars until its own maker)
        return [f"{acct.name} has no maker yet: nothing is cut for it.",
                f"  drop a finished clip in {cfg.inbox_root / acct.name}: it takes the next open slot and waits for "
                f"Approve on Clip Review"]
    raise SystemExit(f"unknown maker {acct.maker!r}")


def _nap(seconds: float) -> None:
    time.sleep(seconds)


def make_passes(cfg: C.Config, acct: C.Account, day: date, only: Optional[List[str]] = None,
                say=print, sleep: Optional[Callable[[float], None]] = None) -> int:
    """The worker's make (factory.py make --_worker: the 21:00 night, and a gap-fill's or the 03:45 retry's
    background make), with its closing collect. GTA (2026-10-18, audit gta-yield; the night shift 2026-10-19): one
    pass, a collect, then another while a slot is still open or failed and a waiting source would be dealt to it, the
    clock is before pass_until (05:30, shift.LAST_PASS) and the last pass's new failures were not all the tool itself
    (TOOL: every pick would fail the same), up to `passes` (8) in all. 10-17's night made one pass: a source whose
    intake failed failed its slots for the day. A failed source is never dealt again that day (tried_on), nor one this
    run skipped for time. The account's render lock is held across the passes, so no other make of it slips in
    between; the render queue still gives the Politics's steps (and the other GTA accounts') their turns between this
    run's steps.
    A pass whose every intake failed ends the passes (two or more, or the watchlist scan failed too, or a second such
    pass): the network, yt-dlp or YouTube's bot check, not the videos; and a plain intake failure is held against its
    upload only if an intake of its pass or a later one worked (IntakeRun, settled at the end), so such a night leaves
    the inbox for the GTA second half and the 03:45 retry. A run with no start deadline of its own (the 21:00 night;
    the retry and the GTA second half bring theirs) gets render_until (05:50): no intake or render starts after it.
    stop_at (06:15, shift.DONE_BY) is the worker's own watchdog: a step still running then is stopped (ShiftOver) and
    the slots it was making are failed, so nothing lands while Andrew signs.
    RESCANS (review of the night shift, 2026-10-18): a night worker that finds no waiting source left for its open or
    failed slots (or whose every intake failed: the network) does not end before rescan_until (03:20): it waits
    rescan_every (40) minutes, nothing of it in the render queue meanwhile (the other accounts have the Mac), then scans
    the watchlist again and makes another pass. A network night's failures are freed first (not held against their
    uploads), so the pass after the wait may deal them again. A rescan pass does not count against `passes`.
    Every check of a pass stays as it is. Other makers: one make and one collect, as before. Returns the passes.
    sleep: the wait between rescans (default _nap, time.sleep; tests move a fake clock)."""
    if acct.maker != "gta":
        for ln in make(cfg, acct, day, only):
            say(ln)
        for ln in K.collect(cfg, acct, day):
            say(ln)
        return 1
    with B.edit(cfg, acct, day):  # make sure the board exists
        pass
    skip: set = set()
    run = IntakeRun()
    n = 0
    from . import renderlock as RL
    hhmm, set_by = str(gta_settings(acct).get("render_until") or ""), False
    if hhmm and RL.env_until() is None:
        try:
            by = night_time(cfg, day, hhmm)
        except SystemExit:
            by = None
        if by is not None and cfg.now() < by:
            os.environ[RL.UNTIL_ENV] = str(int(by.timestamp()))     # every RL.hold of this run reads it
            set_by = True
            say(f"no intake or render of this run starts after {by:%H:%M} on {by:%m-%d} (render_until): a slot not "
                f"started by then fails 'deadline'")
    stop_hhmm, stopped = str(gta_settings(acct).get("stop_at") or ""), False
    try:
        stop = night_time(cfg, day, stop_hhmm) if stop_hhmm else None
    except SystemExit:
        stop = None
    if stop is not None and cfg.now() < stop:
        # every run_logged of this run reads it, on the process clock (the time left from now: a test's clock may not
        # be the real one)
        _STOP_AT[:] = [time.time() + (stop - cfg.now()).total_seconds()]
        say(f"the worker stops itself at {stop:%H:%M} on {stop:%m-%d} (stop_at): a step still running then is "
            f"stopped and its slots fail, so nothing lands while Andrew signs")
    lock = render_lock(acct)
    lock.parent.mkdir(parents=True, exist_ok=True)
    rescans, scan = 0, True
    try:
        with open(lock, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)  # one make of this account at a time, for every pass
            while True:
                n += 1
                started = fail_stamp()
                run.start_pass()
                for ln in make_gta(cfg, acct, day, only, skip=skip, scan=scan, run=run):
                    say(ln)
                scan = False
                for ln in K.collect(cfg, acct, day):
                    say(ln)
                kind: List[str] = []
                more, why = another_pass(cfg, acct, day, only, n, started, skip, run, kind=kind, rescans=rescans)
                if why:
                    say(why)
                if more:
                    continue
                wake = rescan_at(cfg, acct, day, kind[0] if kind else "")
                if wake is None:
                    return n
                rescans += 1
                if kind and kind[0] == "network":
                    for ln in free_failed_passes(cfg, acct, day, run):
                        say(ln)
                say(f"== rescan {rescans}: waiting until {wake:%H:%M}, then a fresh watchlist scan and another pass "
                    f"(nothing of {acct.name} renders meanwhile; rescans until "
                    f"{gta_settings(acct).get('rescan_until')}, then the GTA second half and the 03:45 retry take over)")
                fcntl.flock(lf, fcntl.LOCK_UN)      # another make of this account is not held up while nothing renders
                try:
                    (sleep or _nap)(max(0.0, (wake - cfg.now()).total_seconds()))
                finally:
                    fcntl.flock(lf, fcntl.LOCK_EX)
                scan = True
    except ShiftOver as e:
        stopped = True
        say(f"stopped (stop_at {stop_hhmm}): {e}; nothing new lands while Andrew signs")
        return n
    finally:
        _STOP_AT.clear()
        for ln in settle_intakes(cfg, acct, day, run):    # also on a kill (cmd_make turns SIGTERM into SystemExit)
            say(ln)
        if stopped:
            for ln in reset_stale_making(cfg, acct, day):
                say(ln)
        if set_by:
            os.environ.pop(RL.UNTIL_ENV, None)


def another_pass(cfg: C.Config, acct: C.Account, day: date, only: Optional[List[str]], done: int, started: str,
                 skip: Optional[set] = None, run: Optional[IntakeRun] = None,
                 kind: Optional[List[str]] = None, rescans: int = 0) -> Tuple[bool, str]:
    """(make another pass?, the line that says why or why not), after `done` passes; `started`: when the last one
    began (B.utcnow()), to tell its new failures from older ones; `run`: what the intakes did so far (IntakeRun).
    kind: given a list, why not is appended to it as one word (passes, clock, tool, network, full, no-source), so
    make_passes knows when a rescan may still find something (no-source, network: rescan_at). rescans: how many of
    the `done` passes followed a rescan wait (they do not count against `passes`)."""
    st = gta_settings(acct)
    passes = max(1, int(st.get("passes") or 1))
    nxt = done + 1
    why = kind if kind is not None else []
    if done - max(0, rescans) >= passes:
        why.append("passes")
        return False, (f"no pass {nxt}: {passes} passes made (gta_maker passes)" if passes > 1 else "")
    hhmm = str(st.get("pass_until") or "")
    if hhmm:
        try:
            until = night_time(cfg, day, hhmm)
        except SystemExit:
            until = None
        if until is not None and cfg.now() >= until:
            why.append("clock")
            return False, (f"no pass {nxt}: it is past {hhmm} (pass_until); the GTA second half and the 03:45 retry "
                           f"make what is left before 05:50")
    board = B.read(acct, day) or {}
    slots = board.get("slots") or {}
    new = [s for s in slots.values() if s.get("status") == B.FAILED and str(s.get("failed_at") or "") >= started]
    if new and all(s.get("fail_kind") == "tool" for s in new):
        why.append("tool")
        return False, (f"no pass {nxt}: the tool itself refused ({', '.join(sorted(s['slot'] for s in new))}, TOOL); "
                       f"every pick fails the same until the {acct.skill_dir.name} skill is clean and committed")
    if run is not None and run.all_failed():
        p = run.passes[-1]
        twice = sum(1 for k in range(len(run.passes)) if run.all_failed(k)) >= 2
        if p["failed"] >= 2 or p.get("scan_failed") or twice:
            why.append("network")
            return False, (f"no pass {nxt}: every intake of pass {done} failed ({p['failed']})"
                           + (" and so did the watchlist scan" if p.get("scan_failed") else "")
                           + (", as in an earlier pass" if twice and p["failed"] < 2 else "")
                           + "; likely the network, yt-dlp or YouTube's bot check, not the videos, so they are not "
                             "held against their uploads; the GTA second half and the 03:45 retry try again")
    held = reactions_held(cfg, acct, day, board)
    todo = []
    for s in sorted(slots.values(), key=lambda s: s["slot"]):
        if s.get("status") not in (B.OPEN, B.FAILED) or (only and s["slot"] not in only) or s["slot"] in held:
            continue
        t = dict(s)
        if t.get("format") == "reactions":
            t["format"] = rotation_format(acct, t["slot"])        # as make_gta cuts it
        todo.append(t)
    if not todo:
        why.append("full")
        return False, f"no pass {nxt}: no slot is open or failed"
    used = used_sources(acct) | held_elsewhere(cfg, acct) | tried_on(board) | set(skip or ()) | killed_ids(acct)
    sources = gta_sources(acct, used, day)
    dh = duo_held(acct, board)
    if dh:
        sources = gta_sources(acct, used | dh, day)
    sources = [i for i in sources if not intake_too_late(cfg, acct, day, i)]     # as make_gta deals them
    plan, _ = plan_gta(acct, todo, sources)
    if not plan:
        why.append("no-source")
        return False, f"no pass {nxt}: no waiting source left for {', '.join(s['slot'] for s in todo)}"
    return True, (f"== pass {nxt} of {passes}: {', '.join(s['slot'] for s in todo)} open or failed; "
                  f"{len(plan)} more source(s) to try")


def rescan_at(cfg: C.Config, acct: C.Account, day: date, why: str) -> Optional[datetime]:
    """When the worker, its passes ended for `why` (another_pass's kind), scans the watchlist again: now +
    rescan_every, when why is no-source or network, the night has begun (21:00 on the eve, shift.START: a daytime
    make never waits) and the wait ends by rescan_until (03:20) and before pass_until (05:30); else None (the worker
    ends, as before)."""
    if why not in ("no-source", "network"):
        return None
    st = gta_settings(acct)
    try:
        every = float(st.get("rescan_every") or 0)
    except (TypeError, ValueError):
        every = 0.0
    if every <= 0:
        return None
    from . import shift as SH
    now = cfg.now()
    wake = now + timedelta(minutes=every)
    try:
        if now < night_time(cfg, day, SH.START):
            return None
        for key in ("rescan_until", "pass_until"):
            hhmm = str(st.get(key) or "")
            if hhmm and wake >= night_time(cfg, day, hhmm):
                return None
    except SystemExit:
        return None
    return wake


def free_failed_passes(cfg: C.Config, acct: C.Account, day: date, run: IntakeRun) -> List[str]:
    """Before a rescan after a network night: the failures of every pass whose intakes all failed are not held
    against their uploads (as settle_intakes would leave them at the run's end): they leave their slots'
    sources_tried now, so the pass after the wait may deal them again once the network is back."""
    bad = {k for k in range(len(run.passes)) if run.all_failed(k)}
    out = [e for e in run.pending if e["pass"] in bad]
    if not out:
        return []
    run.pending = [e for e in run.pending if e["pass"] not in bad]
    with B.edit(cfg, acct, day) as bd:
        for e in out:
            for k in e["slots"]:
                t = bd["slots"].get(k)
                if t and t.get("sources_tried"):
                    t["sources_tried"] = [u for u in t["sources_tried"] if u != e["item"].get("url")]
    return [f"{len(out)} intake failure(s) of a pass where every intake failed: not held against their upload(s); "
            f"the pass after the wait may deal them again"]


def spawn_background(argv: List[str], acct: C.Account, day: date) -> str:
    """Run this same command detached, kept awake, one worker per account."""
    lock = C.STATE / acct.slug / ".worker.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    probe = open(lock, "w")
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return f"{acct.name}: a worker is already running (state/{acct.slug}/.worker.lock); not starting a second"
    fcntl.flock(probe, fcntl.LOCK_UN)
    probe.close()
    log = log_path(acct, day, "worker")
    cmd = ["/usr/bin/caffeinate", "-i", sys.executable, str(C.ROOT / "factory.py")] + \
          [a for a in argv if a != "--background"] + ["--_worker"]
    with open(log, "a") as f:
        f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)}\n")
        p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True, cwd=str(C.ROOT))
    return f"{acct.name}: worker started (pid {p.pid}), log {log}"


_HELD: set = set()   # accounts whose worker lock this process holds (hold_worker_lock)


def hold_worker_lock(acct: C.Account):
    lock = C.STATE / acct.slug / ".worker.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock, "w")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _HELD.add(acct.slug)
    return f


def other_worker_running(acct: C.Account) -> bool:
    """A worker other than this process holds the account's lock (a second open of the lock in the holder itself
    would read as held, so the holder knows itself from _HELD)."""
    if acct.slug in _HELD:
        return False
    lock = C.STATE / acct.slug / ".worker.lock"
    if not lock.exists():
        return False
    with open(lock, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False
