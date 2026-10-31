"""A clip the GTA maker packed is collected once: the skill's Desktop run folder holds a copy of every clip of the day
under its own slot names, and that copy must never fill a second slot. No skill is run."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-dupes-state-")   # set before clipfactory is imported:
# config.STATE is read once, and a module found before test_factory would otherwise point the run at state/

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from politics_slots import pin_gta  # noqa: E402  the GTA days these tests were written on
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import makers as M     # noqa: E402

DAY = date(2030, 1, 9)
COLORS = ["red", "green", "blue", "yellow", "white"]


def clip_dir(root: Path, hhmm: str, slug: str, n: int) -> Path:
    d = root / f"{hhmm} {slug}"
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={COLORS[n]}:s=108x192:d=2",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(d / f"01-{slug}.mp4")], check=True)
    for p in ("youtube", "tiktok", "facebook", "reels"):
        (d / f"{p}.txt").write_text(f"{slug} words for {p}\n")
    return d


class CollectDupesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-dupes-"))
        self._state = C.STATE
        C.STATE = self.tmp / "state"
        raw = pin_gta(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(self.tmp / "drafts")
        a = raw["accounts"]["gaming"]
        a["account_dir"] = str(self.tmp / "Gaming")
        a["delivered"] = [str(self.tmp / "Gaming Run {MM}.{DD}.{YYYY}")]
        a["seconds"] = {"min": 1, "max": 3}
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        self.cfg = C.load(p)
        self.acct = self.cfg.account("gaming")
        self.run_folder = self.acct.delivered_dirs(DAY)[0]
        self.pack = M.gta_pack_root(self.acct, DAY)

    def tearDown(self):
        C.STATE = self._state

    def test_a_run_folder_copy_never_fills_the_slot_being_made(self):
        clip_dir(self.pack, "0845", "vice-city-took-over-miami-beach", 0)
        shutil.copytree(self.pack / "0845 vice-city-took-over-miami-beach",
                        self.run_folder / "0845 vice-city-took-over-miami-beach")
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["10:30"]["status"] = B.MAKING
            for k in ("07:00", "12:15", "14:00", "15:45", "17:30", "19:15", "21:00", "23:00"):
                bd["slots"][k]["status"] = B.MAKING   # no open slot: the fallback would land on 10:30
        K.collect(self.cfg, self.acct, DAY, only=["08:45"])
        K.collect(self.cfg, self.acct, DAY, only=["10:30"])
        bd = B.read(self.acct, DAY)
        self.assertEqual(bd["slots"]["08:45"]["status"], B.READY)
        self.assertEqual(bd["slots"]["10:30"]["status"], B.MAKING)
        lines = K.collect(self.cfg, self.acct, DAY)   # the end-of-night collect, no slot named
        self.assertEqual(lines, [])
        self.assertEqual(B.read(self.acct, DAY)["slots"]["10:30"]["status"], B.MAKING)

    def test_a_night_of_three_picks_fills_each_slot_with_its_own_clip(self):
        inbox = self.acct.account_dir / "Publish" / "inbox.json"
        inbox.parent.mkdir(parents=True)
        inbox.write_text(json.dumps({"items": [{"url": "https://www.youtube.com/watch?v=v1", "status": "new",
                                                "kind": "breakdown", "score": 9, "channel": "tgg", "title": "upload"}]}))
        with B.edit(self.cfg, self.acct, DAY):   # make() plans the board first
            pass
        packed = []

        def fake_pack(acct, day, slot, log, env):
            # the factory's pack, then run.py --deliver's rebuild of the run folder: every clip so far, under the
            # skill's own slot names (its seven-slot board puts them all at 07:00 here)
            n = len(packed)
            slug = f"clip-{n}"
            clip_dir(self.pack, slot.replace(":", ""), slug, n)
            packed.append((slot, slug))
            shutil.rmtree(self.run_folder, ignore_errors=True)
            for s, g in packed:
                shutil.copytree(self.pack / f"{s.replace(':', '')} {g}", self.run_folder / f"0700 {g}")
            return f"  {slot}: packed"

        ok = lambda cmd, log, cwd=None, env=None, timeout=0: subprocess.CompletedProcess(cmd, 0)
        with mock.patch.object(M, "run_logged", ok), mock.patch.object(M, "pack_gta_slot", fake_pack):
            M.make_gta(self.cfg, self.acct, DAY)
        K.collect(self.cfg, self.acct, DAY)   # what `make` runs after the maker
        bd = B.read(self.acct, DAY)
        got = {k: s.get("slug") for k, s in bd["slots"].items() if s["status"] == B.READY}
        self.assertEqual(got, {"07:00": "clip-0", "08:45": "clip-1", "10:30": "clip-2"})
        self.assertEqual(sum(s["status"] == B.OPEN for s in bd["slots"].values()), 7)


if __name__ == "__main__":
    unittest.main()
