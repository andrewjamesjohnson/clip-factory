"""Load config/accounts.json into plain objects. Every path is expanded here and nowhere else."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from . import livestate

ROOT = Path(__file__).resolve().parent.parent
# A command with no date works on the next posting day: tomorrow, except before this hour (local), when today's slots
# have not gone out yet and today is the day a night run that passed midnight is still filling (audit LDG-2). 7 since
# the night shift (2026-10-19): the shift fills today's boards until 06:15 (the 06:15 Politics collect, the 06:18
# bench fill, the 06:30 notice) and the first slot posts at 07:00; at 07:00 the next posting day is tomorrow.
POSTING_DAY_STARTS = 7
CONFIG = Path(os.environ.get("CLIP_FACTORY_CONFIG") or (ROOT / "config" / "accounts.json"))
# never the live folder in a test or an offline run, whichever test file imported clipfactory first (livestate.py)
STATE = livestate.safe(Path(os.environ.get("CLIP_FACTORY_STATE") or (ROOT / "state")))
livestate.daytime_queue()       # a test run never meets the render queue's night limits by the real clock (2026-10-20)
# Which machine this checkout runs on (2026-10-19: a second computer is coming for the new GTA accounts). An account
# runs on the machine its accounts.json "machine" names, "main" when it names none; every other machine leaves it alone.
MACHINE_ENV = "CLIP_FACTORY_MACHINE"
MAIN = "main"
# 2026-10-29 (Andrew: "get all the four accounts loaded up into Clipper view... so we can start posting"): the maker of
# an account nothing cuts for. It is on Clip Review and posts what is dropped in its Clip Drop folder, once signed.
HAND = "hand"


# an @name in a profile address: youtube.com/@x, tiktok.com/@x, instagram.com/x, and a Facebook Page's address by its
# username or its Page id (facebook.com/x, facebook.com/1000000000000001, facebook.com/profile.php?id=1000000000000001)
HANDLE_IN_URL = re.compile(r"(?:/@|(?:instagram|facebook)\.com/(?:profile\.php\?id=)?)([A-Za-z0-9._-]+)", re.I)
# the first part of an Instagram or Facebook address that is a post, a list or a page of the site, never a profile
NOT_A_PROFILE = {"p", "reel", "reels", "tv", "stories", "explore", "watch", "videos", "share", "pages", "groups",
                 "events", "people", "photo", "photo.php", "story.php", "permalink.php", "profile.php", "login"}


def handles_in(values) -> set:
    """The @names (lowercase, no @) that these strings name: a handle as typed ("@x") or inside a profile address.
    Lowercase both ways, so @Stunts and @stunts are one name. A Facebook Page counts by the name or
    the Page id its address carries."""
    out = set()
    for v in values:
        if not isinstance(v, str) or not v.strip():
            continue
        v = v.strip()
        if v.startswith("@"):
            out.add(v[1:].lower())
        out |= {m.lower() for m in HANDLE_IN_URL.findall(v)} - NOT_A_PROFILE
    out.discard("")
    return out


def possessive(name) -> str:
    """An account's name as an owner in a generated line: "Gaming's", but "Stunts'" (a name ending in s takes
    the apostrophe alone, as the docs write it; review 10-19 found "Stunts's" in connect's refusals)."""
    name = str(name)
    return name + ("'" if name[-1:] in ("s", "S") else "'s")


def this_machine() -> str:
    """CLIP_FACTORY_MACHINE, else "main" (the Mac the factory began on)."""
    return (os.environ.get(MACHINE_ENV) or "").strip() or MAIN


def expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p)))


