import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""The spare-clip bench (build3/bench, clipfactory/bench.py), offline, on throwaway folders and tiny clips (<= 3 s,
108x192, nice 19). add: what it copies, and every refusal writes nothing (a provisional or bot-checked Politics cast
included). fill: open/failed slots 90+ minutes away only, oldest-expiring first, one source once a day, the 04:50-05:15
window and the 05:15 stop, the wait for the Mac, a REACTIONS slot held before 03:30, the SHA-256 refusal, GTA's words
written for the posting day, the Politics's day rule (the real rotation.py, over the boards and schedule.json), an
iCloud "NAME 2" copy never taken. Then what happens after (reconcile), the card's Bench tag, the morning count, the
Stats field, the bench-fill agent, igcut --bench and bench render (no render: a stand-in run.py).
The night shift (2026-10-19) moved the fill to 06:18, writing between 05:50 and 06:25 (quiet by 06:22): these logic
tests keep the 05:05 clock they were written on (Base patches the window back to 04:50-05:15, so 07:00 is still 115
minutes away), and tests/test_night_shift.py pins the new clock."""
import contextlib
import fcntl
import io
import json
import shutil
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipfactory import bench as BN     # noqa: E402
from clipfactory import board as B      # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import place as PL     # noqa: E402
from test_place import PlaceBase        # noqa: E402

DAY = date(2030, 1, 7)
LAUNCH = date(2030, 3, 2)
GTA_PLATS = ("youtube", "tiktok", "reels", "facebook")
POLITICS_WORDS = ("youtube.txt", "tiktok_reach.txt", "facebook.txt", "instagram.txt")
_COLORS = iter(["red", "green", "blue", "yellow", "cyan", "magenta", "white", "gray", "orange", "purple", "pink",
                "brown", "navy", "olive", "teal", "maroon", "lime", "coral", "gold", "silver", "khaki", "plum", "salmon",
                "tan", "violet", "beige", "azure", "ivory", "lavender", "linen", "orchid", "peru", "sienna", "snow",
                "wheat", "tomato", "thistle", "seashell", "moccasin", "honeydew"] * 40)


def clip(path: Path, seconds: float = 2.0, color: str = "") -> Path:
    """A tiny clip; every call a new colour, so no two clips share a SHA-256 unless asked."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["nice", "-n", "19", "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c={color or next(_COLORS)}:s=108x192:d={seconds}", "-f", "lavfi", "-i",
                    "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


# the skill's copy engine, as Publish/day_folder.py exposes it (clip_entry, gc.packs, paste_files): words for the
# posting date, with its countdown, like gtacopy's "N days to GTA 6."
FAKE_GTA_DAY_FOLDER = '''
import json
from datetime import date
from pathlib import Path
JOBS = Path(__file__).resolve().parent.parent / "Jobs"
SKILL_WORK = Path(%(work)r)
LAUNCH = date(2030, 3, 2)
class _GC:
    @staticmethod
    def packs(clip, cfg, d):
        n = (LAUNCH - d).days
        cd = (" %%d days to GTA 6." %% n) if n > 0 else ""
        return {p: {"title": clip["hook"] + " " + p, "paste": "engine words for " + p + "." + cd + " #gta6",
                    "question_default": bool(clip.get("stock_q"))} for p in ("youtube", "tiktok", "reels", "facebook")}
gc = _GC()
def slate_paths(job):
    return [SKILL_WORK / job / "slate.json", JOBS / job / "_project" / "slate.json"]
def clip_entry(job, slug):
    for p in slate_paths(job):
        if p.exists():
            sl = json.loads(p.read_text())
            c = next((c for c in sl["clips"] if c["slug"] == slug), None)
            if c is not None:
                return c, json.loads(p.with_name("project.json").read_text())
    return {}, {}
def paste_files(dst, clip, cfg, packs):
    for p, k in packs.items():
        (Path(dst) / (p + ".txt")).write_text(k["title"] + "\\n\\n" + k["paste"] + "\\n")
'''

VERIFIED = "Andrew, 2030-01-01: watched the master at 0:04, that is her"


def _engine_scripts() -> Path:
    for base in (ROOT.parent, Path(os.path.expanduser("~"))):
        p = base / ".claude" / "skills" / "viral-clip-engine" / "scripts"
        if (p / "rotation.py").is_file() and (p / "cast.py").is_file():
            return p
    return Path("/nonexistent")


ENGINE = _engine_scripts()


class Base(PlaceBase):
    def setUp(self):
        super().setUp()
        g, led = self.raw["accounts"]["gaming"], self.raw["accounts"]["politics"]
        # the plain ten slots these tests were written on: the live Gaming counts its day by dated "volume" rules
        # since 2026-10-29 (6, then 8 from 11-19), which a 2030 test day would read
        g.pop("volume", None)
        g["daily_target"] = len(g["slots"])
        g["bench"] = {"fill": True, "target": 6, "low": 4, "max": 8, "evergreen_days": 30, "until_cap": "2030-03-01",
                      "lead_minutes": 90, "launch": LAUNCH.isoformat()}
        led["bench"] = {"fill": True, "target": 6, "low": 4, "max": 8, "evergreen_days": 90, "lead_minutes": 90}
        led["outlets"]["instagram_reels"].update(min_seconds=1, max_seconds=1.5)   # the Politics's Instagram cut, scaled
        led["outlets"]["youtube_shorts"].update(cut_min_seconds=1, cut_max_seconds=1.5)   # and its YouTube cut
        self.reload()
        self.politics = self.cfg.account("politics")
        self.t = self.cfg.slot_time(DAY, "05:05")
        # the clock these tests were written on (the night shift moved it: test_night_shift.py pins the new one)
        for k, v in (("NOT_BEFORE", "04:50"), ("QUIET_BY", "05:12"), ("STOP_AT", "05:15"), ("REPORT_TAG", "Bench 05:05:")):
            patch = mock.patch.object(BN, k, v)
            patch.start()
            self.addCleanup(patch.stop)
        self.addday = datetime(2030, 1, 3, 12, 0, tzinfo=self.cfg.tz())
        pub = self.acct.account_dir / "Publish"
        pub.mkdir(parents=True, exist_ok=True)
        (pub / "day_folder.py").write_text(FAKE_GTA_DAY_FOLDER % {"work": str(self.acct.skill_dir / "work")})

    # ------------------------------------------------------------------ fixtures
    def gta_job(self, job="bench-20300103-dev", slug="how-gta6-was-built", url="https://www.youtube.com/watch?v=OGmDFz5mZ5A",
                seconds=2.0, hook="ROCKSTAR REBUILT THE ENGINE", template="underlay", span=(100.0, 140.0), **clip_kw):
        work = self.acct.skill_dir / "work" / job
        work.mkdir(parents=True, exist_ok=True)
        out = self.acct.account_dir / "Jobs" / job
        c = clip(out / "clips" / f"01-{slug}.mp4", seconds)
        (out / "clips" / f"01-{slug}.jpg").write_bytes(b"jpg " + slug.encode())
        (out / "captions").mkdir(parents=True, exist_ok=True)
        (out / "captions" / f"01-{slug}.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTHE ENGINE.\n")
        ent = {"slug": slug, "hook": hook, "template": template, "start": span[0], "end": span[1], **clip_kw}
        sl = json.loads((work / "slate.json").read_text()) if (work / "slate.json").exists() else {"clips": []}
        sl["clips"] = [x for x in sl["clips"] if x["slug"] != slug] + [ent]
        (work / "slate.json").write_text(json.dumps(sl))
        (work / "project.json").write_text(json.dumps({"out_dir": str(out), "source_url": url, "rights": "unverified",
                                                       "source_title": "Inside the Development of GTA 6",
                                                       "host": {"name": "TGG"}, "_run_template": template}))
        return job, slug, c

    def politics_job(self, job="Banking 09.30.26", slug="have-you-returned", template="room", url="https://www.youtube.com/watch?v=hearing0001",
                   cast_verified=VERIFIED, provisional=False, seconds=2.0, mode="fit", caption="politics-a",
                   hook="SMITH: HAVE YOU RETURNED ONE NICKEL?", fmt="hearing", sched=None):
        jd = self.politics.account_dir / "Jobs" / job
        c = clip(jd / "clips" / f"01-{slug}.mp4", seconds)
        (jd / "clips" / f"01-{slug}.jpg").write_bytes(b"jpg " + slug.encode())
        (jd / "captions").mkdir(parents=True, exist_ok=True)
        (jd / "captions" / f"01-{slug}.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nONE NICKEL?\n")
        st = jd / "staged" / f"01-{slug}"
        st.mkdir(parents=True, exist_ok=True)
        for n in POLITICS_WORDS:
            (st / n).write_text(f"{hook}\n\nThe senator asks again. {slug} on {n}\n")
        sl = json.loads((jd / "slate.json").read_text()) if (jd / "slate.json").exists() else {"clips": []}
        sl["clips"] = [x for x in sl["clips"] if x["slug"] != slug] + [{
            "slug": slug, "template": template, "hook": hook, "mode": mode, "caption_style": caption,
            "cards": [{"who": "smith", "at": 1.0}], "start": 10.0, "end": 12.0, "format": fmt}]
        (jd / "slate.json").write_text(json.dumps(sl))
        (jd / "project.json").write_text(json.dumps({"source_url": url, "source_channel": "Senate Banking",
                                                     "rights": "owned", "source_title": "Oversight hearing",
                                                     "hearing_date": "2026-09-30", "format": fmt}))
        person = {"id": "smith", "name": "Jane Smith", "title": "SENATOR", "role": "member",
                  "verified": cast_verified}
        if provisional:
            person["verified"] = None
            person["provisional"] = {"named_as": "Smith", "source_title": "Oversight hearing", "frames": "0:04"}
        (jd / "cast.json").write_text(json.dumps({"hearing": {}, "people": [person]}))
        if sched:
            p = self.politics.account_dir / "Publish" / "schedule.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            s = json.loads(p.read_text()) if p.exists() else {}
            s.setdefault(sched[0], {})[sched[1]] = {"job": job, "slug": slug}
            p.write_text(json.dumps(s))
        return job, slug, c

    def add(self, acct, what, **kw):
        kw.setdefault("now", self.addday)
        kw.setdefault("until", "2030-02-20")
        return BN.add(self.cfg, acct, what, **kw)

    def added(self, acct, what, **kw):
        ok, lines = self.add(acct, what, **kw)
        self.assertTrue(ok, lines)
        return next(i for i in BN.items(acct) if i["id"].endswith(kw.get("slug") or Path(what).name.split(" ", 1)[-1]))

    def refused(self, out, words):
        ok, lines = out
        self.assertFalse(ok, lines)
        self.assertIn("REFUSED, nothing written", lines[0])
        self.assertIn(words, lines[0])

    def politics_ready(self, until="2030-02-20", **kw):
        """A Politics spare with its Instagram cut: added (needs-cut), the cut made into it, refreshed: ready."""
        job, slug, _ = self.politics_job(**kw)
        it = self.added(self.politics, job, slug=slug, until=until)
        self.assertEqual(it["state"], BN.NEEDS_CUT)
        clip(Path(it["_dir"]) / "instagram.mp4", 1.2)
        BN.refresh(self.cfg, self.politics, it["id"])
        it = next(i for i in BN.items(self.politics) if i["id"] == it["id"])
        self.assertEqual(it["state"], BN.READY, it.get("needs"))
        return it

    def fill(self, which="gaming", at=None, **kw):
        clock = kw.pop("clock", None) or (lambda: at or self.t)
        kw.setdefault("busy", lambda: [])
        kw.setdefault("sleep", lambda s: None)
        kw.setdefault("say", lambda s: None)
        kw.setdefault("quiet_s", 0)                     # the quiet rule has its own test (QuietAndPlanned)
        return BN.fill(self.cfg, which, kw.pop("day", DAY), now=clock, **kw)

    def slot(self, k, acct=None, day=DAY):
        return ((B.read(acct or self.acct, day) or {}).get("slots") or {}).get(k)

    def item(self, acct, ident):
        return next(i for i in BN.items(acct) if i["id"] == ident)


