import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# THE VS CARD (clipfactory/vscard.py, 2026-10-28): a 2-second fighting-game card in front of a collected clip that is a
# fight between two named people. The asker stands left, the answerer right; the people come from the engine's plates
# file (placed plates, then anyone its slate named); the faces are the clean portraits the engine saved; it is off
# unless the account's vs_card switch is on; it goes on every file with room (the clip's picture joined by stream
# copy), keeps the delivered SHA-256 as the slot's identity, and a clip with a card sits out of the pre-hook test.
# Tiny synthetic videos and pictures in a throwaway folder; no network.

import json
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from social_fakes import cfg_in_tmp          # noqa: E402
from test_prehook import make_clip, frames   # noqa: E402

from clipfactory import board as B           # noqa: E402
from clipfactory import learn as L           # noqa: E402
from clipfactory import prehook as PH        # noqa: E402
from clipfactory import store                # noqa: E402
from clipfactory import vscard as VS         # noqa: E402

PLATES = {"cards": [{"name": "Dave Calhoun", "who": "calhoun", "role": "witness", "title": "President and CEO, Boeing"},
                    {"name": "Josh Hawley", "who": "hawley", "role": "member", "title": "U.S. Senator, Missouri"}]}


class FightersTest(unittest.TestCase):
    def test_the_asker_stands_left(self):
        pair, why = VS.fighters(PLATES)
        self.assertEqual(why, "")
        self.assertEqual([f["surname"] for f in pair], ["HAWLEY", "CALHOUN"])
        self.assertEqual(pair[0]["title"], "U.S. SENATOR, MISSOURI")

    def test_a_plate_the_render_left_out_still_fights(self):
        rec = {"cards": [PLATES["cards"][0]],
               "people": [{"who": "warren", "name": "Elizabeth Warren", "role": "member", "title": "U.S. Senator"},
                          {"who": "calhoun", "name": "Dave Calhoun", "role": "witness", "title": "CEO"}]}
        pair, _why = VS.fighters(rec)
        self.assertEqual([f["who"] for f in pair], ["warren", "calhoun"])

    def test_the_whole_last_name_and_never_jr(self):
        for name, want in (("Chris Van Hollen", "Van Hollen"), ("Robert F. Kennedy Jr.", "Kennedy"),
                           ("Kennedy, Jr.", "Kennedy"), ("Debbie Wasserman Schultz", "Wasserman Schultz"),
                           ("Van Taylor", "Taylor"), ("Hank Johnson III", "Johnson"),
                           ("Alexandria Ocasio-Cortez", "Ocasio-Cortez"), ("Elizabeth Warren", "Warren"),
                           ("Josh Hawley", "Hawley")):
            self.assertEqual(VS.surname(name), want, name)
        rec = {"cards": [{"name": "Chris Van Hollen", "who": "van hollen", "role": "senator", "title": "Senator"},
                         {"name": "Robert F. Kennedy Jr.", "who": "kennedy", "role": "witness", "title": "HHS"}]}
        pair, _why = VS.fighters(rec)
        self.assertEqual([f["surname"] for f in pair], ["VAN HOLLEN", "KENNEDY"])

    def test_one_person_is_no_fight(self):
        pair, why = VS.fighters({"cards": [PLATES["cards"][1]]})
        self.assertEqual(pair, [])
        self.assertIn("needs two", why)


WARREN = {"cards": [{"name": "Elizabeth Warren", "who": "warren", "role": "member", "title": "U.S. Senator"},
                    {"name": "Dave Calhoun", "who": "calhoun", "role": "witness", "title": "CEO"}]}


class AnnouncerNamesTest(unittest.TestCase):
    def test_first_and_last_names_when_no_take_says_the_last_names(self):
        pair, _why = VS.fighters(WARREN)
        said = {"starts": {}, "speech": 1.8, "kept": False}
        miss = (None, 'none of 4 takes said "Warren! Versus! Calhoun! FIGHT!" cleanly')
        with mock.patch.object(VS.VA, "call", side_effect=[miss, (said, "")]) as call:
            got, why = VS.announce(*pair)
        self.assertIs(got, said)
        self.assertEqual(why, "")
        self.assertEqual([c.args for c in call.call_args_list],
                         [("Warren", "Calhoun"), ("Elizabeth Warren", "Dave Calhoun")])

    def test_a_last_name_on_h_goes_straight_to_first_and_last_names(self):
        pair, _why = VS.fighters(PLATES)
        with mock.patch.object(VS.VA, "call", return_value=({"starts": {}, "speech": 1.8}, "")) as call:
            VS.announce(*pair)
        self.assertEqual([c.args for c in call.call_args_list], [("Josh Hawley", "Dave Calhoun")])
        self.assertEqual(VS.full_name("Robert F. Kennedy Jr."), "Robert Kennedy")
        self.assertEqual(VS.full_name("Chris Van Hollen"), "Chris Van Hollen")
        self.assertEqual(VS.full_name("Kennedy, Jr."), "Kennedy")

    def test_one_try_when_the_model_is_not_there(self):
        pair, _why = VS.fighters(WARREN)
        with mock.patch.object(VS.VA, "call", return_value=(None, "tests run offline: no announcer model")) as call:
            got, why = VS.announce(*pair)
        self.assertIsNone(got)
        self.assertEqual(call.call_count, 1)
        self.assertIn("offline", why)


class SlotTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.acct = self.cfg.account("politics")             # clips of 1 to 3 seconds in the test config
        self.jobs = self.tmp / "Jobs"
        for p in (mock.patch.object(VS, "JOBS", self.jobs), mock.patch.object(VS, "make", self.fake_make)):
            p.start()
            self.addCleanup(p.stop)

    CARD = 1.9                                                     # a stand-in card's length

    def fake_make(self, left, right, line, out, info=None):
        """A small stand-in for the card (the real one is drawn in CardTest)."""
        self.made = (left["surname"], right["surname"], left["img"].size, line)
        return make_clip(out, self.CARD, "red", 300, sar="1/1")

    def slot(self, secs=0.8, cut=0.0, people=True, cast_check=True):
        folder = self.tmp / "drafts" / "0750 a-fight"
        folder.mkdir(parents=True, exist_ok=True)
        clip = make_clip(folder / "01-a-fight.mp4", secs, "green", 440)
        if cast_check:
            (folder / "cast-check.json").write_text(json.dumps({"job": "A Job"}))
        clips = self.jobs / "A Job" / "clips"
        clips.mkdir(parents=True, exist_ok=True)
        rec = json.loads(json.dumps(PLATES))
        if people:
            from PIL import Image
            faces = clips / "01-a-fight.faces"
            faces.mkdir(exist_ok=True)
            rec["faces"] = {}
            for who, colour, looks in (("hawley", "navy", "left"), ("calhoun", "gray", "right")):
                Image.new("RGB", (240, 400), colour).save(faces / f"{who}.png")
                rec["faces"][who] = {"png": str(faces / f"{who}.png"), "master_at": 3.0, "looks": looks}
        (clips / "01-a-fight.cards.json").write_text(json.dumps(rec))
        (self.jobs / "A Job" / "slate.json").write_text(json.dumps(
            {"clips": [{"slug": "a-fight", "credit": "Senate, June 18 2024"}]}))
        s = {"slot": "07:50", "slug": "a-fight", "status": "ready", "notes": [], "folder": str(folder),
             "publish_at": self.cfg.slot_time(date(2031, 7, 14), "07:50").isoformat(),
             "clip": {"path": str(clip), "sha256": B.sha256_file(clip), "seconds": secs}}
        if cut:
            c = make_clip(folder / "instagram_reels.mp4", cut, "gray", 520)
            s["cuts"] = {"instagram_reels": {"path": str(c), "sha256": B.sha256_file(c), "seconds": cut}}
        return s

    def switch(self, value):
        with store.edit(store.path(self.acct.slug, "social", "settings.json"), dict) as d:
            d.setdefault("modes", {})["vs_card"] = value

    def test_off_unless_switched_on(self):
        s = self.slot()
        before = s["clip"]["sha256"]
        rec, why = VS.apply(self.cfg, self.acct, s)
        self.assertIsNone(rec)
        self.assertIn("switch is off", why)
        self.assertEqual(B.sha256_file(Path(s["clip"]["path"])), before)

    def test_a_confirmed_cast_finds_its_job_in_the_schedule(self):
        # 10-27/10-28: 30 clips, 0 cards. cast-check.json is written only while a cast waits for Andrew, so a confirmed
        # two-person fight has none; Publish/schedule.json names every slot's job
        self.switch("on")
        s = self.slot(cast_check=False)
        pub = Path(self.acct.account_dir) / "Publish"
        pub.mkdir(parents=True, exist_ok=True)
        (pub / "schedule.json").write_text(json.dumps({s["publish_at"][:10]: {"07:50": {"job": "A Job",
                                                                                         "slug": "a-fight"}}}))
        rec, why = VS.apply(self.cfg, self.acct, s)
        self.assertEqual(why, "")
        self.assertEqual((rec["left"], rec["right"]), ("Josh Hawley", "Dave Calhoun"))

    def test_no_schedule_and_no_cast_check_says_why(self):
        self.switch("on")
        rec, why = VS.apply(self.cfg, self.acct, self.slot(cast_check=False))
        self.assertIsNone(rec)
        self.assertIn("job is not known", why)

    def test_card_goes_on_and_the_clip_keeps_its_identity(self):
        self.switch("on")
        s = self.slot()
        clip = Path(s["clip"]["path"])
        delivered, own = s["clip"]["sha256"], frames(clip)
        rec, why = VS.apply(self.cfg, self.acct, s)
        self.assertEqual(why, "")
        self.assertEqual((rec["left"], rec["right"]), ("Josh Hawley", "Dave Calhoun"))
        self.assertEqual(self.made[:2], ("HAWLEY", "CALHOUN"))
        self.assertEqual(self.made[3], "POLITICS  ·  SENATE, JUNE 18 2024")
        self.assertAlmostEqual(PH._seconds(clip), 0.8 + self.CARD, delta=0.1)
        self.assertEqual(s["clip"]["delivered_sha256"], delivered)     # its identity on the board
        self.assertIn(delivered, B.clip_idents(s))
        self.assertTrue((clip.parent / VS.KEEP / clip.name).is_file())
        self.assertEqual(frames(clip)[-len(own):], own)                 # the clip's own picture, untouched
        self.assertEqual(rec["join"], ["copy"])
        self.assertIs(s["vs_card"], rec)

    def test_a_file_without_room_posts_as_delivered(self):
        self.switch("on")
        s = self.slot(secs=0.8, cut=2.9)
        cut_before = s["cuts"]["instagram_reels"]["sha256"]
        rec, _why = VS.apply(self.cfg, self.acct, s)
        self.assertEqual(rec["files"], ["clip"])
        self.assertIn("instagram_reels", rec["skipped"])
        self.assertEqual(B.sha256_file(Path(s["cuts"]["instagram_reels"]["path"])), cut_before)

    def test_no_saved_faces_no_card(self):
        self.switch("on")
        s = self.slot(people=False)
        rec, why = VS.apply(self.cfg, self.acct, s)
        self.assertIsNone(rec)
        self.assertIn("no clean face", why)
        self.assertTrue(any("no VS card" in n for n in s["notes"]))

    def test_a_card_sits_out_of_the_pre_hook_test(self):
        s = self.slot()
        s["vs_card"] = {"left": "Josh Hawley", "right": "Dave Calhoun"}
        why = L._cannot_apply(self.acct, {"kind": "pre_hook"}, {}, s)
        self.assertIn("VS card", why)


