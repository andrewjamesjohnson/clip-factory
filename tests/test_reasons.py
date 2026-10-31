"""Reason codes on Pull and Decline, the check each code names, and the kill list the night respects
(clipfactory/reasons.py, built 2026-10-19 after the automatic sign-off made the owner's Pulls and Declines the main
signal). Throwaway folders; no network; no skill is run."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-reasons-state-"))

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import bench as BN     # noqa: E402
from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import direct as D     # noqa: E402
from clipfactory import makers as M     # noqa: E402
from clipfactory import outlets as O    # noqa: E402
from clipfactory import reasons as KL   # noqa: E402
from clipfactory import review as RV    # noqa: E402
from clipfactory import scout as SC     # noqa: E402
from clipfactory import signoff as S    # noqa: E402
from clipfactory import statspage as SP     # noqa: E402

DAY = date(2031, 7, 8)          # a Tuesday
VID = "AbCdEfGhIjK"
URL = f"https://www.youtube.com/watch?v={VID}"
PIC = "https://commons.wikimedia.org/wiki/File:Some_portrait.jpg"


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-reasons-"))
        cls.old_state, C.STATE = C.STATE, cls.tmp / "state"
        cls.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        cls.old_roots = S.CAST_ROOTS
        S.CAST_ROOTS = (cls.tmp / "Clipping" / "Jobs",)
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(cls.tmp / "drafts")
        raw["review_port"] = 0
        raw["auto_sign"] = {"on": True, "lead_minutes": 30, "late_hours": 3, "cast_unchecked": "one"}
        for slug, a in raw["accounts"].items():
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 2, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "tiktok": {"mode": "export"}}
            a["account_dir"] = str(cls.tmp / ("Clipping" if slug == "politics" else a["name"]))
            a["skill_dir"] = str(cls.tmp / f"skill-{slug}")
            a.pop("gta_maker", None)
        cls.raw = raw
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.main = cls.tmp / "main.mp4"
        clip(cls.main, 2.5)
        cls.other = cls.tmp / "other.mp4"
        clip(cls.other, 2.6)

    @classmethod
    def tearDownClass(cls):
        C.STATE = cls.old_state
        O.TOKENS, D.TOKENS = cls.old_tokens
        S.CAST_ROOTS = cls.old_roots

    def setUp(self):
        for d in ("state", "drafts", "delivered", "keys", "Clipping", "Gaming", "skill-gaming"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.politics = self.cfg.account("politics")
        self.gta = self.cfg.account("gaming")

    # ----------------------------------------------------------------------------------------- helpers
    def job(self, slug: str, segments, broll=None, url: str = URL, job: str = "", hhmm: str = "07:00",
            day: date = DAY) -> str:
        """A Politics job whose slate holds `slug`, planned in Publish/schedule.json."""
        job = job or f"job-{slug}"
        jd = self.politics.account_dir / "Jobs" / job
        jd.mkdir(parents=True, exist_ok=True)
        ent = {"slug": slug, "template": "room", "segments": segments}
        if broll is not None:
            ent["broll"] = broll
        sl = jd / "slate.json"
        clips = json.loads(sl.read_text())["clips"] if sl.exists() else []
        sl.write_text(json.dumps({"clips": clips + [ent]}))
        (jd / "project.json").write_text(json.dumps({"source_url": url, "rights": "owned"}))
        sp = self.politics.account_dir / "Publish" / "schedule.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        sched = json.loads(sp.read_text()) if sp.exists() else {}
        sched.setdefault(day.isoformat(), {})[hhmm] = {"job": job, "slug": slug}
        sp.write_text(json.dumps(sched))
        return job

    def deliver(self, acct, hhmm: str, slug: str, video: Path = None, day: date = DAY) -> list:
        d = acct.delivered_dirs(day)[0] / f"{hhmm.replace(':', '')} {slug}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(video or self.main, d / f"01-{slug}.mp4")
        names = ("youtube", "tiktok_reach", "facebook", "instagram") if acct.maker == "politics" else \
            ("youtube", "tiktok", "facebook", "reels")
        for p in names:
            (d / f"{p}.txt").write_text(f"TITLE\nThe exchange at {hhmm}\n\nCAPTION\nWords for {p}.\n")
        return K.collect(self.cfg, acct, day)

    def slot(self, acct, k: str = "07:00", day: date = DAY) -> dict:
        return B.read(acct, day)["slots"][k]

    def at(self, hhmm: str, day: date = DAY):
        return self.cfg.slot_time(day, hhmm)

    def signed(self, acct, k: str, by: str = "Andrew"):
        s = self.slot(acct, k)
        return S.approve(self.cfg, acct, DAY, [k], by, now=self.at("05:00"), seen={k: B.seen_key(s)})


# --------------------------------------------------------------------------------------------- the codes
class Codes(Base):
    def test_ten_codes_and_other_six_hard_each_with_a_gate(self):
        labels = [c.label for c in KL.CODES]
        self.assertEqual(labels, ["WRONG NAME", "FRAMING", "CUT", "AUDIO", "LENGTH", "REPEAT", "INCOMPLETE", "TEXT",
                                  "HOOK", "RIGHTS", "Other"])
        self.assertEqual([c.label for c in KL.CODES if c.hard], labels[:6])
        for c in KL.CODES[:-1]:
            self.assertIn(c.gate, KL.GATES, c.label)
        self.assertEqual(KL.code("hook").label, "HOOK")
        self.assertEqual(KL.code("WRONG NAME").key, "wrong-name")
        self.assertEqual(KL.code("wrong_name").key, "wrong-name")
        self.assertIsNone(KL.code(""))
        with self.assertRaises(ValueError):
            KL.code("boring")

    def test_decline_stores_the_code_beside_the_words_and_kills_the_span(self):
        self.job("pressed", [[100.0, 130.0], [140.0, 170.0]])
        self.deliver(self.politics, "07:00", "pressed")
        lines = S.decline(self.cfg, self.politics, DAY, ["07:00"], "Andrew", "slow open", code="hook")
        self.assertIn("  07:00: declined", lines)              # the line review and the client page read as done
        s = self.slot(self.politics)
        self.assertEqual(s["status"], B.DECLINED)
        r = s["reason"]
        self.assertEqual((r["op"], r["code"], r["label"], r["gate"], r["hard"], r["text"], r["by"]),
                         ("decline", "hook", "HOOK", "hook", False, "slow open", "Andrew"))
        self.assertEqual(r["clip"], s["clip"]["sha256"])
        self.assertEqual(s["reasons"], [r])
        self.assertTrue(B.last_note(s).startswith("declined by Andrew: HOOK (slow open)"))
        es = KL.entries(self.politics)
        self.assertEqual(len(es), 1)
        e = es[0]
        self.assertEqual((e["video"], e["segments"], e["span"]), (VID, [[100.0, 130.0], [140.0, 170.0]], [100.0, 170.0]))
        self.assertEqual((e["code"], e["op"], e["slot"], e["date"], e["slug"]), ("hook", "decline", "07:00",
                                                                                 DAY.isoformat(), "pressed"))
        self.assertFalse(e["whole_video"])
        self.assertIn("on the kill list", r["kill"])
        self.assertIn(f"{VID} 100-170s", r["kill"])
        card = RV._card(self.cfg, self.politics, DAY, s, self.at("05:00"))["bottom"]
        self.assertIn("Declined by Andrew: HOOK (slow open). On the kill list", card)

    def test_other_needs_words_and_words_alone_count_as_other(self):
        self.job("a", [[1, 30]])
        self.deliver(self.politics, "07:00", "a")
        with self.assertRaises(SystemExit):
            S.decline(self.cfg, self.politics, DAY, ["07:00"], "Andrew", "", code="other")
        S.decline(self.cfg, self.politics, DAY, ["07:00"], "Andrew", "the anchor talks over it")
        self.assertEqual(self.slot(self.politics)["reason"]["code"], "other")
        with self.assertRaises(SystemExit):
            S.decline(self.cfg, self.politics, DAY, ["08:45"], "Andrew", "", code="dull")

    def test_make_again_records_a_code_but_kills_nothing(self):
        self.job("b", [[1, 30]])
        self.deliver(self.politics, "07:00", "b")
        S.decline(self.cfg, self.politics, DAY, ["07:00"], "Andrew", "", remake=True, code="text")
        s = self.slot(self.politics)
        self.assertEqual(s["status"], B.OPEN)
        self.assertEqual(s["reasons"][-1]["op"], "remake")
        self.assertEqual(KL.entries(self.politics), [])
        S.decline(self.cfg, self.politics, DAY, ["08:45"], "Andrew", "", remake=True)     # no code: nothing recorded
        self.assertNotIn("reasons", self.slot(self.politics, "08:45"))

    def test_an_old_free_text_decline_still_reads(self):
        with B.edit(self.cfg, self.politics, DAY) as bd:
            s = bd["slots"]["07:00"]
            s.update(status=B.DECLINED, slug="old", notes=["2031-07-07T13:00:00+00:00 collected from x (70.0s)",
                                                           "2031-07-07T14:00:00+00:00 declined by Andrew: weak hook"])
            bd["slots"]["08:45"]["notes"] = ["2031-07-07T15:00:00+00:00 pulled by Andrew; already out on youtube_shorts"]
        old = KL.reasons_of(self.slot(self.politics))
        self.assertEqual([(r["op"], r["code"], r["text"], r["by"]) for r in old], [("decline", None, "weak hook", "Andrew")])
        self.assertEqual(KL.reasons_of(self.slot(self.politics, "08:45"))[0]["text"], "")
        rows = KL.stat_rows(self.cfg)["reasons"]
        self.assertEqual([(r["code"], r["op"], r["legacy"]) for r in rows], [(None, "decline", True)])   # a bare pull: no
        self.assertEqual(rows[0]["week"], "2031-07-07")                                                  # verdict


# --------------------------------------------------------------------------------------------- Pull
class Pulls(Base):
    def test_a_coded_pull_kills_and_approving_the_same_clip_again_lifts_it(self):
        self.job("c", [[10, 40]])
        self.deliver(self.politics, "07:00", "c")
        self.signed(self.politics, "07:00")
        lines = S.pull(self.cfg, self.politics, DAY, ["07:00"], "Andrew", code="text", reason="typo in the title")
        self.assertTrue(any(": pulled" in ln and "TEXT (typo in the title)" in ln for ln in lines), lines)
        s = self.slot(self.politics)
        self.assertEqual((s["status"], s["reason"]["op"], s["reason"]["code"]), (B.READY, "pull", "text"))
        self.assertEqual(len(KL.entries(self.politics)), 1)
        self.assertIn("Pulled by Andrew: TEXT (typo in the title).", RV._card(self.cfg, self.politics, DAY, s,
                                                                              self.at("05:00"))["bottom"])
        out = self.signed(self.politics, "07:00")
        self.assertTrue(any("kill list entry" in ln and "lifted" in ln for ln in out), out)
        self.assertEqual(KL.entries(self.politics), [])
        self.assertEqual(len(KL.entries(self.politics, active=False)), 1)

    def test_a_pull_with_no_reason_records_nothing(self):
        self.job("d", [[10, 40]])
        self.deliver(self.politics, "07:00", "d")
        self.signed(self.politics, "07:00")
        S.pull(self.cfg, self.politics, DAY, ["07:00"], "Andrew")
        s = self.slot(self.politics)
        self.assertNotIn("reason", s)
        self.assertEqual(KL.entries(self.politics), [])
        self.assertEqual(KL.stat_rows(self.cfg)["reasons"], [])

    def test_pulling_a_clip_that_signed_itself_says_so(self):
        self.job("e", [[10, 40]])
        self.deliver(self.politics, "07:00", "e")
        S.approve(self.cfg, self.politics, DAY, ["07:00"], "x", now=self.at("06:35"), auto=True)
        S.pull(self.cfg, self.politics, DAY, ["07:00"], "Andrew", code="audio")
        self.assertTrue(self.slot(self.politics)["reason"]["auto_signed"])
        self.assertTrue(KL.stat_rows(self.cfg)["reasons"][0]["auto"])

    def test_a_pull_then_a_decline_of_the_same_clip_counts_once(self):
        self.job("f", [[10, 40]])
        self.deliver(self.politics, "07:00", "f")
        self.signed(self.politics, "07:00")
        S.pull(self.cfg, self.politics, DAY, ["07:00"], "Andrew", code="cut")
        S.decline(self.cfg, self.politics, DAY, ["07:00"], "Andrew", "", code="audio")
        rows = KL.stat_rows(self.cfg)["reasons"]
        self.assertEqual([(r["code"], r["gate"], r["hard"]) for r in rows], [("audio", "audio", True)])


# --------------------------------------------------------------------------------------------- what it stops
class Stops(Base):
    def kill(self, code: str, segments, broll=None, slug: str = "first"):
        self.job(slug, segments, broll=broll)
        self.deliver(self.politics, "07:00", slug)
        S.decline(self.cfg, self.politics, DAY, ["07:00"], "Andrew", "", code=code)

    def test_collect_refuses_a_killed_span_and_takes_one_beside_it(self):
        self.kill("hook", [[100.0, 170.0]])
        self.job("overlap", [[150.0, 190.0]], hhmm="08:45")         # 20 of its 40 seconds are killed: 50%
        said = self.deliver(self.politics, "08:45", "overlap", video=self.other)
        s = self.slot(self.politics, "08:45")
        self.assertEqual(s["status"], B.FAILED, said)
        self.assertEqual(s.get("fail_kind"), "refused")
        self.assertIn("on the kill list", B.last_note(s))
        self.assertTrue(any("REFUSED, on the kill list" in ln for ln in said), said)
        self.assertEqual(s["kill_hit"]["id"], KL.entries(self.politics)[0]["id"])
        # a clip with 5% of its seconds inside the killed span is cut from another moment: taken
        self.job("beside", [[168.0, 208.0]], hhmm="10:30")
        shutil.copy(self.other, self.tmp / "third.mp4")
        clip(self.tmp / "third.mp4", 2.7)
        self.deliver(self.politics, "10:30", "beside", video=self.tmp / "third.mp4")
        self.assertEqual(self.slot(self.politics, "10:30")["status"], B.READY)

    def test_another_video_is_never_held_against_a_kill(self):
        self.kill("hook", [[100.0, 170.0]])
        self.job("elsewhere", [[100.0, 170.0]], url="https://www.youtube.com/watch?v=ZZZZZZZZZZZ", hhmm="08:45")
        self.deliver(self.politics, "08:45", "elsewhere", video=self.other)
        self.assertEqual(self.slot(self.politics, "08:45")["status"], B.READY)

    def test_rights_kills_the_whole_video(self):
        self.kill("rights", [[100.0, 170.0]])
        e = KL.entries(self.politics)[0]
        self.assertTrue(e["whole_video"])
        self.job("later", [[900.0, 940.0]], hhmm="08:45")
        self.deliver(self.politics, "08:45", "later", video=self.other)
        s = self.slot(self.politics, "08:45")
        self.assertEqual(s["status"], B.FAILED)
        self.assertIn(f"its source video {VID} is on the kill list", B.last_note(s))
        self.assertEqual(set(KL.killed_videos(self.politics)), {VID})
        self.assertIn("NO:", KL.check(self.cfg, self.politics, URL)[0])

    def test_b_roll_is_skipped_only_for_codes_where_the_picture_can_be_wrong(self):
        pic = [{"at": 105, "page": PIC + "/", "asset": "atlas-p-1.jpg", "approved": True}]
        self.kill("hook", [[100.0, 130.0]], broll=pic)
        e = KL.entries(self.politics)[0]
        self.assertEqual(e["broll"], [f"pic:{PIC.lower()}"])
        self.assertFalse(e["broll_enforced"])
        self.job("same-pic", [[500.0, 540.0]], broll=pic, hhmm="08:45")
        self.deliver(self.politics, "08:45", "same-pic", video=self.other)
        self.assertEqual(self.slot(self.politics, "08:45")["status"], B.READY)     # HOOK: the picture was not the fault
        S.decline(self.cfg, self.politics, DAY, ["08:45"], "Andrew", "", code="wrong-name")
        self.assertTrue(KL.entries(self.politics)[-1]["broll_enforced"])
        self.job("third-pic", [[800.0, 840.0]], broll=pic, url="https://www.youtube.com/watch?v=YYYYYYYYYYY",
                 hhmm="10:30")
        clip(self.tmp / "fourth.mp4", 2.8)
        self.deliver(self.politics, "10:30", "third-pic", video=self.tmp / "fourth.mp4")
        s = self.slot(self.politics, "10:30")
        self.assertEqual(s["status"], B.FAILED)
        self.assertIn("its b-roll", B.last_note(s))

    def test_the_bench_rejects_a_killed_span_for_good(self):
        self.kill("repeat", [[100.0, 170.0]])
        # the declined clip on its board already keeps a spare off its seconds (the bench's 15% rule); once its slot is
        # made again the board forgets it, and the kill list still says no
        S.decline(self.cfg, self.politics, DAY, ["07:00"], "Andrew", "", remake=True)
        hist = BN.history(self.cfg, self.politics)
        why = BN.went_out(self.cfg, self.politics, "spare", "job-spare", (120.0, 160.0), {URL, VID}, DAY, hist)
        self.assertTrue(why.startswith("on the kill list:"), why)
        ok = BN.went_out(self.cfg, self.politics, "spare2", "job-spare", (400.0, 440.0), {URL, VID}, DAY, hist)
        self.assertEqual(ok, "")
        with mock.patch.object(KL, "entries", lambda a, active=True: [dict(e, whole_video=True)
                                                                      for e in KL._read(a)["entries"]]):
            whole = BN.went_out(self.cfg, self.politics, "spare3", "job-spare", (400.0, 440.0), {URL, VID}, DAY,
                                BN.history(self.cfg, self.politics))
        self.assertIn(f"its source video {VID} is on the kill list", whole)

    def test_a_lifted_entry_stops_nothing(self):
        self.kill("hook", [[100.0, 170.0]])
        eid = KL.entries(self.politics)[0]["id"]
        with self.assertRaises(SystemExit):
            KL.lift(self.cfg, self.politics, eid, "")
        KL.lift(self.cfg, self.politics, eid, "the clip was fine after all", who="Andrew")
        self.job("again", [[100.0, 170.0]], hhmm="08:45")
        self.deliver(self.politics, "08:45", "again", video=self.other)
        self.assertEqual(self.slot(self.politics, "08:45")["status"], B.READY)
        self.assertIn("LIFTED", "\n".join(KL.show(self.cfg, self.politics, all_=True)))

    def test_check_says_which_seconds_are_killed(self):
        self.kill("hook", [[100.0, 170.0]])
        self.assertIn("100-170", "\n".join(KL.check(self.cfg, self.politics, URL)))
        self.assertIn("NO:", KL.check(self.cfg, self.politics, URL, "110-160")[0])
        self.assertIn("ok:", KL.check(self.cfg, self.politics, URL, "300-340")[0])
        self.assertIn("ok:", KL.check(self.cfg, self.politics, "https://www.youtube.com/watch?v=QQQQQQQQQQQ")[0])

    def test_check_at_pick_time_says_a_repeat(self):
        # 10-25: the night picked moments already posted (Reagan, Cruz/Tucker) and found out only after the render
        self.job("posted", [[100.0, 170.0]], hhmm="08:45")
        self.deliver(self.politics, "08:45", "posted", video=self.other)
        got = KL.check(self.cfg, self.politics, URL, "110-160", day=DAY + timedelta(days=3))[0]
        self.assertIn("NO: a repeat", got)
        self.assertIn("posted", got)
        later = KL.check(self.cfg, self.politics, URL, "400-460", day=DAY + timedelta(days=3))[0]
        self.assertIn("ok:", later)                                      # other seconds of it, another day: fine
        same_day = KL.check(self.cfg, self.politics, URL, "400-460", day=DAY)[0]
        self.assertIn("one source once a day", same_day)

    def test_the_scout_shows_a_rights_kill_as_killed_and_lists_the_kill_list(self):
        self.kill("rights", [[100.0, 170.0]])
        finds = [{"id": VID, "url": URL, "status": "new", "title": "x"},
                 {"id": "other", "url": "https://www.youtube.com/watch?v=OOOOOOOOOOO", "status": "new", "title": "y"}]
        self.assertEqual(SC.kill_listed(self.politics, finds), 1)
        self.assertEqual([f["status"] for f in finds], ["killed", "new"])
        self.assertIn("RIGHTS", finds[0]["why"])
        self.assertIn(f"`{VID}` the whole video", "\n".join(SC._kill_list_lines(self.politics)))


# --------------------------------------------------------------------------------------------- GTA
class Gta(Base):
    def test_gta_b_roll_is_read_off_the_day_politics_without_film_moments(self):
        pub = self.gta.account_dir / "Publish"
        pub.mkdir(parents=True, exist_ok=True)
        (pub / f"broll-used-{DAY}.json").write_text(json.dumps({"entries": [
            {"slug": "s1", "kind": "reel", "source": "3Vose4XY74k", "key": "surf/3Vose4XY74k-r006.mp4", "picture": ""},
            {"slug": "s1", "kind": "film", "source": "film/XAg", "key": "film/XAg@920.15", "picture": ""},
            {"slug": "s1", "kind": "library", "source": "", "key": "lib/x.mp4", "picture": "https://p/1"},
            {"slug": "other", "kind": "reel", "source": "zz", "key": "surf/zz-r001.mp4", "picture": ""}]}))
        ids, srcs, film = KL.gta_broll(self.gta, "s1", DAY.isoformat())
        self.assertEqual(ids, ["key:surf/3Vose4XY74k-r006.mp4", "key:lib/x.mp4", "pic:https://p/1"])
        self.assertEqual(srcs, ["src:3Vose4XY74k"])
        self.assertEqual(film, 1)

    def test_the_night_never_picks_a_killed_candidate_and_skips_a_rights_video(self):
        with KL._edit(self.gta) as d:
            d["entries"] += [{"id": "k1", "video": "vid00000001", "segments": [[100.0, 140.0]], "code": "hook",
                              "date": DAY.isoformat(), "slot": "07:00", "by": "Andrew", "op": "decline"},
                             {"id": "k2", "video": "vid00000002", "segments": [[1.0, 2.0]], "whole_video": True,
                              "code": "rights", "date": DAY.isoformat(), "slot": "08:45", "by": "Andrew",
                              "op": "decline"}]
        cands = [{"n": 1, "start": 100, "end": 140}, {"n": 2, "start": 135, "end": 175}, {"n": 3, "start": 300, "end": 340}]
        with mock.patch.object(M, "job_video", lambda a, j: "vid00000001"):
            keep, said = M.drop_killed(self.gta, "job", cands)
        self.assertEqual([c["n"] for c in keep], [2, 3])          # 2 shares 5 of its 40 seconds: under 15%
        self.assertIn("candidate 1 skipped (kill list:", said[0])
        self.assertEqual(M.killed_ids(self.gta), {"vid00000002"})
        inbox = self.gta.account_dir / "Publish" / "inbox.json"
        inbox.parent.mkdir(parents=True, exist_ok=True)
        rows = {f"vid0000000{n}": {"url": f"https://www.youtube.com/watch?v=vid0000000{n}", "status": "new",
                                   "kind": "breakdown", "score": 10 - n, "channel": f"c{n}", "title": f"t{n}"}
                for n in (1, 2, 3)}
        inbox.write_text(json.dumps({"items": rows}))
        got = M.gta_sources(self.gta, set() | M.killed_ids(self.gta), DAY)
        self.assertEqual([M.video_id(i["url"]) for i in got], ["vid00000001", "vid00000003"])

    def test_a_clip_refused_at_collect_for_killed_b_roll_tries_the_next_candidate(self):
        (self.gta.skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
        log = self.tmp / "make.log"
        tried = []

        def fake_run(cmd, log_, cwd=None, env=None, timeout=0):
            tried.append(int(cmd[cmd.index("--pick") + 1]))
            return subprocess.CompletedProcess(cmd, 0)

        def fake_deliver(cfg, acct, day, slot, log_, env):
            with B.edit(cfg, acct, day) as bd:
                t = bd["slots"][slot]
                if t["pick"] == 1:               # the first render carried killed b-roll: collect refused it
                    t.update(status=B.FAILED, fail_kind="refused", kill_hit={"id": "k", "candidate": 1})
                else:
                    t["status"] = B.READY
            return [f"  {slot}: collected"]

        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"]["07:00"]["status"] = B.MAKING
        cands = [{"n": 1, "start": 0, "end": 40}, {"n": 2, "start": 100, "end": 140}]
        with mock.patch.object(M, "run_logged", fake_run), mock.patch.object(M, "_deliver_slot", fake_deliver), \
                mock.patch.object(M, "gta_candidates", lambda a, j: (cands, [])), \
                mock.patch.object(M, "gta_settings", lambda a: dict(M.GTA_MAKER_DEFAULTS, retries=2)):
            lines = M.cut_gta_slots(self.cfg, self.gta, DAY, "job", "underlay", [{"slot": "07:00"}], log, {})
        self.assertEqual(tried, [1, 2])
        self.assertTrue(any("refused at collect (kill list)" in ln for ln in lines), lines)
        s = self.slot(self.gta)
        self.assertEqual((s["status"], s["pick"]), (B.READY, 2))
        self.assertNotIn("kill_hit", s)


# --------------------------------------------------------------------------------------------- the gates' count
class Holds(Base):
    def two_unchecked(self, slug: str, hhmm: str = "07:00"):
        job = self.job(slug, [[10, 40]], hhmm=hhmm)
        jd = self.politics.account_dir / "Jobs" / job
        people = [{"id": p, "name": p.title(), "title": "MEMBER", "verified": None} for p in ("ann", "bob")]
        (jd / "cast.json").write_text(json.dumps({"people": people}))
        d = self.politics.delivered_dirs(DAY)[0] / f"{hhmm.replace(':', '')} {slug}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "cast-check.json").write_text(json.dumps({
            "rule": "test", "slot": hhmm, "slug": slug, "job": job, "cast_file": str(jd / "cast.json"),
            "people": [{"id": p, "name": p.title(), "title": "MEMBER", "named_as": "", "by_bot": True}
                       for p in ("ann", "bob")]}))
        self.deliver(self.politics, hhmm, slug)

    def test_approving_what_the_cast_rule_held_unchanged_is_a_needless_hold(self):
        self.two_unchecked("held")
        s = self.slot(self.politics)
        self.assertEqual(KL.held_by(self.cfg, self.politics, s, self.at("05:00"))["gate"], "cast")
        self.signed(self.politics, "07:00")
        h = self.slot(self.politics)["gate_holds"]
        self.assertEqual([(x["gate"], x["unchanged"], x["by"]) for x in h], [("cast", True, "Andrew")])
        rows = KL.stat_rows(self.cfg)["holds"]
        self.assertEqual([(r["gate"], r["unchanged"]) for r in rows], [("cast", True)])

    def test_words_edited_before_approve_are_not_unchanged(self):
        self.two_unchecked("edited")
        s = self.slot(self.politics)
        p = Path(s["copy"]["youtube_shorts"]["path"])
        p.write_text(p.read_text() + "\nA better line.\n")
        s = self.slot(self.politics)
        S.approve(self.cfg, self.politics, DAY, ["07:00"], "Andrew", now=self.at("05:00"), seen={"07:00": B.seen_key(s)})
        self.assertFalse(self.slot(self.politics)["gate_holds"][0]["unchanged"])

    def test_a_clip_nothing_held_writes_no_hold(self):
        self.job("free", [[10, 40]])
        self.deliver(self.politics, "07:00", "free")
        self.signed(self.politics, "07:00")
        self.assertNotIn("gate_holds", self.slot(self.politics))

    def test_a_late_slot_approved_is_the_late_rule_holding(self):
        self.job("late", [[10, 40]])
        self.deliver(self.politics, "07:00", "late")
        s = self.slot(self.politics)
        self.assertEqual(KL.held_by(self.cfg, self.politics, s, self.at("11:00"))["gate"], "late")
        S.approve(self.cfg, self.politics, DAY, ["07:00"], "Andrew", now=self.at("11:00"), seen={"07:00": B.seen_key(s)})
        self.assertEqual(self.slot(self.politics)["gate_holds"][0]["gate"], "late")


# --------------------------------------------------------------------------------------------- the one list
class OneList(Base):
    """The owner, 2026-10-20: "Use the reason-codes list ... with gates and hard flags; Blotato's verdicts log takes
    these codes." verdicts.py (the learner's log) reads its REASONS from reasons.CODES."""

    def rows(self):
        from clipfactory import verdicts as VD
        return [(r["verdict"], r["slot"], r["reasons"], r["note"]) for r in VD.rows(self.politics)]

    def test_the_log_takes_the_codes(self):
        from clipfactory import verdicts as VD
        self.assertEqual(VD.REASONS, tuple(c.key for c in KL.CODES))
        self.assertEqual(VD.CHIPS[:2], [["wrong-name", "Wrong name"], ["framing", "Framing"]])    # the chips' own case
        self.assertEqual(VD.HARD, KL.HARD)
        self.assertIn("pull", VD.VERDICTS)
        self.assertEqual(VD.check(["weak_hook", "too_short", "captions", "rights"]), ["hook", "length", "text", "rights"])

    def test_decline_and_a_coded_pull_write_the_learners_log(self):
        self.job("v1", [[10, 40]])
        self.deliver(self.politics, "07:00", "v1")
        S.decline(self.cfg, self.politics, DAY, ["07:00"], "Andrew", "slow open", code="hook")
        self.job("v2", [[100, 140]], hhmm="08:45")
        self.deliver(self.politics, "08:45", "v2", video=self.other)
        self.signed(self.politics, "08:45")
        S.pull(self.cfg, self.politics, DAY, ["08:45"], "Andrew")                    # no reason: nothing in the log
        self.signed(self.politics, "08:45")
        S.pull(self.cfg, self.politics, DAY, ["08:45"], "Andrew", code="text", reason="typo")
        got = [r for r in self.rows() if r[0] != "approve"]
        self.assertEqual(got, [("decline", "07:00", ["hook"], "slow open"), ("pull", "08:45", ["text"], "typo")])
        from clipfactory import learn as L
        counts = L.verdict_counts(self.politics, DAY, DAY)
        self.assertEqual((counts["decline"], counts["pull"]), (1, 1))
        self.assertEqual(counts["reasons"], {"hook": 1, "text": 1})

    def test_make_again_chips_are_the_same_codes_and_kill_nothing(self):
        self.job("v3", [[10, 40]])
        self.deliver(self.politics, "07:00", "v3")
        S.decline(self.cfg, self.politics, DAY, ["07:00"], "Andrew", "too dark", remake=True,
                  reasons=["weak_hook", "framing"])
        r = self.slot(self.politics)["reasons"][-1]
        self.assertEqual((r["op"], r["code"], r["more"], r["text"]), ("remake", "hook", ["framing"], "too dark"))
        self.assertTrue(B.last_note(self.slot(self.politics)).endswith("Andrew: too dark (reasons: hook, framing)"))
        self.assertEqual(KL.entries(self.politics), [])
        self.assertEqual(self.rows(), [("remake", "07:00", ["hook", "framing"], "too dark")])

    def test_old_and_new_codes_in_the_log_count_as_one(self):
        # rows written before the one list (Blotato's own codes) sit beside new ones in verdicts.jsonl: the learner
        # counts them as the code each became, and words() reads both and anything malformed without raising
        from clipfactory import learn as L
        from clipfactory import store
        from clipfactory import verdicts as VD
        base = {"by": "Andrew", "board": DAY.isoformat(), "slot": "07:00", "slug": "s", "clip_sha": "", "note": ""}
        for i, (verdict, reasons) in enumerate([("decline", ["weak_hook", "framing"]), ("decline", ["hook"]),
                                                ("remake", ["too_short", ["odd"], None]), ("decline", ["slow_start"])]):
            store.append(VD._path(self.politics.slug), dict(base, at=f"2031-07-08T0{i}:00:00+00:00", verdict=verdict,
                                                          reasons=reasons))
        got = L.verdict_counts(self.politics, DAY, DAY)
        self.assertEqual((got["decline"], got["remake"]), (3, 1))
        self.assertEqual(got["reasons"]["hook"], 3)
        self.assertEqual((got["reasons"]["framing"], got["reasons"]["length"]), (1, 1))
        self.assertNotIn("weak_hook", got["reasons"])
        self.assertIn("length", VD.words(["weak_hook", "hook", "too_long", None, ["odd"]]))   # reads all, never raises
        self.assertEqual(VD.code_of("weak_hook"), "hook")
        self.assertEqual(VD.normalize([" text ", "WRONG NAME", "nonsense"]), ["text", "wrong-name"])

    def test_the_page_offers_make_again_the_same_codes(self):
        self.job("v4", [[10, 40]])
        self.deliver(self.politics, "07:00", "v4")
        page = RV.page(self.cfg, DAY, self.at("05:00"), tok="t")
        self.assertIn('"wrong-name", "Wrong name"', page.split("const REASONS=", 1)[1].split(";", 1)[0])
        self.assertIn("if(op==='remake'){openChooser(b,op,account,slot);return}", page)


