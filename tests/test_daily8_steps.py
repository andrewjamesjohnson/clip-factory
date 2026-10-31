import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# THE DAILY 8, steps 3 and 4 (the plan Andrew took 10.28; "build them all", 10.29): THE OPENER RULE (opener.py: the
# first yes wins, a calm start read from the clip's own first 3 s after its cold open), THE SEQUEL on the board (the
# best hit not yet sequelled takes 07:50 as its next part, FIGHT 2 moves to 20:40, never a Part 4), THE KEPT MASTERS
# (masters.py: kept 14 days as clones, restored to the temp folder, pruned) and the VS card's PART banner. Synthetic
# boards, captions and folders only.

import json
import shutil
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipfactory import config as C      # noqa: E402
from clipfactory import daily8 as D      # noqa: E402
from clipfactory import masters as MS    # noqa: E402
from clipfactory import opener as OP     # noqa: E402
from clipfactory import vscard as VS     # noqa: E402

ACCT = SimpleNamespace(slug="politics", name="Politics", maker="politics")


def srt(path: Path, *lines) -> None:
    def ts(x):
        ms = int(round(x * 1000))
        return f"00:00:{ms // 1000:02d},{ms % 1000:03d}"
    path.write_text("\n\n".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}" for i, (a, b, t) in enumerate(lines, 1)) + "\n")


