"""The posting jobs on macOS launchd, so posting needs no Hermes: three LaunchAgents in ~/Library/LaunchAgents.

    factory.py timer install [--from-hermes]   write + load the agents (refuses while Hermes still runs the same jobs)
    factory.py timer status                    each agent: loaded, last exit, its log; and its Hermes twin
    factory.py timer check                     run `post-due --dry-run` once under launchd (and prove it can read the
                                               Desktop folders: macOS privacy can block a launchd job there)
    factory.py timer remove                    unload + delete the agents (Hermes's paused jobs stay paused)

com.clipfactory.post-due  every 600 s   factory.py post-due          (was Hermes job factory-post-due)
com.clipfactory.inbox     every 300 s   factory.py inbox --quiet     (was Hermes job factory-inbox)
com.clipfactory.stats     03:30 daily   factory.py stats             (was Hermes job factory-stats)
com.clipfactory.gta-retry 03:45 daily   factory.py night-retry gaming --date today   (the night's failed GTA slots)
com.clipfactory.bench-fill 06:18 daily  factory.py bench fill all --date today   (spare clips into slots still empty)
com.clipfactory.morning   06:30 daily   factory.py morning --date today --notify   (the night shift's notice)
com.clipfactory.social    every 300 s   factory.py social tick --quiet          (the social layer: send, read, sweep)
com.clipfactory.checkpoints every 900 s factory.py social checkpoints --quiet   (each post's views at 1h, 3h, 24h ...)
com.clipfactory.studio    every 600 s   factory.py social studio --quiet        (cards and the coach's numbers)
com.clipfactory.learn     04:40 daily   factory.py social learn --quiet         (yesterday's report and proposals)
com.clipfactory.ideas     14:20 daily   factory.py social ideas --quiet         (what the benchmark channels posted)
Every social part starts off (state/<account>/social/settings.json): with nothing switched on these five do nothing.

Each other GTA account (Stunts, Roleplay: account_agents) gets its own three once it is switched on ("active":
true) on this machine: <slug>-nightly 21:00, <slug>-retry 03:45 and <slug>-scout hourly, a few minutes after Gaming's
(:46, :52 ...). A Lab account (Lab) gets one, <slug>-nightly at 21:00. A switched-off account installs nothing,
and `timer install` takes out what one left behind.

Two runners would post the same clip twice, so install refuses while any of those Hermes jobs is active;
`--from-hermes` pauses them first (`hermes -p gaming cron pause <id>`). Each agent runs the Command Line Tools'
Python.app binary directly (PY below) with its output in state/logs/<name>.log. launchd never starts a second copy of a job that is still running, and
post-due also holds its own run lock (poster.py), so a slow upload never overlaps the next tick.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import config as C
from . import hermes as H

PREFIX = "com.clipfactory."
AGENTS_DIR = Path(os.path.expanduser("~/Library/LaunchAgents"))
# The same Python 3.9 that /usr/bin/python3 runs, called by its own binary. macOS privacy (TCC) judges a launchd job by
# the program launchd starts: started as /usr/bin/python3 (the xcode-select shim) it is refused ~/Desktop even when
# Python.app has Full Disk Access; started as Python.app's binary, the grant applies (proven 2026-10-16).
PYTHON_APP = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app"
PY = PYTHON_APP + "/Contents/MacOS/Python"
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"   # ffprobe for the inbox; launchd's own PATH lacks it

# name -> what it runs, when, and the Hermes job(s) it replaces as (profile, job name)
SCOUT_HOURS = [{"Hour": h, "Minute": 40} for h in range(5, 23)]
AGENTS: List[Dict] = [
    # posting (2026-10-16, first switch)
    {"name": "post-due", "args": ["post-due"], "interval": 600, "twins": [("gaming", "factory-post-due")]},
    {"name": "inbox", "args": ["inbox", "--quiet"], "interval": 300, "twins": [("gaming", "factory-inbox")], "quiet": True},
    {"name": "stats", "args": ["stats"], "at": {"Hour": 3, "Minute": 30}, "twins": [("gaming", "factory-stats")]},
    # clip making and the reports (2026-10-16, no Hermes for clipping at all)
    {"name": "gta-nightly", "args": ["nightly", "gaming"], "at": {"Hour": 21, "Minute": 0},
     "twins": [("gaming", "gta-nightly")], "long": True},
    # 03:45 (audit GTA-10, Andrew 2026-10-17 "install the retry"; the night shift, 2026-10-19): once the night's
    # worker and the GTA second half have stopped, the failed or empty GTA slots made again from the next waiting
    # source (clipfactory/nightretry.py: 03:40-05:30 only, slots 2 h out, nothing new after 05:50, stopped at 06:15;
    # it never waits for a render: the render queue gives each step its turn). Long: it watches its worker.
    {"name": "gta-retry", "args": ["night-retry", "gaming", "--date", "today"],
     "at": {"Hour": 3, "Minute": 45}, "twins": [], "long": True},
    # 06:18 (build3/bench; the night shift, 2026-10-19: after the shift ends at 06:15, before the 06:30 notice): a
    # spare clip from the bench into each slot the night left empty, 90+ minutes away, through `factory.py place`
    # (an unsigned draft). bench.py: it waits for the Mac until 06:22 and writes nothing after 06:25. Long: Standard
    # priority, so its copies are not throttled past 06:25.
    {"name": "bench-fill", "args": ["bench", "fill", "all", "--date", "today"],
     "at": {"Hour": 6, "Minute": 18}, "twins": [], "long": True},
    {"name": "gta-scout", "args": ["scout", "gaming", "--quiet"], "at": SCOUT_HOURS, "twins": [("gaming", "gta-scout")],
     "quiet": True},
    {"name": "politics-scout", "args": ["scout", "politics", "--quiet"], "at": SCOUT_HOURS,
     "twins": [("politics", "politics-scout")], "quiet": True},
    {"name": "politics-collect", "args": ["collect", "politics", "--quiet"], "at": {"Minute": 15},
     "twins": [("politics", "politics-collect")], "quiet": True},
    # the breaking desk's 15-minute check (2026-10-28, clipfactory/breaking.py): the scout's feeds only, no yt-dlp,
    # 07:00 to 19:00 (outside, and right after the scout's own run, it reads nothing); a new alarm is a Mac notice
    {"name": "politics-breaking", "args": ["breaking", "quick", "politics", "--quiet"], "interval": 900,
     "twins": [], "quiet": True},
    # Andrew's phone (2026-10-29, clipfactory/phone.py): read the Yes / No button presses every minute
    {"name": "phone", "args": ["phone", "poll", "--quiet"], "interval": 60, "twins": [], "quiet": True},
    # 06:30 (owner, 2026-10-18: "Normally, I'm up at 6:45 ... clips done by 6:15 and the notification at 6:30 work
    # for me"): the night shift ends at 06:15, the bench fill at 06:25; he signs about 06:45; the first post is 07:00
    {"name": "morning", "args": ["morning", "--date", "today", "--notify"], "at": {"Hour": 6, "Minute": 30},
     "twins": [("gaming", "gta-morning"), ("politics", "politics-morning")]},
    # the review page, always up (starts at login, restarted if it dies), so the Dock app and the notice always land
    {"name": "review", "args": ["review"], "serve": True, "twins": []},
    # Claude's week (2026-10-27, clipfactory/usage.py; the 10-25 run-out lost the 10-26 board): count the new
    # transcript lines, keep now.json fresh for the chat hook, a Mac notice when the week gets worse
    {"name": "usage", "args": ["usage", "check", "--notify", "--quiet"], "interval": 1800, "twins": [], "quiet": True},
    # the social layer (2026-10-19, clipfactory/social.py): light jobs, never the render queue; each part starts off
    # and its step does nothing until it is switched on for an account
    {"name": "social", "args": ["social", "tick", "--quiet"], "interval": 300, "twins": [], "quiet": True},
    {"name": "checkpoints", "args": ["social", "checkpoints", "--quiet"], "interval": 900, "twins": [], "quiet": True},
    {"name": "studio", "args": ["social", "studio", "--quiet"], "interval": 600, "twins": [], "quiet": True},
    {"name": "learn", "args": ["social", "learn", "--quiet"], "at": {"Hour": 4, "Minute": 40}, "twins": [],
     "quiet": True},
    {"name": "ideas", "args": ["social", "ideas", "--quiet"], "at": {"Hour": 14, "Minute": 20}, "twins": [],
     "quiet": True},
]
# Hermes jobs with no launchd twin: their work moved to a Claude scheduled task (the Politics's thinking needs a model,
# and the owner runs Claude, not a model inside Hermes). --from-hermes pauses these too.
RETIRED: List = [("politics", "politics-tonight")]
HERMES_PROFILE = "gaming"   # the profile the first three lived in; kept for the old messages
CHECK = {"name": "post-due-check", "args": ["timer", "probe"]}   # `timer check`: a one-shot, removed after
FIRST_GTA = "gaming"   # its three are in AGENTS under their old names (gta-nightly, gta-retry, gta-scout)
SCOUT_STEP = 6            # minutes between the GTA scouts (:40, :46, :52 ...): one YouTube listing at a time


def _scout_minute(cfg: C.Config, slug: str) -> int:
    """The minute an extra GTA account's hourly scout runs: its own accounts.json "scout_minute", else 6 minutes per
    place after Gaming's :40 in the file's order (switched on or not, so a minute never moves when one switches)."""
    acct = cfg.all_accounts.get(slug) or cfg.accounts.get(slug)
    own = (acct.raw or {}).get("scout_minute") if acct else None
    if isinstance(own, int) and 0 <= own <= 59:
        return own
    extra = [s for s, a in (cfg.all_accounts or cfg.accounts).items() if a.maker == "gta" and s != FIRST_GTA]
    return (40 + SCOUT_STEP * (extra.index(slug) + 1)) % 60 if slug in extra else 46


def _has_scout(slug: str) -> bool:
    """A block of its own in config/scout.json (`scout <slug>` refuses an account without one)."""
    try:
        return isinstance(json.loads((C.ROOT / "config" / "scout.json").read_text()).get(slug), dict)
    except (OSError, ValueError):
        return False


def _gta_agents(cfg: C.Config, slug: str) -> List[Dict]:
    """The night's three for one extra GTA account, as Gaming's: the 21:00 nightly, the 03:45 retry, the scout."""
    out: List[Dict] = [
        {"name": f"{slug}-nightly", "args": ["nightly", slug], "at": {"Hour": 21, "Minute": 0}, "twins": [],
         "long": True},
        {"name": f"{slug}-retry", "args": ["night-retry", slug, "--date", "today"], "at": {"Hour": 3, "Minute": 45},
         "twins": [], "long": True},
    ]
    return out + _scout_agent(cfg, slug)


def _scout_agent(cfg: C.Config, slug: str) -> List[Dict]:
    """An extra GTA account's hourly scout, at its own minute, when config/scout.json has its block."""
    if not _has_scout(slug):
        return []
    m = _scout_minute(cfg, slug)
    return [{"name": f"{slug}-scout", "args": ["scout", slug, "--quiet"],
             "at": [{"Hour": h["Hour"], "Minute": m} for h in SCOUT_HOURS], "twins": [], "quiet": True}]


