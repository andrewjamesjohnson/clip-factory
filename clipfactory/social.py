"""The social layer's front: what the factory, the review page and the timers call, gathered from each part.

The shared files (review.py, poster.py, collect.py, factory.py) never import a social part. They call this module, and
it calls each part through the plug board (registry.py), one at a time, each in its own try: a part that is not built
is skipped, one that raises is logged once in state/logs/social-errors.log and left out. With every switch off each
part answers nothing, so every page, notice and doctor run is what it was before the social layer.

    card_parts(cfg, acct, day, s, now)   the folds under a draft card (coach, first comment, checkpoints ...)
    health_lines(cfg, now, accts)        red and calm lines above the Clips page (holds, Stop, the inbox)
    brief(cfg, now, acct)                the Today band's chips, in order
    cal_items(cfg, now, accts)           cards and stories for the calendar's second strip
    morning_lines(cfg, now)              hold sentences, then "Also: 6 comments to answer; 2 cards to sign."
    doctor(cfg)                          the doctor's social section (switches, parts that will not load, each part)
    run_job(cfg, "tick", quiet)          a timer's steps (registry.JOBS), each under its own lock
    on_posted / after_take               the poster's and collect's hooks into the parts
    act / cli                            Stop everything and Start again; factory.py social ...

The timers (launchd, timer.py): social tick every 5 minutes, studio every 10, checkpoints every 15, learn at 04:40,
ideas at 14:20, each `factory.py social <job> --quiet`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import board as B
from . import config as C
from . import registry as REG
from . import store
from . import switches as SW

PLAT_OF = {"facebook_reels": "facebook", "instagram_reels": "instagram", "youtube_shorts": "youtube"}
LEVELS = ("calm", "warn", "bad")
CAL_KINDS = ("card", "story")
CAL_STATES = ("posted", "scheduled", "review", "problem")
_KIDS: List[subprocess.Popen] = []
# the shared modules every part builds on (the plug lists name the parts themselves)
SEAM = ("store", "switches", "verdicts", "capabilities", "graph", "outward", "extras", "socialstore")


def _is_client(acct) -> bool:
    raw = getattr(acct, "raw", None)
    return isinstance(raw, dict) and str(raw.get("owner") or "self") == "client"


def accounts(cfg: C.Config) -> List[C.Account]:
    """The accounts the social layer works for: switched on, on this machine, never a client's."""
    return [a for a in cfg.accounts.values() if not _is_client(a)]


def _own(accts: Sequence) -> List:
    return [a for a in (accts or []) if not _is_client(a)]


def _by(cfg: C.Config, by: Optional[str] = None) -> str:
    return str(by or (cfg.approvers[0] if cfg.approvers else "factory.py"))


# ------------------------------------------------------------------------------------------------------ hooks
@dataclass
class CollectCtx:
    """What an AFTER_TAKE part sees when collect takes a clip into a slot: the slot is the live dict inside B.edit
    (the board is held), `extras` is the first comment, poll and trial a part adds (written once, after all ran)."""
    cfg: C.Config
    acct: C.Account
    day: date
    slot: dict
    folder: Optional[Path] = None             # the delivery folder the clip came from
    dest: Optional[Path] = None               # the drafts folder it was copied into
    extras: Dict[str, object] = field(default_factory=dict)


def after_take(cfg: C.Config, acct: C.Account, slot: dict, folder) -> None:
    """collect._take's hook: each AFTER_TAKE part (first comment defaults, the learner's and the coach's inputs) runs
    on the slot it just filled; whatever extras they add is written once (extras.write_locked). Nothing for a client,
    and nothing at all while every part is off."""
    if _is_client(acct):
        return None
    day = date.fromisoformat(str(slot["publish_at"])[:10])
    dest = slot.get("folder")
    ctx = CollectCtx(cfg=cfg, acct=acct, day=day, slot=slot, folder=Path(folder) if folder else None,
                     dest=Path(dest) if dest else None, extras={})
    REG.calls(REG.AFTER_TAKE, ctx)
    if ctx.extras:
        write = REG.fn("extras:write_locked")
        if write is not None:
            try:
                write(acct, day, slot, ctx.extras, "collect", "defaults")
            except (Exception, SystemExit) as e:  # noqa: BLE001  (a clip is never lost over its extras)
                REG._log_error("extras:write_locked", e)
    return None


