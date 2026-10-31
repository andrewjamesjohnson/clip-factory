import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The hook coach (clipfactory/coach.py): the measured layer from a clip's own files (srt numbers, the hook, the title,
# the length, the records), scene changes by ffmpeg only from 06:26 to 20:45, a written layer the clip-coach task
# writes through `coach write` (validated), the coach fold on the card (headline "Coach" + the written total + the
# EXPECTED records, one Mechanics line), and "Use this title", which rewrites only the YouTube title of a READY clip's
# words file. Off, it does nothing at all. Throwaway folders only; ffmpeg's scene read is a canned answer; no network.

import contextlib
import io
import json
import math
import shutil
import sys
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from clipfactory import board as B          # noqa: E402
from clipfactory import coach as CO         # noqa: E402
from clipfactory import collect as K        # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import signoff as S        # noqa: E402
from clipfactory import social as SOC       # noqa: E402
from clipfactory import switches as SW      # noqa: E402
from social_fakes import Clock, _master, cfg_in_tmp, serve, slot_posted  # noqa: E402

DAY = date(2031, 7, 8)

GTA_SRT = """1
00:00:00,000 --> 00:00:01,337
STAYING IN THE CITY

2
00:00:01,337 --> 00:00:02,100
THIS NEW MAP

3
00:00:02,100 --> 00:00:02,950
HAS DETAILED HAMLET

4
00:00:03,100 --> 00:00:06,000
THE MAP IS HUGE

5
00:00:10,000 --> 00:00:30,000
WE WALKED IT ALL
"""

POLITICS_SRT = """1
00:00:00,009 --> 00:00:00,900
He said there

2
00:00:00,900 --> 00:00:01,800
was never a ceasefire

3
00:00:01,800 --> 00:00:02,700
on energy

4
00:00:02,700 --> 00:00:03,500
and Wright said

5
00:00:03,500 --> 00:00:30,000
President Trump is always working
"""

POLITICS_YT = """# SLOT      07:00
# TEMPLATE  THE SIT-DOWN
# HOOK      WRIGHT ON THE CEASEFIRE
TITLE
Chris Wright would not say if the ceasefire ever existed

DESCRIPTION
He was asked twice.
Full interview: https://example.com/x

TAGS
energy, ceasefire
"""

SCENE_ERR = """frame=    1 fps=0.0 q=-0.0 size=N/A time=00:00:00.03 bitrate=N/A speed=N/A
[Parsed_showinfo_1 @ 0x600001] config in time_base: 1/15360, frame_rate: 30/1
[Parsed_showinfo_1 @ 0x600001] n:   0 pts:   6656 pts_time:0.433333 duration:    512 fmt:yuv420p
[Parsed_showinfo_1 @ 0x600001] n:   1 pts:  16896 pts_time:1.1     duration:    512 fmt:yuv420p
[Parsed_showinfo_1 @ 0x600001] n:   2 pts:  43520 pts_time:2.833333 duration:    512 fmt:yuv420p
[Parsed_showinfo_1 @ 0x600001] n:   3 pts:  49152 pts_time:3.2     duration:    512 fmt:yuv420p
frame=   90 fps=0.0 q=-0.0 Lsize=N/A time=00:00:03.00 bitrate=N/A speed=  40x
"""


def good_written(titles=None) -> dict:
    return {"scores": {"hook": 7, "keep_watching": 6, "no_sound": 8, "density": 7, "share": 5},
            "fix": "Open on the map line at 0:03, not on the walk.",
            "titles": titles or ["The map is huge in The City", "We walked the new map", "This new map has detail"],
            "why": "A strong first line, then it wanders."}


class CoachBase(unittest.TestCase):
    def setUp(self):
        cfg_in_tmp(self)
        raw = json.loads(self.cfg_path.read_text())
        for slug, a in raw["accounts"].items():          # the skills' work folders in the tmp folder too
            a["skill_dir"] = str(self.tmp / "skills" / slug)
        self.cfg_path.write_text(json.dumps(raw))
        self.cfg = C.load(self.cfg_path)
        self.gta = self.cfg.account("gaming")
        self.led = self.cfg.account("politics")
        REG.reset()

    def on(self, *accts):
        for a in accts or (self.gta, self.led):
            SW.set_mode(self.cfg, a, "coach", "on", "test")

    def clock(self, hhmm: str, day: date = DAY) -> Clock:
        c = Clock(datetime(day.year, day.month, day.day, tzinfo=self.cfg.tz()))
        c.at(hhmm, day)
        return c.patch(self, self.cfg)

    def deliver(self, acct, day: date, slot: str, srt: str = "", pack: dict = None, yt: str = "",
                folder_files: dict = None) -> dict:
        """A delivered folder like the skills write (clip, words, an srt, the GTA pack beside it), then collect."""
        master = _master(self.tmp)
        root = acct.delivered_dirs(day)[0]
        hhmm = slot.replace(":", "")
        d = root / f"{hhmm} clip-{hhmm}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(master, d / "01-clip.mp4")
        for p in ("youtube", "tiktok", "facebook", "reels"):
            text = yt if (p == "youtube" and yt) else f"Clip at {slot} | Creator\n\nWords for {p} at {slot}.\n"
            (d / f"{p}.txt").write_text(text)
        if srt:
            (d / "01-clip.srt").write_text(srt)
        if pack is not None:
            (root / f"{hhmm}.pack.json").write_text(json.dumps(pack))
        for name, text in (folder_files or {}).items():
            (d / name).write_text(text)
        K.collect(self.cfg, acct, day)
        return ((B.read(acct, day) or {}).get("slots") or {})[slot]

    def slot(self, acct, slot: str, day: date = DAY) -> dict:
        return ((B.read(acct, day) or {}).get("slots") or {})[slot]

    def gta_job(self, job: str = "jobx", slug: str = "clip-0700", hook: str = "STAYING IN THE CITY"):
        work = self.gta.skill_dir / "work" / job
        work.mkdir(parents=True, exist_ok=True)
        (work / "slate.json").write_text(json.dumps({"clips": [{"slug": slug, "hook": hook, "template": "underlay",
                                                                "titles": {"youtube": "The City map tour"}}]}))
        (work / "project.json").write_text(json.dumps({"rights": "unverified"}))
        (work / "RUN.md").write_text("## preflight\n    [ok  ] EDGES    fine\n\n## the grade (grade.py)\n"
                                     "[  ok  ] HOOK       first word at +0.00s\n"
                                     "[ WARN ] DEADAIR    a hole of 4.0s at +6.00s\n\n## done\n")
        return work


