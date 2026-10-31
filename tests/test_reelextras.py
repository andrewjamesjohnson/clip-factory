import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The Instagram reel extras (reelextras.py, 2026-10-20): the poll's options are checked (two to four, 25 characters
# each, 200 together as Instagram reads them), the reel's container takes the poll and the trial flag only while the
# switch is on and the clip was signed with them (extras.ig_form), a trial is only an experiment's signed arm, and in
# practice the Log gets "would add the poll Yes / No" once per reel, which the go-live checklist counts. No network.

import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import Clock, cfg_in_tmp, serve, slot_posted, slot_ready  # noqa: E402

from clipfactory import board as B              # noqa: E402
from clipfactory import extras as EX            # noqa: E402
from clipfactory import firstcomment as FC      # noqa: E402
from clipfactory import outward as OW           # noqa: E402
from clipfactory import reelextras as RX        # noqa: E402
from clipfactory import store                   # noqa: E402
from clipfactory import switches as SW          # noqa: E402

DAY = date(2031, 7, 8)
K = "10:30"
IG = "instagram_reels"


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        self.clock = Clock(self.cfg.slot_time(DAY, "10:20")).patch(self, self.cfg)
        self.addCleanup(SW._SEEN.clear)

    def modes(self, **modes) -> None:
        p = SW.file_path(self.gta)
        raw = store.read(p, {}) or {}
        raw.setdefault("modes", {}).update({k.replace("__", "."): v for k, v in modes.items()})
        store.write(p, raw)
        SW._SEEN.clear()

    def experiment(self, signed=True, state="running", arms=("A", "B")) -> None:
        sig = {"by": "Andrew", "at": "2031-07-01T12:00:00+00:00"} if signed else None
        store.write(store.path(self.gta.slug, "learn", "experiments.json"),
                    {"e-0003": {"question": "Do trial reels find new people?", "arms": list(arms),
                                "assign": "trial_reel", "state": state, "signed": sig}})

    def slot(self) -> dict:
        return B.read(self.gta, DAY)["slots"][K]


class OptionsTest(Base):
    def test_the_options_are_checked(self):
        self.assertEqual(RX.check_options([" Yes ", "No", ""]), ["Yes", "No"])
        self.assertEqual(RX.parse_options("Yes | No | Maybe"), ["Yes", "No", "Maybe"])
        for bad, words in ((["Yes"], "2 to 4"), (["a", "b", "c", "d", "e"], "2 to 4"), (["Yes", "x" * 26], "1 to 25"),
                           (["Yes", "yes"], "the same"), (["Yes \u2014 sure", "No"], "dashes"),
                           (["é" * 25, "è" * 25, "ê" * 25, "ë" * 25], "200"), ("Yes|No", "list")):
            with self.assertRaises(SystemExit, msg=repr(bad)) as e:
                RX.check_options(bad)
            self.assertIn(words, str(e.exception))
        self.assertLessEqual(len(EX._poll_json(["x" * 25] * 4)), RX.POLL_CHARS)

    def test_the_card_warns_that_the_caption_is_the_question(self):
        self.modes(poll="practice")
        slot_ready(self, self.gta, DAY, K)
        RX.set_poll(self.cfg, self.gta, DAY, K, ["Yes", "No"], "Andrew")
        s = self.slot()
        q = RX.question(s)
        self.assertTrue(q)
        self.assertLessEqual(len(q), RX.QUESTION_CHARS + 3)
        html = FC.card_part(self.cfg, self.gta, DAY, s, self.clock())
        self.assertIn("Instagram shows your caption as the poll's question: " + RX.esc(q), html)
        self.assertIn('name="poll_a" value="Yes"', html)
        self.assertIn('data-op="extra-poll"', html)
        # the confirm names the options (the box's form keeps it to what is typed when Save is pressed)
        self.assertIn('data-ask="Put this poll on the Instagram reel of Gaming 10:30: Yes / No?', html)
        self.assertIn('data-ask-t="Put this poll on the Instagram reel of Gaming 10:30: {words}?', html)
        self.assertIn('data-w=""', html)
        long = Path(s["copy"][IG]["path"])
        long.write_text("Words " * 40)
        self.assertTrue(RX.question({"copy": {IG: {"path": str(long)}}}).endswith("..."))

    def test_the_poll_op_is_a_page_click_on_a_ready_clip(self):
        self.modes(poll="on")
        slot_ready(self, self.gta, DAY, K)
        get, post = serve(self, self.cfg)
        body = {"op": "extra-poll", "account": "gaming", "date": DAY.isoformat(), "slots": [K],
                "fields": {"poll_a": "Yes", "poll_b": "No", "poll_c": "", "poll_d": "", "part": "poll"}}
        code, got = post(body, page=False)
        self.assertEqual(code, 400)
        code, got = post(body)
        self.assertEqual((code, got["ok"]), (200, True), got)
        self.assertEqual(RX.options(self.slot()), ["Yes", "No"])
        code, got = post(dict(body, fields={"poll_a": "Only one"}))
        self.assertEqual(code, 400)
        self.assertIn("2 to 4 options", got["message"])
        code, got = post(dict(body, op="extra-clear", fields={"part": "poll"}))
        self.assertEqual(code, 200)
        self.assertEqual(RX.options(self.slot()), [])