# --------------------------------------------------------------------------------------------- the page
class Page(Base):
    def test_a_ready_card_offers_the_codes_and_act_declines_with_one(self):
        self.job("p", [[10, 40]])
        self.deliver(self.politics, "07:00", "p")
        s = self.slot(self.politics)
        card = RV._card(self.cfg, self.politics, DAY, s, self.at("05:00"))
        b = card["bottom"]
        self.assertIn('data-op="why" data-why="decline"', b)
        self.assertNotIn('data-op="decline" data-acct', b.split('class="codes"')[0])   # Decline opens the reasons
        chips = re.findall(r'data-op="decline" data-code="([a-z-]+)" data-n="(\d)"', b)
        self.assertEqual([c for c, _ in chips], [c.key for c in KL.CODES if c.key != "other"])
        self.assertEqual([n for _, n in chips], ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"])
        self.assertEqual(b.count('class="code hard"'), 6)
        self.assertIn('placeholder="Other: say what"', b)
        self.assertIn('data-op="decline" data-code="other"', b)
        ok, msg = RV.act(self.cfg, {"op": "decline", "account": "politics", "date": DAY.isoformat(),
                                    "slots": ["07:00"], "code": "framing", "by": "Andrew"}, now=self.at("05:00"))
        self.assertTrue(ok, msg)
        self.assertIn("FRAMING", msg)
        self.assertEqual(self.slot(self.politics)["reason"]["code"], "framing")
        with self.assertRaises(SystemExit):
            RV.act(self.cfg, {"op": "decline", "account": "politics", "date": DAY.isoformat(),
                              "slots": ["08:45"], "code": "meh", "by": "Andrew"}, now=self.at("05:00"))

    def test_a_scheduled_card_pulls_with_a_code_or_with_no_reason(self):
        self.job("q", [[10, 40]])
        self.deliver(self.politics, "07:00", "q")
        self.signed(self.politics, "07:00")
        b = RV._card(self.cfg, self.politics, DAY, self.slot(self.politics), self.at("05:00"))["bottom"]
        self.assertIn('data-op="why" data-why="pull"', b)
        self.assertIn(">Pull, no reason</button>", b)
        self.assertIn("Nothing more of it goes out", b)            # the old confirm's words head the reasons
        self.assertEqual(len(re.findall(r'data-op="pull" data-code="', b)), 11)
        ok, msg = RV.act(self.cfg, {"op": "pull", "account": "politics", "date": DAY.isoformat(),
                                    "slots": ["07:00"], "code": "repeat", "by": "Andrew"}, now=self.at("05:00"))
        self.assertTrue(ok, msg)
        self.assertEqual(self.slot(self.politics)["reason"]["code"], "repeat")

    def test_the_page_script_parses_and_has_its_keys(self):
        self.job("r", [[10, 40]])
        self.deliver(self.politics, "07:00", "r")
        page = RV.page(self.cfg, DAY, self.at("05:00"), tok="t")
        js = page.split("<script>", 1)[1].rsplit("</script>", 1)[0]
        node = shutil.which("node")
        if node:
            r = subprocess.run([node, "--check", "-"], input=js, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        for want in ("keydown", "data-why", "openWhy", "k==='a'||k==='A'", "k==='p'||k==='P'", "/^[0-9]$/"):
            self.assertIn(want, js)

    def test_the_stats_tab_counts_codes_by_week_and_account(self):
        self.job("s", [[10, 40]])
        self.deliver(self.politics, "07:00", "s")
        S.decline(self.cfg, self.politics, DAY, ["07:00"], "Andrew", "", code="length")
        d = SP.data(self.cfg, now=self.at("12:00"))
        rows = d["reasons"]["reasons"]
        self.assertEqual([(r["account"], r["code"], r["gate"], r["hard"]) for r in rows],
                         [("politics", "length", "length", True)])
        today = self.cfg.now().astimezone(self.cfg.tz()).date()
        self.assertEqual(rows[0]["week"], (today - timedelta(days=today.weekday())).isoformat())
        self.assertEqual([g["key"] for g in d["reasons"]["gates"]][:2], ["cast", "framing"])
        page = SP.page("t", RV.CSS, RV.tabs("stats"))
        self.assertIn('id="why-codes"', page)
        self.assertIn("Why clips came back", page)
        node = shutil.which("node")
        if node:
            js = page.split("<script>", 1)[1].rsplit("</script>", 1)[0]
            r = subprocess.run([node, "--check", "-"], input=js, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