# ------------------------------------------------------------------------------------------------------ the numbers
class NumbersTest(unittest.TestCase):
    def test_gta_caps_srt(self):
        sig = CO.srt_signals(CO.parse_srt(GTA_SRT), 35.1)
        self.assertEqual(sig["first_word_s"], 0.0)
        self.assertEqual(sig["wps_3s"], 3.33)                 # 10 words in cues starting before 3 s
        self.assertEqual(sig["longest_gap_s"], 4.0)           # 6.0 to 10.0
        self.assertEqual(sig["longest_gap_at"], 6.0)
        covered = 2.95 + 2.9 + 20.0
        self.assertAlmostEqual(sig["silence_ratio"], round(1 - covered / 35.1, 3), places=3)

    def test_politics_sentence_case_srt(self):
        sig = CO.srt_signals(CO.parse_srt(POLITICS_SRT), 72.6)
        self.assertEqual(sig["first_word_s"], 0.009)
        self.assertEqual(sig["wps_3s"], 4.0)                  # 12 words before 3 s
        self.assertEqual(sig["longest_gap_s"], 0.01)          # only the 9 ms before the first word
        self.assertGreater(sig["silence_ratio"], 0.5)          # the last cue ends at 30 s of 72.6

    def test_reactions_words(self):
        w = [{"start": 0.08, "end": 0.3, "text": "Oh"}, {"start": 0.4, "end": 0.6, "text": "no"},
             {"start": 3.1, "end": 3.4, "text": "wait"}]
        self.assertEqual(CO.words_signals(w), {"first_word_s": 0.08, "wps_3s": 0.67})

    def test_scene_parse_from_canned_stderr(self):
        self.assertEqual(CO.parse_scenes(SCENE_ERR), [0.433, 1.1, 2.833])
        self.assertEqual(CO.parse_scenes(""), [])

    def test_scene_command(self):
        cmd = CO.scene_cmd(Path("/x/clip.mp4"), "w=iw:h=iw*9/16:x=0:y=ih*452/1920")
        self.assertIn("-nostdin", cmd)
        self.assertEqual(cmd[cmd.index("-t") + 1], "3")
        self.assertLess(cmd.index("-t"), cmd.index("-i"))
        vf = cmd[cmd.index("-vf") + 1]
        self.assertTrue(vf.startswith("crop=w=iw:h=iw*9/16"))
        self.assertIn("select='gt(scene,0.12)',showinfo", vf)
        self.assertEqual(cmd[-3:], ["-f", "null", "-"])

    def test_render_and_grade(self):
        lines = CO.render_lines("x\nremoved 98.7s of silence in 15 cut(s)\ntempo x1.100\ncard: 'Chris Wright' at "
                                "+5.0s for 4.0s\nframing: strap +1.0s\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(CO.render_numbers(lines), {"silence_removed_s": 98.7, "silence_cuts": 15, "tempo": 1.1,
                                                    "cards": [{"name": "Chris Wright", "at": 5.0}]})
        block = CO.grade_block("## the grade (grade.py)\n[  ok  ] HOOK       first word at +0.00s\n"
                               "## the grade (grade.py)\nrun two clip-a\n[ BAD  ] OPEN       2 words\n## end\n", "")
        self.assertIn("OPEN", block)
        self.assertEqual(CO.grade_tags(block), {"OPEN": "bad"})
        log = "[  ok  ] HOOK  a\nother\n[  ok  ] HOOK  b\n[ WARN ] PACE  c\n"
        self.assertEqual(CO.grade_tags(CO.grade_block(log)), {"HOOK": "ok", "PACE": "warn"})

    def test_title_features(self):
        f = CO.title_feat('Wright says "never" on 10 deals?', ["Chris Wright"])
        self.assertEqual((f["number"], f["question"], f["name"], f["quote"]), (True, True, False, True))
        self.assertTrue(CO.title_feat("Chris Wright on the deal", ["Chris Wright"])["name"])
        self.assertEqual(CO.title_feat("ABC", [])["caps_ratio"], 1.0)

    def test_scores_and_weights(self):
        class A:
            maker = "gta"
        sig = {"first_word_s": 0.0, "wps_3s": 3.4, "hook_on_screen": True, "scene_changes_3s": 3,
               "longest_gap_s": 0.5, "silence_ratio": 0.05, "length_vs_best": {"youtube_shorts": 0.0},
               "length_best": {"youtube_shorts": [30, 45]}, "words": 100, "title_chars": 40, "wps_all": 3.0}
        m, flags = CO.score(A(), sig)
        self.assertEqual((m["hook"], m["keep_watching"], m["no_sound"], m["density"], m["total"]),
                         (100, 100, 100, 100, 100))
        self.assertIsNone(m["share"])
        self.assertEqual(flags, [])
        slow = dict(sig, first_word_s=2.5, wps_3s=1.0, scene_changes_3s=0)
        m2, f2 = CO.score(A(), slow)
        self.assertLess(m2["hook"], 50)
        self.assertEqual({f["key"] for f in f2}, {"slow_start", "thin_open", "still_open"})
        self.assertNotIn("\u2014", json.dumps(f2, ensure_ascii=False))
        # the playbook's weights win: only the hook counts
        m3, _ = CO.score(A(), slow, {"weights": {"hook": 1, "keep_watching": 0, "no_sound": 0, "density": 0}})
        self.assertEqual(m3["total"], m2["hook"])


# ------------------------------------------------------------------------------------------------------ collect
class CollectTest(CoachBase):
    def test_off_writes_nothing(self):
        s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        self.assertEqual(s["status"], B.READY)
        self.assertFalse((C.STATE / "gaming" / "coach").exists())
        self.assertEqual(CO.card_part(self.cfg, self.gta, DAY, s, self.cfg.now()), "")
        self.assertEqual(SOC.card_parts(self.cfg, self.gta, DAY, s, self.cfg.now()), "")

    def test_gta_collect_measures_and_copies_inputs(self):
        self.on()
        self.gta_job()
        t0 = time.monotonic()
        s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT,
                         pack={"slug": "clip-0700", "job": "jobx", "hook": "STAYING IN THE CITY"})
        self.assertLess(time.monotonic() - t0, 30)
        card = CO.load(self.gta, DAY, "07:00")
        self.assertTrue(CO.fresh(card, s))
        sig = card["signals"]
        self.assertEqual((sig["first_word_s"], sig["wps_3s"]), (0.0, 3.33))
        self.assertEqual(sig["hook_text"], "STAYING IN THE CITY")
        self.assertTrue(sig["hook_on_screen"])
        self.assertEqual(sig["title"], "Clip at 07:00 | Creator")
        self.assertEqual(sig["grade"], {"HOOK": "ok", "DEADAIR": "warn"})
        self.assertIn("scene_changes_3s", sig["missing"])
        self.assertIsInstance(card["measured"]["total"], int)
        inp = CO.inputs_dir(self.gta, s["clip"]["sha256"])
        self.assertEqual(sorted(p.name for p in inp.iterdir()), ["clip.srt", "from.json", "grade.txt", "pack.json"])
        # nothing written into the drafts or delivery folders
        self.assertEqual(sorted(p.name for p in Path(s["folder"]).iterdir()),
                         sorted(["01-clip.mp4", "01-clip.srt", "facebook_reels.txt", "instagram_reels.txt",
                                 "tiktok.txt", "youtube_shorts.txt"]))

    def test_on_collect_is_quick(self):
        self.on()
        s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        ctx = SOC.CollectCtx(cfg=self.cfg, acct=self.gta, day=DAY, slot=dict(s), folder=Path(s["source_folder"]),
                             dest=Path(s["folder"]))
        t0 = time.monotonic()
        CO.on_collect(ctx)
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertEqual(ctx.extras, {})

    def test_drop_folder_clip_missing_inputs(self):
        self.on()
        slot = {"slot": "07:00", "publish_at": self.cfg.slot_time(DAY, "07:00").isoformat(), "status": B.READY,
                "format": "underlay", "slug": "reactions-can-yall-believe-we-here", "cover": None,
                "clip": {"path": str(self.tmp / "gone" / "01-x.mp4"), "sha256": "a" * 64, "seconds": 34.3},
                "copy": {}}
        ctx = SOC.CollectCtx(cfg=self.cfg, acct=self.gta, day=DAY, slot=slot, folder=None, dest=None)
        CO.on_collect(ctx)                                     # never raises
        card = CO.load(self.gta, DAY, "07:00")
        miss = card["signals"]["missing"]
        for k in ("first_word_s", "wps_3s", "hook_on_screen", "title", "creator", "scene_changes_3s"):
            self.assertIn(k, miss)
        self.assertTrue(any(m.startswith("format") for m in miss))
        self.assertEqual(card["signals"]["format"], "")       # a drop clip's format is not trusted
        self.assertIsNotNone(card["measured"]["keep_watching"])   # the length alone still counts
        self.assertIn("Not measured", CO.card_part(self.cfg, self.gta, DAY, slot, self.cfg.now()))

    def test_politics_vanished_work_dir(self):
        self.on()
        job = "ABC Wright 10.18.26"
        jd = self.led.account_dir / "Jobs" / job
        jd.mkdir(parents=True)
        (jd / "slate.json").write_text(json.dumps({"clips": [{"slug": "clip-0700", "hook": "WRIGHT WOULD NOT SAY",
                                                              "mode": "fit"}]}))
        (jd / "project.json").write_text(json.dumps({"source": "/private/tmp/clipwork/gone-for-good/master.mp4",
                                                     "look": "broadcast"}))
        pub = self.led.account_dir / "Publish"
        pub.mkdir(parents=True)
        (pub / "schedule.json").write_text(json.dumps({DAY.isoformat(): {"07:00": {"job": job, "slug": "clip-0700"}}}))
        s = self.deliver(self.led, DAY, "07:00", srt=POLITICS_SRT)
        card = CO.load(self.led, DAY, "07:00")
        self.assertTrue(CO.fresh(card, s))
        sig = card["signals"]
        self.assertEqual(sig["hook_text"], "WRIGHT WOULD NOT SAY")
        self.assertEqual((sig["fit"], sig["fit_y"]), (True, 452))
        self.assertNotIn("render", sig)
        self.assertEqual(sig["sources"]["job"], job)
        # the render log is copied while it is there
        work = self.tmp / "clipwork" / "w"
        work.mkdir(parents=True)
        (work / "render.log").write_text("removed 98.7s of silence in 15 cut(s)\ntempo x1.100\nother\n")
        (jd / "project.json").write_text(json.dumps({"source": str(work / "master.mp4")}))
        CO.copy_inputs(self.cfg, self.led, DAY, s, Path(s["source_folder"]))
        shutil.rmtree(work)                                    # /private/tmp emptied by macOS
        card = CO.measure(self.cfg, self.led, DAY, s)
        self.assertEqual(card["signals"]["render"], {"silence_removed_s": 98.7, "silence_cuts": 15, "tempo": 1.1})

    def test_records_from_the_learner(self):
        self.on()
        rows = [{"format": "underlay", "creator_key": "creator", "hour": 7, "rel_24h": -0.9, "sent_s": 35}
                for _ in range(5)] + [{"format": "pip", "creator_key": "other", "hour": 9, "rel_24h": 0.2}]
        p = C.STATE / "gaming" / "learn" / "features.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(rows))
        with B.edit(self.cfg, self.gta, DAY) as bd:
            pass
        s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT, pack={"job": "jobx", "hook": "X"})
        self.gta_job()
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"]["07:00"]["host"] = "Creator"
        s = self.slot(self.gta, "07:00")
        card = CO.measure(self.cfg, self.gta, DAY, s)
        self.assertEqual(card["signals"]["creator_record"], {"n": 5, "median_rel": -0.9})
        self.assertEqual(card["signals"]["slot_record"], {"n": 5, "median_rel": -0.9})
        self.assertFalse([f for f in card["flags"] if f["key"].startswith("weak_")])   # 5 posts: worded, no flag
        words = {e["what"]: e["words"] for e in card["expected"]}
        self.assertEqual(words["creator"], "Creator clips: usually 0.4x, 5 posts")      # rows with no outlet
        self.assertEqual(words["slot"], "Clips posted between 07:00 and 08:00: usually 0.4x, 5 posts")
        self.assertEqual(words["length"], "Clips under 30s: no posts to go by yet")      # the rows are 35 s
        self.assertEqual(CO.total(self.gta, DAY, "07:00"), card["measured"]["total"])
        self.assertEqual(CO.total(self.gta, DAY, "08:45"), None)

    def test_playbook_only_when_switched_on(self):
        self.on(self.gta)
        pb = C.STATE / "gaming" / "playbook.json"
        pb.parent.mkdir(parents=True, exist_ok=True)
        # an approved rule, as playbook.py keeps them (its coach block is written from the rules in force)
        when = "2031-07-01T00:00:00+00:00"
        pb.write_text(json.dumps({"version": 1, "rules": [
            {"id": "r1", "key": "coach.length_best.youtube_shorts", "value": [1, 2], "approved": when},
            {"id": "r2", "key": "coach.first_word_max_s", "value": 0.5, "approved": when}],
            "coach": {"length_best": {"youtube_shorts": [1, 2]}, "first_word_max_s": 0.5}}))
        s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        card = CO.load(self.gta, DAY, "07:00")
        self.assertEqual(card["signals"]["length_best"]["youtube_shorts"], [30.0, 45.0])
        self.assertEqual(card["signals"]["sources"]["playbook"], "off")
        SW.set_mode(self.cfg, self.gta, "learn.playbook", "on", "test")
        card = CO.measure(self.cfg, self.gta, DAY, s)
        self.assertEqual(card["signals"]["length_best"]["youtube_shorts"], [1.0, 2.0])
        self.assertEqual(card["signals"]["length_vs_best"]["youtube_shorts"], 0.0)