class FormTest(Base):
    def signed_with(self, poll=True, trial=False) -> dict:
        slot_ready(self, self.gta, DAY, K)
        if poll:
            RX.set_poll(self.cfg, self.gta, DAY, K, ["Yes", "No"], "Andrew")
        if trial:
            self.experiment()
            RX.set_trial(self.cfg, self.gta, DAY, K, "e-0003", "B", "Andrew")
        return slot_posted(self, self.gta, DAY, K)[K]

    def test_ig_form_only_when_on_and_signed(self):
        s = self.signed_with(poll=True, trial=True)
        self.assertEqual(EX.ig_form(s, self.gta), {})                      # off
        self.modes(poll="practice", trial_reels="practice")
        self.assertEqual(EX.ig_form(s, self.gta), {})                      # practice: the reel goes as always
        self.modes(poll="on")
        self.assertEqual(EX.ig_form(s, self.gta), {"poll_attachment": '{"option_a":"Yes","option_b":"No"}'})
        self.modes(trial_reels="on")
        self.assertEqual(json.loads(EX.ig_form(s, self.gta)["trial_params"]), {"graduation_strategy": "SS_PERFORMANCE"})
        p = Path(s["extras"]["path"])
        p.write_text(p.read_text().replace('"No"', '"Nope"'))             # edited after signing
        self.assertEqual(EX.ig_form(s, self.gta), {})

    def test_a_trial_is_only_a_signed_experiments_arm(self):
        slot_ready(self, self.gta, DAY, K)
        with self.assertRaises(SystemExit) as e:
            RX.set_trial(self.cfg, self.gta, DAY, K, "e-0003", "B", "Andrew")
        self.assertIn("no experiment", str(e.exception))
        self.experiment(signed=False)
        with self.assertRaises(SystemExit) as e:
            RX.set_trial(self.cfg, self.gta, DAY, K, "e-0003", "B", "Andrew")
        self.assertIn("not signed", str(e.exception))
        self.experiment(arms=("A",))
        with self.assertRaises(SystemExit) as e:
            RX.set_trial(self.cfg, self.gta, DAY, K, "e-0003", "B", "Andrew")
        self.assertIn("no arm 'B'", str(e.exception))
        self.experiment(state="concluded")
        with self.assertRaises(SystemExit):
            RX.set_trial(self.cfg, self.gta, DAY, K, "e-0003", "B", "Andrew")
        self.experiment()
        with self.assertRaises(SystemExit):
            RX.set_trial(self.cfg, self.gta, DAY, K, "e-0003", "B", "Andrew", graduation="SOON")
        RX.set_trial(self.cfg, self.gta, DAY, K, "e-0003", "B", "Andrew", graduation="MANUAL")
        data = EX.read(self.slot())
        self.assertEqual(data["trial"], {IG: {"graduation_strategy": "MANUAL", "experiment": "e-0003", "arm": "B"}})
        self.assertEqual(data["experiment"], {"e-0003": "B"})
        RX.clear(self.cfg, self.gta, DAY, K, "trial", "Andrew")
        self.assertNotIn("extras", self.slot())

    def test_practice_writes_one_log_row_per_reel(self):
        s = self.signed_with(poll=True, trial=True)
        rec = s["outlets"][IG]
        self.assertIsNone(FC.on_posted(self.cfg, self.gta, DAY, s, IG, rec, self.clock()))   # all off: nothing
        self.assertFalse((self.tmp / "state" / "gaming" / "outward").exists())
        for key in ("poll", "trial_reels"):
            SW.set_mode(self.cfg, self.gta, key, "practice", "Andrew", now=self.clock())
        got = FC.on_posted(self.cfg, self.gta, DAY, self.slot(), IG, rec, self.clock())
        self.assertIn("poll practice: would add Yes / No", got)
        rows = list(OW.rows(self.cfg, self.gta, kinds=("poll", "trial")))
        self.assertEqual([(r["kind"], r["mode"], r["state"], r["text"]) for r in rows],
                         [("poll", "practice", "practice", "would add the poll Yes / No"),
                          ("trial", "practice", "practice",
                           "would post as a trial reel (experiment e-0003, arm B, SS_PERFORMANCE)")])
        self.assertEqual(rows[0]["outlet"], IG)
        self.assertEqual(rows[0]["target"]["post_id"], rec["post_id"])
        FC.on_posted(self.cfg, self.gta, DAY, self.slot(), IG, rec, self.clock())
        self.assertEqual(len(list(OW.rows(self.cfg, self.gta, kinds=("poll", "trial")))), 2)   # once per reel
        first = SW.checklist(self.cfg, self.gta, "poll", self.clock())[0][1]
        self.assertIn("1 rows", first)
        html = FC.card_part(self.cfg, self.gta, DAY, self.slot(), self.clock())
        self.assertIn("practice: the reel goes without it", html)
        self.assertIn("experiment e-0003, arm B, SS_PERFORMANCE", html)

    def test_on_notes_what_the_reel_went_with(self):
        s = self.signed_with(poll=True)
        self.modes(poll="on")
        got = FC.on_posted(self.cfg, self.gta, DAY, s, IG, s["outlets"][IG], self.clock())
        self.assertIn("poll: went with Yes / No", got)
        (row,) = list(OW.rows(self.cfg, self.gta, kinds=("poll",)))
        self.assertEqual((row["mode"], row["state"]), ("live", "done"))
        self.assertEqual(RX._marker(self.slot()["outlets"][IG], "poll")["state"], "done")

    def test_the_doctor_says_what_each_waits_for_while_off(self):
        rows = RX.doctor_rows(self.cfg, self.gta, self.clock())
        self.assertEqual([r[0] for r in rows], [None, None])
        self.assertIn("Gaming Instagram polls: off; it waits for a practice week and one verified test Reel",
                      rows[0][1])
        self.assertIn("factory.py social on gaming poll --practice", rows[0][1])
        self.assertIn("Gaming Instagram trial reels: off; it waits for a signed experiment arm (learn.experiments)",
                      rows[1][1])
        self.assertIn("factory.py social on gaming trial_reels --practice", rows[1][1])
        self.modes(poll="practice", trial_reels="on")
        rows = RX.doctor_rows(self.cfg, self.gta, self.clock())
        self.assertEqual([r[0] for r in rows], [None, True])
        self.assertIn("Gaming Instagram polls: practice", rows[0][1])


if __name__ == "__main__":
    unittest.main()