# ------------------------------------------------------------------------------------------------------ add
class Add(Base):
    def test_a_gta_job_is_copied_with_its_word_templates(self):
        job, slug, src = self.gta_job()
        before = {p.name: p.stat().st_mtime_ns for p in src.parent.iterdir()}
        it = self.added(self.acct, job, slug=slug, why="a development story", by="bench-builder")
        d = Path(it["_dir"])
        self.assertEqual(d.parent, C.STATE / "gaming" / "bench")
        self.assertEqual(it["id"], f"{it['made_on'].replace('-', '')}-{slug}")
        self.assertEqual((it["state"], it["format"], it["job"], it["words_from"]), (BN.READY, "underlay", job, "skill"))
        self.assertEqual(it["sha256"], B.sha256_file(src))
        self.assertEqual(B.sha256_file(d / f"{slug}.mp4"), it["sha256"])
        self.assertTrue((d / f"{slug}.jpg").is_file() and (d / f"{slug}.srt").is_file())
        self.assertEqual(it["source"]["urls"], ["https://www.youtube.com/watch?v=OGmDFz5mZ5A"])
        self.assertEqual((it["source"]["host"], it["source"]["rights"]), ("TGG", "unverified"))
        self.assertEqual(it["span"], [100.0, 140.0])
        yt = (d / "youtube.txt").read_text()
        self.assertIn(f"{(LAUNCH - date(2030, 1, 3)).days} days to GTA 6.", yt)           # written for the add day
        self.assertIn(BN.DAYS_TOKEN, (d / "youtube.txt.tmpl").read_text())
        self.assertNotIn("days to GTA 6", (d / "youtube.txt.tmpl").read_text())
        self.assertEqual({p.name: p.stat().st_mtime_ns for p in src.parent.iterdir()}, before)   # copied, never moved
        self.assertEqual(it["evergreen_until"], "2030-02-20")

    def test_check_writes_nothing(self):
        job, slug, _ = self.gta_job()
        ok, lines = self.add(self.acct, job, slug=slug, check=True)
        self.assertTrue(ok, lines)
        self.assertIn("--check: nothing written", lines[-1])
        self.assertEqual(BN.items(self.acct), [])

    def test_refusals_write_nothing(self):
        job, slug, _ = self.gta_job(seconds=3.5, slug="too-long")
        self.refused(self.add(self.acct, job, slug=slug), "takes 1 to 3 seconds")
        job, slug, _ = self.gta_job(slug="counting", count_card={"number": 54})
        self.refused(self.add(self.acct, job, slug=slug), "countdown card")
        job, slug, _ = self.gta_job(slug="stock-question", stock_q=True)
        self.refused(self.add(self.acct, job, slug=slug), "stock one")
        job, slug, _ = self.gta_job(slug="dated-hook", hook="THE TRAILER JUST DROPPED")
        self.refused(self.add(self.acct, job, slug=slug), "'just dropped' in the hook")
        job, slug, _ = self.gta_job(slug="past-cap")
        self.refused(self.add(self.acct, job, slug=slug, until="2030-03-05"), "past 2030-03-01")
        self.refused(self.add(self.acct, job, slug="not-on-slate"), "has no clip 'not-on-slate'")
        self.assertEqual(BN.items(self.acct), [])
        self.assertFalse((C.STATE / "gaming" / "bench").exists() and any(
            p for p in (C.STATE / "gaming" / "bench").iterdir() if not p.name.startswith(".")))

    def test_a_dated_word_may_stay_with_a_reason(self):
        job, slug, _ = self.gta_job(slug="today-intro", hook="TODAY WE LOOK AT THE ENGINE")
        self.refused(self.add(self.acct, job, slug=slug), "'today' in the hook")
        it = self.added(self.acct, job, slug=slug, allow={"today": "the creator's intro, not news"})
        self.assertEqual(it["checks"]["dated_words_allowed"], {"today": "the creator's intro, not news"})

    def test_a_clip_on_a_board_or_on_the_bench_is_refused(self):
        job, slug, src = self.gta_job()
        self.added(self.acct, job, slug=slug)
        self.refused(self.add(self.acct, job, slug=slug), "already on the bench")
        job2, slug2, src2 = self.gta_job(job="bench-20300103-two", slug="second-clip", span=(500.0, 540.0))
        ok, lines = PL.place(self.cfg, self.acct, DAY, "14:00", clip=src2, words=self.gta_words(),
                             now=self.cfg.slot_time(DAY, "06:00"))
        self.assertTrue(ok, lines)
        self.refused(self.add(self.acct, job2, slug=slug2), "already on a board: Gaming 2030-01-07 14:00")

    def test_a_moment_shared_with_a_board_clip_of_the_job_is_refused(self):
        job, slug, _ = self.gta_job(span=(100.0, 140.0))
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["10:30"].update(status=B.READY, slug="night-pick", job=job, pick_span=[110.0, 150.0])
        self.refused(self.add(self.acct, job, slug=slug), "shares 75% of its seconds")

    def test_the_bench_has_a_ceiling(self):
        self.raw["accounts"]["gaming"]["bench"]["max"] = 1
        self.reload()
        job, slug, _ = self.gta_job()
        self.added(self.acct, job, slug=slug)
        job2, slug2, _ = self.gta_job(job="bench-20300103-two", slug="second-clip")
        self.refused(self.add(self.acct, job2, slug=slug2), "bench.max 1")

    def gta_words(self) -> Path:
        d = self.tmp / "words-plain"
        d.mkdir(exist_ok=True)
        for p in GTA_PLATS:
            (d / f"{p}.txt").write_text(f"TITLE {p}\n\nplain words #gta6\n")
        return d

    def test_an_icloud_copy_is_never_taken(self):
        d = self.tmp / "superseded" / "0700 big-crash"
        orig = clip(d / "01-big-crash.mp4")
        dup = clip(d / "01-big-crash 2.mp4")                    # iCloud's copy, other bytes, sorts first
        for p in GTA_PLATS:
            (d / f"{p}.txt").write_text(f"BIG CRASH {p}\n\nwords #gta6\n")
        it = self.added(self.acct, str(d), fmt="gameplay", made_on="2030-01-02")
        self.assertEqual(it["sha256"], B.sha256_file(orig))
        self.assertNotEqual(it["sha256"], B.sha256_file(dup))
        self.assertEqual(it["words_from"], "template")
        only = self.tmp / "superseded" / "0845 lone-copy"
        clip(only / "01-lone-copy 2.mp4")
        for p in GTA_PLATS:
            (only / f"{p}.txt").write_text(f"LONE {p}\n\nwords #gta6\n")
        self.refused(self.add(self.acct, str(only), fmt="gameplay"), "iCloud copy")
        self.refused(self.add(self.acct, str(d.parent / "0700 big-crash 2"), fmt="gameplay"), "no folder")

    def test_a_politics_cast_must_be_verified_by_a_person(self):
        job, slug, _ = self.politics_job(job="Prov 10.01.26", slug="prov-clip", provisional=True)
        self.refused(self.add(self.politics, job, slug=slug), "the cast is provisional")
        job, slug, _ = self.politics_job(job="Bot 10.01.26", slug="bot-clip",
                                       cast_verified="checked by Claude, NEEDS ANDREW'S LOOK")
        self.refused(self.add(self.politics, job, slug=slug), "a bot wrote verified")
        job, slug, _ = self.politics_job(job="Empty 10.01.26", slug="empty-clip", cast_verified="")
        self.refused(self.add(self.politics, job, slug=slug), "verified is empty")
        (self.politics.account_dir / "Jobs" / job / "cast.json").unlink()
        self.refused(self.add(self.politics, job, slug=slug), "the cast is none")
        self.assertEqual(BN.items(self.politics), [])

    def test_a_politics_delivery_with_a_cast_check_is_refused(self):
        # cast-check.json lists who was unchecked at delivery; refused while the job's cast.json still says so (a
        # person checked since: test_breaking.BenchBump, the file is stale and the clip goes on the bench)
        job, slug, _ = self.politics_job(provisional=True)
        d = self.tmp / "delivered" / f"0845 {slug}"
        clip(d / f"01-{slug}.mp4")
        for n in POLITICS_WORDS:
            (d / n).write_text("HOOK\n\nwords\n")
        (d / "cast-check.json").write_text(json.dumps({"job": job, "people": [{"id": "smith", "name": "Jane Smith"}]}))
        self.refused(self.add(self.politics, str(d)), "cast-check.json beside the clip")

    def test_a_politics_spare_waits_for_its_instagram_cut(self):
        it = self.politics_ready()
        self.assertEqual(set(it["files"]["cuts"]), {"instagram_reels"})
        self.assertEqual(it["cast"]["status"], "verified")
        self.assertEqual(it["format"], "room")


