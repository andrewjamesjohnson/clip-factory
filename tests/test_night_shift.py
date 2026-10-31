import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""The night shift (2026-10-19; Andrew 10-18: "One night shift from 9 PM ..., Politics and GTA take turns, refused clips
try the next video, all 20 done for my sign-off, 7 days a week", "clips done by 6:15 and the notification at 6:30 work
for me"), offline: one clock (clipfactory/shift.py) that every agent, deadline and default agrees with; the posting
day before 07:00; the GTA worker's 05:30 passes, 05:50 last start and its own 06:15 watchdog; an intake whose turn in
the render queue came too late; the render queue's cross-account turn rule and --stop-at; who works a board tonight
(shift begin/check/end/wait/status) and the retry and second halves that wait for it; the 06:18 bench window; the
06:15-08:00 morning freeze. The review of 10-18: a run's own clock (`shift begin/check --until`: the 07:45 morning
fill), the render queue's own night limits (05:50 / 06:15 with no flag), the GTA worker's rescans when it runs out of
sources, a render a stopped run left in the queue, the 45-minute idle rule, the retry taking 07:00 after 04:45.
Throwaway state, fake clocks, fake ps tables, tiny shell commands: nothing renders."""
import contextlib
import fcntl
import io
import json
import shutil
import subprocess
import sys
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import factory as F                         # noqa: E402
from clipfactory import bench as BN         # noqa: E402
from clipfactory import board as B          # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import makers as M         # noqa: E402
from clipfactory import nightretry as NR    # noqa: E402
from clipfactory import poster as P         # noqa: E402
from clipfactory import renderlock as RL    # noqa: E402
from clipfactory import shift as SH         # noqa: E402
from clipfactory import timer as TM         # noqa: E402

# the bench's live window, read before test_bench's Base patches its logic tests back to the 05:05 clock
NEW_BENCH = {k: getattr(BN, k) for k in ("NOT_BEFORE", "QUIET_BY", "STOP_AT", "REPORT_TAG")}

import test_bench as TB                     # noqa: E402
from test_gta_maker_fixes import Base as GtaBase, row   # noqa: E402
from test_gta_yield import Yield, local                # noqa: E402

DAY = date(2030, 1, 7)


def hm(cfg: C.Config, d: date, hhmm: str) -> datetime:
    return cfg.slot_time(d, hhmm)


def tmp_cfg(tmp: Path) -> C.Config:
    raw = json.loads((ROOT / "config" / "accounts.json").read_text())
    raw["drafts_root"] = str(tmp / "drafts")
    for slug, a in raw["accounts"].items():
        a["account_dir"] = str(tmp / a["name"])
        a["skill_dir"] = str(tmp / f"skill-{slug}")
    p = tmp / "accounts.json"
    p.write_text(json.dumps(raw))
    return C.load(p)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-nightshift-"))
        self._state = C.STATE
        C.STATE = self.tmp / "state"
        self.cfg = tmp_cfg(self.tmp)

    def tearDown(self):
        C.STATE = self._state
        shutil.rmtree(self.tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------------------------ one clock
class OneClock(unittest.TestCase):
    def test_the_clock(self):
        self.assertEqual((SH.START, SH.LAST_PASS, SH.LAST_START, SH.DONE_BY, SH.BENCH, SH.BENCH_STOP, SH.NOTICE,
                          SH.SIGN), ("21:00", "05:30", "05:50", "06:15", "06:18", "06:25", "06:30", "06:45"))
        self.assertEqual(SH.FREEZE, ("06:15", "08:00"))

    def test_every_launchd_agent_is_on_the_shift_clock(self):
        by = {a["name"]: a for a in TM.AGENTS}

        def at(name):
            a = by[name]["at"]
            return f"{a['Hour']:02d}:{a['Minute']:02d}"
        self.assertEqual(at("gta-nightly"), SH.START)
        self.assertEqual(at("gta-retry"), SH.RETRY)
        self.assertEqual(at("bench-fill"), SH.BENCH)
        self.assertEqual(at("morning"), SH.NOTICE)
        self.assertEqual(TM.plist(by["morning"])["StartCalendarInterval"], {"Hour": 6, "Minute": 30})
        self.assertEqual(by["morning"]["args"], ["morning", "--date", "today", "--notify"])
        self.assertEqual(TM.plist(by["bench-fill"])["StartCalendarInterval"], {"Hour": 6, "Minute": 18})
        self.assertFalse(TM.plist(by["bench-fill"])["RunAtLoad"])       # loading it starts nothing
        self.assertFalse(TM.plist(by["morning"])["RunAtLoad"])
        self.assertEqual(TM.plist(by["gta-retry"])["StartCalendarInterval"], {"Hour": 3, "Minute": 45})

    def test_the_gta_defaults_the_retry_and_the_bench_agree(self):
        d = M.GTA_MAKER_DEFAULTS
        self.assertEqual((d["pass_until"], d["night_until"], d["render_until"], d["stop_at"]),
                         (SH.LAST_PASS, SH.LAST_PASS, SH.LAST_START, SH.DONE_BY))
        self.assertEqual(d["passes"], 8)
        self.assertEqual((d["rescan_every"], d["rescan_until"]), (40, SH.RESCAN_UNTIL))
        self.assertEqual(SH.RESCAN_UNTIL, "03:20")
        self.assertTrue(SH.RESCAN_UNTIL < SH.GTA_SECOND_HALF < SH.RETRY)    # the worker is gone before they look
        self.assertEqual((SH.POLITICS_SECOND_HALF, SH.IDLE_MIN), ("22:00", 45))
        self.assertEqual((NR.START_BY, NR.UNTIL_AT, NR.STOP_BY), (SH.LAST_PASS, SH.LAST_START, SH.DONE_BY))
        self.assertEqual((NEW_BENCH["NOT_BEFORE"], NEW_BENCH["STOP_AT"]), (SH.LAST_START, SH.BENCH_STOP))
        self.assertTrue(SH.BENCH < NEW_BENCH["QUIET_BY"] < NEW_BENCH["STOP_AT"] < SH.NOTICE)
        self.assertEqual(NEW_BENCH["REPORT_TAG"], "Bench 06:18:")
        self.assertTrue(SH.DONE_BY < SH.BENCH < SH.NOTICE < SH.SIGN < "07:00")

    def test_the_morning_freeze(self):
        for hhmm, frozen in (("06:14", False), ("06:15", True), ("06:45", True), ("07:59", True), ("08:00", False),
                             ("21:00", False)):
            self.assertEqual(SH.in_freeze(datetime(2030, 1, 7, int(hhmm[:2]), int(hhmm[3:]))), frozen, hhmm)

    def test_a_late_signature_posts_at_once_up_to_twenty_hours(self):
        self.assertEqual(P.LATE_LIMIT, timedelta(hours=20))
        now = datetime(2030, 1, 7, 7, 40)
        self.assertEqual(P.goes_out({"publish_at": "2030-01-07T07:00:00"}, now), "now (its time has passed)")
        self.assertIn("never", P.goes_out({"publish_at": "2030-01-06T07:00:00"}, datetime(2030, 1, 7, 3, 30)))


class PostingDay(Tmp):
    def at(self, h, m):
        self.cfg.now = lambda: datetime(2030, 1, 7, h, m, tzinfo=self.cfg.tz())

    def test_before_seven_is_today(self):
        self.assertEqual(C.POSTING_DAY_STARTS, 7)
        for h, m, want in ((0, 15, DAY), (5, 15, DAY), (6, 15, DAY), (6, 59, DAY), (7, 0, DAY + timedelta(days=1)),
                           (21, 0, DAY + timedelta(days=1))):
            self.at(h, m)
            self.assertEqual(C.parse_day(None, self.cfg), want, f"{h:02d}:{m:02d}")

    def test_the_hourly_politics_collect_at_0615_collects_today(self):
        # launchd politics-collect runs `collect politics --quiet` with no date: at 06:15 that is today's board,
        # the one the shift is finishing (at 05:15 and 06:15 it used to mean tomorrow)
        self.at(6, 15)
        with mock.patch.object(C, "load", lambda *a: self.cfg), \
                mock.patch.object(F.K, "collect", return_value=[]) as got:
            F.main(["collect", "politics", "--quiet"])
        self.assertEqual(got.call_args[0][2], DAY)


# ------------------------------------------------------------------------------------------------------ the queue
class Queue(Tmp):
    def hold_as(self, account, held_s):
        """This process takes the lock as `account` (another file description: the queue sees it held), since
        held_s seconds ago."""
        d = RL.root()
        (d / "queue").mkdir(parents=True, exist_ok=True)
        fh = open(d / "lock", "a+")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        RL._write(d / "holder.json", {"pid": os.getpid(), "account": account, "what": "intake: a long stream",
                                      "since_ts": time.time() - held_s, "since": "x", "max_hold_s": 0, "token": "t"})
        self.addCleanup(fh.close)
        return fh

    def test_until_0550_from_the_shift_and_its_halves(self):
        at = lambda h, m: datetime(2030, 1, 6 if h >= 12 else 7, h, m).astimezone()   # noqa: E731
        for h, m in ((21, 0), (0, 43), (3, 41), (5, 49)):
            got = datetime.fromtimestamp(RL.until_epoch("05:50", at(h, m)))
            self.assertEqual(got.strftime("%d %H:%M"), "07 05:50", f"{h}:{m}")
        self.assertLess(RL.until_epoch("05:50", at(5, 55)), at(5, 55).timestamp())           # just passed
        self.assertEqual(datetime.fromtimestamp(RL.until_epoch("20:30", at(15, 6))).strftime("%d %H:%M"), "06 20:30")

    def test_a_long_step_of_the_other_account_lets_the_next_waiter_run_beside_it(self):
        self.hold_as("gaming", 61 * 60)
        said = []
        with RL.hold("politics", "patel: render", wait_s=None, until=time.time() + 20, say=said.append) as t:
            self.assertTrue(t.beside)
        self.assertTrue(any("past its turn" in s for s in said), said)
        ev = [json.loads(ln) for ln in (RL.root() / "log.jsonl").read_text().splitlines()]
        self.assertTrue(any(e["event"] == "beside" and e.get("turn") and e.get("holder_account") == "gaming"
                            for e in ev), ev)

    def test_a_short_step_or_the_same_account_keeps_its_turn(self):
        self.hold_as("gaming", 5 * 60)
        with self.assertRaises(RL.Busy):
            with RL.hold("politics", "patel: render", wait_s=None, until=time.time() + 3):
                self.fail("ran beside a step that has held the queue 5 minutes")
        RL._write(RL.root() / "holder.json", dict(RL._read(RL.root() / "holder.json"), since_ts=time.time() - 7200))
        with self.assertRaises(RL.Busy):
            with RL.hold("gaming", "clip 07:00", wait_s=None, until=time.time() + 3):
                self.fail("the same account's next step went beside its own intake")
        with self.assertRaises(RL.Busy):        # --turn 0: off
            with RL.hold("politics", "patel: render", wait_s=None, until=time.time() + 3, turn_s=0):
                self.fail("ran beside with the turn rule off")

    def test_stop_at_stops_a_step_that_runs_into_the_morning(self):
        said = []
        t0 = time.time()
        rc = RL.run("politics", "late render", ["sleep", "30"], wait_s=None, stop_at=time.time() + 1.5,
                    say=said.append)
        self.assertEqual(rc, RL.TIMEOUT_EXIT)
        self.assertLess(time.time() - t0, 20)
        self.assertTrue(any("stop time and was stopped" in s for s in said), said)
        rc = RL.run("politics", "too late", ["/usr/bin/true"], wait_s=None, stop_at=time.time() - 1,
                    say=said.append)
        self.assertEqual(rc, RL.BUSY_EXIT)
        self.assertEqual(RL.run("politics", "in time", ["/usr/bin/true"], wait_s=None,
                                stop_at=time.time() + 60, say=said.append), 0)

    def test_the_command_line_takes_stop_at_and_turn(self):
        with mock.patch.object(RL, "run", return_value=0) as r, self.assertRaises(SystemExit):
            F.main(["renderlock", "run", "--account", "politics", "--what", "x", "--until", "05:50",
                    "--stop-at", "06:15", "--turn", "45", "--", "/usr/bin/true"])
        kw = r.call_args[1]
        self.assertEqual(kw["turn_s"], 45 * 60)
        self.assertEqual(datetime.fromtimestamp(kw["stop_at"]).strftime("%H:%M"), "06:15")
        self.assertEqual(datetime.fromtimestamp(kw["until"]).strftime("%H:%M"), "05:50")


# ------------------------------------------------------------------------------------------------------ the GTA night
class GtaNight(GtaBase):
    """make_passes on the shift's defaults (no gta_maker clock override), run.py faked."""

    def run_night(self, now, fake):
        said = []
        self.cfg.now = lambda: now[0]

        def collect(cfg, acct, day, extra=None, only=None):
            return []
        with B.edit(self.cfg, self.acct, DAY):
            pass
        with mock.patch.object(M, "run_logged", fake), \
                mock.patch.object(M, "pack_gta_slot", lambda a, d, s, l, e: f"  {s}: packed"), \
                mock.patch.object(M.K, "collect", collect), \
                mock.patch.object(M, "_nap", lambda s: now.__setitem__(0, now[0] + timedelta(seconds=s))):
            n = M.make_passes(self.cfg, self.acct, DAY, ["07:00"], say=said.append)    # a rescan moves the clock
        return n, said

    def test_the_night_starts_no_step_after_0550_and_stops_itself_at_0615(self):
        self.inbox([row(1, duration=300)])
        seen = []

        def fake(cmd, log, cwd=None, env=None, timeout=0):
            seen.append((os.environ.get("CLIP_RENDERLOCK_UNTIL"), list(M._STOP_AT)))
            return subprocess.CompletedProcess(cmd, 0)
        now = [hm(self.cfg, DAY - timedelta(days=1), "21:00")]
        n, said = self.run_night(now, fake)
        self.assertTrue(any(s.startswith("no intake or render of this run starts after 05:50") for s in said), said)
        self.assertTrue(any("stops itself at 06:15" in s for s in said), said)
        by = int(hm(self.cfg, DAY, "05:50").timestamp())
        self.assertTrue(seen and all(u == str(by) for u, _ in seen), seen)
        left = seen[0][1][0] - time.time()                               # 9 h 15 from the fake 21:00
        self.assertAlmostEqual(left, 9.25 * 3600, delta=120)
        self.assertEqual(M._STOP_AT, [])                                 # taken back after the run
        self.assertNotIn("CLIP_RENDERLOCK_UNTIL", os.environ)

    def test_a_pass_may_start_until_0530(self):
        self.assertEqual(M.another_pass(self.cfg, self.acct, DAY, None, 1, B.utcnow())[0] in (True, False), True)
        self.cfg.now = lambda: hm(self.cfg, DAY, "05:31")
        more, why = M.another_pass(self.cfg, self.acct, DAY, None, 1, B.utcnow())
        self.assertFalse(more)
        self.assertIn("past 05:30 (pass_until)", why)

    def test_an_intake_that_would_end_after_0530_is_not_dealt(self):
        self.inbox([row(1, duration=600)])                                   # x4 = 40 min
        self.cfg.now = lambda: hm(self.cfg, DAY, "04:40")                    # ends 05:20: in
        self.assertEqual(M.intake_too_late(self.cfg, self.acct, DAY, row(1, duration=600)), "")
        self.cfg.now = lambda: hm(self.cfg, DAY, "04:55")                    # ends 05:35: out
        self.assertIn("after 05:30 (night_until)", M.intake_too_late(self.cfg, self.acct, DAY, row(1, duration=600)))

    def test_the_watchdog_stops_a_running_step_and_fails_what_it_was_making(self):
        self.inbox([row(1, duration=60)])                                    # x3 = 3 min: in before 05:50

        def fake(cmd, log, cwd=None, env=None, timeout=0):
            if cmd[1] == "watch.py":                                         # the scan: fine
                return subprocess.CompletedProcess(cmd, 0)
            raise M.ShiftOver("run.py was still running at 06:15 and was stopped")
        now = [hm(self.cfg, DAY, "05:40")]
        n, said = self.run_night(now, fake)
        s = B.read(self.acct, DAY)["slots"]["07:00"]
        self.assertEqual((s["status"], s.get("fail_kind")), (B.FAILED, "stopped"), s)
        self.assertTrue(any(x.startswith("stopped (stop_at 06:15)") for x in said), said)
        items = json.loads((self.acct.account_dir / "Publish" / "inbox.json").read_text())["items"]
        self.assertNotIn("intake_failed", next(iter(items.values())))       # the night's end blames no video

    def test_run_logged_stops_a_step_at_the_stop_time(self):
        log = self.tmp / "x.log"
        M._STOP_AT[:] = [time.time() + 1.0]
        try:
            t0 = time.time()
            with self.assertRaises(M.ShiftOver):
                M.run_logged(["sleep", "30"], log)
            self.assertLess(time.time() - t0, 25)
            self.assertIn("the worker's stop time", log.read_text())
            M._STOP_AT[:] = [time.time() - 1]
            with self.assertRaises(M.ShiftOver):
                M.run_logged(["sleep", "30"], log)                          # past it: never started
            M._STOP_AT[:] = [time.time() + 600]
            self.assertEqual(M.run_logged(["/usr/bin/true"], log).returncode, 0)
        finally:
            M._STOP_AT.clear()

    def test_an_intake_whose_turn_came_too_late_is_not_started_nor_held_against_it(self):
        self.inbox([row(1, duration=1200)])                                  # x4 = 80 min
        now = [hm(self.cfg, DAY, "04:00")]                                   # dealt at 04:00: ends 05:20
        calls = []

        @contextlib.contextmanager
        def slow_turn(account, what, **kw):
            now[0] = hm(self.cfg, DAY, "04:30")                              # waited 30 min behind the Politics
            yield RL.Turn(locked=True, waited_s=1800)

        def fake(cmd, log, cwd=None, env=None, timeout=0):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)
        with mock.patch.object(RL, "hold", slow_turn):
            n, said = self.run_night(now, fake)
        self.assertEqual([c for c in calls if c[1] == "run.py"], [])
        s = B.read(self.acct, DAY)["slots"]["07:00"]
        self.assertEqual((s["status"], s.get("fail_kind")), (B.FAILED, "late"), s)
        self.assertTrue(any("its turn in the render queue came after 30 min" in n for n in s["notes"]), s["notes"])
        items = json.loads((self.acct.account_dir / "Publish" / "inbox.json").read_text())["items"]
        self.assertNotIn("intake_failed", next(iter(items.values())))
        self.assertFalse(s.get("sources_tried"))                             # settled: the next run may deal it


