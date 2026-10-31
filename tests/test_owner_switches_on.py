"""The owner's posting switches are ON (Andrew, 2026-10-17 night: implement every audit call). Their behaviour is
tested in test_posting_fixes; this pins the live config, so turning one off is a deliberate change. No network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-switches-state-")

import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C   # noqa: E402
from clipfactory import poster as P   # noqa: E402


class SwitchesOnTest(unittest.TestCase):
    def setUp(self):
        self.cfg = C.load(ROOT / "config" / "accounts.json")
        # every owner block, switched on or not (2026-10-19: the new GTA accounts ship off), so a new account is pinned
        # before it is switched on, and the first two are always there
        self.owners = [s for s, a in self.cfg.all_accounts.items() if a.raw.get("owner") != "client"]
        assert self.owners[:2] == ["gaming", "politics"], self.owners

    def test_1_youtube_spread_on_both_channels(self):
        for slug in self.owners:
            acct = self.cfg.account(slug)
            self.assertEqual(P._yt_ahead(acct), timedelta(hours=2), slug)
            self.assertEqual(P._yt_gap(acct), timedelta(minutes=3), slug)

    def test_2_gta_tags_and_the_politics_category(self):
        self.assertTrue(self.cfg.account("gaming").outlets["youtube_shorts"].get("tags_from_hashtags"))
        self.assertEqual(self.cfg.account("politics").outlets["youtube_shorts"].get("category_id"), "25")

    def test_3_signed_by_hand_stays_on_both(self):
        for slug in self.owners:
            self.assertIs(self.cfg.account(slug).raw.get("signed_by_hand_stays"), True, slug)


if __name__ == "__main__":
    unittest.main()
