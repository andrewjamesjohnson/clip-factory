"""The GTA maker's GAMEPLAY lane: which sources a gameplay slot takes and what it hands run.py. No skill is run."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-gameplay-state-")   # set before clipfactory is imported:
# config.STATE is read once, and a module found before test_factory would otherwise point the run at state/

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from politics_slots import pin_gta  # noqa: E402  the GTA days these tests were written on
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import makers as M     # noqa: E402
import factory as F                     # noqa: E402

DAY = date(2030, 1, 7)


def row(n: int, kind: str, channel: str = "") -> dict:
    return {"url": f"https://www.youtube.com/watch?v=v{n}", "status": "new", "kind": kind, "score": 100 - n,
            "channel": channel or f"ch{n}", "name": f"Creator {n}", "title": f"upload {n}"}


class GameplayLaneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-gameplay-"))
        self._state = C.STATE
        C.STATE = self.tmp / "state"
        raw = pin_gta(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(self.tmp / "drafts")
        a = raw["accounts"]["gaming"]
        a.pop("gta_maker", None)            # the live config's owner switches (spread) are not this test's
        # the b-roll and gameplay lanes' own mechanics: since 2026-10-29 the live Gaming deals "best" slots (Best of
        # GTA, tests/test_best_of_gta.py) and counts its day by dated "volume" rules (6, then 8 from 11-19), which a
        # 2030 test day would read; these tests keep the rotation and the plain slots they were written for
        a["formats"], a["slot_formats"] = ["underlay", "pip"], ["underlay", "pip", "gameplay"]
        a.pop("volume", None)
        a["account_dir"] = str(self.tmp / "Gaming")
        a["delivered"] = [str(self.tmp / "delivered" / "{YYYY}{MM}{DD}")]
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        self.cfg = C.load(p)
        self.acct = self.cfg.account("gaming")

    def tearDown(self):
        C.STATE = self._state

    def slots(self, *fmts: str) -> list:
        return [{"slot": self.acct.slots[i], "format": f, "status": B.OPEN} for i, f in enumerate(fmts)]

    def set_formats(self, fmts: dict) -> None:
        with B.edit(self.cfg, self.acct, DAY) as bd:
            for k, f in fmts.items():
                bd["slots"][k]["format"] = f

    # ------------------------------------------------------------------------------------------- the config
    def test_gameplay_is_allowed_but_the_nightly_rotation_is_unchanged(self):
        self.assertIn("gameplay", self.acct.slot_formats)
        self.assertEqual(self.acct.formats, ["underlay", "pip"])
        with B.edit(self.cfg, self.acct, DAY) as bd:
            pass
        self.assertNotIn("gameplay", {s["format"] for s in bd["slots"].values()})

    def test_template_passes_gameplay_through_and_falls_back_to_underlay(self):
        self.assertEqual(M.gta_template(self.acct, "gameplay"), "gameplay")
        self.assertEqual(M.gta_template(self.acct, "pip"), "pip")
        self.assertEqual(M.gta_template(self.acct, "quiz"), "underlay")
        self.acct.slot_formats = ["underlay", "pip"]   # an account that never allowed it
        self.assertEqual(M.gta_template(self.acct, "gameplay"), "underlay")

    # ----------------------------------------------------------------------------------------- the planning
    def test_gameplay_slots_take_only_creators_playing(self):
        sources = [row(1, "breakdown"), row(2, "news"), row(3, "physics"), row(4, "streamer"), row(5, "scout")]
        plan, left = M.plan_gta(self.acct, self.slots("underlay", "gameplay", "pip"), sources)
        got = {s["slot"]: (item["kind"], t) for item, ss, t in plan for s in ss}
        self.assertEqual(got["08:45"], ("physics", "gameplay"))
        self.assertEqual(got["07:00"][0], "breakdown")   # the b-roll lane keeps its best upload
        self.assertEqual(left, [])
        self.assertEqual([ss[0]["slot"] for _, ss, _ in plan], sorted(ss[0]["slot"] for _, ss, _ in plan))

    def test_a_source_is_never_cut_twice_in_one_night(self):
        sources = [row(1, "streamer"), row(2, "breakdown")]
        plan, _ = M.plan_gta(self.acct, self.slots("gameplay", "underlay"), sources)
        self.assertEqual(len({item["url"] for item, _, _ in plan}), len(plan))
        self.assertEqual({item["kind"]: t for item, _, t in plan}, {"streamer": "gameplay", "breakdown": "underlay"})

    def test_gameplay_with_no_creator_playing_is_left_open(self):
        plan, left = M.plan_gta(self.acct, self.slots("gameplay", "underlay"), [row(1, "breakdown"), row(2, "news")])
        self.assertEqual([s["slot"] for s in left], ["07:00"])
        self.assertEqual([(ss[0]["slot"], t) for _, ss, t in plan], [("08:45", "underlay")])

    def test_no_gameplay_slots_plan_as_before(self):
        # the old planner: slots in order, clips_per_source a source, in the first slot's template
        todo = self.slots(*(["underlay", "pip"] * 5))
        sources = [row(i, k) for i, k in enumerate(["streamer", "breakdown", "physics", "news"], start=1)]
        plan, left = M.plan_gta(self.acct, todo, sources)
        n = self.acct.clips_per_source
        old = [(sources[i], todo[i * n:(i + 1) * n]) for i in range(4)]
        self.assertEqual([(i["url"], [s["slot"] for s in ss]) for i, ss, _ in plan],
                         [(i["url"], [s["slot"] for s in ss]) for i, ss in old])
        self.assertEqual([t for _, _, t in plan], [ss[0]["format"] for _, ss in old])
        self.assertEqual(left, [])

    # ------------------------------------------------------------------------------------ what run.py gets
    def test_make_hands_run_py_the_gameplay_template_at_both_stages(self):
        inbox = self.acct.account_dir / "Publish" / "inbox.json"
        inbox.parent.mkdir(parents=True)
        inbox.write_text(json.dumps({"items": [row(1, "breakdown"), row(2, "physics", "kwebbelkop")]}))
        self.set_formats({"07:00": "gameplay", "08:45": "gameplay"})
        calls = []

        def fake_run(cmd, log, cwd=None, env=None, timeout=0):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        with mock.patch.object(M, "run_logged", fake_run), \
                mock.patch.object(M, "pack_gta_slot", lambda *a: "  packed"), \
                mock.patch.object(M.K, "collect", lambda *a, **k: []):
            M.make_gta(self.cfg, self.acct, DAY, only=["07:00", "08:45"])
        runs = [c for c in calls if c[1] == "run.py"]
        intake, picks = runs[0], runs[1:]
        self.assertEqual(intake[2], "https://www.youtube.com/watch?v=v2")   # the creator playing, not the breakdown
        self.assertEqual(intake[intake.index("--template") + 1], "gameplay")
        job = intake[intake.index("--job") + 1]
        self.assertTrue(job.startswith("factory-20300107-gameplay-kwebbelkop"))
        self.assertEqual(len(picks), 2)
        for c in picks:
            self.assertEqual(c[2], job)
            self.assertEqual(c[c.index("--template") + 1], "gameplay")
            self.assertIn("--deliver", c)
        bd = B.read(self.acct, DAY)
        self.assertEqual({bd["slots"][k]["format"] for k in ("07:00", "08:45")}, {"gameplay"})

    def test_dry_run_names_the_lane(self):
        inbox = self.acct.account_dir / "Publish" / "inbox.json"
        inbox.parent.mkdir(parents=True)
        inbox.write_text(json.dumps({"items": [row(1, "breakdown")]}))
        self.set_formats({"07:00": "gameplay"})
        lines = M.make_gta(self.cfg, self.acct, DAY, only=["07:00", "08:45"], dry_run=True)
        self.assertTrue(any(ln.startswith("07:00 (gameplay): no waiting upload of a creator PLAYING") for ln in lines))
        self.assertTrue(any("-> 08:45 (pip)" in ln for ln in lines))

    # ------------------------------------------------------------------------------- factory.py plan --format
    def test_plan_format_sets_open_slots_and_refuses_the_rest(self):
        ns = lambda **k: argparse.Namespace(**{"account": "gaming", "date": DAY.isoformat(), "slots": None,
                                               "format": None, **k})
        F.cmd_plan(self.cfg, ns(slots="14:00,15:45", format="gameplay"))
        bd = B.read(self.acct, DAY)
        self.assertEqual([bd["slots"][k]["format"] for k in ("12:15", "14:00", "15:45")], ["pip", "gameplay", "gameplay"])
        with self.assertRaises(SystemExit):
            F.cmd_plan(self.cfg, ns(slots="14:00", format="quiz"))
        with self.assertRaises(SystemExit):
            F.cmd_plan(self.cfg, ns(format="gameplay"))
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["17:30"]["status"] = B.READY
        with self.assertRaises(SystemExit):
            F.cmd_plan(self.cfg, ns(slots="17:30", format="gameplay"))
        self.assertEqual(B.read(self.acct, DAY)["slots"]["17:30"]["format"], "underlay")


if __name__ == "__main__":
    unittest.main()
