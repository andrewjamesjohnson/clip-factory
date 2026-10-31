import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# THE VS CARD's announcer (clipfactory/vsannounce.py, 2026-10-28, Andrew: "Use voice C ARCADE for the VS card
# announcer. Tighten it and wire it in"): the four words found in the sound, spliced tight, the card's beats on them, a
# pair made once and kept, and never the model in tests, in the night shift's last minutes or without its environment
# (the card then plays its own sound alone). Synthetic tones only; the model never loads here.

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipfactory import vsannounce as VA   # noqa: E402
from clipfactory import vscard as VS       # noqa: E402

SR = VA.SR


def tones(spans):
    """A tone burst for each (start, end) in seconds, silence between."""
    x = np.zeros(int((spans[-1][1] + 0.3) * SR), np.float32)
    for a, e in spans:
        t = np.arange(int((e - a) * SR)) / SR
        x[int(a * SR):int(a * SR) + len(t)] = 0.8 * np.sin(2 * np.pi * 220 * t)
    return x


WORDS4 = [(0.10, 0.45), (0.95, 1.30), (1.90, 2.30), (2.90, 3.20)]   # a slow read: long gaps


class Words(unittest.TestCase):
    def test_four_words_in_the_sound(self):
        b = VA.bursts(tones(WORDS4))
        self.assertEqual(len(b), 4)
        for (a, e), (ga, ge, ca, ce) in zip(WORDS4, b):
            self.assertAlmostEqual(ga, a, delta=0.05)
            self.assertAlmostEqual(ge, e, delta=0.05)
            self.assertLessEqual(ga, ca)                                     # the consonants sit outside the core
            self.assertGreaterEqual(ge, ce)

    def test_a_name_in_two_bursts_joins_its_closest_gap(self):
        spans = [(0.10, 0.30), (0.36, 0.55), (1.05, 1.40), (2.00, 2.40), (3.00, 3.30)]   # "Ocasio... Cortez"
        self.assertEqual(len(VA.bursts(tones(spans))), 4)

    def test_spliced_tight_with_a_beat_before_fight(self):
        x = tones(WORDS4)
        b = VA.bursts(x)
        line, starts, speech = VA.splice(x, b)
        for i in range(3):                                                    # only the silence got shorter:
            said = b[i][3] - b[i][0]                                          # word i up to its core's end,
            kept = 0.04 + 0.06 + VA.GAPS[i]                                   # the silence kept around the cut,
            lead = b[i + 1][2] - b[i + 1][0]                                  # less the next word's soft start
            self.assertAlmostEqual(starts[i + 1] - starts[i], said + kept - lead - 0.008, delta=0.004)
        self.assertLess(speech, WORDS4[-1][1] - WORDS4[0][0] - 1.0)          # over a second shorter than the read
        self.assertGreater(VA.GAPS[2], VA.GAPS[0])                           # the beat before FIGHT! is the longest
        self.assertAlmostEqual(len(line) / SR, speech, delta=0.01)

    def test_a_word_is_never_cut(self):
        x = tones(WORDS4)
        b = VA.bursts(x)
        line, starts, _speech = VA.splice(x, b)
        for (a, e, _ca, _ce), s in zip(b, starts):                           # each word whole, at its new place
            n = int((e - a) * SR) - int(0.01 * SR)
            src, dst = x[int(a * SR) + 100:int(a * SR) + n], line[int(s * SR) + 100:int(s * SR) + n]
            self.assertLess(float(np.abs(src - dst[:len(src)]).max()), 0.05)

    def test_words_that_run_together_are_left_alone(self):
        spans = [(0.10, 0.45), (0.47, 0.80), (1.40, 1.80), (2.40, 2.70)]   # "Warren-versus" in one breath
        x = tones(spans)
        b = VA.bursts(x)
        if len(b) == 4:
            line, starts, _s = VA.splice(x, b)
            self.assertAlmostEqual(starts[1] - starts[0], b[1][0] - b[0][0], delta=0.002)

    def test_the_arena_keeps_the_words_and_adds_a_tail(self):
        x = tones(WORDS4)
        line, _s, _sp = VA.splice(x, VA.bursts(x))
        out = VA.arena(line)
        self.assertGreater(len(out), len(line) / VA.PITCH)                   # lower and longer, with its echo tail
        self.assertLessEqual(float(np.abs(out).max()), 1.0001)
        self.assertLess(float(np.abs(out[-20:]).max()), 0.01)                # the tail is faded, never cut