# ------------------------------------------------------------------------------------------------------ who works
def table(*rows):
    """pid -> (ppid, comm) from (pid, ppid, comm) rows."""
    return {p: (pp, c) for p, pp, c in rows}


class WhoWorks(Tmp):
    POLITICS_TASK, SECOND = "hearing-clip-sweep-2", "politics-early-gap-fill"

    def setUp(self):
        super().setUp()
        self.politics = self.cfg.account("politics")
        self.gta = self.cfg.account("gaming")
        # the first half's Claude session: claude 500 -> zsh 501 -> python 502 (a renderlock run in the background)
        self.working = table((1, 0, "launchd"), (400, 1, "Claude"), (500, 400, "claude"), (501, 500, "zsh"),
                             (502, 501, "Python"), (510, 500, "zsh"), (700, 500, "zsh"))
        self.idle = table((1, 0, "launchd"), (400, 1, "Claude"), (500, 400, "claude"), (501, 500, "zsh"))
        self.gone = table((1, 0, "launchd"), (400, 1, "Claude"))
        self.lstart = lambda pid: "Sun Oct 18 21:00:06 2026"

    def begin(self, task, at, tab, me):
        with mock.patch.object(SH, "_ps", lambda: tab):
            return SH.begin(self.cfg, self.politics, DAY, task, now=at, ps=lambda: tab, lstart=self.lstart, me=me)

    def who(self, at, tab, me=SECOND, seen=None):
        return SH.who_works(self.cfg, self.politics, DAY, me=me, now=at, ps=lambda: tab, lstart=self.lstart,
                            seen_busy=seen, worker=lambda a: False, queued=lambda a: [])

    def test_a_run_records_its_own_claude_session(self):
        lines = self.begin(self.POLITICS_TASK, hm(self.cfg, DAY - timedelta(days=1), "21:00"), self.working, me=700)
        r = SH.read(self.politics, DAY)["runs"][self.POLITICS_TASK]
        self.assertEqual((r["session"], r["session_start"], r["ended_at"]), (500, "Sun Oct 18 21:00:06 2026", None))
        self.assertIn("registered at 21:00 (its Claude session, pid 500)", lines[0])
        self.assertIsNone(SH.session_pid(table((1, 0, "launchd"), (9, 1, "zsh"), (10, 9, "Python")), me=10))

    def test_working_idle_gone_ended(self):
        at = hm(self.cfg, DAY - timedelta(days=1), "21:00")
        self.begin(self.POLITICS_TASK, at, self.working, me=700)
        self.assertTrue(self.who(at + timedelta(hours=3), self.working))                     # a command runs
        self.assertTrue(self.who(at + timedelta(minutes=10), self.idle))                     # between two steps
        self.assertTrue(self.who(at + timedelta(minutes=30), self.idle))                     # 30 min: still working
        self.assertEqual(self.who(at + timedelta(minutes=SH.IDLE_MIN + 1), self.idle), [])   # idle 45 min: stopped
        self.assertEqual(self.who(at + timedelta(minutes=5), self.gone), [])                 # its session is gone
        with mock.patch.object(SH, "_lstart", lambda pid: "another process"):
            st, words = SH.run_state(self.cfg, self.politics, DAY, SH.read(self.politics, DAY)["runs"][self.POLITICS_TASK],
                                     at, self.working, lambda pid: "Mon Oct 19 01:00:00 2026")
        self.assertEqual(st, "gone", words)                                                  # the pid was reused
        SH.end(self.cfg, self.politics, DAY, self.POLITICS_TASK, "10 of 10", now=at + timedelta(hours=4))
        self.assertEqual(self.who(at + timedelta(hours=4), self.working), [])                # it said it is done
        self.assertEqual(SH.busy_under(500, self.working), ["502 Python"])                   # shells do not count

    def test_check_tells_the_first_half_to_stop(self):
        at = hm(self.cfg, DAY - timedelta(days=1), "21:00")
        self.begin(self.POLITICS_TASK, at, self.working, me=700)
        self.assertEqual(SH.check(self.cfg, self.politics, DAY, self.POLITICS_TASK, now=at + timedelta(hours=2))[0], 0)
        self.assertEqual(SH.check(self.cfg, self.politics, DAY, self.POLITICS_TASK, now=hm(self.cfg, DAY, "05:50"))[0],
                         SH.BUSY_EXIT)
        self.begin(self.SECOND, hm(self.cfg, DAY, "01:10"), self.idle, me=None)
        rc, words = SH.check(self.cfg, self.politics, DAY, self.POLITICS_TASK, now=hm(self.cfg, DAY, "01:20"))
        self.assertEqual(rc, SH.TAKEN_EXIT)
        self.assertIn("politics-early-gap-fill took over", words)

    def test_wait_returns_when_the_first_half_stops_or_at_its_deadline(self):
        at = hm(self.cfg, DAY - timedelta(days=1), "21:00")
        self.begin(self.POLITICS_TASK, at, self.working, me=700)
        clock = [hm(self.cfg, DAY, "00:43")]
        tabs = iter([self.working] * 5 + [self.gone] * 3)
        said = []
        cur = [self.working]

        def ps():
            return cur[0]

        def sleep(s):
            clock[0] += timedelta(seconds=s)
            cur[0] = next(tabs)
        rc = SH.wait(self.cfg, self.politics, DAY, self.SECOND, now=lambda: clock[0], sleep=sleep, say=said.append,
                     ps=ps, lstart=self.lstart, worker=lambda a: False, queued=lambda a: [])
        self.assertEqual(rc, 0, said)
        self.assertTrue(said[0].startswith("Politics 2030-01-07: waiting (00:43): hearing-clip-sweep-2"), said)
        cur[0] = self.working
        clock[0] = hm(self.cfg, DAY, "05:40")
        rc = SH.wait(self.cfg, self.politics, DAY, self.SECOND, now=lambda: clock[0],
                     sleep=lambda s: clock.__setitem__(0, clock[0] + timedelta(seconds=s)), say=said.append,
                     ps=lambda: self.working, lstart=self.lstart, worker=lambda a: False, queued=lambda a: [])
        self.assertEqual(rc, SH.BUSY_EXIT)
        self.assertIn("still not free at 05:50", said[-1])

    def test_no_run_registered_means_a_quiet_board_and_an_empty_queue(self):
        at = hm(self.cfg, DAY, "01:00")
        self.assertEqual(SH.who_works(self.cfg, self.politics, DAY, me=self.SECOND, now=at, ps=lambda: {},
                                      worker=lambda a: False, queued=lambda a: []), [])
        busy = SH.who_works(self.cfg, self.politics, DAY, me=self.SECOND, now=at, ps=lambda: {},
                            worker=lambda a: False, queued=lambda a: ["rendering: patel: render"])
        self.assertTrue(busy and "render queue" in busy[0], busy)
        with B.edit(self.cfg, self.politics, DAY):
            pass                                                  # the board just changed
        busy = SH.who_works(self.cfg, self.politics, DAY, me=self.SECOND, now=self.cfg.now(), ps=lambda: {},
                            worker=lambda a: False, queued=lambda a: [])
        self.assertTrue(busy and "board changed" in busy[0], busy)

    def test_the_gta_worker_works_the_gta_board(self):
        lock = C.STATE / "gaming" / ".worker.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            got = SH.who_works(self.cfg, self.gta, DAY, me="night-retry", now=hm(self.cfg, DAY, "03:45"),
                               ps=lambda: {})
        self.assertTrue(got and got[0].startswith("the Gaming worker"), got)
        self.assertEqual(SH.who_works(self.cfg, self.gta, DAY, me="night-retry", now=hm(self.cfg, DAY, "03:45"),
                                      ps=lambda: {}), [])

    def test_the_retry_yields_to_a_working_gta_second_half(self):
        tab = self.working
        with mock.patch.object(SH, "_ps", lambda: tab):
            SH.begin(self.cfg, self.gta, DAY, "gaming-morning-sweep", now=hm(self.cfg, DAY, "03:41"), ps=lambda: tab,
                     lstart=self.lstart, me=700)
        with mock.patch.object(SH, "_ps", lambda: tab), mock.patch.object(SH, "_lstart", self.lstart):
            got = NR._others(self.cfg, self.gta, DAY, hm(self.cfg, DAY, "03:45"))
        self.assertTrue(got and got[0].startswith("gaming-morning-sweep: working"), got)

    def test_the_command_line(self):
        out = io.StringIO()
        with mock.patch.object(C, "load", lambda *a: self.cfg), mock.patch.object(SH, "_ps", lambda: self.working), \
                mock.patch.object(SH, "_lstart", self.lstart), mock.patch.object(SH, "session_pid", lambda *a: 500), \
                contextlib.redirect_stdout(out):
            F.main(["shift", "begin", "politics", "--date", DAY.isoformat(), "--task", self.POLITICS_TASK])
            with self.assertRaises(SystemExit) as e:
                F.main(["shift", "check", "politics", "--date", DAY.isoformat(), "--task", self.POLITICS_TASK])
            F.main(["shift", "end", "politics", "--date", DAY.isoformat(), "--task", self.POLITICS_TASK,
                    "--why", "10 of 10"])
            F.main(["shift", "status", "politics", "--date", DAY.isoformat()])
        self.assertIn(e.exception.code, (0, SH.BUSY_EXIT))           # 0 before 05:50 on the real clock's day
        text = out.getvalue()
        self.assertIn("hearing-clip-sweep-2 registered", text)
        self.assertIn("ended at", text)
        self.assertIn("the night shift for 2030-01-07: starts 21:00 on 2030-01-06", text)
        with self.assertRaises(SystemExit):
            F.main(["shift", "begin", "politics"])            # no --date, no --task: refused


    # ------------------------------------------------------------------------------- review of 10-18
    def test_the_morning_fill_keeps_its_own_clock(self):
        # the 07:45 morning fill (fires ~07:53) runs after the night: the night's 05:50 must not tell it to stop
        MORNING = "politics-morning-fill"
        at = hm(self.cfg, DAY, "07:53")
        self.assertEqual(SH.check(self.cfg, self.politics, DAY, MORNING, now=at)[0], SH.BUSY_EXIT)        # the night's
        self.assertEqual(SH.check(self.cfg, self.politics, DAY, MORNING, now=hm(self.cfg, DAY, "08:00"),
                                  until="11:30")[0], 0)
        lines = self.begin_until(MORNING, at, "11:30")
        self.assertIn("nothing new after 11:30", lines[-1])
        self.assertNotIn("05:50", lines[-1])
        self.assertEqual(SH.read(self.politics, DAY)["runs"][MORNING]["until"], "11:30")
        rc, words = SH.check(self.cfg, self.politics, DAY, MORNING, now=hm(self.cfg, DAY, "08:00"))   # it registered it
        self.assertEqual(rc, 0, words)
        rc, words = SH.check(self.cfg, self.politics, DAY, MORNING, now=hm(self.cfg, DAY, "11:31"))
        self.assertEqual(rc, SH.BUSY_EXIT)
        self.assertIn("past 11:30, this run's last start", words)

    def begin_until(self, task, at, until):
        with mock.patch.object(SH, "_ps", lambda: self.idle):
            return SH.begin(self.cfg, self.politics, DAY, task, now=at, ps=lambda: self.idle, lstart=self.lstart,
                            me=None, until=until)

    def test_a_run_that_never_registered_stops_for_one_that_did(self):
        self.begin(self.SECOND, hm(self.cfg, DAY, "01:10"), self.working, me=700)
        rc, words = SH.check(self.cfg, self.politics, DAY, self.POLITICS_TASK, now=hm(self.cfg, DAY, "01:20"))
        self.assertEqual(rc, SH.TAKEN_EXIT, words)
        SH.end(self.cfg, self.politics, DAY, self.SECOND, "done", now=hm(self.cfg, DAY, "02:00"))
        self.assertEqual(SH.check(self.cfg, self.politics, DAY, self.POLITICS_TASK, now=hm(self.cfg, DAY, "02:10"))[0], 0)

    def test_a_render_left_by_a_stopped_run_still_holds_the_board(self):
        at = hm(self.cfg, DAY - timedelta(days=1), "21:00")
        self.begin(self.POLITICS_TASK, at, self.working, me=700)
        later = at + timedelta(hours=1)
        gone = SH.who_works(self.cfg, self.politics, DAY, me=self.SECOND, now=later, ps=lambda: self.gone,
                            lstart=self.lstart, worker=lambda a: False, queued=lambda a: ["rendering: patel: render"])
        self.assertTrue(gone and "left by a run that stopped" in gone[0], gone)
        self.assertEqual(SH.who_works(self.cfg, self.politics, DAY, me=self.SECOND, now=later, ps=lambda: self.gone,
                                      lstart=self.lstart, worker=lambda a: False, queued=lambda a: []), [])
        gta = SH.who_works(self.cfg, self.gta, DAY, me="night-retry", now=later, ps=lambda: {},
                           worker=lambda a: False, queued=lambda a: ["waiting: 0700 by hand"])
        self.assertEqual(gta, [])                       # nothing registered on GTA: the worker lock says it all

    def test_the_command_line_takes_until_and_says_the_day(self):
        out = io.StringIO()
        self.cfg.now = lambda: hm(self.cfg, DAY, "08:00")
        with mock.patch.object(C, "load", lambda *a: self.cfg), mock.patch.object(SH, "_ps", lambda: self.idle), \
                mock.patch.object(SH, "_lstart", self.lstart), contextlib.redirect_stdout(out):
            F.main(["shift", "begin", "politics", "--date", DAY.isoformat(), "--task", "politics-morning-fill",
                    "--until", "11:30"])
            with self.assertRaises(SystemExit) as e:
                F.main(["shift", "check", "politics", "--date", DAY.isoformat(), "--task",
                        "politics-morning-fill", "--until", "11:30"])
            F.main(["shift", "day"])
            with self.assertRaises(SystemExit) as bad:
                F.main(["shift", "check", "politics", "--date", DAY.isoformat(), "--task",
                        "politics-morning-fill", "--until", "12:15"])
        self.assertEqual(e.exception.code, 0)
        self.assertIn("morning time", str(bad.exception.code))
        self.assertIn(f"\n{(DAY + timedelta(days=1)).isoformat()}\n", out.getvalue())   # 08:00: tomorrow's
        for hhmm, want in (("22:13", DAY), ("00:43", DAY), ("06:59", DAY), ("07:00", DAY + timedelta(days=1))):
            day = DAY - timedelta(days=1) if hhmm >= "12:00" else DAY
            self.cfg.now = lambda d=day, h=hhmm: hm(self.cfg, d, h)
            o = io.StringIO()
            with mock.patch.object(C, "load", lambda *a: self.cfg), contextlib.redirect_stdout(o):
                F.main(["shift", "day"])
            self.assertEqual(o.getvalue().strip(), want.isoformat(), hhmm)