def _day_agent(slug: str, at: str) -> Dict:
    """A day shift's run (2026-10-29): `nightly <slug>` at `at` makes the next posting day's board (a run with no date
    after 07:00), and its worker stops itself at the shift's "until" (makers.day_shift_clock)."""
    return {"name": f"{slug}-day", "args": ["nightly", slug], "at": {"Hour": int(at[:2]), "Minute": int(at[3:])},
            "twins": [], "long": True}


def _cfg(cfg: Optional[C.Config]) -> Optional[C.Config]:
    if cfg is not None:
        return cfg
    try:
        return C.load()
    except (OSError, ValueError, KeyError, SystemExit):
        return None


def _lab_agents(slug: str) -> List[Dict]:
    """A Lab account's one agent: <slug>-nightly at 21:00 (`nightly <slug>`: tomorrow's make, the fallback when
    the 20:05 scripting task did not start it; the worker lock keeps it to one). No retry, no scout, no bench."""
    return [{"name": f"{slug}-nightly", "args": ["nightly", slug], "at": {"Hour": 21, "Minute": 0}, "twins": [],
             "long": True}]


def _garage_agents(slug: str) -> List[Dict]:
    """The Garage maker's one agent (2026-10-29, Andrew: "Cars is at night"): <slug>-nightly at 21:00
    (`nightly <slug>`: the next posting day's edits, each cut in the render queue, its turn beside the Politics and the
    GTA accounts). No retry and no scout: it finds its own footage per vehicle."""
    return [{"name": f"{slug}-nightly", "args": ["nightly", slug], "at": {"Hour": 21, "Minute": 0}, "twins": [],
             "long": True}]


