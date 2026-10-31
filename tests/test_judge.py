"""The judge graded against Andrew (the owner, 2026-10-31, the loops thesis item 18). Throwaway folders; no network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-judge-state-"))

import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import coach as CO       # noqa: E402
from clipfactory import config as C       # noqa: E402
from clipfactory import judge as J        # noqa: E402
from clipfactory import store             # noqa: E402
from clipfactory import verdicts as VD    # noqa: E402

DAY = date(2031, 6, 3)
NOW = datetime(2031, 6, 10, 12, 0, tzinfo=timezone.utc)
SCORES = {"hook": 6, "keep_watching": 6, "no_sound": 8, "density": 7, "share": 5}


class JudgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-judge-"))
        self.old_state, C.STATE = C.STATE, self.tmp / "state"
        self.cfg = C.load(ROOT / "config" / "accounts.json")
        self.acct = self.cfg.account("politics")

    def tearDown(self):
        C.STATE = self.old_state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, verdict, word, avg, days_ago, by="Andrew"):
        at = (NOW - timedelta(days=days_ago)).replace(microsecond=0).isoformat()
        store.append(VD._path(self.acct.slug), {"at": at, "by": by, "verdict": verdict, "board": DAY.isoformat(),
                                                "slot": "07:00", "clip_sha": "x", "coach": {"word": word, "avg": avg}})

    def test_auc_and_grade(self):
        self.assertEqual(J.auc([7, 8], [5, 6]), 1.0)
        self.assertEqual(J.auc([5], [5]), 0.5)
        self.assertEqual(J.auc([4], [6]), 0.0)
        self.assertIsNone(J.auc([], [1]))
        g = J.grade([{"call": "yes", "word": "PASS", "avg": 8}, {"call": "no", "word": "FIX", "avg": 5},
                     {"call": "yes", "word": "FIX", "avg": 7}, {"call": "no", "word": "", "avg": None}])
        self.assertEqual((g["n"], g["words_n"], round(g["words_agree"], 3), round(g["held_share"], 3)), (4, 3, 0.667, 0.667))
        self.assertEqual(g["auc"], 1.0)

    def test_report_reads_his_calls_only_and_says_drift(self):
        for d in range(8, 14):
            self.call("approve", "PASS", 8, d)
            self.call("decline", "FIX", 5, d)
        for d in range(0, 6):
            self.call("approve", "FIX", 5, d)
            self.call("decline", "PASS", 8, d)
        self.call("approve", "PASS", 9, 1, by=J.AUTO)          # the factory's own signature is never his call
        rep = J.report(self.acct, now=NOW)
        self.assertEqual(rep["all"]["n"], 24)
        self.assertEqual(rep["prev7"]["words_agree"], 1.0)
        self.assertEqual(rep["last7"]["words_agree"], 0.0)
        self.assertEqual(sorted(rep["drift"]), ["auc", "words_agree"])
        self.assertFalse(rep["gate_ready"])
        self.assertTrue(any("DRIFT" in ln for ln in J.words(rep)))

    def test_a_call_saves_the_coachs_word(self):
        slot = {"slot": "07:00", "slug": "x", "clip": {"sha256": "abc"}}
        p = CO.card_path(self.acct, DAY, "07:00")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"clip_sha": "abc", "written": {"verdict": "FIX", "scores": SCORES}}))
        row = VD.record(self.cfg, self.acct, DAY, slot, "decline", "Andrew")
        self.assertEqual(row["coach"], {"word": "FIX", "avg": 6.4})
        p.write_text(json.dumps({"clip_sha": "older", "written": {"verdict": "PASS", "scores": SCORES}}))
        self.assertNotIn("coach", VD.record(self.cfg, self.acct, DAY, slot, "approve", "Andrew"))


if __name__ == "__main__":
    unittest.main()
