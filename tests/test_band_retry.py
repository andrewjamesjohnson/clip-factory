import os, tempfile
os.environ["CLIP_FACTORY_OFFLINE"] = "1"
os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp(prefix="clipfactory-band-"))
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipfactory import makers as M  # noqa: E402


class BandStopIsARefusedPick(unittest.TestCase):
    def test_a_stop_at_the_band_is_retried(self):
        out = ("== the band on their words (filmband.py assign)\n   -- 2s, exit 3\n\nSTOPPED at the band: neither "
               "Rockstar's trailers nor their own footage match these words, and a non-GTA reel is off (2026-10-18).")
        self.assertEqual(M.preflight_refusal(M._last_preflight(out)), ["BAND"])
        self.assertNotIn("BAND", M.NOT_RETRIED)

    def test_a_preflight_stop_still_reads_its_gates(self):
        out = "== preflight\n    [FAIL] MUSIC  62% music\nSTOPPED at 'preflight' (exit 1); the whole output is in x"
        self.assertEqual(M.preflight_refusal(out), ["MUSIC"])

    def test_stutter_at_the_grade_is_retried(self):
        out = "    [ BAD  ] STUTTER  band updates 6 times a second\nSTOPPED at the grade: (the STUTTER line above)"
        self.assertEqual(M.preflight_refusal(out), ["STUTTER at the grade"])

    def test_an_unknown_stop_is_not_a_refusal(self):
        self.assertIsNone(M.preflight_refusal("STOPPED at 'the render (build_all.py)' (exit 1)"))


if __name__ == "__main__":
    unittest.main()