class Heard(unittest.TestCase):
    def test_the_call_in_order(self):
        self.assertTrue(VA.says_it("warren! versus! stumpf! fight!", "Warren", "Stumpf"))
        self.assertTrue(VA.says_it("warren vs stompf fight", "Warren", "Stumpf"))
        self.assertFalse(VA.says_it("roaring vs. stone fight!", "Warren", "Stumpf"))    # 10.28: A ARENA's mush
        self.assertFalse(VA.says_it("warren! versus! stumpf!", "Warren", "Stumpf"))     # no FIGHT!
        self.assertTrue(VA.says_it("", "Warren", "Stumpf"))                  # no whisper here: the four words decide

    def test_every_word_of_a_first_and_last_name(self):
        self.assertTrue(VA.says_it("josh hawley! versus! dave calhoun! fight!", "Josh Hawley", "Dave Calhoun"))
        self.assertFalse(VA.says_it("orly! versus! calhoun! fight!", "Hawley", "Calhoun"))      # 10.28, every take
        self.assertFalse(VA.says_it("kristi orly! versus! calhoun! fight!", "Kristi Noem", "Calhoun"))
        self.assertTrue(VA.says_it("van hollen! versus! mace! fight!", "Van Hollen", "Mace"))


class Beats(unittest.TestCase):
    def test_the_card_lands_on_the_words(self):
        call = {"starts": {"left": 0.0, "versus": 0.42, "right": 0.93, "fight": 1.45}, "speech": 1.80}
        t, end = VS.plan_call(call)
        self.assertEqual(t["left"], VS.MERGE)
        self.assertAlmostEqual(t["vs"], VS.CALL_AT + 0.42 + 0.03, places=3)
        self.assertAlmostEqual(t["right"], VS.CALL_AT + 0.93 - 0.02, places=3)
        self.assertAlmostEqual(t["fight"], VS.CALL_AT + 1.45 + 0.03, places=3)
        self.assertLessEqual(end, VS.SECONDS)
        self.assertLess(t["vs"], t["right"])
        self.assertLess(t["right"], t["fight"])

    def test_a_rushed_call_still_gives_each_card_its_room(self):
        call = {"starts": {"left": 0.0, "versus": 0.1, "right": 0.2, "fight": 0.3}, "speech": 0.6}
        t, _end = VS.plan_call(call)
        self.assertGreaterEqual(t["right"] - t["vs"], 0.3 - 1e-9)
        self.assertGreaterEqual(t["fight"] - t["right"], 0.35 - 1e-9)


class Call(unittest.TestCase):
    def test_tests_never_load_the_model(self):
        with mock.patch.object(VA, "cache_dir", return_value=Path(tempfile.mkdtemp())):
            got, why = VA.call("Warren", "Stumpf")
        self.assertIsNone(got)
        self.assertIn("offline", why)

    def test_a_kept_pair_is_used_as_it_is(self):
        d = Path(tempfile.mkdtemp())
        key = VA._key("Warren", "Stumpf")
        VA._write(d / f"{key}.wav", np.zeros(SR, np.float32))
        (d / f"{key}.json").write_text(json.dumps({"starts": {"left": 0.0, "versus": 0.4, "right": 0.9,
                                                              "fight": 1.4}, "speech": 1.7}))
        with mock.patch.object(VA, "cache_dir", return_value=d):
            got, why = VA.call("Warren", "Stumpf")
        self.assertEqual(why, "")
        self.assertTrue(got["kept"])
        self.assertEqual(got["starts"]["fight"], 1.4)

    def test_never_in_the_night_shifts_last_minutes(self):
        here = Path(__file__)
        env = {k: v for k, v in os.environ.items() if k != "CLIP_FACTORY_OFFLINE"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(VA, "PY", here), mock.patch.object(VA, "GEN", here), \
                mock.patch.object(VA, "REF", here), \
                mock.patch.object(VA, "cache_dir", return_value=Path(tempfile.mkdtemp())), \
                mock.patch.object(VA.subprocess, "run", side_effect=AssertionError("the model ran")):
            got, why = VA.call("Warren", "Stumpf", now=datetime(2026, 10, 29, 5, 50))
        self.assertIsNone(got)
        self.assertIn("05:40", why)

    def test_not_installed_plays_the_sound_alone(self):
        env = {k: v for k, v in os.environ.items() if k != "CLIP_FACTORY_OFFLINE"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(VA, "PY", Path("/nowhere/python")), \
                mock.patch.object(VA, "cache_dir", return_value=Path(tempfile.mkdtemp())):
            got, why = VA.call("Warren", "Stumpf", now=datetime(2026, 10, 28, 14, 0))
        self.assertIsNone(got)
        self.assertIn("not installed", why)

    def test_a_pair_has_its_own_name(self):
        self.assertNotEqual(VA._key("Warren", "Stumpf"), VA._key("Stumpf", "Warren"))
        self.assertTrue(VA._key("Ocasio-Cortez", "Powell").startswith(VA.STYLE))


if __name__ == "__main__":
    unittest.main()