def _own_agents(cfg: C.Config, slug: str, acct: C.Account) -> List[Dict]:
    """The agents an account other than Gaming and the Politics brings once it is on: a GTA account's three, a Lab
    or a Garage account's one; none for any other, and none for one fed by hand (2026-10-29: "night": false, or
    maker "hand").
    A GTA account with a day shift (2026-10-29) brings its day run and its scout instead of the night's three."""
    if acct.fed_by_hand():
        return []
    if acct.maker == "gta" and slug != FIRST_GTA and acct.day_shift():
        return [_day_agent(slug, acct.day_shift()[0])] + _scout_agent(cfg, slug)
    if acct.maker == "gta" and slug != FIRST_GTA:
        return _gta_agents(cfg, slug)
    if acct.maker == "lab":
        return _lab_agents(slug)
    if acct.maker == "garage":
        return _garage_agents(slug)
    return []


def _possible_agents(cfg: C.Config, slug: str, acct: C.Account) -> List[Dict]:
    """Every agent an extra account could have had, whatever its block says now (a night's three, a day run, a Lab's
    one): what a switch-off, a move to a day shift or `timer remove` takes out when its plist is still there."""
    if acct.maker == "gta" and slug != FIRST_GTA:
        return _gta_agents(cfg, slug) + [_day_agent(slug, "09:30")]
    if acct.maker == "garage":
        return _garage_agents(slug)
    return _lab_agents(slug) if acct.maker == "lab" else []