def on_posted(cfg: C.Config, acct: C.Account, day: date, slot_key: str, outlet: str, rec: dict,
              now: Optional[datetime] = None) -> List[str]:
    """The poster's hook after a platform took a clip: the slot read fresh from its board, each FOLLOW_UPS part (the
    first comment, the story repost, a Part 2 rule) on it, then the platform's usage headers noted for the guard."""
    if _is_client(acct):
        return []
    now = now or cfg.now()
    try:
        bd = B.read(acct, day) or {}
    except (OSError, ValueError):
        return []
    slot = (bd.get("slots") or {}).get(slot_key)
    if not isinstance(slot, dict):
        return []
    out = [r for _, r in REG.calls(REG.FOLLOW_UPS, cfg, acct, day, slot, outlet, rec, now) if isinstance(r, str) and r]
    note = REG.fn("outward:note_usage")
    plat = PLAT_OF.get(outlet)
    if note is not None and plat:
        try:
            from . import direct as D
            last = getattr(D, "last_usage", None)
            words = note(cfg, acct, plat, last() if callable(last) else {}, now)
            if isinstance(words, str) and words:
                out.append(words)
        except (Exception, SystemExit) as e:  # noqa: BLE001  (posting never stops over the guard's note)
            REG._log_error("outward:note_usage", e)
    return out


# ------------------------------------------------------------------------------------------------------ the page
def card_parts(cfg: C.Config, acct: C.Account, day: date, s: dict, now: datetime) -> str:
    """The social folds under one draft card, in CARD_PARTS order ('' for a client, or when every part is off)."""
    if _is_client(acct):
        return ""
    return "".join(r for _, r in REG.calls(REG.CARD_PARTS, cfg, acct, day, s, now) if isinstance(r, str))


def health_lines(cfg: C.Config, now: datetime, accts: Sequence[C.Account]) -> List[Tuple[str, str]]:
    """(level, words) per part with something to say on the Clips page; "bad" draws the red line."""
    out: List[Tuple[str, str]] = []
    for _, r in REG.calls(REG.HEALTH, cfg, now, _own(accts)):
        if isinstance(r, (list, tuple)) and len(r) == 2 and isinstance(r[1], str) and r[1].strip():
            out.append((str(r[0] or ""), r[1]))
    return out


def brief(cfg: C.Config, now: datetime, acct: str = "all") -> List[dict]:
    """The Today band: every part's summary chips, by their order. [] (no band) when no part has anything."""
    from . import socialui as SX
    chips: List[dict] = []
    for spec, r in REG.calls(REG.SUMMARIES, cfg, now, SX.own_accounts(cfg, acct)):
        for c in r if isinstance(r, list) else []:
            if not isinstance(c, dict) or not str(c.get("words") or "").strip():
                continue
            order = c.get("order")
            chips.append({"key": str(c.get("key") or spec), "words": str(c["words"]), "href": str(c.get("href") or ""),
                          "level": c.get("level") if c.get("level") in LEVELS else "calm",
                          "order": order if isinstance(order, (int, float)) and not isinstance(order, bool) else 99})
    chips.sort(key=lambda c: c["order"])
    return chips


def cal_items(cfg: C.Config, now: datetime, accts: Sequence[C.Account]) -> Dict[str, Dict[str, List[dict]]]:
    """{"YYYY-MM-DD": {slug: [{"k": "card|story", "s": "posted|scheduled|review|problem"}]}} from every part."""
    out: Dict[str, Dict[str, List[dict]]] = {}
    for _, r in REG.calls(REG.CAL_ITEMS, cfg, now, _own(accts)):
        if not isinstance(r, dict):
            continue
        for iso, per in r.items():
            if not isinstance(per, dict):
                continue
            for slug, items in per.items():
                good = [{"k": i["k"], "s": i["s"]} for i in (items if isinstance(items, list) else [])
                        if isinstance(i, dict) and i.get("k") in CAL_KINDS and i.get("s") in CAL_STATES]
                if good:
                    out.setdefault(str(iso), {}).setdefault(str(slug), []).extend(good)
    return out


