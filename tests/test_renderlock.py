import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""The render queue (clipfactory/renderlock.py, next idea 5), offline: one holder at a time, turns in the order they
asked, a crashed holder never blocks the night, a hard-killed `run` whose command lives on still counts, --until (and
CLIP_RENDERLOCK_UNTIL) and --wait, the time limit, re-entry, signals, exit codes, and where a held queue shows (status,
doctor, the review page) and who takes it (the GTA maker's intake and renders, igcut). Throwaway state folders and tiny
shell commands only: nothing renders."""
import contextlib
import json
import shutil
import signal
import subprocess
import sys
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B          # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import makers as M         # noqa: E402
from clipfactory import renderlock as RL    # noqa: E402

PY = sys.executable
# a second process that takes its turn, writes its name to a file while it holds, holds for a while, lets go
TAKER = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from clipfactory import renderlock as RL
acct, what, mark, secs = sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])
with RL.hold(acct, what, wait_s=None):
    with open(mark, "a") as f:
        f.write(what + "\n")
    time.sleep(secs)
"""
# a process that takes the lock and dies without letting go (kill -9 of itself)
CRASH = r"""
import os, signal, sys, time
sys.path.insert(0, sys.argv[1])
from clipfactory import renderlock as RL
with RL.hold("gaming", "crashes mid-render", wait_s=None):
    open(sys.argv[2], "w").write("held")
    os.kill(os.getpid(), signal.SIGKILL)
"""


def wait_for(pred, timeout=15.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        self._state = C.STATE
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-rq-"))
        C.STATE = self.tmp / "state"
        C.STATE.mkdir(parents=True)
        self.env = dict(os.environ, CLIP_FACTORY_STATE=str(C.STATE), CLIP_FACTORY_OFFLINE="1")
        self.env.pop(RL.TOKEN_ENV, None)
        self.env.pop(RL.UNTIL_ENV, None)
        self._until_env = os.environ.pop(RL.UNTIL_ENV, None)
        self.procs = []
        self._poll = RL.POLL_S
        RL.POLL_S = 0.1

    def tearDown(self):
        RL.POLL_S = self._poll
        os.environ.pop(RL.UNTIL_ENV, None)
        if self._until_env is not None:
            os.environ[RL.UNTIL_ENV] = self._until_env
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
            if p.stdout:
                p.stdout.close()
        C.STATE = self._state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def spawn(self, code, *args, **kw):
        p = subprocess.Popen([PY, "-c", code, str(ROOT), *map(str, args)], env=dict(self.env, **kw),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.procs.append(p)
        return p

    def factory(self, *args, timeout=60):
        return subprocess.run([PY, str(ROOT / "factory.py"), *args], env=self.env, capture_output=True, text=True,
                              timeout=timeout)

    def holder(self) -> dict:
        return RL._read(RL.root() / "holder.json")


class Queue(Base):
    def test_one_holder_then_free(self):
        self.assertEqual(RL.status()["state"], "free")
        with RL.hold("gaming", "intake: iShowSpeed") as t:
            self.assertTrue(t.locked)
            st = RL.status()
            self.assertEqual(st["state"], "busy")
            self.assertEqual((st["holder"]["account"], st["holder"]["what"]), ("gaming", "intake: iShowSpeed"))
            self.assertNotIn("token", st["holder"])                     # status never shows the token
            self.assertEqual(st["holder"]["pid"], os.getpid())
        st = RL.status()
        self.assertEqual((st["state"], st["holder"], st["stale"]), ("free", None, None))
        events = [e["event"] for e in RL.recent(10)]
        self.assertEqual(events[-2:], ["took", "left"])

    def test_a_second_process_waits_its_turn(self):
        mark = self.tmp / "order.txt"
        with RL.hold("politics", "patel: render"):
            p = self.spawn(TAKER, "gaming", "intake", mark, 0)
            self.assertTrue(wait_for(lambda: RL.status()["waiting"]))
            time.sleep(0.5)
            self.assertFalse(mark.exists())                           # still waiting while this one holds
            self.assertEqual(RL.status()["waiting"][0]["what"], "intake")
        p.wait(timeout=20)
        self.assertEqual(mark.read_text(), "intake\n")
        self.assertEqual(RL.status()["state"], "free")

    def test_turns_go_in_the_order_asked(self):
        mark = self.tmp / "order.txt"
        with RL.hold("politics", "first"):
            for i, name in enumerate(("second", "third", "fourth")):
                self.spawn(TAKER, "gaming", name, mark, 0.2)
                self.assertTrue(wait_for(lambda: len(RL.status()["waiting"]) == i + 1))
        for p in self.procs:
            p.wait(timeout=30)
        self.assertEqual(mark.read_text().split(), ["second", "third", "fourth"])

    def test_a_gta_hand_pick_goes_first_after_05(self):
        # 10-25: the 21:00 and 23:00 hand picks queued behind a Politics re-cut and gave up at 05:50 (row 5)
        from datetime import datetime
        from unittest import mock
        q = RL.root() / "queue"
        q.mkdir(parents=True, exist_ok=True)
        for i, (acct, what) in enumerate([("politics", "re-cut 0750"), ("gaming", "2100 by hand"),
                                          ("gaming", "make 0700")]):
            RL._write(q / f"{i:020d}-{os.getpid()}-a.json", {"pid": os.getpid(), "account": acct, "what": what})
        at = lambda hm: datetime.now().astimezone().replace(hour=int(hm[:2]), minute=int(hm[3:])).timestamp()
        with mock.patch.object(RL, "_now", lambda: at("05:10")):
            self.assertEqual([t["what"] for _, t, _ in RL._tickets()], ["2100 by hand", "re-cut 0750", "make 0700"])
        with mock.patch.object(RL, "_now", lambda: at("02:10")):
            self.assertEqual([t["what"] for _, t, _ in RL._tickets()], ["re-cut 0750", "2100 by hand", "make 0700"])

    def test_a_crashed_holder_never_blocks_the_night(self):
        flag = self.tmp / "held"
        p = self.spawn(CRASH, flag)
        p.wait(timeout=20)
        self.assertEqual(p.returncode, -signal.SIGKILL)
        self.assertTrue(flag.exists())
        st = RL.status()
        self.assertEqual(st["state"], "free")                         # the kernel let the lock go with the process
        self.assertEqual(st["stale"]["what"], "crashes mid-render")    # its record is shown, and ignored
        self.assertTrue(any("ended without letting go" in ln for ln in RL.lines(None, st)))
        t0 = time.time()
        with RL.hold("politics", "next", wait_s=5) as t:
            self.assertTrue(t.locked)
        self.assertLess(time.time() - t0, 2)

    def test_a_dead_or_stopped_waiter_loses_its_place(self):
        q = RL.root() / "queue"
        q.mkdir(parents=True)
        dead = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
        dead.wait()
        (q / f"{1:020d}-{dead.pid}-aaaaaa.json").write_text(json.dumps({"pid": dead.pid, "what": "gone"}))
        stopped = subprocess.Popen(["/bin/sleep", "30"])
        self.procs.append(stopped)
        old = q / f"{2:020d}-{stopped.pid}-bbbbbb.json"
        old.write_text(json.dumps({"pid": stopped.pid, "what": "frozen"}))
        os.utime(old, (time.time() - 600, time.time() - 600))      # not refreshed for ten minutes
        t0 = time.time()
        with RL.hold("gaming", "intake", wait_s=5) as t:
            self.assertTrue(t.locked)
        self.assertLess(time.time() - t0, 2)
        self.assertFalse((q / f"{1:020d}-{dead.pid}-aaaaaa.json").exists())    # a dead waiter's ticket is removed
        self.assertTrue(old.exists())                                          # a stopped one keeps its place

    def test_until_is_a_deadline_to_start(self):
        mark = self.tmp / "m"
        p = self.spawn(TAKER, "gaming", "long intake", mark, 30)
        self.assertTrue(wait_for(mark.exists))
        with self.assertRaises(RL.Busy):
            with RL.hold("politics", "render", until=time.time() + 0.5):
                self.fail("it must not run")
        self.assertEqual(RL.status()["waiting"], [])                  # its ticket went with it
        self.assertEqual(RL.recent(1)[0]["event"], "gave-up")
        out = self.tmp / "ran"
        hhmm = (datetime.now() - timedelta(minutes=1)).strftime("%H:%M")       # just passed: nothing new starts
        r = self.factory("renderlock", "run", "--account", "politics", "--what", "late", "--until", hhmm,
                         "--", "/usr/bin/touch", str(out))
        self.assertEqual(r.returncode, RL.BUSY_EXIT, r.stdout + r.stderr)
        self.assertFalse(out.exists())
        self.assertIn("not run", r.stdout)
        p.kill()

    def test_until_never_starts_late_even_on_a_free_queue(self):
        self.assertEqual(RL.status()["state"], "free")
        with self.assertRaises(RL.Busy) as cm:
            with RL.hold("politics", "render", until=time.time() - 60):
                self.fail("it must not run")
        self.assertIn("past its", str(cm.exception))
        out = self.tmp / "ran"
        hhmm = (datetime.now() - timedelta(minutes=10)).strftime("%H:%M")
        r = self.factory("renderlock", "run", "--account", "politics", "--what", "late", "--until", hhmm,
                         "--", "/usr/bin/touch", str(out))
        self.assertEqual(r.returncode, RL.BUSY_EXIT, r.stdout + r.stderr)
        self.assertFalse(out.exists())
        self.assertIn(f"past its {hhmm} deadline", r.stdout)
        self.assertEqual(r.stdout.count("not run"), 1, r.stdout)            # said once
        self.assertEqual(RL.status()["state"], "free")
        ahead = (datetime.now() + timedelta(minutes=30)).strftime("%H:%M")  # still ahead: it runs
        r = self.factory("renderlock", "run", "--account", "politics", "--what", "early", "--until", ahead,
                         "--", "/usr/bin/touch", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(out.exists())

    def test_the_deadline_from_the_environment(self):
        os.environ[RL.UNTIL_ENV] = (datetime.now() - timedelta(minutes=5)).strftime("%H:%M")
        with self.assertRaises(RL.Busy):
            with RL.hold("gaming", "make"):
                self.fail("it must not run")

        class Mine(SystemExit):
            pass
        with self.assertRaises(Mine):                                      # the caller's own error (igcut: CutError)
            with RL.hold("gaming", "make", busy=Mine):
                self.fail("it must not run")
        os.environ[RL.UNTIL_ENV] = str(int(time.time() + 600))             # epoch, as `run --until` hands it on
        with RL.hold("gaming", "make") as t:
            self.assertTrue(t.locked)
        os.environ[RL.UNTIL_ENV] = "not a time"                            # unreadable: no deadline, never a lost clip
        with RL.hold("gaming", "make") as t:
            self.assertTrue(t.locked)
        os.environ.pop(RL.UNTIL_ENV)
        # `run --until` hands the deadline to its command, so a `make --background` it starts keeps it
        ahead = (datetime.now() + timedelta(minutes=30)).strftime("%H:%M")
        r = self.factory("renderlock", "run", "--account", "gaming", "--what", "w", "--until", ahead, "--",
                         PY, "-c", f"import os; print('UNTIL=' + os.environ.get({RL.UNTIL_ENV!r}, ''))")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = float(r.stdout.split("UNTIL=")[1].split()[0])
        self.assertAlmostEqual(got, RL.until_epoch(ahead), delta=1)

    def test_until_picks_the_nearest_time(self):
        at = lambda h, m: datetime(2026, 10, 17, h, m).astimezone()  # noqa: E731
        self.assertEqual(datetime.fromtimestamp(RL.until_epoch("04:50", at(21, 0))).strftime("%d %H:%M"), "18 04:50")
        self.assertEqual(datetime.fromtimestamp(RL.until_epoch("04:50", at(2, 43))).strftime("%d %H:%M"), "17 04:50")
        self.assertLess(RL.until_epoch("04:50", at(4, 55)), at(4, 55).timestamp())       # just passed
        self.assertLess(RL.until_epoch("23:30", at(1, 0)), at(1, 0).timestamp())         # last night
        with self.assertRaises(ValueError):
            RL.until_epoch("25:00")

    def test_patience_runs_it_beside_the_holder_and_says_so(self):
        mark = self.tmp / "m"
        p = self.spawn(TAKER, "gaming", "long intake", mark, 30)
        self.assertTrue(wait_for(mark.exists))
        said = []
        with RL.hold("politics", "render", wait_s=0.3, say=said.append) as t:
            self.assertTrue(t.beside and not t.locked)
            st = RL.status()
            self.assertEqual([b["what"] for b in st["beside"]], ["render"])
            self.assertEqual(st["holder"]["what"], "long intake")
            self.assertTrue(any("beside the queue" in ln for ln in RL.lines(None, st)))
        self.assertTrue(any("running anyway beside" in s for s in said), said)
        self.assertEqual(RL.status()["beside"], [])
        p.kill()

    def test_reentry_never_waits_on_itself(self):
        with RL.hold("gaming", "outer") as a:
            with RL.hold("gaming", "inner", wait_s=1) as b:     # same process
                self.assertTrue(a.locked and b.nested)
        # a wrapped command that queues again (a factory step inside renderlock run) runs inside its parent's turn
        inner = ("import sys; sys.path.insert(0, %r); from clipfactory import renderlock as RL\n"
                 "with RL.hold('politics', 'inner', wait_s=1) as t:\n    print('nested' if t.nested else 'NOT')"
                 % str(ROOT))
        r = self.factory("renderlock", "run", "--account", "politics", "--what", "outer", "--", PY, "-c", inner)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nested", r.stdout)

    def test_run_passes_the_exit_status_and_lets_go(self):
        r = self.factory("renderlock", "run", "--account", "gaming", "--what", "w", "--", "/bin/sh", "-c", "exit 3")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(RL.status()["state"], "free")
        r = self.factory("renderlock", "run", "--account", "gaming", "--what", "w")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("put the command after --", r.stderr)
        r = self.factory("renderlock", "run", "--account", "nobody", "--what", "w", "--", "true")
        self.assertIn("unknown account", r.stderr)

    def test_the_time_limit_stops_the_command_and_the_queue_moves_on(self):
        mark = self.tmp / "child.pid"
        t0 = time.time()
        r = self.factory("renderlock", "run", "--account", "gaming", "--what", "hangs", "--max-hold", "0.02",
                         "--", "/bin/sh", "-c", f"sleep 60 & echo $! > {mark}; wait")
        self.assertEqual(r.returncode, RL.TIMEOUT_EXIT, r.stdout + r.stderr)
        self.assertLess(time.time() - t0, 30)
        self.assertIn("was stopped", r.stdout)
        child = int(mark.read_text())
        self.assertTrue(wait_for(lambda: not RL._alive(child), 12))      # the whole group was stopped
        self.assertEqual(RL.status()["state"], "free")
        self.assertEqual(RL.recent(2)[0]["event"], "stopped")

    def test_a_stopped_wrapper_stops_its_command_and_lets_go(self):
        mark = self.tmp / "child.pid"
        p = subprocess.Popen([PY, str(ROOT / "factory.py"), "renderlock", "run", "--account", "gaming", "--what",
                              "killed", "--", "/bin/sh", "-c", f"sleep 60 & echo $! > {mark}; wait"], env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.procs.append(p)
        self.assertTrue(wait_for(lambda: mark.exists() and mark.read_text().strip() and self.holder().get("child")))
        child = int(mark.read_text())
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=30)
        self.assertEqual(p.returncode, 128 + signal.SIGTERM)
        self.assertTrue(wait_for(lambda: not RL._alive(child), 12))
        self.assertEqual(RL.status()["state"], "free")
        self.assertEqual(self.holder(), {})

    def test_a_hard_killed_run_whose_command_lives_on_still_counts(self):
        mark = self.tmp / "child.pid"
        p = subprocess.Popen([PY, str(ROOT / "factory.py"), "renderlock", "run", "--account", "gaming", "--what",
                              "late render", "--", "/bin/sh", "-c", f"echo $$ > {mark}; sleep 60"], env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.procs.append(p)
        self.assertTrue(wait_for(lambda: mark.exists() and mark.read_text().strip() and self.holder().get("child")))
        child = int(mark.read_text())
        try:
            self.assertEqual(self.holder()["child"], child)
            self.assertTrue(self.holder().get("child_start"))
            p.kill()                                                        # kill -9: no chance to stop its command
            p.wait(timeout=10)
            self.assertTrue(RL._alive(child))
            st = RL.status()
            self.assertEqual(st["state"], "busy")
            self.assertTrue(st["holder"]["orphan"])
            self.assertIn(f"pid {child} still runs", RL.lines(None, st)[0])
            level, words = RL.health_words(None)
            self.assertEqual(level, "busy")
            self.assertIn(f"its command (pid {child}) still runs", words)
            with self.assertRaises(RL.Busy):                                # waiters wait for it, as for a holder
                with RL.hold("politics", "render", wait_s=None, until=time.time() + 1.0):
                    self.fail("it must wait for the orphan")
            # past its time limit: the next one takes its turn, and the orphan shows beside the queue
            h = self.holder()
            h["since_ts"] -= RL.DEFAULT_MAX_HOLD_S + 5
            RL._write(RL.root() / "holder.json", h)
            with RL.hold("politics", "render", wait_s=5) as t:
                self.assertTrue(t.locked)
                st = RL.status()
                self.assertEqual(st["holder"]["what"], "render")
                self.assertEqual([(b["pid"], b["orphan"]) for b in st["beside"]], [(child, True)])
                self.assertFalse(RL.doctor(None)[1][0])                    # doctor flags it, with its pid
                self.assertIn(f"ps -p {child}", RL.doctor(None)[1][2])
            self.assertEqual(RL.health_words(None)[0], "stuck")
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child, signal.SIGKILL)
        self.assertTrue(wait_for(lambda: not RL._alive(child), 10))
        self.assertEqual(RL.status()["beside"], [])
        self.assertEqual(RL.status()["state"], "free")

    def test_a_reused_child_pid_is_not_an_orphan(self):
        RL._write(RL.root() / "holder.json", {"pid": 999999, "child": os.getpid(), "child_start": "Thu Jan  1 00:00:00 1970",
                                              "what": "old", "since_ts": time.time(), "token": "x"})
        st = RL.status()
        self.assertEqual((st["state"], st["holder"]), ("free", None))
        with RL.hold("gaming", "new", wait_s=2) as t:
            self.assertTrue(t.locked)

    def test_exit_codes_and_plain_errors(self):
        r = self.factory("renderlock", "run", "--account", "gaming", "--what", "w", "--", "/no/such/render")
        self.assertEqual(r.returncode, 127, r.stdout + r.stderr)
        self.assertIn("no such command", r.stdout)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertEqual(RL.status()["state"], "free")
        r = self.factory("renderlock", "run", "--account", "gaming", "--what", "w", "--", "/bin/sh", "-c",
                         "kill -9 $$")
        self.assertEqual(r.returncode, 128 + signal.SIGKILL, r.stdout + r.stderr)
        r = self.factory("renderlock", "run", "--account", "gaming", "--what", "w", "--max-hold", "0", "--",
                         "/bin/sh", "-c", "sleep 1.5; exit 4")             # 0 = no limit, as --wait 0
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        r = self.factory("renderlock", "run", "--account", "gaming", "--what", "w", "--wait", "-1", "--", "true")
        self.assertIn("0 or more", r.stderr)
        self.assertEqual(RL.status()["state"], "free")

    def test_a_holder_past_its_limit_shows_as_stuck(self):
        with RL.hold("gaming", "intake: Kai", max_hold_s=60):
            h = self.holder()
            h["since_ts"] -= 60 + RL.STUCK_GRACE_S + 5
            RL._write(RL.root() / "holder.json", h)
            st = RL.status()
            self.assertEqual(st["state"], "stuck")
            level, words = RL.health_words(None)
            self.assertEqual(level, "stuck")
            self.assertIn("stuck", words)
            good, text, fix = RL.doctor(None)[0]
            self.assertFalse(good)
            self.assertIn(f"ps -p {os.getpid()}", fix)

    def test_a_record_with_a_reused_pid_is_not_a_holder(self):
        # holder.json names a live process (this one) that does not hold the lock: the probe sees the lock is free
        RL._write(RL.root() / "holder.json", {"pid": os.getpid(), "what": "old", "since_ts": time.time() - 99999,
                                              "token": "x"})
        st = RL.status()
        self.assertEqual((st["state"], st["holder"]), ("free", None))
        self.assertEqual(st["stale"]["what"], "old")
        with RL.hold("gaming", "new", wait_s=2) as t:
            self.assertTrue(t.locked)
            self.assertEqual(self.holder()["what"], "new")

    def test_the_queue_folder_failing_never_costs_a_clip(self):
        with mock.patch.object(RL, "_acquire", side_effect=PermissionError("denied")):
            said = []
            with RL.hold("gaming", "intake", say=said.append) as t:
                ran = True
        self.assertTrue(ran and not t.locked)
        self.assertIn("running without it", said[0])

    def test_status_json_and_words(self):
        r = self.factory("renderlock", "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("free", r.stdout)
        with RL.hold("politics", "patel: render"):
            r = self.factory("renderlock", "status", "--json")
            st = json.loads(r.stdout)
            self.assertEqual(st["holder"]["what"], "patel: render")
            r = self.factory("renderlock", "status")
            self.assertIn("rendering: Politics, patel: render", r.stdout)


# ------------------------------------------------------------------------------------------------ where it shows
class Shows(Base):
    def cfg(self):
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "drafts")
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        return C.load(p)

    def test_the_review_page_says_what_is_rendering(self):
        from clipfactory import review as R
        cfg = self.cfg()
        now = cfg.now()
        self.assertNotIn("Rendering now", R._needs(cfg, now)["head"])
        with RL.hold("gaming", "intake: iShowSpeed"):
            head = R._needs(cfg, now)["head"]
            self.assertIn("Rendering now: Gaming, intake: iShowSpeed", head)
            self.assertIn('class="calm health"', head)
            h = self.holder()
            h["since_ts"] -= RL.DEFAULT_MAX_HOLD_S + RL.STUCK_GRACE_S + 5
            RL._write(RL.root() / "holder.json", h)
            head = R._needs(cfg, now)["head"]
            self.assertIn('class="alert health" role="alert">The render queue is stuck', head)
        self.assertNotIn("Rendering now", R._needs(cfg, now)["head"])

    def test_doctor_has_a_render_queue_line(self):
        import argparse
        import contextlib
        import io
        import factory as F
        from clipfactory import hermes as H
        from clipfactory import timer as TM
        cfg = self.cfg()

        def doctor() -> str:
            buf = io.StringIO()
            with mock.patch.object(TM, "status", return_value=[]), mock.patch.object(H, "status", return_value=[]), \
                    mock.patch.object(H, "drift", return_value=[]), contextlib.redirect_stdout(buf):
                F.cmd_doctor(cfg, argparse.Namespace(fix_paths=False))
            return buf.getvalue()
        out = doctor()
        self.assertIn("render queue (state/renderlock", out)
        self.assertIn("ok  render queue: free", out)
        with RL.hold("politics", "patel: render"):
            self.assertIn("ok  render queue: rendering Politics, patel: render", doctor())


class Takers(Base):
    """The factory's own heavy steps take the queue: the GTA intake, each clip's render, igcut's render."""

    def gta(self):
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "drafts")
        a = raw["accounts"]["gaming"]
        a.pop("gta_maker", None)
        a["account_dir"] = str(self.tmp / "Gaming")
        a["skill_dir"] = str(self.tmp / "skill")
        a["delivered"] = [str(self.tmp / "delivered" / "{YYYY}{MM}{DD}")]
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        cfg = C.load(p)
        acct = cfg.account("gaming")
        for d in ("skill/work", "skill/scripts", "Gaming/Publish"):
            (self.tmp / d).mkdir(parents=True)
        up = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = {f"vid{n:08d}": {"url": f"https://www.youtube.com/watch?v=vid{n:08d}", "status": "new",
                                "kind": "breakdown", "score": 100 - n, "channel": f"ch{n}", "name": f"Creator {n}",
                                "title": f"upload {n}", "duration": 900, "uploaded": up} for n in (1,)}
        (acct.account_dir / "Publish" / "inbox.json").write_text(json.dumps({"items": rows}))
        return cfg, acct

    def test_the_gta_intake_and_each_render_hold_the_queue(self):
        cfg, acct = self.gta()
        seen = []

        def fake_run(cmd, log, cwd=None, env=None, timeout=0):
            st = RL.status()
            step = "intake" if "--job" in cmd else "pick" if "--pick" in cmd else cmd[1]
            seen.append((step, st["holder"]["what"] if st["holder"] else None))
            return subprocess.CompletedProcess(cmd, 0)

        day = date(2030, 1, 7)
        with B.edit(cfg, acct, day):
            pass
        M._HELD.discard("gaming")
        with mock.patch.object(M, "run_logged", fake_run), \
                mock.patch.object(M, "pack_gta_slot", lambda a, d, s, l, e: f"  {s}: packed"), \
                mock.patch.object(M.K, "collect", lambda *a, **k: []):
            M.make_gta(cfg, acct, day, only=["07:00"])
        self.assertEqual(seen[0], ("watch.py", None))                   # the watchlist scan is light: no turn
        self.assertEqual(seen[1], ("intake", "intake: Creator 1"))
        self.assertEqual(seen[2][0], "pick")
        self.assertTrue(seen[2][1].startswith("clip 07:00 (candidate "), seen)
        self.assertEqual(RL.status()["state"], "free")
        log = (C.STATE / "gaming" / "logs" / f"{day.isoformat()}-make.log")
        self.assertTrue(log.parent.is_dir())

    def test_a_make_past_its_deadline_fails_its_slots_plainly(self):
        # a gap-fill starts `make` with CLIP_RENDERLOCK_UNTIL=04:50: a turn that would start later never starts, and
        # the slot says why (failed, never stuck "making"); the source stays in the inbox for the next run
        cfg, acct = self.gta()
        ran = []

        def fake_run(cmd, log, cwd=None, env=None, timeout=0):
            ran.append(cmd[1])
            return subprocess.CompletedProcess(cmd, 0)

        day = date(2030, 1, 7)
        with B.edit(cfg, acct, day):
            pass
        M._HELD.discard("gaming")
        os.environ[RL.UNTIL_ENV] = (datetime.now() - timedelta(minutes=5)).strftime("%H:%M")
        with mock.patch.object(M, "run_logged", fake_run), \
                mock.patch.object(M, "pack_gta_slot", lambda a, d, s, l, e: f"  {s}: packed"), \
                mock.patch.object(M.K, "collect", lambda *a, **k: []):
            M.make_gta(cfg, acct, day, only=["07:00"])
        self.assertEqual(ran, ["watch.py"])                               # no intake, no render
        slot = B.read(acct, day)["slots"]["07:00"]
        self.assertEqual(slot["status"], B.FAILED)
        self.assertIn("deadline", json.dumps(slot))
        inbox = json.loads((acct.account_dir / "Publish" / "inbox.json").read_text())
        self.assertEqual([r["status"] for r in inbox["items"].values()], ["new"])

    def test_igcut_renders_in_its_turn(self):
        from clipfactory import igcut as IG
        src = (ROOT / "clipfactory" / "igcut.py").read_text()
        self.assertIn('with RL.hold(acct.slug, f"cut: {clipd[\'slug\']}", say=log, busy=CutError), open(logf, "w") as lf:',
                      src)
        # the render call itself, with everything around it faked: it runs while the queue is held for the cut
        seen = {}
        real_run = subprocess.run

        def fake_run(cmd, *a, **kw):
            if len(cmd) > 1 and cmd[1] == "build_all.py":
                st = RL.status()
                seen["holder"] = (st["holder"] or {}).get("what")
                out = Path(kw["env"]["CLIP_WORK"]) / "out-ig" / "clips"
                out.mkdir(parents=True, exist_ok=True)
                (out / f"01-{cmd[2]}.mp4").write_bytes(b"x")
                return subprocess.CompletedProcess(cmd, 0)
            return real_run(cmd, *a, **kw)

        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "drafts")
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        cfg = C.load(p)
        acct = cfg.account("politics")
        work = self.tmp / "work"
        work.mkdir()
        (work / "master.mp4").write_bytes(b"m")
        (work / "project.json").write_text("{}")
        job = self.tmp / "job"
        job.mkdir()
        (job / "project.json").write_text(json.dumps({"source": str(work / "master.mp4")}))
        (job / "slate.json").write_text(json.dumps({"clips": [{"slug": "s", "start": 10.0, "end": 80.0}]}))
        folder = self.tmp / "0700 s"
        folder.mkdir()
        lo, hi = IG.O.band(acct, IG.PLAT)
        with mock.patch.object(IG, "find_delivery", return_value=folder), \
                mock.patch.object(IG, "find_job", return_value=job), \
                mock.patch.object(IG, "_gate", return_value=""), \
                mock.patch.object(IG.subprocess, "run", fake_run), \
                mock.patch.object(IG.K, "probe_seconds", return_value=(lo + hi) / 2), \
                mock.patch.object(IG.K, "collect", return_value=[]):
            out = IG.render(cfg, acct, date(2030, 1, 7), "s", end=10.0 + (lo + hi) / 2, python=PY, log=lambda *a: None)
            self.assertEqual(seen.pop("holder"), "cut: s-ig")
            self.assertTrue((folder / "instagram.mp4").exists(), out)
            self.assertEqual(RL.status()["state"], "free")
            # started with CLIP_RENDERLOCK_UNTIL past: igcut's own plain error, and nothing renders
            os.environ[RL.UNTIL_ENV] = (datetime.now() - timedelta(minutes=5)).strftime("%H:%M")
            with self.assertRaises(IG.CutError) as cm:
                IG.render(cfg, acct, date(2030, 1, 7), "s", end=10.0 + (lo + hi) / 2, python=PY, log=lambda *a: None)
        self.assertIn("not run", str(cm.exception.code))
        self.assertNotIn("holder", seen)


if __name__ == "__main__":
    unittest.main()