@dataclass
class Account:
    slug: str
    name: str
    handle: str
    maker: str
    skill_dir: Path
    account_dir: Path
    delivered: List[str]
    slots: List[str]
    daily_target: int
    min_seconds: float
    max_seconds: float
    formats: List[str]
    clips_per_source: int
    platforms: List[str]
    outlets: Dict[str, dict]
    raw: dict = field(default_factory=dict)
    slot_formats: List[str] = field(default_factory=list)   # what a slot may be set to (formats = the rotation)
    # 2026-10-19 (two more GTA accounts): "active": false keeps a block in the file while nothing is made, scheduled
    # or posted for it (it is set up first, switched on once its platform accounts exist); "machine" is the computer
    # that runs it; "kit" names its brand kit in the skill (assets/brand/<kit>, presets/<kit>.json); "short" is its
    # name on Clip Review's calendar and in the morning notice; "job_tag" goes into its skill jobs' names
    active: bool = True
    # 2026-10-21 (Politics New Looks 10.21, Andrew: "The spare post times are OK"): slots 16 to 20, kept apart from
    # `slots` so the fifteen and daily_target never move. A spare slot is on every new board, marked "spare", and is
    # filled only once every base slot holds a clip, with a clip that passed every check; an open one is never
    # missing (board.base_slots, collect, bench, shift).
    spare_slots: List[str] = field(default_factory=list)
    machine: str = MAIN
    kit: str = ""
    short: str = ""
    job_tag: str = ""

    def delivered_dirs(self, day: date) -> List[Path]:
        subs = {"YYYY": f"{day.year:04d}", "MM": f"{day.month:02d}", "DD": f"{day.day:02d}"}
        return [expand(t.format(**subs)) for t in self.delivered]

    def active_slots(self) -> List[str]:
        return self.slots[: max(0, min(self.daily_target, len(self.slots)))]

    def slots_for(self, day: date) -> List[str]:
        """The slots a NEW board for `day` gets: the first "volume" rule whose dates hold the day, else active_slots().
        A rule is {"from": ISO or absent, "until": ISO or absent, "slots": [...]}; its slots are a subset of "slots"
        (2026-10-20, Lab: 3 a day before GTA 6, 10 a day from the launch, by a date in config, never a code
        change). An account without "volume" is unchanged."""
        rule = self.volume_rule(day)
        return [normal_slot(s) for s in rule["slots"]] if rule else self.active_slots()

    def spare_slots_for(self, day: date) -> List[str]:
        """The spare slots a NEW board for `day` gets: none on a day a "volume" rule holds (the rule names the
        day's slots), else spare_slots, minus any that is also a base slot."""
        if self.volume_rule(day):
            return []
        base = set(self.slots_for(day))
        return [s for s in self.spare_slots if s not in base]

    def volume_rule(self, day: date) -> Optional[dict]:
        """The "volume" rule in force on `day` (None: no rule holds the day, or the account has none)."""
        for r in (self.raw or {}).get("volume") or []:
            lo, hi = r.get("from"), r.get("until")
            if (not lo or day >= date.fromisoformat(lo)) and (not hi or day <= date.fromisoformat(hi)):
                return r
        return None

    def handles(self) -> set:
        """Its own @names, lowercase: "handle" and each profile its "links" name. Both count: Politics's handle is
        @capitalpoliticshq and its YouTube @politics; Gaming's TikTok is @gamingp. Its Facebook Page counts
        by the id or the name in its link (Stunts': 1000000000000001)."""
        return handles_in([self.handle] + list(((self.raw or {}).get("links") or {}).values()))

    def fed_by_hand(self) -> bool:
        """Nothing cuts for it by itself (2026-10-29): its maker is "hand" (Cars, until its own maker is
        built), or its block says "night": false (the Mac's night is full, the render queue busy 7.1 to 7.8 of its
        9.25 hours 10-26 to 10-28) and it has no day shift. No night, retry or scout agent; its slots take what is
        dropped in its Clip Drop folder, or a make run by hand. "night" reads as "active" does (a "false" typed by
        hand is false)."""
        raw = self.raw or {}
        return self.maker == HAND or ("night" in raw and not _on(raw["night"]) and self.day_shift() is None)

    def day_shift(self) -> Optional[Tuple[str, str]]:
        """(at, until) when it cuts in the day instead of at night (2026-10-29, Andrew: "Yes, build it", Stunts:
        the night is full, the day's render queue busy 1.2 to 2.2 of 14 hours): its "day_shift" block, beside
        "night": false. At `at` a run makes the next posting day's board, and its worker stops itself at `until`.
        None when there is none or it cannot be used (day_shift_problem says why; doctor says it)."""
        return None if day_shift_problem(self) else _day_shift_times(self.raw)


DAY_SHIFT_FROM = "07:00"      # a run with no date makes the next posting day from POSTING_DAY_STARTS on
DAY_SHIFT_LAST = "20:30"      # done before the 21:00 night (shift.START: the Politics's and Gaming's)