def account_agents(cfg: Optional[C.Config] = None) -> List[Dict]:
    """The agents of each GTA account other than Gaming, and each Lab account, that is switched on and runs on this
    machine (cfg.accounts). None for the live config until Andrew switches one on: AGENTS alone, as before."""
    cfg = _cfg(cfg)
    if cfg is None:
        return []
    return [ag for s, a in cfg.accounts.items() for ag in _own_agents(cfg, s, a)]


# drop mode (clipfactory/drop.py, 2026-10-19): every 5 minutes one request to Rockstar's channel feed; only while a GTA
# VI drop is open (12 hours) does it read the roster's Twitch and Kick clips. Installed only while config/scout.json
# gaming.drop "on" is true (Andrew's switch, off until he says), so the code alone changes no timer and no doctor line.
DROP_AGENT = {"name": "gta-drop", "args": ["drop", FIRST_GTA, "--quiet"], "interval": 300, "twins": [], "quiet": True}


def _drop_on(slug: str = FIRST_GTA) -> bool:
    try:
        d = json.loads((C.ROOT / "config" / "scout.json").read_text()).get(slug) or {}
    except (OSError, ValueError):
        return False
    return isinstance(d.get("drop"), dict) and bool(d["drop"].get("on"))


def drop_agents(cfg: Optional[C.Config] = None) -> List[Dict]:
    """[DROP_AGENT] while drop mode is switched on and Gaming works on this machine; else none."""
    cfg = _cfg(cfg)
    if cfg is None or FIRST_GTA not in cfg.accounts or not _drop_on():
        return []
    return [dict(DROP_AGENT)]


# the afternoon prep (makers.prep_gta, Best of GTA 2026-10-29): `prep <slug>` at gta_maker prep.at, for each GTA account
# with prep.on true; installed only while it is on (off in config until Andrew says), so the code changes no timer.
def _prep_agent(slug: str, at: str = "15:00") -> Dict:
    name = "gta-prep" if slug == FIRST_GTA else f"{slug}-prep"
    try:
        hh, mm = (int(x) for x in str(at).split(":"))
    except (TypeError, ValueError):
        hh, mm = 15, 0
    return {"name": name, "args": ["prep", slug, "--quiet"], "at": {"Hour": hh, "Minute": mm}, "twins": [],
            "long": True}


