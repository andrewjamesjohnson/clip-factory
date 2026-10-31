"""A `verified` a bot wrote goes back to the check at sign-off (Andrew's call 6, 2026-10-17 night; audit LDG-6, CL-7).

The day folder lists such a person in cast-check.json with "by_bot" (viral-clip-engine Publish/day_folder.py, p1b):
collect carries it, the card says the person was marked checked by a bot, and Approve writes Andrew's own line over
the bot's (kept as verified_before). His line must never read as a bot's. Throwaway folders; no network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
# before clipfactory is imported: config.STATE is read once at import
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-botcast-state-")

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import direct as D     # noqa: E402
from clipfactory import outlets as O    # noqa: E402
from clipfactory import review as R     # noqa: E402
from clipfactory import signoff as S    # noqa: E402

DAY = date(2031, 6, 9)
BOT_LINE = ("2026-10-16, checked by Claude from frames at 12253.5s, 12269.9s and 12279.5s ... NEEDS ANDREW'S LOOK "
            "before posting")


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class BotVerifiedCastTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-botcast-"))
        cls.old_state, C.STATE = C.STATE, cls.tmp / "state"
        cls.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        cls.old_roots = S.CAST_ROOTS
        S.CAST_ROOTS = (cls.tmp / "Jobs",)
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(cls.tmp / "drafts")
        for a in raw["accounts"].values():
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 2, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "tiktok": {"mode": "export"}}
            a.pop("signed_by_hand_stays", None)
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.main = cls.tmp / "main.mp4"
        clip(cls.main, 2.5)

    @classmethod
    def tearDownClass(cls):
        C.STATE = cls.old_state
        O.TOKENS, D.TOKENS = cls.old_tokens
        S.CAST_ROOTS = cls.old_roots

    def setUp(self):
        for d in ("state", "drafts", "delivered", "Jobs"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.acct = self.cfg.account("politics")
        self.cast_file = self.tmp / "Jobs" / "SJC FBI Patel 09.15.26" / "cast.json"
        self.cast_file.parent.mkdir(parents=True)
        self.cast_file.write_text(json.dumps({"people": [
            {"id": "patel", "name": "Kash Patel", "title": "DIRECTOR, FBI", "role": "witness", "verified": BOT_LINE},
            {"id": "booker", "name": "Cory Booker", "title": "U.S. SENATOR, NEW JERSEY", "role": "member",
             "verified": "2026-10-16, checked by Claude from frames at 12233.0s"},
            {"id": "grassley", "name": "Chuck Grassley", "title": "CHAIRMAN", "role": "chair",
             "verified": "2026-10-15, by eye at 0:12 of the master"}]}, indent=1))

    def deliver(self, hhmm: str = "0700", slug: str = "patel-yes-or-no") -> Path:
        d = self.acct.delivered_dirs(DAY)[0] / f"{hhmm} {slug}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.main, d / f"01-{slug}.mp4")
        for p in ("youtube", "tiktok_reach", "facebook", "instagram"):
            (d / f"{p}.txt").write_text(f"TITLE\nThe exchange at {hhmm}\n\nCAPTION\nWords for {p}.\n")
        (d / "cast-check.json").write_text(json.dumps({
            "rule": "test", "slot": hhmm, "slug": slug, "job": "SJC FBI Patel 09.15.26",
            "cast_file": str(self.cast_file), "source_title": "Oversight of the FBI",
            "people": [{"id": "booker", "name": "Cory Booker", "title": "U.S. SENATOR, NEW JERSEY", "named_as": "",
                        "by_bot": "2026-10-16, checked by Claude"},
                       {"id": "patel", "name": "Kash Patel", "title": "DIRECTOR, FBI", "named_as": "",
                        "by_bot": BOT_LINE[:200]}]}))
        return d

    def card(self) -> dict:
        return R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))["cards"][0]

    def test_1_card_says_a_bot_marked_it(self):
        self.deliver()
        out = K.collect(self.cfg, self.acct, DAY)
        s = B.read(self.acct, DAY)["slots"]["07:00"]
        self.assertEqual(s["status"], B.READY, out)
        self.assertEqual([p["name"] for p in s["cast_check"]["people"]], ["Cory Booker", "Kash Patel"])
        bottom = self.card()["bottom"]
        self.assertIn("Cast not checked yet", bottom)
        self.assertIn("marked checked by a bot, not by you", bottom)
        self.assertNotIn('named in the title as ""', bottom)
        notes = " ".join(n.get("what", str(n)) if isinstance(n, dict) else str(n) for n in s.get("notes", []))
        self.assertIn("not checked by a person yet", notes)

    def test_2_approve_writes_his_line_over_the_bots(self):
        self.deliver()
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        people = {p["id"]: p for p in json.loads(self.cast_file.read_text())["people"]}
        for pid in ("patel", "booker"):
            self.assertIn("Andrew, at the 2031-06-09 sign-off in Clip Review", people[pid]["verified"])
            self.assertIn("checked by Claude", people[pid]["verified_before"])        # the bot's words are kept
            # his line must not read as a bot's, or the engine would send it to the sign-off again
            self.assertIsNone(S.BOT_VERIFIED.search(people[pid]["verified"]), people[pid]["verified"])
        self.assertEqual(people["grassley"]["verified"], "2026-10-15, by eye at 0:12 of the master")
        self.assertNotIn("verified_before", people["grassley"])
        s = B.read(self.acct, DAY)["slots"]["07:00"]
        self.assertEqual(sorted(s["cast_check"]["confirmed"]["wrote"]), ["Cory Booker", "Kash Patel"])
        self.assertEqual(B.verify(s), [])

    def test_3_a_human_line_is_never_written_over(self):
        data = json.loads(self.cast_file.read_text())
        data["people"][0]["verified"] = "Andrew, 2026-10-18, by eye at 12253.5s"
        self.cast_file.write_text(json.dumps(data))
        self.deliver()
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        people = {p["id"]: p for p in json.loads(self.cast_file.read_text())["people"]}
        self.assertEqual(people["patel"]["verified"], "Andrew, 2026-10-18, by eye at 12253.5s")
        self.assertNotIn("verified_before", people["patel"])
        self.assertIn("verified_before", people["booker"])

    def test_4_the_bot_words(self):
        for said in (BOT_LINE, "checked by hermes", "the bot looked", "Needs Andrew to check"):
            self.assertIsNotNone(S.BOT_VERIFIED.search(said), said)
        for said in ("Andrew, at the 2031-06-09 sign-off in Clip Review: watched the 07:00 clip trump-bot-ban",
                     "2026-08-19, by eye at 5:00:43 of the master"):
            self.assertIsNone(S.BOT_VERIFIED.search(said), said)


if __name__ == "__main__":
    unittest.main()
