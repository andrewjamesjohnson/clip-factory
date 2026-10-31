"""The review page's Stats tab: the numbers it shows, the factory's own counts, the settings, Update now, one read at a
time. Throwaway folders and fake numbers only; nothing can reach the network (clipfactory/net.py)."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B          # noqa: E402
from clipfactory import collect as K        # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import poster as P         # noqa: E402
from clipfactory import review as R         # noqa: E402
from clipfactory import stats as ST         # noqa: E402
from clipfactory import statspage as SP     # noqa: E402

DAY = date(2031, 3, 4)


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


def metrics(views: int) -> dict:
    return {"views": views, "likes": views // 10, "comments": 1, "shares": 0, "at": B.utcnow()}


class StatsPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-stats-"))
        cls.old = (C.STATE, ST.OUT, O.TOKENS, D.TOKENS)
        C.STATE = cls.tmp / "state"
        ST.OUT = C.STATE / "stats"          # stats.py fixes its folder at import: point it at the throwaway too
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(cls.tmp / "drafts")
        raw["review_port"] = 0
        for slug, a in raw["accounts"].items():
            if slug not in ("gaming", "politics"):   # the two this file counts (Stunts and Gaming
                a["active"] = False                           # Garage are on in the live file since 2026-10-29)
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 1, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "instagram_reels": {"mode": "api"}, "tiktok": {"mode": "export"}}
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.master = cls.tmp / "master.mp4"
        clip(cls.master, 2.0)

    @classmethod
    def tearDownClass(cls):
        C.STATE, ST.OUT, O.TOKENS, D.TOKENS = cls.old
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        for d in ("state", "drafts", "delivered", "keys"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.acct = self.cfg.account("gaming")
        SP._cache["key"] = None
        SP._run.update(running=False, finished=0.0, error="", failed=[])

    # ---------------------------------------------------------------------------------------------- helpers
    def ready(self, *slots: str, day: date = DAY) -> None:
        root = self.acct.delivered_dirs(day)[0]
        for k in slots:
            d = root / f"{k.replace(':', '')} clip-{k.replace(':', '')}"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.master, d / "01-x.mp4")
            for p in ("youtube", "tiktok", "facebook", "reels"):
                (d / f"{p}.txt").write_text(f"Clip at {k} | Creator\n\nWords for {p} at {k}.\n")
        K.collect(self.cfg, self.acct, day)

    def posted(self) -> None:
        """07:00 out on YouTube (100 views) and Facebook (40); 08:45 on YouTube (10), Instagram gave up; 10:30 is
        uploaded to YouTube but goes public at 10:30; one older video on the channel matches no clip (7)."""
        self.ready("07:00", "08:45", "10:30")
        with B.edit(self.cfg, self.acct, DAY) as bd:
            sl = bd["slots"]
            for k in ("07:00", "08:45", "10:30"):
                sl[k]["status"] = B.SCHEDULED
            sl["07:00"]["outlets"] = {
                "youtube_shorts": {"video_id": "v1", "url": "https://www.youtube.com/shorts/v1", "metrics": metrics(100)},
                "facebook_reels": {"post_id": "f1", "url": "https://www.facebook.com/reel/f1", "metrics": metrics(40)}}
            sl["08:45"]["outlets"] = {"youtube_shorts": {"video_id": "v2", "metrics": metrics(10)},
                                      "instagram_reels": {"gave_up": True, "why": "said no"}}
            sl["10:30"]["outlets"] = {"youtube_shorts": {"video_id": "v3"}}
        pulled = ST.OUT / "pulled"
        pulled.mkdir(parents=True, exist_ok=True)
        (pulled / "gaming__youtube_shorts.json").write_text(json.dumps({"at": "2031-03-04T12:00:00+00:00", "videos": [
            {"platform_id": "old1", "url": "https://www.youtube.com/shorts/old1", "text": "An older video",
             "published_at": "2031-03-01T18:00:00+00:00", "dur": 41, "views": 7, "likes": 1, "comments": 0, "shares": 0}]}))

    def serve(self):
        srv = R.Server(("127.0.0.1", 0), R.make_handler(self.cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        hosts = {"Host": f"127.0.0.1:{self.cfg.review_port}"}           # the handler checks the configured port
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # past the test run's dead proxy

        def get(path, headers=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={**hosts, **(headers or {})})
            try:
                with direct.open(req, timeout=10) as r:
                    return r.status, dict(r.headers), r.read()
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers), e.read()

        def post(body, tok):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/act", data=json.dumps(body).encode(), method="POST",
                                         headers={**hosts, "Content-Type": "application/json", "X-Token": tok})
            try:
                with direct.open(req, timeout=30) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        return get, post

    # ---------------------------------------------------------------------------------------------- the numbers
    def test_1_the_numbers_come_from_the_boards_and_the_videos_that_match_no_clip(self):
        self.posted()
        d = SP.data(self.cfg, now=self.cfg.slot_time(DAY, "09:00"))
        self.assertEqual(sorted((p["platform"], p["views"]) for p in d["posts"]),
                         [("facebook", 40), ("youtube", 7), ("youtube", 10), ("youtube", 100)])   # 10:30 is not out yet
        p = next(p for p in d["posts"] if p["views"] == 100)
        self.assertEqual((p["day"], p["hm"], p["hour"], p["dow"]), (str(DAY), "07:00", 7, DAY.weekday()))
        self.assertEqual((p["title"], p["url"]), ("Clip at 07:00 | Creator", "https://www.youtube.com/shorts/v1"))
        self.assertEqual((d["today"], d["slots"]), (str(DAY), sorted(set(self.acct.slots))))
        self.assertEqual(d["by_hand"], ["tiktok"])
        f = next(r for r in d["factory"] if r["account"] == "gaming" and r["day"] == str(DAY))
        self.assertEqual((f["slots"], f["made"], f["approved"]), (len(B.read(self.acct, DAY)["slots"]), 3, 3))
        self.assertEqual((f["posted"], f["gave_up"]), ({"youtube": 2, "facebook": 1}, {"instagram": 1}))
        later = SP.data(self.cfg, now=self.cfg.slot_time(DAY, "11:00"))
        self.assertEqual(len(later["posts"]), 5)                                 # 10:30 went public
        self.assertEqual(next(r for r in later["factory"] if r["account"] == "gaming")["posted"]["youtube"], 3)
        self.assertNotEqual(d["stamp"], later["stamp"])

    def test_2_a_clip_nobody_signed_in_time_is_counted(self):
        self.ready("07:00")
        late = self.cfg.slot_time(DAY, "07:00") + P.LATE_LIMIT + timedelta(minutes=5)
        f = next(r for r in SP.data(self.cfg, now=late)["factory"] if r["account"] == "gaming")
        self.assertEqual((f["made"], f["approved"], f["unsigned"]), (1, 0, 1))

    def test_3_a_broken_pulled_file_or_an_unreadable_words_file_is_no_crash(self):
        self.posted()
        (ST.OUT / "pulled" / "gaming__facebook_reels.json").write_text('{"at": "2031-03-04T12:00:00+00:00", "vid')
        words = Path(B.read(self.acct, DAY)["slots"]["07:00"]["copy"]["youtube_shorts"]["path"])
        words.chmod(0)                                                       # as macOS refuses a file it may not read
        self.addCleanup(words.chmod, 0o644)
        d = SP.data(self.cfg, now=self.cfg.slot_time(DAY, "09:00"))
        self.assertEqual(len(d["posts"]), 4)
        self.assertEqual(next(p for p in d["posts"] if p["views"] == 100)["title"], "clip-0700")    # its own name

    # ---------------------------------------------------------------------------------------------- the page
    def test_4_the_tab_and_its_data_are_served(self):
        self.posted()
        get, _ = self.serve()
        code, h, body = get("/stats")
        page = body.decode()
        self.assertEqual(code, 200)
        self.assertIn('<a href="/">Clips</a><a href="/calendar">Calendar</a><a href="/stats" aria-current=page>Stats</a>', page)
        self.assertIn("Update now", page)
        self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])
        with mock.patch.object(C.Config, "now", lambda c: c.slot_time(DAY, "09:00")):
            code, _, raw = get("/stats/data")
        d = json.loads(raw)
        self.assertEqual((code, len(d["posts"])), (200, 4))
        self.assertIn('<a href="/" aria-current=page>Clips</a><a href="/calendar">Calendar</a><a href="/stats">Stats</a>',
                      get(f"/?date={DAY}")[2].decode())
        self.assertEqual(get("/stats/data", {"Host": "evil.example"})[0], 403)

    def test_5_settings_need_the_token_and_sane_numbers(self):
        _, post = self.serve()
        ask = {"op": "stats-settings", "rpm": {"youtube": 0.07}, "goal": 2500}
        self.assertEqual(post(ask, "stale")[0], 403)
        code, res = post(ask, R.token())
        self.assertEqual((code, res["ok"]), (200, True), res)
        s = SP.settings()
        self.assertEqual((s["rpm"]["youtube"], s["rpm"]["facebook"], s["goal"]),
                         (0.07, SP.DEFAULT_SETTINGS["rpm"]["facebook"], 2500.0))       # the rest stay the defaults
        for bad in ({"rpm": {"youtube": -1}}, {"rpm": {"snapchat": 1}}, {"goal": "lots"}, {"rpm": {"youtube": True}},
                    {"rpm": []}):
            code, res = post(dict(bad, op="stats-settings"), R.token())
            self.assertEqual((code, res["ok"]), (400, False), bad)
        self.assertEqual(SP.settings()["rpm"]["youtube"], 0.07)                 # a refused save changes nothing

    def test_6_update_now_reads_in_the_background_and_never_twice_at_once(self):
        self.posted()
        _, post = self.serve()
        code, res = post({"op": "stats-update"}, R.token())
        self.assertEqual((code, res["ok"]), (200, True), res)
        self.assertIn("Reading the numbers", res["message"])
        for _ in range(400):
            if not SP._run["running"]:
                break
            time.sleep(0.05)
        self.assertFalse(SP._run["running"])
        self.assertEqual((SP._run["error"], SP._run["failed"]), ("", []))       # nothing connected: said, not failed
        self.assertTrue((ST.OUT / "posts.json").is_file())                      # and the dashboard files were written
        self.assertIn("under two minutes ago", post({"op": "stats-update"}, R.token())[1]["message"])
        with ST.reading() as got:                                               # the 03:30 run is reading
            self.assertTrue(got)
            self.assertIn("another read", ST.update(self.cfg, wait=False)[0])

    def test_7_a_platform_that_could_not_be_read_is_named_until_a_later_read_works(self):
        lines = ["  Gaming youtube_shorts: FAILED to read numbers: HTTP 403 quotaExceeded",
                 "  Gaming tiktok: not connected (run: factory.py connect tiktok gaming)"]
        SP._run.update(finished=time.time(), failed=SP._failures(self.cfg, lines))
        now = self.cfg.slot_time(DAY, "09:00")
        self.assertEqual([(f["account"], f["platform"], f["why"]) for f in SP.data(self.cfg, now=now)["failed"]],
                         [("Gaming", "YouTube", "HTTP 403 quotaExceeded")])     # not connected is not a failure
        pulled = ST.OUT / "pulled"
        pulled.mkdir(parents=True, exist_ok=True)
        later = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        (pulled / "gaming__youtube_shorts.json").write_text(json.dumps({"at": later, "videos": []}))
        self.assertEqual(SP.data(self.cfg, now=now)["failed"], [])              # the 03:30 run read it since


if __name__ == "__main__":
    unittest.main()
