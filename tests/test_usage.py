import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"]="1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())
"""Claude's week (clipfactory/usage.py, 2026-10-27): the week of 10-19 ran out on Sunday and the 10-26 board was lost.
The meter reads the app's readings plus the transcripts' cost since, the daytime extras stop once the week is in the
night shift's reserve (the night's own tasks never do), and the warning reaches doctor, the Mac and every chat once."""
import json
import shutil
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipfactory import usage as U            # noqa: E402

UTC = timezone.utc
START = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)      # Mon 14:00 MDT, the week of the run-out's next week
NOW = datetime(2026, 10, 27, 19, 0, tzinfo=UTC)        # Tue 13:00 MDT
CONF = dict(U.DEFAULTS, org="org-a", reset="2026-10-26T20:00:00Z")
TAGGED = '<scheduled-task name=\\"{}\\" file=\\"x\\">\\nThis is an automated run'


def call(t: datetime, mid: str, dollars_out: float, model: str = "claude-opus-5-5") -> str:
    """One assistant line whose cost is `dollars_out` (output tokens at $20 a million)."""
    return json.dumps({"type": "assistant", "timestamp": t.isoformat().replace("+00:00", "Z"), "requestId": "r" + mid,
                       "message": {"id": mid, "model": model,
                                   "usage": {"input_tokens": 0, "output_tokens": int(dollars_out / 20 * 1e6)}}}) + "\n"


def first(task: str = "") -> str:
    content = TAGGED.format(task) if task else "hello"
    return '{"type":"user","message":{"role":"user","content":"' + content + '"}}\n'


def status(**kw) -> dict:
    """A status with the week's numbers, for the gate, the check and the hook."""
    st = {"at": NOW.isoformat(), "week_start": START.isoformat(), "week_end": (START + timedelta(days=7)).isoformat(),
          "used": 50.0, "estimated": 5.0, "reading_at": NOW.isoformat(), "reading": 45.0, "scan_at": NOW.isoformat(),
          "night_day": 2.0, "all_day": 3.0, "need_night": 15.0, "need_all": 20.0, "room": 30.0, "pace": 5.0,
          "eta": None, "early": False, "level": "ok", "timezone": "America/Denver"}
    st.update(kw)
    return st


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.projects = self.tmp / "projects"
        (self.projects / "-proj").mkdir(parents=True)
        self.state = U.state_dir()
        shutil.rmtree(self.state, ignore_errors=True)
        self.patches = [mock.patch.object(U, "PROJECTS", self.projects),
                        mock.patch.object(U, "HISTORY", self.tmp / "history.json"),
                        mock.patch.object(U, "TASKS", self.tmp / "tasks"),
                        mock.patch.object(U, "settings", lambda path=None: dict(CONF))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.state, ignore_errors=True)

    def session(self, name: str, lines: str, task: str = "", sub: str = "") -> Path:
        if sub:
            p = self.projects / "-proj" / name / "subagents" / f"{sub}.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(first() + lines)
        else:
            p = self.projects / "-proj" / f"{name}.jsonl"
            p.write_text(first(task) + lines)
        return p

    def put_now(self, st: dict) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "now.json").write_text(json.dumps(st))


class WeekAndReadingsTest(Base):
    def test_1_the_week_steps_by_seven_days_from_the_anchor(self):
        self.assertEqual(U.week(NOW, CONF), (START, START + timedelta(days=7)))
        self.assertEqual(U.week(START - timedelta(minutes=1), CONF)[0], START - timedelta(days=7))
        self.assertEqual(U.week(START + timedelta(days=7), CONF)[0], START + timedelta(days=7))

    def test_2_readings_skip_empty_samples_and_other_orgs_count_apart(self):
        ms = lambda t: int(t.timestamp() * 1000)
        (self.tmp / "history.json").write_text(json.dumps({"version": 2, "samples": [
            {"t": ms(NOW - timedelta(hours=3)), "org": "org-a", "u": {"fh": 1, "sd": 20}},
            {"t": ms(NOW - timedelta(hours=2)), "org": "org-a", "u": {}},
            {"t": ms(NOW - timedelta(hours=1)), "org": "org-b", "u": {"fh": 0, "sd": 70}},
            {"t": ms(START - timedelta(hours=1)), "org": "org-a", "u": {"fh": 0, "sd": 99}}]}))
        rs = U.readings()
        self.assertEqual(len(rs), 3)
        r = U.last_reading(rs, CONF, START, NOW)
        self.assertEqual((r["org"], r["sd"]), ("org-a", 20.0))     # last week's 99 and org-b's 70 do not count