# ------------------------------------------------------------------------------------------------------ fill
class Fill(Base):
    def spare(self, slug, until="2030-02-20", job=None, url=None, made_on="2030-01-03", **kw):
        job = job or f"bench-20300103-{slug}"
        url = url or f"https://www.youtube.com/watch?v={slug[:11].ljust(11, 'x')}"
        self.gta_job(job=job, slug=slug, url=url, **kw)
        return self.added(self.acct, job, slug=slug, until=until, made_on=made_on)

    def test_only_open_or_failed_slots_90_minutes_away_oldest_expiring_first(self):
        self.raw["accounts"]["gaming"]["slots"] = ["06:34", "07:00", "08:45", "10:30", "12:15", "14:00", "15:45",
                                                      "17:30", "19:15", "21:00"]
        self.reload()
        late = self.spare("expires-late", until="2030-02-25")
        soon = self.spare("expires-soon", until="2030-01-20")
        with B.edit(self.cfg, self.acct, DAY) as bd:
            sl = bd["slots"]
            for k in sl:
                sl[k]["status"] = B.READY if k not in ("06:34", "07:00", "08:45") else sl[k]["status"]
            sl["08:45"]["status"] = B.FAILED
            sl["10:30"]["status"] = B.MAKING
            sl["12:15"]["status"] = B.REFUSED
            sl["14:00"]["status"] = B.DECLINED
            sl["15:45"].update(status=B.SCHEDULED, signoff={"by": "Andrew"})
            sl["17:30"]["status"] = B.APPROVED
        before = {k: json.dumps(v, sort_keys=True) for k, v in B.read(self.acct, DAY)["slots"].items()}
        lines = self.fill(at=self.cfg.slot_time(DAY, "05:05"))
        bd = B.read(self.acct, DAY)["slots"]
        self.assertEqual(bd["07:00"]["bench"]["id"], soon["id"], lines)      # the one that expires first, first
        self.assertEqual(bd["08:45"]["bench"]["id"], late["id"], lines)      # a failed slot takes one too
        self.assertNotIn("bench", bd["06:34"])                              # 89 minutes away: left
        self.assertTrue(any("06:34: 89 minutes away" in ln for ln in lines), lines)
        for k in ("10:30", "12:15", "14:00", "15:45", "17:30", "19:15", "21:00"):
            self.assertEqual(json.dumps(bd[k], sort_keys=True), before[k], k)   # making, refused, signed: untouched
        s = bd["07:00"]
        self.assertEqual(s["status"], B.READY)
        self.assertNotIn("signoff", s)
        self.assertTrue(Path(s["clip"]["path"]).is_relative_to(self.cfg.drafts_root))
        self.assertEqual(s["clip"]["sha256"], soon["sha256"])
        self.assertEqual((s["host"], s["rights"], s["job"]), ("TGG", "unverified", soon["job"]))
        self.assertEqual(s["format"], "underlay")
        self.assertTrue(any("from the bench: " + soon["id"] in n for n in s["notes"]), s["notes"])
        self.assertEqual(B.verify(s), [])
        before = json.dumps(B.read(self.acct, DAY)["slots"], sort_keys=True)
        __import__("clipfactory.collect", fromlist=["x"]).collect(self.cfg, self.acct, DAY)   # never taken twice
        self.assertEqual(json.dumps(B.read(self.acct, DAY)["slots"], sort_keys=True), before)
        it = self.item(self.acct, soon["id"])
        self.assertEqual((it["state"], it["uses"][-1]["date"], it["uses"][-1]["slot"]), (BN.PLACED, "2030-01-07", "07:00"))
        self.assertEqual(B.read(self.acct, DAY).get("bench_report", "").count("from the bench"), 2)
        self.assertEqual(B.read(self.acct, DAY)["report"].splitlines()[-1], "Bench 05:05: 07:00 and 08:45 from the bench.")
        shutil.rmtree(it["_dir"])                                             # the bench copy may go: the draft stays
        self.assertEqual(B.verify(s), [])
        from clipfactory import signoff as S
        lines = S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew", now=self.t, seen={"07:00": B.seen_key(s)})
        self.assertIn(self.slot("07:00")["status"], B.SIGNED, lines)

    def test_the_window_and_the_0515_stop(self):
        self.spare("one-spare")
        self.spare("two-spare")
        for at in ("04:30", "05:15", "05:20", "23:00"):
            lines = self.fill(at=self.cfg.slot_time(DAY, at))
            self.assertIn("not run at", lines[0], at)
        lines = self.fill(at=self.cfg.slot_time(DAY - timedelta(days=1), "05:05"))      # another day's 05:05
        self.assertIn("not run at", lines[0])
        self.assertIsNone(B.read(self.acct, DAY))
        self.assertTrue(all(i["state"] == BN.READY for i in BN.items(self.acct)))
        # the clock passes 05:15 during the fill: the first slot is filled, nothing after
        now = [self.cfg.slot_time(DAY, "05:10")]
        real = BN._place

        def place_then_late(*a, **k):
            got = real(*a, **k)
            now[0] = self.cfg.slot_time(DAY, "05:15")
            return got
        with mock.patch.object(BN, "_place", place_then_late):
            lines = self.fill(clock=lambda: now[0])
        bd = B.read(self.acct, DAY)["slots"]
        self.assertEqual(sum(1 for s in bd.values() if s.get("bench")), 1, lines)
        self.assertTrue(any("the fill writes nothing after it" in ln for ln in lines), lines)
        self.assertNotIn("bench_report", B.read(self.acct, DAY))          # no board note after 05:15 either

    def test_it_waits_for_the_mac_and_gives_up_at_0512(self):
        self.spare("one-spare")
        now = [self.cfg.slot_time(DAY, "05:05")]

        def sleep(s):
            now[0] += timedelta(seconds=s)
        lines = self.fill(clock=lambda: now[0], busy=lambda: ["4242 /usr/bin/python3 run.py job --deliver"], sleep=sleep)
        self.assertTrue(any("nothing filled: the Mac was still busy at 05:12" in ln for ln in lines), lines)
        self.assertIsNone(B.read(self.acct, DAY))
        now[0] = self.cfg.slot_time(DAY, "05:05")
        calls = iter([["4242 ffmpeg"], ["4242 ffmpeg"], []])
        lines = self.fill(clock=lambda: now[0], busy=lambda: next(calls, []), sleep=sleep)
        self.assertTrue(any("from the bench" in ln for ln in lines), lines)

    def test_a_gta_render_holds_up_gta_only(self):
        self.spare("one-spare")
        led = self.politics_ready()
        now = [self.cfg.slot_time(DAY, "05:05")]
        gta_run = f"4242 /usr/bin/python3 {self.acct.skill_dir}/scripts/run.py job --deliver"
        lines = self.fill("all", clock=lambda: now[0], busy=lambda: [gta_run], rotation=lambda *a, **k: [],
                          sleep=lambda s: now.__setitem__(0, now[0] + timedelta(seconds=s)))
        self.assertTrue(any("Gaming 2030-01-07: nothing filled: the Mac was still busy" in ln for ln in lines), lines)
        self.assertEqual(self.slot("07:00", self.politics)["bench"]["id"], led["id"], lines)
        now[0] = self.cfg.slot_time(DAY, "05:05")
        lines = self.fill("all", clock=lambda: now[0], busy=lambda: ["4243 /usr/bin/python3 build_all.py some-slug"],
                          rotation=lambda *a, **k: [], sleep=lambda s: now.__setitem__(0, now[0] + timedelta(seconds=s)))
        self.assertTrue(any("Politics 2030-01-07: nothing filled" in ln for ln in lines), lines)  # no name: both

    def test_a_worker_or_a_second_fill_blocks(self):
        self.spare("one-spare")
        lock = C.STATE / "gaming" / ".worker.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            now = [self.cfg.slot_time(DAY, "05:05")]
            lines = self.fill(clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + timedelta(seconds=s)))
            self.assertTrue(any("Gaming 2030-01-07: nothing filled" in ln for ln in lines), lines)
        BN.root(self.acct).mkdir(parents=True, exist_ok=True)
        with open(BN.root(self.acct) / ".fill.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lines = self.fill()
            self.assertTrue(any("another bench fill is running" in ln for ln in lines), lines)
        self.assertIsNone(B.read(self.acct, DAY))

    def test_one_source_once_a_day_and_never_one_on_the_board(self):
        same = "https://www.youtube.com/watch?v=SAMESOURCE1"
        self.spare("from-same-a", url=same, until="2030-01-20", span=(10.0, 40.0))
        self.spare("from-same-b", url=same, until="2030-01-21", job="bench-20300103-from-same-a", span=(400.0, 440.0))
        on_board = self.spare("board-source", url="https://youtu.be/ONBOARD0001", until="2030-01-19")
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["23:00"].update(status=B.READY, slug="night", source_url="https://www.youtube.com/watch?v=ONBOARD0001")
        lines = self.fill()
        placed = [s["bench"]["id"] for s in B.read(self.acct, DAY)["slots"].values() if BN.from_bench(s)]
        self.assertEqual(len(placed), 1, lines)
        self.assertNotIn(on_board["id"], placed)
        self.assertTrue(any("its source is already on the day" in ln for ln in lines), lines)

    def test_a_reactions_slot_is_never_filled_before_0330(self):
        self.spare("one-spare")
        lines = self.fill(at=self.cfg.slot_time(DAY, "03:00"), dry_run=True, slots=["19:15"])
        self.assertTrue(any("19:15: held for the day's REACTIONS clip" in ln for ln in lines), lines)
        self.assertFalse(any("from the bench" in ln for ln in lines), lines)
        lines = self.fill(slots=["19:15"])                                        # 05:05: the hold is over
        s = self.slot("19:15")
        self.assertTrue(BN.from_bench(s), lines)
        self.assertEqual(s["format"], "underlay")

    def test_the_sha_on_any_board_is_refused(self):
        it = self.spare("one-spare")
        other = DAY + timedelta(days=3)
        ok, lines = PL.place(self.cfg, self.acct, other, "14:00", clip=Path(it["_dir"]) / it["files"]["clip"],
                             words=self.gta_words(), now=self.t)
        self.assertTrue(ok, lines)
        lines = self.fill()
        self.assertTrue(any("already on a board (Gaming 2030-01-10 14:00" in ln for ln in lines), lines)
        self.assertFalse(any(BN.from_bench(s) for s in (B.read(self.acct, DAY) or {"slots": {}})["slots"].values()))

    def gta_words(self) -> Path:
        return Add.gta_words(self)

    def test_gta_words_are_written_for_the_posting_day(self):
        it = self.spare("countdown-words")
        d = Path(it["_dir"])
        stored = (d / "youtube.txt").read_text()
        self.fill(slots=["07:00"])
        s = self.slot("07:00")
        yt = Path(s["copy"]["youtube_shorts"]["path"]).read_text()
        self.assertIn(f"{(LAUNCH - DAY).days} days to GTA 6.", yt)                 # the posting day's countdown
        self.assertNotIn(f"{(LAUNCH - date(2030, 1, 3)).days} days to GTA 6.", yt)
        self.assertEqual((d / "youtube.txt").read_text(), stored)                  # the bench copy is unchanged
        self.assertEqual(s["bench"]["words"], "the skill's copy engine")
        # the job's slate is gone: the stored words, the countdown rewritten for the day
        it2 = self.spare("slate-gone")
        shutil.rmtree(self.acct.skill_dir / "work" / it2["job"])
        self.fill(slots=["08:45"])
        s = self.slot("08:45")
        self.assertEqual(s["bench"]["id"], it2["id"])
        yt = Path(s["copy"]["youtube_shorts"]["path"]).read_text()
        self.assertIn(f"{(LAUNCH - DAY).days} days to GTA 6.", yt)
        self.assertNotIn(BN.DAYS_TOKEN, yt)
        self.assertEqual(s["bench"]["words"], "the stored words, the countdown for the day")

    def test_after_the_launch_the_countdown_line_goes(self):
        t = "TITLE\n\nengine words. " + BN.DAYS_TOKEN + " #gta6\n"
        self.assertEqual(BN._render_words(t, LAUNCH - timedelta(days=3), LAUNCH), "TITLE\n\nengine words. 3 days to GTA 6. #gta6\n")
        self.assertEqual(BN._render_words(t, LAUNCH, LAUNCH), "TITLE\n\nengine words. #gta6\n")

    def test_a_name_2_copy_on_the_bench_is_never_taken(self):
        it = self.spare("one-spare")
        d = Path(it["_dir"])
        clip(d / f"{it['slug']} 2.mp4")                     # a stray copy beside it, other bytes
        self.fill(slots=["07:00"])
        self.assertEqual(self.slot("07:00")["clip"]["sha256"], it["sha256"])

    def test_a_changed_bench_file_breaks_the_item(self):
        it = self.spare("one-spare")
        with open(Path(it["_dir"]) / f"{it['slug']}.mp4", "ab") as f:
            f.write(b"\0")
        lines = self.fill()
        self.assertTrue(any("ready -> broken" in ln for ln in lines), lines)
        self.assertEqual(self.item(self.acct, it["id"])["state"], BN.BROKEN)
        self.assertFalse(any(BN.from_bench(s) for s in B.read(self.acct, DAY)["slots"].values()))

    def test_expired_and_not_yet_made_are_skipped(self):
        self.spare("too-old", until="2030-01-06")
        lines = self.fill()
        self.assertTrue(any("no spare clip is ready" in ln for ln in lines), lines)
        self.assertEqual(self.item(self.acct, "20300103-too-old")["state"], BN.EXPIRED)

    def test_a_dry_run_writes_nothing(self):
        self.spare("one-spare")
        self.spare("two-spare")
        before = sorted(p.name for p in C.STATE.rglob("*"))
        lines = self.fill(dry_run=True, at=self.cfg.slot_time(DAY - timedelta(days=1), "20:00"))
        self.assertEqual(sum(1 for ln in lines if "from the bench" in ln), 2, lines)
        self.assertEqual(sorted(p.name for p in C.STATE.rglob("*")), before)
        self.assertIsNone(B.read(self.acct, DAY))

    def test_fill_off_places_nothing(self):
        self.spare("one-spare")
        self.raw["accounts"]["gaming"]["bench"]["fill"] = False
        self.reload()
        lines = self.fill()
        self.assertTrue(any("bench.fill is off" in ln for ln in lines), lines)


@unittest.skipUnless(ENGINE.is_dir(), "viral-clip-engine's rotation.py is not here")
class PoliticsDayRule(Base):
    def setUp(self):
        super().setUp()
        sc = self.politics.skill_dir / "scripts"
        sc.mkdir(parents=True, exist_ok=True)
        for n in ("rotation.py", "cast.py"):
            shutil.copy2(ENGINE / n, sc / n)

    def board_clip(self, k, job, slug, status=B.READY, with_job=True):
        _, _, c = self.politics_job(job=job, slug=slug, template="room", mode="fit", caption="politics-a",
                                  url=f"https://www.youtube.com/watch?v={job[:4]}board001")
        ok, lines = PL.place(self.cfg, self.politics, DAY, k, clip=c,
                             words=self.politics.account_dir / "Jobs" / job / "staged" / f"01-{slug}",
                             fmt="room", now=self.cfg.slot_time(DAY, "05:00"))
        self.assertTrue(ok, lines)
        with B.edit(self.cfg, self.politics, DAY) as bd:
            if with_job:
                bd["slots"][k]["job"] = job                   # the drop-in's job (no schedule.json entry)
            bd["slots"][k]["status"] = status

    def test_a_spare_the_day_rule_refuses_is_skipped_for_one_it_allows(self):
        self.board_clip("12:15", "Room Board 09.01.26", "board-room")
        room = self.politics_ready(job="Room Spare 09.02.26", slug="spare-room", template="room", until="2030-02-10",
                                 url="https://www.youtube.com/watch?v=roomspare01")      # expires first: tried first
        gag = self.politics_ready(job="Gaggle Spare 09.03.26", slug="spare-gaggle", template="gaggle", mode="head",
                                caption="politics-b", fmt="gaggle", url="https://www.youtube.com/watch?v=gagspare001",
                                hook="WHY DID YOU FIRE HER?")
        lines = self.fill("politics", slots=["14:00"])
        s = self.slot("14:00", self.politics)
        self.assertEqual(s["bench"]["id"], gag["id"], lines)
        self.assertTrue(any("the day rule says no" in ln and "back to back in room" in ln for ln in lines), lines)
        self.assertEqual(s["format"], "gaggle")
        self.assertIn("instagram_reels", s.get("cuts") or {})               # its Instagram cut came along
        self.assertEqual(s["rights"], "owned")
        self.assertEqual(self.item(self.politics, room["id"])["state"], BN.READY)

    def test_schedule_json_counts_as_well_as_the_board(self):
        self.politics_job(job="Sched Room 09.04.26", slug="sched-room", url="https://www.youtube.com/watch?v=schedroom01",
                        sched=(DAY.isoformat(), "15:45"))
        room = self.politics_ready(job="Room Spare 09.02.26", slug="spare-room", template="room",
                                 url="https://www.youtube.com/watch?v=roomspare01")
        lines = self.fill("politics", slots=["14:00"])
        self.assertFalse(BN.from_bench(self.slot("14:00", self.politics) or {}), lines)
        self.assertTrue(any("back to back in room" in ln for ln in lines), lines)
        self.assertEqual(self.item(self.politics, room["id"])["state"], BN.READY)

    def test_an_unreadable_board_clip_still_counts_for_its_segment(self):
        self.board_clip("12:15", "Gone 09.05.26", "gone-room", with_job=False)
        shutil.rmtree(self.politics.account_dir / "Jobs" / "Gone 09.05.26")
        self.politics_ready(job="Room Spare 09.02.26", slug="spare-room", template="room",
                          url="https://www.youtube.com/watch?v=roomspare01")
        lines = self.fill("politics", slots=["14:00"])
        self.assertTrue(any("back to back in room" in ln for ln in lines), lines)

    def test_a_cast_unchecked_since_and_a_helper_error_refuse(self):
        it = self.politics_ready()
        cf = self.politics.account_dir / "Jobs" / it["job"] / "cast.json"
        cast = json.loads(cf.read_text())
        cast["people"][0]["verified"] = ""
        cf.write_text(json.dumps(cast))
        lines = self.fill("politics", slots=["14:00"])
        self.assertTrue(any("its cast is unverified now" in ln for ln in lines), lines)
        cast["people"][0]["verified"] = VERIFIED
        cf.write_text(json.dumps(cast))

        def broken(*a, **k):
            raise RuntimeError("rotation.py would not run")
        lines = self.fill("politics", slots=["14:00"], rotation=broken)
        self.assertTrue(any("the day rule could not be checked" in ln for ln in lines), lines)
        self.assertFalse(BN.from_bench(self.slot("14:00", self.politics) or {}))
        lines = self.fill("politics", slots=["14:00"])
        self.assertTrue(BN.from_bench(self.slot("14:00", self.politics)), lines)


# ------------------------------------------------------------------------------------------------------ after
class After(Base):
    def placed(self, k="07:00", slug="one-spare"):
        job, slug, _ = self.gta_job(job=f"bench-20300103-{slug}", slug=slug,
                                    url=f"https://www.youtube.com/watch?v={slug[:11].ljust(11, 'q')}")
        it = self.added(self.acct, job, slug=slug, made_on="2030-01-03")
        self.fill(slots=[k])
        self.assertTrue(BN.from_bench(self.slot(k)))
        return it

    def test_reconcile_every_outcome(self):
        a, b, c, d = (self.placed(k, s) for k, s in (("07:00", "spare-aa"), ("08:45", "spare-bb"),
                                                      ("10:30", "spare-cc"), ("12:15", "spare-dd")))
        from clipfactory import signoff as S
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew", now=self.t, seen={"07:00": B.seen_key(self.slot("07:00"))})
        S.decline(self.cfg, self.acct, DAY, ["08:45"], "Andrew", reason="not today")
        S.decline(self.cfg, self.acct, DAY, ["10:30"], "Andrew", remake=True)
        e = self.placed("14:00", "spare-ee")                               # pulled after YouTube had it
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["14:00"]["outlets"] = {"youtube_shorts": {"video_id": "abcdefghijk"}}
        later = self.cfg.slot_time(DAY + timedelta(days=1), "10:00")       # 12:15 never signed, past 20 hours
        lines = BN.reconcile(self.cfg, self.acct, later)
        st = {i["id"]: i["state"] for i in BN.items(self.acct)}
        self.assertEqual((st[a["id"]], st[b["id"]], st[c["id"]], st[d["id"]], st[e["id"]]),
                         (BN.USED, BN.REJECTED, BN.REJECTED, BN.MISSED, BN.USED), lines)   # never signed: missed
        self.assertFalse(BN.from_bench(self.slot("10:30")))                 # a Make again: no Bench tag left

    def test_the_card_the_morning_and_stats_say_bench(self):
        it = self.placed()
        from clipfactory import review as R
        s = self.slot("07:00")
        self.assertIn(">Bench<", R._top(self.cfg, self.acct, s, self.t))
        bottom = R._bottom(self.cfg, self.acct, DAY, s, self.t)
        self.assertIn("From the bench: cut 2030-01-03 from TGG, good until 2030-02-20", bottom)
        s2 = dict(s, slug="something-else")
        self.assertNotIn(">Bench<", R._top(self.cfg, self.acct, s2, self.t))
        import factory as F
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch.object(C, "load", lambda *a: self.cfg), \
                mock.patch.object(self.cfg, "now", lambda: self.t):
            F.main(["morning", "gaming", "--date", DAY.isoformat()])
        self.assertIn("(1 from the bench)", buf.getvalue())
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"]["outlets"] = {"youtube_shorts": {"video_id": "abcdefghijk", "url": "https://youtu.be/x",
                                                                  "at": B.utcnow()}}
        from clipfactory import stats as ST
        with mock.patch.object(self.cfg, "now", lambda: self.t):
            rows = [p for p in ST.posts_from_boards(self.cfg) if p["clip"] == it["slug"]]
        self.assertEqual([(p["bench"], p["host"]) for p in rows], [(it["id"], "TGG")])
        from clipfactory import statspage as SP
        self.assertIn("bench", SP.FIELDS)

    def test_the_0618_agent(self):
        from clipfactory import timer as TM
        a = next(x for x in TM.AGENTS if x["name"] == "bench-fill")
        p = TM.plist(a)
        self.assertEqual(p["StartCalendarInterval"], {"Hour": 6, "Minute": 18})     # the night shift, 2026-10-19
        self.assertEqual(p["ProgramArguments"][-5:], ["bench", "fill", "all", "--date", "today"])
        self.assertFalse(p["RunAtLoad"])

    def test_status_and_retire(self):
        it = self.placed()
        out = BN.status(self.cfg, self.acct, now=self.t)
        self.assertIn("Gaming bench: 0 ready (keep 4 to 6), 1 placed", out[0])
        lines = BN.retire(self.cfg, it["id"], why="the news moved on")
        self.assertTrue(any("Decline it on its card" in ln for ln in lines), lines)
        self.assertEqual(self.item(self.acct, it["id"])["state"], BN.RETIRED)


