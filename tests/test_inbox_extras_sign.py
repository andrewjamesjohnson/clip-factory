import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The drop folder signs only what the person dropped (review fix, 2026-10-20). collect._take runs social.after_take,
# which can add a default first comment, an experiment's arm or a trial reel to the clip. With "inbox_auto_sign":
# true the drop used to sign those too, unseen; now a dropped clip that carries any of them waits for Approve in
# Clip Review (where the page shows them), and a drop with every social part off still signs as before.
import json
import shutil
import sys
import time
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from clipfactory import board as B             # noqa: E402
from clipfactory import config as C            # noqa: E402
from clipfactory import extras as EX           # noqa: E402
from clipfactory import inbox as I             # noqa: E402
import social_fakes as F                       # noqa: E402


class DropSignsOnlyWhatWasDropped(unittest.TestCase):
    def setup_drop(self, modes: dict, experiment: bool = False, failed_plan: bool = False):
        F.cfg_in_tmp(self)
        raw = json.loads(self.cfg_path.read_text())
        raw["inbox_auto_sign"] = True
        raw["inbox_root"] = str(self.tmp / "drop")
        self.cfg_path.write_text(json.dumps(raw))
        cfg = C.load(self.cfg_path)
        a = cfg.account("gaming")
        if modes:
            sd = C.STATE / a.slug / "social"
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "settings.json").write_text(json.dumps({"v": 1, "modes": modes}))
        if experiment:
            ld = C.STATE / a.slug / "learn"
            ld.mkdir(parents=True, exist_ok=True)
            (ld / "experiments.json").write_text(json.dumps({"e-0001": {
                "kind": "trial_reel", "state": "running", "signed": {"by": "Andrew", "at": "2031-01-01T00:00:00+00:00"},
                "assign": "alternate", "arms": {"A": "normal", "B": "trial"}, "outlets": ["instagram_reels"],
                "posts": {"A": [{"board": "2031-01-01", "slot": "07:00"}], "B": []}}}))
        now = cfg.now()
        if failed_plan:                  # a FAILED slot still carrying the plan's creator and source
            day, key = I._next_slot(cfg, a, now)
            with B.edit(cfg, a, day, create=False) as bd:
                s = bd["slots"][key]
                s["status"] = B.FAILED
                s.update(host="SomeOtherStreamer", source_url="https://www.youtube.com/watch?v=PLANSOURCE", job="j-plan")
        I.ensure(cfg)
        folder = cfg.inbox_root / a.name
        v = folder / "my drop.mp4"
        shutil.copy(F._master(self.tmp), v)
        (folder / "my drop.txt").write_text("My title\n\nMy caption\n")
        old = time.time() - 3600
        os.utime(v, (old, old))
        lines = I.take(cfg, now)
        hit = None
        for d in range(3):
            bd = B.read(a, (now + timedelta(days=d)).date()) or {}
            for s in (bd.get("slots") or {}).values():
                if s.get("source") == "drop folder":
                    hit = s
        self.assertIsNotNone(hit, "\n".join(lines))
        return a, hit, lines, folder

    def test_a_trial_reel_arm_is_never_signed_by_the_drop(self):
        a, hit, lines, folder = self.setup_drop({"learn.experiments": "on", "trial_reels": "on"}, experiment=True)
        self.assertIn("trial", EX.read(hit), "the learner gave the drop no arm: the test proves nothing")
        self.assertEqual(hit["status"], B.READY)
        self.assertNotIn("signoff", hit)
        self.assertEqual(EX.ig_form(hit, a), {}, "an unsigned slot must not post as a trial reel")
        self.assertIn("NOT signed by the drop", json.dumps(hit))      # the board says why it waits
        self.assertTrue(any("ready to approve" in ln and "a trial reel" in ln for ln in lines), lines)
        self.assertFalse(any("goes out" in ln for ln in lines), lines)
        self.assertEqual(len(list((folder / "Taken").glob("*.mp4"))), 1)    # still taken: it is in Clip Review

    def test_a_default_first_comment_is_never_signed_by_the_drop(self):
        a, hit, lines, _ = self.setup_drop({"first_comment.facebook_reels": "on",
                                            "first_comment.youtube_shorts": "on"}, failed_plan=True)
        self.assertTrue(EX.read(hit).get("first_comment"), "no default first comment: the test proves nothing")
        self.assertEqual(hit["status"], B.READY)
        self.assertNotIn("signoff", hit)
        self.assertTrue(any("a first comment" in ln for ln in lines), lines)

    def test_a_drop_with_every_social_part_off_still_signs(self):
        a, hit, lines, _ = self.setup_drop({})
        self.assertFalse((hit.get("extras") or {}).get("path"))
        self.assertIn(hit["status"], B.SIGNED)
        self.assertEqual(hit["signoff"]["hash"], B.content_hash(hit))
        self.assertTrue(any("goes out" in ln for ln in lines), lines)


if __name__ == "__main__":
    unittest.main()
