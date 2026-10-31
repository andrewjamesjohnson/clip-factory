"""Install the two clip bots into Hermes: a profile each (SOUL, config, runbook skill), the small scripts their
scheduled jobs run, and the schedule itself.

    factory.py hermes install [--scratch DIR]   # --scratch rehearses in a throwaway HERMES_HOME
    factory.py hermes sync                      # new skills + new jobs only; the profiles, memory and chats stay
    factory.py hermes status
    factory.py hermes remove                    # profiles and their jobs; the factory's boards and drafts stay

Hermes runs a cron script only from the profile's own scripts/ folder, so each job's script is a three-line
wrapper there that calls this factory.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from . import config as C

HERE = C.ROOT / "hermes"
PY = "/usr/bin/python3"
FACTORY = C.ROOT / "factory.py"

# job name -> (profile, schedule, script or None, agent prompt or None, skills, deliver)
POLITICS_PROMPT = (
    "Tonight's run for Politics. Follow your politics-bot runbook from step 1 for TOMORROW's posting day: "
    "plan, doctor, build every clip the account's rules allow into tomorrow's factory slots (each 30 to 90 seconds: 65 or more unless a complete exchange names payoff_end), "
    "collect, then save your report with `python3 ~/clip-factory/factory.py note politics \"<report>\"` so the "
    "04:30 message carries it. Never approve, schedule, upload, or fill a cast's verified field.")

JOBS: List[Dict] = [
    {"name": "gta-nightly", "profile": "gaming", "schedule": "0 21 * * *", "script": "nightly.sh",
     "args": "nightly gaming", "deliver": "local"},
    {"name": "gta-morning", "profile": "gaming", "schedule": "30 4 * * *", "script": "morning.sh",
     "args": "morning gaming --date today", "deliver": "bot-chat:gaming"},
    {"name": "politics-tonight", "profile": "politics", "schedule": "0 19 * * *", "prompt": POLITICS_PROMPT,
     "skills": ["politics-bot"], "workdir": "~/Desktop/Andrew Johnson/Clipping", "deliver": "local"},
    # the news scouts (scout.py): every hour 05:40-22:40, no AI. The board is fresh for the bots' skills and the owner;
    # the GTA one also lifts the top roster uploads in the inbox before the 21:00 cut.
    {"name": "gta-scout", "profile": "gaming", "schedule": "40 5-22 * * *", "script": "scout.sh",
     "args": "scout gaming --quiet", "deliver": "local"},
    {"name": "politics-scout", "profile": "politics", "schedule": "40 5-22 * * *", "script": "scout.sh",
     "args": "scout politics --quiet", "deliver": "local"},
    {"name": "politics-collect", "profile": "politics", "schedule": "15 * * * *", "script": "collect.sh",
     "args": "collect politics --quiet", "deliver": "local"},
    {"name": "politics-morning", "profile": "politics", "schedule": "30 4 * * *", "script": "morning.sh",
     "args": "morning politics --date today", "deliver": "bot-chat:politics"},
    # Both accounts, plain scripts (--no-agent): the bot's AI never runs them. post-due moves only clips a person
    # signed (Approve signs and schedules); stats reads the numbers for the dashboard.
    {"name": "factory-post-due", "profile": "gaming", "schedule": "*/10 * * * *", "script": "post-due.sh",
     "args": "post-due", "deliver": "local"},
    {"name": "factory-stats", "profile": "gaming", "schedule": "30 3 * * *", "script": "stats.sh",
     "args": "stats", "deliver": "local"},
    # the Desktop drop folder (inbox_root): a clip dragged in takes a slot and waits for Approve (inbox_auto_sign)
    {"name": "factory-inbox", "profile": "gaming", "schedule": "*/5 * * * *", "script": "inbox.sh",
     "args": "inbox --quiet", "deliver": "local"},
]
PROFILES = ["gaming", "politics"]


def _env(scratch: Optional[Path]) -> dict:
    env = dict(os.environ)
    if scratch:
        env["HERMES_HOME"] = str(scratch)
    return env


def _home(scratch: Optional[Path]) -> Path:
    return scratch or Path(os.path.expanduser("~/.hermes"))


def _profile_home(name: str, scratch: Optional[Path]) -> Path:
    return _home(scratch) / "profiles" / name


def _hermes(args: List[str], scratch: Optional[Path], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(["hermes"] + args, env=_env(scratch), capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def _jobs(profile: str, scratch: Optional[Path]) -> List[dict]:
    store = _profile_home(profile, scratch) / "cron" / "jobs.json"
    if not store.exists():
        return []
    data = json.loads(store.read_text())
    return data.get("jobs", data) if isinstance(data, dict) else data


def install(cfg: C.Config, scratch: Optional[Path] = None) -> List[str]:
    lines: List[str] = []
    if scratch:
        scratch.mkdir(parents=True, exist_ok=True)
        lines.append(f"rehearsing in HERMES_HOME={scratch}")
    for name in PROFILES:
        home = _profile_home(name, scratch)
        args = ["profile", "install", str(HERE / name), "--name", name, "-y"]
        if home.exists():
            args.insert(-1, "--force")
        r = _hermes(args, scratch)
        ok = r.returncode == 0
        lines.append(f"profile {name}: {'installed' if ok else 'FAILED'}" + ("" if ok else f"\n{(r.stdout + r.stderr)[-1500:]}"))
        if not ok:
            return lines
        (home / "scripts").mkdir(parents=True, exist_ok=True)
    return lines + _install_jobs(scratch)


def sync(cfg: C.Config, scratch: Optional[Path] = None) -> List[str]:
    """Copy each profile's SOUL.md and skills from ~/clip-factory/hermes/<profile>/skills into the installed profile and schedule any
    job not yet there, without reinstalling the profile (its memory, sessions and sign-ins stay as they are)."""
    import shutil
    lines: List[str] = []
    for name in PROFILES:
        home = _profile_home(name, scratch)
        if not home.exists():
            lines.append(f"profile {name}: not installed (factory.py hermes install)")
            continue
        if (HERE / name / "SOUL.md").exists():
            shutil.copy2(HERE / name / "SOUL.md", home / "SOUL.md")
            lines.append(f"profile {name}: SOUL.md synced")
        for sk in sorted((HERE / name / "skills").iterdir()):
            if (sk / "SKILL.md").exists():
                dst = home / "skills" / sk.name
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sk / "SKILL.md", dst / "SKILL.md")
                lines.append(f"profile {name}: skill {sk.name} synced")
        (home / "scripts").mkdir(parents=True, exist_ok=True)
    return lines + _install_jobs(scratch)


def _install_jobs(scratch: Optional[Path]) -> List[str]:
    lines: List[str] = []
    from . import timer
    for job in JOBS:
        if not _profile_home(job["profile"], scratch).exists():
            continue
        if not scratch and timer.owns_hermes_job(job["name"]):
            where = ("a Claude scheduled task" if any(n == job["name"] for _, n in timer.RETIRED)
                     else "launchd (factory.py timer)")
            lines.append(f"job {job['name']}: runs on {where}, not scheduled in Hermes")
            continue
        have = {j.get("name"): j for j in _jobs(job["profile"], scratch)}
        if job["name"] in have:
            got = have[job["name"]]
            if job.get("prompt") and job["prompt"] not in (got.get("prompt") or ""):
                r = _hermes(["-p", job["profile"], "cron", "edit", str(got.get("id")), "--prompt", job["prompt"]], scratch)
                lines.append(f"job {job['name']}: prompt "
                             + ("updated" if r.returncode == 0 else f"update FAILED\n{(r.stdout + r.stderr)[-600:]}"))
            else:
                lines.append(f"job {job['name']}: already scheduled")
            continue
        args = ["-p", job["profile"], "cron", "create", "--name", job["name"], "--deliver", job["deliver"]]
        if job.get("script"):
            # the args ride in the wrapper's command line: Hermes passes none, so write one wrapper per job
            wrapper = _profile_home(job["profile"], scratch) / "scripts" / f"{job['name']}.sh"
            wrapper.write_text("#!/bin/bash\n# written by clip-factory (factory.py hermes install)\n"
                               f"exec {PY} {shlex.quote(str(FACTORY))} {job['args']}\n")
            wrapper.chmod(0o755)
            args += ["--script", wrapper.name, "--no-agent", job["schedule"]]
        else:
            for s in job.get("skills", []):
                args += ["--skill", s]
            if job.get("workdir"):
                args += ["--workdir", str(C.expand(job["workdir"]))]
            args += [job["schedule"], job["prompt"]]
        r = _hermes(args, scratch)
        lines.append(f"job {job['name']} ({job['profile']}, {job['schedule']}): "
                     + ("scheduled" if r.returncode == 0 else f"FAILED\n{(r.stdout + r.stderr)[-1200:]}"))
    return lines


def status(scratch: Optional[Path] = None) -> List[str]:
    out = []
    for name in PROFILES:
        home = _profile_home(name, scratch)
        if not home.exists():
            out.append(f"--  profile {name}: not installed (factory.py hermes install)")
            continue
        jobs = _jobs(name, scratch)
        desc = ", ".join(f"{j.get('name')} {_sched(j)}{' PAUSED' if j.get('paused') or j.get('enabled') is False else ''}"
                         for j in jobs) or "no jobs"
        out.append(f"ok  profile {name}: {desc}")
    return out


def _band_words(text: str, lo: float, hi: float) -> List[str]:
    """What is wrong with how one runbook states its account's length band (an empty list when it agrees)."""
    import re
    band = f"{lo:.0f} to {hi:.0f} seconds"
    wrong = []
    if band not in text:
        wrong.append(f"never says \"{band}\"")
    for n in re.findall(r"\bunder (\d+) seconds", text):
        if float(n) != hi:
            wrong.append(f"says \"under {n} seconds\"")
    return wrong


