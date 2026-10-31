"""doctor's one-truth check reads the scheduled-task prompts and the account guides for rules that ended (audit
STA-19). Throwaway folders; read-only."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-surfaces-state-")

import json
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C   # noqa: E402
from clipfactory import hermes as H   # noqa: E402


class SurfacesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-surfaces-"))
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        for slug, a in raw["accounts"].items():
            a["account_dir"] = str(self.tmp / "accounts" / slug)
            (self.tmp / "accounts" / slug / "Reference").mkdir(parents=True)
        (self.tmp / "accounts.json").write_text(json.dumps(raw))
        self.cfg = C.load(self.tmp / "accounts.json")
        for task in ("hearing-clip-sweep-2", "politics-early-gap-fill", "gaming-morning-sweep"):
            (self.tmp / "scheduled-tasks" / task).mkdir(parents=True)
            (self.tmp / "scheduled-tasks" / task / "SKILL.md").write_text("Ten a day, 07:00 to 23:00. One Approve.\n")
        led = self.tmp / "accounts" / "politics"
        (led / "Reference" / "hearing-clip-sweep.md").write_text(
            "This WINS over everything else in this file that says hearings only, four a day, or one per template.\n")
        (led / "START HERE.html").write_text("<p>Approve signs and schedules.</p>\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lines(self):
        return [(good, text) for good, text, _ in H.drift(self.cfg, self.tmp) if "task prompts and guides" in text]

    def test_1_clean_files_pass_and_banners_are_not_rules(self):
        got = self.lines()
        self.assertEqual(len(got), len(self.cfg.accounts), got)      # one line per account switched on
        self.assertTrue(all(good for good, _ in got), got)
        surf = H.doc_surfaces(self.cfg, self.tmp / "scheduled-tasks")
        self.assertEqual(len(surf["politics"]), 4)      # two prompts, the sweep, START HERE
        self.assertEqual(len(surf["gaming"]), 1)

    def test_2_a_retired_rule_is_caught(self):
        (self.tmp / "accounts" / "politics" / "SETUP.html").write_text(
            "<p>Four clips a day is safe (four is the ceiling).</p><p>Press Schedule approved.</p>\n")
        (self.tmp / "scheduled-tasks" / "gaming-morning-sweep" / "SKILL.md").write_text(
            "Slots 05:00 to 23:00, every two hours.\n")
        bad = {text for good, text in self.lines() if not good}
        self.assertEqual(len(bad), 2, bad)
        led = next(t for t in bad if t.startswith("Politics"))
        self.assertIn("SETUP.html: the four-a-day ceiling", led)
        self.assertIn("Schedule approved", led)
        self.assertIn("05:00-23:00", next(t for t in bad if t.startswith("Gaming")))

    def test_3_the_words(self):
        self.assertEqual(H.retired_words("up to ten (rotation.py decides)"), [])
        self.assertEqual(len(H.retired_words("as many as rotation.py allows (four is the ceiling)")), 1)
        self.assertEqual(H.retired_words("about every 1h45, 07:00 to 23:00"), [])

    def test_4_only_the_clip_count_wording(self):
        # the old sweep's words, and the count phrased as clips: flagged
        self.assertEqual(len(H.retired_words("builds tomorrow's clips (up to four, one per template, rotation.py)")), 1)
        self.assertEqual(len(H.retired_words("cut up to four clips a night")), 1)
        # four of something else is no day ceiling
        self.assertEqual(H.retired_words("a REACTIONS tile holds up to four streamers"), [])
        self.assertEqual(H.retired_words("up to four plates on one clip"), [])

    def test_5_the_reactions_task_is_read_once_it_exists(self):
        self.assertEqual(len(H.doc_surfaces(self.cfg, self.tmp / "scheduled-tasks")["gaming"]), 1)
        (self.tmp / "scheduled-tasks" / "gta-reactions-daily-auto").mkdir(parents=True)
        (self.tmp / "scheduled-tasks" / "gta-reactions-daily-auto" / "SKILL.md").write_text("Up to four clips a day.\n")
        self.assertEqual(len(H.doc_surfaces(self.cfg, self.tmp / "scheduled-tasks")["gaming"]), 2)
        bad = [text for good, text in self.lines() if not good]
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("SKILL.md: the four-a-day ceiling", bad[0])


    def test_6_a_pointer_task_has_its_prompt_file_read(self):
        """clip-coach and clip-weekly-report install a one-line SKILL.md that points at ~/clip-factory/prompts; the
        rules are in that file, so a retired rule written there is caught (review 2026-10-20)."""
        prompts = self.tmp / "prompts"
        prompts.mkdir()
        (prompts / "coach.md").write_text("Score each draft. Ten a day.\n")
        (prompts / "weekly-report.md").write_text("Post every two hours, slots 05:00 to 23:00.\n")
        with mock.patch.object(H, "PROMPTS", prompts):
            # not installed yet: neither the stub nor the prompt file is read
            self.assertTrue(all(good for good, _ in self.lines()), self.lines())
            self.assertNotIn(prompts / "weekly-report.md", H.doc_surfaces(self.cfg, self.tmp / "scheduled-tasks")
                             ["gaming"])
            for task, file in (("clip-coach", "coach.md"), ("clip-weekly-report", "weekly-report.md")):
                (self.tmp / "scheduled-tasks" / task).mkdir(parents=True)
                (self.tmp / "scheduled-tasks" / task / "SKILL.md").write_text(
                    f"Read and follow ~/clip-factory/prompts/{file}\n")
            surf = H.doc_surfaces(self.cfg, self.tmp / "scheduled-tasks")
            for slug in ("gaming", "politics"):
                self.assertIn(prompts / "coach.md", surf[slug])
                self.assertIn(prompts / "weekly-report.md", surf[slug])
            bad = [text for good, text in self.lines() if not good]
        self.assertEqual(len(bad), 2, bad)                  # both accounts run the weekly report
        for text in bad:
            self.assertIn("weekly-report.md: the 05:00-23:00 two-hourly slots", text)
            self.assertNotIn("coach.md", text)

    def test_7_the_shipped_prompts_state_no_retired_rule(self):
        for task, file in H.TASK_PROMPT_FILES.items():
            self.assertTrue((H.PROMPTS / file).is_file(), file)
            self.assertEqual(H.retired_words((H.PROMPTS / file).read_text()), [], file)

if __name__ == "__main__":
    unittest.main()
