"""A platform's own cut (the Politics's Instagram takes about 60s, its clips run 65-90): collected beside the clip, signed
with it, posted to that platform alone, shown on the card. Throwaway folders and fake platforms; no network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import direct as D     # noqa: E402
from clipfactory import outlets as O    # noqa: E402
from clipfactory import poster as P     # noqa: E402
from clipfactory import review as R     # noqa: E402
from clipfactory import signoff as S    # noqa: E402

DAY = date(2031, 5, 6)


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class CutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-cuts-"))
        cls.old_state, C.STATE = C.STATE, cls.tmp / "state"
        cls.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(cls.tmp / "drafts")
        for a in raw["accounts"].values():
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 2, "max": 3}            # the Politics's 65-90, scaled down
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "instagram_reels": {"mode": "api", "min_seconds": 1, "max_seconds": 1.6},   # its "60s"
                            "tiktok": {"mode": "export"}}
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.main, cls.short, cls.long_cut = cls.tmp / "main.mp4", cls.tmp / "short.mp4", cls.tmp / "long.mp4"
        cls.short2 = cls.tmp / "short2.mp4"                # another cut: one file never joins two slots (test 4e)
        clip(cls.main, 2.5)
        clip(cls.short, 1.2)
        clip(cls.short2, 1.3)
        clip(cls.long_cut, 2.2)

    @classmethod
    def tearDownClass(cls):
        C.STATE = cls.old_state
        O.TOKENS, D.TOKENS = cls.old_tokens

    def setUp(self):
        for d in ("state", "drafts", "delivered"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.acct = self.cfg.account("politics")
        self.calls = []

    def deliver(self, hhmm: str, cut: Path = None, slug: str = "the-exchange") -> Path:
        d = self.acct.delivered_dirs(DAY)[0] / f"{hhmm} {slug}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.main, d / f"01-{slug}.mp4")
        for p in ("youtube", "tiktok_reach", "facebook", "instagram"):
            (d / f"{p}.txt").write_text(f"TITLE\nThe exchange at {hhmm}\n\nCAPTION\nWords for {p}.\n")
        if cut:
            shutil.copy(cut, d / "instagram.mp4")
        return d

    def slot(self, k: str) -> dict:
        return B.read(self.acct, DAY)["slots"][k]

    def posters(self):
        def make(plat):
            def post(acct, path, words, slot):
                self.calls.append((plat, Path(path).name))
                return {"post_id": f"{plat}-{slot['slot']}", "url": f"https://x/{plat}"}
            return post
        return {p: make(p) for p in ("facebook_reels", "instagram_reels")}

    def test_1_the_cut_is_collected_signed_and_posted_to_instagram_alone(self):
        self.deliver("0700", cut=self.short)
        out = K.collect(self.cfg, self.acct, DAY)
        s = self.slot("07:00")
        self.assertEqual(s["status"], B.READY, out)
        self.assertEqual(round(s["cuts"]["instagram_reels"]["seconds"], 1), 1.2)
        self.assertTrue(Path(s["cuts"]["instagram_reels"]["path"]).name == "instagram_reels.mp4")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertEqual(B.verify(self.slot("07:00")), [])
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:05"), posters=self.posters(), uploader=lambda a, s: "vid")
        self.assertIn(("instagram_reels", "instagram_reels.mp4"), self.calls)     # the cut to Instagram
        self.assertIn(("facebook_reels", "01-the-exchange.mp4"), self.calls)     # the whole clip elsewhere

    def test_1b_facebook_takes_only_the_days_first_n_posts(self):
        # 10-26: Politics Facebook posts 7+ got 13 views at most; Instagram and YouTube have no such cliff
        self.cfg.account("politics").outlets["facebook_reels"]["daily_cap"] = 2
        ks = self.acct.slots_for(DAY)[:3]
        for k in ks:
            self.deliver(k.replace(":", ""), slug=f"clip-{k.replace(':', '')}")
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ks, "Andrew")
        for k in ks:
            P.post_due(self.cfg, now=self.cfg.slot_time(DAY, k) + timedelta(minutes=5), posters=self.posters(),
                       uploader=lambda a, s: "vid")
        self.assertEqual([n for p, n in self.calls if p == "facebook_reels"],
                         ["01-clip-0700.mp4", "01-clip-0845.mp4"])
        fb = self.slot(ks[-1])["outlets"]["facebook_reels"]
        self.assertTrue(fb["capped"] and fb["gave_up"])
        self.assertIn("first 2 posts", fb["why"])

    def test_1c_a_day_the_cap_test_names_takes_every_clip_and_marks_the_extras(self):
        # 10-27 to 11-02, the Facebook cap test: "daily_cap_by_day" lifts the cap on the days it names (0: no cap); a
        # post past the usual cap carries past_usual_cap (learn leaves it out); a day not named keeps the cap
        fb = self.cfg.account("politics").outlets["facebook_reels"]
        fb["daily_cap"] = 2
        fb["daily_cap_by_day"] = {DAY.isoformat(): 0, (DAY + timedelta(days=1)).isoformat(): "x"}
        ks = self.acct.slots_for(DAY)[:3]
        for k in ks:
            self.deliver(k.replace(":", ""), slug=f"clip-{k.replace(':', '')}")
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ks, "Andrew")
        for k in ks:
            P.post_due(self.cfg, now=self.cfg.slot_time(DAY, k) + timedelta(minutes=5), posters=self.posters(),
                       uploader=lambda a, s: "vid")
        self.assertEqual(len([n for p, n in self.calls if p == "facebook_reels"]), 3)
        self.assertEqual([self.slot(k)["outlets"]["facebook_reels"].get("past_usual_cap") for k in ks], [None, None, 2])
        self.assertFalse([o for k in ks for o, r in self.slot(k)["outlets"].items()
                          if o != "facebook_reels" and r.get("past_usual_cap")])                 # no cap there
        acct = self.cfg.account("politics")
        self.assertEqual(P._daily_cap(acct, "facebook_reels", DAY), 0)
        self.assertEqual(P._daily_cap(acct, "facebook_reels", DAY + timedelta(days=1)), 2)     # not a number
        self.assertEqual(P._daily_cap(acct, "facebook_reels", DAY + timedelta(days=2)), 2)     # not named
        self.assertEqual(P._daily_cap(acct, "facebook_reels"), 2)

    def test_2_a_changed_cut_voids_the_signature(self):
        self.deliver("0700", cut=self.short)
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        Path(self.slot("07:00")["cuts"]["instagram_reels"]["path"]).write_bytes(b"not the signed cut")
        self.assertTrue(any("cut" in p for p in B.verify(self.slot("07:00"))))

    def test_3_no_cut_and_too_long_is_said_not_tried(self):
        self.deliver("0700")
        K.collect(self.cfg, self.acct, DAY)
        sec = R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        sec = R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))
        self.assertIn("needs its own 2s cut", sec["cards"][0]["bottom"])
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:05"), posters=self.posters(), uploader=lambda a, s: "vid")
        ig = self.slot("07:00")["outlets"]["instagram_reels"]
        self.assertTrue(ig["gave_up"])
        self.assertIn("no Instagram cut", ig["why"])
        self.assertNotIn("tries", ig)                                           # refused at once, not three times
        self.assertIn(("facebook_reels", "01-the-exchange.mp4"), self.calls)

    def test_4_a_cut_that_comes_later_joins_the_clip_signed_or_not(self):
        # 2026-10-29 (Andrew: late cuts join a signed clip by themselves). Before, a signed clip's cut waited for a
        # Pull nobody made: the 06:30 Van Hollen clip was signed at 21:31, its cuts came at 23:02 and 00:53, and
        # Instagram and YouTube gave up at the slot
        d = self.deliver("0700")
        d2 = self.deliver("0845", slug="the-second-exchange")
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["08:45"], "Andrew")
        signed = dict(self.slot("08:45")["signoff"])
        time.sleep(1.1)                                    # the delivery key counts whole seconds
        shutil.copy(self.short, d / "instagram.mp4")
        shutil.copy(self.short2, d2 / "instagram.mp4")
        out = K.collect(self.cfg, self.acct, DAY)
        self.assertIn("instagram_reels", self.slot("07:00").get("cuts", {}), out)     # unsigned: joins
        s = self.slot("08:45")
        self.assertIn("instagram_reels", s.get("cuts", {}), out)                     # signed: joins too
        self.assertIn(s["status"], B.SIGNED)
        self.assertEqual(B.verify(s), [])                                           # the signature takes the cut
        self.assertEqual((s["signoff"]["by"], s["signoff"]["at"]), (signed["by"], signed["at"]))
        self.assertEqual([(j["platform"], j["was"]) for j in s["signoff"]["cuts_joined"]],
                         [("instagram_reels", signed["hash"])])
        self.assertIn("joined the signed clip by itself", B.last_note(s))
        self.assertTrue(any("joined the signed clip" in ln for ln in out), out)
        self.assertEqual(R._waiting_cuts(s, self.acct), [])                         # the card asks for no Pull
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "08:50"), posters=self.posters(), uploader=lambda a, s: "vid")
        self.assertIn(("instagram_reels", "instagram_reels.mp4"), self.calls)

    def test_4b_a_late_cut_with_its_own_words_waits_and_pull_takes_it(self):
        # a late cut joins a signed clip only when it changes nothing that was signed: its own words would
        d = self.deliver("0700")
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        time.sleep(1.1)
        shutil.copy(self.short, d / "instagram.mp4")
        (d / "instagram.cut.txt").write_text("TITLE\nThe exchange, cut\n\nCAPTION\nThe cut's own words.\n")
        K.collect(self.cfg, self.acct, DAY)
        K.collect(self.cfg, self.acct, DAY)                                  # still waiting, every run
        s = self.slot("07:00")
        self.assertNotIn("cuts", s)
        self.assertIn("it brings its own words", B.last_note(s))
        self.assertEqual(R._waiting_cuts(s, self.acct), ["Instagram"])             # the card asks for Pull
        out = S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertTrue(any("Instagram cut" in ln for ln in out), out)
        self.assertIn("instagram_reels", self.slot("07:00")["cuts"])
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertEqual(B.verify(self.slot("07:00")), [])
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:05"), posters=self.posters(), uploader=lambda a, s: "vid")
        self.assertIn(("instagram_reels", "instagram_reels.mp4"), self.calls)

    def test_4c_a_cut_that_comes_after_a_failed_whole_clip_joins_and_try_again_sends_the_cut(self):
        # 2026-10-17: Instagram gave up on the whole 76s clip (Meta's upload error, before cuts existed); its cut then
        # waited in the delivery folder, and Try again re-sent the 76s clip, refused at once. Since 10-29 the cut
        # joins the signed clip by itself, so Try again sends the cut, never the whole clip
        for name in ("meta", "youtube_token"):                 # signed in (files only: nothing can leave a test)
            (O.TOKENS / self.acct.slug).mkdir(parents=True, exist_ok=True)
            (O.TOKENS / self.acct.slug / f"{name}.json").write_text("{}")
        self.addCleanup(shutil.rmtree, O.TOKENS, True)
        d = self.deliver("0700")
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        meta_fail = "HTTP 400 from rupload.facebook.com: ProcessingFailedError"
        with B.edit(self.cfg, self.acct, DAY, create=False) as bd:
            bd["slots"]["07:00"].setdefault("outlets", {})["instagram_reels"] = {
                "tries": 3, "gave_up": True, "why": f"failed 3 times: {meta_fail}", "last_error": meta_fail}
        time.sleep(1.1)
        shutil.copy(self.short, d / "instagram.mp4")
        K.collect(self.cfg, self.acct, DAY)                                  # the cut joins the signed clip
        now = self.cfg.slot_time(DAY, "07:30")
        s = self.slot("07:00")
        self.assertIn("instagram_reels", s["cuts"])
        self.assertEqual(B.verify(s), [])
        self.assertTrue(R._retryable(s, self.acct))
        card = R._card(self.cfg, self.acct, DAY, s, now)
        self.assertIn('data-op="retry"', card["bottom"])
        self.assertNotIn("Press Pull", card["bottom"])
        out = S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertTrue(any("Instagram will be tried again" in ln for ln in out), out)
        P.post_due(self.cfg, now=now, posters=self.posters(), uploader=lambda a, s: "vid")
        self.assertIn(("instagram_reels", "instagram_reels.mp4"), self.calls)
        self.assertNotIn(("instagram_reels", "01-the-exchange.mp4"), self.calls)

    def test_4d_try_again_with_no_cut_anywhere_says_make_the_cut(self):
        self.deliver("0700")
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        with B.edit(self.cfg, self.acct, DAY, create=False) as bd:
            bd["slots"]["07:00"].setdefault("outlets", {})["instagram_reels"] = {
                "tries": 3, "gave_up": True, "why": "failed 3 times: Meta's upload error"}
        out = S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertTrue(any("needs its own cut" in ln and "make the cut" in ln for ln in out), out)
        self.assertTrue(self.slot("07:00")["outlets"]["instagram_reels"]["gave_up"])

    def test_4f_a_late_cut_waits_when_joining_would_change_what_went_out(self):
        d = self.deliver("0700")
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        with B.edit(self.cfg, self.acct, DAY, create=False) as bd:          # Instagram took the clip itself
            bd["slots"]["07:00"].setdefault("outlets", {})["instagram_reels"] = {"post_id": "ig-1", "url": "https://x/ig"}
        time.sleep(1.1)
        shutil.copy(self.short, d / "instagram.mp4")
        K.collect(self.cfg, self.acct, DAY)
        s = self.slot("07:00")
        self.assertNotIn("cuts", s)
        self.assertIn("Instagram has the clip already", B.last_note(s))
        self.assertEqual(B.verify(s), [])
        cut = d / "instagram.mp4"
        s["outlets"]["instagram_reels"] = {"attempt": {"container_id": "c1"}}     # a send on its way
        self.assertIn("on its way", K._late_cut_waits(self.acct, s, "instagram_reels", cut))
        s["outlets"].pop("instagram_reels")
        self.assertEqual(K._late_cut_waits(self.acct, s, "instagram_reels", cut), "")
        self.acct.outlets["instagram_reels"]["late_cut_joins"] = False              # the switch keeps the old way
        self.assertIn("late_cut_joins is off", K._late_cut_waits(self.acct, s, "instagram_reels", cut))
        self.acct.outlets["instagram_reels"].pop("late_cut_joins")
        Path(s["clip"]["path"]).write_bytes(b"not the signed clip")
        self.assertEqual(K._late_cut_waits(self.acct, s, "instagram_reels", cut), "the clip changed after signing")

    def test_4g_a_cut_that_comes_after_its_platform_gave_up_for_want_of_it_waits_for_try_again(self):
        # the 10-29 06:30 case: signed uncut, Instagram gave up at the slot for want of its cut, the cut came later.
        # It joins, nothing goes late by itself, Try again sends it
        d = self.deliver("0700")
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:05"), posters=self.posters(), uploader=lambda a, s: "vid")
        rec = self.slot("07:00")["outlets"]["instagram_reels"]
        self.assertTrue(rec.get("gave_up") and str(rec.get("why")).startswith("no Instagram cut"), rec)
        time.sleep(1.1)
        shutil.copy(self.short, d / "instagram.mp4")
        K.collect(self.cfg, self.acct, DAY)
        s = self.slot("07:00")
        self.assertIn("instagram_reels", s["cuts"])
        self.assertEqual(B.verify(s), [])
        self.assertEqual(s["outlets"]["instagram_reels"]["why"], "its cut came after its time: Try again sends it")
        n = len(self.calls)
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:30"), posters=self.posters(), uploader=lambda a, s: "vid")
        self.assertEqual([c for c in self.calls[n:] if c[0] == "instagram_reels"], [])   # nothing goes by itself
        self.assertTrue(R._retryable(s, self.acct))
        S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:40"), posters=self.posters(), uploader=lambda a, s: "vid")
        self.assertIn(("instagram_reels", "instagram_reels.mp4"), self.calls)

    def test_4e_one_cut_file_never_joins_two_slots(self):
        # 10-23: 13:35 and 15:55 shared one Instagram cut and it went out twice
        self.deliver("0700", cut=self.short)
        self.deliver("0845", cut=self.short, slug="the-second-exchange")
        out = K.collect(self.cfg, self.acct, DAY)
        have = [k for k in ("07:00", "08:45") if (self.slot(k).get("cuts") or {}).get("instagram_reels")]
        self.assertEqual(len(have), 1, out)
        other = "08:45" if have == ["07:00"] else "07:00"
        self.assertTrue(any("the same file as" in n for n in map(str, self.slot(other).get("notes") or [])),
                        self.slot(other).get("notes"))

    def test_5_a_cut_outside_its_band_is_left_out(self):
        self.deliver("0700", cut=self.long_cut)
        out = K.collect(self.cfg, self.acct, DAY)
        self.assertEqual(self.slot("07:00")["status"], B.READY, out)
        self.assertNotIn("cuts", self.slot("07:00"))
        self.assertIn("cut left out", " ".join(self.slot("07:00")["notes"]))

    def test_6_old_signatures_stay_true(self):
        s = {"slot": "07:00", "publish_at": "2031-05-06T07:00:00-06:00", "clip": {"sha256": "a"},
             "copy": {"tiktok": {"sha256": "b"}}}
        self.assertEqual(B.content_hash(s), B.sha256_text(json.dumps(
            {"slot": "07:00", "publish_at": s["publish_at"], "clip": "a", "copy": {"tiktok": "b"}}, sort_keys=True)))

    def test_7_the_card_shows_the_cut(self):
        for name in ("meta", "youtube_token"):                 # signed in (files only: nothing can leave a test)
            (O.TOKENS / self.acct.slug).mkdir(parents=True, exist_ok=True)
            (O.TOKENS / self.acct.slug / f"{name}.json").write_text("{}")
        self.addCleanup(shutil.rmtree, O.TOKENS, True)
        self.deliver("0700", cut=self.short)
        K.collect(self.cfg, self.acct, DAY)
        card = R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))["cards"][0]
        self.assertIn("Instagram gets its own 1s cut", card["html"])
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        card = R._section(self.cfg, self.acct, DAY, self.cfg.slot_time(DAY, "06:00"))["cards"][0]
        self.assertIn("its own 1s cut", card["bottom"])

    def _ig_job(self, clip_extra: dict = None, preflight: str = "print('ALL CLEAR')\n",
                grade: str = "print('No BAD findings.')\n", max_seconds: float = None, cards_record: list = None):
        """A delivered clip, its job, work folder and schedule, and a stand-in engine (the account's skill_dir) whose
        build_all.py records what it was given, and whose preflight.py and grade.py are the given scripts."""
        from clipfactory import igcut as IG
        acct = self.acct
        acct.account_dir = self.tmp / "account"
        job = acct.account_dir / "Jobs" / "Some Hearing 01.02.31"
        work = self.tmp / "clipwork" / "hearing0102"
        for d in ("account", "clipwork", "engine"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        for d in (job, work, acct.account_dir / "Publish"):
            d.mkdir(parents=True, exist_ok=True)
        (work / "master.mp4").write_bytes(b"master")
        (work / "project.json").write_text(json.dumps({"goal": "both", "out_dir": str(job)}))
        (job / "project.json").write_text(json.dumps({"source": str(work / "master.mp4"), "out_dir": str(job)}))
        (job / "cast.json").write_text('{"people": []}')
        clip = {"rank": 1, "slug": "the-exchange", "start": 100.0, "end": 190.0, "hook": "SEVEN ASKS", "min_len": 65,
                "hook2": "NO", "hook2_at": 26, "cards": [{"who": "a", "at": 13, "hold": 4}]}
        clip.update(clip_extra or {})
        (job / "slate.json").write_text(json.dumps({"notes": "x", "clips": [clip]}))
        (acct.account_dir / "Publish" / "schedule.json").write_text(json.dumps(
            {DAY.isoformat(): {"07:00": {"job": job.name, "slug": "the-exchange"}}}))
        self.deliver("0700")
        if max_seconds:                    # room for the clip's own times when the test is not about the band
            acct.outlets["instagram_reels"]["max_seconds"] = max_seconds
        acct.skill_dir = self.tmp / "engine"
        engine = acct.skill_dir / "scripts"
        engine.mkdir(parents=True)
        self.assertEqual(IG.engine(acct), engine)                 # the account's own skill renders and checks it
        (engine / "build_all.py").write_text(
            "import json, os, shutil, sys\n"
            "w = os.environ['CLIP_WORK']\n"
            "p = json.load(open(os.path.join(w, 'project.json')))\n"
            "c = json.load(open(os.path.join(w, 'slate.json')))['clips'][0]\n"
            "assert sys.argv[1] == c['slug'] == 'the-exchange-ig', (sys.argv, c)\n"
            "assert os.path.exists(os.path.join(p['out_dir'], 'cast.json'))\n"
            "json.dump({'project': p, 'clip': c}, open(os.path.join(w, 'seen.json'), 'w'))\n"
            "os.makedirs(os.path.join(p['out_dir'], 'clips'), exist_ok=True)\n"
            f"shutil.copy({str(self.short)!r}, os.path.join(p['out_dir'], 'clips', '01-' + c['slug'] + '.mp4'))\n"
            + (f"json.dump({{'cards': {cards_record!r}}}, open(os.path.join(p['out_dir'], 'clips', "
               "'01-' + c['slug'] + '.cards.json'), 'w'))\n" if cards_record is not None else ""))
        (engine / "preflight.py").write_text(preflight)
        (engine / "grade.py").write_text(grade)
        return IG, acct

    def _seen(self) -> dict:
        return json.loads(next(self.tmp.glob("clipwork/hearing0102-ig-*/seen.json")).read_text())

    def _cut(self) -> Path:
        return self.acct.delivered_dirs(DAY)[0] / "0700 the-exchange" / "instagram.mp4"

    def test_8_igcut_renders_one_tighter_entry_through_the_skill(self):
        """The helper's plumbing against a stand-in engine (a real render is the nightly run's): the slate entry, the
        band, "cut_for", the cast beside out_dir, both checks, the file it delivers and the collect that takes it."""
        IG, acct = self._ig_job(grade="print('[ WARN ] CARDS      look at +2.0s')\nprint('[ WARN ] PICTURE x')\n")
        out = IG.render(self.cfg, acct, DAY, "the-exchange", segments="130-160,170-190", hook="ASKED AGAIN",
                        hook2=False, sets=['cards=[{"who": "a", "at": 0.5, "hold": 3}]'], python=sys.executable,
                        log=lambda *a: None)
        seen = self._seen()
        c, p = seen["clip"], seen["project"]
        self.assertEqual((p["min_clip"], p["max_clip"]), (1.0, 1.6))             # the platform's band, not the account's
        self.assertEqual(p["cut_for"], "instagram_reels")                         # preflight and grade judge it as one
        self.assertEqual(c["segments"], [[130.0, 160.0], [170.0, 190.0]])
        self.assertNotIn("start", c)
        self.assertEqual((c["hook"], c["hard_out"], c["cards"][0]["at"]), ("ASKED AGAIN", True, 0.5))
        self.assertNotIn("hook2", c)
        self.assertNotIn("min_len", c)
        self.assertTrue(self._cut().exists())
        self.assertTrue(any("preflight and grade passed" in ln for ln in out), out)
        self.assertEqual([ln for ln in out if "LOOK:" in ln], ["    LOOK: [ WARN ] CARDS      look at +2.0s"])
        self.assertIn("instagram_reels", self.slot("07:00").get("cuts", {}), out)
        with self.assertRaises(SystemExit):
            IG.render(self.cfg, acct, DAY, "the-exchange", segments="160-130", python=sys.executable)

    def test_8b_a_preflight_failure_refuses_the_cut_before_anything_renders(self):
        IG, acct = self._ig_job(preflight="print('    [FAIL] TEMPLATE rhythm: no cell')\n"
                                          "print('NOT READY: template.')\nraise SystemExit(1)\n", max_seconds=200)
        with self.assertRaises(IG.CutError) as e:
            IG.render(self.cfg, acct, DAY, "the-exchange", end=150, python=sys.executable, log=lambda *a: None)
        self.assertIn("preflight refused the cut", str(e.exception))
        self.assertIn("[FAIL] TEMPLATE rhythm", str(e.exception))
        self.assertEqual(list(self.tmp.glob("clipwork/hearing0102-ig-*/seen.json")), [])   # build_all never ran
        self.assertFalse(self._cut().exists())

    def test_8c_a_bad_grade_refuses_the_cut_and_nothing_is_delivered(self):
        IG, acct = self._ig_job(grade="print('[ BAD  ] LOUD       -30 LUFS')\nraise SystemExit(1)\n", max_seconds=200)
        with self.assertRaises(IG.CutError) as e:
            IG.render(self.cfg, acct, DAY, "the-exchange", end=150, python=sys.executable, log=lambda *a: None)
        self.assertIn("grade refused the cut", str(e.exception))
        self.assertIn("[ BAD  ] LOUD", str(e.exception))
        self.assertFalse(self._cut().exists())

    def test_8d_everything_timed_in_delivered_seconds_must_be_re_timed(self):
        """Cards, figures, poster_at, quote_at and hook2_at, when the cut starts later or drops a middle piece."""
        IG, acct = self._ig_job({"figures": [{"text": "$1.2B", "at": 30, "hold": 3}], "quote_at": 3.0,
                                 "poster_at": 14})
        r = lambda **kw: IG.render(self.cfg, acct, DAY, "the-exchange", python=sys.executable, log=lambda *a: None,
                                   **kw)
        with self.assertRaises(IG.CutError) as e:            # starts 30s later, re-times only the cards
            r(segments="130-160,170-190", hook2=False, sets=['cards=[{"who": "a", "at": 2.0, "hold": 3}]'])
        msg = str(e.exception)
        self.assertIn("starts 30.0s after its clip, so the clip's figures, poster_at, quote_at would land", msg)
        self.assertNotIn("hook2_at", msg)                     # dropped with --no-hook2, so nothing to re-time
        with self.assertRaises(IG.CutError) as e:            # same start, a middle piece left out: still moved
            r(segments="100-120,150-190", hook2=False)
        self.assertIn("leaves out a piece of its clip", str(e.exception))
        self.assertIn("cards", str(e.exception))
        with self.assertRaises(IG.CutError) as e:            # hook2 kept: its time moves too
            r(segments="130-190", sets=['cards=[]', 'figures=[]', 'poster_at=1', 'quote_at=0'])
        self.assertIn("hook2_at", str(e.exception))
        with self.assertRaises(IG.CutError) as e:            # a re-timed field past the cut's end
            r(segments="130-190", hook2=False, sets=['cards=[{"who": "a", "at": 9.0, "hold": 3}]', 'figures=[]',
                                                     'poster_at=1', 'quote_at=0'])
        self.assertIn("cards[0].at = 9.0 is not a time inside the cut", str(e.exception))
        r(segments="130-160,170-190", hook2=False,
          sets=['cards=[{"who": "a", "at": 0.2, "hold": 3}]', 'figures=[]', 'poster_at=1', 'quote_at=0'])
        c = self._seen()["clip"]
        self.assertEqual((c["figures"], c["poster_at"], c["quote_at"], c["cards"][0]["at"]), ([], 1, 0, 0.2))

    def test_8f_a_plate_the_render_dropped_refuses_the_cut_and_a_moved_one_is_a_frame_to_look_at(self):
        """The skill drops a plate no shot of that person can hold (Patel's, 17:08, rather than put it on Booker)."""
        sets = ['cards=[{"who": "booker", "at": 0.2, "hold": 3}, {"who": "patel", "at": 1.1, "hold": 3}]']
        IG, acct = self._ig_job(cards_record=[{"name": "Cory Booker", "who": "booker", "typed_at": 0.2, "at": 0.4}])
        with self.assertRaises(IG.CutError) as e:
            IG.render(self.cfg, acct, DAY, "the-exchange", segments="130-190", hook2=False, sets=sets,
                      python=sys.executable, log=lambda *a: None)
        self.assertIn("dropped the plate for patel", str(e.exception))
        self.assertFalse(self._cut().exists())
        IG, acct = self._ig_job(cards_record=[
            {"name": "Cory Booker", "who": "booker", "typed_at": 0.2, "at": 0.4},
            {"name": "Kash Patel", "who": "patel", "typed_at": 1.1, "at": 1.3, "check_at": 1.4, "moved": "other_shot",
             "warn": True}])
        out = IG.render(self.cfg, acct, DAY, "the-exchange", segments="130-190", hook2=False, sets=sets,
                        python=sys.executable, log=lambda *a: None)
        self.assertIn("    LOOK: plate 'Kash Patel' typed +1.1s, placed +1.3s on another shot (other_shot): pull a "
                      "frame at +1.4s", out)
        self.assertTrue(self._cut().exists())

    def test_8e_a_cut_that_keeps_its_start_and_ends_earlier_needs_no_re_timing(self):
        IG, acct = self._ig_job({"figures": [{"text": "$1.2B", "at": 0.5, "hold": 0.5}]}, max_seconds=200)
        IG.render(self.cfg, acct, DAY, "the-exchange", end=150, python=sys.executable, log=lambda *a: None)
        self.assertEqual(self._seen()["clip"]["figures"][0]["at"], 0.5)
        self.assertTrue(self._cut().exists())

if __name__ == "__main__":
    unittest.main()