# ------------------------------------------------------------------------------------------------------ the studio job
class TickTest(CoachBase):
    def fake_run(self, calls):
        def run(args, *a, **kw):
            calls.append(list(args))
            self.assertIn("ffmpeg", " ".join(args))
            return mock.Mock(returncode=0, stderr=SCENE_ERR, stdout="")
        return run

    def test_night_window_skips_ffmpeg(self):
        self.on()
        self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        self.deliver(self.gta, DAY, "08:45", srt=GTA_SRT)
        calls = []
        with mock.patch.object(CO.subprocess, "run", self.fake_run(calls)):
            for hhmm, day in (("21:00", DAY), ("23:59", DAY), ("03:00", DAY), ("06:25", DAY), ("20:45", DAY),
                              ("06:00", DAY + timedelta(days=1))):
                c = self.clock(hhmm, day)
                self.assertEqual(CO.tick(self.cfg, now=c.now()), [], hhmm)
                self.assertEqual(SOC.run_job(self.cfg, "studio", now=c.now()), [])
            self.assertEqual(calls, [])                         # no ffmpeg at all from 20:45 to 06:25
            c = self.clock("06:26")
            said = CO.tick(self.cfg, now=c.now(), quiet=False)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("3 picture changes" in x for x in said), said)
        card = CO.load(self.gta, DAY, "07:00")
        self.assertEqual(card["signals"]["scene_changes_3s"], 3)
        self.assertEqual(card["signals"]["scene_times"], [0.433, 1.1, 2.833])
        self.assertNotIn("scene_changes_3s", card["signals"]["missing"])
        with mock.patch.object(CO.subprocess, "run", self.fake_run(calls)):
            CO.tick(self.cfg, now=self.clock("12:00").now())
        self.assertEqual(len(calls), 2)                         # read once per clip

    def test_off_tick_does_nothing(self):
        self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        with mock.patch.object(CO.subprocess, "run", side_effect=AssertionError("no ffmpeg while off")):
            self.assertEqual(CO.tick(self.cfg, now=self.clock("12:00").now()), [])
        self.assertFalse((C.STATE / "gaming" / "coach").exists())

    def test_scenes_refused_at_night(self):
        with self.assertRaises(SystemExit):
            CO.scenes(self.cfg, Path("/x.mp4"), now=self.clock("05:00").now())

    def test_politics_fit_crops_the_picture(self):
        self.on(self.led)
        job = "J1"
        jd = self.led.account_dir / "Jobs" / job
        jd.mkdir(parents=True)
        (jd / "slate.json").write_text(json.dumps({"clips": [{"slug": "clip-0700", "mode": "fit"}]}))
        (jd / "project.json").write_text(json.dumps({"look": "broadcast"}))
        pub = self.led.account_dir / "Publish"
        pub.mkdir(parents=True)
        (pub / "schedule.json").write_text(json.dumps({DAY.isoformat(): {"07:00": {"job": job, "slug": "clip-0700"}}}))
        self.deliver(self.led, DAY, "07:00", srt=POLITICS_SRT)
        calls = []
        with mock.patch.object(CO.subprocess, "run", self.fake_run(calls)):
            CO.tick(self.cfg, now=self.clock("09:00").now())
        self.assertEqual(len(calls), 1)
        self.assertIn("crop=w=iw:h=iw*9/16:x=0:y=ih*452/1920,", calls[0][calls[0].index("-vf") + 1])

    def test_ffmpeg_failure_is_tried_three_times(self):
        self.on(self.gta)
        self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        n = []

        def bad(args, *a, **kw):
            n.append(1)
            return mock.Mock(returncode=1, stderr="Invalid data found\n", stdout="")
        with mock.patch.object(CO.subprocess, "run", bad):
            for _ in range(5):
                CO.tick(self.cfg, now=self.clock("10:00").now())
        self.assertEqual(len(n), CO.SCENE_TRIES)
        sg = CO.load(self.gta, DAY, "07:00")["signals"]
        self.assertIn("ffmpeg exit 1", sg["scene_error"])

    def test_stale_on_a_new_clip(self):
        self.on(self.gta)
        s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        CO.write(self.cfg, self.gta, DAY, "07:00", good_written(), "clip-coach", now=self.clock("06:40").now())
        old = CO.load(self.gta, DAY, "07:00")
        self.assertTrue(CO.fresh(old, s))
        with B.edit(self.cfg, self.gta, DAY) as bd:          # a new clip in the slot (Make again, a re-render)
            bd["slots"]["07:00"]["clip"]["sha256"] = "b" * 64
        s = self.slot(self.gta, "07:00")
        self.assertFalse(CO.fresh(old, s))
        part = CO.card_part(self.cfg, self.gta, DAY, s, self.cfg.now())
        self.assertIn("not measured yet", part)
        self.assertIn("earlier clip", part)
        with mock.patch.object(CO.subprocess, "run", self.fake_run([])):
            CO.tick(self.cfg, now=self.clock("10:00").now())
        new = CO.load(self.gta, DAY, "07:00")
        self.assertEqual(new["clip_sha"], "b" * 64)
        self.assertIsNone(new["written"])                    # the old clip's words are not this clip's
        self.assertEqual([a.slug for a, _ in CO.pending(self.cfg, DAY, now=self.clock("06:30").now())], ["gaming"])