def _day_shift_times(raw: Optional[dict]) -> Optional[Tuple[str, str]]:
    ds = (raw or {}).get("day_shift")
    if not isinstance(ds, dict):
        return None
    return normal_slot(str(ds.get("at", ""))), normal_slot(str(ds.get("until", "")))


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def day_shift_problem(acct: "Account") -> str:
    """'' when the account has no "day_shift" or a usable one; else the one plain line that says why it is not used.
    "at" from 07:00 (from then a run with no date makes the next posting day); "until" from 13:00 to 20:30 (its
    stops, an hour before it at the earliest, stay after noon, so they are read on the day the shift runs, and it is
    done before the 21:00 night); "until" at least two hours after "at"; on a GTA account with "night": false."""
    raw = acct.raw or {}
    if "day_shift" not in raw:
        return ""
    if acct.maker != "gta":
        return f'day_shift is for an account the gta maker cuts; {acct.name}\'s maker is "{acct.maker}"'
    if "night" not in raw or _on(raw["night"]):
        return 'day_shift needs "night": false beside it (one shift a day: the day or the night)'
    try:
        times = _day_shift_times(raw)
    except SystemExit as e:
        return f"day_shift: {e}"
    if times is None:
        return 'day_shift is {"at": "HH:MM", "until": "HH:MM"}'
    at, until = times
    if _minutes(at) < _minutes(DAY_SHIFT_FROM):
        return f"day_shift at {at} is before {DAY_SHIFT_FROM} (then a run makes today, not the next posting day)"
    if not 13 * 60 <= _minutes(until) <= _minutes(DAY_SHIFT_LAST):
        return f"day_shift until {until} is not between 13:00 and {DAY_SHIFT_LAST}"
    if _minutes(until) - _minutes(at) < 120:
        return f"day_shift {at} to {until} is under two hours"
    return ""


@dataclass
class Config:
    timezone: str
    drafts_root: Path
    review_port: int
    approvers: List[str]
    accounts: Dict[str, Account]      # the accounts that work here: switched on, and on this machine
    path: Path
    inbox_root: Path = field(default_factory=lambda: expand("~/Desktop/Clip Drop"))
    all_accounts: Dict[str, Account] = field(default_factory=dict)   # every block in the file, switched on or not
    machine: str = MAIN

    def account(self, slug: str) -> Account:
        """Any account in the file, switched on or not: connect, doctor and status work before switch-on. A command
        that makes or schedules asks why_off() first."""
        acct = self.accounts.get(slug) or self.all_accounts.get(slug)
        if acct is None:
            raise SystemExit(f"no account {slug!r}; the accounts are: {', '.join(self.all_accounts or self.accounts)}")
        return acct

    def why_off(self, acct: Account) -> str:
        """'' when the account works here; else the one plain line that says why not (switched off, or another
        machine's)."""
        if not acct.active:
            return (f'{acct.name} is switched off ("active": false in config/accounts.json): nothing is made or '
                    f'scheduled for it. To switch it on: set "active": true, then run factory.py timer install.')
        if acct.machine != self.machine:
            return (f'{acct.name} runs on the machine "{acct.machine}" (accounts.json "machine"); this one is '
                    f'"{self.machine}" ({MACHINE_ENV}): nothing is made or scheduled for it here.')
        return ""

    def tz(self):
        return ZoneInfo(self.timezone) if ZoneInfo else None

    def now(self) -> datetime:
        return datetime.now(self.tz())

    def tomorrow(self) -> date:
        return (self.now() + timedelta(days=1)).date()

    def posting_day(self) -> date:
        """The day a command with no date means: tomorrow, or today before POSTING_DAY_STARTS (07:00)."""
        now = self.now()
        return now.date() if now.hour < POSTING_DAY_STARTS else (now + timedelta(days=1)).date()

    def slot_time(self, day: date, slot: str) -> datetime:
        h, m = (int(x) for x in slot.split(":"))
        return datetime(day.year, day.month, day.day, h, m, tzinfo=self.tz())


def _on(v) -> bool:
    """"active": anything but false (or a "false"/"no"/"off"/0 typed by hand) is on; no key is on."""
    return not (v is False or str(v).strip().lower() in ("false", "no", "off", "0"))


