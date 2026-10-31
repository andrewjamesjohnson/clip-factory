"""A Politics clip whose ending the render could not confirm (CHECK THE ENDING) never signs itself (Andrew 2026-10-26,
"Yes, build all 6": 44 of 95 endings flagged, and the automatic sign-off shipped them)."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import autosign as A    # noqa: E402


class EndingHold(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-ending-"))
        self.work = self.tmp / "clipwork" / "job-x"
        (self.work / "tmp").mkdir(parents=True)
        jd = self.tmp / "acct" / "Jobs" / "Job X"
        jd.mkdir(parents=True)
        (jd / "project.json").write_text(json.dumps({"source": str(self.work / "master.mp4")}))
        self.acct = SimpleNamespace(maker="politics", account_dir=self.tmp / "acct")
        self.slot = {"slug": "the-clip", "job": "Job X", "publish_at": "2030-01-03T06:30:00-06:00"}

    def ending(self, verified):
        (self.work / "tmp" / "01-the-clip.ending.json").write_text(json.dumps(
            {"rule": "pause", "today": {"word": "ASEAN."}, "verified": verified}))

    def test_an_unheard_ending_waits(self):
        self.ending(False)
        why = A.ending_unheard(self.acct, self.slot)
        self.assertIn("CHECK THE ENDING", why)
        self.assertIn("ASEAN.", why)

    def test_a_confirmed_ending_or_no_record_signs(self):
        self.assertEqual(A.ending_unheard(self.acct, self.slot), "")      # nothing on disk: never held
        self.ending(True)
        self.assertEqual(A.ending_unheard(self.acct, self.slot), "")

    def test_gta_is_never_held_by_it(self):
        self.ending(False)
        self.acct.maker = "gta"
        self.assertEqual(A.ending_unheard(self.acct, self.slot), "")


if __name__ == "__main__":
    unittest.main()