# ------------------------------------------------------------------------------------------------------ the written layer
class WriteTest(CoachBase):
    def setUp(self):
        super().setUp()
        self.on()
        self.s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        self.now = self.clock("06:40").now()

    def w(self, data, slot="07:00", acct=None):
        return CO.write(self.cfg, acct or self.gta, DAY, slot, data, "clip-coach", now=self.now)

    def test_good_write(self):
        said = self.w(good_written())
        self.assertIn("written by clip-coach", said)
        w = CO.load(self.gta, DAY, "07:00")["written"]
        self.assertEqual(w["by"], "clip-coach")
        self.assertEqual(len(w["titles"]), 3)
        self.assertEqual(w["scores"]["share"], 5)
        part = CO.card_part(self.cfg, self.gta, DAY, self.s, self.now)
        self.assertEqual(part.count('data-op="coach-title"'), 3)
        self.assertIn("Fix: Open on the map line", part)
        self.assertIn('<summary>Coach 6.6 <span class="cx-sub">of 10</span>', part)
        self.assertIn("Hook words 7, Payoff 6, Reads muted 8, Moment 7, Share 5", part)
        self.assertEqual(CO.load(self.gta, DAY, "07:00")["headline"]["words"], "Coach 6.6 of 10")
        self.assertEqual(CO.pending(self.cfg, DAY, now=self.now), [])

    def test_refusals(self):
        bad = [
            dict(good_written(), titles=["The map is huge", "We walked it", "This new map", "The City"]),
            dict(good_written(), titles=["The map is huge", "We walked it"]),
            dict(good_written(), fix="Open on the map \u2014 not the walk."),
            dict(good_written(), titles=["The map is huge \u2013 really", "We walked it", "This new map"]),
            dict(good_written(), titles=["The map is huge!", "We walked it", "This new map"]),
            dict(good_written(), fix="Open on the map!"),
            dict(good_written(), scores=dict(good_written()["scores"], hook=11)),
            dict(good_written(), scores=dict(good_written()["scores"], hook=0)),
            dict(good_written(), scores=dict(good_written()["scores"], hook=7.5)),
            dict(good_written(), scores=dict(good_written()["scores"], hook=True)),
            dict(good_written(), scores={k: 5 for k in ("hook", "keep_watching", "no_sound", "density")}),
            dict(good_written(), fix="x" * 201),
            dict(good_written(), titles=["x" * 101, "We walked it", "This new map"]),
            dict(good_written(), titles=["The map is\nhuge", "We walked it", "This new map"]),
            dict(good_written(), titles=["We saw Pelosi walk the map", "We walked it", "This new map"]),   # a name made up
            dict(good_written(), titles=["The map is 40 miles", "We walked it", "This new map"]),   # a number made up
            dict(good_written(), titles=["Clip at 07:00", "We walked it", "This new map"]),      # the title it has
            dict(good_written(), titles=["We walked it", "we walked it", "This new map"]),
            ["not", "an", "object"],
        ]
        for data in bad:
            with self.subTest(data=str(data)[:80]):
                with self.assertRaises(SystemExit):
                    self.w(data)
        self.assertIsNone(CO.load(self.gta, DAY, "07:00")["written"])
        self.w(dict(good_written(), scores=dict(good_written()["scores"], hook=10)))     # 10 is allowed (never given)

    def test_only_ready_and_only_on(self):
        slot_posted(self, self.gta, DAY, "08:45")
        with self.assertRaises(SystemExit) as e:
            self.w(good_written(), slot="08:45")
        self.assertIn("scheduled", str(e.exception))
        SW.set_mode(self.cfg, self.gta, "coach", "off", "test")
        with self.assertRaises(SystemExit):
            self.w(good_written())

    def test_politics_names_only_verified_people(self):
        job = "J2"
        jd = self.led.account_dir / "Jobs" / job
        jd.mkdir(parents=True)
        (jd / "slate.json").write_text(json.dumps({"clips": [{"slug": "clip-0700", "hook": "WRIGHT ON THE DEAL"}]}))
        (jd / "project.json").write_text("{}")
        (jd / "cast.json").write_text(json.dumps({"people": [
            {"id": "wright", "name": "Chris Wright", "verified": "Andrew, in Clip Review's Casts: looked at the stills"},
            {"id": "trump", "name": "Donald Trump", "provisional": {"named_as": "Trump", "by": "a bot"}}]}))
        pub = self.led.account_dir / "Publish"
        pub.mkdir(parents=True)
        (pub / "schedule.json").write_text(json.dumps({DAY.isoformat(): {"07:00": {"job": job, "slug": "clip-0700"}}}))
        self.deliver(self.led, DAY, "07:00", srt=POLITICS_SRT, yt=POLITICS_YT)
        ok = {"scores": {"hook": 6, "keep_watching": 6, "no_sound": 7, "density": 6, "share": 6},
              "fix": "Open on his answer.", "why": "",
              "titles": ["Chris Wright would not say", "Wright on the energy ceasefire", "Was there ever a ceasefire?"]}
        with self.assertRaises(SystemExit) as e:
            self.w(dict(ok, titles=["Trump is always working", ok["titles"][1], ok["titles"][2]]), acct=self.led)
        self.assertIn("verified", str(e.exception))
        self.w(ok, acct=self.led)
        text = "\n".join(CO.card_text(self.cfg, self.led, DAY, self.slot(self.led, "07:00")))
        self.assertIn("verified people (the only names a title may use): Chris Wright", text)
        self.assertIn("NOT verified (never name them): Donald Trump", text)
        self.assertIn("[00.01] He said there", text)


# ------------------------------------------------------------------------------------------------------ Use this title
class TitleTest(CoachBase):
    def setUp(self):
        super().setUp()
        self.on()
        self.clock("06:40")
        self.get, self.post = serve(self, self.cfg)

    def body(self, acct, slot, op="coach-title", title="", seen=None):
        s = self.slot(acct, slot)
        return {"op": op, "account": acct.slug, "date": "2000-01-01", "slots": [slot], "by": "Andrew",
                "fields": {"title": title, "date": DAY.isoformat()},
                "seen": B.seen_key(s) if seen is None else seen}

    def test_gta_keeps_its_credit(self):
        s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        CO.write(self.cfg, self.gta, DAY, "07:00", good_written(), "clip-coach")
        yt = Path(s["copy"]["youtube_shorts"]["path"])
        before = yt.read_text()
        part = CO.card_part(self.cfg, self.gta, DAY, s, self.cfg.now())
        self.assertIn("Use &quot;The map is huge in The City | Creator&quot; as the YouTube title of Gaming 07:00",
                      part)
        code, d = self.post(self.body(self.gta, "07:00", title="The map is huge in The City"), page=False)
        self.assertEqual((code, d["ok"]), (400, False))
        self.assertIn("Only a click on the review page", d["message"])
        code, d = self.post(self.body(self.gta, "07:00", title="The map is huge in The City", seen="old"))
        self.assertEqual(code, 400)
        self.assertIn("changed since the page showed", d["message"])
        self.assertEqual(yt.read_text(), before)
        code, d = self.post(self.body(self.gta, "07:00", title="The map is huge in The City"))
        self.assertEqual((code, d["ok"]), (200, True), d)
        after = yt.read_text()
        self.assertEqual(after.split("\n", 1)[0], "The map is huge in The City | Creator")
        self.assertEqual(after.split("\n", 1)[1], before.split("\n", 1)[1])        # nothing else changed
        s = self.slot(self.gta, "07:00")
        self.assertIn("YouTube title set from the coach by Andrew", B.last_note(s))
        card = CO.load(self.gta, DAY, "07:00")
        self.assertEqual(card["used_title"]["title"], "The map is huge in The City | Creator")
        self.assertEqual(card["signals"]["title"], "The map is huge in The City | Creator")
        for p in ("tiktok", "facebook_reels", "instagram_reels"):           # the other words are untouched
            self.assertEqual(B.sha256_text(Path(s["copy"][p]["path"]).read_text()), s["copy"][p]["sha256"])
        # Approve signs the words as they are now (signoff._as_is adopts the edit)
        with B.edit(self.cfg, self.gta, DAY) as bd:
            self.assertEqual(S._as_is(self.gta, bd["slots"]["07:00"]), [])
            self.assertEqual(bd["slots"]["07:00"]["copy"]["youtube_shorts"]["sha256"], B.sha256_text(after))
        # (undo) puts the words back exactly
        self.assertIn("(undo)", CO.card_part(self.cfg, self.gta, DAY, self.slot(self.gta, "07:00"), self.cfg.now()))
        code, d = self.post(self.body(self.gta, "07:00", op="coach-undo"))
        self.assertEqual((code, d["ok"]), (200, True), d)
        self.assertEqual(yt.read_text(), before)
        self.assertIsNone(CO.load(self.gta, DAY, "07:00")["used_title"])

    def test_long_title_drops_the_credit(self):
        s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        long_t = "The map is huge " + "and we walked it " * 5
        long_t = long_t.strip()[:95]
        CO.use_title(self.cfg, self.gta, DAY, "07:00", long_t, "Andrew", B.seen_key(s))
        first = Path(s["copy"]["youtube_shorts"]["path"]).read_text().split("\n", 1)[0]
        self.assertEqual(first, " ".join(long_t.split()))

    def test_politics_title_section(self):
        s = self.deliver(self.led, DAY, "07:00", srt=POLITICS_SRT, yt=POLITICS_YT)
        yt = Path(s["copy"]["youtube_shorts"]["path"])
        self.assertEqual(yt.read_text(), POLITICS_YT)
        code, d = self.post(self.body(self.led, "07:00", title="Wright would not say if there was a ceasefire"))
        self.assertEqual((code, d["ok"]), (200, True), d)
        self.assertEqual(yt.read_text(), POLITICS_YT.replace("Chris Wright would not say if the ceasefire ever existed",
                                                           "Wright would not say if there was a ceasefire"))

    def test_refused_when_signed_bad_or_off(self):
        self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        slot_posted(self, self.gta, DAY, "07:00")
        code, d = self.post(self.body(self.gta, "07:00", title="The map is huge"))
        self.assertEqual(code, 400)
        self.assertIn("It is signed: Pull it to change its title.", d["message"])
        self.deliver(self.gta, DAY, "08:45", srt=GTA_SRT)
        for t in ("The map \u2014 huge", "The map is huge!", "x" * 101, ""):
            code, d = self.post(self.body(self.gta, "08:45", title=t))
            self.assertEqual(code, 400, t)
        SW.set_mode(self.cfg, self.gta, "coach", "off", "test")
        code, d = self.post(self.body(self.gta, "08:45", title="The map is huge"))
        self.assertEqual(code, 400)
        self.assertIn("The coach is off", d["message"])

    def test_card_on_the_page(self):
        self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        code, _, page = self.get(f"/?date={DAY.isoformat()}")
        page = page.decode()
        self.assertEqual(code, 200)
        self.assertIn('<details class="cx" data-k="cx"><summary>Coach <span class="cx-sub">not written yet</span>',
                      page)
        self.assertIn("details.cx{", page)                         # CARD_CSS through registry.card_css()
        SW.set_mode(self.cfg, self.gta, "coach", "off", "test")
        SW.set_mode(self.cfg, self.led, "coach", "off", "test")
        code, _, page = self.get(f"/?date={DAY.isoformat()}")
        self.assertNotIn('class="cx"', page.decode())