def morning_lines(cfg: C.Config, now: Optional[datetime] = None) -> List[str]:
    """The 06:30 notice's social lines, after the accounts': each hold in a sentence, then one "Also: ..." line."""
    now = now or cfg.now()
    out: List[str] = []
    for _, r in REG.calls(REG.MORNING_HOLDS, cfg, now):
        out += [str(x) for x in (r if isinstance(r, list) else [r]) if isinstance(x, str) and x.strip()]
    frags = [r.strip().rstrip(".") for _, r in REG.calls(REG.MORNING, cfg, now) if isinstance(r, str) and r.strip()]
    if frags:
        out.append("Also: " + "; ".join(frags) + ".")
    return out


def _extras_problems(cfg: C.Config, now: datetime, accts: Sequence[C.Account]) -> List[Tuple[C.Account, str, str]]:
    """Signed slots of today and tomorrow, not yet at their time, whose first comment or poll file is gone or was
    changed after it was signed: (account, "YYYY-MM-DD HH:MM", what is wrong)."""
    out: List[Tuple[C.Account, str, str]] = []
    if now.tzinfo is None:
        now = now.replace(tzinfo=cfg.tz())
    today = now.astimezone(cfg.tz()).date()
    for a in _own(accts):
        for d in (today, today + timedelta(days=1)):
            try:
                bd = B.read(a, d) or {}
            except (OSError, ValueError):
                continue
            slots = bd.get("slots") if isinstance(bd, dict) else None
            for k, s in sorted((slots if isinstance(slots, dict) else {}).items()):
                x = s.get("extras") if isinstance(s, dict) else None
                if not isinstance(x, dict) or s.get("status") not in B.SIGNED or not x.get("path"):
                    continue
                try:
                    if datetime.fromisoformat(str(s["publish_at"])) <= now:
                        continue
                except (KeyError, TypeError, ValueError):
                    pass
                p = Path(str(x["path"]))
                if not p.is_file():
                    out.append((a, f"{d} {k}", "its first comment or poll file is gone"))
                elif B.sha256_file(p, cached=True) != x.get("sha256"):
                    out.append((a, f"{d} {k}", "its first comment or poll was changed after it was signed"))
    return out


def extras_health(cfg: C.Config, now: datetime, accts: Sequence[C.Account]) -> Tuple[str, str]:
    """A red line before a signed clip's time when its extras file went away or changed: the poster will refuse it."""
    bad = _extras_problems(cfg, now, accts)
    if not bad:
        return ("", "")
    words = "; ".join(f"{a.name} {when}: {what}" for a, when, what in bad[:3])
    more = f" (and {len(bad) - 3} more)" if len(bad) > 3 else ""
    return ("bad", f"{words}{more}. It will not post: Pull it, then approve it again.")


# ------------------------------------------------------------------------------------------------------ doctor
def _part_names() -> List[str]:
    """Every module the plug board names, in a stable order."""
    names: List[str] = []

    def add(n: str) -> None:
        if n and n not in names:
            names.append(n)
    for p in REG.PAGES:
        add(p.module)
    for spec in (list(REG.SUBROUTES.values()) + list(REG.CARD_PARTS) + list(REG.HEALTH) + list(REG.SUMMARIES)
                 + list(REG.MORNING_HOLDS) + list(REG.MORNING) + list(REG.AFTER_TAKE) + list(REG.FOLLOW_UPS)
                 + list(REG.AFTER_POLL) + list(REG.CAL_ITEMS) + list(REG.SOCIAL_ACTIONS.values())
                 + [s for steps in REG.JOBS.values() for s in steps]):
        add(spec.partition(":")[0])
    for n in list(REG.CARD_CSS) + list(REG.DOCTORS) + list(REG.COMMANDS.values()) + list(REG.EXECUTORS.values()):
        add(n)
    for _, n in REG.ACT_PREFIXES:
        add(n)
    for n in SEAM:
        add(n)
    return names


