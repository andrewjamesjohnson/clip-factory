import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""The suite passes at any hour. On 2026-10-20 it failed between 05:50 and 06:25 on the clock alone: the render queue
applied the night shift's real-clock limits (start by 05:50, stopped at 06:15) to every test that queued a step, and
the bench CLI test ran the real 06:18 fill, which waited for quiet past its timeout. Now a test run has no night limits
(livestate.daytime_queue: CLIP_RENDERLOCK_NIGHT defaults to 0 when unittest is loaded, children included), a real run
keeps them, and the queue's own tests run again here with the wall clock faked to 05:55 and 06:20
(tests/fakeclock/sitecustomize.py, which can run the whole suite at any hour)."""
import shutil
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from clipfactory import livestate as LS     # noqa: E402
from clipfactory import renderlock as RL    # noqa: E402

AT_0555 = datetime(2030, 1, 7, 5, 55).astimezone().timestamp()
# the tests that met the night limits by the real clock, and the bench CLI that met the 06:18 fill
CLOCK_TESTS = ("test_renderlock", "test_night_shift.NightLimits", "test_bench.CommandLine")


def _env(**kw) -> dict:
    env = dict(os.environ, **kw)
    env.pop("CLIP_FACTORY_FAKE_CLOCK_OFFSET", None)       # a whole-suite fake clock: these children set their own
    return env


class TestRun(unittest.TestCase):
    def test_a_test_run_has_no_night_limits(self):
        self.assertEqual(LS.NIGHT_ENV, RL.NIGHT_ENV)
        self.assertEqual(os.environ.get(RL.NIGHT_ENV), "0")
        self.assertEqual(RL.night_limits(AT_0555), (None, None))
        r = subprocess.run([sys.executable, "-c", f"import os; print(os.environ.get({RL.NIGHT_ENV!r}))"],
                           capture_output=True, text=True, env=_env(), timeout=60)
        self.assertEqual(r.stdout.strip(), "0", r.stderr)            # what it starts inherits it

    def test_a_real_run_keeps_them(self):
        """The factory never loads unittest: with nothing set, 05:55 is past the night's 05:50 start-by."""
        env = _env()
        env.pop(RL.NIGHT_ENV, None)
        code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); from clipfactory import renderlock as RL; "
                f"from datetime import datetime as D; u, s = RL.night_limits({AT_0555!r}); "
                "print('unittest' in sys.modules, D.fromtimestamp(u).strftime('%H:%M'), D.fromtimestamp(s).strftime('%H:%M'))")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.stdout.split(), ["False", "05:50", "06:15"], r.stdout + r.stderr)

    def test_the_fake_clock(self):
        r = subprocess.run([sys.executable, "-c", "import subprocess, sys, time, datetime as d; "
                            "print(time.strftime('%H:%M'), d.datetime.now().strftime('%H:%M'), "
                            "subprocess.run([sys.executable, '-c', 'import time; print(time.strftime(\"%H:%M\"))'], "
                            "capture_output=True, text=True).stdout.strip())"],
                           capture_output=True, text=True, timeout=60,
                           env=_env(PYTHONPATH=str(TESTS / "fakeclock"), CLIP_FACTORY_FAKE_CLOCK="05:55"))
        got = r.stdout.split()
        self.assertEqual(len(got), 3, r.stdout + r.stderr)
        self.assertTrue(all(t in ("05:55", "05:56") for t in got), got)   # the child too, on the same clock


class AtAnyHour(unittest.TestCase):
    def test_the_clock_tests_at_0555_and_0620(self):
        runs = {}
        for at in ("05:55", "06:20"):                          # side by side: each has its own state folder
            state = tempfile.mkdtemp(prefix="clipfactory-clock-")
            self.addCleanup(shutil.rmtree, state, True)
            runs[at] = subprocess.Popen([sys.executable, "-m", "unittest", *CLOCK_TESTS], cwd=str(TESTS),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        env=_env(PYTHONPATH=str(TESTS / "fakeclock"), CLIP_FACTORY_FAKE_CLOCK=at,
                                                 CLIP_FACTORY_STATE=state))
        for at, p in runs.items():
            with self.subTest(at=at):
                try:
                    _, err = p.communicate(timeout=600)
                except subprocess.TimeoutExpired:
                    p.kill()
                    _, err = p.communicate()
                self.assertEqual(p.returncode, 0, f"at {at}:\n" + err[-4000:])


if __name__ == "__main__":
    unittest.main()
