import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"]="1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())
"""The Politics fixes of 2026-10-17 evening: the posting day after midnight (LDG-2), the Instagram cut's checks
(LDG-5) and the slot's segment (LDG-11). Throwaway folders, stand-in engines, no network."""
import argparse
import json
import shutil
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import igcut as IG     # noqa: E402
from clipfactory import outlets as O    # noqa: E402

DAY = date(2031, 5, 6)


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-politicsfix-"))
        cls.old_state, C.STATE = C.STATE, cls.tmp / "state"
        cls.old_tokens = O.TOKENS
        O.TOKENS = cls.tmp / "keys"
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(cls.tmp / "drafts")
        for a in raw["accounts"].values():
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 2, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "instagram_reels": {"mode": "api", "min_seconds": 1, "max_seconds": 1.6},
                            "tiktok": {"mode": "export"}}
        raw["accounts"]["politics"]["account_dir"] = str(cls.tmp / "account")
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.main, cls.short = cls.tmp / "main.mp4", cls.tmp / "short.mp4"
        clip(cls.main, 2.5)
        clip(cls.short, 1.2)

    @classmethod
    def tearDownClass(cls):
        C.STATE = cls.old_state
        O.TOKENS = cls.old_tokens
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        for d in ("state", "drafts", "delivered", "account", "clipwork", "engine"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.acct = self.cfg.account("politics")

    def at(self, hh: int, mm: int = 0, day: date = DAY) -> None:
        """Pin the factory's clock to day hh:mm local."""
        tz = self.cfg.tz()
        self.cfg.now = lambda: datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)

    def job(self, template: str = "room", **clip_extra) -> Path:
        """A Politics job, its work folder and its day schedule (the skill's Publish/schedule.json)."""
        acct_dir = self.acct.account_dir
        job = acct_dir / "Jobs" / "Some Hearing 01.02.31"
        work = self.tmp / "clipwork" / "hearing0102"
        for d in (job, work, acct_dir / "Publish"):
            d.mkdir(parents=True, exist_ok=True)
        (work / "master.mp4").write_bytes(b"master")
        (work / "project.json").write_text(json.dumps({"goal": "both", "out_dir": str(job)}))
        (job / "project.json").write_text(json.dumps({"source": str(work / "master.mp4"), "out_dir": str(job)}))
        (job / "cast.json").write_text('{"people": []}')
        entry = {"rank": 1, "slug": "the-exchange", "start": 100.0, "end": 190.0, "hook": "SEVEN ASKS",
                 "template": template, "cards": [{"who": "a", "at": 13, "hold": 4}]}
        entry.update(clip_extra)
        (job / "slate.json").write_text(json.dumps({"clips": [entry]}))
        (acct_dir / "Publish" / "schedule.json").write_text(json.dumps(
            {DAY.isoformat(): {"07:00": {"job": job.name, "slug": "the-exchange"}}}))
        return job

    def deliver(self, hhmm: str = "0700", slug: str = "the-exchange") -> Path:
        d = self.acct.delivered_dirs(DAY)[0] / f"{hhmm} {slug}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.main, d / f"01-{slug}.mp4")
        for p in ("youtube", "tiktok_reach", "facebook", "instagram"):
            (d / f"{p}.txt").write_text(f"TITLE\nThe exchange at {hhmm}\n\nCAPTION\nWords for {p}.\n")
        return d


class PostingDay(Base):
    """LDG-2: a command with no date after midnight still means the day the night run is filling."""

    def test_no_date_before_seven_is_today(self):
        # POSTING_DAY_STARTS 7 since the night shift (2026-10-19): it fills today's boards until 06:15
        for hh, want in ((0, DAY), (1, DAY), (4, DAY), (5, DAY), (6, DAY), (7, date(2031, 5, 7)), (19, date(2031, 5, 7)),
                         (23, date(2031, 5, 7))):
            self.at(hh, 30)
            self.assertEqual(C.parse_day(None, self.cfg), want, f"{hh}:30")
            self.assertEqual(C.parse_day("", self.cfg), want, f"{hh}:30")

    def test_named_days_keep_their_meaning(self):
        self.at(1, 0)
        self.assertEqual(C.parse_day("tomorrow", self.cfg), date(2031, 5, 7))
        self.assertEqual(C.parse_day("today", self.cfg), DAY)
        self.assertEqual(C.parse_day("2031-05-09", self.cfg), date(2031, 5, 9))

    def test_a_note_after_midnight_lands_on_the_same_board(self):
        import factory as F
        self.at(19, 30, DAY - timedelta(days=1))         # the night run starts the evening before
        F.cmd_note(self.cfg, argparse.Namespace(account="politics", date=None, text="evening"))
        self.assertEqual(B.read(self.acct, DAY)["report"], "evening")
        self.at(1, 15)                                   # and passes midnight
        F.cmd_note(self.cfg, argparse.Namespace(account="politics", date=None, text="CAST TO CONFIRM: x"))
        self.assertEqual(B.read(self.acct, DAY)["report"], "CAST TO CONFIRM: x")
        self.assertIsNone(B.read(self.acct, DAY + timedelta(days=1)))

    def test_the_hourly_collect_after_midnight_takes_the_days_clips(self):
        import factory as F
        self.deliver("0700")
        self.at(1, 15)
        F.cmd_collect(self.cfg, argparse.Namespace(account="politics", date=None, frm=None, quiet=True))
        self.assertEqual(B.read(self.acct, DAY)["slots"]["07:00"]["status"], B.READY)


