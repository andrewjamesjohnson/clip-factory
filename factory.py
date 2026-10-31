#!/usr/bin/env python3
"""factory.py - ten clips a day per account, made overnight (launchd + Claude tasks, no Hermes), signed by a
person, then scheduled.

    python3 factory.py plan    <account> [--date D]            the day's board: 10 slots, 07:00 to 23:00 (default: tomorrow)
    python3 factory.py plan    <account> [--date D] --slots 13:00,15:00 --format gameplay   set open slots' format
    python3 factory.py plan    lab [--date D] --resync   the board takes the dated volume rule's slots
    python3 factory.py make    <account> [--date D] [--slots 05:00,07:00] [--standin] [--dry-run] [--background]
                               (GTA in the background: up to 8 passes until 05:30, shortest first, nothing after 05:50,
                               stopped by itself at 06:15; out of videos it scans again every 40 minutes until 03:20:
                               the night shift, clipfactory/shift.py)
    python3 factory.py collect <account> [--date D] [--from DIR] [--quiet]   take what the skill delivered into the drafts
    python3 factory.py note    <account> "text" [--date D]     the bot's report, carried by the morning message
    python3 factory.py night-retry <account> [--date today] [--dry-run]   03:45 (launchd): once the night's worker and
                               the GTA second half stopped, the failed GTA slots made again (every slot after the 06:45
                               sign-off), no make after 05:30, nothing new after 05:50, stopped at 06:15 (nightretry.py)
    python3 factory.py shift begin|check|end|wait <account> --date D --task TASK [--until HH:MM] [--why ..]
    python3 factory.py shift status [account] [--date D]   the night shift (21:00-06:15): who works which board
    python3 factory.py shift day                           the posting day a run starting now fills (before 07:00: today)
    python3 factory.py nightly <account>                       plan + make + collect for tomorrow (what the bot's cron runs)
    python3 factory.py status  [account] [--date D]
    python3 factory.py morning [account]                       the short report the Mac sends at 06:30
    python3 factory.py review                                  the review page, http://127.0.0.1:8781 (always on: launchd)
    python3 factory.py approve <account> [--date D] (--all | --slots 07:00,08:45) --by NAME   sign + schedule: it posts itself
    python3 factory.py pull    <account> [--date D] --slots 07:00 --by NAME [--code C] [--reason ..]   take a scheduled clip back
                               before it goes out; a code (or words) puts it on the kill list (clipfactory/reasons.py)
    python3 factory.py retry   <account> [--date D] --slots 07:00 --by NAME    try again the platforms that gave up
    python3 factory.py stale   <account> --date D --slots 07:50 --why "..." [--by NAME] [--clear]   the night's "do not sign":
                               Approve all and the automatic sign-off skip it; only its own Approve signs it
    python3 factory.py bandcheck [account] [--date D]       cuts that no longer fit their outlet's band (default: today
                               and tomorrow), each with the igcut --pick that makes a new one
    python3 factory.py igcut   <account> --date D --slug SLUG (--end S | --segments a-b,c-d | --pick) [--hook ..] [--no-hook2]
                               [--words FILE]    render a clip's own Instagram cut (the Politics: 30 to 50s) in the nightly run
    python3 factory.py ytcut   <account> --date D (--missing | --slug SLUG (--segments a-b,c-d | --end S | --pick | --from-instagram))
                               the same for YouTube: a network or C-SPAN clip over 60s gets its own cut (59s or less)
    python3 factory.py decline <account> [--date D] --slots 07:00 --by NAME [--reason ...] [--remake]
                               [--reasons hook,framing]        what was wrong, by the reason codes (verdicts.py)
                               [--code C]   one code (reasons.py, the one list: wrong-name, framing, cut, audio, length,
                               repeat, incomplete, text, hook, rights, other with --reason); a Decline puts the clip on
                               the kill list (Make again does not)
    python3 factory.py killlist <account> [--all] [--check URL [--span A-B]] [--lift ID --why ..]
                               what the night and the bench never cut again: spans, videos, b-roll
    python3 factory.py place   <account> --date D --slot 19:15 (--job JOB --slug SLUG | --clip X.mp4 --words W)
                               [--why ..] [--check]   one finished clip into one slot, a draft for Approve
    python3 factory.py place   gaming --date D --slot 19:15 --release   no REACTIONS clip: the night fills it
    python3 factory.py place   <account> --date D --slot 08:55 --replace ...   a newer render of the clip in that unsigned
                               slot takes its place (a clip the day has in another slot is refused: one clip, one slot)
    python3 factory.py bench status [account]                     the spare clips (state/<account>/bench)
    python3 factory.py bench add <account> <clip folder | job --slug S> [--until D] [--why ..]   a checked spare
    python3 factory.py bench fill <account|all> --date today [--dry-run]   06:18 (launchd): spares into empty slots
    python3 factory.py bench retire <id> [--why ..]   |   bench render gaming --job J --slug S   (daylight)
    python3 factory.py schedule <account> [--date D] [--dry-run]   hand off what is signed (Approve already does)
    python3 factory.py connect youtube <account> --client client.json   the owner's own Google sign-in, once per channel
    python3 factory.py connect youtube <account> --client client.json --with comments,analytics   also the comments
                               (youtube.force-ssl) and the numbers (yt-analytics.readonly) for the social layer
    python3 factory.py connect meta    <account>                        Facebook Page + linked Instagram (system user token from Business Settings)
    python3 factory.py connect tiktok  <account> --client-key KEY       TikTok Login Kit for Desktop (his own app, AJJ Clip Factory)
    python3 factory.py connect upload-post <account> [--profile NAME]   unused: TikTok through the paid Upload-Post (declined)
    python3 factory.py inbox [--quiet]                              take clips dropped in ~/Desktop/Clip Drop/<Account>/ (each waits for Approve)
    python3 factory.py post-due [--dry-run]                         post signed clips whose slot has come (launchd, every 10 min)
    python3 factory.py stats [--days 120] [--no-refresh]           every post's numbers -> the Clip Factory Views dashboard files
    python3 factory.py scout <account|all> [--hours 36] [--feed N] [--quiet]   the news scout: today's most clippable finds
    python3 factory.py scout <account> --pick ID [ID ...] --why "..." [--by NAME]   (or --kill): a call on a find
    python3 factory.py casts [--json] | --confirmed [--since today|yesterday|HH:MM] | --stills | --notify
                               the Politics's cast queue (castqueue.py): who waits for Andrew's check, read only.
                               Only he confirms, on the review page (Casts to check); no command does it
    python3 factory.py doctor [--fix-paths]
    python3 factory.py account add <slug> --name "Client" [--seconds 30-60] [--slots 09:00,13:00,19:00]
                               [--approvers "Name"] [--handle @x] [--dry-run]   a client account: its own folder,
                               approvers and page, made by the generic maker (CLIENTS.md)
    python3 factory.py account list | page <slug> | job <slug> <url> --name "Job" | remove <slug>
    python3 factory.py social tick|studio|checkpoints|learn|ideas [--quiet]   the social layer's timers (launchd)
    python3 factory.py social send [--quiet]                      send what is queued now (comments, replies, cards)
    python3 factory.py social status [account]                    each account's switches, holds and warm-up
    python3 factory.py social stop [--why ..] | start             Stop everything social (signed clips keep posting)
    python3 factory.py social set <account> <key> <value>         one switch or setting (state/<account>/social/)
    python3 factory.py social on <account> <part> [--practice|--testers] | off <account> <part>   every part starts off
    python3 factory.py social created <account> YYYY-MM-DD        the day the account was made (the warm-up clock)
    python3 factory.py social tester <account> --platform P --id ID --name N [--remove]   a tester on the Meta app
    python3 factory.py social probe <account> <what>              ask a platform what the account may do (by hand)
    python3 factory.py social holds [account] | release <account> <platform|family> --by NAME
    python3 factory.py social forget <account> --person platform:id   a person out of the log and the inbox
    python3 factory.py social export <account> --to DIR | import --from DIR [--replace]   move it to another Mac
    python3 factory.py engage | rules | extras | cards | coach | checkpoints | learn | ideas ...   each social part's
                               own commands (Inbox, Automations, first comments, Cards, the coach, the checkpoints,
                               Reports, Ideas); a part not built yet says so
    python3 factory.py hermes install|sync|status|remove [--scratch DIR]
    python3 factory.py timer install [--from-hermes] [--only NAME] | status | check | remove   post-due, inbox, stats on
                               macOS launchd (--only usage: that one job, the others left running)
    python3 factory.py lab status|deal|bank|import-bank|inbox|frames|variety|script|redo|obs-setup|obs-check|setup|tidy
                               lab ...   Lab (clipfactory/lab.py): the test bank, takes, frames, scripts.
                               No command sets an outcome: Andrew does, on the Lab tab of Clip Review
    python3 factory.py renderlock run --account A --what "W" [--until HH:MM] [--stop-at HH:MM] [--wait MIN] [--turn MIN]
                               [--max-hold MIN] -- CMD ...
                               wait for the render queue (one render at a time on this Mac), run CMD, let go;
                               never starts at or after --until (exit 75); stopped at --stop-at (exit 124)
    python3 factory.py renderlock status [--json]                 what is rendering, what waits, the last turns
    python3 factory.py usage [--json]                     Claude's week: % used, the clip tasks' reserve, room for chats
    python3 factory.py usage gate --task TASK             each daytime Claude task's first step: exit 3 = skip this run
                               (the week is in the night shift's reserve); the night's own tasks always go
    python3 factory.py usage check [--notify] [--quiet]   every 30 min (launchd): count, a Mac notice when it gets worse
                               (clipfactory/usage.py; the chat hook is `python3 clipfactory/usage.py hook`)

Accounts are in config/accounts.json. The state (one board per account per day) is in state/.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipfactory import board as B          # noqa: E402
from clipfactory import collect as K        # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import makers as M         # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import signoff as S        # noqa: E402
from clipfactory import stats as ST         # noqa: E402

# the Dock app that opens the review page and posts the 06:30 notice (source clipfactory/ClipReview.applescript)
CLIP_REVIEW_APP = Path(os.path.expanduser("~/Applications/Clip Review.app"))

def slots_arg(s: Optional[str]) -> Optional[List[str]]:
    return [C.normal_slot(x) for x in s.split(",") if x.strip()] if s else None


def show(lines: List[str]) -> None:
    for ln in lines:
        print(ln)


def off_here(cfg, acct, what: str) -> bool:
    """A command that makes or schedules for one account asks first: an account switched off ("active": false) or run
    by another machine ("machine") gets one plain line and nothing else, exit 0, so a timer or a bot that asks is told,
    not failed. doctor, connect and status still take it (it is set up before it is switched on)."""
    why = cfg.why_off(acct)
    if why:
        print(f"{what}: {why}")
    return bool(why)


def volume_words(acct, day) -> str:
    """The dated volume rule in force on `day`, in a few words ('' for an account without "volume")."""
    if not (acct.raw or {}).get("volume"):
        return ""
    rule = acct.volume_rule(day)
    slots = acct.slots_for(day)
    why = (rule or {}).get("why") or ("no rule holds the day: its daily_target" if rule is None else "")
    return f"the volume rule for {day}: {len(slots)} a day ({', '.join(slots)})" + (f", {why}" if why else "")


def resync(cfg, acct, day, bd) -> List[str]:
    """plan --resync: a board made before the volume rule changed takes the rule's slots now. A slot the rule lists
    and the board lacks is added as open; an OPEN slot (no clip, nobody making it) the rule no longer lists is taken
    out. A filled, making, failed or signed slot is never touched."""
    want = acct.slots_for(day)
    fresh = B.new_board(cfg, acct, day)["slots"]
    out = []
    for k in want:
        if k not in bd["slots"]:
            bd["slots"][k] = fresh.get(k) or {"slot": k, "publish_at": cfg.slot_time(day, k).isoformat(),
                                              "format": (acct.formats or ["clip"])[0], "status": B.OPEN, "notes": []}
            B.note(bd["slots"][k], "added by plan --resync (the volume rule lists it)")
            out.append(f"  {k}: added (open)")
    for k in sorted(bd["slots"]):
        s = bd["slots"][k]
        if k in want:
            continue
        if s.get("status") == B.OPEN and not s.get("clip"):
            del bd["slots"][k]
            out.append(f"  {k}: taken out (open; the rule no longer lists it)")
        else:
            out.append(f"  {k}: kept ({s.get('status')}): only an open slot is taken out")
    bd["slots"] = dict(sorted(bd["slots"].items()))
    return out or ["  the board already has the rule's slots"]


def cmd_plan(cfg, a):
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    if off_here(cfg, acct, "plan"):
        return
    changed: List[str] = []
    if getattr(a, "resync", False) and not (acct.raw or {}).get("volume"):
        raise SystemExit(f"plan --resync: {acct.name} has no dated volume rule (\"volume\" in config/accounts.json); "
                         f"its boards keep their slots")
    with B.edit(cfg, acct, day) as bd:
        if getattr(a, "resync", False):
            changed = resync(cfg, acct, day, bd)
        if a.format:
            if not a.slots:
                raise SystemExit("--format needs --slots (e.g. --slots 13:00,15:00)")
            if a.format not in acct.slot_formats:
                raise SystemExit(f"{acct.name}: format {a.format!r} is not one of {', '.join(acct.slot_formats)} "
                                 "(slot_formats in config/accounts.json)")
            for k in slots_arg(a.slots) or []:
                s = bd["slots"].get(k)
                if not s:
                    raise SystemExit(f"{acct.name} {day} has no slot {k}")
                if s["status"] not in (B.OPEN, B.FAILED):
                    raise SystemExit(f"{k} is {s['status']}: only an open or failed slot changes format")
                if s["format"] != a.format:
                    B.note(s, f"format {s['format']} -> {a.format}")
                    s["format"] = a.format
    print(f"{acct.name}, posting day {day}: {len(bd['slots'])} slots")
    show(changed)
    for s in sorted(bd["slots"].values(), key=lambda s: s["slot"]):
        print(f"  {s['slot']}  {s['format']:<9} {s['status']}")
    if acct.daily_target > len(acct.slots):
        print(f"  note: daily_target {acct.daily_target} is more than the {len(acct.slots)} slots listed")
    rule = volume_words(acct, day)
    if rule:
        print(f"  {rule}")
        want = acct.slots_for(day)
        if sorted(bd["slots"]) != sorted(want):
            print(f"  the board has {len(bd['slots'])} slots, the rule says {len(want)}: "
                  f"factory.py plan {acct.slug} --date {day} --resync")


def cmd_make(cfg, a):
    import signal as _sig

    def _stop(signum, frame):                # a kill stops the running step's whole group, as Ctrl-C does
        raise SystemExit(128 + signum)
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    if off_here(cfg, acct, "make"):
        return
    for _s in (_sig.SIGTERM, _sig.SIGHUP):
        _sig.signal(_s, _stop)
    if a.standin:
        from clipfactory import livestate
        if livestate.is_live(C.STATE):      # 35s test clips pass every length wall: never on the real boards
            raise SystemExit(f"refused: --standin draws test clips, never on the live boards ({C.STATE}). Clone them "
                             f"(cp -cR {C.STATE} <scratch>/state), set CLIP_FACTORY_STATE to the clone, run it there.")
    if a.background:
        print(M.spawn_background(sys.argv[1:], acct, day))
        return
    hold = M.hold_worker_lock(acct) if a._worker else None
    try:
        if a._worker and not a.dry_run and not a.standin:
            # the worker (--background, the 21:00 night): GTA makes up to 8 passes until 05:30 (makers.make_passes)
            M.make_passes(cfg, acct, day, slots_arg(a.slots), say=print)
            print(status_line(acct, day))
            return
        show(M.make(cfg, acct, day, slots_arg(a.slots), dry_run=a.dry_run, standin=a.standin))
        if not a.dry_run:
            show(K.collect(cfg, acct, day))
            print(status_line(acct, day))
    finally:
        if hold:
            hold.close()


def cmd_collect(cfg, a):
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    if off_here(cfg, acct, "collect"):
        return
    lines = K.collect(cfg, acct, day, extra=[Path(os.path.expanduser(a.frm))] if a.frm else None)
    if a.quiet and not any(":" in ln and ln.startswith("  ") for ln in lines):
        return  # a scheduled check with nothing new stays silent
    show(lines)
    print(status_line(acct, day))


def cmd_night_retry(cfg, a):
    """The 03:45 GTA retry (audit GTA-10; the night shift's GTA fallback): clipfactory/nightretry.py says its guards."""
    from clipfactory import nightretry as NR
    acct, day = cfg.account(a.account), C.parse_day(a.date or "today", cfg)
    if off_here(cfg, acct, "night-retry"):
        return
    NR.run(cfg, acct, day, dry_run=a.dry_run)
    print(status_line(acct, day))


def cmd_note(cfg, a):
    """The bot's report for the morning message (e.g. hearings waiting for a cast check)."""
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    with B.edit(cfg, acct, day) as bd:
        bd["report"] = a.text.strip()[:4000]
        bd["report_at"] = B.utcnow()
    print(f"saved the report on {acct.name} {day}")


def cmd_nightly(cfg, a):
    acct = cfg.account(a.account)
    if off_here(cfg, acct, "nightly"):
        return
    day = C.parse_day(a.date, cfg)
    a.date = day.isoformat()
    with B.edit(cfg, acct, day):
        pass
    if acct.maker == "politics":
        show(K.collect(cfg, acct, day))
        print(status_line(acct, day))
        return
    if a._worker:
        a.slots, a.dry_run, a.standin = None, False, False
        return cmd_make(cfg, a)
    print(M.spawn_background(["make", acct.slug, "--date", day.isoformat()], acct, day))


def status_line(acct, day) -> str:
    bd = B.read(acct, day)
    if not bd:
        return f"{acct.name} {day}: no board"
    n = B.counts(bd)
    return f"{acct.name} {day}: " + ", ".join(f"{v} {k}" for k, v in sorted(n.items()))


def cmd_status(cfg, a):
    day = C.parse_day(a.date, cfg)
    for acct in ([cfg.account(a.account)] if a.account else cfg.accounts.values()):
        bd = B.read(acct, day)
        print(status_line(acct, day))
        if not bd:
            continue
        for s in sorted(bd["slots"].values(), key=lambda s: s["slot"]):
            extra = s.get("slug") or ""
            if s["status"] in (B.FAILED, B.REFUSED) and s.get("notes"):
                extra = s["notes"][-1].split(" ", 1)[-1][:110]
            secs = f"{s['clip']['seconds']:.0f}s" if s.get("clip") else ""
            print(f"  {s['slot']}  {s['status']:<9} {s.get('format', ''):<8} {secs:>4}  {extra}")


NOTICE_CUT = 220    # the Mac notification's body is cut here


def compact_line(counts: List[tuple]) -> str:
    """The morning notice's first line, one short word per account (accounts.json "short"): "GTA 8/10 · Clips 9/10 ·
    RP 7/10 · Politics 7/10 ready". It always fits the notice's cut: with four accounts the full lines run past it
    (335 characters on 10-19 with two), so this line says the count for every account before any detail."""
    parts = [f"{short[:12]} {ready}/{total}" if total else f"{short[:12]} no board" for short, ready, total in counts]
    line = " · ".join(parts) + " ready"
    return line if len(line) <= NOTICE_CUT else line[:NOTICE_CUT]


def notice_body(compact: str, out: List[str]) -> str:
    """The Mac notification: the compact line, then the detail (every line but the indented ones and the link)."""
    detail = " ".join(ln for ln in out if not ln.startswith(("Review", "  ")))
    return (compact + (" " + detail if detail else ""))[:NOTICE_CUT]


def cmd_morning(cfg, a):
    """Short, for a chat message. Silent (no output) when there is nothing for a person to look at."""
    day = cfg.now().date() if a.date == "today" else C.parse_day(a.date, cfg)
    out = []
    counts: List[tuple] = []             # (short, ready, slots) per account, for the notice's first line
    from clipfactory import accounts as AC   # a client the owner does not sign is not in his morning notice
    for acct in ([cfg.account(a.account)] if a.account else [x for x in cfg.accounts.values() if AC.signs_here(cfg, x)]):
        bd = B.read(acct, day)
        short = acct.short or acct.name.split()[0]
        if not bd and acct.fed_by_hand():     # nothing cuts for it (2026-10-29): no board = nothing dropped yet
            continue
        if not bd:
            out.append(f"{acct.name}: no clips made for {day} yet.")
            counts.append((short, 0, 0))
            continue
        n = B.counts(bd)
        shown = [s for s in bd["slots"].values() if not B.empty_spare(s)]    # an empty spare slot is never missing
        ready, total = n.get(B.READY, 0), len(shown)
        counts.append((short, ready, total))
        line = f"{acct.name}, {day}: {ready} of {total} drafts ready to sign"
        from clipfactory import autosign as A    # the automatic sign-off: when they sign themselves, who waits
        auto = A.morning_words(cfg, acct, bd, cfg.now()) if ready else ""
        if auto:
            line = f"{acct.name}, {day}: {ready} of {total} drafts ready, {auto}"
        from clipfactory import bench as BN      # the spares the 06:18 fill put in (state/<account>/bench)
        if BN.from_bench_count(bd):
            line += f" ({BN.from_bench_count(bd)} from the bench)"
        bad = [s for s in shown if s["status"] in (B.FAILED, B.REFUSED, B.OPEN, B.MAKING)
               and not (acct.fed_by_hand() and s["status"] == B.OPEN)]   # an open hand-fed slot waits for a drop
        if bad:
            line += f"; {len(bad)} not made (" + ", ".join(f"{s['slot']} {s['status']}" for s in sorted(bad, key=lambda s: s['slot'])[:5]) + ")"
        out.append(line + ".")
        if bd.get("report"):
            out.append(f"  the bot's note: {bd['report']}")
    try:                                 # cuts that no longer fit their band; clips marked stale (bandcheck, moments)
        from datetime import timedelta
        from clipfactory import bandcheck as BC
        from clipfactory import moments as MO
        shown = [cfg.account(a.account)] if a.account else [x for x in cfg.accounts.values() if AC.signs_here(cfg, x)]
        warn = BC.morning_lines(cfg, cfg.now(), shown,
                                days=sorted({day, cfg.now().date(), cfg.now().date() + timedelta(days=1)}))
        for acct in shown:
            bd = B.read(acct, day) or {}
            marked = sorted(k for k, s in (bd.get("slots") or {}).items() if MO.stale(s) and s.get("status") == B.READY)
            if marked:
                warn.append(f"{acct.name}: {', '.join(marked)} marked STALE by the night: do not sign "
                            f"{'it' if len(marked) == 1 else 'them'} (Approve all and the automatic sign-off skip "
                            f"{'it' if len(marked) == 1 else 'them'}; Decline).")
            for k, j, why in (MO.pairs_signed(cfg, acct, day, bd) if bd.get("slots") else []):
                warn.append(f"{acct.name}: {k} and {j} hold one clip ({why}), both signed: both go out unless you "
                            f"Pull one.")
        out = warn + out
    except Exception:  # noqa: BLE001  (the morning message never fails over a check)
        pass
    try:                                 # the cast queue (castqueue.py): people waiting for his check
        from clipfactory import castqueue as CQ
        casts = CQ.morning_line(cfg) if any(x.maker == "politics" for x in cfg.accounts.values()) else ""
    except Exception:  # noqa: BLE001  (the morning message never fails over the queue)
        casts = ""
    try:                                 # Lab (lab.py): takes waiting for his outcome, scripts not written
        from clipfactory import lab as L
        lab_line = L.morning_line(cfg)
    except Exception:  # noqa: BLE001  (the morning message never fails over the Lab)
        lab_line = ""
    if lab_line:
        out.insert(0, lab_line)
    if casts:                            # before the accounts' lines: the notice is cut at 220 characters
        out.insert(0, casts)
    from clipfactory import poster as P
    from clipfactory import outlets as YT
    for a in cfg.accounts.values():      # the YouTube cap's day-long pause (10-23 to 10-25 the notice never said it)
        if a.active and "youtube_shorts" in YT.auto_platforms(a):
            paused = YT.yt_paused(a)
            if paused:
                out.insert(0, paused)
    try:                                 # Claude's week (usage.py): the 10-25 run-out cost the whole 10-26 board
        from clipfactory import usage as U
        st = U.current()
        week = U.short(st) if st else ""
    except Exception:  # noqa: BLE001  (the morning message never fails over the meter)
        week = ""
    if week:
        out.insert(0, week)
    broke = P.health_words(cfg)          # posting broke, stalled or stopped (audit PP-15): the first line he reads
    if broke:
        out.insert(0, broke)
    if out:
        from clipfactory import bench as BN      # spare clips running low: one line, never in the notice itself
        out += BN.low_lines(cfg)
        try:                                 # the social layer: holds, then "Also: ..." (never the first line)
            from clipfactory import social as SOC
            out += SOC.morning_lines(cfg, cfg.now())
        except (Exception, SystemExit):  # noqa: BLE001  (the morning message never fails over the social layer)
            pass
        out.append(f"Review and sign: http://127.0.0.1:{cfg.review_port}/?date={day}  (or click Clip Review in the Dock)")
    show(out)
    if getattr(a, "notify", False) and out:
        # the Mac's own notification: the compact line (every account's count), then one line per account. Clip
        # Review.app posts it, so a click on it opens that app, and the app opens the review page; without the app, a
        # plain osascript notification (a click does nothing)
        body = notice_body(compact_line(counts), out)
        if CLIP_REVIEW_APP.exists():
            (C.ROOT / "state" / "notify-pending.txt").write_text(body)
            subprocess.run(["open", "-g", str(CLIP_REVIEW_APP)], capture_output=True, timeout=30)
        else:
            subprocess.run(["osascript", "-e", f"display notification {json.dumps(body)} with title "
                            f"{json.dumps('Clip drafts ready to sign')} sound name \"Glass\""],
                           capture_output=True, timeout=30)


def cmd_approve(cfg, a):
    from clipfactory import poster as P
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    if not a.all and not a.slots:
        raise SystemExit("say which: --all, or --slots 07:00,08:45")
    lines = S.approve(cfg, acct, day, None if a.all else slots_arg(a.slots), a.by)
    show(lines)
    if any("goes out" in ln for ln in lines):
        print(f"  {P.kick(cfg)} ({O.routes(acct)})")


def _bench_cut(cfg, acct, a):
    """igcut/ytcut --bench ID: (the item's folder, its job folder), or (None, None) for a delivered clip."""
    if not getattr(a, "bench", None):
        return None, None
    from clipfactory import bench as BN
    return BN.cut_target(cfg, acct, a.bench, a.slug)


def cmd_igcut(cfg, a):
    from clipfactory import igcut as IG
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    into, job_dir = _bench_cut(cfg, acct, a)
    show(IG.render(cfg, acct, day, a.slug, end=a.end, segments=a.segments, hook=a.hook,
                   hook2=False if a.no_hook2 else None, words=Path(os.path.expanduser(a.words)) if a.words else None,
                   python=a.python, sets=a.set, into=into, job_dir=job_dir, pick=a.pick))
    if into is not None:
        from clipfactory import bench as BN
        show(BN.refresh(cfg, acct, a.bench))


def cmd_ytcut(cfg, a):
    from clipfactory import igcut as IG
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    if a.missing:
        show(IG.missing(cfg, acct, day, IG.YOUTUBE))
        return
    if not a.slug:
        raise SystemExit("say which clip: --slug SLUG (or --missing: the day's clips that need a YouTube cut)")
    into, job_dir = _bench_cut(cfg, acct, a)
    show(IG.render(cfg, acct, day, a.slug, end=a.end, segments=a.segments, hook=a.hook,
                   hook2=False if a.no_hook2 else None, words=Path(os.path.expanduser(a.words)) if a.words else None,
                   python=a.python, sets=a.set, plat=IG.YOUTUBE, from_instagram=a.from_instagram, into=into,
                   job_dir=job_dir, pick=a.pick))
    if into is not None:
        from clipfactory import bench as BN
        show(BN.refresh(cfg, acct, a.bench))


def cmd_pull(cfg, a):
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    show(S.pull(cfg, acct, day, slots_arg(a.slots) or [], a.by, code=a.code, reason=a.reason or ""))


def cmd_retry(cfg, a):
    from clipfactory import poster as P
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    lines = S.retry(cfg, acct, day, slots_arg(a.slots) or [], a.by)
    show(lines)
    if any("tried again" in ln and "NOT" not in ln for ln in lines):
        print(f"  {P.kick(cfg)}")


def cmd_stale(cfg, a):
    """The night's "stale, do not sign" mark (clipfactory/moments.py): Approve all and the automatic sign-off skip a
    clip marked stale, and its card says so big and red; only a one-by-one Approve signs it. --clear takes it off."""
    from clipfactory import moments as MO
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    show(MO.mark(cfg, acct, day, slots_arg(a.slots) or [], a.why or "", by=a.by or "the night", clear=a.clear))


def cmd_bandcheck(cfg, a):
    """Cuts on a slot still to post that no longer fit their outlet's band (clipfactory/bandcheck.py), with the
    igcut --pick that makes each one again. Exit 1 when there is any (a prompt or a script can test it)."""
    from clipfactory import bandcheck as BC
    acct = cfg.account(a.account) if a.account else None
    days = [C.parse_day(a.date, cfg)] if a.date else None
    out = BC.lines(cfg, acct, days)
    show(out)
    if any(not ln.endswith("as it is now") and not ln.startswith("      ") for ln in out):
        raise SystemExit(1)


def cmd_decline(cfg, a):
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    reasons = []
    if getattr(a, "reasons", None):            # the review page's chips by their codes (verdicts.REASONS)
        from clipfactory import verdicts as VD
        reasons = VD.check(a.reasons)
    one = {"code": a.code} if getattr(a, "code", None) else {}     # one code (reasons.py), the review page's Decline
    show(S.decline(cfg, acct, day, slots_arg(a.slots) or [], a.by, a.reason or "", remake=a.remake, reasons=reasons,
                   **one))


def cmd_killlist(cfg, a):
    """The account's kill list (clipfactory/reasons.py): what a Pull or Decline said the night never cuts again."""
    from clipfactory import reasons as R
    acct = cfg.account(a.account)
    if a.lift:
        show(R.lift(cfg, acct, a.lift, a.why or "", who=a.by or ""))
    elif a.check:
        show(R.check(cfg, acct, a.check, a.span or "", day=C.parse_day(a.date, cfg) if a.date else None))
    else:
        show(R.show(cfg, acct, all_=a.all))


def cmd_place(cfg, a):
    """One finished clip into one slot, as a draft for Approve (clipfactory/place.py). Never signs or schedules."""
    from clipfactory import place as PL
    if not a.date:
        raise SystemExit("place needs --date (the posting day, YYYY-MM-DD)")
    acct, day, slot = cfg.account(a.account), C.parse_day(a.date, cfg), C.normal_slot(a.slot)
    if off_here(cfg, acct, "place"):
        return

    def path(p):
        return Path(os.path.expanduser(p)) if p else None
    if a.release:
        ok, lines = PL.release(cfg, acct, day, slot, why=a.why or "", check=a.check)
    else:
        ok, lines = PL.place(cfg, acct, day, slot, clip=path(a.clip), words=path(a.words), cover=path(a.cover),
                             sources=a.source_url or [], why=a.why or "", fmt=a.format, host=a.host, job=a.job,
                             slug=a.slug, check=a.check, replace=a.replace)
    show(lines)
    if ok and not a.check:
        print(status_line(acct, day))
    if not ok:
        raise SystemExit(1)


def cmd_schedule(cfg, a):
    from clipfactory import poster as P
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    if off_here(cfg, acct, "schedule"):
        return
    show(O.schedule(cfg, acct, day, dry_run=a.dry_run))
    if not a.dry_run:
        print(f"  {P.kick(cfg)}")


def cmd_stats(cfg, a):
    show(ST.update(cfg, days=a.days, fresh=not a.no_refresh))


def cmd_inbox(cfg, a):
    from clipfactory import inbox as I
    from clipfactory import lab as L
    lines = I.take(cfg)
    for acct in L.lab_accounts(cfg):     # Lab's console recordings (Capture/inbox), switched on or not
        lines += L.watchdog(cfg, acct) + L.inbox_scan(cfg, acct)
    if lines or not a.quiet:
        show(lines or [f"  nothing waiting in {I.root(cfg)}"])


def cmd_post_due(cfg, a):
    from clipfactory import poster as P
    show(P.post_due(cfg, dry_run=a.dry_run) or ["  nothing due"])


def cmd_review(cfg, a):
    from clipfactory import review
    review.serve(cfg)


WITH = ("comments", "analytics")       # connect youtube --with: what the social layer may also read and write


def with_arg(s: Optional[str]) -> List[str]:
    """--with comments,analytics: the extra YouTube permissions asked for, checked."""
    out = [x.strip() for x in (s or "").split(",") if x.strip()]
    bad = [x for x in out if x not in WITH]
    if bad:
        raise SystemExit(f"--with: {', '.join(bad)} is not one of {', '.join(WITH)}")
    return list(dict.fromkeys(out))


def cmd_connect(cfg, a):
    acct = cfg.account(a.account)
    extra = with_arg(getattr(a, "with_", None))
    if extra and a.platform != "youtube":
        raise SystemExit("--with is for connect youtube (Meta's permissions are ticked on its own screen)")
    if a.platform == "youtube":
        if not a.client:
            raise SystemExit("youtube needs --client <the OAuth client JSON from Google Cloud>")
        import inspect
        if extra and "extra" not in inspect.signature(O.connect_youtube).parameters:
            raise SystemExit("--with: asking YouTube for comments and analytics is not built yet")
        kw = {"extra": tuple(extra)} if extra else {}
        print(O.connect_youtube(acct, Path(os.path.expanduser(a.client)), cfg, **kw))
    elif a.platform == "meta":
        from clipfactory import direct as D
        print(D.connect_meta(acct, cfg))
    elif a.platform == "instagram":     # re-find the connected Page's Instagram, no new sign-in
        from clipfactory import direct as D
        print(D.link_instagram(acct, cfg))
    elif a.platform == "upload-post":   # TikTok through Upload-Post: the key once (hidden), then each account's profile
        from clipfactory import direct as D
        print(D.connect_upload_post(acct, a.profile, a.new_key))
    else:
        from clipfactory import direct as D
        if not a.client_key:
            raise SystemExit("tiktok needs --client-key <the app's client key>; the secret is asked for, hidden")
        print(D.connect_tiktok(acct, a.client_key))


# ------------------------------------------------------------------------------------------------------ doctor
LEGACY = {  # the skills still name these Desktop folders in code; the accounts now live in Andrew Johnson/
    "Gaming": "gaming",
    "Clipping": "politics",
}


def cmd_tiktok_now(cfg, a):
    from datetime import date as _date
    from clipfactory import poster as P
    print(P.tiktok_now(cfg, cfg.account(a.account), _date.fromisoformat(a.date), C.normal_slot(a.slot)))


def cmd_daily8(cfg, a):
    """THE DAILY 8 BOARD (clipfactory/daily8.py, 2026-10-28): the scout's board as tomorrow's eight lanes, each with a
    pick and a backup, the news level, the Part 2 hits and what each prepped job's transcript holds. Read only."""
    from datetime import date as _date
    from clipfactory import daily8 as D8
    b = D8.build(cfg, a.account, a.date or _date.today().isoformat())
    print(D8.write(b, Path(a.out) if a.out else cfg.drafts_root / "Scout"))


def cmd_scout(cfg, a):
    """The news scout (clipfactory/scout.py): read the feeds and write the board, or record a pick or a kill."""
    from clipfactory import scout as SC
    from clipfactory import scouthealth as SH
    if a.account != "all" and off_here(cfg, cfg.account(a.account), "scout"):
        return
    # all: the accounts with a scout of their own (a block in config/scout.json; a client has none), switched on here
    slugs = [x.slug for x in SH.scout_accounts(cfg)] if a.account == "all" else [cfg.account(a.account).slug]
    if a.pick or a.kill:
        if len(slugs) != 1:
            raise SystemExit("--pick and --kill take one account")
        acct = cfg.account(slugs[0])
        show(SC.decide(cfg, acct, a.pick or a.kill, "picked" if a.pick else "killed", a.why or "", a.by))
        return
    for slug in slugs:
        lines = SC.run(cfg, cfg.account(slug), hours=a.hours, feed=a.feed)
        show(lines[:1] + lines[-1:] if a.quiet else lines)


def cmd_prep(cfg, a):
    """The afternoon prep (makers.prep_gta, Best of GTA 2026-10-29): the intakes the night would run for the posting
    day, run now, so the night only picks and renders. A GTA account only; the timer runs it while gta_maker
    prep.on is true (timer.prep_agents)."""
    from clipfactory import makers as M
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    if off_here(cfg, acct, "prep"):
        return
    if acct.maker != "gta" or acct.fed_by_hand():
        raise SystemExit(f"prep: {acct.name} is not cut by the GTA skill at night; nothing to prep")
    try:
        lock = M.hold_worker_lock(acct)          # never beside the account's own worker (a night or a day shift)
    except BlockingIOError:
        show([f"prep {acct.name}: its worker is running; nothing prepped"])
        return
    with lock:
        lines = M.prep_gta(cfg, acct, day, dry_run=a.dry_run)
    show(lines[:1] + lines[-1:] if a.quiet else lines)


def cmd_drop(cfg, a):
    """Drop mode (clipfactory/drop.py): watch Rockstar's channel; while a drop is open, rank the roster's Twitch and
    Kick clips of it and keep the REACTIONS candidate in the inbox. --backtest replays a saved drop offline."""
    from clipfactory import drop as DR
    acct = cfg.account(a.account)
    if a.backtest:
        fx = json.loads(Path(a.backtest).expanduser().read_text())
        res = DR.backtest(fx, dict(DR.settings(acct.slug) or {}, **(fx.get("settings") or {})),
                          until_h=a.until_h)
        text = "\n".join(DR.backtest_report(res))
        if a.out:
            Path(a.out).expanduser().write_text(text + "\n")
            Path(a.out).expanduser().with_suffix(".json").write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
        print(text if not a.out else f"backtest: ready {res['ready_at'] or 'never'}; {len(res['pulled'])} clip(s) "
              f"pulled; report {a.out}")
        return
    if off_here(cfg, acct, "drop"):
        return
    if acct.maker != "gta":
        raise SystemExit(f"drop mode is for the GTA accounts; {acct.name} is not one")
    lines = DR.run(cfg, acct, quiet=a.quiet)
    show(lines if not a.quiet else [x for x in lines if not x.startswith("feed:")])


TOOL_PATHS = ("scripts/", "references/", "assets/", "SKILL.md")   # the gaming skill's own (qm_config.TOOL_PATHS)


def cmd_casts(cfg, a):
    """The cast queue (castqueue.py). Read only, except --stills (small frames into a job's castcheck folder) and
    --notify (a Mac notice). There is no confirm here: only Andrew, on the review page."""
    from clipfactory import castqueue as CQ
    if a.confirmed:
        show(CQ.confirmed_lines(cfg, a.since, as_json=a.json))
    elif a.stills:
        show(CQ.pull_all(cfg))
    elif a.notify:
        print(CQ.notify(cfg) or "nothing waits for a cast check: no notice")
    else:
        show(CQ.lines(cfg, as_json=a.json))


def skill_tree(acct) -> tuple:
    """(clean, words) for a GTA skill's git tree, as its renders judge it (qm_config.tool_state): an uncommitted file
    under the tool's paths refuses every render (audit GTA-6; 10-16's first night refused all ten slots so). The
    archive a delivery writes (references/jobs/, references/proofs/) does not count. GIT_OPTIONAL_LOCKS=0: a plain
    `git status` may refresh the index under .git/index.lock, which would make a skill commit or run.py's own
    tool-state check fail while doctor runs."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")   # read only: never take .git/index.lock beside a commit or run.py

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(acct.skill_dir), *args], capture_output=True, text=True, timeout=60,
                              env=env, stdin=subprocess.DEVNULL)
    try:
        st = git("status", "--porcelain", "--", *TOOL_PATHS)
        head = git("log", "-1", "--format=%h %cd", "--date=format:%m-%d %H:%M").stdout.strip()
        branch = git("branch", "--show-current").stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{acct.name}: skill tree unreadable ({e})"
    if st.returncode != 0:
        return False, f"{acct.name}: skill tree unreadable ({(st.stderr or '').strip()[:120]})"
    files = [ln[3:].strip().strip('"') for ln in st.stdout.splitlines() if ln.strip()]
    files = [f for f in files if not f.startswith(("references/jobs/", "references/proofs/"))]
    where = f"{branch or 'detached'} {head}".strip()
    if files:
        return False, (f"{acct.name}: skill tree has {len(files)} uncommitted file(s) at {where}, so every render "
                       f"refuses: {', '.join(files[:4])}" + (" ..." if len(files) > 4 else ""))
    return True, f"{acct.name}: skill tree clean at {where}"


TOKEN_FILES = {"youtube_shorts": "youtube_token.json", "tiktok": "tiktok.json", "instagram_reels": "meta.json",
               "facebook_reels": "meta.json"}


def connect_words(acct, plat: str) -> str:
    """The one command that signs this account in on this platform (each writes its own file in the tokens folder)."""
    f = "/usr/bin/python3 ~/clip-factory/factory.py connect"
    return {"youtube_shorts": f"{f} youtube {acct.slug} --client <the OAuth client JSON>",
            "facebook_reels": f"{f} meta {acct.slug}", "instagram_reels": f"{f} meta {acct.slug} (then connect "
            f"instagram {acct.slug})", "tiktok": f"{f} tiktok {acct.slug} --client-key <the app's client key>"
            }.get(plat, f"{f} ... {acct.slug}")


def _roster(acct) -> Optional[list]:
    """The channels of the account's Publish/watchlist.json; None when it is missing or unreadable."""
    try:
        data = json.loads((acct.account_dir / "Publish" / "watchlist.json").read_text())
    except (OSError, ValueError):
        return None
    chans = data.get("channels") if isinstance(data, dict) else data
    return [c for c in chans if isinstance(c, dict)] if isinstance(chans, list) else None


def _channel_keys(c: dict) -> set:
    """A roster channel by its address (no scheme, www or trailing slash, lowercase) and by its slug."""
    url = str(c.get("url") or "").strip().lower().split("://", 1)[-1]
    url = url[4:] if url.startswith("www.") else url
    keys = {("url", url.rstrip("/"))} if url else set()
    return keys | ({("slug", str(c["slug"]).strip().lower())} if str(c.get("slug") or "").strip() else set())


def gta_ready(cfg, acct, against: list) -> List[tuple]:
    """(done, text, fix, must) for a GTA account's own pieces: its roster, its upload listings, its brand kit, its
    Library. must False: said, never a failure. `against`: the other GTA accounts it must stay apart from (doctor's
    checklist: every other GTA account in the file; a switched-on account: the others that work here). Read only.
    The review of 2026-10-19 found each of these ticked by a file merely existing: setup_account.py installs GTA
    Gaming's roster and links. A fix carries no words when the line is only said (shared_library)."""
    out: List[tuple] = []
    kinds = ", ".join(sorted(M.playing_kinds(M.gta_settings(acct))))
    chans = _roster(acct)
    if not chans:
        out.append((False, "Publish/watchlist.json (its roster): " + ("no channels" if chans == [] else "missing"),
                    f"write its roster: channels of the kinds its slots cut ({kinds}), none of another GTA "
                    f"account's (NEW-ACCOUNTS.md)", True))
    else:
        wrong = sorted({str(c.get("kind") or "") for c in chans if not M.takes_kind(acct, str(c.get("kind") or ""))})
        mine = set().union(*(_channel_keys(c) for c in chans))
        shared = []
        for other in against:
            for c in _roster(other) or []:
                if _channel_keys(c) & mine:
                    shared.append(f"{c.get('name') or c.get('slug') or c.get('url')} is on "
                                  f"{C.possessive(other.name)} too")
        said = [f"kind {', '.join(k or '(none)' for k in wrong)}, which no slot of it cuts"] if wrong else []
        said += shared[:2] + ([f"+{len(shared) - 2} more shared"] if len(shared) > 2 else [])
        out.append((not said, f"Publish/watchlist.json (its roster, {len(chans)} channels)"
                    + (": " + "; ".join(said) if said else ""),
                    f"setup_account.py installs Gaming's roster: keep only channels of the kinds its slots cut "
                    f"({kinds}), none on another GTA account's roster", True))
        wake = launch_wake(acct)
        if wake:
            out.append(wake)
    try:
        listing = json.loads((acct.account_dir / "Publish" / "accounts.json").read_text())
    except (OSError, ValueError):
        listing = None
    if isinstance(listing, dict):                # posted.py's upload listings (missing: nothing to read, no harm)
        theirs = {h: o.name for o in against for h in o.handles()}     # a Facebook Page by its id or name too
        clash = sorted(h for h in C.handles_in(listing.values()) if h in theirs and h not in acct.handles())
        named = (f"the Facebook Page {clash[0]}" if clash[0].isdigit() else f"@{clash[0]}") if clash else ""
        out.append((not clash, "Publish/accounts.json (its own upload listings)"
                    + (f": names {named}, {C.possessive(theirs[clash[0]])}" if clash else ""),
                    "setup_account.py installs Gaming's links: put the account's own profile addresses there, "
                    "or null", True))
    kit = acct.kit or "gta"
    need = [acct.skill_dir / "assets" / "brand" / kit / "kit.json", acct.skill_dir / "presets" / f"{kit}.json"]
    gone = [str(x.relative_to(acct.skill_dir)) for x in need if not x.is_file()]
    # Gaming's kit is what the skill uses when told nothing, so only another kit's absence is a failure: the
    # account would render in Gaming's look, with @gaming burned into the picture
    out.append((not gone, f'brand kit "{kit}" in {acct.skill_dir.name}' + (
        f": {', '.join(gone)} missing" if gone else f" (assets/brand/{kit}/kit.json, presets/{kit}.json)"),
        "merge the gaming skill's branch that adds the kit (assets/brand/<kit>, presets/<kit>.json) before it is "
        "switched on", kit != "gta"))
    kit_day = kit_switch(acct)
    if kit_day:
        out.append(kit_day)
    lib = acct.account_dir / "Library"
    off = lib / "official"
    try:
        has = off.is_dir() and any(off.iterdir())
    except OSError:
        has = False
    out.append((has, f"Library/official (Rockstar's trailers, for the band) in {acct.account_dir.name}",
                "link it to Gaming's, never a copy (in the account's folder: ln -s \"$HOME/Desktop/Andrew Johnson/"
                "Gaming/Library/official\" Library/official)", False))
    shared = shared_library(cfg, acct)
    if shared:
        out.append(shared)
    unlinked = unlinked_library(cfg, acct)
    if unlinked:
        out.append(unlinked)
    out.append(_work_apart(cfg, acct))
    return out


def switch_date(acct) -> Optional[str]:
    """The day the account's game switch turns (gta_maker skip_off_topic, Stunts): the first dated phase of its
    config/scout.json block that changes its damp, where off_topic lives. None for an account with no switch."""
    if not M.gta_settings(acct).get("skip_off_topic"):
        return None
    try:
        block = json.loads((C.ROOT / "config" / "scout.json").read_text()).get(acct.slug) or {}
    except (OSError, ValueError):
        return None
    days = sorted(str(p["from"]) for p in block.get("phases") or [] if isinstance(p, dict) and p.get("from")
                  and ("damp" in (p.get("set") or {}) or "damp" in (p.get("add") or {})))
    return days[0] if days else None


def launch_wake(acct) -> Optional[tuple]:
    """The roster's launch-only channels wake on its prelaunch_until (scout.roster and the skill's watch.py both read
    it), which must be the game switch's own date: Stunts' said 2026-11-12, a week before its 2026-11-19 switch
    (review 10-19). Said, never a failure; None for an account with no switch or no launch-only channel."""
    turn = switch_date(acct)
    try:
        data = json.loads((acct.account_dir / "Publish" / "watchlist.json").read_text())
    except (OSError, ValueError):
        return None
    chans = data.get("channels") if isinstance(data, dict) else None
    late = [c for c in chans or [] if isinstance(c, dict) and c.get("phase") == "launch"]
    if not turn or not late:
        return None
    until = str(data.get("prelaunch_until") or "2026-11-12")      # watch.py's own default
    rel = "on" if until == turn else ("before" if until < turn else "after")
    return (until == turn, f"Publish/watchlist.json prelaunch_until {until}: its {len(late)} launch-only channels "
            f"wake {rel} the game switch ({turn}, config/scout.json)",
            f"set \"prelaunch_until\": \"{turn}\" in its Publish/watchlist.json: one date for the switch", False)


def kit_switch(acct) -> Optional[tuple]:
    """The skill's kit labels a clip's game by date too (posting.game_by_date.from in assets/brand/<kit>/kit.json, the
    stunts kit): it must be the game switch's own date, or for days a clip would be labelled by one date and cut
    by another. Said, never a failure; None for an account with no switch, or a kit with no such date."""
    turn = switch_date(acct)
    if not turn:
        return None
    kit = acct.kit or "gta"
    try:
        posting = json.loads((acct.skill_dir / "assets" / "brand" / kit / "kit.json").read_text()).get("posting") or {}
    except (OSError, ValueError, AttributeError):
        return None
    gbd = posting.get("game_by_date") if isinstance(posting, dict) else None
    day = str(gbd.get("from") or "") if isinstance(gbd, dict) else ""
    if not day:
        return None
    return (day == turn, f'brand kit "{kit}" labels by game from {day}' + (
        ", the game switch's date" if day == turn else f", not the game switch's {turn} (config/scout.json)"),
        "one date: set the kit's posting.game_by_date.from (the skill's) or the phase's from to the same day", False)


def library_home(cfg, acct):
    """The GTA account whose Library the others read (Gaming: the skill's default kit, "gta"). None when acct is
    that account itself, or the file has no such account."""
    if acct.maker != "gta" or (acct.kit or "gta") == "gta":
        return None
    return next((o for o in (cfg.all_accounts or cfg.accounts).values()
                 if o.maker == "gta" and (o.kit or "gta") == "gta" and o.slug != acct.slug), None)


def unlinked_library(cfg, acct) -> Optional[tuple]:
    """A GTA account other than Gaming whose cams.json, slop or compare (or official, when it is there) is
    missing or is its own file or folder, not a link into Gaming's Library (review 10-19): the live skill's
    setup_account.py makes real, empty slop and compare folders and no cams.json (until the skill's rails/brand-kits,
    89d13fb, which links all four), so a new account would start its own facecam registry and lose every measured
    cam. Said, never a failure; None when all four are links (Gaming: nothing said). A missing official has its own
    line above."""
    home = library_home(cfg, acct)
    if home is None:
        return None
    lib, theirs = acct.account_dir / "Library", home.account_dir / "Library"
    real_theirs = _real(theirs)
    missing, own = [], []
    for name in SHARED_LIBRARY:
        e = lib / name
        if not e.exists():
            if name != "official":
                missing.append(name)
            continue
        r = _real(e)
        if not (e.is_symlink() and r is not None and real_theirs is not None
                and (r == real_theirs or real_theirs in r.parents)):
            own.append(name)
    if not missing and not own:
        return None
    said = ([f"{', '.join(missing)} missing"] if missing else []) + \
        ([f"{', '.join(own)} its own, not a link into {C.possessive(home.name)} Library"] if own else [])
    return (False, f"Library: {'; '.join(said)}",
            f"in {acct.account_dir}, take out each empty one and link it: ln -s \"{theirs}/<name>\" Library/<name> "
            f"for {', '.join(missing + own)} (never a copy, NEW-ACCOUNTS.md)", False)


# The Library entries every GTA account reads from Gaming's Library, linked on purpose (2026-10-19): Rockstar's
# trailers (official, 17G), the slop reels (slop, 322G), the comparison reels (compare) and the creators' measured
# facecams (cams.json: they belong to the creators, not to an account). The Desktop syncs to iCloud
# (FXICloudDriveDesktop=1), so a copy per account would send 339G up again. The factory writes nothing in a Library;
# the skill does. analyze.py adds each job's measured cam to cams.json (cams.register: load, add, save the whole
# file). The gameplay lane takes no slop reel (run.py: their own game is the picture), so for Stunts and Roleplay
# cams.json is the one shared write. The skill branch rails/brand-kits (0505e2e, 2026-10-19) makes it safe: cams.save
# and sloplib._save write a temp file beside the RESOLVED file and rename it (atomic through the link, and the link
# stays a link), and any account but Gaming keeps its slop `used` marks in its own Publish/slop-used.json, never in
# slop/index.json. That holds once rails/brand-kits is merged into ~/.claude/skills/gaming. Until then cams.save
# truncates and rewrites in place, and cams.load reads a half-written file as {}: a register in that window saves a
# registry holding one entry, so the whole registry can be lost, not one entry. The render queue runs those steps one
# at a time across every account, except when a step holds it past its turn (60 minutes).
SHARED_LIBRARY = ("cams.json", "compare", "official", "slop")


def _real(p: Path) -> Optional[Path]:
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        return None


def _others_homes(cfg, acct) -> List[tuple]:
    """(real folder, name) of every other account in the file, switched on or not."""
    return [(_real(o.account_dir), o.name) for o in (cfg.all_accounts or cfg.accounts).values()
            if o.slug != acct.slug and o.account_dir != acct.account_dir]


def _whose(cfg, acct, p: Path) -> Optional[tuple]:
    """(the other account's name, its real folder) when p resolves into another account's folder; None when it is
    the account's own (or nobody's)."""
    own, r = _real(acct.account_dir), _real(p)
    if r is None or own is None or r == own or own in r.parents:
        return None
    return next(((name, h) for h, name in _others_homes(cfg, acct) if h is not None and (r == h or h in r.parents)),
                None)


def shared_library(cfg, acct) -> Optional[tuple]:
    """The account's Library entries that are links into another account's Library (SHARED_LIBRARY: Gaming's,
    shared on purpose), said as one line and never a failure, before or after switch-on. None when nothing is
    shared (Gaming's own Library: nothing said, as before)."""
    try:
        entries = sorted((acct.account_dir / "Library").iterdir())
    except OSError:
        return None
    shared: Dict[str, List[str]] = {}
    for e in entries:
        got = _whose(cfg, acct, e)
        if got:
            name, lib, r = got[0], got[1] / "Library", _real(e)
            known = e.name in SHARED_LIBRARY and r is not None and (r == lib or lib in r.parents)
            shared.setdefault(name, []).append(e.name if known else f"{e.name} (not one of the shared libraries)")
    if not shared:
        return None
    said = "; ".join(f"{', '.join(names)} shared with {who}" for who, names in shared.items())
    return (True, f"Library: {said} (links into its Library, on purpose: read by every GTA account, never copied)",
            "", False)


def _work_apart(cfg, acct) -> tuple:
    """Jobs/ and Publish/ are the account's own: a NO when either resolves into another account's folder. Publish
    holds its roster, inbox, schedule and upload listings, Jobs its skill jobs; linked, its nights would deal from and
    write into another account's (the shared Library is a different matter: shared_library)."""
    linked = []
    for name in ("Jobs", "Publish"):
        got = _whose(cfg, acct, acct.account_dir / name)
        if got:
            linked.append(f"{name} is a link into {C.possessive(got[0])} folder")
    return (not linked, "Jobs and Publish its own" + (": " + "; ".join(linked) if linked else ""),
            "make each one a real folder of its own (its roster, inbox and upload listings in Publish; Jobs empty)",
            True)


def setup_lines(cfg, acct) -> List[str]:
    """Doctor's checklist for an account that is not working here yet (switched off, or another machine's): what it
    needs before it is switched on, each "[x]" done or "[ ]" to do. Read only; no line is a failure."""
    from clipfactory import accounts as AC
    from clipfactory import outlets as O

    def box(done: bool, text: str) -> str:
        return f"  [{'x' if done else ' '}] {text}"
    where = ("switched off" if not acct.active else
             f'on the machine "{acct.machine}" (this one is "{cfg.machine}"): its own doctor there checks it')
    out = [f"{acct.name} ({acct.slug}), {where}"]
    out.append(box(acct.account_dir.is_dir(), f"account folder {acct.account_dir}"))
    if acct.maker == "gta":
        # Publish/inbox.json is not on the list: the scout's first feed and the night's scan make it (review 10-19)
        others = [x for x in cfg.all_accounts.values() if x.maker == "gta" and x.slug != acct.slug]
        for done, text, _fix, _must in gta_ready(cfg, acct, others):
            out.append(box(done, text))
    if acct.maker == "lab":                  # Lab (lab.py): its kit, voice, OBS, capture root, bank, dates
        from clipfactory import lab as L
        for done, text, _fix, _must in L.doctor_rows(cfg, acct):
            out.append(box(done, text))
    else:                                    # a Lab account has no scout: its stock is Andrew's own takes
        try:
            blocks = json.loads((C.ROOT / "config" / "scout.json").read_text())
        except (OSError, ValueError):
            blocks = {}
        out.append(box(isinstance(blocks.get(acct.slug), dict), f'config/scout.json block "{acct.slug}"'))
    soul = C.ROOT / "hermes" / acct.slug / "SOUL.md"
    out.append(box(soul.is_file(), f"hermes/{acct.slug}/SOUL.md (states its {acct.min_seconds:.0f} to "
                                   f"{acct.max_seconds:.0f} seconds)"))
    links = acct.raw.get("links") or {}
    empty = [p for p in acct.platforms if not str(links.get(p, "")).startswith("https://")]
    out.append(box(not empty, 'links (accounts.json "links", each profile\'s https address)'
                   + (f": {', '.join(empty)} empty" if empty else "")))
    for plat in acct.platforms:
        tok = O.TOKENS / acct.slug / TOKEN_FILES.get(plat, "-")
        out.append(box(tok.exists(), f"{plat} signed in" + ("" if tok.exists() else f": {connect_words(acct, plat)}")))
    probs = AC.root_problems(dataclasses.replace(cfg, accounts=cfg.all_accounts))
    mine = [x for x in probs if acct.name in x]
    out.append(box(not mine, "its folders apart from every other account's" + (f": {mine[0]}" if mine else "")))
    out.append(f'  then switch it on: set "active": true for {acct.slug} in config/accounts.json (and "machine" if '
               f'another computer runs it), then run factory.py timer install on that machine')
    return out


def board_paths(cfg, days: int = 2) -> tuple:
    """([missing file], [another 'Clip Drafts' folder]) for today's and tomorrow's boards (audit FOLD-2): a Finder drag
    moves a live folder and the factory makes a new empty one, so the boards point at nothing while doctor said "all
    good". Only clips waiting or going out are checked (ready, approved, scheduled)."""
    from datetime import timedelta
    missing = []
    start = cfg.now().date()
    for acct in cfg.accounts.values():
        for n in range(days):
            bd = B.read(acct, start + timedelta(days=n)) or {}
            for s in (bd.get("slots") or {}).values():
                if s.get("status") not in (B.READY, B.APPROVED, B.SCHEDULED):
                    continue
                paths = [s.get("folder"), (s.get("clip") or {}).get("path"), s.get("cover")]
                paths += [(c or {}).get("path") for c in (s.get("copy") or {}).values()]
                paths += [(c or {}).get("path") for c in (s.get("cuts") or {}).values()]
                missing += [p for p in paths if p and not Path(p).exists()]
    desk = Path.home() / "Desktop"
    want = cfg.drafts_root.resolve() if cfg.drafts_root.exists() else cfg.drafts_root
    extra = []
    for pat in ("*/Clip Drafts", "*/*/Clip Drafts"):
        for p in desk.glob(pat):
            try:
                if p.is_dir() and p.resolve() != want:
                    extra.append(p)
            except OSError:
                continue
    return missing, sorted(set(extra))


def cmd_doctor(cfg, a):
    ok = True

    def say(good: bool, text: str, fix: str = ""):
        nonlocal ok
        ok &= good
        print(f"  {'ok ' if good else 'NO '} {text}" + (f"\n        fix: {fix}" if fix and not good else ""))

    print("tools")
    for t in ("ffmpeg", "ffprobe", "yt-dlp"):
        say(bool(shutil.which(t)), t, f"brew install {t if t != 'ffprobe' else 'ffmpeg'}")
    say(Path(M.PY_SKILLS).exists(), f"skills' Python {M.PY_SKILLS}")
    say(bool(shutil.which("hermes")), "hermes on PATH", "install Hermes Agent")
    print("accounts")
    desk = Path.home() / "Desktop"
    if not cfg.accounts:                     # a stray CLIP_FACTORY_MACHINE, or every account off: a night that
        named = [x.name for x in cfg.all_accounts.values() if x.machine == cfg.machine]   # makes nothing is a NO
        here = (f"{C.MACHINE_ENV}={cfg.machine}" if cfg.machine != C.MAIN else f'"{C.MAIN}"')
        if named:
            say(False, f"every account of this machine ({here}) is switched off ({', '.join(named)}): nothing is "
                f"made, scheduled or posted here", 'switch one on ("active": true), then factory.py timer install')
        else:
            say(False, f"no account in config/accounts.json runs on this machine ({here}): every night, scout and "
                f"post here does nothing", f"on the main Mac: unset {C.MACHINE_ENV} (take it out of ~/.zshrc, open a "
                f"new Terminal), then factory.py timer install; on another computer: set \"machine\": "
                f"\"{cfg.machine}\" on the accounts it runs")
    for acct in cfg.accounts.values():
        say(acct.skill_dir.is_dir(), f"{acct.name}: skill {acct.skill_dir}")
        say(acct.account_dir.is_dir(), f"{acct.name}: account folder {acct.account_dir}")
        if (acct.raw or {}).get("volume"):     # a dated volume rule (Lab): the slots of the next posting day
            day = cfg.posting_day()
            say(bool(acct.slots_for(day)), f"{acct.name}: {volume_words(acct, day)}")
        else:
            say(len(acct.active_slots()) == acct.daily_target, f"{acct.name}: {acct.daily_target} slots a day "
                f"({', '.join(acct.active_slots())})")
        if acct.fed_by_hand():               # 2026-10-29: on Clip Review, nothing cuts for it by itself
            why = "it has no maker yet" if acct.maker == C.HAND else '"night": false, no night or scout agent'
            print(f"  --  {acct.name}: nothing cuts for it by itself ({why}); a clip dropped in "
                  f"{cfg.inbox_root / acct.name} takes its next open slot")
        if acct.day_shift():                 # 2026-10-29: it cuts in the day, the night is full
            at, until = acct.day_shift()
            print(f"  --  {acct.name}: cuts in the day, {at} to {until} (day_shift): the next day's board, its worker "
                  f"stopped by {until}; no night or retry agent")
        if C.day_shift_problem(acct):
            say(False, f"{acct.name}: its day_shift is not used: {C.day_shift_problem(acct)}",
                'fix the "day_shift" block in config/accounts.json, then factory.py timer install')
        if acct.maker == "gta":              # switched on: its own pieces stay checked (a NO, not a checklist)
            others = [x for x in cfg.accounts.values() if x.maker == "gta" and x.slug != acct.slug]
            for done, text, fix, must in gta_ready(cfg, acct, others):
                if must:
                    say(done, f"{acct.name}: {text}", fix)
                elif not done:
                    print(f"  --  {acct.name}: {text} (fix: {fix})")
                elif not fix:                # said as it is (the Library shared with Gaming)
                    print(f"  --  {acct.name}: {text}")
        if acct.maker == "lab":              # switched on: Lab's pieces are NO lines, not a checklist
            from clipfactory import lab as L
            for done, text, fix, must in L.doctor_rows(cfg, acct):
                if must:
                    say(done, f"{acct.name}: {text}", fix)
                elif not done:
                    print(f"  --  {acct.name}: {text} (fix: {fix})")
    waiting = [x for s, x in cfg.all_accounts.items() if s not in cfg.accounts]
    if waiting:
        print("accounts being set up (switched off, or run by another machine: a checklist, never a failure)")
        for acct in waiting:
            for line in setup_lines(cfg, acct):
                print(f"  --  {line}")
    print("the GTA skill's tree (a night's renders refuse a dirty tool)")
    trees: set = set()                       # one line per skill: the GTA accounts share the gaming skill
    for acct in cfg.accounts.values():
        if acct.maker in ("gta", "lab") and (acct.skill_dir / ".git").exists() and acct.skill_dir not in trees:
            trees.add(acct.skill_dir)
            good, words = skill_tree(acct)
            days = [f"{x.name} {x.day_shift()[0]} to {x.day_shift()[1]}" for x in cfg.accounts.values()
                    if x.skill_dir == acct.skill_dir and x.day_shift()]     # a day shift cuts with it too (10-29)
            say(good, words, "commit or stash those files before 21:00, and never commit to the skill while the "
                             "night shift is cutting (21:00 to 06:15)"
                             + (f" or a day shift is ({', '.join(days)})" if days else "")
                             + ": a job's later picks refuse a moved tool")
    print("the drafts on today's and tomorrow's boards")
    gone, extra = board_paths(cfg)
    say(not gone, "every clip on the boards is where its board says" if not gone else
        f"{len(gone)} file(s) on the boards are gone, first: {gone[0]}",
        "a folder was dragged away: find it (mdfind -name '<its name>'), move it back where the board says, then "
        "Try again on its card")
    say(not extra, f"one Clip Drafts folder ({cfg.drafts_root})" if not extra else
        f"another Clip Drafts folder: {extra[0]}" + (f" (+{len(extra) - 1})" if len(extra) > 1 else ""),
        f"if it holds drafts, move them back into {cfg.drafts_root}; then park the copy")
    print("the cuts on today's and tomorrow's boards (each inside its outlet's band as it is now)")
    from clipfactory import bandcheck as BC
    for good, text, fix in BC.doctor_rows(cfg):
        say(good, text, fix)
    print("one clip, one slot a day (today's and tomorrow's boards)")
    from datetime import timedelta as _td
    from clipfactory import moments as MO
    for good, text, fix in MO.doctor_rows(cfg, [cfg.now().date(), cfg.now().date() + _td(days=1)]):
        say(good, text, fix)
    print("drop folder")
    from clipfactory import inbox as I
    for good, text, fix in I.check(cfg):
        say(good, text, fix)
    print("the skills' own folder names (they are written into the skills' code)")
    for name, slug in LEGACY.items():
        acct = cfg.accounts.get(slug)
        if not acct:
            continue
        old = desk / name
        good = old.exists() and old.resolve() == acct.account_dir.resolve()
        if a.fix_paths and not old.exists() and acct.account_dir.is_dir():
            old.symlink_to(acct.account_dir)
            print(f"  made the link ~/Desktop/{name} -> {acct.account_dir}")
            good = True
        say(good, f"~/Desktop/{name} reaches {acct.account_dir}",
            "factory.py doctor --fix-paths (adds a link on the Desktop), or teach the skill to read its folder from an env var")
    from clipfactory import accounts as AC
    rows = AC.doctor(cfg)                    # nothing at all until a client is added (or two accounts share a folder)
    if rows:
        print("clients (their own folders, approvers and page)")
        for good, text, fix in rows:
            say(good, text, fix)
    print("outlets")
    for acct in cfg.accounts.values():
        for plat, o in acct.outlets.items():
            mode = o.get("mode", "export")
            extra = ""
            via_up = plat == "tiktok" and o.get("via") == "upload-post"
            tok = O.TOKENS / acct.slug / {"youtube_shorts": "youtube_token.json", "tiktok": "tiktok.json",
                                          "instagram_reels": "meta.json", "facebook_reels": "meta.json"}.get(plat, "-")
            if via_up:
                tok = O.TOKENS / acct.slug / "uploadpost.json"
            extra = f", signed in: {'yes' if tok.exists() else 'no'}"
            if via_up:
                link = json.loads(tok.read_text()) if tok.exists() else {}
                extra += f", via Upload-Post profile '{link.get('profile') or o.get('profile') or acct.slug}'"
                extra += f" -> @{link['handle']}" if link.get("handle") else ""
                extra += f", from {o['from']}" if o.get("from") else ""
            elif plat == "tiktok":
                extra += f", app audited: {bool(o.get('api_app_audited'))}"
            if mode == "api" and not tok.exists():     # it would post nothing and fail at its slot
                say(False, f"{acct.name} {plat}: {mode}{extra}", f"sign in first: {connect_words(acct, plat)}, or set "
                    f"its mode to export in config/accounts.json until it is connected")
                continue
            print(f"  --  {acct.name} {plat}: {mode}{extra}")
    print("posting")
    from clipfactory import poster as P
    health = P.health_words(cfg)
    if not health and not P._read_health():
        print("  --  post-due has not written state/post-due.health yet (its next run does)")
    else:
        say(not health, health or "post-due's last run was clean",
            f"{P.OWNERS_CALL}: read state/logs/post-due.log; launchd runs post-due every 10 minutes "
            f"(factory.py timer status)")
    for line, fix in P.drift_items(cfg):
        say(False, line, fix)
    from clipfactory import outlets as YT
    for a in cfg.accounts.values():          # the YouTube cap's pause: red, not "ok" (10-23 to 10-25)
        if a.active and "youtube_shorts" in YT.auto_platforms(a):
            paused = YT.yt_paused(a)
            if paused:
                say(False, paused, "the phone is verified (10-26): this is YouTube's own cap; keep the youtube_shorts outlet's "
                    "daily_budget under its cap in config/accounts.json")
    from clipfactory import autosign as A    # the automatic sign-off (autosign.py): on or off, per account
    for line in A.doctor_line(cfg):
        print(line)
    from clipfactory import reasons as KL    # the kill list (reasons.py): what a Pull or Decline said never again
    for good, text, fix in KL.doctor_lines(cfg):
        if good is None:
            print(f"  --  {text}")
        else:
            say(good, text, fix)
    print("render queue (state/renderlock: one render at a time on this Mac)")
    from clipfactory import renderlock as RL
    for good, text, fix in RL.doctor(cfg):
        say(good, text, fix)
    print("scouts (the last run: how many sources answered; under 80%, a failed run or two missed runs is "
          "a NO)")
    try:
        from clipfactory import scouthealth as SH
        scout_rows = SH.doctor(cfg)
    except Exception as e:  # noqa: BLE001  (doctor never stops over the scout check)
        scout_rows = [(False, f"scouts: cannot read the last run ({type(e).__name__}: {e})",
                       "look at clipfactory/scouthealth.py and state/<account>/scout/")]
    for good, text, fix in scout_rows:
        if good is None:                     # nothing recorded yet: said, not a failure
            print(f"  --  {text}")
        else:
            say(good, text, fix)
    print("the bench (spare clips for the slots a night leaves empty; bench.py)")
    from clipfactory import bench as BN
    for line in BN.doctor_lines(cfg):
        print(f"  --  {line}")
    print("the social layer (each part starts off; each line says what it waits for)")
    try:
        from clipfactory import social as SOC
        social_rows = SOC.doctor(cfg)
    except (Exception, SystemExit) as e:  # noqa: BLE001  (doctor never stops over the social layer)
        social_rows = [(False, f"the social layer: cannot check it ({type(e).__name__}: {e})",
                        "look at clipfactory/social.py and state/logs/social-errors.log")]
    for good, text, fix in social_rows:
        if good is None:                     # said, not a failure
            print(f"  --  {text}")
        else:
            say(good, text, fix)
    print("posting runner (launchd: factory.py timer)")
    from clipfactory import timer as TM
    for line in TM.status():
        # fine: loaded with Hermes's twin paused, or not on launchd while Hermes still runs that job
        say(line.startswith("ok") or (line.startswith("--") and "ACTIVE" in line), line[4:],
            "factory.py timer install --from-hermes")
    print("Claude's week (usage.py: the warning and the night shift's reserve)")
    try:
        from clipfactory import usage as U
        week_rows = U.doctor()
    except Exception as e:  # noqa: BLE001  (doctor never stops over the meter)
        week_rows = [(False, f"Claude's week: cannot count it ({type(e).__name__}: {e})", "factory.py usage check")]
    for good, text, fix in week_rows:
        if good is None:                     # said, not a failure
            print(f"  --  {text}")
        else:
            say(good, text, fix)
    print("run journal (journal.py: every launchd job's runs, every Claude task's start)")
    try:
        from clipfactory import journal as J
        j_rows = J.doctor()
    except Exception as e:  # noqa: BLE001  (doctor never stops over the journal)
        j_rows = [(None, f"the journal cannot be read ({type(e).__name__}: {e})", "")]
    for good, text, fix in j_rows:
        if good is None:
            print(f"  --  {text}")
        else:
            say(good, text, fix)
    print("the night's path (nightpath.py: each night run's required steps, from its own transcript)")
    try:
        from clipfactory import nightpath as NPH
        p_rows = NPH.doctor()
    except Exception as e:  # noqa: BLE001  (doctor never stops over the transcripts)
        p_rows = [(None, f"the night runs cannot be read ({type(e).__name__}: {e})", "")]
    for good, text, fix in p_rows:
        if good is None:
            print(f"  --  {text}")
        else:
            say(good, text, fix)
    print("hermes")
    from clipfactory import hermes as H
    for line in H.status():
        print(f"  {line}")
    print("the bots' instructions (one truth: config/accounts.json + ~/clip-factory/hermes)")
    for good, text, fix in H.drift(cfg):
        say(good, text, fix)
    print("\nall good" if ok else "\nsome checks failed (above)")


def cmd_usage(cfg, a):
    """Claude's week (clipfactory/usage.py): the meter, a task's gate, the 30-minute check."""
    from clipfactory import usage as U
    if a.action == "gate":
        if not a.task:
            raise SystemExit("usage gate: name the task, e.g. usage gate --task bench-builder-auto")
        go, text = U.gate(a.task)
        try:                                 # the run journal: a Claude task's start (the loops thesis item 6)
            from clipfactory import journal as J
            J.record(f"task {a.task}", "started" if go else "skipped", why=text[:160])
        except Exception:  # noqa: BLE001
            pass
        print(text)
        if not go:
            raise SystemExit(U.STOP)
    elif a.action == "check":
        lines = U.check(notify=a.notify)
        if not a.quiet or len(lines) > 1:
            show(lines)
    else:
        st = U.refresh()
        if a.json:
            print(json.dumps(st, indent=1))
            return
        show([U.line(st) + ".", f"level: {st['level']}" + (f" ({U.short(st)})" if U.short(st) else "")])


def cmd_hermes(cfg, a):
    from clipfactory import hermes as H
    scratch = Path(os.path.expanduser(a.scratch)) if a.scratch else None
    if a.action == "install":
        show(H.install(cfg, scratch=scratch))
    elif a.action == "remove":
        show(H.remove(scratch=scratch))
    elif a.action == "sync":
        show(H.sync(cfg, scratch=scratch))
    else:
        show(H.status(scratch=scratch))


def cmd_renderlock(cfg, a):
    """The render queue (clipfactory/renderlock.py): `run` waits its turn, runs the command, lets go; `status`."""
    from clipfactory import renderlock as RL
    if a.action == "status":
        st = RL.status()
        if a.json:
            print(json.dumps(st, indent=1))
        else:
            show(RL.lines(cfg, st))
        return
    cmd = list(a.cmd or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise SystemExit('renderlock run: put the command after --, e.g. renderlock run --account politics '
                         '--what "patel: render" -- /usr/bin/python3 build_all.py patel-yes-or-no')
    if a.account not in cfg.accounts:
        raise SystemExit(f"renderlock run: unknown account {a.account!r} (one of {', '.join(cfg.accounts)})")
    if a.wait < 0 or a.max_hold < 0:
        raise SystemExit("renderlock run: --wait and --max-hold are minutes, 0 or more (0 = no limit)")
    try:
        until = RL.until_epoch(a.until) if a.until else None
    except ValueError:
        raise SystemExit(f"renderlock run: --until is a time like 05:50, not {a.until!r}") from None
    try:
        stop_at = RL.until_epoch(a.stop_at) if a.stop_at else None
    except ValueError:
        raise SystemExit(f"renderlock run: --stop-at is a time like 06:15, not {a.stop_at!r}") from None
    if a.turn < 0:
        raise SystemExit("renderlock run: --turn is minutes, 0 or more (0 = off)")
    rc = RL.run(a.account, a.what, cmd, wait_s=a.wait * 60 if a.wait else None, until=until,
                max_hold_s=a.max_hold * 60 if a.max_hold else None, names={k: v.name for k, v in cfg.accounts.items()},
                stop_at=stop_at, turn_s=a.turn * 60 if a.turn else None)
    sys.exit(rc)


def cmd_shift(cfg, a):
    """The night shift (clipfactory/shift.py): who works which board tonight, and the clock."""
    from clipfactory import shift as SH
    if a.action == "day":
        print(C.parse_day(None, cfg).isoformat())       # before POSTING_DAY_STARTS (07:00) today, else tomorrow
        return
    if a.action == "status":
        day = C.parse_day(a.date, cfg)
        show(SH.lines(cfg, [cfg.account(a.account)] if a.account else list(cfg.accounts.values()), day))
        return
    if not a.account:
        raise SystemExit(f"shift {a.action}: say the account")
    if not a.date:
        raise SystemExit(f"shift {a.action}: say --date (the posting day the run fills, YYYY-MM-DD)")
    if not a.task:
        raise SystemExit(f"shift {a.action}: say --task (the scheduled task's id, e.g. hearing-clip-sweep-2)")
    acct, day = cfg.account(a.account), C.parse_day(a.date, cfg)
    own = C.normal_slot(a.until) if a.until else None
    if own and a.action in ("begin", "check") and int(own[:2]) >= 12:
        raise SystemExit(f"shift {a.action}: --until is a morning time (before noon), not {a.until!r}")
    if a.action == "begin":
        show(SH.begin(cfg, acct, day, a.task, until=own))
    elif a.action == "end":
        show(SH.end(cfg, acct, day, a.task, a.why or ""))
    elif a.action == "check":
        rc, words = SH.check(cfg, acct, day, a.task, until=own)
        print(words)
        sys.exit(rc)
    else:
        sys.exit(SH.wait(cfg, acct, day, a.task, until=own))


def cmd_bench(cfg, a):
    """The spare-clip bench (clipfactory/bench.py): status, add, fill, retire, render."""
    from clipfactory import bench as BN
    if a.action == "status":
        show(BN.status(cfg, cfg.account(a.account) if a.account else None))
        return
    if a.action == "retire":
        show(BN.retire(cfg, a.id, why=a.why or ""))
        return
    if a.action in ("fill", "add", "render") and a.account != "all" and \
            off_here(cfg, cfg.account(a.account), f"bench {a.action}"):
        return
    if a.action == "fill":
        day = C.parse_day(a.date, cfg)
        lines = BN.fill(cfg, a.account, day, dry_run=a.dry_run, slots=slots_arg(a.slots))
        if not a.dry_run:
            for acct in ([cfg.account(a.account)] if a.account != "all" else cfg.accounts.values()):
                if B.read(acct, day):
                    print(status_line(acct, day))
        return
    acct = cfg.account(a.account)
    if a.action == "render":
        ok, lines = BN.render(cfg, acct, a.job, a.slug, until=a.until)
        show(lines)
        if not ok:
            raise SystemExit(1)
        return
    words, why = list(a.allow_word or []), list(a.because or [])
    if len(words) != len(why) or any(not w.strip() for w in why):
        raise SystemExit("each --allow-word needs its own --because (why that word does not date this clip)")
    ok, lines = BN.add(cfg, acct, a.what, slug=a.slug, until=a.until, why=a.why or "", kind=a.kind or "",
                       by=a.by or "", allow=dict(zip((w.lower() for w in words), why)), made_on=a.made_on,
                       job=a.job, fmt=a.format, check=a.check)
    show(lines)
    if not ok:
        raise SystemExit(1)


def cmd_breaking(cfg, a):
    """The breaking desk (clipfactory/breaking.py): today's breaking moment cut and placed the same day."""
    from clipfactory import breaking as BK
    day = C.parse_day(getattr(a, "date", None) or "today", cfg)
    if a.action == "status":
        accts = [cfg.account(a.account)] if a.account else [x for x in cfg.accounts.values() if x.maker == "politics"]
        for acct in accts:
            show(BK.status_lines(cfg, acct, day))
        return
    acct = cfg.account(a.account)
    if a.action in ("quick", "place", "go") and off_here(cfg, acct, f"breaking {a.action}"):
        return
    if a.action == "quick":
        lines = BK.quick(cfg, acct, force=a.force)
        if not a.quiet or any(", new: " in ln or "not written" in ln for ln in lines):
            show(lines)
    elif a.action == "check":
        code, lines = BK.check(cfg, acct, day)
        show(lines)
        if code:
            raise SystemExit(code)
    elif a.action == "place":
        ok, lines = BK.place(cfg, acct, day, a.job, a.slug, find=a.find or "", by=a.by or "", slot=a.slot,
                             dry_run=a.dry_run, jobs_dir=Path(os.path.expanduser(a.jobs_dir)) if a.jobs_dir else None,
                             bump=a.bump)
        show(lines)
        if not ok:
            raise SystemExit(1)
    elif a.action == "go":
        ok, lines = BK.go(cfg, acct, day, a.slot, by=a.by or "")
        show(lines)
        if not ok:
            raise SystemExit(1)
    elif a.action == "ask":
        ok, lines = BK.ask(cfg, acct, day, a.find, by=a.by or "", what=a.what or "", card=not a.no_card)
        show(lines)
        if not ok:
            raise SystemExit(1)
    elif a.action == "yes":
        show(BK.say_yes(cfg, acct, day, a.find, by=a.by or "Andrew", words=a.words or ""))
    elif a.action == "pass":
        show(BK.pass_on(cfg, acct, day, a.find, a.why, by=a.by or ""))
    elif a.action == "undo":
        ok, lines = BK.undo(cfg, acct, day, a.slot, by=a.by or "")
        show(lines)
        if not ok:
            raise SystemExit(1)
    elif a.action == "replay":
        show(BK.replay(cfg, acct, day))


def cmd_phone(cfg, a):
    """Andrew's phone (clipfactory/phone.py): ntfy pushes with Yes / No buttons, and the button presses read back."""
    from clipfactory import phone as PH
    from clipfactory import breaking as BK
    if a.action == "setup":
        s = PH.settings(make=True)
        print(f"phone pushes {'ON' if s.get('on') else 'OFF'}\n  on the iPhone: the ntfy app, Subscribe to topic, "
              f"server {s['server']}, topic:\n  {s['topic']}")
    elif a.action == "test":
        ok, why = PH.send("Politics test", a.message or "Test from the clip factory. Tap a button.",
                          buttons=[("Yes", "test yes"), ("No", "test no")])
        print(("sent " if ok else "NOT sent: ") + why)
        if not ok:
            raise SystemExit(1)
    elif a.action == "poll":
        from clipfactory import newsphone as NP
        lines = PH.poll({"breaking": lambda w: BK.phone_answer(cfg, w),
                         "news": lambda w: NP.answer(cfg, w),
                         "test": lambda w: [f"  phone: test button {' '.join(w)}"]})
        lines += NP.tick(cfg)
        if lines or not a.quiet:
            show(lines or ["  phone: no button presses"])


def cmd_lab(cfg, a):
    """Lab (clipfactory/lab.py): its test bank, its takes, the evidence frames, the scripts. There is no
    outcome command on purpose: what a take shows is confirmed only by Andrew, on the Lab tab of Clip Review."""
    from clipfactory import lab as L
    acct = cfg.account(a.account)
    if not L.is_lab(acct):
        raise SystemExit(f"lab: {acct.name} is not a Lab account (its maker is {acct.maker!r})")
    if acct.machine != cfg.machine:
        print(f"lab {a.action}: {acct.name} runs on the machine \"{acct.machine}\"; this one is \"{cfg.machine}\"")
        return
    try:
        _lab(cfg, acct, a, L)
    except L.LabError as e:
        raise SystemExit(f"lab {a.action}: {e}") from None


def _lab(cfg, acct, a, L):
    day = C.parse_day(a.date, cfg) if getattr(a, "date", None) else None
    if a.action == "status":
        if a.json:
            print(json.dumps(L.status(cfg, acct, day, stamp=True), indent=1, ensure_ascii=False))
        else:
            show(L.status_lines(cfg, acct, day))
    elif a.action == "deal":
        d = L.deal(cfg, acct, day)
        bank = {t["id"]: t for t in L.bank_read(acct)["tests"]}
        print(f"{acct.name}: record for {d['day']}: {len(d['record'])} ({len(d['slots'])} slots"
              + (f", {d['rule']}" if d["rule"] else "") + f"; {d['filled']} filled, {d['stock']} on the way)")
        for tid in d["record"]:
            print(f"  {tid}  {bank[tid].get('area')}: {bank[tid]['question']}")
        for tid in d["extra"]:
            print(f"  more if there is time: {tid}  {bank[tid].get('area')}: {bank[tid]['question']}")
    elif a.action == "bank":
        _lab_bank(cfg, acct, a, L)
    elif a.action == "import-bank":
        if L.bank_read(acct).get("tests"):
            raise L.LabError(f"{L.bank_path(acct)} already holds tests: the import is one time only")
        research = json.loads(Path(os.path.expanduser(a.frm)).read_text())
        check = json.loads(Path(os.path.expanduser(a.check)).read_text())
        new = L.import_bank(research, check, launch=str(L.launch_day(acct) or "2026-11-19"))
        probs = L.bank_problems(new)
        if probs:
            raise L.LabError("the import has problems, nothing written: " + "; ".join(probs[:5]))
        if a.dry_run:
            print(f"{len(new['tests'])} tests would be imported (--dry-run: nothing written)")
            return
        with L.bank_edit(acct) as bank:
            bank.clear()
            bank.update(new)
        n = {s: sum(1 for t in new["tests"] if t["status"] == s) for s in ("todo", "held")}
        print(f"{acct.name}: {len(new['tests'])} tests imported into {L.bank_path(acct)} ({n['todo']} to record, "
              f"{n['held']} held for your Allow)")
    elif a.action == "inbox":
        show(L.watchdog(cfg, acct) + L.inbox_scan(cfg, acct) or ["  nothing waiting in the Lab inbox"])
    elif a.action == "frames":
        show(L.frames(cfg, acct, a.test, focus=a.focus))
    elif a.action == "variety":
        print(json.dumps(L.variety(cfg, acct, day or cfg.tomorrow()), indent=1, ensure_ascii=False))
    elif a.action == "script":
        ok, lines = L.accept_script(cfg, acct, a.test, Path(os.path.expanduser(a.file)), check=a.check)
        show(lines)
        if not ok:
            raise SystemExit(2)
    elif a.action == "redo":
        show(L.redo(cfg, acct, a.test, a.kind, a.why))
    elif a.action == "obs-setup":
        _lab_obs_setup(acct, a, L)
    elif a.action == "obs-check":
        h = L.obs_check(acct, deep=True)
        if not h.get("ok"):
            raise SystemExit(f"lab obs-check: {h.get('why')}")
        from clipfactory import obs as O
        v = h.get("video") or {}
        ver = O.version_tuple(h.get("obs") or "")
        print(f"  OBS {h.get('obs')} (websocket {h.get('ws')})" + ("" if ver >= O.WANT_OBS else
              "  <- 30.2 or later is wanted (Hybrid MP4, chapters)" if ver >= O.MIN_OBS else "  <- too old: 30.0 at least"))
        print(f"  video {v.get('baseWidth')}x{v.get('baseHeight')} -> {v.get('outputWidth')}x{v.get('outputHeight')} "
              f"at {v.get('fpsNumerator')}/{v.get('fpsDenominator')} fps" + ("" if v.get("outputHeight") == 2160 else
                                                                            "  <- 3840x2160 is wanted"))
        print(f"  the card's picture: {h.get('picture_words')}; its sound: "
              + (f"{h['sound_db']} dB" if h.get("sound_db") is not None else "no meter") + ("" if (h.get("sound_db") or -99)
                                                                                         > -60 else "  <- no game sound"))
        print(f"  OBS records into {h.get('record_dir')} (Record sets it to {L.capture_root(acct) / '_obs'})")
        print("  recording now" if h.get("recording") else "  not recording")
    elif a.action == "setup":
        show(L.setup_folders(acct))
    elif a.action == "tidy":
        if not a.dry_run:
            print("lab tidy only lists (--dry-run): the factory never deletes a take")
        show(L.tidy(acct))


def _lab_bank(cfg, acct, a, L):
    if a.what == "show":
        bank = L.bank_read(acct)
        for t in bank["tests"]:
            if a.test and t["id"] != a.test.upper():
                continue
            print(f"  {t['id']}  {t['status']:<9} {t.get('game')}  {t.get('area')}: {t['question']}"
                  + (f"  [held: {(t.get('hold') or {}).get('why')}]" if t.get("hold") else ""))
        probs = L.bank_problems(bank)
        print(f"  {len(bank['tests'])} tests" + (f"; problems: {'; '.join(probs)}" if probs else ""))
    elif a.what == "add":
        tid = L.add_test(acct, a.question or "", a.game or L.game_on(acct, cfg.now().date()), kind=a.kind or "",
                         area=a.area or "", setup=a.step or [], answer=a.answer or "", sources=a.source or [],
                         by=a.by or "")
        print(f"  {tid} added")
    elif a.what == "hold":
        print(L.hold_test(acct, a.test or "", a.why or ""))
    else:                                # allow, drop: Andrew's own taps on the Lab tab
        raise SystemExit(f"lab bank {a.what}: refused here. A hold is lifted, and a test dropped, only by Andrew on "
                         f"the Lab tab of Clip Review (Holds: Allow / Drop)")


def _lab_obs_setup(acct, a, L):
    """OBS's websocket settings into obs.json (mode 600, its folder 700): the password at a hidden prompt, never
    printed, then one GetVersion to prove it."""
    import getpass
    from clipfactory import obs as O
    path = L.obs_path(acct)
    old = {}
    try:
        old = O.read_settings(path)
    except O.OBSError:
        pass
    pw = getpass.getpass("OBS websocket password (OBS: Tools > WebSocket Server Settings > Show Connect Info): ")
    if not pw.strip():
        raise SystemExit("lab obs-setup: no password typed; nothing written")
    data = {"host": a.host or old.get("host") or "127.0.0.1", "port": int(a.port or old.get("port") or O.DEFAULT_PORT),
            "password": pw, "video_input": a.video_input or old.get("video_input") or "Capture card",
            "audio_input": a.audio_input or old.get("audio_input") or "Capture card audio",
            "scene": a.scene or old.get("scene") or "Lab", "console": a.console or old.get("console") or "PS5"}
    O.write_settings(path, data)
    print(f"  saved {path} (mode 600; the password is not shown)")
    h = L.obs_check(acct)
    print(f"  OBS: {h.get('why')}" + (f" (OBS {h.get('obs')}, websocket {h.get('ws')})" if h.get("ok") else ""))


def cmd_timer(cfg, a):
    from clipfactory import timer as TM
    if a.action == "install":
        show(TM.install(cfg, from_hermes=a.from_hermes, only=a.only))
    elif a.action == "remove":
        show(TM.remove())
    elif a.action == "check":
        show(TM.check())
    elif a.action == "probe":
        show(TM.probe(cfg))
    else:
        show(TM.status())


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="factory.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def acct_cmd(name, fn, account_required=True):
        p = sub.add_parser(name)
        p.add_argument("account", nargs=None if account_required else "?")
        p.add_argument("--date", default=None,
                       help="YYYY-MM-DD, today or tomorrow (default: the next posting day, tomorrow; before 07:00, today)")
        p.set_defaults(fn=fn)
        return p

    p = acct_cmd("plan", cmd_plan)
    p.add_argument("--slots", help="with --format: the slots to set")
    p.add_argument("--format", help="set those slots' format (one of the account's slot_formats)")
    p.add_argument("--resync", action="store_true",
                   help="an account with a dated volume rule: add the rule's missing slots (open), take out the "
                        "open slots it no longer lists; a filled slot is never touched")
    p = acct_cmd("make", cmd_make)
    p.add_argument("--slots")
    p.add_argument("--standin", action="store_true", help="a rehearsal clip per slot instead of the skill")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--background", action="store_true")
    p.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    p = acct_cmd("collect", cmd_collect)
    p.add_argument("--from", dest="frm")
    p.add_argument("--quiet", action="store_true", help="print nothing unless a clip was collected")
    p = acct_cmd("note", cmd_note)
    p.add_argument("text")
    p = acct_cmd("night-retry", cmd_night_retry)
    p.add_argument("--dry-run", action="store_true", help="say which slots it would make again; start nothing")
    p = acct_cmd("nightly", cmd_nightly)
    p.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    acct_cmd("status", cmd_status, account_required=False)
    p = acct_cmd("morning", cmd_morning, account_required=False)
    p.add_argument("--notify", action="store_true", help="also show it as a Mac notification")
    p = acct_cmd("approve", cmd_approve)
    p.add_argument("--all", action="store_true")
    p.add_argument("--slots")
    p.add_argument("--by", required=True)
    p = acct_cmd("pull", cmd_pull)
    p.add_argument("--slots", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--code", help="why (clipfactory/reasons.py): wrong-name, framing, cut, audio, length, repeat, "
                                  "incomplete, text, hook, rights, other; it puts the clip on the kill list")
    p.add_argument("--reason", help="words beside the code (with no code they count as other)")
    p = acct_cmd("igcut", cmd_igcut)
    p.add_argument("--bench", metavar="ID", help="a bench item's id: the cut is made into the item (no slot, no collect)")
    p.add_argument("--slug", required=True, help="the clip's slug (its delivery folder is '<HHMM> <slug>')")
    p.add_argument("--end", type=float, help="the cut ends here (master seconds); it keeps the clip's start")
    p.add_argument("--segments", help="or the pieces it keeps, in order: 3961-3990,4004-4021.5 (master seconds); "
                   "either is measured first, and one that misses the band takes the cut that fits nearest to it")
    p.add_argument("--pick", action="store_true",
                   help="or: the skill's cutpick.py picks the pieces (whole sentences, one piece or two) by the "
                        "render's own measure, so the cut lands inside the band, and re-times the clip's cards")
    p.add_argument("--hook", help="the words on the picture for the cut, when the clip's would overclaim")
    p.add_argument("--no-hook2", action="store_true", help="drop the clip's second hook")
    p.add_argument("--words", help="the cut's own Instagram words (a paste file), when the clip's would overclaim")
    p.add_argument("--python", help="the Python that runs the skill (default: /usr/bin/python3, as the main renders)")
    p.add_argument("--set", action="append", default=[], metavar="KEY=JSON",
                   help='a slate field for the cut, e.g. cards=[{"who":"warsh","at":4.5,"hold":4}] or poster_at=10')
    p = acct_cmd("ytcut", cmd_ytcut)
    p.add_argument("--bench", metavar="ID", help="a bench item's id: the cut is made into the item (no slot, no collect)")
    p.add_argument("--missing", action="store_true", help="list the day's clips that need a YouTube cut and have none")
    p.add_argument("--slug", help="the clip's slug (its delivery folder is '<HHMM> <slug>')")
    p.add_argument("--segments", help="the pieces the cut keeps, in order (master seconds)")
    p.add_argument("--end", type=float, help="or: the cut ends here (master seconds); it keeps the clip's start. "
                   "Either is measured first, and one that misses the band takes the cut that fits nearest to it")
    p.add_argument("--from-instagram", action="store_true",
                   help="or: take the clip's Instagram cut as its YouTube cut (no render; it passed the same checks)")
    p.add_argument("--pick", action="store_true",
                   help="or: the skill's cutpick.py picks the pieces by the render's own measure (as igcut --pick)")
    p.add_argument("--hook", help="the words on the picture for the cut, when the clip's would overclaim")
    p.add_argument("--no-hook2", action="store_true", help="drop the clip's second hook")
    p.add_argument("--words", help="the cut's own YouTube words (a copy of the clip's youtube.txt, corrected)")
    p.add_argument("--python", help="the Python that runs the skill (default: /usr/bin/python3, as the main renders)")
    p.add_argument("--set", action="append", default=[], metavar="KEY=JSON",
                   help="a slate field for the cut, in seconds into the cut (as igcut)")
    p = acct_cmd("retry", cmd_retry)
    p.add_argument("--slots", required=True)
    p.add_argument("--by", required=True)
    p = acct_cmd("stale", cmd_stale)
    p.add_argument("--slots", required=True)
    p.add_argument("--why", help="why it is stale, in a few words (the card shows it)")
    p.add_argument("--by", help="who marks it (default: the night)")
    p.add_argument("--clear", action="store_true", help="take the mark off")
    p = acct_cmd("bandcheck", cmd_bandcheck, account_required=False)
    p = acct_cmd("decline", cmd_decline)
    p.add_argument("--slots", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--reason")
    p.add_argument("--remake", action="store_true")
    p.add_argument("--reasons", help="what was wrong, by code: hook,framing (the reason codes in verdicts.py)")
    p.add_argument("--code", help="why, one code (clipfactory/reasons.py): wrong-name, framing, cut, audio, length, "
                                  "repeat, incomplete, text, hook, rights, other (other needs --reason)")
    p = acct_cmd("killlist", cmd_killlist)
    p.add_argument("--all", action="store_true", help="lifted entries too")
    p.add_argument("--check", metavar="URL", help="may the night cut this video (and --span seconds of it)?")
    p.add_argument("--span", help="A-B or A-B,C-D (source seconds), with --check")
    p.add_argument("--lift", metavar="ID", help="take an entry back (it stays on file, marked lifted)")
    p.add_argument("--why", help="why it is lifted (with --lift)")
    p.add_argument("--by", help="who lifts it (with --lift)")
    p = acct_cmd("place", cmd_place)
    p.add_argument("--slot", required=True, help="the one slot, HH:MM")
    p.add_argument("--clip", help="the finished .mp4 (copied, never moved)")
    p.add_argument("--words", help="a .txt (first line title, then the caption) or a folder of paste files per platform")
    p.add_argument("--cover", help="the cover .jpg (default: the .jpg beside the clip)")
    p.add_argument("--source-url", nargs="+", action="extend", metavar="URL",
                   help="every video the clip is cut from (the nightly never cuts them again)")
    p.add_argument("--job", help="GTA: the skill job; its copy engine writes the words, its clip and sources are used")
    p.add_argument("--slug", help="with --job: the clip's slug on that job's slate")
    p.add_argument("--format", help="the slot's format (default: reactions on a REACTIONS slot, else the slot's own)")
    p.add_argument("--host", help="who is on it, for the card and Stats (default: the job's host)")
    p.add_argument("--why", help="a line for the board's notes")
    p.add_argument("--release", action="store_true", help="no REACTIONS clip for this slot: the nightly fills it")
    p.add_argument("--replace", action="store_true",
                   help="a newer render of the clip this unsigned slot holds (the same slug) takes its place")
    p.add_argument("--check", action="store_true", help="say what would happen, write nothing")
    p = acct_cmd("schedule", cmd_schedule)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("stats")
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--no-refresh", action="store_true", help="skip asking the scheduler; write what the boards hold")
    p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("inbox")
    p.add_argument("--quiet", action="store_true", help="print nothing when nothing was dropped")
    p.set_defaults(fn=cmd_inbox)
    p = sub.add_parser("tiktok-now", help="post one signed clip to TikTok at once (the card's Post to TikTok now)")
    p.add_argument("account")
    p.add_argument("date")
    p.add_argument("slot")
    p.set_defaults(fn=cmd_tiktok_now)
    p = sub.add_parser("post-due")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_post_due)
    sub.add_parser("review").set_defaults(fn=cmd_review)
    p = sub.add_parser("connect")
    p.add_argument("platform", choices=["youtube", "meta", "instagram", "tiktok", "upload-post"])
    p.add_argument("account")
    p.add_argument("--client", help="youtube: the OAuth client JSON file")
    p.add_argument("--client-key", help="tiktok: the app's client key")
    p.add_argument("--profile", help="upload-post: the profile's name there (default: the account's slug)")
    p.add_argument("--new-key", action="store_true", help="upload-post: ask for the API key again")
    p.add_argument("--with", dest="with_", metavar="comments,analytics",
                   help="youtube: also ask for the comments (youtube.force-ssl) and the numbers (yt-analytics.readonly)")
    p.set_defaults(fn=cmd_connect)
    p = sub.add_parser("scout")
    p.add_argument("account", help="an account (gaming, politics ...) or all")
    p.add_argument("--hours", type=float, help="how far back to look (default: config/scout.json, 36)")
    p.add_argument("--feed", type=int, help="GTA: put up to N top roster finds in the inbox (default: auto_feed)")
    p.add_argument("--pick", nargs="+", metavar="ID")
    p.add_argument("--kill", nargs="+", metavar="ID")
    p.add_argument("--why", default="")
    p.add_argument("--by", default="scout")
    p.add_argument("--quiet", action="store_true", help="one line and the board's path")
    p.set_defaults(fn=cmd_scout)
    p = sub.add_parser("daily8")
    p.add_argument("account")
    p.add_argument("--date", help="the scout's day (default today): the board is for the day after")
    p.add_argument("--out", help="where to write (default Clip Drafts/Scout, beside the scout's own board)")
    p.set_defaults(fn=cmd_daily8)
    p = sub.add_parser("prep", help="the afternoon prep: the night's intakes for the posting day, run now (GTA)")
    p.add_argument("account", help="a GTA account (gaming)")
    p.add_argument("--date", help="the posting day (default: the day the next night fills)")
    p.add_argument("--dry-run", action="store_true", help="say what it would take in, take in nothing")
    p.add_argument("--quiet", action="store_true", help="the first and last lines")
    p.set_defaults(fn=cmd_prep)
    p = sub.add_parser("drop", help="GTA drop mode: Rockstar's channel, then the roster's Twitch and Kick clips of a drop")
    p.add_argument("account", help="a GTA account (gaming)")
    p.add_argument("--quiet", action="store_true", help="say only what changed")
    p.add_argument("--backtest", metavar="FIXTURE", help="replay a saved drop offline (tests/fixtures/drop/*.json)")
    p.add_argument("--until-h", type=float, help="backtest: hours after the upload to replay (default horizon_h)")
    p.add_argument("--out", help="backtest: write the report here (.md) and the replay beside it (.json)")
    p.set_defaults(fn=cmd_drop)
    p = sub.add_parser("doctor")
    p.add_argument("--fix-paths", action="store_true")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("casts", help="the Politics's cast queue: who waits for Andrew's check (no command confirms)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--confirmed", action="store_true",
                   help="the checks he wrote (Casts, or Approve at a sign-off), for the runs")
    p.add_argument("--since", help="with --confirmed: today (default), yesterday, YYYY-MM-DD, HH:MM, or an ISO time")
    p.add_argument("--stills", action="store_true", help="pull the missing stills (one small frame per time)")
    p.add_argument("--notify", action="store_true", help="a Mac notice when anyone waits")
    p.set_defaults(fn=cmd_casts)
    from clipfactory import accounts as AC   # client accounts: account add | list | page | job | remove
    AC.add_parser(sub)
    from clipfactory import changes as CH    # the change politics: changes list | due | add (loops thesis item 2)
    CH.add_parser(sub)
    from clipfactory import journal as JR    # the run journal: journal jobs | show (loops thesis item 6)
    JR.add_parser(sub)
    from clipfactory import judge as JG      # the coach graded against Andrew's calls (loops thesis item 18)
    JG.add_parser(sub)
    from clipfactory import nightpath as NPH  # the night runs' required steps (loops thesis item 19)
    NPH.add_parser(sub)
    from clipfactory import gates as GTS     # every gate's refusals, a few to check by hand (loops thesis item 3)
    GTS.add_parser(sub)
    from clipfactory import replay as RPL    # replay real nights before an engine change goes live (loops thesis item 7)
    RPL.add_parser(sub)
    p = sub.add_parser("usage", help="Claude's week: the warning and the night shift's reserve (clipfactory/usage.py)")
    p.add_argument("action", nargs="?", default="show", choices=["show", "gate", "check"])
    p.add_argument("--task", help="gate: the scheduled task's id")
    p.add_argument("--notify", action="store_true", help="check: a Mac notice when the week got worse")
    p.add_argument("--quiet", action="store_true", help="check: print only when there is a notice")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_usage)
    p = sub.add_parser("hermes")
    p.add_argument("action", choices=["install", "sync", "status", "remove"])
    p.add_argument("--scratch", help="a throwaway HERMES_HOME to rehearse in")
    p.set_defaults(fn=cmd_hermes)
    p = sub.add_parser("bench", help="spare clips for the slots a night leaves empty (clipfactory/bench.py)")
    bq = p.add_subparsers(dest="action", required=True)
    q = bq.add_parser("status", help="every spare, its state, until when it is good")
    q.add_argument("account", nargs="?")
    q.set_defaults(fn=cmd_bench)
    q = bq.add_parser("add", help="a finished, checked clip onto the bench (copied, never moved)")
    q.add_argument("account")
    q.add_argument("what", help="a clip folder (the delivery shape), or a job name with --slug")
    q.add_argument("--slug", help="the clip's slug on the job's slate (or the folder's, when it does not say)")
    q.add_argument("--job", help="with a folder: the job it was cut from (the Politics needs it: cast, day rule)")
    q.add_argument("--until", help="YYYY-MM-DD: the last day it may post (default: bench.evergreen_days, capped)")
    q.add_argument("--why", help="why it stays good that long (for the board's note)")
    q.add_argument("--kind", help="a word for what it is (explainer, archive refusal ...)")
    q.add_argument("--format", help="GTA: the lane when no slate says it (underlay, pip, gameplay)")
    q.add_argument("--by", help="who added it")
    q.add_argument("--made-on", help="YYYY-MM-DD it was rendered (default: the clip file's date)")
    q.add_argument("--allow-word", action="append", metavar="WORD", help="a dated word that does not date this clip")
    q.add_argument("--because", action="append", metavar="WHY", help="why (one per --allow-word)")
    q.add_argument("--check", action="store_true", help="say what would happen, write nothing")
    q.set_defaults(fn=cmd_bench)
    q = bq.add_parser("fill", help="spares into the day's empty slots (launchd at 06:18; writes nothing after 06:25)")
    q.add_argument("account", help="an account, or all")
    q.add_argument("--date", required=True, help="today (the fill writes only on its own posting day)")
    q.add_argument("--slots", help="only these slots, HH:MM,HH:MM")
    q.add_argument("--dry-run", action="store_true", help="say what it would place; any time, any day; writes nothing")
    q.set_defaults(fn=cmd_bench)
    q = bq.add_parser("retire", help="a spare that must never go out")
    q.add_argument("id")
    q.add_argument("--why")
    q.set_defaults(fn=cmd_bench)
    q = bq.add_parser("render", help="GTA: render one slated clip for the bench, no slot (daylight, render queue)")
    q.add_argument("account")
    q.add_argument("--job", required=True)
    q.add_argument("--slug", required=True)
    q.add_argument("--until", default="17:30", help="HH:MM: never starts at or after it (default 17:30)")
    q.set_defaults(fn=cmd_bench)
    p = sub.add_parser("phone", help="Andrew's phone: ntfy pushes with Yes / No buttons (clipfactory/phone.py)")
    ph = p.add_subparsers(dest="action", required=True)
    q = ph.add_parser("setup", help="make state/phone.json (random topics) and print the topic to subscribe to")
    q.set_defaults(fn=cmd_phone)
    q = ph.add_parser("test", help="send a test push with two buttons")
    q.add_argument("message", nargs="?")
    q.set_defaults(fn=cmd_phone)
    q = ph.add_parser("poll", help="read the button presses and record them (launchd, every minute)")
    q.add_argument("--quiet", action="store_true")
    q.set_defaults(fn=cmd_phone)
    p = sub.add_parser("breaking", help="the breaking desk: today's breaking moment cut the same day (clipfactory/breaking.py)")
    bk = p.add_subparsers(dest="action", required=True)
    q = bk.add_parser("status", help="today's alarms and the breaking clips placed")
    q.add_argument("account", nargs="?")
    q.add_argument("--date", help="YYYY-MM-DD (default today)")
    q.set_defaults(fn=cmd_breaking)
    q = bk.add_parser("quick", help="the 15-minute check: the scout's feeds only, no yt-dlp (launchd)")
    q.add_argument("account")
    q.add_argument("--force", action="store_true", help="outside 07:00-19:00 too, and right after the scout")
    q.add_argument("--quiet", action="store_true", help="print only a new alarm (the launchd job's log)")
    q.set_defaults(fn=cmd_breaking)
    q = bk.add_parser("check", help="the desk's first step: exit 0 GO (an alarm and a slot), 3 STOP")
    q.add_argument("account")
    q.add_argument("--date", help="YYYY-MM-DD (default today)")
    q.set_defaults(fn=cmd_breaking)
    q = bk.add_parser("place", help="a rendered breaking clip onto the day's board; the clip in its slot to the bench")
    q.add_argument("account")
    q.add_argument("--date", help="YYYY-MM-DD (default today)")
    q.add_argument("--job", required=True, help="the job the clip was rendered in")
    q.add_argument("--slug", required=True)
    q.add_argument("--find", help="the alarm it answers (its video id, from `breaking check`)")
    q.add_argument("--slot", help="HH:MM: only this slot (default: the desk's own pick)")
    q.add_argument("--by", help="who placed it")
    q.add_argument("--jobs-dir", help="a proof job's folder outside the Jobs folder (--dry-run only)")
    q.add_argument("--bump", action="store_true", help="one of the eight slots instead of an extra one (its clip to "
                                                       "the bench)")
    q.add_argument("--dry-run", action="store_true", help="say where it would go, write nothing")
    q.set_defaults(fn=cmd_breaking)
    q = bk.add_parser("ask", help="mark an alarm asked and print the push that asks Andrew (yes or no)")
    q.add_argument("account")
    q.add_argument("--date", help="YYYY-MM-DD (default today)")
    q.add_argument("--find", required=True)
    q.add_argument("--by")
    q.add_argument("--what", help="the news in one plain short line (the desk writes it); default: the video's title")
    q.add_argument("--no-card", action="store_true", help="no picture")
    q.set_defaults(fn=cmd_breaking)
    q = bk.add_parser("yes", help="Andrew's yes to an alarm: the desk cuts it and it posts by itself")
    q.add_argument("account")
    q.add_argument("--date", help="YYYY-MM-DD (default today)")
    q.add_argument("--find", required=True)
    q.add_argument("--by", help="who said yes (default Andrew)")
    q.add_argument("--words", help="his words, as he said them")
    q.set_defaults(fn=cmd_breaking)
    q = bk.add_parser("pass", help="the desk looked at an alarm and does not cut it")
    q.add_argument("account")
    q.add_argument("--date", help="YYYY-MM-DD (default today)")
    q.add_argument("--find", required=True)
    q.add_argument("--why", required=True)
    q.add_argument("--by")
    q.set_defaults(fn=cmd_breaking)
    q = bk.add_parser("go", help="its platform cuts are made: the breaking clip signs itself and posts now")
    q.add_argument("account")
    q.add_argument("--date", help="YYYY-MM-DD (default today)")
    q.add_argument("--slot", required=True, help="HH:MM, the slot `breaking place` gave it")
    q.add_argument("--by")
    q.set_defaults(fn=cmd_breaking)
    q = bk.add_parser("undo", help="the breaking clip out of its slot and the bumped clip back, before it goes out")
    q.add_argument("account")
    q.add_argument("--date", help="YYYY-MM-DD (default today)")
    q.add_argument("--slot", required=True)
    q.add_argument("--by")
    q.set_defaults(fn=cmd_breaking)
    q = bk.add_parser("replay", help="the dry run: the day's scout board as the desk would have read it; writes nothing")
    q.add_argument("account")
    q.add_argument("--date", help="YYYY-MM-DD (default today)")
    q.set_defaults(fn=cmd_breaking)
    p = sub.add_parser("renderlock", help="the render queue: one render at a time on this Mac")
    rl = p.add_subparsers(dest="action", required=True)
    q = rl.add_parser("run", help="wait for the turn, run the command after --, let the next one go")
    q.add_argument("--account", required=True, help="whose render it is (an account in config/accounts.json)")
    q.add_argument("--what", required=True, help='a few words for status and the review page, e.g. "patel: render"')
    q.add_argument("--until", help="HH:MM: it never starts at or after this time, busy or free (exit 75)")
    q.add_argument("--stop-at", help="HH:MM: the command is stopped at this time if it still runs (exit 124)")
    q.add_argument("--wait", type=float, default=90, help="minutes; waited this long, it runs anyway beside the "
                                                          "holder (0 = no limit). Default 90")
    q.add_argument("--turn", type=float, default=60, help="minutes; next in line behind a step of ANOTHER account "
                                                          "that has held the queue this long, it runs beside it "
                                                          "(0 = off). Default 60")
    q.add_argument("--max-hold", type=float, default=240, help="minutes; the command is stopped past this "
                                                               "(0 = no limit). Default 240")
    q.add_argument("cmd", nargs=argparse.REMAINDER)
    q.set_defaults(fn=cmd_renderlock)
    q = rl.add_parser("status", help="what is rendering, what waits, the last turns")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_renderlock)
    p = sub.add_parser("shift", help="the night shift: who works which board tonight (clipfactory/shift.py)")
    p.add_argument("action", choices=["begin", "check", "end", "wait", "status", "day"])
    p.add_argument("account", nargs="?")
    p.add_argument("--date", help="the posting day the run fills (YYYY-MM-DD; status: default the next posting day)")
    p.add_argument("--task", help="the scheduled task's id (hearing-clip-sweep-2, politics-early-gap-fill, ...)")
    p.add_argument("--until", help="HH:MM, this run's own deadline (default 05:50): wait gives up then (exit 75); "
                                   "begin records it and check says stop from then (exit 75; a morning time, e.g. "
                                   "the 07:45 morning fill's 11:30)")
    p.add_argument("--why", help="end: a few words (10 of 10, the clock, an error)")
    p.set_defaults(fn=cmd_shift)
    from clipfactory import registry as REG   # the social layer and each part's commands (a part not built says so)
    social_names = ["social"] + list(REG.COMMANDS)
    for name in social_names:
        p = sub.add_parser(name, help="the social layer (clipfactory/social.py)" if name == "social"
                           else f"a social part's commands (clipfactory/{REG.COMMANDS[name]}.py)")
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(fn=lambda cfg, a, _name=name: REG.run_cli(cfg, _name, a.rest))
    p = sub.add_parser("lab", help="Lab: its test bank, takes, evidence frames and scripts (clipfactory/lab.py)")
    lq = p.add_subparsers(dest="action", required=True)

    def lab_cmd(name, help_):
        q = lq.add_parser(name, help=help_)
        q.add_argument("account")
        q.set_defaults(fn=cmd_lab, date=None)
        return q
    q = lab_cmd("status", "what waits for a script, the stock, the rule in force (--json: the scripting task's form)")
    q.add_argument("--json", action="store_true")
    q.add_argument("--date", help="the posting day (default: tomorrow)")
    q = lab_cmd("deal", "the tests the Lab tab asks to record for the next posting day")
    q.add_argument("--date")
    q = lab_cmd("bank", "the test bank: show | add | hold (Allow and Drop are Andrew's, on the Lab tab)")
    q.add_argument("what", choices=["show", "add", "hold", "allow", "drop"])
    q.add_argument("test", nargs="?")
    q.add_argument("--question")
    q.add_argument("--game", choices=["GTA 5", "GTA 6"])
    q.add_argument("--kind")
    q.add_argument("--area")
    q.add_argument("--step", action="append", help="one setup step (repeat it, in order)")
    q.add_argument("--answer", help="what the wiki says (the recording decides)")
    q.add_argument("--source", action="append")
    q.add_argument("--why")
    q.add_argument("--by")
    q = lab_cmd("import-bank", "one time: the bank from the 10-20 research and its check")
    q.add_argument("--from", dest="frm", required=True)
    q.add_argument("--check", required=True)
    q.add_argument("--dry-run", action="store_true")
    lab_cmd("inbox", "file the console recordings dropped in Capture/inbox (launchd's inbox job does it too)")
    q = lab_cmd("frames", "the evidence frames around each mark of a test's take")
    q.add_argument("test")
    q.add_argument("--focus", type=float, default=0.5, help="the 9:16 crop preview's place, 0-1 (default 0.5)")
    q = lab_cmd("variety", "structures, areas, openers and verdicts used (14 days) and on day D")
    q.add_argument("--date")
    q = lab_cmd("script", "check a test's script.json and accept it (scripted)")
    q.add_argument("test")
    q.add_argument("--file", required=True)
    q.add_argument("--check", action="store_true", help="check only, write nothing")
    q = lab_cmd("redo", "send a test back: relook (the frames disagree: Andrew looks again) or redo (a new take)")
    q.add_argument("test")
    q.add_argument("--kind", required=True, choices=["relook", "redo"])
    q.add_argument("--why", required=True)
    q = lab_cmd("obs-setup", "OBS's websocket password into obs.json (mode 600), at a hidden prompt")
    q.add_argument("--host")
    q.add_argument("--port", type=int)
    q.add_argument("--video-input", help='the capture card\'s Video Capture Device source (default "Capture card")')
    q.add_argument("--audio-input", help='its Audio Input Capture source (default "Capture card audio")')
    q.add_argument("--scene")
    q.add_argument("--console", choices=["PS5", "Xbox"])
    lab_cmd("obs-check", "OBS's version, video settings, record folder, the card's picture and sound")
    lab_cmd("setup", "the capture root (outside iCloud), its folders, and the account folder's Capture link")
    q = lab_cmd("tidy", "each take's size and whether it is spent (lists only: never deletes)")
    q.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("timer")
    p.add_argument("action", choices=["install", "status", "check", "remove", "probe"])
    p.add_argument("--from-hermes", action="store_true", help="pause Hermes's factory-post-due/inbox/stats jobs first")
    p.add_argument("--only", help="install: this one job (e.g. usage), the others left running as they are")
    p.set_defaults(fn=cmd_timer)

    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in social_names:      # each part parses its own words: argparse's REMAINDER refuses a first
        a = argparse.Namespace(cmd=raw[0], rest=raw[1:])     # word that starts with "--" (social import --from DIR)
        a.fn = lambda cfg, ns, _name=raw[0]: REG.run_cli(cfg, _name, ns.rest)
    else:
        a = ap.parse_args(argv)
    if os.environ.get("CLIP_FACTORY_LOG_STAMP"):   # set by the launchd agents: one dated line per run in their log
        from datetime import datetime
        print(f"== {datetime.now():%Y-%m-%d %H:%M:%S} factory.py {' '.join(argv if argv is not None else sys.argv[1:])}",
              flush=True)
    for k in ("_worker", "background", "dry_run", "standin"):
        if not hasattr(a, k):
            setattr(a, k, False)
    _run(a, raw)


def _run(a, raw: List[str]) -> None:
    """Run the command. A launchd job (its parent is launchd, or its agent set CLIP_FACTORY_LOG_STAMP) also leaves one
    line in the run journal (clipfactory/journal.py, the loops thesis item 6). The journal never changes what the
    command does: an error of its own is swallowed, and the command's own exit or error goes on as it was."""
    import time as _time
    words = [str(x) for x in raw if not str(x).startswith("-")][:2]
    launchd = os.getppid() == 1 or bool(os.environ.get("CLIP_FACTORY_LOG_STAMP"))
    job = " ".join(words) if launchd and os.environ.get("CLIP_FACTORY_JOURNAL", "1") != "0" else ""
    t0, status, code = _time.time(), "ok", 0
    try:
        a.fn(C.load(), a)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        status = "ok" if code == 0 else "exit"
        raise
    except BaseException as e:  # noqa: BLE001  (written down, then raised as it was)
        status, code = "failed", type(e).__name__
        raise
    finally:
        if job:
            try:
                from clipfactory import journal as J
                J.record(job, status, code=code, seconds=round(_time.time() - t0, 1))
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
