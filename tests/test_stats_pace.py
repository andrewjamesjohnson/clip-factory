import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""Real pace in Stats (next idea 4): "this month" is the views gained since the month's first snapshot (stats.month),
follower counts carry their gain this month on each payout gate, and TikTok's count, which its sign-in cannot read yet,
can be typed in on the page. Throwaway folders and fake numbers only; nothing reaches the network."""
import json
import shutil
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C         # noqa: E402
from clipfactory import stats as ST         # noqa: E402
from clipfactory import statspage as SP     # noqa: E402

UTC = timezone.utc


def iso(d: datetime) -> str:
    return d.astimezone(UTC).isoformat()


def post(pid: str, up: datetime, views: int, acct: str = "gaming", plat: str = "youtube") -> dict:
    return {"id": pid, "account": acct, "platform": plat, "at": iso(up), "views": views}


class Pace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-pace-"))
        cls.old = (C.STATE, ST.OUT)
        C.STATE = cls.tmp / "state"
        ST.OUT = C.STATE / "stats"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(cls.tmp / "drafts")
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))

    @classmethod
    def tearDownClass(cls):
        C.STATE, ST.OUT = cls.old
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        shutil.rmtree(self.tmp / "state", ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.tz = self.cfg.tz()
        SP._cache["key"] = None

    def at(self, d: int, h: int, m: int = 30, month: int = 3) -> datetime:
        return datetime(2031, month, d, h, m, tzinfo=self.tz)

    def fol(self, n: int, tt_error: bool = True) -> dict:
        by = {"youtube_shorts": {"followers": n, "views": 1000, "at": "x", "field": "f"}}
        if tt_error:
            by["tiktok"] = {"error": "not readable: needs user.info.stats"}
        return {"gaming": by}

    # ------------------------------------------------------------------------------------------ stats.month
    def test_1_this_month_is_gained_since_the_months_first_read(self):
        feb = self.at(28, 3, month=2)
        old = self.at(20, 12, month=2)
        ST.snapshot(self.cfg, [post("a", old, 100)], self.fol(25), now=feb)          # last month: not the base
        ST.snapshot(self.cfg, [post("a", old, 150), post("b", old, 10), post("e", self.at(1, 0, 30), 20)],
                    self.fol(30), now=self.at(1, 3))
        ST.snapshot(self.cfg, [post("a", old, 400), post("b", old, 10), post("e", self.at(1, 0, 30), 70),
                               post("n", self.at(2, 9), 50), post("c", self.at(1, 0, month=2), 999)],
                    self.fol(36), now=self.at(3, 3))
        m = ST.month(self.cfg, now=self.at(3, 12))
        self.assertEqual(m["month"], "2031-03")
        self.assertEqual(m["gains"], {"a": 250, "e": 70, "n": 50})   # e and n went up this month: whole;
        self.assertFalse(m["partial"])                                 # c was first seen later: no starting point
        self.assertEqual(m["reads"], 2)
        self.assertEqual(datetime.fromisoformat(m["base_at"]), self.at(1, 3))
        self.assertEqual(m["followers"]["gaming"]["youtube_shorts"]["gain"], 6)
        self.assertEqual(m["followers"]["gaming"]["youtube_shorts"]["now"], 36)
        self.assertNotIn("tiktok", m["followers"]["gaming"])        # a count that could not be read has no gain
        # next month starts from nothing until its first read
        self.assertEqual(ST.month(self.cfg, now=self.at(1, 1, month=4))["base_at"], "")

    def test_2_a_month_whose_history_began_late_says_so(self):
        ST.snapshot(self.cfg, [post("a", self.at(1, 9, month=2), 500)], now=self.at(17, 18))
        ST.snapshot(self.cfg, [post("a", self.at(1, 9, month=2), 650)], now=self.at(18, 3))
        m = ST.month(self.cfg, now=self.at(18, 9))
        self.assertTrue(m["partial"])
        self.assertEqual(m["gains"], {"a": 150})
        (ST.OUT / "history" / "2031-03-17.json").write_text("{broken")
        self.assertEqual(ST.month(self.cfg, now=self.at(18, 9))["gains"], {})       # a broken day is no crash
        self.assertEqual(ST.month(self.cfg, now=self.at(18, 9))["reads"], 1)

    def test_3_no_history_yet(self):
        m = ST.month(self.cfg, now=self.at(5, 9))
        self.assertEqual((m["base_at"], m["gains"], m["followers"], m["partial"]), ("", {}, {}, False))

    def test_4_the_page_data_carries_the_month(self):
        ST.snapshot(self.cfg, [post("a", self.at(1, 9, month=2), 500)], self.fol(30), now=self.at(1, 3))
        ST.snapshot(self.cfg, [post("a", self.at(1, 9, month=2), 650)], self.fol(31), now=self.at(2, 3))
        d = SP.data(self.cfg, now=self.at(2, 9))
        self.assertEqual(d["month"]["gains"], {"a": 150})
        self.assertEqual(d["month"]["followers"]["gaming"]["youtube"]["gain"], 1)   # the page's platform names
        (ST.OUT / "history").rename(self.tmp / "gone")
        (ST.OUT / "history").write_text("not a folder")
        SP._cache["key"] = None
        self.assertEqual(SP.data(self.cfg, now=self.at(2, 9))["month"]["gains"], {})   # never takes the tab down
        (ST.OUT / "history").unlink()

    # ------------------------------------------------------------------------------------------ typed follower counts
    def test_5_a_typed_tiktok_count_is_kept_with_its_day(self):
        self.assertEqual(SP.settings()["followers_typed"], {})
        ok, _ = SP.save_settings({"followers_typed": {"politics": {"tiktok": 1}}})
        self.assertTrue(ok)
        t = SP.settings()["followers_typed"]["politics"]["tiktok"]
        self.assertEqual((t["n"], t["at"]), (1, date.today().isoformat()))
        p = ST.OUT / "settings.json"
        s = json.loads(p.read_text())
        s["followers_typed"]["politics"]["tiktok"]["at"] = "2031-01-01"
        p.write_text(json.dumps(s))
        SP.save_settings({"followers_typed": {"politics": {"tiktok": 1}}, "goal": 500})   # same count: same day
        self.assertEqual(SP.settings()["followers_typed"]["politics"]["tiktok"]["at"], "2031-01-01")
        SP.save_settings({"followers_typed": {"politics": {"tiktok": 4}}})
        self.assertEqual(SP.settings()["followers_typed"]["politics"]["tiktok"],
                         {"n": 4, "at": date.today().isoformat()})
        SP.save_settings({"rpm": {"youtube": 0.04}})                           # other saves keep it
        self.assertEqual(SP.settings()["followers_typed"]["politics"]["tiktok"]["n"], 4)
        SP.save_settings({"followers_typed": {"politics": {"tiktok": None}}})
        self.assertEqual(SP.settings()["followers_typed"], {})
        for bad in ({"followers_typed": {"politics": {"tiktok": -1}}},
                    {"followers_typed": {"politics": {"tiktok": True}}},
                    {"followers_typed": {"politics": {"tiktok": "12"}}},
                    {"followers_typed": {"politics": {"myspace": 3}}},
                    {"followers_typed": {"No Body!": {"tiktok": 3}}},
                    {"followers_typed": []}):
            with self.assertRaises(SystemExit):
                SP.save_settings(bad)
        s = json.loads(p.read_text())
        s["followers_typed"] = {"gaming": {"tiktok": {"n": "lots"}, "youtube": {"n": 3}}, "x": "y"}
        p.write_text(json.dumps(s))
        self.assertEqual(SP.settings()["followers_typed"], {"gaming": {"youtube": {"n": 3, "at": ""}}})

    # ------------------------------------------------------------------------------------------ the page
    def test_6_the_page_shows_this_month_and_the_gates(self):
        page = SP.page("tok", "", "")
        for words in ("Views gained this month", "This month (estimate)", "'This month'",
                      "Type the follower count from the TikTok app", "this month` : ''", "typedOut()"):
            self.assertIn(words, page)
        node = shutil.which("node")
        if node:                                    # the page's script still parses
            js = self.tmp / "stats.js"
            js.write_text(SP.JS)
            r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