class Segment(Base):
    """LDG-11: a Politics board deals 'any' and the slot takes the delivered clip's segment."""

    def test_new_politics_board_deals_any_and_gta_keeps_its_lanes(self):
        with B.edit(self.cfg, self.acct, DAY) as bd:
            fmts = {s["format"] for s in bd["slots"].values()}
        self.assertEqual(fmts, {"any"})
        gta = self.cfg.account("gaming")
        with B.edit(self.cfg, gta, DAY) as bd:
            self.assertNotIn("any", {s["format"] for s in bd["slots"].values()})

    def test_collect_writes_the_clips_segment(self):
        self.job(template="gaggle")
        self.deliver("0700")
        K.collect(self.cfg, self.acct, DAY)
        s = B.read(self.acct, DAY)["slots"]["07:00"]
        self.assertEqual(s["format"], "gaggle")
        self.assertTrue(any("the clip's segment" in n for n in s["notes"]))

    def test_no_schedule_leaves_the_format(self):
        self.deliver("0700")
        K.collect(self.cfg, self.acct, DAY)
        self.assertEqual(B.read(self.acct, DAY)["slots"]["07:00"]["format"], "any")


@unittest.skip("igcut's stricter checks are held until preflight reads a platform cut's odd rhythm as a WARN "
               "(review 2026-10-17): cf_igcut.py is not deployed")
