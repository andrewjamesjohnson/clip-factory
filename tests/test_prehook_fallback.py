import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# THE PRE-HOOK WHEN THE COLD OPEN FAILS (prehook.fallback, 2026-10-29; Andrew 10.28: "pre-hook when it fails"), made
# THE OPENER RULE's pre-hook the same evening (Daily 8 step 3): a CALM start (no VS card, no shout, no fight, no strong
# line in the clip's own first 3 s) outside the pre-hook test gets the pre-hook at collect, with or without a cold open
# after it; only while the pre_hook.fallback switch is on, from pre_hook.fallback_from; the engine's record must be the
# delivered render's; a calm clip counts in the test (both arms) and the fallback leaves it to its arm; a strong start
# sits out of both. Tiny synthetic videos; no network.

import json
import shutil
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_prehook import Base                # noqa: E402

from clipfactory import learn as L           # noqa: E402
from clipfactory import prehook as PH        # noqa: E402
from clipfactory import social as SOC        # noqa: E402
from clipfactory import store                # noqa: E402
from clipfactory import vscard as VS         # noqa: E402

DAY = date(2031, 7, 14)


class FallbackBase(Base):
    def setUp(self):
        super().setUp()
        self.jobs = self.tmp / "jobs"
        for patch in (mock.patch.object(VS, "JOBS", self.jobs),
                      mock.patch.object(VS, "slot_job", return_value="Job")):
            patch.start()
            self.addCleanup(patch.stop)

    def made(self, s: dict, record, same: bool = True) -> None:
        """The job's own render of the slot's clip, and the engine's cold open record beside it."""
        clips = self.jobs / "Job" / "clips"
        clips.mkdir(parents=True, exist_ok=True)
        stem = Path(s["clip"]["path"]).stem
        if same:
            shutil.copy2(s["clip"]["path"], clips / f"{stem}.mp4")
        else:
            (clips / f"{stem}.mp4").write_bytes(b"another render")
        if record is not None:
            (clips / f"{stem}.coldopen.json").write_text(json.dumps(record))

    def captions(self, s: dict, *lines) -> None:
        """The .srt beside the delivered clip: (start, end, words) cues."""
        def ts(x):
            ms = int(round(x * 1000))
            return f"00:00:{ms // 1000:02d},{ms % 1000:03d}"
        Path(s["clip"]["path"]).with_suffix(".srt").write_text(
            "\n\n".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}" for i, (a, b, t) in enumerate(lines, 1)) + "\n")

    def setting(self, key, value):
        with store.edit(store.path(self.acct.slug, "social", "settings.json"), dict) as d:
            d.setdefault("settings", {})[key] = value


class OpensOnNothingTest(FallbackBase):
    def test_a_refused_cold_open_is_a_bare_opening(self):
        s = self.slot("07:00")
        self.made(s, {"skip": "'That's enough.' has no real pause before it"})
        self.assertEqual(PH.opens_on_nothing(self.acct, s), (True, "'That's enough.' has no real pause before it"))

    def test_a_cold_open_a_vs_card_or_a_pre_hook_opens_it(self):
        s = self.slot("07:00")
        self.made(s, {"a": 1.0, "b": 3.0, "d": 2.0, "text": "Put down the mic."})
        bare, why = PH.opens_on_nothing(self.acct, s)
        self.assertFalse(bare)
        self.assertIn("its cold open", why)
        self.made(s, {"skip": "no line named"})
        self.assertFalse(PH.opens_on_nothing(self.acct, dict(s, vs_card={"left": "a"}))[0])
        self.assertFalse(PH.opens_on_nothing(self.acct, dict(s, pre_hook={"name": "dam-drop"}))[0])

    def test_no_record_or_another_renders_record_is_not_known(self):
        s = self.slot("07:00")
        self.made(s, None)
        self.assertIn("no cold open record", PH.opens_on_nothing(self.acct, s)[1])
        self.made(s, {"skip": "no line named"}, same=False)
        self.assertIn("another render's", PH.opens_on_nothing(self.acct, s)[1])


class SwitchTest(FallbackBase):
    def test_off_unless_switched_on_and_from_its_first_board(self):
        self.assertFalse(PH.fallback_on(self.acct, "2031-07-14"))
        self.switch("pre_hook.fallback", "on")
        self.assertTrue(PH.fallback_on(self.acct, "2031-07-14"))
        self.setting("pre_hook.fallback_from", "2031-07-15")
        self.assertFalse(PH.fallback_on(self.acct, "2031-07-14"))
        self.assertTrue(PH.fallback_on(self.acct, "2031-07-15"))


