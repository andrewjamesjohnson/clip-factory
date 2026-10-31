import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# THE PUNCH-CUT TEST (2026-10-28, Andrew: "Put punch-ins on half of the one-camera clips for a week and compare the
# numbers before keeping them"). The engine cuts the punch-ins at render and draws the arm there, writing it in the
# clip's plates file ("punch_cuts": eligible, arm); the factory's experiment (kind punch_cut, assign "given") takes
# that arm as it is, never one of its own, and a clip the engine left out of the test is practice in it (not
# counted). Synthetic slots and plates files in a throwaway folder; no network, no video.

import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from social_fakes import cfg_in_tmp          # noqa: E402

from clipfactory import learn as L           # noqa: E402
from clipfactory import social as SOC        # noqa: E402
from clipfactory import store                # noqa: E402

DAY = date(2031, 7, 15)


class PunchCutTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.acct = self.cfg.account("politics")
        self.jobs = self.tmp / "Jobs"
        p = mock.patch.object(L, "JOBS_DIR", self.jobs)
        p.start()
        self.addCleanup(p.stop)
        with store.edit(store.path(self.acct.slug, "social", "settings.json"), dict) as d:
            d.setdefault("modes", {})["learn.experiments"] = "on"
        self.eid = L.new_experiment(self.cfg, self.acct, question="Do punch cuts lift a one-camera clip?",
                                    kind="punch_cut", assign="given", by="Andrew", signed=True,
                                    arms={"A": "one camera as shot", "B": "punch cuts"})

    def slot(self, k: str, record):
        folder = self.tmp / "drafts" / k.replace(":", "")
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"01-clip-{k.replace(':', '')}"
        (folder / f"{stem}.mp4").write_bytes(b"x")
        (folder / "cast-check.json").write_text(json.dumps({"job": "A Job"}))
        clips = self.jobs / "A Job" / "clips"
        clips.mkdir(parents=True, exist_ok=True)
        rec = {"cards": []}
        if record is not None:
            rec["punch_cuts"] = record
        (clips / f"{stem}.cards.json").write_text(json.dumps(rec))
        return {"slot": k, "status": "ready", "notes": [], "publish_at": f"{DAY.isoformat()}T{k}:00-06:00",
                "clip": {"path": str(folder / f"{stem}.mp4"), "sha256": "s" + k}}

    def collect(self, s):
        ctx = SOC.CollectCtx(cfg=self.cfg, acct=self.acct, day=DAY, slot=s, extras={})
        L.on_collect(ctx)
        return ctx

    def test_the_arm_is_the_engines(self):
        got = [self.collect(self.slot(k, {"eligible": True, "arm": arm})).extras["experiment"][self.eid]
               for k, arm in (("08:45", "B"), ("10:30", "B"), ("12:15", "A"))]
        self.assertEqual(got, ["B", "B", "A"])                            # never alternated over the engine's draw
        posts = L.experiments(self.acct)[self.eid]["posts"]
        self.assertEqual((len(posts["A"]), len(posts["B"])), (1, 2))
        self.assertFalse(any(p["practice"] for a in "AB" for p in posts[a]))

    def test_a_clip_out_of_the_test_is_practice(self):
        for k, rec in (("08:45", {"eligible": False, "why": "the source cuts on its own"}), ("10:30", None)):
            c = self.collect(self.slot(k, rec))
            self.assertNotIn("experiment", c.extras)
        posts = L.experiments(self.acct)[self.eid]["posts"]
        entries = [p for a in "AB" for p in posts[a]]
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(p["practice"] for p in entries))
        self.assertIn("the source cuts on its own", entries[0]["why"] + entries[1]["why"])

    def test_a_new_clip_in_the_slot_takes_its_own_arm(self):
        self.collect(self.slot("08:45", {"eligible": True, "arm": "A"}))
        self.collect(self.slot("08:45", {"eligible": True, "arm": "B"}))      # Make again: another clip, other side
        posts = L.experiments(self.acct)[self.eid]["posts"]
        self.assertEqual((len(posts["A"]), len(posts["B"])), (0, 1))


if __name__ == "__main__":
    unittest.main()