class Cuts(Base):
    def test_cut_target_and_from_instagram_without_collect(self):
        job, slug, _ = self.politics_job()
        it = self.added(self.politics, job, slug=slug)
        d, jd = BN.cut_target(self.cfg, self.politics, it["id"], slug)
        self.assertEqual((d, jd.name), (Path(it["_dir"]), job))
        with self.assertRaises(SystemExit):
            BN.cut_target(self.cfg, self.politics, it["id"], "another-slug")
        with self.assertRaises(SystemExit):
            BN.cut_target(self.cfg, self.acct, it["id"], slug)
        clip(d / "instagram.mp4", 1.2)
        from clipfactory import igcut as IG
        out = IG._from_instagram(self.cfg, self.politics, DAY, slug, IG.YOUTUBE, d, None, 1.0, 1.5, collect=False)
        self.assertTrue((d / "youtube.mp4").is_file(), out)
        self.assertIsNone(B.read(self.politics, DAY))                        # nothing collected
        lines = BN.refresh(self.cfg, self.politics, it["id"])
        it = self.item(self.politics, it["id"])
        self.assertEqual(set(it["files"]["cuts"]), {"instagram_reels", "youtube_shorts"}, lines)
        self.assertEqual(it["state"], BN.READY)


class Render(Base):
    def setUp(self):
        super().setUp()
        self.job, self.slug, _ = self.gta_job(job="bench-20300107-dev", slug="dev-story")
        pj = self.acct.skill_dir / "work" / self.job / "project.json"
        cfg = json.loads(pj.read_text())
        cfg["date"] = "2000-01-01"
        pj.write_text(json.dumps(cfg))
        self.pj = pj
        self.day = self.cfg.slot_time(DAY, "10:00")

    def runner(self, leak=False):
        calls = []

        def run(account, what, cmd, until=None, names=None, **kw):
            calls.append((account, what, cmd, until, os.environ.get("GTA_ACCOUNT")))
            if leak:
                p = self.acct.account_dir / "Publish" / "schedule.json"
                p.write_text(json.dumps({"2000-01-01": {"00:00": {"job": self.job, "slug": self.slug}}}))
            return 0
        return run, calls

    def test_the_date_goes_first_then_the_deliver_runs_in_the_queue(self):
        run, calls = self.runner()
        ok, lines = BN.render(self.cfg, self.acct, self.job, self.slug, now=self.day, runner=run)
        self.assertTrue(ok, lines)
        cfg = json.loads(self.pj.read_text())
        self.assertNotIn("date", cfg)
        self.assertEqual(cfg["bench"]["was_date"], "2000-01-01")
        (account, what, cmd, until, ga), = calls
        self.assertEqual((account, cmd[-4:]), ("gaming", [self.job, "--deliver", "--slug", self.slug]))
        self.assertEqual(ga, str(self.acct.account_dir))
        self.assertTrue(any("bench add gaming" in ln for ln in lines))
        self.assertFalse((self.acct.account_dir / "Publish" / "schedule.json").exists())

    def test_refusals(self):
        run, calls = self.runner()
        ok, lines = BN.render(self.cfg, self.acct, self.job, self.slug, now=self.cfg.slot_time(DAY, "20:00"), runner=run)
        self.assertFalse(ok)
        self.assertIn("daylight", lines[0])
        ok, lines = BN.render(self.cfg, self.politics, self.job, self.slug, now=self.day, runner=run)
        self.assertFalse(ok)
        self.gta_job(job=self.job, slug="count", count_card={"number": 40})
        self.assertFalse(BN.render(self.cfg, self.acct, self.job, "count", now=self.day, runner=run)[0])
        p = self.acct.account_dir / "Publish" / "schedule.json"
        p.write_text(json.dumps({"2030-01-08": {"07:00": {"job": self.job, "slug": self.slug}}}))
        ok, lines = BN.render(self.cfg, self.acct, self.job, self.slug, now=self.day, runner=run)
        self.assertFalse(ok)
        self.assertIn("delivers into a slot", lines[0])
        self.assertEqual(calls, [])
        p.unlink()
        run, calls = self.runner(leak=True)
        ok, lines = BN.render(self.cfg, self.acct, self.job, self.slug, now=self.day, runner=run)
        self.assertFalse(ok)
        self.assertTrue(any("STOP: the deliver wrote Publish/schedule.json" in ln for ln in lines), lines)