class CardTest(unittest.TestCase):
    def test_the_card_is_full_height_and_short_with_its_own_sound(self):
        from PIL import Image
        tmp = Path(tempfile.mkdtemp())
        L_, R_ = Image.new("RGB", (532, 910), "navy"), Image.new("RGB", (540, 910), "gray")
        out = VS.make({"surname": "HAWLEY", "title": "U.S. SENATOR, MISSOURI", "img": L_},
                      {"surname": "CALHOUN", "title": "PRESIDENT AND CEO, BOEING", "img": R_},
                      "POLITICS", tmp / "card.mp4")
        info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                          "stream=codec_type,width,height", "-show_entries", "format=duration",
                                          "-of", "json", str(out)], capture_output=True, text=True).stdout)
        kinds = {s["codec_type"]: s for s in info["streams"]}
        self.assertEqual((kinds["video"]["width"], kinds["video"]["height"]), (1080, 1920))
        self.assertIn("audio", kinds)
        self.assertTrue(1.5 <= float(info["format"]["duration"]) <= VS.SECONDS + 0.05, info["format"])

    def test_the_beats_follow_the_names(self):
        import numpy as np
        z = lambda s: np.zeros(int(s * VS.SR))                          # noqa: E731
        t, end = VS.plan([z(0.6), z(0.7), z(0.55), z(0.5)])
        self.assertEqual(t["merge"], VS.MERGE)                          # the grounds meet first
        self.assertEqual(t["left"], t["merge"])                         # the left card on its name
        self.assertGreaterEqual(t["vs"], t["left"] + 0.6 - 0.05)        # VS on "Versus!", after the left name
        self.assertGreaterEqual(t["right"], t["vs"] + 0.7 - 0.1)        # the right card as "Versus!" ends
        self.assertGreater(t["fight"], t["right"] + 0.55)               # FIGHT! after the right name
        self.assertLessEqual(end, VS.SECONDS)
        t2, end2 = VS.plan([None, None, None, None])                    # no voice: the card still plays
        self.assertLess(t2["left"], t2["vs"])
        self.assertLess(t2["vs"], t2["right"])
        self.assertLessEqual(end2, VS.SECONDS)

    def test_the_cover_is_the_whole_card_before_fight(self):
        """Andrew 10.30: a fight clip's cover is its VS card, whole (vscard.full_at -> covers.vs_moment)."""
        b = {"merge": 3, "left": 3, "vs": 25, "right": 44, "fight": 62}
        self.assertEqual(VS.full_at(b), round(60.5 / VS.FPS, 3))      # two frames before FIGHT!, mid-frame
        self.assertEqual(VS.full_at({**b, "fight": 52}), round(50.5 / VS.FPS, 3))    # the right card just in
        self.assertLess(VS.full_at({**b, "fight": 48}) * VS.FPS, 48)  # never FIGHT!, however tight
        for t, _ in (VS.plan([None] * 4),):                            # every planned card has such a frame
            f = {k: int(round(v * VS.FPS)) for k, v in t.items()}
            self.assertTrue(f["right"] + 6 <= VS.full_at(f) * VS.FPS < f["fight"])


if __name__ == "__main__":
    unittest.main()