# ------------------------------------------------------------------------------------------------------ doctor, CLI
class DoctorCliTest(CoachBase):
    def run_cli(self, *argv) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            REG.run_cli(self.cfg, "coach", list(argv))
        return out.getvalue()

    def test_doctor_lines(self):
        rows = CO.doctor(self.cfg)
        self.assertEqual(rows[0][:2], (None, "coach: off (it waits for factory.py social on <account> coach)"))
        self.on(self.gta)
        now = self.clock("06:40").now()
        self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        self.deliver(self.gta, DAY, "08:45", srt=GTA_SRT)
        rows = CO.doctor(self.cfg, now)
        texts = [r[1] for r in rows]
        self.assertIn("coach: Gaming: 2 of 2 ready clips measured (0 with picture changes), 0 written today", texts)
        self.assertIn("coach: Gaming: no written scorecards yet (create the clip-coach task at go-live)", texts)
        CO.write(self.cfg, self.gta, DAY, "07:00", good_written(), "clip-coach", now=now)
        texts = [r[1] for r in CO.doctor(self.cfg, now)]
        self.assertIn("coach: Gaming: 2 of 2 ready clips measured (0 with picture changes), 1 written today", texts)
        self.assertFalse(any("no written" in t for t in texts))
        doc = SOC.doctor(self.cfg, now)
        self.assertTrue(any("coach: Gaming: 2 of 2" in r[1] for r in doc))

    def test_cli(self):
        self.assertIn("off for every account", self.run_cli("pending", "--date", DAY.isoformat()))
        self.on(self.gta)
        self.clock("06:40")
        self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        self.assertEqual(self.run_cli("pending", "--date", DAY.isoformat()).strip(),
                         f"gaming {DAY} 07:00 clip-0700")
        card = self.run_cli("card", "gaming", "--date", DAY.isoformat(), "--slot", "07:00")
        for want in ("measured (0 to 100)", "hook on screen", "titles now:", "  YouTube: Clip at 07:00 | Creator",
                     "Mechanics ", "usually fine", "expected (the learner's records", "too few posts to say",
                     "first 15 s of the transcript:", "  [00.00] STAYING IN THE CITY", "playbook coach block (off)",
                     "ideas patterns: none", "GTA has no verified cast"):
            self.assertIn(want, card)
        f = self.tmp / "w.json"
        f.write_text(json.dumps(good_written()))
        said = self.run_cli("write", "gaming", "--date", DAY.isoformat(), "--slot", "07:00", "--file", str(f),
                            "--by", "clip-coach")
        self.assertIn("written by clip-coach", said)
        self.assertIn("nothing waiting", self.run_cli("pending", "--date", DAY.isoformat()))
        show = self.run_cli("show", "gaming", "--date", DAY.isoformat())
        self.assertIn("written by clip-coach", show)
        self.assertIn("Title: The map is huge in The City", show)
        self.assertIn("Coach 6.6 of 10", show)
        self.assertIn("Expected: too few posts to say", show)
        self.assertEqual(json.loads(self.run_cli("show", "gaming", "--date", DAY.isoformat(), "--slot", "0700",
                                                 "--json"))["slot"], "07:00")
        self.assertIn("measured", self.run_cli("measure", "gaming", "--date", DAY.isoformat()))
        with mock.patch.object(CO.subprocess, "run", side_effect=AssertionError("no ffmpeg at night")):
            self.clock("22:00")
            out = self.run_cli("measure", "gaming", "--date", DAY.isoformat(), "--scenes")
        self.assertIn("picture changes wait for 06:26 to 20:45", out)
        with self.assertRaises(SystemExit):
            self.run_cli("write", "gaming", "--date", DAY.isoformat(), "--slot", "07:00", "--file",
                         str(self.tmp / "missing.json"), "--by", "clip-coach")

    def test_ideas_patterns_in_the_card(self):
        self.on(self.gta)
        self.clock("06:40")
        self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        fake = mock.Mock(return_value={"winners": [{"title": "x"}]})
        with mock.patch.object(REG, "fn", side_effect=lambda spec: fake if spec == "ideas:patterns" else None):
            card = self.run_cli("card", "gaming", "--date", DAY.isoformat(), "--slot", "07:00")
        self.assertIn('ideas patterns: {"winners": [{"title": "x"}]}', card)


