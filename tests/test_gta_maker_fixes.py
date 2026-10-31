import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""The GTA maker's 10-17 audit fixes, offline: the picks (skip what preflight refuses, try the next), the film on a
reaction, the face's name, slots never stuck 'making', the comment question, dedupe, the owner switches (spread,
the REACTIONS duo) and the 03:45 retry agent. No skill is run; run.py is faked."""
import fcntl
import json
import subprocess
import sys
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import makers as M     # noqa: E402
from clipfactory import scout as SC     # noqa: E402
from clipfactory import timer as TM     # noqa: E402

DAY = date(2030, 1, 7)
NOW = datetime.now(timezone.utc)


def row(n: int, kind: str = "breakdown", channel: str = "", title: str = "", duration: int = 900, age_d: float = 1.0,
        **kw) -> dict:
    up = (NOW - timedelta(days=age_d)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return dict({"url": f"https://www.youtube.com/watch?v=vid{n:08d}", "status": "new", "kind": kind,
                 "score": 100 - n, "channel": channel or f"ch{n}", "name": f"Creator {n}",
                 "title": title or f"upload {n}", "duration": duration, "uploaded": up}, **kw)


def cand(n: int, a: float, b: float, music: float = 0.0, furniture=(), refuses: bool = False) -> dict:
    ev = [f"music {music:.0%}" + (" - preflight refuses" if refuses else "")] if music >= 0.1 else []
    return {"n": n, "start": a, "end": b, "music_share": music, "furniture": list(furniture), "events": ev,
            "score": 10 - n}


class Base(unittest.TestCase):
    def setUp(self):
        self._state = C.STATE
        self.fresh()

    def fresh(self):
        """A new account, state and skill folder (a test may start over inside itself)."""
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-gtafix-"))
        C.STATE = self.tmp / "state"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "drafts")
        a = raw["accounts"]["gaming"]
        a.pop("gta_maker", None)            # each test sets the owner switches it needs
        # the b-roll and gameplay lanes' own mechanics: since 2026-10-29 the live Gaming deals "best" slots (Best of
        # GTA, tests/test_best_of_gta.py) and counts its day by dated "volume" rules (6, then 8 from 11-19), which a
        # 2030 test day would read; these tests keep the rotation and the plain slots they were written for
        a["formats"], a["slot_formats"] = ["underlay", "pip"], ["underlay", "pip", "gameplay"]
        a.pop("volume", None)
        a["daily_target"] = len(a["slots"])   # the ten slots these tests count (the live daily_target is 8 since 10-29)
        a["clips_per_source"] = 3            # the three a source they were written for (the live one cuts 1 since 10-31)
        a["account_dir"] = str(self.tmp / "Gaming")
        a["skill_dir"] = str(self.tmp / "skill")
        a["delivered"] = [str(self.tmp / "delivered" / "{YYYY}{MM}{DD}")]
        self.raw = raw
        self.reload()
        (self.tmp / "skill" / "work").mkdir(parents=True)
        (self.tmp / "skill" / "scripts").mkdir(parents=True)
        (self.acct.account_dir / "Publish").mkdir(parents=True)
        M._HELD.discard("gaming")

    def tearDown(self):
        C.STATE = self._state
        M._HELD.discard("gaming")

    def reload(self, **gta_maker):
        if gta_maker:
            self.raw["accounts"]["gaming"].setdefault("gta_maker", {}).update(gta_maker)
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(self.raw))
        self.cfg = C.load(p)
        self.acct = self.cfg.account("gaming")

    def inbox(self, rows) -> Path:
        p = self.acct.account_dir / "Publish" / "inbox.json"
        items = {M.video_id(r["url"]) or r["url"]: r for r in rows}
        p.write_text(json.dumps({"items": items}))
        return p

    def board(self) -> dict:
        return B.read(self.acct, DAY)

    def make(self, picks=None, moments=None, intake_rc=0, intake_timeout=False, pack_ok=True, collect_ready=True,
             only=None):
        """make_gta with run.py faked. picks: candidate n -> (exit, output); moments: the candidates intake writes."""
        calls = []
        picks = picks or {}

        def fake_run(cmd, log, cwd=None, env=None, timeout=0):
            calls.append(list(cmd))
            out = ""
            rc = 0
            if cmd[1] == "run.py" and "--job" in cmd:                     # intake
                if intake_timeout:
                    raise subprocess.TimeoutExpired(cmd, 4 * 3600)
                job = cmd[cmd.index("--job") + 1]
                if moments is not None:
                    d = Path(self.acct.skill_dir) / "work" / job
                    d.mkdir(parents=True, exist_ok=True)
                    (d / "moments.json").write_text(json.dumps({"seconds": moments[0], "candidates": moments[1]}))
                rc = intake_rc
            elif cmd[1] == "run.py" and "--pick" in cmd:
                rc, out = picks.get(int(cmd[cmd.index("--pick") + 1]), (0, ""))
            with open(log, "a") as f:
                f.write(f"\n$ {' '.join(cmd)}\n{out}\n")
            return subprocess.CompletedProcess(cmd, rc)

        def fake_pack(acct, day, slot, log, env):
            return f"  {slot}: packed" if pack_ok else f"  {slot}: packing FAILED (boom)"

        def fake_collect(cfg, acct, day, extra=None, only=None):
            if collect_ready and only:
                with B.edit(cfg, acct, day) as bd:
                    for k in only:
                        if bd["slots"][k]["status"] in B.FILLABLE:
                            bd["slots"][k]["status"] = B.READY
                return [f"  {only[0]} x: ready, 40.0s"]
            return []

        with B.edit(self.cfg, self.acct, DAY):      # the board, as make() makes sure of first
            pass
        with mock.patch.object(M, "run_logged", fake_run), mock.patch.object(M, "pack_gta_slot", fake_pack), \
                mock.patch.object(M.K, "collect", fake_collect):
            lines = M.make_gta(self.cfg, self.acct, DAY, only=only)
        return calls, lines

    @staticmethod
    def picks_of(calls) -> list:
        return [int(c[c.index("--pick") + 1]) for c in calls if c[1] == "run.py" and "--pick" in c]


# ------------------------------------------------------------------------------------------- GTA-3 the picks
class Picks(Base):
    def test_music_and_furniture_candidates_are_skipped(self):
        cands = [cand(1, 0, 40, music=0.6, refuses=True), cand(2, 100, 140, furniture=["housekeeping x2"]),
                 cand(3, 10, 50, furniture=["intro"]), cand(4, 300, 340), cand(5, 500, 540), cand(6, 700, 740)]
        self.inbox([row(1)])
        calls, lines = self.make(moments=(4000, cands), only=["07:00", "08:45", "10:30"])
        self.assertEqual(self.picks_of(calls), [4, 5, 6])
        self.assertTrue(any("candidate 1 skipped (music 60%)" in ln for ln in lines), lines)
        self.assertEqual({s["status"] for k, s in self.board()["slots"].items() if k in ("07:00", "08:45", "10:30")},
                         {B.READY})

    def test_a_span_with_no_whole_sentence_for_its_hook_is_skipped_by_name(self):
        # 10-19: moments.py drafts a hook from a whole sentence or not at all, and marks the span for preflight's HOOK
        nohook = dict(cand(1, 0, 40), events=["no whole sentence for a hook - preflight refuses"])
        cands = [nohook, cand(2, 300, 340), cand(3, 500, 540), cand(4, 700, 740)]
        self.inbox([row(1)])
        calls, lines = self.make(moments=(4000, cands), only=["07:00"])
        self.assertEqual(self.picks_of(calls), [2])
        self.assertTrue(any("candidate 1 skipped (no whole sentence for a hook)" in ln for ln in lines), lines)

    def test_intro_and_outro_do_not_skip_on_a_short_source(self):
        cands = [cand(1, 150, 190, furniture=["outro"]), cand(2, 70, 110), cand(3, 10, 50, furniture=["intro"])]
        self.inbox([row(1, duration=225)])
        calls, _ = self.make(moments=(225, cands), only=["07:00", "08:45"])
        self.assertEqual(self.picks_of(calls), [1, 2])      # the verdict after the trailer is not 'furniture' here

    def test_a_preflight_refusal_tries_the_next_candidate_up_to_two_more(self):
        refuse = (1, "    [FAIL] MUSIC    62% music\nSTOPPED at 'preflight' (exit 1); the whole output is in RUN.md")
        cands = [cand(i, i * 100, i * 100 + 40) for i in range(1, 7)]
        self.inbox([row(1)])
        calls, lines = self.make(moments=(4000, cands), picks={1: refuse, 2: refuse, 3: refuse}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1, 2, 3])   # two more, then the slot fails
        s = self.board()["slots"]["07:00"]
        self.assertEqual(s["status"], B.FAILED)
        self.assertEqual(s["picks_tried"], [1, 2, 3])

    def test_the_next_slot_takes_the_next_unused_candidate(self):
        refuse = (1, "    [FAIL] HOOK     the hook is a sentence\nSTOPPED at 'preflight' (exit 1)")
        cands = [cand(i, i * 100, i * 100 + 40) for i in range(1, 7)]
        self.inbox([row(1)])
        calls, _ = self.make(moments=(4000, cands), picks={1: refuse}, only=["07:00", "08:45", "10:30"])
        self.assertEqual(self.picks_of(calls), [1, 2, 3, 4])
        self.assertEqual([self.board()["slots"][k]["pick"] for k in ("07:00", "08:45", "10:30")], [2, 3, 4])

    def test_tool_and_non_preflight_stops_are_not_retried(self):
        # LENGTH is retried since 10-18 (audit gta-yield, tests/test_gta_yield.py): run.py tried the span at 1.0x
        cands = [cand(i, i * 100, i * 100 + 40) for i in range(1, 7)]
        for out in ("    [FAIL] TOOL     the tool has UNCOMMITTED changes\nSTOPPED at 'preflight' (exit 1)",
                    "\nNOT READY: the tool itself. Nothing below was checked.\n\nSTOPPED at 'preflight' (exit 1)",
                    "STOPPED at 'the render (build_all.py)' (exit 1)"):
            self.fresh()
            self.inbox([row(1)])
            calls, _ = self.make(moments=(4000, cands), picks={1: (1, out)}, only=["07:00"])
            self.assertEqual(self.picks_of(calls), [1], out)
            self.assertEqual(self.board()["slots"]["07:00"]["status"], B.FAILED)

    def test_no_moments_file_picks_in_slot_order_as_before(self):
        self.inbox([row(1)])
        calls, _ = self.make(moments=None, only=["07:00", "08:45", "10:30"])
        self.assertEqual(self.picks_of(calls), [1, 2, 3])

    def test_preflight_refusal_reader(self):
        self.assertEqual(M.preflight_refusal("[FAIL] MUSIC x\n[FAIL] PIC y\nSTOPPED at 'preflight' (exit 1)"),
                         ["MUSIC", "PIC"])
        self.assertIsNone(M.preflight_refusal("[FAIL] MUSIC x\nSTOPPED at 'the slate' (exit 1)"))


# --------------------------------------------------------------------------------------------- GTA-4 the film
class Film(Base):
    def film(self) -> Path:
        d = Path(self.acct.skill_dir) / "work" / "launch-trailer"
        d.mkdir(parents=True)
        (d / "cues.json").write_text("[]")
        (d / "master.mp4").write_bytes(b"\0")
        return d / "cues.json"

    def intake(self, calls) -> list:
        return next(c for c in calls if c[1] == "run.py" and "--job" in c)

    def test_a_reaction_to_the_launch_trailer_is_cut_with_the_film(self):
        cues = self.film()
        self.inbox([row(1, kind="reaction", title="iShowSpeed Reacts To GTA 6 - Official Launch Trailer")])
        calls, lines = self.make(only=["07:00"])
        c = self.intake(calls)
        self.assertEqual(c[c.index("--film") + 1], str(cues))
        self.assertTrue(any("the film: launch-trailer" in ln for ln in lines), lines)

    def test_reaction_in_the_title_counts_and_a_breakdown_does_not(self):
        self.film()
        self.inbox([row(1, kind="scout", title="GTA 6 LAUNCH TRAILER REACTION AND BREAKDOWN!")])
        calls, _ = self.make(only=["07:00"])
        self.assertIn("--film", self.intake(calls))
        self.fresh()
        self.film()
        self.inbox([row(1, kind="breakdown", title="GTA 6 Launch Trailer: Everything New YOU MISSED")])
        calls, _ = self.make(only=["07:00"])
        self.assertNotIn("--film", self.intake(calls))

    def test_no_film_on_disk_cuts_without_it_and_says_so(self):
        self.inbox([row(1, kind="reaction", title="Kai Cenat Reacts To GTA 6 - Official Launch Trailer")])
        calls, lines = self.make(only=["07:00"])
        self.assertNotIn("--film", self.intake(calls))
        self.assertTrue(any("the film is not on disk" in ln for ln in lines), lines)

    def test_films_can_be_switched_off(self):
        self.film()
        self.reload(films=[])
        self.inbox([row(1, kind="reaction", title="xQc Reacts to Grand Theft Auto VI Launch Trailer")])
        calls, _ = self.make(only=["07:00"])
        self.assertNotIn("--film", self.intake(calls))


# --------------------------------------------------------------------------------------- GTA-5 the face's name
class Person(Base):
    def test_scout_config_names_the_face_behind_each_channel(self):
        rows = {r["name"]: r.get("person") for r in SC.load("gaming")["youtube_channels"]}
        self.assertEqual(rows["Live Speedy"], "iShowSpeed")
        self.assertEqual(rows["Kai Cenat Live"], "Kai Cenat")
        self.assertEqual(rows["ImStillxQc"], "xQc")
        self.assertEqual(rows["Joe Bart VODs"], "Joe Bartolozzi")

    def test_a_channel_row_carries_the_person_into_the_find(self):
        src = {"label": "Live Speedy", "name": "Live Speedy", "slug": "livespeedy", "person": "iShowSpeed",
               "kind": "reaction"}
        f = SC._vid_row({"id": "mlguGHEeVO0", "title": "iShowSpeed Reacts", "duration": 225}, src, NOW)
        self.assertEqual(f["person"], "iShowSpeed")

    def test_feed_writes_the_person_as_the_inbox_name(self):
        self.inbox([])
        speed = {"id": "mlguGHEeVO0", "kind": "video", "title": "iShowSpeed Reacts To GTA 6 - Official Launch Trailer",
                 "channel": "Live Speedy", "channel_slug": "livespeedy", "url": "u-speed", "duration": 225,
                 "score": 16.0, "published": None, "roster_kind": "reaction", "min_minutes": 3}
        kai = dict(speed, id="B6biwxo0dMQ", channel="Kai Cenat Live", channel_slug="kaicenatlive", url="u-kai",
                   person="Kai Cenat")
        other = dict(speed, id="OTHER000001", channel="TGG", channel_slug="tgg", url="u-tgg", roster_kind="breakdown",
                     duration=900)
        SC.feed_gta(self.acct, [speed, kai, other], 3)
        items = json.loads((self.acct.account_dir / "Publish" / "inbox.json").read_text())["items"]
        self.assertEqual(items["mlguGHEeVO0"]["name"], "iShowSpeed")       # from scout.json (a board find has none)
        self.assertEqual(items["B6biwxo0dMQ"]["name"], "Kai Cenat")        # from the find itself
        self.assertEqual(items["OTHER000001"]["name"], "TGG")              # no person: the channel, as before
        self.assertEqual(items["mlguGHEeVO0"]["channel"], "livespeedy")    # the slug is unchanged


# ------------------------------------------------------------------------------- GTA-9 never stuck 'making'
class NeverStuck(Base):
    def test_pack_failure_fails_the_slot(self):
        self.inbox([row(1)])
        self.make(pack_ok=False, only=["07:00"])
        s = self.board()["slots"]["07:00"]
        self.assertEqual(s["status"], B.FAILED)
        self.assertIn("packing FAILED", B.last_note(s))

    def test_packed_but_not_collected_fails_the_slot(self):
        self.inbox([row(1)])
        self.make(collect_ready=False, only=["07:00"])
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.FAILED)

    def test_an_intake_timeout_fails_its_slots_and_the_night_goes_on(self):
        self.inbox([row(1), row(2)])
        calls = []
        real = [0]

        def fake_run(cmd, log, cwd=None, env=None, timeout=0):
            calls.append(cmd)
            if cmd[1] == "run.py" and "--job" in cmd:
                real[0] += 1
                if real[0] == 1:
                    raise subprocess.TimeoutExpired(cmd, 4 * 3600)
            return subprocess.CompletedProcess(cmd, 0)
        with B.edit(self.cfg, self.acct, DAY):
            pass
        with mock.patch.object(M, "run_logged", fake_run), \
                mock.patch.object(M, "pack_gta_slot", lambda a, d, s, l, e: f"  {s}: packed"), \
                mock.patch.object(M.K, "collect", lambda *a, **k: []):
            lines = M.make_gta(self.cfg, self.acct, DAY, only=["07:00", "08:45", "10:30", "12:15"])
        bd = self.board()["slots"]
        self.assertEqual([bd[k]["status"] for k in ("07:00", "08:45", "10:30")], [B.FAILED] * 3)
        self.assertIn("ran past 4 hours", B.last_note(bd["07:00"]))
        self.assertTrue(any("timed out" in ln for ln in lines))
        self.assertEqual(sum(1 for c in calls if c[1] == "run.py" and "--job" in c), 2)   # the second source still ran

    def test_a_slot_made_again_from_another_source_forgets_the_old_picks(self):
        self.inbox([row(1)])
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"].update(status=B.OPEN, job="factory-old-job", picks_tried=[1, 2, 3], pick=3,
                                        pick_span=[1, 2], question_written={"question": "old?"})
        cands = [cand(i, i * 100, i * 100 + 40) for i in range(1, 4)]
        calls, _ = self.make(moments=(4000, cands), only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1])
        s = self.board()["slots"]["07:00"]
        self.assertEqual((s["picks_tried"], s["pick"]), ([1], 1))
        self.assertNotIn("question_written", s)

    def test_a_making_slot_with_no_worker_is_failed_and_made_again(self):
        self.inbox([row(1), row(2)])
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"].update(status=B.MAKING, source_url="https://www.youtube.com/watch?v=vid00000001")
        calls, lines = self.make(only=["07:00"])
        self.assertTrue(any("left 'making'" in ln for ln in lines), lines)
        intake = next(c for c in calls if c[1] == "run.py" and "--job" in c)
        self.assertEqual(intake[2], "https://www.youtube.com/watch?v=vid00000002")   # the next source
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.READY)

    def test_a_making_slot_is_left_alone_while_another_worker_runs(self):
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"]["status"] = B.MAKING
        lock = C.STATE / "gaming" / ".worker.lock"
        with open(lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)        # another worker holds the account
            self.assertTrue(M.other_worker_running(self.acct))
            self.assertEqual(M.reset_stale_making(self.cfg, self.acct, DAY), [])
            self.assertEqual(self.board()["slots"]["07:00"]["status"], B.MAKING)
            M._HELD.add("gaming")                                 # ... unless that worker is this process
            self.assertFalse(M.other_worker_running(self.acct))
            self.assertTrue(M.reset_stale_making(self.cfg, self.acct, DAY))
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.FAILED)

    def test_an_error_on_one_source_fails_its_slots_and_the_night_goes_on(self):
        self.inbox([row(1), row(2)])
        real = M.cut_gta_slots
        seen = []

        def boom(cfg, acct, day, job, template, slots, log, env):
            seen.append(job)
            if len(seen) == 1:
                raise KeyError("candidates")
            return real(cfg, acct, day, job, template, slots, log, env)
        with mock.patch.object(M, "cut_gta_slots", boom):
            calls, lines = self.make(only=["07:00", "08:45", "10:30", "12:15"])
        bd = self.board()["slots"]
        self.assertEqual([bd[k]["status"] for k in ("07:00", "08:45", "10:30")], [B.FAILED] * 3)
        self.assertIn("KeyError", B.last_note(bd["07:00"]))
        self.assertEqual(bd["12:15"]["status"], B.READY)
        self.assertEqual(len(seen), 2)

    def test_run_logged_stops_the_whole_step_on_a_timeout(self):
        log = self.tmp / "t.log"
        mark = self.tmp / "grandchild.pid"
        t0 = time.time()
        with self.assertRaises(subprocess.TimeoutExpired):
            M.run_logged(["/bin/sh", "-c", f"sleep 30 & echo $! > {mark}; wait"], log, timeout=1)
        self.assertLess(time.time() - t0, 15)
        pid = int(mark.read_text().strip())
        gone = False
        for _ in range(30):                                          # the grandchild (whisper, ffmpeg) is gone too
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                gone = True
                break
            time.sleep(0.1)
        self.assertTrue(gone)
        self.assertIn("stopped: ran past 1 s", log.read_text())


# -------------------------------------------------------------------------------------- GTA-11 the question
class Question(unittest.TestCase):
    def test_question_from_hook(self):
        f = M.question_from_hook
        self.assertEqual(f("Is this map even Leonida"), "Is this map even Leonida?")
        self.assertEqual(f("Lucia is calling from prison."), "Is Lucia calling from prison?")
        self.assertEqual(f("GTA 6 has different dog breeds"), "Does GTA 6 have different dog breeds?")
        self.assertEqual(f("Rockstar has delayed GTA 6"), "Has Rockstar delayed GTA 6?")
        self.assertEqual(f("GTA 6 cars are driving Miami"), "Are GTA 6 cars driving Miami?")
        self.assertEqual(f("This is insane"), "Is this insane?")
        self.assertEqual(f("The City took over Miami Beach"), "")
        self.assertEqual(f(""), "")

    def st(self, **kw) -> dict:
        return dict({"question_default": True, "shape": "event", "who": "TGG", "hook": "", "hook_s": "",
                     "template": "underlay", "question": "Would you have made it out?"}, **kw)

    def test_the_event_default_is_rewritten(self):
        self.assertEqual(M.question_for(self.st(hook_s="Lucia is calling from prison")), ("", ""))  # off by default
        q = lambda st: M.question_for(st, dict(M.GTA_MAKER_DEFAULTS, question_rewrite=True))
        self.assertEqual(q(self.st(hook_s="Lucia is calling from prison")), ("Is Lucia calling from prison?", "from the hook"))
        self.assertEqual(q(self.st(hook_s="The City took over Miami Beach"))[0], "Did you see that coming?")
        self.assertEqual(q(self.st(hook_s="He stuck the ramp landing", template="gameplay-clean"))[0],
                         "Could you pull this off?")
        self.assertEqual(q(self.st(hook="GTA 6 HAS DIFFERENT DOG BREEDS"))[0], "Does GTA 6 have different dog breeds?")

    def test_fitting_defaults_and_typed_questions_are_kept(self):
        q = M.question_for
        self.assertEqual(q(self.st(question_default=False, hook_s="Lucia is calling")), ("", ""))
        self.assertEqual(q(self.st(shape="quote", hook_s="If this is true that's awesome dude",
                                   question="Is TGG right?")), ("", ""))
        self.assertEqual(q(self.st(shape="list", hook_s="5 things you missed", question="Which one is your number one?")),
                         ("", ""))
        self.assertEqual(q(self.st(hook_s="The cops chased him for ten minutes")), ("", ""))    # a chase: it fits
        self.assertEqual(q({}), ("", ""))

    def test_the_card_line(self):
        s = {"status": B.READY, "slug": "lucia", "question_written": {"question": "Is Lucia calling from prison?",
                                                                       "why": "from the hook", "slug": "lucia"}}
        self.assertIn("Is Lucia calling from prison?", M.question_card_line(s))
        self.assertEqual(M.question_card_line(dict(s, status=B.SCHEDULED)), "")
        self.assertEqual(M.question_card_line(dict(s, slug="another-clip")), "")      # collected again since
        self.assertEqual(M.question_card_line({"status": B.READY}), "")


class PackQuestion(Base):
    """PACK itself, against a stand-in day_folder.py: the default question is rewritten in the slate and the files."""
    FAKE_DF = r'''
import json
from pathlib import Path
HERE = Path(__file__).resolve().parent
JOBS = HERE.parent / "Jobs"
WORK = Path(json.loads((HERE / "where.json").read_text())["work"])
def slate_paths(job):
    return [WORK / job / "slate.json"]
def clip_entry(job, slug):
    sl = json.loads((WORK / job / "slate.json").read_text())
    return next(c for c in sl["clips"] if c["slug"] == slug), {"source_title": "TGG launch trailer"}
class gc:
    @staticmethod
    def _parts(c, cfg, d):
        q = c.get("question") or "Would you have made it out?"
        return {"question": q, "shape": "event", "hook_s": "Lucia is calling from prison"}
    @staticmethod
    def packs(c, cfg, d):
        q = c.get("question") or "Would you have made it out?"
        k = {"title": None, "paste": "Lucia is calling from prison.\n" + q, "pinned": q, "who": "TGG",
             "question_default": q == "Would you have made it out?"}
        return {p: dict(k) for p in ("youtube", "tiktok", "reels", "facebook")}
def paste_files(dst, clip, cfg, packs):
    for p, k in packs.items():
        (dst / (p + ".txt")).write_text(k["paste"] + "\n")
'''

    def test_pack_rewrites_the_default_question_in_the_slate_and_the_paste_files(self):
        self.acct.raw.setdefault("gta_maker", {})["question_rewrite"] = True     # off by default since 10-17 evening
        pub = self.acct.account_dir / "Publish"
        work = Path(self.acct.skill_dir) / "work"
        job = "factory-20300107-pip-tgg-x"
        (work / job).mkdir(parents=True)
        (work / job / "slate.json").write_text(json.dumps({"clips": [{"slug": "lucia", "hook": "LUCIA IS CALLING",
                                                                      "template": "pip",
                                                                      "question": "Would you have made it out?"}]}))
        clips = self.acct.account_dir / "Jobs" / job / "clips"
        clips.mkdir(parents=True)
        (clips / "01-lucia.mp4").write_bytes(b"\0")
        (pub / "day_folder.py").write_text(self.FAKE_DF)
        (pub / "where.json").write_text(json.dumps({"work": str(work)}))
        (pub / "schedule.json").write_text(json.dumps({DAY.isoformat(): {"07:00": {"job": job, "slug": "lucia"}}}))
        log = self.tmp / "pack.log"
        line = M.pack_gta_slot(self.acct, DAY, "07:00", log, dict(os.environ))
        self.assertIn("comment question written (from the hook): Is Lucia calling from prison?", line, log.read_text())
        folder = M.gta_pack_root(self.acct, DAY) / "0700 lucia"
        self.assertIn("Is Lucia calling from prison?", (folder / "tiktok.txt").read_text())
        slate = json.loads((work / job / "slate.json").read_text())
        self.assertEqual(slate["clips"][0]["question"], "Is Lucia calling from prison?")
        st = M.pack_state(self.acct, DAY, "07:00")
        self.assertFalse(st["question_default"])
        self.assertEqual(st["written"]["was"], "Would you have made it out?")
        # packed again: the slate's question is no longer the default, so nothing is rewritten
        self.assertEqual(M.pack_gta_slot(self.acct, DAY, "07:00", log, dict(os.environ)), "  07:00: packed")

    def test_a_failed_pack_leaves_no_folder(self):
        root = M.gta_pack_root(self.acct, DAY)
        (root / "0700 half").mkdir(parents=True)
        M._drop_pack(self.acct, DAY, "07:00")
        self.assertFalse((root / "0700 half").exists())


# -------------------------------------------------------------------------------------- GTA-13 dedupe holes
class Dedupe(Base):
    def test_a_job_that_delivered_counts_as_used_by_url_and_id(self):
        work = Path(self.acct.skill_dir) / "work"
        out = self.tmp / "Jobs"
        for name, url, clips, reh in (("factory-x", "https://www.youtube.com/watch?v=11_srAYn_rA", {"a": 1}, False),
                                      ("analyzed-only", "https://www.youtube.com/watch?v=NOTCUT00001", {}, False),
                                      ("_audit-proof", "https://www.youtube.com/watch?v=AUDIT000001", {"a": 1}, False),
                                      ("rehearsal", "https://www.youtube.com/watch?v=REHEARSAL01", {"a": 1}, True)):
            (work / name).mkdir(parents=True)
            (out / name).mkdir(parents=True)
            (out / name / "manifest.json").write_text(json.dumps({"clips": clips}))
            (work / name / "project.json").write_text(json.dumps({"source_url": url, "out_dir": str(out / name),
                                                                 "rehearsal": reh}))
        used = M.used_sources(self.acct)
        self.assertIn("11_srAYn_rA", used)
        self.assertFalse({"NOTCUT00001", "AUDIT000001", "REHEARSAL01"} & used)
        r = row(1, url="https://youtu.be/11_srAYn_rA")
        self.inbox([r, row(2)])
        self.assertEqual([i["url"] for i in M.gta_sources(self.acct, used)], [row(2)["url"]])

    def test_old_rows_age_out(self):
        self.inbox([row(1, age_d=40), row(2, age_d=2)])
        self.assertEqual([i["url"] for i in M.gta_sources(self.acct, set())], [row(2)["url"]])
        self.reload(max_age_days=0)
        self.assertEqual(len(M.gta_sources(self.acct, set())), 2)

    def test_a_channel_taken_off_is_never_cut_by_the_night(self):
        # 10-26: Onespot's videos are music under the talk; its rows already in the inbox must not be cut either
        one, two = dict(row(1), channel="onespot"), dict(row(2), channel="tgg")
        self.inbox([one, two])
        self.reload(skip_channels=["onespot"])
        self.assertEqual([i["url"] for i in M.gta_sources(self.acct, set())], [two["url"]])
        self.reload(skip_channels=[])
        self.assertEqual(len(M.gta_sources(self.acct, set())), 2)

    def test_a_leak_video_is_never_cut_by_the_night(self):
        # 10.19: the watchlist scan, not the scout, put MrBossFTW's leaks video in the inbox; two clips were cut from it
        boss = row(1, title="Cyberleek Has CRASHED OUT...The End Of The GTA 6 Gameplay Leaks!")
        leaked = row(2, title="GTA 6 LEAKER JUST DROPPED 26 MINUTES OF LEAKED GAMEPLAY")
        plain = row(3, title="This NEW GTA 6 Map Shows Us How Much We've Seen Of Leonida")
        self.inbox([boss, leaked, plain])
        self.assertEqual([i["url"] for i in M.gta_sources(self.acct, set())], [plain["url"]])
        self.assertEqual(M.title_skips(self.acct), 2)
        calls, lines = self.make(only=["07:00"])
        self.assertTrue(any("2 upload(s) not cut: their title is about leaks" in ln for ln in lines), lines)
        cut = [c[c.index("run.py") + 1] for c in calls if "run.py" in c and "--job" in c]
        self.assertEqual(cut, [plain["url"]])        # the intake ran on the map video only
        self.reload(skip_titles="")                  # the switch: "" cuts them again (the map video is cut by now)
        self.assertEqual(sorted(i["url"] for i in M.gta_sources(self.acct, set())),
                         sorted([boss["url"], leaked["url"]]))

    def test_the_inbox_row_is_marked_cut_after_intake(self):
        p = self.inbox([row(1), row(2)])
        self.make(only=["07:00"])
        items = json.loads(p.read_text())["items"]
        self.assertEqual(items["vid00000001"]["status"], "cut")
        self.assertEqual(items["vid00000001"]["cut_for"], DAY.isoformat())
        self.assertTrue(items["vid00000001"]["cut_job"].startswith("factory-20300107-"))
        self.assertEqual(items["vid00000002"]["status"], "new")

    def test_video_ids(self):
        v = M.video_id
        self.assertEqual(v("https://www.youtube.com/watch?v=mlguGHEeVO0&t=3s"), "mlguGHEeVO0")
        self.assertEqual(v("https://youtu.be/mlguGHEeVO0"), "mlguGHEeVO0")
        self.assertEqual(v("https://www.youtube.com/shorts/abcdEFGhijk"), "abcdEFGhijk")
        self.assertEqual(v("https://www.twitch.tv/videos/2858049515"), "2858049515")
        self.assertIsNone(v("drop folder"))

    def test_same_titles_make_two_jobs(self):
        a = {"channel": "kaicenat", "title": "GTA 6 LIVE REACTION *First Look*", "url": "https://www.twitch.tv/videos/1"}
        b = dict(a, url="https://www.twitch.tv/videos/2")
        self.assertNotEqual(M.job_name(a, DAY, "pip"), M.job_name(b, DAY, "pip"))
        self.assertTrue(M.job_name(a, DAY, "pip").startswith("factory-20300107-pip-kaicenat-gta-6-live-reaction"))


# --------------------------------------------------------------------------------- GTA-12 spread (owner call)
class Spread(Base):
    def todo(self, n: int) -> list:
        return [{"slot": self.acct.slots[i], "format": ["underlay", "pip"][i % 2], "status": B.OPEN} for i in range(n)]

    def test_off_by_default_slots_stay_in_order(self):
        plan, _ = M.plan_gta(self.acct, self.todo(6), [row(1), row(2)])
        self.assertEqual([[s["slot"] for s in ss] for _, ss, _ in plan],
                         [["07:00", "08:45", "10:30"], ["12:15", "14:00", "15:45"]])

    def test_spread_deals_round_robin_and_caps_a_short_source(self):
        self.reload(spread=True)
        srcs = [row(1, duration=225), row(2, duration=525), row(3, duration=747), row(4, duration=4497)]
        plan, left = M.plan_gta(self.acct, self.todo(10), srcs)
        by = {item["url"]: [s["slot"] for s in ss] for item, ss, _ in plan}
        self.assertEqual(by[srcs[0]["url"]], ["07:00", "14:00"])            # 225 s: two clips, far apart
        self.assertEqual(by[srcs[1]["url"]], ["08:45", "15:45", "21:00"])
        self.assertEqual(by[srcs[2]["url"]], ["10:30", "17:30", "23:00"])
        self.assertEqual(by[srcs[3]["url"]], ["12:15", "19:15"])
        self.assertEqual(left, [])
        for _, ss, _ in plan:                                               # never two slots in a row from one video
            idx = [self.acct.slots.index(s["slot"]) for s in ss]
            self.assertTrue(all(b - a > 1 for a, b in zip(idx, idx[1:])), idx)

    def test_spread_skips_a_near_repeat_pick(self):
        self.reload(spread=True)
        cands = [cand(1, 100, 140), cand(2, 110, 150), cand(3, 200, 240)]
        self.inbox([row(1)])
        calls, _ = self.make(moments=(4000, cands), only=["07:00", "14:00"])
        self.assertEqual(self.picks_of(calls), [1, 3])                       # 2 shares 75% of 1's seconds
        self.fresh()
        self.inbox([row(1)])
        calls, _ = self.make(moments=(4000, cands), only=["07:00", "08:45"])
        self.assertEqual(self.picks_of(calls), [1, 2])                       # off: as before


# --------------------------------------------------------------------------- GTA-7 / GTA-8 REACTIONS (owner)
class Reactions(Base):
    def duo_day(self):
        src = Path(self.acct.skill_dir) / "work" / "launch-reactions" / "src"
        src.mkdir(parents=True)
        (src / "speed.info.json").write_text(json.dumps({"id": "mlguGHEeVO0",
                                                         "webpage_url": "https://www.youtube.com/watch?v=mlguGHEeVO0"}))
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["12:15"]["format"] = "reactions"
        self.inbox([dict(row(1), url="https://www.youtube.com/watch?v=mlguGHEeVO0"), row(2)])

    def test_a_reactions_slot_is_left_open_with_the_reason(self):
        self.duo_day()
        calls, lines = self.make(only=["12:15"])
        s = self.board()["slots"]["12:15"]
        self.assertEqual(s["status"], B.OPEN)
        self.assertIn("Held for the day's REACTIONS clip", B.last_note(s))
        self.assertFalse([c for c in calls if c[1] == "run.py"])
        self.assertTrue(any(ln.startswith("12:15 (reactions): left open") for ln in lines), lines)

    def test_duo_switch_off_cuts_the_reactors_as_before(self):
        self.duo_day()
        calls, _ = self.make(only=["07:00"])
        intake = next(c for c in calls if c[1] == "run.py" and "--job" in c)
        self.assertEqual(intake[2], "https://www.youtube.com/watch?v=mlguGHEeVO0")

    def test_duo_switch_on_holds_the_reactors_back_that_day(self):
        self.reload(duo_skips_singles=True)
        self.duo_day()
        calls, lines = self.make(only=["07:00"])
        intake = next(c for c in calls if c[1] == "run.py" and "--job" in c)
        self.assertEqual(intake[2], row(2)["url"])
        self.assertTrue(any("held for the day's REACTIONS duo" in ln for ln in lines), lines)


# ----------------------------------------------------------------------------------- GTA-10 the 03:45 retry
@unittest.skipUnless(any(x["name"] == "gta-retry" for x in TM.AGENTS),
                     "the 03:45 retry agent is not in timer.py (build2/sweep-gta/patches/sg3_retry.py)")
class Retry(unittest.TestCase):
    def test_the_retry_agent(self):
        a = next(x for x in TM.AGENTS if x["name"] == "gta-retry")
        p = TM.plist(a)
        self.assertEqual(p["ProgramArguments"][2:], ["night-retry", "gaming", "--date", "today"])
        self.assertEqual(p["StartCalendarInterval"], {"Hour": 3, "Minute": 45})
        self.assertEqual(p["ProcessType"], "Standard")
        self.assertTrue(p["AbandonProcessGroup"])
        self.assertEqual(a["twins"], [])


if __name__ == "__main__":
    unittest.main()