# ------------------------------------------------------------------------------------------------------ fix pass (10-18)
NO_RULE = dict(rotation=lambda *a, **k: [])        # the Politics's day rule says yes (it has its own tests above)


class GoneSince(Base):
    """The review's cases: a spare whose clip went out, or is on its way, since it was added is never placed. It is
    rejected for good, with the reason; at the add the same checks refuse it."""

    def gta_spare(self, slug, **kw):
        return Fill.spare(self, slug, **kw)

    def politics_spare(self, job, slug, **kw):
        kw.setdefault("url", f"https://www.youtube.com/watch?v={slug[:11].ljust(11, 'z')}")
        return self.politics_ready(job=job, slug=slug, **kw)

    def test_r2_a_politics_clip_cut_again_and_out_since_is_rejected(self):
        for back, job, slug in ((10, "Banking 09.30.26", "have-you-returned"), (2, "Banking 09.29.26", "show-us-the-app")):
            it = self.politics_spare(job, slug)
            prev = DAY - timedelta(days=back)
            again = clip(self.tmp / f"again-{back}" / f"01-{slug}.mp4")         # the nightly cut it again: new bytes
            with B.edit(self.cfg, self.politics, prev) as bd:
                bd["slots"]["10:30"].update(status=B.SCHEDULED, slug=slug, job=job, format="room",
                                            clip={"sha256": B.sha256_file(again), "seconds": 2})
            lines = self.fill("politics", slots=["14:00"], **NO_RULE)
            self.assertFalse(BN.from_bench(self.slot("14:00", self.politics) or {}), lines)
            got = self.item(self.politics, it["id"])
            self.assertEqual(got["state"], BN.REJECTED, lines)
            self.assertIn(f"already on a board: {prev} 10:30 (scheduled)", got["log"][-1])

    def test_planned_by_the_night_or_out_before_the_boards(self):
        it = self.politics_spare("Banking 09.30.26", "have-you-returned")
        p = self.politics.account_dir / "Publish" / "schedule.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tomorrow = (DAY + timedelta(days=1)).isoformat()
        p.write_text(json.dumps({tomorrow: {"07:00": {"job": "Banking 09.30.26", "slug": "have-you-returned"}}}))
        lines = self.fill("politics", slots=["14:00"], **NO_RULE)
        self.assertFalse(BN.from_bench(self.slot("14:00", self.politics) or {}), lines)
        self.assertEqual(self.item(self.politics, it["id"])["state"], BN.REJECTED)
        self.assertTrue(any(f"plans 'have-you-returned' for {tomorrow} 07:00" in ln for ln in lines), lines)
        # the add: schedule.json had it on a day with no board (August, before the factory): it went out by hand
        job, slug, _ = self.politics_job(job="SBC Atkins 02.12.26", slug="atkins-insider",
                                       url="https://www.youtube.com/watch?v=atkins00001", sched=("2026-08-24", "07:00"))
        self.refused(self.add(self.politics, job, slug=slug), "a day with no board")
        job, slug, _ = self.politics_job(job="HFSC Megabanks 04.10.19", slug="teller-567-short",
                                       url="https://www.youtube.com/watch?v=teller00001")
        (self.politics.account_dir / "Publish" / "posted.json").write_text(json.dumps(
            {"2026-08-19": [{"slug": slug, "job": job, "destination": "facebook", "url": None}]}))
        self.refused(self.add(self.politics, job, slug=slug), "Publish/posted.json lists 'teller-567-short'")
        # a day WITH a board is the truth: planned there, never on it (every slot was full), it may be a spare
        old = DAY - timedelta(days=5)
        with B.edit(self.cfg, self.politics, old):
            pass
        over = self.politics_spare("Overflow 10.10.26", "overflow-clip", sched=(old.isoformat(), "21:00"))
        self.fill("politics", slots=["15:45"], **NO_RULE)
        self.assertEqual(self.slot("15:45", self.politics)["bench"]["id"], over["id"])

    def test_r3_a_gta_moment_posted_from_another_job_of_the_same_source_is_rejected(self):
        url = "https://www.youtube.com/watch?v=OGmDFz5mZ5A"
        it = self.gta_spare("dev-story", url=url, span=(100.0, 140.0))
        prev = DAY - timedelta(days=3)
        with B.edit(self.cfg, self.acct, prev) as bd:
            bd["slots"]["10:30"].update(status=B.SCHEDULED, slug="night-tgg-pick", job="tgg-0845-20300104",
                                        source_url=url, pick_span=[102.0, 142.0], clip={"sha256": "f" * 64, "seconds": 40})
        lines = self.fill(slots=["07:00"])
        self.assertFalse(BN.from_bench(self.slot("07:00") or {}), lines)
        self.assertEqual(self.item(self.acct, it["id"])["state"], BN.REJECTED, lines)
        self.assertTrue(any("shares 95% of its seconds" in ln and "the same source video" in ln for ln in lines), lines)
        # the add: the same, and against a spare in ANY state (this one was just rejected)
        job2, slug2, _ = self.gta_job(job="bench-20300103-again", slug="dev-story-again", url=url, span=(500.0, 540.0))
        ok = self.added(self.acct, job2, slug=slug2)
        BN.set_state(self.item(self.acct, ok["id"]), BN.REJECTED, "declined on its card")
        job3, slug3, _ = self.gta_job(job="bench-20300103-third", slug="dev-story-third", url=url, span=(505.0, 545.0))
        self.refused(self.add(self.acct, job3, slug=slug3), f"with bench {ok['id']} (rejected; the same source video")
        job4, slug4, _ = self.gta_job(job="bench-20300103-fourth", slug="dev-story-fourth", url=url, span=(104.0, 138.0))
        self.refused(self.add(self.acct, job4, slug=slug4), "night-tgg-pick (the same source video")

    def test_a_clip_sent_back_with_make_again_never_comes_back_as_a_spare(self):
        from clipfactory import signoff as S
        job, slug, c = self.gta_job(job="night-20300104-dev", slug="sent-back")
        prev = DAY - timedelta(days=3)
        ok, lines = PL.place(self.cfg, self.acct, prev, "14:00", clip=c, words=Add.gta_words(self),
                             now=self.cfg.slot_time(prev, "06:00"))
        self.assertTrue(ok, lines)
        S.decline(self.cfg, self.acct, prev, ["14:00"], "Andrew", remake=True)    # the slot forgets the clip
        self.assertNotIn("slug", self.slot("14:00", day=prev))
        self.refused(self.add(self.acct, job, slug=slug), f"was delivered on {prev}")
        # a spare added before the send-back is rejected at the next fill
        with mock.patch.object(BN, "went_out", lambda *a, **k: ""):
            self.assertTrue(self.add(self.acct, job, slug=slug)[0])
        lines = self.fill(slots=["07:00"])
        self.assertFalse(BN.from_bench(self.slot("07:00") or {}), lines)
        self.assertEqual(BN.items(self.acct)[0]["state"], BN.REJECTED, lines)

    def test_r4_one_clip_goes_on_the_bench_once(self):
        job, slug, c = self.gta_job(job="bench-20300103-dev", slug="dev-story")
        first = self.added(self.acct, job, slug=slug, made_on="2030-01-02")
        clip(c, 2.0)                                                          # rendered again: new bytes, same slug
        self.refused(self.add(self.acct, job, slug=slug, made_on="2030-01-03"), "already on the bench: 20300102-dev-story")
        BN.retire(self.cfg, first["id"], why="test")
        self.refused(self.add(self.acct, job, slug=slug, made_on="2030-01-03"), "already on the bench")   # retired too
        self.assertEqual(len(BN.items(self.acct)), 1)
        # two of the same clip from before this check: the second is rejected once the first is out
        BN.set_state(self.item(self.acct, first["id"]), BN.READY, "test: back")
        with mock.patch.object(BN, "went_out", lambda *a, **k: ""):
            self.assertTrue(self.add(self.acct, job, slug=slug, made_on="2030-01-03")[0])
        twin = "20300103-dev-story"
        self.fill(slots=["07:00"])
        self.assertEqual(self.slot("07:00")["bench"]["id"], first["id"])        # the one that expires first
        d1 = DAY + timedelta(days=1)
        lines = self.fill(day=d1, at=self.cfg.slot_time(d1, "05:05"), slots=["07:00"])
        self.assertFalse(BN.from_bench(self.slot("07:00", day=d1) or {}), lines)
        self.assertEqual(self.item(self.acct, twin)["state"], BN.REJECTED, lines)
        self.assertIn("the clip 'dev-story' is already on a board: 2030-01-07 07:00", self.item(self.acct, twin)["log"][-1])

    def test_r1_a_spare_never_signed_is_missed_never_stock(self):
        self.raw["accounts"]["gaming"]["bench"].update(low=1, target=2, max=2)
        self.reload()
        it = self.gta_spare("one-spare")
        self.fill(slots=["07:00"])
        self.assertTrue(BN.from_bench(self.slot("07:00")))
        d2 = DAY + timedelta(days=2)
        t2 = self.cfg.slot_time(d2, "05:05")
        self.assertEqual(BN.to_make(self.cfg, self.acct, t2), 1)            # placed: it holds a place in stock
        lines = self.fill(day=d2, at=t2, slots=["07:00"])
        got = self.item(self.acct, it["id"])
        self.assertEqual(got["state"], BN.MISSED, lines)
        self.assertTrue(any("no spare clip is ready" in ln for ln in lines), lines)
        self.assertFalse(BN.from_bench(self.slot("07:00", day=d2) or {}))
        self.assertEqual(BN.to_make(self.cfg, self.acct, t2), 2)            # the builder makes one more
        line = BN.status(self.cfg, self.acct, now=t2)[0]
        self.assertIn("Gaming bench: 0 ready (keep 1 to 2), 1 missed; to make: 2", line)
        self.assertIn("  Gaming bench: 0 spare clip(s) left (the 10:00 builder keeps 1 to 2)",
                      BN.low_lines(self.cfg, now=t2))
        # a ready item whose clip is on a board is not stock whatever its state says (place would refuse it)
        BN.set_state(got, BN.READY, "test: as the first build did")
        self.assertEqual(BN.ready_items(self.cfg, self.acct, t2), [])
        self.assertIn("1 ready but not placeable", BN.status(self.cfg, self.acct, now=t2)[0])

    def test_r1b_a_ready_spare_the_fill_would_reject_is_no_stock(self):
        # 10-24/10-25: status said 8 ready (to make 0) where the dry run rejected 6 as already on boards
        self.raw["accounts"]["gaming"]["bench"].update(low=1, target=2, max=2)
        self.reload()
        it = self.gta_spare("gone-spare")
        t = self.cfg.slot_time(DAY, "05:05")
        self.assertEqual(len(BN.ready_items(self.cfg, self.acct, t)), 1)
        with mock.patch.object(BN, "went_out", lambda *a, **k: "the clip is already on a board: test"):
            self.assertEqual(BN.ready_items(self.cfg, self.acct, t), [])
            self.assertEqual(BN.to_make(self.cfg, self.acct, t), 2)          # it leaves room in bench.max too
            self.assertIn("1 ready but not placeable", BN.status(self.cfg, self.acct, now=t)[0])
        self.assertEqual(self.item(self.acct, it["id"])["state"], BN.READY)  # status never writes


