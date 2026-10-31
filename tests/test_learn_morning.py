import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The morning report and the results coach (2026-10-20): every clip best to worst with its platforms side by side
# and their watch numbers, a platform that held a post back kept apart from the clip, the one-minute lines, the file
# on Andrew's desk (only in a temp folder under a test, whatever the config says), the results coach's notes (checked,
# kept beside the report, shown in the file) and its proposals reaching the runbooks' brief once Andrew signs them,
# and the runbook line in every bot and scout runbook of both accounts. Throwaway folders; nothing reaches a platform.

import json
import re
import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_learn as TL                      # noqa: E402  (its fixtures; a module, so its tests are not run twice)

from clipfactory import learn as L           # noqa: E402
from clipfactory import playbook as PB       # noqa: E402
from clipfactory import store                # noqa: E402

D = TL.D


class Morning(TL.Base):
    def setUp(self):
        super().setUp()
        self.switch(self.gta, "learn", "on")

    def desk_on(self) -> Path:
        d = self.tmp / "desk"
        d.mkdir(exist_ok=True)
        raw = json.loads(self.cfg_path.read_text())
        raw["report_desk"] = str(d)
        self.cfg_path.write_text(json.dumps(raw))
        return d

    def watch(self, day, slot, outlet, **w) -> None:
        p = store.path(self.gta.slug, f"{day}.json")
        with store.edit(p, dict) as bd:
            bd["slots"][slot]["outlets"][outlet]["metrics"].update(w)

    def extra_clip(self, slot="17:30", yt=100, fb=1, ig=150) -> None:
        """One more clip on D-1: YouTube and Instagram near their usual, Facebook almost nothing."""
        day = D - timedelta(days=1)
        p = store.path(self.gta.slug, f"{day}.json")
        bd = store.read(p, {})
        s = self.slot(day, slot, fmt="gaggle")
        for o, v in (("youtube_shorts", yt), ("facebook_reels", fb), ("instagram_reels", ig)):
            self.post(s, o, v)
        bd["slots"][slot] = s
        store.write(p, bd)


class RankingTest(Morning):
    def test_rows_carry_the_watch_numbers(self):
        self.sixty()
        self.watch(D - timedelta(days=1), "07:00", "instagram_reels", watch_pct=31.5, skip_pct=40.0, reach=120,
                   watch_s=11.2)
        self.watch(D - timedelta(days=1), "07:00", "facebook_reels", watch_pct=18.0, skip_pct=None)
        rows = {(r["board"], r["slot"], r["outlet"]): r for r in L.rows_for(self.cfg, self.gta, self.now)}
        ig = rows[(str(D - timedelta(days=1)), "07:00", "instagram_reels")]
        self.assertEqual(ig["watch"], {"watch_pct": 31.5, "watch_s": 11.2, "skip_pct": 40.0, "reach": 120})
        fb = rows[(str(D - timedelta(days=1)), "07:00", "facebook_reels")]
        self.assertEqual(fb["watch"], {"watch_pct": 18.0})           # a None is not a number

    def test_every_clip_once_best_to_worst(self):
        self.sixty()
        rep = L.report(self.cfg, self.gta, day=D, now=self.now, propose=False)
        clips = rep["clips"]
        self.assertEqual([c["slot"] for c in clips][0], "14:00")      # room at 1.1 x 1.5
        self.assertEqual(clips[-1]["slot"], "10:30")                 # gaggle at 0.9
        self.assertEqual([c["rank"] for c in clips], list(range(1, len(clips) + 1)))
        self.assertEqual(len({(c["board"], c["slot"]) for c in clips}), len(clips))     # one entry a clip
        self.assertTrue(all(len(c["posts"]) == 3 for c in clips))   # its three platforms side by side
        c = clips[0]
        want = sum(p["views_24h"] for p in c["posts"]) / sum(p["baseline_24h"] for p in c["posts"])
        self.assertAlmostEqual(c["x"], round(want, 2))
        self.assertEqual({c["board"] for c in clips}, {str(D - timedelta(days=1))})       # first day over only

    def test_a_platform_that_held_a_post_back_is_not_the_clip(self):
        self.sixty()
        self.extra_clip()                               # Facebook 1 against about 200, the others near the usual
        self.extra_clip(slot="21:00", yt=2, fb=1, ig=3)  # everything low: a weak clip, nothing held back
        rep = L.report(self.cfg, self.gta, day=D, now=self.now, propose=False)
        by = {c["slot"]: c for c in rep["clips"]}
        self.assertEqual(by["17:30"]["held_back"], ["facebook_reels"])
        self.assertEqual(by["21:00"]["held_back"], [])
        self.assertEqual(rep["clips"][-1]["slot"], "21:00")
        md = L.markdown(rep)
        self.assertIn("   Held back, not in its x: Facebook 1 (the usual is", md)
        c = by["17:30"]                                 # its x is YouTube and Instagram's, Facebook beside it
        took = [p for p in c["posts"] if p["outlet"] != "facebook_reels"]
        self.assertAlmostEqual(c["x"], round(sum(p["views_24h"] for p in took) / sum(p["baseline_24h"] for p in took), 2))
        lines = L.minute_lines(rep)
        self.assertTrue(any(ln.startswith("- Held back: 1 post under a tenth of the usual on Facebook at 24 hours") for ln in lines),
                        lines)

    def test_a_clip_with_no_usual_comes_last_and_says_so(self):
        self.sixty()
        rows = L.rows_for(self.cfg, self.gta, self.now)
        L.add_baselines(rows)
        for r in rows:
            if r["slot"] == "14:00" and r["board"] == str(D - timedelta(days=1)):
                r["baseline_24h"], r["rel_24h"] = None, None
        clips = L.ranked(self.cfg, rows, (D - timedelta(days=1), D))
        self.assertEqual(clips[-1]["slot"], "14:00")
        self.assertIsNone(clips[-1]["x"])
        self.assertIn("no usual yet", "\n".join(L.clip_lines({"clips": clips})))

    def test_the_report_words(self):
        self.sixty()
        self.watch(D - timedelta(days=1), "14:00", "instagram_reels", watch_pct=44.0, skip_pct=22.0, reach=150)
        rep = L.report(self.cfg, self.gta, day=D, now=self.now, propose=False)
        md = L.report_path(self.gta, D, "md").read_text()
        order = [md.index(h) for h in ("## Biggest problems", "## Every clip, best to worst", "## Best posts",
                                       "## What works", "## Proposals")]
        self.assertEqual(order, sorted(order))
        self.assertRegex(md, r"\n1\. \"Clip [^\"]+ 14:00\": [0-9.]+x the usual \(07\.13 14:00, room, from MrBossFTW\)\.")
        self.assertIn("; Instagram 248 (1.4x), 44% watched, 22% skipped, reach 150.", md)
        for bad in ("—", "–", "!"):
            self.assertNotIn(bad, md)
        self.assertEqual(rep["notes"], None)