def prep_agents(cfg: Optional[C.Config] = None) -> List[Dict]:
    """One `prep` agent per GTA account switched on here whose gta_maker prep.on is true; else none."""
    from . import makers as M
    cfg = _cfg(cfg)
    if cfg is None:
        return []
    out = []
    for s, a in cfg.accounts.items():
        if a.maker == "gta" and not a.fed_by_hand():
            ps = M.prep_settings(a)
            if ps.get("on"):
                out.append(_prep_agent(s, str(ps.get("at") or "15:00")))
    return out


def all_agents(cfg: Optional[C.Config] = None) -> List[Dict]:
    """What `timer install` writes: AGENTS, then account_agents, then drop mode's while it is on, then the afternoon
    preps that are on."""
    return list(AGENTS) + account_agents(cfg) + drop_agents(cfg) + prep_agents(cfg)


def left_behind(cfg: Optional[C.Config] = None, agents_dir: Optional[Path] = None) -> List[Dict]:
    """An extra GTA account's agents whose plist is still in place while it is switched off (or another machine's):
    `timer install` unloads and removes them."""
    cfg = _cfg(cfg)
    if cfg is None:
        return []
    d = agents_dir or AGENTS_DIR
    want = {a["name"] for a in all_agents(cfg)}
    gone = [ag for s, a in cfg.all_accounts.items()
            for ag in _possible_agents(cfg, s, a) if ag["name"] not in want and (d / f"{label(ag)}.plist").exists()]
    if DROP_AGENT["name"] not in want and (d / f"{label(DROP_AGENT)}.plist").exists():
        gone.append(dict(DROP_AGENT))                  # drop mode switched off since: timer install takes it out
    for s, a in cfg.all_accounts.items():              # an afternoon prep switched off since
        ag = _prep_agent(s)
        if a.maker == "gta" and ag["name"] not in want and (d / f"{label(ag)}.plist").exists():
            gone.append(ag)
    return gone

Launchctl = Callable[[List[str]], subprocess.CompletedProcess]