def doctor(cfg: C.Config, now: Optional[datetime] = None) -> List[Tuple[Optional[bool], str, str]]:
    """The doctor's social section: which parts are built, each account's switches, a NO for a part that will not
    load, each part's own lines, and a NO for a signed clip whose extras file went away. Read only, no network."""
    now = now or cfg.now()
    rows: List[Tuple[Optional[bool], str, str]] = []
    names = _part_names()
    built = [n for n in names if REG.present(n)]
    waiting = [n for n in names if n not in built and n not in REG.BROKEN]
    rows.append((None, "parts built here: " + (", ".join(built) or "none")
                 + ("; not built yet: " + ", ".join(waiting) if waiting else ""), ""))
    for name in REG.DOCTORS:
        if name == "social":
            continue
        mod = REG.load(name)
        f = getattr(mod, "doctor", None) if mod is not None else None
        if not callable(f):
            continue
        try:
            got = f(cfg, now)
        except (Exception, SystemExit) as e:  # noqa: BLE001  (doctor never stops over one part, nor its refusal)
            got = [(False, f"{name}: its check failed ({type(e).__name__}: {e})", f"look at clipfactory/{name}.py")]
        for r in got or []:
            if isinstance(r, (list, tuple)) and len(r) == 3:
                rows.append((r[0] if r[0] is None else bool(r[0]), str(r[1]), str(r[2] or "")))
        if name == "switches":                   # what will not load comes right after the switches
            for n, err in sorted(REG.BROKEN.items()):
                rows.append((False, f"clipfactory/{n}.py will not load: {err} (its tab, card part and command are "
                                    f"hidden)", f"fix clipfactory/{n}.py"))
    try:
        for a, when, what in _extras_problems(cfg, now, accounts(cfg)):
            rows.append((False, f"{a.name} {when}: {what}; it will not post",
                         f"factory.py pull {a.slug} --date {when[:10]} --slots {when[11:]} --by NAME, then approve "
                         f"it again on the review page"))
    except (Exception, SystemExit) as e:  # noqa: BLE001  (doctor never raises)
        rows.append((False, f"first comment and poll files: their check failed ({type(e).__name__}: {e})",
                     "look at today's and tomorrow's boards under state/<account>/"))
    return rows


# ------------------------------------------------------------------------------------------------------ jobs
def run_job(cfg: C.Config, name: str, quiet: bool = True, now: Optional[datetime] = None) -> List[str]:
    """One timer's steps (registry.JOBS[name]) in order, each under its own state/social/locks/<step>.lock (a step
    still running from the last tick is skipped, never queued) and its own try. What they said; how each went is in
    state/social/tick.json."""
    steps = REG.JOBS.get(name)
    if steps is None:
        raise SystemExit(f"{name}: not a social job; the jobs are: {', '.join(REG.JOBS)}")
    now = now or cfg.now()
    lines: List[str] = []
    done: List[List[str]] = []
    for spec in steps:
        f = REG.fn(spec)
        if f is None:
            mod = spec.partition(":")[0]
            done.append([spec, f"will not load: {REG.BROKEN[mod]}" if mod in REG.BROKEN else "not built yet"])
            continue
        with store.try_lock(spec.replace(":", "-")) as got:
            if not got:
                done.append([spec, "busy"])
                continue
            try:
                said = f(cfg, now=now, quiet=quiet)
                lines += [str(x) for x in (said if isinstance(said, list) else []) if x]
                done.append([spec, "ok"])
            except (Exception, SystemExit) as e:  # noqa: BLE001  (one step never stops the next)
                REG._log_error(spec, e)
                done.append([spec, f"error: {type(e).__name__}: {e}"[:300]])
                lines.append(f"{spec}: {type(e).__name__}: {e}")
    try:
        with store.edit(store.path("social", "tick.json"), dict) as t:
            t[name] = {"at": now.isoformat(timespec="seconds"), "steps": done}
    except (OSError, SystemExit):
        pass
    if not quiet:
        lines += [f"  {spec}: {how}" for spec, how in done if how != "ok"]
    return lines


