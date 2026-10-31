"""CAST TO CONFIRM (the owner, 2026-10-17: "For a one-speaker clip whose title names the person, let the bot cut it
the same night and I'll confirm the cast at the 05:30 sign-off"): the engine's cast-check.json beside a clip is
collected onto the slot, said on the card, and the owner's Approve writes `verified` into the job's cast.json.
Throwaway folders; no network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
# SET BEFORE clipfactory IS IMPORTED. config.STATE is fixed at import, and test files load alphabetically: this one
# sorts first, so importing clipfactory with no CLIP_FACTORY_STATE pinned the LIVE state folder for every test after
# it (2026-10-17: test_factory wrote 2030 boards and stats into ~/clip-factory/state). setUpClass points C.STATE at
# its own folder as well; this keeps every later file's import off the real one.
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-cast-state-")

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

DAY = date(2031, 5, 6)
TITLE = "You can't 'earn' a billion dollars. | Alexandria Ocasio-Cortez"


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class CastCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-cast-"))
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
        for d in ("state", "drafts", "delivered", "Jobs", "elsewhere"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.acct = self.cfg.account("politics")
        self.cast_file = self.write_cast(self.tmp / "Jobs" / "aoc-job" / "cast.json")

    def write_cast(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"people": [
            {"id": "aoc", "name": "Alexandria Ocasio-Cortez", "title": "U.S. REPRESENTATIVE, NEW YORK",
             "role": "member", "verified": None,
             "provisional": {"named_as": "Alexandria Ocasio-Cortez", "source_title": TITLE,
                             "frames": "0:04, 0:41", "by": "politics bot", "at": "2031-05-05"}},
            {"id": "host", "name": "Some Host", "title": "HOST", "role": "staff",
             "verified": "2031-05-05, by eye at 0:10"}]}, indent=1))
        return path

    def deliver(self, hhmm: str, cast_file: Path = None, slug: str = "earn-a-billion") -> Path:
        d = self.acct.delivered_dirs(DAY)[0] / f"{hhmm} {slug}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.main, d / f"01-{slug}.mp4")
        for p in ("youtube", "tiktok_reach", "facebook", "instagram"):
            (d / f"{p}.txt").write_text(f"TITLE\nThe exchange at {hhmm}\n\nCAPTION\nWords for {p}.\n")
        if cast_file is not None:
            (d / "cast-check.json").write_text(json.dumps({
                "rule": "test", "slot": hhmm, "slug": slug, "job": "aoc-job", "cast_file": str(cast_file),
                "source_title": TITLE,
                "people": [{"id": "aoc", "name": "Alexandria Ocasio-Cortez",
                            "title": "U.S. REPRESENTATIVE, NEW YORK", "named_as": "Alexandria Ocasio-Cortez",
                            "frames": "0:04, 0:41", "by": "politics bot", "at": "2031-05-05"}]}))
        return d

    def slot(self, k: str) -> dict:
        return B.read(self.acct, DAY)["slots"][k]

    def card(self) -> dict:
        return R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))["cards"][0]

    def test_1_collected_and_said_on_the_card(self):
        self.deliver("0700", self.cast_file)
        out = K.collect(self.cfg, self.acct, DAY)
        s = self.slot("07:00")
        self.assertEqual(s["status"], B.READY, out)
        self.assertEqual([p["name"] for p in s["cast_check"]["people"]], ["Alexandria Ocasio-Cortez"])
        self.assertTrue((Path(s["folder"]) / "cast-check.json").is_file())
        self.assertIn("CAST TO CONFIRM", " ".join(out) if isinstance(out, list) else str(out))
        bottom = self.card()["bottom"]
        self.assertIn("Cast not checked yet", bottom)
        self.assertIn("Approving it is your cast check", bottom)

    def test_2_approve_writes_verified_once(self):
        self.deliver("0700", self.cast_file)
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        people = {p["id"]: p for p in json.loads(self.cast_file.read_text())["people"]}
        self.assertIn("Andrew, at the 2031-05-06 sign-off in Clip Review", people["aoc"]["verified"])
        self.assertIn("provisional", people["aoc"])                         # the history is kept
        self.assertEqual(people["host"]["verified"], "2031-05-05, by eye at 0:10")   # nobody else touched
        s = self.slot("07:00")
        self.assertEqual(s["cast_check"]["confirmed"]["wrote"], ["Alexandria Ocasio-Cortez"])
        self.assertIn("Cast confirmed at sign-off by Andrew", self.card()["bottom"])
        self.assertEqual(B.verify(s), [])                                    # the signature is the clip and words

    def test_3_never_writes_outside_the_jobs_folders(self):
        stray = self.write_cast(self.tmp / "elsewhere" / "cast.json")
        before = stray.read_text()
        self.deliver("0700", stray)
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertEqual(stray.read_text(), before)
        self.assertIn("write `verified` by hand", " ".join(n.get("what", str(n)) if isinstance(n, dict) else str(n)
                                                           for n in self.slot("07:00").get("notes", [])))

    def test_4_a_clip_without_the_file_carries_nothing(self):
        self.deliver("0700", self.cast_file)
        self.deliver("0845", None, slug="a-verified-exchange")
        K.collect(self.cfg, self.acct, DAY)
        self.assertIn("cast_check", self.slot("07:00"))
        self.assertNotIn("cast_check", self.slot("08:45"))
        cards = {c["id"]: c for c in R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))["cards"]}
        self.assertIn("Cast not checked yet", cards["c-politics-0700"]["bottom"])
        self.assertNotIn("Cast not checked yet", cards["c-politics-0845"]["bottom"])

    def test_5_the_flag_is_not_part_of_the_signature(self):
        self.deliver("0700", self.cast_file)
        K.collect(self.cfg, self.acct, DAY)
        s = self.slot("07:00")
        h = B.content_hash(s)
        s2 = dict(s)
        s2.pop("cast_check")
        self.assertEqual(h, B.content_hash(s2))


if __name__ == "__main__":
    unittest.main()