class NightLimits(Tmp):
    """The render queue's own night limits: a step asked for between 21:00 and 06:15 starts by 05:50 and is stopped
    at 06:15, whatever flag it was given or forgot."""

    def at(self, d, h, m):
        return datetime(2030, 1, d, h, m).astimezone().timestamp()

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {RL.NIGHT_ENV: ""})      # a test run has them off by default (livestate.py)
        p.start()
        self.addCleanup(p.stop)

    def test_the_limits_by_the_clock(self):
        for (d, h, m), want in (((6, 21, 0), ("07 05:50", "07 06:15")), ((7, 0, 43), ("07 05:50", "07 06:15")),
                                ((7, 3, 41), ("07 05:50", "07 06:15")), ((7, 6, 10), ("07 05:50", "07 06:15"))):
            u, s = RL.night_limits(self.at(d, h, m))
            got = tuple(datetime.fromtimestamp(x).strftime("%d %H:%M") for x in (u, s))
            self.assertEqual(got, want, (h, m))
        for d, h, m in ((7, 6, 15), (7, 7, 53), (7, 12, 0), (6, 20, 59)):
            self.assertEqual(RL.night_limits(self.at(d, h, m)), (None, None), (h, m))
        with mock.patch.dict(os.environ, {RL.NIGHT_ENV: "0"}):
            self.assertEqual(RL.night_limits(self.at(6, 23, 0)), (None, None))

    def test_no_flag_at_0555_does_not_start(self):
        now = time.time()                       # as at 05:55: the night's start-by (05:50) has just passed
        with mock.patch.object(RL, "night_limits", lambda *a: (now - 60, now + 1200)):
            said = []
            self.assertEqual(RL.run("politics", "forgot its flags", ["/usr/bin/true"], wait_s=None,
                                    say=said.append), RL.BUSY_EXIT)
            self.assertTrue(any("the night shift's own limits" in s for s in said), said)
            with self.assertRaises(RL.Busy):
                with RL.hold("politics", "igcut: x", wait_s=None):
                    self.fail("an igcut with no deadline started after the night's 05:50")
            # a later --until is tightened to the night's: still not run
            self.assertEqual(RL.run("politics", "late flag", ["/usr/bin/true"], wait_s=None, until=now + 3600,
                                    say=said.append), RL.BUSY_EXIT)

    def test_no_stop_flag_is_stopped_at_the_nights_0615(self):
        now = time.time()
        said = []
        with mock.patch.object(RL, "night_limits", lambda *a: (now + 600, now + 1.5)):
            t0 = time.time()
            rc = RL.run("politics", "long render", ["sleep", "30"], wait_s=None, say=said.append)
        self.assertEqual(rc, RL.TIMEOUT_EXIT, said)
        self.assertLess(time.time() - t0, 20)
        self.assertTrue(any("stop time and was stopped" in s for s in said), said)

    def test_the_daytime_has_no_night_limits(self):
        with mock.patch.object(RL, "night_limits", lambda *a: (None, None)):
            self.assertEqual(RL.run("politics", "daytime", ["/usr/bin/true"], wait_s=None, say=lambda s: None), 0)