class PlannedAndQuiet(Base):
    def test_r6_a_planned_clip_not_collected_yet_keeps_its_slot(self):
        from clipfactory import collect as K
        job, slug, c = self.politics_job(job="Gap Fill 10.17.26", slug="gap-fill-room",
                                       url="https://www.youtube.com/watch?v=gapfill0001", sched=(DAY.isoformat(), "14:00"))
        spare = self.politics_ready(job="Gaggle Spare 09.03.26", slug="spare-gaggle", template="gaggle", mode="head",
                                  caption="politics-b", fmt="gaggle", url="https://www.youtube.com/watch?v=gagspare001",
                                  hook="WHY DID YOU FIRE HER?")
        lines = self.fill("politics", slots=["14:00"], **NO_RULE)
        self.assertFalse(BN.from_bench(self.slot("14:00", self.politics) or {}), lines)
        self.assertTrue(any(ln.strip().startswith("14:00: kept for gap-fill-room") for ln in lines), lines)
        self.assertEqual(self.item(self.politics, spare["id"])["state"], BN.READY)
        d = Path(self.politics.delivered_dirs(DAY)[0]) / f"1400 {slug}"          # the gap-fill delivers it
        d.mkdir(parents=True)
        shutil.copy2(c, d / c.name)
        for n in POLITICS_WORDS:
            (d / n).write_text("HOOK\n\nwords\n")
        K.collect(self.cfg, self.politics, DAY)
        self.assertEqual(self.slot("14:00", self.politics)["slug"], slug)        # in its own slot, nowhere else
        lines = self.fill("politics", slots=["15:45"], **NO_RULE)        # collected: nothing kept any more
        self.assertEqual(self.slot("15:45", self.politics)["bench"]["id"], spare["id"], lines)

    def test_a_planned_clip_whose_slot_is_taken_keeps_the_first_open_one(self):
        p = self.acct.account_dir / "Publish" / "schedule.json"
        p.write_text(json.dumps({DAY.isoformat(): {"09:30": {"job": "night-job", "slug": "night-clip"}}}))   # no slot
        Fill.spare(self, "one-spare")
        lines = self.fill(slots=["07:00", "08:45"])
        self.assertFalse(BN.from_bench(self.slot("07:00") or {}), lines)
        self.assertTrue(any("07:00: kept for night-clip" in ln for ln in lines), lines)
        self.assertTrue(BN.from_bench(self.slot("08:45")), lines)

    def test_the_fill_waits_for_quiet_while_a_gap_fill_writes_between_steps(self):
        Fill.spare(self, "one-spare")
        now = [self.cfg.slot_time(DAY, "05:05")]
        sched = self.acct.account_dir / "Publish" / "schedule.json"
        writes = iter(range(4))                     # nothing renders, but the first 4 polls see schedule.json change
        at = []

        def sleep(s):
            now[0] += timedelta(seconds=s)
            n = next(writes, None)
            if n is not None:
                sched.write_text(json.dumps({"_": "x" * (n + 1)}))
        real = BN._place

        def place(*a, **k):
            at.append(now[0])
            return real(*a, **k)
        with mock.patch.object(BN, "_place", place):
            lines = self.fill(clock=lambda: now[0], sleep=sleep, quiet_s=180)
        self.assertTrue(BN.from_bench(self.slot("07:00")), lines)
        self.assertEqual(at[0], self.cfg.slot_time(DAY, "05:06") + timedelta(seconds=20 + 180), lines)
        self.assertTrue(any(ln.startswith("waiting for quiet: Gaming: quiet for 0 s") for ln in lines), lines)

    def test_not_quiet_by_0512_fills_nothing_and_the_other_account_goes_on(self):
        Fill.spare(self, "one-spare")
        led = self.politics_ready()
        now = [self.cfg.slot_time(DAY, "05:05")]
        sched = self.acct.account_dir / "Publish" / "schedule.json"

        def sleep(s):                               # GTA's schedule.json changes all the time; the Politics's never
            now[0] += timedelta(seconds=s)
            sched.write_text(json.dumps({"_": now[0].isoformat()}))
        lines = self.fill("all", clock=lambda: now[0], sleep=sleep, quiet_s=180, **NO_RULE)
        self.assertTrue(any("Gaming 2030-01-07: nothing filled: it was not quiet by 05:12" in ln for ln in lines),
                        lines)
        self.assertIsNone(B.read(self.acct, DAY))
        self.assertEqual(self.slot("07:00", self.politics)["bench"]["id"], led["id"], lines)

    def test_r5_no_placement_starts_after_051430_and_helpers_get_the_time_left(self):
        for s_ in ("aa-spare", "bb-spare", "cc-spare"):
            Fill.spare(self, s_)
        now = [self.cfg.slot_time(DAY, "05:14") + timedelta(seconds=15)]
        calls = []

        def slow(*a, **k):
            calls.append(k.get("timeout"))
            now[0] += timedelta(seconds=20)
            return False, "slow"
        with mock.patch.object(BN, "_place", slow):
            lines = self.fill(clock=lambda: now[0], slots=["14:00", "15:45"])
        self.assertEqual(len(calls), 1, lines)                                # the 2nd try would start at 05:14:35
        self.assertLessEqual(calls[0], 15.0)
        self.assertTrue(any("the fill writes nothing after it" in ln for ln in lines), lines)
        # the copy engine is given the time left, and a timeout refuses the clip (never the stored words)
        got = []

        def engine(acct, job, slug, day, out, timeout=180):
            got.append(timeout)
            raise PL.Refused(f"the skill's copy engine ran past {timeout} s")
        now[0] = self.cfg.slot_time(DAY, "05:14")
        with mock.patch.object(PL, "skill_words", engine):
            lines = self.fill(clock=lambda: now[0], slots=["14:00"])
        self.assertEqual(got[0], 30, lines)
        self.assertFalse(BN.from_bench(self.slot("14:00") or {}), lines)
        self.assertTrue(any("ran past 30 s" in ln for ln in lines), lines)
        # an engine that answers, but late: the words are there at 05:14:55, nothing is placed after them
        real = PL.skill_words

        def late(*a, **k):
            out = real(*a, **k)
            now[0] = self.cfg.slot_time(DAY, "05:14") + timedelta(seconds=55)
            return out
        now[0] = self.cfg.slot_time(DAY, "05:14")
        with mock.patch.object(PL, "skill_words", late):
            lines = self.fill(clock=lambda: now[0], slots=["14:00"])
        self.assertFalse(BN.from_bench(self.slot("14:00") or {}), lines)
        self.assertTrue(any("the words took too long, nothing placed after 05:15" in ln for ln in lines), lines)


