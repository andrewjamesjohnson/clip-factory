"""Approve -> it goes out by itself: the review page, Pull, and the poster's YouTube, spacing and guards.
Throwaway folders and fake platforms only; nothing can reach the network (clipfactory/net.py)."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from politics_slots import pin_gta  # noqa: E402  the GTA days these tests were written on
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import direct as D     # noqa: E402
from clipfactory import outlets as O    # noqa: E402
from clipfactory import poster as P     # noqa: E402
from clipfactory import review as R     # noqa: E402
from clipfactory import signoff as S    # noqa: E402

DAY = date(2031, 3, 4)


def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class ReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-review-"))
        cls.old_state, C.STATE = C.STATE, cls.tmp / "state"
        cls.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        raw = pin_gta(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw.pop("auto_sign", None)  # the automatic sign-off (autosign.py) is tests/test_autosign.py's; these are the Approve-only words
        raw["drafts_root"] = str(cls.tmp / "drafts")
        raw["review_port"] = 0
        for a in raw["accounts"].values():
            a.pop("volume", None)       # the plain slots these words count (the live dated rules are 2026-10-29's)
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 1, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "instagram_reels": {"mode": "api"}, "tiktok": {"mode": "export"}}
        raw["accounts"]["gaming"]["daily_target"] = len(raw["accounts"]["gaming"]["slots"])   # its ten
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.master = cls.tmp / "master.mp4"
        clip(cls.master, 2.0)

    @classmethod
    def tearDownClass(cls):
        C.STATE = cls.old_state
        O.TOKENS, D.TOKENS = cls.old_tokens

    def setUp(self):
        shutil.rmtree(C.STATE, ignore_errors=True)
        shutil.rmtree(self.tmp / "drafts", ignore_errors=True)
        shutil.rmtree(self.tmp / "delivered", ignore_errors=True)
        shutil.rmtree(O.TOKENS, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.acct = self.cfg.account("gaming")
        self.calls = []

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

    def connect_youtube(self) -> None:
        tf = O.yt_token_file(self.acct)
        tf.parent.mkdir(parents=True, exist_ok=True)
        tf.write_text("{}")

    def posters(self, fail=()):
        def make(plat):
            def post(acct, path, words, slot):
                self.calls.append((plat, slot["slot"]))
                if plat in fail:
                    raise RuntimeError(f"{plat} said no")
                return {"post_id": f"{plat}-{slot['slot']}", "url": f"https://x/{plat}/{slot['slot']}"}
            return post
        return {p: make(p) for p in ("facebook_reels", "instagram_reels")}

    def uploader(self, err: str = ""):
        def up(acct, slot):
            self.calls.append(("youtube_shorts", slot["slot"]))
            if err:
                raise RuntimeError(err)
            return "vid" + slot["slot"].replace(":", "")
        return up

    def slot(self, k: str, day: date = DAY) -> dict:
        return B.read(self.acct, day)["slots"][k]

    # ---------------------------------------------------------------------------------------------- the flow
    def test_1_approve_goes_out_by_itself(self):
        self.ready("07:00", "08:45")
        self.connect_youtube()
        out = S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew", now=self.cfg.slot_time(DAY, "05:30"))
        self.assertIn("goes out at 07:00", out[0])
        self.assertEqual(self.slot("07:00")["status"], B.SCHEDULED)
        # before its time: YouTube uploads ahead (public at the slot), Facebook and Instagram wait
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "05:31"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.calls, [("youtube_shorts", "07:00")])
        yt = self.slot("07:00")["outlets"]["youtube_shorts"]
        self.assertEqual((yt["video_id"], yt["publish_at"]), ("vid0700", self.slot("07:00")["publish_at"]))
        # at its time: Facebook and Instagram post, once
        for t in ("07:01", "07:11", "07:21"):
            P.post_due(self.cfg, now=self.cfg.slot_time(DAY, t), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(sorted(self.calls), sorted([("youtube_shorts", "07:00"), ("facebook_reels", "07:00"),
                                                    ("instagram_reels", "07:00")]))
        self.assertEqual(self.slot("08:45")["status"], B.READY)            # never signed: never posted

    def test_2_a_slot_signed_before_approve_scheduled_is_handed_off(self):
        self.ready("07:00")
        with B.edit(self.cfg, self.acct, DAY) as bd:                      # what 10-17's approvals left behind
            s = bd["slots"]["07:00"]
            s["signoff"] = {"by": "Andrew", "at": B.utcnow(), "hash": B.content_hash(s)}
            s["status"] = B.APPROVED
        out = P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:20"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.slot("07:00")["status"], B.SCHEDULED, out)
        self.assertIn(("facebook_reels", "07:00"), self.calls)            # 20 minutes late: goes out at once

    def test_3_late_slots_go_out_one_per_platform_every_ten_minutes(self):
        self.ready("07:00", "08:45", "10:30")
        S.approve(self.cfg, self.acct, DAY, None, "Andrew")
        now = self.cfg.slot_time(DAY, "11:00")
        P.post_due(self.cfg, now=now, posters=self.posters(), uploader=self.uploader())
        self.assertEqual([c for c in self.calls if c[0] == "facebook_reels"], [("facebook_reels", "07:00")])
        P.post_due(self.cfg, now=now + timedelta(minutes=5), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(len([c for c in self.calls if c[0] == "facebook_reels"]), 1)     # not yet: 10-minute gap
        P.post_due(self.cfg, now=now + timedelta(minutes=10), posters=self.posters(), uploader=self.uploader())
        self.assertEqual([c[1] for c in self.calls if c[0] == "facebook_reels"], ["07:00", "08:45"])

    def test_4_pull_stops_it_and_says_what_is_already_out(self):
        self.ready("07:00")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "05:30"), posters=self.posters(), uploader=self.uploader())
        out = S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertIn("studio.youtube.com/video/vid0700", out[0])
        self.assertEqual((self.slot("07:00")["status"], self.slot("07:00").get("signoff")), (B.READY, None))
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:05"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.calls, [("youtube_shorts", "07:00")])       # Facebook and Instagram never went
        self.assertIn("already went out", S.decline(self.cfg, self.acct, DAY, ["07:00"], "Andrew", remake=True)[0])

    def test_4b_words_edited_before_signing_are_signed_as_shown(self):
        self.ready("07:00", "08:45")
        p = Path(self.slot("07:00")["copy"]["facebook_reels"]["path"])
        p.write_text("Better words, written after collecting.\n")              # what the 04:30 re-pack did on 10-17
        key = B.seen_key(self.slot("07:00"))
        out = S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew", seen={"07:00": key})
        self.assertIn("goes out", out[0])
        self.assertEqual(B.verify(self.slot("07:00")), [])                   # the signature covers the new words
        self.assertIn("words edited after collecting", " ".join(self.slot("07:00")["notes"]))
        p.write_text("changed after signing\n")                               # but an edit after signing voids it
        self.assertTrue(B.verify(self.slot("07:00")))
        os_key = B.seen_key(self.slot("08:45"))
        Path(self.slot("08:45")["copy"]["tiktok"]["path"]).write_text("edited while the page was open\n")
        out = S.approve(self.cfg, self.acct, DAY, ["08:45"], "Andrew", seen={"08:45": os_key})
        self.assertIn("NOT signed", out[0])
        self.assertEqual(self.slot("08:45")["status"], B.READY)

    def test_4c_pull_then_approve_again_retries_what_gave_up(self):
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        for t in ("07:01", "07:11", "07:21"):
            P.post_due(self.cfg, now=self.cfg.slot_time(DAY, t), posters=self.posters(fail=("instagram_reels",)),
                       uploader=self.uploader())
        self.assertTrue(self.slot("07:00")["outlets"]["instagram_reels"]["gave_up"])
        S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertNotIn("instagram_reels", self.slot("07:00")["outlets"])   # a fresh start for what never went
        self.assertIn("facebook_reels", self.slot("07:00")["outlets"])       # what went out stays on record
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:40"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual([c for c in self.calls if c == ("facebook_reels", "07:00")], [("facebook_reels", "07:00")])
        self.assertEqual(self.slot("07:00")["outlets"]["instagram_reels"]["post_id"], "instagram_reels-07:00")

    def test_4d_a_meta_block_pauses_facebook_and_instagram_without_using_tries(self):
        self.ready("07:00", "08:45")
        S.approve(self.cfg, self.acct, DAY, None, "Andrew")
        blocked = {p: (lambda *a, p=p: self.calls.append((p, "x")) or (_ for _ in ()).throw(
            D.PlatformError("HTTP 400 from graph.facebook.com: API access blocked."))) for p in ("facebook_reels", "instagram_reels")}
        t0 = self.cfg.slot_time(DAY, "09:00")
        P.post_due(self.cfg, now=t0, posters=blocked, uploader=self.uploader())
        self.assertEqual(len(self.calls), 1)                                 # one refusal pauses the whole account
        self.assertIn("developers.facebook.com", O.meta_hold(self.acct))
        fb = self.slot("07:00")["outlets"]["facebook_reels"]
        self.assertNotIn("tries", fb)                                         # not one of the three tries
        sec = R._section(self.cfg, self.acct, DAY, t0)
        self.assertIn("Meta has paused", next(c["bottom"] for c in sec["cards"] if c["id"] == "c-gaming-0700"))
        O.meta_clear(self.acct)                                               # an hour later Meta lets it through
        P.post_due(self.cfg, now=t0 + timedelta(minutes=61), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.slot("07:00")["outlets"]["facebook_reels"]["post_id"], "facebook_reels-07:00")
        self.assertEqual(O.meta_hold(self.acct), "")

    def test_4e_try_again_runs_only_what_gave_up(self):
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        for t in ("07:01", "07:11", "07:21"):
            P.post_due(self.cfg, now=self.cfg.slot_time(DAY, t), posters=self.posters(fail=("instagram_reels",)),
                       uploader=self.uploader())
        card = R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "07:30"))["cards"][0]
        self.assertIn('data-op="retry"', card["bottom"])
        out = S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertIn("Instagram will be tried again", out[0])
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:40"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.slot("07:00")["outlets"]["instagram_reels"]["post_id"], "instagram_reels-07:00")
        self.assertEqual([c for c in self.calls if c[0] == "facebook_reels"], [("facebook_reels", "07:00")])  # once

    def test_5_youtube_cap_waits_a_day_without_using_a_try(self):
        self.ready("07:00", "08:45")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, None, "Andrew")
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "05:30"), posters=self.posters(),
                   uploader=self.uploader("HTTP 400: uploadLimitExceeded"))
        self.assertEqual(len(self.calls), 1)                              # the cap stops every further upload
        self.assertNotIn("tries", self.slot("07:00")["outlets"]["youtube_shorts"])
        self.assertIn("paused", O.yt_hold(self.acct))

    def test_6_one_broken_slot_never_stops_the_others(self):
        self.ready("07:00", "08:45")
        S.approve(self.cfg, self.acct, DAY, None, "Andrew")
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"]["publish_at"] = "not a time"            # anything at all going wrong in one slot
        out = P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "09:00"), posters=self.posters(), uploader=self.uploader())
        self.assertIn(("facebook_reels", "08:45"), self.calls, out)

    def test_7_no_words_for_a_platform_is_said_not_crashed(self):
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, None, "Andrew")
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"]["copy"].pop("instagram_reels")
            s = bd["slots"]["07:00"]
            s["signoff"]["hash"] = B.content_hash(s)
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:05"), posters=self.posters(), uploader=self.uploader())
        ig = self.slot("07:00")["outlets"]["instagram_reels"]
        self.assertEqual((ig["gave_up"], ig["why"]), (True, "no words for this platform"))
        self.assertIn(("facebook_reels", "07:00"), self.calls)

    def test_8_a_dry_run_writes_nothing(self):
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, None, "Andrew")
        before = B.board_path(self.acct, DAY).read_text()
        out = P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:05") + timedelta(days=1), dry_run=True,
                         posters=self.posters(), uploader=self.uploader())
        self.assertTrue(out)
        self.assertEqual(B.board_path(self.acct, DAY).read_text(), before)
        self.assertEqual(self.calls, [])

    def test_9_boards_are_only_dated_files(self):
        self.ready("07:00")
        O.yt_count(self.acct)                                             # youtube-uploads.json beside the boards
        self.assertEqual([p.stem for p in B.all_boards(self.acct)], [DAY.isoformat()])

    # ---------------------------------------------------------------------------------------------- the page
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

    def test_10_the_page_signs_and_updates_without_a_reload(self):
        self.ready("07:00", "08:45")
        get, post = self.serve()
        code, _, html = get(f"/?date={DAY}")
        self.assertEqual(code, 200)
        page = html.decode()
        self.assertIn("Clip at 07:00 | Creator", page)                    # the clip's own title, not its slug
        self.assertIn('data-op="approve"', page)
        self.assertNotIn("youtube-uploads", page)
        seen = {"07:00": B.seen_key(self.slot("07:00"))}
        ask = {"op": "approve", "account": "gaming", "date": str(DAY), "slots": ["07:00"], "seen": seen}
        self.assertEqual(post(ask, "stale")[0], 403)
        code, res = post(dict(ask, seen={"07:00": "not-what-it-is"}), R.token())
        self.assertEqual((code, res["ok"]), (200, False))                  # the page showed something else
        self.assertIn("look at it again", res["message"])
        code, res = post(ask, R.token())
        self.assertEqual((code, res["ok"]), (200, True), res)
        self.assertIn("post by themselves", res["message"])
        code, _, live = get(f"/live?date={DAY}")
        d = json.loads(live)
        card = next(c for s in d["sections"] for c in s["cards"] if c["id"] == "c-gaming-0700")
        self.assertIn("Scheduled", card["top"])
        self.assertIn('data-op="pull"', card["bottom"])
        self.assertIn("by hand", card["bottom"])                           # TikTok: Ready to post folder
        code, res = post({"op": "approve", "account": "nobody", "date": str(DAY), "slots": ["07:00"], "seen": {}}, R.token())
        self.assertEqual((code, res["ok"]), (400, False))
        self.assertEqual(get("/", {"Host": "evil.example"})[0], 403)

    def test_11_videos_stream_in_ranges(self):
        self.ready("07:00")
        get, _ = self.serve()
        path = self.slot("07:00")["clip"]["path"]
        size = Path(path).stat().st_size
        q = "/file?p=" + urllib.request.quote(path) + "&v=1"
        code, h, body = get(q, {"Range": "bytes=0-99"})
        self.assertEqual((code, len(body), h["Content-Range"]), (206, 100, f"bytes 0-99/{size}"))
        self.assertEqual(body, Path(path).read_bytes()[:100])
        code, h, body = get(q, {"Range": "bytes=-10"})
        self.assertEqual((code, body), (206, Path(path).read_bytes()[-10:]))
        self.assertEqual(get(q, {"Range": f"bytes={size + 5}-"})[0], 416)
        self.assertEqual(get("/file?p=" + urllib.request.quote(str(self.cfg_path)))[0], 404)   # outside the drafts

    def test_12_page_words_follow_the_clock(self):
        self.ready("07:00", "08:45")
        now = self.cfg.slot_time(DAY, "08:00")
        sec = R._section(self.cfg, self.acct, DAY, now)
        tops = {c["id"]: c["top"] for c in sec["cards"]}
        self.assertIn("goes out as soon as you approve", tops["c-gaming-0700"])   # its time passed
        self.assertIn("posts in 45m", tops["c-gaming-0845"])
        self.assertIn("8 no clip", sec["head"])
        self.assertIn("Approve all 2", sec["head"])
        late = R._section(self.cfg, self.acct, DAY, now + timedelta(days=1))
        self.assertIn("too late to post", next(c["top"] for c in late["cards"] if c["id"] == "c-gaming-0700"))
        self.assertNotIn("Approve all", late["head"])

    def test_13_one_broken_slot_never_takes_the_page_down(self):
        self.ready("07:00", "08:45")
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"]["publish_at"] = "not a time"
        page = R.page(self.cfg, DAY, now=self.cfg.slot_time(DAY, "06:00"), tok="t")
        self.assertIn("Cannot show", page)
        self.assertIn("Clip at 08:45 | Creator", page)                     # the rest of the day is still there


if __name__ == "__main__":
    unittest.main()