# ------------------------------------------------------------------------------------------------------ GTA rescans
class Rescan(Yield):
    """The night's worker with no source left does not end before 03:20: it waits 40 minutes, scans again, and
    makes another pass (the shift's defaults: no CLOCK of 10-18)."""
    CLOCK = {}

    def run_rescans(self, start, intake, on_nap=None, only=("07:00",), scan_rc=0):
        clock = [start]
        naps = []

        def nap(s):
            naps.append((clock[0], s))
            clock[0] += timedelta(seconds=s)
            if on_nap:
                on_nap(len(naps))
        with mock.patch.object(M, "_nap", nap):
            calls, said, n = self.night(intake=intake, only=list(only), passes=True, clock=clock, scan_rc=scan_rc)
        return calls, said, n, naps

    def test_a_worker_out_of_sources_scans_again_and_cuts_the_new_upload(self):
        a, b = row(1, duration=300), row(2, duration=300)
        self.inbox([a])
        calls, said, n, naps = self.run_rescans(local(self.cfg, DAY - timedelta(days=1), "23:00"), {a["url"]: 1},
                                                on_nap=lambda k: self.inbox([a, b]))   # a new upload meanwhile
        self.assertEqual(self.intakes(calls), [a["url"], b["url"]], said)
        self.assertEqual(sum(1 for c in calls if c[1] == "watch.py"), 2)            # a fresh scan after the wait
        self.assertEqual(len(naps), 1)
        self.assertEqual(naps[0][1], 40 * 60)
        self.assertTrue(any(s.startswith("== rescan 1: waiting until 23:40") for s in said), said)
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.READY)
        self.assertEqual(n, 2)

    def test_the_rescans_end_at_0320(self):
        a = row(1, duration=300)
        self.inbox([a])
        calls, said, n, naps = self.run_rescans(local(self.cfg, DAY, "01:30"), {a["url"]: 1})
        # passes at 01:30, 02:10 and 02:50; a wait from 02:50 would end 03:30, after 03:20: the worker ends
        self.assertEqual([x[0].strftime("%H:%M") for x in naps], ["01:30", "02:10"], said)
        self.assertEqual(n, 3)
        self.assertEqual(sum(1 for c in calls if c[1] == "watch.py"), 3)
        self.assertEqual([s for s in said if s.startswith("no pass")][-1], "no pass 4: no waiting source left for 07:00")

    def test_a_daytime_make_or_a_late_one_never_waits(self):
        a = row(1, duration=300)
        for start in (local(self.cfg, DAY - timedelta(days=1), "14:00"), local(self.cfg, DAY, "03:00")):
            self.fresh()
            self.inbox([a])
            calls, said, n, naps = self.run_rescans(start, {a["url"]: 1})
            self.assertEqual((n, naps), (1, []), (start, said))
        self.fresh()
        self.reload(rescan_every=0)                                                # the switch: end, as before
        self.inbox([a])
        calls, said, n, naps = self.run_rescans(local(self.cfg, DAY - timedelta(days=1), "23:00"), {a["url"]: 1})
        self.assertEqual((n, naps), (1, []))

    def test_a_network_night_waits_and_deals_its_uploads_again(self):
        a, b = row(1, duration=300), row(2, duration=300)
        self.inbox([a, b])
        fail = {a["url"]: 1, b["url"]: 1}
        state = {"net": False}

        def back(k):
            fail.clear()                                                         # the network is back
            state["net"] = True
        calls, said, n, naps = self.run_rescans(local(self.cfg, DAY - timedelta(days=1), "22:00"), fail,
                                                on_nap=back, only=("07:00", "08:45", "10:30", "12:15"))
        self.assertEqual(len(naps), 1, said)
        self.assertTrue(any("not held against their upload(s); the pass after the wait may deal them again" in s
                            for s in said), said)
        self.assertEqual(self.intakes(calls).count(a["url"]), 2, said)             # dealt again after the wait
        for r in (a, b):
            self.assertNotIn("intake_failed", self.row_of(r["url"]))
        self.assertTrue(any(s["status"] == B.READY for s in self.board()["slots"].values()), said)


