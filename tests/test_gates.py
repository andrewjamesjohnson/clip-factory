"""Check the checkers (the owner, 2026-10-31, the loops thesis item 3). Throwaway folders; no network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-gates-state-"))

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C       # noqa: E402
from clipfactory import gates as GT       # noqa: E402
from clipfactory import journal as J      # noqa: E402
from clipfactory import makers as MK      # noqa: E402

RUN = """== preflight
  [ok  ] LENGTH   41.2 s
  [FAIL] BROLL    insert 2 shows nothing from GTA
  [FAIL] OPENING  the first words are 'and so'
STOPPED: refused at BROLL, OPENING
"""


class GatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-gates-"))
        self.old_state, C.STATE = C.STATE, self.tmp / "state"

    def tearDown(self):
        C.STATE = self.old_state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fail_words_by_gate(self):
        self.assertEqual(MK.fail_words(RUN), {"BROLL": "insert 2 shows nothing from GTA",
                                              "OPENING": "the first words are 'and so'"})

    def test_journal_and_night_refusals_are_counted_and_sampled(self):
        J.record("gate gaming", "refused", gates=["BROLL", "OPENING"], day="2026-10-31", slot="07:00", candidate=1,
                 why={"BROLL": "insert 2 shows nothing from GTA"})
        night = self.tmp / "night.jsonl"
        night.write_text(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
                         "content": "cut 3: REFUSED BURNED the name strip reads as a caption"}]}}) + "\n")
        rep = GT.report(files={str(night): {"task": "hearing-clip-sweep-2"}})
        self.assertEqual(rep["count"], {"BROLL": 1, "OPENING": 1, "BURNED": 1})
        text = "\n".join(GT.words(rep))
        self.assertIn("insert 2 shows nothing from GTA", text)
        self.assertIn("REFUSED BURNED the name strip reads as a caption", text)
        C.STATE = self.tmp / "empty"                      # no journal at all: nothing to count
        self.assertIn("no refusal recorded", GT.words(GT.report(files={}))[0])


if __name__ == "__main__":
    unittest.main()