class ReviewFixesTest(CoachBase):
    """The review's findings, each pinned (2031-07-08 is the board; the clock is set per test)."""

    def politics_job(self, cards=("wright",)):
        job = "J2"
        jd = self.led.account_dir / "Jobs" / job
        jd.mkdir(parents=True)
        (jd / "slate.json").write_text(json.dumps({"clips": [{"slug": "clip-0700", "hook": "WRIGHT ON THE DEAL",
                                                              "cards": [{"who": w, "at": 5.0} for w in cards]}]}))
        (jd / "project.json").write_text("{}")
        (jd / "cast.json").write_text(json.dumps({"people": [
            {"id": "wright", "name": "Chris Wright", "verified": "Andrew, in Clip Review's Casts: looked at the stills"},
            {"id": "trump", "name": "Donald Trump", "provisional": {"named_as": "Trump", "by": "a bot"}},
            {"id": "vance", "name": "JD Vance", "verified": "a bot matched the face"}]}))
        pub = self.led.account_dir / "Publish"
        pub.mkdir(parents=True)
        (pub / "schedule.json").write_text(json.dumps({DAY.isoformat(): {"07:00": {"job": job, "slug": "clip-0700"}}}))
        return jd

    def test_a_made_up_name_as_the_first_word_is_refused(self):
        self.on()
        self.clock("06:40")
        self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        others = ["The map is huge in The City", "We walked the new map"]
        for t in ("Ninja walked the new map", "Pelosi walked the new map"):
            with self.subTest(t=t), self.assertRaises(SystemExit) as e:
                CO.write(self.cfg, self.gta, DAY, "07:00", dict(good_written(), titles=[t] + others), "clip-coach")
            self.assertIn(t.split()[0], str(e.exception))
        vocab, banned = CO.vocabulary(self.cfg, self.gta, DAY, self.slot(self.gta, "07:00"))
        for ok in ("The City map tour", "Huge map in The City", "Why the new map is huge", "THE MAP IS HUGE"):
            self.assertEqual(CO.check_title(ok, vocab, banned), "", ok)
        # the page cannot use a title the task could not write
        s = self.slot(self.gta, "07:00")
        with self.assertRaises(SystemExit) as e:
            CO.use_title(self.cfg, self.gta, DAY, "07:00", "Ninja walked the new map", "Andrew", B.seen_key(s))
        self.assertIn("Ninja", str(e.exception))

    def test_politics_first_word_and_every_unverified_person_in_the_cast(self):
        self.on(self.led)
        self.clock("06:40")
        self.politics_job(cards=("wright",))       # Trump and Vance have no card on this clip
        self.deliver(self.led, DAY, "07:00", srt=POLITICS_SRT, yt=POLITICS_YT)
        ok = {"scores": {"hook": 6, "keep_watching": 6, "no_sound": 7, "density": 6, "share": 6},
              "fix": "Open on his answer.", "why": "",
              "titles": ["Chris Wright would not say", "Wright on the energy ceasefire", "Was there ever a ceasefire?"]}
        for t in ("Pelosi would not say if the ceasefire ever existed", "Donald Trump is always working",
                  "Trump is always working", "Vance on the ceasefire"):
            with self.subTest(t=t), self.assertRaises(SystemExit):
                CO.write(self.cfg, self.led, DAY, "07:00", dict(ok, titles=[t] + ok["titles"][1:]), "clip-coach")
        self.assertIsNone(CO.load(self.led, DAY, "07:00")["written"])
        text = "\n".join(CO.card_text(self.cfg, self.led, DAY, self.slot(self.led, "07:00")))
        self.assertIn("verified people (the only names a title may use): Chris Wright", text)
        self.assertIn("NOT verified (never name them): Donald Trump, JD Vance", text)
        CO.write(self.cfg, self.led, DAY, "07:00", ok, "clip-coach")

    def test_angle_brackets_are_refused(self):
        self.assertIn("< or >", CO.check_title("The map is <huge>", {"the", "map", "is", "huge"}, set()))
        self.assertIn("< or >", CO.check_title("The map > the city"))
        self.on(self.gta)
        self.clock("06:40")
        s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        with self.assertRaises(SystemExit) as e:
            CO.use_title(self.cfg, self.gta, DAY, "07:00", "The map is <huge>", "Andrew", B.seen_key(s))
        self.assertIn("< or >", str(e.exception))

    def test_records_count_posts_not_outlet_rows(self):
        self.on(self.gta)
        rows = [{"pid": f"p{i}", "outlet": o, "format": "underlay", "creator_key": "creator", "hour": 7,
                 "rel_24h": -0.9 if o != "tiktok" else -0.1}
                for i in (1, 2) for o in ("youtube_shorts", "facebook_reels", "instagram_reels", "tiktok")]
        p = C.STATE / "gaming" / "learn" / "features.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(rows))
        self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"]["07:00"]["host"] = "Creator"
        card = CO.measure(self.cfg, self.gta, DAY, self.slot(self.gta, "07:00"))
        self.assertEqual(card["signals"]["creator_record"], {"n": 2, "median_rel": -0.9})
        self.assertEqual(card["signals"]["slot_record"]["n"], 2)
        self.assertFalse([f for f in card["flags"] if f["key"].startswith("weak_")])    # 2 posts is not evidence
        self.assertEqual(card["expected"][0]["words"], "Creator clips: 2 posts, too few to say")
        self.assertEqual(card["headline"]["expected"], "too few posts to say")
        # five posts on four outlets: worded one platform at a time, still no flag (a flag needs 8)
        rows = [dict(r, pid=f"q{i}") for i in range(5) for r in rows[:4]]
        p.write_text(json.dumps(rows))
        card = CO.measure(self.cfg, self.gta, DAY, self.slot(self.gta, "07:00"))
        self.assertEqual(card["signals"]["creator_record"]["n"], 5)
        self.assertEqual(card["expected"][0]["words"],
                         "Creator clips: usually 0.4x on YouTube, 5 posts; 0.4x on Facebook, 5 posts; "
                         "0.4x on Instagram, 5 posts; 0.9x on TikTok, 5 posts")
        self.assertFalse([f for f in card["flags"] if f["key"].startswith("weak_")])
        # eight posts, all under the usual at 0.4x: strong evidence, one flag (the platform with the most posts)
        p.write_text(json.dumps([dict(r, pid=f"e{i}") for i in range(8) for r in rows[:4]]))
        card = CO.measure(self.cfg, self.gta, DAY, self.slot(self.gta, "07:00"))
        weak = [f for f in card["flags"] if f["key"].startswith("weak_")]
        self.assertEqual([f["key"] for f in weak], ["weak_creator", "weak_slot"])
        self.assertEqual(weak[0]["words"], "Creator clips have done 0.4x the usual on YouTube over 8 posts, 8 of them "
                                           "under it.")
        self.assertEqual(weak[0]["kind"], "record")

    def test_undo_puts_the_scores_back_too(self):
        self.on(self.gta)
        self.clock("06:40")
        s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"]["07:00"]["host"] = "Creator"
        s = self.slot(self.gta, "07:00")
        before = CO.measure(self.cfg, self.gta, DAY, s)
        CO.use_title(self.cfg, self.gta, DAY, "07:00", "The map", "Andrew", B.seen_key(s))
        mid = CO.load(self.gta, DAY, "07:00")
        self.assertIn("title_short", {f["key"] for f in mid["flags"]})
        CO.undo_title(self.cfg, self.gta, DAY, "07:00", "Andrew", B.seen_key(self.slot(self.gta, "07:00")))
        after = CO.load(self.gta, DAY, "07:00")
        self.assertEqual(after["signals"]["title"], before["signals"]["title"])
        self.assertEqual(after["measured"], before["measured"])
        self.assertEqual(after["flags"], before["flags"])
        self.assertEqual(after["signals"]["title_feat"], before["signals"]["title_feat"])
        self.assertTrue(after["signals"]["title_feat"]["name"])          # "Creator" is in the title: a name

    def test_tempo_from_the_engine_log_and_only_this_clip(self):
        log = ("[01-wright-was-there-ever-a-ceasefire] SUPERCUT 2 segments\n"
               "note: 01-wright-was-there-ever-a-ceasefire removed 98.7s of silence in 15 cut(s); longest pause\n"
               "tempo: 01-wright-was-there-ever-a-ceasefire x1.100  (set on the clip)\n"
               "card: 'Chris Wright' at +5.0s for 4.0s\n"
               "[02-vance-on-the-deal] SUPERCUT 1 segment\n"
               "tempo: 02-vance-on-the-deal x1.050  (set on the clip)\n"
               "card: 'JD Vance' at +3.0s for 4.0s\n"
               "card: 'Chris Wright' at +5.0s for 4.0s\n")
        got = CO.render_numbers(CO.render_lines(log, "wright-was-there-ever-a-ceasefire"))
        self.assertEqual(got, {"silence_removed_s": 98.7, "silence_cuts": 15, "tempo": 1.1,
                               "cards": [{"name": "Chris Wright", "at": 5.0}]})
        self.assertEqual(CO.render_numbers(CO.render_lines(log, "02-vance-on-the-deal"))["tempo"], 1.05)
        self.assertEqual(len(CO.render_lines(log, "a-slug-the-log-never-names")), 5)     # read whole, once each
        # copy_inputs reads only the newest of render.log and render2.log
        self.on(self.led)
        jd = self.politics_job()
        work = self.tmp / "clipwork" / "w"
        work.mkdir(parents=True)
        (work / "render.log").write_text("tempo: 01-clip-0700 x1.200\ncard: 'Old Card' at +1.0s for 4.0s\n")
        (work / "render2.log").write_text("tempo: 01-clip-0700 x1.100\ncard: 'Chris Wright' at +5.0s for 4.0s\n")
        os.utime(work / "render.log", (1_000_000, 1_000_000))
        (jd / "project.json").write_text(json.dumps({"source": str(work / "master.mp4")}))
        s = self.deliver(self.led, DAY, "07:00", srt=POLITICS_SRT)
        sig = CO.load(self.led, DAY, "07:00")["signals"]
        self.assertEqual(sig["render"], {"tempo": 1.1, "cards": [{"name": "Chris Wright", "at": 5.0}]})
        self.assertTrue(s)

    def test_a_past_day_draws_no_waiting_fold(self):
        yday = DAY - timedelta(days=1)
        slot_posted(self, self.gta, yday, "07:00")          # collected and posted before the coach was on
        self.on(self.gta)
        c = self.clock("10:00")
        with mock.patch.object(CO.subprocess, "run", side_effect=AssertionError("no read of a past day")):
            for _ in range(3):
                CO.tick(self.cfg, now=c.advance(minutes=10))
        s = self.slot(self.gta, "07:00", yday)
        self.assertIsNone(CO.load(self.gta, yday, "07:00"))
        self.assertEqual(CO.card_part(self.cfg, self.gta, yday, s, self.cfg.now()), "")
        # today's ready clip with no scorecard yet still says when it is measured
        s = self.deliver(self.gta, DAY, "08:45", srt=GTA_SRT)
        CO.card_path(self.gta, DAY, "08:45").unlink()
        self.assertIn("not measured yet", CO.card_part(self.cfg, self.gta, DAY, s, self.cfg.now()))
        self.assertEqual(CO.card_part(self.cfg, self.gta, DAY, s, self.cfg.now() + timedelta(days=3)), "")

    def test_a_platform_cut_that_joins_later_is_measured_again(self):
        raw = json.loads(self.cfg_path.read_text())
        raw["accounts"]["politics"]["outlets"]["instagram_reels"].update(max_seconds=1.5, cut_max_seconds=1.5)
        self.cfg_path.write_text(json.dumps(raw))
        self.cfg = C.load(self.cfg_path)
        self.led = self.cfg.account("politics")
        self.on(self.led)
        self.clock("09:50")
        self.deliver(self.led, DAY, "07:00", srt=POLITICS_SRT, yt=POLITICS_YT)
        card = CO.load(self.led, DAY, "07:00")
        self.assertIn("Instagram gets", " ".join(f["words"] for f in card["flags"]))
        CO.write(self.cfg, self.led, DAY, "07:00", {
            "scores": {"hook": 6, "keep_watching": 6, "no_sound": 7, "density": 6, "share": 6}, "fix": "Open on it.",
            "titles": ["Wright on the energy ceasefire", "Was there ever a ceasefire?", "Wright would not say"]},
            "clip-coach")
        with B.edit(self.cfg, self.led, DAY) as bd:          # Instagram's own cut joins the slot (collect, Pull)
            s = bd["slots"]["07:00"]
            s.setdefault("cuts", {})["instagram_reels"] = {"path": s["clip"]["path"], "seconds": 1.2,
                                                           "sha256": "c" * 64}
        s = self.slot(self.led, "07:00")
        self.assertTrue(CO.lengths_changed(self.led, CO.load(self.led, DAY, "07:00"), s))
        with mock.patch.object(CO.subprocess, "run", side_effect=lambda *a, **k: mock.Mock(returncode=0, stderr="")):
            CO.tick(self.cfg, now=self.clock("10:00").now())
        card = CO.load(self.led, DAY, "07:00")
        self.assertEqual(card["signals"]["length_by"]["instagram_reels"], 1.2)
        self.assertNotIn("Instagram gets", " ".join(f["words"] for f in card["flags"]))
        self.assertIsNotNone(card["written"])                 # the same clip: the written layer stays
        self.assertFalse(CO.lengths_changed(self.led, card, s))

    def test_the_window_is_the_clock_at_each_read(self):
        self.on(self.gta)
        for hh in ("07:00", "08:45"):
            self.deliver(self.gta, DAY, hh, srt=GTA_SRT)
        c = self.clock("20:44")
        calls = []

        def slow(args, *a, **kw):                            # each read takes a minute of the clock
            calls.append(c.now().strftime("%H:%M"))
            c.advance(minutes=1)
            return mock.Mock(returncode=0, stderr=SCENE_ERR, stdout="")
        with mock.patch.object(CO.subprocess, "run", slow):
            CO.tick(self.cfg, now=c.now() - timedelta(minutes=3))      # the job's `now` from before cards:tick
        self.assertEqual(calls, ["20:44"])                    # 20:45 came during the first read: no second one
        self.assertIsNone(CO.load(self.gta, DAY, "08:45")["signals"].get("scene_changes_3s"))
        self.assertFalse(CO.load(self.gta, DAY, "08:45")["signals"].get("scene_tries"))

    def test_phone_summary_keeps_its_triangle_and_the_rows_can_shrink(self):
        phone = CO.CARD_CSS[CO.CARD_CSS.index("@media (max-width:600px)"):]
        summary = phone[:phone.index("}")]
        self.assertIn("display:list-item", summary)
        self.assertNotIn("display:flex", summary)
        self.assertIn("details.cx>summary .cx-exp{display:block", CO.CARD_CSS)     # EXPECTED under "Coach"
        for no in ("gradient", "box-shadow", "text-shadow", "filter:", "cx-rule"):   # no glows, no bars
            self.assertNotIn(no, CO.CARD_CSS)


