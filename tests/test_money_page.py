"""The Money tab (moneypage.py, 2026-10-29): the Stats tab's numbers turned into a coach. Fake stats data; no network,
no state."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C          # noqa: E402
from clipfactory import moneypage as MP      # noqa: E402
from clipfactory import review as R          # noqa: E402
from clipfactory import statspage as SP      # noqa: E402

NOW = datetime(2026, 10, 29, 16, 0, tzinfo=timezone.utc)


def post(acct, plat, day, views, dur=40):
    return {"account": acct, "platform": plat, "day": day, "views": views, "dur": dur}


def fake(eligible_politics=False):
    return {
        "accounts": [{"id": "gaming", "name": "Gaming"}, {"id": "politics", "name": "Politics"}],
        "posts": [post("politics", "facebook", "2026-10-20", 1000),
                  post("politics", "facebook", "2026-09-01", 9999),         # older than 30 days
                  post("politics", "youtube", "2026-10-01", 500),
                  post("politics", "youtube", "2026-08-15", 700),           # inside 90 days, not 30
                  post("politics", "tiktok", "2026-10-25", 300, dur=75),    # over 1 minute: Creator Rewards
                  post("politics", "tiktok", "2026-10-25", 100, dur=50),
                  post("gaming", "instagram", "2026-10-28", 200)],
        "followers": {"politics": {"youtube": {"followers": 52}, "facebook": {"followers": 256},
                                         "tiktok": {"followers": None}},
                      "gaming": {"youtube": {"followers": 34}}},
        "settings": {"rpm": {"youtube": 0.05, "facebook": 0.10, "tiktok_long": 0.6, "campaign": 1.0},
                     "goal": 10000.0, "followers_typed": {"politics": {"tiktok": {"n": 1200, "at": "2026-10-28"}}},
                     "eligible": {"politics": {"facebook": eligible_politics}, "gaming": {}}},
        "campaign_fees": 0.1, "read_at": "2026-10-29T16:00:00+00:00"}


class MoneyTest(unittest.TestCase):
    def setUp(self):
        self.cfg = C.load()
        p = mock.patch.object(SP, "data", lambda cfg, now=None: fake())
        p.start()
        self.addCleanup(p.stop)

    def test_1_the_numbers_count_the_right_days_and_the_typed_followers(self):
        n = MP.numbers(self.cfg, NOW)
        led = next(a for a in n["accounts"] if a["slug"] == "politics")
        self.assertEqual(led["v30"], {"facebook": 1000, "youtube": 500, "tiktok": 400})   # 09-30 on: the last 30 days
        self.assertEqual(led["v90"]["youtube"], 1200)
        self.assertEqual(led["followers"]["tiktok"], 1200)                  # typed on Stats, the platform not read
        self.assertAlmostEqual(led["campaign"], 1900 / 1000 * 1.0 * 0.9)
        self.assertAlmostEqual(led["programs"], (1000 * 0.10 + 500 * 0.05 + 300 * 0.6) / 1000)
        self.assertEqual(n["goal"], 10000.0)

    def test_2_the_coach_puts_what_pays_soonest_first_with_the_real_numbers(self):
        ps = MP.plays(MP.numbers(self.cfg, NOW))
        self.assertEqual([p["key"] for p in ps][:3], ["campaigns", "service", "facebook"])
        fb = next(p for p in ps if p["key"] == "facebook")
        self.assertIn("1,000 in 30 days", fb["why"])
        yt = next(p for p in ps if p["key"] == "youtube")
        self.assertIn("the Politics 1,200", yt["why"])
        self.assertTrue(all(p["steps"] for p in ps))

    def test_3_the_page_shows_the_tab_the_plan_the_note_and_where_the_facts_come_from(self):
        h = MP.page(self.cfg, NOW, "t", R.CSS, R.tabs("money", "all", self.cfg), R.switch(self.cfg, "money", "all"))
        self.assertIn('<a href="/money" aria-current=page>Money</a>', h)
        self.assertIn("no account is in a paid program yet", h)
        self.assertIn("A path to $10K a month", h)
        self.assertIn('id="copy"', h)
        self.assertIn("I can post 30 like these a month", h)
        for _, url in MP.SOURCES:
            self.assertIn(url, h)
        self.assertIn('href="/money?acct=gaming"', R.switch(self.cfg, "money", "all"))    # the switch stays put

    def test_4_an_account_in_a_program_sends_earnings_to_stats(self):
        with mock.patch.object(SP, "data", lambda cfg, now=None: fake(eligible_politics=True)):
            h = MP.page(self.cfg, NOW, "t", R.CSS, R.tabs("money"), "")
        self.assertIn("On Stats", h)
        self.assertIn("in: facebook", h)

    def test_5_money_sits_after_stats_in_the_tab_strip(self):
        strip = R.tabs("clips")
        self.assertLess(strip.index('href="/stats"'), strip.index('href="/money"'))
        self.assertLess(strip.index('href="/money"'), strip.index('href="/inbox"') if 'href="/inbox"' in strip else 10**9)


if __name__ == "__main__":
    unittest.main()
