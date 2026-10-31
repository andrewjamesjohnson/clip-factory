import os, tempfile
os.environ["CLIP_FACTORY_OFFLINE"] = "1"
os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp(prefix="clipfactory-statsfu-"))
import json, sys, unittest
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipfactory import config as C      # noqa: E402
from clipfactory import statspage as SP  # noqa: E402


class EligibleSince(unittest.TestCase):
    def setUp(self):
        self.old = C.STATE
        C.STATE = Path(tempfile.mkdtemp(prefix="clipfactory-statsfu-state-"))
        self._dir = SP._dir
        SP._dir = lambda: C.STATE / "stats"

    def tearDown(self):
        C.STATE = self.old
        SP._dir = self._dir

    def test_a_tick_records_its_day_and_an_untick_clears_it(self):
        SP.save_settings({"eligible": {"gaming": {"youtube": True}}})
        s = SP.settings()
        self.assertTrue(s["eligible"]["gaming"]["youtube"])
        self.assertEqual(s["eligible_since"]["gaming"]["youtube"], date.today().isoformat())
        raw = json.loads((C.STATE / "stats" / "settings.json").read_text())
        raw["eligible_since"]["gaming"]["youtube"] = "2026-01-02"     # an earlier tick survives a later save
        (C.STATE / "stats" / "settings.json").write_text(json.dumps(raw))
        SP.save_settings({"goal": 500})
        self.assertEqual(SP.settings()["eligible_since"]["gaming"]["youtube"], "2026-01-02")
        SP.save_settings({"eligible": {"gaming": {"youtube": False}}})
        self.assertNotIn("youtube", SP.settings()["eligible_since"].get("gaming", {}))

    def test_the_page_counts_actual_pay_only_from_the_tick(self):
        self.assertIn("eligible_since", SP.JS)
        self.assertIn("String(p.day || '') >= since", SP.JS)


if __name__ == "__main__":
    unittest.main()
