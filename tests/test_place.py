import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""`factory.py place` and the daily REACTIONS slot (audit calls 3 and 4, 2026-10-17), offline, on throwaway folders.

place: one finished clip into one named slot as an unsigned READY draft, files copied, refusals write nothing.
The slot: the night leaves 19:15 for the 13:00 REACTIONS task's clip until 03:30 on the day, then cuts it as a normal
clip; a released slot or a day that already has its REACTIONS clip is not held. Tiny clips (2 s, 108x192)."""
import json
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import makers as M     # noqa: E402
from clipfactory import place as PL     # noqa: E402
from test_gta_maker_fixes import Base as GtaBase, row, cand, DAY as GTA_DAY   # noqa: E402

DAY = date(2030, 1, 7)
PLATS = ("youtube", "tiktok", "reels", "facebook")


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float = 2.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["nice", "-n", "19", "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=black:s=108x192:d={seconds}", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                    "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                    str(path)], check=True)
    return path


def paste_folder(d: Path, plats=PLATS, tag: str = "x") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for p in plats:
        (d / f"{p}.txt").write_text(f"{tag} title {p}\n\n{tag} words for {p} #gta6\n")
    return d


FAKE_DAY_FOLDER = '''
import json
from pathlib import Path
JOBS = Path(__file__).resolve().parent.parent / "Jobs"
class _GC:
    @staticmethod
    def packs(clip, cfg, d):
        return {p: {"title": clip["hook"] + " " + p, "paste": "engine words for " + p + " on " + d.isoformat(),
                    "question_default": False} for p in ("youtube", "tiktok", "reels", "facebook")}
gc = _GC()
def clip_entry(job, slug):
    sl = json.loads((JOBS / job / "slate.json").read_text())
    c = next((c for c in sl["clips"] if c["slug"] == slug), {})
    return c, json.loads((JOBS / job / "project.json").read_text())
def paste_files(dst, clip, cfg, packs):
    for p, k in packs.items():
        (Path(dst) / (p + ".txt")).write_text(k["title"] + "\\n\\n" + k["paste"] + "\\n")
'''


class PlaceBase(unittest.TestCase):
    def setUp(self):
        self._state = C.STATE
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-place-"))
        C.STATE = self.tmp / "state"
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(self.tmp / "drafts")
        for slug, a in raw["accounts"].items():
            a["seconds"] = {"min": 1, "max": 3}
            a["account_dir"] = str(self.tmp / a["name"])
            a["skill_dir"] = str(self.tmp / f"skill-{slug}")
            a["delivered"] = [str(self.tmp / "delivered" / slug / "{YYYY}{MM}{DD}")]
            for o in (a.get("outlets") or {}).values():
                o.pop("min_seconds", None)
                o.pop("max_seconds", None)
        g = raw["accounts"]["gaming"]
        g["reactions_slots"], g["reactions_until"] = ["19:15"], "2031-01-01"
        self.raw = raw
        self.reload()
        self.now = self.cfg.slot_time(DAY, "06:00")          # 07:00 is an hour away, the rest are not
        self.eve = self.cfg.slot_time(date.fromordinal(DAY.toordinal() - 1), "13:00")   # when the REACTIONS task runs

    def tearDown(self):
        C.STATE = self._state

    def reload(self):
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(self.raw))
        self.cfg = C.load(p)
        self.acct = self.cfg.account("gaming")

    def place(self, slot, **kw):
        kw.setdefault("now", self.now)
        return PL.place(self.cfg, kw.pop("acct", self.acct), kw.pop("day", DAY), slot, **kw)

    def slot(self, k, acct=None, day=DAY):
        bd = B.read(acct or self.acct, day)
        return (bd or {}).get("slots", {}).get(k)


class Place(PlaceBase):
    def test_a_clip_lands_ready_unsigned_unscheduled_and_copied(self):
        src = clip(self.tmp / "in" / "01-big-moment.mp4")
        (self.tmp / "in" / "01-big-moment.jpg").write_bytes(b"jpg")
        words = self.tmp / "in" / "words.txt"
        words.write_text("BIG MOMENT\n\nThe whole thing. #gta6\n")
        ok, lines = self.place("14:00", clip=src, words=words, why="by hand",
                               sources=["https://www.youtube.com/watch?v=abcdefghijk"])
        self.assertTrue(ok, lines)
        s = self.slot("14:00")
        self.assertEqual(s["status"], B.READY)
        self.assertNotIn("signoff", s)
        self.assertFalse(s.get("outlets"))
        self.assertEqual(s["source"], "placed")
        self.assertEqual(s["source_urls"], ["https://www.youtube.com/watch?v=abcdefghijk"])
        self.assertEqual(s["source_url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(s["slug"], "big-moment")
        self.assertEqual(set(s["copy"]), set(self.acct.platforms))
        self.assertIn("BIG MOMENT", Path(s["copy"]["youtube_shorts"]["path"]).read_text())
        self.assertTrue(src.exists())                                    # copied, never moved
        self.assertTrue(Path(s["clip"]["path"]).is_file())
        self.assertEqual(s["clip"]["sha256"], B.sha256_file(src))
        self.assertTrue(s["cover"] and Path(s["cover"]).is_file())
        self.assertIn("placed (by hand): waits for Approve", B.last_note(s))
        self.assertEqual(B.verify(s), [])
        used = M.used_sources(self.acct)
        self.assertIn("abcdefghijk", used)                                # the nightly never cuts it again
        before = json.dumps(self.slot("14:00"), sort_keys=True)
        K.collect(self.cfg, self.acct, DAY)                              # a later collect does not take it twice
        self.assertEqual(json.dumps(self.slot("14:00"), sort_keys=True), before)

    def test_check_writes_nothing(self):
        src = clip(self.tmp / "in" / "a.mp4")
        ok, lines = self.place("14:00", clip=src, words=paste_folder(self.tmp / "w"), check=True)
        self.assertTrue(ok, lines)
        self.assertIn("--check: nothing written", lines[-1])
        self.assertIsNone(B.read(self.acct, DAY))
        self.assertFalse((self.tmp / "drafts").exists())
        self.assertFalse((C.STATE / "gaming" / "delivered").exists())

    def assertRefused(self, out, words):
        ok, lines = out
        self.assertFalse(ok, lines)
        self.assertIn("REFUSED, nothing written", lines[0])
        self.assertIn(words, lines[0])

    def test_refusals_write_nothing(self):
        good = clip(self.tmp / "in" / "good.mp4")
        w = paste_folder(self.tmp / "w")
        self.assertRefused(self.place("07:00", clip=good, words=w), "60 minutes away")
        self.assertRefused(self.place("14:00", clip=clip(self.tmp / "in" / "long.mp4", 4.0), words=w), "takes 1 to 3")
        mov = self.tmp / "in" / "x.mov"
        mov.write_bytes(good.read_bytes())
        self.assertRefused(self.place("14:00", clip=mov, words=w), "not an .mp4")
        self.assertRefused(self.place("14:00", clip=good, words=paste_folder(self.tmp / "w2", PLATS[:2])),
                           "no words for")
        self.assertRefused(self.place("14:00", clip=good), "say where the words are")
        self.assertRefused(self.place("14:00", clip=good, words=w, sources=["not a link"]), "is not a link")
        self.assertRefused(self.place("14:00", clip=good, words=w, fmt="quiz"), "format 'quiz'")
        self.assertRefused(self.place("13:00", clip=good, words=w), "has no slot 13:00")
        copy2 = self.tmp / "in" / "good 2.mp4"
        copy2.write_bytes(good.read_bytes())
        self.assertRefused(self.place("14:00", clip=copy2, words=w), "iCloud's copy of good.mp4")
        self.assertIsNone(B.read(self.acct, DAY))                       # not even the board
        self.assertFalse((self.tmp / "drafts").exists())

    def test_only_an_open_or_failed_slot(self):
        w = paste_folder(self.tmp / "w")
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["14:00"]["status"] = B.MAKING
            bd["slots"]["15:45"]["status"] = B.FAILED
            bd["slots"]["15:45"].update(host="Old Host", job="factory-old", pick=3, source_url="https://x.example/old")
        self.assertRefused(self.place("14:00", clip=clip(self.tmp / "a.mp4"), words=w), "is making")
        ok, lines = self.place("15:45", clip=clip(self.tmp / "b.mp4"), words=w)
        self.assertTrue(ok, lines)
        s = self.slot("15:45")
        for k in ("host", "job", "pick", "source_url"):                 # the failed maker's provenance is gone
            self.assertNotIn(k, s)
        self.assertRefused(self.place("15:45", clip=clip(self.tmp / "c.mp4", 2.5), words=w), "is ready")

    def test_a_clip_already_on_any_board_is_refused(self):
        src = clip(self.tmp / "in" / "a.mp4")
        w = paste_folder(self.tmp / "w")
        self.assertTrue(self.place("14:00", clip=src, words=w)[0])
        self.assertRefused(self.place("17:30", clip=src, words=w), "already on a board: Gaming 2030-01-07 14:00")
        politics = self.cfg.account("politics")
        lw = self.tmp / "lw"
        lw.mkdir()
        for n in ("youtube.txt", "tiktok_reach.txt", "facebook.txt", "instagram.txt"):
            (lw / n).write_text("TITLE\n\nwords\n")
        self.assertRefused(self.place("14:00", acct=politics, clip=src, words=lw), "already on a board")
        self.assertEqual(self.slot("17:30")["status"], B.OPEN)

    def test_a_delivery_folder_brings_its_clip_and_platform_cut(self):
        d = paste_folder(self.tmp / "1400 my-clip")
        clip(d / "01-my-clip.mp4")
        clip(d / "instagram.mp4", 1.5)
        ok, lines = self.place("14:00", words=d)
        self.assertTrue(ok, lines)
        s = self.slot("14:00")
        self.assertEqual(s["slug"], "my-clip")
        self.assertIn("instagram_reels", s.get("cuts") or {})
        self.assertIn("x words for tiktok", Path(s["copy"]["tiktok"]["path"]).read_text())

    def test_an_upper_case_mp4_and_a_cover_land(self):
        src = clip(self.tmp / "in" / "Big Clip.mp4")
        up = src.with_name("Big Clip.MP4")
        src.rename(up)
        (self.tmp / "in" / "Big Clip.jpg").write_bytes(b"jpg")
        ok, lines = self.place("14:00", clip=up, words=paste_folder(self.tmp / "w"))
        self.assertTrue(ok, lines)
        s = self.slot("14:00")
        self.assertEqual((Path(s["clip"]["path"]).suffix, s["slug"]), (".mp4", "big-clip"))
        self.assertTrue(s["cover"] and Path(s["cover"]).is_file())

    def test_a_reactions_slot_takes_format_reactions_and_one_a_day(self):
        w = paste_folder(self.tmp / "w")
        ok, lines = self.place("19:15", clip=clip(self.tmp / "rx.mp4"), words=w, host="Speed, Kai", now=self.eve)
        self.assertTrue(ok, lines)
        s = self.slot("19:15")
        self.assertEqual((s["format"], s["host"]), ("reactions", "Speed, Kai"))
        self.assertRefused(self.place("12:15", clip=clip(self.tmp / "rx2.mp4", 2.5), words=w, fmt="reactions",
                                      now=self.eve), "already has 1 REACTIONS clip(s) (19:15)")
        ok, _ = self.place("12:15", clip=clip(self.tmp / "solo.mp4", 2.2), words=w, now=self.eve)
        self.assertTrue(ok)
        self.assertNotEqual(self.slot("12:15")["format"], "reactions")     # an ordinary slot keeps its own format

    def test_after_0330_a_clip_in_the_reactions_slot_is_a_normal_one(self):
        with B.edit(self.cfg, self.acct, DAY) as bd:              # the night marked it while holding it
            bd["slots"]["19:15"]["format"] = "reactions"
        ok, lines = self.place("19:15", clip=clip(self.tmp / "gap.mp4"), words=paste_folder(self.tmp / "w"))
        self.assertTrue(ok, lines)
        self.assertEqual(self.slot("19:15")["format"], M.rotation_format(self.acct, "19:15"))

    def test_two_reactions_slots_take_two(self):
        self.raw["accounts"]["gaming"]["reactions_slots"] = ["12:15", "19:15"]
        self.reload()
        w = paste_folder(self.tmp / "w")
        self.assertTrue(self.place("19:15", clip=clip(self.tmp / "a.mp4"), words=w, now=self.eve)[0])
        self.assertTrue(self.place("12:15", clip=clip(self.tmp / "b.mp4", 2.5), words=w, now=self.eve)[0])
        self.assertEqual({self.slot(k)["format"] for k in ("12:15", "19:15")}, {"reactions"})
        self.assertRefused(self.place("14:00", clip=clip(self.tmp / "c.mp4", 2.2), words=w, fmt="reactions"),
                           "already has 2 REACTIONS clip(s)")

    def test_a_job_gives_the_skill_engine_words_its_clip_and_its_sources(self):
        pub = self.acct.account_dir / "Publish"
        pub.mkdir(parents=True)
        (pub / "day_folder.py").write_text(FAKE_DAY_FOLDER)
        jd = self.acct.account_dir / "Jobs" / "reactions-test"
        clip(jd / "clips" / "01-rx-moment.mp4")
        (jd / "clips" / "01-rx-moment.jpg").write_bytes(b"jpg")
        (jd / "captions").mkdir()
        (jd / "captions" / "01-rx-moment.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHI.\n")
        (jd / "slate.json").write_text(json.dumps({"clips": [{"slug": "rx-moment", "hook": "CAN YALL BELIEVE",
                                                              "template": "reactions"}]}))
        (jd / "project.json").write_text(json.dumps({
            "out_dir": str(jd), "rights": "unverified", "source_title": "4 creators reacting",
            "host": {"name": "iShowSpeed, Kai Cenat"},
            "sources": [{"name": "iShowSpeed", "url": "https://www.youtube.com/watch?v=mlguGHEeVO0"},
                        {"name": "Kai Cenat", "url": "https://www.youtube.com/watch?v=B6biwxo0dMQ"}]}))
        ok, lines = self.place("19:15", job="reactions-test", slug="rx-moment", why="10-17 date card")
        self.assertTrue(ok, lines)
        s = self.slot("19:15")
        self.assertEqual(s["format"], "reactions")
        self.assertEqual((s["host"], s["rights"], s["job"]), ("iShowSpeed, Kai Cenat", "unverified", "reactions-test"))
        self.assertEqual(s["source_urls"], ["https://www.youtube.com/watch?v=mlguGHEeVO0",
                                            "https://www.youtube.com/watch?v=B6biwxo0dMQ"])
        self.assertNotIn("source_url", s)                               # a duo is not one creator's clip
        self.assertIn("engine words for youtube on 2030-01-07", Path(s["copy"]["youtube_shorts"]["path"]).read_text())
        self.assertTrue(any(p.suffix == ".srt" for p in Path(s["folder"]).iterdir()))
        self.assertTrue({"mlguGHEeVO0", "B6biwxo0dMQ"} <= M.used_sources(self.acct))
        self.assertRefused(self.place("17:30", job="reactions-test", slug="nope"), "has no clip 'nope'")

    def test_release(self):
        ok, lines = PL.release(self.cfg, self.acct, DAY, "19:15", why="no wave today")
        self.assertTrue(ok, lines)
        self.assertTrue(self.slot("19:15")["reactions_released"])
        self.assertFalse(PL.release(self.cfg, self.acct, DAY, "14:00")[0])
        self.assertEqual(M.reactions_held(self.cfg, self.acct, DAY, B.read(self.acct, DAY), now=self.now), set())

    def test_the_command_line(self):
        src = clip(self.tmp / "in" / "a.mp4")
        env = dict(os.environ, CLIP_FACTORY_CONFIG=str(self.tmp / "accounts.json"), CLIP_FACTORY_STATE=str(C.STATE),
                   CLIP_FACTORY_OFFLINE="1")
        base = [sys.executable, str(ROOT / "factory.py"), "place", "gaming", "--date", DAY.isoformat()]
        r = subprocess.run(base + ["--slot", "14:00", "--clip", str(src), "--words", str(paste_folder(self.tmp / "w")),
                                   "--source-url", "https://www.youtube.com/watch?v=abcdefghijk", "--check"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--check: nothing written", r.stdout)
        r = subprocess.run(base + ["--slot", "14:00", "--clip", str(src)], capture_output=True, text=True, env=env,
                           timeout=120)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stdout)
        r = subprocess.run([sys.executable, str(ROOT / "factory.py"), "place", "gaming", "--slot", "14:00"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertIn("place needs --date", r.stdout + r.stderr)


# --------------------------------------------------------------------------------------- the night and the slot
class Hold(GtaBase):
    def setUp(self):
        super().setUp()
        self.wave()

    def wave(self, until="2031-01-01", slots=("19:15",)):
        self.raw["accounts"]["gaming"].update(reactions_slots=list(slots), reactions_until=until)
        self.reload()

    def at(self, hhmm, day=GTA_DAY):
        return mock.patch.object(self.cfg, "now", lambda: self.cfg.slot_time(day, hhmm))

    def test_the_night_leaves_the_reactions_slot_open(self):
        self.inbox([row(1), row(2)])
        calls, lines = self.make(only=["17:30", "19:15"])
        s = self.board()["slots"]["19:15"]
        self.assertEqual((s["status"], s["format"]), (B.OPEN, "reactions"))
        self.assertIn("Held for the day's REACTIONS clip", B.last_note(s))
        self.assertFalse([c for c in calls if "--slot" in c and c[c.index("--slot") + 1] == "1915"])
        self.assertEqual(self.board()["slots"]["17:30"]["status"], B.READY)
        self.assertTrue(any(ln.startswith("19:15 (reactions): left open") for ln in lines), lines)

    def test_after_0330_it_is_cut_as_a_normal_clip(self):
        self.inbox([row(1), row(2)])
        self.make(only=["19:15"])                                       # the night: held, marked reactions
        self.assertEqual(self.board()["slots"]["19:15"]["format"], "reactions")
        with self.at("03:41"):
            calls, lines = self.make(only=["19:15"])
        s = self.board()["slots"]["19:15"]
        self.assertEqual(s["status"], B.READY)
        self.assertEqual(s["format"], M.rotation_format(self.acct, "19:15"))
        self.assertTrue(any("no REACTIONS clip in it; cut as a normal clip" in ln for ln in lines), lines)
        with self.at("03:29"):
            self.assertEqual(M.reactions_held(self.cfg, self.acct, GTA_DAY, {"slots": {"19:15": {
                "status": B.OPEN, "format": "reactions"}}}), {"19:15"})

    def test_a_released_slot_or_a_day_with_its_clip_is_not_held(self):
        PL.release(self.cfg, self.acct, GTA_DAY, "19:15")
        self.assertEqual(M.reactions_held(self.cfg, self.acct, GTA_DAY, self.board()), set())
        self.fresh()
        self.wave()
        with B.edit(self.cfg, self.acct, GTA_DAY) as bd:
            bd["slots"]["12:15"].update(format="reactions", status=B.READY)
        self.assertEqual(M.reactions_held(self.cfg, self.acct, GTA_DAY, self.board()), set())

    def test_no_slot_after_reactions_until(self):
        self.wave(until="2029-12-31")
        self.assertEqual(M.reactions_slots(self.acct, GTA_DAY), [])
        self.inbox([row(1)])
        self.make(only=["19:15"])
        self.assertEqual(self.board()["slots"]["19:15"]["status"], B.READY)

    def test_a_slot_filled_while_the_night_runs_is_left_alone(self):
        self.inbox([row(1)])
        real = M.used_sources

        def fill_then_read(acct):                                      # a clip placed after the night read the board
            with B.edit(self.cfg, self.acct, GTA_DAY) as bd:
                bd["slots"]["08:45"].update(status=B.READY, slug="placed-one", source="placed")
            return real(acct)
        with mock.patch.object(M, "used_sources", fill_then_read):
            calls, lines = self.make(only=["07:00", "08:45"])
        s = self.board()["slots"]["08:45"]
        self.assertEqual((s["status"], s["slug"], s["source"]), (B.READY, "placed-one", "placed"))
        self.assertNotIn("job", s)
        self.assertTrue(any("08:45: filled while the night ran" in ln for ln in lines), lines)
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.READY)

    def test_a_remade_slot_drops_what_the_placed_clip_carried(self):
        with B.edit(self.cfg, self.acct, GTA_DAY) as bd:
            bd["slots"]["07:00"].update(status=B.OPEN, source="placed", placed={"at": "x"},
                                        source_urls=["https://www.youtube.com/watch?v=zzzzzzzzzzz"])
        self.inbox([row(1)])
        self.make(only=["07:00"])
        s = self.board()["slots"]["07:00"]
        for k in ("source_urls", "placed", "source"):
            self.assertNotIn(k, s)
        self.assertEqual(s["source_url"], row(1)["url"])


class GradeRetry(GtaBase):
    HOOK = (1, "    [ BAD  ] HOOK     the first 3s do not show what the hook says\n"
               "STOPPED at the grade: the first seconds do not show the hook (the HOOK line above); RUN.md")

    def test_a_hook_stop_at_the_grade_tries_the_next_candidate(self):
        cands = [cand(i, i * 100, i * 100 + 40) for i in range(1, 7)]
        self.inbox([row(1)])
        calls, lines = self.make(moments=(4000, cands), picks={1: self.HOOK}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1, 2])
        s = self.board()["slots"]["07:00"]
        self.assertEqual(s["status"], B.READY)
        self.assertTrue(any("candidate 1 refused (HOOK at the grade); trying the next" in n for n in s["notes"]))

    def test_the_hook_promise_at_preflight_tries_the_next_candidate(self):
        # gta-skill's hook gate (preflight PROMISE, 2026-10-17): a preflight refusal, so the next candidate is tried
        cands = [cand(i, i * 100, i * 100 + 40) for i in range(1, 7)]
        self.inbox([row(1)])
        out = (1, "    [FAIL] PROMISE  'LUCIA IS CALLING' is not said or shown in the first 3s\n"
                  "STOPPED at 'preflight' (exit 1); the whole output is in RUN.md")
        calls, _ = self.make(moments=(4000, cands), picks={1: out}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1, 2])
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.READY)

    def test_another_stop_at_the_grade_is_not_retried(self):
        cands = [cand(i, i * 100, i * 100 + 40) for i in range(1, 7)]
        self.inbox([row(1)])
        out = (1, "    [ BAD  ] FACECUT x\nSTOPPED at the grade: a face at the edge (the FACECUT line above)")
        calls, _ = self.make(moments=(4000, cands), picks={1: out}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1])
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.FAILED)

    def test_the_reader(self):
        self.assertEqual(M.preflight_refusal(self.HOOK[1]), ["HOOK at the grade"])
        self.assertEqual(M.preflight_refusal("[ BAD  ] DEADAIR 1.6s\nSTOPPED at the grade: the delivered clip has dead "
                                             "air (the DEADAIR line above); x"), ["DEADAIR at the grade"])
        self.assertIsNone(M.preflight_refusal("STOPPED at the grade: (the FACECUT line above)"))
        self.assertEqual(M.preflight_refusal("[FAIL] MUSIC x\nSTOPPED at 'preflight' (exit 1)"), ["MUSIC"])


# --------------------------------------------------------------------------------------------- one truth
class Docs(unittest.TestCase):
    def test_the_docs_name_the_reactions_slot_and_the_task(self):
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))["accounts"]["gaming"]
        slots = raw.get("reactions_slots") or []
        if not slots:           # the REACTIONS hold is off since 2026-10-31 (Andrew: "just the best clips"): its note says so
            self.assertIn("OFF since", raw.get("_reactions_note", ""))
            return
        self.assertTrue(raw.get("reactions_until"))
        docs = [ROOT / "README.md", ROOT / "hermes" / "gaming" / "SOUL.md",
                ROOT / "hermes" / "gaming" / "skills" / "gaming-bot" / "SKILL.md",
                ROOT / "hermes" / "gaming" / "skills" / "gaming-scout" / "SKILL.md",
                ROOT / "hermes" / "gaming" / "skills" / "gaming-scout" / "playbook.md"]
        for p in docs:
            text = p.read_text()
            for k in slots:
                self.assertIn(k, text, p)
            self.assertIn("13:00", text, p)
        self.assertIn("factory.py place", (ROOT / "factory.py").read_text())


if __name__ == "__main__":
    unittest.main()
