import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The social doctor over state files that parse but have the wrong shape (2026-10-20 review): a learn report holding
# a list costs only that account's lines, never the other account's; a board holding a list never makes
# `factory.py social doctor` raise; a settings.json whose history is not a list never stops the log page's check.
# Throwaway folders only; no network.

import contextlib
import io
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from clipfactory import board as B          # noqa: E402
from clipfactory import learn as L          # noqa: E402
from clipfactory import logpage as LP       # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import social as SOC       # noqa: E402
from clipfactory import switches as SW      # noqa: E402
import social_fakes as F                    # noqa: E402

NOW = datetime(2031, 7, 8, 16, 0, tzinfo=timezone.utc)


class DoctorShapes(unittest.TestCase):
    def setUp(self):
        F.cfg_in_tmp(self)
        SW._SEEN.clear()
        self.addCleanup(SW._SEEN.clear)
        self.owners = [a for a in self.cfg.accounts.values() if not SOC._is_client(a)]
        self.assertGreaterEqual(len(self.owners), 2)

    def settings(self, a, data: dict) -> None:
        p = SW.file_path(a)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
        SW._SEEN.clear()

    def test_a_list_report_costs_only_its_own_account(self):
        for a in self.owners:
            self.settings(a, {"v": 1, "modes": {"learn": "on"}})
        bad, good = self.owners[0], self.owners[1]
        rp = L.report_path(bad, L._local(self.cfg, NOW).date() - timedelta(days=1))
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text("[1]")
        rows = L.doctor(self.cfg, NOW)                                   # no raise
        texts = [r[1] for r in rows]
        self.assertTrue(any(t.startswith(f"{bad.name} reports: made ?") for t in texts), texts)
        self.assertTrue(any(t.startswith(f"{good.name} reports:") for t in texts), texts)
        self.assertTrue(any(t.startswith(f"{good.name} playbook:") for t in texts), texts)
        doc = [r[1] for r in SOC.doctor(self.cfg, NOW)]
        self.assertFalse(any(t.startswith("learn: its check failed") for t in doc), doc)

    def test_one_account_failing_keeps_the_others_lines(self):
        bad, good = self.owners[0], self.owners[1]
        real = L.proposals

        def boom(a):
            if a.slug == bad.slug:
                raise ValueError("broken proposals")
            return real(a)
        with mock.patch.object(L, "proposals", boom):
            rows = L.doctor(self.cfg, NOW)
        fails = [r for r in rows if r[0] is False and "learn: its check failed" in r[1]]
        self.assertEqual(len(fails), 1, rows)
        self.assertTrue(fails[0][1].startswith(bad.name))
        self.assertIn(f"state/{bad.slug}/learn/", fails[0][2])
        self.assertTrue(any(r[1].startswith(f"{good.name} reports:") for r in rows), rows)
        self.assertTrue(any(r[1].startswith(f"{good.name} playbook:") for r in rows), rows)

    def test_a_list_board_never_makes_the_social_doctor_raise(self):
        a = self.owners[0]
        today = NOW.astimezone(self.cfg.tz()).date()
        bp = B.board_path(a, today)
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text("[1, 2]")
        self.assertEqual(SOC._extras_problems(self.cfg, NOW, [a]), [])
        SOC.doctor(self.cfg, NOW)                                        # no raise
        with mock.patch.object(self.cfg, "now", lambda: NOW), contextlib.redirect_stdout(io.StringIO()):
            REG.run_cli(self.cfg, "social", ["doctor"])                  # factory.py social doctor: no traceback

    def test_an_extras_check_that_raises_is_one_no_row(self):
        with mock.patch.object(SOC, "_extras_problems", side_effect=AttributeError("odd board")):
            rows = SOC.doctor(self.cfg, NOW)
        bad = [r for r in rows if "first comment and poll files: their check failed" in r[1]]
        self.assertEqual(len(bad), 1, rows)
        self.assertIs(bad[0][0], False)
        self.assertIn("odd board", bad[0][1])

    def test_history_not_a_list_never_stops_the_log_page_check(self):
        a = self.owners[0]
        key = "first_comment.facebook_reels"
        self.settings(a, {"v": 1, "modes": {key: "practice"}, "history": 5})
        self.assertEqual(SW.mode(a, key), "practice")
        self.assertIsNone(SW.practice_since(a, key))
        LP.doctor(self.cfg, NOW)                                         # no raise
        doc = [r[1] for r in SOC.doctor(self.cfg, NOW)]
        self.assertFalse(any(t.startswith("logpage: its check failed") for t in doc), doc)


if __name__ == "__main__":
    unittest.main()