# Rules that ended, as a file would state them (audit STA-19, 2026-10-17). An override banner that names an old
# rule to retire it ("Where this page says ... four a day ...") uses other words and is not flagged.
RETIRED = (
    # the clip-count wording only: "up to four streamers" or "up to four plates" is no day ceiling
    (r"four is the ceiling|\bup to four(?:, one per template| (?:clips|drafts|slots|a day|per day|a night)\b)",
     "the four-a-day ceiling (ten a day since 2026-10-17)"),
    (r"\bSchedule approved\b", "a 'Schedule approved' button (Approve signs and schedules since 2026-10-17)"),
    (r"\b05:00 to 23:00\b|\bevery two hours\b", "the 05:00-23:00 two-hourly slots (07:00-23:00 since 2026-10-17)"),
    # the night shift (2026-10-19, clipfactory/shift.py): one shift 21:00-06:15, the notice at 06:30, he signs ~06:45.
    # Andrew's own quoted words ("I'll confirm the cast at the 05:30 sign-off") are history and are not flagged.
    (r"--until (?:20:50|04:50)\b|CLIP_RENDERLOCK_UNTIL=(?:20:50|04:50)\b",
     "the 20:50 / 04:50 render cutoffs (05:50 in the night shift since 2026-10-19)"),
    (r"\b05:25\b", "the 05:25 morning notice (06:30 since the night shift, 2026-10-19)"),
    (r"\b02:43\b", "the 02:43 Politics gap-fill (the Politics's second half waits from ~22:13 since the night shift)"),
    (r"~00:43\b", "the Politics's second half at ~00:43 (it waits from ~22:13 since the night shift's review, 10-18)"),
    (r"\bsigns?(?: it| them)? (?:at|from) 05:30\b|\b(?:Andrew's|his|the owner's) 05:30 sign-off\b|\b05:30 to 06:30\b",
     "the 05:30 sign-off (Andrew signs about 06:45 since the night shift, 2026-10-19)"),
)
TASKS = Path.home() / ".claude" / "scheduled-tasks"
# every scheduled task that makes an account's clips, by the id that runs; a folder not there yet (a task before its
# deploy) is skipped
# clip-coach (the coach's scorecards) and clip-weekly-report (Sunday's report) serve both accounts (the social layer),
# as does bench-builder-auto (both benches).
# The -auto tasks (2026-10-19) are the same prompts saved again in Auto mode; the paused first copies (politics-prep,
# politics-morning-fill, gta-reactions-daily, bench-builder, politics-news-desk, clip-results-coach) were deleted on
# 2026-10-26 (backup: ~/Desktop/CLIP FACTORY/Old tasks backup 10.26). clip-fixer is still the fixer's only copy.
TASK_PROMPTS = {"politics": ("hearing-clip-sweep-2", "politics-early-gap-fill", "politics-prep-auto",
                                   "politics-morning-fill-auto", "bench-builder-auto", "politics-news-desk-auto",
                                   "clip-coach", "clip-weekly-report", "clip-results-coach-auto", "clip-fixer"),
                "gaming": ("gaming-morning-sweep", "gta-reactions-daily-auto", "bench-builder-auto", "clip-coach",
                              "clip-weekly-report", "clip-results-coach-auto", "clip-fixer")}
