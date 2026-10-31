"""stats.match (audit SM-19): a video matched to a slot by its words alone must also be the length that platform was
sent. Two Politics clips from one hearing open on the same dateline, so the words alone cannot tell them apart.
Pure function; no network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-statsmatch-state-")

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import stats as ST   # noqa: E402

DATELINE = "Senate Banking, July 15 2026, the Federal Reserve's semiannual monetary policy report."


def slot(key: str, secs: float, at: str, cut: float = 0.0) -> dict:
    s = {"_key": key, "slot": key[-5:], "slug": key, "publish_at": at, "clip": {"seconds": secs}, "outlets": {}}
    if cut:
        s["cuts"] = {"instagram_reels": {"seconds": cut}}
    return s


def video(pid: str, dur: float, at: str) -> dict:
    return {"platform_id": pid, "text": DATELINE + " More words.", "published_at": at, "dur": dur}


class LengthMatchTest(unittest.TestCase):
    def setUp(self):
        self.orig = ST._slot_text
        ST._slot_text = lambda s, plat: DATELINE      # both clips' captions open the same way

    def tearDown(self):
        ST._slot_text = self.orig

    def test_1_siblings_from_one_hearing_get_their_own_videos(self):
        a = slot("2026-10-17 05:00", 76.0, "2026-10-17T05:00:00-06:00")
        b = slot("2026-10-17 07:00", 88.0, "2026-10-17T07:00:00-06:00")
        # only the 88s clip went out (by hand), a little before the 05:00 slot's time is closest
        got = ST.match([a, b], "tiktok", [video("tt88", 88.4, "2026-10-17T05:10:00-06:00")])
        self.assertEqual({k: v["platform_id"] for k, v in got.items()}, {"2026-10-17 07:00": "tt88"})

    def test_2_unknown_length_still_matches_by_words(self):
        a = slot("2026-10-17 05:00", 76.0, "2026-10-17T05:00:00-06:00")
        got = ST.match([a], "instagram_reels", [video("ig1", 0, "2026-10-17T05:01:00-06:00")])
        self.assertEqual(got["2026-10-17 05:00"]["platform_id"], "ig1")

    def test_3_a_platform_cut_is_the_length_that_counts(self):
        a = slot("2026-10-17 05:00", 76.0, "2026-10-17T05:00:00-06:00", cut=46.0)
        self.assertEqual(ST._sent_secs(a, "instagram_reels"), 46.0)
        self.assertEqual(ST._sent_secs(a, "tiktok"), 76.0)
        got = ST.match([a], "instagram_reels", [video("ig46", 46.2, "2026-10-17T05:01:00-06:00")])
        self.assertIn("2026-10-17 05:00", got)
        got = ST.match([a], "instagram_reels", [video("ig76", 76.0, "2026-10-17T05:01:00-06:00")])
        self.assertEqual(got, {})

    def test_4_a_recorded_id_wins_whatever_the_length(self):
        a = slot("2026-10-17 05:00", 76.0, "2026-10-17T05:00:00-06:00")
        a["outlets"]["youtube_shorts"] = {"video_id": "yt1"}
        got = ST.match([a], "youtube_shorts", [video("yt1", 12.0, "2026-10-17T05:00:00-06:00")])
        self.assertEqual(got["2026-10-17 05:00"]["platform_id"], "yt1")


if __name__ == "__main__":
    unittest.main()