def _launchctl(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl"] + args, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def label(agent: Dict) -> str:
    return PREFIX + agent["name"]


def logs_dir() -> Path:
    return C.STATE / "logs"


def log_path(agent: Dict) -> Path:
    return logs_dir() / f"{agent['name']}.log"


def plist(agent: Dict) -> dict:
    """The LaunchAgent for one job: Python.app's binary factory.py <args>, its output appended to state/logs/<name>.log."""
    p = {
        "Label": label(agent),
        "ProgramArguments": [PY, str(H.FACTORY)] + agent["args"],
        "WorkingDirectory": str(C.ROOT),
        "EnvironmentVariables": {"PATH": PATH, "LANG": "en_US.UTF-8", "PYTHONUTF8": "1",
                                 "PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(log_path(agent)),
        "StandardErrorPath": str(log_path(agent)),
        "ProcessType": "Background",
        "RunAtLoad": False,
    }
    if not agent.get("quiet"):     # a dated line per run; the inbox stays silent until a clip is dropped
        p["EnvironmentVariables"]["CLIP_FACTORY_LOG_STAMP"] = "1"
    if C.this_machine() != C.MAIN:  # a second computer: its agents run its own accounts (launchd has no shell env)
        p["EnvironmentVariables"][C.MACHINE_ENV] = C.this_machine()
    if agent.get("interval"):
        p["StartInterval"] = int(agent["interval"])
    if agent.get("at"):
        at = agent["at"]
        p["StartCalendarInterval"] = [dict(x) for x in at] if isinstance(at, list) else dict(at)
    if agent.get("long"):
        # the nightly cut starts a detached worker that renders for hours: full priority (Background throttles CPU and
        # disk), and launchd must not kill it when the starter exits
        p["ProcessType"] = "Standard"
        p["AbandonProcessGroup"] = True
    if agent.get("once"):
        p["RunAtLoad"] = True
    if agent.get("serve"):
        p["RunAtLoad"] = True
        p["KeepAlive"] = True
        p["ProcessType"] = "Interactive"   # the page answers a click; Background would throttle it
        p["ThrottleInterval"] = 30         # a port still held by a stray copy: retry every 30 s, not in a spin
    return p


def _when(agent: Dict) -> str:
    if agent.get("serve"):
        return "always on"
    if agent.get("interval"):
        return f"every {agent['interval']} s"
    at = agent["at"]
    if isinstance(at, list):
        return f"{len(at)} times a day at :{at[0]['Minute']:02d}"
    if "Hour" not in at:
        return f"hourly at :{at['Minute']:02d}"
    return f"{at['Hour']:02d}:{at['Minute']:02d}"


def write(agent: Dict, agents_dir: Path) -> Path:
    agents_dir.mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{label(agent)}.plist"
    path.write_bytes(plistlib.dumps(plist(agent)))
    return path


# ------------------------------------------------------------------------------------------------------ Hermes twins
def _active(job: dict) -> bool:
    return not (job.get("paused") or job.get("enabled") is False or str(job.get("state", "")).lower() == "paused")


def _twin_keys() -> List:
    return [t for a in AGENTS for t in a["twins"]] + list(RETIRED)


def hermes_twins(scratch: Optional[Path] = None) -> Dict[tuple, dict]:
    """The Hermes jobs these agents replace (and the retired ones), by (profile, name); only the ones that exist."""
    want = set(_twin_keys())
    out: Dict[tuple, dict] = {}
    for profile in sorted({p for p, _ in want}):
        for j in H._jobs(profile, scratch):
            if (profile, j.get("name")) in want:
                out[(profile, j.get("name"))] = dict(j, _profile=profile)
    return out


def active_twins(scratch: Optional[Path] = None) -> List[dict]:
    return [j for j in hermes_twins(scratch).values() if _active(j)]


def installed(agents_dir: Optional[Path] = None) -> List[str]:
    """Names of the agents whose plist is in place (what `hermes sync` must not schedule again)."""
    d = agents_dir or AGENTS_DIR
    return [a["name"] for a in all_agents() if (d / f"{label(a)}.plist").exists()]


def owns_hermes_job(name: str, agents_dir: Optional[Path] = None) -> bool:
    have = set(installed(agents_dir))
    if have and any(n == name for _, n in RETIRED):
        return True
    return any(n == name and a["name"] in have for a in AGENTS for _, n in a["twins"])


def pause_hermes(scratch: Optional[Path] = None) -> List[str]:
    lines = []
    for j in active_twins(scratch):
        r = H._hermes(["-p", j["_profile"], "cron", "pause", str(j.get("id"))], scratch)
        lines.append(f"hermes job {j.get('name')} ({j['_profile']}, {j.get('id')}): "
                     + ("paused" if r.returncode == 0 else f"pause FAILED\n{(r.stdout + r.stderr)[-600:]}"))
    return lines


# ------------------------------------------------------------------------------------------------------ commands
def _load(agent: Dict, path: Path, launchctl: Launchctl) -> str:
    launchctl(["bootout", f"{_domain()}/{label(agent)}"])          # an older copy, if loaded; its error is expected
    r = launchctl(["bootstrap", _domain(), str(path)])
    return "loaded" if r.returncode == 0 else f"load FAILED: {(r.stdout + r.stderr).strip()[-400:]}"


def check_failed(lines: List[str]) -> bool:
    """A `timer check` that did not prove the launchd route: a folder it could not read, no log, or a bad exit."""
    return any(": NO (" in ln or "(no log written)" in ln for ln in lines) or not any(ln.startswith("exit 0 ") for ln in lines)


def install(cfg: C.Config, from_hermes: bool = False, scratch: Optional[Path] = None,
            agents_dir: Optional[Path] = None, launchctl: Launchctl = _launchctl,
            preflight: Optional[Callable[[], List[str]]] = None, only: Optional[str] = None) -> List[str]:
    """Write and load every agent. `only` (a name, e.g. "usage"): that one job alone, the others left running as they
    are (a reload stops a job mid-run: a post-due upload, the night's worker)."""
    agents_dir = agents_dir or AGENTS_DIR
    lines: List[str] = []
    if only and only not in [a["name"] for a in all_agents(cfg)]:
        raise SystemExit(f"timer install --only: no job named {only!r} (one of "
                         f"{', '.join(a['name'] for a in all_agents(cfg))})")
    if cfg is not None and cfg.all_accounts and not any(a.machine == cfg.machine for a in cfg.all_accounts.values()):
        # a stray CLIP_FACTORY_MACHINE: every agent would refuse every account, with the name written into each
        raise SystemExit(f'refused, nothing installed: no account in config/accounts.json runs on the machine '
                         f'"{cfg.machine}" ({C.MACHINE_ENV}), so every night, scout and post would do nothing.\n'
                         f'  fix: on the main Mac, unset {C.MACHINE_ENV} (take it out of ~/.zshrc, open a new '
                         f'Terminal) and run timer install again; on another computer, first set "machine": '
                         f'"{cfg.machine}" on the accounts it runs')
    busy = active_twins(scratch)
    if busy and not from_hermes:
        names = ", ".join(f"{j.get('name')} ({j['_profile']}, {j.get('id')})" for j in busy)
        raise SystemExit(f"refused: Hermes still runs {names}. Two runners would post the same clip twice (or cut the "
                         f"same day twice).\n  fix: factory.py timer install --from-hermes  (pauses those Hermes jobs first)")
    # before Hermes lets go: a launchd job that cannot read ~/Desktop would refuse every clip and give up its slots
    pre = (preflight or (lambda: check(agents_dir=agents_dir, launchctl=launchctl)))()
    if check_failed(pre):
        raise SystemExit("refused: a job under launchd cannot do the work yet (nothing paused, nothing installed):\n"
                         + "\n".join(pre) + "\n  fix: System Settings > Privacy & Security > Full Disk Access > + > "
                         + "Command+Shift+G " + PYTHON_APP + " > Open, switch it on; then `factory.py timer check`")
    if busy:
        lines += pause_hermes(scratch)
        still = active_twins(scratch)
        if still:
            return lines + [f"stopped: {', '.join(j.get('name') for j in still)} still active in Hermes; nothing installed"]
    for agent in all_agents(cfg):
        if only and agent["name"] != only:
            continue
        path = write(agent, agents_dir)
        when = _when(agent)
        lines.append(f"{label(agent)} ({when}): {path.name} written, {_load(agent, path, launchctl)}")
    for agent in ([] if only else left_behind(cfg, agents_dir)):   # an account switched off since: nothing of it runs
        launchctl(["bootout", f"{_domain()}/{label(agent)}"])
        (agents_dir / f"{label(agent)}.plist").unlink(missing_ok=True)
        lines.append(f"{label(agent)}: its account is switched off here; unloaded and removed")
    lines.append(f"logs: {logs_dir()}")
    return lines


def _print(agent: Dict, launchctl: Launchctl) -> Dict[str, str]:
    r = launchctl(["print", f"{_domain()}/{label(agent)}"])
    if r.returncode != 0:
        return {}
    out = {"loaded": "yes"}
    for ln in r.stdout.splitlines():
        k, _, v = ln.strip().partition(" = ")
        if k in ("state", "runs", "last exit code", "pid") and k not in out:
            out[k] = v.strip()
    return out


def status(scratch: Optional[Path] = None, agents_dir: Optional[Path] = None,
           launchctl: Launchctl = _launchctl, cfg: Optional[C.Config] = None) -> List[str]:
    agents_dir = agents_dir or AGENTS_DIR
    twins = hermes_twins(scratch)
    out = []
    if installed(agents_dir) and not os.path.exists(PY):
        out.append(f"NO  {PY}: missing (a Command Line Tools update?); the agents cannot start. "
                   "Fix PY in clipfactory/timer.py, then factory.py timer install")
    for agent in left_behind(cfg, agents_dir):
        out.append(f"--  {label(agent)}: its account is switched off here, plist still in place; factory.py timer "
                   f"install takes it out")
    for agent in all_agents(cfg):
        info = _print(agent, launchctl)
        has_plist = (agents_dir / f"{label(agent)}.plist").exists()
        mine = [twins[t] for t in agent["twins"] if t in twins]
        twin_txt = ("not in Hermes" if not mine else
                    ", ".join(f"Hermes {j.get('name')} {'ACTIVE' if _active(j) else 'paused'}" for j in mine))
        if info:
            good = not any(_active(j) for j in mine)
            desc = ", ".join(f"{k} {v}" for k, v in info.items() if k != "loaded")
            out.append(f"{'ok ' if good else 'NO '} {label(agent)}: loaded ({desc}); {twin_txt}"
                       + ("" if good else "  <- two runners: pause the Hermes job") + f"; log {log_path(agent)}")
        else:
            out.append(f"--  {label(agent)}: {'plist written but NOT loaded' if has_plist else 'not installed'}; {twin_txt}")
    for key in RETIRED:
        j = twins.get(key)
        if j:
            out.append(f"{'NO ' if _active(j) else 'ok '} Hermes {key[1]} ({key[0]}): "
                       + ("ACTIVE  <- its work is a Claude scheduled task now; pause it" if _active(j)
                          else "paused (its work is a Claude scheduled task now)"))
    return out


def check(timeout: float = 90, agents_dir: Optional[Path] = None, launchctl: Launchctl = _launchctl,
          poll: float = 1.0) -> List[str]:
    """Prove the launchd route (its PATH, its folder access, its Python) with a one-shot `post-due --dry-run`."""
    agents_dir = agents_dir or AGENTS_DIR
    agent = dict(CHECK, once=True)
    log = log_path(agent)
    if log.exists():
        log.unlink()
    path = write(agent, agents_dir)
    lines = [f"{label(agent)}: {_load(agent, path, launchctl)}"]
    end = time.time() + timeout
    info: Dict[str, str] = {}
    while time.time() < end:
        info = _print(agent, launchctl)
        if info.get("last exit code", "(never exited)") != "(never exited)" and info.get("state") != "running":
            break
        time.sleep(poll)
    launchctl(["bootout", f"{_domain()}/{label(agent)}"])
    path.unlink(missing_ok=True)
    lines.append(f"exit {info.get('last exit code', '?')} after {info.get('runs', '?')} run(s); log {log}:")
    lines += [f"  | {ln}" for ln in (log.read_text().splitlines() if log.exists() else ["(no log written)"])]
    return lines


def probe(cfg: C.Config) -> List[str]:
    """What `timer check` runs inside launchd: can this process read the folders posting needs, then a dry run."""
    from . import inbox as I
    from . import poster as P
    lines = []
    for what, d in (("drafts", cfg.drafts_root), ("drop folder", I.root(cfg)), ("state", C.STATE)):
        try:
            lines.append(f"  reads {what} {d}: yes ({len(os.listdir(d))} entries)")
        except FileNotFoundError:
            lines.append(f"  reads {what} {d}: not there yet")
        except OSError as e:
            lines.append(f"  reads {what} {d}: NO ({e.strerror}); give {PYTHON_APP} Full Disk Access")
    # the nightly cut runs the clip skills as children under /usr/bin/python3: prove a child reaches the account folders
    for acct in cfg.accounts.values():
        r = subprocess.run(["/usr/bin/python3", "-c", "import os,sys; print(len(os.listdir(sys.argv[1])))",
                            str(acct.account_dir)], capture_output=True, text=True, timeout=60)
        lines.append(f"  skill child reads {acct.name} {acct.account_dir}: "
                     + (f"yes ({r.stdout.strip()} entries)" if r.returncode == 0
                        else f"NO ({(r.stderr.strip().splitlines() or ['?'])[-1][:160]})"))
    return lines + ["post-due --dry-run:"] + (P.post_due(cfg, dry_run=True) or ["  nothing due"])


def remove(agents_dir: Optional[Path] = None, launchctl: Launchctl = _launchctl,
           scratch: Optional[Path] = None) -> List[str]:
    agents_dir = agents_dir or AGENTS_DIR
    out = []
    for agent in AGENTS:
        path = agents_dir / f"{label(agent)}.plist"
        launchctl(["bootout", f"{_domain()}/{label(agent)}"])
        had = path.exists()
        path.unlink(missing_ok=True)
        out.append(f"{label(agent)}: {'unloaded and removed' if had else 'not installed'}")
    cfg = _cfg(None)
    for s, a in (cfg.all_accounts.items() if cfg else []):     # an extra GTA or Lab account's, switched on or not
        for agent in _possible_agents(cfg, s, a):
            path = agents_dir / f"{label(agent)}.plist"
            if path.exists():
                launchctl(["bootout", f"{_domain()}/{label(agent)}"])
                path.unlink(missing_ok=True)
                out.append(f"{label(agent)}: unloaded and removed")
    paused = [j for j in hermes_twins(scratch).values() if not _active(j)]
    if paused:
        out.append("nothing posts or cuts now. To hand the jobs back to Hermes: "
                   + "; ".join(f"hermes -p {j['_profile']} cron resume {j.get('id')}" for j in paused))
    return out
