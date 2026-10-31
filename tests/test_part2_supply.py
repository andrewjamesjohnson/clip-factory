import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""GTA supply plan, Part 2 (2026-10-20), offline: a last-resort row is dealt after every other row, whatever its
channel; the watchlist scan takes uploads up to watch_since_days (7) old at first sight. No skill is run."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from clipfactory import makers as M          # noqa: E402
from test_gta_maker_fixes import Base, row   # noqa: E402


class LastResortLast(Base):
    def test_a_last_resort_row_follows_every_other_row(self):
        """10-20: Onespot's GTA 5 helicopters (its channel's only upload, last_resort) were dealt in the first pass,
        ahead of a reopened GTA 6 video of a channel already dealt."""
        self.inbox([row(1, channel="mrboss", title="GTA 6 map"),
                    row(2, channel="mrboss", title="GTA 6 hidden details"),
                    row(3, channel="onespot", title="GTA 5 helicopters", score=0.05, last_resort=True),
                    row(4, channel="tgg", title="GTA 6 IGN")])
        got = [r["title"] for r in M.gta_sources(self.acct, set())]
        self.assertEqual(got, ["GTA 6 map", "GTA 6 IGN", "GTA 6 hidden details", "GTA 5 helicopters"])

    def test_last_resort_rows_keep_their_own_order_and_still_come(self):
        self.inbox([row(1, channel="a", title="gta 5 one", score=0.05, last_resort=True),
                    row(2, channel="b", title="gta 5 two", score=0.07, last_resort=True)])
        self.assertEqual([r["title"] for r in M.gta_sources(self.acct, set())], ["gta 5 two", "gta 5 one"])


class ScanWindow(Base):
    def test_watch_since(self):
        self.assertEqual(M.watch_since({}), 7.0)
        self.assertEqual(M.watch_since({"watch_since_days": 3}), 3.0)
        self.assertEqual(M.watch_since({"watch_since_days": 30, "max_age_days": 14}), 14.0)
        self.assertEqual(M.watch_since({"watch_since_days": 0.2}), 1.0)
        self.assertEqual(M.watch_since({"watch_since_days": "x"}), 7.0)

    def test_the_night_scans_seven_days(self):
        self.inbox([])
        calls, _ = self.make()
        scans = [c for c in calls if c[1] == "watch.py"]
        self.assertTrue(scans)
        self.assertIn("--since=7", scans[0])
        self.reload(watch_since_days=3)
        calls, _ = self.make()
        self.assertIn("--since=3", [c for c in calls if c[1] == "watch.py"][0])


if __name__ == "__main__":
    unittest.main()