class HeadlineTest(CoachBase):
    """The headline is "Coach" + the written total + the EXPECTED records; the measured layer is one Mechanics line
    (real Politics clips measure 97 to 100 every time, so that number alone says nothing)."""

    class Led:
        maker = "politics"

    # 2026-10-20 07:00 on the Politics, as measured: talks at 0.42 s, 4.67 words a second, 7% silence
    POLITICS_SIG = {"first_word_s": 0.42, "wps_3s": 4.67, "silence_ratio": 0.073, "longest_gap_s": 1.9,
                  "longest_gap_at": 31.2, "wps_all": 3.32, "words": 269, "hook_on_screen": True, "title_chars": 67,
                  "length_s": 81.0, "length_by": {"youtube_shorts": 81.0, "instagram_reels": 41.8},
                  "length_best": {"youtube_shorts": [65.0, 90.0], "instagram_reels": [30.0, 50.0]},
                  "length_vs_best": {"youtube_shorts": 0.0, "instagram_reels": 0.0}}

    @staticmethod
    def rec(label, n, by):
        return {"label": label, "n": n, "median_rel": None,
                "by_outlet": {o: {"n": k, "median_rel": m, "under": u} for o, (k, m, u) in by.items()}}

    def test_clean_mechanics_are_one_line_with_no_flag(self):
        m, flags = CO.score(self.Led(), dict(self.POLITICS_SIG))
        self.assertGreaterEqual(m["total"], 95)
        self.assertEqual(flags, [])
        mech = CO.mechanics(self.POLITICS_SIG, m, flags)
        self.assertEqual(mech["words"], f"Mechanics {m['total']}: talks at 0.4 s, 4.7 words a second")
        self.assertEqual(mech["weak"], [])
        first = CO.mechanics(dict(self.POLITICS_SIG, first_word_s=0.0), m, flags)["words"]
        self.assertIn(": talks from the first frame, 4.7 words a second", first)
        self.assertEqual(CO.mechanics({}, {}, [])["words"], "Mechanics: not measured yet")

    def test_a_weak_mechanic_comes_first_and_flags(self):
        sig = dict(self.POLITICS_SIG, longest_gap_s=4.5, longest_gap_at=40.0, first_word_s=2.5)
        m, flags = CO.score(self.Led(), sig)
        self.assertEqual({f["key"] for f in flags}, {"slow_start", "long_gap"})
        self.assertEqual({f["kind"] for f in flags}, {"mechanics"})
        mech = CO.mechanics(sig, m, flags)
        self.assertEqual(mech["signals"], ["a 4.5 s pause at 40 s", "talks at 2.5 s"])     # the weakest first
        self.assertEqual(sorted(mech["weak"]), ["long_gap", "slow_start"])
        # only the gap is weak: it takes the first place, the opening the second
        sig = dict(self.POLITICS_SIG, longest_gap_s=4.5, longest_gap_at=40.0)
        m, flags = CO.score(self.Led(), sig)
        self.assertEqual(CO.mechanics(sig, m, flags)["signals"], ["a 4.5 s pause at 40 s", "talks at 0.4 s"])

    def test_expected_words(self):
        sig = {"records": {
            "creator": self.rec("MrBossFTW clips", 11, {"facebook_reels": (9, math.log(1.3), 2),
                                                        "youtube_shorts": (2, -1.0, 2)}),
            "format": self.rec("Gaggle clips", 4, {"facebook_reels": (2, -4.8, 2), "youtube_shorts": (2, -4.2, 2)}),
            "slot": self.rec("Clips posted between 07:00 and 08:00", 7,
                             {"facebook_reels": (3, 0.1, 1), "youtube_shorts": (2, 0.2, 0), "tiktok": (2, 0.0, 0)}),
            "length": self.rec("Clips 75s and over", 0, {})}}
        exp = CO.expected(sig)
        self.assertEqual([e["words"] for e in exp], [
            "MrBossFTW clips: usually 1.3x on Facebook, 9 posts",
            "Gaggle clips: 4 posts, too few to say",
            "Clips posted between 07:00 and 08:00: 7 posts, too few on any one platform to say",
            "Clips 75s and over: no posts to go by yet"])
        self.assertEqual([e["enough"] for e in exp], [True, False, False, False])
        self.assertEqual(exp[0]["outlets"], [{"outlet": "facebook_reels", "n": 9, "median_rel": math.log(1.3)}])
        card = {"signals": sig, "measured": {"total": 97}, "flags": []}
        self.assertEqual(CO.headline(card), {"written": None, "words": "Coach",
                                             "expected": "MrBossFTW clips: usually 1.3x on Facebook, 9 posts"})
        del sig["records"]["creator"]
        self.assertEqual(CO.headline(card)["expected"], "too few posts to say")
        card["written"] = {"scores": {"hook": 7, "keep_watching": 6, "no_sound": 8, "density": 7, "share": 5}}
        self.assertEqual(CO.headline(card)["words"], "Coach 6.6 of 10")
        self.assertEqual(CO.times(-4.5), "under 0.1x")
        self.assertEqual(CO.times(math.log(12.0)), "12x")
        text = json.dumps([e["words"] for e in exp] + [CO.headline(card)["expected"]], ensure_ascii=False)
        for bad in ("\u2014", "\u2013", "!"):
            self.assertNotIn(bad, text)

    def test_a_record_flag_needs_strong_evidence(self):
        def flags(k, med, under):
            return CO.record_flags({"records": {"format": self.rec("Gaggle clips", k,
                                                                   {"facebook_reels": (k, med, under)})}})
        self.assertEqual(flags(5, -0.9, 5), [])           # 5 posts: worded, never flagged
        self.assertEqual(flags(7, -2.0, 7), [])
        self.assertEqual(flags(8, -0.3, 8), [])           # 0.7x: no clear gap
        self.assertEqual(flags(8, -0.9, 5), [])           # mixed: 5 of 8 under
        got = flags(9, -1.6, 8)
        self.assertEqual(got, [{"key": "weak_format", "kind": "record",
                                "words": "Gaggle clips have done 0.2x the usual on Facebook over 9 posts, 8 of them "
                                         "under it."}])
        self.assertEqual(flags(9, 1.2, 0), [])            # a strong record is on the EXPECTED line, not a flag

    def test_the_card_headline_and_the_json_fields_the_others_read(self):
        self.on(self.gta)
        self.clock("06:40")
        rows = [{"pid": f"f{i}", "outlet": "facebook_reels", "creator_key": "mrbossftw", "hour": 7,
                 "rel_24h": math.log(1.3), "sent_s": 1.9} for i in range(9)]
        p = C.STATE / "gaming" / "learn" / "features.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"v": 1, "rows": rows}))
        self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"]["07:00"]["host"] = "MrBossFTW"
        s = self.slot(self.gta, "07:00")
        card = CO.measure(self.cfg, self.gta, DAY, s)
        part = CO.card_part(self.cfg, self.gta, DAY, s, self.cfg.now())
        summary = part[part.index("<summary>"):part.index("</summary>")]
        self.assertIn('Coach <span class="cx-sub">not written yet</span>', summary)
        self.assertIn('<span class="cx-k">Expected</span> MrBossFTW clips: usually 1.3x on Facebook, 9 posts',
                      summary)
        self.assertNotIn(f'Coach {card["measured"]["total"]}', summary)   # the mechanics are not the headline
        self.assertNotIn("Mechanics", summary)
        # a 2 s clip with a 4 s pause: both weak, so both lead the Mechanics line, and their flags follow it
        self.assertIn(f'<div class="cx-mech">Mechanics {card["measured"]["total"]}: a 4.0 s pause at 6 s, YouTube '
                      f'gets 2 s, best 30 to 45 s</div><ul class="cx-flags">', part)
        self.assertNotIn("cx-rule", part)
        self.assertIn("Clips posted between 07:00 and 08:00: usually 1.3x on Facebook, 9 posts", part)
        self.assertIn("Clips under 30s: usually 1.3x on Facebook, 9 posts", part)
        CO.write(self.cfg, self.gta, DAY, "07:00", good_written(), "clip-coach")
        card = CO.load(self.gta, DAY, "07:00")
        # nothing the learner or the cards read is renamed; the new fields are added
        self.assertIsInstance(card["measured"]["total"], int)
        self.assertEqual(card["written"]["scores"]["hook"], 7)
        for k in ("creator_record", "slot_record", "format_record", "records", "length_record"):
            self.assertIn(k, card["signals"])
        self.assertEqual(card["signals"]["creator_record"], {"n": 9, "median_rel": round(math.log(1.3), 3)})
        self.assertEqual(card["headline"]["words"], "Coach 6.6 of 10")
        self.assertEqual([e["what"] for e in card["expected"]], ["creator", "slot", "length"])
        self.assertTrue(card["mechanics"]["words"].startswith("Mechanics "))
        self.assertEqual(CO.total(self.gta, DAY, "07:00"), card["measured"]["total"])
        from clipfactory import cards as CD
        self.assertEqual(CD._coach_total(self.gta, DAY, "07:00"), 6.6)
        from clipfactory import learn as L
        self.assertEqual(CO.LENGTH_BANDS, L.LENGTH_BANDS)
        part = CO.card_part(self.cfg, self.gta, DAY, s, self.cfg.now())
        self.assertIn('<summary>Coach 6.6 <span class="cx-sub">of 10</span>', part)
        self.assertLess(part.index("Expected, from the records"), part.index("Written by clip-coach"))
        self.assertLess(part.index("Written by clip-coach"), part.index('class="cx-mech"'))

    def test_the_length_record_is_the_clip_itself(self):
        class A:
            platforms = ["youtube_shorts", "instagram_reels"]
        rows = [{"pid": "y1", "outlet": "youtube_shorts", "sent_s": 80.0, "rel_24h": 0.1},
                {"pid": "i1", "outlet": "instagram_reels", "sent_s": 80.0, "rel_24h": 0.2},
                {"pid": "y2", "outlet": "youtube_shorts", "sent_s": 40.0, "rel_24h": 0.3}]
        got = CO.records(A(), {"cuts": {"instagram_reels": {}}}, rows, "", "", None, 81.0)
        self.assertEqual(list(got), ["length"])
        self.assertEqual(got["length"]["label"], "Clips 75s and over")
        self.assertEqual(got["length"]["by_outlet"], {"youtube_shorts": {"n": 1, "median_rel": 0.1, "under": 0}})
        self.assertEqual(CO.records(A(), {"cuts": {"youtube_shorts": {}, "instagram_reels": {}}}, rows, "", "",
                                    None, 81.0), {})

    def test_an_older_scorecard_is_measured_again_with_its_written_layer(self):
        self.on(self.gta)
        s = self.deliver(self.gta, DAY, "07:00", srt=GTA_SRT)
        CO.write(self.cfg, self.gta, DAY, "07:00", good_written(), "clip-coach", now=self.clock("06:40").now())
        old = CO.load(self.gta, DAY, "07:00")
        for k in ("headline", "expected", "mechanics"):
            old.pop(k)
        old["signals"].pop("records")
        old["flags"] = [{"key": "weak_creator", "words": "Creator: 0.4x the usual views here (n=5)."}]   # old rule
        CO.card_path(self.gta, DAY, "07:00").write_text(json.dumps(old))
        part = CO.card_part(self.cfg, self.gta, DAY, s, self.cfg.now())     # an older file reads the new way
        self.assertIn('<summary>Coach 6.6 <span class="cx-sub">of 10</span>', part)
        self.assertIn("Mechanics ", part)
        self.assertNotIn("n=5", part)                                       # a flag from thin evidence is not shown
        with mock.patch.object(CO.subprocess, "run", side_effect=lambda *a, **k: mock.Mock(returncode=0, stderr="")):
            CO.tick(self.cfg, now=self.clock("10:00").now())
        new = CO.load(self.gta, DAY, "07:00")
        self.assertIn("records", new["signals"])
        self.assertEqual(new["written"]["titles"], good_written()["titles"])
        self.assertEqual(new["headline"]["words"], "Coach 6.6 of 10")

    def test_show_prints_what_the_card_says(self):
        self.on(self.led)
        self.clock("06:40")
        self.deliver(self.led, DAY, "07:00", srt=POLITICS_SRT, yt=POLITICS_YT)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            REG.run_cli(self.cfg, "coach", ["show", "politics", "--date", DAY.isoformat()])
        text = out.getvalue()
        for want in ("the card says:", "    Coach (not written yet)", "    Expected: too few posts to say",
                     "      Clips posted between 07:00 and 08:00: no posts to go by yet", "    Mechanics "):
            self.assertIn(want, text)