class FixPassOther(Base):
    def test_a_cut_with_dated_words_keeps_the_spare_waiting_and_a_changed_cut_breaks_it(self):
        job, slug, _ = self.politics_job()
        it = self.added(self.politics, job, slug=slug)
        d = Path(it["_dir"])
        clip(d / "instagram.mp4", 1.2)
        (d / "instagram.cut.txt").write_text("HOOK\n\nWatch what she said today.\n")
        lines = BN.refresh(self.cfg, self.politics, it["id"])
        it = self.item(self.politics, it["id"])
        self.assertEqual(it["state"], BN.NEEDS_CUT, lines)
        self.assertTrue(any("a word dates its cut's words (instagram.cut.txt: 'today')" in ln for ln in lines), lines)
        (d / "instagram.cut.txt").write_text("HOOK\n\nWatch what she said.\n")
        BN.refresh(self.cfg, self.politics, it["id"])
        it = self.item(self.politics, it["id"])
        self.assertEqual(it["state"], BN.READY)
        self.assertEqual(BN._verify_files(it), "")
        (d / "instagram.cut.txt").write_text("HOOK\n\nsomething else, unchecked\n")
        self.assertIn("cut's words changed", BN._verify_files(it))
        (d / "instagram.cut.txt").write_text("HOOK\n\nWatch what she said.\n")
        clip(d / "youtube.mp4", 1.2)                                             # a cut nobody recorded
        self.assertIn("not the ones recorded", BN._verify_files(it))

    def test_the_reel_source_comes_from_the_slate(self):
        pub = self.acct.account_dir / "Publish"
        (pub / "broll-used-2030-01-01.json").write_text(json.dumps(
            {"sources": ["other-src"], "entries": [{"slug": "duo-clip", "source": "other-src"}]}))   # another day's
        job, slug, _ = self.gta_job(slug="duo-clip", duo={"_reel_source": "reel-src-1"})
        it = self.added(self.acct, job, slug=slug)
        self.assertEqual((it["gta"]["reel_sources"], it["gta"]["reel_from"]), (["reel-src-1"], "slate"))
        (pub / f"broll-used-{DAY}.json").write_text(json.dumps({"sources": ["reel-src-1"]}))
        lines = self.fill(slots=["07:00"])
        self.assertFalse(BN.from_bench(self.slot("07:00") or {}), lines)
        self.assertTrue(any("its b-roll reel source reel-src-1 is taken" in ln for ln in lines), lines)
        self.assertEqual(self.item(self.acct, it["id"])["state"], BN.READY)        # another day may take it

    def test_fill_ships_off_and_a_dry_run_still_shows_what_it_would_place(self):
        self.assertFalse(BN.DEFAULTS["fill"])
        Fill.spare(self, "one-spare")
        self.raw["accounts"]["gaming"]["bench"]["fill"] = False
        self.reload()
        lines = self.fill(dry_run=True, at=self.cfg.slot_time(DAY - timedelta(days=1), "20:00"), slots=["07:00"])
        self.assertTrue(any("bench.fill is off, so the 06:18 fill places nothing" in ln for ln in lines), lines)
        self.assertTrue(any("07:00: from the bench" in ln and "dry run: nothing written" in ln for ln in lines), lines)
        lines = self.fill()
        self.assertTrue(any("bench.fill is off in config/accounts.json; nothing placed" in ln for ln in lines), lines)
        self.assertIsNone(B.read(self.acct, DAY))


class CommandLine(Base):
    def test_the_commands(self):
        env = dict(os.environ, CLIP_FACTORY_CONFIG=str(self.tmp / "accounts.json"), CLIP_FACTORY_STATE=str(C.STATE),
                   CLIP_FACTORY_OFFLINE="1")
        base = [sys.executable, str(ROOT / "factory.py"), "bench"]
        r = subprocess.run(base + ["status"], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Gaming bench: 0 ready", r.stdout)
        r = subprocess.run(base + ["fill", "all"], capture_output=True, text=True, env=env, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--date", r.stderr)
        # tomorrow, never today: the fill only fills its own day, so it is "not run" at every hour. With today, between
        # 05:50 and 06:25 the real fill ran and waited for quiet past this timeout (2026-10-20)
        r = subprocess.run(base + ["fill", "all", "--date", "tomorrow"], capture_output=True, text=True, env=env,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("not run at", r.stdout)
        r = subprocess.run(base + ["add", "gaming", "no-such-job", "--slug", "x", "--allow-word", "today"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("needs its own --because", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