def check_volume(slug: str, a: dict) -> None:
    """An account's "volume" rules, checked as a bad slot is: one plain SystemExit line. Each rule has ISO dates
    ("from", "until", either may be left out), slots inside the account's "slots", and no two rules cover one day."""
    rules = a.get("volume")
    if rules is None:
        return
    where = f'config/accounts.json {slug} "volume"'
    if not isinstance(rules, list):
        raise SystemExit(f"{where}: a list of rules, each {{\"from\", \"until\", \"slots\"}}")
    listed = {normal_slot(s) for s in a.get("slots") or []}
    spans = []
    for i, r in enumerate(rules, start=1):
        if not isinstance(r, dict):
            raise SystemExit(f"{where} rule {i}: an object with \"from\", \"until\" and \"slots\"")
        try:
            lo = date.fromisoformat(r["from"]) if r.get("from") else date.min
            hi = date.fromisoformat(r["until"]) if r.get("until") else date.max
        except (TypeError, ValueError):
            raise SystemExit(f"{where} rule {i}: \"from\" and \"until\" are dates, YYYY-MM-DD") from None
        if lo > hi:
            raise SystemExit(f"{where} rule {i}: \"from\" {r.get('from')} is after \"until\" {r.get('until')}")
        slots = r.get("slots")
        if not isinstance(slots, list) or not slots:
            raise SystemExit(f"{where} rule {i}: \"slots\" is a list of the day's slot times")
        outside = [s for s in (normal_slot(str(x)) for x in slots) if s not in listed]
        if outside:
            raise SystemExit(f"{where} rule {i}: {', '.join(outside)} not in the account's \"slots\"")
        for j, (lo2, hi2) in enumerate(spans, start=1):
            if lo <= hi2 and lo2 <= hi:
                raise SystemExit(f"{where}: rules {j} and {i} both cover {max(lo, lo2)}: one rule a day")
        spans.append((lo, hi))


def load(path: Optional[Path] = None) -> Config:
    path = Path(path or CONFIG)
    data = json.loads(path.read_text())
    machine = this_machine()
    accounts = {}
    for slug, a in data["accounts"].items():
        check_volume(slug, a)
        secs = a.get("seconds", {})
        accounts[slug] = Account(
            slug=slug,
            name=a["name"],
            handle=a.get("handle", ""),
            maker=a["maker"],
            skill_dir=expand(a["skill_dir"]),
            account_dir=expand(a["account_dir"]),
            delivered=list(a.get("delivered", [])),
            slots=[normal_slot(s) for s in a["slots"]],
            daily_target=int(a.get("daily_target", len(a["slots"]))),
            min_seconds=float(secs.get("min", 0)),
            max_seconds=float(secs.get("max", 60)),
            formats=list(a.get("formats", [])),
            clips_per_source=int(a.get("clips_per_source", 3)),
            platforms=list(a.get("platforms", [])),
            outlets=dict(a.get("outlets", {})),
            raw=a,
            slot_formats=list(a.get("slot_formats") or a.get("formats", [])),
            active=_on(a.get("active", True)),
            spare_slots=[normal_slot(s) for s in a.get("spare_slots") or []],
            machine=str(a.get("machine") or MAIN).strip() or MAIN,
            kit=str(a.get("kit") or ("gta" if a["maker"] == "gta" else "")),
            short=str(a.get("short") or ""),
            job_tag=str(a.get("job_tag") or ""),
        )
    # every loop over cfg.accounts (the boards, the review page, posting, the bench, the notice) skips an account that
    # is switched off or runs on another machine, with no other change
    here = {s: x for s, x in accounts.items() if x.active and x.machine == machine}
    return Config(
        timezone=data.get("timezone", "America/Denver"),
        drafts_root=expand(data.get("drafts_root", "~/Desktop/Clip Drafts")),
        review_port=int(data.get("review_port", 8781)),
        approvers=list(data.get("approvers", [])),
        accounts=here,
        path=path,
        inbox_root=expand(data.get("inbox_root", "~/Desktop/Clip Drop")),
        all_accounts=accounts,
        machine=machine,
    )


def normal_slot(s: str) -> str:
    """'0500' | '5:00' | '05:00' -> '05:00'."""
    t = s.replace(":", "").strip()
    if not t.isdigit() or len(t) not in (3, 4):
        raise SystemExit(f"slot {s!r}: use HH:MM")
    t = t.zfill(4)
    h, m = int(t[:2]), int(t[2:])
    if h > 23 or m > 59:
        raise SystemExit(f"slot {s!r}: not a clock time")
    return f"{h:02d}:{m:02d}"


def parse_day(s: Optional[str], cfg: Config) -> date:
    if not s:
        return cfg.posting_day()
    if s == "tomorrow":
        return cfg.tomorrow()
    if s == "today":
        return cfg.now().date()
    return date.fromisoformat(s)
