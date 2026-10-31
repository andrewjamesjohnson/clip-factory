import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The Week page (2026-10-20): a read-only week, Monday first, of every account's clips, cards and stories with the
# Clips page's own status for each, a month strip on top, days stacked with today first on a phone. Clients and
# switched off accounts are not in it. Boards are written by hand (no ffmpeg); no platform is called.

import hashlib
import json
import re
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import Clock, FakeGraph, FakeYouTube, cfg_in_tmp, patch_http, serve  # noqa: E402

from clipfactory import board as B          # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import review as R         # noqa: E402
from clipfactory import statspage as SP     # noqa: E402
from clipfactory import switches as SW      # noqa: E402
from clipfactory import weekpage as WK      # noqa: E402

DAY = date(2031, 7, 8)                       # a Tuesday
MONDAY = date(2031, 7, 7)
OUTS = ("youtube_shorts", "facebook_reels", "instagram_reels")


class WeekTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        raw = json.loads(self.cfg_path.read_text())
        client = dict(raw["accounts"]["gaming"], name="Client Co", short="Client", owner="client",
                      approvers=["Bob"], drafts_root=str(self.tmp / "client-drafts"))
        raw["accounts"]["client-co"] = client
        for slug in ("stunts", "cars"):     # on in the live file since 2026-10-29: off here, so the
            raw["accounts"][slug]["active"] = False      # week still shows a switched-off account never
        self.cfg_path.write_text(json.dumps(raw))
        self.cfg = C.load(self.cfg_path)
        self.gta = self.cfg.account("gaming")
        self.politics = self.cfg.account("politics")
        self.clock = Clock(self.cfg.slot_time(DAY, "12:00")).patch(self, self.cfg)
        self.http = patch_http(self, graph=FakeGraph(), yt=FakeYouTube())

    def tearDown(self):
        self.assertEqual((self.http.graph.calls, self.http.yt.calls), ([], []))

    def slot(self, acct, day: date, hhmm: str, status: str, posted=False, clip=True) -> dict:
        s = {"slot": hhmm, "publish_at": self.cfg.slot_time(day, hhmm).isoformat(), "status": status, "notes": [],
             "format": "clip"}
        if clip:
            folder = self.tmp / "drafts" / acct.slug / day.isoformat() / hhmm.replace(":", "")
            folder.mkdir(parents=True, exist_ok=True)
            clipf = folder / "01-clip.mp4"
            clipf.write_bytes(b"clip " + hhmm.encode())
            s["clip"] = {"path": str(clipf), "sha256": hashlib.sha256(clipf.read_bytes()).hexdigest(), "seconds": 2}
            s["slug"] = f"clip-{hhmm.replace(':', '')}"
            s["copy"] = {}
            for plat in OUTS + ("tiktok",):
                f = folder / f"{plat}.txt"
                f.write_text(f"Title at {hhmm} | Creator\n\nWords.\n")
                s["copy"][plat] = {"path": str(f), "sha256": B.sha256_text(f.read_text()), "from": "x.txt"}
        if status in B.SIGNED:
            s["signoff"] = {"by": "Andrew", "at": B.utcnow(), "hash": B.content_hash(s)}
        if posted:
            at = s["publish_at"]
            s["outlets"] = {"youtube_shorts": {"video_id": "yt1", "url": "https://www.youtube.com/shorts/yt1",
                                               "publish_at": at, "at": at},
                            "facebook_reels": {"post_id": "1", "url": "https://www.facebook.com/reel/1",
                                               "published_at": at, "provider": "direct"},
                            "instagram_reels": {"post_id": "2", "url": "https://www.instagram.com/reel/2/",
                                                "published_at": at, "provider": "direct"}}
        return s

    def board(self, acct, day: date, *slots: dict) -> None:
        p = C.STATE / acct.slug / f"{day.isoformat()}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"account": acct.slug, "date": day.isoformat(),
                                 "slots": {s["slot"]: s for s in slots}}))

    def cards(self, acct, day: date, cards: dict) -> None:
        p = C.STATE / acct.slug / "cards" / f"{day.isoformat()}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"account": acct.slug, "date": day.isoformat(), "cards": cards}))

    def fixture(self):
        g = self.gta
        self.board(g, DAY, self.slot(g, DAY, "07:00", B.SCHEDULED, posted=True),
                   self.slot(g, DAY, "15:30", B.READY),
                   self.slot(g, DAY, "13:00", B.OPEN, clip=False),
                   self.slot(g, DAY, "10:00", B.FAILED, clip=False),
                   self.slot(g, DAY, "19:00", B.SCHEDULED))
        self.board(g, DAY + timedelta(days=2), self.slot(g, DAY + timedelta(days=2), "09:00", B.READY))
        self.board(g, date(2031, 7, 2), self.slot(g, date(2031, 7, 2), "09:00", B.SCHEDULED, posted=True))
        self.board(self.politics, DAY - timedelta(days=1), self.slot(self.politics, DAY - timedelta(days=1), "08:45",
                                                                    B.SCHEDULED, posted=True))
        at = lambda hhmm: self.cfg.slot_time(DAY, hhmm).astimezone(timezone.utc).isoformat()  # noqa: E731
        self.cards(g, DAY, {"countdown-0900": {"id": "countdown-0900", "kind": "countdown", "status": "posted",
                                               "at": at("09:00"), "targets": ["facebook_story"]},
                            "week_best-1900": {"id": "week_best-1900", "kind": "week_best", "status": "ready",
                                               "at": at("19:00"), "targets": ["instagram_carousel"]},
                            "story-0800": {"id": "story-0800", "kind": "story", "status": "scheduled",
                                           "at": at("08:00"), "targets": ["facebook_story"]}})
        SW.set_mode(self.cfg, g, "cards.story_repost", "practice", "Andrew")
        clips = self.cfg.all_accounts["stunts"]                     # switched off: never on the week
        self.board(clips, DAY, self.slot(clips, DAY, "07:20", B.READY))
        client = self.cfg.all_accounts["client-co"]                      # a client's: never on the owner's week
        self.board(client, DAY, self.slot(client, DAY, "07:00", B.READY))

    def test_1_items_and_counts(self):
        self.fixture()
        w = WK.week(self.cfg, {"date": DAY.isoformat()}, self.clock())
        self.assertEqual(w["monday"], MONDAY.isoformat())
        self.assertEqual(w["days"][0], MONDAY.isoformat())
        self.assertEqual(len(w["days"]), 7)
        self.assertEqual([a["id"] for a in w["accounts"]], ["gaming", "politics"])
        gta = w["accounts"][0]
        got = [(i["time"], i["k"], i["s"]) for i in gta["days"][DAY.isoformat()]]
        self.assertEqual(got, [("07:00", "clip", "posted"), ("08:00", "story", "practice"),
                               ("09:00", "card", "posted"), ("10:00", "clip", "problem"),
                               ("13:00", "clip", "empty"), ("15:30", "clip", "needs"),
                               ("19:00", "card", "needs"), ("19:00", "clip", "scheduled")])
        first = gta["days"][DAY.isoformat()][0]
        self.assertEqual(first["href"], f"/?date={DAY}&acct=gaming#c-gaming-0700")
        self.assertEqual(first["title"], "Title at 07:00 | Creator")
        self.assertEqual(gta["days"][DAY.isoformat()][2]["href"], f"/cards?date={DAY}&acct=gaming")
        self.assertIn("Countdown story: Facebook story", [i["title"] for i in gta["days"][DAY.isoformat()]])
        self.assertEqual([i["s"] for i in gta["days"][(DAY + timedelta(days=2)).isoformat()]], ["needs"])
        self.assertEqual(gta["month"], {"posted": 2, "scheduled": 1, "review": 2, "empty": 1, "failed": 1,
                                        "cards": 1})
        politics = w["accounts"][1]
        self.assertEqual([i["s"] for i in politics["days"][(DAY - timedelta(days=1)).isoformat()]], ["posted"])
        self.assertEqual(w["prev"], (MONDAY - timedelta(days=7)).isoformat())
        one = WK.week(self.cfg, {"date": DAY.isoformat(), "acct": "politics"}, self.clock())
        self.assertEqual([a["id"] for a in one["accounts"]], ["politics"])
        nobody = WK.week(self.cfg, {"date": DAY.isoformat(), "acct": "client-co"}, self.clock())
        self.assertEqual([a["id"] for a in nobody["accounts"]], ["gaming", "politics"])  # not a pick

    def test_2_the_html_desktop_and_phone(self):
        self.fixture()
        w = WK.week(self.cfg, {"date": DAY.isoformat()}, self.clock())
        html = WK._view_html(w)
        self.assertIn('<div class="wk-grid"><table>', html)
        self.assertIn('class="wk-days"', html)
        self.assertNotIn("Stunts", html)
        self.assertNotIn("Client Co", html)
        phone = html.split('class="wk-days"', 1)[1]
        labels = re.findall(r'<section class="wk-day[^"]*" aria-label="([^"]+)"', phone)
        self.assertEqual(labels[0], "Tue Jul 8 · today")                      # today first on a phone
        self.assertEqual(labels[1], "Wed Jul 9")
        self.assertEqual(labels[-1], "Mon Jul 7")
        self.assertIn("<span>2 posted</span><span>1 scheduled</span><span>2 to sign</span><span>1 empty</span>"
                      '<span class="wk-bad">1 failed</span><span>1 card posted</span>', html)
        for chip in ("Posted", "Scheduled", "Needs you", "Problem", "Empty", "Practice"):
            self.assertIn(f">{chip}</span>", html)
        self.assertIn(f'href="/week?date={(MONDAY - timedelta(days=7)).isoformat()}"', html)
        self.assertIn('href="/week?date=2031-07-08"', html)                  # This week

    def test_3_served(self):
        self.fixture()
        get, _ = serve(self, self.cfg)
        code, headers, body = get(f"/week?date={DAY}")
        self.assertEqual(code, 200)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        html = body.decode()
        self.assertIn('<body class="sx sx-week">', html)
        self.assertIn('<a href="/" aria-current=page>Clips</a>', html)        # Week sits under Clips
        self.assertIn("<title>Clip Review · Week</title>", html)
        code, _, body = get(f"/week/data?date={DAY}&acct=gaming")
        d = json.loads(body)
        self.assertEqual((code, [a["id"] for a in d["accounts"]], list(d)[-1]), (200, ["gaming"], "stamp"))
        live = json.loads(get(f"/live?date={DAY}")[2])
        self.assertTrue(live["cal"]["week"])                                  # the calendar's Week link
        code, _, _ = get("/week", headers={"Host": "evil.example"})
        self.assertEqual(code, 403)

    def test_4_phone_css(self):
        html = WK.page(self.cfg, "t0k", R.CSS + SP.CSS, R.tabs("week"), R.switch(self.cfg, "week", "all"), "all",
                       {"date": DAY.isoformat()})
        self.assertIn("<meta name=viewport content='width=device-width,initial-scale=1'>", html)
        phone = WK.CSS.split("@media (max-width:600px){", 1)[1]
        self.assertIn(".wk-step{height:40px;min-width:40px}", phone)
        self.assertIn(".wk-it a{min-height:40px}", phone)
        self.assertIn(".wk-grid{display:none}", WK.CSS.split("@media (max-width:900px){", 1)[1])
        for w in re.findall(r"(?<![-\w])(?:min-)?width:(\d+)px", WK.CSS):
            self.assertLessEqual(int(w), 248)
        self.assertNotRegex(WK.CSS, r"gradient|box-shadow|glow")
        self.assertNotIn("—", html)
        self.assertNotIn("–", html)
        self.assertIn('href="/week?acct=gaming"', html)                    # the switch keeps the Week page


if __name__ == "__main__":
    unittest.main()
