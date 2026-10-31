"""The launchd runner (clipfactory/timer.py), on throwaway folders: no real launchctl, no real Hermes, no real posts."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)

import fcntl
import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C     # noqa: E402
from clipfactory import hermes as H     # noqa: E402
from clipfactory import poster as P     # noqa: E402
from clipfactory import timer as TM     # noqa: E402

JOB_IDS = {"factory-post-due": "bd01", "factory-inbox": "04a7", "factory-stats": "cc63", "gta-nightly": "3f57",
           "a-bot-chat-job": "9z99"}   # the last one is no twin of any agent: it must never be touched
TWINS = [n for n in JOB_IDS if n != "a-bot-chat-job"]


class FakeLaunchctl:
    def __init__(self):
        self.calls, self.loaded = [], {}

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "bootstrap":
            lab = plistlib.loads(Path(args[2]).read_bytes())["Label"]
            self.loaded[lab] = True
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "bootout":
            gone = self.loaded.pop(args[1].rsplit("/", 1)[-1], None)
            return subprocess.CompletedProcess(args, 0 if gone else 3, "", "" if gone else "No such process")
        if args[0] == "print":
            lab = args[1].rsplit("/", 1)[-1]
            if lab not in self.loaded:
                return subprocess.CompletedProcess(args, 113, "", "Could not find service")
            return subprocess.CompletedProcess(args, 0, f"{lab} = {{\n\tstate = not running\n\truns = 1\n"
                                                        "\tlast exit code = 0\n}\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")


class TimerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-timer-"))
        self.agents = self.tmp / "LaunchAgents"
        self.scratch = self.tmp / "hermes"
        self.jobs = self.scratch / "profiles" / "gaming" / "cron" / "jobs.json"
        self.jobs.parent.mkdir(parents=True)
        self.write_jobs(paused=False)
        self.old_state, C.STATE = C.STATE, self.tmp / "state"
        from clipfactory import direct as D, outlets as O
        self.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = self.tmp / "no-keys"      # never a real sign-in from a test, even offline
        self.old_hermes = H._hermes
        self.hermes_calls = []
        H._hermes = self.fake_hermes
        self.lc = FakeLaunchctl()

    def tearDown(self):
        C.STATE = self.old_state
        from clipfactory import direct as D, outlets as O
        O.TOKENS, D.TOKENS = self.old_tokens
        H._hermes = self.old_hermes

    def write_jobs(self, paused: bool, fail_pause: bool = False):
        self.fail_pause = fail_pause
        self.jobs.write_text(json.dumps({"jobs": [
            {"id": i, "name": n, "enabled": not (paused and n in TWINS), "state": "scheduled"}
            for n, i in JOB_IDS.items()]}))

    def fake_hermes(self, args, scratch, timeout=180):
        self.hermes_calls.append(args)
        if args[0] == "profile":
            return subprocess.CompletedProcess(args, 0, "installed", "")
        if args[2:4] == ["cron", "pause"] and not self.fail_pause:
            data = json.loads(self.jobs.read_text())
            for j in data["jobs"]:
                if j["id"] == args[4]:
                    j["enabled"], j["state"] = False, "paused"
            self.jobs.write_text(json.dumps(data))
            return subprocess.CompletedProcess(args, 0, "paused", "")
        return subprocess.CompletedProcess(args, 1, "", "nope")

    def install(self, **kw):
        kw.setdefault("preflight", lambda: ["exit 0 after 1 run(s); log x:", "  | reads drafts d: yes (3 entries)"])
        return TM.install(None, scratch=self.scratch, agents_dir=self.agents, launchctl=self.lc, **kw)

    def test_1_plists_run_usr_bin_python_on_the_right_clock_with_logs_in_state(self):
        by = {a["name"]: TM.plist(a) for a in TM.AGENTS}
        self.assertTrue({"post-due", "inbox", "stats", "gta-nightly", "gta-scout", "politics-scout", "politics-collect",
                         "morning"} <= set(by), set(by))
        for name, p in by.items():
            self.assertEqual(p["Label"], f"com.clipfactory.{name}")
            # Python.app's own binary, not the /usr/bin/python3 shim: macOS privacy refuses the shim the Desktop
            self.assertEqual(p["ProgramArguments"][:2], [TM.PY, str(ROOT / "factory.py")])
            self.assertTrue(TM.PY.endswith("/Python.app/Contents/MacOS/Python"))
            self.assertEqual(p["StandardOutPath"], str(self.tmp / "state" / "logs" / f"{name}.log"))
            self.assertIn("/opt/homebrew/bin", p["EnvironmentVariables"]["PATH"])
            self.assertEqual(p["RunAtLoad"], name == "review")   # only the always-on review page starts at login
        self.assertTrue(by["review"]["KeepAlive"])
        self.assertEqual(by["review"]["ProgramArguments"][2:], ["review"])
        self.assertEqual(by["morning"]["StartCalendarInterval"], {"Hour": 6, "Minute": 30})   # he signs about 06:45
        self.assertEqual(by["post-due"]["ProgramArguments"][2:], ["post-due"])
        self.assertEqual(by["post-due"]["StartInterval"], 600)
        self.assertEqual(by["inbox"]["ProgramArguments"][2:], ["inbox", "--quiet"])
        self.assertEqual(by["inbox"]["StartInterval"], 300)
        self.assertNotIn("CLIP_FACTORY_LOG_STAMP", by["inbox"]["EnvironmentVariables"])
        self.assertEqual(by["stats"]["StartCalendarInterval"], {"Hour": 3, "Minute": 30})
        self.assertNotIn("StartInterval", by["stats"])
        # clip making: the nightly cut at 21:00 at full priority, never killed when its starter exits
        self.assertEqual(by["gta-nightly"]["ProgramArguments"][2:], ["nightly", "gaming"])
        self.assertEqual(by["gta-nightly"]["StartCalendarInterval"], {"Hour": 21, "Minute": 0})
        self.assertEqual(by["gta-nightly"]["ProcessType"], "Standard")
        self.assertTrue(by["gta-nightly"]["AbandonProcessGroup"])
        self.assertEqual(len(by["gta-scout"]["StartCalendarInterval"]), 18)          # :40, 05:40 to 22:40
        self.assertEqual(by["politics-collect"]["StartCalendarInterval"], {"Minute": 15})
        self.assertEqual(by["morning"]["ProgramArguments"][2:], ["morning", "--date", "today", "--notify"])
        path = TM.write(TM.AGENTS[0], self.agents)
        r = subprocess.run(["plutil", "-lint", str(path)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_2_refuses_while_hermes_still_runs_the_jobs(self):
        with self.assertRaises(SystemExit) as e:
            self.install()
        self.assertIn("factory-post-due", str(e.exception))
        self.assertIn("--from-hermes", str(e.exception))
        self.assertFalse(self.agents.exists())
        self.assertEqual(self.lc.calls, [])

    def test_3_from_hermes_pauses_the_three_jobs_then_loads_the_agents(self):
        lines = self.install(from_hermes=True)
        paused = sorted(c[4] for c in self.hermes_calls if c[2:4] == ["cron", "pause"])
        self.assertEqual(paused, sorted(JOB_IDS[n] for n in TWINS))
        self.assertEqual(TM.active_twins(self.scratch), [])
        names = json.loads(self.jobs.read_text())["jobs"]
        self.assertTrue(next(j for j in names if j["name"] == "a-bot-chat-job")["enabled"])   # a non-twin is untouched
        every = TM.all_agents(None)        # AGENTS and the live accounts' own (Stunts' day shift, 2026-10-29)
        self.assertEqual(sorted(self.lc.loaded), sorted(TM.label(a) for a in every))
        self.assertEqual(len(list(self.agents.glob("com.clipfactory.*.plist"))), len(every))
        self.assertTrue(all("loaded" in ln for ln in lines if ln.startswith("com.clipfactory")), lines)
        st = TM.status(scratch=self.scratch, agents_dir=self.agents, launchctl=self.lc)
        self.assertTrue(all(ln.startswith("ok ") for ln in st), st)
        old_py, TM.PY = TM.PY, str(self.tmp / "gone" / "Python")    # a Command Line Tools update moved Python
        try:
            st = TM.status(scratch=self.scratch, agents_dir=self.agents, launchctl=self.lc)
        finally:
            TM.PY = old_py
        self.assertTrue(st[0].startswith("NO ") and "missing" in st[0], st)
        # installing again is fine once Hermes is paused (and reloads instead of doubling up)
        self.install()
        self.assertEqual(len(self.lc.loaded), len(every))

    def test_4_a_pause_that_fails_installs_nothing(self):
        self.write_jobs(paused=False, fail_pause=True)
        lines = self.install(from_hermes=True)
        self.assertTrue(any("pause FAILED" in ln for ln in lines))
        self.assertIn("nothing installed", lines[-1])
        self.assertFalse(self.agents.exists())

    def test_4b_a_launchd_job_that_cannot_read_the_desktop_pauses_nothing(self):
        blocked = ["exit 0 after 1 run(s); log x:",
                   "  |   reads drafts /Users/x/Desktop/Clip Drafts: NO (Operation not permitted); give ... Full Disk Access"]
        with self.assertRaises(SystemExit) as e:
            self.install(from_hermes=True, preflight=lambda: blocked)
        self.assertIn("Full Disk Access", str(e.exception))
        self.assertEqual([c for c in self.hermes_calls if c[2:4] == ["cron", "pause"]], [])
        self.assertEqual(len(TM.active_twins(self.scratch)), len(TWINS))
        self.assertFalse(self.agents.exists())
        self.assertTrue(TM.check_failed(["exit 1 after 1 run(s); log x:"]))
        self.assertTrue(TM.check_failed(["exit 0 after 1 run(s); log x:", "  | (no log written)"]))

    def test_5_status_flags_two_runners(self):
        self.write_jobs(paused=True)
        self.install()
        self.write_jobs(paused=False)          # someone resumed the Hermes job by hand
        st = TM.status(scratch=self.scratch, agents_dir=self.agents, launchctl=self.lc)
        with_twin = [ln for ln in st if "not in Hermes" not in ln]
        self.assertEqual(len(with_twin), len(TWINS), st)        # every agent whose Hermes job exists in the fake home
        self.assertTrue(all(ln.startswith("NO ") and "two runners" in ln for ln in with_twin), st)

    def test_6_hermes_sync_never_reschedules_a_job_launchd_owns(self):
        self.write_jobs(paused=True)
        self.install()
        self.assertTrue(TM.owns_hermes_job("factory-post-due", self.agents))
        self.assertTrue(TM.owns_hermes_job("gta-nightly", self.agents))
        self.assertTrue(TM.owns_hermes_job("politics-tonight", self.agents))     # retired to a Claude scheduled task
        self.assertFalse(TM.owns_hermes_job("a-bot-chat-job", self.agents))
        old = TM.AGENTS_DIR
        TM.AGENTS_DIR = self.agents
        try:
            self.jobs.write_text(json.dumps({"jobs": []}))      # the Hermes jobs are gone (a profile reinstall)
            # not a scratch run (the guard is off in rehearsals), so point the profile lookup at the scratch home by hand
            old_home = H._home
            H._home = lambda scratch: self.scratch
            try:
                lines = H.install(None)
            finally:
                H._home = old_home
        finally:
            TM.AGENTS_DIR = old
        for name in ("factory-post-due", "factory-inbox", "factory-stats", "gta-nightly"):
            self.assertIn(f"job {name}: runs on launchd (factory.py timer), not scheduled in Hermes", lines)
        self.assertIn("job politics-tonight: runs on a Claude scheduled task, not scheduled in Hermes", lines)
        created = [c for c in self.hermes_calls if c[2:4] == ["cron", "create"]]
        self.assertEqual(created, [], created)      # every clipping job runs on launchd or Claude now: Hermes gets none

    def test_7_remove_unloads_and_deletes_and_says_how_to_go_back(self):
        self.install(from_hermes=True)
        lines = TM.remove(agents_dir=self.agents, launchctl=self.lc, scratch=self.scratch)
        self.assertEqual(self.lc.loaded, {})
        self.assertEqual(list(self.agents.glob("*.plist")), [])
        self.assertIn(f"hermes -p gaming cron resume {JOB_IDS['factory-post-due']}", lines[-1])

    def test_8_a_second_post_due_stops_while_one_runs(self):
        lock = C.STATE / "post-due.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        cfg = C.load()
        with open(lock, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            out = P.post_due(cfg, posters={})
            self.assertEqual(out, ["  another post-due is running; it goes round again when it is done"])
            self.assertTrue((C.STATE / "post-due.again").exists())     # the busy one is told to go round again
            self.assertNotIn("another", " ".join(P.post_due(cfg, posters={}, dry_run=True)))   # a dry run never waits
        self.assertEqual(P.post_due(cfg, posters={}), [])     # free again: runs (nothing to post with no posters)
        self.assertFalse((C.STATE / "post-due.again").exists())


if __name__ == "__main__":
    unittest.main()
