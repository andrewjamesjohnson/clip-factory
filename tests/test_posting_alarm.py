"""When posting breaks, Andrew hears it (audit PP-15): one Mac notification on the third failed post-due run in a row,
never from a test or a copy; and the 05:25 morning notice opens with the red line. Throwaway state; no network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-alarm-state-")

import argparse
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C   # noqa: E402
from clipfactory import poster as P   # noqa: E402


def factory_module():
    spec = importlib.util.spec_from_file_location("factory_cli_for_alarm_test", ROOT / "factory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AlarmTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-alarm-"))
        self.old_state, C.STATE = C.STATE, self.tmp / "state"
        C.STATE.mkdir(parents=True)
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "drafts")
        (self.tmp / "accounts.json").write_text(json.dumps(raw))
        self.cfg = C.load(self.tmp / "accounts.json")
        self.told = []
        self.orig = (P._tell_owner, P._pass)
        P._tell_owner = lambda text: self.told.append(text)

    def tearDown(self):
        P._tell_owner, P._pass = self.orig
        C.STATE = self.old_state

    def test_1_third_failed_run_tells_once(self):
        def broken(*a, **k):
            raise ImportError("cannot import name 'fb_post' from 'clipfactory.direct'")
        P._pass = broken
        for _ in range(5):
            with self.assertRaises(ImportError):
                P.post_due(self.cfg)
        self.assertEqual(len(self.told), 1, self.told)
        self.assertIn("Posting broke 3 runs in a row", self.told[0])
        self.assertIn("ImportError", self.told[0])

    def test_2_a_clean_run_starts_the_count_again(self):
        def broken(*a, **k):
            raise RuntimeError("boom")
        P._pass = broken
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                P.post_due(self.cfg)
        P._pass = lambda *a, **k: ([], False)
        P.post_due(self.cfg)
        P._pass = broken
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                P.post_due(self.cfg)
        self.assertEqual(self.told, [])

    def test_3_never_a_notification_from_a_test(self):
        P._tell_owner = self.orig[0]
        ran = []
        orig_run = P.subprocess.run
        P.subprocess.run = lambda *a, **k: ran.append(a)
        try:
            P._tell_owner("x")
        finally:
            P.subprocess.run = orig_run
        self.assertEqual(ran, [])

    def test_4_morning_notice_opens_with_the_red_line(self):
        (C.STATE / "post-due.health").write_text(json.dumps({
            "last_run": P._stamp(), "last_error": "ImportError: boom", "error_at": P._stamp(), "fails": 4}))
        fac = factory_module()
        out = io.StringIO()
        with redirect_stdout(out):
            fac.cmd_morning(self.cfg, argparse.Namespace(date="today", account=None, notify=False))
        first = out.getvalue().strip().splitlines()[0]
        self.assertTrue(first.startswith("Posting broke at"), out.getvalue())


if __name__ == "__main__":
    unittest.main()
