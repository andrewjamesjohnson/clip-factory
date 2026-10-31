"""The two guards added after the 2026-10-16 broken uploads: no test reaches a real account, and a YouTube cap error
stops every further try for a day. Throwaway folders; nothing touches the network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)

import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C     # noqa: E402
from clipfactory import direct as D     # noqa: E402
from clipfactory import net             # noqa: E402
from clipfactory import outlets as O    # noqa: E402


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-guard-"))
        self.old_state, C.STATE = C.STATE, self.tmp / "state"
        self.old_urlopen = urllib.request.urlopen
        self.opened = []
        urllib.request.urlopen = lambda *a, **k: self.opened.append(a) or (_ for _ in ()).throw(AssertionError("network"))
        self.acct = C.load().account("politics")

    def tearDown(self):
        C.STATE = self.old_state
        urllib.request.urlopen = self.old_urlopen

    def test_1_a_test_cannot_upload_even_with_a_key_on_disk(self):
        tok = self.tmp / "youtube_token.json"
        tok.write_text(json.dumps({"client_id": "x", "client_secret": "y", "refresh_token": "z",
                                   "access_token": "a", "expires_at": 9e12}))
        with self.assertRaises(net.Offline):
            O._yt_upload(tok, self.tmp / "clip.mp4", {})
        with self.assertRaises(net.Offline):
            D._http("POST", "https://graph.facebook.com/v25.0/123/video_reels", form={"a": "b"})
        self.assertEqual(self.opened, [])          # nothing ever reached urlopen
        # and unittest being loaded is enough on its own, even if a test forgets the switch
        old = _os.environ.pop("CLIP_FACTORY_OFFLINE")
        try:
            self.assertFalse(net.allowed())
        finally:
            _os.environ["CLIP_FACTORY_OFFLINE"] = old

    def test_2_a_cap_error_pauses_youtube_for_a_day(self):
        t0 = 1_800_000_000.0
        self.assertEqual(O.yt_hold(self.acct, now=t0), "")
        O.yt_cool(self.acct, "HTTP 400: uploadLimitExceeded", now=t0)
        self.assertIn("paused", O.yt_hold(self.acct, now=t0 + 3600))
        self.assertIn("daily_budget", O.yt_hold(self.acct, now=t0 + 3600))
        self.assertEqual(O.yt_hold(self.acct, now=t0 + 24 * 3600 + 1), "")
        self.assertTrue(any(k in "The user has exceeded the number of videos they may upload." for k in O.CAP_ERRORS))

    def test_3_never_more_than_the_days_slots_in_24_hours(self):
        t0 = 1_800_000_000.0
        for i in range(O.yt_budget(self.acct)):
            self.assertEqual(O.yt_hold(self.acct, now=t0 + i), "", i)
            O.yt_count(self.acct, now=t0 + i)
        self.assertIn("uploads in the last 24 hours", O.yt_hold(self.acct, now=t0 + 60))
        self.assertEqual(O.yt_hold(self.acct, now=t0 + 86400 + 60), "")

    def test_4_a_daily_budget_under_the_channels_cap(self):
        t0 = 1_800_000_000.0
        acct = C.load().account("politics")
        acct.outlets["youtube_shorts"] = dict(acct.outlets.get("youtube_shorts") or {}, daily_budget=3)
        self.assertEqual(O.yt_budget(acct), 3)
        for i in range(3):
            self.assertEqual(O.yt_room(acct, now=t0 + i), 3 - i)
            O.yt_count(acct, now=t0 + i)
        self.assertIn("its daily YouTube budget is 3", O.yt_hold(acct, now=t0 + 60))
        acct.outlets["youtube_shorts"]["daily_budget"] = 10            # more than the day's 8 slots (2026-10-28): kept,
        self.assertEqual(O.yt_budget(acct), 10)                         # so a rolling 24 hours has room for the morning
        self.assertGreater(O.yt_budget(acct), acct.daily_target)

    def test_5_the_pause_is_said_in_words_and_leaves_no_room(self):
        t0 = 1_800_000_000.0
        self.assertEqual(O.yt_paused(self.acct, now=t0), "")
        O.yt_cool(self.acct, "HTTP 400: uploadLimitExceeded", now=t0)
        self.assertIn("YouTube paused until", O.yt_paused(self.acct, now=t0 + 60))
        self.assertEqual(O.yt_room(self.acct, now=t0 + 60), 0)
        self.assertEqual(O.yt_paused(self.acct, now=t0 + 86401), "")

    def test_6_after_a_pause_todays_clips_go_first(self):
        from datetime import datetime
        from unittest import mock
        from clipfactory import board as B
        from clipfactory import poster as P
        now = datetime(2030, 1, 4, 6, 20).astimezone()
        cfg = C.load()
        acct = cfg.account("politics")
        acct.outlets["youtube_shorts"] = dict(acct.outlets.get("youtube_shorts") or {}, daily_budget=3)
        with B.edit(cfg, acct, now.date()) as bd:      # two signed, one a draft nobody signed yet (2026-10-28: it counts)
            bd["slots"] = {f"0{i}:00": {"slot": f"0{i}:00", "status": B.SCHEDULED if i < 8 else B.READY,
                                        **({"signoff": {"by": "Andrew"}} if i < 8 else {}), "outlets": {},
                                        "publish_at": now.replace(hour=i, minute=0).isoformat()}
                           for i in range(6, 9)}
        with mock.patch.object(O, "covers_slot", lambda *a: True):
            self.assertFalse(P._yt_room_for_yesterday(acct, now))    # 3 of today wait, budget 3: none for yesterday
            with B.edit(cfg, acct, now.date()) as bd:
                bd["slots"]["06:00"]["outlets"] = {"youtube_shorts": {"video_id": "x"}}
            O.yt_count(acct, now=now.timestamp() - 60)
            self.assertFalse(P._yt_room_for_yesterday(acct, now))    # 2 wait (the draft too), 2 left
            acct.outlets["youtube_shorts"]["daily_budget"] = 4
            self.assertTrue(P._yt_room_for_yesterday(acct, now))     # 2 wait, 3 left: one late clip of yesterday


if __name__ == "__main__":
    unittest.main()