class OpenerRule(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.clip = self.tmp / "01-clip.mp4"
        self.clip.write_bytes(b"x")
        self.slot = {"slot": "11:50", "slug": "clip", "format": "sitdown", "clip": {"path": str(self.clip)}}
        for p in (mock.patch.object(OP, "cold_record", return_value=({"skip": "no real pause"}, "")),
                  mock.patch.object(OP, "_slate_clip", return_value={})):
            p.start()
            self.addCleanup(p.stop)

    def test_strong_lines(self):
        self.assertIn("question", OP.strong("Will you resign?"))
        self.assertIn("shout", OP.strong("Sit down!"))
        self.assertIn("room label", OP.strong("[BOOING]"))
        self.assertIn("heat", OP.strong("That is a lie and you know it"))
        self.assertEqual(OP.strong("The committee will come to order."), "")

    def test_a_calm_start_takes_the_pre_hook(self):
        srt(self.clip.with_suffix(".srt"), (0.3, 2.0, "Good morning, Mr. Chairman."), (4.2, 6.0, "Will you resign?"))
        self.assertEqual(OP.decide(ACCT, self.slot)[0], "prehook")

    def test_a_strong_line_in_the_first_3_s_is_no_calm_start(self):
        srt(self.clip.with_suffix(".srt"), (0.3, 2.0, "Good morning."), (2.5, 3.5, "Will you resign?"))
        self.assertEqual(OP.decide(ACCT, self.slot)[0], "own")

    def test_the_count_starts_after_the_cold_open(self):
        srt(self.clip.with_suffix(".srt"), (0.0, 2.0, "You lied!"), (2.2, 4.0, "Good morning, Mr. Chairman."))
        with mock.patch.object(OP, "cold_record", return_value=({"d": 2.0, "text": "You lied!"}, "")):
            self.assertEqual(OP.decide(ACCT, self.slot)[0], "prehook")
            self.assertEqual(OP.own_start({"d": 2.0}), 2.0)
            self.assertEqual(OP.own_start({"skip": "x"}), 0.0)

    def test_the_first_yes_wins(self):
        srt(self.clip.with_suffix(".srt"), (0.3, 2.0, "Good morning."))
        self.assertEqual(OP.decide(ACCT, dict(self.slot, vs_card={"left": "a"}))[0], "vs")
        with mock.patch.object(OP, "cold_record", return_value=({"d": 2.1, "from": OP.SHOUT_FROM}, "")):
            self.assertEqual(OP.decide(ACCT, self.slot)[0], "shout")
        self.assertIn("FACE-OFF", OP.calm(ACCT, dict(self.slot, format="faceoff"))[1])
        with mock.patch.object(OP, "cold_record", return_value=({"d": 2.0, "text": "Shame on you"}, "")):
            srt(self.clip.with_suffix(".srt"), (0.0, 2.0, "Shame on you"), (2.1, 3.0, "How dare you!"))
            self.assertEqual(OP.decide(ACCT, self.slot)[0], "cold")

    def test_a_fight_in_its_own_words_and_a_shout_the_engine_left_are_not_calm(self):
        srt(self.clip.with_suffix(".srt"), (0.2, 1.5, "Good morning."), (2.0, 4.0, "Did you know?"),
            (5.0, 7.0, "Answer the question."), (8.0, 9.0, "Excuse me, let me finish."), (10.0, 12.0, "Yes or no?"))
        self.assertIn("fight", OP.calm(ACCT, self.slot)[1])
        srt(self.clip.with_suffix(".srt"), (0.2, 1.5, "So, you know, the way they speak."))
        with mock.patch.object(OP, "cold_record", return_value=({"skip": "x / the shout comes at +2.6s anyway"}, "")):
            self.assertEqual(OP.decide(ACCT, self.slot), ("shout", "its shout lands at +2.6s: nothing goes before it"))
        with mock.patch.object(OP, "cold_record", return_value=({"skip": "the shout comes at +7.0s anyway"}, "")):
            self.assertEqual(OP.decide(ACCT, self.slot)[0], "prehook")

    def test_no_captions_is_never_called_calm(self):
        self.assertFalse(OP.calm(ACCT, self.slot)[0])

    def test_the_record_says_what_it_opens_on(self):
        srt(self.clip.with_suffix(".srt"), (0.3, 2.0, "Good morning."))
        s = dict(self.slot, notes=[], pre_hook={"name": "dam-drop", "fallback": "calm"})
        self.assertEqual(OP.record(ACCT, s)["kind"], "prehook")
        s = dict(self.slot, notes=[])
        rec = OP.record(ACCT, s)                     # calm, but no pre-hook went on (the test's control arm)
        self.assertEqual(rec["kind"], "own")
        self.assertIn("control arm", rec["why"])


class Sequel(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.state, True)
        p = mock.patch.object(C, "STATE", self.state)
        p.start()
        self.addCleanup(p.stop)
        (self.state / ACCT.slug).mkdir(parents=True)

    def board(self, day, slots):
        (self.state / ACCT.slug / f"{day}.json").write_text(json.dumps({"slots": slots}))

    def hit(self, clip, day, title="Warren grills Stumpf", left=6.0):
        return {"clip": clip, "day": day, "title": title, "views": {"youtube": 9000},
                "source": {"minutes": 9.0, "left_after": left, "left_before": 0.5, "video": "Senate Banking",
                           "url": "https://youtu.be/x", "work": "/private/tmp/clipwork/w", "job": "Job"}}

    LANES = [{"slot": "06:30", "lane": "fight1", "name": "FIGHT 1", "what": "", "pick": {"title": "a"},
              "backup": None, "flipped": False},
             {"slot": "07:50", "lane": "fight2", "name": "FIGHT 2", "what": "", "pick": {"title": "b"},
              "backup": {"title": "c"}, "flipped": False},
             {"slot": "20:40", "lane": "outburst", "name": "OUTBURST", "what": "", "pick": {"title": "d"},
              "backup": None, "flipped": False}]

    def test_the_best_hit_takes_0750_and_fight_2_moves_to_2040(self):
        self.board("2026-10-29", {"06:30": {"slug": "warren-stumpf"}})
        plan = D.sequel_plan(ACCT, [self.hit("warren-stumpf", "2026-10-29")], "2026-10-31")
        self.assertEqual((plan["part"], plan["of"], plan["title"]), (2, "warren-stumpf", "Warren grills Stumpf | Part 2"))
        self.assertIn("same video", plan["where"])
        out = {ln["slot"]: ln for ln in D.apply_sequel([dict(x) for x in self.LANES], plan)}
        self.assertEqual(out["07:50"]["name"], "PART 2")
        self.assertEqual(out["07:50"]["pick"]["sequel"], {"part": 2, "of": "warren-stumpf"})
        self.assertEqual(out["07:50"]["backup"], {"title": "b"})
        self.assertEqual(out["20:40"]["pick"], {"title": "b"})       # FIGHT 2, moved
        self.assertIn("moved for PART 2", out["20:40"]["name"])

    def test_no_room_left_names_what_came_next(self):
        plan = D.sequel_plan(ACCT, [self.hit("x", "2026-10-29", left=0.2)], "2026-10-31")
        self.assertIn("DAYS LATER", plan["where"])

    def test_a_hit_sequelled_already_or_a_part_3_gets_none(self):
        self.board("2026-10-30", {"07:50": {"slug": "x-2", "sequel": {"part": 2, "of": "x"}}})
        self.assertIsNone(D.sequel_plan(ACCT, [self.hit("x", "2026-10-29")], "2026-10-31"))
        self.board("2026-10-29", {"07:50": {"slug": "y", "sequel": {"part": 3, "of": "y0"}}})
        self.assertIsNone(D.sequel_plan(ACCT, [self.hit("y", "2026-10-29")], "2026-10-31"))
        self.board("2026-10-28", {"07:50": {"slug": "z", "sequel": {"part": 2, "of": "z0"}}})
        self.assertEqual(D.sequel_plan(ACCT, [self.hit("z", "2026-10-28")], "2026-10-31")["part"], 3)

    def test_no_plan_leaves_the_board_alone(self):
        lanes = [dict(x) for x in self.LANES]
        self.assertEqual(D.apply_sequel(lanes, None), lanes)


class KeptMasters(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state, self.clipwork = self.tmp / "state", self.tmp / "clipwork"
        for p in (mock.patch.object(C, "STATE", self.state), mock.patch.object(MS, "CLIPWORK", self.clipwork)):
            p.start()
            self.addCleanup(p.stop)
        self.work = self.clipwork / "senate-warren-stumpf"
        (self.work / "tmp").mkdir(parents=True)
        for f in ("master.mp4", "transcript.srt", "project.json", "slate.json"):
            (self.work / f).write_text(f)
        (self.work / "tmp" / "render.mp4").write_text("no")
        p = mock.patch.object(MS, "work_of", return_value=self.work)
        p.start()
        self.addCleanup(p.stop)

    def test_kept_restored_and_pruned(self):
        t = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
        dst, why = MS.keep(ACCT, {"slug": "warren-stumpf"}, now=t)
        self.assertEqual(why, "")
        self.assertTrue((dst / "master.mp4").is_file())
        self.assertFalse((dst / "tmp").exists())
        self.assertEqual(json.loads((dst / "keep.json").read_text())["slugs"], ["warren-stumpf"])
        shutil.rmtree(self.work)                                      # a restart empties /tmp
        got, why = MS.restore(ACCT, "senate-warren-stumpf")
        self.assertEqual((self.clipwork / "senate-warren-stumpf" / "master.mp4").read_text(), "master.mp4")
        self.assertEqual(MS.prune(ACCT, now=t + timedelta(days=13)), [])
        self.assertEqual(MS.prune(ACCT, now=t + timedelta(days=14, seconds=1)), ["senate-warren-stumpf"])
        self.assertIsNone(MS.restore(ACCT, "senate-warren-stumpf")[0])

    def test_after_collect_never_raises(self):
        with mock.patch.object(MS, "keep", side_effect=OSError("disk")):
            self.assertIn("not kept", MS.after_collect(ACCT, {}))


class PartBanner(unittest.TestCase):
    def test_the_card_takes_its_part_from_the_slot(self):
        self.assertEqual(VS.card_part({"sequel": {"part": 2, "of": "w"}}), 2)
        self.assertEqual(VS.card_part({"sequel": {"part": "3"}}), 3)
        self.assertIsNone(VS.card_part({"sequel": {"part": 1}}))
        self.assertIsNone(VS.card_part({}))
        self.assertIsNone(VS.card_part({"sequel": "x"}))

    def test_the_banner_is_drawn_in_the_cards_own_lettering(self):
        img = VS._hot("PART 2", VS.PART_PX, VS.FIRE, rim=(255, 220, 120))
        self.assertGreater(img.width, 300)
        self.assertLess(img.width, VS.W)


if __name__ == "__main__":
    unittest.main()