class DeskTest(Morning):
    def test_no_folder_set_no_file(self):
        self.sixty()
        said = L.daily(self.cfg, now=self.now)
        self.assertFalse(any("morning file" in s for s in said))
        self.assertIsNone(L.desk_dir(self.cfg))

    def test_a_test_never_writes_outside_a_temp_folder(self):
        raw = json.loads(self.cfg_path.read_text())
        raw["report_desk"] = "~/Desktop/CLIP FACTORY/Daily Reviews"
        self.cfg_path.write_text(json.dumps(raw))
        self.assertIsNone(L.desk_dir(self.cfg))
        self.sixty()
        self.assertFalse(any("morning file" in s for s in L.daily(self.cfg, now=self.now)))

    def test_the_morning_file(self):
        d = self.desk_on()
        self.sixty()
        said = L.daily(self.cfg, now=self.now)
        f = d / f"Clip report {D + timedelta(days=1):%m.%d.%Y}.md"
        self.assertIn(f"morning file: {f}", said)
        text = f.read_text()
        self.assertTrue(text.startswith(f"# Clip report, Tuesday {D + timedelta(days=1):%m.%d.%Y}\n"))
        heads = [text.index(h) for h in ("## In one minute", "# Gaming", "## Every clip, best to worst",
                                         "## Biggest problems")]
        self.assertEqual(heads, sorted(heads))
        minute = text[text.index("## In one minute"):text.index("---")]
        self.assertIn("**Gaming** (clips of 07.13): 4 clips,", minute)
        self.assertIn("- Best: \"Clip", minute)
        self.assertIn("- Worst: \"Clip", minute)
        self.assertIn("- Biggest problem: 2 slots failed to make", minute)
        self.assertIn("- Waiting for you: nothing to sign.", minute)
        self.assertLessEqual(len(minute.splitlines()), 12)            # a minute's read
        self.assertNotIn("## Best posts", text)                       # nothing twice in the file he reads
        self.assertFalse(list(d.glob(".*.tmp")))
        self.assertIn("the clip report is in desk", L.morning_line(self.cfg, self.now))
        doc = L.doctor(self.cfg, self.now)
        self.assertTrue(any(ok is True and t.startswith("morning file: Clip report") for ok, t, _ in doc), doc)

    def test_a_missing_folder_is_said_not_made(self):
        d = self.desk_on()
        d.rmdir()
        self.sixty()
        L.daily(self.cfg, now=self.now)
        self.assertFalse(d.exists())                                  # never made on its own
        doc = L.doctor(self.cfg, self.now)
        self.assertTrue(any(ok is False and "is missing" in t for ok, t, _ in doc), doc)


