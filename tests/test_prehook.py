import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The pre-hook (clipfactory/prehook.py, 2026-10-27): a short unrelated moment in front of a collected clip, arm B of a
# "pre_hook" experiment. The clip's own picture is joined by stream copy (its decoded frames unchanged), the library
# clip used longest ago that fits under every ceiling is picked, a clip too long for any pre-hook sits out of BOTH arms,
# the slot keeps the delivered SHA-256 as its identity, a late platform cut gets the same pre-hook, and a test signed
# ahead of its first board assigns nothing before it. Tiny synthetic videos in a throwaway folder; no network.

import json
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from social_fakes import cfg_in_tmp          # noqa: E402

from clipfactory import board as B           # noqa: E402
from clipfactory import learn as L           # noqa: E402
from clipfactory import prehook as PH        # noqa: E402
from clipfactory import social as SOC        # noqa: E402
from clipfactory import store                # noqa: E402

ODD_SAR = "34304/34587"                      # a delivered Politics clip's pixel shape (10-27): the header must match it
W, H = 216, 384


def make_clip(path: Path, secs: float, colour: str, freq: int, sar: str = ODD_SAR) -> Path:
    """A clip encoded the way the engine encodes (its x264 settings and bt709 tags), with an odd pixel shape."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s={W}x{H}:r=30:d={secs}",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={secs}",
                    "-vf", f"drawbox=x=t*40:y=20:w=30:h=30:color=white:t=fill,setsar=sar={sar}:max=1000000",
                    "-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
                    *PH.X264, *PH.AAC, "-shortest", "-movflags", "+faststart", str(path)], check=True)
    return path


def frames(path: Path) -> list:
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v", "-f", "framemd5", "-"],
                         capture_output=True, text=True, check=True).stdout
    return [line.rsplit(",", 1)[-1].strip() for line in out.splitlines() if line and not line.startswith("#")]


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.acct = self.cfg.account("politics")                 # clips of 1 to 3 seconds in the test config
        self.lib = self.tmp / "library"
        self.lib.mkdir()
        rows = []
        for i, (name, secs, colour) in enumerate((("soccer-cam", 0.8, "red"), ("ice-rock", 1.2, "blue")), 1):
            make_clip(self.lib / f"0{i}-{name}.mp4", secs, colour, 300 + 100 * i, sar="1/1")
            rows.append({"num": f"0{i}", "name": name, "file": f"0{i}-{name}.mp4", "seconds": secs})
        (self.lib / "library.json").write_text(json.dumps(rows))
        patcher = mock.patch.object(PH, "LIBRARY", self.lib)
        patcher.start()
        self.addCleanup(patcher.stop)

    def slot(self, k: str, secs: float = 1.5, cut: float = 0.0, day: date = date(2031, 7, 14)) -> dict:
        folder = self.tmp / "drafts" / f"{k.replace(':', '')} clip-{k[:2]}"
        folder.mkdir(parents=True, exist_ok=True)
        clip = make_clip(folder / "01-clip.mp4", secs, "green", 440)
        s = {"slot": k, "publish_at": self.cfg.slot_time(day, k).isoformat(), "status": "ready", "notes": [],
             "slug": f"clip-{k[:2]}", "folder": str(folder),
             "clip": {"path": str(clip), "sha256": B.sha256_file(clip), "seconds": secs}}
        if cut:
            c = make_clip(folder / "instagram_reels.mp4", cut, "gray", 520)
            s["cuts"] = {"instagram_reels": {"path": str(c), "sha256": B.sha256_file(c), "seconds": cut}}
        return s

    def switch(self, key, value):
        with store.edit(store.path(self.acct.slug, "social", "settings.json"), dict) as d:
            d.setdefault("modes", {})[key] = value


class JoinTest(Base):
    def test_the_clip_is_joined_by_copy_and_its_frames_are_unchanged(self):
        s = self.slot("07:00")
        clip = Path(s["clip"]["path"])
        before, header = frames(clip), PH._extradata(clip)
        how = PH.join(self.lib / "02-ice-rock.mp4", clip)
        self.assertEqual(how, "copy")
        self.assertEqual(PH._extradata(clip), header)                      # the stream header is the clip's own
        after = frames(clip)
        self.assertEqual(after[-len(before):], before)                      # every frame of the clip, bit for bit
        self.assertEqual(len(after) - len(before), 36)                      # 1.2s of pre-hook at 30 fps
        self.assertAlmostEqual(PH._seconds(clip), 2.7, delta=0.06)


class PickTest(Base):
    def test_rotation_and_ceilings(self):
        lib = PH.library()
        files = [("clip", Path("x"), 1.5, 3.0)]
        self.assertEqual(PH.pick(self.acct, files, lib)["name"], "soccer-cam")    # never used: library order
        with store.edit(PH.state_path(self.acct), dict) as st:
            st["used"] = {"soccer-cam": "2031-07-14T07:00:00+00:00"}
        self.assertEqual(PH.pick(self.acct, files, lib)["name"], "ice-rock")      # the one used longest ago
        with store.edit(PH.state_path(self.acct), dict) as st:
            st["used"]["ice-rock"] = "2031-07-14T08:00:00+00:00"
        self.assertEqual(PH.pick(self.acct, files, lib)["name"], "soccer-cam")
        tight = files + [("instagram_reels", Path("y"), 2.0, 3.0)]               # 1.0s of room: only 0.8s fits
        self.assertEqual(PH.pick(self.acct, tight, lib)["name"], "soccer-cam")
        self.assertIsNone(PH.pick(self.acct, [("clip", Path("x"), 2.4, 3.0)], lib))


class ApplyTest(Base):
    def test_apply_keeps_the_delivered_identity(self):
        s = self.slot("07:00", cut=1.6)
        was_clip, was_cut = s["clip"]["sha256"], s["cuts"]["instagram_reels"]["sha256"]
        rec, why = PH.apply(self.cfg, self.acct, s)
        self.assertEqual(why, "")
        self.assertEqual(rec["name"], "soccer-cam")
        self.assertEqual(rec["files"], ["clip", "instagram_reels"])
        self.assertEqual(rec["join"], ["copy"])
        self.assertNotEqual(s["clip"]["sha256"], was_clip)
        self.assertEqual(s["clip"]["sha256"], B.sha256_file(Path(s["clip"]["path"])))
        self.assertEqual(s["clip"]["delivered_sha256"], was_clip)
        self.assertEqual(s["cuts"]["instagram_reels"]["delivered_sha256"], was_cut)
        self.assertIn(was_clip, B.clip_idents(s))                           # collect still knows the delivery
        self.assertIn(was_cut, B.clip_shas(s))
        self.assertAlmostEqual(s["clip"]["seconds"], 2.3, delta=0.06)
        kept = Path(s["folder"]) / PH.KEEP / "01-clip.mp4"
        self.assertEqual(B.sha256_file(kept), was_clip)                     # the file as delivered, kept
        again, _ = PH.apply(self.cfg, self.acct, s)                         # a second time: from the delivered file,
        self.assertAlmostEqual(s["clip"]["seconds"], 2.7, delta=0.06)       # never a pre-hook on a pre-hook
        self.assertEqual(again["name"], "ice-rock")

    def test_no_room_leaves_every_file_as_delivered(self):
        s = self.slot("07:00", secs=2.5)
        was = s["clip"]["sha256"]
        rec, why = PH.apply(self.cfg, self.acct, s)
        self.assertIsNone(rec)
        self.assertIn("no pre-hook fits", why)
        self.assertEqual(B.sha256_file(Path(s["clip"]["path"])), was)
        self.assertNotIn("pre_hook", s)

    def test_a_late_cut_gets_the_same_pre_hook(self):
        s = self.slot("07:00")
        PH.apply(self.cfg, self.acct, s)
        c = make_clip(Path(s["folder"]) / "instagram_reels.mp4", 1.5, "gray", 520)
        s["cuts"] = {"instagram_reels": {"path": str(c), "sha256": B.sha256_file(c), "seconds": 1.5}}
        self.assertEqual(PH.apply_cut(self.acct, s, "instagram_reels"), "")
        self.assertAlmostEqual(s["cuts"]["instagram_reels"]["seconds"], 2.3, delta=0.06)
        self.assertIn("instagram_reels", s["pre_hook"]["files"])
        c2 = make_clip(Path(s["folder"]) / "youtube_shorts.mp4", 2.6, "gray", 520)
        s["cuts"]["youtube_shorts"] = {"path": str(c2), "sha256": B.sha256_file(c2), "seconds": 2.6}
        self.assertIn("past", PH.apply_cut(self.acct, s, "youtube_shorts"))  # no room: it posts as delivered
        self.assertEqual(s["cuts"]["youtube_shorts"]["sha256"], B.sha256_file(c2))


def calm_captions(s: dict) -> dict:
    """A calm first line beside the clip (THE OPENER RULE, 2026-10-29: the test takes calm starts only)."""
    Path(s["clip"]["path"]).with_suffix(".srt").write_text(
        "1\n00:00:00,200 --> 00:00:01,400\nThe committee will come to order.\n")
    return s


class ExperimentTest(Base):
    def ctx(self, s: dict, day=date(2031, 7, 14)):
        return SOC.CollectCtx(cfg=self.cfg, acct=self.acct, day=day, slot=calm_captions(s), extras={})

    def test_signed_ahead_then_alternates_and_both_arms_drop_a_clip_with_no_room(self):
        self.switch("learn.experiments", "on")
        eid = L.new_experiment(self.cfg, self.acct, question="Does a pre-hook lift views?", kind="pre_hook",
                               arms={"A": "no pre-hook", "B": "pre-hook"}, by="Andrew", signed=True)
        with store.edit(L.experiments_path(self.acct), dict) as exps:
            exps[eid]["start_on"] = "2031-07-15"
        early = self.ctx(self.slot("07:00"))
        L.on_collect(early)                                                 # before its first board: nothing
        self.assertEqual(L.experiments(self.acct)[eid]["posts"], {"A": [], "B": []})
        self.assertNotIn("pre_hook", early.slot)
        day = date(2031, 7, 15)
        got = {}
        for k in ("08:45", "10:30", "12:15"):
            c = self.ctx(self.slot(k, day=day), day)
            was = c.slot["clip"]["sha256"]
            L.on_collect(c)
            got[k] = c.extras["experiment"][eid]
            self.assertEqual("pre_hook" in c.slot, got[k] == "B")
            self.assertEqual(c.slot["clip"]["sha256"] != was, got[k] == "B")
        self.assertEqual(list(got.values()), ["A", "B", "A"])
        long = self.ctx(self.slot("14:00", secs=2.5, day=day), day)          # no room for any pre-hook: out of the
        L.on_collect(long)                                                  # test, whichever arm it would take
        self.assertNotIn("experiment", long.extras)
        posts = L.experiments(self.acct)[eid]["posts"]
        entry = [p for a in "AB" for p in posts[a] if p["slot"] == "14:00"][0]
        self.assertTrue(entry["practice"])
        self.assertEqual(entry["why"], "no pre-hook fits under the tightest ceiling")


if __name__ == "__main__":
    unittest.main()
