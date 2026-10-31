import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# THE DAILY 8 BOARD (clipfactory/daily8.py, 2026-10-28, step 2 of the Daily 8 plan): the eight lanes from the scout's
# finds (fights first and two people before one, NEWS 1 another story than NEWS 2, reuploads and roundups never take a
# slot, the vault where a lane is about history), the news level and its flips, and what a transcript holds (the
# fight's length, the dodges, the paper). Synthetic finds and cues only.

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipfactory import daily8 as D   # noqa: E402


def find(i, title, score, heat=0.0, fmt="", story=0, outlets=1, people=(), looks=(), lane="cut", rights="network"):
    return {"id": f"v{i}", "kind": "video", "title": title, "url": f"https://youtu.be/v{i}", "channel": "Net",
            "score": score, "heat": heat, "fmt": fmt, "format": fmt, "story": story, "story_outlets": outlets,
            "people": list(people), "looks": list(looks), "lane": lane, "rights": rights, "status": "new"}


VAULT = [{"id": "V-HR-01", "section": "HEARINGS", "heat_n": 9, "year": "2016", "moment": "Warren v. Stumpf",
          "names": [], "looks": []},
         {"id": "V-SD-01", "section": "SIT-DOWN", "heat_n": 8, "year": "2020", "moment": "Blitzer v. Pelosi",
          "names": [], "looks": ["faceoff"]},
         {"id": "V-FL-01", "section": "FLOOR", "heat_n": 9, "year": "2009", "moment": "You lie!", "names": [],
          "looks": ["outburst"]},
         {"id": "V-GG-01", "section": "GAGGLE", "heat_n": 7, "year": "2018", "moment": "Acosta's mic", "names": [],
          "looks": []}]


class Lanes(unittest.TestCase):
    def setUp(self):
        self.f = [find(1, "Senator grills CEO who won't answer", 20, 0.7, "hearing", 1, 2, ("a", "b")),
                  find(2, "Governor lashes out at reporters", 30, 0.8, "gaggle", 2, 3, ("c",)),
                  find(3, "Press gaggle aboard Air Force One", 15, 0.1, "gaggle", 3, 2),
                  find(4, "Fed holds rates", 12, 0.0, "briefing", 4, 6),
                  find(5, "Fed chair briefing", 11, 0.0, "briefing", 4, 6),
                  find(6, "Host presses senator on war powers", 9, 0.5, "interview", 5, 1, ("d", "e")),
                  find(7, "AM Update: fight over budget, rates, polls", 40, 0.9, "podcast", 6, 1)]
        self.sts = D.stories(self.f)

    def lanes(self, level="normal"):
        return {ln["lane"]: ln for ln in D.lanes(self.f, self.sts, VAULT, level)}

    def test_two_people_fight_first(self):
        ln = self.lanes()
        self.assertEqual(ln["fight1"]["pick"]["id"], "v1")         # two named people, though v2 scores higher
        self.assertEqual(ln["fight2"]["pick"]["id"], "v2")

    def test_news2_is_the_top_story_and_news1_another(self):
        ln = self.lanes()
        self.assertEqual(ln["news2"]["pick"]["id"], "v4")          # the story on the most outlets
        self.assertEqual(ln["news1"]["pick"]["id"], "v3")          # a gaggle, not the Fed again

    def test_a_roundup_never_takes_a_fight_or_sitdown(self):
        ln = self.lanes()
        self.assertNotIn("v7", {ln[k]["pick"]["id"] for k in ("fight1", "fight2", "sitdown") if ln[k]["pick"]})
        self.assertTrue(D.roundup("Midterms, Raids, Netanyahu Election Fight: AM Update 10/28"))
        self.assertFalse(D.roundup("Booker SLAMS Trump's election fraud claims"))

    def test_history_lanes_come_from_the_vault(self):
        ln = self.lanes()
        self.assertEqual(ln["flashback"]["pick"]["from"], "vault")
        self.assertEqual(ln["hearing"]["pick"]["id"], "V-HR-01")    # today's one hearing went to FIGHT 1
        self.assertEqual(ln["outburst"]["pick"]["id"], "V-FL-01")

    def test_a_big_day_flips_outburst_and_flashback_never_the_fights(self):
        ln = self.lanes("big")
        self.assertTrue(ln["outburst"]["flipped"] and ln["flashback"]["flipped"])
        self.assertFalse(ln["fight1"]["flipped"] or ln["fight2"]["flipped"] or ln["hearing"]["flipped"])
        huge = self.lanes("huge")
        self.assertTrue(huge["hearing"]["flipped"] and huge["sitdown"]["flipped"])

    def test_nothing_is_used_twice(self):
        ids = [p["id"] for ln in D.lanes(self.f, self.sts, VAULT, "normal") for p in (ln["pick"], ln["backup"]) if p]
        self.assertEqual(len(ids), len(set(ids)))


class Level(unittest.TestCase):
    def st(self, *outlets):
        return [{"story": i, "outlets": o, "finds": [{"title": f"s{i}"}]} for i, o in enumerate(outlets)]

    def test_levels(self):
        self.assertEqual(D.news_level(self.st(6, 5))[0], "normal")
        self.assertEqual(D.news_level(self.st(9, 4))[0], "big")
        self.assertEqual(D.news_level(self.st(9, 8))[0], "normal")           # not far enough ahead
        self.assertEqual(D.news_level(self.st(14, 5))[0], "huge")


class Candidates(unittest.TestCase):
    def test_reuploads_and_uncleared_never_take_a_slot(self):
        board = {"finds": {"a": find(1, "x", 5), "b": find(2, "y", 9, lane="find original", rights="reupload"),
                           "c": find(3, "z", 7, rights="unknown")}}
        self.assertEqual([f["id"] for f in D.candidates(board, None)], ["v1"])


class Moments(unittest.TestCase):
    def cues(self):
        cs, t = [], 0.0
        for k in range(30):                                         # four minutes of a member pressing a witness
            cs.append((t, t + 3, "Will you resign as chief executive of this bank?" if k % 3 == 0 else
                       "Did you return the money you made from this scam?" if k % 3 == 1 else "I, I think that..."))
            t += 8
        cs.append((t, t + 3, "You wrote in your 2014 letter that cross-selling was the strategy."))
        cs.append((t + 5, t + 8, "I didn't hear what you said."))
        return cs

    def test_a_long_fight_is_rounds(self):
        m = D.moments(self.cues())
        self.assertTrue(m["fights"] and m["fights"][0]["minutes"] >= 3.0)
        self.assertTrue(m["rounds"])

    def test_the_same_question_again_is_a_dodge(self):
        self.assertGreaterEqual(len(D.moments(self.cues())["dodges"]), 3)

    def test_their_own_words_are_the_page_but_a_plain_you_said_is_not(self):
        paper = D.moments(self.cues())["paper"]
        self.assertEqual(len(paper), 1)
        self.assertIn("You wrote", paper[0]["line"])

    def test_srt_cues(self):
        srt = "1\n00:00:01,500 --> 00:00:03,000\nYes or no?\n\n2\n00:01:02,000 --> 00:01:04,250\nNo.\n"
        self.assertEqual(D.cues(srt), [(1.5, 3.0, "Yes or no?"), (62.0, 64.25, "No.")])


if __name__ == "__main__":
    unittest.main()