class NotesTest(Morning):
    def notes(self, data) -> Path:
        p = self.tmp / "notes.json"
        p.write_text(json.dumps(data))
        return p

    def test_refused_unless_every_rule_holds(self):
        self.desk_on()
        self.sixty()
        L.daily(self.cfg, now=self.now)
        day1 = str(D - timedelta(days=1))
        bad = [({"clips": [{"board": day1, "slot": "06:00", "what": "x"}]}, "is not in the report's ranking"),
               ({"summary": "Great day — really"}, "no long dashes"),
               ({"summary": "Great day!"}, "no exclamation marks"),
               ({"clips": [{"board": day1, "slot": "07:00", "what": "y" * 301}]}, "300"),
               ({"problems": ["a", "b", "c", "d"]}, "at most 3 problems")]
        for data, why in bad:
            with self.assertRaises(SystemExit) as e:
                L.write_notes(self.cfg, self.gta, D, self.notes(data), "clip-results-coach")
            self.assertIn(why, str(e.exception))
        self.assertFalse(L.notes_path(self.gta, D).exists())
        with self.assertRaises(SystemExit):
            L.write_notes(self.cfg, self.gta, D - timedelta(days=3), self.notes({}), "clip-results-coach")

    def test_kept_shown_and_kept_again(self):
        d = self.desk_on()
        self.sixty()
        L.daily(self.cfg, now=self.now)
        day1 = str(D - timedelta(days=1))
        said = L.write_notes(self.cfg, self.gta, D, self.notes({
            "summary": "The best clip opened on the answer; the worst on the question.",
            "clips": [{"board": day1, "slot": "14:00", "what": "Opens on the answer at 0:00."},
                      {"board": day1, "slot": "10:30", "what": "The first 6 seconds are the question."}],
            "problems": ["2 of the 4 open on the question, not the answer."]}), "clip-results-coach")
        self.assertIn("notes for 2031-07-14 kept (2 clips, 1 problem)", said)
        text = (d / f"Clip report {D + timedelta(days=1):%m.%d.%Y}.md").read_text()
        self.assertIn("- Coach: The best clip opened on the answer; the worst on the question.", text)
        self.assertIn("   What happened: Opens on the answer at 0:00.", text)
        self.assertIn("What the coach saw:\n- 2 of the 4 open on the question, not the answer.", text)
        rep = json.loads(L.report_path(self.gta, D).read_text())
        self.assertEqual(rep["notes"]["by"], "clip-results-coach")
        self.assertIn("What happened: The first 6 seconds", L.report_path(self.gta, D, "md").read_text())
        L.daily(self.cfg, now=self.now)                               # made again by hand: the notes stay
        self.assertIn("What happened: Opens on the answer", (d / f"Clip report {D + timedelta(days=1):%m.%d.%Y}.md")
                      .read_text())

    def test_a_signed_runbook_note_reaches_the_brief(self):
        self.sixty()
        L.daily(self.cfg, now=self.now)
        p = self.tmp / "props.json"
        line = "Start a gaggle clip on the answer, not the reporter's question."
        p.write_text(json.dumps([{"kind": "runbook_note", "words": "07.13 14:00 opened on the answer: 1.6x", "to": line}]))
        made = L.propose_file(self.cfg, self.gta, p, "clip-results-coach")
        self.assertEqual(len(made), 1)
        self.assertNotIn(line, PB.brief(self.gta))                    # proposed is not in force
        L.approve(self.cfg, self.gta, made[0], "Andrew")
        self.assertNotIn(line, PB.brief(self.gta))                    # signed, but the playbook switch is off
        self.switch(self.gta, "learn.playbook", "on")
        self.assertIn(line, PB.brief(self.gta))


class WordsTest(unittest.TestCase):
    def test_transcript_lines(self):
        cues = [(0.2, 1.0, "Though the"), (1.4, 2.0, "drone was"), (10.5, 11.0, "a failure"), (65.0, 66.0, "end"),
                (23.9, 24.5, "  lands   here ")]
        self.assertEqual(L.transcript_lines(cues), ["[0:00] Though the", "[0:01] drone was", "[0:10] a failure",
                                                    "[0:23] lands here", "[1:05] end"])

    def test_the_runbook_line_in_every_choosing_runbook(self):
        for slug, files in (("politics", ("politics-bot", "politics-scout")),
                            ("gaming", ("gaming-bot", "gaming-scout"))):
            for f in files:
                text = (ROOT / "hermes" / slug / "skills" / f / "SKILL.md").read_text()
                self.assertIn(f"`python3 $F learn brief {slug}`", " ".join(text.split()), f)

    def test_the_results_coach_prompt_names_real_commands(self):
        text = (ROOT / "prompts" / "results-coach.md").read_text()
        acts = set(L._parser()._subparsers._group_actions[0].choices)
        for act in re.findall(r"factory\.py learn ([a-z-]+)", text):
            self.assertIn(act, acts)
        self.assertIn("clip-results-coach", text)
        for bad in ("—", "–"):
            self.assertNotIn(bad, text)
        from clipfactory import hermes as H
        self.assertEqual(H.TASK_PROMPT_FILES["clip-results-coach-auto"], "results-coach.md")
        self.assertIn("clip-results-coach-auto", H.TASK_PROMPTS["gaming"])
        self.assertIn("clip-results-coach-auto", H.TASK_PROMPTS["politics"])


if __name__ == "__main__":
    unittest.main()