class FallbackTest(FallbackBase):
    def test_a_calm_start_gets_the_pre_hook_and_says_why(self):
        self.switch("pre_hook.fallback", "on")
        s = self.slot("07:00", cut=1.6)
        was = s["clip"]["sha256"]
        self.made(s, {"skip": "no real pause"})
        self.captions(s, (0.2, 1.4, "The committee will come to order."))
        rec, why = PH.fallback(self.cfg, self.acct, s)
        self.assertEqual(why, "")
        self.assertEqual(rec["fallback"], "no fight and no strong line in its own first 3 s")
        self.assertEqual(s["pre_hook"]["files"], ["clip", "instagram_reels"])
        self.assertEqual(s["clip"]["delivered_sha256"], was)
        self.assertAlmostEqual(s["clip"]["seconds"], 2.3, delta=0.06)       # 0.8s of pre-hook in front
        self.assertTrue(any("the pre-hook is its opener" in n for n in s["notes"]))

    def test_nothing_changes_when_it_is_off_or_the_start_is_strong(self):
        s = self.slot("07:00")
        was = s["clip"]["sha256"]
        self.made(s, {"skip": "no real pause"})
        self.captions(s, (0.2, 1.4, "Will you resign?"))
        self.assertIn("switch is off", PH.fallback(self.cfg, self.acct, s)[1])
        self.switch("pre_hook.fallback", "on")
        rec, why = PH.fallback(self.cfg, self.acct, s)
        self.assertIsNone(rec)
        self.assertIn("a question", why)
        self.assertEqual(s["clip"]["sha256"], was)
        self.assertNotIn("pre_hook", s)
        self.assertIn("VS card", PH.fallback(self.cfg, self.acct, dict(s, vs_card={"left": "a"}))[1])

    def test_a_calm_start_after_its_cold_open_still_takes_the_pre_hook_first(self):
        self.switch("pre_hook.fallback", "on")
        s = self.slot("07:00")
        self.made(s, {"a": 1.0, "b": 3.0, "d": 1.0, "text": "Sit down!"})
        # the cold open's own shout is the first second; the clip's own first 3 s after it are calm
        self.captions(s, (0.0, 1.0, "Sit down!"), (1.2, 1.5, "Thank you, Mr. Chairman."))
        rec, why = PH.fallback(self.cfg, self.acct, s)
        self.assertEqual(why, "")
        self.assertIn("pre_hook", s)

    def test_a_shout_and_a_fight_never_take_one(self):
        self.switch("pre_hook.fallback", "on")
        s = self.slot("07:00")
        self.made(s, {"a": 1.0, "b": 3.0, "d": 1.0, "text": "[SHOUTING]", "from": "THE OUTBURST's shout"})
        self.captions(s, (1.2, 1.5, "Thank you."))
        self.assertIn("its shout", PH.fallback(self.cfg, self.acct, s)[1])
        f = self.slot("07:00")
        self.made(f, {"skip": "no real pause"})
        self.captions(f, (1.2, 1.5, "Thank you."))
        self.assertIn("FACE-OFF", PH.fallback(self.cfg, self.acct, dict(f, format="faceoff"))[1])


class TestTakesCalmStarts(FallbackBase):
    def test_a_calm_start_counts_in_the_test_and_the_fallback_leaves_it_to_its_arm(self):
        self.switch("learn.experiments", "on")
        self.switch("pre_hook.fallback", "on")
        eid = L.new_experiment(self.cfg, self.acct, question="Does a pre-hook lift views?", kind="pre_hook",
                               arms={"A": "no pre-hook", "B": "pre-hook"}, by="Andrew", signed=True)
        calm = self.slot("07:00")
        self.made(calm, {"skip": "no real pause"})
        self.captions(calm, (0.2, 1.4, "The committee will come to order."))
        self.assertEqual(L._cannot_apply(self.acct, {"kind": "pre_hook"}, {}, calm), "")
        ctx = SOC.CollectCtx(cfg=self.cfg, acct=self.acct, day=DAY, slot=calm, extras={})
        L.on_collect(ctx)                                                   # the test: it counts in an arm
        entry = [(a, p) for a in "AB" for p in L.experiments(self.acct)[eid]["posts"][a] if p["slot"] == "07:00"][0]
        self.assertFalse(entry[1]["practice"])
        had = "pre_hook" in calm
        rec, why = PH.fallback(self.cfg, self.acct, calm)                   # then collect's fallback stays out
        self.assertIsNone(rec)
        self.assertIn("the pre-hook test decides this clip" if not had else "it opens on the pre-hook", why)

    def test_a_strong_start_sits_out_of_both_arms(self):
        strong = self.slot("07:00")
        self.made(strong, {"skip": "no real pause"})
        self.captions(strong, (0.2, 1.4, "You are a liar."))
        self.assertIn("not a calm start", L._cannot_apply(self.acct, {"kind": "pre_hook"}, {}, strong))


if __name__ == "__main__":
    unittest.main()