class ScanTest(Base):
    def test_1_costs_by_task_once_per_call_and_only_new_lines(self):
        t = NOW - timedelta(hours=1)
        p = self.session("aaaa", call(t, "m1", 3.0) + call(t, "m1", 3.0) + call(t, "m2", 6.0), task="clip-coach")
        self.session("aaaa", call(t, "m3", 1.0), sub="agent-1")
        self.session("bbbb", call(t, "m4", 30.0))
        rec = U.scan(now=NOW)
        files = rec["files"]
        self.assertEqual(files[str(p)]["task"], "clip-coach")
        sub = [f for k, f in files.items() if "subagents" in k][0]
        self.assertEqual(sub["task"], "clip-coach")                 # a subagent counts for the task that ran it
        self.assertAlmostEqual(U.dollars(rec, t - timedelta(hours=1), NOW), 40.0, places=2)   # m1 once
        self.assertAlmostEqual(U.dollars(rec, t - timedelta(hours=1), NOW, only=lambda k: k != "chat"), 10.0, places=2)
        with open(p, "a") as fh:                                     # the same session goes on
            fh.write(call(t, "m2", 6.0) + call(t + timedelta(minutes=20), "m5", 2.0) + '{"type":"assist')
        rec = U.scan(now=NOW)
        self.assertAlmostEqual(U.dollars(rec, t - timedelta(hours=1), NOW), 42.0, places=2)   # m2 not twice
        off = rec["files"][str(p)]["off"]
        self.assertLess(off, p.stat().st_size)                       # the half-written line waits

    def test_2_old_transcripts_and_buckets_drop_out(self):
        old = self.session("cccc", call(NOW - timedelta(days=9), "o1", 5.0))
        os.utime(old, (NOW.timestamp() - 9 * 86400,) * 2)
        self.session("dddd", call(NOW - timedelta(days=9), "o2", 5.0) + call(NOW, "o3", 1.0))
        rec = U.scan(now=NOW + timedelta(minutes=5))
        self.assertNotIn(str(old), rec["files"])
        self.assertAlmostEqual(U.dollars(rec, NOW - timedelta(days=20), NOW + timedelta(hours=1)), 1.0, places=2)


class StatusTest(Base):
    def rec(self, chat=0.0, night=0.0, extra=0.0, since=None) -> dict:
        t = since or NOW - timedelta(hours=2)
        if chat:
            self.session("chat1", call(t, "c1", chat))
        if night:
            for d in range(1, 8):                                    # every one of the last 7 days
                self.session(f"night{d}", call(NOW - timedelta(days=d), f"n{d}", night), task="hearing-clip-sweep-2")
        if extra:
            for d in range(1, 8):
                self.session(f"extra{d}", call(NOW - timedelta(days=d), f"e{d}", extra), task="clip-coach")
        return U.scan(now=NOW)

    def test_1_used_is_the_reading_plus_what_was_spent_since(self):
        rec = self.rec(chat=150.0)                                   # $150 = 5%
        reading = [{"t": NOW - timedelta(hours=3), "org": "org-a", "sd": 40.0, "fh": 0.0}]
        st = U.status(NOW, CONF, rec, reading)
        self.assertEqual((st["used"], st["estimated"], st["reading"]), (45.0, 5.0, 40.0))
        st = U.status(NOW, CONF, rec, [])                            # no reading this week: all counted
        self.assertEqual(st["used"], 5.0)

    def test_2_the_reserve_follows_the_tasks_and_the_days_left(self):
        rec = self.rec(night=90.0, extra=30.0)                       # 3% + 1% a day
        st = U.status(NOW, CONF, rec, [])
        left = (START + timedelta(days=7) - NOW).total_seconds() / 86400
        self.assertEqual(st["night_day"], 3.0)
        self.assertEqual(st["all_day"], 4.0)
        margin = CONF["margin_pct"] + U.UNCERTAIN * st["estimated"]
        self.assertAlmostEqual(st["need_night"], round(3.0 * left + margin, 1), places=1)
        self.assertAlmostEqual(st["need_all"], round(4.0 * left + margin, 1), places=1)
        st = U.status(NOW, CONF, {}, [])                             # nothing measured: the floors
        self.assertEqual((st["night_day"], st["all_day"]), (1.5, 2.5))

    def test_3_levels(self):
        rec = self.rec(chat=30.0)                                    # 1% in the last 2 hours: a slow pace
        at = lambda sd: U.status(NOW, CONF, rec, [{"t": NOW - timedelta(hours=3), "org": "org-a", "sd": sd, "fh": 0}])
        self.assertEqual(at(10)["level"], "ok")
        need = at(10)["need_all"]
        self.assertEqual(at(100 - need - 1 - 10)["level"], "watch")  # room 10%
        self.assertEqual(at(100 - need - 1 - 3)["level"], "low")     # room 3%
        self.assertEqual(at(100 - need)["level"], "reserve")
        self.assertEqual(at(100)["level"], "out")

    def test_4_a_fast_pace_that_runs_out_before_the_reset_is_watch(self):
        rec = self.rec(chat=900.0)                                   # 30% in a day
        st = U.status(NOW, CONF, rec, [{"t": NOW - timedelta(hours=3), "org": "org-a", "sd": 0.0, "fh": 0}])
        self.assertTrue(st["early"])
        self.assertEqual(st["level"], "watch")
        self.assertIn("runs out", U.line(st))
        self.assertIn("runs out", U.short(st))
        self.assertEqual(U.short(dict(st, level="ok")), "")


