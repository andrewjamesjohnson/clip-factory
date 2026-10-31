"""The automatic sign-off (the owner, 2026-10-19: "every clip that passed its checks signs itself 30 minutes before
its slot, signed as the factory, not me. Clips naming 2 or more people nobody checked still wait for me.").
Throwaway folders; no network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-autosign-state-"))

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

from clipfactory import autosign as A   # noqa: E402
from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import direct as D     # noqa: E402
from clipfactory import outlets as O    # noqa: E402
from clipfactory import poster as P     # noqa: E402
from clipfactory import review as R     # noqa: E402
from clipfactory import signoff as S    # noqa: E402

DAY = date(2031, 6, 3)
TITLE = "You can't 'earn' a billion dollars. | Alexandria Ocasio-Cortez"


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


AOC = {"id": "aoc", "name": "Alexandria Ocasio-Cortez", "title": "U.S. REPRESENTATIVE, NEW YORK",
       "named_as": "Alexandria Ocasio-Cortez", "frames": "0:04, 0:41", "by": "politics bot", "at": "2031-06-02"}
OTHER = {"id": "other", "name": "Some Witness", "title": "WITNESS", "named_as": "", "frames": "0:20",
         "by": "politics bot", "at": "2031-06-02", "by_bot": True}


class AutoSignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-autosign-"))
        cls.old_state, C.STATE = C.STATE, cls.tmp / "state"
        cls.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        cls.old_roots = S.CAST_ROOTS
        S.CAST_ROOTS = (cls.tmp / "Jobs",)
        cls.raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        cls.raw["drafts_root"] = str(cls.tmp / "drafts")
        for a in cls.raw["accounts"].values():
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 2, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "tiktok": {"mode": "export"}}
        cls.main = cls.tmp / "main.mp4"
        clip(cls.main, 2.5)

    @classmethod
    def tearDownClass(cls):
        C.STATE = cls.old_state
        O.TOKENS, D.TOKENS = cls.old_tokens
        S.CAST_ROOTS = cls.old_roots

    def setUp(self):
        for d in ("state", "drafts", "delivered", "Jobs"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.use(on=True)

    def use(self, **auto):
        raw = json.loads(json.dumps(self.raw))
        if auto.get("on") is None:
            raw.pop("auto_sign", None)
        else:
            raw["auto_sign"] = dict({"lead_minutes": 30, "late_hours": 3, "cast_unchecked": "one"}, **auto)
        path = self.tmp / "accounts.json"
        path.write_text(json.dumps(raw))
        self.cfg = C.load(path)
        self.acct = self.cfg.account("politics")

    def at(self, hhmm: str):
        return self.cfg.slot_time(DAY, hhmm)

    def cast(self, people) -> Path:
        path = self.tmp / "Jobs" / "aoc-job" / "cast.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for p in people:
            rows.append({"id": p["id"], "name": p["name"], "title": p["title"], "role": "member", "verified": None,
                         **({"provisional": {"named_as": p["named_as"], "source_title": TITLE, "frames": p["frames"],
                                             "by": p["by"], "at": p["at"]}} if p.get("named_as") else {})})
        path.write_text(json.dumps({"people": rows}, indent=1))
        return path

    def deliver(self, hhmm: str, people=None, slug: str = "earn-a-billion") -> Path:
        d = self.acct.delivered_dirs(DAY)[0] / f"{hhmm} {slug}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.main, d / f"01-{slug}.mp4")
        for p in ("youtube", "tiktok_reach", "facebook", "instagram"):
            (d / f"{p}.txt").write_text(f"TITLE\nThe exchange at {hhmm}\n\nCAPTION\nWords for {p}.\n")
        if people:
            cast_file = self.cast(people)
            (d / "cast-check.json").write_text(json.dumps({
                "rule": "test", "slot": hhmm, "slug": slug, "job": "aoc-job", "cast_file": str(cast_file),
                "source_title": TITLE, "people": people}))
        K.collect(self.cfg, self.acct, DAY)
        return d

    def slot(self, k: str = "07:00") -> dict:
        return B.read(self.acct, DAY)["slots"][k]

    def notes(self, k: str = "07:00") -> str:
        return " | ".join(str(n) for n in self.slot(k).get("notes") or [])

    def card(self, now) -> dict:
        return R._section(self.cfg, self.acct, DAY, now)["cards"][0]

    def test_01_off_without_the_switch(self):
        self.use(on=None)
        self.deliver("0700")
        self.assertEqual(A.run(self.cfg, self.at("06:45")), [])
        self.assertEqual(self.slot()["status"], B.READY)
        self.assertNotIn("signs itself", self.card(self.at("06:00"))["top"])

    def test_02_signs_itself_30_minutes_before_as_the_factory(self):
        self.deliver("0700")
        self.assertEqual(A.run(self.cfg, self.at("06:29")), [])
        self.assertEqual(self.slot()["status"], B.READY)
        out = A.run(self.cfg, self.at("06:31"))
        s = self.slot()
        self.assertEqual(s["status"], B.SCHEDULED, out)
        self.assertEqual(s["signoff"]["by"], A.BY)
        self.assertTrue(s["signoff"]["auto"])
        self.assertNotIn("Andrew", s["signoff"]["by"])
        self.assertEqual(B.verify(s), [])                       # Approve's own signature: the clip, words, time
        self.assertIn("signed automatically", self.notes())
        self.assertIn("automatic: nobody pressed Approve", self.card(self.at("06:32"))["bottom"])

    def test_03_one_person_cast_signs_but_is_never_written_as_checked(self):
        self.deliver("0700", [AOC])
        A.run(self.cfg, self.at("06:31"))
        s = self.slot()
        self.assertEqual(s["status"], B.SCHEDULED)
        people = json.loads((self.tmp / "Jobs" / "aoc-job" / "cast.json").read_text())["people"]
        self.assertIsNone(people[0]["verified"])                # no person watched it: no person's cast check
        self.assertIn("cast not checked by a person (Alexandria Ocasio-Cortez)", self.notes())
        self.assertNotIn("confirmed", s["cast_check"])

    def test_04_two_unchecked_people_wait_for_a_person(self):
        self.deliver("0700", [AOC, OTHER])
        self.assertEqual(A.run(self.cfg, self.at("06:45")), [])
        s = self.slot()
        self.assertEqual(s["status"], B.READY)
        self.assertIn("names 2 people nobody checked", A.why_not(self.cfg, self.acct, s, self.at("06:45")))
        self.assertIn("It will not sign itself", self.card(self.at("06:45"))["bottom"])
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertEqual(self.slot()["signoff"]["by"], "Andrew")  # his Approve still signs it, in his name

    def test_05_a_pull_holds_that_clip_until_a_person_approves(self):
        self.deliver("0700")
        A.run(self.cfg, self.at("06:31"))
        out = S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertIn("will not sign itself again", " ".join(out))
        self.assertEqual(self.slot()["status"], B.READY)
        self.assertEqual(A.run(self.cfg, self.at("06:45")), [])
        self.assertEqual(self.slot()["status"], B.READY)
        self.assertIn("pulled it", self.card(self.at("06:45"))["bottom"])
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        s = self.slot()
        self.assertEqual((s["status"], s["signoff"]["by"]), (B.SCHEDULED, "Andrew"))
        self.assertNotIn("auto_hold", s)

    def test_06_a_new_clip_in_a_pulled_slot_is_free_again(self):
        s = {"clip": {"sha256": "aaa"}}
        A.hold(s, "Andrew")
        self.assertTrue(A.held_by_pull(s))
        s["clip"]["sha256"] = "bbb"
        self.assertFalse(A.held_by_pull(s))

    def test_07_never_more_than_late_hours_after_its_time(self):
        self.deliver("0700")
        self.assertEqual(A.run(self.cfg, self.at("10:01")), [])
        self.assertEqual(self.slot()["status"], B.READY)
        A.run(self.cfg, self.at("09:59"))
        self.assertEqual(self.slot()["status"], B.SCHEDULED)

    def test_08_cast_rules_none_and_all(self):
        self.use(on=True, cast_unchecked="none")
        self.deliver("0700", [AOC])
        A.run(self.cfg, self.at("06:31"))
        self.assertEqual(self.slot()["status"], B.READY)
        self.setUp()
        self.use(on=True, cast_unchecked="all")
        self.deliver("0700", [AOC, OTHER])
        A.run(self.cfg, self.at("06:31"))
        self.assertEqual(self.slot()["status"], B.SCHEDULED)

    def test_09_the_card_and_the_notice_say_when(self):
        self.deliver("0700")
        self.assertIn("signs itself at 06:30", self.card(self.at("06:00"))["top"])
        bd = B.read(self.acct, DAY)
        self.assertIn("sign themselves 30 min before their slots", A.morning_words(self.cfg, self.acct, bd,
                                                                                    self.at("06:00")))

    def test_10_a_dry_run_signs_nothing(self):
        self.deliver("0700")
        out = P.post_due(self.cfg, now=self.at("06:31"), dry_run=True)
        self.assertIn("would sign itself now", " ".join(out))
        self.assertEqual(self.slot()["status"], B.READY)

    def test_11_a_refusal_is_noted_once_and_not_retried_until_it_changes(self):
        d = self.deliver("0700")
        words = Path(next(iter(self.slot()["copy"].values()))["path"])
        words.unlink()
        A.run(self.cfg, self.at("06:31"))
        A.run(self.cfg, self.at("06:41"))
        s = self.slot()
        self.assertEqual(s["status"], B.READY)
        self.assertEqual(self.notes().count("not signed automatically"), 1)
        self.assertIn("Approve refused it", A.why_not(self.cfg, self.acct, s, self.at("06:45")))
        self.assertTrue(d.is_dir())

    def test_12_post_due_signs_then_hands_off_in_one_pass(self):
        self.deliver("0845", slug="later-one")
        # nothing is due to post at 08:16 (YouTube is not connected in this test), but the clip is signed and handed off
        P.post_due(self.cfg, now=self.at("08:16"), posters={})
        s = self.slot("08:45")
        self.assertEqual((s["status"], s["signoff"]["by"]), (B.SCHEDULED, A.BY))

    def test_13_a_client_account_takes_only_its_own_block(self):
        acct = C.Account(**{**self.acct.__dict__, "raw": dict(self.acct.raw, owner="client")})
        self.assertFalse(A.settings(self.cfg, acct)["on"])
        acct2 = C.Account(**{**self.acct.__dict__, "raw": dict(self.acct.raw, owner="client", auto_sign={"on": True})})
        self.assertTrue(A.settings(self.cfg, acct2)["on"])

    def test_14_doctor_says_it_is_on(self):
        self.assertTrue(any("automatic sign-off ON, 30 min before each slot" in ln for ln in A.doctor_line(self.cfg)))

    def test_16_the_coach_gate_on_holds_a_fix_until_the_clip_passes(self):
        from clipfactory import coach as CO
        self.use(on=True, coach_gate="on")
        self.deliver("0700", [AOC])
        s = self.slot()
        p = CO.card_path(self.acct, DAY, "07:00")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"clip_sha": s["clip"]["sha256"], "written": {"verdict": "FIX", "fix": "Open on the answer."}}))
        self.assertEqual(A.run(self.cfg, self.at("06:31")), [])
        self.assertEqual(self.slot()["status"], B.READY)
        why = A.why_not(self.cfg, self.acct, self.slot(), self.at("06:31"), board=B.read(self.acct, DAY))
        self.assertIn("the coach said FIX: Open on the answer.", why)
        p.write_text(json.dumps({"clip_sha": s["clip"]["sha256"], "written": {"verdict": "PASS"}}))
        A.run(self.cfg, self.at("06:31"))
        self.assertEqual(self.slot()["status"], B.SCHEDULED)
        self.use(on=True, coach_gate="shadow")
        self.assertEqual(A.gate_hold(self.acct, s, B.read(self.acct, DAY), A.settings(self.cfg, self.acct)), "")

    def test_17_a_slot_held_twice_waits_for_a_person(self):
        from clipfactory import coach as CO
        st = dict(A.settings(self.cfg, self.acct), coach_gate="on")
        bd = {"date": DAY.isoformat()}
        p = CO.card_path(self.acct, DAY, "09:00")
        p.parent.mkdir(parents=True, exist_ok=True)
        why = ""
        for sha in ("aaa", "bbb"):
            p.write_text(json.dumps({"clip_sha": sha, "written": {"verdict": "FIX", "fix": "Trim the start."}}))
            why = A.gate_hold(self.acct, {"slot": "09:00", "clip": {"sha256": sha}}, bd, st)
        self.assertIn("held twice", why)
        p.write_text(json.dumps({"clip_sha": "old", "written": {}}))
        self.assertIn("waits for the coach", A.gate_hold(self.acct, {"slot": "09:00", "clip": {"sha256": "ccc"}}, bd, st))
        self.assertEqual(A.gate_hold(self.acct, {"slot": "09:00", "clip": {"sha256": "ccc"}}, None, st), "")

    def test_15_the_coach_gate_shadow_writes_down_and_holds_nothing(self):
        from datetime import datetime
        from clipfactory import coach as CO
        s = {"slot": "07:00", "clip": {"sha256": "abc123"}}
        bd = {"slots": {"07:00": s}}
        p = CO.card_path(self.acct, DAY, "07:00")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"clip_sha": "abc123", "written": {"verdict": "KILL"}}))
        now = datetime(2031, 6, 3, 6, 30)
        st = dict(A.settings(self.cfg, self.acct), coach_gate="shadow")
        lines = A.shadow(self.acct, DAY, bd, ["07:00"], st, now)
        self.assertEqual(len(lines), 1)
        self.assertIn("the coach said KILL", lines[0])
        log = p.parent.parent / "gate-shadow.jsonl"
        self.assertEqual(json.loads(log.read_text().splitlines()[-1])["verdict"], "KILL")
        self.assertEqual(A.shadow(self.acct, DAY, bd, ["07:00"], dict(st, coach_gate="off"), now), [])
        stale = {"slots": {"07:00": {"slot": "07:00", "clip": {"sha256": "other"}}}}
        self.assertEqual(A.shadow(self.acct, DAY, stale, ["07:00"], st, now), [])
        self.assertEqual(json.loads(log.read_text().splitlines()[-1])["verdict"], "none")
        self.assertIn(A.settings(self.cfg, self.acct)["coach_gate"], A.GATES)


if __name__ == "__main__":
    unittest.main()