class SourceTest(unittest.TestCase):
    def test_words_and_prompt(self):
        src = (ROOT / "clipfactory" / "coach.py").read_text()
        prompt = (ROOT / "prompts" / "coach.md").read_text()
        for text in (src, prompt):
            self.assertNotIn("\u2014", text)
            self.assertNotIn("\u2013", text)
        self.assertNotIn("!", prompt)
        self.assertNotIn("urllib", src)                       # no network in the coach
        for rule in ("coach pending --date today", "coach card", "coach write", "7 is good", "9 is rare",
                     "10 is\nnever", "transcript", "verified", "extras poll", "shift begin", "never render"):
            self.assertIn(rule.replace("\n", " ") if rule != "10 is\nnever" else "10 is", prompt.replace("\n", " "))
        flat = " ".join(prompt.split())
        for rule in ("Mechanics are usually fine", "The records", "too few to say", "with its n",
                     "judge the moment, the hook words and the payoff", "Clean mechanics do not lift a score",
                     "never invent a fact", "Use only words that are in the transcript, the slate",
                     "name only the people the card lists as verified", "The records never go into a title"):
            self.assertIn(rule, flat)
        self.assertNotIn("Coach 64", prompt)
        from clipfactory import hermes as H
        self.assertEqual(H.retired_words(prompt), [])


class VerdictTest(unittest.TestCase):
    """The coach's one word on a clip (the loops thesis 10.30, item 1): optional; PASS, FIX or KILL; a KILL says why."""

    def test_verdict(self):
        self.assertNotIn("verdict", CO.validate(good_written()))
        self.assertEqual(CO.validate(dict(good_written(), verdict=" kill "))["verdict"], "KILL")
        self.assertEqual(CO.validate(dict(good_written(), verdict="Fix"))["verdict"], "FIX")
        for bad in ("MAYBE", 3):
            with self.assertRaises(SystemExit):
                CO.validate(dict(good_written(), verdict=bad))
        with self.assertRaises(SystemExit):
            CO.validate(dict(good_written(), verdict="KILL", why=""))


if __name__ == "__main__":
    unittest.main()