class GateTest(Base):
    def test_1_extras_stop_in_the_reserve_the_night_never_does(self):
        self.put_now(status(level="reserve", room=-2.0))
        go, text = U.gate("bench-builder-auto", NOW)
        self.assertFalse(go)
        self.assertTrue(text.startswith("STOP: bench-builder-auto"))
        for t in CONF["night_tasks"]:
            self.assertTrue(U.gate(t, NOW)[0], t)
        self.put_now(status(level="out"))
        self.assertFalse(U.gate("clip-coach", NOW)[0])
        self.assertTrue(U.gate("hearing-clip-sweep-2", NOW)[0])

    def test_2_extras_go_when_there_is_room_or_no_count(self):
        self.put_now(status(level="low", room=3.0))
        self.assertTrue(U.gate("clip-coach", NOW)[0])
        (self.state / "now.json").unlink()
        with mock.patch.object(U, "refresh", side_effect=OSError("no transcripts")):
            go, text = U.gate("clip-coach", NOW)
        self.assertTrue(go)
        self.assertIn("never stops a run", text)

    def test_3_factory_usage_gate_exits_3_on_stop(self):
        import factory as F
        self.put_now(status(level="reserve", room=-1.0))
        with mock.patch.object(U, "current", return_value=status(level="reserve", room=-1.0)), \
                mock.patch("sys.stdout"):
            with self.assertRaises(SystemExit) as cm:
                F.main(["usage", "gate", "--task", "clip-coach"])
        self.assertEqual(cm.exception.code, U.STOP)
        with mock.patch.object(U, "current", return_value=status()), mock.patch("sys.stdout"):
            F.main(["usage", "gate", "--task", "clip-coach"])         # GO: no exit


class CheckAndHookTest(Base):
    def run_check(self, st: dict, notify: bool = True):
        with mock.patch.object(U, "refresh", return_value=st), mock.patch.object(U.subprocess, "run") as run:
            lines = U.check(notify=notify, now=NOW)
        return lines, run

    def test_1_a_notice_once_when_the_week_gets_worse(self):
        lines, run = self.run_check(status())
        self.assertEqual((len(lines), run.call_count), (1, 0))
        lines, run = self.run_check(status(level="watch", room=10.0))
        self.assertEqual(run.call_count, 1)
        self.assertIn("osascript", run.call_args[0][0])
        _, run = self.run_check(status(level="watch", room=9.0))
        self.assertEqual(run.call_count, 0)                          # the same level: not again
        _, run = self.run_check(status(level="reserve", room=-1.0))
        self.assertEqual(run.call_count, 1)
        _, run = self.run_check(status(level="ok"))                  # better again (a reset, a reading)
        _, run = self.run_check(status(level="watch", room=10.0))
        self.assertEqual(run.call_count, 1)                          # so the next worsening is told

    def test_2_a_new_week_starts_untold(self):
        self.run_check(status(level="watch", room=10.0))
        nxt = (START + timedelta(days=7)).isoformat()
        _, run = self.run_check(status(level="watch", room=10.0, week_start=nxt))
        self.assertEqual(run.call_count, 1)

    def test_3_the_chat_hook_speaks_once_per_chat_and_level(self):
        self.put_now(status())
        self.assertEqual(U.hook('{"session_id": "s1"}', NOW), "")    # a fine week: silent
        self.put_now(status(level="watch", room=10.0, early=True, pace=30.0,
                            eta=(NOW + timedelta(days=2)).isoformat()))
        text = U.hook('{"session_id": "s1"}', NOW)
        self.assertIn("usage guard", text)
        self.assertIn("Tell Andrew", text)
        self.assertEqual(U.hook('{"session_id": "s1"}', NOW), "")    # told already
        self.assertIn("usage guard", U.hook('{"session_id": "s2"}', NOW))
        self.put_now(status(level="reserve", room=-1.0))
        self.assertIn("RESERVE", U.hook('{"session_id": "s1"}', NOW))
        self.assertEqual(U.hook('{"session_id": "s3"}', NOW + timedelta(hours=4)), "")   # stale count: silent
        self.assertEqual(U.hook("not json", NOW + timedelta(hours=4)), "")

    def test_4_the_hook_script_never_fails(self):
        with mock.patch.object(U, "hook", side_effect=RuntimeError("boom")), mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            stdin.read.return_value = "{}"
            self.assertEqual(U.main(["hook"]), 0)


