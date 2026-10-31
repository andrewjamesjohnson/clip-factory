"""The cast queue (clipfactory/castqueue.py, 2026-10-18): who waits for Andrew's cast check, the stills, his one-tap
Confirm / Not them on the review page, and that nothing else can press them.

Throwaway folders only: a temp state (set before clipfactory is imported), temp Jobs folders (signoff.CAST_ROOTS
pointed at them), one 2.5 s 320x180 synthetic master. No network, no render: the stills are single frames of that
master, never taller than it. The engine's own cast.py is the one beside this factory (<factory>/../.claude/skills/
viral-clip-engine): the integrated tree's in a tree, live's on live."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
# before clipfactory is imported: config.STATE is read once at import
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-castqueue-state-")

import argparse
import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B       # noqa: E402
from clipfactory import castqueue as CQ  # noqa: E402
from clipfactory import collect as K     # noqa: E402
from clipfactory import config as C      # noqa: E402
from clipfactory import direct as D      # noqa: E402
from clipfactory import outlets as O     # noqa: E402
from clipfactory import review as R      # noqa: E402
from clipfactory import signoff as S     # noqa: E402

ENGINE = ROOT.parent / ".claude" / "skills" / "viral-clip-engine"
CAST_PY = ENGINE / "scripts" / "cast.py"
DAY = date(2031, 7, 14)
BOT_LINE = "2026-10-16, checked by Claude from frames at 1.5s and 2.0s of the master. NEEDS ANDREW'S LOOK before posting"
PAGE = True        # what review._from_page says for a browser request from the page itself


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def ffmpeg(*args: str) -> None:
    subprocess.run(["nice", "-n", "19", "ffmpeg", "-nostdin", "-y", "-loglevel", "error", *args], check=True)


def engine():
    """viral-clip-engine's cast.py beside this factory, as a module (None when it is not there)."""
    if not CAST_PY.is_file():
        return None
    spec = importlib.util.spec_from_file_location("engine_cast_for_test", CAST_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def engine_check(job: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["/usr/bin/python3", "-B", str(CAST_PY), "--check", "--job", str(job)],
                          capture_output=True, text=True, timeout=60, cwd=str(CAST_PY.parent))


class CastQueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-castqueue-"))
        cls.old_state, C.STATE = C.STATE, cls.tmp / "state"
        cls.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        cls.old_roots = S.CAST_ROOTS
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(cls.tmp / "drafts")
        raw["review_port"] = 0
        for a in raw["accounts"].values():
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 2, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "tiktok": {"mode": "export"}}
            a.pop("signed_by_hand_stays", None)
        raw["accounts"]["politics"]["skill_dir"] = str(ENGINE)
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.master = cls.tmp / "master.mp4"          # the source video: 2.5 s, 320x180, a moving test picture
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=2.5", "-c:v", "libx264", "-preset",
               "ultrafast", "-pix_fmt", "yuv420p", str(cls.master))
        cls.clipfile = cls.tmp / "clip.mp4"
        ffmpeg("-f", "lavfi", "-i", "color=c=black:s=108x192:d=2.5", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
               "-t", "2.5", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(cls.clipfile))

    @classmethod
    def tearDownClass(cls):
        C.STATE = cls.old_state
        O.TOKENS, D.TOKENS = cls.old_tokens
        S.CAST_ROOTS = cls.old_roots
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------------------------------------ the fixture
    def setUp(self):
        for d in ("state", "drafts", "delivered", "Jobs", "elsewhere"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.jobs = self.tmp / "Jobs"
        self.jobs.mkdir()
        S.CAST_ROOTS = (self.jobs,)
        self.cfg = C.load(self.cfg_path)
        self.acct = self.cfg.account("politics")
        # a town hall: Vance on a plate (not checked), a voter and a nameless anchor on none
        self.town = self.job("CBS Town Hall 10.17.31", "Watch: Vance is pressed on gas prices", people=[
            {"id": "vance", "name": "JD Vance", "title": "VICE PRESIDENT OF THE UNITED STATES", "role": "witness",
             "verified": "", "check_frames": [1.0, 2.0]},
            {"id": "wheaton", "name": "Keyshawn Wheaton", "title": "VOTER, DETROIT", "role": "member", "verified": "",
             "_verify_at": "1s: in the audience"},
            {"id": "anchor", "name": "", "title": "CBS NEWS ANCHOR", "role": "member", "verified": ""}],
            cards={"vance-how-long-is-temporary": ["vance"]})
        # a hearing: Patel checked by a bot (on a plate), Grassley checked by a person; Schiff a proposal
        self.hearing = self.job("SJC Test Oversight 09.15.31", "Oversight of the FBI", people=[
            {"id": "patel", "name": "Kash Patel", "title": "DIRECTOR, FBI", "role": "witness", "verified": BOT_LINE,
             "_verify_at": "CHECK BY EYE at 1.5s."},
            {"id": "grassley", "name": "Chuck Grassley", "title": "CHAIRMAN", "role": "chair",
             "verified": "2031-07-01, by eye at 0:12 of the master"}],
            cards={"patel-yes-or-no": ["patel", "grassley"]})
        prep = json.loads((self.hearing / "cast.json").read_text())
        prep["people"] = prep["people"] + [{"id": "schiff", "name": "Adam Schiff", "title": "U.S. SENATOR, CALIFORNIA",
                                            "role": "member", "verified": "", "check_frames": [0.5, 1.2]}]
        (self.hearing / "cast.prep-schiff.json").write_text(json.dumps(prep, indent=1))
        (self.hearing / "slate.prep-schiff.json").write_text(json.dumps({"clips": [
            {"slug": "patel-fired-for-the-cases", "template": "room", "cards": [{"who": "schiff", "at": 3.0}]}]}))
        # a one-speaker gaggle: Rubio provisional, named in the title
        self.gaggle = self.job("CSPAN Rubio Gaggle 10.17.31", "Secretary Rubio on the plane", people=[
            {"id": "rubio", "name": "Marco Rubio", "title": "SECRETARY OF STATE", "role": "witness", "verified": "",
             "provisional": {"named_as": "Rubio", "source_title": "Secretary Rubio on the plane",
                             "frames": "0:01 and 2s of the master: one man on camera", "by": "politics bot",
                             "at": "2031-07-13"}}],
            cards={"rubio-why-lift-sanctions": ["rubio"]})
        # an old archive roster on no plate, a factory test job, an iCloud copy and a note: never asked about
        self.job("HFSC Old Archive 04.10.19", "Old", people=[
            {"id": "hensarling", "name": "Jeb Hensarling", "title": "CHAIRMAN", "role": "chair", "verified": ""}])
        self.job("MOCK-2031-07-14", "Mock", people=[
            {"id": "mock", "name": "Mock Person", "title": "MOCK", "role": "witness", "verified": ""}],
            cards={"mock-clip": ["mock"]})
        (self.town / "cast 2.json").write_text((self.town / "cast.json").read_text())
        shutil.copytree(self.town, self.jobs / f"{self.town.name} 2")          # an iCloud copy of a whole job folder
        (self.jobs / "_NEXT-TWO-clips.md").write_text("an August note\n")

    def job(self, name: str, title: str, people: list, cards: dict = None, master: bool = True) -> Path:
        d = self.jobs / name
        d.mkdir(parents=True)
        (d / "cast.json").write_text(json.dumps({"hearing": {"body": "test"}, "people": people}, indent=1))
        (d / "project.json").write_text(json.dumps({
            "source": str(self.master) if master else "/nowhere/master.mp4", "source_title": title,
            "source_channel": "Test Channel", "source_url": "https://www.youtube.com/watch?v=abcdefghijk"}))
        (d / "slate.json").write_text(json.dumps({"clips": [
            {"slug": slug, "template": "sitdown", "cards": [{"who": w, "at": 3.4} for w in who]}
            for slug, who in (cards or {}).items()]}))
        return d

    def item(self, job: Path, pid: str) -> dict:
        j = CQ.read_job(job)
        return next(p for p in j["people"] + j["rejected"] if p["id"] == pid)

    def tap(self, op: str, job: Path, pid: str, page: bool = PAGE, by: str = "Andrew", seen: str = None,
            file: str = None):
        it = self.item(job, pid)
        body = {"op": op, "job": job.name, "file": file or it["file"], "person": pid, "by": by,
                "seen": it["key"] if seen is None else seen}
        return R.act(self.cfg, body, page=page)

    def people(self, job: Path, name: str = "cast.json") -> dict:
        return {p["id"]: p for p in json.loads((job / name).read_text())["people"]}

    # ------------------------------------------------------------------------------------------ 1. the list
    def test_01_the_queue_lists_who_waits(self):
        js = CQ.pending(self.cfg)
        by = {j["job"]: j for j in js}
        self.assertEqual(sorted(by), ["CBS Town Hall 10.17.31", "CSPAN Rubio Gaggle 10.17.31",
                                      "SJC Test Oversight 09.15.31"])           # never the archive, MOCK, _ or a copy
        town = by["CBS Town Hall 10.17.31"]
        self.assertEqual([p["id"] for p in town["people"]], ["vance"])          # the voter is on no plate
        self.assertEqual(town["unplated"], ["wheaton"])
        self.assertEqual(town["nameless"], ["anchor"])
        hear = {p["id"]: p for p in by["SJC Test Oversight 09.15.31"]["people"]}
        self.assertEqual(sorted(hear), ["patel", "schiff"])                     # Grassley is checked
        self.assertEqual(hear["patel"]["why"], "bot")
        self.assertEqual(hear["schiff"]["file"], "cast.prep-schiff.json")
        self.assertTrue(hear["schiff"]["proposal"])
        self.assertEqual([u["slug"] for u in hear["schiff"]["unlocks"]], ["patel-fired-for-the-cases"])
        rub = by["CSPAN Rubio Gaggle 10.17.31"]["people"][0]
        self.assertEqual((rub["why"], rub["named_as"]), ("provisional", "Rubio"))
        self.assertEqual(CQ.counts(js), (4, 4))
        self.assertEqual(CQ.morning_line(self.cfg),
                         "Casts: 4 people wait for your check (4 clips name them). They are at the top of Clip Review.")
        text = "\n".join(CQ.lines(self.cfg))
        self.assertIn("no command confirms a cast", text)
        self.assertIn("on no plate, not asked", text)

    def test_02_the_times_come_from_the_bots_own_words(self):
        self.assertEqual(CQ.check_frames({"check_frames": [180, 40, "x", -1, 40.04]}), [40.0, 180.0])
        self.assertEqual(CQ.check_frames({"_verify_at": "40s and 180s: seated left, red tie"}), [40.0, 180.0])
        self.assertEqual(CQ.check_frames({"_verify_at": "Vance calls him 'Tony' at 4:31"}), [])  # a clock: caption time
        self.assertEqual(CQ.check_frames({"provisional": {"frames": "0:04, 0:41, 1:12:05 of the master"}}),
                         [4.0, 41.0, 4325.0])
        self.assertEqual(CQ.check_frames({"_verify_at": "CHECK BY EYE at 12253s.",
                                          "verified": "checked by Claude from frames at 12253.5s, 12269.9s and "
                                                      "12279.5s and 12300s"}), [12253.0, 12269.9, 12279.5, 12300.0])
        self.assertEqual(CQ.check_frames({"verified": "Andrew, by eye at 12253s"}), [])   # a person's own check
        self.assertEqual(CQ.still_name("Vance", 40), "vance-40.0.jpg")
        self.assertEqual(CQ.still_name("o'neil/x", 1.25), "o-neil-x-1.2.jpg")
        self.assertEqual([CQ._clock(x) for x in (40, 1.5, 0.5, 4325, 12279.5, 59.96)],
                         ["0:40", "0:01.5", "0:00.5", "1:12:05", "3:24:39.5", "1:00"])

    def test_03_the_engine_holds_the_same_rules(self):
        eng = engine()
        if eng is None or not hasattr(eng, "needs_look"):
            self.skipTest(f"{CAST_PY} has no cast queue rules yet (the engine part of politics-castqueue)")
        samples = [{"check_frames": [3, 1, 1.04, 2, 9, 5]}, {"_verify_at": "40s, 3:00 and 180 s"},
                   {"provisional": {"frames": "0:01, 2s and 1:02:03"}, "verified": ""},
                   {"verified": BOT_LINE, "_verify_at": "at 1.5s"}, {"verified": "Andrew, by eye at 9s"},
                   {"name": "A B", "title": "T", "check_frames": [1], "rejected": {"name": "a  b", "title": "t",
                                                                                   "frames": [1.0]}},
                   {"name": "A B", "title": "T", "check_frames": [2], "rejected": {"name": "A B", "title": "T",
                                                                                   "frames": [1.0]}},
                   {"name": "A B", "rejected": {"name": "A B", "frames": ["?"]}}, {},
                   {"name": "A C", "title": "T", "verified": "Andrew, by eye at 9s",
                    "rejected": {"name": "A B", "title": "T", "frames": []}},
                   {"name": "A B", "title": "T2", "rejected": {}}, {"name": "A B", "rejected": None}]
        for p in samples:
            self.assertEqual(CQ.check_frames(p), eng.check_frames(p), p)
            self.assertEqual(CQ.needs_look(p), eng.needs_look(p), p)
            self.assertEqual(CQ.rejected_now(p), eng.rejected_now(p), p)
            self.assertEqual(CQ.rejected_changed(p), eng.rejected_changed(p), p)
        # his Not them stands whatever the bot changes (the times, the name, a check in a person's words): a new look
        self.assertEqual([CQ.needs_look(p) for p in samples[5:8] + samples[9:]],
                         ["rejected", "relook", "rejected", "relook", "relook", "rejected"])
        self.assertEqual(CQ.rejected_changed(samples[6]), "the times")
        self.assertEqual(CQ.rejected_changed(samples[9]), "the name")
        for pid, sec in (("vance", 40), ("O'Neil", 1.25), ("", 0)):
            self.assertEqual(CQ.still_name(pid, sec), eng.still_name(pid, sec))
        self.assertEqual((CQ.STILLS, CQ.MAX_FRAMES, CQ.STILL_HEIGHT), (eng.STILLS, eng.MAX_FRAMES, eng.STILL_HEIGHT))
        self.assertEqual(S.BOT_VERIFIED.pattern, eng.BOT_VERIFIED.pattern)

    # ------------------------------------------------------------------------------------------ 2. the stills
    def test_04_stills_are_small_frames_of_the_master(self):
        ok, said = self.tap("cast-stills", self.town, "vance")
        self.assertTrue(ok, said)
        for sec in (1.0, 2.0):
            p = self.town / "castcheck" / f"vance-{sec}.jpg"
            self.assertTrue(p.is_file())
            h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
                                "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout.strip()
            self.assertEqual(h, "180")                                     # never taller than the master
            self.assertTrue(CQ.still_ok(p))
            self.assertTrue(R._allowed_file(self.cfg, p))                   # the page may show it
        self.assertEqual(list((self.town / "castcheck").glob("*.part.jpg")), [])
        stray = self.tmp / "elsewhere" / "castcheck" / "x-1.0.jpg"
        stray.parent.mkdir(parents=True)
        shutil.copy(self.town / "castcheck" / "vance-1.0.jpg", stray)
        self.assertFalse(CQ.still_ok(stray))
        self.assertFalse(R._allowed_file(self.cfg, stray))
        ok, said = self.tap("cast-stills", self.town, "vance")
        self.assertEqual((ok, said), (True, "The stills are there."))
        gone = self.job("CBS Gone 10.17.31", "Gone", people=[
            {"id": "x", "name": "Some One", "title": "T", "role": "witness", "verified": "", "check_frames": [1]}],
            cards={"c": ["x"]}, master=False)
        ok, said = self.tap("cast-stills", gone, "x")
        self.assertFalse(ok)
        self.assertIn("not on this Mac", said)

    # ------------------------------------------------------------------------------------------ 3. confirm
    def test_05_confirm_writes_his_check_and_the_cast_check_passes(self):
        self.tap("cast-stills", self.hearing, "patel")
        with self.assertRaises(SystemExit) as no:
            self.tap("cast-confirm", self.town, "vance")                    # no stills pulled yet
        self.assertIn("no stills", str(no.exception))
        ok, said = self.tap("cast-confirm", self.hearing, "patel")
        self.assertTrue(ok, said)
        self.assertIn("Kash Patel confirmed", said)
        p = self.people(self.hearing)
        self.assertTrue(p["patel"]["verified"].startswith("Andrew, in Clip Review's Casts ("), p["patel"]["verified"])
        self.assertIn("looked at the stills at 0:01.5 of the master", p["patel"]["verified"])
        self.assertIn("this is Kash Patel, DIRECTOR, FBI", p["patel"]["verified"])
        self.assertEqual(p["patel"]["verified_before"], BOT_LINE)
        self.assertIsNone(S.BOT_VERIFIED.search(p["patel"]["verified"]))  # his line never reads as a bot's
        self.assertEqual(p["grassley"]["verified"], "2031-07-01, by eye at 0:12 of the master")   # nobody else
        self.assertNotIn("patel", [x["id"] for x in CQ.read_job(self.hearing)["people"]])
        log = [json.loads(x) for x in (C.STATE / "castqueue" / "log.jsonl").read_text().splitlines()]
        self.assertEqual([(r["op"], r["person"], r["by"]) for r in log if r["op"] == "confirm"],
                         [("confirm", "patel", "Andrew")])
        if CAST_PY.is_file():
            r = engine_check(self.hearing)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)          # cast.py --check passes after
            self.assertIn("cast ok", r.stdout)
            self.assertIn("schiff", said.lower())                           # the proposal still waits on this source
        rows = CQ.confirmed(self.cfg, CQ.since(self.cfg, "today"))
        self.assertEqual([(r["job"], r["person"]) for r in rows], [("SJC Test Oversight 09.15.31", "patel")])

    def test_06_a_proposal_he_confirms_goes_into_cast_json(self):
        self.tap("cast-stills", self.hearing, "schiff")
        ok, said = self.tap("cast-confirm", self.hearing, "schiff")
        self.assertTrue(ok, said)
        main = self.people(self.hearing)
        self.assertIn("schiff", main)
        self.assertTrue(main["schiff"]["verified"].startswith("Andrew, in Clip Review's Casts"))
        self.assertEqual(main["schiff"]["added_from"], "cast.prep-schiff.json")
        self.assertEqual(main["patel"]["verified"], BOT_LINE)                  # only the person he tapped
        self.assertTrue(self.people(self.hearing, "cast.prep-schiff.json")["schiff"]["verified"].startswith("Andrew"))
        self.assertNotIn("schiff", [x["id"] for x in CQ.read_job(self.hearing)["people"]])

    # ------------------------------------------------------------------------------------------ 4. nobody else
    def test_07_only_the_page_and_only_a_person(self):
        self.tap("cast-stills", self.hearing, "patel")
        before = (self.hearing / "cast.json").read_text()
        ok, said = self.tap("cast-confirm", self.hearing, "patel", page=False)     # a script, a command, a bot
        self.assertFalse(ok)
        self.assertIn("only on the review page", said)
        for by in ("Claude", "the bot", "Hermes", "politics bot", "", "Bob"):
            with self.assertRaises(SystemExit, msg=by):
                self.tap("cast-confirm", self.hearing, "patel", by=by)
        self.assertEqual((self.hearing / "cast.json").read_text(), before)
        # over HTTP: the page's token and its Origin, or nothing
        srv = R.Server(("127.0.0.1", 0), R.make_handler(self.cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        host = f"127.0.0.1:{self.cfg.review_port}"
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        it = self.item(self.hearing, "patel")
        body = {"op": "cast-confirm", "job": self.hearing.name, "file": "cast.json", "person": "patel",
                "by": "Andrew", "seen": it["key"], "date": DAY.isoformat()}

        def post(extra):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/act", data=json.dumps(body).encode(), method="POST",
                                         headers={"Host": host, "Content-Type": "application/json",
                                                  "X-Token": R.token(), **extra})
            try:
                with direct.open(req, timeout=60) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        code, res = post({})                                                       # no Origin: not the page
        self.assertEqual((code, res["ok"]), (200, False), res)
        self.assertIn("only on the review page", res["message"])
        code, res = post({"Origin": f"http://{host}", "Sec-Fetch-Site": "cross-site"})
        self.assertFalse(res["ok"], res)
        code, res = post({"Origin": "http://evil.example"})
        self.assertEqual(code, 403)
        self.assertEqual((self.hearing / "cast.json").read_text(), before)
        code, res = post({"Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual((code, res["ok"]), (200, True), res)
        self.assertTrue(self.people(self.hearing)["patel"]["verified"].startswith("Andrew, in Clip Review's Casts"))
        # and no command confirms
        import factory
        for argv in (["casts", "confirm", "patel"], ["casts", "--person", "patel"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                factory.main(argv)

    def test_08_never_outside_the_jobs_folders(self):
        self.tap("cast-stills", self.town, "vance")
        it = self.item(self.town, "vance")
        base = {"op": "cast-confirm", "person": "vance", "by": "Andrew", "seen": it["key"]}
        outside = self.tmp / "elsewhere" / "Outside Job"
        outside.mkdir(parents=True)
        shutil.copy(self.town / "cast.json", outside / "cast.json")
        (self.jobs / "Linked Job").symlink_to(outside, target_is_directory=True)
        for job, f in (("../elsewhere/Outside Job", "cast.json"), ("Linked Job", "cast.json"), ("..", "cast.json"),
                       (self.town.name, "../cast.json"), (self.town.name, "slate.json"), (self.town.name, "cast 2.json"),
                       ("MOCK-2031-07-14", "cast.json"), ("Nobody 01.01.31", "cast.json")):
            with self.assertRaises(SystemExit, msg=(job, f)):
                R.act(self.cfg, dict(base, job=job, file=f), page=PAGE)
        self.assertNotIn("verified_before", (outside / "cast.json").read_text())
        self.assertNotIn("Andrew", (outside / "cast.json").read_text())
        self.assertFalse(S.cast_place_ok(outside / "cast.json"))
        with self.assertRaises(SystemExit):
            S.edit_cast(outside, ["cast.json"], lambda d: None)
        self.assertNotIn("Linked Job", [j["job"] for j in CQ.pending(self.cfg)])

    # ------------------------------------------------------------------------------------------ 5. the page
    def test_09_the_page_with_nobody_and_with_some_waiting(self):
        now = self.cfg.slot_time(DAY, "06:00")
        S.CAST_ROOTS = (self.tmp / "no-jobs-here",)
        page = R.page(self.cfg, DAY, now=now, tok="t")
        self.assertIn('<section class="casts" id="casts" aria-label="Casts to check" hidden>', page)
        self.assertNotIn("data-op=\"cast-", page)
        self.assertEqual(R.live(self.cfg, DAY, now)["casts"]["items"], [])
        S.CAST_ROOTS = (self.jobs,)
        self.tap("cast-stills", self.town, "vance")
        page = R.page(self.cfg, DAY, now=now, tok="t")
        self.assertLess(page.index('id="casts"'), page.index('id="needs"'))      # at the very top
        self.assertIn('Casts to check <span class="n">4</span>', page)
        self.assertNotIn('id="casts" aria-label="Casts to check" hidden', page)
        self.assertIn("That's JD Vance</button>", page)                         # stills shown: Confirm
        self.assertIn('data-op="cast-stills"', page)                              # Patel's not pulled yet
        self.assertIn("Marked checked by a bot, not by you", page)
        self.assertIn("Named in the title as &quot;Rubio&quot;", page)
        self.assertIn("/file?p=", page)
        self.assertIn(f'data-key="{self.item(self.town, "vance")["key"]}"', page)
        live = R.live(self.cfg, DAY, now)
        self.assertEqual(live["casts"]["n"], 4)
        self.assertEqual(len(live["casts"]["items"]), 4)
        self.assertEqual(R.live(self.cfg, DAY, now, acct="gaming")["casts"]["items"], [])   # not on GTA's view
        self.assertTrue(R.live(self.cfg, DAY, now, acct="politics")["casts"]["items"])
        buf = io.StringIO()
        import factory
        with contextlib.redirect_stdout(buf):
            factory.cmd_morning(self.cfg, argparse.Namespace(account=None, date=DAY.isoformat(), notify=False))
        self.assertIn("Casts: 4 people wait for your check", buf.getvalue())
        said = buf.getvalue().splitlines()
        casts = next(i for i, ln in enumerate(said) if ln.startswith("Casts: 4 people wait"))
        accts = [i for i, ln in enumerate(said) if "no clips made for" in ln or "drafts ready to sign" in ln]
        self.assertTrue(accts and casts < min(accts), said)       # before the accounts' lines (cut at 220 characters)

    def test_10_the_seen_key_races(self):
        self.tap("cast-stills", self.town, "vance")
        key = self.item(self.town, "vance")["key"]
        data = json.loads((self.town / "cast.json").read_text())             # the bot rewrites the title meanwhile
        data["people"][0]["title"] = "VICE PRESIDENT"
        (self.town / "cast.json").write_text(json.dumps(data, indent=1))
        before = (self.town / "cast.json").read_text()
        with self.assertRaises(SystemExit) as e:
            self.tap("cast-confirm", self.town, "vance", seen=key)
        self.assertIn("changed since the page showed it", str(e.exception))
        self.assertEqual((self.town / "cast.json").read_text(), before)
        key = self.item(self.town, "vance")["key"]                          # a still re-pulled: another key
        still = self.town / "castcheck" / "vance-1.0.jpg"
        still.write_bytes(still.read_bytes() + b"\0")
        with self.assertRaises(SystemExit):
            self.tap("cast-confirm", self.town, "vance", seen=key)
        key = self.item(self.town, "vance")["key"]                          # two tabs, one key: the first wins
        ok, _ = self.tap("cast-confirm", self.town, "vance", seen=key)
        self.assertTrue(ok)
        with self.assertRaises(SystemExit):
            R.act(self.cfg, {"op": "cast-confirm", "job": self.town.name, "file": "cast.json", "person": "vance",
                             "by": "Andrew", "seen": key}, page=PAGE)
        self.assertEqual(json.loads((self.town / "cast.json").read_text())["people"][0]["verified"].count("Andrew"), 1)
        with self.assertRaises(SystemExit):
            R.act(self.cfg, {"op": "cast-confirm", "job": self.town.name, "file": "cast.json", "person": "vance",
                             "by": "Andrew"}, page=PAGE)                       # no key at all

    # ------------------------------------------------------------------------------------------ 6. Not them
    def deliver(self, hhmm: str, job: Path, pid: str, slug: str) -> None:
        d = self.acct.delivered_dirs(DAY)[0] / f"{hhmm} {slug}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.clipfile, d / f"01-{slug}.mp4")
        for p in ("youtube", "tiktok_reach", "facebook", "instagram"):
            (d / f"{p}.txt").write_text(f"TITLE\nThe exchange at {hhmm}\n\nCAPTION\nWords for {p}.\n")
        p = self.people(job)[pid]
        (d / "cast-check.json").write_text(json.dumps({
            "rule": "test", "slot": hhmm, "slug": slug, "job": job.name, "cast_file": str(job / "cast.json"),
            "source_title": "t", "people": [{"id": pid, "name": p["name"], "title": p["title"],
                                             "named_as": (p.get("provisional") or {}).get("named_as", "")}]}))

    def test_11_not_them_holds_the_clip_until_he_undoes_it(self):
        self.tap("cast-stills", self.gaggle, "rubio")
        ok, said = self.tap("cast-reject", self.gaggle, "rubio")
        self.assertTrue(ok, said)
        rub = self.people(self.gaggle)["rubio"]
        self.assertEqual((rub["rejected"]["by"], rub["rejected"]["name"], rub["rejected"]["frames"]),
                         ("Andrew", "Marco Rubio", [1.0, 2.0]))
        self.assertEqual(rub["verified"], "")
        self.assertEqual(CQ.needs_look(rub), "rejected")
        eng = engine()
        if eng is not None and hasattr(eng, "rejected_now"):
            r = engine_check(self.gaggle)
            self.assertEqual(r.returncode, 1, r.stdout)                         # the engine refuses it in every clip
            self.assertIn("the owner said the stills at 1s, 2s are not Marco Rubio", r.stdout)
        # a clip already cut: no Approve on its card, and Approve refuses it
        self.deliver("0700", self.gaggle, "rubio", "rubio-why-lift-sanctions")
        K.collect(self.cfg, self.acct, DAY)
        card = R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))["cards"][0]
        self.assertIn("You said Marco Rubio is not who the plate names", card["bottom"])
        self.assertNotIn('data-op="approve"', card["bottom"])
        out = S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertIn("NOT signed: you said Marco Rubio is not who the plate names", " ".join(out))
        self.assertEqual(B.read(self.acct, DAY)["slots"]["07:00"]["status"], B.READY)
        self.assertEqual(self.people(self.gaggle)["rubio"]["verified"], "")
        # the bot fixes the times: back in his Casts for a new look, and STILL refused everywhere until he confirms
        data = json.loads((self.gaggle / "cast.json").read_text())
        data["people"][0]["check_frames"] = [0.5]
        (self.gaggle / "cast.json").write_text(json.dumps(data, indent=1))
        it = self.item(self.gaggle, "rubio")
        self.assertEqual((it["why"], it["changed"]), ("relook", "the times"))
        self.assertIn("rubio", [x["id"] for x in CQ.read_job(self.gaggle)["people"]])      # it waits for him again
        if eng is not None and hasattr(eng, "rejected_changed"):
            r = engine_check(self.gaggle)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("and the times changed since: it waits for his new look there", r.stdout)
            with contextlib.redirect_stderr(io.StringIO()):                  # the per-clip check (one speaker) too
                self.assertTrue(eng.check({"people": [self.people(self.gaggle)["rubio"]]},
                                          provisional_title="Secretary Rubio on the plane"))
        cc = B.read(self.acct, DAY)["slots"]["07:00"]["cast_check"]
        self.assertEqual([h["why"] for h in CQ.holds(cc)], ["relook"])
        card = R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))["cards"][0]
        self.assertIn("the bot changed the times since", card["bottom"])
        self.assertNotIn('data-op="approve"', card["bottom"])
        self.assertIn("NOT signed", " ".join(S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")))
        html_ = next(i["html"] for i in CQ.section(self.cfg)["items"] if 'data-person="rubio"' in i["html"])
        self.assertIn("You said Not them to Marco Rubio on ", html_)
        self.assertIn("the bot changed the times since", html_)
        data["people"][0].pop("check_frames")
        (self.gaggle / "cast.json").write_text(json.dumps(data, indent=1))
        self.assertEqual(self.item(self.gaggle, "rubio")["why"], "rejected")       # the times he saw: as he said
        # or he undoes it
        ok, said = self.tap("cast-undo", self.gaggle, "rubio")
        self.assertTrue(ok, said)
        rub = self.people(self.gaggle)["rubio"]
        self.assertNotIn("rejected", rub)
        self.assertEqual(rub["rejected_undone"][0]["undone_by"], "Andrew")
        out = S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertIn("approved by Andrew", " ".join(out))
        self.assertIn("Andrew, at the 2031-07-14 sign-off in Clip Review", self.people(self.gaggle)["rubio"]["verified"])
        rows = CQ.confirmed(self.cfg, datetime(2000, 1, 1, tzinfo=timezone.utc))
        self.assertEqual([(r["op"], r["person"]) for r in rows], [("approve-confirm", "rubio")])

    def test_12_approve_never_writes_over_a_not_them(self):
        self.tap("cast-stills", self.gaggle, "rubio")
        self.deliver("0845", self.gaggle, "rubio", "rubio-again")
        K.collect(self.cfg, self.acct, DAY)
        s = B.read(self.acct, DAY)["slots"]["08:45"]
        self.tap("cast-reject", self.gaggle, "rubio")
        said = S.confirm_cast(self.cfg, s, "Andrew", DAY)                      # a race: the slot was already signed
        self.assertIn("NOT written for Marco Rubio: you said not them in Casts", said)
        self.assertEqual(self.people(self.gaggle)["rubio"]["verified"], "")

    def test_14_a_bot_change_never_lifts_his_not_them(self):
        self.tap("cast-stills", self.gaggle, "rubio")
        self.deliver("0700", self.gaggle, "rubio", "rubio-why-lift-sanctions")     # its plate says Marco Rubio
        K.collect(self.cfg, self.acct, DAY)
        self.tap("cast-reject", self.gaggle, "rubio")
        cc = B.read(self.acct, DAY)["slots"]["07:00"]["cast_check"]
        data = json.loads((self.gaggle / "cast.json").read_text())             # the bot fixes the name
        data["people"][0]["name"] = "Marco Antonio Rubio"
        (self.gaggle / "cast.json").write_text(json.dumps(data, indent=1))
        it = self.item(self.gaggle, "rubio")
        self.assertEqual((it["name"], it["why"], it["changed"]), ("Marco Antonio Rubio", "relook", "the name"))
        if CAST_PY.is_file() and hasattr(engine(), "rejected_changed"):
            self.assertEqual(engine_check(self.gaggle).returncode, 1)            # still refused: he has not looked
        self.assertEqual([h["why"] for h in CQ.holds(cc)], ["relook"])
        ok, said = self.tap("cast-confirm", self.gaggle, "rubio")              # he confirms the new name
        self.assertTrue(ok, said)
        rub = self.people(self.gaggle)["rubio"]
        self.assertNotIn("rejected", rub)                                      # his check lifts his own Not them
        self.assertEqual(rub["rejected_undone"][0]["how"], "confirmed after a new look")
        self.assertIn("a new look, after his Not them of ", rub["verified"])
        self.assertIsNone(S.BOT_VERIFIED.search(rub["verified"]))
        if CAST_PY.is_file():
            self.assertEqual(engine_check(self.gaggle).returncode, 0)            # the new name can be cut now
        # ... but the clip cut under the old name keeps the old plate: held until Decline or Make again
        self.assertEqual([(h["name"], h["why"]) for h in CQ.holds(cc)], [("Marco Rubio", "renamed")])
        self.assertEqual(CQ.cast_now(cc), (["Marco Rubio"], []))
        card = R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))["cards"][0]
        self.assertNotIn('data-op="approve"', card["bottom"])
        self.assertIn("a cut clip keeps its plate", card["bottom"])
        self.assertIn("NOT signed", " ".join(S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")))

    def test_16_only_his_new_look_ends_a_not_them_over_new_times(self):
        self.tap("cast-stills", self.gaggle, "rubio")
        self.deliver("0700", self.gaggle, "rubio", "rubio-why-lift-sanctions")
        K.collect(self.cfg, self.acct, DAY)
        self.tap("cast-reject", self.gaggle, "rubio")
        cc = B.read(self.acct, DAY)["slots"]["07:00"]["cast_check"]
        data = json.loads((self.gaggle / "cast.json").read_text())
        data["people"][0]["check_frames"] = [0.5, 1.5]                           # the bot picks new times
        (self.gaggle / "cast.json").write_text(json.dumps(data, indent=1))
        card = self.item(self.gaggle, "rubio")
        self.assertEqual(card["why"], "relook")
        with self.assertRaises(SystemExit) as e:                                 # the new stills first
            self.tap("cast-confirm", self.gaggle, "rubio")
        self.assertIn("no stills", str(e.exception))
        with self.assertRaises(SystemExit):
            self.tap("cast-reject", self.gaggle, "rubio")                        # never over stills he has not seen
        self.tap("cast-stills", self.gaggle, "rubio")
        ok, said = self.tap("cast-reject", self.gaggle, "rubio")                 # not them again, at the new times
        self.assertTrue(ok, said)
        rub = self.people(self.gaggle)["rubio"]
        self.assertEqual((rub["rejected"]["frames"], rub["rejected_before"][0]["frames"]), ([0.5, 1.5], [1.0, 2.0]))
        self.assertEqual(CQ.needs_look(rub), "rejected")
        data = json.loads((self.gaggle / "cast.json").read_text())
        data["people"][0]["check_frames"] = [0.8]                                # and new times once more
        (self.gaggle / "cast.json").write_text(json.dumps(data, indent=1))
        self.tap("cast-stills", self.gaggle, "rubio")
        ok, said = self.tap("cast-confirm", self.gaggle, "rubio")                # this time it is him
        self.assertTrue(ok, said)
        self.assertEqual(CQ.cast_now(cc), ([], ["Marco Rubio"]))                 # the same plate: he checked it
        self.assertIn("approved by Andrew", " ".join(S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")))
        rows = [r["op"] for r in CQ._log_rows() if r.get("person") == "rubio"]
        self.assertEqual(rows, ["stills", "reject", "stills", "reject", "stills", "confirm"])
        self.assertEqual(CQ.standing(self.gaggle.name), {})

    def test_17_a_removed_entry_or_a_lost_not_them_still_holds(self):
        self.tap("cast-stills", self.hearing, "patel")
        self.deliver("0845", self.hearing, "patel", "patel-yes-or-no")
        K.collect(self.cfg, self.acct, DAY)
        self.tap("cast-reject", self.hearing, "patel")
        cc = B.read(self.acct, DAY)["slots"]["08:45"]["cast_check"]
        self.assertEqual([h["why"] for h in CQ.holds(cc)], ["rejected"])
        data = json.loads((self.hearing / "cast.json").read_text())
        keep = json.loads(json.dumps(data))
        data["people"][0].pop("rejected")                                         # the bot takes his Not them out
        (self.hearing / "cast.json").write_text(json.dumps(data, indent=1))
        self.assertEqual([h["why"] for h in CQ.holds(cc)], ["log"])               # the tap log still says it
        self.assertIn("NOT signed", " ".join(S.approve(self.cfg, self.acct, DAY, ["08:45"], "Andrew")))
        self.assertIn("the cast file no longer says so", "\n".join(CQ.lines(self.cfg)))
        data["people"] = [p for p in data["people"] if p["id"] != "patel"] + [   # ... or the entry, under a new id
            dict(keep["people"][0], id="kpatel")]
        data["people"][-1].pop("rejected")
        (self.hearing / "cast.json").write_text(json.dumps(data, indent=1))
        self.assertEqual([h["why"] for h in CQ.holds(cc)], ["gone"])
        cc2 = dict(cc, people=[dict(cc["people"][0], id="kpatel")])              # a clip cut under the new id
        self.assertEqual([h["why"] for h in CQ.holds(cc2)], ["log"])
        card = R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))["cards"][0]
        self.assertIn("Kash Patel is no longer in the job&#x27;s cast.json", card["bottom"])
        self.assertNotIn('data-op="approve"', card["bottom"])
        self.assertIn("NOT signed", " ".join(S.approve(self.cfg, self.acct, DAY, ["08:45"], "Andrew")))
        self.assertEqual(B.read(self.acct, DAY)["slots"]["08:45"]["status"], B.READY)

    def test_18_not_them_only_over_the_stills_he_sees(self):
        page = R.page(self.cfg, DAY, now=self.cfg.slot_time(DAY, "06:00"), tok="t")
        vance = next(i["html"] for i in CQ.section(self.cfg)["items"] if 'data-person="vance"' in i["html"])
        self.assertNotIn('data-op="cast-reject"', vance)                          # times, no stills yet: no Not them
        self.assertIn('data-op="cast-stills"', vance)
        with self.assertRaises(SystemExit) as e:
            self.tap("cast-reject", self.town, "vance")
        self.assertIn("no stills", str(e.exception))
        self.assertNotIn("rejected", self.people(self.town)["vance"])
        self.tap("cast-stills", self.town, "vance")
        vance = next(i["html"] for i in CQ.section(self.cfg)["items"] if 'data-person="vance"' in i["html"])
        self.assertIn('data-op="cast-reject"', vance)
        ok, _ = self.tap("cast-reject", self.town, "vance")
        self.assertTrue(ok)
        self.assertEqual(self.people(self.town)["vance"]["rejected"]["seen"], [1.0, 2.0])
        self.assertIn("Casts to check", page)

    def test_19_a_video_that_cannot_be_had_is_not_asked_about(self):
        gone = self.job("CBS Gone 10.17.31", "Gone", people=[
            {"id": "x", "name": "Some One", "title": "T", "role": "witness", "verified": BOT_LINE,
             "check_frames": [1]}], cards={"c": ["x"]}, master=False)
        pj = json.loads((gone / "project.json").read_text())
        self.assertEqual(self.item(gone, "x")["why"], "bot")                      # a link: the prep fetches it again
        html_ = next(i["html"] for i in CQ.section(self.cfg)["items"] if 'data-person="x"' in i["html"])
        self.assertIn("The 15:00 prep downloads it again", html_)
        self.assertNotIn("data-op=", html_.replace('data-op="cast-reject"', ""))  # nothing to tap yet
        self.assertNotIn('data-op="cast-reject"', html_)
        self.assertIn("not on this Mac (the 15:00 prep downloads it again)", "\n".join(CQ.lines(self.cfg)))
        for change in ({"source_url": ""}, {"source_gone": "2031-07-14: the video was taken down"}):
            (gone / "project.json").write_text(json.dumps(dict(pj, **change)))
            j = CQ.read_job(gone)
            self.assertEqual(([p["id"] for p in j["people"]], [p["id"] for p in j["stuck"]]), ([], ["x"]))
            self.assertFalse(any('data-person="x"' in i["html"] for i in CQ.section(self.cfg)["items"]))
            self.assertEqual(CQ.counts(CQ.pending(self.cfg))[0], 4)              # not counted as waiting
            self.assertIn("Approve at the sign-off is the check", "\n".join(CQ.lines(self.cfg)))
        # the Watch links: YouTube's own hosts only
        self.assertTrue(CQ._at("https://www.youtube.com/watch?v=abc", 40).endswith("&t=40s"))
        self.assertTrue(CQ._at("https://youtu.be/abc", 40).endswith("?t=40s"))
        for bad in ("https://notyoutube.com/watch?v=abc", "https://youtube.com.evil.example/x", "javascript:alert(1)",
                    "https://evil.example/?youtube.com", "ftp://www.youtube.com/x"):
            self.assertEqual(CQ._at(bad, 40), "", bad)
        # the 15:00 notice says what it is (Clip Review.app shows every notice as "Clip drafts ready to sign")
        buf = io.StringIO()
        import factory
        with contextlib.redirect_stdout(buf):
            factory.main(["casts", "--notify"])
        self.assertTrue(buf.getvalue().startswith("Casts to check (not drafts): 4 people wait"), buf.getvalue())

    def test_15_a_card_whose_person_he_checked_in_casts_says_so(self):
        self.tap("cast-stills", self.hearing, "patel")
        self.deliver("0845", self.hearing, "patel", "patel-yes-or-no")
        K.collect(self.cfg, self.acct, DAY)
        bottom = lambda: R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))["cards"][0]["bottom"]
        self.assertIn("Cast not checked yet", bottom())
        self.tap("cast-confirm", self.hearing, "patel")
        self.assertIn("Cast checked by you since it was cut: Kash Patel", bottom())
        self.assertNotIn("Cast not checked yet", bottom())
        self.assertIn('data-op="approve"', bottom())
        out = " ".join(S.approve(self.cfg, self.acct, DAY, ["08:45"], "Andrew"))
        self.assertIn("approved by Andrew", out)
        self.assertTrue(self.people(self.hearing)["patel"]["verified"].startswith("Andrew, in Clip Review's Casts"))

    def test_13_the_command(self):
        import factory
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            factory.main(["casts"])
        self.assertIn("JD Vance, VICE PRESIDENT OF THE UNITED STATES: not checked", buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            factory.main(["casts", "--json"])
        self.assertEqual(len(json.loads(buf.getvalue())), 3)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            factory.main(["casts", "--stills"])
        self.assertIn("JD Vance: 2 pulled", buf.getvalue())
        self.tap("cast-confirm", self.town, "vance")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            factory.main(["casts", "--confirmed", "--since", "yesterday"])
        self.assertIn("CBS Town Hall 10.17.31  JD Vance  (Casts)", buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            factory.main(["casts", "--notify"])
        self.assertIn("(not shown: a test or a copy)", buf.getvalue())     # never a notice from a test
        self.assertFalse((C.STATE / "notify-pending.txt").exists())
        tz = self.cfg.tz()
        today = self.cfg.now().date()
        self.assertEqual(CQ.since(self.cfg, "today"), datetime.combine(today, datetime.min.time(), tz))
        self.assertEqual(CQ.since(self.cfg, "08:30").hour, 8)
        self.assertEqual(CQ.since(self.cfg, "2031-07-01"), datetime(2031, 7, 1, tzinfo=tz))
        self.assertEqual(CQ.since(self.cfg, "yesterday"), datetime.combine(today - timedelta(days=1),
                                                                         datetime.min.time(), tz))


if __name__ == "__main__":
    unittest.main()