GUIDES = ("Reference/hearing-clip-sweep.md", "START HERE.html", "WORKFLOW.html", "SETUP.html")
# a task whose installed SKILL.md is only a pointer ("Read and follow ~/clip-factory/prompts/coach.md", GO-LIVE step 8):
# its rules live in the prompt it points to, so that file is read too once the task is installed (review 2026-10-20)
PROMPTS = C.ROOT / "prompts"
TASK_PROMPT_FILES = {"clip-coach": "coach.md", "clip-weekly-report": "weekly-report.md",
                     "clip-results-coach-auto": "results-coach.md", "clip-fixer": "fixer.md",
                     "politics-news-desk-auto": "news-desk.md"}


def doc_surfaces(cfg: C.Config, tasks: Optional[Path] = None,
                 prompts: Optional[Path] = None) -> Dict[str, List[Path]]:
    """account slug -> the files outside ~/clip-factory/hermes that state its rules and exist: its scheduled-task
    prompts (and, for a task that points at ~/clip-factory/prompts, the prompt file it points to) and the guides in
    its account folder."""
    tasks = tasks or TASKS
    prompts = prompts or PROMPTS
    out: Dict[str, List[Path]] = {}
    for slug, acct in cfg.accounts.items():
        files: List[Path] = []
        for t in TASK_PROMPTS.get(slug, ()):
            files.append(tasks / t / "SKILL.md")
            if t in TASK_PROMPT_FILES and (tasks / t / "SKILL.md").is_file():   # not installed yet: skipped
                files.append(prompts / TASK_PROMPT_FILES[t])
        files += [acct.account_dir / g for g in GUIDES]
        out[slug] = [f for f in files if f.is_file()]
    return out


