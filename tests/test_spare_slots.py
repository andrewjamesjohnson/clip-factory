"""The Politics's spare slots, 16 to 20 (2026-10-21, Politics New Looks 10.21/PLAN.md section 3).

Andrew: "The spare post times are OK." Five spare times beside the fifteen: every new board carries them marked
"spare"; collect and the bench fill one only once every base slot holds a clip; an empty one is never missing;
`slots` and `daily_target` stay fifteen (tests/politics_slots.py and 497aad6 are untouched).

Since 2026-10-28 the live Politics is THE DAILY 8 (Andrew: "Lock it. Start step 1: 8 a day"): the spare-slot code is
tested on its own copy of the fifteen (FIFTEEN below), and test_1 pins the live eight.
"""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from politics_slots import pin_gta  # noqa: E402  Gaming as the other GTA tests have it (no dated rule)

TMP = Path(tempfile.mkdtemp(prefix="clipfactory-spare-"))
os.environ["CLIP_FACTORY_STATE"] = str(TMP / "state")

from clipfactory import direct as _D     # noqa: E402
from clipfactory import outlets as _O    # noqa: E402
_D.TOKENS = _O.TOKENS = TMP / "no-keys"

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import review as R     # noqa: E402
from clipfactory import shift as SH     # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bench import Base as _BenchBase, DAY as BENCH_DAY, ENGINE   # noqa: E402  (fixtures only: Base has no tests)

DAY = date(2030, 1, 3)
SPARES = ["08:20", "11:50", "16:45", "20:15", "22:30"]
FIFTEEN = ["06:30", "07:50", "08:55", "10:00", "11:20", "12:25", "13:35", "14:50", "15:55", "17:05", "18:20",
           "19:25", "20:40", "21:50", "23:10"]   # the live Politics 2026-10-21 to 10-27, which these tests were written on


def pin_fifteen(raw: dict) -> None:
    """The Politics of the spare-slot days: the fifteen and the five spares (the live config has neither since 10-28)."""
    led = raw["accounts"]["politics"]
    led["slots"], led["daily_target"], led["spare_slots"] = list(FIFTEEN), len(FIFTEEN), list(SPARES)


def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


def deliver(root: Path, hhmm: str, slug: str, seconds: float = 2.0) -> Path:
    d = root / f"{hhmm} {slug}"
    d.mkdir(parents=True, exist_ok=True)
    clip(d / f"01-{slug}.mp4", seconds)
    for plat in ("youtube", "tiktok", "facebook", "reels"):
        (d / f"{plat}.txt").write_text(f"{slug} title\n\n{slug} words for {plat}\n")
    return d


