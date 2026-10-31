"""sweep-gta (2026-10-17 audit): a GTA clip only fills the slot it was cut for (FOLD-4), and a GTA slot is credited to
its clip's own job (GTA-14, GTA-13). Tiny synthetic clips; no skill runs."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-sweepgta-state-")   # before the import

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
from clipfactory import makers as M     # noqa: E402

DAY = date(2030, 1, 9)
COLORS = ["red", "green", "blue", "yellow", "white"]


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def mp4(path: Path, n: int, secs: float = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["nice", "-n", "19", "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c={COLORS[n]}:s=108x192:d={secs}", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                    "-t", str(secs), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
                   check=True)
    return path


def clip_dir(root: Path, hhmm: str, slug: str, n: int) -> Path:
    d = root / f"{hhmm} {slug}"
    mp4(d / f"01-{slug}.mp4", n)
    for p in ("youtube", "tiktok", "facebook", "reels"):
        (d / f"{p}.txt").write_text(f"{slug} words for {p}\n")
    return d


class SweepCollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-sweepgta-"))
        self._state = C.STATE
        C.STATE = self.tmp / "state"
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(self.tmp / "drafts")
        for slug, a in raw["accounts"].items():
            a["account_dir"] = str(self.tmp / a["name"])
            a["skill_dir"] = str(self.tmp / f"skill-{slug}")
            a["delivered"] = [str(self.tmp / (a["name"] + " Run {MM}.{DD}.{YYYY}"))]
            a["seconds"] = {"min": 1, "max": 3}
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        self.cfg = C.load(p)
        self.gta = self.cfg.account("gaming")
        self.run = self.gta.delivered_dirs(DAY)[0]

    def tearDown(self):
        C.STATE = self._state
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- FOLD-4
    def test_a_gta_clip_waits_for_its_own_slot_and_never_hops(self):
        clip_dir(self.run, "0700", "vice-city-took-over-miami-beach", 0)
        K.collect(self.cfg, self.gta, DAY)
        self.assertEqual(B.read(self.gta, DAY)["slots"]["07:00"]["status"], B.READY)
        clip_dir(self.run, "0700", "hey-folks-today-is-friday", 1)  # a hand session's earlier cut, same slot
        lines = K.collect(self.cfg, self.gta, DAY)
        self.assertTrue(any("waits, 07:00 already holds a clip" in ln for ln in lines), lines)
        bd = B.read(self.gta, DAY)
        self.assertEqual(bd["slots"]["07:00"]["slug"], "vice-city-took-over-miami-beach")
        self.assertEqual(bd["slots"]["08:45"]["status"], B.OPEN)    # the hop used to land it here
        # sent back to be made again: the cut that waited is the slot's own, so it fills it
        with B.edit(self.cfg, self.gta, DAY) as b:
            t = b["slots"]["07:00"]
            t["status"] = B.OPEN
            for k in ("clip", "copy", "folder", "slug", "cover"):
                t.pop(k, None)
        K.collect(self.cfg, self.gta, DAY)
        self.assertEqual(B.read(self.gta, DAY)["slots"]["07:00"]["slug"], "hey-folks-today-is-friday")

    def test_a_gta_clip_under_an_old_time_takes_only_an_open_slot(self):
        with B.edit(self.cfg, self.gta, DAY) as b:
            b["slots"]["07:00"]["status"] = B.MAKING                # the worker is cutting it
            b["slots"]["08:45"]["status"] = B.FAILED                # the retry makes it again
            b["slots"]["10:30"]["format"] = "reactions"             # held for the REACTIONS duo
        clip_dir(self.run, "0930", "september-time", 2)          # day_folder.py's old 09:30
        K.collect(self.cfg, self.gta, DAY)
        bd = B.read(self.gta, DAY)
        self.assertEqual([bd["slots"][k]["status"] for k in ("07:00", "08:45", "10:30", "12:15")],
                         [B.MAKING, B.FAILED, B.OPEN, B.READY])
        self.assertEqual(bd["slots"]["12:15"]["slug"], "september-time")

    def test_with_no_open_slot_it_waits(self):
        with B.edit(self.cfg, self.gta, DAY) as b:
            for k, s in b["slots"].items():
                s["status"] = B.FAILED if k < "12:00" else B.MAKING
            b["slots"]["19:15"].update(status=B.OPEN, format="reactions")
        clip_dir(self.run, "1700", "old-time", 3)
        lines = K.collect(self.cfg, self.gta, DAY)
        self.assertTrue(any("1700 old-time: waits, 17:00 is not a slot today and no slot is open" in ln
                            for ln in lines), lines)
        self.assertFalse(any(s.get("slug") == "old-time" for s in B.read(self.gta, DAY)["slots"].values()))

    def test_the_politics_still_moves_a_clip_off_its_own_clock(self):
        led = self.cfg.account("politics")
        run = led.delivered_dirs(DAY)[0]
        d = clip_dir(run, "0700", "first", 0)
        for p in ("tiktok_reach", "instagram"):
            (d / f"{p}.txt").write_text("words\n")
        K.collect(self.cfg, led, DAY)
        d2 = clip_dir(run, "0700", "second", 1)
        for p in ("tiktok_reach", "instagram"):
            (d2 / f"{p}.txt").write_text("words\n")
        K.collect(self.cfg, led, DAY)
        bd = B.read(led, DAY)
        self.assertEqual(bd["slots"]["07:00"]["slug"], "first")
        self.assertEqual(bd["slots"]["08:45"]["slug"], "second")

    # ---------------------------------------------------------------- GTA-14 / GTA-13
    def _job(self, job: str, host, url: str, title: str, run_template: str = "") -> None:
        p = self.gta.skill_dir / "work" / job / "project.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"host": host, "source_url": url, "source_title": title, "source_channel": "chan",
                                 **({"_run_template": run_template} if run_template else {})}))

    def _plan(self, slot: str, job: str, host: str, url: str, fmt: str) -> None:
        with B.edit(self.cfg, self.gta, DAY) as b:
            b["slots"][slot].update(status=B.MAKING, job=job, host=host, source_url=url, format=fmt,
                                    source_title="the plan's title")

    def test_a_slot_is_credited_to_its_clips_own_job(self):
        self._job("factory-x-hazardous", {"name": "Hazardous", "title": "playing GTA 5"},
                  "https://www.youtube.com/watch?v=WT98BmWKNss", "I Tried The Craziest GTA 5 Stunt Challenge")
        sched = self.gta.account_dir / "Publish" / "schedule.json"
        sched.parent.mkdir(parents=True, exist_ok=True)
        sched.write_text(json.dumps({DAY.isoformat(): {"19:15": {"job": "factory-x-hazardous", "slug": "adder-jump",
                                                                  "template": "gameplay-clean"}}}))
        # the plan said MrBoss underlay (10-17's 19:00 slot); the clip is Hazardous gameplay
        self._plan("19:15", "factory-x-mrboss", "MrBossFTW", "https://www.youtube.com/watch?v=OCLJLW0e8jo", "underlay")
        clip_dir(self.run, "1915", "adder-jump", 0)
        K.collect(self.cfg, self.gta, DAY)
        s = B.read(self.gta, DAY)["slots"]["19:15"]
        self.assertEqual(s["status"], B.READY, s.get("notes"))
        self.assertEqual((s["host"], s["format"], s["job"]), ("Hazardous", "gameplay", "factory-x-hazardous"))
        self.assertEqual(s["source_url"], "https://www.youtube.com/watch?v=WT98BmWKNss")
        self.assertEqual(s["source_title"], "I Tried The Craziest GTA 5 Stunt Challenge")
        self.assertTrue(any("credited to the clip's own job factory-x-hazardous" in str(n) for n in s.get("notes", [])))
        self.assertIn("WT98BmWKNss", M.used_sources(self.gta))       # GTA-13: the source counts as cut
        # the label is not part of what is signed
        self.assertEqual(B.content_hash(s), B.content_hash(dict(s, host="someone else", format="pip")))

    def test_the_makers_pack_names_the_job(self):
        self._job("factory-y-speed", "iShowSpeed", "https://www.youtube.com/watch?v=mlguGHEeVO0", "Speed reacts",
                  run_template="underlay")
        pack = M.gta_pack_root(self.gta, DAY)
        clip_dir(pack, "0845", "speed-cannot-believe-it", 1)
        (pack / "0845.pack.json").write_text(json.dumps({"slug": "speed-cannot-believe-it", "job": "factory-y-speed",
                                                        "template": "underlay"}))
        self._plan("08:45", "factory-y-speed", "iShowSpeed", "https://www.youtube.com/watch?v=mlguGHEeVO0", "underlay")
        K.collect(self.cfg, self.gta, DAY, only=["08:45"])
        s = B.read(self.gta, DAY)["slots"]["08:45"]
        self.assertEqual((s["status"], s["host"], s["format"], s["job"]),
                         (B.READY, "iShowSpeed", "underlay", "factory-y-speed"))
        self.assertFalse(any("credited to the clip's own job" in str(n) for n in s.get("notes", [])))  # same job: quiet

    def test_nothing_known_keeps_what_the_maker_wrote(self):
        self._plan("10:30", "factory-z", "TGG", "https://www.youtube.com/watch?v=Ej4S9MxmmOw", "pip")
        clip_dir(self.run, "1030", "no-job-anywhere", 2)
        K.collect(self.cfg, self.gta, DAY)
        s = B.read(self.gta, DAY)["slots"]["10:30"]
        self.assertEqual((s["status"], s["host"], s["format"], s["job"]), (B.READY, "TGG", "pip", "factory-z"))
        self.assertEqual(K.gta_provenance(self.gta, self.tmp / "not a slot folder"), {})

    def test_a_reactions_slot_keeps_what_was_placed(self):
        self._job("reactions-launch-date", "Kai Cenat", "https://www.youtube.com/watch?v=B6biwxo0dMQ", "the duo")
        sched = self.gta.account_dir / "Publish" / "schedule.json"
        sched.parent.mkdir(parents=True, exist_ok=True)
        sched.write_text(json.dumps({DAY.isoformat(): {"19:15": {"job": "reactions-launch-date", "slug": "duo",
                                                                  "template": "reactions"}}}))
        with B.edit(self.cfg, self.gta, DAY) as b:
            b["slots"]["19:15"].update(format="reactions", host="six streamers", source_urls=["u1", "u2"])
        clip_dir(self.run, "1915", "duo", 3)
        K.collect(self.cfg, self.gta, DAY)
        s = B.read(self.gta, DAY)["slots"]["19:15"]
        self.assertEqual((s["status"], s["format"], s["host"]), (B.READY, "reactions", "six streamers"))
        self.assertNotIn("job", s)

    def test_a_reactions_template_never_relabels_the_slot(self):
        self._job("reactions-launch-date", "many", "https://www.youtube.com/watch?v=B6biwxo0dMQ", "the duo")
        sched = self.gta.account_dir / "Publish" / "schedule.json"
        sched.parent.mkdir(parents=True, exist_ok=True)
        sched.write_text(json.dumps({DAY.isoformat(): {"12:15": {"job": "reactions-launch-date", "slug": "duo",
                                                                  "template": "reactions"}}}))
        got = K.gta_provenance(self.gta, self.run / "1215 duo", DAY.isoformat())
        self.assertEqual(got.get("job"), "reactions-launch-date")
        self.assertNotIn("format", got)        # the maker's own lanes only; a REACTIONS slot stays what it is


if __name__ == "__main__":
    unittest.main()