def retired_words(text: str) -> List[str]:
    """Each retired rule the text still states (an empty list when it states none)."""
    return [why for pat, why in RETIRED if re.search(pat, text, re.I)]


def drift(cfg: C.Config, scratch: Optional[Path] = None) -> List[tuple]:
    """(good, text, fix) for every way the bots' instructions can fall out of step: the installed SOUL.md and runbook
    skills against ~/clip-factory/hermes, an agent job's prompt against JOBS, and each account's length band in
    accounts.json against the words its SOUL.md, bot runbook and nightly prompt use."""
    out: List[tuple] = []
    sync_fix = "python3 ~/clip-factory/factory.py hermes sync"
    for name in PROFILES:
        home = _profile_home(name, scratch)
        if not home.exists():
            continue
        srcs = [HERE / name / "SOUL.md"] + sorted((HERE / name / "skills").glob("*/SKILL.md"))
        stale = [s.relative_to(HERE / name) for s in srcs
                 if not (home / s.relative_to(HERE / name)).exists()
                 or (home / s.relative_to(HERE / name)).read_bytes() != s.read_bytes()]
        out.append((not stale, f"{name}: installed SOUL + {len(srcs) - 1} skills match ~/clip-factory/hermes"
                    + (f" (stale: {', '.join(map(str, stale))})" if stale else ""), sync_fix))
        have = {j.get("name"): j for j in _jobs(name, scratch)}
        from . import timer
        for job in JOBS:
            if job["profile"] != name or not job.get("prompt"):
                continue
            if not scratch and timer.owns_hermes_job(job["name"]):
                continue                       # runs elsewhere now; sync leaves its paused Hermes copy alone too
            got = (have.get(job["name"]) or {}).get("prompt") or ""
            out.append((job["prompt"] in got, f"{name}: job {job['name']} runs tonight's prompt", sync_fix))
    for slug, acct in cfg.accounts.items():
        texts = {p.relative_to(C.ROOT): p.read_text() for p in
                 [HERE / slug / "SOUL.md"] + sorted((HERE / slug / "skills").glob("*-bot/SKILL.md")) if p.exists()}
        for job in JOBS:
            if job["profile"] == slug and job.get("prompt"):
                texts[f"hermes.py {job['name']} prompt"] = job["prompt"]
        for where, text in texts.items():
            wrong = _band_words(text, acct.min_seconds, acct.max_seconds)
            out.append((not wrong, f"{acct.name}: {where} states {acct.min_seconds:.0f} to {acct.max_seconds:.0f}s"
                        + (f" ({'; '.join(wrong)})" if wrong else ""),
                        f"edit it to say {acct.min_seconds:.0f} to {acct.max_seconds:.0f} seconds (config/accounts.json)"))
    # the task prompts and account guides: no rule that ended (read-only; a file that cannot be read is skipped)
    for slug, files in doc_surfaces(cfg, (scratch / "scheduled-tasks") if scratch else None).items():
        bad = []
        for f in files:
            try:
                hits = retired_words(f.read_text(errors="replace"))
            except OSError:
                continue
            bad += [f"{f.name}: {h}" for h in hits]
        out.append((not bad, f"{cfg.accounts[slug].name}: {len(files)} task prompts and guides state no retired rule"
                    + (f" ({'; '.join(bad)})" if bad else ""),
                    "edit those lines to today's rule (ten a day, 07:00-23:00, one Approve)"))
    return out


def _sched(j: dict) -> str:
    s = j.get("schedule")
    if isinstance(s, dict):
        return s.get("expr") or s.get("display") or s.get("value") or json.dumps(s)
    return str(s or j.get("schedule_display") or "")


def remove(scratch: Optional[Path] = None) -> List[str]:
    out = []
    for name in PROFILES:
        if not _profile_home(name, scratch).exists():
            out.append(f"profile {name}: not installed")
            continue
        r = _hermes(["profile", "delete", name, "-y"], scratch)
        out.append(f"profile {name}: {'removed' if r.returncode == 0 else 'FAILED ' + (r.stdout + r.stderr)[-500:]}")
    return out