class SpareSlots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(TMP / "drafts")
        raw.pop("inbox_auto_sign", None)
        pin_fifteen(raw)   # spares off live since 2026-10-27, eight slots since 10-28; the code still works
        pin_gta(raw)       # Gaming's dated rule (2026-10-29) is pinned in every GTA test: in one run they share
        #                    a state folder, and a 10-slot board of DAY from another test is read here
        for a in raw["accounts"].values():
            a.pop("signed_by_hand_stays", None)
            a["delivered"] = [str(TMP / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 1, "max": 3}
        cls.cfg_path = TMP / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.cfg = C.load(cls.cfg_path)
        cls.acct = cls.cfg.account("politics")
        cls.dl = cls.acct.delivered_dirs(DAY)[0]

    def test_1_the_fifteen_do_not_move(self):
        live = json.loads((ROOT / "config" / "accounts.json").read_text())["accounts"]["politics"]
        self.assertEqual(len(live["slots"]), 8)      # THE DAILY 8 since 2026-10-28 (fifteen 10-21 to 10-27)
        self.assertEqual(live["daily_target"], 8)
        self.assertEqual(live["spare_slots"], [])   # 2026-10-27, Andrew: "Only want to make 15"; no spares since
        self.assertEqual(self.acct.spare_slots, SPARES)
        self.assertEqual(len(self.acct.slots_for(DAY)), 15)
        self.assertFalse(set(SPARES) & set(self.acct.slots))

    def test_2_spares_are_twenty_minutes_from_every_account(self):
        mins = [int(s[:2]) * 60 + int(s[3:]) for a in self.cfg.accounts.values() if a.active for s in a.slots_for(DAY)]
        for s in SPARES:
            t = int(s[:2]) * 60 + int(s[3:])
            self.assertGreaterEqual(min(abs(t - m) for m in mins), 20, s)

    def test_3_board_carries_fifteen_and_five_spare(self):
        with B.edit(self.cfg, self.acct, DAY) as bd:
            pass
        self.assertEqual(len(bd["slots"]), 20)
        self.assertEqual(sorted(k for k, s in bd["slots"].items() if B.is_spare(s)), SPARES)
        self.assertEqual(list(bd["slots"]), sorted(bd["slots"]))          # spares sit among the fifteen in time order
        self.assertEqual(len(B.base_slots(bd)), 15)
        self.assertFalse(B.spares_may_fill(bd))

    def test_4_collect_fills_a_base_slot_first(self):
        # a clip on the skill's own clock (no such slot) takes the first open BASE slot, never the 08:20 spare
        deliver(self.dl, "0700", "off-clock")
        # a clip cut for a spare time while base slots are open goes to a base slot too
        deliver(self.dl, "0820", "cut-for-a-spare")
        lines = K.collect(self.cfg, self.acct, DAY)
        bd = B.read(self.acct, DAY)
        self.assertEqual(bd["slots"]["08:20"]["status"], B.OPEN, lines)
        self.assertNotIn("clip", bd["slots"]["08:20"])
        filled = [k for k, s in bd["slots"].items() if s["status"] == B.READY]
        self.assertTrue(filled and not set(filled) & set(SPARES), (filled, lines))

    def test_5_a_spare_fills_once_every_base_slot_holds_a_clip(self):
        with B.edit(self.cfg, self.acct, DAY) as bd:
            for s in B.base_slots(bd):
                if s["status"] in B.FILLABLE:
                    s["status"] = B.READY             # stand-ins: every base slot holds a clip
        self.assertTrue(B.spares_may_fill(B.read(self.acct, DAY)))
        deliver(self.dl, "1150", "a-sixteenth")
        lines = K.collect(self.cfg, self.acct, DAY)
        bd = B.read(self.acct, DAY)
        self.assertEqual(bd["slots"]["11:50"]["status"], B.READY, lines)

    def test_6_an_open_spare_is_never_missing(self):
        out = SH.lines(self.cfg, [self.acct], DAY)
        line = next(x for x in out if x.startswith(f"{self.acct.name}:"))
        self.assertIn("of 15 made", line)
        self.assertIn("of 5 spare slots filled", line)

    def test_7_an_account_without_spares_is_unchanged(self):
        gta = self.cfg.account("gaming")
        self.assertEqual(gta.spare_slots, [])
        with B.edit(self.cfg, gta, DAY) as bd:
            pass
        self.assertFalse(any(B.is_spare(s) for s in bd["slots"].values()))
        self.assertEqual(list(bd["slots"]), gta.slots_for(DAY))


DAY2 = date(2030, 1, 4)


class EmptySparesAreNeverMissing(unittest.TestCase):
    """2026-10-22: the 06:30 notice, Clip Review and its calendar count a spare slot only once it holds a clip; an
    empty one (open, being made, failed) is never a slot of the day there and never "not made"."""

    @classmethod
    def setUpClass(cls):
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        root = TMP / "quiet"
        raw["drafts_root"] = str(root / "drafts")
        raw["review_port"] = 0
        pin_fifteen(raw)
        for slug, a in raw["accounts"].items():
            a["account_dir"] = str(root / a["name"])
            a["skill_dir"] = str(root / f"skill-{slug}")
            a["delivered"] = [str(root / "delivered" / slug / "{YYYY}{MM}{DD}")]
        p = root / "accounts.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(raw))
        cls.cfg = C.load(p)
        cls.acct = cls.cfg.account("politics")
        with B.edit(cls.cfg, cls.acct, DAY2) as bd:
            for k in ("06:30", "07:50"):
                bd["slots"][k]["status"] = B.READY
            bd["slots"]["11:50"]["status"] = B.READY      # one spare holds a clip: it counts like any slot
            bd["slots"]["16:45"]["status"] = B.FAILED     # a spare tried and failed: still empty, never missing
        cls.now = cls.cfg.slot_time(DAY2, "06:00")

    def test_8_the_morning_notice(self):
        import argparse
        import contextlib
        import io
        import factory
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            factory.cmd_morning(self.cfg, argparse.Namespace(account="politics", date=DAY2.isoformat(),
                                                             notify=False))
        line = next(ln for ln in buf.getvalue().splitlines() if ln.startswith(f"{self.acct.name}, {DAY2}"))
        self.assertIn("3 of 16 drafts ready", line)        # fifteen base slots and the one spare that holds a clip
        self.assertIn("13 not made (08:55 open", line)
        for k in ("08:20", "16:45", "20:15", "22:30"):
            self.assertNotIn(k, line)

    def test_9_clip_review_and_its_calendar(self):
        sec = R._section(self.cfg, self.acct, DAY2, self.now)
        self.assertIn("3 to review", sec["head"])
        self.assertIn("13 no clip", sec["head"])
        self.assertIn("08:55", sec["empty"])
        for k in ("08:20", "16:45", "20:15", "22:30"):
            self.assertNotIn(k, sec["empty"])
        kinds = R.calendar(self.cfg, self.now, acct="politics")["days"][DAY2.isoformat()]["politics"]
        self.assertEqual(len(kinds), 16)
        self.assertNotIn("problem", kinds)                 # the failed spare is not painted as a problem

    def test_11_the_stats_factory_table(self):
        from clipfactory import statspage as SP
        row = next(r for r in SP._factory(self.cfg, self.cfg.slot_time(DAY2, "23:59"))
                   if r["account"] == "politics" and r["day"] == DAY2.isoformat())
        self.assertEqual(row["slots"], 16)        # the fifteen and the spare that holds a clip, never the empty four

    def test_12_the_learn_morning_report(self):
        from clipfactory import learn as LN
        boards = [(DAY2, B.read(self.acct, DAY2))]
        pub = LN.publishing(self.cfg, self.acct, boards, [], DAY2, self.cfg.slot_time(DAY2, "23:59"))
        self.assertEqual(pub["slots"]["planned"], 16)  # the fifteen and the spare that holds a clip


class BenchKeepsBaseFirst(_BenchBase):
    """2026-10-22: the bench fill, like collect and the inbox, gives a spare slot a clip only once every base slot
    holds one (only matters once the Politics's bench.fill is on)."""

    def test_10_a_bench_clip_takes_a_base_slot_before_an_earlier_spare(self):
        self.raw["accounts"]["politics"]["slots"] = ["09:00", "10:30", "12:15", "14:00", "15:45", "17:30",
                                                           "19:15", "21:00", "23:00"]
        self.raw["accounts"]["politics"]["spare_slots"] = SPARES   # off live since 2026-10-27
        self.reload()
        self.politics = self.cfg.account("politics")
        sc = self.politics.skill_dir / "scripts"         # the day rule's own scripts, as test_bench.PoliticsDayRule
        sc.mkdir(parents=True, exist_ok=True)
        for n in ("rotation.py", "cast.py"):
            (sc / n).write_bytes((ENGINE / n).read_bytes())
        it = self.politics_ready()
        lines = self.fill("politics")
        bd = B.read(self.politics, BENCH_DAY)["slots"]
        self.assertTrue(B.is_spare(bd["08:20"]))
        self.assertNotIn("bench", bd["08:20"], lines)        # the first open slot in time, but a spare
        self.assertEqual(bd["09:00"]["bench"]["id"], it["id"], lines)
        self.assertTrue(any("spare slots 08:20, 11:50, 16:45, 20:15, 22:30: wait" in ln for ln in lines), lines)


if __name__ == "__main__":
    unittest.main()