class DoctorTest(Base):
    def test_1_a_task_that_lost_its_gate_is_a_no(self):
        for t in CONF["gated_tasks"]:
            (self.tmp / "tasks" / t).mkdir(parents=True)
            (self.tmp / "tasks" / t / "SKILL.md").write_text(f"STEP 0 ... usage gate --task {t} ...")
        self.put_now(status())
        rows = U.doctor(NOW)
        self.assertTrue(rows[0][0])
        (self.tmp / "tasks" / "clip-coach" / "SKILL.md").write_text("no gate here")
        self.assertEqual(U.ungated(), ["clip-coach"])
        self.assertFalse(U.doctor(NOW)[0][0])

    def test_2_reserve_is_a_no_watch_is_said(self):
        self.put_now(status(level="reserve", room=-1.0))
        self.assertIn(False, [r[0] for r in U.doctor(NOW)])
        self.put_now(status(level="watch", room=10.0))
        rows = U.doctor(NOW)
        self.assertNotIn(False, [r[0] for r in rows])
        self.assertTrue(any(r[0] is None and r[1].startswith("WATCH") for r in rows))


class TimerOnlyTest(Base):
    def test_1_install_only_the_usage_job_leaves_the_others_alone(self):
        import plistlib
        import subprocess as sp
        from clipfactory import timer as TM
        calls = []

        def lc(args):
            calls.append(args)
            return sp.CompletedProcess(args, 0, "", "")
        agents = self.tmp / "agents"
        scratch = self.tmp / "hermes"
        scratch.mkdir()
        lines = TM.install(None, scratch=scratch, agents_dir=agents, launchctl=lc, preflight=lambda: ["exit 0 the probe ran"], only="usage")
        self.assertEqual([p.name for p in agents.glob("*.plist")], ["com.clipfactory.usage.plist"])
        self.assertTrue(all(c[-1].endswith("com.clipfactory.usage") or c[-1].endswith("usage.plist") for c in calls))
        job = plistlib.loads((agents / "com.clipfactory.usage.plist").read_bytes())
        self.assertEqual(job["ProgramArguments"][-4:], ["usage", "check", "--notify", "--quiet"])
        self.assertEqual(job["StartInterval"], 1800)
        self.assertIn("loaded", lines[0])
        with self.assertRaises(SystemExit):
            TM.install(None, scratch=scratch, agents_dir=agents, launchctl=lc, preflight=lambda: ["exit 0 the probe ran"], only="nope")


class OfflineTest(unittest.TestCase):
    def test_1_a_test_run_never_reads_this_macs_usage(self):
        self.assertEqual(os.environ.get("CLIP_FACTORY_OFFLINE"), "1")
        for p in U.LIVE:
            self.assertTrue(U._offline(p), p)
        self.assertEqual(U.readings(U.LIVE[0]), [])
        self.assertEqual(U.scan(U.LIVE[1], NOW, Path(tempfile.mkdtemp()))["files"], {})
        self.assertEqual(U.ungated(U.LIVE[2], dict(CONF)), [])


if __name__ == "__main__":
    unittest.main()