class RetryLead(Tmp):
    """The 03:45 retry: a slot after Andrew's 06:45 sign-off is takeable all night (07:00 from 04:45 on)."""

    def test_the_seven_oclock_slot_after_0445(self):
        gta = self.cfg.account("gaming")
        with B.edit(self.cfg, gta, DAY) as bd:
            for k, s in bd["slots"].items():
                s["status"] = B.READY
            bd["slots"]["07:00"]["status"] = B.FAILED
        for hhmm, want in (("03:45", ["07:00"]), ("05:10", ["07:00"]), ("05:29", ["07:00"])):
            got, near = NR.due_slots(self.cfg, gta, DAY, hm(self.cfg, DAY, hhmm))
            self.assertEqual((got, near), (want, []), hhmm)
        got, near = NR.due_slots(self.cfg, gta, DAY, hm(self.cfg, DAY, "07:30"))   # a daytime look: 2 hours
        self.assertEqual((got, near), ([], ["07:00"]))


# ------------------------------------------------------------------------------------------------------ the bench
class BenchWindow(TB.Base):
    """The live 06:18 window (test_bench's logic tests keep the 05:05 one)."""
    spare = TB.Fill.spare

    def setUp(self):
        super().setUp()
        for k, v in NEW_BENCH.items():
            p = mock.patch.object(BN, k, v)
            p.start()
            self.addCleanup(p.stop)

    def test_the_0618_fill_writes_between_0550_and_0625(self):
        self.spare("one-spare")
        for at in ("05:49", "06:25", "06:30", "05:05"):
            lines = self.fill(at=self.cfg.slot_time(TB.DAY, at))
            self.assertIn("not run at", lines[0], at)
            self.assertIn("between 05:50 and 06:25", lines[0])
        with B.edit(self.cfg, self.acct, TB.DAY) as bd:
            bd["report"] = "the night's report\nBench 05:05: an old line."
            for k, s in bd["slots"].items():
                if k not in ("07:00", "08:45"):
                    s["status"] = B.READY
        lines = self.fill(at=self.cfg.slot_time(TB.DAY, "06:18"))
        bd = B.read(self.acct, TB.DAY)
        self.assertNotIn("bench", bd["slots"]["07:00"])            # 42 minutes away: place.py's 90-minute lead
        self.assertTrue(any("07:00: 42 minutes away" in ln for ln in lines), lines)
        self.assertIn("bench", bd["slots"]["08:45"], lines)
        self.assertEqual(bd["report"].splitlines(), ["the night's report", "Bench 06:18: 08:45 from the bench."])


if __name__ == "__main__":
    unittest.main()
