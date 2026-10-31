"""The review page's layout (2026-10-17 evening): the account switch, the month calendar, the Needs-you band, every
card carrying its account and date, a waiting platform cut said plainly with no Try again that cannot work, and
private TikTok tests kept out of Posted. Throwaway folders and fake platforms only (clipfactory/net.py)."""
from __future__ import annotations

import os as _os
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-layout-state-"))

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import direct as D     # noqa: E402
from clipfactory import outlets as O    # noqa: E402
from clipfactory import registry as REG  # noqa: E402
from clipfactory import review as R     # noqa: E402
from clipfactory import signoff as S    # noqa: E402

DAY = date(2031, 5, 6)


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class LayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-layout-"))
        cls.old_state, C.STATE = C.STATE, cls.tmp / "state"
        cls.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(cls.tmp / "drafts")
        raw["review_port"] = 0
        for a in raw["accounts"].values():
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 1, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "instagram_reels": {"mode": "api"}, "tiktok": {"mode": "export"}}
            a["links"] = {"youtube_shorts": "https://www.youtube.com/@example", "tiktok": "http://not-https.example",
                          "facebook_reels": "https://www.facebook.com/123"}
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.master = cls.tmp / "master.mp4"
        clip(cls.master, 2.0)

    @classmethod
    def tearDownClass(cls):
        C.STATE = cls.old_state
        O.TOKENS, D.TOKENS = cls.old_tokens

    def setUp(self):
        for d in ("state", "drafts", "delivered", "keys"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.gta = self.cfg.account("gaming")
        self.politics = self.cfg.account("politics")

    def ready(self, acct, *slots: str, day: date = DAY) -> None:
        root = acct.delivered_dirs(day)[0]
        for k in slots:
            d = root / f"{k.replace(':', '')} clip-{k.replace(':', '')}"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.master, d / "01-x.mp4")
            for p in ("youtube", "tiktok", "facebook", "reels"):
                (d / f"{p}.txt").write_text(f"Clip at {k} | Creator\n\nWords for {p} at {k}.\n")
        K.collect(self.cfg, acct, day)

    def at(self, hhmm: str, day: date = DAY):
        return self.cfg.slot_time(day, hhmm)

    def test_1_the_switch_shows_one_account_on_clips_live_and_the_calendar(self):
        self.ready(self.gta, "07:00")
        self.ready(self.politics, "07:00")
        now = self.at("06:00")
        both = R.live(self.cfg, DAY, now)
        owners = [f"acct-{a.slug}" for a in self.cfg.accounts.values()]     # one section per account switched on
        self.assertEqual(owners[:2], ["acct-gaming", "acct-politics"])
        self.assertEqual([s["id"] for s in both["sections"]], owners)
        one = R.live(self.cfg, DAY, now, acct="politics")
        self.assertEqual([s["id"] for s in one["sections"]], ["acct-politics"])
        self.assertEqual([a["id"] for a in one["cal"]["accounts"]], ["politics"])
        self.assertTrue(all(c["acct"] == "politics" for c in one["needs"]["cards"]))
        self.assertEqual(R.pick_acct(self.cfg, "nobody"), "all")
        page = R.page(self.cfg, DAY, now, tok="t", acct="gaming")
        self.assertIn('href="/stats?acct=gaming"', page)                       # the tab keeps the account
        self.assertIn('aria-current=true>Gaming</a>', page)
        self.assertNotIn('id="acct-politics"', page)
        # the exact tab strip (2026-10-20, every social page built): Clips, Stats, then the social tabs in the plug
        # board's order; Automations, Ideas and Week are pages under a tab, never tabs of their own
        self.assertEqual([k for k, _, _ in REG.tab_pages()], ["clips", "stats", "inbox", "cards", "reports", "log"])
        strip = ('<nav class="tabs" aria-label="Pages"><a href="/"{c}>Clips</a><a href="/calendar">Calendar</a>'
                 '<a href="/stats">Stats</a><a href="/money">Money</a>'
                 '<a href="/inbox"{i}>Inbox</a><a href="/cards">Cards</a><a href="/reports"{r}>Reports</a>'
                 '<a href="/log">Log</a></nav>')
        cur = " aria-current=page"
        self.assertEqual(R.tabs("clips"), strip.format(c=cur, i="", r=""))           # unchanged with no account
        self.assertEqual(R.tabs("week"), strip.format(c=cur, i="", r=""))            # Week sits under Clips
        self.assertEqual(R.tabs("rules"), strip.format(c="", i=cur, r=""))           # Automations under Inbox
        self.assertEqual(R.tabs("ideas"), strip.format(c="", i="", r=cur))           # Ideas under Reports
        self.assertIn('<a href="/inbox?acct=gaming">Inbox</a><a href="/cards?acct=gaming">Cards</a>'
                      '<a href="/reports?acct=gaming">Reports</a><a href="/log?acct=gaming">Log</a></nav>',
                      page.replace('<a href="/lab">Lab</a></nav>', '</nav>'))   # Lab's tab, when one runs here

    def test_2_profile_links_only_https_and_only_platforms_posted_to(self):
        sec = R._section(self.cfg, self.gta, DAY, self.at("06:00"))
        self.assertIn('href="https://www.youtube.com/@example"', sec["head"])
        self.assertIn('href="https://www.facebook.com/123"', sec["head"])
        self.assertNotIn("not-https", sec["head"])

    def test_3_the_calendar_paints_every_slot(self):
        self.ready(self.gta, "07:00", "08:45")
        S.approve(self.cfg, self.gta, DAY, ["07:00"], "Andrew", now=self.at("06:00"))
        cal = R.calendar(self.cfg, self.at("06:00"))
        kinds = cal["days"][DAY.isoformat()]["gaming"]
        self.assertEqual(len(kinds), len(self.gta.active_slots()))
        self.assertEqual(kinds[:2], ["scheduled", "review"])
        self.assertEqual(set(kinds[2:]), {"empty"})
        late = R.calendar(self.cfg, self.at("07:00", DAY + timedelta(days=1)) + timedelta(hours=8))
        self.assertEqual(late["days"][DAY.isoformat()]["gaming"][1], "problem")   # never signed in time

    def test_4_needs_you_holds_what_to_sign_and_every_card_says_its_account_and_date(self):
        self.ready(self.gta, "07:00", "08:45")
        self.ready(self.gta, "07:00", day=DAY + timedelta(days=1))
        now = self.at("06:00")
        needs = R._needs(self.cfg, now)
        self.assertEqual(sorted(c["id"] for c in needs["cards"]),
                         [f"n-gaming-{DAY:%Y%m%d}-0700", f"n-gaming-{DAY:%Y%m%d}-0845",
                          f"n-gaming-{DAY + timedelta(days=1):%Y%m%d}-0700"])
        self.assertIn('data-acct="gaming"', needs["cards"][0]["html"])
        self.assertIn(f'data-date="{DAY.isoformat()}"', needs["cards"][0]["html"])
        self.assertIn(f'data-op="approve-all" data-acct="gaming" data-date="{DAY.isoformat()}"', needs["head"])
        page = R.page(self.cfg, DAY, now, tok="t")
        self.assertEqual(page.count('id="c-gaming-0700"'), 0)            # the band holds it, the day list does not
        self.assertEqual(page.count(f'id="n-gaming-{DAY:%Y%m%d}-0700"'), 1)
        S.approve(self.cfg, self.gta, DAY, ["07:00", "08:45"], "Andrew", now=now)
        S.approve(self.cfg, self.gta, DAY + timedelta(days=1), ["07:00"], "Andrew", now=now)
        calm = R._needs(self.cfg, now)
        self.assertEqual(calm["cards"], [])
        self.assertIn("All signed. Next post 07:00, Gaming.", calm["head"])

    def test_5_a_cut_that_came_after_signing_asks_for_pull_and_never_offers_try_again(self):
        self.ready(self.politics, "07:00")
        now = self.at("06:00")
        S.approve(self.cfg, self.politics, DAY, ["07:00"], "Andrew", now=now)
        with B.edit(self.cfg, self.politics, DAY, create=False) as bd:
            s = bd["slots"]["07:00"]
            B.note_once(s, "a new Instagram cut arrived after signing; Pull and approve again to take it")
            s.setdefault("outlets", {})["instagram_reels"] = {
                "gave_up": True, "why": "no Instagram cut: the clip is 76s and Instagram takes 30-50s"}
        s = B.read(self.politics, DAY)["slots"]["07:00"]
        self.assertEqual(R._waiting_cuts(s), ["Instagram"])
        self.assertFalse(R._retryable(s))
        card = R._card(self.cfg, self.politics, DAY, s, now)
        self.assertIn("Instagram cut ready. Press Pull, watch it, then Approve", card["bottom"])
        self.assertNotIn('data-op="retry"', card["bottom"])
        self.assertIn('class="go" data-op="pull"', card["bottom"])
        self.assertEqual(R._need_why(self.cfg, self.politics, s, now), "cut")

    def test_6_a_private_tiktok_test_is_not_posted(self):
        self.ready(self.gta, "07:00")
        S.approve(self.cfg, self.gta, DAY, ["07:00"], "Andrew", now=self.at("06:00"))
        with B.edit(self.cfg, self.gta, DAY, create=False) as bd:
            bd["slots"]["07:00"].setdefault("outlets", {})["tiktok"] = {"post_id": "v1", "privacy": "SELF_ONLY"}
        s = B.read(self.gta, DAY)["slots"]["07:00"]
        rows = R._plat_rows(self.cfg, self.gta, s, self.at("07:30"))
        self.assertIn(("hand", "TikTok", "private test post (Only me): post it by hand"), [r[:3] for r in rows])

    def test_7_a_posted_platform_is_a_link_with_its_views(self):
        self.ready(self.gta, "07:00")
        S.approve(self.cfg, self.gta, DAY, ["07:00"], "Andrew", now=self.at("06:00"))
        with B.edit(self.cfg, self.gta, DAY, create=False) as bd:
            bd["slots"]["07:00"].setdefault("outlets", {})["facebook_reels"] = {
                "post_id": "f1", "url": "https://www.facebook.com/reel/1/", "metrics": {"views": 1234}}
        s = B.read(self.gta, DAY)["slots"]["07:00"]
        bottom = R._bottom(self.cfg, self.gta, DAY, s, self.at("08:00"))
        self.assertIn('<a href="https://www.facebook.com/reel/1/"', bottom)
        self.assertIn("posted · 1.2K views", bottom)
        self.assertEqual(R._views(1), "1 view")
        self.assertEqual(R._views(15400), "15K views")


if __name__ == "__main__":
    unittest.main()