def kick(cfg: C.Config) -> str:
    """Start `factory.py social send` now, in the background, so a click's reply goes out without waiting for the
    5-minute tick. Never from a test, an offline run or a copy of the state (net.why_not)."""
    from . import net
    why = net.why_not()
    if why:
        return f"sending not started ({why})"
    log = C.STATE / "logs" / "social-send.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLIP_FACTORY_LOG_STAMP="1", CLIP_FACTORY_CONFIG=str(cfg.path), CLIP_FACTORY_STATE=str(C.STATE))
    _KIDS[:] = [p for p in _KIDS if p.poll() is None]
    with open(log, "a") as out:
        _KIDS.append(subprocess.Popen([sys.executable, str(C.ROOT / "factory.py"), "social", "send"], cwd=str(C.ROOT),
                                      env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                      start_new_session=True))
    return "sending started"


# ------------------------------------------------------------------------------------------------------ clicks
def act(cfg: C.Config, body: dict, page: bool = False, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """social-stop (Stop everything: every comment, reply, message, story and card on every account; signed clips
    keep posting) and social-start, from the Log tab's header. Only a click on the page."""
    from . import socialui as SX
    op = str(body.get("op") or "")
    by = _by(cfg, body.get("by"))
    if op == "social-stop":
        SX.need_page(page, "stop everything")
        f = REG.fn("outward:stop")
        if f is None:
            raise SystemExit("Stop everything is not built yet (clipfactory/outward.py)")
        return True, str(f(cfg, by, str(body.get("why") or "")))
    if op == "social-start":
        SX.need_page(page, "start everything again")
        f = REG.fn("outward:start")
        if f is None:
            raise SystemExit("Start again is not built yet (clipfactory/outward.py)")
        return True, str(f(cfg, by))
    raise SystemExit(f"{op or 'that'}: not a click the social layer knows")


# ------------------------------------------------------------------------------------------------------ the command
def _account(cfg: C.Config, slug: str) -> C.Account:
    a = cfg.account(slug)
    if _is_client(a):
        raise SystemExit(f"{a.name} is a client's account: the social layer is for the owner's own accounts")
    return a


def _holds(slug: str, now: datetime) -> List[Tuple[str, dict]]:
    """The holds still running for an account (state/<slug>/outward/holds.json), read straight from the file."""
    raw = store.read(store.path(slug, "outward", "holds.json"), {})
    holds = raw.get("holds") if isinstance(raw, dict) else None
    out = []
    for k, h in sorted((holds or {}).items()) if isinstance(holds, dict) else []:
        if not isinstance(h, dict):
            continue
        until = store._when(h.get("until"))
        if until is not None and until <= now:
            continue
        out.append((str(k), h))
    return out


def _hold_line(cfg: C.Config, key: str, h: dict) -> str:
    until = store._when(h.get("until"))
    when = f" until {until.astimezone(cfg.tz()).strftime('%Y-%m-%d %H:%M')}" if until is not None else ""
    why = str(h.get("why") or "").strip().rstrip(".")
    free = "" if h.get("releasable", True) else " (cannot be released early)"
    return f"{key}{when}{': ' + why if why else ''}{free}"


def _status(cfg: C.Config, slug: Optional[str]) -> List[str]:
    now = cfg.now()
    accts = [_account(cfg, slug)] if slug else accounts(cfg)
    out: List[str] = []
    stopped = REG.fn("outward:stopped")
    try:
        words = stopped(cfg) if stopped is not None else ""
    except Exception as e:  # noqa: BLE001
        words = f"cannot tell ({type(e).__name__})"
    out.append(f"Stop everything: {words or 'off'}")
    for a in accts:
        out.append(f"{a.name} ({a.slug})")
        out.append(f"  switches: {SW._on_words(a)}")
        wu = SW.warmup_until(cfg, a)
        if wu is not None:
            out.append(f"  warm-up: automated replies practise until {wu}")
        elif not SW.settings(a)["created"]:
            out.append(f"  warm-up: no creation date yet (factory.py social created {a.slug} YYYY-MM-DD)")
        else:
            out.append("  warm-up: waits for the factory's first post here")
        holds = _holds(a.slug, now.astimezone(timezone.utc))
        out.append("  holds: " + ("; ".join(_hold_line(cfg, k, h) for k, h in holds) if holds else "none"))
        for p in SW.problems(a):
            out.append(f"  NO settings: {p}")
    tick = store.read(store.path("social", "tick.json"), {})
    for job in REG.JOBS:
        t = tick.get(job) if isinstance(tick, dict) else None
        if isinstance(t, dict):
            bad = [f"{s}: {how}" for s, how in t.get("steps") or [] if how not in ("ok", "not built yet")]
            out.append(f"job {job}: last ran {t.get('at')}" + (" (" + "; ".join(bad) + ")" if bad else ""))
        else:
            out.append(f"job {job}: has not run here")
    names = _part_names()
    built = [n for n in names if REG.present(n)]
    out.append("parts built: " + (", ".join(built) or "none"))
    waiting = [n for n in names if n not in built]
    if waiting:
        out.append("not built yet: " + ", ".join(f"{n} (will not load)" if n in REG.BROKEN else n for n in waiting))
    return out


def _value(s: str):
    try:
        return json.loads(s)
    except ValueError:
        return s


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.py social", description="the social layer (clipfactory/social.py)")
    sub = ap.add_subparsers(dest="action", required=True)
    for job in list(REG.JOBS) + ["send"]:
        q = sub.add_parser(job, help="the timer's job" if job != "send" else "send what is queued now")
        q.add_argument("--quiet", action="store_true", help="print only what happened")
    q = sub.add_parser("status", help="each account's switches, holds and warm-up; the jobs; the parts built")
    q.add_argument("account", nargs="?")
    q = sub.add_parser("stop", help="Stop everything: no comment, reply, message, story or card goes out")
    q.add_argument("--why", default="")
    q.add_argument("--by")
    q = sub.add_parser("start", help="start everything again after a stop")
    q.add_argument("--by")
    q = sub.add_parser("set", help="one switch or setting: set <account> <key> <value> (a setting's value as JSON)")
    q.add_argument("account")
    q.add_argument("key")
    q.add_argument("value")
    q.add_argument("--by")
    q = sub.add_parser("on", help="switch a part on (or to practice, or testers)")
    q.add_argument("account")
    q.add_argument("key")
    g = q.add_mutually_exclusive_group()
    g.add_argument("--practice", action="store_true")
    g.add_argument("--testers", action="store_true")
    q.add_argument("--by")
    q = sub.add_parser("off", help="switch a part off")
    q.add_argument("account")
    q.add_argument("key")
    q.add_argument("--by")
    q = sub.add_parser("created", help="the day the account was made (the warm-up clock)")
    q.add_argument("account")
    q.add_argument("day", help="YYYY-MM-DD")
    q.add_argument("--by")
    q = sub.add_parser("tester", help="a person with a role on the Meta app: testers mode sends only to them")
    q.add_argument("account")
    q.add_argument("--platform", required=True, choices=list(SW.PLATFORMS))
    q.add_argument("--id", required=True)
    q.add_argument("--name", default="")
    q.add_argument("--remove", action="store_true")
    q.add_argument("--by")
    q = sub.add_parser("probe", help="ask a platform what this account may do (by hand only: it reaches the network)")
    q.add_argument("account")
    q.add_argument("what")
    q = sub.add_parser("holds", help="the holds still running")
    q.add_argument("account", nargs="?")
    q = sub.add_parser("release", help="end a hold early (only one Meta or YouTube allows)")
    q.add_argument("account")
    q.add_argument("key", help="platform|family, as `holds` shows it")
    q.add_argument("--by", required=True)
    q = sub.add_parser("forget", help="remove a person from the log and the inbox")
    q.add_argument("account")
    q.add_argument("--person", required=True, help="platform:id")
    for name in REG.SOCIAL_ACTIONS:           # listed for --help; cli() hands their words on before parsing
        q = sub.add_parser(name, help="move an account's social state (socialmove.py)")
        q.add_argument("rest", nargs=argparse.REMAINDER)
    sub.add_parser("doctor", help="the doctor's social section alone")
    return ap


def _need(spec: str):
    f = REG.fn(spec)
    if f is None:
        mod = spec.partition(":")[0]
        if mod in REG.BROKEN:
            raise SystemExit(f"clipfactory/{mod}.py will not load: {REG.BROKEN[mod]}")
        raise SystemExit(f"{mod}: not built yet")
    return f


def _show(lines) -> None:
    for ln in lines or []:
        print(ln)


def cli(cfg: C.Config, argv: List[str]) -> None:
    """factory.py social <action> ... (the timers run the jobs with --quiet: nothing printed when nothing happened).
    export and import parse their own words (socialmove): argparse's REMAINDER refuses a first word that starts with
    "--", so `social import --from DIR` is handed on whole, never through this parser."""
    argv = list(argv)
    if argv and argv[0] in REG.SOCIAL_ACTIONS:
        _need(REG.SOCIAL_ACTIONS[argv[0]])(cfg, argv[1:])
        return
    a = _parser().parse_args(argv)
    act_ = a.action
    if act_ in REG.JOBS:
        _show(run_job(cfg, act_, quiet=a.quiet))
    elif act_ == "send":
        f = REG.fn("outward:drain")
        if f is None:
            if not a.quiet:
                print("send: not built yet (clipfactory/outward.py)")
            return
        _show(f(cfg))
    elif act_ == "status":
        _show(_status(cfg, a.account))
    elif act_ == "stop":
        print(_need("outward:stop")(cfg, _by(cfg, a.by), a.why))
    elif act_ == "start":
        print(_need("outward:start")(cfg, _by(cfg, a.by)))
    elif act_ == "set":
        acct = _account(cfg, a.account)
        if a.key in SW.MODES:
            print(SW.set_mode(cfg, acct, a.key, a.value, _by(cfg, a.by)))
        else:
            print(SW.set_setting(cfg, acct, a.key, _value(a.value), _by(cfg, a.by)))
    elif act_ == "on":
        value = "practice" if a.practice else "testers" if a.testers else "on"
        print(SW.set_mode(cfg, _account(cfg, a.account), a.key, value, _by(cfg, a.by)))
    elif act_ == "off":
        print(SW.set_mode(cfg, _account(cfg, a.account), a.key, SW.OFF, _by(cfg, a.by)))
    elif act_ == "created":
        print(SW.set_setting(cfg, _account(cfg, a.account), "created", a.day, _by(cfg, a.by)))
    elif act_ == "tester":
        acct = _account(cfg, a.account)
        if a.remove:
            print(SW.remove_tester(cfg, acct, a.platform, a.id, _by(cfg, a.by)))
        else:
            print(SW.add_tester(cfg, acct, a.platform, a.id, a.name, _by(cfg, a.by)))
    elif act_ == "probe":
        _show(_lines(_need("capabilities:probe")(cfg, _account(cfg, a.account), a.what)))
    elif act_ == "holds":
        now = datetime.now(timezone.utc)
        for acct in ([_account(cfg, a.account)] if a.account else accounts(cfg)):
            holds = _holds(acct.slug, now)
            print(f"{acct.name}: {len(holds) or 'no'} hold{'' if len(holds) == 1 else 's'}")
            for k, h in holds:
                print(f"  {_hold_line(cfg, k, h)}")
    elif act_ == "release":
        print(_need("outward:release")(cfg, _account(cfg, a.account), a.key, a.by))
    elif act_ == "forget":
        acct = _account(cfg, a.account)
        f = REG.fn("socialstore:forget")
        n = f(acct, a.person, _by(cfg), cfg=cfg) if f is not None else _need("outward:forget")(cfg, acct, a.person)
        print(f"{acct.name}: {a.person} forgotten ({n} records)")
    elif act_ == "doctor":
        for good, text, fix in doctor(cfg):
            print(f"  {'-- ' if good is None else 'ok ' if good else 'NO '} {text}"
                  + (f"\n        fix: {fix}" if fix and good is False else ""))


def _lines(v) -> List[str]:
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else ([json.dumps(v)] if v is not None else [])