class InstagramCut(Base):
    """LDG-5: the cut is checked by the skill's preflight before and grade after; a late start must re-time."""

    def engine(self, preflight: str = "print('ALL CLEAR')\n", grade: str = "print('No BAD findings.')\n") -> Path:
        e = self.tmp / "engine"
        e.mkdir(exist_ok=True)
        (e / "build_all.py").write_text(
            "import json, os, shutil, sys\n"
            "w = os.environ['CLIP_WORK']\n"
            "p = json.load(open(os.path.join(w, 'project.json')))\n"
            "c = json.load(open(os.path.join(w, 'slate.json')))['clips'][0]\n"
            "open(os.path.join(w, 'order.txt'), 'a').write('render\\n')\n"
            "json.dump({'project': p, 'clip': c}, open(os.path.join(w, 'seen.json'), 'w'))\n"
            "os.makedirs(os.path.join(p['out_dir'], 'clips'), exist_ok=True)\n"
            f"shutil.copy({str(self.short)!r}, os.path.join(p['out_dir'], 'clips', '01-' + c['slug'] + '.mp4'))\n")
        for name, body in (("preflight.py", preflight), ("grade.py", grade)):
            (e / name).write_text(
                "import os, sys\n"
                f"open(os.path.join(os.environ['CLIP_WORK'], 'order.txt'), 'a').write({name!r} + ' ' + sys.argv[1] + '\\n')\n"
                + body)
        return e

    def cut(self, engine: Path, **kw):
        old = IG.ENGINE
        IG.ENGINE = engine
        try:
            return IG.render(self.cfg, self.acct, DAY, "the-exchange", log=lambda *a: None,
                             **{"python": sys.executable, **kw})
        finally:
            IG.ENGINE = old

    def work(self) -> Path:
        return next(self.tmp.glob("clipwork/hearing0102-ig-*"))

    def test_checks_run_around_the_render_and_mark_the_cut(self):
        self.job()
        folder = self.deliver("0700")
        e = self.engine(grade="print('[ WARN ] CARDS      \\'Kash Patel\\' at +32.5s - pull a frame at +34.0s')\n")
        out = self.cut(e, segments="100-130,170-190")
        w = self.work()
        self.assertEqual((w / "order.txt").read_text().split("\n")[:3],
                         ["preflight.py the-exchange-ig", "render", "grade.py the-exchange-ig"])
        self.assertEqual(json.loads((w / "seen.json").read_text())["project"]["cut_for"], "instagram_reels")
        self.assertTrue((folder / "instagram.mp4").exists())
        self.assertTrue(any("LOOK:" in ln and "pull a frame at +34.0s" in ln for ln in out), out)

    def test_a_preflight_failure_refuses_before_anything_renders(self):
        self.job()
        folder = self.deliver("0700")
        e = self.engine(preflight="print('    [FAIL] CAST     patel: verified was written by a bot')\n"
                                  "print('NOT READY: the-exchange-ig.')\nsys.exit(1)\n")
        with self.assertRaises(SystemExit) as cm:
            self.cut(e, end=150.0)
        self.assertIn("preflight refused the cut", str(cm.exception))
        self.assertIn("[FAIL] CAST", str(cm.exception))
        self.assertFalse((self.work() / "seen.json").exists())          # never rendered
        self.assertFalse((folder / "instagram.mp4").exists())

    def test_a_grade_failure_keeps_the_cut_out_of_the_delivery(self):
        self.job()
        folder = self.deliver("0700")
        e = self.engine(grade="print('[ BAD  ] FLASH      1 shot(s) under 0.30s')\nsys.exit(1)\n")
        with self.assertRaises(SystemExit) as cm:
            self.cut(e, end=150.0)
        self.assertIn("grade refused the cut", str(cm.exception))
        self.assertFalse((folder / "instagram.mp4").exists())

    def test_a_later_start_must_retime_what_the_clip_times(self):
        self.job(poster_at=14.0, hook2="NO", hook2_at=26.0)
        self.deliver("0700")
        e = self.engine()
        with self.assertRaises(SystemExit) as cm:              # cards and poster_at and hook2_at are the clip's
            self.cut(e, segments="130-160,170-190")
        msg = str(cm.exception)
        for k in ("cards", "poster_at", "hook2_at"):
            self.assertIn(k, msg)
        self.assertEqual(list(self.tmp.glob("clipwork/hearing0102-ig-*")), [])   # refused before the clone
        with self.assertRaises(SystemExit) as cm:              # hook2 dropped: only cards and poster_at
            self.cut(e, segments="130-160,170-190", hook2=False, sets=['cards=[{"who": "a", "at": 2.0}]'])
        self.assertIn("poster_at", str(cm.exception))
        self.assertNotIn("hook2_at", str(cm.exception))
        out = self.cut(e, segments="130-160,170-190", hook2=False,
                       sets=['cards=[{"who": "a", "at": 2.0}]', "poster_at=3"])
        self.assertTrue(any("Instagram cut" in ln for ln in out), out)

    def test_the_same_start_needs_no_retiming(self):
        self.job(poster_at=14.0)
        self.deliver("0700")
        out = self.cut(self.engine(), segments="100-130,170-190")
        self.assertTrue(any("Instagram cut" in ln for ln in out), out)

    def test_the_default_interpreter_is_the_main_renders(self):
        self.assertEqual(IG.ENGINE_PYTHON, "/usr/bin/python3")
        self.job()
        self.deliver("0700")
        e = self.engine()
        seen = []
        real = IG.subprocess.run

        def spy(cmd, *a, **k):
            if isinstance(cmd, list) and cmd and str(cmd[-2] if len(cmd) > 1 else "").endswith(".py"):
                seen.append(cmd[0])
            return real(cmd, *a, **k)
        IG.subprocess.run = spy
        old_env = os.environ.pop("CLIP_ENGINE_PYTHON", None)
        try:
            old = IG.ENGINE
            IG.ENGINE = e
            try:
                IG.render(self.cfg, self.acct, DAY, "the-exchange", end=150.0, log=lambda *a: None)
            finally:
                IG.ENGINE = old
        finally:
            IG.subprocess.run = real
            if old_env is not None:
                os.environ["CLIP_ENGINE_PYTHON"] = old_env
        want = IG.ENGINE_PYTHON if Path(IG.ENGINE_PYTHON).exists() else shutil.which("python3")
        self.assertEqual(seen[:3], [want] * 3)


if __name__ == "__main__":
    unittest.main()
